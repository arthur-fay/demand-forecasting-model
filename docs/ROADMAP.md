# Roadmap and deferred work

Each milestone requires separate approval. Nothing from M1 onward is implemented in M0.

## M0: Foundation (this milestone)

- `src/atlas` package, `pyproject.toml`, `uv.lock`, ruff, strict mypy, pytest, CI.
- `legacy-v0` snapshot:
  - SHA-256 manifest and provenance;
  - strict offline loading and fail-closed data contracts;
  - declared known exceptions.
- Reproduction of the six historical aggregate tables, all 14 KPIs and all 10 model
  metrics, each with its meaning stated.
- Cross-checks of the six legacy output tables against the base data.

## M1: Data and features (deferred)

- A single calendar convention (ISO 8601, Monday = 1) for new tables; legacy tables keep
  theirs.
- Anomaly and event flags, recorded as flags rather than silent edits:
  - robust outlier scores;
  - intake gaps such as HPD on 2024-04-11 to 2024-04-14;
  - a holiday calendar known in advance.
- One feature-building function shared by training, backtesting and forecasting, with tests
  that no feature uses data after the forecast origin.
- An explicit "unassigned borough" component, so totals and segments reconcile by
  construction. Its daily total is already available as the measured residual; an
  agency-level breakdown needs a new extraction, and network access requires approval.
- Optional live ingestion: a paginated Socrata client with a truncation guard, retries, an
  app token kept as a secret, and immutable snapshots with extraction metadata
  (`legacy-v1`, ...).

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
