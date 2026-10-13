"""True recursive feedback of the M2 regressions and the engine (models.py, backtest.py).

``RecursiveLinearForecaster`` / ``FittedRecursiveLinear`` must forecast ``o + 1 .. o + H`` by
iterating a one-step model whose lag and window features read *its own* previous
predictions after the origin (clipped at 0 and, for the legacy model, rounded half to even
*before* they are fed back), never the actual observations after the origin. Every test
here is built so that a teacher-forced implementation (observed lags after the origin), a
model that feeds back the raw instead of the post-processed value, or one that rounds half
up, would fail.

Where expected values come from (never from the code under test):

* counterfactual series: the actuals through ``o`` followed by the values the path says it
  fed back. The M1 one-step builder (``forecast_features``, tested in M1) applied to that
  series must give the recursive feature rows *bit for bit*, and the fitted intercept and
  coefficients applied to them give each raw prediction;
* hand-computed AR(1) paths on dyadic numbers (exact in binary floating point) for the clip
  and round-half-even rules, and a plain-Python loop for the unpinned fit;
* a plain-Python transcription of the legacy notebook's recursive loop (cell index 16:
  ``history_values`` list, ``statistics`` module, ``max(., 0)``, ``int(round(.))``) using the
  coefficients atlas fitted;
* plain-Python lag / window / same-weekday means over "actuals, then predictions";
* closed forms for which training rows a missing day removes (``j + 1`` for a gap ``j``
  days before the origin, with 28-day windows);
* the published legacy 30-day forecast (integration test only).
"""

from __future__ import annotations

import dataclasses
import functools
import math
import statistics
from collections.abc import Callable, Sequence
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from atlas.backtest import RECORD_TYPE_OBSERVED_INPUTS, forecast_at_origin, run_backtest
from atlas.features import FeatureSet, Lag, forecast_features, training_features
from atlas.loader import LoadedTable
from atlas.models import (
    FailureStage,
    FittedRecursiveLinear,
    ForecastPath,
    RecursiveLinearForecaster,
    SameWeekdayMeanForecaster,
    SeasonalNaiveForecaster,
    atlas_linear_regression,
    atlas_regression_feature_set,
    legacy_linear_regression,
)
from atlas.regression import FitMethod
from atlas.series import DemandSeries
from tests.m2_helpers import (
    noisy_series,
    noisy_weekly_values,
    perturbed_after,
    records_for,
    series_from,
    single_period_plan,
)

START = date(2023, 1, 1)  # the legacy notebook's first date (its trend_index anchor)
SERIES = noisy_series(START, 520, seed=11)  # 2023-01-01 .. 2024-06-03, integral, complete
H = 30
MIN_ROWS = 100
# Origins: mid-year (July targets unseen by the legacy month dummies), across the new
# year, across 29 Feb 2024, and late enough that every target has an actual.
ORIGINS = (date(2023, 6, 30), date(2023, 12, 20), date(2024, 2, 20), date(2024, 4, 15))

MODELS: dict[str, Callable[[], RecursiveLinearForecaster]] = {
    "atlas": lambda: atlas_linear_regression(min_training_rows=MIN_ROWS),
    "legacy": lambda: legacy_linear_regression(min_training_rows=MIN_ROWS),
}
ROUNDS = {"atlas": False, "legacy": True}


@functools.cache
def fitted_path(name: str, origin: date) -> tuple[FittedRecursiveLinear, ForecastPath]:
    """The model fitted on ``SERIES.through(origin)`` and its ``H``-day recursive path."""
    fitted = MODELS[name]().fit(SERIES.through(origin))
    return fitted, fitted.forecast(H)


def day(stamp: object) -> date:
    return pd.Timestamp(stamp).date()


def reference_postprocess(raw: float, rounding: bool) -> float:
    """The documented rule: clip at 0, then (legacy) round half to even."""
    clipped = max(raw, 0.0)
    return float(round(clipped)) if rounding else clipped


def actual_values(series: DemandSeries) -> dict[date, float]:
    return {day(stamp): float(value) for stamp, value in series.to_series().items()}


