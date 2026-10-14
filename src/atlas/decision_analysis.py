"""Analyses of the M4 planning model: solves, diagnostics, tradeoffs and sensitivity (M4).

Everything here is built on :mod:`atlas.optimizer` (the LP) and :mod:`atlas.planning`
(the independent evaluation). Every programme solved through :func:`solve_problem` is
checked three ways before its result is used: the optimality certificate from primal and
dual values, the plan's constraints in plain loops, and the agreement between the solver's
objective and the objective recomputed from the plan. A failed check is recorded as a
*problem* and the runner reports it as an error; it is never silently accepted.

* **Strict plan**: the planning model as configured (no slack variables).
* **Infeasibility diagnosis** (only when the strict model is infeasible; every number is
  a DIAGNOSTIC, never a plan for the stated constraints):
  the least terminal-target violation reachable with unlimited discretionary cash and
  within the budget (soft targets, ``min sum v``), the least discretionary spend that would
  reach the HYPOTHETICAL target, and the analytic lower bound on the terminal backlog. The
  RELAXED plan is the lexicographic optimum: least violation within the budget first, then
  the planning objective among those plans. It is labelled ``relaxed_terminal_target``.
* **Optimal face**: a complete uniqueness test (active-set distance, see
  :func:`optimal_face`) and the ranges of cash, backlog and daily extra capacity over all
  optimal plans.
* **Shadow prices**: every dual bracketed by one-sided finite differences of the optimal
  value (convex in every right-hand side, so ``backward <= dual <= forward`` must hold).
* **Policies**: the fixed-capacity and backlog-trigger rules, plus the LP at the same
  discretionary cash (least request-days) and at the same backlog (least cash).
* **Frontier**: least request-days for each cap on discretionary cash (epsilon
  constraint), with the least cash achieving it (lexicographic).
* **Sweeps**: one parameter at a time, with the monotonicity properties that are proved
  for the model checked on the results.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final

import numpy as np

from atlas.optimizer import (
    ROW_UNITS,
    Certificate,
    Cleaning,
    DualValue,
    ExtraRow,
    FloatArray,
    LinearProgram,
    LPResult,
    Objective,
    Row,
    SolverSettings,
    SolveStatus,
    Terminal,
    build_lp,
    certify,
    dual_values,
    exact_dot,
    objective_vector,
    solve_lp,
    to_plan,
)
from atlas.planning import (
    Plan,
    PlanCheck,
    PlanEvaluation,
    PlanningProblem,
    ServiceUnit,
    check_plan,
    evaluate_plan,
    least_terminal_backlog,
    tolerance,
)
from atlas.policies import (
    BACKLOG_TRIGGER,
    FIXED_CAPACITY,
    backlog_trigger_plan,
    fixed_capacity_plan,
)

#: Slack allowed above a first-stage optimum in a lexicographic second stage.
LEX_EPS_ABS: Final = 1e-6
LEX_EPS_REL: Final = 1e-9
#: Slack above the optimal score when exploring the optimal face (relative to sum |c x|).
FACE_EPS_REL: Final = 1e-9
#: Active-set distance above which another optimal plan exists (requests or staff hours).
UNIQUENESS_THRESHOLD: Final = 1e-3

RELAXED_TERMINAL_TARGET: Final = "relaxed_terminal_target"
STRICT: Final = "strict"


def lex_cap(value: float) -> float:
    return value + LEX_EPS_ABS + LEX_EPS_REL * abs(value)


@dataclass(frozen=True)
class Solved:
    """An optimal solution with its plan and the three independent checks."""

    lp: LinearProgram
    result: LPResult
    plan: Plan
    evaluation: PlanEvaluation
    check: PlanCheck
    certificate: Certificate
    cleaning: Cleaning
    objective_value: float
    recomputed_value: float | None

    @property
    def x(self) -> FloatArray:
        if self.result.x is None:  # pragma: no cover - Solved is built from optimal results
            raise AssertionError("optimal result without a solution")
        return self.result.x


@dataclass(frozen=True)
class Attempt:
    """The outcome of one programme: ``solved`` is set exactly when it is optimal."""

    objective: Objective
    status: SolveStatus
    message: str
    iterations: int
    solved: Solved | None = None
    problems: tuple[str, ...] = ()

    @property
    def optimal(self) -> bool:
        return self.solved is not None

    def require(self) -> Solved:
        if self.solved is None:
            raise AssertionError(f"programme is {self.status}, not optimal")
        return self.solved

    @property
    def value(self) -> float | None:
        return None if self.solved is None else self.solved.objective_value


def _recomputed(
    problem: PlanningProblem, plan: Plan, evaluation: PlanEvaluation, objective: Objective
) -> float | None:
    if objective is Objective.WEIGHTED:
        return evaluation.penalised_score
    if objective is Objective.DISCRETIONARY_CASH:
        return evaluation.discretionary_cost
    if objective is Objective.BACKLOG_DAYS:
        return evaluation.backlog_request_days
    if objective is Objective.TERMINAL_BACKLOG:
        return evaluation.terminal_backlog
    if objective is Objective.TERMINAL_VIOLATION:
        return terminal_violation(problem, plan)
    return None


def terminal_violation(problem: PlanningProblem, plan: Plan) -> float:
    """Requests above the terminal targets, summed over the units that have one."""
    return math.fsum(
        max(0.0, decisions.backlog[-1] - unit.terminal_backlog_max)
        for unit, decisions in zip(problem.units, plan.units, strict=True)
        if unit.terminal_backlog_max is not None
    )


def solve_problem(
    problem: PlanningProblem,
    settings: SolverSettings,
    objective: Objective = Objective.WEIGHTED,
    *,
    terminal: Terminal = Terminal.HARD,
    budget_row: bool = True,
    extra_rows: Sequence[ExtraRow] = (),
    coefficients: FloatArray | None = None,
) -> Attempt:
    """Build, solve and check one programme (see the module docstring)."""
    lp = build_lp(
        problem,
        objective,
        terminal=terminal,
        budget_row=budget_row,
        extra_rows=extra_rows,
        coefficients=coefficients,
    )
    result = solve_lp(lp, settings)
    if result.status is not SolveStatus.OPTIMAL or result.x is None:
        return Attempt(objective, result.status, result.message, result.iterations)
    plan, cleaning = to_plan(lp, result.x)
    ignore = ([] if terminal is Terminal.HARD else ["terminal_backlog"]) + (
        [] if budget_row else ["budget"]
    )
    check = check_plan(problem, plan, ignore=ignore)
    evaluation = evaluate_plan(problem, plan)
    certificate = certify(lp, result)
    value = lp.value(result.x)
    recomputed = _recomputed(problem, plan, evaluation, objective)
    problems = []
    if not certificate.holds:
        problems.append(f"optimality certificate failed: {certificate.to_dict()}")
    if not check.feasible:
        problems.append(
            "independent check failed: " + "; ".join(v.describe() for v in check.violations[:5])
        )
    if recomputed is not None and abs(recomputed - value) > tolerance(value, recomputed) * 10:
        problems.append(
            f"objective {value!r} differs from the value recomputed from the plan {recomputed!r}"
        )
    solved = Solved(lp, result, plan, evaluation, check, certificate, cleaning, value, recomputed)
    return Attempt(
        objective, result.status, result.message, result.iterations, solved, tuple(problems)
    )


def cap_row(lp_like: LinearProgram, objective: Objective, cap: float, name: str) -> ExtraRow:
    """``objective(v) <= cap`` on the layout of ``lp_like`` (for lexicographic stages)."""
    c, constant = objective_vector(lp_like.problem, lp_like.layout, objective)
    return ExtraRow(Row(name, None, None), c, cap - constant)


def solve_lexicographic(
    problem: PlanningProblem,
    settings: SolverSettings,
    first: Objective,
    second: Objective = Objective.WEIGHTED,
    *,
    terminal: Terminal = Terminal.HARD,
    budget_row: bool = True,
    extra_rows: Sequence[ExtraRow] = (),
) -> tuple[Attempt, Attempt]:
    """Minimise ``first``; then ``second`` among the plans within ``lex_cap`` of the optimum.

    The second stage makes the reported plan well defined: the first objective alone
    leaves cash, timing and backlog free on a large optimal face.
    """
    stage1 = solve_problem(
        problem, settings, first, terminal=terminal, budget_row=budget_row, extra_rows=extra_rows
    )
    if stage1.solved is None:
        return stage1, stage1
    row = cap_row(stage1.solved.lp, first, lex_cap(stage1.solved.objective_value), f"cap_{first}")
    stage2 = solve_problem(
        problem,
        settings,
        second,
        terminal=terminal,
        budget_row=budget_row,
        extra_rows=[*extra_rows, row],
    )
    return stage1, stage2


# --- Infeasibility ------------------------------------------------------------------


@dataclass(frozen=True)
class Diagnosis:
    """Why the strict model is infeasible, and the relaxed plan (DIAGNOSTIC values only).

    ``least_violation_unlimited`` and ``least_violation_within_budget`` are requests above
    the HYPOTHETICAL terminal targets (summed over units); ``min_spend_for_target`` is the
    least discretionary cash (USD) that would reach every target, ``None`` when no amount
    reaches them. ``terminal_lower_bound`` is the analytic bound of
    :func:`atlas.planning.least_terminal_backlog` (exact when no horizon totals are set).
    """

    least_violation_unlimited: float | None
    least_violation_within_budget: float | None
    min_spend_for_target: float | None
    budget_shortfall: float | None
    terminal_lower_bound: Mapping[str, float]
    lower_bound_exact: bool
    reasons: tuple[str, ...]
    relaxed: Attempt
    attempts: tuple[Attempt, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": "DIAGNOSTIC: not a plan for the stated constraints",
            "least_terminal_violation_unlimited_discretionary_cash_requests": (
                self.least_violation_unlimited
            ),
            "least_terminal_violation_within_budget_requests": self.least_violation_within_budget,
            "diagnostic_min_discretionary_spend_to_reach_hypothetical_target_usd": (
                self.min_spend_for_target
            ),
            "diagnostic_budget_shortfall_usd": self.budget_shortfall,
            "analytic_terminal_backlog_lower_bound_requests": dict(self.terminal_lower_bound),
            "analytic_bound_exact": self.lower_bound_exact,
            "reasons": list(self.reasons),
            "relaxed_plan": RELAXED_TERMINAL_TARGET if self.relaxed.optimal else None,
        }


def diagnose(problem: PlanningProblem, settings: SolverSettings) -> Diagnosis:
    """Explain an infeasible strict model (it can only be the terminal targets)."""
    bounds = {u.unit_id: least_terminal_backlog(u) for u in problem.units}
    exact = all(u.overtime_total_hours is None and u.surge_total is None for u in problem.units)
    unlimited = solve_problem(
        problem, settings, Objective.TERMINAL_VIOLATION, terminal=Terminal.SOFT, budget_row=False
    )
    within, relaxed = solve_lexicographic(
        problem, settings, Objective.TERMINAL_VIOLATION, terminal=Terminal.SOFT
    )
    reasons: list[str] = []
    least_unlimited = unlimited.value
    least_within = within.value
    spend: float | None = None
    shortfall: float | None = None
    attempts = [unlimited, within, relaxed]
    reachable = least_unlimited is not None and least_unlimited <= tolerance(least_unlimited)
    if least_unlimited is None:  # pragma: no cover - soft targets are always feasible
        reasons.append("the least terminal-target violation could not be computed")
    elif not reachable:
        reasons.append(
            "the HYPOTHETICAL terminal-backlog target cannot be reached with any amount of "
            f"discretionary cash: the least violation is {least_unlimited:,.1f} requests "
            "(daily limits, totals and delayed surge bind)"
        )
    else:
        cash = solve_problem(
            problem,
            settings,
            Objective.DISCRETIONARY_CASH,
            terminal=Terminal.HARD,
            budget_row=False,
        )
        attempts.append(cash)
        spend = cash.value
        budget = problem.discretionary_budget
        if spend is not None and budget is not None:
            shortfall = spend - budget
            reasons.append(
                "the HYPOTHETICAL discretionary budget is too small for the HYPOTHETICAL "
                f"terminal-backlog target: reaching it needs at least {spend:,.0f} USD of "
                f"overtime and surge, {shortfall:,.0f} USD more than the budget"
            )
    for unit_id, bound in bounds.items():
        unit = problem.unit(unit_id)
        target = unit.terminal_backlog_max
        if exact and target is not None and bound > target + tolerance(bound, target):
            reasons.append(
                f"{unit_id}: even serving at maximum capacity every day leaves "
                f"{bound:,.1f} requests at the end of the horizon (target {target:,.0f})"
            )
    return Diagnosis(
        least_violation_unlimited=least_unlimited,
        least_violation_within_budget=least_within,
        min_spend_for_target=spend,
        budget_shortfall=shortfall,
        terminal_lower_bound=bounds,
        lower_bound_exact=exact,
        reasons=tuple(reasons),
        relaxed=relaxed,
        attempts=tuple(attempts),
    )


# --- Optimal face ------------------------------------------------------------------------


@dataclass(frozen=True)
class FaceRange:
    quantity: str
    units: str
    low: float
    high: float

    @property
    def width(self) -> float:
        return self.high - self.low


@dataclass(frozen=True)
class OptimalFace:
    """Is the optimal plan unique, and how much do optimal plans differ?

    ``max_active_distance`` is the largest total distance from the bounds and tight rows
    active at the reported vertex, over all plans whose score is within ``epsilon`` of the
    optimum (rows normalised by their largest coefficient; requests or staff hours). A
    vertex is determined by its active constraints, so the distance is zero exactly when
    the reported plan is the only optimal plan. Plans that are merely near-optimal move at
    most ``epsilon / rate``, so the test is repeated with ``epsilon / 100``: another optimal
    plan exists only if the distance does not shrink with ``epsilon``
    (``distance_small_epsilon``). Ranges are over the same ``epsilon`` face.
    """

    unique: bool
    epsilon: float
    max_active_distance: float
    distance_small_epsilon: float | None
    ranges: tuple[FaceRange, ...]
    daily_extra_capacity: tuple[tuple[str, int, float, float], ...]
    problems: tuple[str, ...] = ()

    def undetermined_days(self) -> int:
        """Days whose extra capacity (overtime and surge requests) differs across optimal
        plans by more than the uniqueness threshold (meaningful only if not unique)."""
        if self.unique:
            return 0
        return sum(
            1 for _, _, low, high in self.daily_extra_capacity if high - low > UNIQUENESS_THRESHOLD
        )


def face_epsilon(solved: Solved) -> float:
    """``1e-9 * max(1, sum |c_j x_j|)``: solver-accuracy slack above the optimal score."""
    return FACE_EPS_REL * max(1.0, math.fsum(np.abs(solved.lp.c * solved.x).tolist()))


def _face_row(solved: Solved, epsilon: float) -> ExtraRow:
    return ExtraRow(
        Row("cap_optimal_score", None, None), solved.lp.c.copy(), solved.objective_value + epsilon
    )


def _quantity_vectors(lp: LinearProgram) -> dict[str, tuple[str, FloatArray]]:
    layout = lp.layout
    n = layout.size
    vectors: dict[str, tuple[str, FloatArray]] = {}
    cash, _ = objective_vector(lp.problem, layout, Objective.DISCRETIONARY_CASH)
    days, _ = objective_vector(lp.problem, layout, Objective.BACKLOG_DAYS)
    final, _ = objective_vector(lp.problem, layout, Objective.TERMINAL_BACKLOG)
    hours = np.zeros(n)
    surge = np.zeros(n)
    for k in range(len(layout.unit_ids)):
        for day in range(layout.horizon):
            hours[layout.index(k, "overtime_hours", day)] = 1.0
            surge[layout.index(k, "surge", day)] = 1.0
    vectors["discretionary_cash"] = ("usd", cash)
    vectors["backlog_request_days"] = ("request_days", days)
    vectors["terminal_backlog"] = ("requests", final)
    vectors["overtime_hours"] = ("staff_hours", hours)
    vectors["surge_requests"] = ("requests", surge)
    return vectors


def _active_distance(lp: LinearProgram, v: FloatArray) -> tuple[FloatArray, float]:
    """Linear form and constant of the distance from the constraints active at ``v``."""
    linear = np.zeros(lp.layout.size)
    constant = 0.0
    for j in range(lp.layout.size):
        if v[j] - lp.lower[j] <= tolerance(v[j], lp.lower[j]):
            linear[j] += 1.0
            constant -= lp.lower[j]
        if np.isfinite(lp.upper[j]) and lp.upper[j] - v[j] <= tolerance(v[j], lp.upper[j]):
            linear[j] -= 1.0
            constant += lp.upper[j]
    for i, row in enumerate(lp.ub_rows):
        scale = max(1.0, float(np.max(np.abs(lp.a_ub[i]), initial=0.0)))
        magnitude = exact_dot(np.abs(lp.a_ub[i]), np.abs(v)) + abs(float(lp.b_ub[i]))
        slack = float(lp.b_ub[i]) - exact_dot(lp.a_ub[i], v)
        if row.constraint in ROW_UNITS and slack <= tolerance(magnitude):
            linear -= lp.a_ub[i] / scale
            constant += float(lp.b_ub[i]) / scale
    return linear, constant


def optimal_face(problem: PlanningProblem, settings: SolverSettings, strict: Solved) -> OptimalFace:
    """Uniqueness test and ranges over the optimal face of the strict planning model."""
    problems: list[str] = []
    epsilon = face_epsilon(strict)

    def extreme(coefficients: FloatArray, sign: float, eps: float) -> float | None:
        attempt = solve_problem(
            problem,
            settings,
            Objective.CUSTOM,
            extra_rows=[_face_row(strict, eps)],
            coefficients=sign * coefficients,
        )
        if attempt.solved is None:
            problems.append(f"optimal-face programme is {attempt.status}")
            return None
        problems.extend(attempt.problems)
        return exact_dot(coefficients, attempt.solved.x)

    linear, constant = _active_distance(strict.lp, strict.x)
    top = extreme(linear, -1.0, epsilon)
    distance = math.inf if top is None else max(0.0, top + constant)
    small: float | None = None
    unique = distance <= UNIQUENESS_THRESHOLD
    if not unique and top is not None:
        top_small = extreme(linear, -1.0, epsilon / 100)
        small = None if top_small is None else max(0.0, top_small + constant)
        unique = small is not None and (small <= UNIQUENESS_THRESHOLD or small <= 0.05 * distance)
    ranges = []
    for name, (units, vector) in _quantity_vectors(strict.lp).items():
        low = extreme(vector, 1.0, epsilon)
        high = extreme(vector, -1.0, epsilon)
        if low is not None and high is not None:
            ranges.append(FaceRange(name, units, low, high))
    daily: list[tuple[str, int, float, float]] = []
    if not unique:
        for k, unit in enumerate(problem.units):
            for day in range(problem.horizon):
                vector = np.zeros(strict.lp.layout.size)
                vector[strict.lp.layout.index(k, "overtime_hours", day)] = (
                    unit.overtime_productivity
                )
                vector[strict.lp.layout.index(k, "surge", day)] = 1.0
                low = extreme(vector, 1.0, epsilon)
                high = extreme(vector, -1.0, epsilon)
                if low is not None and high is not None:
                    daily.append((unit.unit_id, day, low, high))
    return OptimalFace(
        unique=bool(unique),
        epsilon=epsilon,
        max_active_distance=distance,
        distance_small_epsilon=small,
        ranges=tuple(ranges),
        daily_extra_capacity=tuple(daily),
        problems=tuple(problems),
    )


# --- Shadow prices -----------------------------------------------------------------------

#: Step (natural units: requests, staff hours, USD) of the local finite-difference test.
FD_SMALL_STEP: Final = 1e-3


@dataclass(frozen=True)
class FiniteDifference:
    """One-sided derivatives of the optimal score around one right-hand side.

    The optimal value is convex in every right-hand side and bound, so for any step
    ``backward secant <= left derivative <= dual <= right derivative <= forward secant``.
    ``backward`` / ``forward`` use the local step :data:`FD_SMALL_STEP`; equal slopes
    there mean the dual is locally unique, unequal slopes that the point sits at (or within
    one small step of) a breakpoint, where the solver's dual is one subgradient among
    several. ``practical_*`` use the configured step (1 request, 1 staff hour, 100 USD):
    the average effect of that change, not a derivative. ``backward`` is ``None`` at a
    domain boundary (a right-hand side cannot fall below 0); an infeasible side gives
    ``-inf`` (backward) or ``+inf`` (forward). The forward practical change is split
    into its discretionary-cash and notional-penalty parts (from the re-solved plan).
    """

    dual: DualValue
    step: float
    backward: float | None
    forward: float
    practical_step: float
    practical_backward: float | None
    practical_forward: float
    forward_cash: float | None
    forward_penalty: float | None
    bracket_ok: bool
    convex_ok: bool
    sign_ok: bool

    @property
    def locally_unique(self) -> bool:
        if self.backward is None or math.isinf(self.backward) or math.isinf(self.forward):
            return False
        return abs(self.forward - self.backward) <= _slope_tol(self.forward, self.backward)

    @property
    def status(self) -> str:
        if self.backward is None:
            return "domain_boundary_below"
        if math.isinf(self.backward) or math.isinf(self.forward):
            return "infeasible_side"
        return "locally_unique" if self.locally_unique else "breakpoint"


def _slope_tol(*slopes: float) -> float:
    finite = [abs(v) for v in slopes if math.isfinite(v)]
    return 1e-5 + 1e-6 * max([1.0, *finite])


def _step_for(units: str, steps: Mapping[str, float]) -> float:
    if units.startswith("staff_hours"):
        return steps["staff_hours"]
    if units == "usd":
        return steps["usd"]
    return steps["requests"]


def _perturbed(lp: LinearProgram, kind: str, index: int, delta: float) -> LinearProgram:
    if kind == "eq":
        b_eq = lp.b_eq.copy()
        b_eq[index] += delta
        return replace(lp, b_eq=b_eq)
    if kind == "ub":
        b_ub = lp.b_ub.copy()
        b_ub[index] += delta
        return replace(lp, b_ub=b_ub)
    upper = lp.upper.copy()
    upper[index] += delta
    return replace(lp, upper=upper)


def _locate(lp: LinearProgram, dual: DualValue) -> tuple[str, int]:
    for i, row in enumerate(lp.eq_rows):
        if (row.constraint, row.unit_id, row.day) == (dual.constraint, dual.unit_id, dual.day):
            return "eq", i
    for i, row in enumerate(lp.ub_rows):
        if (row.constraint, row.unit_id, row.day) == (dual.constraint, dual.unit_id, dual.day):
            return "ub", i
    block = "overtime_hours" if dual.constraint == "overtime_daily_limit" else "surge"
    k = lp.layout.unit_ids.index(dual.unit_id or "")
    return "upper", lp.layout.index(k, block, dual.day or 0)


def _value_at(lp: LinearProgram, settings: SolverSettings) -> tuple[float, FloatArray | None]:
    result = solve_lp(lp, settings)
    if result.status is SolveStatus.INFEASIBLE or result.x is None:
        return math.inf, None
    return lp.value(result.x), result.x


def _secants(
    lp: LinearProgram,
    settings: SolverSettings,
    kind: str,
    index: int,
    rhs: float,
    base: float,
    step: float,
) -> tuple[float | None, float, FloatArray | None]:
    up, forward_x = _value_at(_perturbed(lp, kind, index, step), settings)
    forward = (up - base) / step
    down_step = min(step, rhs)
    if down_step <= 0:
        return None, forward, forward_x
    down, _ = _value_at(_perturbed(lp, kind, index, -down_step), settings)
    backward = -math.inf if math.isinf(down) else (base - down) / down_step
    return backward, forward, forward_x


def finite_differences(
    strict: Solved, settings: SolverSettings, steps: Mapping[str, float]
) -> tuple[FiniteDifference, ...]:
    """Bracket every shadow price of the strict optimum (see :class:`FiniteDifference`)."""
    lp = strict.lp
    base = strict.objective_value
    cash_vector, _ = objective_vector(lp.problem, lp.layout, Objective.DISCRETIONARY_CASH)
    results = []
    for dual in dual_values(lp, strict.result):
        kind, index = _locate(lp, dual)
        backward, forward, _ = _secants(lp, settings, kind, index, dual.rhs, base, FD_SMALL_STEP)
        practical = _step_for(dual.rhs_units, steps)
        p_backward, p_forward, forward_x = _secants(
            lp, settings, kind, index, dual.rhs, base, practical
        )
        tol = _slope_tol(dual.marginal, forward, *([] if backward is None else [backward]))
        bracket = dual.marginal <= forward + tol and (
            backward is None or dual.marginal >= backward - tol
        )
        convex = all(
            low is None or low <= high + _slope_tol(high, low)
            for low, high in ((backward, forward), (p_backward, p_forward))
        ) and (
            backward is None
            or p_backward is None
            or (p_backward <= backward + tol and forward <= p_forward + tol)
        )
        # The optimal score never falls when demand rises (proved for d >= 0), so the dual
        # of a balance row with a positive right-hand side is a non-negative subgradient.
        sign = not (dual.constraint == "balance" and dual.rhs > 0 and dual.marginal < -tol)
        cash = penalty = None
        if forward_x is not None:
            cash = (
                exact_dot(cash_vector, forward_x) - exact_dot(cash_vector, strict.x)
            ) / practical
            penalty = p_forward - cash
        results.append(
            FiniteDifference(
                dual=dual,
                step=FD_SMALL_STEP,
                backward=backward,
                forward=forward,
                practical_step=practical,
                practical_backward=p_backward,
                practical_forward=p_forward,
                forward_cash=cash,
                forward_penalty=penalty,
                bracket_ok=bool(bracket),
                convex_ok=bool(convex),
                sign_ok=bool(sign),
            )
        )
    return tuple(results)


# --- Policies ------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyOutcome:
    """A policy plan, its evaluation, and the LP at the same cash and at the same backlog."""

    policy: str
    plan: Plan
    evaluation: PlanEvaluation
    check: PlanCheck
    same_cash: Attempt
    same_backlog: Attempt
    problems: tuple[str, ...] = ()


def _policy_plan(problem: PlanningProblem, policy: str, triggers: Mapping[str, float]) -> Plan:
    if policy == FIXED_CAPACITY:
        return fixed_capacity_plan(problem)
    if policy == BACKLOG_TRIGGER:
        return backlog_trigger_plan(problem, triggers)
    raise AssertionError(f"unknown policy {policy!r}")  # pragma: no cover


def evaluate_policy(
    problem: PlanningProblem,
    settings: SolverSettings,
    policy: str,
    triggers: Mapping[str, float],
) -> PolicyOutcome:
    """Score a policy and compute the LP at its discretionary cash and at its backlog.

    Both LP comparisons drop the terminal target (the policies do not plan for it) and
    keep every other constraint, so the policy plan itself is feasible for them: the LP
    can only do at least as well (checked).
    """
    plan = _policy_plan(problem, policy, triggers)
    evaluation = evaluate_plan(problem, plan)
    check = check_plan(problem, plan)
    probe = build_lp(problem, terminal=Terminal.NONE)
    cash_cap = cap_row(
        probe,
        Objective.DISCRETIONARY_CASH,
        lex_cap(evaluation.discretionary_cost),
        "cap_policy_cash",
    )
    _, same_cash = solve_lexicographic(
        problem,
        settings,
        Objective.BACKLOG_DAYS,
        terminal=Terminal.NONE,
        extra_rows=[cash_cap],
    )
    backlog_caps = [
        cap_row(
            probe,
            Objective.BACKLOG_DAYS,
            lex_cap(evaluation.backlog_request_days),
            "cap_policy_backlog",
        ),
        cap_row(
            probe,
            Objective.TERMINAL_BACKLOG,
            lex_cap(evaluation.terminal_backlog),
            "cap_policy_terminal",
        ),
    ]
    _, same_backlog = solve_lexicographic(
        problem,
        settings,
        Objective.DISCRETIONARY_CASH,
        terminal=Terminal.NONE,
        extra_rows=backlog_caps,
    )
    problems: list[str] = []
    for attempt in (same_cash, same_backlog):
        problems.extend(attempt.problems)
    other = set(check.constraints_violated()) - {"terminal_backlog"}
    if other:
        problems.append(f"policy {policy} violates {sorted(other)}")
    if same_cash.solved is None or same_backlog.solved is None:
        problems.append(f"policy {policy}: the same-cash or same-backlog LP is not optimal")
    else:
        if same_cash.solved.evaluation.backlog_request_days > lex_cap(
            evaluation.backlog_request_days
        ) + tolerance(evaluation.backlog_request_days):
            problems.append(f"policy {policy}: the LP at the same cash has more backlog")
        if same_backlog.solved.evaluation.discretionary_cost > lex_cap(
            evaluation.discretionary_cost
        ) + tolerance(evaluation.discretionary_cost):
            problems.append(f"policy {policy}: the LP at the same backlog costs more")
    return PolicyOutcome(policy, plan, evaluation, check, same_cash, same_backlog, tuple(problems))


def pareto_relation(a: PlanEvaluation, b: PlanEvaluation) -> str:
    """How plan ``a`` compares with plan ``b`` on discretionary cash, request-days and
    terminal backlog (lower is better on each): dominates, dominated, equal or tradeoff."""
    pairs = [
        (a.discretionary_cost, b.discretionary_cost),
        (a.backlog_request_days, b.backlog_request_days),
        (a.terminal_backlog, b.terminal_backlog),
    ]
    better = worse = False
    for mine, theirs in pairs:
        allowed = tolerance(mine, theirs) * 10
        if mine < theirs - allowed:
            better = True
        elif mine > theirs + allowed:
            worse = True
    if better and not worse:
        return "dominates"
    if worse and not better:
        return "dominated"
    return "tradeoff" if better else "equal"


# --- Frontier --------------------------------------------------------------------------


@dataclass(frozen=True)
class FrontierPoint:
    terminal_target: str
    cash_cap: float
    marker: str
    status: SolveStatus
    penalty: float | None
    discretionary_cash: float | None
    request_days: float | None
    terminal_backlog: float | None
    overtime_hours: float | None
    surge_requests: float | None


@dataclass(frozen=True)
class Frontier:
    """Least notional backlog penalty for each cap on discretionary cash (budget dropped)."""

    points: tuple[FrontierPoint, ...]
    cash_for_least_penalty: Mapping[str, float | None]
    monotone_ok: bool
    convex_ok: bool
    on_frontier_check: str
    problems: tuple[str, ...] = ()


def frontier(
    problem: PlanningProblem,
    settings: SolverSettings,
    points: int,
    strict: Solved | None,
) -> Frontier | None:
    """Epsilon-constraint tradeoff between discretionary cash and the notional penalty.

    The second axis is the penalty actually in the planning objective,
    ``P = sum w * b[t] + w_T * b[T]`` (USD-equivalent, notional), so the strict optimum
    must lie on the curve: at its own cash, no plan has a lower penalty (otherwise the
    strict optimum would not minimise cash + P). Request-days and terminal backlog are
    reported for each point. Two curves: with the HYPOTHETICAL terminal targets kept, and
    without them. Each keeps daily limits and totals and drops the budget row; the cap runs
    from 0 to the least cash that achieves the least possible penalty. Each point is
    lexicographic (least penalty, then least cash). The curve must be non-increasing and
    convex in the cap (the cap is a right-hand side). ``None`` when every penalty weight is
    zero (the penalty is then identically zero).
    """
    if all(u.backlog_penalty == 0 and u.terminal_penalty == 0 for u in problem.units):
        return None
    problems: list[str] = []
    result: list[FrontierPoint] = []
    least_cash: dict[str, float | None] = {}
    monotone = convex = True
    for variant, terminal in (("kept", Terminal.HARD), ("dropped", Terminal.NONE)):
        stage1, stage2 = solve_lexicographic(
            problem,
            settings,
            Objective.PENALTY,
            Objective.DISCRETIONARY_CASH,
            terminal=terminal,
            budget_row=False,
        )
        problems.extend(stage1.problems + stage2.problems)
        if stage2.solved is None:
            least_cash[variant] = None
            continue
        top = stage2.solved.evaluation.discretionary_cost
        least_cash[variant] = top
        caps = [(top * i / (points - 1), "grid") for i in range(points)]
        if problem.discretionary_budget is not None and problem.discretionary_budget < top:
            caps.append((problem.discretionary_budget, "configured_budget"))
        if strict is not None and variant == "kept":
            caps.append((strict.evaluation.discretionary_cost, "strict_plan_cash"))
        probe = build_lp(problem, terminal=terminal, budget_row=False)
        curve: list[tuple[float, float]] = []
        for cap, marker in sorted(caps):
            row = cap_row(probe, Objective.DISCRETIONARY_CASH, cap, "cap_frontier_cash")
            first, second = solve_lexicographic(
                problem,
                settings,
                Objective.PENALTY,
                Objective.DISCRETIONARY_CASH,
                terminal=terminal,
                budget_row=False,
                extra_rows=[row],
            )
            problems.extend(first.problems + second.problems)
            if first.solved is None or second.solved is None:
                result.append(
                    FrontierPoint(
                        variant, cap, marker, first.status, None, None, None, None, None, None
                    )
                )
                continue
            evaluation = second.solved.evaluation
            penalty = first.solved.objective_value
            curve.append((cap, penalty))
            result.append(
                FrontierPoint(
                    variant,
                    cap,
                    marker,
                    SolveStatus.OPTIMAL,
                    penalty,
                    evaluation.discretionary_cost,
                    evaluation.backlog_request_days,
                    evaluation.terminal_backlog,
                    evaluation.overtime_hours,
                    evaluation.surge_requests,
                )
            )
        for (_c1, v1), (_c2, v2) in itertools.pairwise(curve):
            if v2 > v1 + 10 * tolerance(v1, v2):
                monotone = False
        for (c1, v1), (c2, v2), (c3, v3) in zip(curve, curve[1:], curve[2:], strict=False):
            if c2 - c1 > 1e-6 and c3 - c2 > 1e-6:
                left = (v2 - v1) / (c2 - c1)
                right = (v3 - v2) / (c3 - c2)
                noise = 20 * tolerance(v1, v2, v3) / min(c2 - c1, c3 - c2)
                if left > right + 1e-6 * max(1.0, abs(left), abs(right)) + noise:
                    convex = False
    check = "not_applicable"
    if strict is not None:
        at = [
            p.penalty
            for p in result
            if p.terminal_target == "kept"
            and p.marker == "strict_plan_cash"
            and p.penalty is not None
        ]
        own = strict.evaluation.backlog_penalty + strict.evaluation.terminal_penalty
        ok = bool(at) and abs(at[0] - own) <= 10 * tolerance(own) + LEX_EPS_ABS
        check = "passed" if ok else "failed"
        if not ok:
            problems.append("the strict optimum does not lie on the cash/penalty frontier")
    if not monotone:
        problems.append("frontier penalty increases with the cash cap")
    if not convex:
        problems.append("frontier is not convex in the cash cap")
    return Frontier(tuple(result), least_cash, monotone, convex, check, tuple(problems))


# --- Sweeps ----------------------------------------------------------------------------


def _scale_units(
    problem: PlanningProblem, change: Callable[[ServiceUnit], ServiceUnit]
) -> PlanningProblem:
    return replace(problem, units=tuple(change(u) for u in problem.units))


def apply_sweep(problem: PlanningProblem, parameter: str, value: float) -> PlanningProblem:
    """``problem`` with one parameter set (``backlog_penalty``) or scaled (multipliers)."""
    if parameter == "demand_multiplier":
        return _scale_units(
            problem, lambda u: replace(u, demand=tuple(d * value for d in u.demand))
        )
    if parameter == "discretionary_budget_multiplier":
        budget = problem.discretionary_budget
        return replace(problem, discretionary_budget=None if budget is None else budget * value)
    if parameter == "regular_hours_multiplier":
        return _scale_units(
            problem, lambda u: replace(u, regular_hours=tuple(h * value for h in u.regular_hours))
        )
    if parameter == "productivity_multiplier":
        return _scale_units(
            problem,
            lambda u: replace(
                u,
                regular_productivity=u.regular_productivity * value,
                overtime_productivity=u.overtime_productivity * value,
            ),
        )
    if parameter == "cost_multiplier":
        return _scale_units(
            problem,
            lambda u: replace(
                u,
                regular_wage=u.regular_wage * value,
                overtime_wage=u.overtime_wage * value,
                surge_price=u.surge_price * value,
            ),
        )
    if parameter == "backlog_penalty":
        return _scale_units(problem, lambda u: replace(u, backlog_penalty=value))
    raise AssertionError(f"unknown sweep parameter {parameter!r}")  # pragma: no cover


@dataclass(frozen=True)
class SweepPoint:
    parameter: str
    value: float
    problem: PlanningProblem
    attempt: Attempt
    binding: tuple[str, ...]
    budget_dual: float | None
    terminal_dual: float | None


#: Proven monotonicity of the strict model in each swept parameter, as
#: (quantity, direction, feasibility direction). "up": non-decreasing as the parameter
#: rises; "down": non-increasing. Feasibility "lost": once infeasible, stays infeasible.
MONOTONE: Final = {
    "demand_multiplier": (("penalised_score", "up"),),
    "discretionary_budget_multiplier": (("penalised_score", "down"),),
    "regular_hours_multiplier": (("penalised_score", "down"),),
    "productivity_multiplier": (("penalised_score", "down"),),
    "cost_multiplier": (("penalised_score", "up"),),
    "backlog_penalty": (
        ("penalised_score", "up"),
        ("backlog_request_days", "down"),
        ("cash_plus_terminal_penalty", "up"),
    ),
}
FEASIBILITY: Final = {
    "demand_multiplier": "lost",
    "discretionary_budget_multiplier": "gained",
    "regular_hours_multiplier": "gained",
    "productivity_multiplier": "gained",
    "cost_multiplier": "lost",
    "backlog_penalty": "unchanged",
}


@dataclass(frozen=True)
class MonotonicityCheck:
    parameter: str
    quantity: str
    direction: str
    holds: bool
    worst_violation: float
    compared_pairs: int


def binding_summary(duals: Sequence[DualValue]) -> tuple[str, ...]:
    """Binding constraint kinds with the number of days they bind (balance rows omitted)."""
    counts: dict[str, int] = {}
    for dual in duals:
        if dual.binding and dual.constraint != "balance":
            counts[dual.constraint] = counts.get(dual.constraint, 0) + 1
    return tuple(
        name if dual_day is None else f"{name} ({counts[name]} days)"
        for name, dual_day in (
            (name, next(d.day for d in duals if d.constraint == name)) for name in sorted(counts)
        )
    )


def _metric(attempt: Attempt, quantity: str, problem: PlanningProblem) -> float | None:
    if attempt.solved is None:
        return None
    evaluation = attempt.solved.evaluation
    if quantity == "cash_plus_terminal_penalty":
        return evaluation.discretionary_cost + evaluation.terminal_penalty
    value = getattr(evaluation, quantity)
    return float(value)


def run_sweep(
    problem: PlanningProblem, settings: SolverSettings, parameter: str, values: Sequence[float]
) -> tuple[tuple[SweepPoint, ...], tuple[MonotonicityCheck, ...]]:
    """Solve the strict model at each value and check the proven monotonicity properties."""
    points = []
    for value in values:
        changed = apply_sweep(problem, parameter, value)
        attempt = solve_problem(changed, settings)
        binding: tuple[str, ...] = ()
        budget_dual = terminal_dual = None
        if attempt.solved is not None:
            duals = dual_values(attempt.solved.lp, attempt.solved.result)
            binding = binding_summary(duals)
            budget_dual = next((d.marginal for d in duals if d.constraint == "budget"), None)
            terminal_dual = next(
                (d.marginal for d in duals if d.constraint == "terminal_backlog"), None
            )
        points.append(
            SweepPoint(parameter, value, changed, attempt, binding, budget_dual, terminal_dual)
        )
    checks = []
    for quantity, direction in MONOTONE[parameter]:
        worst = 0.0
        pairs = 0
        for a, b in itertools.pairwise(points):
            first = _metric(a.attempt, quantity, a.problem)
            second = _metric(b.attempt, quantity, b.problem)
            if first is None or second is None:
                continue
            pairs += 1
            change = second - first if direction == "up" else first - second
            allowed = 10 * tolerance(first, second)
            if change < -allowed:
                worst = max(worst, -change)
        checks.append(MonotonicityCheck(parameter, quantity, direction, worst == 0.0, worst, pairs))
    feasible = [p.attempt.optimal for p in points]
    rule = FEASIBILITY[parameter]
    if rule == "lost":
        holds = all(not (not a and b) for a, b in itertools.pairwise(feasible))
    elif rule == "gained":
        holds = all(not (a and not b) for a, b in itertools.pairwise(feasible))
    else:
        holds = len(set(feasible)) <= 1
    checks.append(
        MonotonicityCheck(
            parameter, "feasibility", rule, holds, 0.0 if holds else 1.0, len(points) - 1
        )
    )
    return tuple(points), tuple(checks)


# --- Scenario analysis -------------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioResult:
    """Everything computed for one scenario."""

    scenario_id: str
    problem: PlanningProblem
    strict: Attempt
    without_target: Attempt
    diagnosis: Diagnosis | None
    face: OptimalFace | None
    duals: tuple[DualValue, ...]
    finite_differences: tuple[FiniteDifference, ...]
    policies: tuple[PolicyOutcome, ...]
    frontier: Frontier | None
    problems: tuple[str, ...] = field(default=())

    @property
    def feasibility(self) -> str:
        return STRICT if self.strict.optimal else RELAXED_TERMINAL_TARGET

    @property
    def reported(self) -> Attempt:
        """The strict plan, or the relaxed plan when the strict model is infeasible."""
        if self.strict.optimal or self.diagnosis is None:
            return self.strict
        return self.diagnosis.relaxed


def analyse_scenario(
    scenario_id: str,
    problem: PlanningProblem,
    settings: SolverSettings,
    triggers: Mapping[str, float],
    *,
    shadow_steps: Mapping[str, float] | None,
    frontier_points: int | None,
) -> ScenarioResult:
    """Strict plan (or diagnosis and relaxed plan), optimal face, duals, policies, frontier."""
    strict = solve_problem(problem, settings)
    without_target = solve_problem(problem, settings, terminal=Terminal.NONE)
    problems = list(strict.problems) + list(without_target.problems)
    diagnosis = face = None
    duals: tuple[DualValue, ...] = ()
    differences: tuple[FiniteDifference, ...] = ()
    if strict.solved is not None:
        duals = dual_values(strict.solved.lp, strict.solved.result)
        face = optimal_face(problem, settings, strict.solved)
        problems.extend(face.problems)
        if shadow_steps is not None:
            differences = finite_differences(strict.solved, settings, shadow_steps)
            for item in differences:
                if not (item.bracket_ok and item.convex_ok and item.sign_ok):
                    problems.append(
                        f"shadow price {item.dual.key()} outside its finite-difference bracket"
                    )
    else:
        diagnosis = diagnose(problem, settings)
        for attempt in diagnosis.attempts:
            problems.extend(attempt.problems)
        if not diagnosis.relaxed.optimal:
            problems.append("the relaxed plan could not be computed")
    policies = tuple(
        evaluate_policy(problem, settings, policy, triggers)
        for policy in (FIXED_CAPACITY, BACKLOG_TRIGGER)
    )
    for outcome in policies:
        problems.extend(outcome.problems)
        if (
            strict.solved is not None
            and outcome.check.feasible
            and strict.solved.evaluation.penalised_score
            > outcome.evaluation.penalised_score
            + 10 * tolerance(outcome.evaluation.penalised_score)
        ):
            problems.append(
                f"the strict optimum scores worse than feasible policy {outcome.policy}"
            )
    curve = None
    if frontier_points is not None:
        curve = frontier(problem, settings, frontier_points, strict.solved)
        if curve is not None:
            problems.extend(curve.problems)
    return ScenarioResult(
        scenario_id=scenario_id,
        problem=problem,
        strict=strict,
        without_target=without_target,
        diagnosis=diagnosis,
        face=face,
        duals=duals,
        finite_differences=differences,
        policies=policies,
        frontier=curve,
        problems=tuple(problems),
    )
