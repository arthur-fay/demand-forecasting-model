"""Security controls of the M5 API: hosts, CORS, limits, keys, headers and safe errors."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from atlas.api.app import create_app
from atlas.api.errors import ApiError
from atlas.api.middleware import host_allowed
from atlas.api.reference import ReferenceStore
from atlas.api.server import check_bind, is_loopback
from atlas.api.simulation import SimulationService
from atlas.errors import ConfigError
from tests.m5_helpers import client, settings

pytestmark = pytest.mark.integration

KEY = "k" * 16 + "-Secret-Value-1234"
SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
}


@pytest.fixture(scope="module")
def api(api_store: ReferenceStore, api_simulator: SimulationService) -> Iterator[TestClient]:
    with client(api_store, api_simulator) as test_client:
        yield test_client


def assert_safe_error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id", "details"}
    assert body["error"]["code"] == code
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    text = response.text
    for leak in ("Traceback", 'File "', "/home/", "site-packages", KEY):
        assert leak not in text
    return body


# --- Headers ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/health/live"),
        ("GET", "/api/v1/forecasts"),
        ("GET", "/api/v1/scenarios/nope"),
        ("GET", "/does/not/exist"),
        ("POST", "/api/v1/simulations"),
    ],
)
def test_every_response_carries_security_headers(api: TestClient, method: str, path: str) -> None:
    response = api.request(method, path, content=b"{}" if method == "POST" else None)
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value
    assert (
        response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    )
    assert len(response.headers["x-request-id"]) == 32
    assert "server" not in response.headers


def test_docs_pages_are_served_without_the_json_csp(api: TestClient) -> None:
    response = api.get("/docs")
    assert response.status_code == 200
    assert "content-security-policy" not in response.headers
    assert api.get("/openapi.json").status_code == 200


def test_docs_can_be_disabled(api_store: ReferenceStore) -> None:
    with client(api_store, docs_enabled=False) as test_client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert_safe_error(test_client.get(path), 404, "not_found")


def test_request_id_is_echoed_only_when_safe(api: TestClient) -> None:
    good = "client-abc_123.XYZ"
    assert api.get("/health/live", headers={"x-request-id": good}).headers["x-request-id"] == good
    for bad in ("short", "has space in it", "a" * 65, "bad\\u000aline", "<script>x</script>"):
        echoed = api.get("/health/live", headers={"x-request-id": bad}).headers["x-request-id"]
        assert echoed != bad
        assert len(echoed) == 32


# --- Hosts and CORS ---------------------------------------------------------------------


def test_unknown_host_header_is_refused(api: TestClient) -> None:
    response = api.get("/api/v1/system", headers={"host": "evil.example"})
    assert_safe_error(response, 400, "invalid_host")


@pytest.mark.parametrize(
    ("host", "allowed", "expected"),
    [
        ("localhost:8000", ("localhost",), True),
        ("LOCALHOST", ("localhost",), True),
        ("api.example.org", ("*.example.org",), True),
        ("example.org", ("*.example.org",), False),
        ("evil-example.org", ("*.example.org",), False),
        ("localhost.evil.org", ("localhost",), False),
        ("", ("localhost",), False),
        ("[::1]:8000", ("localhost",), False),
    ],
)
def test_host_matching(host: str, allowed: tuple[str, ...], expected: bool) -> None:
    assert host_allowed(host, allowed) is expected


def test_cors_allows_only_configured_origins(api_store: ReferenceStore) -> None:
    origin = "http://localhost:5173"
    with client(api_store, cors_origins=(origin,)) as test_client:
        allowed = test_client.get("/api/v1/forecasts", headers={"origin": origin})
        assert allowed.headers["access-control-allow-origin"] == origin
        assert "access-control-allow-credentials" not in allowed.headers
        other = test_client.get("/api/v1/forecasts", headers={"origin": "https://evil.example"})
        assert "access-control-allow-origin" not in other.headers
        preflight = test_client.options(
            "/api/v1/simulations",
            headers={
                "origin": origin,
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type,x-api-key",
            },
        )
        assert preflight.status_code == 200
        assert "POST" in preflight.headers["access-control-allow-methods"]
        refused = test_client.options(
            "/api/v1/simulations",
            headers={"origin": origin, "access-control-request-method": "DELETE"},
        )
        assert refused.status_code == 400


def test_no_cors_by_default(api: TestClient) -> None:
    response = api.get("/api/v1/forecasts", headers={"origin": "http://localhost:5173"})
    assert "access-control-allow-origin" not in response.headers


# --- Bodies -----------------------------------------------------------------------------


def test_body_limit_by_content_length(api: TestClient) -> None:
    body = json.dumps({"parameters": {}, "padding": "x" * 20_000})
    response = api.post(
        "/api/v1/simulations", content=body, headers={"content-type": "application/json"}
    )
    assert_safe_error(response, 413, "payload_too_large")


def test_body_limit_for_streamed_bodies(api: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"padding": "'
        for _ in range(40):
            yield b"x" * 1024
        yield b'"}'

    response = api.post(
        "/api/v1/simulations", content=chunks(), headers={"content-type": "application/json"}
    )
    assert "content-length" not in response.request.headers
    assert_safe_error(response, 413, "payload_too_large")


def test_invalid_content_length_is_refused(api: TestClient) -> None:
    response = api.post(
        "/api/v1/simulations",
        content=b"{}",
        headers={"content-type": "application/json", "content-length": "abc"},
    )
    assert response.status_code in (400, 413)


def test_only_json_bodies(api: TestClient) -> None:
    for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
        response = api.post(
            "/api/v1/simulations", content=b"{}", headers={"content-type": content_type}
        )
        assert_safe_error(response, 415, "unsupported_media_type")


def test_duplicate_keys_are_refused(api: TestClient) -> None:
    response = api.post(
        "/api/v1/simulations",
        content=b'{"horizon_days": 5, "horizon_days": 30}',
        headers={"content-type": "application/json"},
    )
    body = assert_safe_error(response, 422, "validation_error")
    assert body["error"]["details"] == [{"field": "horizon_days", "message": "duplicate key"}]


@pytest.mark.parametrize(
    "content",
    [
        b"{",
        b"[" * 5_000 + b"]" * 5_000,
        b'{"a": ' * 3_000 + b"1" + b"}" * 3_000,
        b"\xff\xfe",
        b"",
    ],
)
def test_malformed_and_hostile_json_never_crashes(api: TestClient, content: bytes) -> None:
    response = api.post(
        "/api/v1/simulations", content=content, headers={"content-type": "application/json"}
    )
    assert response.status_code in (400, 413, 422), response.text
    assert response.json()["error"]["code"] in (
        "bad_request",
        "payload_too_large",
        "validation_error",
    )


# --- API key ----------------------------------------------------------------------------


def test_api_key_protects_only_the_simulation(
    api_store: ReferenceStore, api_simulator: SimulationService, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="atlas.api")
    with client(api_store, api_simulator, api_key=KEY) as test_client:
        assert test_client.get("/api/v1/scenarios").status_code == 200
        missing = test_client.post("/api/v1/simulations", json={"horizon_days": 3})
        assert_safe_error(missing, 401, "unauthorized")
        assert missing.headers["www-authenticate"] == "X-API-Key"
        wrong = test_client.post(
            "/api/v1/simulations", json={"horizon_days": 3}, headers={"x-api-key": KEY + "x"}
        )
        assert_safe_error(wrong, 401, "unauthorized")
        right = test_client.post(
            "/api/v1/simulations", json={"horizon_days": 3}, headers={"x-api-key": KEY}
        )
        assert right.status_code == 200
        system = test_client.get("/api/v1/system").json()
        assert system["data"]["limits"]["simulation_requires_api_key"] is True
        assert KEY not in json.dumps(system)
    assert KEY not in caplog.text


def test_weak_api_keys_are_refused() -> None:
    for key in ("short", "has a space " * 4, "x" * 23):
        with pytest.raises(ConfigError):
            settings(api_key=key)
    assert KEY not in repr(settings(api_key=KEY))


# --- Safe failures ----------------------------------------------------------------------


class _Exploding(ReferenceStore):
    def require_inputs(self) -> Any:
        raise RuntimeError("secret detail /home/user/.ssh/id_rsa")


def test_unexpected_exceptions_are_generic(
    api_store: ReferenceStore,
    api_simulator: SimulationService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    broken = _Exploding(api_store.settings)
    broken.__dict__.update(api_store.__dict__)
    caplog.set_level(logging.ERROR, logger="atlas.api")
    with client(broken, api_simulator, raise_server_exceptions=False) as test_client:
        response = test_client.get("/api/v1/forecasts")
    body = assert_safe_error(response, 500, "internal_error")
    assert "secret" not in response.text
    # The server log keeps the traceback, under the same request id.
    record = next(r for r in caplog.records if getattr(r, "event", None) == "unhandled_exception")
    assert record.exc_info is not None
    assert getattr(record, "request_id", None) == body["error"]["request_id"]


def test_uninitialised_app_answers_503(api_store: ReferenceStore) -> None:
    app = create_app(settings())
    test_client = TestClient(app)  # no lifespan: nothing loaded, no workers
    assert_safe_error(test_client.get("/api/v1/forecasts"), 503, "artifact_unavailable")
    assert_safe_error(test_client.post("/api/v1/simulations", json={}), 503, "artifact_unavailable")


def test_api_error_carries_headers() -> None:
    error = ApiError(503, "simulations_busy", "busy", headers={"Retry-After": "1"})
    assert error.headers == {"Retry-After": "1"}


# --- Binding ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "loopback"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("[::1]", True),
        ("localhost", True),
        ("127.0.0.2", True),
        ("0.0.0.0", False),  # noqa: S104 - the address under test
        ("10.0.0.5", False),
        ("example.org", False),
    ],
)
def test_loopback_detection(host: str, loopback: bool) -> None:
    assert is_loopback(host) is loopback


def test_public_bind_requires_a_key() -> None:
    check_bind("127.0.0.1", 8000, settings())
    with pytest.raises(ConfigError, match="refusing to bind"):
        check_bind("0.0.0.0", 8000, settings())  # noqa: S104
    check_bind("0.0.0.0", 8000, settings(api_key=KEY))  # noqa: S104
    with pytest.raises(ConfigError, match="port"):
        check_bind("127.0.0.1", 0, settings())
