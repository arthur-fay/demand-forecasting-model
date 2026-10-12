"""Command-line interface, exit codes and output formats."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from atlas import __version__
from atlas.cli import EXIT_FAILED, EXIT_OK, EXIT_USAGE, main
from tests.conftest import REPO_ROOT


def run_cli(*args: str) -> tuple[int, str]:
    buffer = io.StringIO()
    code = main(list(args), stdout=buffer)
    return code, buffer.getvalue()


@pytest.mark.integration
def test_check_passes_and_prints_every_stage() -> None:
    code, output = run_cli("--repo-root", str(REPO_ROOT), "check")
    assert code == EXIT_OK
    for line in ("[PASS] integrity", "[PASS] validation", "[PASS] reproduction"):
        assert line in output
    assert "LEGACY-V0-EX-001" in output
    assert "NOT measured operational service capacity" in output


@pytest.mark.integration
def test_json_output_is_machine_readable() -> None:
    code, output = run_cli("--repo-root", str(REPO_ROOT), "--format", "json", "check")
    payload = json.loads(output)
    assert code == EXIT_OK
    assert payload["ok"] is True
    assert payload["snapshot"] == "legacy-v0"
    assert [r["name"] for r in payload["reports"]] == ["integrity", "validation", "reproduction"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("command", "last_stage"),
    [
        (("snapshot", "verify"), "integrity"),
        (("validate",), "validation"),
        (("reproduce",), "reproduction"),
    ],
)
def test_stage_commands(command: tuple[str, ...], last_stage: str) -> None:
    code, output = run_cli("--repo-root", str(REPO_ROOT), "--format", "json", *command)
    assert code == EXIT_OK
    assert json.loads(output)["reports"][-1]["name"] == last_stage


@pytest.mark.integration
def test_manifest_command_prints_committed_manifest_without_writing(tmp_path: Path) -> None:
    manifest = REPO_ROOT / "snapshots" / "legacy-v0" / "manifest.json"
    before = manifest.stat().st_mtime_ns
    code, output = run_cli("--repo-root", str(REPO_ROOT), "snapshot", "manifest")
    assert code == EXIT_OK
    assert output == manifest.read_text(encoding="utf-8")
    assert manifest.stat().st_mtime_ns == before


def test_tampered_snapshot_fails_closed(repo_copy: Path) -> None:
    target = repo_copy / "01 Data" / "daily_segments.csv"
    target.write_bytes(target.read_bytes().replace(b"BROOKLYN", b"BROOKLYM", 1))
    code, output = run_cli("--repo-root", str(repo_copy), "check")
    assert code == EXIT_FAILED
    assert "[FAIL] integrity" in output
    assert "[SKIP] validation: snapshot integrity check failed" in output
    assert "[SKIP] reproduction: snapshot integrity check failed" in output


def test_invalid_data_skips_reproduction(repo_copy: Path) -> None:
    """Contract violations stop the pipeline even if the manifest is regenerated to match."""
    target = repo_copy / "01 Data" / "daily_total.csv"
    target.write_bytes(target.read_bytes().replace(b"2023-01-02,", b"2023-01-01,", 1))
    manifest_path = repo_copy / "snapshots" / "legacy-v0" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(f for f in manifest["files"] if f["table"] == "daily_total")
    entry["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    code, output = run_cli("--repo-root", str(repo_copy), "check")
    assert code == EXIT_FAILED
    assert "[PASS] integrity" in output
    assert "[FAIL] validation" in output
    assert "duplicate_key" in output
    assert "missing_dates" in output
    assert "[SKIP] reproduction: data validation failed" in output


@pytest.mark.parametrize(
    "args",
    [("--snapshot", "../escape", "check"), ("--snapshot", "legacy-v9", "check")],
)
def test_configuration_errors_exit_with_usage_code(
    args: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = run_cli("--repo-root", str(REPO_ROOT), *args)
    assert code == EXIT_USAGE
    assert "atlas: error:" in capsys.readouterr().err


def test_python_module_entry_point_reports_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "atlas", "--version"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"atlas {__version__}"
