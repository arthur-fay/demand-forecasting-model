import AxeBuilder from '@axe-core/playwright';
import { expect, type Page, type Request } from '@playwright/test';

export const PAGES = [
  { path: '/', heading: 'Decision-intelligence research on NYC 311 demand' },
  { path: '/forecasting', heading: 'Forecasting' },
  { path: '/uncertainty', heading: 'Uncertainty and risk' },
  { path: '/decisions', heading: 'Decision optimisation' },
  { path: '/simulator', heading: 'Hypothetical simulator' },
  { path: '/provenance', heading: 'Methods and provenance' },
] as const;

export interface Watch {
  readonly errors: string[];
  readonly violations: string[];
  readonly requests: Request[];
}

/** Record console errors, CSP (and Trusted Types) violations and every request. */
export async function watch(page: Page): Promise<Watch> {
  const recorded: Watch = { errors: [], violations: [], requests: [] };
  await page.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', (event) => {
      console.error(`CSP violation: ${event.violatedDirective} ${event.blockedURI}`);
    });
  });
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const text = message.text();
    if (text.startsWith('CSP violation')) recorded.violations.push(text);
    // A failed request is reported by the tests that cause it on purpose.
    else if (!text.startsWith('Failed to load resource')) recorded.errors.push(text);
  });
  page.on('pageerror', (error) => recorded.errors.push(error.message));
  page.on('request', (request) => recorded.requests.push(request));
  return recorded;
}

export async function clean(recorded: Watch): Promise<void> {
  expect(recorded.violations, 'no CSP or Trusted Types violation').toEqual([]);
  expect(recorded.errors, 'no console error').toEqual([]);
  for (const request of recorded.requests) {
    const headers = await request.allHeaders();
    expect(headers['x-api-key'], `${request.url()} carries no API key`).toBeUndefined();
  }
}

/**
 * Open a page and wait until it has settled. Unless a test injects a failure on purpose,
 * no panel may show an error state: a failed request (a wrong query, a 429, an answer the
 * schemas refuse) is never only a console message.
 */
export async function open(
  page: Page,
  path: string,
  heading: string,
  { failures = false }: { failures?: boolean } = {},
): Promise<void> {
  await page.goto(path);
  await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
  await page.waitForLoadState('networkidle');
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
  if (!failures) await expect(page.locator('[data-kind="error"]')).toHaveCount(0);
}

/** How far the page is wider than its layout viewport, in CSS pixels (0: no sideways scroll). */
export async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

/** axe-core's WCAG 2.0, 2.1 and 2.2 A/AA rules on the page as it is now, one line each. */
export async function violations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze();
  return results.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  );
}
