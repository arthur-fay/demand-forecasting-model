"""Every link of the M4 input provenance chain refuses a forged or inconsistent input.

Each test copies the committed M2 and M3 run directories into a temporary repository,
forges exactly one thing and checks that the specific guard fires. Forgeries that also
re-hash the edited file in its manifest ("edit and re-hash") are included: they pass the
SHA-256 checks and must be caught by the cross-links between runs, or by the pins in the
M4 configuration.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from atlas.config import load_settings
from atlas.decision_inputs import (
    check_same_data,
    load_demand_percentile,
    load_m2_projection,
    load_m3_quantile_path,
    load_m3_status,
)
from atlas.errors import ConfigError
from atlas.experiment import result_fingerprint
from atlas.optimization_config import load_optimization_config
from atlas.optimization_runner import load_inputs
from atlas.runner import RESULT_FILES as M2_RESULT_FILES
from atlas.runner import load_series
from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.integration

M2 = "docs/results/atlas-m2-rolling-origin-v1"
M3 = "docs/results/atlas-m3-intervals-v1"
PROJECTION = f"{M2}/projection"
FULL = f"{M2}/full"
STATUS = f"{M3}/full/calibration_status.json"
M3_PROJECTION = f"{M3}/projection"
MODEL = "atlas_linear_regression"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _rehash(directory: Path, name: str) -> None:
    manifest = _read(directory / "manifest.json")
    manifest["outputs"][name] = _sha(directory / name)
    _write(directory / "manifest.json", manifest)


def _edit_manifest(directory: Path, change: Callable[[dict[str, Any]], None]) -> None:
    manifest = _read(directory / "manifest.json")
    change(manifest)
    _write(directory / "manifest.json", manifest)


def _replace_line(path: Path, old: str, new: str, count: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, count), encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    for name in (M2, M3):
        shutil.copytree(REPO_ROOT / name, target / name)
    return target


def _projection(root: Path) -> Any:
    return load_m2_projection(root, PROJECTION, MODEL, FULL)


# --- M2 projection -----------------------------------------------------------------------


def test_untouched_copy_loads(root: Path) -> None:
    projection = _projection(root)
    status = load_m3_status(root, STATUS)
    check_same_data(projection, status)
    path = load_m3_quantile_path(root, M3_PROJECTION, status, projection, 0.9)
    assert len(path.values) == 30
    assert load_demand_percentile(root, M3_PROJECTION, status) == 11608.75


def _set(key: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def change(manifest: dict[str, Any]) -> None:
        manifest[key] = value

    return change


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (_set("record_type", "historical_backtest"), "is not a future_projection run"),
        (_set("recommended_model", None), "is not the model"),
        (_set("recommendation_source", None), "names no recommending run"),
    ],
)
def test_projection_manifest_forgeries(
    root: Path, change: Callable[[dict[str, Any]], None], match: str
) -> None:
    _edit_manifest(root / PROJECTION, change)
    with pytest.raises(ConfigError, match=match):
        _projection(root)


def test_missing_projection_file(root: Path) -> None:
    (root / PROJECTION / "projections.csv").unlink()
    with pytest.raises(ConfigError, match="is missing"):
        _projection(root)


def test_edited_projection_without_rehash(root: Path) -> None:
    _replace_line(root / PROJECTION / "projections.csv", "11123.683380141376", "11124")
    with pytest.raises(ConfigError, match="SHA-256"):
        _projection(root)


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        (f",{MODEL},", ",renamed_model,", "has no rows"),
        (",projected,", ",failed,", "every row must be a projected"),
        (
            "2026-06-30,2026-06-30,2026-07-05,5,",
            "2026-06-29,2026-06-30,2026-07-05,5,",
            "different origin",
        ),
        ("2026-07-05,5,", "2026-07-15,5,", "consecutive days"),
        (",11123.683380141376,", ",-1,", "finite and non-negative"),
    ],
)
def test_projection_rows_edited_and_rehashed(root: Path, old: str, new: str, match: str) -> None:
    path = root / PROJECTION / "projections.csv"
    count = -1 if old == f",{MODEL}," else 1
    _replace_line(path, old, new, count)
    _rehash(root / PROJECTION, "projections.csv")
    with pytest.raises(ConfigError, match=match):
        _projection(root)


def test_projection_with_a_missing_horizon(root: Path) -> None:
    path = root / PROJECTION / "projections.csv"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(line for line in lines if ",2026-07-05,5," not in line))
    _rehash(root / PROJECTION, "projections.csv")
    with pytest.raises(ConfigError, match="horizons are not"):
        _projection(root)


# --- The recommending M2 run -------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value", "match"),
    [
        ("complete", False, "not complete"),
        ("run_id", "another-run", "is not the run"),
        ("config_fingerprint", "0" * 64, "different M2 configuration"),
        ("result_fingerprint", "0" * 64, "not the one the projection recorded"),
    ],
)
def test_backtest_manifest_forgeries(root: Path, key: str, value: Any, match: str) -> None:
    _edit_manifest(root / FULL, _set(key, value))
    with pytest.raises(ConfigError, match=match):
        _projection(root)


def test_backtest_run_on_other_data(root: Path) -> None:
    def change(manifest: dict[str, Any]) -> None:
        manifest["data"]["data_fingerprint"] = "0" * 64

    _edit_manifest(root / FULL, change)
    with pytest.raises(ConfigError, match="different data"):
        _projection(root)


def _forge_backtest_metrics(root: Path, change: Callable[[dict[str, Any]], None]) -> str:
    """Edit metrics.json and re-hash it: the recorded outputs then no longer give the
    run's result fingerprint. Returns the fingerprint the forged outputs would give."""
    path = root / FULL / "metrics.json"
    metrics = _read(path)
    change(metrics)
    _write(path, metrics)
    _rehash(root / FULL, "metrics.json")
    return result_fingerprint(_read(root / FULL / "manifest.json")["outputs"], M2_RESULT_FILES)


