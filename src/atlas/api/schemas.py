"""HTTP contracts of the ATLAS API (M5): every request and response body, with units.

Every data response is an envelope ``{"data": ..., "integrity": ..., "provenance": ...}``:

* ``integrity`` states what kind of number ``data`` holds (observed history, a projection, a
  historical evaluation, a HYPOTHETICAL simulation, ...), the M3 calibration status read from
  the committed status file, and two flags that the schema itself fixes to ``false``:
  ``point_forecast_validated`` and ``validated_recommendation``. No response of this API can
  claim a validated forecast or a validated recommendation.
* ``provenance`` names every committed file the data came from, with its run id, result
  fingerprint and SHA-256, all checked when the API started.

The M4 structures (scenario outcomes, daily plans, diagnoses, policy comparisons) mirror the
fields of the M4 runner's ``metrics.json`` and ``plans.csv`` exactly, so a simulation and the
committed reference run are described by one contract. Field names keep M4's names; the
unit of every number is in its description.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from atlas.api.problems import ErrorCode
from atlas.planning import MAX_HORIZON_DAYS

API_VERSION: Final = "v1"
HOLIDAY_RELATED_DESCRIPTION: Final = (
    "A federal holiday (actual or observed date) or the day before or after the actual date: "
    "an M3 stratum with markedly lower interval coverage (64.1 % at nominal 80 % and 83.3 % at "
    "95 %, against 84.8 % and 94.3 % on ordinary days, over only 33 distinct target days; "
    "UNCERTAINTY.md section 9.6)."
)
#: Marginal quantile levels of the M3 projection a simulation may use as a stress path.
STRESS_QUANTILE_LEVELS: Final = (0.75, 0.9, 0.975)


_ISO_DATE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _iso_date_only(value: object) -> object:
    """Query dates must be plain ``YYYY-MM-DD`` (no times, no other formats)."""
    if isinstance(value, str) and not _ISO_DATE.fullmatch(value):
        raise ValueError("use a calendar date YYYY-MM-DD")
    return value


#: A calendar date given as exactly ``YYYY-MM-DD``.
IsoDate = Annotated[date, BeforeValidator(_iso_date_only)]

_DIGITS: Final = re.compile(r"[0-9]{1,6}")


def _decimal_digits(value: object) -> object:
    """Query integers are plain decimal digits: no sign, ``_``, exponent or fraction."""
    if isinstance(value, str) and not _DIGITS.fullmatch(value):
        raise ValueError("expected an integer written with the digits 0-9 only")
    return value


#: An integer query parameter (``horizon_days=7``; ``+7``, ``7.0`` and ``7_0`` are refused).
QueryInt = Annotated[int, BeforeValidator(_decimal_digits)]


class _Model(BaseModel):
    # Every response field is always sent: the contract marks fields with defaults as
    # required too, so generated clients never type them as optional.
    model_config = ConfigDict(
        frozen=True, extra="forbid", json_schema_serialization_defaults_required=True
    )


class _Request(_Model):
    """Request bodies are validated strictly: unknown fields, strings for numbers, booleans
    for numbers and fractional integers are all rejected."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        json_schema_serialization_defaults_required=True,
    )


# --- Envelope ---------------------------------------------------------------------------

#: The four M3 decision statuses (machine values; reports print them upper-case).
IntervalStatus = Literal[
    "decision_grade", "failed_calibration", "insufficient_evidence", "unavailable"
]

DataStatus = Literal[
    "observed",
    "projected",
    "historical_evaluation",
    "calibration_assessment",
    "hypothetical_reference_run",
    "hypothetical_simulation",
    "metadata",
]


class Integrity(_Model):
    """What the numbers in ``data`` are, and what they are not."""

    record_type: str = Field(description="Record type of the source (as written by ATLAS).")
    data_status: DataStatus = Field(
        description="observed: recorded history; projected: forecasts of days not yet "
        "observed; historical_evaluation: backtest scores of past days; "
        "calibration_assessment: the M3 calibration verdict; hypothetical_reference_run / "
        "hypothetical_simulation: M4 decision simulations with HYPOTHETICAL parameters."
    )
    point_forecast_validated: Literal[False] = Field(
        default=False,
        description="Always false: the M2 point forecast is not prospectively validated.",
    )
    uncertainty_interval_status: IntervalStatus = Field(
        description="M3 interval status from the committed calibration_status.json "
        "(the reference run reports failed_calibration, printed FAILED_CALIBRATION)."
    )
    uncertainty_decision_grade: bool = Field(
        description="Whether M3 granted decision-grade status (false for the reference run)."
    )
    parameter_provenance: Literal["HYPOTHETICAL"] | None = Field(
        description="HYPOTHETICAL for every M4 result: no operational parameter is measured."
    )
    validated_recommendation: Literal[False] = Field(
        default=False,
        description="Always false: nothing this API returns is a validated staffing, budget "
        "or service-capacity recommendation.",
    )
    notices: list[str] = Field(description="Labels that must travel with the data.")


