import { useState } from 'react';

import type { MetricRow, ModelsData } from '../api/schemas';
import { BarChart } from '../charts/BarChart';
import { LineChart, type LineSeries } from '../charts/LineChart';
import { Callout } from '../components/Callout';
import { Segmented, ViewToggle, type View } from '../components/Controls';
import { DataTable } from '../components/DataTable';
import { Panel } from '../components/Panel';
import { Evidence, ProvenanceFooter } from '../components/Provenance';
import { Async, Loading } from '../components/States';
import { NotValidatedTag, Tag } from '../components/Tag';
import {
  addDays,
  formatCompact,
  formatDate,
  formatInteger,
  formatNumber,
  formatP,
  formatPct,
  formatSigned,
  formatWeekdayDate,
  humanise,
  modelName,
} from '../lib/format';
import { setQueryParam, useQueryParam } from '../router';
import {
  ArchivedProjectionCallout,
  HistoryProjectionChart,
  PageHeader,
  SignificanceCallout,
} from './common';
import {
  h1Test,
  useBucketMetrics,
  useForecast,
  useHistory,
  useHorizonMetrics,
  useModels,
} from './data';

const WINDOWS = [
  { value: '90', label: '90 days' },
  { value: '180', label: '180 days' },
  { value: '365', label: '1 year' },
  { value: 'all', label: 'All' },
] as const;
type Window = (typeof WINDOWS)[number]['value'];

const PERIODS = [
  { value: 'confirmation', label: 'Confirmation' },
  { value: 'development', label: 'Development' },
  { value: 'legacy_window', label: 'Legacy window' },
] as const;
type Period = (typeof PERIODS)[number]['value'];

const METRICS = [
  { value: 'mae', label: 'MAE' },
  { value: 'rmse', label: 'RMSE' },
  { value: 'bias', label: 'Bias' },
] as const;
type Metric = (typeof METRICS)[number]['value'];

const METRIC_TEXT: Record<Metric, string> = {
  mae: 'mean absolute error (requests/day)',
  rmse: 'root mean squared error (requests/day)',
  bias: 'bias, mean of prediction - actual (requests/day; negative = under-forecast)',
};

function parseWindow(value: string | null): Window {
  return WINDOWS.some((w) => w.value === value) ? (value as Window) : '180';
}

function parseHorizon(value: string | null): number {
  const n = Number(value);
  return Number.isInteger(n) && n >= 1 && n <= 30 ? n : 30;
}

function ProjectionPanel() {
  const window = parseWindow(useQueryParam('window'));
  const horizon = parseHorizon(useQueryParam('horizon'));
  const base = useHistory(null);
  const last = base.data?.data.snapshot_last_date;
  const first = base.data?.data.snapshot_first_date;
  const start =
    window === '180' || last === undefined || first === undefined
      ? null
      : window === 'all'
        ? first
        : addDays(last, -(Number(window) - 1));
  const history = useHistory(window === '180' ? null : start);
  const forecast = useForecast();
  const shown = history.data ?? history.previous;
  return (
    <Panel
      title="Observed history and the archived projection"
      subtitle="City-wide 311 requests created per day (requests/day)"
      tags={
        <span className="tag-row">
          <Tag tone="projected">Archived projection</Tag>
          <NotValidatedTag />
        </span>
      }
    >
      <div className="filter-bar">
        <Segmented
          label="History window"
          value={window}
          options={WINDOWS}
          onChange={(value) => {
            setQueryParam('window', value === '180' ? null : value);
          }}
        />
        <div className="field">
          <label className="field-label" htmlFor="horizon">
            <span>Projection horizon</span>
            <span className="unit">
              {horizon} {horizon === 1 ? 'day' : 'days'}
            </span>
          </label>
          <input
            id="horizon"
            type="range"
            min={1}
            max={30}
            step={1}
            value={horizon}
            aria-valuetext={`${horizon} days`}
            onChange={(event) => {
              const value = Number(event.target.value);
              setQueryParam('horizon', value === 30 ? null : String(value));
            }}
          />
        </div>
      </div>
      <Async resource={forecast} label="the forecast">
        {(f) =>
          history.snapshot.status === 'error' ? (
            <Async resource={history} label="the observed history">
              {() => null}
            </Async>
          ) : shown === undefined ? (
            <Loading
              label="the observed history"
              slow={history.snapshot.status === 'loading' && history.snapshot.slow}
            />
          ) : (
            <div className="stack-3">
              <HistoryProjectionChart
                history={shown.data}
                forecast={f.data}
                horizon={horizon}
                stale={history.data === undefined}
              />
              <ArchivedProjectionCallout forecast={f.data} />
              <ProjectionTable points={f.data.points.filter((p) => p.horizon <= horizon)} />
              <Evidence integrity={f.integrity} provenance={f.provenance} />
              {history.data !== undefined && (
                <p className="xsmall muted">
                  History: {formatDate(history.data.data.start)} to{' '}
                  {formatDate(history.data.data.end)},{' '}
                  {formatInteger(history.data.data.points.length)} days, from the{' '}
                  {history.data.data.snapshot_id} snapshot.
                </p>
              )}
              <Evidence
                integrity={shown.integrity}
                provenance={shown.provenance}
                title="What the observed numbers are"
              />
            </div>
          )
        }
      </Async>
    </Panel>
  );
}