def _drop_headline_row(metrics: dict[str, Any]) -> None:
    metrics["summary"] = [
        row
        for row in metrics["summary"]
        if not (row["label"] == "days_1_30" and row["period"] == "confirmation")
    ]


def test_backtest_metrics_edited_and_rehashed(root: Path) -> None:
    _forge_backtest_metrics(root, _drop_headline_row)
    with pytest.raises(ConfigError, match="do not give its result fingerprint"):
        _projection(root)


def _forge_consistently(root: Path, fingerprint: str) -> None:
    """Carry a forged result fingerprint through every file that records it."""
    recommendation = root / FULL / "recommendation.json"
    record = _read(recommendation)
    record["result_fingerprint"] = fingerprint
    _write(recommendation, record)
    _rehash(root / FULL, "recommendation.json")
    _edit_manifest(root / FULL, _set("result_fingerprint", fingerprint))

    def source(manifest: dict[str, Any]) -> None:
        manifest["recommendation_source"]["result_fingerprint"] = fingerprint
        manifest["recommendation_source"]["recommendation_sha256"] = _sha(recommendation)

    _edit_manifest(root / PROJECTION, source)


def test_consistent_forgery_of_the_backtest_metrics_is_caught_by_the_m3_cross_link(
    root: Path,
) -> None:
    fingerprint = _forge_backtest_metrics(root, lambda metrics: metrics.update(label="forged"))
    _forge_consistently(root, fingerprint)
    projection = _projection(root)  # every hash in the M2 chain now agrees ...
    with pytest.raises(ConfigError, match="different M2 recommendation"):
        check_same_data(projection, load_m3_status(root, STATUS))  # ... but not with M3


