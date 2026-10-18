/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';
import { defineConfig } from 'vite';

import { atlasManifest } from './build/atlas-manifest.ts';
import {
  EXPORT_DIRECTORY,
  pagesBase,
  readExport,
  SITE_MANIFEST,
  staticSite,
} from './build/static-site.ts';
import { THIRD_PARTY_NOTICES } from './src/notices.ts';

const { version } = JSON.parse(
  readFileSync(new URL('./package.json', import.meta.url), 'utf-8'),
) as {
  version: string;
};

/** Where `npm run dev` and `npm run preview` find the API (`uv run atlas api serve`). */
const API = process.env.ATLAS_DEV_API ?? 'http://127.0.0.1:8000';
const proxy = {
  '/api/': API,
  '/health/': API,
  '/atlas-deployment.json': API,
};

/**
 * Two build profiles (src/profile.ts), chosen explicitly by the mode:
 *
 * - the default (`npm run build`): the api profile, into the committed `dist/`, at `/`;
 * - `--mode pages` (`npm run build:pages`, M9): the static profile for a static host, into
 *   `dist-pages/` under `ATLAS_PAGES_BASE` (default `/demand-forecasting-model/`), with the
 *   static export compiled in (build/static-site.ts).
 */
export default defineConfig(({ mode }) => {
  const pages = mode === 'pages';
  const root = new URL('.', import.meta.url).pathname;
  const exported = pages ? readExport(`${root}${EXPORT_DIRECTORY}`) : null;
  const base = pages ? pagesBase(process.env) : '/';
  return {
    plugins: [
      react(),
      exported === null
        ? atlasManifest(version)
        : staticSite({ version, base, exported, record: `${root}${SITE_MANIFEST}` }),
    ],
    define: {
      __ATLAS_DASHBOARD_VERSION__: JSON.stringify(version),
      __ATLAS_STATIC__: JSON.stringify(pages),
      __ATLAS_STATIC_INDEX__: JSON.stringify(exported?.index ?? null),
    },
    base,
    build: {
      target: 'es2022',
      outDir: pages ? 'dist-pages' : 'dist',
      emptyOutDir: true,
      sourcemap: false,
      // Never inline assets as data: URIs: the Content-Security-Policy allows 'self' only.
      assetsInlineLimit: 0,
      // Modern browsers support modulepreload natively; the polyfill would add code.
      modulePreload: { polyfill: false },
      reportCompressedSize: true,
      chunkSizeWarningLimit: 200,
      // The copyright and licence text of every package bundled into the code, which their
      // MIT and ISC licences require with every copy (src/notices.ts; the font's is in public/).
      license: { fileName: THIRD_PARTY_NOTICES },
    },
    server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
    preview: { host: '127.0.0.1', port: 4173, strictPort: true, proxy },
    test: {
      environment: 'jsdom',
      include: ['src/**/*.test.{ts,tsx}', 'build/**/*.test.ts'],
      setupFiles: ['src/test/setup.ts'],
      restoreMocks: true,
      // A whole page rendered from fixtures takes 3-4 s with coverage on a busy machine: room
      // for a loaded CI runner, and for the 5 s waits of the tests (src/test/setup.ts) to mean
      // something.
      testTimeout: 20_000,
      // West of UTC, so a calendar date formatted in local time shows the day before and
      // fails a test (CI runs in UTC, where it would not). src/lib/format.test.ts also loads
      // the date module east of UTC, where a date parsed in local time shows the day before.
      env: { TZ: 'America/Los_Angeles' },
      coverage: {
        provider: 'v8',
        include: ['src/**/*.{ts,tsx}'],
        exclude: [
          'src/**/*.test.{ts,tsx}',
          'src/test/**',
          'src/api/openapi.gen.ts',
          'src/main.tsx',
        ],
        thresholds: { lines: 85, functions: 85, branches: 75, statements: 85 },
      },
    },
  };
});
