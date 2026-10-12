"""Table rules, cross-table reconciliation and the declared-exception policy."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from atlas.contracts import DAILY_SEGMENTS, DAILY_TOTAL, TableContract
from atlas.errors import SnapshotError
from atlas.issues import Severity
from atlas.loader import LoadedTable, load_table
from atlas.validation import (
    KnownExceptions,
    ReconciliationExpectation,
    ReconciliationGapException,
    load_exceptions,
    validate_tables,
)
from tests.conftest import SMALL_DATES, write_csv

TOTAL_HEADER = ["request_date", "request_count"]
SEGMENT_HEADER = ["request_date", "agency", "borough", "request_count"]


def load_pair(
    tmp_path: Path,
    total_contract: TableContract,
    segment_contract: TableContract,
    totals: list[list[object]],
    segments: list[list[object]],
) -> dict[str, LoadedTable]:
    return {
        "daily_total": load_table(
            write_csv(tmp_path / "daily_total.csv", TOTAL_HEADER, totals), total_contract
        ),
        "daily_segments": load_table(
            write_csv(tmp_path / "daily_segments.csv", SEGMENT_HEADER, segments), segment_contract
        ),
    }


def register_with(expected: ReconciliationExpectation | None) -> KnownExceptions:
    if expected is None:
        return KnownExceptions(schema_version=1, snapshot_id="test")
    exception = ReconciliationGapException(
        id="TEST-EX-1",
        check="segment_reconciliation_gap",
        title="test gap",
        cause="test",
        evidence=(),
        policy="accept",
        expected=expected,
    )
    return KnownExceptions(schema_version=1, snapshot_id="test", exceptions=(exception,))


# The synthetic segments are 5 requests short of the total on every one of 14 days.
MATCHING = ReconciliationExpectation(
    dates_compared=14,
    dates_with_gap=14,
    dates_reconciled=0,
    total_gap=70,
    max_gap=5,
    max_gap_date=date(2024, 1, 1),
    share_threshold_pct=2.0,
    dates_above_share_threshold=(),
)


@pytest.fixture
def contracts(
    small_total_contract: TableContract, small_segments_contract: TableContract
) -> dict[str, TableContract]:
    return {"daily_total": small_total_contract, "daily_segments": small_segments_contract}


def test_duplicate_dates_are_rejected(
    tmp_path: Path, small_total_contract: TableContract, total_rows: list[list[object]]
) -> None:
    rows = [*total_rows, total_rows[3]]
    table = load_table(write_csv(tmp_path / "t.csv", TOTAL_HEADER, rows), small_total_contract)
    issue = next(i for i in table.issues if i.code == "duplicate_key")
    assert issue.count == 1
    assert issue.examples == ("2024-01-04",)


def test_duplicate_segment_keys_are_rejected(
    tmp_path: Path, small_segments_contract: TableContract, segment_rows: list[list[object]]
) -> None:
    rows = [*segment_rows, segment_rows[0]]
    table = load_table(write_csv(tmp_path / "s.csv", SEGMENT_HEADER, rows), small_segments_contract)
    assert any(i.code == "duplicate_key" for i in table.issues)


def test_missing_calendar_dates_are_rejected(
    tmp_path: Path, small_total_contract: TableContract, total_rows: list[list[object]]
) -> None:
    rows = [row for i, row in enumerate(total_rows) if i not in (0, 6, 13)]
    table = load_table(write_csv(tmp_path / "t.csv", TOTAL_HEADER, rows), small_total_contract)
    issue = next(i for i in table.issues if i.code == "missing_dates")
    assert issue.count == 3
    assert issue.examples == ("2024-01-01", "2024-01-07", "2024-01-14")


def test_dates_outside_the_window_are_rejected(
    tmp_path: Path, small_total_contract: TableContract, total_rows: list[list[object]]
) -> None:
    rows = [*total_rows, ["2024-02-01", 5]]
    table = load_table(write_csv(tmp_path / "t.csv", TOTAL_HEADER, rows), small_total_contract)
    issue = next(i for i in table.issues if i.code == "date_out_of_window")
    assert issue.examples == ("2024-02-01",)


@settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(removed=st.sets(st.integers(min_value=0, max_value=13), max_size=13))
def test_missing_date_count_equals_dates_removed(
    tmp_path_factory: pytest.TempPathFactory,
    small_total_contract: TableContract,
    removed: set[int],
) -> None:
    rows = [[d.isoformat(), 1] for i, d in enumerate(SMALL_DATES) if i not in removed]
    path = write_csv(tmp_path_factory.mktemp("prop") / "t.csv", TOTAL_HEADER, rows)
    missing = [
        i for i in load_table(path, small_total_contract).issues if i.code == "missing_dates"
    ]
    if removed:
        assert missing[0].count == len(removed)
    else:
        assert missing == []


def test_declared_gap_with_matching_statistics_is_accepted(
    tmp_path: Path,
    contracts: dict[str, TableContract],
    total_rows: list[list[object]],
    segment_rows: list[list[object]],
) -> None:
    tables = load_pair(tmp_path, *contracts.values(), total_rows, segment_rows)
    report = validate_tables(tables, contracts, register_with(MATCHING))
    assert report.ok
    accepted = [i for i in report.issues if i.code == "accepted_known_exception"]
    assert len(accepted) == 1
    assert accepted[0].severity is Severity.INFO
    assert accepted[0].exception_id == "TEST-EX-1"
    assert report.facts["reconciliation"]["total_gap"] == 70


def test_undeclared_gap_fails_closed(
    tmp_path: Path,
    contracts: dict[str, TableContract],
    total_rows: list[list[object]],
    segment_rows: list[list[object]],
) -> None:
    tables = load_pair(tmp_path, *contracts.values(), total_rows, segment_rows)
    report = validate_tables(tables, contracts, register_with(None))
    assert not report.ok
    assert [i.code for i in report.errors] == ["unexplained_reconciliation_gap"]


def test_gap_that_drifts_from_declaration_fails(
    tmp_path: Path,
    contracts: dict[str, TableContract],
    total_rows: list[list[object]],
    segment_rows: list[list[object]],
) -> None:
    rows = [list(row) for row in segment_rows]
    rows[0][3] = int(str(rows[0][3])) - 100  # widen the gap on 2024-01-01
    tables = load_pair(tmp_path, *contracts.values(), total_rows, rows)
    report = validate_tables(tables, contracts, register_with(MATCHING))
    drift = next(i for i in report.errors if i.code == "exception_drift")
    assert "total_gap: declared 70, observed 170" in drift.examples
    assert "max_gap: declared 5, observed 105" in drift.examples


def test_segments_exceeding_total_is_always_an_error(
    tmp_path: Path,
    contracts: dict[str, TableContract],
    total_rows: list[list[object]],
    segment_rows: list[list[object]],
) -> None:
    rows = [list(row) for row in segment_rows]
    rows[1][3] = 1000  # 2024-01-01 segments now sum above the city-wide total
    tables = load_pair(tmp_path, *contracts.values(), total_rows, rows)
    report = validate_tables(tables, contracts, register_with(MATCHING))
    exceed = next(i for i in report.errors if i.code == "segments_exceed_total")
    assert exceed.examples == ("2024-01-01",)


def test_perfect_reconciliation_and_stale_exception(
    tmp_path: Path, contracts: dict[str, TableContract], total_rows: list[list[object]]
) -> None:
    segments = [[day, "NYPD", "BRONX", count] for day, count in total_rows]
    tables = load_pair(tmp_path, *contracts.values(), total_rows, segments)

    clean = validate_tables(tables, contracts, register_with(None))
    assert clean.ok
    assert clean.issues == ()

    stale = validate_tables(tables, contracts, register_with(MATCHING))
    assert stale.ok
    assert [(i.code, i.severity) for i in stale.issues] == [("stale_exception", Severity.WARNING)]


def test_missing_table_and_skipped_reconciliation(
    tmp_path: Path, contracts: dict[str, TableContract], total_rows: list[list[object]]
) -> None:
    broken = [list(row) for row in total_rows]
    broken[0][1] = "oops"
    tables = {
        "daily_total": load_table(
            write_csv(tmp_path / "t.csv", TOTAL_HEADER, broken), contracts["daily_total"]
        )
    }
    report = validate_tables(tables, contracts, register_with(None))
    assert {i.code for i in report.errors} == {"malformed_value", "table_missing"}
    assert report.facts["reconciliation"] == "skipped: base tables failed their contracts"


def test_exception_register_loading(tmp_path: Path) -> None:
    missing = load_exceptions(tmp_path / "none.json", "legacy-v0")
    assert missing.exceptions == ()

    wrong_snapshot = tmp_path / "wrong.json"
    wrong_snapshot.write_text(
        json.dumps({"schema_version": 1, "snapshot_id": "other", "exceptions": []}),
        encoding="utf-8",
    )
    with pytest.raises(SnapshotError, match="declares snapshot 'other'"):
        load_exceptions(wrong_snapshot, "legacy-v0")

    malformed = tmp_path / "malformed.json"
    malformed.write_text(
        json.dumps({"schema_version": 1, "snapshot_id": "legacy-v0", "exceptions": [{"id": 1}]}),
        encoding="utf-8",
    )
    with pytest.raises(SnapshotError, match="Invalid known-exception register"):
        load_exceptions(malformed, "legacy-v0")


def test_exception_ids_must_be_unique() -> None:
    exception = register_with(MATCHING).exceptions[0]
    with pytest.raises(ValueError, match="unique"):
        KnownExceptions(schema_version=1, snapshot_id="t", exceptions=(exception, exception))


def test_real_contracts_cover_full_legacy_window() -> None:
    assert DAILY_TOTAL.window == (date(2023, 1, 1), date(2026, 6, 30))
    assert DAILY_SEGMENTS.primary_key == ("request_date", "agency", "borough")
