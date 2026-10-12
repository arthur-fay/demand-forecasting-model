"""Reproduction of the legacy historical summaries, KPIs and model metrics."""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from atlas.issues import CheckReport, Severity
from atlas.loader import LoadedTable
from atlas.reproduce import (
    KPI_MEANINGS,
    Rule,
    compare_table,
    reconcile_kpis,
    reconcile_model_metrics,
    reproduce_tables,
    within_rounding,
)
from atlas.validation import (
    KnownExceptions,
    MetricExpectation,
    MetricReproductionException,
    load_exceptions,
)


def by_name(reports: list[CheckReport]) -> dict[str, CheckReport]:
    return {report.name: report for report in reports}


@pytest.fixture(scope="module")
def register(repo_settings: object) -> KnownExceptions:
    return load_exceptions(
        repo_settings.exceptions_path,  # type: ignore[attr-defined]
        "legacy-v0",
    )


# --- End-to-end on the committed snapshot -----------------------------------------------


@pytest.mark.integration
def test_every_stage_passes_on_the_committed_snapshot(legacy_reports: list[CheckReport]) -> None:
    assert [r.name for r in legacy_reports] == ["integrity", "validation", "reproduction"]
    assert all(r.ok for r in legacy_reports), [r.errors for r in legacy_reports]
    assert all(not r.warnings for r in legacy_reports)


@pytest.mark.integration
def test_known_historical_totals(legacy_reports: list[CheckReport]) -> None:
    totals = by_name(legacy_reports)["reproduction"].facts["historical_totals"]
    assert totals["days"] == 1277
    assert (totals["first_date"], totals["last_date"]) == ("2023-01-01", "2026-06-30")
    assert totals["total_requests"] == 12_331_111
    assert round(totals["average_daily_requests"], 2) == 9656.31
    assert (totals["highest_daily_requests"], totals["highest_date"]) == (22_806, "2026-02-24")
    assert (totals["lowest_daily_requests"], totals["lowest_date"]) == (5_596, "2023-12-25")


@pytest.mark.integration
def test_all_six_historical_tables_reproduce(legacy_reports: list[CheckReport]) -> None:
    reproduced = by_name(legacy_reports)["reproduction"].facts["tables_reproduced"]
    assert sorted(reproduced) == sorted(
        [
            "yearly_workload",
            "ytd_workload",
            "monthly_workload",
            "weekday_workload",
            "borough_workload",
            "agency_workload",
        ]
    )


@pytest.mark.integration
def test_city_wide_versus_borough_filtered_difference_is_measured(
    legacy_reports: list[CheckReport],
) -> None:
    reconciliation = by_name(legacy_reports)["validation"].facts["reconciliation"]
    assert reconciliation["total_requests"] == 12_331_111
    assert reconciliation["segment_requests"] == 12_317_832
    assert reconciliation["total_gap"] == 13_279
    assert reconciliation["dates_with_gap"] == 1273
    assert reconciliation["dates_reconciled"] == 4
    assert reconciliation["dates_segments_exceed_total"] == 0
    assert (reconciliation["max_gap"], reconciliation["max_gap_date"]) == (2434, "2023-09-15")


@pytest.mark.integration
def test_documented_exceptions_are_accepted_explicitly(legacy_reports: list[CheckReport]) -> None:
    accepted = {
        issue.exception_id
        for report in legacy_reports
        for issue in report.issues
        if issue.code == "accepted_known_exception"
    }
    assert accepted == {"LEGACY-V0-EX-001", "LEGACY-V0-EX-002"}
    infos = [i for r in legacy_reports for i in r.issues]
    assert all(issue.severity is Severity.INFO for issue in infos)


@pytest.mark.integration
def test_legacy_kpis_reproduce_with_explicit_meaning(legacy_reports: list[CheckReport]) -> None:
    reproduction = by_name(legacy_reports)["reproduction"]
    kpis = {k["name"]: k for k in reproduction.facts["kpis"]}
    assert set(kpis) == set(KPI_MEANINGS)
    assert all(k["ok"] for k in kpis.values())
    assert kpis["planning_capacity_threshold"]["committed"] == 11_609
    assert (
        "NOT measured operational service capacity"
        in kpis["planning_capacity_threshold"]["meaning"]
    )
    assert kpis["model_mape_pct"]["committed"] == pytest.approx(4.93)
    assert "h=1" in kpis["model_mape_pct"]["meaning"]
    assert "NOT the accuracy of the published 30-day forecast" in kpis["model_mape_pct"]["meaning"]
    assert kpis["advance_warning_days"]["recomputed"] == 13
    assert reproduction.facts["demand_percentile_threshold_exact"] == pytest.approx(11_608.75)


