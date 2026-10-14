"""Analyses of the M4 planning model: solves, diagnostics, tradeoffs and sensitivity (M4).

Everything here is built on :mod:`atlas.optimizer` (the LP) and :mod:`atlas.planning`
(the independent evaluation). Every programme solved through :func:`solve_problem` is
checked three ways before its result is used: the optimality certificate from primal and
dual values, the plan's constraints in plain loops, and the agreement between the solver's
objective and the objective recomputed from the plan. A failed check is recorded as a
*problem* and the runner reports it as an error; it is never silently accepted.

* **Strict plan**: the planning model as configured (no slack variables). Its optimum
  (score, duals, optimal face) comes from the planning programme; the *reported* plan is
  the least-cash plan among the optimal ones (a lexicographic second stage), so that the
  numbers reported for a non-unique optimum do not depend on which vertex HiGHS returns.
* **Infeasibility diagnosis** (only when the strict model is infeasible; every number is
  a DIAGNOSTIC, never a plan for the stated constraints):
  the least terminal-target violation reachable with unlimited discretionary cash and
  within the budget (soft targets, ``min sum v``), the least discretionary spend that would
  reach the HYPOTHETICAL target, and the analytic lower bound on the terminal backlog. The
  RELAXED plan is the lexicographic optimum: least violation within the budget first, then
  the planning objective among those plans, then the least cash. It is labelled
  ``relaxed_terminal_target``.
* **Optimal face**: a complete uniqueness test (active-set distance, see
  :func:`optimal_face`) and the ranges of cash, backlog and daily extra capacity over all
  optimal plans.
* **Shadow prices**: every dual bracketed by one-sided finite differences of the optimal
  value (convex in every right-hand side, so ``backward <= dual <= forward`` must hold).
* **Policies**: the fixed-capacity and backlog-trigger rules, plus the LP at the same
  discretionary cash (least request-days) and at the same backlog (least cash).
* **Frontier**: least notional backlog penalty for each cap on discretionary cash
  (epsilon constraint), with the least cash achieving it (lexicographic).
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
    CERT_REL,
    Certificate,
    Cleaning,
    DualValue,
    ExtraRow,
    FloatArray,
    LinearProgram,
    LPResult,
    Objective,
    OptimizationError,
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
    binding_constraints,
    check_plan,
    evaluate_plan,
    least_terminal_backlog,
    surge_delay_days,
    tolerance,
)
from atlas.policies import (
    BACKLOG_TRIGGER,
    FIXED_CAPACITY,
    backlog_trigger_plan,
    fixed_capacity_plan,
)

#: Slack allowed above an earlier optimum in a later lexicographic stage, used only when the
#: stage cannot be solved on the exact optimal face (see :func:`minimise_on_face`).
LEX_EPS_ABS: Final = 1e-6
LEX_EPS_REL: Final = 1e-9
#: Duals and reduced costs at most this times max(1, max |c|) count as zero when the optimal
#: face is built from complementary slackness (the certificate's dual tolerance, CERT_REL).
FACE_DUAL_REL: Final = CERT_REL
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
    terminal: Terminal = Terminal.HARD
    budget_row: bool = True
    extra_rows: tuple[ExtraRow, ...] = ()
    ignored: tuple[str, ...] = ()

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
    #: True when a later lexicographic stage could not be solved on the exact optimal face
    #: of the earlier one and an epsilon cap (:func:`lex_cap`) was used instead.
    epsilon_fallback: bool = False

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
    if objective is Objective.PENALTY:
        return evaluation.backlog_penalty + evaluation.terminal_penalty
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
    return _checked(
        lp,
        solve_lp(lp, settings),
        terminal=terminal,
        budget_row=budget_row,
        extra_rows=extra_rows,
    )


def _checked(
    lp: LinearProgram,
    result: LPResult,
    *,
    terminal: Terminal,
    budget_row: bool,
    extra_rows: Sequence[ExtraRow],
) -> Attempt:
    """The three independent checks of a solved programme (see the module docstring)."""
    objective, problem = lp.objective, lp.problem
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
    solved = Solved(
        lp,
        result,
        plan,
        evaluation,
        check,
        certificate,
        cleaning,
        value,
        recomputed,
        terminal,
        budget_row,
        tuple(extra_rows),
        tuple(ignore),
    )
    return Attempt(
        objective, result.status, result.message, result.iterations, solved, tuple(problems)
    )


def cap_row(lp_like: LinearProgram, objective: Objective, cap: float, name: str) -> ExtraRow:
    """``objective(v) <= cap`` on the layout of ``lp_like`` (frontier and policy caps)."""
    c, constant = objective_vector(lp_like.problem, lp_like.layout, objective)
    return ExtraRow(Row(name, None, None), c, cap - constant)


def face_programme(lp: LinearProgram, result: LPResult) -> tuple[LinearProgram, float, int, int]:
    """``lp`` restricted to its optimal face, by complementary slackness with ``result``.

    A feasible plan is optimal if and only if it satisfies complementary slackness with an
    optimal dual: it sits at every bound whose reduced cost is nonzero and keeps every row
    with a nonzero dual tight. So the face is the programme with those bounds fixed and
    those rows turned into equalities; no row parallel to the objective (which leaves the
    solver a degenerate, near-infeasible programme on near-ties) is needed. Duals and
    reduced costs within the certificate's dual tolerance (``FACE_DUAL_REL * max(1, max |c|)``)
    count as zero, so prices tied to within that tolerance are treated as tied; callers
    check that the plans they use score the optimum. Returns the restricted programme
    (objective unchanged), the threshold, the number of bounds fixed and the number of rows
    made tight.
    """
    if (
        result.lower_marginals is None
        or result.upper_marginals is None
        or result.ub_marginals is None
    ):  # pragma: no cover - only optimal results are restricted
        raise AssertionError("optimal result without marginals")
    threshold = FACE_DUAL_REL * max(1.0, float(np.max(np.abs(lp.c), initial=0.0)))
    lower, upper = lp.lower.copy(), lp.upper.copy()
    fixed = 0
    for j in range(lp.layout.size):
        if result.lower_marginals[j] > threshold:
            upper[j] = lower[j]
            fixed += 1
        elif result.upper_marginals[j] < -threshold and np.isfinite(upper[j]):
            lower[j] = upper[j]
            fixed += 1
    tight = [i for i in range(len(lp.ub_rows)) if result.ub_marginals[i] < -threshold]
    restricted = replace(
        lp,
        lower=lower,
        upper=upper,
        a_ub=np.vstack([lp.a_ub, -lp.a_ub[tight]]) if tight else lp.a_ub,
        b_ub=np.append(lp.b_ub, -lp.b_ub[tight]) if tight else lp.b_ub,
        ub_rows=(
            *lp.ub_rows,
            *(
                Row(f"tight_{lp.ub_rows[i].constraint}", lp.ub_rows[i].unit_id, lp.ub_rows[i].day)
                for i in tight
            ),
        ),
    )
    return restricted, threshold, fixed, len(tight)


def minimise_on_face(
    lp: LinearProgram,
    result: LPResult,
    settings: SolverSettings,
    objective: Objective,
    c: FloatArray,
    constant: float = 0.0,
) -> tuple[LinearProgram, LPResult, bool]:
    """Minimise ``c @ v + constant`` over the optimal plans of ``lp`` (optimal at ``result``).

    Solved on the exact optimal face (:func:`face_programme`). Only if the solver cannot
    solve that programme (a numerical failure: the earlier optimal plan lies on the face)
    is it repeated with the earlier objective capped at :func:`lex_cap` of its optimum;
    the returned flag says so. The solver error of the fallback, if any, propagates.
    """
    face, _, _, _ = face_programme(lp, result)
    restricted = replace(face, objective=objective, c=c, constant=constant)
    try:
        solution = solve_lp(restricted, settings)
    except OptimizationError:
        solution = None
    if solution is not None and solution.status is SolveStatus.OPTIMAL:
        return restricted, solution, False
    if result.x is None:  # pragma: no cover - only optimal results are restricted
        raise AssertionError("optimal result without a solution")
    capped = replace(
        lp,
        objective=objective,
        c=c,
        constant=constant,
        a_ub=np.vstack([lp.a_ub, lp.c]),
        b_ub=np.append(lp.b_ub, lex_cap(lp.value(result.x)) - lp.constant),
        ub_rows=(*lp.ub_rows, Row(f"cap_{lp.objective}", None, None)),
    )
    return capped, solve_lp(capped, settings), True


def solve_on_face(previous: Solved, settings: SolverSettings, objective: Objective) -> Attempt:
    """Minimise ``objective`` among the optimal plans of ``previous`` (a lexicographic stage).

    Checked like every programme, and also that the earlier objective keeps its optimum.
    """
    lp = previous.lp
    c, constant = objective_vector(lp.problem, lp.layout, objective)
    restricted, result, fallback = minimise_on_face(
        lp, previous.result, settings, objective, c, constant
    )
    attempt = _checked(
        restricted,
        result,
        terminal=previous.terminal,
        budget_row=previous.budget_row,
        extra_rows=previous.extra_rows,
    )
    if attempt.solved is not None:
        kept = lp.value(attempt.solved.x)
        magnitude = math.fsum(np.abs(lp.c * attempt.solved.x).tolist())
        if abs(kept - previous.objective_value) > 10 * tolerance(magnitude, kept):
            attempt = replace(
                attempt,
                problems=(
                    *attempt.problems,
                    f"lexicographic stage {objective} moved the earlier optimum "
                    f"{previous.objective_value!r} to {kept!r}",
                ),
            )
    return replace(attempt, epsilon_fallback=fallback)


def solve_stages(
    problem: PlanningProblem,
    settings: SolverSettings,
    objectives: Sequence[Objective],
    *,
    terminal: Terminal = Terminal.HARD,
    budget_row: bool = True,
    extra_rows: Sequence[ExtraRow] = (),
) -> tuple[Attempt, ...]:
    """Lexicographic optimisation: each objective is minimised over the optimal face of the
    previous stage (:func:`solve_on_face`), so later stages keep every earlier optimum
    exactly. Returns one attempt per stage; an infeasible stage is repeated for the
    remaining ones.
    """
    attempts: list[Attempt] = []
    for objective in objectives:
        if attempts and attempts[-1].solved is None:
            attempts.append(attempts[-1])
            continue
        if not attempts:
            attempt = solve_problem(
                problem,
                settings,
                objective,
                terminal=terminal,
                budget_row=budget_row,
                extra_rows=extra_rows,
            )
        else:
            attempt = solve_on_face(attempts[-1].require(), settings, objective)
        attempts.append(attempt)
    return tuple(attempts)


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
    """Minimise ``first``; then ``second`` among the plans that keep its optimum.

    The second stage makes the reported plan well defined: the first objective alone
    leaves cash, timing and backlog free on a large optimal face.
    """
    stage1, stage2 = solve_stages(
        problem,
        settings,
        (first, second),
        terminal=terminal,
        budget_row=budget_row,
        extra_rows=extra_rows,
    )
    return stage1, stage2


@dataclass(frozen=True)
class Canonical:
    """An optimum and the reported plan: the least-cash plan among the optimal ones.

    ``optimum`` is the programme of the objective itself (its value, duals and optimal
    face); ``plan`` minimises discretionary cash over its optimal face (see
    :func:`solve_on_face`). Reporting the least-cash optimal plan makes cash and the
    penalty (and, without a terminal penalty, the request-days) independent of the vertex
    the solver happens to return; when the optimum is not unique, the ranges over all
    optimal plans are reported as well.
    """

    optimum: Attempt
    plan: Attempt

    @property
    def optimal(self) -> bool:
        return self.optimum.optimal and self.plan.optimal

    @property
    def problems(self) -> tuple[str, ...]:
        if self.plan is self.optimum:
            return self.optimum.problems
        return self.optimum.problems + self.plan.problems


def solve_canonical(
    problem: PlanningProblem,
    settings: SolverSettings,
    objective: Objective = Objective.WEIGHTED,
    *,
    terminal: Terminal = Terminal.HARD,
    budget_row: bool = True,
    extra_rows: Sequence[ExtraRow] = (),
) -> Canonical:
    """``objective``, then the least discretionary cash among its optimal plans."""
    optimum, plan = solve_stages(
        problem,
        settings,
        (objective, Objective.DISCRETIONARY_CASH),
        terminal=terminal,
        budget_row=budget_row,
        extra_rows=extra_rows,
    )
    return Canonical(optimum, plan)


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
    relaxed_optimum: Attempt | None = None
    binding_unlimited: Mapping[str, int] = field(default_factory=dict)
    #: The relaxed plan meets every constraint to within the checking tolerance: the strict
    #: model is infeasible only at the solver's (tighter) tolerance.
    borderline: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": "DIAGNOSTIC: not a plan for the stated constraints",
            "borderline_within_checking_tolerance": self.borderline,
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
            "limits_binding_in_least_violation_plan_with_unlimited_cash": dict(
                sorted(self.binding_unlimited.items())
            ),
            "relaxed_plan": RELAXED_TERMINAL_TARGET if self.relaxed.optimal else None,
        }


#: A least terminal-target violation at most this (requests) means the soft programme met
#: every target at solver precision, which contradicts an infeasible verdict.
CONTRADICTION_TOL: Final = 1e-10


def confirm_infeasible(least_violation: float | None) -> list[str]:
    """Independent confirmation of an infeasible verdict (a problem if it fails).

    The strict model can only be infeasible through the terminal targets (every other
    constraint holds for ``x = o = s = 0``), so it is infeasible exactly when the certified
    soft-target programme within the budget needs a positive violation. A positive
    violation within the checking tolerance confirms the verdict too; the diagnosis then
    calls the scenario BORDERLINE (:attr:`Diagnosis.borderline`).
    """
    if least_violation is None:
        return ["infeasible verdict not confirmed: the soft-target programme failed"]
    if least_violation <= CONTRADICTION_TOL:
        return [
            "infeasible verdict contradicted: a plan within the budget meets every target "
            f"(least violation {least_violation!r})"
        ]
    return []


def describe_binding(problem: PlanningProblem, counts: Mapping[str, int]) -> str:
    """Plain-language list of the limits that bind in a plan (see binding_constraints)."""
    parts = []
    for unit in problem.units:
        if counts.get("overtime_total_limit") and unit.overtime_total_hours is not None:
            parts.append(f"overtime total ({unit.overtime_total_hours:,.0f} staff hours) used up")
        if counts.get("surge_total_limit") and unit.surge_total is not None:
            parts.append(f"surge total ({unit.surge_total:,.0f} requests) used up")
        delay = surge_delay_days(unit)
        if delay:
            parts.append(f"surge unavailable on the first {delay} days")
    if counts.get("overtime_daily_limit"):
        parts.append(f"daily overtime limit reached on {counts['overtime_daily_limit']} days")
    if counts.get("surge_daily_limit"):
        parts.append(f"daily surge limit reached on {counts['surge_daily_limit']} days")
    if counts.get("budget"):
        parts.append("discretionary budget spent")
    return "; ".join(parts) if parts else "no limit binds"


def _amount(value: float, decimals: int) -> str:
    """``value`` with thousands separators, or three significant digits below one unit (so
    a tiny violation or shortfall never prints as 0)."""
    if value == 0 or abs(value) >= 1:
        return f"{value:,.{decimals}f}"
    return f"{value:.3g}"


def diagnose(problem: PlanningProblem, settings: SolverSettings) -> Diagnosis:
    """Explain an infeasible strict model (it can only be the terminal targets)."""
    bounds = {u.unit_id: least_terminal_backlog(u) for u in problem.units}
    exact = all(u.overtime_total_hours is None and u.surge_total is None for u in problem.units)
    unlimited, unlimited_plan = solve_stages(
        problem,
        settings,
        (Objective.TERMINAL_VIOLATION, Objective.DISCRETIONARY_CASH),
        terminal=Terminal.SOFT,
        budget_row=False,
    )
    within, relaxed_optimum, relaxed = solve_stages(
        problem,
        settings,
        (Objective.TERMINAL_VIOLATION, Objective.WEIGHTED, Objective.DISCRETIONARY_CASH),
        terminal=Terminal.SOFT,
    )
    reasons: list[str] = []
    least_unlimited = unlimited.value
    least_within = within.value
    spend: float | None = None
    shortfall: float | None = None
    attempts = [unlimited, unlimited_plan, within, relaxed_optimum, relaxed]
    binding: dict[str, int] = {}
    if unlimited_plan.solved is not None:
        binding = binding_constraints(problem, unlimited_plan.solved.plan)
    reachable = least_unlimited is not None and least_unlimited <= tolerance(least_unlimited)
    if least_unlimited is None:  # pragma: no cover - soft targets are always feasible
        reasons.append("the least terminal-target violation could not be computed")
    elif not reachable:
        reasons.append(
            "the HYPOTHETICAL terminal-backlog target cannot be reached with any amount of "
            f"discretionary cash: the least violation is {_amount(least_unlimited, 1)} "
            f"requests (in the least-violation plan: {describe_binding(problem, binding)})"
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
                "under this deterministic demand path and the HYPOTHETICAL prices, no plan "
                f"spending less than {_amount(spend, 0)} USD on overtime and surge reaches the "
                f"HYPOTHETICAL terminal-backlog target ({_amount(shortfall, 0)} USD above the "
                "HYPOTHETICAL budget; DIAGNOSTIC, not a budget request)"
            )
        elif spend is None:
            reasons.append(
                "with unlimited discretionary cash the least terminal-target violation is "
                f"{_amount(least_unlimited, 1)} requests, within the checking tolerance, but "
                "the solver (tolerance 1e-9) finds no plan that meets the target exactly"
            )
    for unit_id, bound in bounds.items():
        unit = problem.unit(unit_id)
        target = unit.terminal_backlog_max
        if exact and target is not None and bound > target + tolerance(bound, target):
            reasons.append(
                f"{unit_id}: even serving at maximum capacity every day leaves "
                f"{_amount(bound, 1)} requests at the end of the horizon "
                f"(target {_amount(target, 0)})"
            )
    borderline = relaxed.solved is not None and check_plan(problem, relaxed.solved.plan).feasible
    if borderline:
        reasons.append(
            "BORDERLINE: the relaxed plan meets every HYPOTHETICAL constraint to within the "
            "checking tolerance (least violation within the budget "
            f"{_amount(least_within or 0.0, 1)} requests); the strict model is infeasible "
            "only at the solver's tolerance, so this scenario lies on the feasibility boundary"
        )
    if not reasons:
        reasons.append(
            "no plan meets every HYPOTHETICAL constraint at the solver's tolerance; see the "
            "least terminal-target violations"
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
        relaxed_optimum=relaxed_optimum,
        borderline=borderline,
        binding_unlimited=binding,
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

    The optimal face is built exactly, from complementary slackness with the certified
    optimal duals: a feasible plan is optimal if and only if it sits at every bound whose
    reduced cost is nonzero and keeps every row with a nonzero dual tight. Duals and reduced
    costs within the certificate's dual tolerance (``dual_threshold``,
    ``1e-9 * max(1, max |c|)``) count as zero, so prices tied to within that tolerance are
    treated as tied; every plan used below is checked to score the optimum. No slack row
    parallel to the objective is needed, so near-ties neither break the solver nor hide an
    alternative optimum behind a near-flat direction.

    ``max_active_distance`` is the largest total distance, over that face, from the bounds
    and tight rows active at the reported vertex (rows normalised by their largest
    coefficient; requests or staff hours). A vertex is determined by its active constraints,
    so the distance is zero exactly when the reported plan is the only optimal plan.
    Ranges are over the same face.
    """

    unique: bool
    dual_threshold: float
    bounds_fixed: int
    rows_tight: int
    max_active_distance: float
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
    for i in range(len(lp.ub_rows)):
        scale = max(1.0, float(np.max(np.abs(lp.a_ub[i]), initial=0.0)))
        magnitude = exact_dot(np.abs(lp.a_ub[i]), np.abs(v)) + abs(float(lp.b_ub[i]))
        slack = float(lp.b_ub[i]) - exact_dot(lp.a_ub[i], v)
        if slack <= tolerance(magnitude):  # model rows and lexicographic caps alike
            linear -= lp.a_ub[i] / scale
            constant += float(lp.b_ub[i]) / scale
    return linear, constant


