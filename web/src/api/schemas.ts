/**
 * Runtime validation of every API response the dashboard renders.
 *
 * The committed OpenAPI contract (`docs/api/openapi.json`) is the source of truth.
 * `openapi.gen.ts` is generated from it (`npm run gen:api`; CI requires it to be
 * current), and each schema below is checked against the generated type at compile
 * time (`Conforms`): a field declared here that the contract does not have, or has with
 * another type, does not compile. At run time the schemas validate exactly the fields
 * the dashboard uses; unknown fields are dropped (the API may add fields: they are
 * additive by contract).
 *
 * Integrity is enforced, not trusted: `validated_recommendation` and
 * `point_forecast_validated` must be the literal `false`, M4 values must say
 * `HYPOTHETICAL`. A response that claims otherwise is refused as a whole.
 */
import type { components } from './openapi.gen';
import { z } from './zod';

export type Contract = components['schemas'];

/**
 * Compile-time check: every field of `Ours` exists in the contract type `Theirs` with a
 * compatible type (`Theirs` must be assignable to `Ours`).
 */
type Conforms<Theirs extends Ours, Ours> = Theirs;

const date = z.string().check(z.regex(/^\d{4}-\d{2}-\d{2}$/));
const finite = z.number().check(z.refine(Number.isFinite, 'expected a finite number'));
const count = z.number().check(z.int(), z.minimum(0));
const intervalStatus = z.enum([
  'decision_grade',
  'failed_calibration',
  'insufficient_evidence',
  'unavailable',
]);

// --- Envelope -------------------------------------------------------------------------------

export const Integrity = z.object({
  record_type: z.string(),
  data_status: z.enum([
    'observed',
    'projected',
    'historical_evaluation',
    'calibration_assessment',
    'hypothetical_reference_run',
    'hypothetical_simulation',
    'metadata',
  ]),
  point_forecast_validated: z.literal(false),
  uncertainty_interval_status: intervalStatus,
  uncertainty_decision_grade: z.boolean(),
  parameter_provenance: z.nullable(z.literal('HYPOTHETICAL')),
  validated_recommendation: z.literal(false),
  notices: z.array(z.string()),
});
export type Integrity = z.infer<typeof Integrity>;
export type _Integrity = Conforms<Contract['Integrity'], Integrity>;

export const Source = z.object({
  kind: z.string(),
  path: z.string(),
  record_type: z.nullable(z.string()),
  run_id: z.nullable(z.string()),
  result_fingerprint: z.nullable(z.string()),
  config_fingerprint: z.nullable(z.string()),
  files: z.record(z.string(), z.string()),
});
export type Source = z.infer<typeof Source>;
export type _Source = Conforms<Contract['Source'], Source>;

export const Provenance = z.object({
  atlas_version: z.string(),
  api_version: z.string(),
  snapshot_id: z.string(),
  data_fingerprint: z.nullable(z.string()),
  sources: z.array(Source),
});
export type Provenance = z.infer<typeof Provenance>;
export type _Provenance = Conforms<Contract['Provenance'], Provenance>;

function envelope<T extends z.core.$ZodType>(data: T) {
  return z.object({ integrity: Integrity, provenance: Provenance, data });
}

/** Every data response: what the numbers are, where they come from, and the numbers. */
export interface Envelope<T> {
  readonly integrity: Integrity;
  readonly provenance: Provenance;
  readonly data: T;
}

// --- System ---------------------------------------------------------------------------------

const ScientificStatus = z.object({
  forecast_model: z.string(),
  forecast_label: z.string(),
  forecast_confirmation_mae_requests_per_day: finite,
  point_forecast_validated: z.literal(false),
  uncertainty_interval_status: intervalStatus,
  uncertainty_decision_grade: z.boolean(),
  uncertainty_statement: z.string(),
  decision_simulation_label: z.string(),
  parameter_provenance: z.literal('HYPOTHETICAL'),
  validated_recommendation: z.literal(false),
  limitations: z.array(z.string()),
});
export type _ScientificStatus = Conforms<
  Contract['ScientificStatus'],
  z.infer<typeof ScientificStatus>
>;

const SnapshotInfo = z.object({
  snapshot_id: z.string(),
  data_fingerprint: z.string(),
  first_date: date,
  last_date: date,
  n_days: count,
});
export type _SnapshotInfo = Conforms<Contract['SnapshotInfo'], z.infer<typeof SnapshotInfo>>;

