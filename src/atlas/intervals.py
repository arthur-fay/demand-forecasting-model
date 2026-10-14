"""Predictive distributions and prediction-interval methods (M3).

Every method turns a point prediction and a :class:`~atlas.calibration.CalibrationSet`
into an explicit predictive distribution. Quantiles, central intervals and exceedance
probabilities all come from that one distribution; nothing is inferred from interval
endpoints.

==========================  ============================================================
``empirical_quantile``      sample ``prediction + r_i`` (additive) or
                            ``prediction * actual_i / prediction_i`` (relative); quantiles
                            by linear interpolation of the order statistics (the usual
                            "type 7" sample quantile)
``split_conformal``         symmetric sample ``prediction +/- |r_i|``; the ``tau``-quantile
                            above the median is ``prediction + |r|_(k)`` with
                            ``k = ceil((n + 1)(2 tau - 1))`` (below: minus, with
                            ``ceil((n + 1)(1 - 2 tau))``). The central interval of level
                            ``1 - a`` is the split-conformal interval with rank
                            ``ceil((n + 1)(1 - a))``; a rank above ``n`` is unavailable
``gaussian_one_step``       baseline: Normal(prediction, s^2), ``s`` = RMS of horizon-1
                            residuals, at every horizon
``climatology``             baseline: the observed daily counts of a trailing window
==========================  ============================================================

Exceedance. For a sample distribution of ``m`` values,
``P(Y > T) = (#{z_i > T} + 1/2) / (m + 1)``: the midpoint of the conformal predictive
bounds ``[k / (m + 1), (k + 1) / (m + 1)]``. It is strictly between 0 and 1 and
non-increasing in ``T``, so a higher threshold never gets a higher probability.

Validity. Demand is non-negative, so every quantile is clipped at 0 (which never lowers
coverage). Quantiles are non-decreasing in their level by construction, and
:func:`check_quantiles` asserts it for every forecast, so intervals never cross. Nothing
forces intervals to widen with the horizon: each horizon is calibrated on its own errors.

The finite-sample guarantee of split conformal assumes exchangeable residuals. Forecast
errors from overlapping paths, regime changes and growth are not exchangeable, so no
coverage is guaranteed; it is measured (:mod:`atlas.scoring`).
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from statistics import NormalDist
from typing import Final

import numpy as np

from atlas.calibration import CalibrationSet
from atlas.errors import ConfigError
from atlas.uncertainty_config import (
    ClimatologyConfig,
    EmpiricalQuantileConfig,
    GaussianOneStepConfig,
    MethodConfig,
    SplitConformalConfig,
)

#: Ranks are computed from ``(n + 1) * level`` rounded to this many decimals first, so
#: that a level such as 0.95 (not exact in binary) does not move a rank by one.
_RANK_DECIMALS: Final = 9
_STANDARD_NORMAL: Final = NormalDist()


def linear_quantiles(sorted_values: np.ndarray, levels: np.ndarray) -> np.ndarray:
    """Sample quantiles by linear interpolation ("type 7"), as ``numpy.quantile`` with
    ``method="linear"`` computes them, for an already sorted sample (much faster per call)."""
    n = sorted_values.size
    virtual = (n - 1) * levels
    virtual = np.clip(virtual, 0.0, n - 1.0)
    previous = np.floor(virtual).astype("int64")
    following = np.minimum(previous + 1, n - 1)
    gamma = virtual - previous
    low, high = sorted_values[previous], sorted_values[following]
    difference = high - low
    result: np.ndarray = np.where(
        gamma >= 0.5, high - difference * (1.0 - gamma), low + difference * gamma
    )
    return result


def conformal_rank(n: int, level: float) -> int:
    """``ceil((n + 1) * level)``, robust to binary rounding of ``level``."""
    return math.ceil(round((n + 1) * level, _RANK_DECIMALS))


class PredictiveDistribution(ABC):
    """A predictive distribution of one day's demand."""

    @abstractmethod
    def quantiles(self, levels: np.ndarray) -> np.ndarray:
        """Quantiles at ``levels`` (increasing, in (0, 1)); NaN where unavailable."""

    @abstractmethod
    def exceedance(self, thresholds: np.ndarray) -> np.ndarray:
        """``P(Y > T)`` for each threshold, in [0, 1] and non-increasing in ``T``."""


