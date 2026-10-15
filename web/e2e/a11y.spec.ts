import { expect, test } from '@playwright/test';

import { open, PAGES, violations } from './helpers';

for (const scheme of ['light', 'dark'] as const) {
  for (const { path, heading } of PAGES) {
    test(`${path} has no WCAG 2.2 AA violation (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await open(page, path, heading);
      expect(await violations(page)).toEqual([]);
      // The same page with every chart shown as its table and every disclosure open.
      for (const toggle of await page.getByRole('radio', { name: 'Table' }).all())
        await toggle.check();
      for (const summary of await page.locator('details > summary').all()) await summary.click();
      expect(await violations(page)).toEqual([]);
    });
  }
}

test('every chart has a table twin', async ({ page }) => {
  await open(page, '/uncertainty', 'Uncertainty and risk');
  const toggles = page.getByRole('radio', { name: 'Table' });
  const count = await toggles.count();
  expect(count).toBeGreaterThan(1);
  for (let i = 0; i < count; i += 1) await toggles.nth(i).check();
  await expect(page.getByRole('table')).toHaveCount(4);
});

test('a simulation result and a failed run have no WCAG 2.2 AA violation', async ({ page }) => {
  await open(page, '/simulator', 'Hypothetical simulator');
  await page.getByRole('button', { name: 'Run simulation' }).click();
  await expect(page.getByRole('heading', { name: /Simulation result/ })).toBeVisible({
    timeout: 30_000,
  });
  for (const summary of await page.locator('details > summary').all()) await summary.click();
  expect(await violations(page)).toEqual([]);
  await page.route('**/api/v1/simulations', (route) =>
    route.fulfill({
      status: 504,
      contentType: 'application/json',
      body: JSON.stringify({
        error: { code: 'simulation_timeout', message: 'stopped', request_id: 'r', details: [] },
      }),
    }),
  );
  await page.getByRole('button', { name: 'Run simulation' }).click();
  await expect(page.getByText(/Simulation not run/)).toBeVisible();
  expect(await violations(page)).toEqual([]);
});
