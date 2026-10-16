"""Snapshot integrity: manifest model, deterministic generation and tamper detection."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.config import Settings
from atlas.deploy.manifest import CONTRACT, RESEARCH_PATHS
from atlas.errors import SnapshotError
from atlas.issues import CheckReport
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


def codes(report: CheckReport) -> list[str]:
    return [issue.code for issue in report.issues]


def _git(*args: str) -> subprocess.CompletedProcess[bytes]:
    git = shutil.which("git")
    if git is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("git history is not available")
    return subprocess.run([git, "-C", str(REPO_ROOT), *args], capture_output=True, check=False)  # noqa: S603


def _patch_manifest(repo: Path, path: str, **changes: object) -> None:
    manifest_path = repo / "snapshots" / "legacy-v0" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        if entry["path"] == path:
            entry.update(changes)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _rehash(repo: Path, path: str) -> None:
    data = (repo / path).read_bytes()
    _patch_manifest(repo, path, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))


def _verify(repo: Path) -> CheckReport:
    return verify_snapshot(repo, load_manifest(repo / "snapshots/legacy-v0/manifest.json"))


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
    assert codes(report) == [
        "size_mismatch",
        "checksum_mismatch",
        "row_count_mismatch",
        "date_range_mismatch",
    ]


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


# SHA-256 prefixes of the original artifacts recorded during the read-only audit of db46703.
AUDITED_ARTIFACT_HASH_PREFIXES = {
    "02 Notebooks/Project_2_Demand_Workload_Forecasting.ipynb": "3f133a9b",
    "03 SQL/Project2_Demand_Forecasting_Analysis.sql": "fc0607c2",
    "04 Power BI/Project_2_Demand_Workload_Forecasting.pbix": "a11f70d9",
    "05 Screenshots/Capacity_Risk_Details.png": "95d19bf1",
    "05 Screenshots/Demand_Forecasting_Data_Model.png": "87cc48fd",
    "05 Screenshots/Demand_Forecasting_Overview.png": "9073876e",
    "06 Exports/Project2_Demand_Forecasting_Report.pdf": "50ae9ac3",
}


@pytest.mark.integration
def test_artifact_hashes_match_the_audit_record(repo_settings: Settings) -> None:
    manifest = load_manifest(repo_settings.manifest_path)
    recorded = {artifact.path: artifact.sha256 for artifact in manifest.artifacts}
    assert set(recorded) == set(AUDITED_ARTIFACT_HASH_PREFIXES)
    for path, prefix in AUDITED_ARTIFACT_HASH_PREFIXES.items():
        assert recorded[path].startswith(prefix)


@pytest.mark.integration
def test_every_registered_file_matches_the_original_commit(repo_settings: Settings) -> None:
    """The manifest describes the bytes committed in the original project, not later edits."""
    manifest = load_manifest(repo_settings.manifest_path)
    commit = manifest.provenance.origin_commit
    if _git("cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
        pytest.skip(f"origin commit {commit[:7]} is not in this clone (shallow history)")
    for path in [f.path for f in manifest.files] + [a.path for a in manifest.artifacts]:
        blob = _git("show", f"{commit}:{path}")
        assert blob.returncode == 0, path
        expected = next(e.sha256 for e in (*manifest.files, *manifest.artifacts) if e.path == path)
        assert hashlib.sha256(blob.stdout).hexdigest() == expected, path


@pytest.mark.integration
def test_every_registered_file_is_protected_from_line_ending_conversion(
    repo_settings: Settings,
) -> None:
    manifest = load_manifest(repo_settings.manifest_path)
    paths = [f.path for f in manifest.files] + [a.path for a in manifest.artifacts]
    result = _git("check-attr", "text", "--", *paths)
    assert result.returncode == 0
    lines = result.stdout.decode().splitlines()
    assert len(lines) == len(paths)
    assert all(line.endswith(": text: unset") for line in lines), lines


#: Verified byte for byte by the dashboard checks: the committed build (against its own
#: manifest, web/dist/atlas-dashboard.json), the files the build copies into it unchanged and
#: the API answers the frontend tests render (tests/test_m7_fixtures.py).
WEB_BYTE_VERIFIED = ("web/dist", "web/public", "web/src/test/fixtures")


def _byte_verified_paths() -> list[str]:
    """Every file verified byte for byte outside the snapshot manifest: each tracked file
    under the release's research paths (results, configurations, snapshot and data) and
    under the dashboard's build, public files and test fixtures, every output named in a
    docs/results run manifest (committed or regenerated by verify) and the API contract."""
    listed = _git("ls-files", "-z", "--", *RESEARCH_PATHS, *WEB_BYTE_VERIFIED)
    assert listed.returncode == 0
    paths = {p for p in listed.stdout.decode().split("\0") if p}
    for top in WEB_BYTE_VERIFIED:
        assert any(p.startswith(f"{top}/") for p in paths), top
    manifests = sorted((REPO_ROOT / "docs" / "results").glob("**/manifest.json"))
    assert len(manifests) >= 9
    for manifest in manifests:
        outputs = json.loads(manifest.read_text(encoding="utf-8"))["outputs"]
        directory = manifest.parent.relative_to(REPO_ROOT).as_posix()
        paths.update(f"{directory}/{name}" for name in outputs)
    paths.add(CONTRACT)
    return sorted(paths)


def test_results_configs_and_contract_are_protected_from_line_ending_conversion() -> None:
    """Every byte-verified file, the dashboard's included, has the text attribute unset."""
    paths = _byte_verified_paths()
    result = _git("check-attr", "-z", "text", "--", *paths)
    assert result.returncode == 0
    fields = result.stdout.decode().split("\0")[:-1]
    states = dict(zip(fields[0::3], fields[2::3], strict=True))
    assert sorted(states) == paths
    assert [p for p, state in states.items() if state != "unset"] == []


