# The `legacy-v0` data snapshot

`legacy-v0` freezes the data and report artifacts committed by the original project
(commit `db4670335dac76628e6f2cd7b1806b2cd536abfb`). The files remain at their original
paths and are never modified. Two files describe them:

- `snapshots/legacy-v0/manifest.json` records what the files must look like.
- `snapshots/legacy-v0/known_exceptions.json` records the documented data-quality exceptions.

To reproduce the measured statistics in this document:

```bash
uv sync --locked
uv run atlas check                        # or: uv run atlas --format json check
uv run pytest --no-cov tests/test_snapshot.py tests/test_validation.py \
    tests/test_reconciliation.py tests/test_reproduce.py   # the same facts as tests
```

(**M8 correction:** this pointed at `pytest -m integration`, which now selects 41 test
modules, 31 of them the long M2-M7 suites. The four modules above check the facts of this
document in under a minute.)

## Contents

| Role | Tables | Notes |
|---|---|---|
| `base` | `daily_total`, `daily_segments` | Aggregates from the legacy Socrata queries, after the notebook's type coercion and upper-casing (cell index 2-3) and date formatting for export (cell index 18) |
| `derived_aggregate` | `yearly_workload`, `ytd_workload`, `monthly_workload`, `weekday_workload`, `borough_workload`, `agency_workload` | Computed by notebook SQL from the base tables |
| `dimension` | `date_dimension` | Power BI calendar |
| `legacy_model_output` | `actual_forecast_series`, `forecast_test_results`, `future_30_day_forecast`, `capacity_risk_alerts`, `capacity_risk_summary`, `model_metrics`, `project_kpis` | Model, forecast and KPI exports |

The snapshot holds 16 tables (80,582 data rows) and 7 non-tabular artifacts: the notebook,
the SQL script, the Power BI file, the PDF and three screenshots.

- **Every file:** the manifest stores the SHA-256 hash and byte size.
- **Tables:** it also stores the exact header and the number of data rows.
- **Date-indexed tables:** it also stores the first and last date.

Integrity checks all of these.

## Provenance

| Field | Value |
|---|---|
| Source | NYC Open Data, *311 Service Requests from 2020 to Present*, dataset `erm2-nwe9` |
| Extraction | Two aggregate SoQL queries in the legacy notebook (cell index 2-3) |
| Window | 2023-01-01 inclusive to 2026-07-01 exclusive |
| Extracted at | **Not recorded.** The data ends 2026-06-30, and the earliest timestamp in the repository is 2026-07-17 (Power BI file). |
| Completeness | **Incomplete**: no extraction time, query hash, library versions or raw API responses |
| Terms of use | Not verified in this repository; confirm before redistribution |

The source is a live dataset whose records can be revised, so re-querying it may not
reproduce these counts (not verified). `legacy-v0` is therefore the fixed reference for all
historical comparisons.

## Data contracts

`src/atlas/contracts.py` declares a contract for every table. Validation fails (exit code 1)
when any of the following is violated.

**Schema.** The exact column set: no missing, extra, duplicated or blank column names. A
different column order only produces a warning.

**Types.** Digits must be ASCII (`0-9`).

| Kind | Rule |
|---|---|
| `date` | Strict `YYYY-MM-DD`, a real calendar date, within the supported range 1677-09-22 to 2262-04-11 |
| `integer` | Digits with an optional sign, within 64 bits |
| `integral` | Also accepts a zero fraction such as `6209.0` (legacy float exports) |
| `decimal` | A finite number; `1e999` is rejected |
| `string` | No surrounding whitespace |

An empty field is the only null. Null-like text (`NaN`, `NA`, `None`, `null`, `N/A`, in any
case) is rejected in every column, including text columns. The legacy notebook turned a
missing agency into the string `NAN`.

**Nulls, ranges, categories, patterns.** For example:

- counts are non-negative;
- segment counts are at least 1;
- boroughs are one of the five named boroughs;
- agency codes match `[A-Z0-9][A-Z0-9-]*`.

**Keys.** Primary keys are unique, for example one row per date, or per date, agency and
borough.

