import { expect, test } from '@playwright/test';

import { open, PAGES } from './helpers';

for (const { path, heading } of PAGES) {
  test(`${path} fits a phone screen`, async ({ page }) => {
    await open(page, path, heading);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
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
  await page.touchscreen.tap(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await expect(page.locator('.tooltip')).toBeVisible();
});
