/**
 * (M9) The static profile's reads: only recorded answers, only after their SHA-256, through
 * the same checks as an API answer; never a POST. And the committed export holds an answer
 * for every request the static pages can make.
 */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import { addDays } from '../lib/format';
import { ApiError, describeError } from './client';
import { createApi, staticTransport } from './endpoints';
import { ModelsResponse, STATIC_DEPLOYMENT } from './schemas';
import { staticReader, type StaticIndex } from './static';

const BASE = '/demand-forecasting-model/';
const EXPORT = join(import.meta.dirname, '..', '..', 'static-export');

interface ExportManifest {
  readonly files: Record<string, { request: string; sha256: string; bytes: number }>;
}

const manifest = JSON.parse(
  readFileSync(join(EXPORT, 'atlas-static-export.json'), 'utf-8'),
) as ExportManifest;
const INDEX: StaticIndex = Object.fromEntries(
  Object.entries(manifest.files).map(([file, e]) => [
    e.request,
    { file, sha256: e.sha256, bytes: e.bytes },
  ]),
);
const exported = (file: string) => readFileSync(join(EXPORT, file));
const exportedOrNothing = (file: string) => {
  try {
    return exported(file);
  } catch {
    return undefined;
  }
};

function serve(files: Record<string, string> = {}, status = 200) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    expect(init?.method ?? 'GET').toBe('GET');
    const name = url.startsWith(`${BASE}data/`) ? url.slice(`${BASE}data/`.length) : null;
    const body = name === null ? undefined : (files[name] ?? exportedOrNothing(name));
    return Promise.resolve(
      body === undefined
        ? new Response('<h1>404</h1>', { status: 404, headers: { 'Content-Type': 'text/html' } })
        : new Response(body, { status, headers: { 'Content-Type': 'application/json' } }),
    );
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

async function refusal(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  }
  throw new Error('the read was not refused');
}

const reader = staticReader(INDEX, BASE);

describe('a static read', () => {
  it('returns the recorded answer, fetched from the data directory, after its SHA-256', async () => {
    const fetchMock = serve();
    const answer = await reader.read('/api/v1/models', ModelsResponse);
    expect(answer.data.recommended_model).toBe('atlas_linear_regression');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe(`${BASE}data/models.json`);
    expect(init).toMatchObject({ credentials: 'omit', redirect: 'error', cache: 'no-cache' });
  });

  it('refuses a request with no recorded answer, without fetching anything', async () => {
    const fetchMock = serve();
    const error = await refusal(reader.read('/api/v1/system', ModelsResponse));
    expect(error.kind).toBe('not_exported');
    expect(describeError(error)).toMatchObject({
      title: 'Not part of this static demonstration',
      retryable: false,
    });
    // An inherited property is never an entry.
    expect((await refusal(reader.read('constructor', ModelsResponse))).kind).toBe('not_exported');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('refuses a file whose bytes differ from the build record, whatever it contains', async () => {
    const original = exported('models.json').toString('utf-8');
    const altered = original.replace(
      '"significant_after_holm":false',
      '"significant_after_holm":true',
    );
    expect(altered).not.toBe(original);
    serve({ 'models.json': altered });
    const error = await refusal(reader.read('/api/v1/models', ModelsResponse));
    expect(error.kind).toBe('integrity');
    expect(error.message).toMatch(/is not the one this build recorded/);
    // Same size, other bytes.
    serve({
      'models.json': original.replace('atlas_linear_regression', 'atlas_linear_regressioN'),
    });
    expect((await refusal(reader.read('/api/v1/models', ModelsResponse))).kind).toBe('integrity');
  });

  it('applies the API checks to a file that matches its record', async () => {
    // A recorded answer claiming a validated recommendation, with a matching index entry.
    const claims = exported('models.json')
      .toString('utf-8')
      .replace('"validated_recommendation":false', '"validated_recommendation":true');
    const contract = JSON.stringify({ integrity: {}, provenance: {}, data: {} });
    const notJson = '{"not": json';
    const entry = (name: string, body: string) => ({
      file: name,
      sha256: createHash('sha256').update(body).digest('hex'),
      bytes: Buffer.byteLength(body),
    });
    const index: StaticIndex = {
      '/claims': entry('claims.json', claims),
      '/contract': entry('contract.json', contract),
      '/not-json': entry('not-json.json', notJson),
    };
    serve({ 'claims.json': claims, 'contract.json': contract, 'not-json.json': notJson });
    const local = staticReader(index, BASE);
    expect((await refusal(local.read('/claims', ModelsResponse))).kind).toBe('integrity');
    const broken = await refusal(local.read('/contract', ModelsResponse));
    expect(broken.kind).toBe('contract');
    expect(broken.details.length).toBeGreaterThan(0);
    expect((await refusal(local.read('/not-json', ModelsResponse))).kind).toBe('contract');
  });

  it('reports a missing file as missing, with a reload to retry', async () => {
    serve({}, 200);
    const index: StaticIndex = { '/gone': { file: 'gone.json', sha256: '0'.repeat(64), bytes: 1 } };
    const error = await refusal(staticReader(index, BASE).read('/gone', ModelsResponse));
    expect(error).toMatchObject({ kind: 'http', status: 404, code: 'static_file_missing' });
    expect(describeError(error)).toMatchObject({
      title: 'A data file of this site is missing',
      retryable: true,
    });
  });

  it('reports a network failure, a timeout and a cancellation by kind', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('offline'))),
    );
    expect((await refusal(reader.read('/api/v1/models', ModelsResponse))).kind).toBe('network');

    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_input: RequestInfo | URL, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            // As fetch does: an aborted signal rejects at once, a later abort when it comes.
            const abort = () => {
              reject(new DOMException('aborted', 'AbortError'));
            };
            if (init?.signal?.aborted) abort();
            init?.signal?.addEventListener('abort', abort);
          }),
      ),
    );
    const late = await refusal(reader.read('/api/v1/models', ModelsResponse, { timeoutMs: 10 }));
    expect(late.kind).toBe('timeout');
    const controller = new AbortController();
    const pending = reader.read('/api/v1/models', ModelsResponse, { signal: controller.signal });
    controller.abort();
    expect((await refusal(pending)).kind).toBe('aborted');
    const already = new AbortController();
    already.abort();
    const cancelled = reader.read('/api/v1/models', ModelsResponse, { signal: already.signal });
    expect((await refusal(cancelled)).kind).toBe('aborted');
  });

  it('shows nothing where the browser cannot check a file (no Web Crypto)', async () => {
    serve();
    vi.stubGlobal('crypto', {});
    const error = await refusal(reader.read('/api/v1/models', ModelsResponse));
    expect(error.kind).toBe('integrity');
    expect(error.message).toMatch(/Web Crypto/);
  });
});

