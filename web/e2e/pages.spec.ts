/**
 * (M9) The static build in a real browser, served as GitHub Pages serves a project site
 * (scripts/pages-server.mjs, under /demand-forecasting-model/): no API, no Python, no
 * rewrites. Page paths are relative to the site's address (no leading slash).
 */
import { readFileSync } from 'node:fs';

import { expect, test, type Page } from '@playwright/test';

import { clean, open, PAGES, watch } from './helpers';

const BASE = '/demand-forecasting-model/';
/** A page's path relative to the site: '/forecasting' is 'forecasting', '/' is ''. */
const at = (path: string) => path.slice(1);

const site = JSON.parse(
  readFileSync(new URL('../dist-pages/atlas-static-site.json', import.meta.url), 'utf-8'),
) as { base: string; files: Record<string, { sha256: string }> };
const exported = JSON.parse(
  readFileSync(new URL('../static-export/atlas-static-export.json', import.meta.url), 'utf-8'),
) as { files: Record<string, { request: string }>; scientific_status: Record<string, unknown> };

async function heading(page: Page, name: string) {
  await expect(page.getByRole('heading', { level: 1, name })).toBeVisible();
}

test('the build is the one the site manifest describes, under the repository path', () => {
  expect(site.base).toBe(BASE);
  for (const page of PAGES) {
    const file = page.path === '/' ? 'index.html' : `${at(page.path)}.html`;
    expect(Object.keys(site.files)).toContain(file);
  }
  expect(Object.keys(site.files)).toContain('404.html');
  for (const file of Object.keys(exported.files))
    expect(Object.keys(site.files)).toContain(`data/${file}`);
});

test('every page loads from the static files, read-only, with no error or CSP violation', async ({
  page,
  baseURL,
}) => {
  const recorded = await watch(page);
  for (const { path, heading: name } of PAGES) await open(page, at(path), name);
  await expect(page.getByText('Static demo · read-only')).toBeVisible();
  await clean(recorded);
  const origin = new URL(baseURL!).origin;
  for (const request of recorded.requests) {
    const url = new URL(request.url());
    expect(url.origin, request.url()).toBe(origin);
    expect(url.pathname.startsWith(BASE), request.url()).toBe(true);
    // Read-only: every request is a GET of a file of the site, never the API.
    expect(request.method(), request.url()).toBe('GET');
    expect(url.pathname, request.url()).not.toContain('/api/');
  }
  // Every data file read is one the export recorded.
  const data = recorded.requests
    .map((r) => new URL(r.url()).pathname)
    .filter((path) => path.startsWith(`${BASE}data/`))
    .map((path) => path.slice(`${BASE}data/`.length));
  expect(data.length).toBeGreaterThan(0);
  for (const file of data) expect(Object.keys(exported.files)).toContain(file);
});

test('a deep link and a reload of every page are answered 200 with that page', async ({ page }) => {
  for (const { path, heading: name } of PAGES) {
    const response = await page.goto(at(path));
    expect(response?.status(), path).toBe(200);
    await heading(page, name);
    await page.reload();
    await heading(page, name);
    expect(new URL(page.url()).pathname).toBe(`${BASE}${at(path)}`);
  }
});