class Source(_Model):
    """One committed input, checked when the API started."""

    kind: str
    path: str = Field(description="Repository-relative path of the file or run directory.")
    record_type: str | None = None
    run_id: str | None = None
    result_fingerprint: str | None = None
    config_fingerprint: str | None = None
    sha256: str | None = Field(default=None, description="SHA-256 of the file served.")


class Provenance(_Model):
    atlas_version: str
    api_version: str
    snapshot_id: str
    data_fingerprint: str | None = Field(
        description="Fingerprint of the legacy-v0 data (after the M0 integrity and contract "
        "checks) every source was made from."
    )
    sources: list[Source]


class Envelope(_Model):
    """Every data response: ``data`` (in each subclass), ``integrity`` and ``provenance``."""

    integrity: Integrity
    provenance: Provenance


# --- Errors -----------------------------------------------------------------------------


class ErrorDetail(_Model):
    field: str
    message: str


class ErrorBody(_Model):
    code: ErrorCode = Field(description="Stable identifier (docs/API.md, section 4).")
    message: str
    request_id: str | None
    details: list[ErrorDetail] = Field(
        description="Problems by field: the dotted path inside the body "
        "(parameters.regular_wage) or the query parameter (start); <body> for the body."
    )


class ErrorResponse(_Model):
    error: ErrorBody


# --- Health and system ------------------------------------------------------------------


class LiveResponse(_Model):
    status: Literal["alive"]
    atlas_version: str


class ComponentStatus(_Model):
    component: str
    ready: bool
    detail: str | None = Field(description="Why a component is not ready (safe summary).")


class ReadyResponse(_Model):
    status: Literal["ready", "not_ready"]
    components: list[ComponentStatus]


class SnapshotInfo(_Model):
    snapshot_id: str
    data_fingerprint: str
    first_date: date
    last_date: date
    n_days: int


class ScientificStatus(_Model):
    forecast_model: str
    forecast_label: str
    forecast_confirmation_mae_requests_per_day: float
    point_forecast_validated: Literal[False] = False
    uncertainty_interval_status: IntervalStatus
    uncertainty_decision_grade: bool
    uncertainty_statement: str
    decision_simulation_label: str
    parameter_provenance: Literal["HYPOTHETICAL"] = "HYPOTHETICAL"
    validated_recommendation: Literal[False] = False
    limitations: list[str]


class Limits(_Model):
    max_body_bytes: int
    max_concurrent_simulations: int
    simulation_timeout_seconds: float
    simulation_requires_api_key: bool
    max_history_days: int
    max_horizon_days: int


class EndpointInfo(_Model):
    method: Literal["GET", "POST"]
    path: str
    cost: Literal["static", "precomputed", "bounded_computation"]
    interactive: bool
    summary: str


class WorkerPoolStatus(_Model):
    """The simulation worker processes (counters since the server started)."""

    workers: int
    alive: int = Field(description="Worker processes currently running (or importing).")
    idle: int
    started: bool
    completed: int = Field(description="Simulations a worker answered (any outcome).")
    timeouts: int = Field(description="Simulations stopped at their deadline.")
    crashes: int = Field(description="Simulations whose worker died or never became ready.")
    consecutive_crashes: int = Field(
        description="Crashes since the last answered simulation; /health/ready reports the "
        "workers not ready from 3."
    )


class SystemData(_Model):
    service: Literal["atlas-api"]
    atlas_version: str
    api_version: str
    milestone: Literal["M5"]
    snapshot: SnapshotInfo | None
    components: list[ComponentStatus]
    scientific_status: ScientificStatus | None
    limits: Limits
    simulation_workers: WorkerPoolStatus | None
    solver: dict[str, str | None]
    endpoints: list[EndpointInfo]


class SystemResponse(Envelope):
    data: SystemData


