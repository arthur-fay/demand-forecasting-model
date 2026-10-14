"""Interval arithmetic, quantile ordering, exceedance probabilities and exact scores.

Expected values are worked out by hand in the comments, or by plain-Python reference
implementations in the tests.
"""

from __future__ import annotations

import math
from datetime import date
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from atlas.calibration import CalibrationSet, Pooling, Support
from atlas.errors import ConfigError
from atlas.exceedance import (
    HYPOTHETICAL_NOTICE,
    PROVENANCE_HISTORY,
    PROVENANCE_HYPOTHETICAL,
    check_ordering,
    historical_threshold,
    threshold_values,
    trailing_window,
)
from atlas.intervals import (
    GaussianDistribution,
    SampleDistribution,
    check_quantiles,
    climatology,
    conformal_rank,
    empirical_additive,
    empirical_relative,
    gaussian_one_step,
    linear_quantiles,
    model_distribution,
    split_conformal,
)
from atlas.metrics import dependence_test, per_origin_mean
from atlas.scoring import (
    brier_score,
    coverage,
    interval_score,
    origin_test,
    pinball_loss,
    reliability_table,
    weighted_interval_score,
)
from atlas.uncertainty_config import (
    ClimatologyConfig,
    EmpiricalQuantileConfig,
    GaussianOneStepConfig,
    HistoricalThresholdConfig,
    HypotheticalThresholdConfig,
    SplitConformalConfig,
)
from tests.m2_helpers import series_from
from tests.m3_helpers import GRID, day

TAUS = np.array(GRID)


def cal_set(residuals: list[float], predictions: list[float] | None = None) -> CalibrationSet:
    n = len(residuals)
    return CalibrationSet(
        model_id="m",
        horizon=1,
        issue_origin=day(100),
        calibration_origin=day(100),
        cutoff=day(100),
        first_target=None,
        pooling=Pooling.HORIZON,
        pooled_label=None,
        residuals=np.asarray(residuals, dtype="float64"),
        predictions=np.asarray(predictions or [1000.0] * n, dtype="float64"),
        origins=np.arange(n, dtype="int64"),
        targets=np.arange(n, dtype="int64") + 1,
        n_origins=n,
        n_effective=float(n),
        support=Support.SUPPORTED,
    )


