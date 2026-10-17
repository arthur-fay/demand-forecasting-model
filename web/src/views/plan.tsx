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

/** M4's Pareto relation on discretionary cash, request-days and terminal backlog. */
const RELATION: Record<string, string> = {
  dominates: 'Reported plan lower on at least one of the three, higher on none',
  dominated: 'Policy lower on at least one of the three, higher on none',
  tradeoff: 'Each is lower on at least one of the three',
  equal: 'Equal on all three',
};

/**
 * Labels the API lists among the binding constraints that mark the plan itself (M4: "neither
 * is a binding limit"), shown apart from the limits.
 */
const PLAN_MARKERS = new Map([
  ['terminal_backlog_violated', 'the plan ends above its HYPOTHETICAL terminal-backlog target'],
  ['budget_exceeded', 'the plan, computed without the HYPOTHETICAL budget, spends above it'],
]);

/** What the API's uniqueness test leaves after the tie-breaks, as a clause. */
function afterTieBreaks(variant: PlanVariant): string {
  return variant.unique_after_tie_breaks === true
    ? ', and no other plan is left after them'
    : variant.unique_after_tie_breaks === false
      ? ', and even after them its daily overtime and surge are not determined'
      : '';
}

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

/**
 * `headingLevel` is the level of its sub-headings: 3 directly under a panel heading (the
 * simulator), 4 under a scenario heading (Decisions).
 */
export function PlanOutcome({
  outcome,
  plans,
  daily,
  headingLevel = 3,
}: {
  outcome: ScenarioOutcome;
  plans: readonly PlanVariant[];
  daily: readonly DayPlan[];
  headingLevel?: 3 | 4;
}) {
  const reported = reportedPlanName(outcome, daily);
  const evaluation = outcome.reported_plan?.evaluation ?? null;
  const certificate = outcome.reported_plan?.certificate ?? null;
  // Each check of the certificate is its largest violation in units of its tolerance.
  const checks =
    certificate === null
      ? []
      : [
          certificate.primal,
          certificate.dual_sign,
          certificate.stationarity,
          certificate.duality_gap,
        ].filter((value) => value !== null);
  const violation = checks.length > 0 ? Math.max(...checks) : null;
  const notChecked = outcome.reported_plan?.constraints_not_checked ?? [];
  const variant = plans.find((p) => p.plan === reported);
  const target = variant?.terminal_backlog_max ?? null;
  const diagnostic = outcome.feasibility !== 'strict';
  const tag = diagnostic ? <DiagnosticTag /> : <HypotheticalTag />;
  const limits = outcome.binding_constraints.filter((name) => !PLAN_MARKERS.has(name));
  const markers = outcome.binding_constraints.filter((name) => PLAN_MARKERS.has(name));
  const Heading = headingLevel === 3 ? 'h3' : 'h4';
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
            No plan meets every HYPOTHETICAL constraint. The plan below is{' '}
            {variant?.unique_optimal_plan === false
              ? 'one of several equally scored HYPOTHETICAL plans that miss'
              : 'the one that misses'}{' '}
            the terminal-backlog target by the least within the budget: its values are DIAGNOSTIC,
            and any spend shown is never a budget request.
            {variant?.unique_optimal_plan === false &&
              ` The tie-breaks (${outcome.reported_plan_selection}) choose this one${afterTieBreaks(variant)}.`}
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
          {variant?.unique_optimal_plan === false ? (
            <p>
              It is one of several equally scored HYPOTHETICAL plans: other plans reach the same
              optimal score under these assumed values. The tie-breaks (
              {outcome.reported_plan_selection}) choose this one{afterTieBreaks(variant)}. It
              answers a hypothetical question; it is not a recommendation.
            </p>
          ) : (
            <p>
              It is the certified optimum of the linear programme for these assumed values (
              {outcome.reported_plan_selection}). That makes it a correct answer to a hypothetical
              question, not a recommendation.
            </p>
          )}
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
        <Heading className="small">Binding constraints</Heading>
        {limits.length > 0 ? (
          <ul className="badge-list">
            {limits.map((name) => (
              <li key={name} className="chip">
                {name}
              </li>
            ))}
          </ul>
        ) : (
          <p className="small">
            {outcome.reported_plan === null
              ? 'No plan was reported.'
              : 'None reported for this plan.'}
          </p>
        )}
        {markers.map((name) => (
          <p key={name} className="small">
            <DiagnosticTag /> <span className="mono">{name}</span>: {PLAN_MARKERS.get(name)}. It
            marks the plan; it is not a binding limit.
          </p>
        ))}
        <p className="xsmall muted">
          Read from the reported plan itself: the limits it leaves no slack on (within tolerance),
          not counting daily limits of zero. A binding limit need not be a cause: being tight does
          not show that relaxing it would change the plan.
          {limits.length > 0 && ' Counts in brackets are days.'}
        </p>
      </div>
      {certificate !== null && (
        <p className="small secondary">
          <strong>Certified:</strong> the solver's optimum {certificate.holds ? 'passed' : 'FAILED'}{' '}
          the primal and dual feasibility and duality-gap certificate within its tolerances
          {violation !== null &&
            ` (largest violation ${violation.toExponential(1)} of its tolerance)`}
          , and the plan{' '}
          {outcome.reported_plan?.independent_check_passed === true
            ? 'passed'
            : outcome.reported_plan?.independent_check_passed === false
              ? 'did not pass'
              : 'was not given'}{' '}
          an independent re-check of every constraint
          {notChecked.length > 0 &&
            ` except ${notChecked.join(', ')}, which this plan is not held to`}
          .{outcome.problems.length > 0 && ` Problems recorded: ${outcome.problems.join('; ')}.`}
        </p>
      )}
      <DailyCharts
        outcome={outcome}
        daily={daily}
        reported={reported}
        target={target}
        heading={Heading}
      />
      <PolicyComparison outcome={outcome} heading={Heading} />
    </div>
  );
}

