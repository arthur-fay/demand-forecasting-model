"""The M4 planning model as a linear programme, solved with HiGHS through SciPy (M4).

:func:`build_lp` turns a :class:`~atlas.planning.PlanningProblem` into the matrices of

    minimise  c @ v + constant
    subject to  A_ub @ v <= b_ub,  A_eq @ v = b_eq,  lower <= v <= upper

with four blocks of variables per unit and day (requests served ``x``, overtime hours
``o``, surge requests ``s``, end-of-day backlog ``b``; see :mod:`atlas.planning` for the
model and the units). Every row and bound carries a name, its unit and the unit of its
shadow price, so dual values are never reported without units.

:func:`solve_lp` calls ``scipy.optimize.linprog`` with the HiGHS dual simplex
(``highs-ds``). Nothing the solver says is taken on trust:

* :func:`certify` re-checks optimality from the returned primal *and dual* values: primal
  feasibility, dual sign conditions, stationarity ``c - A_ub' y_ub - A_eq' y_eq - y_lo -
  y_up = 0`` and a zero duality gap ``c' v = b_ub' y_ub + b_eq' y_eq + lower' y_lo +
  upper' y_up``. Together these prove optimality (within the stated tolerances) without
  trusting the solver's status;
* :func:`to_plan` maps the solution to a :class:`~atlas.planning.Plan`, which is then
  checked by :func:`atlas.planning.check_plan` without the LP matrices.

No value that reaches a result file goes through BLAS: dot products use :func:`exact_dot`
(correctly rounded ``math.fsum`` of element products), so the same solver output gives the
same bytes on every machine. HiGHS itself does not call BLAS.

SciPy's marginals are partial derivatives of the optimal objective with respect to the
right-hand side (``<=`` rows: ``<= 0``; lower bounds: ``>= 0``; upper bounds: ``<= 0``).
They are local: valid for small changes of one right-hand side, and not unique when the
optimum is degenerate (:mod:`atlas.decision_analysis` brackets them with finite
differences).
"""

from __future__ import annotations

import importlib
import math
import re
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Final, Literal

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import OptimizeResult, linprog

from atlas.errors import AtlasError
from atlas.planning import (
    Plan,
    PlanningProblem,
    UnitPlan,
    tolerance,
)

FloatArray = NDArray[np.float64]

BLOCKS: Final = ("served", "overtime_hours", "surge", "backlog")


class OptimizationError(AtlasError):
    """The solver failed in a way that is not a property of the model (numerics, limits)."""


class Objective(StrEnum):
    """What a linear programme minimises (and the unit of its value).

    ``WEIGHTED`` is the planning objective (the penalised score); the others define the
    diagnostic and tradeoff programmes. ``CUSTOM`` takes explicit coefficients (ranges
    and the uniqueness test over the optimal face).
    """

    WEIGHTED = "weighted"
    TERMINAL_BACKLOG = "terminal_backlog"
    TERMINAL_VIOLATION = "terminal_violation"
    DISCRETIONARY_CASH = "discretionary_cash"
    BACKLOG_DAYS = "backlog_days"
    PENALTY = "penalty"
    CUSTOM = "custom"


OBJECTIVE_UNITS: Final = {
    Objective.WEIGHTED: "usd_equivalent",
    Objective.TERMINAL_BACKLOG: "requests",
    Objective.TERMINAL_VIOLATION: "requests",
    Objective.DISCRETIONARY_CASH: "usd",
    Objective.BACKLOG_DAYS: "request_days",
    Objective.PENALTY: "usd_equivalent_notional",
    Objective.CUSTOM: "mixed",
}


class Terminal(StrEnum):
    """How the terminal-backlog targets enter a programme.

    ``HARD``: ``b[u,T] <= target[u]`` (the planning model). ``SOFT``: one violation
    variable ``v[u] >= 0`` per unit with a target and ``b[u,T] - v[u] <= target[u]``
    (diagnostic programmes). ``NONE``: no terminal row.
    """

    HARD = "hard"
    SOFT = "soft"
    NONE = "none"


#: Unit of the right-hand side and of the shadow price of each row for the planning
#: objective. ``usd_equivalent`` is the unit of the penalised score (discretionary cash
#: plus notional backlog penalties), not of cash: a shadow price is never a wage, a price
#: or a value of staff.
ROW_UNITS: Final = {
    "balance": ("requests", "usd_equivalent_per_request"),
    "capacity": ("requests_per_day", "usd_equivalent_per_request_of_unpaid_capacity"),
    "overtime_total_limit": ("staff_hours", "usd_equivalent_per_overtime_hour_allowed"),
    "surge_total_limit": ("requests", "usd_equivalent_per_surge_request_allowed"),
    "terminal_backlog": ("requests", "usd_equivalent_per_request"),
    "budget": ("usd", "usd_equivalent_per_usd"),
    "overtime_daily_limit": (
        "staff_hours_per_day",
        "usd_equivalent_per_overtime_hour_allowed",
    ),
    "surge_daily_limit": ("requests_per_day", "usd_equivalent_per_surge_request_allowed"),
}


