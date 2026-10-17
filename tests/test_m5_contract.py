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
from atlas.api.schemas import (
    MIN_NONZERO_RATE,
    NULLABLE_PARAMETERS,
    RATE_PARAMETERS,
    SimulationParameters,
)
from atlas.api.server import openapi_document
from atlas.api.simulation import PARAMETER_UNITS, SimulationService
from atlas.optimization_config import ServiceUnitConfig
from tests.conftest import REPO_ROOT
from tests.m5_helpers import M2_PROJECTION, client, read_csv, read_json, settings

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
    # Always sent, so always required: a generated client never types them as optional.
    for name in ("Integrity", "ScientificStatus"):
        required = set(schemas[name]["required"])
        assert {"validated_recommendation", "point_forecast_validated"} <= required, name
    for name, schema in schemas.items():
        if name not in ("SimulationRequest", "SimulationParameters", "DemandSettings"):
            assert set(schema.get("required", [])) == set(schema.get("properties", {})), name


def test_contract_details_for_generated_clients(document: dict[str, Any]) -> None:
    schemas = document["components"]["schemas"]
    codes = set(schemas["ErrorBody"]["properties"]["code"]["enum"])
    assert {"validation_error", "cors_rejected", "server_busy", "simulations_busy"} <= codes
    # The API key is optional unless the server sets one: {} is an alternative.
    post = document["paths"]["/api/v1/simulations"]["post"]
    assert {} in post["security"]
    assert {"APIKeyHeader": []} in post["security"]
    for item in document["paths"].values():
        for operation in item.values():
            assert operation["x-atlas-cost"] in ("static", "precomputed", "bounded_computation")
    adjustments = schemas["ScenarioOutcome"]["properties"]["adjustments"]
    assert adjustments["additionalProperties"]["$ref"].endswith("/HypotheticalQuantity")
    assert schemas["HypotheticalQuantity"]["properties"]["provenance"]["const"] == "HYPOTHETICAL"


def test_request_schema_matches_the_validation(document: dict[str, Any]) -> None:
    schema = document["components"]["schemas"]["SimulationParameters"]
    assert schema["additionalProperties"] is False
    for name, field in SimulationParameters.model_fields.items():
        prop = schema["properties"][name]
        assert "default" not in prop  # omitted means the reference value, not null
        options = prop.get("anyOf", [prop])
        # null is published exactly where the server accepts it.
        assert ({"type": "null"} in options) is (name in NULLABLE_PARAMETERS), name
        numbers = [o for o in options if o.get("type") != "null"]
        if name in RATE_PARAMETERS:  # 0, or at least 0.01 (never null)
            zero, material = numbers
            assert zero["const"] == 0
            assert material["minimum"] == MIN_NONZERO_RATE
            bounds = material
        else:
            (bounds,) = numbers
        lows = [getattr(m, "ge", None) for m in field.metadata]
        highs = [getattr(m, "le", None) for m in field.metadata]
        if name not in RATE_PARAMETERS:
            assert bounds["minimum"] == next(v for v in lows if v is not None)
        assert bounds["maximum"] == next(v for v in highs if v is not None)
    examples = document["paths"]["/api/v1/simulations"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    assert set(examples) == {"reference", "smaller_budget", "two_weeks_more_surge", "stress_path"}


def _keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def test_contract_uses_standard_json_schema_bounds(document: dict[str, Any]) -> None:
    assert not {"ge", "le", "gt", "lt"} & set(_keys(document))
    for path in ("/api/v1/forecasts", "/api/v1/uncertainty/intervals"):
        (parameter,) = document["paths"][path]["get"]["parameters"]
        assert parameter["schema"]["minimum"] == 1
        assert parameter["schema"]["maximum"] == 30


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

#: The M4 report lint (tests/test_m4_runner_cli.py), applied to every string the API sends
#: and to every description in the contract.
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
)
#: "probability" only negated.
ALLOWED_PROBABILITY = (
    "no chance constraint, service-level guarantee or probability of meeting a target is "
    "computed or implied",
    "carries no probability",
    "carry no probability",
    "never a probability",
    "not_a_probability",
    "no interval or probability is decision-grade",
    "intervals and probabilities are not decision-grade",  # M3's status guidance
    # M3's recorded evidence that its exceedance probabilities failed calibration.
    "mean probability",
    "no probability",
)
#: "recommend" only in a not-a-recommendation phrase, or for the M2 *forecasting model*
#: recommendation (which model to project with), never for an M4 plan.
ALLOWED_RECOMMENDATION = (
    "nothing this api returns is a validated staffing, budget or service-capacity recommendation",
    "nothing returned is a validated staffing, budget or service-capacity recommendation",
    "nothing here is a validated staffing, budget or service-capacity recommendation",
    "no result is a validated staffing, budget or service-capacity recommendation",
    "not a validated staffing, budget or service-capacity recommendation",
    "nothing in it is a validated staffing or budget recommendation",
    "not a budget recommendation",
    "validated_recommendation",
    "recommended model",
    "recommended m2 model",
    "confirmation-period recommendation",
    "confirmation tests behind the recommendation",
    "m2 run that recommended the projected model",
    "recommended_model",
    # The M2 selection caveat (views.SELECTION_EVIDENCE_NOTICE): the model recommendation.
    "the recommendation stands only under the pre-registered rule",
)
#: "guarantee" only in the negated disclaimer.
ALLOWED_GUARANTEE = ("no chance constraint, service-level guarantee",)


