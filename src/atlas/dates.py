"""Calendar conventions for data produced by ATLAS (milestone M1 onward).

Every table, flag and feature that ATLAS creates uses ISO 8601:

* weekdays are numbered Monday = 1 ... Sunday = 7 (``ISO_WEEKDAY_NAMES``);
* weeks are ISO weeks: they start on Monday, and week 1 is the week containing the
  year's first Thursday, so an ISO week belongs to an *ISO year* that can differ from
  the calendar year near January 1 (2024-12-30 is 2025-W01, 2026-12-31 is 2026-W53);
* dates are calendar days without a time of day or time zone, rendered ``YYYY-MM-DD``.

The ``legacy-v0`` tables keep their own conventions and are never rewritten. Two differ
from ISO and are converted explicitly, never silently:

* ``weekday_workload.weekday_number`` follows SQLite ``strftime('%w')`` (Sunday = 0);
* ``date_dimension.week_of_year`` is an ISO week paired with the *calendar* year.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum
from typing import Final

import numpy as np
import pandas as pd

from atlas.loader import DATE_PATTERN

ISO_WEEKDAY_NAMES: Final[dict[int, str]] = {
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
    7: "Sunday",
}
ISO_WEEKEND: Final[frozenset[int]] = frozenset({6, 7})


class WeekdayConvention(StrEnum):
    """Weekday numbering schemes found in the repository."""

    ISO = "iso"  # Monday = 1 ... Sunday = 7 (all ATLAS-produced data)
    SQLITE = "sqlite"  # Sunday = 0 ... Saturday = 6 (legacy weekday_workload)


def iso_weekday(dates: pd.DatetimeIndex) -> pd.Index:
    """ISO 8601 weekday numbers, Monday = 1 ... Sunday = 7."""
    return pd.Index(dates.dayofweek + 1, dtype="int64", name="iso_weekday")


def iso_week(dates: pd.DatetimeIndex) -> pd.DataFrame:
    """ISO year, ISO week and ISO weekday of each date, as ``int64`` columns."""
    calendar = dates.isocalendar()
    return pd.DataFrame(
        {
            "iso_year": calendar["year"].to_numpy(dtype="int64"),
            "iso_week": calendar["week"].to_numpy(dtype="int64"),
            "iso_weekday": calendar["day"].to_numpy(dtype="int64"),
        },
        index=dates,
    )


def to_iso_weekday(number: int, convention: WeekdayConvention) -> int:
    """Convert one weekday number from ``convention`` to ISO 8601."""
    if convention is WeekdayConvention.ISO:
        if not 1 <= number <= 7:
            raise ValueError(f"ISO weekday must be 1..7, got {number}")
        return number
    if not 0 <= number <= 6:
        raise ValueError(f"SQLite weekday must be 0..6, got {number}")
    return 7 if number == 0 else number


def days_in_year(dates: pd.DatetimeIndex) -> pd.Index:
    """366 for dates in leap years, otherwise 365."""
    leap = np.asarray(dates.is_leap_year, dtype="int64")
    result: pd.Index = pd.Index(365 + leap, dtype="int64")
    return result


def year_fraction(dates: pd.DatetimeIndex) -> pd.Index:
    """Position within the calendar year in ``[0, 1)``: ``(day_of_year - 1) / days_in_year``.

    January 1 is 0 and December 31 is ``1 - 1/days_in_year`` in every year, so the
    value wraps continuously at the year boundary in leap and common years alike (the
    legacy ``day_of_year / 365.25`` drifts by up to a day and exceeds 1 on 31 December of
    a leap year).
    """
    return pd.Index((dates.dayofyear - 1) / days_in_year(dates), dtype="float64")


def as_date(value: date | pd.Timestamp | str) -> date:
    """Normalise a date-like value to :class:`datetime.date`.

    Accepts a ``date``, an ISO ``YYYY-MM-DD`` string, or a time-zone-naive timestamp at
    exactly midnight. Anything carrying a time of day or a time zone is rejected rather
    than truncated, because truncation could move an event to a different day.
    """
    if isinstance(value, str):
        # date.fromisoformat also accepts "20240101" and "2024-W01-1"; require YYYY-MM-DD.
        if not re.fullmatch(DATE_PATTERN, value):
            raise ValueError(f"expected a YYYY-MM-DD date, got {value!r}")
        return date.fromisoformat(value)
    if isinstance(value, datetime):  # includes pd.Timestamp; checked before date
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is not None or stamp != stamp.normalize():
            raise ValueError(f"expected a calendar date, got timestamp {value!r}")
        return stamp.date()
    return value