def test_consistent_forgery_without_the_headline_row(root: Path) -> None:
    fingerprint = _forge_backtest_metrics(root, _drop_headline_row)
    _forge_consistently(root, fingerprint)
    with pytest.raises(ConfigError, match="no unique days_1_30 confirmation row"):
        _projection(root)


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (_set("record_type", "sealed_selection"), "not a recommendation record"),
        (
            lambda record: record["recommendation"].update(recommended_model="naive"),
            "names a different model",
        ),
    ],
)
def test_recommendation_forgeries(
    root: Path, change: Callable[[dict[str, Any]], None], match: str
) -> None:
    recommendation = root / FULL / "recommendation.json"
    record = _read(recommendation)
    change(record)
    _write(recommendation, record)
    _rehash(root / FULL, "recommendation.json")

    def source(manifest: dict[str, Any]) -> None:
        manifest["recommendation_source"]["recommendation_sha256"] = _sha(recommendation)

    _edit_manifest(root / PROJECTION, source)
    with pytest.raises(ConfigError, match=match):
        _projection(root)


# --- M3 status and projection -------------------------------------------------------------


def test_status_of_another_record_type(root: Path) -> None:
    path = root / STATUS
    record = _read(path)
    record["record_type"] = "sealed_interval_selection"
    _write(path, record)
    with pytest.raises(ConfigError, match="not an M3 calibration status record"):
        load_m3_status(root, STATUS)


def test_status_and_projection_cross_checks(root: Path) -> None:
    projection = _projection(root)
    status = load_m3_status(root, STATUS)
    other_config = dataclasses.replace(status, backtest_config_fingerprint="0" * 64)
    with pytest.raises(ConfigError, match="different M2 configuration"):
        check_same_data(projection, other_config)
    other_data = dataclasses.replace(status, data_fingerprint="0" * 64)
    with pytest.raises(ConfigError, match="different data"):
        check_same_data(projection, other_data)


def _status_source(key: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def change(manifest: dict[str, Any]) -> None:
        manifest["status_source"][key] = value

    return change


def _data(value: str) -> Callable[[dict[str, Any]], None]:
    def change(manifest: dict[str, Any]) -> None:
        manifest["data"]["data_fingerprint"] = value

    return change


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (_set("record_type", "future_projection"), "is not a future_projection_intervals run"),
        (_status_source("calibration_status_sha256", "0" * 64), "different calibration status"),
        (_status_source("result_fingerprint", "0" * 64), "different M3 run"),
        (_set("calibration_status", "decision_grade"), "calibration status differs"),
        (_set("decided_pair", "gaussian_one_step@rolling365"), "not the sealed one"),
        (_set("config_fingerprint", "0" * 64), "different M3 configuration"),
        (_set("backtest_config_fingerprint", "0" * 64), "different M2 configuration"),
        (_data("0" * 64), "different data"),
        (_set("origin", "2026-06-29"), "origin differs"),
    ],
)
def test_m3_projection_manifest_forgeries(
    root: Path, change: Callable[[dict[str, Any]], None], match: str
) -> None:
    _edit_manifest(root / M3_PROJECTION, change)
    projection = _projection(root)
    status = load_m3_status(root, STATUS)
    with pytest.raises(ConfigError, match=match):
        load_m3_quantile_path(root, M3_PROJECTION, status, projection, 0.9)


def test_demand_percentile_checks_the_same_manifest(root: Path) -> None:
    _edit_manifest(root / M3_PROJECTION, _set("calibration_status", "decision_grade"))
    with pytest.raises(ConfigError, match="calibration status differs"):
        load_demand_percentile(root, M3_PROJECTION, load_m3_status(root, STATUS))


def _intervals_line(root: Path, horizon: int) -> str:
    path = root / M3_PROJECTION / "projection_intervals.csv"
    prefix = (
        f"future_projection_intervals,projection,{MODEL},empirical_relative@fixed365,"
        "empirical_relative,fixed365,candidate,2026-06-30,2026-06-30,2026-06-30,"
    )
    lines = [line for line in path.read_text().splitlines() if line.startswith(prefix)]
    return next(line for line in lines if line.split(",")[11] == str(horizon))


def _forge_interval(root: Path, horizon: int, column: int, value: str) -> None:
    line = _intervals_line(root, horizon)
    fields = line.split(",")
    fields[column] = value
    _replace_line(root / M3_PROJECTION / "projection_intervals.csv", line, ",".join(fields))
    _rehash(root / M3_PROJECTION, "projection_intervals.csv")


