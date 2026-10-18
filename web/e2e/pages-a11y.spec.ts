/**
 * (M9) Accessibility and keyboard use of the static build (desktop Chromium), served as
 * GitHub Pages serves it: the same WCAG 2.2 A/AA rules as the API-backed pages, on every
 * page in both themes with every table and disclosure open, and the static simulator's
 * recorded scenarios by keyboard alone.
 */
import { expect, test } from '@playwright/test';

import { horizontalOverflow, open, PAGES, violations } from './helpers';

const at = (path: string) => path.slice(1);

for (const scheme of ['light', 'dark'] as const) {
  for (const { path, heading } of PAGES) {
    test(`${path} has no WCAG 2.2 AA violation in the static build (${scheme})`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await open(page, at(path), heading);
      expect(await violations(page)).toEqual([]);
      for (const toggle of await page.getByRole('radio', { name: 'Table' }).all())
        await toggle.check();
      for (const summary of await page.locator('details > summary').all()) await summary.click();
      expect(await violations(page)).toEqual([]);
    });
  }
}

test('the skip link, the navigation and the recorded scenarios work from the keyboard', async ({
  page,
}) => {
  await open(page, '', PAGES[0].heading);
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/#main$/);

  const link = page
    .getByRole('navigation', { name: 'Dashboard sections' })
    .getByRole('link', { name: /Simulator/ });
  await link.focus();
  await page.keyboard.press('Enter');
  await expect(
    page.getByRole('heading', { level: 1, name: 'Hypothetical simulator' }),
  ).toBeVisible();
  await expect(page.getByRole('main')).toBeFocused();
  await expect(page).toHaveURL(/\/demand-forecasting-model\/simulator$/);

  // The scenario list is a native select: the arrow keys choose, and the result follows.
  const select = page.getByLabel('Reference scenario', { exact: true });
  await select.focus();
  const outline = await select.evaluate((element) => getComputedStyle(element).outlineStyle);
  expect(outline).not.toBe('none');
  await expect(select).toHaveValue('normal');
  await page.keyboard.press('ArrowDown');
  await expect(select).toHaveValue('increased_demand');
  await expect(
    page.getByRole('heading', { level: 3, name: /Recorded result: Increased demand/ }),
  ).toBeVisible();
  expect(await violations(page)).toEqual([]);
});

test('the static pages reflow at 320 CSS pixels', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  for (const { path, heading } of PAGES) {
    await open(page, at(path), heading);
    for (const summary of await page.locator('details > summary').all()) await summary.click();
    expect(await horizontalOverflow(page), path).toBeLessThanOrEqual(0);
  }
});