test('a deep link keeps its query: a reference scenario survives a reload', async ({ page }) => {
  await page.goto('decisions?scenario=reduced_budget');
  await expect(page.getByLabel('Scenario', { exact: true })).toHaveValue('reduced_budget');
  await expect(page.getByText('DIAGNOSTIC: not a plan for the stated constraints')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Scenario', { exact: true })).toHaveValue('reduced_budget');
});

test('the other addresses of a page show it at its one canonical address', async ({ page }) => {
  await page.goto('forecasting.html');
  await heading(page, 'Forecasting');
  await expect(page).toHaveURL(new RegExp(`${BASE}forecasting$`));
  await page.goto('index.html');
  await heading(page, PAGES[0].heading);
  await expect(page).toHaveURL(new RegExp(`${BASE}$`));
  // No such file: the host answers with 404.html, the app itself, at the canonical page.
  const slash = await page.goto('uncertainty/');
  expect(slash?.status()).toBe(404);
  await heading(page, 'Uncertainty and risk');
  await expect(page).toHaveURL(new RegExp(`${BASE}uncertainty$`));
  // The site's address without its slash is redirected to it.
  const bare = await page.goto(BASE.slice(0, -1));
  expect(bare?.status()).toBe(200);
  await expect(page).toHaveURL(new RegExp(`${BASE}$`));
});

test('an unknown address is a 404 that lists the pages, and its links work', async ({ page }) => {
  const response = await page.goto('no-such-page');
  expect(response?.status()).toBe(404);
  await heading(page, 'Page not found');
  await page.getByRole('main').getByRole('link', { name: 'Forecasting' }).click();
  await heading(page, 'Forecasting');
  await expect(page).toHaveURL(new RegExp(`${BASE}forecasting$`));
});

test('the back and forward buttons move between pages', async ({ page, isMobile }) => {
  test.skip(isMobile, 'the phone menu is covered by pages-mobile.spec.ts');
  await open(page, '', PAGES[0].heading);
  const nav = page.getByRole('navigation', { name: 'Dashboard sections' });
  await nav.getByRole('link', { name: /Forecasting/ }).click();
  await heading(page, 'Forecasting');
  await nav.getByRole('link', { name: /Uncertainty/ }).click();
  await heading(page, 'Uncertainty and risk');
  await page.goBack();
  await heading(page, 'Forecasting');
  await page.goBack();
  await heading(page, PAGES[0].heading);
  await page.goForward();
  await heading(page, 'Forecasting');
  await expect(page).toHaveURL(new RegExp(`${BASE}forecasting$`));
});

test('the scientific limitations are next to the results they qualify', async ({ page }) => {
  expect(exported.scientific_status).toMatchObject({
    uncertainty_interval_status: 'failed_calibration',
    m2_h1_significant_after_holm: false,
    m2_selection_evidence_notice: true,
    m4_parameter_provenance: 'HYPOTHETICAL',
    projection_archived_notice: true,
  });
  await open(page, '', PAGES[0].heading);
  await expect(page.getByText('Research software', { exact: true })).toBeVisible();
  const status = page.locator('section', {
    has: page.getByRole('heading', { name: 'Scientific status' }),
  });
  await expect(status.getByText('Not significant after Holm')).toBeVisible();
  await expect(status.getByText('FAILED_CALIBRATION').first()).toBeVisible();
  await expect(status.getByText('HYPOTHETICAL', { exact: true })).toBeVisible();
  await expect(page.getByText('An archived projection, not a live forecast')).toBeVisible();
  await expect(
    page.getByText(/^ARCHIVED PROJECTION: issued from the frozen snapshot/).first(),
  ).toBeVisible();
  await expect(page.getByText(/This is a static, archived copy/)).toBeVisible();

  await open(page, 'forecasting', 'Forecasting');
  const tests = page.locator('section', {
    has: page.getByRole('heading', { name: 'Is the model better than the baselines?' }),
  });
  await expect(
    tests.getByText(/not statistically significant after the Holm adjustment/),
  ).toBeVisible();
  // The M8 erratum, next to the selection and the verdicts it qualifies.
  await expect(tests.getByText(/^M2 SELECTION EVIDENCE: /)).toBeVisible();
  await expect(tests.getByText(/a correctly sized test would most likely have kept/)).toBeVisible();

  await open(page, 'uncertainty', 'Uncertainty and risk');
  await expect(page.getByText(/read these bands as illustrations, not guarantees/)).toBeVisible();
  await expect(page.getByText('FAILED_CALIBRATION').first()).toBeVisible();

  await open(page, 'decisions', 'Decision optimisation');
  await expect(
    page.getByText('HYPOTHETICAL: not a staffing, budget or capacity recommendation'),
  ).toBeVisible();
  await expect(page.getByText(/DIAGNOSTIC/).first()).toBeVisible();
});

test('the simulator says why it cannot run, explores the recorded scenarios and never posts', async ({
  page,
}) => {
  const recorded = await watch(page);
  await open(page, 'simulator', 'Hypothetical simulator');
  const disabled = page.locator('.callout', {
    hasText: 'Interactive simulations are disabled in this deployment',
  });
  await expect(disabled).toContainText('with no ATLAS server behind it');
  await expect(disabled).toContainText('no new simulation can be computed');
  await expect(page.getByText('A recorded result, not a new simulation')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run simulation' })).toBeDisabled();
  await expect(page.getByText('Simulations are exploratory, not operational advice')).toBeVisible();

  const select = page.getByLabel('Reference scenario', { exact: true });
  const scenarios = await select
    .locator('option')
    .evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value));
  expect(scenarios.length).toBe(9);
  for (const scenario of scenarios) {
    await select.selectOption(scenario);
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
    await expect(page.locator('[data-kind="error"]')).toHaveCount(0);
    await expect(page.getByRole('heading', { level: 3, name: /^Recorded result: / })).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`simulator\\?scenario=${scenario}$`));
  }
  // A relaxed scenario keeps its DIAGNOSTIC label, a choice survives a reload.
  await select.selectOption('reduced_budget');
  await expect(
    page.getByText('DIAGNOSTIC: not a plan for the stated constraints').first(),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Reference scenario', { exact: true })).toHaveValue(
    'reduced_budget',
  );
  // The form is shown, read-only, and submitting it sends nothing.
  await page.getByRole('button', { name: 'Run simulation' }).click({ force: true });
  await clean(recorded);
  expect(recorded.requests.filter((r) => r.method() !== 'GET')).toEqual([]);
  expect(recorded.requests.filter((r) => r.url().includes('/simulations'))).toEqual([]);
});

