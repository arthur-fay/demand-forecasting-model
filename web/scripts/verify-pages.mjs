// (M9) Checks a static build (dist-pages/, or the directory given) before it is published:
//
// 1. it is exactly what its atlas-static-site.json lists (every file, size and SHA-256,
//    nothing else, no link), and that manifest is byte for byte the committed
//    web/atlas-static-site.json: the build is the reviewed, reproducible one;
// 2. its data/ are the committed static export, byte for byte;
// 3. every page (index.html, each <page>.html, 404.html) is the same document and carries
//    the Content-Security-Policy in a <meta> before any script;
// 4. no file holds a credential, a private key, a local path or a local host (the licence
//    texts may name their own web sites; nothing else names another origin).
//
//   node scripts/verify-pages.mjs [dist-pages]
import { createHash } from 'node:crypto';
import { lstatSync, readdirSync, readFileSync } from 'node:fs';
import { join, relative, sep } from 'node:path';

const web = new URL('..', import.meta.url).pathname;
const dist = process.argv[2] ?? join(web, 'dist-pages');
const NAME = 'atlas-static-site.json';
const problems = [];
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');

const text = readFileSync(join(dist, NAME));
const manifest = JSON.parse(text.toString('utf-8'));
if (manifest.format !== 'atlas-static-site/1') problems.push(`${NAME} is not atlas-static-site/1`);
const committed = readFileSync(join(web, NAME));
if (!text.equals(committed))
  problems.push(`${NAME} differs from the committed web/${NAME}: rebuild and commit it`);

const walk = (dir) =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory()
      ? walk(join(dir, e.name))
      : [relative(dist, join(dir, e.name)).split(sep).join('/')],
  );
const onDisk = walk(dist)
  .filter((p) => p !== NAME)
  .sort();
for (const path of onDisk)
  if (!(path in manifest.files)) problems.push(`not in the manifest: ${path}`);
for (const [path, { sha256: expected, bytes }] of Object.entries(manifest.files)) {
  let data;
  try {
    data = readFileSync(join(dist, path));
  } catch {
    problems.push(`missing: ${path}`);
    continue;
  }
  if (data.length !== bytes || sha256(data) !== expected) problems.push(`altered: ${path}`);
  if (!lstatSync(join(dist, path)).isFile()) problems.push(`not a regular file: ${path}`);
}

// 2. The data are the committed export.
const exportDir = join(web, 'static-export');
const exported = readdirSync(exportDir).sort();
const shipped = onDisk
  .filter((p) => p.startsWith('data/'))
  .map((p) => p.slice(5))
  .sort();
if (JSON.stringify(exported) !== JSON.stringify(shipped))
  problems.push(`data/ is not the static export: ${shipped.join(', ')}`);
for (const name of exported) {
  const a = readFileSync(join(exportDir, name));
  let b;
  try {
    b = readFileSync(join(dist, 'data', name));
  } catch {
    continue;
  }
  if (!a.equals(b)) problems.push(`data/${name} differs from static-export/${name}`);
}

// 3. Every page is the app, with its policy before any script.
const index = readFileSync(join(dist, 'index.html'));
const pages = [...Object.values(manifest.pages), manifest.not_found];
for (const page of pages) {
  const html = readFileSync(join(dist, page));
  if (!html.equals(index)) problems.push(`${page} is not the same document as index.html`);
}
const html = index.toString('utf-8');
const meta = html.indexOf(
  `<meta http-equiv="Content-Security-Policy" content="${manifest.content_security_policy}" />`,
);
const script = html.indexOf('<script');
if (meta < 0 || script < 0 || meta > script)
  problems.push('index.html has no Content-Security-Policy <meta> before its first script');
if (/<script(?![^>]*\bsrc=)[^>]*>/.test(html)) problems.push('index.html has an inline script');
if (!html.includes(`src="${manifest.base}assets/`)) problems.push('assets are not under the base');

// 4. Nothing secret or local in any text file.
const UNSAFE = [
  ['a private key', /-----BEGIN [A-Z ]*PRIVATE KEY-----/],
  ['a GitHub token', /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}/],
  ['an AWS key', /\bAKIA[0-9A-Z]{16}\b/],
  ['a Slack token', /\bxox[abprs]-[A-Za-z0-9-]{10,}/],
  ['an API key value', /\bsk-[A-Za-z0-9]{20,}/],
  [
    'a credential assignment',
    /(?:api[_-]?key|password|passwd|secret|token)["']?\s*[:=]\s*["'][^"']{8,}/i,
  ],
  ['an API key header', /x-api-key/i],
  ['a local path', /(?<![\w.:/])\/(?:home|root|Users|tmp|private|mnt)\/[A-Za-z0-9._-]+/],
  ['a local host', /\b(?:localhost|127\.0\.0\.1)\b/],
];
for (const path of onDisk) {
  if (!/\.(?:html|js|css|json|txt|svg)$/.test(path)) continue;
  const content = readFileSync(join(dist, path), 'utf-8');
  for (const [what, pattern] of UNSAFE) {
    const match = content.match(pattern);
    if (match) problems.push(`${path} holds ${what}: ${match[0].slice(0, 60)}`);
  }
}

if (problems.length) {
  console.error(problems.join('\n'));
  process.exit(1);
}
console.log(
  `${relative(web, dist) || dist} matches ${NAME}: ${onDisk.length} files under ${manifest.base}, ` +
    `${shipped.length} data files, version ${manifest.version}; no credential or local detail`,
);
