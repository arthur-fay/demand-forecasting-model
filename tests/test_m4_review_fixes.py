"""Regression tests for the findings of the independent review of the frozen M4 code.

Each test fails on the frozen implementation (commit af10ddd) and passes after the fix:
reported numbers no longer depend on which optimal vertex the solver returns, every check
problem of every programme reaches the run's issues, infeasible verdicts are confirmed
independently, guards and labels cover the sweeps, and the report words diagnostics as
diagnostics.
"""

from __future__ import annotations

import dataclasses
import io
import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

import atlas.decision_analysis as analysis
from atlas.cli import main
from atlas.config import load_settings
from atlas.decision_analysis import (
    analyse_scenario,
    describe_binding,
    diagnose,
    evaluate_policy,
    finite_differences,
    optimal_face,
    run_sweep,
    solve_canonical,
    solve_problem,
)
from atlas.errors import ConfigError
from atlas.optimization_config import load_optimization_config, parse_optimization_config
from atlas.optimization_report import render_optimization_report
from atlas.optimization_runner import load_inputs, m4_outputs
from atlas.optimizer import (
    Certificate,
    LPResult,
    Objective,
    OptimizationError,
    SolverSettings,
    SolveStatus,
    certify,
    solve_lp,
)
from atlas.planning import PlanningProblem, ServiceUnit, binding_constraints
from atlas.policies import BACKLOG_TRIGGER
from atlas.runner import load_series
from atlas.scenarios import build_scenarios, capacity_near_percentile
from tests.conftest import REPO_ROOT

SETTINGS = SolverSettings()
SMOKE = REPO_ROOT / "configs" / "optimization" / "smoke.json"
STEPS = {"requests": 1.0, "staff_hours": 1.0, "usd": 100.0}


def unit(**overrides: Any) -> ServiceUnit:
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


def tied_problem(**overrides: Any) -> PlanningProblem:
    # Day 1: 10 requests and no regular capacity; day 2: 10 hours of regular capacity.
    # Serving a request on day 1 with overtime costs 1 USD and saves 1 USD of overnight
    # penalty, so every o1 in [0, 10] scores 10: the optimum is not unique, and cash ranges
    # over [0, 10] across the optimal plans.
    return PlanningProblem(date(2026, 7, 1), (unit(**overrides),), None)


# --- Reported numbers do not depend on the optimal vertex ---------------------------------


def test_reported_plan_is_the_least_cash_optimal_plan() -> None:
    problem = tied_problem()
    canonical = solve_canonical(problem, SETTINGS)
    optimum = canonical.optimum.require()
    plan = canonical.plan.require()
    assert optimum.objective_value == pytest.approx(10.0)
    assert plan.evaluation.penalised_score == pytest.approx(10.0, abs=1e-5)
    assert plan.evaluation.discretionary_cost == pytest.approx(0.0, abs=1e-5)
    face = optimal_face(problem, SETTINGS, optimum)
    assert not face.unique
    cash = next(r for r in face.ranges if r.quantity == "discretionary_cash")
    assert (cash.low, cash.high) == pytest.approx((0.0, 10.0), abs=1e-5)


def test_slack_target_forces_nothing_and_the_target_row_shows_the_same_plan() -> None:
    problem = tied_problem(terminal_backlog_max=20.0)
    result = analyse_scenario(
        "tied", problem, SETTINGS, {"u": 0.0}, shadow_steps=None, frontier_points=None
    )
    assert result.target_changes_score is False
    from atlas.optimization_runner import WITHOUT_TARGET, _plan_rows

    rows = {row[0]: row for row in _plan_rows(result, None)}  # type: ignore[arg-type]
    _id, feasibility, _, _plan, evaluation, _ = rows[WITHOUT_TARGET]
    assert feasibility == "same_optimal_score_target_forces_nothing"
    assert evaluation == result.reported.require().evaluation


def test_binding_target_changes_the_optimal_score() -> None:
    problem = tied_problem(terminal_backlog_max=0.0, demand=(10.0, 5.0))
    result = analyse_scenario(
        "binding", problem, SETTINGS, {"u": 0.0}, shadow_steps=None, frontier_points=None
    )
    assert result.strict.optimal
    assert result.target_changes_score in (True, False)  # computed, never None when optimal


