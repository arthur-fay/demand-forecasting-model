"""Fail-closed M4 commands: run, verify and inspect a hypothetical decision simulation.

``atlas optimize run`` follows the M2/M3 pattern:

1. integrity and validation of the snapshot (:func:`atlas.runner.load_series`); nothing is
   computed if either fails;
2. the provenance chain of every input (:mod:`atlas.decision_inputs`): the committed M2
   projection, the complete M2 run that recommended its model, the complete M3 run whose
   calibration status is attached to every result, and, for a stress path, the M3
   projection with intervals. All of them must describe the snapshot just validated;
3. the scenarios (:mod:`atlas.scenarios`) and every analysis (:mod:`atlas.decision_analysis`);
4. deterministic files and a manifest written atomically (:func:`atlas.experiment.write_run`).

Any failed independent check (certificate, plan validation, objective agreement,
shadow-price bracket, proven monotonicity, policy invariants) is an ERROR: the run is then
marked incomplete. A scenario whose strict model is infeasible is a WARNING, never an
error: infeasibility is a result. ``atlas optimize verify`` re-runs a recorded run with
its recorded configuration and requires byte-identical result files; ``atlas optimize
inspect`` checks a run directory against its manifest without recomputing anything.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pandas as pd

from atlas.config import Settings
from atlas.decision_analysis import (
    RELAXED_TERMINAL_TARGET,
    Attempt,
    FiniteDifference,
    MonotonicityCheck,
    ScenarioResult,
    SweepPoint,
    analyse_scenario,
    format_binding,
    pareto_relation,
    run_sweep,
    terminal_violation,
)
from atlas.decision_inputs import (
    MARGINAL_QUANTILE_PATH,
    M2Projection,
    M3Status,
    QuantilePath,
    check_same_data,
    load_demand_percentile,
    load_m2_projection,
    load_m3_quantile_path,
    load_m3_status,
)
from atlas.errors import ConfigError
from atlas.experiment import (
    MANIFEST_SCHEMA_VERSION,
    code_fingerprint,
    csv_bytes,
    environment_info,
    git_info,
    json_bytes,
    result_fingerprint,
    sha256_bytes,
    utc_now,
    write_run,
)
from atlas.issues import CheckReport, Issue, Severity
from atlas.optimization_config import (
    MarginalQuantileBasis,
    OptimizationConfig,
    load_optimization_config,
    parse_optimization_config,
)
from atlas.optimization_report import render_optimization_report
from atlas.optimizer import (
    OptimizationError,
    SolverSettings,
    SolveStatus,
    solver_versions,
)
from atlas.planning import UNITS, Plan, PlanCheck, PlanEvaluation, binding_constraints
from atlas.runner import RUNS_DIRNAME, SnapshotData, _check_output_dir, _read_json, load_series
from atlas.scenarios import Scenario, build_scenarios, capacity_near_percentile
from atlas.snapshot import sha256_file

RECORD_TYPE_DECISION_SIMULATION: Final = "hypothetical_decision_simulation"
LABEL: Final = (
    "HYPOTHETICAL DECISION SIMULATION: exploratory resource-allocation scenarios computed "
    "from an unvalidated point forecast with HYPOTHETICAL operational parameters. Not a "
    "validated staffing, budget or service-capacity recommendation for any NYC agency."
)
RESULT_FILES: Final = (
    "scenarios.csv",
    "plans.csv",
    "duals.csv",
    "sensitivity.csv",
    "frontier.csv",
    "metrics.json",
)
STRUCTURAL_ASSUMPTIONS: Final = (
    "Demand is the daily count of 311 service requests created (intake), not measured "
    "workload: one request is one unit of work, whatever its type or agency.",
    "One city-wide pool: staff and capacity are fully interchangeable across the agencies "
    "and request types that the city-wide count mixes. A split by agency with the same total "
    "capacity can only leave as much backlog or more, so pooled backlogs are optimistic.",
    "The repository holds no closures, backlog, staffing, service-level or cost data: every "
    "operational parameter, the initial backlog included, is HYPOTHETICAL.",
    "Quantities are continuous (fractional requests and staff hours); arrivals can be "
    "served on the day they arrive; capacity not used on a day is lost.",
    "Demand is treated as known when planning (a deterministic model of the M2 point "
    "forecast or of a labelled stress path), although the forecast's historical error is "
    "large and the M3 intervals failed their calibration criteria; no chance constraint "
    "and no probability of meeting any target is computed.",
    "The penalised score adds HYPOTHETICAL notional penalty weights to discretionary cash. "
    "It ranks plans for one set of weights; it is not a cost and is never compared as one.",
)
LABEL_COLUMNS: Final = (
    "record_type",
    "parameter_provenance",
    "validated_recommendation",
    "point_forecast_validated",
    "m3_interval_status",
)
OPTIMIZED: Final = "optimized"
WITHOUT_TARGET: Final = "optimized_without_terminal_target"
#: How each reported plan is chosen (a policy plan follows its rule).
SELECTION: Final = {
    OPTIMIZED: "optimal score, then least cash, least terminal backlog, least request-days",
    RELAXED_TERMINAL_TARGET: (
        "least target violation within the budget, then optimal score, least cash, least "
        "terminal backlog, least request-days"
    ),
    WITHOUT_TARGET: (
        "optimal score without the target, then least cash, least terminal backlog, least "
        "request-days"
    ),
    "lp_same_cash": (
        "least request-days at the policy's cash, then optimal score, least cash, least "
        "terminal backlog"
    ),
    "lp_same_backlog": (
        "least cash at the policy's request-days and terminal backlog, then optimal score, "
        "least terminal backlog, least request-days"
    ),
}


@dataclass(frozen=True)
class DecisionInputs:
    projection: M2Projection
    status: M3Status
    quantiles: Mapping[float, QuantilePath]
    demand_percentile: float
    data: SnapshotData
    percentile_sha256: str
    #: SHA-256 of projection_intervals.csv, checked against its pin even when no scenario
    #: uses a marginal quantile path (a pin is never recorded unchecked).
    intervals_sha256: str | None = None

    def provenance(self) -> dict[str, Any]:
        return {
            "snapshot": self.data.provenance,
            "forecast": dict(self.projection.provenance),
            "forecast_accuracy": self.projection.accuracy.to_dict(),
            "uncertainty_status": self.status.to_dict(),
            "stress_paths": {
                f"{q:g}": dict(path.provenance) for q, path in sorted(self.quantiles.items())
            },
            "legacy_demand_percentile_requests_per_day": self.demand_percentile,
            "legacy_demand_percentile_source_sha256": self.percentile_sha256,
        }


def _check_pins(config: OptimizationConfig, inputs: DecisionInputs) -> None:
    """Every input file must be the one whose SHA-256 the configuration pins."""
    forecast, uncertainty = config.forecast, config.uncertainty
    found = {
        "forecast.projections_sha256": inputs.projection.provenance.get("projections_sha256"),
        "forecast.m2_config_fingerprint": inputs.projection.provenance.get("config_fingerprint"),
        "uncertainty.calibration_status_sha256": inputs.status.provenance.get("sha256"),
        "uncertainty.projection_exceedance_sha256": inputs.percentile_sha256,
        "uncertainty.projection_intervals_sha256": inputs.intervals_sha256,
    }
    pinned = {
        "forecast.projections_sha256": forecast.projections_sha256,
        "forecast.m2_config_fingerprint": forecast.m2_config_fingerprint,
        "uncertainty.calibration_status_sha256": uncertainty.calibration_status_sha256,
        "uncertainty.projection_exceedance_sha256": uncertainty.projection_exceedance_sha256,
        "uncertainty.projection_intervals_sha256": uncertainty.projection_intervals_sha256,
    }
    for path in inputs.quantiles.values():
        # The stress path must come from the pinned file too.
        found["uncertainty.projection_intervals_sha256 (stress path)"] = path.provenance.get(
            "projection_intervals_sha256"
        )
        pinned["uncertainty.projection_intervals_sha256 (stress path)"] = (
            uncertainty.projection_intervals_sha256
        )
    wrong = sorted(name for name in pinned if found.get(name) != pinned[name])
    if wrong:
        raise ConfigError(
            "input files differ from the SHA-256 values pinned in the configuration: "
            + ", ".join(wrong)
        )


def load_inputs(
    settings: Settings, config: OptimizationConfig, data: SnapshotData
) -> DecisionInputs:
    """Every input with its provenance chain checked (see :mod:`atlas.decision_inputs`)."""
    if settings.snapshot_id != config.snapshot_id:
        raise ConfigError(
            f"the configuration is for snapshot {config.snapshot_id!r}, but "
            f"{settings.snapshot_id!r} is selected (--snapshot or ATLAS_SNAPSHOT_ID)"
        )
    root = settings.repo_root
    projection = load_m2_projection(
        root,
        config.forecast.run_dir,
        config.forecast.model_id,
        config.forecast.backtest_run_dir,
    )
    if projection.data_fingerprint != data.fingerprint:
        raise ConfigError("the M2 projection was made on different data than the snapshot")
    status = load_m3_status(root, config.uncertainty.calibration_status)
    check_same_data(projection, status)
    quantiles = {}
    for scenario in config.scenarios:
        basis = scenario.demand_basis
        if isinstance(basis, MarginalQuantileBasis) and basis.quantile not in quantiles:
            quantiles[basis.quantile] = load_m3_quantile_path(
                root, config.uncertainty.projection_run_dir, status, projection, basis.quantile
            )
    percentile = load_demand_percentile(root, config.uncertainty.projection_run_dir, status)
    run_dir = root / config.uncertainty.projection_run_dir
    exceedance = run_dir / "projection_exceedance.csv"
    intervals = run_dir / "projection_intervals.csv"
    inputs = DecisionInputs(
        projection,
        status,
        quantiles,
        percentile,
        data,
        sha256_file(exceedance),
        sha256_file(intervals) if intervals.is_file() else None,
    )
    _check_pins(config, inputs)
    return inputs


def solver_settings(config: OptimizationConfig) -> SolverSettings:
    solver = config.solver
    return SolverSettings(
        method=solver.method,
        presolve=solver.presolve,
        primal_feasibility_tolerance=solver.primal_feasibility_tolerance,
        dual_feasibility_tolerance=solver.dual_feasibility_tolerance,
        maxiter=solver.maxiter,
    )


# --- Tables --------------------------------------------------------------------------------


def _labels(status: M3Status) -> dict[str, Any]:
    return {
        "record_type": RECORD_TYPE_DECISION_SIMULATION,
        "parameter_provenance": "HYPOTHETICAL",
        "validated_recommendation": False,
        "point_forecast_validated": False,
        "m3_interval_status": status.interval_status,
    }


_EVALUATION_COLUMNS: Final = (
    "discretionary_cost",
    "overtime_cost",
    "surge_cost",
    "regular_cost",
    "operating_cost",
    "backlog_penalty",
    "terminal_penalty",
    "penalised_score",
    "backlog_request_days",
    "terminal_backlog",
    "peak_backlog",
    "days_with_backlog",
    "overtime_hours",
    "surge_requests",
    "idle_paid_capacity",
    "total_served",
    "budget_remaining",
)


def _evaluation_fields(evaluation: PlanEvaluation | None) -> dict[str, Any]:
    if evaluation is None:
        return dict.fromkeys(_EVALUATION_COLUMNS)
    return {name: getattr(evaluation, name) for name in _EVALUATION_COLUMNS}


def _plan_rows(
    result: ScenarioResult, scenario: Scenario
) -> list[tuple[str, str, Attempt | None, Plan | None, PlanEvaluation | None, PlanCheck | None]]:
    """(plan id, feasibility, attempt, plan, evaluation, check) for every reported plan."""
    rows: list[
        tuple[str, str, Attempt | None, Plan | None, PlanEvaluation | None, PlanCheck | None]
    ] = []
    reported = result.reported
    strict = reported.solved if result.strict.optimal else None
    if strict is not None:
        rows.append((OPTIMIZED, "strict", reported, strict.plan, strict.evaluation, strict.check))
    else:
        rows.append((OPTIMIZED, "infeasible", result.strict, None, None, None))
        relaxed = result.diagnosis.relaxed.solved if result.diagnosis else None
        if relaxed is not None and result.diagnosis is not None:
            rows.append(
                (
                    RELAXED_TERMINAL_TARGET,
                    RELAXED_TERMINAL_TARGET,
                    result.diagnosis.relaxed,
                    relaxed.plan,
                    relaxed.evaluation,
                    relaxed.check,
                )
            )
    free_attempt = result.without_target_plan or result.without_target
    free = free_attempt.solved
    if strict is not None and result.target_changes_plan is False:
        # Dropping the target changes neither the optimal score nor the tie-broken plan:
        # the target forces nothing, and the reported plan is that plan for both.
        rows.append(
            (
                WITHOUT_TARGET,
                "same_optimal_score_target_forces_nothing",
                reported,
                strict.plan,
                strict.evaluation,
                strict.check,
            )
        )
    elif free is not None:
        rows.append(
            (
                WITHOUT_TARGET,
                "terminal_target_dropped",
                free_attempt,
                free.plan,
                free.evaluation,
                free.check,
            )
        )
    for outcome in result.policies:
        violated = outcome.check.constraints_violated()
        feasibility = "strict" if not violated else "violates_" + "+".join(violated)
        rows.append(
            (outcome.policy, feasibility, None, outcome.plan, outcome.evaluation, outcome.check)
        )
        for kind, attempt in (
            ("same_cash", outcome.same_cash),
            ("same_backlog", outcome.same_backlog),
        ):
            solved = attempt.solved
            rows.append(
                (
                    f"lp_{kind}_as_{outcome.policy}",
                    "terminal_target_dropped" if solved is not None else str(attempt.status),
                    attempt,
                    None if solved is None else solved.plan,
                    None if solved is None else solved.evaluation,
                    None if solved is None else solved.check,
                )
            )
    return rows


def scenario_frame(
    results: Sequence[ScenarioResult],
    scenarios: Sequence[Scenario],
    status: M3Status,
    forecast_mean: float,
) -> pd.DataFrame:
    rows = []
    for result, scenario in zip(results, scenarios, strict=True):
        reported = result.reported.solved
        reference = None if reported is None else reported.evaluation
        for plan_id, feasibility, attempt, plan, evaluation, _check in _plan_rows(result, scenario):
            violation = None if plan is None else terminal_violation(result.problem, plan)
            relation = (
                None
                if evaluation is None
                or reference is None
                or plan_id in (OPTIMIZED, RELAXED_TERMINAL_TARGET)
                else pareto_relation(reference, evaluation)
            )
            budget = result.problem.discretionary_budget
            # The uniqueness test belongs to the reported plan (strict, or relaxed when the
            # strict model is infeasible), never to an infeasible programme.
            face = (
                result.reported_face
                if (plan_id == OPTIMIZED and result.strict.optimal)
                or plan_id == RELAXED_TERMINAL_TARGET
                else None
            )
            rows.append(
                {
                    **_labels(status),
                    "scenario_id": scenario.scenario_id,
                    "plan": plan_id,
                    "feasibility": feasibility,
                    "plan_selection": None
                    if plan is None
                    else SELECTION.get(plan_id.split("_as_")[0], "policy rule"),
                    "unique_optimal_plan": _verdict(face),
                    "unique_after_tie_breaks": None
                    if face is None
                    else _verdict(result.tie_broken_face),
                    "solver_status": None if attempt is None else str(attempt.status),
                    "demand_basis": scenario.demand_basis,
                    "quantile": scenario.quantile,
                    "demand_total": scenario.demand_total,
                    "regular_capacity_mean": scenario.regular_capacity_mean(),
                    "capacity_to_point_forecast_mean_ratio": scenario.regular_capacity_mean()
                    / forecast_mean,
                    "capacity_to_scenario_demand_mean_ratio": scenario.regular_capacity_mean()
                    / (scenario.demand_total / result.problem.horizon),
                    "demand_interpretation": _interpretation(scenario.demand_basis),
                    "discretionary_budget": budget,
                    "terminal_backlog_max": result.problem.units[0].terminal_backlog_max,
                    "terminal_violation": violation,
                    "satisfies_budget": None
                    if evaluation is None or budget is None
                    else evaluation.discretionary_cost <= budget + 1e-6 + 1e-9 * budget,
                    "reported_plan_relation": relation,
                    **_evaluation_fields(evaluation),
                }
            )
    return pd.DataFrame(rows)


def plans_frame(
    results: Sequence[ScenarioResult], scenarios: Sequence[Scenario], status: M3Status
) -> pd.DataFrame:
    rows = []
    for result, scenario in zip(results, scenarios, strict=True):
        problem = result.problem
        reported_id = OPTIMIZED if result.strict.optimal else RELAXED_TERMINAL_TARGET
        plans: list[tuple[str, Plan]] = []
        if result.reported.solved is not None:
            plans.append((reported_id, result.reported.solved.plan))
        plans += [(outcome.policy, outcome.plan) for outcome in result.policies]
        ranges = {}
        face = result.reported_face
        if face is not None:
            ranges = {(u, d): (low, high) for u, d, low, high in face.daily_extra_capacity}
        for plan_id, plan in plans:
            for unit, decisions in zip(problem.units, plan.units, strict=True):
                previous = unit.initial_backlog
                for day, when in enumerate(problem.dates):
                    extra = (
                        unit.overtime_productivity * decisions.overtime_hours[day]
                        + decisions.surge[day]
                    )
                    low, high = (
                        ranges.get((unit.unit_id, day), (extra, extra))
                        if plan_id == reported_id and face is not None
                        else (None, None)
                    )
                    rows.append(
                        {
                            **_labels(status),
                            "scenario_id": result.scenario_id,
                            "demand_basis": scenario.demand_basis,
                            "demand_interpretation": _interpretation(scenario.demand_basis),
                            "plan": plan_id,
                            "unit_id": unit.unit_id,
                            "day": day + 1,
                            "date": when.isoformat(),
                            "demand": unit.demand[day],
                            "work_available": previous + unit.demand[day],
                            "regular_capacity": unit.regular_capacity(day),
                            "overtime_hours": decisions.overtime_hours[day],
                            "overtime_capacity": unit.overtime_productivity
                            * decisions.overtime_hours[day],
                            "surge_requests": decisions.surge[day],
                            "served": decisions.served[day],
                            "backlog": decisions.backlog[day],
                            "extra_capacity": extra,
                            "extra_capacity_min_over_optimal_plans": low,
                            "extra_capacity_max_over_optimal_plans": high,
                        }
                    )
                    previous = decisions.backlog[day]
    return pd.DataFrame(rows)


def _fd_fields(item: FiniteDifference | None) -> dict[str, Any]:
    if item is None:
        return {
            "fd_step": None,
            "fd_backward": None,
            "fd_forward": None,
            "fd_status": "not_computed",
            "practical_step": None,
            "practical_backward": None,
            "practical_forward": None,
            "practical_forward_cash_part": None,
            "practical_forward_penalty_part": None,
            "bracket_ok": None,
        }
    return {
        "fd_step": item.step,
        "fd_backward": item.backward,
        "fd_forward": item.forward,
        "fd_status": item.status,
        "practical_step": item.practical_step,
        "practical_backward": item.practical_backward,
        "practical_forward": item.practical_forward,
        "practical_forward_cash_part": item.forward_cash,
        "practical_forward_penalty_part": item.forward_penalty,
        "bracket_ok": None
        if item.status == "solver_failed"
        else bool(item.bracket_ok and item.convex_ok and item.sign_ok),
    }


def duals_frame(results: Sequence[ScenarioResult], status: M3Status) -> pd.DataFrame:
    rows = []
    for result in results:
        differences = {item.dual.key(): item for item in result.finite_differences}
        dates = result.problem.dates
        for dual in result.duals:
            rows.append(
                {
                    **_labels(status),
                    "scenario_id": result.scenario_id,
                    "constraint": dual.constraint,
                    "unit_id": dual.unit_id,
                    "day": None if dual.day is None else dual.day + 1,
                    "date": None if dual.day is None else dates[dual.day].isoformat(),
                    "rhs": dual.rhs,
                    "rhs_units": dual.rhs_units,
                    "slack": dual.slack,
                    "binding": dual.binding,
                    # A daily limit of zero (surge before it becomes available) is tight
                    # because nothing can be bought, not because the resource is used up.
                    "limit_is_zero_unavailable": dual.constraint
                    in ("overtime_daily_limit", "surge_daily_limit")
                    and dual.rhs == 0,
                    "solver_dual_subgradient": dual.marginal,
                    "dual_units": dual.marginal_units,
                    **_fd_fields(differences.get(dual.key())),
                }
            )
    return pd.DataFrame(rows)


def _mean_demand(point: SweepPoint) -> float:
    unit = point.problem.units[0]
    return math.fsum(unit.demand) / unit.horizon


def _interpretation(basis: str) -> str:
    """How a row's demand must be read (copied onto every scenario and plan row)."""
    if basis == MARGINAL_QUANTILE_PATH:
        return "hypothetical_stress_not_a_probability"
    return "unvalidated_point_forecast"


