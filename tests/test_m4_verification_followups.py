"""Regression tests for the last gaps found while testing the verification-round fixes.

The agents that wrote ``tests/test_m4_verification_*.py`` against commit ce2b104 found:
- a failed least-cash split of a finite difference still recorded an epsilon-cap fallback;
- an infeasible verdict of the interior-point last resort was not named;
- idle capacity parked at a capped lift counted as a binding cap (a false ERROR);
- the frontier sentence ignored points whose uniqueness test failed;
- lone surrogates in any run-file string crashed the text renderer;
- a symlinked module file inside src/atlas passed the package check;
- the run-id and result-file checks named the wrong reference.
"""

from __future__ import annotations

import io
import json
import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from scipy.optimize import OptimizeResult

import atlas.decision_analysis as analysis
import atlas.optimizer as optimizer
from atlas.cli import EXIT_FAILED, EXIT_USAGE, main
from atlas.decision_analysis import analyse_scenario
from atlas.optimization_report import _tied_points
from atlas.optimization_runner import running_package_is_repository
from atlas.optimizer import OptimizationError, SolverSettings, SolveStatus, build_lp, solve_lp
from atlas.planning import PlanningProblem, ServiceUnit
from tests.conftest import REPO_ROOT

SETTINGS = SolverSettings()
STEPS = {"requests": 1.0, "staff_hours": 1.0, "usd": 100.0}
SMOKE = REPO_ROOT / "configs" / "optimization" / "smoke.json"


def _small_unit() -> ServiceUnit:
    return ServiceUnit(
        unit_id="u",
        demand=(100.0, 120.0, 90.0),
        initial_backlog=50.0,
        regular_hours=(40.0, 40.0, 40.0),
        regular_productivity=2.0,
        overtime_productivity=2.0,
        overtime_max_hours=(10.0, 10.0, 10.0),
        surge_max=(20.0, 20.0, 20.0),
        regular_wage=10.0,
        overtime_wage=30.0,
        surge_price=20.0,
        backlog_penalty=1.5,
        terminal_backlog_max=60.0,
    )


def test_a_failed_least_cash_split_records_no_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    # The least-cash programme of one practical forward step fails on every fallback: no
    # plan, so no "plan keeps the earlier optimum to within the epsilon cap" (a WARNING that
    # used to be recorded); the failure is the problem.
    problem = PlanningProblem(date(2026, 7, 1), (_small_unit(),), 5000.0)
    real_least_cash, real_solve = analysis._least_cash, analysis.solve_lp
    inside = {"on": False}

    def least_cash(lp: Any, result: Any, settings: Any, what: str, problems: Any) -> Any:
        inside["on"] = what == "finite difference balance u 1 +1"
        try:
            return real_least_cash(lp, result, settings, what, problems)
        finally:
            inside["on"] = False

    def solve(lp: Any, settings: Any) -> Any:
        if inside["on"]:
            raise OptimizationError("injected: HiGHS Status 4")
        return real_solve(lp, settings)

    monkeypatch.setattr(analysis, "_least_cash", least_cash)
    monkeypatch.setattr(analysis, "solve_lp", solve)
    result = analyse_scenario(
        "fd", problem, SETTINGS, {"u": 0.0}, shadow_steps=STEPS, frontier_points=None
    )
    item = next(f for f in result.finite_differences if f.dual.key() == ("balance", "u", 1))
    assert item.forward_cash is None
    assert item.stage_fallback is None
    assert "epsilon_cap" not in result.fallbacks
    assert any("least-cash programme is solver_failed" in p for p in item.problems)


def test_an_interior_point_infeasibility_verdict_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    # The dual simplex returns status 0 with and without presolve; the interior-point method
    # finds the programme infeasible. The message names the method that reached the verdict.
    real = optimizer.linprog

    def fake(*args: Any, **kwargs: Any) -> OptimizeResult:
        if kwargs["method"] == "highs-ipm":
            return OptimizeResult(
                status=2, message="The problem is infeasible. (HiGHS Status 8: x)", nit=0
            )
        return OptimizeResult(status=4, message="(HiGHS Status 0: Not Set)", nit=0)

    monkeypatch.setattr(optimizer, "linprog", fake)
    unit = _small_unit()
    result = solve_lp(build_lp(PlanningProblem(date(2026, 7, 1), (unit,), None)), SETTINGS)
    monkeypatch.setattr(optimizer, "linprog", real)
    assert result.status is SolveStatus.INFEASIBLE
    assert result.retried
    assert result.message.endswith("[highs-ipm]")


def test_idle_capacity_parked_at_a_capped_lift_is_not_a_binding_cap() -> None:
    # Demand 1e8 a day, regular capacity 5e7, overtime 0.25 requests per hour: lifting the
    # daily overtime limit needs 2e8 hours a day, but the lift is capped at 5e8 hours, and
    # HiGHS parks unused overtime hours at the cap. The capped relaxation meets the target,
    # so the removal is exact; it used to be reported as "binds at its cap" (an ERROR).
    unit = ServiceUnit(
        unit_id="u",
        demand=(1e8,) * 4,
        initial_backlog=0.0,
        regular_hours=(5e7,) * 4,
        regular_productivity=1.0,
        overtime_productivity=0.25,
        overtime_max_hours=(1e6,) * 4,
        surge_max=(0.0,) * 4,
        regular_wage=1.0,
        overtime_wage=1.0,
        surge_price=1.0,
        backlog_penalty=1.0,
        overtime_total_hours=None,
        terminal_backlog_max=4000.0,
    )
    problem = PlanningProblem(date(2026, 7, 1), (unit,), None)
    result = analyse_scenario(
        "idle", problem, SETTINGS, {"u": 0.0}, shadow_steps=None, frontier_points=None
    )
    assert not any("binds at its cap" in p for p in result.problems)
    assert result.diagnosis is not None
    assert result.diagnosis.limit_relaxations["u:overtime_daily_limit"] == pytest.approx(
        0.0, abs=1e-6
    )
    assert (
        "the daily overtime limit of 1,000,000 staff hours (to 0.0 requests without it)"
        in (result.diagnosis.reasons[0])
    )


