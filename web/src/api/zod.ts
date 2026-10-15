/**
 * zod (mini build), configured before any schema exists.
 *
 * `jitless`: zod can compile object parsers with `new Function`, which the dashboard's
 * Content-Security-Policy forbids (no 'unsafe-eval'). Turned off here, zod never even
 * probes for eval, so the browser records no CSP violation. Every schema module imports
 * `z` from here, never from 'zod/mini' directly (checked by a unit test).
 */
import * as z from 'zod/mini';

z.config({ jitless: true });

export { z };
