/**
 * What the second review found in the plan views (plan.tsx, Decisions.tsx), kept fixed: each
 * test fails on the defect it names.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ScenarioDetailResponse, SimulationResponse } from '../api/schemas';
import { z } from '../api/zod';
import { App } from '../App';
import { formatInteger } from '../lib/format';
import { FIXTURES, json, mockApi } from '../test/api';
import { PlanOutcome } from './plan';

function at(path: string) {
  window.history.replaceState(null, '', path);
  return render(<App />);
}

/** A deep copy of a fixture, to change one field of it. */
function copy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

type Detail = z.infer<typeof ScenarioDetailResponse>['data'];

/** PlanOutcome as the simulator renders it (default heading level), from a recorded answer. */
function plan(data: Pick<Detail, 'outcome' | 'plans' | 'daily_plans'>) {
  return render(<PlanOutcome outcome={data.outcome} plans={data.plans} daily={data.daily_plans} />);
}

const scenario = (fixture: unknown) => z.parse(ScenarioDetailResponse, copy(fixture)).data;
const simulation = (fixture: unknown) => z.parse(SimulationResponse, copy(fixture)).data;

/** The block under the "Binding constraints" heading. */
function bindingBlock(): HTMLElement {
  return screen.getByRole('heading', { name: 'Binding constraints' }).parentElement as HTMLElement;
}

describe('binding constraints are what M4 says they are', () => {
  it('keeps a missed target apart from the limits and makes no causal claim', () => {
    plan(scenario(FIXTURES.scenarioReduced));
    const block = bindingBlock();
    expect(
      within(block)
        .getAllByRole('listitem')
        .map((li) => li.textContent),
    ).toEqual(['budget', 'capacity (30 days)', 'overtime_daily_limit (6 days)']);
    expect(within(block).getByText('terminal_backlog_violated').closest('p')).toHaveTextContent(
      /above its HYPOTHETICAL terminal-backlog target\. It marks the plan; it is not a binding limit\./,
    );
    expect(within(block).getByText('DIAGNOSTIC')).toBeInTheDocument();
    expect(block).not.toHaveTextContent(/holds with equality at the optimum/);
    expect(block).toHaveTextContent(
      /A binding limit need not be a cause: being tight does not show that relaxing it would change the plan\./,
    );
  });

  it('says so when no limit binds instead of showing an empty list', () => {
    const data = simulation(FIXTURES.simulation);
    data.outcome.binding_constraints = [];
    plan(data);
    const block = bindingBlock();
    expect(within(block).getByText('None reported for this plan.')).toBeInTheDocument();
    expect(within(block).queryByRole('list')).not.toBeInTheDocument();
    expect(block).not.toHaveTextContent(/Counts in brackets/);
  });
});

describe('the certificate of a DIAGNOSTIC plan', () => {
  it('keeps the constraints the API did not check', () => {
    const data = scenario(FIXTURES.scenarioReduced);
    expect(data.outcome.reported_plan?.constraints_not_checked).toEqual(['terminal_backlog']);
    plan(data);
    expect(
      screen.getByText(/re-check of every constraint except terminal_backlog, which this plan/),
    ).toHaveTextContent(
      /re-check of every constraint except terminal_backlog, which this plan is not held to\./,
    );
  });

  it('says every constraint only when every constraint was checked', () => {
    plan(scenario(FIXTURES.scenarioNormal));
    expect(screen.getByText(/independent re-check of every constraint/)).toHaveTextContent(
      /re-check of every constraint\.$/,
    );
  });
});

describe('a DIAGNOSTIC plan that is one of several equally scored', () => {
  it('is described as such in the plan', () => {
    const data = simulation(FIXTURES.simulation);
    const relaxed = data.plans.find((p) => p.plan === 'relaxed_terminal_target');
    if (relaxed === undefined) throw new Error('fixture without a relaxed plan');
    relaxed.unique_optimal_plan = false;
    relaxed.unique_after_tie_breaks = false;
    plan(data);
    const text = screen.getByText(/No plan meets every HYPOTHETICAL constraint\./);
    expect(text).toHaveTextContent(/one of several equally scored HYPOTHETICAL plans that miss/);
    expect(text).toHaveTextContent(
      /even after them its daily overtime and surge are not determined/,
    );
    expect(text).not.toHaveTextContent(/the one that misses/);
  });

  it('is tagged in the feasibility table', async () => {
    const list = copy(FIXTURES.scenarios);
    const row = list.data.scenarios.find((s) => s.scenario_id === 'reduced_budget');
    if (row === undefined) throw new Error('fixture without reduced_budget');
    row.optimal_plan_unique = false;
    mockApi({ '/api/v1/scenarios': () => json(list) });
    at('/decisions');
    const table = await screen.findByRole(
      'table',
      { name: /reference scenarios over/ },
      { timeout: 5000 },
    );
    const tr = within(table)
      .getByRole('rowheader', { name: /Reduced budget/ })
      .closest('tr');
    expect(within(tr as HTMLElement).getByText('One of several equally scored')).toBeVisible();
  });
});

