"""M3 configuration rules, the ties to the M2 protocol, and the fail-closed commands.

The command tests use ``configs/uncertainty/smoke.json``, whose origins and targets all lie
in early 2024 (M2 development era): they never compute anything on the M3 evaluation
period of the protocol.
"""

from __future__ import annotations

import io
import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from atlas.backtest import BacktestPlan, Period, PeriodRole, run_backtest
from atlas.cli import main
from atlas.config import load_settings
from atlas.errors import ConfigError
from atlas.experiment import load_config
from atlas.models import ModelFamily
from atlas.runner import load_series
from atlas.uncertainty_config import (
    central_bounds,
    check_against_backtest,
    level_index,
    load_uncertainty_config,
    parse_uncertainty_config,
)
from atlas.uncertainty_runner import (
    load_context,
    point_models,
    recommendation_provenance,
    residual_plan,
)
from tests.conftest import REPO_ROOT
from tests.m3_helpers import BASE_CONFIG, config_dict, make_config
from tests.numeric import assert_matches_reference

PROTOCOL = REPO_ROOT / "configs" / "uncertainty" / "atlas-m3-intervals-v1.json"
SMOKE = REPO_ROOT / "configs" / "uncertainty" / "smoke.json"
M2_PROTOCOL = REPO_ROOT / "configs" / "backtest" / "atlas-m2-rolling-origin-v1.json"
M2_DELAY = REPO_ROOT / "configs" / "backtest" / "atlas-m2-rolling-origin-v1-delay1.json"
SPOT_CHECK = (
    REPO_ROOT / "docs" / "results" / "atlas-m2-rolling-origin-v1" / "full" / "spot_check.csv"
)
M2_PROJECTION = (
    REPO_ROOT / "docs" / "results" / "atlas-m2-rolling-origin-v1" / "projection" / "projections.csv"
)
PROTOCOL_FINGERPRINT = "5afd32068ebc63b5838cfea545326916f0c4dd835ed294a301acd89d8622244b"


def run_cli(*args: str) -> tuple[int, str]:
    buffer = io.StringIO()
    code = main(["--repo-root", str(REPO_ROOT), "--format", "json", *args], stdout=buffer)
    return code, buffer.getvalue()


def report_of(output: str, name: str) -> dict[str, Any]:
    payload = json.loads(output)
    return next(r for r in payload["reports"] if r["name"] == name)


# --- Configuration ------------------------------------------------------------------------


def test_protocol_configuration_is_valid_and_pinned() -> None:
    config = load_uncertainty_config(PROTOCOL)
    assert config.primary_model == "atlas_linear_regression"
    assert config.calibration.data_latency_days == 0
    assert [p.role for p in config.periods] == ["selection", "evaluation"]
    hypothetical = [t for t in config.thresholds if t.kind == "hypothetical"]
    assert hypothetical
    assert all(t.provenance == "HYPOTHETICAL" for t in hypothetical)
    backtest = load_config(M2_PROTOCOL)
    check_against_backtest(config, backtest)
    # Pinned when the protocol was frozen: any change is a new experiment id.
    assert config.fingerprint() == PROTOCOL_FINGERPRINT


def test_fingerprint_ignores_formatting_but_not_values() -> None:
    config = make_config()
    text = json.dumps(BASE_CONFIG, indent=4)
    assert parse_uncertainty_config(text).fingerprint() == config.fingerprint()
    changed = config_dict(levels=[0.5, 0.95], quantile_levels=[0.025, 0.25, 0.5, 0.75, 0.975])
    changed["decision_criteria"]["coverage"][0]["level"] = 0.5
    assert parse_uncertainty_config(changed).fingerprint() != config.fingerprint()


