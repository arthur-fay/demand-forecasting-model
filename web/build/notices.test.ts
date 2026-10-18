/**
 * The licence notices of the committed build (src/notices.ts, M8-05): every package that
 * reaches the browser is named with its version, licence and licence text, and the Inter
 * font ships with its copyright and the SIL Open Font License.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { FONT_LICENCE, THIRD_PARTY_NOTICES } from '../src/notices.ts';
import { MANIFEST_NAME, type DashboardManifest } from './atlas-manifest.ts';

/** A file below web/, as text. */
const read = (path: string) => readFileSync(join(import.meta.dirname, '..', path), 'utf-8');

interface Locked {
  readonly version: string;
  readonly license?: string;
  readonly dev?: boolean;
}

/** What the browser downloads: every package the lock does not mark as development-only. */
const runtime = Object.entries(
  (JSON.parse(read('package-lock.json')) as { packages: Record<string, Locked> }).packages,
)
  .filter(([path, entry]) => path !== '' && entry.dev !== true)
  .map(([path, entry]) => ({ ...entry, name: path.replace(/^node_modules\//, '') }));

/** The font is a CSS asset, which Vite's notices do not cover: it has its own file. */
const FONT = '@fontsource-variable/inter';

describe('the licence notices of the committed build', () => {
  const notices = read(`dist/${THIRD_PARTY_NOTICES}`);
  const sections = new Map(
    notices
      .split(/^## /m)
      .slice(1)
      .map((section) => {
        const [heading = '', ...text] = section.split('\n');
        return [heading, text.join('\n').trim()] as const;
      }),
  );

  it('name every bundled runtime package with its version, licence and licence text', () => {
    const expected = runtime
      .filter((p) => p.name !== FONT)
      .map((p) => `${p.name} - ${p.version} (${p.license ?? 'no licence'})`);
    expect(expected.length).toBeGreaterThan(10);
    // Exactly the runtime packages: no development tool is bundled into the code.
    expect([...sections.keys()].sort()).toEqual(expected.sort());
    for (const [heading, text] of sections) {
      expect(text, heading).toMatch(/Copyright/);
      expect(text, heading).toMatch(/permission/i);
    }
  });

  it('ship the Inter font with its copyright and the SIL Open Font License, as packaged', () => {
    const font = runtime.find((p) => p.name === FONT);
    expect(font?.license).toBe('OFL-1.1');
    const licence = read(`dist/${FONT_LICENCE}`);
    expect(licence).toBe(read(`node_modules/${FONT}/LICENSE`));
    expect(licence).toMatch(/^Copyright 2016 The Inter Project Authors/);
    expect(licence).toContain('SIL OPEN FONT LICENSE Version 1.1');
  });

  it('are files of the build, listed in its manifest', () => {
    const manifest = JSON.parse(read(`dist/${MANIFEST_NAME}`)) as DashboardManifest;
    expect(Object.keys(manifest.files)).toEqual(
      expect.arrayContaining([THIRD_PARTY_NOTICES, FONT_LICENCE]),
    );
  });
});
