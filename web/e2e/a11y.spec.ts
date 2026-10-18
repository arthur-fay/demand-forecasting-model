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

test('the current page stays marked in forced colours', async ({ page }) => {
  await page.emulateMedia({ forcedColors: 'active' });
  await open(page, '/forecasting', 'Forecasting');
  // Forced colours remove its wash and its inset bar: a border in the system's highlight
  // colour marks it instead, and it alone.
  const nav = page.getByRole('navigation', { name: 'Dashboard sections' });
  const current = nav.locator('a[aria-current="page"]');
  await expect(current).toHaveText(/Forecasting/);
  await expect(current).toHaveCSS('border-left-style', 'solid');
  await expect(current).toHaveCSS('border-left-width', '3px');
  await expect(nav.getByRole('link', { name: /Overview/ })).toHaveCSS('border-left-style', 'none');
});

test('the error summary of several invalid fields has no WCAG 2.2 AA violation', async ({
  page,
}) => {
  // Checked in the browser before anything is sent: one link per field, stacked, each a
  // target of at least 24 px (2.5.8) at desktop width, where each link is one line.
  await open(page, '/simulator', 'Hypothetical simulator');
  await page.getByRole('textbox', { name: /Planning horizon/ }).fill('abc');
  await page.getByRole('textbox', { name: /Demand multiplier/ }).fill('9');
  await page.getByRole('button', { name: 'Run simulation' }).click();
  const summary = page.getByRole('alert').filter({ hasText: '2 inputs need attention' });
  const links = summary.getByRole('link');
  await expect(links).toHaveCount(2);
  for (const link of await links.all()) {
    const box = await link.boundingBox();
    expect(box?.height, await link.innerText()).toBeGreaterThanOrEqual(24);
  }
  expect(await violations(page)).toEqual([]);
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
