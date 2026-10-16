import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

import { expect, test } from '@playwright/test';

import { clean, open, PAGES, watch } from './helpers';

const manifest = JSON.parse(
  readFileSync(new URL('../dist/atlas-dashboard.json', import.meta.url), 'utf-8'),
) as {
  files: Record<string, { sha256: string }>;
};

test('every page loads from the API, same origin, with no error or CSP violation', async ({
  page,
  baseURL,
}) => {
  const recorded = await watch(page);
  for (const { path, heading } of PAGES) {
    await open(page, path, heading);
  }
  await clean(recorded);
  const origins = new Set(recorded.requests.map((r) => new URL(r.url()).origin));
  expect([...origins]).toEqual([new URL(baseURL!).origin]);
});

test('the scientific warnings are next to the numbers they qualify', async ({ page }) => {
  await open(page, '/', PAGES[0].heading);
  const status = page.locator('section', {
    has: page.getByRole('heading', { name: 'Scientific status' }),
  });
  await expect(status.getByText('Not significant after Holm')).toBeVisible();
  await expect(status.getByText('FAILED_CALIBRATION').first()).toBeVisible();
  await expect(status.getByText('HYPOTHETICAL', { exact: true })).toBeVisible();
  await expect(page.getByText('An archived projection, not a live forecast')).toBeVisible();

  await open(page, '/uncertainty', 'Uncertainty and risk');
  await expect(page.getByText(/read these bands as illustrations, not guarantees/)).toBeVisible();
  await open(page, '/decisions', 'Decision optimisation');
  await expect(
    page.getByText('HYPOTHETICAL: not a staffing, budget or capacity recommendation'),
  ).toBeVisible();
  await expect(page.getByText(/DIAGNOSTIC/).first()).toBeVisible();
});

test('every reference scenario opens, from the real API, with no error', async ({ page }) => {
  await open(page, '/decisions', 'Decision optimisation');
  const select = page.getByLabel('Scenario', { exact: true });
  const scenarios = await select
    .locator('option')
    .evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value));
  expect(scenarios.length).toBe(9);
  for (const scenario of scenarios) {
    await select.selectOption(scenario);
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
    await expect(page.locator('[data-kind="error"]')).toHaveCount(0);
    await expect(page.getByRole('heading', { name: /Daily resources and backlog/ })).toBeVisible();
  }
});

test('a reference scenario opens from its link and survives a reload', async ({ page }) => {
  await page.goto('/decisions?scenario=reduced_budget');
  await expect(page.getByLabel('Scenario', { exact: true })).toHaveValue('reduced_budget');
  await expect(page.getByText('DIAGNOSTIC: not a plan for the stated constraints')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Scenario', { exact: true })).toHaveValue('reduced_budget');
});

test('the simulator runs the real M4 engine with only the changed inputs', async ({ page }) => {
  const recorded = await watch(page);
  await open(page, '/simulator', 'Hypothetical simulator');
  const horizon = page.getByRole('textbox', { name: /Planning horizon/ });
  await horizon.fill('7');
  await page.getByRole('textbox', { name: /Discretionary budget/ }).fill('20000');
  const posted = page.waitForRequest(
    (r) => r.url().endsWith('/api/v1/simulations') && r.method() === 'POST',
  );
  await page.getByRole('button', { name: 'Run simulation' }).click();
  expect((await posted).postDataJSON()).toEqual({
    horizon_days: 7,
    parameters: { discretionary_budget: 20000 },
  });
  const result = page.getByRole('heading', { name: /Simulation result/ });
  await expect(result).toBeVisible({ timeout: 30_000 });
  await expect(result).toBeFocused();
  await expect(page.getByText('An exploratory mathematical demonstration')).toBeVisible();
  await expect(page.getByText(/Certified:/)).toBeVisible();
  await expect(page.getByText(/no plan spending less than 90,511 USD/)).toBeVisible();
  await clean(recorded);
});

test('M4 refuses inputs it cannot plan, and the form says which', async ({ page }) => {
  await open(page, '/simulator', 'Hypothetical simulator');
  await page.getByRole('textbox', { name: /Planning horizon/ }).fill('7');
  const surge = page.getByRole('textbox', { name: /Surge available from day/ });
  await surge.fill('20');
  await page.getByRole('button', { name: 'Run simulation' }).click();
  await expect(page.getByText(/Simulation not run/)).toBeVisible();
  await expect(surge).toHaveAttribute('aria-invalid', 'true');
  await expect(
    page.getByText('surge_available_from_day must be within 1..horizon_days + 1').first(),
  ).toBeVisible();
});

test('invalid input is refused before anything is sent', async ({ page }) => {
  await open(page, '/simulator', 'Hypothetical simulator');
  let posted = 0;
  page.on('request', (r) => {
    if (r.method() === 'POST') posted += 1;
  });
  const wage = page.getByRole('textbox', { name: /Regular wage/ });
  await wage.fill('0.001');
  await page.getByRole('button', { name: 'Run simulation' }).click();
  await expect(page.getByText('1 input needs attention')).toBeVisible();
  await expect(wage).toHaveAccessibleDescription(/A rate must be 0 or at least 0.01/);
  expect(posted).toBe(0);
});

test('the served files are the build, byte for byte, with the dashboard policy', async ({
  request,
}) => {
  for (const [path, { sha256 }] of Object.entries(manifest.files)) {
    const response = await request.get(path === 'index.html' ? '/' : `/${path}`);
    expect(response.status(), path).toBe(200);
    expect(
      createHash('sha256')
        .update(await response.body())
        .digest('hex'),
      path,
    ).toBe(sha256);
    expect(response.headers()['content-security-policy']).toContain("script-src 'self'");
  }
  expect((await request.get('/atlas-dashboard.json')).status()).toBe(404);
});
