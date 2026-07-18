-- Project 2: Demand & Workload Forecasting Model with Capacity-Risk Alerts
-- SQL dialect: SQLite
-- Purpose: Recreate core analytical checks, KPI summaries, forecast validation,
--          workload segmentation, and capacity-risk reporting used in Power BI.

-- ============================================================
-- 1. TABLE DEFINITIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS daily_total (
    request_date TEXT PRIMARY KEY,
    request_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_segments (
    request_date TEXT NOT NULL,
    agency TEXT NOT NULL,
    borough TEXT NOT NULL,
    request_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS date_dimension (
    date TEXT PRIMARY KEY,
    year INTEGER,
    quarter INTEGER,
    month_number INTEGER,
    month_name TEXT,
    month_short TEXT,
    year_month TEXT,
    year_month_sort INTEGER,
    day_name TEXT,
    weekday_number INTEGER,
    day_of_month INTEGER,
    week_of_year INTEGER,
    week_start TEXT,
    is_weekend INTEGER
);

CREATE TABLE IF NOT EXISTS actual_forecast_series (
    date TEXT NOT NULL,
    actual_requests REAL,
    forecast_requests REAL,
    record_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS forecast_test_results (
    request_date TEXT PRIMARY KEY,
    actual_requests REAL,
    baseline_forecast REAL,
    model_forecast REAL,
    model_error REAL,
    absolute_error REAL,
    absolute_percentage_error REAL
);

CREATE TABLE IF NOT EXISTS model_metrics (
    model_name TEXT PRIMARY KEY,
    mae REAL,
    rmse REAL,
    mape_pct REAL,
    r_squared REAL,
    mape_improvement_pct REAL
);

CREATE TABLE IF NOT EXISTS future_30_day_forecast (
    request_date TEXT PRIMARY KEY,
    weekday_name TEXT,
    forecast_requests REAL,
    forecast_week INTEGER,
    forecast_month TEXT
);

CREATE TABLE IF NOT EXISTS capacity_risk_alerts (
    request_date TEXT PRIMARY KEY,
    weekday_name TEXT,
    forecast_requests REAL,
    forecast_week INTEGER,
    forecast_month TEXT,
    days_ahead INTEGER,
    planning_capacity REAL,
    capacity_utilization_pct REAL,
    capacity_gap_requests REAL,
    available_capacity REAL,
    risk_level TEXT,
    capacity_shortfall_flag INTEGER,
    recommended_action TEXT
);

CREATE TABLE IF NOT EXISTS capacity_risk_summary (
    risk_level TEXT PRIMARY KEY,
    forecast_days INTEGER,
    total_forecast_requests REAL,
    average_utilization_pct REAL,
    total_capacity_gap REAL
);

CREATE TABLE IF NOT EXISTS agency_workload (
    agency TEXT PRIMARY KEY,
    total_requests INTEGER,
    average_daily_requests REAL,
    highest_daily_requests INTEGER,
    lowest_daily_requests INTEGER,
    active_days INTEGER,
    workload_share_pct REAL,
    workload_rank INTEGER
);

CREATE TABLE IF NOT EXISTS borough_workload (
    borough TEXT PRIMARY KEY,
    total_requests INTEGER,
    average_daily_requests REAL,
    highest_daily_requests INTEGER,
    lowest_daily_requests INTEGER,
    days_recorded INTEGER,
    workload_share_pct REAL,
    workload_rank INTEGER
);

CREATE TABLE IF NOT EXISTS weekday_workload (
    weekday_number INTEGER PRIMARY KEY,
    weekday_name TEXT,
    total_requests INTEGER,
    average_daily_requests REAL,
    highest_daily_requests INTEGER,
    lowest_daily_requests INTEGER,
    days_recorded INTEGER,
    workload_share_pct REAL,
    average_workload_rank INTEGER
);

CREATE TABLE IF NOT EXISTS monthly_workload (
    month_start TEXT PRIMARY KEY,
    request_year INTEGER,
    request_month INTEGER,
    total_requests INTEGER,
    average_daily_requests REAL,
    highest_daily_requests INTEGER,
    lowest_daily_requests INTEGER,
    days_recorded INTEGER,
    previous_month_requests INTEGER,
    month_over_month_growth_pct REAL,
    previous_year_month_requests INTEGER,
    year_over_year_growth_pct REAL
);

CREATE TABLE IF NOT EXISTS yearly_workload (
    request_year INTEGER PRIMARY KEY,
    total_requests INTEGER,
    average_daily_requests REAL,
    highest_daily_requests INTEGER,
    lowest_daily_requests INTEGER,
    days_recorded INTEGER,
    previous_year_requests INTEGER,
    year_over_year_growth_pct REAL
);

CREATE TABLE IF NOT EXISTS ytd_workload (
    request_year INTEGER PRIMARY KEY,
    total_requests INTEGER,
    average_daily_requests REAL,
    highest_daily_requests INTEGER,
    lowest_daily_requests INTEGER,
    days_recorded INTEGER,
    previous_ytd_requests INTEGER,
    ytd_growth_pct REAL
);

CREATE TABLE IF NOT EXISTS project_kpis (
    historical_total_requests INTEGER,
    historical_average_daily_requests REAL,
    ytd_2026_growth_pct REAL,
    model_mape_pct REAL,
    baseline_mape_pct REAL,
    mape_improvement_pct REAL,
    forecast_30_day_requests INTEGER,
    forecast_average_daily_requests REAL,
    planning_capacity_threshold REAL,
    capacity_shortfall_days INTEGER,
    total_forecast_capacity_gap REAL,
    maximum_forecast_utilization_pct REAL,
    first_shortfall_date TEXT,
    advance_warning_days INTEGER
);

-- ============================================================
-- 2. DATA QUALITY CHECKS
-- ============================================================

-- Confirm one row per day in the daily total table.
SELECT
    COUNT(*) AS total_rows,
    COUNT(DISTINCT request_date) AS distinct_dates,
    COUNT(*) - COUNT(DISTINCT request_date) AS duplicate_dates
FROM daily_total;

-- Confirm no missing or negative request counts.
SELECT
    SUM(CASE WHEN request_date IS NULL THEN 1 ELSE 0 END) AS missing_dates,
    SUM(CASE WHEN request_count IS NULL THEN 1 ELSE 0 END) AS missing_request_counts,
    SUM(CASE WHEN request_count < 0 THEN 1 ELSE 0 END) AS negative_request_counts
FROM daily_total;

-- Confirm daily segmented totals reconcile to daily totals.
SELECT
    d.request_date,
    d.request_count AS daily_total_requests,
    SUM(s.request_count) AS segmented_requests,
    d.request_count - SUM(s.request_count) AS variance
FROM daily_total AS d
JOIN daily_segments AS s
    ON d.request_date = s.request_date
GROUP BY d.request_date, d.request_count
HAVING ABS(d.request_count - SUM(s.request_count)) > 0
ORDER BY d.request_date;

-- ============================================================
-- 3. EXECUTIVE KPI QUERIES
-- ============================================================

-- Historical request volume and average daily workload.
SELECT
    SUM(request_count) AS historical_total_requests,
    ROUND(AVG(request_count), 2) AS historical_average_daily_requests,
    MAX(request_count) AS highest_daily_requests,
    MIN(request_count) AS lowest_daily_requests
FROM daily_total;

-- Project KPI snapshot used in the Power BI cards.
SELECT
    historical_total_requests,
    forecast_30_day_requests,
    ROUND(model_mape_pct, 2) AS model_mape_pct,
    ROUND(ytd_2026_growth_pct, 2) AS ytd_2026_growth_pct,
    capacity_shortfall_days,
    ROUND(total_forecast_capacity_gap, 0) AS total_forecast_capacity_gap,
    first_shortfall_date,
    advance_warning_days
FROM project_kpis;

-- ============================================================
-- 4. FORECAST ACCURACY
-- ============================================================

-- Test-period forecast accuracy.
SELECT
    COUNT(*) AS test_days,
    ROUND(AVG(absolute_error), 2) AS mean_absolute_error,
    ROUND(
        SQRT(AVG(model_error * model_error)),
        2
    ) AS root_mean_squared_error,
    ROUND(AVG(absolute_percentage_error), 2) AS model_mape_pct
FROM forecast_test_results;

-- Compare baseline and selected model.
SELECT
    model_name,
    ROUND(mae, 2) AS mae,
    ROUND(rmse, 2) AS rmse,
    ROUND(mape_pct, 2) AS mape_pct,
    ROUND(r_squared, 4) AS r_squared,
    ROUND(mape_improvement_pct, 2) AS mape_improvement_pct
FROM model_metrics
ORDER BY mape_pct;

-- Largest test-period forecast errors.
SELECT
    request_date,
    actual_requests,
    model_forecast,
    ROUND(model_error, 2) AS model_error,
    ROUND(absolute_error, 2) AS absolute_error,
    ROUND(absolute_percentage_error, 2) AS absolute_percentage_error
FROM forecast_test_results
ORDER BY absolute_error DESC
LIMIT 10;

-- ============================================================
-- 5. FUTURE 30-DAY FORECAST
-- ============================================================

-- Total and average forecast requests.
SELECT
    ROUND(SUM(forecast_requests), 0) AS forecast_30_day_requests,
    ROUND(AVG(forecast_requests), 2) AS average_daily_forecast,
    ROUND(MAX(forecast_requests), 0) AS peak_forecast_requests,
    ROUND(MIN(forecast_requests), 0) AS minimum_forecast_requests
FROM future_30_day_forecast;

-- Forecast workload by weekday.
SELECT
    weekday_name,
    COUNT(*) AS forecast_days,
    ROUND(SUM(forecast_requests), 0) AS total_forecast_requests,
    ROUND(AVG(forecast_requests), 2) AS average_forecast_requests
FROM future_30_day_forecast
GROUP BY weekday_name
ORDER BY average_forecast_requests DESC;

-- ============================================================
-- 6. CAPACITY-RISK ANALYSIS
-- ============================================================

-- Capacity-risk summary by level.
SELECT
    risk_level,
    forecast_days,
    ROUND(total_forecast_requests, 0) AS total_forecast_requests,
    ROUND(average_utilization_pct, 2) AS average_utilization_pct,
    ROUND(total_capacity_gap, 0) AS total_capacity_gap
FROM capacity_risk_summary
ORDER BY
    CASE risk_level
        WHEN 'High' THEN 1
        WHEN 'Medium' THEN 2
        WHEN 'Low' THEN 3
        ELSE 4
    END;

-- High-risk forecast days requiring action.
SELECT
    request_date,
    weekday_name,
    ROUND(forecast_requests, 0) AS forecast_requests,
    ROUND(planning_capacity, 0) AS planning_capacity,
    ROUND(capacity_utilization_pct, 2) AS capacity_utilization_pct,
    ROUND(capacity_gap_requests, 0) AS capacity_gap_requests,
    days_ahead,
    recommended_action
FROM capacity_risk_alerts
WHERE risk_level = 'High'
ORDER BY request_date;

-- All capacity-shortfall days.
SELECT
    request_date,
    weekday_name,
    ROUND(forecast_requests, 0) AS forecast_requests,
    ROUND(planning_capacity, 0) AS planning_capacity,
    ROUND(capacity_gap_requests, 0) AS capacity_gap_requests,
    ROUND(capacity_utilization_pct, 2) AS capacity_utilization_pct
FROM capacity_risk_alerts
WHERE capacity_shortfall_flag = 1
ORDER BY capacity_gap_requests DESC;

-- Total projected gap and earliest warning.
SELECT
    COUNT(*) AS capacity_shortfall_days,
    ROUND(SUM(capacity_gap_requests), 0) AS total_forecast_capacity_gap,
    ROUND(MAX(capacity_gap_requests), 0) AS largest_single_day_gap,
    MIN(request_date) AS first_shortfall_date,
    MIN(days_ahead) AS minimum_days_ahead
FROM capacity_risk_alerts
WHERE capacity_shortfall_flag = 1;

-- ============================================================
-- 7. OPERATIONAL WORKLOAD ANALYSIS
-- ============================================================

-- Top agencies by workload.
SELECT
    agency,
    total_requests,
    ROUND(average_daily_requests, 2) AS average_daily_requests,
    ROUND(workload_share_pct, 2) AS workload_share_pct,
    workload_rank
FROM agency_workload
ORDER BY total_requests DESC
LIMIT 10;

-- Borough workload ranking.
SELECT
    borough,
    total_requests,
    ROUND(average_daily_requests, 2) AS average_daily_requests,
    ROUND(workload_share_pct, 2) AS workload_share_pct,
    workload_rank
FROM borough_workload
ORDER BY total_requests DESC;

-- Weekday workload pattern.
SELECT
    weekday_number,
    weekday_name,
    total_requests,
    ROUND(average_daily_requests, 2) AS average_daily_requests,
    ROUND(workload_share_pct, 2) AS workload_share_pct,
    average_workload_rank
FROM weekday_workload
ORDER BY weekday_number;

-- ============================================================
-- 8. TREND AND GROWTH ANALYSIS
-- ============================================================

-- Monthly workload trend.
SELECT
    month_start,
    total_requests,
    ROUND(average_daily_requests, 2) AS average_daily_requests,
    ROUND(month_over_month_growth_pct, 2) AS month_over_month_growth_pct,
    ROUND(year_over_year_growth_pct, 2) AS year_over_year_growth_pct
FROM monthly_workload
ORDER BY month_start;

-- Yearly workload growth.
SELECT
    request_year,
    total_requests,
    ROUND(average_daily_requests, 2) AS average_daily_requests,
    ROUND(year_over_year_growth_pct, 2) AS year_over_year_growth_pct
FROM yearly_workload
ORDER BY request_year;

-- Year-to-date growth comparison.
SELECT
    request_year,
    total_requests,
    ROUND(average_daily_requests, 2) AS average_daily_requests,
    previous_ytd_requests,
    ROUND(ytd_growth_pct, 2) AS ytd_growth_pct
FROM ytd_workload
ORDER BY request_year;

-- ============================================================
-- 9. POWER BI SUPPORT VIEWS
-- ============================================================

CREATE VIEW IF NOT EXISTS vw_capacity_risk_details AS
SELECT
    request_date,
    weekday_name,
    forecast_requests,
    planning_capacity,
    risk_level,
    capacity_gap_requests,
    recommended_action
FROM capacity_risk_alerts;

CREATE VIEW IF NOT EXISTS vw_forecast_vs_capacity AS
SELECT
    request_date,
    forecast_requests,
    planning_capacity,
    capacity_utilization_pct,
    capacity_gap_requests,
    risk_level
FROM capacity_risk_alerts;

CREATE VIEW IF NOT EXISTS vw_model_performance AS
SELECT
    request_date,
    actual_requests,
    baseline_forecast,
    model_forecast,
    absolute_error,
    absolute_percentage_error
FROM forecast_test_results;