def optimal_face(
    problem: PlanningProblem, settings: SolverSettings, strict: Solved, *, daily: bool = True
) -> OptimalFace:
    """Uniqueness test and ranges over the optimal face of the programme that produced
    ``strict`` (the planning model, or a relaxed or diagnostic stage with its own target
    handling, budget row and lexicographic caps). ``daily`` False skips the per-day ranges.

    Every face programme is certified, its plan is checked against the constraints of the
    programme (:func:`check_plan`) and must score the optimum of ``strict``; a solver
    failure is recorded as a problem instead of aborting the run.
    """
    problems: list[str] = []
    face, threshold, fixed, tight = face_programme(strict.lp, strict.result)
    optimum = strict.objective_value
    magnitude = math.fsum(np.abs(strict.lp.c * strict.x).tolist())

    def extreme(coefficients: FloatArray, sign: float) -> float | None:
        lp = replace(face, objective=Objective.CUSTOM, c=sign * coefficients, constant=0.0)
        try:
            result = solve_lp(lp, settings)
        except OptimizationError as error:
            problems.append(f"optimal-face programme failed: {error}")
            return None
        if result.status is not SolveStatus.OPTIMAL or result.x is None:
            problems.append(f"optimal-face programme is {result.status}")
            return None
        certificate = certify(lp, result)
        if not certificate.holds:
            problems.append(f"optimal-face certificate failed: {certificate.to_dict()}")
        plan, _ = to_plan(lp, result.x)
        check = check_plan(problem, plan, ignore=strict.ignored)
        if not check.feasible:
            problems.append(
                "optimal-face plan fails the independent check: "
                + "; ".join(v.describe() for v in check.violations[:5])
            )
        score = strict.lp.value(result.x)
        if abs(score - optimum) > 10 * tolerance(magnitude, optimum):
            problems.append(f"optimal-face plan scores {score!r}, not the optimum {optimum!r}")
        return exact_dot(coefficients, result.x)

    linear, constant = _active_distance(strict.lp, strict.x)
    top = extreme(linear, -1.0)
    distance = math.inf if top is None else max(0.0, top + constant)
    unique = distance <= UNIQUENESS_THRESHOLD
    ranges = []
    for name, (units, vector) in _quantity_vectors(strict.lp).items():
        low = extreme(vector, 1.0)
        high = extreme(vector, -1.0)
        if low is not None and high is not None:
            ranges.append(FaceRange(name, units, low, high))
    days: list[tuple[str, int, float, float]] = []
    if daily and not unique:
        for k, unit in enumerate(problem.units):
            for day in range(problem.horizon):
                vector = np.zeros(strict.lp.layout.size)
                vector[strict.lp.layout.index(k, "overtime_hours", day)] = (
                    unit.overtime_productivity
                )
                vector[strict.lp.layout.index(k, "surge", day)] = 1.0
                low = extreme(vector, 1.0)
                high = extreme(vector, -1.0)
                if low is not None and high is not None:
                    days.append((unit.unit_id, day, low, high))
    return OptimalFace(
        unique=bool(unique),
        dual_threshold=threshold,
        bounds_fixed=fixed,
        rows_tight=tight,
        max_active_distance=distance,
        ranges=tuple(ranges),
        daily_extra_capacity=tuple(days),
        problems=tuple(problems),
    )


