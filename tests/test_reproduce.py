"""Reproduction of the legacy historical summaries, KPIs and model metrics."""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest
from pydantic import ValidationError

from atlas.config import Settings
from atlas.issues import CheckReport, Severity
from atlas.legacy import risk_level
from atlas.loader import LoadedTable
from atlas.reproduce import (
    KPI_MEANINGS,
    LEGACY_OUTPUT_TABLES,
    KpiResult,
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
def register(repo_settings: Settings) -> KnownExceptions:
    return load_exceptions(repo_settings.exceptions_path, "legacy-v0")


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
        (9656.3125, 9656.3125, 2, False),  # committed value is not at the stated precision
        (9656.31, 9656.315, 2, True),
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


def test_compare_table_fails_on_out_of_tolerance_and_null_differences() -> None:
    committed = pd.DataFrame({"k": [1, 2, 3], "x": [1.25, None, 3.0]})
    recomputed = pd.DataFrame({"k": [1, 2, 3], "x": [1.26, 2.0, None]})
    issues = compare_table("t", committed, recomputed, "k", [Rule("x", 2)])
    assert [i.code for i in issues] == ["reproduction_mismatch"]
    assert issues[0].count == 3


def test_compare_table_handles_dates_and_text() -> None:
    committed = pd.DataFrame(
        {"k": ["a", "b"], "d": pd.to_datetime(["2024-01-01", "2024-01-02"]), "t": ["x", "y"]}
    )
    recomputed = committed.assign(d=pd.to_datetime(["2024-01-01", "2024-01-03"]), t=["x", "z"])
    issues = compare_table("t", committed, recomputed, "k", [Rule("d"), Rule("t")])
    assert issues[0].examples == (
        "k=b d: committed Timestamp('2024-01-02 00:00:00'), "
        "recomputed Timestamp('2024-01-03 00:00:00')",
        "k=b t: committed 'y', recomputed 'z'",
    )


def test_kpi_result_renders_nulls_as_json_null() -> None:
    result = KpiResult("first_shortfall_date", pd.NaT, None, None, "m", ok=True)
    assert result.to_dict()["committed"] is None
    assert KpiResult("x", pd.NA, 1, None, "m", ok=False).to_dict()["committed"] is None


@pytest.mark.integration
def test_every_legacy_output_table_is_cross_checked(legacy_reports: list[CheckReport]) -> None:
    checked = by_name(legacy_reports)["reproduction"].facts["legacy_outputs_cross_checked"]
    assert sorted(checked) == sorted(LEGACY_OUTPUT_TABLES)


def _frames(tables: dict[str, LoadedTable]) -> dict[str, pd.DataFrame]:
    return {name: table.frame.copy() for name, table in tables.items()}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("table", "column", "selector", "value"),
    [
        # rounded derived value, off by one unit in the last published place
        ("yearly_workload", "average_daily_requests", ("request_year", 2024), 9444.74),
        # null in the committed table where the recomputation has a value
        ("yearly_workload", "previous_year_requests", ("request_year", 2023), 1),
        ("capacity_risk_alerts", "risk_level", ("days_ahead", 6), "Low"),
        ("capacity_risk_alerts", "capacity_utilization_pct", ("days_ahead", 1), 94.70),
        ("capacity_risk_summary", "average_utilization_pct", ("risk_level", "Medium"), 99.99),
        ("date_dimension", "is_weekend", ("day_of_month", 2), 1),
        ("actual_forecast_series", "actual_requests", ("record_type", "Actual"), 9999),
        # (baseline_forecast also feeds the KPIs and metrics; absolute_error feeds nothing else)
        ("forecast_test_results", "absolute_error", ("model_error", -1138), 1),
        ("future_30_day_forecast", "forecast_week", ("forecast_requests", 9969), "2026-07-04"),
    ],
)
def test_tampered_values_fail_reproduction(
    legacy_tables: dict[str, LoadedTable],
    register: KnownExceptions,
    table: str,
    column: str,
    selector: tuple[str, object],
    value: object,
) -> None:
    frame = legacy_tables[table].frame.copy()
    rows = frame.index[frame[selector[0]] == selector[1]]
    frame.loc[rows[0], column] = value
    report = reproduce_tables(_replace_frame(legacy_tables, table, frame), register)
    assert [(i.code, i.subject) for i in report.errors] == [("reproduction_mismatch", table)]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("column", "value"),
    [("model_mape_pct", 3.93), ("first_shortfall_date", pd.NaT), ("ytd_2026_growth_pct", 13.41)],
)
def test_tampered_rounded_or_null_kpis_fail(
    legacy_tables: dict[str, LoadedTable], column: str, value: object
) -> None:
    frames = _frames(legacy_tables)
    frames["project_kpis"].loc[0, column] = value
    issues, results = reconcile_kpis(frames)
    assert [i.code for i in issues] == ["legacy_kpi_mismatch"]
    assert [r.name for r in results if not r.ok] == [column]


