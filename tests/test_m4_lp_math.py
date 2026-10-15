"""Mathematical verification of the M4 linear programme (atlas.optimizer, solve_problem).

Every expected optimum below is derived by hand (the derivation is in a comment next to
the test) or by an independent method: a separately written formulation of the model and
SciPy's interior-point HiGHS solver, policy plans and random feasible plans. The code under
test is never used to compute its own expected value.
"""

from __future__ import annotations

import math
import re
import warnings
from dataclasses import replace
from datetime import date
from typing import Any

import numpy as np
import pytest
import scipy
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from scipy.optimize import OptimizeResult, OptimizeWarning
from scipy.optimize import linprog as scipy_linprog

from atlas import optimizer
from atlas.decision_analysis import Attempt, Solved, solve_problem
from atlas.optimizer import (
    BLOCKS,
    ROW_UNITS,
    ExtraRow,
    LinearProgram,
    Objective,
    OptimizationError,
    Row,
    SolverSettings,
    SolveStatus,
    Terminal,
    _check_magnitudes,
    build_lp,
    certify,
    dual_values,
    plan_vector,
    solve_lp,
    solver_versions,
    to_plan,
    violation_name,
)
from atlas.planning import Plan, PlanningProblem, ServiceUnit, UnitPlan, check_plan
from atlas.policies import backlog_trigger_plan, fixed_capacity_plan

SETTINGS = SolverSettings()
START = date(2024, 1, 1)
EXACT = 1e-9
TOL = 1e-7


def _days(value: float | tuple[float, ...], horizon: int) -> tuple[float, ...]:
    if isinstance(value, tuple):
        assert len(value) == horizon
        return value
    return tuple(float(value) for _ in range(horizon))


def make_unit(
    demand: tuple[float, ...],
    *,
    unit_id: str = "u",
    initial_backlog: float = 0.0,
    regular_hours: float | tuple[float, ...] = 6.0,
    regular_productivity: float = 1.0,
    overtime_productivity: float = 1.0,
    overtime_max_hours: float | tuple[float, ...] = 0.0,
    surge_max: float | tuple[float, ...] = 0.0,
    regular_wage: float = 10.0,
    overtime_wage: float = 3.0,
    surge_price: float = 100.0,
    backlog_penalty: float = 5.0,
    terminal_penalty: float = 0.0,
    overtime_total_hours: float | None = None,
    surge_total: float | None = None,
    terminal_backlog_max: float | None = None,
) -> ServiceUnit:
    horizon = len(demand)
    return ServiceUnit(
        unit_id=unit_id,
        demand=tuple(float(d) for d in demand),
        initial_backlog=initial_backlog,
        regular_hours=_days(regular_hours, horizon),
        regular_productivity=regular_productivity,
        overtime_productivity=overtime_productivity,
        overtime_max_hours=_days(overtime_max_hours, horizon),
        surge_max=_days(surge_max, horizon),
        regular_wage=regular_wage,
        overtime_wage=overtime_wage,
        surge_price=surge_price,
        backlog_penalty=backlog_penalty,
        terminal_penalty=terminal_penalty,
        overtime_total_hours=overtime_total_hours,
        surge_total=surge_total,
        terminal_backlog_max=terminal_backlog_max,
    )


def make_problem(*units: ServiceUnit, budget: float | None = None) -> PlanningProblem:
    return PlanningProblem(start=START, units=tuple(units), discretionary_budget=budget)


def score_by_hand(problem: PlanningProblem, plan: Plan) -> float:
    """sum(c_ot o + c_s s) + w sum(b) + w_T b[T], in plain loops."""
    total = 0.0
    for unit, decisions in zip(problem.units, plan.units, strict=True):
        for day in range(problem.horizon):
            total += unit.overtime_wage * decisions.overtime_hours[day]
            total += unit.surge_price * decisions.surge[day]
            total += unit.backlog_penalty * decisions.backlog[day]
        total += unit.terminal_penalty * decisions.backlog[-1]
    return total


def cash_by_hand(problem: PlanningProblem, plan: Plan) -> float:
    return sum(
        unit.overtime_wage * o + unit.surge_price * s
        for unit, decisions in zip(problem.units, plan.units, strict=True)
        for o, s in zip(decisions.overtime_hours, decisions.surge, strict=True)
    )


def assert_plan_invariants(
    problem: PlanningProblem, plan: Plan, *, terminal: bool = True, budget: bool = True
) -> None:
    """Every model constraint, checked here without atlas.planning.check_plan."""
    for unit, decisions in zip(problem.units, plan.units, strict=True):
        previous = unit.initial_backlog
        for day in range(problem.horizon):
            x = decisions.served[day]
            o = decisions.overtime_hours[day]
            s = decisions.surge[day]
            b = decisions.backlog[day]
            d = unit.demand[day]
            assert abs(b - (previous + d - x)) <= EXACT, (unit.unit_id, day, "balance")
            assert x <= previous + d + TOL, (unit.unit_id, day, "available work")
            capacity = (
                unit.regular_productivity * unit.regular_hours[day]
                + unit.overtime_productivity * o
                + s
            )
            assert x <= capacity + TOL, (unit.unit_id, day, "capacity")
            assert min(x, o, s, b) >= 0.0, (unit.unit_id, day, "non-negative")
            assert o <= unit.overtime_max_hours[day] + TOL
            assert s <= unit.surge_max[day] + TOL
            previous = b
        if unit.overtime_total_hours is not None:
            assert sum(decisions.overtime_hours) <= unit.overtime_total_hours + TOL
        if unit.surge_total is not None:
            assert sum(decisions.surge) <= unit.surge_total + TOL
        if terminal and unit.terminal_backlog_max is not None:
            assert decisions.backlog[-1] <= unit.terminal_backlog_max + TOL
    if budget and problem.discretionary_budget is not None:
        assert cash_by_hand(problem, plan) <= problem.discretionary_budget + TOL