def continued(history: DemandSeries, fed_back: Sequence[float]) -> DemandSeries:
    """``history`` followed by ``fed_back`` on the next days (real-valued)."""
    values = [float(v) for v in history.to_series().to_numpy()] + [float(v) for v in fed_back]
    dates = [history.start + timedelta(days=k) for k in range(len(values))]
    return DemandSeries.from_observations(dates, values, name="counterfactual", integral=False)


def path_parts(path: ForecastPath) -> tuple[pd.Series, pd.DataFrame, pd.Series]:
    assert path.raw_predictions is not None
    assert path.feature_rows is not None
    assert path.inputs is not None
    return path.raw_predictions, path.feature_rows, path.inputs


# --- 1. Counterfactual parity ---------------------------------------------------------------


@pytest.mark.parametrize("origin", ORIGINS)
@pytest.mark.parametrize("name", sorted(MODELS))
def test_each_step_is_the_one_step_model_on_actuals_then_fed_back_values(
    name: str, origin: date
) -> None:
    """Row ``o + h`` == M1 one-step row of (actuals through ``o``, then predictions ``1..h-1``)."""
    fitted, path = fitted_path(name, origin)
    raw, rows, inputs = path_parts(path)
    history = SERIES.through(origin)
    predictions = path.predictions.to_numpy(dtype="float64")
    targets = pd.date_range(origin + timedelta(days=1), periods=H, freq="D")
    assert pd.DatetimeIndex(rows.index).equals(targets)
    assert list(rows.columns) == list(fitted.feature_set.names)
    assert path.failures == {}

    coefficients = fitted.result.coefficients
    intercept = fitted.result.intercept
    for horizon in range(1, H + 1):
        target = origin + timedelta(days=horizon)
        counterfactual = continued(history, predictions[: horizon - 1])
        assert counterfactual.end == target - timedelta(days=1)
        expected_row = forecast_features(
            counterfactual,
            origin=target - timedelta(days=1),
            horizons=(1,),
            feature_set=fitted.feature_set,
        ).frame
        recursive_row = rows.loc[[pd.Timestamp(target)]]
        assert list(expected_row.columns) == list(recursive_row.columns)
        # Bit-for-bit: the same values went through the same feature code.
        assert (
            recursive_row.to_numpy(dtype="float64").tobytes()
            == expected_row.to_numpy(dtype="float64").tobytes()
        ), (name, origin, horizon)
        expected_raw = intercept + float(expected_row.to_numpy(dtype="float64")[0] @ coefficients)
        assert float(raw.iloc[horizon - 1]) == pytest.approx(expected_raw, rel=1e-9, abs=1e-9)
        assert predictions[horizon - 1] == reference_postprocess(
            float(raw.iloc[horizon - 1]), ROUNDS[name]
        )

    # The demand window: actuals through the origin, then exactly the fed-back values.
    first_input = origin - timedelta(days=fitted.feature_set.max_offset)
    actual = history.to_series().loc[pd.Timestamp(first_input) :]
    assert inputs.loc[: pd.Timestamp(origin)].to_numpy().tobytes() == actual.to_numpy().tobytes()
    after = inputs.loc[pd.Timestamp(origin + timedelta(days=1)) :]
    assert pd.DatetimeIndex(after.index).equals(targets)
    assert after.to_numpy().tobytes() == predictions.tobytes()
    if ROUNDS[name]:
        assert all(float(v).is_integer() for v in predictions)  # integers were fed back
        assert not all(float(v).is_integer() for v in raw)

    # Discrimination: the teacher-forced rows (observed demand after the origin) agree at
    # h = 1 only, so a teacher-forcing implementation could not pass the loop above.
    observed = training_features(
        SERIES,
        origin=origin + timedelta(days=H),
        horizon=1,
        feature_set=fitted.feature_set,
        first_target=origin + timedelta(days=1),
    ).frame
    assert observed.iloc[[0]].to_numpy().tobytes() == rows.iloc[[0]].to_numpy().tobytes()
    differs = (observed.to_numpy() != rows.to_numpy()).any(axis=1)
    assert differs[1:].all()


# --- 2. Shock test --------------------------------------------------------------------------


def fitted_with_future(
    name: str, fitted: FittedRecursiveLinear, full: DemandSeries, origin: date
) -> FittedRecursiveLinear:
    """The same fitted model, but holding data *past* its origin (made reachable on purpose)."""
    injected = FittedRecursiveLinear(MODELS[name](), full, fitted.result, fitted.diagnostics)
    injected.origin = origin
    return injected


