"""The release-candidate manifest tool (M8): docs/release/rc_manifest.py.

The tool fingerprints the content git has committed and refuses a checkout that is not clean.
These tests run the working-tree version of the tool (``--root``) on a temporary repository
that holds this checkout's committed tree (``git archive HEAD``) in one commit, followed by a
commit adding the manifest the tool wrote for it. They therefore need neither a clean checkout
nor any git history (a checkout may be shallow). Each test works on its own clone, with git
isolated from the user's and the system's configuration.

Since 1.1.0-rc.1 (schema 2) the manifest also records every declaration of the version, the
static export and site with the publication record, and the earlier candidates' manifests,
which stay as they were issued: the M8 manifest of 1.0.0-rc.1 is checked here byte for byte.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO_ROOT

TOOL = REPO_ROOT / "docs" / "release" / "rc_manifest.py"
SNAPSHOT_MANIFEST = "snapshots/legacy-v0/manifest.json"
#: The M8 candidate's manifest, as issued (docs/release/RELEASE_CANDIDATE.md): never rewritten.
M8_MANIFEST = "docs/release/atlas-1.0.0-rc.1.json"
M8_MANIFEST_SHA256 = "18a985f4a8beb1f0f35ea2a31637a9c6479157bd91e23c73a94d5b025516ae47"
M8_TREE_FINGERPRINT = "35b5b79f7683fdde3564130c91be6b8105fabb0e8e09009352a1450961f32a16"
STATIC_EXPORT_MANIFEST = "web/static-export/atlas-static-export.json"
STATIC_SITE = "web/atlas-static-site.json"
PUBLICATION_RECORD = "docs/release/PAGES_PUBLICATION.json"
M2_RECOMMENDATION = "docs/results/atlas-m2-rolling-origin-v1/full/recommendation.json"
M3_MANIFEST = "docs/results/atlas-m3-intervals-v1/full/manifest.json"
M4_CONFIG = "configs/optimization/atlas-m4-optimization-v1.json"
M4_MANIFEST = "docs/results/atlas-m4-optimization-v1/manifest.json"
#: The identity and defaults of every git call in the temporary repositories (whose objects
#: are stored uncompressed, which keeps writing and reading them fast).
GIT_CONFIG = (
    *("-c", "user.name=ATLAS tests", "-c", "user.email=atlas-tests@example.invalid"),
    *("-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false"),
    *("-c", "core.compression=0"),
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _environment(**extra: str) -> dict[str, str]:
    """This environment without git's variables and without the global and system git
    configuration (Git for Windows sets core.autocrlf=true there)."""
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", **extra)
    return environment


def _git(cwd: Path, *args: str, succeeds: bool = True) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(cwd), *GIT_CONFIG, *args],  # noqa: S607
        capture_output=True,
        text=True,
        env=_environment(),
        check=False,
    )
    assert (result.returncode == 0) == succeeds, result.stderr
    return result.stdout


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _tool(root: Path, *args: str, **environment: str) -> subprocess.CompletedProcess[str]:
    """Run the tool as a reviewer would: plain Python, standard library only (-S keeps this
    virtual environment's packages off the path)."""
    return subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-S", str(TOOL), *args, "--root", str(root)],
        capture_output=True,
        text=True,
        env=_environment(**environment),
        check=False,
    )


def _lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return result.stderr.splitlines()


def _append(path: Path, data: bytes) -> None:
    path.write_bytes(path.read_bytes() + data)


def _edit_json(path: Path, edit: Callable[[Any], object]) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    edit(document)
    path.write_text(json.dumps(document), encoding="utf-8")


def _manifest(repo: Path) -> str:
    """Where the tool writes the manifest in ``repo``: named after its committed version."""
    project = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return f"docs/release/atlas-{project['version']}-rc.1.json"


