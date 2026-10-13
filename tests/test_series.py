"""DemandSeries: invalid timestamps, missing dates and data-quality exception handling."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from atlas.errors import AtlasError, DataQualityError
from atlas.series import DemandSeries
from tests.synthetic import day_range

DAYS = day_range(date(2024, 2, 26), 7)  # crosses the 2024 leap day


def codes(error: DataQualityError) -> set[str]:
    return {issue.code for issue in error.issues}


def test_valid_series_is_sorted_and_typed() -> None:
    values = [10, 11, 12, 13, 14, 15, 16]
    series = DemandSeries.from_observations(list(reversed(DAYS)), list(reversed(values)))
    data = series.to_series()
    assert series.start == date(2024, 2, 26)
    assert series.end == date(2024, 3, 3)
    assert len(series) == 7
    assert list(data) == values
    assert data.dtype == np.float64
    assert series.value_on(date(2024, 2, 29)) == 13
    assert series.missing_dates == ()


@pytest.mark.parametrize(
    "bad",
    [
        "2024-02-29T10:00",  # time of day
        pd.Timestamp("2024-02-29 00:00:01"),
        datetime(2024, 2, 29, 12, 0),
        pd.Timestamp("2024-02-29", tz="UTC"),  # time zone
        datetime(2024, 2, 29, tzinfo=UTC),
        "2024-02-30",  # not a date
        "2023-02-29",  # not a leap year
        "20240229",  # basic ISO format is not accepted
        "2024-W09-4",
        "",
        None,
        pd.NaT,
        float("nan"),
        20240229,
    ],
)
def test_invalid_timestamps_are_reported(bad: object) -> None:
    dates: list[object] = list(DAYS)
    dates[3] = bad
    with pytest.raises(DataQualityError) as raised:
        DemandSeries.from_observations(dates, [1] * 7)
    assert "invalid_timestamp" in codes(raised.value)


def test_midnight_timestamps_and_iso_strings_are_dates() -> None:
    mixed: list[object] = [
        pd.Timestamp("2024-02-26"),
        "2024-02-27",
        datetime(2024, 2, 28),
        date(2024, 2, 29),
    ]
    series = DemandSeries.from_observations(mixed, [1, 2, 3, 4])
    assert series.end == date(2024, 2, 29)


def test_missing_dates_are_rejected_by_default_and_listed() -> None:
    dates = [d for d in DAYS if d != date(2024, 2, 29)]
    with pytest.raises(DataQualityError) as raised:
        DemandSeries.from_observations(dates, [1] * 6)
    (issue,) = raised.value.issues
    assert issue.code == "missing_dates"
    assert issue.examples == ("2024-02-29",)
    series = DemandSeries.from_observations(dates, [1] * 6, allow_missing=True)
    assert series.missing_dates == (date(2024, 2, 29),)
    assert math.isnan(series.value_on(date(2024, 2, 29)))
    assert len(series) == 7


def test_null_values_are_gaps_only_when_allowed() -> None:
    values: list[object] = [1, 2, None, 4, float("nan"), 6, 7]
    with pytest.raises(DataQualityError) as raised:
        DemandSeries.from_observations(DAYS, values)
    assert codes(raised.value) == {"null_value"}
    series = DemandSeries.from_observations(DAYS, values, allow_missing=True)
    assert series.missing_dates == (date(2024, 2, 28), date(2024, 3, 1))


def test_every_problem_is_reported_at_once() -> None:
    dates: list[object] = [*DAYS[:6], DAYS[0], "2024-03-04 08:00"]
    values: list[object] = [-1, math.inf, 2.5, "7", True, 1, 1, 1]
    with pytest.raises(DataQualityError) as raised:
        DemandSeries.from_observations(dates, values)
    assert codes(raised.value) == {
        "invalid_timestamp",
        "duplicate_date",
        "negative_value",
        "non_finite_value",
        "non_integral_value",
        "non_numeric_value",
    }
    assert isinstance(raised.value, AtlasError)
    assert "6 data-quality problem(s)" in str(raised.value)


def test_fractional_values_allowed_when_not_counts() -> None:
    series = DemandSeries.from_observations(DAYS, [0.5] * 7, integral=False)
    assert series.value_on(DAYS[0]) == 0.5


def test_structural_errors() -> None:
    with pytest.raises(DataQualityError, match="no observations") as raised:
        DemandSeries.from_observations([], [])
    assert codes(raised.value) == {"empty_series"}
    with pytest.raises(DataQualityError, match="7 dates but 6 values"):
        DemandSeries.from_observations(DAYS, [1] * 6)
    with pytest.raises(DataQualityError, match="column 'request_count' is missing"):
        DemandSeries.from_frame(pd.DataFrame({"request_date": DAYS}))


@pytest.mark.parametrize(
    "values",
    [
        pd.Series([1.0, 2.0], index=pd.DatetimeIndex(["2024-01-01", "2024-01-03"])),  # gap
        pd.Series([1.0], index=pd.DatetimeIndex(["2024-01-01 06:00"])),  # time of day
        pd.Series([1, 2], index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"])),  # int dtype
        pd.Series([], dtype="float64", index=pd.DatetimeIndex([])),
        pd.Series([1.0], index=pd.DatetimeIndex(["2024-01-01"], tz="UTC")),
        pd.Series([1.0], index=[0]),
    ],
)
def test_constructor_rechecks_invariants(values: pd.Series) -> None:
    with pytest.raises(DataQualityError):
        DemandSeries("bad", values)


def test_known_through_and_copies_do_not_alias() -> None:
    series = DemandSeries.from_observations(DAYS, [1, 2, 3, 4, 5, 6, 7])
    known = series.known_through(date(2024, 2, 29))
    assert list(known) == [1, 2, 3, 4]
    known.iloc[0] = 999
    copy = series.to_series()
    copy.iloc[1] = 999
    assert series.value_on(DAYS[0]) == 1
    assert series.value_on(DAYS[1]) == 2


@pytest.mark.integration
def test_legacy_daily_total_is_a_complete_series(legacy_tables: dict) -> None:  # type: ignore[type-arg]
    series = DemandSeries.from_frame(legacy_tables["daily_total"].frame, name="daily_total")
    assert (series.start, series.end, len(series)) == (date(2023, 1, 1), date(2026, 6, 30), 1277)
    assert series.missing_dates == ()
    assert series.to_series().sum() == 12_331_111


def test_numpy_datetimes_from_a_frame_are_accepted() -> None:
    frame = pd.DataFrame(
        {"request_date": pd.to_datetime([d.isoformat() for d in DAYS]), "request_count": range(7)}
    )
    series = DemandSeries.from_observations(
        frame["request_date"].to_numpy(), frame["request_count"].tolist()
    )
    assert series.end == date(2024, 3, 3)


def test_constructor_rejects_bad_values_and_owns_its_data() -> None:
    index = pd.date_range("2024-01-01", periods=3, freq="D")
    for bad in ([1.0, -1.0, 2.0], [1.0, math.inf, 2.0]):
        with pytest.raises(DataQualityError, match="finite and non-negative"):
            DemandSeries("bad", pd.Series(bad, index=index))
    source = pd.Series([1.0, float("nan"), 3.0], index=index)
    series = DemandSeries("ok", source)
    source.iloc[0] = 999.0
    assert series.value_on(date(2024, 1, 1)) == 1.0
    assert series.missing_dates == (date(2024, 1, 2),)