@settings(max_examples=12, deadline=None)
@given(
    name=st.sampled_from(sorted(MODELS)),
    origin=st.sampled_from(ORIGINS[:3]),
    seed=st.integers(min_value=0, max_value=10**6),
)
def test_recursive_path_never_reads_actuals_after_the_origin(
    name: str, origin: date, seed: int
) -> None:
    fitted, path = fitted_path(name, origin)
    shocked = perturbed_after(SERIES, origin, seed)  # every later day: random 0..50,000
    assert shocked.end == SERIES.end

    # The model is handed the shocked actuals after its origin and must not read them.
    shocked_path = fitted_with_future(name, fitted, shocked, origin).forecast(H)
    reference_parts = path_parts(path)
    for got, expected in zip(path_parts(shocked_path), reference_parts, strict=True):
        assert got.index.equals(expected.index)
        assert got.to_numpy().tobytes() == expected.to_numpy().tobytes()
    assert shocked_path.predictions.to_numpy().tobytes() == path.predictions.to_numpy().tobytes()
    # The same holds through ``fit`` on the truncated shocked series (barrier 0).
    refit_path = MODELS[name]().fit(shocked.through(origin)).forecast(H)
    assert refit_path.predictions.to_numpy().tobytes() == path.predictions.to_numpy().tobytes()

    # Positive control: teacher-forced predictions (observed rows) do see the shock.
    def teacher_forced(series: DemandSeries) -> np.ndarray:
        rows = training_features(
            series,
            origin=origin + timedelta(days=H),
            horizon=1,
            feature_set=fitted.feature_set,
            first_target=origin + timedelta(days=1),
        ).frame
        return fitted.predict_rows(rows)

    before, after = teacher_forced(SERIES), teacher_forced(shocked)
    assert after[0] == before[0]  # the h = 1 row reads only days up to the origin
    assert (after[1:] != before[1:]).all()


def test_engine_forecasts_ignore_a_shock_after_their_origin() -> None:
    plan = single_period_plan(
        date(2023, 12, 1), date(2023, 12, 29), max_horizon=10, min_history_days=300, stride_days=14
    )
    models = [MODELS["atlas"](), MODELS["legacy"]()]
    cutoff = date(2023, 12, 15)
    base = run_backtest(SERIES, plan, models).records
    shocked = run_backtest(perturbed_after(SERIES, cutoff, seed=99), plan, models).records
    model_columns = [
        "prediction",
        "prediction_raw",
        "status",
        "failure_code",
        "estimable",
        "leverage",
    ]
    for model in models:
        for origin in (date(2023, 12, 1), cutoff):
            a = records_for(base, model.spec.model_id, origin)
            b = records_for(shocked, model.spec.model_id, origin)
            pd.testing.assert_frame_equal(
                a[model_columns].reset_index(drop=True), b[model_columns].reset_index(drop=True)
            )
        # Positive controls: the shock reaches the actuals and the next origin's history.
        a = records_for(base, model.spec.model_id, cutoff)
        b = records_for(shocked, model.spec.model_id, cutoff)
        assert (a["actual"].to_numpy() != b["actual"].to_numpy()).all()
        later_a = records_for(base, model.spec.model_id, date(2023, 12, 29))
        later_b = records_for(shocked, model.spec.model_id, date(2023, 12, 29))
        assert (later_a["prediction_raw"].to_numpy() != later_b["prediction_raw"].to_numpy()).all()


# --- 3. Exact clip and round-half-even before feedback -----------------------------------------

AR_START = date(2024, 1, 1)
AR_DAYS = 16
AR_STEPS = 6

