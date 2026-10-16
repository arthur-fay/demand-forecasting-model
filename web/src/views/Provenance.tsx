import { api } from '../api/endpoints';
import { useResource } from '../api/resource';
import type { Provenance as ProvenanceBlock, Source } from '../api/schemas';
import { Callout } from '../components/Callout';
import { DataTable } from '../components/DataTable';
import { Panel } from '../components/Panel';
import { Async } from '../components/States';
import { Tag } from '../components/Tag';
import { useDeployment } from '../deployment';
import { formatDate, formatInteger, humanise, shortHash } from '../lib/format';
import { PageHeader } from './common';
import {
  useComparison,
  useForecast,
  useHistory,
  useIntervals,
  useModels,
  useScenario,
  useScenarios,
  useSimulationParameters,
  useUncertaintyStatus,
} from './data';

const REPOSITORY = 'https://github.com/arthur-fay/demand-forecasting-model/blob/main/docs';

const METHODS = [
  {
    milestone: 'M0-M1',
    title: 'Data foundation and quality',
    text: 'A frozen, hash-verified snapshot of NYC 311 daily totals with fail-closed contracts, reproduction of every legacy figure, causal data-quality flags and one leakage-safe feature builder.',
    doc: 'DATA_SNAPSHOT.md',
  },
  {
    milestone: 'M2',
    title: 'Forecasting and backtesting',
    text: 'The 30-day procedure re-run from every daily origin with a refit each time, scored at every horizon against simple baselines, with a pre-registered selection rule and Holm-adjusted Diebold-Mariano tests.',
    doc: 'BACKTESTING.md',
  },
  {
    milestone: 'M3',
    title: 'Prediction intervals',
    text: 'Horizon-specific intervals calibrated only on errors observable at each origin, evaluated with a pre-registered, dependence-aware protocol. Result: FAILED_CALIBRATION.',
    doc: 'UNCERTAINTY.md',
  },
  {
    milestone: 'M4',
    title: 'Decision simulation',
    text: 'A certified linear programme of service, overtime, surge and backlog under HYPOTHETICAL parameters, with diagnoses of infeasible scenarios, policy comparisons and sensitivity analysis.',
    doc: 'OPTIMIZATION.md',
  },
  {
    milestone: 'M5-M6',
    title: 'API and deployment',
    text: 'A FastAPI service that serves the committed outputs with integrity labels and provenance and runs bounded simulations in isolated workers; a verified, private-first deployment package.',
    doc: 'API.md',
  },
  {
    milestone: 'M7',
    title: 'This dashboard',
    text: 'A static React build served from the same origin as the API, validating every response against the committed contract, with its own Content-Security-Policy and no credential in the browser.',
    doc: 'DASHBOARD.md',
  },
] as const;