class SolveStatus(StrEnum):
    """``FAILED``: the solver gave no verdict (an :class:`OptimizationError` caught by an
    analysis fallback, see :func:`failed_result`); never a statement about the model."""

    OPTIMAL = "optimal"
    INFEASIBLE = "infeasible"
    FAILED = "solver_failed"


@dataclass(frozen=True)
class Layout:
    """Position of every variable: unit-major, then block, then day; then named extras
    (the terminal violation variables of a soft-target programme)."""

    unit_ids: tuple[str, ...]
    horizon: int
    extras: tuple[str, ...] = ()

    @property
    def plan_size(self) -> int:
        return len(self.unit_ids) * len(BLOCKS) * self.horizon

    @property
    def size(self) -> int:
        return self.plan_size + len(self.extras)

    def index(self, unit: int, block: str, day: int) -> int:
        return (unit * len(BLOCKS) + BLOCKS.index(block)) * self.horizon + day

    def extra_index(self, name: str) -> int:
        return self.plan_size + self.extras.index(name)

    def block(self, values: FloatArray, unit: int, block: str) -> FloatArray:
        start = self.index(unit, block, 0)
        return values[start : start + self.horizon]


@dataclass(frozen=True)
class Row:
    """A named constraint row (``day`` is 0-based; ``None`` for a horizon-wide row)."""

    constraint: str
    unit_id: str | None
    day: int | None


@dataclass(frozen=True)
class ExtraRow:
    """An additional ``coefficients @ v <= rhs`` row (frontier and alternative-optima caps)."""

    row: Row
    coefficients: FloatArray
    rhs: float


@dataclass(frozen=True)
class LinearProgram:
    problem: PlanningProblem
    layout: Layout
    objective: Objective
    c: FloatArray
    constant: float
    a_ub: FloatArray
    b_ub: FloatArray
    ub_rows: tuple[Row, ...]
    a_eq: FloatArray
    b_eq: FloatArray
    eq_rows: tuple[Row, ...]
    lower: FloatArray
    upper: FloatArray

    @property
    def objective_units(self) -> str:
        return OBJECTIVE_UNITS[self.objective]

    def value(self, v: FloatArray) -> float:
        return math.fsum([*(self.c * v).tolist(), self.constant])


def objective_vector(
    problem: PlanningProblem, layout: Layout, objective: Objective
) -> tuple[FloatArray, float]:
    """Coefficients and constant of ``objective`` (see :data:`OBJECTIVE_UNITS`).

    The day-T backlog carries both the daily penalty ``w`` and the terminal penalty
    ``w_T`` (it waits overnight like every other day's backlog, and is left at the end).
    """
    c = np.zeros(layout.size)
    constant = 0.0
    last = problem.horizon - 1
    if objective is Objective.TERMINAL_VIOLATION:
        for name in layout.extras:
            c[layout.extra_index(name)] = 1.0
        return c, constant
    for k, unit in enumerate(problem.units):
        for day in range(problem.horizon):
            o = layout.index(k, "overtime_hours", day)
            s = layout.index(k, "surge", day)
            b = layout.index(k, "backlog", day)
            if objective in (Objective.WEIGHTED, Objective.DISCRETIONARY_CASH):
                c[o] = unit.overtime_wage
                c[s] = unit.surge_price
            if objective in (Objective.WEIGHTED, Objective.PENALTY):
                c[b] = unit.backlog_penalty + (unit.terminal_penalty if day == last else 0.0)
            elif objective is Objective.BACKLOG_DAYS or (
                objective is Objective.TERMINAL_BACKLOG and day == last
            ):
                c[b] = 1.0
    return c, constant


def violation_name(unit_id: str) -> str:
    return f"terminal_violation[{unit_id}]"


