/** Rules about the code base itself. */
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(import.meta.dirname);

function files(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return entry.name === 'fixtures' ? [] : files(path);
    return /\.(ts|tsx)$/.test(entry.name) ? [path] : [];
  });
}

describe('the source', () => {
  const sources = files(SRC).filter(
    (path) => !path.endsWith('.test.ts') && !path.endsWith('.test.tsx'),
  );

  it('imports zod only through api/zod.ts (jitless: no eval under the CSP)', () => {
    const offenders = sources.filter(
      (path) =>
        !path.endsWith(join('api', 'zod.ts')) && /from ['"]zod/.test(readFileSync(path, 'utf-8')),
    );
    expect(offenders).toEqual([]);
  });

  it('never injects HTML or evaluates strings', () => {
    const offenders = sources.filter((path) =>
      /dangerouslySetInnerHTML|\.innerHTML|new Function|\beval\(/.test(readFileSync(path, 'utf-8')),
    );
    expect(offenders).toEqual([]);
  });

  it('never stores what the user types (no storage, no cookies)', () => {
    const offenders = sources.filter((path) =>
      /localStorage|sessionStorage|document\.cookie/.test(readFileSync(path, 'utf-8')),
    );
    expect(offenders).toEqual([]);
  });

  it('talks to the API by same-origin paths only', () => {
    const offenders = sources.filter((path) =>
      /fetch\(\s*['"`]https?:/.test(readFileSync(path, 'utf-8')),
    );
    expect(offenders).toEqual([]);
  });
});