def _range(face: Any, quantity: str) -> tuple[float | None, float | None]:
    if face is None:
        return None, None
    for item in face.ranges:
        if item.quantity == quantity:
            return item.low, item.high
    return None, None  # pragma: no cover - every face reports these quantities


def sensitivity_frame(
    sweeps: Sequence[tuple[tuple[SweepPoint, ...], tuple[MonotonicityCheck, ...]]],
    base: Scenario,
    status: M3Status,
    forecast_mean: float,
    demand_percentile: float,
) -> pd.DataFrame:
    rows = []
    for points, _ in sweeps:
        for point in points:
            solved = point.reported.solved
            unit = point.problem.units[0]
            capacity = (
                math.fsum(unit.regular_capacity(d) for d in range(unit.horizon)) / unit.horizon
            )
            ranges: dict[str, Any] = {}
            for quantity in ("discretionary_cash", "backlog_request_days", "terminal_backlog"):
                low, high = _range(point.face, quantity)
                ranges[f"{quantity}_min_over_optimal_plans"] = low
                ranges[f"{quantity}_max_over_optimal_plans"] = high
            rows.append(
                {
                    **_labels(status),
                    "base_scenario": base.scenario_id,
                    "parameter": point.parameter,
                    "value": point.value,
                    "solver_status": str(point.attempt.status),
                    "plan_selection": SELECTION[OPTIMIZED] if solved else None,
                    "unique_optimal_plan": _verdict(point.face),
                    "least_terminal_violation_within_budget": point.least_violation,
                    "regular_capacity_mean": capacity,
                    "capacity_to_point_forecast_mean_ratio": capacity / forecast_mean,
                    "capacity_to_scenario_demand_mean_ratio": capacity / _mean_demand(point),
                    "capacity_within_1pct_of_legacy_demand_percentile": capacity_near_percentile(
                        point.problem, demand_percentile
                    ),
                    "discretionary_budget": point.problem.discretionary_budget,
                    "optimal_score": point.attempt.value,
                    "binding_constraints": "; ".join(point.binding) if solved else None,
                    "budget_dual_usd_equivalent_per_usd": point.budget_dual,
                    "terminal_dual_usd_equivalent_per_request": point.terminal_dual,
                    **_evaluation_fields(None if solved is None else solved.evaluation),
                    **ranges,
                }
            )
    return pd.DataFrame(rows)