function DailyCharts({
  outcome,
  daily,
  reported,
  target,
  heading: Heading,
}: {
  outcome: ScenarioOutcome;
  daily: readonly DayPlan[];
  reported: string;
  target: number | null;
  heading: 'h3' | 'h4';
}) {
  const [view, setView] = useState<View>('chart');
  const days = daily.filter((d) => d.plan === reported).sort((a, b) => a.day - b.day);
  const diagnostic = outcome.feasibility !== 'strict' ? ' (DIAGNOSTIC plan)' : '';
  const byPlan = (plan: string) =>
    daily.filter((d) => d.plan === plan).sort((a, b) => a.day - b.day);
  // Each line has its own pattern as well as its colour: the policies often coincide with
  // the reported plan for days, and one line can hide another.
  const lines = [
    {
      id: reported,
      label: `Reported plan${diagnostic}`,
      color: 'var(--series-1)',
      style: 'solid' as const,
      days,
    },
    {
      id: 'fixed_capacity',
      label: POLICIES.fixed_capacity ?? 'fixed_capacity',
      color: 'var(--series-2)',
      style: 'dashed' as const,
      days: byPlan('fixed_capacity'),
    },
    {
      id: 'backlog_trigger',
      label: POLICIES.backlog_trigger ?? 'backlog_trigger',
      color: 'var(--series-3)',
      style: 'dotted' as const,
      days: byPlan('backlog_trigger'),
    },
  ].filter((line) => line.days.length > 0);
  // The table twin of the backlog chart carries the policies' backlog too, joined by date.
  const policies = lines
    .filter((line) => line.id !== reported)
    .map((line) => ({
      id: line.id,
      label: line.label,
      backlog: new Map(line.days.map((d) => [d.date, d.backlog])),
    }));
  if (days.length === 0) return null;
  return (
    <div className="stack-3">
      <div className="chart-toolbar">
        <Heading className="small">Daily resources and backlog{diagnostic}</Heading>
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
          <p className="chart-title">
            End-of-day backlog: the reported plan and two simple policies
          </p>
          <LineChart
            label="End-of-day backlog: reported plan against two simple policies"
            summary={`End-of-day backlog (requests) of the reported plan, the fixed-capacity policy and the backlog-trigger policy over ${days.length} days${target === null ? '' : `; HYPOTHETICAL terminal target ${formatInteger(target)} requests`}.`}
            series={lines.map((line) => ({
              id: line.id,
              label: line.label,
              color: line.color,
              style: line.style,
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
          caption={`Daily plan${diagnostic}: requests/day except overtime (staff hours) and backlog (requests)${policies.length > 0 ? ", with the simple policies' end-of-day backlog under the same assumptions" : ''}`}
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
              header: 'Backlog (reported plan)',
              numeric: true,
              cell: (d) => formatInteger(d.backlog),
            },
            ...policies.map((policy) => ({
              key: policy.id,
              header: `Backlog: ${policy.label}`,
              numeric: true,
              cell: (d: DayPlan) => formatInteger(policy.backlog.get(d.date)),
            })),
          ]}
        />
      )}
    </div>
  );
}

function PolicyComparison({
  outcome,
  heading: Heading,
}: {
  outcome: ScenarioOutcome;
  heading: 'h3' | 'h4';
}) {
  if (outcome.comparisons.length === 0) return null;
  const diagnostic = outcome.feasibility !== 'strict';
  return (
    <div className="stack-2">
      <Heading className="small">
        Against simple policies under the same assumptions
        {diagnostic && ' (the reported plan is DIAGNOSTIC)'}
      </Heading>
      <DataTable
        caption={`Differences are reported plan minus policy: cash in USD, backlog in request-days and requests (negative: the reported plan has less). The relation compares discretionary cash, request-days and terminal backlog (lower is better on each). A plan that violates the HYPOTHETICAL terminal target is not comparable on score${diagnostic ? ': the reported plan here misses it, so its values are DIAGNOSTIC' : ''}.`}
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
            key: 'comparable',
            header: 'Comparable on score',
            cell: (c) => (c.comparable_on_score ? 'yes' : 'no'),
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
        At equal cash (at most each policy's discretionary cash) and with the HYPOTHETICAL
        terminal-backlog target dropped, the linear programme leaves{' '}
        {outcome.comparisons
          .map(sameCash)
          .filter((text): text is string => text !== null)
          .join(' and ') || 'no comparable figure'}
        ; without the target it can end with a higher terminal backlog than the policy. These
        comparisons show what optimisation adds under the assumed values, nothing about real
        operations.
      </p>
    </div>
  );
}

/** The request-days difference at equal cash in words, its direction from the rounded value. */
function sameCash(comparison: ScenarioOutcome['comparisons'][number]): string | null {
  const value = comparison.request_days_difference_at_same_cash;
  if (value === null) return null;
  const days = Math.round(value); // -0 counts as zero
  const policy = `the ${humanise(comparison.policy).toLowerCase()} policy`;
  return days === 0
    ? `as many request-days as ${policy}`
    : `${formatInteger(Math.abs(days))} request-days ${days < 0 ? 'fewer' : 'more'} than ${policy}`;
}

/** A signed USD difference; its sign and its amount come from the same rounded value. */
export function formatSignedUsd(value: number): string {
  const rounded = Math.round(value);
  const text = formatUsd(Math.abs(rounded));
  if (rounded > 0) return `+${text}`;
  if (rounded < 0) return `−${text}`;
  return text;
}

export { formatUsdCompact };
