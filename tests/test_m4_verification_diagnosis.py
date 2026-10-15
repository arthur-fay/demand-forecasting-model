"""Regression tests for the verification of the round-3 diagnosis fixes (commit ce2b104).

Each test pins one fix and fails when it is reverted: the BORDERLINE override of
"reachable" for two units that each miss their target by less than their own tolerance
(r4-diagnosis-04), the accounting of the uniqueness test of the reported plan after the
tie-breaks (r4-diagnosis-05), no uniqueness verdict from an incomplete test (``_verdict``:
scenarios.csv, sensitivity.csv, frontier.csv and metrics.json), the cap on a lifted daily
limit (``LIFT_CAP``: a cap that binds is "not computed", one that does not gives the exact
violation, and a lift that reached all the work of the horizon never counts as capped), the
wording when a limit removal failed, and configured limits and targets printed as configured
(``_limit``, r4-diagnosis-06).

The runner tests make the smoke run (``configs/optimization/smoke.json``) once per injected
failure, through ``m4_outputs`` (no files are written).
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

import atlas.decision_analysis as analysis
from atlas.config import Settings, load_settings
from atlas.decision_analysis import (
    LIFT_CAP,
    OptimalFace,
    ScenarioResult,
    _cap_binds,
    _limit,
    _relaxed_limits,
    analyse_scenario,
    diagnose,
    reach_tolerance,
    solve_problem,
)
from atlas.issues import Severity
from atlas.optimization_config import parse_optimization_config
from atlas.optimization_runner import M4Outputs, load_inputs, m4_outputs
from atlas.optimizer import (
    LinearProgram,
    LPResult,
    Objective,
    OptimizationError,
    SolverSettings,
    Terminal,
)
from atlas.planning import PlanningProblem, ServiceUnit, tolerance
from atlas.runner import SnapshotData, load_series
from tests.conftest import REPO_ROOT

SETTINGS = SolverSettings()
SMOKE = REPO_ROOT / "configs" / "optimization" / "smoke.json"
SMOKE_SCENARIOS = ("normal", "reduced_budget", "delayed_surge", "m3_marginal_q90_stress")
# The plan whose uniqueness each smoke scenario reports (the relaxed plan when infeasible).
REPORTED_ROWS = [
    ("normal", "optimized"),
    ("reduced_budget", "relaxed_terminal_target"),
    ("delayed_surge", "optimized"),
    ("m3_marginal_q90_stress", "relaxed_terminal_target"),
]
OUT_OF_REACH = (
    "the HYPOTHETICAL terminal-backlog target cannot be reached with any amount of "
    "discretionary cash"
)
REMOVED = (
    "removed one at a time (a daily limit lifted to all the work of the horizon, at most 5e8 a "
    "day; a total dropped; a delayed surge available from the first day), these limits would "
    "lower it: "
)
NONE_LOWERS = (
    "no configured limit lowers it when removed alone (the limits and the regular capacity "
    "bind together)"
)
NONE_COMPUTED_LOWERS = (
    "no configured limit whose removal could be computed lowers it when removed alone"
)
NOT_COMPUTED = "not computed (an ERROR of this run): "
METRICS_KEY = "least_violation_unlimited_cash_with_one_limit_removed_requests"
INJECTED = "injected failure of an optimal-face programme"
FACE_FAILED = f"optimal-face programme failed: {INJECTED}"


def unit(**overrides: Any) -> ServiceUnit:
    """All 30 requests arrive on day 3; 5 overtime hours a day and 5 in total; target 0."""
    base: dict[str, Any] = {
        "unit_id": "u",
        "demand": (0.0, 0.0, 30.0),
        "initial_backlog": 0.0,
        "regular_hours": (0.0, 0.0, 0.0),
        "regular_productivity": 1.0,
        "overtime_productivity": 1.0,
        "overtime_max_hours": (5.0, 5.0, 5.0),
        "surge_max": (0.0, 0.0, 0.0),
        "regular_wage": 1.0,
        "overtime_wage": 1.0,
        "surge_price": 1.0,
        "backlog_penalty": 1.0,
        "overtime_total_hours": 5.0,
        "terminal_backlog_max": 0.0,
    }
    base.update(overrides)
    return ServiceUnit(**base)


def problem_of(*units: ServiceUnit, budget: float | None = None) -> PlanningProblem:
    return PlanningProblem(date(2026, 7, 1), units, budget)


def analysed(problem: PlanningProblem) -> ScenarioResult:
    return analyse_scenario(
        "x",
        problem,
        SETTINGS,
        {u.unit_id: 0.0 for u in problem.units},
        shadow_steps=None,
        frontier_points=None,
    )


# --- r4-diagnosis-04: the BORDERLINE override of "reachable" with two units -----------------------


def test_two_units_each_within_its_own_tolerance_are_borderline_not_out_of_reach() -> None:
    # r4-diagnosis-04: each unit misses its target of 0 by 0.8e-6 requests, within its own
    # check tolerance (1e-6), so the relaxed plan passes the plan check (BORDERLINE). The
    # total, 1.6e-6, is above reach_tolerance (the least of the units' tolerances): only the
    # "or borderline" override keeps the diagnosis from also calling the target out of reach
    # "with any amount of discretionary cash" and naming limits.
    def short(unit_id: str) -> ServiceUnit:
        return unit(
            unit_id=unit_id,
            demand=(10.0,),
            regular_hours=(0.0,),
            overtime_max_hours=(10.0 - 0.8e-6,),
            surge_max=(0.0,),
            overtime_total_hours=None,
        )

    problem = problem_of(short("a"), short("b"))
    diagnosis = diagnose(problem, SETTINGS)
    least = diagnosis.least_violation_unlimited
    assert least is not None
    # The instance still needs the override (otherwise it no longer tests it).
    assert reach_tolerance(problem) == tolerance(0.0)
    assert least > reach_tolerance(problem)
    assert least == pytest.approx(1.6e-6, rel=1e-3)
    assert diagnosis.borderline is True
    assert not any(OUT_OF_REACH in reason for reason in diagnosis.reasons)
    assert len(diagnosis.reasons) == 2
    assert diagnosis.reasons[0] == (
        "with unlimited discretionary cash the least terminal-target violation is 1.6e-06 "
        "requests, within the checking tolerance, but the solver (tolerance 1e-9) finds no "
        "plan that meets the target exactly"
    )
    assert diagnosis.reasons[1].startswith("BORDERLINE: the relaxed plan meets every ")
    assert diagnosis.limit_relaxations == {}
    assert diagnosis.to_dict()[METRICS_KEY] == {}


# --- r4-diagnosis-05 and _verdict: failed and recovered optimal-face tests ------------------------


@contextmanager
def failing_faces(select: Callable[[str, bool], bool], *, rebuild: bool) -> Iterator[None]:
    """Make the optimal-face programmes of the selected ``optimal_face`` calls raise.

    ``select(caller, daily)`` picks a call by the function that makes it and its ``daily``
    flag. With ``rebuild`` the rebuild with exact zero tests succeeds (a recovered fallback);
    without it the rebuild fails too (an incomplete test).
    """
    real_face, real_test, real_feasible = (
        analysis.optimal_face,
        analysis._face_test,
        analysis._solve_feasible,
    )
    state = {"selected": False, "fail": False}

    def optimal_face(
        problem: PlanningProblem, settings: SolverSettings, strict: Any, *, daily: bool = True
    ) -> OptimalFace:
        state["selected"] = select(sys._getframe(1).f_code.co_name, daily)
        try:
            return real_face(problem, settings, strict, daily=daily)
        finally:
            state["selected"] = False

    def face_test(
        problem: PlanningProblem,
        settings: SolverSettings,
        strict: Any,
        *,
        daily: bool,
        zero_threshold: bool,
    ) -> OptimalFace:
        state["fail"] = state["selected"] and not (rebuild and zero_threshold)
        try:
            return real_test(problem, settings, strict, daily=daily, zero_threshold=zero_threshold)
        finally:
            state["fail"] = False

    def solve_feasible(lp: LinearProgram, settings: SolverSettings) -> LPResult:
        if state["fail"]:
            raise OptimizationError(INJECTED)
        return real_feasible(lp, settings)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(analysis, "optimal_face", optimal_face)
        patch.setattr(analysis, "_face_test", face_test)
        patch.setattr(analysis, "_solve_feasible", solve_feasible)
        yield


def after_tie_breaks(caller: str, daily: bool) -> bool:
    """The reported plan's own test: analyse_scenario's only call without per-day ranges."""
    return caller == "analyse_scenario" and not daily


def every_face(_caller: str, _daily: bool) -> bool:
    return True


def tie_break_unit() -> ServiceUnit:
    return ServiceUnit(
        unit_id="u",
        demand=(100.0, 120.0, 90.0),
        initial_backlog=50.0,
        regular_hours=(40.0,) * 3,
        regular_productivity=2.0,
        overtime_productivity=2.0,
        overtime_max_hours=(10.0,) * 3,
        surge_max=(20.0,) * 3,
        regular_wage=10.0,
        overtime_wage=30.0,
        surge_price=20.0,
        backlog_penalty=1.5,
        terminal_backlog_max=60.0,
    )


@pytest.mark.parametrize(
    ("budget", "feasibility"),
    [(5000.0, "strict"), (100.0, "relaxed_terminal_target")],
    ids=["strict", "relaxed"],
)
def test_a_failed_after_tie_break_test_is_a_problem_of_the_scenario(
    budget: float, feasibility: str
) -> None:
    # r4-diagnosis-05: tie_broken_face's problems were not added to the scenario problems
    # (mutant tbface_not_problems), so a test nobody completed gave a silent verdict in a
    # complete run.
    with failing_faces(after_tie_breaks, rebuild=False):
        result = analysed(problem_of(tie_break_unit(), budget=budget))
    assert result.feasibility == feasibility
    tested = result.tie_broken_face
    assert tested is not None
    assert tested.rebuilt is True
    assert tested.problems
    assert set(tested.problems) == {FACE_FAILED}
    assert result.problems == tested.problems
    # Only the after-tie-break test failed: the reported plan's own face test is complete.
    assert result.reported_face is not None
    assert result.reported_face.problems == ()


def test_a_recovered_after_tie_break_test_is_a_fallback() -> None:
    # r4-diagnosis-05: a rebuilt after-tie-break face was not recorded as a fallback (mutant
    # tbface_rebuilt_fb), so stage_fallbacks and the WARNING missed it.
    with failing_faces(after_tie_breaks, rebuild=True):
        result = analysed(problem_of(tie_break_unit(), budget=5000.0))
    tested = result.tie_broken_face
    assert tested is not None
    assert tested.rebuilt is True
    assert tested.problems == ()
    assert result.problems == ()
    assert result.fallbacks == ("face_rebuilt",)


@pytest.fixture(scope="module")
def smoke_data() -> tuple[Settings, SnapshotData]:
    settings = load_settings(repo_root=REPO_ROOT, env={})
    _, data = load_series(settings, "verification_diagnosis")
    assert data is not None
    return settings, data


@dataclass(frozen=True)
class SmokeOutputs:
    outputs: M4Outputs

    def metrics(self) -> dict[str, Any]:
        payload: dict[str, Any] = json.loads(self.outputs.files["metrics.json"])
        return payload

    def scenarios(self) -> list[dict[str, Any]]:
        scenarios: list[dict[str, Any]] = self.metrics()["scenarios"]
        assert tuple(s["scenario_id"] for s in scenarios) == SMOKE_SCENARIOS
        return scenarios

    def rows(self, name: str) -> list[dict[str, str]]:
        """The rows of a CSV file as written (an empty cell is a missing value)."""
        text = self.outputs.files[name].decode("utf-8")
        return list(csv.DictReader(io.StringIO(text)))

    def report_uniqueness(self) -> dict[str, tuple[str, str]]:
        """``unique`` and ``unique after tie-breaks`` of the report's uniqueness table."""
        report = self.outputs.files["report.md"].decode("utf-8")
        found = re.findall(
            r"^\| `(\w+)` \| (?:strict|relaxed) \| ([^|]+) \| ([^|]+) \|", report, re.M
        )
        return {scenario: (unique.strip(), after.strip()) for scenario, unique, after in found}