def build_lp(
    problem: PlanningProblem,
    objective: Objective = Objective.WEIGHTED,
    *,
    terminal: Terminal = Terminal.HARD,
    budget_row: bool = True,
    extra_rows: Sequence[ExtraRow] = (),
    coefficients: FloatArray | None = None,
) -> LinearProgram:
    """The matrices of the planning model (see the module docstring).

    ``terminal`` other than ``HARD`` and ``budget_row`` False change the terminal targets /
    drop the budget (diagnostic programmes only); limits that are ``None`` in the problem
    have no row. ``coefficients`` are required for, and only for, ``Objective.CUSTOM``.
    """
    targets = [u for u in problem.units if u.terminal_backlog_max is not None]
    extras = tuple(violation_name(u.unit_id) for u in targets) if terminal is Terminal.SOFT else ()
    if objective is Objective.TERMINAL_VIOLATION and not extras:
        raise OptimizationError("a terminal-violation objective needs soft terminal targets")
    layout = Layout(tuple(u.unit_id for u in problem.units), problem.horizon, extras)
    n = layout.size
    if (coefficients is None) != (objective is not Objective.CUSTOM):
        raise OptimizationError("explicit coefficients go with Objective.CUSTOM only")
    if coefficients is not None:
        if coefficients.shape != (n,):
            raise OptimizationError("custom objective coefficients have the wrong length")
        c, constant = np.array(coefficients, dtype="float64"), 0.0
    else:
        c, constant = objective_vector(problem, layout, objective)
    lower = np.zeros(n)
    upper = np.full(n, np.inf)
    eq_coefficients: list[FloatArray] = []
    eq_rhs: list[float] = []
    eq_rows: list[Row] = []
    ub_coefficients: list[FloatArray] = []
    ub_rhs: list[float] = []
    ub_rows: list[Row] = []

    def add(rows: list[Row], coefficients: list[FloatArray], rhs: list[float]) -> Any:
        def adder(row: Row, entries: Mapping[int, float], value: float) -> None:
            vector = np.zeros(n)
            for column, coefficient in entries.items():
                vector[column] = coefficient
            rows.append(row)
            coefficients.append(vector)
            rhs.append(value)

        return adder

    add_eq = add(eq_rows, eq_coefficients, eq_rhs)
    add_ub = add(ub_rows, ub_coefficients, ub_rhs)
    cash: dict[int, float] = {}
    for k, unit in enumerate(problem.units):
        uid = unit.unit_id
        for day in range(problem.horizon):
            x = layout.index(k, "served", day)
            o = layout.index(k, "overtime_hours", day)
            s = layout.index(k, "surge", day)
            b = layout.index(k, "backlog", day)
            upper[o] = unit.overtime_max_hours[day]
            upper[s] = unit.surge_max[day]
            # b[t] - b[t-1] + x[t] = d[t]   (b[0] is the initial backlog, moved to the rhs)
            entries = {b: 1.0, x: 1.0}
            if day > 0:
                entries[layout.index(k, "backlog", day - 1)] = -1.0
            initial = unit.initial_backlog if day == 0 else 0.0
            add_eq(Row("balance", uid, day), entries, unit.demand[day] + initial)
            # x[t] - q o[t] - s[t] <= p H[t]
            add_ub(
                Row("capacity", uid, day),
                {x: 1.0, o: -unit.overtime_productivity, s: -1.0},
                unit.regular_capacity(day),
            )
            cash[o] = unit.overtime_wage
            cash[s] = unit.surge_price
        if unit.overtime_total_hours is not None:
            add_ub(
                Row("overtime_total_limit", uid, None),
                {layout.index(k, "overtime_hours", d): 1.0 for d in range(problem.horizon)},
                unit.overtime_total_hours,
            )
        if unit.surge_total is not None:
            add_ub(
                Row("surge_total_limit", uid, None),
                {layout.index(k, "surge", d): 1.0 for d in range(problem.horizon)},
                unit.surge_total,
            )
        if terminal is not Terminal.NONE and unit.terminal_backlog_max is not None:
            entries = {layout.index(k, "backlog", problem.horizon - 1): 1.0}
            if terminal is Terminal.SOFT:
                entries[layout.extra_index(violation_name(uid))] = -1.0
            add_ub(Row("terminal_backlog", uid, None), entries, unit.terminal_backlog_max)
    if budget_row and problem.discretionary_budget is not None:
        add_ub(Row("budget", None, None), cash, problem.discretionary_budget)
    for extra in extra_rows:
        if extra.coefficients.shape != (n,):
            raise OptimizationError("an extra row has the wrong number of coefficients")
        ub_rows.append(extra.row)
        ub_coefficients.append(np.asarray(extra.coefficients, dtype="float64"))
        ub_rhs.append(extra.rhs)
    return LinearProgram(
        problem=problem,
        layout=layout,
        objective=objective,
        c=c,
        constant=constant,
        a_ub=np.vstack(ub_coefficients) if ub_coefficients else np.zeros((0, n)),
        b_ub=np.asarray(ub_rhs, dtype="float64"),
        ub_rows=tuple(ub_rows),
        a_eq=np.vstack(eq_coefficients),
        b_eq=np.asarray(eq_rhs, dtype="float64"),
        eq_rows=tuple(eq_rows),
        lower=lower,
        upper=upper,
    )


@dataclass(frozen=True)
class SolverSettings:
    """HiGHS options passed to ``scipy.optimize.linprog`` (recorded in every manifest).

    No time limit is set (it would make results depend on machine load); the iteration
    limit is the deterministic guard. Only these options are passed, and any
    ``OptimizeWarning`` (an unrecognised or invalid option silently replaced by a HiGHS
    default) is turned into an :class:`OptimizationError`.
    """

    method: Literal["highs-ds"] = "highs-ds"
    presolve: bool = True
    primal_feasibility_tolerance: float = 1e-9
    dual_feasibility_tolerance: float = 1e-9
    maxiter: int = 10_000

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "presolve": self.presolve,
            "primal_feasibility_tolerance": self.primal_feasibility_tolerance,
            "dual_feasibility_tolerance": self.dual_feasibility_tolerance,
            "maxiter": self.maxiter,
        }


