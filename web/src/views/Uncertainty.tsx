import { useState } from 'react';

import type {
  ForecastData,
  HistoryData,
  IntervalsData,
  UncertaintyStatusData,
} from '../api/schemas';
import { LineChart } from '../charts/LineChart';
import { RangeDotChart } from '../charts/RangeDotChart';
import { Callout } from '../components/Callout';
import { ViewToggle, type View } from '../components/Controls';
import { DataTable } from '../components/DataTable';
import { Panel } from '../components/Panel';
import { Evidence, ProvenanceFooter } from '../components/Provenance';
import { Async } from '../components/States';
import { IntervalStatusTag, NotValidatedTag, Tag } from '../components/Tag';
import {
  dateValue,
  formatCompact,
  formatDate,
  formatInteger,
  formatNumber,
  formatShare,
  formatWeekdayDate,
  humanise,
  isoDay,
} from '../lib/format';
import { PageHeader } from './common';
import { useForecast, useHistory, useIntervals, useUncertaintyStatus } from './data';
import { OBSERVED_COLOR, PROJECTED_COLOR } from './series';

/** The quantile levels of a band, as the API keys them. */
const BANDS = [
  { low: '0.1', high: '0.9', fill: 'var(--series-2-wash-strong)' },
  { low: '0.025', high: '0.975', fill: 'var(--series-2-wash)' },
] as const;