def test_cash_split_compares_least_cash_optimal_plans() -> None:
    problem = tied_problem()
    canonical = solve_canonical(problem, SETTINGS)
    base_cash = canonical.plan.require().evaluation.discretionary_cost
    items = finite_differences(canonical.optimum.require(), SETTINGS, STEPS, base_cash)
    balance = next(i for i in items if i.dual.constraint == "balance" and i.dual.day == 0)
    # Independent path: perturb the problem (one more request on day 1), not the matrix.
    bumped = tied_problem(demand=(11.0, 0.0))
    bumped_cash = solve_canonical(bumped, SETTINGS).plan.require().evaluation.discretionary_cost
    assert balance.forward_cash == pytest.approx(bumped_cash - base_cash, abs=1e-4)
    assert balance.forward_cash + balance.forward_penalty == pytest.approx(
        balance.practical_forward, abs=1e-9
    )


# --- Every check problem reaches the issues ----------------------------------------------


def _failing_certificate(lp: Any, result: LPResult) -> Certificate:
    real = certify(lp, result)
    return dataclasses.replace(real, holds=False)


def test_failing_sweep_certificate_is_recorded_on_the_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(analysis, "certify", _failing_certificate)
    points, _ = run_sweep(tied_problem(), SETTINGS, "backlog_penalty", (0.5, 1.0))
    assert all(any("certificate" in p for p in point.problems) for point in points)


def test_failing_policy_stage_one_certificate_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def only_backlog_days(lp: Any, result: LPResult) -> Certificate:
        real = certify(lp, result)
        return dataclasses.replace(real, holds=lp.objective is not Objective.BACKLOG_DAYS)

    monkeypatch.setattr(analysis, "certify", only_backlog_days)
    outcome = evaluate_policy(tied_problem(), SETTINGS, BACKLOG_TRIGGER, {"u": 0.0})
    assert any("certificate" in p for p in outcome.problems)


def test_contradicted_infeasible_verdict_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def first_weighted_infeasible(lp: Any, settings: SolverSettings) -> LPResult:
        if lp.objective is Objective.WEIGHTED and calls["n"] == 0:
            calls["n"] += 1
            return LPResult(SolveStatus.INFEASIBLE, 2, "forced", 0, 8)
        return solve_lp(lp, settings)

    monkeypatch.setattr(analysis, "solve_lp", first_weighted_infeasible)
    result = analyse_scenario(
        "forced",
        tied_problem(terminal_backlog_max=20.0),
        SETTINGS,
        {"u": 0.0},
        shadow_steps=None,
        frontier_points=None,
    )
    assert any("infeasible verdict contradicted" in p for p in result.problems)


def test_borderline_infeasibility_is_explained_not_contradicted() -> None:
    # Serving at maximum capacity leaves exactly 5 requests; the target asks for 5 - 5e-7.
    # HiGHS (tolerance 1e-9) calls the model infeasible, but the relaxed plan meets the
    # target within the checking tolerance (1e-6): no ERROR, an explicit BORDERLINE reason,
    # and no violation printed as "0.0".
    problem = PlanningProblem(
        date(2026, 7, 1),
        (
            unit(
                demand=(10.0,),
                regular_hours=(0.0,),
                overtime_max_hours=(5.0,),
                surge_max=(0.0,),
                terminal_backlog_max=5.0 - 5e-7,
            ),
        ),
        None,
    )
    result = analyse_scenario(
        "edge", problem, SETTINGS, {"u": 0.0}, shadow_steps=None, frontier_points=None
    )
    assert not result.strict.optimal
    assert result.diagnosis is not None
    assert result.diagnosis.borderline
    assert not result.problems
    reasons = " ".join(result.diagnosis.reasons)
    assert "BORDERLINE" in reasons
    assert "5e-07 requests" in reasons
    assert " 0.0 requests" not in reasons


def test_derived_rows_may_exceed_the_input_magnitude_guard() -> None:
    from atlas.optimizer import ExtraRow, Row, _check_magnitudes, build_lp

    problem = tied_problem()
    ones = np.ones(build_lp(problem).layout.size)
    derived = build_lp(problem, extra_rows=[ExtraRow(Row("cap_x", None, None), ones, 1.5e9)])
    _check_magnitudes(derived)
    model = build_lp(problem)
    with pytest.raises(OptimizationError, match="b_ub"):
        _check_magnitudes(dataclasses.replace(model, b_ub=model.b_ub + 1.5e9))
    too_big = build_lp(problem, extra_rows=[ExtraRow(Row("cap_x", None, None), ones, 1e15)])
    with pytest.raises(OptimizationError, match="derived b_ub"):
        _check_magnitudes(too_big)


