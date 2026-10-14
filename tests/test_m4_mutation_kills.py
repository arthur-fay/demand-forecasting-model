"""Tests that kill the mutants which survived the independent mutation review of M4.

The review planted 108 single bugs in the M4 modules, one at a time. Every test here fails
on one of the mutants that the rest of the suite let through (named in each docstring),
and passes on the real code: guards whose failure path was never exercised, tolerances
that were not pinned, and checks that could have been switched off unnoticed.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import atlas.decision_analysis as da
import atlas.decision_inputs as di
from atlas.config import load_settings
from atlas.decision_analysis import (
    MONOTONE,
    analyse_scenario,
    finite_differences,
    frontier,
    optimal_face,
    run_sweep,
    solve_problem,
)
from atlas.errors import ConfigError
from atlas.optimization_config import load_optimization_config
from atlas.optimization_runner import load_inputs
from atlas.optimizer import certify, dual_values, to_plan
from atlas.planning import Plan, PlanningProblem, ServiceUnit, UnitPlan
from atlas.probabilistic import DECISION_GRADE
from atlas.runner import load_series
from tests.conftest import REPO_ROOT
from tests.test_m4_analysis import (
    SETTINGS,
    STEPS,
    frontier_problem,
    make_problem,
    make_unit,
    sweep_problem,
)
from tests.test_m4_runner_cli import (
    EXIT_FAILED,
    EXIT_USAGE,
    SMOKE,
    issue_codes,
    optimization_report,
    run_cli,
)


def case_1a() -> PlanningProblem:
    # One day: 10 requests, 6 of regular capacity, 2 overtime hours at 3 USD, expensive surge.
    return make_problem(
        make_unit(
            demand=[10],
            regular_hours=[6],
            overtime_max=[2],
            overtime_wage=3.0,
            surge_max=[5],
            surge_price=100.0,
            backlog_penalty=5.0,
        )
    )


# --- Optimizer: snapping, certificate tolerances, binding flags ------------------------------


def test_value_far_above_an_upper_bound_is_not_snapped() -> None:
    """Mutant: snap any value above its upper bound, however far (hides a violation)."""
    solved = solve_problem(case_1a(), SETTINGS).require()
    x = solved.x.copy()
    j = solved.lp.layout.index(0, "overtime_hours", 0)
    x[j] = solved.lp.upper[j] + 1e-3
    plan, cleaning = to_plan(solved.lp, x)
    assert plan.units[0].overtime_hours[0] == pytest.approx(2.001)
    assert cleaning.values_adjusted == 0


def test_small_wrong_sign_row_dual_fails_the_certificate() -> None:
    """Mutant: dual-sign tolerance loosened from 1e-9 to 1e-3 of max |c|."""
    solved = solve_problem(case_1a(), SETTINGS).require()
    assert solved.result.ub_marginals is not None
    y = solved.result.ub_marginals.copy()
    y[0] = 1e-5  # capacity row: the dual of a "<=" row must be <= 0
    assert certify(solved.lp, replace(solved.result, ub_marginals=y)).dual_sign > 1.0


def test_dual_on_an_infinite_upper_bound_fails_the_certificate() -> None:
    """Mutant: no check of upper-bound duals on infinite bounds (stationarity and the gap
    are unchanged by this tampering, so only that check catches it)."""
    solved = solve_problem(case_1a(), SETTINGS).require()
    result = solved.result
    assert result.upper_marginals is not None
    assert result.lower_marginals is not None
    j = solved.lp.layout.index(0, "served", 0)
    y_up, y_lo = result.upper_marginals.copy(), result.lower_marginals.copy()
    y_up[j] -= 1e-3
    y_lo[j] += 1e-3
    certificate = certify(solved.lp, replace(result, upper_marginals=y_up, lower_marginals=y_lo))
    assert certificate.stationarity <= 1.0
    assert certificate.duality_gap <= 1.0
    assert not certificate.holds


def test_half_a_unit_of_slack_is_not_binding() -> None:
    """Mutants: rows or daily limits called binding with up to one unit of slack."""
    problem = make_problem(
        make_unit(
            demand=[10],
            regular_hours=[6],
            overtime_max=[4.5],
            overtime_wage=3.0,
            backlog_penalty=5.0,
            overtime_total=4.5,
        ),
        budget=12.5,
    )
    solved = solve_problem(problem, SETTINGS).require()
    assert solved.plan.units[0].overtime_hours[0] == pytest.approx(4.0)
    duals = {d.key(): d for d in dual_values(solved.lp, solved.result)}
    assert not duals[("overtime_daily_limit", "u", 0)].binding
    assert not duals[("budget", "", -1)].binding
    assert not duals[("overtime_total_limit", "u", -1)].binding


# --- Optimal face ----------------------------------------------------------------------------


def test_tight_rows_count_in_the_uniqueness_test() -> None:
    """Mutant: the active-set distance counts bounds only (another optimal plan moves only
    off tight rows here, 3 requests away)."""
    unit = ServiceUnit(
        "a",
        (5.0, 6.0),
        3.0,
        (7.0, 4.0),
        1.0,
        2.0,
        (2.0, 3.0),
        (6.0, 4.0),
        10.0,
        1.0,
        2.0,
        0.0,
        0.0,
        None,
        2.0,
        9.0,
    )
    problem = PlanningProblem(date(2024, 1, 1), (unit,), 1.0)
    strict = solve_problem(problem, SETTINGS).require()
    face = optimal_face(problem, SETTINGS, strict)
    assert face.unique is False
    assert face.max_active_distance == pytest.approx(3.0, abs=1e-4)


def test_daily_extra_capacity_ranges_include_surge() -> None:
    """Mutant: the per-day range measures overtime only."""
    problem = make_problem(
        make_unit(
            demand=[0, 0],
            initial_backlog=10.0,
            surge_max=[10, 10],
            surge_price=1.0,
            terminal_penalty=100.0,
        )
    )
    strict = solve_problem(problem, SETTINGS).require()
    face = optimal_face(problem, SETTINGS, strict)
    assert face.unique is False
    for _, _, low, high in face.daily_extra_capacity:
        assert low == pytest.approx(0.0, abs=1e-4)
        assert high == pytest.approx(10.0, abs=1e-4)
    assert face.undetermined_days() == 2


# --- Checks of every solve -------------------------------------------------------------------


def test_strict_plan_is_checked_against_the_target_and_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutant: the independent plan check always ignores the terminal target and budget."""
    seen: list[list[str]] = []
    real = da.check_plan

    def spy(problem: Any, plan: Any, ignore: Any = ()) -> Any:
        seen.append(list(ignore))
        return real(problem, plan, ignore=ignore)

    monkeypatch.setattr(da, "check_plan", spy)
    solve_problem(case_1a(), SETTINGS)
    assert seen == [[]]