const ComponentStatus = z.object({
  component: z.string(),
  ready: z.boolean(),
  detail: z.nullable(z.string()),
});

export const SystemData = z.object({
  atlas_version: z.string(),
  api_version: z.string(),
  snapshot: z.nullable(SnapshotInfo),
  components: z.array(ComponentStatus),
  scientific_status: z.nullable(ScientificStatus),
  limits: z.object({
    max_concurrent_simulations: count,
    simulation_timeout_seconds: finite,
    simulation_requires_api_key: z.boolean(),
    max_history_days: count,
    max_horizon_days: count,
  }),
  solver: z.record(z.string(), z.nullable(z.string())),
});
export type SystemData = z.infer<typeof SystemData>;
export type _SystemData = Conforms<Contract['SystemData'], SystemData>;
export const SystemResponse = envelope(SystemData);

// --- Forecasts ------------------------------------------------------------------------------

export const ObservedPoint = z.object({
  date,
  requests: z.nullable(finite),
  status: z.enum(['observed', 'missing']),
  federal_holiday: z.nullable(z.string()),
  holiday_related: z.boolean(),
});
export type ObservedPoint = z.infer<typeof ObservedPoint>;
export type _ObservedPoint = Conforms<Contract['ObservedPoint'], ObservedPoint>;

export const HistoryData = z.object({
  snapshot_id: z.string(),
  snapshot_first_date: date,
  snapshot_last_date: date,
  start: date,
  end: date,
  unit: z.literal('requests_per_day'),
  points: z.array(ObservedPoint),
});
export type HistoryData = z.infer<typeof HistoryData>;
export type _HistoryData = Conforms<Contract['HistoryData'], HistoryData>;
export const HistoryResponse = envelope(HistoryData);

export const ForecastPoint = z.object({
  target_date: date,
  horizon: count,
  prediction: finite,
  status: z.literal('projected'),
  federal_holiday: z.nullable(z.string()),
  holiday_related: z.boolean(),
});
export type ForecastPoint = z.infer<typeof ForecastPoint>;
export type _ForecastPoint = Conforms<Contract['ForecastPoint'], ForecastPoint>;

export const ForecastData = z.object({
  model_id: z.string(),
  record_type: z.literal('future_projection'),
  label: z.string(),
  origin: date,
  horizon_days: count,
  unit: z.literal('requests_per_day'),
  points: z.array(ForecastPoint),
  accuracy_context: z.object({
    model_id: z.string(),
    period: z.string(),
    bucket: z.string(),
    mae_requests_per_day: finite,
    bias_requests_per_day: finite,
    bias_sign: z.string(),
    wape_pct: finite,
    n_predictions: count,
  }),
});
export type ForecastData = z.infer<typeof ForecastData>;
export type _ForecastData = Conforms<Contract['ForecastData'], ForecastData>;
export const ForecastResponse = envelope(ForecastData);

export const MetricRow = z.object({
  model_id: z.string(),
  period: z.string(),
  scope: z.enum(['horizon', 'bucket']),
  label: z.string(),
  first_horizon: count,
  last_horizon: count,
  n: count,
  mae: z.nullable(finite),
  rmse: z.nullable(finite),
  bias: z.nullable(finite),
  bias_pct: z.nullable(finite),
  wape_pct: z.nullable(finite),
  mase: z.nullable(finite),
  mape_pct: z.nullable(finite),
});
export type MetricRow = z.infer<typeof MetricRow>;
export type _MetricRow = Conforms<Contract['MetricRow'], MetricRow>;

export const PeriodInfo = z.object({
  name: z.string(),
  role: z.string(),
  first_origin: date,
  last_origin: date,
  first_target: date,
  last_target: date,
  n_origins: count,
});
export type PeriodInfo = z.infer<typeof PeriodInfo>;

export const MetricsData = z.object({
  experiment_id: z.string(),
  label: z.string(),
  reference_model: z.string(),
  recommended_model: z.string(),
  periods: z.array(PeriodInfo),
  rows: z.array(MetricRow),
  notes: z.array(z.string()),
});
export type MetricsData = z.infer<typeof MetricsData>;
export type _MetricsData = Conforms<Contract['MetricsData'], MetricsData>;
export const MetricsResponse = envelope(MetricsData);

