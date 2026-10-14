# ATLAS development guide

ATLAS turns the original NYC 311 demand-forecasting project into a reproducible, tested
Python application. Milestone **M0** is the engineering foundation:

- a frozen, hash-verified record of the historical data (`legacy-v0`);
- an offline loader with fail-closed data contracts;
- recomputation of the legacy aggregate tables, KPIs and model metrics, plus cross-checks
  of the legacy output tables against the base data.

Milestone **M1** adds the data-quality and feature foundation for forecasting:

- ISO 8601 calendar conventions for all new data;
- an explicit `UNASSIGNED` residual that reconciles city-wide and five-borough totals;
- causal data-quality flags ([DATA_QUALITY.md](DATA_QUALITY.md));
- one leakage-safe feature builder for training, direct and recursive forecasting
  ([FEATURES.md](FEATURES.md)).

Milestone **M2** adds forecasting and backtesting ([BACKTESTING.md](BACKTESTING.md)):

- seven models behind one typed interface: four naive-family baselines, seasonal exponential
  smoothing, a faithful re-implementation of the legacy regression and a modernised
  regression, all built on the M1 feature builder;
- a rolling-origin engine that refits every model at every origin on data through that origin
  only, and evaluates the recursive procedure at horizons 1-30;
- a pre-registered protocol (development selection, sealed before a later confirmation
  period), dependence-aware statistics and versioned, fingerprinted run records.

Milestone **M3** adds uncertainty ([UNCERTAINTY.md](UNCERTAINTY.md)):

- residuals of the M2 recursive forecasts, eligible for calibration only once their target
  date is observed by the issue date (`target_date <= origin - latency`);
- three interval methods (empirical additive, empirical relative, split conformal), each
  with a rolling and a fixed calibration scheme, plus two uncertainty baselines;
- exceedance probabilities from explicit predictive distributions, for demand-percentile
  thresholds recomputed at each origin and for user-supplied `HYPOTHETICAL` thresholds;
- a pre-registered selection, a dependence-aware evaluation (coverage, width, interval
  score, pinball loss, WIS, Brier, reliability) and a decision-grade status that is withheld
  when the criteria or the evidence fall short.

Milestone **M4** adds a hypothetical decision simulation ([OPTIMIZATION.md](OPTIMIZATION.md)):

- a linear programme (SciPy HiGHS dual simplex) of daily service, overtime, surge and
  backlog under a discretionary budget and a terminal-backlog target, with every
  operational parameter declared `HYPOTHETICAL` in exact units;
- optimality certificates from primal and dual values, a matrix-free check of every plan,
  diagnostics and labelled relaxed plans for infeasible scenarios, a uniqueness test;
- fixed-capacity and backlog-trigger policies under the same assumptions, tradeoff
  frontiers, sensitivity sweeps with proved monotonicity checked, shadow prices bracketed by
  finite differences; the M3 calibration status on every result.

ATLAS does **not** provide an API or a dashboard, and it does not fetch data from the
network. M4 results are not validated recommendations. See [ROADMAP.md](ROADMAP.md).

## Requirements

- Python 3.12 (pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/) 0.11 or newer (CI installs 0.11.32, hash-pinned)
- No network access is needed after dependencies are installed.

## Install

```bash
uv sync --locked          # creates .venv with the exact versions in uv.lock
```

Runtime dependencies are deliberately small: `pandas`, `pydantic` and, from M4, `scipy`
for its bundled HiGHS linear-programming solver (NumPy and the rest come with them).
Development tools (`pytest`, `pytest-cov`, `hypothesis`, `ruff`, `mypy`, `pandas-stubs`,
`scipy-stubs`) are in the `dev` dependency group, installed by default. `optype`, a
typing-only dependency of `scipy-stubs`, is constrained to a release older than two weeks
when M4 was resolved (`tool.uv.constraint-dependencies`).

## Run the checks

```bash
uv run atlas check                    # integrity -> validation -> reproduction (CI entry point)
uv run atlas snapshot verify          # integrity only: SHA-256, size, header, rows, date ranges
uv run atlas validate                 # integrity + data contracts + declared exceptions
uv run atlas reproduce                # all three stages (same as check)
uv run atlas --format json check      # machine-readable report
uv run atlas snapshot manifest        # print the manifest computed from disk (never writes)
uv run atlas quality                  # M1: integrity -> validation -> residual and quality flags
uv run atlas features                 # M1: catalogue of the default forecast feature set
uv run atlas backtest legacy          # M2: refit the legacy model, compare with its outputs
uv run atlas backtest run --config configs/backtest/smoke.json --output-dir /tmp/smoke
uv run atlas backtest verify /tmp/smoke   # M2: re-run and require identical result files
uv run atlas forecast --config configs/backtest/smoke.json   # M2: labelled future projection
uv run atlas uncertainty run --config configs/uncertainty/smoke.json --output-dir /tmp/m3
uv run atlas uncertainty verify /tmp/m3   # M3: re-run and require identical result files
uv run atlas uncertainty inspect /tmp/m3  # M3: integrity and headline results, no recomputation
uv run atlas optimize run --config configs/optimization/smoke.json --output-dir /tmp/m4
uv run atlas optimize verify /tmp/m4      # M4: re-run and require identical result files
uv run atlas optimize inspect /tmp/m4     # M4: integrity and headline results, no recomputation
uv run python -m atlas --version      # module entry point
```

