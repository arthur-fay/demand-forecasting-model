"""Rolling-origin backtesting and future projections through one prediction path (M2).

For every forecast origin ``o`` of a :class:`BacktestPlan` and every model:

1. ``history = training_window.history(series, o - data_delay_days)``: a new
   :class:`DemandSeries` that *ends at* the data cutoff (barrier 0; the M1 builder adds
   its own truncation and runtime guard). With the default delay of 0 the cutoff is
   the origin itself;
2. :func:`forecast_at_origin` fits the model on that history and forecasts. Backtests
   and :func:`project` both call exactly this function, so the historical evaluation
   exercises the code that produces operational forecasts;
3. only after every forecast of the run exists does the engine look up the actual value
   of each target date (:func:`_attach_actuals`). Actuals never enter model code.

Every ``(period, model, origin, horizon)`` yields exactly one record, with ``status``:

===================  =================================================================
``ok``               a prediction and an observed actual (used by the metrics)
``failed``           no prediction; ``failure_stage`` (fit or forecast), ``failure_code``
                     and ``failure_message`` say why
``missing_actual``   a prediction whose target date has no observation in the snapshot
``beyond_snapshot``  a prediction whose target date is after the last day of the
                     snapshot (only in periods that allow incomplete horizons)
===================  =================================================================

Failure policy. A model reports the conditions it anticipates by raising
:class:`~atlas.errors.ForecastError` with a code; the engine records those and carries
on. Any other exception, including :class:`~atlas.errors.LeakageError`, is a bug and
stops the run, as does a model output that breaks the forecasting contract
(:func:`validate_path`). Nothing is dropped: :meth:`BacktestResult.check_complete`
verifies the record count.

The engine has no randomness and processes origins in chronological order; models are
immutable and every fit returns a new object, so no state passes between origins.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from typing import Any, Final

import numpy as np
import pandas as pd

from atlas.errors import ConfigError, ForecastError
from atlas.features import training_features
from atlas.metrics import seasonal_naive_scale
from atlas.models import (
    Failure,
    FailureStage,
    FitDiagnostics,
    FittedForecaster,
    FittedRecursiveLinear,
    Forecaster,
    ForecastPath,
    ModelSpec,
    check_steps,
    target_dates,
)
from atlas.series import DemandSeries

RECORD_TYPE_LEGACY_HOLDOUT: Final = "legacy_one_step_holdout"
RECORD_TYPE_BACKTEST: Final = "historical_backtest"
RECORD_TYPE_PROJECTION: Final = "future_projection"
RECORD_TYPE_OBSERVED_INPUTS: Final = "diagnostic_observed_inputs"
MAX_HORIZON_LIMIT: Final = 366
MAX_DATA_DELAY: Final = 60

RECORD_COLUMNS: Final = (
    "record_type",
    "period",
    "model_id",
    "origin",
    "data_cutoff",
    "target_date",
    "horizon",
    "prediction",
    "prediction_raw",
    "actual",
    "status",
    "failure_stage",
    "failure_code",
    "failure_message",
    "estimable",
    "leverage",
    "mase_scale",
)
FIT_COLUMNS: Final = (
    "period",
    "model_id",
    "origin",
    "data_cutoff",
    "status",
    "failure_stage",
    "failure_code",
    "failure_message",
    "train_first",
    "train_last",
    "n_train_rows",
    "n_dropped_rows",
    "n_features",
    "rank",
    "solver_rank",
    "identifiable",
    "condition_number",
    "restricted_condition_number",
    "smallest_kept_ratio",
    "largest_dropped_ratio",
    "max_training_leverage",
    "in_sample_mae",
    "n_clipped",
    "n_nonestimable",
    "n_high_leverage",
    "max_prediction_leverage",
    "feature_set_fingerprint",
)
COEFFICIENT_COLUMNS: Final = ("model_id", "origin", "term", "coefficient")
DIAGNOSTIC_COLUMNS: Final = (
    "record_type",
    "period",
    "model_id",
    "origin",
    "target_date",
    "horizon",
    "prediction_observed_inputs",
    "actual",
)


class RecordStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    MISSING_ACTUAL = "missing_actual"
    BEYOND_SNAPSHOT = "beyond_snapshot"


class PeriodRole(StrEnum):
    SELECTION = "selection"  # models are compared and one is selected here
    CONFIRMATION = "confirmation"  # later, disjoint targets; reported, never used to choose
    DESCRIPTIVE = "descriptive"  # extra views (e.g. the legacy holdout window); never used


# --- Plan ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Period:
    """Origins ``first_origin, first_origin + stride, ...`` up to ``last_origin``.

    ``allow_incomplete_horizons`` lets the last origins forecast past the end of the
    snapshot; those records get status ``beyond_snapshot`` instead of being refused.
    """

    name: str
    role: PeriodRole
    first_origin: date
    last_origin: date
    stride_days: int = 1
    allow_incomplete_horizons: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", self.name):
            raise ConfigError(f"invalid period name {self.name!r}")
        object.__setattr__(self, "role", PeriodRole(self.role))
        if self.last_origin < self.first_origin:
            raise ConfigError(f"period {self.name}: last_origin precedes first_origin")
        if isinstance(self.stride_days, bool) or not 1 <= self.stride_days <= 366:
            raise ConfigError(f"period {self.name}: stride_days must be within 1..366")

    def origins(self) -> tuple[date, ...]:
        span = (self.last_origin - self.first_origin).days
        return tuple(
            self.first_origin + timedelta(days=k) for k in range(0, span + 1, self.stride_days)
        )


@dataclass(frozen=True)
class TrainingWindow:
    """Expanding (``days is None``, the default) or sliding window of the last ``days`` days."""

    days: int | None = None

    def __post_init__(self) -> None:
        if self.days is not None and (isinstance(self.days, bool) or self.days < 1):
            raise ConfigError("a sliding training window needs a positive number of days")

    @property
    def kind(self) -> str:
        return "expanding" if self.days is None else "sliding"

    def history(self, series: DemandSeries, cutoff: date) -> DemandSeries:
        """The information set at ``cutoff``: data through ``cutoff`` only."""
        if self.days is None:
            return series.through(cutoff)
        first = max(series.start, cutoff - timedelta(days=self.days - 1))
        return series.window(first, cutoff)


@dataclass(frozen=True)
class BacktestPlan:
    """Which origins to evaluate, how far ahead, and on how much history.

    At most one ``selection`` and one ``confirmation`` period; when both are present the
    selection period comes first and scores no target day that the confirmation period
    scores. ``descriptive`` periods may overlap anything and are never used for choices.

    ``data_delay_days`` (``d``) simulates a reporting delay: the forecast issued at origin
    ``o`` sees data through ``o - d`` only, and still targets ``o + 1 .. o + H``.
    """

    periods: tuple[Period, ...]
    max_horizon: int = 30
    min_history_days: int = 365
    training_window: TrainingWindow = field(default_factory=TrainingWindow)
    data_delay_days: int = 0
    observed_input_diagnostic: bool = False

    def __post_init__(self) -> None:
        if not self.periods:
            raise ConfigError("a backtest plan needs at least one period")
        names = [p.name for p in self.periods]
        if len(set(names)) != len(names):
            raise ConfigError(f"period names must be unique: {names}")
        if isinstance(self.max_horizon, bool) or not 1 <= self.max_horizon <= MAX_HORIZON_LIMIT:
            raise ConfigError(f"max_horizon must be within 1..{MAX_HORIZON_LIMIT}")
        if isinstance(self.min_history_days, bool) or self.min_history_days < 1:
            raise ConfigError("min_history_days must be a positive integer")
        if isinstance(self.data_delay_days, bool) or not (
            0 <= self.data_delay_days <= MAX_DATA_DELAY
        ):
            raise ConfigError(f"data_delay_days must be within 0..{MAX_DATA_DELAY}")
        if self.observed_input_diagnostic and self.data_delay_days:
            raise ConfigError("the observed-input diagnostic requires data_delay_days = 0")
        for role in (PeriodRole.SELECTION, PeriodRole.CONFIRMATION):
            if sum(p.role is role for p in self.periods) > 1:
                raise ConfigError(f"at most one {role.value} period is allowed")
        selection = self.period_with_role(PeriodRole.SELECTION)
        confirmation = self.period_with_role(PeriodRole.CONFIRMATION)
        if selection is not None and confirmation is not None:
            last_selection_target = selection.last_origin + timedelta(days=self.max_horizon)
            if last_selection_target > confirmation.first_origin:
                raise ConfigError(
                    f"periods {selection.name} and {confirmation.name} overlap: "
                    f"{selection.name} scores targets through {last_selection_target}, but "
                    f"{confirmation.name} scores targets from "
                    f"{confirmation.first_origin + timedelta(days=1)}"
                )

    def period_with_role(self, role: PeriodRole) -> Period | None:
        return next((p for p in self.periods if p.role is role), None)

    def origins(self) -> tuple[date, ...]:
        """Every distinct origin, in chronological order."""
        return tuple(sorted({o for p in self.periods for o in p.origins()}))

    def memberships(self) -> dict[date, tuple[str, ...]]:
        """The periods each origin belongs to, in plan order."""
        result: dict[date, list[str]] = {}
        for period in self.periods:
            for origin in period.origins():
                result.setdefault(origin, []).append(period.name)
        return {origin: tuple(names) for origin, names in result.items()}

    def stride_by_period(self) -> dict[str, int]:
        return {p.name: p.stride_days for p in self.periods}

    def scheduled_records(self, n_models: int) -> int:
        return sum(len(p.origins()) for p in self.periods) * n_models * self.max_horizon

    def check_against(self, series: DemandSeries) -> None:
        """Data-dependent checks; raises :class:`ConfigError` listing every problem."""
        problems: list[str] = []
        first_cutoff = self.origins()[0] - timedelta(days=self.data_delay_days)
        history_days = (first_cutoff - series.start).days + 1
        if history_days < self.min_history_days:
            problems.append(
                f"the first origin's data cutoff {first_cutoff} has {history_days} day(s) of "
                f"history (series starts {series.start}); min_history_days is "
                f"{self.min_history_days}"
            )
        for period in self.periods:
            last_target = period.last_origin + timedelta(days=self.max_horizon)
            if period.allow_incomplete_horizons:
                if period.last_origin >= series.end:
                    problems.append(
                        f"period {period.name}: the last origin {period.last_origin} leaves no "
                        f"target inside the series ending {series.end}"
                    )
            elif last_target > series.end:
                problems.append(
                    f"period {period.name}: the last origin {period.last_origin} needs actuals "
                    f"through {last_target}, but the series ends {series.end}"
                )
        if problems:
            raise ConfigError("backtest plan does not fit the data: " + "; ".join(problems))


# --- One origin ---------------------------------------------------------------------


@dataclass(frozen=True)
class OriginForecast:
    """What one model produced at one data cutoff: a path, or the reason it failed."""

    model_id: str
    origin: date
    path: ForecastPath | None
    diagnostics: FitDiagnostics | None
    failure: Failure | None
    fitted: FittedForecaster | None = field(default=None, repr=False)


def validate_path(path: ForecastPath, origin: date, steps: int) -> str | None:
    """Why a model's output breaks the forecasting contract, or ``None`` if it is valid.

    Every horizon has either a finite, non-negative prediction or a failure entry, never
    both and never neither.
    """
    expected = target_dates(origin, steps)
    if path.origin != origin:
        return f"path origin {path.origin} differs from the forecast origin {origin}"
    index = pd.DatetimeIndex(path.predictions.index)
    if not index.equals(expected):
        return f"predictions must be indexed by {expected[0].date()}..{expected[-1].date()}"
    unknown = sorted(set(path.failures) - set(range(1, steps + 1)))
    if unknown:
        return f"failures reported for horizons outside 1..{steps}: {unknown}"
    values = path.predictions.to_numpy(dtype="float64")
    for horizon in range(1, steps + 1):
        value = values[horizon - 1]
        if horizon in path.failures:
            if not np.isnan(value):
                return f"horizon {horizon} has both a prediction and a failure"
        elif not np.isfinite(value) or value < 0:
            return f"horizon {horizon} has an invalid prediction {value!r} and no failure"
    for name, extra in (
        ("raw_predictions", path.raw_predictions),
        ("estimable", path.estimable),
        ("leverage", path.leverage),
    ):
        if extra is not None and len(extra) != steps:
            return f"{name} has {len(extra)} values for {steps} steps"
    return None


def forecast_at_origin(model: Forecaster, history: DemandSeries, steps: int) -> OriginForecast:
    """Fit ``model`` on ``history`` (ending at the data cutoff) and forecast ``steps`` days.

    The single prediction path of ATLAS: rolling-origin backtests and future projections
    both call it. A :class:`~atlas.errors.ForecastError` becomes a recorded
    :class:`~atlas.models.Failure`; any other exception propagates. An output that breaks
    the forecasting contract raises ``AssertionError`` (a bug in the model).
    """
    check_steps(steps)
    model_id = model.spec.model_id
    origin = history.end
    try:
        fitted = model.fit(history)
    except ForecastError as exc:
        failure = Failure(FailureStage.FIT, exc.code, str(exc))
        return OriginForecast(model_id, origin, None, None, failure)
    try:
        path = fitted.forecast(steps)
    except ForecastError as exc:
        failure = Failure(FailureStage.FORECAST, exc.code, str(exc))
        return OriginForecast(model_id, origin, None, fitted.diagnostics, failure, fitted)
    problem = validate_path(path, origin, steps)
    if problem is not None:
        raise AssertionError(f"model {model_id} broke the forecasting contract: {problem}")
    return OriginForecast(model_id, origin, path, fitted.diagnostics, None, fitted)


def _fit_row(period: str, origin: date, outcome: OriginForecast) -> dict[str, Any]:
    diagnostics = outcome.diagnostics or FitDiagnostics()
    failure = outcome.failure
    path = outcome.path
    return {
        "period": period,
        "model_id": outcome.model_id,
        "origin": pd.Timestamp(origin),
        "data_cutoff": pd.Timestamp(outcome.origin),
        "status": RecordStatus.FAILED.value if failure else RecordStatus.OK.value,
        "failure_stage": failure.stage.value if failure else None,
        "failure_code": failure.code if failure else None,
        "failure_message": failure.message if failure else None,
        "train_first": diagnostics.train_first,
        "train_last": diagnostics.train_last,
        "n_train_rows": diagnostics.n_train_rows,
        "n_dropped_rows": diagnostics.n_dropped_rows,
        "n_features": diagnostics.n_features,
        "rank": diagnostics.rank,
        "solver_rank": diagnostics.solver_rank,
        "identifiable": diagnostics.identifiable,
        "condition_number": diagnostics.condition_number,
        "restricted_condition_number": diagnostics.restricted_condition_number,
        "smallest_kept_ratio": diagnostics.smallest_kept_ratio,
        "largest_dropped_ratio": diagnostics.largest_dropped_ratio,
        "max_training_leverage": diagnostics.max_training_leverage,
        "in_sample_mae": diagnostics.in_sample_mae,
        "n_clipped": path.n_clipped if path else None,
        "n_nonestimable": path.n_nonestimable if path and path.estimable is not None else None,
        "n_high_leverage": path.n_high_leverage if path and path.leverage is not None else None,
        "max_prediction_leverage": path.max_leverage if path else None,
        "feature_set_fingerprint": diagnostics.feature_set_fingerprint,
    }


def _record_rows(
    record_type: str,
    period: str,
    origin: date,
    outcome: OriginForecast,
    horizons: int,
    delay: int,
    mase_scale: float | None,
) -> list[dict[str, Any]]:
    """Records for horizons ``1 .. horizons`` from ``origin`` (steps ``delay + h``)."""
    rows: list[dict[str, Any]] = []
    path = outcome.path
    for horizon in range(1, horizons + 1):
        step = delay + horizon
        failure = outcome.failure
        prediction = raw = leverage = np.nan
        estimable: bool | None = None
        if failure is None and path is not None:
            failure = path.failures.get(step)
            if failure is None:
                prediction = float(path.predictions.iloc[step - 1])
                raw = (
                    float(path.raw_predictions.iloc[step - 1])
                    if path.raw_predictions is not None
                    else prediction
                )
                if path.estimable is not None:
                    estimable = bool(path.estimable[step - 1])
                if path.leverage is not None:
                    leverage = float(path.leverage[step - 1])
        rows.append(
            {
                "record_type": record_type,
                "period": period,
                "model_id": outcome.model_id,
                "origin": pd.Timestamp(origin),
                "data_cutoff": pd.Timestamp(outcome.origin),
                "target_date": pd.Timestamp(origin + timedelta(days=horizon)),
                "horizon": horizon,
                "prediction": prediction,
                "prediction_raw": raw,
                "actual": np.nan,
                "status": RecordStatus.FAILED.value if failure else RecordStatus.OK.value,
                "failure_stage": failure.stage.value if failure else None,
                "failure_code": failure.code if failure else None,
                "failure_message": failure.message if failure else None,
                "estimable": estimable,
                "leverage": leverage,
                "mase_scale": np.nan if mase_scale is None else mase_scale,
            }
        )
    return rows


def _observed_input_rows(
    series: DemandSeries, period: str, origin: date, outcome: OriginForecast, horizons: int
) -> list[dict[str, Any]]:
    """Diagnostic only: the fitted model applied to *observed* inputs after the origin.

    Uses actual demand after the origin by construction, so it runs only after the
    model's forecast exists, goes to a separate table and is never used to compare or
    choose models. At horizon 1 it must equal the raw recursive prediction (clipped).
    """
    fitted = outcome.fitted
    if not isinstance(fitted, FittedRecursiveLinear) or outcome.path is None:
        return []
    last = min(origin + timedelta(days=horizons), series.end)
    if last <= origin:
        return []
    rows_frame = training_features(
        series,
        origin=last,
        horizon=1,
        feature_set=fitted.feature_set,
        first_target=origin + timedelta(days=1),
        allow_incomplete=True,
    ).frame
    complete = rows_frame.notna().all(axis=1)
    values = np.full(len(rows_frame), np.nan)
    if complete.any():
        values[complete.to_numpy()] = fitted.predict_rows(rows_frame.loc[complete])
    raw = outcome.path.raw_predictions
    if raw is not None and complete.iloc[0]:
        # Same feature row and coefficients; a one-row and a many-row matrix product may
        # round differently in the last bits, so equality is checked to 1e-9 relative.
        recursive_first = max(float(raw.iloc[0]), 0.0)
        if abs(values[0] - recursive_first) > 1e-9 * max(1.0, abs(recursive_first)):
            raise AssertionError(
                f"observed-input diagnostic disagrees with the recursive forecast at h=1 for "
                f"{outcome.model_id} at {origin}: {values[0]} != {recursive_first}"
            )
    return [
        {
            "record_type": RECORD_TYPE_OBSERVED_INPUTS,
            "period": period,
            "model_id": outcome.model_id,
            "origin": pd.Timestamp(origin),
            "target_date": target,
            "horizon": (target.date() - origin).days,
            "prediction_observed_inputs": value,
            "actual": np.nan,
        }
        for target, value in zip(pd.DatetimeIndex(rows_frame.index), values, strict=True)
    ]


def _attach_actuals(frame: pd.DataFrame, series: DemandSeries, *, statuses: bool) -> None:
    """Look up each target's actual demand, after every forecast exists (in place)."""
    actuals = series.to_series()
    targets = pd.DatetimeIndex(frame["target_date"])
    frame["actual"] = actuals.reindex(targets).to_numpy(dtype="float64")
    if not statuses:
        return
    predicted = frame["status"] == RecordStatus.OK.value
    beyond = targets > pd.Timestamp(series.end)
    frame.loc[predicted & beyond, "status"] = RecordStatus.BEYOND_SNAPSHOT.value
    missing = predicted & ~beyond & frame["actual"].isna()
    frame.loc[missing, "status"] = RecordStatus.MISSING_ACTUAL.value


