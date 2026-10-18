/**
 * (M9) Keyboard use of the static build, in every engine (Chromium, Firefox, WebKit),
 * served as GitHub Pages serves it: the skip link, the navigation and the static
 * simulator's recorded scenarios by keyboard alone. Axe runs in Chromium only
 * (pages-a11y.spec.ts).
 */
import { expect, test, type Page } from '@playwright/test';

import { open, PAGES, violations } from './helpers';

/**
 * Focus the first link with the key the engine moves to links with: Tab, or in WebKit,
 * where Tab skips links unless the user asks otherwise (Safari's own setting), Option+Tab.
 */
async function tabToFirstLink(page: Page, browserName: string) {
  const skip = page.getByRole('link', { name: 'Skip to content' });
  await page.keyboard.press('Tab');
  if (browserName === 'webkit' && !(await skip.evaluate((e) => e === document.activeElement))) {
    await page.locator('body').evaluate((body) => {
      (document.activeElement as HTMLElement | null)?.blur();
      body.focus();
    });
    await page.keyboard.press('Alt+Tab');
  }
  await expect(skip).toBeFocused();
}

test('the skip link, the navigation and the recorded scenarios work from the keyboard', async ({
  page,
  browserName,
}) => {
  await open(page, '', PAGES[0].heading);
  await tabToFirstLink(page, browserName);
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
  if (browserName === 'chromium') expect(await violations(page)).toEqual([]);
});