function StatusPanel() {
  const status = useUncertaintyStatus();
  return (
    <Async resource={status} label="the calibration status">
      {({ data, integrity, provenance }) => (
        <Panel
          title={`Interval status: ${data.interval_status.toUpperCase()}`}
          subtitle={`Pre-registered evaluation of the sealed method ${data.selected_pair} for ${data.primary_model}`}
          tags={<IntervalStatusTag status={data.interval_status} />}
        >
          <Callout tone={data.decision_grade ? 'info' : 'critical'} title={data.statement}>
            {data.failure_reasons.length > 0 && (
              <>
                <p>The recorded reasons:</p>
                <ul>
                  {data.failure_reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </>
            )}
            {data.note !== null && <p>{data.note}</p>}
          </Callout>
          <div className="prose small">
            <p>
              <strong>What this means.</strong> A nominal 80 % interval should contain the outcome
              about 80 % of the time, and the evidence for that must be strong enough to rely on.
              ATLAS pre-registered both conditions before looking at the evaluation period. The
              bands below are the method's honest output, but they <strong>failed</strong> that
              test, so they are not decision-grade: no band or probability on this page is a
              statistical guarantee, and none should be used to size staff or budgets.
            </p>
          </div>
          <Evidence integrity={integrity} provenance={provenance} />
        </Panel>
      )}
    </Async>
  );
}

function BandChartPanel() {
  const intervals = useIntervals();
  const history = useHistory(null);
  const forecast = useForecast();
  return (
    <Panel
      title="The projection with its uncalibrated bands"
      subtitle="Recent observed days, then the archived projection and the marginal quantiles of each projected day (requests/day)"
      tags={
        <span className="tag-row">
          {intervals.data !== undefined && (
            <IntervalStatusTag status={intervals.data.data.interval_status} />
          )}
          <Tag tone="projected">Archived projection</Tag>
          <NotValidatedTag />
        </span>
      }
    >
      <Async resource={intervals} label="the intervals">
        {(i) => (
          <Async resource={history} label="the observed history">
            {(h) => (
              <Async resource={forecast} label="the forecast">
                {(f) => (
                  <BandChart
                    intervals={i.data}
                    history={h.data}
                    forecast={f.data}
                    evidence={<Evidence integrity={i.integrity} provenance={i.provenance} />}
                  />
                )}
              </Async>
            )}
          </Async>
        )}
      </Async>
    </Panel>
  );
}

function BandChart({
  intervals,
  history,
  forecast,
  evidence,
}: {
  intervals: IntervalsData;
  history: HistoryData;
  forecast: ForecastData;
  evidence: React.ReactNode;
}) {
  const [view, setView] = useState<View>('chart');
  const recent = history.points.slice(-45);
  const holidayRelated = new Set(
    intervals.points.filter((p) => p.holiday_related).map((p) => dateValue(p.target_date)),
  );
  const status = intervals.interval_status.toUpperCase();
  const calibrated = intervals.interval_status === 'decision_grade';
  const bands = BANDS.map((band) => ({
    id: `${band.low}-${band.high}`,
    label: `Marginal ${band.low}-${band.high} quantiles (${status})`,
    fill: band.fill,
    points: intervals.points.flatMap((p) => {
      const low = p.quantiles[band.low];
      const high = p.quantiles[band.high];
      return low === undefined || high === undefined
        ? []
        : [{ x: dateValue(p.target_date), low, high }];
    }),
  }));
  return (
    <div className="stack-3">
      <Callout
        tone={calibrated ? 'info' : 'critical'}
        title={
          calibrated
            ? `${status}: calibrated on the evaluation period`
            : `${status}: read these bands as illustrations, not guarantees`
        }
      >
        <p>
          {intervals.label} Holiday-related days (diamonds) form an M3 stratum where coverage was
          markedly lower still.
        </p>
      </Callout>
      <div className="chart-toolbar">
        <span className="small secondary">
          Origin {formatDate(intervals.origin)} · method {intervals.pair_id}
        </span>
        <ViewToggle value={view} onChange={setView} label="Projection with uncalibrated bands" />
      </div>
      {view === 'chart' ? (
        <LineChart
          label="Archived projection with uncalibrated marginal quantile bands"
          summary={`The last ${recent.length} observed days, then the archived projection from ${formatDate(intervals.origin)} with two shaded bands of marginal quantiles. Their calibration failed: they are not reliable intervals.`}
          series={[
            {
              id: 'observed',
              label: 'Observed requests/day',
              color: OBSERVED_COLOR,
              points: recent.map((p) => ({ x: dateValue(p.date), y: p.requests })),
            },
            {
              id: 'projected',
              label: 'Archived projection (point forecast)',
              color: PROJECTED_COLOR,
              style: 'dashed',
              points: intervals.points.map((p) => ({
                x: dateValue(p.target_date),
                y: p.prediction,
              })),
            },
          ]}
          bands={bands}
          marks={intervals.points
            .filter((p) => p.holiday_related)
            .map((p) => ({
              x: dateValue(p.target_date),
              y: p.prediction,
              label: 'holiday-related day (lower M3 coverage)',
            }))}
          marksLabel="Holiday-related day"
          rules={[
            { x: dateValue(forecast.origin), label: `Origin ${formatDate(forecast.origin)}` },
          ]}
          formatValue={(v) => formatInteger(v)}
          formatTick={formatCompact}
          formatX={(x) => formatWeekdayDate(isoDay(x))}
          notesAt={(x) =>
            x > dateValue(intervals.origin)
              ? [
                  `Archived projection; bands ${status}`,
                  ...(holidayRelated.has(x)
                    ? ['Holiday-related: coverage markedly lower in M3']
                    : []),
                ]
              : ['Observed']
          }
          height={300}
        />
      ) : (
        <DataTable
          caption={`Marginal quantiles of each projected day (requests/day). Interval status ${status}${calibrated ? '' : ': not calibrated quantiles, no probability to rely on'}.`}
          rows={intervals.points}
          rowKey={(p) => p.target_date}
          columns={[
            {
              key: 'date',
              header: 'Date',
              rowHeader: true,
              cell: (p) => formatWeekdayDate(p.target_date),
            },
            { key: 'h', header: 'Horizon', numeric: true, cell: (p) => p.horizon },
            ...['0.025', '0.1', '0.5', '0.9', '0.975'].map((level) => ({
              key: level,
              header: `q${level}`,
              numeric: true,
              cell: (p: { quantiles: Record<string, number> }) =>
                formatInteger(p.quantiles[level] ?? null),
            })),
            {
              key: 'point',
              header: 'Point forecast',
              numeric: true,
              cell: (p) => formatInteger(p.prediction),
            },
            {
              key: 'holiday',
              header: 'Holiday-related',
              cell: (p) => (p.holiday_related ? 'yes' : ''),
            },
          ]}
        />
      )}
      {evidence}
    </div>
  );
}

function CoveragePanel() {
  const status = useUncertaintyStatus();
  return (
    <Panel
      title="Why the intervals are not decision-grade"
      subtitle="Observed coverage on the evaluation period, with its confidence interval, against the nominal level and the pre-registered tolerance"
      tags={<Tag tone="neutral">Calibration assessment</Tag>}
    >
      <Async resource={status} label="the coverage evidence">
        {({ data, provenance }) => (
          <CoverageContent data={data} provenance={<ProvenanceFooter provenance={provenance} />} />
        )}
      </Async>
    </Panel>
  );
}

function CoverageContent({
  data,
  provenance,
}: {
  data: UncertaintyStatusData;
  provenance: React.ReactNode;
}) {
  const [view, setView] = useState<View>('chart');
  const levels = data.interval_levels;
  const shares = Object.entries(data.evaluation_share_at_or_below_quantile_days_1_30)
    .map(([level, share]) => ({ level: Number(level), share }))
    .filter((row) => Number.isFinite(row.level))
    .sort((a, b) => a.level - b.level);
  return (
    <div className="stack-4">
      <div className="chart-toolbar">
        <p className="small secondary">
          Two conditions, both pre-registered: coverage within tolerance of the nominal level,{' '}
          <em>and</em> enough independent evidence (an effective sample size of at least 30) to
          tell.
        </p>
        <ViewToggle value={view} onChange={setView} label="Interval coverage" />
      </div>
      {view === 'chart' ? (
        <RangeDotChart
          label="Observed interval coverage against the nominal level"
          summary={levels
            .map(
              (l) =>
                `Nominal ${formatShare(l.level, 0)}: observed ${formatShare(l.coverage)} (interval ${formatShare(l.coverage_ci_low)} to ${formatShare(l.coverage_ci_high)}), effective sample size ${formatNumber(l.effective_sample_size, 1)}`,
            )
            .join('; ')}
          rows={levels.map((l) => ({
            id: String(l.level),
            label: `${formatShare(l.level, 0)} interval`,
            value: l.coverage,
            low: l.coverage_ci_low,
            high: l.coverage_ci_high,
            target: l.level,
            tolerance: l.tolerance_pct === null ? null : l.tolerance_pct / 100,
            notes: [
              `Effective sample size ${formatNumber(l.effective_sample_size, 1)}${l.evidence_sufficient === false ? ' (insufficient: fewer than 30)' : ''}`,
              ...(l.scopes_outside_tolerance.length > 0
                ? [`Outside tolerance in: ${l.scopes_outside_tolerance.join(', ')}`]
                : []),
            ],
          }))}
          domain={[0.5, 1]}
          formatValue={(v) => formatShare(v, 0)}
        />
      ) : (
        <DataTable
          caption="Interval coverage evidence on the evaluation period, horizons 1-30"
          rows={levels}
          rowKey={(l) => String(l.level)}
          columns={[
            {
              key: 'level',
              header: 'Nominal',
              rowHeader: true,
              cell: (l) => formatShare(l.level, 0),
            },
            {
              key: 'coverage',
              header: 'Observed',
              numeric: true,
              cell: (l) => formatShare(l.coverage),
            },
            {
              key: 'ci',
              header: 'Interval',
              numeric: true,
              cell: (l) => `${formatShare(l.coverage_ci_low)} – ${formatShare(l.coverage_ci_high)}`,
            },
            {
              key: 'tol',
              header: 'Tolerance',
              numeric: true,
              cell: (l) =>
                l.tolerance_pct === null ? '–' : `±${formatNumber(l.tolerance_pct, 0)} pp`,
            },
            {
              key: 'ess',
              header: 'Effective n',
              numeric: true,
              cell: (l) => formatNumber(l.effective_sample_size, 1),
            },
            {
              key: 'enough',
              header: 'Enough evidence',
              cell: (l) =>
                l.evidence_sufficient === true
                  ? 'yes'
                  : l.evidence_sufficient === false
                    ? 'no'
                    : 'not computed',
            },
            {
              key: 'outside',
              header: 'Outside tolerance in',
              cell: (l) => l.scopes_outside_tolerance.join(', ') || 'none',
            },
          ]}
        />
      )}
      <h3>Are the quantiles where they should be?</h3>
      <p className="small secondary">
        For each nominal quantile level, the share of evaluation outcomes at or below that quantile
        (horizons 1-30). With calibrated quantiles every point would sit on the dotted diagonal.
      </p>
      {view === 'chart' ? (
        <LineChart
          label="Share of outcomes at or below each nominal quantile"
          summary={shares.map((s) => `Quantile ${s.level}: ${formatShare(s.share)}`).join('; ')}
          xType="linear"
          series={[
            {
              id: 'share',
              label: 'Observed share at or below the quantile',
              color: 'var(--series-1)',
              markers: true,
              points: shares.map((s) => ({ x: s.level, y: s.share })),
            },
          ]}
          diagonal
          xDomain={[0, 1]}
          yDomain={[0, 1]}
          legendExtra={[
            { label: 'Perfect calibration', color: 'var(--ink-muted)', kind: 'dashed' },
          ]}
          formatValue={(v) => formatShare(v)}
          formatTick={(v) => formatShare(v, 0)}
          formatX={(x) => `Nominal quantile ${x}`}
          xTickFormat={(x) => `${x}`}
          height={260}
        />
      ) : (
        <DataTable
          caption="Share of evaluation outcomes at or below each nominal quantile"
          rows={shares}
          rowKey={(s) => String(s.level)}
          columns={[
            { key: 'level', header: 'Nominal quantile', rowHeader: true, cell: (s) => s.level },
            {
              key: 'share',
              header: 'Observed share at or below',
              numeric: true,
              cell: (s) => formatShare(s.share),
            },
          ]}
        />
      )}
      {provenance}
    </div>
  );
}

function ExceedancePanel() {
  const status = useUncertaintyStatus();
  return (
    <Panel
      title="Threshold-exceedance probabilities"
      subtitle="M3 also scored the probability that daily demand exceeds a threshold: the recorded statuses"
    >
      <Async resource={status} label="the exceedance statuses">
        {({ data }) => (
          <DataTable
            caption="Exceedance statuses. The capacities are HYPOTHETICAL thresholds, not real capacities; the trailing-180-day 75th percentile is a demand percentile, never a capacity."
            rows={Object.entries(data.exceedance_status)}
            rowKey={([name]) => name}
            columns={[
              {
                key: 'name',
                header: 'Threshold',
                rowHeader: true,
                cell: ([name]) => humanise(name),
              },
              {
                key: 'status',
                header: 'Status',
                cell: ([, value]) => (
                  <Tag
                    tone={
                      value === 'decision_grade'
                        ? 'good'
                        : value === 'failed_calibration'
                          ? 'critical'
                          : 'serious'
                    }
                  >
                    {value.toUpperCase()}
                  </Tag>
                ),
              },
              {
                key: 'reasons',
                header: 'Recorded reasons',
                cell: ([name]) => (data.exceedance_reasons[name] ?? []).join('; '),
              },
            ]}
          />
        )}
      </Async>
    </Panel>
  );
}

export default function Uncertainty() {
  return (
    <>
      <PageHeader
        eyebrow="M3 · Prediction intervals and probabilistic risk"
        title="Uncertainty and risk"
      >
        <p>
          How uncertain is the projection? ATLAS built horizon-specific intervals from past forecast
          errors and tested their calibration with a pre-registered, dependence-aware protocol. They
          failed that test, and this page shows what failing means rather than hiding it.
        </p>
      </PageHeader>
      <StatusPanel />
      <BandChartPanel />
      <CoveragePanel />
      <ExceedancePanel />
    </>
  );
}
