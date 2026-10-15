/**
 * Reflow (WCAG 1.4.10): at 320 CSS pixels wide (a desktop browser at 400 % zoom) no page
 * scrolls sideways, with the widest content each page can show: every disclosure open and
 * a simulation result on screen. Wide tables scroll inside their own region instead.
 */
import { expect, test } from '@playwright/test';

import { horizontalOverflow, open, PAGES } from './helpers';

test.use({ viewport: { width: 320, height: 640 } });

for (const { path, heading } of PAGES) {
  test(`${path} reflows at 320 CSS pixels`, async ({ page }) => {
    await open(page, path, heading);
    for (const summary of await page.locator('details > summary').all()) await summary.click();
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
  });
}

test('a simulation result reflows at 320 CSS pixels', async ({ page }) => {
  await open(page, '/simulator', 'Hypothetical simulator');
  await page.getByRole('button', { name: 'Run simulation' }).click();
  await expect(page.getByRole('heading', { name: /Simulation result/ })).toBeVisible({
    timeout: 30_000,
  });
  for (const summary of await page.locator('details > summary').all()) await summary.click();
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
});
