"""The integrity guards that ordinary damage never reaches.

A damaged file is caught first by M4's own checks (``load_inputs``, ``optimize inspect``).
The checks below are what stands behind them: a *self-consistent forgery* (files edited and
every hash recomputed), a file swapped between its check and its read, a cross-reference that
disagrees, and a simulation whose result failed an independent check. Each test fails when
the guard it names is deleted (checked with planted mutants, docs/API.md section 14), except
where a second check guards the same property (the M2 configuration fingerprint).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from atlas.api import reference
from atlas.api import worker as worker_module
from atlas.api.problems import ApiError
from atlas.api.reference import (
    FORECAST,
    M3_INTERVALS,
    SNAPSHOT,
    WORDED_INTERVAL_STATUS,
    ArtifactError,
    ReferenceStore,
    _load_m2,
    _load_m3_intervals,
    _load_m4,
    _read_verified,
    load_reference_store,
)
from atlas.api.schemas import ComponentStatus, SimulationRequest
from atlas.api.settings import ApiSettings
from atlas.api.simulation import prepare, worker_context
from atlas.api.views import INTERVALS_LABEL
from atlas.api.worker import compute
from atlas.experiment import result_fingerprint
from atlas.optimization_runner import RESULT_FILES
from atlas.optimizer import OptimizationError
from tests.conftest import REPO_ROOT
from tests.m5_helpers import add_api_inputs

pytestmark = pytest.mark.integration

M4 = "docs/results/atlas-m4-optimization-v1"


def _rehash(run: Path, **manifest_fields: Any) -> None:
    """Make a forged M4 run self-consistent: recompute every output hash and the result
    fingerprint, so M4's own inspection passes."""
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(manifest_fields)
    for name in manifest["outputs"]:
        if (run / name).is_file():
            manifest["outputs"][name] = hashlib.sha256((run / name).read_bytes()).hexdigest()
    manifest["result_fingerprint"] = result_fingerprint(manifest["outputs"], RESULT_FILES)
    (run / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _forged(repo_copy: Path, forge: Callable[[Path], dict[str, Any]]) -> ReferenceStore:
    root = add_api_inputs(repo_copy)
    run = root / M4
    _rehash(run, **forge(run))
    from atlas.optimization_runner import inspect_optimization_command

    reports = inspect_optimization_command(run)
    assert [i.code for r in reports for i in r.errors] == []  # M4 itself is satisfied
    return load_reference_store(ApiSettings(repo_root=root))


# --- Forged M4 runs ------------------------------------------------------------------------


def test_a_run_claiming_a_validated_recommendation_is_not_served(repo_copy: Path) -> None:
    def forge(run: Path) -> dict[str, Any]:
        metrics = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
        metrics["validated_recommendation"] = True
        (run / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        return {"validated_recommendation": True}

    store = _forged(repo_copy, forge)
    state = store.status["m4_reference_run"]
    assert not state.ready
    assert "validated_recommendation=false" in str(state.detail)
    assert not store.status["simulation"].ready  # its defaults are no longer verified


@pytest.mark.parametrize(
    ("old", "new", "label"),
    [
        (",HYPOTHETICAL,", ",MEASURED,", "parameter_provenance=HYPOTHETICAL"),
        (",False,False,", ",True,False,", "validated_recommendation=False"),
        (",failed_calibration,", ",decision_grade,", "M3 status"),
    ],
)
def test_a_run_with_an_unlabelled_row_is_not_served(
    repo_copy: Path, old: str, new: str, label: str
) -> None:
    def forge(run: Path) -> dict[str, Any]:
        lines = (run / "plans.csv").read_text(encoding="utf-8").splitlines(keepends=True)
        assert old in lines[1]
        lines[1] = lines[1].replace(old, new, 1)
        (run / "plans.csv").write_text("".join(lines), encoding="utf-8")
        return {}

    store = _forged(repo_copy, forge)
    state = store.status["m4_reference_run"]
    assert not state.ready
    assert label in str(state.detail)
    assert not store.status["simulation"].ready


# --- Verified bytes ------------------------------------------------------------------------


def test_only_the_bytes_whose_hash_was_recorded_are_parsed(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_bytes(b"{}")
    assert _read_verified(path, hashlib.sha256(b"{}").hexdigest(), "x") == b"{}"
    with pytest.raises(ArtifactError, match="SHA-256 differs"):
        _read_verified(path, hashlib.sha256(b"[]").hexdigest(), "x")
    with pytest.raises(ArtifactError, match="SHA-256 differs"):
        _read_verified(path, None, "x")
    with pytest.raises(ArtifactError, match="cannot be read"):
        _read_verified(tmp_path / "missing.json", "0" * 64, "x")


def test_a_file_swapped_after_inspection_is_not_served(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Time of check versus time of use: M4's inspection passes, then plans.csv changes."""
    import atlas.api.reference as reference

    root = add_api_inputs(repo_copy)
    real = reference.inspect_optimization_command

    def inspect_then_swap(directory: Path) -> Any:
        reports = real(directory)
        plans = directory / "plans.csv"
        plans.write_bytes(plans.read_bytes().replace(b",HYPOTHETICAL,", b",MEASURED,", 1))
        return reports

    monkeypatch.setattr(reference, "inspect_optimization_command", inspect_then_swap)
    store = load_reference_store(ApiSettings(repo_root=root))
    state = store.status["m4_reference_run"]
    assert not state.ready
    assert "SHA-256 differs" in str(state.detail)


def test_a_projection_manifest_swapped_between_reads_is_not_served(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The M4 reference component checks the M3 manifest the stress paths were read from;
    the intervals component reads it again for the run id it serves. A manifest changed
    between the two reads must not be served (M8-20)."""
    root = add_api_inputs(repo_copy)
    manifest = root / "docs/results/atlas-m3-intervals-v1/projection/manifest.json"
    real = reference.load_inputs

    def load_then_swap(*args: Any, **kwargs: Any) -> Any:
        inputs = real(*args, **kwargs)
        text = manifest.read_text(encoding="utf-8")
        run_id = json.loads(text)["run_id"]
        manifest.write_text(text.replace(run_id, "FORGED-RUN-ID"), encoding="utf-8")
        return inputs

    monkeypatch.setattr(reference, "load_inputs", load_then_swap)
    store = load_reference_store(ApiSettings(repo_root=root))
    assert store.status["m4_reference_run"].ready  # it checked the bytes read first
    state = store.status[M3_INTERVALS]
    assert not state.ready
    assert "M3 projection manifest changed" in str(state.detail)


# --- Cross-references between the runs ----------------------------------------------------


def test_cross_references_are_checked(api_store: ReferenceStore) -> None:
    config, inputs = api_store.config, api_store.inputs
    assert config is not None
    assert inputs is not None
    # The M2 run must be the configured backtest run, made with the configured M2 config.
    other_run = config.model_copy(
        update={"forecast": config.forecast.model_copy(update={"backtest_run_dir": "x/y"})}
    )
    with pytest.raises(ArtifactError, match="not the configured backtest run"):
        _load_m2(REPO_ROOT, inputs, other_run)
    other_m2 = config.model_copy(
        update={"forecast": config.forecast.model_copy(update={"m2_config_fingerprint": "0" * 64})}
    )
    with pytest.raises(ArtifactError, match="configuration"):
        _load_m2(REPO_ROOT, inputs, other_m2)
    other_data = dataclasses.replace(
        inputs,
        data=dataclasses.replace(
            inputs.data, provenance={**inputs.data.provenance, "data_fingerprint": "0" * 64}
        ),
    )
    with pytest.raises(ArtifactError, match="different data"):
        _load_m2(REPO_ROOT, other_data, config)
    # The M3 intervals must describe the M2 projection the API serves.
    projection = inputs.projection
    shifted = dataclasses.replace(
        inputs,
        projection=dataclasses.replace(
            projection, predictions=(projection.predictions[0] + 1, *projection.predictions[1:])
        ),
    )
    with pytest.raises(ArtifactError, match="differ from the M2 projection"):
        _load_m3_intervals(REPO_ROOT, shifted, config)
    # The M4 run must have used the current M2 projection and carry the current M3 status.
    other_projection = dataclasses.replace(
        inputs,
        projection=dataclasses.replace(
            projection, provenance={**projection.provenance, "projections_sha256": "0" * 64}
        ),
    )
    with pytest.raises(ArtifactError, match="different M2 projection"):
        _load_m4(REPO_ROOT, config, other_projection)
    other_status = dataclasses.replace(
        inputs, status=dataclasses.replace(inputs.status, interval_status="decision_grade")
    )
    with pytest.raises(ArtifactError, match="different M3 status"):
        _load_m4(REPO_ROOT, config, other_status)
    # The projection manifests whose run ids the API serves must be the ones the M4 run
    # recorded: their SHA-256 and their run id are each compared.
    ((level, path),) = inputs.quantiles.items()  # the reference run's one stress path
    for key in ("manifest_sha256", "run_id"):
        forged_projection = dataclasses.replace(
            inputs,
            projection=dataclasses.replace(
                projection, provenance={**projection.provenance, key: "FORGED"}
            ),
        )
        with pytest.raises(ArtifactError, match="M2 projection manifest"):
            _load_m4(REPO_ROOT, config, forged_projection)
        forged_path = dataclasses.replace(path, provenance={**path.provenance, key: "FORGED"})
        with pytest.raises(ArtifactError, match="M3 projection manifest"):
            _load_m4(REPO_ROOT, config, dataclasses.replace(inputs, quantiles={level: forged_path}))
    with pytest.raises(ArtifactError, match="M3 projection manifest"):
        _load_m4(REPO_ROOT, config, dataclasses.replace(inputs, quantiles={}))


# --- Fixed wording that must be true of the data -------------------------------------------


def test_a_status_the_fixed_wording_does_not_state_is_not_served(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # INTERVALS_LABEL, the quantile descriptions of the contract and M4's stress-path notice
    # say the intervals FAILED their calibration. A run whose status is anything else must
    # not be served next to that wording: the whole chain fails closed until it is reviewed.
    assert f"FAILED its pre-registered calibration criteria ({WORDED_INTERVAL_STATUS})" in (
        INTERVALS_LABEL
    )
    monkeypatch.setattr(reference, "WORDED_INTERVAL_STATUS", "decision_grade")
    store = load_reference_store(ApiSettings(repo_root=REPO_ROOT))
    state = store.status[FORECAST]
    assert state.ready is False
    assert "failed_calibration" in str(state.detail)
    assert "wording" in str(state.detail)
    assert [c.component for c in store.components() if c.ready] == [SNAPSHOT]


def test_the_archived_notice_holds_only_for_a_projection_from_the_last_observed_day(
    api_store: ReferenceStore,
) -> None:
    inputs = api_store.inputs
    assert inputs is not None
    reference.check_fixed_wording(inputs)  # the committed runs
    projection = inputs.projection
    earlier = dataclasses.replace(
        inputs,
        projection=dataclasses.replace(projection, origin=projection.origin - timedelta(days=1)),
    )
    with pytest.raises(ArtifactError, match="last observed day"):
        reference.check_fixed_wording(earlier)


def test_require_honours_readiness_not_presence(api_store: ReferenceStore) -> None:
    store = dataclasses.replace(api_store, status=dict(api_store.status))
    store.status[M3_INTERVALS] = ComponentStatus(component=M3_INTERVALS, ready=False, detail="x")
    assert store.m3_intervals is not None  # loaded, but not ready: never served
    with pytest.raises(ApiError) as caught:
        store.require_m3_intervals()
    assert caught.value.code == "artifact_unavailable"


# --- Fail closed in the worker -------------------------------------------------------------


def _config_json(store: ReferenceStore) -> str:
    return prepare(store, SimulationRequest.model_validate({"horizon_days": 2})).config_json


def test_a_result_that_failed_a_check_is_never_ok(
    api_store: ReferenceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = worker_module.analyse_scenario

    def with_problem(*args: Any, **kwargs: Any) -> Any:
        return dataclasses.replace(real(*args, **kwargs), problems=("plan check failed",))

    monkeypatch.setattr(worker_module, "analyse_scenario", with_problem)
    status, message = compute(worker_context(api_store), _config_json(api_store))
    assert status == "failed"
    assert "plan check failed" in message


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (OptimizationError("no certified optimum"), "failed"),
        (RuntimeError("unexpected"), "error"),
    ],
)
def test_worker_exceptions_map_to_documented_outcomes(
    api_store: ReferenceStore,
    monkeypatch: pytest.MonkeyPatch,
    exception: Exception,
    expected: str,
) -> None:
    def raising(*args: Any, **kwargs: Any) -> Any:
        raise exception

    monkeypatch.setattr(worker_module, "analyse_scenario", raising)
    status, message = compute(worker_context(api_store), _config_json(api_store))
    assert status == expected
    if expected == "error":
        assert "Traceback" in message  # logged by the API, never sent to the client
    else:
        assert "certified" in message