export const PrimaryTest = z.object({
  hypothesis: z.string(),
  model_id: z.string(),
  reference: z.string(),
  period: z.string(),
  first_horizon: count,
  last_horizon: count,
  mae: finite,
  mae_reference: finite,
  mae_difference: finite,
  mae_difference_ci_low: z.nullable(finite),
  mae_difference_ci_high: z.nullable(finite),
  holm_adjusted_p_value: z.nullable(finite),
  significant_after_holm: z.nullable(z.boolean()),
  effective_sample_size: z.nullable(finite),
});
export type PrimaryTest = z.infer<typeof PrimaryTest>;
export type _PrimaryTest = Conforms<Contract['PrimaryTest'], PrimaryTest>;

export const ModelInfo = z.object({
  model_id: z.string(),
  family: z.string(),
  model_type: z.string(),
  procedure: z.string(),
  description: z.string(),
  spec_fingerprint: z.string(),
  selected_on_development: z.boolean(),
  recommended: z.boolean(),
  mae_days_1_30_by_period: z.record(z.string(), z.nullable(finite)),
});
export type ModelInfo = z.infer<typeof ModelInfo>;
export type _ModelInfo = Conforms<Contract['ModelInfo'], ModelInfo>;

export const ModelsData = z.object({
  recommended_model: z.string(),
  recommendation_period: z.string(),
  recommendation_reason: z.string(),
  reference_model: z.string(),
  selection: z.object({
    period: z.string(),
    rule: z.nullable(z.string()),
    selected_model: z.string(),
    best_baseline: z.nullable(z.string()),
    best_regression: z.nullable(z.string()),
    margin_met: z.nullable(z.boolean()),
    significant: z.nullable(z.boolean()),
    reason: z.nullable(z.string()),
  }),
  primary_tests: z.array(PrimaryTest),
  multiplicity: z.nullable(z.string()),
  models: z.array(ModelInfo),
});
export type ModelsData = z.infer<typeof ModelsData>;
export type _ModelsData = Conforms<Contract['ModelsData'], ModelsData>;
export const ModelsResponse = envelope(ModelsData);

// --- Uncertainty ----------------------------------------------------------------------------

export const IntervalLevelEvidence = z.object({
  level: finite,
  coverage: z.nullable(finite),
  coverage_ci_low: z.nullable(finite),
  coverage_ci_high: z.nullable(finite),
  effective_sample_size: z.nullable(finite),
  evidence_sufficient: z.nullable(z.boolean()),
  rejected: z.nullable(z.boolean()),
  tolerance_pct: z.nullable(finite),
  scopes_outside_tolerance: z.array(z.string()),
});
export type IntervalLevelEvidence = z.infer<typeof IntervalLevelEvidence>;
export type _IntervalLevelEvidence = Conforms<
  Contract['IntervalLevelEvidence'],
  IntervalLevelEvidence
>;

export const UncertaintyStatusData = z.object({
  interval_status: intervalStatus,
  decision_grade: z.boolean(),
  statement: z.string(),
  note: z.nullable(z.string()),
  evaluation_period: z.string(),
  primary_model: z.string(),
  selected_pair: z.string(),
  failure_reasons: z.array(z.string()),
  interval_levels: z.array(IntervalLevelEvidence),
  exceedance_status: z.record(z.string(), z.string()),
  exceedance_reasons: z.record(z.string(), z.array(z.string())),
  evaluation_share_at_or_below_quantile_days_1_30: z.record(z.string(), finite),
});
export type UncertaintyStatusData = z.infer<typeof UncertaintyStatusData>;
export type _UncertaintyStatusData = Conforms<
  Contract['UncertaintyStatusData'],
  UncertaintyStatusData
>;
export const UncertaintyStatusResponse = envelope(UncertaintyStatusData);

export const IntervalPoint = z.object({
  target_date: date,
  horizon: count,
  prediction: finite,
  quantiles: z.record(z.string(), finite),
  status: z.literal('projected'),
  holiday_related: z.boolean(),
});
export type IntervalPoint = z.infer<typeof IntervalPoint>;
export type _IntervalPoint = Conforms<Contract['IntervalPoint'], IntervalPoint>;

export const IntervalsData = z.object({
  model_id: z.string(),
  pair_id: z.string(),
  interval_status: intervalStatus,
  label: z.string(),
  origin: date,
  points: z.array(IntervalPoint),
});
export type IntervalsData = z.infer<typeof IntervalsData>;
export type _IntervalsData = Conforms<Contract['IntervalsData'], IntervalsData>;
export const IntervalsResponse = envelope(IntervalsData);

