"""Forecasting models behind one typed interface, shared by backtests and projections (M2).

Every model is a :class:`Forecaster`. It is fitted on a :class:`~atlas.series.DemandSeries`
that *ends at the forecast origin* (the backtest engine passes ``series.through(origin)``,
so later observations are absent from the object, not just hidden), and the fitted model
produces a :class:`ForecastPath` for ``origin + 1 .. origin + steps``. Historical backtests
and future projections call exactly the same two methods (see :mod:`atlas.backtest`).

All demand inputs are computed by the M1 feature builder (:mod:`atlas.features`), so every
model inherits its leakage guards: data is truncated at the origin and each read is checked
against the row's information cutoff.

Baselines (direct: every horizon is computed from actual data through the origin)

=========================  ==============================================================
``naive``                  ``y(o)`` for every horizon (``Lag(h)`` at target ``o + h``)
``seasonal_naive``         ``y(o + h - m * ceil(h / m))`` with ``m = 7``: the most recent
                           observed day with the target's weekday (``Lag(7 * ceil(h / 7))``)
``same_weekday_mean``      mean of the ``K`` most recent observed same-weekday values
``same_weekday_median``    median of the ``K`` most recent observed same-weekday values
=========================  ==============================================================

Regressions (recursive: predictions are fed back as demand for later lags and windows)

``legacy_linear_regression``
    A faithful re-implementation of the legacy notebook (cell index 14-16): the same 32
    features in the same order, an intercept, scikit-learn's minimum-norm least squares
    on a rank-deficient design, and a recursive forecast that clips each prediction at 0
    and rounds it to an integer (half to even, as Python's ``round``) *before* feeding it
    back. It reproduces the 90 published holdout predictions and the 30 published future
    forecasts exactly (``atlas backtest legacy``).
``atlas_linear_regression``
    The modernised specification ``atlas-m2-regression-v1``: non-collinear M1 features,
    ordinary least squares on standardised columns, full rank required, clipped at 0,
    not rounded. Fixed before any backtest was run.
"""

from __future__ import annotations

import hashlib
import json
import math
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd

from atlas.errors import (
    ConfigError,
    DataQualityError,
    ForecastError,
    InsufficientHistoryError,
    ModelFitError,
)
from atlas.features import (
    DEFAULT_TREND_ANCHOR,
    AnnualFourier,
    Calendar,
    CalendarField,
    Feature,
    FeatureSet,
    HolidayIndicator,
    HolidayRelation,
    Lag,
    LegacyAnnualCycle,
    MonthIndicator,
    RollingMean,
    RollingStd,
    SameWeekdayMean,
    SameWeekdayMedian,
    Trend,
    WeekdayIndicator,
    forecast_features,
    recursive_forecast,
    training_features,
)
from atlas.regression import HIGH_LEVERAGE, FitMethod, LinearFit, fit_linear
from atlas.series import DemandSeries

LEGACY_FEATURE_SET_NAME = "legacy-notebook-cell-14"
ATLAS_FEATURE_SET_NAME = "atlas-m2-regression-v1"


class ModelFamily(StrEnum):
    BASELINE = "baseline"
    REGRESSION = "regression"


class Procedure(StrEnum):
    DIRECT = "direct"  # each horizon from actual data through the origin
    RECURSIVE = "recursive"  # one-step model iterated, predictions fed back


class FailureStage(StrEnum):
    FIT = "fit"
    FORECAST = "forecast"
    VALIDATE = "validate"


@dataclass(frozen=True)
class ModelSpec:
    """Identity and parameters of a model.

    ``fingerprint`` covers everything that changes what the model computes: type, family,
    procedure, every parameter and behaviour switch, the feature set and the
    ``implementation_version``, which is increased whenever the code of a model type
    changes its output. The prose ``description`` is excluded.
    """

    model_id: str
    model_type: str
    family: ModelFamily
    procedure: Procedure
    description: str
    params: Mapping[str, Any] = field(default_factory=dict)
    feature_set: str | None = None
    feature_set_fingerprint: str | None = None
    implementation_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_type": self.model_type,
            "family": self.family.value,
            "procedure": self.procedure.value,
            "description": self.description,
            "params": dict(sorted(self.params.items())),
            "feature_set": self.feature_set,
            "feature_set_fingerprint": self.feature_set_fingerprint,
            "implementation_version": self.implementation_version,
        }

    def fingerprint(self) -> str:
        identity = {k: v for k, v in self.to_dict().items() if k != "description"}
        canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Failure:
    """Why a model produced no forecast for a horizon (or for a whole origin)."""

    stage: FailureStage
    code: str
    message: str


