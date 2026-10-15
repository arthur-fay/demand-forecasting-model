"""The release manifest (``release.json``): what exactly a release is.

Written last by the release build, after the release passed its checks, and read by every
later step. It identifies:

* the release id, the full git commit and the ATLAS version;
* the Python version and interpreter of the release's environment;
* the dependency lock (SHA-256 of ``uv.lock``) and the locked runtime packages;
* the data snapshot (its manifest's SHA-256 and the data fingerprint the committed runs
  were made from);
* every research artifact (``snapshots/``, ``01 Data/``, ``docs/results/``, ``configs/``)
  by SHA-256, and a fingerprint of the whole set;
* every file of the exported commit (code, templates, configuration) by SHA-256, and one
  fingerprint of the installed environment (``.venv``);
* the API contract (SHA-256 of ``docs/api/openapi.json``);
* the scientific-integrity status: the M3 interval status (``failed_calibration``), the M4
  parameter provenance (``HYPOTHETICAL``) and ``validated_recommendation: false``;
* the build: when, with which uv, and which checks passed.

:func:`check_release` recomputes the fingerprints of a release on disk: an altered research
artifact, lock, contract, code file or environment is refused before activation, and
post-deployment verification compares the running API with the manifest
(:mod:`atlas.deploy.verify`).
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Final

from atlas.deploy.layout import RELEASE_MANIFEST, valid_release_id

MANIFEST_VERSION: Final = 1
RESEARCH_PATHS: Final = ("snapshots", "01 Data", "docs/results", "configs")
CONTRACT: Final = "docs/api/openapi.json"
LOCK: Final = "uv.lock"
M4_MANIFEST: Final = "docs/results/atlas-m4-optimization-v1/manifest.json"
M3_STATUS: Final = "docs/results/atlas-m3-intervals-v1/full/calibration_status.json"
_COMMIT: Final = re.compile(r"[0-9a-f]{40}")


class ManifestError(ValueError):
    """A release manifest is missing, malformed or does not match the release on disk."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def research_files(release_dir: Path) -> dict[str, str]:
    """SHA-256 of every research artifact, by repository-relative path (sorted)."""
    files: dict[str, str] = {}
    for top in RESEARCH_PATHS:
        base = release_dir / top
        if not base.is_dir():
            raise ManifestError(f"{top}/ is missing from {release_dir}")
        for path in sorted(base.rglob("*")):
            if path.is_file() and not path.is_symlink():
                files[path.relative_to(release_dir).as_posix()] = sha256_file(path)
    return dict(sorted(files.items()))


def source_files(release_dir: Path) -> dict[str, str]:
    """SHA-256 of every file of the exported commit (everything except the environment and
    the manifest itself): code, templates, configuration and research artifacts."""
    files: dict[str, str] = {}
    for path in sorted(release_dir.rglob("*")):
        relative = path.relative_to(release_dir)
        if relative.parts[0] in (".venv", RELEASE_MANIFEST):
            continue
        if path.is_symlink() or path.is_file():
            files[relative.as_posix()] = (
                "symlink:" + str(path.readlink()) if path.is_symlink() else sha256_file(path)
            )
    return files


def environment_fingerprint(release_dir: Path) -> str:
    """One fingerprint of the installed environment (``.venv``: files and links)."""
    files: dict[str, str] = {}
    base = release_dir / ".venv"
    for path in sorted(base.rglob("*")):
        relative = path.relative_to(release_dir).as_posix()
        if path.is_symlink():
            files[relative] = "symlink:" + str(path.readlink())
        elif path.is_file():
            files[relative] = sha256_file(path)
    return tree_fingerprint(files)


def tree_fingerprint(files: Mapping[str, str]) -> str:
    """One SHA-256 over the sorted ``path NUL sha256`` lines of a file set."""
    digest = hashlib.sha256()
    for path, value in sorted(files.items()):
        digest.update(f"{path}\0{value}\n".encode())
    return digest.hexdigest()


def _toml(path: Path) -> dict[str, Any]:
    tomllib = importlib.import_module("tomllib")
    loaded: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    return loaded


def project_version(release_dir: Path) -> str:
    version = _toml(release_dir / "pyproject.toml").get("project", {}).get("version")
    if not isinstance(version, str):
        raise ManifestError("pyproject.toml has no project version")
    return version


def runtime_packages(release_dir: Path) -> dict[str, str]:
    """The locked runtime packages (the development group excluded), name -> version."""
    lock = _toml(release_dir / LOCK)
    packages = {p["name"]: p for p in lock.get("package", [])}
    pending = [d["name"] for d in packages.get("atlas", {}).get("dependencies", [])]
    found: dict[str, str] = {}
    while pending:
        name = pending.pop()
        if name in found or name not in packages:
            continue
        found[name] = str(packages[name].get("version", ""))
        pending.extend(d["name"] for d in packages[name].get("dependencies", []))
    return dict(sorted(found.items()))


def python_of(release_dir: Path) -> dict[str, str | None]:
    """The interpreter of the release's environment, from ``.venv/pyvenv.cfg``."""
    values: dict[str, str] = {}
    try:
        text = (release_dir / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError:
        return {"version": None, "home": None}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    return {
        "version": values.get("version_info") or values.get("version"),
        "home": values.get("home"),
    }


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"{path} cannot be read as JSON") from exc
    if not isinstance(value, dict):
        raise ManifestError(f"{path} is not a JSON object")
    return value


