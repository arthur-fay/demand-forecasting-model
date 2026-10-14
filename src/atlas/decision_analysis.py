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
  the planning objective among those plans, then the tie-breaks (least cash, terminal
  backlog, request-days). It is labelled ``relaxed_terminal_target``.
* **Optimal face**: a uniqueness test (active-set distance up to
  :data:`UNIQUENESS_THRESHOLD`, see :func:`optimal_face`) and the ranges of cash, backlog
  and daily extra capacity over all optimal plans.
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
    exact_matvec,
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
    #: How a later lexicographic stage was solved when the optimal face of the earlier one
    #: did not keep its optimum or failed (see :func:`minimise_on_face`): ``None`` (the face
    #: worked), ``"zero_threshold"`` or ``"epsilon_cap"``; for caps on a policy's or the
    #: strict plan's own values, ``"policy_epsilon_cap"`` or ``"frontier_epsilon_cap"``.
    stage_fallback: str | None = None

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


def face_programme(
    lp: LinearProgram, result: LPResult, *, zero_threshold: bool = False
) -> tuple[LinearProgram, float, int, int]:
    """``lp`` restricted to its optimal face, by complementary slackness with ``result``.

    A feasible plan is optimal if and only if it satisfies complementary slackness with an
    optimal dual: it sits at every bound whose reduced cost is nonzero and keeps every row
    with a nonzero dual tight. So the face is the programme with those bounds fixed and
    those rows turned into equalities; no row parallel to the objective (which leaves the
    solver a degenerate, near-infeasible programme on near-ties) is needed.

    A reduced cost counts as zero when it is within ``FACE_DUAL_REL`` of its column's own
    stationarity magnitude ``|c_j| + sum_i |a_ij y_i|`` (the certificate's scale), and a row
    dual when it is within ``FACE_DUAL_REL`` of the largest column scale it couples,
    divided by the coefficient. A large weight on one column therefore does not widen the
    tie tolerance of the others. ``zero_threshold`` counts only exact zeros (a smaller
    face, used when the face with thresholds turns out not to keep the optimum). Every row
    and bound is widened to the optimum's own activity (tight rows are pinned there), so an
    optimum that is feasible only to within the solver's tolerance stays on its face.
    Returns the restricted programme (objective unchanged), the largest threshold, the
    number of bounds fixed and the number of rows made tight.
    """
    if (
        result.x is None
        or result.lower_marginals is None
        or result.upper_marginals is None
        or result.ub_marginals is None
        or result.eq_marginals is None
    ):  # pragma: no cover - only optimal results are restricted
        raise AssertionError("optimal result without marginals")
    y_ub, y_eq = result.ub_marginals, result.eq_marginals
    column_scale = (
        np.abs(lp.c)
        + exact_matvec(np.abs(lp.a_ub.T), np.abs(y_ub))
        + exact_matvec(np.abs(lp.a_eq.T), np.abs(y_eq))
    )
    column_tau = (
        np.zeros(lp.layout.size)
        if zero_threshold
        else (FACE_DUAL_REL * np.maximum(1.0, column_scale))
    )
    row_tau = np.zeros(len(lp.ub_rows))
    if not zero_threshold:
        for i in range(len(lp.ub_rows)):
            coefficients = np.abs(lp.a_ub[i])
            support = coefficients > 0
            scale = (
                float(np.max(column_scale[support] / coefficients[support]))
                if support.any()
                else 0.0
            )
            row_tau[i] = FACE_DUAL_REL * max(1.0, scale)
    x = result.x
    # The optimum is feasible only to within the solver's tolerance: widen every bound and
    # row to include it, so the face is never empty (the widening is at most that
    # tolerance; check_plan still judges every plan against the configured limits).
    lower = np.minimum(lp.lower, x)
    upper = np.where(np.isfinite(lp.upper), np.maximum(lp.upper, x), lp.upper)
    fixed = 0
    for j in range(lp.layout.size):
        if result.lower_marginals[j] > column_tau[j]:
            upper[j] = lower[j]
            fixed += 1
        elif result.upper_marginals[j] < -column_tau[j] and np.isfinite(upper[j]):
            lower[j] = upper[j]
            fixed += 1
    tight = [i for i in range(len(lp.ub_rows)) if y_ub[i] < -row_tau[i]]
    b_ub = np.maximum(lp.b_ub, exact_matvec(lp.a_ub, x))
    restricted = replace(
        lp,
        lower=lower,
        upper=upper,
        a_ub=np.vstack([lp.a_ub, -lp.a_ub[tight]]) if tight else lp.a_ub,
        b_ub=np.append(b_ub, -b_ub[tight]) if tight else b_ub,
        ub_rows=(
            *lp.ub_rows,
            *(
                Row(f"tight_{lp.ub_rows[i].constraint}", lp.ub_rows[i].unit_id, lp.ub_rows[i].day)
                for i in tight
            ),
        ),
    )
    threshold = float(max(np.max(column_tau, initial=0.0), np.max(row_tau, initial=0.0)))
    return restricted, threshold, fixed, len(tight)


def _keeps_optimum(lp: LinearProgram, optimum: float, solution: LPResult | None) -> bool:
    """Whether ``solution`` scores the optimum of ``lp`` to within the checking tolerance."""
    if solution is None or solution.status is not SolveStatus.OPTIMAL or solution.x is None:
        return False
    magnitude = math.fsum(np.abs(lp.c * solution.x).tolist())
    return abs(lp.value(solution.x) - optimum) <= 10 * tolerance(magnitude, optimum)