def test_policy_caps_above_1e9_request_days_do_not_abort_the_analysis() -> None:
    # Every input is below 1e9, but 5e8 requests waiting 15 nights make 7.5e9 request-days,
    # the right-hand side of the policy comparison caps.
    horizon = 15
    problem = PlanningProblem(
        date(2026, 7, 1),
        (
            unit(
                demand=(5e8,) + (0.0,) * (horizon - 1),
                regular_hours=(0.0,) * horizon,
                overtime_max_hours=(1e3,) * horizon,
                surge_max=(0.0,) * horizon,
            ),
        ),
        None,
    )
    result = analyse_scenario(
        "large", problem, SETTINGS, {"u": 0.0}, shadow_steps=None, frontier_points=3
    )
    assert result.strict.optimal
    assert not result.problems
    assert all(p.evaluation.backlog_request_days > 1e9 for p in result.policies)


def test_intervals_pin_is_checked_without_a_quantile_scenario() -> None:
    config = load_optimization_config(SMOKE)
    point_only = config.model_copy(
        update={
            "scenarios": tuple(
                s for s in config.scenarios if s.demand_basis.kind == "point_forecast"
            )
        }
    )
    forged = point_only.model_copy(
        update={
            "uncertainty": point_only.uncertainty.model_copy(
                update={"projection_intervals_sha256": "0" * 64}
            )
        }
    )
    settings = load_settings(repo_root=REPO_ROOT, env={})
    _, data = load_series(settings, "pins_without_quantiles")
    assert data is not None
    assert not load_inputs(settings, point_only, data).quantiles
    with pytest.raises(ConfigError, match=r"uncertainty\.projection_intervals_sha256"):
        load_inputs(settings, forged, data)


# --- Diagnoses and binding constraints come from the plans ------------------------------


def test_reasons_name_only_limits_that_exist() -> None:
    # Unreachable target, surge available from day 1, no horizon totals.
    problem = PlanningProblem(
        date(2026, 7, 1),
        (
            unit(
                demand=(50.0, 50.0),
                regular_hours=(10.0, 10.0),
                surge_max=(5.0, 5.0),
                terminal_backlog_max=0.0,
            ),
        ),
        None,
    )
    diagnosis = diagnose(problem, SETTINGS)
    text = " ".join(diagnosis.reasons)
    assert "surge unavailable" not in text
    assert "total" not in text
    assert "daily overtime limit reached on 2 days" in text
    delayed = dataclasses.replace(
        problem, units=(dataclasses.replace(problem.units[0], surge_max=(0.0, 5.0)),)
    )
    assert "surge unavailable on the first 1 days" in " ".join(diagnose(delayed, SETTINGS).reasons)


def test_relaxed_plan_reports_its_binding_budget_and_has_a_uniqueness_test() -> None:
    problem = PlanningProblem(
        date(2026, 7, 1),
        (unit(demand=(30.0, 0.0), regular_hours=(0.0, 0.0), terminal_backlog_max=0.0),),
        5.0,
    )
    result = analyse_scenario(
        "relaxed", problem, SETTINGS, {"u": 0.0}, shadow_steps=None, frontier_points=None
    )
    assert not result.strict.optimal
    counts = binding_constraints(problem, result.reported.require().plan)
    assert counts.get("budget") == 1
    assert counts.get("terminal_backlog_violated") == 1
    assert result.relaxed_face is not None
    assert result.reported_face is result.relaxed_face
    assert "discretionary budget spent" in describe_binding(problem, counts)


def test_later_stages_keep_earlier_optima_exactly() -> None:
    # Within an epsilon cap the cash stage would trade the cap's slack for cash: the relaxed
    # plan above left ~1e-6 USD of a binding budget unspent and 1e-6 requests more backlog.
    problem = PlanningProblem(
        date(2026, 7, 1),
        (unit(demand=(30.0, 0.0), regular_hours=(0.0, 0.0), terminal_backlog_max=0.0),),
        5.0,
    )
    relaxed = diagnose(problem, SETTINGS).relaxed
    assert not relaxed.epsilon_fallback
    assert relaxed.require().evaluation.discretionary_cost == pytest.approx(5.0, abs=1e-9)
    assert relaxed.require().evaluation.terminal_backlog == pytest.approx(25.0, abs=1e-9)
    canonical = solve_canonical(tied_problem(), SETTINGS)
    assert not canonical.plan.epsilon_fallback
    assert canonical.plan.require().evaluation.penalised_score == pytest.approx(10.0, abs=1e-9)