def frontier_frame(results: Sequence[ScenarioResult], status: M3Status) -> pd.DataFrame:
    rows = []
    for result in results:
        if result.frontier is None:
            continue
        budget = result.problem.discretionary_budget
        for point in result.frontier.points:
            rows.append(
                {
                    **_labels(status),
                    "label": "HYPOTHETICAL TRADEOFF, not a budget recommendation",
                    "scenario_id": result.scenario_id,
                    "terminal_target": point.terminal_target,
                    "cash_cap": point.cash_cap,
                    "marker": point.marker,
                    "solver_status": str(point.status),
                    "exceeds_configured_budget": None
                    if budget is None
                    else point.cash_cap > budget,
                    "notional_penalty": point.penalty,
                    "discretionary_cost": point.discretionary_cash,
                    "backlog_request_days": point.request_days,
                    "terminal_backlog": point.terminal_backlog,
                    "overtime_hours": point.overtime_hours,
                    "surge_requests": point.surge_requests,
                    "unique_after_tie_breaks": point.unique_after_tie_breaks,
                    "overtime_hours_min_over_tied_plans": _low(point.overtime_hours_range),
                    "overtime_hours_max_over_tied_plans": _high(point.overtime_hours_range),
                    "surge_requests_min_over_tied_plans": _low(point.surge_requests_range),
                    "surge_requests_max_over_tied_plans": _high(point.surge_requests_range),
                }
            )
    return pd.DataFrame(rows)


