"""Application layer: response data built from the reference store (no HTTP here).

Each function returns the ``data`` of one endpoint together with its ``integrity`` and
``provenance`` blocks. The labels are the ones ATLAS already writes with these numbers
(M2's record-type labels, M3's status statement and guidance, M4's simulation label), so
the API never words a caveat more weakly than the files it serves.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any, Final

from atlas import __version__
from atlas.api.errors import ApiError, not_found
from atlas.api.reference import FORECAST, M4_REFERENCE, SNAPSHOT, ReferenceStore
from atlas.api.schemas import (
    API_VERSION,
    ComparisonData,
    ComparisonMetric,
    ComparisonRow,
    EndpointInfo,
    ForecastAccuracyContext,
    ForecastData,
    ForecastPoint,
    HistoryData,
    Integrity,
    IntervalsData,
    Limits,
    MetricRow,
    MetricsData,
    ModelInfo,
    ModelsData,
    PeriodInfo,
    PrimaryTest,
    ScenarioDetailData,
    ScenarioListData,
    ScenarioOutcome,
    ScenarioSummary,
    ScientificStatus,
    SelectionInfo,
    SnapshotInfo,
    Source,
    SystemData,
    UncertaintyStatusData,
)
from atlas.api.settings import ApiSettings
from atlas.decision_inputs import MARGINAL_QUANTILE_NOTICE
from atlas.events import holidays_between
from atlas.experiment import RECORD_TYPE_LABELS
from atlas.optimization_runner import LABEL as M4_LABEL
from atlas.optimizer import solver_versions
from atlas.planning import MAX_HORIZON_DAYS
from atlas.uncertainty_runner import PROJECTION_LABEL, STATUS_GUIDANCE

#: The longest history window one request may return (the snapshot has 1,277 days).
MAX_HISTORY_DAYS: Final = 1_500
#: History returned when no window is given: the last 180 observed days.
DEFAULT_HISTORY_DAYS: Final = 180


def point_forecast_notice(store: ReferenceStore) -> str:
    """The demand caveat, with the recommended model's measured historical error."""
    text = "The M2 point forecast is not prospectively validated"
    if store.inputs is None:
        return text + "."
    accuracy = store.inputs.projection.accuracy
    return (
        f"{text}: on M2's {accuracy.period} period its MAE over horizons 1-30 was "
        f"{accuracy.mae:,.0f} requests/day and its bias {accuracy.bias:+,.0f} requests/day "
        "(prediction - actual)."
    )


OBSERVED_NOTICE: Final = (
    "OBSERVED: city-wide 311 service requests created per day (intake, not workload) from "
    "the frozen, hash-verified legacy-v0 snapshot; not a measure of service capacity."
)
RELAXED_NOTICE: Final = (
    "A scenario with feasibility relaxed_terminal_target has no plan that meets every "
    "HYPOTHETICAL constraint: its reported plan and required spends are DIAGNOSTIC values, "
    "never budget requests."
)

