/**
 * A fake same-origin API for component tests, answering with the API's real responses
 * (src/test/fixtures, kept byte-identical to the API by tests/test_m7_fixtures.py).
 */
import { vi } from 'vitest';

import comparison from './fixtures/comparison.json';
import forecast from './fixtures/forecast.json';
import history from './fixtures/history.json';
import intervals from './fixtures/intervals.json';
import metricsBucket from './fixtures/metrics-bucket.json';
import metricsHorizon from './fixtures/metrics-horizon-confirmation.json';
import models from './fixtures/models.json';
import scenarioNormal from './fixtures/scenario-normal.json';
import scenarioReduced from './fixtures/scenario-reduced_budget.json';
import scenarios from './fixtures/scenarios.json';
import simulation from './fixtures/simulation-7d-budget.json';
import parameters from './fixtures/simulation-parameters.json';
import system from './fixtures/system.json';
import uncertaintyStatus from './fixtures/uncertainty-status.json';

export const FIXTURES = {
  comparison,
  forecast,
  history,
  intervals,
  metricsBucket,
  metricsHorizon,
  models,
  scenarioNormal,
  scenarioReduced,
  scenarios,
  simulation,
  parameters,
  system,
  uncertaintyStatus,
};

export const DESCRIPTORS = {
  local: {
    format: 'atlas-deployment/1',
    profile: 'local',
    simulations: 'enabled',
    operational_detail: true,
  },
  localWithKey: {
    format: 'atlas-deployment/1',
    profile: 'local',
    simulations: 'disabled_api_key',
    operational_detail: true,
  },
  private: {
    format: 'atlas-deployment/1',
    profile: 'private',
    simulations: 'enabled',
    operational_detail: true,
  },
  publicDemo: {
    format: 'atlas-deployment/1',
    profile: 'public-demo',
    simulations: 'disabled_public_demo',
    operational_detail: false,
  },
} as const;

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json',
      'X-Request-ID': 'test-request-0001',
      ...headers,
    },
  });
}

export function apiError(
  status: number,
  code: string,
  message: string,
  details: { field: string; message: string }[] = [],
  headers: Record<string, string> = {},
): Response {
  return json({ error: { code, message, request_id: 'req-12345678', details } }, status, headers);
}

/** The URL a fetch call asked for, whatever form its input took. */
export function urlOf(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  return input instanceof URL ? input.href : input.url;
}

export type Handler = (url: URL, init: RequestInit | undefined) => Response | Promise<Response>;

/** Routes by "path?query" first, then by path. */
const ROUTES: Record<string, () => unknown> = {
  '/api/v1/system': () => FIXTURES.system,
  '/api/v1/models': () => FIXTURES.models,
  '/api/v1/forecasts': () => FIXTURES.forecast,
  '/api/v1/forecasts/history': () => FIXTURES.history,
  '/api/v1/forecasts/metrics?scope=bucket': () => FIXTURES.metricsBucket,
  '/api/v1/forecasts/metrics': () => FIXTURES.metricsHorizon,
  '/api/v1/uncertainty/status': () => FIXTURES.uncertaintyStatus,
  '/api/v1/uncertainty/intervals': () => FIXTURES.intervals,
  '/api/v1/scenarios': () => FIXTURES.scenarios,
  '/api/v1/scenarios/comparison': () => FIXTURES.comparison,
  '/api/v1/scenarios/normal': () => FIXTURES.scenarioNormal,
  '/api/v1/scenarios/reduced_budget': () => FIXTURES.scenarioReduced,
  '/api/v1/simulations/parameters': () => FIXTURES.parameters,
  '/api/v1/simulations': () => FIXTURES.simulation,
};

/**
 * Stub `fetch` with the fixtures; `overrides` replace any route ("path" or "path?query").
 * Returns the mock, whose calls record every request.
 */
export function mockApi(
  overrides: Record<string, Handler> = {},
  descriptor: unknown = DESCRIPTORS.local,
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(urlOf(input), 'http://localhost');
    const full = `${url.pathname}${url.search}`;
    const override = overrides[full] ?? overrides[url.pathname];
    if (override) return override(url, init);
    if (url.pathname === '/atlas-deployment.json') return json(descriptor);
    const route = ROUTES[full] ?? ROUTES[url.pathname];
    if (route) return json(route());
    return apiError(404, 'not_found', 'no such resource');
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** The parsed JSON body of the n-th call to `path` (for POST assertions). */
export function sentBody(fetchMock: ReturnType<typeof mockApi>, path: string): unknown[] {
  return fetchMock.mock.calls
    .filter(([input]) => new URL(urlOf(input), 'http://localhost').pathname === path)
    .map(([, init]) =>
      typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined,
    );
}
