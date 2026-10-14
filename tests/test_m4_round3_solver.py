"""Regression tests for the third independent review of the solve layer (round 3).

* solve-layer-02: at large scale HiGHS's absolute tolerances (``1e-9``) are below the
  rounding error of the data, so presolve called programmes that contain a known feasible
  plan infeasible and the simplex stopped with status 15. Every programme is now scaled
  exactly by a power of two before it is solved (the reference run keeps ``K = 1``), and an
  infeasible verdict of presolve on a programme known to contain a feasible point (an
  optimal face, an epsilon cap, caps on a feasible plan's values) is confirmed without
  presolve.
* solve-layer-03 / diagnosis-fd-duals-08: a solver error in the last lexicographic fallback
  aborted the run, and the policy caps were loosened only when the first stage failed.
* frontier: a cap whose first stage is optimal but whose tie-break failed was recorded as
  "optimal" without values and called infeasible.
* solve-layer-01: a row's tie threshold was set by an unrelated column fixed at a bound, so
  a genuine budget dual counted as zero and the least-cash plan was silently not optimal.
"""

from __future__ import annotations

import dataclasses
import math
import random
from datetime import date
from typing import Any

import numpy as np
import pytest

import atlas.decision_analysis as analysis
import atlas.optimizer as optimizer
from atlas.config import load_settings
from atlas.decision_analysis import (
    analyse_scenario,
    evaluate_policy,
    face_programme,
    frontier,
    minimise_on_face,
    solve_problem,
    stages_under_caps,
    tie_broken,
)
from atlas.optimization_config import load_optimization_config
from atlas.optimization_runner import load_inputs, m4_outputs
from atlas.optimizer import (
    LinearProgram,
    LPResult,
    Objective,
    OptimizationError,
    SolverSettings,
    SolveStatus,
    Terminal,
    build_lp,
    certify,
    objective_vector,
    scale_factor,
    solve_lp,
)
from atlas.planning import PlanningProblem, ServiceUnit
from atlas.policies import BACKLOG_TRIGGER, FIXED_CAPACITY
from atlas.runner import load_series
from atlas.scenarios import build_scenarios
from tests.conftest import REPO_ROOT

SETTINGS = SolverSettings()
CONFIGS = REPO_ROOT / "configs" / "optimization"
STEPS = {"requests": 1.0, "staff_hours": 1.0, "usd": 100.0}


def _unit(**overrides: Any) -> ServiceUnit:
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


def _problem(budget: float | None = None, **overrides: Any) -> PlanningProblem:
    return PlanningProblem(date(2026, 7, 1), (_unit(**overrides),), budget)


def _times(problem: PlanningProblem, k: float) -> PlanningProblem:
    """Every quantity (demand, backlog, hours, limits, totals, target, budget) times ``k``."""

    def scaled(u: ServiceUnit) -> ServiceUnit:
        def opt(v: float | None) -> float | None:
            return None if v is None else v * k

        extra: dict[str, Any] = {  # a configured daily surge capacity, where it exists
            name: opt(getattr(u, name))
            for name in ("surge_capacity_when_available",)
            if name in {f.name for f in dataclasses.fields(u)}
        }
        return dataclasses.replace(
            u,
            **extra,
            demand=tuple(d * k for d in u.demand),
            initial_backlog=u.initial_backlog * k,
            regular_hours=tuple(h * k for h in u.regular_hours),
            overtime_max_hours=tuple(h * k for h in u.overtime_max_hours),
            surge_max=tuple(s * k for s in u.surge_max),
            overtime_total_hours=opt(u.overtime_total_hours),
            surge_total=opt(u.surge_total),
            terminal_backlog_max=opt(u.terminal_backlog_max),
        )

    budget = problem.discretionary_budget
    return dataclasses.replace(
        problem,
        units=tuple(scaled(u) for u in problem.units),
        discretionary_budget=None if budget is None else budget * k,
    )


def _reference_scenarios() -> dict[str, Any]:
    config = load_optimization_config(CONFIGS / "atlas-m4-optimization-v1.json")
    settings = load_settings(repo_root=REPO_ROOT, env={})
    _, data = load_series(settings, "round3_solver")
    assert data is not None
    inputs = load_inputs(settings, config, data)
    dates, point = inputs.projection.first(config.horizon_days)
    scenarios = build_scenarios(config, dates, point, inputs.quantiles, inputs.demand_percentile)
    return {"config": config, "scenarios": {s.scenario_id: s for s in scenarios}}