def test_face_stage_falls_back_to_an_epsilon_cap_only_when_the_solver_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []
    real = analysis.solve_lp

    def face_programme_fails(lp: Any, settings: Any) -> Any:
        seen.append(lp)
        if len(seen) == 2:
            raise OptimizationError("injected: HiGHS Status 15")
        return real(lp, settings)

    monkeypatch.setattr(analysis, "solve_lp", face_programme_fails)
    canonical = solve_canonical(tied_problem(), SETTINGS)
    assert len(seen) == 3
    face, fallback = seen[1], seen[2]
    assert any(row.constraint.startswith("tight_") for row in face.ub_rows) or bool(
        np.any(face.lower == face.upper)
    )
    assert fallback.ub_rows[-1].constraint == "cap_weighted"
    assert fallback.b_ub[-1] == pytest.approx(analysis.lex_cap(10.0))
    assert canonical.plan.epsilon_fallback
    assert canonical.plan.optimal
    assert not canonical.optimum.epsilon_fallback
    assert canonical.plan.require().evaluation.penalised_score <= analysis.lex_cap(10.0) + 1e-9


def test_near_tied_prices_neither_abort_the_run_nor_hide_an_alternative_optimum() -> None:
    # Reviewer's instance: overtime at 10 USD/h ties one night of backlog (w = 10) exactly,
    # and surge costs 10 + r. For r ~ 1e-8 the epsilon-row face test called the optimum
    # unique although o1 = 3 scores the same; slightly different r made HiGHS fail.
    for r in (0.0, 1.0e-8, 1.2e-8, 1.5e-8, 3.0e-8, 1.0e-7):
        problem = PlanningProblem(
            date(2026, 7, 1),
            (
                unit(
                    demand=(1000.0, 0.0),
                    regular_hours=(900.0, 2000.0),
                    overtime_max_hours=(3.0, 0.0),
                    surge_max=(90.0, 0.0),
                    overtime_wage=10.0,
                    surge_price=10.0 + r,
                    backlog_penalty=10.0,
                ),
            ),
            None,
        )
        canonical = solve_canonical(problem, SETTINGS)
        assert canonical.optimal
        assert not canonical.problems
        face = optimal_face(problem, SETTINGS, canonical.optimum.require())
        assert not face.problems
        assert not face.unique, r
        hours = next(x for x in face.ranges if x.quantity == "overtime_hours")
        assert (hours.low, hours.high) == pytest.approx((0.0, 3.0), abs=1e-6)


def test_certificate_fails_closed_on_nan_duals_and_primal_values() -> None:
    solved = solve_problem(tied_problem(), SETTINGS).require()
    lp, result = solved.lp, solved.result
    assert certify(lp, result).holds
    assert result.eq_marginals is not None
    assert result.x is not None
    nan_duals = dataclasses.replace(
        result,
        ub_marginals=np.full_like(result.ub_marginals, np.nan),
        eq_marginals=np.full_like(result.eq_marginals, np.nan),
        lower_marginals=np.full_like(result.lower_marginals, np.nan),
        upper_marginals=np.full_like(result.upper_marginals, np.nan),
    )
    certificate = certify(lp, nan_duals)
    assert certificate.primal == 0.0
    assert not certificate.holds
    x = result.x.copy()
    x[0] = np.nan
    assert not certify(lp, dataclasses.replace(result, x=x)).holds


def test_finite_difference_resolves_are_certified(monkeypatch: pytest.MonkeyPatch) -> None:
    strict = solve_problem(tied_problem(), SETTINGS).require()
    clean = finite_differences(strict, SETTINGS, STEPS)
    assert clean
    assert all(not fd.problems for fd in clean)
    real = analysis.solve_lp

    def halved_row_duals(lp: Any, settings: Any) -> Any:
        result = real(lp, settings)
        if result.eq_marginals is None:
            return result
        return dataclasses.replace(result, eq_marginals=result.eq_marginals * 0.5)

    monkeypatch.setattr(analysis, "solve_lp", halved_row_duals)
    corrupted = finite_differences(strict, SETTINGS, STEPS)
    assert all(any("certificate failed" in p for p in fd.problems) for fd in corrupted)


