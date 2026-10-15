# Roadmap and deferred work

Each milestone requires separate approval. M0-M4 are merged; M5 is implemented on
`feature/atlas-m5-backend-api` and awaits review. Nothing from M6 onward is implemented.

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

## M5: HTTP API (implemented; awaiting approval)

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

## Later milestones (deferred, outline only)

- **M6:** hardened deployment on a small Ubuntu VPS.
- **M7:** lightweight dashboard, model card and data card.

## Known M0 limitations

- **CI action pinning (resolved in M5):** CI pinned GitHub Actions to major-version tags.
  M5 pins them to the commits those tags pointed to: `actions/checkout` v4.4.0 and
  `actions/setup-python` v5.6.0. uv itself is installed from a hash-pinned requirements file.
- **No dependency cooldown:** the original lockfile was resolved without a release-age
  cooldown (`exclude-newer`). Since M4, new packages are held to releases at least two weeks
  old through `tool.uv.constraint-dependencies` (see `pyproject.toml`).
- **No LICENSE file:** the code licence and the NYC Open Data attribution wording need an
  owner decision.
- **Legacy README claims:** the legacy README still contains the original headline claims. A
  notice at its top points to this documentation; the claims themselves are not rewritten in
  M0.
- **Legacy-specific reproduction:** reproduction rules exist only for `legacy-v0`. Other
  snapshots report the stage as "not applicable".
