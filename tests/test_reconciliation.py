"""The UNASSIGNED residual: exact reconciliation by construction, no invented agency records."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from atlas.contracts import DAILY_BOROUGH_COMPONENTS, UNASSIGNED_COMPONENT
from atlas.errors import DataQualityError
from atlas.loader import LoadedTable, validate_frame
from atlas.reconciliation import COMPONENT_ORDER, build_borough_components
from atlas.validation import (
    KnownExceptions,
    ReconciliationExpectation,
    ReconciliationGapException,
)

DATES = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])


def frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    total = pd.DataFrame({"request_date": DATES, "request_count": [100, 50, 70]})
    segments = pd.DataFrame(
        {
            "request_date": pd.to_datetime(
                ["2024-01-01", "2024-01-01", "2024-01-01", "2024-01-02", "2024-01-03"]
            ),
            "agency": ["NYPD", "HPD", "NYPD", "NYPD", "DOT"],
            "borough": ["BRONX", "BRONX", "QUEENS", "BROOKLYN", "STATEN ISLAND"],
            "request_count": [40, 20, 30, 50, 69],
        }
    )
    return total, segments


def register(**overrides: object) -> KnownExceptions:
    """A register declaring exactly the gap of :func:`frames` (10 + 0 + 1)."""
    expected = {
        "dates_compared": 3,
        "dates_with_gap": 2,
        "dates_reconciled": 1,
        "total_gap": 11,
        "max_gap": 10,
        "max_gap_date": date(2024, 1, 1),
        "share_threshold_pct": 2.0,
        "dates_above_share_threshold": (date(2024, 1, 1),),
    } | overrides
    exception = ReconciliationGapException(
        id="TEST-EX-001",
        check="segment_reconciliation_gap",
        title="test gap",
        cause="synthetic",
        evidence=(),
        policy="accept",
        expected=ReconciliationExpectation.model_validate(expected),
    )
    return KnownExceptions(schema_version=1, snapshot_id="test", exceptions=(exception,))


def test_components_split_each_total_exactly() -> None:
    total, segments = frames()
    result = build_borough_components(total, segments, register())
    frame = result.frame
    assert tuple(frame.columns) == DAILY_BOROUGH_COMPONENTS.column_names
    assert len(frame) == 3 * 6
    assert list(frame["component"][:6]) == list(COMPONENT_ORDER)
    day1 = frame[frame["request_date"] == "2024-01-01"].set_index("component")["request_count"]
    assert day1.to_dict() == {
        "BRONX": 60,
        "BROOKLYN": 0,
        "MANHATTAN": 0,
        "QUEENS": 30,
        "STATEN ISLAND": 0,
        UNASSIGNED_COMPONENT: 10,
    }
    sums = frame.groupby("request_date")["request_count"].sum()
    assert list(sums) == [100, 50, 70]
    assert list(result.residual()) == [10, 0, 1]
    assert result.residual().name == "unassigned_residual"
    assert set(frame["component_kind"]) == {"borough_segment", "unassigned_residual"}
    assert list(frame["iso_weekday"][::6]) == [1, 2, 3]  # 2024-01-01 is a Monday
    assert validate_frame(frame, DAILY_BOROUGH_COMPONENTS).ok


def test_residual_has_no_agency_dimension() -> None:
    total, segments = frames()
    frame = build_borough_components(total, segments, register()).frame
    assert "agency" not in frame.columns
    residual_rows = frame[frame["component"] == UNASSIGNED_COMPONENT]
    assert len(residual_rows) == 3  # one aggregate row per date, nothing finer
    assert (residual_rows["component_kind"] == "unassigned_residual").all()
    assert len(segments) == 5  # input untouched


def test_undeclared_gap_is_refused() -> None:
    total, segments = frames()
    empty = KnownExceptions(schema_version=1, snapshot_id="test")
    with pytest.raises(DataQualityError, match="unexplained_reconciliation_gap") as raised:
        build_borough_components(total, segments, empty)
    assert raised.value.issues[0].code == "unexplained_reconciliation_gap"


def test_drift_from_declared_gap_is_refused() -> None:
    total, segments = frames()
    with pytest.raises(DataQualityError, match="exception_drift"):
        build_borough_components(total, segments, register(total_gap=12))


def test_segments_exceeding_total_is_refused() -> None:
    total, segments = frames()
    segments.loc[4, "request_count"] = 71  # 2024-01-03: segments 71 > total 70
    with pytest.raises(DataQualityError, match="segments_exceed_total"):
        build_borough_components(total, segments, register())


def test_reconciled_data_needs_no_exception() -> None:
    total, segments = frames()
    total["request_count"] = [90, 50, 69]
    empty = KnownExceptions(schema_version=1, snapshot_id="test")
    result = build_borough_components(total, segments, empty)
    assert result.residual().sum() == 0


def test_components_contract_rejects_negative_or_unknown_components() -> None:
    total, segments = frames()
    frame = build_borough_components(total, segments, register()).frame.copy()
    frame.loc[5, "request_count"] = -1
    frame.loc[0, "component"] = "NEWARK"
    codes = {issue.code for issue in validate_frame(frame, DAILY_BOROUGH_COMPONENTS).issues}
    assert codes == {"out_of_range", "invalid_category"}


@pytest.mark.integration
def test_legacy_residual_matches_declared_exception(
    legacy_tables: dict[str, LoadedTable], repo_settings: object
) -> None:
    from atlas.validation import load_exceptions

    exceptions = load_exceptions(repo_settings.exceptions_path, "legacy-v0")  # type: ignore[attr-defined]
    total = legacy_tables["daily_total"].frame
    result = build_borough_components(total, legacy_tables["daily_segments"].frame, exceptions)
    residual = result.residual()
    assert len(result.frame) == 1277 * 6
    assert int(residual.sum()) == 13_279 == exceptions.reconciliation.expected.total_gap  # type: ignore[union-attr]
    assert int(residual.max()) == 2_434
    assert residual.idxmax() == pd.Timestamp("2023-09-15")
    assert int((residual == 0).sum()) == 4
    assert (residual >= 0).all()
    sums = result.frame.groupby("request_date")["request_count"].sum()
    expected = total.set_index("request_date")["request_count"].astype("int64")
    assert sums.to_dict() == expected.to_dict()
    assert result.issues[0].exception_id == "LEGACY-V0-EX-001"