# (a, b, y_origin, y_origin_minus_1, rounded: raw, predictions, n_clipped,
#  unrounded: raw, predictions, n_clipped), all hand-computed with raw = a + b * fed:
# a=-5.5, b=2, y=4.5: 3.5 -> 4 | 2.5 -> 2 | -1.5 -> 0 | -5.5 ...; unrounded 3.5, 1.5, -2.5 ...
# a=-1.5, b=1, y=5:   3.5 -> 4 | 2.5 -> 2 | 0.5 -> 0 (not clipped) | -1.5 ...;
#                     unrounded 3.5, 2.0, 0.5, -1.0, -1.5 ...
AR_CASES = [
    (
        -5.5,
        2.0,
        4.5,
        5.0,
        [3.5, 2.5, -1.5, -5.5, -5.5, -5.5],
        [4.0, 2.0, 0.0, 0.0, 0.0, 0.0],
        4,
        [3.5, 1.5, -2.5, -5.5, -5.5, -5.5],
        [3.5, 1.5, 0.0, 0.0, 0.0, 0.0],
        4,
    ),
    (
        -1.5,
        1.0,
        5.0,
        6.5,
        [3.5, 2.5, 0.5, -1.5, -1.5, -1.5],
        [4.0, 2.0, 0.0, 0.0, 0.0, 0.0],
        3,
        [3.5, 2.0, 0.5, -1.0, -1.5, -1.5],
        [3.5, 2.0, 0.5, 0.0, 0.0, 0.0],
        3,
    ),
]


def ar1_series(a: float, b: float, y_origin: float) -> DemandSeries:
    """Noiseless ``y_t = a + b * y_(t-1)`` ending at ``y_origin`` (dyadic, so exact)."""
    values = [y_origin]
    for _ in range(AR_DAYS - 1):
        values.append((values[-1] - a) / b)
    values.reverse()
    assert all(v >= 0 for v in values)
    assert all(values[k] == a + b * values[k - 1] for k in range(1, AR_DAYS))
    dates = [AR_START + timedelta(days=k) for k in range(AR_DAYS)]
    return DemandSeries.from_observations(dates, values, name="ar1", integral=False)


def ar1_model(rounding: bool) -> RecursiveLinearForecaster:
    return RecursiveLinearForecaster(
        model_id="ar1",
        model_type="test_ar1",
        feature_set=FeatureSet((Lag(1),), name="ar1"),
        method=FitMethod.STANDARDIZED_OLS,
        min_training_rows=5,
        round_feedback=rounding,
        description="AR(1) on the previous day, for the feedback tests.",
    )


@pytest.mark.parametrize("rounding", [True, False], ids=["round_feedback", "no_rounding"])
@pytest.mark.parametrize(
    (
        "a",
        "b",
        "y_origin",
        "y_before",
        "raw_rounded",
        "pred_rounded",
        "clipped_rounded",
        "raw_plain",
        "pred_plain",
        "clipped_plain",
    ),
    AR_CASES,
    ids=["b=2", "b=1"],
)
def test_clip_and_round_half_even_happen_before_feedback(
    rounding: bool,
    a: float,
    b: float,
    y_origin: float,
    y_before: float,
    raw_rounded: list[float],
    pred_rounded: list[float],
    clipped_rounded: int,
    raw_plain: list[float],
    pred_plain: list[float],
    clipped_plain: int,
) -> None:
    series = ar1_series(a, b, y_origin)
    model = ar1_model(rounding)
    fitted = model.fit(series)
    assert fitted.diagnostics.n_train_rows == AR_DAYS - 1
    # The noiseless fit recovers a and b (to rounding error) ...
    assert float(fitted.result.coefficients[0]) == pytest.approx(b, rel=1e-9)
    assert fitted.result.intercept == pytest.approx(a, abs=1e-9)
    # ... and is pinned to them exactly, so the raw predictions hit the .5 ties exactly.
    exact = dataclasses.replace(fitted.result, coefficients=np.array([b]), intercept=a)
    path = FittedRecursiveLinear(model, series, exact, fitted.diagnostics).forecast(AR_STEPS)
    raw, rows, inputs = path_parts(path)

    expected_raw = raw_rounded if rounding else raw_plain
    expected = pred_rounded if rounding else pred_plain
    assert raw.tolist() == expected_raw
    assert path.predictions.tolist() == expected
    assert path.n_clipped == (clipped_rounded if rounding else clipped_plain)
    # What was fed back is the recorded (clipped, rounded) prediction, not the raw value.
    assert rows["lag_1"].tolist() == [y_origin, *expected[:-1]]
    assert inputs.tolist() == [y_before, y_origin, *expected]
    assert pd.DatetimeIndex(inputs.index).equals(
        pd.date_range(series.end - timedelta(days=1), periods=AR_STEPS + 2, freq="D")
    )
    if rounding:  # the two rules really differ on these paths
        assert raw_rounded[1] != raw_plain[1]