function ProjectionTable({
  points,
}: {
  points: readonly {
    horizon: number;
    target_date: string;
    prediction: number;
    federal_holiday: string | null;
    holiday_related: boolean;
  }[];
}) {
  return (
    <details className="disclosure">
      <summary>Projection by day ({points.length} days)</summary>
      <div className="disclosure-body">
        <DataTable
          caption="Archived M2 projection (requests/day). Holiday-related days are an M3 stratum where interval coverage was markedly lower."
          rows={points}
          rowKey={(p) => p.target_date}
          columns={[
            { key: 'h', header: 'Horizon', cell: (p) => `day ${p.horizon}`, rowHeader: true },
            { key: 'date', header: 'Date', cell: (p) => formatWeekdayDate(p.target_date) },
            {
              key: 'value',
              header: 'Projected requests/day',
              numeric: true,
              cell: (p) => formatInteger(p.prediction),
            },
            {
              key: 'holiday',
              header: 'Holiday',
              cell: (p) => p.federal_holiday ?? (p.holiday_related ? 'holiday-related' : ''),
            },
          ]}
        />
      </div>
    </details>
  );
}

/**
 * Every role each model holds, in words (a role is never told by colour alone): the
 * recommended model can also be the best baseline, which can be missing.
 */
function modelRoles(models: ModelsData): Map<string, string> {
  const roles = new Map<string, string>();
  for (const [id, role] of [
    [models.recommended_model, 'recommended'],
    [models.selection.best_baseline, 'best baseline'],
    [models.reference_model, 'reference'],
  ] as const) {
    if (id === null) continue;
    const known = roles.get(id);
    roles.set(id, known === undefined ? role : `${known}, ${role}`);
  }
  return roles;
}

/** A model's name with its roles, e.g. "ATLAS linear regression (recommended)". */
function nameWithRoles(id: string, roles: ReadonlyMap<string, string>): string {
  const role = roles.get(id);
  return role === undefined ? modelName(id) : `${modelName(id)} (${role})`;
}

function chosenModels(
  models: ModelsData,
): { id: string; label: string; color: string; style: 'solid' | 'dashed' | 'dotted' }[] {
  // Each model keeps its colour and line pattern (never colour alone) and every role it holds.
  const slots = [
    [models.recommended_model, 'var(--series-1)', 'solid'],
    [models.selection.best_baseline, 'var(--series-2)', 'dashed'],
    [models.reference_model, 'var(--series-3)', 'dotted'],
  ] as const;
  const roles = modelRoles(models);
  const chosen: {
    id: string;
    label: string;
    color: string;
    style: 'solid' | 'dashed' | 'dotted';
  }[] = [];
  for (const [id, color, style] of slots) {
    if (id === null || chosen.some((c) => c.id === id)) continue;
    chosen.push({ id, label: nameWithRoles(id, roles), color, style });
  }
  return chosen;
}

function HorizonPanel() {
  const [period, setPeriod] = useState<Period>('confirmation');
  const [metric, setMetric] = useState<Metric>('mae');
  const [view, setView] = useState<View>('chart');
  const metrics = useHorizonMetrics(period);
  const models = useModels();
  return (
    <Panel
      title="Accuracy by forecast horizon"
      subtitle="Rolling-origin backtest: the 30-day procedure re-run from every origin of the period, scored day by day"
      tags={<Tag tone="neutral">Historical backtest</Tag>}
      actions={<ViewToggle value={view} onChange={setView} label="Accuracy by forecast horizon" />}
    >
      <div className="filter-bar">
        <Segmented label="Period" value={period} options={PERIODS} onChange={setPeriod} />
        <Segmented label="Metric" value={metric} options={METRICS} onChange={setMetric} />
      </div>
      {period === 'legacy_window' && (
        <Callout tone="info" title="The legacy window is descriptive only">
          <p>
            It reproduces the window of the original project; horizons may be incomplete there.
            Selection and confirmation use the other two periods.
          </p>
        </Callout>
      )}
      <Async resource={models} label="the models">
        {(m) => (
          // Each period is its own answer: another period's rows are never shown in its place.
          <Async resource={metrics} label="the backtest metrics">
            {(shown) => (
              <HorizonContent
                rows={shown.data.rows.filter((r) => r.period === period)}
                models={m.data}
                metric={metric}
                view={view}
                evidence={<Evidence integrity={shown.integrity} provenance={shown.provenance} />}
              />
            )}
          </Async>
        )}
      </Async>
    </Panel>
  );
}