@dataclass(frozen=True)
class FitDiagnostics:
    """What a model was fitted on, and how well-posed the fit was."""

    n_train_rows: int = 0
    n_dropped_rows: int = 0
    train_first: date | None = None
    train_last: date | None = None
    n_features: int = 0
    rank: int | None = None
    solver_rank: int | None = None
    condition_number: float | None = None
    restricted_condition_number: float | None = None
    smallest_kept_ratio: float | None = None
    largest_dropped_ratio: float | None = None
    max_training_leverage: float | None = None
    identifiable: bool | None = None
    in_sample_mae: float | None = None
    feature_set_fingerprint: str | None = None


@dataclass(frozen=True)
class ForecastPath:
    """Predictions for ``origin + 1 .. origin + steps``; ``NaN`` exactly where ``failures`` says.

    ``raw_predictions`` are the model outputs before clipping at 0 and rounding (equal to
    ``predictions`` for models that do neither). For regressions, ``estimable`` and
    ``leverage`` describe each prediction row (see :mod:`atlas.regression`).
    ``feature_rows`` and ``inputs`` hold what a recursive model actually consumed: its
    feature rows and the demand window (actual demand through the origin, then the values
    it fed back), for inspection and tests.
    """

    origin: date
    predictions: pd.Series
    failures: Mapping[int, Failure] = field(default_factory=dict)
    raw_predictions: pd.Series | None = field(default=None, repr=False)
    estimable: np.ndarray | None = field(default=None, repr=False)
    leverage: np.ndarray | None = field(default=None, repr=False)
    n_clipped: int = 0
    feature_rows: pd.DataFrame | None = field(default=None, repr=False)
    inputs: pd.Series | None = field(default=None, repr=False)

    @property
    def n_nonestimable(self) -> int:
        return 0 if self.estimable is None else int((~self.estimable).sum())

    @property
    def n_high_leverage(self) -> int:
        return 0 if self.leverage is None else int((self.leverage > HIGH_LEVERAGE).sum())

    @property
    def max_leverage(self) -> float | None:
        return None if self.leverage is None else float(self.leverage.max())


class FittedForecaster(ABC):
    """A model fitted at one origin; produces forecasts from that origin only."""

    origin: date
    diagnostics: FitDiagnostics

    @abstractmethod
    def forecast(self, steps: int) -> ForecastPath: ...


class Forecaster(ABC):
    """A forecasting model: identity (:attr:`spec`) and :meth:`fit`."""

    @property
    @abstractmethod
    def spec(self) -> ModelSpec: ...

    @abstractmethod
    def fit(self, history: DemandSeries) -> FittedForecaster:
        """Fit on ``history``, which ends at the forecast origin (``history.end``)."""


def target_dates(origin: date, steps: int) -> pd.DatetimeIndex:
    """``origin + 1 .. origin + steps`` as a daily index named ``target_date``."""
    check_steps(steps)
    return pd.date_range(origin + timedelta(days=1), periods=steps, freq="D", name="target_date")


def check_steps(steps: int) -> None:
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
        raise ConfigError(f"steps must be a positive integer, got {steps!r}")


def weeks_back(horizon: int, season: int = 7) -> int:
    """Smallest ``k`` with ``season * k >= horizon``: the most recent known seasonal lag."""
    return math.ceil(horizon / season)


# --- Baselines (direct) ---------------------------------------------------------------


class DirectFeatureBaseline(Forecaster):
    """Forecast for ``o + h`` = value of one M1 feature at target ``o + h`` (data through ``o``).

    Horizons that use the same feature are computed in one call. A horizon whose input
    reads a missing observation fails on its own (code ``unavailable_feature``); the
    other horizons are unaffected.
    """

    @abstractmethod
    def feature_for(self, horizon: int) -> Feature: ...

    def fit(self, history: DemandSeries) -> FittedForecaster:
        return _FittedDirect(self, history)


