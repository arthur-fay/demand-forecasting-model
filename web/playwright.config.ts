/**
 * End-to-end tests in a real browser against real servers:
 *
 * - `api`: the API serves the committed build (`uv run atlas api serve`), as locally and
 *   through an SSH tunnel; every page, the simulator against the real M4 engine, failures
 *   injected at the network layer, keyboard use, accessibility (axe), the CSP.
 * - `mobile`: the same server on a phone-sized viewport.
 * - `nginx-private`, `nginx-public`: the production nginx site rendered for a release with
 *   the dashboard published, TLS, the real API behind its socket (`tests/m7_stack.py`).
 * - `firefox`, `webkit`: the API-backed pages and failure states in the other two engines,
 *   only when ATLAS_E2E_ALL_BROWSERS=1 (below).
 *
 * By default every Chromium project runs and all three servers start. Playwright starts
 * every server whatever `--project` filter is given, so ATLAS_E2E_API_ONLY=1 starts the API
 * server alone and defines only the projects that use it (`api`, `mobile`, and `firefox`
 * and `webkit` when enabled), not the nginx ones. CI's Firefox and WebKit step sets both.
 *
 * The browser maps atlas.example.org to 127.0.0.1 (the site answers its own name only)
 * and accepts the throw-away test CA.
 *
 * (M9) ATLAS_E2E_PAGES=1 (`npm run test:e2e:pages`) runs the static build instead: the
 * built `dist-pages/` (`npm run build:pages` first) served by a local stand-in for GitHub
 * Pages (scripts/pages-server.mjs) under the repository's path, with no API and no Python.
 * Its projects are `pages` (desktop Chromium), `pages-mobile` (a phone) and, with
 * ATLAS_E2E_ALL_BROWSERS=1, `pages-firefox` and `pages-webkit`; only that server starts.
 */
import { defineConfig, devices } from '@playwright/test';

/** A local Chromium (set in the development container); CI uses Playwright's own. */
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
const launchOptions = {
  ...(executablePath ? { executablePath } : {}),
  args: [
    '--host-resolver-rules=MAP atlas.example.org 127.0.0.1',
    // Straight to the local servers, whatever proxy the environment configures.
    '--no-proxy-server',
    '--disable-background-networking',
  ],
};
export const API_PORT = 8766;
export const PAGES_PORT = 4180;
export const PAGES_BASE = '/demand-forecasting-model/';
/** The site's address: page paths in the pages specs are relative to it (no leading slash). */
export const PAGES_URL = `http://127.0.0.1:${PAGES_PORT}${PAGES_BASE}`;
export const PRIVATE_PORT = 8443;
export const PUBLIC_PORT = 8444;
export const ACCOUNT = { username: 'alice', password: 'correct-horse-battery-staple-1234' };

/**
 * M8: the API-backed pages and failure states in Firefox (Gecko) and WebKit (Safari's
 * engine, not Safari itself), when ATLAS_E2E_ALL_BROWSERS=1. CI installs those browsers;
 * the development container has Chromium only, so they are off by default. They take no
 * Chromium launch flags: the API answers on 127.0.0.1, so no host mapping is needed.
 */
const otherEngines = process.env.ATLAS_E2E_ALL_BROWSERS === '1';
const engine = (name: string, device: string) => ({
  name,
  testMatch: /\/(dashboard|failures)\.spec\.ts$/,
  use: { ...devices[device], launchOptions: {}, baseURL: `http://127.0.0.1:${API_PORT}` },
});

/** The API server alone: no nginx stack, and none of the projects that need one. */
const apiOnly = process.env.ATLAS_E2E_API_ONLY === '1';

/** (M9) The static build alone, as GitHub Pages serves it. */
const pages = process.env.ATLAS_E2E_PAGES === '1';
const pagesProject = (name: string, device: string, testMatch: RegExp, options = {}) => ({
  name,
  testMatch,
  use: { ...devices[device], baseURL: PAGES_URL, ...options },
});
const pagesProjects = [
  pagesProject('pages', 'Desktop Chrome', /\/pages(-a11y)?\.spec\.ts$/, { launchOptions }),
  pagesProject('pages-mobile', 'Pixel 7', /\/pages(-mobile)?\.spec\.ts$/, { launchOptions }),
  ...(otherEngines
    ? [
        pagesProject('pages-firefox', 'Desktop Firefox', /\/pages\.spec\.ts$/, {
          launchOptions: {},
        }),
        pagesProject('pages-webkit', 'Desktop Safari', /\/pages\.spec\.ts$/, { launchOptions: {} }),
      ]
    : []),
];
const pagesServer = {
  command: `node scripts/pages-server.mjs --port ${PAGES_PORT} --base ${PAGES_BASE}`,
  url: PAGES_URL,
  timeout: 30_000,
  reuseExistingServer: false,
};

const stack = (profile: string, https: number, http: number) => ({
  command: `uv run python -m tests.m7_stack --profile ${profile} --https-port ${https} --http-port ${http}`,
  cwd: '..',
  port: https,
  timeout: 180_000,
  reuseExistingServer: false,
  stdout: 'ignore' as const,
  // SIGTERM, so the stack stops its nginx (a daemon, outside the process group) itself.
  gracefulShutdown: { signal: 'SIGTERM' as const, timeout: 20_000 },
});

export default defineConfig({
  testDir: 'e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { trace: 'retain-on-failure', launchOptions },
  projects: pages
    ? pagesProjects
    : [
        {
          name: 'api',
          testMatch: /\/(dashboard|failures|a11y|keyboard|reflow)\.spec\.ts$/,
          use: {
            ...devices['Desktop Chrome'],
            launchOptions,
            baseURL: `http://127.0.0.1:${API_PORT}`,
          },
        },
        {
          name: 'mobile',
          testMatch: /\/mobile\.spec\.ts$/,
          use: { ...devices['Pixel 7'], launchOptions, baseURL: `http://127.0.0.1:${API_PORT}` },
        },
        ...(apiOnly
          ? []
          : [
              {
                name: 'nginx-private',
                testMatch: /\/nginx-private\.spec\.ts$/,
                use: {
                  ...devices['Desktop Chrome'],
                  launchOptions,
                  baseURL: `https://atlas.example.org:${PRIVATE_PORT}`,
                  ignoreHTTPSErrors: true,
                  httpCredentials: ACCOUNT,
                },
              },
              {
                name: 'nginx-public',
                testMatch: /\/nginx-public\.spec\.ts$/,
                use: {
                  ...devices['Desktop Chrome'],
                  launchOptions,
                  baseURL: `https://atlas.example.org:${PUBLIC_PORT}`,
                  ignoreHTTPSErrors: true,
                },
              },
            ]),
        ...(otherEngines
          ? [engine('firefox', 'Desktop Firefox'), engine('webkit', 'Desktop Safari')]
          : []),
      ],
  webServer: pages
    ? [pagesServer]
    : [
        {
          command: `uv run atlas api serve --port ${API_PORT}`,
          cwd: '..',
          url: `http://127.0.0.1:${API_PORT}/health/ready`,
          timeout: 120_000,
          reuseExistingServer: false,
          env: { ATLAS_API_SIMULATION_TIMEOUT_SECONDS: '30' },
        },
        ...(apiOnly
          ? []
          : [stack('private', PRIVATE_PORT, 8081), stack('public-demo', PUBLIC_PORT, 8082)]),
      ],
});