// --- Scenarios (M4) -------------------------------------------------------------------------

const feasibility = z.enum(['strict', 'relaxed_terminal_target']);

export const ScenarioSummary = z.object({
  scenario_id: z.string(),
  description: z.string(),
  demand_basis: z.string(),
  feasibility,
  plan_label: z.string(),
  discretionary_cost: z.nullable(finite),
  backlog_request_days: z.nullable(finite),
  terminal_backlog: z.nullable(finite),
  peak_backlog: z.nullable(finite),
  overtime_hours: z.nullable(finite),
  surge_requests: z.nullable(finite),
  optimal_plan_unique: z.nullable(z.boolean()),
  unique_after_tie_breaks: z.nullable(z.boolean()),
  binding_constraints: z.array(z.string()),
  diagnosis_reasons: z.array(z.string()),
});
export type ScenarioSummary = z.infer<typeof ScenarioSummary>;
export type _ScenarioSummary = Conforms<Contract['ScenarioSummary'], ScenarioSummary>;

export const ScenarioListData = z.object({
  run_id: z.string(),
  result_fingerprint: z.string(),
  config_fingerprint: z.string(),
  label: z.string(),
  horizon_days: count,
  first_date: date,
  last_date: date,
  scenarios: z.array(ScenarioSummary),
});
export type ScenarioListData = z.infer<typeof ScenarioListData>;
export type _ScenarioListData = Conforms<Contract['ScenarioListData'], ScenarioListData>;
export const ScenarioListResponse = envelope(ScenarioListData);

const HypotheticalQuantity = z.object({
  value: finite,
  unit: z.string(),
  provenance: z.literal('HYPOTHETICAL'),
  assumption: z.string(),
});

export const ComparisonRow = z.object({
  scenario_id: z.string(),
  description: z.string(),
  adjustments: z.record(z.string(), HypotheticalQuantity),
  feasibility,
  values_status: z.enum(['plan_meets_every_constraint', 'DIAGNOSTIC']),
  terminal_backlog_max: z.nullable(finite),
  terminal_violation: z.nullable(finite),
  values: z.record(z.string(), z.nullable(finite)),
  difference_from_baseline: z.record(z.string(), z.nullable(finite)),
});
export type ComparisonRow = z.infer<typeof ComparisonRow>;
export type _ComparisonRow = Conforms<Contract['ComparisonRow'], ComparisonRow>;

export const ComparisonData = z.object({
  baseline_scenario_id: z.string(),
  metrics: z.array(
    z.enum([
      'discretionary_cost',
      'backlog_request_days',
      'terminal_backlog',
      'peak_backlog',
      'overtime_hours',
      'surge_requests',
      'penalised_score',
    ]),
  ),
  units: z.record(z.string(), z.string()),
  rows: z.array(ComparisonRow),
  caveat: z.string(),
});
export type ComparisonData = z.infer<typeof ComparisonData>;
export type _ComparisonData = Conforms<Contract['ComparisonData'], ComparisonData>;
export const ComparisonResponse = envelope(ComparisonData);

export const DayPlan = z.object({
  plan: z.string(),
  day: count,
  date,
  demand: finite,
  work_available: finite,
  regular_capacity: finite,
  overtime_hours: finite,
  overtime_capacity: finite,
  surge_requests: finite,
  served: finite,
  backlog: finite,
  extra_capacity: finite,
});
export type DayPlan = z.infer<typeof DayPlan>;
export type _DayPlan = Conforms<Contract['DayPlan'], DayPlan>;

const PlanEvaluation = z.object({
  regular_cost: finite,
  overtime_cost: finite,
  surge_cost: finite,
  discretionary_cost: finite,
  operating_cost: finite,
  backlog_penalty: finite,
  terminal_penalty: finite,
  penalised_score: finite,
  total_demand: finite,
  initial_backlog: finite,
  total_served: finite,
  backlog_request_days: finite,
  terminal_backlog: finite,
  peak_backlog: finite,
  days_with_backlog: count,
  overtime_hours: finite,
  surge_requests: finite,
  idle_paid_capacity: finite,
  discretionary_budget: z.nullable(finite),
  budget_remaining: z.nullable(finite),
});
export type PlanEvaluation = z.infer<typeof PlanEvaluation>;
export type _PlanEvaluation = Conforms<Contract['PlanEvaluation'], PlanEvaluation>;