# --- Exact scaling in solve_lp ---------------------------------------------------------------


def test_programmes_of_ordinary_magnitude_are_not_scaled() -> None:
    small = build_lp(_problem(budget=50.0))
    assert scale_factor(small) == 1.0
    edge = build_lp(_problem(demand=(optimizer.SCALE_THRESHOLD, 0.0)))
    assert scale_factor(edge) == 1.0
    above = build_lp(_problem(demand=(optimizer.SCALE_THRESHOLD * 1.5, 0.0)))
    assert scale_factor(above) > 1.0
    assert optimizer.SCALE_THRESHOLD * 1.5 / scale_factor(above) < 2.0**optimizer.SCALE_EXPONENT
    huge = build_lp(_problem(demand=(3e8, 1e8)))
    factor = scale_factor(huge)
    assert math.frexp(factor)[0] == 0.5  # a power of two
    assert 2.0 ** (optimizer.SCALE_EXPONENT - 1) <= 3e8 / factor < 2.0**optimizer.SCALE_EXPONENT


def test_scaling_is_exact_and_leaves_every_dual_unchanged() -> None:
    # A programme whose largest value lies in [2**20, 2**21) is solved as built; the same
    # programme with every quantity times 2**8 is scaled by K = 2**8 back onto it, so HiGHS
    # sees identical numbers: the solution is exactly 2**8 times larger and every dual
    # (rows, equalities, bounds) is identical, bit for bit.
    base = _problem(
        budget=1.5e6,
        demand=(1.6e6, 2.0e5),
        regular_hours=(1.0e6, 1.2e6),
        overtime_max_hours=(3.0e5, 3.0e5),
        surge_max=(1.0e5, 1.0e5),
        overtime_wage=3.0,
        surge_price=4.0,
        backlog_penalty=2.5,
        terminal_backlog_max=1.0e5,
        terminal_penalty=7.0,
    )
    small = build_lp(base)
    big = build_lp(_times(base, 2.0**8))
    assert scale_factor(small) == 1.0
    assert scale_factor(big) == 2.0**8
    a, b = solve_lp(small, SETTINGS), solve_lp(big, SETTINGS)
    assert a.status is b.status is SolveStatus.OPTIMAL
    assert a.x is not None
    assert b.x is not None
    assert np.array_equal(b.x, a.x * 2.0**8)
    for name in ("ub_marginals", "eq_marginals", "lower_marginals", "upper_marginals"):
        assert np.array_equal(getattr(a, name), getattr(b, name)), name
    assert a.objective is not None
    assert b.objective == a.objective * 2.0**8
    assert certify(big, b).holds


def test_scaling_keeps_the_objective_constant_and_derived_cap_rows() -> None:
    # Cap rows (b_ub = cap - constant) and the objective constant: the constant is not
    # passed to HiGHS and the value is recomputed on the original programme.
    problem = _problem(budget=None, demand=(4e8, 0.0), regular_hours=(1e8, 3e8))
    probe = build_lp(problem)
    cap = analysis.cap_row(probe, Objective.DISCRETIONARY_CASH, 2.5e8, "cap_frontier_cash")
    lp = build_lp(problem, Objective.PENALTY, budget_row=False, extra_rows=[cap])
    lp = dataclasses.replace(lp, constant=12.5)
    assert scale_factor(lp) > 1.0
    result = solve_lp(lp, SETTINGS)
    assert result.status is SolveStatus.OPTIMAL
    assert result.x is not None
    assert result.objective == lp.value(result.x)
    assert certify(lp, result).holds
    cash, _ = objective_vector(problem, lp.layout, Objective.DISCRETIONARY_CASH)
    assert optimizer.exact_dot(cash, result.x) <= 2.5e8 * (1 + 1e-12)


