# M8 reproducibility and build verification

Whether every result and every build artifact of ATLAS 1.0.0-rc.1 can be regenerated from the
repository, and what was actually regenerated in M8. Audit machine: a 4-vCPU Intel Xeon with
AVX-512 (OpenBLAS core `SkylakeX`, the same kernel family as the committed reference runs),
Ubuntu 24.04, Python 3.12.3, uv 0.11.32, Node.js 22.22.2.

## 1. What was regenerated and compared

| Artifact | Regenerated how | Result |
|---|---|---|
| `legacy-v0` snapshot | `atlas check`: every hash, size, header, row count and date range; reproduction of the six derived tables and every legacy KPI | identical; 14 of 14 KPIs |
| Legacy one-step holdout and 30-day forecast | `atlas backtest legacy` | 90 of 90 and 30 of 30 reproduced; committed files byte-identical |
| M2 full reference run (development, confirmation, legacy window) | `atlas backtest run --period development --period confirmation --period legacy_window --sealed-selection …` by the forecasting auditor | **byte-identical**, result fingerprint `d53bc911…` (197,610 records) |
| M2 sealed development run | `atlas backtest verify` with the code of its own commit `a16672f` | byte-identical (`fbb948ec…`) |
| M2 projection | `atlas forecast` from the committed recommendation | byte-identical |
| M3 selection, sealed full run and projection | re-run in order by the uncertainty auditor; `atlas uncertainty verify` on the full run | **byte-identical** (`2e673489…`, `c4d2adbf…`) |
| M4 reference run | `atlas optimize verify` (baseline and candidate) and a scratch re-run of the reference configuration | **byte-identical**, 8 of 8 files |
| M2, M3 and M4 smoke runs | `run` then `verify`, as in CI | byte-identical |
| API contract | `atlas api openapi` against `docs/api/openapi.json` | identical |
| Dashboard build `web/dist` | `npm ci` and `npm run build` from the lock, at the baseline, by the dashboard auditor, and twice after the version bump | **byte-identical**; `verify:dist` matches the manifest |
| Release environment | `uv sync --locked --no-dev` twice for the same commit | the environment fingerprint now reproduces (M8-24) |

The measured run on the candidate commit is in [M8_CORRECTIONS.md](M8_CORRECTIONS.md) section 1.

## 2. What makes it reproducible

- **Frozen inputs.** One immutable snapshot, hash-pinned; configurations fingerprinted before
  any run; every run records its configuration, data, code, environment and output fingerprints
  in a manifest; `verify` regenerates a run and compares every file.
- **Locked environments.** `uv.lock` (hashes for every wheel) with uv itself installed from a
  hash-pinned file; `web/package-lock.json` with integrity hashes and `npm ci`. M8 changed
  neither lock except the project's own version.
- **No randomness, or a fixed seed.** M2-M4 have no random component. The M8 size and
  dependence studies fix their seeds, and tests check their recorded output.
- **Line endings (M8-07).** Every byte-verified file (results, configurations, the contract,
  the snapshot, the build, the dashboard fixtures and `web/public`) is excluded from line-ending
  conversion, and a test exports the committed files with `core.autocrlf=true` (as a checkout
  would convert them, with an unprotected file as a positive control) and checks every hash.
  Before M8, such a checkout (the Git for Windows default) broke 29 of 40 result hashes.
- **Versions tied together (M8-36).** One test requires the eight version declarations (Python
  package, lock, dashboard package and lock, build manifest, contract) to be equal.
- **Documented commands that work (M8-12).** The first M2 verify command in the documentation
  failed with the current code; it now points at a command that passes, and the sealed
  development record's own-commit procedure is spelled out.

## 3. Limits

- **CPU class.** The locked NumPy's OpenBLAS chooses kernels by CPU. On a machine without
  AVX-512 the regression predictions differ in the last bits, so `verify` of the M2 projection
  and of the legacy holdout fails there while M4 still passes (shown by forcing the AVX2 core
  locally). The tests compare fresh computations within 1e-9 relative; byte-identical
  verification needs the same kernel family ([../DEVELOPMENT.md](../DEVELOPMENT.md)).
- **Not automated in CI.** The full M2 and M3 reproduction takes about 7 and 5 CPU-minutes. M8
  adds a manual workflow (`reproduce-research.yml`) that regenerates every committed run and
  records the CPU and OpenBLAS core it ran on (M8-38). GitHub can dispatch it only once the
  workflow is on the default branch, so its first run is an owner action after the merge.
- **Python patch version.** The release environment fingerprint includes the interpreter; a
  release must be rebuilt after a `python3.12` update on the server (the build re-verifies the M4
  reproduction).
- **Source data.** The snapshot cannot be re-derived from NYC Open Data: the extraction time
  was not recorded and the live dataset can be revised.

## 4. The candidate as a whole

[`../release/rc_manifest.py`](../release/rc_manifest.py) fingerprints every tracked file of the
candidate together with the locks, the snapshot, the research artifacts, the dashboard build and
the contract, in [`../release/atlas-1.0.0-rc.1.json`](../release/atlas-1.0.0-rc.1.json); `--check`
compares a checkout with it ([../release/RELEASE_CANDIDATE.md](../release/RELEASE_CANDIDATE.md)).
