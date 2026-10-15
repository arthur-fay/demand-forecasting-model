import { api } from '../api/endpoints';
import { useResource } from '../api/resource';
import type { Integrity } from '../api/schemas';
import { Callout } from '../components/Callout';
import { Stat } from '../components/Controls';
import { DataTable } from '../components/DataTable';
import { Panel } from '../components/Panel';
import { Evidence, ProvenanceFooter } from '../components/Provenance';
import { Async } from '../components/States';
import {
  DiagnosticTag,
  EquallyScoredTag,
  HypotheticalTag,
  IntervalStatusTag,
  NotValidatedTag,
  Tag,
} from '../components/Tag';
import { useDeployment } from '../deployment';
import {
  formatDate,
  formatInteger,
  formatP,
  formatPct,
  formatSigned,
  formatUsd,
  humanise,
  shortHash,
} from '../lib/format';
import { Link } from '../router';
import { ArchivedProjectionCallout, HistoryProjectionChart, PageHeader } from './common';
import {
  h1Test,
  useForecast,
  useHistory,
  useModels,
  useScenarios,
  useUncertaintyStatus,
} from './data';

function StatusStrip() {
  const forecast = useForecast();
  const models = useModels();
  const status = useUncertaintyStatus();
  const scenarios = useScenarios();
  return (
    <section aria-labelledby="status-heading" className="stack-3">
      <h2 id="status-heading">Scientific status</h2>
      <div className="stat-grid">
        <Async resource={forecast} label="the forecast">
          {({ data }) => (
            <Stat
              label="Point forecast accuracy (historical)"
              value={formatInteger(data.accuracy_context.mae_requests_per_day)}
              unit="requests/day MAE"
              tags={<NotValidatedTag />}
              note={`M2 confirmation period, horizons 1-30, ${formatInteger(data.accuracy_context.n_predictions)} re-forecasts. Not prospectively validated.`}
            />
          )}
        </Async>
        <Async resource={models} label="the model comparison">
          {({ data }) => {
            const h1 = h1Test(data.primary_tests);
            if (h1 === undefined)
              return <Stat label="Model against best baseline" value="not reported" />;
            return (
              <Stat
                label="Model lead over the best baseline (H1)"
                value={formatSigned(h1.mae_difference)}
                unit="requests/day"
                tags={
                  h1.significant_after_holm === true ? (
                    <Tag tone="good">Significant after Holm</Tag>
                  ) : h1.significant_after_holm === false ? (
                    <Tag tone="serious">Not significant after Holm</Tag>
                  ) : (
                    <Tag tone="neutral">Significance not computed</Tag>
                  )
                }
                note={`Interval ${formatSigned(h1.mae_difference_ci_low)} to ${formatSigned(h1.mae_difference_ci_high)}, Holm-adjusted p ${formatP(h1.holm_adjusted_p_value)}.`}
              />
            );
          }}
        </Async>
        <Async resource={status} label="the uncertainty status">
          {({ data }) => (
            <Stat
              label="Prediction intervals (M3)"
              // The words in the tile (they wrap); the exact status, FAILED_CALIBRATION, is the tag.
              value={humanise(data.interval_status)}
              kind="status"
              tags={<IntervalStatusTag status={data.interval_status} />}
              note={
                data.decision_grade
                  ? 'Decision-grade.'
                  : 'Not decision-grade: no interval or probability is a guarantee.'
              }
            />
          )}
        </Async>
        <Async resource={scenarios} label="the reference scenarios">
          {({ data }) => {
            const strict = data.scenarios.filter((s) => s.feasibility === 'strict').length;
            return (
              <Stat
                label="Decision scenarios (M4)"
                value={`${data.scenarios.length} scenarios`}
                tags={<HypotheticalTag />}
                note={`${strict} meet every HYPOTHETICAL constraint; ${data.scenarios.length - strict} cannot, and report DIAGNOSTIC plans.`}
              />
            );
          }}
        </Async>
      </div>
    </section>
  );
}

