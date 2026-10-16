/**
 * What the second independent review of M7 found in the views, kept fixed: each test
 * fails on the defect it names.
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { SLOW_MS } from '../api/resource';
import { App } from '../App';
import { apiError, DESCRIPTORS, FIXTURES, json, mockApi } from '../test/api';

function at(path: string) {
  window.history.replaceState(null, '', path);
  return render(<App />);
}

/** A deep copy of a fixture, to change one field of it. */
function copy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

const down = () => apiError(503, 'artifact_unavailable', 'not ready');

describe('M2: the H1 result reads the right way round', () => {
  it('names the difference and its sign on the Overview tile', async () => {
    mockApi();
    at('/');
    const tile = (
      await screen.findByText(
        'MAE difference, model minus best baseline (H1)',
        {},
        { timeout: 5000 },
      )
    ).closest('.stat') as HTMLElement;
    expect(within(tile).getByText('−127')).toBeInTheDocument();
    expect(
      within(tile).getByText(/^Negative means the model is more accurate\./),
    ).toBeInTheDocument();
    expect(within(tile).getByText('Not significant after Holm')).toBeInTheDocument();
  });

  it('is H1, found by name, on the Forecasting page too', async () => {
    const models = copy(FIXTURES.models);
    models.data.primary_tests.reverse(); // H2 (significant) first
    mockApi({ '/api/v1/models': () => json(models) });
    at('/forecasting');
    expect(
      await screen.findByText(
        /^H1: .*not statistically significant after the Holm adjustment$/,
        {},
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^H2: .*significant after the Holm adjustment$/)).toBeNull();
  });
});

describe('M2: model roles are words, not only colours', () => {
  it('says which bar and which table rows are the recommended model and the best baseline', async () => {
    mockApi();
    const user = userEvent.setup();
    at('/forecasting');
    const panel = await screen.findByRole('region', { name: 'Models against baselines' });
    const bars = await within(panel).findByRole(
      'slider',
      { name: /Mean absolute error over horizons 1-30/ },
      { timeout: 5000 },
    );
    expect(bars).toHaveAccessibleDescription(
      /ATLAS linear regression \(recommended\): 985 requests\/day; .*Median of last 5 same weekdays \(best baseline\): 1,112/,
    );
    bars.focus();
    await user.keyboard('{ArrowDown}');
    expect(bars).toHaveAttribute('aria-valuetext', expect.stringMatching(/985 \(recommended\)/));
    await user.click(within(panel).getByText(/Every metric by horizon bucket/));
    const table = within(panel).getByRole('table', { name: /Backtest metrics by horizon bucket/ });
    for (const row of table.querySelectorAll('tr[data-highlight="true"]'))
      expect(within(row as HTMLElement).getByRole('rowheader')).toHaveTextContent(
        'ATLAS linear regression (recommended)',
      );
  });

  it('keeps both roles of a model that holds two, with no legend entry for a colour not drawn', async () => {
    const models = copy(FIXTURES.models);
    models.data.selection.best_baseline = models.data.recommended_model;
    mockApi({ '/api/v1/models': () => json(models) });
    at('/forecasting');
    const panel = await screen.findByRole('region', { name: 'Models against baselines' });
    const bars = await within(panel).findByRole(
      'slider',
      { name: /Mean absolute error over horizons 1-30/ },
      { timeout: 5000 },
    );
    expect(bars).toHaveAccessibleDescription(
      /ATLAS linear regression \(recommended, best baseline\): 985/,
    );
    const legend = within(panel).getByRole('list', { name: 'Legend' });
    expect(within(legend).getByText('Recommended model')).toBeInTheDocument();
    expect(within(legend).queryByText('Best baseline (development)')).toBeNull();
  });
});

