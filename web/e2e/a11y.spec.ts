import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { open, PAGES } from './helpers';

for (const scheme of ['light', 'dark'] as const) {
  for (const { path, heading } of PAGES) {
    test(`${path} has no WCAG 2.1 AA violation (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await open(page, path, heading);
      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze();
      expect(
        results.violations.map(
          (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
        ),
      ).toEqual([]);
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