def minimise_on_face(
    lp: LinearProgram,
    result: LPResult,
    settings: SolverSettings,
    objective: Objective,
    c: FloatArray,
    constant: float = 0.0,
) -> tuple[LinearProgram, LPResult, str | None]:
    """Minimise ``c @ v + constant`` over the optimal plans of ``lp`` (optimal at ``result``).

    Solved on the optimal face (:func:`face_programme`) and accepted only if the plan keeps
    the earlier optimum. Otherwise (a face loosened by the zero tests, or a solver failure)
    the face is rebuilt counting only exact zeros (``"zero_threshold"``), and as a last
    resort the earlier objective is capped at :func:`lex_cap` of its optimum
    (``"epsilon_cap"``). The returned flag names the fallback used; the solver error of the
    last fallback, if any, propagates.
    """
    if result.x is None:  # pragma: no cover - only optimal results are restricted
        raise AssertionError("optimal result without a solution")
    optimum = lp.value(result.x)
    for zero_threshold, flag in ((False, None), (True, "zero_threshold")):
        face, _, _, _ = face_programme(lp, result, zero_threshold=zero_threshold)
        restricted = replace(face, objective=objective, c=c, constant=constant)
        try:
            solution: LPResult | None = solve_lp(restricted, settings)
        except OptimizationError:
            solution = None
        if solution is not None and _keeps_optimum(lp, optimum, solution):
            return restricted, solution, flag
    capped = replace(
        lp,
        objective=objective,
        c=c,
        constant=constant,
        a_ub=np.vstack([lp.a_ub, lp.c]),
        b_ub=np.append(lp.b_ub, lex_cap(optimum) - lp.constant),
        ub_rows=(*lp.ub_rows, Row(f"cap_{lp.objective}", None, None)),
    )
    return capped, solve_lp(capped, settings), "epsilon_cap"