def _smoke(
    smoke_data: tuple[Settings, SnapshotData],
    select: Callable[[str, bool], bool],
    *,
    rebuild: bool,
    analyses: bool,
) -> SmokeOutputs:
    settings, data = smoke_data
    payload = json.loads(SMOKE.read_text(encoding="utf-8"))
    if not analyses:  # the scenario-level results only
        payload.update(sensitivity=None, frontier=None, shadow_prices=None)
    config = parse_optimization_config(payload)
    inputs = load_inputs(settings, config, data)
    with failing_faces(select, rebuild=rebuild):
        return SmokeOutputs(m4_outputs(settings, config, inputs))


@pytest.fixture(scope="module")
def tie_break_failed(smoke_data: tuple[Settings, SnapshotData]) -> SmokeOutputs:
    return _smoke(smoke_data, after_tie_breaks, rebuild=False, analyses=False)


@pytest.fixture(scope="module")
def tie_break_rebuilt(smoke_data: tuple[Settings, SnapshotData]) -> SmokeOutputs:
    return _smoke(smoke_data, after_tie_breaks, rebuild=True, analyses=False)


@pytest.fixture(scope="module")
def every_face_failed(smoke_data: tuple[Settings, SnapshotData]) -> SmokeOutputs:
    return _smoke(smoke_data, every_face, rebuild=False, analyses=True)


