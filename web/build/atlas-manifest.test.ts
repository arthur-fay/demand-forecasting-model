/** The build plugin's manifest: exactly the built files, their hashes, the client routes. */
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { PAGE_PATHS } from '../src/pages.ts';
import { buildManifest, MANIFEST_FORMAT, MANIFEST_NAME } from './atlas-manifest.ts';

function build(files: Record<string, string>): string {
  const directory = mkdtempSync(join(tmpdir(), 'atlas-dist-'));
  for (const [path, text] of Object.entries(files)) {
    mkdirSync(join(directory, path, '..'), { recursive: true });
    writeFileSync(join(directory, path), text);
  }
  return directory;
}

const sha256 = (text: string) => createHash('sha256').update(text).digest('hex');

describe('buildManifest', () => {
  it('lists every built file with its hash and size, sorted, and the client routes', () => {
    const directory = build({
      'index.html': '<!doctype html>',
      'assets/b-12345678.js': 'b',
      'assets/a-12345678.css': 'a{}',
      [MANIFEST_NAME]: '{"stale": true}',
    });
    const manifest = buildManifest(directory, '1.2.3');
    expect(manifest.format).toBe(MANIFEST_FORMAT);
    expect(manifest.version).toBe('1.2.3');
    expect(manifest.entry).toBe('index.html');
    expect(manifest.routes).toEqual([...PAGE_PATHS]);
    // The manifest never lists itself; paths are POSIX and sorted.
    expect(Object.keys(manifest.files)).toEqual([
      'assets/a-12345678.css',
      'assets/b-12345678.js',
      'index.html',
    ]);
    expect(manifest.files['index.html']).toEqual({
      sha256: sha256('<!doctype html>'),
      bytes: 15,
    });
  });

  it('refuses a build without index.html', () => {
    expect(() => buildManifest(build({ 'assets/a-12345678.js': 'a' }), '1.0.0')).toThrow(
      /no index.html/,
    );
  });

  it('refuses a link in the build', () => {
    const directory = build({ 'index.html': 'x' });
    symlinkSync('/etc/hostname', join(directory, 'linked.txt'));
    expect(() => buildManifest(directory, '1.0.0')).toThrow(/only regular files/);
  });
});