def _strip(text: str, allowed: tuple[str, ...]) -> str:
    for phrase in sorted(allowed, key=len, reverse=True):
        text = text.replace(phrase, " ")
    return text


def honesty_problems(text: str) -> list[str]:
    """What the M4 lint would flag in ``text`` (empty when it passes)."""
    lowered = " ".join(text.lower().split())
    problems = [word for word in FORBIDDEN if word in lowered]
    if "probabilit" in _strip(lowered, ALLOWED_PROBABILITY):
        problems.append("probability (not negated)")
    if "recommend" in _strip(lowered, ALLOWED_RECOMMENDATION):
        problems.append("recommend (outside an allowed phrase)")
    if "guarantee" in _strip(lowered, ALLOWED_GUARANTEE):
        problems.append("guarantee (not negated)")
    if "calibrated" in _strip(lowered, ("not calibrated",)):
        problems.append("calibrated (not negated)")
    return problems


def test_wording_lint_catches_what_m4_forbids() -> None:
    assert honesty_problems("this plan has a high probability of success") == [
        "probability (not negated)"
    ]
    assert honesty_problems("a staffing recommendation") == [
        "recommend (outside an allowed phrase)"
    ]
    assert honesty_problems("intervals calibrated on past errors") == ["calibrated (not negated)"]
    assert honesty_problems("we recommend this; it saves money") == [
        "saves",
        "we recommend",
        "recommend (outside an allowed phrase)",
    ]
    assert honesty_problems("they carry no probability; not a budget recommendation") == []