@pytest.mark.integration
def test_a_run_reports_a_failed_after_tie_break_test_as_an_error_with_no_verdict(
    tie_break_failed: SmokeOutputs,
) -> None:
    # r4-diagnosis-05: with only the after-tie-break test failing, every scenario had zero
    # problems (a complete run) and scenarios.csv, metrics.json and the report said
    # "unique after tie-breaks: no" (distance inf), a verdict nobody computed.
    for scenario in tie_break_failed.scenarios():
        assert scenario["problems"]
        assert set(scenario["problems"]) == {FACE_FAILED}
        assert scenario["reported_plan_after_tie_breaks"]["unique"] is None
        # The reported plan's own face test was not touched.
        assert scenario["optimal_face"]["unique"] is True
    errors = [i for i in tie_break_failed.outputs.issues if i.severity is Severity.ERROR]
    assert {(i.code, i.subject, i.message) for i in errors} == {
        ("check_failed", scenario_id, FACE_FAILED) for scenario_id in SMOKE_SCENARIOS
    }
    metrics = tie_break_failed.metrics()
    assert metrics["checks"]["errors"] == len(errors)
    rows = tie_break_failed.rows("scenarios.csv")
    tested = [r for r in rows if r["unique_optimal_plan"] != ""]
    assert [(r["scenario_id"], r["plan"]) for r in tested] == REPORTED_ROWS
    assert [r["unique_optimal_plan"] for r in tested] == ["True"] * 4
    assert [r["unique_after_tie_breaks"] for r in rows] == [""] * len(rows)
    assert tie_break_failed.report_uniqueness() == dict.fromkeys(SMOKE_SCENARIOS, ("yes", "n/a"))


