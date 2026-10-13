"""The committed M3 reference results are consistent with each other, the code and the docs.

Expected values come from the committed files under ``docs/results/atlas-m3-intervals-v1``.
Nothing here re-runs the three-minute pipeline (``atlas uncertainty verify`` does, and CI
runs it on the smoke configuration). These tests fail if a committed file is edited, if the
seal and the evaluation run disagree, or if the documentation drifts from the metrics.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from atlas.uncertainty_config import load_uncertainty_config
from atlas.uncertainty_report import render_uncertainty_report
from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.integration

RESULTS = REPO_ROOT / "docs" / "results" / "atlas-m3-intervals-v1"
SELECTION = RESULTS / "selection"
FULL = RESULTS / "full"
PROJECTION = RESULTS / "projection"
PROTOCOL = REPO_ROOT / "configs" / "uncertainty" / "atlas-m3-intervals-v1.json"
M2_FULL = REPO_ROOT / "docs" / "results" / "atlas-m2-rolling-origin-v1" / "full"
M2_PROJECTION = REPO_ROOT / "docs" / "results" / "atlas-m2-rolling-origin-v1" / "projection"
DOC = REPO_ROOT / "docs" / "UNCERTAINTY.md"
FREEZE_COMMIT = "cfdf634f1b535e0f2e70252f9d1c469bd9bac3da"
SEAL_COMMIT_PREFIX = "ff44f54"
SELECTED = "empirical_relative@fixed365"
PRIMARY = "atlas_linear_regression"
B_STAR = "same_weekday_median_5w"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def full() -> dict[str, Any]:
    return read_json(FULL / "metrics.json")


@pytest.fixture(scope="module")
def selection_metrics() -> dict[str, Any]:
    return read_json(SELECTION / "metrics.json")


@pytest.mark.parametrize("directory", [SELECTION, FULL, PROJECTION])
def test_committed_files_match_their_manifest(directory: Path) -> None:
    manifest = read_json(directory / "manifest.json")
    kept = [name for name in manifest["outputs"] if (directory / name).is_file()]
    assert kept
    for name in kept:
        assert sha256(directory / name) == manifest["outputs"][name], name


@pytest.mark.parametrize("directory", [SELECTION, FULL])
def test_runs_used_the_frozen_protocol_on_clean_code(directory: Path) -> None:
    manifest = read_json(directory / "manifest.json")
    config = load_uncertainty_config(PROTOCOL)
    assert manifest["complete"] is True
    assert manifest["config_fingerprint"] == config.fingerprint()
    assert manifest["config"] == config.model_dump(mode="json")
    assert manifest["backtest_config_fingerprint"] == config.backtest_config_fingerprint
    assert manifest["git"]["dirty"] is False
    m2 = read_json(M2_FULL / "manifest.json")
    assert manifest["data"]["data_fingerprint"] == m2["data"]["data_fingerprint"]
    assert manifest["recommendation"]["recommended_model"] == PRIMARY
    assert manifest["recommendation"]["result_fingerprint"] == m2["result_fingerprint"]


def test_selection_ran_before_evaluation_and_was_reproduced(
    full: dict[str, Any], selection_metrics: dict[str, Any]
) -> None:
    selection_manifest = read_json(SELECTION / "manifest.json")
    full_manifest = read_json(FULL / "manifest.json")
    assert selection_manifest["periods_run"] == ["m3_selection"]
    assert full_manifest["periods_run"] == ["m3_selection", "m3_evaluation"]
    assert selection_manifest["git"]["commit"] == FREEZE_COMMIT
    assert full_manifest["git"]["commit"].startswith(SEAL_COMMIT_PREFIX)
    # Same code for both runs: only the curated seal was added in between.
    assert selection_manifest["code"]["code_sha256"] == full_manifest["code"]["code_sha256"]
    sealed = read_json(SELECTION / "selection.json")
    assert sealed["record_type"] == "sealed_interval_selection"
    assert sealed["result_fingerprint"] == selection_manifest["result_fingerprint"]
    assert full_manifest["sealed_selection"] == sealed
    assert sealed["selection"] == selection_metrics["selection"]
    assert full["selection"] == sealed["selection"]
    assert full["selection_seal_check"] == {"differences": []}
    assert full["decided_pair"] == SELECTED == sealed["selection"]["selected_pair"]
    # The selection run computed nothing on the evaluation period.
    assert {r["period"] for r in selection_metrics["interval_summary"]} == {"m3_selection"}
    assert selection_metrics["decision_status"] is None


@pytest.mark.parametrize("directory", [SELECTION, FULL])
def test_reports_render_from_their_metrics(directory: Path) -> None:
    metrics = read_json(directory / "metrics.json")
    assert (directory / "report.md").read_text(encoding="utf-8") == render_uncertainty_report(
        metrics
    )


def test_status_record_matches_the_metrics(full: dict[str, Any]) -> None:
    status = read_json(FULL / "calibration_status.json")
    manifest = read_json(FULL / "manifest.json")
    assert status["record_type"] == "calibration_status"
    assert status["result_fingerprint"] == manifest["result_fingerprint"]
    assert status["decision_status"] == full["decision_status"]
    assert status["decided_pair"] == SELECTED
    decision = full["decision_status"]
    assert decision["period"] == "m3_evaluation"
    assert decision["interval_status"] == "failed_calibration"
    assert decision["decision_grade"] is False
    levels = {lv["level"]: lv for lv in decision["by_model"][PRIMARY]["intervals"]["levels"]}
    assert levels[0.95]["scopes_outside_tolerance"] == ["days_8_14", "days_15_30"]
    assert all(not lv["evidence_sufficient"] for lv in levels.values())


def test_every_interval_was_issued_and_thin_ones_are_counted(full: dict[str, Any]) -> None:
    for row in full["interval_summary"]:
        assert row["n_unavailable"] == 0
        assert row["n_point_failed"] == 0
        assert row["n_missing_actual"] == 0
    thin = {
        (r["period"], r["model_id"], r["pair_id"]): r["n_thin"]
        for r in full["calibration_support"]
        if r["n_thin"]
    }
    # Only early selection-period sets of B* under the rolling scheme fall below an effective
    # sample size of 20; the selected method and the baselines are never thin.
    assert thin
    assert all(
        period == "m3_selection" and model == B_STAR and pair.endswith("@rolling365")
        for period, model, pair in thin
    )


def test_spot_check_intervals_respect_eligibility_and_ordering() -> None:
    spot = pd.read_csv(
        FULL / "spot_check_intervals.csv",
        parse_dates=[
            "origin",
            "calibration_origin",
            "cal_cutoff",
            "cal_last_target",
            "target_date",
        ],
    )
    model = spot[spot["model_id"] != "model_free"]
    assert (model["cal_last_target"] <= model["cal_cutoff"]).all()
    assert (model["cal_cutoff"] == model["calibration_origin"]).all()  # latency 0
    assert (model["calibration_origin"] <= model["origin"]).all()
    fixed = model[model["scheme_id"] == "fixed365"]
    starts = {"m3_selection": "2024-08-01", "m3_evaluation": "2025-06-30"}
    assert (fixed["calibration_origin"] == pd.to_datetime(fixed["period"].map(starts))).all()
    quantiles = spot[[c for c in spot.columns if c.startswith("q_")]].to_numpy()
    assert np.all(np.diff(quantiles, axis=1) >= 0)
    assert np.all(quantiles >= 0)


def test_projection_carries_the_m2_forecast_and_the_status() -> None:
    manifest = read_json(PROJECTION / "manifest.json")
    status = read_json(FULL / "calibration_status.json")
    assert manifest["record_type"] == "future_projection_intervals"
    assert manifest["calibration_status"] == status["decision_status"]["interval_status"]
    assert manifest["status_source"]["calibration_status_sha256"] == sha256(
        FULL / "calibration_status.json"
    )
    intervals = pd.read_csv(PROJECTION / "projection_intervals.csv", float_precision="round_trip")
    primary = intervals[(intervals["model_id"] == PRIMARY) & (intervals["pair_id"] == SELECTED)]
    m2 = pd.read_csv(M2_PROJECTION / "projections.csv", float_precision="round_trip")
    assert primary["prediction"].tolist() == m2["prediction"].tolist()
    assert (primary["status"] == "projected").all()
    quantiles = primary[[c for c in primary.columns if c.startswith("q_")]].to_numpy()
    assert np.all(np.diff(quantiles, axis=1) >= 0)
    exceedance = pd.read_csv(PROJECTION / "projection_exceedance.csv")
    hypothetical = exceedance[exceedance["threshold_provenance"] == "HYPOTHETICAL"]
    assert set(hypothetical["threshold_label"]) == {
        "hypothetical_capacity_12000",
        "hypothetical_capacity_13000",
    }
    historical = exceedance[exceedance["threshold_label"] == "trailing180_p75"]
    assert round(float(historical["threshold_value"].iloc[0])) == 11_609
    wide = exceedance.pivot_table(
        index=["model_id", "pair_id", "horizon"], columns="threshold_label", values="probability"
    )
    assert (wide["hypothetical_capacity_12000"] >= wide["hypothetical_capacity_13000"]).all()
    report = (PROJECTION / "report.md").read_text(encoding="utf-8")
    assert "Calibration status: FAILED_CALIBRATION" in report


# --- The documentation quotes the metrics ---------------------------------------------------


def _f(v: Any, d: int = 1) -> str:
    return "n/a" if v is None else f"{v:,.{d}f}"


def _pct(v: Any) -> str:
    return "n/a" if v is None else f"{100 * v:.1f} %"


def _p(v: Any) -> str:
    return "n/a" if v is None else ("<0.001" if v < 0.001 else f"{v:.3f}")


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    return "\n".join(lines + ["| " + " | ".join(r) + " |" for r in rows])


NAMES = {
    PRIMARY: "ATLAS regression",
    B_STAR: "same-weekday median 5w (B*)",
    "model_free": "none (model-free)",
}
BUCKET_NAMES = {
    "day_1": "day 1",
    "days_2_7": "days 2-7",
    "days_8_14": "days 8-14",
    "days_15_30": "days 15-30",
    "days_1_30": "days 1-30",
}


def _row(full: dict[str, Any], period: str, model: str, pair: str, label: str) -> dict[str, Any]:
    return next(
        r
        for r in full["interval_summary"]
        if (r["period"], r["model_id"], r["pair_id"], r["label"]) == (period, model, pair, label)
    )


def doc_tables(full: dict[str, Any], sealed: dict[str, Any]) -> dict[str, str]:
    """Every results table of docs/UNCERTAINTY.md, rendered from the committed metrics."""
    tables: dict[str, str] = {}
    tables["selection"] = _table(
        [
            "rank",
            "candidate",
            "mean WIS",
            "coverage 80 %",
            "coverage 95 %",
            "width 80 %",
            "width 95 %",
        ],
        [
            [
                str(i),
                f"`{r['pair_id']}`",
                _f(r["wis"]),
                _pct(r["coverage_80"]),
                _pct(r["coverage_95"]),
                _f(r["mean_width_80"], 0),
                _f(r["mean_width_95"], 0),
            ]
            for i, r in enumerate(sealed["selection"]["ranking"], 1)
        ],
    )
    levels = full["decision_status"]["by_model"][PRIMARY]["intervals"]["levels"]
    tables["status"] = _table(
        [
            "level",
            "coverage, days 1-30",
            "95 % HAC interval",
            "buckets outside tolerance",
            "p (coverage = level)",
            "eff. n",
        ],
        [
            [
                f"{lv['level'] * 100:g} %",
                _pct(lv["coverage"]),
                f"{100 * lv['coverage_ci_low']:.1f} to {100 * lv['coverage_ci_high']:.1f} %",
                ", ".join(lv["scopes_outside_tolerance"]) or "none",
                _p(lv["p_value_conservative"]),
                _f(lv["effective_sample_size"]),
            ]
            for lv in levels
        ],
    )
    for period in ("m3_evaluation", "m3_selection"):
        rows = []
        for label, name in BUCKET_NAMES.items():
            r = _row(full, period, PRIMARY, SELECTED, label)
            rows.append(
                [
                    name,
                    _f(r["n_evaluated"], 0),
                    _pct(r["coverage_80"]),
                    _pct(r["coverage_95"]),
                    _f(r["mean_width_80"], 0),
                    _f(r["mean_width_95"], 0),
                    _f(r["interval_score_95"], 0),
                    _f(r["wis"]),
                    _f(r["coverage_eff_n_95"]),
                ]
            )
        tables[f"buckets_{period}"] = _table(
            [
                "horizons",
                "forecasts",
                "coverage 80 %",
                "coverage 95 %",
                "width 80 %",
                "width 95 %",
                "Winkler 95 %",
                "WIS",
                "eff. n (95 %)",
            ],
            rows,
        )
    gaussian = "gaussian_one_step@rolling365"
    tables["baselines"] = _table(
        ["point model", "method", "coverage 80 %", "coverage 95 %", "width 95 %", "WIS"],
        [
            [
                NAMES[model],
                f"`{pair}`",
                _pct(_row(full, "m3_evaluation", model, pair, "days_1_30")["coverage_80"]),
                _pct(_row(full, "m3_evaluation", model, pair, "days_1_30")["coverage_95"]),
                _f(_row(full, "m3_evaluation", model, pair, "days_1_30")["mean_width_95"], 0),
                _f(_row(full, "m3_evaluation", model, pair, "days_1_30")["wis"]),
            ]
            for model, pair in [
                (PRIMARY, SELECTED),
                (PRIMARY, gaussian),
                (B_STAR, SELECTED),
                (B_STAR, gaussian),
                ("model_free", "climatology_180d"),
            ]
        ],
    )
    tables["comparisons"] = _table(
        [
            "period",
            "point model",
            "reference",
            "WIS",
            "reference WIS",
            "skill",
            "WIS difference, 95 % CI",
            "p",
            "eff. n",
        ],
        [
            [
                r["period"].removeprefix("m3_"),
                NAMES[r["model_id"]],
                f"{NAMES[r['reference_model']]} `{r['reference_pair']}`",
                _f(r["wis"]),
                _f(r["wis_reference"]),
                _f(r["wis_skill_pct"]) + " %",
                f"{_f(r['wis_difference_ci_low'])} to {_f(r['wis_difference_ci_high'])}",
                _p(r["wis_p_conservative"]),
                _f(r["wis_eff_n"]),
            ]
            for r in full["comparisons"]
            if r["label"] == "days_1_30" and r["pair_id"] == SELECTED
        ],
    )
    statuses = {
        e["threshold_label"]: e["status"]
        for e in full["decision_status"]["by_model"][PRIMARY]["exceedance"]
    }
    rows = []
    reliability: list[dict[str, Any]] = []
    for r in full["exceedance_summary"]:
        if (r["period"], r["label"], r["model_id"], r["pair_id"]) != (
            "m3_evaluation",
            "days_1_30",
            PRIMARY,
            SELECTED,
        ):
            continue
        hypothetical = " (HYPOTHETICAL)" if r["threshold_kind"] == "hypothetical" else ""
        rows.append(
            [
                f"`{r['threshold_label']}`{hypothetical}",
                _f(r["mean_threshold"], 0),
                f"{r['n_events']:,}",
                _f(r["mean_probability"], 3),
                _f(r["event_frequency"], 3),
                _f(r["brier"], 4),
                _f(r["brier_reference"], 4),
                _f(r["brier_skill_score"], 3),
                _p(r["brier_p_conservative"]),
                _f(r["brier_eff_n"]),
                statuses[r["threshold_label"]],
            ]
        )
        if r["threshold_label"] == "trailing180_p75":
            reliability = r["reliability"]
    tables["exceedance"] = _table(
        [
            "threshold",
            "mean threshold",
            "events",
            "mean p",
            "observed freq.",
            "Brier",
            "Brier climatology",
            "skill",
            "p",
            "eff. n",
            "status",
        ],
        rows,
    )
    tables["reliability"] = _table(
        ["probability bin", "forecasts", "mean p", "observed freq."],
        [
            [
                f"{b['bin_low']:.1f}-{b['bin_high']:.1f}",
                f"{b['n']:,}",
                _f(b["mean_probability"], 3),
                _f(b["observed_frequency"], 3),
            ]
            for b in reliability
        ],
    )
    tables["strata"] = _table(
        [
            "target days",
            "forecasts",
            "distinct days",
            "coverage 80 %",
            "coverage 95 %",
            "width 95 %",
            "MAE",
            "bias",
            "Brier (P75)",
        ],
        [
            [
                r["stratum"].replace("_", "-"),
                f"{r['n_evaluated']:,}",
                str(r["n_target_days"]),
                _pct(r["coverage_80"]),
                _pct(r["coverage_95"]),
                _f(r["mean_width_95"], 0),
                _f(r["mae"]),
                _f(r["bias"]),
                _f(r.get("brier_historical"), 4),
            ]
            for r in full["strata"]
            if (r["period"], r["model_id"], r["pair_id"]) == ("m3_evaluation", PRIMARY, SELECTED)
        ],
    )
    tables["candidates"] = _table(
        ["method", "coverage 80 %", "coverage 95 %", "width 95 %", "WIS"],
        [
            [
                f"`{pair}`",
                _pct(_row(full, "m3_evaluation", PRIMARY, pair, "days_1_30")["coverage_80"]),
                _pct(_row(full, "m3_evaluation", PRIMARY, pair, "days_1_30")["coverage_95"]),
                _f(_row(full, "m3_evaluation", PRIMARY, pair, "days_1_30")["mean_width_95"], 0),
                _f(_row(full, "m3_evaluation", PRIMARY, pair, "days_1_30")["wis"]),
            ]
            for pair in [*full["protocol"]["candidates"], gaussian]
        ],
    )
    return tables


def test_documentation_tables_are_generated_from_the_metrics(full: dict[str, Any]) -> None:
    text = DOC.read_text(encoding="utf-8")
    sealed = read_json(SELECTION / "selection.json")
    for name, table in doc_tables(full, sealed).items():
        assert table in text, f"table {name} in docs/UNCERTAINTY.md differs from metrics.json"


def test_documentation_states_the_status_and_fingerprints(full: dict[str, Any]) -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "FAILED_CALIBRATION" in text
    assert read_json(FULL / "manifest.json")["result_fingerprint"] in text
    assert read_json(SELECTION / "manifest.json")["result_fingerprint"] in text
    assert load_uncertainty_config(PROTOCOL).fingerprint() in text
    assert f"`{SELECTED}`" in text
