"""M4 runner, report and CLI: files, labels, wording, verify/inspect and fail-closed paths.

Every command test uses ``configs/optimization/smoke.json`` (10-day horizon). The smoke run
is computed once per module and reused; tests that need a modified run work on a copy.
The frozen reference configuration is never run here.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import json
import math
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import scipy
from hypothesis import assume, given, settings
from hypothesis import strategies as st

import atlas.optimizer
from atlas.cli import EXIT_FAILED, EXIT_OK, EXIT_USAGE, main
from atlas.config import Settings
from atlas.experiment import result_fingerprint
from atlas.optimization_config import load_optimization_config
from atlas.optimization_report import (
    NOT_A_RECOMMENDATION,
    _difference,
    _n,
    render_optimization_report,
)
from atlas.optimization_runner import (
    LABEL,
    LABEL_COLUMNS,
    RECORD_TYPE_DECISION_SIMULATION,
    RESULT_FILES,
    STRUCTURAL_ASSUMPTIONS,
    load_inputs,
    m4_outputs,
)
from atlas.runner import load_series
from tests.conftest import REPO_ROOT

SMOKE = REPO_ROOT / "configs" / "optimization" / "smoke.json"
M2_PROJECTION = (
    REPO_ROOT / "docs" / "results" / "atlas-m2-rolling-origin-v1" / "projection" / "projections.csv"
)
M3_STATUS = (
    REPO_ROOT / "docs" / "results" / "atlas-m3-intervals-v1" / "full" / "calibration_status.json"
)
M3_RUN = REPO_ROOT / "docs" / "results" / "atlas-m3-intervals-v1" / "full"
DAILY_TOTAL = REPO_ROOT / "01 Data" / "daily_total.csv"
CSV_FILES = ("scenarios.csv", "plans.csv", "duals.csv", "sensitivity.csv", "frontier.csv")
ALL_FILES = (*RESULT_FILES, "report.md", "config.json")
SENTENCE = (
    "Nothing in this report is a validated staffing, budget or service-capacity recommendation"
)
FD_STATUSES = {"locally_unique", "breakpoint", "domain_boundary_below", "infeasible_side"}

# Smoke configuration values, read by hand from configs/optimization/smoke.json.
INITIAL_BACKLOG = 2000.0
REGULAR_CAPACITY = 4300 * 2.4  # regular hours x regular productivity = 10,320 requests/day
TERMINAL_MAX = 3000.0
BUDGET = 200_000.0
OVERTIME_WAGE = 57.0
OVERTIME_PRODUCTIVITY = 2.2
SURGE_PRICE = 28.0
HORIZON = 10


def run_cli(*args: str, output_format: str = "json") -> tuple[int, str]:
    buffer = io.StringIO()
    code = main(["--repo-root", str(REPO_ROOT), "--format", output_format, *args], stdout=buffer)
    return code, buffer.getvalue()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def rows_of_bytes(content: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content.decode("utf-8"))))


def optimization_report(output: str, name: str = "optimization") -> dict[str, Any]:
    payload = json.loads(output)
    return next(r for r in payload["reports"] if r["name"] == name)


def issue_codes(report: dict[str, Any], severity: str | None = None) -> list[str]:
    return [i["code"] for i in report["issues"] if severity is None or i["severity"] == severity]


@dataclass(frozen=True)
class SmokeRun:
    code: int
    output: str
    directory: Path

    def rows(self, name: str) -> list[dict[str, str]]:
        return read_rows(self.directory / name)

    def manifest(self) -> dict[str, Any]:
        return json.loads((self.directory / "manifest.json").read_text(encoding="utf-8"))

    def metrics(self) -> dict[str, Any]:
        return json.loads((self.directory / "metrics.json").read_text(encoding="utf-8"))

    def report(self) -> str:
        return (self.directory / "report.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def smoke_run(tmp_path_factory: pytest.TempPathFactory) -> SmokeRun:
    directory = tmp_path_factory.mktemp("m4") / "smoke"
    code, output = run_cli(
        "optimize", "run", "--config", str(SMOKE), "--output-dir", str(directory)
    )
    return SmokeRun(code, output, directory)


def copy_run(run: SmokeRun, target: Path) -> Path:
    shutil.copytree(run.directory, target)
    return target


# --- 1. Files and manifest -------------------------------------------------------------------


@pytest.mark.integration
def test_smoke_run_exits_zero_and_writes_every_file(smoke_run: SmokeRun) -> None:
    assert smoke_run.code == EXIT_OK
    written = {p.name for p in smoke_run.directory.iterdir()}
    assert written == {"manifest.json", *ALL_FILES}
    report = optimization_report(smoke_run.output)
    assert report["ok"] is True
    assert issue_codes(report, "error") == []
    assert report["facts"]["complete"] is True


@pytest.mark.integration
def test_manifest_output_hashes_match_files_and_give_the_result_fingerprint(
    smoke_run: SmokeRun,
) -> None:
    manifest = smoke_run.manifest()
    outputs = manifest["outputs"]
    assert set(outputs) == set(ALL_FILES)
    for name, digest in outputs.items():
        assert sha256(smoke_run.directory / name) == digest, name
    assert manifest["result_files"] == list(RESULT_FILES)
    assert manifest["result_fingerprint"] == result_fingerprint(outputs, RESULT_FILES)
    # Independent recomputation: SHA-256 of "name:sha\n" lines in RESULT_FILES order.
    joined = "".join(f"{n}:{sha256(smoke_run.directory / n)}\n" for n in RESULT_FILES)
    assert manifest["result_fingerprint"] == hashlib.sha256(joined.encode()).hexdigest()
    assert manifest["complete"] is True
    assert manifest["record_type"] == RECORD_TYPE_DECISION_SIMULATION
    assert manifest["label"] == LABEL
    assert manifest["validated_recommendation"] is False
    assert manifest["structural_assumptions"] == list(STRUCTURAL_ASSUMPTIONS)
    facts = optimization_report(smoke_run.output)["facts"]
    assert facts["result_fingerprint"] == manifest["result_fingerprint"]


@pytest.mark.integration
def test_manifest_records_solver_code_git_and_environment(smoke_run: SmokeRun) -> None:
    manifest = smoke_run.manifest()
    config = json.loads(SMOKE.read_text(encoding="utf-8"))
    solver = manifest["solver"]
    assert solver["settings"] == config["solver"]
    assert solver["scipy"] == scipy.__version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", solver["highs"])
    code = manifest["code"]
    assert re.fullmatch(r"[0-9a-f]{64}", code["code_sha256"])
    assert code["files"] > 0
    assert code["uv_lock_sha256"] == sha256(REPO_ROOT / "uv.lock")
    git = manifest["git"]
    assert set(git) >= {"available", "commit", "dirty"}
    environment = manifest["environment"]
    assert set(environment) >= {"atlas", "python", "platform", "packages"}
    assert manifest["config"] == json.loads((smoke_run.directory / "config.json").read_bytes())
    assert manifest["config"]["experiment_id"] == "atlas-m4-smoke"


@pytest.mark.integration
def test_manifest_records_input_provenance(smoke_run: SmokeRun) -> None:
    manifest = smoke_run.manifest()
    inputs = manifest["inputs"]
    forecast = inputs["forecast"]
    m2_manifest = json.loads((M2_PROJECTION.parent / "manifest.json").read_bytes())
    assert forecast["run_id"] == m2_manifest["run_id"]
    assert forecast["projections_sha256"] == sha256(M2_PROJECTION)
    assert forecast["model_id"] == "atlas_linear_regression"
    status = inputs["uncertainty_status"]
    assert status["source"]["sha256"] == sha256(M3_STATUS)
    assert status["interval_status"] == "failed_calibration"
    data = manifest["data"]
    assert data["data_fingerprint"] == m2_manifest["data"]["data_fingerprint"]
    assert data["table_sha256"] == sha256(DAILY_TOTAL)
    assert status["data_fingerprint"] == data["data_fingerprint"]


@pytest.mark.integration
def test_manifest_records_each_scenario_demand_hash_and_adjustments(smoke_run: SmokeRun) -> None:
    scenarios = smoke_run.manifest()["scenarios"]
    assert set(scenarios) == {"normal", "reduced_budget", "delayed_surge", "m3_marginal_q90_stress"}
    # Independent hash of the first 10 days of the M2 projection: "YYYY-MM-DD,repr(value)\n".
    rows = [r for r in read_rows(M2_PROJECTION) if r["model_id"] == "atlas_linear_regression"]
    rows.sort(key=lambda r: int(r["horizon"]))
    lines = "".join(f"{r['target_date']},{float(r['prediction'])!r}\n" for r in rows[:HORIZON])
    point = hashlib.sha256(lines.encode("utf-8")).hexdigest()
    for scenario_id in ("normal", "reduced_budget", "delayed_surge"):
        assert scenarios[scenario_id]["demand_sha256"] == point
        assert scenarios[scenario_id]["demand_basis"] == "point_forecast"
    assert scenarios["m3_marginal_q90_stress"]["demand_sha256"] != point
    assert scenarios["normal"]["adjustments"] == {}
    budget = scenarios["reduced_budget"]["adjustments"]["discretionary_budget_multiplier"]
    assert budget["value"] == 0.25
    assert budget["provenance"] == "HYPOTHETICAL"
    surge = scenarios["delayed_surge"]["adjustments"]["surge_available_from_day"]
    assert surge["value"] == 4
    assert surge["provenance"] == "HYPOTHETICAL"


# --- 2. Label propagation --------------------------------------------------------------------


def assert_labelled(rows: list[dict[str, str]], interval_status: str) -> None:
    assert rows, "a result table must not be empty"
    for row in rows:
        assert row["record_type"] == RECORD_TYPE_DECISION_SIMULATION
        assert row["parameter_provenance"] == "HYPOTHETICAL"
        assert row["validated_recommendation"] == "False"
        assert row["point_forecast_validated"] == "False"
        assert row["m3_interval_status"] == interval_status


@pytest.mark.integration
@pytest.mark.parametrize("name", CSV_FILES)
def test_every_csv_row_carries_the_hypothetical_labels(smoke_run: SmokeRun, name: str) -> None:
    rows = smoke_run.rows(name)
    assert list(rows[0])[: len(LABEL_COLUMNS)] == list(LABEL_COLUMNS)
    assert_labelled(rows, "failed_calibration")


@pytest.mark.integration
def test_metrics_json_is_labelled_with_the_m3_status(smoke_run: SmokeRun) -> None:
    metrics = smoke_run.metrics()
    assert metrics["record_type"] == RECORD_TYPE_DECISION_SIMULATION
    assert metrics["validated_recommendation"] is False
    assert metrics["point_forecast_validated"] is False
    assert metrics["parameter_provenance"] == "HYPOTHETICAL"
    assert metrics["label"] == LABEL
    status = metrics["inputs"]["uncertainty_status"]
    assert status["interval_status"] == "failed_calibration"
    assert status["decision_grade"] is False
    assert metrics["structural_assumptions"] == list(STRUCTURAL_ASSUMPTIONS)


@pytest.mark.integration
def test_report_states_label_status_assumptions_and_disclaimer(smoke_run: SmokeRun) -> None:
    report = smoke_run.report()
    assert LABEL in report
    assert "FAILED_CALIBRATION" in report
    for assumption in STRUCTURAL_ASSUMPTIONS:
        assert assumption in report
    assert SENTENCE in report
    assert NOT_A_RECOMMENDATION in report
    # The disclaimer opens and closes the report.
    assert report.rstrip().endswith(NOT_A_RECOMMENDATION)
    assert report.index(NOT_A_RECOMMENDATION) < report.index("## Inputs")


# --- 3. Report wording lint ------------------------------------------------------------------

FORBIDDEN = (
    "saves",
    "saving",
    "reduces cost",
    "cost-effective",
    "optimal staffing",
    "should hire",
    "we recommend",
    "is recommended",
    "confidence",
    "90 % safe",
    "chance of",
)
# The only sentence the report uses with "guarantee": a negated disclaimer.
NEGATED_GUARANTEE = (
    "no chance constraint, service-level guarantee or probability of meeting a target is "
    "computed or implied"
)
ALLOWED_PROBABILITY = (NEGATED_GUARANTEE, "carries no probability", "carry no probability")
ALLOWED_RECOMMENDATION = (
    "nothing in this report is a validated staffing, budget or service-capacity recommendation",
    "not a validated staffing, budget or service-capacity recommendation",
    "not a budget recommendation",
)


def strip_allowed(text: str, allowed: tuple[str, ...]) -> str:
    for phrase in allowed:  # longest phrases first, so a shorter one cannot split a longer
        text = text.replace(phrase, " ")
    return text


@pytest.mark.integration
def test_report_wording_lint(smoke_run: SmokeRun) -> None:
    text = re.sub(r"\s+", " ", smoke_run.report().lower())
    for phrase in FORBIDDEN:
        assert phrase not in text, phrase
    # "guarantee" only inside the negated disclaimer.
    assert NEGATED_GUARANTEE in text
    assert "guarantee" not in strip_allowed(text, (NEGATED_GUARANTEE,))
    # "probability" only negated: the disclaimer sentence, "carry/carries no probability",
    # then any remaining "no probability".
    assert text.count("probability") >= 3
    remainder = strip_allowed(text, ALLOWED_PROBABILITY).replace("no probability", " ")
    assert "probability" not in remainder
    assert "probabilit" not in remainder
    # "recommend" in any form only inside the allowed not-a-recommendation phrases.
    assert text.count("recommendation") >= 2
    assert "recommend" not in strip_allowed(text, ALLOWED_RECOMMENDATION)


def test_wording_lint_catches_unnegated_probability_and_recommendation() -> None:
    # The lint itself must reject what it is meant to reject.
    bad = "this plan has a high probability of success and is a staffing recommendation"
    assert "probability" in strip_allowed(bad, ALLOWED_PROBABILITY).replace("no probability", "")
    assert "recommend" in strip_allowed(bad, ALLOWED_RECOMMENDATION)


# --- 4./5. verify and inspect ----------------------------------------------------------------


@pytest.mark.integration
def test_verify_reproduces_every_file_byte_for_byte(smoke_run: SmokeRun) -> None:
    code, output = run_cli("optimize", "verify", str(smoke_run.directory))
    assert code == EXIT_OK
    report = optimization_report(output, "optimization_verification")
    facts = report["facts"]
    assert facts["identical"] is True
    assert set(facts["files"]) == set(ALL_FILES)
    assert set(facts["files"].values()) == {"identical"}
    assert facts["altered_on_disk"] == []
    assert facts["result_fingerprint"] == smoke_run.manifest()["result_fingerprint"]
    assert issue_codes(report, "error") == []


@pytest.mark.integration
def test_verify_detects_one_altered_byte_in_plans(smoke_run: SmokeRun, tmp_path: Path) -> None:
    run_dir = copy_run(smoke_run, tmp_path / "altered")
    plans = run_dir / "plans.csv"
    content = bytearray(plans.read_bytes())
    position = content.index(b"failed_calibration")
    content[position] = ord("F")  # one byte: f -> F
    plans.write_bytes(bytes(content))
    code, output = run_cli("optimize", "verify", str(run_dir))
    assert code == EXIT_FAILED
    report = optimization_report(output, "optimization_verification")
    assert issue_codes(report, "error") == ["file_altered"]
    assert report["issues"][0]["subject"] == "plans.csv"
    assert report["facts"]["altered_on_disk"] == ["plans.csv"]
    assert report["facts"]["identical"] is False


@pytest.mark.integration
def test_inspect_passes_and_prints_the_scenario_table(smoke_run: SmokeRun) -> None:
    code, output = run_cli("optimize", "inspect", str(smoke_run.directory), output_format="text")
    assert code == EXIT_OK
    assert "[PASS] optimization_inspection" in output
    assert "M3 calibration status FAILED_CALIBRATION: not decision-grade" in output
    assert "scenario                    plan" in output
    assert smoke_run.manifest()["result_fingerprint"] in output
    for scenario_id, plan in (
        ("normal", "strict"),
        ("reduced_budget", "relaxed_terminal_target"),
        ("delayed_surge", "strict"),
        ("m3_marginal_q90_stress", "relaxed_terminal_target"),
    ):
        assert re.search(rf"^\s+{scenario_id}\s+{plan}\s", output, re.MULTILINE), scenario_id


@pytest.mark.integration
def test_inspect_refuses_a_run_marked_incomplete(smoke_run: SmokeRun, tmp_path: Path) -> None:
    run_dir = copy_run(smoke_run, tmp_path / "incomplete")
    manifest = smoke_run.manifest()
    manifest["complete"] = False
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    code, output = run_cli("optimize", "inspect", str(run_dir))
    assert code == EXIT_FAILED
    report = optimization_report(output, "optimization_inspection")
    assert issue_codes(report, "error") == ["run_incomplete"]


@pytest.mark.integration
@pytest.mark.parametrize("command", ["verify", "inspect"])
def test_verify_and_inspect_refuse_a_non_m4_directory(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = run_cli("optimize", command, str(M3_RUN))
    assert code == EXIT_USAGE
    assert f"is not a {RECORD_TYPE_DECISION_SIMULATION} run" in capsys.readouterr().err


# --- 6. Refusals before any computation ------------------------------------------------------


def _forbid_computation(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("nothing may be computed before the refusal")

    monkeypatch.setattr("atlas.optimization_runner.load_series", explode)
    monkeypatch.setattr("atlas.optimization_runner.m4_outputs", explode)


def test_require_clean_refuses_uncommitted_changes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "atlas.optimization_runner.git_info",
        lambda _root: {"available": True, "commit": "x", "dirty": True},
    )
    _forbid_computation(monkeypatch)
    target = tmp_path / "clean"
    code, _ = run_cli(
        "optimize",
        "run",
        "--config",
        str(SMOKE),
        "--output-dir",
        str(target),
        "--require-clean",
    )
    assert code == EXIT_USAGE
    assert "uncommitted changes" in capsys.readouterr().err
    assert not target.exists()


def test_existing_output_directory_is_refused_before_computing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _forbid_computation(monkeypatch)
    target = tmp_path / "taken"
    target.mkdir()
    code, _ = run_cli("optimize", "run", "--config", str(SMOKE), "--output-dir", str(target))
    assert code == EXIT_USAGE
    assert "already exists" in capsys.readouterr().err
    assert list(target.iterdir()) == []


# --- 7. FAILED_CALIBRATION handling ----------------------------------------------------------


@pytest.mark.integration
def test_cli_output_states_the_failed_calibration_status(smoke_run: SmokeRun) -> None:
    report = optimization_report(smoke_run.output)
    assert report["facts"]["m3_interval_status"] == "failed_calibration"
    warning = next(i for i in report["issues"] if i["code"] == "uncertainty_not_decision_grade")
    assert warning["severity"] == "warning"
    assert "FAILED_CALIBRATION" in warning["message"]
    info = next(i for i in report["issues"] if i["code"] == "hypothetical_simulation")
    assert info["message"] == LABEL


@pytest.mark.integration
def test_decision_grade_status_still_yields_no_validated_recommendation(
    repo_settings: Settings,
) -> None:
    config = load_optimization_config(SMOKE)
    _, data = load_series(repo_settings, "optimization")
    assert data is not None
    inputs = load_inputs(repo_settings, config, data)
    graded = dataclasses.replace(
        inputs,
        status=dataclasses.replace(
            inputs.status, interval_status="decision_grade", decision_grade=True
        ),
    )
    outputs = m4_outputs(repo_settings, config, graded)
    assert outputs.payload["validated_recommendation"] is False
    assert outputs.payload["point_forecast_validated"] is False
    assert outputs.payload["parameter_provenance"] == "HYPOTHETICAL"
    assert outputs.payload["inputs"]["uncertainty_status"]["interval_status"] == "decision_grade"
    codes = [i.code for i in outputs.issues]
    assert "uncertainty_not_decision_grade" not in codes
    assert "hypothetical_simulation" in codes
    for name in CSV_FILES:
        assert_labelled(rows_of_bytes(outputs.files[name]), "decision_grade")
    report = outputs.files["report.md"].decode("utf-8")
    assert LABEL in report
    assert SENTENCE in report


# --- 8. Scenario facts -----------------------------------------------------------------------


def scenario_row(run: SmokeRun, scenario_id: str, plan: str) -> dict[str, str]:
    return next(
        r
        for r in run.rows("scenarios.csv")
        if r["scenario_id"] == scenario_id and r["plan"] == plan
    )


def plan_days(run: SmokeRun, scenario_id: str, plan: str) -> list[dict[str, str]]:
    rows = [
        r for r in run.rows("plans.csv") if r["scenario_id"] == scenario_id and r["plan"] == plan
    ]
    return sorted(rows, key=lambda r: int(r["day"]))


@pytest.mark.integration
def test_reduced_budget_is_infeasible_and_reports_a_relaxed_plan(smoke_run: SmokeRun) -> None:
    strict = scenario_row(smoke_run, "reduced_budget", "optimized")
    assert strict["feasibility"] == "infeasible"
    relaxed = scenario_row(smoke_run, "reduced_budget", "relaxed_terminal_target")
    assert relaxed["feasibility"] == "relaxed_terminal_target"
    assert float(relaxed["discretionary_budget"]) == pytest.approx(0.25 * BUDGET)
    assert relaxed["satisfies_budget"] == "True"
    terminal = float(relaxed["terminal_backlog"])
    assert float(relaxed["terminal_violation"]) == pytest.approx(terminal - TERMINAL_MAX)
    # By hand: the fixed-capacity backlog stays positive every day (checked below), so every
    # extra request served lowers the final backlog one for one. The cheapest extra capacity
    # is overtime at 57 / 2.2 = 25.9 USD/request (< 28 for surge); 50,000 USD buy
    # 50,000 / 57 = 877.2 hours (within 500/day and 3,000 in total), i.e. 1,929.8 requests.
    # Least final backlog = b0 + sum(d) - 10 * 10,320 - 1,929.8.
    days = plan_days(smoke_run, "reduced_budget", "fixed_capacity")
    assert len(days) == HORIZON
    assert all(float(d["backlog"]) > 0 for d in days)
    demand = math.fsum(float(d["demand"]) for d in days)
    extra = 0.25 * BUDGET / OVERTIME_WAGE * OVERTIME_PRODUCTIVITY
    expected = INITIAL_BACKLOG + demand - HORIZON * REGULAR_CAPACITY - extra
    assert terminal == pytest.approx(expected, rel=1e-7)
    assert len(plan_days(smoke_run, "reduced_budget", "relaxed_terminal_target")) == HORIZON
    report = optimization_report(smoke_run.output)
    warnings = {i["subject"] for i in report["issues"] if i["code"] == "strict_model_infeasible"}
    assert warnings == {"reduced_budget", "m3_marginal_q90_stress"}
    assert all(
        i["severity"] == "warning"
        for i in report["issues"]
        if i["code"] == "strict_model_infeasible"
    )
    assert smoke_run.manifest()["scenarios"]["reduced_budget"]["feasibility"] == (
        "relaxed_terminal_target"
    )


@pytest.mark.integration
def test_normal_is_strict_and_its_duals_are_bracketed(smoke_run: SmokeRun) -> None:
    assert scenario_row(smoke_run, "normal", "optimized")["feasibility"] == "strict"
    rows = [r for r in smoke_run.rows("duals.csv") if r["scenario_id"] == "normal"]
    assert rows
    for row in rows:
        assert row["fd_status"] in FD_STATUSES, row
        assert row["bracket_ok"] == "True", row
    metrics = next(s for s in smoke_run.metrics()["scenarios"] if s["scenario_id"] == "normal")
    assert metrics["shadow_prices"]["n"] == len(rows)
    assert metrics["shadow_prices"]["brackets_ok"] is True
    # Shadow prices are configured for "normal" only.
    others = [r for r in smoke_run.rows("duals.csv") if r["scenario_id"] != "normal"]
    assert {r["fd_status"] for r in others} <= {"not_computed"}


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario_id", ["normal", "reduced_budget", "delayed_surge", "m3_marginal_q90_stress"]
)
def test_fixed_capacity_rows_flag_a_missed_terminal_target(
    smoke_run: SmokeRun, scenario_id: str
) -> None:
    days = plan_days(smoke_run, scenario_id, "fixed_capacity")
    assert len(days) == HORIZON
    # Independent recursion: regular staff only, b[t] = max(0, b[t-1] + d[t] - p*H).
    backlog = INITIAL_BACKLOG
    for day in days:
        assert float(day["regular_capacity"]) == pytest.approx(REGULAR_CAPACITY)
        backlog = max(0.0, backlog + float(day["demand"]) - REGULAR_CAPACITY)
        assert float(day["backlog"]) == pytest.approx(backlog, abs=1e-6)
        assert float(day["overtime_hours"]) == 0
        assert float(day["surge_requests"]) == 0
    row = scenario_row(smoke_run, scenario_id, "fixed_capacity")
    assert float(row["terminal_backlog"]) == pytest.approx(backlog, abs=1e-6)
    assert float(row["discretionary_cost"]) == 0
    misses = backlog > TERMINAL_MAX + 1e-6
    assert misses  # 10 x 10,320 is below the forecast demand, so every scenario misses it
    assert row["feasibility"] == ("violates_terminal_backlog" if misses else "strict")
    assert float(row["terminal_violation"]) == pytest.approx(max(0.0, backlog - TERMINAL_MAX))


@pytest.mark.integration
def test_every_plan_in_plans_csv_balances_and_respects_capacity(smoke_run: SmokeRun) -> None:
    rows = smoke_run.rows("plans.csv")
    keys = sorted({(r["scenario_id"], r["plan"]) for r in rows})
    assert keys
    for scenario_id, plan in keys:
        days = plan_days(smoke_run, scenario_id, plan)
        assert [int(d["day"]) for d in days] == list(range(1, HORIZON + 1))
        previous = INITIAL_BACKLOG
        for d in days:
            demand, served, backlog = float(d["demand"]), float(d["served"]), float(d["backlog"])
            capacity = (
                float(d["regular_capacity"])
                + OVERTIME_PRODUCTIVITY * float(d["overtime_hours"])
                + float(d["surge_requests"])
            )
            tol = 1e-6 * max(1.0, abs(demand))
            assert float(d["work_available"]) == pytest.approx(previous + demand)
            assert backlog == pytest.approx(previous + demand - served, abs=tol)
            assert served <= capacity + tol
            assert backlog >= -tol
            assert float(d["overtime_hours"]) <= 500 + 1e-6
            assert float(d["surge_requests"]) <= 500 + 1e-6
            previous = backlog
        if scenario_id == "delayed_surge":
            # Surge only from planning day 4 (1-based): days 1-3 carry none.
            assert all(float(d["surge_requests"]) <= 1e-6 for d in days[:3])


@pytest.mark.integration
def test_sensitivity_covers_every_configured_sweep_value(smoke_run: SmokeRun) -> None:
    config = json.loads(SMOKE.read_text(encoding="utf-8"))
    rows = smoke_run.rows("sensitivity.csv")
    sweeps = config["sensitivity"]["sweeps"]
    assert len(rows) == sum(len(s["values"]) for s in sweeps)
    for sweep in sweeps:
        values = sorted(float(r["value"]) for r in rows if r["parameter"] == sweep["parameter"])
        assert values == sorted(float(v) for v in sweep["values"]), sweep["parameter"]
    assert {r["base_scenario"] for r in rows} == {"normal"}
    # A zero discretionary budget leaves only regular capacity: 0 USD of discretionary cash.
    zero = next(
        r
        for r in rows
        if r["parameter"] == "discretionary_budget_multiplier" and float(r["value"]) == 0
    )
    assert float(zero["discretionary_budget"]) == 0
    if zero["discretionary_cost"]:
        assert float(zero["discretionary_cost"]) == pytest.approx(0, abs=1e-6)


@pytest.mark.integration
def test_frontier_rows_carry_the_tradeoff_label(smoke_run: SmokeRun) -> None:
    rows = smoke_run.rows("frontier.csv")
    assert rows
    assert {r["label"] for r in rows} == {"HYPOTHETICAL TRADEOFF, not a budget recommendation"}
    assert {r["scenario_id"] for r in rows} == {"normal"}
    assert {r["terminal_target"] for r in rows} == {"kept", "dropped"}
    for row in rows:
        assert row["exceeds_configured_budget"] == str(float(row["cash_cap"]) > BUDGET)
    for target in ("kept", "dropped"):
        grid = [r for r in rows if r["terminal_target"] == target and r["marker"] == "grid"]
        assert len(grid) == 5  # "points": 5 in the smoke configuration


# --- 9. ERROR path ---------------------------------------------------------------------------


@pytest.mark.integration
def test_failed_certificate_marks_the_run_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real: Callable[..., Any] = atlas.optimizer.certify

    def failing(*args: Any, **kwargs: Any) -> Any:
        return dataclasses.replace(real(*args, **kwargs), holds=False)

    monkeypatch.setattr("atlas.decision_analysis.certify", failing)
    target = tmp_path / "broken"
    code, output = run_cli("optimize", "run", "--config", str(SMOKE), "--output-dir", str(target))
    assert code == EXIT_FAILED
    report = optimization_report(output)
    errors = [i for i in report["issues"] if i["severity"] == "error"]
    assert errors
    assert {i["code"] for i in errors} <= {"check_failed", "monotonicity_violated"}
    assert any("optimality certificate failed" in i["message"] for i in errors)
    assert report["facts"]["complete"] is False
    manifest = json.loads((target / "manifest.json").read_bytes())
    assert manifest["complete"] is False
    assert json.loads((target / "metrics.json").read_bytes())["checks"]["errors"] == len(errors)
    monkeypatch.undo()
    code, output = run_cli("optimize", "inspect", str(target))
    assert code == EXIT_FAILED
    assert "run_incomplete" in issue_codes(optimization_report(output, "optimization_inspection"))


# --- 10. Report rendering and formatting helpers ---------------------------------------------


@pytest.mark.integration
def test_report_rendering_is_deterministic_for_a_fixed_payload(smoke_run: SmokeRun) -> None:
    payload = smoke_run.metrics()
    first = render_optimization_report(payload)
    assert render_optimization_report(payload) == first
    assert render_optimization_report(json.loads(json.dumps(payload))) == first
    assert render_optimization_report(dict(reversed(list(payload.items())))) == first


@pytest.mark.integration
def test_report_md_is_reproducible_from_the_written_metrics_json(smoke_run: SmokeRun) -> None:
    # The report is documented as a pure function of its metrics.json payload, so rendering
    # the payload read back from disk must give report.md byte for byte.
    rendered = render_optimization_report(smoke_run.metrics())
    assert rendered.encode("utf-8") == (smoke_run.directory / "report.md").read_bytes()


@pytest.mark.parametrize(
    ("value", "digits", "expected"),
    [
        (None, 0, "n/a"),
        (True, 0, "yes"),
        (False, 2, "no"),
        (float("nan"), 1, "n/a"),
        (float("inf"), 0, "+inf"),
        (float("-inf"), 3, "-inf"),
        (-0.0, 0, "0"),
        (-0.4, 0, "0"),
        (0.0, 0, "0"),
        (7, 0, "7"),
        (1_234_567.891, 2, "1,234,567.89"),
        (-1_234.5678, 1, "-1,234.6"),
        (-0.6, 0, "-1"),
        ("text", 0, "text"),
    ],
)
def test_n_formats_none_booleans_nan_infinities_and_negative_zero(
    value: Any, digits: int, expected: str
) -> None:
    assert _n(value, digits) == expected


@pytest.mark.parametrize("digits", [0, 1, 2, 3, 4, 5, 6])
def test_n_formats_negative_zero_like_positive_zero(digits: int) -> None:
    # A value that rounds to zero must print the same as zero at the same precision:
    # f"{0.0:,.3f}" is "0.000", so -0.0 and -1e-9 must print "0.000" too.
    zero = f"{0.0:,.{digits}f}"
    assert _n(-0.0, digits) == zero
    assert _n(-1e-9, digits) == zero


@settings(max_examples=40, deadline=None)
@given(
    value=st.floats(min_value=-1e-3, max_value=0.0, allow_nan=False),
    digits=st.integers(min_value=0, max_value=3),
)
def test_n_never_prints_a_signed_zero(value: float, digits: int) -> None:
    text = _n(value, digits)
    if float(text.replace(",", "")) == 0:
        assert not text.startswith("-")


@pytest.mark.parametrize(
    ("value", "digits", "expected"),
    [
        (None, 0, "n/a"),
        (0.0, 0, "the same USD"),
        (-0.0, 0, "the same USD"),
        (0.49, 0, "the same USD"),
        (-0.0004, 3, "the same USD"),
        (-1234.4, 0, "1,234 USD less"),
        (205.2, 0, "205 USD more"),
        (-0.6, 0, "1 USD less"),
        (float("inf"), 0, "+inf USD more"),
        (float("-inf"), 0, "+inf USD less"),
    ],
)
def test_difference_wording_follows_the_sign(
    value: float | None, digits: int, expected: str
) -> None:
    assert _difference(value, "USD", "less", "more", digits) == expected


@settings(max_examples=40, deadline=None)
@given(
    value=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False),
    digits=st.integers(min_value=0, max_value=3),
)
def test_difference_never_shows_a_zero_magnitude_with_a_direction(
    value: float, digits: int
) -> None:
    # Exact binary rounding ties (+-0.5 at 0 digits) are the subject of the next test.
    assume(not (digits == 0 and abs(value) == 0.5))
    text = _difference(value, "USD", "less", "more", digits)
    if text == "the same USD":
        return
    number, _unit, word = text.split(" ")
    assert word == ("less" if value < 0 else "more")
    assert float(number.replace(",", "")) > 0, text


@pytest.mark.parametrize(("value", "digits"), [(0.5, 0), (-0.5, 0)])
def test_difference_at_a_rounding_tie_does_not_print_zero_more_or_less(
    value: float, digits: int
) -> None:
    # 0.5 is not below the "same" threshold 0.5 * 10**-digits, but Python rounds it half to
    # even, so "0 USD more" would be printed: the shown magnitude must not be zero.
    text = _difference(value, "USD", "less", "more", digits)
    assert text == "the same USD" or float(text.split(" ")[0].replace(",", "")) > 0, text


def test_difference_of_nan_has_no_direction() -> None:
    assert _difference(float("nan"), "USD", "less", "more") == "n/a"
