"""Rolling-origin backtest engine (``atlas.backtest``): time travel, plan, failures, projection.

Covers :func:`run_backtest` (information set handed to each model, one fit per distinct
origin, records, statuses, fit rows, MASE scales, ordering, determinism), the plan objects
(:class:`BacktestPlan`, :class:`Period`, :class:`TrainingWindow`) and their fail-closed
validation, the failure policy and output contract of :func:`forecast_at_origin`, the
missing-data policy as seen through the engine, and :func:`project` parity with the backtest.

Expected values never come from the code under test. They come from:

* closed forms on the linear series ``y(start + k) = 1000 + k`` and ``datetime`` arithmetic;
* plain-Python references written in this file: the input days each direct baseline reads
  (naive ``y(o)``, seasonal naive ``y(o + h - 7 * ceil(h / 7))``, the same-weekday windows
  ``y(o + h - 7w)`` for ``w = ceil(h / 7) ..``), the days a 28-day recursive regression reads
  (``o - 27 .. o``) and the training rows that read a gap (``t - 28 <= gap <= t``), the MASE
  scale as the mean of ``|y_t - y_(t-7)|`` over pairs with both days observed, and the status
  rules (failure first, then beyond the snapshot, then a missing actual);
* comparisons between engine runs that must agree by construction: two series equal through
  the data cutoff, the same plan run twice or with its periods reordered, and a backtest
  origin against a projection from the series truncated at that origin.

Time travel is checked on every record whose origin is ``<= d`` (not only ``< d``): with the
data equal through ``d``, a forecast issued at ``d`` itself must not change either.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import atlas.models
from atlas.backtest import (
    FIT_COLUMNS,
    RECORD_COLUMNS,
    BacktestPlan,
    BacktestResult,
    Period,
    PeriodRole,
    RecordStatus,
    TrainingWindow,
    forecast_at_origin,
    project,
    run_backtest,
    validate_path,
)
from atlas.errors import (
    ConfigError,
    DataQualityError,
    FeatureError,
    ForecastError,
    InsufficientHistoryError,
    LeakageError,
    ModelFitError,
)
from atlas.experiment import csv_bytes
from atlas.loader import LoadedTable
from atlas.models import (
    Failure,
    FailureStage,
    FitDiagnostics,
    FittedForecaster,
    Forecaster,
    ForecastPath,
    ModelFamily,
    ModelSpec,
    NaiveForecaster,
    Procedure,
    SameWeekdayMeanForecaster,
    SameWeekdayMedianForecaster,
    SeasonalExponentialSmoothing,
    SeasonalNaiveForecaster,
    atlas_linear_regression,
    atlas_regression_feature_set,
    legacy_linear_regression,
    target_dates,
)
from atlas.series import DemandSeries
from tests.m2_helpers import (
    ContractBreakingForecaster,
    FailingForecaster,
    LeakyForecaster,
    RaisingForecaster,
    SpyForecaster,
    linear_values,
    noisy_weekly_values,
    perturbed_after,
    series_from,
    single_period_plan,
)

START = date(2023, 1, 1)
DAYS = 200  # 2023-01-01 .. 2023-07-19
MIN_ROWS = 60  # regressions: enough complete training rows from ~2023-04 on
# Sliding window: long enough to keep Presidents' Day (2023-02-20) and its eve and day after
# in the training rows of every origin used here; with fewer days the ATLAS regression
# rightly fails on a constant holiday column.
SLIDING = 120
SELECTION = PeriodRole.SELECTION
CONFIRMATION = PeriodRole.CONFIRMATION
DESCRIPTIVE = PeriodRole.DESCRIPTIVE

ALL_IDS = (
    "naive",
    "seasonal_naive",
    "same_weekday_mean_4w",
    "same_weekday_median_5w",
    "seasonal_exponential_smoothing",
    "legacy_linear_regression",
    "atlas_linear_regression",
)
DIRECT_IDS = ALL_IDS[:4]
REGRESSION_IDS = ALL_IDS[5:]
STATUSES = {s.value for s in RecordStatus}


def all_models() -> list[Forecaster]:
    """Every real model, in the configured order (baselines first)."""
    return [
        NaiveForecaster(),
        SeasonalNaiveForecaster(),
        SameWeekdayMeanForecaster(),
        SameWeekdayMedianForecaster(),
        SeasonalExponentialSmoothing(),
        legacy_linear_regression(min_training_rows=MIN_ROWS),
        atlas_linear_regression(min_training_rows=MIN_ROWS),
    ]


def regression_models() -> list[Forecaster]:
    return [
        legacy_linear_regression(min_training_rows=MIN_ROWS),
        atlas_linear_regression(min_training_rows=MIN_ROWS),
    ]


def direct_baselines() -> list[Forecaster]:
    return [
        NaiveForecaster(),
        SeasonalNaiveForecaster(),
        SameWeekdayMeanForecaster(),
        SameWeekdayMedianForecaster(),
    ]


def day(offset: int) -> date:
    return START + timedelta(days=offset)


def value_at(values: Sequence[float | None], when: date) -> float | None:
    """Plain lookup of the value list a series was built from (``None`` = missing)."""
    index = (when - START).days
    if index < 0 or index >= len(values):
        return None
    value = values[index]
    return None if value is None else float(value)


def ceil_weeks(horizon: int) -> int:
    return -(-horizon // 7)


def origins_plan(
    origins: Sequence[date],
    *,
    max_horizon: int,
    window_days: int | None = None,
    data_delay_days: int = 0,
    min_history_days: int = 1,
) -> BacktestPlan:
    """One descriptive single-origin period per origin (for irregular origin sets)."""
    return BacktestPlan(
        periods=tuple(
            Period(f"o{i:02d}", DESCRIPTIVE, origin, origin) for i, origin in enumerate(origins)
        ),
        max_horizon=max_horizon,
        min_history_days=min_history_days,
        training_window=TrainingWindow(window_days),
        data_delay_days=data_delay_days,
    )


def at_origin(frame: pd.DataFrame, origin: date, model_id: str | None = None) -> pd.DataFrame:
    selected = frame["origin"] == pd.Timestamp(origin)
    if model_id is not None:
        selected &= frame["model_id"] == model_id
    return frame[selected].reset_index(drop=True)


def through(frame: pd.DataFrame, last_origin: date) -> pd.DataFrame:
    return frame[frame["origin"] <= pd.Timestamp(last_origin)].reset_index(drop=True)


def after(frame: pd.DataFrame, first_origin: date) -> pd.DataFrame:
    return frame[frame["origin"] > pd.Timestamp(first_origin)].reset_index(drop=True)


def comparable(frame: pd.DataFrame) -> pd.DataFrame:
    """Text-like columns as plain objects with ``None`` for missing (dtype-neutral)."""
    result = frame.reset_index(drop=True)
    for column in result.columns:
        values = result[column]
        if not (pd.api.types.is_numeric_dtype(values) or pd.api.types.is_datetime64_dtype(values)):
            result[column] = pd.Series(
                [None if v is None or v is pd.NA or v != v else v for v in values], dtype=object
            )
    return result


def by_horizon(records: pd.DataFrame, model_id: str, origin: date) -> pd.DataFrame:
    return at_origin(records, origin, model_id).set_index("horizon").sort_index()


def changed_horizons(
    a: pd.DataFrame, b: pd.DataFrame, model_id: str, origin: date, column: str = "prediction"
) -> set[int]:
    left = by_horizon(a, model_id, origin)[column]
    right = by_horizon(b, model_id, origin)[column]
    assert list(left.index) == list(right.index)
    return {int(h) for h in left.index if left.loc[h] != right.loc[h]}


# --- Local instrumented models ---------------------------------------------------------


def _spec(model_id: str) -> ModelSpec:
    return ModelSpec(model_id, "test_" + model_id, ModelFamily.BASELINE, Procedure.DIRECT, "test")


@dataclass
class StepRecorder(Forecaster):
    """Records ``(history.end, steps)`` of every forecast; predicts the step number."""

    model_id: str = "steps"
    calls: list[tuple[date, int]] = field(default_factory=list)

    @property
    def spec(self) -> ModelSpec:
        return _spec(self.model_id)

    def fit(self, history: DemandSeries) -> FittedForecaster:
        return _StepFit(self, history.end)


class _StepFit(FittedForecaster):
    def __init__(self, owner: StepRecorder, origin: date) -> None:
        self.owner = owner
        self.origin = origin
        self.diagnostics = FitDiagnostics()

    def forecast(self, steps: int) -> ForecastPath:
        self.owner.calls.append((self.origin, steps))
        index = target_dates(self.origin, steps)
        values = np.arange(1, steps + 1, dtype="float64")
        return ForecastPath(self.origin, pd.Series(values, index=index))


PathBuilder = Callable[[date, int], ForecastPath]


@dataclass(frozen=True)
class PathForecaster(Forecaster):
    """Returns whatever path ``build(origin, steps)`` makes (output-contract tests)."""

    build: PathBuilder
    model_id: str = "scripted_path"

    @property
    def spec(self) -> ModelSpec:
        return _spec(self.model_id)

    def fit(self, history: DemandSeries) -> FittedForecaster:
        return _PathFit(history.end, self.build)


class _PathFit(FittedForecaster):
    def __init__(self, origin: date, build: PathBuilder) -> None:
        self.origin = origin
        self.build = build
        self.diagnostics = FitDiagnostics()

    def forecast(self, steps: int) -> ForecastPath:
        return self.build(self.origin, steps)


@dataclass(frozen=True)
class ErrorAtStage(Forecaster):
    """Raises ``fit_error`` in ``fit`` or ``forecast_error`` in ``forecast`` (if given)."""

    fit_error: Exception | None = None
    forecast_error: Exception | None = None
    model_id: str = "error_at_stage"

    @property
    def spec(self) -> ModelSpec:
        return _spec(self.model_id)

    def fit(self, history: DemandSeries) -> FittedForecaster:
        if self.fit_error is not None:
            raise self.fit_error
        return _ErrorFit(history.end, self.forecast_error)


class _ErrorFit(FittedForecaster):
    def __init__(self, origin: date, error: Exception | None) -> None:
        self.origin = origin
        self.error = error
        self.diagnostics = FitDiagnostics()

    def forecast(self, steps: int) -> ForecastPath:
        if self.error is not None:
            raise self.error
        index = target_dates(self.origin, steps)
        return ForecastPath(self.origin, pd.Series(5.0, index=index, dtype="float64"))


def constant_path(origin: date, steps: int, value: float = 1.0) -> ForecastPath:
    return ForecastPath(origin, pd.Series(value, index=target_dates(origin, steps)))


# --- 1. Time travel at engine level ------------------------------------------------------

TT_FIRST = date(2023, 5, 1)
TT_LAST = date(2023, 5, 6)
TT_SPLIT = date(2023, 5, 3)


def _time_travel_runs(
    seed: int,
    split: date,
    window_days: int | None,
    *,
    first: date,
    last: date,
    stride: int,
    horizon: int,
) -> tuple[BacktestResult, BacktestResult, DemandSeries, DemandSeries]:
    a = series_from(START, noisy_weekly_values(DAYS, seed))
    b = perturbed_after(a, split, seed + 1)
    plan = single_period_plan(
        first,
        last,
        max_horizon=horizon,
        stride_days=stride,
        window_days=window_days,
        min_history_days=60,
    )
    return run_backtest(a, plan, all_models()), run_backtest(b, plan, all_models()), a, b


def _assert_identical_through(a: BacktestResult, b: BacktestResult, split: date) -> None:
    """Everything produced at origins ``<= split`` agrees; only later actuals may differ."""
    early_a, early_b = through(a.records, split), through(b.records, split)
    pd.testing.assert_frame_equal(early_a.drop(columns="actual"), early_b.drop(columns="actual"))
    known = early_a["target_date"] <= pd.Timestamp(split)
    pd.testing.assert_series_equal(early_a.loc[known, "actual"], early_b.loc[known, "actual"])
    pd.testing.assert_frame_equal(through(a.fits, split), through(b.fits, split))
    pd.testing.assert_frame_equal(through(a.coefficients, split), through(b.coefficients, split))


@pytest.mark.parametrize("window_days", [None, SLIDING], ids=["expanding", "sliding"])
def test_records_before_the_perturbation_are_identical_for_every_model(
    window_days: int | None,
) -> None:
    a, b, series_a, series_b = _time_travel_runs(
        7, TT_SPLIT, window_days, first=TT_FIRST, last=TT_LAST, stride=1, horizon=30
    )
    # The perturbation is real: every day after the split differs, none before it.
    sa, sb = series_a.to_series(), series_b.to_series()
    split = pd.Timestamp(TT_SPLIT)
    assert sa.loc[:split].equals(sb.loc[:split])
    assert bool(
        (sa.loc[split + pd.Timedelta(days=1) :] != sb.loc[split + pd.Timedelta(days=1) :]).all()
    )

    early = through(a.records, TT_SPLIT)
    assert set(early["model_id"]) == set(ALL_IDS)
    assert len(early) == 3 * len(ALL_IDS) * 30  # origins 05-01, 05-02, 05-03
    assert bool((early["status"] == "ok").all())  # every model really forecast
    _assert_identical_through(a, b, TT_SPLIT)
    # Actuals after the split come from the perturbed series (they are attached afterwards).
    later_targets = early["target_date"] > split
    assert bool(
        (
            early.loc[later_targets, "actual"]
            != through(b.records, TT_SPLIT).loc[later_targets, "actual"]
        ).all()
    )

    # Positive control: from the first origin after the split the forecasts do change.
    for origin in (date(2023, 5, 4), date(2023, 5, 5), date(2023, 5, 6)):
        assert changed_horizons(a.records, b.records, "naive", origin) == set(range(1, 31))
        for model_id in REGRESSION_IDS:
            changed = changed_horizons(a.records, b.records, model_id, origin, "prediction_raw")
            assert changed == set(range(1, 31))


@settings(max_examples=6, deadline=None)
@given(
    offset=st.integers(min_value=0, max_value=8),
    seed=st.integers(min_value=0, max_value=10**6),
    window_days=st.sampled_from([None, SLIDING]),
)
def test_time_travel_property(offset: int, seed: int, window_days: int | None) -> None:
    split = date(2023, 4, 30) + timedelta(days=offset)  # before, among and after the origins
    first, last = date(2023, 5, 1), date(2023, 5, 7)  # origins 05-01, 05-03, 05-05, 05-07
    a, b, _, _ = _time_travel_runs(
        seed, split, window_days, first=first, last=last, stride=2, horizon=10
    )
    _assert_identical_through(a, b, split)
    later_a, later_b = after(a.records, split), after(b.records, split)
    if len(later_a):
        # Some forecast issued after the split must see the perturbed data.
        assert not later_a["prediction"].equals(later_b["prediction"])


# --- 2. Positive control: exactly the horizons that read the changed day ------------------


def _median_crossing(values: list[int], when: date) -> int:
    """A replacement for ``y(when)`` that moves the median of it and its 4 same weekdays."""
    window = [values[(when - START).days - 7 * w] for w in range(5)]
    current = values[(when - START).days]
    replacement = 0 if current >= statistics.median(window) else 3 * max(window)
    changed = [replacement, *window[1:]]
    assert statistics.median(changed) != statistics.median(window)  # precondition
    return replacement


def test_changing_only_the_origin_day_changes_exactly_the_horizons_that_read_it() -> None:
    origin = date(2023, 5, 10)
    k = (origin - START).days
    values = noisy_weekly_values(DAYS, 11)
    at_origin_changed = list(values)
    at_origin_changed[k] = _median_crossing(values, origin)
    before_changed = list(values)
    before_changed[k - 1] = _median_crossing(values, origin - timedelta(days=1))

    plan = single_period_plan(origin, origin, max_horizon=30, min_history_days=60)
    base = run_backtest(series_from(START, values), plan, all_models()).records
    moved = run_backtest(series_from(START, at_origin_changed), plan, all_models()).records
    moved_before = run_backtest(series_from(START, before_changed), plan, all_models()).records
    assert bool((base["status"] == "ok").all())

    every = set(range(1, 31))
    weekly = {7, 14, 21, 28}  # o + h - 7w = o  <=>  h = 7w
    expected_origin = {
        "naive": every,
        "seasonal_naive": weekly,
        "same_weekday_mean_4w": weekly,
        "same_weekday_median_5w": weekly,
        "seasonal_exponential_smoothing": every,  # the level absorbs alpha * error at o
        "legacy_linear_regression": every,
        "atlas_linear_regression": every,
    }
    for model_id, expected in expected_origin.items():
        assert changed_horizons(base, moved, model_id, origin) == expected, model_id

    one_less = {6, 13, 20, 27}  # o + h - 7w = o - 1  <=>  h + 1 = 7w
    expected_before = {
        "naive": set(),
        "seasonal_naive": one_less,
        "same_weekday_mean_4w": one_less,
        "same_weekday_median_5w": one_less,
        "legacy_linear_regression": every,
        "atlas_linear_regression": every,
    }
    for model_id, expected in expected_before.items():
        assert changed_horizons(base, moved_before, model_id, origin) == expected, model_id

    # And the values themselves are the closed forms of the definitions.
    seasonal = by_horizon(base, "seasonal_naive", origin)["prediction"]
    assert seasonal.loc[7] == values[k]
    assert seasonal.loc[6] == values[k - 1]
    assert seasonal.loc[8] == values[k - 6]
    assert by_horizon(moved, "naive", origin)["prediction"].loc[30] == at_origin_changed[k]


# --- 3. What each model receives -------------------------------------------------------


@pytest.mark.parametrize(
    ("window_days", "delay"),
    [(None, 0), (None, 3), (40, 0), (40, 2), (200, 0)],
    ids=["expanding", "expanding-delay3", "sliding40", "sliding40-delay2", "sliding-clamped"],
)
def test_models_receive_exactly_the_information_set_at_the_data_cutoff(
    window_days: int | None, delay: int
) -> None:
    values = noisy_weekly_values(120, 5)  # 2023-01-01 .. 2023-04-30
    series = series_from(START, values)
    development = Period("development", SELECTION, date(2023, 3, 1), date(2023, 3, 20), 3)
    bridge = Period("bridge", DESCRIPTIVE, date(2023, 3, 10), date(2023, 3, 24), 2)
    plan = BacktestPlan(
        periods=(development, bridge),
        max_horizon=7,
        min_history_days=30,
        training_window=TrainingWindow(window_days),
        data_delay_days=delay,
    )
    dev_origins = [date(2023, 3, d) for d in range(1, 21, 3)]
    bridge_origins = [date(2023, 3, d) for d in range(10, 25, 2)]
    distinct = sorted(set(dev_origins) | set(bridge_origins))
    assert len(distinct) < len(dev_origins) + len(bridge_origins)  # the periods overlap
    assert plan.origins() == tuple(distinct)
    assert plan.memberships() == {
        o: tuple(
            name
            for name, origins in (("development", dev_origins), ("bridge", bridge_origins))
            if o in origins
        )
        for o in distinct
    }
    assert plan.stride_by_period() == {"development": 3, "bridge": 2}
    assert plan.scheduled_records(3) == (len(dev_origins) + len(bridge_origins)) * 7 * 3

    spy = SpyForecaster()
    result = run_backtest(series, plan, [spy])

    # One fit per distinct origin, in chronological order, on data through the cutoff.
    assert len(spy.seen) == len(distinct)
    for (first_seen, last_seen, length, total), origin in zip(spy.seen, distinct, strict=True):
        cutoff = origin - timedelta(days=delay)
        first = START if window_days is None else max(START, cutoff - timedelta(window_days - 1))
        assert (first_seen, last_seen) == (first, cutoff)
        assert length == (cutoff - first).days + 1
        assert total == float(sum(values[(first - START).days : (cutoff - START).days + 1]))

    # Records and fit rows are emitted once per period membership.
    assert len(result.fits) == len(dev_origins) + len(bridge_origins)
    assert len(result.records) == (len(dev_origins) + len(bridge_origins)) * 7
    for name, origins in (("development", dev_origins), ("bridge", bridge_origins)):
        fits = result.fits[result.fits["period"] == name]
        assert [ts.date() for ts in fits["origin"]] == origins
        assert [ts.date() for ts in fits["data_cutoff"]] == [
            o - timedelta(days=delay) for o in origins
        ]
    lag = (result.records["origin"] - result.records["data_cutoff"]).dt.days
    assert bool((lag == delay).all())
    # Sorted by period (name), then origin and horizon: "bridge" (from 03-10) comes before
    # "development" (from 03-01), so a sort by origin first would interleave them.
    records = result.records
    expected_keys = [
        (name, pd.Timestamp(o), h)
        for name, origins in (("bridge", bridge_origins), ("development", dev_origins))
        for o in origins
        for h in range(1, 8)
    ]
    assert list(zip(records["period"], records["origin"], records["horizon"], strict=True)) == (
        expected_keys
    )
    assert list(zip(result.fits["period"], result.fits["origin"], strict=True)) == [
        (name, pd.Timestamp(o))
        for name, origins in (("bridge", bridge_origins), ("development", dev_origins))
        for o in origins
    ]


# --- 4. Horizons and target dates across calendar boundaries ------------------------------

BOUNDARY_ORIGINS = (date(2023, 12, 31), date(2024, 2, 28), date(2024, 2, 29), date(2024, 12, 31))


def _boundary_plan(delay: int) -> BacktestPlan:
    return BacktestPlan(
        periods=tuple(
            Period("o" + o.strftime("%Y%m%d"), DESCRIPTIVE, o, o) for o in BOUNDARY_ORIGINS
        ),
        max_horizon=30,
        min_history_days=60,
        data_delay_days=delay,
    )


def test_target_dates_and_values_across_year_month_and_leap_boundaries() -> None:
    values = linear_values(800)  # 2023-01-01 .. 2025-03-11, y = 1000 + day index
    series = series_from(START, values)
    steps = StepRecorder()
    # Configured order deliberately not alphabetical: records follow the configuration.
    models: list[Forecaster] = [steps, SeasonalNaiveForecaster(), NaiveForecaster()]
    result = run_backtest(series, _boundary_plan(0), models)
    records = result.records
    assert len(records) == 4 * 3 * 30

    hand = {
        (date(2023, 12, 31), 1): date(2024, 1, 1),
        (date(2023, 12, 31), 30): date(2024, 1, 30),
        (date(2024, 2, 28), 1): date(2024, 2, 29),
        (date(2024, 2, 28), 2): date(2024, 3, 1),
        (date(2024, 2, 29), 1): date(2024, 3, 1),
        (date(2024, 2, 29), 30): date(2024, 3, 30),
        (date(2024, 12, 31), 1): date(2025, 1, 1),
        (date(2024, 12, 31), 30): date(2025, 1, 30),
    }
    for (origin, horizon), target in hand.items():
        row = by_horizon(records, "naive", origin).loc[horizon]
        assert row["target_date"].date() == target

    for origin in BOUNDARY_ORIGINS:
        for model_id in ("naive", "seasonal_naive", "steps"):
            rows = by_horizon(records, model_id, origin)
            assert list(rows.index) == list(range(1, 31))
            for horizon, row in rows.iterrows():
                target = origin + timedelta(days=int(horizon))
                assert row["target_date"].date() == target
                assert row["data_cutoff"].date() == origin
                assert row["actual"] == value_at(values, target)
                if model_id == "naive":
                    assert row["prediction"] == value_at(values, origin)
                elif model_id == "seasonal_naive":
                    read = target - timedelta(days=7 * ceil_weeks(int(horizon)))
                    assert row["prediction"] == value_at(values, read)
                else:
                    assert row["prediction"] == float(horizon)
    assert steps.calls == [(origin, 30) for origin in BOUNDARY_ORIGINS]

    # Records are sorted by period, origin, model (configured order) and horizon.
    order = {"steps": 0, "seasonal_naive": 1, "naive": 2}
    assert list(records["model_id"].iloc[::30]) == ["steps", "seasonal_naive", "naive"] * 4
    keys = list(
        zip(
            records["period"],
            records["origin"],
            records["model_id"].map(order),
            records["horizon"],
            strict=True,
        )
    )
    assert keys == sorted(keys)
    fit_keys = list(
        zip(
            result.fits["period"],
            result.fits["origin"],
            result.fits["model_id"].map(order),
            strict=True,
        )
    )
    assert fit_keys == sorted(fit_keys)
    assert list(records.columns) == list(RECORD_COLUMNS)
    assert list(result.fits.columns) == list(FIT_COLUMNS)
    assert set(records["record_type"]) == {"historical_backtest"}


def test_data_delay_shifts_the_cutoff_not_the_targets() -> None:
    values = linear_values(800)
    series = series_from(START, values)
    steps = StepRecorder()
    models: list[Forecaster] = [NaiveForecaster(), SeasonalNaiveForecaster(), steps]
    records = run_backtest(series, _boundary_plan(1), models).records
    assert len(records) == 4 * 3 * 30
    # The model forecasts H + 1 steps from the cutoff o - 1.
    assert steps.calls == [(origin - timedelta(days=1), 31) for origin in BOUNDARY_ORIGINS]
    for origin in BOUNDARY_ORIGINS:
        cutoff = origin - timedelta(days=1)
        naive = by_horizon(records, "naive", origin)
        seasonal = by_horizon(records, "seasonal_naive", origin)
        stepped = by_horizon(records, "steps", origin)
        for horizon in range(1, 31):
            target = origin + timedelta(days=horizon)
            for rows in (naive, seasonal, stepped):
                assert rows.loc[horizon, "target_date"].date() == target
                assert rows.loc[horizon, "data_cutoff"].date() == cutoff
            assert naive.loc[horizon, "prediction"] == value_at(values, cutoff)
            # Step s = h + 1 from the cutoff: y(o + h - 7 * ceil((h + 1) / 7)).
            read = target - timedelta(days=7 * ceil_weeks(horizon + 1))
            assert seasonal.loc[horizon, "prediction"] == value_at(values, read)
            assert stepped.loc[horizon, "prediction"] == float(horizon + 1)
        # Without the delay, h = 7 would read y(o); with it, it reads y(o - 7).
        assert seasonal.loc[7, "prediction"] == value_at(values, origin - timedelta(days=7))


def test_path_failures_are_mapped_through_the_data_delay() -> None:
    """A failure the model reports for step s lands on horizon s - delay."""

    def build(origin: date, steps: int) -> ForecastPath:
        index = target_dates(origin, steps)
        values = np.arange(1, steps + 1, dtype="float64")
        values[2] = np.nan  # step 3
        failure = Failure(FailureStage.FORECAST, "step_three", "planted at step 3")
        return ForecastPath(origin, pd.Series(values, index=index), {3: failure})

    series = series_from(START, linear_values(120))
    for delay, failed_horizon in ((0, 3), (2, 1)):
        plan = single_period_plan(
            date(2023, 3, 1), date(2023, 3, 1), max_horizon=10, data_delay_days=delay
        )
        rows = run_backtest(series, plan, [PathForecaster(build)]).records.set_index("horizon")
        failed = rows[rows["status"] == "failed"]
        assert list(failed.index) == [failed_horizon]
        assert failed.loc[failed_horizon, "failure_code"] == "step_three"
        assert failed.loc[failed_horizon, "failure_stage"] == "forecast"
        assert math.isnan(failed.loc[failed_horizon, "prediction"])
        ok = rows[rows["status"] == "ok"]
        assert [float(v) for v in ok["prediction"]] == [float(h + delay) for h in ok.index]


def test_path_details_are_mapped_through_the_data_delay() -> None:
    """``prediction_raw``, ``estimable`` and ``leverage`` of step s land on horizon s - delay."""

    def build(origin: date, steps: int) -> ForecastPath:
        index = target_dates(origin, steps)
        step = np.arange(1, steps + 1, dtype="float64")
        return ForecastPath(
            origin,
            pd.Series(step, index=index),
            raw_predictions=pd.Series(10.0 * step - 15.0, index=index),
            estimable=np.asarray([s % 3 != 0 for s in range(1, steps + 1)]),
            leverage=step / 100.0,
        )

    series = series_from(START, linear_values(120))
    for delay in (0, 2):
        plan = single_period_plan(
            date(2023, 3, 1), date(2023, 3, 1), max_horizon=8, data_delay_days=delay
        )
        rows = run_backtest(series, plan, [PathForecaster(build)]).records.set_index("horizon")
        steps = [h + delay for h in range(1, 9)]
        assert list(rows["prediction"]) == [float(s) for s in steps]
        assert list(rows["prediction_raw"]) == [10.0 * s - 15.0 for s in steps]
        assert list(rows["estimable"]) == [s % 3 != 0 for s in steps]
        assert list(rows["leverage"]) == [s / 100.0 for s in steps]


# --- 5. Completeness and statuses -------------------------------------------------------

GAP = date(2023, 4, 10)
COMPLETE_END = date(2023, 4, 30)


def _expected_status(model_id: str, origin: date, horizon: int, fail_at: set[date]) -> str:
    target = origin + timedelta(days=horizon)
    if model_id == "failing" and origin in fail_at:
        return "failed"
    if model_id == "naive" and origin == GAP:  # naive reads y(o) at every horizon
        return "failed"
    if target > COMPLETE_END:
        return "beyond_snapshot"
    if target == GAP:
        return "missing_actual"
    return "ok"


def test_every_scheduled_record_exists_and_statuses_partition_them() -> None:
    values: list[float | None] = [float(v) for v in linear_values(120)]
    values[(GAP - START).days] = None
    series = series_from(START, values)
    assert series.end == COMPLETE_END
    fail_at = {date(2023, 3, 9), date(2023, 4, 14)}
    development = Period("development", SELECTION, date(2023, 3, 1), date(2023, 3, 25), 4)
    bridge = Period(
        "bridge",
        DESCRIPTIVE,
        date(2023, 4, 6),
        date(2023, 4, 28),
        2,
        allow_incomplete_horizons=True,
    )
    plan = BacktestPlan(periods=(development, bridge), max_horizon=30, min_history_days=30)
    models: list[Forecaster] = [
        SpyForecaster(),
        NaiveForecaster(),
        FailingForecaster(fail_at=frozenset(fail_at)),
    ]
    result = run_backtest(series, plan, models)
    records = result.records

    period_origins = {
        "development": [date(2023, 3, 1) + timedelta(days=4 * k) for k in range(7)],
        "bridge": [date(2023, 4, 6) + timedelta(days=2 * k) for k in range(12)],
    }
    assert GAP in period_origins["bridge"]
    assert len(records) == (7 + 12) * 3 * 30
    expected: Counter[tuple[str, str, str]] = Counter()
    for period, origins in period_origins.items():
        for origin in origins:
            for model_id in ("spy", "naive", "failing"):
                for horizon in range(1, 31):
                    status = _expected_status(model_id, origin, horizon, fail_at)
                    expected[(period, model_id, status)] += 1
    actual = Counter(zip(records["period"], records["model_id"], records["status"], strict=True))
    assert actual == expected
    assert result.status_counts() == {
        period: {
            model_id: {
                status: count
                for (p, m, status), count in sorted(expected.items())
                if p == period and m == model_id
            }
            for model_id in ("failing", "naive", "spy")
        }
        for period in ("bridge", "development")
    }

    # The four statuses all occur and partition the records.
    assert set(records["status"]) == STATUSES
    assert sum(actual.values()) == len(records)
    assert set(records.loc[records["status"] == "beyond_snapshot", "period"]) == {"bridge"}
    is_ok = records["status"] == "ok"
    is_failed = records["status"] == "failed"
    missing = records["status"] == "missing_actual"
    beyond = records["status"] == "beyond_snapshot"
    assert bool(records.loc[is_ok, ["prediction", "actual"]].notna().all().all())
    assert bool(records.loc[is_failed, "prediction"].isna().all())
    assert bool(records.loc[is_failed, "failure_code"].notna().all())
    assert bool(records.loc[~is_failed, "failure_code"].isna().all())
    assert set(records.loc[missing, "target_date"]) == {pd.Timestamp(GAP)}
    assert bool(records.loc[missing | beyond, "prediction"].notna().all())
    assert bool(records.loc[missing | beyond, "actual"].isna().all())
    assert bool((records.loc[beyond, "target_date"] > pd.Timestamp(COMPLETE_END)).all())
    # Actuals are the observed values of the target dates.
    observed = records.loc[is_ok]
    assert [float(v) for v in observed["actual"]] == [
        value_at(values, ts.date()) for ts in observed["target_date"]
    ]


# --- 6. Failure policy ------------------------------------------------------------------

FAIL_SERIES_DAYS = 120
FAIL_AT = frozenset({date(2023, 3, 5), date(2023, 3, 8)})


@pytest.mark.parametrize("stage", ["fit", "forecast"])
def test_planted_failures_are_recorded_and_isolated(stage: str) -> None:
    series = series_from(START, linear_values(FAIL_SERIES_DAYS))
    plan = single_period_plan(date(2023, 3, 1), date(2023, 3, 10), max_horizon=14)
    failing = FailingForecaster(fail_at=FAIL_AT, stage=stage, code="planted_x", value=7.0)
    with_failing = run_backtest(
        series, plan, [NaiveForecaster(), failing, SeasonalNaiveForecaster()]
    )
    without = run_backtest(series, plan, [NaiveForecaster(), SeasonalNaiveForecaster()])

    # Column dtypes follow the content (the failure columns are str-typed once a run has
    # a failure, ``n_clipped`` turns float once one fit has none), so values are compared.
    others = with_failing.records[with_failing.records["model_id"] != "failing"]
    pd.testing.assert_frame_equal(comparable(others), comparable(without.records))
    other_fits = with_failing.fits[with_failing.fits["model_id"] != "failing"]
    pd.testing.assert_frame_equal(
        comparable(other_fits), comparable(without.fits), check_dtype=False
    )
    assert bool(others["failure_code"].isna().all())

    rows = with_failing.records[with_failing.records["model_id"] == "failing"]
    assert len(rows) == 10 * 14
    planted = rows["origin"].isin([pd.Timestamp(d) for d in FAIL_AT])
    assert int(planted.sum()) == 2 * 14
    assert set(rows.loc[planted, "status"]) == {"failed"}
    assert set(rows.loc[planted, "failure_stage"]) == {stage}
    assert set(rows.loc[planted, "failure_code"]) == {"planted_x"}
    assert all(f"planted {stage} failure at" in m for m in rows.loc[planted, "failure_message"])
    assert bool(rows.loc[planted, ["prediction", "prediction_raw"]].isna().all().all())
    assert set(rows.loc[~planted, "status"]) == {"ok"}
    assert set(rows.loc[~planted, "prediction"]) == {7.0}
    assert bool(rows.loc[~planted, "failure_code"].isna().all())

    fits = with_failing.fits[with_failing.fits["model_id"] == "failing"]
    fit_planted = fits["origin"].isin([pd.Timestamp(d) for d in FAIL_AT])
    assert set(fits.loc[fit_planted, "status"]) == {"failed"}
    assert set(fits.loc[fit_planted, "failure_stage"]) == {stage}
    assert set(fits.loc[fit_planted, "failure_code"]) == {"planted_x"}
    assert set(fits.loc[~fit_planted, "status"]) == {"ok"}


@pytest.mark.parametrize(
    ("model", "stage", "code"),
    [
        (
            ErrorAtStage(fit_error=ForecastError("base error in fit", "base_code")),
            "fit",
            "base_code",
        ),
        (
            ErrorAtStage(forecast_error=ModelFitError("fit error late", "late_code")),
            "forecast",
            "late_code",
        ),
        (ErrorAtStage(fit_error=ModelFitError("default code")), "fit", "fit_error"),
        (ErrorAtStage(forecast_error=ForecastError("default code")), "forecast", "forecast_error"),
    ],
    ids=["forecast-error-in-fit", "fit-error-in-forecast", "fit-default", "forecast-default"],
)
def test_failure_stage_is_where_the_error_was_raised(
    model: ErrorAtStage, stage: str, code: str
) -> None:
    series = series_from(START, linear_values(60))
    plan = single_period_plan(date(2023, 2, 1), date(2023, 2, 2), max_horizon=3)
    result = run_backtest(series, plan, [model])
    assert set(result.records["status"]) == {"failed"}
    assert set(result.records["failure_stage"]) == {stage}
    assert set(result.records["failure_code"]) == {code}
    assert len(result.records) == 2 * 3
    assert set(result.fits["failure_stage"]) == {stage}


@pytest.mark.parametrize(
    "error",
    [
        ValueError,
        ZeroDivisionError,
        ArithmeticError,
        np.linalg.LinAlgError,
        FeatureError,
        InsufficientHistoryError,
        DataQualityError,
        KeyError,
        RuntimeError,
    ],
)
def test_unexpected_exceptions_stop_the_backtest(error: type[Exception]) -> None:
    series = series_from(START, linear_values(60))
    plan = single_period_plan(date(2023, 2, 1), date(2023, 2, 3), max_horizon=3)
    models: list[Forecaster] = [NaiveForecaster(), RaisingForecaster(error=error)]
    with pytest.raises(error, match="planted bug"):
        run_backtest(series, plan, models)
    # The same exception from forecast() (after a successful fit) stops the run too.
    late: list[Forecaster] = [NaiveForecaster(), ErrorAtStage(forecast_error=error("planted bug"))]
    with pytest.raises(error, match="planted bug"):
        run_backtest(series, plan, late)
    with pytest.raises(error, match="planted bug"):
        project(series, [ErrorAtStage(forecast_error=error("planted bug"))], max_horizon=3)


def test_leakage_error_propagates_out_of_the_backtest_and_the_projection() -> None:
    series = series_from(START, linear_values(60))
    plan = single_period_plan(date(2023, 2, 1), date(2023, 2, 3), max_horizon=3)
    with pytest.raises(LeakageError):
        run_backtest(series, plan, [NaiveForecaster(), LeakyForecaster()])
    with pytest.raises(LeakageError):
        project(series, [LeakyForecaster()], max_horizon=3)


@pytest.mark.parametrize("mode", ["shifted_index", "negative", "nan"])
def test_contract_breaking_outputs_are_bugs(mode: str) -> None:
    series = series_from(START, linear_values(60))
    plan = single_period_plan(date(2023, 2, 1), date(2023, 2, 3), max_horizon=3)
    models: list[Forecaster] = [NaiveForecaster(), ContractBreakingForecaster(mode=mode)]
    with pytest.raises(AssertionError, match="broke the forecasting contract"):
        run_backtest(series, plan, models)
    with pytest.raises(AssertionError, match="broke the forecasting contract"):
        project(series, [ContractBreakingForecaster(mode=mode)], max_horizon=3)


def _failure(code: str = "x") -> Failure:
    return Failure(FailureStage.FORECAST, code, "planted")


BROKEN_PATHS: dict[str, PathBuilder] = {
    "prediction_and_failure": lambda o, s: ForecastPath(
        o, pd.Series(1.0, index=target_dates(o, s)), {1: _failure()}
    ),
    "failure_beyond_steps": lambda o, s: ForecastPath(
        o, pd.Series(1.0, index=target_dates(o, s)), {s + 1: _failure()}
    ),
    "failure_at_zero": lambda o, s: ForecastPath(
        o, pd.Series(1.0, index=target_dates(o, s)), {0: _failure()}
    ),
    "wrong_origin": lambda o, s: ForecastPath(
        o - timedelta(days=1), pd.Series(1.0, index=target_dates(o, s))
    ),
    "one_day_short": lambda o, s: ForecastPath(o, pd.Series(1.0, index=target_dates(o, s - 1))),
    "infinite": lambda o, s: ForecastPath(o, pd.Series(np.inf, index=target_dates(o, s))),
    "raw_too_short": lambda o, s: ForecastPath(
        o,
        pd.Series(1.0, index=target_dates(o, s)),
        raw_predictions=pd.Series(1.0, index=target_dates(o, s - 1)),
    ),
    "estimable_too_short": lambda o, s: ForecastPath(
        o, pd.Series(1.0, index=target_dates(o, s)), estimable=np.ones(s - 1, dtype=bool)
    ),
    "leverage_too_short": lambda o, s: ForecastPath(
        o, pd.Series(1.0, index=target_dates(o, s)), leverage=np.ones(s - 1)
    ),
}


@pytest.mark.parametrize("mode", sorted(BROKEN_PATHS))
def test_every_output_contract_violation_stops_the_run(mode: str) -> None:
    series = series_from(START, linear_values(60))
    plan = single_period_plan(date(2023, 2, 1), date(2023, 2, 1), max_horizon=4)
    model = PathForecaster(BROKEN_PATHS[mode])
    assert validate_path(BROKEN_PATHS[mode](date(2023, 2, 1), 4), date(2023, 2, 1), 4) is not None
    with pytest.raises(AssertionError, match="broke the forecasting contract"):
        run_backtest(series, plan, [model])


def test_a_valid_path_with_a_per_horizon_failure_is_accepted() -> None:
    origin = date(2023, 2, 1)

    def build(o: date, s: int) -> ForecastPath:
        values = np.full(s, 2.0)
        values[1] = np.nan
        return ForecastPath(o, pd.Series(values, index=target_dates(o, s)), {2: _failure("h2")})

    assert validate_path(build(origin, 4), origin, 4) is None
    outcome = forecast_at_origin(PathForecaster(build), series_from(START, linear_values(32)), 4)
    assert outcome.failure is None
    assert outcome.origin == origin
    series = series_from(START, linear_values(60))
    plan = single_period_plan(origin, origin, max_horizon=4)
    rows = run_backtest(series, plan, [PathForecaster(build)]).records.set_index("horizon")
    assert list(rows["status"]) == ["ok", "failed", "ok", "ok"]
    assert rows.loc[2, "failure_code"] == "h2"


# --- 7. Missing-data policy through the engine -------------------------------------------

MISSING_DAY = date(2023, 5, 15)
GAP_DAYS = 220  # 2023-01-01 .. 2023-08-08


def _gap_values(seed: int = 3) -> list[float | None]:
    values: list[float | None] = [float(v) for v in noisy_weekly_values(GAP_DAYS, seed)]
    values[(MISSING_DAY - START).days] = None
    return values


def baseline_reads(model_id: str, origin: date, horizon: int) -> list[date]:
    """The days a direct baseline reads for target ``origin + horizon`` (design 2.4)."""
    target = origin + timedelta(days=horizon)
    first = ceil_weeks(horizon)
    weeks = {
        "naive": 0,
        "seasonal_naive": 1,
        "same_weekday_mean_4w": 4,
        "same_weekday_median_5w": 5,
    }
    if model_id == "naive":
        return [origin]
    return [target - timedelta(days=7 * w) for w in range(first, first + weeks[model_id])]


def baseline_reference(values: Sequence[float | None], model_id: str, read: list[date]) -> float:
    observed = [value_at(values, d) for d in read]
    numbers = [v for v in observed if v is not None]
    assert len(numbers) == len(observed)
    if model_id == "same_weekday_mean_4w":
        return sum(numbers) / len(numbers)
    if model_id == "same_weekday_median_5w":
        return float(statistics.median(numbers))
    return numbers[0]


def mase_reference(values: Sequence[float | None], cutoff: date) -> float:
    """Mean ``|y_t - y_(t-7)|`` over pairs with both days observed, through ``cutoff``."""
    differences = []
    for t in range(7, (cutoff - START).days + 1):
        current, week_ago = values[t], values[t - 7]
        if current is not None and week_ago is not None:
            differences.append(abs(current - week_ago))
    return sum(differences) / len(differences)


def test_direct_baselines_fail_exactly_the_horizons_that_read_the_missing_day() -> None:
    values = _gap_values()
    series = series_from(START, values)
    first, last = MISSING_DAY - timedelta(days=2), MISSING_DAY + timedelta(days=35)
    plan = single_period_plan(first, last, max_horizon=30, min_history_days=60)
    records = run_backtest(series, plan, direct_baselines()).records
    failures_seen = Counter()
    for row in records.itertuples(index=False):
        origin, horizon = row.origin.date(), int(row.horizon)
        read = baseline_reads(str(row.model_id), origin, horizon)
        if MISSING_DAY in read:
            failures_seen[row.model_id] += 1
            assert row.status == "failed", (row.model_id, origin, horizon)
            assert (row.failure_stage, row.failure_code) == ("forecast", "unavailable_feature")
            assert math.isnan(row.prediction)
        else:
            target = origin + timedelta(days=horizon)
            expected = "missing_actual" if target == MISSING_DAY else "ok"
            assert row.status == expected, (row.model_id, origin, horizon)
            assert row.prediction == baseline_reference(values, str(row.model_id), read)
    # Each baseline really met the gap (naive only at the origin on the gap itself).
    assert failures_seen["naive"] == 30
    assert min(failures_seen.values()) >= 30


GAP_ORIGINS = tuple(MISSING_DAY + timedelta(days=k) for k in (-1, 0, 13, 27, 28, 29))


def test_regressions_fail_whole_origins_whose_recursion_reads_the_gap() -> None:
    values = _gap_values()
    series = series_from(START, values)
    models = [SeasonalExponentialSmoothing(), *regression_models()]
    plan = origins_plan(GAP_ORIGINS, max_horizon=30, min_history_days=60)
    result = run_backtest(series, plan, models)
    for origin in GAP_ORIGINS:
        reads_gap = origin - timedelta(days=27) <= MISSING_DAY <= origin
        # Training rows t from start + 28 to the origin; a row reads t - 28 .. t.
        rows = [START + timedelta(days=k) for k in range(28, (origin - START).days + 1)]
        dropped = [t for t in rows if t - timedelta(days=28) <= MISSING_DAY <= t]
        kept = [t for t in rows if t not in dropped]
        for model_id in REGRESSION_IDS:
            records = by_horizon(result.records, model_id, origin)
            fit = at_origin(result.fits, origin, model_id).iloc[0]
            assert len(records) == 30
            if reads_gap:
                assert set(records["status"]) == {"failed"}, (model_id, origin)
                assert set(records["failure_stage"]) == {"forecast"}
                assert set(records["failure_code"]) == {"missing_input"}
                assert (fit["status"], fit["failure_code"]) == ("failed", "missing_input")
            else:
                expected = [
                    "missing_actual" if origin + timedelta(days=h) == MISSING_DAY else "ok"
                    for h in range(1, 31)
                ]
                assert list(records["status"]) == expected, (model_id, origin)
                assert fit["status"] == "ok"
            assert fit["n_dropped_rows"] == len(dropped)
            assert fit["n_train_rows"] == len(kept)
            assert fit["train_first"] == kept[0]
            assert fit["train_last"] == kept[-1]
        ets = by_horizon(result.records, "seasonal_exponential_smoothing", origin)
        assert "failed" not in set(ets["status"])  # a missing day only skips an update
    # The cases discriminate: some origins fail, some succeed with rows dropped.
    statuses = {
        origin: set(at_origin(result.records, origin, "atlas_linear_regression")["status"])
        for origin in GAP_ORIGINS
    }
    assert [s == {"failed"} for s in statuses.values()] == [False, True, True, True, False, False]
    late = at_origin(result.fits, GAP_ORIGINS[4], "legacy_linear_regression").iloc[0]
    assert late["n_dropped_rows"] == 29
    assert late["train_last"] == MISSING_DAY - timedelta(days=1)


def test_mase_scale_uses_only_observed_pairs_through_the_cutoff() -> None:
    values = _gap_values()
    series = series_from(START, values)
    origins = (
        MISSING_DAY - timedelta(days=1),
        MISSING_DAY,
        MISSING_DAY + timedelta(days=3),
        MISSING_DAY + timedelta(days=9),
    )
    # The scale covers all data observed by the cutoff (metrics docstring), whatever the
    # training window: the same reference with a 30-day sliding window.
    for delay, window_days in ((0, None), (2, None), (0, 30), (1, 30)):
        plan = origins_plan(
            origins,
            max_horizon=3,
            data_delay_days=delay,
            window_days=window_days,
            min_history_days=60,
        )
        records = run_backtest(series, plan, [SpyForecaster(), NaiveForecaster()]).records
        for origin in origins:
            scales = set(at_origin(records, origin)["mase_scale"])
            expected = mase_reference(values, origin - timedelta(days=delay))
            assert scales == {expected}
    # The reference is discriminating: dropping the gap and differencing by position gives
    # a different number once the gap is inside the history.
    cutoff = MISSING_DAY + timedelta(days=9)
    kept = [v for v in values[: (cutoff - START).days + 1] if v is not None]
    by_position = [abs(kept[i] - kept[i - 7]) for i in range(7, len(kept))]
    assert sum(by_position) / len(by_position) != mase_reference(values, cutoff)


# --- 8. Plan validation -----------------------------------------------------------------

P_SEL = Period("development", SELECTION, date(2023, 3, 1), date(2023, 3, 10))


def _plan(*periods: Period, **kwargs: Any) -> BacktestPlan:
    return BacktestPlan(periods=periods, **({"max_horizon": 7, "min_history_days": 30} | kwargs))


INVALID_PLANS: dict[str, Callable[[], object]] = {
    "targets_overlap": lambda: _plan(
        P_SEL, Period("confirmation", CONFIRMATION, date(2023, 3, 16), date(2023, 3, 20))
    ),
    "confirmation_first": lambda: _plan(
        P_SEL, Period("confirmation", CONFIRMATION, date(2023, 2, 1), date(2023, 2, 10))
    ),
    "duplicate_names": lambda: _plan(
        P_SEL, Period("development", DESCRIPTIVE, date(2023, 3, 1), date(2023, 3, 2))
    ),
    "two_selection": lambda: _plan(
        P_SEL, Period("other", SELECTION, date(2023, 5, 1), date(2023, 5, 2))
    ),
    "two_confirmation": lambda: _plan(
        Period("conf_a", CONFIRMATION, date(2023, 5, 1), date(2023, 5, 2)),
        Period("conf_b", CONFIRMATION, date(2023, 6, 1), date(2023, 6, 2)),
    ),
    "no_periods": lambda: _plan(),
    "stride_0": lambda: Period("development", SELECTION, date(2023, 3, 1), date(2023, 3, 9), 0),
    "stride_367": lambda: Period("dev", SELECTION, date(2023, 3, 1), date(2023, 3, 9), 367),
    "stride_bool": lambda: Period("dev", SELECTION, date(2023, 3, 1), date(2023, 3, 9), True),
    "reversed_period": lambda: Period("dev", SELECTION, date(2023, 3, 9), date(2023, 3, 1)),
    "max_horizon_0": lambda: _plan(P_SEL, max_horizon=0),
    "max_horizon_bool": lambda: _plan(P_SEL, max_horizon=True),
    "max_horizon_367": lambda: _plan(P_SEL, max_horizon=367),
    "min_history_0": lambda: _plan(P_SEL, min_history_days=0),
    "delay_negative": lambda: _plan(P_SEL, data_delay_days=-1),
    "delay_61": lambda: _plan(P_SEL, data_delay_days=61),
    "delay_with_observed_inputs": lambda: _plan(
        P_SEL, data_delay_days=1, observed_input_diagnostic=True
    ),
    "sliding_0": lambda: TrainingWindow(0),
    "sliding_negative": lambda: TrainingWindow(-3),
    "sliding_bool": lambda: TrainingWindow(True),
    "role_unknown": lambda: Period("dev", "tuning", date(2023, 3, 1), date(2023, 3, 2)),  # type: ignore[arg-type]
}
INVALID_NAMES = ("Development", "1dev", "dev-1", "", "a" * 33, "dév", "dev ", "_dev")


@pytest.mark.parametrize("case", sorted(INVALID_PLANS))
def test_invalid_plans_are_refused_at_construction(case: str) -> None:
    with pytest.raises((ConfigError, ValueError)) as caught:
        INVALID_PLANS[case]()
    if case != "role_unknown":  # an unknown enum value is a ValueError from PeriodRole
        assert isinstance(caught.value, ConfigError)


@pytest.mark.parametrize("name", INVALID_NAMES)
def test_invalid_period_names_are_refused(name: str) -> None:
    with pytest.raises(ConfigError, match="invalid period name"):
        Period(name, DESCRIPTIVE, date(2023, 3, 1), date(2023, 3, 2))


def test_valid_plans_at_the_boundaries() -> None:
    for name in ("a", "a" * 32, "dev_2", "legacy_bridge"):
        Period(name, DESCRIPTIVE, date(2023, 3, 1), date(2023, 3, 1))
    # The last selection target may be the confirmation's first origin (disjoint targets).
    confirmation = Period("confirmation", CONFIRMATION, date(2023, 3, 17), date(2023, 3, 20))
    _plan(P_SEL, confirmation)  # selection targets through 03-17, confirmation from 03-18
    with pytest.raises(ConfigError, match="overlap"):
        _plan(P_SEL, Period("confirmation", CONFIRMATION, date(2023, 3, 16), date(2023, 3, 20)))
    # Descriptive periods may overlap anything, including both other roles.
    bridge = Period("bridge", DESCRIPTIVE, date(2023, 2, 20), date(2023, 3, 25))
    plan = _plan(P_SEL, confirmation, bridge, max_horizon=7, min_history_days=30)
    series = series_from(START, linear_values(120))
    result = run_backtest(series, plan, [NaiveForecaster()])
    assert len(result.records) == (10 + 4 + 34) * 7
    assert plan.origins() == tuple(date(2023, 2, 20) + timedelta(days=k) for k in range(34))
    assert TrainingWindow(1).kind == "sliding"
    assert TrainingWindow().kind == "expanding"
    assert BacktestPlan(periods=(P_SEL,), max_horizon=366).max_horizon == 366
    assert BacktestPlan(periods=(P_SEL,), data_delay_days=60).data_delay_days == 60


def _check_refused(series: DemandSeries, plan: BacktestPlan, match: str) -> None:
    spy = SpyForecaster()
    with pytest.raises(ConfigError, match=match):
        run_backtest(series, plan, [spy])
    assert spy.seen == []  # fail closed: nothing was fitted


def test_data_dependent_plan_checks() -> None:
    series = series_from(START, linear_values(120))  # 2023-01-01 .. 2023-04-30
    origin = date(2023, 3, 1)  # 60 days of history through 03-01
    # Minimum history: exactly enough passes, one more day is refused (also with a delay).
    run_backtest(
        series,
        _plan(Period("dev", SELECTION, origin, origin), min_history_days=60),
        [SpyForecaster()],
    )
    _check_refused(
        series, _plan(Period("dev", SELECTION, origin, origin), min_history_days=61), "history"
    )
    _check_refused(
        series,
        _plan(Period("dev", SELECTION, origin, origin), min_history_days=60, data_delay_days=1),
        "history",
    )
    # The last target must be observed unless the period allows incomplete horizons.
    end = series.end
    last_ok = end - timedelta(days=7)
    run_backtest(series, _plan(Period("dev", SELECTION, origin, last_ok)), [SpyForecaster()])
    _check_refused(
        series, _plan(Period("dev", SELECTION, origin, last_ok + timedelta(days=1))), "actuals"
    )
    incomplete = Period(
        "bridge", DESCRIPTIVE, origin, end - timedelta(days=1), allow_incomplete_horizons=True
    )
    run_backtest(series, _plan(incomplete), [SpyForecaster()])
    too_late = Period("bridge", DESCRIPTIVE, origin, end, allow_incomplete_horizons=True)
    _check_refused(series, _plan(too_late), "no target")
    # Models: at least one, with unique ids.
    plan = _plan(Period("dev", SELECTION, origin, origin))
    with pytest.raises(ConfigError, match="at least one model"):
        run_backtest(series, plan, [])
    with pytest.raises(ConfigError, match="unique"):
        run_backtest(series, plan, [NaiveForecaster(), NaiveForecaster()])
    with pytest.raises(ConfigError, match="at least one model"):
        project(series, [], max_horizon=3)


# --- 9. Determinism ---------------------------------------------------------------------

DET_SELECTION = Period("development", SELECTION, date(2023, 5, 1), date(2023, 5, 4))
DET_BRIDGE = Period("bridge", DESCRIPTIVE, date(2023, 5, 3), date(2023, 5, 5))


def _det_plan(*periods: Period) -> BacktestPlan:
    return BacktestPlan(
        periods=periods, max_horizon=14, min_history_days=60, observed_input_diagnostic=True
    )


def _frames(result: BacktestResult) -> dict[str, pd.DataFrame]:
    return {
        "records": result.records,
        "fits": result.fits,
        "coefficients": result.coefficients,
        "observed_inputs": result.observed_inputs,
    }


def test_backtests_are_deterministic_and_independent_of_period_and_model_order() -> None:
    series = series_from(START, noisy_weekly_values(DAYS, 21))
    models = all_models()
    first = run_backtest(series, _det_plan(DET_SELECTION, DET_BRIDGE), models)
    again = run_backtest(series, _det_plan(DET_SELECTION, DET_BRIDGE), models)  # same objects
    fresh = run_backtest(series, _det_plan(DET_SELECTION, DET_BRIDGE), all_models())
    assert len(first.observed_inputs) > 0
    # Coefficients: once per (model, origin), only for the identifiable regression (the
    # legacy design is rank-deficient), the intercept then the 18 features.
    coefficients = first.coefficients
    assert set(coefficients["model_id"]) == {"atlas_linear_regression"}
    assert not coefficients.duplicated(["model_id", "origin", "term"]).any()
    assert Counter(coefficients["origin"]) == {
        pd.Timestamp(date(2023, 5, 1) + timedelta(days=k)): 19 for k in range(5)
    }
    terms = list(coefficients.loc[coefficients["origin"] == pd.Timestamp(2023, 5, 1), "term"])
    assert terms == ["intercept", *atlas_regression_feature_set().names]
    for other in (again, fresh):
        for name, frame in _frames(first).items():
            pd.testing.assert_frame_equal(frame, _frames(other)[name])
            assert csv_bytes(frame) == csv_bytes(_frames(other)[name])

    # Periods listed in another order: the same outputs, byte for byte.
    swapped = run_backtest(series, _det_plan(DET_BRIDGE, DET_SELECTION), all_models())
    for name, frame in _frames(first).items():
        assert csv_bytes(frame) == csv_bytes(_frames(swapped)[name])

    # A plan with only one of the periods: identical records for the shared origins.
    alone = run_backtest(series, _det_plan(DET_SELECTION), all_models())
    shared = first.records[first.records["period"] == "development"].reset_index(drop=True)
    pd.testing.assert_frame_equal(shared, alone.records)
    # The same origin in two periods gives the same forecast.
    columns = [c for c in RECORD_COLUMNS if c != "period"]
    for origin in (date(2023, 5, 3), date(2023, 5, 4)):
        in_dev = at_origin(first.records[first.records["period"] == "development"], origin)
        in_bridge = at_origin(first.records[first.records["period"] == "bridge"], origin)
        pd.testing.assert_frame_equal(in_dev[columns], in_bridge[columns])

    # Models in reverse order: each model's records are unchanged.
    reverse = run_backtest(series, _det_plan(DET_SELECTION, DET_BRIDGE), all_models()[::-1])
    for model_id in ALL_IDS:
        mine = first.records[first.records["model_id"] == model_id].reset_index(drop=True)
        theirs = reverse.records[reverse.records["model_id"] == model_id].reset_index(drop=True)
        pd.testing.assert_frame_equal(mine, theirs)


def test_observed_input_diagnostic_is_separate_and_changes_no_forecast() -> None:
    """The diagnostic reads actuals after the origin, so it must not touch any record."""
    values = noisy_weekly_values(DAYS, 23)
    series = series_from(START, values)
    models = [NaiveForecaster(), *regression_models()]
    first, last = date(2023, 5, 1), date(2023, 5, 3)
    plain = run_backtest(series, single_period_plan(first, last, max_horizon=10), models)
    plan = single_period_plan(first, last, max_horizon=10, observed_input_diagnostic=True)
    with_diagnostic = run_backtest(series, plan, models)
    for name in ("records", "fits", "coefficients"):
        pd.testing.assert_frame_equal(
            _frames(plain)[name], _frames(with_diagnostic)[name], check_dtype=False
        )
    assert plain.observed_inputs.empty
    assert "prediction_observed_inputs" not in with_diagnostic.records.columns

    diagnostic = with_diagnostic.observed_inputs
    assert set(diagnostic["record_type"]) == {"diagnostic_observed_inputs"}
    assert set(diagnostic["model_id"]) == set(REGRESSION_IDS)  # regressions only
    assert len(diagnostic) == 3 * len(REGRESSION_IDS) * 10
    for origin in (first, first + timedelta(days=1), last):
        for model_id in REGRESSION_IDS:
            rows = by_horizon(diagnostic, model_id, origin)
            recursive = by_horizon(with_diagnostic.records, model_id, origin)
            assert list(rows.index) == list(range(1, 11))
            assert [d.date() for d in rows["target_date"]] == [
                origin + timedelta(days=h) for h in range(1, 11)
            ]
            assert list(rows["actual"]) == [
                value_at(values, origin + timedelta(days=h)) for h in range(1, 11)
            ]
            # Step 1 reads only actuals in both procedures: the same raw prediction.
            first_raw = max(float(recursive.loc[1, "prediction_raw"]), 0.0)
            observed = float(rows.loc[1, "prediction_observed_inputs"])
            assert abs(observed - first_raw) <= 1e-9 * max(1.0, first_raw)
            # Later steps read observed demand instead of fed-back predictions.
            later = rows.loc[2:, "prediction_observed_inputs"].to_numpy()
            fed_back = recursive.loc[2:, "prediction_raw"].to_numpy()
            assert not np.allclose(later, fed_back)


# --- 10. In-sample versus out-of-sample -------------------------------------------------


@pytest.mark.parametrize(
    ("window_days", "delay"),
    [(None, 0), (None, 2), (SLIDING, 0), (SLIDING, 1)],
    ids=["expanding", "expanding-delay2", "sliding", "sliding-delay1"],
)
def test_regressions_are_fitted_only_on_rows_through_the_cutoff(
    monkeypatch: pytest.MonkeyPatch, window_days: int | None, delay: int
) -> None:
    calls: list[tuple[date, date, date, float]] = []
    original = atlas.models.fit_linear

    def recording(x: pd.DataFrame, y: pd.Series, **kwargs: Any) -> Any:
        fit = original(x, y, **kwargs)
        calls.append(
            (
                pd.Timestamp(x.index.min()).date(),
                pd.Timestamp(x.index.max()).date(),
                pd.Timestamp(y.index.max()).date(),
                fit.in_sample_mae,
            )
        )
        return fit

    monkeypatch.setattr(atlas.models, "fit_linear", recording)
    series = series_from(START, noisy_weekly_values(DAYS, 13))
    origins = [date(2023, 5, 1) + timedelta(days=k) for k in range(4)]
    plan = single_period_plan(
        origins[0],
        origins[-1],
        max_horizon=5,
        min_history_days=60,
        window_days=window_days,
        data_delay_days=delay,
    )
    result = run_backtest(series, plan, regression_models())

    expected: list[tuple[date, date, date]] = []
    for origin in origins:
        cutoff = origin - timedelta(days=delay)
        first_day = START if window_days is None else cutoff - timedelta(days=window_days - 1)
        expected += [(first_day + timedelta(days=28), cutoff, cutoff)] * 2
    assert [call[:3] for call in calls] == expected  # one fit per model and origin
    fits = result.fits
    assert [d.date() for d in fits["data_cutoff"]] == [cutoff for _, cutoff, _ in expected]
    assert list(fits["train_last"]) == [cutoff for _, cutoff, _ in expected]
    assert list(fits["train_first"]) == [first for first, _, _ in expected]
    assert list(fits["n_train_rows"]) == [(c - f).days + 1 for f, c, _ in expected]
    assert set(fits["n_dropped_rows"]) == {0}
    assert list(fits["in_sample_mae"]) == [call[3] for call in calls]
    # Every forecast target lies after every training target of its fit.
    for row in result.records.itertuples(index=False):
        assert row.target_date > row.data_cutoff
    # In-sample errors are diagnostics of the fit only, never forecast records.
    assert "in_sample_mae" in fits.columns
    assert not any("in_sample" in column for column in result.records.columns)
    projection = project(series.through(origins[0]), regression_models(), max_horizon=3)
    assert not any("in_sample" in column for column in projection.records.columns)


# --- 11. Operational parity with the projection ----------------------------------------

PARITY_COLUMNS = [
    "model_id",
    "origin",
    "data_cutoff",
    "target_date",
    "horizon",
    "prediction",
    "prediction_raw",
    "failure_stage",
    "failure_code",
    "failure_message",
    "estimable",
    "leverage",
]


def _assert_projection_matches(
    backtest: BacktestResult,
    series: DemandSeries,
    origin: date,
    models: list[Forecaster],
    window: TrainingWindow | None = None,
) -> None:
    horizon = backtest.plan.max_horizon
    projection = project(
        series.through(origin), models, max_horizon=horizon, training_window=window
    )
    assert projection.origin == origin
    records = projection.records
    assert "actual" not in records.columns
    assert set(records["record_type"]) == {"future_projection"}
    assert len(records) == len(models) * horizon
    expected = at_origin(backtest.records, origin)
    pd.testing.assert_frame_equal(records[PARITY_COLUMNS], expected[PARITY_COLUMNS])
    ok = expected["status"] == "ok"
    assert set(records.loc[ok, "status"]) <= {"projected"}
    fit_columns = [c for c in FIT_COLUMNS if c != "period"]
    pd.testing.assert_frame_equal(
        projection.fits[fit_columns], at_origin(backtest.fits, origin)[fit_columns]
    )
    pd.testing.assert_frame_equal(projection.coefficients, at_origin(backtest.coefficients, origin))


def test_projection_equals_the_backtest_at_the_same_origin() -> None:
    series = series_from(START, noisy_weekly_values(DAYS, 17))
    origins = (date(2023, 5, 1), date(2023, 5, 20))
    plan = single_period_plan(
        origins[0], origins[1], max_horizon=30, stride_days=19, min_history_days=60
    )
    backtest = run_backtest(series, plan, all_models())
    assert backtest.plan.origins() == origins
    assert bool((backtest.records["status"] == "ok").all())
    for origin in origins:
        _assert_projection_matches(backtest, series, origin, all_models())


def test_projection_equals_a_sliding_window_backtest() -> None:
    series = series_from(START, noisy_weekly_values(DAYS, 19))
    origin = date(2023, 5, 12)
    window = TrainingWindow(SLIDING)
    plan = single_period_plan(
        origin, origin, max_horizon=30, window_days=SLIDING, min_history_days=60
    )
    models = [SeasonalExponentialSmoothing(), *regression_models()]
    backtest = run_backtest(series, plan, models)
    _assert_projection_matches(backtest, series, origin, models, window)
    # And the window matters: an expanding projection differs.
    expanding = project(series.through(origin), regression_models(), max_horizon=30)
    sliding = project(
        series.through(origin), regression_models(), max_horizon=30, training_window=window
    )
    assert not expanding.records["prediction"].equals(sliding.records["prediction"])


def test_projection_labels_and_failures() -> None:
    series = series_from(START, linear_values(60))
    end = series.end
    failing = FailingForecaster(fail_at=frozenset({end}))
    result = project(series, [NaiveForecaster(), failing], max_horizon=5)
    records = result.records
    assert result.origin == end
    assert set(records["record_type"]) == {"future_projection"}
    assert "actual" not in records.columns
    assert [d.date() for d in records["target_date"]] == [
        end + timedelta(days=h) for _ in range(2) for h in range(1, 6)
    ]
    naive = records[records["model_id"] == "naive"]
    assert set(naive["status"]) == {"projected"}
    assert set(naive["prediction"]) == {float(1000 + 59)}
    failed = records[records["model_id"] == "failing"]
    assert set(failed["status"]) == {"failed"}
    assert set(failed["failure_code"]) == {"planted_failure"}
    assert "period" not in result.fits.columns
    assert list(result.fits["status"]) == ["ok", "failed"]


def test_projection_refuses_duplicate_model_ids() -> None:
    """Two models with one id would give two indistinguishable records per horizon."""
    series = series_from(START, linear_values(60))
    with pytest.raises(ConfigError, match="unique"):
        project(series, [NaiveForecaster(), NaiveForecaster()], max_horizon=3)


# --- Integration: the committed legacy-v0 snapshot -----------------------------------------


@pytest.mark.integration
def test_time_travel_and_parity_on_the_snapshot(legacy_tables: dict[str, LoadedTable]) -> None:
    """Around the 2024/2025 boundary, where legacy predictions become non-estimable."""
    series = DemandSeries.from_frame(legacy_tables["daily_total"].frame, name="daily_total")
    split = date(2024, 12, 31)
    perturbed = perturbed_after(series, split, 2026)
    models = [
        NaiveForecaster(),
        SeasonalNaiveForecaster(),
        SameWeekdayMeanForecaster(),
        SameWeekdayMedianForecaster(),
        SeasonalExponentialSmoothing(),
        legacy_linear_regression(),
        atlas_linear_regression(),
    ]
    plan = single_period_plan(
        date(2024, 12, 30), date(2025, 1, 1), max_horizon=30, min_history_days=365
    )
    real = run_backtest(series, plan, models)
    changed = run_backtest(perturbed, plan, models)
    assert bool((real.records["status"] == "ok").all())
    _assert_identical_through(real, changed, split)
    later = date(2025, 1, 1)
    assert changed_horizons(real.records, changed.records, "naive", later) == set(range(1, 31))
    # The legacy design is rank-deficient: estimability is reported, and identical too.
    legacy = at_origin(real.records, split, "legacy_linear_regression")
    assert set(legacy["estimable"]) <= {True, False}
    _assert_projection_matches(real, series, split, models)