ENDPOINTS: Final = (
    EndpointInfo(
        method="GET",
        path="/health/live",
        cost="static",
        interactive=True,
        summary="Process liveness.",
    ),
    EndpointInfo(
        method="GET",
        path="/health/ready",
        cost="static",
        interactive=True,
        summary="Readiness of every reference component.",
    ),
    EndpointInfo(
        method="GET",
        path="/api/v1/system",
        cost="precomputed",
        interactive=True,
        summary="Versions, provenance, scientific status and limits.",
    ),
    EndpointInfo(
        method="GET",
        path="/api/v1/models",
        cost="precomputed",
        interactive=True,
        summary="M2 models, selection and recommendation.",
    ),
    EndpointInfo(
        method="GET",
        path="/api/v1/forecasts",
        cost="precomputed",
        interactive=True,
        summary="The 30-day M2 projection (projected, not validated).",
    ),
    EndpointInfo(
        method="GET",
        path="/api/v1/forecasts/history",
        cost="precomputed",
        interactive=True,
        summary="Observed daily demand (legacy-v0).",
    ),
    EndpointInfo(
        method="GET",
        path="/api/v1/forecasts/metrics",
        cost="precomputed",
        interactive=True,
        summary="M2 backtest accuracy by model, period and horizon.",
    ),
    EndpointInfo(
        method="GET",
        path="/api/v1/uncertainty/status",
        cost="precomputed",
        interactive=True,
        summary="M3 calibration status (failed_calibration).",
    ),
    EndpointInfo(
        method="GET",
        path="/api/v1/uncertainty/intervals",
        cost="precomputed",
        interactive=True,
        summary="M3 projection quantiles (not calibrated).",
    ),
    EndpointInfo(
        method="GET",
        path="/api/v1/scenarios",
        cost="precomputed",
        interactive=True,
        summary="M4 reference scenarios (HYPOTHETICAL).",
    ),
    EndpointInfo(
        method="GET",
        path="/api/v1/scenarios/comparison",
        cost="precomputed",
        interactive=True,
        summary="Side-by-side M4 scenarios with differences.",
    ),
    EndpointInfo(
        method="GET",
        path="/api/v1/scenarios/{scenario_id}",
        cost="precomputed",
        interactive=True,
        summary="One M4 scenario with its daily plans.",
    ),
    EndpointInfo(
        method="GET",
        path="/api/v1/simulations/parameters",
        cost="precomputed",
        interactive=True,
        summary="Defaults and ranges of simulation inputs.",
    ),
    EndpointInfo(
        method="POST",
        path="/api/v1/simulations",
        cost="bounded_computation",
        interactive=True,
        summary="One bounded HYPOTHETICAL simulation (M4 engine).",
    ),
)

COMPARISON_UNITS: Final = {
    "discretionary_cost": "usd",
    "backlog_request_days": "request_days",
    "terminal_backlog": "requests",
    "peak_backlog": "requests",
    "overtime_hours": "staff_hours",
    "surge_requests": "requests",
    "penalised_score": "usd_equivalent (ranking score, not a cost)",
}


def holiday_names(start: date, end: date) -> dict[date, str]:
    """Observed federal holidays between ``start`` and ``end`` (M1 rules), by date."""
    return {h.observed: h.name for h in holidays_between(start, end) if start <= h.observed <= end}


# --- Envelope pieces --------------------------------------------------------------------


def _status(store: ReferenceStore) -> tuple[str, bool, str | None]:
    state = store.status.get(FORECAST)
    if store.inputs is None or state is None or not state.ready:
        return "unavailable", False, None
    status = store.inputs.status
    return status.interval_status, status.decision_grade, status.statement


def integrity(
    store: ReferenceStore,
    *,
    record_type: str,
    data_status: Any,
    notices: Sequence[str],
    hypothetical: bool = False,
) -> Integrity:
    interval_status, decision_grade, _ = _status(store)
    guidance = STATUS_GUIDANCE.get(interval_status)
    extra = [] if guidance is None or data_status in ("observed", "metadata") else [guidance]
    return Integrity(
        record_type=record_type,
        data_status=data_status,
        uncertainty_interval_status=interval_status,
        uncertainty_decision_grade=decision_grade,
        parameter_provenance="HYPOTHETICAL" if hypothetical else None,
        notices=[*notices, *extra],
    )


def envelope(
    store: ReferenceStore, data: Any, integrity_block: Integrity, sources: Sequence[Source]
) -> dict[str, Any]:
    return {
        "data": data,
        "integrity": integrity_block,
        "provenance": store.provenance(sources),
    }


# --- System -----------------------------------------------------------------------------


