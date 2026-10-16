"""The release-candidate manifest tool (M8): docs/release/rc_manifest.py.

The tool fingerprints the content git has committed and refuses a checkout that is not clean.
These tests run the working-tree version of the tool (``--root``) on a temporary repository
that holds this checkout's committed tree (``git archive HEAD``) in one commit, followed by a
commit adding the manifest the tool wrote for it. They therefore need neither a clean checkout
nor any git history (CI checks out a shallow clone). Each test works on its own clone, with git
isolated from the user's and the system's configuration.
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
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

TOOL = REPO_ROOT / "docs" / "release" / "rc_manifest.py"
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


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(cwd), *GIT_CONFIG, *args],  # noqa: S607
        capture_output=True,
        text=True,
        env=_environment(),
        check=False,
    )
    assert result.returncode == 0, result.stderr
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
    assert document["schema"] == "atlas-release-candidate/1"
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
    status = document["scientific_status"]
    assert sorted(status) == ["declared", "recorded"]
    config = (clone / M4_CONFIG).read_text(encoding="utf-8")
    hypothetical = config.count('"provenance": "HYPOTHETICAL"')
    assert hypothetical == config.count('"provenance"') > 0
    assert status["recorded"]["m4_parameter_provenance"] == {"HYPOTHETICAL": hypothetical}
    assert status["recorded"]["m4_validated_recommendation"] is False
    assert status["declared"]["validated_recommendation"] is False
    m3 = json.loads((clone / M3_MANIFEST).read_text(encoding="utf-8"))
    assert document["snapshot"]["data_fingerprint"] == m3["data"]["data_fingerprint"]
    assert M3_MANIFEST in document["snapshot"]["data_fingerprint_recorded_in"]

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
    for mode in ("--check", "--write"):
        result = _tool(clone, mode)
        assert result.returncode == 1, mode
        assert _lines(result) == [
            f"refusing to {mode[2:]}: the checkout is not clean (3 path(s) besides "
            f"{manifest}); commit or remove them, or use a fresh clone",
            "  modified: README.md",
            "  untracked: src/atlas/evil.py",
            "  untracked: web/dist/stray.txt",
        ]
    assert (clone / manifest).read_bytes() == committed

    _git(clone, "checkout", "--", "README.md")
    (clone / "src/atlas/evil.py").unlink()
    (clone / "web/dist/stray.txt").unlink()
    # Ignored files are not part of the candidate.
    for ignored in (".venv/bin/python", "web/node_modules/a/index.js", "web/dist/.DS_Store"):
        (clone / ignored).parent.mkdir(parents=True, exist_ok=True)
        (clone / ignored).write_text("ignored\n", encoding="utf-8")
    assert _tool(clone, "--check").returncode == 0

    # A change hidden from git status is refused as well.
    _git(clone, "update-index", "--assume-unchanged", "README.md")
    _append(clone / "README.md", b"\nhidden\n")
    assert _lines(_tool(clone, "--check"))[1:] == [
        "  hidden from git status (assume-unchanged): README.md"
    ]
    _git(clone, "update-index", "--no-assume-unchanged", "README.md")
    _git(clone, "checkout", "--", "README.md")

    if os.name == "posix":  # with core.fileMode, as on Linux and macOS
        (clone / "README.md").chmod(0o755)
        assert _lines(_tool(clone, "--check"))[1:] == ["  modified: README.md"]


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
    m4 = json.loads((clone / M4_MANIFEST).read_text(encoding="utf-8"))
    m4["validated_recommendation"] = True
    (clone / M4_MANIFEST).write_text(json.dumps(m4), encoding="utf-8")
    _commit(clone, "an M4 manifest claiming a validated recommendation")
    assert _tool(clone, "--write").returncode == 0
    written = json.loads((clone / manifest).read_text(encoding="utf-8"))
    assert written["scientific_status"]["recorded"]["m4_validated_recommendation"] is True

    m3 = json.loads((clone / M3_MANIFEST).read_text(encoding="utf-8"))
    m3["data"]["data_fingerprint"] = "0" * 64
    (clone / M3_MANIFEST).write_text(json.dumps(m3), encoding="utf-8")
    _commit(clone, "an M3 manifest computed from other data")
    result = _tool(clone, "--write")
    assert result.returncode == 2
    assert len(_lines(result)) == 1
    assert result.stderr.startswith(
        "rc_manifest: error: the committed results record different data fingerprints: "
    )
    assert f"{M3_MANIFEST}: {'0' * 64}" in result.stderr


def test_the_tool_fails_clearly_where_it_cannot_run(tmp_path: Path, clone: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    no_git = tmp_path / "empty-path"
    no_git.mkdir()
    cases = [
        (outside, {"GIT_CEILING_DIRECTORIES": str(tmp_path)}, f"{outside} is not a git checkout"),
        (clone / "docs", {}, f"{clone / 'docs'} is not the top level of its git checkout"),
        (clone, {"PATH": str(no_git)}, "git is required but was not found on PATH"),
    ]
    for root, environment, message in cases:
        result = _tool(root, "--check", **environment)
        assert result.returncode == 2, message
        assert result.stdout == ""
        assert len(_lines(result)) == 1, result.stderr
        assert result.stderr.startswith(f"rc_manifest: error: {message}")

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