# --- Results ------------------------------------------------------------------------


@dataclass(frozen=True)
class BacktestResult:
    plan: BacktestPlan
    models: tuple[ModelSpec, ...]
    records: pd.DataFrame
    fits: pd.DataFrame
    coefficients: pd.DataFrame
    observed_inputs: pd.DataFrame

    def check_complete(self) -> None:
        """Exactly one record per scheduled (period, model, origin, horizon)."""
        expected = self.plan.scheduled_records(len(self.models))
        keys = self.records[["period", "model_id", "origin", "horizon"]]
        if len(self.records) != expected or keys.duplicated().any():
            raise AssertionError(
                f"backtest produced {len(self.records)} records for {expected} scheduled"
            )

    def status_counts(self) -> dict[str, dict[str, dict[str, int]]]:
        """``{period: {model_id: {status: count}}}``."""
        result: dict[str, dict[str, dict[str, int]]] = {}
        for _, group in self.records.groupby(["period", "model_id", "status"], sort=True):
            period = str(group["period"].iloc[0])
            model_id = str(group["model_id"].iloc[0])
            status = str(group["status"].iloc[0])
            result.setdefault(period, {}).setdefault(model_id, {})[status] = len(group)
        return result


def _sorted_frame(
    rows: list[dict[str, Any]], columns: Sequence[str], keys: list[str], order: dict[str, int]
) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(rows, columns=list(columns))
    if frame.empty:
        return frame
    frame["_order"] = frame["model_id"].map(order)
    sort_keys = [key if key != "model_id" else "_order" for key in keys]
    frame.sort_values(sort_keys, inplace=True, kind="stable")
    frame.drop(columns="_order", inplace=True)
    frame.reset_index(drop=True, inplace=True)
    return frame


