"""Synthetic series and an independent reference implementation of the M1 features.

The reference implementation deliberately shares no code with ``atlas.features``: it
uses plain Python dates, dictionaries and the ``statistics`` module, one target at a
time, so a bug in the vectorised implementation cannot also be present here.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Mapping
from datetime import date, timedelta

from atlas.series import DemandSeries


def day_range(start: date, days: int) -> list[date]:
    return [start + timedelta(days=offset) for offset in range(days)]


def linear_series(
    start: date, days: int, intercept: int = 1000, *, name: str = "y"
) -> DemandSeries:
    """``y(start + k) = intercept + k``: every lag and window has a closed form."""
    dates = day_range(start, days)
    return DemandSeries.from_observations(dates, [intercept + k for k in range(days)], name=name)


def random_series(start: date, days: int, seed: int, *, name: str = "y") -> DemandSeries:
    """Integer demand with weekly seasonality and noise, from a seeded generator."""
    rng = random.Random(seed)
    dates = day_range(start, days)
    values = [
        max(0, round(9000 + 1500 * math.sin(2 * math.pi * d.isoweekday() / 7) + rng.gauss(0, 700)))
        for d in dates
    ]
    return DemandSeries.from_observations(dates, values, name=name)


def values_by_date(series: DemandSeries) -> dict[date, float]:
    return {ts.date(): float(v) for ts, v in series.to_series().items()}


# --- Reference implementation (one row at a time, pure Python) ----------------------


def _window(known: Mapping[date, float], target: date, offsets: range | list[int]) -> list[float]:
    values = []
    for offset in offsets:
        day = target - timedelta(days=offset)
        value = known.get(day, math.nan)
        values.append(value)
    return values


def reference_row(known: Mapping[date, float], target: date) -> dict[str, float]:
    """The default-feature-set demand features of ``target`` from ``known`` values only."""

    def mean(values: list[float]) -> float:
        return math.nan if any(math.isnan(v) for v in values) else statistics.fmean(values)

    def stdev(values: list[float]) -> float:
        return math.nan if any(math.isnan(v) for v in values) else statistics.stdev(values)

    row = {f"lag_{k}": _window(known, target, [k])[0] for k in (1, 7, 14, 28, 364)}
    row["rolling_mean_7"] = mean(_window(known, target, range(1, 8)))
    row["rolling_mean_28"] = mean(_window(known, target, range(1, 29)))
    row["same_weekday_mean_4w"] = mean(_window(known, target, [7, 14, 21, 28]))
    row["rolling_std_7"] = stdev(_window(known, target, range(1, 8)))
    row["rolling_std_28"] = stdev(_window(known, target, range(1, 29)))
    return row


def reference_calendar(target: date) -> dict[str, float]:
    _, iso_week, iso_weekday = target.isocalendar()
    days_in_year = 366 if (date(target.year, 12, 31) - date(target.year, 1, 1)).days == 365 else 365
    fraction = (target.timetuple().tm_yday - 1) / days_in_year
    next_day = target + timedelta(days=1)
    return {
        "iso_weekday": float(iso_weekday),
        "is_weekend": float(iso_weekday >= 6),
        **{f"is_iso_weekday_{k}": float(iso_weekday == k) for k in range(2, 8)},
        "month": float(target.month),
        "day_of_month": float(target.day),
        "day_of_year": float(target.timetuple().tm_yday),
        "iso_week": float(iso_week),
        "is_month_start": float(target.day == 1),
        "is_month_end": float(next_day.month != target.month),
        "year_sin_1": math.sin(2 * math.pi * fraction),
        "year_cos_1": math.cos(2 * math.pi * fraction),
        "year_sin_2": math.sin(4 * math.pi * fraction),
        "year_cos_2": math.cos(4 * math.pi * fraction),
        "trend_days": float((target - date(2023, 1, 1)).days),
    }
