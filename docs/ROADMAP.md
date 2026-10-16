# Roadmap and deferred work

Each milestone requires separate approval. M0-M7 are merged (M7, the web dashboard, in
`194fa4a`); M6 phase B (a real deployment) needs separate authorisation (DEPLOYMENT.md
section 18). M8, the final audit and its corrections, prepares release candidate 1.0.0,
which is under review.

## M0: Foundation (merged)

- `src/atlas` package, `pyproject.toml`, `uv.lock`, ruff, strict mypy, pytest, CI.
- `legacy-v0` snapshot:
  - SHA-256 manifest and provenance;
  - strict offline loading and fail-closed data contracts;
  - declared known exceptions.
- Reproduction of the six historical aggregate tables, all 14 KPIs and all 10 model
  metrics, each with its meaning stated.
- Cross-checks of the six legacy output tables against the base data.

## M1: Data and features (merged)

See [FEATURES.md](FEATURES.md) and [DATA_QUALITY.md](DATA_QUALITY.md).

- ISO 8601 conventions (Monday = 1, ISO weeks) for all new data; legacy tables keep theirs
  and are converted explicitly.
- `daily_borough_components`: five boroughs plus the `UNASSIGNED` aggregate residual, so
  totals and segments reconcile by construction. No agency-level residual is invented.
- Causal data-quality flags: missing periods, city-wide surges and low volume, segment intake
  interruptions and catch-up surges, with holiday context. The HPD gap of 2024-04-11 to
  2024-04-14 is detected.
- Federal holidays by rule and declared events gated by announcement date.
- One feature builder shared by training, direct and recursive forecasting, with tests that no
  feature uses data after the forecast origin.

Still deferred (not part of the approved M1 scope, which excludes network access):

- Live ingestion: a paginated Socrata client with a truncation guard, retries, an app token
  kept as a secret, and immutable snapshots with extraction metadata (`legacy-v1`, ...).
- An agency-level breakdown of the residual, which needs a new extraction.
- New York City and New York State observances that are not federal holidays.

## M2: Forecasting and backtesting (merged)

See [BACKTESTING.md](BACKTESTING.md).

- Rolling-origin evaluation of the forecast actually delivered (recursive for the
  regressions, direct for the baselines) at every horizon from 1 to 30, with expanding
  windows, daily origins and a refit at every origin.
- Baselines with the same information: naive, horizon-correct weekly seasonal naive, mean
  and median of recent same weekdays, and seasonal exponential smoothing. The legacy
  regression is re-implemented exactly (it reproduces all 120 published predictions) and
  re-scored under the same protocol, next to a modernised regression.
- Metrics by horizon and bucket (MAE, RMSE, bias, WAPE, MASE, MAPE as secondary),
  improvement over seasonal naive and over the best baseline on common forecasts, and
  Harvey-Leybourne-Newbold Diebold-Mariano tests with HAC variances. Test-set R² is not
  reported.
- A pre-registered protocol: development selection sealed before a later confirmation
  period, a margin and significance rule for preferring a regression, and Holm-adjusted
  primary tests.
- Run records with configuration, data, code and spec fingerprints, git commit, package and
  BLAS versions; `atlas backtest verify` requires byte-identical results.
- An independent adversarial review (leakage, recursion and legacy fidelity, statistics and
  claims, mutation testing, requirements). Its confirmed findings were fixed; the amendments
  are listed in [BACKTESTING.md](BACKTESTING.md), section 9.

Deliberately not done in M2:

- **Seasonal naive with drift:** replaced by seasonal exponential smoothing, which adapts the
  level instead of extrapolating a slope.
- **Seed in the run registry:** M2 has no randomness, so there is no seed to record.
- **Scoring the archived July 2026 forecast:** it needs data after 2026-06-30, which would be
  a new extraction (not authorised).

## M3: Prediction intervals and probabilistic risk (merged)

See [UNCERTAINTY.md](UNCERTAINTY.md).

- Residuals of the M2 recursive forecasts, re-generated with the M2 engine at every origin,
  eligible for calibration only when `target_date <= calibration origin - latency`.
- Horizon-specific calibration with predeclared bucket pooling counted in distinct forecast
  paths, an effective-sample-size label (`thin`) and `unavailable` when there is too little
  history.
- Three interval methods (empirical additive, empirical relative, split conformal), each
  with a rolling (online) and a fixed calibration scheme; Gaussian one-step and climatology
  baselines.
- Exceedance probabilities from explicit predictive distributions for the trailing-180-day
  demand percentile (recomputed at each origin) and for user-supplied thresholds that must
  be labelled `HYPOTHETICAL`.