def _presolve_says_infeasible(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Make HiGHS's presolve call every programme infeasible (status 8), as it does at scale."""
    real = optimizer._linprog
    calls: list[bool] = []

    def fake(lp: LinearProgram, settings: SolverSettings, presolve: bool) -> Any:
        calls.append(presolve)
        result = real(lp, settings, presolve)
        if presolve:
            result["status"] = 2
            result["message"] = "The problem is infeasible. (HiGHS Status 8: x)"
        return result

    monkeypatch.setattr(optimizer, "_linprog", fake)
    return calls


def test_presolve_infeasibility_of_a_known_feasible_programme_is_overturned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lp = build_lp(_problem(budget=50.0))
    calls = _presolve_says_infeasible(monkeypatch)
    # Every presolve verdict of infeasibility is confirmed without presolve; a certified
    # optimum of the re-solve overturns it (a genuinely infeasible model stays infeasible:
    # test_a_confirmed_infeasible_verdict_keeps_presolve_result).
    solved = solve_lp(lp, SETTINGS)
    assert calls == [True, False]
    assert solved.status is SolveStatus.OPTIMAL
    assert solved.retried
    assert certify(lp, solved).holds
    again = analysis._solve_feasible(lp, SETTINGS)
    assert again.status is SolveStatus.OPTIMAL
    assert again.retried


def test_a_face_that_presolve_calls_infeasible_is_solved_without_presolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strict = solve_problem(_problem(budget=50.0), SETTINGS).require()
    _presolve_says_infeasible(monkeypatch)
    cash, _ = objective_vector(strict.lp.problem, strict.lp.layout, Objective.DISCRETIONARY_CASH)
    _, result, flag = minimise_on_face(
        strict.lp, strict.result, SETTINGS, Objective.DISCRETIONARY_CASH, cash
    )
    assert flag is None
    assert result.status is SolveStatus.OPTIMAL
    assert result.retried
    face = analysis.optimal_face(strict.lp.problem, SETTINGS, strict)
    assert face.problems == ()


def test_a_confirmed_infeasible_verdict_keeps_presolve_result() -> None:
    # Demand of 30 with no capacity and a target of 0: infeasible with and without presolve;
    # the result (message, iterations) is presolve's, as before the confirmation existed.
    lp = build_lp(
        _problem(
            demand=(30.0, 0.0),
            regular_hours=(0.0, 0.0),
            terminal_backlog_max=0.0,
            overtime_max_hours=(0.0, 0.0),
        )
    )
    first = solve_lp(lp, SETTINGS)
    result = analysis._solve_feasible(lp, SETTINGS)
    assert result.status is SolveStatus.INFEASIBLE
    assert not result.retried
    assert result == first


@pytest.mark.integration
def test_the_reference_configuration_solves_every_programme_unscaled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The reference run's results are byte-identical to those before scaling existed only
    # because every programme it solves keeps K = 1 (its largest value is about 1.09e6).
    factors: list[float] = []
    real = optimizer.scale_factor

    def spy(lp: LinearProgram) -> float:
        factors.append(real(lp))
        return factors[-1]

    monkeypatch.setattr(optimizer, "scale_factor", spy)
    config = load_optimization_config(CONFIGS / "atlas-m4-optimization-v1.json")
    settings = load_settings(repo_root=REPO_ROOT, env={})
    _, data = load_series(settings, "round3_reference_unscaled")
    assert data is not None
    outputs = m4_outputs(settings, config, load_inputs(settings, config, data))
    assert len(factors) > 1000
    assert set(factors) == {1.0}
    assert not [i for i in outputs.issues if i.severity.value == "error"]


# --- solve-layer-02: large scale ---------------------------------------------------------------


@pytest.mark.integration
def test_the_reference_normal_scenario_times_300_has_no_false_errors() -> None:
    # Exact x300 rescale (3.3e6 requests/day): 4 ERRORs at 66de697 (terminal_backlog stage
    # "infeasible although the earlier optimum lies on its face", and both frontier endpoints
    # "optimal" without values and called infeasible).
    reference = _reference_scenarios()
    config = reference["config"]
    base = reference["scenarios"]["normal"]
    problem = _times(base.problem, 300.0)
    triggers = {k: v * 300.0 for k, v in base.triggers.items()}
    result = analyse_scenario(
        "normal_x300",
        problem,
        SETTINGS,
        triggers,
        shadow_steps=config.shadow_prices.finite_difference_steps.model_dump(),
        frontier_points=config.frontier.points,
    )
    assert result.problems == ()
    assert result.frontier is not None
    points = result.frontier.points
    assert all(p.status is not SolveStatus.FAILED for p in points)
    optimal = [p for p in points if p.status is SolveStatus.OPTIMAL]
    assert all(p.penalty is not None and p.discretionary_cash is not None for p in optimal)
    # both endpoints (the cap at the least cash for the least penalty) have values
    for variant in ("kept", "dropped"):
        top = result.frontier.cash_for_least_penalty[variant]
        end = [p for p in optimal if p.terminal_target == variant and p.cash_cap == top]
        assert end, variant
    assert all(o.same_cash.optimal and o.same_backlog.optimal for o in result.policies)


def _reference_shaped(seed: int, lo: float, hi: float) -> tuple[PlanningProblem, dict[str, float]]:
    """A random reference-shaped unit (the round-3 reviewers' generator, same RNG stream)."""
    rng = random.Random(seed)
    d = 10 ** rng.uniform(lo, hi)
    horizon = rng.randint(10, 30)
    demand = tuple(d * rng.uniform(0.85, 1.15) for _ in range(horizon))
    p = 2.5 * rng.choice([1.0, rng.uniform(0.8, 1.2)])
    q = 2.25 * rng.choice([1.0, rng.uniform(0.8, 1.2)])
    reg = d / p * rng.uniform(0.9, 1.02)
    ot = reg * rng.uniform(0.03, 0.15)
    delay = rng.choice([0, 0, rng.randint(1, horizon - 1)])
    smax = d * rng.uniform(0.02, 0.1)
    unit = ServiceUnit(
        unit_id="u",
        demand=demand,
        initial_backlog=d * rng.uniform(0, 1.5),
        regular_hours=tuple(reg for _ in range(horizon)),
        regular_productivity=p,
        overtime_productivity=q,
        overtime_max_hours=tuple(ot for _ in range(horizon)),
        surge_max=tuple(0.0 if t < delay else smax for t in range(horizon)),
        regular_wage=40.0 * rng.choice([1.0, 1.1, 1.2]),
        overtime_wage=60.0 * rng.choice([1.0, 1.1, 1.2]),
        surge_price=30.0 * rng.choice([1.0, 1.1, 1.2]),
        backlog_penalty=rng.choice([0.0, 0.5, 1.0, 2.0, 4.0, 8.0]),
        terminal_penalty=rng.choice([0.0, 0.0, 5.0]),
        overtime_total_hours=rng.choice([None, ot * horizon * rng.uniform(0.3, 0.8)]),
        surge_total=rng.choice([None, None, smax * horizon * rng.uniform(0.3, 0.8)]),
        terminal_backlog_max=rng.choice([None, d * rng.uniform(0.1, 1.0)]),
    )
    budget = rng.choice([None, d * horizon * rng.uniform(0.3, 1.5)])
    return PlanningProblem(date(2026, 7, 1), (unit,), budget), {"u": d * rng.uniform(0.1, 1.0)}


@pytest.mark.integration
def test_presolve_false_infeasibility_at_realistic_scale_is_overturned() -> None:
    # 3.35e4 requests/day, 26 days: two frontier face tests gave 16 false "optimal-face
    # programme is infeasible" at 66de697 (presolve status 8; optimal without presolve). The
    # programmes are below the scaling threshold, so the confirmation re-solve fixes them.
    problem, triggers = _reference_shaped(50003, 3.0, 5.0)
    assert 3e4 < sum(problem.units[0].demand) / problem.horizon < 4e4
    result = analyse_scenario(
        "ctl_50003", problem, SETTINGS, triggers, shadow_steps=STEPS, frontier_points=7
    )
    assert result.problems == ()
    assert result.frontier is not None
    assert all(p.unique_after_tie_breaks is not None for p in result.frontier.points if p.penalty)


# --- solve-layer-03 / diagnosis-fd-duals-08: no abort, loose caps on any stage --------------------


def test_a_solver_error_in_the_epsilon_cap_is_returned_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strict = solve_problem(_problem(budget=50.0), SETTINGS).require()

    def fail(lp: LinearProgram, settings: SolverSettings) -> LPResult:
        raise OptimizationError("injected: HiGHS Status 15")

    monkeypatch.setattr(analysis, "solve_lp", fail)
    cash, _ = objective_vector(strict.lp.problem, strict.lp.layout, Objective.DISCRETIONARY_CASH)
    _, result, flag = minimise_on_face(
        strict.lp, strict.result, SETTINGS, Objective.DISCRETIONARY_CASH, cash
    )
    assert flag == "epsilon_cap"
    assert result.status is SolveStatus.FAILED
    assert result.x is None
    assert "injected" in result.message
    stage = analysis.solve_on_face(strict, SETTINGS, Objective.DISCRETIONARY_CASH)
    assert stage.status is SolveStatus.FAILED
    assert any("lexicographic stage discretionary_cash failed" in p for p in stage.problems)
    assert stage.stage_fallback == "epsilon_cap"


def _has_row(lp: LinearProgram, name: str) -> bool:
    return any(row.constraint == name for row in lp.ub_rows)


@pytest.mark.integration
def test_a_failing_tie_break_under_policy_caps_is_an_error_not_an_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The SL-03 path: the backlog-trigger same-backlog chain's terminal_backlog stage raises
    # on the face, the face of exact zeros and the epsilon cap (exact and loose caps). The
    # run completes, every result is written, and the failure is an ERROR.
    real = optimizer.solve_lp

    def fail_terminal_stage(lp: LinearProgram, settings: SolverSettings) -> LPResult:
        if lp.objective is Objective.TERMINAL_BACKLOG and _has_row(lp, "cap_policy_backlog"):
            raise OptimizationError("injected: HiGHS Status 15")
        return real(lp, settings)

    monkeypatch.setattr(analysis, "solve_lp", fail_terminal_stage)
    config = load_optimization_config(CONFIGS / "smoke.json")
    settings = load_settings(repo_root=REPO_ROOT, env={})
    _, data = load_series(settings, "round3_epsilon_cap_raise")
    assert data is not None
    outputs = m4_outputs(settings, config, load_inputs(settings, config, data))
    assert {"scenarios.csv", "plans.csv", "metrics.json"} <= set(outputs.files)
    errors = [i for i in outputs.issues if i.severity.value == "error"]
    assert errors
    assert any("lexicographic stage terminal_backlog failed" in i.message for i in errors)


def _policy_problem() -> PlanningProblem:
    return _problem(
        budget=500.0,
        demand=(40.0, 30.0, 20.0),
        regular_hours=(10.0, 10.0, 10.0),
        overtime_max_hours=(10.0, 10.0, 10.0),
        surge_max=(10.0, 10.0, 10.0),
        overtime_wage=2.0,
        surge_price=3.0,
        backlog_penalty=1.0,
    )


def test_policy_caps_are_loosened_when_a_later_stage_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # diagnosis-fd-duals-08: stage 1 under the exact policy caps is optimal, a later stage
    # is not. The loose caps are now tried for any stage, and the retry is recorded.
    problem = _policy_problem()
    reference = evaluate_policy(problem, SETTINGS, BACKLOG_TRIGGER, {"u": 5.0})
    assert reference.problems == ()
    assert reference.same_backlog.optimal
    exact_caps: dict[str, float] = {}
    real = optimizer.solve_lp

    def stage2_infeasible_under_exact_caps(lp: LinearProgram, settings: SolverSettings) -> LPResult:
        rows = [i for i, row in enumerate(lp.ub_rows) if row.constraint == "cap_policy_backlog"]
        if lp.objective is Objective.WEIGHTED and rows:
            rhs = float(lp.b_ub[rows[0]])
            exact = exact_caps.setdefault("backlog", rhs)  # the exact chain runs first
            if rhs == exact:
                return LPResult(SolveStatus.INFEASIBLE, 2, "injected (HiGHS Status 8)", 0, 8)
        return real(lp, settings)

    monkeypatch.setattr(analysis, "solve_lp", stage2_infeasible_under_exact_caps)
    outcome = evaluate_policy(problem, SETTINGS, BACKLOG_TRIGGER, {"u": 5.0})
    assert outcome.problems == ()
    assert outcome.same_backlog.optimal
    assert outcome.fallbacks.count("policy_epsilon_cap") == 1
    assert outcome.least_cash_at_policy_backlog == pytest.approx(
        reference.least_cash_at_policy_backlog, rel=1e-6, abs=1e-5
    )


def test_a_cap_chain_that_raises_is_recorded_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(lp: LinearProgram, settings: SolverSettings) -> LPResult:
        if _has_row(lp, "cap_policy_cash"):
            raise OptimizationError("injected: HiGHS Status 15")
        return solve_lp(lp, settings)

    monkeypatch.setattr(analysis, "solve_lp", fail)
    stages = stages_under_caps(
        _policy_problem(),
        SETTINGS,
        tie_broken(Objective.BACKLOG_DAYS, Objective.WEIGHTED),
        [(Objective.DISCRETIONARY_CASH, 10.0, "cap_policy_cash")],
        terminal=Terminal.NONE,
        fallback="policy_epsilon_cap",
    )
    assert all(a.status is SolveStatus.FAILED for a in stages)
    assert any("failed: injected" in p for p in analysis.attempt_problems(stages))
    outcome = evaluate_policy(_policy_problem(), SETTINGS, FIXED_CAPACITY, {"u": 5.0})
    assert any("not optimal" in p for p in outcome.problems)


def test_the_fd08_same_backlog_programme_is_optimal() -> None:
    # diagnosis-fd-duals-08 (seed 1307): 885k requests/day; the weighted stage under the exact
    # caps was called infeasible on every fallback (an ERROR) although stage 1 was optimal.
    unit = ServiceUnit(
        unit_id="u0",
        demand=(885213.9514962228, 257048.6959191367, 0.0, 663385.0265959839, 867991.6827767921),
        initial_backlog=4653.359114431432,
        regular_hours=(
            35020.95026388898,
            399796.7778984731,
            106147.87316414979,
            442909.0660541966,
            128696.02325504427,
        ),
        regular_productivity=1.0,
        overtime_productivity=1.0,
        overtime_max_hours=(93405.84400157191,) * 5,
        surge_max=(0.0,) * 5,
        regular_wage=20.0,
        overtime_wage=120.0,
        surge_price=80.0,
        backlog_penalty=8.0,
        terminal_penalty=0.0,
    )
    problem = PlanningProblem(date(2026, 7, 1), (unit,), 36418510.409653306)
    outcome = evaluate_policy(problem, SETTINGS, BACKLOG_TRIGGER, {"u0": 500000.0})
    assert outcome.problems == ()
    assert outcome.same_cash.optimal
    assert outcome.same_backlog.optimal


# --- frontier: a failed tie-break is not an infeasible cap -------------------------------------


def _frontier_problem() -> PlanningProblem:
    return _problem(
        budget=None,
        demand=(30.0, 10.0),
        regular_hours=(5.0, 5.0),
        overtime_max_hours=(10.0, 10.0),
        surge_max=(10.0, 10.0),
        overtime_wage=1.0,
        surge_price=2.0,
        backlog_penalty=3.0,
        terminal_backlog_max=15.0,
    )


def test_a_frontier_cap_whose_tie_break_fails_is_not_called_infeasible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = _frontier_problem()
    clean = frontier(problem, SETTINGS, 5, None)
    assert clean is not None
    assert clean.problems == ()
    real = optimizer.solve_lp

    def fail_tie_break(lp: LinearProgram, settings: SolverSettings) -> LPResult:
        if lp.objective is Objective.TERMINAL_BACKLOG and _has_row(lp, "cap_frontier_cash"):
            raise OptimizationError("injected: HiGHS Status 15")
        return real(lp, settings)

    monkeypatch.setattr(analysis, "solve_lp", fail_tie_break)
    curve = frontier(problem, SETTINGS, 5, None)
    assert curve is not None
    failed = [p for p in curve.points if p.status is SolveStatus.FAILED]
    assert failed
    good = {(p.terminal_target, p.cash_cap): p for p in clean.points}
    for point in failed:
        expected = good[(point.terminal_target, point.cash_cap)]
        if expected.status is SolveStatus.OPTIMAL:  # the cap is feasible: its penalty is kept
            assert point.penalty == pytest.approx(expected.penalty, rel=1e-9, abs=1e-9)
        assert point.discretionary_cash is None
    # never "optimal" without values, never "infeasible" for a feasible cap
    assert not [p for p in curve.points if p.status is SolveStatus.OPTIMAL and p.penalty is None]
    assert not [p for p in curve.problems if "reported infeasible although" in p]
    assert not [p for p in curve.problems if "infeasible without a target" in p]
    assert any("has no tie-broken plan: the cap is feasible" in p for p in curve.problems)
    assert any("lexicographic stage terminal_backlog failed" in p for p in curve.problems)


def test_an_infeasible_frontier_cap_is_still_classified_infeasible() -> None:
    # Below the least cash that reaches the target, the cap is infeasible: no problem.
    curve = frontier(_frontier_problem(), SETTINGS, 5, None)
    assert curve is not None
    kept = [p for p in curve.points if p.terminal_target == "kept"]
    assert any(p.status is SolveStatus.INFEASIBLE for p in kept)
    assert all(p.penalty is None for p in kept if p.status is SolveStatus.INFEASIBLE)
    assert curve.problems == ()


# --- solve-layer-01: a genuine budget dual is not a tie -----------------------------------------


def _row_tau_problem() -> PlanningProblem:
    # One unit, 3 days: day-1 overtime at 30 USD/request beats carrying at 30.0003, so the
    # unique optimum spends the whole budget (90,000 USD); the terminal backlog of 100 is
    # forced, and w_T = 1e6 plays no part in the budget trade-off.
    return _problem(
        budget=90000.0,
        demand=(10000.0, 0.0, 110.0),
        regular_hours=(0.0, 10000.0, 10.0),
        overtime_max_hours=(10000.0, 0.0, 0.0),
        surge_max=(0.0, 0.0, 0.0),
        overtime_wage=30.0,
        surge_price=30.0,
        backlog_penalty=30.0003,
        terminal_penalty=1e6,
    )


def test_a_row_threshold_is_not_set_by_a_column_fixed_at_its_bound() -> None:
    strict = solve_problem(_row_tau_problem(), SETTINGS).require()
    budget = next(i for i, r in enumerate(strict.lp.ub_rows) if r.constraint == "budget")
    assert strict.result.ub_marginals is not None
    assert -1.1e-5 < strict.result.ub_marginals[budget] < -0.9e-5  # a genuine dual
    face, _, _, _ = face_programme(strict.lp, strict.result)
    assert any(r.constraint == "tight_budget" for r in face.ub_rows)


def test_the_least_cash_plan_spends_the_budget_when_its_dual_is_genuine() -> None:
    result = analyse_scenario(
        "sl01", _row_tau_problem(), SETTINGS, {"u": 0.0}, shadow_steps=STEPS, frontier_points=None
    )
    assert result.problems == ()
    assert result.fallbacks == ()
    reported = result.reported.require()
    assert reported.evaluation.discretionary_cost == pytest.approx(90000.0, rel=1e-9)
    budget = next(d for d in result.duals if d.constraint == "budget")
    assert budget.binding
    assert budget.slack == pytest.approx(0.0, abs=1e-6)
    assert result.face is not None
    assert result.face.unique
    split = next(f for f in result.finite_differences if f.dual.constraint == "budget")
    assert split.forward_cash == pytest.approx(1.0, rel=1e-6)


def test_a_loose_face_that_loses_through_a_dropped_dual_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The safety net on its own: with the old rule (the largest scale over the whole row,
    # including the fixed day-3 overtime column whose scale is about w_T), the budget dual
    # counts as zero and the least-cash face plan spends nothing. Its score is within
    # 10 x the tolerance of the whole score (dominated by w_T b[T]), but it loses 0.9
    # through the dropped budget dual, far beyond the tolerance of the terms it changes: the
    # face is rebuilt from exact zeros (recorded), and the plan is right.
    real = analysis._face_tests

    def old_rule(lp: LinearProgram, result: LPResult, zero_threshold: bool) -> Any:
        tests = real(lp, result, zero_threshold)
        if zero_threshold:
            return tests
        assert result.ub_marginals is not None
        assert result.eq_marginals is not None
        scale = (
            np.abs(lp.c)
            + optimizer.exact_matvec(np.abs(lp.a_ub.T), np.abs(result.ub_marginals))
            + optimizer.exact_matvec(np.abs(lp.a_eq.T), np.abs(result.eq_marginals))
        )
        row_tau = np.array(
            [
                analysis.FACE_DUAL_REL
                * max(1.0, float(np.max(scale[row != 0] / np.abs(row[row != 0]))))
                for row in lp.a_ub
            ]
        )
        return dataclasses.replace(tests, row_tau=row_tau, tight=result.ub_marginals < -row_tau)

    monkeypatch.setattr(analysis, "_face_tests", old_rule)
    result = analyse_scenario(
        "sl01", _row_tau_problem(), SETTINGS, {"u": 0.0}, shadow_steps=None, frontier_points=None
    )
    assert result.problems == ()
    assert "zero_threshold" in result.fallbacks
    assert result.reported.require().evaluation.discretionary_cost == pytest.approx(90000.0)
    assert result.face is not None
    assert result.face.unique
    assert result.face.rebuilt
