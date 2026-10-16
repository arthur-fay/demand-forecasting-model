# Interactive web dashboard (M7)

The dashboard is the human interface to ATLAS. It shows the committed research outputs of
M2 (forecasting), M3 (uncertainty) and M4 (decision simulation) and runs bounded
HYPOTHETICAL simulations, all through the M5 API. It is a static single-page application in
`web/` (React, TypeScript), built once into the committed `web/dist/` and served next to
the API from the same origin: by the API itself on a workstation or through an SSH tunnel,
and by nginx in the private and public-demo deployments. The server needs no Node.js.

> **Status: merged into `main` (merge commit `194fa4a`) and reviewed in the M8 release audit
> ([docs/audit](audit/README.md)); section 17 lists the corrections of the dashboard's
> findings.** Nothing has been deployed to any server or domain.
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
17. M8 release audit: corrections

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

**Licences of what ships.** The build contains minified copies of the packages below and the
Inter font, whose licences require the copyright and licence text with every copy (MIT and
ISC: the copyright and permission notice; SIL Open Font License 1.1: the copyright and the
licence). Every build ships them next to the code:

- `third-party-licences.txt`, written by Vite's `build.license` (`vite.config.ts`): the name,
  version, licence and licence text of every package bundled into the JavaScript;
- `inter-licence.txt`, the `LICENSE` of `@fontsource-variable/inter` unchanged (the Inter
  copyright and the OFL-1.1 text), copied into `web/public/` so that it ships with the font,
  which Vite's notices do not cover (it is a CSS asset).

Both are files of the build, listed in its manifest and served by the API and nginx like
every other file, as `text/plain; charset=utf-8` (the one type added for them; the policy is
unchanged), and the footer of every page links to them. `web/build/notices.test.ts` fails if
a runtime package of the lock is missing from the notices or a development package is in
them, or if the font's file differs from its package's; `tests/test_m7_dashboard.py` checks
that the API serves both. This inventory covers the third-party code only: it does not choose
the project's own licence.

| Package | Version | Licence | Copyright | Shipped as |
|---|---|---|---|---|
| react | 19.3.0 | MIT | Meta Platforms, Inc. and affiliates | JavaScript |
| react-dom | 19.3.0 | MIT | Meta Platforms, Inc. and affiliates | JavaScript |
| scheduler (of react-dom) | 0.28.0 | MIT | Meta Platforms, Inc. and affiliates | JavaScript |
| zod | 4.6.5 | MIT | 2025 Colin McDonnell | JavaScript |
| d3-scale | 4.0.2 | ISC | 2010-2021 Mike Bostock | JavaScript |
| d3-shape | 3.2.0 | ISC | 2010-2022 Mike Bostock | JavaScript |
| d3-array (of d3-scale) | 3.2.4 | ISC | 2010-2023 Mike Bostock | JavaScript |
| d3-color (of d3-interpolate) | 3.1.0 | ISC | 2010-2022 Mike Bostock | JavaScript |
| d3-format (of d3-scale) | 3.1.2 | ISC | 2010-2026 Mike Bostock | JavaScript |
| d3-interpolate (of d3-scale) | 3.0.1 | ISC | 2010-2021 Mike Bostock | JavaScript |
| d3-path (of d3-shape) | 3.1.0 | ISC | 2015-2022 Mike Bostock | JavaScript |
| d3-time (of d3-scale) | 3.1.0 | ISC | 2010-2022 Mike Bostock | JavaScript |
| d3-time-format (of d3-scale) | 4.1.0 | ISC | 2010-2021 Mike Bostock | JavaScript |
| internmap (of d3-array) | 2.0.3 | ISC | 2021 Mike Bostock | JavaScript |
| @fontsource-variable/inter | 5.3.0 | OFL-1.1 | 2016 The Inter Project Authors | the Inter variable font (one Latin woff2) |

