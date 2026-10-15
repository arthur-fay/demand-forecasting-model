/**
 * One M4 plan, as the API reports it: feasibility, the certified reported plan, the
 * diagnosis of an infeasible scenario, binding constraints, daily resources and backlog,
 * and the comparison with two simple policies. Shared by the reference scenarios and the
 * simulator. Every value is the API's; DIAGNOSTIC values are labelled as such everywhere.
 */
import { useState } from 'react';

import type { DayPlan, PlanVariant, ScenarioOutcome } from '../api/schemas';
import { LineChart } from '../charts/LineChart';
import { StackedColumns } from '../charts/StackedColumns';
import { Callout } from '../components/Callout';
import { Stat, ViewToggle, type View } from '../components/Controls';
import { DataTable } from '../components/DataTable';
import { DiagnosticTag, HypotheticalTag, Tag } from '../components/Tag';
import {
  dateValue,
  formatCompact,
  formatInteger,
  formatNumber,
  formatShortDate,
  formatSigned,
  formatUsd,
  formatUsdCompact,
  formatWeekdayDate,
  humanise,
  isoDay,
} from '../lib/format';

const POLICIES: Record<string, string> = {
  fixed_capacity: 'Fixed-capacity policy (no overtime or surge)',
  backlog_trigger: 'Backlog-trigger policy',
};

const RELATION: Record<string, string> = {
  dominates: 'The reported plan is better on both cash and backlog',
  dominated: 'The policy is better on both cash and backlog',
  tradeoff: 'A tradeoff: one spends less, the other leaves less backlog',
  equal: 'The same outcome',
};

/** The daily-plan name of the reported plan (strict: optimised; otherwise the relaxed plan). */
export function reportedPlanName(outcome: ScenarioOutcome, daily: readonly DayPlan[]): string {
  const names = new Set(daily.map((d) => d.plan));
  if (outcome.feasibility === 'strict' && names.has('optimized')) return 'optimized';
  const relaxed = outcome.diagnosis?.relaxed_plan ?? 'relaxed_terminal_target';
  return names.has(relaxed) ? relaxed : (daily[0]?.plan ?? 'optimized');
}

export function FeasibilityTag({ feasibility }: { feasibility: ScenarioOutcome['feasibility'] }) {
  return feasibility === 'strict' ? (
    <Tag tone="good" title="The reported plan meets every HYPOTHETICAL constraint">
      Meets every constraint
    </Tag>
  ) : (
    <DiagnosticTag />
  );
}

