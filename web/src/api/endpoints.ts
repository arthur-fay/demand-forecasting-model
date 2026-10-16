/**
 * Typed calls to the ATLAS API (`/api/v1`, operationIds of docs/api/openapi.json).
 */
import { request, type RequestOptions } from './client';
import {
  ComparisonResponse,
  DeploymentDescriptor,
  ForecastResponse,
  HistoryResponse,
  IntervalsResponse,
  MetricsResponse,
  ModelsResponse,
  ScenarioDetailResponse,
  ScenarioListResponse,
  SimulationParametersResponse,
  SimulationResponse,
  SystemResponse,
  UncertaintyStatusResponse,
  type SimulationRequest,
} from './schemas';

export const API = '/api/v1';
/** Served next to the dashboard by whatever serves it (the API itself, or nginx). */
export const DEPLOYMENT_DESCRIPTOR = '/atlas-deployment.json';
/**
 * The client deadline of a simulation: above every deadline the server can have, so the
 * server's own answer (504 at its deadline) always arrives first. The API accepts at most
 * 120 s (`ATLAS_API_SIMULATION_TIMEOUT_SECONDS`); the deployment at most 60 s, with nginx
 * waiting 20 s more.
 */
export const SIMULATION_TIMEOUT_MS = 150_000;

type Options = Pick<RequestOptions, 'signal' | 'timeoutMs'>;

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

export const api = {
  deployment: (o?: Options) => request(DEPLOYMENT_DESCRIPTOR, DeploymentDescriptor, o),
  /** getSystem: versions, snapshot, scientific status (operational detail: not public). */
  system: (o?: Options) => request(`${API}/system`, SystemResponse, o),
  /** listModels: the M2 models, selection and the Holm-adjusted primary tests. */
  models: (o?: Options) => request(`${API}/models`, ModelsResponse, o),
  /** getForecast: the archived 30-day M2 projection (projected). */
  forecast: (o?: Options) =>
    request(`${API}/forecasts${query({ horizon_days: 30 })}`, ForecastResponse, o),
  /** getHistory: observed daily totals in [start, end] (default: the last 180 days). */
  history: (window: { start?: string; end?: string }, o?: Options) =>
    request(`${API}/forecasts/history${query(window)}`, HistoryResponse, o),
  /** getForecastMetrics: M2 backtest accuracy by horizon bucket or horizon. */
  metrics: (
    filter: { scope?: 'bucket' | 'horizon'; period?: string; model_id?: string },
    o?: Options,
  ) => request(`${API}/forecasts/metrics${query(filter)}`, MetricsResponse, o),
  /** getUncertaintyStatus: the M3 calibration verdict. */
  uncertaintyStatus: (o?: Options) =>
    request(`${API}/uncertainty/status`, UncertaintyStatusResponse, o),
  /** getUncertaintyIntervals: marginal quantiles of the projection (failed calibration). */
  intervals: (o?: Options) =>
    request(`${API}/uncertainty/intervals${query({ horizon_days: 30 })}`, IntervalsResponse, o),
  /** listScenarios: the nine M4 reference scenarios. */
  scenarios: (o?: Options) => request(`${API}/scenarios`, ScenarioListResponse, o),
  /** compareScenarios: every scenario against the baseline (differences only between strict plans). */
  comparison: (o?: Options) => request(`${API}/scenarios/comparison`, ComparisonResponse, o),
  /** getScenario: one scenario with its daily plans. */
  scenario: (id: string, o?: Options) => {
    if (!/^[a-z][a-z0-9_]{0,63}$/.test(id))
      throw new Error(`invalid scenario id ${JSON.stringify(id)}`);
    return request(`${API}/scenarios/${id}`, ScenarioDetailResponse, o);
  },
  /** getSimulationParameters: defaults, ranges, units and assumptions of every input. */
  simulationParameters: (o?: Options) =>
    request(`${API}/simulations/parameters`, SimulationParametersResponse, o),
  /** runSimulation: one bounded HYPOTHETICAL simulation (the only computation). */
  simulate: (body: SimulationRequest, o?: Options) =>
    request(`${API}/simulations`, SimulationResponse, {
      ...o,
      method: 'POST',
      body,
      timeoutMs: o?.timeoutMs ?? SIMULATION_TIMEOUT_MS,
    }),
} as const;