- A pre-registered protocol (method selected on M2's development targets, sealed, evaluated
  on M2's confirmation period, which is labelled as already exposed), dependence-aware
  scores and decision-grade criteria; `atlas uncertainty run|verify|inspect|project`.

Deliberately not done in M3:

- **Quantile regression or full distributional models:** they would need new dependencies or
  new model specifications; M3 calibrates the M2 forecasts instead.
- **Monotone widening across horizons:** not imposed; the report measures it.
- **Scoring the archived July 2026 forecast:** still needs data after 2026-06-30.

## M4: Hypothetical decision simulation and optimisation (merged)

See [OPTIMIZATION.md](OPTIMIZATION.md).

- A linear programme of daily service, overtime, surge and backlog (SciPy HiGHS dual
  simplex), one city-wide unit (the domain model supports several units sharing a budget),
  every operational parameter `HYPOTHETICAL` with an exact unit and assumption.
- Certified optima (primal and dual feasibility, zero duality gap), a matrix-free check of
  every plan, labelled relaxed plans and diagnostics for infeasible scenarios, explicit
  tie-breaks (least cash, then terminal backlog, then request-days), a uniqueness test over
  the optimal face (no other optimal plan farther than 0.001), shadow prices bracketed by
  finite differences.
- Fixed-capacity and backlog-trigger policies under the same assumptions, LP comparisons at
  equal cash and equal backlog, tradeoff frontiers, sensitivity sweeps with the proved
  monotonicity properties checked; nine scenarios including an M2-bias shift and an M3
  marginal-quantile stress path; the M3 calibration status on every result;
  `atlas optimize run|verify|inspect`.
- An independent six-lens review after the freeze (including a re-implementation and
  mutation testing), then two further independent reviews of the fixes and a verification of
  the last round; every confirmed defect fixed with a regression test. Reference results:
  [`docs/results/atlas-m4-optimization-v1/`](results/atlas-m4-optimization-v1/report.md)
  (OPTIMIZATION.md sections 9-10).

Deliberately not done in M4:

- **Chance constraints or stochastic programming:** the M3 intervals failed calibration, so
  no probabilistic guarantee could be supported.
- **Agency-level units:** no agency capacity data exist; the configuration accepts one
  city-wide unit.
- **Verified parameters:** no evidence register exists, so every parameter is `HYPOTHETICAL`.
- **Integer staffing, fixed costs or shift rules:** they need a MILP and data ATLAS lacks.

## M5: HTTP API (merged)

See [API.md](API.md) and the contract [`docs/api/openapi.json`](api/openapi.json).

- A FastAPI modular monolith (`atlas.api`): routers and Pydantic contracts (transport),
  a reference store, views and a simulation service (application), and the unchanged M0-M4
  modules (domain), wired by dependency injection.
- Fourteen documented endpoints (twelve under `/api/v1`, plus unversioned health probes):
  - health and readiness, system metadata;
  - the M2 models, projection, observed history and backtest metrics;
  - the M3 calibration status and projection quantiles;
  - the M4 reference scenarios, one scenario's detail, a scenario comparison;
  - bounded HYPOTHETICAL simulations.
- Every response carries an `integrity` block. It states observed, projected, historical or
  HYPOTHETICAL, and the M3 status `failed_calibration`. Its `validated_recommendation` and
  `point_forecast_validated` flags are fixed to `false` by the schema. The `provenance` block
  names every committed source with its fingerprints.
- The committed outputs are loaded once at startup through M4's own provenance checks and
  `optimize inspect` (no recomputation), with per-component readiness, so damaged artifacts
  fail closed.
- Simulations go through M4's configuration parser, scenario builder and `analyse_scenario`.
  They reproduce the reference scenarios exactly and run in spawned worker processes. The M4
  solve layer is not thread-safe; the processes also allow hard deadlines and keep metadata
  requests unblocked. Inputs are bounded, there is no queue (503), and a deadline kills the
  worker (504). Lost workers are replaced, logged, counted and reported by readiness.
- Honest by construction: the M2 H1 result (not significant after Holm) travels with every
  response serving M2 numbers; intervals are labelled as failing calibration; holiday-related days
  carry M3's flag; comparisons never subtract a DIAGNOSTIC plan from a feasible one; a test
  applies the M4 wording rules to every string the API sends.
- Security:
  - strict validation (bodies, query parameters) and body limits;
  - a request-read deadline per connection and a JSON in-flight cap;
  - a host allow-list (IPv6 included), explicit CORS with JSON rejections, security headers;
  - an optional API key, and refusal of a public bind without one;
  - a generic 500 with the traceback only in the log;
  - no filesystem paths from requests, no writes, no shell or outbound access; the only
    processes started are the fixed-argument simulation workers.
- Structured JSON logging; `atlas api serve|openapi|bench`; measured latency and memory
  (API.md section 9).
- GitHub Actions are pinned to commit SHAs (the M0 limitation below).

Deliberately not done in M5:

- **Deployment, TLS, per-client rate limiting:** M6, behind a reverse proxy.
- **Interactive shadow prices, frontiers or sweeps:** research analyses of several seconds
  each; served precomputed for the reference scenarios only.
- **User accounts:** one optional shared key protects the only computing endpoint; GET
  endpoints serve committed research outputs.

## M6: Secure deployment, phase A (merged)

See [DEPLOYMENT.md](DEPLOYMENT.md).

- Native systemd deployment on one Ubuntu 24.04 LTS server, chosen over containers for a
  small VPS (no daemon memory, no ufw bypass, comparable isolation measured): the M5 API
  under an unprivileged account, sandboxed (`systemd-analyze security` exposure 0.9),
  loopback-only, `Type=notify` with a watchdog, bounded restarts, resource limits by
  profile (`low`, `standard`).
- nginx (Ubuntu) as the only entry point: TLS (certbot), HSTS, HTTP Basic accounts on every
  route, a route allow-list generated from the committed OpenAPI contract, per-client
  request, connection and simulation limits, read deadlines and body limits, JSON errors, a
  header allow-list; the simulation key added by the proxy only, on a Unix socket only the
  service can create. Private-first profiles (`tunnel`, `private`); a `public-demo` profile
  separated from computation and operational detail, refused without a security review.
- A read-only preflight (standard library only) that refuses unsuitable servers with
  actions, including the x86-64-v2 requirement of the locked NumPy and the lock's wheels.
- Immutable releases built from an exact commit with the locked dependencies, checked as
  the service user (snapshot, M4 reproduction byte for byte, contract), described by a
  manifest of fingerprints; activation with verification (health, identity, scientific
  integrity, access control, exposure) and automatic return to the last verified state
  (release and configuration); recovery, pruning, upgrade plans; secrets as systemd
  credentials.
- An independent three-lens review (security, reliability, documentation and tests); every
  confirmed finding fixed (DEPLOYMENT.md section 20).
- Lightweight monitoring (a systemd timer) with an optional alert hook; persistent, capped
  logs without secrets.
- Tests with real nginx, openssl and systemd-analyze, real API processes, and an end-to-end
  test on a disposable systemd server (CI job `deploy-e2e`).

Deliberately not done in M6 phase A:

- **Any real deployment, DNS, firewall or certificate change:** phase B, separately
  authorised.
- **Containers, orchestration, managed services:** not justified on one small server.
- **A metrics stack (Prometheus, Grafana):** the timer-driven monitor covers the needs.
- **Public exposure:** the public-demo profile exists, refused until a security review.

## M7: Interactive web dashboard (merged)

See [DASHBOARD.md](DASHBOARD.md).

- A static single-page application in `web/` (React 19, TypeScript strict, Vite), built
  reproducibly into the committed `web/dist` with a manifest of every file's SHA-256; the
  production server needs no Node.js. Charts are the dashboard's own SVG (d3-scale and
  d3-shape only), each a keyboard control with a data table.