# --- M2: models, forecast, history, metrics ---------------------------------------------


class ModelInfo(_Model):
    model_id: str
    family: str = Field(description="baseline or regression.")
    model_type: str
    procedure: str = Field(description="direct or recursive (how 30-day forecasts are made).")
    description: str
    params: dict[str, Any]
    spec_fingerprint: str
    selected_on_development: bool
    recommended: bool
    mae_days_1_30_by_period: dict[str, float | None] = Field(
        description="MAE (requests/day) over horizons 1-30 in each M2 period."
    )


class SelectionInfo(_Model):
    period: str
    rule: str | None
    selected_model: str
    best_baseline: str | None
    best_regression: str | None
    margin_met: bool | None
    significant: bool | None
    reason: str | None


class PrimaryTest(_Model):
    """One pre-registered confirmation test of the recommended model (Holm-adjusted)."""

    hypothesis: str
    model_id: str
    reference: str
    period: str
    first_horizon: int
    last_horizon: int
    mae: float = Field(description="requests/day")
    mae_reference: float = Field(description="requests/day")
    mae_difference: float = Field(description="model - reference, requests/day")
    mae_difference_ci_low: float | None
    mae_difference_ci_high: float | None
    holm_adjusted_p_value: float | None
    significant_after_holm: bool | None = Field(
        description="Holm-adjusted p below 0.05 (null if no p-value was computed)."
    )
    effective_sample_size: float | None


class ModelsData(_Model):
    recommended_model: str
    recommendation_period: str
    recommendation_reason: str
    reference_model: str
    selection: SelectionInfo
    primary_tests: list[PrimaryTest] = Field(
        description="The confirmation tests behind the recommendation; read the interval of "
        "the MAE difference and the Holm-adjusted p-value before calling the model better."
    )
    multiplicity: str | None
    models: list[ModelInfo]


class ModelsResponse(Envelope):
    data: ModelsData


class ForecastPoint(_Model):
    target_date: date
    horizon: int = Field(ge=1, le=30, description="Days after the forecast origin.")
    prediction: float = Field(description="Point forecast, requests/day.")
    status: Literal["projected"]
    federal_holiday: str | None = Field(
        default=None, description="US federal holiday observed that day (M1 rules)."
    )
    holiday_related: bool = Field(default=False, description=HOLIDAY_RELATED_DESCRIPTION)


class ForecastAccuracyContext(_Model):
    """Historical error of the projected model on M2's confirmation period (context only;
    never used to adjust the forecast)."""

    model_id: str
    period: str
    bucket: str
    mae_requests_per_day: float
    bias_requests_per_day: float
    bias_sign: str
    wape_pct: float
    n_predictions: int


class ForecastData(_Model):
    model_id: str
    record_type: Literal["future_projection"]
    label: str
    origin: date = Field(description="Last observed day the forecast was made from.")
    horizon_days: int
    unit: Literal["requests_per_day"]
    points: list[ForecastPoint]
    accuracy_context: ForecastAccuracyContext


class ForecastResponse(Envelope):
    data: ForecastData


class ObservedPoint(_Model):
    date: date
    requests: int | None = Field(
        ge=0, description="City-wide 311 requests created that day (null: not recorded)."
    )
    status: Literal["observed", "missing"]
    federal_holiday: str | None = Field(
        default=None, description="US federal holiday observed that day (M1 rules)."
    )
    holiday_related: bool = Field(default=False, description=HOLIDAY_RELATED_DESCRIPTION)


class HistoryData(_Model):
    snapshot_id: str
    start: date
    end: date
    unit: Literal["requests_per_day"]
    snapshot_first_date: date
    snapshot_last_date: date
    points: list[ObservedPoint]


class HistoryResponse(Envelope):
    data: HistoryData


class PeriodInfo(_Model):
    name: str
    role: str
    first_origin: date
    last_origin: date
    first_target: date
    last_target: date
    n_origins: int


class MetricRow(_Model):
    """Accuracy of one model over one horizon or horizon bucket of one M2 period."""

    model_id: str
    period: str
    scope: Literal["horizon", "bucket"]
    label: str
    first_horizon: int
    last_horizon: int
    n: int
    mae: float | None = Field(description="Mean absolute error, requests/day.")
    rmse: float | None = Field(description="Root mean squared error, requests/day.")
    bias: float | None = Field(description="Mean of prediction - actual, requests/day.")
    bias_pct: float | None
    wape_pct: float | None
    mase: float | None
    mape_pct: float | None = Field(description="Secondary metric only (see BACKTESTING.md).")