@dataclass(frozen=True)
class LPResult:
    """What the solver returned (``x`` and marginals are ``None`` unless optimal).

    ``highs_status`` is the HiGHS model status read from the solver message (7 optimal,
    8 infeasible, ...); ``retried`` is True when an inconclusive status was re-solved
    without presolve, or when presolve's infeasible verdict on a programme known to contain
    a feasible point was overturned by a re-solve without presolve.
    """

    status: SolveStatus
    code: int
    message: str
    iterations: int
    highs_status: int | None = None
    retried: bool = False
    x: FloatArray | None = None
    objective: float | None = None
    ub_marginals: FloatArray | None = None
    eq_marginals: FloatArray | None = None
    lower_marginals: FloatArray | None = None
    upper_marginals: FloatArray | None = None


#: HiGHS model statuses (``HighsModelStatus``) that ATLAS interprets.
HIGHS_OPTIMAL: Final = 7
HIGHS_INFEASIBLE: Final = 8
HIGHS_UNBOUNDED_OR_INFEASIBLE: Final = 9
HIGHS_INCONCLUSIVE: Final = (9, 10, 15)
#: Largest magnitude accepted in a programme (larger inputs are a configuration error).
MAX_MAGNITUDE: Final = 1e9
#: Nonzero matrix coefficients at or below HiGHS's ``small_matrix_value`` are dropped by the
#: solver, which then solves a different programme; they are rejected.
MIN_MAGNITUDE: Final = 1e-9
#: Bound on derived right-hand sides (sums of inputs over the horizon); HiGHS treats 1e20
#: as infinite.
MAX_DERIVED_MAGNITUDE: Final = 1e15
_HIGHS_STATUS: Final = re.compile(r"HiGHS Status (\d+)")


def _array(value: Any) -> FloatArray:
    return np.asarray(value, dtype="float64")


def exact_dot(a: FloatArray, b: FloatArray) -> float:
    """Correctly rounded dot product, independent of BLAS kernels and SIMD paths.

    Each element product is one IEEE multiplication; ``math.fsum`` then rounds the exact
    sum once. numpy's ``@`` and reductions pick CPU-specific kernels whose last bits
    differ between machines.
    """
    return math.fsum((a * b).tolist())


def exact_matvec(matrix: FloatArray, v: FloatArray) -> FloatArray:
    """``matrix @ v`` row by row with :func:`exact_dot`."""
    return np.array([exact_dot(row, v) for row in matrix], dtype="float64")


def is_derived_row(row: Row) -> bool:
    """Analysis rows (lexicographic, frontier and policy caps, optimal-face rows)."""
    return row.constraint.startswith(("cap_", "tight_"))


def _check_magnitudes(lp: LinearProgram) -> None:
    """Reject non-finite data, planning data of magnitude ``MAX_MAGNITUDE`` or more, nonzero
    matrix coefficients of ``MIN_MAGNITUDE`` or less, and derived right-hand sides of
    ``MAX_DERIVED_MAGNITUDE`` or more.

    Derived rows (caps on request-days or penalties, for example) aggregate the inputs over
    the horizon, so they may exceed the input bound while every input is within it.
    """
    for name, matrix in (("A_ub", lp.a_ub), ("A_eq", lp.a_eq)):
        magnitudes = np.abs(matrix[np.isfinite(matrix)])
        tiny = magnitudes[(magnitudes > 0) & (magnitudes <= MIN_MAGNITUDE)]
        if tiny.size:
            raise OptimizationError(
                f"{name} has a nonzero coefficient of magnitude <= {MIN_MAGNITUDE:g} "
                f"({float(tiny.min()):g}), which HiGHS drops; rescale the inputs or set "
                "the price or productivity to 0"
            )
    finite_upper = lp.upper[np.isfinite(lp.upper)]
    derived = np.array([is_derived_row(row) for row in lp.ub_rows], dtype=bool)
    for name, values, limit in (
        ("objective", lp.c, MAX_MAGNITUDE),
        ("A_ub", lp.a_ub, MAX_MAGNITUDE),
        ("b_ub", lp.b_ub[~derived] if derived.size else lp.b_ub, MAX_MAGNITUDE),
        ("derived b_ub", lp.b_ub[derived] if derived.size else lp.b_ub[:0], MAX_DERIVED_MAGNITUDE),
        ("A_eq", lp.a_eq, MAX_MAGNITUDE),
        ("b_eq", lp.b_eq, MAX_MAGNITUDE),
        ("lower bounds", lp.lower, MAX_MAGNITUDE),
        ("upper bounds", finite_upper, MAX_MAGNITUDE),
    ):
        if values.size and (
            not np.all(np.isfinite(values)) or float(np.max(np.abs(values))) >= limit
        ):
            raise OptimizationError(
                f"{name} has a non-finite value or one of magnitude >= {limit:g}; "
                "rescale the inputs"
            )


