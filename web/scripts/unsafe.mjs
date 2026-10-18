// (M9) What must never be in a published file: credentials, local or internal detail, and
// links to any site but the few the bundled code and licences name. verify-pages.mjs runs
// it over every text file of the static build and every file name; build/unsafe.test.ts
// tests every pattern. A scan finds what its patterns describe, not every possible secret:
// it is a check on mistakes, next to the review of what is committed.
import { isIPv6 } from 'node:net';

/** The only file types a static build may contain. */
export const ALLOWED_EXTENSIONS = ['.html', '.js', '.css', '.json', '.txt', '.svg', '.woff2'];

/** Binary files: not scanned as text; each must carry its format's signature. */
export const BINARY_EXTENSIONS = ['.woff2'];

/** The first bytes of each binary type (WOFF2: `wOF2`). */
export const SIGNATURES = { '.woff2': 'wOF2' };

/**
 * The hosts a built file may name in a URL: SVG and MathML namespaces, React's error
 * pages, the repository and the Inter font's source, and the font's licence.
 */
export const ALLOWED_HOSTS = ['www.w3.org', 'react.dev', 'github.com', 'scripts.sil.org'];

/** [what it is, pattern]: each pattern is reviewed, and none occurs in a clean build. */
export const UNSAFE = [
  ['a private key', /-----BEGIN [A-Z ]*PRIVATE KEY-----/],
  ['a GitHub token', /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}/],
  ['an AWS key', /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/],
  ['a Slack token', /\bxox[abprs]-[A-Za-z0-9-]{10,}/],
  ['an API key value', /\bsk-[A-Za-z0-9_-]{20,}/],
  ['a JSON web token', /\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+/],
  [
    'a credential assignment',
    // A quoted value, or an unquoted one that looks like a secret (long, or with a digit).
    /(?:api[_-]?key|password|passwd|secret|token)["']?\s*[:=]\s*(?:["'][^"']{8,}|[A-Za-z0-9_+/=-]{12,}|(?=[A-Za-z0-9_+/=-]*\d)[A-Za-z0-9_+/=-]{8,})/i,
  ],
  ['an authorization header', /\bauthorization\s*:\s*["']?[A-Za-z0-9]/i],
  [
    'a bearer or basic credential',
    /\b(?:Bearer|Basic) (?=[A-Za-z0-9._~+/-]*\d)[A-Za-z0-9._~+/-]{16,}/,
  ],
  ['an API key header', /x-api-key/i],
  ['a URL with credentials', /\b[a-z][a-z0-9+.-]*:\/\/[^/\s:@"'<>]*:[^/\s@"'<>]+@/i],
  ['a file URL', /\bfile:\/\//i],
  [
    'a local path',
    /(?<![\w.:/])\/(?:home|root|Users|tmp|var|etc|opt|srv|mnt|private|run|usr|nix|Volumes|workspace|__w)\/[A-Za-z0-9._-]/,
  ],
  [
    'a Windows path',
    /\b[A-Za-z]:[\\/]+(?:Users|Windows|Program Files|ProgramData|home)\b|\b[A-Za-z]:(?:\\{1,2}|\/)[\w .$-]+(?:\\{1,2}|\/)[\w.$-]/i,
  ],
  ['a network share', /\\\\[A-Za-z0-9.-]+\\[A-Za-z0-9$._-]+/],
  ['a local host', /\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0)\b|\[::1?\]/i],
  [
    'an internal host name',
    /\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:local|localdomain|internal|corp|lan|intranet|home\.arpa)\b/i,
  ],
  [
    'an IP address',
    /(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])/,
  ],
  ['an IPv6 address', /\b(?:[0-9a-f]{1,4}:){5,7}[0-9a-f]{1,4}\b/i],
  ['an e-mail address', /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b/],
];

/** Spaces a browser skips in a URL (tab, newline) or around it. */
const GAP = '[\\t\\n\\r ]*';
const HOST_NAME =
  '[a-z0-9-]+(?:\\.[a-z0-9-]+)*\\.[a-z][a-z0-9-]+|\\[[0-9a-f:.]+\\]|\\d{1,3}(?:\\.\\d{1,3}){3}';
/**
 * Where a URL names a host: any scheme followed by two or more slashes (or backslashes);
 * http, https, ws, wss and ftp with fewer (a browser reads `https:host` and `https:/host` as
 * `https://host`); and a protocol-relative `//host` where a link, a source, a CSS url() or
 * a string starts.
 */
const LINKS = [
  new RegExp(`\\b[a-z][a-z0-9+.-]*:(?:${GAP}[\\\\/]){2,}${GAP}([^\\s/"'\`<>)\\]\\\\?#]+)`, 'gi'),
  new RegExp(`\\b(?:https?|wss?|ftp):${GAP}[\\\\/]?${GAP}(${HOST_NAME})(?![a-z0-9-])`, 'gi'),
  new RegExp(
    `(?:\\b(?:href|src|srcset|action|formaction|poster|data|xlink:href)\\s*=\\s*["'\`]?|\\burl\\(\\s*["']?|@import\\s+["']|["'\`])${GAP}[\\\\/]{2}${GAP}(${HOST_NAME})(?![a-z0-9-])`,
    'gi',
  ),
];

/** A compressed IPv6 address (`::`), as the shortest run a browser or server would read. */
const IPV6_COMPRESSED = /(?<![\w:.])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![\w:.])/gi;

/** Every finding in `text`: '<what>: <match>' (empty when the text is clean). */
export function findUnsafe(text) {
  const found = [];
  for (const [what, pattern] of UNSAFE) {
    const match = pattern.exec(text);
    if (match) found.push(`${what}: ${match[0].slice(0, 80)}`);
  }
  for (const match of text.matchAll(IPV6_COMPRESSED)) {
    const groups = match[0].split(':').filter((group) => group !== '');
    if (match[0].includes('::') && groups.length >= 2 && isIPv6(match[0])) {
      found.push(`an IPv6 address: ${match[0]}`);
      break;
    }
  }
  const seen = new Set();
  for (const pattern of LINKS)
    for (const match of text.matchAll(pattern)) {
      const host = match[1].toLowerCase().replace(/:\d+$/, '');
      if (!ALLOWED_HOSTS.includes(host) && !seen.has(match.index)) {
        seen.add(match.index);
        found.push(`a link to another site: ${match[0].trim().slice(0, 80)}`);
      }
    }
  return found;
}
