# Roadmap and deferred work

Each milestone requires separate approval. M0 is merged; M1 is implemented on
`feature/atlas-m1-data-features` and awaits review. Nothing from M2 onward is implemented.

## M0: Foundation (this milestone)

- `src/atlas` package, `pyproject.toml`, `uv.lock`, ruff, strict mypy, pytest, CI.
- `legacy-v0` snapshot:
  - SHA-256 manifest and provenance;
  - strict offline loading and fail-closed data contracts;
  - declared known exceptions.
- Reproduction of the six historical aggregate tables, all 14 KPIs and all 10 model
  metrics, each with its meaning stated.
- Cross-checks of the six legacy output tables against the base data.

## M1: Data and features (implemented; awaiting approval)

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

## M2: Backtesting (deferred)

- Rolling-origin evaluation of the forecast actually delivered, recursive or direct, at every
  horizon from 1 to 30, with expanding windows and a fixed origin schedule.
- Fair baselines with the same information: naive, weekly seasonal naive at horizon h,
  seasonal naive with drift, and the mean or median of recent same weekdays. The legacy
  regression is re-scored under the same protocol.
- Metrics by horizon (MAE, RMSE, bias, WAPE, MASE), and Diebold-Mariano comparisons against
  the seasonal-naive baseline. Test-set R² will not be reported.
- A run registry recording, for every run, the data snapshot hash, configuration hash, git
  SHA, seed and library versions.
- If approved: score the archived July 2026 forecast against the realised July 2026 data.

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
