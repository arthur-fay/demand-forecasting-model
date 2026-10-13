"""Historical probabilistic backtesting: intervals, exceedance, selection and status (M3).

Data flow (each step only sees what the previous one hands it):

1. The M2 engine (:func:`atlas.backtest.run_backtest`) re-forecasts every origin of the
   residual span with the point models; its records are the only source of residuals and
   point predictions.
2. :func:`issue_forecasts` walks the origins of each M3 period in order. At origin ``o``,
   for each point model and each method/scheme pair, it asks the
   :class:`~atlas.calibration.ResidualStore` for the eligible residuals (targets
   ``<= c - latency`` with ``c = o`` for a rolling scheme and ``c`` = the period's first
   origin for a fixed one), builds the predictive distribution and records its quantiles
   and exceedance probabilities. Thresholds and the climatology baseline read the series
   only through the forecast's data cutoff.
3. Only then are the actual values attached, by target date (:func:`attach_outcomes`).
4. :func:`select_interval_method` ranks the candidates on the *selection* period alone;
   the evaluation period is scored with the sealed choice and never selects. Every
   candidate is still scored on the evaluation period, as a description.
5. :func:`decision_status` applies the pre-registered criteria to the evaluation period
   and withholds decision-grade status when coverage or the evidence is inadequate.

Model selection (M2), interval selection (M3 selection period) and interval evaluation
(M3 evaluation period) use different data, in that order.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final

import numpy as np
import pandas as pd

from atlas.calibration import CalibrationPolicy, CalibrationSet, ResidualStore, Support
from atlas.errors import ConfigError
from atlas.evaluation import holiday_related_dates
from atlas.exceedance import (
    HISTORICAL_NOTICE,
    HYPOTHETICAL_NOTICE,
    PROVENANCE_HYPOTHETICAL,
    ThresholdValue,
    check_ordering,
    provenance,
    threshold_values,
    trailing_window,
)
from atlas.intervals import PredictiveDistribution, climatology, model_distribution
from atlas.metrics import HorizonBucket, per_origin_mean, scopes
from atlas.scoring import (
    brier_score,
    coverage,
    interval_score,
    mean_or_none,
    origin_test,
    pinball_loss,
    reliability_table,
    weighted_interval_score,
)
from atlas.series import DemandSeries
from atlas.stats import bandwidth_sensitivity
from atlas.uncertainty_config import (
    ClimatologyConfig,
    GaussianOneStepConfig,
    HypotheticalThresholdConfig,
    PairConfig,
    UncertaintyConfig,
    UncertaintyPeriodConfig,
    central_bounds,
    level_index,
)

RECORD_TYPE_PROBABILISTIC: Final = "historical_probabilistic_backtest"
RECORD_TYPE_PROJECTION_INTERVALS: Final = "future_projection_intervals"
MODEL_FREE: Final = "model_free"
STATUS_OK: Final = "ok"
STATUS_UNAVAILABLE: Final = "unavailable"
STATUS_POINT_FAILED: Final = "point_failed"
STATUS_MISSING_ACTUAL: Final = "missing_actual"
ISSUED: Final = (STATUS_OK, STATUS_MISSING_ACTUAL)

DECISION_GRADE: Final = "decision_grade"
FAILED_CALIBRATION: Final = "failed_calibration"
INSUFFICIENT_EVIDENCE: Final = "insufficient_evidence"
UNAVAILABLE: Final = "unavailable"


def quantile_column(tau: float) -> str:
    return f"q_{tau:g}"


def level_key(level: float) -> str:
    return f"{level * 100:g}"


def interval_columns(grid: Sequence[float]) -> tuple[str, ...]:
    return (
        "record_type",
        "period",
        "model_id",
        "pair_id",
        "method_id",
        "scheme_id",
        "role",
        "origin",
        "calibration_origin",
        "data_cutoff",
        "target_date",
        "horizon",
        "prediction",
        "actual",
        "status",
        "support",
        "pooling",
        "pooled_bucket",
        "cal_n",
        "cal_n_origins",
        "cal_n_effective",
        "cal_cutoff",
        "cal_first_target",
        "cal_last_target",
        *(quantile_column(t) for t in grid),
    )


EXCEEDANCE_COLUMNS: Final = (
    "record_type",
    "period",
    "model_id",
    "pair_id",
    "origin",
    "target_date",
    "horizon",
    "threshold_label",
    "threshold_kind",
    "threshold_provenance",
    "threshold_value",
    "probability",
    "actual",
    "event",
    "status",
)


@dataclass(frozen=True)
class EngineSettings:
    """What the engine takes from the M2 protocol and the M3 configuration."""

    max_horizon: int
    data_delay_days: int
    buckets: tuple[HorizonBucket, ...]
    policy: CalibrationPolicy
    quantile_levels: tuple[float, ...]

    @classmethod
    def from_config(
        cls,
        config: UncertaintyConfig,
        buckets: Sequence[HorizonBucket],
        data_delay_days: int,
        *,
        latency_days: int | None = None,
    ) -> EngineSettings:
        latency = config.calibration.data_latency_days if latency_days is None else latency_days
        return cls(
            max_horizon=config.max_horizon,
            data_delay_days=data_delay_days,
            buckets=tuple(buckets),
            policy=CalibrationPolicy(
                data_latency_days=latency,
                min_calibration_origins=config.calibration.min_calibration_origins,
                min_effective_calibration=config.calibration.min_effective_calibration,
                pool_buckets=config.calibration.pooling == "horizon_then_bucket",
            ),
            quantile_levels=config.quantile_levels,
        )


# --- Issuing forecasts -------------------------------------------------------------------


@dataclass(frozen=True)
class PointTable:
    """Point predictions of one model: ``matrix[i, h - 1]`` for origin ``origins[i]``."""

    index: Mapping[int, int]
    matrix: np.ndarray

    @classmethod
    def from_records(cls, records: pd.DataFrame, model_id: str, max_horizon: int) -> PointTable:
        rows = records[records["model_id"] == model_id]
        stamps = pd.DatetimeIndex(rows["origin"])
        ordinals = np.fromiter((d.toordinal() for d in stamps.date), "int64", len(stamps))
        unique = np.unique(ordinals)
        index = {int(o): i for i, o in enumerate(unique)}
        matrix = np.full((unique.size, max_horizon), np.nan)
        positions = np.searchsorted(unique, ordinals)
        horizons = rows["horizon"].to_numpy(dtype="int64")
        predictions = rows["prediction"].to_numpy(dtype="float64")
        failed = (rows["status"] == "failed").to_numpy()
        matrix[positions, horizons - 1] = np.where(failed, np.nan, predictions)
        return cls(index, matrix)

    def row(self, origin: date) -> np.ndarray:
        position = self.index.get(origin.toordinal())
        if position is None:
            raise ConfigError(f"the backtest has no forecasts at origin {origin}")
        row: np.ndarray = self.matrix[position]
        return row


@dataclass
class _Rows:
    intervals: list[dict[str, Any]]
    exceedance: list[dict[str, Any]]


def _calibration_fields(calibration: CalibrationSet | None) -> dict[str, Any]:
    if calibration is None:
        return {
            "support": None,
            "pooling": None,
            "pooled_bucket": None,
            "cal_n": None,
            "cal_n_origins": None,
            "cal_n_effective": np.nan,
            "cal_cutoff": pd.NaT,
            "cal_first_target": pd.NaT,
            "cal_last_target": pd.NaT,
        }
    first = calibration.first_used_target
    last = calibration.last_target
    return {
        "support": calibration.support.value,
        "pooling": calibration.pooling.value,
        "pooled_bucket": calibration.pooled_label,
        "cal_n": calibration.n,
        "cal_n_origins": calibration.n_origins,
        "cal_n_effective": (
            np.nan if calibration.n_effective is None else float(calibration.n_effective)
        ),
        "cal_cutoff": pd.Timestamp(calibration.cutoff),
        "cal_first_target": pd.NaT if first is None else pd.Timestamp(first),
        "cal_last_target": pd.NaT if last is None else pd.Timestamp(last),
    }


def _emit(
    rows: _Rows,
    *,
    record_type: str,
    period: str,
    model_id: str,
    pair: PairConfig,
    role: str,
    origin: date,
    data_cutoff: date,
    horizon: int,
    prediction: float,
    distribution: PredictiveDistribution | None,
    calibration: CalibrationSet | None,
    calibration_origin: date | None,
    grid: np.ndarray,
    thresholds: Sequence[ThresholdValue] | None,
    point_failed: bool,
    fields: Mapping[str, Any] | None = None,
) -> None:
    quantiles = np.full(grid.size, np.nan)
    status = STATUS_POINT_FAILED if point_failed else STATUS_UNAVAILABLE
    if distribution is not None:
        quantiles = distribution.quantiles(grid)
        if not np.isnan(quantiles).any():
            status = STATUS_OK
    target = origin + timedelta(days=horizon)
    calibration_fields = dict(_calibration_fields(calibration) if fields is None else fields)
    if status == STATUS_UNAVAILABLE and calibration is not None:
        calibration_fields["support"] = Support.UNAVAILABLE.value
    row: dict[str, Any] = {
        "record_type": record_type,
        "period": period,
        "model_id": model_id,
        "pair_id": pair.pair_id,
        "method_id": pair.method,
        "scheme_id": pair.scheme,
        "role": role,
        "origin": pd.Timestamp(origin),
        "calibration_origin": (
            pd.NaT if calibration_origin is None else pd.Timestamp(calibration_origin)
        ),
        "data_cutoff": pd.Timestamp(data_cutoff),
        "target_date": pd.Timestamp(target),
        "horizon": horizon,
        "prediction": prediction,
        "actual": np.nan,
        "status": status,
        **calibration_fields,
    }
    for tau, value in zip(grid, quantiles, strict=True):
        row[quantile_column(float(tau))] = float(value) if status == STATUS_OK else np.nan
    rows.intervals.append(row)
    if thresholds is None:
        return
    limits = np.array([np.nan if t.value is None else t.value for t in thresholds])
    probabilities = np.full(limits.size, np.nan)
    if status == STATUS_OK and distribution is not None:
        present = ~np.isnan(limits)
        probabilities[present] = distribution.exceedance(limits[present])
        check_ordering(limits, probabilities)
    for threshold, limit, probability in zip(thresholds, limits, probabilities, strict=True):
        rows.exceedance.append(
            {
                "record_type": record_type,
                "period": period,
                "model_id": model_id,
                "pair_id": pair.pair_id,
                "origin": pd.Timestamp(origin),
                "target_date": pd.Timestamp(target),
                "horizon": horizon,
                "threshold_label": threshold.label,
                "threshold_kind": threshold.kind,
                "threshold_provenance": threshold.provenance,
                "threshold_value": limit,
                "probability": probability,
                "actual": np.nan,
                "event": np.nan,
                "status": STATUS_UNAVAILABLE if np.isnan(limit) else status,
            }
        )


class _Calibrator:
    """Calibration sets per (model, scheme, calibration origin, horizon), cached."""

    def __init__(
        self,
        stores: Mapping[str, ResidualStore],
        config: UncertaintyConfig,
        settings: EngineSettings,
    ) -> None:
        self.stores = stores
        self.config = config
        self.settings = settings
        self._cache: dict[tuple[str, str, date, int], CalibrationSet] = {}
        self._fields: dict[tuple[str, str, date, int], dict[str, Any]] = {}

    def fields(self, calibration: CalibrationSet, scheme_id: str) -> dict[str, Any]:
        """The record columns describing ``calibration`` (computed once per set)."""
        key = (calibration.model_id, scheme_id, calibration.calibration_origin, calibration.horizon)
        cached = self._fields.get(key)
        if cached is None:
            cached = self._fields[key] = _calibration_fields(calibration)
        return cached

    def get(
        self, model_id: str, scheme_id: str, issue: date, calibration_origin: date, horizon: int
    ) -> CalibrationSet:
        key = (model_id, scheme_id, calibration_origin, horizon)
        cached = self._cache.get(key)
        if cached is not None:
            if cached.calibration_origin > issue:  # pragma: no cover - guarded by construction
                raise AssertionError("cached calibration is later than the issue origin")
            return cached
        scheme = self.config.scheme(scheme_id)
        result = self.stores[model_id].calibration_set(
            horizon=horizon,
            issue_origin=issue,
            calibration_origin=calibration_origin,
            window_days=scheme.window_days,
            policy=self.settings.policy,
            buckets=self.settings.buckets,
        )
        self._cache[key] = result
        return result

    def forget_before(self, origin: date) -> None:
        """Drop cached rolling sets of earlier origins (fixed sets stay)."""
        self._cache = {k: v for k, v in self._cache.items() if k[2] >= origin or self._fixed(k[1])}
        self._fields = {k: v for k, v in self._fields.items() if k in self._cache}

    def _fixed(self, scheme_id: str) -> bool:
        return self.config.scheme(scheme_id).kind == "fixed"


def calibration_origin_for(
    config: UncertaintyConfig, scheme_id: str, origin: date, period_start: date
) -> date:
    return period_start if config.scheme(scheme_id).kind == "fixed" else origin


def issue_forecasts(
    records: pd.DataFrame,
    series: DemandSeries,
    config: UncertaintyConfig,
    settings: EngineSettings,
    *,
    periods: Sequence[UncertaintyPeriodConfig],
    pairs: Sequence[PairConfig],
    exceedance_pairs: Sequence[str] = (),
    record_type: str = RECORD_TYPE_PROBABILISTIC,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Issue every pair's predictive distribution at every origin of ``periods``.

    Returns the interval and exceedance frames *without* actual values; call
    :func:`attach_outcomes` afterwards. Exceedance rows are written only for the pair ids
    in ``exceedance_pairs``.
    """
    grid = np.asarray(settings.quantile_levels, dtype="float64")
    stores = ResidualStore.from_records(records, config.point_models, settings.max_horizon)
    points = {
        m: PointTable.from_records(records, m, settings.max_horizon) for m in config.point_models
    }
    calibrator = _Calibrator(stores, config, settings)
    rows = _Rows([], [])
    wanted = set(exceedance_pairs)
    candidate_ids = {p.pair_id for p in config.candidates}
    for period in periods:
        span = (period.last_origin - period.first_origin).days
        for offset in range(span + 1):
            origin = period.first_origin + timedelta(days=offset)
            calibrator.forget_before(origin)
            data_cutoff = origin - timedelta(days=settings.data_delay_days)
            thresholds = threshold_values(config.thresholds, series, data_cutoff)
            for pair in pairs:
                method = config.method(pair.method)
                role = "candidate" if pair.pair_id in candidate_ids else "baseline"
                with_thresholds = thresholds if pair.pair_id in wanted else None
                if isinstance(method, ClimatologyConfig):
                    window = trailing_window(series, data_cutoff, method.window_days)
                    sample = climatology(window, method.min_observations)
                    centre = float(np.median(window)) if sample is not None else float("nan")
                    for horizon in range(1, settings.max_horizon + 1):
                        _emit(
                            rows,
                            record_type=record_type,
                            period=period.name,
                            model_id=MODEL_FREE,
                            pair=pair,
                            role=role,
                            origin=origin,
                            data_cutoff=data_cutoff,
                            horizon=horizon,
                            prediction=centre,
                            distribution=sample,
                            calibration=None,
                            calibration_origin=None,
                            grid=grid,
                            thresholds=with_thresholds,
                            point_failed=False,
                        )
                    continue
                if pair.scheme is None:  # pragma: no cover - rejected by the configuration
                    raise ConfigError(f"pair {pair.pair_id} needs a calibration scheme")
                anchor = calibration_origin_for(config, pair.scheme, origin, period.first_origin)
                for model_id in config.point_models:
                    predictions = points[model_id].row(origin)
                    for horizon in range(1, settings.max_horizon + 1):
                        prediction = float(predictions[horizon - 1])
                        failed = math.isnan(prediction)
                        used = 1 if isinstance(method, GaussianOneStepConfig) else horizon
                        calibration = calibrator.get(model_id, pair.scheme, origin, anchor, used)
                        distribution: PredictiveDistribution | None = (
                            None if failed else model_distribution(method, prediction, calibration)
                        )
                        _emit(
                            rows,
                            record_type=record_type,
                            period=period.name,
                            model_id=model_id,
                            pair=pair,
                            role=role,
                            origin=origin,
                            data_cutoff=data_cutoff,
                            horizon=horizon,
                            prediction=prediction,
                            distribution=distribution,
                            calibration=calibration,
                            calibration_origin=anchor,
                            grid=grid,
                            thresholds=with_thresholds,
                            point_failed=failed,
                            fields=calibrator.fields(calibration, pair.scheme),
                        )
    order = {
        "period": {p.name: i for i, p in enumerate(periods)},
        "model_id": {m: i for i, m in enumerate((*config.point_models, MODEL_FREE))},
        "pair_id": {p.pair_id: i for i, p in enumerate(pairs)},
    }
    intervals = _sorted(rows.intervals, interval_columns(settings.quantile_levels), order)
    # Stable sort: the thresholds of one forecast keep their configured order.
    exceedance = _sorted(rows.exceedance, EXCEEDANCE_COLUMNS, order)
    return intervals, exceedance