def test_a_cap_that_binds_on_a_tight_day_is_still_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    # Control: with the cap lowered so that the capped relaxation still misses the target
    # and serves at full capacity, the removal is not exact and is reported as not computed.
    monkeypatch.setattr(analysis, "LIFT_CAP", 1.2e8)
    unit = ServiceUnit(
        unit_id="u",
        demand=(1e8,) * 4,
        initial_backlog=0.0,
        regular_hours=(5e7,) * 4,
        regular_productivity=1.0,
        overtime_productivity=0.25,
        overtime_max_hours=(1e6,) * 4,
        surge_max=(0.0,) * 4,
        regular_wage=1.0,
        overtime_wage=1.0,
        surge_price=1.0,
        backlog_penalty=1.0,
        overtime_total_hours=None,
        terminal_backlog_max=4000.0,
    )
    problem = PlanningProblem(date(2026, 7, 1), (unit,), None)
    result = analyse_scenario(
        "tight", problem, SETTINGS, {"u": 0.0}, shadow_steps=None, frontier_points=None
    )
    assert any(
        "limit relaxation u:overtime_daily_limit: the lifted limit binds at its cap" in p
        for p in result.problems
    )
    assert result.diagnosis is not None
    assert result.diagnosis.limit_relaxations["u:overtime_daily_limit"] is None


def test_the_frontier_sentence_counts_points_whose_uniqueness_test_failed() -> None:
    text = _tied_points(
        {
            "points_not_unique_after_tie_breaks": 0,
            "points_solver_failed": 0,
            "points_uniqueness_not_tested": 10,
        }
    )
    assert text.startswith("every tested point is unique after the tie-breaks")
    assert "at 10 points the uniqueness test could not be completed" in text
    assert _tied_points({"points_not_unique_after_tie_breaks": 0}) == (
        "every point is unique after the tie-breaks"
    )


# --- Run files ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def smoke_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("verification_followups") / "run"
    args = ["optimize", "run", "--config", str(SMOKE), "--output-dir", str(directory)]
    assert main(["--repo-root", str(REPO_ROOT), *args], stdout=io.StringIO()) == 0
    return directory


def _cli(*args: str) -> tuple[int, str]:
    buffer = io.StringIO()
    code = main(["--repo-root", str(REPO_ROOT), "--format", "json", *args], stdout=buffer)
    return code, buffer.getvalue()


@pytest.mark.integration
@pytest.mark.parametrize("command", ["inspect", "verify"])
def test_a_lone_surrogate_in_any_run_file_string_is_a_usage_error(
    smoke_run: Path, tmp_path: Path, command: str
) -> None:
    directory = tmp_path / "run"
    shutil.copytree(smoke_run, directory)
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["result_fingerprint"] = "\ud800"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    buffer = io.StringIO()
    code = main(["--repo-root", str(REPO_ROOT), "optimize", command, str(directory)], stdout=buffer)
    assert code == EXIT_USAGE


@pytest.mark.integration
def test_run_id_and_result_files_name_their_own_references(smoke_run: Path, tmp_path: Path) -> None:
    directory = tmp_path / "run"
    shutil.copytree(smoke_run, directory)
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["run_id"] = "nyc-validated-staffing-plan"
    manifest["result_files"] = ["scenarios.csv"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    code, output = _cli("optimize", "inspect", str(directory))
    assert code == EXIT_FAILED
    report = next(
        r for r in json.loads(output)["reports"] if r["name"] == "optimization_inspection"
    )
    messages = [i["message"] for i in report["issues"] if i["code"] == "manifest_field_differs"]
    assert any("experiment id and its configuration and data fingerprints" in m for m in messages)
    assert any("the result files an M4 run writes" in m for m in messages)
    assert not any("'run_id' differs from the recorded metrics.json" in m for m in messages)


def test_a_symlinked_module_file_is_not_the_repositorys_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A tracked symlink for one module inside src/atlas can point outside git, where edits are
    # invisible to git status: such a layout never counts as the repository's package, even
    # when the package itself is imported from the repository.
    import atlas

    root = tmp_path / "repo"
    package = root / "src" / "atlas"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(atlas, "__file__", str(package / "__init__.py"))
    assert running_package_is_repository(root) is True
    outside = tmp_path / "outside.py"
    outside.write_text("", encoding="utf-8")
    (package / "optimization_runner.py").symlink_to(outside)
    assert running_package_is_repository(root) is False


def test_solve_lp_still_raises_when_no_method_concludes(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(*_args: Any, **_kwargs: Any) -> OptimizeResult:
        return OptimizeResult(status=4, message="(HiGHS Status 0: Not Set)", nit=0)

    monkeypatch.setattr(optimizer, "linprog", fake)
    lp = build_lp(PlanningProblem(date(2026, 7, 1), (_small_unit(),), None))
    with pytest.raises(OptimizationError, match="did not finish"):
        solve_lp(lp, replace(SETTINGS, presolve=False))