def _linprog(lp: LinearProgram, settings: SolverSettings, presolve: bool) -> OptimizeResult:
    bounds = [
        (float(low), None if math.isinf(high) else float(high))
        for low, high in zip(lp.lower, lp.upper, strict=True)
    ]
    has_ub = lp.a_ub.shape[0] > 0
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result: OptimizeResult = linprog(
            lp.c,
            A_ub=lp.a_ub if has_ub else None,
            b_ub=lp.b_ub if has_ub else None,
            A_eq=lp.a_eq,
            b_eq=lp.b_eq,
            bounds=bounds,
            method=settings.method,
            options={
                "presolve": presolve,
                "primal_feasibility_tolerance": settings.primal_feasibility_tolerance,
                "dual_feasibility_tolerance": settings.dual_feasibility_tolerance,
                "maxiter": settings.maxiter,
                "disp": False,
            },
        )
    if caught:
        raise OptimizationError(
            "HiGHS warned (an option may have been replaced by a default): "
            + "; ".join(str(w.message) for w in caught)
        )
    return result


def _highs_status(result: OptimizeResult) -> int | None:
    match = _HIGHS_STATUS.search(str(result.message))
    return int(match.group(1)) if match else None


#: Programmes whose right-hand sides and finite bounds are all at most this (in absolute
#: value) are solved as built; larger ones are scaled exactly (:func:`scale_factor`). Up to
#: ``2**21`` (about 2.1e6) HiGHS's absolute tolerance ``1e-9`` still spans at least two
#: units in the last place (``ulp(2**21)`` is about 4.7e-10); above it, it does not. The
#: largest value of any programme of the reference run is about 1.09e6.
SCALE_THRESHOLD: Final = 2.0**21
#: A scaled programme's largest value is brought into ``[2**(SCALE_EXPONENT - 1),
#: 2**SCALE_EXPONENT)``, the magnitudes of the reference run's programmes.
SCALE_EXPONENT: Final = 21


def scale_factor(lp: LinearProgram) -> float:
    """The power of two ``K`` the right-hand sides and finite bounds of ``lp`` are divided by.

    HiGHS's feasibility tolerances (``1e-9``) are absolute. On values of ``1e8``-``1e9`` they
    are below the rounding error of one floating-point operation, so presolve declares
    feasible programmes infeasible and the simplex cannot clean up the unscaled solution
    (status 15). Dividing every right-hand side and finite bound by a power of two is exact
    in floating point and maps the programme onto an equivalent one: ``x = K x'``, the
    objective is ``K`` times the scaled one, and every dual (row, equality and bound
    marginal) is unchanged, since stationarity and complementary slackness do not involve
    the right-hand sides. ``K = 1`` when the largest value is at most
    :data:`SCALE_THRESHOLD`; otherwise the smallest power of two that brings it below
    ``2**SCALE_EXPONENT``. It depends only on the programme's data, so it is deterministic.
    """
    values = [
        np.abs(lp.b_ub),
        np.abs(lp.b_eq),
        np.abs(lp.lower[np.isfinite(lp.lower)]),
        np.abs(lp.upper[np.isfinite(lp.upper)]),
    ]
    largest = max((float(np.max(v)) for v in values if v.size), default=0.0)
    if not math.isfinite(largest) or largest <= SCALE_THRESHOLD:
        return 1.0
    _, exponent = math.frexp(largest)  # 2**(exponent - 1) <= largest < 2**exponent
    return math.ldexp(1.0, max(0, exponent - SCALE_EXPONENT))


def _scaled(lp: LinearProgram, factor: float) -> LinearProgram:
    """``lp`` with every right-hand side and bound divided by ``factor`` (a power of two)."""
    if factor == 1.0:
        return lp
    return replace(
        lp,
        b_ub=lp.b_ub / factor,
        b_eq=lp.b_eq / factor,
        lower=lp.lower / factor,
        upper=lp.upper / factor,
    )


