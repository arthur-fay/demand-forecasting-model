import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { App } from '../App';
import { mockApi, urlOf } from '../test/api';

function at(path: string) {
  window.history.replaceState(null, '', path);
  return render(<App />);
}

async function tables(user: ReturnType<typeof userEvent.setup>) {
  for (const toggle of screen.getAllByRole('radio', { name: 'Table' })) await user.click(toggle);
}

describe('every chart has its table', () => {
  it('forecasting: history, projection and backtests as tables, every filter', async () => {
    const fetchMock = mockApi();
    const user = userEvent.setup();
    at('/forecasting?window=all&horizon=7');
    await screen.findByRole('heading', { name: 'Forecasting', level: 1 });
    await screen.findByRole('slider', { name: /Observed history/ }, { timeout: 5000 });
    // The whole history was asked for from the snapshot's first day.
    const asked = fetchMock.mock.calls.map(([input]) => urlOf(input));
    expect(asked).toContain('/api/v1/forecasts/history?start=2023-01-01');
    await screen.findByRole('slider', { name: /mean absolute error/ }, { timeout: 5000 });
    await tables(user);
    const history = screen.getByRole('table', {
      name: /Observed history and the archived 30-day projection/,
    });
    expect(within(history).getAllByText('projected')).toHaveLength(7);
    await user.click(screen.getAllByRole('radio', { name: 'RMSE' })[0]!);
    expect(screen.getByRole('table', { name: /root mean squared error/ })).toBeInTheDocument();
    await user.click(screen.getAllByRole('radio', { name: 'Legacy window' })[0]!);
    expect(screen.getByText('The legacy window is descriptive only')).toBeInTheDocument();
    await user.click(screen.getAllByRole('radio', { name: '90 days' })[0]!);
    expect(window.location.search).toContain('window=90');
    await user.click(screen.getByText(/Projection by day/));
    expect(screen.getByRole('table', { name: /Archived M2 projection/ })).toBeInTheDocument();
  });

  it('uncertainty: bands, coverage and calibration as tables', async () => {
    mockApi();
    const user = userEvent.setup();
    at('/uncertainty');
    await screen.findByRole('slider', { name: /uncalibrated/ }, { timeout: 5000 });
    await screen.findByRole('slider', { name: /Observed interval coverage/ });
    await tables(user);
    expect(
      screen.getByRole('table', { name: /Marginal quantiles of each projected day/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole('table', { name: /Interval coverage evidence/ })).toBeInTheDocument();
    expect(screen.getByRole('table', { name: /Share of evaluation outcomes/ })).toBeInTheDocument();
    expect(screen.getByRole('table', { name: /Exceedance statuses/ })).toBeInTheDocument();
  });

  it('decisions: every measure, the daily plan as a table, another scenario', async () => {
    mockApi();
    const user = userEvent.setup();
    at('/decisions?scenario=unknown_one');
    expect(
      await screen.findByText(/There is no reference scenario named/, {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    await screen.findByRole(
      'slider',
      { name: /Daily capacity of the reported plan/ },
      { timeout: 5000 },
    );
    for (const measure of ['Backlog request-days', 'Terminal backlog', 'Overtime hours']) {
      await user.click(screen.getByRole('radio', { name: measure }));
      expect(
        screen.getByRole('slider', { name: new RegExp(measure.replace(/-/g, '.'), 'i') }),
      ).toBeInTheDocument();
    }
    await tables(user);
    expect(screen.getByRole('table', { name: /Daily plan/ })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Scenario'), 'reduced_budget');
    expect(window.location.search).toBe('?scenario=reduced_budget');
    expect(
      await screen.findByText(/DIAGNOSTIC: not a plan for the stated constraints/),
    ).toBeInTheDocument();
  });

  it('provenance: sources, service and methods', async () => {
    mockApi();
    at('/provenance');
    expect(
      await screen.findByRole(
        'table',
        { name: /Components verified at startup/ },
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
    expect(await screen.findByRole('table', { name: /Committed sources/ })).toBeInTheDocument();
    expect(screen.getByText('BACKTESTING.md (opens GitHub)')).toBeInTheDocument();
  });
});

describe('the simulator form', () => {
  it('switches to a stress path, removes a limit, resets, and cancels a slow run', async () => {
    let calls = 0;
    const fetchMock = mockApi({
      '/api/v1/simulations': (_url, init) => {
        calls += 1;
        return new Promise((_, reject) => {
          init?.signal?.addEventListener('abort', () => {
            reject(new DOMException('aborted', 'AbortError'));
          });
        });
      },
    });
    const user = userEvent.setup();
    at('/simulator');
    await screen.findByRole('heading', { name: 'Hypothetical inputs' });
    await user.click(screen.getByRole('radio', { name: 'M3 quantile stress path' }));
    expect(screen.getByLabelText('Quantile level')).toBeInTheDocument();
    expect(screen.getByText(/HYPOTHETICAL STRESS PATH/)).toBeInTheDocument();
    const terminal = screen.getAllByRole('checkbox', { name: 'No limit' });
    await user.click(terminal[0]!);
    expect(screen.getByText(/2 changes from the reference/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Run simulation' }));
    expect(await screen.findByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.getByText(/Solving on the server/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
    expect(calls).toBe(1);
    await user.click(screen.getByRole('button', { name: 'Reset to reference' }));
    expect(
      screen.getByText(/No change: this runs the reference configuration/),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalled();
  });
});
