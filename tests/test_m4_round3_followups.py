"""Regression tests for gaps found while testing the round-3 fixes.

The agents that wrote ``tests/test_m4_round3_*.py`` found that some fixes of commit a7ca6a6
were incomplete:
- the reach tolerance summed over units, which can hide one unit's violation;
- the backward-step rule labelled a resolved kink ``backward_unresolved`` once the right-hand
  side fell just below the step;
- the after-tie-break uniqueness summary reported 0 undetermined days although the per-day
  ranges were never computed;
- malformed scalar values in ``metrics.json`` crashed the text renderer of ``inspect``.

Each test here fails on a7ca6a6, except the two kink cases above the step (controls).
"""

from __future__ import annotations

import io
import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from atlas.cli import EXIT_FAILED, EXIT_USAGE, main
from atlas.decision_analysis import analyse_scenario, diagnose, reach_tolerance
from atlas.optimization_runner import _face_summary
from atlas.optimizer import SolverSettings
from atlas.planning import PlanningProblem, ServiceUnit, check_plan
from tests.conftest import REPO_ROOT

SETTINGS = SolverSettings()
STEPS = {"requests": 1.0, "staff_hours": 1.0, "usd": 100.0}
SMOKE = REPO_ROOT / "configs" / "optimization" / "smoke.json"


def _unit(uid: str, gap: float) -> ServiceUnit:
    return ServiceUnit(
        unit_id=uid,
        demand=(10.0,),
        initial_backlog=0.0,
        regular_hours=(0.0,),
        regular_productivity=1.0,
        overtime_productivity=1.0,
        overtime_max_hours=(10.0 - gap,),
        surge_max=(0.0,),
        regular_wage=1.0,
        overtime_wage=1.0,
        surge_price=1.0,
        backlog_penalty=1.0,
        terminal_backlog_max=0.0,
    )


def test_one_units_violation_is_not_hidden_by_another_units_tolerance() -> None:
    # Unit a misses its target by 1.5e-6 requests, above its own check tolerance (1e-6);
    # unit b meets it. Summing the two units' tolerances (2e-6) called the target
    # "within the checking tolerance" although the plan check rejects unit a.
    problem = PlanningProblem(date(2026, 7, 1), (_unit("a", 1.5e-6), _unit("b", 0.0)), None)
    assert reach_tolerance(problem) == pytest.approx(1e-6)
    diagnosis = diagnose(problem, SETTINGS)
    relaxed = diagnosis.relaxed.require().plan
    assert not check_plan(problem, relaxed).feasible
    assert not diagnosis.borderline
    text = " ".join(diagnosis.reasons)
    assert "cannot be reached with any amount of discretionary cash" in text
    assert "within the checking tolerance" not in text
    assert f"a: the daily overtime limit of {10.0 - 1.5e-6:,} staff hours" in text
    assert diagnosis.limit_relaxations["a:overtime_daily_limit"] == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("rhs", [0.5e-3, 0.999e-3, 1.001e-3, 2e-3])
def test_a_resolved_kink_keeps_its_label_across_the_step(rhs: float) -> None:
    # Backward slope 0, forward slope 3 at a right-hand side near the local step (1e-3),
    # next to an optimum of about 1e7 USD: the noise allowance (about 4e-4) is far below the
    # 3 USD gap, so the kink is resolved on both sides of the step: never "unresolved".
    big = 1e7
    unit = ServiceUnit(
        unit_id="u",
        demand=(rhs, big),
        initial_backlog=0.0,
        regular_hours=(rhs, big / 2),
        regular_productivity=1.0,
        overtime_productivity=2.0,
        overtime_max_hours=(0.0, big),
        surge_max=(0.0, 0.0),
        regular_wage=1.0,
        overtime_wage=60.0,
        surge_price=1.0,
        backlog_penalty=1.5,
    )
    result = analyse_scenario(
        "kink",
        PlanningProblem(date(2026, 7, 1), (unit,), None),
        SETTINGS,
        {"u": 0.0},
        shadow_steps=STEPS,
        frontier_points=None,
    )
    item = next(f for f in result.finite_differences if f.dual.key() == ("balance", "u", 0))
    assert item.backward == pytest.approx(0.0, abs=1e-3)
    assert item.forward == pytest.approx(3.0, abs=1e-3)
    assert item.status == "breakpoint"
    assert not item.backward_unresolved
    assert not result.problems


