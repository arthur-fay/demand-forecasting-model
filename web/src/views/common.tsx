/** View-level building blocks shared by several pages. */
import { useState } from 'react';

import type { ForecastData, HistoryData, Integrity, PrimaryTest } from '../api/schemas';
import { LineChart } from '../charts/LineChart';
import { Callout } from '../components/Callout';
import { ViewToggle, type View } from '../components/Controls';
import { DataTable } from '../components/DataTable';
import { Tag } from '../components/Tag';
import {
  dateValue,
  formatCompact,
  formatDate,
  formatInteger,
  formatP,
  formatSigned,
  formatWeekdayDate,
  isoDay,
  modelName,
} from '../lib/format';
import { holidayMarks, observedSeries, projectedSeries } from './series';

export function PageHeader({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <header className="page-header">
      <p className="eyebrow">{eyebrow}</p>
      <h1>{title}</h1>
      {children !== undefined && <div className="lead">{children}</div>}
    </header>
  );
}

/** The archived-projection warning: shown wherever the projection is. */
export function ArchivedProjectionCallout({ forecast }: { forecast: ForecastData }) {
  const first = forecast.points[0]?.target_date;
  const last = forecast.points[forecast.points.length - 1]?.target_date;
  return (
    <Callout tone="warning" title="An archived projection, not a live forecast">
      <p>
        This forecast was made from data up to {formatDate(forecast.origin)} and covers{' '}
        {first !== undefined && last !== undefined
          ? `${formatDate(first)} to ${formatDate(last)}`
          : 'the following days'}
        . Those days are not in the frozen snapshot, so it has never been scored against what
        happened, and it says nothing about today. Its historical accuracy (M2 confirmation period,
        horizons 1-30): mean absolute error{' '}
        {formatInteger(forecast.accuracy_context.mae_requests_per_day)} requests/day, bias{' '}
        {formatSigned(forecast.accuracy_context.bias_requests_per_day)} requests/day (
        {forecast.accuracy_context.bias_sign}).
      </p>
    </Callout>
  );
}

/** H1 (recommended model against the best baseline): the significance verdict, as reported. */
export function SignificanceCallout({
  test,
  multiplicity,
}: {
  test: PrimaryTest;
  multiplicity: string | null;
}) {
  const significant = test.significant_after_holm;
  const low = test.mae_difference_ci_low;
  const high = test.mae_difference_ci_high;
  const interval =
    low === null || high === null
      ? null
      : low <= 0 && high >= 0
        ? 'The interval includes zero.'
        : 'The interval excludes zero.';
  return (
    <Callout
      tone={significant === true ? 'info' : 'serious'}
      title={
        significant === true
          ? `${test.hypothesis}: significant after the Holm adjustment`
          : significant === false
            ? `${test.hypothesis}: not statistically significant after the Holm adjustment`
            : `${test.hypothesis}: significance was not computed`
      }
    >
      <p>
        {modelName(test.model_id)} minus {modelName(test.reference)}, mean absolute error over
        horizons {test.first_horizon}-{test.last_horizon} ({test.period} period):{' '}
        {formatSigned(test.mae_difference)} requests/day, interval{' '}
        {formatSigned(test.mae_difference_ci_low)} to {formatSigned(test.mae_difference_ci_high)},
        Holm-adjusted p {formatP(test.holm_adjusted_p_value)}.{interval !== null && ` ${interval}`}
        {significant !== true && ' The lead over the best baseline is not established.'}
      </p>
      {multiplicity !== null && <p className="muted">{multiplicity}</p>}
    </Callout>
  );
}

export function IntervalStatusCallout({
  integrity,
  statement,
}: {
  integrity: Integrity;
  statement?: string;
}) {
  if (integrity.uncertainty_interval_status === 'decision_grade') return null;
  return (
    <Callout
      tone="critical"
      title={`Uncertainty status: ${integrity.uncertainty_interval_status.toUpperCase()}`}
    >
      <p>
        {statement ??
          'The M3 prediction intervals failed their pre-registered calibration criteria: no interval, band or probability shown here is a reliable statistical guarantee.'}
      </p>
    </Callout>
  );
}

