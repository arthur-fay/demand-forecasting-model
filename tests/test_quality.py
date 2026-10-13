"""Data-quality flags: planted anomalies with hand-checkable scores, causality, legacy results."""

from __future__ import annotations

import dataclasses
import random
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from atlas.contracts import QUALITY_FLAGS
from atlas.issues import Severity
from atlas.loader import LoadedTable, validate_frame
from atlas.quality import (
    DEFAULT_THRESHOLDS,
    TOTAL_SUBJECT,
    FlagCode,
    QualityFlag,
    QualityThresholds,
    assess_snapshot,
    citywide_anomalies,
    detect_flags,
    flag_severity,
    flags_frame,
    missing_periods,
    quality_report,
    same_weekday_scores,
    segment_interruptions,
    unassigned_share_flags,
)
from atlas.series import DemandSeries
from atlas.validation import KnownExceptions, load_exceptions
from tests.synthetic import day_range

START = date(2024, 2, 5)  # a Monday; no federal holiday until 2024-02-19
BASE_WEEK = [100.0, 102.0, 98.0, 100.0, 101.0, 99.0, 100.0]


def weekly_series(days: int, overrides: dict[int, float | None] | None = None) -> DemandSeries:
    """The same weekly pattern repeated: each weekday's history is constant."""
    values: list[float | None] = [BASE_WEEK[k % 7] for k in range(days)]
    for position, value in (overrides or {}).items():
        values[position] = value
    return DemandSeries.from_observations(
        day_range(START, days), values, allow_missing=True, integral=False
    )


# --- Same-weekday scores and city-wide flags ------------------------------------------


def test_score_has_the_documented_closed_form() -> None:
    """Eight identical predecessors: median 100, MAD 0, so the scale is 5 % of 100."""
    series = weekly_series(70, {56: 150.0, 63: 126.0})  # day 56 and 63 are Mondays
    scores = same_weekday_scores(series)
    assert np.isnan(scores.score.iloc[:56]).all()  # fewer than 8 same-weekday values
    assert scores.reference.iloc[56] == 100.0
    assert scores.scale.iloc[56] == 5.0
    assert scores.score.iloc[56] == pytest.approx(10.0)
    # Day 63's window includes the 150 surge: median of [150, 100 x 7] is still 100.
    assert scores.reference.iloc[63] == 100.0
    assert scores.score.iloc[63] == pytest.approx(5.2)


def test_scale_uses_mad_when_it_exceeds_the_floor() -> None:
    history = [100.0, 120.0, 80.0, 100.0, 140.0, 60.0, 100.0, 100.0]  # same weekday, oldest first
    values: list[float] = []
    for week in range(8):
        values.extend([history[week]] + [100.0] * 6)
    values.append(150.0)  # day 56 (Monday)
    series = DemandSeries.from_observations(day_range(START, 57), values, integral=False)
    scores = same_weekday_scores(series)
    # |deviations| = 0,20,20,0,40,40,0,0 -> MAD 10 -> scale 14.826 (> floor 5).
    assert scores.scale.iloc[56] == pytest.approx(14.826)
    assert scores.score.iloc[56] == pytest.approx(50 / 14.826)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (126.0, FlagCode.SURGE),  # score +5.2, +26 %
        (124.0, None),  # score +4.8: below the score threshold
        (74.0, FlagCode.LOW_VOLUME),  # score -5.2, -26 %
        (76.0, None),
    ],
)
def test_citywide_thresholds(value: float, code: FlagCode | None) -> None:
    flags = citywide_anomalies(weekly_series(60, {56: value}))
    assert [f.code for f in flags] == ([code] if code else [])
    if code:
        (flag,) = flags
        assert (flag.start, flag.end, flag.subject) == (
            START + timedelta(56),
            START + timedelta(56),
            TOTAL_SUBJECT,
        )
        assert flag.observed == value
        assert flag.expected == 100.0


def test_relative_change_threshold_also_applies() -> None:
    """A high score alone is not a surge: the change must also be >= 25 %."""
    thresholds = QualityThresholds(min_relative_scale=0.01)  # scale 1 -> score 20 for +20 %
    flags = citywide_anomalies(weekly_series(60, {56: 120.0}), thresholds)
    assert flags == []


