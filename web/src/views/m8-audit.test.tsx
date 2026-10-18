/**
 * What the M8 release audit found in the dashboard (docs/audit/M8_FINDINGS_REGISTER.md),
 * kept fixed: each test fails on the defect it names.
 */
import { getConfig, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { ScenarioDetailResponse } from '../api/schemas';
import { z } from '../api/zod';
import { App } from '../App';
import { FONT_LICENCE, THIRD_PARTY_NOTICES } from '../notices';
import { apiError, FIXTURES, json, mockApi } from '../test/api';
import { PlanOutcome } from './plan';

function at(path: string) {
  window.history.replaceState(null, '', path);
  return render(<App />);
}

/** A deep copy of a fixture, to change one field of it. */
function copy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

describe('M8-05: the licence notices', () => {
  it('are linked from the footer of every page', async () => {
    mockApi();
    at('/forecasting');
    await screen.findByRole('heading', { name: 'Forecasting', level: 1 });
    const footer = screen.getByRole('contentinfo');
    expect(within(footer).getByRole('link', { name: /third-party licences/i })).toHaveAttribute(
      'href',
      `/${THIRD_PARTY_NOTICES}`,
    );
    expect(within(footer).getByRole('link', { name: /Inter font licence/i })).toHaveAttribute(
      'href',
      `/${FONT_LICENCE}`,
    );
  });
});

describe('M8-28: the legacy window on the Forecasting page', () => {
  it('shows the target days that were scored, never past the end of the snapshot', async () => {
    mockApi();
    at('/forecasting');
    const table = await screen.findByRole('table', { name: /M2 periods/ });
    const legacy = within(table).getByRole('rowheader', { name: 'Legacy window' }).closest('tr');
    // The API's nominal last target is 2026-07-29 (last origin + 30); the snapshot ends on
    // 2026-06-30, so no later day was scored.
    expect(legacy).toHaveTextContent('Mar 4, 2026 – Jun 30, 2026');
    expect(legacy).not.toHaveTextContent('Jul 29');
    const confirmation = within(table)
      .getByRole('rowheader', { name: 'Confirmation' })
      .closest('tr');
    expect(confirmation).toHaveTextContent('Jul 1, 2025 – Jun 30, 2026');
    expect(table).toHaveAccessibleName(/incomplete/);
  });

  it('ranks the models in the legacy window with its descriptive-only caveat', async () => {
    mockApi();
    const user = userEvent.setup();
    at('/forecasting');
    const panel = await screen.findByRole('region', { name: 'Models against baselines' });
    await within(panel).findByRole('slider', { name: /Mean absolute error over horizons 1-30/ });
    expect(within(panel).queryByText('The legacy window is descriptive only')).toBeNull();
    await user.click(within(panel).getByRole('radio', { name: 'Legacy window' }));
    expect(within(panel).getByText('The legacy window is descriptive only')).toBeInTheDocument();
    expect(within(panel).getByText(/horizons may be incomplete there/)).toBeInTheDocument();
  });
});

describe('M8-18: the calibration wording follows the served M3 status', () => {
  type Status = 'decision_grade' | 'failed_calibration' | 'insufficient_evidence' | 'unavailable';

  /** What the bands are called with each status. */
  const BANDS_WORD: Record<Status, string | null> = {
    decision_grade: 'calibrated',
    failed_calibration: 'uncalibrated',
    insufficient_evidence: 'uncalibrated',
    unavailable: null,
  };

  /** The API's answers with another M3 status (and neutral API wording). */
  function serve(status: Status) {
    const answer = copy(FIXTURES.uncertaintyStatus);
    answer.data.interval_status = status;
    answer.data.decision_grade = status === 'decision_grade';
    answer.data.statement = 'The statement of the API.';
    answer.data.failure_reasons = [];
    answer.integrity.uncertainty_interval_status = status;
    answer.integrity.uncertainty_decision_grade = status === 'decision_grade';
    answer.integrity.notices = [];
    const intervals = copy(FIXTURES.intervals);
    intervals.data.interval_status = status;
    intervals.data.label = 'The label of the API.';
    intervals.integrity.uncertainty_interval_status = status;
    intervals.integrity.uncertainty_decision_grade = status === 'decision_grade';
    intervals.integrity.notices = [];
    mockApi({
      '/api/v1/uncertainty/status': () => json(answer),
      '/api/v1/uncertainty/intervals': () => json(intervals),
    });
  }

  /** The dashboard's own words about the outcome: header, status prose, band chart. */
  async function words() {
    at('/uncertainty');
    const prose = (await screen.findByText(/What this means\./)).closest('p') as HTMLElement;
    const header = screen.getByRole('heading', { level: 1 }).closest('header') as HTMLElement;
    const chart = await screen.findByRole('slider', { name: /Archived projection with/ });
    return { header, prose, chart };
  }

  for (const status of ['insufficient_evidence', 'unavailable', 'decision_grade'] as const) {
    it(`never says the intervals failed when the status is ${status}`, async () => {
      serve(status);
      const { header, prose, chart } = await words();
      for (const element of [header, prose]) expect(element).not.toHaveTextContent(/fail/i);
      expect(chart).not.toHaveAccessibleDescription(/fail/i);
      // An unavailable status says nothing about the outcome: plain quantile bands.
      const word = BANDS_WORD[status];
      expect(chart).toHaveAccessibleName(
        new RegExp(`^Archived projection with ${word === null ? '' : `${word} `}marginal quantile`),
      );
      expect(
        screen.getByRole('region', { name: `The projection with its ${word ?? 'quantile'} bands` }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole('region', { name: 'Why the intervals are not decision-grade' }) === null,
      ).toBe(status === 'decision_grade');
    });
  }

  it('names the intervals elsewhere by the status they were served with', async () => {
    serve('insufficient_evidence');
    at('/provenance');
    const methods = await screen.findByRole('region', { name: 'Methods' });
    expect(await within(methods).findByText(/Result: INSUFFICIENT_EVIDENCE\./)).toBeVisible();
  });

  it('names the intervals of the M4 stress paths by their served status', async () => {
    const list = copy(FIXTURES.scenarios);
    list.integrity.uncertainty_interval_status = 'insufficient_evidence';
    mockApi({ '/api/v1/scenarios': () => json(list) });
    at('/decisions');
    expect(
      await screen.findByText(/stress path from the INSUFFICIENT_EVIDENCE intervals/),
    ).toBeInTheDocument();
  });

  it('says they failed when the status is FAILED_CALIBRATION', async () => {
    serve('failed_calibration');
    const { header, prose, chart } = await words();
    expect(header).toHaveTextContent(/They failed that test/);
    expect(prose).toHaveTextContent(/they failed that test, so they are not decision-grade/);
    expect(chart).toHaveAccessibleDescription(/Their calibration failed/);
    expect(chart).toHaveAccessibleName(
      /^Archived projection with uncalibrated marginal quantile bands\./,
    );
    expect(
      screen.getByRole('region', { name: 'Why the intervals are not decision-grade' }),
    ).toBeInTheDocument();
  });

  it('says the result could not be loaded when the status does not load', async () => {
    mockApi({
      '/api/v1/uncertainty/status': () => apiError(503, 'artifact_unavailable', 'not ready'),
    });
    at('/uncertainty');
    const panel = await screen.findByRole('region', { name: 'Interval coverage' });
    expect(await within(panel).findByRole('alert')).toBeInTheDocument();
    const header = screen.getByRole('heading', { level: 1 }).closest('header') as HTMLElement;
    expect(header).toHaveTextContent(/Its result could not be loaded here\./);
    expect(header).not.toHaveTextContent(/The status below says how they did/);
  });
});

describe('the calibration chart (an M8 observation)', () => {
  it('keys its dotted diagonal in the legend, dotted, as the text calls it', async () => {
    mockApi();
    at('/uncertainty');
    const chart = await screen.findByRole('slider', { name: /Share of outcomes at or below/ });
    // The legend was left out for a single series, and with it the diagonal's key.
    const legend = within(chart.closest('.stack-2') as HTMLElement).getByRole('list', {
      name: 'Legend',
    });
    const key = within(legend).getByText('Perfect calibration');
    expect(key.querySelector('line')).toHaveAttribute('stroke-dasharray', '1.5 3.5');
    expect(screen.getByText(/would sit on the dotted diagonal/)).toBeInTheDocument();
  });
});

describe('M8-19: the optimality certificate', () => {
  const scenario = z.parse(ScenarioDetailResponse, copy(FIXTURES.scenarioNormal)).data;

  it('shows its largest violation as a fraction of the tolerance, not as a duality gap', () => {
    render(
      <PlanOutcome
        outcome={scenario.outcome}
        plans={scenario.plans}
        daily={scenario.daily_plans}
      />,
    );
    const text = screen.getByText(/Certified:/).closest('p') as HTMLElement;
    // The API's duality_gap of the normal scenario is 6.08e-9 of its tolerance.
    expect(text).toHaveTextContent(/largest violation 6\.1e-9 of its tolerance/);
    expect(text).not.toHaveTextContent(/gap 6/);
  });

  it('shows the largest of its four checks, whichever it is', () => {
    // In the reduced-budget scenario stationarity (6.58e-8) exceeds the duality gap (5.47e-9).
    const reduced = z.parse(ScenarioDetailResponse, copy(FIXTURES.scenarioReduced)).data;
    const certificate = reduced.outcome.reported_plan?.certificate;
    expect(certificate?.stationarity).toBeGreaterThan(certificate?.duality_gap ?? Infinity);
    render(
      <PlanOutcome outcome={reduced.outcome} plans={reduced.plans} daily={reduced.daily_plans} />,
    );
    const text = screen.getByText(/Certified:/).closest('p') as HTMLElement;
    expect(text).toHaveTextContent(/largest violation 6\.6e-8 of its tolerance/);
    expect(text).not.toHaveTextContent(/5\.5e-9/);
  });

  it('calls each plan an optimum within the solver tolerance, not the exact optimum', async () => {
    mockApi();
    at('/decisions');
    const panel = await screen.findByRole('region', { name: 'Reading the optimisation' });
    expect(panel).not.toHaveTextContent(/exact optimum|zero duality gap/);
    expect(panel).toHaveTextContent(/an optimum of an assumed problem within those tolerances/);
    expect(panel).toHaveTextContent(/one of several when the optimum is not unique/);
  });

  it('states the tolerances of each check as the optimiser applies them', async () => {
    // optimizer.certify (docs/OPTIMIZATION.md section 4): only the primal rows and bounds
    // have an absolute part; the dual signs, stationarity and the gap are relative only.
    mockApi();
    at('/decisions');
    const panel = await screen.findByRole('region', { name: 'Reading the optimisation' });
    expect(panel).toHaveTextContent(
      /primal feasibility \(each row and bound within 1e-7 \+ 1e-9 × its magnitude\)/,
    );
    expect(panel).toHaveTextContent(
      /for dual feasibility and for its duality gap \(each within 1e-9 × its magnitude\)/,
    );
    expect(panel).not.toHaveTextContent(/1e-7 absolute and 1e-9 relative/);
  });
});

describe('M8-31: the Methods links', () => {
  it('say they open the current documentation on main, and where the release has its own', async () => {
    mockApi();
    at('/provenance');
    const panel = await screen.findByRole('region', { name: 'Methods' });
    const link = within(panel).getByRole('link', { name: /BACKTESTING\.md/ });
    expect(link).toHaveAttribute(
      'href',
      'https://github.com/arthur-fay/demand-forecasting-model/blob/main/docs/BACKTESTING.md',
    );
    expect(link).toHaveAccessibleName('BACKTESTING.md on the main branch (opens GitHub)');
    expect(panel).toHaveTextContent(/may have changed since this release/);
    expect(panel).toHaveTextContent(/docs\/ directory of the release/);
    // M8-11: the legacy figures are reproduced within their published precision, but one.
    expect(panel).toHaveTextContent(
      /every legacy figure within its published precision \(one RMSE rounding is a declared exception\)/,
    );
  });
});

describe('M8-30: the asynchronous waits of the tests', () => {
  it('allow a page 5 s to render, not the 1 s default of Testing Library', () => {
    expect(getConfig().asyncUtilTimeout).toBe(5000);
  });
});

describe('M8-34: the Overview heading', () => {
  it('describes the research without marketing language', async () => {
    mockApi();
    at('/');
    const heading = await screen.findByRole('heading', { level: 1 });
    expect(heading).toHaveTextContent(
      'NYC 311 demand: forecasts, uncertainty and hypothetical scenarios',
    );
    expect(document.body).not.toHaveTextContent(/decision.intelligence/i);
  });
});

describe('M8 final review: the confirmation tests panel', () => {
  it('shows the size caveat of the selection test next to the selection and the verdicts', async () => {
    // The panel marks H2 significant and quotes the selection reason ("the selection-period
    // DM test is significant at 0.05"). The API sends the caveat of that test with them: it
    // must be read in the same panel, after the selection, not only in another panel.
    const notices = FIXTURES.models.integrity.notices;
    const caveat = notices.find((notice) => notice.startsWith('M2 SELECTION EVIDENCE'));
    expect(caveat).toMatch(/selection p-value is 0\.069, above the gate/);
    mockApi();
    at('/forecasting');
    const panel = await screen.findByRole('region', {
      name: 'Is the model better than the baselines?',
    });
    const selection = await within(panel).findByText(/selection-period DM test is significant/);
    const shown = within(panel).getByText(caveat!);
    expect(selection.compareDocumentPosition(shown) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    for (const notice of notices) expect(within(panel).getByText(notice)).toBeInTheDocument();
  });
});
