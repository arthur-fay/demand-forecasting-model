# Interactive web dashboard (M7)

The dashboard is the human interface to ATLAS. It shows the committed research outputs of
M2 (forecasting), M3 (uncertainty) and M4 (decision simulation) and runs bounded
HYPOTHETICAL simulations, all through the M5 API. It is a static single-page application in
`web/` (React, TypeScript), built once into the committed `web/dist/` and served next to
the API from the same origin: by the API itself on a workstation or through an SSH tunnel,
and by nginx in the private and public-demo deployments. The server needs no Node.js.

> **Status: implemented on `feature/atlas-m7-dashboard`, awaiting review.** Nothing has been
> deployed to any server or domain.
>
> **What the dashboard shows** is research output, labelled as such next to every number:
> - The forecast is an archived, unvalidated point forecast (M2). The model's lead over the
>   best baseline is **not statistically significant after the Holm adjustment**.
> - The M3 prediction intervals have the status **FAILED_CALIBRATION**.
> - Every M4 operational parameter is **HYPOTHETICAL**. There is no real NYC staffing or cost
>   data, and no result is a validated staffing, budget or service-capacity recommendation.
> - Simulations are exploratory: they show how the M4 model responds to assumptions.

Contents:

1. Architecture
2. Technology decisions and dependencies
3. Screens and workflows
4. API integration
5. The simulator
6. Scientific integrity in the interface
7. Security
8. Accessibility
9. Responsive design and mobile
10. Performance (measured)
11. Development, build and checks
12. Deployment, upgrade and rollback
13. Testing
14. Limitations
15. Risks for M8
16. Independent review

## 1. Architecture

```
 browser (desktop, phone)
   │  same origin: pages, files and /api/v1 share one scheme, host and port
   │
   ├── workstation / SSH tunnel ──► atlas api serve (127.0.0.1:8000)
   │                                 ├─ DashboardMiddleware: the verified build, from memory
   │                                 │    pages ─► index.html · /assets/<name>-<hash>.<ext>
   │                                 │    /atlas-deployment.json (profile "local")
   │                                 └─ the M5 API, unchanged (/api/v1, /health)
   │
   └── private / public-demo ─────► nginx (TLS, accounts, per-client limits)
                                     ├─ dashboard locations: exact names from the build
                                     │    root /opt/atlas/dashboard/<release id> (a verified copy)
                                     │    /atlas-deployment.json (written into the site)
                                     └─ API locations (M6, unchanged) ─► unix socket ─► API
```

`web/` holds the source; `web/dist/` holds the committed build and its manifest
`atlas-dashboard.json` (format `atlas-dashboard/1`): the version, the entry `index.html`,
the client routes (`web/src/pages.ts`), and every file with its SHA-256 and size. CI
rebuilds `dist/` from the locked toolchain and fails if a single byte differs, so the
committed build is exactly what the source produces.

Python reads that manifest in one place, `atlas.dashboard` (standard library only):

- `atlas.api.dashboard` loads the build at startup, verifies every file against the manifest
  and serves it from memory by exact path. A damaged build is logged
  (`dashboard_unavailable`) and not served; the API keeps working.
- `atlas.deploy.routes` turns the manifest into nginx locations (the *static route class*):
  each page and file by its exact name, nothing else.
- `atlas.deploy.publish` copies the release's build to `/opt/atlas/dashboard/<release id>/`
  (owner root, group www-data, 0750/0640), verifies the copy, and renames it into place.
- `atlas.deploy.manifest` records the build in `release.json`; `atlas.deploy.verify` checks
  after every activation that the API serves it and that nginx's copy is exactly the
  recorded files.

Inside the application:

| Layer | Modules | Role |
|---|---|---|
| Pages | `pages.ts`, `router.tsx`, `App.tsx` | the six pages (one source of truth for navigation, titles and server routes), a small history router, lazy-loaded views, an error boundary per page |
| API | `api/openapi.gen.ts`, `api/schemas.ts`, `api/client.ts`, `api/endpoints.ts`, `api/resource.ts` | types generated from the contract, runtime schemas checked against them at compile time, the fetch client with typed errors, one function per operation, a small shared cache |
| Deployment | `deployment.tsx` | what this deployment allows, from `/atlas-deployment.json`; fail-closed |
| Views | `views/*.tsx`, `views/data.ts`, `views/series.ts`, `views/plan.tsx`, `views/simulatorForm.ts` | each page; selection and reshaping of API data for charts and tables (no recomputation) |
| Charts | `charts/*.tsx` | SVG line, band, bar, stacked-column and range charts on d3-scale/d3-shape, each with a table |
| Components | `components/*.tsx` | callouts, tags, panels, provenance footers, loading and error states, tables, controls |
| Styles | `styles/*.css` | design tokens (light and dark), layout, components, charts |