class _FittedDirect(FittedForecaster):
    def __init__(self, model: DirectFeatureBaseline, history: DemandSeries) -> None:
        self._model = model
        self._history = history
        self.origin = history.end
        self.diagnostics = FitDiagnostics(
            train_first=history.start, train_last=history.end, n_features=1
        )

    def forecast(self, steps: int) -> ForecastPath:
        targets = target_dates(self.origin, steps)
        groups: dict[Feature, list[int]] = {}
        for horizon in range(1, steps + 1):
            groups.setdefault(self._model.feature_for(horizon), []).append(horizon)
        values = np.full(steps, np.nan)
        failures: dict[int, Failure] = {}
        for feature, horizons in groups.items():
            # A horizon whose input would lie before the first day held fails on its own;
            # the others in the group are still forecast.
            earliest = self._history.start + timedelta(days=max(feature.offsets))
            reachable = []
            for horizon in horizons:
                target = targets[horizon - 1].date()
                if target < earliest:
                    failures[horizon] = Failure(
                        FailureStage.FORECAST,
                        "insufficient_history",
                        f"{feature.name} for {target} reads before the history starts "
                        f"{self._history.start}",
                    )
                else:
                    reachable.append(horizon)
            if not reachable:
                continue
            matrix = forecast_features(
                self._history,
                origin=self.origin,
                horizons=tuple(reachable),
                feature_set=FeatureSet((feature,), name=feature.name),
                allow_incomplete=True,
            )
            column = matrix.frame[feature.name]
            for horizon in reachable:
                target = targets[horizon - 1]
                value = float(column.loc[target])
                if math.isnan(value):
                    failures[horizon] = Failure(
                        FailureStage.FORECAST,
                        "unavailable_feature",
                        f"{feature.name} for {target.date()} reads a missing observation",
                    )
                else:
                    values[horizon - 1] = value
        predictions = pd.Series(values, index=targets, name="prediction", dtype="float64")
        return ForecastPath(
            self.origin, predictions, failures, raw_predictions=predictions.rename("prediction_raw")
        )


@dataclass(frozen=True)
class NaiveForecaster(DirectFeatureBaseline):
    model_id: str = "naive"

    @property
    def spec(self) -> ModelSpec:
        return ModelSpec(
            self.model_id,
            "naive",
            ModelFamily.BASELINE,
            Procedure.DIRECT,
            "Last observed value: yhat(o+h) = y(o) for every horizon.",
        )

    def feature_for(self, horizon: int) -> Feature:
        return Lag(horizon)


@dataclass(frozen=True)
class SeasonalNaiveForecaster(DirectFeatureBaseline):
    model_id: str = "seasonal_naive"
    season_days: int = 7

    def __post_init__(self) -> None:
        if isinstance(self.season_days, bool) or not 1 <= self.season_days <= 366:
            raise ConfigError("season_days must be an integer within 1..366")

    @property
    def spec(self) -> ModelSpec:
        return ModelSpec(
            self.model_id,
            "seasonal_naive",
            ModelFamily.BASELINE,
            Procedure.DIRECT,
            "Horizon-correct seasonal naive: yhat(o+h) = y(o + h - m*ceil(h/m)), the most "
            "recent observed day one or more whole seasons before the target.",
            {"season_days": self.season_days},
        )

    def feature_for(self, horizon: int) -> Feature:
        return Lag(self.season_days * weeks_back(horizon, self.season_days))


@dataclass(frozen=True)
class SameWeekdayMeanForecaster(DirectFeatureBaseline):
    model_id: str = "same_weekday_mean_4w"
    weeks: int = 4

    @property
    def spec(self) -> ModelSpec:
        return ModelSpec(
            self.model_id,
            "same_weekday_mean",
            ModelFamily.BASELINE,
            Procedure.DIRECT,
            f"Mean of the {self.weeks} most recent observed values on the target's weekday "
            "(on or before the origin).",
            {"weeks": self.weeks},
        )

    def feature_for(self, horizon: int) -> Feature:
        return SameWeekdayMean(self.weeks, first_week=weeks_back(horizon))


@dataclass(frozen=True)
class SameWeekdayMedianForecaster(DirectFeatureBaseline):
    model_id: str = "same_weekday_median_5w"
    weeks: int = 5

    @property
    def spec(self) -> ModelSpec:
        return ModelSpec(
            self.model_id,
            "same_weekday_median",
            ModelFamily.BASELINE,
            Procedure.DIRECT,
            f"Median of the {self.weeks} most recent observed values on the target's weekday "
            "(on or before the origin).",
            {"weeks": self.weeks},
        )

    def feature_for(self, horizon: int) -> Feature:
        return SameWeekdayMedian(self.weeks, first_week=weeks_back(horizon))