### Running the M2 protocol

The protocol in `configs/backtest/atlas-m2-rolling-origin-v1.json` is pre-registered. It is
run in two steps, and the second step refuses to run without the first:

```bash
# 1. development (selection) period only: writes selection.json next to the results
uv run atlas backtest run --require-clean --output-dir runs/v1-development

# 2. later periods, using the sealed development choice unchanged
uv run atlas backtest run --require-clean \
    --period development --period confirmation --period legacy_window \
    --sealed-selection runs/v1-development/selection.json --output-dir runs/v1-full
```

- Without `--period`, only the selection period runs.
- Any other period needs `--sealed-selection` (same configuration and data fingerprints, a
  structurally valid selection) and must be run together with the development period, whose
  recomputed selection must equal the sealed one in every decision field; otherwise the run
  fails, is marked incomplete and writes no `recommendation.json`.
- The period rules (at most one selection and one confirmation period, disjoint target
  windows) are checked when the configuration is loaded, whichever periods a run names.
- An existing `--output-dir` is refused before anything is computed.
- `atlas forecast --recommendation` accepts only the `recommendation.json` inside the complete
  run directory that wrote it.
- `--require-clean` refuses to run when code, configuration or tests have uncommitted
  changes, so the recorded git commit identifies the code that ran.

The full protocol takes about six minutes on one core. The run directory holds
`manifest.json`, `forecasts.csv` (one row per model, origin and horizon), `fits.csv`,
`coefficients.csv`, `observed_inputs.csv`, `metrics.json`, `report.md`, `config.json`, and
`selection.json` / `recommendation.json` where applicable.

`runs/` is git-ignored. The reference results quoted in the documentation are a curated copy
in `docs/results/` (manifests, metrics, reports and selection records, but not the large
`forecasts.csv`, whose SHA-256 is in the manifest). `atlas backtest verify` regenerates a run
with the current code (data-quality flags included) and compares every regenerated file byte
for byte.

### Running the M3 protocol

`configs/uncertainty/atlas-m3-intervals-v1.json` is pre-registered the same way: the
selection period first, then the evaluation period with the sealed selection, which must be
reproduced in the same run. The configuration pins the fingerprint of the M2 configuration,
and its primary model must be the model recommended by the complete M2 run it names.

```bash
uv run atlas uncertainty run --require-clean --output-dir runs/m3-selection
uv run atlas uncertainty run --require-clean \
    --period m3_selection --period m3_evaluation \
    --sealed-selection runs/m3-selection/selection.json --output-dir runs/m3-full
uv run atlas uncertainty verify runs/m3-full
uv run atlas uncertainty project --status runs/m3-full/calibration_status.json
```

