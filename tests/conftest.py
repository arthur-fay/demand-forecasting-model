"""Shared fixtures.

Unit tests use tiny synthetic CSV files written to ``tmp_path`` together with copies of
the real contracts narrowed to a two-week window. Integration tests read the committed
``legacy-v0`` snapshot, which is never modified: tamper tests work on a copy.
"""

from __future__ import annotations

import csv
import dataclasses
import shutil
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from pathlib import Path

import pytest

from atlas.config import Settings, load_settings
from atlas.contracts import DAILY_SEGMENTS, DAILY_TOTAL, TableContract
from atlas.issues import CheckReport
from atlas.loader import LoadedTable
from atlas.pipeline import load_snapshot_tables, run
from atlas.snapshot import load_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]

SMALL_START = date(2024, 1, 1)
SMALL_END = date(2024, 1, 14)
SMALL_DATES = [SMALL_START + timedelta(days=offset) for offset in range(14)]


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
    return path


@pytest.fixture
def small_total_contract() -> TableContract:
    return dataclasses.replace(DAILY_TOTAL, window=(SMALL_START, SMALL_END))


@pytest.fixture
def small_segments_contract() -> TableContract:
    return dataclasses.replace(DAILY_SEGMENTS, window=(SMALL_START, SMALL_END))


@pytest.fixture
def total_rows() -> list[list[object]]:
    """Fourteen consecutive days of city-wide totals."""
    return [[d.isoformat(), 1000 + 10 * i] for i, d in enumerate(SMALL_DATES)]


@pytest.fixture
def segment_rows(total_rows: list[list[object]]) -> list[list[object]]:
    """Two segments per day that sum to 5 fewer than the city-wide total."""
    rows: list[list[object]] = []
    for day, total in total_rows:
        assert isinstance(total, int)
        rows.append([day, "NYPD", "BROOKLYN", total - 405])
        rows.append([day, "HPD", "QUEENS", 400])
    return rows


@pytest.fixture(scope="session")
def repo_settings() -> Settings:
    return load_settings(repo_root=REPO_ROOT, env={})


@pytest.fixture(scope="session")
def legacy_reports(repo_settings: Settings) -> list[CheckReport]:
    return run(repo_settings)


@pytest.fixture(scope="session")
def legacy_tables(repo_settings: Settings) -> dict[str, LoadedTable]:
    manifest = load_manifest(repo_settings.manifest_path)
    return load_snapshot_tables(repo_settings, manifest)


_COPIED = (
    "pyproject.toml",
    "snapshots",
    "01 Data",
    "02 Notebooks",
    "03 SQL",
    "04 Power BI",
    "05 Screenshots",
    "06 Exports",
)


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    """A disposable copy of everything the snapshot checks read."""
    target = tmp_path / "repo"
    target.mkdir()
    for name in _COPIED:
        source = REPO_ROOT / name
        if source.is_dir():
            shutil.copytree(source, target / name)
        else:
            shutil.copy2(source, target / name)
    return target
