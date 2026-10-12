"""M1 command-line entry points and pipeline stage, plus contract checks of in-memory tables."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
import pytest

from atlas.cli import EXIT_FAILED, EXIT_OK, main
from atlas.config import load_settings
from atlas.contracts import QUALITY_FLAGS, Column, Kind, TableContract
from atlas.features import default_feature_set
from atlas.loader import validate_frame
from atlas.pipeline import run_quality
from atlas.registry import SNAPSHOT_CONTRACTS
from tests.conftest import REPO_ROOT


def run_cli(*args: str) -> tuple[int, str]:
    buffer = io.StringIO()
    code = main(list(args), stdout=buffer)
    return code, buffer.getvalue()


@pytest.mark.integration
def test_quality_command_text() -> None:
    code, output = run_cli("--repo-root", str(REPO_ROOT), "quality")
    assert code == EXIT_OK
    for line in ("[PASS] integrity", "[PASS] validation", "[PASS] quality"):
        assert line in output
    assert "UNASSIGNED residual" in output
    assert "13,279 requests (0.1077%) on 1273 of 1277 dates" in output
    assert "2024-04-11..2024-04-14, observed 0" in output
    assert "[reproduction]" not in output


@pytest.mark.integration
def test_quality_command_json() -> None:
    code, output = run_cli("--repo-root", str(REPO_ROOT), "--format", "json", "quality")
    payload = json.loads(output)
    assert code == EXIT_OK
    assert [r["name"] for r in payload["reports"]] == ["integrity", "validation", "quality"]
    quality = payload["reports"][-1]
    assert quality["ok"] is True
    assert quality["error_count"] == 0
    assert quality["facts"]["flag_counts"]["intake_interruption"] == 8
    assert quality["facts"]["residual"]["total"] == 13_279


def test_quality_is_skipped_when_integrity_fails(repo_copy: Path) -> None:
    target = repo_copy / "01 Data" / "daily_total.csv"
    target.write_bytes(target.read_bytes().replace(b"6209", b"6210", 1))
    code, output = run_cli("--repo-root", str(repo_copy), "quality")
    assert code == EXIT_FAILED
    assert "[SKIP] validation: snapshot integrity check failed" in output
    assert "[SKIP] quality: snapshot integrity check failed" in output


def test_quality_is_skipped_when_validation_fails(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Integrity passes (files untouched) but a stricter contract fails validation.
    contracts = dict(SNAPSHOT_CONTRACTS["legacy-v0"])
    total = contracts["daily_total"]
    strict = TableContract(
        name=total.name,
        description=total.description,
        columns=(total.columns[0], Column("request_count", Kind.INTEGER, maximum=1)),
        primary_key=total.primary_key,
    )
    contracts["daily_total"] = strict
    monkeypatch.setitem(SNAPSHOT_CONTRACTS, "legacy-v0", contracts)
    reports = run_quality(load_settings(repo_root=repo_copy, env={}))
    assert [r.name for r in reports] == ["integrity", "validation", "quality"]
    assert reports[-1].skipped_reason == "data validation failed"


def test_quality_not_applicable_without_base_tables(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import atlas.pipeline as pipeline

    def without_base(*args: object) -> dict[str, object]:
        return {}

    monkeypatch.setattr(pipeline, "load_snapshot_tables", without_base)
    monkeypatch.setitem(SNAPSHOT_CONTRACTS, "legacy-v0", {})
    reports = run_quality(load_settings(repo_root=repo_copy, env={}))
    assert reports[-1].name == "quality"
    assert reports[-1].issues[0].code == "not_applicable"


def test_features_command_needs_no_data(tmp_path: Path) -> None:
    code, output = run_cli("--repo-root", str(tmp_path), "features")  # not a repository
    assert code == EXIT_OK
    feature_set = default_feature_set()
    assert f"{len(feature_set)} features, history needed 364 days" in output
    assert "lag_364" in output
    assert "t-28..t-1" in output
    code, output = run_cli("--format", "json", "features")
    payload = json.loads(output)
    assert payload["fingerprint"] == feature_set.fingerprint()
    assert [f["name"] for f in payload["features"]] == list(feature_set.names)


# --- validate_frame -------------------------------------------------------------------

CONTRACT = TableContract(
    name="t",
    description="test",
    columns=(
        Column("day", Kind.DATE),
        Column("n", Kind.INTEGER, minimum=0),
        Column("x", Kind.DECIMAL, nullable=True),
        Column("label", Kind.STRING, nullable=True),
    ),
    primary_key=("day",),
    min_rows=0,
)


def test_validate_frame_accepts_well_typed_frames() -> None:
    frame = pd.DataFrame(
        {
            "day": pd.to_datetime(["2024-02-29", "2024-03-01"]),
            "n": [1, 2],
            "x": [0.5, float("nan")],
            "label": ["a", None],
        }
    )
    checked = validate_frame(frame, CONTRACT)
    assert checked.ok
    assert checked.frame["n"].tolist() == [1, 2]
    assert validate_frame(frame.iloc[0:0], CONTRACT).ok  # min_rows = 0


def test_validate_frame_applies_the_csv_rules() -> None:
    frame = pd.DataFrame(
        {
            "day": [pd.Timestamp("2024-02-29 06:00"), pd.Timestamp("2024-02-29")],
            "n": [1.5, -1.0],  # floats in an INTEGER column
            "x": [float("inf"), 1.0],
            "label": [" padded", "NaN"],
        }
    )
    codes = {(i.subject, i.code) for i in validate_frame(frame, CONTRACT).issues}
    assert ("t.day", "malformed_value") in codes
    assert ("t.n", "malformed_value") in codes
    assert ("t.x", "malformed_value") in codes  # inf
    assert ("t.label", "malformed_value") in codes
    assert ("t.label", "null_token") in codes


def test_validate_frame_on_empty_quality_table() -> None:
    frame = pd.DataFrame(columns=list(QUALITY_FLAGS.column_names))
    assert validate_frame(frame, QUALITY_FLAGS).ok
    strict = TableContract("s", "d", (Column("d", Kind.DATE),), ("d",))
    assert {i.code for i in validate_frame(pd.DataFrame({"d": []}), strict).issues} == {
        "empty_table"
    }
