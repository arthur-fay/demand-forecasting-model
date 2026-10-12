"""Historical summaries recomputed from the two base tables.

Each function reproduces one aggregate table of the legacy notebook (cells 8-13)
from ``daily_total`` or ``daily_segments`` alone, keeping the legacy conventions so
the results can be compared with the committed CSVs:

* weekday numbers follow SQLite ``strftime('%w')`` (Sunday = 0);
* agency and borough statistics are computed over *active* days only, because
  zero-request segments are absent from ``daily_segments``;
* averages and growth rates are returned unrounded; comparison code decides
  whether a committed rounded value is a valid rounding.

Previous-period values are looked up by calendar period (year - 1, month - 1,
month - 12) rather than by row position, which is equivalent for the contiguous
legacy data and stays correct if a period is ever missing.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, Literal

import numpy as np
import pandas as pd

_SQLITE_WEEKDAY_NAMES = {
    0: "Sunday",
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
}


def daily_counts(daily_total: pd.DataFrame) -> pd.Series:
    """City-wide requests per day as an int64 series indexed by date."""
    series = daily_total.set_index("request_date")["request_count"].astype("int64").sort_index()
    series.index = pd.DatetimeIndex(series.index, name="request_date")
    return series


def _period_stats(daily: pd.Series, keys: pd.Index | pd.Series) -> pd.DataFrame:
    grouped = daily.groupby(keys)
    return pd.DataFrame(
        {
            "total_requests": grouped.sum().astype("int64"),
            "average_daily_requests": grouped.mean().astype("float64"),
            "highest_daily_requests": grouped.max().astype("int64"),
            "lowest_daily_requests": grouped.min().astype("int64"),
            "days_recorded": grouped.count().astype("int64"),
        }
    )


def growth_pct(current: pd.Series, previous: pd.Series) -> pd.Series:
    """Percentage change from ``previous`` to ``current`` (NaN where undefined)."""
    previous = previous.astype("float64")
    return (current.astype("float64") - previous) * 100.0 / previous.where(previous != 0)


def _dates(series: pd.Series) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(series.index)


def historical_totals(daily_total: pd.DataFrame) -> dict[str, Any]:
    daily = daily_counts(daily_total)
    dates = _dates(daily)
    return {
        "days": len(daily),
        "first_date": dates.min().date().isoformat(),
        "last_date": dates.max().date().isoformat(),
        "total_requests": int(daily.sum()),
        "average_daily_requests": float(daily.mean()),
        "lowest_daily_requests": int(daily.min()),
        "lowest_date": dates[int(np.argmin(daily.to_numpy()))].date().isoformat(),
        "highest_daily_requests": int(daily.max()),
        "highest_date": dates[int(np.argmax(daily.to_numpy()))].date().isoformat(),
    }


def yearly_workload(daily_total: pd.DataFrame) -> pd.DataFrame:
    daily = daily_counts(daily_total)
    stats = _period_stats(daily, _dates(daily).year.rename("request_year"))
    previous = stats["total_requests"].reindex(stats.index - 1)
    stats["previous_year_requests"] = previous.to_numpy(dtype="float64")
    stats["year_over_year_growth_pct"] = growth_pct(
        stats["total_requests"], stats["previous_year_requests"]
    )
    return stats.reset_index()


def ytd_workload(
    daily_total: pd.DataFrame,
    years: Sequence[int] = (2025, 2026),
    through: tuple[int, int] = (6, 30),
) -> pd.DataFrame:
    """Totals from January 1 through ``through`` (month, day) for each of ``years``."""
    daily = daily_counts(daily_total)
    month, day = through
    selected = []
    for year in years:
        start, end = pd.Timestamp(year, 1, 1), pd.Timestamp(year, month, day)
        selected.append(daily[(daily.index >= start) & (daily.index <= end)])
    window = pd.concat(selected)
    stats = _period_stats(window, _dates(window).year.rename("request_year"))
    previous = stats["total_requests"].reindex(stats.index - 1)
    stats["previous_ytd_requests"] = previous.to_numpy(dtype="float64")
    stats["ytd_growth_pct"] = growth_pct(stats["total_requests"], stats["previous_ytd_requests"])
    return stats.reset_index()


def monthly_workload(daily_total: pd.DataFrame) -> pd.DataFrame:
    daily = daily_counts(daily_total)
    periods = _dates(daily).to_period("M").rename("period")
    stats = _period_stats(daily, periods)
    previous_month = stats["total_requests"].reindex(stats.index - 1)
    previous_year = stats["total_requests"].reindex(stats.index - 12)
    stats["previous_month_requests"] = previous_month.to_numpy(dtype="float64")
    stats["month_over_month_growth_pct"] = growth_pct(
        stats["total_requests"], stats["previous_month_requests"]
    )
    stats["previous_year_month_requests"] = previous_year.to_numpy(dtype="float64")
    stats["year_over_year_growth_pct"] = growth_pct(
        stats["total_requests"], stats["previous_year_month_requests"]
    )
    period_index = pd.PeriodIndex(stats.index)
    stats.insert(0, "month_start", period_index.to_timestamp().astype("datetime64[ns]"))
    stats.insert(1, "request_year", period_index.year.astype("int64"))
    stats.insert(2, "request_month", period_index.month.astype("int64"))
    return stats.reset_index(drop=True)


def sqlite_weekday_number(index: pd.DatetimeIndex) -> pd.Index:
    """SQLite ``strftime('%w')`` numbering: Sunday = 0 ... Saturday = 6."""
    return pd.Index((index.dayofweek + 1) % 7, name="weekday_number")


def weekday_workload(daily_total: pd.DataFrame) -> pd.DataFrame:
    daily = daily_counts(daily_total)
    stats = _period_stats(daily, sqlite_weekday_number(_dates(daily)))
    stats.insert(0, "weekday_name", [_SQLITE_WEEKDAY_NAMES[int(n)] for n in stats.index])
    stats["workload_share_pct"] = stats["total_requests"] * 100.0 / stats["total_requests"].sum()
    # The legacy SQL ranks on the average already rounded to two decimals.
    rounded_average = stats["average_daily_requests"].round(2)
    stats["average_workload_rank"] = rounded_average.rank(method="dense", ascending=False).astype(
        "int64"
    )
    return stats.reset_index()


def segment_workload(
    daily_segments: pd.DataFrame, by: Literal["borough", "agency"]
) -> pd.DataFrame:
    """Per-borough or per-agency statistics over the days on which the segment appears."""
    per_day = daily_segments.groupby(["request_date", by])["request_count"].sum().astype("int64")
    grouped = per_day.groupby(level=by)
    days_column = "days_recorded" if by == "borough" else "active_days"
    stats = pd.DataFrame(
        {
            "total_requests": grouped.sum().astype("int64"),
            "average_daily_requests": grouped.mean().astype("float64"),
            "highest_daily_requests": grouped.max().astype("int64"),
            "lowest_daily_requests": grouped.min().astype("int64"),
            days_column: grouped.count().astype("int64"),
        }
    )
    stats["workload_share_pct"] = stats["total_requests"] * 100.0 / stats["total_requests"].sum()
    stats["workload_rank"] = (
        stats["total_requests"].rank(method="dense", ascending=False).astype("int64")
    )
    return stats.reset_index().sort_values(["workload_rank", by], kind="mergesort")


def demand_percentile_threshold(
    daily_total: pd.DataFrame, window_days: int = 180, quantile: float = 0.75
) -> float:
    """Quantile of the most recent ``window_days`` daily totals (linear interpolation).

    This reproduces the legacy "planning capacity". It is a statistic of *incoming
    demand*; it says nothing about the capacity of any agency to resolve requests.
    """
    if window_days < 1 or not 0.0 <= quantile <= 1.0:
        raise ValueError("window_days must be >= 1 and quantile within [0, 1]")
    recent = daily_counts(daily_total).tail(window_days)
    return float(recent.quantile(quantile))


def last_observed_date(daily_total: pd.DataFrame) -> date:
    return _dates(daily_counts(daily_total)).max().date()
