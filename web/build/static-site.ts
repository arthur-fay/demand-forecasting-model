/**
 * (M9) The static build for a static host such as GitHub Pages: `npm run build:pages`
 * (`vite build --mode pages`), written to `dist-pages/` and published as it is.
 *
 * A static host serves files and nothing else: no API, no rewrites, no response headers
 * of ours. So the build:
 *
 * - reads the static export (`static-export/`, written by `atlas static export`), checks
 *   every file against the SHA-256 and size its manifest records, and copies the files to
 *   `data/` with the manifest; the list (request, file, SHA-256, size) is compiled into
 *   the code, which refuses in the browser a file that differs (`src/api/static.ts`);
 * - is served under a base path (`ATLAS_PAGES_BASE`, by default the repository's path on
 *   GitHub Pages, `/demand-forecasting-model/`): every asset and data URL is absolute
 *   under it, so a page works at any depth;
 * - writes every page as `<page>.html` (GitHub Pages answers `/forecasting` with
 *   `forecasting.html`), so a deep link or a reload is answered 200 with the app, and a
 *   `404.html` that is the app as well: any other address shows its "Page not found" view,
 *   with a 404 status, and a host without extensionless pages still shows the right page;
 * - puts its Content-Security-Policy in the page (`<meta http-equiv>`, `STATIC_CSP`), as a
 *   static host cannot send it as a header: the dashboard's HTML policy without
 *   `frame-ancestors`, which a policy in the page cannot carry;
 * - records `atlas-static-site.json` (format `atlas-static-site/1`): every file with its
 *   SHA-256 and size, the base path, the pages and the export manifest's SHA-256. A build
 *   under the default base also writes it to `web/atlas-static-site.json`, which is
 *   committed: CI rebuilds and requires the same bytes (`git diff`), verify-pages compares
 *   the build with git's committed copy, and the publication checks the site against it.
 */
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';
import type { Plugin } from 'vite';

import { PAGE_PATHS } from '../src/pages.ts';

export const EXPORT_DIRECTORY = 'static-export';
export const EXPORT_MANIFEST = 'atlas-static-export.json';
export const EXPORT_FORMAT = 'atlas-static-export/1';
export const SITE_MANIFEST = 'atlas-static-site.json';
export const SITE_FORMAT = 'atlas-static-site/1';
export const DATA_DIRECTORY = 'data';
export const NOT_FOUND_PAGE = '404.html';
/** The project site of arthur-fay/demand-forecasting-model on GitHub Pages. */
export const DEFAULT_PAGES_BASE = '/demand-forecasting-model/';

/**
 * atlas.dashboard.DASHBOARD_CSP without `frame-ancestors` (ignored in a `<meta>` policy):
 * scripts, styles, fonts, images and requests from this origin only, nothing inline, no
 * eval, Trusted Types with no policy.
 */
export const STATIC_CSP =
  "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; " +
  "connect-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; " +
  "object-src 'none'; require-trusted-types-for 'script'; trusted-types 'none'";

const CHARSET = '<meta charset="utf-8" />';
const NOSCRIPT_API =
  'The ATLAS dashboard needs JavaScript. The research outputs are also available as JSON from\n        the ATLAS API under /api/v1.';
const NOSCRIPT_STATIC =
  'The ATLAS dashboard needs JavaScript. The recorded research outputs it shows are JSON files\n        listed in data/atlas-static-export.json.';

const FILE = /^[a-z0-9][a-z0-9_.-]{0,95}\.json$/;
const SHA256 = /^[0-9a-f]{64}$/;
const BASE = /^\/(?:[A-Za-z0-9._-]+\/)*$/;

export interface ExportEntry {
  readonly request: string;
  readonly sha256: string;
  readonly bytes: number;
}

export interface StaticExport {
  readonly manifest: Buffer;
  readonly version: string;
  readonly files: ReadonlyMap<string, { readonly entry: ExportEntry; readonly bytes: Buffer }>;
  /** What the code embeds: each request's file, SHA-256 and size. */
  readonly index: Record<string, { file: string; sha256: string; bytes: number }>;
}

const sha256 = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex');

/** The base path of a static build (`ATLAS_PAGES_BASE`), checked. */
export function pagesBase(env: Record<string, string | undefined>): string {
  const base = env.ATLAS_PAGES_BASE ?? DEFAULT_PAGES_BASE;
  if (!BASE.test(base) || base.split('/').some((part) => part === '.' || part === '..'))
    throw new Error(`ATLAS_PAGES_BASE must be a path such as /name/ (or /), not ${base}`);
  return base;
}