def solve_on_face(previous: Solved, settings: SolverSettings, objective: Objective) -> Attempt:
    """Minimise ``objective`` among the optimal plans of ``previous`` (a lexicographic stage).

    Checked like every programme, and also that the earlier objective keeps its optimum. A
    stage that is not optimal is a problem: the earlier optimum lies on its face.
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
    extra: list[str] = []
    if attempt.solved is None:
        extra.append(
            f"lexicographic stage {objective} is {attempt.status} although the earlier "
            "optimum lies on its face"
        )
    else:
        kept = lp.value(attempt.solved.x)
        magnitude = math.fsum(np.abs(lp.c * attempt.solved.x).tolist())
        if abs(kept - previous.objective_value) > 10 * tolerance(magnitude, kept):
            extra.append(
                f"lexicographic stage {objective} moved the earlier optimum "
                f"{previous.objective_value!r} to {kept!r}"
            )
    return replace(attempt, problems=(*attempt.problems, *extra), stage_fallback=fallback)


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
    previous stage (:func:`solve_on_face`), so later stages keep every earlier optimum.
    Returns one attempt per stage; an infeasible stage is repeated for the remaining ones.
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
    """Minimise ``first``; then ``second`` among the plans that keep its optimum."""
    stage1, stage2 = solve_stages(
        problem,
        settings,
        (first, second),
        terminal=terminal,
        budget_row=budget_row,
        extra_rows=extra_rows,
    )
    return stage1, stage2


#: Tie-breaks among equally scored plans, in order: least discretionary cash, then least
#: terminal backlog, then least request-days. They make every reported total (cash,
#: penalty, terminal backlog, request-days) independent of the optimal vertex HiGHS
#: returns; only the day-by-day split (and overtime against surge) can remain open, and
#: the uniqueness test reports it.
TIE_BREAKS: Final = (
    Objective.DISCRETIONARY_CASH,
    Objective.TERMINAL_BACKLOG,
    Objective.BACKLOG_DAYS,
)


def tie_broken(*first: Objective) -> tuple[Objective, ...]:
    """``first`` followed by the tie-breaks not already among them."""
    return (*first, *(o for o in TIE_BREAKS if o not in first))


@dataclass(frozen=True)
class Canonical:
    """An optimum and the reported plan: the tie-broken plan among the optimal ones.

    ``optimum`` is the programme of the objective itself (its value, duals and optimal
    face); ``plan`` is the last stage of :data:`TIE_BREAKS` (least cash, then least
    terminal backlog, then least request-days among the optimal plans), so every reported
    total is independent of the vertex the solver happens to return; when the optimum is
    not unique, the ranges over all optimal plans are reported as well.
    """

    optimum: Attempt
    plan: Attempt
    stages: tuple[Attempt, ...] = ()

    @property
    def optimal(self) -> bool:
        return self.optimum.optimal and self.plan.optimal

    @property
    def problems(self) -> tuple[str, ...]:
        return attempt_problems(self.stages or (self.optimum, self.plan))

    @property
    def fallbacks(self) -> tuple[str, ...]:
        return stage_fallbacks(self.stages)


def attempt_problems(attempts: Sequence[Attempt]) -> tuple[str, ...]:
    """The problems of distinct attempts (a repeated infeasible stage counts once)."""
    seen: list[Attempt] = []
    problems: list[str] = []
    for attempt in attempts:
        if any(attempt is other for other in seen):
            continue
        seen.append(attempt)
        problems.extend(attempt.problems)
    return tuple(problems)


def stage_fallbacks(attempts: Sequence[Attempt]) -> tuple[str, ...]:
    """The fallbacks used by distinct attempts (see :attr:`Attempt.stage_fallback`)."""
    seen: list[Attempt] = []
    flags: list[str] = []
    for attempt in attempts:
        if any(attempt is other for other in seen):
            continue
        seen.append(attempt)
        if attempt.stage_fallback is not None:
            flags.append(attempt.stage_fallback)
    return tuple(flags)


def solve_canonical(
    problem: PlanningProblem,
    settings: SolverSettings,
    objective: Objective = Objective.WEIGHTED,
    *,
    terminal: Terminal = Terminal.HARD,
    budget_row: bool = True,
    extra_rows: Sequence[ExtraRow] = (),
) -> Canonical:
    """``objective``, then the tie-breaks (:data:`TIE_BREAKS`) among its optimal plans."""
    stages = solve_stages(
        problem,
        settings,
        tie_broken(objective),
        terminal=terminal,
        budget_row=budget_row,
        extra_rows=extra_rows,
    )
    return Canonical(stages[0], stages[-1], stages)


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
    #: Least violation with unlimited cash when one configured limit is removed alone
    #: (``"unit:limit"`` -> requests; ``None`` if that programme failed), computed only
    #: when no amount of cash reaches the target; see :func:`limit_relaxations`.
    limit_relaxations: Mapping[str, float | None] = field(default_factory=dict)
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
            "least_violation_unlimited_cash_with_one_limit_removed_requests": dict(
                sorted(self.limit_relaxations.items())
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


@dataclass(frozen=True)
class LimitRelaxation:
    """One configured limit removed alone, and the least violation (unlimited cash) then."""

    unit_id: str
    limit: str
    description: str
    least_violation: float | None
    attempt: Attempt

    def lowers(self, least_violation: float, reach: float = 0.0) -> bool:
        """Whether removing this limit lowers ``least_violation`` by more than solver noise.

        ``reach`` is the violation the plan check tolerates (:func:`reach_tolerance`): a
        relaxation that gets within it reaches the target. Otherwise the drop must exceed
        ``10 * tolerance`` (or half the violation, for a violation just above ``reach``).
        """
        if self.least_violation is None:
            return False
        if self.least_violation <= reach:
            return least_violation > reach
        return self.least_violation < least_violation - min(
            10 * tolerance(least_violation), least_violation / 2
        )


def reach_tolerance(problem: PlanningProblem) -> float:
    """A total terminal-target violation the plan check tolerates in every unit.

    :func:`check_plan` allows each unit ``tolerance(final, target)``; a total violation of
    at most the smallest of these is within it for every unit however it is shared (for a
    single unit this is exactly the check's tolerance).
    """
    allowed = [
        tolerance(unit.terminal_backlog_max)
        for unit in problem.units
        if unit.terminal_backlog_max is not None
    ]
    return min(allowed, default=0.0)


def _relaxed_limits(unit: ServiceUnit) -> list[tuple[str, str, ServiceUnit]]:
    """``(limit, description, unit with that limit removed)`` for every configured limit.

    A daily limit is removed by raising it above all the work of the horizon; a delayed
    surge becomes available from the first day at its configured daily capacity, also when
    it is configured to start after the horizon (``surge_capacity_when_available``).
    """
    work = math.fsum(unit.demand) + unit.initial_backlog + 1.0
    variants: list[tuple[str, str, ServiceUnit]] = []
    if unit.overtime_productivity > 0:
        hours = work / unit.overtime_productivity
        variants.append(
            (
                "overtime_daily_limit",
                f"the daily overtime limit of {_amount(max(unit.overtime_max_hours), 0)} "
                "staff hours",
                replace(unit, overtime_max_hours=(hours,) * unit.horizon),
            )
        )
    if any(limit > 0 for limit in unit.surge_max):
        variants.append(
            (
                "surge_daily_limit",
                f"the daily surge limit of {_amount(max(unit.surge_max), 0)} requests",
                replace(
                    unit, surge_max=tuple(work if limit > 0 else 0.0 for limit in unit.surge_max)
                ),
            )
        )
        delay = surge_delay_days(unit)
        if delay:
            daily = max(unit.surge_max)
            variants.append(
                (
                    "surge_delay",
                    f"the surge delay of {delay} days",
                    replace(
                        unit,
                        surge_max=tuple(
                            daily if day < delay else limit
                            for day, limit in enumerate(unit.surge_max)
                        ),
                    ),
                )
            )
    elif unit.surge_capacity_when_available:
        daily = unit.surge_capacity_when_available
        variants.append(
            (
                "surge_delay",
                f"the surge start after the horizon (surge of {_amount(daily, 0)} requests a "
                "day is never available)",
                replace(unit, surge_max=(daily,) * unit.horizon),
            )
        )
    if unit.overtime_total_hours is not None:
        variants.append(
            (
                "overtime_total_limit",
                f"the overtime total of {_amount(unit.overtime_total_hours, 0)} staff hours",
                replace(unit, overtime_total_hours=None),
            )
        )
    if unit.surge_total is not None:
        variants.append(
            (
                "surge_total_limit",
                f"the surge total of {_amount(unit.surge_total, 0)} requests",
                replace(unit, surge_total=None),
            )
        )
    return variants


def limit_relaxations(
    problem: PlanningProblem, settings: SolverSettings
) -> tuple[LimitRelaxation, ...]:
    """The least violation with unlimited cash when each configured limit is removed alone.

    This names the limits that keep the target out of reach without depending on which
    least-violation plan the solver returns (that plan is rarely unique): a limit is named
    only if removing it alone lowers the least violation. The soft-target programme is
    always feasible, so a relaxation that is not optimal (or that the solver refuses) is a
    problem, never a silent "does not lower it".
    """
    results = []
    for k, unit in enumerate(problem.units):
        for limit, description, relaxed_unit in _relaxed_limits(unit):
            units = list(problem.units)
            units[k] = relaxed_unit
            try:
                attempt = solve_problem(
                    replace(problem, units=tuple(units)),
                    settings,
                    Objective.TERMINAL_VIOLATION,
                    terminal=Terminal.SOFT,
                    budget_row=False,
                )
            except OptimizationError as error:
                # Not optimal; the problem (an ERROR) says why.
                attempt = Attempt(
                    Objective.TERMINAL_VIOLATION,
                    SolveStatus.INFEASIBLE,
                    f"solver error: {error}",
                    0,
                    problems=(f"limit relaxation {unit.unit_id}:{limit} failed: {error}",),
                )
            if not attempt.optimal and not attempt.problems:
                attempt = replace(
                    attempt,
                    problems=(
                        f"limit relaxation {unit.unit_id}:{limit} is {attempt.status} although "
                        "the soft-target programme is always feasible",
                    ),
                )
            results.append(
                LimitRelaxation(unit.unit_id, limit, description, attempt.value, attempt)
            )
    return tuple(results)


def _limits_text(
    problem: PlanningProblem,
    relaxations: Sequence[LimitRelaxation],
    least: float,
    reach: float,
) -> str:
    def prefix(r: LimitRelaxation) -> str:
        return f"{r.unit_id}: " if len(problem.units) > 1 else ""

    named = [
        prefix(r) + f"{r.description} (to {_amount(r.least_violation or 0.0, 1)} requests "
        "without it)"
        for r in relaxations
        if r.lowers(least, reach)
    ]
    failed = [prefix(r) + r.description for r in relaxations if r.least_violation is None]
    if named:
        text = (
            "removed one at a time (a daily limit lifted above all the work of the horizon, "
            "a total dropped, a delayed surge available from the first day), these limits "
            "would lower it: " + "; ".join(named)
        )
    else:
        text = (
            "no configured limit lowers it when removed alone (the limits and the regular "
            "capacity bind together)"
        )
    if failed:
        text += "; not computed (solver failure): " + "; ".join(failed)
    return text


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
    unlimited = solve_problem(
        problem,
        settings,
        Objective.TERMINAL_VIOLATION,
        terminal=Terminal.SOFT,
        budget_row=False,
    )
    stages = solve_stages(
        problem,
        settings,
        tie_broken(Objective.TERMINAL_VIOLATION, Objective.WEIGHTED),
        terminal=Terminal.SOFT,
    )
    within, relaxed_optimum, relaxed = stages[0], stages[1], stages[-1]
    reasons: list[str] = []
    least_unlimited = unlimited.value
    least_within = within.value
    spend: float | None = None
    shortfall: float | None = None
    attempts = [unlimited, *stages]
    relaxations: tuple[LimitRelaxation, ...] = ()
    # The plan check's tolerance, so a scenario is never both "out of reach at any cash"
    # and BORDERLINE (its relaxed plan, within the budget, passing the check).
    reach = reach_tolerance(problem)
    borderline = relaxed.solved is not None and check_plan(problem, relaxed.solved.plan).feasible
    reachable = least_unlimited is not None and (least_unlimited <= reach or borderline)
    if least_unlimited is None:  # pragma: no cover - soft targets are always feasible
        reasons.append("the least terminal-target violation could not be computed")
    elif not reachable:
        relaxations = limit_relaxations(problem, settings)
        attempts.extend(r.attempt for r in relaxations)
        reasons.append(
            "the HYPOTHETICAL terminal-backlog target cannot be reached with any amount of "
            f"discretionary cash: the least violation is {_amount(least_unlimited, 1)} "
            f"requests; {_limits_text(problem, relaxations, least_unlimited, reach)} "
            "(DIAGNOSTIC)"
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
            # Below one USD of shortfall, print the spend precisely enough to show it.
            spend_text = _amount(spend, 0) if abs(shortfall) >= 1 else f"{spend:,.9g}"
            reasons.append(
                "under this deterministic demand path and the HYPOTHETICAL prices, no plan "
                f"spending less than {spend_text} USD on overtime and surge reaches the "
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
                f"{_amount(bound, 1)} requests at the end of the horizon, "
                f"{_amount(bound - target, 1)} above the target of {_amount(target, 0)}"
            )
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
        limit_relaxations={f"{r.unit_id}:{r.limit}": r.least_violation for r in relaxations},
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

    The optimal face is built from complementary slackness with the certified optimal
    duals (:func:`face_programme`): a feasible plan is optimal if and only if it sits at
    every bound whose reduced cost is nonzero and keeps every row with a nonzero dual
    tight. Values within ``1e-9`` of their own column's (or row's) scale count as zero, so
    prices tied to within that relative tolerance are treated as tied; every plan used
    below is checked to score the optimum, and if one does not the face is rebuilt with
    exact zero tests only (``rebuilt``).

    ``max_active_distance`` is the largest total distance, over that face, from the bounds
    and tight rows active at the optimal vertex HiGHS returned (rows normalised by their
    largest coefficient; requests or staff hours). A vertex is determined by its active
    constraints, so the distance is zero exactly when no other optimal plan exists; the
    plan is called unique when the distance is at most :data:`UNIQUENESS_THRESHOLD`
    (0.001 requests or staff hours). Any vertex of the face gives the same verdict.
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
    rebuilt: bool = False

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
    handling, budget row and lexicographic restrictions). ``daily`` False skips the
    per-day ranges.

    Every face programme is certified, its plan is checked against the constraints of the
    programme (:func:`check_plan`) and must score the optimum of ``strict``. If a plan does
    not (the zero tests loosened the face), the face is rebuilt with exact zero tests only;
    a solver failure is recorded as a problem instead of aborting the run.
    """
    face = _face_test(problem, settings, strict, daily=daily, zero_threshold=False)
    if face.problems and any("not the optimum" in p for p in face.problems):
        retry = _face_test(problem, settings, strict, daily=daily, zero_threshold=True)
        return replace(retry, rebuilt=True)
    return face


def _face_test(
    problem: PlanningProblem,
    settings: SolverSettings,
    strict: Solved,
    *,
    daily: bool,
    zero_threshold: bool,
) -> OptimalFace:
    problems: list[str] = []
    face, threshold, fixed, tight = face_programme(
        strict.lp, strict.result, zero_threshold=zero_threshold
    )
    optimum = strict.objective_value

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
        if not _keeps_optimum(strict.lp, optimum, result):
            problems.append(
                f"optimal-face plan scores {strict.lp.value(result.x)!r}, "
                f"not the optimum {optimum!r}"
            )
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
    after the step). Slopes are compared with a tolerance that includes the rounding noise
    of two optimal values divided by each step actually taken (``noise`` is that allowance
    for the forward against the backward local secant). Every re-solve is certified;
    ``problems`` lists the failures, and a slope the solver could not compute (local or
    practical) gives the status ``solver_failed``. When the backward step is so small
    (``min(step, rhs)`` for a tiny right-hand side) that the rounding noise divided by it
    exceeds the slope tolerance, the backward secant cannot resolve the slope: it is kept
    but marked ``backward_unresolved`` and left out of the bracket and convexity tests,
    instead of widening the agreement test until any two slopes agree.
    ``stage_fallback`` records a fallback of the least-cash stage of the split (see
    :func:`minimise_on_face`).
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
    stage_fallback: str | None = None
    backward_unresolved: bool = False

    @property
    def locally_unique(self) -> bool:
        if (
            self.backward is None
            or self.backward_unresolved
            or math.isinf(self.backward)
            or math.isinf(self.forward)
        ):
            return False
        return (
            abs(self.forward - self.backward)
            <= _slope_tol(self.forward, self.backward) + self.noise
        )

    @property
    def status(self) -> str:
        slopes = [
            v
            for v in (
                self.forward,
                self.backward,
                self.practical_forward,
                self.practical_backward,
            )
            if v is not None
        ]
        if any(math.isnan(v) for v in slopes):
            return "solver_failed"
        if self.backward is None:
            return "domain_boundary_below"
        if self.backward_unresolved:
            return "backward_unresolved"
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
) -> tuple[float | None, float, LPResult | None, float]:
    """Backward and forward secants of the optimal value, the forward re-solve and the
    backward step taken (``min(step, rhs)``, 0 at a domain boundary)."""
    up_lp = _perturbed(lp, kind, index, step)
    up, up_result = _value_at(up_lp, settings, f"{what} +{step:g}", problems)
    forward = (up - base) / step
    down_step = min(step, rhs)
    if down_step <= 0:
        return None, forward, up_result, 0.0
    down, _ = _value_at(
        _perturbed(lp, kind, index, -down_step), settings, f"{what} -{down_step:g}", problems
    )
    backward = -math.inf if math.isinf(down) else (base - down) / down_step
    return backward, forward, up_result, down_step


