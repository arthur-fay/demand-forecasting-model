# Data card: the `legacy-v0` snapshot

ATLAS uses one frozen dataset, the `legacy-v0` snapshot: daily counts of NYC 311 service requests
from 2023-01-01 to 2026-06-30, inherited from the original project and registered, hashed and
validated by ATLAS M0. This card follows the questions of *Datasheets for Datasets*. The technical
reference is [DATA_SNAPSHOT.md](DATA_SNAPSHOT.md) (contracts and reproduction) and
[DATA_QUALITY.md](DATA_QUALITY.md) (reconciliation and anomaly flags); the models built on it are
described in the [model card](MODEL_CARD.md).

## 1. Source and attribution

| | |
|---|---|
| Source | **NYC Open Data**, *311 Service Requests from 2020 to Present*, dataset `erm2-nwe9`, published by the City of New York |
| Endpoint used | `https://data.cityofnewyork.us/resource/erm2-nwe9.json` (Socrata SoQL) |
| Window | 2023-01-01 inclusive to 2026-07-01 exclusive |
| Extracted | **not recorded** by the original run (the data end 2026-06-30; the earliest timestamp in the repository is 2026-07-17) |
| Registered in ATLAS | 2026-10-12 (`snapshots/legacy-v0/manifest.json`), from the original commit `db46703` |

**Attribution.** Source data: NYC Open Data, 311 Service Requests (dataset `erm2-nwe9`), City of
New York. The tables in this repository are aggregates derived from it by the original project
(counts by day, and by day, agency and borough). ATLAS is an independent research project; it is
not affiliated with, sponsored or endorsed by the City of New York or NYC Open Data.

**Terms of use: not verified.** The City publishes the terms that govern NYC Open Data
(nyc.gov, *Terms of Use*), and individual datasets can carry further terms from the agency that
supplies them. This repository has never recorded or checked them, and the M8 audit environment
could not retrieve them, because its network policy blocks nyc.gov. The repository is public
(since 2026-07-18), so the aggregates are already redistributed. The owner must therefore read the
current terms now, confirm that redistribution of these aggregates is permitted, and add any
required attribution or disclaimer wording. The alternative is to make the repository private until
that is done. The release gate makes this a condition of the release
([release/RELEASE_GATE.md](release/RELEASE_GATE.md) section 1). Nothing in ATLAS asserts a
permission.

## 2. Composition

The snapshot registers 16 tables (80,582 data rows) and 7 non-tabular artifacts. Every file is
fixed by SHA-256 and size; every table also by its exact header, row count and, for daily tables,
its date range.

| Role | Table | Rows | Content |
|---|---|---:|---|
| base | `daily_total` | 1,277 | city-wide requests per calendar day (no borough filter) |
| base | `daily_segments` | 76,458 | requests per day, agency and borough, **five named boroughs only** |
| derived aggregate | `yearly_workload`, `ytd_workload`, `monthly_workload`, `weekday_workload`, `borough_workload`, `agency_workload` | 4, 2, 42, 7, 5, 17 | computed by the original notebook from the base tables |
| dimension | `date_dimension` | 1,307 | the Power BI calendar |
| legacy model output | `actual_forecast_series`, `forecast_test_results`, `future_30_day_forecast`, `capacity_risk_alerts`, `capacity_risk_summary`, `model_metrics`, `project_kpis` | 1,307, 90, 30, 30, 3, 2, 1 | the original project's model exports and KPIs |

Artifacts: the original Colab notebook, the SQLite script, the Power BI file, the PDF report and
three screenshots.

**What one row is.** In `daily_total`, one calendar day of request creation dates (the Socrata
floating timestamp, so NYC local days; daylight-saving days have 23 or 25 hours, which the audit
found immaterial). Counts are of requests created, not resolved.

**Personal data.** None. The snapshot holds aggregate counts only; no request text, address,
location or identifier. The legacy Power BI file embeds generic local file paths of the original
author's machine (no personal data).

