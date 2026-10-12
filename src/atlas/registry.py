"""Registry of known snapshots and the contracts that govern them.

``legacy-v0`` freezes the artifacts committed by the original project (commit
db4670335dac76628e6f2cd7b1806b2cd536abfb). The files stay at their original paths;
the registry only describes them. Provenance is recorded as *incomplete* because
the original run did not record its extraction time, query hash or library versions.
"""

from __future__ import annotations

from datetime import date

from atlas.contracts import LEGACY_V0_CONTRACTS, TableContract
from atlas.errors import SnapshotError
from atlas.snapshot import ArtifactSpec, Provenance, SnapshotSpec, TableRole, TableSpec

LEGACY_DATA_DIR = "01 Data"

_LEGACY_ROLES: dict[str, TableRole] = {
    "daily_total": "base",
    "daily_segments": "base",
    "yearly_workload": "derived_aggregate",
    "ytd_workload": "derived_aggregate",
    "monthly_workload": "derived_aggregate",
    "weekday_workload": "derived_aggregate",
    "borough_workload": "derived_aggregate",
    "agency_workload": "derived_aggregate",
    "date_dimension": "dimension",
    "actual_forecast_series": "legacy_model_output",
    "forecast_test_results": "legacy_model_output",
    "future_30_day_forecast": "legacy_model_output",
    "capacity_risk_alerts": "legacy_model_output",
    "capacity_risk_summary": "legacy_model_output",
    "model_metrics": "legacy_model_output",
    "project_kpis": "legacy_model_output",
}

LEGACY_V0_PROVENANCE = Provenance(
    source_name="311 Service Requests from 2020 to Present",
    source_publisher="NYC Open Data (City of New York)",
    source_dataset_id="erm2-nwe9",
    source_endpoint="https://data.cityofnewyork.us/resource/erm2-nwe9.json",
    extraction_method=(
        "Two aggregate SoQL queries in the legacy notebook (cells 2-3): count(*) grouped by "
        "date_trunc_ymd(created_date) with no borough filter (daily_total), and grouped by "
        "date, agency and borough restricted to BRONX, BROOKLYN, MANHATTAN, QUEENS and "
        "STATEN ISLAND (daily_segments). All other tables were computed from these two in "
        "the notebook (cells 8-18) and exported for Power BI."
    ),
    extraction_window_start=date(2023, 1, 1),
    extraction_window_end_exclusive=date(2026, 7, 1),
    extracted_at=None,
    extracted_at_note=(
        "Not recorded by the original run. The data ends on 2026-06-30 and the earliest "
        "timestamp in the repository is 2026-07-17 (Power BI file); the extraction time "
        "cannot be established more precisely from the repository."
    ),
    produced_by=(
        "02 Notebooks/Project_2_Demand_Workload_Forecasting.ipynb (Google Colab), "
        "exported by cell 18 and committed manually"
    ),
    origin_commit="db4670335dac76628e6f2cd7b1806b2cd536abfb",
    completeness="incomplete",
    known_gaps=(
        "No extraction timestamp, query hash, row-limit check or API response metadata.",
        "No record of Python or library versions used by the original run.",
        "Raw API responses were not retained; only the two aggregate tables survive.",
        "Upstream 311 records may have been revised after extraction, so re-querying the "
        "source may not reproduce these counts exactly (not verified).",
    ),
    terms_of_use=(
        "Published by NYC Open Data. The applicable terms of use were not verified in "
        "this repository; confirm them before redistribution."
    ),
)


def _table_spec(contract: TableContract) -> TableSpec:
    return TableSpec(
        table=contract.name,
        path=f"{LEGACY_DATA_DIR}/{contract.name}.csv",
        role=_LEGACY_ROLES[contract.name],
        description=contract.description,
        date_column=contract.date_column,
    )


LEGACY_V0_SPEC = SnapshotSpec(
    snapshot_id="legacy-v0",
    registered_at=date(2026, 10, 12),
    description=(
        "Frozen artifacts of the original NYC 311 demand-forecasting project: two base "
        "aggregates, fourteen derived tables and the original report artifacts."
    ),
    provenance=LEGACY_V0_PROVENANCE,
    tables=tuple(_table_spec(contract) for contract in LEGACY_V0_CONTRACTS.values()),
    artifacts=(
        ArtifactSpec(
            path="02 Notebooks/Project_2_Demand_Workload_Forecasting.ipynb",
            kind="notebook",
            description="Original Colab notebook (pipeline, model and exports).",
        ),
        ArtifactSpec(
            path="03 SQL/Project2_Demand_Forecasting_Analysis.sql",
            kind="sql",
            description="Original SQLite analysis script (not executed by ATLAS).",
        ),
        ArtifactSpec(
            path="04 Power BI/Project_2_Demand_Workload_Forecasting.pbix",
            kind="power_bi",
            description="Original Power BI report (binary).",
        ),
        ArtifactSpec(
            path="05 Screenshots/Capacity_Risk_Details.png",
            kind="image",
            description="Dashboard screenshot: capacity risk details page.",
        ),
        ArtifactSpec(
            path="05 Screenshots/Demand_Forecasting_Data_Model.png",
            kind="image",
            description="Dashboard screenshot: Power BI data model.",
        ),
        ArtifactSpec(
            path="05 Screenshots/Demand_Forecasting_Overview.png",
            kind="image",
            description="Dashboard screenshot: overview page.",
        ),
        ArtifactSpec(
            path="06 Exports/Project2_Demand_Forecasting_Report.pdf",
            kind="pdf",
            description="Original PDF export of the dashboard.",
        ),
    ),
)

SNAPSHOT_SPECS: dict[str, SnapshotSpec] = {LEGACY_V0_SPEC.snapshot_id: LEGACY_V0_SPEC}
SNAPSHOT_CONTRACTS: dict[str, dict[str, TableContract]] = {"legacy-v0": LEGACY_V0_CONTRACTS}


def spec_for(snapshot_id: str) -> SnapshotSpec:
    try:
        return SNAPSHOT_SPECS[snapshot_id]
    except KeyError:
        raise SnapshotError(
            f"Unknown snapshot {snapshot_id!r}; known: {sorted(SNAPSHOT_SPECS)}"
        ) from None


def contracts_for(snapshot_id: str) -> dict[str, TableContract]:
    try:
        return SNAPSHOT_CONTRACTS[snapshot_id]
    except KeyError:
        raise SnapshotError(f"No data contracts registered for snapshot {snapshot_id!r}") from None