test('every history window and backtest period comes from the export', async ({ page }) => {
  await open(page, 'forecasting', 'Forecasting');
  // The days each window shows (the snapshot has 1,277).
  for (const [window, days] of [
    ['90 days', '90'],
    ['1 year', '365'],
    ['All', '1,277'],
    ['180 days', '180'],
  ] as const) {
    await page.getByRole('radio', { name: window, exact: true }).check();
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
    await expect(page.locator('[data-kind="error"]')).toHaveCount(0);
    await expect(
      page.getByText(new RegExp(`^History: .*, ${days} days, from the legacy-v0`)),
    ).toBeVisible();
  }
  const horizon = page.locator('section', {
    has: page.getByRole('heading', { name: 'Accuracy by forecast horizon' }),
  });
  for (const period of ['Development', 'Legacy window', 'Confirmation']) {
    await horizon.getByRole('radio', { name: period, exact: true }).check();
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
    await expect(page.locator('[data-kind="error"]')).toHaveCount(0);
  }
});

test('a data file that differs from the build record is refused, never shown', async ({ page }) => {
  await page.route(`**${BASE}data/models.json`, async (route) => {
    const response = await route.fetch();
    const body = (await response.text()).replace(
      '"significant_after_holm":false',
      '"significant_after_holm":true',
    );
    await route.fulfill({ response, body });
  });
  // The Overview's H1 tile and the Forecasting tests both come from this file: neither verdict
  // may appear, only the refusal.
  await open(page, '', PAGES[0].heading, { failures: true });
  await expect(page.getByText(/is not the one this build recorded/).first()).toBeVisible();
  await expect(page.getByText('Significant after Holm', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Not significant after Holm', { exact: true })).toHaveCount(0);
  await open(page, 'forecasting', 'Forecasting', { failures: true });
  await expect(page.getByText(/is not the one this build recorded/).first()).toBeVisible();
  await expect(page.getByText(/significant after the Holm adjustment/)).toHaveCount(0);
});

test('a missing data file is reported where its numbers would be', async ({ page }) => {
  await page.route(`**${BASE}data/scenarios.json`, (route) =>
    route.fulfill({ status: 404, contentType: 'text/html', body: '<h1>404</h1>' }),
  );
  await open(page, '', PAGES[0].heading, { failures: true });
  await expect(page.getByText('A data file of this site is missing').first()).toBeVisible();
  // The other panels keep their own answers.
  await expect(page.getByText('Not significant after Holm')).toBeVisible();
});

test('the licence notices open under the repository path', async ({ page, request }) => {
  await open(page, '', PAGES[0].heading);
  for (const name of ['third-party licences', 'Inter font licence']) {
    const href = await page.getByRole('link', { name }).getAttribute('href');
    expect(href?.startsWith(BASE), name).toBe(true);
    const answer = await request.get(href!);
    expect(answer.status()).toBe(200);
    expect(answer.headers()['content-type']).toContain('text/plain');
  }
});
