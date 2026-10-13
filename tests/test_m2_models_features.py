"""M2 models (baselines, exponential smoothing, feature sets, ModelSpec) and M1 additions.

Covers ``atlas.models`` (direct baselines, ``SeasonalExponentialSmoothing``, the legacy and
ATLAS regression feature sets, ``ModelSpec`` identity, small regression-model contracts),
the M2 additions to ``atlas.features`` (``CalendarField.YEAR``, ``MonthIndicator``,
``LegacyAnnualCycle``, ``SameWeekdayMedian``) and ``DemandSeries.through`` / ``window``.

Expected values never come from the code under test. They come from:

* closed forms on the linear series ``y(start + k) = 1000 + k`` (hand-derived offsets);
* plain-Python references written in this file: the baseline definitions in the design
  (input dates computed with ``datetime`` arithmetic, ``statistics.median``), an
  ETS(A,N,A) loop written from the ``SeasonalExponentialSmoothing`` docstring, and the
  legacy notebook cell 14 transcribed row by row (row shifts, ``statistics.stdev``);
* values printed by the legacy notebook (cell 14 outputs) for the committed snapshot;
* fingerprints pinned after the definitions were checked by the tests above them.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import random
import statistics
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from atlas.errors import (
    ConfigError,
    DataQualityError,
    FeatureError,
    ForecastError,
    LeakageError,
    ModelFitError,
)
from atlas.events import DEFAULT_EVENT_CALENDAR
from atlas.features import (
    AnnualFourier,
    Calendar,
    CalendarField,
    FeatureContext,
    FeatureSet,
    HolidayIndicator,
    HolidayRelation,
    LegacyAnnualCycle,
    MonthIndicator,
    SameWeekdayMedian,
    Trend,
    default_feature_set,
    forecast_features,
    training_features,
)
from atlas.loader import LoadedTable
from atlas.models import (
    ATLAS_FEATURE_SET_NAME,
    ETS_ALPHAS,
    ETS_GAMMAS,
    LEGACY_FEATURE_SET_NAME,
    FailureStage,
    Forecaster,
    ForecastPath,
    ModelFamily,
    ModelSpec,
    NaiveForecaster,
    Procedure,
    RecursiveLinearForecaster,
    SameWeekdayMeanForecaster,
    SameWeekdayMedianForecaster,
    SeasonalExponentialSmoothing,
    SeasonalNaiveForecaster,
    atlas_linear_regression,
    atlas_regression_feature_set,
    check_steps,
    legacy_feature_set,
    legacy_linear_regression,
    target_dates,
    weeks_back,
)
from atlas.regression import FitMethod
from atlas.series import DemandSeries
from tests.m2_helpers import linear_values, noisy_weekly_values, perturbed_after, series_from

ANCHOR = date(2023, 1, 1)


def ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def daily_series(start: date, values: Sequence[float | None]) -> DemandSeries:
    """A series built with the constructor, so the last day may also be missing."""
    index = pd.date_range(start, periods=len(values), freq="D", name="date")
    data = [math.nan if v is None else float(v) for v in values]
    return DemandSeries("y", pd.Series(data, index=index, dtype="float64", name="y"))


def as_value_map(series: DemandSeries) -> dict[date, float | None]:
    """``date -> value`` (``None`` for a missing observation), from the public copy."""
    out: dict[date, float | None] = {}
    for stamp, value in series.to_series().items():
        out[pd.Timestamp(stamp).date()] = None if math.isnan(value) else float(value)
    return out


# --- Plain-Python baseline reference ------------------------------------------------------

# kind, weeks: the definitions in the design (section 2.4, models 1-4).
BASELINES: dict[str, tuple[Callable[[], Forecaster], str, int]] = {
    "naive": (NaiveForecaster, "naive", 1),
    "seasonal_naive": (SeasonalNaiveForecaster, "seasonal_naive", 1),
    "same_weekday_mean_4w": (SameWeekdayMeanForecaster, "mean", 4),
    "same_weekday_median_5w": (SameWeekdayMedianForecaster, "median", 5),
    "same_weekday_median_4w": (
        lambda: SameWeekdayMedianForecaster("same_weekday_median_4w", weeks=4),
        "median",
        4,
    ),
    "same_weekday_mean_2w": (
        lambda: SameWeekdayMeanForecaster("same_weekday_mean_2w", weeks=2),
        "mean",
        2,
    ),
}


def baseline_inputs(kind: str, weeks: int, origin: date, horizon: int) -> list[date]:
    """The days a direct baseline reads for target ``origin + horizon``."""
    target = origin + timedelta(days=horizon)
    first_week = ceil_div(horizon, 7)
    if kind == "naive":
        days_read = [origin]
    elif kind == "seasonal_naive":
        days_read = [target - timedelta(days=7 * first_week)]
    else:
        days_read = [target - timedelta(days=7 * (first_week + j)) for j in range(weeks)]
    assert all(day <= origin for day in days_read)  # the reference itself never looks ahead
    return days_read


def baseline_reference(
    kind: str, weeks: int, known: dict[date, float | None], origin: date, horizon: int
) -> float | None:
    """Expected forecast, or ``None`` when an input day is missing."""
    values = [known.get(day) for day in baseline_inputs(kind, weeks, origin, horizon)]
    if any(value is None for value in values):
        return None
    observed = [float(v) for v in values if v is not None]
    if kind in ("naive", "seasonal_naive"):
        return observed[0]
    if kind == "mean":
        return math.fsum(observed) / len(observed)
    return float(statistics.median(observed))


# --- Baselines: closed forms on the linear series -----------------------------------------

LINEAR_START = date(2024, 1, 1)
LINEAR = series_from(LINEAR_START, linear_values(120))  # y(start + k) = 1000 + k
ORIGIN_INDEX = 75
ORIGIN = LINEAR_START + timedelta(days=ORIGIN_INDEX)  # 2024-03-16 (inputs cross 29 Feb)
Y_ORIGIN = 1000 + ORIGIN_INDEX


def test_weeks_back_and_target_dates() -> None:
    for horizon in range(1, 61):
        assert weeks_back(horizon) == (horizon + 6) // 7
    assert weeks_back(10, season=5) == 2
    index = target_dates(date(2024, 2, 27), 5)
    assert index.name == "target_date"
    assert [d.date() for d in index] == [
        date(2024, 2, 28),
        date(2024, 2, 29),
        date(2024, 3, 1),
        date(2024, 3, 2),
        date(2024, 3, 3),
    ]
    assert [d.date() for d in target_dates(date(2025, 12, 30), 3)] == [
        date(2025, 12, 31),
        date(2026, 1, 1),
        date(2026, 1, 2),
    ]


@pytest.mark.parametrize("steps", [0, -1, True, 2.0, "3"])
def test_invalid_steps_are_config_errors(steps: Any) -> None:
    with pytest.raises(ConfigError):
        check_steps(steps)
    with pytest.raises(ConfigError):
        NaiveForecaster().fit(LINEAR).forecast(steps)


def _closed_form(model_id: str, horizon: int) -> float:
    """Hand-derived forecasts on y = 1000 + k at origin index 75 (``d = h - 7 ceil(h/7)``).

    Seasonal naive reads ``o + d``; the same-weekday baselines read ``o + d - 7j`` for
    ``j = 0..weeks-1``, so their mean is ``y(o) + d - 7 (weeks - 1) / 2`` and, the values
    being equally spaced, so is their median (the middle value, or the mean of the two
    middle values for an even count).
    """
    d = horizon - 7 * math.ceil(horizon / 7)
    return {
        "naive": float(Y_ORIGIN),
        "seasonal_naive": float(Y_ORIGIN + d),
        "same_weekday_mean_4w": Y_ORIGIN + d - 10.5,
        "same_weekday_median_5w": float(Y_ORIGIN + d - 14),
        "same_weekday_median_4w": Y_ORIGIN + d - 10.5,
        "same_weekday_mean_2w": Y_ORIGIN + d - 3.5,
    }[model_id]


@pytest.mark.parametrize("model_id", sorted(BASELINES))
def test_baselines_on_linear_series_every_horizon(model_id: str) -> None:
    factory, kind, weeks = BASELINES[model_id]
    model = factory()
    assert model.spec.model_id == model_id
    history = LINEAR.through(ORIGIN)
    fitted = model.fit(history)
    assert fitted.origin == ORIGIN
    path = fitted.forecast(30)
    assert path.origin == ORIGIN
    expected_index = pd.date_range(ORIGIN + timedelta(days=1), periods=30, freq="D")
    assert path.predictions.index.equals(expected_index)
    assert path.failures == {}
    known = as_value_map(LINEAR)
    for horizon in range(1, 31):
        target = pd.Timestamp(ORIGIN + timedelta(days=horizon))
        assert path.predictions.loc[target] == _closed_form(model_id, horizon), horizon
        assert path.predictions.loc[target] == baseline_reference(
            kind, weeks, known, ORIGIN, horizon
        )


def test_seasonal_naive_hand_table_beyond_one_week() -> None:
    """y(o + h - 7 ceil(h/7)) on y = 1000 + k, origin y(o) = 1075, read off by hand."""
    path = SeasonalNaiveForecaster().fit(LINEAR.through(ORIGIN)).forecast(30)
    by_horizon = dict(zip(range(1, 31), path.predictions.tolist(), strict=True))
    expected = {
        1: 1069.0,  # o - 6: the same weekday one week before the target
        6: 1074.0,  # o - 1
        7: 1075.0,  # o itself
        8: 1069.0,  # two weeks back: o + 8 - 14
        13: 1074.0,
        14: 1075.0,
        15: 1069.0,  # three weeks back: o + 15 - 21
        21: 1075.0,
        22: 1069.0,
        28: 1075.0,
        29: 1069.0,  # five weeks back: o + 29 - 35
        30: 1070.0,  # o + 30 - 35 = o - 5
    }
    assert {h: by_horizon[h] for h in expected} == expected
    # Never a value after the origin, never older than six days.
    assert all(1069.0 <= v <= 1075.0 for v in by_horizon.values())


NOISY_START = date(2024, 1, 1)
NOISY_VALUES = noisy_weekly_values(150, seed=2026)
NOISY = series_from(NOISY_START, NOISY_VALUES)


@pytest.mark.parametrize("model_id", sorted(BASELINES))
def test_baselines_match_reference_on_noisy_series(model_id: str) -> None:
    """On noisy data the median and the mean differ; each must match its own definition."""
    factory, kind, weeks = BASELINES[model_id]
    origin = NOISY_START + timedelta(days=100)
    path = factory().fit(NOISY.through(origin)).forecast(30)
    known = as_value_map(NOISY)
    expected = [baseline_reference(kind, weeks, known, origin, h) for h in range(1, 31)]
    assert path.predictions.tolist() == expected
    np.testing.assert_array_equal(path.raw_predictions, path.predictions)


def test_median_and_mean_baselines_differ_on_noisy_series() -> None:
    """Guard: the noisy fixture discriminates a median implemented as a mean (and back)."""
    origin = NOISY_START + timedelta(days=100)
    history = NOISY.through(origin)
    median4 = SameWeekdayMedianForecaster("m4", weeks=4).fit(history).forecast(30).predictions
    mean4 = SameWeekdayMeanForecaster("a4", weeks=4).fit(history).forecast(30).predictions
    assert int((median4 != mean4).sum()) >= 25


@settings(max_examples=20, deadline=None)
@given(
    model_id=st.sampled_from(sorted(BASELINES)),
    origin_index=st.integers(min_value=34, max_value=149),
    steps=st.integers(min_value=1, max_value=30),
)
def test_baselines_property_against_reference(model_id: str, origin_index: int, steps: int) -> None:
    factory, kind, weeks = BASELINES[model_id]
    origin = NOISY_START + timedelta(days=origin_index)
    path = factory().fit(NOISY.through(origin)).forecast(steps)
    known = as_value_map(NOISY)
    assert len(path.predictions) == steps
    assert path.predictions.tolist() == [
        baseline_reference(kind, weeks, known, origin, h) for h in range(1, steps + 1)
    ]


def test_baselines_do_not_depend_on_data_after_the_origin() -> None:
    origin = NOISY_START + timedelta(days=90)
    other = perturbed_after(NOISY, origin, seed=7)
    assert other.to_series().iloc[91:].tolist() != NOISY.to_series().iloc[91:].tolist()
    for factory, _, _ in BASELINES.values():
        model = factory()
        a = model.fit(NOISY.through(origin)).forecast(30).predictions
        b = model.fit(other.through(origin)).forecast(30).predictions
        pd.testing.assert_series_equal(a, b)


# --- Baselines: gaps and short histories --------------------------------------------------


@pytest.mark.parametrize("gap_offset", [0, -2, -9, -30, -40])
@pytest.mark.parametrize("model_id", sorted(BASELINES))
def test_missing_day_fails_exactly_the_horizons_that_read_it(
    model_id: str, gap_offset: int
) -> None:
    factory, kind, weeks = BASELINES[model_id]
    origin = NOISY_START + timedelta(days=100)
    gap = origin + timedelta(days=gap_offset)
    values: list[float | None] = [float(v) for v in NOISY_VALUES]
    values[(gap - NOISY_START).days] = None
    gappy = series_from(NOISY_START, values)
    assert gappy.missing_dates == (gap,)

    model = factory()
    path = model.fit(gappy.through(origin)).forecast(30)
    complete = model.fit(NOISY.through(origin)).forecast(30)

    expected_failed = {h for h in range(1, 31) if gap in baseline_inputs(kind, weeks, origin, h)}
    assert set(path.failures) == expected_failed
    for horizon in range(1, 31):
        prediction = path.predictions.iloc[horizon - 1]
        if horizon in expected_failed:
            failure = path.failures[horizon]
            assert failure.code == "unavailable_feature"
            assert failure.stage is FailureStage.FORECAST
            assert math.isnan(prediction)
        else:
            assert prediction == complete.predictions.iloc[horizon - 1]
    np.testing.assert_array_equal(path.raw_predictions, path.predictions)


def test_gap_cases_are_discriminating() -> None:
    """Guard on the parametrisation above: hand-checked failing sets for some cases."""
    origin = NOISY_START + timedelta(days=100)

    def failing(kind: str, weeks: int, offset: int) -> set[int]:
        gap = origin + timedelta(days=offset)
        return {h for h in range(1, 31) if gap in baseline_inputs(kind, weeks, origin, h)}

    assert failing("naive", 1, 0) == set(range(1, 31))
    assert failing("naive", 1, -2) == set()
    assert failing("seasonal_naive", 1, 0) == {7, 14, 21, 28}
    assert failing("seasonal_naive", 1, -2) == {5, 12, 19, 26}
    assert failing("mean", 4, -9) == {5, 12, 19, 26}
    assert failing("mean", 4, -30) == set()
    assert failing("median", 5, -30) == {5, 12, 19, 26}
    assert failing("median", 5, -40) == set()


@pytest.mark.parametrize(
    ("model_id", "fails"),
    [
        ("naive", False),
        ("seasonal_naive", False),
        ("same_weekday_mean_4w", True),
        ("same_weekday_median_5w", True),
    ],
)
def test_insufficient_history_is_recorded_per_horizon(model_id: str, fails: bool) -> None:
    """Ten days of history: the same-weekday baselines need 22+ days for every horizon."""
    factory, kind, weeks = BASELINES[model_id]
    origin = LINEAR_START + timedelta(days=9)
    path = factory().fit(LINEAR.through(origin)).forecast(30)
    assert len(path.predictions) == 30
    if fails:
        assert sorted(path.failures) == list(range(1, 31))
        assert {f.code for f in path.failures.values()} == {"insufficient_history"}
        assert {f.stage for f in path.failures.values()} == {FailureStage.FORECAST}
        assert path.predictions.isna().all()
    else:
        known = as_value_map(LINEAR.through(origin))
        assert path.failures == {}
        assert path.predictions.tolist() == [
            baseline_reference(kind, weeks, known, origin, h) for h in range(1, 31)
        ]


@pytest.mark.parametrize(
    ("model_id", "history_days"), [("seasonal_naive", 4), ("same_weekday_mean_4w", 25)]
)
def test_insufficient_history_fails_only_horizons_reading_before_the_start(
    model_id: str, history_days: int
) -> None:
    factory, kind, weeks = BASELINES[model_id]
    origin = LINEAR_START + timedelta(days=history_days - 1)
    history = LINEAR.through(origin)
    known = as_value_map(history)
    path = factory().fit(history).forecast(14)
    short = {h for h in range(1, 15) if min(baseline_inputs(kind, weeks, origin, h)) < LINEAR_START}
    assert short == {1, 2, 3, 8, 9, 10}  # by hand: d = h - 7 ceil(h/7) in {-6, -5, -4}
    assert set(path.failures) == short
    for horizon in range(1, 15):
        if horizon not in short:
            assert path.predictions.iloc[horizon - 1] == baseline_reference(
                kind, weeks, known, origin, horizon
            )


# --- Seasonal exponential smoothing --------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class EtsReference:
    alpha: float
    gamma: float
    level: float
    seasonal: tuple[float, ...]
    raw: tuple[float, ...]
    sse: float


def ets_reference(
    values: Sequence[float],
    *,
    alphas: Sequence[float] = ETS_ALPHAS,
    gammas: Sequence[float] = ETS_GAMMAS,
    season: int = 7,
    burn_in: int = 28,
    steps: int = 30,
    constrained: bool = True,
) -> EtsReference:
    """ETS(A,N,A) written from the docstring, one grid point and one day at a time.

    ``constrained=False`` drops the ``gamma <= 1 - alpha`` rule (only to show that a test
    case depends on it).
    """
    n = len(values)
    best: tuple[float, float, float, float, list[float]] | None = None
    for alpha in alphas:
        for gamma in gammas:
            if constrained and gamma > 1 - alpha:
                continue
            first = list(values[:season])
            level = sum(first) / season
            seasonal = [v - level for v in first]
            sse = 0.0
            for t in range(season, n):
                observed = values[t]
                if math.isnan(observed):
                    continue  # no update, not counted
                position = t % season  # calendar position, not "observed count"
                error = observed - (level + seasonal[position])
                if t >= season + burn_in:
                    sse += error * error
                level = level + alpha * error
                seasonal[position] = seasonal[position] + gamma * error
            if best is None or sse < best[0]:  # strict: first minimum wins ties
                best = (sse, alpha, gamma, level, list(seasonal))
    assert best is not None
    sse, alpha, gamma, level, seasonal = best
    raw = tuple(level + seasonal[(n - 1 + h) % season] for h in range(1, steps + 1))
    return EtsReference(alpha, gamma, level, tuple(seasonal), raw, sse)


def random_walk_weekly(count: int, seed: int) -> list[int]:
    """A drifting level (random walk) plus a fixed weekly pattern and noise: interior optimum."""
    rng = random.Random(seed)
    pattern = [rng.randint(-1500, 1500) for _ in range(7)]
    level = 6000.0
    out = []
    for k in range(count):
        level += rng.gauss(0, 250)
        out.append(max(0, round(level + pattern[k % 7] + rng.gauss(0, 150))))
    return out


def assert_matches_reference(model: SeasonalExponentialSmoothing, series: DemandSeries) -> None:
    values = series.to_series().tolist()
    expected = ets_reference(
        values,
        alphas=model.alphas,
        gammas=model.gammas,
        season=model.season_days,
        burn_in=model.burn_in_days,
    )
    fitted = model.fit(series)
    assert (fitted.alpha, fitted.gamma) == (expected.alpha, expected.gamma)
    assert fitted.level == pytest.approx(expected.level, rel=1e-12, abs=1e-9)
    np.testing.assert_allclose(fitted.seasonal, expected.seasonal, rtol=0, atol=1e-9)
    path = fitted.forecast(30)
    assert path.origin == series.end
    assert path.failures == {}
    assert path.predictions.index.equals(
        pd.date_range(series.end + timedelta(days=1), periods=30, freq="D")
    )
    assert path.raw_predictions is not None
    np.testing.assert_allclose(path.raw_predictions, expected.raw, rtol=0, atol=1e-9)
    np.testing.assert_allclose(
        path.predictions, [max(v, 0.0) for v in expected.raw], rtol=0, atol=1e-9
    )
    assert path.n_clipped == sum(v < 0 for v in expected.raw)


@pytest.mark.parametrize("seed", [0, 4])
def test_ets_matches_reference_and_burn_in_matters(seed: int) -> None:
    values = random_walk_weekly(150, seed)
    series = series_from(date(2024, 1, 1), values)
    floats = [float(v) for v in values]
    with_burn_in = ets_reference(floats)
    without = ets_reference(floats, burn_in=0)
    # Guard: these seeds make the selection depend on excluding the 28 warm-up days, and
    # the optimum is not the first grid point.
    assert (with_burn_in.alpha, with_burn_in.gamma) != (without.alpha, without.gamma)
    assert (with_burn_in.alpha, with_burn_in.gamma) != (ETS_ALPHAS[0], ETS_GAMMAS[0])
    assert_matches_reference(SeasonalExponentialSmoothing(), series)


@settings(max_examples=15, deadline=None)
@given(
    seed=st.integers(min_value=0, max_value=10**6),
    count=st.integers(min_value=36, max_value=160),
    noisy_walk=st.booleans(),
)
def test_ets_property_against_reference(seed: int, count: int, noisy_walk: bool) -> None:
    values = random_walk_weekly(count, seed) if noisy_walk else noisy_weekly_values(count, seed)
    assert_matches_reference(SeasonalExponentialSmoothing(), series_from(date(2023, 5, 1), values))


def test_ets_other_season_and_burn_in() -> None:
    model = SeasonalExponentialSmoothing("ets5", season_days=5, burn_in_days=10)
    values = random_walk_weekly(90, seed=11)
    assert_matches_reference(model, series_from(date(2024, 1, 1), values))


def test_ets_missing_values_skip_updates_at_their_calendar_position() -> None:
    values: list[float | None] = [float(v) for v in random_walk_weekly(120, seed=4)]
    for index in (9, 40, 41, 77, 119):  # includes the origin itself
        values[index] = None
    series = daily_series(date(2024, 1, 1), values)
    assert series.end == date(2024, 4, 29)
    assert len(series.missing_dates) == 5
    assert_matches_reference(SeasonalExponentialSmoothing(), series)
    fitted = SeasonalExponentialSmoothing().fit(series)
    assert fitted.diagnostics.n_dropped_rows == 5
    assert fitted.diagnostics.n_train_rows == (120 - 35) - 4  # 40, 41, 77, 119 after warm-up
    assert fitted.diagnostics.train_first == date(2024, 1, 1)
    assert fitted.diagnostics.train_last == date(2024, 4, 29)


def test_ets_exact_season_ties_go_to_the_first_grid_point() -> None:
    """A purely periodic series with an integral first-week mean: every one-step error is 0.

    All grid points tie (SSE 0), so the first one wins, the state never moves and the
    forecast is the periodic continuation.
    """
    week = [100, 200, 300, 400, 500, 600, 700]  # mean 400, seasonal terms -300..300
    values = week * 9 + week[:3]  # 66 days, ends on position 2
    fitted = SeasonalExponentialSmoothing().fit(series_from(date(2024, 1, 1), values))
    assert (fitted.alpha, fitted.gamma) == (0.05, 0.01)
    assert fitted.level == 400.0
    assert fitted.seasonal == (-300.0, -200.0, -100.0, 0.0, 100.0, 200.0, 300.0)
    path = fitted.forecast(10)
    assert path.predictions.tolist() == [float(v) for v in (week[3:] + week[:6])]
    assert path.n_clipped == 0


def test_ets_clips_negative_forecasts_and_counts_them() -> None:
    values = [5000, 0, 0, 0, 0, 0, 0] * 6 + [0] * 10 + [3000, 0, 0, 0, 0, 0, 0] * 2
    series = series_from(date(2024, 1, 1), values)
    expected = ets_reference([float(v) for v in values], steps=30)
    assert sum(v < 0 for v in expected.raw) > 0  # guard: the case really clips
    assert_matches_reference(SeasonalExponentialSmoothing(), series)
    path = SeasonalExponentialSmoothing().fit(series).forecast(30)
    assert (path.predictions >= 0).all()


def test_ets_respects_gamma_at_most_one_minus_alpha() -> None:
    model = SeasonalExponentialSmoothing("ets", alphas=(0.5, 0.9), gammas=(0.05, 0.2, 0.5))
    values = random_walk_weekly(120, seed=6)
    series = series_from(date(2024, 1, 1), values)
    unconstrained = ets_reference(
        [float(v) for v in values], alphas=model.alphas, gammas=model.gammas, constrained=False
    )
    # Guard: without the rule this series would select an excluded point.
    assert (unconstrained.alpha, unconstrained.gamma) == (0.9, 0.2)
    assert_matches_reference(model, series)
    fitted = model.fit(series)
    assert (fitted.alpha, fitted.gamma) not in {(0.9, 0.2), (0.9, 0.5)}


def test_ets_minimum_history_and_error_codes() -> None:
    start = date(2024, 1, 1)
    model = SeasonalExponentialSmoothing()
    values = random_walk_weekly(36, seed=3)
    model.fit(series_from(start, values))  # 7 + 28 + 1 days: just enough
    with pytest.raises(ModelFitError) as short:
        model.fit(series_from(start, values[:35]))
    assert short.value.code == "insufficient_history"
    assert isinstance(short.value, ForecastError)

    no_burn_in = SeasonalExponentialSmoothing("ets0", burn_in_days=0)
    no_burn_in.fit(series_from(start, values[:8]))
    with pytest.raises(ModelFitError) as shorter:
        no_burn_in.fit(series_from(start, values[:7]))
    assert shorter.value.code == "insufficient_history"

    incomplete: list[float | None] = [float(v) for v in values]
    incomplete[3] = None
    with pytest.raises(ModelFitError) as missing:
        model.fit(series_from(start, incomplete))
    assert missing.value.code == "missing_input"
    later_gap = [float(v) for v in values] + [None, 5000.0]
    model.fit(series_from(start, later_gap))  # a gap after the first week is fine


@pytest.mark.parametrize(
    "kwargs",
    [
        {"season_days": 1},
        {"season_days": 367},
        {"season_days": True},
        {"burn_in_days": -1},
        {"burn_in_days": True},
        {"alphas": ()},
        {"gammas": ()},
        {"alphas": (0.0,)},
        {"alphas": (0.5,), "gammas": (0.0,)},
        {"alphas": (-0.2,), "gammas": (0.1,)},
        {"alphas": (0.9,), "gammas": (0.2,)},  # no point with gamma <= 1 - alpha
    ],
)
def test_ets_invalid_configuration(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ConfigError):
        SeasonalExponentialSmoothing(**kwargs)


@pytest.mark.parametrize(
    "kwargs", [{"alphas": (0.5, 1.5)}, {"alphas": (0.5,), "gammas": (0.1, 1.2)}]
)
def test_ets_grid_values_outside_unit_interval_are_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ConfigError):
        SeasonalExponentialSmoothing(**kwargs)


def test_ets_spec() -> None:
    spec = SeasonalExponentialSmoothing().spec
    assert spec.model_id == "seasonal_exponential_smoothing"
    assert spec.family is ModelFamily.BASELINE
    assert spec.params == {
        "season_days": 7,
        "burn_in_days": 28,
        "alphas": [0.05, 0.1, 0.2, 0.3, 0.5, 0.7],
        "gammas": [0.01, 0.05, 0.1, 0.2, 0.3],
    }
    other = SeasonalExponentialSmoothing(alphas=(0.1, 0.2)).spec
    assert other.fingerprint() != spec.fingerprint()


# --- Feature additions (M1) ----------------------------------------------------------------

FEATURE_START = date(2023, 1, 1)
FEATURE_DAYS = 1096  # 2023-01-01 .. 2025-12-31, includes 2024-02-29
FEATURE_VALUES: list[float | None] = [float(v) for v in noisy_weekly_values(FEATURE_DAYS, 99)]
GAP_DAYS = (date(2024, 2, 29), date(2025, 6, 10))
for _gap in GAP_DAYS:
    FEATURE_VALUES[(_gap - FEATURE_START).days] = None
FEATURE_SERIES = series_from(FEATURE_START, FEATURE_VALUES)
ALL_DATES = [FEATURE_START + timedelta(days=k) for k in range(FEATURE_DAYS)]


def calendar_matrix(features: Sequence[Any]) -> pd.DataFrame:
    """Rows for every day of 2023-2025 (calendar features read no demand)."""
    matrix = training_features(
        FEATURE_SERIES,
        origin=ALL_DATES[-1],
        feature_set=FeatureSet(tuple(features)),
        allow_incomplete=True,
    )
    assert [d.date() for d in matrix.frame.index] == ALL_DATES
    return matrix.frame


def test_calendar_year_field() -> None:
    assert CalendarField.YEAR.value == "year"
    assert Calendar("year").field is CalendarField.YEAR
    frame = calendar_matrix([Calendar(CalendarField.YEAR)])
    assert list(frame.columns) == ["year"]
    assert frame["year"].tolist() == [float(d.year) for d in ALL_DATES]
    assert frame["year"].dtype == np.float64


def test_month_indicators() -> None:
    frame = calendar_matrix([MonthIndicator(m) for m in range(1, 13)])
    assert list(frame.columns) == [f"is_month_{m}" for m in range(1, 13)]
    for month in range(1, 13):
        expected = [1.0 if d.month == month else 0.0 for d in ALL_DATES]
        assert frame[f"is_month_{month}"].tolist() == expected
    assert (frame.sum(axis=1) == 1.0).all()
    assert frame.loc[pd.Timestamp("2024-02-29"), "is_month_2"] == 1.0
    assert frame.loc[pd.Timestamp("2024-03-01"), "is_month_3"] == 1.0


@pytest.mark.parametrize("month", [0, 13, True, 1.0, "1"])
def test_month_indicator_rejects_invalid_months(month: Any) -> None:
    with pytest.raises(FeatureError):
        MonthIndicator(month)


def test_legacy_annual_cycle_matches_notebook_formula() -> None:
    frame = calendar_matrix([LegacyAnnualCycle("sin"), LegacyAnnualCycle("cos")])
    doy = [d.timetuple().tm_yday for d in ALL_DATES]
    np.testing.assert_allclose(
        frame["legacy_annual_sin"], [math.sin(2 * math.pi * k / 365.25) for k in doy], atol=1e-12
    )
    np.testing.assert_allclose(
        frame["legacy_annual_cos"], [math.cos(2 * math.pi * k / 365.25) for k in doy], atol=1e-12
    )
    row = frame.loc
    # 31 Dec of a leap year is day 366: the angle passes a full turn, so sin > 0 there,
    # while 31 Dec of a common year (day 365) is still just below a full turn.
    leap_end = row[pd.Timestamp("2024-12-31"), "legacy_annual_sin"]
    assert leap_end == pytest.approx(math.sin(2 * math.pi * 0.75 / 365.25), abs=1e-12)
    assert leap_end > 0
    assert row[pd.Timestamp("2023-12-31"), "legacy_annual_sin"] < 0
    # Day-of-year based (not year fraction): 29 Feb 2024 and 1 Mar 2023 are both day 60.
    assert (
        row[pd.Timestamp("2024-02-29"), "legacy_annual_sin"]
        == row[pd.Timestamp("2023-03-01"), "legacy_annual_sin"]
    )
    assert (
        row[pd.Timestamp("2024-03-01"), "legacy_annual_cos"]
        != row[pd.Timestamp("2023-03-01"), "legacy_annual_cos"]
    )
    fourier = calendar_matrix([AnnualFourier(1, "sin")])["year_sin_1"]
    assert not np.allclose(fourier, frame["legacy_annual_sin"], atol=1e-6)
    with pytest.raises(FeatureError):
        LegacyAnnualCycle("tan")


def test_new_feature_specs_and_names() -> None:
    assert SameWeekdayMedian(5).name == "same_weekday_median_5w"
    assert SameWeekdayMedian(3, first_week=2).name == "same_weekday_median_3w_from_2w"
    assert SameWeekdayMedian(5).offsets == (7, 14, 21, 28, 35)
    assert SameWeekdayMedian(3, first_week=2).offsets == (14, 21, 28)
    assert SameWeekdayMedian(5).spec() == {
        "type": "SameWeekdayMedian",
        "name": "same_weekday_median_5w",
        "weeks": 5,
        "first_week": 1,
    }
    assert MonthIndicator(4).spec() == {"type": "MonthIndicator", "name": "is_month_4", "month": 4}
    assert LegacyAnnualCycle("cos").spec() == {
        "type": "LegacyAnnualCycle",
        "name": "legacy_annual_cos",
        "function": "cos",
    }
    assert Calendar(CalendarField.YEAR).offsets == ()
    assert MonthIndicator(1).offsets == ()
    assert LegacyAnnualCycle("sin").offsets == ()
    for bad in ({"weeks": 0}, {"weeks": 3, "first_week": 0}, {"weeks": True}):
        with pytest.raises(FeatureError):
            SameWeekdayMedian(**bad)


MEDIANS = (
    SameWeekdayMedian(5),
    SameWeekdayMedian(4),
    SameWeekdayMedian(1),
    SameWeekdayMedian(3, first_week=2),
)


def median_reference(
    known: dict[date, float | None], target: date, weeks: int, first_week: int
) -> float:
    values = [
        known.get(target - timedelta(days=7 * w)) for w in range(first_week, first_week + weeks)
    ]
    if any(v is None for v in values):
        return math.nan
    return float(statistics.median([float(v) for v in values if v is not None]))


def test_same_weekday_median_matches_reference_over_2023_2025() -> None:
    matrix = training_features(
        FEATURE_SERIES,
        origin=ALL_DATES[-1],
        horizon=1,
        feature_set=FeatureSet(MEDIANS),
        allow_incomplete=True,
    )
    known = as_value_map(FEATURE_SERIES)
    assert matrix.frame.index[0].date() == FEATURE_START + timedelta(days=35)
    n_nan = 0
    for stamp, row in matrix.frame.iterrows():
        target = pd.Timestamp(stamp).date()
        for feature in MEDIANS:
            expected = median_reference(known, target, feature.weeks, feature.first_week)
            actual = row[feature.name]
            if math.isnan(expected):
                n_nan += 1
                assert math.isnan(actual), (target, feature.name)
            else:
                assert actual == expected, (target, feature.name)
    # Each gap is read by 5 + 4 + 1 + 3 rows (one per offset of each feature).
    assert n_nan == 2 * (5 + 4 + 1 + 3)
    # Hand check across the leap day: the 5-week median for 2024-03-07 reads 2024-02-29
    # (missing), so it is NaN; for 2024-03-06 it reads 02-28, 02-21, 02-14, 02-07, 01-31.
    assert math.isnan(matrix.frame.loc[pd.Timestamp("2024-03-07"), "same_weekday_median_5w"])
    reads = [date(2024, 2, 28), date(2024, 2, 21), date(2024, 2, 14), date(2024, 2, 7)]
    observed = [known[d] for d in [*reads, date(2024, 1, 31)]]
    assert None not in observed
    expected_0306 = statistics.median([float(v) for v in observed if v is not None])
    assert matrix.frame.loc[pd.Timestamp("2024-03-06"), "same_weekday_median_5w"] == expected_0306


def test_same_weekday_median_even_count_is_mean_of_middle_values() -> None:
    week_values = [10.0, 40.0, 20.0, 1000.0]  # sorted: 10, 20, 40, 1000 -> (20 + 40) / 2
    start = date(2024, 1, 1)
    values: list[float | None] = [0.0] * 29
    for j, v in enumerate(week_values):  # y[t - 7(j+1)] for target t = start + 28
        values[28 - 7 * (j + 1)] = v
    series = series_from(start, values)
    origin = start + timedelta(days=27)
    matrix = forecast_features(
        series, origin=origin, horizons=(1,), feature_set=FeatureSet((SameWeekdayMedian(4),))
    )
    assert matrix.frame.iloc[0, 0] == 30.0


def test_same_weekday_median_declared_leakage_guard() -> None:
    origin = date(2024, 6, 30)
    known = as_value_map(FEATURE_SERIES)
    one_week = FeatureSet((SameWeekdayMedian(5),))
    two_weeks = FeatureSet((SameWeekdayMedian(5, first_week=2),))
    matrix = forecast_features(
        FEATURE_SERIES, origin=origin, horizons=tuple(range(1, 8)), feature_set=one_week
    )
    for horizon in range(1, 8):
        target = origin + timedelta(days=horizon)
        assert matrix.frame.iloc[horizon - 1, 0] == median_reference(known, target, 5, 1)
    with pytest.raises(LeakageError):
        forecast_features(FEATURE_SERIES, origin=origin, horizons=(8,), feature_set=one_week)
    forecast_features(FEATURE_SERIES, origin=origin, horizons=(14,), feature_set=two_weeks)
    with pytest.raises(LeakageError):
        forecast_features(FEATURE_SERIES, origin=origin, horizons=(15,), feature_set=two_weeks)


@pytest.mark.parametrize(("first_week", "horizon"), [(1, 8), (1, 30), (2, 15), (3, 22)])
def test_same_weekday_median_runtime_leakage_guard(first_week: int, horizon: int) -> None:
    """The read itself is refused, whatever the feature declares and whatever data is held.

    The context holds the *whole* series (later days included); the row's cutoff is the
    origin, so a row at horizon > 7 * first_week must be refused by ``FeatureContext.read``.
    """
    origin = date(2024, 6, 30)
    target = pd.DatetimeIndex([pd.Timestamp(origin + timedelta(days=horizon))])

    def context(cutoff: date) -> FeatureContext:
        return FeatureContext(
            targets=target,
            demand=FEATURE_SERIES.to_series(),
            cutoffs=pd.DatetimeIndex([pd.Timestamp(cutoff)]),
            origins=(origin,),
            events=DEFAULT_EVENT_CALENDAR,
        )

    feature = SameWeekdayMedian(4, first_week=first_week)
    with pytest.raises(LeakageError):
        feature.compute(context(origin))
    # Positive control: with the cutoff at the latest day it reads, the same row computes.
    latest_read = target[0].date() - timedelta(days=7 * first_week)
    value = feature.compute(context(latest_read))
    known = as_value_map(FEATURE_SERIES)
    assert value.tolist() == [median_reference(known, target[0].date(), 4, first_week)]


def test_default_m1_feature_set_is_unchanged() -> None:
    default = default_feature_set()
    assert default.fingerprint() == (
        "f69f30a2674b71922520b7ab6b87fddffd4a5038f0fb9a5f992209f4fabc8f33"
    )
    names = set(default.names)
    added = {"year", *(f"is_month_{m}" for m in range(1, 13))}
    assert not names & added
    assert not any(n.startswith(("legacy_annual_", "same_weekday_median")) for n in names)
    assert {"is_month_start", "is_month_end", "same_weekday_mean_4w"} <= names


# --- Legacy and ATLAS regression feature sets -----------------------------------------------

NOTEBOOK_COLUMNS = (
    "trend_index",
    "year",
    "day_of_year",
    "is_weekend",
    "annual_sin",
    "annual_cos",
    "lag_1",
    "lag_7",
    "lag_14",
    "lag_28",
    "rolling_mean_7",
    "rolling_mean_28",
    "rolling_std_28",
    *(f"weekday_{k}" for k in range(7)),  # pandas dayofweek: Monday = 0 .. Sunday = 6
    *(f"month_{m}" for m in range(1, 13)),
)
LEGACY_NAMES = (
    "trend_days",
    "year",
    "day_of_year",
    "is_weekend",
    "legacy_annual_sin",
    "legacy_annual_cos",
    "lag_1",
    "lag_7",
    "lag_14",
    "lag_28",
    "rolling_mean_7",
    "rolling_mean_28",
    "rolling_std_28",
    *(f"is_iso_weekday_{k}" for k in range(1, 8)),
    *(f"is_month_{m}" for m in range(1, 13)),
)
ATLAS_NAMES = (
    "is_iso_weekday_2",
    "is_iso_weekday_3",
    "is_iso_weekday_4",
    "is_iso_weekday_5",
    "is_iso_weekday_6",
    "is_iso_weekday_7",
    "year_sin_1",
    "year_cos_1",
    "year_sin_2",
    "year_cos_2",
    "trend_days",
    "is_observed_holiday",
    "is_day_before_holiday",
    "is_day_after_holiday",
    "lag_1",
    "lag_7",
    "same_weekday_mean_4w",
    "rolling_mean_28",
)
LEGACY_FINGERPRINT = "09665f4190a064af9661486af9f451fed295ef235f499111ffa8c15be56c045c"
ATLAS_FINGERPRINT = "9e5d3e14cf7eb2aae0692c4ce6fe9d507a9374cf762fd6500c6bfd9e3299f6af"


def notebook_cell_14(dates: Sequence[date], counts: Sequence[float]) -> pd.DataFrame:
    """Plain-Python transcription of notebook cell 14 on a gap-free, sorted series.

    Row shifts as in the notebook (``shift(k)``, ``shift(1).rolling(w)``), trend counted
    from the first date, dummies in ``get_dummies`` order; the first 28 rows (incomplete
    lags) are dropped as ``dropna`` does.
    """
    first = dates[0]
    rows = []
    for i in range(28, len(dates)):
        day = dates[i]
        weekday = day.weekday()
        doy = day.timetuple().tm_yday
        previous_28 = list(counts[i - 28 : i])
        row: list[float] = [
            float((day - first).days),
            float(day.year),
            float(doy),
            float(weekday >= 5),
            math.sin(2 * math.pi * doy / 365.25),
            math.cos(2 * math.pi * doy / 365.25),
            counts[i - 1],
            counts[i - 7],
            counts[i - 14],
            counts[i - 28],
            math.fsum(counts[i - 7 : i]) / 7,
            math.fsum(previous_28) / 28,
            statistics.stdev(previous_28),
            *(float(weekday == k) for k in range(7)),
            *(float(day.month == m) for m in range(1, 13)),
        ]
        rows.append(row)
    index = pd.DatetimeIndex([pd.Timestamp(d) for d in dates[28:]])
    return pd.DataFrame(rows, index=index, columns=list(NOTEBOOK_COLUMNS))


EXACT_COLUMNS = [c for c in NOTEBOOK_COLUMNS if c not in ("annual_sin", "annual_cos")]
EXACT_COLUMNS.remove("rolling_std_28")


def assert_legacy_matches_notebook(series: DemandSeries) -> pd.DataFrame:
    values = series.to_series()
    dates = [pd.Timestamp(d).date() for d in values.index]
    reference = notebook_cell_14(dates, values.tolist())
    matrix = training_features(
        series, origin=series.end, horizon=1, feature_set=legacy_feature_set()
    )
    frame = matrix.frame
    assert tuple(frame.columns) == LEGACY_NAMES
    assert frame.index.equals(reference.index)
    for ours, theirs in zip(LEGACY_NAMES, NOTEBOOK_COLUMNS, strict=True):
        if theirs in EXACT_COLUMNS:
            assert frame[ours].tolist() == reference[theirs].tolist(), ours
        else:
            np.testing.assert_allclose(
                frame[ours], reference[theirs], rtol=1e-12, atol=1e-12, err_msg=ours
            )
    assert matrix.target is not None
    assert matrix.target.tolist() == values.iloc[28:].tolist()
    return frame


def test_legacy_feature_set_equals_notebook_cell_14_on_noisy_series() -> None:
    values = noisy_weekly_values(800, seed=14)  # 2023-01-01 .. 2025-03-10: leap year inside
    series = series_from(ANCHOR, values)
    frame = assert_legacy_matches_notebook(series)
    assert len(frame) == 800 - 28
    assert frame.index[0].date() == date(2023, 1, 29)


def test_legacy_trend_anchor_is_fixed_not_the_window_start() -> None:
    """The trend counts from 2023-01-01 even when the history starts later (design 6.10)."""
    start = date(2023, 3, 5)
    series = series_from(start, noisy_weekly_values(60, seed=3))
    frame = training_features(series, origin=series.end, feature_set=legacy_feature_set()).frame
    first = frame.index[0].date()
    assert first == start + timedelta(days=28)
    assert frame["trend_days"].iloc[0] == float((first - ANCHOR).days)
    moved = legacy_feature_set(date(2024, 1, 1))
    assert moved.fingerprint() != legacy_feature_set().fingerprint()


def test_legacy_feature_set_definition_and_fingerprint() -> None:
    features = legacy_feature_set()
    assert features.name == LEGACY_FEATURE_SET_NAME == "legacy-notebook-cell-14"
    assert features.names == LEGACY_NAMES
    assert len(features) == 32
    assert features.max_offset == 28
    assert features.min_offset == 1
    trend = features.features[0]
    assert isinstance(trend, Trend)
    assert trend.anchor == ANCHOR
    assert features.fingerprint() == LEGACY_FINGERPRINT


def test_atlas_regression_feature_set_definition_and_fingerprint() -> None:
    features = atlas_regression_feature_set()
    assert features.name == ATLAS_FEATURE_SET_NAME == "atlas-m2-regression-v1"
    assert features.names == ATLAS_NAMES
    assert len(features) == 18
    assert features.max_offset == 28
    assert features.min_offset == 1
    assert all(max(f.offsets, default=0) <= 28 for f in features)
    holidays = [f for f in features if isinstance(f, HolidayIndicator)]
    assert [h.relation for h in holidays] == [
        HolidayRelation.OBSERVED,
        HolidayRelation.EVE,
        HolidayRelation.AFTER,
    ]
    assert HolidayIndicator(HolidayRelation.ON).name not in features.names
    assert "is_iso_weekday_1" not in features.names  # Monday is the reference level
    trends = [f for f in features if isinstance(f, Trend)]
    assert [t.anchor for t in trends] == [ANCHOR]
    assert features.fingerprint() == ATLAS_FINGERPRINT
    assert len({ATLAS_FINGERPRINT, LEGACY_FINGERPRINT, default_feature_set().fingerprint()}) == 3


@pytest.mark.integration
def test_legacy_features_on_snapshot_match_notebook(
    legacy_tables: dict[str, LoadedTable],
) -> None:
    """Committed legacy-v0 daily_total: cell-14 reference and the values the notebook printed."""
    series = DemandSeries.from_frame(legacy_tables["daily_total"].frame, name="daily_total")
    frame = assert_legacy_matches_notebook(series)
    assert len(frame) == 1249  # "Complete model rows: 1249"
    assert int((frame.index < pd.Timestamp("2026-04-02")).sum()) == 1159  # "Training rows"
    printed = {  # date: trend, lag_1, lag_7, lag_14, lag_28, rm7, rm28, rstd28 (2 decimals)
        "2023-01-29": (28, 6936, 6449, 6835, 6209, 7886.29, 7797.82, 850.29),
        "2023-01-30": (29, 6998, 7987, 7053, 6680, 7964.71, 7826.00, 807.69),
        "2023-01-31": (30, 8455, 8851, 9287, 8255, 8031.57, 7889.39, 783.72),
        "2023-02-01": (31, 8381, 7863, 8530, 8462, 7964.43, 7893.89, 786.25),
        "2023-02-02": (32, 8687, 8940, 7986, 8414, 8082.14, 7901.93, 793.39),
        "2026-06-26": (1272, 10955, 10239, 10998, 11629, 10915.00, 11172.64, 634.09),
        "2026-06-27": (1273, 11031, 10414, 10184, 10085, 11028.14, 11151.29, 628.20),
        "2026-06-28": (1274, 9885, 11685, 10698, 11464, 10952.57, 11144.14, 641.76),
        "2026-06-29": (1275, 10999, 11305, 11535, 11810, 10854.57, 11127.54, 639.19),
        "2026-06-30": (1276, 11949, 10440, 11242, 11593, 10946.57, 11132.50, 645.20),
    }
    columns = [
        "trend_days",
        "lag_1",
        "lag_7",
        "lag_14",
        "lag_28",
        "rolling_mean_7",
        "rolling_mean_28",
        "rolling_std_28",
    ]
    for day, expected in printed.items():
        row = frame.loc[pd.Timestamp(day), columns]
        assert [round(float(v), 2) for v in row] == [float(v) for v in expected], day


# --- ModelSpec ------------------------------------------------------------------------------

BASE_SPEC = ModelSpec(
    "model_a",
    "type_a",
    ModelFamily.BASELINE,
    Procedure.DIRECT,
    "Some prose.",
    {"b": 2, "a": [1, 2]},
    "feature-set-a",
    "f" * 64,
    1,
)


def test_model_spec_fingerprint_is_sha256_of_canonical_identity() -> None:
    identity = {
        "model_id": "model_a",
        "model_type": "type_a",
        "family": "baseline",
        "procedure": "direct",
        "params": {"a": [1, 2], "b": 2},
        "feature_set": "feature-set-a",
        "feature_set_fingerprint": "f" * 64,
        "implementation_version": 1,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    assert BASE_SPEC.fingerprint() == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert BASE_SPEC.to_dict() == {**identity, "description": "Some prose."}
    json.dumps(BASE_SPEC.to_dict(), allow_nan=False)


def test_model_spec_fingerprint_excludes_description_and_param_order() -> None:
    reworded = dataclasses.replace(BASE_SPEC, description="Completely different prose.")
    assert reworded.fingerprint() == BASE_SPEC.fingerprint()
    reordered = dataclasses.replace(BASE_SPEC, params={"a": [1, 2], "b": 2})
    assert reordered.fingerprint() == BASE_SPEC.fingerprint()


@pytest.mark.parametrize(
    "change",
    [
        {"model_id": "model_b"},
        {"model_type": "type_b"},
        {"family": ModelFamily.REGRESSION},
        {"procedure": Procedure.RECURSIVE},
        {"params": {"b": 3, "a": [1, 2]}},
        {"params": {"b": 2, "a": [2, 1]}},
        {"params": {"b": 2, "a": [1, 2], "c": None}},
        {"params": {"b": 2}},
        {"feature_set": "feature-set-b"},
        {"feature_set": None},
        {"feature_set_fingerprint": "e" * 64},
        {"implementation_version": 2},
    ],
)
def test_model_spec_fingerprint_changes_with_identity(change: dict[str, Any]) -> None:
    assert dataclasses.replace(BASE_SPEC, **change).fingerprint() != BASE_SPEC.fingerprint()


def _all_models() -> list[Forecaster]:
    return [
        NaiveForecaster(),
        SeasonalNaiveForecaster(),
        SameWeekdayMeanForecaster(),
        SameWeekdayMedianForecaster(),
        SeasonalExponentialSmoothing(),
        legacy_linear_regression(),
        atlas_linear_regression(),
    ]


def test_specs_of_all_model_types() -> None:
    specs = {model.spec.model_id: model.spec for model in _all_models()}
    assert list(specs) == [
        "naive",
        "seasonal_naive",
        "same_weekday_mean_4w",
        "same_weekday_median_5w",
        "seasonal_exponential_smoothing",
        "legacy_linear_regression",
        "atlas_linear_regression",
    ]
    for model_id in list(specs)[:5]:
        assert specs[model_id].family is ModelFamily.BASELINE
        assert specs[model_id].procedure is Procedure.DIRECT
        assert specs[model_id].feature_set_fingerprint is None
    assert specs["naive"].params == {}
    assert specs["seasonal_naive"].params == {"season_days": 7}
    assert specs["same_weekday_mean_4w"].params == {"weeks": 4}
    assert specs["same_weekday_median_5w"].params == {"weeks": 5}
    assert specs["same_weekday_mean_4w"].model_type == "same_weekday_mean"
    assert specs["same_weekday_median_5w"].model_type == "same_weekday_median"

    legacy = specs["legacy_linear_regression"]
    assert legacy.family is ModelFamily.REGRESSION
    assert legacy.procedure is Procedure.RECURSIVE
    assert dict(legacy.params) == {
        "fit_method": "legacy_min_norm",
        "intercept": True,
        "min_training_rows": 365,
        "training_horizon": 1,
        "clip_at_zero": True,
        "round_feedback": "half_even_before_feedback",
        "features": list(LEGACY_NAMES),
    }
    assert legacy.feature_set == "legacy-notebook-cell-14"
    assert legacy.feature_set_fingerprint == LEGACY_FINGERPRINT

    atlas = specs["atlas_linear_regression"]
    assert atlas.family is ModelFamily.REGRESSION
    assert atlas.procedure is Procedure.RECURSIVE
    assert dict(atlas.params) == {
        "fit_method": "standardized_ols",
        "intercept": True,
        "min_training_rows": 365,
        "training_horizon": 1,
        "clip_at_zero": True,
        "round_feedback": "none",
        "features": list(ATLAS_NAMES),
    }
    assert atlas.feature_set == "atlas-m2-regression-v1"
    assert atlas.feature_set_fingerprint == ATLAS_FINGERPRINT

    fingerprints = [spec.fingerprint() for spec in specs.values()]
    assert len(set(fingerprints)) == len(fingerprints)
    for spec in specs.values():
        json.dumps(spec.to_dict(), allow_nan=False)


def test_baseline_parameters_change_the_fingerprint() -> None:
    base = {m.spec.model_id: m.spec.fingerprint() for m in _all_models()[:4]}
    assert SeasonalNaiveForecaster(season_days=14).spec.fingerprint() != base["seasonal_naive"]
    assert SameWeekdayMeanForecaster(weeks=3).spec.fingerprint() != base["same_weekday_mean_4w"]
    assert SameWeekdayMedianForecaster(weeks=3).spec.fingerprint() != base["same_weekday_median_5w"]
    assert NaiveForecaster("naive_2").spec.fingerprint() != base["naive"]


@pytest.mark.parametrize(
    "change",
    [
        {"round_feedback": False},
        {"method": FitMethod.STANDARDIZED_OLS},
        {"min_training_rows": 366},
        {"implementation_version": 2},
        {"feature_set": atlas_regression_feature_set()},
        {"model_type": "something_else"},
        {"model_id": "legacy_2"},
    ],
)
def test_regression_behaviour_switches_change_the_fingerprint(change: dict[str, Any]) -> None:
    legacy = legacy_linear_regression()
    assert dataclasses.replace(legacy, **change).spec.fingerprint() != legacy.spec.fingerprint()


def test_regression_description_does_not_change_the_fingerprint() -> None:
    legacy = legacy_linear_regression()
    reworded = dataclasses.replace(legacy, description="Reworded.")
    assert reworded.spec.fingerprint() == legacy.spec.fingerprint()
    assert reworded.spec.description == "Reworded."


@pytest.mark.parametrize("rows", [0, -5, True])
def test_regression_min_training_rows_validation(rows: Any) -> None:
    with pytest.raises(ConfigError):
        legacy_linear_regression(min_training_rows=rows)


# --- Regression models: boundary conditions owned by models.py -------------------------------


def test_regression_fit_errors_carry_codes() -> None:
    start = date(2023, 1, 1)
    short = series_from(start, noisy_weekly_values(20, seed=1))
    for model in (legacy_linear_regression(), atlas_linear_regression()):
        with pytest.raises(ModelFitError) as too_short:
            model.fit(short)
        assert too_short.value.code == "insufficient_history"
    few_rows = series_from(start, noisy_weekly_values(70, seed=1))  # 42 training rows
    model = legacy_linear_regression(min_training_rows=43)
    with pytest.raises(ModelFitError) as too_few:
        model.fit(few_rows)
    assert too_few.value.code == "insufficient_training_rows"
    assert "42 complete training rows" in str(too_few.value)
    fitted = legacy_linear_regression(min_training_rows=42).fit(few_rows)
    assert fitted.diagnostics.n_train_rows == 42
    assert fitted.diagnostics.n_dropped_rows == 0
    assert fitted.diagnostics.train_first == start + timedelta(days=28)
    assert fitted.diagnostics.train_last == few_rows.end


def test_regression_postprocess_clips_then_rounds_half_even_only_for_legacy() -> None:
    series = series_from(date(2023, 1, 1), noisy_weekly_values(120, seed=5))
    legacy = legacy_linear_regression(min_training_rows=30).fit(series)
    cases = {2.5: 2.0, 3.5: 4.0, 0.5: 0.0, 1.5: 2.0, -3.7: 0.0, 1234.49: 1234.0, 10.51: 11.0}
    for raw, expected in cases.items():
        assert legacy.postprocess(raw) == expected, raw
    plain = dataclasses.replace(
        legacy_linear_regression(min_training_rows=30), round_feedback=False
    )
    fitted = plain.fit(series)
    assert isinstance(plain, RecursiveLinearForecaster)
    for raw in (2.5, 3.5, 1234.49):
        assert fitted.postprocess(raw) == raw
    assert fitted.postprocess(-0.1) == 0.0


def test_regression_forecast_path_is_consistent() -> None:
    """Rounded legacy path: predictions are integers, raw values unrounded, inputs fed back."""
    series = series_from(date(2023, 1, 1), noisy_weekly_values(150, seed=8))
    fitted = legacy_linear_regression(min_training_rows=60).fit(series)
    path = fitted.forecast(10)
    assert isinstance(path, ForecastPath)
    assert path.failures == {}
    assert path.raw_predictions is not None
    expected = [float(round(max(v, 0.0))) for v in path.raw_predictions]
    assert path.predictions.tolist() == expected
    assert path.inputs is not None
    fed_back = path.inputs.loc[path.predictions.index]
    assert fed_back.tolist() == path.predictions.tolist()
    assert path.feature_rows is not None
    lag_1 = path.feature_rows["lag_1"].tolist()
    assert lag_1[0] == series.to_series().iloc[-1]
    assert lag_1[1:] == path.predictions.tolist()[:-1]


# --- DemandSeries.through / window ------------------------------------------------------------

SERIES_START = date(2024, 1, 1)
SERIES_VALUES: list[float | None] = [float(v) for v in linear_values(60)]
SERIES_VALUES[20] = None  # 2024-01-21 missing
SERIES = series_from(SERIES_START, SERIES_VALUES, name="demo")


def test_through_contents() -> None:
    origin = date(2024, 2, 10)
    cut = SERIES.through(origin)
    assert isinstance(cut, DemandSeries)
    assert cut.name == "demo"
    assert (cut.start, cut.end) == (SERIES_START, origin)
    assert len(cut) == (origin - SERIES_START).days + 1 == 41
    assert cut.missing_dates == (date(2024, 1, 21),)
    values = cut.to_series()
    assert values.index.equals(pd.date_range(SERIES_START, origin, freq="D"))
    expected = SERIES_VALUES[:41]
    assert [None if math.isnan(v) else v for v in values] == expected
    assert SERIES.through(SERIES.end).to_series().equals(SERIES.to_series())
    single = SERIES.through(SERIES_START)
    assert (len(single), single.start, single.end) == (1, SERIES_START, SERIES_START)
    assert SERIES.through("2024-02-10").to_series().equals(values)
    assert SERIES.through(pd.Timestamp("2024-02-10")).to_series().equals(values)


def test_window_contents() -> None:
    window = SERIES.window(date(2024, 1, 15), date(2024, 1, 25))
    assert (window.start, window.end, len(window)) == (date(2024, 1, 15), date(2024, 1, 25), 11)
    assert window.missing_dates == (date(2024, 1, 21),)
    assert [None if math.isnan(v) else v for v in window.to_series()] == SERIES_VALUES[14:25]
    starts_missing = SERIES.window(date(2024, 1, 21), date(2024, 1, 22))
    assert starts_missing.missing_dates == (date(2024, 1, 21),)
    assert starts_missing.value_on(date(2024, 1, 22)) == 1021.0
    whole = SERIES.window(SERIES.start, SERIES.end)
    assert whole.to_series().equals(SERIES.to_series())


@settings(max_examples=20, deadline=None)
@given(first=st.integers(min_value=0, max_value=59), length=st.integers(min_value=1, max_value=60))
def test_window_property(first: int, length: int) -> None:
    last = min(first + length - 1, 59)
    a, b = SERIES_START + timedelta(days=first), SERIES_START + timedelta(days=last)
    window = SERIES.window(a, b)
    assert [None if math.isnan(v) else v for v in window.to_series()] == SERIES_VALUES[
        first : last + 1
    ]
    assert SERIES.through(b).to_series().equals(SERIES.window(SERIES_START, b).to_series())


def test_through_and_window_are_independent_copies() -> None:
    parent = series_from(SERIES_START, SERIES_VALUES, name="demo")
    child = parent.through(date(2024, 1, 31))
    window = parent.window(date(2024, 1, 10), date(2024, 1, 31))
    before_child, before_window = child.to_series(), window.to_series()
    # Mutate the parent's private storage after slicing: the children must not change.
    parent._values.iloc[:] = 0.0
    assert child.to_series().equals(before_child)
    assert window.to_series().equals(before_window)
    # And the other way round.
    fresh = series_from(SERIES_START, SERIES_VALUES, name="demo")
    sliced = fresh.through(date(2024, 1, 31))
    sliced._values.iloc[:] = 7.0
    assert fresh.to_series().iloc[0] == 1000.0
    # The public copy cannot be used to mutate either.
    exported = sliced.to_series()
    exported.iloc[:] = -1.0
    assert sliced.to_series().iloc[0] == 7.0


@pytest.mark.parametrize(
    ("first", "last"),
    [
        (date(2023, 12, 31), date(2024, 1, 10)),  # starts before the series
        (date(2024, 1, 10), date(2024, 3, 1)),  # ends after the series (end 2024-02-29)
        (date(2024, 1, 20), date(2024, 1, 10)),  # reversed
        (date(2024, 3, 1), date(2024, 3, 5)),  # entirely after
    ],
)
def test_window_outside_range_is_rejected(first: date, last: date) -> None:
    with pytest.raises(DataQualityError):
        SERIES.window(first, last)


@pytest.mark.parametrize("origin", [date(2023, 12, 31), date(2024, 3, 1), date(2025, 1, 1)])
def test_through_outside_range_is_rejected(origin: date) -> None:
    assert SERIES.end == date(2024, 2, 29)
    with pytest.raises(DataQualityError):
        SERIES.through(origin)
