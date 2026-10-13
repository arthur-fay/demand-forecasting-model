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
DELAY1 = RESULTS / "atlas-m2-rolling-origin-v1-delay1" / "development"
DOC = REPO_ROOT / "docs" / "BACKTESTING.md"
LIMITATIONS = REPO_ROOT / "docs" / "LEGACY_LIMITATIONS.md"
# SHA-256 of the files of the first reference run (commit 10343e2, before the independent
# review). The review amended reporting and enforcement only, so these must not change.
PRE_REVIEW_OUTPUTS = {
    "forecasts.csv": "0e512044257fb716152618c64c88a10450f4d49dbe6fd59ed61b6538f71eeaba",
    "fits.csv": "5443998e2182deb9782bdadc0cbeb48c5e5923ac99d5ad3af95b0c3237099717",
    "observed_inputs.csv": "d20ee7bafc66e37a303a2d5c7f9f6c1cd28186304856571001dddb579634e151",
}
SEALED_COMMIT = "a16672f3454a36e474ae1fe3290f0646b79ff599"
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


@pytest.mark.parametrize("directory", [V1 / "development", V1 / "full", DELAY1])
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
    assert metrics["selection_seal_check"]["differences"] == []
    assert recommendation["recommendation"] == metrics["recommendation"]
    assert recommendation["result_fingerprint"] == full["result_fingerprint"]
    assert recommendation["recommendation"]["recommended_model"] == "atlas_linear_regression"


def test_the_sealed_development_run_is_the_pre_registration_record() -> None:
    """The sealed run was produced at the freeze commit, on a clean tree, and the reference
    run (later code) reproduces every development-period metric it reported."""
    development = read_json(V1 / "development" / "manifest.json")
    assert development["git"]["commit"] == SEALED_COMMIT
    assert development["git"]["dirty"] is False
    sealed = read_json(V1 / "development" / "metrics.json")
    full = read_json(V1 / "full" / "metrics.json")
    keys = ("n_predictions", "n_failed", "mae", "rmse", "bias", "wape_pct", "mase", "mape_pct")

    def rows(metrics: dict[str, Any]) -> dict[tuple[str, str], tuple[Any, ...]]:
        return {
            (r["model_id"], r["label"]): tuple(r[k] for k in keys)
            for r in metrics["summary"]
            if r["period"] == "development"
        }

    assert rows(sealed) == rows(full)
    assert sealed["selection"] == full["selection"]


def test_the_review_amendments_changed_no_forecast() -> None:
    full = read_json(V1 / "full" / "manifest.json")
    for name, digest in PRE_REVIEW_OUTPUTS.items():
        assert full["outputs"][name] == digest, name


@pytest.mark.parametrize("directory", [V1 / "full", DELAY1])
def test_report_is_rendered_from_the_committed_metrics(directory: Path) -> None:
    """Runs produced by the current reporting code. (The sealed development run was
    rendered by the code of its own commit; see the test above.)"""
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
    document = " ".join(DOC.read_text(encoding="utf-8").split())
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


# --- Quoted numbers in the prose (review finding: hand-written claims had drifted) ------


def _summary(metrics: dict[str, Any], model: str, period: str, label: str) -> dict[str, Any]:
    return next(
        r
        for r in metrics["summary"]
        if r["model_id"] == model and r["period"] == period and r["label"] == label
    )


def _range(values: list[float], digits: int = 1) -> str:
    return f"{min(values):.{digits}f}-{max(values):.{digits}f}"


def test_documented_legacy_mape_ranges_cover_every_horizon() -> None:
    metrics = read_json(V1 / "full" / "metrics.json")
    window = [
        r["mape_pct"]
        for r in metrics["comparison_windows"][0]["rows"]
        if r["model_id"] == "legacy_linear_regression" and 2 <= r["horizon"] <= 30
    ]
    year = [
        _summary(metrics, "legacy_linear_regression", "confirmation", f"h{h:02d}")["mape_pct"]
        for h in range(7, 31)
    ]
    assert len(window) == 29
    for path in (DOC, LIMITATIONS):
        text = " ".join(path.read_text(encoding="utf-8").split())
        assert f"{_range(window)} % at horizons 2-30" in text, path
        assert f"{_range(year)} % from horizon 7 on" in text, path