def run_backtest(
    series: DemandSeries, plan: BacktestPlan, models: Sequence[Forecaster]
) -> BacktestResult:
    """Evaluate every model at every origin of ``plan`` (see the module docstring)."""
    if not models:
        raise ConfigError("at least one model is required")
    specs = tuple(model.spec for model in models)
    ids = [spec.model_id for spec in specs]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"model ids must be unique: {ids}")
    plan.check_against(series)
    delay = plan.data_delay_days
    steps = plan.max_horizon + delay
    memberships = plan.memberships()
    records: list[dict[str, Any]] = []
    fits: list[dict[str, Any]] = []
    coefficients: list[dict[str, Any]] = []
    observed: list[dict[str, Any]] = []
    for origin in plan.origins():
        cutoff = origin - timedelta(days=delay)
        history = plan.training_window.history(series, cutoff)
        # MASE scale from the information set at the cutoff, common to every model.
        scale = seasonal_naive_scale(series.known_through(cutoff))
        for model in models:
            outcome = forecast_at_origin(model, history, steps)
            for period in memberships[origin]:
                fits.append(_fit_row(period, origin, outcome))
                records.extend(
                    _record_rows(
                        RECORD_TYPE_BACKTEST,
                        period,
                        origin,
                        outcome,
                        plan.max_horizon,
                        delay,
                        scale,
                    )
                )
                if plan.observed_input_diagnostic:
                    observed.extend(
                        _observed_input_rows(series, period, origin, outcome, plan.max_horizon)
                    )
            coefficients.extend(_coefficient_rows(origin, outcome))
    order = {model_id: position for position, model_id in enumerate(ids)}
    record_frame = _sorted_frame(
        records, RECORD_COLUMNS, ["period", "origin", "model_id", "horizon"], order
    )
    _attach_actuals(record_frame, series, statuses=True)
    observed_frame = _sorted_frame(
        observed, DIAGNOSTIC_COLUMNS, ["period", "origin", "model_id", "horizon"], order
    )
    if not observed_frame.empty:
        _attach_actuals(observed_frame, series, statuses=False)
    result = BacktestResult(
        plan,
        specs,
        record_frame,
        _sorted_frame(fits, FIT_COLUMNS, ["period", "origin", "model_id"], order),
        _sorted_frame(coefficients, COEFFICIENT_COLUMNS, ["origin", "model_id"], order),
        observed_frame,
    )
    result.check_complete()
    return result


