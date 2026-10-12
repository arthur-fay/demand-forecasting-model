"""Explicit treatment of the city-wide versus five-borough reconciliation gap.

``daily_total`` counts every request; ``daily_segments`` only those whose borough is one
of the five named boroughs (declared exception ``LEGACY-V0-EX-001``). M0 measures the
difference and accepts it only in its declared form. M1 makes it a first-class,
clearly labelled quantity:

    daily_total(d) = sum over the five boroughs of segments(d, borough) + UNASSIGNED(d)

``UNASSIGNED`` is an *aggregate residual*. It is the measured number of requests that
the segment query did not return, and it exists only at city-wide daily level: the
snapshot holds no agency, borough or record-level detail for these requests, so none is
invented. No agency-level residual is produced, and the agency table is never altered.

Policy (fail closed): the components are built only when the M0 reconciliation check
(:func:`atlas.validation.check_reconciliation`) reports no error, that is when segments
never exceed the total and any gap is exactly the one declared in the register.
Otherwise :class:`~atlas.errors.DataQualityError` is raised with the M0 findings.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from atlas.contracts import BOROUGHS, DAILY_BOROUGH_COMPONENTS, UNASSIGNED_COMPONENT
from atlas.dates import iso_weekday
from atlas.errors import DataQualityError
from atlas.issues import Issue, Severity
from atlas.loader import validate_frame
from atlas.validation import KnownExceptions, ReconciliationStats, check_reconciliation

COMPONENT_ORDER: tuple[str, ...] = (*sorted(BOROUGHS), UNASSIGNED_COMPONENT)


@dataclass(frozen=True)
class BoroughComponents:
    """The reconciled table plus the M0 statistics and findings it was built from."""

    frame: pd.DataFrame
    stats: ReconciliationStats
    issues: tuple[Issue, ...]

    def residual(self) -> pd.Series:
        """Daily ``UNASSIGNED`` residual as an ``int64`` series indexed by date."""
        rows = self.frame[self.frame["component"] == UNASSIGNED_COMPONENT]
        series = rows.set_index("request_date")["request_count"].astype("int64")
        series.index = pd.DatetimeIndex(series.index, name="request_date")
        return series.rename("unassigned_residual")


def build_borough_components(
    daily_total: pd.DataFrame, daily_segments: pd.DataFrame, register: KnownExceptions
) -> BoroughComponents:
    """Split each daily total into five borough components and the ``UNASSIGNED`` residual.

    The result has one row per date and component (six per date, zeros explicit), in
    ``COMPONENT_ORDER``, and satisfies the ``daily_borough_components`` contract.
    """
    issues, stats = check_reconciliation(daily_total, daily_segments, register)
    errors = tuple(issue for issue in issues if issue.severity is Severity.ERROR)
    if errors:
        raise DataQualityError(
            "Totals and segments do not reconcile in the declared form; the residual is not "
            "built. " + " ".join(f"[{issue.code}] {issue.message}" for issue in errors),
            errors,
        )

    totals = daily_total.set_index("request_date")["request_count"].astype("int64")
    dates = pd.DatetimeIndex(totals.index).sort_values()
    by_borough = (
        daily_segments.groupby(["request_date", "borough"])["request_count"]
        .sum()
        .unstack("borough")
        .reindex(index=dates, columns=sorted(BOROUGHS))
        .fillna(0)
        .astype("int64")
    )
    by_borough[UNASSIGNED_COMPONENT] = totals.reindex(dates).to_numpy() - by_borough.sum(axis=1)

    by_borough.index.name = "request_date"
    frame = by_borough.reset_index().melt(
        id_vars="request_date", var_name="component", value_name="request_count"
    )
    frame["component"] = pd.Categorical(frame["component"], categories=COMPONENT_ORDER)
    frame = frame.sort_values(["request_date", "component"], kind="mergesort")
    frame["component"] = frame["component"].astype(str)
    frame["component_kind"] = frame["component"].map(
        lambda c: "unassigned_residual" if c == UNASSIGNED_COMPONENT else "borough_segment"
    )
    frame["iso_weekday"] = iso_weekday(pd.DatetimeIndex(frame["request_date"])).to_numpy()
    frame = frame[list(DAILY_BOROUGH_COMPONENTS.column_names)].reset_index(drop=True)
    frame["request_count"] = frame["request_count"].astype("int64")

    checked = validate_frame(frame, DAILY_BOROUGH_COMPONENTS)
    if not checked.ok:  # a bug in this function, not a data problem
        raise AssertionError(f"daily_borough_components violates its contract: {checked.issues}")
    return BoroughComponents(frame=frame, stats=stats, issues=tuple(issues))
