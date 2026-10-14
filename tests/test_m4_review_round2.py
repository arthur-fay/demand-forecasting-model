"""Regression tests for the second independent review, of the fixes made after the first.

Each test reproduces a defect that the first round of fixes introduced or left (commit
e3eb4e9) and passes after the second round: false policy-comparison infeasibility at scale,
tie thresholds widened by one large weight, an unchecked cash/penalty split, faces emptied
by tolerance-feasible optima, reasons naming limits that do not bind, mislabelled "forces
nothing" rows, vertex-dependent frontier and table values, unrecorded fallbacks, and
several wording, formatting and robustness gaps.
"""

from __future__ import annotations

import io
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import atlas.decision_analysis as analysis
from atlas.cli import main
from atlas.config import load_settings
from atlas.decision_analysis import (
    FrontierPoint,
    analyse_scenario,
    diagnose,
    evaluate_policy,
    finite_differences,
    frontier,
    optimal_face,
    solve_canonical,
    solve_problem,
)
from atlas.optimization_config import load_optimization_config
from atlas.optimization_report import _tied_points, render_optimization_report
from atlas.optimization_runner import WITHOUT_TARGET, _plan_rows, load_inputs, m4_outputs
from atlas.optimizer import Objective, OptimizationError, SolverSettings, SolveStatus
from atlas.planning import PlanningProblem, ServiceUnit
from atlas.policies import BACKLOG_TRIGGER
from atlas.runner import load_series
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


def one_unit(budget: float | None = None, **overrides: Any) -> PlanningProblem:
    return PlanningProblem(date(2026, 7, 1), (unit(**overrides),), budget)


SEED63_UNIT: dict[str, Any] = {
    "unit_id": "u",
    "demand": (
        11646.30054081102,
        27645.150725366315,
        16591.9650432118,
        6263.195038247198,
        23498.907074643892,
        5961.394139712748,
        9265.145551860931,
        5922.020029197334,
        20333.39886352876,
        25905.866469461896,
        19891.053881955777,
        9784.277284165537,
        25970.258373665514,
        26659.37661623402,
        12531.900347064968,
        11117.713001199148,
        21517.773291354795,
        29291.135645424973,
        27733.271267698256,
        26794.845547635796,
        22611.175315376277,
        9922.805680605485,
        19936.20238041268,
        15125.616887334545,
        20607.45866139241,
        16669.161485156692,
        9357.698133159105,
    ),
    "initial_backlog": 35914.64043618222,
    "regular_hours": (
        107.60499532121948,
        1024.2452909940419,
        988.9961500089154,
        2899.543849964763,
        352.64322955577416,
        1733.6199563698626,
        2828.900437889099,
        1834.591359137915,
        2782.5687125554905,
        1182.8843023029697,
        1313.146972305164,
        1111.049608761079,
        1106.688940192392,
        1043.41474374919,
        223.67719380123987,
        2955.659731145483,
        2200.78637170342,
        2035.1878014432248,
        1597.08126044293,
        601.4364548361991,
        676.3100975815537,
        2902.2409113490244,
        2471.8670471781425,
        1367.025065995965,
        2230.2549593652984,
        2875.8484763305323,
        126.62945244628587,
    ),
    "regular_productivity": 5.829145856744772,
    "overtime_productivity": 6.0117541309568985,
    "overtime_max_hours": (
        204.56078803281832,
        323.67450515171834,
        176.84034024248461,
        72.7899845983179,
        521.6550394797114,
        254.36293393673623,
        480.92006904425506,
        78.53724864788295,
        184.42472240568188,
        296.0556473188527,
        22.114956851581624,
        248.49048537995688,
        107.34402449752194,
        237.5448207553318,
        175.8555820522084,
        467.2634996482968,
        304.70357780352714,
        151.06680438715992,
        353.58927907914307,
        286.2473846888002,
        133.44081199227463,
        466.67869084436825,
        348.8663824192299,
        7.61579611067158,
        324.5469718002145,
        238.72783395465447,
        51.856662614581396,
    ),
    "surge_max": (
        3883.775821213956,
        2481.4359131375586,
        3972.337123632407,
        1905.97460766031,
        1749.6232087926305,
        627.7480493155059,
        2655.842122858194,
        1932.5995522152436,
        720.970432516769,
        3358.9031807735396,
        2361.6529267318433,
        343.4931610500631,
        3918.8134268304966,
        1673.2833773239886,
        3697.3746471503737,
        1643.289639298466,
        208.29020596248384,
        2196.5392310934853,
        1630.310000640518,
        3124.1615064595553,
        1981.4254006144477,
        1480.9630920798847,
        1646.8280364138607,
        1928.3046420880685,
        2923.3371884328567,
        3207.217558084416,
        1588.1895150274536,
    ),
    "regular_wage": 34.229404738037466,
    "overtime_wage": 45.628024973260864,
    "surge_price": 15.203398584076544,
    "backlog_penalty": 1.5589705915973997,
    "terminal_penalty": 8.51082602920913,
    "overtime_total_hours": 4000.0,
    "surge_total": None,
    "terminal_backlog_max": None,
}
SEED63_BUDGET = 979302.2395655923
SEED63_TRIGGER = 7170.043654371727


