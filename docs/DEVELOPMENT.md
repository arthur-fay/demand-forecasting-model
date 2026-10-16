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

Milestone **M5** adds an HTTP API ([API.md](API.md)):

- a FastAPI service that serves the committed M2-M4 outputs, each response labelled with
  what its numbers are (observed, projected, historical, HYPOTHETICAL) and with the M3
  status, and naming every source with its fingerprints;
- bounded HYPOTHETICAL simulations with the M4 engine in isolated worker processes;
- strict validation, limits, safe errors, structured logging and a committed OpenAPI
  contract (`docs/api/openapi.json`).

Milestone **M6** (phase A) adds a deployment package ([DEPLOYMENT.md](DEPLOYMENT.md)):

- `atlas.deploy` (standard library only) and the `atlas-deploy` command: a read-only server
  preflight, a validated deployment configuration, systemd units and an nginx site rendered
  from it, release manifests, health verification, activation with automatic rollback,
  recovery, pruning, upgrade plans and a periodic monitor;
- `deploy/`: the templates, the bootstrap and release-build scripts, the example
  configuration and the disposable test server used by the end-to-end test.

Milestone **M7** adds the web dashboard ([DASHBOARD.md](DASHBOARD.md)):

- `web/`: a React and TypeScript single-page application over the API, validated against the
  contract, with the scientific limitations next to every number and bounded HYPOTHETICAL
  simulations where the deployment allows them; built into the committed `web/dist`;
- `atlas.dashboard` and `atlas.api.dashboard`: the build's manifest and verification, and the
  API serving it at `/`; the deployment package publishes, verifies and rolls it back with
  every release.

Nothing is deployed publicly, and ATLAS does not fetch data from the network. M4 results are
not validated recommendations. See [ROADMAP.md](ROADMAP.md).

## Requirements

- Python 3.12 (pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/) 0.11 or newer (CI installs 0.11.32, hash-pinned)
- No network access is needed after dependencies are installed.

## Install

```bash
uv sync --locked          # creates .venv with the exact versions in uv.lock
```

