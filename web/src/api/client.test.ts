import { afterEach, describe, expect, it, vi } from 'vitest';

import { apiError, FIXTURES, json, mockApi, urlOf } from '../test/api';
import { ApiError, describeError, request } from './client';
import { api } from './endpoints';
import { ForecastResponse, ModelsResponse } from './schemas';

function failing(error: unknown): Promise<never> {
  return Promise.reject(error instanceof Error ? error : new Error(String(error)));
}

async function caught(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error('expected an ApiError');
}

afterEach(() => {
  vi.useRealTimers();
});

describe('request', () => {
  it('validates a real answer and keeps the integrity block', async () => {
    mockApi();
    const forecast = await request('/api/v1/forecasts?horizon_days=30', ForecastResponse);
    expect(forecast.data.points).toHaveLength(30);
    expect(forecast.integrity.validated_recommendation).toBe(false);
    expect(forecast.integrity.uncertainty_interval_status).toBe('failed_calibration');
    expect(forecast.provenance.sources.length).toBeGreaterThan(0);
  });

  it('sends same-origin GETs without any credential header', async () => {
    const fetchMock = mockApi();
    await api.models();
    const [, init] = fetchMock.mock.calls[0] ?? [];
    expect(init?.credentials).toBe('same-origin');
    expect(init?.headers).toEqual({ Accept: 'application/json' });
    expect(JSON.stringify(init?.headers)).not.toMatch(/api-key|authorization/i);
  });

  it('refuses an answer that does not match the contract, naming the field', async () => {
    const broken = structuredClone(FIXTURES.forecast) as {
      data: { points: { prediction: unknown }[] };
    };
    broken.data.points[0]!.prediction = 'eleven thousand';
    mockApi({ '/api/v1/forecasts': () => json(broken) });
    const error = await caught(api.forecast());
    expect(error.kind).toBe('contract');
    expect(error.details[0]?.field).toBe('data.points.0.prediction');
  });

  it('refuses a non-finite number (JSON cannot carry one, but a proxy could break it)', async () => {
    const broken = structuredClone(FIXTURES.forecast) as {
      data: { accuracy_context: { mae_requests_per_day: unknown } };
    };
    broken.data.accuracy_context.mae_requests_per_day = null;
    mockApi({ '/api/v1/forecasts': () => json(broken) });
    expect((await caught(api.forecast())).kind).toBe('contract');
  });

  it.each(['validated_recommendation', 'point_forecast_validated'])(
    'refuses an answer that claims %s',
    async (flag) => {
      const forged = structuredClone(FIXTURES.models) as { integrity: Record<string, unknown> };
      forged.integrity[flag] = true;
      mockApi({ '/api/v1/models': () => json(forged) });
      const error = await caught(request('/api/v1/models', ModelsResponse));
      expect(error.kind).toBe('integrity');
      expect(describeError(error).title).toBe('Answer refused');
    },
  );

  it('refuses M4 values that are not labelled HYPOTHETICAL', async () => {
    const forged = structuredClone(FIXTURES.scenarios) as { integrity: Record<string, unknown> };
    forged.integrity.parameter_provenance = 'MEASURED';
    mockApi({ '/api/v1/scenarios': () => json(forged) });
    expect((await caught(api.scenarios())).kind).toBe('contract');
  });

  it('reads the API error body: code, message, details, request id, Retry-After', async () => {
    mockApi({
      '/api/v1/simulations': () =>
        apiError(503, 'simulations_busy', 'every simulation worker is busy', [], {
          'Retry-After': '1',
        }),
    });
    const error = await caught(api.simulate({}));
    expect(error).toMatchObject({
      kind: 'http',
      status: 503,
      code: 'simulations_busy',
      retryAfterSeconds: 1,
    });
    expect(error.requestId).toBe('req-12345678');
    expect(describeError(error).title).toBe('The server is busy');
    expect(describeError(error).detail).toContain('Retry in 1 s');
  });

  it('keeps the status of an error that is not the API error body', async () => {
    mockApi({ '/api/v1/models': () => new Response('<html>Bad gateway</html>', { status: 502 }) });
    const error = await caught(api.models());
    expect(error).toMatchObject({ kind: 'http', status: 502, code: null });
    expect(describeError(error).title).toBe('Server error');
  });

  it('reports an answer that is not JSON', async () => {
    mockApi({
      '/api/v1/models': () =>
        new Response('<html>login</html>', {
          status: 200,
          headers: { 'Content-Type': 'text/html' },
        }),
    });
    expect((await caught(api.models())).kind).toBe('contract');
  });

  it('reports a network failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => failing(new TypeError('Failed to fetch'))),
    );
    const error = await caught(api.models());
    expect(error.kind).toBe('network');
    expect(describeError(error).retryable).toBe(true);
  });

  it('times out a request that never answers', async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_input: unknown, init?: RequestInit) =>
          new Promise((_, reject) => {
            init?.signal?.addEventListener('abort', () => {
              reject(new DOMException('aborted', 'AbortError'));
            });
          }),
      ),
    );
    const pending = caught(request('/api/v1/models', ModelsResponse, { timeoutMs: 5000 }));
    await vi.advanceTimersByTimeAsync(5001);
    const error = await pending;
    expect(error.kind).toBe('timeout');
    expect(error.message).toContain('5 s');
  });

  it('distinguishes a cancellation by the caller from a timeout', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_input: unknown, init?: RequestInit) =>
          new Promise((_, reject) => {
            init?.signal?.addEventListener('abort', () => {
              reject(new DOMException('aborted', 'AbortError'));
            });
          }),
      ),
    );
    const controller = new AbortController();
    const pending = caught(api.simulate({}, { signal: controller.signal }));
    controller.abort();
    expect((await pending).kind).toBe('aborted');
  });

  it('posts a simulation as JSON with only the given fields', async () => {
    const fetchMock = mockApi();
    await api.simulate({ horizon_days: 7 });
    const [input, init] = fetchMock.mock.calls[0] ?? [];
    expect(urlOf(input!)).toBe('/api/v1/simulations');
    expect(init?.method).toBe('POST');
    expect(init?.body).toBe('{"horizon_days":7}');
    expect(init?.headers).toEqual({
      Accept: 'application/json',
      'Content-Type': 'application/json',
    });
  });

  it('never builds a path from an unchecked scenario id', () => {
    expect(() => api.scenario('../system')).toThrow(/invalid scenario id/);
    expect(() => api.scenario('normal?x=1')).toThrow(/invalid scenario id/);
  });
});

describe('describeError', () => {
  it.each([
    ['artifact_unavailable', 503, 'This component is unavailable'],
    ['not_found', 404, 'Not available in this deployment'],
    ['unauthorized', 401, 'Not authorised'],
    ['upstream_unavailable', 503, 'The API is not answering'],
    ['rate_limited', 429, 'The server is busy'],
  ])('names %s', (code, status, title) => {
    expect(describeError(new ApiError('http', 'm', '/x', { code, status })).title).toBe(title);
  });

  it('explains a contract mismatch as a release mismatch', () => {
    const { detail, retryable } = describeError(new ApiError('contract', 'bad', '/x'));
    expect(detail).toContain('different releases');
    expect(retryable).toBe(false);
  });
});
