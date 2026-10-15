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