def _sorted(
    rows: list[dict[str, Any]],
    columns: Sequence[str],
    order: Mapping[str, Mapping[str, int]],
) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(rows, columns=list(columns))
    if frame.empty:
        return frame
    keys = []
    for name, mapping in order.items():
        frame[f"_{name}"] = frame[name].map(mapping)
        keys.append(f"_{name}")
    keys += ["origin", "horizon"]
    frame.sort_values(keys, inplace=True, kind="stable")
    frame.drop(columns=[k for k in keys if k.startswith("_")], inplace=True)
    frame.reset_index(drop=True, inplace=True)
    return frame


def attach_outcomes(
    intervals: pd.DataFrame, exceedance: pd.DataFrame, series: DemandSeries
) -> None:
    """Attach actual demand by target date, after every forecast exists (in place)."""
    actuals = series.to_series()
    for frame in (intervals, exceedance):
        if frame.empty:
            continue
        targets = pd.DatetimeIndex(frame["target_date"])
        frame["actual"] = actuals.reindex(targets).to_numpy(dtype="float64")
        missing = (frame["status"] == STATUS_OK) & frame["actual"].isna()
        frame.loc[missing, "status"] = STATUS_MISSING_ACTUAL
    if not exceedance.empty:
        known = (exceedance["status"] == STATUS_OK) & exceedance["threshold_value"].notna()
        exceedance["event"] = np.where(
            known,
            (exceedance["actual"] > exceedance["threshold_value"]).astype("float64"),
            np.nan,
        )