# --- Statistical baseline -------------------------------------------------------------

ETS_ALPHAS: tuple[float, ...] = (0.05, 0.1, 0.2, 0.3, 0.5, 0.7)
ETS_GAMMAS: tuple[float, ...] = (0.01, 0.05, 0.1, 0.2, 0.3)


@dataclass(frozen=True)
class SeasonalExponentialSmoothing(Forecaster):
    """Additive seasonal exponential smoothing, ETS(A,N,A), with a weekly season.

    State: a level ``l`` and ``m = 7`` seasonal terms. With one-step error
    ``e_t = y_t - (l_{t-1} + s_{t-m})``: ``l_t = l_{t-1} + alpha * e_t`` and
    ``s_t = s_{t-m} + gamma * e_t``. Initial state from the first week of the history
    (``l = mean``, ``s_j = y_j - l``). ``alpha`` and ``gamma`` are chosen from a fixed grid
    (``gamma <= 1 - alpha``) by the smallest in-sample one-step squared error after a
    ``burn_in_days`` warm-up; ties go to the first grid point, so the fit is
    deterministic. A missing observation updates nothing (``e_t = 0``) and is excluded
    from the error. The forecast for ``o + h`` is ``l_o + s`` of the target's weekday.
    """

    model_id: str = "seasonal_exponential_smoothing"
    season_days: int = 7
    burn_in_days: int = 28
    alphas: tuple[float, ...] = ETS_ALPHAS
    gammas: tuple[float, ...] = ETS_GAMMAS

    def __post_init__(self) -> None:
        if isinstance(self.season_days, bool) or not 2 <= self.season_days <= 366:
            raise ConfigError("season_days must be an integer within 2..366")
        if isinstance(self.burn_in_days, bool) or self.burn_in_days < 0:
            raise ConfigError("burn_in_days must be a non-negative integer")
        values = (*self.alphas, *self.gammas)
        grid = [(a, g) for a in self.alphas for g in self.gammas if g <= 1 - a]
        if not grid or not all(0 < v < 1 for v in values):
            raise ConfigError("the smoothing grid needs values strictly between 0 and 1")

    @property
    def spec(self) -> ModelSpec:
        return ModelSpec(
            self.model_id,
            "seasonal_exponential_smoothing",
            ModelFamily.BASELINE,
            Procedure.DIRECT,
            "Additive seasonal exponential smoothing ETS(A,N,A), weekly season, smoothing "
            "parameters chosen from a fixed grid by in-sample one-step squared error.",
            {
                "season_days": self.season_days,
                "burn_in_days": self.burn_in_days,
                "alphas": list(self.alphas),
                "gammas": list(self.gammas),
            },
        )

    def fit(self, history: DemandSeries) -> FittedExponentialSmoothing:
        values = history.to_series().to_numpy(dtype="float64")
        season = self.season_days
        if len(values) < season + self.burn_in_days + 1:
            raise ModelFitError(
                f"{len(values)} days of history; exponential smoothing needs at least "
                f"{season + self.burn_in_days + 1}",
                "insufficient_history",
            )
        if np.isnan(values[:season]).any():
            raise ModelFitError(
                "the first week of the history must be complete to initialise", "missing_input"
            )
        grid = [(a, g) for a in self.alphas for g in self.gammas if g <= 1 - a]
        alpha = np.array([a for a, _ in grid])
        gamma = np.array([g for _, g in grid])
        level = np.full(len(grid), values[:season].mean())
        seasonal = np.tile(values[:season] - values[:season].mean(), (len(grid), 1))
        sse = np.zeros(len(grid))
        for t in range(season, len(values)):
            position = t % season
            observed = values[t]
            if np.isnan(observed):
                continue  # no update: level and seasonal terms carry over
            error = observed - (level + seasonal[:, position])
            if t >= season + self.burn_in_days:
                sse += error * error
            level = level + alpha * error
            seasonal[:, position] = seasonal[:, position] + gamma * error
        best = int(np.argmin(sse))  # first minimum: deterministic tie-break
        diagnostics = FitDiagnostics(
            n_train_rows=int(np.isfinite(values[season + self.burn_in_days :]).sum()),
            n_dropped_rows=int(np.isnan(values).sum()),
            train_first=history.start,
            train_last=history.end,
            n_features=2,
            in_sample_mae=None,
        )
        return FittedExponentialSmoothing(
            origin=history.end,
            diagnostics=diagnostics,
            alpha=float(alpha[best]),
            gamma=float(gamma[best]),
            level=float(level[best]),
            seasonal=tuple(float(v) for v in seasonal[best]),
            last_index=len(values) - 1,
            season_days=season,
        )


