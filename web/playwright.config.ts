/**
 * End-to-end tests in a real browser against real servers:
 *
 * - `api`: the API serves the committed build (`uv run atlas api serve`), as locally and
 *   through an SSH tunnel; every page, the simulator against the real M4 engine, failures
 *   injected at the network layer, keyboard use, accessibility (axe), the CSP.
 * - `mobile`: the same server on a phone-sized viewport.
 * - `nginx-private`, `nginx-public`: the production nginx site rendered for a release with
 *   the dashboard published, TLS, the real API behind its socket (`tests/m7_stack.py`).
 *
 * The browser maps atlas.example.org to 127.0.0.1 (the site answers its own name only)
 * and accepts the throw-away test CA.
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
  testMatch: /(dashboard|failures)\.spec\.ts/,
  use: { ...devices[device], launchOptions: {}, baseURL: `http://127.0.0.1:${API_PORT}` },
});

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
  projects: [
    {
      name: 'api',
      testMatch: /(dashboard|failures|a11y|keyboard|reflow)\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], launchOptions, baseURL: `http://127.0.0.1:${API_PORT}` },
    },
    {
      name: 'mobile',
      testMatch: /mobile\.spec\.ts/,
      use: { ...devices['Pixel 7'], launchOptions, baseURL: `http://127.0.0.1:${API_PORT}` },
    },
    {
      name: 'nginx-private',
      testMatch: /nginx-private\.spec\.ts/,
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
      testMatch: /nginx-public\.spec\.ts/,
      use: {
        ...devices['Desktop Chrome'],
        launchOptions,
        baseURL: `https://atlas.example.org:${PUBLIC_PORT}`,
        ignoreHTTPSErrors: true,
      },
    },
    ...(otherEngines
      ? [engine('firefox', 'Desktop Firefox'), engine('webkit', 'Desktop Safari')]
      : []),
  ],
  webServer: [
    {
      command: `uv run atlas api serve --port ${API_PORT}`,
      cwd: '..',
      url: `http://127.0.0.1:${API_PORT}/health/ready`,
      timeout: 120_000,
      reuseExistingServer: false,
      env: { ATLAS_API_SIMULATION_TIMEOUT_SECONDS: '30' },
    },
    stack('private', PRIVATE_PORT, 8081),
    stack('public-demo', PUBLIC_PORT, 8082),
  ],
});
