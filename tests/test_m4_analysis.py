"""M4 analyses in :mod:`atlas.decision_analysis`, on tiny synthetic planning problems.

Expected values are derived by hand (the derivation is in a comment next to each check) or
by an independent linear programme written here from the model of :mod:`atlas.planning`
(own variable layout, solved with the HiGHS interior-point method ``highs-ipm``), never by
calling the code under test a second time. No test reads the frozen M4 protocol.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import replace
from datetime import date

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scipy.optimize import linprog

from atlas.decision_analysis import (
    FEASIBILITY,
    LEX_EPS_ABS,
    MONOTONE,
    RELAXED_TERMINAL_TARGET,
    STRICT,
    FiniteDifference,
    analyse_scenario,
    apply_sweep,
    diagnose,
    evaluate_policy,
    finite_differences,
    frontier,
    lex_cap,
    optimal_face,
    pareto_relation,
    run_sweep,
    solve_lexicographic,
    solve_problem,
)
from atlas.optimizer import Objective, SolverSettings, SolveStatus, Terminal
from atlas.planning import (
    Plan,
    PlanningProblem,
    ServiceUnit,
    UnitPlan,
    check_plan,
    evaluate_plan,
    least_terminal_backlog,
)
from atlas.policies import BACKLOG_TRIGGER, FIXED_CAPACITY

SETTINGS = SolverSettings()
STEPS = {"requests": 1.0, "staff_hours": 1.0, "usd": 100.0}
START = date(2024, 1, 1)
ABS = 1e-5


def make_unit(
    unit_id: str = "u",
    *,
    demand: Sequence[float],
    initial_backlog: float = 0.0,
    regular_hours: Sequence[float] | None = None,
    regular_productivity: float = 1.0,
    overtime_productivity: float = 1.0,
    overtime_max: Sequence[float] | None = None,
    surge_max: Sequence[float] | None = None,
    regular_wage: float = 0.0,
    overtime_wage: float = 1.0,
    surge_price: float = 1.0,
    backlog_penalty: float = 0.0,
    terminal_penalty: float = 0.0,
    overtime_total: float | None = None,
    surge_total: float | None = None,
    target: float | None = None,
) -> ServiceUnit:
    horizon = len(demand)
    zeros = [0.0] * horizon

    def floats(values: Sequence[float] | None) -> tuple[float, ...]:
        return tuple(float(v) for v in (zeros if values is None else values))

    return ServiceUnit(
        unit_id=unit_id,
        demand=floats(demand),
        initial_backlog=float(initial_backlog),
        regular_hours=floats(regular_hours),
        regular_productivity=float(regular_productivity),
        overtime_productivity=float(overtime_productivity),
        overtime_max_hours=floats(overtime_max),
        surge_max=floats(surge_max),
        regular_wage=float(regular_wage),
        overtime_wage=float(overtime_wage),
        surge_price=float(surge_price),
        backlog_penalty=float(backlog_penalty),
        terminal_penalty=float(terminal_penalty),
        overtime_total_hours=overtime_total,
        surge_total=surge_total,
        terminal_backlog_max=target,
    )


def make_problem(*units: ServiceUnit, budget: float | None = None) -> PlanningProblem:
    return PlanningProblem(START, tuple(units), budget)


def plan_of(
    unit_id: str,
    served: Sequence[float],
    overtime: Sequence[float],
    surge: Sequence[float],
    backlog: Sequence[float],
) -> UnitPlan:
    return UnitPlan(
        unit_id,
        tuple(float(v) for v in served),
        tuple(float(v) for v in overtime),
        tuple(float(v) for v in surge),
        tuple(float(v) for v in backlog),
    )


# --- An independent reference LP (own layout; HiGHS interior point) ---------------------------

_X, _O, _S, _B = range(4)


def _col(problem: PlanningProblem, k: int, day: int, block: int) -> int:
    # Day-major within a unit (the code under test is block-major).
    return (k * problem.horizon + day) * 4 + block


def ref_vector(problem: PlanningProblem, name: str) -> np.ndarray:
    """Objective vectors in the reference layout (see :mod:`atlas.planning`)."""
    n = len(problem.units) * problem.horizon * 4
    c = np.zeros(n)
    last = problem.horizon - 1
    for k, unit in enumerate(problem.units):
        for day in range(problem.horizon):
            if name in ("score", "cash"):
                c[_col(problem, k, day, _O)] = unit.overtime_wage
                c[_col(problem, k, day, _S)] = unit.surge_price
            if name in ("score", "penalty"):
                terminal = unit.terminal_penalty if day == last else 0.0
                c[_col(problem, k, day, _B)] = unit.backlog_penalty + terminal
            if name == "request_days" or (name == "terminal" and day == last):
                c[_col(problem, k, day, _B)] = 1.0
    return c


def reference_lp(
    problem: PlanningProblem,
    c: np.ndarray,
    *,
    terminal: bool = True,
    budget: bool = True,
    caps: Sequence[tuple[np.ndarray, float]] = (),
) -> tuple[float, np.ndarray | None]:
    """``min c v`` over the plans of ``problem``; ``(inf, None)`` when infeasible."""
    n = c.size
    a_eq, b_eq, a_ub, b_ub = [], [], [], []
    bounds: list[tuple[float, float | None]] = [(0.0, None)] * n
    cash = ref_vector(problem, "cash")
    for k, unit in enumerate(problem.units):
        for day in range(problem.horizon):
            row = np.zeros(n)
            row[_col(problem, k, day, _B)] = 1.0
            row[_col(problem, k, day, _X)] = 1.0
            if day:
                row[_col(problem, k, day - 1, _B)] = -1.0
            a_eq.append(row)
            b_eq.append(unit.demand[day] + (unit.initial_backlog if day == 0 else 0.0))
            row = np.zeros(n)
            row[_col(problem, k, day, _X)] = 1.0
            row[_col(problem, k, day, _O)] = -unit.overtime_productivity
            row[_col(problem, k, day, _S)] = -1.0
            a_ub.append(row)
            b_ub.append(unit.regular_productivity * unit.regular_hours[day])
            bounds[_col(problem, k, day, _O)] = (0.0, unit.overtime_max_hours[day])
            bounds[_col(problem, k, day, _S)] = (0.0, unit.surge_max[day])
        for block, total in ((_O, unit.overtime_total_hours), (_S, unit.surge_total)):
            if total is not None:
                row = np.zeros(n)
                for day in range(problem.horizon):
                    row[_col(problem, k, day, block)] = 1.0
                a_ub.append(row)
                b_ub.append(total)
        if terminal and unit.terminal_backlog_max is not None:
            row = np.zeros(n)
            row[_col(problem, k, problem.horizon - 1, _B)] = 1.0
            a_ub.append(row)
            b_ub.append(unit.terminal_backlog_max)
    if budget and problem.discretionary_budget is not None:
        a_ub.append(cash)
        b_ub.append(problem.discretionary_budget)
    for vector, rhs in caps:
        a_ub.append(vector)
        b_ub.append(rhs)
    result = linprog(
        c,
        A_ub=np.array(a_ub),
        b_ub=np.array(b_ub),
        A_eq=np.array(a_eq),
        b_eq=np.array(b_eq),
        bounds=bounds,
        method="highs-ipm",
    )
    if result.status == 2:
        return math.inf, None
    assert result.status == 0, result.message
    return float(result.fun), np.asarray(result.x)


def reference_value(problem: PlanningProblem, name: str, **kwargs: bool) -> float:
    return reference_lp(problem, ref_vector(problem, name), **kwargs)[0]


# --- 1. Infeasibility diagnosis --------------------------------------------------------------


def budget_too_small_problem() -> PlanningProblem:
    # T=2, d=[10,10], regular capacity 5/day, overtime 4 h/day at 3 USD/request (q=1),
    # surge 3/day at 4 USD/request, target 2, budget 15 USD.
    return make_problem(
        make_unit(
            demand=[10, 10],
            regular_hours=[5, 5],
            overtime_max=[4, 4],
            surge_max=[3, 3],
            overtime_wage=3.0,
            surge_price=4.0,
            backlog_penalty=1.0,
            target=2.0,
        ),
        budget=15.0,
    )


def test_diagnose_budget_too_small_gives_hand_minimum_spend_shortfall_and_violation() -> None:
    problem = budget_too_small_problem()
    assert solve_problem(problem, SETTINGS).status is SolveStatus.INFEASIBLE
    diagnosis = diagnose(problem, SETTINGS)
    # Regular capacity alone leaves b = [5, 10]: 8 requests must go to reach b[T] <= 2.
    # Overtime (3 USD/request, 8 h available) is cheaper than surge (4): 8 * 3 = 24 USD.
    assert diagnosis.least_violation_unlimited == pytest.approx(0.0, abs=ABS)
    assert diagnosis.min_spend_for_target == pytest.approx(24.0, abs=ABS)
    assert diagnosis.budget_shortfall == pytest.approx(24.0 - 15.0, abs=ABS)
    # 15 USD buys 5 overtime requests: b[T] = 10 - 5 = 5, three above the target.
    assert diagnosis.least_violation_within_budget == pytest.approx(3.0, abs=ABS)
    # Maximum capacity 5 + 4 + 3 = 12 >= 10 every day: the analytic bound is 0, exact.
    assert diagnosis.terminal_lower_bound == {"u": pytest.approx(0.0)}
    assert diagnosis.lower_bound_exact is True
    reason = " ".join(diagnosis.reasons)
    assert "no plan spending less than 24 USD" in reason
    assert "DIAGNOSTIC, not a budget request" in reason
    assert not any("cannot be reached" in reason for reason in diagnosis.reasons)


def test_diagnose_relaxed_plan_respects_budget_and_relaxes_only_the_target() -> None:
    problem = budget_too_small_problem()
    diagnosis = diagnose(problem, SETTINGS)
    relaxed = diagnosis.relaxed.require()
    assert diagnosis.to_dict()["relaxed_plan"] == RELAXED_TERMINAL_TARGET
    assert diagnosis.to_dict()["label"].startswith("DIAGNOSTIC")
    assert relaxed.evaluation.discretionary_cost <= 15.0 + ABS
    assert relaxed.evaluation.terminal_backlog == pytest.approx(5.0, abs=ABS)
    assert relaxed.check.feasible
    assert check_plan(problem, relaxed.plan, ignore=["terminal_backlog"]).feasible
    # Checked against every constraint, only the terminal target is violated.
    assert check_plan(problem, relaxed.plan).constraints_violated() == ["terminal_backlog"]
    assert diagnosis.relaxed.problems == ()


def test_diagnose_unreachable_target_has_positive_unlimited_violation_and_no_spend() -> None:
    # T=1, d=10, no regular capacity, at most 3 overtime + 2 surge requests: b[T] >= 5,
    # target 1, so the least violation is 4 whatever the cash.
    problem = make_problem(
        make_unit(demand=[10], overtime_max=[3], surge_max=[2], overtime_wage=2.0, target=1.0)
    )
    diagnosis = diagnose(problem, SETTINGS)
    assert diagnosis.least_violation_unlimited == pytest.approx(4.0, abs=ABS)
    assert diagnosis.least_violation_unlimited > 0
    assert diagnosis.least_violation_within_budget == pytest.approx(4.0, abs=ABS)
    assert diagnosis.min_spend_for_target is None
    assert diagnosis.budget_shortfall is None
    assert diagnosis.terminal_lower_bound["u"] == pytest.approx(5.0)
    assert any("cannot be reached" in reason for reason in diagnosis.reasons)
    assert any("even serving at maximum capacity" in reason for reason in diagnosis.reasons)
    relaxed = diagnosis.relaxed.require()
    # Reaching the least violation needs every extra request: 3 * 2 + 2 * 1 = 8 USD.
    assert relaxed.evaluation.discretionary_cost == pytest.approx(8.0, abs=ABS)


def test_diagnose_shared_budget_allocates_to_the_cheaper_violation_first() -> None:
    # Both units: no regular capacity, d=10 on the single day, q=1, ample overtime.
    # v_A = max(0, 10 - o_A - 5), v_B = max(0, 10 - o_B); o_A + 2 o_B <= 20.
    # One USD removes 1 request of v_A but only 0.5 of v_B, so spend on A first:
    # o_A = 5 (5 USD, v_A = 0), then 15 USD on B: o_B = 7.5, v_B = 2.5. Total 2.5, not 5
    # (5 would be e.g. o_B = 10 first, leaving v_A = 5).
    problem = make_problem(
        make_unit("ua", demand=[10], overtime_max=[100], overtime_wage=1.0, target=5.0),
        make_unit("ub", demand=[10], overtime_max=[100], overtime_wage=2.0, target=0.0),
        budget=20.0,
    )
    diagnosis = diagnose(problem, SETTINGS)
    assert diagnosis.least_violation_within_budget == pytest.approx(2.5, abs=ABS)
    assert diagnosis.least_violation_unlimited == pytest.approx(0.0, abs=ABS)
    # Reaching both targets: o_A = 5 (5 USD) + o_B = 10 (20 USD) = 25 USD.
    assert diagnosis.min_spend_for_target == pytest.approx(25.0, abs=ABS)
    assert diagnosis.budget_shortfall == pytest.approx(5.0, abs=ABS)
    relaxed = diagnosis.relaxed.require()
    assert relaxed.plan.unit("ua").backlog[-1] == pytest.approx(5.0, abs=ABS)
    assert relaxed.plan.unit("ub").backlog[-1] == pytest.approx(2.5, abs=ABS)
    assert relaxed.evaluation.discretionary_cost <= 20.0 + ABS


def test_least_terminal_backlog_is_a_lower_bound_when_an_overtime_total_binds() -> None:
    # d=[10,0,10], regular capacity [0,10,0], overtime 10 h/day but 10 h in total.
    # The recursion (totals ignored): max capacity [10,20,10] -> b = 0, 0, 0.
    # Greedy with the total (all 10 h on day 1) would leave b[T] = 10; the LP keeps the
    # hours for day 3 (day 1's 10 requests are served by day 2's regular hours): 0.
    unit = make_unit(
        demand=[10, 0, 10], regular_hours=[0, 10, 0], overtime_max=[10, 10, 10], overtime_total=10
    )
    problem = make_problem(unit)
    lp_least = reference_value(problem, "terminal", budget=False)
    assert least_terminal_backlog(unit) == pytest.approx(0.0)
    assert lp_least == pytest.approx(0.0, abs=ABS)
    assert least_terminal_backlog(unit) <= lp_least + ABS
    # A strict gap: d=[10,10], no regular capacity, 10 h/day but 10 h in total.
    # Bound (totals ignored) 0; true least terminal backlog 20 - 10 = 10.
    tight = make_unit(demand=[10, 10], overtime_max=[10, 10], overtime_total=10, target=0.0)
    assert least_terminal_backlog(tight) == pytest.approx(0.0)
    least_tight = reference_value(make_problem(tight), "terminal", terminal=False, budget=False)
    assert least_tight == pytest.approx(10.0, abs=ABS)
    diagnosis = diagnose(make_problem(tight), SETTINGS)
    assert diagnosis.lower_bound_exact is False
    assert diagnosis.least_violation_unlimited == pytest.approx(10.0, abs=ABS)
    assert not any("even serving at maximum capacity" in r for r in diagnosis.reasons)


small = st.integers(min_value=0, max_value=12)


@settings(max_examples=40, deadline=None)
@given(
    horizon=st.integers(min_value=1, max_value=4),
    data=st.data(),
)
def test_least_terminal_backlog_equals_lp_minimum_without_totals(
    horizon: int, data: st.DataObject
) -> None:
    days = st.lists(small, min_size=horizon, max_size=horizon)
    unit = make_unit(
        demand=data.draw(days),
        initial_backlog=data.draw(small),
        regular_hours=data.draw(days),
        regular_productivity=data.draw(st.sampled_from([0.5, 1.0, 2.0])),
        overtime_productivity=data.draw(st.sampled_from([0.5, 1.0, 1.5])),
        overtime_max=data.draw(days),
        surge_max=data.draw(days),
    )
    lp_least = reference_value(make_problem(unit), "terminal", budget=False)
    assert least_terminal_backlog(unit) == pytest.approx(lp_least, abs=ABS)


# --- 2. Lexicographic solves ------------------------------------------------------------------


def test_solve_lexicographic_second_stage_removes_idle_paid_overtime() -> None:
    # T=3, d=[5,5,0], regular capacity [5,5,10] covers every day, overtime 5 h/day at
    # 10 USD/h, budget 1000. Least terminal backlog = 0 with or without overtime.
    problem = make_problem(
        make_unit(
            demand=[5, 5, 0],
            regular_hours=[5, 5, 10],
            overtime_max=[5, 5, 5],
            overtime_wage=10.0,
            backlog_penalty=1.0,
        ),
        budget=1000.0,
    )
    # A plan paying 5 idle overtime hours on each day is also stage-1 optimal: stage 1
    # alone does not determine the cash.
    idle = Plan((plan_of("u", [5, 5, 0], [5, 5, 5], [0, 0, 0], [0, 0, 0]),))
    assert check_plan(problem, idle).feasible
    assert evaluate_plan(problem, idle).terminal_backlog == 0.0
    assert evaluate_plan(problem, idle).idle_paid_capacity == pytest.approx(15.0)
    stage1, stage2 = solve_lexicographic(problem, SETTINGS, Objective.TERMINAL_BACKLOG)
    assert stage1.value == pytest.approx(0.0, abs=ABS)
    second = stage2.require()
    assert second.evaluation.terminal_backlog == pytest.approx(stage1.value or 0.0, abs=1e-9)
    assert second.evaluation.overtime_hours == pytest.approx(0.0, abs=ABS)
    assert second.evaluation.idle_paid_capacity == pytest.approx(0.0, abs=ABS)
    assert second.evaluation.penalised_score == pytest.approx(0.0, abs=ABS)
    # The second stage runs on the optimal face of the first (no epsilon cap row).
    assert stage2.stage_fallback is None
    assert not [row for row in second.lp.ub_rows if row.constraint.startswith("cap_")]
    assert stage1.problems == ()
    assert stage2.problems == ()


def test_solve_lexicographic_keeps_first_objective_exactly() -> None:
    # T=1, d=10, no regular capacity, 10 overtime hours at 1 USD: least terminal backlog
    # 0 needs all 10 hours; the second stage (least cash) cannot drop any of them (with an
    # epsilon cap it could drop lex_cap(0) = 1e-6 of them).
    problem = make_problem(make_unit(demand=[10], overtime_max=[10], overtime_wage=1.0))
    stage1, stage2 = solve_lexicographic(
        problem, SETTINGS, Objective.TERMINAL_BACKLOG, Objective.DISCRETIONARY_CASH
    )
    assert stage1.value == pytest.approx(0.0, abs=ABS)
    assert stage2.value == pytest.approx(10.0, abs=1e-9)
    assert stage2.require().evaluation.terminal_backlog == pytest.approx(0.0, abs=1e-9)
    assert lex_cap(0.0) == pytest.approx(LEX_EPS_ABS)


def test_solve_lexicographic_returns_the_infeasible_first_stage_twice() -> None:
    problem = make_problem(make_unit(demand=[10], overtime_max=[3], target=0.0))
    stage1, stage2 = solve_lexicographic(problem, SETTINGS, Objective.TERMINAL_BACKLOG)
    assert stage1.status is SolveStatus.INFEASIBLE
    assert stage2 is stage1


# --- 3. Optimal face ---------------------------------------------------------------------------


def test_optimal_face_unique_on_strictly_priced_instance() -> None:
    # d=10/day, regular capacity 5/day, overtime 1 USD/request, w=2 per request-day,
    # surge 5 USD/request. Deferring a request costs w=2 > 1, surge is dearer: the only
    # optimum is o = [5, 5], s = 0, b = [0, 0].
    problem = make_problem(
        make_unit(
            demand=[10, 10],
            regular_hours=[5, 5],
            overtime_max=[10, 10],
            surge_max=[3, 3],
            overtime_wage=1.0,
            surge_price=5.0,
            backlog_penalty=2.0,
        )
    )
    strict = solve_problem(problem, SETTINGS).require()
    face = optimal_face(problem, SETTINGS, strict)
    assert face.unique is True
    assert face.undetermined_days() == 0
    assert face.daily_extra_capacity == ()
    assert face.problems == ()
    widths = {r.quantity: r.width for r in face.ranges}
    assert set(widths) == {
        "discretionary_cash",
        "backlog_request_days",
        "terminal_backlog",
        "overtime_hours",
        "surge_requests",
    }
    assert all(w == pytest.approx(0.0, abs=1e-4) for w in widths.values())
    cash = next(r for r in face.ranges if r.quantity == "discretionary_cash")
    assert cash.low == pytest.approx(10.0, abs=1e-4)
    assert cash.units == "usd"


def test_optimal_face_not_unique_when_timing_of_overtime_is_free() -> None:
    # B0=10, no demand or regular hours, 10 overtime h/day at 1 USD, w=0, w_T=100. Every
    # optimum buys exactly 10 hours (score 10), split freely between the two days.
    problem = make_problem(
        make_unit(
            demand=[0, 0],
            initial_backlog=10.0,
            overtime_max=[10, 10],
            overtime_wage=1.0,
            terminal_penalty=100.0,
        )
    )
    strict = solve_problem(problem, SETTINGS).require()
    assert strict.objective_value == pytest.approx(10.0, abs=ABS)
    face = optimal_face(problem, SETTINGS, strict)
    assert face.unique is False
    assert face.max_active_distance > 1e-3
    assert face.problems == ()
    ranges = {r.quantity: r for r in face.ranges}
    # Total overtime and cash are fixed at 10; day-1 backlog b1 = 10 - o1 ranges over [0, 10].
    assert ranges["overtime_hours"].width == pytest.approx(0.0, abs=1e-4)
    assert ranges["discretionary_cash"].width == pytest.approx(0.0, abs=1e-4)
    assert ranges["backlog_request_days"].low == pytest.approx(0.0, abs=1e-4)
    assert ranges["backlog_request_days"].high == pytest.approx(10.0, abs=1e-4)
    by_day = {(uid, day): (low, high) for uid, day, low, high in face.daily_extra_capacity}
    assert set(by_day) == {("u", 0), ("u", 1)}
    for low, high in by_day.values():
        assert low == pytest.approx(0.0, abs=1e-4)
        assert high == pytest.approx(10.0, abs=1e-4)
        assert high - low > 0
    assert face.undetermined_days() == 2


# --- 4. Finite differences ------------------------------------------------------------------


def _fd_instances() -> list[PlanningProblem]:
    return [
        # Interior optimum, overtime used, surge unused.
        make_problem(
            make_unit(
                demand=[10, 10],
                regular_hours=[5, 5],
                overtime_max=[10, 10],
                surge_max=[3, 3],
                overtime_wage=1.0,
                surge_price=5.0,
                backlog_penalty=2.0,
            )
        ),
        # Budget and overtime total binding, a target with slack, terminal penalty.
        make_problem(
            make_unit(
                demand=[8, 12, 6],
                initial_backlog=4.0,
                regular_hours=[6, 6, 6],
                overtime_max=[4, 4, 4],
                surge_max=[2, 2, 2],
                overtime_wage=2.0,
                surge_price=3.0,
                backlog_penalty=1.5,
                terminal_penalty=2.0,
                overtime_total=6.0,
                target=15.0,
            ),
            budget=14.0,
        ),
        # Two units sharing a budget.
        make_problem(
            make_unit("ua", demand=[6, 6], regular_hours=[3, 3], overtime_max=[5, 5],
                      overtime_wage=1.0, backlog_penalty=3.0),
            make_unit("ub", demand=[4, 9], regular_hours=[4, 4], surge_max=[5, 5],
                      surge_price=2.0, backlog_penalty=2.5, target=3.0),
            budget=12.0,
        ),
    ]  # fmt: skip


@pytest.mark.parametrize("index", range(3))
def test_finite_differences_bracket_every_dual(index: int) -> None:
    problem = _fd_instances()[index]
    strict = solve_problem(problem, SETTINGS).require()
    differences = finite_differences(strict, SETTINGS, STEPS)
    assert differences
    for item in differences:
        assert item.bracket_ok, item
        assert item.convex_ok, item
        assert item.sign_ok, item
        assert item.step == pytest.approx(1e-3)


def _kink_differences() -> dict[tuple[str, str, int], FiniteDifference]:
    # d=10 = regular capacity 10, overtime 5 h at 1 USD, w=3, no surge (S_max = 0).
    problem = make_problem(
        make_unit(
            demand=[10],
            regular_hours=[10],
            overtime_max=[5],
            overtime_wage=1.0,
            backlog_penalty=3.0,
        )
    )
    strict = solve_problem(problem, SETTINGS).require()
    assert strict.objective_value == pytest.approx(0.0, abs=ABS)
    return {d.dual.key(): d for d in finite_differences(strict, SETTINGS, STEPS)}


def test_finite_differences_report_breakpoint_where_demand_equals_capacity() -> None:
    items = _kink_differences()
    balance = items[("balance", "u", 0)]
    capacity = items[("capacity", "u", 0)]
    # One more request: served by overtime at 1 USD (cheaper than w=3) -> forward 1.
    # One fewer: still nothing to buy -> backward 0.
    assert balance.forward == pytest.approx(1.0, abs=1e-4)
    assert balance.backward == pytest.approx(0.0, abs=1e-4)
    assert balance.status == "breakpoint"
    # One more unit of regular capacity: idle -> forward 0. One less: 1 USD of overtime
    # replaces it -> backward (0 - 1 * step) / step = -1.
    assert capacity.forward == pytest.approx(0.0, abs=1e-4)
    assert capacity.backward == pytest.approx(-1.0, abs=1e-4)
    assert capacity.status == "breakpoint"
    assert all(d.bracket_ok and d.convex_ok and d.sign_ok for d in items.values())


def test_finite_differences_zero_upper_bound_is_a_domain_boundary() -> None:
    items = _kink_differences()
    surge = items[("surge_daily_limit", "u", 0)]
    assert surge.dual.rhs == 0.0
    assert surge.backward is None
    assert surge.practical_backward is None
    assert surge.status == "domain_boundary_below"
    assert surge.locally_unique is False


def test_finite_differences_infeasible_side_at_least_achievable_target() -> None:
    # d=10, no regular capacity, 6 overtime h at 1 USD, w=0.5 < 1: without the target
    # nothing is bought. The target 4 = least achievable (10 - 6) forces o = 6.
    # Raising it by e saves (1 - 0.5) e: forward -0.5. Lowering it is infeasible.
    problem = make_problem(
        make_unit(demand=[10], overtime_max=[6], overtime_wage=1.0, backlog_penalty=0.5, target=4.0)
    )
    strict = solve_problem(problem, SETTINGS).require()
    items = {d.dual.key(): d for d in finite_differences(strict, SETTINGS, STEPS)}
    target = items[("terminal_backlog", "u", -1)]
    assert target.backward == -math.inf
    assert target.forward == pytest.approx(-0.5, abs=1e-4)
    assert target.status == "infeasible_side"
    assert target.bracket_ok
    assert target.convex_ok
    # The overtime limit is at its maximum use too: lowering it is infeasible.
    assert items[("overtime_daily_limit", "u", 0)].status == "infeasible_side"


# --- 5. Policies -------------------------------------------------------------------------------


def policy_problem() -> PlanningProblem:
    return make_problem(
        make_unit(
            demand=[10, 12, 8],
            initial_backlog=5.0,
            regular_hours=[8, 8, 8],
            overtime_max=[4, 4, 4],
            surge_max=[3, 3, 3],
            overtime_wage=2.0,
            surge_price=3.0,
            backlog_penalty=1.0,
            terminal_penalty=2.0,
            target=6.0,
        ),
        budget=30.0,
    )


@pytest.mark.parametrize("policy", [FIXED_CAPACITY, BACKLOG_TRIGGER])
def test_evaluate_policy_lp_never_worse_at_same_cash_or_same_backlog(policy: str) -> None:
    outcome = evaluate_policy(policy_problem(), SETTINGS, policy, {"u": 3.0})
    assert outcome.problems == ()
    same_cash = outcome.same_cash.require().evaluation
    same_backlog = outcome.same_backlog.require().evaluation
    policy_eval = outcome.evaluation
    assert same_cash.discretionary_cost <= lex_cap(policy_eval.discretionary_cost) + ABS
    assert same_cash.backlog_request_days <= policy_eval.backlog_request_days + ABS
    assert same_backlog.discretionary_cost <= policy_eval.discretionary_cost + ABS
    assert same_backlog.backlog_request_days <= lex_cap(policy_eval.backlog_request_days) + ABS
    assert same_backlog.terminal_backlog <= lex_cap(policy_eval.terminal_backlog) + ABS


def test_evaluate_policy_fixed_capacity_matches_lp_at_zero_cash() -> None:
    # Fixed capacity: work 15 -> serve 8, b=7; 7+12=19 -> 8, b=11; 11+8=19 -> 8, b=11.
    # Request-days 7 + 11 + 11 = 29, zero cash. With zero cash serving all you can each
    # day is optimal, so the LP at the same (zero) cash also has 29 request-days.
    outcome = evaluate_policy(policy_problem(), SETTINGS, FIXED_CAPACITY, {"u": 3.0})
    assert outcome.plan.unit("u").backlog == (7.0, 11.0, 11.0)
    assert outcome.evaluation.discretionary_cost == 0.0
    assert outcome.evaluation.backlog_request_days == pytest.approx(29.0)
    lp = outcome.same_cash.require().evaluation
    assert lp.backlog_request_days == pytest.approx(29.0, abs=ABS)
    assert lp.discretionary_cost == pytest.approx(0.0, abs=ABS)
    # The fixed policy violates the target (11 > 6) and is reported as such.
    assert outcome.check.constraints_violated() == ["terminal_backlog"]


@settings(max_examples=25, deadline=None)
@given(
    horizon=st.integers(min_value=1, max_value=3),
    trigger=st.integers(min_value=0, max_value=10),
    budget=st.one_of(st.none(), st.integers(min_value=0, max_value=60)),
    data=st.data(),
)
def test_evaluate_policy_dominance_holds_on_random_instances(
    horizon: int, trigger: int, budget: int | None, data: st.DataObject
) -> None:
    days = st.lists(small, min_size=horizon, max_size=horizon)
    problem = make_problem(
        make_unit(
            demand=data.draw(days),
            initial_backlog=data.draw(small),
            regular_hours=data.draw(days),
            overtime_max=data.draw(days),
            surge_max=data.draw(days),
            overtime_wage=data.draw(st.integers(min_value=0, max_value=5)),
            surge_price=data.draw(st.integers(min_value=0, max_value=5)),
            backlog_penalty=data.draw(st.integers(min_value=0, max_value=5)),
        ),
        budget=None if budget is None else float(budget),
    )
    for policy in (FIXED_CAPACITY, BACKLOG_TRIGGER):
        outcome = evaluate_policy(problem, SETTINGS, policy, {"u": float(trigger)})
        assert outcome.problems == ()
        mine = outcome.evaluation
        # Independent check: least request-days with at most the policy's cash.
        cap = (ref_vector(problem, "cash"), lex_cap(mine.discretionary_cost))
        least_days = reference_lp(problem, ref_vector(problem, "request_days"), caps=[cap])[0]
        assert least_days <= mine.backlog_request_days + ABS
        assert outcome.same_cash.require().evaluation.backlog_request_days == pytest.approx(
            least_days, abs=1e-4
        )
        assert (
            outcome.same_backlog.require().evaluation.discretionary_cost
            <= mine.discretionary_cost + ABS
        )


# --- 6. Pareto relation ----------------------------------------------------------------------


def test_pareto_relation_classifies_constructed_pairs() -> None:
    problem = make_problem(make_unit(demand=[10, 10], overtime_max=[10, 10], overtime_wage=1.0))
    # Plan A buys 10 h on day 1: cash 10, b = [0, 10], 10 request-days, terminal 10.
    a = evaluate_plan(problem, Plan((plan_of("u", [10, 0], [10, 0], [0, 0], [0, 10]),)))
    # Plan B buys nothing: cash 0, b = [10, 20], 30 request-days, terminal 20.
    b = evaluate_plan(problem, Plan((plan_of("u", [0, 0], [0, 0], [0, 0], [10, 20]),)))
    assert pareto_relation(a, b) == "tradeoff"
    assert pareto_relation(b, a) == "tradeoff"
    assert pareto_relation(a, a) == "equal"
    cheaper = replace(a, discretionary_cost=a.discretionary_cost - 1.0)
    assert pareto_relation(cheaper, a) == "dominates"
    assert pareto_relation(a, cheaper) == "dominated"
    less_terminal = replace(a, terminal_backlog=a.terminal_backlog - 0.5)
    assert pareto_relation(less_terminal, a) == "dominates"
    # A difference below the tolerance (1e-9 USD) counts as equal.
    noise = replace(a, discretionary_cost=a.discretionary_cost + 1e-9)
    assert pareto_relation(noise, a) == "equal"
    # Better on one criterion, worse on another, equal on the third.
    mixed = replace(a, discretionary_cost=9.0, backlog_request_days=11.0)
    assert pareto_relation(mixed, a) == "tradeoff"


# --- 7. Frontier -----------------------------------------------------------------------------


def frontier_problem() -> PlanningProblem:
    # Unit A: 10 requests arrive on day 2; overtime then at 1 USD saves w_T = 5 each.
    # Unit B: 10 requests on day 1; day-1 overtime saves 2 * 0.1 = 0.2 per USD, day-2 0.1.
    return make_problem(
        make_unit("ua", demand=[0, 10], overtime_max=[0, 10], terminal_penalty=5.0),
        make_unit("ub", demand=[10, 0], overtime_max=[10, 10], backlog_penalty=0.1),
    )


def test_frontier_matches_hand_curve_and_strict_optimum_lies_on_it() -> None:
    problem = frontier_problem()
    strict = solve_problem(problem, SETTINGS).require()
    # Strict optimum: A buys 10 (5 > 1), B nothing (0.2 < 1): cash 10, penalty 0.1 * 20 = 2.
    assert strict.evaluation.discretionary_cost == pytest.approx(10.0, abs=ABS)
    curve = frontier(problem, SETTINGS, 5, strict)
    assert curve is not None
    assert curve.monotone_ok
    assert curve.convex_ok
    assert curve.on_frontier_check == "passed"
    assert curve.problems == ()
    # Least penalty 0 needs A's 10 h and B's 10 h on day 1: 20 USD (no target: kept = dropped).
    assert curve.cash_for_least_penalty["kept"] == pytest.approx(20.0, abs=ABS)
    assert curve.cash_for_least_penalty["dropped"] == pytest.approx(20.0, abs=ABS)
    # P(c) = 52 - 5 c for c <= 10, then 2 - 0.2 (c - 10) up to c = 20: at the grid caps
    # 0, 5, 10, 15, 20 (the top cap carries the lexicographic slack of ~1e-6 USD).
    expected = [52.0, 27.0, 2.0, 1.0, 0.0]
    for variant in ("kept", "dropped"):
        grid = [p for p in curve.points if p.terminal_target == variant and p.marker == "grid"]
        assert [p.cash_cap for p in grid] == pytest.approx([0, 5, 10, 15, 20], abs=1e-4)
        for point, penalty in zip(grid, expected, strict=True):
            assert point.status is SolveStatus.OPTIMAL
            assert point.penalty == pytest.approx(penalty, abs=1e-4)
            assert point.discretionary_cash == pytest.approx(point.cash_cap, abs=1e-4)
    at_strict = [p for p in curve.points if p.marker == "strict_plan_cash"]
    assert len(at_strict) == 1
    assert at_strict[0].penalty == pytest.approx(2.0, abs=1e-4)


def test_frontier_on_penalty_axis_passes_where_request_days_axis_would_fail() -> None:
    problem = frontier_problem()
    strict = solve_problem(problem, SETTINGS).require()
    # Strict plan: b_A = [0, 0], b_B = [10, 10] -> 20 request-days at 10 USD.
    assert strict.evaluation.backlog_request_days == pytest.approx(20.0, abs=ABS)
    # A plan with the same 10 USD on B's day 1: b_A = [0, 10], b_B = [0, 0] -> 10
    # request-days. A request-days frontier would put the strict optimum off the curve.
    other = Plan(
        (
            plan_of("ua", [0, 0], [0, 0], [0, 0], [0, 10]),
            plan_of("ub", [10, 0], [10, 0], [0, 0], [0, 0]),
        )
    )
    assert check_plan(problem, other).feasible
    other_eval = evaluate_plan(problem, other)
    assert other_eval.discretionary_cost == pytest.approx(10.0)
    assert other_eval.backlog_request_days == pytest.approx(10.0)
    assert other_eval.backlog_request_days < strict.evaluation.backlog_request_days
    curve = frontier(problem, SETTINGS, 5, strict)
    assert curve is not None
    assert curve.on_frontier_check == "passed"


def test_frontier_is_none_when_every_penalty_weight_is_zero() -> None:
    problem = make_problem(make_unit(demand=[10, 10], overtime_max=[5, 5], target=10.0))
    strict = solve_problem(problem, SETTINGS).require()
    assert frontier(problem, SETTINGS, 5, strict) is None


def test_frontier_caps_below_least_cash_for_target_are_infeasible_points() -> None:
    # d=10, no regular capacity, 10 overtime h at 1 USD, w=2, target 4: reaching the target
    # needs >= 6 USD. Least penalty: buy all 10 h (10 USD). Grid caps 0, 2.5, 5, 7.5, 10.
    problem = make_problem(
        make_unit(
            demand=[10], overtime_max=[10], overtime_wage=1.0, backlog_penalty=2.0, target=4.0
        )
    )
    strict = solve_problem(problem, SETTINGS).require()
    curve = frontier(problem, SETTINGS, 5, strict)
    assert curve is not None
    kept = [p for p in curve.points if p.terminal_target == "kept" and p.marker == "grid"]
    assert [p.cash_cap for p in kept] == pytest.approx([0, 2.5, 5, 7.5, 10], abs=1e-4)
    for point in kept[:3]:
        assert point.status is SolveStatus.INFEASIBLE
        assert point.penalty is None
        assert point.discretionary_cash is None
    for point in kept[3:]:
        assert point.status is SolveStatus.OPTIMAL
        # Penalty 2 * (10 - cap).
        assert point.penalty == pytest.approx(2.0 * (10.0 - point.cash_cap), abs=1e-4)
    dropped = [p for p in curve.points if p.terminal_target == "dropped"]
    assert all(p.status is SolveStatus.OPTIMAL for p in dropped)
    assert curve.on_frontier_check == "passed"
    assert curve.monotone_ok
    assert curve.convex_ok


# --- 8. Sweeps -------------------------------------------------------------------------------


def sweep_problem() -> PlanningProblem:
    return make_problem(
        make_unit("ua", demand=[4, 6], regular_hours=[2, 3], regular_productivity=1.5,
                  overtime_productivity=1.2, overtime_max=[2, 2], surge_max=[1, 1],
                  regular_wage=30.0, overtime_wage=45.0, surge_price=20.0,
                  backlog_penalty=2.0, terminal_penalty=1.0),
        make_unit("ub", demand=[1, 2], regular_hours=[1, 1], regular_productivity=2.0,
                  overtime_productivity=1.0, regular_wage=10.0, overtime_wage=15.0,
                  surge_price=5.0, backlog_penalty=0.5),
        budget=80.0,
    )  # fmt: skip


def test_apply_sweep_transforms_each_parameter_as_documented() -> None:
    base = sweep_problem()
    a, b = base.units
    demand = apply_sweep(base, "demand_multiplier", 1.5)
    assert demand.units[0].demand == (6.0, 9.0)
    assert demand.units[1].demand == (1.5, 3.0)
    assert demand.discretionary_budget == 80.0
    assert apply_sweep(base, "discretionary_budget_multiplier", 0.5).discretionary_budget == 40.0
    no_budget = replace(base, discretionary_budget=None)
    assert apply_sweep(no_budget, "discretionary_budget_multiplier", 2.0).discretionary_budget is (
        None
    )
    hours = apply_sweep(base, "regular_hours_multiplier", 2.0)
    assert hours.units[0].regular_hours == (4.0, 6.0)
    assert hours.units[1].regular_hours == (2.0, 2.0)
    assert hours.units[0].regular_productivity == a.regular_productivity
    productivity = apply_sweep(base, "productivity_multiplier", 2.0)
    assert productivity.units[0].regular_productivity == pytest.approx(3.0)
    assert productivity.units[0].overtime_productivity == pytest.approx(2.4)
    assert productivity.units[1].regular_productivity == pytest.approx(4.0)
    assert productivity.units[1].overtime_productivity == pytest.approx(2.0)
    assert productivity.units[0].regular_hours == a.regular_hours
    cost = apply_sweep(base, "cost_multiplier", 1.1)
    for scaled, original in zip(cost.units, (a, b), strict=True):
        assert scaled.regular_wage == pytest.approx(original.regular_wage * 1.1)
        assert scaled.overtime_wage == pytest.approx(original.overtime_wage * 1.1)
        assert scaled.surge_price == pytest.approx(original.surge_price * 1.1)
        assert scaled.backlog_penalty == original.backlog_penalty
        assert scaled.terminal_penalty == original.terminal_penalty
    penalty = apply_sweep(base, "backlog_penalty", 7.0)
    assert [u.backlog_penalty for u in penalty.units] == [7.0, 7.0]
    assert [u.terminal_penalty for u in penalty.units] == [1.0, 0.0]
    assert penalty.units[0].overtime_wage == a.overtime_wage
    # The base problem is untouched (frozen dataclasses, replaced not mutated).
    assert base.units == (a, b)


SWEEP_VALUES = {
    "demand_multiplier": [0.5, 1.0, 1.5],
    "discretionary_budget_multiplier": [0.0, 0.5, 1.0, 2.0],
    "regular_hours_multiplier": [0.5, 1.0, 2.0],
    "productivity_multiplier": [0.5, 1.0, 2.0],
    "cost_multiplier": [0.5, 1.0, 2.0],
    "backlog_penalty": [0.0, 1.0, 5.0, 20.0],
}


def _non_decreasing(values: Sequence[float | None]) -> bool:
    known = [v for v in values if v is not None]
    return all(b >= a - 1e-6 * max(1.0, abs(a)) for a, b in itertools.pairwise(known))


@settings(max_examples=30, deadline=None)
@given(
    horizon=st.integers(min_value=1, max_value=3),
    budget=st.one_of(st.none(), st.integers(min_value=0, max_value=80)),
    target=st.one_of(st.none(), st.integers(min_value=0, max_value=15)),
    data=st.data(),
)
def test_run_sweep_monotonicity_on_random_instances(
    horizon: int, budget: int | None, target: int | None, data: st.DataObject
) -> None:
    days = st.lists(small, min_size=horizon, max_size=horizon)
    problem = make_problem(
        make_unit(
            demand=data.draw(days),
            initial_backlog=data.draw(small),
            regular_hours=data.draw(days),
            regular_productivity=data.draw(st.sampled_from([0.5, 1.0, 2.0])),
            overtime_productivity=data.draw(st.sampled_from([0.5, 1.0, 2.0])),
            overtime_max=data.draw(days),
            surge_max=data.draw(days),
            overtime_wage=data.draw(st.integers(min_value=0, max_value=8)),
            surge_price=data.draw(st.integers(min_value=0, max_value=8)),
            backlog_penalty=data.draw(st.integers(min_value=0, max_value=6)),
            terminal_penalty=data.draw(st.integers(min_value=0, max_value=6)),
            overtime_total=data.draw(st.one_of(st.none(), st.just(float(horizon * 3)))),
            target=None if target is None else float(target),
        ),
        budget=None if budget is None else float(budget),
    )
    for parameter, values in SWEEP_VALUES.items():
        points, checks = run_sweep(problem, SETTINGS, parameter, values)
        assert [p.value for p in points] == values
        assert all(c.holds for c in checks), checks
        assert checks[-1].quantity == "feasibility"
        assert checks[-1].direction == FEASIBILITY[parameter]
        evaluations = [p.attempt.solved.evaluation if p.attempt.solved else None for p in points]
        scores = [None if e is None else e.penalised_score for e in evaluations]
        if parameter in ("demand_multiplier", "cost_multiplier", "backlog_penalty"):
            assert _non_decreasing(scores), scores
        else:
            assert _non_decreasing([None if s is None else -s for s in scores]), scores
        if parameter == "backlog_penalty":
            days_ = [None if e is None else -e.backlog_request_days for e in evaluations]
            assert _non_decreasing(days_)
            rest = [
                None if e is None else e.discretionary_cost + e.terminal_penalty
                for e in evaluations
            ]
            assert _non_decreasing(rest)
        feasible = [p.attempt.optimal for p in points]
        rule = FEASIBILITY[parameter]
        if rule == "lost":
            assert feasible == sorted(feasible, reverse=True)
        elif rule == "gained":
            assert feasible == sorted(feasible)
        else:
            assert len(set(feasible)) == 1
        # Independent check of each feasible point's optimal score.
        for point in points:
            reference = reference_value(point.problem, "score")
            if point.attempt.solved is None:
                assert reference == math.inf
            else:
                assert point.attempt.solved.evaluation.penalised_score == pytest.approx(
                    reference, abs=1e-4, rel=1e-7
                )


def test_cash_is_not_monotone_in_backlog_penalty_when_terminal_penalty_is_positive() -> None:
    # Why MONOTONE checks cash + terminal penalty, never cash alone, for backlog_penalty.
    problem = make_problem(
        make_unit(
            demand=[4, 4, 3, 6],
            initial_backlog=8.0,
            regular_hours=[5, 4, 4, 3],
            overtime_max=[5, 6, 5, 2],
            surge_max=[2, 2, 3, 0],
            overtime_total=3.0,
            overtime_wage=10.0,
            surge_price=15.0,
            terminal_penalty=30.0,
        )
    )
    assert "discretionary_cost" not in {q for q, _ in MONOTONE["backlog_penalty"]}

    def cash_range(w: float) -> tuple[float, float, float]:
        # Independent: optimal score, then least and most cash over the optimal plans.
        changed = apply_sweep(problem, "backlog_penalty", w)
        score_vector = ref_vector(changed, "score")
        best, _ = reference_lp(changed, score_vector)
        cap = [(score_vector, best + 1e-7)]
        cash = ref_vector(changed, "cash")
        low, _ = reference_lp(changed, cash, caps=cap)
        high, _ = reference_lp(changed, -cash, caps=cap)
        return best, low, -high

    best_8, low_8, high_8 = cash_range(8.0)
    best_16, low_16, high_16 = cash_range(16.0)
    assert low_8 == pytest.approx(105.0, abs=1e-4)
    assert high_8 == pytest.approx(105.0, abs=1e-4)
    assert low_16 == pytest.approx(90.0, abs=1e-4)
    assert high_16 == pytest.approx(90.0, abs=1e-4)
    # Every optimal plan at w=16 spends less cash than every optimal plan at w=8.
    assert high_16 < low_8
    points, checks = run_sweep(problem, SETTINGS, "backlog_penalty", [8.0, 16.0])
    assert all(c.holds for c in checks)
    first, second = (p.attempt.require().evaluation for p in points)
    assert first.penalised_score == pytest.approx(best_8, abs=1e-4)
    assert second.penalised_score == pytest.approx(best_16, abs=1e-4)
    assert second.discretionary_cost < first.discretionary_cost
    # Cash + terminal penalty does rise: 105 + 30 * 1 = 135 -> 90 + 30 * 2 = 150.
    assert first.discretionary_cost + first.terminal_penalty == pytest.approx(135.0, abs=1e-4)
    assert second.discretionary_cost + second.terminal_penalty == pytest.approx(150.0, abs=1e-4)


def test_run_sweep_records_binding_constraints_and_budget_dual() -> None:
    # d=10, no regular capacity, 10 overtime h at 1 USD, w=3, budget 4: the budget binds;
    # one more USD buys one request and saves w=3 -> budget shadow price -(3 - 1)/1 = -2.
    problem = make_problem(
        make_unit(demand=[10], overtime_max=[10], overtime_wage=1.0, backlog_penalty=3.0),
        budget=4.0,
    )
    points, _ = run_sweep(problem, SETTINGS, "discretionary_budget_multiplier", [1.0])
    point = points[0]
    assert point.budget_dual == pytest.approx(-2.0, abs=1e-6)
    assert point.terminal_dual is None
    assert "budget" in point.binding
    assert "capacity (1 days)" in point.binding


# --- 9. Scenario analysis --------------------------------------------------------------------


def test_analyse_scenario_healthy_instance_has_no_problems() -> None:
    problem = policy_problem()
    result = analyse_scenario(
        "healthy", problem, SETTINGS, {"u": 3.0}, shadow_steps=STEPS, frontier_points=5
    )
    assert result.problems == ()
    assert result.feasibility == STRICT
    # The reported plan is the least-cash plan among the optimal ones (a second stage).
    assert result.reported is result.strict_plan
    reported = result.reported.require()
    optimum = result.strict.require().objective_value
    assert reported.evaluation.penalised_score == pytest.approx(optimum, abs=1e-4)
    assert reported.evaluation.discretionary_cost <= (
        result.strict.require().evaluation.discretionary_cost + 1e-4
    )
    assert result.diagnosis is None
    assert result.face is not None
    assert result.duals
    assert len(result.finite_differences) == len(result.duals)
    assert [p.policy for p in result.policies] == [FIXED_CAPACITY, BACKLOG_TRIGGER]
    assert result.frontier is not None
    assert result.without_target.optimal
    # Dropping the target can only lower the optimal score.
    assert (
        result.without_target.require().objective_value
        <= result.strict.require().objective_value + ABS
    )
    assert result.strict.require().objective_value == pytest.approx(
        reference_value(problem, "score"), abs=1e-4
    )


def test_analyse_scenario_infeasible_instance_reports_the_relaxed_plan() -> None:
    problem = budget_too_small_problem()
    result = analyse_scenario(
        "short", problem, SETTINGS, {"u": 2.0}, shadow_steps=STEPS, frontier_points=4
    )
    assert result.feasibility == RELAXED_TERMINAL_TARGET
    assert result.diagnosis is not None
    assert result.reported is result.diagnosis.relaxed
    assert result.reported.optimal
    assert result.face is None
    assert result.duals == ()
    assert result.finite_differences == ()
    assert result.problems == ()
    assert result.frontier is not None
    assert result.frontier.on_frontier_check == "not_applicable"


def test_solve_problem_checks_agree_with_reference_lp_on_terminal_backlog_objective() -> None:
    problem = policy_problem()
    attempt = solve_problem(problem, SETTINGS, Objective.TERMINAL_BACKLOG, terminal=Terminal.NONE)
    assert attempt.problems == ()
    assert attempt.value == pytest.approx(reference_value(problem, "terminal", terminal=False))
