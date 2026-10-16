/**
 * Writes `dist/atlas-dashboard.json`: what exactly a dashboard build is.
 *
 * It lists every built file with its SHA-256 and size, and the client routes (from
 * `src/pages.ts`). The Python side reads it to serve and verify the build:
 *
 * - the API's dashboard mount (`atlas.dashboard`) serves exactly these files, from memory,
 *   after checking every hash;
 * - `atlas-deploy` renders one exact nginx location per file and per route, publishes a
 *   verified copy per release, and records the manifest in `release.json`.
 *
 * The build must be reproducible: CI rebuilds from the committed sources and lock and
 * requires the same bytes (`npm run verify:dist`).
 */
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';
import type { Plugin } from 'vite';

import { PAGE_PATHS } from '../src/pages.ts';

export const MANIFEST_NAME = 'atlas-dashboard.json';
export const MANIFEST_FORMAT = 'atlas-dashboard/1';

export interface DashboardManifest {
  readonly format: typeof MANIFEST_FORMAT;
  readonly version: string;
  readonly entry: 'index.html';
  readonly routes: readonly string[];
  readonly files: Readonly<Record<string, { readonly sha256: string; readonly bytes: number }>>;
}

function walk(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return walk(path);
    if (!entry.isFile()) throw new Error(`${path}: only regular files may be built`);
    return [path];
  });
}

export function buildManifest(outDir: string, version: string): DashboardManifest {
  const files: Record<string, { sha256: string; bytes: number }> = {};
  const paths = walk(outDir)
    .map((path) => relative(outDir, path).split(sep).join('/'))
    .filter((path) => path !== MANIFEST_NAME)
    .sort();
  for (const path of paths) {
    const bytes = readFileSync(join(outDir, path));
    files[path] = { sha256: createHash('sha256').update(bytes).digest('hex'), bytes: bytes.length };
  }
  if (!('index.html' in files)) throw new Error('the build has no index.html');
  return { format: MANIFEST_FORMAT, version, entry: 'index.html', routes: [...PAGE_PATHS], files };
}

export function atlasManifest(version: string): Plugin {
  let outDir = '';
  return {
    name: 'atlas-dashboard-manifest',
    apply: 'build',
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir);
    },
    closeBundle() {
      const manifest = buildManifest(outDir, version);
      writeFileSync(join(outDir, MANIFEST_NAME), JSON.stringify(manifest, null, 2) + '\n');
    },
  };
}