# --- Policy comparisons at scale ------------------------------------------------------------


def test_policy_caps_on_the_policy_values_never_make_the_comparison_infeasible() -> None:
    # The backlog-trigger plan sits exactly on the caps of the same-backlog programme; at
    # this scale (27 days, ~18,000 requests a day) HiGHS calls the exactly capped programme
    # infeasible, although the policy plan satisfies it to 1e-11. The caps are then loosened
    # to lex_cap, and the fallback is recorded.
    problem = PlanningProblem(date(2026, 7, 1), (ServiceUnit(**SEED63_UNIT),), SEED63_BUDGET)
    outcome = evaluate_policy(problem, SETTINGS, BACKLOG_TRIGGER, {"u": SEED63_TRIGGER})
    assert outcome.same_cash.optimal
    assert outcome.same_backlog.optimal
    assert not outcome.problems
    assert "policy_epsilon_cap" in outcome.fallbacks


# --- Face thresholds, kept optimum, tolerance-feasible optima ----------------------------------


@pytest.mark.parametrize("terminal_penalty", [0.0, 1e6, 5e8])
def test_one_large_weight_does_not_turn_real_price_gaps_into_ties(terminal_penalty: float) -> None:
    # Overtime costs 30 USD per request on day 1; deferring a request to day 2 costs 30.0009.
    # The optimum (serve everything on day 1) is unique. A threshold relative to max |c|
    # (w_T = 1e6) treated the 0.0009 gap as a tie and reported a non-optimal plan.
    problem = one_unit(
        demand=(1000.0, 0.0),
        regular_hours=(0.0, 1000.0),
        overtime_max_hours=(2000.0, 0.0),
        overtime_wage=30.0,
        surge_price=30.0,
        backlog_penalty=30.0009,
        terminal_penalty=terminal_penalty,
    )
    canonical = solve_canonical(problem, SETTINGS)
    assert not canonical.problems
    assert canonical.fallbacks == ()
    plan = canonical.plan.require().evaluation
    assert plan.discretionary_cost == pytest.approx(30000.0, abs=1e-6)
    assert plan.penalised_score == pytest.approx(30000.0, abs=1e-6)
    face = optimal_face(problem, SETTINGS, canonical.optimum.require())
    assert face.unique
    assert not face.problems


def test_cash_penalty_split_keeps_the_optimum_after_the_step() -> None:
    problem = one_unit(
        demand=(1000.0, 1000.0),
        regular_hours=(0.0, 1000.0),
        overtime_max_hours=(2000.0, 0.0),
        overtime_wage=30.0,
        surge_price=30.0,
        backlog_penalty=30.0009,
        terminal_penalty=1e6,
    )
    strict = solve_problem(problem, SETTINGS).require()
    item = next(
        fd
        for fd in finite_differences(strict, SETTINGS, STEPS)
        if fd.dual.constraint == "capacity" and fd.dual.day == 1
    )
    assert item.practical_forward == pytest.approx(0.0, abs=1e-6)
    assert item.forward_cash == pytest.approx(0.0, abs=1e-6)
    assert item.forward_penalty == pytest.approx(0.0, abs=1e-6)
    assert not item.problems


def test_an_optimum_feasible_only_within_the_solver_tolerance_keeps_its_face() -> None:
    # Serving at full capacity leaves exactly 5 requests; the target asks for 5 - 1e-9,
    # which HiGHS accepts at its tolerance. The face used to be empty (8 ERRORs).
    problem = one_unit(
        demand=(10.0,),
        regular_hours=(0.0,),
        overtime_max_hours=(5.0,),
        surge_max=(0.0,),
        terminal_backlog_max=5.0 - 1e-9,
    )
    result = analyse_scenario(
        "edge", problem, SETTINGS, {"u": 0.0}, shadow_steps=STEPS, frontier_points=3
    )
    assert result.strict.optimal
    assert not result.problems
    assert result.face is not None
    assert result.face.unique