def _least_cash(
    lp: LinearProgram,
    result: LPResult,
    settings: SolverSettings,
    what: str,
    problems: list[str],
) -> tuple[float | None, str | None]:
    """Least discretionary cash among the optimal plans of ``lp`` (certified, and kept at
    the optimum by :func:`minimise_on_face`), and the fallback used, if any."""
    cash, _ = objective_vector(lp.problem, lp.layout, Objective.DISCRETIONARY_CASH)
    try:
        restricted, solution, fallback = minimise_on_face(
            lp, result, settings, Objective.DISCRETIONARY_CASH, cash
        )
    except OptimizationError as error:
        problems.append(f"{what}: least cash: {error}")
        return None, "epsilon_cap"
    if solution.x is None:
        problems.append(f"{what}: least-cash programme is {solution.status}")
        return None, fallback
    _certified(restricted, solution, f"{what}: least cash", problems)
    if result.x is not None and not _keeps_optimum(lp, lp.value(result.x), solution):
        problems.append(f"{what}: the least-cash plan does not keep the optimal score")
    return restricted.value(solution.x), fallback


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
    base_fallback: str | None = None
    if base_cash is None:
        base_cash, base_fallback = _least_cash(
            lp, strict.result, settings, "base point", base_problems
        )
    results = []
    for dual in dual_values(lp, strict.result):
        problems = list(base_problems)
        base_problems = []
        what = f"finite difference {dual.constraint} {dual.unit_id or ''} {dual.day}".strip()
        kind, index = _locate(lp, dual)
        step = small_step(dual.rhs)
        backward, forward, _, down = _secants(
            lp, settings, kind, index, dual.rhs, base, step, what, problems
        )
        practical = _step_for(dual.rhs_units, steps)
        p_backward, p_forward, p_result, p_down = _secants(
            lp, settings, kind, index, dual.rhs, base, practical, what, problems
        )
        # Rounding noise of two optimal values divided by each step actually taken (the
        # backward step is min(step, rhs), smaller than the forward one near zero).
        nf, pnf = value_noise / step, value_noise / practical
        nb = value_noise / down if down > 0 else 0.0
        pnb = value_noise / p_down if p_down > 0 else 0.0
        slope = _slope_tol(dual.marginal, forward, *([] if backward is None else [backward]))
        # A backward step cut short by a tiny right-hand side, so small that its noise
        # allowance exceeds the slope tolerance, cannot resolve a slope; such a secant is
        # kept but not tested.
        # Only the cut makes it unresolvable (a full step has the noise nf), and only when the
        # gap between the slopes is within that noise (a larger gap is a resolved kink).
        unresolved = (
            backward is not None
            and math.isfinite(backward)
            and down < step
            and nb > slope + nf
            and abs(forward - backward) <= slope + nf + nb
        )
        b_test = None if unresolved else backward
        pb_test = (
            None
            if p_backward is not None
            and math.isfinite(p_backward)
            and p_down < practical
            and pnb > _slope_tol(p_forward, p_backward) + pnf
            and abs(p_forward - p_backward) <= _slope_tol(p_forward, p_backward) + pnf + pnb
            else p_backward
        )
        bracket = dual.marginal <= forward + slope + nf and (
            b_test is None or dual.marginal >= b_test - slope - nb
        )
        convex = (
            (b_test is None or b_test <= forward + _slope_tol(forward, b_test) + nf + nb)
            and (
                pb_test is None or pb_test <= p_forward + _slope_tol(p_forward, pb_test) + pnf + pnb
            )
            and (
                b_test is None
                or pb_test is None
                or (
                    pb_test <= b_test + slope + pnb + nb and forward <= p_forward + slope + nf + pnf
                )
            )
        )
        # The optimal score never falls when demand rises (proved for d >= 0), so the dual
        # of a balance row with a positive right-hand side is a non-negative subgradient.
        sign = not (dual.constraint == "balance" and dual.rhs > 0 and dual.marginal < -(slope + nf))
        cash = penalty = None
        fallback = base_fallback
        base_fallback = None
        if math.isfinite(p_forward) and base_cash is not None and p_result is not None:
            up = _perturbed(lp, kind, index, practical)
            up_cash, up_fallback = _least_cash(
                up, p_result, settings, f"{what} +{practical:g}", problems
            )
            fallback = fallback or up_fallback
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
                noise=nf + nb,
                problems=tuple(problems),
                stage_fallback=fallback,
                backward_unresolved=bool(unresolved),
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
    fallbacks: tuple[str, ...] = ()


