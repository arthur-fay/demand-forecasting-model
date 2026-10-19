/**
 * (M9) Keyboard use of the static build, in every engine (Chromium, Firefox, WebKit),
 * served as GitHub Pages serves it: from the start of the page, the skip link, the
 * navigation and the static simulator's scenario chooser are each reached with the tab key
 * and used with Enter or the arrow keys. Axe runs in Chromium only (pages-a11y.spec.ts).
 */
import { expect, test, type Locator, type Page } from '@playwright/test';

import { DEFAULT_PAGES_BASE as BASE } from '../build/static-site';
import { open, PAGES, violations } from './helpers';

/**
 * The key that moves to the next link: Tab, or Option+Tab in WebKit on macOS, where Tab
 * skips links unless the user changes Safari's setting. Playwright's WebKit on Linux, which
 * CI runs, moves to links with Tab.
 */
const tabKey = (browserName: string) =>
  browserName === 'webkit' && process.platform === 'darwin' ? 'Alt+Tab' : 'Tab';

/** Press the tab key until `target` has the focus (at most `most` times). */
async function tabTo(page: Page, key: string, target: Locator, most = 60) {
  for (let presses = 0; presses < most; presses += 1) {
    if (await target.evaluate((element) => element === document.activeElement)) return;
    await page.keyboard.press(key);
  }
  await expect(target).toBeFocused();
}

test('the skip link, the navigation and the recorded scenarios work from the keyboard', async ({
  page,
  browserName,
}) => {
  const key = tabKey(browserName);
  await open(page, '', PAGES[0].heading);
  // The first stop is the skip link, and it moves to the content.
  await page.keyboard.press(key);
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/#main$/);

  // From the start of the page, the tab key reaches the navigation's Simulator link.
  await open(page, '', PAGES[0].heading);
  const link = page
    .getByRole('navigation', { name: 'Dashboard sections' })
    .getByRole('link', { name: /Simulator/ });
  await tabTo(page, key, link);
  await page.keyboard.press('Enter');
  await expect(
    page.getByRole('heading', { level: 1, name: 'Hypothetical simulator' }),
  ).toBeVisible();
  await expect(page.getByRole('main')).toBeFocused();
  await expect(page).toHaveURL(new RegExp(`${BASE}simulator$`));

  // The scenario list is a native select, reached from the page's content: the arrow keys
  // choose, and the result follows.
  const select = page.getByLabel('Reference scenario', { exact: true });
  await tabTo(page, key, select);
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