def test_tiny_coefficients_that_highs_would_drop_are_rejected() -> None:
    problem = one_unit(budget=100.0, surge_max=(5.0, 5.0), surge_price=1e-9)
    with pytest.raises(OptimizationError, match="HiGHS drops"):
        solve_problem(problem, SETTINGS)


# --- Diagnoses name only limits that matter -------------------------------------------------


def test_reasons_attribute_limits_to_their_own_unit() -> None:
    # Unit a cannot reach its target because of its 1-hour overtime total; unit b has a
    # generous total and a surge delay that matter for nothing.
    a = unit(
        unit_id="a",
        demand=(10.0, 10.0),
        regular_hours=(5.0, 5.0),
        overtime_total_hours=1.0,
        terminal_backlog_max=0.0,
    )
    b = unit(
        unit_id="b",
        demand=(10.0, 10.0),
        regular_hours=(5.0, 5.0),
        overtime_total_hours=100.0,
        surge_max=(0.0, 3.0),
        surge_total=50.0,
    )
    diagnosis = diagnose(PlanningProblem(date(2026, 7, 1), (a, b), None), SETTINGS)
    text = diagnosis.reasons[0]
    assert "a: the overtime total of 1 staff hours" in text
    assert "b:" not in text
    assert "the surge delay" not in text


def test_a_daily_limit_that_cannot_lower_the_violation_is_not_named() -> None:
    # The overtime total (10 h) is half of what the daily limits allow (2 x 10 h): raising
    # the daily limit changes nothing, whichever least-violation plan the solver returns.
    problem = one_unit(
        demand=(100.0, 100.0),
        regular_hours=(10.0, 10.0),
        overtime_max_hours=(10.0, 10.0),
        overtime_total_hours=10.0,
        terminal_backlog_max=0.0,
    )
    diagnosis = diagnose(problem, SETTINGS)
    text = diagnosis.reasons[0]
    assert "the overtime total of 10 staff hours (to 160.0 requests without it)" in text
    assert "daily overtime limit" not in text
    assert diagnosis.limit_relaxations["u:overtime_daily_limit"] == pytest.approx(170.0)


# --- Labels of the target-dropped row ----------------------------------------------------------


def test_a_target_that_trims_a_tied_face_changes_the_reported_plan() -> None:
    # Every overtime level in [0, 10] scores 10; the target (at most 5 left) excludes the
    # cheaper ones. The score does not change, the least-cash plan does.
    problem = one_unit(
        demand=(10.0,),
        regular_hours=(0.0,),
        overtime_max_hours=(10.0,),
        surge_max=(0.0,),
        terminal_backlog_max=5.0,
    )
    result = analyse_scenario(
        "tie", problem, SETTINGS, {"u": 0.0}, shadow_steps=None, frontier_points=None
    )
    assert result.target_changes_score is False
    assert result.target_changes_plan is True
    rows = {row[0]: row for row in _plan_rows(result, None)}  # type: ignore[arg-type]
    _id, feasibility, _attempt, _plan, evaluation, _check = rows[WITHOUT_TARGET]
    assert feasibility == "terminal_target_dropped"
    assert evaluation is not None
    assert evaluation.discretionary_cost == pytest.approx(0.0, abs=1e-9)
    assert result.reported.require().evaluation.discretionary_cost == pytest.approx(5.0)


# --- Finite differences near zero right-hand sides -------------------------------------------


@pytest.mark.parametrize(("tiny", "big"), [(1e-5, 1e5), (1e-12, 1e3)])
def test_backward_steps_below_the_forward_step_get_their_own_noise(tiny: float, big: float) -> None:
    horizon = 5
    problem = PlanningProblem(
        date(2026, 7, 1),
        (
            ServiceUnit(
                unit_id="u",
                demand=(big, tiny, big * 0.7, tiny, big * 0.3),
                initial_backlog=tiny,
                regular_hours=(big * 0.2, 0.0, big * 0.1, big * 0.1, 0.0),
                regular_productivity=1.0,
                overtime_productivity=1.3,
                overtime_max_hours=(big * 0.05,) * horizon,
                surge_max=(tiny, big * 0.03, tiny, big * 0.02, big * 0.01),
                regular_wage=17.0,
                overtime_wage=41.3,
                surge_price=37.9,
                backlog_penalty=7.7,
                terminal_penalty=11.1,
                overtime_total_hours=big * 0.13,
                surge_total=big * 0.04,
            ),
        ),
        big * 3.7,
    )
    strict = solve_problem(problem, SETTINGS).require()
    differences = finite_differences(strict, SETTINGS, STEPS)
    assert all(fd.bracket_ok and fd.convex_ok and fd.sign_ok for fd in differences)


