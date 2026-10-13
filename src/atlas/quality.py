"""Data-quality flags: missing periods, unusual surges and potential intake interruptions.

Flags *annotate* periods; they never edit, fill or drop data. Every detector is
**causal**: whether a day is flagged depends only on observations dated on or before
that day, so the flags can later be used in a rolling-origin evaluation without
look-ahead. (The *end date* of a multi-day flag naturally depends on when the
condition stops, so causality holds for the set of flagged days, not for interval
boundaries.) Flags describe what was measured; whether a flagged day is an error in the
data or a real event (a storm, a heat wave, a holiday) is a judgement they support but
do not make.

Detectors (thresholds in :class:`QualityThresholds`, documented in docs/DATA_QUALITY.md):

``missing_period``
    A run of consecutive dates with no observation in a :class:`DemandSeries`.
``surge`` / ``low_volume`` (city-wide total)
    Robust same-weekday score. For day *d* the reference is the median of the eight
    preceding same-weekday values (*d*-7 ... *d*-56); the scale is
    ``max(1.4826 * MAD, 5 % of the median)``. A day is a ``surge`` when its score is at
    least +5 and it is at least 25 % above the reference, ``low_volume`` (a potential
    city-wide intake interruption) when its score is at most -5 and it is at least 25 %
    below. Days without eight complete same-weekday predecessors are not scored.
``intake_interruption`` (each agency and each borough)
    The volume of a segment that is normally never near zero collapses: the run starts
    on a day whose count is at most 10 % of the 10th percentile of the preceding 28
    days, provided that percentile is at least 50 requests, and continues while each
    following day stays at or below 10 % of the *same* starting reference.
``catch_up_surge``
    Within three days after an interruption ends (and before the next one starts), the
    first day on which the segment reaches at least 1.5 times its median over the 28
    days before the interruption: a backlog being entered late, so the interruption was
    in intake, not in demand.
``unassigned_share`` (the UNASSIGNED residual)
    Days on which the residual exceeds the share declared for the reconciliation
    exception (2 % of the day's total for legacy-v0).

Flags touching a federal holiday, its observed day, its eve or the day after carry the
holiday in ``holiday``. A low-volume or interruption flag whose *every* day is
holiday-related is reported as ``info`` (expected) rather than ``warning``. A city-wide
surge on the day of a segment's catch-up surge is linked to it in ``explanation``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd

from atlas.contracts import QUALITY_FLAGS, UNASSIGNED_COMPONENT
from atlas.errors import DataQualityError
from atlas.events import holidays_between
from atlas.issues import CheckReport, Issue, Severity, examples
from atlas.loader import validate_frame
from atlas.reconciliation import build_borough_components
from atlas.series import DemandSeries
from atlas.validation import DEFAULT_SHARE_THRESHOLD_PCT, KnownExceptions


class FlagCode(StrEnum):
    MISSING_PERIOD = "missing_period"
    SURGE = "surge"
    LOW_VOLUME = "low_volume"
    INTAKE_INTERRUPTION = "intake_interruption"
    CATCH_UP_SURGE = "catch_up_surge"
    UNASSIGNED_SHARE = "unassigned_share"


_DESCRIPTIONS: dict[FlagCode, str] = {
    FlagCode.MISSING_PERIOD: "Dates with no observation (kept as gaps, never filled).",
    FlagCode.SURGE: "City-wide demand far above the same-weekday reference.",
    FlagCode.LOW_VOLUME: (
        "City-wide demand far below the same-weekday reference (potential intake "
        "interruption, or a holiday)."
    ),
    FlagCode.INTAKE_INTERRUPTION: "A segment's intake collapsed to near zero.",
    FlagCode.CATCH_UP_SURGE: "A segment's volume spiked right after an interruption (backlog).",
    FlagCode.UNASSIGNED_SHARE: (
        "An unusually large share of the day's requests is outside the five named boroughs."
    ),
}


@dataclass(frozen=True)
class QualityThresholds:
    """Detector parameters. Defaults are fixed a priori and documented; see module doc."""

    same_weekday_weeks: int = 8
    robust_score: float = 5.0
    min_relative_change: float = 0.25
    min_relative_scale: float = 0.05
    interruption_window_days: int = 28
    interruption_reference_quantile: float = 0.10
    interruption_min_reference: float = 50.0
    interruption_max_fraction: float = 0.10
    catch_up_days: int = 3
    catch_up_ratio: float = 1.5

    def __post_init__(self) -> None:
        if self.same_weekday_weeks < 3 or self.interruption_window_days < 7:
            raise ValueError("reference windows are too short to be robust")
        if self.robust_score <= 0 or not 0 < self.min_relative_change < 1:
            raise ValueError("score and relative-change thresholds must be positive")
        if not 0 < self.interruption_reference_quantile < 1:
            raise ValueError("interruption_reference_quantile must be within (0, 1)")
        if not 0 < self.interruption_max_fraction < 1 or self.catch_up_ratio <= 1:
            raise ValueError("interruption fraction must be in (0, 1), catch-up ratio > 1")
        if self.catch_up_days < 1 or self.interruption_min_reference <= 0:
            raise ValueError("catch_up_days and interruption_min_reference must be positive")


DEFAULT_THRESHOLDS = QualityThresholds()


@dataclass(frozen=True, slots=True, order=True)
class QualityFlag:
    """One flagged period of one subject (``total:CITYWIDE``, ``agency:HPD``, ...)."""

    start: date
    end: date
    code: FlagCode
    subject: str
    observed: float | None = None  # sum over the period
    expected: float | None = None  # reference level summed over the period
    score: float | None = None  # most extreme robust score in the period
    explanation: str | None = None  # links to related flags (catch-up, interruption)
    holiday: str | None = None  # federal holiday context of the days in the period
    holiday_days: int = 0  # how many days of the period have holiday context

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"flag ends before it starts: {self.start}..{self.end}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def to_record(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "subject": self.subject,
            "start_date": pd.Timestamp(self.start),
            "end_date": pd.Timestamp(self.end),
            "days": self.days,
            "observed": self.observed,
            "expected": self.expected,
            "score": None if self.score is None else round(self.score, 4),
            "explanation": self.explanation,
            "holiday": self.holiday,
        }


TOTAL_SUBJECT = "total:CITYWIDE"
UNASSIGNED_SUBJECT = "total:UNASSIGNED"


# --- Detectors ----------------------------------------------------------------------


def _runs(days: Iterable[date]) -> list[tuple[date, date]]:
    """Group dates into maximal runs of consecutive days."""
    runs: list[tuple[date, date]] = []
    for day in sorted(days):
        if runs and day == runs[-1][1] + timedelta(days=1):
            runs[-1] = (runs[-1][0], day)
        else:
            runs.append((day, day))
    return runs


def missing_periods(series: DemandSeries, subject: str = TOTAL_SUBJECT) -> list[QualityFlag]:
    return [
        QualityFlag(start, end, FlagCode.MISSING_PERIOD, subject)
        for start, end in _runs(series.missing_dates)
    ]


@dataclass(frozen=True)
class SameWeekdayScores:
    """Per-day reference, scale and robust score (``NaN`` where not scored)."""

    observed: pd.Series
    reference: pd.Series
    scale: pd.Series
    score: pd.Series


def same_weekday_scores(
    series: DemandSeries, thresholds: QualityThresholds = DEFAULT_THRESHOLDS
) -> SameWeekdayScores:
    """Robust score of each day against its preceding same-weekday values (causal)."""
    values = series.to_series()
    weeks = thresholds.same_weekday_weeks
    window = np.column_stack(
        [
            values.shift(7 * k, freq="D").reindex(values.index).to_numpy()
            for k in range(1, weeks + 1)
        ]
    )
    complete = ~np.isnan(window).any(axis=1)
    reference = np.full(len(values), np.nan)
    scale = np.full(len(values), np.nan)
    if complete.any():
        rows = window[complete]
        median = np.median(rows, axis=1)
        mad = np.median(np.abs(rows - median[:, None]), axis=1)
        reference[complete] = median
        scale[complete] = np.maximum(1.4826 * mad, thresholds.min_relative_scale * median)
    with np.errstate(divide="ignore", invalid="ignore"):
        score = (values.to_numpy() - reference) / scale
    score[~np.isfinite(score)] = np.nan
    index = values.index
    return SameWeekdayScores(
        observed=values,
        reference=pd.Series(reference, index=index),
        scale=pd.Series(scale, index=index),
        score=pd.Series(score, index=index),
    )


def citywide_anomalies(
    series: DemandSeries, thresholds: QualityThresholds = DEFAULT_THRESHOLDS
) -> list[QualityFlag]:
    """``surge`` and ``low_volume`` flags on the city-wide total, grouped into runs."""
    scores = same_weekday_scores(series, thresholds)
    ratio = scores.observed / scores.reference
    surge = (scores.score >= thresholds.robust_score) & (
        ratio >= 1 + thresholds.min_relative_change
    )
    low = (scores.score <= -thresholds.robust_score) & (ratio <= 1 - thresholds.min_relative_change)
    flags: list[QualityFlag] = []
    for code, mask, pick in ((FlagCode.SURGE, surge, max), (FlagCode.LOW_VOLUME, low, min)):
        days = [d.date() for d in pd.DatetimeIndex(scores.observed.index[mask.to_numpy()])]
        for start, end in _runs(days):
            span = slice(pd.Timestamp(start), pd.Timestamp(end))
            flags.append(
                QualityFlag(
                    start,
                    end,
                    code,
                    TOTAL_SUBJECT,
                    observed=float(scores.observed[span].sum()),
                    expected=float(scores.reference[span].sum()),
                    score=float(pick(scores.score[span])),
                )
            )
    return flags


def segment_interruptions(
    segment: pd.Series, subject: str, thresholds: QualityThresholds = DEFAULT_THRESHOLDS
) -> list[QualityFlag]:
    """``intake_interruption`` and ``catch_up_surge`` flags for one segment's daily counts.

    ``segment`` must be a daily series on a complete calendar (zeros explicit, ``NaN``
    where the count is unknown). A window containing an unknown day is not used.
    """
    values = segment.to_numpy(dtype="float64")
    dates = [d.date() for d in pd.DatetimeIndex(segment.index)]
    window = thresholds.interruption_window_days
    fraction = thresholds.interruption_max_fraction

    # Pass 1: interruption runs (start, end, typical level before the run).
    runs: list[tuple[int, int, float]] = []
    i = window
    while i < len(values):
        history = values[i - window : i]
        if np.isnan(history).any() or np.isnan(values[i]):
            i += 1
            continue
        reference = float(np.quantile(history, thresholds.interruption_reference_quantile))
        if reference < thresholds.interruption_min_reference or values[i] > fraction * reference:
            i += 1
            continue
        end = i
        while end + 1 < len(values) and values[end + 1] <= fraction * reference:
            end += 1
        runs.append((i, end, float(np.median(history))))
        i = end + 1

    # Pass 2: a catch-up is searched only up to the day before the next interruption,
    # so a day is never the catch-up of two interruptions.
    flags: list[QualityFlag] = []
    for number, (first, last, typical) in enumerate(runs):
        flags.append(
            QualityFlag(
                dates[first],
                dates[last],
                FlagCode.INTAKE_INTERRUPTION,
                subject,
                observed=float(values[first : last + 1].sum()),
                expected=typical * (last - first + 1),
            )
        )
        next_start = runs[number + 1][0] if number + 1 < len(runs) else len(values)
        stop = min(last + 1 + thresholds.catch_up_days, next_start, len(values))
        for after in range(last + 1, stop):
            if values[after] >= thresholds.catch_up_ratio * typical:
                flags.append(
                    QualityFlag(
                        dates[after],
                        dates[after],
                        FlagCode.CATCH_UP_SURGE,
                        subject,
                        observed=float(values[after]),
                        expected=typical,
                        explanation=f"after intake_interruption {dates[first]}..{dates[last]}",
                    )
                )
                break
    return flags


def segment_matrix(
    daily_segments: pd.DataFrame, by: str, calendar: pd.DatetimeIndex
) -> pd.DataFrame:
    """Daily counts per ``by`` value on ``calendar``; an absent segment row means zero."""
    counts = (
        daily_segments.groupby(["request_date", by])["request_count"]
        .sum()
        .unstack(by)
        .reindex(calendar)
        .fillna(0)
        .astype("float64")
    )
    return counts.reindex(columns=sorted(counts.columns))


# --- Holiday context and assembly ---------------------------------------------------


def _holiday_context(start: date, end: date) -> dict[date, str]:
    """Label every day in ``start .. end`` related to a federal holiday.

    A day can carry several labels (Friday 2026-07-03 is both the observed Independence
    Day and its eve); they are joined in a fixed order: holiday, observed, eve, day after.
    """
    labels: dict[date, list[str]] = {}
    one_day = timedelta(days=1)
    for holiday in holidays_between(start - one_day, end + one_day):
        related = [(holiday.actual, holiday.name)]
        if holiday.observed != holiday.actual:
            related.append((holiday.observed, f"{holiday.name} (observed)"))
        related.append((holiday.actual - one_day, f"eve of {holiday.name}"))
        related.append((holiday.actual + one_day, f"day after {holiday.name}"))
        for day, label in related:
            if start <= day <= end:
                labels.setdefault(day, []).append(label)
    return {day: "; ".join(names) for day, names in labels.items()}


def _explain(flags: list[QualityFlag]) -> list[QualityFlag]:
    if not flags:
        return flags
    first = min(flag.start for flag in flags)
    last = max(flag.end for flag in flags)
    holidays = _holiday_context(first, last)
    catch_ups: dict[date, list[str]] = {}
    for flag in flags:
        if flag.code is FlagCode.CATCH_UP_SURGE:
            catch_ups.setdefault(flag.start, []).append(flag.subject)
    explained: list[QualityFlag] = []
    for flag in flags:
        notes = [flag.explanation] if flag.explanation else []
        days = [flag.start + timedelta(days=k) for k in range(flag.days)]
        if flag.code is FlagCode.SURGE:
            notes.extend(
                f"catch_up_surge of {subject} on {d}"
                for d in days
                for subject in sorted(catch_ups.get(d, []))
            )
        labels = [holidays[d] for d in days if d in holidays]
        explained.append(
            dataclasses.replace(
                flag,
                explanation="; ".join(notes) or None,
                holiday="; ".join(dict.fromkeys(labels)) or None,  # day order, no repeats
                holiday_days=len(labels),
            )
        )
    return explained


def unassigned_share_flags(
    residual: pd.Series, totals: pd.Series, share_threshold_pct: float
) -> list[QualityFlag]:
    """Days on which the UNASSIGNED residual exceeds ``share_threshold_pct`` of the total.

    Uses only same-day values, so it is causal. The threshold is the one declared for the
    reconciliation exception (2 % for legacy-v0).
    """
    aligned = totals.reindex(residual.index).astype("float64")
    share = residual.astype("float64") * 100.0 / aligned.where(aligned > 0)
    days = [
        d.date() for d in pd.DatetimeIndex(share.index[(share > share_threshold_pct).to_numpy()])
    ]
    flags = []
    for start, end in _runs(days):
        span = slice(pd.Timestamp(start), pd.Timestamp(end))
        flags.append(
            QualityFlag(
                start,
                end,
                FlagCode.UNASSIGNED_SHARE,
                UNASSIGNED_SUBJECT,
                observed=float(residual[span].sum()),
                expected=None,
                explanation=(
                    f"UNASSIGNED share up to {float(share[span].max()):.2f}% of the city-wide "
                    f"total (threshold {share_threshold_pct:g}%)"
                ),
            )
        )
    return flags


def detect_flags(
    total: DemandSeries,
    daily_segments: pd.DataFrame | None = None,
    thresholds: QualityThresholds = DEFAULT_THRESHOLDS,
    *,
    residual: pd.Series | None = None,
    share_threshold_pct: float = DEFAULT_SHARE_THRESHOLD_PCT,
) -> list[QualityFlag]:
    """Run every detector; flags are sorted by start date, code and subject.

    ``residual`` (the daily UNASSIGNED component) enables the ``unassigned_share`` flag.
    """
    flags = missing_periods(total) + citywide_anomalies(total, thresholds)
    if residual is not None:
        flags.extend(unassigned_share_flags(residual, total.to_series(), share_threshold_pct))
    if daily_segments is not None and not daily_segments.empty:
        calendar = pd.DatetimeIndex(total.to_series().index)
        unknown = total.to_series().isna().to_numpy()
        for by in ("agency", "borough"):
            matrix = segment_matrix(daily_segments, by, calendar)
            matrix[unknown] = np.nan  # no total on that date: segment count is unknown
            for name in matrix.columns:
                flags.extend(segment_interruptions(matrix[name], f"{by}:{name}", thresholds))
    return sorted(_explain(flags), key=lambda f: (f.start, f.code.value, f.subject))


def flags_frame(flags: Iterable[QualityFlag]) -> pd.DataFrame:
    """Flags as a table satisfying the ``quality_flags`` contract."""
    records = [flag.to_record() for flag in flags]
    frame = pd.DataFrame.from_records(records, columns=list(QUALITY_FLAGS.column_names))
    checked = validate_frame(frame, QUALITY_FLAGS)
    if not checked.ok:  # a bug in this module, not a data problem
        raise AssertionError(f"quality_flags violates its contract: {checked.issues}")
    return frame


def flag_severity(flag: QualityFlag) -> Severity:
    """``info`` for low volume or an interruption on which *every* day is holiday-related.

    Every other flag is a ``warning``. Severity is a property of the whole run: a run that
    starts on a holiday eve and continues past the holiday is a ``warning``.
    """
    expected_low = flag.code in (FlagCode.LOW_VOLUME, FlagCode.INTAKE_INTERRUPTION)
    all_holiday = flag.holiday is not None and flag.holiday_days == flag.days
    return Severity.INFO if expected_low and all_holiday else Severity.WARNING


def quality_report(flags: list[QualityFlag], facts: Mapping[str, Any] | None = None) -> CheckReport:
    """Summarise flags as M0 issues: one issue per (code, subject, severity)."""
    groups: dict[tuple[FlagCode, str, Severity], list[QualityFlag]] = {}
    for flag in flags:
        groups.setdefault((flag.code, flag.subject, flag_severity(flag)), []).append(flag)
    issues = []
    for (code, subject, severity), members in sorted(
        groups.items(), key=lambda item: (item[0][0].value, item[0][1], item[0][2].value)
    ):
        issues.append(
            Issue(
                code.value,
                severity,
                subject,
                _DESCRIPTIONS[code],
                count=len(members),
                examples=examples(_describe(flag) for flag in members),
            )
        )
    all_facts: dict[str, Any] = {
        "flags": [
            {
                **flag.to_record(),
                "start_date": flag.start.isoformat(),
                "end_date": flag.end.isoformat(),
            }
            for flag in flags
        ],
        "flag_counts": {code.value: sum(f.code is code for f in flags) for code in FlagCode},
    }
    all_facts.update(facts or {})
    return CheckReport(name="quality", issues=tuple(issues), facts=all_facts)


def _describe(flag: QualityFlag) -> str:
    span = flag.start.isoformat() if flag.days == 1 else f"{flag.start}..{flag.end}"
    parts = [span]
    if flag.observed is not None and flag.expected is not None:
        parts.append(f"observed {flag.observed:,.0f} vs reference {flag.expected:,.0f}")
    if flag.score is not None:
        parts.append(f"score {flag.score:+.1f}")
    for note in (flag.holiday, flag.explanation):
        if note:
            parts.append(f"[{note}]")
    return ", ".join(parts)


# --- Snapshot stage -----------------------------------------------------------------


def assess_snapshot(
    daily_total: pd.DataFrame,
    daily_segments: pd.DataFrame,
    register: KnownExceptions,
    thresholds: QualityThresholds = DEFAULT_THRESHOLDS,
) -> CheckReport:
    """The ``quality`` stage: reconciled residual plus every data-quality flag.

    Errors (the stage fails): the residual cannot be built because totals and segments
    do not reconcile in their declared form, or the residual disagrees with the M0
    reconciliation statistics. Flags are warnings or info; they never fail the stage.
    """
    subject = "daily_total~daily_segments"
    try:
        components = build_borough_components(daily_total, daily_segments, register)
    except DataQualityError as exc:
        return CheckReport(name="quality", issues=exc.issues)

    residual = components.residual()
    stats = components.stats
    largest = int(residual.max())
    facts: dict[str, Any] = {
        "residual": {
            "label": UNASSIGNED_COMPONENT,
            "total": int(residual.sum()),
            "dates": len(residual),
            "dates_positive": int((residual > 0).sum()),
            "max": largest,
            "max_date": (
                pd.DatetimeIndex(residual.index)[int(residual.to_numpy().argmax())]
                .date()
                .isoformat()
                if largest > 0
                else None
            ),
            "share_of_total_pct": (
                round(float(residual.sum()) * 100 / stats.total_requests, 4)
                if stats.total_requests
                else 0.0
            ),
            "exception_id": next(
                (issue.exception_id for issue in components.issues if issue.exception_id), None
            ),
        }
    }
    issues: list[Issue] = []
    if int(residual.sum()) != stats.total_gap or int(residual.max()) != stats.max_gap:
        issues.append(
            Issue(
                "residual_mismatch",
                Severity.ERROR,
                subject,
                "The UNASSIGNED residual disagrees with the M0 reconciliation statistics.",
            )
        )

    flags = _snapshot_flags(daily_total, daily_segments, register, thresholds, residual)
    flags_frame(flags)  # contract check of the flag table
    report = quality_report(flags, facts)
    return CheckReport(name=report.name, issues=(*issues, *report.issues), facts=report.facts)


def _snapshot_flags(
    daily_total: pd.DataFrame,
    daily_segments: pd.DataFrame,
    register: KnownExceptions,
    thresholds: QualityThresholds,
    residual: pd.Series,
) -> list[QualityFlag]:
    total = DemandSeries.from_frame(daily_total, name="daily_total", allow_missing=True)
    declared = register.reconciliation
    return detect_flags(
        total,
        daily_segments,
        thresholds,
        residual=residual,
        share_threshold_pct=(
            declared.expected.share_threshold_pct if declared else DEFAULT_SHARE_THRESHOLD_PCT
        ),
    )


def snapshot_flags(
    daily_total: pd.DataFrame,
    daily_segments: pd.DataFrame,
    register: KnownExceptions,
    thresholds: QualityThresholds = DEFAULT_THRESHOLDS,
) -> list[QualityFlag]:
    """Every flag ``atlas quality`` reports for a validated snapshot (M2 evaluation filter).

    Raises :class:`DataQualityError` when the reconciliation residual cannot be built.
    Flags are causal annotations: M2 uses them only to choose which target days a
    secondary analysis leaves out, never as model inputs.
    """
    components = build_borough_components(daily_total, daily_segments, register)
    return _snapshot_flags(daily_total, daily_segments, register, thresholds, components.residual())


def warning_flag_dates(flags: Iterable[QualityFlag]) -> list[date]:
    """Every date covered by a warning-level flag, sorted and without duplicates."""
    days: set[date] = set()
    for flag in flags:
        if flag_severity(flag) is Severity.WARNING:
            days.update(flag.start + timedelta(days=k) for k in range(flag.days))
    return sorted(days)
