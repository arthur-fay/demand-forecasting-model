"""The dashboard's test fixtures are the API's real answers, byte for byte (M7).

The TypeScript tests (``web/src/**/*.test.ts*``) render the dashboard from recorded API
responses in ``web/src/test/fixtures``. This test re-asks the API for each of them and
requires the same bytes, so the frontend tests can never drift from what the API serves:
a change to the API or to a committed result fails here until the fixtures are recorded
again (``ATLAS_UPDATE_DASHBOARD_FIXTURES=1 uv run pytest tests/test_m7_fixtures.py --no-cov``;
without ``--no-cov`` the whole-package coverage gate fails a run of one module).

GET bodies are deterministic (docs/API.md section 4), and so is a simulation's body for a
given request. The one exception is ``/api/v1/system``: its worker counters change as
simulations run, so they are compared without the ``simulation_workers`` block.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import pytest

from tests.conftest import REPO_ROOT
from tests.m5_helpers import client

if TYPE_CHECKING:
    from atlas.api.reference import ReferenceStore
    from atlas.api.simulation import SimulationService

FIXTURES = REPO_ROOT / "web" / "src" / "test" / "fixtures"
UPDATE = os.environ.get("ATLAS_UPDATE_DASHBOARD_FIXTURES") == "1"
SIMULATION = {
    "horizon_days": 7,
    "parameters": {"discretionary_budget": 20000, "backlog_penalty": 3},
}

CASES: dict[str, tuple[str, str, Any]] = {
    "system.json": ("GET", "/api/v1/system", None),
    "models.json": ("GET", "/api/v1/models", None),
    "forecast.json": ("GET", "/api/v1/forecasts?horizon_days=30", None),
    "history.json": ("GET", "/api/v1/forecasts/history", None),
    "metrics-bucket.json": ("GET", "/api/v1/forecasts/metrics?scope=bucket", None),
    "metrics-horizon-confirmation.json": (
        "GET",
        "/api/v1/forecasts/metrics?scope=horizon&period=confirmation",
        None,
    ),
    "uncertainty-status.json": ("GET", "/api/v1/uncertainty/status", None),
    "intervals.json": ("GET", "/api/v1/uncertainty/intervals?horizon_days=30", None),
    "scenarios.json": ("GET", "/api/v1/scenarios", None),
    "comparison.json": ("GET", "/api/v1/scenarios/comparison", None),
    "scenario-normal.json": ("GET", "/api/v1/scenarios/normal", None),
    "scenario-reduced_budget.json": ("GET", "/api/v1/scenarios/reduced_budget", None),
    "simulation-parameters.json": ("GET", "/api/v1/simulations/parameters", None),
    "simulation-7d-budget.json": ("POST", "/api/v1/simulations", SIMULATION),
}


def _comparable(name: str, body: bytes) -> Any:
    document = json.loads(body)
    if name == "system.json":
        document["data"].pop("simulation_workers", None)
    return document


@pytest.mark.parametrize("name", sorted(CASES))
def test_each_fixture_is_the_api_answer(
    name: str, api_store: ReferenceStore, api_simulator: SimulationService
) -> None:
    method, path, body = CASES[name]
    with client(api_store, api_simulator) as test_client:
        answer = test_client.get(path) if method == "GET" else test_client.post(path, json=body)
    assert answer.status_code == 200, answer.text
    fixture = FIXTURES / name
    if UPDATE:
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_bytes(answer.content)
    assert fixture.is_file(), f"record the fixtures: ATLAS_UPDATE_DASHBOARD_FIXTURES=1 ({name})"
    if name == "system.json":
        assert _comparable(name, fixture.read_bytes()) == _comparable(name, answer.content)
    else:
        assert fixture.read_bytes() == answer.content, f"{name} differs from the API's answer"


def test_no_stray_fixture() -> None:
    present = {path.name for path in FIXTURES.glob("*.json")}
    assert present == set(CASES), "every fixture is checked against the API"