def threshold(**changes: Any) -> dict[str, Any]:
    base = dict(BASE_CONFIG["thresholds"][1])
    base.update(changes)
    return base


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"thresholds": [threshold(provenance="VERIFIED")]}, "provenance"),
        ({"thresholds": [threshold(provenance=None)]}, "provenance"),
        ({"thresholds": [threshold(units="requests")]}, "units"),
        ({"thresholds": [threshold(assumptions="short")]}, "assumptions"),
        ({"thresholds": [threshold(value=-5)]}, "value"),
        ({"thresholds": [threshold(), threshold()]}, "labels must be unique"),
        ({"quantile_levels": [0.1, 0.25, 0.5, 0.75, 0.9]}, "must contain 0.025"),
        ({"quantile_levels": [0.025, 0.1, 0.25, 0.75, 0.9, 0.975]}, "median"),
        ({"quantile_levels": [0.1, 0.025, 0.5, 0.9, 0.975]}, "strictly increasing"),
        ({"quantile_levels": [0.0, 0.025, 0.5, 0.9, 0.975]}, "strictly between"),
        ({"levels": [0.95, 0.8]}, "strictly increasing"),
        ({"levels": [0.8, 1.0]}, "strictly between"),
        ({"primary_model": "other"}, "primary_model"),
        ({"point_models": ["med", "med"], "primary_model": "med"}, "distinct"),
        ({"candidates": [{"method": "gaussian_one_step", "scheme": "rolling90"}]}, "candidate"),
        ({"candidates": [{"method": "empirical_additive", "scheme": "weekly"}]}, "unknown scheme"),
        ({"baselines": [{"method": "climatology_180d", "scheme": "rolling90"}]}, "no scheme"),
        ({"baselines": [{"method": "gaussian_one_step", "scheme": "x"}]}, "unknown scheme"),
        ({"baselines": [{"method": "empirical_additive", "scheme": "rolling90"}]}, "baseline type"),
        ({"residual_last_origin": "2023-01-01"}, "precedes"),
        ({"sensitivity": {"latency_days": [-1]}}, "shorter"),
        ({"description": "x", "unexpected": 1}, "Extra inputs"),
    ],
)
def test_invalid_configurations_are_rejected(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        make_config(**changes)


def test_period_and_selection_rules() -> None:
    periods = BASE_CONFIG["periods"]
    overlapping = [periods[0], dict(periods[1], first_origin="2023-11-03")]
    with pytest.raises(ConfigError, match="overlap"):
        make_config(periods=overlapping)
    with pytest.raises(ConfigError, match="outside the residual span"):
        make_config(periods=[dict(periods[0], first_origin="2023-01-01")])
    with pytest.raises(ConfigError, match="at most one selection"):
        make_config(periods=[periods[0], dict(periods[0], name="sel2")], selection=None)
    with pytest.raises(ConfigError, match="last_origin precedes"):
        make_config(periods=[dict(periods[0], last_origin="2023-06-01")], selection=None)
    with pytest.raises(ConfigError, match="unique"):
        make_config(periods=[periods[0], dict(periods[1], name="sel")])
    rule = BASE_CONFIG["selection"]
    with pytest.raises(ConfigError, match=r"selection\.period"):
        make_config(selection=dict(rule, period="ev"))
    with pytest.raises(ConfigError, match="point_model"):
        make_config(selection=dict(rule, point_model="sn"))
    with pytest.raises(ConfigError, match="horizons"):
        make_config(selection=dict(rule, last_horizon=8))


def test_criteria_rules() -> None:
    criteria = BASE_CONFIG["decision_criteria"]
    with pytest.raises(ConfigError, match="one tolerance per level"):
        make_config(decision_criteria=dict(criteria, coverage=criteria["coverage"][:1]))
    with pytest.raises(ConfigError, match="climatology baseline"):
        make_config(decision_criteria=dict(criteria, exceedance_reference="gaussian_one_step"))
    config = make_config()
    assert config.tolerance_pct(0.95) == 3.0
    assert central_bounds(0.8) == pytest.approx((0.1, 0.9))
    assert level_index(config.quantile_levels, 0.975) == 6
    with pytest.raises(ConfigError, match="not in the grid"):
        level_index(config.quantile_levels, 0.3)
    with pytest.raises(ConfigError, match="unknown period"):
        config.period("nope")
    with pytest.raises(ConfigError, match="unknown candidate"):
        config.pair("nope")


def test_duplicate_keys_and_bad_json_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="duplicate"):
        parse_uncertainty_config('{"schema_version": 1, "schema_version": 1}')
    with pytest.raises(ConfigError, match="not valid JSON"):
        parse_uncertainty_config("{")
    with pytest.raises(ConfigError, match="cannot read"):
        load_uncertainty_config(tmp_path / "missing.json")


