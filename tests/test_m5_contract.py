"""The OpenAPI contract and the wording of every caveat the API sends.

``docs/api/openapi.json`` is the committed contract for M7; it must equal the document the
code generates, and the generated document must describe every route completely.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from atlas.api.reference import ReferenceStore
from atlas.api.schemas import SimulationParameters
from atlas.api.server import openapi_document
from atlas.api.simulation import PARAMETER_UNITS, SimulationService
from atlas.optimization_config import ServiceUnitConfig
from tests.conftest import REPO_ROOT
from tests.m5_helpers import client, settings

pytestmark = pytest.mark.integration

COMMITTED = REPO_ROOT / "docs" / "api" / "openapi.json"


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return openapi_document(settings())


def test_committed_contract_is_current(document: dict[str, Any]) -> None:
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert committed == document, "regenerate: uv run atlas api openapi > docs/api/openapi.json"
    # The committed file is exactly what the command prints (sorted keys, two spaces).
    assert COMMITTED.read_text(encoding="utf-8") == (
        json.dumps(document, indent=2, sort_keys=True) + "\n"
    )


def test_every_operation_is_documented(document: dict[str, Any]) -> None:
    operations = [
        (path, method, op)
        for path, item in document["paths"].items()
        for method, op in item.items()
    ]
    assert len(operations) == 14
    ids = [op["operationId"] for _, _, op in operations]
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"(get|list|compare|run)[A-Z][A-Za-z]+", i) for i in ids)
    for path, _method, op in operations:
        assert path.startswith(("/api/v1/", "/health/"))
        assert op["summary"]
        assert op["description"]
        assert op["tags"]
        responses = op["responses"]
        assert "200" in responses
        assert "422" in responses  # unknown query parameters are refused everywhere
        for status, response in responses.items():
            if status == "200":
                continue
            schema = response["content"]["application/json"]["schema"]["$ref"]
            expected = (
                "ReadyResponse"
                if path == "/health/ready" and status == "503"
                else ("ErrorResponse")
            )
            assert schema.endswith("/" + expected), (path, status)
    assert "HTTPValidationError" not in document["components"]["schemas"]


def test_integrity_is_fixed_by_the_schema(document: dict[str, Any]) -> None:
    schemas = document["components"]["schemas"]
    integrity = schemas["Integrity"]["properties"]
    assert integrity["validated_recommendation"]["const"] is False
    assert integrity["point_forecast_validated"]["const"] is False
    assert set(integrity["uncertainty_interval_status"]["enum"]) == {
        "decision_grade",
        "failed_calibration",
        "insufficient_evidence",
        "unavailable",
    }
    envelopes = [
        name
        for name in schemas
        if name.endswith("Response")
        and name
        not in (
            "ErrorResponse",
            "LiveResponse",
            "ReadyResponse",
        )
    ]
    assert len(envelopes) == 12
    for name in envelopes:
        assert set(schemas[name]["required"]) == {"data", "integrity", "provenance"}, name


def test_request_schema_matches_the_validation(document: dict[str, Any]) -> None:
    schema = document["components"]["schemas"]["SimulationParameters"]
    assert schema["additionalProperties"] is False
    for name, field in SimulationParameters.model_fields.items():
        prop = schema["properties"][name]
        bounds = next(option for option in prop["anyOf"] if option["type"] != "null")
        lows = [getattr(m, "ge", None) for m in field.metadata]
        highs = [getattr(m, "le", None) for m in field.metadata]
        assert bounds["minimum"] == next(v for v in lows if v is not None)
        assert bounds["maximum"] == next(v for v in highs if v is not None)
    examples = document["paths"]["/api/v1/simulations"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    assert set(examples) == {"reference", "smaller_budget", "two_weeks_more_surge", "stress_path"}


def test_parameter_units_are_the_m4_units() -> None:
    for name, unit in PARAMETER_UNITS.items():
        if name == "discretionary_budget":
            assert unit == "usd"
            continue
        annotation = ServiceUnitConfig.model_fields[name].annotation
        text = str(annotation)
        quantity = next(
            cls for cls in getattr(annotation, "__args__", (annotation,)) if cls is not type(None)
        )
        expected = quantity.model_fields["unit"].annotation.__args__[0]
        assert unit == expected, (name, text)
    assert set(PARAMETER_UNITS) == set(SimulationParameters.model_fields)


def test_served_openapi_equals_the_document(
    api_store: ReferenceStore, document: dict[str, Any]
) -> None:
    with client(api_store) as api:
        assert api.get("/openapi.json").json() == document


# --- Wording -----------------------------------------------------------------------------

#: The M4 report lint (tests/test_m4_runner_cli.py), applied to everything the API says.
FORBIDDEN = (
    "saves",
    "saving",
    "reduces cost",
    "cost-effective",
    "optimal staffing",
    "should hire",
    "we recommend",
    "is recommended",
    "confidence",
    "90 % safe",
    "chance of",
    "guarantee",
    "validated recommendation",
)
ALLOWED = (
    "not a validated staffing, budget or service-capacity recommendation",
    "nothing this api returns is a validated staffing, budget or service-capacity recommendation",
    "nothing returned is a validated staffing, budget or service-capacity recommendation",
    "nothing here is a validated staffing, budget or service-capacity recommendation",
    "no result is a validated staffing, budget or service-capacity recommendation",
    "nothing in it is a validated staffing or budget recommendation",
    "validated_recommendation",
    "no chance constraint",
)


def _texts(value: Any, key: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _texts(v, str(k))
    elif isinstance(value, list):
        for item in value:
            yield from _texts(item, key)
    elif isinstance(value, str) and len(value) > 12 and " " in value:
        yield key, value


def assert_honest(text: str, where: str) -> None:
    lowered = text.lower()
    for phrase in sorted(ALLOWED, key=len, reverse=True):
        lowered = lowered.replace(phrase, " ")
    for word in FORBIDDEN:
        assert word not in lowered, (where, word, text)


def test_no_promotional_or_probabilistic_wording(
    api_store: ReferenceStore, api_simulator: SimulationService, document: dict[str, Any]
) -> None:
    with client(api_store, api_simulator) as api:
        bodies = [
            api.get(path).json()
            for path in (
                "/api/v1/system",
                "/api/v1/forecasts",
                "/api/v1/forecasts/history",
                "/api/v1/uncertainty/status",
                "/api/v1/uncertainty/intervals",
                "/api/v1/scenarios",
                "/api/v1/scenarios/comparison",
                "/api/v1/simulations/parameters",
            )
        ]
        bodies.append(api.post("/api/v1/simulations", json={"horizon_days": 5}).json())
    for body in bodies:
        # Labels the API composes (not texts copied from M2 metrics, e.g. model names).
        for where, text in _texts(body["integrity"]):
            assert_honest(text, where)
        for key in ("limitations", "notes", "caveat", "label", "plan_label"):
            for where, text in _texts({key: _find(body["data"], key)}):
                assert_honest(text, where)
    for where, text in _texts({"info": document["info"], "tags": document["tags"]}):
        assert_honest(text, where)


def _find(value: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k == key:
                found.append(v)
            found.extend(_find(v, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(_find(item, key))
    return found


def test_every_data_response_carries_the_status_guidance(api_store: ReferenceStore) -> None:
    with client(api_store) as api:
        for path in (
            "/api/v1/forecasts",
            "/api/v1/uncertainty/intervals",
            "/api/v1/scenarios",
            "/api/v1/forecasts/metrics",
        ):
            notices = " ".join(api.get(path).json()["integrity"]["notices"])
            assert "not decision-grade" in notices, path


def test_live_docs_render(api_store: ReferenceStore) -> None:
    with client(api_store) as api:
        assert "swagger" in api.get("/docs").text.lower()
        assert "redoc" in api.get("/redoc").text.lower()


def test_testclient_import_has_no_deprecation() -> None:
    # pytest turns warnings into errors: importing the client must stay warning-free.
    assert TestClient.__module__.startswith("starlette")