def solve_strict(problem: PlanningProblem) -> Solved:
    attempt = solve_problem(problem, SETTINGS)
    assert attempt.status is SolveStatus.OPTIMAL
    assert attempt.problems == ()
    solved = attempt.require()
    assert_plan_invariants(problem, solved.plan)
    assert solved.certificate.holds
    assert solved.objective_value == pytest.approx(score_by_hand(problem, solved.plan), abs=1e-7)
    return solved


def approx(values: tuple[float, ...], expected: tuple[float, ...]) -> None:
    assert values == pytest.approx(expected, abs=1e-7)


def dual_map(solved: Solved) -> dict[tuple[str, str, int], float]:
    return {d.key(): d.marginal for d in dual_values(solved.lp, solved.result)}


# --- 1. Hand-solvable optima ----------------------------------------------------------------


def case_1a(backlog_penalty: float = 5.0) -> PlanningProblem:
    # Surge is available but far too expensive (100 USD/request), so it stays at its lower
    # bound with a strictly positive reduced cost and the duals of the optimum are unique.
    return make_problem(
        make_unit(
            (10.0,),
            regular_hours=6.0,
            overtime_max_hours=2.0,
            overtime_wage=3.0,
            surge_max=5.0,
            surge_price=100.0,
            backlog_penalty=backlog_penalty,
        )
    )


def test_one_day_overtime_cheaper_than_penalty_serves_eight_with_score_sixteen() -> None:
    # Overtime costs c_ot/q = 3 USD/request < w = 5, so buy all 2 hours: x = 6 + 2 = 8,
    # b = 10 - 8 = 2, score = 3 * 2 + 5 * 2 = 16.
    solved = solve_strict(case_1a())
    plan = solved.plan.units[0]
    approx(plan.served, (8.0,))
    approx(plan.overtime_hours, (2.0,))
    approx(plan.surge, (0.0,))
    approx(plan.backlog, (2.0,))
    assert solved.objective_value == pytest.approx(16.0, abs=1e-9)
    assert solved.evaluation.penalised_score == pytest.approx(16.0, abs=1e-9)


def test_one_day_penalty_below_overtime_price_buys_no_overtime_score_eight() -> None:
    # w = 2 < c_ot/q = 3: an hour of overtime costs 3 and saves 2, so o = 0, x = 6, b = 4,
    # score = 2 * 4 = 8.
    solved = solve_strict(case_1a(backlog_penalty=2.0))
    plan = solved.plan.units[0]
    approx(plan.served, (6.0,))
    approx(plan.overtime_hours, (0.0,))
    approx(plan.backlog, (4.0,))
    assert solved.objective_value == pytest.approx(8.0, abs=1e-9)


def three_day_holding_problem() -> PlanningProblem:
    return make_problem(
        make_unit(
            (10.0, 2.0, 6.0),
            regular_hours=6.0,
            overtime_max_hours=4.0,
            overtime_wage=3.0,
            backlog_penalty=1.0,
            terminal_penalty=10.0,
        )
    )


def test_three_days_holding_work_for_low_demand_day_beats_overtime() -> None:
    # d = (10, 2, 6), regular capacity 6/day, overtime 3 USD/request, w = 1, w_T = 10.
    # Day 1 leaves b1 >= 4 - o1, costing 3 o1 + 1 * (4 - o1) >= 4 with equality only at
    # o1 = 0. Day 2 has 4 + 2 = 6 = capacity, so b2 = 0; day 3 has 6 = capacity, b3 = 0.
    # Optimum: o = 0, x = (6, 6, 6), b = (4, 0, 0), score 4.
    problem = three_day_holding_problem()
    solved = solve_strict(problem)
    plan = solved.plan.units[0]
    approx(plan.overtime_hours, (0.0, 0.0, 0.0))
    approx(plan.served, (6.0, 6.0, 6.0))
    approx(plan.backlog, (4.0, 0.0, 0.0))
    assert solved.objective_value == pytest.approx(4.0, abs=1e-9)
    # The myopic trigger rule (trigger 0) buys 4 overtime hours on day 1: score 3 * 4 = 12.
    myopic = backlog_trigger_plan(problem, {"u": 0.0})
    assert myopic.units[0].overtime_hours == pytest.approx((4.0, 0.0, 0.0))
    assert score_by_hand(problem, myopic) == pytest.approx(12.0)
    assert check_plan(problem, myopic).feasible


def surge_or_overtime_problem(surge_price: float) -> PlanningProblem:
    return make_problem(
        make_unit(
            (10.0,),
            regular_hours=6.0,
            overtime_productivity=2.0,
            overtime_wage=10.0,
            overtime_max_hours=10.0,
            surge_price=surge_price,
            surge_max=3.0,
            backlog_penalty=20.0,
        )
    )


def test_surge_bought_first_when_cheaper_per_request_than_overtime() -> None:
    # Overtime costs c_ot/q = 10/2 = 5 USD/request; surge 4 USD/request (up to 3).
    # Shortfall 4 < w = 20 per request, so clear it: surge 3 (12 USD), then 1 request of
    # overtime = 0.5 h (5 USD). Score 17, backlog 0.
    solved = solve_strict(surge_or_overtime_problem(4.0))
    plan = solved.plan.units[0]
    approx(plan.surge, (3.0,))
    approx(plan.overtime_hours, (0.5,))
    approx(plan.backlog, (0.0,))
    assert solved.objective_value == pytest.approx(17.0, abs=1e-9)


def test_overtime_bought_when_cheaper_per_request_even_if_wage_exceeds_surge_price() -> None:
    # Surge 6 USD/request is below the overtime WAGE (10 USD/h) but above the overtime
    # price per request (5): buy 4 requests = 2 h of overtime (20 USD), no surge.
    solved = solve_strict(surge_or_overtime_problem(6.0))
    plan = solved.plan.units[0]
    approx(plan.surge, (0.0,))
    approx(plan.overtime_hours, (2.0,))
    approx(plan.backlog, (0.0,))
    assert solved.objective_value == pytest.approx(20.0, abs=1e-9)