def test_documented_reliability_comparison_is_paired() -> None:
    metrics = read_json(V1 / "full" / "metrics.json")
    row = next(
        r
        for r in metrics["regression_reliability"]
        if r["model_id"] == "legacy_linear_regression" and r["period"] == "development"
    )
    assert row["comparator"] == "same_weekday_median_5w"
    assert row["n_reliable_pairs"] + row["n_removed_pairs"] == row["n_all_pairs"] == row["n_all"]
    text = " ".join(DOC.read_text(encoding="utf-8").split())
    assert (
        f"B\\* on exactly the same {row['n_reliable_pairs']:,} forecasts scores "
        f"{row['comparator_mae_reliable_pairs']:,.1f}"
    ) in text
    deficit = 100 * (row["mae_reliable_pairs"] / row["comparator_mae_reliable_pairs"] - 1)
    assert deficit > 0  # the flagged forecasts do not explain the deficit
    assert f"still {deficit:.1f} % worse" in text


def test_documented_recursion_shares() -> None:
    metrics = read_json(V1 / "full" / "metrics.json")
    rows = metrics["observed_input_decomposition"]
    text = " ".join(DOC.read_text(encoding="utf-8").split())
    for model, period in (
        ("legacy_linear_regression", "development"),
        ("atlas_linear_regression", "development"),
        ("legacy_linear_regression", "confirmation"),
        ("atlas_linear_regression", "confirmation"),
    ):
        cell = {r["horizon"]: r for r in rows if r["model_id"] == model and r["period"] == period}
        recursive = cell[30]["mae_recursive"] - cell[1]["mae_recursive"]
        observed = cell[30]["mae_observed_inputs"] - cell[1]["mae_observed_inputs"]
        share = 100 * (recursive - observed) / recursive
        quoted = (
            f"{share:.0f} % (observed-input MAE {cell[1]['mae_observed_inputs']:,.1f} → "
            f"{cell[30]['mae_observed_inputs']:,.1f})"
            if period == "development" and model == "legacy_linear_regression"
            else f"{share:.0f} %"
        )
        assert quoted in text, (model, period)
    # For the legacy model in development, the observed-input error grows by more than
    # half as much as the recursive error: recursion is not the whole story there.
    dev = {
        r["horizon"]: r
        for r in rows
        if r["model_id"] == "legacy_linear_regression" and r["period"] == "development"
    }
    growth = dev[30]["mae_recursive"] - dev[1]["mae_recursive"]
    assert dev[30]["mae_observed_inputs"] - dev[1]["mae_observed_inputs"] > growth / 2


def test_documented_delay_ranking_statement() -> None:
    plain = [
        r["model_id"]
        for r in read_json(V1 / "development" / "selection.json")["selection"]["ranking"]
    ]
    delayed = [r["model_id"] for r in read_json(DELAY1 / "selection.json")["selection"]["ranking"]]
    assert plain[:2] == delayed[:2]  # best regression and B* unchanged
    moved = [m for i, m in enumerate(plain) if delayed.index(m) != i]
    text = " ".join(DOC.read_text(encoding="utf-8").split())
    assert "the ranking does not change" not in text
    for model in moved:
        swap = f"{SHORT[model]} {plain.index(model) + 1} → {delayed.index(model) + 1}"
        assert swap in text, swap


def test_documented_origin_month_losses() -> None:
    metrics = read_json(V1 / "full" / "metrics.json")
    months = metrics["by_origin_month"]
    cells = {(r["period"], r["model_id"], r["origin_month"]): r for r in months}
    keys = sorted({(r["period"], r["origin_month"]) for r in months})
    losing = [
        k
        for k in keys
        if cells[(k[0], "atlas_linear_regression", k[1])]["mae"]
        > cells[(k[0], "same_weekday_median_5w", k[1])]["mae"]
    ]
    text = " ".join(DOC.read_text(encoding="utf-8").split())
    assert f"loses to the same-weekday median in {len(losing)} of {len(keys)} origin months" in text
    assert ("development", "2024-12") in losing
    assert "the hard months (holidays, year-end, winter surges)" not in text