@pytest.mark.parametrize("rounding", [True, False], ids=["round_feedback", "no_rounding"])
@pytest.mark.parametrize("case", AR_CASES, ids=["b=2", "b=1"])
def test_unpinned_fit_iterates_its_own_parameters(rounding: bool, case: tuple[object, ...]) -> None:
    """With the coefficients as fitted, the path equals a plain-Python iteration of them."""
    a, b, y_origin = (float(v) for v in case[:3])
    fitted = ar1_model(rounding).fit(ar1_series(a, b, y_origin))
    slope = float(fitted.result.coefficients[0])
    intercept = fitted.result.intercept
    fed, raws, predictions = y_origin, [], []
    for _ in range(AR_STEPS):
        value = intercept + slope * fed
        raws.append(value)
        fed = reference_postprocess(value, rounding)
        predictions.append(fed)
    path = fitted.forecast(AR_STEPS)
    raw, _, inputs = path_parts(path)
    assert raw.tolist() == raws
    assert path.predictions.tolist() == predictions
    assert inputs.tolist()[2:] == predictions
    assert path.n_clipped == sum(v < 0 for v in raws)


# --- 4. The legacy notebook's recursive loop in plain Python -----------------------------------


def notebook_cell_16_loop(
    history_values: list[float],
    origin: date,
    intercept: float,
    coefficients: Sequence[float],
    steps: int = H,
) -> tuple[list[float], list[int]]:
    """Notebook cell index 16, transcribed: one feature row per future date, then append."""
    history = list(history_values)
    raws: list[float] = []
    forecasts: list[int] = []
    for step in range(1, steps + 1):
        future_date = origin + timedelta(days=step)
        weekday = future_date.weekday()  # pandas dayofweek: Monday = 0
        day_of_year = future_date.timetuple().tm_yday
        row = [
            float((future_date - START).days),  # trend_index from the first date of data
            float(future_date.year),
            float(day_of_year),
            float(weekday >= 5),
            math.sin(2 * math.pi * day_of_year / 365.25),
            math.cos(2 * math.pi * day_of_year / 365.25),
            history[-1],
            history[-7],
            history[-14],
            history[-28],
            statistics.fmean(history[-7:]),
            statistics.fmean(history[-28:]),
            statistics.stdev(history[-28:]),
            *(float(weekday == k) for k in range(7)),  # weekday_0 .. weekday_6
            *(float(future_date.month == m) for m in range(1, 13)),  # month_1 .. month_12
        ]
        predicted = intercept + sum(c * x for c, x in zip(coefficients, row, strict=True))
        raws.append(predicted)
        # Notebook: int(round(max(predicted, 0))); Python's round already returns an int and
        # rounds half to even.
        predicted_requests = round(max(predicted, 0))
        forecasts.append(predicted_requests)
        history.append(predicted_requests)
    return raws, forecasts


@pytest.mark.parametrize("origin", ORIGINS[1:3], ids=["across_new_year", "across_29_feb"])
def test_legacy_forecast_equals_the_notebook_loop(origin: date) -> None:
    fitted, path = fitted_path("legacy", origin)
    raw, _, _ = path_parts(path)
    history = [float(v) for v in SERIES.through(origin).to_series()]
    raws, forecasts = notebook_cell_16_loop(
        history, origin, fitted.result.intercept, fitted.result.coefficients.tolist()
    )
    assert path.predictions.tolist() == [float(v) for v in forecasts]
    assert raw.to_numpy() == pytest.approx(raws, rel=1e-9)
    # The loop is sensitive to feedback: feeding it the actuals instead changes h >= 2.
    actual = actual_values(SERIES)
    teacher = []
    for horizon in range(1, H + 1):
        known = [*history, *(actual[origin + timedelta(days=k)] for k in range(1, horizon))]
        teacher.append(
            notebook_cell_16_loop(
                known,
                origin + timedelta(days=horizon - 1),
                fitted.result.intercept,
                fitted.result.coefficients.tolist(),
                steps=1,
            )[1][0]
        )
    assert teacher[0] == forecasts[0]
    assert sum(t != f for t, f in zip(teacher[1:], forecasts[1:], strict=True)) >= H - 3


