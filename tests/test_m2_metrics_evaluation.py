"""Error metrics, paired comparisons, model selection, recommendation and the metrics payload.

Covers :mod:`atlas.metrics`, :mod:`atlas.evaluation` and the determinism of
:func:`atlas.report.render_report` (M2 requirements: exact arithmetic of error metrics,
baseline comparisons under identical origins, model failure handling).

Where the expected values come from:

* metric arithmetic: small hand examples worked out in the comments, plus a plain-Python
  reference (``_ref_metrics``) used by a property test;
* DM statistics, intervals and effective sample sizes: plain-Python re-implementations of
  the documented estimators (``_ref_dm_statistic``, ``_ref_dm``,
  ``_ref_effective_sample_size``), Student-t p-values from Abramowitz & Stegun 26.7.3/4
  (``_ref_t_two_sided_p``) and the tabulated quantile ``t(0.975, 59) = 2.000995``;
* regression reliability, fit summary and influence diagnostics: hand-planted record and
  fit frames whose expected aggregates are worked out in the comments;
* selection, recommendation, underperformance and sensitivity views: backtests of
  *scripted* models whose prediction is ``actual + e(origin, horizon)`` with integer
  ``e`` chosen by the test. The scripted models know the outcomes only to make their
  errors exact (they are test fixtures, not forecasters); every expected MAE and count is
  then recomputed from the scripts in plain Python (``ref_*`` helpers), never from the
  code under test. Model ``type`` entries in the test configurations are placeholders:
  the evaluation reads model families from the backtest result, not from the config.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import math
import random
import statistics
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from atlas.backtest import FIT_COLUMNS, RECORD_COLUMNS, BacktestResult, run_backtest
from atlas.errors import ConfigError, ForecastError, ModelFitError
from atlas.evaluation import (
    SELECTION_EXACT_FIELDS,
    SELECTION_TEST_CLOSE,
    SELECTION_TEST_EXACT,
    _fit_summary,
    _influence,
    _regression_reliability,
    evaluate,
    recommend,
    select_model,
    selection_differences,
    underperformance,
)
from atlas.experiment import ExperimentConfig, json_bytes, parse_config
from atlas.features import FeatureSet, Lag
from atlas.metrics import (
    HorizonBucket,
    compare,
    contiguous,
    dependence_test,
    effective_sample_size,
    error_metrics,
    paired,
    per_origin_mean,
    scopes,
    seasonal_naive_scale,
    summarize,
    window_summary,
)
from atlas.models import (
    Failure,
    FailureStage,
    FitDiagnostics,
    FittedForecaster,
    Forecaster,
    ForecastPath,
    ModelFamily,
    ModelSpec,
    Procedure,
    RecursiveLinearForecaster,
    SeasonalNaiveForecaster,
    target_dates,
)
from atlas.regression import FitMethod
from atlas.report import render_report
from atlas.series import DemandSeries
from tests.m2_helpers import noisy_series

REF = "seasonal_naive"
T_975_DF59 = 2.000995378  # Student-t 97.5 % quantile, 59 degrees of freedom (tables)
BASELINE = ModelFamily.BASELINE
REGRESSION = ModelFamily.REGRESSION

# One synthetic year: scripted predictions are ``actual + e``. No negative scripted error is
# larger than 1,000 in size and the series stays above 6,000, so every prediction is positive.
SERIES = noisy_series(date(2024, 1, 1), 366, seed=11)
TRUTH: dict[date, float] = {
    ts.date(): float(v) for ts, v in SERIES.to_series().items() if not math.isnan(v)
}


# --- Plain-Python references ----------------------------------------------------------------


@dataclass(frozen=True)
class _RefMetrics:
    mae: float
    rmse: float
    bias: float
    bias_pct: float | None
    wape_pct: float | None
    mape_pct: float | None


def _ref_metrics(prediction: Sequence[float], actual: Sequence[float]) -> _RefMetrics:
    errors = [p - a for p, a in zip(prediction, actual, strict=True)]
    n = len(errors)
    total = math.fsum(actual)
    total_abs = math.fsum(abs(a) for a in actual)
    return _RefMetrics(
        mae=math.fsum(abs(e) for e in errors) / n,
        rmse=math.sqrt(math.fsum(e * e for e in errors) / n),
        bias=math.fsum(errors) / n,
        bias_pct=100 * math.fsum(errors) / total if total != 0 else None,
        wape_pct=100 * math.fsum(abs(e) for e in errors) / total_abs if total_abs else None,
        mape_pct=(
            100 * math.fsum(abs(e) / abs(a) for e, a in zip(errors, actual, strict=True)) / n
            if all(a != 0 for a in actual)
            else None
        ),
    )


def _autocovariance(values: Sequence[float], lag: int) -> float:
    size = len(values)
    mean = math.fsum(values) / size
    centered = [v - mean for v in values]
    return math.fsum(centered[i] * centered[i - lag] for i in range(lag, size)) / size


def _ref_lrv(values: Sequence[float], bandwidth: int, kernel: str) -> float:
    total = _autocovariance(values, 0)
    for lag in range(1, min(bandwidth, len(values) - 1) + 1):
        weight = 1.0 if kernel == "rectangular" else 1.0 - lag / (bandwidth + 1)
        total += 2 * weight * _autocovariance(values, lag)
    return total


def _ref_dm_statistic(differences: Sequence[float], horizon: int) -> float:
    """HLN-corrected DM statistic: rectangular kernel at h - 1, Bartlett fallback."""
    size = len(differences)
    mean = math.fsum(differences) / size
    bandwidth = min(horizon - 1, size - 1)
    variance = _ref_lrv(differences, bandwidth, "rectangular")
    if variance <= 0:
        variance = _ref_lrv(differences, bandwidth, "bartlett")
    correction = (size + 1 - 2 * horizon + horizon * (horizon - 1) / size) / size
    return mean / (math.sqrt(variance / size) / math.sqrt(correction))


def _ref_effective_sample_size(values: Sequence[float], overlap: int) -> float:
    size = len(values)
    gamma0 = _autocovariance(values, 0)
    variance = _ref_lrv(values, min(2 * overlap, size - 1), "bartlett")
    return min(float(size), size * gamma0 / variance)


def _ref_t_two_sided_p(statistic: float, df: int) -> float:
    """``P(|T| >= |t|)`` for integer ``df`` (Abramowitz & Stegun 26.7.3 / 26.7.4)."""
    theta = math.atan(abs(statistic) / math.sqrt(df))
    sine, cos2 = math.sin(theta), math.cos(theta) ** 2
    if df % 2 == 1:
        term = total = math.cos(theta)
        for k in range(1, (df - 3) // 2 + 1):
            term *= 2 * k / (2 * k + 1) * cos2
            total += term
        central = 2.0 / math.pi * (theta + (sine * total if df > 1 else 0.0))
    else:
        term = total = 1.0
        for k in range(1, (df - 2) // 2 + 1):
            term *= (2 * k - 1) / (2 * k) * cos2
            total += term
        central = sine * total
    return 1.0 - central


def _ref_t_quantile(probability: float, df: int) -> float:
    """Upper Student-t quantile (``probability > 0.5``) by bisection on ``_ref_t_two_sided_p``."""
    tail = 2.0 * (1.0 - probability)
    low, high = 0.0, 1.0
    while _ref_t_two_sided_p(high, df) > tail:
        high *= 2.0
    for _ in range(200):
        middle = (low + high) / 2.0
        if _ref_t_two_sided_p(middle, df) > tail:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def _ref_hln(size: int, horizon: int) -> float:
    """Harvey-Leybourne-Newbold small-sample factor."""
    return (size + 1 - 2 * horizon + horizon * (horizon - 1) / size) / size


@dataclass(frozen=True)
class _RefDM:
    """The DM test of the ``atlas.stats`` docstring, rebuilt in plain Python."""

    size: int
    mean: float
    estimator: str
    error: float  # HLN standard error: rectangular kernel at h - 1 (Bartlett fallback)
    bartlett_error: float  # Bartlett kernel at 2h, same HLN factor

    @property
    def p_value(self) -> float:
        return _ref_t_two_sided_p(self.mean / self.error, self.size - 1)

    @property
    def bartlett_p_value(self) -> float:
        return _ref_t_two_sided_p(self.mean / self.bartlett_error, self.size - 1)

    @property
    def conservative_p_value(self) -> float:
        return max(self.p_value, self.bartlett_p_value)

    @property
    def basis(self) -> str:
        return "bartlett_2h" if self.bartlett_error > self.error else self.estimator

    def half_width(self, quantile: float) -> float:
        """Half-width of the reported interval: the larger of the two standard errors."""
        return quantile * max(self.error, self.bartlett_error)


def _ref_dm(differences: Sequence[float], horizon: int) -> _RefDM:
    size = len(differences)
    bandwidth = min(horizon - 1, size - 1)
    variance, estimator = _ref_lrv(differences, bandwidth, "rectangular"), "rectangular"
    if variance <= 0:
        variance, estimator = _ref_lrv(differences, bandwidth, "bartlett"), "bartlett_fallback"
    bartlett = _ref_lrv(differences, min(2 * horizon, size - 1), "bartlett")
    correction = _ref_hln(size, horizon)
    return _RefDM(
        size=size,
        mean=math.fsum(differences) / size,
        estimator=estimator,
        error=math.sqrt(variance / size / correction),
        bartlett_error=math.sqrt(bartlett / size / correction),
    )


def _holm_two(p1: float, p2: float) -> tuple[float, float]:
    """Holm adjustment of two p-values, by hand: 2 * smaller, then max with the larger."""
    low, high = sorted((p1, p2))
    adjusted_low = min(1.0, 2 * low)
    adjusted_high = max(adjusted_low, min(1.0, high))
    return (adjusted_low, adjusted_high) if p1 <= p2 else (adjusted_high, adjusted_low)


def _daterange(first: date, last: date) -> list[date]:
    return [first + timedelta(days=k) for k in range((last - first).days + 1)]


# --- Hand-built record frames -----------------------------------------------------------------


def _record(
    model_id: str,
    period: str,
    origin: date,
    horizon: int,
    *,
    status: str = "ok",
    prediction: float = math.nan,
    actual: float = math.nan,
    scale: float = math.nan,
    raw: float | None = None,
) -> dict[str, Any]:
    return {
        "record_type": "historical_backtest",
        "period": period,
        "model_id": model_id,
        "origin": pd.Timestamp(origin),
        "data_cutoff": pd.Timestamp(origin),
        "target_date": pd.Timestamp(origin + timedelta(days=horizon)),
        "horizon": horizon,
        "prediction": prediction,
        "prediction_raw": prediction if raw is None else raw,
        "actual": actual,
        "status": status,
        "failure_stage": "fit" if status == "failed" else None,
        "failure_code": "planted" if status == "failed" else None,
        "failure_message": "planted" if status == "failed" else None,
        "estimable": None,
        "leverage": math.nan,
        "mase_scale": scale,
    }


def _frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame.from_records(rows, columns=list(RECORD_COLUMNS))


# --- Scripted models ------------------------------------------------------------------------


@dataclass(frozen=True)
class Script:
    """A model whose error at (origin, horizon) is fixed by the test (integer)."""

    model_id: str
    family: ModelFamily
    error: Callable[[date, int], int]
    fail_at: frozenset[date] = frozenset()
    fail_horizons: frozenset[int] = frozenset()

    def ok(self, origin: date, horizon: int) -> bool:
        return origin not in self.fail_at and horizon not in self.fail_horizons

    def abs_error(self, origin: date, horizon: int) -> int:
        return abs(self.error(origin, horizon))


@dataclass(frozen=True, eq=False)
class ScriptedForecaster(Forecaster):
    """Prediction = actual + scripted error; fit failures and per-horizon failures planted."""

    script: Script
    truth: Mapping[date, float] = field(default_factory=lambda: TRUTH)

    @property
    def spec(self) -> ModelSpec:
        return ModelSpec(
            self.script.model_id,
            "scripted",
            self.script.family,
            Procedure.DIRECT,
            "Scripted errors (test fixture).",
        )

    def fit(self, history: DemandSeries) -> FittedForecaster:
        if history.end in self.script.fail_at:
            raise ModelFitError(f"planted failure at {history.end}", "planted_failure")
        return _ScriptedFit(self.script, self.truth, history.end)


class _ScriptedFit(FittedForecaster):
    def __init__(self, script: Script, truth: Mapping[date, float], origin: date) -> None:
        self.script = script
        self.truth = truth
        self.origin = origin
        self.diagnostics = FitDiagnostics()

    def forecast(self, steps: int) -> ForecastPath:
        index = target_dates(self.origin, steps)
        values: list[float] = []
        failures: dict[int, Failure] = {}
        for horizon, target in enumerate(index, start=1):
            if horizon in self.script.fail_horizons:
                failures[horizon] = Failure(FailureStage.FORECAST, "planted", "planted")
                values.append(math.nan)
            else:
                values.append(self.truth[target.date()] + self.script.error(self.origin, horizon))
        return ForecastPath(self.origin, pd.Series(values, index=index, dtype="float64"), failures)


@dataclass(frozen=True)
class PeriodSpec:
    name: str
    role: str
    first: date
    last: date

    def origins(self) -> list[date]:
        return _daterange(self.first, self.last)

    def as_config(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "first_origin": self.first.isoformat(),
            "last_origin": self.last.isoformat(),
        }


def experiment(
    model_ids: Sequence[str],
    periods: Sequence[PeriodSpec],
    *,
    max_horizon: int,
    selection: Mapping[str, Any] | None = None,
    sensitivity: Mapping[str, Any] | None = None,
    windows: Sequence[Mapping[str, Any]] = (),
    buckets: Sequence[tuple[str, int, int]] = (("day_1", 1, 1), ("days_2_3", 2, 3)),
    observed_input_diagnostic: bool = False,
) -> ExperimentConfig:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": "test-m2-evaluation",
        "description": "Synthetic evaluation test.",
        "snapshot_id": "synthetic",
        "target_table": "daily_total",
        "max_horizon": max_horizon,
        "min_history_days": 30,
        "observed_input_diagnostic": observed_input_diagnostic,
        "periods": [p.as_config() for p in periods],
        "horizon_buckets": [{"label": b[0], "first": b[1], "last": b[2]} for b in buckets],
        "models": [
            {"type": "seasonal_naive", "model_id": m, "season_days": 7}
            if m == REF
            else {"type": "naive", "model_id": m}
            for m in model_ids
        ],
        "reference_model": REF,
        "comparison_windows": list(windows),
    }
    if selection is not None:
        payload["selection"] = dict(selection)
    if sensitivity is not None:
        payload["sensitivity"] = dict(sensitivity)
    return parse_config(payload)


def selection_rule(
    candidates: Sequence[str],
    *,
    last_horizon: int = 3,
    max_failure_rate: float = 0.0,
    margin: float = 3.0,
    alpha: float = 0.05,
) -> dict[str, Any]:
    return {
        "period": "dev",
        "candidates": list(candidates),
        "first_horizon": 1,
        "last_horizon": last_horizon,
        "max_failure_rate": max_failure_rate,
        "regression_margin_pct": margin,
        "alpha": alpha,
    }


def run_scripts(
    scripts: Sequence[Script],
    periods: Sequence[PeriodSpec],
    *,
    max_horizon: int = 3,
    **options: Any,
) -> tuple[ExperimentConfig, BacktestResult]:
    config = experiment([s.model_id for s in scripts], periods, max_horizon=max_horizon, **options)
    models = [ScriptedForecaster(s) for s in scripts]
    return config, run_backtest(SERIES, config.plan(), models)


@dataclass(frozen=True)
class RefRecord:
    period: str
    role: str
    model_id: str
    origin: date
    horizon: int
    ok: bool
    abs_error: int

    @property
    def target(self) -> date:
        return self.origin + timedelta(days=self.horizon)


def ref_records(
    scripts: Sequence[Script], periods: Sequence[PeriodSpec], max_horizon: int
) -> list[RefRecord]:
    return [
        RefRecord(p.name, p.role, s.model_id, o, h, s.ok(o, h), s.abs_error(o, h))
        for p in periods
        for o in p.origins()
        for s in scripts
        for h in range(1, max_horizon + 1)
    ]


def ref_common_mae(
    scripts: Mapping[str, Script], models: Sequence[str], origins: Sequence[date], hs: range
) -> tuple[dict[str, float], int]:
    """MAE of each model on the (origin, horizon) pairs on which every listed model is ok."""
    pairs = [(o, h) for o in origins for h in hs if all(scripts[m].ok(o, h) for m in models)]
    maes = {m: sum(scripts[m].abs_error(o, h) for o, h in pairs) / len(pairs) for m in models}
    return maes, len(pairs)


def constant(value: int) -> Callable[[date, int], int]:
    return lambda _origin, _horizon: value


DEV_60 = PeriodSpec("dev", "selection", date(2024, 3, 1), date(2024, 4, 29))  # 60 origins
DEV_20 = PeriodSpec("dev", "selection", date(2024, 3, 1), date(2024, 3, 20))
DEV_10 = PeriodSpec("dev", "selection", date(2024, 3, 1), date(2024, 3, 10))


def _block_differentials(seed: int) -> list[int]:
    """60 per-origin loss differentials: drift -6, a +-25 level held for 6 origins, noise.

    The 6-origin blocks keep the differential autocorrelated beyond lag ``h - 1``, so the
    Bartlett-2h standard error can be the larger one (the review's mutant #12 series).
    """
    rng = random.Random(seed)
    blocks = [rng.choice([-1, 1]) for _ in range(12)]
    return [-6 + 25 * blocks[i // 6] + rng.randint(-10, 10) for i in range(60)]


def _differential_scripts(differentials: Sequence[int]) -> list[Script]:
    """On DEV_60, ``base_b`` has ``|e| = 100`` and ``reg_r`` ``|e| = 100 + d(origin)``.

    Every horizon of an origin has the same differential, so the per-origin loss
    differential of any scope is ``d(origin)``.
    """
    assert min(differentials) > -100  # reg_r's error stays positive
    by_origin = dict(zip(DEV_60.origins(), differentials, strict=True))
    return [
        Script(REF, BASELINE, constant(400)),
        Script("base_b", BASELINE, constant(100)),
        Script("reg_r", REGRESSION, lambda o, _h: 100 + by_origin[o]),
    ]


# ===========================================================================================
# metrics.error_metrics
# ===========================================================================================


def test_error_metrics_hand_example() -> None:
    # e = pred - actual = [2, -2, 10, 0]; |e| = [2, 2, 10, 0]; sum(actual) = 75
    metrics = error_metrics([12, 18, 50, 5], [10, 20, 40, 5], [2, 1, 4, 8])
    assert metrics.n == 4
    assert metrics.mae == 3.5  # 14 / 4
    assert metrics.rmse == math.sqrt(27.0)  # (4 + 4 + 100 + 0) / 4 = 27
    assert metrics.bias == 2.5  # 10 / 4, positive = over-forecast
    assert metrics.bias_pct == pytest.approx(100 * 10 / 75, rel=1e-15)
    assert metrics.wape_pct == pytest.approx(100 * 14 / 75, rel=1e-15)
    assert metrics.mape_pct == pytest.approx(100 * (0.2 + 0.1 + 0.25 + 0.0) / 4, rel=1e-15)
    # Per-forecast scales: mean(2/2, 2/1, 10/4, 0/8) = 5.5 / 4; a pooled ratio
    # mean|e| / mean(scale) would give 3.5 / 3.75 instead.
    assert metrics.mase == 1.375


@pytest.mark.parametrize(
    ("prediction", "actual", "bias", "bias_pct"),
    [([110.0], [100.0], 10.0, 10.0), ([90.0], [100.0], -10.0, -10.0), ([5.0], [5.0], 0.0, 0.0)],
)
def test_bias_is_prediction_minus_actual(
    prediction: list[float], actual: list[float], bias: float, bias_pct: float
) -> None:
    metrics = error_metrics(prediction, actual)
    assert metrics.bias == bias
    assert metrics.bias_pct == bias_pct
    assert metrics.mae == abs(bias)


def test_error_metrics_of_nothing_is_none_not_zero() -> None:
    metrics = error_metrics([], [], [])
    assert metrics.to_dict() == {
        "n": 0,
        "mae": None,
        "rmse": None,
        "bias": None,
        "bias_pct": None,
        "wape_pct": None,
        "mase": None,
        "mape_pct": None,
    }


def test_zero_actual_sum_makes_bias_pct_undefined_but_not_wape() -> None:
    # sum(actual) = 0 but sum(|actual|) = 10; no actual is zero, so MAPE is defined.
    metrics = error_metrics([-3.0, 9.0], [-5.0, 5.0])
    assert metrics.bias_pct is None
    assert metrics.wape_pct == 60.0  # 100 * (2 + 4) / 10
    assert metrics.mape_pct == pytest.approx(60.0, rel=1e-15)  # 100 * (2/5 + 4/5) / 2
    assert metrics.mae == 3.0


def test_bias_pct_divides_by_the_signed_sum_and_wape_by_the_absolute_sum() -> None:
    # e = [2, 4]; sum(actual) = 10 but sum(|actual|) = 20.
    metrics = error_metrics([-3.0, 19.0], [-5.0, 15.0])
    assert metrics.bias_pct == 60.0  # 100 * 6 / 10
    assert metrics.wape_pct == 30.0  # 100 * 6 / 20


def test_all_zero_actuals_make_every_ratio_undefined() -> None:
    metrics = error_metrics([1.0, 2.0], [0.0, 0.0])
    assert (metrics.bias_pct, metrics.wape_pct, metrics.mape_pct) == (None, None, None)
    assert (metrics.mae, metrics.bias) == (1.5, 1.5)


def test_one_zero_actual_disables_mape_only() -> None:
    metrics = error_metrics([1.0, 12.0], [0.0, 10.0])
    assert metrics.mape_pct is None
    assert metrics.wape_pct == 30.0  # 100 * 3 / 10
    assert metrics.bias_pct == 30.0
    assert metrics.mae == 1.5


@pytest.mark.parametrize(
    "scale",
    [None, [1.0, 0.0], [1.0, math.nan], [1.0, -2.0], [math.inf, 1.0]],
    ids=["absent", "zero", "nan", "negative", "infinite"],
)
def test_mase_is_none_when_any_scale_is_unusable(scale: list[float] | None) -> None:
    metrics = error_metrics([11.0, 12.0], [10.0, 10.0], scale)
    assert metrics.mase is None
    assert metrics.mae == 1.5  # the other metrics are unaffected


@pytest.mark.parametrize(
    ("prediction", "actual", "scale"),
    [
        ([1.0, 2.0], [1.0], None),
        ([[1.0], [2.0]], [[1.0], [2.0]], None),
        ([1.0, 2.0], [1.0, 2.0], [1.0]),
    ],
    ids=["lengths", "two_dimensional", "scale_length"],
)
def test_error_metrics_rejects_misaligned_inputs(
    prediction: list[Any], actual: list[Any], scale: list[float] | None
) -> None:
    with pytest.raises(ForecastError, match=r"align|equal length"):
        error_metrics(prediction, actual, scale)


@pytest.mark.parametrize(
    ("prediction", "actual"),
    [([math.nan], [1.0]), ([1.0], [math.nan]), ([math.inf], [1.0]), ([1.0], [-math.inf])],
)
def test_error_metrics_rejects_non_finite_values(
    prediction: list[float], actual: list[float]
) -> None:
    with pytest.raises(ForecastError, match="finite"):
        error_metrics(prediction, actual)


@settings(max_examples=20, deadline=None)
@given(
    st.lists(
        st.tuples(st.integers(0, 20_000), st.integers(1, 20_000), st.integers(1, 500)),
        min_size=1,
        max_size=40,
    )
)
def test_error_metrics_match_plain_python_reference(rows: list[tuple[int, int, int]]) -> None:
    prediction = [float(r[0]) for r in rows]
    actual = [float(r[1]) for r in rows]
    scale = [float(r[2]) for r in rows]
    metrics = error_metrics(prediction, actual, scale)
    expected = _ref_metrics(prediction, actual)
    assert metrics.n == len(rows)
    assert metrics.mae == pytest.approx(expected.mae, rel=1e-12, abs=1e-12)
    assert metrics.rmse == pytest.approx(expected.rmse, rel=1e-12, abs=1e-12)
    assert metrics.bias == pytest.approx(expected.bias, rel=1e-12, abs=1e-9)
    assert metrics.bias_pct == pytest.approx(expected.bias_pct, rel=1e-12, abs=1e-9)
    assert metrics.wape_pct == pytest.approx(expected.wape_pct, rel=1e-12, abs=1e-12)
    assert metrics.mape_pct == pytest.approx(expected.mape_pct, rel=1e-12, abs=1e-12)
    expected_mase = math.fsum(
        abs(p - a) / s for p, a, s in zip(prediction, actual, scale, strict=True)
    ) / len(rows)
    assert metrics.mase == pytest.approx(expected_mase, rel=1e-12, abs=1e-12)
    assert metrics.rmse is not None
    assert metrics.mae is not None
    assert metrics.rmse >= metrics.mae - 1e-9


# ===========================================================================================
# metrics.seasonal_naive_scale, contiguous, per_origin_mean, effective_sample_size, scopes
# ===========================================================================================


def _daily(values: Sequence[float]) -> pd.Series:
    return pd.Series(
        values, index=pd.date_range("2024-01-01", periods=len(values), freq="D"), dtype="float64"
    )


def test_seasonal_naive_scale_by_hand_skips_pairs_with_a_missing_value() -> None:
    nan = math.nan
    history = _daily([10, 20, 30, nan, 50, 60, 70, 12, 25, 30, 46, nan, 66, 71])
    # Pairs (t, t-7): |12-10|=2, |25-20|=5, |30-30|=0, (46, nan) and (nan, 50) skipped,
    # |66-60|=6, |71-70|=1 -> (2 + 5 + 0 + 6 + 1) / 5.
    assert seasonal_naive_scale(history) == 2.8


def test_seasonal_naive_scale_linear_series_and_other_season() -> None:
    assert seasonal_naive_scale(_daily(range(1000, 1050))) == 7.0
    # season 2 on [1, 4, 2, 8, 3]: |2-1| = 1, |8-4| = 4, |3-2| = 1 -> 2
    assert seasonal_naive_scale(_daily([1, 4, 2, 8, 3]), season=2) == 2.0


@pytest.mark.parametrize(
    "values",
    [
        [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],  # no pair at all
        [1.0, math.nan, 3.0, 4.0, 5.0, 6.0, 7.0, math.nan, 9.0],  # (nan, 1) / (9, nan): none
        [5.0] * 20,  # scale 0: MASE undefined
    ],
    ids=["too_short", "no_complete_pair", "zero_scale"],
)
def test_seasonal_naive_scale_none_cases(values: list[float]) -> None:
    assert seasonal_naive_scale(_daily(values)) is None


def test_seasonal_naive_scale_needs_more_than_one_season() -> None:
    # Exactly 8 values: one pair |9 - 1| = 8.
    assert seasonal_naive_scale(_daily([1, 2, 3, 4, 5, 6, 7, 9])) == 8.0


@pytest.mark.parametrize(
    ("origins", "stride", "expected"),
    [
        ([], 1, True),
        (["2024-01-01"], 3, True),
        (["2024-01-01", "2024-01-02", "2024-01-03"], 1, True),
        (["2024-01-03", "2024-01-01", "2024-01-02"], 1, True),  # sorted first
        (["2024-01-01", "2024-01-02", "2024-01-04"], 1, False),  # gap
        (["2024-01-01", "2024-01-02", "2024-01-03"], 2, False),  # wrong stride
        (["2024-01-01", "2024-01-08", "2024-01-15"], 7, True),
        (["2024-01-01", "2024-01-08", "2024-01-22"], 7, False),
    ],
)
def test_contiguous(origins: list[str], stride: int, expected: bool) -> None:
    assert contiguous(pd.DatetimeIndex(origins), stride) is expected


def test_per_origin_mean_keeps_only_complete_origins() -> None:
    o1, o2, o3 = (pd.Timestamp(f"2024-01-0{d}") for d in (1, 2, 3))
    cell = pd.DataFrame(
        {"origin": [o2, o1, o1, o3, o1, o3, o2, o3], "horizon": [1, 1, 2, 1, 3, 2, 3, 3]},
        index=[10, 11, 12, 13, 14, 15, 16, 17],
    )
    values = pd.Series([4.0, 1.0, 2.0, 5.0, 6.0, 7.0, 8.0, 9.0], index=cell.index)
    result = per_origin_mean(cell, values, 3)
    # o1: (1 + 2 + 6) / 3 = 3; o2 has two horizons only (dropped); o3: (5 + 7 + 9) / 3 = 7.
    assert list(result.index) == [o1, o3]
    assert list(result.to_numpy()) == [3.0, 7.0]


def test_effective_sample_size_iid_close_to_t_and_ar1_much_smaller() -> None:
    rng = np.random.default_rng(20240101)
    noise = rng.normal(0.0, 1.0, 400)
    ess_iid = effective_sample_size(noise, 1)
    assert ess_iid is not None
    assert ess_iid == pytest.approx(_ref_effective_sample_size(noise.tolist(), 1), rel=1e-9)
    assert 0.8 * 400 <= ess_iid <= 400
    ar = np.zeros(400)
    for t in range(1, 400):
        ar[t] = 0.95 * ar[t - 1] + noise[t]
    ess_ar = effective_sample_size(ar, 10)
    assert ess_ar is not None
    assert ess_ar == pytest.approx(_ref_effective_sample_size(ar.tolist(), 10), rel=1e-9)
    assert ess_ar < 0.2 * 400


def test_effective_sample_size_is_capped_at_t_for_anticorrelated_values() -> None:
    values = np.array([1.0, -1.0] * 30)
    # Negative autocorrelation makes T * gamma0 / LRV exceed T; the result is capped.
    assert effective_sample_size(values, 1) == 60.0


@pytest.mark.parametrize("values", [[], [3.0], [2.0, 2.0, 2.0]], ids=["empty", "one", "flat"])
def test_effective_sample_size_none_cases(values: list[float]) -> None:
    assert effective_sample_size(np.asarray(values, dtype="float64"), 1) is None


@settings(max_examples=20, deadline=None)
@given(
    st.lists(st.floats(-1e3, 1e3, allow_nan=False), min_size=2, max_size=60),
    st.integers(1, 10),
)
def test_effective_sample_size_never_exceeds_t(values: list[float], overlap: int) -> None:
    ess = effective_sample_size(np.asarray(values, dtype="float64"), overlap)
    if ess is not None:
        assert 0 < ess <= len(values)


def test_dependence_test_overlap_divides_by_stride_and_refuses_gaps() -> None:
    rng = random.Random(3)
    weekly = pd.date_range("2020-01-06", periods=60, freq="7D")
    series = pd.Series([rng.uniform(-5, 5) for _ in range(60)], index=weekly)
    test, effective = dependence_test(series, horizon=30, stride_days=7)
    assert test.horizon == 5  # ceil(30 / 7) overlapping origin steps
    assert test.computed
    assert test.statistic == pytest.approx(_ref_dm_statistic(series.tolist(), 5), rel=1e-9)
    assert effective == pytest.approx(_ref_effective_sample_size(series.tolist(), 5), rel=1e-9)
    gappy = series.drop(series.index[10])
    test, _ = dependence_test(gappy, horizon=30, stride_days=7)
    assert not test.computed
    assert test.reason == "origin grid has gaps"
    assert test.p_value is None


def test_scopes_and_buckets() -> None:
    buckets = (HorizonBucket("day_1", 1, 1), HorizonBucket("days_2_3", 2, 3))
    assert scopes(buckets, 3) == [
        ("horizon", "h01", 1, 1),
        ("horizon", "h02", 2, 2),
        ("horizon", "h03", 3, 3),
        ("bucket", "day_1", 1, 1),
        ("bucket", "days_2_3", 2, 3),
        ("bucket", "days_1_3", 1, 3),
    ]
    assert scopes(buckets, 3, horizons=False) == [
        ("bucket", "day_1", 1, 1),
        ("bucket", "days_2_3", 2, 3),
        ("bucket", "days_1_3", 1, 3),
    ]
    assert buckets[1].contains(2)
    assert buckets[1].contains(3)
    assert not buckets[1].contains(1)
    assert not buckets[1].contains(4)
    for first, last in ((0, 1), (3, 2)):
        with pytest.raises(ConfigError, match="invalid horizon bucket"):
            HorizonBucket("bad", first, last)


# ===========================================================================================
# metrics.summarize
# ===========================================================================================

O1, O2, O3 = date(2024, 1, 10), date(2024, 1, 11), date(2024, 1, 12)
BUCKETS_3 = (HorizonBucket("day_1", 1, 1), HorizonBucket("days_2_3", 2, 3))


def _summary_records() -> pd.DataFrame:
    rows = [
        # origin O1 (scale 10): h1 e=+10, h2 e=-5, h3 failed
        _record("m", "dev", O1, 1, prediction=110, actual=100, scale=10),
        _record("m", "dev", O1, 2, prediction=95, actual=100, scale=10),
        _record("m", "dev", O1, 3, status="failed", actual=100, scale=10),
        # origin O2 (scale 20): h1 e=-20, h2 missing actual, h3 e=+50
        _record("m", "dev", O2, 1, prediction=100, actual=120, scale=20),
        _record("m", "dev", O2, 2, status="missing_actual", prediction=130, scale=20),
        _record("m", "dev", O2, 3, prediction=150, actual=100, scale=20),
        # origin O3 (scale 4): h1 failed, h2 e=+4, h3 beyond the snapshot
        _record("m", "dev", O3, 1, status="failed", actual=100, scale=4),
        _record("m", "dev", O3, 2, prediction=104, actual=100, scale=4),
        _record("m", "dev", O3, 3, status="beyond_snapshot", prediction=90, scale=4),
    ]
    # A second model and period with huge errors: must not leak into model m's rows.
    rows += [
        _record(model, period, O1, h, prediction=5000, actual=100, scale=1)
        for model, period in (("other", "dev"), ("m", "conf"))
        for h in (1, 2, 3)
    ]
    return _frame(rows)


def test_summarize_counts_statuses_and_scores_only_ok_records() -> None:
    rows = summarize(_summary_records(), BUCKETS_3, 3)
    keys = [(r["model_id"], r["period"], r["scope"], r["label"]) for r in rows]
    assert len(keys) == len(set(keys)) == 3 * 6
    cells = {(r["model_id"], r["period"], r["label"]): r for r in rows}
    # (label, scheduled, origins, predictions, failed, missing, beyond, [(pred, act, scale)])
    expected = [
        ("h01", 3, 2, 2, 1, 0, 0, [(110, 100, 10), (100, 120, 20)]),
        ("h02", 3, 2, 2, 0, 1, 0, [(95, 100, 10), (104, 100, 4)]),
        ("h03", 3, 1, 1, 1, 0, 1, [(150, 100, 20)]),
        ("day_1", 3, 2, 2, 1, 0, 0, [(110, 100, 10), (100, 120, 20)]),
        ("days_2_3", 6, 3, 3, 1, 1, 1, [(95, 100, 10), (104, 100, 4), (150, 100, 20)]),
        (
            "days_1_3",
            9,
            3,
            5,
            2,
            1,
            1,
            [(110, 100, 10), (100, 120, 20), (95, 100, 10), (104, 100, 4), (150, 100, 20)],
        ),
    ]
    for label, scheduled, origins, predictions, failed, missing, beyond, ok in expected:
        row = cells[("m", "dev", label)]
        assert row["n_scheduled"] == scheduled, label
        assert row["n_origins"] == origins, label
        assert row["n_predictions"] == row["n"] == predictions, label
        assert row["n_failed"] == failed, label
        assert row["n_missing_actual"] == missing, label
        assert row["n_beyond_snapshot"] == beyond, label
        reference = _ref_metrics([p for p, _, _ in ok], [a for _, a, _ in ok])
        assert row["mae"] == pytest.approx(reference.mae, rel=1e-15), label
        assert row["rmse"] == pytest.approx(reference.rmse, rel=1e-15), label
        assert row["bias"] == pytest.approx(reference.bias, rel=1e-15), label
        assert row["bias_pct"] == pytest.approx(reference.bias_pct, rel=1e-15), label
        assert row["wape_pct"] == pytest.approx(reference.wape_pct, rel=1e-15), label
        assert row["mape_pct"] == pytest.approx(reference.mape_pct, rel=1e-15), label
        mase = sum(abs(p - a) / s for p, a, s in ok) / len(ok)
        assert row["mase"] == pytest.approx(mase, rel=1e-15), label
    # Literal hand values for the all-horizon bucket: |e| = 10, 20, 5, 4, 50.
    whole = cells[("m", "dev", "days_1_3")]
    assert whole["mae"] == 17.8  # 89 / 5
    assert whole["bias"] == 7.8  # (10 - 20 - 5 + 4 + 50) / 5
    assert whole["bias_pct"] == 7.5  # 100 * 39 / 520
    assert whole["mase"] == 1.2  # (1 + 1 + 0.5 + 1 + 2.5) / 5
    assert (whole["first_horizon"], whole["last_horizon"], whole["scope"]) == (1, 3, "bucket")
    # Too few origins for an interval on the bias.
    assert whole["bias_ci_low"] is None
    assert whole["bias_ci_high"] is None
    assert "bias_ci_low" not in cells[("m", "dev", "h01")]
    # The other model and period are summarised on their own.
    assert cells[("other", "dev", "days_1_3")]["mae"] == 4900.0
    assert cells[("m", "conf", "days_1_3")]["n_predictions"] == 3


def test_summarize_bias_interval_is_the_t_interval_for_one_step_errors() -> None:
    rng = random.Random(8)
    start = date(2024, 2, 1)
    errors = [rng.randint(-40, 60) for _ in range(60)]
    frame = _frame(
        [
            _record("m", "dev", start + timedelta(days=k), 1, prediction=500 + e, actual=500)
            for k, e in enumerate(errors)
        ]
    )
    row = next(
        r
        for r in summarize(frame, (HorizonBucket("day_1", 1, 1),), 1, {"dev": 1})
        if r["label"] == "day_1"
    )
    # h = 1: no autocovariance terms, HLN factor (T - 1) / T -> mean +- t * s / sqrt(T).
    mean = statistics.fmean(errors)
    half = T_975_DF59 * statistics.stdev(errors) / math.sqrt(60)
    assert row["bias"] == pytest.approx(mean, rel=1e-12)
    assert row["bias_ci_low"] == pytest.approx(mean - half, rel=1e-6)
    assert row["bias_ci_high"] == pytest.approx(mean + half, rel=1e-6)


def test_summarize_bias_interval_of_a_multi_horizon_bucket_uses_its_last_horizon() -> None:
    """Review mutant F8/F21: a bucket's bias interval allows for overlap = its LAST horizon.

    Errors depend on the target day, so neighbouring origins share errors and the overlap
    matters. Reference: per-origin mean error over the bucket (complete origins), HLN DM
    with the rectangular kernel at ``last - 1``, HLN factor with ``h = last``, ``t(59)``,
    and the larger of that standard error and the Bartlett-``2 * last`` one.
    """

    def error(origin: date, horizon: int) -> int:
        target = origin + timedelta(days=horizon)
        return random.Random(target.toordinal()).randint(0, 60) - 30

    scripts = [Script(REF, BASELINE, constant(400)), Script("m", BASELINE, error)]
    config, result = run_scripts(scripts, [DEV_60])
    rows = {
        r["label"]: r
        for r in summarize(result.records, config.buckets(), 3, {"dev": 1})
        if r["model_id"] == "m"
    }
    for label, first, last in (("days_2_3", 2, 3), ("days_1_3", 1, 3)):
        per_origin = [
            statistics.fmean(error(o, h) for h in range(first, last + 1)) for o in DEV_60.origins()
        ]
        reference = _ref_dm(per_origin, last)
        half = reference.half_width(T_975_DF59)
        row = rows[label]
        assert row["bias"] == pytest.approx(reference.mean, rel=1e-12, abs=1e-12), label
        assert row["bias_ci_low"] == pytest.approx(reference.mean - half, rel=1e-6), label
        assert row["bias_ci_high"] == pytest.approx(reference.mean + half, rel=1e-6), label
        # The overlap matters here: the bucket's first horizon would give another width.
        wrong = _ref_dm(per_origin, first).half_width(T_975_DF59)
        assert abs(wrong - half) > 0.01 * half, label


# ===========================================================================================
# metrics.paired / compare / window_summary
# ===========================================================================================


def _paired_records() -> pd.DataFrame:
    start = date(2024, 3, 1)
    origins = [start + timedelta(days=k) for k in range(6)]
    rows = []
    for k, origin in enumerate(origins):
        hard = k >= 4  # the last two origins are the hard cases
        for h in (1, 2):
            actual = 1000.0
            # reference: -10 on normal pairs, +1000 on hard pairs; fails at (o4, h2)
            if k == 3 and h == 2:
                rows.append(_record("ref", "dev", origin, h, status="failed", actual=actual))
            else:
                ref_error = 1000 if hard else -10
                rows.append(
                    _record(
                        "ref",
                        "dev",
                        origin,
                        h,
                        prediction=actual + ref_error,
                        actual=actual,
                        scale=5,
                    )
                )
            # model a: +15 where it forecasts; failed on the hard pairs; no actual at (o3, h1)
            if hard:
                rows.append(_record("a", "dev", origin, h, status="failed", actual=actual))
            elif k == 2 and h == 1:
                rows.append(
                    _record("a", "dev", origin, h, status="missing_actual", prediction=1015)
                )
            else:
                rows.append(
                    _record("a", "dev", origin, h, prediction=actual + 15, actual=actual, scale=5)
                )
    # Another period on the same origins: pairs must never cross periods.
    rows.append(_record("a", "bridge", origins[0], 1, prediction=1100, actual=1000, scale=5))
    rows.append(_record("ref", "bridge", origins[0], 1, prediction=1040, actual=1000, scale=5))
    rows.append(_record("ref", "conf", origins[1], 1, prediction=1000, actual=1000, scale=5))
    return _frame(rows)


def test_paired_keeps_only_pairs_both_models_scored_in_the_same_period() -> None:
    joined = paired(_paired_records(), "a", "ref")
    start = date(2024, 3, 1)
    expected = {
        ("dev", start, 1),
        ("dev", start, 2),
        ("dev", start + timedelta(days=1), 1),
        ("dev", start + timedelta(days=1), 2),
        ("dev", start + timedelta(days=2), 2),
        ("dev", start + timedelta(days=3), 1),
        ("bridge", start, 1),
    }
    got = {
        (p, pd.Timestamp(o).date(), int(h))
        for p, o, h in joined[["period", "origin", "horizon"]].itertuples(index=False)
    }
    assert got == expected
    assert len(joined) == len(expected)
    dev = joined[joined["period"] == "dev"]
    assert set(dev["prediction"] - dev["actual"]) == {15.0}
    assert set(dev["prediction_ref"] - dev["actual"]) == {-10.0}


def test_compare_is_on_common_pairs_so_skipping_hard_cases_earns_nothing() -> None:
    rows = compare(_paired_records(), "a", "ref", (HorizonBucket("day_1", 1, 1),), 2)
    cells = {(r["period"], r["label"]): r for r in rows}
    whole = cells[("dev", "days_1_2")]
    # Common dev pairs: 6, where a's |e| = 15 and ref's |e| = 10. Over each model's own
    # forecasts ref's MAE would be (7 * 10 + 4 * 1000) / 11 and a would look far better.
    assert whole["n_common"] == 6
    assert whole["n_origins"] == 4
    assert whole["mae"] == 15.0
    assert whole["mae_reference"] == 10.0
    assert whole["mae_difference"] == 5.0
    assert whole["mae_improvement_pct"] == pytest.approx(-50.0, rel=1e-15)
    assert whole["rmse_improvement_pct"] == pytest.approx(-50.0, rel=1e-15)
    assert (whole["mase"], whole["mase_reference"]) == (3.0, 2.0)
    assert cells[("dev", "h01")]["n_common"] == 3  # o1, o2, o4
    assert cells[("dev", "h02")]["n_common"] == 3  # o1, o2, o3
    assert cells[("dev", "day_1")]["n_common"] == 3
    # Only origins with every horizon of the scope paired enter the DM test.
    assert whole["dm_n_origins"] == 2
    assert cells[("dev", "h01")]["dm_n_origins"] == 3
    # The bridge period is compared on its own.
    bridge = cells[("bridge", "days_1_2")]
    assert (bridge["n_common"], bridge["mae"], bridge["mae_reference"]) == (1, 100.0, 40.0)
    assert ("conf", "days_1_2") not in cells  # model a has no conf forecasts


def test_compare_improvement_sign_and_dm_fields_with_too_few_origins() -> None:
    rows = compare(_paired_records(), "ref", "a", (HorizonBucket("day_1", 1, 1),), 2)
    whole = next(r for r in rows if r["period"] == "dev" and r["label"] == "days_1_2")
    assert whole["mae_improvement_pct"] == pytest.approx(100 * (1 - 10 / 15), rel=1e-15)
    assert whole["mae_difference"] == -5.0
    assert whole["dm_computed"] is False
    assert whole["dm_reason"] == "fewer than 50 origins"
    for key in (
        "dm_statistic",
        "dm_p_value",
        "dm_bartlett_p_value",
        "dm_p_value_conservative",
        "mae_difference_ci_low",
        "mae_difference_ci_high",
        "mae_improvement_ci_low_pct",
        "mae_improvement_ci_high_pct",
    ):
        assert whole[key] is None, key


def test_compare_dm_uses_per_origin_means_over_the_scope() -> None:
    rng = random.Random(42)
    start = date(2024, 2, 1)
    rows = []
    differences: dict[int, list[float]] = {}
    for k in range(61):
        origin = start + timedelta(days=k)
        for h in (1, 2, 3):
            model_error = rng.randint(-80, 80)
            ref_error = rng.randint(-120, 120)
            if k == 60 and h == 3:  # last origin incomplete for the model: no DM entry
                rows.append(_record("a", "dev", origin, h, status="failed", actual=900))
            else:
                rows.append(
                    _record("a", "dev", origin, h, prediction=900 + model_error, actual=900)
                )
                differences.setdefault(k, []).append(abs(model_error) - abs(ref_error))
            rows.append(_record("ref", "dev", origin, h, prediction=900 + ref_error, actual=900))
    rows_out = compare(_frame(rows), "a", "ref", (HorizonBucket("days_2_3", 2, 3),), 3, {"dev": 1})
    cells = {r["label"]: r for r in rows_out}
    per_origin = [statistics.fmean(differences[k]) for k in range(60)]
    whole = cells["days_1_3"]
    assert whole["n_common"] == 182
    assert whole["dm_n_origins"] == 60
    assert whole["dm_computed"] is True
    assert whole["dm_estimator"] in {"rectangular", "bartlett_fallback"}
    assert whole["dm_statistic"] == pytest.approx(_ref_dm_statistic(per_origin, 3), rel=1e-9)
    # The CI of the MAE difference is centred on the mean per-origin differential.
    centre = (whole["mae_difference_ci_low"] + whole["mae_difference_ci_high"]) / 2
    assert centre == pytest.approx(statistics.fmean(per_origin), rel=1e-9, abs=1e-9)
    p = whole["dm_p_value_conservative"]
    assert p == max(whole["dm_p_value"], whole["dm_bartlett_p_value"])
    # Horizon 2..3 bucket: overlap 3 (its last horizon), origins complete on h2 and h3.
    bucket = cells["days_2_3"]
    assert bucket["dm_n_origins"] == 60
    bucket_diffs = [statistics.fmean(differences[k][1:3]) for k in range(60)]
    assert bucket["dm_statistic"] == pytest.approx(_ref_dm_statistic(bucket_diffs, 3), rel=1e-9)
    # Single horizon 1: overlap 1, all 61 origins.
    h1 = cells["h01"]
    assert h1["dm_n_origins"] == 61
    h1_diffs = [differences[k][0] for k in range(61)]
    assert h1["dm_statistic"] == pytest.approx(_ref_dm_statistic(h1_diffs, 1), rel=1e-9)
    assert h1["effective_sample_size"] == pytest.approx(
        _ref_effective_sample_size(h1_diffs, 1), rel=1e-9
    )


def _square_wave_differentials(seed: int) -> list[int]:
    """Drift -8, +-30 alternating every 3 origins, noise.

    The negative autocovariances at lags 3 to 6 shrink the Bartlett-2h estimate, so the
    rectangular (``h - 1``) standard error is the larger one for the 3-horizon scopes.
    """
    rng = random.Random(seed)
    return [-8 + (30 if (i // 3) % 2 else -30) + rng.randint(-10, 10) for i in range(60)]


def _iid_differentials(seed: int, drift: int) -> list[int]:
    rng = random.Random(seed)
    return [drift + rng.randint(-40, 40) for _ in range(60)]


def test_compare_interval_excludes_zero_exactly_when_the_conservative_p_is_below_5pct() -> None:
    """Review CI consistency: the MAE-difference interval uses the larger DM standard error.

    For every compare() row, ``[ci_low, ci_high]`` excludes 0 exactly when
    ``dm_p_value_conservative < 0.05``, and ``dm_ci_basis`` names the wider standard error.
    The cases cover both bases and the critical one where only the rectangular test
    rejects (an interval from the rectangular standard error alone would exclude 0).
    """
    cases = {
        "blocks": _block_differentials(737),
        "square_wave": _square_wave_differentials(5),
        "null": _iid_differentials(1, 0),
        "strong": _iid_differentials(2, -25),
    }
    bases: set[str] = set()
    critical_rows = 0
    for name, differentials in cases.items():
        config, result = run_scripts(_differential_scripts(differentials), [DEV_60])
        rows = compare(result.records, "reg_r", "base_b", config.buckets(), 3, {"dev": 1})
        assert len(rows) == 6, name
        for row in rows:
            where = (name, row["label"])
            # The per-origin differential is d(origin) in every scope; overlap = last horizon.
            reference = _ref_dm(differentials, row["last_horizon"])
            assert row["dm_computed"] is True, where
            assert row["dm_ci_basis"] == reference.basis, where
            assert row["dm_p_value"] == pytest.approx(reference.p_value, rel=1e-6, abs=1e-12)
            assert row["dm_p_value_conservative"] == pytest.approx(
                reference.conservative_p_value, rel=1e-6, abs=1e-12
            ), where
            half = reference.half_width(T_975_DF59)
            assert row["mae_difference"] == pytest.approx(reference.mean, rel=1e-12, abs=1e-9)
            assert row["mae_difference_ci_low"] == pytest.approx(reference.mean - half, rel=1e-6)
            assert row["mae_difference_ci_high"] == pytest.approx(reference.mean + half, rel=1e-6)
            excludes_zero = row["mae_difference_ci_low"] > 0 or row["mae_difference_ci_high"] < 0
            assert excludes_zero is (row["dm_p_value_conservative"] < 0.05), where
            bases.add(row["dm_ci_basis"])
            if reference.p_value < 0.05 <= reference.conservative_p_value:
                critical_rows += 1
    assert bases == {"rectangular", "bartlett_2h"}
    assert critical_rows > 0


def test_window_summary_unrounded_metrics_clip_and_count_targets() -> None:
    o1, o2 = date(2026, 4, 1), date(2026, 4, 2)
    rows = [
        # target 04-02 (actual 100): rounded 101, raw 100.6
        _record("leg", "bridge", o1, 1, prediction=101, raw=100.6, actual=100, scale=2),
        # target 04-03 (actual 10): rounded 12, raw 12.4
        _record("leg", "bridge", o1, 2, prediction=12, raw=12.4, actual=10, scale=2),
        # target 04-03 (actual 10): raw -3 is clipped to 0 before scoring
        _record("leg", "bridge", o2, 1, prediction=0, raw=-3.0, actual=10, scale=2),
        # target 04-04: outside the window
        _record("leg", "bridge", o2, 2, prediction=50, raw=50, actual=40, scale=2),
        # not ok: excluded
        _record("other", "bridge", o1, 1, status="missing_actual", prediction=7),
        _record("other", "bridge", o2, 1, status="failed", actual=10),
        # same target and horizon, another period
        _record("leg", "dev", o1, 1, prediction=104, raw=104, actual=100, scale=2),
    ]
    frame = _frame(rows)
    window = {"first_target": pd.Timestamp("2026-04-02"), "last_target": pd.Timestamp("2026-04-03")}
    result = window_summary(frame, period="bridge", **window)
    assert [(r["model_id"], r["horizon"]) for r in result] == [("leg", 1), ("leg", 2)]
    h1, h2 = result
    assert (h1["n"], h1["n_targets"]) == (2, 2)
    assert h1["mae"] == 5.5  # (1 + 10) / 2
    assert h1["mae_unrounded"] == pytest.approx((0.6 + 10) / 2, rel=1e-12)  # clipped raw
    assert h1["rmse_unrounded"] == pytest.approx(math.sqrt((0.6**2 + 100) / 2), rel=1e-12)
    assert h1["mape_pct_unrounded"] == pytest.approx(100 * (0.6 / 100 + 1.0) / 2, rel=1e-12)
    assert h1["mase"] == 2.75  # (1/2 + 10/2) / 2
    assert (h2["n"], h2["n_targets"], h2["mae"]) == (1, 1, 2.0)
    assert h2["mae_unrounded"] == pytest.approx(2.4, rel=1e-12)
    # Without a period both periods count: 3 forecasts on 2 distinct target days at h = 1.
    both = window_summary(frame, **window)
    first = next(r for r in both if r["model_id"] == "leg" and r["horizon"] == 1)
    assert (first["n"], first["n_targets"]) == (3, 2)
    assert first["mae"] == 5.0  # (1 + 10 + 4) / 3


# ===========================================================================================
# evaluation.select_model
# ===========================================================================================


def test_candidate_with_any_failure_is_ineligible_and_cannot_win() -> None:
    fail_day = date(2024, 3, 4)
    scripts = [
        Script(REF, BASELINE, constant(30)),
        Script("base_b", BASELINE, constant(-20)),
        # Perfect wherever it forecasts, but one fit failure in the selection period.
        Script("reg_fail", REGRESSION, constant(0), fail_at=frozenset({fail_day})),
    ]
    config, result = run_scripts(
        scripts, [DEV_10], selection=selection_rule([REF, "base_b", "reg_fail"])
    )
    selection = select_model(result, config)
    assert selection is not None
    assert selection["eligibility"] == [
        {"model_id": REF, "scheduled": 30, "failed": 0, "eligible": True},
        {"model_id": "base_b", "scheduled": 30, "failed": 0, "eligible": True},
        {"model_id": "reg_fail", "scheduled": 30, "failed": 3, "eligible": False},
    ]
    assert [r["model_id"] for r in selection["ranking"]] == ["base_b", REF]
    assert [r["mae"] for r in selection["ranking"]] == [20.0, 30.0]
    assert selection["n_common_pairs"] == 30  # the ineligible model does not shrink them
    assert selection["n_common_origins"] == 10
    assert selection["best_regression"] is None
    assert selection["best_baseline"] == "base_b"
    assert selection["selected_model"] == "base_b"
    assert selection["selected_family"] == "baseline"
    assert selection["regression_vs_best_baseline"] is None


def test_failed_baseline_is_ineligible_too() -> None:
    scripts = [
        Script(REF, BASELINE, constant(30)),
        Script("base_fail", BASELINE, constant(1), fail_at=frozenset({date(2024, 3, 10)})),
        Script("base_b", BASELINE, constant(20)),
    ]
    config, result = run_scripts(
        scripts, [DEV_10], selection=selection_rule([REF, "base_fail", "base_b"])
    )
    selection = select_model(result, config)
    assert selection is not None
    assert selection["best_baseline"] == selection["selected_model"] == "base_b"
    assert "base_fail" not in [r["model_id"] for r in selection["ranking"]]


def test_failure_rate_equal_to_the_limit_is_eligible() -> None:
    # 1 failed origin of 10 -> rate 3 / 30 = 0.1, and the limit is 0.1 (inclusive).
    scripts = [
        Script(REF, BASELINE, constant(30)),
        Script("base_b", BASELINE, constant(20), fail_at=frozenset({date(2024, 3, 5)})),
    ]
    config, result = run_scripts(
        scripts, [DEV_10], selection=selection_rule([REF, "base_b"], max_failure_rate=0.1)
    )
    selection = select_model(result, config)
    assert selection is not None
    assert [e["eligible"] for e in selection["eligibility"]] == [True, True]
    assert selection["selected_model"] == "base_b"
    assert selection["n_common_pairs"] == 27


def test_ranking_is_on_pairs_common_to_every_eligible_candidate() -> None:
    hard = frozenset(DEV_10.origins()[::2])  # 5 of 10 origins
    scripts = [
        Script(REF, BASELINE, constant(30)),
        # x: 10 on easy origins, 100 on hard ones (own MAE 55)
        Script("base_x", BASELINE, lambda o, _h: 100 if o in hard else 10),
        # y: 20 where it forecasts, fails on every hard origin (own MAE 20)
        Script("base_y", BASELINE, constant(20), fail_at=hard),
    ]
    config, result = run_scripts(
        scripts,
        [DEV_10],
        selection=selection_rule([REF, "base_x", "base_y"], max_failure_rate=0.5),
    )
    selection = select_model(result, config)
    assert selection is not None
    assert selection["n_common_pairs"] == 15
    assert selection["n_common_origins"] == 5
    assert [(r["model_id"], r["mae"]) for r in selection["ranking"]] == [
        ("base_x", 10.0),
        ("base_y", 20.0),
        (REF, 30.0),
    ]
    assert selection["best_baseline"] == "base_x"


def test_best_baseline_and_best_regression_by_family_with_ties_to_the_earlier_candidate() -> None:
    scripts = [
        Script(REF, BASELINE, constant(50)),
        Script("base_p", BASELINE, constant(40)),
        Script("reg_slow", REGRESSION, constant(45)),
        Script("base_q", BASELINE, constant(-40)),  # ties base_p in MAE
        Script("reg_fast", REGRESSION, constant(-42)),
    ]
    candidates = [REF, "base_q", "reg_slow", "base_p", "reg_fast"]  # base_q listed first
    config, result = run_scripts(scripts, [DEV_10], selection=selection_rule(candidates))
    selection = select_model(result, config)
    assert selection is not None
    assert [r["model_id"] for r in selection["ranking"]] == [
        "base_q",
        "base_p",
        "reg_fast",
        "reg_slow",
        REF,
    ]
    assert [r["family"] for r in selection["ranking"]] == [
        "baseline",
        "baseline",
        "regression",
        "regression",
        "baseline",
    ]
    assert selection["best_baseline"] == "base_q"
    assert selection["best_regression"] == "reg_fast"
    # 42 is worse than 40: no margin, the baseline is selected.
    assert selection["margin_met"] is False
    assert selection["selected_model"] == "base_q"


def _even_baseline_error(origin: date, horizon: int) -> int:
    return -(100 + 10 * horizon + 2 * (origin.toordinal() % 5))  # even, varies by origin


@pytest.mark.parametrize(
    ("extra", "selected", "margin_met"),
    [(0, "reg_r", True), (1, "base_b", False)],
    ids=["exactly_at_margin", "just_short_of_margin"],
)
def test_margin_rule_with_a_significant_difference(
    extra: int, selected: str, margin_met: bool
) -> None:
    # R's |e| is exactly half of B's (+ extra), so MAE(R) = 0.5 * MAE(B) + extra; margin 50 %.
    scripts = [
        Script(REF, BASELINE, constant(400)),
        Script("base_b", BASELINE, _even_baseline_error),
        Script("reg_r", REGRESSION, lambda o, h: -_even_baseline_error(o, h) // 2 + extra),
    ]
    config, result = run_scripts(
        scripts, [DEV_60], selection=selection_rule([REF, "base_b", "reg_r"], margin=50.0)
    )
    names = {s.model_id: s for s in scripts}
    maes, _ = ref_common_mae(names, [REF, "base_b", "reg_r"], DEV_60.origins(), range(1, 4))
    assert maes["reg_r"] == maes["base_b"] / 2 + extra
    # Per-origin differentials -(60 + d % 5) - ... vary little: |t| is far above any
    # critical value (bound: the HAC variance is at most 7 * gamma0 for these kernels).
    diffs = [
        statistics.fmean(
            names["reg_r"].abs_error(o, h) - names["base_b"].abs_error(o, h) for h in (1, 2, 3)
        )
        for o in DEV_60.origins()
    ]
    assert abs(statistics.fmean(diffs)) / math.sqrt(7 * _autocovariance(diffs, 0) / 60) > 10
    selection = select_model(result, config)
    assert selection is not None
    assert selection["best_baseline"] == "base_b"
    assert selection["best_regression"] == "reg_r"
    assert selection["significant"] is True
    assert selection["margin_met"] is margin_met
    assert selection["selected_model"] == selected
    assert [r["mae"] for r in selection["ranking"][:2]] == [maes["reg_r"], maes["base_b"]]
    test = selection["regression_vs_best_baseline"]
    assert test["model_id"] == "reg_r"
    assert test["reference"] == "base_b"
    assert test["mae_difference"] == pytest.approx(maes["reg_r"] - maes["base_b"], rel=1e-12)


def test_regression_needs_enough_origins_for_the_significance_rule() -> None:
    scripts = [
        Script(REF, BASELINE, constant(400)),
        Script("base_b", BASELINE, _even_baseline_error),
        Script("reg_r", REGRESSION, lambda o, h: -_even_baseline_error(o, h) // 4),
    ]
    config, result = run_scripts(
        scripts, [DEV_20], selection=selection_rule([REF, "base_b", "reg_r"])
    )
    selection = select_model(result, config)
    assert selection is not None
    assert selection["margin_met"] is True  # about 75 % better
    assert selection["regression_vs_best_baseline"]["dm_computed"] is False
    assert selection["significant"] is False
    assert selection["selected_model"] == "base_b"


def test_noisy_advantage_within_the_margin_is_not_significant() -> None:
    origins = DEV_60.origins()
    rng = random.Random(1234)
    zero_origins = frozenset(rng.sample(origins, 30))
    scripts = [
        Script(REF, BASELINE, constant(400)),
        Script("base_b", BASELINE, constant(100)),
        # |e| is 0 on half the origins and 190 on the other half: MAE 95 <= 0.97 * 100.
        Script("reg_r", REGRESSION, lambda o, _h: 0 if o in zero_origins else -190),
    ]
    config, result = run_scripts(
        scripts, [DEV_60], selection=selection_rule([REF, "base_b", "reg_r"])
    )
    diffs = [(0 if o in zero_origins else 190) - 100 for o in origins]
    assert abs(_ref_dm_statistic(diffs, 3)) < 1.5  # p > 0.13 for any of the kernels
    selection = select_model(result, config)
    assert selection is not None
    assert selection["ranking"][0] == {"model_id": "reg_r", "family": "regression", "mae": 95.0}
    assert selection["margin_met"] is True
    assert selection["regression_vs_best_baseline"]["dm_computed"] is True
    assert selection["regression_vs_best_baseline"]["dm_p_value_conservative"] > 0.05
    assert selection["significant"] is False
    assert selection["selected_model"] == "base_b"


def test_selection_significance_uses_the_conservative_p_value() -> None:
    """Review mutant #12: the selection test is significant only on the conservative p.

    The block-correlated differential (seed 737) has a rectangular/HLN p below 0.05 but a
    Bartlett-2h (conservative) p above it: the margin is met, yet the baseline is selected.
    """
    differentials = _block_differentials(737)
    reference = _ref_dm(differentials, 3)  # one differential per origin, overlap 3
    assert reference.p_value < 0.05 <= reference.bartlett_p_value  # the planted situation
    assert reference.mean <= -3.0  # MAE(R) = 100 + mean(d) <= 0.97 * MAE(B*) = 97
    config, result = run_scripts(
        _differential_scripts(differentials),
        [DEV_60],
        selection=selection_rule([REF, "base_b", "reg_r"]),
    )
    selection = select_model(result, config)
    assert selection is not None
    test = selection["regression_vs_best_baseline"]
    assert (test["model_id"], test["reference"]) == ("reg_r", "base_b")
    assert test["mae_difference"] == pytest.approx(reference.mean, rel=1e-12)
    assert test["dm_p_value"] == pytest.approx(reference.p_value, rel=1e-6)
    assert test["dm_p_value_conservative"] == pytest.approx(
        reference.conservative_p_value, rel=1e-6
    )
    assert selection["margin_met"] is True
    assert selection["significant"] is False
    assert selection["significant"] is (
        test["dm_p_value_conservative"] < 0.05 and test["mae_difference"] < 0
    )
    assert selection["best_regression"] == "reg_r"
    assert selection["selected_model"] == "base_b"
    assert selection["selected_family"] == "baseline"


@dataclass(frozen=True)
class StridedPeriodSpec(PeriodSpec):
    """A period whose origins are ``stride`` days apart."""

    stride: int = 1

    def origins(self) -> list[date]:
        span = (self.last - self.first).days
        return [self.first + timedelta(days=k) for k in range(0, span + 1, self.stride)]

    def as_config(self) -> dict[str, Any]:
        return super().as_config() | {"origin_stride_days": self.stride}


# 50 origins two days apart (2024-03-01 .. 2024-06-07): just enough for a DM test.
DEV_STRIDE_2 = StridedPeriodSpec("dev", "selection", date(2024, 3, 1), date(2024, 6, 7), 2)


def test_selection_dm_test_uses_the_selection_period_origin_stride() -> None:
    """Review mutant F15: select_model passes the selection period's origin stride to DM.

    With origins two days apart the grid is contiguous at stride 2 and horizons 1..3
    overlap ``ceil(3 / 2) = 2`` origin steps; treated as daily, the grid would look gappy
    and the test would not be computed (so the better regression could never be selected).
    """
    scripts = [
        Script(REF, BASELINE, constant(400)),
        Script("base_b", BASELINE, _even_baseline_error),
        Script("reg_r", REGRESSION, lambda o, h: -_even_baseline_error(o, h) // 2),
    ]
    config, result = run_scripts(
        scripts, [DEV_STRIDE_2], selection=selection_rule([REF, "base_b", "reg_r"])
    )
    origins = DEV_STRIDE_2.origins()
    assert len(origins) == 50
    by_id = {s.model_id: s for s in scripts}
    # |e_R| = |e_B| / 2 at every pair, and |e_B| varies with the origin: variance > 0.
    differentials = [
        statistics.fmean(
            by_id["reg_r"].abs_error(o, h) - by_id["base_b"].abs_error(o, h) for h in (1, 2, 3)
        )
        for o in origins
    ]
    assert len(set(differentials)) > 1
    selection = select_model(result, config)
    assert selection is not None
    test = selection["regression_vs_best_baseline"]
    assert test["n_common"] == 150
    assert test["n_origins"] == 50
    assert test["dm_n_origins"] == 50
    assert test["dm_computed"] is True
    assert test["dm_reason"] is None
    assert test["dm_statistic"] == pytest.approx(_ref_dm_statistic(differentials, 2), rel=1e-9)
    assert selection["margin_met"] is True
    assert selection["significant"] is True
    assert selection["selected_model"] == "reg_r"


def test_select_model_is_none_without_a_rule_or_without_the_selection_period() -> None:
    scripts = [Script(REF, BASELINE, constant(1)), Script("base_b", BASELINE, constant(2))]
    config, result = run_scripts(scripts, [DEV_10])
    assert select_model(result, config) is None
    conf = PeriodSpec("conf", "confirmation", date(2024, 4, 1), date(2024, 4, 5))
    config = experiment(
        [REF, "base_b"],
        [DEV_10, conf],
        max_horizon=3,
        selection=selection_rule([REF, "base_b"]),
    )
    result = run_backtest(SERIES, config.plan(["conf"]), [ScriptedForecaster(s) for s in scripts])
    assert select_model(result, config) is None


# ===========================================================================================
# evaluation.selection_differences (sealed vs recomputed selection)
# ===========================================================================================

# The decision fields a re-run must reproduce, as documented for the sealed selection.
DECISION_FIELDS = (
    "rule",
    "period",
    "first_horizon",
    "last_horizon",
    "eligibility",
    "n_common_pairs",
    "n_common_origins",
    "best_baseline",
    "best_regression",
    "margin_met",
    "significant",
    "selected_model",
    "selected_family",
    "reason",
)
TEST_IDENTITY_FIELDS = ("model_id", "reference", "n_common", "n_origins", "dm_computed")
TEST_NUMBER_FIELDS = (
    "mae",
    "mae_reference",
    "mae_difference",
    "dm_p_value",
    "dm_p_value_conservative",
)


def _sealed_selection() -> dict[str, Any]:
    """A hand-written selection with moderate numbers (a fresh copy on every call)."""
    return copy.deepcopy(
        {
            "rule": "Pre-registered: synthetic rule.",
            "period": "dev",
            "first_horizon": 1,
            "last_horizon": 3,
            "eligibility": [
                {"model_id": REF, "scheduled": 180, "failed": 0, "eligible": True},
                {"model_id": "base_b", "scheduled": 180, "failed": 0, "eligible": True},
                {"model_id": "reg_r", "scheduled": 180, "failed": 0, "eligible": True},
            ],
            "n_common_pairs": 180,
            "n_common_origins": 60,
            "ranking": [
                {"model_id": "reg_r", "family": "regression", "mae": 85.25},
                {"model_id": "base_b", "family": "baseline", "mae": 100.5},
                {"model_id": REF, "family": "baseline", "mae": 400.0},
            ],
            "best_baseline": "base_b",
            "best_regression": "reg_r",
            "regression_vs_best_baseline": {
                "model_id": "reg_r",
                "reference": "base_b",
                "period": "dev",
                "n_common": 180,
                "n_origins": 60,
                "dm_computed": True,
                "mae": 85.25,
                "mae_reference": 100.5,
                "mae_difference": -15.25,
                "dm_p_value": 0.0123,
                "dm_p_value_conservative": 0.0234,
            },
            "margin_met": True,
            "significant": True,
            "selected_model": "reg_r",
            "selected_family": "regression",
            "reason": "reg_r beats the best baseline.",
        }
    )


def test_selection_difference_fields_are_the_decision_fields() -> None:
    """Review finding: a sealed selection is checked on every decision field."""
    assert SELECTION_EXACT_FIELDS == DECISION_FIELDS
    assert SELECTION_TEST_EXACT == TEST_IDENTITY_FIELDS
    assert SELECTION_TEST_CLOSE == TEST_NUMBER_FIELDS


def test_selection_differences_none_for_identical_selections_and_missing_recomputation() -> None:
    """Review finding: identical selections differ in nothing; no recomputation is reported."""
    sealed = _sealed_selection()
    assert selection_differences(sealed, sealed) == []
    assert selection_differences(sealed, _sealed_selection()) == []
    # As read back from the sealed JSON file (lists for tuples, same floats).
    assert selection_differences(json.loads(json_bytes(sealed)), sealed) == []
    assert selection_differences(sealed, None) == ["the selection period was not recomputed"]


@pytest.mark.parametrize("name", DECISION_FIELDS)
def test_selection_differences_names_each_changed_decision_field(name: str) -> None:
    """Review finding: each decision field that changes is reported by name."""
    recomputed = _sealed_selection()
    recomputed[name] = "<changed>"
    assert selection_differences(_sealed_selection(), recomputed) == [name]


def test_selection_differences_reports_fields_in_a_fixed_order() -> None:
    """Review finding: decision fields first, then the ranking, then the selection test."""
    recomputed = _sealed_selection()
    recomputed["selected_model"] = "base_b"
    recomputed["best_baseline"] = REF
    recomputed["ranking"].reverse()
    recomputed["regression_vs_best_baseline"]["mae"] = 90.0
    assert selection_differences(_sealed_selection(), recomputed) == [
        "best_baseline",
        "selected_model",
        "ranking",
        "regression_vs_best_baseline.mae",
    ]


@pytest.mark.parametrize(
    ("row", "key", "value", "different"),
    [
        (0, "model_id", "base_b", True),
        (1, "family", "regression", True),
        (0, "mae", 85.25 * (1 + 1e-6), True),
        (2, "mae", 400.0 * (1 + 1e-12), False),
        (0, "mae", None, True),
    ],
    ids=["other_model", "family", "mae_relative_1e-6", "mae_relative_1e-12", "mae_missing"],
)
def test_selection_differences_ranking_rows(
    row: int, key: str, value: Any, different: bool
) -> None:
    """Review finding: ranking rows must match in model, family and MAE (relative 1e-9)."""
    recomputed = _sealed_selection()
    recomputed["ranking"][row][key] = value
    expected = ["ranking"] if different else []
    assert selection_differences(_sealed_selection(), recomputed) == expected


def test_selection_differences_ranking_order_and_length() -> None:
    """Review finding: a swapped ranking order or a missing row is a difference."""
    swapped = _sealed_selection()
    swapped["ranking"][1], swapped["ranking"][2] = swapped["ranking"][2], swapped["ranking"][1]
    assert selection_differences(_sealed_selection(), swapped) == ["ranking"]
    # Only the order of the two baselines differs; the MAEs by position are unchanged.
    reordered = _sealed_selection()
    first, second = reordered["ranking"][1], reordered["ranking"][2]
    first["model_id"], second["model_id"] = second["model_id"], first["model_id"]
    assert selection_differences(_sealed_selection(), reordered) == ["ranking"]
    shorter = _sealed_selection()
    shorter["ranking"].pop()
    assert selection_differences(_sealed_selection(), shorter) == ["ranking"]
    assert selection_differences(shorter, _sealed_selection()) == ["ranking"]


def test_selection_differences_regression_test_present_on_one_side_only() -> None:
    """Review finding: a selection test on one side only is a difference."""
    without = _sealed_selection() | {"regression_vs_best_baseline": None}
    assert selection_differences(_sealed_selection(), without) == ["regression_vs_best_baseline"]
    assert selection_differences(without, _sealed_selection()) == ["regression_vs_best_baseline"]
    assert selection_differences(without, copy.deepcopy(without)) == []


@pytest.mark.parametrize("name", TEST_IDENTITY_FIELDS)
def test_selection_differences_regression_test_identity_fields(name: str) -> None:
    """Review finding: the selection test's identities and counts must match exactly."""
    recomputed = _sealed_selection()
    test = recomputed["regression_vs_best_baseline"]
    test[name] = False if name == "dm_computed" else "<changed>"
    assert selection_differences(_sealed_selection(), recomputed) == [
        f"regression_vs_best_baseline.{name}"
    ]


@pytest.mark.parametrize("name", TEST_NUMBER_FIELDS)
def test_selection_differences_regression_test_numbers_to_a_relative_1e9(name: str) -> None:
    """Review finding: the selection test's numbers match to a relative 1e-9 (BLAS noise)."""
    sealed = _sealed_selection()
    value = sealed["regression_vs_best_baseline"][name]
    for factor, expected in (
        (1 + 1e-6, [f"regression_vs_best_baseline.{name}"]),
        (1 + 1e-12, []),
        (1 - 1e-12, []),
    ):
        recomputed = _sealed_selection()
        recomputed["regression_vs_best_baseline"][name] = value * factor
        assert selection_differences(sealed, recomputed) == expected, factor
    recomputed = _sealed_selection()
    recomputed["regression_vs_best_baseline"][name] = None
    assert selection_differences(sealed, recomputed) == [f"regression_vs_best_baseline.{name}"]


# ===========================================================================================
# evaluation.recommend
# ===========================================================================================

REC_DEV = PeriodSpec("dev", "selection", date(2024, 3, 1), date(2024, 3, 5))
REC_CONF = PeriodSpec("conf", "confirmation", date(2024, 4, 1), date(2024, 5, 30))  # 60


def _jitter(offset: int) -> Callable[[date, int], int]:
    """Deterministic pseudo-random integer in 0..60 per (origin, horizon)."""

    def error(origin: date, horizon: int) -> int:
        return random.Random(origin.toordinal() * 31 + horizon * 7 + offset).randint(0, 60)

    return error


def _rec_run(
    reg_error: Callable[[date, int], int],
    base_error: Callable[[date, int], int],
    *,
    reg_fail: frozenset[date] = frozenset(),
) -> tuple[ExperimentConfig, BacktestResult, dict[str, Script]]:
    noise = _jitter(3)
    scripts = [
        Script(REF, BASELINE, lambda o, h: 120 + noise(o, h)),
        Script("base_b", BASELINE, base_error),
        Script("reg_r", REGRESSION, reg_error, fail_at=reg_fail),
    ]
    config, result = run_scripts(
        scripts, [REC_DEV, REC_CONF], selection=selection_rule([REF, "base_b", "reg_r"])
    )
    return config, result, {s.model_id: s for s in scripts}


SELECTED_REGRESSION = {
    "selected_model": "reg_r",
    "best_baseline": "base_b",
    "best_regression": "reg_r",
}


def _base_noise(origin: date, horizon: int) -> int:
    return -(100 + _jitter(1)(origin, horizon))


@pytest.mark.parametrize(
    ("reg_error", "recommended", "reason"),
    [
        (lambda o, h: 90 + _jitter(2)(o, h), "reg_r", "Confirmed"),
        (lambda o, h: -_base_noise(o, h), "reg_r", "Confirmed"),  # equal MAE: not worse
        (lambda o, h: 140 + _jitter(2)(o, h), "base_b", "Overturned"),
    ],
    ids=["better", "equal", "worse"],
)
def test_recommend_confirms_or_overturns_a_selected_regression(
    reg_error: Callable[[date, int], int], recommended: str, reason: str
) -> None:
    config, result, scripts = _rec_run(reg_error, _base_noise)
    maes, n = ref_common_mae(scripts, ["reg_r", "base_b", REF], REC_CONF.origins(), range(1, 4))
    assert n == 180
    recommendation = recommend(result, config, SELECTED_REGRESSION)
    assert recommendation is not None
    assert recommendation["period"] == "conf"
    assert recommendation["selected_model"] == "reg_r"
    assert recommendation["recommended_model"] == recommended
    assert recommendation["recommended_family"] == scripts[recommended].family.value
    assert recommendation["reason"].startswith(reason)
    assert recommendation["maes_common"] == maes
    assert list(recommendation["maes_common"]) == ["reg_r", "base_b", REF]


def test_recommend_judges_the_regression_on_common_confirmation_pairs() -> None:
    hard = frozenset(REC_CONF.origins()[::3])
    config, result, scripts = _rec_run(
        constant(150),
        lambda o, _h: -1000 if o in hard else -100,
        reg_fail=hard,
    )
    # On its own forecasts reg_r (150) beats base_b (about 400); on common pairs it loses.
    own_base = statistics.fmean(
        scripts["base_b"].abs_error(o, h) for o in REC_CONF.origins() for h in (1, 2, 3)
    )
    assert own_base > 150
    recommendation = recommend(result, config, SELECTED_REGRESSION)
    assert recommendation is not None
    assert recommendation["recommended_model"] == "base_b"
    assert recommendation["reason"].startswith("Overturned")
    assert recommendation["maes_common"]["reg_r"] == 150.0
    assert recommendation["maes_common"]["base_b"] == 100.0


def test_recommend_primary_tests_are_holm_adjusted() -> None:
    config, result, scripts = _rec_run(lambda o, h: 90 + _jitter(2)(o, h), _base_noise)
    recommendation = recommend(result, config, SELECTED_REGRESSION)
    assert recommendation is not None
    h1, h2 = recommendation["primary_tests"]
    assert h1["hypothesis"].startswith("H1")
    assert (h1["model_id"], h1["reference"], h1["period"]) == ("reg_r", "base_b", "conf")
    assert h2["hypothesis"].startswith("H2")
    assert (h2["model_id"], h2["reference"], h2["period"]) == ("reg_r", REF, "conf")
    pair_maes, _ = ref_common_mae(scripts, ["reg_r", "base_b"], REC_CONF.origins(), range(1, 4))
    assert (h1["mae"], h1["mae_reference"]) == (pair_maes["reg_r"], pair_maes["base_b"])
    p1, p2 = h1["dm_p_value_conservative"], h2["dm_p_value_conservative"]
    assert p1 is not None
    assert p2 is not None
    assert 0 < p1 < 1  # a moderate p-value, so the adjustment is visible
    expected = _holm_two(p1, p2)
    assert h1["holm_adjusted_p_value"] == pytest.approx(expected[0], rel=1e-15)
    assert h2["holm_adjusted_p_value"] == pytest.approx(expected[1], rel=1e-15)
    assert h1["holm_adjusted_p_value"] >= p1
    assert h2["holm_adjusted_p_value"] >= p2


def test_recommend_keeps_a_selected_baseline_and_skips_tests_against_itself() -> None:
    config, result, _ = _rec_run(lambda o, h: 140 + _jitter(2)(o, h), _base_noise)
    baseline_selected = {
        "selected_model": "base_b",
        "best_baseline": "base_b",
        "best_regression": "reg_r",
    }
    recommendation = recommend(result, config, baseline_selected)
    assert recommendation is not None
    assert recommendation["recommended_model"] == "base_b"
    assert [(t["model_id"], t["reference"]) for t in recommendation["primary_tests"]] == [
        ("reg_r", "base_b"),
        ("base_b", REF),
    ]
    reference_selected = {"selected_model": REF, "best_baseline": REF, "best_regression": None}
    recommendation = recommend(result, config, reference_selected)
    assert recommendation is not None
    assert recommendation["recommended_model"] == REF
    assert recommendation["primary_tests"] == []


def test_recommend_is_none_without_a_confirmation_period() -> None:
    scripts = [Script(REF, BASELINE, constant(1)), Script("base_b", BASELINE, constant(2))]
    config, result = run_scripts(scripts, [DEV_10], selection=selection_rule([REF, "base_b"]))
    selection = select_model(result, config)
    assert selection is not None
    assert recommend(result, config, selection) is None


# ===========================================================================================
# evaluation.underperformance
# ===========================================================================================


def test_underperformance_lists_exactly_the_cells_where_the_regression_is_worse() -> None:
    o4 = date(2024, 3, 4)
    dev = PeriodSpec("dev", "selection", date(2024, 3, 1), o4)
    extra = PeriodSpec("extra", "descriptive", date(2024, 3, 5), date(2024, 3, 6))

    def per_horizon(values: tuple[int, int, int], at_o4: tuple[int, int, int] | None = None):
        def error(origin: date, horizon: int) -> int:
            table = at_o4 if (at_o4 is not None and origin == o4) else values
            return table[horizon - 1]

        return error

    scripts = [
        Script(REF, BASELINE, constant(500)),
        Script("base_1", BASELINE, per_horizon((10, 10, 10), (1000, 1000, 1000))),
        Script("base_2", BASELINE, per_horizon((30, 5, 5))),
        # reg_1 vs base_1 on days 2-3: (8 + 12) * 3 + 998 + 1002 = 2060 = base_1's sum: equal
        Script("reg_1", REGRESSION, per_horizon((20, 8, 12), (1010, 998, 1002))),
        # reg_2 is better everywhere it forecasts in dev; much worse in the extra period
        Script(
            "reg_2",
            REGRESSION,
            lambda o, _h: 1 if o <= o4 else 4000,
            fail_at=frozenset({o4}),
        ),
        # reg_3 skips the hard origin: only common pairs show it is worse than base_1
        Script("reg_3", REGRESSION, constant(12), fail_at=frozenset({o4})),
    ]
    _, result = run_scripts(scripts, [dev, extra])
    buckets = (HorizonBucket("day_1", 1, 1), HorizonBucket("days_2_3", 2, 3))
    rows = underperformance(result, buckets, ["dev"])
    listed = {(r["model_id"], r["reference"], r["period"], r["label"]) for r in rows}
    assert len(listed) == len(rows)
    assert listed == {
        ("reg_1", "base_1", "dev", "day_1"),
        ("reg_1", "base_1", "dev", "days_1_3"),
        ("reg_1", "base_2", "dev", "day_1"),
        ("reg_1", "base_2", "dev", "days_2_3"),
        ("reg_1", "base_2", "dev", "days_1_3"),
        ("reg_3", "base_1", "dev", "day_1"),
        ("reg_3", "base_1", "dev", "days_2_3"),
        ("reg_3", "base_1", "dev", "days_1_3"),
        ("reg_3", "base_2", "dev", "days_2_3"),
    }
    # Cross-check every listed and unlisted cell against the plain-Python reference.
    by_id = {s.model_id: s for s in scripts}
    for regression in ("reg_1", "reg_2", "reg_3"):
        for baseline in (REF, "base_1", "base_2"):
            for label, hs in (
                ("day_1", range(1, 2)),
                ("days_2_3", range(2, 4)),
                ("days_1_3", range(1, 4)),
            ):
                maes, n = ref_common_mae(by_id, [regression, baseline], dev.origins(), hs)
                cell = (regression, baseline, "dev", label)
                assert (cell in listed) is (maes[regression] > maes[baseline]), cell
                if cell in listed:
                    row = next(
                        r
                        for r in rows
                        if (r["model_id"], r["reference"], r["period"], r["label"]) == cell
                    )
                    assert row["n_common"] == n
                    assert (row["mae"], row["mae_reference"]) == (
                        maes[regression],
                        maes[baseline],
                    )
                    assert row["mae_improvement_pct"] < 0
    reg_3_row = next(r for r in rows if r["model_id"] == "reg_3" and r["label"] == "day_1")
    assert (reg_3_row["n_common"], reg_3_row["mae"], reg_3_row["mae_reference"]) == (3, 12.0, 10.0)
    # The extra (descriptive) period is listed only when asked for.
    with_extra = underperformance(result, buckets, ["dev", "extra"])
    assert ("reg_2", "base_1", "extra", "days_1_3") in {
        (r["model_id"], r["reference"], r["period"], r["label"]) for r in with_extra
    }


# ===========================================================================================
# evaluation.evaluate: one full synthetic experiment
# ===========================================================================================

E_DEV = PeriodSpec("dev", "selection", date(2024, 5, 1), date(2024, 6, 29))  # 60 origins
E_CONF = PeriodSpec("conf", "confirmation", date(2024, 7, 2), date(2024, 8, 30))  # 60 origins
E_BRIDGE = PeriodSpec("bridge", "descriptive", date(2024, 8, 20), date(2024, 9, 5))
E_PERIODS = (E_DEV, E_CONF, E_BRIDGE)
E_DECISION = (E_DEV, E_CONF)
E_H = 3
REG_FAIL_DAY = date(2024, 5, 15)
FLAGGED = (date(2024, 5, 10), date(2024, 7, 20), date(2024, 9, 4), date(2023, 1, 1))
EXCLUDED = (date(2024, 6, 1), date(2024, 6, 10))
# Federal holidays touching the decision targets (2024-05-02 .. 2024-09-02), with the days
# before and after: Memorial Day (Mon 05-27), Juneteenth (Wed 06-19), Independence Day
# (Thu 07-04), Labor Day (Mon 09-02).
HOLIDAY_RELATED = frozenset(
    {
        date(2024, 5, 26),
        date(2024, 5, 27),
        date(2024, 5, 28),
        date(2024, 6, 18),
        date(2024, 6, 19),
        date(2024, 6, 20),
        date(2024, 7, 3),
        date(2024, 7, 4),
        date(2024, 7, 5),
        date(2024, 9, 1),
        date(2024, 9, 2),
        date(2024, 9, 3),
    }
)
LEGACY_BLOCK = {
    "rows": [
        {
            "label": "published one-step (synthetic)",
            "n": 90,
            "mae": 1.5,
            "rmse": 2.5,
            "mape_pct": 4.93,
        }
    ]
}

E_SCRIPTS = (
    Script(REF, BASELINE, lambda o, h: 150 + 20 * h + 5 * (o.toordinal() % 5)),
    Script("base_b", BASELINE, lambda o, h: -(100 + 10 * h + 3 * (o.toordinal() % 4))),
    Script(
        "reg_r",
        REGRESSION,
        lambda o, h: 50 + 5 * h + 2 * (o.toordinal() % 3) if o < E_CONF.first else 130 + 10 * h,
    ),
    Script("reg_fail", REGRESSION, constant(0), fail_at=frozenset({REG_FAIL_DAY})),
)
E_BY_ID = {s.model_id: s for s in E_SCRIPTS}


@dataclass(frozen=True)
class MainCase:
    config: ExperimentConfig
    result: BacktestResult
    payload: dict[str, Any]
    reference: list[RefRecord]

    def decision_ok(self) -> Iterator[RefRecord]:
        return (r for r in self.reference if r.ok and r.role != "descriptive")


@pytest.fixture(scope="module")
def main_case() -> MainCase:
    windows = [
        {
            "label": "conf_full",
            "kind": "legacy_holdout",
            "description": "Every horizon reaches every target.",
            "period": "conf",
            "first_target": "2024-07-05",
            "last_target": "2024-08-31",
        },
        {
            "label": "conf_wide",
            "description": "Starts before horizon 3 can reach it.",
            "period": "conf",
            "first_target": "2024-07-03",
            "last_target": "2024-08-31",
        },
        {
            "label": "dev_full",
            "description": "Reachable, but reg_fail failed on three of its forecasts.",
            "period": "dev",
            "first_target": "2024-05-04",
            "last_target": "2024-06-30",
        },
    ]
    sensitivity = {
        "exclude_flagged_targets": True,
        "holiday_stratum": True,
        "origin_exclusions": [
            {
                "label": "early_june",
                "first_origin": EXCLUDED[0].isoformat(),
                "last_origin": EXCLUDED[1].isoformat(),
                "reason": "Synthetic exclusion.",
            }
        ],
    }
    config, result = run_scripts(
        E_SCRIPTS,
        E_PERIODS,
        max_horizon=E_H,
        selection=selection_rule([REF, "base_b", "reg_r", "reg_fail"]),
        sensitivity=sensitivity,
        windows=windows,
    )
    payload = evaluate(
        result,
        config,
        data_fingerprint="synthetic-data",
        flagged_dates=FLAGGED,
        legacy_holdout=LEGACY_BLOCK,
    )
    return MainCase(config, result, payload, ref_records(E_SCRIPTS, E_PERIODS, E_H))


def _view(payload: Mapping[str, Any], label: str) -> dict[str, Any]:
    return next(v for v in payload["sensitivity"] if v["label"] == label)


def _expected_view_counts(
    records: Sequence[RefRecord], keep: Callable[[RefRecord], bool]
) -> dict[tuple[str, str, str], int]:
    """n_predictions per (model, period, bucket) among the kept ok decision records."""
    counts: dict[tuple[str, str, str], int] = {}
    for record in records:
        if not keep(record):
            continue
        for label, first, last in (("day_1", 1, 1), ("days_2_3", 2, 3), ("days_1_3", 1, 3)):
            if first <= record.horizon <= last:
                key = (record.model_id, record.period, label)
                counts[key] = counts.get(key, 0) + 1
    return counts


def _assert_view(
    view: Mapping[str, Any], case: MainCase, keep: Callable[[RefRecord], bool]
) -> None:
    kept = [r for r in case.decision_ok() if keep(r)]
    assert view["n_records_ok"] == len(kept)
    expected = _expected_view_counts(kept, lambda _r: True)
    got = {(r["model_id"], r["period"], r["label"]): r["n_predictions"] for r in view["summary"]}
    assert {k: v for k, v in got.items() if v} == expected
    assert {r["period"] for r in view["summary"]} <= {"dev", "conf"}


def test_payload_identity_periods_and_status_counts(main_case: MainCase) -> None:
    payload = main_case.payload
    assert payload["record_type"] == "historical_backtest"
    assert payload["label"].startswith("HISTORICAL BACKTEST")
    assert [c["record_type"] for c in payload["categories"]] == [
        "legacy_one_step_holdout",
        "historical_backtest",
        "future_projection",
    ]
    assert payload["config_fingerprint"] == main_case.config.fingerprint()
    assert payload["data_fingerprint"] == "synthetic-data"
    assert (payload["max_horizon"], payload["training_window"], payload["reference_model"]) == (
        3,
        "expanding",
        REF,
    )
    periods = {p["name"]: p for p in payload["periods"]}
    assert periods["dev"]["n_origins"] == 60
    assert periods["dev"]["first_target"] == date(2024, 5, 2)
    assert periods["dev"]["last_target"] == date(2024, 7, 2)
    assert periods["conf"]["role"] == "confirmation"
    assert periods["bridge"]["n_origins"] == 17
    counts = payload["status_counts"]
    assert counts["dev"]["reg_fail"] == {"failed": 3, "ok": 177}
    assert counts["conf"]["reg_fail"] == {"ok": 180}
    assert counts["bridge"]["base_b"] == {"ok": 51}


def test_payload_selection_and_recommendation(main_case: MainCase) -> None:
    payload = main_case.payload
    selection = payload["selection"]
    assert payload["selection_recomputed"] is None
    eligible = {e["model_id"]: e["eligible"] for e in selection["eligibility"]}
    assert eligible == {REF: True, "base_b": True, "reg_r": True, "reg_fail": False}
    maes, n = ref_common_mae(E_BY_ID, [REF, "base_b", "reg_r"], E_DEV.origins(), range(1, 4))
    assert selection["n_common_pairs"] == n == 180
    assert [(r["model_id"], r["mae"]) for r in selection["ranking"]] == sorted(
        maes.items(), key=lambda item: item[1]
    )
    assert (selection["best_baseline"], selection["best_regression"]) == ("base_b", "reg_r")
    assert selection["margin_met"] is True
    assert selection["significant"] is True
    assert selection["selected_model"] == "reg_r"
    # In the confirmation period reg_r (|e| = 130 + 10 h) is worse than base_b
    # (|e| <= 109 + 10 h): the pre-registered rule falls back to the baseline.
    recommendation = payload["recommendation"]
    assert recommendation["recommended_model"] == "base_b"
    assert recommendation["reason"].startswith("Overturned")
    conf_maes, _ = ref_common_mae(E_BY_ID, ["reg_r", "base_b", REF], E_CONF.origins(), range(1, 4))
    assert recommendation["maes_common"] == conf_maes
    tests = recommendation["primary_tests"]
    assert [(t["model_id"], t["reference"]) for t in tests] == [("reg_r", "base_b"), ("reg_r", REF)]


def test_payload_comparisons_use_common_pairs(main_case: MainCase) -> None:
    payload = main_case.payload
    to_ref = {
        (r["model_id"], r["period"], r["label"]): r for r in payload["comparison_to_reference"]
    }
    assert {m for m, _, _ in to_ref} == {"base_b", "reg_r", "reg_fail"}
    assert {p for _, p, _ in to_ref} == {"dev", "conf", "bridge"}
    row = to_ref[("reg_fail", "dev", "days_1_3")]
    maes, n = ref_common_mae(E_BY_ID, ["reg_fail", REF], E_DEV.origins(), range(1, 4))
    assert (row["n_common"], row["mae"], row["mae_reference"]) == (n, 0.0, maes[REF])
    assert n == 177
    assert row["mae_improvement_pct"] == 100.0
    to_best = {
        (r["model_id"], r["period"], r["label"]) for r in payload["comparison_to_best_baseline"]
    }
    assert {m for m, _, _ in to_best} == {REF, "reg_r", "reg_fail"}


def test_payload_underperformance_excludes_descriptive_periods(main_case: MainCase) -> None:
    listed = {
        (r["model_id"], r["reference"], r["period"], r["label"])
        for r in main_case.payload["underperformance"]
    }
    expected = set()
    for regression in ("reg_r", "reg_fail"):
        for baseline in (REF, "base_b"):
            for period in E_DECISION:
                for label, hs in (
                    ("day_1", range(1, 2)),
                    ("days_2_3", range(2, 4)),
                    ("days_1_3", range(1, 4)),
                ):
                    maes, _ = ref_common_mae(E_BY_ID, [regression, baseline], period.origins(), hs)
                    if maes[regression] > maes[baseline]:
                        expected.add((regression, baseline, period.name, label))
    assert listed == expected
    assert expected == {
        ("reg_r", "base_b", "conf", "day_1"),
        ("reg_r", "base_b", "conf", "days_2_3"),
        ("reg_r", "base_b", "conf", "days_1_3"),
    }


def test_sensitivity_flagged_targets_are_removed_exactly(main_case: MainCase) -> None:
    view = _view(main_case.payload, "exclude_flagged_targets")
    _assert_view(view, main_case, lambda r: r.target not in FLAGGED)
    assert view["excluded_target_dates"] == sorted(d.isoformat() for d in FLAGGED)
    # Two flagged days are decision targets, each reached at 3 horizons by 4 models.
    assert sum(1 for _ in main_case.decision_ok()) - view["n_records_ok"] == 2 * 3 * 4


def test_sensitivity_origin_exclusion(main_case: MainCase) -> None:
    view = _view(main_case.payload, "early_june")
    _assert_view(view, main_case, lambda r: not EXCLUDED[0] <= r.origin <= EXCLUDED[1])
    assert view["description"] == "Synthetic exclusion."
    assert view["excluded_origins"] == ["2024-06-01", "2024-06-10"]
    assert sum(1 for _ in main_case.decision_ok()) - view["n_records_ok"] == 10 * 3 * 4


def test_sensitivity_holiday_stratum_partitions_decision_records(main_case: MainCase) -> None:
    holiday = _view(main_case.payload, "holiday_related_targets")
    other = _view(main_case.payload, "other_targets")
    _assert_view(holiday, main_case, lambda r: r.target in HOLIDAY_RELATED)
    _assert_view(other, main_case, lambda r: r.target not in HOLIDAY_RELATED)
    assert holiday["n_records_ok"] + other["n_records_ok"] == sum(
        1 for _ in main_case.decision_ok()
    )
    assert holiday["n_records_ok"] > 0


def test_sensitivity_target_aligned(main_case: MainCase) -> None:
    view = _view(main_case.payload, "target_aligned")
    spans = {p.name: (p.first + timedelta(days=E_H), p.last + timedelta(days=1)) for p in E_PERIODS}
    _assert_view(view, main_case, lambda r: spans[r.period][0] <= r.target <= spans[r.period][1])
    # 58 aligned targets per period, 3 horizons, 4 models, minus reg_fail's 3 failures.
    assert view["n_records_ok"] == 2 * 58 * 3 * 4 - 3
    assert "vs_best_baseline" in view
    assert {r["model_id"] for r in view["vs_best_baseline"]} == {REF, "reg_r", "reg_fail"}


def test_comparison_windows(main_case: MainCase) -> None:
    windows = {w["label"]: w for w in main_case.payload["comparison_windows"]}
    full = windows["conf_full"]
    assert full["n_target_days"] == 58
    assert full["complete_at_every_horizon"] is True
    assert full["legacy_one_step_holdout"] == LEGACY_BLOCK
    rows = {(r["model_id"], r["horizon"]): r for r in full["rows"]}
    assert set(rows) == {(m, h) for m in E_BY_ID for h in (1, 2, 3)}
    first, last = date(2024, 7, 5), date(2024, 8, 31)
    for (model_id, horizon), row in rows.items():
        targets = _daterange(first, last)
        errors = [
            E_BY_ID[model_id].abs_error(t - timedelta(days=horizon), horizon) for t in targets
        ]
        assert (row["n"], row["n_targets"]) == (58, 58)
        assert row["mae"] == sum(errors) / 58
    assert windows["conf_wide"]["complete_at_every_horizon"] is False
    assert "legacy_one_step_holdout" not in windows["conf_wide"]
    dev_full = windows["dev_full"]
    assert dev_full["complete_at_every_horizon"] is False
    assert {(r["model_id"], r["horizon"]): r["n"] for r in dev_full["rows"]}[("reg_fail", 2)] == 57


def test_by_origin_month_covers_decision_periods_only(main_case: MainCase) -> None:
    rows = main_case.payload["by_origin_month"]
    got = {(r["period"], r["model_id"], r["origin_month"]): r["n"] for r in rows}
    expected: dict[tuple[str, str, str], int] = {}
    for record in main_case.decision_ok():
        key = (record.period, record.model_id, record.origin.strftime("%Y-%m"))
        expected[key] = expected.get(key, 0) + 1
    assert got == expected


def test_payload_is_strict_json_and_report_is_deterministic(main_case: MainCase) -> None:
    payload = main_case.payload

    def walk(value: Any) -> Iterator[Any]:
        if isinstance(value, Mapping):
            for item in value.values():
                yield from walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                yield from walk(item)
        else:
            yield value

    floats = [v for v in walk(payload) if isinstance(v, float)]
    assert floats
    assert all(math.isfinite(v) for v in floats)
    encoded = json_bytes(payload)
    decoded = json.loads(encoded, parse_constant=lambda token: pytest.fail(token))
    assert json.loads(json.dumps(decoded, allow_nan=False)) == decoded
    report = render_report(payload)
    assert report == render_report(payload)
    # A fresh, identical evaluation renders the same bytes.
    again = evaluate(
        main_case.result,
        main_case.config,
        data_fingerprint="synthetic-data",
        flagged_dates=FLAGGED,
        legacy_holdout=LEGACY_BLOCK,
    )
    assert json_bytes(again) == encoded
    assert render_report(again) == report
    assert "**HISTORICAL BACKTEST" in report
    assert "Not prospective validation" in report
    for key in ("legacy_one_step_holdout", "historical_backtest", "future_projection"):
        assert f"- `{key}`: " in report
    assert "Legacy one-step holdout: single fit, horizon 1, observed lags." in report
    assert "Future projection: beyond the data, no observed outcome." in report
    assert "published one-step (synthetic)" in report
    assert "**Selected: `reg_r`.**" in report
    assert "**Recommended: `base_b`.**" in report


def test_report_rendered_from_metrics_json_matches_report_md(main_case: MainCase) -> None:
    # Design 6.11: report.md is deterministic from metrics.json, so re-rendering the
    # decoded metrics.json must give the bytes written next to it.
    decoded = json.loads(json_bytes(main_case.payload))
    assert render_report(decoded) == render_report(main_case.payload)


def test_sealed_selection_drives_comparisons_and_is_checked(main_case: MainCase) -> None:
    sealed = dict(main_case.payload["selection"]) | {"best_baseline": REF, "selected_model": REF}
    payload = evaluate(
        main_case.result,
        main_case.config,
        data_fingerprint="synthetic-data",
        sealed_selection=sealed,
    )
    assert payload["selection"] == sealed
    assert payload["selection_recomputed"] == main_case.payload["selection"]
    assert {r["reference"] for r in payload["comparison_to_best_baseline"]} == {REF}
    assert payload["recommendation"]["recommended_model"] == REF
    # Review finding: the seal check names exactly the decision fields that differ.
    assert main_case.payload["selection_seal_check"] is None
    assert payload["selection_seal_check"] == {
        "fields": [*DECISION_FIELDS, "ranking", "regression_vs_best_baseline"],
        "differences": ["best_baseline", "selected_model"],
    }


# ===========================================================================================
# evaluation.evaluate: seal check, bandwidth sensitivity, influence (two decision periods)
# ===========================================================================================

# Noisy scripts with moderate DM p-values: reg_r beats base_b in both periods.
TWO_SCRIPTS = (
    Script(REF, BASELINE, lambda o, h: 120 + _jitter(3)(o, h)),
    Script("base_b", BASELINE, _base_noise),
    Script("reg_r", REGRESSION, lambda o, h: 90 + _jitter(2)(o, h)),
)
TWO_BY_ID = {s.model_id: s for s in TWO_SCRIPTS}
SELECTION_TEST_NAME = "selection: best regression vs best baseline"
# (kernel, bandwidth) at overlap h: rectangular h-1 and 2h-1, Bartlett 2h, 3h and 4h.
BANDWIDTHS_H3 = [
    ("rectangular", 2),
    ("rectangular", 5),
    ("bartlett", 6),
    ("bartlett", 9),
    ("bartlett", 12),
]


@dataclass(frozen=True)
class TwoPeriodCase:
    config: ExperimentConfig
    result: BacktestResult
    payload: dict[str, Any]


def _two_period_case(dev: PeriodSpec) -> TwoPeriodCase:
    config, result = run_scripts(
        TWO_SCRIPTS, [dev, E_CONF], selection=selection_rule([REF, "base_b", "reg_r"])
    )
    return TwoPeriodCase(config, result, evaluate(result, config, data_fingerprint="x"))


@pytest.fixture(scope="module")
def two_period_case() -> TwoPeriodCase:
    return _two_period_case(E_DEV)


def _differentials(
    model_id: str, reference: str, origins: Sequence[date], scripts: Mapping[str, Script]
) -> list[float]:
    """Per-origin mean of ``|e_model| - |e_reference|`` over horizons 1..3 (all ok)."""
    return [
        statistics.fmean(
            scripts[model_id].abs_error(o, h) - scripts[reference].abs_error(o, h)
            for h in (1, 2, 3)
        )
        for o in origins
    ]


def _assert_variants_match_reference(
    variants: Sequence[Mapping[str, Any]], differentials: Sequence[float], overlap: int
) -> None:
    """Each variant: HLN factor at ``overlap``, ``t(T - 1)``, its own HAC variance."""
    size = len(differentials)
    mean = math.fsum(differentials) / size
    gamma0 = _autocovariance(differentials, 0)
    quantile = _ref_t_quantile(0.975, size - 1)
    for variant in variants:
        variance = _ref_lrv(differentials, variant["bandwidth"], variant["kernel"])
        if variance <= 0:
            assert variant["p_value"] is None, variant
            assert variant["ci_half_width"] is None, variant
            assert variant["effective_sample_size"] is None, variant
            continue
        error = math.sqrt(variance / size / _ref_hln(size, overlap))
        assert variant["p_value"] == pytest.approx(
            _ref_t_two_sided_p(mean / error, size - 1), rel=1e-6, abs=1e-12
        ), variant
        assert variant["ci_half_width"] == pytest.approx(quantile * error, rel=1e-9), variant
        assert variant["effective_sample_size"] == pytest.approx(size * gamma0 / variance, rel=1e-9)


def test_selection_seal_check_is_empty_when_the_sealed_selection_is_reproduced(
    two_period_case: TwoPeriodCase,
) -> None:
    """Review finding: payload['selection_seal_check'] is None without a sealed selection
    and lists no difference when the sealed selection (read back from JSON) is reproduced.
    """
    case = two_period_case
    assert case.payload["selection_seal_check"] is None
    sealed = json.loads(json_bytes(case.payload["selection"]))
    payload = evaluate(case.result, case.config, data_fingerprint="x", sealed_selection=sealed)
    assert payload["selection_recomputed"] == case.payload["selection"]
    assert payload["selection_seal_check"] == {
        "fields": [*DECISION_FIELDS, "ranking", "regression_vs_best_baseline"],
        "differences": [],
    }


def test_bandwidth_sensitivity_of_the_selection_and_primary_tests(
    two_period_case: TwoPeriodCase,
) -> None:
    """Review finding: payload['bandwidth_sensitivity'] re-runs the selection test and H1/H2
    under wider HAC bandwidths; the first variant is the primary rectangular test and the
    Bartlett-2h variant is the reported Bartlett check.
    """
    payload = two_period_case.payload
    entries = payload["bandwidth_sensitivity"]
    selection = payload["selection"]
    primary = payload["recommendation"]["primary_tests"]
    assert [e["test"] for e in entries] == [
        SELECTION_TEST_NAME,
        primary[0]["hypothesis"],
        primary[1]["hypothesis"],
    ]
    assert [e["test"][:3] for e in entries[1:]] == ["H1:", "H2:"]
    assert [(e["period"], e["model_id"], e["reference"]) for e in entries] == [
        ("dev", "reg_r", "base_b"),
        ("conf", "reg_r", "base_b"),
        ("conf", "reg_r", REF),
    ]
    tests = [selection["regression_vs_best_baseline"], *primary]
    for entry, test, period in zip(entries, tests, (E_DEV, E_CONF, E_CONF), strict=True):
        assert set(entry) == {
            "test",
            "period",
            "model_id",
            "reference",
            "n_origins",
            "overlap",
            "mean_difference",
            "variants",
        }
        differentials = _differentials(
            entry["model_id"], entry["reference"], period.origins(), TWO_BY_ID
        )
        assert entry["n_origins"] == 60
        assert entry["overlap"] == 3
        assert entry["mean_difference"] == pytest.approx(statistics.fmean(differentials), rel=1e-12)
        variants = entry["variants"]
        assert [(v["kernel"], v["bandwidth"]) for v in variants] == BANDWIDTHS_H3
        assert test["dm_estimator"] == "rectangular"
        assert variants[0]["p_value"] == pytest.approx(test["dm_p_value"], rel=1e-12)
        assert variants[2]["p_value"] == pytest.approx(test["dm_bartlett_p_value"], rel=1e-12)
        _assert_variants_match_reference(variants, differentials, 3)
    # Moderate p-values somewhere, so the comparisons above are not all against zero.
    assert 0.01 < primary[0]["dm_p_value"] < 0.5
    assert _ref_t_quantile(0.975, 59) == pytest.approx(T_975_DF59, rel=1e-8)  # vs the tables


def test_bandwidth_sensitivity_without_enough_origins_or_without_a_rule() -> None:
    """Review finding: no variants when the DM test is not computed; nothing without a rule."""
    case = _two_period_case(PeriodSpec("dev", "selection", date(2024, 5, 1), date(2024, 5, 20)))
    entries = case.payload["bandwidth_sensitivity"]
    assert [(e["test"][:3], e["period"], e["n_origins"]) for e in entries] == [
        (SELECTION_TEST_NAME[:3], "dev", 20),
        ("H1:", "conf", 60),
        ("H2:", "conf", 60),
    ]
    assert case.payload["selection"]["regression_vs_best_baseline"]["dm_computed"] is False
    assert entries[0]["variants"] == []
    assert entries[0]["mean_difference"] == pytest.approx(
        statistics.fmean(
            _differentials(
                "reg_r",
                "base_b",
                _daterange(date(2024, 5, 1), date(2024, 5, 20)),
                TWO_BY_ID,
            )
        ),
        rel=1e-12,
    )
    assert [len(e["variants"]) for e in entries[1:]] == [5, 5]
    config, result = run_scripts(TWO_SCRIPTS, [DEV_10])
    assert evaluate(result, config, data_fingerprint="x")["bandwidth_sensitivity"] == []


def test_bandwidth_sensitivity_overlap_counts_origin_steps_with_a_stride() -> None:
    """Review finding: with origins two days apart, horizons 1..3 overlap 2 origin steps."""
    scripts = [
        Script(REF, BASELINE, constant(400)),
        Script("base_b", BASELINE, _even_baseline_error),
        Script("reg_r", REGRESSION, lambda o, h: -_even_baseline_error(o, h) // 2),
    ]
    config, result = run_scripts(
        scripts, [DEV_STRIDE_2], selection=selection_rule([REF, "base_b", "reg_r"])
    )
    payload = evaluate(result, config, data_fingerprint="x")
    assert payload["recommendation"] is None  # no confirmation period: no primary tests
    (entry,) = payload["bandwidth_sensitivity"]
    assert (entry["test"], entry["n_origins"], entry["overlap"]) == (SELECTION_TEST_NAME, 50, 2)
    variants = entry["variants"]
    # overlap 2: rectangular 1 and 3, Bartlett 4, 6 and 8.
    assert [(v["kernel"], v["bandwidth"]) for v in variants] == [
        ("rectangular", 1),
        ("rectangular", 3),
        ("bartlett", 4),
        ("bartlett", 6),
        ("bartlett", 8),
    ]
    test = payload["selection"]["regression_vs_best_baseline"]
    assert variants[0]["p_value"] == pytest.approx(test["dm_p_value"], rel=1e-12)
    differentials = _differentials(
        "reg_r", "base_b", DEV_STRIDE_2.origins(), {s.model_id: s for s in scripts}
    )
    _assert_variants_match_reference(variants, differentials, 2)


def test_payload_influence_matches_plain_python(two_period_case: TwoPeriodCase) -> None:
    """Review mutant F3/F20 (payload level): signed per-target contributions to
    ``MAE(model) - MAE(B*)`` on common forecasts, largest in size first, top 10.
    """
    entries = two_period_case.payload["influence"]
    assert [(e["model_id"], e["reference"], e["period"]) for e in entries] == [
        ("reg_r", "base_b", "dev"),
        ("reg_r", "base_b", "conf"),
        (REF, "base_b", "dev"),
        (REF, "base_b", "conf"),
    ]
    periods = {"dev": E_DEV, "conf": E_CONF}
    for entry in entries:
        sums: dict[date, int] = {}
        counts: dict[date, int] = {}
        pairs = [(o, h) for o in periods[entry["period"]].origins() for h in (1, 2, 3)]
        for origin, horizon in pairs:
            target = origin + timedelta(days=horizon)
            loss = TWO_BY_ID[entry["model_id"]].abs_error(origin, horizon)
            loss -= TWO_BY_ID[entry["reference"]].abs_error(origin, horizon)
            sums[target] = sums.get(target, 0) + loss
            counts[target] = counts.get(target, 0) + 1
        total = sum(sums.values())
        assert total != 0
        top = entry["top_target_dates"]
        assert len(top) == 10
        listed = [date.fromisoformat(r["target_date"]) for r in top]
        for row, target in zip(top, listed, strict=True):
            assert row["forecasts"] == counts[target]
            assert row["contribution_to_mae_difference"] == pytest.approx(
                sums[target] / len(pairs), rel=1e-9, abs=1e-12
            )
            assert row["share_of_difference"] == pytest.approx(
                sums[target] / total, rel=1e-9, abs=1e-12
            )
        sizes = [abs(r["contribution_to_mae_difference"]) for r in top]
        assert sizes == sorted(sizes, reverse=True)
        smallest = min(abs(sums[t]) for t in listed)
        assert all(abs(v) <= smallest for t, v in sums.items() if t not in listed)


def _small_window_case(
    scripts: Sequence[Script], first_target: str, last_target: str
) -> dict[str, Any]:
    dev = PeriodSpec("dev", "selection", date(2024, 3, 1), date(2024, 3, 10))
    window = {
        "label": "window",
        "description": "test window",
        "period": "dev",
        "first_target": first_target,
        "last_target": last_target,
    }
    config, result = run_scripts(scripts, [dev], windows=[window])
    payload = evaluate(result, config, data_fingerprint="x")
    return payload["comparison_windows"][0]


def test_window_fully_reachable_without_failures_is_complete() -> None:
    scripts = [Script(REF, BASELINE, constant(5)), Script("base_b", BASELINE, constant(7))]
    window = _small_window_case(scripts, "2024-03-04", "2024-03-11")
    assert window["n_target_days"] == 8
    assert window["complete_at_every_horizon"] is True
    assert len(window["rows"]) == 6


def test_window_with_a_horizon_never_forecast_is_not_complete() -> None:
    scripts = [
        Script(REF, BASELINE, constant(5)),
        Script("base_b", BASELINE, constant(7), fail_horizons=frozenset({3})),
    ]
    window = _small_window_case(scripts, "2024-03-04", "2024-03-11")
    assert ("base_b", 3) not in {(r["model_id"], r["horizon"]) for r in window["rows"]}
    assert window["complete_at_every_horizon"] is False


def test_window_without_any_scored_target_is_not_complete() -> None:
    scripts = [Script(REF, BASELINE, constant(5)), Script("base_b", BASELINE, constant(7))]
    window = _small_window_case(scripts, "2024-06-01", "2024-06-05")
    assert window["rows"] == []
    assert window["complete_at_every_horizon"] is False


def test_observed_input_decomposition_aggregates_both_procedures() -> None:
    series = noisy_series(date(2024, 1, 1), 160, seed=5)
    dev = PeriodSpec("dev", "selection", date(2024, 4, 1), date(2024, 4, 10))
    config = experiment(
        [REF, "ar_reg"],
        [dev],
        max_horizon=5,
        buckets=(("day_1", 1, 1), ("days_2_5", 2, 5)),
        observed_input_diagnostic=True,
    )
    regression = RecursiveLinearForecaster(
        model_id="ar_reg",
        model_type="test_ar",
        feature_set=FeatureSet((Lag(1), Lag(7)), name="test-ar"),
        method=FitMethod.STANDARDIZED_OLS,
        min_training_rows=30,
        round_feedback=False,
        description="Test AR regression.",
    )
    result = run_backtest(series, config.plan(), [SeasonalNaiveForecaster(REF), regression])
    payload = evaluate(result, config, data_fingerprint="x")
    rows = payload["observed_input_decomposition"]
    assert [(r["period"], r["model_id"], r["horizon"]) for r in rows] == [
        ("dev", "ar_reg", h) for h in range(1, 6)
    ]
    actual = {ts.date(): float(v) for ts, v in series.to_series().items()}
    records = result.records[result.records["model_id"] == "ar_reg"]
    observed = result.observed_inputs
    for row in rows:
        horizon = row["horizon"]
        recursive = records[records["horizon"] == horizon]
        diag = observed[(observed["model_id"] == "ar_reg") & (observed["horizon"] == horizon)]
        rec_errors = [
            abs(p - actual[pd.Timestamp(t).date()])
            for p, t in zip(recursive["prediction"], recursive["target_date"], strict=True)
        ]
        obs_errors = [
            abs(p - actual[pd.Timestamp(t).date()])
            for p, t in zip(diag["prediction_observed_inputs"], diag["target_date"], strict=True)
        ]
        assert row["n"] == len(rec_errors) == len(obs_errors) == 10
        assert row["mae_recursive"] == pytest.approx(math.fsum(rec_errors) / 10, rel=1e-12)
        assert row["mae_observed_inputs"] == pytest.approx(math.fsum(obs_errors) / 10, rel=1e-12)
    # At h = 1 both procedures make the same forecast; later the inputs differ.
    assert rows[0]["mae_observed_inputs"] == pytest.approx(rows[0]["mae_recursive"], rel=1e-9)
    assert rows[0]["mape_pct_observed_inputs"] == pytest.approx(
        rows[0]["mape_pct_recursive"], rel=1e-9
    )
    assert any(r["mae_observed_inputs"] != pytest.approx(r["mae_recursive"]) for r in rows[1:])
    report = render_report(payload)
    assert "## What recursion costs (observed-input diagnostic)" in report


# ===========================================================================================
# evaluation diagnostics: regression reliability, fit summary, influence
# ===========================================================================================

D1, D2, D3, D4, D5 = (date(2024, 1, d) for d in range(10, 15))


def _scored(
    model_id: str,
    period: str,
    origin: date,
    horizon: int,
    abs_error: int,
    *,
    estimable: bool | None = None,
    leverage: float = math.nan,
    status: str = "ok",
) -> dict[str, Any]:
    """A record with ``|e| = abs_error`` (actual 1000) and the regression reliability flags."""
    row = _record(
        model_id,
        period,
        origin,
        horizon,
        status=status,
        prediction=1000.0 + abs_error,
        actual=1000.0,
        scale=1.0,
    )
    row["estimable"] = estimable
    row["leverage"] = leverage
    return row


def _reliability_records() -> pd.DataFrame:
    rows = [
        # (origin, h): |e_reg|, estimable, leverage            |e_base|
        _scored("reg", "dev", D1, 1, 10, estimable=True, leverage=0.1),  # reliable       6
        _scored("reg", "dev", D1, 2, 20, estimable=True, leverage=0.5),  # reliable (=)   30
        _scored("reg", "dev", D2, 1, 30, estimable=False, leverage=0.2),  # non-estimable 12
        _scored("reg", "dev", D2, 2, 40, estimable=True, leverage=0.8),  # high leverage  36
        _scored("reg", "dev", D3, 1, 50, estimable=False, leverage=0.9),  # both          48
        _scored("reg", "dev", D3, 2, 60, estimable=True, leverage=0.3),  # reliable, base failed
        _scored("reg", "dev", D4, 1, 70, estimable=False, leverage=0.9, status="failed"),
        _scored("base", "dev", D1, 1, 6),
        _scored("base", "dev", D1, 2, 30),
        _scored("base", "dev", D2, 1, 12),
        _scored("base", "dev", D2, 2, 36),
        _scored("base", "dev", D3, 1, 48),
        _scored("base", "dev", D3, 2, 999, status="failed"),
        _scored("base", "dev", D4, 1, 7),  # the regression failed here: no pair
        # Another period: compared on its own (the dev comparator must not pair with it).
        _scored("reg", "conf", D1, 1, 100, estimable=True, leverage=0.9),
        _scored("base", "conf", D1, 1, 1),
    ]
    return _frame(rows)


RELIABILITY_ALL_ONLY = {
    "conf": {
        "period": "conf",
        "model_id": "reg",
        "n_all": 1,
        "mae_all": 100.0,
        "n_nonestimable": 0,
        "n_high_leverage": 1,
        "n_reliable": 0,
        "mae_reliable_only": None,
    },
    "dev": {
        "period": "dev",
        "model_id": "reg",
        "n_all": 6,
        "mae_all": 35.0,  # (10 + 20 + 30 + 40 + 50 + 60) / 6
        "n_nonestimable": 2,  # 30, 50
        "n_high_leverage": 2,  # 40, 50 (leverage 0.5 is not above the limit)
        "n_reliable": 3,  # 10, 20, 60
        "mae_reliable_only": 30.0,  # 90 / 3
    },
}
RELIABILITY_PAIRS = {
    "conf": {
        "n_all_pairs": 1,
        "mae_all_pairs": 100.0,
        "comparator_mae_all_pairs": 1.0,
        "n_reliable_pairs": 0,
        "mae_reliable_pairs": None,
        "comparator_mae_reliable_pairs": None,
        "n_removed_pairs": 1,
        "mae_removed_pairs": 100.0,
        "comparator_mae_removed_pairs": 1.0,
    },
    "dev": {
        # The pair at (D3, 2) is gone: the comparator failed there.
        "n_all_pairs": 5,
        "mae_all_pairs": 30.0,  # (10 + 20 + 30 + 40 + 50) / 5
        "comparator_mae_all_pairs": 26.4,  # (6 + 30 + 12 + 36 + 48) / 5
        "n_reliable_pairs": 2,
        "mae_reliable_pairs": 15.0,  # (10 + 20) / 2
        "comparator_mae_reliable_pairs": 18.0,  # (6 + 30) / 2
        "n_removed_pairs": 3,
        "mae_removed_pairs": 40.0,  # (30 + 40 + 50) / 3
        "comparator_mae_removed_pairs": 32.0,  # (12 + 36 + 48) / 3
    },
}


def test_regression_reliability_by_hand() -> None:
    """Review mutants F1/F18: reliable = estimable and leverage <= HIGH_LEVERAGE (0.5).

    Hand-planted forecasts: reliable, at the leverage limit, non-estimable, high
    leverage, both, a failed one, and one whose comparator failed (not a pair). With a
    comparator the regression and B* are compared on identical kept and removed pairs.
    """
    rows = _regression_reliability(_reliability_records(), "base")
    assert [(r["period"], r["model_id"]) for r in rows] == [("conf", "reg"), ("dev", "reg")]
    for row in rows:
        period = row["period"]
        expected = RELIABILITY_ALL_ONLY[period] | {"comparator": "base"}
        expected |= RELIABILITY_PAIRS[period]
        assert row == pytest.approx(expected, rel=1e-12), period
    rows = _regression_reliability(_reliability_records())
    for row in rows:
        expected = RELIABILITY_ALL_ONLY[row["period"]] | {"comparator": None}
        assert row == pytest.approx(expected, rel=1e-12), row["period"]


def _fit(model_id: str, day: int, **values: Any) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(FIT_COLUMNS)
    row |= {
        "period": "dev",
        "model_id": model_id,
        "origin": pd.Timestamp(date(2024, 3, day)),
        "data_cutoff": pd.Timestamp(date(2024, 3, day)),
        "status": "ok",
    }
    return row | values


FIT_KEYS = (
    "rank",
    "solver_rank",
    "identifiable",
    "n_dropped_rows",
    "restricted_condition_number",
    "max_training_leverage",
    "in_sample_mae",
    "n_clipped",
    "n_nonestimable",
    "n_high_leverage",
)
# Successful regression fits (5 features, 99 + day training rows), one per day:
# day, rank, solver rank, identifiable, dropped rows, restricted condition number,
# max training leverage, in-sample MAE, clipped, non-estimable, high-leverage predictions.
REGRESSION_FITS = (
    (1, 5.0, 5.0, True, 2, 10.0, 0.3, 4.0, 0, 0.0, 1.0),
    (2, 4.0, 5.0, False, 1, 30.0, 0.6, 6.0, 1, 2.0, 0.0),
    (3, 3.0, 3.0, False, 0, math.inf, 0.4, 5.0, 3, 1.0, 2.0),
    (4, 5.0, math.nan, True, 0, 20.0, 0.2, 7.0, 0, 0.0, 4.0),
    (5, 5.0, 5.0, True, 0, math.nan, 0.1, 3.0, 0, 0.0, 0.0),
)


def _fits_frame() -> pd.DataFrame:
    rows = [
        _fit("reg", day, n_features=5, n_train_rows=99 + day, **dict(zip(FIT_KEYS, v, strict=True)))
        for day, *v in REGRESSION_FITS
    ]
    rows += [
        _fit("reg", day, status="failed", failure_stage="fit", failure_code=code)
        for day, code in ((6, "rank_deficient"), (7, "singular_design"), (8, "rank_deficient"))
    ]
    # A baseline: no regression diagnostics at all.
    rows += [
        _fit("base", day, n_features=1, n_train_rows=0, n_dropped_rows=0, n_clipped=0)
        for day in (1, 2, 3)
    ]
    return pd.DataFrame.from_records(rows, columns=list(FIT_COLUMNS))


FIT_SUMMARY = [
    {
        "model_id": "base",
        "period": "dev",
        "n_origins": 3,
        "n_failed": 0,
        "failure_codes": {},
        "n_features": 1,
        "n_train_rows_min": 0,
        "n_train_rows_max": 0,
        "n_dropped_rows_total": 0,
        "rank_min": None,
        "rank_max": None,
        "n_rank_deficient_fits": 0,
        "solver_rank_disagreements": 0,
        "restricted_condition_median": None,
        "restricted_condition_max": None,
        "max_training_leverage_max": None,
        "n_nonestimable_predictions": None,
        "n_high_leverage_predictions": None,
        "n_clipped_predictions": 0,
        "in_sample_mae_median": None,
    },
    {
        "model_id": "reg",
        "period": "dev",
        "n_origins": 8,
        "n_failed": 3,
        "failure_codes": {"rank_deficient": 2, "singular_design": 1},
        "n_features": 5,
        "n_train_rows_min": 100,
        "n_train_rows_max": 104,
        "n_dropped_rows_total": 3,  # 2 + 1, failed fits excluded
        "rank_min": 3,
        "rank_max": 5,
        "n_rank_deficient_fits": 2,  # days 2 and 3 (3 fits are identifiable)
        "solver_rank_disagreements": 1,  # day 2; day 4 has no solver rank
        "restricted_condition_median": 20.0,  # median(10, 30, 20); inf and NaN dropped
        "restricted_condition_max": 30.0,
        "max_training_leverage_max": 0.6,
        "n_nonestimable_predictions": 3,  # 0 + 2 + 1 + 0 + 0
        "n_high_leverage_predictions": 7,  # 1 + 0 + 2 + 4 + 0
        "n_clipped_predictions": 4,  # 0 + 1 + 3 + 0 + 0
        "in_sample_mae_median": 5.0,  # median(4, 6, 5, 7, 3)
    },
]


def test_fit_summary_by_hand() -> None:
    """Review mutants F2/F19: fit diagnostics aggregated per model and period by hand."""
    assert _fit_summary(_fits_frame()) == FIT_SUMMARY


def test_payload_reliability_uses_the_best_baseline_and_reports_the_fit_summary() -> None:
    """Review mutants F1/F18 and F2/F19 (payload level): B* is the reliability comparator."""
    scripts = [
        Script(REF, BASELINE, constant(400)),
        Script("base_b", BASELINE, lambda _o, h: -(5 + h)),
        Script("reg_r", REGRESSION, lambda _o, h: 10 * h),
    ]
    config, result = run_scripts(
        scripts, [DEV_10], selection=selection_rule([REF, "base_b", "reg_r"])
    )
    records = result.records.copy()
    regression = records["model_id"] == "reg_r"
    records.loc[regression, "estimable"] = True
    records.loc[regression, "leverage"] = 0.5
    records.loc[regression & (records["origin"] == pd.Timestamp(2024, 3, 2)), "estimable"] = False
    high = regression & (records["origin"] == pd.Timestamp(2024, 3, 3)) & (records["horizon"] == 1)
    records.loc[high, "leverage"] = 0.9
    planted = dataclasses.replace(result, records=records, fits=_fits_frame())
    payload = evaluate(planted, config, data_fingerprint="x")
    assert payload["selection"]["best_baseline"] == "base_b"
    # reg_r: |e| = 10 h on 10 origins; base_b: |e| = 5 + h. Removed: (03-02, h = 1..3)
    # non-estimable and (03-03, h = 1) high leverage.
    assert payload["regression_reliability"] == [
        pytest.approx(
            {
                "period": "dev",
                "model_id": "reg_r",
                "n_all": 30,
                "mae_all": 20.0,
                "n_nonestimable": 3,
                "n_high_leverage": 1,
                "n_reliable": 26,
                "mae_reliable_only": 530 / 26,  # 600 - (10 + 20 + 30) - 10
                "comparator": "base_b",
                "n_all_pairs": 30,
                "mae_all_pairs": 20.0,
                "comparator_mae_all_pairs": 7.0,
                "n_reliable_pairs": 26,
                "mae_reliable_pairs": 530 / 26,
                "comparator_mae_reliable_pairs": 183 / 26,  # 210 - (6 + 7 + 8) - 6
                "n_removed_pairs": 4,
                "mae_removed_pairs": 17.5,  # (10 + 20 + 30 + 10) / 4
                "comparator_mae_removed_pairs": 6.75,  # (6 + 7 + 8 + 6) / 4
            },
            rel=1e-12,
        )
    ]
    assert payload["fit_summary"] == FIT_SUMMARY


def _influence_records() -> pd.DataFrame:
    rows = [
        # (origin, h) -> target: |e_reg| - |e_base|
        _scored("reg", "dev", D1, 1, 30),  # D2: +20
        _scored("base", "dev", D1, 1, 10),
        _scored("reg", "dev", D1, 2, 5),  # D3: -20
        _scored("base", "dev", D1, 2, 25),
        _scored("reg", "dev", D2, 1, 10),  # D3: -40
        _scored("base", "dev", D2, 1, 50),
        _scored("reg", "dev", D2, 2, 70),  # D4: +60
        _scored("base", "dev", D2, 2, 10),
        _scored("reg", "dev", D3, 1, 10),  # D4: -20
        _scored("base", "dev", D3, 1, 30),
        _scored("reg", "dev", D3, 2, 0),  # D5: -20
        _scored("base", "dev", D3, 2, 20),
        # Not common forecasts: ignored.
        _scored("reg", "dev", D4, 1, 500),
        _scored("base", "dev", D4, 1, 0, status="failed"),
        _scored("base", "dev", D4, 2, 900),
        _scored("reg", "conf", D1, 1, 800),
        _scored("base", "conf", D1, 1, 0),
    ]
    return _frame(rows)


def test_influence_by_hand() -> None:
    """Review mutants F3/F20: per-target contributions ``(|e_model| - |e_ref|) / N``.

    Six common forecasts (N = 6). By target: D2 +20, D3 -20 - 40 = -60, D4 +60 - 20 = +40,
    D5 -20; total -20 (the MAE difference times N). Ordered by size; the tie between
    D2 (+20) and D5 (-20) keeps date order.
    """
    rows = _influence(_influence_records(), "reg", "base", "dev")
    expected = [
        (D3, 2, -60, 3.0),
        (D4, 2, 40, -2.0),
        (D2, 1, 20, -1.0),
        (D5, 1, -20, 1.0),
    ]
    assert [r["target_date"] for r in rows] == [t.isoformat() for t, _, _, _ in expected]
    for row, (target, forecasts, total, share) in zip(rows, expected, strict=True):
        assert row["forecasts"] == forecasts, target
        assert row["contribution_to_mae_difference"] == pytest.approx(total / 6, rel=1e-12)
        assert row["share_of_difference"] == pytest.approx(share, rel=1e-12)
    # The contributions add up to MAE(reg) - MAE(base) on the common forecasts.
    mae_difference = (30 + 5 + 10 + 70 + 10 + 0) / 6 - (10 + 25 + 50 + 10 + 30 + 20) / 6
    assert math.fsum(r["contribution_to_mae_difference"] for r in rows) == pytest.approx(
        mae_difference, rel=1e-12
    )
    top_two = _influence(_influence_records(), "reg", "base", "dev", top=2)
    assert [r["target_date"] for r in top_two] == [D3.isoformat(), D4.isoformat()]
    assert _influence(_influence_records(), "reg", "base", "bridge") == []