const Programme = z.object({
  status: z.string(),
  certificate: z.nullable(
    z.object({ holds: z.boolean(), duality_gap: z.nullable(finite), primal: z.nullable(finite) }),
  ),
  independent_check_passed: z.nullable(z.boolean()),
  evaluation: z.nullable(PlanEvaluation),
});
export type Programme = z.infer<typeof Programme>;
export type _Programme = Conforms<Contract['Programme'], Programme>;

export const PolicyComparison = z.object({
  policy: z.enum(['fixed_capacity', 'backlog_trigger']),
  reported_plan: z.string(),
  policy_constraints_violated: z.array(z.string()),
  comparable_on_score: z.boolean(),
  relation_of_reported_plan: z.enum(['dominates', 'dominated', 'tradeoff', 'equal']),
  discretionary_cost_difference: finite,
  request_days_difference: finite,
  terminal_backlog_difference: finite,
  request_days_difference_at_same_cash: z.nullable(finite),
  cash_difference_at_same_backlog: z.nullable(finite),
});
export type PolicyComparison = z.infer<typeof PolicyComparison>;
export type _PolicyComparison = Conforms<Contract['PolicyComparison'], PolicyComparison>;

export const Diagnosis = z.object({
  label: z.string(),
  reasons: z.array(z.string()),
  diagnostic_min_discretionary_spend_to_reach_hypothetical_target_usd: z.nullable(finite),
  diagnostic_budget_shortfall_usd: z.nullable(finite),
  least_terminal_violation_unlimited_discretionary_cash_requests: z.nullable(finite),
  least_terminal_violation_within_budget_requests: z.nullable(finite),
  relaxed_plan: z.nullable(z.string()),
});
export type Diagnosis = z.infer<typeof Diagnosis>;
export type _Diagnosis = Conforms<Contract['Diagnosis'], Diagnosis>;

export const ScenarioOutcome = z.object({
  scenario_id: z.string(),
  description: z.string(),
  demand_basis: z.string(),
  quantile: z.nullable(finite),
  adjustments: z.record(z.string(), HypotheticalQuantity),
  demand_total_requests: finite,
  demand_total_vs_point_forecast_pct: finite,
  regular_capacity_mean_requests_per_day: finite,
  discretionary_budget_usd: z.nullable(finite),
  feasibility,
  reported_plan: z.nullable(Programme),
  reported_plan_selection: z.string(),
  diagnosis: z.nullable(Diagnosis),
  binding_constraints: z.array(z.string()),
  comparisons: z.array(PolicyComparison),
  problems: z.array(z.string()),
});
export type ScenarioOutcome = z.infer<typeof ScenarioOutcome>;
export type _ScenarioOutcome = Conforms<Contract['ScenarioOutcome'], ScenarioOutcome>;

const PlanTotals = z.object({
  discretionary_cost: finite,
  overtime_cost: finite,
  surge_cost: finite,
  regular_cost: finite,
  backlog_request_days: finite,
  terminal_backlog: finite,
  peak_backlog: finite,
  overtime_hours: finite,
  surge_requests: finite,
  total_served: finite,
});

export const PlanVariant = z.object({
  plan: z.string(),
  feasibility: z.string(),
  terminal_backlog_max: z.nullable(finite),
  terminal_violation: z.nullable(finite),
  satisfies_budget: z.nullable(z.boolean()),
  unique_optimal_plan: z.nullable(z.boolean()),
  unique_after_tie_breaks: z.nullable(z.boolean()),
  totals: z.nullable(PlanTotals),
});
export type PlanVariant = z.infer<typeof PlanVariant>;
export type _PlanVariant = Conforms<Contract['PlanVariant'], PlanVariant>;

export const HypotheticalParameter = z.object({
  scope: z.string(),
  parameter: z.string(),
  value: z.union([finite, z.string()]),
  unit: z.string(),
  provenance: z.literal('HYPOTHETICAL'),
  assumption: z.string(),
});
export type HypotheticalParameter = z.infer<typeof HypotheticalParameter>;
export type _HypotheticalParameter = Conforms<
  Contract['HypotheticalParameter'],
  HypotheticalParameter
>;

