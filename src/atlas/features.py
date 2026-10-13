"""Deterministic, leakage-safe feature generation shared by training, forecasting and backtests.

Vocabulary
----------
*target date* ``t``
    The day whose demand a feature row describes (or will be used to predict).
*origin* ``o``
    The last day whose demand is known when a forecast is issued. A forecast for
    ``t = o + h`` has *horizon* ``h >= 1``.
*information cutoff* of a row
    The last demand date the row may read. A feature with *offset* ``k`` reads the
    demand of day ``t - k`` (the offset is measured in calendar days, never in rows).

Guarantees
----------
1. **No look-ahead.** Every builder first truncates the demand series at the origin
   (:meth:`DemandSeries.known_through`), so observations after the origin are not even
   passed to a feature. Each row is then checked: the most recent day it reads must be
   on or before its information cutoff, otherwise :class:`LeakageError` is raised.
2. **One implementation.** Training rows, direct forecast rows and recursive forecast
   rows are all produced by the same private function, :func:`_build`, from the same
   :class:`FeatureSet`. Only the information available differs:

   ===================== ===================== ===================================
   builder               rows                  information for row ``t``
   ===================== ===================== ===================================
   training_features     every ``t <= o``      actual demand through ``t - h``
   forecast_features     ``t = o + h``         actual demand through ``o``
   recursive_forecast    ``t = o+1 .. o+H``    actual through ``o``, then the
                                               predictions for ``o+1 .. t-1``
   ===================== ===================== ===================================

   Consequently a training row for ``t`` at horizon ``h`` is identical to the
   forecast row for ``t`` issued at origin ``t - h``, and a recursive row equals the
   training row whenever the predictions it consumed equal the actual values.
3. **Calendar arithmetic.** Lags and windows are looked up by date, so a missing
   observation yields ``NaN`` in the features that read it (and an error unless
   ``allow_incomplete``), never a value from the wrong day.
4. **Insufficient history** (a row reading before the first observation) raises
   :class:`InsufficientHistoryError`; nothing is padded or back-filled.
5. **Determinism.** No randomness, no dependence on row order or wall-clock time;
   columns follow the feature-set order and every value is ``float64``. The trend
   anchor is a fixed date, not "the first date in the data", so a feature value does
   not change with the length of the training window.
6. **Known-in-advance events only.** Calendar and holiday features depend on the target
   date alone. A declared event is visible to a row only if it was announced on or
   before that row's origin. Data-quality flags are never features.
"""

from __future__ import annotations

import hashlib
import json
import math
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, ClassVar, Final

import numpy as np
import pandas as pd

from atlas.dates import ISO_WEEKEND, as_date, iso_week, year_fraction
from atlas.errors import (
    DataQualityError,
    FeatureError,
    InsufficientHistoryError,
    LeakageError,
)
from atlas.events import DEFAULT_EVENT_CALENDAR, EventCalendar
from atlas.issues import Issue, Severity, examples
from atlas.series import DemandSeries

DEFAULT_TREND_ANCHOR: Final = date(2023, 1, 1)
MAX_OFFSET_DAYS: Final = 3 * 366


class FeatureKind(StrEnum):
    CALENDAR = "calendar"
    TREND = "trend"
    LAG = "lag"
    ROLLING = "rolling"
    VARIABILITY = "variability"
    EVENT = "event"


def _day_numbers(index: pd.DatetimeIndex) -> np.ndarray:
    """Whole days since 1970-01-01 for each date (exact integer calendar arithmetic)."""
    return np.asarray(index.to_numpy(dtype="datetime64[D]").astype("int64"), dtype="int64")


