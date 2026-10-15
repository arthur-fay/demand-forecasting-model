/**
 * The one way the dashboard talks to the API: same origin, JSON, validated.
 *
 * Every response is checked against its schema (`schemas.ts`) before anything renders.
 * Failures are typed (`ApiError.kind`) so each view can say exactly what went wrong; a
 * failed request is never replaced by other numbers. No credential is ever added here:
 * in the private deployment the browser's own HTTP Basic session covers the API (same
 * origin), and nginx adds the simulation key itself.
 */
import { ErrorResponse } from './schemas';
import { z } from './zod';

export type ApiErrorKind =
  /** The request never got an HTTP answer (offline, DNS, connection reset). */
  | 'network'
  /** No answer within the client's deadline. */
  | 'timeout'
  /** The caller cancelled (navigation, a newer request). Never shown as an error. */
  | 'aborted'
  /** An HTTP error status with the API's (or the proxy's) error body. */
  | 'http'
  /** A 2xx answer that does not match the contract: nothing of it is shown. */
  | 'contract'
  /** A 2xx answer claiming a validated recommendation or forecast: refused. */
  | 'integrity';

export interface ErrorDetail {
  readonly field: string;
  readonly message: string;
}

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly requestId: string | null;
  readonly details: readonly ErrorDetail[];
  readonly retryAfterSeconds: number | null;
  readonly path: string;

  constructor(
    kind: ApiErrorKind,
    message: string,
    path: string,
    options: {
      status?: number | null;
      code?: string | null;
      requestId?: string | null;
      details?: readonly ErrorDetail[];
      retryAfterSeconds?: number | null;
    } = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
    this.path = path;
    this.status = options.status ?? null;
    this.code = options.code ?? null;
    this.requestId = options.requestId ?? null;
    this.details = options.details ?? [];
    this.retryAfterSeconds = options.retryAfterSeconds ?? null;
  }
}

export const DEFAULT_TIMEOUT_MS = 20_000;

export interface RequestOptions {
  readonly signal?: AbortSignal | undefined;
  readonly timeoutMs?: number | undefined;
  readonly method?: 'GET' | 'POST';
  readonly body?: unknown;
}

function retryAfter(response: Response): number | null {
  const value = response.headers.get('Retry-After');
  if (value === null || !/^\d{1,4}$/.test(value)) return null;
  return Number(value);
}

/** A short, safe summary of why a body did not validate (paths and messages only). */
function summarise(issues: readonly z.core.$ZodIssue[]): ErrorDetail[] {
  return issues.slice(0, 5).map((issue) => ({
    field: issue.path.length ? issue.path.map(String).join('.') : '<body>',
    message: issue.message,
  }));
}

function claimsValidation(body: unknown): boolean {
  if (typeof body !== 'object' || body === null || !('integrity' in body)) return false;
  const { integrity } = body;
  if (typeof integrity !== 'object' || integrity === null) return false;
  const flags = integrity as Record<string, unknown>;
  return flags.validated_recommendation === true || flags.point_forecast_validated === true;
}

/** Fetch `path` (same origin) and validate the JSON answer with `schema`. */
export async function request<S extends z.core.$ZodType>(
  path: string,
  schema: S,
  options: RequestOptions = {},
): Promise<z.infer<S>> {
  const { signal, timeoutMs = DEFAULT_TIMEOUT_MS, method = 'GET', body } = options;
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
  let text: string;
  try {
    response = await fetch(path, {
      method,
      headers:
        body === undefined
          ? { Accept: 'application/json' }
          : { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: body === undefined ? null : JSON.stringify(body),
      signal: controller.signal,
      credentials: 'same-origin',
      redirect: 'error',
      cache: 'no-store',
    });
    text = await response.text();
  } catch (cause) {
    if (deadline.passed) {
      throw new ApiError(
        'timeout',
        `No answer within ${Math.round(timeoutMs / 1000)} s: the connection may be slow or the server busy.`,
        path,
      );
    }
    if (signal?.aborted) throw new ApiError('aborted', 'The request was cancelled.', path);
    throw new ApiError(
      'network',
      `The server could not be reached (${cause instanceof Error ? cause.message : 'network error'}).`,
      path,
    );
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', forward);
  }

  const requestId = response.headers.get('X-Request-ID');
  let parsed: unknown = undefined;
  let json = false;
  if ((response.headers.get('Content-Type') ?? '').includes('application/json')) {
    try {
      parsed = JSON.parse(text);
      json = true;
    } catch {
      json = false;
    }
  }

  if (!response.ok) {
    const error = json ? ErrorResponse.safeParse(parsed) : null;
    if (error?.success) {
      const { code, message, request_id, details } = error.data.error;
      throw new ApiError('http', message, path, {
        status: response.status,
        code,
        requestId: request_id ?? requestId,
        details,
        retryAfterSeconds: retryAfter(response),
      });
    }
    throw new ApiError('http', `The server answered HTTP ${response.status}.`, path, {
      status: response.status,
      requestId,
      retryAfterSeconds: retryAfter(response),
    });
  }
  if (!json) {
    throw new ApiError('contract', 'The answer is not JSON.', path, {
      status: response.status,
      requestId,
    });
  }
  if (claimsValidation(parsed)) {
    throw new ApiError(
      'integrity',
      'The answer claims a validated forecast or recommendation, which ATLAS never issues; it was refused.',
      path,
      { status: response.status, requestId },
    );
  }
  const result = z.safeParse(schema, parsed);
  if (!result.success) {
    throw new ApiError(
      'contract',
      'The answer does not match the API contract; nothing of it is shown.',
      path,
      {
        status: response.status,
        requestId,
        details: summarise(result.error.issues),
      },
    );
  }
  return result.data;
}

/** A human sentence for an error, by kind and code (used by every error state). */
export function describeError(error: unknown): {
  title: string;
  detail: string;
  retryable: boolean;
} {
  if (!(error instanceof ApiError)) {
    return { title: 'Unexpected error', detail: String(error), retryable: true };
  }
  switch (error.kind) {
    case 'timeout':
      return { title: 'The request timed out', detail: error.message, retryable: true };
    case 'network':
      return { title: 'The server could not be reached', detail: error.message, retryable: true };
    case 'contract':
      return {
        title: 'Unexpected answer from the API',
        detail: `${error.message} This usually means the dashboard and the API are from different releases.`,
        retryable: false,
      };
    case 'integrity':
      return { title: 'Answer refused', detail: error.message, retryable: false };
    case 'aborted':
      return { title: 'Cancelled', detail: error.message, retryable: true };
    case 'http':
      break;
  }
  switch (error.code) {
    case 'artifact_unavailable':
      return {
        title: 'This component is unavailable',
        detail: `${error.message} The server verified its committed results at startup and one of them did not pass; the other views keep working.`,
        retryable: true,
      };
    case 'server_busy':
    case 'simulations_busy':
    case 'rate_limited':
      return {
        title: 'The server is busy',
        detail: `${error.message}${error.retryAfterSeconds !== null ? ` Retry in ${error.retryAfterSeconds} s.` : ''}`,
        retryable: true,
      };
    case 'upstream_unavailable':
      return { title: 'The API is not answering', detail: error.message, retryable: true };
    case 'not_found':
      return {
        title: 'Not available in this deployment',
        detail: error.message,
        retryable: false,
      };
    case 'unauthorized':
      return { title: 'Not authorised', detail: error.message, retryable: false };
    default:
      return {
        title: error.status !== null && error.status >= 500 ? 'Server error' : 'Request refused',
        detail: error.message,
        retryable: error.status !== null && error.status >= 500,
      };
  }
}
