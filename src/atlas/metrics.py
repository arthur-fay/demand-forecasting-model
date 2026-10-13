"""Forecast-error metrics, horizon buckets and paired comparisons (M2).

Conventions (``e = prediction - actual``):

=================  ======================================================================
``mae``            mean ``|e|``
``rmse``           ``sqrt(mean(e**2))``
``bias``           mean ``e``; positive means over-forecasting
``bias_pct``       ``100 * sum(e) / sum(actual)``
``wape_pct``       ``100 * sum(|e|) / sum(|actual|)``
``mase``           mean of ``|e| / scale``, where ``scale`` is the in-sample MAE of the
                   one-step weekly seasonal naive forecast over all data observed by the
                   forecast's data cutoff (``mean |y_t - y_{t-7}|``). MASE below 1 is **not**
                   skill against seasonal naive: that comparison is ``mae_improvement_pct``
                   on common forecasts
``mape_pct``       ``100 * mean(|e| / |actual|)``. Secondary only, and not comparable with the
                   legacy 4.93 %, which is a one-step score on one block with observed lags
=================  ======================================================================

A metric whose denominator is zero (or whose inputs are empty) is ``None``, never 0 or
``inf``.

Paired comparisons use only the ``(origin, horizon)`` pairs on which *both* models produced
a forecast with an observed actual, so a model is never credited for skipping hard cases.
Their Diebold-Mariano tests (:mod:`atlas.stats`) use one loss differential per origin,
averaged over the horizons of the scope, and only origins with every horizon paired.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from atlas.errors import ConfigError, ForecastError
from atlas.stats import DieboldMariano, diebold_mariano, long_run_variance

OK = "ok"

Values = Sequence[float] | np.ndarray | pd.Series


@dataclass(frozen=True)
class HorizonBucket:
    label: str
    first: int
    last: int

    def __post_init__(self) -> None:
        if not 1 <= self.first <= self.last:
            raise ConfigError(f"invalid horizon bucket {self.label}: {self.first}..{self.last}")

    def contains(self, horizon: int) -> bool:
        return self.first <= horizon <= self.last


DEFAULT_BUCKETS: tuple[HorizonBucket, ...] = (
    HorizonBucket("day_1", 1, 1),
    HorizonBucket("days_2_7", 2, 7),
    HorizonBucket("days_8_14", 8, 14),
    HorizonBucket("days_15_30", 15, 30),
)


@dataclass(frozen=True)
class ErrorMetrics:
    n: int
    mae: float | None
    rmse: float | None
    bias: float | None
    bias_pct: float | None
    wape_pct: float | None
    mase: float | None
    mape_pct: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def error_metrics(prediction: Values, actual: Values, scale: Values | None = None) -> ErrorMetrics:
    """Every point metric for aligned predictions and actuals (optionally MASE scales)."""
    predicted = np.asarray(prediction, dtype="float64")
    observed = np.asarray(actual, dtype="float64")
    if predicted.shape != observed.shape or predicted.ndim != 1:
        raise ForecastError("prediction and actual must be 1-D arrays of equal length")
    if not (np.isfinite(predicted).all() and np.isfinite(observed).all()):
        raise ForecastError("metrics need finite predictions and actuals")
    n = int(predicted.size)
    if n == 0:
        return ErrorMetrics(0, None, None, None, None, None, None, None)
    error = predicted - observed
    absolute = np.abs(error)
    total_actual = float(observed.sum())
    total_absolute_actual = float(np.abs(observed).sum())
    mase: float | None = None
    if scale is not None:
        scales = np.asarray(scale, dtype="float64")
        if scales.shape != predicted.shape:
            raise ForecastError("scale must align with the predictions")
        if np.isfinite(scales).all() and (scales > 0).all():
            mase = float((absolute / scales).mean())
    return ErrorMetrics(
        n=n,
        mae=float(absolute.mean()),
        rmse=float(math.sqrt(float((error**2).mean()))),
        bias=float(error.mean()),
        bias_pct=100.0 * float(error.sum()) / total_actual if total_actual != 0 else None,
        wape_pct=(
            100.0 * float(absolute.sum()) / total_absolute_actual
            if total_absolute_actual != 0
            else None
        ),
        mase=mase,
        mape_pct=(
            100.0 * float((absolute / np.abs(observed)).mean()) if (observed != 0).all() else None
        ),
    )


def seasonal_naive_scale(history: pd.Series, season: int = 7) -> float | None:
    """In-sample MAE of the one-step seasonal naive forecast: mean ``|y_t - y_{t-season}|``.

    Only pairs with both values observed are used; ``None`` if there is no pair or the
    scale is zero (MASE would be undefined). ``history`` must be on a daily index.
    """
    values = history.to_numpy(dtype="float64")
    if len(values) <= season:
        return None
    differences = np.abs(values[season:] - values[:-season])
    differences = differences[np.isfinite(differences)]
    if differences.size == 0:
        return None
    scale = float(differences.mean())
    return scale if scale > 0 else None


# --- Helpers over backtest records ------------------------------------------------------


def ok_records(records: pd.DataFrame) -> pd.DataFrame:
    return records[records["status"] == OK]


def scopes(
    buckets: Iterable[HorizonBucket], max_horizon: int, *, horizons: bool = True
) -> list[tuple[str, str, int, int]]:
    """``(scope, label, first, last)`` for each horizon, each bucket and all horizons."""
    result: list[tuple[str, str, int, int]] = []
    if horizons:
        result += [("horizon", f"h{h:02d}", h, h) for h in range(1, max_horizon + 1)]
    result += [("bucket", b.label, b.first, b.last) for b in buckets]
    result.append(("bucket", f"days_1_{max_horizon}", 1, max_horizon))
    return result


def contiguous(origins: pd.DatetimeIndex, stride_days: int) -> bool:
    """True when the origins form one gap-free grid with the given stride."""
    index = origins.sort_values()
    if len(index) < 2:
        return True
    steps = np.diff(index.to_numpy(dtype="datetime64[D]").astype("int64"))
    return bool((steps == stride_days).all())


def per_origin_mean(cell: pd.DataFrame, values: pd.Series, width: int) -> pd.Series:
    """Mean of ``values`` per origin, for origins with all ``width`` horizons present."""
    grouped = values.groupby(cell["origin"]).agg(["mean", "size"])
    complete = grouped[grouped["size"] == width].sort_index()
    return complete["mean"]


def effective_sample_size(values: np.ndarray, overlap: int) -> float | None:
    """``T * gamma_0 / long-run variance`` (Bartlett, bandwidth ``2 * overlap``), at most ``T``.

    Roughly how many independent observations the dependent series is worth.
    """
    size = int(values.size)
    if size < 2:
        return None
    centered = values - values.mean()
    gamma0 = float(centered @ centered) / size
    variance = long_run_variance(values, min(2 * overlap, size - 1), "bartlett")
    if gamma0 <= 0 or variance <= 0:
        return None
    return min(float(size), size * gamma0 / variance)


def dependence_test(
    per_origin: pd.Series, *, horizon: int, stride_days: int
) -> tuple[DieboldMariano, float | None]:
    """DM test of a per-origin series, plus its HAC effective sample size."""
    overlap = max(1, math.ceil(horizon / stride_days))
    values = per_origin.to_numpy(dtype="float64")
    test = diebold_mariano(
        values,
        horizon=overlap,
        contiguous=contiguous(pd.DatetimeIndex(per_origin.index), stride_days),
    )
    return test, effective_sample_size(values, overlap)


# --- Summaries ----------------------------------------------------------------------


def summarize(
    records: pd.DataFrame,
    buckets: Iterable[HorizonBucket],
    max_horizon: int,
    stride_days: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Metrics per model and period, for every horizon, every bucket and all horizons.

    ``n_predictions`` counts forecasts with an observed actual (``status == ok``);
    failed forecasts and forecasts without an actual are counted separately. For
    buckets, ``bias_ci_*`` is a 95 % HAC interval for the mean error.
    """
    strides = dict(stride_days or {})
    rows: list[dict[str, Any]] = []
    for _, group in records.groupby(["model_id", "period"], sort=True):
        model_id, period = str(group["model_id"].iloc[0]), str(group["period"].iloc[0])
        for scope, label, first, last in scopes(buckets, max_horizon):
            in_scope = group[group["horizon"].between(first, last)]
            cell = ok_records(in_scope)
            metrics = error_metrics(cell["prediction"], cell["actual"], cell["mase_scale"])
            row: dict[str, Any] = {
                "model_id": model_id,
                "period": period,
                "scope": scope,
                "label": label,
                "first_horizon": first,
                "last_horizon": last,
                "n_scheduled": len(in_scope),
                "n_origins": int(cell["origin"].nunique()),
                "n_predictions": len(cell),
                "n_failed": int((in_scope["status"] == "failed").sum()),
                "n_missing_actual": int((in_scope["status"] == "missing_actual").sum()),
                "n_beyond_snapshot": int((in_scope["status"] == "beyond_snapshot").sum()),
                **metrics.to_dict(),
            }
            if scope == "bucket":
                errors = cell["prediction"] - cell["actual"]
                test, _ = dependence_test(
                    per_origin_mean(cell, errors, last - first + 1),
                    horizon=last,
                    stride_days=strides.get(period, 1),
                )
                row["bias_ci_low"] = test.ci_low
                row["bias_ci_high"] = test.ci_high
            rows.append(row)
    return rows