@pytest.mark.parametrize(
    ("column", "value", "match"),
    [
        (13, "unavailable", "were not issued"),
        (14, "thin", "were not issued"),
        (10, "2026-07-31", "target dates differ"),
        (12, "11124", "point predictions differ"),
        (28, "-1", "finite and non-negative"),
    ],
)
def test_m3_interval_rows_edited_and_rehashed(
    root: Path, column: int, value: str, match: str
) -> None:
    # columns: 10 target_date, 12 prediction, 13 status, 14 support, 28 q_0.9
    _forge_interval(root, 2, column, value)
    projection = _projection(root)
    status = load_m3_status(root, STATUS)
    with pytest.raises(ConfigError, match=match):
        load_m3_quantile_path(root, M3_PROJECTION, status, projection, 0.9)


def test_m3_interval_row_removed_and_rehashed(root: Path) -> None:
    path = root / M3_PROJECTION / "projection_intervals.csv"
    line = _intervals_line(root, 7)
    _replace_line(path, line + "\n", "")
    _rehash(root / M3_PROJECTION, "projection_intervals.csv")
    projection = _projection(root)
    status = load_m3_status(root, STATUS)
    with pytest.raises(ConfigError, match="horizons do not match"):
        load_m3_quantile_path(root, M3_PROJECTION, status, projection, 0.9)


def test_primary_model_must_be_the_projected_model(root: Path) -> None:
    projection = _projection(root)
    status = dataclasses.replace(
        load_m3_status(root, STATUS), primary_model="same_weekday_median_5w"
    )
    with pytest.raises(ConfigError, match="primary model is not"):
        load_m3_quantile_path(root, M3_PROJECTION, status, projection, 0.9)


def test_percentile_with_another_provenance_is_refused(root: Path) -> None:
    path = root / M3_PROJECTION / "projection_exceedance.csv"
    _replace_line(path, "DERIVED_FROM_HISTORY", "HYPOTHETICAL")
    _rehash(root / M3_PROJECTION, "projection_exceedance.csv")
    with pytest.raises(ConfigError, match="no unique trailing180_p75"):
        load_demand_percentile(root, M3_PROJECTION, load_m3_status(root, STATUS))


# --- Pins in the M4 configuration -------------------------------------------------------------


@pytest.mark.parametrize(
    ("section", "field", "name"),
    [
        ("forecast", "projections_sha256", "forecast.projections_sha256"),
        ("forecast", "m2_config_fingerprint", "forecast.m2_config_fingerprint"),
        ("uncertainty", "calibration_status_sha256", "uncertainty.calibration_status_sha256"),
        (
            "uncertainty",
            "projection_intervals_sha256",
            "uncertainty.projection_intervals_sha256",
        ),
        (
            "uncertainty",
            "projection_exceedance_sha256",
            "uncertainty.projection_exceedance_sha256",
        ),
    ],
)
def test_every_input_must_match_its_pin(section: str, field: str, name: str) -> None:
    config = load_optimization_config(REPO_ROOT / "configs" / "optimization" / "smoke.json")
    part = getattr(config, section).model_copy(update={field: "0" * 64})
    forged = config.model_copy(update={section: part})
    settings = load_settings(repo_root=REPO_ROOT, env={})
    _, data = load_series(settings, "pins")
    assert data is not None
    load_inputs(settings, config, data)  # the committed pins hold
    with pytest.raises(ConfigError, match=name.replace(".", r"\.")):
        load_inputs(settings, forged, data)


def test_configuration_for_another_snapshot_is_refused() -> None:
    config = load_optimization_config(REPO_ROOT / "configs" / "optimization" / "smoke.json")
    settings = load_settings(repo_root=REPO_ROOT, env={})
    _, data = load_series(settings, "pins")
    assert data is not None
    other = config.model_copy(update={"snapshot_id": "legacy-v9"})
    with pytest.raises(ConfigError, match="snapshot 'legacy-v9'"):
        load_inputs(settings, other, data)