## 2. Technology decisions and dependencies

| Choice | Why | Rejected |
|---|---|---|
| React 19 + TypeScript (strict, `noUncheckedIndexedAccess`) | asked for by the brief; typed end to end from the contract | — |
| Vite 8, static build | one `dist/` of hashed files, no server runtime; the production server needs no Node.js | server-side rendering (needs a Node.js process) |
| Charts drawn as SVG by the dashboard, with **d3-scale** and **d3-shape** only | scales and path generators, no DOM manipulation; every mark, label, legend and keyboard behaviour is ours, so each chart can be a keyboard-operable slider with a data table | Recharts, Chart.js, ECharts, Plotly: 50-1000 KB, canvas or opaque SVG that a screen reader cannot use, and their own tooltips and colours |
| **zod 4** (`zod/mini`, `jitless`) | runtime validation of every response against the contract; the mini build tree-shakes to what is used; `jitless` because zod would otherwise try to compile validators with `new Function`, which the CSP forbids | hand-written guards (drift), ajv (compiles with `new Function`) |
| **@fontsource-variable/inter** | the Inter variable font self-hosted (one Latin woff2): no request to a font CDN, so `font-src 'self'` holds | system fonts only (inconsistent figures across platforms: tabular numerals matter in tables) |
| No router, state or UI library | six pages and one form: a 100-line router and `useSyncExternalStore` are enough | React Router, Redux/TanStack Query, component kits |
| openapi-typescript (development only) | generates `openapi.gen.ts` from `docs/api/openapi.json`; CI regenerates it and fails on a difference | a generated client (adds runtime code; ours is 270 lines) |

Runtime dependencies (what reaches the browser): `react`, `react-dom`, `zod`, `d3-scale`
(and its d3 dependencies), `d3-shape`, `@fontsource-variable/inter`. Development tools:
Vite, Vitest, Testing Library, jsdom, ESLint (typescript-eslint, react-hooks, jsx-a11y),
Prettier, Playwright with axe-core, openapi-typescript.