def surge_timing_problem(surge_max: tuple[float, float]) -> PlanningProblem:
    return make_problem(
        make_unit(
            (10.0, 6.0),
            regular_hours=6.0,
            surge_max=surge_max,
            surge_price=2.0,
            backlog_penalty=5.0,
        )
    )


def test_surge_available_from_day_one_clears_backlog_immediately() -> None:
    # d = (10, 6), capacity 6, surge 2 USD/request, w = 5. b1 >= 4 - s1, b2 >= b1 - s2:
    # s1 = 4 (8 USD) gives b = (0, 0), score 8; waiting would add 5 per request-day.
    solved = solve_strict(surge_timing_problem((10.0, 10.0)))
    plan = solved.plan.units[0]
    approx(plan.surge, (4.0, 0.0))
    approx(plan.backlog, (0.0, 0.0))
    assert solved.objective_value == pytest.approx(8.0, abs=1e-9)


def test_delayed_surge_forces_carrying_backlog_and_buying_on_day_two() -> None:
    # Same, surge only on day 2: b1 = 4 (penalty 20); day 2 buys s2 = 4 (8 USD) since
    # 2 < 5, b2 = 0. Score 28 (not buying: 5 * 4 + 5 * 4 = 40).
    solved = solve_strict(surge_timing_problem((0.0, 10.0)))
    plan = solved.plan.units[0]
    approx(plan.surge, (0.0, 4.0))
    approx(plan.served, (6.0, 10.0))
    approx(plan.backlog, (4.0, 0.0))
    assert solved.objective_value == pytest.approx(28.0, abs=1e-9)


def overtime_total_problem(total: float | None) -> PlanningProblem:
    return make_problem(
        make_unit(
            (8.0, 8.0, 8.0),
            regular_hours=6.0,
            overtime_max_hours=2.0,
            overtime_wage=1.0,
            backlog_penalty=5.0,
            overtime_total_hours=total,
        )
    )


def test_overtime_total_limit_binds_across_days_and_is_spent_early() -> None:
    # d = 8/day, capacity 6, overtime <= 2/day at 1 USD/h, w = 5.
    # Without a total: o = (2, 2, 2), b = 0, score 6.
    unlimited = solve_strict(overtime_total_problem(None))
    approx(unlimited.plan.units[0].overtime_hours, (2.0, 2.0, 2.0))
    assert unlimited.objective_value == pytest.approx(6.0, abs=1e-9)
    # With sum o <= 3: sum b = 3 (2 - o1) + 2 (2 - o2) + (2 - o3), so the hours go to the
    # earliest days: o = (2, 1, 0), b = (0, 1, 3), score 3 + 5 * 4 = 23.
    solved = solve_strict(overtime_total_problem(3.0))
    plan = solved.plan.units[0]
    approx(plan.overtime_hours, (2.0, 1.0, 0.0))
    approx(plan.backlog, (0.0, 1.0, 3.0))
    assert solved.objective_value == pytest.approx(23.0, abs=1e-9)
    # One more allowed hour goes to day 2: saves 2 request-days * 5, costs 1 -> -9 (the
    # same slope on both sides, so the dual is unique).
    assert dual_map(solved)[("overtime_total_limit", "u", -1)] == pytest.approx(-9.0, abs=1e-7)


def budget_problem(budget: float | None) -> PlanningProblem:
    return make_problem(
        make_unit(
            (20.0,),
            regular_hours=6.0,
            overtime_max_hours=10.0,
            overtime_wage=4.0,
            surge_max=5.0,
            surge_price=2.0,
            backlog_penalty=10.0,
        ),
        budget=budget,
    )


def test_budget_binds_and_cheaper_source_per_request_is_bought_first() -> None:
    # Shortfall 14, w = 10. Surge 2 USD/request (<= 5), overtime 4 USD/request.
    # Budget 18: surge 5 (10 USD), then 8 USD of overtime = 2 h. x = 13, b = 7,
    # score = 18 + 70 = 88, spend exactly 18.
    solved = solve_strict(budget_problem(18.0))
    plan = solved.plan.units[0]
    approx(plan.surge, (5.0,))
    approx(plan.overtime_hours, (2.0,))
    approx(plan.backlog, (7.0,))
    assert solved.evaluation.discretionary_cost == pytest.approx(18.0, abs=1e-9)
    assert solved.evaluation.budget_remaining == pytest.approx(0.0, abs=1e-9)
    assert solved.objective_value == pytest.approx(88.0, abs=1e-9)
    duals = dual_map(solved)
    # One USD more buys 1/4 overtime request: saves 10/4, costs 1 -> -1.5.
    assert duals[("budget", "", -1)] == pytest.approx(-1.5, abs=1e-7)
    # One more surge request (2 USD) displaces 1/2 overtime request: net 1/2 request less
    # backlog -> -5.
    assert duals[("surge_daily_limit", "u", 0)] == pytest.approx(-5.0, abs=1e-7)


def test_budget_unconstrained_reference_clears_all_work() -> None:
    # Without the budget: surge 5 (10 USD) + overtime 9 h (36 USD), b = 0, score 46.
    solved = solve_strict(budget_problem(None))
    plan = solved.plan.units[0]
    approx(plan.surge, (5.0,))
    approx(plan.overtime_hours, (9.0,))
    approx(plan.backlog, (0.0,))
    assert solved.objective_value == pytest.approx(46.0, abs=1e-9)


# --- 3. Zero demand -----------------------------------------------------------------------


def test_zero_demand_and_zero_backlog_gives_all_zero_plan_and_zero_score() -> None:
    problem = make_problem(
        # Positive prices: with a zero price, idle paid capacity would be another optimum.
        make_unit((0.0, 0.0, 0.0), overtime_max_hours=3.0, surge_max=3.0, overtime_wage=0.5),
        make_unit((0.0, 0.0, 0.0), unit_id="v", overtime_max_hours=1.0, surge_price=0.5),
        budget=10.0,
    )
    solved = solve_strict(problem)
    assert np.all(np.abs(solved.x) <= 1e-12)
    assert solved.objective_value == pytest.approx(0.0, abs=1e-12)
    assert solved.evaluation.discretionary_cost == pytest.approx(0.0, abs=1e-12)