def protocol_with(**changes: Any) -> Any:
    payload = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    payload.update(changes)
    return parse_uncertainty_config(payload)


def test_ties_to_the_m2_protocol() -> None:
    backtest = load_config(M2_PROTOCOL)
    with pytest.raises(ConfigError, match="pinned"):
        check_against_backtest(protocol_with(backtest_config_fingerprint="1" * 64), backtest)
    with pytest.raises(ConfigError, match="not configured"):
        check_against_backtest(
            protocol_with(point_models=["atlas_linear_regression", "ghost"]), backtest
        )
    payload = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    periods = payload["periods"]
    late_selection = [dict(periods[0], last_origin="2025-06-10"), dict(periods[1])]
    with pytest.raises(ConfigError, match=r"overlap|selection period"):
        check_against_backtest(protocol_with(periods=late_selection), backtest)
    early_evaluation = [
        dict(periods[0], last_origin="2025-03-31"),
        dict(periods[1], first_origin="2025-06-01"),
    ]
    with pytest.raises(ConfigError, match="confirmation origins"):
        check_against_backtest(protocol_with(periods=early_evaluation), backtest)
    early_residuals = protocol_with(residual_first_origin="2024-01-01")
    with pytest.raises(ConfigError, match="residual_first_origin"):
        check_against_backtest(early_residuals, backtest)
    shorter = protocol_with(
        max_horizon=14,
        selection=dict(payload["selection"], last_horizon=14),
    )
    with pytest.raises(ConfigError, match="max_horizon"):
        check_against_backtest(shorter, backtest)
    delayed = load_config(M2_DELAY)
    with pytest.raises(ConfigError, match="data delay"):
        check_against_backtest(
            protocol_with(backtest_config_fingerprint=delayed.fingerprint()), delayed
        )


# --- Integration with M2 ----------------------------------------------------------------------


def compare_with_spot_check(spot_path: Path) -> None:
    """Recompute three M2 spot-check origins with the M3 residual plan and compare."""
    settings = load_settings(repo_root=REPO_ROOT)
    context = load_context(settings, PROTOCOL)
    _, data = load_series(settings, "test")
    assert data is not None
    origins = [date(2024, 2, 1), date(2025, 5, 31), date(2026, 5, 31)]
    template = residual_plan(context)
    plan = BacktestPlan(
        tuple(
            Period(f"o{k}", PeriodRole.DESCRIPTIVE, o, o, 1, True) for k, o in enumerate(origins)
        ),
        max_horizon=template.max_horizon,
        min_history_days=template.min_history_days,
        training_window=template.training_window,
        data_delay_days=template.data_delay_days,
    )
    models = point_models(context)
    result = run_backtest(data.series, plan, models)
    spot = pd.read_csv(spot_path, parse_dates=["origin"], float_precision="round_trip")
    for model in models:
        model_id = model.spec.model_id
        for origin in origins:
            mine = result.records[
                (result.records["model_id"] == model_id)
                & (result.records["origin"] == pd.Timestamp(origin))
            ].sort_values("horizon")
            theirs = spot[(spot["model_id"] == model_id) & (spot["origin"] == pd.Timestamp(origin))]
            theirs = theirs.drop_duplicates("horizon").sort_values("horizon")
            assert mine["horizon"].tolist() == theirs["horizon"].tolist()
            assert mine["status"].tolist() == theirs["status"].tolist()
            # The reference was computed on another CPU: least squares may differ in the
            # last bits there (tests/numeric.py); the baselines must match exactly.
            assert_matches_reference(
                mine["prediction"],
                theirs["prediction"],
                uses_blas=model.spec.family is ModelFamily.REGRESSION,
                what=f"{model_id} at {origin}",
            )


@pytest.mark.integration
def test_residual_backtest_reproduces_the_m2_spot_check() -> None:
    compare_with_spot_check(SPOT_CHECK)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("model_id", "error"),
    [
        ("atlas_linear_regression", 0.1),  # a tenth of a request: rejected despite the tolerance
        ("atlas_linear_regression", -25.0),
        ("same_weekday_median_5w", 1.0),  # no BLAS: any difference is rejected
    ],
)
def test_spot_check_rejects_a_materially_different_reference(
    tmp_path: Path, model_id: str, error: float
) -> None:
    spot = pd.read_csv(SPOT_CHECK, float_precision="round_trip")
    hit = (spot["model_id"] == model_id) & (spot["origin"] == "2025-05-31") & (spot["horizon"] == 9)
    assert hit.any()
    spot.loc[hit, "prediction"] = spot.loc[hit, "prediction"] + error
    edited = tmp_path / "spot_check.csv"
    spot.to_csv(edited, index=False, float_format="%.17g")
    with pytest.raises(AssertionError, match=model_id):
        compare_with_spot_check(edited)


