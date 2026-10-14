"""Hypothetical service-planning problems, plans, and their independent evaluation (M4).

This module is the solver-independent half of the M4 decision simulation. It defines

* :class:`PlanningProblem`: a planning horizon of 1-30 days, one or more service units
  coupled only by an optional shared budget, and every operational parameter in explicit
  canonical units (see :data:`UNITS`);
* :class:`Plan`: daily decisions for every unit (requests served, overtime hours, surge
  requests bought) and the resulting end-of-day backlog;
* :func:`evaluate_plan`: the cost breakdown and backlog statistics of any plan, computed
  in plain Python loops;
* :func:`check_plan`: every constraint of the model checked independently of the linear
  programme that produced the plan (the LP matrices are never consulted here).

Model (per unit, day ``t = 1..T``; ``b[0]`` is the initial backlog)::

    b[t] = b[t-1] + d[t] - x[t]                          backlog balance (requests)
    0 <= x[t] <= b[t-1] + d[t]                           service never exceeds the work
    x[t] <= p * H[t] + q * o[t] + s[t]                   service never exceeds capacity
    0 <= o[t] <= O_max[t],   sum_t o[t] <= O_total       overtime limits (staff hours)
    0 <= s[t] <= S_max[t],   sum_t s[t] <= S_total       surge limits (requests)
    b[t] >= 0,               b[T] <= target              backlog (requests)
    sum_units sum_t (c_ot * o[t] + c_s * s[t]) <= D      discretionary cash (USD)

``D`` is the discretionary budget: cash available for overtime and surge over the
horizon, shared by all units. The regular staff cost ``F = sum_t c_reg * H[t]`` is
committed (paid whether or not the hours are used), reported separately and not part of
``D``; it is a constant of the objective and does not affect any decision. Arrivals may be
served on the day they arrive. Quantities are continuous: requests and hours are treated as
divisible, which is an approximation for counts. The "service never exceeds the work"
constraint is implied by the balance and ``b[t] >= 0`` and is checked here explicitly.

The planning objective is a **penalised score** in USD-equivalent units: the discretionary
cash plus two *notional* penalties, ``w`` per request left in the backlog at the end of
each day and ``w_T`` per request left at the end of the horizon. The penalties are
HYPOTHETICAL weights that express a preference for less waiting; they are not cash, are
never counted against the budget, and are always reported separately from the cash. The
score mixes cash with notional penalties, so it is a ranking device for one set of
weights, never a cost: comparisons are reported on cash and backlog separately. The
committed regular cost ``F`` is not part of the score (it is the same for every plan).

Every operational number in an M4 run is ``HYPOTHETICAL`` (see
:mod:`atlas.optimization_config`); nothing in ATLAS measures the staffing, productivity,
costs or service capacity of any NYC agency.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Final

from atlas.errors import AtlasError

#: Planning horizons longer than the M2 forecast horizon are not supported.
MAX_HORIZON_DAYS: Final = 30

#: Canonical units of every quantity in a :class:`PlanningProblem` and a :class:`Plan`.
UNITS: Final = {
    "demand": "requests_per_day",
    "served": "requests_per_day",
    "surge": "requests_per_day",
    "backlog": "requests",
    "initial_backlog": "requests",
    "terminal_backlog_max": "requests",
    "surge_total": "requests",
    "regular_hours": "staff_hours_per_day",
    "overtime_hours": "staff_hours_per_day",
    "overtime_max_hours": "staff_hours_per_day",
    "overtime_total_hours": "staff_hours",
    "regular_productivity": "requests_per_staff_hour",
    "overtime_productivity": "requests_per_staff_hour",
    "regular_wage": "usd_per_staff_hour",
    "overtime_wage": "usd_per_staff_hour",
    "surge_price": "usd_per_request",
    "backlog_penalty": "usd_per_request_day",
    "terminal_penalty": "usd_per_request",
    "discretionary_budget": "usd",
}

#: Absolute part of every feasibility tolerance (requests, staff hours or USD).
FEASIBILITY_ABS_TOL: Final = 1e-6
#: Relative part, applied to the magnitude of the terms of the constraint checked.
FEASIBILITY_REL_TOL: Final = 1e-9

_UNIT_ID: Final = re.compile(r"[a-z][a-z0-9_]{0,31}")


class PlanningError(AtlasError):
    """A planning problem or plan is malformed (wrong lengths, negative or non-finite values)."""


def tolerance(*magnitudes: float) -> float:
    """Allowed violation of a constraint whose terms have the given magnitudes."""
    scale = max((abs(m) for m in magnitudes), default=0.0)
    return FEASIBILITY_ABS_TOL + FEASIBILITY_REL_TOL * scale


def _finite_nonnegative(name: str, values: Iterable[float]) -> None:
    for value in values:
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise PlanningError(f"{name} must be numbers, got {type(value).__name__}")
        if not math.isfinite(value) or value < 0:
            raise PlanningError(f"{name} must be finite and non-negative, got {value!r}")


@dataclass(frozen=True)
class ServiceUnit:
    """One service unit: its demand and every operational parameter, in canonical units.

    Per-day sequences have one value per planning day. ``surge_max`` is zero on days when
    surge capacity is not available (for example before a delayed start). ``None`` for a
    total or for ``terminal_backlog_max`` means that limit is not imposed.
    """

    unit_id: str
    demand: tuple[float, ...]
    initial_backlog: float
    regular_hours: tuple[float, ...]
    regular_productivity: float
    overtime_productivity: float
    overtime_max_hours: tuple[float, ...]
    surge_max: tuple[float, ...]
    regular_wage: float
    overtime_wage: float
    surge_price: float
    backlog_penalty: float
    terminal_penalty: float = 0.0
    overtime_total_hours: float | None = None
    surge_total: float | None = None
    terminal_backlog_max: float | None = None

    def __post_init__(self) -> None:
        if not _UNIT_ID.fullmatch(self.unit_id):
            raise PlanningError(f"invalid unit id {self.unit_id!r}")
        horizon = len(self.demand)
        for name in ("demand", "regular_hours", "overtime_max_hours", "surge_max"):
            values = getattr(self, name)
            if not isinstance(values, tuple):
                raise PlanningError(f"{self.unit_id}.{name} must be a tuple")
            if len(values) != horizon:
                raise PlanningError(
                    f"{self.unit_id}.{name} has {len(values)} days, demand has {horizon}"
                )
            _finite_nonnegative(f"{self.unit_id}.{name}", values)
        scalars = (
            "initial_backlog",
            "regular_productivity",
            "overtime_productivity",
            "regular_wage",
            "overtime_wage",
            "surge_price",
            "backlog_penalty",
            "terminal_penalty",
        )
        _finite_nonnegative(f"{self.unit_id} parameters", [getattr(self, n) for n in scalars])
        optional = ("overtime_total_hours", "surge_total", "terminal_backlog_max")
        _finite_nonnegative(
            f"{self.unit_id} limits",
            [v for v in (getattr(self, n) for n in optional) if v is not None],
        )
        if self.regular_productivity <= 0 or self.overtime_productivity <= 0:
            raise PlanningError(f"{self.unit_id}: productivities must be positive")

    @property
    def horizon(self) -> int:
        return len(self.demand)

    def regular_capacity(self, day: int) -> float:
        """Requests per day the regular staff hours of day ``day`` (0-based) can serve."""
        return self.regular_productivity * self.regular_hours[day]

    def max_capacity(self, day: int) -> float:
        """Regular capacity plus every overtime hour and surge request allowed that day."""
        return (
            self.regular_capacity(day)
            + self.overtime_productivity * self.overtime_max_hours[day]
            + self.surge_max[day]
        )

    @property
    def regular_cost(self) -> float:
        """Cash cost of the regular staff hours over the horizon (fixed, USD)."""
        return math.fsum(self.regular_wage * hours for hours in self.regular_hours)

    @property
    def overtime_cost_per_request(self) -> float:
        return self.overtime_wage / self.overtime_productivity


@dataclass(frozen=True)
class PlanningProblem:
    """A planning horizon of consecutive days, its service units and the shared budget.

    ``discretionary_budget`` (USD) bounds the cash spent on overtime and surge over the
    horizon by all units together; ``None`` means no limit. The problem is only a container
    of validated inputs: it makes no claim that any number in it is realistic.
    """

    start: date
    units: tuple[ServiceUnit, ...]
    discretionary_budget: float | None = None

    def __post_init__(self) -> None:
        if not self.units:
            raise PlanningError("a planning problem needs at least one service unit")
        ids = [u.unit_id for u in self.units]
        if len(set(ids)) != len(ids):
            raise PlanningError(f"unit ids must be unique: {ids}")
        horizons = {u.horizon for u in self.units}
        if len(horizons) != 1:
            raise PlanningError(f"every unit needs the same horizon, got {sorted(horizons)}")
        horizon = horizons.pop()
        if not 1 <= horizon <= MAX_HORIZON_DAYS:
            raise PlanningError(f"the horizon must be 1..{MAX_HORIZON_DAYS} days, got {horizon}")
        if self.discretionary_budget is not None:
            _finite_nonnegative("discretionary_budget", [self.discretionary_budget])

    @property
    def horizon(self) -> int:
        return self.units[0].horizon

    @property
    def dates(self) -> tuple[date, ...]:
        return tuple(self.start + timedelta(days=offset) for offset in range(self.horizon))

    @property
    def regular_cost(self) -> float:
        return math.fsum(unit.regular_cost for unit in self.units)

    def unit(self, unit_id: str) -> ServiceUnit:
        for candidate in self.units:
            if candidate.unit_id == unit_id:
                return candidate
        raise PlanningError(f"unknown unit {unit_id!r}")


@dataclass(frozen=True)
class UnitPlan:
    """Daily decisions of one unit: requests served, overtime hours, surge requests, backlog."""

    unit_id: str
    served: tuple[float, ...]
    overtime_hours: tuple[float, ...]
    surge: tuple[float, ...]
    backlog: tuple[float, ...]

    def __post_init__(self) -> None:
        lengths = {len(self.served), len(self.overtime_hours), len(self.surge), len(self.backlog)}
        if len(lengths) != 1:
            raise PlanningError(f"plan of {self.unit_id}: sequences differ in length")
        for name in ("served", "overtime_hours", "surge", "backlog"):
            for value in getattr(self, name):
                if not isinstance(value, int | float) or not math.isfinite(value):
                    raise PlanningError(f"plan of {self.unit_id}: {name} must be finite numbers")


@dataclass(frozen=True)
class Plan:
    """A complete set of decisions for every unit of a problem (in the problem's unit order)."""

    units: tuple[UnitPlan, ...]

    def unit(self, unit_id: str) -> UnitPlan:
        for candidate in self.units:
            if candidate.unit_id == unit_id:
                return candidate
        raise PlanningError(f"plan has no unit {unit_id!r}")


def backlog_path(initial: float, demand: Sequence[float], served: Sequence[float]) -> list[float]:
    """End-of-day backlog from the balance ``b[t] = b[t-1] + d[t] - x[t]`` (no clipping)."""
    path: list[float] = []
    level = initial
    for arrivals, done in zip(demand, served, strict=True):
        level = level + arrivals - done
        path.append(level)
    return path


@dataclass(frozen=True)
class PlanEvaluation:
    """Cash costs, notional penalties and backlog statistics of one plan (all units summed).

    Units: costs and penalties in USD (penalties are notional weights, not cash); backlog in
    requests; ``backlog_request_days`` is the sum of end-of-day backlogs, i.e. the number of
    request-days spent waiting overnight; hours in staff hours. ``discretionary_cost`` is
    the cash counted against the budget (overtime and surge); ``operating_cost`` adds the
    committed regular staff cost. ``penalised_score`` = discretionary cash + backlog
    penalty + terminal penalty (USD-equivalent, mixes cash with notional weights).
    ``idle_paid_capacity`` counts overtime and surge requests paid for but not used.
    """

    regular_cost: float
    overtime_cost: float
    surge_cost: float
    discretionary_cost: float
    operating_cost: float
    backlog_penalty: float
    terminal_penalty: float
    penalised_score: float
    total_demand: float
    initial_backlog: float
    total_served: float
    backlog_request_days: float
    terminal_backlog: float
    peak_backlog: float
    days_with_backlog: int
    overtime_hours: float
    surge_requests: float
    idle_paid_capacity: float
    discretionary_budget: float | None
    budget_remaining: float | None

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def evaluate_plan(problem: PlanningProblem, plan: Plan) -> PlanEvaluation:
    """Costs and statistics of ``plan`` as recorded (its backlog is not recomputed here)."""
    _check_shapes(problem, plan)
    overtime_cost: list[float] = []
    surge_cost: list[float] = []
    penalty: list[float] = []
    terminal: list[float] = []
    backlogs: list[float] = []
    idle: list[float] = []
    by_day = [0.0] * problem.horizon
    for unit, decisions in zip(problem.units, plan.units, strict=True):
        overtime_cost.extend(unit.overtime_wage * h for h in decisions.overtime_hours)
        surge_cost.extend(unit.surge_price * s for s in decisions.surge)
        penalty.extend(unit.backlog_penalty * b for b in decisions.backlog)
        terminal.append(unit.terminal_penalty * decisions.backlog[-1])
        backlogs.extend(decisions.backlog)
        for day, level in enumerate(decisions.backlog):
            by_day[day] += level
            extra = (
                unit.overtime_productivity * decisions.overtime_hours[day] + decisions.surge[day]
            )
            used = max(0.0, decisions.served[day] - unit.regular_capacity(day))
            idle.append(max(0.0, extra - used))
    regular = problem.regular_cost
    discretionary = math.fsum([*overtime_cost, *surge_cost])
    operating = math.fsum([regular, *overtime_cost, *surge_cost])
    backlog_penalty = math.fsum(penalty)
    terminal_penalty = math.fsum(terminal)
    budget = problem.discretionary_budget
    return PlanEvaluation(
        regular_cost=regular,
        overtime_cost=math.fsum(overtime_cost),
        surge_cost=math.fsum(surge_cost),
        discretionary_cost=discretionary,
        operating_cost=operating,
        backlog_penalty=backlog_penalty,
        terminal_penalty=terminal_penalty,
        penalised_score=math.fsum([discretionary, backlog_penalty, terminal_penalty]),
        total_demand=math.fsum(d for u in problem.units for d in u.demand),
        initial_backlog=math.fsum(u.initial_backlog for u in problem.units),
        total_served=math.fsum(x for p in plan.units for x in p.served),
        backlog_request_days=math.fsum(backlogs),
        terminal_backlog=math.fsum(p.backlog[-1] for p in plan.units),
        peak_backlog=max(by_day),
        days_with_backlog=sum(1 for level in by_day if level > tolerance(level)),
        overtime_hours=math.fsum(h for p in plan.units for h in p.overtime_hours),
        surge_requests=math.fsum(s for p in plan.units for s in p.surge),
        idle_paid_capacity=math.fsum(idle),
        discretionary_budget=budget,
        budget_remaining=None if budget is None else budget - discretionary,
    )


@dataclass(frozen=True)
class Violation:
    """One constraint not satisfied by a plan, beyond the feasibility tolerance."""

    constraint: str
    unit_id: str | None
    day: int | None
    amount: float
    units: str
    allowed: float

    def describe(self) -> str:
        where = "" if self.unit_id is None else f" {self.unit_id}"
        when = "" if self.day is None else f" day {self.day + 1}"
        return f"{self.constraint}{where}{when}: exceeded by {self.amount:.6g} {self.units}"


@dataclass(frozen=True)
class PlanCheck:
    """Result of :func:`check_plan`. ``hard`` constraints decide strict feasibility."""

    violations: tuple[Violation, ...] = field(default=())
    max_balance_residual: float = 0.0

    @property
    def feasible(self) -> bool:
        return not self.violations

    def constraints_violated(self) -> list[str]:
        return sorted({v.constraint for v in self.violations})


#: Constraints whose violation makes a plan infeasible, in reporting order.
CONSTRAINTS: Final = (
    "nonnegative_served",
    "nonnegative_overtime",
    "nonnegative_surge",
    "nonnegative_backlog",
    "balance",
    "available_work",
    "capacity",
    "overtime_daily_limit",
    "surge_daily_limit",
    "overtime_total_limit",
    "surge_total_limit",
    "terminal_backlog",
    "budget",
)


def check_plan(
    problem: PlanningProblem,
    plan: Plan,
    *,
    ignore: Sequence[str] = (),
) -> PlanCheck:
    """Check every constraint of the model for ``plan``, without the LP that produced it.

    ``ignore`` names constraints deliberately relaxed (a diagnostic plan); they are not
    checked, so the caller must report the plan as relaxed, never as feasible.
    """
    _check_shapes(problem, plan)
    unknown = sorted(set(ignore) - set(CONSTRAINTS))
    if unknown:
        raise PlanningError(f"unknown constraint(s) {unknown}")
    violations: list[Violation] = []
    max_residual = 0.0

    def require(
        constraint: str, unit_id: str | None, day: int | None, excess: float, unit: str, *mag: float
    ) -> None:
        allowed = tolerance(*mag)
        if constraint not in ignore and excess > allowed:
            violations.append(Violation(constraint, unit_id, day, excess, unit, allowed))

    for unit, decisions in zip(problem.units, plan.units, strict=True):
        uid = unit.unit_id
        previous = unit.initial_backlog
        for day in range(problem.horizon):
            demand = unit.demand[day]
            served = decisions.served[day]
            hours = decisions.overtime_hours[day]
            surge = decisions.surge[day]
            backlog = decisions.backlog[day]
            require("nonnegative_served", uid, day, -served, "requests_per_day", served)
            require("nonnegative_overtime", uid, day, -hours, "staff_hours_per_day", hours)
            require("nonnegative_surge", uid, day, -surge, "requests_per_day", surge)
            require("nonnegative_backlog", uid, day, -backlog, "requests", backlog)
            expected = previous + demand - served
            residual = abs(backlog - expected)
            max_residual = max(max_residual, residual)
            require("balance", uid, day, residual, "requests", previous, demand, served, backlog)
            work = previous + demand
            require("available_work", uid, day, served - work, "requests_per_day", served, work)
            capacity = unit.regular_capacity(day) + unit.overtime_productivity * hours + surge
            require("capacity", uid, day, served - capacity, "requests_per_day", served, capacity)
            limit = unit.overtime_max_hours[day]
            require(
                "overtime_daily_limit", uid, day, hours - limit, "staff_hours_per_day", hours, limit
            )
            limit = unit.surge_max[day]
            require("surge_daily_limit", uid, day, surge - limit, "requests_per_day", surge, limit)
            previous = backlog
        if unit.overtime_total_hours is not None:
            total = math.fsum(decisions.overtime_hours)
            require(
                "overtime_total_limit",
                uid,
                None,
                total - unit.overtime_total_hours,
                "staff_hours",
                total,
                unit.overtime_total_hours,
            )
        if unit.surge_total is not None:
            total = math.fsum(decisions.surge)
            require(
                "surge_total_limit",
                uid,
                None,
                total - unit.surge_total,
                "requests",
                total,
                unit.surge_total,
            )
        if unit.terminal_backlog_max is not None:
            final = decisions.backlog[-1]
            require(
                "terminal_backlog",
                uid,
                None,
                final - unit.terminal_backlog_max,
                "requests",
                final,
                unit.terminal_backlog_max,
            )
    budget = problem.discretionary_budget
    if budget is not None:
        spent = evaluate_plan(problem, plan).discretionary_cost
        require("budget", None, None, spent - budget, "usd", spent, budget)
    return PlanCheck(tuple(violations), max_residual)


def least_terminal_backlog(unit: ServiceUnit) -> float:
    """Smallest end-of-horizon backlog the unit can reach with unlimited money.

    Serving as much as possible every day is optimal for this purpose (a request served
    earlier never reduces later capacity), so the backlog follows
    ``b[t] = max(0, b[t-1] + d[t] - max_capacity[t])``. Horizon totals are not applied, so
    this is a lower bound when an overtime or surge total binds.
    """
    level = unit.initial_backlog
    for day in range(unit.horizon):
        level = max(0.0, level + unit.demand[day] - unit.max_capacity(day))
    return level


def _check_shapes(problem: PlanningProblem, plan: Plan) -> None:
    expected = [u.unit_id for u in problem.units]
    actual = [p.unit_id for p in plan.units]
    if expected != actual:
        raise PlanningError(f"plan units {actual} do not match problem units {expected}")
    for decisions in plan.units:
        if len(decisions.served) != problem.horizon:
            raise PlanningError(
                f"plan of {decisions.unit_id} has {len(decisions.served)} days, "
                f"the problem {problem.horizon}"
            )


def binding_constraints(problem: PlanningProblem, plan: Plan) -> dict[str, int]:
    """Constraints that are tight in ``plan`` (slack within tolerance), from the plan itself.

    Daily constraints map to the number of days they bind; a daily limit of zero (for
    example surge before it becomes available) is not counted, because nothing can be
    bought anyway. ``terminal_backlog_violated`` marks a plan above its terminal target
    (a relaxed plan). Independent of the LP and of its dual values, so it works for every
    plan, relaxed and policy plans included.
    """
    _check_shapes(problem, plan)
    counts: dict[str, int] = {}

    def add(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    for unit, decisions in zip(problem.units, plan.units, strict=True):
        for day in range(problem.horizon):
            hours = decisions.overtime_hours[day]
            surge = decisions.surge[day]
            capacity = unit.regular_capacity(day) + unit.overtime_productivity * hours + surge
            if capacity - decisions.served[day] <= tolerance(capacity):
                add("capacity")
            limit = unit.overtime_max_hours[day]
            if limit > 0 and limit - hours <= tolerance(limit):
                add("overtime_daily_limit")
            limit = unit.surge_max[day]
            if limit > 0 and limit - surge <= tolerance(limit):
                add("surge_daily_limit")
        total = unit.overtime_total_hours
        if total is not None and total - math.fsum(decisions.overtime_hours) <= tolerance(total):
            add("overtime_total_limit")
        total = unit.surge_total
        if total is not None and total - math.fsum(decisions.surge) <= tolerance(total):
            add("surge_total_limit")
        target = unit.terminal_backlog_max
        if target is not None:
            final = decisions.backlog[-1]
            if final > target + tolerance(final, target):
                add("terminal_backlog_violated")
            elif target - final <= tolerance(final, target):
                add("terminal_backlog")
    budget = problem.discretionary_budget
    if budget is not None:
        spent = evaluate_plan(problem, plan).discretionary_cost
        if budget - spent <= tolerance(spent, budget):
            add("budget")
    return counts


def surge_delay_days(unit: ServiceUnit) -> int:
    """Leading days on which surge is unavailable although it is available later (0 if none)."""
    first = next((day for day, limit in enumerate(unit.surge_max) if limit > 0), None)
    return 0 if first is None else first
