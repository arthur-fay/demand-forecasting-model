/**
 * What the checks of the second review's fixes found (docs/DASHBOARD.md section 16).
 */
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { SLOW_MS } from '../api/resource';
import { App } from '../App';
import { apiError, FIXTURES, json, mockApi } from '../test/api';

function at(path: string) {
  window.history.replaceState(null, '', path);
  return render(<App />);
}

function copy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

describe('the coverage evidence', () => {
  it('says a test M3 could not compute was not computed, not that it was not rejected', async () => {
    const status = copy(FIXTURES.uncertaintyStatus);
    const level = status.data.interval_levels[0]!;
    level.test_computed = false;
    level.rejected = false; // what M3 sends when the test could not be computed
    mockApi({ '/api/v1/uncertainty/status': () => json(status) });
    const user = userEvent.setup();
    at('/uncertainty');
    const panel = await screen.findByRole(
      'region',
      { name: 'Why the intervals are not decision-grade' },
      { timeout: 5000 },
    );
    await user.click(within(panel).getByRole('radio', { name: 'Table' }));
    const table = await within(panel).findByRole(
      'table',
      { name: /Interval coverage evidence/ },
      { timeout: 5000 },
    );
    const rows = within(table).getAllByRole('row');
    const first = rows.find((row) => within(row).queryByText(/80/) !== null) as HTMLElement;
    expect(within(first).getByText('not computed')).toBeInTheDocument();
  });
});

describe('the scenario detail', () => {
  it('says when the server is slow, as every other panel does', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let release = () => {};
      mockApi({
        '/api/v1/scenarios/normal': () =>
          new Promise<Response>((resolve) => {
            release = () => {
              resolve(json(FIXTURES.scenarioNormal));
            };
          }),
      });
      at('/decisions');
      const panel = await screen.findByRole(
        'region',
        { name: 'Scenario detail' },
        { timeout: 5000 },
      );
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SLOW_MS + 100);
      });
      expect(within(panel).getByText(/Still waiting for the server/)).toBeInTheDocument();
      act(() => {
        release();
      });
      expect(
        await within(panel).findByRole('heading', { name: /Normal/ }, { timeout: 5000 }),
      ).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('a simulation the solver could not certify', () => {
  it('is reported as giving no result, not as a run that never happened', async () => {
    mockApi({
      '/api/v1/simulations': () =>
        apiError(500, 'simulation_failed', 'no certified result: the solver stopped'),
    });
    const user = userEvent.setup();
    at('/simulator');
    await user.click(await screen.findByRole('button', { name: 'Run simulation' }));
    expect(await screen.findByText(/^No simulation result/)).toBeInTheDocument();
    expect(screen.queryByText(/Simulation not run/)).not.toBeInTheDocument();
    expect(screen.getByText(/returned no plan rather than an unverified one/)).toBeInTheDocument();
  });
});