@pytest.mark.integration
def test_primary_model_must_be_the_m2_recommendation(tmp_path: Path) -> None:
    settings = load_settings(repo_root=REPO_ROOT)
    _, data = load_series(settings, "test")
    assert data is not None
    context = load_context(settings, PROTOCOL)
    provenance = recommendation_provenance(settings, context, data)
    assert provenance is not None
    assert provenance["recommended_model"] == "atlas_linear_regression"
    payload = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    payload["primary_model"] = "same_weekday_median_5w"
    payload["selection"]["point_model"] = "same_weekday_median_5w"
    path = tmp_path / "swapped.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="not the M2 recommendation"):
        recommendation_provenance(settings, load_context(settings, path), data)
    payload["recommendation"] = "../outside.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="inside the repository"):
        recommendation_provenance(settings, load_context(settings, path), data)


@pytest.mark.integration
def test_historical_threshold_reproduces_the_legacy_planning_percentile() -> None:
    from atlas.exceedance import historical_threshold

    settings = load_settings(repo_root=REPO_ROOT)
    _, data = load_series(settings, "test")
    assert data is not None
    config = load_uncertainty_config(PROTOCOL)
    value = historical_threshold(config.historical_thresholds()[0], data.series, data.series.end)
    assert value is not None
    assert round(value) == 11_609  # the legacy "planning capacity" of 2026-06-30


# --- Commands on the smoke configuration -------------------------------------------------------


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    base = tmp_path_factory.mktemp("m3")
    selection = base / "selection"
    code, output = run_cli(
        "uncertainty", "run", "--config", str(SMOKE), "--output-dir", str(selection)
    )
    assert code == 0, output
    full = base / "full"
    code, output = run_cli(
        "uncertainty",
        "run",
        "--config",
        str(SMOKE),
        "--period",
        "smoke_selection",
        "--period",
        "smoke_evaluation",
        "--sealed-selection",
        str(selection / "selection.json"),
        "--output-dir",
        str(full),
    )
    assert code == 0, output
    return {"base": base, "selection": selection, "full": full}


@pytest.mark.integration
def test_selection_run_writes_a_seal_and_no_status(runs: dict[str, Path]) -> None:
    directory = runs["selection"]
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["record_type"] == "historical_probabilistic_backtest"
    assert manifest["complete"] is True
    assert manifest["periods_run"] == ["smoke_selection"]
    sealed = json.loads((directory / "selection.json").read_text(encoding="utf-8"))
    assert sealed["record_type"] == "sealed_interval_selection"
    assert sealed["selection"]["period"] == "smoke_selection"
    assert not (directory / "calibration_status.json").exists()
    metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["decision_status"] is None
    intervals = pd.read_csv(directory / "intervals.csv")
    assert set(intervals["period"]) == {"smoke_selection"}
    assert (intervals["pooling"] == "bucket").any()  # long horizons were pooled
    from atlas.uncertainty_report import render_uncertainty_report

    assert (directory / "report.md").read_text(encoding="utf-8") == render_uncertainty_report(
        metrics
    )


@pytest.mark.integration
def test_evaluation_needs_a_seal_and_the_selection_period(runs: dict[str, Path]) -> None:
    code, _ = run_cli("uncertainty", "run", "--config", str(SMOKE), "--period", "smoke_evaluation")
    assert code == 2
    code, _ = run_cli(
        "uncertainty",
        "run",
        "--config",
        str(SMOKE),
        "--period",
        "smoke_evaluation",
        "--sealed-selection",
        str(runs["selection"] / "selection.json"),
    )
    assert code == 2
    code, _ = run_cli("uncertainty", "run", "--config", str(SMOKE), "--period", "nope")
    assert code == 2
    code, _ = run_cli(
        "uncertainty", "run", "--config", str(SMOKE), "--output-dir", str(runs["selection"])
    )
    assert code == 2  # existing output directory, refused before any computation


