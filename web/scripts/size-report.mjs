// Bundle sizes of the committed build, raw and gzip (what nginx and the API send), with
// budgets: the first load of a page must stay small enough for a modest phone on a slow
// connection. Prints a Markdown table; exits 1 if a budget is exceeded.
//
// (M9) `--pages`: the static build (dist-pages/, under its base path), with budgets for its
// data files too (GitHub Pages compresses JSON as it sends it).
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { gzipSync } from 'node:zlib';

const pages = process.argv.includes('--pages');
const dist = new URL(pages ? '../dist-pages/' : '../dist/', import.meta.url).pathname;
const manifest = JSON.parse(
  readFileSync(join(dist, pages ? 'atlas-static-site.json' : 'atlas-dashboard.json'), 'utf-8'),
);
const base = manifest.base ?? '/';
const html = readFileSync(join(dist, 'index.html'), 'utf-8');
const kb = (n) => (n / 1000).toFixed(1);
const gz = (path) =>
  path.endsWith('.woff2')
    ? readFileSync(join(dist, path)).length
    : gzipSync(readFileSync(join(dist, path)), { level: 9 }).length;

// What index.html loads before anything else: its script, preloads and stylesheet.
const initial = [...html.matchAll(/(?:src|href)="([^"]+)"/g)]
  .map((m) => m[1])
  .filter((p) => p.startsWith(base))
  .map((p) => p.slice(base.length))
  .filter((p) => p in manifest.files);
const rows = Object.entries(manifest.files)
  .map(([path, { bytes }]) => ({
    path,
    bytes,
    gzip: gz(path),
    initial: path === 'index.html' || initial.includes(path),
  }))
  .sort((a, b) => b.gzip - a.gzip);

const sum = (list, key) => list.reduce((total, row) => total + row[key], 0);
const first = rows.filter((r) => r.initial);
const firstJs = first.filter((r) => r.path.endsWith('.js'));
const lazyJs = rows.filter((r) => !r.initial && r.path.endsWith('.js'));
const font = rows.filter((r) => r.path.endsWith('.woff2'));
const data = rows.filter((r) => r.path.startsWith('data/'));
const code = rows.filter((r) => !r.path.startsWith('data/'));

const BUDGETS = [
  ['initial JavaScript (gzip)', sum(firstJs, 'gzip'), 110_000],
  ['initial HTML, CSS and JavaScript (gzip)', sum(first, 'gzip'), 125_000],
  ['largest lazy chunk (gzip)', Math.max(...lazyJs.map((r) => r.gzip)), 30_000],
  ['font (woff2)', sum(font, 'gzip'), 50_000],
  // The static build adds a copy of index.html per page and 404.html (1.4 KB each).
  [pages ? 'whole build without data (raw)' : 'whole build (raw)', sum(code, 'bytes'), 1_000_000],
  ...(pages
    ? [
        // Each page reads only the files it shows (the scenarios one at a time).
        ['largest data file (gzip)', Math.max(...data.map((r) => r.gzip)), 30_000],
        ['every data file (gzip)', sum(data, 'gzip'), 300_000],
        ['every data file (raw)', sum(data, 'bytes'), 1_500_000],
      ]
    : []),
];

console.log('| File | Raw (KB) | gzip (KB) | First load |');
console.log('|---|---:|---:|:---:|');
for (const r of rows)
  console.log(`| ${r.path} | ${kb(r.bytes)} | ${kb(r.gzip)} | ${r.initial ? 'yes' : ''} |`);
console.log('\n| Budget | Measured (KB) | Limit (KB) | |');
console.log('|---|---:|---:|---|');
let failed = false;
for (const [name, value, limit] of BUDGETS) {
  const ok = value <= limit;
  failed ||= !ok;
  console.log(`| ${name} | ${kb(value)} | ${kb(limit)} | ${ok ? 'ok' : 'OVER BUDGET'} |`);
}
process.exit(failed ? 1 : 0);