def _policy_plan(problem: PlanningProblem, policy: str, triggers: Mapping[str, float]) -> Plan:
    if policy == FIXED_CAPACITY:
        return fixed_capacity_plan(problem)
    if policy == BACKLOG_TRIGGER:
        return backlog_trigger_plan(problem, triggers)
    raise AssertionError(f"unknown policy {policy!r}")  # pragma: no cover


def stages_under_caps(
    problem: PlanningProblem,
    settings: SolverSettings,
    objectives: Sequence[Objective],
    caps: Sequence[tuple[Objective, float, str]],
    *,
    terminal: Terminal,
    budget_row: bool = True,
    fallback: str,
) -> tuple[Attempt, ...]:
    """:func:`solve_stages` under caps ``objective <= value``, exact first.

    When the capped values are those of a known feasible plan (a policy's, or the strict
    plan's own cash), that plan sits exactly on the caps and HiGHS can call the programme
    infeasible at its tolerance. Only then are the caps loosened to :func:`lex_cap`, and the
    first stage records ``fallback``.
    """
    probe = build_lp(problem, terminal=terminal, budget_row=budget_row)

    def solve(loose: bool) -> tuple[Attempt, ...]:
        rows = [
            cap_row(probe, objective, lex_cap(value) if loose else value, name)
            for objective, value, name in caps
        ]
        return solve_stages(
            problem,
            settings,
            objectives,
            terminal=terminal,
            budget_row=budget_row,
            extra_rows=rows,
        )

    stages = solve(False)
    if stages[0].status is not SolveStatus.INFEASIBLE:
        return stages
    stages = solve(True)
    if not stages[0].optimal:
        return stages
    first = replace(stages[0], stage_fallback=fallback)
    return (first, *(first if a is stages[0] else a for a in stages[1:]))