def _low(span: tuple[float, float] | None) -> float | None:
    return None if span is None else span[0]


def _high(span: tuple[float, float] | None) -> float | None:
    return None if span is None else span[1]


# --- Metrics -----------------------------------------------------------------------------


def _attempt_summary(attempt: Attempt | None) -> dict[str, Any] | None:
    if attempt is None:
        return None
    solved = attempt.solved
    if solved is None:
        return {"status": str(attempt.status), "message": attempt.message}
    return {
        "status": str(attempt.status),
        "objective": str(attempt.objective),
        "objective_value": solved.objective_value,
        "objective_recomputed_from_plan": solved.recomputed_value,
        "certificate": solved.certificate.to_dict(),
        "independent_check_passed": solved.check.feasible,
        "constraints_not_checked": list(solved.ignored),
        "max_balance_residual": solved.check.max_balance_residual,
        "values_snapped_to_bounds": solved.cleaning.values_adjusted,
        "largest_snap": solved.cleaning.max_adjustment,
        "highs_status": solved.result.highs_status,
        "retried_without_presolve": solved.result.retried,
        "stage_fallback": attempt.stage_fallback,
        "iterations": solved.result.iterations,
        "evaluation": solved.evaluation.to_dict(),
    }


def _programme_summary(attempt: Attempt) -> dict[str, Any] | None:
    summary = _attempt_summary(attempt)
    if summary is None or attempt.solved is None:
        return summary
    return {
        **summary,
        "evaluation_note": "one optimal vertex returned by the solver; the reported plan is "
        "reported_plan (among the optimal plans: least discretionary cash, then least "
        "terminal backlog, then least request-days)",
    }


def _comparisons(result: ScenarioResult) -> list[dict[str, Any]]:
    reported = result.reported.solved
    if reported is None:
        return []
    mine = reported.evaluation
    rows = []
    for outcome in result.policies:
        theirs = outcome.evaluation
        entry: dict[str, Any] = {
            "policy": outcome.policy,
            "reported_plan": result.feasibility,
            "policy_constraints_violated": outcome.check.constraints_violated(),
            "comparable_on_score": result.strict.optimal and outcome.check.feasible,
            "relation_of_reported_plan": pareto_relation(mine, theirs),
            "discretionary_cost_difference": mine.discretionary_cost - theirs.discretionary_cost,
            "request_days_difference": mine.backlog_request_days - theirs.backlog_request_days,
            "terminal_backlog_difference": mine.terminal_backlog - theirs.terminal_backlog,
        }
        least_days = outcome.least_request_days_at_policy_cash
        least_cash = outcome.least_cash_at_policy_backlog
        if least_days is not None:
            entry["lp_request_days_at_policy_cash"] = least_days
            entry["request_days_difference_at_same_cash"] = least_days - theirs.backlog_request_days
        if least_cash is not None:
            entry["lp_cash_at_policy_backlog"] = least_cash
            entry["cash_difference_at_same_backlog"] = least_cash - theirs.discretionary_cost
        rows.append(entry)
    return rows


def _sweep_summary(
    point: SweepPoint, forecast_mean: float, demand_percentile: float
) -> dict[str, Any]:
    solved = point.reported.solved
    evaluation = None if solved is None else solved.evaluation
    unit = point.problem.units[0]
    capacity = math.fsum(unit.regular_capacity(d) for d in range(unit.horizon)) / unit.horizon
    return {
        "parameter": point.parameter,
        "value": point.value,
        "status": str(point.attempt.status),
        "optimal_score": point.attempt.value,
        "discretionary_cost": None if evaluation is None else evaluation.discretionary_cost,
        "backlog_request_days": None if evaluation is None else evaluation.backlog_request_days,
        "terminal_backlog": None if evaluation is None else evaluation.terminal_backlog,
        "unique_optimal_plan": _verdict(point.face),
        "least_terminal_violation_within_budget": point.least_violation,
        "stage_fallbacks": list(point.fallbacks),
        "capacity_to_point_forecast_mean_ratio": capacity / forecast_mean,
        "capacity_to_scenario_demand_mean_ratio": capacity / _mean_demand(point),
        "capacity_within_1pct_of_legacy_demand_percentile": capacity_near_percentile(
            point.problem, demand_percentile
        ),
    }


def _verdict(face: Any) -> bool | None:
    """A face's uniqueness verdict, ``None`` when the test was not completed (a recorded
    problem, an ERROR): a failed test is not evidence of other optimal plans."""
    if face is None or face.problems:
        return None
    return bool(face.unique)


def _face_summary(face: Any, *, daily: bool = True) -> dict[str, Any] | None:
    """``daily`` False: the per-day ranges were not computed, so the count of undetermined
    days is unknown (``None``), not 0."""
    if face is None:
        return None
    return {
        "unique": _verdict(face),
        "method": "complementary_slackness_with_certified_duals",
        "dual_threshold": face.dual_threshold,
        "bounds_fixed": face.bounds_fixed,
        "rows_tight": face.rows_tight,
        "max_active_distance": face.max_active_distance,
        "undetermined_days": face.undetermined_days() if daily or face.unique else None,
        "ranges": [
            {"quantity": r.quantity, "units": r.units, "low": r.low, "high": r.high}
            for r in face.ranges
        ],
    }


def _fallback_issue(subject: str, fallbacks: Sequence[str]) -> Issue:
    counts = {name: fallbacks.count(name) for name in sorted(set(fallbacks))}
    return Issue(
        "stage_fallback",
        Severity.WARNING,
        subject,
        "A lexicographic stage or optimal-face test needed a fallback "
        f"({', '.join(f'{k} x{v}' for k, v in counts.items())}): its plan keeps the earlier "
        "optimum only to within the epsilon cap, or comes from a smaller face.",
    )


def _scenario_metrics(result: ScenarioResult, scenario: Scenario) -> dict[str, Any]:
    fd = result.finite_differences
    reported = result.reported.solved
    # result.duals carry the slack and binding state of the reported plan.
    binding_at_reported = {d.key(): d.binding for d in result.duals}
    return {
        **scenario.to_dict(),
        "feasibility": result.feasibility,
        "optimal_score": result.strict.value,
        "reported_plan_selection": SELECTION[
            OPTIMIZED if result.strict.optimal else RELAXED_TERMINAL_TARGET
        ],
        "reported_plan": _attempt_summary(result.reported),
        # The planning programme itself: its optimal value and certificate; its evaluation
        # is one optimal vertex, not the reported plan when the optimum is not unique.
        "strict_programme": _programme_summary(result.strict),
        "without_terminal_target": _attempt_summary(
            result.without_target_plan or result.without_target
        ),
        "terminal_target_changes_optimal_score": result.target_changes_score,
        "terminal_target_changes_reported_plan": result.target_changes_plan,
        "stage_fallbacks": list(result.fallbacks),
        "diagnosis": None if result.diagnosis is None else result.diagnosis.to_dict(),
        "relaxed": None if result.diagnosis is None else _attempt_summary(result.diagnosis.relaxed),
        "optimal_face": _face_summary(result.reported_face),
        "reported_plan_after_tie_breaks": _face_summary(result.tie_broken_face, daily=False),
        "binding_constraints": []
        if reported is None
        else list(format_binding(binding_constraints(result.problem, reported.plan))),
        "shadow_prices": {
            "computed": bool(fd),
            "n": len(fd),
            "brackets_ok": None
            if not fd
            else (
                None
                if all(i.status == "solver_failed" for i in fd)
                else all(
                    i.bracket_ok and i.convex_ok and i.sign_ok
                    for i in fd
                    if i.status != "solver_failed"
                )
            ),
            "locally_unique": sum(1 for i in fd if i.status == "locally_unique"),
            "breakpoint": sum(1 for i in fd if i.status == "breakpoint"),
            "solver_failed": sum(1 for i in fd if i.status == "solver_failed"),
            "domain_boundary_or_infeasible_side": sum(
                1 for i in fd if i.status in ("domain_boundary_below", "infeasible_side")
            ),
            "backward_unresolved": sum(1 for i in fd if i.status == "backward_unresolved"),
            "headline": [
                {
                    "constraint": i.dual.constraint,
                    "solver_dual_subgradient": i.dual.marginal,
                    "dual_units": i.dual.marginal_units,
                    "fd_backward": i.backward,
                    "fd_forward": i.forward,
                    "fd_status": i.status,
                    "practical_forward_cash_part": i.forward_cash,
                    "practical_forward_penalty_part": i.forward_penalty,
                    "binding": binding_at_reported.get(i.dual.key(), i.dual.binding),
                }
                for i in fd
                if i.dual.day is None
            ],
        },
        "comparisons": _comparisons(result),
        "frontier": None
        if result.frontier is None
        else {
            "on_frontier_check": result.frontier.on_frontier_check,
            "monotone_ok": result.frontier.monotone_ok,
            "convex_ok": result.frontier.convex_ok,
            "least_cash_for_least_penalty": dict(result.frontier.cash_for_least_penalty),
            "points_not_unique_after_tie_breaks": sum(
                1 for point in result.frontier.points if point.unique_after_tie_breaks is False
            ),
            "points_with_overtime_or_surge_totals_open": sum(
                1 for point in result.frontier.points if point.totals_open()
            ),
            "points_solver_failed": sum(
                1 for point in result.frontier.points if point.status is SolveStatus.FAILED
            ),
            "points_uniqueness_not_tested": sum(
                1
                for point in result.frontier.points
                if point.status is SolveStatus.OPTIMAL and point.unique_after_tie_breaks is None
            ),
            "least_penalty_programme_status": dict(result.frontier.least_penalty_status),
        },
        "problems": list(result.problems),
    }