def system(store: ReferenceStore, settings: ApiSettings) -> dict[str, Any]:
    """Service metadata; ``settings`` are the running app's (its limits and key policy)."""
    snapshot = None
    if store.snapshot is not None and store.status[SNAPSHOT].ready:
        p = store.snapshot.provenance
        snapshot = SnapshotInfo(
            snapshot_id=str(p["snapshot_id"]),
            data_fingerprint=str(p["data_fingerprint"]),
            first_date=p["first_date"],
            last_date=p["last_date"],
            n_days=int(p["n_days"]),
        )
    scientific = None
    sources: list[Source] = []
    if store.inputs is not None and store.status[FORECAST].ready:
        inputs = store.inputs
        scientific = ScientificStatus(
            forecast_model=inputs.projection.model_id,
            forecast_label=inputs.projection.label,
            forecast_confirmation_mae_requests_per_day=inputs.projection.accuracy.mae,
            uncertainty_interval_status=inputs.status.interval_status,
            uncertainty_decision_grade=inputs.status.decision_grade,
            uncertainty_statement=inputs.status.statement,
            decision_simulation_label=M4_LABEL,
            limitations=[
                point_forecast_notice(store),
                STATUS_GUIDANCE.get(inputs.status.interval_status, inputs.status.statement),
                "Every M4 operational parameter is HYPOTHETICAL; no result is a validated "
                "staffing, budget or service-capacity recommendation.",
                "Demand is the count of requests created (intake), not measured workload.",
            ],
        )
        sources = [store.snapshot_source(), store.forecast_source(), store.status_source()]
        if store.status[M4_REFERENCE].ready:
            sources.append(store.m4_source())
        sources.append(store.config_source())
    data = SystemData(
        service="atlas-api",
        atlas_version=__version__,
        api_version=API_VERSION,
        milestone="M5",
        snapshot=snapshot,
        components=store.components(),
        scientific_status=scientific,
        limits=Limits(
            max_body_bytes=settings.max_body_bytes,
            max_concurrent_simulations=settings.max_concurrent_simulations,
            simulation_timeout_seconds=settings.simulation_timeout_seconds,
            simulation_requires_api_key=settings.simulation_requires_key,
            max_history_days=MAX_HISTORY_DAYS,
            max_horizon_days=MAX_HORIZON_DAYS,
        ),
        solver=solver_versions(),
        endpoints=list(ENDPOINTS),
    )
    block = integrity(
        store,
        record_type="api_system",
        data_status="metadata",
        notices=["Service metadata; see each endpoint for the status of its numbers."],
    )
    return envelope(store, data, block, sources)


# --- M2 ---------------------------------------------------------------------------------


def _m2_integrity(store: ReferenceStore) -> Integrity:
    m2 = store.require_m2()
    notes = [RECORD_TYPE_LABELS["historical_backtest"]]
    legacy = m2.payload.get("legacy_headline_note")
    if isinstance(legacy, str):
        notes.append(legacy)
    notes.append(
        "The confirmation period was used to confirm the recommended model; scores on it are "
        "historical, not prospective."
    )
    return integrity(
        store, record_type="historical_backtest", data_status="historical_evaluation", notices=notes
    )


def _m2_sources(store: ReferenceStore) -> list[Source]:
    m2 = store.require_m2()
    return [
        Source(
            kind="m2_backtest_run",
            path=m2.run_dir,
            record_type="historical_backtest",
            run_id=m2.run_id,
            result_fingerprint=m2.result_fingerprint,
            config_fingerprint=m2.config_fingerprint,
            sha256=m2.metrics_sha256,
        )
    ]


def _headline_mae(payload: Any, model_id: str) -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    for row in payload.get("summary", []):
        if (
            row.get("model_id") == model_id
            and row.get("scope") == "bucket"
            and row.get("label") == "days_1_30"
        ):
            mae = row.get("mae")
            values[str(row.get("period"))] = None if mae is None else float(mae)
    return dict(sorted(values.items()))


