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
| Deployment (M6) | no exposure found; two MEDIUM consistency defects and three LOW issues corrected (M8-02, M8-03, M8-24 to M8-26); in the final review, a floor for the dashboard policy a release records |
| Dashboard (M7) | strict CSP with Trusted Types verified in a browser; no credential in the browser; notices for shipped code added (M8-05), served since the final review with a policy for text |
| Dependencies | 0 known vulnerabilities (pip-audit, npm audit, all severities); lockfiles complete and hash-checked; no install script runs on Linux; monitoring added (M8-06) |
| CI | read-only permissions, actions pinned to verified SHAs, now on Node.js 24 (M8-32) |
| Secrets | none in the 1,228 blobs reachable from every ref of the candidate (979 at the baseline); secrets never in the environment, argv, logs or unit properties |

## 2. API

Tested against live servers on loopback (no key, with a key and CORS, with a 1 s deadline):

- **Contract.** The served OpenAPI equals the committed one. Schemathesis 4.31.1 generated 729
  cases over all 14 operations with every check enabled: no server error, no schema breach, no
  undocumented status. Its two reports of a schema-compliant request being refused (422) are
  documented cross-field rules: a history window outside the snapshot, and a quantile sent
  with a point-forecast basis. That run was on the baseline. The final independent review
  repeated it on the corrected API, with the same result.
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
  (M8-26).
- **The recorded dashboard policy (final review).** The policy a release records in
  `release.json` is rendered into nginx and verified against the API, so every tool holds it
  to a floor before using it. The release check refuses a record below the floor before
  anything changes, in every profile; rendering refuses it; and verification refuses it even
  when the API sends what the record says. The floor has four parts:
  - Recorded header names must be among those the dashboard sends (Content-Security-Policy,
    Cross-Origin-Opener-Policy, Cross-Origin-Resource-Policy, Permissions-Policy; compared
    without regard to case), each once. They are never one that a dashboard location sets
    itself (Strict-Transport-Security, X-Content-Type-Options, X-Frame-Options,
    Referrer-Policy, X-Robots-Tag, Cache-Control). Names and values must be in character sets
    nginx embeds safely.
  - The recorded HTML policy must be one policy naming each directive once, with
    `default-src 'none'`, `frame-ancestors 'none'`, `require-trusted-types-for 'script'` and
    a `script-src`.
  - In it, `script-src`, `script-src-elem`, `script-src-attr`, `worker-src` and `child-src`
    may name only `'self'`, hashes, `'none'` and `'report-sample'`: never `'unsafe-inline'`,
    `'unsafe-eval'`, `'strict-dynamic'`, a nonce, `*`, a scheme or a host.
  - A recorded policy for plain text (from 1.0.0) must likewise be one policy naming each
    directive once, with `default-src 'none'` and `frame-ancestors 'none'`, and no script
    source.

  0.7.0's record and 1.0.0's meet the floor. Before this correction the record was checked
  for characters only, and verification compared the API's policy with the record alone. A
  release whose code and record agreed on a permissive policy therefore verified, a weaker
  control than M7's. What the floor leaves open: the values of Cross-Origin-Opener-Policy,
  Cross-Origin-Resource-Policy and Permissions-Policy, and whether they are recorded at all,
  are checked only for characters nginx embeds safely, and directives other than those named
  are sent as recorded.

## 4. Dashboard

The API and nginx send a strict policy for the HTML (`default-src 'none'`, scripts and styles
from the same origin only, no inline code, `require-trusted-types-for 'script'`,
`frame-ancestors 'none'`) plus `X-Frame-Options`, `Referrer-Policy: no-referrer`, `nosniff`,
cross-origin isolation headers and a `Permissions-Policy`. Every page loads with no CSP or
Trusted Types violation, and since the final review so do both licence notices. The notices
are plain text shown in the browser's own viewer. They now get a policy for text
(`default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none';
frame-ancestors 'none'`) with the other dashboard headers. The HTML policy had refused the
viewer's inline style, so every visit raised a violation and long lines did not wrap. The
browser never holds a credential: the private profile relies on the
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
- **Secrets.** The scan covered every blob reachable from every ref for key formats, tokens,
  private keys, credentials in URLs and connection strings, and high-entropy assignments. Zip
  and PDF contents were included. It found none:
  - at the baseline, 979 blobs;
  - repeated on the candidate, 1,228 blobs. The only new matches were two quotations in
    [M8_AUDIT_EVIDENCE.md](M8_AUDIT_EVIDENCE.md): a description of key generation and the
    legacy Power BI path.

  Test passwords and keys are obvious fakes. The legacy Power BI file embeds only generic local
  paths.

## 6. What remains

- HTTP Basic accounts are shared secrets without MFA or roles, and one key protects simulations:
  adequate for a private demonstration, not for a public service.
- `public-demo` is refused by the tool until a security review, DDoS protection and a
  log-retention decision.
- A release's recorded dashboard policy is held to a floor on the headers it names, the
  default source, framing and which scripts run. The values of its other headers and its
  other directives are not checked beyond nginx-safe characters, and `release.json` is not
  authenticated: only root writes it.
- Operating-system packages (Python, OpenSSL, nginx) are not pinned; updates arrive through
  `unattended-upgrades`. After a Python update, restart the service and re-run the M4
  reproduction as the service user ([../DEPLOYMENT.md](../DEPLOYMENT.md) section 17).
- Commits are not signed; builds trust GitHub for the commit content and PyPI for the wheels
  (both hash-checked).
- Playwright's browser downloads in CI are not checksum-verified (CI only; nothing ships).
- The real server, its firewall, TLS issuance and network exposure are verifiable only in
  phase B.

The independent post-correction security review is summarised in
[M8_CORRECTIONS.md](M8_CORRECTIONS.md) section 1.