@pytest.fixture(scope="module")
def origin(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("this copy of the repository is not a git checkout")
    archive = subprocess.run(  # noqa: S603
        ["git", "-C", str(REPO_ROOT), "-c", "core.autocrlf=false", "archive", "HEAD"],  # noqa: S607
        capture_output=True,
        env=_environment(),
        check=False,
    )
    assert archive.returncode == 0, archive.stderr.decode()
    repo = tmp_path_factory.mktemp("rc-manifest") / "origin"
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(repo, filter="data")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-q", "-m", "the committed tree")
    written = _tool(repo, "--write")
    assert written.returncode == 0, written.stderr
    assert written.stdout == f"wrote {_manifest(repo)}\n"
    _commit(repo, "the release-candidate manifest")
    return repo


@pytest.fixture(scope="module")
def manifest(origin: Path) -> str:
    return _manifest(origin)


@pytest.fixture
def clone(origin: Path, tmp_path: Path) -> Path:
    _git(tmp_path, "clone", "-q", str(origin), "work")
    return tmp_path / "work"


def test_a_written_manifest_checks_and_records_the_committed_content(
    clone: Path, manifest: str
) -> None:
    checked = _tool(clone, "--check")
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout.startswith(f"OK: the checkout matches {manifest}\n")

    text = (clone / manifest).read_bytes().decode("utf-8")
    document = json.loads(text)
    assert text == json.dumps(document, indent=2, sort_keys=True) + "\n"
    assert "\r" not in text
    assert document["schema"] == "atlas-release-candidate/2"
    index = {}
    for record in _git(clone, "ls-files", "-s", "-z").split("\0")[:-1]:
        info, path = record.split("\t", 1)
        index[path] = info.split(" ")[0]
    files = document["source"]["files"]
    assert sorted(files) == sorted(p for p in index if p != manifest)
    assert document["source"]["tracked_files"] == len(files)
    assert {p: entry.split(" ")[0] for p, entry in files.items()} == {
        p: mode for p, mode in index.items() if p != manifest
    }
    readme = hashlib.sha256((clone / "README.md").read_bytes()).hexdigest()
    assert files["README.md"] == f"100644 {readme}"
    assert document["locks"]["uv.lock"] == files["uv.lock"].split(" ")[1]

    # The status separates what the committed results record from what the release declares.
    assert "status" not in document
    assert document["declared"]["status"].startswith("release candidate under review: ")
    status = document["scientific_status"]
    assert sorted(status) == ["declared", "recorded"]
    config = (clone / M4_CONFIG).read_text(encoding="utf-8")
    hypothetical = config.count('"provenance": "HYPOTHETICAL"')
    assert hypothetical == config.count('"provenance"') > 0
    assert status["recorded"]["m4_provenance_declarations"] == {"HYPOTHETICAL": hypothetical}
    assert status["recorded"]["m4_validated_recommendation"] is False
    assert status["declared"]["validated_recommendation"] is False
    m3 = json.loads((clone / M3_MANIFEST).read_text(encoding="utf-8"))
    assert document["snapshot"]["data_fingerprint"] == m3["data"]["data_fingerprint"]
    assert M3_MANIFEST in document["snapshot"]["data_fingerprint_recorded_in"]
    for declared in ("m2_advantage", "m3_intervals", "m4_parameters", "nature"):
        assert status["declared"][declared], declared
    assert status["declared"]["m3_intervals"].startswith("FAILED_CALIBRATION")

    # (1.1.0) Every declaration of the version is the committed version.
    version = document["version"]
    assert manifest == f"docs/release/atlas-{version}-rc.1.json"
    assert len(document["versions"]) == 11
    assert set(document["versions"].values()) == {version}
    # Its own release documents are named after its version; the M8 ones stay as issued.
    documents = document["documents"]
    assert documents["release_gate"] == f"docs/release/RELEASE_GATE_{version}.md"
    assert documents["release_candidate"] == f"docs/release/RELEASE_CANDIDATE_{version}.md"
    assert documents["publication_record"] == PUBLICATION_RECORD
    assert all((clone / path).is_file() for path in documents.values())
    # The static export, the site and the publication record, as committed.
    static = document["static_site"]
    export = static["export"]
    assert export["manifest_sha256"] == files[STATIC_EXPORT_MANIFEST].split(" ")[1]
    assert sorted(export["files"]) == sorted(
        p for p in files if p.startswith("web/static-export/") and p != STATIC_EXPORT_MANIFEST
    )
    assert export["scientific_status"]["uncertainty_interval_status"] == "failed_calibration"
    assert export["scientific_status"]["validated_recommendation"] is False
    assert static["site"]["manifest_sha256"] == files[STATIC_SITE].split(" ")[1]
    record = json.loads((clone / PUBLICATION_RECORD).read_text(encoding="utf-8"))
    publication = static["publication"]
    assert publication["sha256"] == files[PUBLICATION_RECORD].split(" ")[1]
    assert publication["conditions"] == record["conditions"]
    assert publication["every_condition_true"] is all(record["conditions"].values())
    assert publication["release_version"] == record["release_version"]
    # Earlier candidates are recorded, not rewritten.
    if manifest != M8_MANIFEST:
        assert document["candidates"][M8_MANIFEST] == {
            "sha256": M8_MANIFEST_SHA256,
            "schema": "atlas-release-candidate/1",
            "candidate": "1.0.0-rc.1",
            "generated_from_commit": "39fa4c70a2140725804408f6a7fbbaf31e66f7f4",
            "tree_fingerprint": M8_TREE_FINGERPRINT,
            "tracked_files": 441,
        }
        assert (clone / M8_MANIFEST).read_bytes() == (REPO_ROOT / M8_MANIFEST).read_bytes()

    # Writing is deterministic; a manifest written but not yet committed checks as well.
    _append(clone / "README.md", b"\nA committed change.\n")
    _commit(clone, "a change")
    assert _tool(clone, "--write").returncode == 0
    first = (clone / manifest).read_bytes()
    assert _tool(clone, "--write").returncode == 0
    assert (clone / manifest).read_bytes() == first
    assert _git(clone, "status", "--porcelain") == f" M {manifest}\n"
    rechecked = _tool(clone, "--check")
    assert rechecked.returncode == 0
    assert rechecked.stdout.splitlines()[-1] == (
        "note: the manifest is modified: the checkout matches it, not a committed one"
    )
    assert "note:" not in checked.stdout


def test_a_committed_change_is_named(clone: Path, manifest: str) -> None:
    _append(clone / "README.md", b"\nchanged\n")
    _append(clone / "web/dist/inter-licence.txt", b"\nchanged\n")
    (clone / "src/atlas/added_by_test.py").write_text('"""Added."""\n', encoding="utf-8")
    _git(clone, "rm", "-q", ".env.example")
    _commit(clone, "changes outside and inside the areas")
    result = _tool(clone, "--check")
    assert result.returncode == 1
    assert result.stdout == ""
    assert _lines(result) == [
        f"MISMATCH: the checkout differs from {manifest}",
        "  removed: .env.example",
        "  changed: README.md",
        "  added: src/atlas/added_by_test.py",
        "  changed: web/dist/inter-licence.txt",
        "  sections that differ: dashboard_build, source",
    ]

    for number in range(60):
        (clone / "extra" / f"{number:02d}.txt").parent.mkdir(exist_ok=True)
        (clone / "extra" / f"{number:02d}.txt").write_text(f"{number}\n", encoding="utf-8")
    _commit(clone, "sixty more files")
    lines = _lines(_tool(clone, "--check"))
    assert len(lines) == 1 + 50 + 2
    assert lines[-2] == "  ... and 14 more (64 in total)"


def test_a_committed_mode_change_is_named(clone: Path, manifest: str) -> None:
    (clone / "README.md").chmod(0o755)
    _git(clone, "update-index", "--chmod=+x", "README.md")
    _commit(clone, "README.md made executable")
    result = _tool(clone, "--check")
    assert result.returncode == 1
    assert _lines(result) == [
        f"MISMATCH: the checkout differs from {manifest}",
        "  mode changed: README.md (100644 -> 100755)",
        "  sections that differ: source",
    ]


def test_an_unclean_checkout_is_refused_and_named(clone: Path, manifest: str) -> None:
    committed = (clone / manifest).read_bytes()
    _append(clone / "README.md", b"\nnot committed\n")
    (clone / "src/atlas/evil.py").write_text("x = 1\n", encoding="utf-8")
    (clone / "web/dist/stray.txt").write_text("stray\n", encoding="utf-8")
    (clone / "src/atlas/intended.py").write_text("y = 2\n", encoding="utf-8")
    _git(clone, "add", "-N", "src/atlas/intended.py")
    _git(clone, "rm", "-q", ".env.example")
    _git(clone, "mv", "docs/DATA_CARD.md", "docs/DATA_CARD_MOVED.md")
    # Changes git status does not compare (a sparse checkout marks entries skip-worktree).
    _git(clone, "update-index", "--assume-unchanged", "src/atlas/__init__.py")
    _git(clone, "update-index", "--skip-worktree", "web/index.html")
    for mode in ("--check", "--write"):
        result = _tool(clone, mode)
        assert result.returncode == 1, mode
        assert _lines(result) == [
            f"refusing to {mode[2:]}: the checkout is not clean (8 path(s) besides "
            f"{manifest}); commit or remove them, or use a fresh clone",
            "  deleted: .env.example",
            "  modified: README.md",
            "  renamed: docs/DATA_CARD.md -> docs/DATA_CARD_MOVED.md",
            "  added, not committed: src/atlas/intended.py",
            "  untracked: src/atlas/evil.py",
            "  untracked: web/dist/stray.txt",
            "  hidden from git status (assume-unchanged): src/atlas/__init__.py",
            "  hidden from git status (skip-worktree): web/index.html",
        ]
    assert (clone / manifest).read_bytes() == committed

    _git(clone, "update-index", "--no-assume-unchanged", "src/atlas/__init__.py")
    _git(clone, "update-index", "--no-skip-worktree", "web/index.html")
    _git(clone, "reset", "-q", "--hard")
    _git(clone, "clean", "-q", "-f")
    assert _git(clone, "status", "--porcelain") == ""
    # Ignored files are not part of the candidate.
    for ignored in (".venv/bin/python", "web/node_modules/a/index.js", "web/dist/.DS_Store"):
        (clone / ignored).parent.mkdir(parents=True, exist_ok=True)
        (clone / ignored).write_text("ignored\n", encoding="utf-8")
    assert _tool(clone, "--check").returncode == 0

    # An uncommitted mode change, where the filesystem records modes (core.fileMode).
    if _git(clone, "config", "--bool", "core.fileMode").strip() == "true":
        (clone / "README.md").chmod(0o755)
        assert _lines(_tool(clone, "--check"))[1:] == ["  modified: README.md"]
        (clone / "README.md").chmod(0o644)

    # A staged rename of pyproject.toml (whose version names the manifest) and an unresolved
    # merge (which leaves no committed content to read) are refused like any other change.
    _git(clone, "mv", "pyproject.toml", "pyproject.old")
    result = _tool(clone, "--check")
    assert result.returncode == 1
    assert _lines(result) == [
        "refusing to check: the checkout is not clean (1 path(s) besides the manifest); "
        "commit or remove them, or use a fresh clone",
        "  renamed: pyproject.toml -> pyproject.old",
    ]
    _git(clone, "reset", "-q", "--hard")
    _git(clone, "checkout", "-q", "-b", "other")
    _append(clone / "README.md", b"\nother\n")
    _commit(clone, "a change on another branch")
    _git(clone, "checkout", "-q", "-")
    _append(clone / "README.md", b"\nthis branch\n")
    _commit(clone, "a conflicting change")
    _git(clone, "merge", "-q", "other", succeeds=False)
    result = _tool(clone, "--check")
    assert result.returncode == 1
    assert _lines(result) == [
        "refusing to check: the checkout is not clean (1 path(s) besides the manifest); "
        "commit or remove them, or use a fresh clone",
        "  unmerged: README.md",
    ]


def test_the_m8_candidate_manifest_is_kept_as_issued() -> None:
    """1.0.0-rc.1's manifest is the M8 record: the 1.1.0 preparation adds its own candidate
    (atlas-1.1.0-rc.1.json) beside it, and never regenerates or edits it. It is checked with
    the tool of the M8 merge (7d3639c), whose tree it fingerprints (docs/STATIC_DEMO.md 11)."""
    data = (REPO_ROOT / M8_MANIFEST).read_bytes()
    assert hashlib.sha256(data).hexdigest() == M8_MANIFEST_SHA256
    document = json.loads(data)
    assert (document["schema"], document["version"], document["candidate"]) == (
        "atlas-release-candidate/1",
        "1.0.0",
        "1.0.0-rc.1",
    )
    assert document["source"]["tree_fingerprint"] == M8_TREE_FINGERPRINT
    assert document["source"]["tracked_files"] == 441
    assert document["documents"]["release_gate"] == "docs/release/RELEASE_GATE.md"
    assert document["documents"]["release_candidate"] == "docs/release/RELEASE_CANDIDATE.md"


def test_contradictory_version_and_static_records_are_refused(clone: Path, manifest: str) -> None:
    """A version left behind by a partial bump (docs/DEVELOPMENT.md, "Versioning"), a static site
    built from another export and an export that does not list its answers are each named on
    one line, and the tool stops (exit 2) instead of fingerprinting them."""
    version = tomllib.loads((clone / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    release = version["version"]
    answer = "web/static-export/history.json"
    cases: list[tuple[str, Callable[[Path], object], str]] = [
        (
            "web/package.json",
            lambda path: _edit_json(path, lambda d: d.update(version="9.9.9")),
            "the committed version declarations differ from pyproject.toml's "
            f"{release!r}: web/package.json version is '9.9.9'",
        ),
        (
            STATIC_SITE,
            lambda path: _edit_json(path, lambda d: d.update(version="9.9.9")),
            "the committed version declarations differ from pyproject.toml's "
            f"{release!r}: {STATIC_SITE} version is '9.9.9'",
        ),
        (
            answer,
            lambda path: _edit_json(path, lambda d: d["provenance"].update(atlas_version="9.9.9")),
            "the committed version declarations differ from pyproject.toml's "
            f"{release!r}: web/static-export answers' provenance.atlas_version is "
            f"[{release!r}, '9.9.9']",
        ),
        (
            answer,
            lambda path: _append(path, b"\n"),
            f"{STATIC_EXPORT_MANIFEST} does not list the committed answers",
        ),
        (
            STATIC_SITE,
            lambda path: _edit_json(path, lambda d: d.update(export_manifest_sha256="0" * 64)),
            f"{STATIC_SITE} names another export than {STATIC_EXPORT_MANIFEST}",
        ),
    ]
    for path, edit, message in cases:
        edit(clone / path)
        _commit(clone, f"an inconsistent {path}")
        for mode in ("--check", "--write"):
            result = _tool(clone, mode)
            assert (result.returncode, result.stderr) == (2, f"rc_manifest: error: {message}\n")
        _git(clone, "reset", "-q", "--hard", "HEAD~1")
    assert _tool(clone, "--check").returncode == 0


def test_a_crlf_checkout_matches_the_manifest(origin: Path, manifest: str, tmp_path: Path) -> None:
    """core.autocrlf=true (the Git for Windows default) rewrites every text file that no
    .gitattributes rule protects, including the locks, the sources and the manifest itself;
    git status still calls the checkout clean, and so must the tool (537563c: MISMATCH)."""
    _git(tmp_path, "clone", "-q", "-c", "core.autocrlf=true", str(origin), "windows")
    windows = tmp_path / "windows"
    for converted in ("README.md", "uv.lock", "src/atlas/__init__.py", manifest):
        assert b"\r\n" in (windows / converted).read_bytes(), converted
    assert _git(windows, "status", "--porcelain") == ""
    result = _tool(windows, "--check")
    assert result.returncode == 0, result.stderr
    assert result.stdout == _tool(origin, "--check").stdout


def test_the_status_is_read_from_the_committed_results(clone: Path, manifest: str) -> None:
    config = (clone / M4_CONFIG).read_text(encoding="utf-8")
    hypothetical = config.count('"provenance": "HYPOTHETICAL"')
    _edit_json(clone / M4_MANIFEST, lambda m4: m4.update(validated_recommendation=True))
    measured = {"provenance": "MEASURED"}
    _edit_json(clone / M4_CONFIG, lambda config: config["discretionary_budget"].update(measured))
    _commit(clone, "an M4 run claiming a validated recommendation and one measured parameter")
    assert _tool(clone, "--write").returncode == 0
    written = json.loads((clone / manifest).read_text(encoding="utf-8"))
    recorded = written["scientific_status"]["recorded"]
    assert recorded["m4_validated_recommendation"] is True
    assert recorded["m4_provenance_declarations"] == {
        "HYPOTHETICAL": hypothetical - 1,
        "MEASURED": 1,
    }

    # The M2, M3 and M4 results must record one data fingerprint, computed from the committed
    # snapshot manifest; an inconsistency is named on one line, and the tool stops (exit 2).
    reference = json.loads((clone / M2_RECOMMENDATION).read_text(encoding="utf-8"))
    other = "0" * 64
    cases: list[tuple[str, Callable[[Any], object], str]] = [
        (
            M3_MANIFEST,
            lambda m3: m3["data"].update(data_fingerprint=other),
            "the committed results record different data fingerprints: "
            f"{M2_RECOMMENDATION} records {reference['data_fingerprint']}, "
            f"but {M3_MANIFEST} records {other}",
        ),
        (
            M3_MANIFEST,
            lambda m3: m3["data"].pop("data_fingerprint"),
            f"{M3_MANIFEST} records no data fingerprint",
        ),
        (
            M4_MANIFEST,
            lambda m4: m4["data"].update(manifest_sha256=other),
            f"{M4_MANIFEST} was not computed from the committed {SNAPSHOT_MANIFEST}",
        ),
    ]
    for path, edit, message in cases:
        _edit_json(clone / path, edit)
        _commit(clone, f"an inconsistent {path}")
        result = _tool(clone, "--check")
        assert (result.returncode, result.stderr) == (2, f"rc_manifest: error: {message}\n")
        _git(clone, "reset", "-q", "--hard", "HEAD~1")

    # The release documents the manifest lists must be committed.
    _git(clone, "rm", "-q", "docs/MODEL_CARD.md")
    _commit(clone, "the model card removed")
    result = _tool(clone, "--write")
    assert (result.returncode, result.stderr) == (
        2,
        "rc_manifest: error: docs/MODEL_CARD.md is not committed\n",
    )


def test_the_tool_fails_clearly_where_it_cannot_run(
    tmp_path: Path, clone: Path, manifest: str
) -> None:
    # A manifest file that holds JSON but no manifest is a mismatch (exit 1), not a failure.
    (clone / manifest).write_text("[]\n", encoding="utf-8")
    result = _tool(clone, "--check")
    assert (result.returncode, result.stderr) == (1, f"MISMATCH: {manifest} is not a JSON object\n")
    _git(clone, "checkout", "--", manifest)

    outside = tmp_path / "outside"
    outside.mkdir()
    no_git = tmp_path / "empty-path"
    no_git.mkdir()
    cases = [
        (
            outside,
            {"GIT_CEILING_DIRECTORIES": str(tmp_path)},
            f"{outside} is not a usable git checkout (git rev-parse failed: fatal: not a git "
            "repository",
        ),
        (clone / "docs", {}, f"{clone / 'docs'} is not the top level of its git checkout"),
        (clone, {"PATH": str(no_git)}, "git is required but was not found on PATH"),
    ]
    if os.name == "posix":
        # git refusing a repository it does not trust: its 'fatal:' line, not the hint after it.
        refusing = tmp_path / "refusing-git"
        refusing.mkdir()
        (refusing / "git").write_text(
            "#!/bin/sh\n"
            "echo \"fatal: detected dubious ownership in repository at '/srv/atlas'\" >&2\n"
            "echo 'To add an exception for this directory, call:' >&2\n"
            "echo >&2\n"
            "echo '\tgit config --global --add safe.directory /srv/atlas' >&2\n"
            "exit 128\n",
            encoding="utf-8",
        )
        (refusing / "git").chmod(0o755)
        message = (
            f"{clone} is not a usable git checkout (git rev-parse failed: fatal: detected "
            "dubious ownership in repository at '/srv/atlas')"
        )
        cases.append((clone, {"PATH": str(refusing)}, message))
    for root, environment, message in cases:
        result = _tool(root, "--check", **environment)
        assert result.returncode == 2, message
        assert result.stdout == ""
        assert len(_lines(result)) == 1, result.stderr
        assert result.stderr.startswith(f"rc_manifest: error: {message}")

    # A submodule's content is not in this repository, so it cannot be fingerprinted.
    head = _git(clone, "rev-parse", "HEAD").strip()
    _git(clone, "update-index", "--add", "--cacheinfo", f"160000,{head},vendored")
    (clone / "vendored").mkdir()
    _git(clone, "commit", "-q", "-m", "a submodule")
    result = _tool(clone, "--check")
    assert (result.returncode, result.stderr) == (
        2,
        "rc_manifest: error: vendored is a submodule, whose content cannot be fingerprinted\n",
    )

    # An interpreter older than 3.11 (no tomllib) is named before anything else runs.
    older = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            "import runpy, sys; sys.version_info = (3, 10, 14, 'final', 0); "
            f"sys.argv = ['rc_manifest.py', '--check']; runpy.run_path({str(TOOL)!r}, "
            "run_name='__main__')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert older.returncode == 2
    assert older.stderr == "rc_manifest: error: Python 3.11 or later is required, not 3.10.14\n"