def test_zero_demand_with_initial_backlog_clears_it_at_regular_capacity() -> None:
    # B0 = 10, capacity 4/day, overtime 100 USD/request (never worth it, w = 1).
    # x = (4, 4, 2), b = (6, 2, 0), score = 1 * (6 + 2) = 8.
    problem = make_problem(
        make_unit(
            (0.0, 0.0, 0.0),
            initial_backlog=10.0,
            regular_hours=4.0,
            overtime_max_hours=5.0,
            overtime_wage=100.0,
            backlog_penalty=1.0,
        )
    )
    solved = solve_strict(problem)
    plan = solved.plan.units[0]
    approx(plan.served, (4.0, 4.0, 2.0))
    approx(plan.backlog, (6.0, 2.0, 0.0))
    approx(plan.overtime_hours, (0.0, 0.0, 0.0))
    assert solved.objective_value == pytest.approx(8.0, abs=1e-9)


# --- 4. Infeasibility and the absence of slack -------------------------------------------------


def unreachable_target_problem() -> PlanningProblem:
    return make_problem(
        make_unit((10.0,), regular_hours=6.0, overtime_max_hours=2.0, terminal_backlog_max=1.0)
    )


def test_unreachable_terminal_target_is_infeasible_without_a_plan() -> None:
    # At most 6 + 2 = 8 of 10 requests can be served: b >= 2 > target 1.
    attempt = solve_problem(unreachable_target_problem(), SETTINGS)
    assert attempt.status is SolveStatus.INFEASIBLE
    assert attempt.solved is None
    assert not attempt.optimal
    assert attempt.value is None


def test_soft_target_least_violation_equals_hand_value() -> None:
    # The least terminal backlog is 2, the target 1: least violation 1 request.
    attempt = solve_problem(
        unreachable_target_problem(),
        SETTINGS,
        Objective.TERMINAL_VIOLATION,
        terminal=Terminal.SOFT,
        budget_row=False,
    )
    assert attempt.problems == ()
    assert attempt.value == pytest.approx(1.0, abs=1e-9)
    solved = attempt.require()
    approx(solved.plan.units[0].backlog, (2.0,))


def budget_target_problem(budget: float) -> PlanningProblem:
    return make_problem(
        make_unit(
            (10.0,),
            regular_hours=6.0,
            overtime_max_hours=4.0,
            overtime_wage=3.0,
            terminal_backlog_max=0.0,
        ),
        budget=budget,
    )


def test_budget_too_small_for_terminal_target_is_infeasible() -> None:
    # Reaching b = 0 needs 4 h * 3 USD = 12 USD > 5.
    attempt = solve_problem(budget_target_problem(5.0), SETTINGS)
    assert attempt.status is SolveStatus.INFEASIBLE
    assert attempt.solved is None


def test_budget_exactly_enough_for_terminal_target_is_feasible_and_spent() -> None:
    solved = solve_strict(budget_target_problem(12.0))
    approx(solved.plan.units[0].overtime_hours, (4.0,))
    assert solved.evaluation.discretionary_cost == pytest.approx(12.0, abs=1e-9)


def test_strict_lp_has_no_slack_columns_and_only_named_model_rows() -> None:
    horizon = 3
    problem = make_problem(
        make_unit(
            (5.0, 6.0, 7.0),
            overtime_max_hours=1.0,
            surge_max=2.0,
            overtime_total_hours=2.0,
            surge_total=3.0,
            terminal_backlog_max=4.0,
        ),
        make_unit((1.0, 2.0, 3.0), unit_id="v", terminal_backlog_max=1.0),
        budget=50.0,
    )
    lp = build_lp(problem)
    expected_columns = len(BLOCKS) * horizon * 2
    assert expected_columns == 24
    assert lp.layout.extras == ()
    assert lp.layout.size == lp.layout.plan_size == expected_columns
    assert lp.c.shape == (expected_columns,)
    assert lp.a_ub.shape[1] == lp.a_eq.shape[1] == expected_columns
    assert lp.lower.shape == lp.upper.shape == (expected_columns,)
    assert all(row.constraint in ROW_UNITS for row in lp.ub_rows)
    assert {row.constraint for row in lp.eq_rows} == {"balance"}
    assert len(lp.eq_rows) == 2 * horizon
    # 6 capacity rows, 2 horizon totals (unit u), 2 terminal rows, 1 budget row.
    names = [row.constraint for row in lp.ub_rows]
    assert names.count("capacity") == 2 * horizon
    assert names.count("overtime_total_limit") == names.count("surge_total_limit") == 1
    assert names.count("terminal_backlog") == 2
    assert names.count("budget") == 1
    assert len(names) == 11
    assert np.all(lp.lower == 0.0)


# --- 5. Objective calculation ------------------------------------------------------------------


def test_objective_equals_recomputed_score_and_excludes_regular_cost() -> None:
    problem = make_problem(
        make_unit(
            (9.0, 3.0, 8.0),
            initial_backlog=2.0,
            regular_hours=(5.0, 6.0, 5.0),
            regular_wage=11.0,
            overtime_max_hours=2.0,
            overtime_wage=2.0,
            surge_max=1.0,
            surge_price=3.0,
            backlog_penalty=1.0,
            terminal_penalty=4.0,
        )
    )
    solved = solve_strict(problem)
    evaluation = solved.evaluation
    regular_by_hand = 11.0 * (5.0 + 6.0 + 5.0)
    assert evaluation.regular_cost == pytest.approx(regular_by_hand)
    cash = cash_by_hand(problem, solved.plan)
    assert evaluation.discretionary_cost == pytest.approx(cash, abs=1e-9)
    assert evaluation.operating_cost == pytest.approx(regular_by_hand + cash, abs=1e-9)
    assert solved.objective_value == pytest.approx(evaluation.penalised_score, abs=1e-9)
    assert solved.objective_value == pytest.approx(score_by_hand(problem, solved.plan), abs=1e-9)
    # The score has no trace of the 176 USD regular cost: the LP constant is zero.
    assert solved.lp.constant == 0.0
    assert evaluation.penalised_score < regular_by_hand
    raised = replace(problem, units=(replace(problem.units[0], regular_wage=1000.0),))
    assert solve_strict(raised).objective_value == pytest.approx(solved.objective_value, abs=1e-9)


