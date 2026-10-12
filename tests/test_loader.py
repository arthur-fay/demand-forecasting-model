"""Strict loading: schema, types, nulls, ranges, categories and determinism."""

from __future__ import annotations

import dataclasses
import random
from pathlib import Path

import pandas as pd
import pytest

from atlas.contracts import Column, Kind, TableContract
from atlas.issues import Severity
from atlas.loader import load_table
from tests.conftest import write_csv

TOTAL_HEADER = ["request_date", "request_count"]
SEGMENT_HEADER = ["request_date", "agency", "borough", "request_count"]


def codes(table: object) -> set[str]:
    return {issue.code for issue in table.issues}  # type: ignore[attr-defined]


def test_valid_table_loads_with_declared_types(
    tmp_path: Path, small_total_contract: TableContract, total_rows: list[list[object]]
) -> None:
    path = write_csv(tmp_path / "daily_total.csv", TOTAL_HEADER, total_rows)
    table = load_table(path, small_total_contract)

    assert table.ok
    assert table.issues == ()
    assert len(table.frame) == 14
    assert str(table.frame["request_date"].dtype) == "datetime64[ns]"
    assert str(table.frame["request_count"].dtype) == "Int64"
    assert table.frame["request_count"].sum() == sum(1000 + 10 * i for i in range(14))


def test_loading_is_deterministic_and_independent_of_row_order(
    tmp_path: Path, small_total_contract: TableContract, total_rows: list[list[object]]
) -> None:
    ordered = write_csv(tmp_path / "ordered.csv", TOTAL_HEADER, total_rows)
    shuffled_rows = list(total_rows)
    random.Random(7).shuffle(shuffled_rows)
    shuffled = write_csv(tmp_path / "shuffled.csv", TOTAL_HEADER, shuffled_rows)

    first = load_table(ordered, small_total_contract).frame
    second = load_table(ordered, small_total_contract).frame
    from_shuffled = load_table(shuffled, small_total_contract).frame

    pd.testing.assert_frame_equal(first, second)
    pd.testing.assert_frame_equal(first, from_shuffled)
    assert pd.util.hash_pandas_object(first).sum() == pd.util.hash_pandas_object(second).sum()


def test_missing_file_is_reported(tmp_path: Path, small_total_contract: TableContract) -> None:
    table = load_table(tmp_path / "absent.csv", small_total_contract)
    assert not table.ok
    assert codes(table) == {"file_missing"}
    assert list(table.frame.columns) == TOTAL_HEADER


def test_empty_file_and_header_only_file(
    tmp_path: Path, small_total_contract: TableContract
) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    assert codes(load_table(empty, small_total_contract)) == {"empty_file"}

    header_only = write_csv(tmp_path / "header.csv", TOTAL_HEADER, [])
    assert "empty_table" in codes(load_table(header_only, small_total_contract))


def test_non_utf8_file_is_rejected(tmp_path: Path, small_total_contract: TableContract) -> None:
    path = tmp_path / "latin1.csv"
    path.write_bytes(b"request_date,request_count\n2024-01-01,1\xe9\n")
    assert codes(load_table(path, small_total_contract)) == {"encoding_error"}


def test_duplicate_and_blank_header_names_are_rejected(
    tmp_path: Path, small_total_contract: TableContract
) -> None:
    duplicate = write_csv(tmp_path / "dup.csv", ["request_date", "request_date"], [["a", "b"]])
    assert codes(load_table(duplicate, small_total_contract)) == {"duplicate_column"}
    blank = write_csv(tmp_path / "blank.csv", ["request_date", ""], [["a", "b"]])
    assert codes(load_table(blank, small_total_contract)) == {"blank_column_name"}


def test_rows_with_wrong_field_count_are_reported_with_line_numbers(
    tmp_path: Path, small_total_contract: TableContract, total_rows: list[list[object]]
) -> None:
    path = write_csv(tmp_path / "ragged.csv", TOTAL_HEADER, total_rows)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("2024-01-15,1,extra\n")
    table = load_table(path, small_total_contract)
    issue = next(i for i in table.issues if i.code == "malformed_row")
    assert issue.count == 1
    assert issue.examples == ("line 16: 3 fields",)


def test_missing_and_unexpected_columns_are_errors(
    tmp_path: Path, small_total_contract: TableContract
) -> None:
    path = write_csv(tmp_path / "schema.csv", ["request_date", "count"], [["2024-01-01", "5"]])
    table = load_table(path, small_total_contract)
    assert {"missing_column", "unexpected_column"} <= codes(table)
    assert not table.ok


def test_reordered_columns_only_warn(
    tmp_path: Path, small_total_contract: TableContract, total_rows: list[list[object]]
) -> None:
    rows = [[count, day] for day, count in total_rows]
    path = write_csv(tmp_path / "reordered.csv", ["request_count", "request_date"], rows)
    table = load_table(path, small_total_contract)
    assert table.ok
    assert [(i.code, i.severity) for i in table.issues] == [("column_order", Severity.WARNING)]


