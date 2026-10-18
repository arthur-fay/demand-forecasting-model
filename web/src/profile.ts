/**
 * (M9) The build profile: where this build's data come from and where it is served. Both
 * are fixed when it is built (vite.config.ts); a build never guesses its profile.
 *
 * - `api` (the committed web/dist, `npm run build`): the ATLAS API on the same origin, at
 *   `/`, served by the API itself or by nginx (M7). Unchanged by M9.
 * - `static` (`npm run build:pages`): the API's recorded answers (web/static-export), read
 *   as files from a static host with no server behind it, under a path such as
 *   `/demand-forecasting-model/` (GitHub Pages). Read-only: nothing is computed.
 *
 * Both profiles share every view, chart and integrity component; only the transport
 * (api/endpoints.ts), the base path (router.tsx) and the words that say where an answer
 * came from (below) differ. Every build-time constant is read here and nowhere else.
 */
import type { StaticIndex } from './api/static';

export type Profile = 'api' | 'static';

export const STATIC: boolean = __ATLAS_STATIC__;
export const PROFILE: Profile = STATIC ? 'static' : 'api';

/** The path the dashboard is served under, with its trailing slash: `/` in the api profile. */
export const BASE: string = import.meta.env.BASE_URL;

/** The static export's files by request (static profile; null in the api profile). */
export const STATIC_INDEX: StaticIndex | null = __ATLAS_STATIC_INDEX__;

/** The words that say where the numbers come from: the static profile's. */
export const STATIC_ORIGIN_TEXT = {
  footer:
    'Every number is a recorded answer of the ATLAS API, exported from committed, verified research outputs; your browser checks each file against the SHA-256 this build recorded. This is a static, archived copy: nothing on it is computed live.',
  overview:
    "This static copy shows that API's recorded answers, each with its status and its sources; nothing on it is computed live.",
  limitations:
    'As the API stated them in the recorded answers; each also appears next to the numbers it qualifies',
  fileHashes:
    "Every file hash was verified against its run's record when this answer was exported (atlas static export), and your browser checked the answer itself against the SHA-256 this build recorded.",
  sources:
    'Each committed file behind the numbers, with the fingerprints verified when the answers were exported',
};

/** ... and the api profile's (M7's words, unchanged). */
export const API_ORIGIN_TEXT: typeof STATIC_ORIGIN_TEXT = {
  footer: 'Every number is served by the ATLAS API from committed, verified research outputs.',
  overview: 'Every number below comes from that API, with its status and its sources.',
  limitations: 'As the API states them; each also appears next to the numbers it qualifies',
  fileHashes: "Every file hash was verified by the server against its run's record at startup.",
  sources:
    'Each committed file behind the numbers, with the fingerprints the server verified at startup',
};

export const ORIGIN_TEXT = STATIC ? STATIC_ORIGIN_TEXT : API_ORIGIN_TEXT;