class MetricsData(_Model):
    experiment_id: str
    label: str
    reference_model: str
    recommended_model: str
    periods: list[PeriodInfo]
    rows: list[MetricRow]
    notes: list[str]


class MetricsResponse(Envelope):
    data: MetricsData


# --- M3: uncertainty --------------------------------------------------------------------


class IntervalLevelEvidence(_Model):
    """Coverage evidence of the primary model's intervals at one nominal level."""

    level: float
    coverage: float | None
    coverage_ci_low: float | None
    coverage_ci_high: float | None
    effective_sample_size: float | None
    evidence_sufficient: bool | None
    p_value_conservative: float | None
    rejected: bool | None
    scopes_outside_tolerance: list[str]
    test_computed: bool | None
    tolerance_pct: float | None


class UncertaintyStatusData(_Model):
    interval_status: IntervalStatus = Field(description="failed_calibration for the reference run.")
    decision_grade: bool
    statement: str
    note: str | None
    evaluation_period: str
    primary_model: str
    selected_pair: str
    failure_reasons: list[str] = Field(description="Why the intervals failed, as recorded.")
    interval_levels: list[IntervalLevelEvidence]
    exceedance_status: dict[str, str]
    exceedance_reasons: dict[str, list[str]]
    evaluation_share_at_or_below_quantile_days_1_30: dict[str, float] = Field(
        description="Share of evaluation outcomes at or below each nominal quantile "
        "(perfect calibration would equal the quantile level)."
    )


class UncertaintyStatusResponse(Envelope):
    data: UncertaintyStatusData


class IntervalPoint(_Model):
    target_date: date
    horizon: int
    prediction: float
    quantiles: dict[str, float] = Field(
        description="Marginal quantiles (requests/day) keyed by level. Their calibration "
        "FAILED (M3 status failed_calibration): they carry no probability to rely on."
    )
    status: Literal["projected"]
    holiday_related: bool = Field(description=HOLIDAY_RELATED_DESCRIPTION)


class IntervalsData(_Model):
    model_id: str
    pair_id: str
    interval_status: IntervalStatus
    label: str
    origin: date
    points: list[IntervalPoint]


class IntervalsResponse(Envelope):
    data: IntervalsData


# --- M4: scenario outcomes (shared by the reference run and simulations) ----------------


class HypotheticalQuantity(_Model):
    """A HYPOTHETICAL number with its exact unit and the assumption behind it."""

    value: int | float
    unit: str
    provenance: Literal["HYPOTHETICAL"]
    assumption: str


class HypotheticalParameter(_Model):
    """One HYPOTHETICAL parameter of a run (a sweep grid has a text value listing it)."""

    scope: str = Field(description="unit:<id>, budget, scenario:<id> or sensitivity:<id>.")
    parameter: str
    value: int | float | str
    unit: str
    provenance: Literal["HYPOTHETICAL"]
    assumption: str


class Certificate(_Model):
    holds: bool
    primal: float | None
    dual_sign: float | None
    stationarity: float | None
    duality_gap: float | None
    primal_objective: float | None
    dual_objective: float | None


class PlanEvaluation(_Model):
    """Totals of one plan. Cash in USD; penalties are notional USD-equivalent weights,
    never cash; backlog in requests; request-days = sum of end-of-day backlogs."""

    regular_cost: float = Field(description="Committed regular staff cost, USD (not in score).")
    overtime_cost: float = Field(description="USD.")
    surge_cost: float = Field(description="USD.")
    discretionary_cost: float = Field(description="Overtime + surge cash against the budget, USD.")
    operating_cost: float = Field(description="Regular + discretionary cash, USD.")
    backlog_penalty: float = Field(description="Notional weight x request-days (not cash).")
    terminal_penalty: float = Field(description="Notional weight x terminal backlog (not cash).")
    penalised_score: float = Field(description="Ranking score only; never a cost.")
    total_demand: float
    initial_backlog: float
    total_served: float
    backlog_request_days: float
    terminal_backlog: float
    peak_backlog: float
    days_with_backlog: int
    overtime_hours: float
    surge_requests: float
    idle_paid_capacity: float
    discretionary_budget: float | None
    budget_remaining: float | None


