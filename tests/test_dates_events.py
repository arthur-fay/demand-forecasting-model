"""ISO 8601 conventions, legacy weekday conversion, holidays and declared events."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from atlas.dates import (
    ISO_WEEKDAY_NAMES,
    WeekdayConvention,
    as_date,
    days_in_year,
    iso_week,
    iso_weekday,
    to_iso_weekday,
    year_fraction,
)
from atlas.events import (
    EventCalendar,
    KnownEvent,
    federal_holidays,
    holidays_between,
    observed_date,
)
from atlas.loader import LoadedTable


def test_iso_weekday_numbers_and_names() -> None:
    week = pd.date_range("2024-12-30", periods=7, freq="D")  # Monday .. Sunday
    assert list(iso_weekday(week)) == [1, 2, 3, 4, 5, 6, 7]
    assert [ISO_WEEKDAY_NAMES[n] for n in iso_weekday(week)] == list(week.day_name())


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        ("2020-12-31", (2020, 53, 4)),
        ("2021-01-03", (2020, 53, 7)),
        ("2021-01-04", (2021, 1, 1)),
        ("2024-12-30", (2025, 1, 1)),
        ("2026-12-31", (2026, 53, 4)),
        ("2027-01-03", (2026, 53, 7)),
        ("2024-02-29", (2024, 9, 4)),
    ],
)
def test_iso_week_at_year_boundaries(day: str, expected: tuple[int, int, int]) -> None:
    frame = iso_week(pd.DatetimeIndex([day]))
    assert tuple(frame.iloc[0]) == expected
    assert date.fromisoformat(day).isocalendar() == expected


def test_sqlite_weekday_conversion_covers_every_day() -> None:
    assert [to_iso_weekday(n, WeekdayConvention.SQLITE) for n in range(7)] == [7, 1, 2, 3, 4, 5, 6]
    assert [to_iso_weekday(n, WeekdayConvention.ISO) for n in range(1, 8)] == list(range(1, 8))
    for bad, convention in ((7, WeekdayConvention.SQLITE), (0, WeekdayConvention.ISO)):
        with pytest.raises(ValueError, match="weekday"):
            to_iso_weekday(bad, convention)


@pytest.mark.integration
def test_legacy_weekday_conventions_convert_to_iso(
    legacy_tables: dict[str, LoadedTable],
) -> None:
    """The legacy tables are read as-is; converting their numbering agrees with ISO names."""
    workload = legacy_tables["weekday_workload"].frame
    for number, name in zip(workload["weekday_number"], workload["weekday_name"], strict=True):
        assert ISO_WEEKDAY_NAMES[to_iso_weekday(int(number), WeekdayConvention.SQLITE)] == name
    dimension = legacy_tables["date_dimension"].frame
    dates = pd.DatetimeIndex(dimension["date"])
    assert list(dimension["weekday_number"]) == list(iso_weekday(dates))


def test_year_fraction_handles_leap_years() -> None:
    dates = pd.DatetimeIndex(["2024-01-01", "2024-12-31", "2025-12-31", "2024-02-29"])
    assert list(days_in_year(dates)) == [366, 366, 365, 366]
    fractions = list(year_fraction(dates))
    assert fractions[0] == 0.0
    assert fractions[1] == pytest.approx(365 / 366)
    assert fractions[2] == pytest.approx(364 / 365)
    assert fractions[3] == pytest.approx(59 / 366)
    assert max(fractions) < 1


def test_as_date_accepts_only_calendar_days() -> None:
    assert as_date("2024-02-29") == date(2024, 2, 29)
    assert as_date(pd.Timestamp("2024-02-29")) == date(2024, 2, 29)
    for bad in ("2024-2-29", "2024-02-29 00:00", pd.Timestamp("2024-02-29 01:00")):
        with pytest.raises(ValueError, match="date"):
            as_date(bad)


# Dates published by the U.S. Office of Personnel Management.
OPM_2026 = {
    date(2026, 1, 1): "New Year's Day",
    date(2026, 1, 19): "Birthday of Martin Luther King, Jr.",
    date(2026, 2, 16): "Washington's Birthday",
    date(2026, 5, 25): "Memorial Day",
    date(2026, 6, 19): "Juneteenth National Independence Day",
    date(2026, 7, 3): "Independence Day",  # July 4 is a Saturday
    date(2026, 9, 7): "Labor Day",
    date(2026, 10, 12): "Columbus Day",
    date(2026, 11, 11): "Veterans Day",
    date(2026, 11, 26): "Thanksgiving Day",
    date(2026, 12, 25): "Christmas Day",
}


def test_federal_holidays_match_published_2026_schedule() -> None:
    observed = {h.observed: h.name for h in federal_holidays(2026)}
    assert observed == OPM_2026


@pytest.mark.parametrize(
    ("actual", "observed"),
    [
        (date(2022, 1, 1), date(2021, 12, 31)),  # Saturday -> Friday, previous year
        (date(2022, 6, 19), date(2022, 6, 20)),  # Sunday -> Monday
        (date(2022, 12, 25), date(2022, 12, 26)),
        (date(2023, 11, 11), date(2023, 11, 10)),
        (date(2024, 7, 4), date(2024, 7, 4)),
    ],
)
def test_observance_rule(actual: date, observed: date) -> None:
    assert observed_date(actual) == observed


def test_floating_holidays() -> None:
    by_name = {h.name: h.actual for h in federal_holidays(2024)}
    assert by_name["Thanksgiving Day"] == date(2024, 11, 28)
    assert by_name["Memorial Day"] == date(2024, 5, 27)  # last Monday of May
    assert by_name["Labor Day"] == date(2024, 9, 2)
    assert by_name["Birthday of Martin Luther King, Jr."] == date(2024, 1, 15)
    assert {h.name for h in federal_holidays(2025)} == set(OPM_2026.values())
    assert len(federal_holidays(2020)) == 10  # before Juneteenth became a federal holiday
    assert federal_holidays(2023)[0].actual == date(2023, 1, 1)
    assert federal_holidays(2023)[0].observed == date(2023, 1, 2)


def test_holidays_between_includes_observed_dates_across_years() -> None:
    found = holidays_between(date(2021, 12, 31), date(2021, 12, 31))
    assert [(h.actual, h.observed) for h in found] == [(date(2022, 1, 1), date(2021, 12, 31))]
    assert holidays_between(date(2024, 1, 2), date(2024, 1, 1)) == ()
    with pytest.raises(ValueError, match=r"2000\.\.2100"):
        holidays_between(date(1999, 1, 1), date(2000, 1, 1))
    with pytest.raises(ValueError, match=r"2000\.\.2100"):
        federal_holidays(2101)


def test_declared_events_are_gated_by_announcement() -> None:
    event = KnownEvent("system migration", date(2026, 3, 1), date(2026, 3, 3), date(2026, 2, 1))
    calendar = EventCalendar((event,))
    days = [date(2026, 2, 28), date(2026, 3, 1), date(2026, 3, 3), date(2026, 3, 4)]
    assert calendar.declared_event_days(days, origin=date(2026, 1, 31)) == [False] * 4
    assert calendar.declared_event_days(days, origin=date(2026, 2, 1)) == [
        False,
        True,
        True,
        False,
    ]
    assert calendar.events_known_at(date(2026, 1, 31)) == ()


@pytest.mark.parametrize(
    "factory",
    [
        lambda: KnownEvent(" ", date(2026, 1, 1), date(2026, 1, 1), date(2025, 1, 1)),
        lambda: KnownEvent("x", date(2026, 1, 2), date(2026, 1, 1), date(2025, 1, 1)),
        lambda: EventCalendar(
            (
                KnownEvent("x", date(2026, 1, 1), date(2026, 1, 1), date(2025, 1, 1)),
                KnownEvent("x", date(2026, 2, 1), date(2026, 2, 1), date(2025, 1, 1)),
            )
        ),
    ],
)
def test_invalid_events(factory: object) -> None:
    with pytest.raises(ValueError, match="event"):
        factory()  # type: ignore[operator]


def test_juneteenth_starts_in_2021() -> None:
    by_name = {h.name: h for h in federal_holidays(2021)}
    juneteenth = by_name["Juneteenth National Independence Day"]
    assert (juneteenth.actual, juneteenth.observed) == (date(2021, 6, 19), date(2021, 6, 18))
    assert len(federal_holidays(2021)) == 11


def test_as_date_numpy_and_wrong_types() -> None:
    import numpy as np

    assert as_date(np.datetime64("2024-02-29")) == date(2024, 2, 29)
    for bad in (np.datetime64("2024-02-29T06:00"), np.datetime64("NaT", "D"), 20240229, 1.5):
        with pytest.raises(ValueError, match="date"):
            as_date(bad)  # type: ignore[arg-type]


def test_events_require_plain_dates() -> None:
    from datetime import datetime

    with pytest.raises(ValueError, match="announced_on"):
        KnownEvent("x", date(2026, 1, 1), date(2026, 1, 1), datetime(2025, 1, 1, 9))  # type: ignore[arg-type]