@pytest.mark.parametrize(
    ("bad_date", "expected_code"),
    [
        ("2024/01/05", "malformed_value"),
        ("2024-02-30", "malformed_value"),
        ("05-01-2024", "malformed_value"),
        ("2024-01-05T00:00:00", "malformed_value"),
        ("", "null_value"),
    ],
)
def test_malformed_or_missing_dates_are_rejected(
    tmp_path: Path,
    small_total_contract: TableContract,
    total_rows: list[list[object]],
    bad_date: str,
    expected_code: str,
) -> None:
    rows = [list(row) for row in total_rows]
    rows[4][0] = bad_date
    table = load_table(write_csv(tmp_path / "t.csv", TOTAL_HEADER, rows), small_total_contract)
    assert expected_code in codes(table)
    # The bad row also leaves a hole in the calendar.
    assert "missing_dates" in codes(table)


@pytest.mark.parametrize(
    ("bad_count", "expected_code"),
    [
        ("12.5", "malformed_value"),
        ("abc", "malformed_value"),
        ("NaN", "malformed_value"),
        ("1e3", "malformed_value"),
        (" 12", "malformed_value"),
        ("-3", "out_of_range"),
        ("", "null_value"),
    ],
)
def test_invalid_counts_are_rejected(
    tmp_path: Path,
    small_total_contract: TableContract,
    total_rows: list[list[object]],
    bad_count: str,
    expected_code: str,
) -> None:
    rows = [list(row) for row in total_rows]
    rows[2][1] = bad_count
    table = load_table(write_csv(tmp_path / "t.csv", TOTAL_HEADER, rows), small_total_contract)
    assert expected_code in codes(table)
    assert not table.ok


def test_bad_values_are_reported_with_line_and_value(
    tmp_path: Path, small_total_contract: TableContract, total_rows: list[list[object]]
) -> None:
    rows = [list(row) for row in total_rows]
    rows[0][1] = "x"
    rows[3][1] = "y"
    table = load_table(write_csv(tmp_path / "t.csv", TOTAL_HEADER, rows), small_total_contract)
    issue = next(i for i in table.issues if i.code == "malformed_value")
    assert issue.subject == "daily_total.request_count"
    assert issue.count == 2
    assert issue.examples == ("line 2: 'x'", "line 5: 'y'")


def test_segment_categories_patterns_and_whitespace(
    tmp_path: Path, small_segments_contract: TableContract, segment_rows: list[list[object]]
) -> None:
    rows = [list(row) for row in segment_rows]
    rows[0][2] = "UNSPECIFIED"  # not one of the five named boroughs
    rows[1][1] = "nypd"  # agency codes are upper case
    rows[2][2] = " QUEENS"  # surrounding whitespace is not silently stripped
    table = load_table(write_csv(tmp_path / "s.csv", SEGMENT_HEADER, rows), small_segments_contract)
    assert {"invalid_category", "pattern_mismatch", "malformed_value"} <= codes(table)


def test_zero_count_segment_violates_contract(
    tmp_path: Path, small_segments_contract: TableContract, segment_rows: list[list[object]]
) -> None:
    rows = [list(row) for row in segment_rows]
    rows[0][3] = 0  # empty segments must be absent, not zero
    table = load_table(write_csv(tmp_path / "s.csv", SEGMENT_HEADER, rows), small_segments_contract)
    assert "out_of_range" in codes(table)


def test_integral_and_decimal_kinds(tmp_path: Path) -> None:
    contract = TableContract(
        name="kinds",
        description="test",
        columns=(
            Column("key", Kind.INTEGER),
            Column("integral", Kind.INTEGRAL, nullable=True),
            Column("decimal", Kind.DECIMAL, nullable=True, minimum=-1, maximum=1),
        ),
        primary_key=("key",),
    )
    good = write_csv(
        tmp_path / "good.csv",
        ["key", "integral", "decimal"],
        [[1, "6209.0", "0.25"], [2, "7", "-1"], [3, "", ""], [4, "+12", ".5"]],
    )
    loaded = load_table(good, contract)
    assert loaded.ok, loaded.issues
    assert loaded.frame["integral"].tolist() == [6209, 7, pd.NA, 12]

    bad = write_csv(
        tmp_path / "bad.csv",
        ["key", "integral", "decimal"],
        [[1, "6209.5", "0.25"], [2, "7", "inf"], [3, "8", "nan"], [4, "9", "2.0"]],
    )
    issues = load_table(bad, contract).issues
    malformed = {i.subject: i.count for i in issues if i.code == "malformed_value"}
    assert malformed == {"kinds.integral": 1, "kinds.decimal": 2}
    assert any(i.code == "out_of_range" and i.subject == "kinds.decimal" for i in issues)


def test_row_limits_are_enforced(tmp_path: Path) -> None:
    contract = TableContract(
        name="limited",
        description="test",
        columns=(Column("key", Kind.INTEGER),),
        primary_key=("key",),
        min_rows=2,
        max_rows=3,
    )
    one = write_csv(tmp_path / "one.csv", ["key"], [[1]])
    assert "too_few_rows" in codes(load_table(one, contract))
    four = write_csv(tmp_path / "four.csv", ["key"], [[1], [2], [3], [4]])
    assert "too_many_rows" in codes(load_table(four, contract))
    narrowed = dataclasses.replace(contract, min_rows=1, max_rows=None)
    assert load_table(four, narrowed).ok
