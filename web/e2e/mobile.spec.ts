import { expect, test } from '@playwright/test';

import { horizontalOverflow, open, PAGES } from './helpers';

for (const { path, heading } of PAGES) {
  test(`${path} fits a phone screen`, async ({ page }) => {
    await open(page, path, heading);
    // clientWidth, not innerWidth: a phone browser zooms out to fit wide content, which
    // makes innerWidth grow with the overflow it should reveal.
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
  });
}

test('the navigation opens from the menu on a phone', async ({ page }) => {
  await open(page, '/', PAGES[0].heading);
  const nav = page.getByRole('navigation', { name: 'Dashboard sections' });
  await expect(nav).toBeHidden();
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await expect(nav).toBeVisible();
  await nav.getByRole('link', { name: /Decision optimisation/ }).click();
  await expect(
    page.getByRole('heading', { level: 1, name: 'Decision optimisation' }),
  ).toBeVisible();
  await expect(nav).toBeHidden();
});

test('a chart reads by touch', async ({ page }) => {
  await open(page, '/', PAGES[0].heading);
  const chart = page.locator('.chart-svg').first();
  await chart.scrollIntoViewIfNeeded();
  const box = (await chart.boundingBox())!;
  for (const at of [0.05, 0.35, 0.5, 0.95]) {
    await page.touchscreen.tap(box.x + box.width * at, box.y + box.height * 0.5);
    const tooltip = page.locator('.tooltip');
    await expect(tooltip).toBeVisible();
    // The reading stays inside the chart, whatever the point tapped.
    const tip = (await tooltip.boundingBox())!;
    expect(tip.x).toBeGreaterThanOrEqual(box.x - 1);
    expect(tip.x + tip.width).toBeLessThanOrEqual(box.x + box.width + 1);
  }
});
