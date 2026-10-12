"""Snapshot integrity: manifest model, deterministic generation and tamper detection."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.config import Settings
from atlas.errors import SnapshotError
from atlas.registry import LEGACY_V0_SPEC, contracts_for, spec_for
from atlas.snapshot import (
    ManifestFile,
    build_manifest,
    load_manifest,
    manifest_to_json,
    resolve_in_repo,
    sha256_file,
    verify_snapshot,
)
from tests.conftest import REPO_ROOT

# SHA-256 prefixes recorded during the read-only audit of commit db46703, before this
# package existed. They guard against a manifest regenerated from altered data.
AUDITED_BASE_HASH_PREFIXES = {
    "01 Data/daily_total.csv": "16a338f4851f6238",
    "01 Data/daily_segments.csv": "67dee3a0a097bd06",
}


def codes(report: object) -> list[str]:
    return [issue.code for issue in report.issues]  # type: ignore[attr-defined]


@pytest.mark.integration
def test_committed_snapshot_verifies_cleanly(repo_settings: Settings) -> None:
    manifest = load_manifest(repo_settings.manifest_path)
    report = verify_snapshot(repo_settings.repo_root, manifest)
    assert report.ok
    assert report.issues == ()
    assert report.facts["tables"] == 16
    assert report.facts["artifacts"] == 7
    assert report.facts["data_rows"] == 80_582
    assert report.facts["provenance_completeness"] == "incomplete"


@pytest.mark.integration
def test_manifest_regeneration_is_deterministic_and_matches_committed_file(
    repo_settings: Settings,
) -> None:
    first = manifest_to_json(build_manifest(REPO_ROOT, LEGACY_V0_SPEC))
    second = manifest_to_json(build_manifest(REPO_ROOT, LEGACY_V0_SPEC))
    assert first == second
    assert first == repo_settings.manifest_path.read_text(encoding="utf-8")


@pytest.mark.integration
def test_base_table_hashes_match_the_audit_record(repo_settings: Settings) -> None:
    manifest = load_manifest(repo_settings.manifest_path)
    for path, audited in AUDITED_BASE_HASH_PREFIXES.items():
        entry = next(f for f in manifest.files if f.path == path)
        assert entry.sha256.startswith(audited)
        assert sha256_file(REPO_ROOT / path) == entry.sha256


@pytest.mark.integration
def test_manifest_records_schema_ranges_and_provenance(repo_settings: Settings) -> None:
    manifest = load_manifest(repo_settings.manifest_path)
    total = manifest.file("daily_total")
    assert total.rows == 1277
    assert total.columns == ("request_date", "request_count")
    assert (str(total.date_min), str(total.date_max)) == ("2023-01-01", "2026-06-30")
    segments = manifest.file("daily_segments")
    assert segments.rows == 76_458
    assert manifest.provenance.source_dataset_id == "erm2-nwe9"
    assert manifest.provenance.extracted_at is None
    assert manifest.provenance.origin_commit.startswith("db46703")
    # Every manifest table has a contract and every contract column is in the manifest header.
    contracts = contracts_for("legacy-v0")
    assert {f.table for f in manifest.files} == set(contracts)
    for entry in manifest.files:
        assert entry.columns == contracts[entry.table].column_names


def test_single_byte_change_is_detected(repo_copy: Path) -> None:
    target = repo_copy / "01 Data" / "daily_total.csv"
    data = bytearray(target.read_bytes())
    data[-3] = ord("9") if data[-3] != ord("9") else ord("8")  # same size, different content
    target.write_bytes(bytes(data))
    report = verify_snapshot(
        repo_copy, load_manifest(repo_copy / "snapshots/legacy-v0/manifest.json")
    )
    assert not report.ok
    assert codes(report) == ["checksum_mismatch"]


def test_appended_row_changes_size_hash_and_row_count(repo_copy: Path) -> None:
    target = repo_copy / "01 Data" / "daily_total.csv"
    with target.open("a", encoding="utf-8") as handle:
        handle.write("2026-07-01,1\n")
    report = verify_snapshot(
        repo_copy, load_manifest(repo_copy / "snapshots/legacy-v0/manifest.json")
    )
    assert codes(report) == ["size_mismatch", "checksum_mismatch", "row_count_mismatch"]


def test_missing_file_and_modified_artifact_are_detected(repo_copy: Path) -> None:
    (repo_copy / "01 Data" / "model_metrics.csv").unlink()
    pdf = repo_copy / "06 Exports" / "Project2_Demand_Forecasting_Report.pdf"
    pdf.write_bytes(pdf.read_bytes() + b"\n")
    report = verify_snapshot(
        repo_copy, load_manifest(repo_copy / "snapshots/legacy-v0/manifest.json")
    )
    assert {"file_missing", "size_mismatch", "checksum_mismatch"} <= set(codes(report))
    subjects = {issue.subject for issue in report.errors}
    assert subjects == {"model_metrics", "06 Exports/Project2_Demand_Forecasting_Report.pdf"}


def test_unregistered_csv_is_a_warning_not_an_error(repo_copy: Path) -> None:
    (repo_copy / "01 Data" / "scratch.csv").write_text("a\n1\n", encoding="utf-8")
    report = verify_snapshot(
        repo_copy, load_manifest(repo_copy / "snapshots/legacy-v0/manifest.json")
    )
    assert report.ok
    assert [(i.code, i.examples) for i in report.warnings] == [
        ("unregistered_file", ("01 Data/scratch.csv",))
    ]


def test_manifest_loading_failures(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError, match="not found"):
        load_manifest(tmp_path / "manifest.json")
    broken = tmp_path / "broken.json"
    broken.write_text('{"schema_version": 1}', encoding="utf-8")
    with pytest.raises(SnapshotError, match="Invalid snapshot manifest"):
        load_manifest(broken)


@pytest.mark.parametrize(
    "path", ["../outside.csv", "/etc/passwd", "01 Data\\daily_total.csv", "a/./b.csv"]
)
def test_unsafe_manifest_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValidationError, match="relative POSIX path"):
        ManifestFile(
            table="t",
            path=path,
            role="base",
            description="d",
            sha256="0" * 64,
            bytes=0,
            rows=0,
            columns=("a",),
            date_column=None,
            date_min=None,
            date_max=None,
        )
    with pytest.raises(SnapshotError):
        resolve_in_repo(REPO_ROOT, path)


def test_sha256_file_streams_correct_digest(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    payload = b"atlas" * 500_000  # larger than one read chunk
    target.write_bytes(payload)
    assert sha256_file(target) == hashlib.sha256(payload).hexdigest()


def test_unknown_snapshot_is_rejected() -> None:
    with pytest.raises(SnapshotError, match="Unknown snapshot"):
        spec_for("legacy-v9")
    with pytest.raises(SnapshotError, match="No data contracts"):
        contracts_for("legacy-v9")


def test_building_a_manifest_requires_every_registered_file(repo_copy: Path) -> None:
    (repo_copy / "03 SQL" / "Project2_Demand_Forecasting_Analysis.sql").unlink()
    with pytest.raises(SnapshotError, match="Registered artifact is missing"):
        build_manifest(repo_copy, LEGACY_V0_SPEC)
    (repo_copy / "01 Data" / "ytd_workload.csv").unlink()
    with pytest.raises(SnapshotError, match="Registered file is missing"):
        build_manifest(repo_copy, LEGACY_V0_SPEC)