@dataclass(frozen=True)
class M4Outputs:
    files: dict[str, bytes]
    outputs: dict[str, str]
    result_fingerprint: str
    payload: dict[str, Any]
    issues: tuple[Issue, ...]


def m4_outputs(settings: Settings, config: OptimizationConfig, inputs: DecisionInputs) -> M4Outputs:
    """Compute every result of a run and its deterministic files (no I/O besides inputs)."""
    solver = solver_settings(config)
    dates, point = inputs.projection.first(config.horizon_days)
    forecast_mean = math.fsum(point) / len(point)
    scenarios = build_scenarios(config, dates, point, inputs.quantiles, inputs.demand_percentile)
    shadow = set() if config.shadow_prices is None else set(config.shadow_prices.scenarios)
    steps = (
        None
        if config.shadow_prices is None
        else config.shadow_prices.finite_difference_steps.model_dump()
    )
    curves = set() if config.frontier is None else set(config.frontier.scenarios)
    results = [
        analyse_scenario(
            scenario.scenario_id,
            scenario.problem,
            solver,
            scenario.triggers,
            shadow_steps=steps if scenario.scenario_id in shadow else None,
            frontier_points=None
            if config.frontier is None or scenario.scenario_id not in curves
            else config.frontier.points,
        )
        for scenario in scenarios
    ]
    by_id = {s.scenario_id: s for s in scenarios}
    sweeps: list[tuple[tuple[SweepPoint, ...], tuple[MonotonicityCheck, ...]]] = []
    base: Scenario | None = None
    if config.sensitivity is not None:
        base = by_id[config.sensitivity.base_scenario]
        for sweep in config.sensitivity.sweeps:
            sweeps.append(run_sweep(base.problem, solver, sweep.parameter, sweep.values))
    status = inputs.status
    frames = {
        "scenarios.csv": scenario_frame(results, scenarios, status, forecast_mean),
        "plans.csv": plans_frame(results, scenarios, status),
        "duals.csv": duals_frame(results, status),
        "sensitivity.csv": sensitivity_frame(
            sweeps, base, status, forecast_mean, inputs.demand_percentile
        )
        if base is not None
        else pd.DataFrame(),
        "frontier.csv": frontier_frame(results, status),
    }
    issues: list[Issue] = [
        Issue("hypothetical_simulation", Severity.INFO, "optimization", LABEL),
    ]
    if not status.decision_grade:
        issues.append(
            Issue(
                "uncertainty_not_decision_grade",
                Severity.WARNING,
                "uncertainty",
                f"M3 calibration status {status.interval_status.upper()}: the forecast "
                "uncertainty is not decision-grade, so no result here can be decision-grade.",
            )
        )
    for result in results:
        for problem in result.problems:
            issues.append(Issue("check_failed", Severity.ERROR, result.scenario_id, problem))
        if not result.strict.optimal:
            reasons = "; ".join(result.diagnosis.reasons) if result.diagnosis else ""
            issues.append(
                Issue(
                    "strict_model_infeasible",
                    Severity.WARNING,
                    result.scenario_id,
                    "No plan meets every HYPOTHETICAL constraint; a relaxed plan (least "
                    f"terminal-target violation within the budget) is reported. Reasons: "
                    f"{reasons}",
                )
            )
        if result.fallbacks:
            issues.append(_fallback_issue(result.scenario_id, result.fallbacks))
    for swept_points, _ in sweeps:
        for swept in swept_points:
            if swept.fallbacks:
                issues.append(
                    _fallback_issue(
                        f"sensitivity:{swept.parameter}={swept.value:g}", swept.fallbacks
                    )
                )
            for problem in swept.problems:
                issues.append(
                    Issue(
                        "check_failed",
                        Severity.ERROR,
                        f"sensitivity:{swept.parameter}={swept.value:g}",
                        problem,
                    )
                )
    monotonicity = []
    for _, checks in sweeps:
        for check in checks:
            monotonicity.append(
                {
                    "parameter": check.parameter,
                    "quantity": check.quantity,
                    "direction": check.direction,
                    "holds": check.holds,
                    "worst_violation": check.worst_violation,
                    "compared_pairs": check.compared_pairs,
                }
            )
            if not check.holds:
                issues.append(
                    Issue(
                        "monotonicity_violated",
                        Severity.ERROR,
                        f"sensitivity:{check.parameter}",
                        f"{check.quantity} should be {check.direction} in {check.parameter}; "
                        f"worst violation {check.worst_violation:.6g}",
                    )
                )
    payload: dict[str, Any] = {
        "record_type": RECORD_TYPE_DECISION_SIMULATION,
        "label": LABEL,
        "experiment_id": config.experiment_id,
        "config_fingerprint": config.fingerprint(),
        "validated_recommendation": False,
        "point_forecast_validated": False,
        "parameter_provenance": "HYPOTHETICAL",
        "units": UNITS,
        "structural_assumptions": list(STRUCTURAL_ASSUMPTIONS),
        "hypothetical_parameters": config.hypothetical_parameters(),
        "inputs": inputs.provenance(),
        "horizon": {
            "days": config.horizon_days,
            "first_date": dates[0].isoformat(),
            "last_date": dates[-1].isoformat(),
            "forecast_mean_requests_per_day": forecast_mean,
            "forecast_total_requests": math.fsum(point),
        },
        "solver": {"settings": solver.to_dict(), **solver_versions()},
        "scenarios": [
            _scenario_metrics(result, scenario)
            for result, scenario in zip(results, scenarios, strict=True)
        ],
        "sensitivity": {
            "base_scenario": None if base is None else base.scenario_id,
            "monotonicity": monotonicity,
            "points": [
                _sweep_summary(swept, forecast_mean, inputs.demand_percentile)
                for swept_points, _ in sweeps
                for swept in swept_points
            ],
        },
        "checks": {
            "errors": sum(1 for i in issues if i.severity is Severity.ERROR),
            "stage_fallbacks": sum(len(r.fallbacks) for r in results)
            + sum(len(swept.fallbacks) for points, _ in sweeps for swept in points),
            "scenarios_strictly_feasible": sum(1 for r in results if r.strict.optimal),
            "scenarios_infeasible": sum(1 for r in results if not r.strict.optimal),
        },
    }
    files = {name: csv_bytes(frame) for name, frame in frames.items()}
    files["metrics.json"] = json_bytes(payload)
    outputs = {name: sha256_bytes(content) for name, content in files.items()}
    fingerprint = result_fingerprint(outputs, RESULT_FILES)
    # The report is rendered from metrics.json as written (signed zeros and non-finite
    # values normalised), so it is a pure function of that file.
    written = json.loads(files["metrics.json"])
    files["report.md"] = render_optimization_report(written).encode("utf-8")
    files["config.json"] = json_bytes(config.model_dump(mode="json"))
    outputs = {name: sha256_bytes(content) for name, content in files.items()}
    return M4Outputs(files, outputs, fingerprint, payload, tuple(issues))


# --- Commands ----------------------------------------------------------------------------


def _run_id(config: OptimizationConfig, data: SnapshotData) -> str:
    return f"{config.experiment_id}-{config.fingerprint()[:8]}-{data.fingerprint[:8]}"


