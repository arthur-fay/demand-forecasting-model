"""Regression tests for the third review: scenario-analysis wiring and finite differences.

Each test pins a fix of commit a7ca6a6, or a round-2 fix that mutation testing found
unguarded, and fails when that fix is reverted: the without-target row and flags of a
binding or sub-request target, the tie-breaks of the policy comparisons, frontier points
and relaxed plan, the finite-difference status of a failed or unresolvable re-solve, the
shadow-price headline of a tied optimum, and the uniqueness test of the reported plan after
the tie-breaks.
"""

from __future__ import annotations

import io
import json
import math
from dataclasses import replace
from datetime import date
from typing import Any

import pandas as pd
import pytest

import atlas.decision_analysis as analysis
from atlas.config import load_settings
from atlas.decision_analysis import (
    RELAXED_TERMINAL_TARGET,
    ScenarioResult,
    analyse_scenario,
    evaluate_policy,
    frontier,
)
from atlas.decision_inputs import POINT_FORECAST, M3Status
from atlas.issues import Severity
from atlas.optimization_config import load_optimization_config
from atlas.optimization_report import (
    _dual_section,
    _face_section,
    _model_section,
    _scenario_section,
    render_optimization_report,
)
from atlas.optimization_runner import (
    OPTIMIZED,
    SELECTION,
    WITHOUT_TARGET,
    _plan_rows,
    _scenario_metrics,
    load_inputs,
    m4_outputs,
    scenario_frame,
)
from atlas.optimizer import Objective, SolverSettings
from atlas.planning import PlanningProblem, ServiceUnit
from atlas.policies import BACKLOG_TRIGGER
from atlas.runner import load_series
from atlas.scenarios import Scenario
from tests.conftest import REPO_ROOT

SETTINGS = SolverSettings()
SMOKE = REPO_ROOT / "configs" / "optimization" / "smoke.json"
STEPS = {"requests": 1.0, "staff_hours": 1.0, "usd": 100.0}
STATUS = M3Status(
    interval_status="not_decision_grade",
    decision_grade=False,
    statement="Synthetic status for directly built scenarios.",
    selected_pair="none",
    primary_model="none",
    exceedance={},
    data_fingerprint="0" * 64,
    provenance={},
)
FD_STATUSES = (
    "locally_unique",
    "breakpoint",
    "solver_failed",
    "domain_boundary_or_infeasible_side",
    "backward_unresolved",
)


def unit(**overrides: Any) -> ServiceUnit:
    base: dict[str, Any] = {
        "unit_id": "u",
        "demand": (10.0, 0.0),
        "initial_backlog": 0.0,
        "regular_hours": (0.0, 10.0),
        "regular_productivity": 1.0,
        "overtime_productivity": 1.0,
        "overtime_max_hours": (10.0, 10.0),
        "surge_max": (0.0, 0.0),
        "regular_wage": 1.0,
        "overtime_wage": 1.0,
        "surge_price": 5.0,
        "backlog_penalty": 1.0,
    }
    base.update(overrides)
    return ServiceUnit(**base)


def one_unit(budget: float | None = None, **overrides: Any) -> PlanningProblem:
    return PlanningProblem(date(2026, 7, 1), (unit(**overrides),), budget)


def scenario_of(scenario_id: str, problem: PlanningProblem) -> Scenario:
    total = math.fsum(d for u in problem.units for d in u.demand)
    return Scenario(
        scenario_id=scenario_id,
        description="Directly built scenario for the round-3 analysis tests.",
        demand_basis=POINT_FORECAST,
        quantile=None,
        adjustments={},
        problem=problem,
        triggers={u.unit_id: 0.0 for u in problem.units},
        demand_sha256="0" * 64,
        base_demand_total=total,
    )


def analyse(
    scenario_id: str, problem: PlanningProblem, steps: dict[str, float] | None = None
) -> ScenarioResult:
    return analyse_scenario(
        scenario_id,
        problem,
        SETTINGS,
        {u.unit_id: 0.0 for u in problem.units},
        shadow_steps=steps,
        frontier_points=None,
    )


def metrics_of(result: ScenarioResult) -> dict[str, Any]:
    return _scenario_metrics(result, scenario_of(result.scenario_id, result.problem))