# --- 6. Optimality against an independent formulation and other plans --------------------------


def independent_lp(problem: PlanningProblem) -> dict[str, Any]:
    """The model written again, day-major (index ((t * U + k) * 4 + j), j = x, o, s, b)."""
    units = problem.units
    horizon = problem.horizon
    n = 4 * horizon * len(units)

    def col(k: int, day: int, j: int) -> int:
        return ((day * len(units) + k) * 4) + j

    c = np.zeros(n)
    bounds: list[tuple[float, float | None]] = [(0.0, None)] * n
    a_eq, b_eq, a_ub, b_ub = [], [], [], []
    cash = np.zeros(n)
    for k, unit in enumerate(units):
        for day in range(horizon):
            x, o, s, b = (col(k, day, j) for j in range(4))
            c[o] = unit.overtime_wage
            c[s] = unit.surge_price
            c[b] = unit.backlog_penalty
            cash[o] = unit.overtime_wage
            cash[s] = unit.surge_price
            bounds[o] = (0.0, unit.overtime_max_hours[day])
            bounds[s] = (0.0, unit.surge_max[day])
            row = np.zeros(n)
            row[b] = 1.0
            row[x] = 1.0
            rhs = unit.demand[day]
            if day == 0:
                rhs += unit.initial_backlog
            else:
                row[col(k, day - 1, 3)] = -1.0
            a_eq.append(row)
            b_eq.append(rhs)
            row = np.zeros(n)
            row[x] = 1.0
            row[o] = -unit.overtime_productivity
            row[s] = -1.0
            a_ub.append(row)
            b_ub.append(unit.regular_productivity * unit.regular_hours[day])
        c[col(k, horizon - 1, 3)] += unit.terminal_penalty
        for limit, j in ((unit.overtime_total_hours, 1), (unit.surge_total, 2)):
            if limit is not None:
                row = np.zeros(n)
                for day in range(horizon):
                    row[col(k, day, j)] = 1.0
                a_ub.append(row)
                b_ub.append(limit)
        if unit.terminal_backlog_max is not None:
            row = np.zeros(n)
            row[col(k, horizon - 1, 3)] = 1.0
            a_ub.append(row)
            b_ub.append(unit.terminal_backlog_max)
    if problem.discretionary_budget is not None:
        a_ub.append(cash)
        b_ub.append(problem.discretionary_budget)
    return {
        "c": c,
        "A_ub": np.vstack(a_ub),
        "b_ub": np.asarray(b_ub),
        "A_eq": np.vstack(a_eq),
        "b_eq": np.asarray(b_eq),
        "bounds": bounds,
    }


def ipm(**kwargs: Any) -> OptimizeResult:
    result: OptimizeResult = scipy_linprog(method="highs-ipm", **kwargs)
    return result


def ipm_same_matrices(lp: LinearProgram) -> OptimizeResult:
    bounds = [
        (float(low), None if math.isinf(high) else float(high))
        for low, high in zip(lp.lower, lp.upper, strict=True)
    ]
    return ipm(c=lp.c, A_ub=lp.a_ub, b_ub=lp.b_ub, A_eq=lp.a_eq, b_eq=lp.b_eq, bounds=bounds)


def random_feasible_candidate(problem: PlanningProblem, rng: np.random.Generator) -> Plan:
    """Random overtime/surge within every limit and the budget, then random service."""
    hours = []
    surges = []
    for unit in problem.units:
        o = np.array(unit.overtime_max_hours) * rng.uniform(0.0, 1.0, problem.horizon)
        s = np.array(unit.surge_max) * rng.uniform(0.0, 1.0, problem.horizon)
        if unit.overtime_total_hours is not None and o.sum() > unit.overtime_total_hours:
            o *= unit.overtime_total_hours / o.sum()
        if unit.surge_total is not None and s.sum() > unit.surge_total:
            s *= unit.surge_total / s.sum()
        hours.append(o)
        surges.append(s)
    if problem.discretionary_budget is not None:
        spend = sum(
            float(u.overtime_wage * o.sum() + u.surge_price * s.sum())
            for u, o, s in zip(problem.units, hours, surges, strict=True)
        )
        if spend > problem.discretionary_budget:
            factor = problem.discretionary_budget / spend
            hours = [o * factor for o in hours]
            surges = [s * factor for s in surges]
    units = []
    greedy = rng.uniform() < 0.5
    for unit, o, s in zip(problem.units, hours, surges, strict=True):
        level = unit.initial_backlog
        served, backlog = [], []
        for day in range(problem.horizon):
            work = level + unit.demand[day]
            capacity = unit.regular_capacity(day) + unit.overtime_productivity * o[day] + s[day]
            x = min(work, capacity) * (1.0 if greedy else rng.uniform(0.0, 1.0))
            level = work - x
            served.append(float(x))
            backlog.append(float(level))
        units.append(
            UnitPlan(
                unit.unit_id,
                tuple(served),
                tuple(float(v) for v in o),
                tuple(float(v) for v in s),
                tuple(backlog),
            )
        )
    return Plan(tuple(units))


small = st.integers(min_value=0, max_value=10)