describe('the static transport', () => {
  it('never sends a simulation, and fixes its deployment as read-only', async () => {
    const fetchMock = serve();
    const api = createApi(staticTransport(INDEX, BASE));
    expect(api.profile).toBe('static');
    const error = await refusal(api.simulate({ horizon_days: 7 }));
    expect(error.kind).toBe('not_exported');
    expect(fetchMock).not.toHaveBeenCalled();
    await expect(api.deployment()).resolves.toEqual(STATIC_DEPLOYMENT);
    expect(STATIC_DEPLOYMENT).toMatchObject({
      simulations: 'disabled_static_site',
      operational_detail: false,
    });
    expect((await refusal(api.system())).kind).toBe('not_exported');
  });
});

describe('the committed export', () => {
  const source = (path: string) =>
    readFileSync(join(import.meta.dirname, '..', 'views', path), 'utf-8');

  it('answers every request the static pages can make', async () => {
    serve();
    const api = createApi(staticTransport(INDEX, BASE));
    const history = await api.history({});
    const { snapshot_first_date: first, snapshot_last_date: last } = history.data;
    // The Forecasting page's windows and periods, as its source defines them.
    const forecasting = source('Forecasting.tsx');
    const windows = [...forecasting.matchAll(/\{ value: '(\w+)', label: '[^']+' \}/g)].map(
      (m) => m[1]!,
    );
    expect(windows).toEqual([
      '90',
      '180',
      '365',
      'all',
      'confirmation',
      'development',
      'legacy_window',
      'mae',
      'rmse',
      'bias',
    ]);
    for (const window of ['90', '365', 'all'])
      await api.history({
        start: window === 'all' ? first : addDays(last, -(Number(window) - 1)),
      });
    for (const period of ['confirmation', 'development', 'legacy_window'])
      await api.metrics({ scope: 'horizon', period });
    await api.metrics({ scope: 'bucket' });
    await api.models();
    await api.forecast();
    await api.uncertaintyStatus();
    await api.intervals();
    await api.comparison();
    await api.simulationParameters();
    const scenarios = await api.scenarios();
    expect(scenarios.data.scenarios).toHaveLength(9);
    for (const scenario of scenarios.data.scenarios) await api.scenario(scenario.scenario_id);
    // ... and nothing the pages cannot ask for.
    // 9 fixed requests, 3 more history windows, 3 periods by horizon, 9 scenarios.
    expect(Object.keys(INDEX)).toHaveLength(9 + 3 + 3 + 9);
  });

  it('records the scientific status the pages must show', () => {
    const status = (
      JSON.parse(readFileSync(join(EXPORT, 'atlas-static-export.json'), 'utf-8')) as {
        scientific_status: Record<string, unknown>;
      }
    ).scientific_status;
    expect(status).toMatchObject({
      point_forecast_validated: false,
      validated_recommendation: false,
      uncertainty_interval_status: 'failed_calibration',
      uncertainty_decision_grade: false,
      m2_h1_significant_after_holm: false,
      m2_selection_evidence_notice: true,
      projection_record_type: 'future_projection',
      projection_archived_notice: true,
      m4_parameter_provenance: 'HYPOTHETICAL',
    });
  });
});