def test_objective_disagreement_is_a_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutant: the solver objective is never compared with the recomputed score."""
    real = da.evaluate_plan

    def off_by_one(problem: Any, plan: Any) -> Any:
        evaluation = real(problem, plan)
        return replace(evaluation, penalised_score=evaluation.penalised_score + 1.0)

    monkeypatch.setattr(da, "evaluate_plan", off_by_one)
    attempt = solve_problem(case_1a(), SETTINGS)
    assert any("differs from the value recomputed" in p for p in attempt.problems)


# --- Shadow prices ---------------------------------------------------------------------------


def _balance_difference(monkeypatch: pytest.MonkeyPatch, marginal: float) -> Any:
    real = da.dual_values

    def tampered(lp: Any, result: Any) -> Any:
        return tuple(
            replace(d, marginal=marginal) if d.constraint == "balance" else d
            for d in real(lp, result)
        )

    monkeypatch.setattr(da, "dual_values", tampered)
    strict = solve_problem(case_1a(), SETTINGS).require()
    differences = finite_differences(strict, SETTINGS, STEPS)
    return next(i for i in differences if i.dual.constraint == "balance")


def test_dual_below_the_backward_secant_fails_the_bracket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutant: the lower side of the bracket (dual >= backward secant) is dropped."""
    item = _balance_difference(monkeypatch, 0.0)  # the true dual is 5 = backward = forward
    assert item.backward == pytest.approx(5.0, abs=1e-4)
    assert not item.bracket_ok


