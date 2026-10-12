"""Fail-closed orchestration of the M0 checks.

Stages run in order: ``integrity`` -> ``validation`` -> ``reproduction``. A stage runs
only when every earlier stage passed; later stages are reported as skipped, so no
result is ever computed from unverified or invalid data.
"""

from __future__ import annotations

from enum import IntEnum

from atlas.config import Settings
from atlas.issues import CheckReport
from atlas.loader import LoadedTable, load_table
from atlas.registry import contracts_for
from atlas.reproduce import reproduce_tables
from atlas.snapshot import SnapshotManifest, load_manifest, resolve_in_repo, verify_snapshot
from atlas.validation import KnownExceptions, load_exceptions, validate_tables


class Stage(IntEnum):
    INTEGRITY = 1
    VALIDATION = 2
    REPRODUCTION = 3


def load_snapshot_tables(settings: Settings, manifest: SnapshotManifest) -> dict[str, LoadedTable]:
    """Load every contracted table of the snapshot (call only after integrity passed)."""
    contracts = contracts_for(settings.snapshot_id)
    tables: dict[str, LoadedTable] = {}
    for entry in manifest.files:
        contract = contracts.get(entry.table)
        if contract is None:
            continue
        tables[entry.table] = load_table(resolve_in_repo(settings.repo_root, entry.path), contract)
    return tables


def _skipped(name: str, reason: str) -> CheckReport:
    return CheckReport(name=name, skipped_reason=reason)


def run(settings: Settings, through: Stage = Stage.REPRODUCTION) -> list[CheckReport]:
    """Run stages up to and including ``through``; raises only on unusable configuration."""
    manifest = load_manifest(settings.manifest_path)
    register: KnownExceptions = load_exceptions(settings.exceptions_path, settings.snapshot_id)

    integrity = verify_snapshot(settings.repo_root, manifest)
    reports = [integrity]
    if through is Stage.INTEGRITY:
        return reports
    if not integrity.ok:
        reports.append(_skipped("validation", "snapshot integrity check failed"))
        if through is Stage.REPRODUCTION:
            reports.append(_skipped("reproduction", "snapshot integrity check failed"))
        return reports

    tables = load_snapshot_tables(settings, manifest)
    validation = validate_tables(tables, contracts_for(settings.snapshot_id), register)
    reports.append(validation)
    if through is Stage.VALIDATION:
        return reports
    if not validation.ok:
        reports.append(_skipped("reproduction", "data validation failed"))
        return reports

    reports.append(reproduce_tables(tables, register))
    return reports