@pytest.mark.integration
def test_a_run_counts_a_recovered_after_tie_break_test_as_a_fallback(
    tie_break_rebuilt: SmokeOutputs,
) -> None:
    # r4-diagnosis-05: a recovered rebuild of the after-tie-break face appears in each
    # scenario's stage_fallbacks, the run's count and a stage_fallback WARNING; the
    # recovered test still gives its verdict.
    for scenario in tie_break_rebuilt.scenarios():
        assert scenario["problems"] == []
        assert scenario["stage_fallbacks"] == ["face_rebuilt"]
        assert scenario["reported_plan_after_tie_breaks"]["unique"] is True
    metrics = tie_break_rebuilt.metrics()
    assert metrics["checks"]["stage_fallbacks"] == len(SMOKE_SCENARIOS)
    assert metrics["checks"]["errors"] == 0
    warnings = [i for i in tie_break_rebuilt.outputs.issues if i.code == "stage_fallback"]
    assert [i.subject for i in warnings] == list(SMOKE_SCENARIOS)
    assert all(i.severity is Severity.WARNING for i in warnings)
    assert all("(face_rebuilt x1)" in i.message for i in warnings)
    rows = tie_break_rebuilt.rows("scenarios.csv")
    tested = [r for r in rows if r["unique_after_tie_breaks"] != ""]
    assert [(r["scenario_id"], r["plan"]) for r in tested] == REPORTED_ROWS
    assert [r["unique_after_tie_breaks"] for r in tested] == ["True"] * 4


@pytest.mark.integration
def test_an_incomplete_face_test_gives_no_verdict_in_scenarios_csv(
    every_face_failed: SmokeOutputs,
) -> None:
    # _verdict: a failed uniqueness test (distance inf, unique False) is not evidence of
    # other optimal plans; the reported plan's verdicts are None (an empty cell).
    rows = every_face_failed.rows("scenarios.csv")
    # The reported plans were computed (their face tests ran and failed).
    status = {(r["scenario_id"], r["plan"]): r["solver_status"] for r in rows}
    assert [status[row] for row in REPORTED_ROWS] == ["optimal"] * 4
    assert [r["unique_optimal_plan"] for r in rows] == [""] * len(rows)
    assert [r["unique_after_tie_breaks"] for r in rows] == [""] * len(rows)
    assert every_face_failed.report_uniqueness() == dict.fromkeys(SMOKE_SCENARIOS, ("n/a", "n/a"))