def models(store: ReferenceStore) -> dict[str, Any]:
    m2 = store.require_m2()
    payload = m2.payload
    recommendation = payload.get("recommendation") or {}
    selection = payload.get("selection") or {}
    recommended = str(recommendation.get("recommended_model"))
    selected = str(selection.get("selected_model"))
    infos = [
        ModelInfo(
            model_id=str(model["model_id"]),
            family=str(model.get("family")),
            model_type=str(model.get("model_type")),
            procedure=str(model.get("procedure")),
            description=str(model.get("description")),
            params=dict(model.get("params") or {}),
            spec_fingerprint=str(model.get("spec_fingerprint")),
            selected_on_development=model["model_id"] == selected,
            recommended=model["model_id"] == recommended,
            mae_days_1_30_by_period=_headline_mae(payload, str(model["model_id"])),
        )
        for model in payload["models"]
    ]
    data = ModelsData(
        recommended_model=recommended,
        recommendation_period=str(recommendation.get("period")),
        recommendation_reason=str(recommendation.get("reason")),
        reference_model=str(payload.get("reference_model")),
        selection=SelectionInfo(
            period=str(selection.get("period")),
            rule=_str_or_none(selection.get("rule")),
            selected_model=selected,
            best_baseline=_str_or_none(selection.get("best_baseline")),
            best_regression=_str_or_none(selection.get("best_regression")),
            margin_met=_bool_or_none(selection.get("margin_met")),
            significant=_bool_or_none(selection.get("significant")),
            reason=_str_or_none(selection.get("reason")),
        ),
        primary_tests=[
            PrimaryTest(
                hypothesis=str(test.get("hypothesis")),
                model_id=str(test.get("model_id")),
                reference=str(test.get("reference")),
                period=str(test.get("period")),
                first_horizon=int(test["first_horizon"]),
                last_horizon=int(test["last_horizon"]),
                mae=float(test["mae"]),
                mae_reference=float(test["mae_reference"]),
                mae_difference=float(test["mae_difference"]),
                mae_difference_ci_low=test.get("mae_difference_ci_low"),
                mae_difference_ci_high=test.get("mae_difference_ci_high"),
                holm_adjusted_p_value=test.get("holm_adjusted_p_value"),
                effective_sample_size=test.get("effective_sample_size"),
            )
            for test in recommendation.get("primary_tests") or []
        ],
        multiplicity=_str_or_none(recommendation.get("multiplicity")),
        models=infos,
    )
    return envelope(store, data, _m2_integrity(store), _m2_sources(store))


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def metrics(
    store: ReferenceStore,
    *,
    model_id: str | None,
    period: str | None,
    scope: str,
) -> dict[str, Any]:
    m2 = store.require_m2()
    payload = m2.payload
    known_models = sorted(str(m["model_id"]) for m in payload["models"])
    known_periods = [str(p["name"]) for p in payload["periods"]]
    if model_id is not None and model_id not in known_models:
        raise not_found("model", model_id, known_models)
    if period is not None and period not in known_periods:
        raise not_found("period", period, known_periods)
    rows = [
        MetricRow(
            model_id=str(row["model_id"]),
            period=str(row["period"]),
            scope=row["scope"],
            label=str(row["label"]),
            first_horizon=int(row["first_horizon"]),
            last_horizon=int(row["last_horizon"]),
            n=int(row["n"]),
            mae=row.get("mae"),
            rmse=row.get("rmse"),
            bias=row.get("bias"),
            bias_pct=row.get("bias_pct"),
            wape_pct=row.get("wape_pct"),
            mase=row.get("mase"),
            mape_pct=row.get("mape_pct"),
        )
        for row in payload["summary"]
        if row.get("scope") == scope
        and (model_id is None or row.get("model_id") == model_id)
        and (period is None or row.get("period") == period)
    ]
    periods = [
        PeriodInfo(
            name=str(p["name"]),
            role=str(p["role"]),
            first_origin=date.fromisoformat(str(p["first_origin"])),
            last_origin=date.fromisoformat(str(p["last_origin"])),
            first_target=date.fromisoformat(str(p["first_target"])),
            last_target=date.fromisoformat(str(p["last_target"])),
            n_origins=int(p["n_origins"]),
        )
        for p in payload["periods"]
    ]
    notes = [
        "bias = mean(prediction - actual): negative means under-forecast.",
        "MAPE is a secondary metric; MAE and RMSE are in requests/day.",
        "legacy_window is descriptive only (incomplete horizons are allowed there).",
    ]
    data = MetricsData(
        experiment_id=str(payload.get("experiment_id")),
        label=str(payload.get("label")),
        reference_model=str(payload.get("reference_model")),
        recommended_model=str((payload.get("recommendation") or {}).get("recommended_model")),
        periods=periods,
        rows=rows,
        notes=notes,
    )
    return envelope(store, data, _m2_integrity(store), _m2_sources(store))


