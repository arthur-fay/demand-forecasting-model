import { useState } from 'react';

import type { ComparisonData, ScenarioListData } from '../api/schemas';
import { BarChart } from '../charts/BarChart';
import { Callout } from '../components/Callout';
import { Segmented, ViewToggle, type View } from '../components/Controls';
import { DataTable } from '../components/DataTable';
import { Panel } from '../components/Panel';
import { Evidence, ProvenanceFooter } from '../components/Provenance';
import { Async, Loading } from '../components/States';
import { DiagnosticTag, EquallyScoredTag, HypotheticalTag, Tag } from '../components/Tag';
import {
  formatDate,
  formatInteger,
  formatNumber,
  formatSigned,
  formatUnit,
  formatUsd,
  humanise,
} from '../lib/format';
import { setQueryParam, useQueryParam } from '../router';
import { PageHeader } from './common';
import { useComparison, useScenario, useScenarios } from './data';
import { FeasibilityTag, formatSignedUsd, PlanOutcome } from './plan';

const METRICS = [
  { value: 'discretionary_cost', label: 'Discretionary cash' },
  { value: 'backlog_request_days', label: 'Backlog request-days' },
  { value: 'terminal_backlog', label: 'Terminal backlog' },
  { value: 'overtime_hours', label: 'Overtime hours' },
] as const;
type Metric = (typeof METRICS)[number]['value'];

function formatMetric(metric: string, value: number | null): string {
  if (value === null) return '–';
  return metric === 'discretionary_cost' || metric === 'penalised_score'
    ? formatUsd(value)
    : formatInteger(value);
}

function ComparisonPanel() {
  const comparison = useComparison();
  const [metric, setMetric] = useState<Metric>('discretionary_cost');
  const [view, setView] = useState<View>('chart');
  return (
    <Panel
      title="Comparing the reference scenarios"
      subtitle="The reported plan of each scenario; DIAGNOSTIC values belong to plans that miss the terminal-backlog target"
      tags={<HypotheticalTag />}
      actions={<ViewToggle value={view} onChange={setView} label="Scenario comparison" />}
    >
      <Segmented label="Measure" value={metric} options={METRICS} onChange={setMetric} />
      <Async resource={comparison} label="the scenario comparison">
        {({ data, integrity, provenance }) => (
          <div className="stack-3">
            <Callout tone="warning" title="How to read these differences">
              <p>{data.caveat}</p>
            </Callout>
            {view === 'chart' ? (
              <ComparisonChart data={data} metric={metric} />
            ) : (
              <ComparisonTable data={data} />
            )}
            <Evidence integrity={integrity} provenance={provenance} />
          </div>
        )}
      </Async>
    </Panel>
  );
}

function ComparisonChart({ data, metric }: { data: ComparisonData; metric: Metric }) {
  const unit = formatUnit(data.units[metric] ?? '');
  return (
    <BarChart
      label={`${humanise(metric)} by scenario (${unit})`}
      summary={data.rows
        .map(
          (row) =>
            `${humanise(row.scenario_id)}: ${formatMetric(metric, row.values[metric] ?? null)}${row.values_status === 'DIAGNOSTIC' ? ' (DIAGNOSTIC)' : ''}`,
        )
        .join('; ')}
      valueLabel={`${humanise(metric)} (${unit})`}
      formatValue={(v) => formatMetric(metric, v)}
      rows={data.rows.map((row) => ({
        id: row.scenario_id,
        label: humanise(row.scenario_id),
        value: row.values[metric] ?? null,
        color: row.values_status === 'DIAGNOSTIC' ? 'var(--neutral-mark)' : 'var(--series-1)',
        ...(row.values_status === 'DIAGNOSTIC' ? { suffix: 'DIAGNOSTIC' } : {}),
        notes: [
          row.description,
          row.values_status === 'DIAGNOSTIC'
            ? `DIAGNOSTIC: misses the terminal target by ${formatInteger(row.terminal_violation)} requests`
            : 'Meets every HYPOTHETICAL constraint',
        ],
      }))}
      legend={[
        { label: 'Meets every HYPOTHETICAL constraint', color: 'var(--series-1)', kind: 'swatch' },
        {
          label: 'DIAGNOSTIC (relaxed plan, misses the target)',
          color: 'var(--neutral-mark)',
          kind: 'swatch',
        },
      ]}
    />
  );
}

