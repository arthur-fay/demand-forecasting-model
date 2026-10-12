# Architecture

## 1. The original project (preserved as-is)

The original project is a single Google Colab notebook plus artifacts derived from it.
None of these files are modified by ATLAS; their SHA-256 hashes are part of the
`legacy-v0` manifest and are verified on every run.

```
NYC Open Data Socrata API (dataset erm2-nwe9, live)                 notebook cells 1-3
  ├─ daily_total     city-wide count per day of created_date
  └─ daily_segments  count per day, agency and borough (five named boroughs only)
        │
        ▼
in-memory SQLite + pandas: yearly/YTD/monthly/weekday/borough/agency    cells 6-13
linear regression, 90-day holdout, recursive 30-day forecast             cells 14-16
"capacity" threshold and risk tiers                                      cell 17
16 CSV exports -> zip -> manual download -> "01 Data/"                   cells 18-19
        │
        ▼
Power BI report (04), PDF export (06), screenshots (05), SQL script (03, not executed)
```

Properties of that pipeline that shaped M0:

- The only data entry point was a live, changing API; no extraction time, query hash or
  library versions were recorded, and no code path reads the committed CSVs.
- Data-quality checks printed `PASSED` but never stopped execution.
- Everything ran in one notebook with shared global state and Colab-specific paths.

## 2. The ATLAS package (M0)

M0 adds an importable package that works entirely offline on the committed data.

```
src/atlas/
  config.py       Settings: flag > environment > default; repository discovery
  errors.py       AtlasError, ConfigError, SnapshotError (configuration problems only)
  issues.py       Issue (code, severity, subject, examples) and CheckReport
  contracts.py    Column kinds and the declared contract of each legacy table
  loader.py       Strict CSV reading and parsing against a contract
  snapshot.py     Manifest model, SHA-256 hashing, manifest generation, verification
  registry.py     legacy-v0 specification: tables, artifacts, provenance
  validation.py   Cross-table reconciliation and the known-exception register
  summaries.py    Historical aggregates recomputed from the two base tables
  reproduce.py    Comparison with the committed tables, KPIs and model metrics
  pipeline.py     Fail-closed stage orchestration
  cli.py          argparse command-line interface (`atlas`)
```

### Data flow

```
snapshots/legacy-v0/manifest.json ──► integrity      every file: size, SHA-256, header, rows
                                         │ pass
"01 Data/*.csv" ──► loader (contracts) ──► validation  types, nulls, ranges, keys, calendar,
                                         │ pass         totals vs segments, declared exceptions
                                         ▼
                                     reproduction    6 aggregate tables, 14 KPIs, 10 metrics
```

### Design decisions

| Decision | Reason |
|---|---|
| Legacy files stay at their original paths; the manifest only describes them | M0 must not move or modify the original artifacts. |
| Every CSV field is read as text and parsed by declared kind | Results do not depend on pandas type inference, which differs between pandas versions. Only an empty field is null; `NaN` or `NA` text is malformed. |
| Problems are collected as `Issue`s, not raised | A report shows every problem in a run. Exceptions are reserved for unusable configuration. |
| Stages are fail-closed and skip downstream work | Nothing is computed from unverified or invalid data. |
| Known exceptions must match observed statistics exactly | A documented defect is accepted only in its documented form; any drift fails. |
| Rounded legacy values are checked as "valid rounding of the exact value" | Works for both SQLite half-up and Python half-even rounding without hiding real differences. |
| Legacy conventions are reproduced, not "fixed" (Sunday = 0 weekdays, active-day averages) | The goal of M0 is to prove what the original numbers are. Corrections belong to later milestones. |
| Minimal runtime dependencies (pandas, pydantic); argparse rather than a CLI framework | Small attack surface and fewer version conflicts on a small VPS. |
| `uv.lock` committed; `pandas>=3.0,<3.1` | Reproducible installs; one pandas minor version. |

### Extending to new snapshots

A new snapshot needs a specification in `registry.py`, contracts for its tables, and a
manifest generated once with `atlas snapshot manifest` and committed under
`snapshots/<id>/`. Registered manifests are never regenerated in place: a changed dataset
is a new snapshot id.
