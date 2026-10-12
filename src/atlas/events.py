"""Calendar events that are known before the days they affect.

Two sources, both usable as forecast features without look-ahead:

* **US federal holidays** (5 U.S.C. 6103), computed by rule for any year in
  ``SUPPORTED_YEARS``. A rule-based holiday is treated as known arbitrarily far in
  advance. (Juneteenth was enacted on 2021-06-17 and is applied from 2021; for data
  starting in 2023 the difference is immaterial.)
  Observance follows the federal rule: a holiday on a Saturday is observed on the
  preceding Friday, one on a Sunday on the following Monday (so New Year's Day 2022,
  a Saturday, was observed on Friday 2021-12-31). Juneteenth is a federal holiday
  from 2021. No external calendar or network access is used.
* **Declared events** (:class:`KnownEvent`): one-off events such as a scheduled system
  migration, each with the date on which it was *announced*. A feature computed for a
  forecast issued at origin ``o`` may use an event only if ``announced_on <= o``.
  Data-quality flags (``atlas.quality``) are detected after the fact and are therefore
  never events.

New York City and New York State observances that are not federal holidays (for
example Lincoln's Birthday or Election Day) are not included in M1.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

SUPPORTED_YEARS: Final = range(2000, 2101)
JUNETEENTH_FIRST_YEAR: Final = 2021

# ISO weekday numbers (Monday = 1), see atlas.dates.
_MONDAY, _THURSDAY, _SATURDAY, _SUNDAY = 1, 4, 6, 7


@dataclass(frozen=True, slots=True, order=True)
class Holiday:
    """One federal holiday: the calendar date and the weekday on which it is observed."""

    actual: date
    observed: date
    name: str


@dataclass(frozen=True, slots=True)
class KnownEvent:
    """A declared one-off event affecting ``start`` .. ``end`` (inclusive).

    ``announced_on`` is the first date on which the event was known; a forecast issued
    before that date must not see it.
    """

    name: str
    start: date
    end: date
    announced_on: date

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("event name must not be blank")
        for label in ("start", "end", "announced_on"):
            value = getattr(self, label)
            if isinstance(value, datetime) or not isinstance(value, date):
                raise ValueError(f"event {label} must be a datetime.date, got {value!r}")
        if self.end < self.start:
            raise ValueError(f"event {self.name!r} ends before it starts")

    def active_on(self, day: date) -> bool:
        return self.start <= day <= self.end

    def known_at(self, origin: date) -> bool:
        return self.announced_on <= origin


def _nth_weekday(year: int, month: int, iso_weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (iso_weekday - first.isoweekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, iso_weekday: int) -> date:
    next_month = date(year + month // 12, month % 12 + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.isoweekday() - iso_weekday) % 7)


def observed_date(actual: date) -> date:
    """Federal observance: Saturday -> preceding Friday, Sunday -> following Monday."""
    if actual.isoweekday() == _SATURDAY:
        return actual - timedelta(days=1)
    if actual.isoweekday() == _SUNDAY:
        return actual + timedelta(days=1)
    return actual


@functools.cache
def federal_holidays(year: int) -> tuple[Holiday, ...]:
    """The federal holidays whose *actual* date falls in ``year``, in date order."""
    if year not in SUPPORTED_YEARS:
        raise ValueError(
            f"federal holiday rules are defined for {SUPPORTED_YEARS.start}.."
            f"{SUPPORTED_YEARS.stop - 1}, not {year}"
        )
    fixed = [
        ("New Year's Day", date(year, 1, 1)),
        ("Independence Day", date(year, 7, 4)),
        ("Veterans Day", date(year, 11, 11)),
        ("Christmas Day", date(year, 12, 25)),
    ]
    if year >= JUNETEENTH_FIRST_YEAR:
        fixed.append(("Juneteenth National Independence Day", date(year, 6, 19)))
    floating = [
        ("Birthday of Martin Luther King, Jr.", _nth_weekday(year, 1, _MONDAY, 3)),
        ("Washington's Birthday", _nth_weekday(year, 2, _MONDAY, 3)),
        ("Memorial Day", _last_weekday(year, 5, _MONDAY)),
        ("Labor Day", _nth_weekday(year, 9, _MONDAY, 1)),
        ("Columbus Day", _nth_weekday(year, 10, _MONDAY, 2)),
        ("Thanksgiving Day", _nth_weekday(year, 11, _THURSDAY, 4)),
    ]
    holidays = [Holiday(day, observed_date(day), name) for name, day in (*fixed, *floating)]
    return tuple(sorted(holidays))


def holidays_between(start: date, end: date) -> tuple[Holiday, ...]:
    """Holidays whose actual *or* observed date lies within ``start`` .. ``end``."""
    if end < start:
        return ()
    first, last = SUPPORTED_YEARS.start, SUPPORTED_YEARS.stop - 1
    if start.year < first or end.year > last:
        raise ValueError(f"federal holiday rules are defined for {first}..{last} only")
    # Observance moves a date by at most one day, possibly across a year boundary.
    years = range(max(start.year - 1, first), min(end.year + 1, last) + 1)
    found = [
        holiday
        for year in years
        for holiday in federal_holidays(year)
        if start <= holiday.actual <= end or start <= holiday.observed <= end
    ]
    return tuple(sorted(found))


@dataclass(frozen=True)
class EventCalendar:
    """Federal holidays plus declared events; the single source of event features."""

    declared: tuple[KnownEvent, ...] = ()

    def __post_init__(self) -> None:
        names = [event.name for event in self.declared]
        if len(names) != len(set(names)):
            raise ValueError("declared event names must be unique")

    def holiday_actual_dates(self, start: date, end: date) -> frozenset[date]:
        return frozenset(h.actual for h in holidays_between(start, end))

    def holiday_observed_dates(self, start: date, end: date) -> frozenset[date]:
        return frozenset(h.observed for h in holidays_between(start, end))

    def events_known_at(self, origin: date) -> tuple[KnownEvent, ...]:
        return tuple(event for event in self.declared if event.known_at(origin))

    def declared_event_days(self, days: Iterable[date], origin: date) -> list[bool]:
        """For each day, whether an event announced on or before ``origin`` covers it."""
        known: Sequence[KnownEvent] = self.events_known_at(origin)
        return [any(event.active_on(day) for event in known) for day in days]


DEFAULT_EVENT_CALENDAR = EventCalendar()