def forecast(store: ReferenceStore, horizon_days: int) -> dict[str, Any]:
    inputs = store.require_inputs()
    projection = inputs.projection
    dates, values = projection.first(horizon_days)
    accuracy = projection.accuracy
    data = ForecastData(
        model_id=projection.model_id,
        record_type="future_projection",
        label=projection.label,
        origin=projection.origin,
        horizon_days=horizon_days,
        unit="requests_per_day",
        points=[
            ForecastPoint(
                target_date=d,
                horizon=h,
                prediction=v,
                status="projected",
                federal_holiday=holiday_names(d, d).get(d),
            )
            for h, (d, v) in enumerate(zip(dates, values, strict=True), start=1)
        ],
        accuracy_context=ForecastAccuracyContext(
            model_id=accuracy.model_id,
            period=accuracy.period,
            bucket=accuracy.bucket,
            mae_requests_per_day=accuracy.mae,
            bias_requests_per_day=accuracy.bias,
            bias_sign="prediction - actual (negative = under-forecast)",
            wape_pct=accuracy.wape_pct,
            n_predictions=accuracy.n_predictions,
        ),
    )
    block = integrity(
        store,
        record_type="future_projection",
        data_status="projected",
        notices=[projection.label, point_forecast_notice(store)],
    )
    return envelope(
        store,
        data,
        block,
        [store.forecast_source(), store.accuracy_source(), store.status_source()],
    )


def history(store: ReferenceStore, start: date | None, end: date | None) -> dict[str, Any]:
    snapshot = store.require_snapshot()
    first, last = snapshot.series.start, snapshot.series.end
    end = last if end is None else end
    start = max(first, end - timedelta(days=DEFAULT_HISTORY_DAYS - 1)) if start is None else start
    problems = []
    if start > end:
        problems.append({"field": "start", "message": "start must not be after end"})
    if (end - start).days + 1 > MAX_HISTORY_DAYS:
        problems.append({"field": "end", "message": f"at most {MAX_HISTORY_DAYS} days per request"})
    if start < first or end > last:
        problems.append(
            {
                "field": "start" if start < first else "end",
                "message": f"the snapshot covers {first.isoformat()}..{last.isoformat()}",
            }
        )
    if problems:
        raise ApiError(422, "validation_error", "invalid history window", details=problems)
    offset = (start - first).days
    points = list(store.history[offset : offset + (end - start).days + 1])
    data = HistoryData(
        snapshot_id=str(snapshot.provenance["snapshot_id"]),
        start=start,
        end=end,
        unit="requests_per_day",
        snapshot_first_date=first,
        snapshot_last_date=last,
        points=points,
    )
    block = integrity(
        store, record_type="observed_daily_total", data_status="observed", notices=[OBSERVED_NOTICE]
    )
    return envelope(store, data, block, [store.snapshot_source()])


# --- M3 ---------------------------------------------------------------------------------


def uncertainty_status(store: ReferenceStore) -> dict[str, Any]:
    inputs = store.require_inputs()
    status = inputs.status
    record = store.m3_record
    data = UncertaintyStatusData(
        interval_status=status.interval_status,
        decision_grade=status.decision_grade,
        statement=status.statement,
        note=None if record is None else record.note,
        evaluation_period=str(status.provenance.get("evaluation_period")),
        primary_model=status.primary_model,
        selected_pair=status.selected_pair,
        failure_reasons=[] if record is None else list(record.failure_reasons),
        interval_levels=[] if record is None else list(record.levels),
        exceedance_status=dict(status.exceedance),
        exceedance_reasons={}
        if record is None
        else {k: list(v) for k, v in record.exceedance_reasons.items()},
        evaluation_share_at_or_below_quantile_days_1_30=dict(status.quantile_calibration),
    )
    notices = [status.statement]
    if record is not None and record.note:
        notices.append(record.note)
    block = integrity(
        store,
        record_type="calibration_status",
        data_status="calibration_assessment",
        notices=notices,
    )
    return envelope(store, data, block, [store.status_source()])


