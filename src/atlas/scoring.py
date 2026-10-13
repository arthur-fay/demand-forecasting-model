"""Calibration and scoring of probabilistic forecasts (M3).

Point scores, for an outcome ``y``:

=====================  ===================================================================
coverage               ``1`` if ``lower <= y <= upper`` (closed interval), else ``0``
width                  ``upper - lower``
interval score         Winkler / Gneiting-Raftery score of a central ``(1 - a)`` interval:
                       ``(u - l) + (2 / a)(l - y) 1{y < l} + (2 / a)(y - u) 1{y > u}``
                       (proper; lower is better; in requests per day)
pinball loss           ``(y - q)(tau - 1{y < q})`` for the ``tau``-quantile ``q``
WIS                    weighted interval score of the median and ``K`` central intervals
                       (Bracher et al., 2021): ``(|y - m| / 2 + sum_k a_k / 2 * IS_k) /
                       (K + 1/2)``
Brier score            ``(p - 1{y > T})^2`` for an exceedance probability ``p``
=====================  ===================================================================

Dependence. Forecasts from neighbouring origins share target days and forecast paths, so
scores are not independent. Every uncertainty statement reuses the M2 machinery
(:func:`atlas.metrics.dependence_test`): one value per origin (the mean over the scope's
horizons, origins with every horizon present only), a HAC variance with overlap equal to
the scope's last horizon, the HLN correction, and the HAC effective sample size. For
coverage the tested series is ``coverage - level``; a test or interval is not computed on
an origin grid with gaps or for fewer than ``max(3h, 50)`` origins.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from atlas.errors import ConfigError
from atlas.metrics import dependence_test, per_origin_mean

Array = np.ndarray


def _arrays(*values: Any) -> list[Array]:
    arrays = [np.asarray(v, dtype="float64") for v in values]
    shape = arrays[0].shape
    if any(a.shape != shape for a in arrays) or arrays[0].ndim != 1:
        raise ConfigError("scores need aligned one-dimensional arrays")
    return arrays


def coverage(y: Any, lower: Any, upper: Any) -> Array:
    outcome, low, high = _arrays(y, lower, upper)
    return ((low <= outcome) & (outcome <= high)).astype("float64")


def interval_score(y: Any, lower: Any, upper: Any, alpha: float) -> Array:
    if not 0.0 < alpha < 1.0:
        raise ConfigError("alpha must be strictly between 0 and 1")
    outcome, low, high = _arrays(y, lower, upper)
    below = np.where(outcome < low, low - outcome, 0.0)
    above = np.where(outcome > high, outcome - high, 0.0)
    result: Array = (high - low) + (2.0 / alpha) * below + (2.0 / alpha) * above
    return result


def pinball_loss(y: Any, quantile: Any, tau: float) -> Array:
    if not 0.0 < tau < 1.0:
        raise ConfigError("tau must be strictly between 0 and 1")
    outcome, q = _arrays(y, quantile)
    result: Array = (outcome - q) * (tau - (outcome < q).astype("float64"))
    return result


def weighted_interval_score(
    y: Any,
    median: Any,
    lowers: Sequence[Any],
    uppers: Sequence[Any],
    alphas: Sequence[float],
) -> Array:
    if not (len(lowers) == len(uppers) == len(alphas)) or not alphas:
        raise ConfigError("WIS needs one lower and one upper bound per alpha")
    outcome, centre = _arrays(y, median)
    total = 0.5 * np.abs(outcome - centre)
    for lower, upper, alpha in zip(lowers, uppers, alphas, strict=True):
        total = total + (alpha / 2.0) * interval_score(outcome, lower, upper, alpha)
    result: Array = total / (len(alphas) + 0.5)
    return result


def brier_score(probability: Any, event: Any) -> Array:
    p, e = _arrays(probability, event)
    if np.any((p < 0) | (p > 1)) or not np.isin(e, (0.0, 1.0)).all():
        raise ConfigError("Brier scores need probabilities in [0, 1] and 0/1 events")
    result: Array = (p - e) ** 2
    return result


def reliability_table(probability: Any, event: Any, bins: int = 10) -> list[dict[str, Any]]:
    """Equal-width probability bins: count, mean probability and observed frequency.

    Bin ``k`` holds ``k / bins <= p < (k + 1) / bins`` (the last bin includes 1).
    """
    p, e = _arrays(probability, event)
    if bins < 1:
        raise ConfigError("at least one bin is required")
    index = np.minimum((p * bins).astype("int64"), bins - 1)
    rows = []
    for k in range(bins):
        chosen = index == k
        count = int(chosen.sum())
        rows.append(
            {
                "bin_low": k / bins,
                "bin_high": (k + 1) / bins,
                "n": count,
                "mean_probability": float(p[chosen].mean()) if count else None,
                "observed_frequency": float(e[chosen].mean()) if count else None,
            }
        )
    return rows


# --- Dependence-aware summaries ----------------------------------------------------------


def origin_test(
    cell: pd.DataFrame, values: pd.Series, *, first: int, last: int, offset: float = 0.0
) -> dict[str, Any]:
    """HAC test and interval for the mean of ``values - offset`` over a horizon scope.

    ``cell`` holds the rows of the scope (``origin`` and ``horizon`` columns); ``values``
    is aligned with it. Returns the DM-style test of mean zero, its 95 % interval (larger of
    the two HAC standard errors, so it agrees with the conservative p-value), and the HAC
    effective sample size of the per-origin series.
    """
    per_origin = per_origin_mean(cell, values - offset, last - first + 1)
    test, effective = dependence_test(per_origin, horizon=last, stride_days=1)
    return {
        "n_origins_tested": int(per_origin.size),
        "test_computed": test.computed,
        "test_reason": test.reason,
        "mean": test.mean_difference,
        "ci_low": test.ci_low,
        "ci_high": test.ci_high,
        "p_value": test.p_value,
        "p_value_bartlett": test.bartlett_p_value,
        "p_value_conservative": test.p_value_conservative,
        "effective_sample_size": effective,
    }


def mean_or_none(values: Any) -> float | None:
    array = np.asarray(values, dtype="float64")
    return float(array.mean()) if array.size else None
