import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { App } from './App';
import { apiError, DESCRIPTORS, FIXTURES, json, mockApi, sentBody, urlOf } from './test/api';

function at(path: string) {
  window.history.replaceState(null, '', path);
  return render(<App />);
}

async function heading(name: RegExp | string, level = 1) {
  return screen.findByRole('heading', { name, level }, { timeout: 5000 });
}

describe('scientific warnings next to the numbers', () => {
  it('overview: not validated, not significant, FAILED_CALIBRATION, HYPOTHETICAL', async () => {
    mockApi();
    at('/');
    await heading(/NYC 311 demand: forecasts, uncertainty and hypothetical scenarios/);
    const status = (await screen.findByRole('heading', { name: 'Scientific status' })).closest(
      'section',
    )!;
    await within(status).findByText('Not significant after Holm');
    expect(within(status).getAllByText('FAILED_CALIBRATION').length).toBeGreaterThan(0);
    expect(within(status).getByText('Not validated')).toBeInTheDocument();
    expect(within(status).getByText('HYPOTHETICAL')).toBeInTheDocument();
    // The archived-projection warning sits with the forecast summary.
    expect(
      await screen.findByText('An archived projection, not a live forecast'),
    ).toBeInTheDocument();
    // The API's own notices travel with the chart: its label, in the API's words (the start
    // of the recorded answer's label, so that the API may reword it).
    const label = FIXTURES.forecast.data.label.slice(0, 40).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    expect((await screen.findAllByText(new RegExp(label))).length).toBeGreaterThan(0);
  });

  it('forecasting: the H1 test is reported as not significant after Holm', async () => {
    mockApi();
    at('/forecasting');
    await heading('Forecasting');
    expect(
      await screen.findByText(
        /not statistically significant after the Holm adjustment/,
        {},
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/The interval includes zero/)).toBeInTheDocument();
  });

  it('uncertainty: every band is labelled with the failed status, never as a guarantee', async () => {
    mockApi();
    at('/uncertainty');
    await heading('Uncertainty and risk');
    expect(
      await screen.findByRole('heading', { name: 'Interval status: FAILED_CALIBRATION' }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(
        /FAILED_CALIBRATION: read these bands as illustrations, not guarantees/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('Marginal 0.1-0.9 quantiles (FAILED_CALIBRATION)')).toBeInTheDocument();
    expect(screen.queryByText(/decision-grade:? yes/i)).not.toBeInTheDocument();
  });

  it('decisions: DIAGNOSTIC rows are never compared as plan differences', async () => {
    mockApi();
    const user = userEvent.setup();
    at('/decisions');
    await heading('Decision optimisation');
    await screen.findByText('How to read these differences');
    await user.click(screen.getAllByRole('radio', { name: 'Table' })[0]!);
    const table = screen.getByRole('table', { name: /Every scenario against the baseline/ });
    const relaxed = FIXTURES.comparison.data.rows.filter(
      (r) => r.values_status === 'DIAGNOSTIC',
    ).length;
    expect(within(table).getAllByText('not comparable')).toHaveLength(relaxed);
    expect(within(table).getAllByText('DIAGNOSTIC')).toHaveLength(relaxed);
  });

  it('decisions: an infeasible scenario reports a DIAGNOSTIC plan and its diagnosis', async () => {
    mockApi();
    at('/decisions?scenario=reduced_budget');
    await heading('Decision optimisation');
    expect(
      await screen.findByText(
        /DIAGNOSTIC: not a plan for the stated constraints/,
        {},
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/not a budget request/).length).toBeGreaterThan(0);
  });
});

describe('loading and error states', () => {
  it('says when the connection is slow, then shows the data', { timeout: 10_000 }, async () => {
    let release: (value: Response) => void = () => undefined;
    mockApi({
      '/api/v1/uncertainty/status': () => new Promise<Response>((resolve) => (release = resolve)),
    });
    at('/uncertainty');
    await heading('Uncertainty and risk');
    expect(screen.getAllByText(/Loading the calibration status/).length).toBeGreaterThan(0);
    expect(
      (await screen.findAllByText(/the connection seems slow/, {}, { timeout: 4000 })).length,
    ).toBeGreaterThan(0);
    act(() => {
      release(json(FIXTURES.uncertaintyStatus));
    });
    expect(
      await screen.findByRole('heading', { name: 'Interval status: FAILED_CALIBRATION' }),
    ).toBeInTheDocument();
  });

  it('shows an unavailable component as an error, with no numbers in its place, and retries', async () => {
    const user = userEvent.setup();
    let fail = true;
    mockApi({
      '/api/v1/scenarios': () =>
        fail
          ? apiError(503, 'artifact_unavailable', 'the M4 reference run is not available')
          : json(FIXTURES.scenarios),
    });
    at('/');
    await heading(/NYC 311 demand: forecasts, uncertainty and hypothetical scenarios/);
    const alerts = await screen.findAllByText(/This component is unavailable/);
    expect(alerts.length).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/No numbers are shown in place of the missing ones/).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText('Request id req-12345678').length).toBeGreaterThan(0);
    fail = false;
    await user.click(screen.getAllByRole('button', { name: 'Retry' })[0]!);
    expect(await screen.findAllByText('Reduced budget')).not.toHaveLength(0);
  });

  it('refuses a response that breaks the contract and says why', async () => {
    const broken = structuredClone(FIXTURES.intervals) as { data: { interval_status: string } };
    broken.data.interval_status = 'calibrated';
    mockApi({ '/api/v1/uncertainty/intervals': () => json(broken) });
    at('/uncertainty');
    expect(
      await screen.findAllByText(/Unexpected answer from the API/, {}, { timeout: 5000 }),
    ).not.toHaveLength(0);
    expect(screen.getAllByText('data.interval_status').length).toBeGreaterThan(0);
  });
});

