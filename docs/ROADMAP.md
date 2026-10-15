# Roadmap and deferred work

Each milestone requires separate approval. M0-M3 are merged; M4 is implemented on
`feature/atlas-m4-optimization` and awaits review. Nothing from M5 onward is implemented.

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

## M4: Hypothetical decision simulation and optimisation (implemented; awaiting approval)

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

## Later milestones (deferred, outline only)

- **M5:** FastAPI service with typed contracts.
- **M6:** hardened deployment on a small Ubuntu VPS.
- **M7:** lightweight dashboard, model card and data card.

## Known M0 limitations

- **CI action pinning:** CI pins GitHub Actions to major-version tags
  (`actions/checkout@v4`, `actions/setup-python@v5`). Pinning to commit SHAs was not possible
  offline and should be done before M5. uv itself is installed from a hash-pinned
  requirements file.
- **No dependency cooldown:** the lockfile was resolved without a release-age cooldown
  (`exclude-newer`).
- **No LICENSE file:** the code licence and the NYC Open Data attribution wording need an
  owner decision.
- **Legacy README claims:** the legacy README still contains the original headline claims. A
  notice at its top points to this documentation; the claims themselves are not rewritten in
  M0.
- **Legacy-specific reproduction:** reproduction rules exist only for `legacy-v0`. Other
  snapshots report the stage as "not applicable".
