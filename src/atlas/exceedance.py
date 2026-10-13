"""Exceedance thresholds and probabilities: ``P(demand on target > T | data at origin)`` (M3).

Two kinds of threshold, never confused:

* **historical** (provenance ``DERIVED_FROM_HISTORY``): a quantile of the observed daily
  counts in a trailing window that ends at each forecast's data cutoff, recomputed at every
  origin from information available then. The default, the 75th percentile of the last 180
  days with linear interpolation, is the legacy "planning capacity" rule: a high-demand
  reference, not a measured capacity;
* **hypothetical** (provenance ``HYPOTHETICAL``): a user-supplied number with units and
  stated assumptions. Reports print the label next to every result that uses it.

Probabilities come from each method's predictive distribution
(:meth:`~atlas.intervals.PredictiveDistribution.exceedance`), never from interval
endpoints. :func:`check_ordering` asserts, for every forecast, that probabilities lie in
[0, 1] and do not increase when the threshold increases.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

import numpy as np
import pandas as pd

from atlas.series import DemandSeries
from atlas.uncertainty_config import (
    HistoricalThresholdConfig,
    HypotheticalThresholdConfig,
    ThresholdConfig,
)

PROVENANCE_HISTORY: Final = "DERIVED_FROM_HISTORY"
PROVENANCE_HYPOTHETICAL: Final = "HYPOTHETICAL"
HYPOTHETICAL_NOTICE: Final = (
    "HYPOTHETICAL threshold: a user-supplied number, not a verified NYC agency service capacity."
)
HISTORICAL_NOTICE: Final = (
    "Historical demand percentile: a high-demand reference computed from past incoming "
    "requests, not a measured service capacity."
)


@dataclass(frozen=True)
class ThresholdValue:
    label: str
    kind: str
    provenance: str
    value: float | None  # None: not computable from the information available
    units: str = "requests_per_day"


def provenance(threshold: ThresholdConfig) -> str:
    if isinstance(threshold, HypotheticalThresholdConfig):
        return PROVENANCE_HYPOTHETICAL
    return PROVENANCE_HISTORY


def trailing_window(series: DemandSeries, cutoff: date, window_days: int) -> np.ndarray:
    """Observed daily counts in the ``window_days`` days ending at ``cutoff`` (NaN dropped).

    Reads nothing after ``cutoff``: the window is cut from ``series.known_through(cutoff)``.
    """
    known = series.known_through(cutoff)
    first = cutoff - timedelta(days=window_days - 1)
    values = known.loc[pd.Timestamp(first) :].to_numpy(dtype="float64")
    return values[np.isfinite(values)]


def historical_threshold(
    threshold: HistoricalThresholdConfig, series: DemandSeries, cutoff: date
) -> float | None:
    """The threshold at a forecast whose data cutoff is ``cutoff`` (``None`` if too few
    observations: the probability is then unavailable, not guessed)."""
    window = trailing_window(series, cutoff, threshold.window_days)
    if window.size < threshold.min_observations:
        return None
    return float(np.quantile(window, threshold.quantile, method="linear"))


def threshold_values(
    thresholds: tuple[ThresholdConfig, ...], series: DemandSeries, cutoff: date
) -> list[ThresholdValue]:
    values: list[ThresholdValue] = []
    for threshold in thresholds:
        if isinstance(threshold, HistoricalThresholdConfig):
            value = historical_threshold(threshold, series, cutoff)
        else:
            value = float(threshold.value)
        values.append(ThresholdValue(threshold.label, threshold.kind, provenance(threshold), value))
    return values


def check_ordering(thresholds: np.ndarray, probabilities: np.ndarray) -> None:
    """Probabilities in [0, 1], non-increasing in the threshold (NaN thresholds skipped)."""
    present = ~np.isnan(thresholds)
    limits, chances = thresholds[present], probabilities[present]
    if np.any(np.isnan(chances)) or np.any(chances < 0) or np.any(chances > 1):
        raise AssertionError(f"exceedance probabilities outside [0, 1]: {chances}")
    order = np.argsort(limits, kind="stable")
    if np.any(np.diff(chances[order]) > 1e-12):
        raise AssertionError(
            f"a higher threshold got a higher exceedance probability: {limits} -> {chances}"
        )
