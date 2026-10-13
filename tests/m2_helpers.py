"""Shared helpers for the M2 tests: synthetic series, small instrumented models, plans.

Expected values in the M2 tests come from closed forms or from plain-Python reference
calculations written in the tests, never from the code under test.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from atlas.backtest import BacktestPlan, Period, PeriodRole, TrainingWindow
from atlas.errors import ForecastError, ModelFitError
from atlas.features import FeatureSet, Lag, forecast_features
from atlas.models import (
    FitDiagnostics,
    FittedForecaster,
    Forecaster,
    ForecastPath,
    ModelFamily,
    ModelSpec,
    Procedure,
    target_dates,
)
from atlas.series import DemandSeries


def days(start: date, count: int) -> list[date]:
    return [start + timedelta(days=k) for k in range(count)]


def series_from(
    start: date, values: Sequence[float | None], *, name: str = "y", allow_missing: bool = False
) -> DemandSeries:
    """A series from consecutive daily values (``None`` = missing observation)."""
    dates = days(start, len(values))
    kept = [(d, v) for d, v in zip(dates, values, strict=True) if v is not None]
    return DemandSeries.from_observations(
        [d for d, _ in kept],
        [v for _, v in kept],
        name=name,
        allow_missing=allow_missing or len(kept) < len(dates),
    )


def linear_values(count: int, intercept: int = 1000, slope: int = 1) -> list[int]:
    """``y(k) = intercept + slope * k``: every lag and window has a closed form."""
    return [intercept + slope * k for k in range(count)]


def noisy_weekly_values(count: int, seed: int, level: int = 9000) -> list[int]:
    """Integral demand with weekly seasonality, a slow trend and noise (seeded)."""
    rng = random.Random(seed)
    return [
        max(
            0,
            round(
                level
                + 2 * k
                + 1500 * math.sin(2 * math.pi * k / 7)
                + 400 * math.cos(2 * math.pi * k / 365.25)
                + rng.gauss(0, 500)
            ),
        )
        for k in range(count)
    ]


def noisy_series(start: date, count: int, seed: int) -> DemandSeries:
    return series_from(start, noisy_weekly_values(count, seed))


def perturbed_after(series: DemandSeries, cutoff: date, seed: int) -> DemandSeries:
    """Every value after ``cutoff`` replaced by an unrelated random integer."""
    rng = random.Random(seed)
    values = series.to_series()
    dates = [ts.date() for ts in pd.DatetimeIndex(values.index)]
    new: list[float | None] = []
    for d, v in zip(dates, values.to_numpy(), strict=True):
        if d <= cutoff:
            new.append(None if np.isnan(v) else float(v))
        else:
            new.append(float(rng.randint(0, 50_000)))
    return series_from(dates[0], new, name=series.name)


def single_period_plan(
    first_origin: date,
    last_origin: date,
    *,
    max_horizon: int = 30,
    min_history_days: int = 1,
    stride_days: int = 1,
    role: PeriodRole = PeriodRole.SELECTION,
    window_days: int | None = None,
    data_delay_days: int = 0,
    allow_incomplete_horizons: bool = False,
    observed_input_diagnostic: bool = False,
    name: str = "development",
) -> BacktestPlan:
    return BacktestPlan(
        periods=(
            Period(
                name,
                role,
                first_origin,
                last_origin,
                stride_days,
                allow_incomplete_horizons,
            ),
        ),
        max_horizon=max_horizon,
        min_history_days=min_history_days,
        training_window=TrainingWindow(window_days),
        data_delay_days=data_delay_days,
        observed_input_diagnostic=observed_input_diagnostic,
    )


# --- Instrumented models ------------------------------------------------------------------


def _spec(model_id: str, family: ModelFamily = ModelFamily.BASELINE) -> ModelSpec:
    return ModelSpec(model_id, "test_" + model_id, family, Procedure.DIRECT, "test model")


@dataclass
class SpyForecaster(Forecaster):
    """Forecasts a constant and records every history it was given."""

    model_id: str = "spy"
    value: float = 100.0
    seen: list[tuple[date, date, int, float]] = field(default_factory=list)

    @property
    def spec(self) -> ModelSpec:
        return _spec(self.model_id)

    def fit(self, history: DemandSeries) -> FittedForecaster:
        values = history.to_series()
        self.seen.append((history.start, history.end, len(history), float(np.nansum(values))))
        return _ConstantFit(history.end, self.value)


class _ConstantFit(FittedForecaster):
    def __init__(self, origin: date, value: float) -> None:
        self.origin = origin
        self.value = value
        self.diagnostics = FitDiagnostics()

    def forecast(self, steps: int) -> ForecastPath:
        index = target_dates(self.origin, steps)
        return ForecastPath(self.origin, pd.Series(self.value, index=index, dtype="float64"))


@dataclass(frozen=True)
class FailingForecaster(Forecaster):
    """Raises ``ModelFitError`` (fit) or ``ForecastError`` (forecast) at chosen origins."""

    model_id: str = "failing"
    fail_at: frozenset[date] = frozenset()
    stage: str = "fit"
    code: str = "planted_failure"
    value: float = 100.0

    @property
    def spec(self) -> ModelSpec:
        return _spec(self.model_id)

    def fit(self, history: DemandSeries) -> FittedForecaster:
        if self.stage == "fit" and history.end in self.fail_at:
            raise ModelFitError(f"planted fit failure at {history.end}", self.code)
        fitted = _ConstantFit(history.end, self.value)
        if self.stage == "forecast" and history.end in self.fail_at:
            return _FailingFit(history.end, self.code)
        return fitted


class _FailingFit(FittedForecaster):
    def __init__(self, origin: date, code: str) -> None:
        self.origin = origin
        self.code = code
        self.diagnostics = FitDiagnostics()

    def forecast(self, steps: int) -> ForecastPath:
        raise ForecastError(f"planted forecast failure at {self.origin}", self.code)


@dataclass(frozen=True)
class LeakyForecaster(Forecaster):
    """Asks the M1 builder for a horizon-2 row from a lag-1 feature: a LeakageError."""

    model_id: str = "leaky"

    @property
    def spec(self) -> ModelSpec:
        return _spec(self.model_id)

    def fit(self, history: DemandSeries) -> FittedForecaster:
        forecast_features(
            history, origin=history.end, horizons=(2,), feature_set=FeatureSet((Lag(1),))
        )
        return _ConstantFit(history.end, 1.0)  # pragma: no cover - never reached


@dataclass(frozen=True)
class RaisingForecaster(Forecaster):
    """Raises an arbitrary exception type: a bug, which must stop the backtest."""

    model_id: str = "raising"
    error: type[Exception] = ValueError

    @property
    def spec(self) -> ModelSpec:
        return _spec(self.model_id)

    def fit(self, history: DemandSeries) -> FittedForecaster:
        raise self.error("planted bug")


@dataclass(frozen=True)
class ContractBreakingForecaster(Forecaster):
    """Returns predictions on the wrong dates, or negative/NaN values without a failure."""

    model_id: str = "contract_breaking"
    mode: str = "shifted_index"

    @property
    def spec(self) -> ModelSpec:
        return _spec(self.model_id)

    def fit(self, history: DemandSeries) -> FittedForecaster:
        return _BrokenFit(history.end, self.mode)


class _BrokenFit(FittedForecaster):
    def __init__(self, origin: date, mode: str) -> None:
        self.origin = origin
        self.mode = mode
        self.diagnostics = FitDiagnostics()

    def forecast(self, steps: int) -> ForecastPath:
        index = target_dates(self.origin, steps)
        if self.mode == "shifted_index":
            return ForecastPath(self.origin, pd.Series(1.0, index=index - pd.Timedelta(days=1)))
        if self.mode == "negative":
            return ForecastPath(self.origin, pd.Series(-1.0, index=index))
        return ForecastPath(self.origin, pd.Series(np.nan, index=index))


def records_for(frame: pd.DataFrame, model_id: str, origin: date) -> pd.DataFrame:
    """The records of one model at one origin, in horizon order."""
    selected = frame[(frame["model_id"] == model_id) & (frame["origin"] == pd.Timestamp(origin))]
    return selected.sort_values("horizon")


def as_dates(values: Iterable[pd.Timestamp]) -> list[date]:
    return [pd.Timestamp(v).date() for v in values]