function ComparisonTable({ data }: { data: ComparisonData }) {
  return (
    <DataTable
      caption={`Every scenario against the baseline (${data.baseline_scenario_id}). A difference (this scenario minus the baseline) is shown only when both plans meet every constraint.`}
      rows={data.rows}
      rowKey={(row) => row.scenario_id}
      highlight={(row) => row.scenario_id === data.baseline_scenario_id}
      columns={[
        {
          key: 'scenario',
          header: 'Scenario',
          rowHeader: true,
          cell: (row) => humanise(row.scenario_id),
        },
        {
          key: 'status',
          header: 'Values',
          cell: (row) =>
            row.values_status === 'DIAGNOSTIC' ? (
              <DiagnosticTag />
            ) : (
              <Tag tone="good">Meets every constraint</Tag>
            ),
        },
        ...data.metrics
          .filter((m) => m !== 'penalised_score')
          .flatMap((m) => [
            {
              key: m,
              header: `${humanise(m)} (${formatUnit(data.units[m] ?? '')})`,
              numeric: true,
              cell: (row: (typeof data.rows)[number]) => formatMetric(m, row.values[m] ?? null),
            },
          ]),
        {
          key: 'diff',
          header: `Cash vs baseline (${formatUnit(data.units.discretionary_cost ?? '')})`,
          numeric: true,
          cell: (row) => {
            const value = row.difference_from_baseline.discretionary_cost ?? null;
            return value === null ? (
              <span className="muted">not comparable</span>
            ) : (
              formatSignedUsd(value)
            );
          },
        },
      ]}
    />
  );
}

function FeasibilityPanel() {
  const scenarios = useScenarios();
  return (
    <Panel
      title="Feasible and infeasible scenarios"
      subtitle="Which scenarios have a plan that meets every HYPOTHETICAL constraint, and why not"
    >
      <Async resource={scenarios} label="the scenarios">
        {({ data, provenance }) => (
          <div className="stack-3">
            <FeasibilityTable data={data} />
            <ProvenanceFooter provenance={provenance} />
          </div>
        )}
      </Async>
    </Panel>
  );
}

function FeasibilityTable({ data }: { data: ScenarioListData }) {
  return (
    <DataTable
      caption={`${data.scenarios.length} reference scenarios over ${data.horizon_days} days from ${formatDate(data.first_date)}. Binding constraints are read from each reported plan (no slack left); terminal_backlog_violated marks a DIAGNOSTIC plan above its target and is not a binding limit.`}
      rows={data.scenarios}
      rowKey={(s) => s.scenario_id}
      columns={[
        {
          key: 'scenario',
          header: 'Scenario',
          rowHeader: true,
          cell: (s) => (
            <a
              href={`/decisions?scenario=${s.scenario_id}#scenario-detail`}
              onClick={(event) => {
                // A modified click keeps the browser's own behaviour (a new tab or window).
                if (
                  event.defaultPrevented ||
                  event.button !== 0 ||
                  event.metaKey ||
                  event.ctrlKey ||
                  event.shiftKey ||
                  event.altKey
                )
                  return;
                event.preventDefault();
                setQueryParam('scenario', s.scenario_id);
                // The focus moves with the view, so the next Tab and a screen reader continue
                // from the detail rather than from this table.
                const heading = document.getElementById('scenario-detail');
                heading?.setAttribute('tabindex', '-1');
                heading?.scrollIntoView({ block: 'start' });
                heading?.focus({ preventScroll: true });
              }}
            >
              {humanise(s.scenario_id)}
            </a>
          ),
        },
        { key: 'description', header: 'What changes', cell: (s) => s.description },
        {
          key: 'feasibility',
          header: 'Feasibility',
          cell: (s) => (
            <>
              <FeasibilityTag feasibility={s.feasibility} />{' '}
              <EquallyScoredTag unique={s.optimal_plan_unique} />
            </>
          ),
        },
        {
          key: 'binding',
          header: 'Binding constraints',
          cell: (s) =>
            s.binding_constraints.length === 0 ? (
              <span className="muted">none reported</span>
            ) : (
              s.binding_constraints.join(', ')
            ),
        },
        {
          key: 'why',
          header: 'Diagnosis',
          cell: (s) =>
            s.diagnosis_reasons.length === 0 ? (
              <span className="muted">none needed</span>
            ) : (
              <details>
                <summary>Why it is infeasible</summary>
                {s.diagnosis_reasons.join(' ')}
              </details>
            ),
        },
      ]}
    />
  );
}

