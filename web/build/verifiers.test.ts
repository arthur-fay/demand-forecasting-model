/**
 * The build verifiers (scripts/verify-dist.mjs, scripts/verify-pages.mjs) on planted copies
 * of the builds: each defect is reported by name, never followed, never a crash.
 * verify-pages needs a static build (`npm run build:pages`); the Pages workflow runs this
 * file after making one.
 */
import { spawnSync } from 'node:child_process';
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

const WEB = join(import.meta.dirname, '..');
const copies: string[] = [];

function copyOf(build: string): string {
  const directory = mkdtempSync(join(tmpdir(), 'atlas-verify-'));
  copies.push(directory);
  cpSync(join(WEB, build), join(directory, build), { recursive: true });
  return join(directory, build);
}

function verify(script: string, directory: string) {
  const run = spawnSync(process.execPath, [join(WEB, 'scripts', script), directory], {
    cwd: WEB,
    encoding: 'utf-8',
  });
  return { status: run.status, stderr: run.stderr };
}

afterEach(() => {
  for (const directory of copies.splice(0)) rmSync(directory, { recursive: true, force: true });
});

describe('verify-dist (the api build)', () => {
  it('passes the committed build', () => {
    expect(verify('verify-dist.mjs', copyOf('dist')).status).toBe(0);
  });

  it.each(['constructor', '__proto__', 'hasOwnProperty', 'other'])(
    'refuses an unlisted file named %s',
    (name) => {
      const dist = copyOf('dist');
      writeFileSync(join(dist, name), '{"x":1}');
      const run = verify('verify-dist.mjs', dist);
      expect(run.status).toBe(1);
      expect(run.stderr).toContain(`not in the manifest: ${name}`);
    },
  );

  it('reports a listed file that is a link, without following it', () => {
    const dist = copyOf('dist');
    rmSync(join(dist, 'favicon.svg'));
    symlinkSync(join(dist, 'assets'), join(dist, 'favicon.svg'));
    const run = verify('verify-dist.mjs', dist);
    expect(run.status).toBe(1);
    expect(run.stderr).toContain('not a regular file: favicon.svg');
    expect(run.stderr).not.toContain('EISDIR');
  });

  it('reports a manifest that is a link, or no manifest, without following it', () => {
    const dist = copyOf('dist');
    const manifest = join(dist, 'atlas-dashboard.json');
    const outside = join(dist, '..', 'atlas-dashboard.json');
    cpSync(manifest, outside);
    rmSync(manifest);
    symlinkSync(outside, manifest);
    const linked = verify('verify-dist.mjs', dist);
    expect(linked.status).toBe(1);
    expect(linked.stderr).toContain('not a regular file: atlas-dashboard.json');
    rmSync(manifest);
    writeFileSync(manifest, '{');
    const broken = verify('verify-dist.mjs', dist);
    expect(broken.status).toBe(1);
    expect(broken.stderr).toContain('not a dashboard manifest');
    expect(broken.stderr).not.toContain('SyntaxError');
  });
});

describe.skipIf(!existsSync(join(WEB, 'dist-pages', 'atlas-static-site.json')))(
  'verify-pages (the static build)',
  () => {
    it('reports a listed file that is a link to a directory, without a crash', () => {
      const site = copyOf('dist-pages');
      rmSync(join(site, 'favicon.svg'));
      symlinkSync(join(site, 'assets'), join(site, 'favicon.svg'));
      const run = verify('verify-pages.mjs', site);
      expect(run.status).toBe(1);
      expect(run.stderr).toContain('not a regular file: favicon.svg');
      expect(run.stderr).not.toContain('EISDIR');
    });

    it('refuses an unlisted file, a file name that holds an address and a font that is none', () => {
      const site = copyOf('dist-pages');
      writeFileSync(join(site, 'constructor'), '{}');
      writeFileSync(join(site, 'owner@example.org.txt'), 'x');
      const manifest = JSON.parse(readFileSync(join(site, 'atlas-static-site.json'), 'utf-8')) as {
        files: Record<string, unknown>;
      };
      const font = Object.keys(manifest.files).find((path) => path.endsWith('.woff2'));
      expect(font).toBeDefined();
      writeFileSync(join(site, font as string), 'not a font');
      const run = verify('verify-pages.mjs', site);
      expect(run.status).toBe(1);
      expect(run.stderr).toContain('not in the manifest: constructor');
      expect(run.stderr).toContain('the name owner@example.org.txt holds an e-mail address');
      expect(run.stderr).toContain(`${font as string} is not a .woff2 file (no signature)`);
    });

    it('scans the site manifest and names a build for another base', () => {
      const site = copyOf('dist-pages');
      const path = join(site, 'atlas-static-site.json');
      const manifest = JSON.parse(readFileSync(path, 'utf-8')) as Record<string, unknown>;
      // The build's own base is the committed one (only a build under it writes the record).
      const base = manifest.base as string;
      const other = base === '/' ? '/other/' : '/';
      writeFileSync(
        path,
        JSON.stringify({ ...manifest, base: other, note: 'https://evil.example/' }, null, 2),
      );
      const run = verify('verify-pages.mjs', site);
      expect(run.status).toBe(1);
      expect(run.stderr).toContain(
        `built for the base ${other}, the committed web/atlas-static-site.json for ${base}`,
      );
      expect(run.stderr).toContain('atlas-static-site.json holds a link to another site');
    });

    it.each([
      ['{', 'invalid JSON'],
      ['{"format":"atlas-static-site/1","files":null}', 'no files'],
      ['{"format":"atlas-static-site/1","files":{},"base":"/x/"}', 'no pages'],
    ])('reports a malformed manifest (%s: %s) without a crash', (text) => {
      const site = copyOf('dist-pages');
      writeFileSync(join(site, 'atlas-static-site.json'), text);
      const run = verify('verify-pages.mjs', site);
      expect(run.status).toBe(1);
      expect(run.stderr).toContain('atlas-static-site.json is not a valid atlas-static-site/1');
      // The difference from the committed record is reported too.
      expect(run.stderr).toMatch(/differs from the committed|built for the base \/x\//);
      expect(run.stderr).not.toMatch(/SyntaxError|TypeError|\n\s+at /);
    });

    it('reports a directory the manifest does not list, and scans its name', () => {
      const site = copyOf('dist-pages');
      mkdirSync(join(site, 'owner@example.org'));
      mkdirSync(join(site, 'assets', 'evil.example'));
      const run = verify('verify-pages.mjs', site);
      expect(run.status).toBe(1);
      expect(run.stderr).toContain('not in the manifest: owner@example.org/');
      expect(run.stderr).toContain('the name owner@example.org/ holds an e-mail address');
      expect(run.stderr).toContain('not in the manifest: assets/evil.example/');
    });
  },
);