function HorizonContent({
  rows,
  models,
  metric,
  view,
  evidence,
}: {
  rows: readonly MetricRow[];
  models: ModelsData;
  metric: Metric;
  view: View;
  evidence: React.ReactNode;
}) {
  const chosen = chosenModels(models);
  const series: LineSeries[] = chosen.map((model) => ({
    id: model.id,
    label: model.label,
    color: model.color,
    style: model.style,
    markers: true,
    points: rows
      .filter((r) => r.model_id === model.id)
      .sort((a, b) => a.first_horizon - b.first_horizon)
      .map((r) => ({ x: r.first_horizon, y: r[metric] })),
  }));
  const ids = [...new Set(rows.map((r) => r.model_id))];
  const horizons = [...new Set(rows.map((r) => r.first_horizon))].sort((a, b) => a - b);
  const value = (model: string, h: number) =>
    rows.find((r) => r.model_id === model && r.first_horizon === h)?.[metric] ?? null;
  return (
    <div className="stack-3">
      {view === 'chart' ? (
        <LineChart
          label={`Backtest ${METRIC_TEXT[metric]} by horizon`}
          summary={`${METRIC_TEXT[metric]} at horizons 1 to 30 for ${chosen.map((m) => m.label).join(', ')}. Lower absolute values are more accurate.`}
          series={series}
          xType="linear"
          formatValue={(v) => `${formatInteger(v)} requests/day`}
          formatTick={formatCompact}
          formatX={(x) => `Horizon ${x} (${x === 1 ? '1 day' : `${x} days`} after the origin)`}
          xTickFormat={(x) => `${x}`}
          height={260}
          {...(metric === 'bias'
            ? {}
            : {
                yDomain: [
                  0,
                  Math.max(1, ...series.flatMap((s) => s.points.map((p) => p.y ?? 0))),
                ] as const,
              })}
        />
      ) : (
        <DataTable
          caption={`Backtest ${METRIC_TEXT[metric]} by horizon, every model`}
          rows={horizons}
          rowKey={(h) => String(h)}
          highlight={() => false}
          columns={[
            { key: 'h', header: 'Horizon', rowHeader: true, cell: (h) => `${h}` },
            ...ids.map((id) => ({
              key: id,
              header: modelName(id),
              numeric: true,
              cell: (h: number) => formatNumber(value(id, h), 0),
            })),
          ]}
        />
      )}
      <p className="xsmall muted">
        Horizon h: the forecast for the h-th day after the origin, made from data up to the origin.
        Recursive models feed their own forecasts back in; baselines forecast each horizon directly.
      </p>
      {evidence}
    </div>
  );
}