@pytest.mark.integration
def test_an_incomplete_face_test_gives_no_verdict_in_sensitivity_csv(
    every_face_failed: SmokeOutputs,
) -> None:
    # _verdict in sensitivity_frame and _sweep_summary: every optimal sweep point's face
    # test failed, so none has a verdict (it read False, "not unique").
    rows = every_face_failed.rows("sensitivity.csv")
    assert sum(r["solver_status"] == "optimal" for r in rows) == 14
    assert [r["unique_optimal_plan"] for r in rows] == [""] * len(rows)
    points = every_face_failed.metrics()["sensitivity"]["points"]
    assert len(points) == len(rows)
    assert [p["unique_optimal_plan"] for p in points] == [None] * len(points)
    report = every_face_failed.outputs.files["report.md"].decode("utf-8")
    # The report's sweep table flags no point "not unique" (backlog_penalty 0 is, when tested).
    sweep_rows = re.findall(r"^\| \w+ \| [\d.]+ \| optimal \|.*$", report, re.MULTILINE)
    assert len(sweep_rows) == 14
    assert not any("not unique" in line for line in sweep_rows)


@pytest.mark.integration
def test_an_incomplete_face_test_gives_no_verdict_in_frontier_csv(
    every_face_failed: SmokeOutputs,
) -> None:
    # _verdict rule in frontier(): a point whose after-tie-break test failed has
    # unique_after_tie_breaks None, not False, and is not counted as tied.
    rows = every_face_failed.rows("frontier.csv")
    assert sum(r["solver_status"] == "optimal" for r in rows) == 10
    assert [r["unique_after_tie_breaks"] for r in rows] == [""] * len(rows)
    frontier = every_face_failed.scenarios()[0]["frontier"]
    assert frontier["points_not_unique_after_tie_breaks"] == 0
    assert frontier["points_with_overtime_or_surge_totals_open"] == 0


@pytest.mark.integration
def test_an_incomplete_face_test_gives_no_verdict_in_metrics(
    every_face_failed: SmokeOutputs,
) -> None:
    # _verdict in _face_summary: optimal_face and reported_plan_after_tie_breaks.
    for scenario in every_face_failed.scenarios():
        assert scenario["optimal_face"]["unique"] is None
        assert scenario["reported_plan_after_tie_breaks"]["unique"] is None
        assert scenario["problems"]
        assert set(scenario["problems"]) == {FACE_FAILED}


# --- LIFT_CAP: a lifted daily limit capped below the input bound ----------------------------------


def test_a_capped_lift_that_binds_is_not_computed() -> None:
    # LIFT_CAP: every input is below 1e9, but all the work of the horizon (1.4e9 requests) is
    # not. The daily surge limit is lifted to the cap (5e8 a day), and the capped relaxation
    # is limited by it (it serves its whole capacity, 6e8 of 7e8 a day): the removal is not
    # exact, so it is "not computed" with the cap message (an ERROR), never the capped
    # violation (199,996,000) and never the magnitude error of an uncapped lift.
    problem = problem_of(
        unit(
            demand=(7e8, 7e8),
            regular_hours=(1e8, 1e8),
            overtime_productivity=10.0,
            overtime_max_hours=(0.0, 0.0),
            surge_max=(1e6, 1e6),
            overtime_total_hours=0.0,
            terminal_backlog_max=4000.0,
        )
    )
    lifted = {limit: relaxed for limit, _, relaxed in _relaxed_limits(problem.units[0])}
    assert LIFT_CAP == 5e8
    capped = lifted["surge_daily_limit"]
    assert capped.surge_max == (5e8, 5e8)
    attempt = solve_problem(
        replace(problem, units=(capped,)),
        SETTINGS,
        Objective.TERMINAL_VIOLATION,
        terminal=Terminal.SOFT,
        budget_row=False,
    )
    assert attempt.solved is not None
    plan = attempt.solved.plan.units[0]
    # The cap limits the relaxation (otherwise this instance no longer tests the rule).
    assert plan.served == pytest.approx((6e8, 6e8))
    assert plan.surge == pytest.approx((5e8, 5e8))
    assert attempt.value == pytest.approx(199_996_000.0)
    result = analysed(problem)
    assert result.problems == (
        "limit relaxation u:surge_daily_limit: the lifted limit binds at its cap of 5e+08, so "
        "the removal is not exact",
    )
    diagnosis = result.diagnosis
    assert diagnosis is not None
    assert diagnosis.limit_relaxations == {
        "u:overtime_daily_limit": pytest.approx(1_197_996_000.0),
        "u:surge_daily_limit": None,
        "u:overtime_total_limit": pytest.approx(1_197_996_000.0),
    }
    # Also the wording when a removal could not be computed (no "bind together").
    assert diagnosis.reasons[0] == (
        f"{OUT_OF_REACH}: the least violation is 1,197,996,000.0 requests; "
        f"{NONE_COMPUTED_LOWERS}; {NOT_COMPUTED}the daily surge limit of 1,000,000 requests "
        "(DIAGNOSTIC)"
    )