@pytest.mark.integration
def test_seal_of_another_configuration_is_refused(runs: dict[str, Path]) -> None:
    sealed = json.loads((runs["selection"] / "selection.json").read_text(encoding="utf-8"))
    for key, value, message in (
        ("config_fingerprint", "0" * 64, "different configuration"),
        ("data_fingerprint", "0" * 64, "different data"),
        ("record_type", "sealed_selection", "not a sealed interval selection"),
    ):
        edited = runs["base"] / f"seal-{key}.json"
        edited.write_text(json.dumps({**sealed, key: value}), encoding="utf-8")
        code, _ = run_cli(
            "uncertainty",
            "run",
            "--config",
            str(SMOKE),
            "--period",
            "smoke_selection",
            "--period",
            "smoke_evaluation",
            "--sealed-selection",
            str(edited),
        )
        assert code == 2, message


@pytest.mark.integration
def test_full_run_writes_a_status_and_verifies(runs: dict[str, Path]) -> None:
    directory = runs["full"]
    status = json.loads((directory / "calibration_status.json").read_text(encoding="utf-8"))
    assert status["record_type"] == "calibration_status"
    assert status["decision_status"]["period"] == "smoke_evaluation"
    assert status["decision_status"]["interval_status"] != "decision_grade"  # 31 origins
    metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["selection_seal_check"] == {"differences": []}
    code, output = run_cli("uncertainty", "verify", str(directory))
    assert code == 0, output
    assert report_of(output, "uncertainty_verification")["facts"]["identical"] is True


