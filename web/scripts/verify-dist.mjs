// Checks that dist/ is exactly what dist/atlas-dashboard.json lists: every file, its size
// and SHA-256, and nothing else. (CI also rebuilds from the lock and requires
// `git diff --exit-code -- web/dist` to be empty: the committed build is reproducible.)
import { createHash } from 'node:crypto';
import { lstatSync, readdirSync, readFileSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
const manifest = JSON.parse(readFileSync(join(dist, 'atlas-dashboard.json'), 'utf-8'));
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
for (const path of onDisk)
  if (!(path in manifest.files)) problems.push(`not in the manifest: ${path}`);
for (const path of listed) {
  let data;
  try {
    data = readFileSync(join(dist, path));
  } catch {
    problems.push(`missing: ${path}`);
    continue;
  }
  const { sha256, bytes } = manifest.files[path];
  if (data.length !== bytes || createHash('sha256').update(data).digest('hex') !== sha256)
    problems.push(`altered: ${path}`);
  // A link is never part of a build, even one pointing at the right bytes (servers refuse it).
  if (!lstatSync(join(dist, path)).isFile()) problems.push(`not a regular file: ${path}`);
}
if (problems.length) {
  console.error(problems.join('\n'));
  process.exit(1);
}
console.log(`dist/ matches its manifest: ${listed.length} files, version ${manifest.version}`);