def _obj(value: Any) -> Mapping[str, Any]:
    """``value`` if it is an object, else an empty one (a malformed file never crashes)."""
    return value if isinstance(value, Mapping) else {}


def headline(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Compact facts for the text renderer (one row per scenario)."""
    rows = []
    scenarios = payload.get("scenarios", [])
    for scenario in scenarios if isinstance(scenarios, list) else []:
        scenario = _obj(scenario)
        evaluation = _obj(_obj(scenario.get("reported_plan")).get("evaluation"))
        diagnosis = _obj(scenario.get("diagnosis"))
        rows.append(
            {
                "scenario_id": scenario.get("scenario_id"),
                "feasibility": scenario.get("feasibility"),
                "discretionary_cost": evaluation.get("discretionary_cost"),
                "backlog_request_days": evaluation.get("backlog_request_days"),
                "terminal_backlog": evaluation.get("terminal_backlog"),
                "terminal_violation": diagnosis.get(
                    "least_terminal_violation_within_budget_requests"
                ),
            }
        )
    status = _obj(_obj(payload.get("inputs")).get("uncertainty_status"))
    return {
        "label": payload.get("label"),
        "m3_interval_status": status.get("interval_status"),
        "m3_decision_grade": bool(status.get("decision_grade")),
        "scenarios": rows,
    }


def _manifest(
    settings: Settings,
    config: OptimizationConfig,
    inputs: DecisionInputs,
    outputs: M4Outputs,
    run_id: str,
    git: Mapping[str, Any],
    runtime: float,
) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "record_type": RECORD_TYPE_DECISION_SIMULATION,
        "label": LABEL,
        "run_id": run_id,
        "complete": not any(i.severity is Severity.ERROR for i in outputs.issues),
        "experiment_id": config.experiment_id,
        "config_fingerprint": config.fingerprint(),
        "config": config.model_dump(mode="json"),
        "data": inputs.data.provenance,
        "inputs": inputs.provenance(),
        "scenarios": {
            s["scenario_id"]: {
                "demand_sha256": s["demand_sha256"],
                "demand_basis": s["demand_basis"],
                "adjustments": s["adjustments"],
                "feasibility": s["feasibility"],
            }
            for s in outputs.payload["scenarios"]
        },
        "structural_assumptions": list(STRUCTURAL_ASSUMPTIONS),
        "validated_recommendation": False,
        "solver": outputs.payload["solver"],
        "code": {
            **code_fingerprint(settings.repo_root),
            "running_package_is_repository_src": running_package_is_repository(settings.repo_root),
        },
        "git": dict(git),
        "environment": environment_info(),
        "result_files": list(RESULT_FILES),
        "outputs": outputs.outputs,
        "result_fingerprint": outputs.result_fingerprint,
        "informational_not_fingerprinted": {
            "created_at_utc": utc_now(),
            "runtime_seconds": round(runtime, 1),
        },
    }


def _solver_failure(stage: str, exc: OptimizationError) -> CheckReport:
    """A solver failure is an ERROR of the run (exit 1), not a configuration problem."""
    return CheckReport(
        name=stage,
        issues=(
            Issue(
                "solver_failed",
                Severity.ERROR,
                "optimization",
                f"The solver failed, so no result was written: {exc}",
            ),
        ),
    )


def running_package_is_repository(repo_root: Path) -> bool:
    """Whether the imported ``atlas`` package is ``<repo_root>/src/atlas``.

    The code hash and the git commit describe the repository root; they identify the code
    that ran only if that is where the package was imported from (not another checkout on
    ``PYTHONPATH``, an editable install of a different tree, or ``--repo-root`` pointing
    elsewhere).
    """
    import atlas

    expected = repo_root / "src" / "atlas"
    # A symlinked src/atlas (or src) can point outside git, where edits are invisible to
    # git status: such a layout does not identify the code either.
    if expected.is_symlink() or expected.parent.is_symlink():
        return False
    if any(path.is_symlink() for path in expected.rglob("*")):
        return False
    running = Path(atlas.__file__).resolve().parent
    return running == expected.resolve() and running.is_relative_to(repo_root.resolve())


def _git(root: Path, *args: str) -> str | None:
    executable = shutil.which("git")
    if executable is None:
        return None
    try:
        # Absolute executable, fixed argument list, no shell; literal pathspecs (a file
        # name with glob characters must not match a different tracked file).
        completed = subprocess.run(  # noqa: S603
            [executable, "--literal-pathspecs", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _require_committed_config(repo_root: Path, config_path: Path) -> None:
    """A reference run's configuration must be the committed file under ``configs/``.

    ``git status`` only covers the repository, misses ignored files and trusts
    ``skip-worktree``/``assume-unchanged`` flags, so the file must be tracked (matched
    literally, not as a glob) and its content must equal the blob committed at HEAD.
    """
    root = repo_root.resolve()
    path = config_path.resolve()
    if not path.is_relative_to(root / "configs"):
        raise ConfigError(
            f"refusing a reference run: the configuration {config_path} is not under "
            f"{root / 'configs'}"
        )
    relative = path.relative_to(root).as_posix()
    if _git(root, "ls-files", "--error-unmatch", "--", relative) is None:
        raise ConfigError(
            f"refusing a reference run: the configuration {config_path} is not tracked by git"
        )
    # "HEAD:./path" is relative to the repository root given here, also when that root is
    # a subdirectory of a larger git repository.
    committed = _git(root, "rev-parse", f"HEAD:./{relative}")
    if committed is None:
        raise ConfigError(
            f"refusing a reference run: the configuration {config_path} is not in the "
            "commit at HEAD"
        )
    # The raw bytes must equal the committed blob, or the file must hash to it after git's
    # own filters (core.autocrlf). Either way skip-worktree and ignored copies cannot pass.
    if (
        _git_blob(root, f"HEAD:./{relative}") != path.read_bytes()
        and _git(root, "hash-object", "--", relative) != committed
    ):
        raise ConfigError(
            f"refusing a reference run: the configuration {config_path} differs from the "
            "committed file"
        )


def _git_blob(root: Path, spec: str) -> bytes | None:
    executable = shutil.which("git")
    if executable is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603
            [executable, "-C", str(root), "cat-file", "blob", spec],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def run_optimization_command(
    settings: Settings,
    config_path: Path,
    *,
    output_dir: Path | None = None,
    require_clean: bool = False,
) -> list[CheckReport]:
    """``atlas optimize run``: every scenario and analysis, persisted as a run directory."""
    config = load_optimization_config(config_path)
    if output_dir is not None:
        _check_output_dir(output_dir)
    git = git_info(settings.repo_root)
    if require_clean and (not git.get("available") or git.get("dirty")):
        raise ConfigError(
            "refusing a reference run: the code, configuration or tests have uncommitted "
            "changes (or git is unavailable)"
        )
    if require_clean:
        _require_committed_config(settings.repo_root, config_path)
        if not running_package_is_repository(settings.repo_root):
            raise ConfigError(
                "refusing a reference run: the atlas package that runs is not "
                f"{settings.repo_root / 'src' / 'atlas'}, so the recorded commit and code hash "
                "would not identify the code that ran (check --repo-root, ATLAS_REPO_ROOT and "
                "PYTHONPATH)"
            )
    started = time.perf_counter()
    reports, data = load_series(settings, "optimization")
    if data is None:
        return reports
    inputs = load_inputs(settings, config, data)
    run_id = _run_id(config, data)
    directory = output_dir or settings.repo_root / RUNS_DIRNAME / run_id
    _check_output_dir(directory)
    try:
        outputs = m4_outputs(settings, config, inputs)
    except OptimizationError as exc:
        return [*reports, _solver_failure("optimization", exc)]
    manifest = _manifest(
        settings, config, inputs, outputs, run_id, git, time.perf_counter() - started
    )
    write_run(directory, outputs.files, manifest)
    package_issues = (
        ()
        if manifest["code"]["running_package_is_repository_src"]
        else (
            Issue(
                "running_package_not_repository",
                Severity.WARNING,
                "code",
                "The atlas package that ran is not "
                f"{settings.repo_root / 'src' / 'atlas'}: the recorded commit and code hash do "
                "not identify the code that ran (check --repo-root, ATLAS_REPO_ROOT and "
                "PYTHONPATH).",
            ),
        )
    )
    reports.append(
        CheckReport(
            name="optimization",
            issues=(*outputs.issues, *package_issues),
            facts={
                "run_dir": str(directory),
                "run_id": run_id,
                "complete": manifest["complete"],
                "result_fingerprint": outputs.result_fingerprint,
                **headline(outputs.payload),
            },
        )
    )
    return reports


def _check_manifest_shape(manifest: Any, run_dir: Path) -> None:
    """A malformed manifest is a usage error (exit 2), never an internal error."""
    if not isinstance(manifest.get("config"), dict):
        raise ConfigError(f"{run_dir}/manifest.json has no recorded configuration")
    for key in ("code", "git", "solver"):
        if key in manifest and manifest[key] is not None and not isinstance(manifest[key], dict):
            raise ConfigError(f"{run_dir}/manifest.json: '{key}' is not an object")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict) or any(
        not isinstance(outputs.get(name), str) for name in RESULT_FILES
    ):
        raise ConfigError(
            f"{run_dir}/manifest.json does not record a SHA-256 for every result file "
            f"({', '.join(RESULT_FILES)})"
        )
    for name, digest in outputs.items():
        if not isinstance(digest, str):
            raise ConfigError(
                f"{run_dir}/manifest.json: recorded output {name!r} has no SHA-256 string"
            )
        try:
            encoded = name.encode("utf-8") if isinstance(name, str) else None
        except UnicodeEncodeError:
            encoded = None
        if (
            encoded is None
            or name in ("", ".", "..")
            or "/" in name
            or "\\" in name
            or "\x00" in name
            or len(encoded) > 255
        ):
            raise ConfigError(
                f"{run_dir}/manifest.json: recorded output {name!r} is not a plain file name"
            )


#: Manifest fields that copy fingerprinted content (metrics.json): they must agree with it.
_MANIFEST_COPIES: Final = (
    "record_type",
    "label",
    "validated_recommendation",
    "config_fingerprint",
    "experiment_id",
    "inputs",
    "structural_assumptions",
)


def _manifest_copy_issues(
    manifest: Mapping[str, Any], metrics: Mapping[str, Any], against: str
) -> list[Issue]:
    """ERRORs for manifest labels and provenance that disagree with ``metrics.json``.

    The manifest is not fingerprinted, so a hand-edited ``validated_recommendation`` or M3
    status would otherwise pass ``verify`` and ``inspect``. ``against`` names the
    ``metrics.json`` compared with (the recorded one, or the recomputation).
    """
    inputs = _obj(metrics.get("inputs"))
    data = _obj(manifest.get("data"))
    fingerprint = manifest.get("config_fingerprint")
    copies: dict[str, tuple[Any, Any]] = {
        key: (manifest.get(key), metrics.get(key)) for key in _MANIFEST_COPIES
    }
    copies["data"] = (manifest.get("data"), inputs.get("snapshot"))
    copies["solver"] = (manifest.get("solver"), metrics.get("solver"))
    issues = [
        Issue(
            "manifest_field_differs",
            Severity.ERROR,
            "manifest.json",
            f"The manifest's {key!r} differs from {against}.",
        )
        for key, (recorded_value, expected) in copies.items()
        if recorded_value != expected
    ]
    if manifest.get("result_files") != list(RESULT_FILES):
        issues.append(
            Issue(
                "manifest_field_differs",
                Severity.ERROR,
                "manifest.json",
                "The manifest's 'result_files' differs from the result files an M4 run writes.",
            )
        )
    run_id = (
        f"{manifest.get('experiment_id')}-{str(fingerprint)[:8]}-"
        f"{str(data.get('data_fingerprint'))[:8]}"
    )
    if manifest.get("run_id") != run_id:
        issues.append(
            Issue(
                "manifest_field_differs",
                Severity.ERROR,
                "manifest.json",
                "The manifest's 'run_id' differs from the one its experiment id and its "
                "configuration and data fingerprints give.",
            )
        )
    recorded = _obj(manifest.get("scenarios"))
    scenarios = metrics.get("scenarios")
    computed = {
        str(s.get("scenario_id")): {
            "demand_sha256": s.get("demand_sha256"),
            "demand_basis": s.get("demand_basis"),
            "adjustments": s.get("adjustments"),
            "feasibility": s.get("feasibility"),
        }
        for s in (scenarios if isinstance(scenarios, list) else [])
        if isinstance(s, Mapping)
    }
    if dict(recorded) != computed:
        issues.append(
            Issue(
                "manifest_field_differs",
                Severity.ERROR,
                "manifest.json",
                "The manifest's 'scenarios' (demand, adjustments, feasibility) differ from "
                f"{against}.",
            )
        )
    return issues


def _missing_issues(absent: Sequence[str]) -> list[Issue]:
    """An M4 run always writes every result file, so a recorded file that is gone is an
    ERROR (unlike M2/M3, which may omit optional outputs)."""
    return [
        Issue("file_missing", Severity.ERROR, name, f"{name} is recorded but missing.")
        for name in absent
    ]


def _no_lone_surrogates(value: Any, path: Path, what: str) -> None:
    """JSON escapes can encode lone surrogates, which no text output can print: such a
    file is malformed (a usage error), not a crash of the renderer."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ConfigError(f"{path}: the {what} contains text that is not valid") from exc
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def _read_run_json(path: Path, what: str) -> Any:
    """:func:`_read_json`, with undecodable bytes a usage error too."""
    try:
        payload = _read_json(path, what)
        _no_lone_surrogates(payload, path, what)
        return payload
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path}: the {what} is not valid UTF-8 ({exc.reason})") from exc
    except RecursionError as exc:
        raise ConfigError(f"{path}: the {what} is nested too deeply to read") from exc
    except ValueError as exc:  # e.g. an integer literal beyond Python's digit limit
        raise ConfigError(f"{path}: the {what} cannot be read ({exc})") from exc