def frame_of(*results: ScenarioResult) -> pd.DataFrame:
    scenarios = [scenario_of(r.scenario_id, r.problem) for r in results]
    return scenario_frame(results, scenarios, STATUS, forecast_mean=10.0)


def row_of(frame: pd.DataFrame, scenario_id: str, plan: str) -> pd.Series:
    rows = frame[(frame["scenario_id"] == scenario_id) & (frame["plan"] == plan)]
    assert len(rows) == 1
    return rows.iloc[0]


def flag(value: Any) -> bool | None:
    """A boolean cell of scenarios.csv (``None`` when empty)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return bool(value)


def section(lines: list[str], title: str) -> list[str]:
    """The lines of one ``## title`` section of a report."""
    start = lines.index(f"## {title}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), None)
    return lines[start:end]


def table_rows(lines: list[str], scenario_id: str) -> list[dict[str, str]]:
    """The cells of a scenario's rows in the first Markdown table of ``lines``."""
    header = next(line for line in lines if line.startswith("| scenario |"))
    names = [c.strip() for c in header.strip("|").split("|")]
    return [
        dict(zip(names, (c.strip() for c in line.strip("|").split("|")), strict=True))
        for line in lines
        if line.startswith(f"| `{scenario_id}` |")
    ]


def table(lines: list[str], scenario_id: str) -> dict[str, str]:
    """The cells of a scenario's only row in the first Markdown table of ``lines``."""
    (row,) = table_rows(lines, scenario_id)
    return row


# --- The without-target row: binding and sub-request targets ---------------------------------

BINDING_TARGETS: dict[str, tuple[dict[str, Any], tuple[float, float, float]]] = {
    # One day: overtime costs 1 USD per request, waiting 0.5. Without the target nothing is
    # bought (cash 0, 10 left, score 5); the target (at most 5 left) buys 5 hours.
    "one_day": (
        {
            "demand": (10.0,),
            "regular_hours": (0.0,),
            "overtime_max_hours": (10.0,),
            "surge_max": (0.0,),
            "backlog_penalty": 0.5,
            "terminal_backlog_max": 5.0,
        },
        (7.5, 5.0, 10.0),
    ),
    # Two days, overtime at 2 USD per request against a penalty of 1 per request-day: the
    # target (at most 1 left) costs 10 USD (score 11); without it nothing is bought and 6
    # requests are left (score 10).
    "two_days": (
        {
            "demand": (8.0, 6.0),
            "regular_hours": (4.0, 4.0),
            "overtime_max_hours": (10.0, 10.0),
            "overtime_wage": 2.0,
            "terminal_backlog_max": 1.0,
        },
        (11.0, 10.0, 6.0),
    ),
}


@pytest.mark.parametrize("case", sorted(BINDING_TARGETS))
def test_a_binding_target_changes_the_score_and_the_without_target_row(case: str) -> None:
    # test-strength-01: with `changes_plan` not set when the target changes the score, every
    # binding target took the "forces nothing" shortcut: the without-target row showed the
    # strict plan while metrics.json showed the no-target plan (the round-2 R3 defect).
    overrides, (strict_score, free_score, free_terminal) = BINDING_TARGETS[case]
    problem = one_unit(**overrides)
    result = analyse(case, problem)
    assert not result.problems
    assert result.strict.value == pytest.approx(strict_score)
    assert result.without_target.value == pytest.approx(free_score)
    assert result.target_changes_score is True
    assert result.target_changes_plan is True
    rows = {row[0]: row for row in _plan_rows(result, scenario_of(case, problem))}
    _id, feasibility, attempt, _plan, evaluation, _check = rows[WITHOUT_TARGET]
    assert feasibility == "terminal_target_dropped"
    assert attempt is result.without_target_plan
    assert evaluation is not None
    assert evaluation.discretionary_cost == pytest.approx(0.0, abs=1e-9)
    assert evaluation.terminal_backlog == pytest.approx(free_terminal)
    # metrics.json, scenarios.csv and the report describe the same no-target plan.
    metrics = metrics_of(result)
    assert metrics["terminal_target_changes_optimal_score"] is True
    assert metrics["terminal_target_changes_reported_plan"] is True
    free = metrics["without_terminal_target"]["evaluation"]
    assert free["discretionary_cost"] == pytest.approx(0.0, abs=1e-9)
    assert free["terminal_backlog"] == pytest.approx(free_terminal)
    row = row_of(frame_of(result), case, WITHOUT_TARGET)
    assert row["feasibility"] == "terminal_target_dropped"
    assert row["plan_selection"] == SELECTION[WITHOUT_TARGET]
    assert row["terminal_backlog"] == pytest.approx(free_terminal)
    target = problem.units[0].terminal_backlog_max
    assert target is not None
    assert row["terminal_violation"] == pytest.approx(free_terminal - target)
    sentence = f"- `{case}`: dropping the HYPOTHETICAL target changes the optimal score."
    assert sentence in _scenario_section([metrics])


@pytest.mark.parametrize("scale", [0.1, 1.0])
def test_a_target_that_trims_a_tied_face_changes_the_reported_plan_at_any_scale(
    scale: float,
) -> None:
    # test-strength-09: every overtime level in [0, 10 * scale] scores the same; the target
    # (at most 5 * scale left) excludes the cheaper half. At scale 0.1 the trim is half a
    # request, which a one-request threshold called "forces nothing". The metrics flag must
    # follow target_changes_plan (not target_changes_score), and the report has a sentence
    # for this case.
    problem = one_unit(
        demand=(10.0 * scale,),
        regular_hours=(0.0,),
        overtime_max_hours=(10.0 * scale,),
        surge_max=(0.0,),
        terminal_backlog_max=5.0 * scale,
    )
    result = analyse("trim", problem)
    assert not result.problems
    assert result.target_changes_score is False
    assert result.target_changes_plan is True
    assert result.reported.require().evaluation.discretionary_cost == pytest.approx(5.0 * scale)
    rows = {row[0]: row for row in _plan_rows(result, scenario_of("trim", problem))}
    _id, feasibility, _attempt, _plan, evaluation, _check = rows[WITHOUT_TARGET]
    assert feasibility == "terminal_target_dropped"
    assert evaluation is not None
    assert evaluation.discretionary_cost == pytest.approx(0.0, abs=1e-9)
    assert evaluation.terminal_backlog == pytest.approx(10.0 * scale)
    row = row_of(frame_of(result), "trim", WITHOUT_TARGET)
    assert row["feasibility"] == "terminal_target_dropped"
    assert row["terminal_violation"] == pytest.approx(5.0 * scale)
    metrics = metrics_of(result)
    assert metrics["terminal_target_changes_optimal_score"] is False
    assert metrics["terminal_target_changes_reported_plan"] is True
    assert (
        "- `trim`: dropping the HYPOTHETICAL target does not change the optimal score but "
        "changes the reported plan (equally scored plans with less cash or backlog miss the "
        "target)."
    ) in _scenario_section([metrics])


def test_a_target_the_tie_broken_plan_already_meets_forces_nothing() -> None:
    # Control for the two tests above: the no-target tie-broken plan (no overtime, 10 left)
    # meets a target of 10, so the target changes neither the score nor the plan, and the
    # row says so.
    problem = one_unit(
        demand=(10.0,),
        regular_hours=(0.0,),
        overtime_max_hours=(10.0,),
        surge_max=(0.0,),
        terminal_backlog_max=10.0,
    )
    result = analyse("slack", problem)
    assert result.target_changes_score is False
    assert result.target_changes_plan is False
    row = row_of(frame_of(result), "slack", WITHOUT_TARGET)
    assert row["feasibility"] == "same_optimal_score_target_forces_nothing"
    metrics = metrics_of(result)
    assert metrics["terminal_target_changes_reported_plan"] is False
    assert (
        "- `slack`: dropping the HYPOTHETICAL target changes neither the optimal score nor "
        "the reported plan."
    ) in _scenario_section([metrics])


# --- Tie-breaks outside the canonical solve ----------------------------------------------------


def test_policy_comparison_plans_are_tie_broken() -> None:
    # test-strength-02: without the tie-breaks the lp_same_cash / lp_same_backlog plans are
    # whichever optimal vertex HiGHS returns. The backlog-trigger policy spends 10 USD and
    # leaves 10 request-days and 5 requests. At that cash, day-1 surge (2 USD per request,
    # 2 request-days saved) and day-2 overtime (1 USD per request, 1 request-day saved) save
    # the same request-days per USD: the least request-days (10) and the score (20) leave
    # the mix open, from 4 surge requests on day 1 (7 left at the end) to 1.5 surge
    # requests and 5 overtime hours on day 2 (4.5 left). Both comparisons promise the least
    # terminal backlog among such plans (SELECTION), so both report 4.5.
    problem = one_unit(
        demand=(10.0, 10.0),
        initial_backlog=1.0,
        regular_hours=(2.0, 6.0),
        overtime_max_hours=(2.0, 5.0),
        surge_max=(5.0, 1.0),
        surge_price=2.0,
    )
    outcome = evaluate_policy(problem, SETTINGS, BACKLOG_TRIGGER, {"u": 5.0})
    assert not outcome.problems
    policy = outcome.evaluation
    assert policy.discretionary_cost == pytest.approx(10.0)
    assert policy.backlog_request_days == pytest.approx(10.0)
    assert policy.terminal_backlog == pytest.approx(5.0)
    assert outcome.least_request_days_at_policy_cash == pytest.approx(10.0)
    assert outcome.least_cash_at_policy_backlog == pytest.approx(10.0)
    # The reported plans are the last tie-break stages: least terminal backlog after least
    # cash (same cash), least request-days after least terminal backlog (same backlog).
    assert outcome.same_cash.objective is Objective.TERMINAL_BACKLOG
    assert outcome.same_backlog.objective is Objective.BACKLOG_DAYS
    for attempt in (outcome.same_cash, outcome.same_backlog):
        solved = attempt.require()
        assert solved.evaluation.discretionary_cost == pytest.approx(10.0)
        assert solved.evaluation.backlog_request_days == pytest.approx(10.0)
        assert solved.evaluation.penalised_score == pytest.approx(20.0)
        assert solved.evaluation.terminal_backlog == pytest.approx(4.5)
        assert solved.plan.units[0].overtime_hours == pytest.approx((2.0, 5.0))
        assert solved.plan.units[0].surge == pytest.approx((1.5, 0.0))


# 10 requests on day 1, regular capacity only on day 3, overtime at 1 USD per request on any
# day and only the terminal backlog penalised: the penalty does not care on which day the
# overtime is bought, the request-days do (buying on day 1 is least).
LATE_CAPACITY: dict[str, Any] = {
    "demand": (10.0, 0.0, 0.0),
    "regular_hours": (0.0, 0.0, 5.0),
    "overtime_max_hours": (10.0, 10.0, 10.0),
    "surge_max": (0.0, 0.0, 0.0),
    "backlog_penalty": 0.0,
}


def test_frontier_points_are_tie_broken_before_their_uniqueness_test() -> None:
    # test-strength-02 (frontier): with the tie-breaks cut to cash only, the points at caps
    # 2.5 and 5 bought overtime on day 3 (22.5 request-days instead of 17.5), and testing
    # the face of the penalty stage instead of the last stage called every point not unique.
    problem = one_unit(**LATE_CAPACITY, terminal_penalty=1.0)
    curve = frontier(problem, SETTINGS, 3, None)
    assert curve is not None
    assert not curve.problems
    assert curve.cash_for_least_penalty == {
        "kept": pytest.approx(5.0),
        "dropped": pytest.approx(5.0),
    }
    # cash cap -> (penalty, request-days, terminal backlog) of the tie-broken plan
    expected = {0.0: (5.0, 25.0, 5.0), 2.5: (2.5, 17.5, 2.5), 5.0: (0.0, 10.0, 0.0)}
    assert len(curve.points) == 6
    for point in curve.points:
        penalty, request_days, terminal = expected[round(point.cash_cap, 9)]
        assert point.penalty == pytest.approx(penalty, abs=1e-9)
        assert point.discretionary_cash == pytest.approx(point.cash_cap, abs=1e-9)
        assert point.request_days == pytest.approx(request_days)
        assert point.terminal_backlog == pytest.approx(terminal, abs=1e-9)
        assert point.overtime_hours == pytest.approx(point.cash_cap, abs=1e-9)
        assert point.unique_after_tie_breaks is True
        assert not point.totals_open()


def test_the_relaxed_plan_is_tie_broken_and_tested_after_the_tie_breaks() -> None:
    # test-strength-02 (relaxed plan): the same tie when the target (nothing left) is out of
    # the 2.5 USD budget. The relaxed plan promises least violation within the budget, then
    # the score, least cash, least terminal backlog and least request-days: its 2.5 overtime
    # hours are bought on day 1. Cut to cash only, they were bought on day 3.
    # integrity-of-claims-01: its uniqueness after the tie-breaks is tested too (the relaxed
    # optimum's face is not unique, the tie-broken plan is), also in scenarios.csv.
    problem = one_unit(2.5, **LATE_CAPACITY, terminal_penalty=0.0, terminal_backlog_max=0.0)
    result = analyse("relaxed", problem)
    assert not result.problems
    assert not result.strict.optimal
    assert result.diagnosis is not None
    relaxed = result.diagnosis.relaxed
    assert relaxed.objective is Objective.BACKLOG_DAYS
    solved = relaxed.require()
    assert solved.evaluation.discretionary_cost == pytest.approx(2.5)
    assert solved.evaluation.terminal_backlog == pytest.approx(2.5)
    assert solved.evaluation.backlog_request_days == pytest.approx(17.5)
    assert solved.plan.units[0].overtime_hours == pytest.approx((2.5, 0.0, 0.0), abs=1e-9)
    assert result.relaxed_face is not None
    assert result.relaxed_face.unique is False
    assert result.tie_broken_face is not None
    assert result.tie_broken_face.unique is True
    row = row_of(frame_of(result), "relaxed", RELAXED_TERMINAL_TARGET)
    assert flag(row["unique_optimal_plan"]) is False
    assert flag(row["unique_after_tie_breaks"]) is True
    metrics = metrics_of(result)
    assert metrics["optimal_face"]["unique"] is False
    assert metrics["reported_plan_after_tie_breaks"]["unique"] is True


# --- Finite-difference status -------------------------------------------------------------------

# Three days with a budget and a target: 14 duals, every right-hand side 10 or more (so a
# re-solve's step tells the local secant, 0.001, from the practical one, 1 or 100).
FD_UNIT: dict[str, Any] = {
    "demand": (100.0, 120.0, 90.0),
    "initial_backlog": 50.0,
    "regular_hours": (40.0, 40.0, 40.0),
    "regular_productivity": 2.0,
    "overtime_productivity": 2.0,
    "overtime_max_hours": (10.0, 10.0, 10.0),
    "surge_max": (20.0, 20.0, 20.0),
    "regular_wage": 10.0,
    "overtime_wage": 30.0,
    "surge_price": 20.0,
    "backlog_penalty": 1.5,
    "terminal_backlog_max": 60.0,
}
FAILED_SLOPE = {
    "local_backward": "backward",
    "practical_forward": "practical_forward",
    "practical_backward": "practical_backward",
}


@pytest.mark.parametrize("failing", sorted(FAILED_SLOPE))
def test_a_slope_any_re_solve_could_not_compute_is_solver_failed(
    monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    # test-strength-10 / diagnosis-fd-duals-04: the status tested the local slopes only (and,
    # mutated, the forward one only). A failed backward or practical re-solve then gave
    # "breakpoint" or "locally_unique", and metrics counted no solver failure.
    real = analysis._value_at

    def value_at(lp: Any, settings: Any, what: str, problems: list[str]) -> Any:
        token = what.rsplit(" ", 1)[-1]  # "+0.001", "-1", "+100", ...
        size = "local" if float(token[1:]) < 0.5 else "practical"
        if f"{size}_{'forward' if token[0] == '+' else 'backward'}" == failing:
            problems.append(f"{what}: injected solver failure")
            return math.nan, None
        return real(lp, settings, what, problems)

    monkeypatch.setattr(analysis, "_value_at", value_at)
    result = analyse("fd", PlanningProblem(date(2026, 7, 1), (unit(**FD_UNIT),), 5000.0), STEPS)
    differences = result.finite_differences
    assert len(differences) == 14

    def nan(fd: Any) -> bool:
        value = getattr(fd, FAILED_SLOPE[failing])
        return value is not None and math.isnan(value)

    failed = [fd for fd in differences if nan(fd)]
    assert len(failed) == len(differences)  # every dual has both sides here
    assert all(fd.status == "solver_failed" for fd in failed)
    assert all(fd.problems for fd in failed)
    prices = metrics_of(result)["shadow_prices"]
    assert prices["solver_failed"] == len(failed)
    assert prices["locally_unique"] == prices["breakpoint"] == 0
    assert sum(prices[name] for name in FD_STATUSES) == prices["n"]
    line = next(x for x in _dual_section([metrics_of(result)]) if x.startswith("- `fd`:"))
    assert f"{len(failed)} not computed (solver failure)" in line


def test_without_failures_the_same_duals_have_slope_statuses() -> None:
    # Control for the test above: the same instance without injected failures has no
    # solver_failed status, so the statuses above come from the failures.
    result = analyse("fd", PlanningProblem(date(2026, 7, 1), (unit(**FD_UNIT),), 5000.0), STEPS)
    assert not result.problems
    statuses = [fd.status for fd in result.finite_differences]
    assert len(statuses) == 14
    assert set(statuses) <= {"locally_unique", "breakpoint"}


def _kink(tiny: float, big: float) -> PlanningProblem:
    # Day 1's demand equals its regular capacity (tiny). Above it the excess waits both
    # nights at 1.5 USD each (overtime, 30 USD per request, is never worth it): slope 3.
    # Below it capacity idles: slope 0. Day 2 leaves big / 2 waiting, so the optimum (and
    # its rounding noise) is large.
    return one_unit(
        demand=(tiny, big),
        regular_hours=(tiny, big / 2),
        overtime_productivity=2.0,
        overtime_max_hours=(0.0, big),
        overtime_wage=60.0,
        surge_price=1.0,
        backlog_penalty=1.5,
    )


@pytest.mark.parametrize(("tiny", "big"), [(1e-8, 1e7), (1e-9, 1e8)])
def test_a_kink_at_a_right_hand_side_far_below_the_step_is_backward_unresolved(
    tiny: float, big: float
) -> None:
    # diagnosis-fd-duals-05: the backward secant can step down only by `tiny`, and the
    # rounding noise of the optimum divided by that step (21 to 2,000 USD per request)
    # exceeds any slope gap, so the agreement test called the kink (0 against 3) locally
    # unique. Such a secant cannot resolve a slope: its status is backward_unresolved, it is
    # counted under that name and the report says so.
    result = analyse("kink", _kink(tiny, big), STEPS)
    assert not result.problems
    fd = next(i for i in result.finite_differences if i.dual.key() == ("balance", "u", 0))
    assert fd.dual.rhs == pytest.approx(tiny)
    assert fd.forward == pytest.approx(3.0, rel=1e-4)
    assert fd.practical_forward == pytest.approx(3.0, rel=1e-4)
    assert fd.backward_unresolved is True
    assert fd.locally_unique is False
    assert fd.status == "backward_unresolved"
    assert fd.bracket_ok
    assert fd.convex_ok
    assert fd.sign_ok
    statuses = [i.status for i in result.finite_differences]
    metrics = metrics_of(result)
    prices = metrics["shadow_prices"]
    assert prices["backward_unresolved"] == statuses.count("backward_unresolved") >= 1
    assert prices["locally_unique"] == statuses.count("locally_unique")
    assert sum(prices[name] for name in FD_STATUSES) == prices["n"] == len(statuses)
    line = next(x for x in _dual_section([metrics]) if x.startswith("- `kink`:"))
    assert (
        f"{prices['backward_unresolved']} with a backward step too small to resolve the "
        "slope (a right-hand side below the step)"
    ) in line


def test_a_kink_above_the_step_is_still_a_breakpoint() -> None:
    # Control: with day 1 at 0.01 requests the backward step is the full local step, the
    # secant resolves the kink and it is a breakpoint, not backward_unresolved.
    result = analyse("kink", _kink(0.01, 1e7), STEPS)
    assert not result.problems
    fd = next(i for i in result.finite_differences if i.dual.key() == ("balance", "u", 0))
    assert fd.backward_unresolved is False
    assert fd.backward == pytest.approx(0.0, abs=1e-3)
    assert fd.forward == pytest.approx(3.0, rel=1e-4)
    assert fd.status == "breakpoint"


# --- Headline shadow prices ---------------------------------------------------------------------


def test_the_shadow_price_headline_describes_the_reported_plan() -> None:
    # test-strength-11: the metrics.json headline took `binding` from the solver's vertex (the
    # round-2 M4-INT-06 split). Every overtime level in [0, 10] on day 1 scores 10; the
    # reported plan buys none, so neither the 10 USD budget nor the 10-hour overtime total
    # binds, whichever vertex HiGHS returned.
    result = analyse("tied", one_unit(10.0, overtime_total_hours=10.0), STEPS)
    assert not result.problems
    assert result.reported.require().evaluation.discretionary_cost == pytest.approx(0.0, abs=1e-9)
    reported = {d.key(): d for d in result.duals}
    for key in (("budget", "", -1), ("overtime_total_limit", "u", -1)):
        assert reported[key].binding is False
        assert reported[key].slack == pytest.approx(10.0)

    def headline(r: ScenarioResult) -> dict[str, bool]:
        rows = metrics_of(r)["shadow_prices"]["headline"]
        return {h["constraint"]: h["binding"] for h in rows}

    assert headline(result) == {"budget": False, "overtime_total_limit": False}
    # The finite differences keep the duals of the vertex; where that vertex binds (the one
    # spending the budget), the headline must still describe the reported plan.
    at_vertex = replace(
        result,
        finite_differences=tuple(
            replace(fd, dual=replace(fd.dual, binding=True)) for fd in result.finite_differences
        ),
    )
    assert headline(at_vertex) == {"budget": False, "overtime_total_limit": False}
    rows = table_rows(_dual_section([metrics_of(at_vertex)]), "tied")
    assert {(r["constraint"], r["binding"]) for r in rows} == {
        ("budget", "no"),
        ("overtime_total_limit", "no"),
    }


# --- Uniqueness of the reported plan after the tie-breaks ---------------------------------------


def test_the_reported_plan_is_tested_for_uniqueness_after_the_tie_breaks() -> None:
    # integrity-of-claims-01: the report said "so every reported total is determined" without
    # testing the reported plan. `split`: overtime and surge both cost 1 USD per request and
    # all 10 requests are served on day 1 by any mix, so cash, terminal backlog and
    # request-days are determined but the overtime/surge split is not. `resolved`: every
    # overtime level ties on the score, and least cash (none) picks one plan. `unique`:
    # overtime is worth buying in full.
    split = analyse(
        "split",
        one_unit(
            overtime_max_hours=(10.0, 0.0),
            surge_max=(10.0, 0.0),
            surge_price=1.0,
            backlog_penalty=2.0,
        ),
    )
    resolved = analyse("resolved", one_unit())
    unique = analyse("unique", one_unit(backlog_penalty=2.0))
    results = (split, resolved, unique)
    # scenario -> (optimal face unique, unique after the tie-breaks)
    expected = {"split": (False, False), "resolved": (False, True), "unique": (True, True)}
    for result in results:
        assert not result.problems
        assert result.face is not None
        assert result.tie_broken_face is not None
        assert (result.face.unique, result.tie_broken_face.unique) == expected[result.scenario_id]
    assert split.tie_broken_face is not None
    ranges = {r.quantity: (r.low, r.high) for r in split.tie_broken_face.ranges}
    assert ranges["discretionary_cash"] == pytest.approx((10.0, 10.0))
    assert ranges["terminal_backlog"] == pytest.approx((0.0, 0.0), abs=1e-9)
    assert ranges["overtime_hours"] == pytest.approx((0.0, 10.0), abs=1e-9)
    assert ranges["surge_requests"] == pytest.approx((0.0, 10.0), abs=1e-9)
    # scenarios.csv: only the reported plan's row carries the verdicts.
    frame = frame_of(*results)
    for scenario_id, (face, after) in expected.items():
        row = row_of(frame, scenario_id, OPTIMIZED)
        assert flag(row["unique_optimal_plan"]) is face
        assert flag(row["unique_after_tie_breaks"]) is after
        others = frame[(frame["scenario_id"] == scenario_id) & (frame["plan"] != OPTIMIZED)]
        assert others["unique_after_tie_breaks"].isna().all()
    # metrics.json and the report's uniqueness table.
    metrics = [metrics_of(result) for result in results]
    lines = _face_section(metrics)
    for m in metrics:
        face, after = expected[m["scenario_id"]]
        assert m["optimal_face"]["unique"] is face
        assert m["reported_plan_after_tie_breaks"]["unique"] is after
        cells = table(lines, m["scenario_id"])
        assert cells["unique"] == ("yes" if face else "no")
        assert cells["unique after tie-breaks"] == ("yes" if after else "no")
    model = " ".join(_model_section())
    assert "every reported total is determined" not in model
    assert "the reported cash, terminal backlog and request-days are determined" in model
    assert "`unique after tie-breaks`" in model


# --- The smoke run with overtime priced like surge --------------------------------------------


@pytest.fixture(scope="module")
def smoke_tied(tmp_path_factory: pytest.TempPathFactory) -> Any:
    # Overtime at 61.6 USD per staff hour and 2.2 requests per hour costs 28 USD per
    # request, the surge price: every reported plan leaves its overtime/surge split open.
    raw = json.loads(SMOKE.read_text(encoding="utf-8"))
    (service_unit,) = raw["service_units"]
    service_unit["overtime_wage"]["value"] = 61.6
    path = tmp_path_factory.mktemp("smoke_tied") / "smoke_tied.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_optimization_config(path)
    settings = load_settings(repo_root=REPO_ROOT, env={})
    _, data = load_series(settings, "review_round3_analysis")
    assert data is not None
    return m4_outputs(settings, config, load_inputs(settings, config, data))


@pytest.mark.integration
def test_a_run_flags_reported_plans_whose_split_is_open(smoke_tied: Any) -> None:
    # integrity-of-claims-01 end to end: scenarios.csv, metrics.json and report.md agree that
    # no reported plan is unique after the tie-breaks, and no check fails.
    assert not [i for i in smoke_tied.issues if i.severity is Severity.ERROR]
    payload = json.loads(smoke_tied.files["metrics.json"])
    frame = pd.read_csv(io.BytesIO(smoke_tied.files["scenarios.csv"]))
    report = render_optimization_report(payload).splitlines()
    uniqueness = section(report, "Is the optimal plan unique?")
    assert len(payload["scenarios"]) == 4
    for scenario in payload["scenarios"]:
        scenario_id = scenario["scenario_id"]
        after = scenario["reported_plan_after_tie_breaks"]
        assert after["unique"] is False
        hours = next(r for r in after["ranges"] if r["quantity"] == "overtime_hours")
        assert hours["high"] - hours["low"] > 1.0
        plan = OPTIMIZED if scenario["feasibility"] == "strict" else RELAXED_TERMINAL_TARGET
        rows = frame[frame["scenario_id"] == scenario_id]
        assert flag(row_of(frame, scenario_id, plan)["unique_after_tie_breaks"]) is False
        assert rows[rows["plan"] != plan]["unique_after_tie_breaks"].isna().all()
        assert table(uniqueness, scenario_id)["unique after tie-breaks"] == "no"
    assert not any("every reported total is determined" in line for line in report)


@pytest.mark.integration
def test_a_run_shows_the_no_target_plan_for_binding_targets(smoke_tied: Any) -> None:
    # test-strength-01 end to end: in `normal` and `delayed_surge` the target changes the
    # score; their without-target rows in scenarios.csv must be the no-target plan of
    # metrics.json, not the strict plan labelled "forces nothing".
    payload = json.loads(smoke_tied.files["metrics.json"])
    frame = pd.read_csv(io.BytesIO(smoke_tied.files["scenarios.csv"]))
    binding = [s for s in payload["scenarios"] if s["terminal_target_changes_optimal_score"]]
    assert sorted(s["scenario_id"] for s in binding) == ["delayed_surge", "normal"]
    for scenario in binding:
        assert scenario["terminal_target_changes_reported_plan"] is True
        free = scenario["without_terminal_target"]["evaluation"]
        row = row_of(frame, scenario["scenario_id"], WITHOUT_TARGET)
        assert row["feasibility"] == "terminal_target_dropped"
        assert row["terminal_violation"] > 1.0
        assert row["terminal_backlog"] == pytest.approx(free["terminal_backlog"])
        assert row["discretionary_cost"] == pytest.approx(free["discretionary_cost"], abs=1e-9)