@st.composite
def unit_strategy(draw: st.DrawFn, unit_id: str, horizon: int) -> ServiceUnit:
    def days(high: int) -> tuple[float, ...]:
        values = draw(st.lists(st.integers(0, high), min_size=horizon, max_size=horizon))
        return tuple(float(v) for v in values)

    def optional(high: int) -> float | None:
        value = draw(st.one_of(st.none(), st.integers(0, high)))
        return None if value is None else float(value)

    return ServiceUnit(
        unit_id=unit_id,
        demand=days(20),
        initial_backlog=float(draw(small)),
        regular_hours=days(8),
        regular_productivity=float(draw(st.integers(1, 3))),
        overtime_productivity=float(draw(st.integers(1, 3))),
        overtime_max_hours=days(4),
        surge_max=days(5),
        regular_wage=float(draw(small)),
        overtime_wage=float(draw(small)),
        surge_price=float(draw(small)),
        backlog_penalty=float(draw(st.integers(0, 5))),
        terminal_penalty=float(draw(st.integers(0, 5))),
        overtime_total_hours=optional(10),
        surge_total=optional(10),
        terminal_backlog_max=optional(15),
    )


@st.composite
def problem_strategy(draw: st.DrawFn) -> PlanningProblem:
    horizon = draw(st.integers(1, 4))
    count = draw(st.integers(1, 2))
    units = tuple(draw(unit_strategy(f"u{k}", horizon)) for k in range(count))
    budget = draw(st.one_of(st.none(), st.integers(0, 60)))
    return make_problem(*units, budget=None if budget is None else float(budget))


HYPOTHESIS = settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@HYPOTHESIS
@given(problem=problem_strategy())
def test_highs_ds_optimum_matches_ipm_on_same_and_independent_matrices(
    problem: PlanningProblem,
) -> None:
    attempt = solve_problem(problem, SETTINGS)
    same = ipm_same_matrices(build_lp(problem))
    independent = ipm(**independent_lp(problem))
    if attempt.solved is None:
        assert attempt.status is SolveStatus.INFEASIBLE
        assert same.status == 2
        assert independent.status == 2
        return
    assert attempt.problems == ()
    assert same.status == 0
    assert independent.status == 0
    value = attempt.solved.objective_value
    for reference in (same.fun, independent.fun):
        assert value == pytest.approx(reference, rel=1e-6, abs=1e-6)
    assert_plan_invariants(problem, attempt.solved.plan)
    assert value == pytest.approx(score_by_hand(problem, attempt.solved.plan), abs=1e-6)


@HYPOTHESIS
@given(problem=problem_strategy(), seed=st.integers(0, 2**32 - 1), trigger=small)
def test_strict_optimum_no_worse_than_any_feasible_policy_or_random_plan(
    problem: PlanningProblem, seed: int, trigger: int
) -> None:
    rng = np.random.default_rng(seed)
    candidates = [
        fixed_capacity_plan(problem),
        backlog_trigger_plan(problem, {u.unit_id: float(trigger) for u in problem.units}),
        *(random_feasible_candidate(problem, rng) for _ in range(5)),
    ]
    feasible = [plan for plan in candidates if check_plan(problem, plan).feasible]
    attempt = solve_problem(problem, SETTINGS)
    if attempt.solved is None:
        # A feasible plan would prove the strict model feasible.
        assert feasible == []
        return
    best = attempt.solved.evaluation.penalised_score
    for plan in feasible:
        assert_plan_invariants(problem, plan)
        assert best <= score_by_hand(problem, plan) + 1e-6


@HYPOTHESIS
@given(problem=problem_strategy())
def test_dual_signs_on_random_optima(problem: PlanningProblem) -> None:
    attempt = solve_problem(problem, SETTINGS)
    if attempt.solved is None:
        return
    solved = attempt.solved
    assert solved.certificate.holds
    sign_tol = 1e-7
    for dual in dual_values(solved.lp, solved.result):
        assert (dual.rhs_units, dual.marginal_units) == ROW_UNITS[dual.constraint]
        if dual.constraint == "balance":
            if dual.rhs > 0:
                assert dual.marginal >= -sign_tol, dual
        else:
            assert dual.marginal <= sign_tol, dual
            # Only a tight constraint can carry a non-zero shadow price; a daily limit of
            # zero (resource unavailable) is tight but never called binding.
            if abs(dual.marginal) > 1e-6:
                assert dual.binding or dual.rhs == 0, dual
                assert dual.slack <= 1e-6 + 1e-9 * abs(dual.rhs), dual
    assert np.all(solved.result.lower_marginals >= -sign_tol)


# --- 7. Certificate ----------------------------------------------------------------------------


def test_certificate_holds_on_optimum_and_fails_on_tampered_duals_or_primal() -> None:
    solved = solve_strict(case_1a())
    lp, result = solved.lp, solved.result
    good = certify(lp, result)
    assert good.holds
    assert good.primal_objective == pytest.approx(16.0, abs=1e-9)
    assert good.dual_objective == pytest.approx(16.0, abs=1e-9)
    assert result.eq_marginals is not None
    assert result.ub_marginals is not None
    assert result.x is not None
    flipped_eq = replace(result, eq_marginals=-result.eq_marginals)
    assert not certify(lp, flipped_eq).holds
    flipped_ub = replace(result, ub_marginals=-result.ub_marginals)
    bad = certify(lp, flipped_ub)
    assert not bad.holds
    assert bad.dual_sign > 1.0
    moved = result.x.copy()
    moved[lp.layout.index(0, "served", 0)] += 1e-3
    perturbed = certify(lp, replace(result, x=moved))
    assert not perturbed.holds
    assert perturbed.primal > 1.0
    # A feasible but sub-optimal point (no overtime: x = 6, b = 4, score 20) fails on the
    # duality gap, not on feasibility.
    sub = np.zeros(lp.layout.size)
    sub[lp.layout.index(0, "served", 0)] = 6.0
    sub[lp.layout.index(0, "backlog", 0)] = 4.0
    suboptimal = certify(lp, replace(result, x=sub))
    assert suboptimal.primal <= 1.0
    assert not suboptimal.holds


def test_certify_refuses_a_result_without_a_solution() -> None:
    lp = build_lp(unreachable_target_problem())
    result = solve_lp(lp, SETTINGS)
    assert result.status is SolveStatus.INFEASIBLE
    with pytest.raises(OptimizationError, match="only an optimal solution"):
        certify(lp, result)