def _big_unit(**overrides: Any) -> ServiceUnit:
    """1.2e9 requests over four days (above the cap), 2e8 a day of regular capacity."""
    base: dict[str, Any] = {
        "demand": (3e8,) * 4,
        "regular_hours": (2e8,) * 4,
        "overtime_max_hours": (1e6,) * 4,
        "surge_max": (0.0,) * 4,
        "overtime_total_hours": 2e8,
        "terminal_backlog_max": 4000.0,
    }
    base.update(overrides)
    return unit(**base)


NOT_BINDING: dict[str, tuple[ServiceUnit, dict[str, float], str]] = {
    # The overtime total (2e8 h) keeps every day below the capped 5e8 h.
    "overtime_capped_work": (
        _big_unit(),
        {"u:overtime_daily_limit": 199_996_000.0, "u:overtime_total_limit": 395_996_000.0},
        "the daily overtime limit of 1,000,000 staff hours (to 199,996,000.0 requests without it)",
    ),
    # 4e8 requests (below the cap), but 1.6e9 overtime hours at 0.25 requests an hour: the
    # hours are capped (5e8), and the overtime total keeps every day below the cap.
    "overtime_capped_hours": (
        _big_unit(
            demand=(1e8,) * 4,
            regular_hours=(5e7,) * 4,
            overtime_productivity=0.25,
        ),
        {"u:overtime_daily_limit": 149_996_000.0, "u:overtime_total_limit": 198_996_000.0},
        "the daily overtime limit of 1,000,000 staff hours (to 149,996,000.0 requests without it)",
    ),
    # Surge from day 2, 1e6 a day, 2e8 in total; a day without surge stays without surge
    # (its lifted limit is 0, and 0 used there is not "at the cap").
    "surge": (
        _big_unit(
            overtime_max_hours=(0.0,) * 4,
            overtime_total_hours=0.0,
            surge_max=(0.0, 1e6, 1e6, 1e6),
            surge_total=2e8,
        ),
        {
            "u:overtime_daily_limit": 396_996_000.0,
            "u:surge_daily_limit": 199_996_000.0,
            "u:surge_delay": 395_996_000.0,
            "u:overtime_total_limit": 396_996_000.0,
            "u:surge_total_limit": 396_996_000.0,
        },
        "the daily surge limit of 1,000,000 requests (to 199,996,000.0 requests without it)",
    ),
}


@pytest.mark.parametrize("case", sorted(NOT_BINDING))
def test_a_capped_lift_that_does_not_bind_gives_the_exact_violation(case: str) -> None:
    # LIFT_CAP: a lift capped below all the work of the horizon whose cap the relaxed plan
    # does not reach is an exact removal: its least violation is reported and named, with
    # no problem (an uncapped lift beyond 1e9 was refused; a cap test that ignores what the
    # plan uses would call it "not computed").
    problem_unit, relaxations, named = NOT_BINDING[case]
    lifted = {limit: relaxed for limit, _, relaxed in _relaxed_limits(problem_unit)}
    daily = "surge_daily_limit" if case == "surge" else "overtime_daily_limit"
    caps = lifted[daily].surge_max if case == "surge" else lifted[daily].overtime_max_hours
    assert max(caps) == LIFT_CAP  # the lift is capped (otherwise this tests nothing)
    result = analysed(problem_of(problem_unit))
    assert result.problems == ()
    diagnosis = result.diagnosis
    assert diagnosis is not None
    assert diagnosis.limit_relaxations == {
        key: pytest.approx(value) for key, value in relaxations.items()
    }
    assert named in diagnosis.reasons[0]
    assert NOT_COMPUTED not in diagnosis.reasons[0]


# The noise instance NOISY["overtime_daily_limit"] of tests/test_m4_round3_diagnosis.py: the
# relaxed plan of the lifted daily surge limit keeps idle surge capacity at the lift (all the
# work of the horizon) on some day.
UNCAPPED: dict[str, Any] = {
    "demand": (669.0, 909.0, 19341.0, 17419.0),
    "initial_backlog": 14615.0,
    "regular_hours": (368.0, 198.0, 440.0, 458.0),
    "regular_productivity": 3.4,
    "overtime_productivity": 6.3,
    "overtime_max_hours": (346.0, 108.0, 279.0, 224.0),
    "surge_max": (0.0, 0.0, 1991.0, 2205.0),
    "regular_wage": 32.0,
    "overtime_wage": 31.0,
    "surge_price": 31.0,
    "backlog_penalty": 6.8,
    "terminal_penalty": 11.0,
    "overtime_total_hours": 515.0,
    "surge_total": None,
    "terminal_backlog_max": 1464.0,
}