Runtime dependencies are deliberately small: `pandas`, `pydantic` and, from M4, `scipy`
for its bundled HiGHS linear-programming solver (NumPy and the rest come with them). M5 adds
`fastapi` (0.141; 0.142 would add an OpenTelemetry dependency ATLAS does not use),
`uvicorn` (without extras) and `h11`, uvicorn's HTTP/1.1 implementation, which the API's
request-read deadline builds on. The tests use `httpx2`, the HTTP client Starlette's test
client asks for.
Development tools (`pytest`, `pytest-cov`, `hypothesis`, `ruff`, `mypy`, `pandas-stubs`,
`scipy-stubs`) are in the `dev` dependency group, installed by default. New and upgraded
packages are held to releases at least two weeks old. That is a review rule, not a setting
uv enforces (see [Dependency maintenance](#dependency-maintenance)); three caps in
`tool.uv.constraint-dependencies` hold back releases that were younger than that when the
lock was resolved: `optype` (a typing-only dependency of `scipy-stubs`, since M4), and
`starlette` and `truststore` (since M5).

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
uv run atlas api serve                # M5: HTTP API on http://127.0.0.1:8000 (docs at /docs)
uv run atlas api openapi              # M5: print the OpenAPI contract (never writes)
uv run atlas api bench                # M5: measured latency and memory on this machine
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
`configs/` whose content equals the blob committed at `HEAD`. `optimize verify` fails when the recomputation reproduces a failed check (not
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
| API settings (M5) | `atlas api serve --host/--port` | `ATLAS_API_*` | see [API.md](API.md), section 1 |
| Deployment (M6) | `atlas-deploy --root` | `/etc/atlas/deploy.env` (`ATLAS_DEPLOY_*`) | see [DEPLOYMENT.md](DEPLOYMENT.md), section 6 |
| Dashboard (M7) | — | `ATLAS_API_DASHBOARD`; `ATLAS_DEV_API` for `npm run dev` | served at `/` by `atlas api serve`; see [DASHBOARD.md](DASHBOARD.md), section 11 |

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
- The M6 tests run the rendered deployment files through the real `nginx`, `openssl`,
  `shellcheck` and `systemd-analyze` when they are installed; with
  `ATLAS_REQUIRE_DEPLOY_TOOLS=1` (as in CI) a missing tool fails the tests instead of
  skipping them. On Ubuntu: `sudo apt-get install nginx shellcheck` (stop the nginx service;
  the tests run their own on loopback ports).
- The end-to-end deployment test runs in a disposable systemd container and is opt-in:
  `ATLAS_E2E_CONTAINER=1 uv run pytest tests/test_m6_container.py --no-cov -v` (needs Docker
  and privileges to run a container with systemd; CI runs it in the `deploy-e2e` job).

The dashboard (`web/`, Node.js 22.22.2+ or 24.15+ on development machines and CI only) has
its own checks: `cd web && npm ci && npm run format:check && npm run lint && npm run
typecheck && npm test && npm run build && npm run verify:dist && npm run size`, and the
browser tests `npx playwright test` (first `npx playwright install chromium`; they need
nginx and openssl on `PATH` and start the API and two nginx stacks; see
[DASHBOARD.md](DASHBOARD.md) section 11). After `npm run build`, commit `web/dist`: CI
rebuilds it and fails on any difference. `tests/test_m7_fixtures.py` keeps the fixtures of
the frontend tests identical to the API's answers.

GitHub Actions runs three workflows:

- `.github/workflows/ci.yml`, on pushes to `main` and on pull requests: all of the above
  plus `atlas check`, `atlas quality`, `atlas backtest legacy`, a smoke backtest followed by
  `atlas backtest verify`, an M3 smoke run followed by `atlas uncertainty verify`, the M4
  smoke and reference runs followed by `atlas optimize verify` and `inspect`, an M5 step
  that checks the committed OpenAPI contract and serves the reference data from a real
  server, shellcheck of the deployment scripts, (job `deploy-e2e`) the M6 end-to-end
  deployment test in a disposable systemd container, and (job `dashboard`) the dashboard's
  checks, a reproducible build and the browser tests.
- `.github/workflows/dependency-audit.yml`, weekly, on demand and on pull requests that
  change a lock or a dependency declaration: known vulnerabilities in the locked Python and
  npm packages (see [Dependency maintenance](#dependency-maintenance)).
- `.github/workflows/reproduce-research.yml`, on demand only: regenerates every committed
  reference run and compares it with the committed files (see
  [Reproducing the committed reference results](#reproducing-the-committed-reference-results)).

Every action is pinned to a full commit SHA with its version in a comment:
`actions/checkout` v7.0.1, `actions/setup-python` v7.0.0, `actions/setup-node` v7.0.0 and
`actions/upload-artifact` v7.0.1, all of which run on Node.js 24. uv is installed from a
hash-pinned requirements file (`.github/ci-requirements.txt`).

## Reproducing the committed reference results

CI re-verifies only the smoke runs and the M4 reference run. It does not re-derive the
committed M2 and M3 reference runs in `docs/results/`, which take several minutes each. The
tests recompute the M2 forecasts at six spot-check origins and the point forecasts of the
M2 projection (within a relative tolerance), and check the documents against
`metrics.json` and the re-rendered reports; nothing in CI recomputes the M2 and M3 metrics
or the M3 intervals. A change to the evaluation or interval code could therefore stop
reproducing a published number without failing CI. Before a release, run the manual
workflow `reproduce-research.yml` (Actions > Reproduce research results > Run workflow, on
the branch or tag of the commit to be released). It runs one job per committed run, in
parallel, and fails on any difference. The same checks by hand:

```bash
uv run atlas backtest verify docs/results/atlas-m2-rolling-origin-v1/full
uv run atlas backtest verify docs/results/atlas-m2-rolling-origin-v1-delay1/development
uv run atlas uncertainty verify docs/results/atlas-m3-intervals-v1/selection
uv run atlas uncertainty verify docs/results/atlas-m3-intervals-v1/full
uv run atlas optimize verify docs/results/atlas-m4-optimization-v1
# runs without a verify command: regenerate, then compare through the two manifests
uv run atlas forecast --output-dir runs/m2-projection \
    --recommendation docs/results/atlas-m2-rolling-origin-v1/full/recommendation.json
uv run python .github/scripts/same_outputs.py \
    docs/results/atlas-m2-rolling-origin-v1/projection runs/m2-projection
uv run atlas uncertainty project --output-dir runs/m3-projection \
    --status docs/results/atlas-m3-intervals-v1/full/calibration_status.json
uv run python .github/scripts/same_outputs.py \
    docs/results/atlas-m3-intervals-v1/projection runs/m3-projection
uv run atlas backtest legacy --output-dir runs/legacy-holdout
uv run python .github/scripts/same_outputs.py \
    docs/results/legacy-one-step-holdout runs/legacy-holdout
```

The sealed M2 development record (`docs/results/atlas-m2-rolling-origin-v1/development`)
does not verify with the current code: the reporting code changed after it was sealed
([BACKTESTING.md](BACKTESTING.md), section 8). The workflow verifies a copy of it with the
code and lock of commit `a16672f` (`git archive a16672f`, `uv sync --locked`, `atlas
backtest verify <copy>`). With the current code, the full reference run recomputes the
development period and its sealed selection.

On 2026-10-16, at commit `8f077ed`, every check passed on a 4-core Intel Xeon with AVX-512.
The runs shared the machine with each other and with other work, so the wall times are
indicative only:

| Run | Check | Wall time | Result |
|---|---|---|---|
| M2 reference run (`full`) | `backtest verify` | 10.6 min | identical, fingerprint `d53bc911…5a8a` |
| M2 delay sensitivity | `backtest verify` | 6.0 min | identical, fingerprint `79d4f796…f22d` |
| M2 sealed development run, code of `a16672f` | `backtest verify` | 6.3 min (with `uv sync`) | identical, fingerprint `fbb948ec…ea43` |
| M2 projection | `forecast`, `same_outputs.py` | 4 s | 5 of 5 files identical |
| Legacy one-step holdout | `backtest legacy`, `same_outputs.py` | 5 s | 2 of 2 files identical |
| M3 selection run | `uncertainty verify` | 5.1 min | identical, fingerprint `2e673489…7194` |
| M3 reference run (`full`) | `uncertainty verify` | 6.6 min | identical, fingerprint `c4d2adbf…dfaf` |
| M3 projection | `uncertainty project`, `same_outputs.py` | 3.6 min | 3 of 3 files identical |
| M4 reference run | `optimize verify` | 43 s | identical, fingerprint `0dc4eb0f…3c82` |

Byte-identical verification needs the same BLAS kernels as the recorded run (see
[Reproducibility across machines](#reproducibility-across-machines)). The committed runs
were computed with AVX-512 kernels; on a runner without AVX-512, `verify` can fail in the
last bits of the regression outputs although nothing is wrong. The workflow logs the CPU
and warns when AVX-512 is missing; re-run it, or run the commands on an AVX-512 machine.

## Dependency maintenance

The repository owner approves every change to `uv.lock`, `web/package-lock.json` and the
pinned actions, lifts the cooldown caps and decides on exceptions. No Dependabot or
Renovate configuration exists: upgrades are deliberate commits.

**Release-age cooldown.** A new or upgraded Python package, npm package or GitHub Action
must have been published at least two weeks earlier (publication time on PyPI, the npm
registry, or the date of the action's release commit). Neither uv nor npm enforces this
here; it is a review rule, checked by whoever changes a lock. uv's `exclude-newer` setting
could enforce it, but adopting it would re-resolve the lock and downgrade eight packages
that entered at M0, numpy among them (M8 audit, finding M8-33), so every result would have
to be verified again.

**Caps.** When a lock is resolved while a wanted release is younger than two weeks, a cap
in `tool.uv.constraint-dependencies` (`pyproject.toml`) holds it back, with its review date
in the comment beside it. Caps do not expire on their own: on the review date the owner
removes the cap, runs `uv lock --upgrade-package <name>` and the full checks and commits
the lock, or records why the cap stays.

| Cap | Release held back | Published (PyPI, UTC) | Review date |
|---|---|---|---|
| `optype<0.19` (typing only, via `scipy-stubs`) | 0.19.0 | 2026-10-02 23:14 | 2026-10-16 |
| `truststore<0.10.5` (tests only, via `httpx2`) | 0.10.5 | 2026-10-12 22:27 | 2026-10-26 |
| `starlette<1.8` (runtime, via `fastapi`) | 1.8.0 | 2026-10-13 07:54 | 2026-10-27 |

**Known vulnerabilities.** `.github/workflows/dependency-audit.yml` audits the locks every
Monday, on demand, and on every pull request that changes `uv.lock`, `pyproject.toml`,
`web/package.json` or `web/package-lock.json` (or the workflow and the uv bootstrap):

- `pip-audit`, an exact release more than two weeks old run with `uv tool run` (its own
  dependencies resolved as PyPI listed them on a fixed date), checks the hash-pinned export
  of the runtime dependencies and, separately, of the development dependencies against the
  PyPI advisory data, and fails on any known vulnerability;
- `npm audit --omit=dev --audit-level=moderate` checks what ships in the dashboard bundle,
  and `npm audit --audit-level=high` checks every locked package (the CI `dashboard` job
  also runs the latter on every push).

GitHub disables scheduled workflows in a repository without activity for 60 days; the
owner re-enables the workflow under Actions, and runs it by hand before every release.
The Python part, locally:

```bash
uv export --frozen --format requirements-txt --no-dev --no-emit-project -o runtime.txt
uv export --frozen --format requirements-txt --only-dev --no-emit-project -o dev.txt
uv tool run --exclude-newer 2026-10-02T00:00:00Z --from pip-audit==2.10.1 \
    pip-audit --strict --require-hashes --disable-pip -r runtime.txt   # then dev.txt
```

On 2026-10-16 these commands found no known vulnerability in the lock of commit `8f077ed`:
pip-audit 2.10.1 checked the 18 runtime packages that apply on Linux with CPython 3.12 (of
19 exported; `tzdata` is installed only on Windows) and 27 of the 29 development packages
(the other two are for Windows and WebAssembly only), and both `npm audit` levels reported
0 vulnerabilities. As a control, the same pip-audit release reported `h11==0.14.0`
(PYSEC-2026-348, fixed in 0.16.0) and exited with status 1.

**Handling an advisory.** A fix for a vulnerability that affects ATLAS is taken as soon as
it is published, inside the cooldown if need be: the owner lifts any cap in the way, upgrades only
the affected package, reads its changelog, runs the full checks, notes the exception in the
commit message, and builds and deploys a new release. A deployed release keeps the
environment it was built with (its `.venv`, from `uv.lock`); the server's
unattended-upgrades do not update it. An advisory that does not affect ATLAS (for example
one in a development tool, or in a code path ATLAS never calls) may be ignored with
`--ignore-vuln <id>` in the workflow, with the reason and a review date beside it.

**GitHub Actions.** To update an action, take its newest release that is at least two weeks
old and runs on a supported Node.js runtime: read the tag's commit with
`git ls-remote --tags https://github.com/actions/<name>`, fetch that commit and check
`runs.using` in its `action.yml` and the release's breaking changes against the inputs the
workflows use, then pin the full SHA with the version in a comment.

## Versioning

The code carries the version being released, as MAJOR.MINOR.PATCH: a release candidate of
version 1.0.0 already carries 1.0.0, and so does the release itself. It is declared in
`pyproject.toml`, `src/atlas/__init__.py` and `web/package.json`, and copied into
`uv.lock`, `web/package-lock.json`, the dashboard build (`web/dist/atlas-dashboard.json`)
and the API contract (`docs/api/openapi.json`). `tests/test_m8_release.py` requires all of
them to be equal, so a version change is one commit: edit the three declarations, then run
`uv lock`, `npm version <version> --no-git-tag-version` in `web/`,
`uv run atlas api openapi > docs/api/openapi.json`, record the dashboard fixtures again
(`ATLAS_UPDATE_DASHBOARD_FIXTURES=1 uv run pytest tests/test_m7_fixtures.py --no-cov`),
rebuild and commit `web/dist`, and update the tests and documents that quote the version.

Release-candidate status is not part of the version. The release id
(`<version>-<12 hex digits of the commit>`), the dashboard manifest and `build-release.sh`
accept MAJOR.MINOR.PATCH only, and a pre-release would be spelled differently in the two
ecosystems (`1.0.0rc1` for Python, `1.0.0-rc.1` for npm). A candidate is identified by the
release-candidate manifest and by a tag `v<version>-rc.<n>` (for example `v1.0.0-rc.1`) on
the approved commit, which only the owner creates, after approval. The validators keep
MAJOR.MINOR.PATCH.

## Repository layout

```
src/atlas/            Python package (see ARCHITECTURE.md)
tests/                pytest suite
snapshots/legacy-v0/  frozen manifest and known-exception register
configs/backtest/     versioned M2 experiment configurations (pre-registered protocol)
configs/uncertainty/  versioned M3 uncertainty configurations (pre-registered protocol)
configs/optimization/ versioned M4 decision-simulation configurations (HYPOTHETICAL values)
docs/api/             the committed OpenAPI contract of the M5 API
deploy/               M6 deployment templates, host scripts, example configuration, test server
web/                  M7 dashboard source (src/, e2e/, scripts/) and its committed build (dist/)
docs/                 this guide, architecture, data, limitations, roadmap, backtesting,
                      uncertainty, optimisation, API, deployment, dashboard
docs/results/         curated reference results of the M2, M3 and M4 runs
.github/              CI, dependency-audit and reproduction workflows, the uv bootstrap, scripts
runs/                 local run directories (git-ignored)
01 Data/ ... 06 Exports/   original project artifacts, unchanged and hash-verified
```

## Line endings on Windows

Every file that ATLAS verifies byte for byte is protected from line-ending conversion in
`.gitattributes`. Without this, `core.autocrlf=true` (the Git for Windows default) would
rewrite text files on checkout while `git status` stayed clean, and every SHA-256 check on
them would fail. The protected files are:

- every file registered in the snapshot manifest: CSVs, the notebook and the SQL script are
  `-text`, and the Power BI file, PDF and images are `binary`;
- the research results in `docs/results/` (`-text`): each run manifest records the SHA-256
  of its outputs, which `verify`, `inspect`, the API's readiness check and the M4 inputs
  check, and every release fingerprints them;
- the configurations in `configs/` (`-text`), fingerprinted with the results in every
  release;
- the API contract `docs/api/openapi.json` (`-text`), which CI and `build-release.sh`
  compare byte for byte;
- the committed dashboard build (`web/dist`) and the frontend test fixtures (`-text`).

Tests in `tests/test_snapshot.py` assert that every snapshot-registered path, every file
under the release's research paths, every output named in a `docs/results` manifest and the
contract are covered, and that an export of them with `core.autocrlf=true` (`git archive`
converts line endings as a checkout does) is identical to the committed files and matches
every manifest hash.
