/**
 * (M9) The dashboard in the static profile: the same views, reading the committed static
 * export under the GitHub Pages path, read-only, with the static wording and the recorded
 * reference scenarios in place of simulations. `profile.ts` is the static build's here.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { App } from './App';
import { urlOf } from './test/api';

vi.mock('./profile', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./profile')>();
  const fs = await import('node:fs');
  const path = await import('node:path');
  const manifest = JSON.parse(
    fs.readFileSync(
      path.join(import.meta.dirname, '..', 'static-export', 'atlas-static-export.json'),
      'utf-8',
    ),
  ) as { files: Record<string, { request: string; sha256: string; bytes: number }> };
  return {
    ...actual,
    STATIC: true,
    PROFILE: 'static',
    BASE: '/demand-forecasting-model/',
    STATIC_INDEX: Object.fromEntries(
      Object.entries(manifest.files).map(([file, e]) => [
        e.request,
        { file, sha256: e.sha256, bytes: e.bytes },
      ]),
    ),
    ORIGIN_TEXT: actual.STATIC_ORIGIN_TEXT,
  };
});

const BASE = '/demand-forecasting-model/';
const EXPORT = join(import.meta.dirname, '..', 'static-export');

/** The static host: the export's files under <base>data/, nothing else. */
function staticHost() {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(urlOf(input), 'http://localhost');
    expect(init?.method ?? 'GET').toBe('GET');
    if (url.pathname.startsWith(`${BASE}data/`)) {
      try {
        const body = readFileSync(join(EXPORT, url.pathname.slice(`${BASE}data/`.length)));
        return Promise.resolve(
          new Response(body, { headers: { 'Content-Type': 'application/json' } }),
        );
      } catch {
        // not a file of the export
      }
    }
    return Promise.resolve(new Response('<h1>404</h1>', { status: 404 }));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function at(path: string) {
  window.history.replaceState(null, '', `${BASE}${path}`);
  return render(<App />);
}

async function heading(name: RegExp | string, level = 1) {
  return screen.findByRole('heading', { name, level }, { timeout: 5000 });
}

function requested(fetchMock: ReturnType<typeof staticHost>): string[] {
  return fetchMock.mock.calls.map(([input]) => new URL(urlOf(input), 'http://localhost').pathname);
}

describe('the static profile', () => {
  it('labels the deployment, links under the base path and says where the numbers come from', async () => {
    const fetchMock = staticHost();
    at('');
    await heading(/NYC 311 demand: forecasts, uncertainty and hypothetical scenarios/);
    expect(screen.getByText('Static demo · read-only')).toBeInTheDocument();
    expect(screen.getByText('Research software')).toBeInTheDocument();
    expect(
      screen.getByText(/This is a static, archived copy: nothing on it is computed live/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/This static copy shows that API's recorded answers/),
    ).toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Dashboard sections' });
    expect(within(nav).getByRole('link', { name: /Forecasting/ })).toHaveAttribute(
      'href',
      `${BASE}forecasting`,
    );
    expect(screen.getByRole('link', { name: 'third-party licences' })).toHaveAttribute(
      'href',
      `${BASE}third-party-licences.txt`,
    );
    const status = (await screen.findByRole('heading', { name: 'Scientific status' })).closest(
      'section',
    )!;
    await within(status).findByText('Not significant after Holm');
    expect(within(status).getAllByText('FAILED_CALIBRATION').length).toBeGreaterThan(0);
    await waitFor(() => {
      expect(screen.queryAllByText(/The limitations could not be loaded/)).toHaveLength(0);
    });
    // Only the recorded files, never the API or the deployment descriptor.
    for (const path of requested(fetchMock))
      expect(path).toMatch(/^\/demand-forecasting-model\/data\/[a-z0-9_.-]+\.json$/);
  });

  it('moves between pages under the base path', async () => {
    staticHost();
    const user = userEvent.setup();
    at('');
    await heading(/NYC 311 demand/);
    const nav = screen.getByRole('navigation', { name: 'Dashboard sections' });
    await user.click(within(nav).getByRole('link', { name: /Decision optimisation/ }));
    await heading('Decision optimisation');
    expect(window.location.pathname).toBe(`${BASE}decisions`);
    expect(document.title).toBe('Decision optimisation · ATLAS');
  });

  it('simulator: explains why nothing can run, explores the recorded scenarios, never posts', async () => {
    const fetchMock = staticHost();
    const user = userEvent.setup();
    at('simulator');
    await heading('Hypothetical simulator');
    expect(
      screen.getByText('Interactive simulations are disabled in this deployment'),
    ).toBeInTheDocument();
    expect(screen.getByText(/with no ATLAS server behind it/)).toBeInTheDocument();
    expect(await screen.findByText('A recorded result, not a new simulation')).toBeInTheDocument();
    const select = await screen.findByLabelText('Reference scenario');
    expect(within(select).getAllByRole('option')).toHaveLength(9);
    expect(await heading(/Recorded result: Normal/, 3)).toBeInTheDocument();
    await user.selectOptions(select, 'reduced_budget');
    expect(await heading(/Recorded result: Reduced budget/, 3)).toBeInTheDocument();
    expect(window.location.search).toBe('?scenario=reduced_budget');
    expect(
      (await screen.findAllByText('DIAGNOSTIC: not a plan for the stated constraints')).length,
    ).toBeGreaterThan(0);
    const run = await screen.findByRole('button', { name: 'Run simulation' });
    expect(run).toBeDisabled();
    expect(fetchMock.mock.calls.every(([, init]) => (init?.method ?? 'GET') === 'GET')).toBe(true);
    expect(requested(fetchMock).some((path) => path.includes('simulations/'))).toBe(false);
  });

  it('simulator: an unknown scenario in the address falls back, and says so', async () => {
    staticHost();
    at('simulator?scenario=no_such');
    expect(
      await screen.findByText('There is no reference scenario named “no_such”'),
    ).toBeInTheDocument();
    expect(await heading(/Recorded result: Normal/, 3)).toBeInTheDocument();
  });

  it('provenance: no operational detail, the static build and its sources', async () => {
    const fetchMock = staticHost();
    at('provenance');
    await heading('Methods and provenance');
    expect(
      screen.getByText(/This static copy holds recorded research outputs only/),
    ).toBeInTheDocument();
    expect(screen.getByText(/static profile: files on a static host/)).toBeInTheDocument();
    expect(
      screen.getByText(
        'Each committed file behind the numbers, with the fingerprints verified when the answers were exported',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/a static host cannot send it as a header/)).toBeInTheDocument();
    expect(
      await screen.findByText('STATIC_DEMO.md on the main branch (opens GitHub)'),
    ).toBeInTheDocument();
    expect(await screen.findByText(/and was recorded from ATLAS 1\.0\.0/)).toBeInTheDocument();
    for (const path of requested(fetchMock)) expect(path.startsWith(`${BASE}data/`)).toBe(true);
  });

  it('every page keeps its limitations next to its results', async () => {
    staticHost();
    const user = userEvent.setup();
    at('forecasting');
    await heading('Forecasting');
    expect(
      await screen.findByText(/not statistically significant after the Holm adjustment/),
    ).toBeInTheDocument();
    expect((await screen.findAllByText(/^M2 SELECTION EVIDENCE: /)).length).toBeGreaterThan(0);
    expect(
      screen.getAllByText('An archived projection, not a live forecast').length,
    ).toBeGreaterThan(0);
    expect(
      (await screen.findAllByText(/^ARCHIVED PROJECTION: issued from the frozen snapshot/)).length,
    ).toBeGreaterThan(0);
    const nav = screen.getByRole('navigation', { name: 'Dashboard sections' });
    expect(
      within(nav).getByText('Simulations off here; explore recorded results'),
    ).toBeInTheDocument();
    await user.click(within(nav).getByRole('link', { name: /Uncertainty/ }));
    expect(
      await screen.findByRole('heading', { name: 'Interval status: FAILED_CALIBRATION' }),
    ).toBeInTheDocument();
    await user.click(within(nav).getByRole('link', { name: /Decision optimisation/ }));
    expect(
      await screen.findByText('HYPOTHETICAL: not a staffing, budget or capacity recommendation'),
    ).toBeInTheDocument();
    // From the recorded scenarios: the six relaxed plans, each labelled DIAGNOSTIC.
    const feasibility = await screen.findByRole('table', { name: /9 reference scenarios over/ });
    expect(within(feasibility).getAllByText('DIAGNOSTIC', { exact: true })).toHaveLength(6);
    expect(within(feasibility).getAllByText('Meets every constraint')).toHaveLength(3);
    await user.click(within(nav).getByRole('link', { name: /Overview/ }));
    expect(
      await screen.findByText(/a correctly sized selection test would most likely have kept/),
    ).toHaveTextContent(/M2 SELECTION EVIDENCE notice in the Forecasting page/);
  });

  it('a recorded file that is missing is shown as such, never replaced', async () => {
    const fetchMock = staticHost();
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = new URL(urlOf(input), 'http://localhost');
      if (url.pathname.endsWith('/models.json'))
        return Promise.resolve(new Response('missing', { status: 404 }));
      const name = url.pathname.slice(`${BASE}data/`.length);
      return Promise.resolve(
        new Response(readFileSync(join(EXPORT, name)), {
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    });
    at('forecasting');
    await heading('Forecasting');
    expect(
      (await screen.findAllByText(/A data file of this site is missing/)).length,
    ).toBeGreaterThan(0);
    // The tests panel's verdict comes from the missing file and is absent; the H1 notice
    // the forecast's own file carries stays.
    expect(screen.queryByText(/significant after the Holm adjustment/)).toBeNull();
    expect(
      (await screen.findAllByText(/\(H1\) is not statistically established/)).length,
    ).toBeGreaterThan(0);
  });
});