def test_a_lift_that_is_not_capped_never_binds_at_the_cap() -> None:
    # LIFT_CAP / _cap_binds: a lift that reached all the work of the horizon is exact, even
    # where the relaxed plan parks idle capacity at it. Without the early return such a
    # removal was "not computed" with the cap message (an ERROR) and dropped from the reason.
    problem = problem_of(unit(**UNCAPPED))
    lifted = {limit: relaxed for limit, _, relaxed in _relaxed_limits(problem.units[0])}
    relaxed = lifted["surge_daily_limit"]
    attempt = solve_problem(
        replace(problem, units=(relaxed,)),
        SETTINGS,
        Objective.TERMINAL_VIOLATION,
        terminal=Terminal.SOFT,
        budget_row=False,
    )
    assert attempt.solved is not None
    used = attempt.solved.plan.units[0].surge
    # The instance still meets the lift on some day (otherwise it no longer tests the rule).
    assert any(
        cap > 0 and v >= cap - tolerance(cap)
        for v, cap in zip(used, relaxed.surge_max, strict=True)
    )
    result = analysed(problem)
    assert not any("binds at its cap" in p for p in result.problems)
    assert result.problems == ()
    diagnosis = result.diagnosis
    assert diagnosis is not None
    assert diagnosis.limit_relaxations["u:surge_daily_limit"] == pytest.approx(0.0, abs=1e-6)
    named = "the daily surge limit of 2,205 requests (to 0.0 requests without it)"
    assert named in diagnosis.reasons[0]


def test_cap_binds_only_for_a_capped_lift_used_to_its_cap() -> None:
    # _cap_binds, decision by decision: a capped lift binds where a day uses it to the cap
    # (and serves that whole capacity); not below it, not on a day without capacity (cap 0),
    # never for a lift that reached all the work of the horizon, and never for a removed
    # total.
    small = unit(
        demand=(10.0, 20.0),
        regular_hours=(0.0, 0.0),
        overtime_max_hours=(5.0, 5.0),
        surge_max=(0.0, 4.0),
        overtime_total_hours=None,
    )
    big = unit(
        demand=(6e8, 6e8),
        regular_hours=(1e8, 1e8),
        overtime_productivity=2.0,
        overtime_max_hours=(1e6, 1e6),
        surge_max=(0.0, 1e6),
        overtime_total_hours=None,
    )

    def decisions(configured: ServiceUnit, hours: Any, surge: Any) -> SimpleNamespace:
        served = tuple(
            configured.regular_capacity(day) + configured.overtime_productivity * h + s
            for day, (h, s) in enumerate(zip(hours, surge, strict=True))
        )
        return SimpleNamespace(served=served, overtime_hours=tuple(hours), surge=tuple(surge))

    for configured, capped in ((small, False), (big, True)):
        lifted = {limit: relaxed for limit, _, relaxed in _relaxed_limits(configured)}
        hours = lifted["overtime_daily_limit"].overtime_max_hours
        surge = lifted["surge_daily_limit"].surge_max
        assert hours == ((LIFT_CAP / 2,) * 2 if capped else (31.0, 31.0))
        assert surge == ((0.0, LIFT_CAP) if capped else (0.0, 31.0))
        at_lift = decisions(configured, hours, surge)
        below = decisions(configured, [h / 2 for h in hours], [s / 2 for s in surge])
        idle = decisions(configured, (0.0, 0.0), (0.0, 0.0))
        for limit in ("overtime_daily_limit", "surge_daily_limit"):
            assert _cap_binds(at_lift, limit, configured, lifted[limit]) is capped, limit
            assert _cap_binds(below, limit, configured, lifted[limit]) is False, limit
            assert _cap_binds(idle, limit, configured, lifted[limit]) is False, limit
        total = replace(configured, overtime_total_hours=10.0)
        removed = replace(total, overtime_total_hours=None)
        assert _cap_binds(at_lift, "overtime_total_limit", total, removed) is False


# --- The wording when a removal failed ------------------------------------------------------------