@pytest.mark.integration
def test_model_metric_row_set_is_enforced(legacy_tables: dict[str, LoadedTable]) -> None:
    frames = _frames(legacy_tables)
    empty = KnownExceptions(schema_version=1, snapshot_id="legacy-v0")
    extra = pd.concat(
        [frames["model_metrics"], frames["model_metrics"].iloc[[0]].assign(model_name="Mystery")],
        ignore_index=True,
    )
    issues, _ = reconcile_model_metrics({**frames, "model_metrics": extra}, empty)
    assert "unknown_model_row" in [i.code for i in issues]

    metrics = frames["model_metrics"]
    missing = metrics[metrics["model_name"] != "7-Day Seasonal Baseline"]
    issues, _ = reconcile_model_metrics({**frames, "model_metrics": missing}, empty)
    assert "reproduction_key_mismatch" in [i.code for i in issues]


def _metric_register(row: str, committed: float, recomputed: float) -> KnownExceptions:
    exception = MetricReproductionException(
        id="X",
        check="legacy_metric_reproduction",
        title="t",
        cause="c",
        evidence=(),
        policy="accept",
        expected=MetricExpectation(
            table="model_metrics",
            row=row,
            column="rmse",
            committed=committed,
            recomputed_rounded=recomputed,
            decimals=2,
        ),
    )
    return KnownExceptions(schema_version=1, snapshot_id="legacy-v0", exceptions=(exception,))


@pytest.mark.integration
def test_metric_exception_requires_committed_value_to_match(
    legacy_tables: dict[str, LoadedTable],
) -> None:
    frames = _frames(legacy_tables)
    issues, _ = reconcile_model_metrics(
        frames, _metric_register("Linear Regression Forecast", 676.6, 676.69)
    )
    assert [i.code for i in issues] == ["exception_drift"]


@pytest.mark.integration
def test_metric_exception_matching_nothing_is_reported_stale(
    legacy_tables: dict[str, LoadedTable],
) -> None:
    frames = _frames(legacy_tables)
    issues, _ = reconcile_model_metrics(
        frames,
        _metric_register("Linear Regresion Forecast", 676.7, 676.69),  # typo in row
    )
    codes = [(i.code, i.severity) for i in issues]
    assert ("stale_exception", Severity.WARNING) in codes
    assert ("reproduction_mismatch", Severity.ERROR) in codes


def test_metric_exception_schema_is_restricted() -> None:
    with pytest.raises(ValidationError):
        MetricExpectation.model_validate(
            {
                "table": "project_kpis",
                "row": "r",
                "column": "rmse",
                "committed": 1.0,
                "recomputed_rounded": 1.0,
                "decimals": 2,
            }
        )


def test_reproduction_reports_missing_tables(legacy_tables: dict[str, LoadedTable]) -> None:
    partial = {k: v for k, v in legacy_tables.items() if k != "date_dimension"}
    report = reproduce_tables(partial, KnownExceptions(schema_version=1, snapshot_id="legacy-v0"))
    assert [(i.code, i.examples) for i in report.errors] == [("table_missing", ("date_dimension",))]


def test_capacity_rules_match_the_legacy_tiers() -> None:
    assert [risk_level(u) for u in (94.99, 95.0, 100.0, 100.01, 109.99, 110.0)] == [
        "Low",
        "Medium",
        "Medium",
        "High",
        "High",
        "Critical",
    ]
