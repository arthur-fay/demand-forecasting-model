# M8 security and dependency assessment

The security review of ATLAS for the 1.0.0 release candidate: what was attacked and inspected,
what was found and corrected, and what remains. Threat model: a private-first research
demonstration on one server, nginx with TLS and HTTP Basic accounts in front of an API that
listens on loopback; an optional `public-demo` profile that must never compute.

**No automated tool and no review can show that software has no vulnerability.** This
assessment reports what was tested and what was found; it is not a certification.

## 1. Summary

| Area | Result |
|---|---|
| API (M5) | no exploitable defect found; three LOW issues corrected (M8-20, M8-21, M8-23) |
| Deployment (M6) | no exposure found; two MEDIUM consistency defects and three LOW issues corrected (M8-02, M8-03, M8-24 to M8-26) |
| Dashboard (M7) | strict CSP with Trusted Types verified in a browser; no credential in the browser; notices for shipped code added (M8-05) |
| Dependencies | 0 known vulnerabilities (pip-audit, npm audit, all severities); lockfiles complete and hash-checked; no install script runs on Linux; monitoring added (M8-06) |
| CI | read-only permissions, actions pinned to verified SHAs, now on Node.js 24 (M8-32) |
| Secrets | none in the 979 blobs of the history; secrets never in the environment, argv, logs or unit properties |

## 2. API

Tested against live servers on loopback (no key, with a key and CORS, with a 1 s deadline):

- **Contract.** The served OpenAPI equals the committed one. Schemathesis 4.31.1 generated 729
  cases over all 14 operations with every check enabled: no server error, no schema breach, no
  undocumented status.
- **Authentication.** The simulation key is compared in constant time (`hmac.compare_digest`)
  and checked before the body is read and again in the route. Missing, empty, wrong, prefix,
  Latin-1, 8 KB and comma-joined keys get 401; no path, method or header variant (encoded,
  trailing slash, double slash, case, `X-HTTP-Method-Override`) runs a simulation without it.
  Binding to a public address without a key is refused at startup.
- **Public demo.** It cannot compute at the API level, independently of nginx: every profile
  loads the key credential, and nginx renders no compute location outside the private profile.
- **Input and resources.** Strict models refuse every hostile input tried (deep nesting,
  duplicate keys, NaN and infinities, huge numbers, extra fields, path traversal, header
  injection, Host variants); bodies are capped; a slow request is cut; the in-flight cap holds;
  a full worker pool answers 503 with `Retry-After`, a deadline kills the worker (504) and the
  pool replaces it; memory stayed flat under load.
- **No computation on reads.** 750 instrumented reference requests made 0 solver calls, 0
  scenario builds and 0 process starts.
- **Integrity.** Every served file is hash-checked at startup and readiness fails closed when
  one is altered.
- **Disclosure.** Error bodies are generic; logs are JSON without the key or the query string.
- **Corrected.** The run identities of the M2 and M3 projection manifests are now checked
  against the M4 record (M8-20); HEAD is routed only where the API answers it (M8-21); the
  `/docs` and `/redoc` pages load one pinned version of their scripts with Subresource
  Integrity under their own restrictive policy (M8-23).

## 3. Deployment

- **Least privilege.** The API runs as an unprivileged account under a sandboxed unit
  (`systemd-analyze security` exposure 0.9); releases are root-owned and read-only; the key is a
  systemd credential; nginx adds the key only on compute locations, over a Unix socket only the
  service can create.
- **nginx.** TLS 1.2 and 1.3 with forward-secret AEAD ciphers, HSTS, Basic accounts on every
  location (dashboard files included), a route allow-list generated from the contract, rate,
  connection and body limits, read deadlines, a header allow-list, no certificate for unknown
  names. Rendered sites pass `nginx -t`.
- **Build integrity.** Releases are built from an exact commit (SHA checked), with every wheel
  checked against `uv.lock` hashes; the release's own checks run as the unprivileged user.