def test_a_failed_removal_never_claims_the_limits_bind_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # r4-diagnosis-03 (fixed in ce2b104): with no computed removal lowering the violation and
    # one removal failed, the reason said "no configured limit lowers it when removed alone
    # (the limits and the regular capacity bind together)" next to the untested limit, and
    # called the failure "(solver failure)" although it is an ERROR of the run.
    problem = problem_of(unit())
    # Neither removal lowers the 25 requests: the limits bind together.
    assert diagnose(problem, SETTINGS).reasons[0] == (
        f"{OUT_OF_REACH}: the least violation is 25.0 requests; {NONE_LOWERS} (DIAGNOSTIC)"
    )
    real = analysis.solve_problem

    def solve(problem: PlanningProblem, *args: Any, **kwargs: Any) -> analysis.Attempt:
        if problem.units[0].overtime_total_hours is None:  # the removed overtime total
            raise OptimizationError("injected")
        return real(problem, *args, **kwargs)

    monkeypatch.setattr(analysis, "solve_problem", solve)
    diagnosis = diagnose(problem, SETTINGS)
    assert diagnosis.limit_relaxations == {
        "u:overtime_daily_limit": pytest.approx(25.0),
        "u:overtime_total_limit": None,
    }
    assert diagnosis.reasons[0] == (
        f"{OUT_OF_REACH}: the least violation is 25.0 requests; {NONE_COMPUTED_LOWERS}; "
        f"{NOT_COMPUTED}the overtime total of 5 staff hours (DIAGNOSTIC)"
    )
    assert "bind together" not in diagnosis.reasons[0]


# --- _limit: configured limits and targets as configured ------------------------------------------


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (1.5, "1.5"),
        (2.25, "2.25"),
        (4000.4, "4,000.4"),
        (1234.5, "1,234.5"),
        (1500.75, "1,500.75"),
        (4000.0, "4,000"),
        (10.0, "10"),
        (1e6, "1,000,000"),
        (0.0, "0"),
        (0.4, "0.4"),
        (0.0125, "0.0125"),
        (0.123456, "0.123456"),
    ],
)
def test_limit_prints_a_configured_value_as_configured(value: float, text: str) -> None:
    # r4-diagnosis-06: whole numbers without decimals (with thousands separators), other
    # values of one unit or more with up to two decimals, below one three significant digits.
    assert _limit(value) == text


@pytest.mark.parametrize(
    ("overrides", "named"),
    [
        (
            {"overtime_max_hours": (1.5,) * 3, "overtime_total_hours": None},
            "the daily overtime limit of 1.5 staff hours (to 0.0 requests without it)",
        ),
        (
            {"overtime_max_hours": (0.0125,) * 3, "overtime_total_hours": None},
            "the daily overtime limit of 0.0125 staff hours (to 0.0 requests without it)",
        ),
        (
            {"overtime_total_hours": 1.5},
            "the overtime total of 1.5 staff hours (to 25.0 requests without it)",
        ),
        (
            {
                "overtime_max_hours": (0.0,) * 3,
                "overtime_total_hours": None,
                "surge_max": (1.5,) * 3,
            },
            "the daily surge limit of 1.5 requests (to 0.0 requests without it)",
        ),
        (
            {
                "overtime_max_hours": (0.0,) * 3,
                "overtime_total_hours": None,
                "surge_max": (5.0,) * 3,
                "surge_total": 2.5,
            },
            "the surge total of 2.5 requests (to 25.0 requests without it)",
        ),
        (
            {
                "overtime_max_hours": (0.0,) * 3,
                "overtime_total_hours": None,
                "surge_capacity_when_available": 1.5,
            },
            "the surge start after the horizon (surge of 1.5 requests a day is never "
            "available) (to 28.5 requests without it)",
        ),
    ],
    ids=[
        "overtime_daily",
        "overtime_daily_below_one",
        "overtime_total",
        "surge_daily",
        "surge_total",
        "surge_start",
    ],
)
def test_the_diagnosis_names_limits_as_configured(overrides: dict[str, Any], named: str) -> None:
    # r4-diagnosis-06: every limit description used _amount(x, 0), so 1.5 read as 2 (33 %
    # off) and 0.0125 as 0.01.
    reason = diagnose(problem_of(unit(**overrides)), SETTINGS).reasons[0]
    assert reason.startswith(OUT_OF_REACH)
    assert named in reason


def test_the_analytic_bound_prints_the_target_as_configured() -> None:
    # r4-diagnosis-06: a target of 4,000.4 read "0.05 above the target of 4,000".
    problem = problem_of(
        unit(
            demand=(4030.45,),
            regular_hours=(0.0,),
            overtime_max_hours=(30.0,),
            surge_max=(0.0,),
            overtime_total_hours=None,
            terminal_backlog_max=4000.4,
        )
    )
    diagnosis = diagnose(problem, SETTINGS)
    assert diagnosis.terminal_lower_bound == {"u": pytest.approx(4000.45)}
    assert diagnosis.reasons[-1] == (
        "u: even serving at maximum capacity every day leaves 4,000.4 requests at the end of "
        "the horizon, 0.05 above the target of 4,000.4"
    )
