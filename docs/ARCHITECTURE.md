# Architecture

Notebook cells are referred to by their 0-based index in the `.ipynb` file ("cell index N").
The notebook's own headings number the analysis steps differently: cell index 15, for
example, is headed "STEP 16".

## 1. The original project (preserved as-is)

The original project is a single Google Colab notebook plus artifacts derived from it.
None of these files are modified by ATLAS; their SHA-256 hashes are part of the
`legacy-v0` manifest and are verified on every run.

```
NYC Open Data Socrata API (dataset erm2-nwe9, live)                    cell index 1-3
  ├─ daily_total     city-wide count per day of created_date
  └─ daily_segments  count per day, agency and borough (five named boroughs only)
        │
        ▼
SQLite database file on the Colab VM + pandas: yearly/YTD/monthly/
  weekday/borough/agency aggregates                                    cell index 6-13
linear regression, 90-day holdout, recursive 30-day forecast           cell index 14-16
"capacity" threshold and risk tiers                                    cell index 17
16 CSV exports -> zip -> manual download -> "01 Data/"                 cell index 18-19
        │
        ▼
Power BI report (04), PDF export (06), screenshots (05), SQL script (03, not executed)
```

Properties of that pipeline that shaped M0:

- The only data entry point was a live, changing API. No extraction time, query hash or
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
  legacy.py       Legacy output tables re-derived from their inputs (no model fitting)
  reproduce.py    Comparison with the committed tables, KPIs and model metrics
  pipeline.py     Fail-closed stage orchestration
  cli.py          argparse command-line interface (`atlas`)

  M1 additions
  dates.py           ISO 8601 conventions; explicit conversion of legacy weekday numbers
  events.py          US federal holidays by rule; declared events gated by announcement date
  series.py          DemandSeries: validated daily series (the only input to features)
  reconciliation.py  daily_borough_components with the UNASSIGNED aggregate residual
  quality.py         Data-quality flags and the `quality` stage
  features.py        Feature definitions and the training / forecast / recursive builders
```

### Data flow

```
snapshots/legacy-v0/manifest.json ──► integrity     every file: size, SHA-256; tables also
                                         │ pass       header, row count, date range
"01 Data/*.csv" ──► loader (contracts) ──► validation  types, nulls, ranges, keys, calendar,
                                         │ pass       totals vs segments, declared exceptions
                                         ▼
                                     reproduction   6 aggregate tables, 6 legacy output
                                                    tables, 14 KPIs, 10 model metrics
```

### Design decisions

| Decision | Reason |
|---|---|
| Legacy files stay at their original paths; the manifest only describes them | M0 must not move or modify the original artifacts. |
| Every CSV field is read as text and parsed by declared kind | Results do not depend on pandas type inference, which differs between pandas versions. Only an empty field is null; null-like text (`NaN`, `NA`, `None`, `null`, `N/A`) is rejected in every column, including text columns. |
| Problems are collected as `Issue`s, not raised | A report shows every problem in a run. Exceptions are reserved for unusable configuration; anything else that escapes is a bug and exits with code 3. |
| Stages are fail-closed and skip downstream work | Nothing is computed from unverified or invalid data. |
| Known exceptions must match observed statistics exactly | A documented defect is accepted only in its documented form; any drift fails, and a declaration that matches nothing is reported as stale. |
| Rounded legacy values must be a valid rounding of the exact value, at the published precision | Works for both SQLite half-up and Python half-even rounding without hiding real differences. |
| Legacy conventions are reproduced, not "fixed" (Sunday = 0 weekdays, active-day averages, the demand-percentile "capacity") | The goal of M0 is to prove what the original numbers are. Corrections belong to later milestones. |
| Minimal runtime dependencies (pandas, pydantic); argparse rather than a CLI framework | Small attack surface and fewer version conflicts on a small VPS. |
| `uv.lock` committed; `pandas>=3.0,<3.1` | Reproducible installs; one pandas minor version. |

### M1 data flow

```
validated daily_total ─► DemandSeries ─► features.py ─► training rows   (t <= o, data through t-h)
                              │                      ├► forecast rows   (t = o+h, data through o)
                              │                      └► recursive rows  (data through o + predictions)
                              ▼
validated daily_segments ─► reconciliation.py ─► five boroughs + UNASSIGNED residual
                        └─► quality.py ─► flags (missing, surge, low volume, interruption, catch-up)
```

`atlas quality` runs integrity → validation → quality. It does not depend on the reproduction
of legacy outputs, and `atlas check` is unchanged.

| M1 decision | Reason |
|---|---|
| One private builder behind training, direct and recursive features | Parity by construction; tests check it exactly |
| Truncate at the origin, then guard every read against the row's cutoff | Two independent barriers against look-ahead; the guard does not trust declared offsets |
| Lags by calendar date; gaps raise unless explicitly allowed | A missing day can never shift a lag; nothing is imputed silently |
| Fixed trend anchor | Feature values do not depend on the training-window length |
| Residual as one labelled aggregate component, built only when M0 accepts the gap | Totals reconcile by construction without inventing agency records |
| Flags are causal annotations, never edits or features | Usable in rolling-origin evaluation; the data stays as recorded |
| M1 tables checked by the M0 loader (`validate_frame`) | One set of contract rules for files and in-memory tables |
| Pinned feature-set fingerprint and pinned legacy flag inventory | Definition or detection changes must be deliberate |

### Extending to new snapshots

A new snapshot needs:

1. a specification in `registry.py` (`SNAPSHOT_SPECS`) and contracts for its tables
   (`SNAPSHOT_CONTRACTS`);
2. a manifest generated once with `atlas snapshot manifest` and committed under
   `snapshots/<id>/`;
3. optionally a known-exception register.

The reproduction rules in `reproduce.py` are specific to the `legacy-v0` tables and run only
for snapshots listed in `registry.REPRODUCIBLE_SNAPSHOTS`; for any other snapshot the stage
reports "not applicable". Registered manifests are never regenerated to describe changed
data: a changed dataset is a new snapshot id.