class FittedExponentialSmoothing(FittedForecaster):
    def __init__(
        self,
        *,
        origin: date,
        diagnostics: FitDiagnostics,
        alpha: float,
        gamma: float,
        level: float,
        seasonal: tuple[float, ...],
        last_index: int,
        season_days: int,
    ) -> None:
        self.origin = origin
        self.diagnostics = diagnostics
        self.alpha = alpha
        self.gamma = gamma
        self.level = level
        self.seasonal = seasonal
        self._last_index = last_index
        self._season = season_days

    def seasonal_at_step(self, step: int) -> float:
        """The seasonal term added to the level ``step`` days after the origin."""
        return self.seasonal[(self._last_index + step) % self._season]

    def parameters(self) -> dict[str, float]:
        """Chosen ``alpha`` and ``gamma``, the final level and the seasonal terms.

        ``season_step_k`` is the term for steps ``k, k + m, k + 2m, ...`` (``m`` the
        season length), so the forecast ``h`` days ahead is ``level`` plus
        ``season_step_k`` with ``k = (h - 1) mod m + 1``, clipped at zero.
        """
        terms = {"alpha": self.alpha, "gamma": self.gamma, "level": self.level}
        for step in range(1, self._season + 1):
            terms[f"season_step_{step}"] = self.seasonal_at_step(step)
        return terms

    def forecast(self, steps: int) -> ForecastPath:
        targets = target_dates(self.origin, steps)
        raw = np.array([self.level + self.seasonal_at_step(h) for h in range(1, steps + 1)])
        predictions = pd.Series(np.maximum(raw, 0.0), index=targets, name="prediction")
        return ForecastPath(
            self.origin,
            predictions,
            {},
            raw_predictions=pd.Series(raw, index=targets, name="prediction_raw"),
            n_clipped=int((raw < 0).sum()),
        )


# --- Regressions (recursive) ----------------------------------------------------------


def legacy_feature_set(trend_anchor: date = DEFAULT_TREND_ANCHOR) -> FeatureSet:
    """The 32 legacy notebook features (cell index 14), in the notebook's column order.

    The notebook's ``trend_index`` counts days from the first date of its data
    (2023-01-01). Under least squares with an intercept the anchor of a linear trend is
    irrelevant: centring removes it. It is therefore fixed at 2023-01-01, which keeps the
    feature-set fingerprint independent of the training window.

    Weekday and month dummies are full sets; with the intercept and ``is_weekend`` the
    design has three exact linear dependencies, as in the notebook. A fourth appears while
    the training data span only 2023-2024: ``trend - 365 * (year - 2023) - (day_of_year
    - 1)`` counts leap days, and is 0 on every day until 2025-01-01.
    """
    features: list[Feature] = [
        Trend(trend_anchor),
        Calendar(CalendarField.YEAR),
        Calendar(CalendarField.DAY_OF_YEAR),
        Calendar(CalendarField.IS_WEEKEND),
        LegacyAnnualCycle("sin"),
        LegacyAnnualCycle("cos"),
        Lag(1),
        Lag(7),
        Lag(14),
        Lag(28),
        RollingMean(7),
        RollingMean(28),
        RollingStd(28),
        *(WeekdayIndicator(day) for day in range(1, 8)),
        *(MonthIndicator(month) for month in range(1, 13)),
    ]
    return FeatureSet(tuple(features), name=LEGACY_FEATURE_SET_NAME)


