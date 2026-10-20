# Static read-only demonstration (M9, GitHub Pages)

M9 makes the ATLAS dashboard work on a static file host, with no Python server, VPS or paid
backend behind it. The primary target is GitHub Pages, at the project address
`https://arthur-fay.github.io/demand-forecasting-model/`.

> **Status: M9 phase A, prepared and tested, not published.** GitHub Pages is not enabled for
> this repository, and nothing in M9 enables it. The publication workflow exists, but it
> cannot publish: its committed approval record states no owner decision
> ([section 10](#10-publication-controls)). Publication needs:
>
> - the NYC Open Data terms confirmed, and the code licence the owner chose (MIT, for original
>   ATLAS code only) recorded in the approval record
>   ([section 9](#9-licensing-and-data-use-prerequisites));
> - a version decision ([section 11](#11-release-and-versioning));
> - the owner's separate approval and three GitHub settings
>   ([section 12](#12-owner-instructions-activating-github-pages)).

What the static site shows is the same research output as the M7 dashboard, with the same
limitations next to the same numbers:

- **M2: the forecast's advantage is not established.** The lead over the best baseline is not
  significant after Holm. The M8 erratum is shown with the selection: a correctly sized test
  would most likely have kept the baseline.
- **M3: FAILED_CALIBRATION.** No interval or probability is decision-grade.
- **M4: every parameter is HYPOTHETICAL.** Six of the nine reference scenarios report
  DIAGNOSTIC plans.
- **Archived, not current.** The projection was issued from data ending 2026-06-30, and it has
  never been scored.
- **Research software**, not a validated NYC operational planning tool.

Contents:

1. What the static profile is
2. Architecture
3. The static export
4. The dashboard's static profile
5. GitHub Pages compatibility
6. Security and public information
7. Scientific integrity on the static site
8. Build, test and verify
9. Licensing and data-use prerequisites
10. Publication controls
11. Release and versioning
12. Owner instructions: activating GitHub Pages
13. Limitations and what remains unverified
14. Measured results
15. Independent review

## 1. What the static profile is

The dashboard now has two **build profiles** (`web/src/profile.ts`), chosen explicitly when it
is built and never guessed at run time:

| | api profile (M7) | static profile (M9) |
|---|---|---|
| Built by | `npm run build` | `npm run build:pages` (`vite build --mode pages`) |
| Output | `web/dist/`, committed | `web/dist-pages/`, built in CI; its manifest `web/atlas-static-site.json` is committed |
| Served by | the API itself, or nginx (private, public-demo) | any static file host; GitHub Pages first |
| Path | `/` | `/demand-forecasting-model/` (`ATLAS_PAGES_BASE`) |
| Numbers from | the API, live, over `/api/v1` | the API's recorded answers, files under `data/` |
| Simulations | when the deployment allows them | never: the recorded M4 reference scenarios instead |
| Operational detail (`/api/v1/system`) | private deployments only | never exported |
| Content-Security-Policy | HTTP header (`atlas.dashboard.DASHBOARD_CSP`) | `<meta>` in the page, without `frame-ancestors` |
| Needs at run time | Python, the API, optionally nginx | nothing: files only |

Both profiles render the same views, charts, tags, callouts and provenance footers. What
differs:

- the transport behind `api` (`web/src/api/endpoints.ts`);
- the base path (`web/src/router.tsx`);
- the deployment: asked of the server, or fixed in the build (`web/src/deployment.tsx`);
- the sentences that say where an answer came from (`web/src/profile.ts`, `ORIGIN_TEXT`, and
  the Provenance view);
- the Simulator: the recorded reference scenarios in place of a run (section 4);
- the build: the policy in a `<meta>`, the `noscript` text, the page files and `404.html`.

**The api profile's behaviour is unchanged.** M9 adds three things that both profiles show:

- the footer's sentence that ATLAS is not affiliated with or endorsed by the City of New York
  or NYC Open Data;
- on the Overview, next to the recommended model, the M8 erratum's conclusion ("after the M8
  size correction, a correctly sized selection test would most likely have kept the
  same-weekday median baseline") and where it is shown in full;
- an M9 entry in the Methods list.

Its committed build is rebuilt from the refactored sources, and the M7 tests run against it.
The static transport, reader and recorded-scenario panel are not in it. Some static-only
wording is, and is never shown there: the navigation line, the deployment badge, the
Provenance and Simulator explanations, and two error messages. A served descriptor can never
claim the static profile.

## 2. Architecture

```
 committed research outputs (docs/results, configs, snapshots/legacy-v0)
   │  the API's reference store checks every file against its run's SHA-256
   ▼
 atlas static export ── the API in-process: GET each read the static pages make
   │                    checks: status 200, integrity labels, sources, public safety
   ▼
 web/static-export/   24 answers, byte for byte, + atlas-static-export.json   (committed)
   │  atlas static verify: a new export must give the same bytes
   ▼
 npm run build:pages ─ checks each file against the export manifest, compiles the list
   │                   (request → file, SHA-256, size) into the code, writes each page
   │                   as <page>.html and 404.html, puts the CSP in the page
   ▼
 web/dist-pages/      + atlas-static-site.json (every file's SHA-256)
   │  = web/atlas-static-site.json (committed)  ·  verify-pages  ·  browser tests
   ▼
 GitHub Pages ─────── only by a manual, gated dispatch (section 10); not in phase A
   │
   ▼
 browser ── fetch <base>data/<file> ─► SHA-256 = the build's record? ─► the API's checks
            (integrity labels, schema) ─► the same views as the api profile
```

## 3. The static export

`atlas static export` (`src/atlas/api/static_export.py`) records the exact body the API
answers to each GET request the read-only pages make. The API runs in-process, over the
committed reference store, through its full middleware stack; no socket is opened.
`atlas static verify` checks an export against its manifest, then exports again and requires
the same bytes for every file and for the manifest.

```bash
uv run atlas static export     # writes web/static-export (replaces an earlier export)
uv run atlas static verify     # exit 0: the committed export is what the outputs give
```

**What is exported**: 24 answers, 1.07 MB raw (1.09 MB with the manifest) and 182 KB gzip in
all (Node's zlib at level 9, as `npm run size:pages` measures).

| Files | Requests (the exact paths and queries the api profile sends) |
|---|---|
| `models.json` | `/api/v1/models` |
| `forecast.json` | `/api/v1/forecasts?horizon_days=30` |
| `history.json`, `history-90d.json`, `history-365d.json`, `history-all.json` | `/api/v1/forecasts/history` (the API's default, the last 180 days) and `?start=` 2026-04-02, 2025-07-01, 2023-01-01: the Forecasting page's windows |
| `metrics-bucket.json` | `/api/v1/forecasts/metrics?scope=bucket` |
| `metrics-horizon-<period>.json` (3) | `/api/v1/forecasts/metrics?scope=horizon&period=` each M2 period |
| `uncertainty-status.json`, `intervals.json` | `/api/v1/uncertainty/status`, `/api/v1/uncertainty/intervals?horizon_days=30` |
| `scenarios.json`, `comparison.json` | `/api/v1/scenarios`, `/api/v1/scenarios/comparison` |
| `scenario-<id>.json` (9) | `/api/v1/scenarios/<id>`, every reference scenario |
| `simulation-parameters.json` | `/api/v1/simulations/parameters` (the reference inputs and their ranges) |

The periods and scenarios come from the answers themselves (`metrics-bucket.json`,
`scenarios.json`), so the export cannot leave one out. The history windows are those of the
Forecasting page; a Python test and a Vitest test both check that the two agree.

**What is not exported**, and why (also listed in the manifest's `not_exported`):

- `GET /api/v1/system`: operational metadata (component readiness, limits, worker state,
  solver versions, endpoint inventory). It is not public, as in the public-demo profile.
- `/health/*`: the state of a running server.
- The OpenAPI document and its pages: the contract is in the repository (`docs/api`).
- `POST /api/v1/simulations`: a computation. It needs the Python M4 engine.

**What a file is.** Each file holds the API's bytes, so its integrity block, notices,
provenance (repository-relative paths, run ids, fingerprints, file SHA-256) and numbers are
unchanged. Where the dashboard's test fixtures record the same request, the export's file is
byte-identical to the fixture.

**Determinism.** No clock, random value or environment-dependent setting enters an answer or
the manifest. The export's API settings are fixed: no key, no documentation pages, no
dashboard mount, and one host name of its own. The API's GET bodies are deterministic. Two
exports of one commit are byte-identical, which a test checks, also with the API's
environment variables set.

**The checks**, before anything is written. A failure stops the export and nothing is
written:

- **Inputs.** Every component of the reference store is ready: every committed research
  file matched its run's SHA-256.
- **Answers.** Every request answers 200 with JSON.
- **Integrity labels.** No answer claims a validated forecast or recommendation. Every M4
  answer is `HYPOTHETICAL`. All answers carry one M3 interval status, and each carries
  notices.
- **Provenance.** Every source path is repository-relative, with no `..`. Every source file
  hashes to the SHA-256 its answer names; this repeats the API's startup check
  independently.
- **Public safety.** No string or key looks like:
  - an absolute local path (Unix roots and CI workspaces), a Windows path (any drive), a
    network share or a `file:` URL;
  - a URL with credentials, a local or internal host name, or an IPv4 or IPv6 address
    (compressed forms included);
  - an e-mail address, a private key, a token (GitHub, AWS, Slack, API key, JSON web token)
    or a credential word;
  - or contains the repository's local path.

  These are patterns: they find what they describe, as a check on mistakes next to the
  review of what is committed. The export has no allow-list of hosts; the build's scan
  (section 6) has one, and scans the exported files too.

**The manifest**, `atlas-static-export.json` (format `atlas-static-export/1`), records:

- for each file: its request, SHA-256, size, `data_status` and `record_type`;
- the snapshot and its data fingerprint;
- the merged sources behind the answers;
- the scientific status the answers record, read from them and never asserted: H1 not
  significant, the selection-evidence notice present, `failed_calibration`, the archived
  projection's dates and notice, `HYPOTHETICAL`, and 3 of 9 scenarios strict;
- what was left out, and why.

It is also published, as `data/atlas-static-export.json`.

**When a result changes**, export again (`atlas static export`), rebuild the static site
(`npm run build:pages`), and commit the export and `web/atlas-static-site.json`. Record the
dashboard's test fixtures again too ([DASHBOARD.md](DASHBOARD.md) section 11): the tests
require the files the two share to be byte-identical. `web/dist` does not depend on the
results. Until all of this is done, the tests fail. Plan for this: the API's answers carry
the ATLAS version, so a version bump changes every file.

## 4. The dashboard's static profile

**Reads** (`web/src/api/static.ts`). Every view still calls `api.models()`, `api.history()`
and the rest, so the request paths are built by the same code in both profiles. In the
static profile, a read:

1. looks up its exact request in the list compiled into the build
   (`__ATLAS_STATIC_INDEX__`, from the export manifest). A request with no recorded answer
   is refused (`not_exported`, "Not part of this static demonstration") and nothing is
   fetched.
2. fetches `<base>data/<file>` from the same origin: GET, no credentials, no redirects,
   revalidated (`cache: 'no-cache'`).
3. requires the file's exact size and SHA-256, checked with Web Crypto. A stale, damaged or
   substituted file is refused (`integrity`) and nothing of it is shown. The message suggests
   a reload, since a site part-way through an update can mix versions. A missing file is
   reported as missing (`static_file_missing`), with a retry.
4. passes the answer through the same checks as an API answer (`accept` in `client.ts`): a
   claim of a validated forecast or recommendation is refused, then the schema must match.

**No silent fallback.** A failed read is shown as an error state where the numbers would
be, with its reason. No cached or invented value ever stands in for it, as in the api
profile. A static build without its export list fails to start rather than fall back to HTTP.

**The deployment** is fixed in the build (`STATIC_DEPLOYMENT`): profile `static`,
simulations `disabled_static_site`, no operational detail. No server is asked, and a
descriptor that a server sends can never claim it (the served schema has no such value). The
top bar shows "Static demo · read-only" next to "Research software".

**Routing** (`web/src/router.tsx`). App paths stay `/`, `/forecasting` and so on; the router
puts them under the base path, in links and in history entries. A static host cannot
rewrite a request, so the build:

- writes each page as `<page>.html`, so GitHub Pages answers `/forecasting` with
  `forecasting.html` (status 200);
- writes a `404.html` that is the app itself. Any other address shows the dashboard's
  "Page not found" view, with links to every page, under a 404 status.

A page reached as `forecasting.html`, `index.html` or `forecasting/` is shown at its
canonical address (`history.replaceState`), so one page has one address. An address whose
rewritten form a URL would read as another path (a dot segment: `...html` would leave `..`,
which resolves out of the site) is left as it is and shows "Page not found". The back and
forward buttons, reloads, query strings (`?scenario=reduced_budget`, `?window=365`) and the
skip link all work, as in the api profile.

**The simulator.** A static host cannot run the Python M4 engine. The Simulator page
therefore:

- **says why**: "a static file host (such as GitHub Pages) serves it, with no ATLAS server
  behind it, so the Python M4 engine cannot run and no new simulation can be computed";
- **offers the committed reference scenarios to explore**: a "Reference scenario" chooser,
  kept in the address (`?scenario=`). For the chosen scenario it shows the recorded result:
  its assumptions, feasibility (DIAGNOSTIC when relaxed), plan, daily resources and backlog,
  policy comparison and evidence. These are the panels of the Decision optimisation page's
  scenario detail, except its table of HYPOTHETICAL parameters and the model's structural
  assumptions, which a link opens on that page (the form below lists the reference
  assumption of each adjustable input);
- **never presents a choice as a new optimisation**: the panel is titled "Explore the recorded
  reference scenarios", tagged "Recorded result" and HYPOTHETICAL. Its callout names the
  committed run and its result fingerprint, and says that choosing a scenario "does not run
  the optimisation";
- **keeps the form visible but read-only**, with its reference values, ranges and
  assumptions. The Run button is disabled, and the transport refuses a simulation even if
  the button is bypassed: no POST can leave a static build.

The full simulator is unchanged in the api profile and runs wherever ATLAS runs with its API.

**Wording.** Wherever the api profile says the server verified or served something, the
static profile says what happened instead:

- the footer and the Overview header: "a recorded answer of the ATLAS API … your browser
  checks each file … a static, archived copy: nothing on it is computed live";
- each provenance footer: verified at export, and checked by the browser against this build;
- the Methods and provenance page: the build, the data ("recorded from ATLAS 1.1.0"), a
  policy carried in the page, no server;
- the navigation's line under Simulator: "Simulations off here; explore recorded results".

The api profile's sentences are M7's, plus the two caveats of section 1 that both profiles
show; the static wording in its build is never shown (section 1).

## 5. GitHub Pages compatibility

| Requirement | How |
|---|---|
| Repository subpath | `base: '/demand-forecasting-model/'` in the pages mode (`ATLAS_PAGES_BASE`, checked: a path such as `/name/` or `/`) |
| Asset paths | Vite writes every script, style, font and icon URL under the base; content-hashed names |
| Static JSON | `<base>data/<file>`, absolute under the base, so any page depth works |
| Page routing, refresh, deep links | `<page>.html` per page (200); `404.html` is the app (404 for unknown addresses, and the right page even on a host without extensionless pages) |
| Browser navigation | History API under the base; canonical addresses; back and forward tested |
| Mobile | the M7 responsive layout; tested on a Pixel 7 profile and at 320 CSS px |
| Static-host 404 | the app's "Page not found", with links; status 404 |
| HTTPS | GitHub serves `*.github.io` over HTTPS. Web Crypto, which the integrity check needs, exists only in secure contexts: https, or this machine |
| Cache | GitHub sets the caching headers, so the build cannot. Assets have content-hashed names; data files are revalidated on first read and checked by SHA-256. A page left open across a publication reloads (the error says so) |
| No server-side rewrites | none needed (above) |
| No Python at run time | none: files only |

The local stand-in (`web/scripts/pages-server.mjs`) follows the same rules:

- the base path without its slash is redirected to it;
- a path is a file, else a directory (with its slash), else `<path>.html`, else `404.html`
  with status 404;
- only GET and HEAD are answered, no header of the site's own is sent, and `max-age=600`
  with an ETag;
- the path is read as the request sent it, before any URL parsing: outside the base it is
  not the site's (a plain 404); inside it, an encoded dot, slash, backslash or NUL (`%2E`,
  `%2F`, `%5C`, `%00`), a backslash or a dot segment is never a file (404.html). Browsers
  resolve dot segments and backslashes before they send a request, so only other clients
  send those. The stand-in is stricter than a host that decodes them;
- every file, 404.html included, is resolved through its links each time it is served and
  must stay in the build.

It is not GitHub Pages: the real behaviour is checked after activation (section 12, step 8).

## 6. Security and public information

**What the site holds.** Static files only:

- the dashboard's code, styles, font and licence notices;
- the 24 recorded answers and their manifest;
- `atlas-static-site.json`.

It holds no credential, key, token, cookie or storage. It makes no POST and needs no server,
and it shows no operational metadata. `verify-pages.mjs` refuses any file of a type a build
never holds (anything but HTML, JavaScript, CSS, JSON, text, SVG and the woff2 font) and a
font without the WOFF2 signature. A link or a directory where a file is listed is reported,
never followed; so is a directory the manifest does not account for, empty or not, and a
manifest that is not well formed. It then scans every file and directory name and every
text file, the site manifest included (`web/scripts/unsafe.mjs`, tested pattern by pattern in `build/unsafe.test.ts`;
`build/verifiers.test.ts` plants defects in copies of both builds), for:

- private keys; GitHub, AWS and Slack tokens; API key values; JSON web tokens;
- credential assignments, the keyword possibly with a suffix (`SECRET_KEY`,
  `client_secret_value`), with a quoted value or an unquoted one that looks like a secret
  (with a digit, or long and in both cases); `Authorization` headers, bearer or basic
  credentials, the API key header, and URLs with credentials (the user may be empty);
- `file:` URLs, local paths (Unix, a Windows drive with a backslash or a two-segment
  forward-slash path, network shares and CI workspaces), local and internal host names (any
  case, and `::1`), IPv4 and IPv6 addresses (compressed forms included, also at the end of a
  sentence; short hex runs such as `a::b` are not taken for one), and e-mail addresses;
- links to any site but the four the bundled code and licences name: `www.w3.org` (SVG and
  MathML namespaces), `react.dev` (React's error pages), `github.com` (the repository and the
  Inter font's source) and `scripts.sil.org` (the font's licence). A link is:
  - any scheme followed by two slashes (a regex literal after a property name, such as
    `{test:/\.ts$/}`, is not one);
  - http, https, ws, wss or ftp with backslashes, or with fewer slashes where a string or a
    link begins: from this https site `http:host` and `ws:host` reach that host, and so does
    any of them parsed without a base (the https forms, which a page of the site resolves
    to itself, are flagged too, to be safe);
  - a protocol-relative `//host` (or more slashes, backslashes, `\/\/` in a script, a user
    before the host) where a link, a source, a later `srcset` candidate, a CSS `url()`, an
    `@import`, a refresh `url=` or a string begins.

These are patterns: a check on mistakes, next to the review of what is committed, not a
proof that nothing else could be. The export repeats its own checks without the host
allow-list (section 3); the build's scan covers the exported files too, as `data/`. Both pass
on the committed build and export.

**Information published.** Only what the public repository already holds: the snapshot's
daily totals, as observed history, and the committed M2, M3 and M4 results, each with
repository-relative provenance. Publishing the site redistributes these derived NYC 311 data
on a public web page; section 9 explains why that needs the owner's decision first. The
HTML pages keep `noindex, nofollow` and `Referrer-Policy: no-referrer`, as `<meta>` elements.
Only HTML can carry them: M7's nginx sends `X-Robots-Tag: noindex, nofollow` on every
response, while the static site's JSON answers, its manifest and the licence texts carry no
robots directive, and a project site cannot publish a `robots.txt` (crawlers read it only at
the origin's root, which belongs to the account's user site). Search engines can therefore
index the recorded figures apart from the pages that show their limitations; each JSON
answer still carries its own integrity notices. The owner accepts this with
`public_static_profile_approved`.

**Differences from the M7/M8 nginx deployment.** GitHub Pages cannot send response headers of
the site's own. The static build therefore applies what a page can carry:

| Protection (nginx, M7/M8) | Static site | Consequence |
|---|---|---|
| `Content-Security-Policy` header (`DASHBOARD_CSP`) | the same policy in a `<meta http-equiv>`, before any script, **without `frame-ancestors`** (a meta policy cannot carry it) | same-origin scripts, styles, fonts, images and requests only; nothing inline, no eval; Trusted Types with no policy |
| `frame-ancestors 'none'`, `X-Frame-Options: DENY` | **none** | another site can frame the pages. They hold no session, form or action to hijack: risk low, recorded |
| `X-Content-Type-Options: nosniff` | GitHub's own headers (not ours; verify after activation) | — |
| `Referrer-Policy: no-referrer` | `<meta name="referrer">` | same |
| `X-Robots-Tag: noindex, nofollow` (every response) | `<meta name="robots">` in the HTML pages only | the JSON data, the manifest and the licence texts can be indexed (above) |
| `Permissions-Policy` (camera, microphone, …) | none | the pages use none of these features |
| `Cross-Origin-Opener-Policy: same-origin` | none (a `<meta>` cannot carry it) | a page on another site that opened the dashboard keeps a handle on its tab and can later send that tab to a look-alike page (tabnabbing). With COOP, nginx cuts that handle. The review demonstrated both locally. An origin of its own does not change this |
| `Cross-Origin-Resource-Policy` | none | other sites can load the files; they are public, and GitHub already allows any origin to read them |
| HSTS | GitHub's (on `github.io`) | — |
| Cache policy (`immutable` assets, `no-cache` pages) | GitHub's (about 10 minutes) | integrity is held by SHA-256, not by caching |
| Accounts, per-client rate limits, allow-lists | none | the site is public and read-only; GitHub's limits apply |

**Shared origin: an integrity risk, not only a privacy one.** Every GitHub Pages site of the
account `arthur-fay` is served from the same origin, `https://arthur-fay.github.io`: its
project sites, and a user site (`arthur-fay/arthur-fay.github.io`) at the root if one is ever
made. The browser treats them as one site. The dashboard holds nothing another page of that
origin could take (no storage, cookies or credentials), and it injects no HTML. But the
integrity of what it shows depends on every Pages site of the account, and on every script
those sites load:

- a page of another of those sites can open the dashboard (a new window or a frame) and
  rewrite what it shows, at the dashboard's own address, after every SHA-256 check has passed.
  The review demonstrated this on the local stand-in: a page beside the site changed the
  Uncertainty page's "FAILED_CALIBRATION" to another status. No policy a page can carry
  prevents it;
- a user site at the root can register a service worker for the whole origin, which can serve
  other files. This site never registers one. The static reader refuses to read anything while
  a service worker controls the page (an error explains why). That catches an accidental or
  stale worker, but not one written to deceive, which could change the code as well.

The origin can also move. If the account's user site has, or later gets, a custom domain,
GitHub serves every project site that has no custom domain of its own under that domain, at
`/demand-forecasting-model/`. The dashboard then shares that domain's origin, with whatever
it serves at its root, and its published address changes (the workflow's check follows the
address GitHub reports).

So publishing needs the owner to review the origin first (condition `shared_origin_reviewed`,
section 10). Either no other Pages site of the account exists, or none of them registers a
service worker or loads a script from another site and the owner accepts that they share the
origin; and the user site has no custom domain, or the owner reviews what that domain serves.
Better still, the site gets an origin of its own: a custom domain for this repository, or an
account or organisation used for nothing else.

**Another address.** The build and its committed record are for
`/demand-forecasting-model/`. A custom domain serving the site at its root needs the base
path `/`:

1. change `DEFAULT_PAGES_BASE` in `web/build/static-site.ts` (the Playwright configuration
   and the static e2e specs import it) and the `--base` default of
   `web/scripts/pages-server.mjs`; the tests that pin the default
   (`build/static-site.test.ts`) and the site (`tests/test_m9_pages_workflow.py`); and the
   gate's `SITE` (`.github/scripts/pages_gate.py`) with the record's `site`;
2. rebuild (`npm run build:pages`) and commit `web/atlas-static-site.json`.

`ATLAS_PAGES_BASE` alone builds for another base, for a local check, but never rewrites the
committed record, and `verify-pages` names the mismatch ("built for the base /, the committed
… for /demand-forecasting-model/"). The workflow builds for the default base.

**Dependencies.** The static build ships the packages the api build already ships, with the
same third-party licence notices. CI runs `npm audit --audit-level=high` on every build, and
the weekly audit covers the locks (M8).

**The M8 verdict on `public-demo`.** M8 gave a NO-GO for the nginx `public-demo` profile. That
profile exposes a live API server: rate limits, the threat surface of a server, no
computation. The static site has no server and no computation, so the M8 reasons do not
transfer as they stand. It is still a new public profile, so it needs its own explicit
approval: the condition `public_static_profile_approved` (section 10).

## 7. Scientific integrity on the static site

The static site serves the same answers, so every statement of M7's table
([DASHBOARD.md](DASHBOARD.md) section 6) is shown in the same place, from the same field.
Both the browser tests (`e2e/pages.spec.ts`) and the component tests of the static profile
(`src/m9-static.test.tsx`) check each one on the static pages; the Overview's pointer to the
erratum is also checked in the api profile (`src/views/m8-audit.test.tsx`):

| Conclusion | Where it is shown |
|---|---|
| M2's lead over the best baseline is not established | Overview status ("Not significant after Holm"); Forecasting tests panel (H1 "not statistically significant after the Holm adjustment") |
| The M8 statistical erratum | Forecasting tests panel, with the selection: the API's "M2 SELECTION EVIDENCE" notice ("a correctly sized test would most likely have kept …"); since M9, on the Overview next to the recommended model: "after the M8 size correction, a correctly sized selection test would most likely have kept the same-weekday median baseline: see the M2 SELECTION EVIDENCE notice in the Forecasting page's 'Is the model better than the baselines?' panel" |
| M3: FAILED_CALIBRATION | Overview status; Uncertainty page ("Interval status: FAILED_CALIBRATION"), every band and coverage chart |
| M4: HYPOTHETICAL, DIAGNOSTIC plans | Overview scenarios; Decisions callout; every scenario (the six relaxed plans tagged DIAGNOSTIC, from the recorded answers); the static simulator's recorded results |
| The forecast is historical and archived | "An archived projection, not a live forecast"; the API's "ARCHIVED PROJECTION" notice; the footer's "static, archived copy" |
| Research software, not an operational tool | the top bar's "Research software"; the footer; the Overview text |
| A recorded scenario is not a new optimisation | the static simulator's callout and labels; the navigation's "Simulations off here; explore recorded results" |

The export records these facts in `scientific_status`. Two tests require them there:
`tests/test_m9_static_export.py` and `src/api/static.test.ts`. A change to the results that
removed one would fail them.

## 8. Build, test and verify

```bash
uv run atlas static export && uv run atlas static verify   # the export (Python, once per result change)
cd web
npm run build:pages            # dist-pages/, and web/atlas-static-site.json (default base only)
npm run verify:pages           # every file as recorded; the manifest = git's committed copy; the export, the policy, the scan
npm run size:pages             # the size budgets, with the data
npm run preview:pages          # http://127.0.0.1:4180/demand-forecasting-model/ (the local Pages stand-in)
npm run test:e2e:pages         # the browser suite, on a stand-in of its own at port 4180 (stop preview:pages first; Chromium and a phone)
ATLAS_E2E_PAGES=1 ATLAS_E2E_ALL_BROWSERS=1 npx playwright test --project pages-firefox --project pages-webkit
npx vitest run build/verifiers.test.ts   # the verifiers on planted copies (verify-pages needs dist-pages/)
```

| Suite | What it checks |
|---|---|
| `tests/test_m9_static_export.py` | the committed export verifies and reproduces byte for byte; each file is the API's answer to its request, through the test client; the fixtures' bytes; coverage of the static pages' requests, and nothing operational; the history windows equal the Forecasting page's; the scientific status and every label; sources repository-relative and equal to the committed files; no unsafe text, and every unsafe pattern found; every refusal of `check_answer`; nothing written from damaged outputs or on a refused request; the environment ignored; replacing an earlier export, refusing other directories; every difference `verify` must find; the command; the policy equals `DASHBOARD_CSP` without `frame-ancestors`; the line-ending rule |
| `tests/test_m9_pages_workflow.py` | the command and the tests follow the committed record (phase A's is refused with every switch set; the owner's complete approval passes); each switch and each owner decision is required; unknown fields, impossible dates, dates before M9 or after today, notes that are not text and repeated keys refused; the workflow never publishes on its own, uploads the build straight after its verification, checks the downloaded artifact on the publication job's own runner before publishing it and every served file after, keeps a pull request's runs and each dispatch apart from main's pushes, has minimal permissions, changes no Pages setting and runs every static check; the check of the artifact (exactly the listed files and the manifest; every difference named: altered, missing, extra, a link, a path outside); the check of a published site (every differing file named; a cut-off answer fetched again; its deadline; https only) |
| Vitest: `src/api/static.test.ts`, `src/m9-static.test.tsx`, `src/router.test.ts`, `build/static-site.test.ts`, `build/unsafe.test.ts`, `build/verifiers.test.ts` | the static reader: the recorded answer after its SHA-256, refusals without a fetch, altered files refused, the API checks, a missing file, network, timeout, cancellation, no Web Crypto, a foreign service worker (and reads where none can control the page); no simulation ever sent; the export answers every request the pages can make; the views in the static profile (badge, wording, links under the base, every page's limitations from the recorded answers, the recorded scenarios, the disabled form, no POST, a missing file); router base paths, canonical forms (repeated slashes and suffixes, query and fragment kept, never fatal, never out of the base); the build plugin's checks of the export, the base path and the site manifest; every pattern of the scan, and none in the committed build and export; both verifiers on planted copies (unlisted files named `constructor` and the like, links, a false font, names and the manifest scanned, a build for another base) |
| Playwright `pages` (desktop Chromium) | every page from the files, read-only (GET only, never `/api/`), with no console error or CSP violation; deep links and reloads (200); canonical addresses, and none that leaves the site; the 404 page and its links; back and forward; the scientific limitations next to their results, the Overview's erratum pointer and the navigation's line included; the static simulator (nine recorded scenarios, DIAGNOSTIC kept, reload, no POST); every history window and period; a tampered file refused; a missing file reported; the licence notices under the base; axe WCAG 2.2 A/AA on every page in both themes with every table and disclosure open; keyboard (from the start of the page, the tab key reaches the skip link, the Simulator's navigation link and the scenario chooser, which Enter and the arrow keys then use); reflow at 320 px |
| Playwright `pages-mobile` (Chromium, Pixel 7) | the functional suite above, and the phone: menu, every page, no sideways scroll, axe |
| Playwright `pages-firefox`, `pages-webkit` (CI) | the functional suite (`pages.spec.ts`) and keyboard use (`pages-keyboard.spec.ts`); axe, reflow and the phone layout run in Chromium only |

The M0-M8 suites run as before. The api profile's build is rebuilt and checked byte for
byte, and its browser suite runs against it.

**CI.** `.github/workflows/pages.yml`, part A, runs on every pull request and push to `main`:

- the export check;
- the reproducible static build, which must equal the committed manifest;
- `verify-pages` and the size budgets;
- `npm audit`;
- the verifiers on planted copies (`build/verifiers.test.ts`);
- the browser suite: desktop Chromium (functional, axe, keyboard, reflow), a phone
  (Chromium), and Firefox and WebKit (functional and keyboard).

`ci.yml` keeps its jobs. Its pytest and Vitest runs include the M9 tests.

## 9. Licensing and data-use prerequisites

The M8 release gate left two owner questions open
([release/RELEASE_GATE.md](release/RELEASE_GATE.md) section 1, gate M), and the static site
does not resolve them. The first, the code licence, has since been answered: the owner chose
the MIT License for original ATLAS code (item 1). The second, the NYC Open Data terms, is
still open (item 2). Publishing the static site makes both more concrete: it puts the code and
the derived data on a public web page, in addition to the public repository.

1. **The project licence — chosen for original code, not yet publication approval.**
   The owner chose the [MIT License](../LICENSE), Copyright (c) 2026 arthur-fay,
   for original ATLAS source code the owner may license. This grant **does not cover**
   NYC 311 source data, derived historical City aggregates, or other owners' software
   and fonts. See the [README](../README.md) and
   [code/data terms notice](OPEN_SOURCE_AND_DATA_TERMS.md). Choosing the licence is not
   approval to publish: the `project_licence_decided` publication flag stays false until
   the owner records the decision in the publication record, in a reviewed pull request
   (sections 10 and 12).
2. **The NYC Open Data terms.** The site republishes daily totals derived from NYC Open Data,
   *311 Service Requests from 2020 to Present* (dataset `erm2-nwe9`): every observed history
   window, and every forecast and plan computed from them. The owner reads the current terms
   of use, which the City publishes for NYC Open Data, and any terms of the supplying agency
   for this dataset. The owner then confirms that redistributing these aggregates on a public
   site is permitted, and adds any attribution or disclaimer wording the terms require. The
   site's footer names the source ("NYC Open Data, 311 Service Requests (dataset erm2-nwe9)")
   and, since M9, says that ATLAS is "an independent research project, not affiliated with or
   endorsed by the City of New York or NYC Open Data". This is the substance of the data card's
   attribution ([DATA_CARD.md](DATA_CARD.md) section 1), not its exact words: the data card also
   names the City of New York as the publisher, and says "affiliated with, sponsored or
   endorsed by". If the terms prescribe wording, the footer must use it (`web/src/App.tsx`),
   and the site is rebuilt. The M8 audit could not retrieve the terms, because its network
   blocked nyc.gov. On 2026-10-19 the City's general NYC Open Data policies were reviewed:
   the portal's Terms of Use, the NYC Open Data FAQ and the public policies of the Open Data
   Technical Standards Manual ([OPEN_SOURCE_AND_DATA_TERMS.md](OPEN_SOURCE_AND_DATA_TERMS.md)).
   The FAQ and the Manual describe broadly permissive reuse, and the Manual adds that a
   republisher may have to identify the source, version and modifications. The portal's Terms
   of Use incorporate the NYC.gov terms and any additional terms of the agency that supplies a
   dataset. These general policies are not the terms of this dataset: the governing NYC.gov
   terms, the dataset's current metadata and any terms of the supplying agency for `erm2-nwe9`
   have not been verified, so this condition is still open, and
   `nyc_open_data_terms_confirmed` stays false.
3. **Third-party notices.** Already met: the build ships the licences of the bundled code and
   of the Inter font, linked from every page (M8). They stay valid for the static build.

The root [MIT License](../LICENSE), Copyright (c) 2026 arthur-fay, is the only licence the
project grants, and it covers only original ATLAS code the owner may license. The project
grants no licence or permission for the NYC 311 data, the aggregates derived from them or
third-party works: the data keep the terms of the City and of the supplying agency (item 2),
and the bundled code and the Inter font keep their own licences, whose notices the build
ships (item 3). Until the licence decision is recorded (item 1) and the data terms are
confirmed (item 2), the site must not be published. The publication record has a condition
for each (section 10).

## 10. Publication controls

Publication is part B of `.github/workflows/pages.yml`. It runs only when all of these hold:

1. **A manual dispatch**: Actions > "Static demo (GitHub Pages, M9)" > Run workflow, with
   `publish` ticked. No push, pull request, schedule or other event can start part B.
2. **On `main`**, with the **confirmation phrase** typed: `publish the ATLAS static demo`.
3. **The repository variable `ATLAS_PAGES_PUBLICATION` is `approved`.** It is a setting only a
   repository administrator can change, outside the code.
4. **The reviewed record [`docs/release/PAGES_PUBLICATION.json`](release/PAGES_PUBLICATION.json)
   states every decision**, checked by `.github/scripts/pages_gate.py`:
   - `project_licence_decided`, `nyc_open_data_terms_confirmed`: section 9;
   - `release_version_decided`, with `release_version` equal to the code's version: section 11;
   - `limitations_accepted`: [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md), on a public site;
   - `public_static_profile_approved`: this profile, distinct from M8's `public-demo`;
   - `shared_origin_reviewed`: the origin the site shares with the account's other Pages sites
     (section 6);
   - `pages_settings_configured`: Pages uses "GitHub Actions" as its source, and the
     `github-pages` environment allows `main` only and requires the owner's review
     (section 12, steps 2 and 3);
   - `publication_approved`;
   - with `decided_by`, `decided_on` and `approval_reference` (where the approval is
     recorded, for example the pull request).

   The record is read strictly, so what a reviewer reads is what counts: a key given twice,
   an unknown field, notes that are not plain text, and a `decided_on` that is not a real
   date between 2026-10-18 (M9 phase A) and the day of the run (UTC) are refused. **M9 phase
   A commits the record with every condition false. No run can publish.** That rests on the
   committed record, which the gate refuses even with every switch set, not on a test: the
   tests follow the record, so the owner's complete approval passes CI.
5. **GitHub's `github-pages` environment rules: required.** Required reviewers and `main`
   only (section 12, step 2). The gate's checks run in this repository's workflow, so anyone
   with write access could dispatch a changed workflow from another branch. The environment's
   rules are enforced by GitHub itself, whatever the workflow says, and are what stops that.
   Fork pull requests cannot publish at all: they get a read-only token and no OIDC token.
6. **Pages enabled with "GitHub Actions" as its source.** Until the owner sets this,
   publication fails. The workflow never changes a Pages setting.

The artifact that is published is the one part A built and verified in the same run:

- the reproduced export;
- the build whose manifest equals the committed one, verified file by file and scanned;
- uploaded straight after that verification, before anything else runs on the runner (the
  browsers' installation, the tests);
- then tested in four browser projects. If any test fails, nothing is published.

It is uploaded only for a publication and kept one day. The one job that holds
`pages: write` and `id-token: write` publishes it, in three steps:

1. on its own runner, which runs no npm code, it downloads the artifact and requires it to be
   the committed build exactly (`.github/scripts/pages_check_artifact.py`): the files the
   committed manifest lists, each with its size and SHA-256, and the manifest itself, and
   nothing else (no other file, no link, no path outside the archive, nothing after the
   archive's end). The build job's runner ran the dependencies' install scripts, so this
   check does not trust it;
2. deploy-pages publishes that artifact;
3. it fetches every file of the committed manifest from the published site and requires its
   size and SHA-256 (`.github/scripts/pages_check_site.py`). The check is repeated while the
   publication reaches every edge, a cut-off answer is fetched again, each fetch has
   10 seconds in all, and the whole check stops after 8 minutes, naming every file that
   differs.

No push displaces a publication: a pull request's runs have a group of their own, and so
does each dispatch, so no push can replace or cancel a publication that waits for its
review. Publications themselves run one at a time, and a later dispatch's publication
replaces one still pending in that queue.

## 11. Release and versioning

**The 1.0.0-rc.1 candidate is unchanged.** Its manifest
([release/atlas-1.0.0-rc.1.json](release/atlas-1.0.0-rc.1.json)) identifies the M8 content: tree
fingerprint `35b5b79f7683fdde3564130c91be6b8105fabb0e8e09009352a1450961f32a16` over 441 files.
The M8 merge commit `7d3639c` has exactly that tree (the same as the approved head `ccb93ea`),
and `python3 docs/release/rc_manifest.py --check` passes on a clean checkout of it. M9 does
not edit the manifest or the release-candidate documents (`RELEASE_GATE.md`,
`RELEASE_CANDIDATE.md`). On an M9 checkout, `--check` reports the M9 changes, as it should:
M9 is not rc.1.

**M9 needs a new version.** It changes tracked content in four places:

- the Python package: a new module and command;
- the dashboard's sources and its committed build;
- the tests and the CI;
- the documentation.

Under the M8 rule ("any change to the candidate's content makes a new candidate"), M9 is not
part of rc.1. It adds a deployment profile and a frontend adapter, and changes no API
contract, research result, snapshot or legacy artifact.

The recommendation:

1. Keep `v1.0.0-rc.1` for the M8 content: if approved, tag it on `7d3639c`, as the M8 gate
   describes, independently of M9.
2. Release M9 as **1.1.0**, a minor version: a new, backward-compatible capability. Give it
   its own candidate, `v1.1.0-rc.1`, with its own manifest (`rc_manifest.py --write`
   derives `atlas-1.1.0-rc.1.json` from the version).
3. The version bump is the release preparation's first commit
   ([DEVELOPMENT.md](DEVELOPMENT.md) "Versioning"). It changes every recorded answer, because
   they carry the version, so it is followed by `atlas static export` and both builds.

M9 phase A **does not bump the version**: the version is the owner's decision. Until then the
code still says 1.0.0. The publication gate turns this into an explicit choice: the record's
`release_version` must equal the code's version, so publishing means bumping the code first
(to 1.1.0, as recommended) or recording 1.0.0 for content that is not rc.1. The repository's
tooling supports only the first: `rc_manifest.py` names its candidate `rc.1`, and on M9
content at 1.0.0 its `--write` would overwrite the M8 manifest
(`atlas-1.0.0-rc.1.json`). A `1.0.0-rc.2` would first need that tool to take a candidate
name and to refuse to overwrite another candidate's manifest. Either way, it never happens
silently.

## 12. Owner instructions: activating GitHub Pages

Do these steps only after the decisions of sections 9 and 11, and only when you decide to
publish. Steps 2 and 3 publish nothing by themselves. Each step is reversible (step 9).

1. **Decide.** The NYC Open Data terms (section 9; the code licence is already chosen, and
   step 4 records it), the version (section 11), the limitations
   ([KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md)), the shared origin (section 6), and the
   static profile itself. If the version changes, bump it first
   ([DEVELOPMENT.md](DEVELOPMENT.md) "Versioning": export again, rebuild both profiles) and
   merge that.
2. **Protect the environment first.** Settings > Environments > New environment, named
   `github-pages` (GitHub would otherwise create it, without rules, once Pages uses Actions).
   Then:
   - "Deployment branches and tags": **Selected branches and tags**, add `main`;
   - "Required reviewers": add yourself.
3. **Enable Pages with Actions as its source.** Settings > Pages > Build and deployment >
   Source: **GitHub Actions**. Do not choose "Deploy from a branch". Leave "Custom domain"
   empty: the build's base path assumes the project address (section 6, "Another address").
   Pages for this repository is public whatever the repository's visibility, unless the
   account's plan offers private Pages.
4. **Record the decisions.** On a branch, edit `docs/release/PAGES_PUBLICATION.json`:
   - set each condition to `true` only if it holds (`pages_settings_configured` now does);
   - set `release_version` to the code's version;
   - set `decided_by`, `decided_on` (YYYY-MM-DD: the day of the decision, not before
     2026-10-18 and not after today's date in UTC, which the gate and CI check) and
     `approval_reference`;
   - rewrite `notes`, which still says that nothing is decided.

   Open a pull request and merge it once CI is green. The tests follow the record: one that
   sets every condition must also be complete and pass the gate, and any other is refused.
5. **Set the switch.** Settings > Secrets and variables > Actions > Variables > New repository
   variable: name `ATLAS_PAGES_PUBLICATION`, value `approved`.
6. **Publish.** Actions > "Static demo (GitHub Pages, M9)" > Run workflow:
   - branch `main`;
   - tick `publish`;
   - type `publish the ATLAS static demo`;
   - Run.

   Part A builds and tests; the gate checks; approve the `github-pages` deployment when
   GitHub asks, **within 24 hours**: the build to publish is kept one day. After that the
   publication fails for want of its artifact; dispatch again (it rebuilds and retests).
7. **Check the run.** The step "The artifact is the committed build" runs before anything is
   published, and the last step, "The published site serves every file of the committed
   build, byte for byte", after. If the last one fails, the site is already live: unpublish it
   (step 9), then read the files the step names.
8. **Check the site** (the things only the real host can show):
   - `https://arthur-fay.github.io/demand-forecasting-model/` loads, with "Static demo ·
     read-only", and no error in the browser console;
   - `.../forecasting` and the other pages open directly, and a reload keeps them. In the
     developer tools' network tab, their status is 200 (or 404, if GitHub does not serve
     extensionless pages: the page still shows, and the documentation should then say so);
   - `.../no-such-page` shows "Page not found", with links;
   - the response headers of a page: record what GitHub sends (`content-security-policy` is
     absent: the policy is in the page; `x-content-type-options`, `cache-control`,
     `strict-transport-security`);
   - the site on a phone.
9. **To unpublish:**
   - Settings > Pages: unpublish the site (GitHub offers this on the Pages settings page of a
     published site), or disable Pages;
   - Settings > Secrets and variables > Actions > Variables: delete `ATLAS_PAGES_PUBLICATION`,
     or set it to anything but `approved`, so that no dispatch can publish again;
   - in a pull request, set the record's `publication_approved` back to `false`.
10. **To update the site later:**
    - a pull request that exports again and rebuilds (CI requires both). If it bumps the
      version, the same pull request updates the record's `release_version` (the gate
      refuses a record for another version) and, when the approval is renewed for the new
      content, `decided_on` and `approval_reference`;
    - merge it;
    - dispatch the workflow again, as in step 6.

## 13. Limitations and what remains unverified

- **The real GitHub Pages has not been exercised**: it is not enabled, and enabling it is not
  authorised. Three behaviours are checked only against the local stand-in: extensionless
  pages, the 404 page, and GitHub's headers and caching. Step 8 of section 12 checks them on
  the real host.
- **No framing or opener protection**: a static host cannot send `frame-ancestors`,
  `X-Frame-Options` or `Cross-Origin-Opener-Policy`. Another site can frame the pages, and a
  site that opened the dashboard can later send its tab to a look-alike page (section 6).
- **No robots directive outside the HTML pages**: the JSON answers and the licence texts can
  be indexed apart from the pages that qualify them (section 6).
- **The shared origin can move**: a custom domain on the account's user site would move the
  dashboard under that domain (section 6).
- **Shared origin** with the owner's other GitHub Pages sites. What the site shows is only as
  trustworthy as every one of them and every script they load: a page of the same origin can
  rewrite the dashboard after its checks (section 6). The owner reviews this before
  publication (`shared_origin_reviewed`); an origin of its own removes it.
- **No live simulation**, by design: the recorded reference scenarios only.
- **A page open across a publication** may refuse a data file whose SHA-256 changed. It says
  so, and a reload loads the new version. Nothing wrong is shown.
- **Browsers.** Desktop Chromium (every check), a Pixel 7 profile in Chromium (the phone
  layout), and Playwright's Firefox and WebKit (in CI: the functional suite and keyboard
  use) are tested. Axe and reflow run in Chromium only. Real Safari, real phones and screen
  readers are not tested, as in M8.
- **The scans are patterns** (sections 3 and 6): they catch the mistakes they describe, not
  every possible secret or link.
- **The static site inherits every limitation of the results** it shows
  ([KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md)). It shows a frozen snapshot that ends
  2026-06-30.
- **The NYC Open Data terms are unconfirmed**, and the code licence the owner chose (MIT,
  original ATLAS code only) is not yet recorded in the publication record (section 9).

## 14. Measured results

Measured on the development container (Chromium 141, Node.js 22.22, Python 3.12) on
2026-10-18, on the head that corrects the third check of the review (section 15). Firefox
and WebKit run in CI only. GitHub's checks on the pull request show CI's runs of each head,
and its description records those of its final head.

| Check | Result |
|---|---|
| `atlas static export` | 24 answers, 1.07 MB (1.09 MB with the manifest); deterministic (two exports byte-identical) |
| `atlas static verify` | PASS (files, reproduction) |
| Static build | reproducible (`atlas-static-site.json` identical across two builds, and to the committed one); `verify-pages` PASS: 49 files under `/demand-forecasting-model/`, 25 data files, no credential, local detail or foreign link |
| api build (`web/dist`) | reproducible; `verify:dist` PASS; the static transport, reader and recorded-scenario panel are not in it (section 1 lists the static wording that is) |
| Size, static build (gzip: Node's zlib, level 9, `npm run size:pages`) | initial JavaScript 88.0 KB (budget 110); initial HTML, CSS and JS 94.5 KB (125); largest lazy chunk 20.8 KB (30); font 48.3 KB (50); build without data 543.8 KB raw (1,000); largest data file 17.8 KB (30); all data 181.7 KB (300) |
| Data a page reads on a first visit (gzip level 9, Python's `gzip`) | Overview 12.5 KB; Forecasting 35.9 KB; Uncertainty 10.0 KB; Decisions 15.6 KB; Simulator 15.0 KB; Provenance 32.3 KB. The whole history (9.1 KB) only with "All"; one scenario (about 9 KB) at a time |

| Suite | Result |
|---|---|
| pytest, the whole suite (M0 to M9, with coverage) | 3105 passed, 103 skipped (nginx and an opt-in study), coverage 96.05 %: the last complete local run, on `21ee567`; this head adds six M9 tests, counted above, and CI runs the whole suite on it |
| pytest, M9: `tests/test_m9_static_export.py`, `tests/test_m9_pages_workflow.py` | 71 and 50 passed |
| Vitest, the whole suite (25 files) | 316 passed; coverage 95.2 % statements, 84.3 % branches, 94.0 % functions, 96.6 % lines (thresholds 85, 75, 85, 85; the first two vary by a few tenths between runs, with the order in which the Forecasting view's answers arrive) |
| Vitest, M9 (the six files of section 8) | 123 passed |
| Playwright, static: `pages` (desktop Chromium) and `pages-mobile` | 49 passed, 1 skipped: the back and forward buttons on the phone (`pages-mobile.spec.ts` goes back through the menu; forward is tested on desktop) |
| Playwright, api profile (`ATLAS_E2E_API_ONLY=1`) | 56 passed |
| Playwright, `pages-firefox` and `pages-webkit` | CI only: 15 and 15 passed on the previous head (`21ee567`), and passed again on each later head |

## 15. Independent review

The adversarial review of M9 phase A, its findings and their corrections are recorded in
[audit/M9_PHASE_A_REVIEW.md](audit/M9_PHASE_A_REVIEW.md).