class Programme(_Model):
    """One solved programme: its status, the three independent checks and the plan totals."""

    status: str
    objective: str | None = None
    objective_value: float | None = None
    objective_recomputed_from_plan: float | None = None
    highs_status: int | None = None
    iterations: int | None = None
    retried_without_presolve: bool | None = None
    stage_fallback: str | None = None
    certificate: Certificate | None = None
    independent_check_passed: bool | None = None
    constraints_not_checked: list[str] = Field(default_factory=list)
    values_snapped_to_bounds: int | None = None
    largest_snap: float | None = None
    max_balance_residual: float | None = None
    evaluation: PlanEvaluation | None = None
    message: str | None = None
    evaluation_note: str | None = None


class FaceRange(_Model):
    quantity: str
    units: str
    low: float
    high: float


class OptimalFace(_Model):
    """Uniqueness test over all optimal plans (M4 section 7)."""

    unique: bool | None = Field(description="null when the test could not be completed.")
    method: str
    dual_threshold: float
    bounds_fixed: int
    rows_tight: int
    max_active_distance: float
    undetermined_days: int | None
    ranges: list[FaceRange]


class Diagnosis(_Model):
    """Why the strict model is infeasible: DIAGNOSTIC values, never budget requests."""

    label: str
    borderline_within_checking_tolerance: bool
    least_terminal_violation_unlimited_discretionary_cash_requests: float | None
    least_terminal_violation_within_budget_requests: float | None
    diagnostic_min_discretionary_spend_to_reach_hypothetical_target_usd: float | None
    diagnostic_budget_shortfall_usd: float | None
    analytic_terminal_backlog_lower_bound_requests: dict[str, float]
    analytic_bound_exact: bool
    reasons: list[str]
    least_violation_unlimited_cash_with_one_limit_removed_requests: dict[str, float | None]
    relaxed_plan: str | None


class PolicyComparison(_Model):
    """The reported plan against a simple policy under the same assumptions."""

    policy: Literal["fixed_capacity", "backlog_trigger"]
    reported_plan: str
    policy_constraints_violated: list[str]
    comparable_on_score: bool
    relation_of_reported_plan: Literal["dominates", "dominated", "tradeoff", "equal"]
    discretionary_cost_difference: float = Field(description="Reported - policy, USD.")
    request_days_difference: float = Field(description="Reported - policy, request-days.")
    terminal_backlog_difference: float = Field(description="Reported - policy, requests.")
    lp_request_days_at_policy_cash: float | None = None
    request_days_difference_at_same_cash: float | None = None
    lp_cash_at_policy_backlog: float | None = None
    cash_difference_at_same_backlog: float | None = None


class ShadowPriceHeadline(_Model):
    constraint: str
    solver_dual_subgradient: float
    dual_units: str
    fd_backward: float | None
    fd_forward: float | None
    fd_status: str
    practical_forward_cash_part: float | None
    practical_forward_penalty_part: float | None
    binding: bool


class ShadowPrices(_Model):
    """Bracketed shadow prices of the reference run (USD-equivalent; not wages)."""

    computed: bool
    n: int
    brackets_ok: bool | None
    locally_unique: int
    breakpoint: int
    solver_failed: int
    domain_boundary_or_infeasible_side: int
    backward_unresolved: int
    headline: list[ShadowPriceHeadline]


class FrontierSummary(_Model):
    on_frontier_check: str | None
    monotone_ok: bool | None
    convex_ok: bool | None
    least_cash_for_least_penalty: dict[str, float | None]
    points_not_unique_after_tie_breaks: int
    points_with_overtime_or_surge_totals_open: int
    points_solver_failed: int
    points_uniqueness_not_tested: int
    least_penalty_programme_status: dict[str, str]


