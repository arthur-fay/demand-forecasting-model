"""Release manifests: identification, fingerprints and refusal of altered releases; and the
committed research artifacts, which M6 must not change.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from atlas.deploy import cli
from atlas.deploy.manifest import (
    RESEARCH_PATHS,
    ManifestError,
    build_manifest,
    check_release,
    load_manifest,
    research_files,
    runtime_packages,
    scientific_status,
)
from tests.conftest import REPO_ROOT
from tests.m6_helpers import make_release, write

#: The merge commit of M5 (arthur-fay/demand-forecasting-model#7), the base of M6.
M5_MERGE = "3f48f2049d13fb28c00fef21cecd8c68d25958a6"


def test_the_manifest_identifies_the_release(tmp_path: Path) -> None:
    release = make_release(tmp_path)
    manifest = load_manifest(release)
    assert manifest["release_id"] == release.name
    assert manifest["git_commit"] == "a" * 40
    assert manifest["atlas_version"] == release.name.split("-")[0]
    assert manifest["python"]["version"] == "3.12.3"
    assert (
        manifest["dependencies"]["lock_sha256"]
        == hashlib.sha256((REPO_ROOT / "uv.lock").read_bytes()).hexdigest()
    )
    assert manifest["snapshot"] == {
        "snapshot_id": "legacy-v0",
        "manifest_sha256": hashlib.sha256(
            (REPO_ROOT / "snapshots/legacy-v0/manifest.json").read_bytes()
        ).hexdigest(),
        "data_fingerprint": "5ab8b3a59e107158da9a3a285ea58a22c566f5b0da067e0f3fae6ee5e3d73a4d",
    }
    assert manifest["scientific_status"] == {
        "m3_interval_status": "failed_calibration",
        "m3_decision_grade": False,
        "m4_parameter_provenance": "HYPOTHETICAL",
        "m4_validated_recommendation": False,
        "point_forecast_validated": False,
        "validated_recommendation": False,
    }
    assert manifest["research_artifacts"]["files"] == research_files(REPO_ROOT)
    assert check_release(release) == []


def test_runtime_packages_are_the_locked_production_set() -> None:
    packages = runtime_packages(REPO_ROOT)
    assert packages["numpy"]
    assert packages["scipy"] == "1.18.1"
    assert packages["fastapi"]
    assert not {"pytest", "mypy", "ruff", "hypothesis", "httpx2"} & set(packages)


@pytest.mark.parametrize(
    ("relative", "fragment"),
    [
        ("docs/results/atlas-m4-optimization-v1/metrics.json", "research artifact altered"),
        ("01 Data/daily_total.csv", "research artifact altered"),
        ("uv.lock", "uv.lock differs"),
        ("docs/api/openapi.json", "openapi.json differs"),
        ("deploy/templates/atlas.service.in", "release file altered"),
        (".venv/bin/atlas", "environment (.venv) differs"),
    ],
)
def test_an_altered_release_is_refused(tmp_path: Path, relative: str, fragment: str) -> None:
    release = make_release(tmp_path)
    target = release / relative
    target.chmod(0o644)
    with target.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    problems = check_release(release)
    assert any(fragment in problem for problem in problems), problems


def test_added_and_removed_files_are_refused(tmp_path: Path) -> None:
    release = make_release(tmp_path)
    write(release / "src/atlas/backdoor.py", "import os\n")
    (release / "configs/optimization/smoke.json").unlink()
    problems = check_release(release)
    assert any("src/atlas/backdoor.py" in p for p in problems)
    assert any("research artifact missing: configs/optimization/smoke.json" in p for p in problems)


def test_bytecode_written_later_does_not_alter_the_release(tmp_path: Path) -> None:
    """Root running the release's Python writes ``__pycache__``: derived, not identity."""
    release = make_release(tmp_path)
    for relative in ("src/atlas/__pycache__/cli.cpython-312.pyc", ".venv/lib/x/__pycache__/y.pyc"):
        write(release / relative, "bytecode")
    assert check_release(release) == []
    write(release / "src/atlas/__pycache__/planted.py", "import os\n")  # not bytecode
    assert any("planted.py" in p for p in check_release(release))


def test_manifest_errors(tmp_path: Path) -> None:
    release = make_release(tmp_path)
    renamed = release.with_name("9.9.9-bbbbbbbbbbbb")
    shutil.copytree(release, renamed, symlinks=True)
    assert any("describes" in p for p in check_release(renamed))
    (release / "release.json").write_text("[]")
    assert check_release(release) == [f"{release / 'release.json'} is not a JSON object"]
    with pytest.raises(ManifestError, match="40-character"):
        build_manifest(
            renamed, release_id=renamed.name, commit="abc", built_at="", uv_version="", checks=[]
        )
    with pytest.raises(ManifestError, match="does not match"):
        build_manifest(
            renamed, release_id=renamed.name, commit="b" * 40, built_at="", uv_version="", checks=[]
        )
    with pytest.raises(ManifestError, match="invalid release id"):
        build_manifest(
            renamed, release_id="../x", commit="b" * 40, built_at="", uv_version="", checks=[]
        )


def test_the_manifest_commands(tmp_path: Path) -> None:
    release = make_release(tmp_path)
    out = io.StringIO()
    assert cli.main(["manifest", "check", str(release)], stdout=out) == 0
    assert "matches its manifest" in out.getvalue()
    out = io.StringIO()
    assert cli.main(["manifest", "show", str(release)], stdout=out) == 0
    assert json.loads(out.getvalue())["release_id"] == release.name
    (release / "release.json").unlink()
    out = io.StringIO()
    assert (
        cli.main(
            [
                "manifest",
                "write",
                str(release),
                "--commit",
                "a" * 40,
                "--uv-version",
                "0.11.32",
                "--check",
                "atlas check",
            ],
            stdout=out,
        )
        == 0
    )
    assert load_manifest(release)["build"]["checks_passed"] == ["atlas check"]
    (release / "uv.lock").write_text("changed")
    assert cli.main(["manifest", "check", str(release)], stdout=io.StringIO()) == 1


def test_scientific_status_is_read_from_the_committed_files() -> None:
    status = scientific_status(REPO_ROOT)
    assert status["m3_interval_status"] == "failed_calibration"
    assert status["m3_decision_grade"] is False


# --- The research artifacts are unchanged by M6 -------------------------------------------------


def _git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(REPO_ROOT), *args],  # noqa: S607
        capture_output=True,
        check=False,
    )


def test_every_research_artifact_is_byte_identical_to_the_m5_merge() -> None:
    """The deployment work changes no committed research output (M6 scope rule)."""
    if _git("cat-file", "-e", f"{M5_MERGE}^{{commit}}").returncode != 0:
        pytest.skip("the M5 merge commit is not in this clone (shallow checkout)")
    listed = _git("ls-tree", "-r", "--name-only", M5_MERGE, "--", *RESEARCH_PATHS)
    names = [n for n in listed.stdout.decode().splitlines() if n]
    assert len(names) > 50
    current = research_files(REPO_ROOT)
    assert sorted(current) == sorted(names)  # nothing added or removed
    for name in names:
        committed = _git("show", f"{M5_MERGE}:{name}").stdout
        assert current[name] == hashlib.sha256(committed).hexdigest(), name