@pytest.mark.integration
def test_legacy_notebook_loop_on_the_snapshot(legacy_tables: dict[str, LoadedTable]) -> None:
    """Committed daily_total: the loop with atlas' coefficients gives the published forecast."""
    series = DemandSeries.from_frame(legacy_tables["daily_total"].frame, name="daily_total")
    assert series.start == START
    published = legacy_tables["future_30_day_forecast"].frame
    published_values = [int(v) for v in published["forecast_requests"]]
    for origin in (date(2025, 6, 30), series.end):
        fitted = legacy_linear_regression().fit(series.through(origin))
        path = fitted.forecast(H)
        history = [float(v) for v in series.through(origin).to_series()]
        _, forecasts = notebook_cell_16_loop(
            history, origin, fitted.result.intercept, fitted.result.coefficients.tolist()
        )
        assert path.predictions.tolist() == [float(v) for v in forecasts], origin
        if origin == series.end:
            assert forecasts == published_values
            assert [day(d) for d in published["request_date"]] == [
                origin + timedelta(days=k) for k in range(1, H + 1)
            ]


# --- 5. Direct baselines versus recursive feedback ---------------------------------------------


def window_mean(values: dict[date, float], target: date, offsets: Sequence[int]) -> float:
    return statistics.fmean(values[target - timedelta(days=k)] for k in offsets)


@pytest.mark.parametrize("origin", ORIGINS[1:])
def test_direct_baselines_read_only_actuals_through_the_origin(origin: date) -> None:
    actual = actual_values(SERIES)
    history = SERIES.through(origin)
    naive_path = SeasonalNaiveForecaster().fit(history).forecast(H)
    expected = [actual[origin + timedelta(days=h - 7 * math.ceil(h / 7))] for h in range(1, H + 1)]
    assert naive_path.predictions.tolist() == expected
    assert naive_path.feature_rows is None
    assert naive_path.inputs is None

    mean_path = SameWeekdayMeanForecaster().fit(history).forecast(H)
    for horizon in range(8, 15):
        target = origin + timedelta(days=horizon)
        direct = math.fsum(actual[target - timedelta(days=7 * w)] for w in (2, 3, 4, 5)) / 4
        assert mean_path.predictions.iloc[horizon - 1] == direct
        # A recursive same-weekday mean would read its own week-1 prediction instead.
        own = float(mean_path.predictions.iloc[horizon - 8])
        recursive = math.fsum([own, *(actual[target - timedelta(days=7 * w)] for w in (2, 3, 4))])
        assert recursive / 4 != direct


@pytest.mark.parametrize("origin", ORIGINS)
@pytest.mark.parametrize("name", sorted(MODELS))
def test_recursive_rows_read_their_own_predictions(name: str, origin: date) -> None:
    """Plain-Python lags and windows over (actuals through ``o``, then the predictions)."""
    _, path = fitted_path(name, origin)
    _, rows, _ = path_parts(path)
    predictions = path.predictions.to_numpy(dtype="float64")
    known = {d: v for d, v in actual_values(SERIES).items() if d <= origin}
    for horizon in range(1, H + 1):
        known[origin + timedelta(days=horizon)] = float(predictions[horizon - 1])
    assert rows["lag_1"].iloc[1] == predictions[0]  # h = 2 reads the h = 1 prediction
    for horizon in range(1, H + 1):
        target = origin + timedelta(days=horizon)
        row = rows.loc[pd.Timestamp(target)]
        for lag in (1, 7) + ((14, 28) if name == "legacy" else ()):
            assert row[f"lag_{lag}"] == known[target - timedelta(days=lag)], (horizon, lag)
        assert row["rolling_mean_28"] == pytest.approx(
            window_mean(known, target, range(1, 29)), rel=1e-12
        )
        if name == "atlas":
            assert row["same_weekday_mean_4w"] == pytest.approx(
                window_mean(known, target, (7, 14, 21, 28)), rel=1e-12
            )
        else:
            assert row["rolling_mean_7"] == pytest.approx(
                window_mean(known, target, range(1, 8)), rel=1e-12
            )
            window = [known[target - timedelta(days=k)] for k in range(1, 29)]
            assert row["rolling_std_28"] == pytest.approx(statistics.stdev(window), rel=1e-9)
    # Week 2 reads week 1's predictions through lag_7.
    assert rows["lag_7"].iloc[7:].tolist() == predictions[: H - 7].tolist()


