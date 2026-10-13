"""Refit the legacy model with the M2 implementation and compare with its published outputs.

This reproduces two *different* legacy procedures, which M2 keeps apart:

1. **One-step holdout** (notebook cell index 15; record type
   ``legacy_one_step_holdout``): fit on every row before the last 90 days, then predict
   each of those 90 days from its *observed* previous days (``h = 1``, teacher forcing).
   This is where the published MAE 514.30, RMSE 676.70 and MAPE 4.93 % come from. It is
   not a 30-day forecast and is never part of the backtest.
2. **Recursive 30-day forecast** (cell index 16): refit on all rows, then forecast
   2026-07-01..2026-07-30 recursively, feeding each clipped, integer-rounded prediction
   back as demand. It is checked through :func:`atlas.backtest.forecast_at_origin`, the
   function the backtest and ``atlas forecast`` use, so the 30 exact matches vouch for
   the code that produced the backtest.

Both use :func:`atlas.models.legacy_linear_regression` and the M1 feature builder.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final

import numpy as np
import pandas as pd

from atlas.backtest import RECORD_TYPE_LEGACY_HOLDOUT, forecast_at_origin
from atlas.errors import ForecastError
from atlas.features import training_features
from atlas.issues import CheckReport, Issue, Severity
from atlas.metrics import error_metrics
from atlas.models import FittedRecursiveLinear, legacy_linear_regression
from atlas.series import DemandSeries

HOLDOUT_DAYS: Final = 90
FORECAST_DAYS: Final = 30
MODEL_ROW: Final = "Linear Regression Forecast"
BASELINE_ROW: Final = "7-Day Seasonal Baseline"


@dataclass(frozen=True)
class LegacyHoldout:
    """The legacy one-step holdout recomputed: predictions are clipped, not rounded."""

    dates: pd.DatetimeIndex
    actual: np.ndarray
    predictions: np.ndarray
    rank: int
    n_features: int
    n_train_rows: int

    def metrics(self) -> dict[str, float]:
        scores = error_metrics(self.predictions, self.actual)
        if scores.mae is None or scores.rmse is None or scores.mape_pct is None:
            raise AssertionError("the holdout has no rows")
        return {"mae": scores.mae, "rmse": scores.rmse, "mape_pct": scores.mape_pct}

    def records(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "record_type": RECORD_TYPE_LEGACY_HOLDOUT,
                "target_date": self.dates,
                "horizon": 1,
                "prediction": self.predictions,
                "prediction_rounded": np.round(self.predictions),
                "actual": self.actual,
            }
        )


def legacy_holdout(series: DemandSeries, holdout_days: int = HOLDOUT_DAYS) -> LegacyHoldout:
    """Notebook cell index 15: fit before the last days, predict them from observed lags."""
    test_start = series.end - timedelta(days=holdout_days - 1)
    fitted = legacy_linear_regression(min_training_rows=1).fit(
        series.through(test_start - timedelta(days=1))
    )
    rows = training_features(
        series,
        origin=series.end,
        horizon=1,
        feature_set=fitted.feature_set,
        first_target=test_start,
    )
    if rows.target is None:  # training rows always carry targets
        raise AssertionError("training_features returned no target")
    return LegacyHoldout(
        dates=pd.DatetimeIndex(rows.frame.index),
        actual=rows.target.to_numpy(dtype="float64"),
        predictions=fitted.predict_rows(rows.frame),
        rank=fitted.result.diagnostics.rank,
        n_features=len(fitted.feature_set),
        n_train_rows=fitted.diagnostics.n_train_rows,
    )


def _published_metrics(model_metrics: pd.DataFrame, row: str) -> dict[str, float]:
    found = model_metrics.loc[model_metrics["model_name"] == row]
    if found.empty:
        return {}
    return {name: float(found[name].iloc[0]) for name in ("mae", "rmse", "mape_pct")}


def legacy_holdout_rows(holdout: LegacyHoldout, model_metrics: pd.DataFrame) -> dict[str, Any]:
    """Category-1 rows for the legacy-holdout comparison window of the backtest report."""
    published = _published_metrics(model_metrics, MODEL_ROW)
    baseline = _published_metrics(model_metrics, BASELINE_ROW)
    reproduced = holdout.metrics()
    n = len(holdout.dates)
    return {
        "record_type": RECORD_TYPE_LEGACY_HOLDOUT,
        "first_target": holdout.dates[0].date().isoformat(),
        "last_target": holdout.dates[-1].date().isoformat(),
        "rows": [
            {
                "label": "published legacy one-step holdout (single fit, observed lags)",
                "n": n,
                **published,
            },
            {
                "label": "M2 re-computation of the legacy one-step holdout",
                "n": n,
                **reproduced,
            },
            {"label": "published 7-day seasonal baseline y(t-7)", "n": n, **baseline},
        ],
    }


def legacy_refit_check(
    series: DemandSeries,
    test_results: pd.DataFrame,
    future_forecast: pd.DataFrame,
    model_metrics: pd.DataFrame,
) -> tuple[CheckReport, LegacyHoldout]:
    """Compare the M2 legacy refit with the three published legacy tables."""
    issues: list[Issue] = []
    subject = "legacy_linear_regression"

    holdout = legacy_holdout(series)
    published_dates = pd.DatetimeIndex(pd.to_datetime(test_results["request_date"]))
    published = test_results["model_forecast"].to_numpy(dtype="float64")
    same_dates = published_dates.equals(holdout.dates)
    rounded = np.round(holdout.predictions)  # the notebook exported np.round (half to even)
    holdout_matches = int((rounded == published).sum()) if same_dates else 0
    if not same_dates or holdout_matches != len(published):
        issues.append(
            Issue(
                "holdout_mismatch",
                Severity.ERROR,
                subject,
                f"{holdout_matches} of {len(published)} published holdout predictions reproduced.",
            )
        )
    scores = holdout.metrics()
    committed = _published_metrics(model_metrics, MODEL_ROW)
    metric_rows: list[dict[str, Any]] = []
    for name in ("mae", "rmse", "mape_pct"):
        recomputed = scores[name]
        ok = name in committed and round(recomputed, 2) == committed[name]
        metric_rows.append(
            {
                "metric": name,
                "committed": committed.get(name),
                "recomputed": recomputed,
                "ok": ok,
            }
        )
        if not ok:
            issues.append(
                Issue(
                    "holdout_metric_mismatch",
                    Severity.ERROR,
                    subject,
                    f"{name}: recomputed {recomputed:.4f}, published {committed.get(name)}.",
                )
            )

    steps = len(future_forecast)
    outcome = forecast_at_origin(legacy_linear_regression(min_training_rows=1), series, steps)
    if outcome.path is None or not isinstance(outcome.fitted, FittedRecursiveLinear):
        raise ForecastError(f"the legacy model failed to forecast: {outcome.failure}")
    path = outcome.path
    forecast = path.predictions
    future_dates = pd.DatetimeIndex(pd.to_datetime(future_forecast["request_date"]))
    future_published = future_forecast["forecast_requests"].to_numpy(dtype="float64")
    future_same_dates = future_dates.equals(pd.DatetimeIndex(forecast.index))
    future_matches = (
        int((forecast.to_numpy() == future_published).sum()) if future_same_dates else 0
    )
    fed_back = path.inputs.loc[forecast.index] if path.inputs is not None else None
    fed_back_ok = fed_back is not None and bool((fed_back.to_numpy() == future_published).all())
    raw = (
        path.raw_predictions.to_numpy() if path.raw_predictions is not None else forecast.to_numpy()
    )
    rounding_gap = float(np.abs(raw - forecast.to_numpy()).max())
    if not future_same_dates or future_matches != steps or not fed_back_ok:
        issues.append(
            Issue(
                "recursive_forecast_mismatch",
                Severity.ERROR,
                subject,
                f"{future_matches} of {steps} published recursive forecasts reproduced; fed-back "
                f"inputs equal the published integers: {fed_back_ok}.",
            )
        )
    if not issues:
        issues.append(
            Issue(
                "legacy_design_rank_deficient",
                Severity.INFO,
                subject,
                f"The legacy design has rank {holdout.rank} of {holdout.n_features} features: "
                "predictions are reproducible but the coefficients are not identifiable and "
                "are not reported.",
            )
        )
    report = CheckReport(
        name="legacy_refit",
        issues=tuple(issues),
        facts={
            "holdout": {
                "record_type": RECORD_TYPE_LEGACY_HOLDOUT,
                "procedure": "one-step (h = 1) with observed lags, single fit (cell index 15)",
                "first_date": holdout.dates[0].date().isoformat(),
                "last_date": holdout.dates[-1].date().isoformat(),
                "n_train_rows": holdout.n_train_rows,
                "predictions_reproduced": holdout_matches,
                "predictions_published": len(published),
                "max_abs_difference_unrounded": (
                    float(np.abs(holdout.predictions - published).max()) if same_dates else None
                ),
                "metrics": metric_rows,
            },
            "recursive_forecast": {
                "procedure": "recursive 30-day forecast through forecast_at_origin, clip at 0 "
                "and round half to even before feedback (cell index 16)",
                "first_date": forecast.index[0].date().isoformat(),
                "last_date": forecast.index[-1].date().isoformat(),
                "forecasts_reproduced": future_matches,
                "forecasts_published": steps,
                "fed_back_inputs_equal_published": fed_back_ok,
                "max_abs_raw_minus_rounded": rounding_gap,
            },
            "design_rank": holdout.rank,
            "n_features": holdout.n_features,
        },
    )
    return report, holdout