function ScenarioDetailPanel() {
  const scenarios = useScenarios();
  const requested = useQueryParam('scenario');
  const ids = scenarios.data?.data.scenarios.map((s) => s.scenario_id) ?? [];
  const id = requested !== null && ids.includes(requested) ? requested : (ids[0] ?? null);
  const detail = useScenario(id);
  const shown = detail.data ?? detail.previous;
  return (
    <Panel
      id="scenario-detail"
      title="Scenario detail"
      subtitle="Daily resources, backlog and the policy comparison of one reference scenario"
      tags={<HypotheticalTag />}
      actions={
        ids.length > 0 && id !== null ? (
          <div className="field">
            <label className="field-label" htmlFor="scenario-select">
              Scenario
            </label>
            <select
              id="scenario-select"
              className="select"
              value={id}
              onChange={(event) => {
                setQueryParam('scenario', event.target.value);
              }}
            >
              {ids.map((value) => (
                <option key={value} value={value}>
                  {humanise(value)}
                </option>
              ))}
            </select>
          </div>
        ) : undefined
      }
    >
      {requested !== null && ids.length > 0 && !ids.includes(requested) && (
        <Callout tone="info" title={`There is no reference scenario named “${requested}”`}>
          <p>Showing {humanise(id ?? '')} instead.</p>
        </Callout>
      )}
      {scenarios.snapshot.status === 'error' ? (
        <Async resource={scenarios} label="the scenarios">
          {() => null}
        </Async>
      ) : detail.snapshot.status === 'error' ? (
        <Async resource={detail} label="the scenario">
          {() => null}
        </Async>
      ) : shown === undefined ? (
        <Loading
          label="the scenario"
          slow={[scenarios.snapshot, detail.snapshot].some(
            (snapshot) => snapshot.status === 'loading' && snapshot.slow,
          )}
        />
      ) : (
        <div className="stack-4" data-stale={detail.data === undefined ? 'true' : undefined}>
          <div className="stack-2">
            <h3>
              {humanise(shown.data.outcome.scenario_id)}{' '}
              <FeasibilityTag feasibility={shown.data.outcome.feasibility} />
            </h3>
            <p className="secondary">{shown.data.outcome.description}</p>
            <dl className="kv">
              <dt>Demand</dt>
              <dd>
                {shown.data.outcome.demand_basis === 'point_forecast'
                  ? 'The M2 point forecast'
                  : `Per-day marginal M3 quantile ${shown.data.outcome.quantile ?? ''} (a stress path with no probability)`}
                : {formatInteger(shown.data.outcome.demand_total_requests)} requests in total (
                {formatSigned(shown.data.outcome.demand_total_vs_point_forecast_pct, 1)} % against
                the point forecast)
              </dd>
              <dt>Regular capacity</dt>
              <dd>
                {formatInteger(shown.data.outcome.regular_capacity_mean_requests_per_day)}{' '}
                requests/day on average (HYPOTHETICAL)
              </dd>
              <dt>Budget</dt>
              <dd>
                {shown.data.outcome.discretionary_budget_usd === null
                  ? 'no limit'
                  : `${formatUsd(shown.data.outcome.discretionary_budget_usd)} for overtime and surge (HYPOTHETICAL)`}
              </dd>
              {Object.entries(shown.data.outcome.adjustments).map(([name, quantity]) => (
                <AdjustmentRow
                  key={name}
                  name={name}
                  value={quantity.value}
                  unit={quantity.unit}
                  assumption={quantity.assumption}
                />
              ))}
            </dl>
          </div>
          <PlanOutcome
            outcome={shown.data.outcome}
            plans={shown.data.plans}
            daily={shown.data.daily_plans}
            headingLevel={4}
          />
          <details className="disclosure">
            <summary>
              The HYPOTHETICAL parameters of the reference run (
              {shown.data.hypothetical_parameters.length})
            </summary>
            <div className="disclosure-body">
              <DataTable
                caption="Every value is assumed, with its documented rationale; none is measured."
                rows={shown.data.hypothetical_parameters}
                rowKey={(p) => `${p.scope}:${p.parameter}`}
                columns={[
                  {
                    key: 'p',
                    header: 'Parameter',
                    rowHeader: true,
                    cell: (p) => humanise(p.parameter),
                  },
                  { key: 'scope', header: 'Scope', cell: (p) => p.scope },
                  {
                    key: 'v',
                    header: 'Value',
                    numeric: true,
                    cell: (p) =>
                      typeof p.value === 'number'
                        ? `${formatNumber(p.value, p.value % 1 === 0 ? 0 : 2)} ${formatUnit(p.unit)}`
                        : `${p.value} (${formatUnit(p.unit)})`,
                  },
                  { key: 'a', header: 'Assumption', cell: (p) => p.assumption },
                ]}
              />
            </div>
          </details>
          <details className="disclosure">
            <summary>
              Structural assumptions of the model ({shown.data.structural_assumptions.length})
            </summary>
            <div className="disclosure-body">
              <ul className="notice-list">
                {shown.data.structural_assumptions.map((text) => (
                  <li key={text}>{text}</li>
                ))}
              </ul>
            </div>
          </details>
          <Evidence integrity={shown.integrity} provenance={shown.provenance} />
        </div>
      )}
    </Panel>
  );
}