class ScenarioOutcome(_Model):
    """One scenario as the M4 runner reports it (``metrics.json`` ``scenarios[]``)."""

    scenario_id: str
    description: str
    demand_basis: str = Field(description="point_forecast or per_day_marginal_quantile_path.")
    quantile: float | None
    adjustments: dict[str, HypotheticalQuantity] = Field(
        description="HYPOTHETICAL changes to the base case."
    )
    demand_sha256: str
    demand_total_requests: float
    demand_total_vs_point_forecast_pct: float
    regular_capacity_mean_requests_per_day: float
    discretionary_budget_usd: float | None
    feasibility: Literal["strict", "relaxed_terminal_target"] = Field(
        description="strict: the plan meets every HYPOTHETICAL constraint. "
        "relaxed_terminal_target: no plan does; the reported plan is the DIAGNOSTIC plan that "
        "misses the terminal target by the least amount within the budget."
    )
    optimal_score: float | None
    reported_plan_selection: str
    reported_plan: Programme | None
    strict_programme: Programme | None
    without_terminal_target: Programme | None
    relaxed: Programme | None
    terminal_target_changes_optimal_score: bool | None
    terminal_target_changes_reported_plan: bool | None
    stage_fallbacks: list[str]
    diagnosis: Diagnosis | None
    optimal_face: OptimalFace | None
    reported_plan_after_tie_breaks: OptimalFace | None
    binding_constraints: list[str]
    shadow_prices: ShadowPrices
    comparisons: list[PolicyComparison]
    frontier: FrontierSummary | None
    problems: list[str] = Field(description="Failed checks (an empty list on a clean result).")


class DayPlan(_Model):
    """One day of one plan (``plans.csv``): requests/day, staff hours, requests."""

    plan: str = Field(description="optimized, relaxed_terminal_target, or a policy name.")
    day: int
    date: date
    demand: float
    work_available: float = Field(description="Backlog carried in + demand, requests.")
    regular_capacity: float
    overtime_hours: float
    overtime_capacity: float
    surge_requests: float
    served: float
    backlog: float = Field(description="End-of-day backlog, requests.")
    extra_capacity: float
    extra_capacity_min_over_optimal_plans: float | None
    extra_capacity_max_over_optimal_plans: float | None


class PlanTotals(_Model):
    """The totals ``scenarios.csv`` records for one plan (units as in
    :class:`PlanEvaluation`)."""

    discretionary_cost: float
    overtime_cost: float
    surge_cost: float
    regular_cost: float
    operating_cost: float
    backlog_penalty: float
    terminal_penalty: float
    penalised_score: float
    backlog_request_days: float
    terminal_backlog: float
    peak_backlog: float
    days_with_backlog: int
    overtime_hours: float
    surge_requests: float
    idle_paid_capacity: float
    total_served: float
    budget_remaining: float | None


class PlanVariant(_Model):
    """One plan of a scenario (``scenarios.csv``): reported, without target, policies and
    the LP at a policy's cash or backlog."""

    plan: str
    feasibility: str
    plan_selection: str | None
    unique_optimal_plan: bool | None
    unique_after_tie_breaks: bool | None
    solver_status: str | None
    terminal_backlog_max: float | None = Field(
        description="The HYPOTHETICAL terminal target, requests (null: no target)."
    )
    terminal_violation: float | None
    satisfies_budget: bool | None
    reported_plan_relation: str | None
    totals: PlanTotals | None


class ScenarioSummary(_Model):
    scenario_id: str
    description: str
    demand_basis: str
    feasibility: Literal["strict", "relaxed_terminal_target"]
    plan_label: str
    discretionary_cost: float | None
    backlog_request_days: float | None
    terminal_backlog: float | None
    peak_backlog: float | None
    overtime_hours: float | None
    surge_requests: float | None
    optimal_plan_unique: bool | None
    unique_after_tie_breaks: bool | None
    binding_constraints: list[str]
    diagnosis_reasons: list[str]


class ScenarioListData(_Model):
    run_id: str
    result_fingerprint: str
    config_fingerprint: str
    label: str
    horizon_days: int
    first_date: date
    last_date: date
    scenarios: list[ScenarioSummary]


class ScenarioListResponse(Envelope):
    data: ScenarioListData


class ScenarioDetailData(_Model):
    run_id: str
    label: str
    outcome: ScenarioOutcome
    plans: list[PlanVariant]
    daily_plans: list[DayPlan]
    hypothetical_parameters: list[HypotheticalParameter] = Field(
        description="Every HYPOTHETICAL parameter of the run with its unit and assumption."
    )
    structural_assumptions: list[str]


class ScenarioDetailResponse(Envelope):
    data: ScenarioDetailData


ComparisonMetric = Literal[
    "discretionary_cost",
    "backlog_request_days",
    "terminal_backlog",
    "peak_backlog",
    "overtime_hours",
    "surge_requests",
    "penalised_score",
]