# --- 6. Engine: observed-input diagnostic ------------------------------------------------------


def test_observed_input_diagnostic_is_separate_from_the_recursive_records() -> None:
    plan_kwargs = {"max_horizon": 14, "min_history_days": 300, "stride_days": 15}
    first, last = date(2023, 12, 1), date(2024, 3, 1)
    models = [SeasonalNaiveForecaster(), MODELS["atlas"](), MODELS["legacy"]()]
    on = run_backtest(
        SERIES,
        single_period_plan(first, last, observed_input_diagnostic=True, **plan_kwargs),
        models,
    )
    off = run_backtest(SERIES, single_period_plan(first, last, **plan_kwargs), models)

    # Switching the diagnostic on changes nothing in the backtest itself.
    pd.testing.assert_frame_equal(on.records, off.records)
    pd.testing.assert_frame_equal(on.fits, off.fits)
    pd.testing.assert_frame_equal(on.coefficients, off.coefficients)
    assert off.observed_inputs.empty
    origins = [first + timedelta(days=15 * k) for k in range(7)]
    diagnostic = on.observed_inputs
    assert set(diagnostic["record_type"]) == {RECORD_TYPE_OBSERVED_INPUTS}
    assert set(diagnostic["model_id"]) == {"atlas_linear_regression", "legacy_linear_regression"}
    assert len(diagnostic) == 2 * len(origins) * 14

    keys = ["period", "model_id", "origin", "horizon"]
    merged = diagnostic.merge(on.records, on=keys, how="left", suffixes=("", "_record"))
    assert len(merged) == len(diagnostic)
    assert (merged["status"] == "ok").all()
    first_step = merged[merged["horizon"] == 1]
    assert len(first_step) == 2 * len(origins)
    for observed, raw in zip(
        first_step["prediction_observed_inputs"], first_step["prediction_raw"], strict=True
    ):
        assert observed == pytest.approx(max(raw, 0.0), rel=1e-9, abs=1e-9)
    later = merged[merged["horizon"] > 1]
    gap = (later["prediction_observed_inputs"] - later["prediction_raw"].clip(lower=0)).abs()
    assert (gap > 1e-6).any()
    at_h2 = merged[merged["horizon"] == 2]
    assert (
        (at_h2["prediction_observed_inputs"] - at_h2["prediction_raw"].clip(lower=0)).abs() > 1e-6
    ).all()

    # The diagnostic is the fitted model on *observed* rows: rebuild it for the atlas model
    # from the published coefficients and the M1 one-step rows of the actual series.
    names = list(atlas_regression_feature_set().names)
    atlas_rows = diagnostic[diagnostic["model_id"] == "atlas_linear_regression"]
    for origin in origins:
        table = on.coefficients[
            (on.coefficients["model_id"] == "atlas_linear_regression")
            & (on.coefficients["origin"] == pd.Timestamp(origin))
        ].set_index("term")["coefficient"]
        assert list(table.index) == ["intercept", *names]
        for horizon in (1, 2, 7, 14):
            target = origin + timedelta(days=horizon)
            row = forecast_features(
                SERIES,
                origin=target - timedelta(days=1),
                horizons=(1,),
                feature_set=atlas_regression_feature_set(),
            ).frame.iloc[0]
            expected = max(
                float(table["intercept"])
                + math.fsum(float(table[n]) * float(row[n]) for n in names),
                0.0,
            )
            got = atlas_rows[
                (atlas_rows["origin"] == pd.Timestamp(origin)) & (atlas_rows["horizon"] == horizon)
            ]["prediction_observed_inputs"]
            assert float(got.iloc[0]) == pytest.approx(expected, rel=1e-9)

    # The engine's records are the model's own recursive path, bit for bit.
    for name in sorted(MODELS):
        model = MODELS[name]()
        for origin in (origins[0], origins[-1]):
            path = model.fit(SERIES.through(origin)).forecast(14)
            got = records_for(on.records, model.spec.model_id, origin)
            assert got["prediction"].to_numpy().tobytes() == path.predictions.to_numpy().tobytes()


