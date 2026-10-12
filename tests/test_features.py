"""Feature generation: correctness, leakage, parity, gaps, history, calendar edges, determinism.

Expected values come from closed forms on synthetic series or from the independent
reference implementation in ``tests/synthetic.py``, never from ``atlas.features`` itself.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from atlas.errors import DataQualityError, FeatureError, InsufficientHistoryError, LeakageError
from atlas.events import EventCalendar, KnownEvent
from atlas.features import (
    AnnualFourier,
    Calendar,
    CalendarField,
    DeclaredEventIndicator,
    Feature,
    FeatureContext,
    FeatureKind,
    FeatureSet,
    HolidayIndicator,
    HolidayRelation,
    Lag,
    RollingMean,
    RollingStd,
    SameWeekdayMean,
    Trend,
    WeekdayIndicator,
    default_feature_set,
    forecast_features,
    recursive_forecast,
    training_features,
)
from atlas.series import DemandSeries
from tests.synthetic import (
    day_range,
    linear_series,
    random_series,
    reference_calendar,
    reference_row,
    values_by_date,
)

START = date(2022, 1, 1)
DAYS = 900  # 2022-01-01 .. 2024-06-18: covers the 2024 leap day
DEFAULT = default_feature_set()
DEMAND_NAMES = (
    "lag_1",
    "lag_7",
    "lag_14",
    "lag_28",
    "lag_364",
    "rolling_mean_7",
    "rolling_mean_28",
    "same_weekday_mean_4w",
    "rolling_std_7",
    "rolling_std_28",
)


@pytest.fixture(scope="module")
def noisy() -> DemandSeries:
    return random_series(START, DAYS, seed=20261012)


@pytest.fixture(scope="module")
def linear() -> DemandSeries:
    return linear_series(START, DAYS)


def perturb_after(series: DemandSeries, origin: date, seed: int) -> DemandSeries:
    """Replace every value after ``origin`` with an unrelated random value."""
    rng = random.Random(seed)
    values = series.to_series()
    dates = [ts.date() for ts in values.index]
    new = [
        v if d <= origin else float(rng.randint(0, 10**6))
        for d, v in zip(dates, values, strict=True)
    ]
    return DemandSeries.from_observations(dates, new, name=series.name)


def frame_digest(frame: pd.DataFrame) -> str:
    payload = frame.to_numpy(dtype="float64").tobytes() + "|".join(frame.columns).encode()
    payload += frame.index.to_numpy(dtype="datetime64[ns]").tobytes()
    return hashlib.sha256(payload).hexdigest()


# --- Correctness of lag and rolling calculations ------------------------------------


def test_closed_forms_on_linear_series(linear: DemandSeries) -> None:
    """y(t) = 1000 + k: lag_k = y - k, mean of w days ending t-1 = y - (w+1)/2, etc."""
    matrix = training_features(linear, origin=date(2024, 6, 1))
    y = linear.to_series().reindex(matrix.frame.index)
    frame = matrix.frame
    for lag in (1, 7, 14, 28, 364):
        np.testing.assert_array_equal(frame[f"lag_{lag}"], y - lag)
    np.testing.assert_allclose(frame["rolling_mean_7"], y - 4.0, rtol=0, atol=1e-9)
    np.testing.assert_allclose(frame["rolling_mean_28"], y - 14.5, rtol=0, atol=1e-9)
    np.testing.assert_allclose(frame["same_weekday_mean_4w"], y - 17.5, rtol=0, atol=1e-9)
    # Sample standard deviation of w consecutive integers: sqrt(w (w + 1) / 12).
    np.testing.assert_allclose(frame["rolling_std_7"], math.sqrt(7 * 8 / 12), atol=1e-9)
    np.testing.assert_allclose(frame["rolling_std_28"], math.sqrt(28 * 29 / 12), atol=1e-9)
    np.testing.assert_array_equal(matrix.target, y)


def test_hand_computed_window() -> None:
    """A tiny series where every number can be checked by hand."""
    values = [10, 20, 40, 80, 160, 320, 640, 1280]
    series = DemandSeries.from_observations(day_range(date(2024, 1, 1), 8), values)
    feature_set = FeatureSet((Lag(1), Lag(3), RollingMean(3), RollingStd(3), RollingMean(2, lag=2)))
    matrix = training_features(series, origin=date(2024, 1, 8), feature_set=feature_set)
    # First target with 3 days of history: 2024-01-04 (value 80).
    assert matrix.frame.index[0] == pd.Timestamp("2024-01-04")
    row = matrix.frame.loc["2024-01-08"]  # target 1280; history ..., 160, 320, 640
    assert row["lag_1"] == 640
    assert row["lag_3"] == 160
    assert row["rolling_mean_3"] == pytest.approx((160 + 320 + 640) / 3)
    # Mean 373.33...; squared deviations sum to 119466.67; divide by 3 - 1; square root.
    assert row["rolling_std_3"] == pytest.approx(
        math.sqrt(((160 - 1120 / 3) ** 2 + (320 - 1120 / 3) ** 2 + (640 - 1120 / 3) ** 2) / 2)
    )
    assert row["rolling_std_3"] == pytest.approx(244.404037064, rel=1e-9)
    assert row["rolling_mean_2_lag_2"] == pytest.approx((160 + 320) / 2)
    assert matrix.target.loc["2024-01-08"] == 1280


def test_matches_independent_reference_on_noisy_series(noisy: DemandSeries) -> None:
    origin = date(2024, 5, 31)
    matrix = training_features(noisy, origin=origin)
    known = {d: v for d, v in values_by_date(noisy).items() if d <= origin}
    rng = random.Random(7)
    index = matrix.frame.index
    sampled = [*rng.sample(list(index), 60), index[0], index[-1]]
    for target in sampled:
        expected = reference_row(known, target.date()) | reference_calendar(target.date())
        actual = matrix.frame.loc[target]
        for name, value in expected.items():
            assert actual[name] == pytest.approx(value, rel=1e-12, abs=1e-9), (target, name)


def test_lags_are_by_calendar_date_not_row_position() -> None:
    """With a missing date, lag_7 must be y(t - 7 days), not the value seven rows back."""
    dates = day_range(date(2024, 1, 1), 30)
    values: list[float | None] = [float(100 + k) for k in range(30)]
    values[10] = None  # 2024-01-11 unobserved
    series = DemandSeries.from_observations(dates, values, allow_missing=True)
    feature_set = FeatureSet((Lag(7),))
    matrix = training_features(
        series, origin=date(2024, 1, 30), feature_set=feature_set, allow_incomplete=True
    )
    lag7 = matrix.frame["lag_7"]
    assert lag7.loc["2024-01-30"] == 100 + 29 - 7
    assert math.isnan(lag7.loc["2024-01-18"])  # reads 2024-01-11
    assert lag7.loc["2024-01-19"] == 100 + 18 - 7
    assert not matrix.complete.loc["2024-01-18"]
    assert math.isnan(matrix.target.loc["2024-01-11"])


# --- Temporal leakage --------------------------------------------------------------


@settings(max_examples=25, deadline=None)
@given(offset=st.integers(min_value=400, max_value=DAYS - 40), seed=st.integers(0, 10**6))
def test_training_rows_ignore_data_after_origin(offset: int, seed: int) -> None:
    series = random_series(START, DAYS, seed=11)
    origin = START + timedelta(days=offset)
    original = training_features(series, origin=origin)
    perturbed = training_features(perturb_after(series, origin, seed), origin=origin)
    pd.testing.assert_frame_equal(original.frame, perturbed.frame)
    pd.testing.assert_series_equal(original.target, perturbed.target)
    assert original.frame.index[-1].date() == origin


@settings(max_examples=25, deadline=None)
@given(offset=st.integers(min_value=400, max_value=DAYS - 40), seed=st.integers(0, 10**6))
def test_forecast_and_recursive_rows_ignore_data_after_origin(offset: int, seed: int) -> None:
    series = random_series(START, DAYS, seed=12)
    origin = START + timedelta(days=offset)
    perturbed = perturb_after(series, origin, seed)
    weekly = FeatureSet((Lag(7), Lag(14), SameWeekdayMean(4), Calendar(CalendarField.ISO_WEEKDAY)))
    direct = [
        forecast_features(s, origin=origin, horizons=(1, 4, 7), feature_set=weekly)
        for s in (series, perturbed)
    ]
    pd.testing.assert_frame_equal(direct[0].frame, direct[1].frame)

    def predictor(row: pd.Series) -> float:
        return 0.5 * row["lag_1"] + 0.5 * row["lag_7"]

    forecasts = [
        recursive_forecast(s, origin=origin, steps=30, predictor=predictor)
        for s in (series, perturbed)
    ]
    pd.testing.assert_series_equal(forecasts[0].predictions, forecasts[1].predictions)
    pd.testing.assert_frame_equal(forecasts[0].features.frame, forecasts[1].features.frame)


def test_perturbing_the_last_known_day_does_change_features(noisy: DemandSeries) -> None:
    """Control for the invariance tests: they would pass trivially if nothing were read."""
    origin = date(2024, 3, 1)
    values = noisy.to_series()
    values.loc[pd.Timestamp(origin)] += 1000
    changed = DemandSeries.from_observations([t.date() for t in values.index], values.tolist())
    before = forecast_features(noisy, origin=origin)
    after = forecast_features(changed, origin=origin)
    assert after.frame["lag_1"].iloc[0] == before.frame["lag_1"].iloc[0] + 1000
    assert after.frame["rolling_mean_7"].iloc[0] == pytest.approx(
        before.frame["rolling_mean_7"].iloc[0] + 1000 / 7
    )


def test_training_row_never_reads_its_own_target(linear: DemandSeries) -> None:
    """Raising the target day's value must leave that row's features unchanged."""
    target = date(2024, 2, 10)
    values = linear.to_series()
    values.loc[pd.Timestamp(target)] = 0.0
    changed = DemandSeries.from_observations([t.date() for t in values.index], values.tolist())
    before = training_features(linear, origin=date(2024, 3, 1)).frame.loc[pd.Timestamp(target)]
    after = training_features(changed, origin=date(2024, 3, 1)).frame.loc[pd.Timestamp(target)]
    pd.testing.assert_series_equal(before, after)


@dataclass(frozen=True)
class _UnderDeclared(Feature):
    """Claims to read offset 1 but actually reads the target day (offset 0)."""

    kind: ClassVar[FeatureKind] = FeatureKind.LAG

    @property
    def name(self) -> str:
        return "cheat"

    @property
    def description(self) -> str:
        return "reads the target itself"

    @property
    def offsets(self) -> tuple[int, ...]:
        return (1,)

    def compute(self, context: FeatureContext) -> np.ndarray:
        return context.read(0)


def test_runtime_guard_catches_a_feature_that_misdeclares_its_offsets(linear: DemandSeries) -> None:
    feature_set = FeatureSet((_UnderDeclared(),))
    with pytest.raises(LeakageError, match="information cutoff"):
        training_features(linear, origin=date(2024, 1, 31), feature_set=feature_set)
    with pytest.raises(LeakageError):
        forecast_features(linear, origin=date(2024, 1, 31), feature_set=feature_set)


def test_horizon_longer_than_smallest_offset_is_refused(linear: DemandSeries) -> None:
    with pytest.raises(LeakageError, match="horizon-2"):
        forecast_features(linear, origin=date(2024, 1, 31), horizons=(1, 2))
    with pytest.raises(LeakageError):
        training_features(linear, origin=date(2024, 1, 31), horizon=2)


def test_direct_feature_set_supports_matching_horizon(linear: DemandSeries) -> None:
    weekly = FeatureSet((Lag(7), SameWeekdayMean(2), RollingMean(7, lag=7)))
    assert weekly.supports_horizon(7)
    assert not weekly.supports_horizon(8)
    matrix = forecast_features(linear, origin=date(2024, 1, 31), horizons=(7,), feature_set=weekly)
    # Target 2024-02-07; all reads are on or before 2024-01-31.
    y_target = linear.value_on(date(2024, 2, 7))
    assert matrix.frame.loc["2024-02-07", "lag_7"] == y_target - 7
    assert matrix.frame.loc["2024-02-07", "rolling_mean_7_lag_7"] == y_target - 10
    assert matrix.cutoffs.iloc[0] == pd.Timestamp("2024-01-31")


def test_calendar_only_feature_set_supports_any_horizon(linear: DemandSeries) -> None:
    calendar = FeatureSet((Calendar(CalendarField.MONTH), Trend()))
    assert calendar.min_offset is None
    assert calendar.max_offset == 0
    matrix = forecast_features(
        linear, origin=date(2024, 1, 31), horizons=(1, 400), feature_set=calendar
    )
    assert list(matrix.frame["month"]) == [2.0, 3.0]


def test_declared_event_visible_only_after_announcement(linear: DemandSeries) -> None:
    event = KnownEvent("street fair", date(2024, 2, 10), date(2024, 2, 11), date(2024, 2, 5))
    events = EventCalendar((event,))
    feature_set = FeatureSet((DeclaredEventIndicator(), Lag(1)))
    before = forecast_features(
        linear,
        origin=date(2024, 2, 4),
        horizons=(6, 7),
        feature_set=FeatureSet((DeclaredEventIndicator(),)),
        events=events,
    )
    assert list(before.frame.index.day) == [10, 11]  # both targets are event days
    assert before.frame["is_declared_event"].sum() == 0
    early = recursive_forecast(
        linear,
        origin=date(2024, 2, 4),
        steps=10,
        predictor=lambda r: r["lag_1"],
        feature_set=feature_set,
        events=events,
    )
    assert early.features.frame["is_declared_event"].sum() == 0  # announced after origin
    late = recursive_forecast(
        linear,
        origin=date(2024, 2, 5),
        steps=10,
        predictor=lambda r: r["lag_1"],
        feature_set=feature_set,
        events=events,
    )
    flagged = late.features.frame.index[late.features.frame["is_declared_event"] == 1]
    assert [d.date() for d in flagged] == [date(2024, 2, 10), date(2024, 2, 11)]
    # Training rows use each row's own origin (t - 1): 2024-02-10 and 02-11 are both known.
    train = training_features(
        linear, origin=date(2024, 2, 20), feature_set=feature_set, events=events
    )
    assert train.frame["is_declared_event"].sum() == 2


# --- Training / forecasting parity ---------------------------------------------------


def test_training_rows_equal_forecast_rows_at_each_origin(noisy: DemandSeries) -> None:
    train = training_features(noisy, origin=date(2024, 6, 1))
    for target in train.frame.index[-60:]:
        row = forecast_features(noisy, origin=(target - pd.Timedelta(days=1)).date())
        pd.testing.assert_frame_equal(row.frame, train.frame.loc[[target]])


def test_direct_horizon_training_parity(noisy: DemandSeries) -> None:
    weekly = FeatureSet((Lag(7), Lag(14), SameWeekdayMean(3), RollingStd(7, lag=7), Trend()))
    train = training_features(noisy, origin=date(2024, 6, 1), horizon=7, feature_set=weekly)
    assert (train.cutoffs == train.frame.index - pd.Timedelta(days=7)).all()
    for target in train.frame.index[-30:]:
        row = forecast_features(
            noisy, origin=(target - pd.Timedelta(days=7)).date(), horizons=(7,), feature_set=weekly
        )
        pd.testing.assert_frame_equal(row.frame, train.frame.loc[[target]])


def test_recursive_rows_equal_training_rows_when_predictions_are_exact(noisy: DemandSeries) -> None:
    """Teacher forcing: feeding back the actual values reproduces the training rows."""
    actual = noisy.to_series()
    origin = date(2024, 3, 15)

    def oracle(row: pd.Series) -> float:
        return float(actual.loc[row.name])

    forecast = recursive_forecast(noisy, origin=origin, steps=45, predictor=oracle)
    train = training_features(noisy, origin=origin + timedelta(days=45))
    pd.testing.assert_frame_equal(
        forecast.features.frame, train.frame.loc[forecast.features.frame.index]
    )
    assert list(forecast.predictions) == list(actual.loc[forecast.predictions.index])


def test_recursive_seasonal_naive_feeds_predictions_back(noisy: DemandSeries) -> None:
    """Predicting lag_7 recursively repeats the last observed week forever."""
    origin = date(2024, 3, 15)
    forecast = recursive_forecast(noisy, origin=origin, steps=21, predictor=lambda r: r["lag_7"])
    last_week = [noisy.value_on(origin - timedelta(days=6 - k)) for k in range(7)]
    assert list(forecast.predictions) == last_week * 3
    # From step 2 on, lag_1 is the previous prediction, not the actual value.
    frame = forecast.features.frame
    assert frame["lag_1"].iloc[1] == forecast.predictions.iloc[0]
    assert frame["lag_1"].iloc[0] == noisy.value_on(origin)


def test_recursive_rows_are_cut_off_one_day_before_each_target(noisy: DemandSeries) -> None:
    forecast = recursive_forecast(noisy, origin=date(2024, 3, 15), steps=5, predictor=lambda r: 1.0)
    expected = forecast.features.frame.index - pd.Timedelta(days=1)
    assert list(forecast.features.cutoffs) == list(expected)
    assert forecast.inputs.index[-1] == pd.Timestamp("2024-03-20")


@pytest.mark.parametrize("bad", [math.nan, math.inf, -1.0, "100", True, None])
def test_recursive_rejects_invalid_predictions(noisy: DemandSeries, bad: object) -> None:
    with pytest.raises(FeatureError, match="predictor returned"):
        recursive_forecast(noisy, origin=date(2024, 3, 15), steps=3, predictor=lambda r: bad)  # type: ignore[arg-type,return-value]


# --- Missing dates, invalid timestamps, insufficient history -------------------------


def test_gap_raises_unless_incomplete_rows_are_allowed() -> None:
    dates = day_range(date(2023, 1, 1), 500)
    values: list[float | None] = [1000.0 + k for k in range(500)]
    values[450] = None  # 2024-03-26
    series = DemandSeries.from_observations(dates, values, allow_missing=True)
    with pytest.raises(DataQualityError, match="missing observations"):
        training_features(series, origin=date(2024, 5, 14))
    matrix = training_features(series, origin=date(2024, 5, 14), allow_incomplete=True)
    gap = pd.Timestamp("2024-03-26")
    for name, offsets in (("lag_1", [1]), ("rolling_mean_28", range(1, 29)), ("lag_364", [364])):
        affected = {gap + pd.Timedelta(days=k) for k in offsets}
        nan_rows = set(matrix.frame.index[matrix.frame[name].isna()])
        assert nan_rows == affected & set(matrix.frame.index), name
    assert not matrix.complete.loc[gap]  # its target is missing


def test_forecast_from_origin_with_gap_in_window_raises() -> None:
    dates = day_range(date(2024, 1, 1), 60)
    values: list[float | None] = [100.0] * 60
    values[55] = None
    series = DemandSeries.from_observations(dates, values, allow_missing=True)
    with pytest.raises(DataQualityError):
        forecast_features(
            series, origin=date(2024, 2, 29), feature_set=FeatureSet((RollingMean(7),))
        )
    with pytest.raises(DataQualityError):
        recursive_forecast(
            series,
            origin=date(2024, 2, 29),
            steps=2,
            predictor=lambda r: 1.0,
            feature_set=FeatureSet((RollingMean(7),)),
        )


def test_insufficient_history(linear: DemandSeries) -> None:
    first_valid = START + timedelta(days=364)
    matrix = training_features(linear, origin=date(2023, 2, 1))
    assert matrix.frame.index[0].date() == first_valid
    with pytest.raises(InsufficientHistoryError, match="earliest valid target: 2022-12-31"):
        training_features(linear, origin=date(2023, 2, 1), first_target=date(2022, 12, 30))
    with pytest.raises(InsufficientHistoryError, match="no training rows"):
        training_features(linear, origin=date(2022, 12, 30))
    with pytest.raises(InsufficientHistoryError):
        forecast_features(linear, origin=date(2022, 12, 29))
    with pytest.raises(InsufficientHistoryError):
        recursive_forecast(linear, origin=date(2022, 6, 1), steps=1, predictor=lambda r: 1.0)
    # The first forecast that has enough history is accepted.
    assert len(forecast_features(linear, origin=date(2022, 12, 30)).frame) == 1


@pytest.mark.parametrize(
    "call",
    [
        lambda s: training_features(s, origin=date(2021, 12, 31)),
        lambda s: forecast_features(s, origin=date(2026, 1, 1)),
        lambda s: recursive_forecast(s, origin=date(2026, 1, 1), steps=1, predictor=lambda r: 1.0),
    ],
)
def test_origin_outside_series(linear: DemandSeries, call: object) -> None:
    with pytest.raises(InsufficientHistoryError):
        call(linear)  # type: ignore[operator]


def test_training_origin_after_data_end_uses_all_data(linear: DemandSeries) -> None:
    matrix = training_features(linear, origin=linear.end + timedelta(days=10))
    assert matrix.frame.index[-1].date() == linear.end
    assert matrix.origin == linear.end + timedelta(days=10)


@pytest.mark.parametrize(
    "origin", ["2024-01-31T12:00", pd.Timestamp("2024-01-31 06:00"), "20240131"]
)
def test_invalid_origin_timestamps_are_rejected(linear: DemandSeries, origin: object) -> None:
    with pytest.raises(ValueError, match="date"):
        forecast_features(linear, origin=origin)  # type: ignore[arg-type]


def test_origin_may_be_a_midnight_timestamp_or_iso_string(linear: DemandSeries) -> None:
    by_date = forecast_features(linear, origin=date(2024, 1, 31)).frame
    pd.testing.assert_frame_equal(by_date, forecast_features(linear, origin="2024-01-31").frame)  # type: ignore[arg-type]
    pd.testing.assert_frame_equal(
        by_date, forecast_features(linear, origin=pd.Timestamp("2024-01-31")).frame
    )


# --- Leap years and calendar boundaries ---------------------------------------------


def test_leap_day_lags_and_calendar(linear: DemandSeries) -> None:
    matrix = training_features(linear, origin=date(2024, 3, 2))
    frame = matrix.frame
    y = values_by_date(linear)
    assert frame.loc["2024-03-01", "lag_1"] == y[date(2024, 2, 29)]
    assert frame.loc["2024-02-29", "lag_1"] == y[date(2024, 2, 28)]
    assert frame.loc["2024-02-29", "day_of_year"] == 60
    assert frame.loc["2024-03-01", "day_of_year"] == 61
    assert frame.loc["2024-02-29", "is_month_end"] == 1
    assert frame.loc["2024-02-28", "is_month_end"] == 0
    # lag_364 of 2024-03-01 is 2023-03-03: 364 calendar days, across the leap day.
    assert frame.loc["2024-03-01", "lag_364"] == y[date(2023, 3, 3)]


def test_year_boundaries_and_iso_weeks() -> None:
    series = linear_series(date(2019, 1, 1), 2600)
    targets = [
        date(2020, 12, 31),  # leap year: day 366, ISO 2020-W53
        date(2021, 1, 1),  # ISO 2020-W53 day 5
        date(2021, 1, 4),  # ISO 2021-W01 day 1
        date(2024, 12, 30),  # ISO 2025-W01
        date(2024, 12, 31),  # leap year: day 366
        date(2025, 1, 1),
        date(2025, 12, 31),
        date(2026, 1, 1),
    ]
    frame = training_features(series, origin=date(2026, 2, 1)).frame
    for target in targets:
        expected = reference_calendar(target)
        for name, value in expected.items():
            assert frame.loc[pd.Timestamp(target), name] == pytest.approx(value, abs=1e-12), (
                target,
                name,
            )
    assert frame.loc["2020-12-31", "iso_week"] == 53
    assert frame.loc["2024-12-30", "iso_week"] == 1
    assert frame.loc["2024-12-31", "day_of_year"] == 366
    # The annual cycle restarts exactly at January 1 in leap and common years.
    for jan1 in ("2021-01-01", "2025-01-01", "2026-01-01"):
        assert frame.loc[jan1, "year_sin_1"] == 0.0
        assert frame.loc[jan1, "year_cos_1"] == 1.0
    step_into_new_year = (
        frame.loc["2025-01-01", "year_sin_1"] - frame.loc["2024-12-31", "year_sin_1"]
    )
    assert step_into_new_year == pytest.approx(math.sin(2 * math.pi / 366), rel=1e-9)


def test_trend_uses_a_fixed_anchor_not_the_data_start(noisy: DemandSeries) -> None:
    later_start = DemandSeries.from_observations(
        [t.date() for t in noisy.to_series().index[200:]], noisy.to_series().iloc[200:].tolist()
    )
    a = training_features(noisy, origin=date(2024, 6, 1)).frame
    b = training_features(later_start, origin=date(2024, 6, 1)).frame
    common = a.index.intersection(b.index)
    assert len(common) > 100
    pd.testing.assert_frame_equal(a.loc[common], b.loc[common])
    assert a.loc["2023-01-01", "trend_days"] == 0
    assert a.loc["2022-12-31", "trend_days"] == -1


def test_holiday_features_on_known_dates() -> None:
    series = linear_series(date(2023, 1, 1), 1300)
    frame = training_features(
        series,
        origin=date(2026, 7, 20),
        feature_set=FeatureSet(tuple(HolidayIndicator(r) for r in HolidayRelation)),
    ).frame
    assert frame.loc["2025-11-27", "is_holiday"] == 1  # Thanksgiving
    assert frame.loc["2025-11-28", "is_day_after_holiday"] == 1
    assert frame.loc["2025-12-24", "is_day_before_holiday"] == 1
    assert frame.loc["2026-07-04", "is_holiday"] == 1  # Saturday
    assert frame.loc["2026-07-04", "is_observed_holiday"] == 0
    assert frame.loc["2026-07-03", "is_observed_holiday"] == 1  # observed Friday
    assert frame.loc["2026-07-03", "is_holiday"] == 0
    assert frame.loc["2023-12-31", "is_day_before_holiday"] == 1
    assert frame.loc["2024-06-19", "is_holiday"] == 1  # Juneteenth
    # 2023, 2024 and 2025 have 11 federal holidays each; 2026 has 6 up to July 20.
    assert frame["is_holiday"].sum() == 11 * 3 + 6


# --- Determinism and reproducibility -------------------------------------------------


def test_identical_inputs_give_identical_bytes(noisy: DemandSeries) -> None:
    digests = {
        frame_digest(training_features(noisy, origin=date(2024, 6, 1)).frame) for _ in range(3)
    }
    assert len(digests) == 1


def test_input_row_order_does_not_matter(noisy: DemandSeries) -> None:
    pairs = list(noisy.to_series().items())
    random.Random(3).shuffle(pairs)
    shuffled = DemandSeries.from_observations([p[0].date() for p in pairs], [p[1] for p in pairs])
    pd.testing.assert_frame_equal(
        training_features(noisy, origin=date(2024, 6, 1)).frame,
        training_features(shuffled, origin=date(2024, 6, 1)).frame,
    )


def test_output_layout_is_fixed(noisy: DemandSeries) -> None:
    matrix = training_features(noisy, origin=date(2024, 6, 1))
    assert tuple(matrix.frame.columns) == DEFAULT.names
    assert set(matrix.frame.dtypes) == {np.dtype("float64")}
    assert matrix.frame.index.name == "target_date"
    assert matrix.frame.index.is_monotonic_increasing


def test_feature_set_fingerprint_is_pinned() -> None:
    """Changing any default feature definition must be a deliberate, reviewed change."""
    assert DEFAULT.fingerprint() == (
        "f69f30a2674b71922520b7ab6b87fddffd4a5038f0fb9a5f992209f4fabc8f33"
    )
    assert default_feature_set().fingerprint() == DEFAULT.fingerprint()
    assert default_feature_set(date(2024, 1, 1)).fingerprint() != DEFAULT.fingerprint()
    assert len(DEFAULT) == 34
    assert DEFAULT.max_offset == 364
    assert DEFAULT.min_offset == 1


# --- Definitions -------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Lag(0),
        lambda: Lag(-1),
        lambda: Lag(True),  # type: ignore[arg-type]
        lambda: Lag(1.0),  # type: ignore[arg-type]
        lambda: Lag(5000),
        lambda: RollingMean(0),
        lambda: RollingStd(1),
        lambda: RollingStd(7, ddof=2),
        lambda: SameWeekdayMean(0),
        lambda: SameWeekdayMean(200),
        lambda: WeekdayIndicator(0),
        lambda: WeekdayIndicator(8),
        lambda: AnnualFourier(0, "sin"),
        lambda: AnnualFourier(1, "tan"),
        lambda: FeatureSet(()),
        lambda: FeatureSet((Lag(1), Lag(1))),
    ],
)
def test_invalid_definitions_are_rejected(factory: object) -> None:
    with pytest.raises(FeatureError):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize("horizon", [0, -1, True, 1.5])
def test_invalid_horizons_are_rejected(linear: DemandSeries, horizon: object) -> None:
    with pytest.raises(FeatureError):
        training_features(linear, origin=date(2024, 1, 31), horizon=horizon)  # type: ignore[arg-type]
    with pytest.raises(FeatureError):
        forecast_features(linear, origin=date(2024, 1, 31), horizons=(horizon,))  # type: ignore[arg-type]


def test_invalid_requests_are_rejected(linear: DemandSeries) -> None:
    with pytest.raises(FeatureError, match="horizon"):
        forecast_features(linear, origin=date(2024, 1, 31), horizons=())
    with pytest.raises(FeatureError, match="unique"):
        forecast_features(linear, origin=date(2024, 1, 31), horizons=(1, 1))
    for steps in (0, True):
        with pytest.raises(FeatureError, match="steps"):
            recursive_forecast(
                linear, origin=date(2024, 1, 31), steps=steps, predictor=lambda r: 1.0
            )


def test_every_default_feature_is_documented_and_typed() -> None:
    catalog = DEFAULT.catalog()
    assert list(catalog["name"]) == list(DEFAULT.names)
    assert catalog["description"].str.len().min() > 20
    assert set(catalog["kind"]) == {k.value for k in FeatureKind}
    for feature in DEFAULT:
        spec = feature.spec()
        assert spec["name"] == feature.name
        assert spec["type"] == type(feature).__name__
    # Monday is the reference level: no full dummy set (avoids the legacy collinearity).
    assert "is_iso_weekday_1" not in DEFAULT.names


def test_missing_last_target_is_reported() -> None:
    dates = day_range(date(2024, 1, 1), 10)
    values: list[float | None] = [float(k) for k in range(10)]
    values[-1] = None
    series = DemandSeries.from_observations(dates, values, allow_missing=True)
    feature_set = FeatureSet((Lag(1),))
    with pytest.raises(DataQualityError, match="training target"):
        training_features(series, origin=date(2024, 1, 10), feature_set=feature_set)
    matrix = training_features(
        series, origin=date(2024, 1, 10), feature_set=feature_set, allow_incomplete=True
    )
    assert list(matrix.complete) == [True] * 8 + [False]


def test_forecast_matrix_completeness_ignores_absent_target(linear: DemandSeries) -> None:
    matrix = forecast_features(linear, origin=date(2024, 1, 31))
    assert matrix.target is None
    assert bool(matrix.complete.iloc[0])


def test_holiday_feature_on_empty_batch() -> None:
    context = FeatureContext(
        targets=pd.DatetimeIndex([]),
        demand=pd.Series([], dtype="float64", index=pd.DatetimeIndex([])),
        cutoffs=pd.DatetimeIndex([]),
        origins=(),
        events=EventCalendar(),
    )
    assert HolidayIndicator(HolidayRelation.ON).compute(context).shape == (0,)


# --- Regressions from the independent review -----------------------------------------


def test_training_rows_see_events_only_from_their_own_origin(linear: DemandSeries) -> None:
    """An event announced after it began is invisible to rows whose origin precedes it."""
    late = KnownEvent("late notice", date(2024, 2, 10), date(2024, 2, 25), date(2024, 2, 14))
    events = EventCalendar((late,))
    only_event = FeatureSet((DeclaredEventIndicator(),))

    def flagged(horizon: int) -> list[date]:
        train = training_features(
            linear, origin=date(2024, 3, 1), horizon=horizon, feature_set=only_event, events=events
        )
        return [d.date() for d in train.frame.index[train.frame["is_declared_event"] == 1]]

    # Row t has origin t - h; it sees the event only once t - h >= 2024-02-14.
    assert flagged(1) == day_range(date(2024, 2, 15), 11)  # 02-15 .. 02-25
    assert flagged(7) == day_range(date(2024, 2, 21), 5)  # 02-21 .. 02-25


def test_direct_rows_see_events_only_as_of_the_origin(linear: DemandSeries) -> None:
    origin = date(2024, 2, 1)
    event = KnownEvent(
        "parade", origin + timedelta(7), origin + timedelta(7), origin + timedelta(1)
    )
    calendar_only = FeatureSet((DeclaredEventIndicator(), Trend()))
    matrix = forecast_features(
        linear,
        origin=origin,
        horizons=(1, 7),
        feature_set=calendar_only,
        events=EventCalendar((event,)),
    )
    assert list(matrix.frame["is_declared_event"]) == [0.0, 0.0]
    later = forecast_features(
        linear,
        origin=origin + timedelta(1),
        horizons=(6,),
        feature_set=calendar_only,
        events=EventCalendar((event,)),
    )
    assert list(later.frame["is_declared_event"]) == [1.0]


def test_recursive_and_training_rows_differ_only_by_event_knowledge(noisy: DemandSeries) -> None:
    """Documented train/serve difference: a recursive row knows events as of the origin."""
    actual = noisy.to_series()
    origin = date(2024, 3, 15)
    event = KnownEvent("fair", date(2024, 3, 25), date(2024, 3, 26), date(2024, 3, 20))
    events = EventCalendar((event,))
    forecast = recursive_forecast(
        noisy, origin=origin, steps=20, predictor=lambda r: float(actual.loc[r.name]), events=events
    )
    train = training_features(noisy, origin=origin + timedelta(days=20), events=events)
    rows = train.frame.loc[forecast.features.frame.index]
    difference = forecast.features.frame.compare(rows)
    assert list(difference.columns.get_level_values(0).unique()) == ["is_declared_event"]
    assert [d.date() for d in difference.index] == [date(2024, 3, 25), date(2024, 3, 26)]


def test_training_rows_do_not_depend_on_the_origin(noisy: DemandSeries) -> None:
    """M2 can build training rows once and slice them for each earlier origin."""
    early = training_features(noisy, origin=date(2024, 1, 31), horizon=1)
    late = training_features(noisy, origin=date(2024, 5, 31), horizon=1)
    pd.testing.assert_frame_equal(early.frame, late.frame.loc[early.frame.index])


def test_builders_pass_no_data_after_the_origin(
    noisy: DemandSeries, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Structural truncation, tested on its own (the runtime guard is tested separately)."""
    import atlas.features as features

    seen: list[pd.Timestamp] = []
    original = features._build

    def spy(**kwargs: object) -> object:
        demand = kwargs["demand"]
        assert isinstance(demand, pd.Series)
        seen.append(demand.index.max())
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(features, "_build", spy)
    origin = date(2024, 3, 1)
    training_features(noisy, origin=origin)
    forecast_features(noisy, origin=origin)
    assert max(seen) == pd.Timestamp(origin)
    seen.clear()
    recursive_forecast(noisy, origin=origin, steps=3, predictor=lambda r: 1.0)
    assert seen[0] == pd.Timestamp(origin)  # later steps hold only predictions after it


