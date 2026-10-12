"""Strict, deterministic, offline loading of CSV tables against a contract.

Files are read with the standard-library ``csv`` module and every field is kept as
text until it is parsed against the column's declared :class:`~atlas.contracts.Kind`.
Nothing is type-inferred, so results do not depend on pandas inference rules that
differ between pandas versions. An empty field is the only representation of
null; strings such as ``"NaN"`` or ``"NA"`` are treated as malformed values.

Every violation of the single-table contract is collected as an
:class:`~atlas.issues.Issue` (rather than raising on the first one) so that a
report shows all problems in a file at once.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from atlas.contracts import Column, Kind, TableContract
from atlas.issues import Issue, Severity, examples

DATE_PATTERN = r"\d{4}-\d{2}-\d{2}"
INTEGER_PATTERN = r"[+-]?\d+"
INTEGRAL_PATTERN = r"[+-]?\d+(?:\.0+)?"
DECIMAL_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"

_HEADER_LINE = 1


@dataclass(frozen=True)
class RawTable:
    """A CSV file as text: header plus well-formed data rows."""

    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    line_numbers: tuple[int, ...]  # 1-based file line of each row in ``rows``


@dataclass(frozen=True)
class LoadedTable:
    """A table parsed against its contract, plus every contract violation found."""

    name: str
    frame: pd.DataFrame
    issues: tuple[Issue, ...]

    @property
    def ok(self) -> bool:
        return not any(issue.severity is Severity.ERROR for issue in self.issues)


def _error(
    code: str, table: str, message: str, *, count: int = 1, sample: tuple[str, ...] = ()
) -> Issue:
    return Issue(code, Severity.ERROR, table, message, count=count, examples=sample)


def read_csv_strict(path: Path, table: str) -> tuple[RawTable | None, list[Issue]]:
    """Read ``path`` as UTF-8 CSV without any type inference.

    Returns ``(None, issues)`` when the file cannot be used at all (missing,
    undecodable, empty or with an unusable header).
    """
    issues: list[Issue] = []
    if not path.is_file():
        return None, [_error("file_missing", table, f"File not found: {path}")]
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            records = list(csv.reader(handle, strict=True))
    except UnicodeDecodeError as exc:
        return None, [_error("encoding_error", table, f"File is not valid UTF-8: {exc}")]
    except csv.Error as exc:
        return None, [_error("csv_syntax_error", table, f"CSV syntax error: {exc}")]

    if not records:
        return None, [_error("empty_file", table, "File is empty (no header row).")]

    header = tuple(records[0])
    duplicated = sorted({name for name in header if header.count(name) > 1})
    if duplicated:
        issues.append(
            _error(
                "duplicate_column",
                table,
                "Header repeats column names.",
                count=len(duplicated),
                sample=examples(duplicated),
            )
        )
    if any(name == "" for name in header):
        issues.append(_error("blank_column_name", table, "Header contains an empty column name."))
    if issues:
        return None, issues

    rows: list[tuple[str, ...]] = []
    lines: list[int] = []
    bad_lines: list[str] = []
    for offset, record in enumerate(records[1:]):
        line = _HEADER_LINE + 1 + offset
        if len(record) != len(header):
            bad_lines.append(f"line {line}: {len(record)} fields")
            continue
        rows.append(tuple(record))
        lines.append(line)
    if bad_lines:
        issues.append(
            _error(
                "malformed_row",
                table,
                f"Rows do not have exactly {len(header)} fields.",
                count=len(bad_lines),
                sample=examples(bad_lines),
            )
        )
    return RawTable(header=header, rows=tuple(rows), line_numbers=tuple(lines)), issues


def _line_examples(values: pd.Series, mask: pd.Series, lines: pd.Series) -> tuple[str, ...]:
    flagged = mask[mask].index
    return examples(f"line {lines[i]}: {values[i]!r}" for i in flagged)


def _parse_column(
    values: pd.Series, column: Column, table: str, lines: pd.Series
) -> tuple[pd.Series, list[Issue]]:
    """Parse one text column into its typed representation and collect violations."""
    issues: list[Issue] = []
    subject = f"{table}.{column.name}"
    is_null = values == ""
    present = ~is_null

    if not column.nullable and bool(is_null.any()):
        issues.append(
            _error(
                "null_value",
                subject,
                "Required value is empty.",
                count=int(is_null.sum()),
                sample=_line_examples(values, is_null, lines),
            )
        )

    typed: pd.Series
    if column.kind is Kind.DATE:
        wellformed = values.str.fullmatch(DATE_PATTERN)
        parsed = pd.to_datetime(
            values.where(present & wellformed), format="%Y-%m-%d", errors="coerce"
        )
        typed = parsed.astype("datetime64[ns]")
        malformed = present & typed.isna()
    elif column.kind in (Kind.INTEGER, Kind.INTEGRAL):
        pattern = INTEGER_PATTERN if column.kind is Kind.INTEGER else INTEGRAL_PATTERN
        wellformed = values.str.fullmatch(pattern)
        malformed = present & ~wellformed
        digits = values.str.replace(r"\.0+$", "", regex=True)
        # Python int parsing is exact for arbitrarily large counts (no float round-trip).
        as_int = digits.where(present & wellformed).map(int, na_action="ignore")
        typed = as_int.astype("Int64")
    elif column.kind is Kind.DECIMAL:
        wellformed = values.str.fullmatch(DECIMAL_PATTERN)
        malformed = present & ~wellformed
        typed = pd.to_numeric(values.where(present & wellformed), errors="coerce").astype("float64")
    else:
        malformed = present & (values != values.str.strip())
        typed = values.where(present)

    if bool(malformed.any()):
        issues.append(
            _error(
                "malformed_value",
                subject,
                f"Value is not a valid {column.kind.value}.",
                count=int(malformed.sum()),
                sample=_line_examples(values, malformed, lines),
            )
        )

    valid = present & ~malformed
    if column.kind is Kind.STRING:
        if column.allowed is not None:
            invalid = valid & ~values.isin(sorted(column.allowed))
            if bool(invalid.any()):
                issues.append(
                    _error(
                        "invalid_category",
                        subject,
                        f"Value is not one of {sorted(column.allowed)}.",
                        count=int(invalid.sum()),
                        sample=_line_examples(values, invalid, lines),
                    )
                )
        if column.pattern is not None:
            mismatched = valid & ~values.str.fullmatch(column.pattern)
            if bool(mismatched.any()):
                issues.append(
                    _error(
                        "pattern_mismatch",
                        subject,
                        f"Value does not match the pattern {column.pattern!r}.",
                        count=int(mismatched.sum()),
                        sample=_line_examples(values, mismatched, lines),
                    )
                )
    elif column.kind is not Kind.DATE:
        numeric = typed.astype("float64")
        out_of_range = pd.Series(False, index=values.index)
        bounds: list[str] = []
        if column.minimum is not None:
            out_of_range |= valid & (numeric < column.minimum)
            bounds.append(f">= {column.minimum:g}")
        if column.maximum is not None:
            out_of_range |= valid & (numeric > column.maximum)
            bounds.append(f"<= {column.maximum:g}")
        if bool(out_of_range.any()):
            issues.append(
                _error(
                    "out_of_range",
                    subject,
                    f"Value must be {' and '.join(bounds)}.",
                    count=int(out_of_range.sum()),
                    sample=_line_examples(values, out_of_range, lines),
                )
            )

    typed = typed.where(~malformed)
    return typed, issues


def _check_table_rules(frame: pd.DataFrame, contract: TableContract) -> list[Issue]:
    """Row-count, primary-key and calendar rules that apply to the whole table."""
    issues: list[Issue] = []
    name = contract.name
    rows = len(frame)

    if rows == 0:
        issues.append(_error("empty_table", name, "Table has no data rows."))
    elif rows < contract.min_rows:
        issues.append(
            _error(
                "too_few_rows", name, f"Expected at least {contract.min_rows} rows, found {rows}."
            )
        )
    if contract.max_rows is not None and rows > contract.max_rows:
        issues.append(
            _error(
                "too_many_rows", name, f"Expected at most {contract.max_rows} rows, found {rows}."
            )
        )

    key = list(contract.primary_key)
    if key and all(column in frame.columns for column in key):
        complete = frame[key].notna().all(axis=1)
        duplicated = frame[complete].duplicated(subset=key, keep="first")
        if bool(duplicated.any()):
            keys = frame[complete][duplicated][key].astype(str).agg("|".join, axis=1)
            issues.append(
                _error(
                    "duplicate_key",
                    name,
                    f"Primary key ({', '.join(key)}) is not unique.",
                    count=int(duplicated.sum()),
                    sample=examples(keys),
                )
            )

    date_column = contract.date_column
    if contract.window is not None and date_column is not None and date_column in frame.columns:
        start, end = (pd.Timestamp(bound) for bound in contract.window)
        dates = frame[date_column].dropna()
        outside = dates[(dates < start) | (dates > end)]
        if not outside.empty:
            issues.append(
                _error(
                    "date_out_of_window",
                    name,
                    f"Dates must fall within {start.date()} .. {end.date()}.",
                    count=len(outside),
                    sample=examples(d.date() for d in outside.sort_values()),
                )
            )
        if contract.require_every_date:
            expected = pd.date_range(start, end, freq="D")
            missing = expected.difference(pd.DatetimeIndex(dates.unique()))
            if len(missing) > 0:
                issues.append(
                    _error(
                        "missing_dates",
                        name,
                        f"Calendar dates absent within {start.date()} .. {end.date()}.",
                        count=len(missing),
                        sample=examples(d.date() for d in missing),
                    )
                )
    return issues


def parse_table(raw: RawTable, contract: TableContract) -> LoadedTable:
    """Parse a :class:`RawTable` against ``contract``; never raises on bad data."""
    issues: list[Issue] = []
    name = contract.name
    expected = contract.column_names
    header = raw.header

    missing = [column for column in expected if column not in header]
    unexpected = [column for column in header if column not in expected]
    if missing:
        issues.append(
            _error(
                "missing_column",
                name,
                "Required columns are absent.",
                count=len(missing),
                sample=examples(missing),
            )
        )
    if unexpected:
        issues.append(
            _error(
                "unexpected_column",
                name,
                "Columns are not declared in the contract.",
                count=len(unexpected),
                sample=examples(unexpected),
            )
        )
    if not missing and not unexpected and header != expected:
        issues.append(
            Issue(
                "column_order",
                Severity.WARNING,
                name,
                "Columns are present but not in contract order.",
            )
        )

    text = pd.DataFrame(list(raw.rows), columns=list(header), dtype="str")
    lines = pd.Series(raw.line_numbers, index=text.index, dtype="int64")
    typed_columns: dict[str, pd.Series] = {}
    for column in contract.columns:
        if column.name not in text.columns:
            continue
        typed, column_issues = _parse_column(text[column.name], column, name, lines)
        typed_columns[column.name] = typed
        issues.extend(column_issues)

    frame = pd.DataFrame(typed_columns, index=text.index)
    issues.extend(_check_table_rules(frame, contract))

    key = list(contract.primary_key)
    if key and all(column in frame.columns for column in key):
        frame = frame.sort_values(key, kind="mergesort", na_position="last")
    frame = frame.reset_index(drop=True)
    return LoadedTable(name=name, frame=frame, issues=tuple(issues))


def load_table(path: Path, contract: TableContract) -> LoadedTable:
    """Read and parse one CSV file against its contract."""
    raw, read_issues = read_csv_strict(path, contract.name)
    if raw is None:
        empty = pd.DataFrame(columns=list(contract.column_names))
        return LoadedTable(name=contract.name, frame=empty, issues=tuple(read_issues))
    loaded = parse_table(raw, contract)
    return LoadedTable(name=loaded.name, frame=loaded.frame, issues=(*read_issues, *loaded.issues))
