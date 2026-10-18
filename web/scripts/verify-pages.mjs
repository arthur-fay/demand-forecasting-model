// (M9) Checks a static build (dist-pages/, or the directory given) before it is published:
//
// 1. it is exactly what its atlas-static-site.json lists (every file, size and SHA-256,
//    nothing else, no link), and that manifest is byte for byte the one committed in git
//    (`git show HEAD:web/atlas-static-site.json`: the build writes its own copy into the
//    working tree, so comparing with that copy alone would prove nothing);
// 2. its data/ are the committed static export, byte for byte;
// 3. every page (index.html, each <page>.html, 404.html) is the same document and carries
//    the Content-Security-Policy in a <meta> before any script;
// 4. every file is of an allowed type (a font starts with its signature), and no file name
//    and no text file, the manifest included, holds a credential, a private key, a local
//    or internal path, host or address, an e-mail address, or a link to a site other than
//    the few the bundled code and licences name (scripts/unsafe.mjs).
//
// A file that is not a regular file (a link, a directory where a file is listed) or cannot
// be read is reported, never followed.
//
//   node scripts/verify-pages.mjs [dist-pages]
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { lstatSync, readdirSync, readFileSync } from 'node:fs';
import { extname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

import { ALLOWED_EXTENSIONS, BINARY_EXTENSIONS, SIGNATURES, findUnsafe } from './unsafe.mjs';

const web = fileURLToPath(new URL('..', import.meta.url));
const dist = process.argv[2] ?? join(web, 'dist-pages');
const NAME = 'atlas-static-site.json';
const problems = [];
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const own = (object, key) => Object.hasOwn(object, key);

const files = new Map();
/** A file of the build, read once: null (and a problem) unless it is a readable regular file. */
function read(path) {
  if (files.has(path)) return files.get(path);
  let data = null;
  try {
    if (lstatSync(join(dist, path)).isFile()) data = readFileSync(join(dist, path));
    else problems.push(`not a regular file: ${path}`);
  } catch {
    problems.push(`unreadable: ${path}`);
  }
  files.set(path, data);
  return data;
}
function fail() {
  console.error(problems.join('\n'));
  process.exit(1);
}

const text = read(NAME);
if (text === null) fail();
let committed = null;
try {
  committed = execFileSync('git', ['show', `HEAD:web/${NAME}`], { cwd: web, maxBuffer: 1 << 24 });
} catch {
  problems.push(`web/${NAME} cannot be read from git (HEAD): commit it, in a git checkout`);
}
const parse = (bytes) => {
  try {
    return JSON.parse(bytes.toString('utf-8'));
  } catch {
    return null;
  }
};
const plain = (value) => typeof value === 'object' && value !== null && !Array.isArray(value);
const manifest = parse(text);
if (committed !== null && !text.equals(committed)) {
  const base = parse(committed)?.base;
  problems.push(
    typeof base === 'string' &&
      plain(manifest) &&
      typeof manifest.base === 'string' &&
      base !== manifest.base
      ? `${NAME} is built for the base ${manifest.base}, the committed web/${NAME} for ` +
          `${base}: a build for another address needs DEFAULT_PAGES_BASE changed ` +
          '(build/static-site.ts; docs/STATIC_DEMO.md section 6)'
      : `${NAME} differs from the committed web/${NAME}: rebuild and commit it`,
  );
}
if (
  !plain(manifest) ||
  manifest.format !== 'atlas-static-site/1' ||
  !plain(manifest.files) ||
  !plain(manifest.pages) ||
  typeof manifest.not_found !== 'string' ||
  typeof manifest.base !== 'string' ||
  typeof manifest.content_security_policy !== 'string' ||
  !Object.values(manifest.files).every((entry) => plain(entry))
) {
  problems.push(`${NAME} is not a valid atlas-static-site/1 manifest`);
  fail();
}

// Files and directories alike: an empty directory, or one whose name holds what a name must
// not, is part of what is published too.
const directories = [];
const walk = (dir) =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const path = relative(dist, join(dir, e.name)).split(sep).join('/');
    if (!e.isDirectory()) return [path];
    directories.push(path);
    return walk(join(dir, e.name));
  });
const onDisk = walk(dist)
  .filter((p) => p !== NAME)
  .sort();
const listed = Object.keys(manifest.files).sort();
if (JSON.stringify(onDisk) !== JSON.stringify(listed)) {
  for (const path of onDisk)
    if (!own(manifest.files, path)) problems.push(`not in the manifest: ${path}`);
  for (const path of listed) if (!onDisk.includes(path)) problems.push(`missing: ${path}`);
}
for (const directory of directories) {
  if (!listed.some((path) => path.startsWith(`${directory}/`)))
    problems.push(`not in the manifest: ${directory}/`);
  for (const finding of findUnsafe(directory))
    problems.push(`the name ${directory}/ holds ${finding}`);
}
for (const path of listed) {
  if (!onDisk.includes(path)) continue;
  const { sha256: expected, bytes } = manifest.files[path];
  // A link is never part of a build, even one pointing at the right bytes.
  const data = read(path);
  if (data !== null && (data.length !== bytes || sha256(data) !== expected))
    problems.push(`altered: ${path}`);
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
  const data = read(`data/${name}`);
  if (data !== null && !readFileSync(join(exportDir, name)).equals(data))
    problems.push(`data/${name} differs from static-export/${name}`);
}

// 3. Every page is the app, with its policy before any script.
const index = read('index.html');
if (index === null) fail();
for (const page of [...Object.values(manifest.pages), manifest.not_found]) {
  if (!onDisk.includes(page)) {
    problems.push(`page missing: ${page}`);
    continue;
  }
  const html = read(page);
  if (html !== null && !html.equals(index))
    problems.push(`${page} is not the same document as index.html`);
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

// 4. Allowed types only; nothing secret, local or foreign in any name or text.
for (const path of [...onDisk, NAME]) {
  for (const finding of findUnsafe(path)) problems.push(`the name ${path} holds ${finding}`);
  const extension = extname(path);
  if (!ALLOWED_EXTENSIONS.includes(extension)) {
    problems.push(`a file of a type a build never holds: ${path}`);
    continue;
  }
  const data = read(path);
  if (data === null) continue;
  if (BINARY_EXTENSIONS.includes(extension)) {
    if (!data.subarray(0, 4).equals(Buffer.from(SIGNATURES[extension], 'latin1')))
      problems.push(`${path} is not a ${extension} file (no signature)`);
    continue;
  }
  for (const finding of findUnsafe(data.toString('utf-8')))
    problems.push(`${path} holds ${finding}`);
}

if (problems.length) fail();
console.log(
  `${relative(web, dist) || dist} matches the committed ${NAME}: ${onDisk.length} files under ` +
    `${manifest.base}, ${shipped.length} data files, version ${manifest.version}; ` +
    'no credential, local detail or foreign link',
);