# --- Shadow prices -----------------------------------------------------------------------

#: Least step (natural units: requests, staff hours, USD) of the local finite-difference test.
FD_SMALL_STEP: Final = 1e-3
#: The local step is at least this fraction of the right-hand side, so the test does not
#: depend on the scale of the inputs (scaling every quantity by k moves every breakpoint
#: k times further away and leaves the duals unchanged).
FD_RELATIVE_STEP: Final = 1e-7
#: Rounding noise allowed in an optimal value, relative to ``sum |c_j x_j|`` (64 ulps).
VALUE_NOISE_REL: Final = 64 * 2.0**-52


def small_step(rhs: float) -> float:
    """The local finite-difference step for a right-hand side ``rhs``."""
    return max(FD_SMALL_STEP, FD_RELATIVE_STEP * abs(rhs))


@dataclass(frozen=True)
class FiniteDifference:
    """One-sided derivatives of the optimal score around one right-hand side.

    The optimal value is convex in every right-hand side and bound, so for any step
    ``backward secant <= left derivative <= dual <= right derivative <= forward secant``.
    ``backward`` / ``forward`` use the local step (:func:`small_step`); equal slopes there
    mean the dual is locally unique, unequal slopes that the point sits at (or within one
    small step of) a breakpoint, where the solver's dual is one subgradient among several.
    ``practical_*`` use the configured step (1 request, 1 staff hour, 100 USD): the average
    effect of that change, not a derivative. ``backward`` is ``None`` at a domain boundary
    (a right-hand side cannot fall below 0); an infeasible side gives ``-inf`` (backward)
    or ``+inf`` (forward). The forward practical change is split into its
    discretionary-cash and notional-penalty parts (least-cash optimal plans before and
    after the step). Slopes are compared with a tolerance that includes ``noise``, the
    rounding noise of two optimal values divided by the step. Every re-solve is certified;
    ``problems`` lists the failures.
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
    noise: float = 0.0
    problems: tuple[str, ...] = ()

    @property
    def locally_unique(self) -> bool:
        if self.backward is None or math.isinf(self.backward) or math.isinf(self.forward):
            return False
        return (
            abs(self.forward - self.backward)
            <= _slope_tol(self.forward, self.backward) + self.noise
        )

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


def _certified(lp: LinearProgram, result: LPResult, what: str, problems: list[str]) -> None:
    certificate = certify(lp, result)
    if not certificate.holds:
        problems.append(f"{what}: optimality certificate failed: {certificate.to_dict()}")


def _value_at(
    lp: LinearProgram, settings: SolverSettings, what: str, problems: list[str]
) -> tuple[float, LPResult | None]:
    """Certified optimal value of a re-solve (``inf`` if infeasible)."""
    try:
        result = solve_lp(lp, settings)
    except OptimizationError as error:
        problems.append(f"{what}: {error}")
        return math.nan, None
    if result.status is SolveStatus.INFEASIBLE or result.x is None:
        return math.inf, None
    _certified(lp, result, what, problems)
    return lp.value(result.x), result


def _secants(
    lp: LinearProgram,
    settings: SolverSettings,
    kind: str,
    index: int,
    rhs: float,
    base: float,
    step: float,
    what: str,
    problems: list[str],
) -> tuple[float | None, float, LPResult | None]:
    """Backward and forward secants of the optimal value, and the forward re-solve."""
    up_lp = _perturbed(lp, kind, index, step)
    up, up_result = _value_at(up_lp, settings, f"{what} +{step:g}", problems)
    forward = (up - base) / step
    down_step = min(step, rhs)
    if down_step <= 0:
        return None, forward, up_result
    down, _ = _value_at(
        _perturbed(lp, kind, index, -down_step), settings, f"{what} -{down_step:g}", problems
    )
    backward = -math.inf if math.isinf(down) else (base - down) / down_step
    return backward, forward, up_result


def _least_cash(
    lp: LinearProgram,
    result: LPResult,
    settings: SolverSettings,
    what: str,
    problems: list[str],
) -> float | None:
    """Least discretionary cash among the optimal plans of ``lp`` (certified)."""
    cash, _ = objective_vector(lp.problem, lp.layout, Objective.DISCRETIONARY_CASH)
    try:
        restricted, solution, _ = minimise_on_face(
            lp, result, settings, Objective.DISCRETIONARY_CASH, cash
        )
    except OptimizationError as error:
        problems.append(f"{what}: least cash: {error}")
        return None
    if solution.x is None:
        problems.append(f"{what}: least-cash programme is {solution.status}")
        return None
    _certified(restricted, solution, f"{what}: least cash", problems)
    return restricted.value(solution.x)


def finite_differences(
    strict: Solved,
    settings: SolverSettings,
    steps: Mapping[str, float],
    base_cash: float | None = None,
) -> tuple[FiniteDifference, ...]:
    """Bracket every shadow price of the strict optimum (see :class:`FiniteDifference`).

    The cash and penalty parts of the practical forward step compare the least-cash optimal
    plans before and after the step (``base_cash`` is the least cash at the base point), so
    they do not depend on which optimal vertex the solver returns.
    """
    lp = strict.lp
    base = strict.objective_value
    value_noise = 2 * VALUE_NOISE_REL * max(1.0, math.fsum(np.abs(lp.c * strict.x).tolist()))
    base_problems: list[str] = []
    if base_cash is None:
        base_cash = _least_cash(lp, strict.result, settings, "base point", base_problems)
    results = []
    for dual in dual_values(lp, strict.result):
        problems = list(base_problems)
        base_problems = []
        what = f"finite difference {dual.constraint} {dual.unit_id or ''} {dual.day}".strip()
        kind, index = _locate(lp, dual)
        step = small_step(dual.rhs)
        backward, forward, _ = _secants(
            lp, settings, kind, index, dual.rhs, base, step, what, problems
        )
        practical = _step_for(dual.rhs_units, steps)
        p_backward, p_forward, p_result = _secants(
            lp, settings, kind, index, dual.rhs, base, practical, what, problems
        )
        noise = value_noise / step
        p_noise = value_noise / practical
        tol = _slope_tol(dual.marginal, forward, *([] if backward is None else [backward])) + noise
        bracket = dual.marginal <= forward + tol and (
            backward is None or dual.marginal >= backward - tol
        )
        convex = (
            (backward is None or backward <= forward + _slope_tol(forward, backward) + noise)
            and (
                p_backward is None
                or p_backward <= p_forward + _slope_tol(p_forward, p_backward) + p_noise
            )
            and (
                backward is None
                or p_backward is None
                or (p_backward <= backward + tol and forward <= p_forward + tol)
            )
        )
        # The optimal score never falls when demand rises (proved for d >= 0), so the dual
        # of a balance row with a positive right-hand side is a non-negative subgradient.
        sign = not (dual.constraint == "balance" and dual.rhs > 0 and dual.marginal < -tol)
        cash = penalty = None
        if math.isfinite(p_forward) and base_cash is not None and p_result is not None:
            up = _perturbed(lp, kind, index, practical)
            up_cash = _least_cash(up, p_result, settings, f"{what} +{practical:g}", problems)
            if up_cash is not None:
                cash = (up_cash - base_cash) / practical
                penalty = p_forward - cash
        results.append(
            FiniteDifference(
                dual=dual,
                step=step,
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
                noise=noise,
                problems=tuple(problems),
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
    least_request_days_at_policy_cash: float | None = None
    least_cash_at_policy_backlog: float | None = None


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
        probe, Objective.DISCRETIONARY_CASH, evaluation.discretionary_cost, "cap_policy_cash"
    )
    cash_first, same_cash = solve_lexicographic(
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
            evaluation.backlog_request_days,
            "cap_policy_backlog",
        ),
        cap_row(
            probe, Objective.TERMINAL_BACKLOG, evaluation.terminal_backlog, "cap_policy_terminal"
        ),
    ]
    backlog_first, same_backlog = solve_lexicographic(
        problem,
        settings,
        Objective.DISCRETIONARY_CASH,
        terminal=Terminal.NONE,
        extra_rows=backlog_caps,
    )
    problems: list[str] = []
    for attempt in (cash_first, same_cash, backlog_first, same_backlog):
        problems.extend(attempt.problems)
    other = set(check.constraints_violated()) - {"terminal_backlog"}
    if other:
        problems.append(f"policy {policy} violates {sorted(other)}")
    least_days = cash_first.value
    least_cash = backlog_first.value
    if least_days is None or least_cash is None or not same_cash.optimal:
        problems.append(f"policy {policy}: the same-cash or same-backlog LP is not optimal")
    else:
        # The policy plan is feasible for both programmes, so the LP can only do as well.
        if least_days > evaluation.backlog_request_days + 10 * tolerance(least_days):
            problems.append(f"policy {policy}: the LP at the same cash has more backlog")
        if least_cash > evaluation.discretionary_cost + 10 * tolerance(least_cash) + LEX_EPS_ABS:
            problems.append(f"policy {policy}: the LP at the same backlog costs more")
    return PolicyOutcome(
        policy,
        plan,
        evaluation,
        check,
        same_cash,
        same_backlog,
        tuple(problems),
        least_request_days_at_policy_cash=least_days,
        least_cash_at_policy_backlog=least_cash,
    )


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
                # Confirm independently: only the target can make a capped plan impossible,
                # so the cap must lie below the least cash that reaches the target.
                if terminal is Terminal.NONE:
                    problems.append(f"frontier point at cap {cap!r} infeasible without a target")
                else:
                    needed = solve_problem(
                        problem,
                        settings,
                        Objective.DISCRETIONARY_CASH,
                        terminal=Terminal.HARD,
                        budget_row=False,
                    )
                    problems.extend(needed.problems)
                    if needed.value is not None and cap >= needed.value + 10 * tolerance(cap):
                        problems.append(
                            f"frontier point at cap {cap!r} reported infeasible although "
                            f"{needed.value!r} USD reaches the target"
                        )
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
    """One sweep value: the planning optimum (``attempt``: score and duals), the reported
    least-cash optimal plan, its optimal face (uniqueness and ranges), or the least
    terminal-target violation within the budget when the strict model is infeasible."""

    parameter: str
    value: float
    problem: PlanningProblem
    attempt: Attempt
    binding: tuple[str, ...]
    budget_dual: float | None
    terminal_dual: float | None
    plan_attempt: Attempt | None = None
    face: OptimalFace | None = None
    least_violation: float | None = None
    problems: tuple[str, ...] = ()

    @property
    def reported(self) -> Attempt:
        return self.plan_attempt if self.plan_attempt is not None else self.attempt


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


DAILY_CONSTRAINTS: Final = ("capacity", "overtime_daily_limit", "surge_daily_limit")


def format_binding(counts: Mapping[str, int]) -> tuple[str, ...]:
    """``binding_constraints`` counts as labels; daily constraints with their day count."""
    return tuple(
        f"{name} ({counts[name]} days)" if name in DAILY_CONSTRAINTS else name
        for name in sorted(counts)
    )


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
    """Solve the strict model at each value and check the proven monotonicity properties.

    The score is the planning optimum; the other quantities come from the least-cash
    optimal plan (the properties hold for every optimal plan). Every programme's check
    problems are kept on the point.
    """
    points = []
    for value in values:
        changed = apply_sweep(problem, parameter, value)
        canonical = solve_canonical(changed, settings)
        attempt = canonical.optimum
        problems = list(canonical.problems)
        binding: tuple[str, ...] = ()
        budget_dual = terminal_dual = None
        face: OptimalFace | None = None
        least_violation: float | None = None
        if attempt.solved is not None:
            duals = dual_values(attempt.solved.lp, attempt.solved.result)
            budget_dual = next((d.marginal for d in duals if d.constraint == "budget"), None)
            terminal_dual = next(
                (d.marginal for d in duals if d.constraint == "terminal_backlog"), None
            )
            face = optimal_face(changed, settings, attempt.solved, daily=False)
            problems.extend(face.problems)
        if canonical.plan.solved is not None:
            binding = format_binding(binding_constraints(changed, canonical.plan.solved.plan))
        else:
            soft = solve_problem(
                changed, settings, Objective.TERMINAL_VIOLATION, terminal=Terminal.SOFT
            )
            problems.extend(soft.problems)
            least_violation = soft.value
            problems.extend(confirm_infeasible(least_violation))
        points.append(
            SweepPoint(
                parameter,
                value,
                changed,
                attempt,
                binding,
                budget_dual,
                terminal_dual,
                canonical.plan,
                face,
                least_violation,
                tuple(problems),
            )
        )
    checks = []
    for quantity, direction in MONOTONE[parameter]:
        worst = 0.0
        pairs = 0
        for a, b in itertools.pairwise(points):
            source_a = a.attempt if quantity == "penalised_score" else a.reported
            source_b = b.attempt if quantity == "penalised_score" else b.reported
            first = _metric(source_a, quantity, a.problem)
            second = _metric(source_b, quantity, b.problem)
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
    """Everything computed for one scenario.

    ``strict`` is the planning optimum (score, duals, optimal face); ``strict_plan`` the
    reported least-cash optimal plan. ``without_target`` / ``without_target_plan`` are the
    same without the HYPOTHETICAL terminal target; ``target_changes_score`` says whether
    dropping the target changes the optimal score at all (if not, the target forces nothing
    and the two programmes share their optimal plans).
    """

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
    strict_plan: Attempt | None = None
    without_target_plan: Attempt | None = None
    target_changes_score: bool | None = None
    relaxed_face: OptimalFace | None = None

    @property
    def feasibility(self) -> str:
        return STRICT if self.strict.optimal else RELAXED_TERMINAL_TARGET

    @property
    def reported(self) -> Attempt:
        """The least-cash strict plan, or the relaxed plan when the strict model is
        infeasible."""
        if self.strict.optimal or self.diagnosis is None:
            return self.strict_plan if self.strict_plan is not None else self.strict
        return self.diagnosis.relaxed

    @property
    def reported_face(self) -> OptimalFace | None:
        return self.face if self.strict.optimal else self.relaxed_face


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
    strict_solve = solve_canonical(problem, settings)
    free_solve = solve_canonical(problem, settings, terminal=Terminal.NONE)
    strict, strict_plan = strict_solve.optimum, strict_solve.plan
    problems = list(strict_solve.problems) + list(free_solve.problems)
    diagnosis = face = relaxed_face = None
    duals: tuple[DualValue, ...] = ()
    differences: tuple[FiniteDifference, ...] = ()
    changes: bool | None = None
    if strict.solved is not None and free_solve.optimum.solved is not None:
        mine = strict.solved.objective_value
        other = free_solve.optimum.solved.objective_value
        changes = abs(mine - other) > 10 * tolerance(mine, other) + LEX_EPS_ABS
    if strict.solved is not None:
        duals = dual_values(strict.solved.lp, strict.solved.result)
        face = optimal_face(problem, settings, strict.solved)
        problems.extend(face.problems)
        if shadow_steps is not None:
            base_cash = (
                None
                if strict_plan.solved is None
                else strict_plan.solved.evaluation.discretionary_cost
            )
            differences = finite_differences(strict.solved, settings, shadow_steps, base_cash)
            for item in differences:
                problems.extend(item.problems)
                if not (item.bracket_ok and item.convex_ok and item.sign_ok):
                    problems.append(
                        f"shadow price {item.dual.key()} outside its finite-difference bracket"
                    )
    else:
        diagnosis = diagnose(problem, settings)
        for attempt in diagnosis.attempts:
            problems.extend(attempt.problems)
        problems.extend(confirm_infeasible(diagnosis.least_violation_within_budget))
        if not diagnosis.relaxed.optimal:
            problems.append("the relaxed plan could not be computed")
        elif diagnosis.relaxed_optimum is not None and diagnosis.relaxed_optimum.solved:
            relaxed_face = optimal_face(problem, settings, diagnosis.relaxed_optimum.solved)
            problems.extend(relaxed_face.problems)
    policies = tuple(
        evaluate_policy(problem, settings, policy, triggers)
        for policy in (FIXED_CAPACITY, BACKLOG_TRIGGER)
    )
    for outcome in policies:
        problems.extend(outcome.problems)
        if (
            strict.solved is not None
            and outcome.check.feasible
            and strict.solved.objective_value
            > outcome.evaluation.penalised_score
            + 10 * tolerance(outcome.evaluation.penalised_score)
        ):
            problems.append(
                f"the strict optimum scores worse than feasible policy {outcome.policy}"
            )
    curve = None
    if frontier_points is not None:
        curve = frontier(problem, settings, frontier_points, strict_plan.solved)
        if curve is not None:
            problems.extend(curve.problems)
    return ScenarioResult(
        scenario_id=scenario_id,
        problem=problem,
        strict=strict,
        without_target=free_solve.optimum,
        diagnosis=diagnosis,
        face=face,
        duals=duals,
        finite_differences=differences,
        policies=policies,
        frontier=curve,
        problems=tuple(problems),
        strict_plan=strict_plan,
        without_target_plan=free_solve.plan,
        target_changes_score=changes,
        relaxed_face=relaxed_face,
    )
