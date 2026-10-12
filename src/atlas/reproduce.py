"""Reproduce the legacy historical summaries and headline KPIs from the base tables.

Comparison rules:

* integer and text columns must match exactly;
* columns the legacy pipeline rounded to ``d`` decimals pass when the committed value
  is a valid rounding of the exact recomputed value, i.e.
  ``|committed - exact| <= 0.5 * 10**-d`` (plus floating-point slack). This accepts
  either half-up (SQLite) or half-even (Python/NumPy) rounding without hiding real
  differences.

Reproducing a legacy number proves only that it was *computed* as described. What
each number *means* is recorded next to it; see ``docs/LEGACY_LIMITATIONS.md``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from atlas import legacy, summaries
from atlas.issues import CheckReport, Issue, Severity, examples
from atlas.loader import LoadedTable
from atlas.validation import KnownExceptions, MetricReproductionException

_FLOAT_SLACK = 1e-9
REGRESSION_MODEL = "Linear Regression Forecast"
BASELINE_MODEL = "7-Day Seasonal Baseline"


@dataclass(frozen=True)
class Rule:
    """Comparison rule for one column: exact (``decimals is None``) or rounded."""

    column: str
    decimals: int | None = None


def _exact(*columns: str) -> tuple[Rule, ...]:
    return tuple(Rule(column) for column in columns)


def _rounded(decimals: int, *columns: str) -> tuple[Rule, ...]:
    return tuple(Rule(column, decimals) for column in columns)


def _is_null(value: object) -> bool:
    return (
        value is None
        or value is pd.NA
        or value is pd.NaT
        or (isinstance(value, float) and math.isnan(value))
    )


def _key(value: object) -> str:
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    return str(value)


def _to_float(value: object) -> float:
    """Convert a numeric scalar (Python or NumPy) to float; reject anything else."""
    if isinstance(value, bool | np.bool_):
        raise TypeError(f"expected a number, got a boolean: {value!r}")
    if isinstance(value, int | float | np.integer | np.floating):
        return float(value)
    raise TypeError(f"expected a number, got {type(value).__name__}: {value!r}")


def within_rounding(committed: float, exact: float, decimals: int) -> bool:
    """True when ``committed`` is a valid rounding of ``exact`` to ``decimals`` places.

    The committed value must itself carry at most ``decimals`` decimal places, and must
    lie within half a unit in the last place of the exact value.
    """
    at_precision = math.isclose(committed, round(committed, decimals), abs_tol=_FLOAT_SLACK)
    return at_precision and abs(committed - exact) <= 0.5 * 10.0**-decimals + _FLOAT_SLACK


def _as_date(value: object) -> date | None:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _values_match(committed: object, recomputed: object, rule: Rule) -> bool:
    if _is_null(committed) or _is_null(recomputed):
        return _is_null(committed) and _is_null(recomputed)
    if rule.decimals is not None:
        return within_rounding(_to_float(committed), _to_float(recomputed), rule.decimals)
    if _as_date(committed) is not None or _as_date(recomputed) is not None:
        return _as_date(committed) == _as_date(recomputed)
    if isinstance(committed, str) or isinstance(recomputed, str):
        return str(committed) == str(recomputed)
    return _to_float(committed) == _to_float(recomputed)


def _columns_by_key(
    frame: pd.DataFrame, key: str, rules: Sequence[Rule]
) -> dict[str, dict[str, object]]:
    """Map each compared column to ``{key: value}`` using native Python values.

    Column-wise access keeps each column's own type (``iterrows`` would upcast a row
    mixing integers and floats to float).
    """
    keys = [_key(value) for value in frame[key].tolist()]
    columns = {key: dict.fromkeys(keys)}
    for rule in rules:
        columns[rule.column] = dict(zip(keys, frame[rule.column].tolist(), strict=True))
    return columns


def compare_table(
    name: str,
    committed: pd.DataFrame,
    recomputed: pd.DataFrame,
    key: str,
    rules: Sequence[Rule],
) -> list[Issue]:
    """Compare a committed table with its recomputation, keyed by ``key``."""
    issues: list[Issue] = []
    left = _columns_by_key(committed, key, rules)
    right = _columns_by_key(recomputed, key, rules)

    left_keys, right_keys = set(left[key]), set(right[key])
    only_committed = sorted(left_keys - right_keys)
    only_recomputed = sorted(right_keys - left_keys)
    if only_committed or only_recomputed:
        issues.append(
            Issue(
                "reproduction_key_mismatch",
                Severity.ERROR,
                name,
                f"Keys differ: {len(only_committed)} only in the committed table, "
                f"{len(only_recomputed)} only in the recomputation.",
                count=len(only_committed) + len(only_recomputed),
                examples=examples([*only_committed, *only_recomputed]),
            )
        )

    mismatches: list[str] = []
    for k in sorted(left_keys & right_keys):
        for rule in rules:
            committed_value = left[rule.column][k]
            recomputed_value = right[rule.column][k]
            if not _values_match(committed_value, recomputed_value, rule):
                mismatches.append(
                    f"{key}={k} {rule.column}: committed {committed_value!r}, "
                    f"recomputed {recomputed_value!r}"
                )
    if mismatches:
        issues.append(
            Issue(
                "reproduction_mismatch",
                Severity.ERROR,
                name,
                "Committed values differ from the recomputation.",
                count=len(mismatches),
                examples=examples(mismatches),
            )
        )
    return issues


_WORKLOAD = (
    *_exact("total_requests", "highest_daily_requests", "lowest_daily_requests"),
    *_rounded(2, "average_daily_requests"),
)

HISTORICAL_TABLES: dict[
    str, tuple[str, str, Callable[[Mapping[str, pd.DataFrame]], pd.DataFrame], tuple[Rule, ...]]
] = {
    # table: (source table, key, recompute function, rules)
    "yearly_workload": (
        "daily_total",
        "request_year",
        lambda t: summaries.yearly_workload(t["daily_total"]),
        (
            *_WORKLOAD,
            *_exact("days_recorded", "previous_year_requests"),
            *_rounded(2, "year_over_year_growth_pct"),
        ),
    ),
    "ytd_workload": (
        "daily_total",
        "request_year",
        lambda t: summaries.ytd_workload(t["daily_total"], years=(2025, 2026), through=(6, 30)),
        (
            *_WORKLOAD,
            *_exact("days_recorded", "previous_ytd_requests"),
            *_rounded(2, "ytd_growth_pct"),
        ),
    ),
    "monthly_workload": (
        "daily_total",
        "month_start",
        lambda t: summaries.monthly_workload(t["daily_total"]),
        (
            *_exact("request_year", "request_month", "days_recorded"),
            *_WORKLOAD,
            *_exact("previous_month_requests", "previous_year_month_requests"),
            *_rounded(2, "month_over_month_growth_pct", "year_over_year_growth_pct"),
        ),
    ),
    "weekday_workload": (
        "daily_total",
        "weekday_number",
        lambda t: summaries.weekday_workload(t["daily_total"]),
        (
            *_exact("weekday_name", "days_recorded", "average_workload_rank"),
            *_WORKLOAD,
            *_rounded(2, "workload_share_pct"),
        ),
    ),
    "borough_workload": (
        "daily_segments",
        "borough",
        lambda t: summaries.segment_workload(t["daily_segments"], by="borough"),
        (
            *_WORKLOAD,
            *_exact("days_recorded", "workload_rank"),
            *_rounded(2, "workload_share_pct"),
        ),
    ),
    "agency_workload": (
        "daily_segments",
        "agency",
        lambda t: summaries.segment_workload(t["daily_segments"], by="agency"),
        (
            *_WORKLOAD,
            *_exact("active_days", "workload_rank"),
            *_rounded(2, "workload_share_pct"),
        ),
    ),
}


def _legacy_threshold(frames: Mapping[str, pd.DataFrame]) -> int:
    # Python round() as in the legacy notebook (int(round(q))); half-even, but q ends in .75.
    return round(summaries.demand_percentile_threshold(frames["daily_total"], 180, 0.75))


def _recomputed_alerts(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    return legacy.capacity_alerts(
        frames["future_30_day_forecast"],
        _legacy_threshold(frames),
        summaries.last_observed_date(frames["daily_total"]),
    )


def _calendar_bounds(frames: Mapping[str, pd.DataFrame]) -> tuple[date, date]:
    first = summaries.daily_counts(frames["daily_total"]).index.min()
    last = frames["future_30_day_forecast"]["request_date"].max()
    return pd.Timestamp(first).date(), pd.Timestamp(last).date()


# Legacy output tables: every column the notebook derived arithmetically is re-derived from
# the base data (and, where unavoidable, from the exported model predictions).
LEGACY_OUTPUT_TABLES: dict[
    str, tuple[str, Callable[[Mapping[str, pd.DataFrame]], pd.DataFrame], tuple[Rule, ...]]
] = {
    # table: (key, recompute function, rules)
    "forecast_test_results": (
        "request_date",
        lambda t: legacy.holdout_results(t["daily_total"], t["forecast_test_results"]),
        (
            *_exact("actual_requests", "baseline_forecast", "model_error", "absolute_error"),
            *_rounded(2, "absolute_percentage_error"),
        ),
    ),
    "future_30_day_forecast": (
        "request_date",
        lambda t: legacy.forecast_calendar(t["future_30_day_forecast"]),
        _exact("weekday_name", "forecast_week", "forecast_month"),
    ),
    "capacity_risk_alerts": (
        "request_date",
        _recomputed_alerts,
        (
            *_exact(
                "weekday_name",
                "forecast_requests",
                "forecast_week",
                "forecast_month",
                "days_ahead",
                "planning_capacity",
                "capacity_gap_requests",
                "available_capacity",
                "risk_level",
                "capacity_shortfall_flag",
                "recommended_action",
            ),
            *_rounded(2, "capacity_utilization_pct"),
        ),
    ),
    "capacity_risk_summary": (
        "risk_level",
        lambda t: legacy.capacity_summary(_recomputed_alerts(t)),
        (
            *_exact("forecast_days", "total_forecast_requests", "total_capacity_gap"),
            *_rounded(2, "average_utilization_pct"),
        ),
    ),
    "date_dimension": (
        "date",
        lambda t: legacy.date_dimension(*_calendar_bounds(t)),
        _exact(
            "year",
            "quarter",
            "month_number",
            "month_name",
            "month_short",
            "year_month",
            "year_month_sort",
            "day_name",
            "weekday_number",
            "day_of_month",
            "week_of_year",
            "week_start",
            "is_weekend",
        ),
    ),
    "actual_forecast_series": (
        "date",
        lambda t: legacy.actual_forecast_series(t["daily_total"], t["future_30_day_forecast"]),
        _exact("actual_requests", "forecast_requests", "record_type"),
    ),
}

REQUIRED_TABLES = frozenset(
    {
        "daily_total",
        "daily_segments",
        "project_kpis",
        "model_metrics",
        *HISTORICAL_TABLES,
        *LEGACY_OUTPUT_TABLES,
    }
)


# --- Legacy KPIs ----------------------------------------------------------------------


@dataclass(frozen=True)
class KpiResult:
    name: str
    committed: object
    recomputed: object
    decimals: int | None
    meaning: str
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        def render(value: object) -> object:
            if _is_null(value):
                return None
            if isinstance(value, (date, pd.Timestamp)):
                return value.isoformat()
            if isinstance(value, (np.integer,)):
                return int(value)
            if isinstance(value, (np.floating,)):
                return float(value)
            return value

        return {
            "name": self.name,
            "committed": render(self.committed),
            "recomputed": render(self.recomputed),
            "decimals": self.decimals,
            "ok": self.ok,
            "meaning": self.meaning,
        }


KPI_MEANINGS: dict[str, str] = {
    "historical_total_requests": "Sum of city-wide daily 311 requests, 2023-01-01 to 2026-06-30.",
    "historical_average_daily_requests": "Mean city-wide requests per calendar day.",
    "ytd_2026_growth_pct": "Jan 1-Jun 30, 2026 total versus the same period of 2025.",
    "model_mape_pct": (
        "Legacy holdout MAPE at horizon h=1 using observed lag features (single 90-day block). "
        "NOT the accuracy of the published 30-day forecast."
    ),
    "baseline_mape_pct": (
        "Weekly seasonal-naive y(t-7) MAPE on the same block; it uses less recent information "
        "than the model, so the comparison is not like-for-like."
    ),
    "mape_improvement_pct": (
        "Relative reduction of MAPE versus that baseline, not a gain in accuracy points."
    ),
    "forecast_30_day_requests": (
        "Sum of the legacy recursive 30-day point forecast, which was never evaluated beyond h=1."
    ),
    "forecast_average_daily_requests": "Mean of the legacy 30-day point forecast.",
    "planning_capacity_threshold": (
        "75th percentile of the last 180 days of incoming requests. A demand percentile, NOT "
        "measured operational service capacity."
    ),
    "capacity_shortfall_days": (
        "Forecast days above that demand percentile; the excesses are smaller than the model's "
        "own h=1 error."
    ),
    "total_forecast_capacity_gap": (
        "Sum of positive point-forecast excesses over the percentile; ignores backlog carry-over "
        "and forecast uncertainty."
    ),
    "maximum_forecast_utilization_pct": "Largest forecast divided by the demand percentile.",
    "first_shortfall_date": "First forecast day above the demand percentile.",
    "advance_warning_days": (
        "Days from the data cutoff (2026-06-30) to the first flagged day; a horizon index, not a "
        "validated lead time."
    ),
}

_KPI_DECIMALS: dict[str, int | None] = {
    "historical_total_requests": None,
    "historical_average_daily_requests": 2,
    "ytd_2026_growth_pct": 2,
    "model_mape_pct": 2,
    "baseline_mape_pct": 2,
    "mape_improvement_pct": 2,
    "forecast_30_day_requests": None,
    "forecast_average_daily_requests": 2,
    "planning_capacity_threshold": None,
    "capacity_shortfall_days": None,
    "total_forecast_capacity_gap": None,
    "maximum_forecast_utilization_pct": 2,
    "first_shortfall_date": None,
    "advance_warning_days": None,
}


def _mape(actual: pd.Series, predicted: pd.Series) -> float:
    actual_f = actual.astype("float64")
    return float((np.abs(actual_f - predicted.astype("float64")) / actual_f).mean() * 100.0)


def holdout_metrics(test_results: pd.DataFrame, prediction_column: str) -> dict[str, float]:
    """MAE, RMSE, MAPE and R^2 of the exported (integer-rounded) holdout predictions."""
    actual = test_results["actual_requests"].astype("float64")
    predicted = test_results[prediction_column].astype("float64")
    errors = actual - predicted
    total_ss = float(((actual - actual.mean()) ** 2).sum())
    return {
        "mae": float(errors.abs().mean()),
        "rmse": float(np.sqrt((errors**2).mean())),
        "mape_pct": _mape(actual, predicted),
        "r_squared": 1.0 - float((errors**2).sum()) / total_ss if total_ss else float("nan"),
    }


def recompute_kpis(frames: Mapping[str, pd.DataFrame]) -> dict[str, object]:
    """Recompute every legacy headline KPI from the committed tables."""
    daily_total = frames["daily_total"]
    totals = summaries.historical_totals(daily_total)
    ytd = summaries.ytd_workload(daily_total).set_index("request_year")
    tests = frames["forecast_test_results"]
    model = holdout_metrics(tests, "model_forecast")
    baseline = holdout_metrics(tests, "baseline_forecast")
    forecast = frames["future_30_day_forecast"].sort_values("request_date")
    values = forecast["forecast_requests"].astype("int64")
    threshold = _legacy_threshold(frames)
    above = forecast[values > threshold]
    first: date | None = above["request_date"].iloc[0].date() if not above.empty else None
    last_observed = summaries.last_observed_date(daily_total)
    return {
        "historical_total_requests": totals["total_requests"],
        "historical_average_daily_requests": totals["average_daily_requests"],
        "ytd_2026_growth_pct": _to_float(ytd.loc[2026, "ytd_growth_pct"]),
        "model_mape_pct": model["mape_pct"],
        "baseline_mape_pct": baseline["mape_pct"],
        "mape_improvement_pct": (baseline["mape_pct"] - model["mape_pct"])
        / baseline["mape_pct"]
        * 100.0,
        "forecast_30_day_requests": int(values.sum()),
        "forecast_average_daily_requests": float(values.mean()),
        "planning_capacity_threshold": threshold,
        "capacity_shortfall_days": len(above),
        "total_forecast_capacity_gap": int((values - threshold).clip(lower=0).sum()),
        "maximum_forecast_utilization_pct": float(values.max() * 100.0 / threshold),
        "first_shortfall_date": first,
        "advance_warning_days": (first - last_observed).days if first is not None else None,
    }


def _kpi_matches(committed: object, recomputed: object, decimals: int | None) -> bool:
    if _is_null(committed) or _is_null(recomputed):
        return _is_null(committed) and _is_null(recomputed)
    if isinstance(committed, pd.Timestamp):
        committed = committed.date()
    if isinstance(committed, date) or isinstance(recomputed, date):
        return committed == recomputed
    if decimals is not None:
        return within_rounding(_to_float(committed), _to_float(recomputed), decimals)
    return _to_float(committed) == _to_float(recomputed)


def reconcile_kpis(frames: Mapping[str, pd.DataFrame]) -> tuple[list[Issue], list[KpiResult]]:
    committed_row = frames["project_kpis"].iloc[0]
    recomputed = recompute_kpis(frames)
    results: list[KpiResult] = []
    for name, decimals in _KPI_DECIMALS.items():
        committed = committed_row[name]
        if isinstance(committed, pd.Timestamp):
            committed = committed.date()
        results.append(
            KpiResult(
                name=name,
                committed=committed,
                recomputed=recomputed[name],
                decimals=decimals,
                meaning=KPI_MEANINGS[name],
                ok=_kpi_matches(committed, recomputed[name], decimals),
            )
        )
    failures = [r for r in results if not r.ok]
    issues: list[Issue] = []
    if failures:
        issues.append(
            Issue(
                "legacy_kpi_mismatch",
                Severity.ERROR,
                "project_kpis",
                "Published KPI values do not follow from the committed tables.",
                count=len(failures),
                examples=examples(
                    f"{r.name}: committed {r.committed!r}, recomputed {r.recomputed!r}"
                    for r in failures
                ),
            )
        )
    return issues, results


# --- Legacy model metrics -------------------------------------------------------------

_METRIC_DECIMALS = {"mae": 2, "rmse": 2, "mape_pct": 2, "r_squared": 4, "mape_improvement_pct": 2}


def _metric_exception(
    register: KnownExceptions, row: str, column: str
) -> MetricReproductionException | None:
    for exception in register.metric_exceptions:
        expected = exception.expected
        if expected.table == "model_metrics" and expected.row == row and expected.column == column:
            return exception
    return None


def reconcile_model_metrics(
    frames: Mapping[str, pd.DataFrame], register: KnownExceptions
) -> tuple[list[Issue], list[dict[str, Any]]]:
    tests = frames["forecast_test_results"]
    recomputed = {
        REGRESSION_MODEL: holdout_metrics(tests, "model_forecast"),
        BASELINE_MODEL: holdout_metrics(tests, "baseline_forecast"),
    }
    model_mape = recomputed[REGRESSION_MODEL]["mape_pct"]
    baseline_mape = recomputed[BASELINE_MODEL]["mape_pct"]
    recomputed[REGRESSION_MODEL]["mape_improvement_pct"] = (
        (baseline_mape - model_mape) / baseline_mape * 100.0
    )
    recomputed[BASELINE_MODEL]["mape_improvement_pct"] = 0.0

    issues: list[Issue] = []
    results: list[dict[str, Any]] = []
    matched: set[str] = set()
    committed = frames["model_metrics"].set_index("model_name")
    unknown = sorted(set(committed.index) - set(recomputed))
    if unknown:
        issues.append(
            Issue(
                "unknown_model_row",
                Severity.ERROR,
                "model_metrics",
                "Rows cannot be reproduced from forecast_test_results.",
                count=len(unknown),
                examples=examples(unknown),
            )
        )
    for model_name, metrics in recomputed.items():
        if model_name not in committed.index:
            issues.append(
                Issue(
                    "reproduction_key_mismatch",
                    Severity.ERROR,
                    "model_metrics",
                    f"Expected row {model_name!r} is missing.",
                )
            )
            continue
        for column, decimals in _METRIC_DECIMALS.items():
            committed_value = _to_float(committed.at[model_name, column])
            exact = metrics[column]
            ok = within_rounding(committed_value, exact, decimals)
            status = "reproduced" if ok else "mismatch"
            exception = _metric_exception(register, model_name, column)
            if exception is not None:
                matched.add(exception.id)
                expected = exception.expected
                declared_match = math.isclose(
                    expected.committed, committed_value, abs_tol=_FLOAT_SLACK
                ) and math.isclose(
                    expected.recomputed_rounded,
                    round(exact, expected.decimals),
                    abs_tol=_FLOAT_SLACK,
                )
                if ok:
                    issues.append(
                        Issue(
                            "stale_exception",
                            Severity.WARNING,
                            "model_metrics",
                            f"{model_name} {column} now reproduces; the exception is stale.",
                            exception_id=exception.id,
                        )
                    )
                elif declared_match:
                    status = "accepted_known_exception"
                    issues.append(
                        Issue(
                            "accepted_known_exception",
                            Severity.INFO,
                            "model_metrics",
                            f"{model_name} {column}: committed {committed_value}, recomputed "
                            f"{exact:.4f}. Accepted: {exception.title}",
                            exception_id=exception.id,
                        )
                    )
                else:
                    status = "exception_drift"
                    issues.append(
                        Issue(
                            "exception_drift",
                            Severity.ERROR,
                            "model_metrics",
                            f"{model_name} {column}: committed {committed_value}, recomputed "
                            f"{exact:.4f} no longer match the declared exception.",
                            exception_id=exception.id,
                        )
                    )
            elif not ok:
                issues.append(
                    Issue(
                        "reproduction_mismatch",
                        Severity.ERROR,
                        "model_metrics",
                        f"{model_name} {column}: committed {committed_value}, "
                        f"recomputed {exact:.4f}.",
                    )
                )
            results.append(
                {
                    "model": model_name,
                    "metric": column,
                    "committed": committed_value,
                    "recomputed": round(exact, 6),
                    "status": status,
                }
            )
    for exception in register.metric_exceptions:
        if exception.id not in matched:
            issues.append(
                Issue(
                    "stale_exception",
                    Severity.WARNING,
                    "model_metrics",
                    f"Declared exception for row {exception.expected.row!r}, column "
                    f"{exception.expected.column!r} matches no reproduced metric.",
                    exception_id=exception.id,
                )
            )
    return issues, results


# --- Stage entry point ----------------------------------------------------------------


def reproduce_tables(tables: Mapping[str, LoadedTable], register: KnownExceptions) -> CheckReport:
    """Recompute every derived summary and legacy KPI and compare with the committed values."""
    missing = sorted(REQUIRED_TABLES - set(tables))
    if missing:
        return CheckReport(
            name="reproduction",
            issues=(
                Issue(
                    "table_missing",
                    Severity.ERROR,
                    "reproduction",
                    "Tables required for reproduction were not loaded.",
                    count=len(missing),
                    examples=examples(missing),
                ),
            ),
        )
    frames = {name: table.frame for name, table in tables.items()}
    issues: list[Issue] = []
    reproduced: list[str] = []
    for name, (_source, key, recompute, rules) in HISTORICAL_TABLES.items():
        table_issues = compare_table(name, frames[name], recompute(frames), key, rules)
        issues.extend(table_issues)
        if not table_issues:
            reproduced.append(name)
    cross_checked: list[str] = []
    for name, (key, recompute, rules) in LEGACY_OUTPUT_TABLES.items():
        table_issues = compare_table(name, frames[name], recompute(frames), key, rules)
        issues.extend(table_issues)
        if not table_issues:
            cross_checked.append(name)

    kpi_issues, kpis = reconcile_kpis(frames)
    metric_issues, metrics = reconcile_model_metrics(frames, register)
    issues.extend(kpi_issues)
    issues.extend(metric_issues)

    totals = summaries.historical_totals(frames["daily_total"])
    facts: dict[str, Any] = {
        "historical_totals": {
            **totals,
            "average_daily_requests": round(totals["average_daily_requests"], 4),
        },
        "demand_percentile_threshold_exact": summaries.demand_percentile_threshold(
            frames["daily_total"], 180, 0.75
        ),
        "tables_reproduced": reproduced,
        "legacy_outputs_cross_checked": cross_checked,
        "kpis": [result.to_dict() for result in kpis],
        "model_metrics": metrics,
    }
    return CheckReport(name="reproduction", issues=tuple(issues), facts=facts)