@dataclass(frozen=True)
class _ReadsTooFarBack(Feature):
    """Declares offset 1 but reads 400 days back, beyond any window it asked for."""

    kind: ClassVar[FeatureKind] = FeatureKind.LAG

    @property
    def name(self) -> str:
        return "far"

    @property
    def description(self) -> str:
        return "reads outside its declared window"

    @property
    def offsets(self) -> tuple[int, ...]:
        return (1,)

    def compute(self, context: FeatureContext) -> np.ndarray:
        return context.read(400)


def test_reads_before_the_held_window_are_missing_not_wrapped(noisy: DemandSeries) -> None:
    with pytest.raises(DataQualityError):
        recursive_forecast(
            noisy,
            origin=date(2024, 3, 1),
            steps=2,
            predictor=lambda r: 1.0,
            feature_set=FeatureSet((_ReadsTooFarBack(),)),
        )


@pytest.mark.parametrize("feature", [RollingStd(7), SameWeekdayMean(4), RollingMean(7), Lag(7)])
def test_each_window_feature_alone_refuses_a_gap(feature: Feature) -> None:
    dates = day_range(date(2024, 1, 1), 60)
    values: list[float | None] = [100.0 + k % 5 for k in range(60)]
    values[49] = None  # 2024-02-19
    series = DemandSeries.from_observations(dates, values, allow_missing=True)
    feature_set = FeatureSet((feature,))
    with pytest.raises(DataQualityError):
        training_features(
            series,
            origin=date(2024, 2, 29),
            feature_set=feature_set,
            first_target=date(2024, 2, 20),
        )
    matrix = training_features(
        series,
        origin=date(2024, 2, 29),
        feature_set=feature_set,
        first_target=date(2024, 2, 20),
        allow_incomplete=True,
    )
    gap = pd.Timestamp("2024-02-19")
    reads_gap = {
        t
        for t in matrix.frame.index
        if any(t - pd.Timedelta(days=k) == gap for k in feature.offsets)
    }
    assert set(matrix.frame.index[matrix.frame[feature.name].isna()]) == reads_gap
    assert reads_gap


