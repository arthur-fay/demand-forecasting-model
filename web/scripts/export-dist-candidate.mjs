/**
 * Temporary read-only CI helper. Exports only changed generated dist files,
 * including ignored/untracked ones, as base64 Git objects for owner review.
 * Never modifies repository refs, releases, or publication state.
 */
import { execFileSync } from 'node:child_process';
import { readFileSync, readdirSync } from 'node:fs';
import { join, relative } from 'node:path';

const cwd = process.cwd();
const git = (args) => execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();
const tracked = new Set(git(['ls-files', '-z', '--', 'dist']).split('\0').filter(Boolean));
const files = [];
const walk = (directory) => {
  for (const item of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, item.name);
    if (item.isDirectory()) walk(path);
    else if (item.isFile()) files.push(relative(cwd, path).split('\\').join('/'));
    else throw new Error('Unexpected generated file type: ' + path);
  }
};
walk(join(cwd, 'dist'));
const present = new Set(files);
let changes = 0;
let deletions = 0;
for (const path of files.sort()) {
  const sha = git(['hash-object', path]);
  const previous = tracked.has(path) ? git(['rev-parse', 'HEAD:web/' + path]) : null;
  if (sha === previous) continue;
  const bytes = readFileSync(join(cwd, path));
  console.log('ATLAS_DIST_FILE_BEGIN ' + encodeURIComponent('web/' + path) + ' ' + sha + ' ' + bytes.length);
  const b64 = bytes.toString('base64');
  for (let i = 0; i < b64.length; i += 4000) console.log(b64.slice(i, i + 4000));
  console.log('ATLAS_DIST_FILE_END');
  changes++;
}
for (const path of [...tracked].sort()) if (!present.has(path)) {
  console.log('ATLAS_DIST_DELETE ' + encodeURIComponent('web/' + path));
  deletions++;
}
console.log('ATLAS_DIST_SUMMARY changed=' + changes + ' deleted=' + deletions);
