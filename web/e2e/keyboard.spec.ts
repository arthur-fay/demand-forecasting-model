import { expect, test } from '@playwright/test';

import { open } from './helpers';

test('the skip link, the navigation and the charts work from the keyboard', async ({ page }) => {
  await open(page, '/', 'Decision-intelligence research on NYC 311 demand');
  await page.keyboard.press('Tab');
  const skip = page.getByRole('link', { name: 'Skip to content' });
  await expect(skip).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/#main$/);

  // Reach a navigation link by keyboard and follow it.
  const link = page
    .getByRole('navigation', { name: 'Dashboard sections' })
    .getByRole('link', { name: /Forecasting/ });
  await link.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 1, name: 'Forecasting' })).toBeVisible();
  await expect(page.getByRole('main')).toBeFocused();

  const chart = page.getByRole('slider', { name: /Observed history/ });
  await chart.focus();
  await page.keyboard.press('End');
  await expect(chart).toHaveAttribute('aria-valuetext', /Archived projection/);
  await expect(page.locator('.tooltip')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.tooltip')).toBeHidden();
});

test('focus is always visible', async ({ page }) => {
  await open(page, '/decisions', 'Decision optimisation');
  const select = page.getByLabel('Scenario', { exact: true });
  await select.focus();
  const outline = await select.evaluate((element) => getComputedStyle(element).outlineStyle);
  expect(outline).not.toBe('none');
});

test('reduced motion turns the page transition off', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await open(page, '/', 'Decision-intelligence research on NYC 311 demand');
  const duration = await page
    .locator('.page-enter')
    .evaluate((element) => getComputedStyle(element).animationDuration);
  expect(parseFloat(duration)).toBeLessThan(0.001);
});

test('a field reached by keyboard is never hidden under a sticky bar', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await open(page, '/simulator', 'Hypothetical simulator');
  const ids = await page
    .locator('form input[type="text"]')
    // Enabled ones: a limit left at "No limit" disables its field, which Tab then skips.
    .evaluateAll((inputs) =>
      inputs.filter((input) => !(input as HTMLInputElement).disabled).map((input) => input.id),
    );
  expect(ids.length).toBeGreaterThan(10);
  // Tab through the form until every text field has had the focus: each must then be the
  // element at its own centre (not the top bar or the action bar drawn over it). WCAG 2.4.11.
  // Both ways: Tab scrolls each field up from under the action bar, Shift+Tab down from
  // under the top bar.
  const visit = async (key: 'Tab' | 'Shift+Tab') => {
    const visited = new Set<string>();
    for (let step = 0; step < 200 && visited.size < ids.length; step++) {
      const focused = await page.evaluate(() => {
        const element = document.activeElement;
        if (!(element instanceof HTMLInputElement) || element.type !== 'text') return null;
        const box = element.getBoundingClientRect();
        const top = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
        return { id: element.id, covered: top !== element };
      });
      if (focused !== null) {
        expect(focused.covered, `${focused.id} is covered when focused (${key})`).toBe(false);
        visited.add(focused.id);
      }
      await page.keyboard.press(key);
    }
    expect([...visited].sort(), `every field reached by ${key}`).toEqual([...ids].sort());
  };
  await page.getByRole('textbox', { name: /Planning horizon/ }).focus();
  await visit('Tab');
  await page.getByRole('button', { name: 'Run simulation' }).focus();
  await visit('Shift+Tab');
});

test('a failed simulation is reported where the reader is', async ({ page }) => {
  await page.route('**/api/v1/simulations', (route) =>
    route.fulfill({
      status: 503,
      contentType: 'application/json',
      headers: { 'Retry-After': '1' },
      body: JSON.stringify({
        error: {
          code: 'simulations_busy',
          message: 'Every worker is busy.',
          request_id: 'r1',
          details: [],
        },
      }),
    }),
  );
  await open(page, '/simulator', 'Hypothetical simulator');
  await page.getByRole('button', { name: 'Run simulation' }).click();
  const alert = page.getByText(/Simulation not run/);
  await expect(alert).toBeInViewport();
});
