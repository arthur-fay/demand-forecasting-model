# The `legacy-v0` data snapshot

`legacy-v0` freezes the data and report artifacts committed by the original project
(commit `db4670335dac76628e6f2cd7b1806b2cd536abfb`). The files remain at their original
paths and are never modified. `snapshots/legacy-v0/manifest.json` records what they must
look like; `snapshots/legacy-v0/known_exceptions.json` records the documented data-quality
exceptions.

Reproduce every statement in this document with:

```bash
uv sync --locked
uv run atlas check            # or: uv run atlas --format json check
uv run pytest -m integration  # the same facts as automated tests
```

## Contents

| Role | Tables | Notes |
|---|---|---|
| `base` | `daily_total`, `daily_segments` | Raw aggregates returned by the legacy Socrata queries |
| `derived_aggregate` | `yearly_workload`, `ytd_workload`, `monthly_workload`, `weekday_workload`, `borough_workload`, `agency_workload` | Computed by notebook SQL from the base tables |
| `dimension` | `date_dimension` | Power BI calendar |
| `legacy_model_output` | `actual_forecast_series`, `forecast_test_results`, `future_30_day_forecast`, `capacity_risk_alerts`, `capacity_risk_summary`, `model_metrics`, `project_kpis` | Model, forecast and KPI exports |

16 tables (80,582 data rows) and 7 non-tabular artifacts (notebook, SQL script, Power BI
file, PDF, three screenshots). For every file the manifest stores SHA-256 and byte size.
For tables it also stores the exact header, the number of data rows and, for date-indexed
tables, the first and last date.

## Provenance

| Field | Value |
|---|---|
| Source | NYC Open Data, *311 Service Requests from 2020 to Present*, dataset `erm2-nwe9` |
| Extraction | Two aggregate SoQL queries in the legacy notebook (cells 2-3) |
| Window | 2023-01-01 inclusive to 2026-07-01 exclusive |
| Extracted at | **Not recorded.** Data ends 2026-06-30; the earliest repository timestamp is 2026-07-17 |
| Completeness | **Incomplete**: no extraction time, query hash, library versions or raw API responses |
| Terms of use | Not verified in this repository; confirm before redistribution |

Because the source is a live dataset whose records can be revised, re-querying it may not
reproduce these counts (not verified). `legacy-v0` is therefore the fixed reference for all
historical comparisons.

## Data contracts

`src/atlas/contracts.py` declares a contract for every table. Validation fails (exit code 1)
when any of these is violated:

- **Schema:** exact column set (no missing, no extra, no duplicated or blank names). A
  different column order only produces a warning.
- **Types:** `date` is strict `YYYY-MM-DD` and must be a real calendar date; `integer` is
  digits with an optional sign; `integral` also accepts a zero fraction such as `6209.0`
  (legacy float exports); `decimal` is a finite number; `string` must have no surrounding
  whitespace. An empty field is the only null; `NaN` or `NA` text is malformed.
- **Nulls, ranges, categories, patterns:** for example counts are non-negative, segment
  counts are at least 1, boroughs are one of the five named boroughs, agency codes match
  `[A-Z0-9][A-Z0-9-]*`.
- **Keys:** primary keys are unique (for example one row per date, or per date, agency and
  borough).
- **Calendar:** dates stay inside the declared window and, for daily tables, every calendar
  date of the window is present (`daily_total` and `daily_segments`: 2023-01-01 to 2026-06-30,
  1,277 days).
- **Files:** UTF-8, rectangular CSV (every row has as many fields as the header).

The legacy conventions are recorded as they are, not corrected: `weekday_workload` numbers
weekdays Sunday = 0 (SQLite `%w`), `date_dimension` uses Monday = 1 to Sunday = 7 with ISO week
numbers, and agency or borough averages are taken over active days only, because empty
segments are absent from `daily_segments`.

## City-wide totals versus borough-filtered segments

The two base tables describe **different populations**:

- `daily_total` counts **every** service request per day (no borough filter).
- `daily_segments` counts requests per agency and borough **only for the five named boroughs**
  (BRONX, BROOKLYN, MANHATTAN, QUEENS, STATEN ISLAND). Requests with any other borough value
  were never fetched at segment level.

Measured on `legacy-v0`:

| Measure | Value |
|---|---|
| City-wide total | 12,331,111 |
| Five-borough segment sum | 12,317,832 |
| Difference (requests not assigned to a named borough) | 13,279 (0.1077 %) |
| Dates with a difference | 1,273 of 1,277 |
| Dates that reconcile exactly | 4 |
| Dates where segments exceed the total | 0 |
| Largest single-day difference | 2,434 on 2023-09-15 (25.54 % of that day) |
| Days where the difference exceeds 2 % of the day | 1 (2023-09-15) |

**No records are invented to force reconciliation.** The excluded requests cannot be
recovered from this snapshot, so ATLAS measures the gap and accepts it only as the declared
exception below. Consequences:

- Agency and borough shares (for example NYPD 44.55 %, Brooklyn 30.38 %) are shares of the
  five-borough subset, not of all requests.
- Segment-level series do not sum to the city-wide series. Any future hierarchical model
  needs an explicit "unassigned borough" component, which requires re-extraction (M1).

## Declared known exceptions

A declared exception is accepted (reported as `INFO`) only when the observed statistics
match the declaration exactly. A gap with no declaration fails, a gap that differs from its
declaration fails (`exception_drift`), and a declaration that no longer applies is a warning
(`stale_exception`).

| Id | Check | What is accepted |
|---|---|---|
| `LEGACY-V0-EX-001` | `segment_reconciliation_gap` | The borough-filter gap above, with exactly the statistics in the table |
| `LEGACY-V0-EX-002` | `legacy_metric_reproduction` | Regression RMSE published as 676.70 but 676.69 when recomputed from the exported forecasts, because the notebook rounded predictions to integers before export and computed metrics on the unrounded values |

Every other published metric and KPI reproduces within its published precision.

## What reproduction covers

From the two base tables ATLAS recomputes the six derived aggregate tables and compares
every value (integers exactly; rounded values must be a valid rounding of the exact result).
It also recomputes all 14 values in `project_kpis` and all 10 values in `model_metrics`.
Historical summary: 1,277 days, 12,331,111 requests, mean 9,656.31 per day, minimum 5,596
(2023-12-25), maximum 22,806 (2026-02-24).

## Other observations (not contract violations; deferred to M1)

The read-only audit also noted patterns that the contracts deliberately do not fail on:
intermittent agency codes such as `3-1-1` (one request) and `NYC311-PRD`; days on which an
agency has no rows inside its active span (for example HPD on 2024-04-11 to 2024-04-14,
followed by 7,398 requests on 2024-04-15); and extreme days such as 2026-02-24. These need
an anomaly-flagging policy, which is planned for M1.
