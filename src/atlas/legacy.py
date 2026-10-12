"""Re-derive the legacy notebook's output tables from their inputs.

No model is fitted or evaluated here. Given the committed base data and the committed
model predictions, these functions recompute every column that the legacy notebook
derived arithmetically (notebook cell index 15-18, i.e. "STEP 16-19"), so that the
exported tables can be cross-checked against the base data:

* ``forecast_test_results``: actuals and the seven-day baseline from ``daily_total``;
  errors from the exported predictions;
* ``future_30_day_forecast``: calendar labels of each forecast date;
* ``capacity_risk_alerts`` and ``capacity_risk_summary``: the legacy threshold rule;
* ``date_dimension`` and ``actual_forecast_series``: the Power BI support tables.

The legacy rules are reproduced verbatim, including the "planning capacity" that is
really a percentile of incoming demand (see docs/LEGACY_LIMITATIONS.md).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from atlas import summaries

RISK_ACTIONS: dict[str, str] = {
    "Critical": "Activate surge capacity and escalate immediately",
    "High": "Add capacity and prioritize high-impact requests",
    "Medium": "Monitor closely and prepare backup capacity",
    "Low": "Normal operating plan",
}


def risk_level(utilization_pct: float) -> str:
    """Legacy tiers on the utilisation already rounded to two decimals (cell index 17)."""
    if utilization_pct >= 110:
        return "Critical"
    if utilization_pct > 100:
        return "High"
    if utilization_pct >= 95:
        return "Medium"
    return "Low"


def holdout_results(daily_total: pd.DataFrame, test_results: pd.DataFrame) -> pd.DataFrame:
    """Columns of ``forecast_test_results`` that follow from ``daily_total`` and the predictions."""
    daily = summaries.daily_counts(daily_total)
    dates = pd.DatetimeIndex(test_results["request_date"])
    actual = daily.reindex(dates).to_numpy(dtype="float64")
    baseline = daily.shift(7).reindex(dates).to_numpy(dtype="float64")
    predicted = test_results["model_forecast"].to_numpy(dtype="float64")
    error = actual - predicted
    return pd.DataFrame(
        {
            "request_date": dates,
            "actual_requests": actual,
            "baseline_forecast": baseline,
            "model_error": error,
            "absolute_error": abs(error),
            "absolute_percentage_error": abs(error) / actual * 100.0,
        }
    )


def forecast_calendar(forecast: pd.DataFrame) -> pd.DataFrame:
    """Calendar labels the notebook attached to each forecast date (cell index 16)."""
    dates = pd.DatetimeIndex(forecast["request_date"])
    return pd.DataFrame(
        {
            "request_date": dates,
            "weekday_name": dates.day_name(),
            "forecast_requests": forecast["forecast_requests"].to_numpy(dtype="int64"),
            "forecast_week": dates.to_period("W").astype(str),
            "forecast_month": dates.to_period("M").astype(str),
        }
    )


def capacity_alerts(forecast: pd.DataFrame, threshold: int, last_observed: date) -> pd.DataFrame:
    """The legacy alert table for a given threshold and data cutoff (cell index 17)."""
    alerts = forecast_calendar(forecast)
    dates = pd.DatetimeIndex(alerts["request_date"])
    values = alerts["forecast_requests"]
    utilization = (values / threshold * 100.0).round(2)
    gap = (values - threshold).clip(lower=0)
    alerts["days_ahead"] = (dates - pd.Timestamp(last_observed)).days
    alerts["planning_capacity"] = threshold
    alerts["capacity_utilization_pct"] = utilization
    alerts["capacity_gap_requests"] = gap
    alerts["available_capacity"] = (threshold - values).clip(lower=0)
    alerts["risk_level"] = [risk_level(float(u)) for u in utilization]
    alerts["capacity_shortfall_flag"] = (gap > 0).astype("int64")
    alerts["recommended_action"] = alerts["risk_level"].map(RISK_ACTIONS)
    return alerts


def capacity_summary(alerts: pd.DataFrame) -> pd.DataFrame:
    """Alert counts and totals by risk level (cell index 17)."""
    grouped = alerts.groupby("risk_level")
    return pd.DataFrame(
        {
            "forecast_days": grouped["request_date"].count(),
            "total_forecast_requests": grouped["forecast_requests"].sum(),
            "average_utilization_pct": grouped["capacity_utilization_pct"].mean(),
            "total_capacity_gap": grouped["capacity_gap_requests"].sum(),
        }
    ).reset_index()


def date_dimension(start: date, end: date) -> pd.DataFrame:
    """The Power BI calendar (cell index 18): Monday=1..Sunday=7 and ISO week numbers."""
    dates = pd.date_range(start, end, freq="D")
    return pd.DataFrame(
        {
            "date": dates,
            "year": dates.year,
            "quarter": "Q" + dates.quarter.astype(str),
            "month_number": dates.month,
            "month_name": dates.month_name(),
            "month_short": dates.strftime("%b"),
            "year_month": dates.strftime("%Y-%m"),
            "year_month_sort": dates.strftime("%Y%m").astype(int),
            "day_name": dates.day_name(),
            "weekday_number": dates.dayofweek + 1,
            "day_of_month": dates.day,
            "week_of_year": dates.isocalendar().week.astype(int).to_numpy(),
            "week_start": dates - pd.to_timedelta(dates.dayofweek, unit="D"),
            "is_weekend": (dates.dayofweek >= 5).astype(int),
        }
    )


def actual_forecast_series(daily_total: pd.DataFrame, forecast: pd.DataFrame) -> pd.DataFrame:
    """Observed totals followed by the forecast; the two series never overlap (cell index 18)."""
    daily = summaries.daily_counts(daily_total)
    actual = pd.DataFrame(
        {
            "date": pd.DatetimeIndex(daily.index),
            "actual_requests": daily.to_numpy(dtype="float64"),
            "forecast_requests": float("nan"),
            "record_type": "Actual",
        }
    )
    future = pd.DataFrame(
        {
            "date": pd.DatetimeIndex(forecast["request_date"]),
            "actual_requests": float("nan"),
            "forecast_requests": forecast["forecast_requests"].to_numpy(dtype="float64"),
            "record_type": "Forecast",
        }
    )
    return pd.concat([actual, future], ignore_index=True)
