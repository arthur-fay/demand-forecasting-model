"""Fail-closed orchestration of the checks.

Stages run in order: ``integrity`` -> ``validation`` -> ``reproduction`` (M0), and
``integrity`` -> ``validation`` -> ``quality`` (M1, :func:`run_quality`). A stage runs
only when every stage it depends on passed; later stages are reported as skipped, so
no result is ever computed from unverified or invalid data.
"""

from __future__ import annotations

from enum import IntEnum

from atlas.config import Settings
from atlas.issues import CheckReport, Issue, Severity
from atlas.loader import LoadedTable, load_table
from atlas.quality import assess_snapshot
from atlas.registry import REPRODUCIBLE_SNAPSHOTS, contracts_for
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


def _integrity_and_validation(
    settings: Settings, through: Stage
) -> tuple[list[CheckReport], dict[str, LoadedTable], KnownExceptions | None]:
    """Run integrity, then validation if requested and integrity passed."""
    manifest = load_manifest(settings.manifest_path)
    integrity = verify_snapshot(settings.repo_root, manifest)
    reports = [integrity]
    if through is Stage.INTEGRITY:
        return reports, {}, None
    if not integrity.ok:
        reports.append(_skipped("validation", "snapshot integrity check failed"))
        return reports, {}, None

    # Loaded only when needed, so a broken register cannot block `atlas snapshot verify`.
    register = load_exceptions(settings.exceptions_path, settings.snapshot_id)
    tables = load_snapshot_tables(settings, manifest)
    reports.append(validate_tables(tables, contracts_for(settings.snapshot_id), register))
    return reports, tables, register


def validated_tables(
    settings: Settings, stage_name: str
) -> tuple[list[CheckReport], dict[str, LoadedTable] | None]:
    """Integrity then validation, for a later stage named ``stage_name`` (M2 commands).

    Returns the reports and the validated tables, or ``None`` for the tables (with a
    skipped report for ``stage_name`` appended) when either stage failed.
    """
    reports, tables, register = _integrity_and_validation(settings, Stage.VALIDATION)
    if register is None:
        reports.append(_skipped(stage_name, "snapshot integrity check failed"))
        return reports, None
    if not reports[-1].ok:
        reports.append(_skipped(stage_name, "data validation failed"))
        return reports, None
    return reports, tables


def run(settings: Settings, through: Stage = Stage.REPRODUCTION) -> list[CheckReport]:
    """Run stages up to and including ``through``; raises only on unusable configuration."""
    reports, tables, register = _integrity_and_validation(settings, through)
    if through is Stage.INTEGRITY:
        return reports
    if register is None:  # integrity failed
        if through is Stage.REPRODUCTION:
            reports.append(_skipped("reproduction", "snapshot integrity check failed"))
        return reports
    if through is Stage.VALIDATION:
        return reports
    if not reports[-1].ok:
        reports.append(_skipped("reproduction", "data validation failed"))
        return reports

    if settings.snapshot_id not in REPRODUCIBLE_SNAPSHOTS:
        not_applicable = Issue(
            "not_applicable",
            Severity.INFO,
            "reproduction",
            f"No reproduction rules are registered for snapshot {settings.snapshot_id!r}.",
        )
        reports.append(CheckReport(name="reproduction", issues=(not_applicable,)))
        return reports
    reports.append(reproduce_tables(tables, register))
    return reports


def run_quality(settings: Settings) -> list[CheckReport]:
    """Integrity -> validation -> quality (M1): reconciled residual and data-quality flags.

    Quality depends only on validated base tables, not on reproduction of legacy outputs.
    """
    reports, tables, register = _integrity_and_validation(settings, Stage.VALIDATION)
    if register is None:
        reports.append(_skipped("quality", "snapshot integrity check failed"))
        return reports
    if not reports[-1].ok:
        reports.append(_skipped("quality", "data validation failed"))
        return reports
    total, segments = tables.get("daily_total"), tables.get("daily_segments")
    if total is None or segments is None:
        not_applicable = Issue(
            "not_applicable",
            Severity.INFO,
            "quality",
            f"Snapshot {settings.snapshot_id!r} has no daily_total and daily_segments tables.",
        )
        reports.append(CheckReport(name="quality", issues=(not_applicable,)))
        return reports
    reports.append(assess_snapshot(total.frame, segments.frame, register))
    return reports