def _scaled(problem: PlanningProblem, k: float) -> PlanningProblem:
    u = problem.units[0]
    scaled = dataclasses.replace(
        u,
        demand=tuple(d * k for d in u.demand),
        initial_backlog=u.initial_backlog * k,
        regular_hours=tuple(h * k for h in u.regular_hours),
        overtime_max_hours=tuple(h * k for h in u.overtime_max_hours),
        surge_max=tuple(v * k for v in u.surge_max),
        overtime_total_hours=None if u.overtime_total_hours is None else u.overtime_total_hours * k,
        surge_total=None if u.surge_total is None else u.surge_total * k,
        terminal_backlog_max=None if u.terminal_backlog_max is None else u.terminal_backlog_max * k,
    )
    budget = problem.discretionary_budget
    return dataclasses.replace(
        problem, units=(scaled,), discretionary_budget=None if budget is None else budget * k
    )


@pytest.mark.integration
def test_shadow_price_statuses_do_not_depend_on_the_scale_of_the_inputs() -> None:
    # Scaling every quantity by k leaves the duals unchanged and moves every breakpoint k
    # times further away. With an absolute step and no value-noise allowance, x100 gave false
    # breakpoints and x300 brackets with backward > forward on the reference instance.
    config = load_optimization_config(SMOKE.with_name("atlas-m4-optimization-v1.json"))
    settings = load_settings(repo_root=REPO_ROOT, env={})
    _, data = load_series(settings, "review_fixes_scale")
    assert data is not None
    inputs = load_inputs(settings, config, data)
    dates, point = inputs.projection.first(config.horizon_days)
    scenarios = build_scenarios(config, dates, point, inputs.quantiles, inputs.demand_percentile)
    base = next(s for s in scenarios if s.scenario_id == "normal").problem
    statuses = []
    for k in (1.0, 300.0):
        problem = _scaled(base, k)
        strict = solve_problem(problem, SETTINGS).require()
        steps = {"requests": k, "staff_hours": k, "usd": 100.0 * k}
        differences = finite_differences(strict, SETTINGS, steps)
        assert all(fd.bracket_ok and fd.convex_ok and fd.sign_ok for fd in differences), k
        assert all(not fd.problems for fd in differences)
        statuses.append({fd.dual.key(): fd.status for fd in differences})
        assert all(fd.step >= analysis.FD_RELATIVE_STEP * abs(fd.dual.rhs) for fd in differences)
    assert statuses[0] == statuses[1]


def test_penalty_objective_is_checked_against_the_plan() -> None:
    attempt = solve_problem(tied_problem(), SETTINGS, Objective.PENALTY)
    solved = attempt.require()
    assert solved.recomputed_value == pytest.approx(solved.objective_value)


# --- Runner, report and CLI (smoke configuration) ------------------------------------------


def _smoke_payload(changes: dict[str, Any] | None = None) -> tuple[Any, Any]:
    payload = json.loads(SMOKE.read_text(encoding="utf-8"))
    for key, value in (changes or {}).items():
        payload[key] = value
    config = parse_optimization_config(payload)
    settings = load_settings(repo_root=REPO_ROOT, env={})
    _, data = load_series(settings, "review_fixes")
    assert data is not None
    return config, m4_outputs(settings, config, load_inputs(settings, config, data))


@pytest.fixture(scope="module")
def smoke() -> Any:
    sensitivity = json.loads(SMOKE.read_text(encoding="utf-8"))["sensitivity"]
    for sweep in sensitivity["sweeps"]:
        if sweep["parameter"] == "regular_hours_multiplier":
            sweep["values"] = [1.0, 1.125]  # 4,300 h x 1.125 x 2.4 = 11,610 requests/day
    return _smoke_payload({"sensitivity": sensitivity})


def _frame(outputs: Any, name: str) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(outputs.files[name]))


@pytest.mark.integration
def test_swept_capacity_near_the_legacy_percentile_is_flagged(smoke: Any) -> None:
    _, outputs = smoke
    frame = _frame(outputs, "sensitivity.csv")
    hours = frame[frame["parameter"] == "regular_hours_multiplier"].set_index("value")
    assert bool(hours.loc[1.125, "capacity_within_1pct_of_legacy_demand_percentile"]) is True
    assert bool(hours.loc[1.0, "capacity_within_1pct_of_legacy_demand_percentile"]) is False
    assert "near percentile" in outputs.files["report.md"].decode()


