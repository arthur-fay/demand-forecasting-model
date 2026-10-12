# ATLAS development guide

ATLAS turns the original NYC 311 demand-forecasting project into a reproducible, tested
Python application. Milestone **M0** is the engineering foundation:

- a frozen, hash-verified record of the historical data (`legacy-v0`);
- an offline loader with fail-closed data contracts;
- recomputation of the legacy aggregate tables, KPIs and model metrics, plus cross-checks
  of the legacy output tables against the base data.

M0 does **not** forecast, optimise, serve an API or fetch data from the network. See
[ROADMAP.md](ROADMAP.md).

## Requirements

- Python 3.12 (pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/) 0.11 or newer (CI installs 0.11.32, hash-pinned)
- No network access is needed after dependencies are installed.

## Install

```bash
uv sync --locked          # creates .venv with the exact versions in uv.lock
```

Runtime dependencies are deliberately small: `pandas` and `pydantic` (NumPy and the
rest come with them). Development tools (`pytest`, `pytest-cov`, `hypothesis`, `ruff`,
`mypy`, `pandas-stubs`) are in the `dev` dependency group, installed by default.

## Run the checks

```bash
uv run atlas check                    # integrity -> validation -> reproduction (CI entry point)
uv run atlas snapshot verify          # integrity only: SHA-256, size, header, rows, date ranges
uv run atlas validate                 # integrity + data contracts + declared exceptions
uv run atlas reproduce                # all three stages (same as check)
uv run atlas --format json check      # machine-readable report
uv run atlas snapshot manifest        # print the manifest computed from disk (never writes)
uv run python -m atlas --version      # module entry point
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

GitHub Actions (`.github/workflows/ci.yml`) runs all of the above plus `atlas check` on
pushes to `main` and on pull requests. It installs uv from a hash-pinned requirements file
(`.github/ci-requirements.txt`).

## Repository layout

```
src/atlas/            Python package (see ARCHITECTURE.md)
tests/                pytest suite
snapshots/legacy-v0/  frozen manifest and known-exception register
docs/                 this guide, architecture, data, limitations, roadmap
01 Data/ ... 06 Exports/   original project artifacts, unchanged and hash-verified
```

## Line endings on Windows

`.gitattributes` stops git from converting line endings in every file registered in the
snapshot manifest: CSVs, the notebook and the SQL script are `-text`, and the Power BI file,
PDF and images are `binary`. Without this, `core.autocrlf=true` (the Git for Windows default)
would rewrite these files on checkout and every SHA-256 check would fail. A test asserts that
every registered path is covered.