def test_undetermined_days_are_unknown_when_daily_ranges_were_not_computed() -> None:
    # Overtime is free (wage 0) and only waiting is penalised, so 0..5 overtime hours on day 1
    # all tie: one day is not determined. The after-tie-break test skips the per-day ranges,
    # so its count must be unknown (None), not 0.
    unit = ServiceUnit(
        unit_id="u",
        demand=(10.0, 0.0),
        initial_backlog=0.0,
        regular_hours=(10.0, 0.0),
        regular_productivity=1.0,
        overtime_productivity=1.0,
        overtime_max_hours=(5.0, 0.0),
        surge_max=(0.0, 0.0),
        regular_wage=1.0,
        overtime_wage=0.0,
        surge_price=5.0,
        backlog_penalty=1.0,
    )
    result = analyse_scenario(
        "free",
        PlanningProblem(date(2026, 7, 1), (unit,), None),
        SETTINGS,
        {"u": 0.0},
        shadow_steps=None,
        frontier_points=None,
    )
    full = _face_summary(result.face)
    assert full is not None
    assert full["unique"] is False
    assert full["undetermined_days"] == 1
    after = _face_summary(result.tie_broken_face, daily=False)
    assert after is not None
    assert after["unique"] is False
    assert after["undetermined_days"] is None


# --- inspect never crashes on malformed values ------------------------------------------------


@pytest.fixture(scope="module")
def smoke_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("followups") / "run"
    args = ["optimize", "run", "--config", str(SMOKE), "--output-dir", str(directory)]
    assert main(["--repo-root", str(REPO_ROOT), *args], stdout=io.StringIO()) == 0
    return directory


def _inspect(directory: Path) -> tuple[int, str]:
    buffer = io.StringIO()
    code = main(
        ["--repo-root", str(REPO_ROOT), "optimize", "inspect", str(directory)], stdout=buffer
    )
    return code, buffer.getvalue()


def _tampered(smoke_run: Path, tmp_path: Path, change: Any) -> Path:
    directory = tmp_path / "run"
    shutil.copytree(smoke_run, directory)
    path = directory / "metrics.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    change(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return directory


def _set_cost(payload: dict[str, Any]) -> None:
    payload["scenarios"][0]["reported_plan"]["evaluation"]["discretionary_cost"] = "x"


def _empty_scenario(payload: dict[str, Any]) -> None:
    payload["scenarios"][0] = {}


def _null_feasibility(payload: dict[str, Any]) -> None:
    payload["scenarios"][0]["feasibility"] = None


@pytest.mark.integration
@pytest.mark.parametrize("change", [_set_cost, _empty_scenario, _null_feasibility])
def test_inspect_text_reports_malformed_values_instead_of_crashing(
    smoke_run: Path, tmp_path: Path, change: Any
) -> None:
    code, text = _inspect(_tampered(smoke_run, tmp_path, change))
    assert code == EXIT_FAILED
    assert "[file_altered]" in text


@pytest.mark.integration
def test_deeply_nested_metrics_are_a_usage_error(smoke_run: Path, tmp_path: Path) -> None:
    directory = tmp_path / "run"
    shutil.copytree(smoke_run, directory)
    (directory / "metrics.json").write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    code, _ = _inspect(directory)
    assert code == EXIT_USAGE


@pytest.mark.integration
def test_an_output_name_with_a_nul_byte_is_a_usage_error(smoke_run: Path, tmp_path: Path) -> None:
    directory = tmp_path / "run"
    shutil.copytree(smoke_run, directory)
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["outputs"]["a\u0000b"] = "0" * 64
    path.write_text(json.dumps(manifest), encoding="utf-8")
    code, _ = _inspect(directory)
    assert code == EXIT_USAGE
