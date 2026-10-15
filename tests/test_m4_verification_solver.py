"""Regression tests for the verification of the round-3 fixes: the solve layer (ce2b104).

Each test pins one fix of commit ce2b104, or a round-3 test gap the verification found
unguarded, and fails when that fix is reverted:

* r4-solve-layer-01: HiGHS statuses other than 7 and 8 (0 not set, 4 solve error, 10, 15
  and a first 9) are re-solved without presolve, then by the interior-point method, whose
  optimum is used only if the certificate holds on the original programme (and is named in
  its message). Small big-M instances the dual simplex cannot conclude, and the reference
  configuration with a big-M terminal penalty at NYC scale, now complete.
* r4-solve-layer-03 / r4-diagnosis-01: a finite-difference re-solve that failed leaves the
  bracket, convexity and sign tests undone (``None``), not failed: no "outside its
  finite-difference bracket" problem, an empty ``bracket_ok`` in duals.csv, and metrics and
  report computed over the other duals.
* r4-solve-layer-02: a stage with no plan records no fallback; the frontier records whether
  its least-penalty programme was infeasible or failed, counts points without a plan, and
  the report says so instead of "no plan meets the target" or "every point is unique".
* Round-3 test gaps: test-strength-03 (the 10 x tolerance of the kept-optimum check),
  test-strength-07 (the epsilon cap is exactly ``lex_cap`` of the optimum), r4-suite-02
  (the optimal face widens every row to the optimum) and test-strength-06 (exact fallback
  and problem accounting).
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Literal

import numpy as np
import pytest
from scipy.optimize import OptimizeResult

import atlas.decision_analysis as analysis
import atlas.optimization_runner as runner
import atlas.optimizer as optimizer
from atlas.config import load_settings
from atlas.decision_analysis import (
    ScenarioResult,
    analyse_scenario,
    face_programme,
    finite_differences,
    minimise_on_face,
    optimal_face,
    solve_canonical,
    solve_problem,
)
from atlas.decision_inputs import POINT_FORECAST, M3Status
from atlas.experiment import csv_bytes
from atlas.issues import Severity
from atlas.optimization_config import load_optimization_config, parse_optimization_config
from atlas.optimization_report import _dual_section, _frontier_section, _tied_points
from atlas.optimization_runner import _scenario_metrics, duals_frame, load_inputs, solver_settings
from atlas.optimizer import (
    LinearProgram,
    LPResult,
    Objective,
    OptimizationError,
    SolverSettings,
    SolveStatus,
    build_lp,
    certify,
    exact_matvec,
    objective_vector,
    solve_lp,
)
from atlas.planning import PlanningProblem, ServiceUnit, tolerance
from atlas.runner import load_series
from atlas.scenarios import Scenario, build_scenarios
from tests.conftest import REPO_ROOT

SETTINGS = SolverSettings()
CONFIGS = REPO_ROOT / "configs" / "optimization"
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
Method = Literal["highs-ds", "highs-ipm"] | None


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


def _scenario(scenario_id: str, problem: PlanningProblem) -> Scenario:
    total = math.fsum(d for u in problem.units for d in u.demand)
    return Scenario(
        scenario_id=scenario_id,
        description="Directly built scenario for the verification tests.",
        demand_basis=POINT_FORECAST,
        quantile=None,
        adjustments={},
        problem=problem,
        triggers={u.unit_id: 0.0 for u in problem.units},
        demand_sha256="0" * 64,
        base_demand_total=total,
    )


def _metrics(result: ScenarioResult) -> dict[str, Any]:
    return _scenario_metrics(result, _scenario(result.scenario_id, result.problem))


def _line(lines: list[str], scenario_id: str) -> str:
    return next(x for x in lines if x.startswith(f"- `{scenario_id}`:"))


def _has_row(lp: LinearProgram, name: str) -> bool:
    return any(row.constraint == name for row in lp.ub_rows)


# --- r4-solve-layer-01: statuses the dual simplex cannot conclude ---------------------------------


def _inconclusive(status: int) -> OptimizeResult:
    """What HiGHS returns for model status ``status`` without a verdict (SciPy code 4)."""
    result = OptimizeResult()
    result.update(status=4, message=f"(HiGHS Status {status}: injected)", x=None, nit=0)
    result["success"] = False
    return result


Calls = list[tuple[bool, Method]]


def _spy_linprog(
    monkeypatch: pytest.MonkeyPatch,
    fake: Callable[[LinearProgram, SolverSettings, bool, Method, int], OptimizeResult | None],
) -> Calls:
    """Route every HiGHS call through ``fake`` (``None``: solve for real); record each call's
    presolve flag and method (``None``: the configured dual simplex)."""
    real = optimizer._linprog
    calls: Calls = []

    def spy(
        lp: LinearProgram, settings: SolverSettings, presolve: bool, method: Method = None
    ) -> OptimizeResult:
        calls.append((presolve, method))
        result = fake(lp, settings, presolve, method, len(calls))
        return real(lp, settings, presolve, method) if result is None else result

    monkeypatch.setattr(optimizer, "_linprog", spy)
    return calls


def _small_lp() -> LinearProgram:
    return build_lp(_problem(budget=50.0))


@pytest.mark.parametrize("status", [0, 4, 10, 15])
def test_any_inconclusive_status_is_re_solved_without_presolve(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    # r4-solve-layer-01: only 9, 10 and 15 were re-solved without presolve; 0 (not set) and
    # 4 (solve error) raised at once and aborted the run. (A first 9 that the presolve-free
    # re-solve concludes is also caught by the infeasibility confirmation, so its own retry
    # is pinned below, where that re-solve fails too.)
    lp = _small_lp()
    plain = solve_lp(lp, SETTINGS)
    calls = _spy_linprog(monkeypatch, lambda *a: _inconclusive(status) if a[4] == 1 else None)
    result = solve_lp(lp, SETTINGS)
    assert calls == [(True, None), (False, None)]
    assert result.status is SolveStatus.OPTIMAL
    assert result.retried
    assert result.highs_status == 7
    assert "highs-ipm" not in result.message
    assert certify(lp, result).holds
    assert result.objective == plain.objective


@pytest.mark.parametrize("status", [0, 4])
def test_the_interior_point_method_is_the_last_resort_and_is_named(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    # r4-solve-layer-01: when the dual simplex fails with and without presolve, highs-ipm
    # (with crossover, no presolve) solves the programme; its certified optimum is used and
    # the message says which method produced it.
    lp = _small_lp()
    plain = solve_lp(lp, SETTINGS)
    calls = _spy_linprog(
        monkeypatch, lambda *a: None if a[3] == "highs-ipm" else _inconclusive(status)
    )
    result = solve_lp(lp, SETTINGS)
    assert calls == [(True, None), (False, None), (False, "highs-ipm")]
    assert result.status is SolveStatus.OPTIMAL
    assert result.retried
    assert result.highs_status == 7
    assert result.message.endswith(" [highs-ipm]")
    assert certify(lp, result).holds
    assert result.objective == pytest.approx(plain.objective, rel=1e-12, abs=1e-12)


def test_an_uncertified_interior_point_optimum_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # r4-solve-layer-01: the interior-point optimum is used only if the certificate holds on
    # the original programme; a shifted solution (balance rows violated by 1) raises.
    lp = _small_lp()
    real = optimizer._linprog

    def fake(*a: Any) -> OptimizeResult | None:
        if a[3] != "highs-ipm":
            return _inconclusive(0)
        result = real(a[0], a[1], a[2], a[3])
        result["x"] = result.x + 1.0
        return result

    _spy_linprog(monkeypatch, fake)
    with pytest.raises(OptimizationError, match=r"the highs-ipm optimum is not certified"):
        solve_lp(lp, SETTINGS)


def test_an_interior_point_infeasibility_verdict_is_infeasible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # r4-solve-layer-01: an infeasible strict programme on which the dual simplex fails both
    # ways (the big-M case of the reference configuration) is proved infeasible by highs-ipm,
    # so the scenario gets a relaxed plan instead of aborting the run.
    lp = build_lp(
        _problem(
            demand=(30.0, 0.0),
            regular_hours=(0.0, 0.0),
            overtime_max_hours=(0.0, 0.0),
            terminal_backlog_max=0.0,
        )
    )
    calls = _spy_linprog(monkeypatch, lambda *a: None if a[3] == "highs-ipm" else _inconclusive(4))
    result = solve_lp(lp, SETTINGS)
    assert calls == [(True, None), (False, None), (False, "highs-ipm")]
    assert result.status is SolveStatus.INFEASIBLE
    assert result.retried
    assert result.x is None


def test_a_status_no_method_concludes_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # r4-solve-layer-01: every method is tried once, and a status still inconclusive after
    # highs-ipm is a solver failure (an error), never a verdict.
    calls = _spy_linprog(monkeypatch, lambda *a: _inconclusive(0))
    with pytest.raises(OptimizationError, match=r"HiGHS did not finish \(status 4\)"):
        solve_lp(_small_lp(), SETTINGS)
    assert calls == [(True, None), (False, None), (False, "highs-ipm")]


def test_a_first_unbounded_or_infeasible_verdict_reaches_the_interior_point_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # r4-solve-layer-01: a first 9 (unbounded or infeasible) is inconclusive like 0 and 4: it
    # is re-solved without presolve and, when that fails too, by highs-ipm. Read as presolve's
    # infeasible verdict instead, the confirmation re-solve's failure kept the feasible
    # programme "infeasible".
    lp = _small_lp()
    statuses = {1: 9, 2: 0}
    calls = _spy_linprog(
        monkeypatch, lambda *a: _inconclusive(statuses[a[4]]) if a[4] in statuses else None
    )
    result = solve_lp(lp, SETTINGS)
    assert calls == [(True, None), (False, None), (False, "highs-ipm")]
    assert result.status is SolveStatus.OPTIMAL
    assert result.message.endswith(" [highs-ipm]")
    assert certify(lp, result).holds


def _seed_407() -> PlanningProblem:
    # The judge's near-tie fuzz seed 407 written out: one unit, 2 days, 87,000 and 67,000
    # requests, w_T = 5e8. HiGHS's dual simplex with presolve returns status 0 (not set) on
    # its strict programmes; the run aborted at solve_canonical.
    return PlanningProblem(
        date(2026, 7, 1),
        (
            _unit(
                unit_id="u0",
                demand=(87000.0, 67000.0),
                initial_backlog=6000.0,
                regular_hours=(12800.0, 9600.0),
                regular_productivity=2.5,
                overtime_productivity=2.25,
                overtime_max_hours=(4000.0, 36000.0),
                surge_max=(3000.0, 6000.0),
                regular_wage=20.0,
                overtime_wage=45.0,
                surge_price=40.0,
                backlog_penalty=20.4,
                terminal_penalty=5e8,
            ),
        ),
        14000.0,
    )


def _seed_340() -> PlanningProblem:
    # Fuzz seed 340 written out: two units, 5 days, w_T = 1e3 and 5e8. The dual simplex
    # returns status 0 on the strict and free programmes with and without presolve; only
    # highs-ipm solves them (certified).
    return PlanningProblem(
        date(2026, 7, 1),
        (
            _unit(
                unit_id="u0",
                demand=(40.0, 28.0, 89.0, 66.0, 70.0),
                initial_backlog=4.0,
                regular_hours=(16.0, 19.0, 9.0, 35.0, 0.0),
                regular_productivity=1.0,
                overtime_productivity=2.0,
                overtime_max_hours=(8.0, 0.0, 0.0, 0.0, 0.0),
                surge_max=(0.0, 9.0, 5.0, 0.0, 0.0),
                regular_wage=20.0,
                overtime_wage=45.0,
                surge_price=22.5,
                backlog_penalty=22.4999999775,
                terminal_penalty=1000.0,
            ),
            _unit(
                unit_id="u1",
                demand=(19000.0, 54000.0, 66000.0, 34000.0, 13000.0),
                initial_backlog=6000.0,
                regular_hours=(11000.0, 30000.0, 0.0, 26000.0, 0.0),
                regular_productivity=1.0,
                overtime_productivity=1.0,
                overtime_max_hours=(0.0, 22000.0, 15000.0, 3000.0, 0.0),
                surge_max=(0.0, 0.0, 4000.0, 5000.0, 10000.0),
                regular_wage=20.0,
                overtime_wage=45.0,
                surge_price=45.00045,
                backlog_penalty=45.00000004500001,
                terminal_penalty=5e8,
            ),
        ),
        59000.0,
    )


def _record_statuses(monkeypatch: pytest.MonkeyPatch) -> list[tuple[bool, Method, int | None]]:
    real = optimizer._linprog
    seen: list[tuple[bool, Method, int | None]] = []

    def spy(
        lp: LinearProgram, settings: SolverSettings, presolve: bool, method: Method = None
    ) -> OptimizeResult:
        result = real(lp, settings, presolve, method)
        seen.append((presolve, method, optimizer._highs_status(result)))
        return result

    monkeypatch.setattr(optimizer, "_linprog", spy)
    return seen


def test_a_two_day_big_m_problem_the_dual_simplex_cannot_conclude_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # r4-solve-layer-01 (sl01 seed 407): aborted with "HiGHS did not finish (status 4):
    # (HiGHS Status 0: Not Set)". The dual simplex without presolve concludes it.
    seen = _record_statuses(monkeypatch)
    result = analyse_scenario(
        "sl01_407", _seed_407(), SETTINGS, {"u0": 0.0}, shadow_steps=STEPS, frontier_points=3
    )
    # The instance still exercises the path (a HiGHS that concludes it directly would make
    # this test vacuous): status 0 with presolve, each followed by a presolve-free re-solve
    # that concludes, and no interior-point solve.
    first = [k for k, call in enumerate(seen) if call == (True, None, 0)]
    assert first
    assert all(seen[k + 1] == (False, None, 7) for k in first)
    assert not [call for call in seen if call[1] == "highs-ipm"]
    assert result.problems == ()
    assert result.feasibility == "strict"
    assert result.strict.require().result.retried
    assert result.reported.require().certificate.holds


def test_a_big_m_problem_only_the_interior_point_method_solves_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # r4-solve-layer-01 (sl01 seed 340): the dual simplex fails with and without presolve;
    # the certified highs-ipm optimum is the planning optimum, and its message says so.
    seen = _record_statuses(monkeypatch)
    result = analyse_scenario(
        "sl01_340",
        _seed_340(),
        SETTINGS,
        {"u0": 0.0, "u1": 0.0},
        shadow_steps=None,
        frontier_points=None,
    )
    assert (False, "highs-ipm", 7) in seen
    assert result.problems == ()
    assert result.feasibility == "strict"
    strict = result.strict.require()
    assert strict.result.message.endswith(" [highs-ipm]")
    assert strict.certificate.holds
    assert result.reported.require().check.feasible


def _big_m_reference_config(terminal_penalty: float) -> Any:
    """The reference configuration with only HYPOTHETICAL unit values changed (the
    verification's bigm_cfg_1e8.json): productivity 2.0, 800 overtime hours a day and no
    overtime total, overtime 66 USD/h, surge 36 USD, backlog 0.5 USD/request-day and the
    terminal penalty ``terminal_penalty``."""
    payload = json.loads((CONFIGS / "atlas-m4-optimization-v1.json").read_text(encoding="utf-8"))
    payload["experiment_id"] = "verification-big-m-terminal-penalty"
    unit = payload["service_units"][0]
    for key, value in (
        ("regular_productivity", 2.0),
        ("overtime_max_hours_per_day", 800),
        ("overtime_wage", 66),
        ("surge_price", 36),
        ("backlog_penalty", 0.5),
        ("terminal_penalty", terminal_penalty),
    ):
        unit[key]["value"] = value
    unit["overtime_total_hours"] = None
    return parse_optimization_config(payload)


@pytest.mark.integration
def test_a_big_m_terminal_penalty_at_nyc_scale_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    # r4-solve-layer-01: at 11,132 requests/day with w_T = 1e8 the strict programme of
    # `normal` and `combined_stress` gave HiGHS status 0 with and without presolve, and
    # `optimize run` exited with solver_failed and no run directory. highs-ipm proves the
    # strict programme infeasible; the scenario gets its relaxed plan without any ERROR.
    config = _big_m_reference_config(1e8)
    settings = load_settings(repo_root=REPO_ROOT, env={})
    _, data = load_series(settings, "verification_big_m")
    assert data is not None
    inputs = load_inputs(settings, config, data)
    dates, point = inputs.projection.first(config.horizon_days)
    scenarios = {
        s.scenario_id: s
        for s in build_scenarios(config, dates, point, inputs.quantiles, inputs.demand_percentile)
    }
    solver = solver_settings(config)
    seen = _record_statuses(monkeypatch)
    for scenario_id in ("normal", "combined_stress"):
        seen.clear()
        scenario = scenarios[scenario_id]
        result = analyse_scenario(
            scenario_id,
            scenario.problem,
            solver,
            scenario.triggers,
            shadow_steps=None,
            frontier_points=None,
        )
        assert seen[:3] == [(True, None, 0), (False, None, 0), (False, "highs-ipm", 8)]
        assert result.strict.status is SolveStatus.INFEASIBLE
        assert result.feasibility == "relaxed_terminal_target"
        assert result.problems == ()
        assert result.reported.require().certificate.holds


# --- r4-solve-layer-03 / r4-diagnosis-01: a failed re-solve is not a failed bracket ---------------

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
FAILING = ("balance", "u", 1)


def _fd_result(monkeypatch: pytest.MonkeyPatch | None, only: str | None) -> ScenarioResult:
    """The 14 duals of FD_UNIT; with ``monkeypatch``, the practical re-solves (+/-1 request or
    staff hour, +/-100 USD) of the duals whose label starts with ``only`` (every dual for
    ``""``) fail, as HiGHS status 4 did on the 28-day big-M instance of the finding."""
    if monkeypatch is not None:
        real = analysis._value_at

        def value_at(
            lp: LinearProgram, settings: SolverSettings, what: str, problems: list[str]
        ) -> tuple[float, LPResult | None]:
            step = float(what.rsplit(" ", 1)[-1][1:])  # "+0.001", "-1", "+100", ...
            if only is not None and what.startswith(only) and step >= 0.5:
                problems.append(f"{what}: injected solver failure")
                return math.nan, None
            return real(lp, settings, what, problems)

        monkeypatch.setattr(analysis, "_value_at", value_at)
    problem = PlanningProblem(date(2026, 7, 1), (_unit(**FD_UNIT),), 5000.0)
    return analyse_scenario(
        "fd", problem, SETTINGS, {"u": 0.0}, shadow_steps=STEPS, frontier_points=None
    )


def _bracket_cells(result: ScenarioResult) -> dict[tuple[str, str, int], str]:
    """duals.csv's bracket_ok cell per dual, as written."""
    text = csv_bytes(duals_frame([result], STATUS)).decode("utf-8")
    return {
        (row["constraint"], row["unit_id"], int(float(row["day"])) - 1 if row["day"] else -1): row[
            "bracket_ok"
        ]
        for row in csv.DictReader(io.StringIO(text))
    }


def test_a_failed_finite_difference_re_solve_leaves_its_bracket_untested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # r4-solve-layer-03 / r4-diagnosis-01: the NaN slope of a failed re-solve made every
    # comparison False, so the dual got bracket_ok False, an extra ERROR "outside its
    # finite-difference bracket", metrics brackets_ok False and "A BRACKET FAILED".
    # Control: without the failure the same report says "all brackets hold".
    clean = _line(_dual_section([_metrics(_fd_result(None, None))]), "fd")
    assert clean.startswith("- `fd`: 14 duals bracketed (all brackets hold); ")
    result = _fd_result(monkeypatch, "finite difference balance u 1 ")
    items = {fd.dual.key(): fd for fd in result.finite_differences}
    assert len(items) == 14
    failed = items[FAILING]
    assert failed.status == "solver_failed"
    assert (failed.bracket_ok, failed.convex_ok, failed.sign_ok) == (None, None, None)
    others = [fd for key, fd in items.items() if key != FAILING]
    assert all(fd.status != "solver_failed" for fd in others)
    assert all(fd.bracket_ok is fd.convex_ok is fd.sign_ok is True for fd in others)
    # Only the two re-solve errors are problems (ERRORs of the run).
    assert result.problems == (
        "finite difference balance u 1 +1: injected solver failure",
        "finite difference balance u 1 -1: injected solver failure",
    )
    cells = _bracket_cells(result)
    assert cells[FAILING] == ""
    assert {cell for key, cell in cells.items() if key != FAILING} == {"True"}
    prices = _metrics(result)["shadow_prices"]
    assert prices["brackets_ok"] is True
    assert prices["solver_failed"] == 1
    line = _line(_dual_section([_metrics(result)]), "fd")
    assert line.startswith("- `fd`: 14 duals bracketed (all computed brackets hold); ")
    assert line.endswith(", 1 not computed (solver failure).")


def test_when_every_finite_difference_fails_no_bracket_is_computed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # r4-diagnosis-01: brackets_ok is computed over the duals whose slopes exist; with none,
    # it is None ("no bracket could be computed"), not False.
    result = _fd_result(monkeypatch, "")
    assert len(result.finite_differences) == 14
    assert all(fd.status == "solver_failed" for fd in result.finite_differences)
    assert all(
        (fd.bracket_ok, fd.convex_ok, fd.sign_ok) == (None, None, None)
        for fd in result.finite_differences
    )
    assert len(result.problems) == 28
    assert not [p for p in result.problems if "outside its finite-difference bracket" in p]
    assert set(_bracket_cells(result).values()) == {""}
    prices = _metrics(result)["shadow_prices"]
    assert prices["brackets_ok"] is None
    assert prices["solver_failed"] == 14
    line = _line(_dual_section([_metrics(result)]), "fd")
    assert line.startswith("- `fd`: 14 duals bracketed (no bracket could be computed); ")


# --- r4-solve-layer-02: failed stages, least-penalty status and points without a plan -----------


def _frontier_problem(budget: float | None = None, **overrides: Any) -> PlanningProblem:
    return _problem(
        budget=budget,
        **{
            "demand": (30.0, 10.0),
            "regular_hours": (5.0, 5.0),
            "overtime_max_hours": (10.0, 10.0),
            "surge_max": (10.0, 10.0),
            "overtime_wage": 1.0,
            "surge_price": 2.0,
            "backlog_penalty": 3.0,
            "terminal_backlog_max": 15.0,
            **overrides,
        },
    )


def _failing(
    monkeypatch: pytest.MonkeyPatch, when: Callable[[LinearProgram], LPResult | None]
) -> None:
    """``solve_lp`` of the analyses gives ``when(lp)`` (an LPResult, or raises) where that is
    not ``None``, and solves for real otherwise."""
    real = optimizer.solve_lp

    def solve(lp: LinearProgram, settings: SolverSettings) -> LPResult:
        injected = when(lp)
        return real(lp, settings) if injected is None else injected

    monkeypatch.setattr(analysis, "solve_lp", solve)


def _raise(message: str) -> LPResult:
    raise OptimizationError(message)


def _frontier_tie_breaks_fail(monkeypatch: pytest.MonkeyPatch) -> ScenarioResult:
    # Every frontier point's terminal-backlog tie-break raises on its face, the face of exact
    # zeros and the epsilon cap (exact and loose caps); the least penalty is certified.
    _failing(
        monkeypatch,
        lambda lp: (
            _raise("injected: HiGHS Status 15")
            if lp.objective is Objective.TERMINAL_BACKLOG and _has_row(lp, "cap_frontier_cash")
            else None
        ),
    )
    return analyse_scenario(
        "f", _frontier_problem(), SETTINGS, {"u": 0.0}, shadow_steps=None, frontier_points=5
    )


def test_a_stage_without_a_plan_records_no_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    # r4-solve-layer-02: a failed tie-break kept stage_fallback "epsilon_cap", so it was
    # counted in checks.stage_fallbacks and warned as "its plan keeps the earlier optimum only
    # to within the epsilon cap", although no plan exists. Its failure is the ERROR.
    result = _frontier_tie_breaks_fail(monkeypatch)
    assert result.frontier is not None
    assert any("lexicographic stage terminal_backlog failed" in p for p in result.problems)
    assert result.frontier.fallbacks == ()
    assert result.fallbacks == ()
    assert _metrics(result)["stage_fallbacks"] == []
    # The same holds for the reported plan's own chain: its failed last stage has no fallback.
    _failing(
        monkeypatch,
        lambda lp: (
            _raise("injected: HiGHS Status 4") if lp.objective is Objective.BACKLOG_DAYS else None
        ),
    )
    canonical = solve_canonical(_problem(budget=50.0), SETTINGS)
    assert not canonical.plan.optimal
    assert canonical.plan.status is SolveStatus.FAILED
    assert canonical.plan.stage_fallback is None
    assert canonical.fallbacks == ()


def test_frontier_points_without_a_plan_are_counted_and_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # r4-solve-layer-02: points whose tie-break failed have no uniqueness test, yet the report
    # said "every point is unique after the tie-breaks" and metrics did not count them.
    result = _frontier_tie_breaks_fail(monkeypatch)
    assert result.frontier is not None
    failed = [p for p in result.frontier.points if p.status is SolveStatus.FAILED]
    assert len(failed) == 9
    assert all(p.unique_after_tie_breaks is None for p in failed)
    summary = _metrics(result)["frontier"]
    assert summary["points_solver_failed"] == 9
    assert summary["points_not_unique_after_tie_breaks"] == 0
    assert summary["least_penalty_programme_status"] == {"kept": "optimal", "dropped": "optimal"}
    line = _line(_frontier_section([_metrics(result)]), "f")
    assert line.endswith(
        "every tested point is unique after the tie-breaks; 9 points could not be computed "
        "(solver failure: no plan, no uniqueness test). DIAGNOSTIC tradeoff points, not budgets."
    )


def test_the_tied_points_sentence_counts_points_without_a_plan() -> None:
    # r4-solve-layer-02: the sentence for tied points also names the points without a plan.
    assert _tied_points({"points_not_unique_after_tie_breaks": 0}) == (
        "every point is unique after the tie-breaks"
    )
    assert _tied_points(
        {
            "points_not_unique_after_tie_breaks": 2,
            "points_with_overtime_or_surge_totals_open": 1,
            "points_solver_failed": 3,
        }
    ) == (
        "at 2 points other plans tie on penalty, cash, terminal backlog and request-days with "
        "a different daily schedule; the overtime or surge totals differ too at 1 of them; 3 "
        "points could not be computed (solver failure: no plan, no uniqueness test)"
    )


def _top(lp: LinearProgram, objective: Objective, kept: bool) -> bool:
    """A stage of a frontier variant's least-penalty programme (no budget row, no frontier or
    policy cap; its own epsilon cap included)."""
    return (
        lp.objective is objective
        and _has_row(lp, "terminal_backlog") is kept
        and not _has_row(lp, "budget")
        and not any(row.constraint.startswith(("cap_frontier", "cap_policy")) for row in lp.ub_rows)
    )


KEPT = "with the terminal target kept, "
DROPPED = "without the target, "
UNREACHABLE = "no plan meets the HYPOTHETICAL target at any cash cap"
NOT_SOLVED = "its least-penalty programme could not be solved (solver failure)"
FAILED_TOP: dict[str, Callable[[LinearProgram], LPResult | None]] = {
    # The target cannot be met at any cash: the least-penalty programme is infeasible.
    "unreachable": lambda lp: None,
    # The least-penalty programme itself raises.
    "kept_top_raises": lambda lp: (
        _raise("injected: HiGHS Status 4") if _top(lp, Objective.PENALTY, kept=True) else None
    ),
    # Its least-penalty stage is optimal, its least-cash stage comes back infeasible: a solver
    # failure (the earlier optimum lies on that face), not an unreachable target.
    "kept_cash_stage_infeasible": lambda lp: (
        LPResult(SolveStatus.INFEASIBLE, 2, "injected (HiGHS Status 8)", 0, 8)
        if _top(lp, Objective.DISCRETIONARY_CASH, kept=True)
        else None
    ),
    "dropped_top_raises": lambda lp: (
        _raise("injected: HiGHS Status 4") if _top(lp, Objective.PENALTY, kept=False) else None
    ),
}
EXPECTED_TOP = {
    "unreachable": ({"kept": "infeasible", "dropped": "optimal"}, KEPT + UNREACHABLE),
    "kept_top_raises": ({"kept": "solver_failed", "dropped": "optimal"}, KEPT + NOT_SOLVED),
    "kept_cash_stage_infeasible": (
        {"kept": "solver_failed", "dropped": "optimal"},
        KEPT + NOT_SOLVED,
    ),
    "dropped_top_raises": ({"kept": "optimal", "dropped": "solver_failed"}, DROPPED + NOT_SOLVED),
}


@pytest.mark.parametrize("case", sorted(FAILED_TOP))
def test_the_least_penalty_programme_status_separates_infeasible_from_failed(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    # r4-solve-layer-02: a failing least-penalty programme gave least cash None, which the
    # report read as "with the terminal target kept, no plan meets the HYPOTHETICAL target at
    # any cash cap" (kept) or "no point could be computed" (dropped).
    _failing(monkeypatch, FAILED_TOP[case])
    if case == "unreachable":  # 10 requests of capacity for 40 of demand, target 15
        problem = _frontier_problem(
            budget=1000.0, overtime_max_hours=(0.0, 0.0), surge_max=(0.0, 0.0)
        )
    else:
        problem = _frontier_problem(budget=1000.0)
    result = analyse_scenario(
        "f", problem, SETTINGS, {"u": 0.0}, shadow_steps=None, frontier_points=5
    )
    status, sentence = EXPECTED_TOP[case]
    assert result.frontier is not None
    assert dict(result.frontier.least_penalty_status) == status
    summary = _metrics(result)["frontier"]
    assert summary["least_penalty_programme_status"] == status
    for variant, verdict in status.items():
        assert (summary["least_cash_for_least_penalty"][variant] is None) is (verdict != "optimal")
    line = _line(_frontier_section([_metrics(result)]), "f")
    assert f"`f`: {sentence}; " in line or f"; {sentence}; " in line
    if case != "unreachable":
        assert UNREACHABLE not in line


# --- test-strength-03: the kept-optimum check rejects a plan above the optimum --------------------


def _defer_problem() -> PlanningProblem:
    # One unit, two days. Day 1: demand 1,000, no regular hours, overtime at 30 USD/h. Day 2:
    # 1,000 regular hours, no overtime. Carrying a request one night costs 30.0009, so the
    # unique optimum works 1,000 overtime hours on day 1: cash 30,000, score 30,000.
    return _problem(
        demand=(1000.0, 0.0),
        regular_hours=(0.0, 1000.0),
        overtime_max_hours=(2000.0, 0.0),
        overtime_wage=30.0,
        surge_price=30.0,
        backlog_penalty=30.0009,
    )


def _loose_faces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every face built with thresholds is loosened completely (no bound fixed, no row made
    tight), as a too-wide zero threshold would; the face of exact zeros is unchanged."""
    real = analysis.face_programme

    def loose(
        lp: LinearProgram, result: LPResult, *, zero_threshold: bool = False
    ) -> tuple[LinearProgram, float, int, int]:
        if zero_threshold:
            return real(lp, result, zero_threshold=True)
        return lp, 0.0, 0, 0

    monkeypatch.setattr(analysis, "face_programme", loose)


def test_the_kept_optimum_check_rejects_a_plan_above_the_optimum() -> None:
    # test-strength-03 (M08): loosening "10 * tolerance" in _keeps_optimum to "1e9 *
    # tolerance" survived every test; a plan scoring twice the optimum then passed.
    strict = solve_problem(_defer_problem(), SETTINGS).require()
    lp, optimum = strict.lp, strict.objective_value
    assert optimum == pytest.approx(30000.0, rel=1e-12)
    assert analysis._keeps_optimum(lp, optimum, strict.result)
    cash, _ = objective_vector(lp.problem, lp.layout, Objective.DISCRETIONARY_CASH)
    defer = solve_lp(
        replace(lp, objective=Objective.DISCRETIONARY_CASH, c=cash, constant=0.0), SETTINGS
    )
    assert defer.x is not None
    assert lp.value(defer.x) == pytest.approx(60001.8, rel=1e-9)
    assert not analysis._keeps_optimum(lp, optimum, defer)
    # The rule itself: a plan more than 10 x the checking tolerance above the optimum is
    # rejected, one within it is kept.
    assert strict.result.x is not None
    magnitude = math.fsum(np.abs(lp.c * strict.result.x).tolist())
    tol = tolerance(magnitude, optimum)
    assert not analysis._keeps_optimum(lp, optimum - 20 * tol, strict.result)
    assert analysis._keeps_optimum(lp, optimum - 5 * tol, strict.result)


def test_a_loose_face_falls_back_to_exact_zeros_and_keeps_the_optimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # test-strength-03 (M08): with the thresholded face loosened, the least-cash plan on it
    # defers day 1 (cash 0, score 60,001.8); the check must reject it, so the stage falls back
    # to the face of exact zeros and the face test is rebuilt, unique with no problem.
    strict = solve_problem(_defer_problem(), SETTINGS).require()
    _loose_faces(monkeypatch)
    cash, _ = objective_vector(strict.lp.problem, strict.lp.layout, Objective.DISCRETIONARY_CASH)
    restricted, solution, flag = minimise_on_face(
        strict.lp, strict.result, SETTINGS, Objective.DISCRETIONARY_CASH, cash
    )
    assert flag == "zero_threshold"
    assert solution.x is not None
    assert strict.lp.value(solution.x) == pytest.approx(30000.0, rel=1e-12)
    assert restricted.value(solution.x) == pytest.approx(30000.0, rel=1e-12)
    face = optimal_face(_defer_problem(), SETTINGS, strict)
    assert face.problems == ()
    assert face.rebuilt
    assert face.unique


# --- test-strength-07: the epsilon cap is exactly lex_cap of the optimum --------------------------


def test_the_epsilon_cap_is_exactly_lex_cap_of_the_optimum(monkeypatch: pytest.MonkeyPatch) -> None:
    # test-strength-07 (M11): the cap value was asserted with pytest.approx (rel 1e-6), which
    # cannot tell lex_cap(optimum) from the optimum itself; an exact cap is the round-1
    # failure mode (HiGHS calls the capped programme infeasible at scale).
    strict = solve_problem(_problem(budget=50.0), SETTINGS).require()
    lp = strict.lp
    assert strict.result.x is not None
    optimum = lp.value(strict.result.x)
    monkeypatch.setattr(analysis, "_keeps_optimum", lambda *_args: False)
    cash, _ = objective_vector(lp.problem, lp.layout, Objective.DISCRETIONARY_CASH)
    capped, solution, flag = minimise_on_face(
        lp, strict.result, SETTINGS, Objective.DISCRETIONARY_CASH, cash
    )
    assert flag == "epsilon_cap"
    assert solution.status is SolveStatus.OPTIMAL
    assert capped.ub_rows[-1].constraint == "cap_weighted"
    assert np.array_equal(capped.a_ub[-1], lp.c)
    assert capped.b_ub[-1] == analysis.lex_cap(optimum) - lp.constant
    assert capped.b_ub[-1] > optimum - lp.constant


# --- r4-suite-02: the optimal face holds an optimum that is feasible only within tolerance --------


def test_the_face_widens_every_row_to_an_optimum_that_violates_it() -> None:
    # r4-suite-02 (M05): serving at full capacity leaves 5 requests against a target of
    # 5 - 1e-9, which HiGHS accepts at its tolerance. Without the widening the face excludes
    # the optimum; since presolve-free re-solves rescue that instance, the widening is
    # asserted on face_programme directly.
    problem = _problem(
        demand=(10.0,),
        regular_hours=(0.0,),
        overtime_max_hours=(5.0,),
        surge_max=(0.0,),
        terminal_backlog_max=5.0 - 1e-9,
    )
    strict = solve_problem(problem, SETTINGS).require()
    lp, x = strict.lp, strict.x
    activity = exact_matvec(lp.a_ub, x)
    row = next(i for i, r in enumerate(lp.ub_rows) if r.constraint == "terminal_backlog")
    assert 0.0 < activity[row] - lp.b_ub[row] < 1e-8  # the optimum violates the row
    face, _, _, tight = face_programme(lp, strict.result)
    rows = len(lp.ub_rows)
    assert np.array_equal(face.b_ub[:rows], np.maximum(lp.b_ub, activity))
    assert face.b_ub[row] == activity[row]
    assert face.b_ub[row] > lp.b_ub[row]
    assert len(face.ub_rows) == rows + tight
    assert np.all(exact_matvec(face.a_ub, x) <= face.b_ub)


# --- test-strength-06: exact fallback and problem accounting --------------------------------------


def _fallback_problem() -> PlanningProblem:
    # The defer instance with 1,000 requests on each day (round-3 demo_fallbacks.py).
    return _problem(
        demand=(1000.0, 1000.0),
        regular_hours=(0.0, 1000.0),
        overtime_max_hours=(2000.0, 0.0),
        overtime_wage=30.0,
        surge_price=30.0,
        backlog_penalty=30.0009,
    )


def test_every_fallback_source_of_a_scenario_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    # test-strength-06 (M26, M32, M33): with every thresholded face loose, each lexicographic
    # stage and face test needs its fallback. Each finite difference records the least-cash
    # fallback of its forward step, the frontier its stages' and face tests', and the
    # scenario all of them.
    _loose_faces(monkeypatch)
    result = analyse_scenario(
        "demo", _fallback_problem(), SETTINGS, {"u": 0.0}, shadow_steps=STEPS, frontier_points=3
    )
    assert result.problems == ()
    assert [fd.stage_fallback for fd in result.finite_differences] == ["zero_threshold"] * 8
    assert result.frontier is not None
    assert Counter(result.frontier.fallbacks) == {"zero_threshold": 9, "face_rebuilt": 2}
    assert Counter(result.fallbacks) == {"zero_threshold": 22, "face_rebuilt": 3}
    assert _metrics(result)["stage_fallbacks"] == list(result.fallbacks)


def test_the_base_point_fallback_attaches_to_the_first_dual_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # test-strength-06 (M27): without base_cash, the base point's least-cash fallback is
    # recorded once, on the first dual, not repeated on every dual.
    strict = solve_problem(_problem(), SETTINGS).require()
    real = analysis._keeps_optimum
    calls: list[int] = []

    def first_rejected(*args: Any) -> bool:
        calls.append(1)
        return False if len(calls) == 1 else real(*args)

    monkeypatch.setattr(analysis, "_keeps_optimum", first_rejected)
    differences = finite_differences(strict, SETTINGS, STEPS)
    assert len(differences) == 8
    assert [fd.stage_fallback for fd in differences] == ["zero_threshold"] + [None] * 7


def test_a_repeated_failed_stage_counts_its_problem_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # test-strength-06 (M57): a stage that comes back infeasible is repeated for the remaining
    # tie-breaks (the same attempt); its problem is one ERROR, not one per repetition.
    real = analysis.minimise_on_face

    def no_plan(*args: Any, **kwargs: Any) -> tuple[LinearProgram, LPResult, str | None]:
        restricted, solution, flag = real(*args, **kwargs)
        return restricted, replace(solution, status=SolveStatus.INFEASIBLE, x=None), flag

    monkeypatch.setattr(analysis, "minimise_on_face", no_plan)
    canonical = solve_canonical(_problem(), SETTINGS)
    assert [str(s.status) for s in canonical.stages] == ["optimal"] + ["infeasible"] * 3
    assert canonical.stages[1] is canonical.stages[2] is canonical.stages[3]
    assert canonical.problems == (
        "lexicographic stage discretionary_cash is infeasible although the earlier optimum "
        "lies on its face",
    )


@dataclass(frozen=True)
class LooseSmoke:
    """The smoke run computed with every thresholded face loose, and what it was made of."""

    payload: dict[str, Any]
    issues: tuple[Any, ...]
    results: tuple[ScenarioResult, ...]
    sweep_points: tuple[Any, ...]


@pytest.fixture(scope="module")
def loose_smoke() -> LooseSmoke:
    results: list[ScenarioResult] = []
    sweeps: list[Any] = []
    real_analyse, real_sweep = analysis.analyse_scenario, analysis.run_sweep

    def analyse(*args: Any, **kwargs: Any) -> ScenarioResult:
        results.append(real_analyse(*args, **kwargs))
        return results[-1]

    def sweep(*args: Any, **kwargs: Any) -> Any:
        sweeps.append(real_sweep(*args, **kwargs))
        return sweeps[-1]

    with pytest.MonkeyPatch.context() as patch:
        _loose_faces(patch)
        patch.setattr(runner, "analyse_scenario", analyse)
        patch.setattr(runner, "run_sweep", sweep)
        config = load_optimization_config(CONFIGS / "smoke.json")
        settings = load_settings(repo_root=REPO_ROOT, env={})
        _, data = load_series(settings, "verification_loose_faces")
        assert data is not None
        outputs = runner.m4_outputs(settings, config, load_inputs(settings, config, data))
    return LooseSmoke(
        json.loads(outputs.files["metrics.json"]),
        outputs.issues,
        tuple(results),
        tuple(point for points, _ in sweeps for point in points),
    )


@pytest.mark.integration
def test_forced_fallbacks_on_the_smoke_run_are_counted_exactly(loose_smoke: LooseSmoke) -> None:
    # test-strength-06 (M26, M32, M34, M36): checks.stage_fallbacks is the sum of the
    # per-scenario and per-sweep-point lists, each list is the analysis's own, and the
    # finite-difference, frontier and sweep sources are each counted.
    payload, results = loose_smoke.payload, loose_smoke.results
    scenarios = payload["scenarios"]
    points = payload["sensitivity"]["points"]
    assert [s["scenario_id"] for s in scenarios] == [r.scenario_id for r in results]
    for summary, result in zip(scenarios, results, strict=True):
        assert summary["stage_fallbacks"] == list(result.fallbacks)
    assert [len(s["stage_fallbacks"]) for s in scenarios] == [69, 7, 8, 9]
    per_point = [len(p["stage_fallbacks"]) for p in points]
    assert per_point == [len(p.fallbacks) for p in loose_smoke.sweep_points]
    assert sum(per_point) == 28
    assert payload["checks"]["stage_fallbacks"] == 121 == 69 + 7 + 8 + 9 + 28
    normal = results[0]
    assert normal.scenario_id == "normal"
    assert len(normal.finite_differences) == 43
    assert all(fd.stage_fallback == "zero_threshold" for fd in normal.finite_differences)
    assert normal.frontier is not None
    assert Counter(normal.frontier.fallbacks) == {"zero_threshold": 18}
    assert Counter(normal.fallbacks) == {"zero_threshold": 68, "face_rebuilt": 1}
    assert all(
        p.fallbacks in ((), ("zero_threshold", "face_rebuilt")) for p in loose_smoke.sweep_points
    )


@pytest.mark.integration
def test_forced_fallbacks_on_the_smoke_run_give_one_warning_each(loose_smoke: LooseSmoke) -> None:
    # test-strength-06 (M35, M37): one stage_fallback WARNING (not an ERROR) per scenario and
    # per sweep point that used a fallback, and the run stays complete (no ERROR).
    warned = [i for i in loose_smoke.issues if i.code == "stage_fallback"]
    assert all(i.severity is Severity.WARNING for i in warned)
    expected = [r.scenario_id for r in loose_smoke.results if r.fallbacks] + [
        f"sensitivity:{p.parameter}={p.value:g}" for p in loose_smoke.sweep_points if p.fallbacks
    ]
    assert len(expected) == 4 + 14
    assert [i.subject for i in warned] == expected
    assert not [i for i in loose_smoke.issues if i.severity is Severity.ERROR]
    assert loose_smoke.payload["checks"]["errors"] == 0
    assert all(r.problems == () for r in loose_smoke.results)