- Six pages: Overview, Forecasting, Uncertainty and risk, Decision optimisation, the
  Simulator, Methods and provenance. Every result shown is a field of a validated API answer
  (the page chrome states two fixed facts of this release: the snapshot's end date and the
  number of reference scenarios);
  the integrity statements sit next to the numbers they qualify (M2 not significant after
  Holm, M3 FAILED_CALIBRATION, M4 HYPOTHETICAL, archived projections, exploratory
  simulations), with provenance under every panel of results.
- API integration from the contract: generated types, runtime schemas checked against them
  at compile time, typed errors (network, timeout, HTTP, contract, integrity), a refusal of
  any answer claiming validation, no fallback numbers; component tests on fixtures that a
  Python test keeps byte-identical to the API's answers.
- The simulator: a form built from the API's ranges, validation on both sides, only changed
  inputs sent, cancel, every server error explained; disabled with a reason in the public
  demo, behind an API key, or when the deployment cannot be read.
- Security: a strict Content-Security-Policy for HTML (self only, Trusted Types, no inline
  code); no credential in the browser; a static route class serving exact file names only
  (in the API from memory, in nginx from a verified per-release copy with
  `disable_symlinks on`, its own rate zone, behind the same accounts); profiles kept
  distinct by a deployment descriptor.
- Deployment: the build in every release manifest, published and verified at activation
  before the site points to it, checked by `atlas-deploy verify`, moved with the API by
  every rollback, pruned with its release.
- Accessibility (WCAG 2.2 AA target: axe, keyboard, reduced motion, forced colours, 320 px
  reflow), light and dark themes, measured size budgets enforced in CI.