def _texts(value: Any, where: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _texts(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _texts(item, f"{where}[{i}]")
    elif isinstance(value, str):
        yield where, value


def _served_bodies(api: TestClient) -> list[tuple[str, Any]]:
    paths = [
        "/health/live",
        "/health/ready",
        "/api/v1/system",
        "/api/v1/models",
        "/api/v1/forecasts",
        "/api/v1/forecasts/history",
        "/api/v1/forecasts/metrics",
        "/api/v1/forecasts/metrics?scope=horizon",
        "/api/v1/uncertainty/status",
        "/api/v1/uncertainty/intervals",
        "/api/v1/scenarios",
        "/api/v1/scenarios/comparison",
        "/api/v1/simulations/parameters",
        "/api/v1/scenarios/nope",
        "/api/v1/forecasts/history?end=2030-01-01",
    ]
    paths += [
        f"/api/v1/scenarios/{s['scenario_id']}"
        for s in api.get("/api/v1/scenarios").json()["data"]["scenarios"]
    ]
    bodies = [(path, api.get(path).json()) for path in paths]
    for body in (
        {"horizon_days": 5},
        {"horizon_days": 7, "parameters": {"discretionary_budget": 1000}},
        {"demand": {"basis": "m3_marginal_quantile", "quantile": 0.975}},
        {"parameters": {"regular_hours_per_day": 4643.5}},
        {"horizon_days": 5, "parameters": {"surge_available_from_day": 20}},
        {"parameters": {"regular_wage": None}},
        {"parameters": {"unknown": 1}},
    ):
        bodies.append((f"POST {body}", api.post("/api/v1/simulations", json=body).json()))
    return bodies


def test_nothing_the_api_says_breaks_the_m4_wording_rules(
    api_store: ReferenceStore, api_simulator: SimulationService, document: dict[str, Any]
) -> None:
    with client(api_store, api_simulator) as api:
        bodies = _served_bodies(api)
    flagged = [
        (path, where, text, problems)
        for path, body in bodies
        for where, text in _texts(body)
        if (problems := honesty_problems(text))
    ]
    assert flagged == []
    described = [
        (where, text, problems)
        for where, text in _texts(document)
        # Titles are generated from field names (validated_recommendation, ...).
        if where.endswith((".description", ".summary")) and (problems := honesty_problems(text))
    ]
    assert described == []


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


def _archived_projection_notice() -> str:
    """The archived-projection notice, from the committed M2 projection files."""
    manifest = read_json(M2_PROJECTION / "manifest.json")
    last = manifest["data"]["last_date"]
    targets = sorted({r["target_date"] for r in read_csv(M2_PROJECTION / "projections.csv")})
    return (
        f"ARCHIVED PROJECTION: issued from the frozen snapshot, whose last observed day is "
        f"{last}, for the target days {targets[0]} to {targets[-1]}. It is not a live "
        f"forecast, and it has never been scored: no outcome after {last} is in the snapshot."
    )


def test_every_projected_response_says_the_projection_is_archived(
    api_store: ReferenceStore, api_simulator: SimulationService
) -> None:
    notice = _archived_projection_notice()
    assert notice == (
        "ARCHIVED PROJECTION: issued from the frozen snapshot, whose last observed day is "
        "2026-06-30, for the target days 2026-07-01 to 2026-07-30. It is not a live forecast, "
        "and it has never been scored: no outcome after 2026-06-30 is in the snapshot."
    )
    with client(api_store, api_simulator) as api:
        scenarios = api.get("/api/v1/scenarios").json()["data"]["scenarios"]
        paths = [
            "/api/v1/forecasts",
            "/api/v1/forecasts?horizon_days=1",
            "/api/v1/uncertainty/intervals",
            "/api/v1/scenarios",
            "/api/v1/scenarios/comparison",
            "/api/v1/simulations/parameters",
            *(f"/api/v1/scenarios/{s['scenario_id']}" for s in scenarios),
        ]
        responses = {path: api.get(path) for path in paths}
        stress = {"basis": "m3_marginal_quantile", "quantile": 0.9}
        for body in ({}, {"horizon_days": 5, "demand": stress}):
            responses[f"POST {body}"] = api.post("/api/v1/simulations", json=body)
        system = api.get("/api/v1/system").json()
        history = api.get("/api/v1/forecasts/history").json()
    for name, response in responses.items():
        assert response.status_code == 200, (name, response.text)
        assert notice in response.json()["integrity"]["notices"], name
    assert notice in system["data"]["scientific_status"]["limitations"]
    # Observed history is not a projection.
    assert not any("ARCHIVED" in n for n in history["integrity"]["notices"])


def test_certificate_fields_say_they_are_tolerance_ratios(document: dict[str, Any]) -> None:
    schema = document["components"]["schemas"]["Certificate"]
    fields = schema["properties"]
    for name in ("primal", "dual_sign", "stationarity", "duality_gap"):
        description = fields[name]["description"]
        assert "in units of its tolerance" in description, name
        assert "at most 1 passes" in description, name
    assert "not the absolute gap" in fields["duality_gap"]["description"]
    assert "1e-7" in fields["primal"]["description"]
    assert "1e-9" in fields["duality_gap"]["description"]
    for name in ("holds", "primal_objective", "dual_objective"):
        assert fields[name]["description"], name
    assert "certificate" in schema["description"].lower()


def test_live_docs_render(api_store: ReferenceStore) -> None:
    with client(api_store) as api:
        # Built during startup: the first /openapi.json never stalls other requests.
        assert api.app.openapi_schema is not None  # type: ignore[attr-defined]
        assert "swagger" in api.get("/docs").text.lower()
        assert "redoc" in api.get("/redoc").text.lower()


def test_testclient_import_has_no_deprecation() -> None:
    # pytest turns warnings into errors: importing the client must stay warning-free.
    assert TestClient.__module__.startswith("starlette")


def _operation(document: dict[str, Any], method: str, url: str) -> dict[str, Any]:
    path = url.split("?", 1)[0]
    for template, item in document["paths"].items():
        if re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), path) and method in item:
            operation: dict[str, Any] = item[method]
            return operation
    raise AssertionError(f"no operation for {method} {path}")


