"""Declarative data contracts for the tables of the ``legacy-v0`` snapshot.

A contract states what a valid table looks like: its exact columns, the type of
every value, nullability, ranges, allowed categories, the primary key and, for
date-indexed tables, the calendar window the dates must cover. The loader and
validator enforce these contracts and fail closed on any violation.

The two *base* tables (``daily_total`` and ``daily_segments``) are the raw
aggregates returned by the legacy Socrata queries. Every other table is an output
of the legacy notebook and carries the legacy conventions verbatim (for example
the SQLite ``%w`` weekday numbering, Sunday = 0); M0 records those conventions
instead of silently "fixing" them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Kind(StrEnum):
    """How the text of a CSV field must be parsed."""

    DATE = "date"  # ISO 8601 calendar date: YYYY-MM-DD
    INTEGER = "integer"  # optional sign followed by digits
    INTEGRAL = "integral"  # integer, optionally written with a zero fraction ("6209.0")
    DECIMAL = "decimal"  # finite decimal number
    STRING = "string"  # non-empty text without surrounding whitespace


@dataclass(frozen=True)
class Column:
    name: str
    kind: Kind
    nullable: bool = False
    minimum: float | None = None
    maximum: float | None = None
    allowed: frozenset[str] | None = None
    pattern: str | None = None  # full-match regular expression for STRING columns


@dataclass(frozen=True)
class TableContract:
    name: str
    description: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...]
    date_column: str | None = None
    window: tuple[date, date] | None = None  # inclusive bounds for ``date_column``
    require_every_date: bool = False  # every calendar date in ``window`` must appear
    min_rows: int = 1
    max_rows: int | None = None

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    def column(self, name: str) -> Column:
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError(name)


# --- Calendar windows of the legacy run ---------------------------------------------------

HISTORY_START = date(2023, 1, 1)
HISTORY_END = date(2026, 6, 30)  # extraction window end was 2026-07-01 (exclusive)
HOLDOUT_START = date(2026, 4, 2)
FORECAST_START = date(2026, 7, 1)
FORECAST_END = date(2026, 7, 30)

HISTORY_WINDOW = (HISTORY_START, HISTORY_END)
HOLDOUT_WINDOW = (HOLDOUT_START, HISTORY_END)
FORECAST_WINDOW = (FORECAST_START, FORECAST_END)
CALENDAR_WINDOW = (HISTORY_START, FORECAST_END)

# --- Shared vocabularies ------------------------------------------------------------------

BOROUGHS = frozenset({"BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"})
AGENCY_PATTERN = r"[A-Z0-9][A-Z0-9-]*"
WEEKDAY_NAMES = frozenset(
    {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
)
MONTH_NAMES = frozenset(
    {
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    }
)
MONTH_SHORT_NAMES = frozenset(
    {"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"}
)
RISK_LEVELS = frozenset({"Low", "Medium", "High", "Critical"})


def _count(name: str, *, nullable: bool = False, minimum: float = 0) -> Column:
    return Column(name, Kind.INTEGER, nullable=nullable, minimum=minimum)


def _pct(name: str, *, nullable: bool = False) -> Column:
    return Column(name, Kind.DECIMAL, nullable=nullable)


def _workload_stats(days_column: str) -> tuple[Column, ...]:
    return (
        _count("total_requests"),
        Column("average_daily_requests", Kind.DECIMAL, minimum=0),
        _count("highest_daily_requests"),
        _count("lowest_daily_requests"),
        Column(days_column, Kind.INTEGER, minimum=1),
    )


# --- Base tables (raw aggregates from the legacy Socrata queries) -----------------------

DAILY_TOTAL = TableContract(
    name="daily_total",
    description=(
        "City-wide count of NYC 311 service requests per calendar day of created_date "
        "(no borough filter)."
    ),
    columns=(
        Column("request_date", Kind.DATE),
        _count("request_count"),
    ),
    primary_key=("request_date",),
    date_column="request_date",
    window=HISTORY_WINDOW,
    require_every_date=True,
)

DAILY_SEGMENTS = TableContract(
    name="daily_segments",
    description=(
        "Requests per day, agency and borough, restricted to the five named boroughs. "
        "Only non-empty segments are present: a missing row means zero requests."
    ),
    columns=(
        Column("request_date", Kind.DATE),
        Column("agency", Kind.STRING, pattern=AGENCY_PATTERN),
        Column("borough", Kind.STRING, allowed=BOROUGHS),
        _count("request_count", minimum=1),
    ),
    primary_key=("request_date", "agency", "borough"),
    date_column="request_date",
    window=HISTORY_WINDOW,
    require_every_date=True,
)

# --- Derived aggregates (legacy notebook SQL, cells 8-13) -------------------------------

YEARLY_WORKLOAD = TableContract(
    name="yearly_workload",
    description="Calendar-year totals of daily_total with year-over-year growth (2026 is partial).",
    columns=(
        Column("request_year", Kind.INTEGER, minimum=2000, maximum=2100),
        *_workload_stats("days_recorded"),
        Column("previous_year_requests", Kind.INTEGRAL, nullable=True, minimum=0),
        _pct("year_over_year_growth_pct", nullable=True),
    ),
    primary_key=("request_year",),
)

YTD_WORKLOAD = TableContract(
    name="ytd_workload",
    description="January 1 - June 30 totals of daily_total for 2025 and 2026 with growth.",
    columns=(
        Column("request_year", Kind.INTEGER, minimum=2000, maximum=2100),
        *_workload_stats("days_recorded"),
        Column("previous_ytd_requests", Kind.INTEGRAL, nullable=True, minimum=0),
        _pct("ytd_growth_pct", nullable=True),
    ),
    primary_key=("request_year",),
)

MONTHLY_WORKLOAD = TableContract(
    name="monthly_workload",
    description="Monthly totals of daily_total with month-over-month and year-over-year growth.",
    columns=(
        Column("month_start", Kind.DATE),
        Column("request_year", Kind.INTEGER, minimum=2000, maximum=2100),
        Column("request_month", Kind.INTEGER, minimum=1, maximum=12),
        *_workload_stats("days_recorded"),
        Column("previous_month_requests", Kind.INTEGRAL, nullable=True, minimum=0),
        _pct("month_over_month_growth_pct", nullable=True),
        Column("previous_year_month_requests", Kind.INTEGRAL, nullable=True, minimum=0),
        _pct("year_over_year_growth_pct", nullable=True),
    ),
    primary_key=("month_start",),
)

WEEKDAY_WORKLOAD = TableContract(
    name="weekday_workload",
    description="Day-of-week totals of daily_total. weekday_number follows SQLite %w (Sunday=0).",
    columns=(
        Column("weekday_number", Kind.INTEGER, minimum=0, maximum=6),
        Column("weekday_name", Kind.STRING, allowed=WEEKDAY_NAMES),
        *_workload_stats("days_recorded"),
        Column("workload_share_pct", Kind.DECIMAL, minimum=0, maximum=100),
        Column("average_workload_rank", Kind.INTEGER, minimum=1, maximum=7),
    ),
    primary_key=("weekday_number",),
    min_rows=7,
    max_rows=7,
)

BOROUGH_WORKLOAD = TableContract(
    name="borough_workload",
    description="Borough totals of daily_segments (five-borough population, active days only).",
    columns=(
        Column("borough", Kind.STRING, allowed=BOROUGHS),
        *_workload_stats("days_recorded"),
        Column("workload_share_pct", Kind.DECIMAL, minimum=0, maximum=100),
        Column("workload_rank", Kind.INTEGER, minimum=1),
    ),
    primary_key=("borough",),
    max_rows=5,
)

AGENCY_WORKLOAD = TableContract(
    name="agency_workload",
    description="Agency totals of daily_segments (five-borough population, active days only).",
    columns=(
        Column("agency", Kind.STRING, pattern=AGENCY_PATTERN),
        *_workload_stats("active_days"),
        Column("workload_share_pct", Kind.DECIMAL, minimum=0, maximum=100),
        Column("workload_rank", Kind.INTEGER, minimum=1),
    ),
    primary_key=("agency",),
)

# --- Dimension and legacy model outputs (legacy notebook cells 14-18) -------------------

DATE_DIMENSION = TableContract(
    name="date_dimension",
    description=(
        "Power BI calendar. weekday_number is Monday=1..Sunday=7 and week_of_year is the ISO "
        "week, paired with the calendar year (legacy convention)."
    ),
    columns=(
        Column("date", Kind.DATE),
        Column("year", Kind.INTEGER, minimum=2000, maximum=2100),
        Column("quarter", Kind.STRING, pattern=r"Q[1-4]"),
        Column("month_number", Kind.INTEGER, minimum=1, maximum=12),
        Column("month_name", Kind.STRING, allowed=MONTH_NAMES),
        Column("month_short", Kind.STRING, allowed=MONTH_SHORT_NAMES),
        Column("year_month", Kind.STRING, pattern=r"\d{4}-(0[1-9]|1[0-2])"),
        Column("year_month_sort", Kind.INTEGER, minimum=200001, maximum=210012),
        Column("day_name", Kind.STRING, allowed=WEEKDAY_NAMES),
        Column("weekday_number", Kind.INTEGER, minimum=1, maximum=7),
        Column("day_of_month", Kind.INTEGER, minimum=1, maximum=31),
        Column("week_of_year", Kind.INTEGER, minimum=1, maximum=53),
        Column("week_start", Kind.DATE),
        Column("is_weekend", Kind.INTEGER, minimum=0, maximum=1),
    ),
    primary_key=("date",),
    date_column="date",
    window=CALENDAR_WINDOW,
    require_every_date=True,
)

ACTUAL_FORECAST_SERIES = TableContract(
    name="actual_forecast_series",
    description=(
        "Observed daily totals followed by the legacy 30-day point forecast. Counts are "
        "serialised as floats ('6209.0'); the two series never overlap."
    ),
    columns=(
        Column("date", Kind.DATE),
        Column("actual_requests", Kind.INTEGRAL, nullable=True, minimum=0),
        Column("forecast_requests", Kind.INTEGRAL, nullable=True, minimum=0),
        Column("record_type", Kind.STRING, allowed=frozenset({"Actual", "Forecast"})),
    ),
    primary_key=("date",),
    date_column="date",
    window=CALENDAR_WINDOW,
    require_every_date=True,
)

FORECAST_TEST_RESULTS = TableContract(
    name="forecast_test_results",
    description=(
        "Legacy 90-day holdout. Predictions are one-step-ahead (h=1) using observed lags and "
        "were rounded to integers before export."
    ),
    columns=(
        Column("request_date", Kind.DATE),
        _count("actual_requests", minimum=1),
        _count("baseline_forecast"),
        _count("model_forecast"),
        Column("model_error", Kind.INTEGER),
        _count("absolute_error"),
        Column("absolute_percentage_error", Kind.DECIMAL, minimum=0),
    ),
    primary_key=("request_date",),
    date_column="request_date",
    window=HOLDOUT_WINDOW,
    require_every_date=True,
)

_FORECAST_COLUMNS = (
    Column("request_date", Kind.DATE),
    Column("weekday_name", Kind.STRING, allowed=WEEKDAY_NAMES),
    _count("forecast_requests"),
    Column("forecast_week", Kind.STRING, pattern=r"\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}"),
    Column("forecast_month", Kind.STRING, pattern=r"\d{4}-(0[1-9]|1[0-2])"),
)

FUTURE_30_DAY_FORECAST = TableContract(
    name="future_30_day_forecast",
    description="Legacy recursive 30-day point forecast (never evaluated beyond h=1).",
    columns=_FORECAST_COLUMNS,
    primary_key=("request_date",),
    date_column="request_date",
    window=FORECAST_WINDOW,
    require_every_date=True,
)

CAPACITY_RISK_ALERTS = TableContract(
    name="capacity_risk_alerts",
    description=(
        "Legacy alert table. 'planning_capacity' is the 75th percentile of the last 180 days "
        "of incoming requests, not a measured service capacity."
    ),
    columns=(
        *_FORECAST_COLUMNS,
        Column("days_ahead", Kind.INTEGER, minimum=1, maximum=366),
        _count("planning_capacity", minimum=1),
        Column("capacity_utilization_pct", Kind.DECIMAL, minimum=0),
        _count("capacity_gap_requests"),
        _count("available_capacity"),
        Column("risk_level", Kind.STRING, allowed=RISK_LEVELS),
        Column("capacity_shortfall_flag", Kind.INTEGER, minimum=0, maximum=1),
        Column("recommended_action", Kind.STRING),
    ),
    primary_key=("request_date",),
    date_column="request_date",
    window=FORECAST_WINDOW,
    require_every_date=True,
)

CAPACITY_RISK_SUMMARY = TableContract(
    name="capacity_risk_summary",
    description="Legacy alert counts and totals by risk level.",
    columns=(
        Column("risk_level", Kind.STRING, allowed=RISK_LEVELS),
        _count("forecast_days"),
        _count("total_forecast_requests"),
        Column("average_utilization_pct", Kind.DECIMAL, minimum=0),
        _count("total_capacity_gap"),
    ),
    primary_key=("risk_level",),
    max_rows=4,
)

MODEL_METRICS = TableContract(
    name="model_metrics",
    description="Legacy holdout metrics (h=1, observed lags) for the regression and baseline.",
    columns=(
        Column("model_name", Kind.STRING),
        Column("mae", Kind.DECIMAL, minimum=0),
        Column("rmse", Kind.DECIMAL, minimum=0),
        Column("mape_pct", Kind.DECIMAL, minimum=0),
        Column("r_squared", Kind.DECIMAL),
        Column("mape_improvement_pct", Kind.DECIMAL),
    ),
    primary_key=("model_name",),
)

PROJECT_KPIS = TableContract(
    name="project_kpis",
    description="Single-row table of the headline KPIs published by the legacy project.",
    columns=(
        _count("historical_total_requests"),
        Column("historical_average_daily_requests", Kind.DECIMAL, minimum=0),
        Column("ytd_2026_growth_pct", Kind.DECIMAL),
        Column("model_mape_pct", Kind.DECIMAL, minimum=0),
        Column("baseline_mape_pct", Kind.DECIMAL, minimum=0),
        Column("mape_improvement_pct", Kind.DECIMAL),
        _count("forecast_30_day_requests"),
        Column("forecast_average_daily_requests", Kind.DECIMAL, minimum=0),
        _count("planning_capacity_threshold", minimum=1),
        _count("capacity_shortfall_days"),
        _count("total_forecast_capacity_gap"),
        Column("maximum_forecast_utilization_pct", Kind.DECIMAL, minimum=0),
        Column("first_shortfall_date", Kind.DATE, nullable=True),
        _count("advance_warning_days", nullable=True),
    ),
    primary_key=(),
    max_rows=1,
)

LEGACY_V0_CONTRACTS: dict[str, TableContract] = {
    contract.name: contract
    for contract in (
        DAILY_TOTAL,
        DAILY_SEGMENTS,
        YEARLY_WORKLOAD,
        YTD_WORKLOAD,
        MONTHLY_WORKLOAD,
        WEEKDAY_WORKLOAD,
        BOROUGH_WORKLOAD,
        AGENCY_WORKLOAD,
        DATE_DIMENSION,
        ACTUAL_FORECAST_SERIES,
        FORECAST_TEST_RESULTS,
        FUTURE_30_DAY_FORECAST,
        CAPACITY_RISK_ALERTS,
        CAPACITY_RISK_SUMMARY,
        MODEL_METRICS,
        PROJECT_KPIS,
    )
}
