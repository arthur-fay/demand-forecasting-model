# ATLAS: reproducible forecasting research on NYC 311 demand

ATLAS asks how well the daily number of NYC 311 service requests can be forecast 1 to 30
days ahead, how uncertain those forecasts are, and what a planning model would do with them
under stated, hypothetical assumptions. It is a reproducible research demonstration, rebuilt
from a July 2026 portfolio project ([archived below](#the-original-project-july-2026-archived)).
It is **not an operational tool**: it works on a frozen snapshot of the data whose last day is
2026-06-30, its forecasts are archived projections, and it makes no staffing, budget or
capacity recommendation. "Demand" means requests created per day (intake); the data hold
nothing on closures, backlog, staffing or service levels.

> **Status: ATLAS v1.0, release candidate under review.** Milestones M0-M7 are merged. M8 is
> the final audit and its corrections: see the [audit](docs/audit/README.md) and the
> [release gate](docs/release/RELEASE_GATE.md). The code carries the version being released
> (1.0.0); the candidate status is carried by the release-candidate manifest and by a tag
> `v1.0.0-rc.1` that only the owner creates after approval.

## What ATLAS found

Every number below comes from a committed result in [`docs/results/`](docs/results/), or,
for the M8 erratum, from the recorded size study in
[`docs/audit/scripts/`](docs/audit/scripts/dm_size_study_output.md); the linked documents
give the protocols and the full tables.

1. **Forecast accuracy: a lead over simple baselines that is not statistically
   established** ([BACKTESTING.md](docs/BACKTESTING.md)). Seven models, refitted at each of
   822 historical forecast origins on the data up to that origin only, forecast 1-30 days
   ahead under a protocol fixed before any run: the model is chosen on a development period
   and confirmed on a later confirmation year (targets 2025-07-01 to 2026-06-30, the last
   year in the data). On that year the selected regression's mean absolute error over
   horizons 1-30 is **984.9 requests per day** (WAPE 9.2 %), against 1,112.2 for the best
   simple baseline B\* (the median of the last five same weekdays) and 1,371.5 for seasonal
   naive.
   - Against B\* it is 11.4 % lower, but the 95 % interval of the difference, −256 to +2
     requests per day, includes zero, and the Holm-adjusted p-value is 0.054: **not
     significant**.
   - Against seasonal naive it is 28.2 % lower. The Holm-adjusted p-value is 0.007 nominal,
     and 0.045 after the M8 size correction below. It rises above 0.05 under three of the five
     HAC bandwidths once each is size-corrected, and the correction itself assumes shorter
     error dependence than the data show. ⟦H2-DEPENDENCE-README⟧ So this advantage, too, is
     borderline rather than established.
   - These are re-forecasts of days already in the data, not a prospective validation.
2. **M8 statistical erratum: the selection evidence is weak**
   ([BACKTESTING.md, section 12](docs/BACKTESTING.md#12-m8-erratum-the-size-of-the-diebold-mariano-test)).
   The pre-registered development selection test (HLN-DM, conservative p 0.044) passed its
   5 % gate as computed. At a 30-day horizon with 336-486 overlapping origins, however, this
   test over-rejects under its own assumptions: 7.6-9.1 % false rejections at a nominal 5 %,
   measured in M8.
   - After a size correction the selection p-value is 0.069, above the gate.
   - A correctly sized test would therefore most likely have kept the simpler baseline B\*.
   - The regression remains the recommended model only under the pre-registered rule as
     computed, and its statistical support is weak.
   - The M2 statements that the selection and H2 are robust to the HAC bandwidth do not hold
     once each bandwidth is size-corrected.
   - The disclosed H1 result (not significant after Holm) is unchanged.
3. **Prediction intervals: FAILED_CALIBRATION** ([UNCERTAINTY.md](docs/UNCERTAINTY.md)).
   Intervals and exceedance probabilities are built only from errors observed by each
   issue date, and were selected and evaluated under a pre-registered protocol. Over the
   evaluation year the 95 % intervals contained 92.1 % of outcomes (the 80 % intervals
   79.9 %), more than 3 points short of 95 % at horizons 8-30, so their pre-registered status
   is FAILED_CALIBRATION. The evidence is also thin: the year's 336 overlapping 30-day paths
   are worth about 8-9 independent observations. No interval or probability is
   decision-grade.
4. **Planning: optimal plans for a HYPOTHETICAL model** ([OPTIMIZATION.md](docs/OPTIMIZATION.md)).
   A linear programme of daily service, overtime, surge and backlog over the projected
   demand. Its plans are certified optimal within small numerical tolerances (primal
   feasibility to 1e-7 plus 1e-9 of the magnitude; dual feasibility and the duality gap to
   1e-9 relative; and a matrix-free check of every plan), but only for a model whose
   staffing, productivity, cost and budget numbers are all invented and labelled
   `HYPOTHETICAL`: no such data exist in the project. Six of its nine reference scenarios
   have no plan that meets every hypothetical constraint. Nothing in it is a
   recommendation.
5. **Reproducibility.** Every committed result reproduces byte for byte. Each run records
   the fingerprints of its configuration, data, code and outputs, and `verify` regenerates
   it from the snapshot and compares every file. This was checked again in M8 for the M2,
   M3 and M4 reference runs (the sealed M2 development record with the code of its own
   commit, as [BACKTESTING.md](docs/BACKTESTING.md), section 8, explains). Results that pass
   through BLAS, the regressions, can differ in the last bits on another CPU family
   ([DEVELOPMENT.md](docs/DEVELOPMENT.md)).
6. **The original project's headline figures** reproduce arithmetically from its data
   within their published precision, except one declared rounding exception (the
   regression RMSE: 676.70 published, 676.69 recomputed, `LEGACY-V0-EX-002`), but several
   do not mean what they were said to mean. The archived section below shows each one with
   what it actually measures.

## What is in the repository

| Milestone | What it adds | Documentation |
|---|---|---|
| M0 | The frozen, hash-verified `legacy-v0` snapshot; fail-closed data contracts; reproduction of every legacy aggregate, KPI and model metric within its published precision, except one declared RMSE rounding exception (`LEGACY-V0-EX-002`), each with its actual meaning | [DATA_SNAPSHOT.md](docs/DATA_SNAPSHOT.md), [LEGACY_LIMITATIONS.md](docs/LEGACY_LIMITATIONS.md) |
| M1 | Causal data-quality flags, ISO 8601 calendar conventions, a reconciliation residual, a feature builder with leakage guards | [DATA_QUALITY.md](docs/DATA_QUALITY.md), [FEATURES.md](docs/FEATURES.md) |
| M2 | Rolling-origin backtesting of seven models, a pre-registered selection and confirmation protocol, dependence-aware tests | [BACKTESTING.md](docs/BACKTESTING.md) |
| M3 | Prediction intervals and exceedance probabilities, with a pre-registered decision-grade test that they failed (FAILED_CALIBRATION) | [UNCERTAINTY.md](docs/UNCERTAINTY.md) |
| M4 | A HYPOTHETICAL linear-programming simulation with optimality certificates, policies, tradeoff frontiers and sensitivity analysis | [OPTIMIZATION.md](docs/OPTIMIZATION.md) |
| M5 | An HTTP API over the committed outputs, with bounded HYPOTHETICAL simulations; every response states what its numbers are and carries `validated_recommendation: false` | [API.md](docs/API.md) |
| M6 | A private-first deployment package for one Ubuntu 24.04 server: systemd and nginx, verified activation, automatic rollback, monitoring. Nothing has been deployed | [DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| M7 | An accessible web dashboard over the API (React and TypeScript, built once into the committed `web/dist`; the server needs no Node.js) | [DASHBOARD.md](docs/DASHBOARD.md) |
| M8 | The release audit, its verified findings and their corrections | [audit](docs/audit/README.md) |

## Run it

Requirements: [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync --locked              # the locked dependencies
uv run atlas check            # snapshot integrity, data contracts, legacy reproduction
uv run atlas quality          # data-quality flags
uv run atlas api serve        # the API and the dashboard at http://127.0.0.1:8000/
```

Reproduce the committed results; each command regenerates a run and compares every file:

```bash
uv run atlas backtest verify docs/results/atlas-m2-rolling-origin-v1/full   # M2, about 7 minutes
uv run atlas uncertainty verify docs/results/atlas-m3-intervals-v1/full     # M3, about 5 minutes
uv run atlas optimize verify docs/results/atlas-m4-optimization-v1          # M4, about 30 seconds
```

Tests, linting and the dashboard build are described in
[DEVELOPMENT.md](docs/DEVELOPMENT.md). The checks a release must pass, and their recorded
results, are in the [release gate](docs/release/RELEASE_GATE.md).

## Documentation

| Document | What it covers |
|---|---|
| [Model card](docs/MODEL_CARD.md) | The forecasting, interval and planning models: intended use, evaluation, limits |
| [Data card](docs/DATA_CARD.md) | The `legacy-v0` data: source, extraction, contents, quality, licence status |
| [Known limitations](docs/KNOWN_LIMITATIONS.md) | Every disclosed limitation in one place |
| [Release gate](docs/release/RELEASE_GATE.md) | The release checks and their results |
| [M8 audit](docs/audit/README.md) | The release audit: baseline assessment, findings register, evidence |
| [Data snapshot](docs/DATA_SNAPSHOT.md) | Provenance, contracts and reconciliation of the frozen data |
| [Data quality](docs/DATA_QUALITY.md) | Calendar conventions, the reconciliation residual, data-quality flags and their limits |
| [Features](docs/FEATURES.md) | The leakage-guarded feature builder |
| [Backtesting](docs/BACKTESTING.md) | The M2 protocol, results and the M8 erratum |
| [Uncertainty](docs/UNCERTAINTY.md) | The M3 protocol, results and decision status |
| [Optimisation](docs/OPTIMIZATION.md) | The HYPOTHETICAL M4 simulation |
| [HTTP API](docs/API.md) | The M5 API and its contract |
| [Deployment](docs/DEPLOYMENT.md) | The M6 deployment package and operations |
| [Dashboard](docs/DASHBOARD.md) | The M7 web dashboard |
| [Architecture](docs/ARCHITECTURE.md) | Modules, data flows and design decisions |
| [Development](docs/DEVELOPMENT.md) | Setup, tests, continuous integration, reproducibility across machines |
| [Roadmap](docs/ROADMAP.md) | Milestones, deferred work and known limitations |
| [Legacy limitations](docs/LEGACY_LIMITATIONS.md) | What each original headline figure does and does not show |

## Data and attribution

The data are daily counts from **NYC Open Data, *311 Service Requests from 2020 to
Present*** (dataset `erm2-nwe9`), published by the City of New York. The original project
aggregated them once, for 2023-01-01 to 2026-06-30; ATLAS freezes those aggregates as the
hash-verified `legacy-v0` snapshot. The extraction time was not recorded
([DATA_SNAPSHOT.md](docs/DATA_SNAPSHOT.md)). ATLAS is not affiliated with or endorsed by the
City of New York. The source's terms of use were not verified in this repository. This
public repository already redistributes the derived tables. Confirming that the terms permit
this, and the attribution wording they require, is an owner action and a condition of the
release ([release/RELEASE_GATE.md](docs/release/RELEASE_GATE.md) section 1).

## Licence

There is no licence file. The repository is public, but no licence has been granted for the
code or the derived data. Choosing one, or deciding that none is granted, is the owner's
decision and a condition of the release ([release/RELEASE_GATE.md](docs/release/RELEASE_GATE.md)
section 1).

## The original project (July 2026, archived)

ATLAS was rebuilt from a portfolio project published in July 2026 as *Demand & Workload
Forecasting Model with Capacity-Risk Alerts*, which described itself as "an end-to-end
analytics project that transforms historical operational request data into a 30-day demand
forecast, evaluates model accuracy, and identifies future dates when forecast demand may
exceed planning capacity". Its files (notebook, SQL, Power BI report, PDF, screenshots and
CSV exports) are kept unchanged and are hash-verified as part of `legacy-v0`.

This section keeps that project's README content, with each claim qualified where it
stands. The qualifications come from `atlas check`, which prints the actual meaning next to
every reproduced figure, and from [LEGACY_LIMITATIONS.md](docs/LEGACY_LIMITATIONS.md). Two
words need translating throughout: **"workload"** was the number of requests created
(intake), not work, and **"planning capacity"** was the 75th percentile of the last 180 days
of incoming requests, a demand statistic, not any agency's capacity. The unedited original
README is in the git history (commit `db46703`).

### Original dashboard

![Original Power BI overview](05%20Screenshots/Demand_Forecasting_Overview.png)

*The original Power BI overview. Its "Model MAPE" is a one-step-ahead holdout score, not
30-day forecast accuracy, and its "planning capacity" is a demand percentile (see the KPI
table below).*

![Original capacity-risk details](05%20Screenshots/Capacity_Risk_Details.png)

*The original capacity-risk page. Its "Recommended Action" column holds the original
report's illustrative rules, attached to tiers of a demand percentile. No capacity, staffing
or backlog data support them, and ATLAS makes no recommendations.*

### Original overview

The original project was presented as helping operations leaders "anticipate workload,
evaluate forecast reliability, and prepare for capacity shortfalls before they occur". Its
workflow, with what each step was:

- historical request validation and aggregation;
- daily, monthly, yearly, weekday, agency and borough analysis (agency and borough shares
  cover the five named boroughs only);
- training and a single 90-day holdout test at horizon 1;
- a baseline and regression comparison on that holdout;
- a 30-day recursive forecast for 2026-07-01 to 2026-07-30, never evaluated beyond horizon 1;
- a "planning-capacity" threshold, which is a demand percentile;
- risk tiers of that percentile, with illustrative rules per tier;
- an interactive Power BI report.

### Original KPIs, with what they actually measure

| KPI (as published) | Result | What it actually measures |
|---|---:|---|
| Historical Requests | 12.33M | Sum of city-wide daily 311 requests created, 2023-01-01 to 2026-06-30 (12,331,111). |
| Historical Average Daily Requests | 9,656 | Mean city-wide requests per calendar day. |
| 30-Day Forecast Requests | 330,941 | Sum of the legacy recursive 30-day point forecast for 2026-07-01..30. Never evaluated beyond horizon 1; an archived projection, never scored. |
| Forecast Average Daily Requests | 11,031 | Mean of that point forecast. |
| Model MAPE | 4.93% | Holdout MAPE at horizon 1 with observed lag features, on one 90-day block (2026-04-02 to 2026-06-30). Not the accuracy of the published 30-day forecast. |
| Baseline MAPE | 8.23% | Weekly seasonal-naive `y(t−7)` on the same block; it uses less recent information than the model, so the comparison is not like for like. |
| MAPE Improvement | 40.10% | Relative reduction of MAPE versus that baseline (8.23 % to 4.93 %, 3.30 points), not a gain in accuracy. |
| 2026 YTD Growth | 13.40% | Requests created from 1 January to 30 June 2026 against the same period of 2025. |
| Planning Capacity | 11,609 requests/day | 75th percentile of the last 180 days of incoming requests: a demand percentile, not measured operational capacity. |
| Capacity Shortfall Days | 5 | Forecast days above that percentile; each excess (12 to 210 requests) is smaller than the model's own one-step error (MAE 514.3). |
| Total Forecast Capacity Gap | 566 requests | Sum of the positive point-forecast excesses over the percentile; ignores backlog carry-over and forecast uncertainty. |
| Maximum Forecast Utilization | 101.81% | Largest point forecast divided by the demand percentile. |
| First Forecast Shortfall | July 13, 2026 | First forecast day above the demand percentile. |
| Advance Warning | 13 days | Days from the data cutoff (2026-06-30) to that day: a horizon index, not a validated lead time. |

### Original insights, and what ATLAS found

- **Original:** "The selected regression forecast achieved a 4.93% MAPE, compared with
  8.23% for the 7-day seasonal baseline." **ATLAS:** reproduced exactly, but it is a
  one-step-ahead holdout score with observed lags. Run recursively, as the published
  forecast was, the same model scores 5.5-5.9 % at horizons 2-30 on those 90 days, which
  were an easy period, and 9.0-9.8 % from horizon 7 on over the whole confirmation year
  (targets 2025-07-01 to 2026-06-30, which end with those 90 days).
- **Original:** "Forecast accuracy improved by 40.10% over the baseline model." **ATLAS:** a
  40.1 % relative reduction of MAPE (8.23 % to 4.93 %), on a comparison in which the model
  saw the previous day and the baseline only the day a week earlier; not a 40 % gain in
  accuracy.
- **Original:** "The next 30 days are expected to generate approximately 330,941 requests."
  **ATLAS:** an archived projection for 2026-07-01 to 2026-07-30 from data ending
  2026-06-30, never scored: no outcome after 2026-06-30 is in the snapshot. ATLAS's own
  archived projection for the same days totals 333,968, also unscored.
- **Original:** "Five forecast dates are expected to exceed the daily planning capacity of
  11,609 requests." **ATLAS:** five point forecasts exceed a demand percentile, not a
  capacity, by 12 to 210 requests, 13 to 28 days ahead. At those horizons the legacy
  model's recursive mean absolute error over the confirmation year (targets 2025-07-01 to
  2026-06-30) is 1,067-1,104 requests per day, more than twice its one-step holdout MAE.
- **Original:** "The projected total capacity gap is 566 requests, with the earliest
  shortfall forecast for July 13, 2026." **ATLAS:** 566 is the sum of those five excesses.
  Over the 30 days the forecast total (330,941) is below 30 times the percentile (348,270).
- **Original:** "The model provides 13 days of advance warning before the first expected
  shortfall." **ATLAS:** 13 is the number of days from the data cutoff to the first forecast
  day above the percentile: a horizon index, not a validated lead time. The project's dated
  files are all later than that day (Power BI file 2026-07-17, PDF 2026-07-18).
- **Original:** "2026 year-to-date workload is 13.40% higher than the comparable 2025
  period." **ATLAS:** reproduced; it counts requests created, not workload.
- **Original:** "NYPD represents the largest agency workload share at approximately 44.55%"
  and "Brooklyn represents the largest borough workload share at approximately 30.38%."
  **ATLAS:** reproduced, as shares of the requests with one of the five named boroughs
  (13,279 requests, 0.11 %, have none), and of requests created, not workload.
- **Original:** "Tuesday has the highest average weekday workload at approximately 10,283
  requests." **ATLAS:** reproduced; again requests created.

### Original forecasting method, and what ATLAS found

The original project compared two approaches on its 90-day holdout, each day predicted from
the observed previous days:

1. **7-day seasonal baseline:** test MAPE 8.23 %.
2. **Linear regression:** test MAPE 4.93 %, RMSE 676.70 (676.69 when recomputed from the
   exported forecasts, a declared exception), MAE 514.30, R² 0.5787 (the same one-step
   holdout; ATLAS does not report test-set R²).

**Original:** "The linear regression model was selected because it produced the lowest
holdout-period error." **ATLAS:** it was chosen on that single one-step holdout. In ATLAS's
rolling-origin backtest, the exact re-implementation of this regression is worse than three
simple baselines beyond day 1 in the development period (MAE 1,041.9 against 953.0 for the
median of the last five same weekdays; none of these deficits is statistically
significant) and better than them in the confirmation year (5.8 % ahead of that median,
p 0.26). Its coefficients are not identifiable, 465 of its development forecasts are not
even estimable, and ATLAS's backtesting concludes that it should not be used
([BACKTESTING.md](docs/BACKTESTING.md), finding 6).

### Original capacity-risk logic

The original report compared each forecast day with the demand percentile of 11,609
requests per day and assigned a tier by the ratio of the two ("utilization"): Critical at
110 % or more, High above 100 %, Medium from 95 %, Low below. No day reached Critical.

| Risk Level | Forecast Days | Average Utilization | Total Capacity Gap |
|---|---:|---:|---:|
| High | 5 | 100.97% | 566 |
| Medium | 13 | 97.23% | 0 |
| Low | 12 | 90.16% | 0 |

"Utilization" and "capacity gap" are measured against a demand percentile, and no forecast
interval or probability entered the tiers. The original report attached an illustrative
rule to each tier ("High: add capacity and prioritize high-impact requests"; "Medium:
monitor closely and prepare backup capacity"; "Low: continue the normal operating plan").
These rules rest on no capacity, staffing or backlog data; they are not recommendations,
and ATLAS makes none.

### Original dashboard pages

- **Overview:** historical requests, 30-day forecast requests, model MAPE (one-step
  holdout), 2026 YTD growth, forecast demand against the demand percentile ("planning
  capacity"), actual against forecast requests, navigation to the risk details.
- **Capacity Risk Details:** forecast date, forecast requests, the demand percentile
  ("planning capacity"), risk tier, excess over the percentile ("capacity gap"), the
  illustrative rule ("recommended operational action"), weekday and risk-tier filters.

### Data model

![Original Power BI data model](05%20Screenshots/Demand_Forecasting_Data_Model.png)

The Power BI report used analytical tables for: historical daily totals and operational
segments; date attributes; actual and forecast series; forecast test results; model
metrics; the 30-day forecast; the risk tiers and their summary; agency, borough, weekday,
monthly, yearly and year-to-date request counts; and the project KPIs.

### Tools and technologies

Python, pandas, NumPy, scikit-learn, Matplotlib, SQL and SQLite, Google Colab, Power BI
Desktop, Power Query, DAX, Git and GitHub.

### Original skills list

The original README listed these skills, qualified here where the project's work differs
from the name:

- time-series forecasting (a 30-day recursive forecast, evaluated at horizon 1 only);
- feature engineering;
- train/test validation (one 90-day holdout block);
- forecast-error measurement;
- baseline-model comparison (against a baseline with less recent information);
- "capacity planning" (a threshold at a demand percentile; no capacity data);
- risk classification (tiers of that percentile);
- operational analytics (of requests created);
- SQL validation and reporting;
- Power BI dashboard development;
- data modelling and DAX;
- executive data storytelling (its claims are qualified above);
- GitHub repository documentation.

### Original repository structure

The original files, at their original paths (the current repository adds `src/`, `tests/`,
`docs/`, `configs/`, `snapshots/`, `deploy/` and `web/`; see
[DEVELOPMENT.md](docs/DEVELOPMENT.md)):

```text
demand-forecasting-model/
├── 01 Data/
│   ├── actual_forecast_series.csv
│   ├── agency_workload.csv
│   ├── borough_workload.csv
│   ├── capacity_risk_alerts.csv
│   ├── capacity_risk_summary.csv
│   ├── daily_segments.csv
│   ├── daily_total.csv
│   ├── date_dimension.csv
│   ├── forecast_test_results.csv
│   ├── future_30_day_forecast.csv
│   ├── model_metrics.csv
│   ├── monthly_workload.csv
│   ├── project_kpis.csv
│   ├── weekday_workload.csv
│   ├── yearly_workload.csv
│   └── ytd_workload.csv
├── 02 Notebooks/
│   └── Project_2_Demand_Workload_Forecasting.ipynb
├── 03 SQL/
│   └── Project2_Demand_Forecasting_Analysis.sql
├── 04 Power BI/
│   └── Project_2_Demand_Workload_Forecasting.pbix
├── 05 Screenshots/
│   ├── Demand_Forecasting_Overview.png
│   ├── Capacity_Risk_Details.png
│   └── Demand_Forecasting_Data_Model.png
├── 06 Exports/
│   └── Project2_Demand_Forecasting_Report.pdf
└── README.md
```

### Original project files

- [The Google Colab notebook](02%20Notebooks/Project_2_Demand_Workload_Forecasting.ipynb)
- [The SQL analysis](03%20SQL/Project2_Demand_Forecasting_Analysis.sql)
- [The Power BI dashboard](04%20Power%20BI/Project_2_Demand_Workload_Forecasting.pbix)
- [The PDF report](06%20Exports/Project2_Demand_Forecasting_Report.pdf)
- [The overview screenshot](05%20Screenshots/Demand_Forecasting_Overview.png)
- [The capacity-risk screenshot](05%20Screenshots/Capacity_Risk_Details.png)
- [The Power BI data model](05%20Screenshots/Demand_Forecasting_Data_Model.png)

The PDF report and the Power BI file carry the original claims unqualified; read them with
the table above.

## Author

**Arthur Fay**

Data Analytics and Business Intelligence Portfolio Project