def paired(records: pd.DataFrame, model_id: str, reference: str) -> pd.DataFrame:
    """Forecasts of ``model_id`` and ``reference`` on common ``(period, origin, horizon)``."""
    keys = ["period", "origin", "horizon"]
    columns = [*keys, "target_date", "prediction", "actual", "mase_scale"]
    ok = ok_records(records)
    model = ok.loc[ok["model_id"] == model_id, columns]
    base = ok.loc[ok["model_id"] == reference, [*keys, "prediction"]]
    return model.merge(base, on=keys, suffixes=("", "_ref"), validate="one_to_one")


def _improvement(value: float | None, reference: float | None) -> float | None:
    if value is None or reference is None or reference == 0:
        return None
    return 100.0 * (1.0 - value / reference)


def compare(
    records: pd.DataFrame,
    model_id: str,
    reference: str,
    buckets: Iterable[HorizonBucket],
    max_horizon: int,
    stride_days: Mapping[str, int] | None = None,
    *,
    horizons: bool = True,
) -> list[dict[str, Any]]:
    """``model_id`` versus ``reference`` on common forecasts, per period and scope.

    ``mae_improvement_pct`` is ``100 * (1 - MAE_model / MAE_reference)``: positive means
    the model's absolute errors are smaller on the same forecasts. ``mae_difference`` is
    ``MAE_model - MAE_reference`` with a 95 % HAC interval; the DM test uses absolute-error
    loss. The interval for the improvement treats the reference MAE as fixed.
    """
    strides = dict(stride_days or {})
    joined = paired(records, model_id, reference)
    rows: list[dict[str, Any]] = []
    for _, group in joined.groupby("period", sort=True):
        period = str(group["period"].iloc[0])
        stride = strides.get(period, 1)
        for scope, label, first, last in scopes(buckets, max_horizon, horizons=horizons):
            cell = group[group["horizon"].between(first, last)]
            model = error_metrics(cell["prediction"], cell["actual"], cell["mase_scale"])
            base = error_metrics(cell["prediction_ref"], cell["actual"], cell["mase_scale"])
            loss = (cell["prediction"] - cell["actual"]).abs() - (
                cell["prediction_ref"] - cell["actual"]
            ).abs()
            test, effective = dependence_test(
                per_origin_mean(cell, loss, last - first + 1), horizon=last, stride_days=stride
            )
            ref_mae = base.mae
            difference = None if model.mae is None or ref_mae is None else model.mae - ref_mae
            rows.append(
                {
                    "model_id": model_id,
                    "reference": reference,
                    "period": period,
                    "scope": scope,
                    "label": label,
                    "first_horizon": first,
                    "last_horizon": last,
                    "n_common": model.n,
                    "n_origins": int(cell["origin"].nunique()),
                    "mae": model.mae,
                    "mae_reference": ref_mae,
                    "mae_difference": difference,
                    "mae_difference_ci_low": test.ci_low,
                    "mae_difference_ci_high": test.ci_high,
                    "mae_improvement_pct": _improvement(model.mae, ref_mae),
                    "mae_improvement_ci_low_pct": (
                        None
                        if test.ci_high is None or not ref_mae
                        else -100 * test.ci_high / ref_mae
                    ),
                    "mae_improvement_ci_high_pct": (
                        None if test.ci_low is None or not ref_mae else -100 * test.ci_low / ref_mae
                    ),
                    "rmse": model.rmse,
                    "rmse_reference": base.rmse,
                    "rmse_improvement_pct": _improvement(model.rmse, base.rmse),
                    "mase": model.mase,
                    "mase_reference": base.mase,
                    "dm_n_origins": test.n,
                    "dm_computed": test.computed,
                    "dm_reason": test.reason,
                    "dm_estimator": test.estimator,
                    "dm_statistic": test.statistic,
                    "dm_p_value": test.p_value,
                    "dm_bartlett_p_value": test.bartlett_p_value,
                    "dm_p_value_conservative": test.p_value_conservative,
                    "effective_sample_size": effective,
                }
            )
    return rows