def test_negative_balance_dual_fails_the_sign_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutant: the balance-dual sign check always passes."""
    assert not _balance_difference(monkeypatch, -1.0).sign_ok


def test_convexity_failure_of_a_bracket_is_a_scenario_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutant: only bracket_ok and sign_ok, not convex_ok, become scenario problems."""
    real = da.finite_differences

    def non_convex(*args: Any, **kwargs: Any) -> Any:
        return tuple(replace(item, convex_ok=False) for item in real(*args, **kwargs))

    monkeypatch.setattr(da, "finite_differences", non_convex)
    result = analyse_scenario(
        "s", case_1a(), SETTINGS, {"u": 0.0}, shadow_steps=STEPS, frontier_points=None
    )
    assert any("outside its finite-difference bracket" in p for p in result.problems)


# --- Sweeps, frontier, policies --------------------------------------------------------------


def test_violated_monotonicity_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutant: a monotonicity check that always holds."""
    monkeypatch.setitem(MONOTONE, "demand_multiplier", (("penalised_score", "down"),))
    _, checks = run_sweep(sweep_problem(), SETTINGS, "demand_multiplier", [0.5, 1.0, 1.5])
    score = next(c for c in checks if c.quantity == "penalised_score")
    assert score.worst_violation > 0
    assert score.holds is False


def test_strict_plan_off_the_frontier_fails_the_check() -> None:
    """Mutant: the on-frontier check always passes."""
    problem = frontier_problem()
    strict = solve_problem(problem, SETTINGS).require()
    off = replace(
        strict,
        evaluation=replace(
            strict.evaluation, backlog_penalty=strict.evaluation.backlog_penalty + 1.0
        ),
    )
    curve = frontier(problem, SETTINGS, 5, off)
    assert curve is not None
    assert curve.on_frontier_check == "failed"
    assert "the strict optimum does not lie on the cash/penalty frontier" in curve.problems


def test_a_budget_below_the_least_cash_is_a_frontier_point() -> None:
    """Mutant: the configured-budget point is never added to the frontier."""
    problem = replace(frontier_problem(), discretionary_budget=12.0)
    strict = solve_problem(problem, SETTINGS).require()
    curve = frontier(problem, SETTINGS, 5, strict)
    assert curve is not None
    budget_points = [p for p in curve.points if p.marker == "configured_budget"]
    assert {p.terminal_target for p in budget_points} == {"kept", "dropped"}
    assert all(p.cash_cap == 12.0 for p in budget_points)


def test_strict_optimum_worse_than_a_feasible_policy_is_a_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutant: the "LP no worse than any feasible policy" check is switched off."""
    real = da.evaluate_policy

    def better(*args: Any, **kwargs: Any) -> Any:
        outcome = real(*args, **kwargs)
        return replace(outcome, evaluation=replace(outcome.evaluation, penalised_score=-1.0))

    monkeypatch.setattr(da, "evaluate_policy", better)
    problem = make_problem(
        make_unit(demand=[4], regular_hours=[6], overtime_max=[2], backlog_penalty=1.0)
    )
    result = analyse_scenario(
        "s", problem, SETTINGS, {"u": 0.0}, shadow_steps=None, frontier_points=None
    )
    assert any("scores worse than feasible policy" in p for p in result.problems)


def test_a_policy_over_budget_is_a_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutant: budget violations of a policy plan are ignored like the terminal target."""

    def overspend(problem: Any, triggers: Any) -> Plan:
        return Plan((UnitPlan("u", (8.0,), (2.0,), (0.0,), (2.0,)),))

    monkeypatch.setattr(da, "backlog_trigger_plan", overspend)
    problem = make_problem(
        make_unit(
            demand=[10], regular_hours=[6], overtime_max=[2], overtime_wage=3.0, backlog_penalty=5.0
        ),
        budget=3.0,
    )
    outcome = da.evaluate_policy(problem, SETTINGS, "backlog_trigger", {"u": 0.0})
    assert any("violates ['budget']" in p for p in outcome.problems)


def test_lp_worse_than_the_policy_at_the_same_cash_is_a_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutant: the "LP at the policy's cash has no more backlog" check is switched off."""
    real = da.solve_lexicographic
    calls: list[int] = []

    def worse(*args: Any, **kwargs: Any) -> Any:
        first, second = real(*args, **kwargs)
        calls.append(1)
        if len(calls) == 1:  # the same-cash comparison: least request-days first
            first = replace(first, solved=replace(first.require(), objective_value=1e9))
        return first, second

    monkeypatch.setattr(da, "solve_lexicographic", worse)
    outcome = da.evaluate_policy(case_1a(), SETTINGS, "fixed_capacity", {"u": 0.0})
    assert any("the LP at the same cash has more backlog" in p for p in outcome.problems)