/** Read and check the static export in `directory` (throws on any difference). */
export function readExport(directory: string): StaticExport {
  const manifest = readFileSync(join(directory, EXPORT_MANIFEST));
  const parsed = JSON.parse(manifest.toString('utf-8')) as {
    format?: unknown;
    atlas_version?: unknown;
    files?: Record<string, ExportEntry>;
  };
  if (parsed.format !== EXPORT_FORMAT || typeof parsed.atlas_version !== 'string')
    throw new Error(`${EXPORT_MANIFEST} is not an ${EXPORT_FORMAT} manifest`);
  const listed = parsed.files ?? {};
  const present = readdirSync(directory).filter((name) => name !== EXPORT_MANIFEST);
  const unlisted = present.filter((name) => !Object.hasOwn(listed, name));
  if (unlisted.length > 0)
    throw new Error(`files the export manifest does not list: ${unlisted.join(', ')}`);
  const files = new Map<string, { entry: ExportEntry; bytes: Buffer }>();
  const index: Record<string, { file: string; sha256: string; bytes: number }> = {};
  for (const [name, entry] of Object.entries(listed)) {
    if (!FILE.test(name) || !SHA256.test(entry.sha256) || !entry.request.startsWith('/api/v1/'))
      throw new Error(`invalid export entry ${name}`);
    const bytes = readFileSync(join(directory, name));
    if (bytes.length !== entry.bytes || sha256(bytes) !== entry.sha256)
      throw new Error(`${name} differs from the SHA-256 or size the export manifest records`);
    if (Object.hasOwn(index, entry.request)) throw new Error(`two files answer ${entry.request}`);
    files.set(name, { entry, bytes });
    index[entry.request] = { file: name, sha256: entry.sha256, bytes: entry.bytes };
  }
  return { manifest, version: parsed.atlas_version, files, index };
}

function walk(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return walk(path);
    if (!entry.isFile()) throw new Error(`${path}: only regular files may be built`);
    return [path];
  });
}

/** The page file of an app path: `/forecasting` is `forecasting.html` (`/` is `index.html`). */
export function pageFile(path: string): string {
  return path === '/' ? 'index.html' : `${path.slice(1)}.html`;
}

export interface SiteManifest {
  readonly format: typeof SITE_FORMAT;
  readonly version: string;
  readonly profile: 'static';
  readonly base: string;
  readonly entry: 'index.html';
  readonly not_found: typeof NOT_FOUND_PAGE;
  readonly pages: Readonly<Record<string, string>>;
  readonly export_manifest_sha256: string;
  readonly content_security_policy: string;
  readonly files: Readonly<Record<string, { readonly sha256: string; readonly bytes: number }>>;
}

export function buildSiteManifest(
  outDir: string,
  version: string,
  base: string,
  exportManifest: Buffer,
): SiteManifest {
  const files: Record<string, { sha256: string; bytes: number }> = {};
  const paths = walk(outDir)
    .map((path) => relative(outDir, path).split(sep).join('/'))
    .filter((path) => path !== SITE_MANIFEST)
    .sort();
  for (const path of paths) {
    const bytes = readFileSync(join(outDir, path));
    files[path] = { sha256: sha256(bytes), bytes: bytes.length };
  }
  return {
    format: SITE_FORMAT,
    version,
    profile: 'static',
    base,
    entry: 'index.html',
    not_found: NOT_FOUND_PAGE,
    pages: Object.fromEntries(PAGE_PATHS.map((path) => [path, pageFile(path)])),
    export_manifest_sha256: sha256(exportManifest),
    content_security_policy: STATIC_CSP,
    files,
  };
}

/** The static build's plugin (see the module comment). */
export function staticSite(options: {
  readonly version: string;
  readonly base: string;
  readonly exported: StaticExport;
  /** Where the committed copy of the site manifest is written (web/atlas-static-site.json). */
  readonly record: string;
}): Plugin {
  const { version, base, exported, record } = options;
  if (exported.version !== version)
    throw new Error(
      `the static export is of ATLAS ${exported.version}, the dashboard ${version}: export again`,
    );
  let outDir = '';
  return {
    name: 'atlas-static-site',
    apply: 'build',
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir);
    },
    transformIndexHtml: {
      order: 'pre',
      handler(html) {
        if (!html.includes(CHARSET) || !html.includes(NOSCRIPT_API))
          throw new Error('index.html changed: update build/static-site.ts');
        return html
          .replace(
            CHARSET,
            `${CHARSET}\n    <meta http-equiv="Content-Security-Policy" content="${STATIC_CSP}" />`,
          )
          .replace(NOSCRIPT_API, NOSCRIPT_STATIC);
      },
    },
    closeBundle() {
      const data = join(outDir, DATA_DIRECTORY);
      mkdirSync(data, { recursive: true });
      for (const [name, { bytes }] of exported.files) writeFileSync(join(data, name), bytes);
      writeFileSync(join(data, EXPORT_MANIFEST), exported.manifest);
      const index = readFileSync(join(outDir, 'index.html'));
      for (const path of PAGE_PATHS)
        if (path !== '/') writeFileSync(join(outDir, pageFile(path)), index);
      writeFileSync(join(outDir, NOT_FOUND_PAGE), index);
      const manifest = buildSiteManifest(outDir, version, base, exported.manifest);
      const text = JSON.stringify(manifest, null, 2) + '\n';
      writeFileSync(join(outDir, SITE_MANIFEST), text);
      // The committed record is the default base's (the published site); a build under
      // another base (ATLAS_PAGES_BASE) leaves it alone.
      if (base === DEFAULT_PAGES_BASE) writeFileSync(record, text);
      else
        this.warn(
          `base ${base}: web/${SITE_MANIFEST} not rewritten (it records ${DEFAULT_PAGES_BASE})`,
        );
    },
  };
}