def test_consecutive_surge_days_form_one_flag() -> None:
    series = weekly_series(70, {57: 200.0, 58: 210.0, 60: 205.0})
    flags = citywide_anomalies(series)
    assert [(f.start, f.days) for f in flags] == [
        (START + timedelta(57), 2),
        (START + timedelta(60), 1),
    ]
    assert flags[0].observed == 410.0
    assert flags[0].expected == 102.0 + 98.0
    assert flags[0].score == pytest.approx((210 - 98) / (0.05 * 98))


def test_missing_periods_are_grouped() -> None:
    series = weekly_series(30, {3: None, 4: None, 10: None})
    flags = missing_periods(series)
    assert [(f.start, f.end, f.days) for f in flags] == [
        (START + timedelta(3), START + timedelta(4), 2),
        (START + timedelta(10), START + timedelta(10), 1),
    ]
    assert all(f.code is FlagCode.MISSING_PERIOD for f in flags)


def test_gap_in_window_leaves_day_unscored() -> None:
    series = weekly_series(70, {7: None, 63: 500.0})  # day 63's 8-week window contains day 7
    scores = same_weekday_scores(series)
    assert np.isnan(scores.score.iloc[63])
    assert not any(f.code is FlagCode.SURGE for f in citywide_anomalies(series))


# --- Segment interruptions -----------------------------------------------------------


def segment(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.date_range(START, periods=len(values), freq="D"))


def test_interruption_and_catch_up() -> None:
    values = [200.0] * 28 + [0.0, 5.0, 20.0, 450.0, 200.0]
    flags = segment_interruptions(segment(values), "agency:HPD")
    interruption, catch_up = flags
    assert interruption.code is FlagCode.INTAKE_INTERRUPTION
    assert (interruption.start, interruption.end) == (START + timedelta(28), START + timedelta(30))
    assert interruption.observed == 25.0
    assert interruption.expected == 600.0
    assert catch_up.code is FlagCode.CATCH_UP_SURGE
    assert catch_up.start == START + timedelta(31)
    assert catch_up.observed == 450.0
    assert catch_up.expected == 200.0


def test_interruption_boundaries() -> None:
    """Reference = 10th percentile of the previous 28 days; a day must be <= 10 % of it."""
    assert segment_interruptions(segment([200.0] * 28 + [20.0]), "agency:X")  # exactly 10 %
    assert not segment_interruptions(segment([200.0] * 28 + [21.0]), "agency:X")
    # Reference below the 50-request minimum: a sparse segment is never flagged.
    assert not segment_interruptions(segment([49.0] * 28 + [0.0]), "agency:X")
    assert segment_interruptions(segment([50.0] * 28 + [0.0]), "agency:X")
    # Fewer than 28 days of history: not assessed.
    assert not segment_interruptions(segment([200.0] * 27 + [0.0]), "agency:X")
    # A catch-up must reach 1.5 x the median within three days.
    no_catch_up = [200.0] * 28 + [0.0, 299.0, 299.0, 299.0, 400.0]
    codes = [f.code for f in segment_interruptions(segment(no_catch_up), "agency:X")]
    assert codes == [FlagCode.INTAKE_INTERRUPTION]


def test_unknown_days_are_not_assessed() -> None:
    values = [200.0] * 28 + [0.0]
    values[5] = float("nan")
    assert not segment_interruptions(segment(values), "agency:X")


def test_detect_flags_on_segments_treats_absent_rows_as_zero() -> None:
    days = 40
    total = DemandSeries.from_observations(day_range(START, days), [1000] * days)
    rows = [
        (START + timedelta(k), "HPD", "QUEENS", 300)
        for k in range(days)
        if k not in (30, 31)  # HPD absent on two days -> zero requests
    ]
    segments = pd.DataFrame(rows, columns=["request_date", "agency", "borough", "request_count"])
    segments["request_date"] = pd.to_datetime(segments["request_date"])
    flags = detect_flags(total, segments)
    subjects = {(f.code, f.subject, f.start) for f in flags}
    assert (FlagCode.INTAKE_INTERRUPTION, "agency:HPD", START + timedelta(30)) in subjects
    assert (FlagCode.INTAKE_INTERRUPTION, "borough:QUEENS", START + timedelta(30)) in subjects


# --- Causality ----------------------------------------------------------------------


def flagged_days(flags: list[QualityFlag]) -> set[tuple[str, str, date]]:
    return {
        (f.code.value, f.subject, f.start + timedelta(days=k)) for f in flags for k in range(f.days)
    }


