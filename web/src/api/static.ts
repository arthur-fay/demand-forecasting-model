/**
 * (M9) The static profile's reads: the API's recorded answers, as files of the site.
 *
 * `atlas static export` records the exact body the API answers to each GET the read-only
 * pages make (web/static-export); the static build copies them to `<base>data/` and
 * compiles their list into the code (`__ATLAS_STATIC_INDEX__`: request, file, SHA-256 and
 * size). A read here:
 *
 * 1. finds its request (the same path and query the api profile would send) in that list;
 *    a request with no recorded answer is refused (`not_exported`) and nothing is fetched,
 *    so no other answer, cached or approximate, ever stands in for it;
 * 2. fetches the file from the same origin (no credentials, no redirects);
 * 3. requires its exact size and SHA-256 (Web Crypto), so a stale, damaged or substituted
 *    file is refused (`integrity`), never shown;
 * 4. then applies the checks every API answer gets (`accept`: no claim of a validated
 *    forecast or recommendation, and the schema).
 *
 * No write, no POST, no computation: a simulation is refused as `not_exported`.
 */
import { ApiError, DEFAULT_TIMEOUT_MS, accept, type RequestOptions } from './client';
import type { z } from './zod';

export interface StaticEntry {
  readonly file: string;
  readonly sha256: string;
  readonly bytes: number;
}

export type StaticIndex = Readonly<Record<string, StaticEntry>>;

/** Where the static build puts the exported files, below its base path. */
export const DATA_DIRECTORY = 'data/';

const FILE = /^[a-z0-9][a-z0-9_.-]{0,95}\.json$/;

function hex(buffer: ArrayBuffer): string {
  return [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export interface StaticReader {
  read<S extends z.core.$ZodType>(
    path: string,
    schema: S,
    options?: Pick<RequestOptions, 'signal' | 'timeoutMs'>,
  ): Promise<z.infer<S>>;
}

/** A reader of the recorded answers in `index`, served below `base`. */
export function staticReader(index: StaticIndex, base: string): StaticReader {
  return {
    async read(path, schema, options = {}) {
      const entry = Object.hasOwn(index, path) ? index[path] : undefined;
      if (entry === undefined || !FILE.test(entry.file)) {
        throw new ApiError(
          'not_exported',
          'This static copy of the dashboard holds no recorded answer for this request, so nothing is shown in its place.',
          path,
        );
      }
      const url = `${base}${DATA_DIRECTORY}${entry.file}`;
      const { signal, timeoutMs = DEFAULT_TIMEOUT_MS } = options;
      const controller = new AbortController();
      const deadline = { passed: false };
      const timer = setTimeout(() => {
        deadline.passed = true;
        controller.abort();
      }, timeoutMs);
      const forward = () => {
        controller.abort();
      };
      if (signal?.aborted) controller.abort();
      signal?.addEventListener('abort', forward, { once: true });
      let response: Response;
      let bytes: ArrayBuffer;
      try {
        response = await fetch(url, {
          headers: { Accept: 'application/json' },
          signal: controller.signal,
          credentials: 'omit',
          redirect: 'error',
          // Revalidated on every first read: a file replaced by a later publication is
          // fetched again, and the SHA-256 below decides whether it can be shown.
          cache: 'no-cache',
        });
        bytes = await response.arrayBuffer();
      } catch (cause) {
        if (deadline.passed) {
          throw new ApiError(
            'timeout',
            `The data file did not arrive within ${Math.round(timeoutMs / 1000)} s: the connection may be slow.`,
            path,
          );
        }
        if (signal?.aborted) throw new ApiError('aborted', 'The request was cancelled.', path);
        throw new ApiError(
          'network',
          `The data file could not be loaded (${cause instanceof Error ? cause.message : 'network error'}).`,
          path,
        );
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener('abort', forward);
      }
      if (!response.ok) {
        throw new ApiError(
          'http',
          `The site answered HTTP ${response.status} for its data file ${entry.file}.`,
          path,
          {
            status: response.status,
            code: response.status === 404 ? 'static_file_missing' : null,
          },
        );
      }
      // Web Crypto exists only in a secure context (https, or this machine): without it
      // nothing can be checked, so nothing is shown.
      const subtle = (globalThis as { crypto?: { subtle?: SubtleCrypto } }).crypto?.subtle;
      if (subtle === undefined) {
        throw new ApiError(
          'integrity',
          'This browser cannot check the data files here (the Web Crypto API needs a secure https connection), so nothing is shown.',
          path,
          { status: response.status },
        );
      }
      const digest = hex(await subtle.digest('SHA-256', bytes));
      if (bytes.byteLength !== entry.bytes || digest !== entry.sha256) {
        throw new ApiError(
          'integrity',
          `The data file ${entry.file} is not the one this build recorded (its SHA-256 differs), so nothing of it is shown. The site may be part-way through an update: reload the page.`,
          path,
          { status: response.status },
        );
      }
      let parsed: unknown;
      try {
        parsed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
      } catch {
        throw new ApiError('contract', 'The data file is not JSON.', path, {
          status: response.status,
        });
      }
      return accept(path, schema, parsed, { status: response.status, requestId: null });
    },
  };
}