const WINDOW_LABEL = 'Observed history and the archived 30-day projection';

/** History (observed) joined to the projection (projected) by date, styles kept apart. */
export function HistoryProjectionChart({
  history,
  forecast,
  horizon,
  stale = false,
  height,
}: {
  history: HistoryData;
  forecast: ForecastData;
  horizon: number;
  stale?: boolean;
  height?: number;
}) {
  const [view, setView] = useState<View>('chart');
  const projected = forecast.points.filter((p) => p.horizon <= horizon);
  const observed = history.points;
  const lastObserved = observed[observed.length - 1];
  const summary =
    `Observed city-wide 311 requests per day from ${formatDate(history.start)} to ${formatDate(history.end)}` +
    (lastObserved?.requests != null
      ? ` (last observed day: ${formatInteger(lastObserved.requests)})`
      : '') +
    `, then the archived projection for ${projected.length} days from ${formatDate(forecast.origin)}, dashed.`;
  const holidays = new Map<number, string>();
  for (const p of observed)
    if (p.holiday_related) holidays.set(dateValue(p.date), 'Holiday-related day (M3 stratum)');
  for (const p of projected)
    if (p.holiday_related)
      holidays.set(
        dateValue(p.target_date),
        'Holiday-related day: interval coverage was markedly lower here in M3',
      );
  return (
    <div className="stack-3">
      <div className="chart-toolbar">
        <span className="tag-row">
          <Tag tone="observed">Observed to {formatDate(history.end)}</Tag>
          <Tag tone="projected">Projected from {formatDate(forecast.origin)}</Tag>
        </span>
        <ViewToggle value={view} onChange={setView} label={WINDOW_LABEL} />
      </div>
      {view === 'chart' ? (
        <LineChart
          label={WINDOW_LABEL}
          summary={summary}
          series={[observedSeries(observed), projectedSeries(projected)]}
          marks={holidayMarks(observed, projected)}
          rules={[
            {
              x: dateValue(forecast.origin),
              label: `Forecast origin ${formatDate(forecast.origin)}`,
            },
          ]}
          formatValue={(v) => `${formatInteger(v)} requests/day`}
          formatTick={formatCompact}
          formatX={(x) => formatWeekdayDate(isoDay(x))}
          notesAt={(x) => {
            const note = holidays.get(x);
            const kind =
              x > dateValue(forecast.origin)
                ? 'Archived projection: not observed, not validated'
                : 'Observed';
            return note === undefined ? [kind] : [kind, note];
          }}
          stale={stale}
          {...(height !== undefined ? { height } : {})}
        />
      ) : (
        <DataTable
          caption={`${WINDOW_LABEL} (requests/day). Status says which rows are observed and which are projected.`}
          rows={[
            ...observed.map((p) => ({
              date: p.date,
              value: p.requests,
              status: p.status === 'missing' ? 'missing' : 'observed',
              holiday: p.federal_holiday,
            })),
            ...projected.map((p) => ({
              date: p.target_date,
              value: p.prediction,
              status: 'projected',
              holiday: p.federal_holiday,
            })),
          ]}
          rowKey={(row) => row.date}
          columns={[
            {
              key: 'date',
              header: 'Date',
              cell: (row) => formatWeekdayDate(row.date),
              rowHeader: true,
            },
            { key: 'status', header: 'Status', cell: (row) => row.status },
            {
              key: 'value',
              header: 'Requests/day',
              cell: (row) => formatInteger(row.value),
              numeric: true,
            },
            { key: 'holiday', header: 'Federal holiday', cell: (row) => row.holiday ?? '' },
          ]}
        />
      )}
    </div>
  );
}