def intervals(store: ReferenceStore, horizon_days: int) -> dict[str, Any]:
    inputs = store.require_inputs()
    intervals_ = store.require_m3_intervals()
    data = IntervalsData(
        model_id=inputs.status.primary_model,
        pair_id=intervals_.pair_id,
        interval_status=inputs.status.interval_status,
        label=PROJECTION_LABEL,
        origin=inputs.projection.origin,
        points=list(intervals_.points[:horizon_days]),
    )
    block = integrity(
        store,
        record_type="future_projection_intervals",
        data_status="projected",
        notices=[PROJECTION_LABEL, point_forecast_notice(store)],
    )
    return envelope(
        store,
        data,
        block,
        [store.intervals_source(), store.status_source(), store.forecast_source()],
    )


# --- M4 ---------------------------------------------------------------------------------


def _m4_integrity(store: ReferenceStore, outcomes: Sequence[ScenarioOutcome]) -> Integrity:
    notices = [M4_LABEL, RELAXED_NOTICE]
    if any(o.quantile is not None for o in outcomes):
        notices.append(MARGINAL_QUANTILE_NOTICE)
    return integrity(
        store,
        record_type="hypothetical_decision_simulation",
        data_status="hypothetical_reference_run",
        notices=notices,
        hypothetical=True,
    )


def _m4_sources(store: ReferenceStore) -> list[Source]:
    return [
        store.m4_source(),
        store.config_source(),
        store.forecast_source(),
        store.status_source(),
    ]


def plan_label(outcome: ScenarioOutcome) -> str:
    if outcome.feasibility == "strict":
        unique = outcome.optimal_face.unique if outcome.optimal_face is not None else None
        return (
            "HYPOTHETICAL optimised plan (meets every HYPOTHETICAL constraint)"
            if unique
            else "one of several equally scored HYPOTHETICAL plans"
            if unique is False
            else "HYPOTHETICAL optimised plan (uniqueness not established)"
        )
    return (
        "DIAGNOSTIC relaxed_terminal_target plan: no plan meets every HYPOTHETICAL "
        "constraint; this one misses the terminal target by the least amount"
    )


def summary(outcome: ScenarioOutcome) -> ScenarioSummary:
    evaluation = outcome.reported_plan.evaluation if outcome.reported_plan else None

    def value(name: str) -> float | None:
        return None if evaluation is None else float(getattr(evaluation, name))

    face = outcome.reported_plan_after_tie_breaks
    return ScenarioSummary(
        scenario_id=outcome.scenario_id,
        description=outcome.description,
        demand_basis=outcome.demand_basis,
        feasibility=outcome.feasibility,
        plan_label=plan_label(outcome),
        discretionary_cost=value("discretionary_cost"),
        backlog_request_days=value("backlog_request_days"),
        terminal_backlog=value("terminal_backlog"),
        peak_backlog=value("peak_backlog"),
        overtime_hours=value("overtime_hours"),
        surge_requests=value("surge_requests"),
        optimal_plan_unique=None if outcome.optimal_face is None else outcome.optimal_face.unique,
        unique_after_tie_breaks=None if face is None else face.unique,
        binding_constraints=list(outcome.binding_constraints),
        diagnosis_reasons=[] if outcome.diagnosis is None else list(outcome.diagnosis.reasons),
    )


def scenario_list(store: ReferenceStore) -> dict[str, Any]:
    m4 = store.require_m4()
    outcomes = list(m4.outcomes.values())
    data = ScenarioListData(
        run_id=m4.run_id,
        result_fingerprint=m4.result_fingerprint,
        config_fingerprint=m4.config_fingerprint,
        label=m4.label,
        horizon_days=int(m4.horizon["days"]),
        first_date=date.fromisoformat(str(m4.horizon["first_date"])),
        last_date=date.fromisoformat(str(m4.horizon["last_date"])),
        scenarios=[summary(o) for o in outcomes],
    )
    return envelope(store, data, _m4_integrity(store, outcomes), _m4_sources(store))


