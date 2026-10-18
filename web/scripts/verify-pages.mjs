// (M9) Checks a static build (dist-pages/, or the directory given) before it is published:
//
// 1. it is exactly what its atlas-static-site.json lists (every file, size and SHA-256,
//    nothing else, no link), and that manifest is byte for byte the one committed in git
//    (`git show HEAD:web/atlas-static-site.json`: the build writes its own copy into the
//    working tree, so comparing with that copy alone would prove nothing);
// 2. its data/ are the committed static export, byte for byte;
// 3. every page (index.html, each <page>.html, 404.html) is the same document and carries
//    the Content-Security-Policy in a <meta> before any script;
// 4. every file is of an allowed type, and no text file holds a credential, a private key,
//    a local or internal path, host or address, an e-mail address, or a link to a site
//    other than the few the bundled code and licences name (scripts/unsafe.mjs).
//
//   node scripts/verify-pages.mjs [dist-pages]
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { lstatSync, readdirSync, readFileSync } from 'node:fs';
import { extname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

import { ALLOWED_EXTENSIONS, BINARY_EXTENSIONS, findUnsafe } from './unsafe.mjs';

const web = fileURLToPath(new URL('..', import.meta.url));
const dist = process.argv[2] ?? join(web, 'dist-pages');
const NAME = 'atlas-static-site.json';
const problems = [];
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const own = (object, key) => Object.hasOwn(object, key);

const text = readFileSync(join(dist, NAME));
const manifest = JSON.parse(text.toString('utf-8'));
if (manifest.format !== 'atlas-static-site/1') problems.push(`${NAME} is not atlas-static-site/1`);
let committed = null;
try {
  committed = execFileSync('git', ['show', `HEAD:web/${NAME}`], { cwd: web, maxBuffer: 1 << 24 });
} catch {
  problems.push(`web/${NAME} cannot be read from git (HEAD): commit it, in a git checkout`);
}
if (committed !== null && !text.equals(committed))
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
const listed = Object.keys(manifest.files).sort();
if (JSON.stringify(onDisk) !== JSON.stringify(listed)) {
  for (const path of onDisk)
    if (!own(manifest.files, path)) problems.push(`not in the manifest: ${path}`);
  for (const path of listed) if (!onDisk.includes(path)) problems.push(`missing: ${path}`);
}
for (const path of listed) {
  if (!onDisk.includes(path)) continue;
  const { sha256: expected, bytes } = manifest.files[path];
  const data = readFileSync(join(dist, path));
  if (data.length !== bytes || sha256(data) !== expected) problems.push(`altered: ${path}`);
  // A link is never part of a build, even one pointing at the right bytes.
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
  if (!shipped.includes(name)) continue;
  if (!readFileSync(join(exportDir, name)).equals(readFileSync(join(dist, 'data', name))))
    problems.push(`data/${name} differs from static-export/${name}`);
}

// 3. Every page is the app, with its policy before any script.
const index = readFileSync(join(dist, 'index.html'));
for (const page of [...Object.values(manifest.pages), manifest.not_found]) {
  let html;
  try {
    html = readFileSync(join(dist, page));
  } catch {
    problems.push(`page missing: ${page}`);
    continue;
  }
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

// 4. Allowed types only; nothing secret, local or foreign in any text.
for (const path of onDisk) {
  const extension = extname(path);
  if (!ALLOWED_EXTENSIONS.includes(extension)) {
    problems.push(`a file of a type a build never holds: ${path}`);
    continue;
  }
  if (BINARY_EXTENSIONS.includes(extension)) continue;
  for (const finding of findUnsafe(readFileSync(join(dist, path), 'utf-8')))
    problems.push(`${path} holds ${finding}`);
}

if (problems.length) {
  console.error(problems.join('\n'));
  process.exit(1);
}
console.log(
  `${relative(web, dist) || dist} matches the committed ${NAME}: ${onDisk.length} files under ` +
    `${manifest.base}, ${shipped.length} data files, version ${manifest.version}; ` +
    'no credential, local detail or foreign link',
);
