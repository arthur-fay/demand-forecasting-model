import { expect, test } from '@playwright/test';

import { open } from './helpers';

const json = (
  status: number,
  code: string,
  message: string,
  headers: Record<string, string> = {},
) => ({
  status,
  contentType: 'application/json',
  headers,
  body: JSON.stringify({ error: { code, message, request_id: 'e2e-request-1', details: [] } }),
});

test('a slow connection is announced, then the data appears', async ({ page }) => {
  await page.route('**/api/v1/uncertainty/status', async (route) => {
    // Well past the 2.5 s threshold: the notice stays up long enough to be seen on a busy runner.
    await new Promise((resolve) => setTimeout(resolve, 6000));
    await route.continue();
  });
  await page.goto('/uncertainty');
  await expect(page.getByText(/the connection seems slow/).first()).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Interval status: FAILED_CALIBRATION' }),
  ).toBeVisible();
});

test('an unavailable component fails alone and can be retried', async ({ page }) => {
  let fail = true;
  await page.route('**/api/v1/scenarios', async (route) => {
    if (fail)
      await route.fulfill(
        json(503, 'artifact_unavailable', 'the M4 reference run is not available'),
      );
    else await route.continue();
  });
  await open(page, '/', 'Decision-intelligence research on NYC 311 demand', { failures: true });
  await expect(page.getByText(/This component is unavailable/).first()).toBeVisible();
  // The rest of the page still works.
  await expect(page.getByText('Not significant after Holm')).toBeVisible();
  fail = false;
  await page.getByRole('button', { name: 'Retry' }).first().click();
  await expect(page.getByRole('link', { name: 'Reduced budget' })).toBeVisible();
});

test('a network failure is reported, never replaced by numbers', async ({ page }) => {
  await page.route('**/api/v1/forecasts?**', (route) => route.abort('connectionreset'));
  await page.goto('/');
  await expect(page.getByText(/The server could not be reached/).first()).toBeVisible();
  await expect(
    page.getByText(/No numbers are shown in place of the missing ones/).first(),
  ).toBeVisible();
});

test('an answer that breaks the contract is refused', async ({ page }) => {
  await page.route('**/api/v1/models', async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as { integrity: Record<string, unknown> };
    body.integrity.validated_recommendation = true;
    await route.fulfill({ response, json: body });
  });
  await page.goto('/forecasting');
  await expect(page.getByText(/Answer refused/).first()).toBeVisible();
});

for (const [status, code, text] of [
  [504, 'simulation_timeout', /stopped the solve at its deadline/],
  [503, 'simulations_busy', /keeps no queue/],
] as const) {
  test(`a simulation ${code} is explained`, async ({ page }) => {
    await page.route('**/api/v1/simulations', (route) =>
      route.fulfill(json(status, code, 'refused', status === 503 ? { 'Retry-After': '1' } : {})),
    );
    await open(page, '/simulator', 'Hypothetical simulator');
    await page.getByRole('button', { name: 'Run simulation' }).click();
    await expect(page.getByText(text)).toBeVisible();
  });
}

test('a page that fails to load offers a reload', async ({ page }) => {
  await page.route(/\/assets\/Simulator-.*\.js$/, (route) => route.abort());
  await page.goto('/');
  await page
    .getByRole('navigation', { name: 'Dashboard sections' })
    .getByRole('link', { name: /Simulator/ })
    .click();
  await expect(page.getByText('This page could not be displayed.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Reload' })).toBeVisible();
});
