"""Shared helpers for the M7 dashboard tests."""

from __future__ import annotations

import shutil
from pathlib import Path

from atlas.dashboard import DIST
from atlas.deploy.manifest import build_manifest, load_manifest, write_manifest
from tests.conftest import REPO_ROOT
from tests.m6_helpers import make_release

BUILD = REPO_ROOT / DIST


def copy_build(target: Path) -> Path:
    """A copy of the committed dashboard build at ``target`` (``.../web/dist``)."""
    shutil.copytree(BUILD, target)
    return target


def make_dashboard_release(base: Path, *, commit: str = "a" * 40) -> Path:
    """A release (as ``make_release``) that also carries the committed dashboard build,
    with its manifest written again so that it records the dashboard."""
    release = make_release(base, commit=commit)
    for path in [release, *release.rglob("*")]:
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | 0o200)
    copy_build(release / DIST)
    previous = load_manifest(release)
    manifest = build_manifest(
        release,
        release_id=release.name,
        commit=commit,
        built_at=previous["build"]["built_at"],
        uv_version="0.11.32",
        checks=[*previous["build"]["checks_passed"], "dashboard build"],
    )
    write_manifest(release, manifest)
    for path in [release, *release.rglob("*")]:
        if not path.is_symlink():
            path.chmod(path.stat().st_mode & ~0o022)
    return release