function ModelComparisonPanel() {
  const models = useModels();
  const buckets = useBucketMetrics();
  const [period, setPeriod] = useState<Period>('confirmation');
  return (
    <Panel
      title="Models against baselines"
      subtitle="Mean absolute error over horizons 1-30 (requests/day): lower is better"
      tags={<Tag tone="neutral">Historical backtest</Tag>}
    >
      <Segmented label="Period" value={period} options={PERIODS} onChange={setPeriod} />
      <Async resource={models} label="the models">
        {({ data, integrity, provenance }) => {
          const ranked = [...data.models].sort(
            (a, b) =>
              (a.mae_days_1_30_by_period[period] ?? Infinity) -
              (b.mae_days_1_30_by_period[period] ?? Infinity),
          );
          const roles = modelRoles(data);
          const bars = ranked.map((m) => {
            const role = roles.get(m.model_id);
            return {
              id: m.model_id,
              label: modelName(m.model_id),
              value: m.mae_days_1_30_by_period[period] ?? null,
              color:
                m.model_id === data.recommended_model
                  ? 'var(--series-1)'
                  : m.model_id === data.selection.best_baseline
                    ? 'var(--series-2)'
                    : 'var(--neutral-mark)',
              notes: [`${m.family}, ${m.procedure} forecasts`, m.description],
              // The role in words after the value (and in the reading), not only the colour.
              ...(role !== undefined ? { suffix: role } : {}),
            };
          });
          return (
            <div className="stack-3">
              <BarChart
                label={`Mean absolute error over horizons 1-30, ${period} period`}
                summary={ranked
                  .map(
                    (m) =>
                      `${nameWithRoles(m.model_id, roles)}: ${formatInteger(m.mae_days_1_30_by_period[period] ?? null)} requests/day`,
                  )
                  .join('; ')}
                valueLabel="MAE, horizons 1-30"
                formatValue={(v) => formatInteger(v)}
                rows={bars}
                // Only the colours drawn: a model holding two roles has the first one's colour.
                legend={(
                  [
                    { label: 'Recommended model', color: 'var(--series-1)', kind: 'swatch' },
                    {
                      label: 'Best baseline (development)',
                      color: 'var(--series-2)',
                      kind: 'swatch',
                    },
                    { label: 'Other models', color: 'var(--neutral-mark)', kind: 'swatch' },
                  ] as const
                ).filter((item) => bars.some((bar) => bar.color === item.color))}
              />
              <Async resource={buckets} label="the metrics by horizon bucket">
                {(b) => (
                  <details className="disclosure">
                    <summary>Every metric by horizon bucket ({period})</summary>
                    <div className="disclosure-body">
                      <DataTable
                        caption={`Backtest metrics by horizon bucket, ${period} period. MAE, RMSE and bias in requests/day; bias = prediction - actual. MAPE is secondary.`}
                        rows={b.data.rows.filter((r) => r.period === period)}
                        rowKey={(r) => `${r.model_id}:${r.label}`}
                        highlight={(r) => r.model_id === data.recommended_model}
                        columns={[
                          {
                            key: 'model',
                            header: 'Model',
                            rowHeader: true,
                            cell: (r) => nameWithRoles(r.model_id, roles),
                          },
                          {
                            key: 'bucket',
                            header: 'Horizons',
                            cell: (r) =>
                              r.first_horizon === r.last_horizon
                                ? `${r.first_horizon}`
                                : `${r.first_horizon}-${r.last_horizon}`,
                          },
                          { key: 'n', header: 'n', numeric: true, cell: (r) => formatInteger(r.n) },
                          {
                            key: 'mae',
                            header: 'MAE',
                            numeric: true,
                            cell: (r) => formatInteger(r.mae),
                          },
                          {
                            key: 'rmse',
                            header: 'RMSE',
                            numeric: true,
                            cell: (r) => formatInteger(r.rmse),
                          },
                          {
                            key: 'bias',
                            header: 'Bias',
                            numeric: true,
                            cell: (r) => formatSigned(r.bias),
                          },
                          {
                            key: 'wape',
                            header: 'WAPE',
                            numeric: true,
                            cell: (r) => formatPct(r.wape_pct),
                          },
                          {
                            key: 'mase',
                            header: 'MASE',
                            numeric: true,
                            cell: (r) => formatNumber(r.mase, 2),
                          },
                          {
                            key: 'mape',
                            header: 'MAPE',
                            numeric: true,
                            cell: (r) => formatPct(r.mape_pct),
                          },
                        ]}
                      />
                      <ul className="notice-list">
                        {b.data.notes.map((note) => (
                          <li key={note}>{note}</li>
                        ))}
                      </ul>
                    </div>
                  </details>
                )}
              </Async>
              <Evidence integrity={integrity} provenance={provenance} />
            </div>
          );
        }}
      </Async>
    </Panel>
  );
}