# --- Detection of discrepancies -------------------------------------------------------


def _replace_frame(
    tables: dict[str, LoadedTable], name: str, frame: pd.DataFrame
) -> dict[str, LoadedTable]:
    copy = dict(tables)
    copy[name] = dataclasses.replace(tables[name], frame=frame)
    return copy


@pytest.mark.integration
def test_altered_derived_value_is_detected(
    legacy_tables: dict[str, LoadedTable], register: KnownExceptions
) -> None:
    yearly = legacy_tables["yearly_workload"].frame.copy()
    yearly.loc[yearly["request_year"] == 2024, "total_requests"] += 1
    report = reproduce_tables(_replace_frame(legacy_tables, "yearly_workload", yearly), register)
    mismatch = next(i for i in report.errors if i.subject == "yearly_workload")
    assert mismatch.code == "reproduction_mismatch"
    assert "yearly_workload" not in report.facts["tables_reproduced"]


@pytest.mark.integration
def test_altered_kpi_is_detected(legacy_tables: dict[str, LoadedTable]) -> None:
    kpis = legacy_tables["project_kpis"].frame.copy()
    kpis.loc[0, "capacity_shortfall_days"] = 4
    frames = {name: t.frame for name, t in legacy_tables.items()}
    frames["project_kpis"] = kpis
    issues, results = reconcile_kpis(frames)
    assert [i.code for i in issues] == ["legacy_kpi_mismatch"]
    assert [r.name for r in results if not r.ok] == ["capacity_shortfall_days"]


@pytest.mark.integration
def test_metric_exception_policy(legacy_tables: dict[str, LoadedTable]) -> None:
    frames = {name: t.frame for name, t in legacy_tables.items()}
    empty = KnownExceptions(schema_version=1, snapshot_id="legacy-v0")
    issues, _ = reconcile_model_metrics(frames, empty)
    assert [(i.code, i.severity) for i in issues] == [("reproduction_mismatch", Severity.ERROR)]

    def register_for(column: str, recomputed: float) -> KnownExceptions:
        exception = MetricReproductionException(
            id="X",
            check="legacy_metric_reproduction",
            title="t",
            cause="c",
            evidence=(),
            policy="accept",
            expected=MetricExpectation(
                table="model_metrics",
                row="Linear Regression Forecast",
                column=column,
                committed=676.7 if column == "rmse" else 514.3,
                recomputed_rounded=recomputed,
                decimals=2,
            ),
        )
        return KnownExceptions(schema_version=1, snapshot_id="legacy-v0", exceptions=(exception,))

    drift, _ = reconcile_model_metrics(frames, register_for("rmse", 676.5))
    assert [i.code for i in drift] == ["exception_drift"]
    stale, _ = reconcile_model_metrics(frames, register_for("mae", 514.3))
    codes = [(i.code, i.severity) for i in stale]
    assert ("stale_exception", Severity.WARNING) in codes
    assert ("reproduction_mismatch", Severity.ERROR) in codes  # rmse no longer covered


# --- Comparison primitives ------------------------------------------------------------


@pytest.mark.parametrize(
    ("committed", "exact", "decimals", "expected"),
    [
        (9656.31, 9656.3125, 2, True),
        (2.68, 2.675, 2, True),  # half-up
        (2.67, 2.675, 2, True),  # half-even / binary representation
        (676.7, 676.6857, 2, False),
        (0.5787, 0.578741, 4, True),
        (40.1, 40.1036, 2, True),
        (40.11, 40.1036, 2, False),
    ],
)
def test_within_rounding(committed: float, exact: float, decimals: int, expected: bool) -> None:
    assert within_rounding(committed, exact, decimals) is expected


def test_compare_table_reports_missing_keys_and_value_mismatches() -> None:
    committed = pd.DataFrame({"k": [1, 2, 3], "n": [10, 20, 30], "x": [1.25, 2.0, None]})
    recomputed = pd.DataFrame({"k": [1, 2, 4], "n": [10, 21, 40], "x": [1.254, 2.0, None]})
    issues = compare_table("t", committed, recomputed, "k", [Rule("n"), Rule("x", 2)])
    by_code = {issue.code: issue for issue in issues}
    assert by_code["reproduction_key_mismatch"].examples == ("3", "4")
    assert by_code["reproduction_mismatch"].count == 1
    assert by_code["reproduction_mismatch"].examples == ("k=2 n: committed 20, recomputed 21",)