**Summary.** 12,331,111 requests over 1,277 days, mean 9,656.3 per day; minimum 5,596
(2023-12-25), maximum 22,806 (2026-02-24). No missing or duplicate dates.

## 3. Collection and preprocessing

Two aggregate SoQL queries in the original notebook (cells 2-3): `count(*)` grouped by
`date_trunc_ymd(created_date)` without a borough filter (`daily_total`), and grouped by date,
agency and borough restricted to BRONX, BROOKLYN, MANHATTAN, QUEENS and STATEN ISLAND
(`daily_segments`). Neither query hit its row limit (5,000 and 500,000). The notebook upper-cased
agency and borough names, coerced types and exported the tables for Power BI (cells 8-18). ATLAS
changes none of these files; it reads them through strict contracts and records the legacy
conventions as they are (for example Sunday = 0 in `weekday_workload`).

**Not recorded (provenance gaps):** the extraction time, a query hash, row-limit checks, API
response metadata, the raw responses and the versions of the libraries used. The source is a live
dataset whose records can be revised, so re-querying it may not reproduce these counts; ATLAS treats
`legacy-v0` as the fixed reference and never re-fetches it.

## 4. Known properties and quality

- **Two populations.** City-wide totals include requests without a named borough; the segments do
  not. The difference is 13,279 requests (0.1077 %) on 1,273 of 1,277 days, at most 2,434 on
  2023-09-15 (25.5 % of that day), accepted only as the declared exception `LEGACY-V0-EX-001`.
  Agency and borough shares (for example NYPD 44.55 %, Brooklyn 30.38 %) are shares of the
  five-borough subset.
- **Anomalies.** `atlas quality` flags 28 events: 11 city-wide surges, 4 low-volume days (all
  holidays), 8 agency intake interruptions (for example HPD 2024-04-11 to 04-14, followed by 7,398
  requests on 04-15), 4 catch-up surges and 1 day with a large unassigned share. The detectors are
  robust but incomplete: they miss part of the winter 2025-26 cluster, including 2026-01-27 (19,225
  requests, 70 % above its same-weekday median), and a second DHS outage within four weeks of a
  first ([DATA_QUALITY.md](DATA_QUALITY.md)).
- **Coding changes.** Agency codes change over time (for example `DOE` stops on 2026-03-19,
  `OOS` starts on 2025-09-17, `NYC311-PRD` appears only from 2026-03-02 to 04-11); immaterial for
  city-wide totals.
- **Legacy metric rounding.** The original RMSE (676.70) differs from its recomputation (676.69)
  because the notebook rounded predictions before export; declared as `LEGACY-V0-EX-002`. Every
  other legacy figure reproduces within its published precision (`atlas check`).

## 5. Uses

**Used for:** the M2 rolling-origin backtest and the archived projection, the M3 intervals, the
M4 hypothetical scenarios, the API and the dashboard, all as a frozen research dataset.

**Not suitable for:** any statement about service need, response quality or equity (311 counts
measure reporting behaviour, which differs between communities); agency-level capacity analysis
(no capacity data, and the five-borough filter drops requests); anything after 2026-06-30.

## 6. Integrity, distribution and maintenance

- **Integrity.** `atlas check` verifies every hash, size, header, row count and date range, then
  validates every contract and reproduces the derived tables and every legacy KPI. Loading fails
  closed on any mismatch. The files are stored without line-ending conversion (`.gitattributes`).
- **Distribution.** The tables are committed to this public repository. Their redistribution is
  subject to the NYC Open Data terms above, which have not been confirmed (owner action).
  **(M9)** The static demonstration would also publish the daily totals, and everything
  computed from them, on a public web page (GitHub Pages). It is not published, and its
  publication is gated on the same confirmation ([STATIC_DEMO.md](STATIC_DEMO.md) section 9).
- **Maintenance.** `legacy-v0` is immutable. New data would be a new snapshot (`legacy-v1` or
  later) with recorded extraction metadata, a pagination and truncation guard and its own
  manifest ([ROADMAP.md](ROADMAP.md), deferred work).