class ComparisonRow(_Model):
    scenario_id: str
    description: str
    adjustments: dict[str, HypotheticalQuantity]
    feasibility: Literal["strict", "relaxed_terminal_target"]
    values_status: Literal["plan_meets_every_constraint", "DIAGNOSTIC"] = Field(
        description="DIAGNOSTIC: no plan meets every HYPOTHETICAL constraint; the values are "
        "those of the relaxed plan, which misses the terminal target."
    )
    terminal_backlog_max: float | None = Field(description="The HYPOTHETICAL target, requests.")
    terminal_violation: float | None = Field(
        description="Requests the reported plan leaves above the target (0 when it meets it)."
    )
    values: dict[str, float | None]
    difference_from_baseline: dict[str, float | None] = Field(
        description="This scenario minus the baseline (same units as values); null unless "
        "both plans meet every constraint."
    )


class ComparisonData(_Model):
    baseline_scenario_id: str
    metrics: list[ComparisonMetric]
    units: dict[str, str]
    rows: list[ComparisonRow]
    caveat: str


class ComparisonResponse(Envelope):
    data: ComparisonData


# --- M4: interactive simulation ---------------------------------------------------------


class DemandSettings(_Request):
    """The demand path of a simulation: the M2 point forecast, or a labelled M3 marginal
    quantile path (a HYPOTHETICAL stress path, never a probability), then scaled."""

    basis: Literal["point_forecast", "m3_marginal_quantile"] = "point_forecast"
    quantile: float | None = Field(
        default=None,
        description="One of 0.75, 0.9, 0.975; required (and only allowed) with basis "
        "m3_marginal_quantile.",
        json_schema_extra={"enum": [*STRESS_QUANTILE_LEVELS, None]},
    )
    multiplier: float = Field(default=1.0, ge=0.5, le=2.0, allow_inf_nan=False)
    shift_per_day: float = Field(
        default=0.0, ge=0.0, le=5_000.0, allow_inf_nan=False, description="requests/day"
    )

    @field_validator("quantile")
    @classmethod
    def _offered_level(cls, value: float | None) -> float | None:
        if value is not None and value not in STRESS_QUANTILE_LEVELS:
            raise ValueError(f"must be one of {', '.join(map(str, STRESS_QUANTILE_LEVELS))}")
        return value


#: Money and penalty rates are 0 or at least this: a smaller nonzero rate would put a
#: coefficient of 1e-9 or less into the programme, which HiGHS drops silently (M4 rejects it).
MIN_NONZERO_RATE: Final = 0.01


class SimulationParameters(_Request):
    """HYPOTHETICAL operational parameters of the one city-wide unit.

    An omitted field takes the reference configuration's value (GET
    /api/v1/simulations/parameters). For the three optional limits
    (``overtime_total_hours``, ``surge_total``, ``terminal_backlog_max``) and the budget, an
    explicit ``null`` removes the limit; for every other field ``null`` is rejected. The
    ranges keep every programme at the scale the M4 solver layer was reviewed at.
    """

    initial_backlog: float | None = Field(
        default=None, ge=0, le=200_000, allow_inf_nan=False, description="requests"
    )
    regular_hours_per_day: float | None = Field(
        default=None, ge=0, le=12_000, allow_inf_nan=False, description="staff_hours_per_day"
    )
    regular_productivity: float | None = Field(
        default=None, ge=0.1, le=10, allow_inf_nan=False, description="requests_per_staff_hour"
    )
    overtime_productivity: float | None = Field(
        default=None, ge=0.1, le=10, allow_inf_nan=False, description="requests_per_staff_hour"
    )
    overtime_max_hours_per_day: float | None = Field(
        default=None, ge=0, le=5_000, allow_inf_nan=False, description="staff_hours_per_day"
    )
    overtime_total_hours: float | None = Field(
        default=None,
        ge=0,
        le=150_000,
        allow_inf_nan=False,
        description="staff_hours over the horizon; null removes the limit",
    )
    surge_max_per_day: float | None = Field(
        default=None, ge=0, le=50_000, allow_inf_nan=False, description="requests_per_day"
    )
    surge_total: float | None = Field(
        default=None,
        ge=0,
        le=1_500_000,
        allow_inf_nan=False,
        description="requests over the horizon; null removes the limit",
    )
    surge_available_from_day: int | None = Field(
        default=None,
        ge=1,
        le=31,
        description="planning_day (1-based); horizon_days + 1 means never within the horizon",
    )
    regular_wage: float | None = Field(
        default=None, ge=0, le=1_000, allow_inf_nan=False, description="usd_per_staff_hour"
    )
    overtime_wage: float | None = Field(
        default=None, ge=0, le=1_000, allow_inf_nan=False, description="usd_per_staff_hour"
    )
    surge_price: float | None = Field(
        default=None, ge=0, le=1_000, allow_inf_nan=False, description="usd_per_request"
    )
    backlog_penalty: float | None = Field(
        default=None,
        ge=0,
        le=1_000,
        allow_inf_nan=False,
        description="usd_per_request_day (notional weight, not cash)",
    )
    terminal_penalty: float | None = Field(
        default=None,
        ge=0,
        le=10_000,
        allow_inf_nan=False,
        description="usd_per_request (notional weight, not cash)",
    )
    terminal_backlog_max: float | None = Field(
        default=None,
        ge=0,
        le=1_000_000,
        allow_inf_nan=False,
        description="requests left after the last day; null removes the target",
    )
    backlog_trigger: float | None = Field(
        default=None,
        ge=0,
        le=1_000_000,
        allow_inf_nan=False,
        description="requests (trigger of the backlog-trigger comparison policy)",
    )
    discretionary_budget: float | None = Field(
        default=None,
        ge=0,
        le=100_000_000,
        allow_inf_nan=False,
        description="usd for overtime and surge over the horizon; null removes the limit",
    )

    @field_validator(
        "regular_wage", "overtime_wage", "surge_price", "backlog_penalty", "terminal_penalty"
    )
    @classmethod
    def _zero_or_material(cls, value: float | None) -> float | None:
        if value is not None and 0 < value < MIN_NONZERO_RATE:
            raise ValueError(f"must be 0 or at least {MIN_NONZERO_RATE}")
        return value