describe('M2: accuracy by horizon for another period', () => {
  it('says it is loading, and when it is slow, instead of drawing an empty frame', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let release = () => {};
      const development = copy(FIXTURES.metricsHorizon);
      for (const row of development.data.rows) row.period = 'development';
      mockApi({
        '/api/v1/forecasts/metrics': (url) => {
          if (url.searchParams.get('scope') === 'bucket') return json(FIXTURES.metricsBucket);
          if (url.searchParams.get('period') !== 'development')
            return json(FIXTURES.metricsHorizon);
          return new Promise<Response>((resolve) => {
            release = () => {
              resolve(json(development));
            };
          });
        },
      });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime.bind(vi) });
      at('/forecasting');
      const panel = await screen.findByRole('region', { name: 'Accuracy by forecast horizon' });
      await within(panel).findByRole('slider', { name: /mean absolute error/ }, { timeout: 5000 });
      await user.click(within(panel).getByRole('radio', { name: 'Development' }));
      expect(within(panel).getByRole('status')).toHaveTextContent('Loading the backtest metrics');
      expect(within(panel).queryByRole('slider')).toBeNull();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SLOW_MS + 100);
      });
      expect(within(panel).getByText(/Still waiting for the server/)).toBeInTheDocument();
      act(() => {
        release();
      });
      const chart = await within(panel).findByRole('slider', { name: /mean absolute error/ });
      expect(Number(chart.getAttribute('aria-valuemax'))).toBe(30);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('M3: the pre-registered criteria, as UNCERTAINTY.md states them', () => {
  it('states all three conditions and shows the horizon buckets that fail', async () => {
    mockApi();
    const user = userEvent.setup();
    at('/uncertainty');
    const rule = await screen.findByText(
      /^Three conditions, all pre-registered/,
      {},
      { timeout: 5000 },
    );
    expect(rule).toHaveTextContent(
      '(±5 pp at 80 %, ±3 pp at 95 %) over days 1-30 and in every horizon bucket',
    );
    expect(rule).toHaveTextContent('a HAC test that does not reject the nominal coverage at 5 %');
    expect(
      screen.getByText('95 % interval: coverage outside tolerance in days_8_14, days_15_30'),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('slider', { name: /Observed interval coverage/ }),
    ).toHaveAccessibleDescription(/95 %: .*outside tolerance in days_8_14, days_15_30/);
    await user.click(
      within(
        screen.getByRole('region', { name: 'Why the intervals are not decision-grade' }),
      ).getAllByRole('radio', { name: 'Table' })[0]!,
    );
    const table = screen.getByRole('table', { name: /Interval coverage evidence/ });
    expect(
      within(table).getByRole('columnheader', { name: /Nominal coverage rejected/ }),
    ).toBeInTheDocument();
  });

  it('keeps the provenance of the exceedance statuses with them', async () => {
    mockApi();
    at('/uncertainty');
    const panel = await screen.findByRole('region', { name: 'Threshold-exceedance probabilities' });
    expect(
      await within(panel).findByText(/Sources and fingerprints/, {}, { timeout: 5000 }),
    ).toBeInTheDocument();
  });
});

describe('provenance of the observed data', () => {
  it('lists every committed file behind the numbers, merged per source', async () => {
    // The scenario detail cites only plans.csv of the M4 run: the row can list the other
    // files only if the sources of every answer are merged, not replaced by the last one.
    const detail = copy(FIXTURES.scenarioNormal) as unknown as {
      provenance: { sources: { kind: string; files: Record<string, string> }[] };
    };
    for (const source of detail.provenance.sources)
      if (source.kind === 'm4_reference_run')
        source.files = Object.fromEntries(
          Object.entries(source.files).filter(([name]) => name === 'plans.csv'),
        );
    mockApi({ '/api/v1/scenarios/normal': () => json(detail) });
    at('/provenance');
    const table = await screen.findByRole('table', { name: /Committed sources/ });
    await waitFor(
      () => {
        expect(screen.queryByText('Collecting sources…')).toBeNull();
      },
      { timeout: 5000 },
    );
    expect(within(table).getByText('01 Data')).toBeInTheDocument();
    expect(within(table).getByText(/daily_total\.csv/)).toBeInTheDocument();
    const run = within(table)
      .getByText('docs/results/atlas-m4-optimization-v1')
      .closest('tr') as HTMLElement;
    for (const file of ['metrics.json', 'plans.csv', 'scenarios.csv'])
      expect(within(run).getByText(new RegExp(file.replace('.', '\\.')))).toBeInTheDocument();
  });

  it('stops collecting when the scenario list cannot be loaded', async () => {
    mockApi({ '/api/v1/scenarios': down }, DESCRIPTORS.publicDemo);
    at('/provenance');
    expect(
      await screen.findByText(/source lists could not be loaded/, {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText('Collecting sources…')).toBeNull();
    });
  });

  it.each(['/forecasting', '/uncertainty', '/'])(
    'shows the API label of the observed numbers on %s',
    async (path) => {
      mockApi();
      at(path);
      expect(
        (
          await screen.findAllByText(
            /^OBSERVED: city-wide 311 service requests created per day/,
            {},
            { timeout: 5000 },
          )
        ).length,
      ).toBeGreaterThan(0);
    },
  );
});

describe('the overview limitations', () => {
  it('names the answers that failed, without claiming no number is shown', async () => {
    mockApi({ '/api/v1/forecasts': down, '/api/v1/scenarios': down }, DESCRIPTORS.publicDemo);
    at('/');
    expect(
      await screen.findByText(
        /could not be loaded: the requests for the forecast and the scenarios failed/,
        {},
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No number is shown/)).toBeNull();
    expect(screen.queryByText(/did not answer/)).toBeNull();
  });

  it('says when the list is incomplete', async () => {
    mockApi({ '/api/v1/forecasts': down }, DESCRIPTORS.publicDemo);
    at('/');
    expect(
      await screen.findByText(
        'This list is incomplete: the limitations sent with the forecast could not be loaded.',
        {},
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
    const panel = screen.getByRole('region', { name: 'Warnings and limitations' });
    expect(within(panel).getAllByRole('listitem').length).toBeGreaterThan(0);
  });
});
