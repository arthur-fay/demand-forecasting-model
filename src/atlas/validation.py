"""Snapshot validation: single-table contracts, cross-table rules and declared exceptions.

Policy (fail closed):

* Any contract violation in any table is an error.
* ``daily_segments`` may never contain more requests on a date than ``daily_total``.
* A gap between the city-wide total and the five-borough segment sum is an error
  **unless** a known exception declares it *and* the observed gap statistics match
  the declaration exactly. Accepted exceptions are reported as ``info`` so they stay
  visible. Any drift from the declaration is an error.
* A declared exception that no longer occurs is reported as a warning (stale).

No records are invented to force reconciliation: the gap is measured and reported.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from atlas.contracts import TableContract
from atlas.errors import SnapshotError
from atlas.issues import CheckReport, Issue, Severity, examples
from atlas.loader import LoadedTable

DEFAULT_SHARE_THRESHOLD_PCT = 2.0


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReconciliationExpectation(_Frozen):
    dates_compared: int
    dates_with_gap: int
    dates_reconciled: int
    total_gap: int
    max_gap: int
    max_gap_date: date
    share_threshold_pct: float = Field(gt=0, le=100)
    dates_above_share_threshold: tuple[date, ...]


class ReconciliationGapException(_Frozen):
    id: str
    check: Literal["segment_reconciliation_gap"]
    title: str
    cause: str
    evidence: tuple[str, ...]
    policy: Literal["accept"]
    expected: ReconciliationExpectation


class MetricExpectation(_Frozen):
    # Only legacy model metrics can be declared today; anything else is a schema error.
    table: Literal["model_metrics"]
    row: str
    column: Literal["mae", "rmse", "mape_pct", "r_squared", "mape_improvement_pct"]
    committed: float
    recomputed_rounded: float
    decimals: int = Field(ge=0, le=10)


class MetricReproductionException(_Frozen):
    id: str
    check: Literal["legacy_metric_reproduction"]
    title: str
    cause: str
    evidence: tuple[str, ...]
    policy: Literal["accept"]
    expected: MetricExpectation


KnownException = Annotated[
    ReconciliationGapException | MetricReproductionException, Field(discriminator="check")
]


class KnownExceptions(_Frozen):
    schema_version: Literal[1]
    snapshot_id: str
    exceptions: tuple[KnownException, ...] = ()

    @model_validator(mode="after")
    def _unique_ids(self) -> KnownExceptions:
        ids = [exception.id for exception in self.exceptions]
        if len(ids) != len(set(ids)):
            raise ValueError("exception ids must be unique")
        gaps = [e for e in self.exceptions if isinstance(e, ReconciliationGapException)]
        if len(gaps) > 1:
            raise ValueError("at most one segment_reconciliation_gap exception is allowed")
        return self

    @property
    def reconciliation(self) -> ReconciliationGapException | None:
        for exception in self.exceptions:
            if isinstance(exception, ReconciliationGapException):
                return exception
        return None

    @property
    def metric_exceptions(self) -> tuple[MetricReproductionException, ...]:
        return tuple(e for e in self.exceptions if isinstance(e, MetricReproductionException))


def load_exceptions(path: Path, snapshot_id: str) -> KnownExceptions:
    """Load the exception register. A missing file means "no exceptions declared"."""
    if not path.is_file():
        return KnownExceptions(schema_version=1, snapshot_id=snapshot_id)
    try:
        register = KnownExceptions.model_validate_json(path.read_bytes())
    except ValidationError as exc:
        raise SnapshotError(f"Invalid known-exception register {path}:\n{exc}") from exc
    except OSError as exc:
        raise SnapshotError(f"Cannot read known-exception register {path}: {exc}") from exc
    if register.snapshot_id != snapshot_id:
        raise SnapshotError(
            f"{path} declares snapshot {register.snapshot_id!r}, expected {snapshot_id!r}"
        )
    return register


# --- Cross-table reconciliation -------------------------------------------------------


@dataclass(frozen=True)
class ReconciliationStats:
    """How the city-wide daily total compares with the five-borough segment sum."""

    dates_compared: int
    dates_with_gap: int
    dates_reconciled: int
    dates_segments_exceed_total: int
    total_requests: int
    segment_requests: int
    total_gap: int
    gap_share_pct: float
    max_gap: int
    max_gap_date: date | None
    max_gap_share_pct: float
    share_threshold_pct: float
    dates_above_share_threshold: tuple[date, ...]
    dates_exceeding: tuple[date, ...]

    def as_facts(self) -> dict[str, Any]:
        facts = asdict(self)
        facts["max_gap_date"] = self.max_gap_date.isoformat() if self.max_gap_date else None
        facts["dates_above_share_threshold"] = [
            d.isoformat() for d in self.dates_above_share_threshold
        ]
        facts["dates_exceeding"] = [d.isoformat() for d in self.dates_exceeding[:5]]
        facts["gap_share_pct"] = round(self.gap_share_pct, 4)
        facts["max_gap_share_pct"] = round(self.max_gap_share_pct, 4)
        return facts


def reconcile_segments(
    daily_total: pd.DataFrame,
    daily_segments: pd.DataFrame,
    share_threshold_pct: float = DEFAULT_SHARE_THRESHOLD_PCT,
) -> ReconciliationStats:
    """Measure, per date, ``daily_total - sum(daily_segments)``."""
    totals = daily_total.set_index("request_date")["request_count"].astype("int64")
    segment_sums = daily_segments.groupby("request_date")["request_count"].sum().astype("int64")
    dates = totals.index.union(segment_sums.index)
    totals = totals.reindex(dates, fill_value=0)
    segment_sums = segment_sums.reindex(dates, fill_value=0)
    gap = totals - segment_sums
    share = (gap / totals.where(totals > 0)).fillna(0.0) * 100.0

    positive = gap[gap > 0]
    exceeding = gap[gap < 0]
    max_gap = int(positive.max()) if not positive.empty else 0
    positive_dates = pd.DatetimeIndex(positive.index)
    max_gap_date = (
        positive_dates[int(positive.to_numpy().argmax())].date() if len(positive) else None
    )
    above = pd.DatetimeIndex(share[(share > share_threshold_pct) & (gap > 0)].index)
    total_requests = int(totals.sum())
    return ReconciliationStats(
        dates_compared=len(dates),
        dates_with_gap=len(positive),
        dates_reconciled=int((gap == 0).sum()),
        dates_segments_exceed_total=len(exceeding),
        total_requests=total_requests,
        segment_requests=int(segment_sums.sum()),
        total_gap=int(positive.sum()),
        gap_share_pct=float(positive.sum() * 100.0 / total_requests) if total_requests else 0.0,
        max_gap=max_gap,
        max_gap_date=max_gap_date,
        max_gap_share_pct=float(share.max()) if len(share) else 0.0,
        share_threshold_pct=share_threshold_pct,
        dates_above_share_threshold=tuple(d.date() for d in above),
        dates_exceeding=tuple(d.date() for d in pd.DatetimeIndex(exceeding.index)),
    )


def _expectation_differences(
    stats: ReconciliationStats, expected: ReconciliationExpectation
) -> list[str]:
    observed = {
        "dates_compared": stats.dates_compared,
        "dates_with_gap": stats.dates_with_gap,
        "dates_reconciled": stats.dates_reconciled,
        "total_gap": stats.total_gap,
        "max_gap": stats.max_gap,
        "max_gap_date": stats.max_gap_date,
        "dates_above_share_threshold": stats.dates_above_share_threshold,
    }
    declared = expected.model_dump()
    return [
        f"{field}: declared {declared[field]}, observed {value}"
        for field, value in observed.items()
        if declared[field] != value
    ]


def check_reconciliation(
    daily_total: pd.DataFrame, daily_segments: pd.DataFrame, register: KnownExceptions
) -> tuple[list[Issue], ReconciliationStats]:
    declared = register.reconciliation
    threshold = declared.expected.share_threshold_pct if declared else DEFAULT_SHARE_THRESHOLD_PCT
    stats = reconcile_segments(daily_total, daily_segments, threshold)
    subject = "daily_total~daily_segments"
    issues: list[Issue] = []

    if stats.dates_segments_exceed_total:
        issues.append(
            Issue(
                "segments_exceed_total",
                Severity.ERROR,
                subject,
                "Segment sum exceeds the city-wide total, which is impossible for a filtered "
                "subset of the same records.",
                count=stats.dates_segments_exceed_total,
                examples=examples(stats.dates_exceeding),
            )
        )

    if stats.dates_with_gap == 0:
        if declared is not None:
            issues.append(
                Issue(
                    "stale_exception",
                    Severity.WARNING,
                    subject,
                    "A reconciliation gap is declared but none was observed.",
                    exception_id=declared.id,
                )
            )
        return issues, stats

    summary = (
        f"{stats.dates_with_gap} of {stats.dates_compared} dates have fewer segment requests "
        f"than the city-wide total; {stats.total_gap:,} requests ({stats.gap_share_pct:.4f}%) "
        f"are not assigned to a named borough; largest gap {stats.max_gap:,} on "
        f"{stats.max_gap_date}."
    )
    if declared is None:
        issues.append(
            Issue(
                "unexplained_reconciliation_gap",
                Severity.ERROR,
                subject,
                summary + " No known exception declares this gap.",
                count=stats.dates_with_gap,
            )
        )
        return issues, stats

    differences = _expectation_differences(stats, declared.expected)
    if differences:
        issues.append(
            Issue(
                "exception_drift",
                Severity.ERROR,
                subject,
                "Observed reconciliation gap no longer matches the declared known exception.",
                count=len(differences),
                examples=examples(differences),
                exception_id=declared.id,
            )
        )
    else:
        issues.append(
            Issue(
                "accepted_known_exception",
                Severity.INFO,
                subject,
                summary + f" Accepted: {declared.title}",
                count=stats.dates_with_gap,
                exception_id=declared.id,
            )
        )
    return issues, stats


# --- Orchestration --------------------------------------------------------------------


def validate_tables(
    tables: Mapping[str, LoadedTable],
    contracts: Mapping[str, TableContract],
    register: KnownExceptions,
) -> CheckReport:
    """Combine single-table contract results with cross-table rules."""
    issues: list[Issue] = []
    for name in contracts:
        if name not in tables:
            issues.append(
                Issue("table_missing", Severity.ERROR, name, "Contracted table was not loaded.")
            )
            continue
        issues.extend(tables[name].issues)

    facts: dict[str, Any] = {
        "tables_validated": sorted(name for name in contracts if name in tables),
        "rows": {name: len(table.frame) for name, table in sorted(tables.items())},
    }

    base = [tables.get("daily_total"), tables.get("daily_segments")]
    if all(table is not None and table.ok for table in base):
        total, segments = (table.frame for table in base if table is not None)
        reconciliation_issues, stats = check_reconciliation(total, segments, register)
        issues.extend(reconciliation_issues)
        facts["reconciliation"] = stats.as_facts()
    elif "daily_total" in contracts and "daily_segments" in contracts:
        facts["reconciliation"] = "skipped: base tables failed their contracts"

    return CheckReport(name="validation", issues=tuple(issues), facts=facts)
