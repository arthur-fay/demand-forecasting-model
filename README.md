# Demand & Workload Forecasting Model with Capacity-Risk Alerts

An end-to-end analytics project that transforms historical operational request data into a 30-day demand forecast, evaluates model accuracy, and identifies future dates when forecast demand may exceed planning capacity.

> **ATLAS engineering foundation (milestone M0).** This project is being rebuilt as ATLAS, a
> reproducible and tested Python application. The original project artifacts (notebook, SQL,
> Power BI, PDF, screenshots and CSV data) are preserved unchanged; in this README only the
> broken notebook link was corrected, and the repository tree below shows the original
> layout (see the [development guide](docs/DEVELOPMENT.md) for the new one). The data (NYC
> Open Data 311 Service Requests, dataset `erm2-nwe9`) is frozen and hash-verified as the
> `legacy-v0` snapshot.
>
> **Read before relying on the headline figures:** the 4.93% MAPE is a one-step-ahead holdout
> score using observed values, not validated 30-day forecast accuracy, and "planning capacity"
> is the 75th percentile of recent incoming requests, not operational service capacity. See
> [Known limitations of the legacy claims](docs/LEGACY_LIMITATIONS.md).
>
> **M2 backtest:** ATLAS now evaluates the recursive 30-day procedure the original forecast
> actually used, from hundreds of historical origins, against simple baselines. See
> [Forecasting and backtesting (M2)](docs/BACKTESTING.md) for the protocol and the measured
> results before quoting any accuracy figure.
>
> **M3 uncertainty:** horizon-specific prediction intervals and threshold-exceedance
> probabilities, calibrated only on errors observed by each issue date and evaluated with a
> pre-registered, dependence-aware protocol. See
> [Prediction intervals and probabilistic risk (M3)](docs/UNCERTAINTY.md), including the
> decision-grade status, before using any interval or probability.
>
> **M4 decision simulation:** a linear-programming model of service, overtime, surge and
> backlog that turns the forecast into **hypothetical** resource-allocation scenarios, with
> certified optima, policy comparisons, tradeoff frontiers and sensitivity analysis. Every
> operational parameter is `HYPOTHETICAL`; nothing in it is a validated staffing or budget
> recommendation. See [Decision simulation and optimisation (M4)](docs/OPTIMIZATION.md).
>
> Quick start: `uv sync --locked && uv run atlas check && uv run atlas quality` ·
> [Development guide](docs/DEVELOPMENT.md) · [Architecture](docs/ARCHITECTURE.md) ·
> [Data snapshot](docs/DATA_SNAPSHOT.md) · [Data quality (M1)](docs/DATA_QUALITY.md) ·
> [Forecast features (M1)](docs/FEATURES.md) · [Backtesting (M2)](docs/BACKTESTING.md) ·
> [Uncertainty (M3)](docs/UNCERTAINTY.md) · [Optimisation (M4)](docs/OPTIMIZATION.md) ·
> [Roadmap](docs/ROADMAP.md)

## Dashboard Preview

![Demand Forecasting Overview](05%20Screenshots/Demand_Forecasting_Overview.png)

## Capacity Risk Details

![Capacity Risk Details](05%20Screenshots/Capacity_Risk_Details.png)

## Project Overview

This project was designed to help operations leaders anticipate workload, evaluate forecast reliability, and prepare for capacity shortfalls before they occur.

The workflow includes:

- Historical workload validation and aggregation
- Daily, monthly, yearly, weekday, agency, and borough analysis
- Time-series training and holdout testing
- Baseline and regression-model comparison
- 30-day demand forecasting
- Planning-capacity thresholds
- Risk-level classification and recommended actions
- Interactive Power BI reporting

## Executive KPIs

| KPI | Result |
|---|---:|
| Historical Requests | 12.33M |
| Historical Average Daily Requests | 9,656 |
| 30-Day Forecast Requests | 330,941 |
| Forecast Average Daily Requests | 11,031 |
| Model MAPE | 4.93% |
| Baseline MAPE | 8.23% |
| MAPE Improvement | 40.10% |
| 2026 YTD Growth | 13.40% |
| Planning Capacity | 11,609 requests/day |
| Capacity Shortfall Days | 5 |
| Total Forecast Capacity Gap | 566 requests |
| Maximum Forecast Utilization | 101.81% |
| First Forecast Shortfall | July 13, 2026 |
| Advance Warning | 13 days |