# --- Scores per forecast -----------------------------------------------------------------


def _evaluated(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[frame["status"] == STATUS_OK]


def _bounds(frame: pd.DataFrame, level: float) -> tuple[pd.Series, pd.Series]:
    low, high = central_bounds(level)
    return frame[quantile_column_for(frame, low)], frame[quantile_column_for(frame, high)]


def quantile_column_for(frame: pd.DataFrame, tau: float) -> str:
    """The quantile column of ``frame`` for level ``tau`` (tolerant to float spelling)."""
    for column in frame.columns:
        if column.startswith("q_") and abs(float(column[2:]) - tau) <= 1e-9:
            return str(column)
    raise ConfigError(f"no quantile column for level {tau}")


def score_frame(evaluated: pd.DataFrame, config: UncertaintyConfig) -> pd.DataFrame:
    """Per-forecast scores of evaluated intervals (coverage, width, IS per level; WIS;
    pinball per quantile; ``y <= q`` indicators)."""
    y = evaluated["actual"].to_numpy(dtype="float64")
    scores: dict[str, Any] = {}
    lowers, uppers, alphas = [], [], []
    for level in config.levels:
        low, high = _bounds(evaluated, level)
        key = level_key(level)
        scores[f"covered_{key}"] = coverage(y, low, high)
        scores[f"width_{key}"] = (high - low).to_numpy(dtype="float64")
        scores[f"interval_score_{key}"] = interval_score(y, low, high, 1.0 - level)
        lowers.append(low)
        uppers.append(high)
        alphas.append(1.0 - level)
    median = evaluated[quantile_column_for(evaluated, 0.5)]
    scores["wis"] = weighted_interval_score(y, median, lowers, uppers, alphas)
    pinball = []
    for tau in config.quantile_levels:
        q = evaluated[quantile_column_for(evaluated, tau)].to_numpy(dtype="float64")
        loss = pinball_loss(y, q, tau)
        scores[f"pinball_{tau:g}"] = loss
        scores[f"below_{tau:g}"] = (y <= q).astype("float64")
        pinball.append(loss)
    scores["mean_pinball"] = np.mean(pinball, axis=0)
    scores["abs_error"] = np.abs(evaluated["prediction"].to_numpy(dtype="float64") - y)
    scores["error"] = evaluated["prediction"].to_numpy(dtype="float64") - y
    return pd.DataFrame(scores, index=evaluated.index)


# --- Summaries ---------------------------------------------------------------------------


def _scope_list(
    config: UncertaintyConfig, buckets: Sequence[HorizonBucket], horizons: bool
) -> list[tuple[str, str, int, int]]:
    return scopes(buckets, config.max_horizon, horizons=horizons)


def interval_summary(
    intervals: pd.DataFrame,
    config: UncertaintyConfig,
    buckets: Sequence[HorizonBucket],
    *,
    horizons: bool = True,
) -> list[dict[str, Any]]:
    """Coverage (with HAC interval and test against the level), width, interval score,
    WIS, pinball loss and quantile calibration per period, model, pair and scope."""
    rows: list[dict[str, Any]] = []
    if intervals.empty:
        return rows
    for _, group in intervals.groupby(["period", "model_id", "pair_id"], sort=False):
        evaluated = _evaluated(group)
        scored = score_frame(evaluated, config) if len(evaluated) else pd.DataFrame()
        for scope, label, first, last in _scope_list(config, buckets, horizons):
            in_scope = group[group["horizon"].between(first, last)]
            cell = evaluated[evaluated["horizon"].between(first, last)]
            cell_scores = scored.loc[cell.index] if len(cell) else pd.DataFrame()
            issued = in_scope["status"].isin(ISSUED)
            row: dict[str, Any] = {
                "period": str(group["period"].iloc[0]),
                "model_id": str(group["model_id"].iloc[0]),
                "pair_id": str(group["pair_id"].iloc[0]),
                "role": str(group["role"].iloc[0]),
                "scope": scope,
                "label": label,
                "first_horizon": first,
                "last_horizon": last,
                "n_scheduled": len(in_scope),
                "n_issued": int(issued.sum()),
                "n_unavailable": int((in_scope["status"] == STATUS_UNAVAILABLE).sum()),
                "n_point_failed": int((in_scope["status"] == STATUS_POINT_FAILED).sum()),
                "n_missing_actual": int((in_scope["status"] == STATUS_MISSING_ACTUAL).sum()),
                "n_thin": int((issued & (in_scope["support"] == Support.THIN.value)).sum()),
                "n_bucket_pooled": int((issued & (in_scope["pooling"] == "bucket")).sum()),
                "n_evaluated": len(cell),
                "n_origins": int(cell["origin"].nunique()),
                "n_target_days": int(cell["target_date"].nunique()),
            }
            for level in config.levels:
                key = level_key(level)
                if len(cell) == 0:
                    row.update({f"coverage_{key}": None, f"mean_width_{key}": None})
                    continue
                covered = cell_scores[f"covered_{key}"]
                width = cell_scores[f"width_{key}"]
                test = origin_test(cell, covered, first=first, last=last, offset=level)
                row.update(
                    {
                        f"coverage_{key}": float(covered.mean()),
                        f"coverage_ci_low_{key}": _shift(test["ci_low"], level),
                        f"coverage_ci_high_{key}": _shift(test["ci_high"], level),
                        f"coverage_p_{key}": test["p_value_conservative"],
                        f"coverage_test_computed_{key}": test["test_computed"],
                        f"coverage_test_reason_{key}": test["test_reason"],
                        f"coverage_eff_n_{key}": test["effective_sample_size"],
                        f"mean_width_{key}": float(width.mean()),
                        f"median_width_{key}": float(width.median()),
                        f"mean_relative_width_{key}": _relative(width, cell["prediction"]),
                        f"interval_score_{key}": float(cell_scores[f"interval_score_{key}"].mean()),
                    }
                )
            if len(cell):
                row["wis"] = float(cell_scores["wis"].mean())
                row["mean_pinball"] = float(cell_scores["mean_pinball"].mean())
                row["mae"] = float(cell_scores["abs_error"].mean())
                row["bias"] = float(cell_scores["error"].mean())
                row["pinball"] = {
                    f"{t:g}": float(cell_scores[f"pinball_{t:g}"].mean())
                    for t in config.quantile_levels
                }
                row["quantile_calibration"] = {
                    f"{t:g}": float(cell_scores[f"below_{t:g}"].mean())
                    for t in config.quantile_levels
                }
            else:
                row.update({"wis": None, "mean_pinball": None, "mae": None, "bias": None})
            rows.append(row)
    return rows


def _shift(value: float | None, level: float) -> float | None:
    return None if value is None else value + level


def _relative(width: pd.Series, prediction: pd.Series) -> float | None:
    positive = prediction.to_numpy(dtype="float64") > 0
    if not positive.any():
        return None
    ratio = (
        width.to_numpy(dtype="float64")[positive] / prediction.to_numpy(dtype="float64")[positive]
    )
    return float(ratio.mean())


def calibration_support(intervals: pd.DataFrame) -> list[dict[str, Any]]:
    """How each pair was calibrated: pooling, thin and unavailable counts, sample sizes."""
    rows: list[dict[str, Any]] = []
    residual = intervals[intervals["model_id"] != MODEL_FREE]
    for _, group in residual.groupby(["period", "model_id", "pair_id"], sort=False):
        n = group["cal_n"].dropna().to_numpy(dtype="float64")
        n_eff = group["cal_n_effective"].dropna().to_numpy(dtype="float64")
        rows.append(
            {
                "period": str(group["period"].iloc[0]),
                "model_id": str(group["model_id"].iloc[0]),
                "pair_id": str(group["pair_id"].iloc[0]),
                "n_scheduled": len(group),
                "n_ok_or_missing_actual": int(group["status"].isin(ISSUED).sum()),
                "n_unavailable": int((group["status"] == STATUS_UNAVAILABLE).sum()),
                "n_point_failed": int((group["status"] == STATUS_POINT_FAILED).sum()),
                "n_horizon_specific": int((group["pooling"] == "horizon").sum()),
                "n_bucket_pooled": int((group["pooling"] == "bucket").sum()),
                "n_supported": int((group["support"] == Support.SUPPORTED.value).sum()),
                "n_thin": int((group["support"] == Support.THIN.value).sum()),
                "cal_n_min": float(n.min()) if n.size else None,
                "cal_n_median": float(np.median(n)) if n.size else None,
                "cal_n_max": float(n.max()) if n.size else None,
                "cal_n_effective_min": float(n_eff.min()) if n_eff.size else None,
                "cal_n_effective_median": float(np.median(n_eff)) if n_eff.size else None,
                "first_calibration_origin": group["calibration_origin"].min(),
                "last_calibration_origin": group["calibration_origin"].max(),
                "first_cal_target": group["cal_first_target"].min(),
                "last_cal_target": group["cal_last_target"].max(),
            }
        )
    return rows


def _paired(
    left: pd.DataFrame, right: pd.DataFrame, config: UncertaintyConfig
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame] | None:
    keys = ["origin", "horizon"]
    a, b = _evaluated(left), _evaluated(right)
    common = a[keys].merge(b[keys], on=keys)
    if common.empty:
        return None
    a = a.merge(common, on=keys).sort_values(keys, kind="stable").reset_index(drop=True)
    b = b.merge(common, on=keys).sort_values(keys, kind="stable").reset_index(drop=True)
    return a, score_frame(a, config), score_frame(b, config)


def comparisons(
    intervals: pd.DataFrame,
    config: UncertaintyConfig,
    buckets: Sequence[HorizonBucket],
    pairs: Sequence[tuple[str, str, str, str]],
) -> list[dict[str, Any]]:
    """Paired interval comparisons ``(model, pair) vs (reference model, reference pair)``
    on common evaluated forecasts: WIS and interval-score differences with HAC tests."""
    rows: list[dict[str, Any]] = []
    for period in intervals["period"].unique():
        in_period = intervals[intervals["period"] == period]
        for model_id, pair_id, ref_model, ref_pair in pairs:
            left = in_period[
                (in_period["model_id"] == model_id) & (in_period["pair_id"] == pair_id)
            ]
            right = in_period[
                (in_period["model_id"] == ref_model) & (in_period["pair_id"] == ref_pair)
            ]
            if left.empty or right.empty:
                continue
            paired = _paired(left, right, config)
            if paired is None:
                continue
            cells, mine, theirs = paired
            for _, label, first, last in _scope_list(config, buckets, horizons=False):
                chosen = cells["horizon"].between(first, last)
                cell = cells[chosen]
                if cell.empty:
                    continue
                row: dict[str, Any] = {
                    "period": str(period),
                    "model_id": model_id,
                    "pair_id": pair_id,
                    "reference_model": ref_model,
                    "reference_pair": ref_pair,
                    "label": label,
                    "first_horizon": first,
                    "last_horizon": last,
                    "n_common": len(cell),
                }
                for metric in ("wis", *(f"interval_score_{level_key(lv)}" for lv in config.levels)):
                    ours = mine.loc[chosen, metric].to_numpy()
                    ref = theirs.loc[chosen, metric].to_numpy()
                    test = origin_test(
                        cell, pd.Series(ours - ref, index=cell.index), first=first, last=last
                    )
                    row[metric] = float(ours.mean())
                    row[f"{metric}_reference"] = float(ref.mean())
                    row[f"{metric}_skill_pct"] = (
                        100.0 * (1.0 - ours.mean() / ref.mean()) if ref.mean() > 0 else None
                    )
                    row[f"{metric}_difference_ci_low"] = test["ci_low"]
                    row[f"{metric}_difference_ci_high"] = test["ci_high"]
                    row[f"{metric}_p_conservative"] = test["p_value_conservative"]
                    row[f"{metric}_eff_n"] = test["effective_sample_size"]
                rows.append(row)
    return rows


def exceedance_summary(
    exceedance: pd.DataFrame,
    config: UncertaintyConfig,
    buckets: Sequence[HorizonBucket],
    reference_pair: str,
) -> list[dict[str, Any]]:
    """Brier score, skill against the climatology reference (paired, HAC test), mean
    probability against observed frequency, and a reliability table."""
    rows: list[dict[str, Any]] = []
    if exceedance.empty:
        return rows
    evaluated = exceedance[exceedance["event"].notna() & exceedance["probability"].notna()]
    keys = ["period", "origin", "horizon", "threshold_label"]
    reference = evaluated[evaluated["pair_id"] == reference_pair][[*keys, "probability"]]
    reference = reference.rename(columns={"probability": "probability_reference"})
    labels = {t.label: t for t in config.thresholds}
    for _, group in evaluated.groupby(
        ["period", "model_id", "pair_id", "threshold_label"], sort=False
    ):
        merged = group.merge(reference, on=keys, how="left")
        threshold = labels[str(group["threshold_label"].iloc[0])]
        for _, label, first, last in _scope_list(config, buckets, horizons=False):
            cell = merged[merged["horizon"].between(first, last)].reset_index(drop=True)
            if cell.empty:
                continue
            p = cell["probability"].to_numpy(dtype="float64")
            e = cell["event"].to_numpy(dtype="float64")
            brier = brier_score(p, e)
            row: dict[str, Any] = {
                "period": str(group["period"].iloc[0]),
                "model_id": str(group["model_id"].iloc[0]),
                "pair_id": str(group["pair_id"].iloc[0]),
                "threshold_label": threshold.label,
                "threshold_kind": threshold.kind,
                "threshold_provenance": str(group["threshold_provenance"].iloc[0]),
                "label": label,
                "first_horizon": first,
                "last_horizon": last,
                "n_evaluated": len(cell),
                "n_origins": int(cell["origin"].nunique()),
                "n_events": int(e.sum()),
                "mean_threshold": float(cell["threshold_value"].mean()),
                "mean_probability": float(p.mean()),
                "event_frequency": float(e.mean()),
                "calibration_gap": float(p.mean() - e.mean()),
                "brier": float(brier.mean()),
            }
            with_reference = cell["probability_reference"].notna().to_numpy()
            is_reference = str(group["pair_id"].iloc[0]) == reference_pair
            if with_reference.all() and not is_reference:
                ref_brier = brier_score(cell["probability_reference"].to_numpy(), e)
                test = origin_test(cell, pd.Series(brier - ref_brier), first=first, last=last)
                row.update(
                    {
                        "brier_reference": float(ref_brier.mean()),
                        "brier_skill_score": (
                            1.0 - brier.mean() / ref_brier.mean() if ref_brier.mean() > 0 else None
                        ),
                        "brier_difference_ci_low": test["ci_low"],
                        "brier_difference_ci_high": test["ci_high"],
                        "brier_p_conservative": test["p_value_conservative"],
                        "brier_eff_n": test["effective_sample_size"],
                    }
                )
            if label == f"days_1_{config.max_horizon}":
                row["reliability"] = reliability_table(p, e)
            rows.append(row)
    return rows


def _stratum_masks(
    frame: pd.DataFrame, holidays: set[pd.Timestamp], flagged: set[pd.Timestamp]
) -> dict[str, pd.Series]:
    targets = pd.DatetimeIndex(frame["target_date"])
    holiday = pd.Series(targets.isin(list(holidays)), index=frame.index)
    flag = pd.Series(targets.isin(list(flagged)), index=frame.index)
    return {"ordinary": ~holiday & ~flag, "holiday_related": holiday, "flagged": flag}


def strata_summary(
    intervals: pd.DataFrame,
    exceedance: pd.DataFrame,
    config: UncertaintyConfig,
    pair_ids: Sequence[str],
    flagged_dates: Sequence[date],
) -> list[dict[str, Any]]:
    """Coverage, width, scores, point error and Brier on ordinary, holiday-related and
    flagged target days (days 1..H; descriptive, no tests: the strata leave gaps)."""
    rows: list[dict[str, Any]] = []
    chosen = _evaluated(intervals[intervals["pair_id"].isin(pair_ids)])
    if chosen.empty:
        return rows
    first = pd.Timestamp(chosen["target_date"].min()).date()
    last = pd.Timestamp(chosen["target_date"].max()).date()
    holidays = holiday_related_dates(first, last)
    flagged = {pd.Timestamp(d) for d in flagged_dates}
    historical = {t.label for t in config.historical_thresholds()}
    scored = score_frame(chosen, config)
    masks = _stratum_masks(chosen, holidays, flagged)
    ex = exceedance[exceedance["event"].notna() & exceedance["threshold_label"].isin(historical)]
    ex_masks = _stratum_masks(ex, holidays, flagged) if not ex.empty else {}
    for (period, model_id, pair_id), group in chosen.groupby(
        ["period", "model_id", "pair_id"], sort=False
    ):
        for stratum, mask in masks.items():
            cell = group[mask.loc[group.index]]
            row: dict[str, Any] = {
                "period": period,
                "model_id": model_id,
                "pair_id": pair_id,
                "stratum": stratum,
                "n_evaluated": len(cell),
                "n_target_days": int(cell["target_date"].nunique()),
                "n_origins": int(cell["origin"].nunique()),
            }
            if len(cell):
                s = scored.loc[cell.index]
                for level in config.levels:
                    key = level_key(level)
                    row[f"coverage_{key}"] = float(s[f"covered_{key}"].mean())
                    row[f"mean_width_{key}"] = float(s[f"width_{key}"].mean())
                    row[f"interval_score_{key}"] = float(s[f"interval_score_{key}"].mean())
                row["wis"] = float(s["wis"].mean())
                row["mae"] = float(s["abs_error"].mean())
                row["bias"] = float(s["error"].mean())
            if ex_masks:
                in_group = (
                    (ex["period"] == period)
                    & (ex["model_id"] == model_id)
                    & (ex["pair_id"] == pair_id)
                    & ex_masks[stratum]
                )
                cell_ex = ex[in_group]
                if len(cell_ex):
                    row["brier_historical"] = mean_or_none(
                        brier_score(cell_ex["probability"], cell_ex["event"])
                    )
                    row["event_frequency_historical"] = float(cell_ex["event"].mean())
                    row["mean_probability_historical"] = float(cell_ex["probability"].mean())
            rows.append(row)
    return rows


def width_profile(
    intervals: pd.DataFrame, config: UncertaintyConfig, pair_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """Mean width by horizon, and how often it falls from one horizon to the next.

    Widths are not forced to grow with the horizon; this reports whether they do.
    """
    rows: list[dict[str, Any]] = []
    issued = intervals[intervals["status"].isin(ISSUED) & intervals["pair_id"].isin(pair_ids)]
    for (period, model_id, pair_id), group in issued.groupby(
        ["period", "model_id", "pair_id"], sort=False
    ):
        for level in config.levels:
            low, high = _bounds(group, level)
            width = (high - low).rename("width")
            by_h = width.groupby(group["horizon"]).mean().sort_index()
            steps = np.diff(by_h.to_numpy())
            per_origin = width.groupby(group["origin"]).apply(
                lambda w: bool(np.all(np.diff(w.to_numpy()) >= -1e-9))
            )
            rows.append(
                {
                    "period": period,
                    "model_id": model_id,
                    "pair_id": pair_id,
                    "level": level,
                    "mean_width_by_horizon": [float(v) for v in by_h.to_numpy()],
                    "n_decreasing_steps": int((steps < 0).sum()),
                    "share_of_origins_nondecreasing": float(per_origin.mean()),
                }
            )
    return rows


# --- Selection and status ----------------------------------------------------------------

SELECTION_RULE: Final = (
    "Among the candidate method/scheme pairs, those with an unavailable-forecast rate above "
    "max_unavailable_rate on the selection period are ineligible; the others are ranked by "
    "mean WIS over the selection horizons on the forecasts every eligible candidate issued, "
    "for the primary point model; ties go to the earlier candidate. The evaluation period "
    "never selects."
)


def select_interval_method(
    intervals: pd.DataFrame, config: UncertaintyConfig
) -> dict[str, Any] | None:
    """The pre-registered choice of one candidate on the selection period."""
    rule = config.selection
    if rule is None:
        return None
    frame = intervals[
        (intervals["period"] == rule.period)
        & (intervals["model_id"] == rule.point_model)
        & intervals["horizon"].between(rule.first_horizon, rule.last_horizon)
    ]
    if frame.empty:
        return None
    eligibility = []
    eligible: list[str] = []
    for pair in config.candidates:
        rows = frame[frame["pair_id"] == pair.pair_id]
        unavailable = int((rows["status"] == STATUS_UNAVAILABLE).sum())
        failed = int((rows["status"] == STATUS_POINT_FAILED).sum())
        rate = (unavailable + failed) / len(rows) if len(rows) else 1.0
        ok = len(rows) > 0 and rate <= rule.max_unavailable_rate
        eligibility.append(
            {
                "pair_id": pair.pair_id,
                "n_scheduled": len(rows),
                "n_unavailable": unavailable,
                "n_point_failed": failed,
                "unavailable_rate": rate,
                "eligible": ok,
            }
        )
        if ok:
            eligible.append(pair.pair_id)
    result: dict[str, Any] = {
        "rule": SELECTION_RULE,
        "period": rule.period,
        "point_model": rule.point_model,
        "criterion": rule.criterion,
        "first_horizon": rule.first_horizon,
        "last_horizon": rule.last_horizon,
        "max_unavailable_rate": rule.max_unavailable_rate,
        "eligibility": eligibility,
        "ranking": [],
        "n_common": 0,
        "selected_pair": None,
        "selected_method": None,
        "selected_scheme": None,
        "reason": "no eligible candidate",
    }
    if not eligible:
        return result
    keys = ["origin", "horizon"]
    evaluated = _evaluated(frame[frame["pair_id"].isin(eligible)])
    counts = evaluated.groupby(keys).size()
    common = counts[counts == len(eligible)].reset_index()[keys]
    ranking = []
    for position, pair_id in enumerate(eligible):
        cell = evaluated[evaluated["pair_id"] == pair_id].merge(common, on=keys)
        scored = score_frame(cell, config)
        entry: dict[str, Any] = {
            "pair_id": pair_id,
            "order": position,
            "wis": float(scored["wis"].mean()) if len(cell) else None,
        }
        for level in config.levels:
            key = level_key(level)
            entry[f"coverage_{key}"] = float(scored[f"covered_{key}"].mean()) if len(cell) else None
            entry[f"mean_width_{key}"] = float(scored[f"width_{key}"].mean()) if len(cell) else None
        ranking.append(entry)
    ranking.sort(key=lambda r: (math.inf if r["wis"] is None else r["wis"], r["order"]))
    best = config.pair(ranking[0]["pair_id"])
    result.update(
        {
            "ranking": ranking,
            "n_common": len(common),
            "selected_pair": best.pair_id,
            "selected_method": best.method,
            "selected_scheme": best.scheme,
            "reason": f"lowest mean WIS on the {rule.period} period ({ranking[0]['wis']:.6g})",
        }
    )
    return result


SELECTION_DECISION_FIELDS: Final = (
    "rule",
    "period",
    "point_model",
    "criterion",
    "first_horizon",
    "last_horizon",
    "max_unavailable_rate",
    "eligibility",
    "n_common",
    "selected_pair",
    "selected_method",
    "selected_scheme",
)


def _close(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


def selection_differences(recomputed: Mapping[str, Any], sealed: Mapping[str, Any]) -> list[str]:
    """Decision fields in which a recomputed selection differs from the sealed one."""
    differing = [
        k for k in SELECTION_DECISION_FIELDS if not _close(recomputed.get(k), sealed.get(k))
    ]
    ranked = [(r.get("pair_id"), r.get("wis")) for r in recomputed.get("ranking", [])]
    sealed_ranked = [(r.get("pair_id"), r.get("wis")) for r in sealed.get("ranking", [])]
    if not _close([list(x) for x in ranked], [list(x) for x in sealed_ranked]):
        differing.append("ranking")
    return differing


def _row(rows: Sequence[Mapping[str, Any]], **match: Any) -> Mapping[str, Any] | None:
    return next((r for r in rows if all(r.get(k) == v for k, v in match.items())), None)


def interval_status(
    summary: Sequence[Mapping[str, Any]],
    config: UncertaintyConfig,
    buckets: Sequence[HorizonBucket],
    *,
    period: str,
    model_id: str,
    pair_id: str,
) -> dict[str, Any]:
    """Apply the pre-registered coverage criteria (see :class:`DecisionCriteriaConfig`)."""
    criteria = config.decision_criteria
    all_label = f"days_1_{config.max_horizon}"
    overall = _row(summary, period=period, model_id=model_id, pair_id=pair_id, label=all_label)
    reasons: list[str] = []
    levels: list[dict[str, Any]] = []
    if overall is None or not overall.get("n_evaluated"):
        return {"status": UNAVAILABLE, "levels": [], "reasons": ["no evaluated interval"]}
    failed = insufficient = False
    if overall["n_unavailable"] or overall["n_point_failed"]:
        insufficient = True
        reasons.append(
            f"{overall['n_unavailable'] + overall['n_point_failed']} scheduled interval(s) "
            "were not issued"
        )
    for level in config.levels:
        key = level_key(level)
        tolerance = config.tolerance_pct(level) / 100.0
        bucket_rows = [
            _row(summary, period=period, model_id=model_id, pair_id=pair_id, label=b.label)
            for b in buckets
        ]
        outside = [
            str(r["label"])
            for r in [overall, *bucket_rows]
            if r is not None
            and (r.get(f"coverage_{key}") is None or abs(r[f"coverage_{key}"] - level) > tolerance)
        ]
        p_value = overall.get(f"coverage_p_{key}")
        computed = bool(overall.get(f"coverage_test_computed_{key}"))
        rejected = computed and p_value is not None and p_value < criteria.alpha
        eff_n = overall.get(f"coverage_eff_n_{key}")
        enough = computed and eff_n is not None and eff_n >= criteria.min_effective_sample_size
        if outside:
            failed = True
            reasons.append(
                f"{key} % coverage outside +/-{config.tolerance_pct(level):g} pp of {key} % in: "
                + ", ".join(outside)
            )
        if rejected:
            failed = True
            reasons.append(f"{key} % coverage = {key} % rejected (conservative p {p_value:.3g})")
        if not computed:
            insufficient = True
            reason = overall.get(f"coverage_test_reason_{key}") or "unknown reason"
            reasons.append(f"{key} % coverage test not computed ({reason})")
        elif not enough:
            insufficient = True
            reasons.append(
                f"{key} % coverage evidence: HAC effective sample size "
                + ("unavailable" if eff_n is None else f"{eff_n:.1f}")
                + f" < {criteria.min_effective_sample_size:g}"
            )
        levels.append(
            {
                "level": level,
                "coverage": overall.get(f"coverage_{key}"),
                "coverage_ci_low": overall.get(f"coverage_ci_low_{key}"),
                "coverage_ci_high": overall.get(f"coverage_ci_high_{key}"),
                "tolerance_pct": config.tolerance_pct(level),
                "scopes_outside_tolerance": outside,
                "p_value_conservative": p_value,
                "test_computed": computed,
                "rejected": rejected,
                "effective_sample_size": eff_n,
                "evidence_sufficient": enough,
            }
        )
    status = (
        FAILED_CALIBRATION if failed else INSUFFICIENT_EVIDENCE if insufficient else DECISION_GRADE
    )
    return {"status": status, "levels": levels, "reasons": reasons}


def exceedance_status(
    rows: Sequence[Mapping[str, Any]],
    config: UncertaintyConfig,
    *,
    period: str,
    model_id: str,
    pair_id: str,
) -> list[dict[str, Any]]:
    criteria = config.decision_criteria
    all_label = f"days_1_{config.max_horizon}"
    result: list[dict[str, Any]] = []
    for threshold in config.thresholds:
        row = _row(
            rows,
            period=period,
            model_id=model_id,
            pair_id=pair_id,
            threshold_label=threshold.label,
            label=all_label,
        )
        reasons: list[str] = []
        if row is None or not row.get("n_evaluated"):
            result.append(
                {
                    "threshold_label": threshold.label,
                    "status": UNAVAILABLE,
                    "reasons": ["no evaluated probability"],
                }
            )
            continue
        failed = insufficient = False
        gap = row["calibration_gap"]
        if abs(gap) > criteria.exceedance_max_calibration_gap:
            failed = True
            reasons.append(
                f"mean probability {row['mean_probability']:.3f} vs observed frequency "
                f"{row['event_frequency']:.3f} "
                f"(gap above {criteria.exceedance_max_calibration_gap:g})"
            )
        skill = row.get("brier_skill_score")
        p_value = row.get("brier_p_conservative")
        if skill is None:
            insufficient = True
            reasons.append("no paired reference Brier score")
        elif skill <= 0:
            failed = True
            reasons.append(f"Brier score not better than the reference (skill {skill:.3f})")
        elif p_value is None or p_value >= criteria.alpha:
            insufficient = True
            reasons.append(
                "Brier improvement over the reference not significant "
                + ("(test not computed)" if p_value is None else f"(conservative p {p_value:.3g})")
            )
        eff_n = row.get("brier_eff_n")
        if eff_n is None or eff_n < criteria.min_effective_sample_size:
            insufficient = True
            reasons.append(
                "HAC effective sample size "
                + ("not computed" if eff_n is None else f"{eff_n:.1f}")
                + f" < {criteria.min_effective_sample_size:g}"
            )
        status = (
            FAILED_CALIBRATION
            if failed
            else INSUFFICIENT_EVIDENCE
            if insufficient
            else DECISION_GRADE
        )
        hypothetical = row["threshold_provenance"] == PROVENANCE_HYPOTHETICAL
        result.append(
            {
                "threshold_label": threshold.label,
                "threshold_provenance": row["threshold_provenance"],
                "notice": HYPOTHETICAL_NOTICE if hypothetical else HISTORICAL_NOTICE,
                "status": status,
                "brier": row["brier"],
                "brier_reference": row.get("brier_reference"),
                "brier_skill_score": skill,
                "p_value_conservative": p_value,
                "mean_probability": row["mean_probability"],
                "event_frequency": row["event_frequency"],
                "calibration_gap": gap,
                "effective_sample_size": eff_n,
                "n_events": row["n_events"],
                "n_evaluated": row["n_evaluated"],
                "reasons": reasons,
            }
        )
    return result


STATUS_STATEMENTS: Final = {
    DECISION_GRADE: (
        "The selected intervals met every pre-registered coverage criterion on the "
        "evaluation period with an adequate effective sample size."
    ),
    FAILED_CALIBRATION: (
        "The selected intervals FAILED the pre-registered calibration criteria on the "
        "evaluation period. Decision-grade status is withheld; the intervals are reported as "
        "they are, not adjusted after the fact."
    ),
    INSUFFICIENT_EVIDENCE: (
        "The selected intervals did not fail the coverage criteria, but the evaluation "
        "history is too short (effective sample size) to support a reliable coverage claim. "
        "Decision-grade status is withheld; the intervals are exploratory."
    ),
    UNAVAILABLE: "No interval could be evaluated.",
}


def decision_status(
    summary: Sequence[Mapping[str, Any]],
    exceedance_rows: Sequence[Mapping[str, Any]],
    config: UncertaintyConfig,
    buckets: Sequence[HorizonBucket],
    selected_pair: str,
) -> dict[str, Any] | None:
    evaluation = config.period_with_role("evaluation")
    if evaluation is None or not any(r["period"] == evaluation.name for r in summary):
        return None
    by_model: dict[str, dict[str, Any]] = {}
    for model_id in config.point_models:
        intervals = interval_status(
            summary,
            config,
            buckets,
            period=evaluation.name,
            model_id=model_id,
            pair_id=selected_pair,
        )
        exceed = exceedance_status(
            exceedance_rows,
            config,
            period=evaluation.name,
            model_id=model_id,
            pair_id=selected_pair,
        )
        by_model[model_id] = {"intervals": intervals, "exceedance": exceed}
    primary = by_model[config.primary_model]
    status = primary["intervals"]["status"]
    return {
        "period": evaluation.name,
        "primary_model": config.primary_model,
        "selected_pair": selected_pair,
        "interval_status": status,
        "decision_grade": status == DECISION_GRADE,
        "statement": STATUS_STATEMENTS[status],
        "by_model": by_model,
        "note": (
            "The evaluation period is M2's confirmation period: already used to confirm the "
            "point model, so this is a historical evaluation, not an untouched prospective "
            "test. Statuses of models other than the primary one are descriptive."
        ),
    }


def coverage_bandwidth_sensitivity(
    intervals: pd.DataFrame,
    config: UncertaintyConfig,
    *,
    period: str,
    model_id: str,
    pair_id: str,
) -> list[dict[str, Any]]:
    """The coverage test of the selected pair under wider HAC bandwidths (descriptive)."""
    cell = _evaluated(
        intervals[
            (intervals["period"] == period)
            & (intervals["model_id"] == model_id)
            & (intervals["pair_id"] == pair_id)
        ]
    )
    rows: list[dict[str, Any]] = []
    if cell.empty:
        return rows
    scored = score_frame(cell, config)
    for level in config.levels:
        key = level_key(level)
        per_origin = per_origin_mean(cell, scored[f"covered_{key}"] - level, config.max_horizon)
        for entry in bandwidth_sensitivity(per_origin.to_numpy(), horizon=config.max_horizon):
            rows.append({"level": level, "period": period, "model_id": model_id, **entry})
    return rows


def level_quantile_columns(config: UncertaintyConfig) -> dict[str, tuple[str, str]]:
    """``{level key: (lower column, upper column)}`` for the configured levels."""
    result = {}
    for level in config.levels:
        low, high = central_bounds(level)
        grid = config.quantile_levels
        result[level_key(level)] = (
            quantile_column(grid[level_index(grid, low)]),
            quantile_column(grid[level_index(grid, high)]),
        )
    return result


# --- One evaluation run ------------------------------------------------------------------

LIMITATIONS: Final = (
    "Historical re-issue, not prospective validation: every interval is re-issued at a past "
    "origin from one frozen snapshot (a single final data vintage) and scored against "
    "recorded actuals.",
    "The evaluation period is M2's confirmation period. Its point errors were examined in M2 "
    "(and the model was confirmed there), so it is not an untouched test; only the interval "
    "and probability results are new.",
    "Forecast paths from neighbouring origins overlap and errors follow month-long regimes: "
    "thousands of scored forecasts are worth only tens of independent observations (see the "
    "HAC effective sample sizes). Coverage intervals and p-values are indicative.",
    "Residual-based intervals assume that the errors of the calibration window describe the "
    "errors to come. Level shifts, growth and unusual periods (year-end) break this; "
    "split-conformal guarantees require exchangeability, which does not hold here.",
    "Historical thresholds are percentiles of incoming demand, and hypothetical thresholds "
    "are user-supplied numbers. Neither is a measured service capacity of any NYC agency.",
)


@dataclass(frozen=True)
class ProbabilisticResult:
    intervals: pd.DataFrame
    exceedance: pd.DataFrame
    payload: dict[str, Any]


def threshold_descriptions(config: UncertaintyConfig) -> list[dict[str, Any]]:
    rows = []
    for threshold in config.thresholds:
        if isinstance(threshold, HypotheticalThresholdConfig):
            rows.append(
                {
                    "label": threshold.label,
                    "kind": threshold.kind,
                    "provenance": PROVENANCE_HYPOTHETICAL,
                    "value": threshold.value,
                    "units": threshold.units,
                    "assumptions": threshold.assumptions,
                    "notice": HYPOTHETICAL_NOTICE,
                }
            )
        else:
            rows.append(
                {
                    "label": threshold.label,
                    "kind": threshold.kind,
                    "provenance": provenance(threshold),
                    "window_days": threshold.window_days,
                    "quantile": threshold.quantile,
                    "min_observations": threshold.min_observations,
                    "units": "requests_per_day",
                    "description": threshold.description,
                    "notice": HISTORICAL_NOTICE,
                }
            )
    return rows


def _comparison_pairs(
    config: UncertaintyConfig, decided: str | None
) -> list[tuple[str, str, str, str]]:
    result: list[tuple[str, str, str, str]] = []
    gaussian = [
        p.pair_id
        for p in config.baselines
        if isinstance(config.method(p.method), GaussianOneStepConfig)
    ]
    climatologies = [
        p.pair_id
        for p in config.baselines
        if isinstance(config.method(p.method), ClimatologyConfig)
    ]
    for model_id in config.point_models:
        for candidate in config.candidates:
            for reference in gaussian:
                result.append((model_id, candidate.pair_id, model_id, reference))
            for reference in climatologies:
                result.append((model_id, candidate.pair_id, MODEL_FREE, reference))
    if decided is not None:
        for model_id in config.point_models:
            if model_id != config.primary_model:
                result.append((config.primary_model, decided, model_id, decided))
    return result


def run_probabilistic(
    records: pd.DataFrame,
    series: DemandSeries,
    config: UncertaintyConfig,
    *,
    buckets: Sequence[HorizonBucket],
    data_delay_days: int,
    periods: Sequence[str],
    sealed_selection: Mapping[str, Any] | None = None,
    flagged_dates: Sequence[date] = (),
) -> ProbabilisticResult:
    """Issue, score and judge the probabilistic forecasts of the named M3 periods."""
    settings = EngineSettings.from_config(config, buckets, data_delay_days)
    chosen = [config.period(name) for name in config.period_names if name in set(periods)]
    if not chosen:
        raise ConfigError("no M3 period to run")
    for period in chosen:
        last_target = period.last_origin + timedelta(days=config.max_horizon)
        if last_target > series.end:
            raise ConfigError(
                f"period {period.name} needs actuals through {last_target}, but the series "
                f"ends {series.end}"
            )
    pairs = config.pairs()
    intervals, _ = issue_forecasts(records, series, config, settings, periods=chosen, pairs=pairs)
    attach_outcomes(intervals, pd.DataFrame(columns=list(EXCEEDANCE_COLUMNS)), series)
    selection = select_interval_method(intervals, config)
    seal_check: dict[str, Any] | None = None
    decided: str | None = None
    if sealed_selection is not None:
        differences = (
            ["selection period not run"]
            if selection is None
            else selection_differences(selection, sealed_selection)
        )
        seal_check = {"differences": differences}
        decided = sealed_selection.get("selected_pair")
    elif selection is not None:
        decided = selection.get("selected_pair")
    baseline_ids = [p.pair_id for p in config.baselines]
    exceedance_ids = (
        [decided] if decided else [p.pair_id for p in config.candidates]
    ) + baseline_ids
    exceedance_pairs = [config.pair(pair_id) for pair_id in exceedance_ids]
    again, exceedance = issue_forecasts(
        records,
        series,
        config,
        settings,
        periods=chosen,
        pairs=exceedance_pairs,
        exceedance_pairs=exceedance_ids,
    )
    attach_outcomes(again, exceedance, series)
    subset = intervals[intervals["pair_id"].isin(exceedance_ids)].reset_index(drop=True)
    if not again.equals(subset):  # the two passes must issue identical intervals
        raise AssertionError("re-issuing the selected intervals gave different results")
    summary = interval_summary(intervals, config, buckets)
    reference = config.decision_criteria.exceedance_reference
    exceedance_rows = exceedance_summary(exceedance, config, buckets, reference)
    focus = [decided] if decided else [p.pair_id for p in config.candidates]
    payload: dict[str, Any] = {
        "record_type": RECORD_TYPE_PROBABILISTIC,
        "experiment_id": config.experiment_id,
        "config_fingerprint": config.fingerprint(),
        "periods_run": [p.name for p in chosen],
        "protocol": {
            "primary_model": config.primary_model,
            "point_models": list(config.point_models),
            "max_horizon": config.max_horizon,
            "levels": list(config.levels),
            "quantile_levels": list(config.quantile_levels),
            "data_latency_days": config.calibration.data_latency_days,
            "data_delay_days": data_delay_days,
            "eligibility_rule": (
                "residual.target_date <= calibration_origin - data_latency_days, with "
                "calibration_origin = issue origin (rolling) or the period's first origin (fixed)"
            ),
            "min_calibration_origins": config.calibration.min_calibration_origins,
            "min_effective_calibration": config.calibration.min_effective_calibration,
            "pooling": config.calibration.pooling,
            "buckets": [{"label": b.label, "first": b.first, "last": b.last} for b in buckets],
            "periods": [p.model_dump(mode="json") for p in config.periods],
            "candidates": [p.pair_id for p in config.candidates],
            "baselines": baseline_ids,
            "decision_criteria": config.decision_criteria.model_dump(mode="json"),
        },
        "thresholds": threshold_descriptions(config),
        "selection": selection,
        "selection_seal_check": seal_check,
        "decided_pair": decided,
        "calibration_support": calibration_support(intervals),
        "interval_summary": summary,
        "comparisons": comparisons(intervals, config, buckets, _comparison_pairs(config, decided)),
        "exceedance_summary": exceedance_rows,
        "strata": (
            strata_summary(intervals, exceedance, config, [*focus, *baseline_ids], flagged_dates)
            if config.sensitivity.strata
            else []
        ),
        "width_profile": width_profile(intervals, config, [*focus, *baseline_ids]),
        "decision_status": (
            decision_status(summary, exceedance_rows, config, buckets, decided) if decided else None
        ),
        "sensitivity": _sensitivity(
            records, series, config, buckets, data_delay_days, chosen, intervals, decided
        ),
        "limitations": list(LIMITATIONS),
    }
    return ProbabilisticResult(intervals, exceedance, payload)


def _sensitivity(
    records: pd.DataFrame,
    series: DemandSeries,
    config: UncertaintyConfig,
    buckets: Sequence[HorizonBucket],
    data_delay_days: int,
    periods: Sequence[UncertaintyPeriodConfig],
    intervals: pd.DataFrame,
    decided: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"latency": [], "coverage_bandwidth": []}
    if decided is None:
        return result
    pair = config.pair(decided)
    for latency in config.sensitivity.latency_days:
        settings = EngineSettings.from_config(
            config, buckets, data_delay_days, latency_days=latency
        )
        shifted, _ = issue_forecasts(
            records, series, config, settings, periods=periods, pairs=[pair]
        )
        attach_outcomes(shifted, pd.DataFrame(columns=list(EXCEEDANCE_COLUMNS)), series)
        for row in interval_summary(shifted, config, buckets, horizons=False):
            result["latency"].append({"data_latency_days": latency, **row})
    evaluation = config.period_with_role("evaluation")
    if evaluation is not None and evaluation.name in {p.name for p in periods}:
        result["coverage_bandwidth"] = coverage_bandwidth_sensitivity(
            intervals,
            config,
            period=evaluation.name,
            model_id=config.primary_model,
            pair_id=decided,
        )
    return result
