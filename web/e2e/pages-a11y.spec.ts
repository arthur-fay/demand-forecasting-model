/**
 * (M9) Accessibility of the static build (desktop Chromium), served as GitHub Pages serves
 * it: the same WCAG 2.2 A/AA rules as the API-backed pages, on every page in both themes
 * with every table and disclosure open, and reflow at 320 CSS pixels. Keyboard use is in
 * pages-keyboard.spec.ts (every engine).
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

test('the static pages reflow at 320 CSS pixels', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  for (const { path, heading } of PAGES) {
    await open(page, at(path), heading);
    for (const summary of await page.locator('details > summary').all()) await summary.click();
    expect(await horizontalOverflow(page), path).toBeLessThanOrEqual(0);
  }
});
