"""Summary functions on small synthetic series with hand-checked answers."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from atlas import summaries


def daily(values: dict[str, int]) -> pd.DataFrame:
    frame = pd.DataFrame(
        {"request_date": pd.to_datetime(list(values)), "request_count": list(values.values())}
    )
    frame["request_count"] = frame["request_count"].astype("Int64")
    return frame


def test_weekday_numbering_follows_sqlite_sunday_zero() -> None:
    # 2024-01-07 is a Sunday, 2024-01-08 a Monday.
    result = summaries.weekday_workload(daily({"2024-01-07": 10, "2024-01-08": 30}))
    by_number = result.set_index("weekday_number")
    assert by_number.loc[0, "weekday_name"] == "Sunday"
    assert by_number.loc[1, "weekday_name"] == "Monday"
    assert by_number.loc[1, "average_workload_rank"] == 1
    assert by_number["workload_share_pct"].sum() == pytest.approx(100.0)


def test_yearly_growth_uses_previous_calendar_year() -> None:
    frame = daily({"2023-06-01": 100, "2024-06-01": 150, "2026-06-01": 90})
    result = summaries.yearly_workload(frame).set_index("request_year")
    assert result.loc[2024, "previous_year_requests"] == 100
    assert result.loc[2024, "year_over_year_growth_pct"] == pytest.approx(50.0)
    # 2025 is absent, so 2026 has no previous year (not 2024 by row position).
    assert math.isnan(result.loc[2026, "previous_year_requests"])
    assert math.isnan(result.loc[2026, "year_over_year_growth_pct"])


def test_monthly_previous_periods_are_calendar_based() -> None:
    frame = daily({"2024-01-15": 10, "2024-02-15": 20, "2025-02-15": 30})
    result = summaries.monthly_workload(frame).set_index("month_start")
    feb_2025 = result.loc[pd.Timestamp("2025-02-01")]
    assert feb_2025["previous_year_month_requests"] == 20
    assert feb_2025["year_over_year_growth_pct"] == pytest.approx(50.0)
    assert math.isnan(feb_2025["previous_month_requests"])  # January 2025 is absent
    assert result.loc[pd.Timestamp("2024-02-01"), "month_over_month_growth_pct"] == pytest.approx(
        100.0
    )


def test_ytd_window_is_inclusive() -> None:
    frame = daily({"2025-01-01": 5, "2025-06-30": 5, "2025-07-01": 99, "2026-01-01": 20})
    result = summaries.ytd_workload(frame, years=(2025, 2026), through=(6, 30))
    assert result.set_index("request_year")["total_requests"].to_dict() == {2025: 10, 2026: 20}


def test_segment_statistics_use_active_days_only() -> None:
    segments = pd.DataFrame(
        {
            "request_date": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-03"]),
            "agency": ["A", "B", "A"],
            "borough": ["BRONX", "BRONX", "QUEENS"],
            "request_count": pd.array([4, 7, 2], dtype="Int64"),
        }
    )
    agency = summaries.segment_workload(segments, by="agency").set_index("agency")
    assert agency.loc["A", "active_days"] == 2
    assert agency.loc["A", "average_daily_requests"] == pytest.approx(3.0)  # not 6/3 days
    assert agency.loc["A", "workload_rank"] == 2
    borough = summaries.segment_workload(segments, by="borough").set_index("borough")
    assert borough.loc["BRONX", "total_requests"] == 11
    assert "days_recorded" in borough.columns


def test_growth_is_undefined_for_zero_previous() -> None:
    result = summaries.growth_pct(pd.Series([5.0, 5.0]), pd.Series([0.0, 4.0]))
    assert math.isnan(result.iloc[0])
    assert result.iloc[1] == pytest.approx(25.0)


def test_demand_percentile_threshold_and_arguments() -> None:
    frame = daily({f"2024-01-{d:02d}": d for d in range(1, 11)})
    assert summaries.demand_percentile_threshold(frame, window_days=4, quantile=0.5) == 8.5
    with pytest.raises(ValueError, match="window_days"):
        summaries.demand_percentile_threshold(frame, window_days=0)
    with pytest.raises(ValueError, match="quantile"):
        summaries.demand_percentile_threshold(frame, quantile=1.5)


def test_historical_totals_report_extremes_with_dates() -> None:
    totals = summaries.historical_totals(daily({"2024-01-01": 7, "2024-01-02": 3, "2024-01-03": 9}))
    assert totals == {
        "days": 3,
        "first_date": "2024-01-01",
        "last_date": "2024-01-03",
        "total_requests": 19,
        "average_daily_requests": pytest.approx(19 / 3),
        "lowest_daily_requests": 3,
        "lowest_date": "2024-01-02",
        "highest_daily_requests": 9,
        "highest_date": "2024-01-03",
    }