A run directory holds `manifest.json`, `forecasts.csv` (the M2 residual backtest),
`intervals.csv` (one row per period, model, method, origin and horizon, with the quantiles
and the calibration window), `exceedance.csv` (one row per forecast and threshold),
`metrics.json`, `report.md`, `config.json`, `backtest_config.json`, and `selection.json` /
`calibration_status.json` where applicable. `atlas uncertainty project` accepts only the
`calibration_status.json` inside the complete run that wrote it, recomputes the residual
backtest (which must reproduce that run's `forecasts.csv`) and labels every projection with
the evaluation status.

### Running the M4 decision simulation

`configs/optimization/atlas-m4-optimization-v1.json` is the reference configuration. It reads
only committed, hash-verified files (the M2 projection and recommending run, the M3 status
and projection), so it runs offline in well under a minute:

```bash
uv run atlas optimize run --require-clean --output-dir runs/m4
uv run atlas optimize verify runs/m4
uv run atlas optimize inspect runs/m4
```

A run directory holds `manifest.json`, `config.json`, `scenarios.csv` (one row per scenario
and plan: optimised, relaxed, without the target, policies, LP at equal cash / backlog),
`plans.csv` (one row per scenario, plan and day), `duals.csv` (every row and bound with its
shadow price and finite-difference bracket), `sensitivity.csv`, `frontier.csv`,
`metrics.json` and `report.md`. Every CSV row carries the HYPOTHETICAL and not-validated
labels and the M3 status. The curated reference copy is in
`docs/results/atlas-m4-optimization-v1/`.

For M4, `--require-clean` also requires the configuration to be a git-tracked file under
`configs/`. `optimize verify` fails when the recomputation reproduces a failed check (not
only when bytes differ), and `optimize inspect` reads the failed-check count from the
fingerprinted `metrics.json` as well as the manifest's `complete` flag.

### Reproducibility across machines

Two different guarantees apply:

- **Same environment:** every run is deterministic. `atlas backtest verify` and
  `atlas uncertainty verify` re-run a recorded run and require byte-identical files
  (SHA-256), and the engine tests compare frames exactly. CI runs and verifies each smoke
  run on the same runner.
- **Across machines:** the locked numpy wheel bundles OpenBLAS, which picks CPU-specific
  kernels at run time (`OPENBLAS_CORETYPE` overrides the choice). The committed reference
  results were produced with AVX-512 kernels; GitHub-hosted runners without AVX-512 use
  AVX2 kernels, and least-squares predictions then differ by a few units in the last place
  (at most 1.5e-15 relative on the M2 spot check). Tests that compare a fresh computation
  with a committed reference therefore use `tests/numeric.py`: exact equality for models
  that do not call BLAS, and a relative tolerance of 1e-9 (0.00001 requests at typical
  demand) for the regressions. Committed files are always compared exactly with each
  other.

A run recorded on one machine may therefore fail `verify` on a machine with a different
CPU, in the last bits of the regression predictions; re-run it there instead. To check a
change against another kernel family locally:

```bash
OPENBLAS_CORETYPE=Haswell uv run pytest      # AVX2 kernels, as on some CI runners
```

Every command prints the report of each stage it ran, prerequisites included. Stages run in
order, and a stage is **skipped** when an earlier one failed, so nothing is ever computed from
unverified or invalid data.

| Exit code | Meaning |
|---|---|
| 0 | every requested stage passed |
| 1 | a stage failed or was skipped because an earlier stage failed |
| 2 | usage or configuration error (bad flag, unknown snapshot, unreadable manifest or exception register) |
| 3 | unexpected internal error, i.e. a bug; the traceback is printed to stderr |

Expected result on a clean checkout: every stage passes. Two documented data-quality
exceptions are reported as `INFO` with their ids (`LEGACY-V0-EX-001`, `LEGACY-V0-EX-002`);
see [DATA_SNAPSHOT.md](DATA_SNAPSHOT.md).

## Configuration

Resolution order: command-line flag, then environment variable, then default. M0 uses no
secrets; `.env.example` documents the variables. ATLAS does not load `.env` files itself;
export the variables or use `uv run --env-file .env atlas ...`. `.env` files are git-ignored.

| Setting | Flag | Environment variable | Default |
|---|---|---|---|
| Repository root | `--repo-root` | `ATLAS_REPO_ROOT` | nearest ancestor of the working directory containing `snapshots/` and `pyproject.toml` |
| Snapshot | `--snapshot` | `ATLAS_SNAPSHOT_ID` | `legacy-v0` |

Snapshot ids are restricted to `[a-z0-9][a-z0-9._-]{0,63}`, which rules out path traversal.

## Quality gates

```bash
uv run ruff format --check .   # formatting
uv run ruff check .            # lint (pycodestyle, pyflakes, isort, bugbear, bandit, ...)
uv run mypy                    # strict type checking of src/atlas
uv run pytest                  # full suite with branch coverage; fails under 90 %
```

`pytest` treats warnings as errors. The 90 % coverage gate applies to the whole package, so
run subsets with `--no-cov`, for example `uv run pytest -m integration --no-cov`.

- Tests marked `integration` read the committed snapshot.
- Other tests use small synthetic CSV files, except the tamper tests, which run on a
  temporary copy of the committed snapshot. No test ever modifies the real files.
- One integration test compares every registered file with the original commit
  (`db46703`). It is skipped when that commit is not in the clone (shallow history); CI
  checks out the full history.

GitHub Actions (`.github/workflows/ci.yml`) runs all of the above plus `atlas check`,
`atlas quality`, `atlas backtest legacy`, a smoke backtest followed by
`atlas backtest verify`, an M3 smoke run followed by `atlas uncertainty verify`, and the M4
smoke and reference runs followed by `atlas optimize verify` and `inspect`, on pushes to
`main` and on pull requests. It installs uv from a hash-pinned requirements file
(`.github/ci-requirements.txt`).

## Repository layout

```
src/atlas/            Python package (see ARCHITECTURE.md)
tests/                pytest suite
snapshots/legacy-v0/  frozen manifest and known-exception register
configs/backtest/     versioned M2 experiment configurations (pre-registered protocol)
configs/uncertainty/  versioned M3 uncertainty configurations (pre-registered protocol)
configs/optimization/ versioned M4 decision-simulation configurations (HYPOTHETICAL values)
docs/                 this guide, architecture, data, limitations, roadmap, backtesting,
                      uncertainty, optimisation
docs/results/         curated reference results of the M2, M3 and M4 runs
runs/                 local run directories (git-ignored)
01 Data/ ... 06 Exports/   original project artifacts, unchanged and hash-verified
```

## Line endings on Windows

`.gitattributes` stops git from converting line endings in every file registered in the
snapshot manifest: CSVs, the notebook and the SQL script are `-text`, and the Power BI file,
PDF and images are `binary`. Without this, `core.autocrlf=true` (the Git for Windows default)
would rewrite these files on checkout and every SHA-256 check would fail. A test asserts that
every registered path is covered.
