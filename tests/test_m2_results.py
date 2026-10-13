"""The committed M2 reference results are consistent with the engine and the documentation.

Expected values come from the committed files under ``docs/results/``; the engine is re-run
only for the few origins in ``spot_check.csv``. These tests fail if the code changes what
the protocol computes, if a committed file is edited, or if the documentation drifts from
the metrics.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from atlas.backtest import BacktestPlan, Period, PeriodRole, run_backtest
from atlas.experiment import load_config
from atlas.report import render_report
from atlas.runner import load_series
from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.integration

RESULTS = REPO_ROOT / "docs" / "results"
V1 = RESULTS / "atlas-m2-rolling-origin-v1"
CONFIG = REPO_ROOT / "configs" / "backtest" / "atlas-m2-rolling-origin-v1.json"
V1_FINGERPRINT = "78da64e8c2ce75d9318cf855137d015e9692dbd50517a70d00243d7f731b1753"
DOC = REPO_ROOT / "docs" / "BACKTESTING.md"
MODELS = (
    "naive",
    "seasonal_naive",
    "same_weekday_mean_4w",
    "same_weekday_median_5w",
    "seasonal_exponential_smoothing",
    "legacy_linear_regression",
    "atlas_linear_regression",
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "directory",
    [V1 / "development", V1 / "full", RESULTS / "atlas-m2-rolling-origin-v1-delay1/development"],
)
def test_committed_files_match_their_manifest(directory: Path) -> None:
    manifest = read_json(directory / "manifest.json")
    assert manifest["record_type"] == "historical_backtest"
    assert manifest["complete"] is True
    assert manifest["git"]["dirty"] is False
    present = [p.name for p in directory.iterdir() if p.name != "manifest.json"]
    assert present, "no result files committed"
    for name in present:
        if name == "spot_check.csv":
            continue  # extracted from forecasts.csv after the run
        assert sha256(directory / name) == manifest["outputs"][name], name


def test_the_sealed_selection_chain() -> None:
    sealed = read_json(V1 / "development" / "selection.json")
    development = read_json(V1 / "development" / "manifest.json")
    full = read_json(V1 / "full" / "manifest.json")
    recommendation = read_json(V1 / "full" / "recommendation.json")
    config = load_config(CONFIG)
    assert config.fingerprint() == V1_FINGERPRINT
    assert sealed["config_fingerprint"] == V1_FINGERPRINT == full["config_fingerprint"]
    assert sealed["result_fingerprint"] == development["result_fingerprint"]
    assert development["periods_run"] == ["development"]
    assert full["periods_run"] == ["development", "confirmation", "legacy_window"]
    assert full["sealed_selection"] == sealed
    metrics = read_json(V1 / "full" / "metrics.json")
    assert metrics["selection"] == sealed["selection"]
    recomputed = metrics["selection_recomputed"]
    assert recomputed["selected_model"] == sealed["selection"]["selected_model"]
    assert recomputed["ranking"] == sealed["selection"]["ranking"]
    assert recommendation["recommendation"] == metrics["recommendation"]
    assert recommendation["result_fingerprint"] == full["result_fingerprint"]


@pytest.mark.parametrize("directory", [V1 / "development", V1 / "full"])
def test_report_is_rendered_from_the_committed_metrics(directory: Path) -> None:
    metrics = read_json(directory / "metrics.json")
    assert render_report(metrics) == (directory / "report.md").read_text(encoding="utf-8")


def _spot_plan(spot: pd.DataFrame, max_horizon: int) -> BacktestPlan:
    periods = []
    for index, (period, origin) in enumerate(
        sorted({(p, o) for p, o in zip(spot["period"], spot["origin"], strict=True)})
    ):
        day = date.fromisoformat(origin)
        periods.append(
            Period(
                f"spot_{index}_{period}",
                PeriodRole.DESCRIPTIVE,
                day,
                day,
                allow_incomplete_horizons=True,
            )
        )
    return BacktestPlan(tuple(periods), max_horizon=max_horizon, min_history_days=365)


def test_spot_check_origins_are_recomputed_exactly(repo_settings: Any) -> None:
    spot = pd.read_csv(V1 / "full" / "spot_check.csv", float_precision="round_trip")
    assert sorted(set(spot["model_id"])) == sorted(MODELS)
    assert len(spot) == 6 * len(MODELS) * 30
    config = load_config(CONFIG)
    _, data = load_series(repo_settings, "spot_check")
    assert data is not None
    result = run_backtest(data.series, _spot_plan(spot, config.max_horizon), config.build_models())
    recomputed = result.records.copy()
    recomputed["origin"] = recomputed["origin"].dt.strftime("%Y-%m-%d")
    keys = ["model_id", "origin", "horizon"]
    merged = spot.merge(recomputed, on=keys, suffixes=("", "_new"), validate="one_to_one")
    assert len(merged) == len(spot)
    legacy = merged["model_id"] == "legacy_linear_regression"
    # Integer feedback: the legacy predictions must match exactly (no raw value in these
    # origins lies within 0.0015 of a rounding boundary, far above floating-point noise).
    np.testing.assert_array_equal(
        merged.loc[legacy, "prediction"], merged.loc[legacy, "prediction_new"]
    )
    np.testing.assert_allclose(merged["prediction"], merged["prediction_new"], rtol=1e-9)
    np.testing.assert_allclose(merged["prediction_raw"], merged["prediction_raw_new"], rtol=1e-9)
    np.testing.assert_allclose(merged["mase_scale"], merged["mase_scale_new"], rtol=1e-12)
    assert (merged["status"] == merged["status_new"]).all()
    assert merged["actual"].equals(merged["actual_new"])


# --- Documentation tables -----------------------------------------------------------------

SHORT = {
    "naive": "naive",
    "seasonal_naive": "seasonal naive (SN)",
    "same_weekday_mean_4w": "same-weekday mean 4w",
    "same_weekday_median_5w": "same-weekday median 5w (B*)",
    "seasonal_exponential_smoothing": "exp. smoothing",
    "legacy_linear_regression": "legacy regression",
    "atlas_linear_regression": "ATLAS regression",
}
BUCKETS = ("day_1", "days_2_7", "days_8_14", "days_15_30", "days_1_30")


def _row(metrics: dict[str, Any], period: str, model: str) -> str:
    def find(key: str, label: str) -> dict[str, Any] | None:
        return next(
            (
                r
                for r in metrics[key]
                if r["model_id"] == model and r["period"] == period and r["label"] == label
            ),
            None,
        )

    cells = [SHORT[model]]
    for label in BUCKETS:
        cells.append(f"{find('summary', label)['mae']:,.1f}")  # type: ignore[index]
    versus = find("comparison_to_reference", "days_1_30")
    best = find("comparison_to_best_baseline", "days_1_30")
    cells.append("ref" if versus is None else f"{versus['mae_improvement_pct']:+.1f} %")
    cells.append("B*" if best is None else f"{best['mae_improvement_pct']:+.1f} %")
    return "| " + " | ".join(cells) + " |"


@pytest.mark.parametrize("period", ["development", "confirmation"])
def test_documented_bucket_tables_match_the_metrics(period: str) -> None:
    metrics = read_json(V1 / "full" / "metrics.json")
    document = DOC.read_text(encoding="utf-8")
    for model in MODELS:
        assert _row(metrics, period, model) in document, (period, model)


def test_documented_headline_numbers_match_the_metrics() -> None:
    metrics = read_json(V1 / "full" / "metrics.json")
    document = DOC.read_text(encoding="utf-8")
    tests = {t["hypothesis"][:2]: t for t in metrics["recommendation"]["primary_tests"]}
    h1, h2 = tests["H1"], tests["H2"]
    assert f"{h1['mae_difference']:,.1f} [{h1['mae_difference_ci_low']:,.1f}, " in document
    assert f"Holm p = {h2['holm_adjusted_p_value']:.3f}" in document
    assert f"(Holm p = {h1['holm_adjusted_p_value']:.3f})" in document
    assert metrics["recommendation"]["recommended_model"] == "atlas_linear_regression"
    assert "**recommended model `atlas_linear_regression`**" in document
    window = metrics["comparison_windows"][0]
    assert window["complete_at_every_horizon"] is True
    for row in window["rows"]:
        if row["model_id"] == "seasonal_naive" and row["horizon"] <= 7:
            # The engine's horizon-correct seasonal naive equals the published baseline.
            assert (round(row["mae"], 2), round(row["rmse"], 2), round(row["mape_pct"], 2)) == (
                851.16,
                1107.24,
                8.23,
            )