def scientific_status(release_dir: Path) -> dict[str, Any]:
    """The committed scientific-integrity status a deployment must keep serving."""
    status = _json(release_dir / M3_STATUS).get("decision_status", {})
    m4 = _json(release_dir / M4_MANIFEST)
    return {
        "m3_interval_status": status.get("interval_status"),
        "m3_decision_grade": bool(status.get("decision_grade")),
        "m4_parameter_provenance": "HYPOTHETICAL",
        "m4_validated_recommendation": bool(m4.get("validated_recommendation")),
        "point_forecast_validated": False,
        "validated_recommendation": False,
    }


def build_manifest(
    release_dir: Path,
    *,
    release_id: str,
    commit: str,
    built_at: str,
    uv_version: str,
    checks: Iterable[str],
) -> dict[str, Any]:
    """The manifest of the release in ``release_dir`` (see the module docstring)."""
    if not valid_release_id(release_id):
        raise ManifestError(f"invalid release id {release_id!r}")
    if not _COMMIT.fullmatch(commit):
        raise ManifestError("the commit must be the full 40-character SHA-1")
    version = project_version(release_dir)
    if release_id != f"{version}-{commit[:12]}":
        raise ManifestError(f"release id {release_id} does not match {version} and {commit[:12]}")
    files = research_files(release_dir)
    sources = source_files(release_dir)
    m4 = _json(release_dir / M4_MANIFEST)
    data = m4.get("data", {})
    snapshot_id = str(data.get("snapshot_id", ""))
    return {
        "manifest_version": MANIFEST_VERSION,
        "release_id": release_id,
        "git_commit": commit,
        "atlas_version": version,
        "python": python_of(release_dir),
        "dependencies": {
            "lock_sha256": sha256_file(release_dir / LOCK),
            "runtime_packages": runtime_packages(release_dir),
        },
        "snapshot": {
            "snapshot_id": snapshot_id,
            "manifest_sha256": sha256_file(
                release_dir / "snapshots" / snapshot_id / "manifest.json"
            ),
            "data_fingerprint": data.get("data_fingerprint"),
        },
        "research_artifacts": {
            "paths": list(RESEARCH_PATHS),
            "count": len(files),
            "fingerprint": tree_fingerprint(files),
            "files": files,
        },
        "source": {"fingerprint": tree_fingerprint(sources), "files": sources},
        "environment_fingerprint": environment_fingerprint(release_dir),
        "api_contract_sha256": sha256_file(release_dir / CONTRACT),
        "scientific_status": scientific_status(release_dir),
        "build": {"built_at": built_at, "uv_version": uv_version, "checks_passed": list(checks)},
    }


def write_manifest(release_dir: Path, manifest: Mapping[str, Any]) -> Path:
    path = release_dir / RELEASE_MANIFEST
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_manifest(release_dir: Path) -> dict[str, Any]:
    manifest = _json(release_dir / RELEASE_MANIFEST)
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise ManifestError(f"{release_dir.name}: unsupported manifest version")
    for key in ("release_id", "git_commit", "atlas_version", "research_artifacts", "snapshot"):
        if key not in manifest:
            raise ManifestError(f"{release_dir.name}: the manifest has no {key}")
    return manifest


def check_release(release_dir: Path, manifest: Mapping[str, Any] | None = None) -> list[str]:
    """Differences between a release on disk and its manifest (empty: they agree)."""
    try:
        manifest = manifest if manifest is not None else load_manifest(release_dir)
    except ManifestError as exc:
        return [str(exc)]
    problems: list[str] = []
    if manifest.get("release_id") != release_dir.name:
        problems.append(
            f"the manifest describes {manifest.get('release_id')}, not {release_dir.name}"
        )
    try:
        files = research_files(release_dir)
    except ManifestError as exc:
        return [*problems, str(exc)]
    recorded: Mapping[str, str] = manifest["research_artifacts"].get("files", {})
    for path in sorted(set(recorded) | set(files)):
        if path not in files:
            problems.append(f"research artifact missing: {path}")
        elif path not in recorded:
            problems.append(f"research artifact not in the manifest: {path}")
        elif files[path] != recorded[path]:
            problems.append(f"research artifact altered: {path}")
    if tree_fingerprint(files) != manifest["research_artifacts"].get("fingerprint"):
        problems.append("the research artifact fingerprint differs from the manifest")
    for name, key in (
        (LOCK, ("dependencies", "lock_sha256")),
        (CONTRACT, ("api_contract_sha256",)),
    ):
        expected: Any = manifest
        for part in key:
            expected = expected.get(part) if isinstance(expected, Mapping) else None
        try:
            actual = sha256_file(release_dir / name)
        except OSError:
            problems.append(f"{name} is missing")
            continue
        if actual != expected:
            problems.append(f"{name} differs from the manifest")
    recorded_sources: Mapping[str, str] = manifest.get("source", {}).get("files", {})
    sources = source_files(release_dir)
    changed = sorted(
        path
        for path in set(recorded_sources) | set(sources)
        if recorded_sources.get(path) != sources.get(path)
    )
    problems += [f"release file altered, added or removed: {path}" for path in changed[:20]]
    executable = release_dir / ".venv" / "bin" / "atlas"
    if not executable.exists():
        problems.append("the release has no environment (.venv/bin/atlas)")
    elif environment_fingerprint(release_dir) != manifest.get("environment_fingerprint"):
        problems.append("the installed environment (.venv) differs from the manifest")
    return problems[:50]
