import { expect, test } from '@playwright/test';

import { clean, open, PAGES, watch } from './helpers';

test('the private deployment serves the dashboard through nginx, with an account', async ({
  page,
}) => {
  const recorded = await watch(page);
  for (const { path, heading } of PAGES) await open(page, path, heading);
  await expect(page.getByText('Private deployment', { exact: true })).toBeVisible();
  await clean(recorded);
});

// (Navigations, not Playwright's request client: only the browser maps the site's name.)
test('the headers come from nginx: the dashboard policy on HTML, HSTS', async ({ page }) => {
  const html = (await page.goto('/'))!;
  expect(html.headers()['content-security-policy']).toContain("require-trusted-types-for 'script'");
  expect(html.headers()['strict-transport-security']).toMatch(/^max-age=/);
  const api = (await page.goto('/api/v1/forecasts'))!;
  expect(api.headers()['content-security-policy']).toBe(
    "default-src 'none'; frame-ancestors 'none'",
  );
});

test('a simulation runs from the browser without the browser holding a key', async ({ page }) => {
  const recorded = await watch(page);
  await open(page, '/simulator', 'Hypothetical simulator');
  await page.getByRole('textbox', { name: /Planning horizon/ }).fill('7');
  await page.getByRole('button', { name: 'Run simulation' }).click();
  await expect(page.getByRole('heading', { name: /Simulation result/ })).toBeVisible({
    timeout: 30_000,
  });
  await clean(recorded);
});

test('without an account nothing is served', async ({ browser, baseURL }) => {
  // Wrong credentials, explicitly (a new context would otherwise reuse the project's).
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    httpCredentials: { username: 'alice', password: 'not-the-password-0000' },
  });
  const page = await context.newPage();
  for (const path of ['/', '/atlas-deployment.json', '/api/v1/forecasts']) {
    const response = (await page.goto(`${baseURL}${path}`))!;
    expect(response.status(), path).toBe(401);
  }
  await context.close();
});
