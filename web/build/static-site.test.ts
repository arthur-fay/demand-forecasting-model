/** (M9) The static build's plugin: the export it accepts, its base path, its manifest. */
import { createHash } from 'node:crypto';
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import {
  buildSiteManifest,
  DEFAULT_PAGES_BASE,
  EXPORT_MANIFEST,
  pageFile,
  pagesBase,
  readExport,
  STATIC_CSP,
  staticSite,
} from './static-site';

const EXPORT = join(import.meta.dirname, '..', 'static-export');
const scratch: string[] = [];

function copy(): string {
  const directory = mkdtempSync(join(tmpdir(), 'atlas-export-'));
  scratch.push(directory);
  cpSync(EXPORT, directory, { recursive: true });
  return directory;
}

afterEach(() => {
  for (const directory of scratch.splice(0)) rmSync(directory, { recursive: true, force: true });
});

describe('the static export the build accepts', () => {
  it('reads the committed export, every file checked, indexed by request', () => {
    const exported = readExport(EXPORT);
    expect(exported.files.size).toBe(24);
    expect(exported.index['/api/v1/models']).toMatchObject({ file: 'models.json' });
    expect(exported.index['/api/v1/forecasts/history?start=2023-01-01']).toMatchObject({
      file: 'history-all.json',
    });
    const manifest = readFileSync(join(EXPORT, EXPORT_MANIFEST));
    expect(exported.manifest.equals(manifest)).toBe(true);
  });

  it('refuses an altered, an unlisted or a missing file', () => {
    const altered = copy();
    writeFileSync(join(altered, 'models.json'), '{}');
    expect(() => readExport(altered)).toThrow(/models.json differs/);
    const unlisted = copy();
    writeFileSync(join(unlisted, 'extra.json'), '{}');
    expect(() => readExport(unlisted)).toThrow(/does not list: extra.json/);
    const missing = copy();
    rmSync(join(missing, 'scenarios.json'));
    expect(() => readExport(missing)).toThrow(/scenarios.json/);
  });

  it('refuses a manifest of another format or an unsafe entry', () => {
    const other = copy();
    const manifest = JSON.parse(readFileSync(join(other, EXPORT_MANIFEST), 'utf-8')) as {
      format: string;
      files: Record<string, { request: string }>;
    };
    writeFileSync(join(other, EXPORT_MANIFEST), JSON.stringify({ ...manifest, format: 'x/1' }));
    expect(() => readExport(other)).toThrow(/not an atlas-static-export\/1 manifest/);
    const unsafe = copy();
    manifest.files['models.json']!.request = 'https://example.org/api/v1/models';
    writeFileSync(join(unsafe, EXPORT_MANIFEST), JSON.stringify(manifest));
    expect(() => readExport(unsafe)).toThrow(/invalid export entry models.json/);
  });

  it('refuses an export of another ATLAS version than the dashboard', () => {
    const exported = readExport(EXPORT);
    expect(() =>
      staticSite({ version: '9.9.9', base: DEFAULT_PAGES_BASE, exported, record: '/dev/null' }),
    ).toThrow(/export again/);
  });
});

describe('the base path', () => {
  it('defaults to the repository path on GitHub Pages and accepts a checked override', () => {
    expect(pagesBase({})).toBe('/demand-forecasting-model/');
    expect(pagesBase({ ATLAS_PAGES_BASE: '/' })).toBe('/');
    expect(pagesBase({ ATLAS_PAGES_BASE: '/a/b/' })).toBe('/a/b/');
    for (const bad of ['demand', '/demand', '/../', '//x/', '/a b/', 'https://x/'])
      expect(() => pagesBase({ ATLAS_PAGES_BASE: bad }), bad).toThrow(/ATLAS_PAGES_BASE/);
  });
});

describe('the site', () => {
  it('names each page file', () => {
    expect(pageFile('/')).toBe('index.html');
    expect(pageFile('/forecasting')).toBe('forecasting.html');
  });

  it('records every file with its SHA-256, the pages, the policy and the export', () => {
    const directory = mkdtempSync(join(tmpdir(), 'atlas-site-'));
    scratch.push(directory);
    writeFileSync(join(directory, 'index.html'), '<!doctype html>');
    const exportManifest = readFileSync(join(EXPORT, EXPORT_MANIFEST));
    const manifest = buildSiteManifest(directory, '1.0.0', DEFAULT_PAGES_BASE, exportManifest);
    expect(manifest).toMatchObject({
      format: 'atlas-static-site/1',
      profile: 'static',
      base: DEFAULT_PAGES_BASE,
      not_found: '404.html',
      content_security_policy: STATIC_CSP,
      export_manifest_sha256: createHash('sha256').update(exportManifest).digest('hex'),
    });
    expect(manifest.pages['/simulator']).toBe('simulator.html');
    expect(Object.keys(manifest.files)).toEqual(['index.html']);
  });

  it('keeps the dashboard policy, less what a policy in the page cannot carry', () => {
    expect(STATIC_CSP).toContain("script-src 'self'");
    expect(STATIC_CSP).toContain("require-trusted-types-for 'script'");
    expect(STATIC_CSP).not.toContain('frame-ancestors');
    expect(STATIC_CSP).not.toContain('unsafe');
  });

  it('the committed site manifest is of the default base and of this export', () => {
    const committed = JSON.parse(
      readFileSync(join(import.meta.dirname, '..', 'atlas-static-site.json'), 'utf-8'),
    ) as { base: string; export_manifest_sha256: string; files: Record<string, unknown> };
    expect(committed.base).toBe(DEFAULT_PAGES_BASE);
    expect(committed.export_manifest_sha256).toBe(
      createHash('sha256')
        .update(readFileSync(join(EXPORT, EXPORT_MANIFEST)))
        .digest('hex'),
    );
    expect(Object.keys(committed.files)).toContain('data/atlas-static-export.json');
  });
});