def evaluate_policy(
    problem: PlanningProblem,
    settings: SolverSettings,
    policy: str,
    triggers: Mapping[str, float],
) -> PolicyOutcome:
    """Score a policy and compute the LP at its discretionary cash and at its backlog.

    Both LP comparisons drop the terminal target (the policies do not plan for it) and
    keep every other constraint, so the policy plan itself is feasible for them: the LP
    can only do at least as well (checked). Each is tie-broken (:data:`TIE_BREAKS`).
    """
    plan = _policy_plan(problem, policy, triggers)
    evaluation = evaluate_plan(problem, plan)
    check = check_plan(problem, plan)
    same_cash_stages = stages_under_caps(
        problem,
        settings,
        tie_broken(Objective.BACKLOG_DAYS, Objective.WEIGHTED),
        [(Objective.DISCRETIONARY_CASH, evaluation.discretionary_cost, "cap_policy_cash")],
        terminal=Terminal.NONE,
        fallback="policy_epsilon_cap",
    )
    same_backlog_stages = stages_under_caps(
        problem,
        settings,
        tie_broken(Objective.DISCRETIONARY_CASH, Objective.WEIGHTED),
        [
            (Objective.BACKLOG_DAYS, evaluation.backlog_request_days, "cap_policy_backlog"),
            (Objective.TERMINAL_BACKLOG, evaluation.terminal_backlog, "cap_policy_terminal"),
        ],
        terminal=Terminal.NONE,
        fallback="policy_epsilon_cap",
    )
    cash_first, same_cash = same_cash_stages[0], same_cash_stages[-1]
    backlog_first, same_backlog = same_backlog_stages[0], same_backlog_stages[-1]
    problems = list(attempt_problems((*same_cash_stages, *same_backlog_stages)))
    other = set(check.constraints_violated()) - {"terminal_backlog"}
    if other:
        problems.append(f"policy {policy} violates {sorted(other)}")
    least_days = cash_first.value
    least_cash = backlog_first.value
    if least_days is None or least_cash is None or not same_cash.optimal:
        problems.append(f"policy {policy}: the same-cash or same-backlog LP is not optimal")
    else:
        # The policy plan is feasible for both programmes, so the LP can only do as well.
        if least_days > evaluation.backlog_request_days + 10 * tolerance(least_days) + (
            LEX_EPS_ABS if cash_first.stage_fallback else 0.0
        ):
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
        fallbacks=stage_fallbacks((*same_cash_stages, *same_backlog_stages)),
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
    #: Whether the tie-broken plan is the only plan with its penalty, cash, terminal
    #: backlog and request-days; if not, the day-by-day schedule differs between such
    #: plans, and the overtime and surge totals may too, within these ranges (over all
    #: such plans).
    unique_after_tie_breaks: bool | None = None
    overtime_hours_range: tuple[float, float] | None = None
    surge_requests_range: tuple[float, float] | None = None

    def totals_open(self) -> bool:
        """Whether the overtime or surge total differs between the tied plans by more than
        the uniqueness threshold (only the daily schedule differs otherwise)."""
        if self.unique_after_tie_breaks is not False:
            return False
        return any(
            span is not None and span[1] - span[0] > UNIQUENESS_THRESHOLD
            for span in (self.overtime_hours_range, self.surge_requests_range)
        )