def solve_lp(lp: LinearProgram, settings: SolverSettings) -> LPResult:
    """Solve ``lp`` with HiGHS; a solver failure (limits, model error) raises.

    The programme is first scaled exactly by :func:`scale_factor` (``K = 1`` for every
    programme of ordinary magnitude); the solution is mapped back (``x = K x'``, duals
    unchanged) and everything downstream (certificate, plan, values) uses the original
    programme. A scaled solution is accepted only if its certificate holds on the original
    programme (HiGHS's absolute tolerances act as ``K x 1e-9`` there); if it does not, or if
    HiGHS fails on the scaled programme, the programme is solved unscaled and the first
    certified result is used.

    The outcome is classified on the HiGHS model status, not only on SciPy's integer
    (SciPy maps a HiGHS model error to its "infeasible" code 2, and inconclusive outcomes
    to code 4): 7 is optimal, 8 infeasible; 9 (unbounded or infeasible), 10 (unbounded)
    and 15 (unknown) are re-solved once without presolve; after that 9 counts as
    infeasible, because every programme built here is bounded on its feasible set
    (``x, b <= b[0] + sum d``, ``o`` and ``s`` by their daily limits, violations by the
    backlog). Anything else raises :class:`OptimizationError`. Presolve decides
    feasibility at HiGHS's absolute tolerance, so its infeasible verdict is confirmed by a
    re-solve without presolve: a certified optimum of that re-solve overturns it
    (``retried``); otherwise presolve's result is returned unchanged.
    """
    _check_magnitudes(lp)
    factor = scale_factor(lp)
    try:
        result = _solve_scaled(lp, settings, factor)
    except OptimizationError:
        if factor == 1.0:
            raise
        return _solve_scaled(lp, settings, 1.0)
    if factor == 1.0 or result.status is not SolveStatus.OPTIMAL or certify(lp, result).holds:
        return result
    try:
        unscaled = _solve_scaled(lp, settings, 1.0)
    except OptimizationError:
        return result
    if unscaled.status is SolveStatus.OPTIMAL and certify(lp, unscaled).holds:
        return replace(unscaled, retried=True)
    return result


def _solve_scaled(lp: LinearProgram, settings: SolverSettings, factor: float) -> LPResult:
    """One solve of ``lp`` divided by ``factor`` (see :func:`solve_lp`)."""
    scaled = _scaled(lp, factor)
    result = _linprog(scaled, settings, settings.presolve)
    highs = _highs_status(result)
    retried = False
    if highs in HIGHS_INCONCLUSIVE and settings.presolve:
        result = _linprog(scaled, settings, presolve=False)
        highs = _highs_status(result)
        retried = True
    status = _classify(result, highs)
    if status is SolveStatus.INFEASIBLE:
        infeasible = LPResult(
            status,
            int(result.status),
            str(result.message),
            int(getattr(result, "nit", 0) or 0),
            highs,
            retried,
        )
        if retried or not settings.presolve:
            return infeasible
        again = _linprog(scaled, settings, presolve=False)
        again_highs = _highs_status(again)
        try:
            again_status = _classify(again, again_highs)
        except OptimizationError:
            return infeasible
        if again_status is not SolveStatus.OPTIMAL:
            return infeasible
        candidate = _solution(lp, again, again_highs, factor, retried=True)
        return candidate if certify(lp, candidate).holds else infeasible
    return _solution(lp, result, highs, factor, retried=retried)


def _classify(result: OptimizeResult, highs: int | None) -> SolveStatus:
    code = int(result.status)
    if highs == HIGHS_OPTIMAL and code == 0:
        return SolveStatus.OPTIMAL
    if highs in (HIGHS_INFEASIBLE, HIGHS_UNBOUNDED_OR_INFEASIBLE):
        return SolveStatus.INFEASIBLE
    raise OptimizationError(f"HiGHS did not finish (status {code}): {result.message!s}")


def _solution(
    lp: LinearProgram, result: OptimizeResult, highs: int | None, factor: float, *, retried: bool
) -> LPResult:
    """The optimal ``result`` of ``lp`` divided by ``factor``, mapped back to ``lp``."""
    has_ub = lp.a_ub.shape[0] > 0
    x = _array(result.x) * factor
    return LPResult(
        SolveStatus.OPTIMAL,
        int(result.status),
        str(result.message),
        int(getattr(result, "nit", 0) or 0),
        highs,
        retried,
        x=x,
        objective=lp.value(x),
        ub_marginals=_array(result.ineqlin.marginals) if has_ub else np.zeros(0),
        eq_marginals=_array(result.eqlin.marginals),
        lower_marginals=_array(result.lower.marginals),
        upper_marginals=_array(result.upper.marginals),
    )


def failed_result(error: OptimizationError) -> LPResult:
    """The result of a programme the solver could not solve (status ``FAILED``, no solution),
    for analysis fallbacks that record a solver error instead of aborting the run."""
    return LPResult(SolveStatus.FAILED, -1, str(error), 0)


#: Tolerances of the optimality certificate: absolute and relative parts for rows and
#: bounds, relative part for dual signs, stationarity and the duality gap. Far below any
#: meaningful error; the margin measured on the reference run is in docs/OPTIMIZATION.md.
CERT_ABS: Final = 1e-7
CERT_REL: Final = 1e-9


@dataclass(frozen=True)
class Certificate:
    """Primal feasibility, dual feasibility and the duality gap of an LP solution.

    Each entry is the largest violation *in units of its tolerance* (see
    :data:`CERT_ABS`, :data:`CERT_REL`): 1.0 is exactly at the tolerance. ``holds`` means
    every entry is at most 1, which proves the solution optimal within those tolerances
    without trusting the solver's status.
    """

    primal: float
    dual_sign: float
    stationarity: float
    duality_gap: float
    primal_objective: float
    dual_objective: float
    holds: bool

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def _ratio(excess: FloatArray, allowed: FloatArray | float) -> float:
    if excess.size == 0:
        return 0.0
    return float(np.max(np.maximum(excess, 0.0) / allowed))