@pytest.mark.parametrize("seed", range(3))
def test_flags_are_prefix_consistent(seed: int) -> None:
    """For every cut-off k, flags computed from data up to k equal the full run's up to k."""
    rng = random.Random(seed)
    days = 150
    dates = day_range(date(2024, 1, 1), days)
    totals = [rng.choice([8000, 9000, 10000, 15000, 4000]) for _ in range(days)]
    hpd = [rng.choice([0, *[300] * 12, 900]) for _ in range(days)]

    def flags_until(k: int) -> set[tuple[str, str, date]]:
        series = DemandSeries.from_observations(dates[: k + 1], totals[: k + 1])
        segments = pd.DataFrame(
            {
                "request_date": pd.to_datetime(dates[: k + 1]),
                "agency": "HPD",
                "borough": "BRONX",
                "request_count": hpd[: k + 1],
            }
        )
        return flagged_days(detect_flags(series, segments))

    full = flags_until(days - 1)
    assert any(code == "intake_interruption" for code, _, _ in full)  # the test has teeth
    for k in range(56, days - 1):
        assert flags_until(k) == {f for f in full if f[2] <= dates[k]}, dates[k]


# --- Holidays, severity, reports and contracts ---------------------------------------


def test_holiday_low_volume_is_info_with_label() -> None:
    days = day_range(date(2023, 9, 1), 120)  # Thanksgiving 2023-11-23 has 8 weeks of history
    values = [100.0 if d != date(2023, 11, 23) else 50.0 for d in days]
    series = DemandSeries.from_observations(days, values, integral=False)
    (flag,) = detect_flags(series)
    assert flag.code is FlagCode.LOW_VOLUME
    assert flag.holiday == "Thanksgiving Day"
    assert flag_severity(flag) is Severity.INFO
    surge = dataclasses.replace(flag, code=FlagCode.SURGE)
    assert flag_severity(surge) is Severity.WARNING
    assert flag_severity(dataclasses.replace(flag, holiday=None)) is Severity.WARNING


def test_holiday_context_covers_eve_after_and_observed_day() -> None:
    days = day_range(date(2026, 5, 1), 80)  # July 4 2026 is a Saturday, observed July 3
    special = {date(2026, 7, 1), date(2026, 7, 3), date(2026, 7, 5)}  # not consecutive
    values = [40.0 if d in special else 100.0 for d in days]
    series = DemandSeries.from_observations(days, values, integral=False)
    labels = {f.start: f.holiday for f in detect_flags(series)}
    assert labels[date(2026, 7, 1)] is None
    assert labels[date(2026, 7, 3)] == "Independence Day (observed); eve of Independence Day"
    assert labels[date(2026, 7, 5)] == "day after Independence Day"


def test_flag_table_satisfies_contract_and_report_groups_flags() -> None:
    series = weekly_series(80, {3: None, 57: 200.0, 64: 30.0})
    flags = detect_flags(series)
    frame = flags_frame(flags)
    assert validate_frame(frame, QUALITY_FLAGS).ok
    assert list(frame["code"]) == ["missing_period", "surge", "low_volume"]
    report = quality_report(flags, {"extra": 1})
    assert report.ok
    assert report.facts["flag_counts"]["surge"] == 1
    assert report.facts["extra"] == 1
    assert [i.code for i in report.issues] == ["low_volume", "missing_period", "surge"]
    assert all(i.severity is Severity.WARNING for i in report.issues)
    assert validate_frame(flags_frame([]), QUALITY_FLAGS).ok


def test_threshold_validation() -> None:
    for bad in (
        {"same_weekday_weeks": 2},
        {"robust_score": 0},
        {"min_relative_change": 1.0},
        {"interruption_reference_quantile": 0.0},
        {"interruption_max_fraction": 1.0},
        {"catch_up_ratio": 1.0},
        {"catch_up_days": 0},
    ):
        with pytest.raises(ValueError, match=r"must|too short"):
            QualityThresholds(**bad)  # type: ignore[arg-type]
    assert DEFAULT_THRESHOLDS.robust_score == 5.0


def test_assess_snapshot_fails_when_residual_cannot_be_built() -> None:
    total = pd.DataFrame({"request_date": pd.to_datetime(["2024-01-01"]), "request_count": [10]})
    segments = pd.DataFrame(
        {
            "request_date": pd.to_datetime(["2024-01-01"]),
            "agency": ["NYPD"],
            "borough": ["BRONX"],
            "request_count": [7],
        }
    )
    report = assess_snapshot(total, segments, KnownExceptions(schema_version=1, snapshot_id="t"))
    assert not report.ok
    assert [i.code for i in report.errors] == ["unexplained_reconciliation_gap"]


