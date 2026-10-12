"""Strict loading: schema, types, nulls, ranges, categories and determinism."""

from __future__ import annotations

import dataclasses
import random
from pathlib import Path

import pandas as pd
import pytest

from atlas.contracts import Column, Kind, TableContract
from atlas.issues import Severity
from atlas.loader import LoadedTable, load_table
from tests.conftest import write_csv

TOTAL_HEADER = ["request_date", "request_count"]
SEGMENT_HEADER = ["request_date", "agency", "borough", "request_count"]


def codes(table: LoadedTable) -> set[str]:
    return {issue.code for issue in table.issues}


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


def test_csv_quoting_errors_are_reported(
    tmp_path: Path, small_total_contract: TableContract
) -> None:
    path = tmp_path / "quotes.csv"
    path.write_text('request_date,request_count\n"2024-01-01"x,5\n', encoding="utf-8")
    table = load_table(path, small_total_contract)
    assert codes(table) == {"csv_syntax_error"}
    assert not table.ok


@pytest.mark.parametrize("bad_date", ["0024-03-05", "9999-12-31", "1500-06-01", "2262-04-12"])
def test_dates_outside_the_supported_range_are_reported_not_raised(
    tmp_path: Path,
    small_total_contract: TableContract,
    total_rows: list[list[object]],
    bad_date: str,
) -> None:
    rows = [list(row) for row in total_rows]
    rows[0][0] = bad_date
    table = load_table(write_csv(tmp_path / "t.csv", TOTAL_HEADER, rows), small_total_contract)
    assert "malformed_value" in codes(table)


@pytest.mark.parametrize(
    "count", ["9223372036854775808", "-9223372036854775809", "9" * 20, "9" * 5000]
)
def test_integers_beyond_64_bits_are_reported_not_raised(
    tmp_path: Path,
    small_total_contract: TableContract,
    total_rows: list[list[object]],
    count: str,
) -> None:
    rows = [list(row) for row in total_rows]
    rows[1][1] = count
    table = load_table(write_csv(tmp_path / "t.csv", TOTAL_HEADER, rows), small_total_contract)
    assert "malformed_value" in codes(table)


def test_largest_int64_value_is_accepted_exactly(
    tmp_path: Path, small_total_contract: TableContract, total_rows: list[list[object]]
) -> None:
    rows = [list(row) for row in total_rows]
    rows[1][1] = str(2**63 - 1)
    table = load_table(write_csv(tmp_path / "t.csv", TOTAL_HEADER, rows), small_total_contract)
    assert table.ok, table.issues
    assert table.frame.loc[1, "request_count"] == 2**63 - 1


@pytest.mark.parametrize(
    ("column", "value"),
    [(0, "\u0662\u0660\u0662\u0664-01-05"), (1, "\uff11\uff12"), (1, "\u0661\u0662")],
)
def test_non_ascii_digits_are_rejected(
    tmp_path: Path,
    small_total_contract: TableContract,
    total_rows: list[list[object]],
    column: int,
    value: str,
) -> None:
    rows = [list(row) for row in total_rows]
    rows[4][column] = value
    table = load_table(write_csv(tmp_path / "t.csv", TOTAL_HEADER, rows), small_total_contract)
    assert "malformed_value" in codes(table)


def test_decimals_must_be_finite_ascii_numbers(tmp_path: Path) -> None:
    contract = TableContract(
        name="d",
        description="test",
        columns=(Column("key", Kind.INTEGER), Column("x", Kind.DECIMAL)),
        primary_key=("key",),
    )
    rows = [[1, "1e999"], [2, "-1e999"], [3, "\u0663.25"], [4, "\uff11"], [5, "2.5e3"]]
    issues = load_table(write_csv(tmp_path / "d.csv", ["key", "x"], rows), contract).issues
    malformed = next(i for i in issues if i.code == "malformed_value")
    assert malformed.count == 4
    assert [i.code for i in issues] == ["malformed_value"]


@pytest.mark.parametrize("token", ["NAN", "nan", "NA", "None", "null", "N/A"])
def test_null_like_text_is_rejected_in_text_columns(
    tmp_path: Path,
    small_segments_contract: TableContract,
    segment_rows: list[list[object]],
    token: str,
) -> None:
    rows = [list(row) for row in segment_rows]
    rows[0][1] = token  # the legacy notebook turned a missing agency into "NAN"
    table = load_table(write_csv(tmp_path / "s.csv", SEGMENT_HEADER, rows), small_segments_contract)
    assert "null_token" in codes(table)
    assert not table.ok


def test_line_numbers_account_for_multiline_quoted_fields(tmp_path: Path) -> None:
    contract = TableContract(
        name="m",
        description="test",
        columns=(Column("key", Kind.INTEGER), Column("note", Kind.STRING)),
        primary_key=("key",),
    )
    path = tmp_path / "m.csv"
    path.write_text('key,note\n1,"first\nsecond"\nx,ok\n', encoding="utf-8")
    issue = next(i for i in load_table(path, contract).issues if i.code == "malformed_value")
    assert issue.examples == ("line 4: 'x'",)