def atlas_regression_feature_set() -> FeatureSet:
    """``atlas-m2-regression-v1``: 18 non-collinear M1 features (plus the intercept).

    Weekday indicators with Monday as the reference level, annual Fourier terms of order
    1 and 2, a trend from the fixed M1 anchor, three federal-holiday indicators (the
    federal day off, and the days before and after the actual holiday), and four demand
    features: the previous day, the same weekday a week earlier, the mean of the
    previous four same weekdays and the 28-day mean. All offsets are at most 28 days.

    The actual-date holiday indicator is deliberately left out: it differs from the
    day-off indicator only when a holiday falls on a weekend, which happens on two
    training days in 2023 and on no backtest target day, so its coefficient would be
    identified by one event and never tested.
    """
    features: list[Feature] = [
        *(WeekdayIndicator(day) for day in range(2, 8)),
        AnnualFourier(1, "sin"),
        AnnualFourier(1, "cos"),
        AnnualFourier(2, "sin"),
        AnnualFourier(2, "cos"),
        Trend(DEFAULT_TREND_ANCHOR),
        HolidayIndicator(HolidayRelation.OBSERVED),
        HolidayIndicator(HolidayRelation.EVE),
        HolidayIndicator(HolidayRelation.AFTER),
        Lag(1),
        Lag(7),
        SameWeekdayMean(4),
        RollingMean(28),
    ]
    return FeatureSet(tuple(features), name=ATLAS_FEATURE_SET_NAME)


@dataclass(frozen=True)
class RecursiveLinearForecaster(Forecaster):
    """A one-step linear model on M1 features, iterated by :func:`recursive_forecast`.

    Training rows are horizon-1 rows for every target from the first date with enough
    history through the origin, built from observed demand. Rows that read a missing
    observation are dropped explicitly and counted (``n_dropped_rows``). Each recursive
    step clips the prediction at 0 (counted) and, when ``round_feedback``, rounds it to
    an integer (half to even) *before* it is fed back; the fed-back value is the
    recorded prediction, and the unclipped, unrounded value is kept as the raw one.

    Anticipated conditions become :class:`~atlas.errors.ForecastError` with a code:
    ``insufficient_history`` and ``insufficient_training_rows`` (fit), the fit errors of
    :func:`~atlas.regression.fit_linear`, and ``missing_input`` when a recursive step
    would read a missing observation. Recursion cannot skip a step, so such a failure
    applies to every horizon of that origin; nothing is imputed.
    """

    model_id: str
    model_type: str
    feature_set: FeatureSet
    method: FitMethod
    min_training_rows: int
    round_feedback: bool
    description: str
    implementation_version: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.min_training_rows, bool) or self.min_training_rows < 1:
            raise ConfigError("min_training_rows must be a positive integer")

    @property
    def spec(self) -> ModelSpec:
        return ModelSpec(
            self.model_id,
            self.model_type,
            ModelFamily.REGRESSION,
            Procedure.RECURSIVE,
            self.description,
            {
                "fit_method": self.method.value,
                "intercept": True,
                "min_training_rows": self.min_training_rows,
                "training_horizon": 1,
                "clip_at_zero": True,
                "round_feedback": "half_even_before_feedback" if self.round_feedback else "none",
                "features": list(self.feature_set.names),
            },
            self.feature_set.name,
            self.feature_set.fingerprint(),
            self.implementation_version,
        )

    def fit(self, history: DemandSeries) -> FittedRecursiveLinear:
        origin = history.end
        try:
            matrix = training_features(
                history,
                origin=origin,
                horizon=1,
                feature_set=self.feature_set,
                allow_incomplete=True,
            )
        except InsufficientHistoryError as exc:
            raise ModelFitError(str(exc), "insufficient_history") from exc
        if matrix.target is None:  # training rows always carry targets
            raise AssertionError("training_features returned no target")
        complete = matrix.complete
        x = matrix.frame.loc[complete]
        y = matrix.target.loc[complete]
        if len(x) < self.min_training_rows:
            raise ModelFitError(
                f"{len(x)} complete training rows through {origin}; at least "
                f"{self.min_training_rows} required",
                "insufficient_training_rows",
            )
        result = fit_linear(x, y, method=self.method)
        design = result.diagnostics
        diagnostics = FitDiagnostics(
            n_train_rows=len(x),
            n_dropped_rows=int((~complete).sum()),
            train_first=pd.Timestamp(x.index[0]).date(),
            train_last=pd.Timestamp(x.index[-1]).date(),
            n_features=len(self.feature_set),
            rank=design.rank,
            solver_rank=design.solver_rank,
            condition_number=design.condition_number,
            restricted_condition_number=design.restricted_condition_number,
            smallest_kept_ratio=design.smallest_kept_ratio,
            largest_dropped_ratio=design.largest_dropped_ratio,
            max_training_leverage=design.max_training_leverage,
            identifiable=design.identifiable,
            in_sample_mae=result.in_sample_mae,
            feature_set_fingerprint=self.feature_set.fingerprint(),
        )
        return FittedRecursiveLinear(self, history, result, diagnostics)