class FeatureContext:
    """Everything a feature may read when computing values for a batch of rows.

    ``demand`` is the information set: actual values up to the origin, optionally
    followed by recursive predictions, on a contiguous daily index. Features must read
    it only through :meth:`read`, which refuses any day after the row's information
    cutoff (``cutoffs[i]``). ``origins[i]`` is the forecast origin of row ``i``; it decides
    which declared events are visible.
    """

    def __init__(
        self,
        *,
        targets: pd.DatetimeIndex,
        demand: pd.Series,
        cutoffs: pd.DatetimeIndex,
        origins: tuple[date, ...],
        events: EventCalendar,
    ) -> None:
        if not len(targets) == len(cutoffs) == len(origins):
            raise AssertionError("targets, cutoffs and origins must align")
        demand_index = pd.DatetimeIndex(demand.index)
        if len(demand_index) and not demand_index.equals(
            pd.date_range(demand_index[0], periods=len(demand_index), freq="D")
        ):
            raise AssertionError("demand must be on a contiguous daily index")
        self.targets = targets
        self.origins = origins
        self.events = events
        self._target_days = _day_numbers(targets)
        self._cutoff_days = _day_numbers(cutoffs)
        self._values = demand.to_numpy(dtype="float64")
        self._first_day = int(_day_numbers(demand_index[:1])[0]) if len(demand_index) else 0

    def read(self, offset: int) -> np.ndarray:
        """Demand of day ``t - offset`` for every target ``t`` (``NaN`` if not held).

        Raises :class:`LeakageError` if, for any row, that day is after the row's
        information cutoff. This check does not rely on a feature declaring its offsets.
        """
        wanted = self._target_days - offset
        after_cutoff = wanted > self._cutoff_days
        if after_cutoff.any():
            first = self.targets[after_cutoff][0].date()
            raise LeakageError(
                f"a feature read demand {offset} day(s) before {first}, after that row's "
                "information cutoff"
            )
        position = wanted - self._first_day
        held = (position >= 0) & (position < len(self._values))
        result = np.full(len(wanted), np.nan)
        result[held] = self._values[position[held]]
        return result

    def read_window(self, offsets: Sequence[int]) -> np.ndarray:
        """Matrix of shape (rows, len(offsets)) of demand at each offset."""
        return np.column_stack([self.read(offset) for offset in offsets])


# --- Feature definitions ------------------------------------------------------------


@dataclass(frozen=True)
class Feature(ABC):
    """A single named feature. Subclasses are frozen dataclasses with typed parameters.

    ``offsets`` lists every demand offset (in days before the target) the feature
    reads; calendar and event features read no demand and have no offsets.
    """

    kind: ClassVar[FeatureKind]

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    def offsets(self) -> tuple[int, ...]:
        return ()

    @abstractmethod
    def compute(self, context: FeatureContext) -> np.ndarray: ...

    def spec(self) -> dict[str, Any]:
        """Canonical, JSON-serialisable definition (used for the feature-set fingerprint)."""
        params = {key: _jsonable(value) for key, value in asdict(self).items()}
        return {"type": type(self).__name__, "name": self.name, **params}


