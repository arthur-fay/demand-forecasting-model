"""Immutable data snapshots: manifest model, generation and integrity verification.

A snapshot is a set of files identified by SHA-256. Its manifest records, for
every tabular file, the hash, byte size, exact header, data-row count and date
range, plus a provenance record. Registered snapshots are immutable: verification
fails if a single byte changes, and the manifest is never rewritten in place.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from atlas.errors import SnapshotError
from atlas.issues import CheckReport, Issue, Severity, examples

_HASH_CHUNK = 1 << 20

TableRole = Literal["base", "derived_aggregate", "dimension", "legacy_model_output"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _check_relative_posix(path: str) -> str:
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or "\\" in path or path != str(pure):
        raise ValueError(f"path must be a normalised, relative POSIX path: {path!r}")
    return path


class Provenance(_Frozen):
    source_name: str
    source_publisher: str
    source_dataset_id: str
    source_endpoint: str
    extraction_method: str
    extraction_window_start: date
    extraction_window_end_exclusive: date
    extracted_at: datetime | None
    extracted_at_note: str
    produced_by: str
    origin_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    completeness: Literal["complete", "incomplete"]
    known_gaps: tuple[str, ...]
    terms_of_use: str


class ManifestFile(_Frozen):
    table: str
    path: str
    role: TableRole
    description: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    rows: int = Field(ge=0)
    columns: tuple[str, ...]
    date_column: str | None
    date_min: date | None
    date_max: date | None

    @field_validator("path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        return _check_relative_posix(value)


class ManifestArtifact(_Frozen):
    path: str
    kind: str
    description: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        return _check_relative_posix(value)


class SnapshotManifest(_Frozen):
    schema_version: Literal[1]
    snapshot_id: str
    registered_at: date
    immutable: Literal[True]
    description: str
    provenance: Provenance
    files: tuple[ManifestFile, ...]
    artifacts: tuple[ManifestArtifact, ...]

    @model_validator(mode="after")
    def _unique_entries(self) -> SnapshotManifest:
        tables = [f.table for f in self.files]
        paths = [f.path for f in self.files] + [a.path for a in self.artifacts]
        if len(set(tables)) != len(tables):
            raise ValueError("table names must be unique")
        if len(set(paths)) != len(paths):
            raise ValueError("paths must be unique")
        return self

    def file(self, table: str) -> ManifestFile:
        for entry in self.files:
            if entry.table == table:
                return entry
        raise KeyError(table)


# --- Specifications used to build a manifest -----------------------------------------


class TableSpec(_Frozen):
    table: str
    path: str
    role: TableRole
    description: str
    date_column: str | None = None

    @field_validator("path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        return _check_relative_posix(value)


class ArtifactSpec(_Frozen):
    path: str
    kind: str
    description: str

    @field_validator("path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        return _check_relative_posix(value)


class SnapshotSpec(_Frozen):
    snapshot_id: str
    registered_at: date
    description: str
    provenance: Provenance
    tables: tuple[TableSpec, ...]
    artifacts: tuple[ArtifactSpec, ...]


# --- Hashing and file description ---------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_in_repo(repo_root: Path, relative: str) -> Path:
    """Resolve a manifest path and refuse anything that escapes the repository."""
    root = repo_root.resolve()
    try:
        candidate = (root / _check_relative_posix(relative)).resolve()
    except ValueError as exc:
        raise SnapshotError(str(exc)) from exc
    if not candidate.is_relative_to(root):
        raise SnapshotError(f"Manifest path escapes the repository: {relative!r}")
    return candidate


def _read_header_and_rows(path: Path) -> tuple[tuple[str, ...], list[list[str]]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            records = list(csv.reader(handle, strict=True))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise SnapshotError(f"{path} is not a readable UTF-8 CSV file: {exc}") from exc
    if not records:
        raise SnapshotError(f"{path} is empty")
    return tuple(records[0]), records[1:]


def _date_range(
    header: tuple[str, ...], rows: list[list[str]], date_column: str, label: str
) -> tuple[date | None, date | None]:
    if date_column not in header:
        raise SnapshotError(f"{label} has no date column {date_column!r}")
    position = header.index(date_column)
    try:
        dates = [date.fromisoformat(row[position]) for row in rows if row[position]]
    except (ValueError, IndexError) as exc:
        raise SnapshotError(f"{label}: unparseable {date_column!r}: {exc}") from exc
    return (min(dates), max(dates)) if dates else (None, None)


def describe_table(repo_root: Path, spec: TableSpec) -> ManifestFile:
    """Compute the manifest entry for one tabular file."""
    path = resolve_in_repo(repo_root, spec.path)
    if not path.is_file():
        raise SnapshotError(f"Registered file is missing: {spec.path}")
    header, rows = _read_header_and_rows(path)
    date_min: date | None = None
    date_max: date | None = None
    if spec.date_column is not None:
        date_min, date_max = _date_range(header, rows, spec.date_column, spec.path)
    return ManifestFile(
        table=spec.table,
        path=spec.path,
        role=spec.role,
        description=spec.description,
        sha256=sha256_file(path),
        bytes=path.stat().st_size,
        rows=len(rows),
        columns=header,
        date_column=spec.date_column,
        date_min=date_min,
        date_max=date_max,
    )


def describe_artifact(repo_root: Path, spec: ArtifactSpec) -> ManifestArtifact:
    path = resolve_in_repo(repo_root, spec.path)
    if not path.is_file():
        raise SnapshotError(f"Registered artifact is missing: {spec.path}")
    return ManifestArtifact(
        path=spec.path,
        kind=spec.kind,
        description=spec.description,
        sha256=sha256_file(path),
        bytes=path.stat().st_size,
    )


def build_manifest(repo_root: Path, spec: SnapshotSpec) -> SnapshotManifest:
    """Describe the files named by ``spec`` as they currently exist on disk.

    Deterministic: the same files and specification always produce an identical
    manifest. Used once to register a snapshot and afterwards only in tests that
    prove the committed manifest still matches the registry and the files.
    """
    return SnapshotManifest(
        schema_version=1,
        snapshot_id=spec.snapshot_id,
        registered_at=spec.registered_at,
        immutable=True,
        description=spec.description,
        provenance=spec.provenance,
        files=tuple(describe_table(repo_root, table) for table in spec.tables),
        artifacts=tuple(describe_artifact(repo_root, artifact) for artifact in spec.artifacts),
    )


def manifest_to_json(manifest: SnapshotManifest) -> str:
    return json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def load_manifest(path: Path) -> SnapshotManifest:
    if not path.is_file():
        raise SnapshotError(f"Snapshot manifest not found: {path}")
    try:
        # Bytes, not text: invalid UTF-8 then surfaces as a ValidationError, not a crash.
        return SnapshotManifest.model_validate_json(path.read_bytes())
    except ValidationError as exc:
        raise SnapshotError(f"Invalid snapshot manifest {path}:\n{exc}") from exc
    except OSError as exc:
        raise SnapshotError(f"Cannot read snapshot manifest {path}: {exc}") from exc


# --- Integrity verification ---------------------------------------------------------


def _verify_bytes(
    repo_root: Path, subject: str, relative: str, expected_bytes: int, expected_sha: str
) -> tuple[Path | None, list[Issue]]:
    path = resolve_in_repo(repo_root, relative)
    if not path.is_file():
        return None, [
            Issue(
                "file_missing", Severity.ERROR, subject, f"Registered file is missing: {relative}"
            )
        ]
    issues: list[Issue] = []
    size = path.stat().st_size
    if size != expected_bytes:
        issues.append(
            Issue(
                "size_mismatch",
                Severity.ERROR,
                subject,
                f"{relative}: {size} bytes on disk, manifest records {expected_bytes}.",
            )
        )
    actual_sha = sha256_file(path)
    if actual_sha != expected_sha:
        issues.append(
            Issue(
                "checksum_mismatch",
                Severity.ERROR,
                subject,
                f"{relative}: SHA-256 {actual_sha} does not match manifest {expected_sha}.",
            )
        )
    return path, issues


def _unregistered_csvs(repo_root: Path, manifest: SnapshotManifest) -> list[str]:
    registered = {entry.path for entry in manifest.files}
    folders = {PurePosixPath(entry.path).parent for entry in manifest.files}
    found: list[str] = []
    for folder in sorted(folders):
        directory = repo_root / folder
        if directory.is_dir():
            for candidate in sorted(directory.glob("*.csv")):
                relative = candidate.relative_to(repo_root).as_posix()
                if relative not in registered:
                    found.append(relative)
    return found


def verify_snapshot(repo_root: Path, manifest: SnapshotManifest) -> CheckReport:
    """Check every registered file and artifact against the manifest, byte for byte."""
    issues: list[Issue] = []
    for entry in manifest.files:
        path, file_issues = _verify_bytes(
            repo_root, entry.table, entry.path, entry.bytes, entry.sha256
        )
        issues.extend(file_issues)
        if path is None:
            continue
        try:
            header, rows = _read_header_and_rows(path)
        except SnapshotError as exc:
            issues.append(Issue("unreadable_file", Severity.ERROR, entry.table, str(exc)))
            continue
        if header != entry.columns:
            issues.append(
                Issue(
                    "header_mismatch",
                    Severity.ERROR,
                    entry.table,
                    f"Header {list(header)} differs from manifest {list(entry.columns)}.",
                )
            )
        if len(rows) != entry.rows:
            issues.append(
                Issue(
                    "row_count_mismatch",
                    Severity.ERROR,
                    entry.table,
                    f"{len(rows)} data rows on disk, manifest records {entry.rows}.",
                )
            )
        if entry.date_column is not None:
            try:
                observed = _date_range(header, rows, entry.date_column, entry.path)
            except SnapshotError as exc:
                issues.append(Issue("date_range_mismatch", Severity.ERROR, entry.table, str(exc)))
            else:
                if observed != (entry.date_min, entry.date_max):
                    issues.append(
                        Issue(
                            "date_range_mismatch",
                            Severity.ERROR,
                            entry.table,
                            f"{entry.date_column} spans {observed[0]}..{observed[1]} on disk, "
                            f"manifest records {entry.date_min}..{entry.date_max}.",
                        )
                    )

    for artifact in manifest.artifacts:
        _, artifact_issues = _verify_bytes(
            repo_root, artifact.path, artifact.path, artifact.bytes, artifact.sha256
        )
        issues.extend(artifact_issues)

    unregistered = _unregistered_csvs(repo_root, manifest)
    if unregistered:
        issues.append(
            Issue(
                "unregistered_file",
                Severity.WARNING,
                "snapshot",
                "CSV files next to registered tables are not part of the snapshot.",
                count=len(unregistered),
                examples=examples(unregistered),
            )
        )

    facts = {
        "snapshot_id": manifest.snapshot_id,
        "registered_at": manifest.registered_at.isoformat(),
        "tables": len(manifest.files),
        "artifacts": len(manifest.artifacts),
        "data_rows": sum(entry.rows for entry in manifest.files),
        "bytes": sum(entry.bytes for entry in manifest.files)
        + sum(artifact.bytes for artifact in manifest.artifacts),
        "provenance_completeness": manifest.provenance.completeness,
    }
    return CheckReport(name="integrity", issues=tuple(issues), facts=facts)


def entries_by_table(files: Iterable[ManifestFile]) -> dict[str, ManifestFile]:
    return {entry.table: entry for entry in files}
