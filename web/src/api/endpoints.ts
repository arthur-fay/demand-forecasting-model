/**
 * Typed calls to the ATLAS API (`/api/v1`, operationIds of docs/api/openapi.json).
 *
 * (M9) Every view reads through `api`. In the api profile its reads are HTTP requests to
 * the API on the same origin (`client.ts`); in the static profile the same requests are
 * answered by the API's recorded answers (`static.ts`), and what needs a server (the
 * simulation, the operational metadata, the deployment descriptor) is refused or fixed.
 * Both build the same request paths here, so a recorded answer is found by the exact
 * request the api profile would send.
 */
import { BASE, STATIC, STATIC_INDEX } from '../profile';
import { ApiError, request, type RequestOptions } from './client';
import {
  ComparisonResponse,
  ForecastResponse,
  HistoryResponse,
  MetricsResponse,
  ModelsResponse,
  ScenarioDetailResponse,
  ScenarioListResponse,
  ServedDeploymentDescriptor,
  SimulationParametersResponse,
  SimulationResponse,
  SystemResponse,
  UncertaintyStatusResponse,
  IntervalsResponse,
  STATIC_DEPLOYMENT,
  type DeploymentDescriptor,
  type SimulationRequest,
} from './schemas';
import { staticReader, type StaticIndex } from './static';
import type { z } from './zod';

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

/** How the reads reach their answers: the API over HTTP, or a static export. */
export interface Transport {
  readonly profile: 'api' | 'static';
  get<S extends z.core.$ZodType>(path: string, schema: S, o?: Options): Promise<z.infer<S>>;
  deployment(o?: Options): Promise<DeploymentDescriptor>;
  simulate(body: SimulationRequest, o?: Options): Promise<z.infer<typeof SimulationResponse>>;
}

/** The api profile: same-origin HTTP (M7, unchanged). */
export const httpTransport: Transport = {
  profile: 'api',
  get: (path, schema, o) => request(path, schema, o),
  deployment: (o) => request(DEPLOYMENT_DESCRIPTOR, ServedDeploymentDescriptor, o),
  simulate: (body, o) =>
    request(`${API}/simulations`, SimulationResponse, {
      ...o,
      method: 'POST',
      body,
      timeoutMs: o?.timeoutMs ?? SIMULATION_TIMEOUT_MS,
    }),
};

/** The static profile: the recorded answers in `index`, below `base`. Never a POST. */
export function staticTransport(index: StaticIndex, base: string): Transport {
  const reader = staticReader(index, base);
  return {
    profile: 'static',
    get: (path, schema, o) => reader.read(path, schema, o),
    deployment: () => Promise.resolve(STATIC_DEPLOYMENT),
    simulate: () =>
      Promise.reject(
        new ApiError(
          'not_exported',
          'This static copy of the dashboard has no server, so the M4 engine cannot run here: no simulation was sent.',
          `${API}/simulations`,
        ),
      ),
  };
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

/** The API's operations over `transport`. */
export function createApi(transport: Transport) {
  const get: Transport['get'] = (path, schema, o) => transport.get(path, schema, o);
  return {
    profile: transport.profile,
    deployment: (o?: Options) => transport.deployment(o),
    /** getSystem: versions, snapshot, scientific status (operational detail: not public). */
    system: (o?: Options) => get(`${API}/system`, SystemResponse, o),
    /** listModels: the M2 models, selection and the Holm-adjusted primary tests. */
    models: (o?: Options) => get(`${API}/models`, ModelsResponse, o),
    /** getForecast: the archived 30-day M2 projection (projected). */
    forecast: (o?: Options) =>
      get(`${API}/forecasts${query({ horizon_days: 30 })}`, ForecastResponse, o),
    /** getHistory: observed daily totals in [start, end] (default: the last 180 days). */
    history: (window: { start?: string; end?: string }, o?: Options) =>
      get(`${API}/forecasts/history${query(window)}`, HistoryResponse, o),
    /** getForecastMetrics: M2 backtest accuracy by horizon bucket or horizon. */
    metrics: (
      filter: { scope?: 'bucket' | 'horizon'; period?: string; model_id?: string },
      o?: Options,
    ) => get(`${API}/forecasts/metrics${query(filter)}`, MetricsResponse, o),
    /** getUncertaintyStatus: the M3 calibration verdict. */
    uncertaintyStatus: (o?: Options) =>
      get(`${API}/uncertainty/status`, UncertaintyStatusResponse, o),
    /** getUncertaintyIntervals: marginal quantiles of the projection (failed calibration). */
    intervals: (o?: Options) =>
      get(`${API}/uncertainty/intervals${query({ horizon_days: 30 })}`, IntervalsResponse, o),
    /** listScenarios: the nine M4 reference scenarios. */
    scenarios: (o?: Options) => get(`${API}/scenarios`, ScenarioListResponse, o),
    /** compareScenarios: every scenario against the baseline (differences only between strict plans). */
    comparison: (o?: Options) => get(`${API}/scenarios/comparison`, ComparisonResponse, o),
    /** getScenario: one scenario with its daily plans. */
    scenario: (id: string, o?: Options) => {
      if (!/^[a-z][a-z0-9_]{0,63}$/.test(id))
        throw new Error(`invalid scenario id ${JSON.stringify(id)}`);
      return get(`${API}/scenarios/${id}`, ScenarioDetailResponse, o);
    },
    /** getSimulationParameters: defaults, ranges, units and assumptions of every input. */
    simulationParameters: (o?: Options) =>
      get(`${API}/simulations/parameters`, SimulationParametersResponse, o),
    /** runSimulation: one bounded HYPOTHETICAL simulation (the only computation). */
    simulate: (body: SimulationRequest, o?: Options) => transport.simulate(body, o),
  } as const;
}

export type Api = ReturnType<typeof createApi>;

/**
 * The build's own transport, fixed by its profile (vite.config.ts), never chosen at run
 * time. A static build without its export index fails here rather than fall back to HTTP.
 */
function buildTransport(): Transport {
  if (!STATIC) return httpTransport;
  if (STATIC_INDEX === null) throw new Error('a static build without its export index');
  return staticTransport(STATIC_INDEX, BASE);
}

export const api: Api = createApi(buildTransport());
