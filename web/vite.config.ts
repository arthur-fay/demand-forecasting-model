/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';
import { defineConfig } from 'vite';

import { atlasManifest } from './build/atlas-manifest.ts';

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

export default defineConfig({
  plugins: [react(), atlasManifest(version)],
  define: { __ATLAS_DASHBOARD_VERSION__: JSON.stringify(version) },
  base: '/',
  build: {
    target: 'es2022',
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    // Never inline assets as data: URIs: the Content-Security-Policy allows 'self' only.
    assetsInlineLimit: 0,
    // Modern browsers support modulepreload natively; the polyfill would add code.
    modulePreload: { polyfill: false },
    reportCompressedSize: true,
    chunkSizeWarningLimit: 200,
  },
  server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true, proxy },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}', 'build/**/*.test.ts'],
    setupFiles: ['src/test/setup.ts'],
    restoreMocks: true,
    // A whole page rendered from fixtures takes 3-4 s with coverage on a busy machine: room
    // for a loaded CI runner, and for the 5 s waits inside the tests to mean something.
    testTimeout: 20_000,
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.test.{ts,tsx}', 'src/test/**', 'src/api/openapi.gen.ts', 'src/main.tsx'],
      thresholds: { lines: 85, functions: 85, branches: 75, statements: 85 },
    },
  },
});
