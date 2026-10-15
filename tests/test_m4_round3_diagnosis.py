"""Regression tests for the third review of the infeasibility diagnosis (commit a7ca6a6).

Each test reproduces a defect of the diagnosis at 0794ba7 and passes after the fix: limits
named for a noise-sized drop, an initial backlog left out of a lifted daily limit, a failed
limit removal named as lowering the violation or dropped silently, a lifted limit beyond
the magnitude bound aborting the run, a surge configured to start after the horizon never
tested, two tolerances that let a scenario be both out of reach and BORDERLINE, limits and
bounds rounded to whole units, and wording that called a removed limit "relaxed".
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

import atlas.decision_analysis as analysis
from atlas.config import Settings, load_settings
from atlas.decision_analysis import LimitRelaxation, analyse_scenario, diagnose
from atlas.issues import Severity
from atlas.optimization_config import parse_optimization_config
from atlas.optimization_runner import load_inputs, m4_outputs
from atlas.optimizer import Objective, OptimizationError, SolverSettings, SolveStatus
from atlas.planning import PlanningError, PlanningProblem, ServiceUnit, tolerance
from atlas.runner import SnapshotData, load_series
from atlas.scenarios import build_unit
from tests.conftest import REPO_ROOT

SETTINGS = SolverSettings()
SMOKE = REPO_ROOT / "configs" / "optimization" / "smoke.json"
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
METRICS_KEY = "least_violation_unlimited_cash_with_one_limit_removed_requests"


def unit(**overrides: Any) -> ServiceUnit:
    """All 30 requests arrive on day 3; 5 overtime hours in total; target 0."""
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


def overtime_total_unit() -> ServiceUnit:
    # 200 requests, 20 regular; the 10-hour overtime total binds, the daily limits do not.
    return unit(
        demand=(100.0, 100.0),
        regular_hours=(10.0, 10.0),
        overtime_max_hours=(10.0, 10.0),
        surge_max=(0.0, 0.0),
        surge_price=5.0,
        overtime_total_hours=10.0,
    )


# --- LimitRelaxation.lowers: one tolerance, and noise is not a drop ---------------------------


def _relaxation(least_violation: float | None) -> LimitRelaxation:
    attempt = analysis.Attempt(Objective.TERMINAL_VIOLATION, SolveStatus.OPTIMAL, "", 0)
    return LimitRelaxation("u", "overtime_daily_limit", "a limit", least_violation, attempt)


@pytest.mark.parametrize(
    ("relaxed", "least", "reach", "lowers"),
    [
        # A programme that failed never lowers anything (R3 test-strength-05, M21).
        (None, 170.0, 0.0, False),
        # A 1-ulp drop at a realistic magnitude is solver noise (test-strength-04).
        (1307.0763307205634, 1307.0763307205636, 0.0, False),
        # Within the plan check's tolerance of the target: it reaches it, although the drop
        # (3.5e-6) is less than half the violation and less than 10 * tolerance (fd-duals-02).
        (4.5e-6, 8e-6, 5e-6, True),
        # A drop of 1e-5 from 1.5e-5: more than half the violation, below 10 * tolerance.
        (5e-6, 1.5e-5, 1e-6, True),
        # The violation is itself within the tolerance: there is nothing to lower.
        (0.0, 4e-6, 5e-6, False),
    ],
)
def test_lowers_counts_a_reached_target_and_ignores_solver_noise(
    relaxed: float | None, least: float, reach: float, lowers: bool
) -> None:
    assert _relaxation(relaxed).lowers(least, reach) is lowers


# Random single-unit instances at reference-like magnitudes (seeds 275 and 345 of a search
# like search_relax_noise.py, rounded): removing the limit lowers the least violation by
# 1-2 ulps only.
NOISY: dict[str, tuple[dict[str, Any], str, str, tuple[str, ...]]] = {
    "overtime_daily_limit": (
        {
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
        },
        "u:overtime_daily_limit",
        "the daily overtime limit of 346 staff hours",
        (
            "the daily surge limit of 2,205 requests (to 0.0 requests without it)",
            "the surge delay of 2 days (to 34,660.9 requests without it)",
            "the overtime total of 515 staff hours (to 36,286.3 requests without it)",
        ),
    ),
    "surge_delay": (
        {
            "demand": (5224.0, 1194.0, 4960.0, 6434.0),
            "initial_backlog": 273.0,
            "regular_hours": (292.0, 260.0, 379.0, 303.0),
            "regular_productivity": 2.1,
            "overtime_productivity": 4.4,
            "overtime_max_hours": (67.0, 3.0, 41.0, 74.0),
            "surge_max": (0.0, 496.0, 570.0, 430.0),
            "regular_wage": 48.0,
            "overtime_wage": 53.0,
            "surge_price": 7.0,
            "backlog_penalty": 8.5,
            "terminal_penalty": 13.0,
            "overtime_total_hours": 181.0,
            "surge_total": 1114.0,
            "terminal_backlog_max": 304.0,
        },
        "u:surge_delay",
        "the surge delay of 1 days",
        (
            "the overtime total of 181 staff hours (to 13,261.6 requests without it)",
            "the surge total of 1,114 requests (to 12,897.2 requests without it)",
        ),
    ),
}


@pytest.mark.parametrize("case", sorted(NOISY))
def test_a_noise_sized_drop_does_not_name_the_limit(case: str) -> None:
    # test-strength-04: without the tolerance in LimitRelaxation.lowers, the reason names a
    # limit whose removal changes nothing and quotes the unchanged violation.
    overrides, key, description, named = NOISY[case]
    diagnosis = diagnose(problem_of(unit(**overrides)), SETTINGS)
    least = diagnosis.least_violation_unlimited
    relaxed = diagnosis.limit_relaxations[key]
    assert least is not None
    assert relaxed is not None
    # The instance still meets solver noise (otherwise it no longer tests the tolerance).
    assert 0 < least - relaxed <= 10 * tolerance(least)
    reason = diagnosis.reasons[0]
    assert description not in reason
    assert reason.endswith(REMOVED + "; ".join(named) + " (DIAGNOSTIC)")


# --- Construction and failure of the limit removals -------------------------------------------


def test_the_lifted_daily_limit_covers_the_initial_backlog() -> None:
    # test-strength-05 (M19): 100 requests of initial backlog and 1 a day, 10 overtime hours a
    # day. A daily limit "lifted" to the demand alone (3 h) cannot clear the backlog, so the
    # only limit that matters was no longer named.
    problem = problem_of(
        unit(
            demand=(1.0, 1.0),
            initial_backlog=100.0,
            regular_hours=(0.0, 0.0),
            overtime_max_hours=(10.0, 10.0),
            surge_max=(0.0, 0.0),
            overtime_total_hours=None,
        )
    )
    diagnosis = diagnose(problem, SETTINGS)
    assert diagnosis.least_violation_unlimited == pytest.approx(82.0)
    assert diagnosis.limit_relaxations == {"u:overtime_daily_limit": pytest.approx(0.0, abs=1e-9)}
    assert diagnosis.reasons[0].endswith(
        REMOVED + "the daily overtime limit of 10 staff hours (to 0.0 requests without it) "
        "(DIAGNOSTIC)"
    )


def _fail_lifted_overtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """The removal of the daily overtime limit (raised above 100 h) comes back infeasible,
    as a false HiGHS 'infeasible' at scale would."""
    real = analysis.solve_problem

    def solve(problem: PlanningProblem, *args: Any, **kwargs: Any) -> analysis.Attempt:
        attempt = real(problem, *args, **kwargs)
        if problem.units[0].overtime_max_hours[0] > 100.0:
            return replace(attempt, status=SolveStatus.INFEASIBLE, solved=None)
        return attempt

    monkeypatch.setattr(analysis, "solve_problem", solve)


def test_a_non_optimal_limit_removal_is_a_problem_and_never_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # test-strength-05 (M21) and test-strength-15: a soft-target programme is always feasible,
    # so a non-optimal removal is a solver failure. It was dropped silently (no problem, no
    # ERROR), and with lowers() true on None it was named "(to 0.0 requests)".
    _fail_lifted_overtime(monkeypatch)
    problem = problem_of(overtime_total_unit())
    diagnosis = diagnose(problem, SETTINGS)
    assert diagnosis.limit_relaxations == {
        "u:overtime_daily_limit": None,
        "u:overtime_total_limit": pytest.approx(160.0),
    }
    assert diagnosis.reasons[0].endswith(
        REMOVED + "the overtime total of 10 staff hours (to 160.0 requests without it); "
        "not computed (an ERROR of this run): the daily overtime limit of 10 staff hours "
        "(DIAGNOSTIC)"
    )
    result = analyse_scenario(
        "relax_fail", problem, SETTINGS, {"u": 0.0}, shadow_steps=None, frontier_points=None
    )
    assert result.problems == (
        "limit relaxation u:overtime_daily_limit is infeasible although the soft-target "
        "programme is always feasible",
    )


def test_a_limit_lifted_beyond_the_magnitude_bound_is_an_error_not_an_abort() -> None:
    # diagnosis-fd-duals-03: every input is below 1e9, but the daily surge limit lifted to
    # the horizon's work (1.2e9 requests) is refused by the magnitude check. The whole
    # analysis used to abort with OptimizationError; now that removal is an ERROR and is
    # reported as not computed, and the other removals are still tested.
    problem = problem_of(
        unit(
            demand=(6e8, 6e8),
            regular_hours=(1e8, 1e8),
            overtime_productivity=2.25,
            overtime_max_hours=(1e6, 1e6),
            surge_max=(1e6, 1e6),
            overtime_wage=60.0,
            surge_price=40.0,
            overtime_total_hours=None,
            terminal_backlog_max=4000.0,
        ),
        budget=3e8,
    )
    result = analyse_scenario(
        "big", problem, SETTINGS, {"u": 0.0}, shadow_steps=None, frontier_points=None
    )
    assert result.feasibility == "relaxed_terminal_target"
    # Lifting a daily limit to all the work of the horizon (1.2e9 requests) would exceed the
    # input bound, so both lifts are capped at 5e8 a day. The capped relaxations reach the
    # target with capacity to spare (idle capacity parked at the cap does not bind), so both
    # removals are exact: no ERROR, and both limits are named.
    assert result.problems == ()
    assert result.diagnosis is not None
    assert result.diagnosis.limit_relaxations == {
        "u:overtime_daily_limit": pytest.approx(0.0, abs=1e-6),
        "u:surge_daily_limit": pytest.approx(0.0, abs=1e-6),
    }
    assert result.diagnosis.reasons[0].endswith(
        REMOVED + "the daily overtime limit of 1,000,000 staff hours (to 0.0 requests without "
        "it); the daily surge limit of 1,000,000 requests (to 0.0 requests without it) "
        "(DIAGNOSTIC)"
    )


# --- A surge configured to start after the horizon --------------------------------------------


def test_a_surge_starting_after_the_horizon_is_tested() -> None:
    # diagnosis-fd-duals-01: surge of 10 requests a day, configured to start on day 4 of 3.
    # It was never relaxed, and the reason said no configured limit lowers the violation;
    # surge from day 1 serves 10 of the 30 requests that arrive on day 3.
    diagnosis = diagnose(problem_of(unit(surge_capacity_when_available=10.0)), SETTINGS)
    assert diagnosis.least_violation_unlimited == pytest.approx(25.0)
    assert diagnosis.limit_relaxations == {
        "u:overtime_daily_limit": pytest.approx(25.0),
        "u:surge_delay": pytest.approx(15.0),
        "u:overtime_total_limit": pytest.approx(25.0),
    }
    assert diagnosis.reasons[0].endswith(
        REMOVED + "the surge start after the horizon (surge of 10 requests a day is never "
        "available) (to 15.0 requests without it) (DIAGNOSTIC)"
    )
    with pytest.raises(PlanningError, match="u limits must be finite and non-negative"):
        unit(surge_capacity_when_available=-1.0)


def test_build_unit_keeps_the_surge_capacity_configured_after_the_horizon() -> None:
    # diagnosis-fd-duals-01 through the configuration: surge_available_from_day = horizon + 1
    # ("never within the horizon") gives an all-zero surge_max, so the unit must carry the
    # configured 500 requests a day for the diagnosis to test it.
    payload = json.loads(SMOKE.read_text(encoding="utf-8"))
    demand = (13000.0,) * payload["horizon_days"]

    def built_from(day: int) -> ServiceUnit:
        payload["service_units"][0]["surge_available_from_day"]["value"] = day
        config = parse_optimization_config(payload)
        return build_unit(config.unit, demand, config.scenario("normal"))

    never, last_day = built_from(11), built_from(10)
    assert never.surge_max == (0.0,) * 10
    assert never.surge_capacity_when_available == 500.0
    assert last_day.surge_max == (0.0,) * 9 + (500.0,)
    assert last_day.surge_capacity_when_available == 500.0
    diagnosis = diagnose(problem_of(never), SETTINGS)
    assert diagnosis.limit_relaxations == {
        "citywide:overtime_daily_limit": pytest.approx(19200.0),
        "citywide:surge_delay": pytest.approx(14200.0),
        "citywide:overtime_total_limit": pytest.approx(14800.0),
    }
    # Surge from day 1 either way: the same violation as removing the 9-day delay.
    delayed = diagnose(problem_of(last_day), SETTINGS).limit_relaxations
    assert diagnosis.limit_relaxations["citywide:surge_delay"] == pytest.approx(
        delayed["citywide:surge_delay"]
    )
    assert diagnosis.reasons[0].endswith(
        REMOVED + "the surge start after the horizon (surge of 500 requests a day is never "
        "available) (to 14,200.0 requests without it); the overtime total of 3,000 staff "
        "hours (to 14,800.0 requests without it) (DIAGNOSTIC)"
    )


# --- One tolerance for "out of reach", BORDERLINE and a named removal -------------------------


def _small_gap(gap: float, target: float) -> PlanningProblem:
    # One day, target + 10 requests, 10 - gap overtime hours: the least violation is gap.
    return problem_of(
        unit(
            demand=(target + 10.0,),
            regular_hours=(0.0,),
            overtime_max_hours=(10.0 - gap,),
            surge_max=(0.0,),
            overtime_total_hours=None,
            terminal_backlog_max=target,
        )
    )


def test_a_violation_within_the_check_tolerance_is_not_called_out_of_reach() -> None:
    # diagnosis-fd-duals-02: a least violation of 3e-6 against a target of 4,000 is within
    # the plan check's tolerance (5e-6). It was also called out of reach "with any amount of
    # discretionary cash" while the same diagnosis said BORDERLINE.
    diagnosis = diagnose(_small_gap(3e-6, 4000.0), SETTINGS)
    assert diagnosis.borderline is True
    assert not any(OUT_OF_REACH in reason for reason in diagnosis.reasons)
    assert diagnosis.reasons[0].startswith(
        "with unlimited discretionary cash the least terminal-target violation is 3e-06 "
        "requests, within the checking tolerance"
    )
    assert diagnosis.reasons[-1].startswith("BORDERLINE: ")
    assert diagnosis.limit_relaxations == {}


@pytest.mark.parametrize("gap", [3e-6, 8e-6])
def test_a_removal_that_reaches_the_target_is_named(gap: float) -> None:
    # diagnosis-fd-duals-02: a least violation between 1e-6 and 1e-5 is out of reach (target
    # 0), and removing the daily overtime limit reaches the target; a drop of less than
    # 10 * tolerance used not to count, so the reason said no configured limit lowers it.
    diagnosis = diagnose(_small_gap(gap, 0.0), SETTINGS)
    assert diagnosis.borderline is False
    assert diagnosis.limit_relaxations == {"u:overtime_daily_limit": pytest.approx(0.0, abs=1e-9)}
    assert diagnosis.reasons[0].startswith(OUT_OF_REACH)
    # The configured limit is 10 - gap staff hours, printed as configured.
    assert diagnosis.reasons[0].endswith(
        REMOVED + f"the daily overtime limit of {10.0 - gap:,} staff hours (to 0.0 requests "
        "without it) (DIAGNOSTIC)"
    )


# --- Limits and bounds below one unit ------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "named"),
    [
        (
            {"overtime_max_hours": (0.4,) * 3, "overtime_total_hours": None},
            "the daily overtime limit of 0.4 staff hours (to 0.0 requests without it)",
        ),
        (
            {
                "overtime_max_hours": (0.0,) * 3,
                "overtime_total_hours": None,
                "surge_max": (0.5,) * 3,
            },
            "the daily surge limit of 0.5 requests (to 0.0 requests without it)",
        ),
        (
            {"overtime_total_hours": 0.4},
            "the overtime total of 0.4 staff hours (to 25.0 requests without it)",
        ),
        (
            {
                "overtime_max_hours": (0.0,) * 3,
                "overtime_total_hours": None,
                "surge_max": (5.0,) * 3,
                "surge_total": 0.25,
            },
            "the surge total of 0.25 requests (to 25.0 requests without it)",
        ),
        (
            {
                "overtime_max_hours": (0.0,) * 3,
                "overtime_total_hours": None,
                "surge_capacity_when_available": 0.5,
            },
            "the surge start after the horizon (surge of 0.5 requests a day is never "
            "available) (to 29.5 requests without it)",
        ),
    ],
    ids=["overtime_daily", "surge_daily", "overtime_total", "surge_total", "surge_start"],
)
def test_limits_below_one_unit_keep_three_significant_digits(
    overrides: dict[str, Any], named: str
) -> None:
    # diagnosis-fd-duals-07: a 0.4 h/day limit read "the daily overtime limit of 0 staff
    # hours", as if overtime were unavailable.
    reason = diagnose(problem_of(unit(**overrides)), SETTINGS).reasons[0]
    assert named in reason


def test_the_analytic_bound_states_its_excess_over_the_target() -> None:
    # diagnosis-fd-duals-07: a bound of 4,000.04 against a target of 4,000 read "leaves
    # 4,000.0 requests at the end of the horizon (target 4,000)", as if it met the target.
    problem = problem_of(
        unit(
            demand=(4010.04,),
            regular_hours=(0.0,),
            overtime_max_hours=(10.0,),
            surge_max=(0.0,),
            overtime_total_hours=None,
            terminal_backlog_max=4000.0,
        )
    )
    diagnosis = diagnose(problem, SETTINGS)
    assert diagnosis.terminal_lower_bound == {"u": pytest.approx(4000.04)}
    assert diagnosis.reasons[-1] == (
        "u: even serving at maximum capacity every day leaves 4,000.0 requests at the end of "
        "the horizon, 0.04 above the target of 4,000"
    )


# --- A removed limit is described as removed ------------------------------------------------------


def test_removed_limits_are_described_as_removed() -> None:
    # integrity-of-claims-04: the figures are for removing each limit entirely, but the
    # reason said "relaxed one at a time ... (to 160.0 requests)" and the metrics key said
    # "relaxed", which reads as if a modest relaxation reached that violation.
    diagnosis = diagnose(problem_of(overtime_total_unit()), SETTINGS)
    assert diagnosis.reasons[0] == (
        f"{OUT_OF_REACH}: the least violation is 170.0 requests; "
        + REMOVED
        + "the overtime total of 10 staff hours (to 160.0 requests without it) (DIAGNOSTIC)"
    )
    payload = diagnosis.to_dict()
    assert payload[METRICS_KEY] == {
        "u:overtime_daily_limit": pytest.approx(170.0),
        "u:overtime_total_limit": pytest.approx(160.0),
    }
    assert "least_violation_unlimited_cash_with_one_limit_relaxed_requests" not in payload
    # Surge configured but with no capacity recorded: nothing else to remove.
    none_lowers = diagnose(problem_of(unit()), SETTINGS).reasons[0]
    assert none_lowers == (
        f"{OUT_OF_REACH}: the least violation is 25.0 requests; {NONE_LOWERS} (DIAGNOSTIC)"
    )


# --- Through the runner ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def smoke_data() -> tuple[Settings, SnapshotData]:
    settings = load_settings(repo_root=REPO_ROOT, env={})
    _, data = load_series(settings, "round3_diagnosis")
    assert data is not None
    return settings, data


def _run(smoke_data: tuple[Settings, SnapshotData], payload: dict[str, Any]) -> Any:
    settings, data = smoke_data
    config = parse_optimization_config(payload)
    return m4_outputs(settings, config, load_inputs(settings, config, data))


def _diagnosis(outputs: Any, scenario_id: str) -> dict[str, Any]:
    metrics = json.loads(outputs.files["metrics.json"])
    scenario = next(s for s in metrics["scenarios"] if s["scenario_id"] == scenario_id)
    diagnosis: dict[str, Any] = scenario["diagnosis"]
    return diagnosis


@pytest.mark.integration
def test_a_failed_limit_removal_is_an_error_of_the_run(
    smoke_data: tuple[Settings, SnapshotData], monkeypatch: pytest.MonkeyPatch
) -> None:
    # test-strength-15: a removal the solver refuses marks the run incomplete (an ERROR on
    # its scenario) and is reported as not computed, never as "does not lower it".
    real = analysis.solve_problem

    def solve(problem: PlanningProblem, *args: Any, **kwargs: Any) -> analysis.Attempt:
        if max(problem.units[0].surge_max) > 500.0:  # the lifted daily surge limit
            raise OptimizationError("injected")
        return real(problem, *args, **kwargs)

    monkeypatch.setattr(analysis, "solve_problem", solve)
    outputs = _run(smoke_data, json.loads(SMOKE.read_text(encoding="utf-8")))
    errors = [
        (i.code, i.subject, i.message) for i in outputs.issues if i.severity is Severity.ERROR
    ]
    assert errors == [
        (
            "check_failed",
            "m3_marginal_q90_stress",
            "limit relaxation citywide:surge_daily_limit failed: injected",
        )
    ]
    diagnosis = _diagnosis(outputs, "m3_marginal_q90_stress")
    assert diagnosis[METRICS_KEY]["citywide:surge_daily_limit"] is None
    assert diagnosis["reasons"][0].endswith(
        REMOVED + "the overtime total of 3,000 staff hours (to 6,537.0 requests without it); "
        "not computed (an ERROR of this run): the daily surge limit of 500 requests (DIAGNOSTIC)"
    )


@pytest.mark.integration
def test_a_run_tests_a_surge_configured_after_the_horizon(
    smoke_data: tuple[Settings, SnapshotData],
) -> None:
    # diagnosis-fd-duals-01 end to end: with surge from day 11 of 10, the q0.9 scenario's
    # reason and metrics.json include the surge start among the removed limits.
    payload = json.loads(SMOKE.read_text(encoding="utf-8"))
    payload["service_units"][0]["surge_available_from_day"]["value"] = 11
    outputs = _run(smoke_data, payload)
    diagnosis = _diagnosis(outputs, "m3_marginal_q90_stress")
    relaxations = diagnosis[METRICS_KEY]
    assert set(relaxations) == {
        "citywide:overtime_daily_limit",
        "citywide:overtime_total_limit",
        "citywide:surge_delay",
    }
    least = diagnosis["least_terminal_violation_unlimited_discretionary_cash_requests"]
    assert relaxations["citywide:surge_delay"] < least - 1.0
    assert (
        "the surge start after the horizon (surge of 500 requests a day is never available) "
        "(to " in diagnosis["reasons"][0]
    )
    assert not any(i.severity is Severity.ERROR for i in outputs.issues)