@dataclass(frozen=True)
class SampleDistribution(PredictiveDistribution):
    """The empirical distribution of ``values`` (sorted on construction).

    ``rule`` is ``interpolated`` (linear, type 7) or ``symmetric_conformal`` (used by
    :func:`split_conformal`, which supplies ``centre`` and ``absolute``).
    """

    values: np.ndarray
    rule: str = "interpolated"
    centre: float | None = None
    absolute: np.ndarray | None = None

    def __post_init__(self) -> None:
        values = np.sort(np.asarray(self.values, dtype="float64"))
        if values.size == 0 or not np.isfinite(values).all():
            raise ConfigError("a sample distribution needs at least one finite value")
        object.__setattr__(self, "values", values)
        if self.rule not in ("interpolated", "symmetric_conformal"):
            raise ConfigError(f"unknown quantile rule {self.rule!r}")
        if self.rule == "symmetric_conformal":
            if self.centre is None or self.absolute is None:
                raise ConfigError("the symmetric conformal rule needs a centre and |residuals|")
            object.__setattr__(self, "absolute", np.sort(np.abs(self.absolute)))

    def quantiles(self, levels: np.ndarray) -> np.ndarray:
        taus = np.asarray(levels, dtype="float64")
        if self.rule == "interpolated":
            raw = linear_quantiles(self.values, taus)
        else:
            raw = self._conformal(taus)
        return check_quantiles(np.where(np.isnan(raw), np.nan, np.maximum(raw, 0.0)), taus)

    def _conformal(self, taus: np.ndarray) -> np.ndarray:
        if self.absolute is None or self.centre is None:  # pragma: no cover - checked above
            raise AssertionError("missing conformal inputs")
        n = int(self.absolute.size)
        result = np.full(taus.size, np.nan)
        for position, tau in enumerate(taus):
            if abs(tau - 0.5) <= 1e-12:
                result[position] = self.centre
                continue
            rank = conformal_rank(n, abs(2.0 * tau - 1.0))
            if 1 <= rank <= n:
                offset = float(self.absolute[rank - 1])
                result[position] = self.centre + offset if tau > 0.5 else self.centre - offset
        return result

    def exceedance(self, thresholds: np.ndarray) -> np.ndarray:
        limits = np.asarray(thresholds, dtype="float64")
        m = self.values.size
        above = m - np.searchsorted(self.values, limits, side="right")
        return (above + 0.5) / (m + 1.0)


@dataclass(frozen=True)
class GaussianDistribution(PredictiveDistribution):
    mean: float
    sd: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.mean) and math.isfinite(self.sd)) or self.sd <= 0:
            raise ConfigError("a Gaussian distribution needs a finite mean and a positive sd")

    def quantiles(self, levels: np.ndarray) -> np.ndarray:
        taus = np.asarray(levels, dtype="float64")
        raw = np.array([self.mean + self.sd * _STANDARD_NORMAL.inv_cdf(float(t)) for t in taus])
        return check_quantiles(np.maximum(raw, 0.0), taus)

    def exceedance(self, thresholds: np.ndarray) -> np.ndarray:
        limits = np.asarray(thresholds, dtype="float64")
        return np.array(
            [1.0 - _STANDARD_NORMAL.cdf((float(t) - self.mean) / self.sd) for t in limits]
        )


def check_quantiles(values: np.ndarray, levels: np.ndarray) -> np.ndarray:
    """Assert non-crossing, non-negative quantiles (ignoring unavailable ones)."""
    if values.shape != levels.shape:
        raise AssertionError("one quantile per level is required")
    if np.any(np.diff(levels) <= 0):
        raise AssertionError("quantile levels must be strictly increasing")
    present = values[~np.isnan(values)]
    if np.any(present < 0) or np.any(np.diff(present) < 0):
        raise AssertionError(f"quantiles cross or are negative: {values}")
    return values


# --- Methods ----------------------------------------------------------------------------


def empirical_additive(prediction: float, calibration: CalibrationSet) -> SampleDistribution:
    return SampleDistribution(prediction + calibration.residuals)


def empirical_relative(prediction: float, calibration: CalibrationSet) -> SampleDistribution | None:
    """``prediction * actual_i / prediction_i`` over calibration forecasts with
    ``prediction_i > 0`` (``None`` if none, or if the issued prediction is not positive)."""
    positive = calibration.predictions > 0
    if prediction <= 0 or not positive.any():
        return None
    actual = calibration.predictions[positive] + calibration.residuals[positive]
    return SampleDistribution(prediction * actual / calibration.predictions[positive])


def split_conformal(prediction: float, calibration: CalibrationSet) -> SampleDistribution:
    absolute = np.abs(calibration.residuals)
    symmetric = np.concatenate([prediction - absolute, prediction + absolute])
    return SampleDistribution(
        symmetric, rule="symmetric_conformal", centre=prediction, absolute=absolute
    )


def gaussian_one_step(
    prediction: float, horizon_one: CalibrationSet
) -> GaussianDistribution | None:
    """Normal(prediction, s^2), ``s`` = RMS of the eligible horizon-1 residuals."""
    if horizon_one.n == 0:
        return None
    sd = float(np.sqrt(np.mean(horizon_one.residuals**2)))
    return GaussianDistribution(prediction, sd) if sd > 0 else None


def climatology(observations: np.ndarray, min_observations: int) -> SampleDistribution | None:
    observed = np.asarray(observations, dtype="float64")
    observed = observed[np.isfinite(observed)]
    if observed.size < min_observations:
        return None
    return SampleDistribution(observed)


def model_distribution(
    method: MethodConfig, prediction: float, calibration: CalibrationSet
) -> PredictiveDistribution | None:
    """The distribution of a residual-based method (``None`` when unavailable).

    ``calibration`` is the horizon's own set, except for ``gaussian_one_step``, which
    takes the horizon-1 set.
    """
    if calibration.n == 0:
        return None
    match method:
        case EmpiricalQuantileConfig(score="additive"):
            return empirical_additive(prediction, calibration)
        case EmpiricalQuantileConfig(score="relative"):
            return empirical_relative(prediction, calibration)
        case SplitConformalConfig():
            return split_conformal(prediction, calibration)
        case GaussianOneStepConfig():
            return gaussian_one_step(prediction, calibration)
        case ClimatologyConfig():
            raise ConfigError("climatology does not use residuals")
    raise AssertionError(f"unhandled method {method!r}")  # pragma: no cover
