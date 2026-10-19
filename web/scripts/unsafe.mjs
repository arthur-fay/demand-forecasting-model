// (M9) What must never be in a published file: credentials, local or internal detail, and
// links to any site but the few the bundled code and licences name. verify-pages.mjs runs
// it over every text file of the static build and every file and directory name;
// build/unsafe.test.ts tests every pattern. A scan finds what its patterns describe, not
// every possible secret: it is a check on mistakes, next to the review of what is committed.
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

/** A word in any case, in a pattern whose other parts are case-sensitive. */
const ci = (word) => word.replace(/[a-z]/g, (c) => `[${c}${c.toUpperCase()}]`);
/** The characters of an unquoted credential value. */
const VALUE = '[A-Za-z0-9_+/=-]';

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
    // The keyword may carry a suffix (SECRET_KEY, client_secret_value). A quoted value, or
    // an unquoted one that looks like a secret: 8 or more characters with a digit, or 12 or
    // more in both cases, not followed by an expression (`token=createTokenizer(e)`).
    new RegExp(
      `(?:${ci('api')}[_-]?${ci('key')}|${ci('password')}|${ci('passwd')}|${ci('secret')}|${ci('token')})` +
        `(?:[_-]?[A-Za-z]+)*["']?\\s*[:=]\\s*(?:["'][^"']{8,}|(?:(?=${VALUE}*\\d)${VALUE}{8,}|` +
        `(?=${VALUE}*[a-z])(?=${VALUE}*[A-Z])${VALUE}{12,})(?![\\w+/=(.[-]))`,
    ),
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
    // A drive and a backslash (one, or two as in a JS string; not a JS escape such as
    // `A:\n`), or a drive and a forward slash with two segments.
    /\b[A-Za-z]:[\\/]+(?:[Uu]sers|[Ww]indows|[Pp]rogram [Ff]iles|[Pp]rogram[Dd]ata|home)\b|\b[A-Za-z]:(?:\\\\|\\(?![nrtbfvux0'"`\\]))[\w.$-]|\b[A-Za-z]:\/[\w .$-]+\/[\w.$-]/,
  ],
  ['a network share', /\\\\[A-Za-z0-9.-]+\\[A-Za-z0-9$._-]+/],
  [
    'a local host',
    /\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0)\b|\[::1?\]|(?<![\w:.])::1(?![\w:]|\.\d)/i,
  ],
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

/** What a browser skips inside a URL: tabs and newlines (spaces only around it). */
const IN = '[\\t\\n\\r]*';
const HOST_NAME =
  '[a-z0-9-]+(?:\\.[a-z0-9-]+)*\\.[a-z][a-z0-9-]+|\\[[0-9a-f:.]+\\]|\\d{1,3}(?:\\.\\d{1,3}){3}';
/** An optional user (and password) before the host: `//user@host`, `https://@host`. */
const USER = '(?:[^\\s/"\'`<>@\\\\]*@)?';
/**
 * Where a URL names a host:
 *
 * 1. any scheme followed by two slashes (tabs and newlines between them count for nothing);
 * 2. http, https, ws, wss and ftp, which also read a backslash as a slash and need fewer:
 *    `http:host` and `ws:host` name a host from an https page, as does any of them parsed
 *    without a base (the https forms, which a page of this site resolves to itself, are
 *    flagged too, to be safe). Without slashes, only where a string or a link begins, since
 *    minified code writes `{http:e.http}`;
 * 3. a protocol-relative `//host` (or more slashes, or backslashes, `\/\/` in a JS string)
 *    where a link, a source, a srcset candidate, a CSS `url()`, an `@import`, a refresh
 *    `url=` or a string begins.
 *
 * A regex literal after a property name (`{test:/\.ts$/}`) is none of these.
 */
const LINKS = [
  new RegExp(
    `\\b[a-z][a-z0-9+.-]*:${IN}/${IN}/[\\\\/\\t\\n\\r]*${USER}([^\\s/"'\`<>)\\]\\\\?#]+)`,
    'gi',
  ),
  new RegExp(
    `\\b(?:https?|wss?|ftp):(?:${IN}[\\\\/]){2,4}${IN}${USER}(${HOST_NAME})(?![a-z0-9-])`,
    'gi',
  ),
  new RegExp(
    `(?<=(?:["'\`(=]|\\burl\\()\\s*)(?:https?|wss?|ftp):(?:${IN}[\\\\/])?${IN}${USER}(${HOST_NAME})(?![a-z0-9-])`,
    'gi',
  ),
  new RegExp(
    `(?:\\b(?:href|src|srcset|action|formaction|poster|data|ping|xlink:href)\\s*=\\s*["'\`]?|\\bsrcset\\s*=\\s*["'][^"']*?,\\s*|\\burl\\s*=\\s*["']?|\\burl\\(\\s*["']?|@import\\s+["']|["'\`])[\\t\\n\\r ]*(?:${IN}[\\\\/]){2,}${IN}${USER}(${HOST_NAME})(?![a-z0-9-])`,
    'gi',
  ),
];

/**
 * A compressed IPv6 address (`::`), confirmed by `node:net`. It may end a sentence (a final
 * `.` not followed by a digit). Short runs of hex such as `a::b` or `C::9bb` (data tables,
 * scope operators) are not taken for one: an address needs a group of four digits, or three
 * groups (`fe80::1`, `2001:db8::abcd`, `fd00::1234:5678`).
 */
const IPV6_COMPRESSED = /(?<![\w:.])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![\w]|:[\w:]|\.\d)/gi;

function compressedIPv6(candidate) {
  const groups = candidate.split(':').filter((group) => group !== '');
  return (
    candidate.includes('::') &&
    groups.length >= 2 &&
    (groups.length >= 3 || groups.some((group) => group.length === 4)) &&
    isIPv6(candidate)
  );
}

/** Every finding in `text`: '<what>: <match>' (empty when the text is clean). */
export function findUnsafe(text) {
  const found = [];
  for (const [what, pattern] of UNSAFE) {
    const match = pattern.exec(text);
    if (match) found.push(`${what}: ${match[0].slice(0, 80)}`);
  }
  for (const match of text.matchAll(IPV6_COMPRESSED)) {
    if (compressedIPv6(match[0])) {
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