def _jsonable(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    return value


def _coerce_enum(feature: Feature, attribute: str, enum: type[StrEnum]) -> None:
    """Store ``attribute`` as a member of ``enum``; plain strings are converted, others refused."""
    value = getattr(feature, attribute)
    try:
        member = enum(value)
    except ValueError:
        raise FeatureError(f"{attribute} must be one of {[m.value for m in enum]}") from None
    object.__setattr__(feature, attribute, member)


def _check_int(value: object, label: str, low: int, high: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise FeatureError(f"{label} must be an integer within {low}..{high}, got {value!r}")


def _check_offset(value: int, label: str, minimum: int = 1) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FeatureError(f"{label} must be an integer, got {value!r}")
    if not minimum <= value <= MAX_OFFSET_DAYS:
        raise FeatureError(f"{label} must be within {minimum}..{MAX_OFFSET_DAYS}, got {value}")


@dataclass(frozen=True)
class Lag(Feature):
    """Demand ``lag`` days before the target: ``y[t - lag]``."""

    lag: int
    kind: ClassVar[FeatureKind] = FeatureKind.LAG

    def __post_init__(self) -> None:
        _check_offset(self.lag, "lag")

    @property
    def name(self) -> str:
        return f"lag_{self.lag}"

    @property
    def description(self) -> str:
        return f"Demand {self.lag} day(s) before the target date."

    @property
    def offsets(self) -> tuple[int, ...]:
        return (self.lag,)

    def compute(self, context: FeatureContext) -> np.ndarray:
        return context.read(self.lag)


@dataclass(frozen=True)
class RollingMean(Feature):
    """Mean of ``window`` consecutive days ending ``lag`` days before the target.

    Reads ``y[t - lag - window + 1] .. y[t - lag]``. ``NaN`` if any of them is missing.
    """

    window: int
    lag: int = 1
    kind: ClassVar[FeatureKind] = FeatureKind.ROLLING

    def __post_init__(self) -> None:
        _check_offset(self.window, "window")
        _check_offset(self.lag, "lag")

    @property
    def name(self) -> str:
        suffix = "" if self.lag == 1 else f"_lag_{self.lag}"
        return f"rolling_mean_{self.window}{suffix}"

    @property
    def description(self) -> str:
        return (
            f"Mean demand over the {self.window} days ending {self.lag} day(s) before the "
            "target date."
        )

    @property
    def offsets(self) -> tuple[int, ...]:
        return tuple(range(self.lag, self.lag + self.window))

    def compute(self, context: FeatureContext) -> np.ndarray:
        window = context.read_window(self.offsets)
        return np.where(np.isnan(window).any(axis=1), np.nan, window.mean(axis=1))


@dataclass(frozen=True)
class RollingStd(Feature):
    """Sample standard deviation (``ddof``) of the same window as :class:`RollingMean`."""

    window: int
    lag: int = 1
    ddof: int = 1
    kind: ClassVar[FeatureKind] = FeatureKind.VARIABILITY

    def __post_init__(self) -> None:
        _check_offset(self.window, "window", minimum=2)
        _check_offset(self.lag, "lag")
        if isinstance(self.ddof, bool) or self.ddof not in (0, 1):
            raise FeatureError("ddof must be 0 or 1")

    @property
    def name(self) -> str:
        suffix = "" if self.lag == 1 else f"_lag_{self.lag}"
        ddof = "" if self.ddof == 1 else "_ddof_0"
        return f"rolling_std_{self.window}{suffix}{ddof}"

    @property
    def description(self) -> str:
        return (
            f"Standard deviation (ddof={self.ddof}) of demand over the {self.window} days "
            f"ending {self.lag} day(s) before the target date."
        )

    @property
    def offsets(self) -> tuple[int, ...]:
        return tuple(range(self.lag, self.lag + self.window))

    def compute(self, context: FeatureContext) -> np.ndarray:
        window = context.read_window(self.offsets)
        with np.errstate(invalid="ignore"):
            std = window.std(axis=1, ddof=self.ddof)
        return np.where(np.isnan(window).any(axis=1), np.nan, std)


@dataclass(frozen=True)
class SameWeekdayMean(Feature):
    """Mean of the previous ``weeks`` values on the target's weekday.

    Reads ``y[t - 7*first_week], y[t - 7*(first_week+1)], ...`` (``weeks`` values).
    """

    weeks: int
    first_week: int = 1
    kind: ClassVar[FeatureKind] = FeatureKind.ROLLING

    def __post_init__(self) -> None:
        _check_offset(self.weeks, "weeks")
        _check_offset(self.first_week, "first_week")
        _check_offset(7 * (self.first_week + self.weeks - 1), "largest offset")

    @property
    def name(self) -> str:
        suffix = "" if self.first_week == 1 else f"_from_{self.first_week}w"
        return f"same_weekday_mean_{self.weeks}w{suffix}"

    @property
    def description(self) -> str:
        return (
            f"Mean demand on the target's weekday over {self.weeks} week(s), starting "
            f"{self.first_week} week(s) before the target date."
        )

    @property
    def offsets(self) -> tuple[int, ...]:
        return tuple(7 * week for week in range(self.first_week, self.first_week + self.weeks))

    def compute(self, context: FeatureContext) -> np.ndarray:
        window = context.read_window(self.offsets)
        return np.where(np.isnan(window).any(axis=1), np.nan, window.mean(axis=1))


@dataclass(frozen=True)
class SameWeekdayMedian(Feature):
    """Median of the previous ``weeks`` values on the target's weekday (M2 baseline input).

    Reads the same days as :class:`SameWeekdayMean`; ``NaN`` if any of them is missing.
    With an even ``weeks`` the median is the mean of the two middle values.
    """

    weeks: int
    first_week: int = 1
    kind: ClassVar[FeatureKind] = FeatureKind.ROLLING

    def __post_init__(self) -> None:
        _check_offset(self.weeks, "weeks")
        _check_offset(self.first_week, "first_week")
        _check_offset(7 * (self.first_week + self.weeks - 1), "largest offset")

    @property
    def name(self) -> str:
        suffix = "" if self.first_week == 1 else f"_from_{self.first_week}w"
        return f"same_weekday_median_{self.weeks}w{suffix}"

    @property
    def description(self) -> str:
        return (
            f"Median demand on the target's weekday over {self.weeks} week(s), starting "
            f"{self.first_week} week(s) before the target date."
        )

    @property
    def offsets(self) -> tuple[int, ...]:
        return tuple(7 * week for week in range(self.first_week, self.first_week + self.weeks))

    def compute(self, context: FeatureContext) -> np.ndarray:
        window = context.read_window(self.offsets)
        incomplete = np.isnan(window).any(axis=1)
        # The median of a row with a NaN is never used, so silence numpy's warning for it.
        filled = np.where(np.isnan(window), 0.0, window)
        return np.where(incomplete, np.nan, np.median(filled, axis=1))


class CalendarField(StrEnum):
    ISO_WEEKDAY = "iso_weekday"  # Monday = 1 ... Sunday = 7
    IS_WEEKEND = "is_weekend"  # Saturday or Sunday
    DAY_OF_MONTH = "day_of_month"
    MONTH = "month"
    DAY_OF_YEAR = "day_of_year"  # 1 .. 365 or 366
    ISO_WEEK = "iso_week"  # 1 .. 53, of the ISO year
    IS_MONTH_START = "is_month_start"
    IS_MONTH_END = "is_month_end"
    YEAR = "year"  # calendar year (used by the legacy model specification, M2)


@dataclass(frozen=True)
class Calendar(Feature):
    """A calendar attribute of the target date (ISO 8601 conventions, see atlas.dates)."""

    field: CalendarField
    kind: ClassVar[FeatureKind] = FeatureKind.CALENDAR

    def __post_init__(self) -> None:
        _coerce_enum(self, "field", CalendarField)

    @property
    def name(self) -> str:
        return self.field.value

    @property
    def description(self) -> str:
        return f"Calendar attribute of the target date: {self.field.value} (ISO 8601)."

    def compute(self, context: FeatureContext) -> np.ndarray:
        dates = context.targets
        match self.field:
            case CalendarField.ISO_WEEKDAY:
                values: Any = dates.dayofweek + 1
            case CalendarField.IS_WEEKEND:
                values = np.isin(dates.dayofweek + 1, sorted(ISO_WEEKEND))
            case CalendarField.DAY_OF_MONTH:
                values = dates.day
            case CalendarField.MONTH:
                values = dates.month
            case CalendarField.DAY_OF_YEAR:
                values = dates.dayofyear
            case CalendarField.ISO_WEEK:
                values = iso_week(dates)["iso_week"].to_numpy()
            case CalendarField.IS_MONTH_START:
                values = dates.is_month_start
            case CalendarField.IS_MONTH_END:
                values = dates.is_month_end
            case CalendarField.YEAR:
                values = dates.year
            case _:  # pragma: no cover - exhaustive over CalendarField
                raise AssertionError(self.field)
        return np.asarray(values, dtype="float64")


@dataclass(frozen=True)
class WeekdayIndicator(Feature):
    """1.0 when the target's ISO weekday is ``iso_weekday``, else 0.0."""

    iso_weekday: int
    kind: ClassVar[FeatureKind] = FeatureKind.CALENDAR

    def __post_init__(self) -> None:
        _check_int(self.iso_weekday, "iso_weekday", 1, 7)

    @property
    def name(self) -> str:
        return f"is_iso_weekday_{self.iso_weekday}"

    @property
    def description(self) -> str:
        return f"Indicator: the target date is ISO weekday {self.iso_weekday} (Monday = 1)."

    def compute(self, context: FeatureContext) -> np.ndarray:
        return np.asarray(context.targets.dayofweek + 1 == self.iso_weekday, dtype="float64")


@dataclass(frozen=True)
class MonthIndicator(Feature):
    """1.0 when the target's calendar month is ``month`` (1 = January), else 0.0."""

    month: int
    kind: ClassVar[FeatureKind] = FeatureKind.CALENDAR

    def __post_init__(self) -> None:
        _check_int(self.month, "month", 1, 12)

    @property
    def name(self) -> str:
        return f"is_month_{self.month}"

    @property
    def description(self) -> str:
        return f"Indicator: the target date is in calendar month {self.month}."

    def compute(self, context: FeatureContext) -> np.ndarray:
        return np.asarray(context.targets.month == self.month, dtype="float64")


@dataclass(frozen=True)
class LegacyAnnualCycle(Feature):
    """``sin`` or ``cos`` of ``2 * pi * day_of_year / 365.25``: the legacy notebook convention.

    Exists only so the legacy model can be reproduced exactly (M2). It drifts by up to a
    day per year and exceeds a full cycle on 31 December of a leap year; new models should
    use :class:`AnnualFourier`.
    """

    function: str  # "sin" or "cos"
    kind: ClassVar[FeatureKind] = FeatureKind.CALENDAR

    def __post_init__(self) -> None:
        if self.function not in ("sin", "cos"):
            raise FeatureError("function must be 'sin' or 'cos'")

    @property
    def name(self) -> str:
        return f"legacy_annual_{self.function}"

    @property
    def description(self) -> str:
        return f"Legacy annual term: {self.function}(2*pi*day_of_year/365.25)."

    def compute(self, context: FeatureContext) -> np.ndarray:
        day_of_year = np.asarray(context.targets.dayofyear, dtype="float64")
        angle = 2.0 * math.pi * day_of_year / 365.25
        return np.sin(angle) if self.function == "sin" else np.cos(angle)


@dataclass(frozen=True)
class AnnualFourier(Feature):
    """``sin`` or ``cos`` of ``2 * pi * order * year_fraction(t)``.

    ``year_fraction`` is ``(day_of_year - 1) / days_in_year``, so the term is continuous
    from 31 December to 1 January in leap and common years.
    """

    order: int
    function: str  # "sin" or "cos"
    kind: ClassVar[FeatureKind] = FeatureKind.CALENDAR

    def __post_init__(self) -> None:
        if self.function not in ("sin", "cos"):
            raise FeatureError("function must be 'sin' or 'cos'")
        _check_int(self.order, "order", 1, 12)

    @property
    def name(self) -> str:
        return f"year_{self.function}_{self.order}"

    @property
    def description(self) -> str:
        return f"Annual seasonality: {self.function}(2*pi*{self.order}*year_fraction)."

    def compute(self, context: FeatureContext) -> np.ndarray:
        angle = 2.0 * math.pi * self.order * year_fraction(context.targets).to_numpy()
        return np.sin(angle) if self.function == "sin" else np.cos(angle)


@dataclass(frozen=True)
class Trend(Feature):
    """Days from a fixed ``anchor`` date to the target date (negative before it)."""

    anchor: date = DEFAULT_TREND_ANCHOR
    kind: ClassVar[FeatureKind] = FeatureKind.TREND

    def __post_init__(self) -> None:
        if isinstance(self.anchor, datetime) or not isinstance(self.anchor, date):
            raise FeatureError(f"anchor must be a datetime.date, got {self.anchor!r}")

    @property
    def name(self) -> str:
        return "trend_days"

    @property
    def description(self) -> str:
        return f"Days since the fixed anchor {self.anchor.isoformat()} (not the data start)."

    def compute(self, context: FeatureContext) -> np.ndarray:
        delta = context.targets - pd.Timestamp(self.anchor)
        return np.asarray(delta.days, dtype="float64")


class HolidayRelation(StrEnum):
    ON = "on"  # the actual date of a federal holiday
    OBSERVED = "observed"  # the federal day off (moved off Saturday/Sunday)
    EVE = "eve"  # the day before a holiday's actual date
    AFTER = "after"  # the day after a holiday's actual date


@dataclass(frozen=True)
class HolidayIndicator(Feature):
    """1.0 when the target date has ``relation`` to a US federal holiday."""

    relation: HolidayRelation
    kind: ClassVar[FeatureKind] = FeatureKind.EVENT

    def __post_init__(self) -> None:
        _coerce_enum(self, "relation", HolidayRelation)

    @property
    def name(self) -> str:
        return {
            HolidayRelation.ON: "is_holiday",
            HolidayRelation.OBSERVED: "is_observed_holiday",
            HolidayRelation.EVE: "is_day_before_holiday",
            HolidayRelation.AFTER: "is_day_after_holiday",
        }[self.relation]

    @property
    def description(self) -> str:
        return {
            HolidayRelation.ON: "The target date is a US federal holiday (actual date).",
            HolidayRelation.OBSERVED: "The target date is a federal day off (observed date).",
            HolidayRelation.EVE: "The day after the target date is a US federal holiday.",
            HolidayRelation.AFTER: "The day before the target date was a US federal holiday.",
        }[self.relation]

    def compute(self, context: FeatureContext) -> np.ndarray:
        if len(context.targets) == 0:
            return np.zeros(0)
        first = context.targets.min().date() - timedelta(days=1)
        last = context.targets.max().date() + timedelta(days=1)
        if self.relation is HolidayRelation.OBSERVED:
            marked = context.events.holiday_observed_dates(first, last)
        else:
            marked = context.events.holiday_actual_dates(first, last)
        shift = {
            HolidayRelation.ON: 0,
            HolidayRelation.OBSERVED: 0,
            HolidayRelation.EVE: 1,
            HolidayRelation.AFTER: -1,
        }[self.relation]
        return np.asarray(
            [(t.date() + timedelta(days=shift)) in marked for t in context.targets],
            dtype="float64",
        )


@dataclass(frozen=True)
class DeclaredEventIndicator(Feature):
    """1.0 when a declared event announced on or before the row's origin covers the target."""

    kind: ClassVar[FeatureKind] = FeatureKind.EVENT

    @property
    def name(self) -> str:
        return "is_declared_event"

    @property
    def description(self) -> str:
        return (
            "A declared event covers the target date and was announced on or before the "
            "forecast origin."
        )

    def compute(self, context: FeatureContext) -> np.ndarray:
        flags = [
            context.events.declared_event_days([t.date()], origin)[0]
            for t, origin in zip(context.targets, context.origins, strict=True)
        ]
        return np.asarray(flags, dtype="float64")


# --- Feature sets -------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureSet:
    """An ordered collection of features with unique names."""

    features: tuple[Feature, ...]
    name: str = "custom"

    def __post_init__(self) -> None:
        if not self.features:
            raise FeatureError("a feature set needs at least one feature")
        names = [feature.name for feature in self.features]
        duplicated = sorted({n for n in names if names.count(n) > 1})
        if duplicated:
            raise FeatureError(f"duplicate feature names: {duplicated}")

    def __iter__(self) -> Iterator[Feature]:
        return iter(self.features)

    def __len__(self) -> int:
        return len(self.features)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(feature.name for feature in self.features)

    @property
    def min_offset(self) -> int | None:
        """Smallest demand offset read; ``None`` if no feature reads demand."""
        offsets = [o for feature in self.features for o in feature.offsets]
        return min(offsets) if offsets else None

    @property
    def max_offset(self) -> int:
        """Largest demand offset read: the history needed before the first target."""
        offsets = [o for feature in self.features for o in feature.offsets]
        return max(offsets) if offsets else 0

    def supports_horizon(self, horizon: int) -> bool:
        """True when every demand read of a row at ``horizon`` is at or before its origin."""
        return self.min_offset is None or self.min_offset >= horizon

    def fingerprint(self) -> str:
        """SHA-256 of the canonical JSON definition; identical sets share a fingerprint."""
        canonical = json.dumps(
            [feature.spec() for feature in self.features], sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def catalog(self) -> pd.DataFrame:
        """One row per feature: name, kind, offsets read and description."""
        return pd.DataFrame(
            [
                {
                    "name": feature.name,
                    "kind": feature.kind.value,
                    "min_offset": min(feature.offsets) if feature.offsets else None,
                    "max_offset": max(feature.offsets) if feature.offsets else None,
                    "description": feature.description,
                }
                for feature in self.features
            ]
        )


def default_feature_set(trend_anchor: date = DEFAULT_TREND_ANCHOR) -> FeatureSet:
    """The ATLAS M1 default: one-step-ahead features, usable recursively.

    Weekday indicators omit Monday (the reference level), so together with an intercept
    they are not collinear (the legacy model used a full dummy set plus ``is_weekend``).
    """
    features: list[Feature] = [
        Calendar(CalendarField.ISO_WEEKDAY),
        Calendar(CalendarField.IS_WEEKEND),
        *(WeekdayIndicator(day) for day in range(2, 8)),
        Calendar(CalendarField.MONTH),
        Calendar(CalendarField.DAY_OF_MONTH),
        Calendar(CalendarField.DAY_OF_YEAR),
        Calendar(CalendarField.ISO_WEEK),
        Calendar(CalendarField.IS_MONTH_START),
        Calendar(CalendarField.IS_MONTH_END),
        AnnualFourier(1, "sin"),
        AnnualFourier(1, "cos"),
        AnnualFourier(2, "sin"),
        AnnualFourier(2, "cos"),
        Trend(trend_anchor),
        Lag(1),
        Lag(7),
        Lag(14),
        Lag(28),
        Lag(364),
        RollingMean(7),
        RollingMean(28),
        SameWeekdayMean(4),
        RollingStd(7),
        RollingStd(28),
        HolidayIndicator(HolidayRelation.ON),
        HolidayIndicator(HolidayRelation.OBSERVED),
        HolidayIndicator(HolidayRelation.EVE),
        HolidayIndicator(HolidayRelation.AFTER),
        DeclaredEventIndicator(),
    ]
    return FeatureSet(tuple(features), name="atlas-m1-default")


# --- Results ------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureMatrix:
    """Feature rows for a set of target dates, with the information they were built from.

    ``frame`` is indexed by ``target_date`` with one ``float64`` column per feature, in
    feature-set order. ``cutoffs`` holds each row's information cutoff. ``target`` holds
    the actual demand of each target date for training rows, and is ``None`` otherwise.
    """

    frame: pd.DataFrame
    feature_set: FeatureSet
    origin: date
    horizon: int | None
    cutoffs: pd.Series
    target: pd.Series | None = None

    @property
    def complete(self) -> pd.Series:
        """True for rows without any ``NaN`` feature (and, if present, a known target)."""
        mask = self.frame.notna().all(axis=1)
        if self.target is not None:
            mask &= self.target.notna()
        return mask


# --- Builders -----------------------------------------------------------------------


def _as_targets(targets: Sequence[date] | pd.DatetimeIndex) -> pd.DatetimeIndex:
    index = pd.DatetimeIndex([pd.Timestamp(as_date(t)) for t in targets], name="target_date")
    if index.has_duplicates:
        raise FeatureError("target dates must be unique")
    return index.sort_values()


def _build(
    *,
    demand: pd.Series,
    history_start: date,
    targets: pd.DatetimeIndex,
    cutoffs: pd.DatetimeIndex,
    origins: tuple[date, ...],
    feature_set: FeatureSet,
    events: EventCalendar,
    allow_incomplete: bool,
) -> tuple[pd.DataFrame, pd.Series]:
    """The single feature computation behind every public builder."""
    # Callers guarantee at least one target; reads after a row's cutoff are refused
    # by FeatureContext.read, independently of the offsets a feature declares.
    earliest_read = targets - pd.Timedelta(days=feature_set.max_offset)
    short = targets[earliest_read < pd.Timestamp(history_start)]
    if feature_set.max_offset and len(short):
        raise InsufficientHistoryError(
            f"{len(short)} target date(s) need {feature_set.max_offset} days of history "
            f"before them but the series starts {history_start}; first affected: "
            f"{short[0].date()} (earliest valid target: "
            f"{history_start + timedelta(days=feature_set.max_offset)})"
        )

    context = FeatureContext(
        targets=targets, demand=demand, cutoffs=cutoffs, origins=origins, events=events
    )
    columns: dict[str, np.ndarray] = {}
    for feature in feature_set:
        values = np.asarray(feature.compute(context), dtype="float64")
        if values.shape != (len(targets),):
            raise AssertionError(f"feature {feature.name} returned shape {values.shape}")
        columns[feature.name] = values
    frame = pd.DataFrame(columns, index=targets, columns=list(feature_set.names))

    incomplete = frame.index[frame.isna().any(axis=1)]
    if len(incomplete) and not allow_incomplete:
        issue = Issue(
            "incomplete_features",
            Severity.ERROR,
            "features",
            "Rows read a missing observation; pass allow_incomplete=True to keep them as NaN.",
            count=len(incomplete),
            examples=examples(d.date() for d in incomplete),
        )
        raise DataQualityError(
            f"{len(incomplete)} feature row(s) depend on missing observations; first: "
            f"{incomplete[0].date()}",
            (issue,),
        )
    return frame, pd.Series(cutoffs, index=targets, name="information_cutoff")


def _check_horizon(feature_set: FeatureSet, horizon: int) -> None:
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise FeatureError(f"horizon must be a positive integer, got {horizon!r}")
    if not feature_set.supports_horizon(horizon):
        raise LeakageError(
            f"feature set {feature_set.name!r} reads demand {feature_set.min_offset} day(s) "
            f"before the target, so it cannot produce horizon-{horizon} rows without "
            "unknown values; use recursive_forecast or features with offsets >= horizon"
        )


def training_features(
    series: DemandSeries,
    *,
    origin: date,
    horizon: int = 1,
    feature_set: FeatureSet | None = None,
    first_target: date | None = None,
    events: EventCalendar = DEFAULT_EVENT_CALENDAR,
    allow_incomplete: bool = False,
) -> FeatureMatrix:
    """Training rows for every target ``t <= origin``, each built from data through ``t - h``.

    Only observations dated on or before ``origin`` are used, for features *and*
    targets. Rows start at ``first_target`` or, by default, at the first date with enough
    history for every feature.
    """
    feature_set = feature_set or default_feature_set()
    origin = as_date(origin)
    _check_horizon(feature_set, horizon)
    if origin < series.start:
        raise InsufficientHistoryError(f"origin {origin} precedes the series start {series.start}")
    last = min(origin, series.end)  # data may end before the origin (e.g. a late feed)
    first = as_date(first_target or series.start + timedelta(days=feature_set.max_offset))
    if first > last:
        raise InsufficientHistoryError(
            f"no training rows: the first target with enough history is {first}, after the "
            f"last known date {last}"
        )
    demand = series.known_through(origin)
    targets = pd.date_range(first, last, freq="D", name="target_date")
    cutoffs = targets - pd.Timedelta(days=horizon)
    frame, cutoff_series = _build(
        demand=demand,
        history_start=series.start,
        targets=targets,
        cutoffs=cutoffs,
        origins=tuple(c.date() for c in cutoffs),
        feature_set=feature_set,
        events=events,
        allow_incomplete=allow_incomplete,
    )
    target = demand.reindex(targets).rename("target")
    if target.isna().any() and not allow_incomplete:
        missing = target.index[target.isna()]
        raise DataQualityError(
            f"{len(missing)} training target(s) are missing; first: {missing[0].date()}"
        )
    return FeatureMatrix(frame, feature_set, origin, horizon, cutoff_series, target)


def forecast_features(
    series: DemandSeries,
    *,
    origin: date,
    horizons: Sequence[int] = (1,),
    feature_set: FeatureSet | None = None,
    events: EventCalendar = DEFAULT_EVENT_CALENDAR,
    allow_incomplete: bool = False,
) -> FeatureMatrix:
    """Direct-forecast rows for ``origin + h`` for each ``h`` in ``horizons``.

    Every row is built from actual demand through ``origin`` only. The feature set must
    support the largest horizon (all offsets >= horizon); otherwise use
    :func:`recursive_forecast`.
    """
    feature_set = feature_set or default_feature_set()
    origin = as_date(origin)
    if not horizons:
        raise FeatureError("at least one horizon is required")
    for horizon in horizons:
        _check_horizon(feature_set, horizon)
    if not series.start <= origin <= series.end:
        raise InsufficientHistoryError(
            f"origin {origin} is outside the series {series.start}..{series.end}"
        )
    targets = _as_targets([origin + timedelta(days=h) for h in horizons])
    cutoffs = pd.DatetimeIndex([pd.Timestamp(origin)] * len(targets))
    frame, cutoff_series = _build(
        demand=series.known_through(origin),
        history_start=series.start,
        targets=targets,
        cutoffs=cutoffs,
        origins=(origin,) * len(targets),
        feature_set=feature_set,
        events=events,
        allow_incomplete=allow_incomplete,
    )
    return FeatureMatrix(frame, feature_set, origin, max(horizons), cutoff_series)


Predictor = Callable[[pd.Series], float]


@dataclass(frozen=True)
class RecursiveForecast:
    """Predictions for ``origin + 1 .. origin + steps`` and the feature rows behind them."""

    origin: date
    predictions: pd.Series
    features: FeatureMatrix
    inputs: pd.Series = field(repr=False)  # demand window read: actuals, then predictions


def recursive_forecast(
    series: DemandSeries,
    *,
    origin: date,
    steps: int,
    predictor: Predictor,
    feature_set: FeatureSet | None = None,
    events: EventCalendar = DEFAULT_EVENT_CALENDAR,
) -> RecursiveForecast:
    """Forecast ``steps`` days ahead, feeding each prediction back as demand.

    Row ``t`` is built from actual demand through ``origin`` plus the predictions for
    ``origin + 1 .. t - 1``; actual observations after ``origin`` are never read.
    ``predictor`` receives the feature row (a ``Series`` indexed by feature name) and
    returns a finite, non-negative prediction. No model is fitted here.
    """
    feature_set = feature_set or default_feature_set()
    origin = as_date(origin)
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
        raise FeatureError(f"steps must be a positive integer, got {steps!r}")
    _check_horizon(feature_set, 1)
    if not series.start <= origin <= series.end:
        raise InsufficientHistoryError(
            f"origin {origin} is outside the series {series.start}..{series.end}"
        )
    # Only the last max_offset days before the first target can be read.
    window_start = pd.Timestamp(origin - timedelta(days=feature_set.max_offset))
    demand = series.known_through(origin).loc[window_start:]
    targets = pd.date_range(origin + timedelta(days=1), periods=steps, freq="D", name="target_date")
    all_cutoffs = targets - pd.Timedelta(days=1)

    # Features that read no demand depend only on the target date and the origin, so they
    # are computed once for every step; demand features are recomputed at each step from
    # the actuals plus the predictions so far. Both use the same Feature.compute code.
    static = tuple(f for f in feature_set if not f.offsets)
    dynamic = tuple(f for f in feature_set if f.offsets)
    static_frame = pd.DataFrame(index=targets)
    if static:
        static_frame, _ = _build(
            demand=demand,
            history_start=series.start,
            targets=targets,
            cutoffs=all_cutoffs,
            origins=(origin,) * steps,
            feature_set=FeatureSet(static),
            events=events,
            allow_incomplete=False,
        )
    dynamic_set = FeatureSet(dynamic) if dynamic else None
    rows: list[pd.DataFrame] = []
    predictions: dict[pd.Timestamp, float] = {}
    for step in range(steps):
        target = targets[step]
        row = static_frame.iloc[[step]]
        if dynamic_set is not None:
            dynamic_row, _ = _build(
                demand=demand,
                history_start=series.start,
                targets=targets[[step]],
                cutoffs=all_cutoffs[[step]],
                origins=(origin,),
                feature_set=dynamic_set,
                events=events,
                allow_incomplete=False,
            )
            row = pd.concat([row, dynamic_row], axis=1)
        row = row[list(feature_set.names)]
        value = predictor(row.iloc[0].copy())
        if isinstance(value, bool) or not isinstance(value, int | float | np.integer | np.floating):
            raise FeatureError(f"predictor returned {type(value).__name__}, expected a number")
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise FeatureError(f"predictor returned {value!r} for {target.date()}")
        predictions[target] = value
        demand = pd.concat([demand, pd.Series([value], index=pd.DatetimeIndex([target]))])
        rows.append(row)
    cutoffs = [pd.Series(all_cutoffs, index=targets, name="information_cutoff")]
    frame = pd.concat(rows)
    frame.index.name = "target_date"
    matrix = FeatureMatrix(frame, feature_set, origin, steps, pd.concat(cutoffs))
    forecast = pd.Series(predictions, name="prediction", dtype="float64")
    forecast.index = pd.DatetimeIndex(forecast.index, name="target_date")
    return RecursiveForecast(origin, forecast, matrix, demand.rename("inputs"))
