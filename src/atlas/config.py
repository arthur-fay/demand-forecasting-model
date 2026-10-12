"""Runtime configuration.

Settings are resolved in this order: explicit arguments (command-line flags),
then environment variables, then defaults. M0 handles no secrets; the
environment variables only locate the repository and select a snapshot.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from atlas.errors import ConfigError

DEFAULT_SNAPSHOT_ID = "legacy-v0"
ENV_REPO_ROOT = "ATLAS_REPO_ROOT"
ENV_SNAPSHOT_ID = "ATLAS_SNAPSHOT_ID"
SNAPSHOTS_DIRNAME = "snapshots"
MANIFEST_FILENAME = "manifest.json"
EXCEPTIONS_FILENAME = "known_exceptions.json"

# Snapshot identifiers become directory names, so restrict them to a safe alphabet
# (this also rules out path traversal such as "../other").
_SNAPSHOT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


@dataclass(frozen=True)
class Settings:
    """Resolved locations for one snapshot inside one repository checkout."""

    repo_root: Path
    snapshot_id: str = DEFAULT_SNAPSHOT_ID

    @property
    def snapshot_dir(self) -> Path:
        return self.repo_root / SNAPSHOTS_DIRNAME / self.snapshot_id

    @property
    def manifest_path(self) -> Path:
        return self.snapshot_dir / MANIFEST_FILENAME

    @property
    def exceptions_path(self) -> Path:
        return self.snapshot_dir / EXCEPTIONS_FILENAME


def discover_repo_root(start: Path) -> Path:
    """Return the nearest ancestor of ``start`` that looks like an ATLAS checkout."""
    start = start.resolve()
    for candidate in (start, *start.parents):
        if (candidate / SNAPSHOTS_DIRNAME).is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    raise ConfigError(
        f"Could not find the ATLAS repository root from {start}. Run inside the "
        f"repository, pass --repo-root, or set {ENV_REPO_ROOT}."
    )


def validate_snapshot_id(snapshot_id: str) -> str:
    if not _SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
        raise ConfigError(
            f"Invalid snapshot id {snapshot_id!r}: use lowercase letters, digits, '.', '_' "
            "or '-' (at most 64 characters, starting with a letter or digit)."
        )
    return snapshot_id


def load_settings(
    *,
    repo_root: Path | None = None,
    snapshot_id: str | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> Settings:
    """Resolve settings from arguments, then environment variables, then defaults."""
    environ = os.environ if env is None else env

    if repo_root is None:
        env_root = environ.get(ENV_REPO_ROOT, "").strip()
        root = Path(env_root) if env_root else discover_repo_root(cwd or Path.cwd())
    else:
        root = repo_root
    root = root.expanduser().resolve()
    if not (root / SNAPSHOTS_DIRNAME).is_dir():
        raise ConfigError(f"{root} has no '{SNAPSHOTS_DIRNAME}/' directory.")

    chosen_id = snapshot_id or environ.get(ENV_SNAPSHOT_ID, "").strip() or DEFAULT_SNAPSHOT_ID
    return Settings(repo_root=root, snapshot_id=validate_snapshot_id(chosen_id))