def window_summary(
    records: pd.DataFrame,
    *,
    first_target: pd.Timestamp,
    last_target: pd.Timestamp,
    period: str | None = None,
) -> list[dict[str, Any]]:
    """Per model and horizon metrics for forecasts whose target falls in a date window.

    ``*_unrounded`` metrics use the raw prediction clipped at 0 but not rounded (they
    differ from the main metrics only for models that round, i.e. the legacy regression).
    """
    ok = ok_records(records)
    if period is not None:
        ok = ok[ok["period"] == period]
    window = ok[ok["target_date"].between(first_target, last_target)]
    rows: list[dict[str, Any]] = []
    for _, cell in window.groupby(["model_id", "horizon"], sort=True):
        metrics = error_metrics(cell["prediction"], cell["actual"], cell["mase_scale"])
        raw = error_metrics(cell["prediction_raw"].clip(lower=0.0), cell["actual"])
        rows.append(
            {
                "model_id": str(cell["model_id"].iloc[0]),
                "horizon": int(cell["horizon"].iloc[0]),
                "n_targets": int(cell["target_date"].nunique()),
                **metrics.to_dict(),
                "mae_unrounded": raw.mae,
                "rmse_unrounded": raw.rmse,
                "mape_pct_unrounded": raw.mape_pct,
            }
        )
    return rows