export function PlanOutcome({
  outcome,
  plans,
  daily,
}: {
  outcome: ScenarioOutcome;
  plans: readonly PlanVariant[];
  daily: readonly DayPlan[];
}) {
  const reported = reportedPlanName(outcome, daily);
  const evaluation = outcome.reported_plan?.evaluation ?? null;
  const certificate = outcome.reported_plan?.certificate ?? null;
  const variant = plans.find((p) => p.plan === reported);
  const target = variant?.terminal_backlog_max ?? null;
  const diagnostic = outcome.feasibility !== 'strict';
  const tag = diagnostic ? <DiagnosticTag /> : <HypotheticalTag />;
  return (
    <div className="stack-4">
      {diagnostic ? (
        <Callout
          tone="serious"
          title={
            outcome.diagnosis?.label ?? 'DIAGNOSTIC: no plan meets every HYPOTHETICAL constraint'
          }
        >
          <p>
            No plan meets every HYPOTHETICAL constraint. The plan below is the one that misses the
            terminal-backlog target by the least within the budget: its values are DIAGNOSTIC, and
            any spend shown is never a budget request.
          </p>
          {outcome.diagnosis !== null && (
            <ul>
              {outcome.diagnosis.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
        </Callout>
      ) : (
        <Callout tone="info" title="The reported plan meets every HYPOTHETICAL constraint">
          <p>
            It is the certified optimum of the linear programme for these assumed values (
            {outcome.reported_plan_selection}). That makes it a correct answer to a hypothetical
            question, not a recommendation.
          </p>
        </Callout>
      )}
      {evaluation !== null && (
        <div className="stat-grid">
          <Stat
            label="Discretionary cash (overtime + surge)"
            value={formatUsd(evaluation.discretionary_cost)}
            tags={tag}
            note={
              evaluation.discretionary_budget === null
                ? 'No budget limit.'
                : `Budget ${formatUsd(evaluation.discretionary_budget)} (HYPOTHETICAL).`
            }
          />
          <Stat
            label="Backlog after the last day"
            value={formatInteger(evaluation.terminal_backlog)}
            unit="requests"
            tags={tag}
            note={
              target === null
                ? 'No terminal target.'
                : `HYPOTHETICAL target: at most ${formatInteger(target)}.`
            }
          />
          <Stat
            label="Peak end-of-day backlog"
            value={formatInteger(evaluation.peak_backlog)}
            unit="requests"
            tags={tag}
            note={`${formatInteger(evaluation.days_with_backlog)} days end with a backlog; ${formatInteger(evaluation.backlog_request_days)} request-days in total.`}
          />
          <Stat
            label="Overtime and surge"
            value={formatInteger(evaluation.overtime_hours)}
            unit="overtime hours"
            tags={tag}
            note={`${formatInteger(evaluation.surge_requests)} surge requests; ${formatUsd(evaluation.overtime_cost)} overtime and ${formatUsd(evaluation.surge_cost)} surge cash.`}
          />
        </div>
      )}
      {outcome.diagnosis !== null && (
        <dl className="kv">
          <dt>Least spend that reaches the target</dt>
          <dd>
            {outcome.diagnosis
              .diagnostic_min_discretionary_spend_to_reach_hypothetical_target_usd === null
              ? 'none: no amount of discretionary cash reaches it'
              : `${formatUsd(outcome.diagnosis.diagnostic_min_discretionary_spend_to_reach_hypothetical_target_usd)} (DIAGNOSTIC, not a budget request)`}
          </dd>
          <dt>Least terminal violation within the budget</dt>
          <dd>
            {formatInteger(outcome.diagnosis.least_terminal_violation_within_budget_requests)}{' '}
            requests
          </dd>
          <dt>Least violation with unlimited cash</dt>
          <dd>
            {formatInteger(
              outcome.diagnosis.least_terminal_violation_unlimited_discretionary_cash_requests,
            )}{' '}
            requests
          </dd>
        </dl>
      )}
      <div className="stack-2">
        <h4 className="small">Binding constraints</h4>
        <ul className="badge-list">
          {outcome.binding_constraints.map((name) => (
            <li key={name} className="chip">
              {name}
            </li>
          ))}
        </ul>
        <p className="xsmall muted">
          A binding constraint holds with equality at the optimum: relaxing it would change the
          plan. Counts in brackets are days.
        </p>
      </div>
      {certificate !== null && (
        <p className="small secondary">
          <strong>Certified:</strong> the solver's optimum {certificate.holds ? 'passed' : 'FAILED'}{' '}
          the primal and dual feasibility and duality-gap certificate
          {certificate.duality_gap !== null && ` (gap ${certificate.duality_gap.toExponential(1)})`}
          , and the plan{' '}
          {outcome.reported_plan?.independent_check_passed === true ? 'passed' : 'did not pass'} an
          independent re-check of every constraint.
          {outcome.problems.length > 0 && ` Problems recorded: ${outcome.problems.join('; ')}.`}
        </p>
      )}
      <DailyCharts outcome={outcome} daily={daily} reported={reported} target={target} />
      <PolicyComparison outcome={outcome} />
    </div>
  );
}

function DailyCharts({
  outcome,
  daily,
  reported,
  target,
}: {
  outcome: ScenarioOutcome;
  daily: readonly DayPlan[];
  reported: string;
  target: number | null;
}) {
  const [view, setView] = useState<View>('chart');
  const days = daily.filter((d) => d.plan === reported).sort((a, b) => a.day - b.day);
  const diagnostic = outcome.feasibility !== 'strict' ? ' (DIAGNOSTIC plan)' : '';
  const byPlan = (plan: string) =>
    daily.filter((d) => d.plan === plan).sort((a, b) => a.day - b.day);
  const lines = [
    { id: reported, label: `Reported plan${diagnostic}`, color: 'var(--series-1)', days },
    {
      id: 'fixed_capacity',
      label: POLICIES.fixed_capacity ?? 'fixed_capacity',
      color: 'var(--series-2)',
      days: byPlan('fixed_capacity'),
    },
    {
      id: 'backlog_trigger',
      label: POLICIES.backlog_trigger ?? 'backlog_trigger',
      color: 'var(--series-3)',
      days: byPlan('backlog_trigger'),
    },
  ].filter((line) => line.days.length > 0);
  if (days.length === 0) return null;
  return (
    <div className="stack-3">
      <div className="chart-toolbar">
        <h4 className="small">Daily resources and backlog{diagnostic}</h4>
        <ViewToggle value={view} onChange={setView} label="Daily plan" />
      </div>
      {view === 'chart' ? (
        <>
          <StackedColumns
            label={`Daily capacity of the reported plan${diagnostic}`}
            summary={`Daily HYPOTHETICAL capacity over ${days.length} days: regular capacity, overtime and surge, with the planned demand path as a line (requests/day).`}
            categories={days.map((d) => formatShortDate(dateValue(d.date)))}
            stacks={[
              {
                id: 'regular',
                label: 'Regular capacity',
                color: 'var(--series-1)',
                values: days.map((d) => d.regular_capacity),
              },
              {
                id: 'overtime',
                label: 'Overtime capacity',
                color: 'var(--series-2)',
                values: days.map((d) => d.overtime_capacity),
              },
              {
                id: 'surge',
                label: 'Surge requests',
                color: 'var(--series-3)',
                values: days.map((d) => d.surge_requests),
              },
            ]}
            overlays={[
              {
                id: 'demand',
                label: 'Demand path',
                color: 'var(--ink)',
                values: days.map((d) => d.demand),
              },
            ]}
            extraRows={(index) => {
              const day = days[index];
              return day === undefined
                ? []
                : [
                    { label: 'Served', value: `${formatInteger(day.served)} requests` },
                    {
                      label: 'End-of-day backlog',
                      value: `${formatInteger(day.backlog)} requests`,
                    },
                  ];
            }}
            formatValue={(v) => `${formatInteger(v)}/day`}
            formatTick={formatCompact}
          />
          <LineChart
            label="End-of-day backlog: reported plan against two simple policies"
            summary={`End-of-day backlog (requests) of the reported plan, the fixed-capacity policy and the backlog-trigger policy over ${days.length} days${target === null ? '' : `; HYPOTHETICAL terminal target ${formatInteger(target)} requests`}.`}
            series={lines.map((line) => ({
              id: line.id,
              label: line.label,
              color: line.color,
              points: line.days.map((d) => ({ x: dateValue(d.date), y: d.backlog })),
            }))}
            levels={
              target === null
                ? []
                : [{ y: target, label: `Terminal target ${formatInteger(target)} (HYPOTHETICAL)` }]
            }
            formatValue={(v) => `${formatInteger(v)} requests`}
            formatTick={formatCompact}
            formatX={(x) => formatWeekdayDate(isoDay(x))}
            yDomain={[
              0,
              Math.max(1, target ?? 0, ...lines.flatMap((l) => l.days.map((d) => d.backlog))),
            ]}
            height={240}
          />
        </>
      ) : (
        <DataTable
          caption={`Daily plan${diagnostic}: requests/day except overtime (staff hours) and backlog (requests)`}
          rows={days}
          rowKey={(d) => d.date}
          columns={[
            {
              key: 'date',
              header: 'Date',
              rowHeader: true,
              cell: (d) => formatWeekdayDate(d.date),
            },
            {
              key: 'demand',
              header: 'Demand',
              numeric: true,
              cell: (d) => formatInteger(d.demand),
            },
            {
              key: 'regular',
              header: 'Regular capacity',
              numeric: true,
              cell: (d) => formatInteger(d.regular_capacity),
            },
            {
              key: 'ot',
              header: 'Overtime hours',
              numeric: true,
              cell: (d) => formatInteger(d.overtime_hours),
            },
            {
              key: 'otc',
              header: 'Overtime capacity',
              numeric: true,
              cell: (d) => formatInteger(d.overtime_capacity),
            },
            {
              key: 'surge',
              header: 'Surge',
              numeric: true,
              cell: (d) => formatInteger(d.surge_requests),
            },
            {
              key: 'served',
              header: 'Served',
              numeric: true,
              cell: (d) => formatInteger(d.served),
            },
            {
              key: 'backlog',
              header: 'Backlog',
              numeric: true,
              cell: (d) => formatInteger(d.backlog),
            },
          ]}
        />
      )}
    </div>
  );
}

function PolicyComparison({ outcome }: { outcome: ScenarioOutcome }) {
  if (outcome.comparisons.length === 0) return null;
  return (
    <div className="stack-2">
      <h4 className="small">Against simple policies under the same assumptions</h4>
      <DataTable
        caption="Differences are reported plan minus policy: cash in USD, backlog in request-days and requests (negative: the reported plan has less)."
        rows={outcome.comparisons}
        rowKey={(c) => c.policy}
        columns={[
          {
            key: 'policy',
            header: 'Policy',
            rowHeader: true,
            cell: (c) => POLICIES[c.policy] ?? humanise(c.policy),
          },
          {
            key: 'relation',
            header: 'Relation',
            cell: (c) => RELATION[c.relation_of_reported_plan] ?? c.relation_of_reported_plan,
          },
          {
            key: 'cash',
            header: 'Cash difference',
            numeric: true,
            cell: (c) => formatSignedUsd(c.discretionary_cost_difference),
          },
          {
            key: 'days',
            header: 'Request-days difference',
            numeric: true,
            cell: (c) => formatSigned(c.request_days_difference),
          },
          {
            key: 'terminal',
            header: 'Terminal backlog difference',
            numeric: true,
            cell: (c) => formatSigned(c.terminal_backlog_difference),
          },
          {
            key: 'violates',
            header: 'Policy violates',
            cell: (c) =>
              c.policy_constraints_violated.length === 0
                ? 'nothing'
                : c.policy_constraints_violated.join(', '),
          },
        ]}
      />
      <p className="xsmall muted">
        At equal cash, the linear programme leaves{' '}
        {outcome.comparisons
          .map((c) =>
            c.request_days_difference_at_same_cash === null
              ? null
              : `${formatNumber(Math.abs(c.request_days_difference_at_same_cash), 0)} request-days ${c.request_days_difference_at_same_cash <= 0 ? 'fewer' : 'more'} than the ${humanise(c.policy).toLowerCase()} policy`,
          )
          .filter((text): text is string => text !== null)
          .join(' and ') || 'no comparable figure'}
        . These comparisons show what optimisation adds under the assumed values, nothing about real
        operations.
      </p>
    </div>
  );
}

function formatSignedUsd(value: number): string {
  const text = formatUsd(Math.abs(value));
  if (Math.round(value) > 0) return `+${text}`;
  if (Math.round(value) < 0) return `−${text}`;
  return text;
}

export { formatUsdCompact };
