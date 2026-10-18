// Checks that dist/ (or the directory given) is exactly what its atlas-dashboard.json
// lists: every file, its size and SHA-256, and nothing else. (CI also rebuilds from the
// lock and requires `git diff --exit-code -- web/dist` to be empty: the committed build is
// reproducible.)
//
//   node scripts/verify-dist.mjs [dist]
import { createHash } from 'node:crypto';
import { lstatSync, readdirSync, readFileSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const dist = process.argv[2] ?? fileURLToPath(new URL('../dist/', import.meta.url));
// The manifest, too, must be a regular file of the build (a link is reported, not followed).
let manifest;
try {
  if (!lstatSync(join(dist, 'atlas-dashboard.json')).isFile())
    throw new Error('not a regular file: atlas-dashboard.json');
  manifest = JSON.parse(readFileSync(join(dist, 'atlas-dashboard.json'), 'utf-8'));
  if (typeof manifest.files !== 'object' || manifest.files === null) throw new Error('no files');
} catch (error) {
  console.error(
    error instanceof Error && error.message.startsWith('not a regular file')
      ? error.message
      : 'atlas-dashboard.json is missing, unreadable or not a dashboard manifest',
  );
  process.exit(1);
}
const walk = (dir) =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory()
      ? walk(join(dir, e.name))
      : [relative(dist, join(dir, e.name)).split(sep).join('/')],
  );
const onDisk = walk(dist)
  .filter((p) => p !== 'atlas-dashboard.json')
  .sort();
const listed = Object.keys(manifest.files).sort();
const problems = [];
// Own properties only: a file named `constructor` or `__proto__` is not listed.
for (const path of onDisk)
  if (!Object.hasOwn(manifest.files, path)) problems.push(`not in the manifest: ${path}`);
for (const path of listed) {
  if (!onDisk.includes(path)) {
    problems.push(`missing: ${path}`);
    continue;
  }
  // A link is never part of a build, even one pointing at the right bytes (servers refuse it).
  if (!lstatSync(join(dist, path)).isFile()) {
    problems.push(`not a regular file: ${path}`);
    continue;
  }
  let data;
  try {
    data = readFileSync(join(dist, path));
  } catch {
    problems.push(`unreadable: ${path}`);
    continue;
  }
  const { sha256, bytes } = manifest.files[path];
  if (data.length !== bytes || createHash('sha256').update(data).digest('hex') !== sha256)
    problems.push(`altered: ${path}`);
}
if (JSON.stringify(onDisk) !== JSON.stringify(listed) && problems.length === 0)
  problems.push('the files on disk are not the files the manifest lists');
if (problems.length) {
  console.error(problems.join('\n'));
  process.exit(1);
}
console.log(
  `${process.argv[2] ?? 'dist/'} matches its manifest: ${listed.length} files, version ${manifest.version}`,
);