**Calendar.** Dates stay inside the declared window. For daily tables, every calendar date of
the window must be present: for `daily_total` and `daily_segments` that is 2023-01-01 to
2026-06-30, 1,277 days.

**Files.** UTF-8, valid CSV quoting, and every row has as many fields as the header.

The legacy conventions are recorded as they are, not corrected:

- `weekday_workload` numbers weekdays Sunday = 0 (SQLite `%w`).
- `date_dimension` uses Monday = 1 to Sunday = 7 with ISO week numbers.
- Agency and borough averages are taken over active days only, because empty segments are
  absent from `daily_segments`.

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

**No records are invented to force reconciliation.** ATLAS measures the gap and accepts it
only as the declared exception below. Consequences:

- Agency and borough shares (for example NYPD 44.55 %, Brooklyn 30.38 %) are shares of the
  five-borough subset, not of all requests.
- The daily residual (city-wide total minus segment sum) is available from `legacy-v0`.
  Breaking it down by agency, and confirming that it consists only of records without a named
  borough, requires a new extraction, which is deferred: it was outside the approved M1
  scope ([ROADMAP.md](ROADMAP.md), M1). (**M8 correction:** previously attributed to M1.)
  M1 represents the residual as the `UNASSIGNED` component
  ([DATA_QUALITY.md](DATA_QUALITY.md), section 2).

## Declared known exceptions

A declared exception is accepted (reported as `INFO`) only when the observed statistics match
the declaration exactly; all seven declared reconciliation statistics are compared.

- A gap with no declaration fails.
- A gap that differs from its declaration fails (`exception_drift`).
- A declaration that no longer applies, or matches nothing, is a warning (`stale_exception`).

| Id | Check | What is accepted |
|---|---|---|
| `LEGACY-V0-EX-001` | `segment_reconciliation_gap` | The borough-filter gap above, with exactly the statistics in the table |
| `LEGACY-V0-EX-002` | `legacy_metric_reproduction` | Regression RMSE published as 676.70 but 676.69 when recomputed from the exported forecasts, because the notebook rounded predictions to integers before export and computed metrics on the unrounded values |

Every other published metric and KPI reproduces within its published precision.

## What reproduction covers

| Table group | How it is checked |
|---|---|
| The six derived aggregate tables | Recomputed from the base tables; every value compared (integers exactly, rounded values must be a valid rounding of the exact result) |
| `forecast_test_results` | Actuals and the seven-day baseline re-derived from `daily_total`; errors re-derived from the exported predictions |
| `future_30_day_forecast` | Weekday, week and month labels re-derived from the dates |
| `capacity_risk_alerts`, `capacity_risk_summary` | Re-derived from the forecast and the threshold recomputed from `daily_total`, using the legacy rule |
| `date_dimension`, `actual_forecast_series` | Re-derived from `daily_total` and the forecast |
| `project_kpis` (14 values), `model_metrics` (10 values) | Recomputed |

The model predictions themselves (`model_forecast`, `forecast_requests`) are inputs. M0
fits no model, so they are checked only for consistency with every table that uses them.

Historical summary: 1,277 days; 12,331,111 requests; mean 9,656.31 per day; minimum 5,596
(2023-12-25); maximum 22,806 (2026-02-24).

## Other observations (from the read-only audit)

The independent audit of the original commit also noted patterns that the contracts
deliberately do not fail on:

- intermittent agency codes such as `3-1-1` (one request) and `NYC311-PRD`;
- days on which an agency has no rows inside its active span, for example HPD on 2024-04-11
  to 2024-04-14, followed by 7,398 requests on 2024-04-15;
- extreme days such as 2026-02-24.

The contracts still do not fail on them, by design. M1 implemented the anomaly-flagging
policy this section once announced: `uv run atlas quality` flags the HPD interruption of
2024-04-11..14, its catch-up on 2024-04-15 and the surge of 2026-02-24, among 28 flags. The
flags annotate the data and never change it; their rules, results and known limits are in
[DATA_QUALITY.md](DATA_QUALITY.md), section 3. (**M8 correction:** this section previously
said the policy was planned for M1 and that `atlas check` does not cover these patterns;
`atlas check` still does not, `atlas quality` does.)