def test_a_slope_the_solver_could_not_compute_is_not_a_breakpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strict = solve_problem(one_unit(), SETTINGS).require()

    def fail(lp: Any, settings: Any) -> Any:
        raise OptimizationError("injected")

    monkeypatch.setattr(analysis, "solve_lp", fail)
    differences = finite_differences(strict, SETTINGS, STEPS, base_cash=0.0)
    assert differences
    assert all(fd.status == "solver_failed" for fd in differences)
    assert all(fd.problems for fd in differences)


# --- Frontier points report what the tie-breaks leave open ------------------------------------


def test_frontier_points_flag_overtime_and_surge_left_open() -> None:
    # Overtime and surge both cost 1 USD per request: at a cash cap of 5, five requests are
    # served on day 1 by any mix of the two.
    problem = one_unit(
        demand=(10.0, 0.0),
        regular_hours=(0.0, 10.0),
        overtime_max_hours=(10.0, 0.0),
        surge_max=(10.0, 0.0),
        surge_price=1.0,
        backlog_penalty=2.0,
    )
    curve = frontier(problem, SETTINGS, 3, None)
    assert curve is not None
    assert not curve.problems
    middle = [p for p in curve.points if p.cash_cap == pytest.approx(5.0)]
    assert middle
    assert all(p.unique_after_tie_breaks is False for p in middle)
    assert all(p.overtime_hours_range == pytest.approx((0.0, 5.0)) for p in middle)
    assert all(p.totals_open() for p in middle)


def test_a_frontier_tie_in_the_daily_schedule_alone_leaves_no_total_open() -> None:
    def point(unique: bool | None, hours: tuple[float, float]) -> FrontierPoint:
        return FrontierPoint(
            "kept",
            1.0,
            "grid",
            SolveStatus.OPTIMAL,
            1.0,
            1.0,
            1.0,
            0.0,
            hours[0],
            2.0,
            unique_after_tie_breaks=unique,
            overtime_hours_range=hours,
            surge_requests_range=(2.0, 2.0),
        )

    assert not point(False, (3.0, 3.0)).totals_open()  # only the timing differs
    assert point(False, (3.0, 3.1)).totals_open()
    assert not point(True, (3.0, 3.0)).totals_open()
    assert not point(None, (3.0, 3.0)).totals_open()
    text = _tied_points(
        {"points_not_unique_after_tie_breaks": 6, "points_with_overtime_or_surge_totals_open": 0}
    )
    assert "at 6 points other plans tie" in text
    assert "different daily schedule" in text
    assert "differ too at 0 of them" in text
    assert "open" not in text.replace("overtime or surge totals differ", "")
    assert _tied_points({"points_not_unique_after_tie_breaks": 0}) == (
        "every point is unique after the tie-breaks"
    )


def test_the_canonical_plan_breaks_ties_on_cash_terminal_backlog_and_request_days() -> None:
    canonical = solve_canonical(one_unit(), SETTINGS)
    assert [stage.objective for stage in canonical.stages] == [
        Objective.WEIGHTED,
        Objective.DISCRETIONARY_CASH,
        Objective.TERMINAL_BACKLOG,
        Objective.BACKLOG_DAYS,
    ]


def test_spend_and_shortfall_are_printed_consistently() -> None:
    # 5 overtime hours at 1.3 USD reach the target; the budget is 3e-10 short of 6.5 USD.
    problem = one_unit(
        budget=6.5 * (1 - 3e-10),
        demand=(10.0,),
        regular_hours=(5.0,),
        overtime_max_hours=(10.0,),
        surge_max=(0.0,),
        overtime_wage=1.3,
        terminal_backlog_max=0.0,
    )
    reason = diagnose(problem, SETTINGS).reasons[0]
    assert "less than 6.5 USD" in reason
    assert "e-09 USD above the HYPOTHETICAL budget" in reason


# --- Runner, report and CLI ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def smoke() -> Any:
    config = load_optimization_config(SMOKE)
    settings = load_settings(repo_root=REPO_ROOT, env={})
    _, data = load_series(settings, "review_round2")
    assert data is not None
    return m4_outputs(settings, config, load_inputs(settings, config, data))


def _frame(outputs: Any, name: str) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(outputs.files[name]))


@pytest.mark.integration
def test_infeasible_strict_rows_carry_no_selection_or_uniqueness(smoke: Any) -> None:
    frame = _frame(smoke, "scenarios.csv")
    rows = frame[(frame["plan"] == "optimized") & (frame["feasibility"] == "infeasible")]
    assert len(rows) > 0
    assert rows["plan_selection"].isna().all()
    assert rows["unique_optimal_plan"].isna().all()