# --- 7. Missing input during the recursion ---------------------------------------------------

GAP_ORIGIN = date(2024, 2, 1)
GAP_VALUES = noisy_weekly_values(480, seed=3)


def series_missing(days_before_origin: int) -> DemandSeries:
    values: list[float | None] = [float(v) for v in GAP_VALUES]
    values[(GAP_ORIGIN - START).days - days_before_origin] = None
    return series_from(START, values)


@pytest.mark.parametrize("gap", [0, 1, 6, 13, 27])
@pytest.mark.parametrize("name", sorted(MODELS))
def test_missing_day_in_the_first_window_fails_every_horizon(name: str, gap: int) -> None:
    series = series_missing(gap)
    history = series.through(GAP_ORIGIN)
    missing_day = GAP_ORIGIN - timedelta(days=gap)
    assert history.missing_dates == (missing_day,)
    outcome = forecast_at_origin(MODELS[name](), history, H)
    assert outcome.path is None  # no horizon is forecast: nothing is imputed
    assert outcome.failure is not None
    assert outcome.failure.stage is FailureStage.FORECAST
    assert outcome.failure.code == "missing_input"
    assert str(GAP_ORIGIN + timedelta(days=1)) in outcome.failure.message
    # The fit itself succeeded on the complete rows: the target row of the missing day and
    # the ``gap`` later rows whose 28-day windows read it are dropped, nothing else.
    assert outcome.diagnostics is not None
    assert outcome.diagnostics.n_dropped_rows == gap + 1
    assert outcome.diagnostics.train_last == GAP_ORIGIN - timedelta(days=gap + 1)


@pytest.mark.parametrize("name", sorted(MODELS))
def test_missing_day_outside_the_first_window_does_not_stop_the_recursion(name: str) -> None:
    """Control: a gap 28 days before the origin is read by no forecast step."""
    outcome = forecast_at_origin(MODELS[name](), series_missing(28).through(GAP_ORIGIN), H)
    assert outcome.failure is None
    assert outcome.path is not None
    assert np.isfinite(outcome.path.predictions.to_numpy()).all()
    assert outcome.diagnostics is not None
    # Its target row and the 28 rows whose windows read it, o - 28 .. o, are dropped.
    assert outcome.diagnostics.n_dropped_rows == 29
    assert outcome.diagnostics.train_last == GAP_ORIGIN - timedelta(days=29)


def test_engine_records_every_horizon_of_a_failed_recursion() -> None:
    series = series_missing(6)
    later = GAP_ORIGIN + timedelta(days=28)  # its first window starts after the gap
    plan = single_period_plan(
        GAP_ORIGIN, later, max_horizon=H, min_history_days=300, stride_days=28
    )
    models = [SeasonalNaiveForecaster(), MODELS["atlas"](), MODELS["legacy"]()]
    result = run_backtest(series, plan, models)
    for name in sorted(MODELS):
        model_id = MODELS[name]().spec.model_id
        failed = records_for(result.records, model_id, GAP_ORIGIN)
        assert failed["horizon"].tolist() == list(range(1, H + 1))
        assert (failed["status"] == "failed").all()
        assert (failed["failure_stage"] == "forecast").all()
        assert (failed["failure_code"] == "missing_input").all()
        assert failed["prediction"].isna().all()
        assert failed["prediction_raw"].isna().all()
        fit = result.fits[
            (result.fits["model_id"] == model_id)
            & (result.fits["origin"] == pd.Timestamp(GAP_ORIGIN))
        ]
        assert fit["status"].tolist() == ["failed"]
        assert fit["n_dropped_rows"].tolist() == [7]
        recovered = records_for(result.records, model_id, later)
        assert (recovered["status"] == "ok").all()
    # Contrast: a direct baseline fails only the horizons that read the missing day.
    naive = records_for(result.records, "seasonal_naive", GAP_ORIGIN)
    failed_horizons = naive.loc[naive["status"] == "failed", "horizon"].tolist()
    assert failed_horizons == [1, 8, 15, 22, 29]
    assert set(naive.loc[naive["status"] == "failed", "failure_code"]) == {"unavailable_feature"}