# --- Inputs and the runner -------------------------------------------------------------------


def test_decision_grade_needs_the_decision_grade_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mutant: the decision-grade flag is read without the interval status."""
    source = REPO_ROOT / "docs" / "results" / "atlas-m3-intervals-v1" / "full"
    record = json.loads((source / "calibration_status.json").read_text(encoding="utf-8"))
    run = tmp_path / "m3"
    run.mkdir()
    (run / "metrics.json").write_text(json.dumps({"interval_summary": []}), encoding="utf-8")
    monkeypatch.setattr(di, "_status_source", lambda path, rec: {"record_type": "test"})
    for interval_status, expected in (("failed_calibration", False), (DECISION_GRADE, True)):
        record["decision_status"]["decision_grade"] = True
        record["decision_status"]["interval_status"] = interval_status
        (run / "calibration_status.json").write_text(json.dumps(record), encoding="utf-8")
        status = di.load_m3_status(tmp_path, "m3/calibration_status.json")
        assert status.decision_grade is expected


def test_projection_on_other_snapshot_data_is_refused() -> None:
    """Mutant: the M2 projection is not tied to the snapshot just validated."""
    config = load_optimization_config(SMOKE)
    settings = load_settings(repo_root=REPO_ROOT, env={})
    _, data = load_series(settings, "pins")
    assert data is not None
    other = dataclasses.replace(data, provenance={**data.provenance, "data_fingerprint": "0" * 64})
    with pytest.raises(ConfigError, match="different data than the snapshot"):
        load_inputs(settings, config, other)


@pytest.mark.integration
def test_violated_monotonicity_marks_the_run_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mutant: a violated monotonicity never becomes an ERROR."""
    monkeypatch.setitem(MONOTONE, "demand_multiplier", (("penalised_score", "down"),))
    target = tmp_path / "monotone"
    code, output = run_cli("optimize", "run", "--config", str(SMOKE), "--output-dir", str(target))
    assert code == EXIT_FAILED
    assert "monotonicity_violated" in issue_codes(optimization_report(output), "error")
    assert json.loads((target / "manifest.json").read_bytes())["complete"] is False


@pytest.mark.integration
def test_verify_detects_a_report_that_no_longer_reproduces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mutant: verify treats every recomputed file as identical (report.md is outside the
    result fingerprint, and the copy on disk is intact)."""
    target = tmp_path / "run"
    code, _ = run_cli("optimize", "run", "--config", str(SMOKE), "--output-dir", str(target))
    assert code == 0
    monkeypatch.setattr(
        "atlas.optimization_runner.render_optimization_report", lambda payload: "changed\n"
    )
    code, output = run_cli("optimize", "verify", str(target))
    assert code == EXIT_FAILED
    report = optimization_report(output, "optimization_verification")
    assert issue_codes(report, "error") == ["output_differs"]
    assert report["facts"]["files"]["report.md"] == "different"


def test_require_clean_refuses_when_git_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Mutant: --require-clean accepts a run when git is unavailable."""
    monkeypatch.setattr("atlas.optimization_runner.git_info", lambda _root: {"available": False})
    code, _ = run_cli(
        "optimize",
        "run",
        "--config",
        str(SMOKE),
        "--output-dir",
        str(tmp_path / "x"),
        "--require-clean",
    )
    assert code == EXIT_USAGE
    assert "git is unavailable" in capsys.readouterr().err