@pytest.mark.integration
def test_zero_daily_limits_are_never_called_binding(smoke: Any) -> None:
    frame = _frame(smoke, "duals.csv")
    zero = frame[frame["limit_is_zero_unavailable"]]
    assert len(zero) > 0
    assert not zero["binding"].any()


@pytest.mark.integration
def test_nothing_is_called_bracketed_when_nothing_was_computed(smoke: Any) -> None:
    payload = json.loads(smoke.files["metrics.json"])
    infeasible = [s for s in payload["scenarios"] if s["feasibility"] != "strict"]
    assert infeasible
    assert all(s["shadow_prices"]["brackets_ok"] is None for s in infeasible)
    for scenario in payload["scenarios"]:
        if scenario["shadow_prices"]["computed"]:
            scenario["shadow_prices"]["headline"] = []
    report = render_optimization_report(payload)
    assert "No dual was bracketed" not in report
    assert "The duals of `normal`" in report


@pytest.mark.integration
def test_sub_unit_violations_never_print_as_zero(smoke: Any) -> None:
    payload = json.loads(smoke.files["metrics.json"])
    relaxed = next(s for s in payload["scenarios"] if s["diagnosis"])
    relaxed["diagnosis"]["least_terminal_violation_within_budget_requests"] = 0.2871685
    report = render_optimization_report(payload)
    assert "least terminal-target violation within the budget: 0.287 requests" in report
    assert "(0.287 over target)" in report


@pytest.mark.integration
def test_every_fallback_is_counted_and_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    real = analysis._keeps_optimum

    def first_rejected(*args: Any) -> bool:
        calls["n"] += 1
        return False if calls["n"] == 1 else real(*args)

    monkeypatch.setattr(analysis, "_keeps_optimum", first_rejected)
    config = load_optimization_config(SMOKE)
    settings = load_settings(repo_root=REPO_ROOT, env={})
    _, data = load_series(settings, "review_round2_fallback")
    assert data is not None
    outputs = m4_outputs(settings, config, load_inputs(settings, config, data))
    payload = json.loads(outputs.files["metrics.json"])
    assert payload["checks"]["stage_fallbacks"] >= 1
    assert any(i.code == "stage_fallback" for i in outputs.issues)


@pytest.mark.integration
def test_malformed_run_files_are_usage_errors(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    assert main(["optimize", "run", "--config", str(SMOKE), "--output-dir", str(run_dir)]) == 0
    manifest_path = run_dir / "manifest.json"
    original = json.loads(manifest_path.read_text())
    manifest_path.write_text(json.dumps({**original, "code": "x"}))
    assert main(["optimize", "verify", str(run_dir)], stdout=io.StringIO()) == 2
    manifest_path.write_text(json.dumps(original))
    metrics_path = run_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text())
    metrics_path.write_text(json.dumps({**metrics, "scenarios": [1]}))
    assert main(["optimize", "inspect", str(run_dir)], stdout=io.StringIO()) == 2


@pytest.mark.integration
def test_verify_text_separates_recomputation_from_on_disk_changes(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    assert main(["optimize", "run", "--config", str(SMOKE), "--output-dir", str(run_dir)]) == 0
    with (run_dir / "report.md").open("a", encoding="utf-8") as handle:
        handle.write("tampered\n")
    stream = io.StringIO()
    assert main(["optimize", "verify", str(run_dir)], stdout=stream) == 1
    text = stream.getvalue()
    assert "recomputed files identical to the recorded hashes" in text
    assert "altered on disk: report.md" in text
    assert "DIFFERENT" not in text


def test_duals_describe_the_reported_plan() -> None:
    # Every overtime level in [0, 10] on day 1 scores 10; the reported (least-cash) plan uses
    # none, so day 1's overtime limit has its full 10 hours of slack whatever vertex HiGHS
    # returned.
    problem = PlanningProblem(date(2026, 7, 1), (unit(),), None)
    result = analyse_scenario(
        "tied", problem, SETTINGS, {"u": 0.0}, shadow_steps=None, frontier_points=None
    )
    reported = result.reported.require().plan.units[0]
    dual = next(d for d in result.duals if d.key() == ("overtime_daily_limit", "u", 0))
    assert dual.slack == pytest.approx(10.0 - reported.overtime_hours[0])
    assert math.isclose(reported.overtime_hours[0], 0.0, abs_tol=1e-9)
