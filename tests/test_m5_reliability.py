"""Reliability: missing or corrupted artifacts fail safely; nothing is ever written.

Each test copies the repository inputs the API reads into ``tmp_path``, damages one file,
and loads a reference store from the copy. The real repository is never modified.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from atlas.api.reference import COMPONENTS, ReferenceStore, load_reference_store
from atlas.api.settings import ApiSettings
from tests.conftest import REPO_ROOT
from tests.m5_helpers import add_api_inputs, client, tree_hashes

pytestmark = pytest.mark.integration

M4 = "docs/results/atlas-m4-optimization-v1"
M2_FULL = "docs/results/atlas-m2-rolling-origin-v1/full"
M2_PROJECTION = "docs/results/atlas-m2-rolling-origin-v1/projection"
M3_FULL = "docs/results/atlas-m3-intervals-v1/full"
M3_PROJECTION = "docs/results/atlas-m3-intervals-v1/projection"
CONFIG = "configs/optimization/atlas-m4-optimization-v1.json"


@pytest.fixture
def api_repo(repo_copy: Path) -> Path:
    return add_api_inputs(repo_copy)


def load(root: Path) -> ReferenceStore:
    return load_reference_store(ApiSettings(repo_root=root))


def ready(store: ReferenceStore) -> dict[str, bool]:
    return {c.component: c.ready for c in store.components()}


def append(relative: str, payload: bytes = b"\n") -> Callable[[Path], None]:
    def damage(root: Path) -> None:
        with (root / relative).open("ab") as handle:
            handle.write(payload)

    return damage


def delete(relative: str) -> Callable[[Path], None]:
    def damage(root: Path) -> None:
        (root / relative).unlink()

    return damage


def replace_text(relative: str, old: str, new: str) -> Callable[[Path], None]:
    def damage(root: Path) -> None:
        path = root / relative
        text = path.read_text(encoding="utf-8")
        assert old in text
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    return damage


def test_a_complete_copy_is_ready(api_repo: Path) -> None:
    store = load(api_repo)
    assert ready(store) == dict.fromkeys(COMPONENTS, True)


#: (damage, components expected NOT ready). The forecast component carries the whole
#: provenance chain (M2 projection and accuracy, M3 status and projection pins), so any
#: break in it takes down every component built on it: fail closed.
CHAIN = {
    "forecast_and_uncertainty_status",
    "m2_backtest",
    "m3_projection_intervals",
    "m4_reference_run",
    "simulation",
}
#: Simulations start from the reference configuration's defaults, so they are ready only
#: when the M4 reference run made with that configuration is verified.
M4_CHAIN = {"m4_reference_run", "simulation"}
CASES = {
    "m4_metrics_edited": (append(f"{M4}/metrics.json", b" "), M4_CHAIN),
    "m4_plans_missing": (delete(f"{M4}/plans.csv"), M4_CHAIN),
    "m4_manifest_not_json": (replace_text(f"{M4}/manifest.json", "{", "["), M4_CHAIN),
    "m4_manifest_incomplete": (
        replace_text(f"{M4}/manifest.json", '"complete": true', '"complete": false'),
        M4_CHAIN,
    ),
    # The significance level is read from the run's configuration, which must still give
    # the recorded configuration fingerprint.
    "m2_manifest_alpha_edited": (
        replace_text(f"{M2_FULL}/manifest.json", '"alpha": 0.05', '"alpha": 0.5'),
        {"m2_backtest"},
    ),
    "m2_projection_edited": (
        replace_text(f"{M2_PROJECTION}/projections.csv", "11123.683380141376", "11123.6833801414"),
        CHAIN,
    ),
    "m2_metrics_edited": (append(f"{M2_FULL}/metrics.json", b" "), CHAIN),
    # The projection manifests are pinned by no configuration: the M4 reference run's record
    # of each one (SHA-256 and run id) is what their served run ids are checked against.
    "m2_projection_manifest_run_id_edited": (
        replace_text(
            f"{M2_PROJECTION}/manifest.json",
            '"run_id": "atlas-m2-rolling-origin-v1-78da64e8-5ab8b3a5"',
            '"run_id": "FORGED-RUN-ID"',
        ),
        M4_CHAIN,
    ),
    "m3_projection_manifest_run_id_edited": (
        replace_text(
            f"{M3_PROJECTION}/manifest.json",
            '"run_id": "atlas-m3-intervals-v1-5afd3206-5ab8b3a5"',
            '"run_id": "FORGED-RUN-ID"',
        ),
        M4_CHAIN,
    ),
    "m3_status_edited": (
        replace_text(f"{M3_FULL}/calibration_status.json", "failed_calibration", "decision_grade"),
        CHAIN,
    ),
    "m3_intervals_missing": (delete(f"{M3_PROJECTION}/projection_intervals.csv"), CHAIN),
    "reference_config_edited": (replace_text(CONFIG, '"value": 8000', '"value": 8001'), M4_CHAIN),
    "reference_config_not_json": (replace_text(CONFIG, "{", "{{"), CHAIN),
    "snapshot_data_edited": (
        replace_text("01 Data/daily_total.csv", "2023-01-01,6209", "2023-01-01,6210"),
        {"snapshot"} | CHAIN,
    ),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_damaged_artifacts_make_components_unavailable(api_repo: Path, case: str) -> None:
    damage, failing = CASES[case]
    damage(api_repo)
    store = load(api_repo)
    states = ready(store)
    assert {name for name, ok in states.items() if not ok} == failing
    for component in store.components():
        if not component.ready:
            assert component.detail
            assert str(api_repo) not in component.detail
            assert "Traceback" not in component.detail
            assert len(component.detail) <= 300


def test_a_forged_projection_run_id_makes_readiness_fail(api_repo: Path) -> None:
    CASES["m2_projection_manifest_run_id_edited"][0](api_repo)
    store = load(api_repo)
    with client(store) as api:
        ready = api.get("/health/ready")
        assert ready.status_code == 503
        states = {c["component"]: c for c in ready.json()["components"]}
        assert "M2 projection manifest" in str(states["m4_reference_run"]["detail"])
        # The forged identifier never reaches a response that rests on the M4 record.
        for path in ("/api/v1/scenarios", "/api/v1/simulations/parameters"):
            response = api.get(path)
            assert response.status_code == 503
            assert "FORGED-RUN-ID" not in response.text


def test_endpoints_of_unavailable_components_answer_503(api_repo: Path) -> None:
    append(f"{M4}/metrics.json", b" ")(api_repo)
    store = load(api_repo)
    with client(store) as api:
        assert api.get("/health/live").status_code == 200
        ready_response = api.get("/health/ready")
        assert ready_response.status_code == 503
        states = {c["component"]: c for c in ready_response.json()["components"]}
        assert states["m4_reference_run"]["ready"] is False
        for path in (
            "/api/v1/scenarios",
            "/api/v1/scenarios/normal",
            "/api/v1/scenarios/comparison",
        ):
            response = api.get(path)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "artifact_unavailable"
        # Every other component keeps serving.
        for path in (
            "/api/v1/forecasts",
            "/api/v1/forecasts/history",
            "/api/v1/uncertainty/status",
        ):
            assert api.get(path).status_code == 200
        system = api.get("/api/v1/system").json()
        kinds = {s["kind"] for s in system["provenance"]["sources"]}
        assert "m4_reference_run" not in kinds


def test_a_broken_snapshot_still_starts_and_reports(api_repo: Path) -> None:
    replace_text("01 Data/daily_total.csv", "2023-01-01,6209", "2023-01-01,6210")(api_repo)
    store = load(api_repo)
    with client(store) as api:
        assert api.get("/health/live").status_code == 200
        assert api.get("/health/ready").status_code == 503
        system = api.get("/api/v1/system")
        assert system.status_code == 200
        body = system.json()
        assert body["data"]["snapshot"] is None
        assert body["data"]["scientific_status"] is None
        assert body["integrity"]["uncertainty_interval_status"] == "unavailable"
        for path in ("/api/v1/forecasts/history", "/api/v1/forecasts", "/api/v1/models"):
            assert api.get(path).status_code == 503
        simulation = api.post("/api/v1/simulations", json={})
        assert simulation.status_code == 503


def test_a_missing_repository_input_is_reported(api_repo: Path) -> None:
    delete(f"{M3_FULL}/manifest.json")(api_repo)
    store = load(api_repo)
    assert not store.ready
    detail = store.status["forecast_and_uncertainty_status"].detail
    assert detail is not None
    assert str(api_repo) not in detail


def test_serving_never_writes(api_store: ReferenceStore) -> None:
    watched = [
        REPO_ROOT / "docs" / "results",
        REPO_ROOT / "configs",
        REPO_ROOT / "snapshots",
        REPO_ROOT / "01 Data",
    ]
    before = tree_hashes(*watched)
    with client(api_store) as api:
        for path in (
            "/api/v1/system",
            "/api/v1/models",
            "/api/v1/forecasts",
            "/api/v1/forecasts/history?start=2023-01-01&end=2026-06-30",
            "/api/v1/forecasts/metrics?scope=horizon",
            "/api/v1/uncertainty/status",
            "/api/v1/uncertainty/intervals",
            "/api/v1/scenarios",
            "/api/v1/scenarios/m3_marginal_q90_stress",
            "/api/v1/scenarios/comparison",
            "/api/v1/simulations/parameters",
        ):
            assert api.get(path).status_code == 200
    assert tree_hashes(*watched) == before


def test_store_repr_is_small(api_store: ReferenceStore) -> None:
    text = repr(api_store)
    assert len(text) < 400
    assert "snapshot=ready" in text


def test_component_order_and_dependencies(api_repo: Path) -> None:
    # Without the snapshot nothing downstream is even attempted.
    (api_repo / "snapshots" / "legacy-v0" / "manifest.json").write_text("{}", encoding="utf-8")
    store = load(api_repo)
    assert [c.component for c in store.components()] == list(COMPONENTS)
    for name in COMPONENTS[1:]:
        detail = store.status[name].detail
        assert detail is not None
        assert detail.startswith("depends on:")
    assert json.dumps([c.model_dump() for c in store.components()])