class SimulationRequest(_Request):
    horizon_days: int = Field(
        default=MAX_HORIZON_DAYS,
        ge=1,
        le=MAX_HORIZON_DAYS,
        description="Planning days, from the first projected day.",
    )
    demand: DemandSettings = DemandSettings()
    parameters: SimulationParameters = SimulationParameters()


class ParameterSpec(_Model):
    name: str
    unit: str
    default: float | int | None
    minimum: float
    maximum: float
    nullable: bool = Field(description="An explicit null removes this limit.")
    default_assumption: str


class RangeSpec(_Model):
    default: float
    minimum: float
    maximum: float
    unit: str


class HorizonSpec(_Model):
    default: int
    minimum: int
    maximum: int
    first_date: date = Field(description="The first projected day (day 1 of every plan).")


class DemandOptions(_Model):
    basis: list[Literal["point_forecast", "m3_marginal_quantile"]]
    quantile: list[float] = Field(description="Levels offered for m3_marginal_quantile.")
    multiplier: RangeSpec
    shift_per_day: RangeSpec
    notice: str = Field(description="What a quantile stress path is, and is not.")


class SimulationParametersData(_Model):
    horizon_days: HorizonSpec
    demand: DemandOptions
    parameters: list[ParameterSpec]
    legacy_demand_percentile_requests_per_day: float = Field(
        description="A regular capacity within 1 % of this demand percentile is rejected: "
        "M4 never uses a demand percentile as a service capacity."
    )
    notes: list[str]


class SimulationParametersResponse(Envelope):
    data: SimulationParametersData


class ResolvedParameter(_Model):
    name: str
    value: float | int | None
    unit: str
    provenance: Literal["HYPOTHETICAL"]
    source: Literal["request", "reference_default", "removed_by_request"]
    assumption: str
    reference_assumption: str | None = Field(
        description="For a reference default: the assumption the reference configuration "
        "documents, which describes the 30-day reference with every other value at its "
        "default (horizon totals are not prorated)."
    )


class SimulationData(_Model):
    simulation_id: str = Field(
        description="SHA-256 of the canonical M4 configuration the request resolved to: equal "
        "requests give equal ids, and equal results with the same ATLAS and solver versions "
        "(provenance.atlas_version, GET /api/v1/system)."
    )
    label: str
    horizon_days: int
    first_date: date
    last_date: date
    demand_basis: str
    demand_notice: str | None
    parameters: list[ResolvedParameter]
    capacity_to_point_forecast_mean_ratio: float
    outcome: ScenarioOutcome
    plans: list[PlanVariant]
    daily_plans: list[DayPlan]
    structural_assumptions: list[str]
    limitations: list[str]


class SimulationResponse(Envelope):
    data: SimulationData