ERROR_CASES: list[tuple[str, str, dict[str, Any], str]] = [
    ("get", "/health/live?x=1", {}, "validation_error"),
    ("get", "/health/ready", {"headers": {"host": "evil.example"}}, "invalid_host"),
    ("get", "/api/v1/system?x=1", {}, "validation_error"),
    ("get", "/api/v1/models?x=1", {}, "validation_error"),
    ("get", "/api/v1/forecasts?horizon_days=31", {}, "validation_error"),
    ("get", "/api/v1/forecasts/history?end=0001-01-01", {}, "validation_error"),
    ("get", "/api/v1/forecasts/metrics?model_id=nope", {}, "not_found"),
    ("get", "/api/v1/uncertainty/status", {"headers": {"host": "evil.example"}}, "invalid_host"),
    ("get", "/api/v1/uncertainty/intervals?horizon_days=0", {}, "validation_error"),
    ("get", "/api/v1/scenarios?x=1", {}, "validation_error"),
    ("get", "/api/v1/scenarios/nope", {}, "not_found"),
    ("get", "/api/v1/scenarios/comparison?baseline=nope", {}, "not_found"),
    ("get", "/api/v1/simulations/parameters?x=1", {}, "validation_error"),
    ("post", "/api/v1/simulations", {"content": b"{}"}, "unsupported_media_type"),
    (
        "post",
        "/api/v1/simulations",
        {"content": b'{"horizon_days": "\xff"}', "headers": {"content-type": "application/json"}},
        "bad_request",
    ),
    (
        "post",
        "/api/v1/simulations",
        {"content": b"{" + b" " * 20_000 + b"}", "headers": {"content-type": "application/json"}},
        "payload_too_large",
    ),
    ("post", "/api/v1/simulations", {"json": {"horizon_days": 0}}, "validation_error"),
    (
        "post",
        "/api/v1/simulations",
        {"json": {"parameters": {"regular_hours_per_day": 4643.5}}},
        "simulation_rejected",
    ),
]


def test_observed_errors_are_documented_on_their_operation(
    api_store: ReferenceStore, api_simulator: SimulationService, document: dict[str, Any]
) -> None:
    with client(api_store, api_simulator) as api:
        observed = [
            (method, url, api.request(method.upper(), url, **kwargs), code)
            for method, url, kwargs, code in ERROR_CASES
        ]
    with client(api_store, api_simulator, api_key="k" * 32) as api:
        observed.append(
            (
                "post",
                "/api/v1/simulations",
                api.post("/api/v1/simulations", json={}),
                "unauthorized",
            )
        )
    for method, url, response, code in observed:
        assert response.json()["error"]["code"] == code, (url, response.text)
        documented = _operation(document, method, url)["responses"]
        status = str(response.status_code)
        assert status in documented, (method, url, status)
        assert code in documented[status]["description"], (method, url, code)