**Versions and supply chain.** `web/package.json` pins exact versions (`.npmrc`:
`save-exact`) and `package-lock.json` records every package with its integrity hash; CI
installs with `npm ci` and runs `npm audit --audit-level=high` (0 vulnerabilities at the
time of writing). The project's two-week release-age cooldown (M4, `pyproject.toml`) was
applied to the npm packages as well: every one of the 426 locked packages was published
before 1 October 2026, at least 14 days before this milestone (checked against the
registry's publish times). npm has no setting that enforces this, so it is a review rule
for future updates.

## 3. Screens and workflows

Every page has a header naming its milestone, the warnings that qualify its numbers placed
next to them, and a provenance footer per panel (the source files, their fingerprints and
the API endpoint the numbers came from). Each chart has a "Table" view with the same values.

| Page | Path | What it shows | API |
|---|---|---|---|
| Overview | `/` | the scientific status of M2, M3 and M4 in one row; the data snapshot (dates, days, fingerprint); the 30-day archived projection summary; the last 180 observed days joined to the projection; the reference scenarios with feasibility; the API's own warnings and limitations | `system`, `models`, `forecasts`, `forecasts/history`, `uncertainty/status`, `scenarios` |
| Forecasting | `/forecasting` | the full observed history and the archived projection (federal holidays marked); backtest accuracy by horizon; models against baselines (MAE); the pre-registered confirmation tests with their Holm-adjusted p-values; the protocol's periods and run identity | `forecasts`, `forecasts/history`, `forecasts/metrics`, `models` |
| Uncertainty & risk | `/uncertainty` | the interval status (FAILED_CALIBRATION) first; the projection with its uncalibrated quantile bands (holiday-related days marked); observed against nominal coverage with confidence intervals and the tolerance; quantile reliability; threshold-exceedance statuses | `uncertainty/status`, `uncertainty/intervals`, `forecasts/history` |
| Decision optimisation | `/decisions` | the reference scenarios compared (DIAGNOSTIC plans shown as such, never subtracted from feasible ones); which scenarios are feasible and why not; one scenario in detail (daily resources, backlog, policy comparison) with its HYPOTHETICAL assumptions | `scenarios`, `scenarios/comparison`, `scenarios/{id}` |
| Simulator | `/simulator` | a form built from the API's parameter ranges; one bounded M4 solve on the server; the result with its plan, the inputs as resolved, the limitations and assumptions | `simulations/parameters`, `simulations` |
| Methods & provenance | `/provenance` | service versions, components and limits (private deployments only); every committed source with its fingerprints; the dashboard build and deployment; what each milestone did | `system`, every view's provenance |

Unknown paths show a "not found" page listing the pages; a page whose code fails shows an
error with a reload button instead of a blank screen; the navigation names the deployment
(`local`, `private`, `public demo`) and whether simulations are available.

**Workflows.** Reading the status (Overview); checking whether the model is better than its
baselines and by how much (Forecasting → confirmation tests); understanding why the intervals
are not decision-grade (Uncertainty → coverage); comparing scenarios and opening one
(Decisions, `?scenario=<id>` keeps the choice in the URL); running a what-if (Simulator);
tracing a number to its source (each panel's provenance footer, then Methods & provenance).

## 4. API integration

- **The contract is the source of truth.** `web/src/api/openapi.gen.ts` is generated from
  `docs/api/openapi.json` (`npm run gen:api`; CI fails if it is stale). The runtime schemas in
  `schemas.ts` are checked against the generated types at compile time
  (`Conforms<Generated, Schema>`): a contract change that the schemas do not follow fails
  `tsc`.
- **Every response is validated** before anything renders. A 2xx answer that does not match
  is refused (`contract`): nothing of it is shown, the error names up to five fields.
- **Integrity flags are enforced.** A response whose `integrity` claims
  `validated_recommendation: true` or `point_forecast_validated: true` is refused
  (`integrity`) before validation, whatever else it contains.
- **Errors are typed and shown** where the data would be: `network`, `timeout` (20 s for
  reads, 150 s for a simulation), `http` (with the API's code, message, request id and
  `Retry-After`), `contract`, `integrity`; `aborted` (navigation) is never shown. Each error
  state says what happened, offers a retry when a retry can help, and shows the request id
  for the server log. **No error is ever replaced by other numbers**, cached or invented.
- **Slow answers** show a loading state with the reason after 2.5 s ("still waiting for the
  server"); a page never waits for unrelated data: each panel loads on its own.
- **No backend math is duplicated.** The views select, join by date (observed and projected
  series stay separate), sort and format; every metric, interval, cost, feasibility status
  and difference shown is a field of an API answer. Rounding is for display only.
- **One cache.** `resource.ts` shares answers between pages for the session (the committed
  outputs do not change while a release runs); a retry refetches.
- **The fixtures are the API.** The component tests render the real recorded answers in
  `web/src/test/fixtures/`, and `tests/test_m7_fixtures.py` asks the API for each of them and
  requires the same bytes: the frontend tests cannot drift from what the API serves.

## 5. The simulator

The form is built from `GET /api/v1/simulations/parameters`: every input with its default,
range, unit, assumption and whether it can be removed ("no limit"). Every input is a text
field, so exact values can be entered on a phone and with assistive technology; bounded
inputs also have a slider for pointer users (the text field is the keyboard control).

- **Validation as you type** against the published ranges (numbers only, integers where the
  API requires them, the money and penalty rule "0 or at least 0.01"). Errors are attached
  to their fields (`aria-invalid`, `aria-describedby`) and summarised above the button with
  links to each field. The API validates everything again (M4's own parser and cross-field
  rules): its field errors are mapped back onto the same fields.
- **Only changed fields are sent**; everything else takes the reference default, as the API
  defines. The request sent is shown with the result.
- **Running**: the button shows the elapsed time and a Cancel button aborts the request;
  leaving the page cancels it too. A second run is not possible while one runs.
- **Errors**: `simulations_busy` (no queue: retry), `simulation_timeout` (the server stopped
  the solve at its deadline), a client timeout (no answer in 150 s), `simulation_rejected`
  (M4 refused the inputs as stated), `simulation_failed` (no certified result, so no plan),
  `rate_limited` (the proxy's per-client limit), 401 (a key is required: the dashboard never
  holds one). The previous result, if any, stays visible and is marked as not reflecting the
  last attempt.
- **The result**: feasibility, the plan and its daily values, the API's `label` (HYPOTHETICAL,
  exploratory), the demand stress-path notice when M3 quantiles are used, the limitations,
  every resolved input with its source (your value, reference, removed), the structural
  assumptions, integrity and provenance. The result heading takes the focus.
- **Disabled, never hidden.** When this deployment cannot run simulations the form stays
  visible and read-only with the reason in a callout: in public-demo ("computation is not
  exposed"), when the API requires a key (the tunnel profile: "a browser must never hold
  one"), and when the deployment descriptor cannot be read (fail-closed).

## 6. Scientific integrity in the interface

| Statement | Where it appears | Source |
|---|---|---|
| M2: not significant after Holm | Overview status (tag "Not significant after Holm"); Forecasting confirmation tests (each test's adjusted p and "No"); every panel serving M2 numbers via its notices | `models`, `forecasts/metrics` (`h1`, tests), `integrity.notices` |
| M3: FAILED_CALIBRATION | Overview status; Uncertainty header callout (critical); next to every band and coverage chart; holiday-related days marked | `uncertainty/status`, `integrity.uncertainty_interval_status` |
| M4: HYPOTHETICAL, not a recommendation | Overview scenario panel; Decisions page header and every scenario; each simulation result; every parameter row | `integrity.parameter_provenance`, `label`, `assumption` fields |
| No real NYC staffing or cost data | Decisions and Simulator callouts; the parameters' assumptions | API `notes`, `limitations` |
| Archived forecasts are not live | "Archived projection" tags on every projected series with its issue date; the projection never reads as "today" | `data_status: projected`, `forecast_label` |
| Simulations are exploratory | Simulator page callout and each result | `label`, `limitations` |
| DIAGNOSTIC plans | scenario comparison and plan panels: values of plans that miss the terminal-backlog target are labelled DIAGNOSTIC and never compared as plan differences | `values_status`, `feasibility` |
| Provenance | a footer on every panel; the Methods & provenance page | `provenance` |

The text of every warning is the API's where the API provides one; the dashboard's own
wording follows the M4 wording rules (no "optimal staffing", "recommended", "predicts").
Status colours are never alone: each comes with an icon and a label.

## 7. Security

**No credentials in the browser.** The dashboard holds no key, token or password: in the
private profile the browser's own HTTP Basic session covers every request (same origin), and
nginx adds the simulation key itself. `structure.test.ts` fails if the source uses storage or
cookies, `eval`, `new Function`, HTML injection or another origin; `test_m7_dashboard.py`
fails if the build sets a credential header; the browser tests check that no request carries
a key.

**Content-Security-Policy for HTML** (`atlas.dashboard.DASHBOARD_CSP`, sent by both the API and
nginx; JSON keeps the API's `default-src 'none'`):

```
default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self';
connect-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none';
frame-ancestors 'none'; object-src 'none'; require-trusted-types-for 'script';
trusted-types 'none'
```

No inline script or style (colours are set through the CSSOM, which CSP allows), no
third-party origin, no `eval`, and Trusted Types with no policy, so no string can ever reach
an HTML sink. The browser tests record every CSP violation report and require none. Other
headers: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy:
no-referrer`, `Cross-Origin-Opener-Policy` and `Cross-Origin-Resource-Policy: same-origin`, a
`Permissions-Policy` denying every powerful feature, and HSTS through nginx.

**The static route class.** Neither server maps a URL onto the file system:

- The API answers only the exact paths in the verified manifest, from memory.
- nginx gets one location per class, generated from the manifest:
  - the pages (one exact-match regex) answer `index.html` through `try_files`;
  - the assets (one regex of their exact names) and the favicon are served from the
    published copy with `disable_symlinks on`;
  - the descriptor goes through the same authentication, then a named location.
  - Every one is GET/HEAD only, behind the same accounts and allow-list as the API, with its
    own per-client zone (`atlas_static`, 20 requests/s, burst 60) so that a first load or
    several tabs never use up the API's limit. nginx counts requests before authentication,
    so a client without an account can try passwords at most twice as fast here as on the
    API (accounts have 192-bit or 20-character passwords).
  - The descriptor is JSON and carries the API's headers (`default-src 'none'`, `no-store`);
    hashed files are cached as `max-age=31536000, immutable` (not `public`: they are behind
    accounts, so shared caches must not keep them).
- A planted file, a link, an encoded or dotted path, a trailing slash, another case: all 404
  or 400 (tested against real nginx).
- Page paths can never shadow the API: `atlas.deploy.routes` refuses a page under `/api`,
  `/health`, `/docs` or `/openapi.json`.

**Profiles stay distinct** (`/atlas-deployment.json`, `atlas.dashboard.descriptor`):

| Profile | Served by | Descriptor | Simulations in the browser | Operational detail |
|---|---|---|---|---|
| local (`atlas api serve`, no key) | the API | `local`, `enabled` | yes | yes |
| local with a key, and the tunnel profile | the API | `local`, `disabled_api_key` | no (a browser must not hold the key) | yes |
| private | nginx, behind accounts | `private`, `enabled` | yes (nginx adds the key) | yes |
| public-demo | nginx | `public-demo`, `disabled_public_demo` | no (the route does not exist) | no |

The descriptor only *informs* the interface: the server decides. In public-demo the
simulation route and `/api/v1/system` do not exist in nginx, whatever the browser does.

## 8. Accessibility

Target: WCAG 2.2 level AA.

- Landmarks (`header`, `nav`, `main`, `footer`), a skip link, one `h1` per page and ordered
  headings; on navigation the focus moves to the main content and the document title changes.
- Every chart is a keyboard control (`role="slider"`): arrow keys move the reading day by
  day, Home/End jump to the ends, Escape leaves it; `aria-valuetext` reads the date and every
  series' value. A "Table" toggle shows the same numbers as a captioned table with row
  headers. Pointer and touch show the same reading.
- Identity is never colour alone: series have legends, each line its own pattern (solid,
  dashed, dotted) and multi-series bars direct labels; statuses carry an icon and a word.
- Colours come from design tokens with a light and a dark theme (following the system): text
  meets 4.5:1, chart marks and control borders 3:1 against their surface in both themes; the
  three series colours pass a colour-vision-deficiency check on every pair (ΔE ≥ 8).
- Forms: visible labels, units and ranges in the description, errors attached to fields and
  summarised with links, a failed run shown where the focus goes, the result announced by
  moving the focus to it; focused fields never sit under the sticky bars (scroll padding).
- `prefers-reduced-motion` removes transitions; `forced-colors` keeps lines, focus rings and
  bands visible; the layout reflows at 320 CSS px and at 200 % zoom without horizontal page
  scrolling (wide tables scroll inside their own region).
- Tested with axe-core on every page in light and dark themes (no violation), keyboard-only
  tests (skip link, navigation, charts, every simulator field reachable and visible), reflow at
  320 px, and the jsx-a11y lint rules. Not tested: real screen-reader sessions (NVDA, JAWS,
  VoiceOver) and users with disabilities.

## 9. Responsive design and mobile

One layout from 320 px up: the navigation becomes a menu button below 1024 px; panels stack;
statistic rows wrap; charts keep their aspect and their labels; tables scroll inside their
panel. Controls are at least 24 × 24 CSS px (WCAG 2.2 target size), the menu button and the
navigation links 44 px. A tap on a chart shows the reading and keeps it until the next tap.
Tested in Chromium on a Pixel 7 profile (412 × 915) and at 320 × 640 (no horizontal
scrolling on any page).

## 10. Performance (measured)

Build budgets (`npm run size`, enforced in CI; gzip as the servers send it):

| Measure | Budget | Measured |
|---|---|---|
| initial JavaScript (gzip) | 110 KB | 84.5 KB |
| initial HTML, CSS and JavaScript (gzip) | 125 KB | 90.7 KB |
| largest lazy chunk (gzip) | 30 KB | 20.5 KB |
| font (woff2) | 50 KB | 48.3 KB |
| the whole build (raw) | 1 MB | 495.8 KB |

Page loads, measured in Chromium against `atlas api serve` on the development container
(median of three cold loads, cache disabled; "throttled" is a slow 4G connection, 150 ms
round trip and 1.6 Mbit/s, with the CPU slowed four times; "settled" means every panel has
its data). The API itself sends JSON uncompressed; behind nginx it is gzipped, so the
transferred sizes below are upper bounds.

| Page | First paint | Heading | Settled | Throttled: first paint | Heading | Settled | Transferred |
|---|---:|---:|---:|---:|---:|---:|---:|
| Overview | 0.24 s | 0.40 s | 0.49 s | 1.23 s | 1.85 s | 2.29 s | 234 KB |
| Forecasting | 0.23 s | 0.50 s | 0.65 s | 1.25 s | 1.84 s | 2.64 s | 325 KB |
| Uncertainty | 0.26 s | 0.39 s | 0.42 s | 1.18 s | 1.91 s | 2.33 s | 218 KB |
| Decisions | 0.25 s | 0.63 s | 0.65 s | 1.20 s | 1.85 s | 2.73 s | 268 KB |
| Simulator | 0.30 s | 0.30 s | 0.38 s | 1.16 s | 1.76 s | 2.00 s | 189 KB |
| Provenance | 0.30 s | 0.37 s | 0.46 s | 1.17 s | 1.76 s | 2.20 s | 233 KB |

A real simulation (7 days, one changed budget) answered in 0.2-0.7 s. Views download only
what they show: the history is the last 180 days by default (20 KB); the whole snapshot
(1,277 days, 136 KB) only when "All" is chosen. Moving the pointer over a chart re-renders
only its crosshair. Each page's code loads when it is first opened. Hashed assets are cached for a year
(`immutable`); `index.html` is revalidated (`no-cache`, with an ETag) and the descriptor is
never cached, so a new release is picked up on the next load. Text is compressed (gzip) by both servers.

## 11. Development, build and checks

Requirements: Node.js 22.12+ (or 24) and npm, on a development machine or CI only.

```bash
cd web
npm ci                         # the locked toolchain
npm run dev                    # http://localhost:5173, /api proxied to ATLAS_DEV_API (default http://127.0.0.1:8000)
npm run format:check && npm run lint && npm run typecheck
npm run gen:api                # after a contract change (docs/api/openapi.json)
npm test                       # Vitest; npm run test:coverage for the coverage gate
npm run build                  # writes dist/ and dist/atlas-dashboard.json: commit both
npm run verify:dist && npm run size
npx playwright test            # browser tests (starts the API and two nginx stacks)
```

Locally the API serves the committed build: `uv run atlas api serve` and open
`http://127.0.0.1:8000/`. `ATLAS_API_DASHBOARD=false` turns the mount off. After a
change to the API or its committed results, record the fixtures again:
`ATLAS_UPDATE_DASHBOARD_FIXTURES=1 uv run pytest tests/test_m7_fixtures.py`.

## 12. Deployment, upgrade and rollback

The build is part of every release (it is committed), so frontend and backend move
together:

- **Build** (`deploy/scripts/build-release.sh`): checks the release's `web/dist` against its
  manifest as the service user; `release.json` records the dashboard (version, files,
  manifest SHA-256); `atlas-deploy manifest check` refuses an altered, removed or unrecorded
  file.
- **Activation** in an exposed profile publishes the release's build to
  `/opt/atlas/dashboard/<release id>/` and verifies the copy **before** writing the nginx
  site that points to it; a release whose build is altered is refused before anything
  changes. In the tunnel profile nothing is published (the API serves the dashboard).
- **Verification** (after every activation): the API serves the release's `index.html` with
  the dashboard policy and the descriptor; the published copy holds exactly the recorded
  files.
- **Rollback** (automatic or explicit) re-renders the site of the restored release, so nginx
  serves that release's dashboard with that release's API. Copies are kept per release;
  `atlas-deploy prune` removes the copies of pruned releases.
- **Status and plans**: `atlas-deploy status` shows the dashboard version and fingerprint;
  `plan-upgrade` says whether the dashboard changes.

**Upgrading an M6 server to M7.** Plan and activate the M7 release with **its own**
`atlas-deploy` (`/opt/atlas/releases/<new id>/.venv/bin/atlas-deploy plan-upgrade <new id>`,
then `... activate <new id>`). The M6 tool cannot render the M7 site template (an unknown
placeholder): its `plan-upgrade` fails and its `activate` refuses before changing anything,
which is safe but not useful. `atlas-deploy render --apply` of an exposed site publishes the
release's dashboard first, and on a server refuses a checkout (its build is not verified). Rolling back from M7 to an M6 release works with the M7 tool
(an M6 release has no dashboard: its site has no dashboard locations). The first M7
activation also needs `/opt/atlas/dashboard` (created by the M7 `bootstrap-host.sh`, or
`sudo install -d -o root -g www-data -m 0750 /opt/atlas/dashboard`).

See [DEPLOYMENT.md](DEPLOYMENT.md) section 19 for how this fits the M6 package.

## 13. Testing

| Suite | What it checks |
|---|---|
| Vitest (`web/src/**/*.test.ts(x)`, 99 tests; coverage gates 85 % statements, functions and lines, 75 % branches) | the client: validation, integrity refusal, every error kind, timeouts, aborts, `Retry-After`; formatting (dates in UTC, units, signs); the simulator form (ranges, integers, the money rule, minimal requests, API errors mapped onto fields); the cache; every view rendered from the real fixtures (warnings present next to their numbers, DIAGNOSTIC handling, disabled simulations in each profile, error states without numbers); charts (keyboard, readings, tables); the source free of storage, cookies, `eval` and HTML injection; the review's findings (`review.test.tsx`) |
| `tests/test_m7_fixtures.py` (15) | each fixture is byte for byte the API's answer |
| `tests/test_m7_dashboard.py` (83) | the build manifest, verification and tamper detection; the API mount: exact paths, methods, HEAD, ETag/304, gzip, cache policies, the CSP once, the descriptor by key setting, a damaged build not served, the API unchanged |
| `tests/test_m7_deploy.py` (38) | the static route class (exact names, no shadowing of the API), both exposed profiles, `nginx -t`; publication (modes, verification, altered copies replaced, planted files removed); the release manifest; activation order, tunnel, rollback, explicit rollback, prune, status, upgrade plans; verification of the served and published dashboard |
| `tests/test_m7_proxy.py` (32) | real nginx with TLS and the real API: accounts, every page and file with its bytes, types, cache and policy; compression; methods; unlisted files, links and traversal; the API's own policy beside it; a simulation without a key in the browser; the public demo; the access log; rate limits (static never 429, the API still) |
| `tests/test_m6_container.py` (+2) | on a disposable Ubuntu 24.04 server with systemd: the dashboard through nginx after a real activation, and the dashboard following the API through the automatic and explicit rollbacks |
| Playwright (`web/e2e`, 4 projects) | every page in Chromium against the real API; the simulator against the real M4 engine; reflow at 320 px (every disclosure open, a simulation result shown); failures injected at the network layer (a slow answer, an unavailable component, a network failure, a contract and an integrity violation, a simulation stopped at its deadline or refused for lack of a worker, a page whose code fails to load); keyboard-only use; axe on every page in both themes; no CSP violation; a phone viewport; the private and public-demo nginx sites (accounts, simulations, the read-only demo) |
| CI job `dashboard` | format, lint, types, contract types up to date, Vitest with coverage, a reproducible build (`git diff --exit-code -- dist`), `verify:dist`, size budgets, `npm audit`, Playwright |

## 14. Limitations

- **Research software.** The dashboard inherits every limitation of M2-M5 (docs/API.md
  section 11). It presents archived results; nothing is live.
- **Simulations in the tunnel profile** are disabled in the browser because the API there
  requires a key. Use `curl` with the key, or the private profile.
- **One account model.** HTTP Basic through nginx: no per-user roles; everyone with an
  account can run simulations (rate-limited).
- **Accessibility** is tested automatically and by keyboard; not with screen-reader users.
- **Browsers**: tested in Chromium only (desktop and a phone profile). The code uses
  standard APIs supported by current Firefox and Safari, but they were not run. Trusted
  Types is enforced by Chromium-based browsers; others ignore that directive (the rest of
  the policy still applies).
- **The npm release-age cooldown** is a review rule, not enforced by a tool.

## 15. Risks for M8

- **Results changing under the dashboard.** M8 work that changes the API or the committed
  results changes the fixtures; `test_m7_fixtures.py` will fail until they are recorded again
  and the views reviewed for the new numbers and wording.
- **Contract evolution.** New or changed fields need `gen:api`, schema updates and the
  views' review; an unknown field is tolerated, a changed one is refused (by design).
- **Public exposure.** The public-demo dashboard is read-only, but serving it publicly still
  needs the M6 security review, DDoS protection and a decision on log retention.
- **Toolchain upkeep.** React, Vite, zod and Playwright move fast; updates must respect the
  cooldown, keep the build reproducible (CI checks it) and the size budgets.
- **Simulation load.** Every account can start simulations; a busier deployment needs
  per-user quotas or a queue (M5 has none by design).
- **Over-interpretation.** The more polished the interface, the more its numbers look
  authoritative; any new view must carry the same integrity statements next to its numbers.

## 16. Independent review

Four independent reviewers (separate agents, read-only, each with one brief) examined M7:
scientific integrity, numbers and API integration; user experience, accessibility, mobile
and performance (with screenshots of every page at 1280 and 390 px, light and dark, and the
public-demo state); security and deployment (rendered sites, the API mount probed
in-process); tests and CI. Every finding was checked against the code before it was fixed;
each fix has a test that fails without it (`web/src/views/review.test.tsx`, the e2e specs,
`tests/test_m7_*.py`).

| Finding | Severity | Fix |
|---|---|---|
| H1 taken as the first test of the list: a different order would have shown H2's significant result as H1's | minor (integrity) | H1 found by name, as the API does; "not computed" distinct from "not significant"; the callout states what the interval shows |
| Chart roles assigned after removing duplicates: with the recommended model also the best baseline, the reference would be labelled "best baseline" | minor | each model keeps its own roles and colour |
| The simulator showed the reference rationale without the API's caveat that it describes the 30-day reference run | minor (integrity) | the caveat is shown with every rationale |
| A plan that is one of several equally scored optima was called "the certified optimum" | minor (integrity) | the API's uniqueness is shown (tag in the summaries, wording in the plan) |
| A contract error's details were shown as mistakes in the user's inputs | minor | only 422 answers are mapped onto fields |
| The client gave up on a simulation (45 s) before a server allowed a longer deadline, and then said the server had stopped it | minor | the client deadline is 150 s, above any server deadline; its own timeout has its own message |
| The limitations panel loaded forever when its sources failed | minor | an explicit failure state |
| A DIAGNOSTIC plan's policy comparison was not labelled as such | minor (integrity) | heading, caption and a "comparable on score" column; exact relation wording |
| An independent check that did not run read as failed; no stale mark after editing the inputs; body errors overwrote each other | nit | fixed |
| **No reflow at 320 px**; Provenance and the simulation result scrolled sideways at 390 px (the end-to-end test measured against a zoomed-out phone viewport) | major (accessibility) | `minmax(0, 1fr)` grid tracks, long identifiers wrap; the tests measure against the layout viewport and run at 320 px with every disclosure open |
| A failed simulation was announced but not visible (the error rendered far below) | major (UX) | the error is shown where the focus goes |
| Focused fields hidden under the sticky bars (WCAG 2.4.11) | major (accessibility) | scroll padding; a test tabs through every field |
| The touch tooltip was clipped off-screen on phones | major (mobile) | measured and kept inside the chart; tested at four positions |
| The progress counter re-announced every 200 ms | minor (accessibility) | announced once |
| The public-demo simulator still read as runnable | minor | the reason first, consistent text, disabled fields styled as such |
| Control borders (1.75:1), one series colour (2.74:1) and the neutral mark (2.41:1) below 3:1; faded stale results | minor (accessibility) | new tokens, validated (contrast and colour-vision deficiency, all pairs); stale results marked by a rule and a sentence |
| Some series told apart by colour alone; a target label over the lines | minor | a line pattern per series; a haloed, right-aligned label; visible chart title |
| FAILED_CALIBRATION broken mid-word in a tile; current page not marked in forced colours | minor | fixed |
| Every pointer move redrew the whole chart (24 ms at 4x CPU slowdown on 1,278 points) | minor (performance) | static layers built once per render, map lookups |
| Wording, captions clipped in scrolling tables, Escape on the menu, a zero line through phone labels | nit | fixed |
| Dashboard files had a looser rate limit (50/s) than the API, counted before authentication | minor (security) | 20/s, burst 60 (at most twice the API's), documented |
| `Accept-Encoding: gzip;q=0` still received gzip from the API mount | nit | q-values honoured |
| JSON answers on dashboard paths (descriptor, 405) carried the HTML policy | nit | the API's policy |
| The descriptor's ETag never changed; replacing a published copy could leave none if the second rename failed | nit | content ETag; the previous copy is put back |
| `atlas-deploy render --apply` could point the site at an unpublished copy | nit | it publishes first, and refuses a checkout on a server |
| `plan-upgrade` from M6 to M7 needs the new tool too; `public` on assets behind accounts | nit | documented; `public` removed |

Checked and found sound by the reviewers (summarised): the static route class (no path
leaves the published directory; authentication and the allow-list cover every dashboard
location); headers set once; no BREACH-style exposure; the CSP matches the build; no key
or credential in the source or the build; publication, rollback, pruning and verification;
CI permissions and pinned actions; no npm install scripts run on Linux; the date handling
(UTC throughout); units, signs and labels against the fixtures; the zod schemas against the
contract; the simulator's bounds equal the server's; text contrast in both themes;
landmarks, headings, focus management, the chart sliders and the form's error handling.
