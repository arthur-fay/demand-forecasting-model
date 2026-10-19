/** (M9) The static build on a phone (Pixel 7), served as GitHub Pages serves it. */
import { expect, test } from '@playwright/test';

import { DEFAULT_PAGES_BASE as BASE } from '../build/static-site';
import { horizontalOverflow, open, PAGES, violations } from './helpers';

const at = (path: string) => path.slice(1);

for (const { path, heading } of PAGES) {
  test(`${path} of the static build fits a phone screen`, async ({ page }) => {
    await open(page, at(path), heading);
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
    expect(await violations(page)).toEqual([]);
  });
}

test('the navigation opens from the menu and moves between static pages', async ({ page }) => {
  await open(page, '', PAGES[0].heading);
  const nav = page.getByRole('navigation', { name: 'Dashboard sections' });
  await expect(nav).toBeHidden();
  for (const { label, heading, path } of [
    { label: /Simulator/, heading: 'Hypothetical simulator', path: 'simulator' },
    { label: /Forecasting/, heading: 'Forecasting', path: 'forecasting' },
  ]) {
    await page.getByRole('button', { name: 'Open navigation' }).click();
    await expect(nav).toBeVisible();
    await nav.getByRole('link', { name: label }).click();
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
    await expect(nav).toBeHidden();
    await expect(page).toHaveURL(new RegExp(`${BASE}${path}$`));
  }
  await page.goBack();
  await expect(
    page.getByRole('heading', { level: 1, name: 'Hypothetical simulator' }),
  ).toBeVisible();
});

test('a recorded scenario can be chosen and read on a phone', async ({ page }) => {
  await open(page, 'simulator', 'Hypothetical simulator');
  await page.getByLabel('Reference scenario', { exact: true }).selectOption('combined_stress');
  await expect(
    page.getByRole('heading', { level: 3, name: /Recorded result: Combined stress/ }),
  ).toBeVisible();
  await expect(page.locator('[data-kind="error"]')).toHaveCount(0);
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
});