def type7(values: list[float], tau: float) -> float:
    """Plain-Python sample quantile with linear interpolation (Hyndman-Fan type 7)."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * tau
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


# --- Quantiles and intervals ------------------------------------------------------------------


def test_empirical_additive_quantiles_are_prediction_plus_residual_quantiles() -> None:
    # Residuals -200..200 step 100 (n = 5): type-7 positions (n - 1) * tau = 4 * tau.
    distribution = empirical_additive(1000.0, cal_set([-200.0, -100.0, 0.0, 100.0, 200.0]))
    q = distribution.quantiles(TAUS)
    # tau 0.025 -> position 0.1 -> -200 + 0.1 * 100 = -190; 0.1 -> 0.4 -> -160; 0.25 -> -100;
    # 0.5 -> 0; 0.75 -> 100; 0.9 -> 3.6 -> 160; 0.975 -> 3.9 -> 190.
    assert q == pytest.approx([810.0, 840.0, 900.0, 1000.0, 1100.0, 1160.0, 1190.0])


def test_interval_bounds_are_clipped_at_zero_and_never_cross() -> None:
    distribution = empirical_additive(50.0, cal_set([-300.0, -100.0, 0.0, 100.0, 300.0]))
    q = distribution.quantiles(TAUS)
    assert q[0] == 0.0  # 50 - 280 clipped: demand cannot be negative
    assert q[1] == 0.0
    assert np.all(np.diff(q) >= 0)


def test_linear_quantiles_equal_numpy_exactly() -> None:
    rng = np.random.default_rng(7)
    for n in (1, 2, 3, 10, 39, 365):
        values = np.sort(rng.normal(10_000, 1_500, n))
        assert np.array_equal(linear_quantiles(values, TAUS), np.quantile(values, TAUS))
        assert linear_quantiles(values, TAUS) == pytest.approx(
            [type7(list(values), t) for t in TAUS]
        )


def test_conformal_rank_is_robust_to_binary_rounding() -> None:
    # 100 * 0.07 is 7.000000000000001 in floating point; a naive ceil would give rank 8.
    assert math.ceil((99 + 1) * 0.07) == 8
    assert conformal_rank(99, 0.07) == 7
    assert conformal_rank(99, 0.95) == 95
    assert conformal_rank(19, 0.9) == 18
    assert conformal_rank(18, 0.95) == 19  # ceil(19 * 0.95) = ceil(18.05)


def test_split_conformal_uses_the_finite_sample_rank() -> None:
    # |r| = 1..19 (n = 19), signs alternate. Central 80 %: rank ceil(20 * 0.8) = 16;
    # central 95 %: rank ceil(20 * 0.95) = 19; 50 %: rank ceil(20 * 0.5) = 10.
    residuals = [float(k) * (-1) ** k for k in range(1, 20)]
    distribution = split_conformal(1000.0, cal_set(residuals))
    q = distribution.quantiles(TAUS)
    assert list(q) == [981.0, 984.0, 990.0, 1000.0, 1010.0, 1016.0, 1019.0]


def test_split_conformal_level_beyond_the_sample_is_unavailable() -> None:
    # n = 18: rank ceil(19 * 0.95) = 19 > 18, so the 95 % bounds do not exist.
    distribution = split_conformal(1000.0, cal_set([float(k) for k in range(1, 19)]))
    q = distribution.quantiles(TAUS)
    assert np.isnan(q[0])
    assert np.isnan(q[-1])
    assert q[1] == 1000.0 - 16.0  # rank ceil(19 * 0.8) = 16 still exists
    assert q[3] == 1000.0


def test_symmetric_conformal_exceedance_uses_both_signs() -> None:
    distribution = split_conformal(1000.0, cal_set([10.0, -20.0, 30.0]))
    # Symmetric sample: 970, 980, 990, 1010, 1020, 1030 (m = 6).
    assert distribution.exceedance(np.array([1000.0]))[0] == pytest.approx(3.5 / 7)
    assert distribution.exceedance(np.array([1025.0]))[0] == pytest.approx(1.5 / 7)


def test_empirical_relative_scales_with_the_prediction() -> None:
    # Calibration forecasts 100 and 200 with actuals 110 and 180: ratios 1.1 and 0.9.
    calibration = cal_set([10.0, -20.0], predictions=[100.0, 200.0])
    distribution = empirical_relative(1000.0, calibration)
    assert distribution is not None
    assert list(distribution.values) == pytest.approx([900.0, 1100.0])
    assert empirical_relative(0.0, calibration) is None
    assert empirical_relative(1000.0, cal_set([1.0], predictions=[0.0])) is None


def test_gaussian_one_step_uses_the_rms_of_horizon_one_residuals() -> None:
    distribution = gaussian_one_step(1000.0, cal_set([30.0, -40.0]))
    assert distribution is not None
    sd = math.sqrt((900 + 1600) / 2)
    assert distribution.sd == pytest.approx(sd)
    z = NormalDist().inv_cdf(0.9)
    assert distribution.quantiles(np.array([0.1, 0.5, 0.9])) == pytest.approx(
        [1000 - z * sd, 1000.0, 1000 + z * sd]
    )
    assert distribution.exceedance(np.array([1000.0]))[0] == pytest.approx(0.5)
    assert gaussian_one_step(1000.0, cal_set([0.0, 0.0])) is None
    assert gaussian_one_step(1000.0, cal_set([])) is None
    with pytest.raises(ConfigError):
        GaussianDistribution(1.0, 0.0)


def test_climatology_needs_enough_observations() -> None:
    assert climatology(np.array([1.0, np.nan, 3.0]), 3) is None
    distribution = climatology(np.array([1.0, np.nan, 3.0, 2.0]), 3)
    assert distribution is not None
    assert list(distribution.values) == [1.0, 2.0, 3.0]


def test_model_distribution_dispatch() -> None:
    calibration = cal_set([-10.0, 0.0, 10.0], predictions=[100.0, 100.0, 100.0])
    additive = EmpiricalQuantileConfig(type="empirical_quantile", method_id="a", score="additive")
    relative = EmpiricalQuantileConfig(type="empirical_quantile", method_id="r", score="relative")
    conformal = SplitConformalConfig(type="split_conformal", method_id="c")
    gaussian = GaussianOneStepConfig(type="gaussian_one_step", method_id="g")
    climate = ClimatologyConfig(
        type="climatology", method_id="k", window_days=180, min_observations=10
    )
    assert isinstance(model_distribution(additive, 500.0, calibration), SampleDistribution)
    rel = model_distribution(relative, 500.0, calibration)
    assert isinstance(rel, SampleDistribution)
    assert list(rel.values) == pytest.approx([450.0, 500.0, 550.0])
    assert model_distribution(conformal, 500.0, calibration) is not None
    assert isinstance(model_distribution(gaussian, 500.0, calibration), GaussianDistribution)
    assert model_distribution(additive, 500.0, cal_set([])) is None
    with pytest.raises(ConfigError, match="climatology"):
        model_distribution(climate, 500.0, calibration)


def test_check_quantiles_rejects_crossing_and_negative_values() -> None:
    levels = np.array([0.1, 0.5, 0.9])
    with pytest.raises(AssertionError, match="cross"):
        check_quantiles(np.array([5.0, 4.0, 6.0]), levels)
    with pytest.raises(AssertionError, match="cross"):
        check_quantiles(np.array([-1.0, 4.0, 6.0]), levels)
    with pytest.raises(AssertionError, match="increasing"):
        check_quantiles(np.array([1.0, 2.0, 3.0]), np.array([0.5, 0.1, 0.9]))
    assert list(check_quantiles(np.array([np.nan, 4.0, 6.0]), levels)[1:]) == [4.0, 6.0]


def test_sample_distribution_validation() -> None:
    with pytest.raises(ConfigError):
        SampleDistribution(np.array([]))
    with pytest.raises(ConfigError):
        SampleDistribution(np.array([1.0, np.inf]))
    with pytest.raises(ConfigError):
        SampleDistribution(np.array([1.0]), rule="nearest")
    with pytest.raises(ConfigError):
        SampleDistribution(np.array([1.0]), rule="symmetric_conformal")


finite = st.floats(min_value=-5_000, max_value=5_000, allow_nan=False, allow_infinity=False)


@settings(max_examples=150, deadline=None)
@given(
    residuals=st.lists(finite, min_size=1, max_size=60),
    prediction=st.floats(min_value=0, max_value=20_000, allow_nan=False),
    method=st.sampled_from(["additive", "conformal", "relative"]),
)
def test_property_quantiles_never_cross(
    residuals: list[float], prediction: float, method: str
) -> None:
    calibration = cal_set(residuals, predictions=[1_000.0] * len(residuals))
    if method == "additive":
        distribution = empirical_additive(prediction, calibration)
    elif method == "conformal":
        distribution = split_conformal(prediction, calibration)
    else:
        relative = empirical_relative(max(prediction, 1.0), calibration)
        assert relative is not None
        distribution = relative
    q = distribution.quantiles(TAUS)
    present = q[~np.isnan(q)]
    assert np.all(present >= 0)
    assert np.all(np.diff(present) >= 0)
    # Every central interval contains its narrower intervals and the median.
    if not np.isnan(q).any():
        assert q[0] <= q[1] <= q[3] <= q[5] <= q[6]


@settings(max_examples=150, deadline=None)
@given(
    values=st.lists(finite, min_size=1, max_size=80),
    thresholds=st.lists(finite, min_size=1, max_size=10),
)
def test_property_exceedance_is_bounded_and_falls_with_the_threshold(
    values: list[float], thresholds: list[float]
) -> None:
    distribution = SampleDistribution(np.array(values))
    limits = np.array(sorted(thresholds))
    p = distribution.exceedance(limits)
    m = len(values)
    assert np.all(p > 0)
    assert np.all(p < 1)
    assert np.all(np.diff(p) <= 0)
    for limit, chance in zip(limits, p, strict=True):
        above = sum(v > limit for v in values)
        assert chance == pytest.approx((above + 0.5) / (m + 1))
    check_ordering(limits, p)


def test_exceedance_closed_form() -> None:
    distribution = SampleDistribution(np.arange(1.0, 10.0))  # 1..9
    p = distribution.exceedance(np.array([0.0, 5.0, 9.0, 100.0]))
    # #{v > T}: 9, 4, 0, 0 -> (k + 0.5) / 10.
    assert list(p) == pytest.approx([0.95, 0.45, 0.05, 0.05])


def test_exceedance_and_quantiles_agree_approximately() -> None:
    # At the interpolated tau-quantile the exceedance probability is close to 1 - tau.
    rng = np.random.default_rng(3)
    values = rng.normal(0, 1, 365)
    distribution = SampleDistribution(values)
    q = distribution.quantiles(TAUS)  # clipped at 0, so use the upper quantiles only
    for tau, value in zip(TAUS[3:], q[3:], strict=True):
        assert abs(distribution.exceedance(np.array([value]))[0] - (1 - tau)) <= 1.5 / 366


def test_check_ordering_rejects_violations() -> None:
    with pytest.raises(AssertionError, match="higher"):
        check_ordering(np.array([1.0, 2.0]), np.array([0.2, 0.3]))
    with pytest.raises(AssertionError, match="outside"):
        check_ordering(np.array([1.0]), np.array([1.2]))
    check_ordering(np.array([np.nan, 2.0]), np.array([np.nan, 0.3]))


# --- Thresholds -------------------------------------------------------------------------------


def test_historical_threshold_reads_only_the_trailing_window() -> None:
    values = [float(v) for v in range(1, 401)]
    series = series_from(date(2023, 1, 1), values)
    cutoff = date(2023, 12, 31)  # day index 364, value 365
    spec = HistoricalThresholdConfig(
        kind="historical_quantile",
        label="p75",
        window_days=180,
        quantile=0.75,
        min_observations=150,
        description="test",
    )
    window = trailing_window(series, cutoff, 180)
    assert list(window) == [float(v) for v in range(186, 366)]
    assert historical_threshold(spec, series, cutoff) == pytest.approx(type7(list(window), 0.75))
    # Values after the cutoff never matter.
    changed = series_from(date(2023, 1, 1), values[:365] + [1e6] * 35)
    assert historical_threshold(spec, changed, cutoff) == historical_threshold(spec, series, cutoff)
    # Too few observations: unavailable, not guessed.
    short = series_from(date(2023, 1, 1), values[:100])
    assert historical_threshold(spec, short, date(2023, 4, 10)) is None


def test_threshold_values_carry_their_provenance() -> None:
    series = series_from(date(2023, 1, 1), [float(v) for v in range(1, 201)])
    hypothetical = HypotheticalThresholdConfig(
        kind="hypothetical",
        label="cap",
        value=150.0,
        units="requests_per_day",
        provenance="HYPOTHETICAL",
        assumptions="a number for this test only, not a capacity",
    )
    historical = HistoricalThresholdConfig(
        kind="historical_quantile",
        label="p75",
        window_days=180,
        quantile=0.75,
        min_observations=150,
        description="test",
    )
    values = threshold_values((historical, hypothetical), series, date(2023, 7, 19))
    assert [v.provenance for v in values] == [PROVENANCE_HISTORY, PROVENANCE_HYPOTHETICAL]
    assert values[1].value == 150.0
    assert "not a verified NYC agency service capacity" in HYPOTHETICAL_NOTICE


# --- Scores -------------------------------------------------------------------------------------


def test_coverage_is_closed_at_both_ends() -> None:
    assert list(coverage([5, 10, 15, 4], [4, 11, 15, 4], [6, 12, 20, 4])) == [1, 0, 1, 1]


def test_interval_score_exact() -> None:
    # alpha 0.2: inside -> width 2; below by 1 -> 1 + 10 * 1 = 11; above by 5 -> 5 + 10 * 5.
    assert list(interval_score([5, 10, 25], [4, 11, 15], [6, 12, 20], 0.2)) == [2, 11, 55]
    with pytest.raises(ConfigError):
        interval_score([1], [0], [2], 1.0)


def test_pinball_loss_exact() -> None:
    # tau 0.9: y above q by 2 -> 0.9 * 2 = 1.8; y below q by 2 -> 0.1 * 2 = 0.2; equal -> 0.
    assert pinball_loss([10, 10, 10], [8, 12, 10], 0.9) == pytest.approx([1.8, 0.2, 0.0])
    with pytest.raises(ConfigError):
        pinball_loss([1], [1], 0.0)


def test_wis_exact_and_equal_to_scaled_pinball_sum() -> None:
    # One 80 % interval [8, 12] and median 9, y = 10: IS = 4, so
    # WIS = (0.5 * |10 - 9| + 0.1 * 4) / 1.5 = 0.6.
    assert weighted_interval_score([10], [9], [[8]], [[12]], [0.2]) == pytest.approx([0.6])
    rng = np.random.default_rng(11)
    y = rng.normal(100, 20, 50)
    q = np.sort(rng.normal(100, 25, (50, 5)), axis=1)  # tau 0.025, 0.1, 0.5, 0.9, 0.975
    wis = weighted_interval_score(y, q[:, 2], [q[:, 1], q[:, 0]], [q[:, 3], q[:, 4]], [0.2, 0.05])
    taus = [0.025, 0.1, 0.5, 0.9, 0.975]
    pinball = sum(pinball_loss(y, q[:, k], t) for k, t in enumerate(taus))
    assert wis == pytest.approx(pinball / 2.5)
    with pytest.raises(ConfigError):
        weighted_interval_score([1], [1], [[0]], [], [0.2])


def test_brier_and_reliability_exact() -> None:
    assert brier_score([0.2, 0.9, 0.5], [0, 1, 1]) == pytest.approx([0.04, 0.01, 0.25])
    with pytest.raises(ConfigError):
        brier_score([1.2], [1])
    with pytest.raises(ConfigError):
        brier_score([0.5], [0.5])
    table = reliability_table([0.05, 0.15, 0.12, 1.0, 0.95], [0, 1, 0, 1, 1], bins=10)
    assert [row["n"] for row in table] == [1, 2, 0, 0, 0, 0, 0, 0, 0, 2]
    assert table[1]["mean_probability"] == pytest.approx(0.135)
    assert table[1]["observed_frequency"] == 0.5
    assert table[2]["mean_probability"] is None
    assert table[9]["observed_frequency"] == 1.0
    with pytest.raises(ConfigError):
        reliability_table([0.5], [1], bins=0)


def test_scores_need_aligned_arrays() -> None:
    with pytest.raises(ConfigError):
        coverage([1, 2], [0], [3, 4])


def test_origin_test_is_the_m2_dependence_test_of_per_origin_means() -> None:
    rng = np.random.default_rng(5)
    origins = pd.date_range("2024-01-01", periods=120, freq="D")
    cell = pd.DataFrame(
        {
            "origin": np.repeat(origins, 3),
            "horizon": np.tile([1, 2, 3], 120),
        }
    )
    values = pd.Series(rng.binomial(1, 0.8, len(cell)).astype(float), index=cell.index)
    result = origin_test(cell, values, first=1, last=3, offset=0.8)
    test, effective = dependence_test(
        per_origin_mean(cell, values - 0.8, 3), horizon=3, stride_days=1
    )
    assert result["test_computed"] is True
    assert result["mean"] == pytest.approx(values.mean() - 0.8)
    assert result["ci_low"] == test.ci_low
    assert result["ci_high"] == test.ci_high
    assert result["p_value_conservative"] == test.p_value_conservative
    assert result["effective_sample_size"] == effective
    assert result["n_origins_tested"] == 120
    # Too few origins: the test is not computed, and no interval is claimed.
    short = origin_test(cell.iloc[:30], values.iloc[:30], first=1, last=3)
    assert short["test_computed"] is False
    assert short["ci_low"] is None


def test_origin_test_uses_complete_origins_only() -> None:
    origins = pd.date_range("2024-01-01", periods=60, freq="D")
    cell = pd.DataFrame({"origin": np.repeat(origins, 3), "horizon": np.tile([1, 2, 3], 60)})
    cell = cell.drop(index=[4]).reset_index(drop=True)  # origin 2 lacks horizon 2
    values = pd.Series(np.arange(len(cell), dtype=float) % 5, index=cell.index)
    result = origin_test(cell, values, first=1, last=3)
    assert result["n_origins_tested"] == 59
