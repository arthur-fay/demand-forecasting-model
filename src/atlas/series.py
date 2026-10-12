"""A validated daily demand series: the only input type of feature generation.

:class:`DemandSeries` guarantees, once constructed:

* the index is a strictly increasing run of consecutive calendar days (no time of day,
  no time zone, no duplicates, no gaps in the index itself);
* every observed value is finite and non-negative and, for request counts, integral;
* a date with no observation is held as ``NaN`` and listed in :attr:`missing_dates`.
  Missing dates are rejected unless the caller passes ``allow_missing=True``; they are
  never filled, interpolated or dropped silently.

Construction collects every problem before raising :class:`~atlas.errors.DataQualityError`
so that one error message lists all of them. Lags and windows are later computed by
calendar date, never by row position, so a missing date can never shift a lag onto the
wrong day.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from atlas.dates import as_date
from atlas.errors import DataQualityError
from atlas.issues import Issue, Severity, examples

DateLike = date | pd.Timestamp | str


def _error(code: str, subject: str, message: str, values: Sequence[object] = ()) -> Issue:
    return Issue(
        code,
        Severity.ERROR,
        subject,
        message,
        count=max(len(values), 1),
        examples=examples(values),
    )


def _parse_dates(raw: Sequence[object], subject: str) -> tuple[list[date | None], list[Issue]]:
    """Parse each date-like value; anything with a time of day or a time zone is invalid."""
    parsed: list[date | None] = []
    invalid: list[str] = []
    for value in raw:
        try:
            if value is None or value is pd.NaT or (isinstance(value, float) and math.isnan(value)):
                raise ValueError("missing timestamp")
            if not isinstance(value, date | str):
                raise TypeError(f"unsupported type {type(value).__name__}")
            parsed.append(as_date(value))
        except (TypeError, ValueError):
            parsed.append(None)
            invalid.append(repr(value))
    issues: list[Issue] = []
    if invalid:
        issues.append(
            _error(
                "invalid_timestamp",
                subject,
                "Dates must be calendar days (YYYY-MM-DD) without a time of day or time zone.",
                invalid,
            )
        )
    return parsed, issues


def _parse_values(
    raw: Sequence[object], subject: str, *, integral: bool, allow_missing: bool
) -> tuple[list[float], list[Issue]]:
    values: list[float] = []
    bad_type: list[str] = []
    nulls: list[int] = []
    non_finite: list[str] = []
    negative: list[str] = []
    fractional: list[str] = []
    for position, value in enumerate(raw):
        if value is None or value is pd.NA or (isinstance(value, float) and math.isnan(value)):
            nulls.append(position)
            values.append(math.nan)
            continue
        if isinstance(value, bool | np.bool_) or not isinstance(
            value, int | float | np.integer | np.floating
        ):
            bad_type.append(repr(value))
            values.append(math.nan)
            continue
        number = float(value)
        if not math.isfinite(number):
            non_finite.append(repr(value))
        elif number < 0:
            negative.append(repr(value))
        elif integral and not number.is_integer():
            fractional.append(repr(value))
        values.append(number)
    issues: list[Issue] = []
    if bad_type:
        issues.append(_error("non_numeric_value", subject, "Values must be numbers.", bad_type))
    if nulls and not allow_missing:
        issues.append(
            _error(
                "null_value",
                subject,
                "Values are missing; pass allow_missing=True to keep them as gaps.",
                [f"row {p}" for p in nulls],
            )
        )
    if non_finite:
        issues.append(_error("non_finite_value", subject, "Values must be finite.", non_finite))
    if negative:
        issues.append(_error("negative_value", subject, "Demand cannot be negative.", negative))
    if fractional:
        issues.append(
            _error("non_integral_value", subject, "Request counts must be integers.", fractional)
        )
    return values, issues


@dataclass(frozen=True)
class DemandSeries:
    """Daily demand on a complete calendar index; ``NaN`` marks a missing observation.

    Build instances with :meth:`from_observations` or :meth:`from_frame`; the
    constructor itself only re-checks the structural invariants.
    """

    name: str
    _values: pd.Series

    def __post_init__(self) -> None:
        index = self._values.index
        if not isinstance(index, pd.DatetimeIndex) or len(index) == 0:
            raise DataQualityError(f"{self.name}: series must have a non-empty DatetimeIndex")
        expected = pd.date_range(index[0], periods=len(index), freq="D")
        midnight = bool((index == index.normalize()).all())
        if index.tz is not None or not midnight or not index.equals(expected):
            raise DataQualityError(f"{self.name}: index must be consecutive calendar days")
        if self._values.dtype != np.float64:
            raise DataQualityError(f"{self.name}: values must be float64")

    # --- construction ---------------------------------------------------------------

    @classmethod
    def from_observations(
        cls,
        dates: Iterable[object],
        values: Iterable[object],
        *,
        name: str = "demand",
        allow_missing: bool = False,
        integral: bool = True,
    ) -> DemandSeries:
        """Validate ``(date, value)`` pairs, in any order, into a daily series.

        Raises :class:`DataQualityError` listing every problem found: invalid
        timestamps, duplicate dates, non-numeric, non-finite, negative or (when
        ``integral``) fractional values, and missing values or dates unless
        ``allow_missing``.
        """
        raw_dates, raw_values = list(dates), list(values)
        if len(raw_dates) != len(raw_values):
            raise DataQualityError(
                f"{name}: {len(raw_dates)} dates but {len(raw_values)} values",
            )
        if not raw_dates:
            issue = _error("empty_series", name, "The series has no observations.")
            raise DataQualityError(f"{name}: the series has no observations", (issue,))

        parsed_dates, issues = _parse_dates(raw_dates, name)
        parsed_values, value_issues = _parse_values(
            raw_values, name, integral=integral, allow_missing=allow_missing
        )
        issues.extend(value_issues)

        counts = Counter(d for d in parsed_dates if d is not None)
        duplicated = sorted(d for d, n in counts.items() if n > 1)
        if duplicated:
            issues.append(
                _error("duplicate_date", name, "Each date may appear only once.", duplicated)
            )

        pairs = sorted(
            (d, v) for d, v in zip(parsed_dates, parsed_values, strict=True) if d is not None
        )
        observed = pd.Series(
            [v for _, v in pairs],
            index=pd.DatetimeIndex([pd.Timestamp(d) for d, _ in pairs]),
            dtype="float64",
        )
        observed = observed[~observed.index.duplicated(keep="first")]
        full = pd.DatetimeIndex([])
        if len(observed):
            full = pd.date_range(observed.index[0], observed.index[-1], freq="D")
            missing = [d.date() for d in full.difference(pd.DatetimeIndex(observed.index))]
            if missing and not allow_missing:
                issues.append(
                    _error(
                        "missing_dates",
                        name,
                        "Calendar dates are absent; pass allow_missing=True to keep them as gaps.",
                        missing,
                    )
                )
        if issues:
            raise DataQualityError(
                f"{name}: {len(issues)} data-quality problem(s): "
                + "; ".join(f"{i.code} x{i.count}" for i in issues),
                tuple(issues),
            )
        series = observed.reindex(full).astype("float64")
        series.index.name = "date"
        series.name = name
        return cls(name, series)

    @classmethod
    def from_frame(
        cls,
        frame: pd.DataFrame,
        *,
        date_column: str = "request_date",
        value_column: str = "request_count",
        name: str | None = None,
        allow_missing: bool = False,
        integral: bool = True,
    ) -> DemandSeries:
        """Build from two columns of a frame, for example the loaded ``daily_total``."""
        for column in (date_column, value_column):
            if column not in frame.columns:
                raise DataQualityError(f"column {column!r} is missing")
        return cls.from_observations(
            frame[date_column].tolist(),
            frame[value_column].tolist(),
            name=name or value_column,
            allow_missing=allow_missing,
            integral=integral,
        )

    # --- access -----------------------------------------------------------------------

    @property
    def start(self) -> date:
        return pd.Timestamp(self._values.index[0]).date()

    @property
    def end(self) -> date:
        return pd.Timestamp(self._values.index[-1]).date()

    @property
    def missing_dates(self) -> tuple[date, ...]:
        index = pd.DatetimeIndex(self._values.index[self._values.isna()])
        return tuple(d.date() for d in index)

    def __len__(self) -> int:
        return len(self._values)

    def to_series(self) -> pd.Series:
        """A copy of the values (``float64``, daily ``DatetimeIndex``, ``NaN`` = missing)."""
        return self._values.copy()

    def known_through(self, origin: date) -> pd.Series:
        """Copy of the observations dated on or before ``origin`` (the information set)."""
        return self._values.loc[: pd.Timestamp(origin)].copy()

    def value_on(self, day: date) -> float:
        return float(self._values.loc[pd.Timestamp(day)])