# --- 8. Dual values on the hand-solved case ----------------------------------------------------


def test_case_1a_duals_equal_hand_derivatives_with_units() -> None:
    # Value function V(d, cap, O_max) = 3 * O_max + 5 * (d - cap - O_max) near the optimum:
    # dV/dd = +5 (= w), dV/dcap = -5, dV/dO_max = 3 - 5 = -2 (= c_ot - w q).
    solved = solve_strict(case_1a())
    duals = {d.key(): d for d in dual_values(solved.lp, solved.result)}
    balance = duals[("balance", "u", 0)]
    capacity = duals[("capacity", "u", 0)]
    overtime = duals[("overtime_daily_limit", "u", 0)]
    surge = duals[("surge_daily_limit", "u", 0)]
    assert balance.marginal == pytest.approx(5.0, abs=1e-7)
    assert capacity.marginal == pytest.approx(-5.0, abs=1e-7)
    assert overtime.marginal == pytest.approx(-2.0, abs=1e-7)
    assert surge.marginal == pytest.approx(0.0, abs=1e-7)
    assert capacity.binding
    assert overtime.binding
    assert not surge.binding
    assert surge.slack == pytest.approx(5.0)
    assert (balance.rhs_units, balance.marginal_units) == (
        "requests",
        "usd_equivalent_per_request",
    )
    assert capacity.marginal_units == "usd_equivalent_per_request_of_unpaid_capacity"
    assert overtime.rhs_units == "staff_hours_per_day"
    assert overtime.marginal_units == "usd_equivalent_per_overtime_hour_allowed"
    for dual in duals.values():
        assert (dual.rhs_units, dual.marginal_units) == ROW_UNITS[dual.constraint]
    all_units = {unit for pair in ROW_UNITS.values() for unit in pair}
    assert "usd_per_staff_hour" not in all_units
    assert all("wage" not in unit for unit in all_units)


def test_dual_values_refused_for_diagnostic_objective() -> None:
    attempt = solve_problem(case_1a(), SETTINGS, Objective.BACKLOG_DAYS)
    solved = attempt.require()
    with pytest.raises(OptimizationError, match="planning objective only"):
        dual_values(solved.lp, solved.result)


# --- 9. Solver classification ------------------------------------------------------------------


def fake_result(status: int, message: str) -> OptimizeResult:
    return OptimizeResult(status=status, message=message, x=None, nit=0, success=False)


def test_highs_model_error_raises_and_is_not_reported_infeasible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(*_args: Any, **_kwargs: Any) -> OptimizeResult:
        return fake_result(2, "Model error (HiGHS Status 2: Model error)")

    monkeypatch.setattr(optimizer, "linprog", fake)
    with pytest.raises(OptimizationError, match="did not finish"):
        solve_problem(case_1a(), SETTINGS)


def test_unknown_status_is_retried_without_presolve(monkeypatch: pytest.MonkeyPatch) -> None:
    presolve_flags: list[bool] = []

    def fake(*args: Any, **kwargs: Any) -> OptimizeResult:
        presolve_flags.append(kwargs["options"]["presolve"])
        if len(presolve_flags) == 1:
            return fake_result(4, "Unknown (HiGHS Status 15: Unknown)")
        result: OptimizeResult = scipy_linprog(*args, **kwargs)
        return result

    monkeypatch.setattr(optimizer, "linprog", fake)
    lp = build_lp(case_1a())
    result = solve_lp(lp, SETTINGS)
    assert presolve_flags == [True, False]
    assert result.retried
    assert result.status is SolveStatus.OPTIMAL
    assert result.highs_status == 7
    assert result.objective == pytest.approx(16.0, abs=1e-9)


def test_unknown_status_without_presolve_tries_the_interior_point_method_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without presolve there is no presolve-free re-solve; the last resort is the interior-
    # point method, and a status still inconclusive there raises.
    methods: list[str] = []

    def fake(*_args: Any, **kwargs: Any) -> OptimizeResult:
        methods.append(kwargs["method"])
        return fake_result(4, "Unknown (HiGHS Status 15: Unknown)")

    monkeypatch.setattr(optimizer, "linprog", fake)
    with pytest.raises(OptimizationError, match="did not finish"):
        solve_lp(build_lp(case_1a()), replace(SETTINGS, presolve=False))
    assert methods == ["highs-ds", "highs-ipm"]


def test_unbounded_or_infeasible_after_retry_counts_as_infeasible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake(*_args: Any, **_kwargs: Any) -> OptimizeResult:
        calls.append(1)
        return fake_result(4, "Problem is infeasible or unbounded (HiGHS Status 9: ...)")

    monkeypatch.setattr(optimizer, "linprog", fake)
    result = solve_lp(build_lp(case_1a()), SETTINGS)
    assert len(calls) == 2
    assert result.retried
    assert result.status is SolveStatus.INFEASIBLE
    assert result.x is None


def test_message_without_highs_status_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(*_args: Any, **_kwargs: Any) -> OptimizeResult:
        return fake_result(0, "Optimization terminated successfully.")

    monkeypatch.setattr(optimizer, "linprog", fake)
    with pytest.raises(OptimizationError, match="did not finish"):
        solve_lp(build_lp(case_1a()), SETTINGS)


def test_optimize_warning_from_linprog_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(*args: Any, **kwargs: Any) -> OptimizeResult:
        warnings.warn("Unrecognized options detected: {'bogus': 1}", OptimizeWarning, stacklevel=2)
        result: OptimizeResult = scipy_linprog(*args, **kwargs)
        return result

    monkeypatch.setattr(optimizer, "linprog", fake)
    with pytest.raises(OptimizationError, match="HiGHS warned"):
        solve_lp(build_lp(case_1a()), SETTINGS)