## Key Business Insights

- The selected regression forecast achieved a **4.93% MAPE**, compared with **8.23%** for the 7-day seasonal baseline.
- Forecast accuracy improved by **40.10%** over the baseline model.
- The next 30 days are expected to generate approximately **330,941 requests**.
- Five forecast dates are expected to exceed the daily planning capacity of **11,609 requests**.
- The projected total capacity gap is **566 requests**, with the earliest shortfall forecast for **July 13, 2026**.
- The model provides **13 days of advance warning** before the first expected shortfall.
- 2026 year-to-date workload is **13.40% higher** than the comparable 2025 period.
- NYPD represents the largest agency workload share at approximately **44.55%**.
- Brooklyn represents the largest borough workload share at approximately **30.38%**.
- Tuesday has the highest average weekday workload at approximately **10,283 requests**.

## Dashboard Pages

### Overview

- Historical Requests
- 30-Day Forecast Requests
- Model MAPE
- 2026 YTD Growth
- Forecast Demand vs Planning Capacity
- Actual vs Forecast Requests
- Navigation to detailed capacity-risk analysis

### Capacity Risk Details

- Forecast date
- Forecast requests
- Planning capacity
- Risk level
- Capacity gap
- Recommended operational action
- Weekday and risk-level filters

## Forecasting Method

The project compares two forecasting approaches:

1. **7-Day Seasonal Baseline**
   - Uses recent weekly demand as a simple benchmark
   - Test MAPE: **8.23%**

2. **Linear Regression Forecast**
   - Uses historical demand patterns and engineered time features
   - Test MAPE: **4.93%**
   - RMSE: **676.70**
   - MAE: **514.30**
   - R²: **0.5787**

The linear regression model was selected because it produced the lowest holdout-period error.

## Capacity-Risk Logic

Forecast dates are compared with the planning-capacity threshold of **11,609 requests per day**.

| Risk Level | Forecast Days | Average Utilization | Total Capacity Gap |
|---|---:|---:|---:|
| High | 5 | 100.97% | 566 |
| Medium | 13 | 97.23% | 0 |
| Low | 12 | 90.16% | 0 |

Recommended actions include:

- **High:** Add capacity and prioritize high-impact requests
- **Medium:** Monitor closely and prepare backup capacity
- **Low:** Continue the normal operating plan

## Data Model

![Demand Forecasting Data Model](05%20Screenshots/Demand_Forecasting_Data_Model.png)

The Power BI report uses analytical tables for:

- Historical daily totals and operational segments
- Date attributes
- Actual and forecast series
- Forecast test results
- Model metrics
- Future 30-day forecasts
- Capacity-risk alerts and summaries
- Agency, borough, weekday, monthly, yearly, and YTD workload
- Project KPI values

## Tools and Technologies

- Python
- Pandas
- NumPy
- Scikit-learn
- Matplotlib
- SQL / SQLite
- Google Colab
- Power BI Desktop
- Power Query
- DAX
- Git and GitHub

## Repository Structure

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

## Project Files

- [Open the Google Colab notebook](02%20Notebooks/Project_2_Demand_Workload_Forecasting.ipynb)
- [View the SQL analysis](03%20SQL/Project2_Demand_Forecasting_Analysis.sql)
- [Download the Power BI dashboard](04%20Power%20BI/Project_2_Demand_Workload_Forecasting.pbix)
- [View the PDF report](06%20Exports/Project2_Demand_Forecasting_Report.pdf)
- [View the Overview dashboard](05%20Screenshots/Demand_Forecasting_Overview.png)
- [View Capacity Risk Details](05%20Screenshots/Capacity_Risk_Details.png)
- [View the Power BI data model](05%20Screenshots/Demand_Forecasting_Data_Model.png)

## Skills Demonstrated

- Time-series forecasting
- Feature engineering
- Train/test validation
- Forecast-error measurement
- Baseline-model comparison
- Capacity planning
- Risk classification
- Operational analytics
- SQL validation and reporting
- Power BI dashboard development
- Data modeling and DAX
- Executive data storytelling
- GitHub repository documentation

## Author

**Arthur Fay**

Data Analytics and Business Intelligence Portfolio Project