@pytest.mark.integration
def test_verify_and_inspect_detect_altered_files(runs: dict[str, Path]) -> None:
    copy = runs["base"] / "tampered"
    shutil.copytree(runs["full"], copy)
    report = copy / "report.md"
    report.write_text(report.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
    code, output = run_cli("uncertainty", "inspect", str(copy))
    assert code == 1
    assert report_of(output, "uncertainty_inspection")["facts"]["altered_on_disk"] == ["report.md"]
    (copy / "intervals.csv").unlink()
    (copy / "exceedance.csv").unlink()
    report.write_bytes((runs["full"] / "report.md").read_bytes())
    code, output = run_cli("uncertainty", "inspect", str(copy))
    assert code == 0, output
    facts = report_of(output, "uncertainty_inspection")["facts"]
    assert facts["files_absent"] == ["exceedance.csv", "intervals.csv"]
    assert facts["selected_pair"]
    buffer = io.StringIO()
    assert (
        main(["--repo-root", str(REPO_ROOT), "uncertainty", "inspect", str(copy)], stdout=buffer)
        == 0
    )
    assert "evaluation status" in buffer.getvalue()


@pytest.mark.integration
def test_edited_seal_marks_the_run_incomplete(runs: dict[str, Path]) -> None:
    sealed = json.loads((runs["selection"] / "selection.json").read_text(encoding="utf-8"))
    ranking = sealed["selection"]["ranking"]
    other = ranking[1]["pair_id"]
    sealed["selection"]["selected_pair"] = other
    edited = runs["base"] / "edited-seal.json"
    edited.write_text(json.dumps(sealed), encoding="utf-8")
    directory = runs["base"] / "edited"
    code, output = run_cli(
        "uncertainty",
        "run",
        "--config",
        str(SMOKE),
        "--period",
        "smoke_selection",
        "--period",
        "smoke_evaluation",
        "--sealed-selection",
        str(edited),
        "--output-dir",
        str(directory),
    )
    assert code == 1
    issues = {i["code"] for i in report_of(output, "uncertainty")["issues"]}
    assert "selection_differs_from_seal" in issues
    assert not (directory / "calibration_status.json").exists()
    # No status is judged from a run that did not reproduce its seal.
    metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["decision_status"] is None
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["complete"] is False
    code, _ = run_cli("uncertainty", "project", "--status", str(runs["full"] / "manifest.json"))
    assert code == 2


@pytest.mark.integration
def test_projection_uses_the_m2_point_forecasts_and_labels_the_status(
    runs: dict[str, Path],
) -> None:
    directory = runs["base"] / "projection"
    code, output = run_cli(
        "uncertainty",
        "project",
        "--status",
        str(runs["full"] / "calibration_status.json"),
        "--output-dir",
        str(directory),
    )
    assert code == 0, output
    facts = report_of(output, "uncertainty_projection")["facts"]
    assert facts["origin"] == "2026-06-30"
    intervals = pd.read_csv(directory / "projection_intervals.csv")
    assert set(intervals["record_type"]) == {"future_projection_intervals"}
    assert "actual" not in intervals.columns
    m2 = pd.read_csv(M2_PROJECTION, float_precision="round_trip")
    primary = intervals[
        (intervals["model_id"] == "atlas_linear_regression")
        & (intervals["pair_id"] == facts["decided_pair"])
    ]
    # Same code path as `atlas forecast`. The committed projection was computed on another
    # machine, so BLAS may differ in the last bits (tests/numeric.py).
    assert_matches_reference(
        primary["prediction"], m2["prediction"], uses_blas=True, what="projection"
    )
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["calibration_status"] == facts["calibration_status"]
    report = (directory / "report.md").read_text(encoding="utf-8")
    assert "FUTURE PROJECTION WITH INTERVALS" in report
    assert "not a verified NYC agency service capacity" in report
    # The smoke residuals end in 2024, so nothing can be calibrated at 2026-06-30: the
    # intervals are reported as unavailable rather than invented.
    assert (primary["status"] == "unavailable").all()
    tampered = runs["base"] / "status-copy"
    shutil.copytree(runs["full"], tampered)
    status_file = tampered / "calibration_status.json"
    status = json.loads(status_file.read_text(encoding="utf-8"))
    status["decision_status"]["interval_status"] = "decision_grade"
    status_file.write_text(json.dumps(status), encoding="utf-8")
    code, _ = run_cli("uncertainty", "project", "--status", str(status_file))
    assert code == 2


def _rehash(directory: Path, name: str) -> None:
    """Edit-and-re-hash: update manifest.json so that it records the edited file."""
    import hashlib

    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["outputs"][name] = hashlib.sha256((directory / name).read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.integration
def test_forged_status_fails_verify_and_project(runs: dict[str, Path]) -> None:
    forged = runs["base"] / "forged"
    shutil.copytree(runs["full"], forged)
    status_file = forged / "calibration_status.json"
    status = json.loads(status_file.read_text(encoding="utf-8"))
    status["decision_status"]["interval_status"] = "decision_grade"
    status["decision_status"]["decision_grade"] = True
    status_file.write_text(json.dumps(status), encoding="utf-8")
    _rehash(forged, "calibration_status.json")
    code, output = run_cli("uncertainty", "inspect", str(forged))
    assert code == 0  # the manifest is not signed; inspect only checks hashes
    code, output = run_cli("uncertainty", "verify", str(forged))
    assert code == 1
    facts = report_of(output, "uncertainty_verification")["facts"]
    assert facts["files"]["calibration_status.json"] == "different"
    code, _ = run_cli("uncertainty", "project", "--status", str(status_file))
    assert code == 2


@pytest.mark.integration
def test_projection_refuses_a_status_from_an_incomplete_or_other_run(
    runs: dict[str, Path],
) -> None:
    incomplete = runs["base"] / "incomplete-with-status"
    shutil.copytree(runs["full"], incomplete)
    manifest_path = incomplete / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["complete"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    code, output = run_cli(
        "uncertainty", "project", "--status", str(incomplete / "calibration_status.json")
    )
    assert code == 2
    code, output = run_cli("uncertainty", "inspect", str(incomplete))
    assert code == 1
    issues = {i["code"] for i in report_of(output, "uncertainty_inspection")["issues"]}
    assert "run_incomplete" in issues
    other = runs["base"] / "other-fingerprint"
    shutil.copytree(runs["full"], other)
    status_file = other / "calibration_status.json"
    status = json.loads(status_file.read_text(encoding="utf-8"))
    status["result_fingerprint"] = "0" * 64
    status_file.write_text(json.dumps(status), encoding="utf-8")
    _rehash(other, "calibration_status.json")
    code, _ = run_cli("uncertainty", "project", "--status", str(status_file))
    assert code == 2
