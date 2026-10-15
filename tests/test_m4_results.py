"""The committed M4 reference run is consistent with its manifest, the code and the docs.

Expected values come from the committed files under ``docs/results/atlas-m4-optimization-v1``.
The reference run is cheap (well under a minute) and BLAS-free, so it is also recomputed
here: once in this process and once in a subprocess under a different OpenBLAS kernel
family, and both must reproduce every committed result file byte for byte.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from atlas.cli import EXIT_FAILED, main
from atlas.config import load_settings
from atlas.experiment import result_fingerprint
from atlas.optimization_config import load_optimization_config
from atlas.optimization_report import render_optimization_report
from atlas.optimization_runner import (
    LABEL,
    RECORD_TYPE_DECISION_SIMULATION,
    RESULT_FILES,
    STRUCTURAL_ASSUMPTIONS,
    load_inputs,
    m4_outputs,
)
from atlas.runner import load_series
from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.integration

RESULTS = REPO_ROOT / "docs" / "results" / "atlas-m4-optimization-v1"
CONFIG = REPO_ROOT / "configs" / "optimization" / "atlas-m4-optimization-v1.json"
DOC = REPO_ROOT / "docs" / "OPTIMIZATION.md"
CSV_FILES = ("scenarios.csv", "plans.csv", "duals.csv", "sensitivity.csv", "frontier.csv")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return read_json(RESULTS / "manifest.json")


@pytest.fixture(scope="module")
def metrics() -> dict[str, Any]:
    return read_json(RESULTS / "metrics.json")


def test_every_committed_file_matches_its_manifest(manifest: dict[str, Any]) -> None:
    assert set(manifest["outputs"]) == {*RESULT_FILES, "report.md", "config.json"}
    for name, digest in manifest["outputs"].items():
        assert sha256(RESULTS / name) == digest, name
    assert result_fingerprint(manifest["outputs"], RESULT_FILES) == manifest["result_fingerprint"]


def test_reference_run_used_the_frozen_configuration_on_clean_code(
    manifest: dict[str, Any],
) -> None:
    config = load_optimization_config(CONFIG)
    assert manifest["record_type"] == RECORD_TYPE_DECISION_SIMULATION
    assert manifest["complete"] is True
    assert manifest["config_fingerprint"] == config.fingerprint()
    assert manifest["config"] == config.model_dump(mode="json")
    assert read_json(RESULTS / "config.json") == config.model_dump(mode="json")
    assert manifest["git"]["dirty"] is False
    assert manifest["validated_recommendation"] is False
    assert manifest["structural_assumptions"] == list(STRUCTURAL_ASSUMPTIONS)


def test_report_renders_from_its_metrics(metrics: dict[str, Any]) -> None:
    rendered = render_optimization_report(metrics)
    assert (RESULTS / "report.md").read_text(encoding="utf-8") == rendered


def test_every_row_carries_the_labels(metrics: dict[str, Any]) -> None:
    status = metrics["inputs"]["uncertainty_status"]["interval_status"]
    assert status == "failed_calibration"
    for name in CSV_FILES:
        frame = pd.read_csv(RESULTS / name)
        assert len(frame) > 0, name
        assert set(frame["record_type"]) == {RECORD_TYPE_DECISION_SIMULATION}, name
        assert set(frame["parameter_provenance"]) == {"HYPOTHETICAL"}, name
        assert set(frame["validated_recommendation"]) == {False}, name
        assert set(frame["point_forecast_validated"]) == {False}, name
        assert set(frame["m3_interval_status"]) == {status}, name
    report = (RESULTS / "report.md").read_text(encoding="utf-8")
    assert LABEL in report
    assert "FAILED_CALIBRATION" in report


def test_recomputation_reproduces_every_committed_file(manifest: dict[str, Any]) -> None:
    settings = load_settings(repo_root=REPO_ROOT, env={})
    config = load_optimization_config(CONFIG)
    _, data = load_series(settings, "m4_results")
    assert data is not None
    outputs = m4_outputs(settings, config, load_inputs(settings, config, data))
    assert outputs.outputs == manifest["outputs"]
    assert outputs.result_fingerprint == manifest["result_fingerprint"]


_SCRIPT = """
import json
from pathlib import Path
from atlas.config import load_settings
from atlas.optimization_config import load_optimization_config
from atlas.optimization_runner import load_inputs, m4_outputs
from atlas.runner import load_series