**Versions and supply chain.** `web/package.json` pins exact versions (`.npmrc`:
`save-exact`) and `package-lock.json` records every package with its integrity hash; CI
installs with `npm ci` and runs `npm audit --audit-level=high` (0 vulnerabilities at the
time of writing); since M8 a weekly workflow (`.github/workflows/dependency-audit.yml`) also
audits the shipped packages at moderate severity and re-audits both locks for new advisories
(DEVELOPMENT.md, "Dependency maintenance"). The project's two-week release-age cooldown (a
review rule since M4, not enforced by a tool) was applied to the npm packages as well: every one of the 426 locked packages was published
before 1 October 2026, at least 14 days before this milestone (checked against the
registry's publish times). npm has no setting that enforces this, so it is a review rule
for future updates.

## 3. Screens and workflows

Every page has a header naming its milestone, the warnings that qualify its numbers placed
next to them, and a provenance footer under every panel of research results (the source
files with their SHA-256, run ids and fingerprints, the snapshot, and the API and ATLAS
versions). The Overview's row of status figures has no footer of its own: its sources are in
the footers of the panels below it. Each chart has a "Table" view with its values (the band
chart's observed days are in the history table of the Forecasting page, as its caption says).

| Page | Path | What it shows | API |
|---|---|---|---|
| Overview | `/` | the scientific status of M2, M3 and M4 in one row; the data snapshot (dates, days, fingerprint); the 30-day archived projection summary; the last 180 observed days joined to the projection; the reference scenarios with feasibility; the API's own warnings and limitations | `system`, `models`, `forecasts`, `forecasts/history`, `uncertainty/status`, `scenarios` |
| Forecasting | `/forecasting` | the full observed history and the archived projection (federal holidays marked); backtest accuracy by horizon; models against baselines (MAE); the pre-registered confirmation tests with their Holm-adjusted p-values; the protocol's periods and run identity | `forecasts`, `forecasts/history`, `forecasts/metrics`, `models` |
| Uncertainty & risk | `/uncertainty` | the interval status (FAILED_CALIBRATION) first; the projection with its uncalibrated quantile bands (holiday-related days marked); observed against nominal coverage with confidence intervals and the tolerance; quantile reliability; threshold-exceedance statuses | `uncertainty/status`, `uncertainty/intervals`, `forecasts`, `forecasts/history` |
| Decision optimisation | `/decisions` | the reference scenarios compared (DIAGNOSTIC plans shown as such, never subtracted from feasible ones); which scenarios are feasible and why not; one scenario in detail (daily resources, backlog, policy comparison) with its HYPOTHETICAL assumptions | `scenarios`, `scenarios/comparison`, `scenarios/{id}` |
| Simulator | `/simulator` | a form built from the API's parameter ranges; one bounded M4 solve on the server; the result with its plan, the inputs as resolved, the limitations and assumptions | `simulations/parameters`, `simulations` |
| Methods & provenance | `/provenance` | service versions, components and limits (private deployments only); every committed source with its fingerprints; the dashboard build and deployment; what each milestone did, with links labelled as the current documentation on the `main` branch (the release's own copy is its `docs/` directory) | `system`; for the sources, `models`, `forecasts`, `forecasts/history`, `uncertainty/status`, `uncertainty/intervals`, `scenarios`, `scenarios/comparison`, the first scenario's `scenarios/{id}`, `simulations/parameters` |

The servers answer the six page paths exactly: any other address (a typo, `/forecasting/`,
`/Forecasting`) gets the API's or nginx's JSON 404 (section 7). The dashboard's own "Page not
found" view, which lists the pages, appears only where a server returns `index.html` for
every path (`npm run dev`, `npm run preview`). A page whose code fails shows an error with a
reload button instead of a blank screen. The top bar names the deployment from its
descriptor ("Local API", "Private deployment", "Public demo") and adds "read-only" where the
descriptor says simulations cannot run from the browser.

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
range, unit, assumption and whether it can be removed ("no limit"), with the reference values'
provenance footer. Every input is a text field, so exact values can be entered on a phone and
with assistive technology; the planning horizon, the demand multiplier and the demand shift
also have a slider for pointer users (the text field is the keyboard control). The M4
parameters have none: their ranges (up to 0-100,000,000) are too wide for a useful slider.

- **Validation as you type** against the published ranges (numbers only, integers where the
  API requires them, the money and penalty rule "0 or at least 0.01"). Errors are attached
  to their fields (`aria-invalid`, `aria-describedby`) and summarised above the button with
  links to each field. The API validates everything again (M4's own parser and cross-field
  rules): its field errors are mapped back onto the same fields.
- **Only changed fields are sent**; everything else takes the reference default, as the API
  defines. The request sent is shown with the result.
- **Running**: the elapsed time is shown next to the button (a screen reader hears "Running
  the simulation on the server." once), and a Cancel button aborts the request: "Simulation
  cancelled." is shown and announced, and the focus returns to the Run button. Leaving the
  page cancels the request too. A second run is not possible while one runs.
- **Errors**: `simulations_busy` (no queue: retry), `simulation_timeout` (the server stopped
  the solve at its deadline), a client timeout (no answer in 150 s), `simulation_rejected`
  (M4 refused the inputs as stated), `simulation_failed` (no certified result, so no plan),
  `rate_limited` (the proxy's per-client limit), 401 (a key is required: the dashboard never
  holds one). An error answer from the API is titled "Simulation not run"; an answer the
  dashboard refused (contract, integrity) or that never arrived (timeout, network) is titled
  "Simulation result not shown", because the server may have completed that run. The
  previous result, if any, stays visible and is marked as not reflecting the last attempt.
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
| Provenance | a footer under every panel of research results (the Overview's status row shares those of the panels below it); the Methods & provenance page | `provenance` |

The text of every warning is the API's where the API provides one. Where the dashboard
describes the M3 outcome in its own words (the Uncertainty page's header and status text, the
band chart's name and summary, the coverage panel's title, the intervals behind the M4 stress
paths on Decisions and the Simulator, the M3 result on Methods & provenance), the words are
chosen from the status the API serves: the intervals are said to have failed only when the
status is FAILED_CALIBRATION, called calibrated only when it is DECISION_GRADE and
uncalibrated only when it is FAILED_CALIBRATION or INSUFFICIENT_EVIDENCE. When the status is
UNAVAILABLE, or cannot be loaded, the page says so and names no outcome. The
dashboard's own wording keeps to the M4 wording rules on recommendations and promises, as the
API applies them ([API.md](API.md) section 10, `test_m5_contract.py`): "recommend" only in a
not-a-recommendation phrase or for the M2 model recommendation ("Recommended model"), and no
promotional term ("optimal staffing", "saves", "cost-effective"). The archived forecast is
never presented as a live prediction; "confidence interval" is used only in its statistical
sense (the interval of a measured coverage). Status colours are never alone: each comes with
an icon and a label.

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
`Permissions-Policy` denying camera, microphone, geolocation, payment and USB, and HSTS
through nginx.

**The static route class.** Neither server maps a URL onto the file system:

- The API answers only the exact paths in the verified manifest, from memory.
- nginx gets one location per class, generated from the manifest:
  - the pages (one exact-match regex) answer `index.html` through `try_files`;
  - the assets (one regex of their exact names) and the other top-level files (the favicon
    and the licence notices, one exact location each) are served from the published copy
    with `disable_symlinks on`;
  - the descriptor goes through the same authentication, then a named location.
  - Every one is GET/HEAD only, behind the same accounts and allow-list as the API. The
    pages, assets and top-level files have their own per-client zone (`atlas_static`, 20
    requests/s, burst 60) so that a first load or several tabs never use up the API's
    limit; the descriptor, one small request per page load, counts against the API's read
    limit. nginx counts requests before authentication, so a client without an account can
    try passwords at most twice as fast on the dashboard's files as on the API, and the two
    zones add up: up to 30 requests/s per client in all (10 on API paths, 20 on dashboard
    files), against M6's 10. Accounts have 192-bit or 20-character passwords.
  - The descriptor is JSON and carries the API's headers (`default-src 'none'`, `no-store`);
    hashed files are cached as `max-age=31536000, immutable` (not `public`: they are behind
    accounts, so shared caches must not keep them).
- A planted file, a link, an encoded or dotted path, a trailing slash, another case: all 404
  or 400, answered in JSON whatever extension the path ends with (nginx types nothing by
  extension outside the dashboard's own locations); tested against real nginx.
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
  dashed, dotted; point markers only where they leave the pattern visible), multi-series
  bars direct labels, and a model's role (recommended, best baseline) is written next to its
  bar; statuses carry an icon and a word.
- Colours come from design tokens with a light and a dark theme (following the system): text
  meets 4.5:1, chart marks and control borders 3:1 against their surface in both themes, and
  a line drawn over bands runs on a halo of the surface colour; the three series colours
  pass a colour-vision-deficiency check on every pair (ΔE ≥ 8).
- Forms: visible labels, units and ranges in the description, errors attached to fields and
  summarised with links (each a target of at least 24 px), a failed run shown where the focus
  goes, the result announced by moving the focus to it. Focused fields never sit under the
  sticky bars: scroll padding keeps them clear of the top bar and of the simulator's action
  bar, which is sticky only in a window at least 40rem wide and 32rem high (on phones and at
  high zoom it stays in place), with the list of changed inputs above it rather than in it.
- `prefers-reduced-motion` removes transitions; `forced-colors` keeps lines, focus rings and
  bands visible and marks the current page, the chosen option of each segmented control and
  the line keys of tags; the layout reflows at 320 CSS px and at 200 % zoom without
  horizontal page scrolling (wide tables scroll inside their own region).
- Tested with axe-core's WCAG 2.0, 2.1 and 2.2 A and AA rules (target size included) on every
  page in light and dark themes, again with every chart's table and every disclosure open,
  on a simulation result, a failed run and the error summary of two invalid fields, and on
  the phone layout (no violation); keyboard-only tests (skip link, navigation, charts, every
  simulator field reached by Tab and by Shift+Tab and never under a sticky bar); the current
  page in forced colours; reflow at 320 px; unit tests of the colour tokens (contrast in both
  themes, colour-vision deficiency on every series pair), of the single announcement of a
  running simulation and of the focus through a run and its cancellation; and the jsx-a11y
  lint rules. Not tested: real screen-reader sessions (NVDA, JAWS, VoiceOver) and users with
  disabilities.

## 9. Responsive design and mobile

One layout from 320 px up: below 1024 px the navigation becomes a menu button, and the menu
opens over the page just under the top bar, wherever the page is scrolled (Escape from the
menu, a chosen page or the focus moving on into the page closes it); below 360 px the top
bar shows the ATLAS mark without its word, so the deployment tags fit. Panels stack;
statistic rows wrap; charts keep their aspect and their labels (label room is sized from
the labels); tables scroll inside their panel. Buttons and text fields are at least 36 CSS px
high, segmented options 30 px, the links of the simulator's error summary 24 px, the menu
button and the navigation links 44 px; the 18 px "No limit" checkbox (its label also toggles
it) and the small disclosure summaries rely on the spacing exception of the WCAG 2.2 target
size, which axe checks on every page. A tap on a chart shows the reading and keeps it until
the next tap.
Tested in Chromium on a Pixel 7 profile (412 × 915) and at 320 × 640 (no horizontal
scrolling on any page).

## 10. Performance (measured)

Build budgets (`npm run size`, enforced in CI; gzip as the servers send it), measured on the
build after the M8 corrections (section 17; the whole build includes the 16.6 KB of licence
notices):

| Measure | Budget | Measured |
|---|---|---|
| initial JavaScript (gzip) | 110 KB | 85.0 KB |
| initial HTML, CSS and JavaScript (gzip) | 125 KB | 91.3 KB |
| largest lazy chunk (gzip) | 30 KB | 20.7 KB |
| font (woff2) | 50 KB | 48.3 KB |
| the whole build (raw) | 1 MB | 524.4 KB |

Page loads, measured for M7 (before those corrections) in Chromium against `atlas api serve`
on the development container (median of three cold loads, cache disabled; "throttled" is a
slow 4G connection, 150 ms round trip and 1.6 Mbit/s, with the CPU slowed four times;
"settled" means every panel has its data). The API itself sends JSON uncompressed; behind
nginx it is gzipped, so the transferred sizes below are upper bounds.

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
only its crosshair. Each page's code loads when it is first opened. Hashed assets are cached
for a year (`immutable`); `index.html` is revalidated (`no-cache`, with an ETag) and the
descriptor is never cached, so a new release is picked up on the next load. Text is
compressed (gzip) by both servers.

## 11. Development, build and checks

Requirements: Node.js 22 (22.22.2 or later) or 24 (24.15 or later), the versions the lock's
test tools declare, and npm, on a development machine or CI only.

```bash
cd web
npm ci                         # the locked toolchain
npm run dev                    # http://localhost:5173, /api proxied to ATLAS_DEV_API (default http://127.0.0.1:8000)
npm run format:check && npm run lint && npm run typecheck
npm run gen:api                # after a contract change (docs/api/openapi.json)
npm test                       # Vitest; npm run test:coverage for the coverage gate
npm run build                  # writes dist/ and dist/atlas-dashboard.json: commit both
npm run verify:dist && npm run size
npx playwright install chromium # once: npm ci downloads no browser
npx playwright test            # browser tests (starts the API and two nginx stacks)
```

The browser tests use Chromium only: `npx playwright install chromium` downloads it (on a
bare Debian or Ubuntu host `--with-deps` also installs its system libraries, with sudo), or
`PLAYWRIGHT_CHROMIUM_EXECUTABLE` names a local one. They also need uv, nginx and openssl on
`PATH` (on Debian or Ubuntu `sudo apt-get install nginx`, add `/usr/sbin` to `PATH` and stop
the nginx service, as CI does) and the ports 8766, 8443, 8444, 8081 and 8082 free.

Locally the API serves the committed build: `uv run atlas api serve` and open
`http://127.0.0.1:8000/`. `ATLAS_API_DASHBOARD=false` turns the mount off. After a
change to the API or its committed results, record the fixtures again:
`ATLAS_UPDATE_DASHBOARD_FIXTURES=1 uv run pytest tests/test_m7_fixtures.py --no-cov` (the
whole-package coverage gate does not apply to one module).

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
  the dashboard policy and descriptor format **that the release records** in its
  `release.json` (M8-03; releases built before 1.0.0 record only their CSP, and the tool's
  other values apply); the published copy holds exactly the recorded files (a link or any
  other file that is not a regular file fails the check).
- **Rollback** (automatic or explicit) re-renders the site of the restored release **with that
  release's recorded headers and descriptor** (M8-03), so nginx serves that release's dashboard
  with that release's API, even when a later tool performs the rollback. `atlas-deploy
  recover` installs the release's site again, restarts the service and reloads nginx before it
  keeps an interrupted activation as verified (M8-02). A rollback by hand, without the tool,
  switches only the API ([DEPLOYMENT.md](DEPLOYMENT.md) section 11). Copies are kept per
  release; `atlas-deploy prune` removes the copies of pruned releases.
- **Status and plans**: `atlas-deploy status` shows the dashboard version and fingerprint;
  `plan-upgrade` says whether the dashboard changes. A malformed build manifest stops
  `render`, `validate` and `plan-upgrade` with an error naming the release (exit 2).
- **`atlas-deploy render --apply`** of an exposed site publishes the release's dashboard
  first, and only for the **current** installed release (`--release-dir
  /opt/atlas/releases/<id>`, also through `/opt/atlas/current`) whose build matches its
  `release.json`, under the deployment lock and recorded in `history.jsonl` (M8-26). Before
  the first activation only `--stage acme` is accepted; any other release is refused with a
  pointer to `atlas-deploy activate <id>`. A checkout or a copy elsewhere is refused; in a
  rehearsal (`--root`) a checkout's site reads the checkout's own build.

**Upgrading an M6 server to M7.** Plan and activate the M7 release with **its own**
`atlas-deploy` (`/opt/atlas/releases/<new id>/.venv/bin/atlas-deploy plan-upgrade <new id>`,
then `... activate <new id>`), in every profile. In the private and public-demo profiles the
M6 tool cannot render the M7 site template (an unknown placeholder): its `plan-upgrade`
fails and its `activate` refuses before changing anything. In the tunnel profile (no nginx
site) it plans and activates the M7 release, but never runs the M7 check that the API
serves the dashboard. The first activation in an exposed profile creates
`/opt/atlas/dashboard` (root:www-data 0750) if the M6 bootstrap did not: nothing needs to be
prepared by hand. Rolling back from M7 to an M6 release works with the M7 tool (an M6
release has no dashboard: its site has no dashboard locations).

See [DEPLOYMENT.md](DEPLOYMENT.md) section 19 for how this fits the M6 package.

## 13. Testing

| Suite | What it checks |
|---|---|
| Vitest (`web/src/**/*.test.ts(x)` and `web/build`, 191 tests, run in the America/Los_Angeles time zone; queries wait up to 5 s; coverage gates 85 % statements, functions and lines, 75 % branches) | the client: validation, integrity refusal, every error kind, timeouts, aborts, `Retry-After`; formatting (dates in UTC, also with the module loaded in UTC−11, UTC−7 and UTC+14; units, signs); the simulator form (ranges, integers, the money rule, minimal requests, API errors mapped onto fields); the cache; every view rendered from the real fixtures (warnings present next to their numbers, DIAGNOSTIC handling, disabled simulations in each profile, error states without numbers); charts (keyboard, readings, tables, markers, ticks, labels, halos, no line rebuilt when the reading moves); the colour tokens (contrast in both themes, colour-vision deficiency); the shell (menu, Escape, deployment badge); the source free of storage, cookies, `eval` and HTML injection; the build plugin and the licence notices of the committed build; the reviews' findings (`review.test.tsx`, the `round2-*.test.ts(x)` files and `m8-audit.test.tsx`) |
| `tests/test_m7_fixtures.py` (15) | each fixture is byte for byte the API's answer |
| `tests/test_m7_dashboard.py` (94) | the build manifest, verification and tamper detection; the licence notices listed and served; the API mount: exact paths (also as raw request paths, which the test client would normalise), methods, HEAD, ETag/304, gzip, cache policies, the CSP once, the descriptor by key setting, a damaged build not served, the API unchanged |
| `tests/test_m7_deploy.py` (57) | the static route class (exact names, no shadowing of the API), both exposed profiles, `nginx -t` without a warning; the static rate (at most twice the API's read rate); compression and types (nothing typed by the requested extension); publication (modes, verification, altered copies replaced, planted files removed); the release manifest; activation order, tunnel, rollback, explicit rollback, prune, status, upgrade plans; verification of the served and published dashboard (links refused); `render --apply` (only the current installed release, as recorded, also through `current`; the ACME site before the first activation; M8-26); a malformed build manifest an error, not a crash; M8: recovery of an activation interrupted during the restart (M8-02), a later tool restoring and rolling back to an older release with its own CSP, Permissions-Policy and descriptor, and unsafe recorded policies refused (M8-03) |
| `tests/test_m7_proxy.py` (32) | real nginx with TLS and the real API, inside an http block with the distribution's types by extension (as on Ubuntu): accounts, every page and file with its bytes, types, cache and policy; nginx's own 400, 401 and 404 answers in JSON whatever the extension; compression, pages included; methods; unlisted files, links and traversal; the API's own policy beside it; a simulation without a key in the browser; the public demo; the access log; rate limits (static never 429, the API still) |
| `tests/test_m6_container.py` (+2) | on a disposable Ubuntu 24.04 server with systemd: the dashboard through nginx after a real activation; after the automatic rollback and an explicit rollback from a second verified release to the first (M8-37), the site's dashboard root (the restored release's copy), read from the site file |
| Playwright (`web/e2e`, 4 projects) | every page in Chromium against the real API, with no error panel; every reference scenario opened; the simulator against the real M4 engine; reflow at 320 px (every disclosure open, a simulation result shown); failures injected at the network layer (a slow answer, an unavailable component, a network failure, a contract violation and an integrity violation, a simulation stopped at its deadline or refused for lack of a worker, a page whose code fails to load); keyboard-only use (every simulator field by Tab and Shift+Tab against both sticky bars); axe's WCAG 2.2 A/AA rules on every page in both themes, with tables and disclosures open, on a simulation result, a failed run, the error summary of two invalid fields and the phone layout; the current page in forced colours; no CSP violation; a phone viewport; the Forecasting page's dates in the Pacific/Kiritimati and America/Los_Angeles time zones; the footer's licence notices; the private and public-demo nginx sites (accounts, simulations, the read-only demo) |
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
- **Methods links** open the current documentation on GitHub's `main` branch, which can
  differ from the release being viewed (its own copy is its `docs/` directory); the page
  says so.

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

M7 had two independent reviews, each followed by fixes.

**First review.** Four independent reviewers (separate agents, read-only, each with one
brief) examined M7: scientific integrity, numbers and API integration; user experience,
accessibility, mobile and performance (with screenshots of every page at 1280 and 390 px,
light and dark, and the public-demo state); security and deployment (rendered sites, the API
mount probed in-process); tests and CI. Every finding was checked against the code before it
was fixed. Most fixes came with a test that fails without them (`web/src/views/review.test.tsx`,
the e2e specs, `tests/test_m7_*.py`). The second review found several that had none; tests
were then added for the client's simulation deadline and its message, the static rate, the
single announcement of a run, the current page in forced colours, the redraw cost, Escape
on the menu and the colour tokens. Still without a test: the wording for an independent
check that did not run, merged body errors on one field, the line pattern each forecasting
series is given, the status word that broke mid-word in a tile, the nit-level wording and
layout fixes, and the refusal of a checkout by `render --apply` on a server (it runs only as
root on a server; the refusal of a directory outside the releases is tested).

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

**Second review.** A second, larger review followed those fixes, through six lenses:
scientific integrity, accessibility and user experience, security and deployment, tests and
CI, documentation, and completeness against the brief. Each finding was then checked by two
independent skeptics, who tried to refute it, reproduced it where they could (the API mount
in-process, real nginx, Chromium, mutations of the code) and proposed the smallest fix. Of 75
findings, 69 were confirmed, 5 were refuted and 1 was disputed. The confirmed ones are below,
grouped (several findings described the same defect). The severity is the reviewer's unless
both skeptics agreed on another. The last column names where a fix's test lives
(`web/src/views/round2-plan.test.tsx`, `web/src/views/round2-views.test.tsx`,
`web/src/round2-shell*.test.tsx`, `web/src/round2-tests-*.test.ts(x)`, `review.test.tsx`, the
e2e specs, `tests/test_m7_deploy.py`, `tests/test_m7_proxy.py`); for every row marked Vitest
or pytest, at least one of its tests was checked to fail with the fix reverted. "none" says
how the fix was checked instead.

| Finding | Severity | Fix | Test |
|---|---|---|---|
| **Scientific integrity and wording** | | | |
| The binding-constraint note said relaxing a binding limit would change the plan, counted a missed terminal-backlog target as a binding limit, and showed an empty list as a bare heading | minor | M4's own meaning (limits left with no slack, not causes); `terminal_backlog_violated` and `budget_exceeded` shown apart, tagged DIAGNOSTIC; the M4 report's footnote under the Decisions table; "None reported for this plan." | Vitest |
| A DIAGNOSTIC plan was said to have passed a re-check of "every constraint", though the API did not check its terminal target | minor | "every constraint except …", from the API's `constraints_not_checked` (now in the schemas) | Vitest |
| A DIAGNOSTIC plan that is one of several equally scored plans was not described as such (the first fix covered strict plans only) | minor | the plan's wording and the "One of several equally scored" tag for relaxed plans too | Vitest (plan, Decisions); none (Overview) |
| The equal-cash sentence took its direction from solver noise ("0 request-days more") and did not say that this comparison drops the terminal target | minor | direction and number from one rounded value ("as many request-days as"); the sentence says the HYPOTHETICAL target is dropped | Vitest |
| The Overview's H1 tile called a negative MAE difference the model's "lead", with no sign convention | minor | "MAE difference, model minus best baseline (H1)", "Negative means the model is more accurate"; the API's value unchanged | Vitest |
| The Uncertainty page stated two pre-registered criteria; M3 has three, and the horizon-bucket condition that fails was left out | minor | the three conditions as UNCERTAINTY.md states them, tolerances from the API, the buckets outside tolerance named, the HAC test's verdict in the table | Vitest |
| Static text called every demand path the point forecast, the M3 quantile stress paths included | nit | the API's wording (an unvalidated point forecast or a labelled per-day stress path, with no probability) on Decisions and the Simulator | Vitest |
| "Cash vs baseline" had no unit or direction | nit | "(USD)", signed amounts, "this scenario minus the baseline" | Vitest |
| An answer the dashboard refused, or that never arrived, was reported as "Simulation not run" | nit | "Simulation result not shown" when the server may have completed the run | Vitest |
| The limitations panel's failure said "No number is shown without them" while numbers were shown | nit | names the requests that failed; says when the list is incomplete | Vitest |
| **Provenance and completeness** | | | |
| Results without their provenance (the Overview status tiles, the exceedance statuses, the simulator's reference values); the sources list left out the observed snapshot and two M4 files; the history's notices were never shown | minor | footers added; the Overview status row has none, its sources being in the footers of the panels below it (section 3); sources merged per file from every view's answers; the observed data's notices shown with it | Vitest |
| "Models against baselines" told the recommended model and the best baseline apart by colour alone, and dropped a role when one model held both | minor | roles in words on the bars, the summary and the table rows, both kept; the legend lists only the colours drawn | Vitest |
| Chart tables lacked series their chart draws (the policies' backlog; the band chart's observed days) | minor | the policies' columns, joined by date; the band table's caption says where the observed days are | Vitest (plan table); none (caption) |
| The horizon-accuracy panel went blank, with no loading message, while another period loaded; the scenario detail never said it was slow | minor | loading and slow states, never another period's numbers | Vitest (Forecasting); none (Decisions) |
| **Accessibility, keyboard and mobile** | | | |
| On phones the sticky simulator action bar wrapped taller than its scroll padding and hid focused fields and the failed-run message (the first fix was incomplete) | major | the bar is sticky only from 40rem wide and 32rem high; the list of changes sits above it | Vitest (the list's place); the CSS measured in Chromium |
| The phone menu opened off-screen when the page was scrolled | major | it opens over the page under the top bar, whose height is measured | Vitest (the measured height); the CSS measured in Chromium |
| At 320 px the top bar's tags overlapped the ATLAS link | minor | the word ATLAS hidden below 360 px (the link keeps its name) | none: measured in Chromium at 320-420 px in five profiles |
| Cancel and Retry dropped the focus to the page; a cancelled run was not announced | minor | the focus returns to Run (focusable while it runs) or stays in the retried section; "Simulation cancelled." announced | Vitest |
| Scenario links in the feasibility table scrolled to the detail but left the focus behind, and a modified click could not open a new tab | minor | the focus moves to the detail heading; modified clicks are left to the browser | Vitest |
| In the public demo the only tab stops were 21 identical "Reference assumption" summaries, and the text still invited edits | minor | each summary names its field; read-only wording | Vitest |
| In forced colours the chosen option of a segmented control and the tags' line keys were not shown | minor | a Highlight outline; a CanvasText key | none: checked with emulated forced colours |
| With the menu open, Escape on a chart closed the menu and moved the focus | nit | Escape closes the menu only from the menu or the top bar; the focus moving into the page closes it | Vitest |
| A field reached from the error summary had its label under the top bar | nit | a scroll margin on fields | none (CSS) |
| The simulation result skipped a heading level | nit | the plan's headings follow the page (h3, h4 on Decisions) | Vitest |
| The top bar did not say when a local API cannot run simulations | nit | "Local API · read-only" | Vitest |
| **Charts** | | | |
| The archived projection line sat on its own bands at 2.2-2.4:1, below 3:1 | minor | a surface-coloured halo under every line drawn over bands | Vitest |
| On phones point markers hid the horizon chart's line patterns, and its x axis had one tick | minor | markers only where they leave the patterns visible; at least three ticks | Vitest |
| A chart reading kept a stale position when the data changed ("Thu, Jan 1, 1970") | minor | the reading is dropped when the number of points changes, so it can no longer name a day outside the data | Vitest |
| Chart labels placed with fixed pixel budgets escaped their panel on phones and under text spacing | minor | label room sized from the labels; a rule's label on the side with room | Vitest |
| **Security and deployment** | | | |
| nginx's own JSON error answers took a type from the path's extension on Ubuntu (the distribution's `mime.types`); the test nginx had no such types and hid it | minor | `types { }` at server level; the test nginx has the distribution's types | pytest (real nginx) |
| `render --apply` published any directory named like a release, without checking its location or its `release.json`, and refused `/opt/atlas/current` | nit | only an installed release whose build matches its `release.json`; `current` resolved | pytest |
| A malformed dashboard manifest made `render`, `validate` and `plan-upgrade` fail as an internal error (exit 3) | nit | an error naming the release (exit 2) | pytest |
| `verify` skipped links to directories in the published copy | nit | the same walk as publication: any link or non-regular file fails | pytest |
| nginx never compressed the pages (a charset inside `default_type`) | nit | a bare type and `charset utf-8` | pytest (real nginx) |
| **Tests and CI** | | | |
| First-review fixes without a test that fails without them (the client's simulation deadline, the static rate, the single announcement, the forced-colours page mark, the redraw cost, Escape on the menu), while this section said each had one | minor | tests added; this section corrected (above) | Vitest, e2e, pytest |
| No real test of several WCAG 2.2 criteria, non-text contrast, colour-vision deficiency, forced colours or runtime performance | minor | axe's WCAG 2.2 rules everywhere (target size included); token contrast and colour-vision tests; the forced-colours page mark; a count of line rebuilds | Vitest, e2e |
| The sticky-bar keyboard test checked 11 of 19 fields and never the top bar | minor | every enabled field, by Tab and by Shift+Tab | e2e |
| The e2e test named for a contract violation injected only an integrity flag | minor | a real contract violation added; the other one renamed | e2e |
| The Forecasting half of the "H1 found by name" fix was unguarded | minor | a test with the API's tests listed in reverse | Vitest |
| **Documentation** | | | |
| "Unknown paths show a not found page": the servers answer a JSON 404 | minor | corrected (section 3) | — |
| The fixture-recording command exited 1 (the whole-package coverage gate) | minor | `--no-cov` | — |
| The documented Node.js minimum was below what the lock declares | minor | 22.22.2 or 24.15, in the docs, `engines` and CI | — |
| The browser tests' prerequisites (Chromium, nginx, openssl, free ports) were not documented | minor | section 11 and DEVELOPMENT.md | — |
| Statements stronger than the code: a slider for every bounded input, no "recommended" anywhere, a Permissions-Policy denying every powerful feature, axe covering WCAG 2.2, 24 px targets, the descriptor in the static rate zone, "at most twice" read as the whole rate before authentication, the Uncertainty page's endpoints | nit | each stated as the code does it (sections 3-9) | — |
| The upgrade notes required creating `/opt/atlas/dashboard`, and said the M6 tool fails on an M7 release in every profile | nit | activation creates it; the M6 tool refuses only in the exposed profiles | — |
| DEPLOYMENT.md: a rollback by hand leaves nginx on the newer release's dashboard (not said); the access matrix and the exposure control without the dashboard; stale test counts; the container test said to check nginx over HTTP after both rollbacks | nit | corrected | — |

## 17. M8 release audit: corrections

The M8 release audit of `194fa4a` ([M8_FINDINGS_REGISTER.md](audit/M8_FINDINGS_REGISTER.md))
found one MEDIUM and several LOW defects in the dashboard. Each correction below has a test
that fails on `8f077ed` (the audit's base) and passes with the correction (for M8-11, the
dashboard's sentence; this document's status has none); most are in
`web/src/views/m8-audit.test.tsx`. No research result, API answer or fixture changed.

| Finding | Severity | Correction | Test |
|---|---|---|---|
| M8-05: the build shipped minified MIT and ISC code and the OFL font without their copyright and licence texts | MEDIUM | every build ships `third-party-licences.txt` (Vite's `build.license`) and `inter-licence.txt` (the font package's licence), listed in the manifest, served as text by the API and nginx, linked from the footer; inventory in section 2 | `web/build/notices.test.ts`, `test_m7_dashboard.py`, `test_m7_deploy.py` (one nginx location for each notice), Vitest (footer), e2e (the links open the notices) |
| M8-27: with two or more client-side field errors, the error summary's links were 16 px targets on a 20 px pitch (WCAG 2.2, 2.5.8) | LOW | the links are at least 24 px high | e2e: axe on the summary of two invalid fields, and each link's height |
| M8-28: the Reproducibility table gave the legacy window's target days to Jul 29, 2026 (the last origin + 30, past the data), and the model ranking showed legacy-window MAE without its caveat | LOW | the scored range, cut at the snapshot's last day from the API, with a caption that says so; the "descriptive only" callout in the ranking panel too | Vitest |
| M8-29: the "whatever the time zone" date test ran only in CI's UTC, where removing the UTC formatting passed | LOW | Vitest runs in America/Los_Angeles; the date module is also loaded and checked in UTC−11, UTC−7 and UTC+14; the browser tests read the Forecasting page in Pacific/Kiritimati and America/Los_Angeles | Vitest (fails on both mutations: no UTC formatting, local-time parsing); e2e |
| M8-30: most asynchronous queries of the view tests waited Testing Library's default 1 s, too short for a first page render on a loaded machine | LOW | 5 s for every query (`src/test/setup.ts`) | Vitest (the setting) |
| M8-31: the Methods links pointed at the moving `main` branch while the page shows one release's fingerprints | LOW | the links say that they open the current documentation on `main`, and the page says where the release's own copy is (its `docs/` directory). A link pinned to the release is not possible yet: a build cannot name the commit it is committed in, and a release tag exists only after the owner approves a release | Vitest |
| M8-34: the Overview heading used "decision-intelligence" marketing language | LOW | "NYC 311 demand: forecasts, uncertainty and hypothetical scenarios" | Vitest, e2e |
| M8-18 (dashboard part): "failed" and "uncalibrated" were written into the Uncertainty page whatever status the API served | LOW | the words are chosen from the served status (section 6); the navigation reads "M3 intervals and their calibration"; an unused callout with fixed wording was removed | Vitest (each status, and a status that cannot be loaded) |
| M8-19 (dashboard part): a certificate ratio was shown as "gap 6.1e-9", and each plan was called "the exact optimum" with "a zero duality gap" | LOW | "largest violation 6.1e-9 of its tolerance" (the largest of the certificate's four ratios); the tolerances as `optimizer.certify` applies them ([OPTIMIZATION.md](OPTIMIZATION.md) section 4): primal feasibility within 1e-7 + 1e-9 × magnitude, dual feasibility and the duality gap within 1e-9 × magnitude; "an optimum of an assumed problem within those tolerances, and one of several when the optimum is not unique" | Vitest (also a scenario whose largest ratio is stationarity, not the gap) |
| M8-11 (dashboard part): this document still said M7 awaited review; the Methods page claimed "reproduction of every legacy figure" | LOW | the status above; "within its published precision (one RMSE rounding is a declared exception)" | Vitest (the Methods text) |
| Observation: the calibration chart's "Perfect calibration" key was declared dashed, and never drawn (a one-series chart had no legend) | — | the legend is drawn whenever it has two entries; the key is dotted, like the diagonal | Vitest |

The statements of section 14 on what is not tested (screen readers, Firefox, Safari) still
hold.