function AdjustmentRow({
  name,
  value,
  unit,
  assumption,
}: {
  name: string;
  value: number;
  unit: string;
  assumption: string;
}) {
  return (
    <>
      <dt>{humanise(name)}</dt>
      <dd>
        {formatNumber(value, value % 1 === 0 ? 0 : 2)} {formatUnit(unit)}{' '}
        <Tag tone="warning">HYPOTHETICAL</Tag> <span className="muted">{assumption}</span>
      </dd>
    </>
  );
}

function TradeoffsPanel() {
  return (
    <Panel
      title="Reading the optimisation"
      subtitle="What the linear programme decides, and what its answers do not mean"
    >
      <div className="prose small">
        <p>
          For each day the model decides how much overtime and surge capacity to add to a fixed
          regular capacity, and carries unserved requests over as backlog. It minimises a{' '}
          <strong>penalised score</strong>: the discretionary cash plus a notional weight on every
          request-day of backlog. The weight is a ranking device, never a cost. Among plans with the
          optimal score it reports the one with the least cash, then the least terminal backlog,
          then the fewest request-days.
        </p>
        <p>
          <strong>The tradeoff</strong> is between cash and backlog. More discretionary cash buys
          overtime and surge, which lowers the backlog; a tighter budget, higher demand or lower
          capacity raises it. When no plan can bring the end-of-horizon backlog under the
          HYPOTHETICAL target within the budget, the scenario is infeasible: ATLAS then reports the
          plan that misses the target by the least, labelled <strong>DIAGNOSTIC</strong>, and
          explains which limit blocks it.
        </p>
        <p>
          Every result is <strong>certified</strong>: the solver's answer is checked for primal and
          dual feasibility with a zero duality gap, and every constraint of the plan is re-checked
          independently (a DIAGNOSTIC plan against every constraint but the terminal target it
          misses). That makes each plan the exact optimum of an <em>assumed</em> problem. It does
          not make the assumptions true: the demand (the unvalidated point forecast, or a labelled
          stress path) is treated as known, and staffing, productivity, costs and budget are
          illustrative.
        </p>
      </div>
    </Panel>
  );
}

export default function Decisions() {
  return (
    <>
      <PageHeader eyebrow="M4 · Hypothetical decision simulation" title="Decision optimisation">
        <p>
          How could a city-wide pool of capacity absorb the projected demand? A linear programme
          plans overtime, surge capacity and backlog day by day under nine reference scenarios.
          Every parameter is HYPOTHETICAL: the answers show the model's mechanics and tradeoffs, not
          what any agency should do.
        </p>
      </PageHeader>
      <Callout
        tone="warning"
        title="HYPOTHETICAL: not a staffing, budget or capacity recommendation"
      >
        <p>
          ATLAS has no data on NYC staffing, productivity, operating costs or budgets. Every such
          value below is illustrative and labelled HYPOTHETICAL, and the demand is the unvalidated
          M2 point forecast or, where labelled, a per-day M3 quantile stress path from the
          FAILED_CALIBRATION intervals, with no probability.
        </p>
      </Callout>
      <ComparisonPanel />
      <FeasibilityPanel />
      <ScenarioDetailPanel />
      <TradeoffsPanel />
    </>
  );
}