function SystemPanel() {
  const { operationalDetail, descriptor } = useDeployment();
  const system = useResource(operationalDetail ? 'system' : null, () => api.system());
  if (!operationalDetail) {
    return (
      <Panel title="Service" subtitle="Versions, components and limits">
        <Callout tone="info" title="Operational detail is not exposed in this deployment">
          <p>
            {descriptor?.profile === 'public-demo'
              ? 'The public demonstration serves the precomputed research outputs only; component readiness, worker state and limits stay private.'
              : 'The deployment descriptor could not be read, so the dashboard does not ask for operational detail.'}
          </p>
        </Callout>
      </Panel>
    );
  }
  return (
    <Panel title="Service" subtitle="Versions, components and limits, from /api/v1/system">
      <Async resource={system} label="the service metadata">
        {({ data }) => (
          <div className="stack-3">
            <dl className="kv">
              <dt>ATLAS</dt>
              <dd>
                {data.atlas_version} (API {data.api_version})
              </dd>
              <dt>Solver</dt>
              <dd>
                {Object.entries(data.solver)
                  .map(([name, version]) => `${name} ${version ?? 'unknown'}`)
                  .join(', ')}
              </dd>
              {data.snapshot !== null && (
                <>
                  <dt>Snapshot</dt>
                  <dd>
                    {data.snapshot.snapshot_id}, {formatDate(data.snapshot.first_date)} to{' '}
                    {formatDate(data.snapshot.last_date)} ({formatInteger(data.snapshot.n_days)}{' '}
                    days), fingerprint{' '}
                    <span className="mono">{shortHash(data.snapshot.data_fingerprint, 16)}</span>
                  </dd>
                </>
              )}
              <dt>Simulations</dt>
              <dd>
                {data.limits.max_concurrent_simulations} at a time,{' '}
                {data.limits.simulation_timeout_seconds} s deadline,
                {data.limits.simulation_requires_api_key ? ' API key required' : ' no API key'}
              </dd>
            </dl>
            <DataTable
              caption="Components verified at startup (a damaged artifact makes its component, and those built on it, unavailable)"
              rows={data.components}
              rowKey={(c) => c.component}
              columns={[
                {
                  key: 'c',
                  header: 'Component',
                  rowHeader: true,
                  cell: (c) => humanise(c.component),
                },
                {
                  key: 'r',
                  header: 'Ready',
                  cell: (c) =>
                    c.ready ? <Tag tone="good">Ready</Tag> : <Tag tone="critical">Not ready</Tag>,
                },
                { key: 'd', header: 'Detail', cell: (c) => c.detail ?? '' },
              ]}
            />
            {data.scientific_status !== null && (
              <div className="stack-2">
                <h3>Scientific status</h3>
                <ul className="notice-list">
                  {data.scientific_status.limitations.map((text) => (
                    <li key={text}>{text}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </Async>
    </Panel>
  );
}

function SourcesPanel() {
  const scenarios = useScenarios();
  // One scenario's detail (the one Decisions opens first) names the daily plans' file.
  const scenario = scenarios.data?.data.scenarios[0]?.scenario_id ?? null;
  const detail = useScenario(scenario);
  const resources = [
    useHistory(null),
    useModels(),
    useForecast(),
    useUncertaintyStatus(),
    useIntervals(),
    scenarios,
    useComparison(),
    detail,
    useSimulationParameters(),
  ];
  const blocks: ProvenanceBlock[] = resources.flatMap((r) =>
    r.data === undefined ? [] : [r.data.provenance],
  );
  // A run can be cited by several answers, each naming the files it used: keep them all.
  const sources = new Map<string, Source>();
  for (const block of blocks)
    for (const source of block.sources) {
      const key = `${source.kind}:${source.path}`;
      const seen = sources.get(key);
      sources.set(key, seen ? { ...seen, files: { ...seen.files, ...source.files } } : source);
    }
  // The detail waits for the scenario list: idle without an id is not loading.
  const loading = resources.some(
    (r) =>
      r.snapshot.status === 'loading' ||
      (r.snapshot.status === 'idle' && (r !== detail || scenario !== null)),
  );
  const failed = resources.filter((r) => r.snapshot.status === 'error').length;
  const first = blocks[0];
  return (
    <Panel
      title="Sources of every view"
      subtitle="Each committed file behind the numbers, with the fingerprints the server verified at startup"
    >
      {first !== undefined && (
        <p className="small secondary">
          Every source was made from the {first.snapshot_id} snapshot, data fingerprint{' '}
          <span className="mono">{first.data_fingerprint ?? 'unavailable'}</span>, and is served by
          ATLAS {first.atlas_version}.
        </p>
      )}
      {loading && <p className="small muted">Collecting sources…</p>}
      {failed > 0 && (
        <p className="small muted">
          {failed} source lists could not be loaded; their views say why.
        </p>
      )}
      <DataTable
        caption="Committed sources (repository-relative paths)"
        rows={[...sources.values()]}
        rowKey={(s) => `${s.kind}:${s.path}`}
        columns={[
          { key: 'kind', header: 'Source', rowHeader: true, cell: (s) => humanise(s.kind) },
          { key: 'path', header: 'Path', cell: (s) => <span className="mono">{s.path}</span> },
          {
            key: 'run',
            header: 'Run',
            cell: (s) => <span className="mono">{s.run_id ?? '–'}</span>,
          },
          {
            key: 'result',
            header: 'Result fingerprint',
            cell: (s) => (
              <span className="mono" title={s.result_fingerprint ?? undefined}>
                {shortHash(s.result_fingerprint)}
              </span>
            ),
          },
          {
            key: 'files',
            header: 'Files (SHA-256)',
            cell: (s) => (
              <ul className="notice-list">
                {Object.entries(s.files).map(([name, sha]) => (
                  <li key={name}>
                    {name}{' '}
                    <span className="mono" title={sha}>
                      {shortHash(sha)}
                    </span>
                  </li>
                ))}
              </ul>
            ),
          },
        ]}
      />
    </Panel>
  );
}

function DashboardPanel() {
  const { descriptor } = useDeployment();
  return (
    <Panel title="This dashboard" subtitle="Build, deployment and security">
      <dl className="kv">
        <dt>Build</dt>
        <dd>
          atlas-dashboard {__ATLAS_DASHBOARD_VERSION__}: a static build, verified against its
          manifest by the server
        </dd>
        <dt>Deployment</dt>
        <dd>
          {descriptor === null
            ? 'unknown (descriptor unavailable)'
            : `${descriptor.profile}; simulations ${descriptor.simulations.replace(/_/g, ' ')}; operational detail ${descriptor.operational_detail ? 'available' : 'not exposed'}`}
        </dd>
        <dt>Data</dt>
        <dd>
          Same-origin reads of /api/v1 only; every answer validated against the committed OpenAPI
          contract
        </dd>
        <dt>Security</dt>
        <dd>
          A strict Content-Security-Policy (same-origin scripts, styles and requests; no inline
          code, no eval, Trusted Types), no cookies, no storage of your inputs, no credential: the
          private deployment's proxy adds the simulation key itself
        </dd>
      </dl>
    </Panel>
  );
}

function MethodsPanel() {
  return (
    <Panel title="Methods" subtitle="What each milestone did, and where its full documentation is">
      <ol className="methods-list">
        {METHODS.map((m) => (
          <li key={m.milestone}>
            <span className="chip">{m.milestone}</span>
            <div className="stack-2">
              <h3>{m.title}</h3>
              <p className="small secondary">{m.text}</p>
              <a className="small" href={`${REPOSITORY}/${m.doc}`} rel="noreferrer" target="_blank">
                {m.doc} (opens GitHub)
              </a>
            </div>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

export default function Provenance() {
  return (
    <>
      <PageHeader eyebrow="Methods, sources and limitations" title="Methods and provenance">
        <p>
          Where every number comes from, how it was produced, and what it cannot support. ATLAS is
          research software: nothing it shows is a validated staffing, budget or service-capacity
          recommendation.
        </p>
      </PageHeader>
      <MethodsPanel />
      <SourcesPanel />
      <SystemPanel />
      <DashboardPanel />
    </>
  );
}
