"""Calibration data for prediction intervals: residual extraction and eligibility (M3).

The residuals are the out-of-sample errors of the M2 engine's own recursive forecasts,
``r = actual - prediction`` (note the sign: ``y = prediction + r``; M2's metrics use
``prediction - actual``), one per model, origin and horizon whose actual was observed.

**Eligibility.** An interval issued at origin ``o`` may use a residual only if its target
date was observed by then:

    ``residual.target_date <= c - data_latency``, with calibration origin ``c <= o``.

:class:`ResidualStore` is the only way M3 code reaches residuals, and every
:class:`CalibrationSet` it returns is built from a slice that ends at that cutoff; a final
guard raises :class:`~atlas.errors.LeakageError` if a later target ever slipped in. A
residual's prediction was made at its own origin, before its target date, so the whole
residual is known at the cutoff. Nothing calibrates an earlier forecast with a later
observation.

**Dependence.** Neighbouring origins' forecast paths overlap, so residuals are not
independent. Two things follow:

* sample sizes are counted in *distinct forecast origins* (paths). When horizon ``h`` has
  too few and its predeclared bucket is pooled, the horizons of one path still count once;
* each set carries the HAC effective sample size of its residual series (Bartlett,
  bandwidth ``2h``; for a pooled bucket, per-origin means and twice the bucket's last
  horizon). Below the configured minimum an
  interval is still issued but labelled ``thin`` (exploratory).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Final

import numpy as np
import pandas as pd

from atlas.errors import ConfigError, LeakageError
from atlas.metrics import HorizonBucket, effective_sample_size

OK_STATUS: Final = "ok"


class Support(StrEnum):
    SUPPORTED = "supported"  # enough distinct origins and enough effective sample
    THIN = "thin"  # issued, but the effective sample is below the minimum: exploratory
    UNAVAILABLE = "unavailable"  # too few eligible residuals even after pooling: no estimate


class Pooling(StrEnum):
    HORIZON = "horizon"
    BUCKET = "bucket"
    NONE = "none"


@dataclass(frozen=True)
class CalibrationPolicy:
    """Sample-size rules shared by every calibration set of an experiment."""

    data_latency_days: int
    min_calibration_origins: int
    min_effective_calibration: float
    pool_buckets: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.data_latency_days, bool) or self.data_latency_days < 0:
            raise ConfigError("data_latency_days must be a non-negative integer")
        if self.min_calibration_origins < 1:
            raise ConfigError("min_calibration_origins must be at least 1")
        if self.min_effective_calibration < 0:
            raise ConfigError("min_effective_calibration must be non-negative")


@dataclass(frozen=True)
class CalibrationSet:
    """The eligible residuals behind one interval: model, horizon and issue origin.

    ``cutoff`` is the last admissible target date (``calibration_origin - latency``);
    ``first_target`` the first one (``None``: no window). ``origins`` and ``targets`` are
    proleptic ordinals (:meth:`datetime.date.toordinal`) of the residuals used.
    """

    model_id: str
    horizon: int
    issue_origin: date
    calibration_origin: date
    cutoff: date
    first_target: date | None
    pooling: Pooling
    pooled_label: str | None
    residuals: np.ndarray
    predictions: np.ndarray
    origins: np.ndarray
    targets: np.ndarray
    n_origins: int
    n_effective: float | None
    support: Support

    @property
    def n(self) -> int:
        return int(self.residuals.size)

    @property
    def last_target(self) -> date | None:
        return date.fromordinal(int(self.targets.max())) if self.targets.size else None

    @property
    def first_used_target(self) -> date | None:
        return date.fromordinal(int(self.targets.min())) if self.targets.size else None


@dataclass(frozen=True)
class _HorizonResiduals:
    targets: np.ndarray  # sorted ascending
    origins: np.ndarray
    residuals: np.ndarray
    predictions: np.ndarray


class ResidualStore:
    """Residuals of one model's backtest forecasts, by horizon and target date.

    Built from M2 backtest records (``status == "ok"``: a prediction and an observed
    actual). Calibration code receives only :class:`CalibrationSet` objects from
    :meth:`calibration_set`, never the store's arrays.
    """

    def __init__(self, records: pd.DataFrame, model_id: str, max_horizon: int) -> None:
        if isinstance(max_horizon, bool) or max_horizon < 1:
            raise ConfigError("max_horizon must be a positive integer")
        rows = records[(records["model_id"] == model_id) & (records["status"] == OK_STATUS)]
        origins = _ordinals(rows["origin"])
        targets = _ordinals(rows["target_date"])
        horizons = rows["horizon"].to_numpy(dtype="int64")
        if (targets - origins != horizons).any():
            raise ConfigError(f"{model_id}: records with target_date != origin + horizon")
        prediction = rows["prediction"].to_numpy(dtype="float64")
        actual = rows["actual"].to_numpy(dtype="float64")
        if not (np.isfinite(prediction).all() and np.isfinite(actual).all()):
            raise ConfigError(f"{model_id}: ok records need finite predictions and actuals")
        self.model_id = model_id
        self.max_horizon = max_horizon
        self._by_horizon: dict[int, _HorizonResiduals] = {}
        for horizon in range(1, max_horizon + 1):
            chosen = horizons == horizon
            order = np.argsort(targets[chosen], kind="stable")
            self._by_horizon[horizon] = _HorizonResiduals(
                targets[chosen][order],
                origins[chosen][order],
                (actual[chosen] - prediction[chosen])[order],
                prediction[chosen][order],
            )

    @classmethod
    def from_records(
        cls, records: pd.DataFrame, model_ids: Sequence[str], max_horizon: int
    ) -> dict[str, ResidualStore]:
        return {model_id: cls(records, model_id, max_horizon) for model_id in model_ids}

    def n_residuals(self) -> int:
        return sum(int(part.targets.size) for part in self._by_horizon.values())

    def _slice(self, horizon: int, first: int | None, cutoff: int) -> _HorizonResiduals:
        part = self._by_horizon[horizon]
        high = int(np.searchsorted(part.targets, cutoff, side="right"))
        low = 0 if first is None else int(np.searchsorted(part.targets, first, side="left"))
        low = min(low, high)
        return _HorizonResiduals(
            part.targets[low:high],
            part.origins[low:high],
            part.residuals[low:high],
            part.predictions[low:high],
        )

    def calibration_set(
        self,
        *,
        horizon: int,
        issue_origin: date,
        calibration_origin: date,
        window_days: int | None,
        policy: CalibrationPolicy,
        buckets: Sequence[HorizonBucket],
    ) -> CalibrationSet:
        """The residuals that may calibrate horizon ``horizon`` of a forecast issued at
        ``issue_origin``, calibrated at ``calibration_origin`` (see the module docstring)."""
        if not 1 <= horizon <= self.max_horizon:
            raise ConfigError(f"horizon {horizon} is outside 1..{self.max_horizon}")
        if calibration_origin > issue_origin:
            raise LeakageError(
                f"calibration origin {calibration_origin} is after the issue origin "
                f"{issue_origin}: an earlier forecast would be calibrated with later data"
            )
        cutoff = calibration_origin - timedelta(days=policy.data_latency_days)
        first = None if window_days is None else cutoff - timedelta(days=window_days - 1)
        cutoff_ord = cutoff.toordinal()
        first_ord = None if first is None else first.toordinal()
        own = self._slice(horizon, first_ord, cutoff_ord)
        # One residual per origin at a single horizon, so its size counts distinct origins.
        pooling, label, chosen = Pooling.HORIZON, None, own
        overlap, n_origins = horizon, int(own.origins.size)
        if n_origins < policy.min_calibration_origins:
            pooling, chosen, n_origins = Pooling.NONE, _empty(), 0
            bucket = next((b for b in buckets if b.contains(horizon)), None)
            if policy.pool_buckets and bucket is not None and bucket.first != bucket.last:
                parts = [self._slice(h, first_ord, cutoff_ord) for h in bucket_range(bucket)]
                pooled = _concat(parts)
                distinct = int(np.unique(pooled.origins).size)
                if distinct >= policy.min_calibration_origins:
                    pooling, label, chosen, overlap, n_origins = (
                        Pooling.BUCKET,
                        bucket.label,
                        pooled,
                        bucket.last,
                        distinct,
                    )
        if chosen.targets.size and int(chosen.targets.max()) > cutoff_ord:
            raise LeakageError(
                f"{self.model_id}: a calibration residual targets "
                f"{date.fromordinal(int(chosen.targets.max()))}, after the cutoff {cutoff}"
            )
        n_effective = _effective_size(chosen, overlap) if pooling is not Pooling.NONE else None
        if pooling is Pooling.NONE:
            support = Support.UNAVAILABLE
        elif n_effective is None or n_effective < policy.min_effective_calibration:
            support = Support.THIN
        else:
            support = Support.SUPPORTED
        return CalibrationSet(
            model_id=self.model_id,
            horizon=horizon,
            issue_origin=issue_origin,
            calibration_origin=calibration_origin,
            cutoff=cutoff,
            first_target=first,
            pooling=pooling,
            pooled_label=label,
            residuals=chosen.residuals,
            predictions=chosen.predictions,
            origins=chosen.origins,
            targets=chosen.targets,
            n_origins=n_origins,
            n_effective=n_effective,
            support=support,
        )


def bucket_range(bucket: HorizonBucket) -> range:
    return range(bucket.first, bucket.last + 1)


def _ordinals(values: pd.Series) -> np.ndarray:
    stamps = pd.DatetimeIndex(values)
    return np.fromiter((d.toordinal() for d in stamps.date), dtype="int64", count=len(stamps))


def _empty() -> _HorizonResiduals:
    empty_int = np.empty(0, dtype="int64")
    empty_float = np.empty(0, dtype="float64")
    return _HorizonResiduals(empty_int, empty_int, empty_float, empty_float)


def _concat(parts: Sequence[_HorizonResiduals]) -> _HorizonResiduals:
    targets = np.concatenate([p.targets for p in parts])
    origins = np.concatenate([p.origins for p in parts])
    order = np.lexsort((origins, targets))
    return _HorizonResiduals(
        targets[order],
        origins[order],
        np.concatenate([p.residuals for p in parts])[order],
        np.concatenate([p.predictions for p in parts])[order],
    )


def _effective_size(part: _HorizonResiduals, overlap: int) -> float | None:
    """HAC effective sample size of the residual series in origin order.

    For a single horizon there is one residual per origin. For a pooled bucket the
    residuals of each origin (one path) are averaged first, so a path counts once.
    """
    if part.origins.size < 2:
        return None
    order = np.argsort(part.origins, kind="stable")
    origins = part.origins[order]
    residuals = part.residuals[order]
    unique, starts = np.unique(origins, return_index=True)
    if unique.size != origins.size:
        sums = np.add.reduceat(residuals, starts)
        counts = np.diff(np.append(starts, origins.size))
        residuals = sums / counts
    return effective_sample_size(residuals, overlap)