describe('the simulator', () => {
  async function openSimulator(descriptor: unknown = DESCRIPTORS.local, overrides = {}) {
    const fetchMock = mockApi(overrides, descriptor);
    at('/simulator');
    await heading('Hypothetical simulator');
    await screen.findByRole('heading', { name: 'Hypothetical inputs' });
    return fetchMock;
  }

  it('sends only the changed values and shows the solver result with its labels', async () => {
    const user = userEvent.setup();
    const fetchMock = await openSimulator();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Run simulation' })).toBeEnabled();
    });
    const horizon = screen.getByLabelText(/Planning horizon/, { selector: 'input[type=text]' });
    await user.clear(horizon);
    await user.type(horizon, '7');
    const budget = screen.getByLabelText(/Discretionary budget/, { selector: 'input[type=text]' });
    await user.clear(budget);
    await user.type(budget, '20000');
    await user.click(screen.getByRole('button', { name: 'Run simulation' }));
    expect(await screen.findByRole('heading', { name: /Simulation result/ })).toBeInTheDocument();
    expect(sentBody(fetchMock, '/api/v1/simulations')).toEqual([
      { horizon_days: 7, parameters: { discretionary_budget: 20000 } },
    ]);
    expect(screen.getByText('An exploratory mathematical demonstration')).toBeInTheDocument();
    expect(screen.getByText('Limitations of this simulation (from the API)')).toBeInTheDocument();
    expect(screen.getAllByText('DIAGNOSTIC').length).toBeGreaterThan(0);
  });

  it('refuses invalid input before sending anything, and says where', async () => {
    const user = userEvent.setup();
    const fetchMock = await openSimulator();
    const backlog = screen.getByLabelText(/Initial backlog/, { selector: 'input[type=text]' });
    await user.clear(backlog);
    await user.type(backlog, 'lots');
    await user.click(screen.getByRole('button', { name: 'Run simulation' }));
    expect(await screen.findByText(/1 input needs attention/)).toBeInTheDocument();
    expect(backlog).toHaveAttribute('aria-invalid', 'true');
    expect(backlog).toHaveAccessibleDescription(/Enter a number/);
    expect(sentBody(fetchMock, '/api/v1/simulations')).toEqual([]);
  });

  it('maps the API validation errors onto the fields', async () => {
    const user = userEvent.setup();
    await openSimulator(DESCRIPTORS.local, {
      '/api/v1/simulations': () =>
        apiError(422, 'simulation_rejected', 'M4 refused the configuration', [
          {
            field: 'parameters.surge_available_from_day',
            message: 'must be at most horizon_days + 1 (8)',
          },
        ]),
    });
    const surge = screen.getByLabelText(/Surge available from day/, {
      selector: 'input[type=text]',
    });
    await user.clear(surge);
    await user.type(surge, '20');
    await user.click(screen.getByRole('button', { name: 'Run simulation' }));
    expect(await screen.findByText(/Simulation not run/)).toBeInTheDocument();
    expect(screen.getByText(/M4 refused these inputs as stated/)).toBeInTheDocument();
    expect(surge).toHaveAccessibleDescription(/must be at most horizon_days \+ 1/);
  });

  it.each([
    [504, 'simulation_timeout', /stopped the solve at its deadline/],
    [503, 'simulations_busy', /keeps no queue/],
    [500, 'simulation_failed', /returned no plan rather than an unverified one/],
    [429, 'rate_limited', /limits how many simulations/],
  ])('explains a %i %s', async (status, code, explanation) => {
    const user = userEvent.setup();
    await openSimulator(DESCRIPTORS.local, {
      '/api/v1/simulations': () => apiError(status, code, 'refused'),
    });
    await user.click(screen.getByRole('button', { name: 'Run simulation' }));
    expect(await screen.findByText(explanation)).toBeInTheDocument();
  });

  it.each([
    ['public demo', DESCRIPTORS.publicDemo, /public read-only demonstration/],
    ['an API key', DESCRIPTORS.localWithKey, /a browser must never hold one/],
  ])('is read-only with %s, and says why', async (_name, descriptor, reason) => {
    const fetchMock = await openSimulator(descriptor);
    expect(await screen.findByText(reason)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run simulation' })).toBeDisabled();
    expect(
      screen.getByLabelText(/Initial backlog/, { selector: 'input[type=text]' }),
    ).toBeDisabled();
    expect(sentBody(fetchMock, '/api/v1/simulations')).toEqual([]);
  });

  it('stays disabled when the deployment descriptor cannot be read', async () => {
    await openSimulator(DESCRIPTORS.local, {
      '/atlas-deployment.json': () => apiError(404, 'not_found', 'no'),
    });
    expect(await screen.findByText(/descriptor could not be read/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run simulation' })).toBeDisabled();
  });
});

describe('public-demo deployment', () => {
  it('never asks for operational detail', async () => {
    const fetchMock = mockApi({}, DESCRIPTORS.publicDemo);
    at('/provenance');
    await heading('Methods and provenance');
    expect(
      await screen.findByText('Operational detail is not exposed in this deployment'),
    ).toBeInTheDocument();
    expect(screen.getByText('Public demo · read-only')).toBeInTheDocument();
    const asked = fetchMock.mock.calls.map(([input]) => urlOf(input));
    expect(asked).not.toContain('/api/v1/system');
  });
});

describe('navigation and keyboard', () => {
  it('moves between pages, updates the title and focuses the content', async () => {
    mockApi();
    const user = userEvent.setup();
    at('/');
    await heading(/NYC 311 demand: forecasts, uncertainty and hypothetical scenarios/);
    const nav = screen.getByRole('navigation', { name: 'Dashboard sections' });
    await user.click(within(nav).getByRole('link', { name: /Uncertainty & risk/ }));
    await heading('Uncertainty and risk');
    expect(document.title).toBe('Uncertainty and risk · ATLAS');
    expect(window.location.pathname).toBe('/uncertainty');
    expect(document.activeElement).toBe(screen.getByRole('main'));
    expect(within(nav).getByRole('link', { name: /Uncertainty & risk/ })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it('reads chart values with the arrow keys', async () => {
    mockApi();
    const user = userEvent.setup();
    at('/');
    const slider = await screen.findByRole(
      'slider',
      { name: /Observed history and the archived 30-day projection/ },
      { timeout: 5000 },
    );
    slider.focus();
    await user.keyboard('{End}');
    expect(slider.getAttribute('aria-valuetext')).toMatch(/Jul 30, 2026.*Archived projection/);
    await user.keyboard('{Home}');
    expect(slider.getAttribute('aria-valuetext')).toMatch(
      /Observed requests\/day: [\d,]+ requests\/day/,
    );
    await user.keyboard('{ArrowRight}');
    expect(slider).toHaveAttribute('aria-valuenow', '2');
    await user.keyboard('{Escape}');
    expect(slider).toHaveAttribute('aria-valuenow', '0');
  });

  it('opens the navigation from the menu button on small screens', async () => {
    mockApi();
    const user = userEvent.setup();
    at('/');
    const button = screen.getByRole('button', { name: 'Open navigation' });
    expect(button).toHaveAttribute('aria-expanded', 'false');
    await user.click(button);
    expect(screen.getByRole('button', { name: 'Close navigation' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });

  it('has a page for unknown paths', async () => {
    mockApi();
    at('/nowhere');
    expect(await heading('Page not found')).toBeInTheDocument();
  });
});

describe('the deployment badge', () => {
  it.each([
    [DESCRIPTORS.private, 'Private deployment'],
    [DESCRIPTORS.local, 'Local API'],
  ])('names the profile', async (descriptor, label) => {
    mockApi({}, descriptor);
    at('/provenance');
    expect(await screen.findByText(label)).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});
