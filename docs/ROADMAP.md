# Roadmap and deferred work

Each milestone requires separate approval. M0 and M1 are merged; M2 is implemented on
`feature/atlas-m2-backtesting` and awaits review. Nothing from M3 onward is implemented.

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

## M2: Forecasting and backtesting (implemented; awaiting approval)

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

Deliberately not done in M2:

- **Seasonal naive with drift:** replaced by seasonal exponential smoothing, which adapts the
  level instead of extrapolating a slope.
- **Seed in the run registry:** M2 has no randomness, so there is no seed to record.
- **Scoring the archived July 2026 forecast:** it needs data after 2026-06-30, which would be
  a new extraction (not authorised).

## Later milestones (deferred, outline only)

- **M3:** calibrated, horizon-specific prediction intervals and exceedance probabilities in
  place of fixed risk tiers.
- **M4:** scenario analysis and a small backlog optimisation model. Its operational
  parameters are explicitly `HYPOTHETICAL` unless evidence is supplied.
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