class FittedRecursiveLinear(FittedForecaster):
    def __init__(
        self,
        model: RecursiveLinearForecaster,
        history: DemandSeries,
        result: LinearFit,
        diagnostics: FitDiagnostics,
    ) -> None:
        self._model = model
        self._history = history
        self.feature_set = model.feature_set
        self.result = result
        self.origin = history.end
        self.diagnostics = diagnostics

    def postprocess(self, value: float) -> float:
        """Clip at 0, then round half to even when the model feeds back integers."""
        value = max(value, 0.0)
        if self._model.round_feedback:
            value = float(round(value))  # Python round: half to even, like int(round(x))
        return value

    def coefficients(self) -> pd.Series | None:
        """Coefficients with the intercept, or ``None`` when they are not identifiable."""
        if not self.result.diagnostics.identifiable:
            return None
        table = self.result.coefficient_table()
        combined: pd.Series = pd.concat([pd.Series({"intercept": self.result.intercept}), table])
        return combined

    def predict_rows(self, rows: pd.DataFrame) -> np.ndarray:
        """Teacher-forced predictions for given feature rows, clipped at 0 (not rounded).

        For the legacy one-step holdout reproduction and the observed-input diagnostic
        only: these rows read *observed* demand after the origin. Backtests and projections
        use :meth:`forecast`.
        """
        values = self.result.predict(rows.loc[:, list(self.feature_set.names)].to_numpy())
        return np.asarray(np.maximum(values, 0.0), dtype="float64")

    def forecast(self, steps: int) -> ForecastPath:
        check_steps(steps)
        raw: list[float] = []
        estimable: list[bool] = []
        leverage: list[float] = []

        def predictor(row: pd.Series) -> float:
            x = row.to_numpy(dtype="float64")
            value = float(self.result.predict(x)[0])
            raw.append(value)
            estimable.append(bool(self.result.is_estimable(x)[0]))
            leverage.append(float(self.result.leverage(x)[0]))
            return self.postprocess(value)

        try:
            forecast = recursive_forecast(
                self._history,
                origin=self.origin,
                steps=steps,
                predictor=predictor,
                feature_set=self.feature_set,
            )
        except DataQualityError as exc:
            raise ForecastError(str(exc), "missing_input") from exc
        except InsufficientHistoryError as exc:
            raise ForecastError(str(exc), "insufficient_history") from exc
        index = forecast.predictions.index
        raw_series = pd.Series(raw, index=index, name="prediction_raw", dtype="float64")
        return ForecastPath(
            self.origin,
            forecast.predictions.rename("prediction"),
            {},
            raw_predictions=raw_series,
            estimable=np.asarray(estimable, dtype=bool),
            leverage=np.asarray(leverage, dtype="float64"),
            n_clipped=int((raw_series < 0).sum()),
            feature_rows=forecast.features.frame,
            inputs=forecast.inputs,
        )


def legacy_linear_regression(
    model_id: str = "legacy_linear_regression", min_training_rows: int = 365
) -> RecursiveLinearForecaster:
    return RecursiveLinearForecaster(
        model_id=model_id,
        model_type="legacy_linear_regression",
        feature_set=legacy_feature_set(),
        method=FitMethod.LEGACY_MIN_NORM,
        min_training_rows=min_training_rows,
        round_feedback=True,
        description=(
            "Faithful legacy notebook regression: 32 collinear features, minimum-norm least "
            "squares with intercept (scikit-learn LinearRegression semantics), recursive "
            "30-day procedure with clip-at-0 and integer rounding before feedback."
        ),
    )


def atlas_linear_regression(
    model_id: str = "atlas_linear_regression", min_training_rows: int = 365
) -> RecursiveLinearForecaster:
    return RecursiveLinearForecaster(
        model_id=model_id,
        model_type="atlas_linear_regression",
        feature_set=atlas_regression_feature_set(),
        method=FitMethod.STANDARDIZED_OLS,
        min_training_rows=min_training_rows,
        round_feedback=False,
        description=(
            "Modernised regression atlas-m2-regression-v1: 18 non-collinear M1 features plus "
            "intercept, OLS on standardised columns (full rank required), recursive, "
            "clipped at 0."
        ),
    )
