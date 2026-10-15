import { expect, test } from '@playwright/test';

import { clean, open, PAGES, watch } from './helpers';

test('the public demo is read-only and says so', async ({ page }) => {
  const recorded = await watch(page);
  await open(page, '/simulator', 'Hypothetical simulator');
  await expect(page.getByText(/This is the public read-only demonstration/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run simulation' })).toBeDisabled();
  await expect(page.getByText('Public demo · read-only', { exact: true })).toBeVisible();
  await open(page, '/provenance', 'Methods and provenance');
  await expect(
    page.getByText('Operational detail is not exposed in this deployment'),
  ).toBeVisible();
  for (const { path, heading } of PAGES) await open(page, path, heading);
  const asked = recorded.requests.map((r) => new URL(r.url()).pathname);
  expect(asked).not.toContain('/api/v1/system');
  expect(recorded.requests.filter((r) => r.method() === 'POST')).toEqual([]);
  await clean(recorded);
});