def test_eve_and_after_are_relative_to_the_actual_holiday() -> None:
    series = linear_series(date(2022, 1, 1), 1700)
    frame = training_features(
        series,
        origin=date(2026, 7, 31),
        feature_set=FeatureSet(tuple(HolidayIndicator(r) for r in HolidayRelation)),
    ).frame
    # Independence Day 2026 is a Saturday, observed on Friday 07-03.
    assert frame.loc["2026-07-03", ["is_day_before_holiday", "is_observed_holiday"]].tolist() == [
        1,
        1,
    ]
    assert frame.loc["2026-07-05", "is_day_after_holiday"] == 1
    assert frame.loc["2026-07-06", "is_day_after_holiday"] == 0
    # Christmas 2022 is a Sunday, observed on Monday 12-26.
    assert frame.loc["2022-12-24", "is_day_before_holiday"] == 1
    assert frame.loc["2022-12-26", ["is_observed_holiday", "is_day_after_holiday"]].tolist() == [
        1,
        1,
    ]


def test_string_parameters_are_coerced_to_their_enums() -> None:
    by_string = HolidayIndicator("observed")  # type: ignore[arg-type]
    assert by_string.relation is HolidayRelation.OBSERVED
    assert by_string.spec() == HolidayIndicator(HolidayRelation.OBSERVED).spec()
    assert Calendar("month").field is CalendarField.MONTH  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: HolidayIndicator("tomorrow"),  # type: ignore[arg-type]
        lambda: Calendar("century"),  # type: ignore[arg-type]
        lambda: WeekdayIndicator(2.0),  # type: ignore[arg-type]
        lambda: AnnualFourier(1.5, "sin"),  # type: ignore[arg-type]
        lambda: Trend(datetime(2023, 1, 1, 18)),
        lambda: Trend("2023-01-01"),  # type: ignore[arg-type]
        lambda: RollingStd(7, ddof=True),  # type: ignore[arg-type]
    ],
)
def test_mistyped_parameters_are_rejected(factory: object) -> None:
    with pytest.raises(FeatureError):
        factory()  # type: ignore[operator]


def test_rolling_std_names_are_unique_per_ddof() -> None:
    assert RollingStd(7).name == "rolling_std_7"
    assert RollingStd(7, ddof=0).name == "rolling_std_7_ddof_0"
    FeatureSet((RollingStd(7), RollingStd(7, ddof=0)))  # no duplicate-name error


def test_unsorted_horizons_give_sorted_rows(linear: DemandSeries) -> None:
    weekly = FeatureSet((Lag(7),))
    matrix = forecast_features(
        linear, origin=date(2024, 1, 31), horizons=(7, 1, 3), feature_set=weekly
    )
    assert matrix.frame.index.is_monotonic_increasing
    assert matrix.horizon == 7


def test_numpy_datetime_origin_is_a_date(linear: DemandSeries) -> None:
    matrix = forecast_features(linear, origin=np.datetime64("2024-01-31"))  # type: ignore[arg-type]
    assert matrix.origin == date(2024, 1, 31)
    assert type(matrix.origin) is date
