"""Planning problems, plan evaluation and checking, and the two simple policies (M4).

No solver is involved. Every expected number is derived by hand in a comment next to the
assertion, or checked against an independent re-implementation written in plain loops in
this file; the code under test is never used to compute its own expected value.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from atlas.planning import (
    CONSTRAINTS,
    MAX_HORIZON_DAYS,
    Plan,
    PlanningError,
    PlanningProblem,
    ServiceUnit,
    UnitPlan,
    backlog_path,
    check_plan,
    evaluate_plan,
    least_terminal_backlog,
)
from atlas.policies import backlog_trigger_plan, fixed_capacity_plan

START = date(2024, 1, 1)
EXACT = 1e-9


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
    regular_hours: float | tuple[float, ...] = 10.0,
    regular_productivity: float = 1.0,
    overtime_productivity: float = 2.0,
    overtime_max_hours: float | tuple[float, ...] = 4.0,
    surge_max: float | tuple[float, ...] = 10.0,
    regular_wage: float = 20.0,
    overtime_wage: float = 30.0,
    surge_price: float = 20.0,
    backlog_penalty: float = 2.0,
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


def unit_plan(
    served: tuple[float, ...],
    overtime: tuple[float, ...],
    surge: tuple[float, ...],
    backlog: tuple[float, ...],
    unit_id: str = "u",
) -> UnitPlan:
    return UnitPlan(unit_id, served, overtime, surge, backlog)


def approx(value: float) -> object:
    return pytest.approx(value, abs=EXACT)


# --------------------------------------------------------------------------------------
# The reference 3-day problem and plan used by the evaluation and check tests.
#
# Regular capacity p*H = 1 * 8 = 8 requests/day, overtime productivity q = 2 requests per
# staff hour, overtime at most 3 h/day and 6 h in total, surge at most 4 requests/day and
# 8 in total, terminal backlog at most 10 requests, discretionary budget 1000 USD.
#
#   day  work = b[t-1]+d   o     s    capacity = 8+2o+s   served   backlog
#   1    5+10 = 15         1     2    12                  12       3
#   2    3+10 = 13         1.5   2    13                  13       0
#   3    0+10 = 10         0.5   1    10                  9        1
# --------------------------------------------------------------------------------------


def reference_unit(**overrides: object) -> ServiceUnit:
    fields: dict[str, object] = {
        "unit_id": "u",
        "initial_backlog": 5.0,
        "regular_hours": 8.0,
        "regular_productivity": 1.0,
        "overtime_productivity": 2.0,
        "overtime_max_hours": 3.0,
        "surge_max": 4.0,
        "regular_wage": 20.0,
        "overtime_wage": 30.0,
        "surge_price": 12.0,
        "backlog_penalty": 2.0,
        "terminal_penalty": 5.0,
        "overtime_total_hours": 6.0,
        "surge_total": 8.0,
        "terminal_backlog_max": 10.0,
    }
    fields.update(overrides)
    return make_unit((10.0, 10.0, 10.0), **fields)  # type: ignore[arg-type]


def reference_problem(budget: float | None = 1000.0, **overrides: object) -> PlanningProblem:
    return make_problem(reference_unit(**overrides), budget=budget)


REFERENCE = {
    "served": (12.0, 13.0, 9.0),
    "overtime": (1.0, 1.5, 0.5),
    "surge": (2.0, 2.0, 1.0),
    "backlog": (3.0, 0.0, 1.0),
}


def reference_plan(**changes: dict[int, float]) -> Plan:
    """The reference plan with some day values replaced: ``served={2: 7.0}`` etc."""
    columns = {name: list(values) for name, values in REFERENCE.items()}
    for name, by_day in changes.items():
        for day, value in by_day.items():
            columns[name][day] = value
    return Plan(
        (
            unit_plan(
                tuple(columns["served"]),
                tuple(columns["overtime"]),
                tuple(columns["surge"]),
                tuple(columns["backlog"]),
            ),
        )
    )


# --------------------------------------------------------------------------------------
# 1. ServiceUnit / PlanningProblem validation and derived quantities
# --------------------------------------------------------------------------------------


def test_service_unit_rejects_sequences_whose_length_differs_from_demand() -> None:
    unit = make_unit((1.0, 2.0, 3.0))
    with pytest.raises(PlanningError, match=r"u\.regular_hours has 2 days, demand has 3"):
        replace(unit, regular_hours=(1.0, 2.0))
    with pytest.raises(PlanningError, match=r"u\.surge_max has 4 days, demand has 3"):
        replace(unit, surge_max=(1.0, 1.0, 1.0, 1.0))
    with pytest.raises(PlanningError, match=r"u\.overtime_max_hours has 1 days"):
        replace(unit, overtime_max_hours=(1.0,))
    with pytest.raises(PlanningError, match=r"u\.regular_hours has 3 days, demand has 2"):
        replace(unit, demand=(1.0, 2.0))


def test_service_unit_rejects_lists_instead_of_tuples() -> None:
    unit = make_unit((1.0, 2.0))
    with pytest.raises(PlanningError, match=r"u\.demand must be a tuple"):
        replace(unit, demand=[1.0, 2.0])


@pytest.mark.parametrize("bad", [-1.0, -1e-12, math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", ["demand", "regular_hours", "overtime_max_hours", "surge_max"])
def test_service_unit_rejects_negative_or_non_finite_daily_values(field: str, bad: float) -> None:
    unit = make_unit((1.0, 2.0))
    with pytest.raises(PlanningError, match="must be finite and non-negative"):
        replace(unit, **{field: (1.0, bad)})


@pytest.mark.parametrize(
    "field",
    [
        "initial_backlog",
        "regular_productivity",
        "overtime_productivity",
        "regular_wage",
        "overtime_wage",
        "surge_price",
        "backlog_penalty",
        "terminal_penalty",
        "overtime_total_hours",
        "surge_total",
        "terminal_backlog_max",
    ],
)
@pytest.mark.parametrize("bad", [-0.5, math.nan, math.inf])
def test_service_unit_rejects_negative_or_non_finite_scalars(field: str, bad: float) -> None:
    unit = make_unit((1.0, 2.0))
    with pytest.raises(PlanningError, match="must be finite and non-negative"):
        replace(unit, **{field: bad})


def test_service_unit_rejects_bool_values_daily_and_scalar() -> None:
    unit = make_unit((1.0, 2.0))
    with pytest.raises(PlanningError, match="must be numbers, got bool"):
        replace(unit, demand=(1.0, True))
    with pytest.raises(PlanningError, match="must be numbers, got bool"):
        replace(unit, regular_wage=True)
    with pytest.raises(PlanningError, match="must be numbers, got bool"):
        replace(unit, surge_total=False)
    with pytest.raises(PlanningError, match="must be numbers, got str"):
        replace(unit, demand=(1.0, "2"))


@pytest.mark.parametrize("field", ["regular_productivity", "overtime_productivity"])
def test_service_unit_rejects_zero_productivity(field: str) -> None:
    unit = make_unit((1.0,))
    with pytest.raises(PlanningError, match="productivities must be positive"):
        replace(unit, **{field: 0.0})


@pytest.mark.parametrize("unit_id", ["", "A", "Unit", "1abc", "_a", "a-b", "a b", "é", "a" * 33])
def test_service_unit_rejects_invalid_unit_ids(unit_id: str) -> None:
    with pytest.raises(PlanningError, match="invalid unit id"):
        make_unit((1.0,), unit_id=unit_id)


@pytest.mark.parametrize("unit_id", ["a", "a1", "north_east", "a" * 32])
def test_service_unit_accepts_valid_unit_ids(unit_id: str) -> None:
    assert make_unit((1.0,), unit_id=unit_id).unit_id == unit_id


def test_service_unit_accepts_zero_values_and_none_limits() -> None:
    unit = make_unit(
        (0.0, 0.0),
        regular_hours=0.0,
        overtime_max_hours=0.0,
        surge_max=0.0,
        regular_wage=0.0,
        overtime_wage=0.0,
        surge_price=0.0,
        backlog_penalty=0.0,
    )
    assert unit.horizon == 2
    assert unit.overtime_total_hours is None
    assert unit.max_capacity(0) == 0.0


def test_planning_problem_needs_units_with_unique_ids_and_one_horizon() -> None:
    with pytest.raises(PlanningError, match="at least one service unit"):
        PlanningProblem(start=START, units=())
    with pytest.raises(PlanningError, match="unit ids must be unique"):
        make_problem(make_unit((1.0,), unit_id="a"), make_unit((2.0,), unit_id="a"))
    with pytest.raises(PlanningError, match=r"same horizon, got \[2, 3\]"):
        make_problem(make_unit((1.0, 1.0), unit_id="a"), make_unit((1.0,) * 3, unit_id="b"))


@pytest.mark.parametrize("horizon", [0, MAX_HORIZON_DAYS + 1, 45])
def test_planning_problem_rejects_horizons_outside_1_to_30(horizon: int) -> None:
    with pytest.raises(PlanningError, match=rf"1\.\.30 days, got {horizon}"):
        make_problem(make_unit((1.0,) * horizon))


@pytest.mark.parametrize("horizon", [1, MAX_HORIZON_DAYS])
def test_planning_problem_accepts_horizons_1_and_30(horizon: int) -> None:
    assert make_problem(make_unit((1.0,) * horizon)).horizon == horizon


@pytest.mark.parametrize("budget", [-1.0, math.nan, math.inf, True])
def test_planning_problem_rejects_bad_budget(budget: float) -> None:
    with pytest.raises(PlanningError, match="discretionary_budget"):
        make_problem(make_unit((1.0,)), budget=budget)


def test_planning_problem_dates_are_consecutive_from_start() -> None:
    problem = PlanningProblem(start=date(2024, 2, 27), units=(make_unit((1.0,) * 4),))
    # 2024 is a leap year: 27 Feb, 28 Feb, 29 Feb, 1 Mar.
    assert problem.dates == (
        date(2024, 2, 27),
        date(2024, 2, 28),
        date(2024, 2, 29),
        date(2024, 3, 1),
    )


def test_regular_cost_is_wage_times_hours_summed_over_days_and_units() -> None:
    a = make_unit((1.0, 1.0, 1.0), unit_id="a", regular_hours=(8.0, 6.0, 0.0), regular_wage=20.0)
    b = make_unit((1.0, 1.0, 1.0), unit_id="b", regular_hours=2.5, regular_wage=40.0)
    # a: 20 * (8 + 6 + 0) = 280 USD; b: 40 * 2.5 * 3 = 300 USD; problem 580 USD.
    assert a.regular_cost == approx(280.0)
    assert b.regular_cost == approx(300.0)
    assert make_problem(a, b).regular_cost == approx(580.0)


def test_capacities_and_overtime_cost_per_request() -> None:
    unit = make_unit(
        (1.0, 1.0),
        regular_hours=(8.0, 4.0),
        regular_productivity=1.5,
        overtime_productivity=2.5,
        overtime_max_hours=(2.0, 0.0),
        surge_max=(0.0, 7.0),
        overtime_wage=45.0,
    )
    # day 0: 1.5*8 = 12 regular; + 2.5*2 + 0 = 17.  day 1: 1.5*4 = 6; + 0 + 7 = 13.
    assert unit.regular_capacity(0) == approx(12.0)
    assert unit.regular_capacity(1) == approx(6.0)
    assert unit.max_capacity(0) == approx(17.0)
    assert unit.max_capacity(1) == approx(13.0)
    # 45 USD/h / 2.5 requests/h = 18 USD/request.
    assert unit.overtime_cost_per_request == approx(18.0)


def test_unit_lookup_by_id() -> None:
    a, b = make_unit((1.0,), unit_id="a"), make_unit((1.0,), unit_id="b")
    problem = make_problem(a, b)
    assert problem.unit("b") is b
    with pytest.raises(PlanningError, match="unknown unit 'c'"):
        problem.unit("c")
    plan = Plan((unit_plan((1.0,), (0.0,), (0.0,), (0.0,), unit_id="a"),))
    assert plan.unit("a").unit_id == "a"
    with pytest.raises(PlanningError, match="plan has no unit 'b'"):
        plan.unit("b")


def test_unit_plan_rejects_ragged_or_non_finite_sequences() -> None:
    with pytest.raises(PlanningError, match="sequences differ in length"):
        unit_plan((1.0, 2.0), (0.0,), (0.0, 0.0), (0.0, 0.0))
    with pytest.raises(PlanningError, match="served must be finite numbers"):
        unit_plan((math.nan,), (0.0,), (0.0,), (0.0,))
    with pytest.raises(PlanningError, match="backlog must be finite numbers"):
        unit_plan((1.0,), (0.0,), (0.0,), (math.inf,))


# --------------------------------------------------------------------------------------
# 2. backlog_path and evaluate_plan
# --------------------------------------------------------------------------------------


def test_backlog_path_follows_the_balance_without_clipping() -> None:
    # 5 + 10 - 12 = 3; 3 + 0 - 4 = -1 (not clipped); -1 + 3 - 0 = 2.
    assert backlog_path(5.0, (10.0, 0.0, 3.0), (12.0, 4.0, 0.0)) == [3.0, -1.0, 2.0]
    assert backlog_path(7.0, (), ()) == []


def test_backlog_path_requires_equal_lengths() -> None:
    with pytest.raises(ValueError, match=r"shorter|longer"):
        backlog_path(0.0, (1.0, 2.0), (1.0,))


def test_evaluate_plan_reference_every_field() -> None:
    problem = reference_problem()
    evaluation = evaluate_plan(problem, reference_plan())
    # overtime 1 + 1.5 + 0.5 = 3 h at 30 USD/h = 90 USD.
    assert evaluation.overtime_hours == approx(3.0)
    assert evaluation.overtime_cost == approx(90.0)
    # surge 2 + 2 + 1 = 5 requests at 12 USD = 60 USD.
    assert evaluation.surge_requests == approx(5.0)
    assert evaluation.surge_cost == approx(60.0)
    # discretionary cash = 90 + 60 = 150; regular = 20 USD/h * 8 h * 3 days = 480.
    assert evaluation.discretionary_cost == approx(150.0)
    assert evaluation.regular_cost == approx(480.0)
    assert evaluation.operating_cost == approx(630.0)
    # backlog (3, 0, 1): w = 2 on every end-of-day backlog incl. the last: 2 * 4 = 8.
    assert evaluation.backlog_penalty == approx(8.0)
    # w_T = 5 on the last day's backlog only: 5 * 1 = 5.
    assert evaluation.terminal_penalty == approx(5.0)
    # score = 150 + 8 + 5 = 163 (the committed regular cost 480 is excluded).
    assert evaluation.penalised_score == approx(163.0)
    assert evaluation.total_demand == approx(30.0)
    assert evaluation.initial_backlog == approx(5.0)
    assert evaluation.total_served == approx(34.0)  # 12 + 13 + 9
    assert evaluation.backlog_request_days == approx(4.0)  # 3 + 0 + 1
    assert evaluation.terminal_backlog == approx(1.0)
    assert evaluation.peak_backlog == approx(3.0)
    assert evaluation.days_with_backlog == 2  # days 1 and 3
    # extra = q*o + s, used = max(0, x - 8):
    #   day1 extra 2+2 = 4, used 4 -> 0; day2 3+2 = 5, used 5 -> 0; day3 1+1 = 2, used 1 -> 1.
    assert evaluation.idle_paid_capacity == approx(1.0)
    assert evaluation.discretionary_budget == 1000.0
    assert evaluation.budget_remaining == approx(850.0)  # 1000 - 150
    # Conservation: initial + demand - served = terminal backlog: 5 + 30 - 34 = 1.
    assert evaluation.initial_backlog + evaluation.total_demand - evaluation.total_served == approx(
        evaluation.terminal_backlog
    )


def test_evaluate_plan_to_dict_has_every_field() -> None:
    evaluation = evaluate_plan(reference_problem(), reference_plan())
    data = evaluation.to_dict()
    assert set(data) == set(evaluation.__dataclass_fields__)
    assert data["penalised_score"] == approx(163.0)


def test_evaluate_plan_without_budget_reports_no_remaining_budget() -> None:
    evaluation = evaluate_plan(reference_problem(budget=None), reference_plan())
    assert evaluation.discretionary_budget is None
    assert evaluation.budget_remaining is None


def test_evaluate_plan_reports_a_negative_remaining_budget_when_overspent() -> None:
    evaluation = evaluate_plan(reference_problem(budget=100.0), reference_plan())
    assert evaluation.budget_remaining == approx(-50.0)  # 100 - 150


def test_evaluate_plan_two_units_sums_backlog_by_day() -> None:
    a = make_unit((5.0, 5.0), unit_id="a", backlog_penalty=1.0, terminal_penalty=10.0)
    b = make_unit((5.0, 5.0), unit_id="b", backlog_penalty=3.0, terminal_penalty=0.0)
    plan = Plan(
        (
            unit_plan((2.0, 8.0), (0.0, 0.0), (0.0, 0.0), (3.0, 0.0), unit_id="a"),
            unit_plan((3.0, 3.0), (0.0, 0.0), (0.0, 0.0), (2.0, 4.0), unit_id="b"),
        )
    )
    evaluation = evaluate_plan(make_problem(a, b), plan)
    # by day: 3 + 2 = 5, 0 + 4 = 4; peak is of the daily total 5, not of one unit (4).
    assert evaluation.peak_backlog == approx(5.0)
    assert evaluation.days_with_backlog == 2
    assert evaluation.backlog_request_days == approx(9.0)
    # w: a 1 * (3 + 0) = 3, b 3 * (2 + 4) = 18 -> 21; w_T: a 10 * 0 + b 0 * 4 = 0.
    assert evaluation.backlog_penalty == approx(21.0)
    assert evaluation.terminal_penalty == approx(0.0)
    assert evaluation.terminal_backlog == approx(4.0)
    assert evaluation.penalised_score == approx(21.0)
    # regular cost 20 * 10 * 2 per unit = 400, two units = 800.
    assert evaluation.regular_cost == approx(800.0)
    assert evaluation.operating_cost == approx(800.0)


def test_evaluate_plan_ignores_tiny_backlog_when_counting_days() -> None:
    unit = make_unit((5.0, 5.0))
    plan = Plan((unit_plan((5.0, 4.0), (0.0, 0.0), (0.0, 0.0), (1e-9, 1.0)),))
    evaluation = evaluate_plan(make_problem(unit), plan)
    assert evaluation.days_with_backlog == 1


def test_evaluate_plan_counts_unused_overtime_and_surge_as_idle_even_below_regular() -> None:
    # Regular capacity 10; served 7 < 10, so none of the 2*1 + 3 = 5 bought requests is used.
    unit = make_unit((7.0,))
    plan = Plan((unit_plan((7.0,), (1.0,), (3.0,), (0.0,)),))
    assert evaluate_plan(make_problem(unit), plan).idle_paid_capacity == approx(5.0)


def test_evaluate_plan_rejects_mismatched_plans() -> None:
    problem = reference_problem()
    wrong_id = Plan((replace(reference_plan().units[0], unit_id="v"),))
    with pytest.raises(PlanningError, match="do not match problem units"):
        evaluate_plan(problem, wrong_id)
    short = Plan((unit_plan((1.0, 1.0), (0.0, 0.0), (0.0, 0.0), (0.0, 0.0)),))
    with pytest.raises(PlanningError, match="has 2 days, the problem 3"):
        evaluate_plan(problem, short)


# --------------------------------------------------------------------------------------
# 3. check_plan: one plan per constraint
# --------------------------------------------------------------------------------------


def test_reference_plan_is_feasible() -> None:
    check = check_plan(reference_problem(), reference_plan())
    assert check.feasible
    assert check.violations == ()
    assert check.max_balance_residual == approx(0.0)


# Each case: (problem overrides, budget, plan changes, constraint, unit, day, amount, units).
# Day indices are 0-based. Derivations:
#   nonnegative_served: day3 x=-1, o=s=0 -> b3 = 0+10+1 = 11 (terminal target lifted).
#   nonnegative_overtime: day3 o=-1, s=1 -> capacity 8-2+1 = 7, x=7, b3 = 3.
#   nonnegative_surge: day3 o=0.5, s=-1 -> capacity 8+1-1 = 8, x=8, b3 = 2.
#   balance: day3 recorded b3 = 2 instead of 0+10-9 = 1 -> residual 1.
#   capacity: day3 o=0, s=1 -> capacity 9 < x=10, b3 = 0.
#   overtime_daily_limit: day3 o=3.4 > 3 (total 1+1.5+3.4 = 5.9 <= 6).
#   surge_daily_limit: day1 o=2, s=0 (capacity 12), day3 s=4.5 > 4 (total 0+2+4.5 = 6.5 <= 8).
#   overtime_total_limit: o = (3, 3, 0.5), total 6.5 > 6 (each day <= 3).
#   surge_total_limit: s = (4, 4, 1), total 9 > 8 (each day <= 4).
#   terminal_backlog: day2 x=10 -> b2 = 3; day3 x=0, o=s=0 -> b3 = 13 > 10.
#   budget: the reference plan's 150 USD against a budget of 100 USD.
CASES = [
    (
        {"terminal_backlog_max": None},
        1000.0,
        {"served": {2: -1.0}, "overtime": {2: 0.0}, "surge": {2: 0.0}, "backlog": {2: 11.0}},
        "nonnegative_served",
        2,
        1.0,
        "requests_per_day",
    ),
    (
        {},
        1000.0,
        {"served": {2: 7.0}, "overtime": {2: -1.0}, "backlog": {2: 3.0}},
        "nonnegative_overtime",
        2,
        1.0,
        "staff_hours_per_day",
    ),
    (
        {},
        1000.0,
        {"served": {2: 8.0}, "surge": {2: -1.0}, "backlog": {2: 2.0}},
        "nonnegative_surge",
        2,
        1.0,
        "requests_per_day",
    ),
    ({}, 1000.0, {"backlog": {2: 2.0}}, "balance", 2, 1.0, "requests"),
    (
        {},
        1000.0,
        {"served": {2: 10.0}, "overtime": {2: 0.0}, "backlog": {2: 0.0}},
        "capacity",
        2,
        1.0,
        "requests_per_day",
    ),
    ({}, 1000.0, {"overtime": {2: 3.4}}, "overtime_daily_limit", 2, 0.4, "staff_hours_per_day"),
    (
        {},
        1000.0,
        {"overtime": {0: 2.0}, "surge": {0: 0.0, 2: 4.5}},
        "surge_daily_limit",
        2,
        0.5,
        "requests_per_day",
    ),
    (
        {},
        1000.0,
        {"overtime": {0: 3.0, 1: 3.0}},
        "overtime_total_limit",
        None,
        0.5,
        "staff_hours",
    ),
    ({}, 1000.0, {"surge": {0: 4.0, 1: 4.0}}, "surge_total_limit", None, 1.0, "requests"),
    (
        {},
        1000.0,
        {
            "served": {1: 10.0, 2: 0.0},
            "overtime": {2: 0.0},
            "surge": {2: 0.0},
            "backlog": {1: 3.0, 2: 13.0},
        },
        "terminal_backlog",
        None,
        3.0,
        "requests",
    ),
    ({}, 100.0, {}, "budget", None, 50.0, "usd"),
]


@pytest.mark.parametrize(
    ("overrides", "budget", "changes", "constraint", "day", "amount", "units"),
    CASES,
    ids=[case[3] for case in CASES],
)
def test_check_plan_reports_exactly_the_violated_constraint(
    overrides: dict[str, object],
    budget: float,
    changes: dict[str, dict[int, float]],
    constraint: str,
    day: int | None,
    amount: float,
    units: str,
) -> None:
    problem = reference_problem(budget=budget, **overrides)
    check = check_plan(problem, reference_plan(**changes))
    assert not check.feasible
    assert check.constraints_violated() == [constraint]
    assert len(check.violations) == 1
    violation = check.violations[0]
    assert violation.constraint == constraint
    assert violation.unit_id == (None if constraint == "budget" else "u")
    assert violation.day == day
    assert violation.amount == approx(amount)
    assert violation.units == units
    assert 0 < violation.allowed < 1e-5
    # The same plan passes when that constraint is deliberately relaxed.
    assert check_plan(problem, reference_plan(**changes), ignore=(constraint,)).feasible


def test_every_constraint_has_a_dedicated_case_or_a_coupled_test() -> None:
    covered = {case[3] for case in CASES} | {"nonnegative_backlog", "available_work"}
    assert covered == set(CONSTRAINTS)


def test_negative_backlog_and_available_work_are_coupled_by_the_balance() -> None:
    # With the balance satisfied, b[t] = work - x[t], so b[t] < 0 exactly when x[t] > work:
    # neither can be violated alone. Day3: o=0.5, s=2 -> capacity 11, x = 11 > work 10,
    # b3 = 0 + 10 - 11 = -1 (surge total 2+2+2 = 6 <= 8).
    plan = reference_plan(served={2: 11.0}, surge={2: 2.0}, backlog={2: -1.0})
    problem = reference_problem()
    check = check_plan(problem, plan)
    assert check.constraints_violated() == ["available_work", "nonnegative_backlog"]
    by_name = {v.constraint: v for v in check.violations}
    assert by_name["nonnegative_backlog"].amount == approx(1.0)
    assert by_name["nonnegative_backlog"].units == "requests"
    assert by_name["nonnegative_backlog"].day == 2
    assert by_name["available_work"].amount == approx(1.0)
    assert by_name["available_work"].units == "requests_per_day"
    only_backlog = check_plan(problem, plan, ignore=("available_work",))
    assert only_backlog.constraints_violated() == ["nonnegative_backlog"]
    only_work = check_plan(problem, plan, ignore=("nonnegative_backlog",))
    assert only_work.constraints_violated() == ["available_work"]


def test_available_work_with_nonnegative_recorded_backlog_breaks_the_balance() -> None:
    # Same day3 decisions but b3 recorded as 0: x = 11 > work 10 and |0 - (-1)| = 1.
    plan = reference_plan(served={2: 11.0}, surge={2: 2.0}, backlog={2: 0.0})
    check = check_plan(reference_problem(), plan)
    assert check.constraints_violated() == ["available_work", "balance"]
    assert check.max_balance_residual == approx(1.0)


def test_check_plan_records_the_largest_balance_residual() -> None:
    # day1 b recorded 3.25 (residual 0.25); day2 then expects 3.25 + 10 - 13 = 0.25,
    # recorded 0 (residual 0.25); day3 b recorded 2 vs 1 (residual 1).
    plan = reference_plan(backlog={0: 3.25, 2: 2.0})
    check = check_plan(reference_problem(), plan)
    assert check.max_balance_residual == approx(1.0)
    assert [v.day for v in check.violations] == [0, 1, 2]
    assert {v.constraint for v in check.violations} == {"balance"}


def test_check_plan_accepts_violations_within_tolerance() -> None:
    # day2 serves 13 + 1e-9: capacity and available work exceeded by 1e-9, b2 = -1e-9;
    # day3 b3 = -1e-9 + 10 - 9 = 1 - 1e-9; and 1e-9 USD over a budget of exactly 150.
    tiny = 1e-9
    plan = reference_plan(served={1: 13.0 + tiny}, backlog={1: -tiny, 2: 1.0 - tiny})
    assert check_plan(reference_problem(budget=150.0 - tiny), plan).feasible
    # A balance residual of 1e-9 also passes and is still reported as the residual.
    check = check_plan(reference_problem(), reference_plan(backlog={2: 1.0 + tiny}))
    assert check.feasible
    assert check.max_balance_residual == pytest.approx(tiny, rel=1e-3)


def test_check_plan_rejects_unknown_ignored_constraints() -> None:
    with pytest.raises(PlanningError, match=r"unknown constraint\(s\) \['no_such_rule'\]"):
        check_plan(reference_problem(), reference_plan(), ignore=("budget", "no_such_rule"))


def test_check_plan_rejects_mismatched_plan_units_or_horizon() -> None:
    problem = reference_problem()
    with pytest.raises(PlanningError, match="do not match problem units"):
        check_plan(problem, Plan((replace(reference_plan().units[0], unit_id="v"),)))
    with pytest.raises(PlanningError, match="do not match problem units"):
        check_plan(problem, Plan(()))
    four_days = Plan((unit_plan((1.0,) * 4, (0.0,) * 4, (0.0,) * 4, (0.0,) * 4),))
    with pytest.raises(PlanningError, match="has 4 days, the problem 3"):
        check_plan(problem, four_days)


def test_violation_describe_uses_one_based_days() -> None:
    plan = reference_plan(served={2: 10.0}, overtime={2: 0.0}, backlog={2: 0.0})
    (violation,) = check_plan(reference_problem(), plan).violations
    assert violation.describe() == "capacity u day 3: exceeded by 1 requests_per_day"
    (budget,) = check_plan(reference_problem(budget=100.0), reference_plan()).violations
    assert budget.describe() == "budget: exceeded by 50 usd"


# --------------------------------------------------------------------------------------
# 4. least_terminal_backlog
# --------------------------------------------------------------------------------------


def test_least_terminal_backlog_with_surge_every_day() -> None:
    # max capacity 8 + 2*3 + 4 = 18/day; initial 5, demand (20, 30, 5):
    # max(0, 5+20-18) = 7; max(0, 7+30-18) = 19; max(0, 19+5-18) = 6.
    unit = reference_unit(surge_max=4.0)
    unit = replace(unit, demand=(20.0, 30.0, 5.0))
    assert least_terminal_backlog(unit) == approx(6.0)


def test_least_terminal_backlog_with_delayed_surge() -> None:
    # surge only on day 3: capacity (14, 14, 18):
    # 5+20-14 = 11; 11+30-14 = 27; 27+5-18 = 14.
    unit = replace(reference_unit(), demand=(20.0, 30.0, 5.0), surge_max=(0.0, 0.0, 4.0))
    assert least_terminal_backlog(unit) == approx(14.0)


def test_least_terminal_backlog_clips_at_zero_and_ignores_horizon_totals() -> None:
    # capacity 14/day (no surge): max(0, 0+0-14) = 0, then 0+30-14 = 16 (not 30-28 = 2).
    # The overtime total of 0 h is ignored (the result is a lower bound).
    unit = make_unit(
        (0.0, 30.0),
        regular_hours=8.0,
        overtime_productivity=2.0,
        overtime_max_hours=3.0,
        surge_max=0.0,
        overtime_total_hours=0.0,
    )
    assert least_terminal_backlog(unit) == approx(16.0)


def test_least_terminal_backlog_is_zero_when_capacity_suffices() -> None:
    # Reference unit: 18/day against 5 + 10 on day 1 and 10 after.
    assert least_terminal_backlog(reference_unit()) == 0.0


# --------------------------------------------------------------------------------------
# 5. fixed_capacity_plan
# --------------------------------------------------------------------------------------


def fixed_example(**overrides: object) -> PlanningProblem:
    fields: dict[str, object] = {
        "initial_backlog": 3.0,
        "regular_hours": (8.0, 8.0, 4.0, 8.0),
        "regular_productivity": 1.5,
        "overtime_wage": 1.0,
        "surge_price": 0.0,
        "overtime_max_hours": 10.0,
        "surge_max": 50.0,
    }
    fields.update(overrides)
    return make_problem(make_unit((10.0, 15.0, 5.0, 2.0), **fields))  # type: ignore[arg-type]


def test_fixed_capacity_plan_hand_example() -> None:
    # regular capacity 1.5 * (8, 8, 4, 8) = (12, 12, 6, 12); initial backlog 3:
    #   day1 work 13 -> serve 12, b 1; day2 work 16 -> 12, b 4;
    #   day3 work 9 -> 6, b 3;       day4 work 5 -> 5, b 0.
    problem = fixed_example()
    (decisions,) = fixed_capacity_plan(problem).units
    assert decisions.unit_id == "u"
    assert decisions.served == pytest.approx((12.0, 12.0, 6.0, 5.0), abs=EXACT)
    assert decisions.backlog == pytest.approx((1.0, 4.0, 3.0, 0.0), abs=EXACT)
    # Never buys overtime or surge, even when surge is free and plentiful.
    assert decisions.overtime_hours == (0.0, 0.0, 0.0, 0.0)
    assert decisions.surge == (0.0, 0.0, 0.0, 0.0)
    assert check_plan(problem, fixed_capacity_plan(problem)).feasible


def test_fixed_capacity_plan_conserves_backlog_and_costs_only_regular() -> None:
    problem = fixed_example()
    evaluation = evaluate_plan(problem, fixed_capacity_plan(problem))
    # 3 + (10 + 15 + 5 + 2) - (12 + 12 + 6 + 5) = 35 - 35 = 0.
    assert evaluation.total_served == approx(35.0)
    assert evaluation.terminal_backlog == approx(0.0)
    assert evaluation.discretionary_cost == 0.0
    assert evaluation.idle_paid_capacity == 0.0
    # w = 2 on (1 + 4 + 3 + 0) = 8 request-days -> 16; the score has no cash.
    assert evaluation.penalised_score == approx(16.0)


def test_fixed_capacity_plan_can_miss_the_terminal_target() -> None:
    # Last-day demand 20: day4 work 3 + 20 = 23, serves 12, backlog 11 > target 0.
    unit = make_unit(
        (10.0, 15.0, 5.0, 20.0),
        initial_backlog=3.0,
        regular_hours=(8.0, 8.0, 4.0, 8.0),
        regular_productivity=1.5,
        terminal_backlog_max=0.0,
        overtime_total_hours=0.0,
        surge_total=0.0,
    )
    problem = make_problem(unit, budget=0.0)
    plan = fixed_capacity_plan(problem)
    assert plan.units[0].backlog[-1] == approx(11.0)
    check = check_plan(problem, plan)
    assert check.constraints_violated() == ["terminal_backlog"]
    assert check.violations[0].amount == approx(11.0)


def test_fixed_capacity_plan_handles_zero_regular_hours_and_several_units() -> None:
    a = make_unit((4.0, 4.0), unit_id="a", regular_hours=0.0, initial_backlog=1.0)
    b = make_unit((4.0, 4.0), unit_id="b", regular_hours=3.0)
    plan = fixed_capacity_plan(make_problem(a, b))
    # a serves nothing: backlog 1+4 = 5, 9.  b: capacity 3 -> backlog 1, 2.
    assert [p.unit_id for p in plan.units] == ["a", "b"]
    assert plan.units[0].served == (0.0, 0.0)
    assert plan.units[0].backlog == pytest.approx((5.0, 9.0))
    assert plan.units[1].served == pytest.approx((3.0, 3.0))
    assert plan.units[1].backlog == pytest.approx((1.0, 2.0))


# --------------------------------------------------------------------------------------
# 6. backlog_trigger_plan
#
# Example unit: regular capacity 10/day, q = 2 requests/h, overtime at most 4 h/day (8
# requests), surge at most 10 requests/day, trigger 5, initial backlog 0, demand
# (12, 20, 30). Overtime costs 30 USD/h / 2 = 15 USD/request.
# --------------------------------------------------------------------------------------


def trigger_unit(**overrides: object) -> ServiceUnit:
    fields: dict[str, object] = {
        "regular_hours": 10.0,
        "overtime_productivity": 2.0,
        "overtime_max_hours": 4.0,
        "surge_max": 10.0,
        "overtime_wage": 30.0,
        "surge_price": 20.0,
    }
    fields.update(overrides)
    return make_unit((12.0, 20.0, 30.0), **fields)  # type: ignore[arg-type]


def decisions_of(problem: PlanningProblem, triggers: dict[str, float]) -> UnitPlan:
    (decisions,) = backlog_trigger_plan(problem, triggers).units
    return decisions


def assert_columns(
    decisions: UnitPlan,
    served: tuple[float, ...],
    overtime: tuple[float, ...],
    surge: tuple[float, ...],
    backlog: tuple[float, ...],
) -> None:
    assert decisions.served == pytest.approx(served, abs=EXACT)
    assert decisions.overtime_hours == pytest.approx(overtime, abs=EXACT)
    assert decisions.surge == pytest.approx(surge, abs=EXACT)
    assert decisions.backlog == pytest.approx(backlog, abs=EXACT)


def test_backlog_trigger_overtime_cheaper_then_surge_when_daily_overtime_runs_out() -> None:
    # overtime 15 < surge 20 USD/request.
    # day1 work 12, 12 - 10 - 5 < 0: nothing bought, serve 10, b 2.
    # day2 work 22, shortfall 22 - 10 - 5 = 7: 7 requests of overtime = 3.5 h, b 5.
    # day3 work 35, shortfall 20: overtime 8 (4 h, daily limit) + surge 10 (daily limit),
    #      2 left unbought: serve 28, b 7.
    problem = make_problem(trigger_unit())
    decisions = decisions_of(problem, {"u": 5.0})
    assert_columns(
        decisions, (10.0, 17.0, 28.0), (0.0, 3.5, 4.0), (0.0, 0.0, 10.0), (2.0, 5.0, 7.0)
    )
    evaluation = evaluate_plan(problem, Plan((decisions,)))
    assert evaluation.discretionary_cost == approx(425.0)  # 30 * 7.5 + 20 * 10
    assert evaluation.idle_paid_capacity == approx(0.0)


def test_backlog_trigger_surge_cheaper_is_bought_first() -> None:
    # surge 10 < overtime 15 USD/request.
    # day2 shortfall 7 -> surge 7.  day3 shortfall 20 -> surge 10, then overtime 8 (4 h).
    problem = make_problem(trigger_unit(surge_price=10.0))
    assert_columns(
        decisions_of(problem, {"u": 5.0}),
        (10.0, 17.0, 28.0),
        (0.0, 0.0, 4.0),
        (0.0, 7.0, 10.0),
        (2.0, 5.0, 7.0),
    )


def test_backlog_trigger_tie_buys_overtime_first() -> None:
    # 30 / 2 = 15 USD/request for both sources: overtime first.
    problem = make_problem(trigger_unit(surge_price=15.0))
    assert_columns(
        decisions_of(problem, {"u": 5.0}),
        (10.0, 17.0, 28.0),
        (0.0, 3.5, 4.0),
        (0.0, 0.0, 10.0),
        (2.0, 5.0, 7.0),
    )


def test_backlog_trigger_compares_overtime_per_request_not_per_hour() -> None:
    # Overtime 18 USD/h looks dearer than surge 12 USD/request, but at q = 2 it is
    # 9 USD/request, so overtime is bought first: day2 7 requests = 3.5 h.
    problem = make_problem(trigger_unit(overtime_wage=18.0, surge_price=12.0))
    decisions = decisions_of(problem, {"u": 5.0})
    assert decisions.overtime_hours[1] == approx(3.5)
    assert decisions.surge[1] == 0.0


def test_backlog_trigger_falls_back_to_surge_when_overtime_total_is_spent() -> None:
    # overtime total 5 h: day2 uses 3.5 h (7 requests), 1.5 h left.
    # day3 shortfall 20: overtime 1.5 h = 3 requests, surge 10, 7 unbought:
    #      serve 10 + 3 + 10 = 23, b 35 - 23 = 12.
    problem = make_problem(trigger_unit(overtime_total_hours=5.0))
    decisions = decisions_of(problem, {"u": 5.0})
    assert_columns(
        decisions, (10.0, 17.0, 23.0), (0.0, 3.5, 1.5), (0.0, 0.0, 10.0), (2.0, 5.0, 12.0)
    )
    assert check_plan(problem, Plan((decisions,))).feasible


def test_backlog_trigger_falls_back_to_overtime_when_surge_total_is_spent() -> None:
    # surge cheaper (10), surge total 9: day2 surge 7 (2 left);
    # day3 shortfall 20: surge 2, overtime 8 (4 h), 10 unbought: serve 20, b 15.
    problem = make_problem(trigger_unit(surge_price=10.0, surge_total=9.0))
    assert_columns(
        decisions_of(problem, {"u": 5.0}),
        (10.0, 17.0, 20.0),
        (0.0, 0.0, 4.0),
        (0.0, 7.0, 2.0),
        (2.0, 5.0, 15.0),
    )


def test_backlog_trigger_falls_back_to_overtime_before_surge_is_available() -> None:
    # surge cheaper (10) but only from day 3 (surge_max (0, 0, 10)):
    # day2 shortfall 7 -> overtime 3.5 h; day3 shortfall 20 -> surge 10, overtime 8 (4 h).
    problem = make_problem(trigger_unit(surge_price=10.0, surge_max=(0.0, 0.0, 10.0)))
    decisions = decisions_of(problem, {"u": 5.0})
    assert_columns(
        decisions, (10.0, 17.0, 28.0), (0.0, 3.5, 4.0), (0.0, 0.0, 10.0), (2.0, 5.0, 7.0)
    )
    assert check_plan(problem, Plan((decisions,))).feasible


def test_backlog_trigger_stops_exactly_at_the_budget() -> None:
    # budget 100 USD, overtime 15 USD/request: day2 buys 100/15 = 6.667 requests
    # (3.333 h = 100 USD), serve 16.667, b 22 - 16.667 = 5.333.
    # day3: nothing affordable, serve 10, b 5.333 + 30 - 10 = 25.333.
    problem = make_problem(trigger_unit(), budget=100.0)
    decisions = decisions_of(problem, {"u": 5.0})
    bought = 100.0 / 15.0
    assert_columns(
        decisions,
        (10.0, 10.0 + bought, 10.0),
        (0.0, bought / 2.0, 0.0),
        (0.0, 0.0, 0.0),
        (2.0, 12.0 - bought, 32.0 - bought),
    )
    plan = Plan((decisions,))
    evaluation = evaluate_plan(problem, plan)
    assert evaluation.discretionary_cost == pytest.approx(100.0, abs=1e-9)
    assert evaluation.budget_remaining == pytest.approx(0.0, abs=1e-9)
    assert check_plan(problem, plan).feasible


def test_backlog_trigger_budget_runs_out_part_way_through_the_cheaper_source() -> None:
    # budget 150: day2 overtime 7 requests = 105 USD (45 left).
    # day3: overtime affordable 45/15 = 3 requests (1.5 h); surge 20 USD/request, 0 left.
    #       serve 13, b 35 - 13 = 22.
    problem = make_problem(trigger_unit(), budget=150.0)
    decisions = decisions_of(problem, {"u": 5.0})
    assert_columns(
        decisions, (10.0, 17.0, 13.0), (0.0, 3.5, 1.5), (0.0, 0.0, 0.0), (2.0, 5.0, 22.0)
    )
    assert check_plan(problem, Plan((decisions,))).feasible


def test_backlog_trigger_zero_price_source_is_affordable_with_no_budget() -> None:
    # Surge is free (0 < 15) and the budget is 0: day2 surge 7; day3 surge 10 and no
    # overtime (15 USD/request, nothing left): serve 20, b 15.
    problem = make_problem(trigger_unit(surge_price=0.0), budget=0.0)
    decisions = decisions_of(problem, {"u": 5.0})
    assert_columns(
        decisions, (10.0, 17.0, 20.0), (0.0, 0.0, 0.0), (0.0, 7.0, 10.0), (2.0, 5.0, 15.0)
    )
    assert check_plan(problem, Plan((decisions,))).feasible


def test_backlog_trigger_free_overtime_is_affordable_with_no_budget() -> None:
    # Overtime wage 0 -> 0 USD/request: day2 3.5 h, day3 4 h; surge 20 unaffordable.
    problem = make_problem(trigger_unit(overtime_wage=0.0), budget=0.0)
    assert_columns(
        decisions_of(problem, {"u": 5.0}),
        (10.0, 17.0, 18.0),
        (0.0, 3.5, 4.0),
        (0.0, 0.0, 0.0),
        (2.0, 5.0, 17.0),
    )


def test_backlog_trigger_with_zero_trigger_clears_the_backlog_when_possible() -> None:
    # trigger 0: day1 shortfall 2 -> overtime 1 h, b 0; day2 shortfall 10 -> overtime 8
    # (4 h) + surge 2, b 0; day3 shortfall 20 -> overtime 8 + surge 10, b 2.
    problem = make_problem(trigger_unit())
    assert_columns(
        decisions_of(problem, {"u": 0.0}),
        (12.0, 20.0, 28.0),
        (1.0, 4.0, 4.0),
        (0.0, 2.0, 10.0),
        (0.0, 0.0, 2.0),
    )


def test_backlog_trigger_buys_nothing_below_the_trigger() -> None:
    # trigger 100 above every day's excess work: identical to fixed capacity.
    problem = make_problem(trigger_unit())
    decisions = decisions_of(problem, {"u": 100.0})
    assert_columns(
        decisions, (10.0, 10.0, 10.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (2.0, 12.0, 32.0)
    )
    # Exactly at the trigger: day1 work 12 - 10 = 2 = trigger, nothing bought.
    assert decisions_of(problem, {"u": 2.0}).overtime_hours[0] == 0.0


def test_backlog_trigger_requires_a_valid_trigger_for_every_unit() -> None:
    problem = make_problem(trigger_unit(unit_id="a"), trigger_unit(unit_id="b"))
    with pytest.raises(PlanningError, match=r"no backlog trigger for unit\(s\) \['b'\]"):
        backlog_trigger_plan(problem, {"a": 1.0})
    for bad in (-1.0, math.nan, math.inf):
        with pytest.raises(PlanningError, match="trigger of b must be finite and non-negative"):
            backlog_trigger_plan(problem, {"a": 1.0, "b": bad})


def two_unit_problem(budget: float, order: tuple[str, str] = ("a", "b")) -> PlanningProblem:
    # Two identical units, demand (20, 20), regular capacity 10, overtime 15 USD/request
    # (8 requests/day max), no surge.
    units = {
        uid: make_unit((20.0, 20.0), unit_id=uid, surge_max=0.0, overtime_wage=30.0)
        for uid in order
    }
    return make_problem(*(units[uid] for uid in order), budget=budget)


def test_backlog_trigger_shared_budget_is_consumed_in_unit_order_within_a_day() -> None:
    # budget 100, trigger 5: day1 a shortfall 20 - 10 - 5 = 5 -> 75 USD (2.5 h), 25 left;
    # b shortfall 5 -> 25/15 = 1.667 requests (0.833 h), 0 left; day2 nothing.
    plan = backlog_trigger_plan(two_unit_problem(100.0), {"a": 5.0, "b": 5.0})
    a, b = plan.units
    assert a.overtime_hours == pytest.approx((2.5, 0.0), abs=EXACT)
    assert b.overtime_hours == pytest.approx((25.0 / 30.0, 0.0), abs=EXACT)
    assert a.backlog == pytest.approx((5.0, 15.0), abs=EXACT)
    assert b.backlog == pytest.approx((10.0 - 25.0 / 15.0, 20.0 - 25.0 / 15.0), abs=EXACT)
    # Reversing the unit order gives b the first claim.
    reversed_plan = backlog_trigger_plan(two_unit_problem(100.0, ("b", "a")), {"a": 5.0, "b": 5.0})
    assert reversed_plan.unit("b").overtime_hours == pytest.approx((2.5, 0.0), abs=EXACT)
    assert reversed_plan.unit("a").overtime_hours == pytest.approx((25.0 / 30.0, 0.0), abs=EXACT)


def test_backlog_trigger_processes_days_before_units() -> None:
    # budget 150: day1 a 75 USD, b 75 USD -> nothing left for a on day2. A unit-by-unit
    # pass would instead spend 75 + 75 on a's two days and leave b with nothing.
    plan = backlog_trigger_plan(two_unit_problem(150.0), {"a": 5.0, "b": 5.0})
    assert plan.unit("a").overtime_hours == pytest.approx((2.5, 0.0), abs=EXACT)
    assert plan.unit("b").overtime_hours == pytest.approx((2.5, 0.0), abs=EXACT)
    problem = two_unit_problem(150.0)
    evaluation = evaluate_plan(problem, plan)
    assert evaluation.discretionary_cost == approx(150.0)
    assert evaluation.idle_paid_capacity == approx(0.0)
    assert check_plan(problem, plan).feasible


def test_backlog_trigger_uses_each_units_own_trigger() -> None:
    # Unlimited budget; a trigger 0, b trigger 8.
    # a: day1 shortfall 10 -> 8 (daily limit), b 2; day2 work 22, shortfall 12 -> 8, b 4.
    # b: day1 shortfall 20 - 10 - 8 = 2 -> 2, b 8; day2 work 28, shortfall 10 -> 8, b 10.
    problem = make_problem(
        make_unit((20.0, 20.0), unit_id="a", surge_max=0.0),
        make_unit((20.0, 20.0), unit_id="b", surge_max=0.0),
    )
    plan = backlog_trigger_plan(problem, {"a": 0.0, "b": 8.0})
    assert plan.unit("a").overtime_hours == pytest.approx((4.0, 4.0), abs=EXACT)
    assert plan.unit("a").backlog == pytest.approx((2.0, 4.0), abs=EXACT)
    assert plan.unit("b").overtime_hours == pytest.approx((1.0, 4.0), abs=EXACT)
    assert plan.unit("b").backlog == pytest.approx((8.0, 10.0), abs=EXACT)


# --------------------------------------------------------------------------------------
# Property tests: both policies on random small problems.
# --------------------------------------------------------------------------------------

_NON_NEG = st.floats(min_value=0.0, max_value=40.0, allow_nan=False, allow_infinity=False)
_PRICE = st.one_of(st.just(0.0), st.floats(min_value=0.1, max_value=50.0))
_POSITIVE = st.floats(min_value=0.25, max_value=3.0)
_OPTIONAL = st.one_of(st.none(), st.floats(min_value=0.0, max_value=30.0))


@st.composite
def small_problems(draw: st.DrawFn) -> tuple[PlanningProblem, dict[str, float]]:
    horizon = draw(st.integers(min_value=1, max_value=4))
    n_units = draw(st.integers(min_value=1, max_value=2))

    def days(strategy: st.SearchStrategy[float]) -> tuple[float, ...]:
        return tuple(draw(st.lists(strategy, min_size=horizon, max_size=horizon)))

    units = []
    triggers = {}
    for index in range(n_units):
        uid = f"u{index}"
        units.append(
            ServiceUnit(
                unit_id=uid,
                demand=days(_NON_NEG),
                initial_backlog=draw(_NON_NEG),
                regular_hours=days(st.floats(min_value=0.0, max_value=15.0)),
                regular_productivity=draw(_POSITIVE),
                overtime_productivity=draw(_POSITIVE),
                overtime_max_hours=days(st.floats(min_value=0.0, max_value=6.0)),
                surge_max=days(st.floats(min_value=0.0, max_value=10.0)),
                regular_wage=draw(_PRICE),
                overtime_wage=draw(_PRICE),
                surge_price=draw(_PRICE),
                backlog_penalty=draw(_PRICE),
                terminal_penalty=draw(_PRICE),
                overtime_total_hours=draw(_OPTIONAL),
                surge_total=draw(_OPTIONAL),
                terminal_backlog_max=draw(_OPTIONAL),
            )
        )
        triggers[uid] = draw(st.floats(min_value=0.0, max_value=20.0))
    budget = draw(st.one_of(st.none(), st.floats(min_value=0.0, max_value=300.0)))
    return make_problem(*units, budget=budget), triggers


def _independent_check(problem: PlanningProblem, plan: Plan) -> None:
    """Every constraint except the terminal target, in plain loops with a 1e-7 tolerance."""
    tol = 1e-7
    cash = 0.0
    for unit, decisions in zip(problem.units, plan.units, strict=True):
        previous = unit.initial_backlog
        for day in range(problem.horizon):
            x = decisions.served[day]
            o = decisions.overtime_hours[day]
            s = decisions.surge[day]
            b = decisions.backlog[day]
            assert x >= -tol
            assert o >= -tol
            assert s >= -tol
            assert b >= -tol
            assert abs(previous + unit.demand[day] - x - b) <= tol * (1 + previous)
            assert x <= previous + unit.demand[day] + tol
            capacity = (
                unit.regular_productivity * unit.regular_hours[day]
                + unit.overtime_productivity * o
                + s
            )
            assert x <= capacity + tol
            assert o <= unit.overtime_max_hours[day] + tol
            assert s <= unit.surge_max[day] + tol
            cash += unit.overtime_wage * o + unit.surge_price * s
            previous = b
        if unit.overtime_total_hours is not None:
            assert sum(decisions.overtime_hours) <= unit.overtime_total_hours + tol
        if unit.surge_total is not None:
            assert sum(decisions.surge) <= unit.surge_total + tol
    if problem.discretionary_budget is not None:
        assert cash <= problem.discretionary_budget + tol * (1 + cash)


@settings(max_examples=40, deadline=None)
@given(small_problems())
def test_policy_plans_satisfy_every_constraint_but_the_terminal_target(
    case: tuple[PlanningProblem, dict[str, float]],
) -> None:
    problem, triggers = case
    for plan in (fixed_capacity_plan(problem), backlog_trigger_plan(problem, triggers)):
        assert [p.unit_id for p in plan.units] == [u.unit_id for u in problem.units]
        check = check_plan(problem, plan)
        assert set(check.constraints_violated()) <= {"terminal_backlog"}
        assert check_plan(problem, plan, ignore=("terminal_backlog",)).feasible
        _independent_check(problem, plan)


@settings(max_examples=40, deadline=None)
@given(small_problems())
def test_policy_plan_structural_properties(
    case: tuple[PlanningProblem, dict[str, float]],
) -> None:
    problem, triggers = case
    fixed = fixed_capacity_plan(problem)
    trigger = backlog_trigger_plan(problem, triggers)
    for unit, f, t in zip(problem.units, fixed.units, trigger.units, strict=True):
        # Fixed capacity: no extra capacity; serves min(work, regular capacity).
        assert all(o == 0.0 for o in f.overtime_hours)
        assert all(s == 0.0 for s in f.surge)
        level = unit.initial_backlog
        for day in range(problem.horizon):
            work = level + unit.demand[day]
            assert f.served[day] == pytest.approx(min(work, unit.regular_capacity(day)))
            level = f.backlog[day]
        # Conservation for both plans: b0 + sum d - sum x = b[T].
        for decisions in (f, t):
            assert unit.initial_backlog + sum(unit.demand) - sum(decisions.served) == (
                pytest.approx(decisions.backlog[-1], abs=1e-7)
            )
        # Trigger: buys only above the trigger, never below it, and never on a day whose
        # excess work is at or under the trigger.
        level = unit.initial_backlog
        for day in range(problem.horizon):
            work = level + unit.demand[day]
            bought = t.overtime_hours[day] > 0 or t.surge[day] > 0
            if work - unit.regular_capacity(day) <= triggers[unit.unit_id]:
                assert not bought
            if bought:
                assert t.backlog[day] >= triggers[unit.unit_id] - 1e-7
            level = t.backlog[day]
        # The trigger plan never leaves more backlog than fixed capacity.
        for day in range(problem.horizon):
            assert t.backlog[day] <= f.backlog[day] + 1e-7
    # Bought capacity is fully used.
    assert evaluate_plan(problem, trigger).idle_paid_capacity == pytest.approx(0.0, abs=1e-7)
    assert evaluate_plan(problem, fixed).idle_paid_capacity == 0.0