- **Corrected.** An interrupted activation could be recorded as verified while nginx still
  served the previous release (M8-02); a later tool could not roll back to an older release whose
  dashboard policy differed (M8-03); the environment fingerprint was not reproducible (M8-24);
  IPv6 was on by default although per-client limits count each address (M8-25);
  `render --apply` could install a site for a non-current release without the lock or a record
  (M8-26). The release's recorded dashboard headers are now rendered into nginx, so they are
  validated against strict character sets first; a crafted record is refused.

## 4. Dashboard

The API and nginx send a strict policy for the HTML (`default-src 'none'`, scripts and styles
from the same origin only, no inline code, `require-trusted-types-for 'script'`,
`frame-ancestors 'none'`) plus `X-Frame-Options`, `Referrer-Policy: no-referrer`, `nosniff`,
cross-origin isolation headers and a `Permissions-Policy`. Every page loads with no CSP or
Trusted Types violation. The browser never holds a credential: the private profile relies on the
browser's Basic session and nginx adds the key. The build is verified at startup (a tampered file
is refused) and reproduced byte for byte in CI. The shipped code's licence notices now ship with
it (M8-05).

## 5. Dependencies and supply chain

- **Locks.** `uv.lock`: 43 registry packages and 706 files, every SHA-256 equal to PyPI's, none
  yanked. `web/package-lock.json`: 428 entries, each with a SHA-512 integrity hash, resolved from
  the npm registry, with a valid registry signature; no git, HTTP or file sources. M8 changed
  neither lock except the project's own version.
- **Known vulnerabilities.** pip-audit (PyPI advisory data): 0 in the 40 packages that apply to
  the platform. `npm audit`: 0 at every severity (16 production, 413 development packages).
- **Install scripts.** The only npm install scripts are two macOS-only `fsevents` copies, which
  never run on Linux; the runtime installs from wheels only.
- **Licences of dependencies.** The shipped JavaScript is MIT, ISC and OFL-1.1 (now with its
  notices); the Python runtime is MIT, BSD, PSF and Apache. Copyleft (MPL-2.0, CC-BY-4.0) appears
  only in development tools that never ship.
- **Monitoring (M8-06).** A weekly workflow audits the locked Python packages (pinned pip-audit,
  hash-checked export) and the npm packages (moderate severity for what ships, high for
  everything), and runs on every pull request that changes a lock. The dependency policy, with
  the cooldown as a review rule and the review dates of the three caps, is in
  [../DEVELOPMENT.md](../DEVELOPMENT.md) (M8-33).
- **CI.** Workflow permissions `contents: read`; `persist-credentials: false`; no
  `pull_request_target`; no untrusted expression in a run step; every action pinned to a full
  SHA, independently checked against its tag, all on Node.js 24 (M8-32).
- **Secrets.** A scan of every blob reachable from every ref (979) for key formats, tokens,
  private keys and high-entropy assignments found none; test passwords and keys are obvious
  fakes. The legacy Power BI file embeds only generic local paths.

## 6. What remains

- HTTP Basic accounts are shared secrets without MFA or roles, and one key protects simulations:
  adequate for a private demonstration, not for a public service.
- `public-demo` is refused by the tool until a security review, DDoS protection and a
  log-retention decision.
- Operating-system packages (Python, OpenSSL, nginx) are not pinned; updates arrive through
  `unattended-upgrades` and a release must be rebuilt after a Python update.
- Commits are not signed; builds trust GitHub for the commit content and PyPI for the wheels
  (both hash-checked).
- Playwright's browser downloads in CI are not checksum-verified (CI only; nothing ships).
- The real server, its firewall, TLS issuance and network exposure are verifiable only in
  phase B.

The independent post-correction security review is summarised in
[M8_CORRECTIONS.md](M8_CORRECTIONS.md) section 1.