def certify(lp: LinearProgram, result: LPResult) -> Certificate:
    """Check optimality of ``result`` from its primal and dual values (module docstring)."""
    if (
        result.x is None
        or result.ub_marginals is None
        or result.eq_marginals is None
        or result.lower_marginals is None
        or result.upper_marginals is None
    ):
        raise OptimizationError("only an optimal solution can be certified")
    v = result.x
    y_ub, y_eq = result.ub_marginals, result.eq_marginals
    y_lo, y_up = result.lower_marginals, result.upper_marginals
    finite_up = np.isfinite(lp.upper)
    upper = np.where(finite_up, lp.upper, 0.0)

    def row_allowed(a: FloatArray, b: FloatArray) -> FloatArray:
        allowed: FloatArray = CERT_ABS + CERT_REL * np.maximum(
            np.abs(b), exact_matvec(np.abs(a), np.abs(v))
        )
        return allowed

    primal = max(
        _ratio(exact_matvec(lp.a_ub, v) - lp.b_ub, row_allowed(lp.a_ub, lp.b_ub)),
        _ratio(np.abs(exact_matvec(lp.a_eq, v) - lp.b_eq), row_allowed(lp.a_eq, lp.b_eq)),
        _ratio(lp.lower - v, CERT_ABS + CERT_REL * np.abs(lp.lower)),
        _ratio(np.where(finite_up, v - upper, 0.0), CERT_ABS + CERT_REL * np.abs(upper)),
    )
    sign_allowed = CERT_REL * max(1.0, float(np.max(np.abs(lp.c), initial=0.0)))
    dual_sign = max(
        _ratio(y_ub, sign_allowed),
        _ratio(-y_lo, sign_allowed),
        _ratio(y_up, sign_allowed),
        _ratio(np.abs(np.where(finite_up, 0.0, y_up)), sign_allowed),
    )
    reduced = lp.c - exact_matvec(lp.a_ub.T, y_ub) - exact_matvec(lp.a_eq.T, y_eq) - y_lo - y_up
    magnitude = (
        np.abs(lp.c)
        + exact_matvec(np.abs(lp.a_ub.T), np.abs(y_ub))
        + exact_matvec(np.abs(lp.a_eq.T), np.abs(y_eq))
        + np.abs(y_lo)
        + np.abs(y_up)
    )
    stationarity = _ratio(np.abs(reduced), CERT_REL * np.maximum(1.0, magnitude))
    primal_terms = (lp.c * v).tolist()
    dual_terms = [
        *(lp.b_ub * y_ub).tolist(),
        *(lp.b_eq * y_eq).tolist(),
        *(lp.lower * y_lo).tolist(),
        *(upper * y_up).tolist(),
    ]
    primal_value = math.fsum(primal_terms)
    dual_value = math.fsum(dual_terms)
    gap_allowed = CERT_REL * max(
        1.0, math.fsum(abs(t) for t in primal_terms), math.fsum(abs(t) for t in dual_terms)
    )
    gap = abs(primal_value - dual_value) / gap_allowed
    finite = all(bool(np.all(np.isfinite(values))) for values in (v, y_ub, y_eq, y_lo, y_up))
    # Fail closed: a NaN anywhere makes every comparison False, and the built-in max
    # ignores a NaN that is not its first argument.
    holds = finite and all(
        math.isfinite(ratio) and ratio <= 1.0 for ratio in (primal, dual_sign, stationarity, gap)
    )
    return Certificate(
        primal=primal,
        dual_sign=dual_sign,
        stationarity=stationarity,
        duality_gap=gap,
        primal_objective=primal_value + lp.constant,
        dual_objective=dual_value + lp.constant,
        holds=bool(holds),
    )


@dataclass(frozen=True)
class Cleaning:
    """Solver values just outside their bounds (within tolerance), snapped onto them."""

    values_adjusted: int
    max_adjustment: float


def to_plan(lp: LinearProgram, x: FloatArray) -> tuple[Plan, Cleaning]:
    """The plan of a solution vector, with round-off outside the bounds snapped to them.

    Only values within :func:`atlas.planning.tolerance` of a bound are snapped; anything
    further out is kept as returned, so :func:`atlas.planning.check_plan` reports it.
    """
    values = np.array(x, dtype="float64")
    adjusted = 0
    largest = 0.0
    for index, value in enumerate(values):
        low, high = lp.lower[index], lp.upper[index]
        target = value
        if value < low and low - value <= tolerance(value, low):
            target = low
        elif value > high and value - high <= tolerance(value, high):
            target = high
        if target != value:
            adjusted += 1
            largest = max(largest, abs(target - value))
            values[index] = target
    units = tuple(
        UnitPlan(
            unit_id=unit_id,
            served=tuple(float(v) for v in lp.layout.block(values, k, "served")),
            overtime_hours=tuple(float(v) for v in lp.layout.block(values, k, "overtime_hours")),
            surge=tuple(float(v) for v in lp.layout.block(values, k, "surge")),
            backlog=tuple(float(v) for v in lp.layout.block(values, k, "backlog")),
        )
        for k, unit_id in enumerate(lp.layout.unit_ids)
    )
    return Plan(units), Cleaning(adjusted, largest)


