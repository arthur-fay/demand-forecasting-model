from __future__ import annotations

from pathlib import Path

import pytest

from atlas.config import (
    DEFAULT_SNAPSHOT_ID,
    ENV_REPO_ROOT,
    ENV_SNAPSHOT_ID,
    discover_repo_root,
    load_settings,
)
from atlas.errors import ConfigError
from tests.conftest import REPO_ROOT


def test_explicit_repo_root_defines_snapshot_paths() -> None:
    settings = load_settings(repo_root=REPO_ROOT, env={})
    assert settings.snapshot_id == DEFAULT_SNAPSHOT_ID
    assert settings.manifest_path == REPO_ROOT / "snapshots" / "legacy-v0" / "manifest.json"
    assert settings.exceptions_path.name == "known_exceptions.json"


def test_environment_variables_are_used_when_no_arguments_given() -> None:
    settings = load_settings(env={ENV_REPO_ROOT: str(REPO_ROOT), ENV_SNAPSHOT_ID: "legacy-v0"})
    assert settings.repo_root == REPO_ROOT
    assert settings.snapshot_id == "legacy-v0"


def test_arguments_take_precedence_over_environment(tmp_path: Path) -> None:
    settings = load_settings(
        repo_root=REPO_ROOT,
        snapshot_id="legacy-v0",
        env={ENV_REPO_ROOT: str(tmp_path), ENV_SNAPSHOT_ID: "other"},
    )
    assert settings.repo_root == REPO_ROOT
    assert settings.snapshot_id == "legacy-v0"


def test_repo_root_is_discovered_from_a_nested_directory() -> None:
    assert discover_repo_root(REPO_ROOT / "src" / "atlas") == REPO_ROOT
    assert load_settings(env={}, cwd=REPO_ROOT / "tests").repo_root == REPO_ROOT


def test_discovery_outside_a_checkout_fails(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Could not find the ATLAS repository root"):
        load_settings(env={}, cwd=tmp_path)


def test_root_without_snapshots_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no 'snapshots/' directory"):
        load_settings(repo_root=tmp_path, env={})


@pytest.mark.parametrize("snapshot_id", ["../escape", "Legacy", "a/b", "x" * 65, ".hidden"])
def test_unsafe_or_malformed_snapshot_ids_are_rejected(snapshot_id: str) -> None:
    with pytest.raises(ConfigError, match="Invalid snapshot id"):
        load_settings(repo_root=REPO_ROOT, snapshot_id=snapshot_id, env={})


def test_blank_snapshot_id_falls_back_to_default() -> None:
    settings = load_settings(repo_root=REPO_ROOT, snapshot_id="", env={ENV_SNAPSHOT_ID: "  "})
    assert settings.snapshot_id == DEFAULT_SNAPSHOT_ID