def test_a_crlf_checkout_writes_every_byte_verified_file_unchanged(tmp_path: Path) -> None:
    """core.autocrlf=true (the Git for Windows default) must not rewrite any of them.

    ``git archive`` converts line endings exactly as a checkout does; with
    ``--worktree-attributes`` it applies this working tree's .gitattributes to the files
    committed at HEAD. README.md, which no rule protects, is the positive control: it must
    come out with CRLF, or the export did not convert anything and proves nothing.
    """
    control = "README.md"
    trees: dict[str, dict[str, bytes]] = {}
    for autocrlf in ("false", "true"):
        archive = tmp_path / f"autocrlf-{autocrlf}.tar"
        result = _git(
            *("-c", f"core.autocrlf={autocrlf}", "archive", "--worktree-attributes"),
            *("-o", str(archive), "HEAD", "--", *RESEARCH_PATHS, *WEB_BYTE_VERIFIED),
            *(CONTRACT, control),
        )
        assert result.returncode == 0, result.stderr.decode()
        trees[autocrlf] = {}
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                extracted = tar.extractfile(member) if member.isfile() else None
                if extracted is not None:
                    trees[autocrlf][member.name] = extracted.read()
    unconverted, converted = trees["false"].pop(control), trees["true"].pop(control)
    assert b"\r\n" not in unconverted
    assert converted.count(b"\r\n") == unconverted.count(b"\n"), "autocrlf converted nothing"
    crlf = trees["true"]
    assert sorted(crlf) == sorted(trees["false"])
    assert [name for name in crlf if crlf[name] != trees["false"][name]] == []
    checked = 0
    for name in sorted(crlf):
        if not name.endswith("/manifest.json") or not name.startswith("docs/results/"):
            continue
        directory = name.rsplit("/", 1)[0]
        for output, sha256 in json.loads(crlf[name])["outputs"].items():
            if f"{directory}/{output}" in crlf:
                digest = hashlib.sha256(crlf[f"{directory}/{output}"]).hexdigest()
                assert digest == sha256, f"{directory}/{output}"
                checked += 1
    assert checked >= 40
    assert b"\r" not in crlf[CONTRACT]
    # The dashboard build verifies against its own manifest, and the public files the build
    # copies into it stay identical to their copies.
    build = json.loads(crlf["web/dist/atlas-dashboard.json"])["files"]
    assert len(build) >= 10
    for name, entry in build.items():
        assert hashlib.sha256(crlf[f"web/dist/{name}"]).hexdigest() == entry["sha256"], name
    public = sorted(name for name in crlf if name.startswith("web/public/"))
    assert public
    for name in public:
        assert crlf[name] == crlf[name.replace("web/public/", "web/dist/", 1)], name
    assert any(name.startswith("web/src/test/fixtures/") for name in crlf)


def test_header_change_is_detected(repo_copy: Path) -> None:
    path = "01 Data/model_metrics.csv"
    target = repo_copy / path
    target.write_bytes(target.read_bytes().replace(b"model_name,", b"model,", 1))
    _rehash(repo_copy, path)
    assert codes(_verify(repo_copy)) == ["header_mismatch"]


def test_unreadable_registered_file_is_reported(repo_copy: Path) -> None:
    path = "01 Data/capacity_risk_summary.csv"
    target = repo_copy / path
    target.write_bytes(target.read_bytes() + b"\xff\xfe\n")
    _rehash(repo_copy, path)
    assert codes(_verify(repo_copy)) == ["unreadable_file"]


def test_manifest_date_range_is_verified(repo_copy: Path) -> None:
    _patch_manifest(repo_copy, "01 Data/daily_total.csv", date_min="2023-01-02")
    report = _verify(repo_copy)
    assert codes(report) == ["date_range_mismatch"]
    assert "manifest records 2023-01-02..2026-06-30" in report.issues[0].message


def test_duplicate_manifest_entries_are_rejected(tmp_path: Path) -> None:
    manifest = json.loads(
        (REPO_ROOT / "snapshots/legacy-v0/manifest.json").read_text(encoding="utf-8")
    )
    manifest["files"].append(manifest["files"][0])
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SnapshotError, match="must be unique"):
        load_manifest(path)


def test_undecodable_manifest_is_a_snapshot_error(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_bytes(b'{"schema_version": 1, "snapshot_id": "\xff"}')
    with pytest.raises(SnapshotError, match="Invalid snapshot manifest"):
        load_manifest(path)


def test_symlink_escaping_the_repository_is_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("a\n", encoding="utf-8")
    (repo / "link.csv").symlink_to(outside)
    with pytest.raises(SnapshotError, match="escapes the repository"):
        resolve_in_repo(repo, "link.csv")