# --- legacy-v0 ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def legacy_flags(legacy_tables: dict[str, LoadedTable]) -> list[QualityFlag]:
    total = DemandSeries.from_frame(legacy_tables["daily_total"].frame, name="daily_total")
    return detect_flags(total, legacy_tables["daily_segments"].frame)


def _find(flags: list[QualityFlag], code: FlagCode, subject: str, start: str) -> QualityFlag:
    matches = [
        f for f in flags if f.code is code and f.subject == subject and f.start.isoformat() == start
    ]
    assert len(matches) == 1, (code, subject, start)
    return matches[0]


@pytest.mark.integration
def test_legacy_hpd_intake_interruption_and_catch_up(legacy_flags: list[QualityFlag]) -> None:
    gap = _find(legacy_flags, FlagCode.INTAKE_INTERRUPTION, "agency:HPD", "2024-04-11")
    assert gap.end == date(2024, 4, 14)
    assert gap.observed == 0
    catch_up = _find(legacy_flags, FlagCode.CATCH_UP_SURGE, "agency:HPD", "2024-04-15")
    assert catch_up.observed == 7398
    citywide = _find(legacy_flags, FlagCode.SURGE, TOTAL_SUBJECT, "2024-04-15")
    assert citywide.observed == 15899
    assert citywide.explanation == "catch_up_surge of agency:HPD on 2024-04-15"


@pytest.mark.integration
def test_legacy_flag_inventory_is_pinned(legacy_flags: list[QualityFlag]) -> None:
    """A change in detection on the frozen snapshot must be a reviewed change."""
    counts = {code: sum(f.code is code for f in legacy_flags) for code in FlagCode}
    assert counts == {
        FlagCode.MISSING_PERIOD: 0,
        FlagCode.SURGE: 11,
        FlagCode.LOW_VOLUME: 4,
        FlagCode.INTAKE_INTERRUPTION: 8,
        FlagCode.CATCH_UP_SURGE: 4,
        FlagCode.UNASSIGNED_SHARE: 0,  # needs the residual: see test_legacy_quality_stage
    }
    edc_christmas = _find(legacy_flags, FlagCode.INTAKE_INTERRUPTION, "agency:EDC", "2023-12-25")
    assert edc_christmas.holiday == "Christmas Day"
    assert flag_severity(edc_christmas) is Severity.INFO
    lows = [f for f in legacy_flags if f.code is FlagCode.LOW_VOLUME]
    assert [(f.start.isoformat(), f.holiday) for f in lows] == [
        ("2023-11-23", "Thanksgiving Day"),
        ("2023-12-25", "Christmas Day"),
        ("2024-01-01", "New Year's Day"),
        ("2024-05-27", "Memorial Day"),
    ]
    largest = max((f for f in legacy_flags if f.code is FlagCode.SURGE), key=lambda f: f.score or 0)
    assert largest.start == date(2023, 9, 29)
    assert largest.observed == 17962
    interrupted = {f.subject for f in legacy_flags if f.code is FlagCode.INTAKE_INTERRUPTION}
    assert interrupted == {"agency:HPD", "agency:DHS", "agency:DOB", "agency:EDC"}
    assert not any(f.subject.startswith("borough:") for f in legacy_flags)
    assert validate_frame(flags_frame(legacy_flags), QUALITY_FLAGS).ok


@pytest.mark.integration
def test_legacy_quality_stage(legacy_tables: dict[str, LoadedTable], repo_settings: object) -> None:
    register = load_exceptions(repo_settings.exceptions_path, "legacy-v0")  # type: ignore[attr-defined]
    report = assess_snapshot(
        legacy_tables["daily_total"].frame, legacy_tables["daily_segments"].frame, register
    )
    assert report.ok
    residual = report.facts["residual"]
    assert residual["total"] == 13_279
    assert residual["max"] == 2_434
    assert residual["max_date"] == "2023-09-15"
    assert residual["dates_positive"] == 1273
    assert residual["share_of_total_pct"] == 0.1077
    assert residual["exception_id"] == "LEGACY-V0-EX-001"
    assert len(report.facts["flags"]) == 28
    (share,) = [f for f in report.facts["flags"] if f["code"] == "unassigned_share"]
    assert (share["start_date"], share["observed"]) == ("2023-09-15", 2434.0)
    day_total = legacy_tables["daily_total"].frame.set_index("request_date")["request_count"]
    expected_pct = 2434 * 100 / int(day_total[pd.Timestamp("2023-09-15")])
    assert f"{expected_pct:.2f}%" in share["explanation"]
    assert report.facts["flag_counts"]["unassigned_share"] == 1