def test_check_magnitudes_rejects_huge_or_nan_coefficients() -> None:
    lp = build_lp(case_1a())
    _check_magnitudes(lp)
    below = lp.c.copy()
    below[0] = 9.99e8
    _check_magnitudes(replace(lp, c=below))
    huge = lp.c.copy()
    huge[0] = 1e9
    with pytest.raises(OptimizationError, match="objective has a non-finite value"):
        _check_magnitudes(replace(lp, c=huge))
    nan_rhs = lp.b_eq.copy()
    nan_rhs[0] = math.nan
    with pytest.raises(OptimizationError, match="b_eq has a non-finite value"):
        _check_magnitudes(replace(lp, b_eq=nan_rhs))
    big_matrix = lp.a_ub.copy()
    big_matrix[0, 0] = -2e9
    with pytest.raises(OptimizationError, match="A_ub"):
        solve_lp(replace(lp, a_ub=big_matrix), SETTINGS)
    huge_demand = make_problem(make_unit((1e9,)))
    with pytest.raises(OptimizationError, match="rescale the inputs"):
        solve_problem(huge_demand, SETTINGS)


# --- 10. build_lp argument validation and soft targets ------------------------------------------


def test_build_lp_rejects_mismatched_objective_arguments() -> None:
    problem = case_1a()
    size = build_lp(problem).layout.size
    with pytest.raises(OptimizationError, match="explicit coefficients"):
        build_lp(problem, Objective.CUSTOM)
    with pytest.raises(OptimizationError, match="explicit coefficients"):
        build_lp(problem, Objective.WEIGHTED, coefficients=np.zeros(size))
    with pytest.raises(OptimizationError, match="wrong length"):
        build_lp(problem, Objective.CUSTOM, coefficients=np.zeros(size + 1))
    with pytest.raises(OptimizationError, match="needs soft terminal targets"):
        build_lp(unreachable_target_problem(), Objective.TERMINAL_VIOLATION)
    with pytest.raises(OptimizationError, match="needs soft terminal targets"):
        build_lp(problem, Objective.TERMINAL_VIOLATION, terminal=Terminal.SOFT)
    bad_row = ExtraRow(Row("cap_test", None, None), np.zeros(size - 1), 1.0)
    with pytest.raises(OptimizationError, match="extra row has the wrong number"):
        build_lp(problem, extra_rows=[bad_row])


def test_soft_terminal_adds_one_violation_column_per_unit_with_target() -> None:
    problem = make_problem(
        make_unit((5.0, 5.0), unit_id="a", terminal_backlog_max=1.0),
        make_unit((5.0, 5.0), unit_id="b"),
        make_unit((5.0, 5.0), unit_id="c", terminal_backlog_max=0.0),
    )
    hard = build_lp(problem)
    soft = build_lp(problem, terminal=Terminal.SOFT)
    none = build_lp(problem, terminal=Terminal.NONE)
    assert soft.layout.extras == (violation_name("a"), violation_name("c"))
    assert soft.layout.size == hard.layout.size + 2 == 3 * 4 * 2 + 2
    assert none.layout.size == hard.layout.size
    assert [r.constraint for r in none.ub_rows].count("terminal_backlog") == 0
    rows = [i for i, r in enumerate(soft.ub_rows) if r.constraint == "terminal_backlog"]
    assert len(rows) == 2
    for i, unit_id, k in zip(rows, ("a", "c"), (0, 2), strict=True):
        row = soft.a_ub[i]
        assert row[soft.layout.extra_index(violation_name(unit_id))] == -1.0
        assert row[soft.layout.index(k, "backlog", 1)] == 1.0
        assert np.count_nonzero(row) == 2
    # Violation columns are non-negative and unbounded above.
    for name in soft.layout.extras:
        j = soft.layout.extra_index(name)
        assert soft.lower[j] == 0.0
        assert math.isinf(soft.upper[j])


# --- 11. Snapping of round-off in to_plan -------------------------------------------------------


def snapping_lp() -> LinearProgram:
    return build_lp(
        make_problem(make_unit((0.0, 0.0), overtime_max_hours=2.0, surge_max=1.0, surge_price=1.0))
    )


def test_to_plan_snaps_tiny_round_off_onto_bounds_and_counts_it() -> None:
    lp = snapping_lp()
    v = np.zeros(lp.layout.size)
    v[lp.layout.index(0, "overtime_hours", 0)] = -1e-12
    v[lp.layout.index(0, "surge", 1)] = 1.0 + 1e-12
    plan, cleaning = to_plan(lp, v)
    assert cleaning.values_adjusted == 2
    assert cleaning.max_adjustment == pytest.approx(1e-12, rel=1e-3)
    assert plan.units[0].overtime_hours == (0.0, 0.0)
    assert plan.units[0].surge == (0.0, 1.0)
    assert check_plan(lp.problem, plan).feasible
    expected = np.zeros(lp.layout.size)
    expected[lp.layout.index(0, "surge", 1)] = 1.0
    np.testing.assert_array_equal(plan_vector(lp, plan), expected)


def test_to_plan_keeps_large_violation_for_check_plan_to_report() -> None:
    lp = snapping_lp()
    v = np.zeros(lp.layout.size)
    v[lp.layout.index(0, "overtime_hours", 0)] = -1e-3
    plan, cleaning = to_plan(lp, v)
    assert cleaning.values_adjusted == 0
    assert cleaning.max_adjustment == 0.0
    assert plan.units[0].overtime_hours[0] == -1e-3
    check = check_plan(lp.problem, plan)
    assert not check.feasible
    assert check.constraints_violated() == ["nonnegative_overtime"]


# --- 12. Solver versions -----------------------------------------------------------------------


def test_solver_versions_report_scipy_and_highs() -> None:
    versions = solver_versions()
    assert versions["scipy"] == scipy.__version__
    assert versions["highs"] is not None
    assert re.fullmatch(r"\d+\.\d+\.\d+", versions["highs"])


def test_attempt_from_infeasible_solve_has_no_problems_recorded() -> None:
    attempt: Attempt = solve_problem(budget_target_problem(5.0), SETTINGS)
    assert attempt.problems == ()
    assert attempt.iterations >= 0
    with pytest.raises(AssertionError, match="not optimal"):
        attempt.require()
