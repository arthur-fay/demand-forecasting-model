# ATLAS development guide

ATLAS turns the original NYC 311 demand-forecasting project into a reproducible, tested
Python application. Milestone **M0** is the engineering foundation: a frozen record of
the historical data (`legacy-v0`), an offline loader with fail-closed data contracts, and
reproduction of every historical summary the original project published.

M0 does **not** forecast, optimise, serve an API or fetch data from the network. See
[ROADMAP.md](ROADMAP.md).

## Requirements

- Python 3.12 (pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/) 0.11 or newer (CI pins 0.11.32)
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
uv run atlas snapshot verify          # SHA-256, size, header and row count of every file
uv run atlas validate                 # integrity + data contracts + declared exceptions
uv run atlas reproduce                # ... + recompute historical tables and legacy KPIs
uv run atlas --format json check      # machine-readable report
uv run atlas snapshot manifest        # print the manifest computed from disk (never writes)
python -m atlas --version             # module entry point
```

Exit codes: `0` all requested checks passed, `1` a check failed, `2` usage or
configuration error. Stages run in order and a stage is **skipped** when an earlier one
failed, so nothing is ever computed from unverified or invalid data.

Expected result on a clean checkout: every stage passes. Two documented data-quality
exceptions are reported as `INFO` with their ids (`LEGACY-V0-EX-001`, `LEGACY-V0-EX-002`);
see [DATA_SNAPSHOT.md](DATA_SNAPSHOT.md).

## Configuration

Resolution order: command-line flag, then environment variable, then default. M0 uses no
secrets; `.env.example` documents the variables. `.env` files are git-ignored and are not
loaded automatically.

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
uv run pytest                  # tests, branch coverage, fails under 90 %
```

`pytest` treats warnings as errors. Tests marked `integration` read the committed
snapshot; everything else uses small synthetic CSV files written to a temporary directory.
Tamper tests always work on a temporary copy of the repository, never on the real files.

GitHub Actions (`.github/workflows/ci.yml`) runs all of the above plus `atlas check` on
pushes to `main` and `feature/**`, and on pull requests.

## Repository layout

```
src/atlas/            Python package (see ARCHITECTURE.md)
tests/                pytest suite
snapshots/legacy-v0/  frozen manifest and known-exception register
docs/                 this guide, architecture, data, limitations, roadmap
01 Data/ ... 06 Exports/   original project artifacts, unchanged and hash-verified
```

## Line endings on Windows

`.gitattributes` stops git from converting line endings in CSV and snapshot files. Without
it, `core.autocrlf=true` would rewrite the CSVs on checkout and every SHA-256 check would
fail.
