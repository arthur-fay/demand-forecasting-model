"""Calibration data: residual extraction, eligibility by target date and latency, pooling.

The central rule is ``residual.target_date <= calibration_origin - data_latency`` with
``calibration_origin <= issue_origin``. Every expected set below is computed by a plain
filter in the test, independently of the store's slicing.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from atlas import calibration as calibration_module
from atlas.calibration import (
    CalibrationPolicy,
    Pooling,
    ResidualStore,
    Support,
    _HorizonResiduals,
)
from atlas.errors import ConfigError, LeakageError
from atlas.metrics import HorizonBucket, effective_sample_size
from tests.m3_helpers import BUCKETS, day, grid_records, records_frame

POLICY = CalibrationPolicy(
    data_latency_days=0, min_calibration_origins=1, min_effective_calibration=0
)


def residual(i: int, h: int) -> float:
    """A distinct, recognisable residual per (origin index, horizon)."""
    return 100.0 * i + h


def store(n_origins: int = 40, max_horizon: int = 7) -> ResidualStore:
    return ResidualStore(grid_records("m", day(0), n_origins, max_horizon, residual), "m", 7)


def expected(n_origins: int, horizon: int, cutoff: date, first: date | None = None) -> list[float]:
    """Plain filter: residuals of horizon ``h`` whose target lies in ``[first, cutoff]``."""
    values = []
    for i in range(n_origins):
        target = day(i) + timedelta(days=horizon)
        if target <= cutoff and (first is None or target >= first):
            values.append(residual(i, horizon))
    return values


@pytest.mark.parametrize("latency", [0, 1, 3, 10])
@pytest.mark.parametrize("horizon", [1, 3, 7])
def test_eligible_residuals_end_at_origin_minus_latency(latency: int, horizon: int) -> None:
    issue = day(25)
    policy = CalibrationPolicy(latency, 1, 0, pool_buckets=False)
    result = store().calibration_set(
        horizon=horizon,
        issue_origin=issue,
        calibration_origin=issue,
        window_days=None,
        policy=policy,
        buckets=BUCKETS,
    )
    cutoff = issue - timedelta(days=latency)
    assert result.cutoff == cutoff
    assert list(result.residuals) == expected(40, horizon, cutoff)
    assert result.last_target == cutoff
    assert all(date.fromordinal(int(t)) <= cutoff for t in result.targets)
    # The residual targeting the cutoff day is in; the one after it is out.
    assert residual(25 - latency - horizon, horizon) in result.residuals
    assert residual(25 - latency - horizon + 1, horizon) not in result.residuals


def test_target_on_the_issue_day_is_eligible_with_zero_latency() -> None:
    # Origin o is the last observed day (M2 convention), so a target dated o is observed.
    result = store().calibration_set(
        horizon=1,
        issue_origin=day(10),
        calibration_origin=day(10),
        window_days=None,
        policy=POLICY,
        buckets=BUCKETS,
    )
    assert result.last_target == day(10)
    assert result.residuals[-1] == residual(9, 1)


@pytest.mark.parametrize("window", [1, 5, 12])
def test_window_keeps_only_the_last_window_days_of_targets(window: int) -> None:
    issue = day(30)
    result = store().calibration_set(
        horizon=2,
        issue_origin=issue,
        calibration_origin=issue,
        window_days=window,
        policy=POLICY,
        buckets=BUCKETS,
    )
    first = issue - timedelta(days=window - 1)
    assert result.first_target == first
    assert list(result.residuals) == expected(40, 2, issue, first)
    assert result.n == window


def test_fixed_calibration_uses_the_calibration_origin_cutoff() -> None:
    result = store().calibration_set(
        horizon=3,
        issue_origin=day(35),
        calibration_origin=day(20),
        window_days=None,
        policy=POLICY,
        buckets=BUCKETS,
    )
    assert result.cutoff == day(20)
    assert list(result.residuals) == expected(40, 3, day(20))
    assert result.issue_origin == day(35)


def test_calibrating_with_a_later_calibration_origin_is_refused() -> None:
    with pytest.raises(LeakageError, match="later data"):
        store().calibration_set(
            horizon=1,
            issue_origin=day(10),
            calibration_origin=day(11),
            window_days=None,
            policy=POLICY,
            buckets=BUCKETS,
        )


def test_guard_catches_a_slice_that_returns_a_later_target(monkeypatch: pytest.MonkeyPatch) -> None:
    residuals = store()
    original = ResidualStore._slice

    def leaky(self: ResidualStore, horizon: int, first: int | None, cutoff: int):  # type: ignore[no-untyped-def]
        return original(self, horizon, first, cutoff + 5)

    monkeypatch.setattr(ResidualStore, "_slice", leaky)
    with pytest.raises(LeakageError, match="after the cutoff"):
        residuals.calibration_set(
            horizon=1,
            issue_origin=day(20),
            calibration_origin=day(20),
            window_days=None,
            policy=POLICY,
            buckets=BUCKETS,
        )


def test_only_ok_records_become_residuals() -> None:
    rows = [("m", day(i), 1, 1000.0, 1000.0 + i) for i in range(10)]

    def status(model_id: str, origin: date, horizon: int) -> str:
        return {day(2): "failed", day(4): "missing_actual", day(6): "beyond_snapshot"}.get(
            origin, "ok"
        )

    frame = records_frame(rows, status=status)
    frame.loc[frame["status"] == "missing_actual", "actual"] = np.nan
    result = ResidualStore(frame, "m", 1).calibration_set(
        horizon=1,
        issue_origin=day(20),
        calibration_origin=day(20),
        window_days=None,
        policy=POLICY,
        buckets=BUCKETS,
    )
    assert list(result.residuals) == [0.0, 1.0, 3.0, 5.0, 7.0, 8.0, 9.0]


def test_residual_sign_is_actual_minus_prediction() -> None:
    frame = records_frame([("m", day(0), 1, 900.0, 1000.0), ("m", day(1), 1, 1100.0, 1000.0)])
    result = ResidualStore(frame, "m", 1).calibration_set(
        horizon=1,
        issue_origin=day(5),
        calibration_origin=day(5),
        window_days=None,
        policy=POLICY,
        buckets=BUCKETS,
    )
    assert list(result.residuals) == [100.0, -100.0]
    assert list(result.predictions) == [900.0, 1100.0]


def test_other_models_are_ignored() -> None:
    frame = pd.concat(
        [
            grid_records("a", day(0), 5, 1, lambda i, h: 1.0),
            grid_records("b", day(0), 5, 1, lambda i, h: 2.0),
        ]
    )
    result = ResidualStore(frame, "b", 1).calibration_set(
        horizon=1,
        issue_origin=day(10),
        calibration_origin=day(10),
        window_days=None,
        policy=POLICY,
        buckets=BUCKETS,
    )
    assert set(result.residuals) == {2.0}


def test_inconsistent_records_are_rejected() -> None:
    frame = records_frame([("m", day(0), 1, 900.0, 1000.0)])
    frame.loc[0, "target_date"] = pd.Timestamp(day(5))
    with pytest.raises(ConfigError, match="target_date"):
        ResidualStore(frame, "m", 1)
    good = records_frame([("m", day(0), 1, 900.0, 1000.0)])
    good.loc[0, "actual"] = np.inf
    with pytest.raises(ConfigError, match="finite"):
        ResidualStore(good, "m", 1)
    with pytest.raises(ConfigError, match="max_horizon"):
        ResidualStore(good, "m", 0)


def test_horizon_out_of_range() -> None:
    with pytest.raises(ConfigError, match="outside"):
        store().calibration_set(
            horizon=8,
            issue_origin=day(10),
            calibration_origin=day(10),
            window_days=None,
            policy=POLICY,
            buckets=BUCKETS,
        )


# --- Pooling and dependence ------------------------------------------------------------------


def test_horizon_specific_when_enough_distinct_origins() -> None:
    policy = CalibrationPolicy(0, 10, 0)
    result = store().calibration_set(
        horizon=3,
        issue_origin=day(20),
        calibration_origin=day(20),
        window_days=None,
        policy=policy,
        buckets=BUCKETS,
    )
    assert result.pooling is Pooling.HORIZON
    assert result.pooled_label is None
    assert result.n_origins == result.n == 18  # targets day 3..20


def test_bucket_pooling_counts_paths_once() -> None:
    # Horizon 4 at issue day 12 has origins 0..8 (9); the bucket 2..4 has origins 0..10
    # (11 distinct paths) but 9 + 10 + 11 = 30 residuals.
    issue = day(12)
    enough = CalibrationPolicy(0, 11, 0)
    pooled = store().calibration_set(
        horizon=4,
        issue_origin=issue,
        calibration_origin=issue,
        window_days=None,
        policy=enough,
        buckets=BUCKETS,
    )
    assert pooled.pooling is Pooling.BUCKET
    assert pooled.pooled_label == "days_2_4"
    assert pooled.n == 30
    assert pooled.n_origins == 11
    want = sorted(expected(40, 2, issue) + expected(40, 3, issue) + expected(40, 4, issue))
    assert sorted(pooled.residuals) == want
    # 30 residuals are not 12 independent paths: one more required origin and nothing is issued.
    too_few = CalibrationPolicy(0, 12, 0)
    none = store().calibration_set(
        horizon=4,
        issue_origin=issue,
        calibration_origin=issue,
        window_days=None,
        policy=too_few,
        buckets=BUCKETS,
    )
    assert none.pooling is Pooling.NONE
    assert none.support is Support.UNAVAILABLE
    assert none.n == 0
    assert none.n_effective is None
    assert none.last_target is None


def test_pooling_can_be_switched_off_and_single_horizon_buckets_never_pool() -> None:
    issue = day(12)
    policy = CalibrationPolicy(0, 11, 0, pool_buckets=False)
    result = store().calibration_set(
        horizon=4,
        issue_origin=issue,
        calibration_origin=issue,
        window_days=None,
        policy=policy,
        buckets=BUCKETS,
    )
    assert result.pooling is Pooling.NONE
    one = store().calibration_set(
        horizon=1,
        issue_origin=day(5),
        calibration_origin=day(5),
        window_days=None,
        policy=CalibrationPolicy(0, 10, 0),
        buckets=BUCKETS,
    )
    assert one.pooling is Pooling.NONE  # day_1 is a single-horizon bucket


def test_effective_size_and_support_labels() -> None:
    values = grid_records("m", day(0), 60, 1, lambda i, h: float((-1) ** i * (i % 7)))
    residuals = ResidualStore(values, "m", 1)
    result = residuals.calibration_set(
        horizon=1,
        issue_origin=day(60),
        calibration_origin=day(60),
        window_days=None,
        policy=CalibrationPolicy(0, 10, 0),
        buckets=BUCKETS,
    )
    assert result.n_effective == pytest.approx(effective_sample_size(result.residuals, 1))
    assert result.support is Support.SUPPORTED
    strict = residuals.calibration_set(
        horizon=1,
        issue_origin=day(60),
        calibration_origin=day(60),
        window_days=None,
        policy=CalibrationPolicy(0, 10, 1e6),
        buckets=BUCKETS,
    )
    assert strict.support is Support.THIN
    constant = ResidualStore(grid_records("m", day(0), 20, 1, lambda i, h: 5.0), "m", 1)
    flat = constant.calibration_set(
        horizon=1,
        issue_origin=day(30),
        calibration_origin=day(30),
        window_days=None,
        policy=CalibrationPolicy(0, 5, 0),
        buckets=BUCKETS,
    )
    assert flat.n_effective is None  # no variance: no effective size, labelled thin
    assert flat.support is Support.THIN


def test_pooled_effective_size_averages_each_path_first() -> None:
    part = _HorizonResiduals(
        targets=np.array([3, 4, 4, 5, 5, 6], dtype="int64"),
        origins=np.array([1, 2, 1, 3, 2, 3], dtype="int64"),
        residuals=np.array([1.0, 2.0, 3.0, 4.0, 6.0, 8.0]),
        predictions=np.ones(6),
    )
    # Per-origin means in origin order: origin 1 -> (1 + 3) / 2, 2 -> (2 + 6) / 2, 3 -> 6.
    want = effective_sample_size(np.array([2.0, 4.0, 6.0]), 3)
    assert calibration_module._effective_size(part, 3) == want


def test_policy_validation() -> None:
    with pytest.raises(ConfigError):
        CalibrationPolicy(-1, 1, 0)
    with pytest.raises(ConfigError):
        CalibrationPolicy(0, 0, 0)
    with pytest.raises(ConfigError):
        CalibrationPolicy(0, 1, -1.0)


@settings(max_examples=60, deadline=None)
@given(
    issue=st.integers(min_value=0, max_value=45),
    lag=st.integers(min_value=0, max_value=20),
    latency=st.integers(min_value=0, max_value=6),
    window=st.one_of(st.none(), st.integers(min_value=1, max_value=30)),
    horizon=st.integers(min_value=1, max_value=7),
)
def test_property_set_equals_brute_force_filter(
    issue: int, lag: int, latency: int, window: int | None, horizon: int
) -> None:
    calibration_origin = day(max(0, issue - lag))
    policy = CalibrationPolicy(latency, 1, 0, pool_buckets=False)
    result = store().calibration_set(
        horizon=horizon,
        issue_origin=day(issue),
        calibration_origin=calibration_origin,
        window_days=window,
        policy=policy,
        buckets=(HorizonBucket("all", 1, 7),),
    )
    cutoff = calibration_origin - timedelta(days=latency)
    first = None if window is None else cutoff - timedelta(days=window - 1)
    want = expected(40, horizon, cutoff, first)
    assert list(result.residuals) == want
    assert result.n_origins == len(want)
    assert all(date.fromordinal(int(t)) <= cutoff for t in result.targets)
    assert all(
        date.fromordinal(int(o)) < date.fromordinal(int(t))
        for o, t in zip(result.origins, result.targets, strict=True)
    )


def test_effective_size_uses_the_horizon_as_overlap() -> None:
    # Autocorrelated residuals: the effective size depends on the bandwidth 2h.
    rng = np.random.default_rng(4)
    noise = rng.normal(0, 1, 200)
    walk = np.convolve(noise, np.ones(6) / 6, mode="same")
    frame = grid_records("m", day(0), 200, 7, lambda i, h: float(walk[i]))
    result = ResidualStore(frame, "m", 7).calibration_set(
        horizon=5,
        issue_origin=day(210),
        calibration_origin=day(210),
        window_days=None,
        policy=CalibrationPolicy(0, 10, 0),
        buckets=BUCKETS,
    )
    assert result.n_effective == effective_sample_size(result.residuals, 5)
    assert result.n_effective != effective_sample_size(result.residuals, 1)
