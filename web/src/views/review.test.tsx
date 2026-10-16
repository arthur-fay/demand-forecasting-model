/**
 * What the independent review of M7 found, kept fixed: each test fails on the defect it
 * names (see docs/DASHBOARD.md section 16).
 */
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { SIMULATION_TIMEOUT_MS } from '../api/endpoints';
import { App } from '../App';
import { apiError, DESCRIPTORS, FIXTURES, json, mockApi, type Handler } from '../test/api';

function at(path: string) {
  window.history.replaceState(null, '', path);
  return render(<App />);
}

/** A deep copy of a fixture, to change one field of it. */
function copy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/** An answer that never comes: the request ends only when the client aborts it. */
const unanswered: Handler = (_url, init) =>
  new Promise((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => {
      reject(new DOMException('aborted', 'AbortError'));
    });
  });

/** What the live regions of the page say, as a screen reader hears it (aria-hidden left out). */
function spoken(): string[] {
  const regions = document.querySelectorAll(
    '[aria-live]:not([aria-live="off"]), [role="status"], [role="alert"], [role="log"]',
  );
  return [...regions].map((region) => {
    const clone = region.cloneNode(true) as Element;
    for (const hidden of clone.querySelectorAll('[aria-hidden="true"]')) hidden.remove();
    return clone.textContent;
  });
}

describe('M2: the significance shown is H1, found by name', () => {
  it('is not taken from the first test the API happens to list', async () => {
    const models = copy(FIXTURES.models);
    models.data.primary_tests.reverse(); // H2 (significant) first
    mockApi({ '/api/v1/models': () => json(models) });
    at('/');
    const tile = (
      await screen.findByText('MAE difference, model minus best baseline (H1)')
    ).closest('.stat') as HTMLElement;
    expect(within(tile).getByText('Not significant after Holm')).toBeInTheDocument();
    expect(within(tile).queryByText('Significant after Holm')).not.toBeInTheDocument();
  });

  it('keeps each model with its own role when one model holds two', async () => {
    const models = copy(FIXTURES.models);
    models.data.selection.best_baseline = models.data.recommended_model;
    mockApi({ '/api/v1/models': () => json(models) });
    at('/forecasting');
    await screen.findByRole('slider', { name: /mean absolute error/ }, { timeout: 5000 });
    expect(screen.getAllByText(/\(recommended, best baseline\)/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Seasonal naive.*\(reference\)/).length).toBeGreaterThan(0);
  });
});

describe('M4: plans are described as the API describes them', () => {
  it('names a plan that is one of several equally scored', async () => {
    mockApi();
    at('/decisions');
    const table = await screen.findByRole('table', { name: /reference scenarios over/ });
    const row = within(table)
      .getByRole('rowheader', { name: /Increased costs/ })
      .closest('tr');
    expect(within(row as HTMLElement).getByText('One of several equally scored')).toBeVisible();
  });

  it('labels the policy comparison of a DIAGNOSTIC plan', async () => {
    mockApi();
    at('/decisions?scenario=reduced_budget');
    expect(
      await screen.findByText(/Against simple policies.*\(the reported plan is DIAGNOSTIC\)/),
    ).toBeInTheDocument();
    expect(screen.getAllByRole('columnheader', { name: 'Comparable on score' }).length).toBe(1);
  });
});

describe('the simulator', () => {
  it('never shows a contract error as a mistake in the inputs', async () => {
    mockApi({ '/api/v1/simulations': () => json({ data: {} }) });
    const user = userEvent.setup();
    at('/simulator');
    await user.click(await screen.findByRole('button', { name: 'Run simulation' }));
    expect(await screen.findByText(/Unexpected answer from the API/)).toBeInTheDocument();
    expect(screen.queryByText(/needs? attention/)).not.toBeInTheDocument();
  });

  it('marks a result once the inputs change, and keeps the API caveat on reference values', async () => {
    mockApi();
    const user = userEvent.setup();
    at('/simulator');
    await user.click(await screen.findByRole('button', { name: 'Run simulation' }));
    await screen.findByRole('heading', { name: /Simulation result/ });
    expect(screen.queryByText(/The inputs have changed/)).not.toBeInTheDocument();
    await user.click(screen.getByText(/Every input of this run/));
    expect(screen.getByText(/may not hold for this simulation/)).toBeInTheDocument();
    const horizon = screen.getByRole('textbox', { name: /Planning horizon/ });
    await user.clear(horizon);
    await user.type(horizon, '12');
    expect(screen.getByText(/The inputs have changed since this result/)).toBeInTheDocument();
  });

  it('waits past every server deadline, then says it stopped waiting without blaming the server', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mockApi({ '/api/v1/simulations': unanswered });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime.bind(vi) });
      at('/simulator');
      await user.click(await screen.findByRole('button', { name: 'Run simulation' }));
      // The longest deadline the API accepts (ATLAS_API_SIMULATION_TIMEOUT_SECONDS, 120 s;
      // the deployment's is shorter): the dashboard still waits, so the server's own answer
      // (504 at its deadline) arrives first.
      const longest = 120_000;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(longest + 1000);
      });
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
      expect(screen.queryByText(/No answer arrived in time/)).not.toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SIMULATION_TIMEOUT_MS - longest);
      });
      expect(await screen.findByText(/No answer arrived in time/)).toBeInTheDocument();
      expect(screen.queryByText(/server stopped the solve/)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('announces a run once, not each tick of its counter', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mockApi({ '/api/v1/simulations': unanswered });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime.bind(vi) });
      at('/simulator');
      await user.click(await screen.findByRole('button', { name: 'Run simulation' }));
      const counter = () => screen.getByText(/Solving on the server… [\d.]+ s/).textContent;
      const later = async () => {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1000);
        });
      };
      await later();
      const [shown, heard] = [counter(), spoken()];
      expect(heard).toContain('Running the simulation on the server.');
      await later();
      expect(counter()).not.toBe(shown);
      expect(spoken()).toEqual(heard);
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows the reason first when this deployment cannot simulate', async () => {
    mockApi({}, DESCRIPTORS.publicDemo);
    at('/simulator');
    expect(
      await screen.findByText('Interactive simulations are disabled in this deployment'),
    ).toBeInTheDocument();
    expect(await screen.findByText(/Running simulations is disabled/)).toBeInTheDocument();
    expect(screen.queryByText(/re-run the\s+M4 linear programme/)).not.toBeInTheDocument();
  });
});

describe('the overview', () => {
  it('says when the limitations could not be loaded instead of loading forever', async () => {
    const down = () => apiError(503, 'artifact_unavailable', 'not ready');
    mockApi({ '/api/v1/forecasts': down, '/api/v1/scenarios': down }, DESCRIPTORS.publicDemo);
    at('/');
    expect(
      await screen.findByText(/The limitations could not be loaded/, {}, { timeout: 5000 }),
    ).toBeInTheDocument();
  });
});