def _coefficient_rows(origin: date, outcome: OriginForecast) -> list[dict[str, Any]]:
    """Fitted coefficients of identifiable regressions (none for the legacy design)."""
    if not isinstance(outcome.fitted, FittedRecursiveLinear):
        return []
    table = outcome.fitted.coefficients()
    if table is None:
        return []
    return [
        {
            "model_id": outcome.model_id,
            "origin": pd.Timestamp(origin),
            "term": str(term),
            "coefficient": float(value),
        }
        for term, value in table.items()
    ]


# --- Future projection ----------------------------------------------------------------


@dataclass(frozen=True)
class ProjectionResult:
    origin: date
    max_horizon: int
    models: tuple[ModelSpec, ...]
    records: pd.DataFrame
    fits: pd.DataFrame
    coefficients: pd.DataFrame


def project(
    series: DemandSeries,
    models: Sequence[Forecaster],
    *,
    max_horizon: int,
    training_window: TrainingWindow | None = None,
) -> ProjectionResult:
    """Forecast ``1 .. max_horizon`` days beyond the last observed day of ``series``.

    Uses :func:`forecast_at_origin`, the same path as the backtest. The records carry
    ``record_type = "future_projection"`` and no actual values: these forecasts have no
    observed outcome and are not validated by the backtest.
    """
    check_steps(max_horizon)
    if max_horizon > MAX_HORIZON_LIMIT:
        raise ConfigError(f"max_horizon must be within 1..{MAX_HORIZON_LIMIT}")
    if not models:
        raise ConfigError("at least one model is required")
    ids = [model.spec.model_id for model in models]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"model ids must be unique: {ids}")
    window = training_window or TrainingWindow()
    origin = series.end
    history = window.history(series, origin)
    records: list[dict[str, Any]] = []
    fits: list[dict[str, Any]] = []
    coefficients: list[dict[str, Any]] = []
    for model in models:
        outcome = forecast_at_origin(model, history, max_horizon)
        fits.append(_fit_row("projection", origin, outcome))
        records.extend(
            _record_rows(
                RECORD_TYPE_PROJECTION, "projection", origin, outcome, max_horizon, 0, None
            )
        )
        coefficients.extend(_coefficient_rows(origin, outcome))
    order = {spec.model_id: i for i, spec in enumerate(m.spec for m in models)}
    record_frame = _sorted_frame(records, RECORD_COLUMNS, ["model_id", "horizon"], order)
    record_frame = record_frame.drop(columns=["actual", "mase_scale", "period"])
    record_frame["status"] = record_frame["status"].replace(RecordStatus.OK.value, "projected")
    fit_frame = _sorted_frame(fits, FIT_COLUMNS, ["model_id"], order).drop(columns="period")
    return ProjectionResult(
        origin,
        max_horizon,
        tuple(m.spec for m in models),
        record_frame,
        fit_frame,
        _sorted_frame(coefficients, COEFFICIENT_COLUMNS, ["model_id"], order),
    )