def test_assess_snapshot_reports_residual_mismatch(
    legacy_tables: dict[str, LoadedTable], repo_settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defence in depth: the residual must agree with the M0 statistics it was built from."""
    import atlas.quality as quality
    from atlas.reconciliation import build_borough_components

    def tampered(*args: object) -> object:
        result = build_borough_components(*args)  # type: ignore[arg-type]
        frame = result.frame.copy()
        frame.loc[frame["component"] == "UNASSIGNED", "request_count"] += 1
        return dataclasses.replace(result, frame=frame)

    monkeypatch.setattr(quality, "build_borough_components", tampered)
    register = load_exceptions(repo_settings.exceptions_path, "legacy-v0")  # type: ignore[attr-defined]
    report = assess_snapshot(
        legacy_tables["daily_total"].frame, legacy_tables["daily_segments"].frame, register
    )
    assert [i.code for i in report.errors] == ["residual_mismatch"]


# --- Regressions from the independent review -----------------------------------------


def test_one_day_is_never_the_catch_up_of_two_interruptions() -> None:
    """[100]*28 + [0, 50, 0, 200]: day 31 is the catch-up of the second interruption only."""
    flags = segment_interruptions(
        segment([100.0] * 28 + [0.0, 50.0, 0.0, 200.0, 100.0]), "agency:X"
    )
    catch_ups = [f for f in flags if f.code is FlagCode.CATCH_UP_SURGE]
    assert [(f.start, f.explanation) for f in catch_ups] == [
        (
            START + timedelta(31),
            f"after intake_interruption {START + timedelta(30)}..{START + timedelta(30)}",
        )
    ]
    assert validate_frame(flags_frame(flags), QUALITY_FLAGS).ok


def test_catch_up_ratio_boundary() -> None:
    at_ratio = segment_interruptions(segment([200.0] * 28 + [0.0, 300.0]), "agency:X")
    assert [f.code for f in at_ratio] == [FlagCode.INTAKE_INTERRUPTION, FlagCode.CATCH_UP_SURGE]


def test_score_boundary_is_inclusive() -> None:
    (flag,) = citywide_anomalies(weekly_series(60, {56: 125.0}))  # score 5.0, ratio 1.25
    assert flag.code is FlagCode.SURGE
    assert flag.score == 5.0


def test_low_volume_reports_most_extreme_score() -> None:
    (flag,) = citywide_anomalies(weekly_series(60, {56: 74.0, 57: 60.0}))
    assert flag.days == 2
    assert flag.score == pytest.approx((60 - 102) / (0.05 * 102))


def test_unknown_total_days_do_not_create_segment_flags() -> None:
    days = 40
    values: list[float | None] = [1000.0] * days
    values[30] = None
    total = DemandSeries.from_observations(day_range(START, days), values, allow_missing=True)
    segments = pd.DataFrame(
        {
            "request_date": pd.to_datetime([START + timedelta(k) for k in range(days) if k != 30]),
            "agency": "HPD",
            "borough": "QUEENS",
            "request_count": 300,
        }
    )
    codes = [(f.code, f.subject) for f in detect_flags(total, segments)]
    assert codes == [(FlagCode.MISSING_PERIOD, TOTAL_SUBJECT)]


def test_run_extending_past_a_holiday_is_a_warning() -> None:
    days = day_range(date(2023, 11, 1), 60)
    total = DemandSeries.from_observations(days, [10000] * 60)

    def run(zero_days: set[date]) -> QualityFlag:
        counts = [0 if d in zero_days else 300 for d in days]
        segments = pd.DataFrame(
            {
                "request_date": pd.to_datetime(days),
                "agency": "HPD",
                "borough": "BRONX",
                "request_count": counts,
            }
        )
        flags = [f for f in detect_flags(total, segments) if f.subject == "agency:HPD"]
        assert len(flags) == 1
        return flags[0]

    long_run = run({date(2023, 12, k) for k in range(21, 25)})  # 21..24: only the 24th is an eve
    assert long_run.holiday == "eve of Christmas Day"
    assert flag_severity(long_run) is Severity.WARNING
    christmas = run({date(2023, 12, 25)})
    assert flag_severity(christmas) is Severity.INFO


def test_surge_links_every_catch_up_on_its_day() -> None:
    days = 70
    total_values = [10000.0] * days
    total_values[60] = 20000.0
    total = DemandSeries.from_observations(day_range(START, days), total_values)
    rows = []
    for k in range(days):
        for agency in ("HPD", "DOB"):
            count = 0 if k in (58, 59) else (900 if k == 60 else 300)
            rows.append(
                (START + timedelta(k), agency, "BRONX" if agency == "HPD" else "QUEENS", count)
            )
    segments = pd.DataFrame(rows, columns=["request_date", "agency", "borough", "request_count"])
    segments["request_date"] = pd.to_datetime(segments["request_date"])
    flags = detect_flags(total, segments)
    (surge,) = [f for f in flags if f.code is FlagCode.SURGE]
    day = START + timedelta(60)
    for subject in ("agency:DOB", "agency:HPD", "borough:BRONX", "borough:QUEENS"):
        assert f"catch_up_surge of {subject} on {day}" in (surge.explanation or "")


def test_only_surges_are_linked_to_catch_ups() -> None:
    days = 70
    total_values = [10000.0] * days
    total_values[60] = 5000.0  # low volume on the catch-up day
    total = DemandSeries.from_observations(day_range(START, days), total_values)
    counts = [0 if k in (58, 59) else (900 if k == 60 else 300) for k in range(days)]
    segments = pd.DataFrame(
        {
            "request_date": pd.to_datetime(day_range(START, days)),
            "agency": "HPD",
            "borough": "BRONX",
            "request_count": counts,
        }
    )
    (low,) = [f for f in detect_flags(total, segments) if f.code is FlagCode.LOW_VOLUME]
    assert low.explanation is None


def test_holiday_label_of_day_after_uses_the_previous_day() -> None:
    days = day_range(date(2023, 9, 1), 120)
    values = [100.0 if d != date(2023, 11, 24) else 50.0 for d in days]
    series = DemandSeries.from_observations(days, values, integral=False)
    (flag,) = detect_flags(series)
    assert flag.holiday == "day after Thanksgiving Day"


def test_unassigned_share_flags() -> None:
    index = pd.date_range(START, periods=4, freq="D")
    residual = pd.Series([2, 3, 0, 5], index=index)
    totals = pd.Series([100.0, 100.0, 0.0, 100.0], index=index)
    flags = unassigned_share_flags(residual, totals, 2.0)
    assert [(f.start, f.days, f.observed) for f in flags] == [
        (START + timedelta(1), 1, 3.0),  # 2 % is not above 2 %
        (START + timedelta(3), 1, 5.0),
    ]
    assert "5.00%" in (flags[1].explanation or "")


def test_flag_dates_are_ordered() -> None:
    with pytest.raises(ValueError, match="ends before it starts"):
        QualityFlag(date(2024, 1, 2), date(2024, 1, 1), FlagCode.SURGE, TOTAL_SUBJECT)


def test_assess_snapshot_detects_a_residual_max_mismatch(
    legacy_tables: dict[str, LoadedTable], repo_settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    import atlas.quality as quality
    from atlas.reconciliation import build_borough_components

    def moved(*args: object) -> object:
        result = build_borough_components(*args)  # type: ignore[arg-type]
        frame = result.frame.copy()
        residual_rows = frame["component"] == "UNASSIGNED"
        largest = frame[residual_rows]["request_count"].idxmax()
        other = frame[residual_rows].index[0]
        frame.loc[largest, "request_count"] -= 1  # same total, smaller maximum
        frame.loc[other, "request_count"] += 1
        return dataclasses.replace(result, frame=frame)

    monkeypatch.setattr(quality, "build_borough_components", moved)
    register = load_exceptions(repo_settings.exceptions_path, "legacy-v0")  # type: ignore[attr-defined]
    report = assess_snapshot(
        legacy_tables["daily_total"].frame, legacy_tables["daily_segments"].frame, register
    )
    assert [i.code for i in report.errors] == ["residual_mismatch"]


def test_assess_snapshot_without_any_residual() -> None:
    total = pd.DataFrame({"request_date": pd.to_datetime(["2024-01-01"]), "request_count": [7]})
    segments = pd.DataFrame(
        {
            "request_date": pd.to_datetime(["2024-01-01"]),
            "agency": ["NYPD"],
            "borough": ["BRONX"],
            "request_count": [7],
        }
    )
    report = assess_snapshot(total, segments, KnownExceptions(schema_version=1, snapshot_id="t"))
    assert report.ok
    assert report.facts["residual"]["max_date"] is None
    assert report.facts["residual"]["exception_id"] is None