def plan_vector(lp: LinearProgram, plan: Plan) -> FloatArray:
    """The solution vector of ``plan`` in the layout of ``lp`` (inverse of :func:`to_plan`)."""
    values = np.zeros(lp.layout.size)
    for k, decisions in enumerate(plan.units):
        for block in BLOCKS:
            start = lp.layout.index(k, block, 0)
            values[start : start + lp.layout.horizon] = getattr(decisions, block)
    return values


@dataclass(frozen=True)
class DualValue:
    """Slack, binding state and shadow price of one named row or upper bound.

    ``marginal`` is the derivative of the optimal objective (units ``marginal_units``) with
    respect to the right-hand side ``rhs`` (units ``rhs_units``). ``binding`` means the
    slack is within tolerance of zero; a binding constraint can have a zero marginal
    (degeneracy) and only a tight constraint can have a non-zero one. A daily limit of zero
    (the resource is unavailable that day) is never called binding, although its marginal
    can be non-zero.
    """

    constraint: str
    unit_id: str | None
    day: int | None
    rhs: float
    slack: float
    binding: bool
    marginal: float
    rhs_units: str
    marginal_units: str

    def key(self) -> tuple[str, str, int]:
        return (self.constraint, self.unit_id or "", -1 if self.day is None else self.day)


def dual_values(lp: LinearProgram, result: LPResult) -> tuple[DualValue, ...]:
    """Every named row and finite overtime / surge upper bound with its shadow price.

    Only meaningful for the planning objective (:attr:`Objective.WEIGHTED`), whose units
    are USD; balance rows have no slack (equalities) and are reported as binding.
    """
    if lp.objective is not Objective.WEIGHTED:
        raise OptimizationError("shadow prices are reported for the planning objective only")
    if result.x is None or result.ub_marginals is None or result.eq_marginals is None:
        raise OptimizationError("only an optimal solution has shadow prices")
    if result.upper_marginals is None:  # pragma: no cover - set together with x
        raise OptimizationError("missing bound marginals")
    v = result.x
    duals: list[DualValue] = []
    for i, row in enumerate(lp.eq_rows):
        rhs_units, marginal_units = ROW_UNITS[row.constraint]
        duals.append(
            DualValue(
                row.constraint,
                row.unit_id,
                row.day,
                float(lp.b_eq[i]),
                0.0,
                True,
                float(result.eq_marginals[i]),
                rhs_units,
                marginal_units,
            )
        )
    for i, row in enumerate(lp.ub_rows):
        if row.constraint not in ROW_UNITS:
            continue  # analysis rows (caps) are not model constraints
        rhs_units, marginal_units = ROW_UNITS[row.constraint]
        slack = float(lp.b_ub[i] - exact_dot(lp.a_ub[i], v))
        scale = exact_dot(np.abs(lp.a_ub[i]), np.abs(v)) + abs(float(lp.b_ub[i]))
        duals.append(
            DualValue(
                row.constraint,
                row.unit_id,
                row.day,
                float(lp.b_ub[i]),
                slack,
                bool(slack <= tolerance(scale)),
                float(result.ub_marginals[i]),
                rhs_units,
                marginal_units,
            )
        )
    for k, unit_id in enumerate(lp.layout.unit_ids):
        for block, constraint in (
            ("overtime_hours", "overtime_daily_limit"),
            ("surge", "surge_daily_limit"),
        ):
            rhs_units, marginal_units = ROW_UNITS[constraint]
            for day in range(lp.layout.horizon):
                j = lp.layout.index(k, block, day)
                slack = float(lp.upper[j] - v[j])
                duals.append(
                    DualValue(
                        constraint,
                        unit_id,
                        day,
                        float(lp.upper[j]),
                        slack,
                        # A zero limit (surge not yet available) is unavailable, not used up.
                        bool(lp.upper[j] > 0 and slack <= tolerance(lp.upper[j], v[j])),
                        float(result.upper_marginals[j]),
                        rhs_units,
                        marginal_units,
                    )
                )
    return tuple(duals)


def solver_versions() -> dict[str, str | None]:
    """SciPy version and the version of the HiGHS library bundled with it."""
    import scipy

    highs: str | None
    try:  # private module: SciPy has no public accessor for the bundled HiGHS version
        core = importlib.import_module("scipy.optimize._highspy._core")
        highs = ".".join(
            str(getattr(core, f"HIGHS_VERSION_{part}")) for part in ("MAJOR", "MINOR", "PATCH")
        )
    except (ImportError, AttributeError):  # pragma: no cover - depends on the SciPy build
        highs = None
    return {"scipy": str(scipy.__version__), "highs": highs}