describe('the equal-cash sentence', () => {
  it('takes its direction from the rounded value, never from solver noise', () => {
    // The recorded simulation has +1.5e-11 request-days for both policies.
    plan(simulation(FIXTURES.simulation));
    const text = screen.getByText(/At equal cash/);
    expect(text).toHaveTextContent(
      /leaves as many request-days as the fixed capacity policy and as many request-days as the backlog trigger policy/,
    );
    expect(text).not.toHaveTextContent(/request-days more/);
  });

  it('gives a real difference with its direction, and says the target is dropped', () => {
    plan(scenario(FIXTURES.scenarioNormal));
    const text = screen.getByText(/At equal cash/);
    expect(text).toHaveTextContent(
      /as many request-days as the fixed capacity policy and 57,280 request-days fewer than the backlog trigger policy/,
    );
    expect(text).toHaveTextContent(/HYPOTHETICAL terminal-backlog target dropped/);
  });
});

describe('the daily table has every series the backlog chart draws', () => {
  it("lists the policies' end-of-day backlog by date", async () => {
    const data = scenario(FIXTURES.scenarioNormal);
    const user = userEvent.setup();
    plan(data);
    await user.click(screen.getByRole('radio', { name: 'Table' }));
    const table = screen.getByRole('table', { name: /Daily plan/ });
    expect(
      within(table).getByRole('columnheader', { name: 'Backlog (reported plan)' }),
    ).toBeVisible();
    const day3 = within(table).getAllByRole('row')[3] as HTMLElement;
    const cells = within(day3)
      .getAllByRole('cell')
      .map((td) => td.textContent);
    const backlog = (name: string) =>
      formatInteger(data.daily_plans.find((d) => d.plan === name && d.day === 3)?.backlog);
    expect(
      within(table)
        .getAllByRole('columnheader')
        .slice(-2)
        .map((th) => th.textContent),
    ).toEqual([
      'Backlog: Fixed-capacity policy (no overtime or surge)',
      'Backlog: Backlog-trigger policy',
    ]);
    expect(cells.slice(-3)).toEqual([
      backlog('optimized'),
      backlog('fixed_capacity'),
      backlog('backlog_trigger'),
    ]);
  });
});

describe('heading levels follow the page', () => {
  it('uses level 3 under the simulation result heading', () => {
    plan(simulation(FIXTURES.simulation));
    for (const name of [
      'Binding constraints',
      /Daily resources and backlog/,
      /Against simple policies/,
    ])
      expect(screen.getByRole('heading', { name, level: 3 })).toBeInTheDocument();
  });

  it('uses level 4 under the scenario heading of Decisions', async () => {
    mockApi();
    at('/decisions?scenario=reduced_budget');
    expect(
      await screen.findByRole(
        'heading',
        { name: 'Binding constraints', level: 4 },
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
  });
});

describe('the Decisions page', () => {
  const scrollIntoView = vi.fn();
  afterEach(() => {
    scrollIntoView.mockClear();
    Reflect.deleteProperty(Element.prototype, 'scrollIntoView');
  });

  it('moves the focus to the scenario detail from the feasibility table', async () => {
    Element.prototype.scrollIntoView = scrollIntoView;
    mockApi();
    const user = userEvent.setup();
    at('/decisions');
    const table = await screen.findByRole(
      'table',
      { name: /reference scenarios over/ },
      { timeout: 5000 },
    );
    within(table).getByRole('link', { name: 'Reduced budget' }).focus();
    await user.keyboard('{Enter}');
    expect(window.location.search).toBe('?scenario=reduced_budget');
    expect(document.activeElement).toBe(document.getElementById('scenario-detail'));
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it('leaves a modified click to the browser (a new tab or window)', async () => {
    Element.prototype.scrollIntoView = scrollIntoView;
    mockApi();
    at('/decisions');
    const table = await screen.findByRole(
      'table',
      { name: /reference scenarios over/ },
      { timeout: 5000 },
    );
    let prevented: boolean | undefined;
    // Records what the page did, then stops jsdom from navigating.
    const record = (event: MouseEvent) => {
      prevented = event.defaultPrevented;
      event.preventDefault();
    };
    document.addEventListener('click', record);
    try {
      fireEvent.click(within(table).getByRole('link', { name: 'Reduced budget' }), {
        ctrlKey: true,
      });
    } finally {
      document.removeEventListener('click', record);
    }
    expect(prevented).toBe(false);
    expect(window.location.search).toBe('');
  });

  it('gives the cash difference from the baseline its unit and direction', async () => {
    mockApi();
    const user = userEvent.setup();
    at('/decisions');
    await screen.findByText(/How to read these differences/, {}, { timeout: 5000 });
    await user.click(screen.getAllByRole('radio', { name: 'Table' })[0] as HTMLElement);
    const table = screen.getByRole('table', { name: /this scenario minus the baseline/ });
    expect(
      within(table).getByRole('columnheader', { name: 'Cash vs baseline (USD)' }),
    ).toBeVisible();
    expect(within(table).getByText('+$43,720')).toBeInTheDocument();
  });

  it('does not call every demand path the point forecast', async () => {
    mockApi();
    at('/decisions');
    expect(
      await screen.findByText(/or, where labelled, a per-day M3 quantile stress path/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/the demand \(the unvalidated point forecast, or a labelled stress path\)/),
    ).toBeInTheDocument();
  });
});