function SnapshotPanel() {
  const history = useHistory(null);
  const { operationalDetail } = useDeployment();
  const system = useResource(operationalDetail ? 'system' : null, () => api.system());
  return (
    <Panel
      title="Data snapshot"
      subtitle="The frozen, hash-verified input of every result"
      tags={<Tag tone="observed">Observed</Tag>}
    >
      <Async resource={history} label="the snapshot">
        {({ data, provenance }) => (
          <div className="stack-3">
            <dl className="kv">
              <dt>Source</dt>
              <dd>
                NYC Open Data, 311 Service Requests (dataset erm2-nwe9), city-wide daily totals
              </dd>
              <dt>Snapshot</dt>
              <dd>{data.snapshot_id}</dd>
              <dt>Days covered</dt>
              <dd>
                {formatDate(data.snapshot_first_date)} to {formatDate(data.snapshot_last_date)}
                {system.data?.data.snapshot != null &&
                  ` (${formatInteger(system.data.data.snapshot.n_days)} days)`}
              </dd>
              <dt>Data fingerprint</dt>
              <dd className="mono" title={provenance.data_fingerprint ?? undefined}>
                {shortHash(provenance.data_fingerprint, 16)}
              </dd>
              <dt>Unit</dt>
              <dd>Requests created per day (intake), not measured workload</dd>
            </dl>
            <p className="small secondary">
              No new data has been extracted since {formatDate(data.snapshot_last_date)}: every view
              describes the frozen snapshot and outputs computed from it.
            </p>
            <ProvenanceFooter provenance={provenance} />
          </div>
        )}
      </Async>
    </Panel>
  );
}

function ForecastSummaryPanel() {
  const forecast = useForecast();
  return (
    <Panel
      title="Forecast summary"
      subtitle="The archived 30-day M2 projection (requests/day)"
      tags={
        <span className="tag-row">
          <Tag tone="projected">Archived projection</Tag>
          <NotValidatedTag />
        </span>
      }
    >
      <Async resource={forecast} label="the forecast">
        {({ data, provenance }) => {
          const first = data.points[0];
          const last = data.points[data.points.length - 1];
          return (
            <div className="stack-3">
              <dl className="kv">
                <dt>Model</dt>
                <dd>
                  {data.model_id}{' '}
                  <span className="muted">(recommended by the pre-registered M2 protocol)</span>
                </dd>
                <dt>Origin</dt>
                <dd>{formatDate(data.origin)}</dd>
                {first !== undefined && (
                  <>
                    <dt>Day 1</dt>
                    <dd>
                      {formatDate(first.target_date)}: {formatInteger(first.prediction)}{' '}
                      requests/day
                    </dd>
                  </>
                )}
                {last !== undefined && (
                  <>
                    <dt>Day {last.horizon}</dt>
                    <dd>
                      {formatDate(last.target_date)}: {formatInteger(last.prediction)} requests/day
                    </dd>
                  </>
                )}
                <dt>Historical WAPE</dt>
                <dd>
                  {formatPct(data.accuracy_context.wape_pct)} (confirmation period, horizons 1-30)
                </dd>
              </dl>
              <ArchivedProjectionCallout forecast={data} />
              <ProvenanceFooter provenance={provenance} />
            </div>
          );
        }}
      </Async>
    </Panel>
  );
}

function VolumePanel() {
  const history = useHistory(null);
  const forecast = useForecast();
  return (
    <Panel
      title="Historical request volume"
      subtitle="City-wide 311 requests created per day: the last 180 observed days, then the archived projection (requests/day)"
      actions={<Link href="/forecasting">Explore the forecast</Link>}
    >
      <Async resource={history} label="the observed history">
        {(h) => (
          <Async resource={forecast} label="the forecast">
            {(f) => (
              <div className="stack-3">
                <HistoryProjectionChart history={h.data} forecast={f.data} horizon={30} />
                <Evidence integrity={f.integrity} provenance={f.provenance} />
              </div>
            )}
          </Async>
        )}
      </Async>
    </Panel>
  );
}