def verify_optimization_command(settings: Settings, run_dir: Path) -> list[CheckReport]:
    """``atlas optimize verify``: re-run a recorded run and compare every result file."""
    manifest = _read_run_json(run_dir / "manifest.json", "run manifest")
    if manifest.get("record_type") != RECORD_TYPE_DECISION_SIMULATION:
        raise ConfigError(f"{run_dir} is not a {RECORD_TYPE_DECISION_SIMULATION} run")
    _check_manifest_shape(manifest, run_dir)
    config = parse_optimization_config(manifest["config"])
    if config.fingerprint() != manifest.get("config_fingerprint"):
        raise ConfigError("the recorded configuration does not match its recorded fingerprint")
    reports, data = load_series(settings, "optimization_verification")
    if data is None:
        return reports
    inputs = load_inputs(settings, config, data)
    try:
        outputs = m4_outputs(settings, config, inputs)
    except OptimizationError as exc:
        return [*reports, _solver_failure("optimization_verification", exc)]
    recorded = manifest.get("outputs", {})
    issues: list[Issue] = []
    files: dict[str, str] = {}
    for name, digest in outputs.outputs.items():
        same = recorded.get(name) == digest
        files[name] = "identical" if same else "different"
        if not same:
            issues.append(
                Issue(
                    "output_differs",
                    Severity.ERROR,
                    name,
                    f"SHA-256 {digest} differs from the recorded {recorded.get(name)}.",
                )
            )
    if manifest.get("result_fingerprint") != outputs.result_fingerprint:
        issues.append(
            Issue(
                "result_fingerprint_differs",
                Severity.ERROR,
                "manifest.json",
                f"Result fingerprint {outputs.result_fingerprint} differs from the recorded "
                f"{manifest.get('result_fingerprint')}.",
            )
        )
    # A file whose bytes on disk differ from the recorded hash was altered on disk, unless
    # the disk copy equals the recomputation: then the manifest entry was edited.
    altered: list[str] = []
    edited: list[str] = []
    for name, expected in sorted(recorded.items()):
        if not (run_dir / name).is_file():
            continue
        on_disk = sha256_file(run_dir / name)
        if on_disk != expected:
            (edited if on_disk == outputs.outputs.get(name) else altered).append(name)
    issues += [
        Issue("file_altered", Severity.ERROR, name, f"{name} no longer matches manifest.json.")
        for name in altered
    ]
    issues += [
        Issue(
            "manifest_entry_differs",
            Severity.ERROR,
            name,
            f"{name} on disk equals the recomputation, but manifest.json records another hash.",
        )
        for name in edited
    ]
    absent = sorted(name for name in recorded if not (run_dir / name).is_file())
    issues += _missing_issues(absent)
    issues += _manifest_copy_issues(
        manifest, json.loads(outputs.files["metrics.json"]), "the recomputed metrics.json"
    )
    if not running_package_is_repository(settings.repo_root):
        issues.append(
            Issue(
                "running_package_not_repository",
                Severity.ERROR,
                "code",
                "The atlas package that runs is not "
                f"{settings.repo_root / 'src' / 'atlas'}: the recomputation does not test the "
                "code of this repository (check --repo-root, ATLAS_REPO_ROOT and PYTHONPATH).",
            )
        )
    if _obj(manifest.get("code")).get("running_package_is_repository_src") is False:
        issues.append(
            Issue(
                "recorded_package_not_repository",
                Severity.WARNING,
                "manifest.json",
                "The run was made with an atlas package outside its repository root: its "
                "recorded commit and code hash do not identify the code that ran.",
            )
        )
    # Reproducing a failed run byte for byte does not make it pass: its failed checks are
    # failures of the recomputation too.
    recomputed_errors = [i for i in outputs.issues if i.severity is Severity.ERROR]
    # A fallback of the recomputation is reported too (a WARNING), as by run and inspect.
    issues += [i for i in outputs.issues if i.code == "stage_fallback"]
    issues += [
        Issue("recomputed_check_failed", Severity.ERROR, i.subject, i.message)
        for i in recomputed_errors
    ]
    if manifest.get("complete") is not (not recomputed_errors):
        issues.append(
            Issue(
                "complete_flag_inconsistent",
                Severity.ERROR,
                "manifest.json",
                f"The manifest records complete={manifest.get('complete')!r}, but the "
                f"recomputation has {len(recomputed_errors)} failed check(s).",
            )
        )
    recorded_solver = manifest.get("solver") or {}
    now_solver = solver_versions()
    now_code = code_fingerprint(settings.repo_root)
    now_git = git_info(settings.repo_root)
    differences = {
        "code_sha256": [(manifest.get("code") or {}).get("code_sha256"), now_code["code_sha256"]],
        "git_commit": [(manifest.get("git") or {}).get("commit"), now_git.get("commit")],
        "scipy": [recorded_solver.get("scipy"), now_solver["scipy"]],
        "highs": [recorded_solver.get("highs"), now_solver["highs"]],
    }
    reports.append(
        CheckReport(
            name="optimization_verification",
            issues=tuple(issues),
            facts={
                "run_dir": str(run_dir),
                "recorded_result_fingerprint": manifest.get("result_fingerprint"),
                "result_fingerprint": outputs.result_fingerprint,
                "identical": not altered
                and not edited
                and not absent
                and all(files[name] == "identical" for name in files)
                and manifest.get("result_fingerprint") == outputs.result_fingerprint,
                "fingerprint_identical": manifest.get("result_fingerprint")
                == outputs.result_fingerprint,
                "recomputed_errors": len(recomputed_errors),
                "files": files,
                "altered_on_disk": altered,
                "manifest_entries_edited": edited,
                "files_absent": absent,
                "provenance_differences": {k: v for k, v in differences.items() if v[0] != v[1]},
                "label": LABEL,
                "m3_interval_status": inputs.status.interval_status,
                "m3_decision_grade": inputs.status.decision_grade,
            },
        )
    )
    return reports


