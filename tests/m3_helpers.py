"""Shared helpers for the M3 tests: hand-made backtest records and synthetic configurations.

Expected values in the M3 tests come from closed forms or from plain-Python reference
calculations written in the tests, never from the code under test.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from atlas.metrics import HorizonBucket
from atlas.uncertainty_config import UncertaintyConfig, parse_uncertainty_config

BUCKETS = (
    HorizonBucket("day_1", 1, 1),
    HorizonBucket("days_2_4", 2, 4),
    HorizonBucket("days_5_7", 5, 7),
)
GRID = (0.025, 0.1, 0.25, 0.5, 0.75, 0.9, 0.975)
D0 = date(2024, 1, 1)


def day(offset: int) -> date:
    return D0 + timedelta(days=offset)


def records_frame(
    rows: Iterable[tuple[str, date, int, float, float | None]],
    *,
    status: Callable[[str, date, int], str] | None = None,
) -> pd.DataFrame:
    """M2-shaped records from ``(model_id, origin, horizon, prediction, actual)`` tuples.

    ``actual`` None gives ``missing_actual``; ``status`` may override the status.
    """
    records = []
    for model_id, origin, horizon, prediction, actual in rows:
        state = "ok" if actual is not None else "missing_actual"
        if status is not None:
            state = status(model_id, origin, horizon)
        records.append(
            {
                "record_type": "historical_backtest",
                "period": "m3_residuals",
                "model_id": model_id,
                "origin": pd.Timestamp(origin),
                "target_date": pd.Timestamp(origin + timedelta(days=horizon)),
                "horizon": horizon,
                "prediction": np.nan if state == "failed" else prediction,
                "actual": np.nan if actual is None else actual,
                "status": state,
            }
        )
    return pd.DataFrame.from_records(records)


def grid_records(
    model_id: str,
    first: date,
    n_origins: int,
    max_horizon: int,
    residual: Callable[[int, int], float],
    prediction: float = 1000.0,
) -> pd.DataFrame:
    """Records at origins ``first .. first + n - 1`` for horizons ``1..H``.

    The residual (actual - prediction) of origin index ``i`` and horizon ``h`` is
    ``residual(i, h)``.
    """
    rows = [
        (model_id, first + timedelta(days=i), h, prediction, prediction + residual(i, h))
        for i in range(n_origins)
        for h in range(1, max_horizon + 1)
    ]
    return records_frame(rows)


BASE_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "experiment_id": "synthetic-m3",
    "description": "synthetic configuration for tests",
    "backtest_config": "configs/backtest/none.json",
    "backtest_config_fingerprint": "0" * 64,
    "recommendation": None,
    "max_horizon": 7,
    "point_models": ["med", "sn"],
    "primary_model": "med",
    "residual_first_origin": "2023-03-01",
    "residual_last_origin": "2024-06-30",
    "levels": [0.8, 0.95],
    "quantile_levels": list(GRID),
    "calibration": {
        "data_latency_days": 0,
        "min_calibration_origins": 30,
        "min_effective_calibration": 5,
        "pooling": "horizon_then_bucket",
    },
    "schemes": [
        {"scheme_id": "rolling90", "kind": "rolling", "window_days": 90},
        {"scheme_id": "fixed90", "kind": "fixed", "window_days": 90},
    ],
    "methods": [
        {"type": "empirical_quantile", "method_id": "empirical_additive", "score": "additive"},
        {"type": "empirical_quantile", "method_id": "empirical_relative", "score": "relative"},
        {"type": "split_conformal", "method_id": "conformal_absolute"},
        {"type": "gaussian_one_step", "method_id": "gaussian_one_step"},
        {
            "type": "climatology",
            "method_id": "climatology_180d",
            "window_days": 180,
            "min_observations": 150,
        },
    ],
    "candidates": [
        {"method": "empirical_additive", "scheme": "rolling90"},
        {"method": "conformal_absolute", "scheme": "rolling90"},
        {"method": "empirical_additive", "scheme": "fixed90"},
    ],
    "baselines": [
        {"method": "gaussian_one_step", "scheme": "rolling90"},
        {"method": "climatology_180d"},
    ],
    "periods": [
        {
            "name": "sel",
            "role": "selection",
            "first_origin": "2023-07-01",
            "last_origin": "2023-10-31",
        },
        {
            "name": "ev",
            "role": "evaluation",
            "first_origin": "2023-11-15",
            "last_origin": "2024-06-15",
        },
    ],
    "selection": {
        "period": "sel",
        "point_model": "med",
        "criterion": "wis",
        "first_horizon": 1,
        "last_horizon": 7,
        "max_unavailable_rate": 0.0,
    },
    "thresholds": [
        {
            "kind": "historical_quantile",
            "label": "trailing180_p75",
            "window_days": 180,
            "quantile": 0.75,
            "min_observations": 150,
            "description": "p75 of the last 180 days",
        },
        {
            "kind": "hypothetical",
            "label": "hypothetical_low",
            "value": 9000,
            "units": "requests_per_day",
            "provenance": "HYPOTHETICAL",
            "assumptions": "an illustrative number for tests, not a capacity",
        },
        {
            "kind": "hypothetical",
            "label": "hypothetical_high",
            "value": 11000,
            "units": "requests_per_day",
            "provenance": "HYPOTHETICAL",
            "assumptions": "a second illustrative number for tests, not a capacity",
        },
    ],
    "decision_criteria": {
        "coverage": [{"level": 0.8, "tolerance_pct": 5.0}, {"level": 0.95, "tolerance_pct": 3.0}],
        "alpha": 0.05,
        "min_effective_sample_size": 30,
        "exceedance_max_calibration_gap": 0.05,
        "exceedance_reference": "climatology_180d",
    },
    "sensitivity": {"latency_days": [3], "strata": True},
}


def config_dict(**changes: Any) -> dict[str, Any]:
    """A deep copy of :data:`BASE_CONFIG` with top-level keys replaced."""
    payload = copy.deepcopy(BASE_CONFIG)
    payload.update(copy.deepcopy(changes))
    return payload


def make_config(**changes: Any) -> UncertaintyConfig:
    return parse_uncertainty_config(config_dict(**changes))


def nested(payload: Mapping[str, Any], **changes: Any) -> dict[str, Any]:
    """Replace keys of a nested mapping (one level), returning a copy."""
    result = copy.deepcopy(dict(payload))
    result.update(changes)
    return result