function ScenarioPanel() {
  const scenarios = useScenarios();
  return (
    <Panel
      title="Scenario overview"
      subtitle={`The committed M4 reference run: one HYPOTHETICAL city-wide unit planned over the projection`}
      tags={<HypotheticalTag />}
      actions={<Link href="/decisions">Open decision optimisation</Link>}
    >
      <Async resource={scenarios} label="the scenarios">
        {({ data, integrity, provenance }) => (
          <div className="stack-3">
            <Callout tone="warning" title="Every operational parameter is HYPOTHETICAL">
              <p>
                ATLAS holds no staffing, cost, budget or capacity data for any NYC agency. These
                plans are exploratory mathematics over assumed values, never recommendations.
              </p>
            </Callout>
            <DataTable
              caption={`Reference scenarios, ${formatDate(data.first_date)} to ${formatDate(data.last_date)} (${data.horizon_days} days). Discretionary cost in USD; terminal backlog in requests.`}
              rows={data.scenarios}
              rowKey={(s) => s.scenario_id}
              columns={[
                {
                  key: 'scenario',
                  header: 'Scenario',
                  rowHeader: true,
                  cell: (s) => (
                    <Link href={`/decisions?scenario=${s.scenario_id}`}>
                      {humanise(s.scenario_id)}
                    </Link>
                  ),
                },
                {
                  key: 'feasibility',
                  header: 'Feasibility',
                  cell: (s) =>
                    s.feasibility === 'strict' ? (
                      <>
                        <Tag tone="good">Meets every constraint</Tag>{' '}
                        <EquallyScoredTag unique={s.optimal_plan_unique} />
                      </>
                    ) : (
                      <DiagnosticTag />
                    ),
                },
                {
                  key: 'cost',
                  header: 'Discretionary cost',
                  numeric: true,
                  cell: (s) => formatUsd(s.discretionary_cost),
                },
                {
                  key: 'terminal',
                  header: 'Terminal backlog',
                  numeric: true,
                  cell: (s) => formatInteger(s.terminal_backlog),
                },
              ]}
            />
            <Evidence integrity={integrity} provenance={provenance} />
          </div>
        )}
      </Async>
    </Panel>
  );
}

function LimitationsPanel() {
  const { operationalDetail } = useDeployment();
  const system = useResource(operationalDetail ? 'system' : null, () => api.system());
  const forecast = useForecast();
  const scenarios = useScenarios();
  const fromSystem = system.data?.data.scientific_status?.limitations;
  const notices = (integrity: Integrity | undefined) => integrity?.notices ?? [];
  const sources = operationalDetail ? [system, forecast, scenarios] : [forecast, scenarios];
  const waiting = sources.some(
    (r) => r.snapshot.status === 'loading' || r.snapshot.status === 'idle',
  );
  const limitations = fromSystem ?? [
    ...new Set([...notices(forecast.data?.integrity), ...notices(scenarios.data?.integrity)]),
  ];
  return (
    <Panel
      title="Warnings and limitations"
      subtitle="As the API states them; each also appears next to the numbers it qualifies"
    >
      {limitations.length === 0 ? (
        waiting ? (
          <p className="muted small">Loading the limitations…</p>
        ) : (
          <p className="small" role="status">
            The limitations could not be loaded because the API did not answer. No number is shown
            without them.
          </p>
        )
      ) : (
        <ul className="notice-list">
          {limitations.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export default function Overview() {
  return (
    <>
      <PageHeader
        eyebrow="ATLAS · NYC 311 demand research"
        title="Decision-intelligence research on NYC 311 demand"
      >
        <p>
          ATLAS turns a legacy 30-day demand forecast for New York City's 311 service requests into
          reproducible, tested software: rolling-origin backtests against baselines (M2), prediction
          intervals with a pre-registered calibration test (M3), and a linear-programming model of
          HYPOTHETICAL resources (M4), served by a verified API. Every number below comes from that
          API, with its status and its sources.
        </p>
      </PageHeader>
      <StatusStrip />
      <VolumePanel />
      <div className="grid-2">
        <SnapshotPanel />
        <ForecastSummaryPanel />
      </div>
      <ScenarioPanel />
      <LimitationsPanel />
    </>
  );
}