- Tests: Vitest, Python (manifest, API mount, nginx, deployment), Playwright against the real
  API and both nginx profiles, the disposable-server end-to-end test; CI job `dashboard`.

Deliberately not done in M7:

- **A model card and a data card as separate documents:** the Methods and provenance page
  links each milestone's documentation and shows the snapshot's provenance; formal cards are
  left for M8.
- **User accounts, roles, saved simulations:** no storage of any kind in the browser or the
  server.
- **Simulations in the browser through the SSH tunnel:** the API requires a key there, and a
  browser must never hold one.
- **Live data:** the dashboard shows the committed, archived outputs only.

## M8: Final audit, corrections and release candidate (under review)

See [the M8 audit](audit/README.md).

- A release audit of `194fa4a` (`main` after the M7 merge), recorded before any correction:
  a baseline assessment, a register of verified findings with their evidence, and the
  checks that held up.
- Corrections of the verified findings, each with a regression test or a recorded check.
  The research results are not changed; where a finding concerns a committed result, the
  correction is an erratum in the documentation (for example the size of the M2
  Diebold-Mariano test, [BACKTESTING.md](BACKTESTING.md), section 12).
- The README rewritten as an account of ATLAS, with the original July 2026 project kept as
  an archived, qualified section.
- Release candidate 1.0.0, under review. The code carries the version being released
  (1.0.0). The candidate status is carried by the release-candidate manifest
  ([release/RELEASE_CANDIDATE.md](release/RELEASE_CANDIDATE.md)) and by a tag `v1.0.0-rc.1`,
  which only the owner creates, after approval.
- Release documents: a [model card](MODEL_CARD.md), a [data card](DATA_CARD.md), the
  [known limitations](KNOWN_LIMITATIONS.md), the [release gate](release/RELEASE_GATE.md) with
  its acceptance matrix, and a [phase B runbook](release/VPS_PHASE_B_RUNBOOK.md) (prepared,
  not executed).
- A final independent review of the corrected candidate, in two rounds. Its findings were
  corrected before the candidate was fixed
  ([audit/M8_CORRECTIONS.md](audit/M8_CORRECTIONS.md) section 7).
- Verdict: **CONDITIONAL GO** for a private-first research demonstration. NO-GO for
  operational use and for the `public-demo` profile. A real deployment (phase B) is not
  authorised. Owner actions:
  - the licence and the NYC Open Data terms, due now because the repository is public;
  - accepting the disclosed limitations;
  - the tags;
  - the first run of the research-reproduction workflow after the merge;
  - the dependency caps' review dates.

**Deferred to a future protocol version** (each needs new data or would change committed
results):

- a correctly sized and dependence-robust comparison test, pre-registered before any run;
- interval quantiles with a finite-sample correction, and coverage tests whose degrees of
  freedom follow the effective sample size;
- a new snapshot after 2026-06-30, with recorded extraction metadata, for an untouched
  evaluation;
- the generator wording that M8 corrected only by errata (M8-15, M8-17).

## Known M0 limitations

- **CI action pinning (resolved in M5):** CI pinned GitHub Actions to major-version tags.
  M5 pins them to the commits those tags pointed to; M8 moved the pins to releases that run
  on Node.js 24 (`actions/checkout` v7.0.1, `actions/setup-python` v7.0.0,
  `actions/setup-node` v7.0.0, `actions/upload-artifact` v7.0.1), because GitHub deprecated
  the Node.js 20 runtime of the earlier pins. uv itself is installed from a hash-pinned
  requirements file. Updates follow [DEVELOPMENT.md](DEVELOPMENT.md), "Dependency
  maintenance".
- **No dependency cooldown:** the original lockfile was resolved without a release-age
  cooldown (`exclude-newer`). Since M4 a two-week cooldown is a review rule, not enforced by
  a tool: `tool.uv.constraint-dependencies` in `pyproject.toml` only caps three named
  packages (optype, starlette and truststore), each held until its review date; it does not
  hold back any other new or upgraded package. See [DEVELOPMENT.md](DEVELOPMENT.md).
  (**M8 correction:** this previously said that the constraints hold every new package to
  releases at least two weeks old.)
- **No LICENSE file:** the code licence and the NYC Open Data attribution wording need an
  owner decision. (M8: the README now gives the attribution and states that the source's
  terms of use have not been verified. Both remain owner actions, and they are due now: the
  repository is public, so the release gate makes them a condition of the release.)
- **Legacy README claims (resolved in M8):** the README kept the original headline claims
  under a notice until M8. M8 rewrote it: it now describes ATLAS first and keeps the
  original project as an archived section in which every claim is qualified in place.
- **Legacy-specific reproduction:** reproduction rules exist only for `legacy-v0`. Other
  snapshots report the stage as "not applicable".