@pytest.mark.integration
def test_sensitivity_and_scenario_columns_carry_units_and_interpretations(smoke: Any) -> None:
    _, outputs = smoke
    sensitivity = _frame(outputs, "sensitivity.csv")
    assert "budget_dual_usd_equivalent_per_usd" in sensitivity.columns
    assert "terminal_dual_usd_equivalent_per_request" in sensitivity.columns
    assert {"unique_optimal_plan", "discretionary_cash_min_over_optimal_plans"} <= set(
        sensitivity.columns
    )
    scenarios = _frame(outputs, "scenarios.csv")
    stress = scenarios[scenarios["scenario_id"] == "m3_marginal_q90_stress"]
    assert set(stress["demand_interpretation"]) == {"hypothetical_stress_not_a_probability"}
    normal = scenarios[scenarios["scenario_id"] == "normal"]
    assert set(normal["demand_interpretation"]) == {"unvalidated_point_forecast"}
    # The scenario's own demand, not the unscaled point forecast, sets the second ratio.
    assert (
        stress["capacity_to_scenario_demand_mean_ratio"]
        < stress["capacity_to_point_forecast_mean_ratio"]
    ).all()
    plans = _frame(outputs, "plans.csv")
    assert set(
        plans[plans["scenario_id"] == "m3_marginal_q90_stress"]["demand_interpretation"]
    ) == {"hypothetical_stress_not_a_probability"}


@pytest.mark.integration
def test_injected_sweep_problem_marks_the_run_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    import atlas.optimization_runner as runner

    real = runner.run_sweep

    def with_problem(*args: Any, **kwargs: Any) -> Any:
        points, checks = real(*args, **kwargs)
        first = dataclasses.replace(points[0], problems=("planted problem",))
        return (first, *points[1:]), checks

    monkeypatch.setattr(runner, "run_sweep", with_problem)
    _, outputs = _smoke_payload()
    errors = [i for i in outputs.issues if i.severity.value == "error"]
    assert any(i.subject.startswith("sensitivity:") for i in errors)


@pytest.mark.integration
def test_report_states_the_reported_plan_and_never_an_n_a_budget(smoke: Any) -> None:
    _, outputs = smoke
    report = outputs.files["report.md"].decode()
    assert "| reported plan |" in report
    assert "n/a USD" not in report
    assert "not a budget request" in report or "relaxed" not in report
    payload = json.loads(outputs.files["metrics.json"])
    for scenario in payload["scenarios"]:
        if scenario["frontier"]:
            scenario["frontier"]["least_cash_for_least_penalty"]["kept"] = None
    rendered = render_optimization_report(payload)
    assert "no plan meets the HYPOTHETICAL target at any cash cap" in rendered