@dataclass(frozen=True)
class Frontier:
    """Least notional backlog penalty for each cap on discretionary cash (budget dropped)."""

    points: tuple[FrontierPoint, ...]
    cash_for_least_penalty: Mapping[str, float | None]
    monotone_ok: bool
    convex_ok: bool
    on_frontier_check: str
    problems: tuple[str, ...] = ()
    fallbacks: tuple[str, ...] = ()


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
    lexicographic (least penalty, then the tie-breaks of :data:`TIE_BREAKS`), and its
    uniqueness test gives the ranges of overtime and surge over the plans it leaves open.
    The curve must be non-increasing and convex in the cap (the cap is a right-hand side).
    ``None`` when every penalty weight is zero (the penalty is then identically zero).
    """
    if all(u.backlog_penalty == 0 and u.terminal_penalty == 0 for u in problem.units):
        return None
    problems: list[str] = []
    result: list[FrontierPoint] = []
    least_cash: dict[str, float | None] = {}
    monotone = convex = True
    fallbacks: list[str] = []
    for variant, terminal in (("kept", Terminal.HARD), ("dropped", Terminal.NONE)):
        top_stages = solve_stages(
            problem,
            settings,
            tie_broken(Objective.PENALTY),
            terminal=terminal,
            budget_row=False,
        )
        problems.extend(attempt_problems(top_stages))
        fallbacks.extend(stage_fallbacks(top_stages))
        if top_stages[-1].solved is None:
            least_cash[variant] = None
            continue
        top = top_stages[-1].solved.evaluation.discretionary_cost
        least_cash[variant] = top
        caps = [(top * i / (points - 1), "grid") for i in range(points)]
        if problem.discretionary_budget is not None and problem.discretionary_budget < top:
            caps.append((problem.discretionary_budget, "configured_budget"))
        if strict is not None and variant == "kept":
            caps.append((strict.evaluation.discretionary_cost, "strict_plan_cash"))
        curve: list[tuple[float, float]] = []
        for cap, marker in sorted(caps):
            stages = stages_under_caps(
                problem,
                settings,
                tie_broken(Objective.PENALTY),
                [(Objective.DISCRETIONARY_CASH, cap, "cap_frontier_cash")],
                terminal=terminal,
                budget_row=False,
                fallback="frontier_epsilon_cap",
            )
            first, last = stages[0], stages[-1]
            problems.extend(attempt_problems(stages))
            fallbacks.extend(stage_fallbacks(stages))
            if first.solved is None or last.solved is None:
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
            evaluation = last.solved.evaluation
            penalty = first.solved.objective_value
            curve.append((cap, penalty))
            face = optimal_face(problem, settings, last.solved, daily=False)
            problems.extend(face.problems)
            if face.rebuilt:
                fallbacks.append("face_rebuilt")
            spans = {r.quantity: (r.low, r.high) for r in face.ranges}
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
                    unique_after_tie_breaks=face.unique,
                    overtime_hours_range=spans.get("overtime_hours"),
                    surge_requests_range=spans.get("surge_requests"),
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
    return Frontier(
        tuple(result), least_cash, monotone, convex, check, tuple(problems), tuple(fallbacks)
    )


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
    tie-broken optimal plan, its optimal face (uniqueness and ranges), or the least
    terminal-target violation within the budget when the strict model is infeasible.
    ``fallbacks`` lists the stage fallbacks used (see :attr:`Attempt.stage_fallback`)."""

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
    fallbacks: tuple[str, ...] = ()

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

    The score is the planning optimum; the other quantities come from the tie-broken
    optimal plan (the properties hold for every optimal plan). Every programme's check
    problems are kept on the point.
    """
    points = []
    for value in values:
        changed = apply_sweep(problem, parameter, value)
        canonical = solve_canonical(changed, settings)
        attempt = canonical.optimum
        problems = list(canonical.problems)
        fallbacks = list(canonical.fallbacks)
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
            if face.rebuilt:
                fallbacks.append("face_rebuilt")
        if canonical.plan.solved is not None:
            binding = format_binding(binding_constraints(changed, canonical.plan.solved.plan))
        elif attempt.solved is None:
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
                tuple(fallbacks),
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
    reported tie-broken optimal plan. ``without_target`` / ``without_target_plan`` are the
    same without the HYPOTHETICAL terminal target; ``target_changes_score`` says whether
    dropping the target changes the optimal score at all, and ``target_changes_plan``
    whether it changes the reported (tie-broken) plan: the target can leave the score
    unchanged and still exclude cheaper equally scored plans. ``duals`` carry the slack and
    binding state of the reported plan (the duals are optimal for every optimal plan).
    ``fallbacks`` lists every stage fallback used (see :attr:`Attempt.stage_fallback`).
    ``tie_broken_face`` tests the reported plan after the tie-breaks.
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
    target_changes_plan: bool | None = None
    fallbacks: tuple[str, ...] = ()
    #: Uniqueness test of the reported plan itself, on the optimal face of its last
    #: tie-break stage: whether another plan has the same score, cash, terminal backlog and
    #: request-days (its overtime/surge split or daily schedule would then differ).
    tie_broken_face: OptimalFace | None = None

    @property
    def feasibility(self) -> str:
        return STRICT if self.strict.optimal else RELAXED_TERMINAL_TARGET

    @property
    def reported(self) -> Attempt:
        """The tie-broken strict plan, or the relaxed plan when the strict model is
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
    fallbacks = list(strict_solve.fallbacks) + list(free_solve.fallbacks)
    diagnosis = face = relaxed_face = None
    duals: tuple[DualValue, ...] = ()
    differences: tuple[FiniteDifference, ...] = ()
    changes: bool | None = None
    changes_plan: bool | None = None
    if strict.solved is not None and free_solve.optimum.solved is not None:
        mine = strict.solved.objective_value
        other = free_solve.optimum.solved.objective_value
        changes = abs(mine - other) > 10 * tolerance(mine, other) + LEX_EPS_ABS
        free = free_solve.plan.solved
        if changes:
            changes_plan = True
        elif free is not None:
            # Same score: the no-target tie-broken plan is also the strict one exactly when
            # it meets the target (it is then minimal over a superset of the strict face).
            violation = terminal_violation(problem, free.plan)
            changes_plan = violation > tolerance(violation)
    if strict.solved is not None:
        if strict_plan.solved is not None:
            duals = dual_values(
                strict.solved.lp, replace(strict.solved.result, x=strict_plan.solved.x)
            )
        else:
            duals = dual_values(strict.solved.lp, strict.solved.result)
        face = optimal_face(problem, settings, strict.solved)
        problems.extend(face.problems)
        if face.rebuilt:
            fallbacks.append("face_rebuilt")
        if shadow_steps is not None:
            base_cash = (
                None
                if strict_plan.solved is None
                else strict_plan.solved.evaluation.discretionary_cost
            )
            differences = finite_differences(strict.solved, settings, shadow_steps, base_cash)
            for item in differences:
                problems.extend(item.problems)
                if item.stage_fallback is not None:
                    fallbacks.append(item.stage_fallback)
                if not (item.bracket_ok and item.convex_ok and item.sign_ok):
                    problems.append(
                        f"shadow price {item.dual.key()} outside its finite-difference bracket"
                    )
    else:
        diagnosis = diagnose(problem, settings)
        problems.extend(attempt_problems(diagnosis.attempts))
        fallbacks.extend(stage_fallbacks(diagnosis.attempts))
        problems.extend(confirm_infeasible(diagnosis.least_violation_within_budget))
        if not diagnosis.relaxed.optimal:
            problems.append("the relaxed plan could not be computed")
        elif diagnosis.relaxed_optimum is not None and diagnosis.relaxed_optimum.solved:
            relaxed_face = optimal_face(problem, settings, diagnosis.relaxed_optimum.solved)
            problems.extend(relaxed_face.problems)
            if relaxed_face.rebuilt:
                fallbacks.append("face_rebuilt")
    policies = tuple(
        evaluate_policy(problem, settings, policy, triggers)
        for policy in (FIXED_CAPACITY, BACKLOG_TRIGGER)
    )
    for outcome in policies:
        problems.extend(outcome.problems)
        fallbacks.extend(outcome.fallbacks)
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
    last = strict_plan.solved if strict.solved is not None else None
    if strict.solved is None and diagnosis is not None:
        last = diagnosis.relaxed.solved
    tie_broken_face = None
    if last is not None:
        tie_broken_face = optimal_face(problem, settings, last, daily=False)
        problems.extend(tie_broken_face.problems)
        if tie_broken_face.rebuilt:
            fallbacks.append("face_rebuilt")
    curve = None
    if frontier_points is not None:
        curve = frontier(problem, settings, frontier_points, strict_plan.solved)
        if curve is not None:
            problems.extend(curve.problems)
            fallbacks.extend(curve.fallbacks)
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
        target_changes_plan=changes_plan,
        fallbacks=tuple(fallbacks),
        tie_broken_face=tie_broken_face,
    )