def inspect_optimization_command(run_dir: Path) -> list[CheckReport]:
    """``atlas optimize inspect``: integrity of a run directory and its headline results."""
    manifest = _read_run_json(run_dir / "manifest.json", "run manifest")
    if manifest.get("record_type") != RECORD_TYPE_DECISION_SIMULATION:
        raise ConfigError(f"{run_dir} is not a {RECORD_TYPE_DECISION_SIMULATION} run")
    _check_manifest_shape(manifest, run_dir)
    recorded = manifest["outputs"]
    altered = [
        name
        for name, expected in sorted(recorded.items())
        if (run_dir / name).is_file() and sha256_file(run_dir / name) != expected
    ]
    absent = sorted(name for name in recorded if not (run_dir / name).is_file())
    issues = [
        Issue("file_altered", Severity.ERROR, name, f"{name} no longer matches manifest.json.")
        for name in altered
    ]
    issues += _missing_issues([name for name in absent if name != "metrics.json"])
    if manifest.get("complete") is not True:
        issues.append(
            Issue(
                "run_incomplete",
                Severity.ERROR,
                "manifest.json",
                "The manifest marks the run incomplete.",
            )
        )
    if result_fingerprint(recorded, RESULT_FILES) != manifest.get("result_fingerprint"):
        issues.append(
            Issue(
                "result_fingerprint_differs",
                Severity.ERROR,
                "manifest.json",
                "The recorded outputs do not give the recorded result fingerprint.",
            )
        )
    if "metrics.json" in absent:
        issues.append(
            Issue("metrics_missing", Severity.ERROR, "metrics.json", "metrics.json is absent.")
        )
        return [CheckReport(name="optimization_inspection", issues=tuple(issues))]
    payload = _read_run_json(run_dir / "metrics.json", "metrics")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not all(isinstance(x, dict) for x in scenarios):
        raise ConfigError(f"{run_dir}/metrics.json: 'scenarios' is not a list of objects")
    if not isinstance(payload.get("inputs", {}), dict):
        raise ConfigError(f"{run_dir}/metrics.json: 'inputs' is not an object")
    if "metrics.json" not in altered:
        issues += _manifest_copy_issues(manifest, payload, "the recorded metrics.json")
        # The embedded configuration must still give its recorded fingerprint.
        try:
            embedded = parse_optimization_config(manifest["config"]).fingerprint()
        except (ConfigError, ValueError) as exc:
            embedded = f"invalid: {exc}"
        if embedded != manifest.get("config_fingerprint"):
            issues.append(
                Issue(
                    "manifest_field_differs",
                    Severity.ERROR,
                    "manifest.json",
                    "The manifest's embedded configuration does not give its recorded "
                    "configuration fingerprint.",
                )
            )
    if _obj(manifest.get("code")).get("running_package_is_repository_src") is False:
        issues.append(
            Issue(
                "recorded_package_not_repository",
                Severity.WARNING,
                "manifest.json",
                "The run was made with an atlas package outside its repository root: its "
                "recorded commit and code hash do not identify the code that ran.",
            )
        )
    # metrics.json is covered by the result fingerprint; the manifest flag is not.
    checks = payload.get("checks") if isinstance(payload, dict) else None
    errors = checks.get("errors") if isinstance(checks, dict) else None
    if errors != 0:
        issues.append(
            Issue(
                "checks_failed",
                Severity.ERROR,
                "metrics.json",
                f"metrics.json records {errors!r} failed check(s).",
            )
        )
    fallbacks = checks.get("stage_fallbacks") if isinstance(checks, dict) else None
    if isinstance(fallbacks, int) and fallbacks > 0:
        issues.append(
            Issue(
                "stage_fallback",
                Severity.WARNING,
                "metrics.json",
                f"metrics.json records {fallbacks} stage fallback(s): those plans keep their "
                "earlier optimum only to within the epsilon cap, or come from a smaller face "
                "(per scenario and sweep point in 'stage_fallbacks').",
            )
        )
    if (errors == 0) is not (manifest.get("complete") is True):
        issues.append(
            Issue(
                "complete_flag_inconsistent",
                Severity.ERROR,
                "manifest.json",
                f"The manifest records complete={manifest.get('complete')!r}, but metrics.json "
                f"records {errors!r} failed check(s).",
            )
        )
    return [
        CheckReport(
            name="optimization_inspection",
            issues=tuple(issues),
            facts={
                "run_dir": str(run_dir),
                "run_id": manifest.get("run_id"),
                "complete": manifest.get("complete"),
                "result_fingerprint": manifest.get("result_fingerprint"),
                "files_checked": sorted(set(recorded) - set(absent)),
                "files_absent": absent,
                "altered_on_disk": altered,
                **headline(payload),
            },
        )
    ]


__all__ = [
    "LABEL",
    "RECORD_TYPE_DECISION_SIMULATION",
    "RESULT_FILES",
    "STRUCTURAL_ASSUMPTIONS",
    "DecisionInputs",
    "M4Outputs",
    "inspect_optimization_command",
    "load_inputs",
    "m4_outputs",
    "run_optimization_command",
    "verify_optimization_command",
]