export const ScenarioDetailData = z.object({
  run_id: z.string(),
  label: z.string(),
  outcome: ScenarioOutcome,
  plans: z.array(PlanVariant),
  daily_plans: z.array(DayPlan),
  hypothetical_parameters: z.array(HypotheticalParameter),
  structural_assumptions: z.array(z.string()),
});
export type ScenarioDetailData = z.infer<typeof ScenarioDetailData>;
export type _ScenarioDetailData = Conforms<Contract['ScenarioDetailData'], ScenarioDetailData>;
export const ScenarioDetailResponse = envelope(ScenarioDetailData);

// --- Simulations ----------------------------------------------------------------------------

const RangeSpec = z.object({ default: finite, minimum: finite, maximum: finite, unit: z.string() });

export const ParameterSpec = z.object({
  name: z.string(),
  unit: z.string(),
  default: z.nullable(finite),
  minimum: finite,
  maximum: finite,
  nullable: z.boolean(),
  default_assumption: z.string(),
});
export type ParameterSpec = z.infer<typeof ParameterSpec>;
export type _ParameterSpec = Conforms<Contract['ParameterSpec'], ParameterSpec>;

export const SimulationParametersData = z.object({
  horizon_days: z.object({ default: count, minimum: count, maximum: count, first_date: date }),
  demand: z.object({
    basis: z.array(z.enum(['point_forecast', 'm3_marginal_quantile'])),
    quantile: z.array(finite),
    multiplier: RangeSpec,
    shift_per_day: RangeSpec,
    notice: z.string(),
  }),
  parameters: z.array(ParameterSpec),
  legacy_demand_percentile_requests_per_day: finite,
  notes: z.array(z.string()),
});
export type SimulationParametersData = z.infer<typeof SimulationParametersData>;
export type _SimulationParametersData = Conforms<
  Contract['SimulationParametersData'],
  SimulationParametersData
>;
export const SimulationParametersResponse = envelope(SimulationParametersData);

export const ResolvedParameter = z.object({
  name: z.string(),
  value: z.nullable(finite),
  unit: z.string(),
  provenance: z.literal('HYPOTHETICAL'),
  source: z.enum(['request', 'reference_default', 'removed_by_request']),
  assumption: z.string(),
  reference_assumption: z.nullable(z.string()),
});
export type ResolvedParameter = z.infer<typeof ResolvedParameter>;
export type _ResolvedParameter = Conforms<Contract['ResolvedParameter'], ResolvedParameter>;

export const SimulationData = z.object({
  simulation_id: z.string(),
  label: z.string(),
  horizon_days: count,
  first_date: date,
  last_date: date,
  demand_basis: z.string(),
  demand_notice: z.nullable(z.string()),
  parameters: z.array(ResolvedParameter),
  capacity_to_point_forecast_mean_ratio: finite,
  outcome: ScenarioOutcome,
  plans: z.array(PlanVariant),
  daily_plans: z.array(DayPlan),
  structural_assumptions: z.array(z.string()),
  limitations: z.array(z.string()),
});
export type SimulationData = z.infer<typeof SimulationData>;
export type _SimulationData = Conforms<Contract['SimulationData'], SimulationData>;
export const SimulationResponse = envelope(SimulationData);

/** The simulation request body (what the dashboard sends; validated by the API). */
export type SimulationRequest = Partial<Contract['SimulationRequest']>;
export type SimulationParameterName = keyof Contract['SimulationParameters'];

// --- Errors ---------------------------------------------------------------------------------

export const ErrorResponse = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    request_id: z.nullable(z.string()),
    details: z.array(z.object({ field: z.string(), message: z.string() })),
  }),
});
export type ErrorResponse = z.infer<typeof ErrorResponse>;

// --- The deployment descriptor (served next to the dashboard, not part of the API) -----------

export const DeploymentDescriptor = z.object({
  format: z.literal('atlas-deployment/1'),
  /** local: the API serves the dashboard itself (development, SSH tunnel). */
  profile: z.enum(['local', 'private', 'public-demo']),
  /** Whether this deployment lets a browser run simulations, and if not, why not. */
  simulations: z.enum(['enabled', 'disabled_public_demo', 'disabled_api_key']),
  /** Whether /api/v1/system and /health/ready are reachable (operational detail). */
  operational_detail: z.boolean(),
});
export type DeploymentDescriptor = z.infer<typeof DeploymentDescriptor>;