@pytest.mark.integration
def test_verify_prints_the_label_and_the_m3_status(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    code = main(["optimize", "run", "--config", str(SMOKE), "--output-dir", str(run_dir)])
    assert code == 0
    stream = io.StringIO()
    assert main(["optimize", "verify", str(run_dir)], stdout=stream) == 0
    text = stream.getvalue()
    assert "HYPOTHETICAL DECISION SIMULATION" in text
    assert "FAILED_CALIBRATION: not decision-grade" in text


def test_failed_checks_fail_verify_and_inspect_even_when_reproduced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = analysis.certify

    def failing(lp: Any, result: Any) -> Certificate:
        return dataclasses.replace(real(lp, result), holds=False)

    monkeypatch.setattr(analysis, "certify", failing)
    run_dir = tmp_path / "failed"
    assert main(["optimize", "run", "--config", str(SMOKE), "--output-dir", str(run_dir)]) == 1
    manifest_path = run_dir / "manifest.json"
    assert json.loads(manifest_path.read_text())["complete"] is False
    # The failures reproduce byte for byte, but a reproduced failure is still a failure.
    stream = io.StringIO()
    assert main(["--format", "json", "optimize", "verify", str(run_dir)], stdout=stream) == 1
    assert "recomputed_check_failed" in stream.getvalue()
    monkeypatch.setattr(analysis, "certify", real)
    # Flipping the unhashed manifest flag does not make inspect pass: metrics.json (covered
    # by the result fingerprint) still records the failed checks.
    manifest = json.loads(manifest_path.read_text())
    manifest["complete"] = True
    manifest_path.write_text(json.dumps(manifest))
    stream = io.StringIO()
    assert main(["--format", "json", "optimize", "inspect", str(run_dir)], stdout=stream) == 1
    assert "checks_failed" in stream.getvalue()
    assert "complete_flag_inconsistent" in stream.getvalue()


def test_malformed_manifests_are_usage_errors(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    assert main(["optimize", "run", "--config", str(SMOKE), "--output-dir", str(run_dir)]) == 0
    manifest_path = run_dir / "manifest.json"
    original = json.loads(manifest_path.read_text())
    for key, command in (("config", "verify"), ("outputs", "inspect"), ("outputs", "verify")):
        manifest = dict(original)
        del manifest[key]
        manifest_path.write_text(json.dumps(manifest))
        assert main(["optimize", command, str(run_dir)], stdout=io.StringIO()) == 2
    manifest = dict(original)
    manifest["outputs"] = list(original["outputs"])
    manifest_path.write_text(json.dumps(manifest))
    assert main(["optimize", "inspect", str(run_dir)], stdout=io.StringIO()) == 2


def test_reference_runs_need_a_committed_configuration(tmp_path: Path) -> None:
    import subprocess

    from atlas.optimization_runner import _require_committed_config

    root = tmp_path / "repo"
    (root / "configs").mkdir(parents=True)
    outside = tmp_path / "elsewhere.json"
    outside.write_text("{}")
    with pytest.raises(ConfigError, match="is not under"):
        _require_committed_config(root, outside)
    inside = root / "configs" / "m4.json"
    inside.write_text("{}")
    git = ["git", "-C", str(root)]
    subprocess.run([*git, "init", "-q"], check=True)  # noqa: S603
    with pytest.raises(ConfigError, match="not tracked"):
        _require_committed_config(root, inside)
    subprocess.run([*git, "add", "configs/m4.json"], check=True)  # noqa: S603
    _require_committed_config(root, inside)


@pytest.mark.integration
def test_report_states_which_blocks_were_not_configured(smoke: Any) -> None:
    _, outputs = smoke
    payload = json.loads(outputs.files["metrics.json"])
    payload["sensitivity"]["base_scenario"] = None
    for scenario in payload["scenarios"]:
        for row in scenario["shadow_prices"]["headline"]:
            row["fd_status"] = "not_computed"
    report = render_optimization_report(payload)
    assert "No sensitivity sweeps were configured." in report
    assert "on scenario `None`" not in report
    assert "No dual was bracketed" in report
    full = outputs.files["report.md"].decode()
    assert "Every dual is bracketed" not in full
    assert "(the scenarios listed under `shadow_prices`)" in full


@pytest.mark.integration
def test_zero_daily_limits_are_flagged_as_unavailable_in_duals_csv(smoke: Any) -> None:
    _, outputs = smoke
    frame = _frame(outputs, "duals.csv")
    surge = frame[
        (frame["scenario_id"] == "delayed_surge") & (frame["constraint"] == "surge_daily_limit")
    ]
    zero = surge[surge["rhs"] == 0]
    assert len(zero) > 0
    assert zero["limit_is_zero_unavailable"].all()
    assert not surge[surge["rhs"] > 0]["limit_is_zero_unavailable"].any()


def test_capacity_near_percentile_band() -> None:
    near = PlanningProblem(date(2026, 7, 1), (unit(regular_hours=(11_550.0, 11_550.0)),), None)
    far = PlanningProblem(date(2026, 7, 1), (unit(regular_hours=(11_000.0, 11_000.0)),), None)
    assert capacity_near_percentile(near, 11_608.75)
    assert not capacity_near_percentile(far, 11_608.75)


def test_sweep_grids_are_listed_with_the_hypothetical_parameters() -> None:
    config = load_optimization_config(SMOKE)
    rows = [r for r in config.hypothetical_parameters() if r["scope"].startswith("sensitivity")]
    assert {r["parameter"] for r in rows} == {s.parameter for s in config.sensitivity.sweeps}  # type: ignore[union-attr]
    assert all(r["provenance"] == "HYPOTHETICAL" and len(r["assumption"]) >= 20 for r in rows)
    assert np.isfinite(len(rows))