def scenario_detail(store: ReferenceStore, scenario_id: str) -> dict[str, Any]:
    m4 = store.require_m4()
    outcome = m4.outcomes.get(scenario_id)
    if outcome is None:
        raise not_found("scenario", scenario_id, list(m4.outcomes))
    data = ScenarioDetailData(
        run_id=m4.run_id,
        label=m4.label,
        outcome=outcome,
        plans=list(m4.variants[scenario_id]),
        daily_plans=list(m4.daily[scenario_id]),
        hypothetical_parameters=[
            p
            for p in m4.hypothetical_parameters
            if not str(p.get("scope", "")).startswith("scenario:")
            or p.get("scope") == f"scenario:{scenario_id}"
        ],
        structural_assumptions=list(m4.structural_assumptions),
    )
    return envelope(store, data, _m4_integrity(store, [outcome]), _m4_sources(store))


def _value(outcome: ScenarioOutcome, metric: str) -> float | None:
    if metric == "penalised_score":
        return outcome.optimal_score if outcome.feasibility == "strict" else None
    evaluation = outcome.reported_plan.evaluation if outcome.reported_plan else None
    return None if evaluation is None else float(getattr(evaluation, metric))


def comparison(
    store: ReferenceStore,
    scenario_ids: Sequence[str],
    baseline: str,
    metrics_: Sequence[ComparisonMetric],
) -> dict[str, Any]:
    m4 = store.require_m4()
    known = list(m4.outcomes)
    for identifier in [*scenario_ids, baseline]:
        if identifier not in m4.outcomes:
            raise not_found("scenario", identifier, known)
    chosen = list(dict.fromkeys(scenario_ids)) or known
    if baseline not in chosen:
        chosen.insert(0, baseline)
    base = m4.outcomes[baseline]
    rows = []
    for identifier in chosen:
        outcome = m4.outcomes[identifier]
        values = {m: _value(outcome, m) for m in metrics_}
        differences: dict[str, float | None] = {}
        for metric in metrics_:
            mine, theirs = values[metric], _value(base, metric)
            differences[metric] = None if mine is None or theirs is None else mine - theirs
        rows.append(
            ComparisonRow(
                scenario_id=identifier,
                feasibility=outcome.feasibility,
                values=values,
                difference_from_baseline=differences,
            )
        )
    data = ComparisonData(
        baseline_scenario_id=baseline,
        metrics=list(metrics_),
        units={m: COMPARISON_UNITS[m] for m in metrics_},
        rows=rows,
        caveat=(
            "Differences between HYPOTHETICAL scenarios computed from one unvalidated demand "
            "path; they illustrate the model's sensitivity, not expected outcomes. "
            "penalised_score is reported only for strictly feasible scenarios (a relaxed "
            "plan's score is a different programme's)."
        ),
    )
    outcomes = [m4.outcomes[i] for i in chosen]
    return envelope(store, data, _m4_integrity(store, outcomes), _m4_sources(store))


def simulation_integrity(store: ReferenceStore, quantile: bool) -> Integrity:
    notices = [M4_LABEL, RELAXED_NOTICE, point_forecast_notice(store)]
    if quantile:
        notices.append(MARGINAL_QUANTILE_NOTICE)
    return integrity(
        store,
        record_type="hypothetical_decision_simulation",
        data_status="hypothetical_simulation",
        notices=notices,
        hypothetical=True,
    )


def simulation_sources(store: ReferenceStore) -> list[Source]:
    return [store.config_source(), store.forecast_source(), store.status_source()]


__all__ = [
    "MAX_HISTORY_DAYS",
    "comparison",
    "forecast",
    "history",
    "intervals",
    "metrics",
    "models",
    "scenario_detail",
    "scenario_list",
    "simulation_integrity",
    "simulation_sources",
    "system",
    "uncertainty_status",
]
