"""Facts behind docs/LEGACY_LIMITATIONS.md, checked against the committed data.

These tests do not evaluate any model. They pin down what the published legacy numbers
are, so the documented limitations stay tied to evidence.
"""

from __future__ import annotations

import pandas as pd
import pytest

from atlas import summaries
from atlas.loader import LoadedTable

pytestmark = pytest.mark.integration


def frame(tables: dict[str, LoadedTable], name: str) -> pd.DataFrame:
    return tables[name].frame


def test_planning_capacity_is_a_percentile_of_incoming_demand(
    legacy_tables: dict[str, LoadedTable],
) -> None:
    daily = summaries.daily_counts(frame(legacy_tables, "daily_total"))
    window = daily.tail(180)
    threshold = round(window.quantile(0.75))
    assert threshold == 11_609
    assert (window.index.min().date().isoformat(), window.index.max().date().isoformat()) == (
        "2026-01-02",
        "2026-06-30",
    )
    # By construction a quarter of the calibration window lies above the "capacity".
    assert int((window > threshold).sum()) == 45


def test_flagged_shortfalls_are_smaller_than_the_models_one_step_error(
    legacy_tables: dict[str, LoadedTable],
) -> None:
    alerts = frame(legacy_tables, "capacity_risk_alerts")
    flagged = alerts[alerts["capacity_shortfall_flag"] == 1]
    gaps = dict(
        zip(
            flagged["request_date"].dt.strftime("%Y-%m-%d"),
            flagged["capacity_gap_requests"].astype(int),
            strict=True,
        )
    )
    assert gaps == {
        "2026-07-13": 12,
        "2026-07-20": 84,
        "2026-07-21": 58,
        "2026-07-27": 202,
        "2026-07-28": 210,
    }
    assert flagged["days_ahead"].astype(int).tolist() == [13, 20, 21, 27, 28]
    mae = float(
        frame(legacy_tables, "model_metrics")
        .set_index("model_name")
        .at["Linear Regression Forecast", "mae"]
    )
    assert max(gaps.values()) < mae  # every excess is below the h=1 holdout MAE (514.3)


def test_holdout_baseline_used_the_observed_value_seven_days_earlier(
    legacy_tables: dict[str, LoadedTable],
) -> None:
    daily = summaries.daily_counts(frame(legacy_tables, "daily_total"))
    tests = frame(legacy_tables, "forecast_test_results")
    lagged = daily.shift(7).reindex(pd.DatetimeIndex(tests["request_date"]))
    assert (tests["baseline_forecast"].astype(int).to_numpy() == lagged.to_numpy()).all()
    assert tests["request_date"].min().date().isoformat() == "2026-04-02"


def test_thirty_day_forecast_total_is_below_thirty_days_of_threshold(
    legacy_tables: dict[str, LoadedTable],
) -> None:
    forecast = frame(legacy_tables, "future_30_day_forecast")["forecast_requests"].astype(int)
    assert int(forecast.sum()) == 330_941
    assert 30 * 11_609 == 348_270
    assert int(forecast.sum()) < 30 * 11_609  # aggregate "utilisation" is about 95 %