function TestsPanel() {
  const models = useModels();
  return (
    <Panel
      title="Is the model better than the baselines?"
      subtitle="Pre-registered confirmation tests (Diebold-Mariano with HAC variances, Holm-adjusted)"
    >
      <Async resource={models} label="the statistical tests">
        {({ data, provenance }) => {
          const h1 = h1Test(data.primary_tests);
          return (
            <div className="stack-3">
              {h1 !== undefined && (
                <SignificanceCallout test={h1} multiplicity={data.multiplicity} />
              )}
              <DataTable
                caption="Primary confirmation tests. Differences are model minus reference, in requests/day; negative means the model is more accurate."
                rows={data.primary_tests}
                rowKey={(t) => t.hypothesis}
                columns={[
                  { key: 'h', header: 'Hypothesis', rowHeader: true, cell: (t) => t.hypothesis },
                  {
                    key: 'mae',
                    header: 'Model MAE',
                    numeric: true,
                    cell: (t) => formatInteger(t.mae),
                  },
                  {
                    key: 'ref',
                    header: 'Reference MAE',
                    numeric: true,
                    cell: (t) => formatInteger(t.mae_reference),
                  },
                  {
                    key: 'diff',
                    header: 'Difference',
                    numeric: true,
                    cell: (t) => formatSigned(t.mae_difference),
                  },
                  {
                    key: 'ci',
                    header: 'Interval',
                    numeric: true,
                    cell: (t) =>
                      `${formatSigned(t.mae_difference_ci_low)} to ${formatSigned(t.mae_difference_ci_high)}`,
                  },
                  {
                    key: 'p',
                    header: 'Holm p',
                    numeric: true,
                    cell: (t) => formatP(t.holm_adjusted_p_value),
                  },
                  {
                    key: 'sig',
                    header: 'Significant',
                    cell: (t) =>
                      t.significant_after_holm === true ? (
                        <Tag tone="good">Yes</Tag>
                      ) : t.significant_after_holm === false ? (
                        <Tag tone="serious">No</Tag>
                      ) : (
                        'not computed'
                      ),
                  },
                  {
                    key: 'ess',
                    header: 'Effective n',
                    numeric: true,
                    cell: (t) => formatNumber(t.effective_sample_size, 1),
                  },
                ]}
              />
              <dl className="kv">
                <dt>Recommended</dt>
                <dd>
                  {data.recommended_model}: {data.recommendation_reason}
                </dd>
                <dt>Selected on development</dt>
                <dd>
                  {data.selection.selected_model}
                  {data.selection.reason !== null && `: ${data.selection.reason}`}
                </dd>
                {data.selection.rule !== null && (
                  <>
                    <dt>Pre-registered rule</dt>
                    <dd>{data.selection.rule}</dd>
                  </>
                )}
              </dl>
              <ProvenanceFooter provenance={provenance} />
            </div>
          );
        }}
      </Async>
    </Panel>
  );
}

function ReproducibilityPanel() {
  const metrics = useBucketMetrics();
  return (
    <Panel
      title="Reproducibility"
      subtitle="The protocol's periods and the run every metric comes from"
    >
      <Async resource={metrics} label="the backtest record">
        {({ data, provenance }) => (
          <div className="stack-3">
            <DataTable
              caption="M2 periods. Each origin re-fits every model on data up to that day and forecasts the next 30 days."
              rows={data.periods}
              rowKey={(p) => p.name}
              columns={[
                { key: 'name', header: 'Period', rowHeader: true, cell: (p) => humanise(p.name) },
                { key: 'role', header: 'Role', cell: (p) => p.role },
                {
                  key: 'origins',
                  header: 'Origins',
                  cell: (p) => `${formatDate(p.first_origin)} – ${formatDate(p.last_origin)}`,
                },
                {
                  key: 'targets',
                  header: 'Target days',
                  cell: (p) => `${formatDate(p.first_target)} – ${formatDate(p.last_target)}`,
                },
                {
                  key: 'n',
                  header: 'n origins',
                  numeric: true,
                  cell: (p) => formatInteger(p.n_origins),
                },
              ]}
            />
            <dl className="kv">
              <dt>Experiment</dt>
              <dd className="mono">{data.experiment_id}</dd>
              <dt>Recommended model</dt>
              <dd>
                {modelName(data.recommended_model)}{' '}
                <span className="mono">({data.recommended_model})</span>
              </dd>
              <dt>Reference model</dt>
              <dd>
                {modelName(data.reference_model)}{' '}
                <span className="mono">({data.reference_model})</span>
              </dd>
            </dl>
            <p className="small secondary">
              The run is reproduced byte for byte by <code>atlas backtest verify</code>; its
              configuration, data and result fingerprints are below.
            </p>
            <ProvenanceFooter provenance={provenance} />
          </div>
        )}
      </Async>
    </Panel>
  );
}

export default function Forecasting() {
  return (
    <>
      <PageHeader eyebrow="M2 · Forecasting and backtesting" title="Forecasting">
        <p>
          The 30-day forecast of city-wide 311 requests, the archived projection it produced on the
          last observed day, and how the same procedure performed when re-run from hundreds of past
          origins against simple baselines.
        </p>
      </PageHeader>
      <ProjectionPanel />
      <HorizonPanel />
      <ModelComparisonPanel />
      <TestsPanel />
      <ReproducibilityPanel />
    </>
  );
}