root = Path({root!r})
settings = load_settings(repo_root=root, env={{}})
config = load_optimization_config(root / "configs/optimization/atlas-m4-optimization-v1.json")
_, data = load_series(settings, "cross_environment")
print(json.dumps(m4_outputs(settings, config, load_inputs(settings, config, data)).outputs))
"""


@pytest.mark.parametrize("kernel", ["Prescott", "Haswell"])
def test_byte_identical_under_another_blas_kernel_family(
    kernel: str, manifest: dict[str, Any]
) -> None:
    # HiGHS does not call BLAS and every written value is computed with correctly rounded
    # sums, so a different OpenBLAS kernel (as on another CPU) must not change one byte.
    env = {**os.environ, "OPENBLAS_CORETYPE": kernel}
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and script, no shell
        [sys.executable, "-c", _SCRIPT.format(root=str(REPO_ROOT))],
        capture_output=True,
        text=True,
        env=env,
        check=True,
        timeout=600,
    )
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == manifest["outputs"]


def _repository_copy(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    target.mkdir()
    for name in ("pyproject.toml", "snapshots", "01 Data", "02 Notebooks", "03 SQL"):
        source = REPO_ROOT / name
        if source.is_dir():
            shutil.copytree(source, target / name)
        else:
            shutil.copy2(source, target / name)
    for name in ("04 Power BI", "05 Screenshots", "06 Exports", "configs"):
        shutil.copytree(REPO_ROOT / name, target / name)
    for name in ("atlas-m2-rolling-origin-v1", "atlas-m3-intervals-v1"):
        shutil.copytree(REPO_ROOT / "docs" / "results" / name, target / "docs" / "results" / name)
    return target


def test_optimize_is_skipped_when_snapshot_integrity_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository_copy(tmp_path)
    target = repo / "01 Data" / "daily_total.csv"
    target.write_bytes(target.read_bytes().replace(b"6209", b"6210", 1))
    output = tmp_path / "out"
    code = main(
        [
            "--repo-root",
            str(repo),
            "optimize",
            "run",
            "--config",
            str(repo / "configs" / "optimization" / "smoke.json"),
            "--output-dir",
            str(output),
        ]
    )
    text = capsys.readouterr().out
    assert code == EXIT_FAILED
    assert "[FAIL] integrity" in text
    assert "[SKIP] optimization: snapshot integrity check failed" in text
    assert not output.exists()


def test_documentation_names_the_reference_run(manifest: dict[str, Any]) -> None:
    text = DOC.read_text(encoding="utf-8")
    assert manifest["result_fingerprint"] in text
    assert manifest["config_fingerprint"] in text
    assert re.search(r"FAILED_CALIBRATION", text)


#: The feasibility switch points of the one-parameter sweeps of ``normal`` that
#: docs/OPTIMIZATION.md section 9.4 quotes (four decimals), computed here by bisection on the
#: strict model with the run's own code (they are not part of the run's outputs).
SWITCH_POINTS = {
    "demand_multiplier": (1.0, 1.02, 1.0098),
    "regular_hours_multiplier": (0.98, 1.0, 0.9901),
    "productivity_multiplier": (0.98, 1.0, 0.9904),
    "discretionary_budget_multiplier": (0.5, 0.75, 0.7082),
    "cost_multiplier": (1.2, 1.5, 1.4119),
}


def test_feasibility_switch_points_quoted_in_section_9() -> None:
    from atlas.decision_analysis import apply_sweep, solve_problem
    from atlas.optimization_runner import solver_settings
    from atlas.optimizer import SolveStatus
    from atlas.scenarios import build_scenarios

    config = load_optimization_config(CONFIG)
    settings = load_settings(repo_root=REPO_ROOT, env={})
    _, data = load_series(settings, "m4_switch_points")
    assert data is not None
    inputs = load_inputs(settings, config, data)
    dates, point = inputs.projection.first(config.horizon_days)
    scenarios = build_scenarios(config, dates, point, inputs.quantiles, inputs.demand_percentile)
    base = next(s for s in scenarios if s.scenario_id == "normal").problem
    solver = solver_settings(config)

    def feasible(parameter: str, value: float) -> bool:
        attempt = solve_problem(apply_sweep(base, parameter, value), solver)
        return attempt.status is SolveStatus.OPTIMAL

    text = DOC.read_text(encoding="utf-8")
    for parameter, (low, high, quoted) in SWITCH_POINTS.items():
        at_low = feasible(parameter, low)
        assert at_low != feasible(parameter, high), parameter
        for _ in range(30):
            middle = (low + high) / 2
            if feasible(parameter, middle) == at_low:
                low = middle
            else:
                high = middle
        assert round((low + high) / 2, 4) == quoted, parameter
        assert f"{quoted:.4f}".rstrip("0") in text or f"{quoted:.3f}" in text, parameter
