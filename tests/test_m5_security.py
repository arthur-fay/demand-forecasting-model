"""Security controls of the M5 API: hosts, CORS, limits, keys, headers and safe errors."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from atlas.api.app import create_app
from atlas.api.dependencies import repeated_keys
from atlas.api.middleware import MAX_IN_FLIGHT, RequestContextMiddleware, host_allowed
from atlas.api.problems import ApiError
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
            response = test_client.get(path)
            assert_safe_error(response, 404, "not_found")
            assert "content-security-policy" in response.headers  # no docs: no exemption


def test_only_the_exact_docs_pages_skip_the_csp(api: TestClient) -> None:
    for path in ("/docsX", "/docs-anything", "/redocfoo", "/docs/oauth2"):
        response = api.get(path)
        assert response.status_code == 404
        assert "content-security-policy" in response.headers, path


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
        ("[::1]:8000", ("::1",), True),
        ("[::1]", ("::1",), True),
        ("[::1]x", ("::1",), False),
        ("::1", ("::1",), False),  # an IPv6 Host must be bracketed
        ("localhost:abc", ("localhost",), False),
        ("localhost:", ("localhost",), False),
        ("localhost:123456", ("localhost",), False),
        ("localhost:\u00b2", ("localhost",), False),  # a non-ASCII digit
        ("127.0.0.1:1@evil.example", ("127.0.0.1",), False),
    ],
)
def test_host_matching(host: str, allowed: tuple[str, ...], expected: bool) -> None:
    assert host_allowed(host, allowed) is expected


def test_cors_allows_only_configured_origins(api_store: ReferenceStore) -> None:
    origin = "http://localhost:5173"
    with client(api_store, cors_origins=(origin,)) as test_client:
        allowed = test_client.get("/api/v1/forecasts", headers={"origin": origin})
        assert allowed.headers["access-control-allow-origin"] == origin
        exposed = {h.strip() for h in allowed.headers["access-control-expose-headers"].split(",")}
        assert exposed == {"X-Request-ID", "Server-Timing", "Retry-After"}
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
        body = assert_safe_error(refused, 400, "cors_rejected")
        assert "method" in body["error"]["message"]
        evil = test_client.options(
            "/api/v1/simulations",
            headers={"origin": "https://evil.example", "access-control-request-method": "POST"},
        )
        assert "origin" in assert_safe_error(evil, 400, "cors_rejected")["error"]["message"]
        assert "access-control-allow-origin" not in evil.headers
        # Errors made before the CORS layer (body limit, Host check) stay readable by an
        # allowed origin, and only by it.
        for headers, status in (
            ({"content-length": "999999", "content-type": "application/json"}, 413),
            ({"host": "evil.example"}, 400),
        ):
            for sender, readable in ((origin, True), ("https://evil.example", False)):
                response = test_client.post(
                    "/api/v1/simulations", headers={**headers, "origin": sender}, content=b"{}"
                )
                assert response.status_code == status
                assert ("access-control-allow-origin" in response.headers) is readable
                if readable:
                    assert response.headers["access-control-allow-origin"] == origin
                    assert "X-Request-ID" in response.headers["access-control-expose-headers"]


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


def test_many_keys_are_checked_in_linear_time(api: TestClient) -> None:
    # About the largest object a 16 KiB body holds: 1,700 distinct keys.
    keys = {f"k{i}": 0 for i in range(1_700)}
    content = json.dumps({"parameters": keys}, separators=(",", ":")).encode("utf-8")
    assert len(content) <= 16_384
    started = time.perf_counter()
    response = api.post(
        "/api/v1/simulations", content=content, headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    assert time.perf_counter() - started < 1.0
    # At the largest allowed body limit (1 MiB): about 100,000 keys, still linear.
    big = json.dumps({f"k{i}": i for i in range(100_000)}).encode("utf-8")
    started = time.perf_counter()
    assert repeated_keys(big) == []
    assert repeated_keys(big[:-1] + b', "k7": 1}') == ["k7"]
    assert time.perf_counter() - started < 2.0


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


# --- Query strictness and request limits ---------------------------------------------


def test_history_dates_far_outside_the_snapshot_are_422(api: TestClient) -> None:
    for query, field in (
        ("end=0001-01-01", "end"),
        ("end=0001-06-29", "end"),
        ("start=9999-12-31", "start"),
        ("start=0001-01-01&end=9999-12-31", "start"),
    ):
        response = api.get(f"/api/v1/forecasts/history?{query}")
        body = assert_safe_error(response, 422, "validation_error")
        assert body["error"]["details"][0]["field"] == field, query


def test_query_integers_are_plain_digits(api: TestClient) -> None:
    for bad in ("1_0", "+5", "5.0", " 5", "05e0", "\\u0665"):
        response = api.get("/api/v1/forecasts", params={"horizon_days": bad})
        assert_safe_error(response, 422, "validation_error")
    assert len(api.get("/api/v1/forecasts?horizon_days=05").json()["data"]["points"]) == 5


def test_a_scalar_query_parameter_may_not_repeat(api: TestClient) -> None:
    response = api.get("/api/v1/forecasts?horizon_days=1&horizon_days=30")
    body = assert_safe_error(response, 422, "validation_error")
    assert body["error"]["details"] == [
        {"field": "horizon_days", "message": "given more than once"}
    ]
    # List parameters repeat by design.
    listed = api.get("/api/v1/scenarios/comparison?scenario_id=normal&scenario_id=reduced_budget")
    assert listed.status_code == 200


def test_health_answers_head(api: TestClient) -> None:
    for path in ("/health/live", "/health/ready"):
        response = api.head(path)
        assert response.status_code == 200
        assert response.content == b""
        assert response.headers["x-request-id"]


def _context_middleware(api: TestClient) -> RequestContextMiddleware:
    layer: Any = api.app
    api.get("/health/live")  # builds the middleware stack
    layer = layer.middleware_stack
    while not isinstance(layer, RequestContextMiddleware):
        layer = layer.app
    return layer


def test_requests_in_flight_are_capped_with_a_json_503(
    api_store: ReferenceStore, caplog: pytest.LogCaptureFixture
) -> None:
    with client(api_store) as test_client, caplog.at_level(logging.WARNING, "atlas.api"):
        middleware = _context_middleware(test_client)
        middleware.in_flight = MAX_IN_FLIGHT  # as if 64 requests were being served
        try:
            response = test_client.get("/api/v1/system")
            logged = [r for r in caplog.records if getattr(r, "event", None) == "api_error"]
            assert [getattr(r, "code", None) for r in logged] == ["server_busy"]
            body = assert_safe_error(response, 503, "server_busy")
            assert response.headers["retry-after"] == "1"
            assert "64" in body["error"]["message"]
            # Health probes are never turned away.
            assert test_client.get("/health/live").status_code == 200
        finally:
            middleware.in_flight = 0
        assert test_client.get("/api/v1/system").status_code == 200
        assert middleware.in_flight == 0  # every request released its slot


@pytest.mark.parametrize("host", ["*.com", ".", "-", "a-", "[::1]", "*", "bad host", "*.-x.org"])
def test_allowed_hosts_must_be_specific(host: str) -> None:
    with pytest.raises(ConfigError):
        settings(allowed_hosts=(host,))


def test_ipv6_loopback_is_allowed_by_default(api_store: ReferenceStore) -> None:
    with client(api_store, allowed_hosts=("localhost", "127.0.0.1", "::1")) as test_client:
        assert test_client.get("/health/live", headers={"host": "[::1]:8000"}).status_code == 200
        assert_safe_error(
            test_client.get("/health/live", headers={"host": "[::2]:8000"}), 400, "invalid_host"
        )
    settings(allowed_hosts=("fe80::1", "atlas.example.org"))


def test_every_operation_refuses_unknown_query_parameters(api: TestClient) -> None:
    document = api.get("/openapi.json").json()
    for path, item in document["paths"].items():
        url = path.replace("{scenario_id}", "normal") + "?debug=1"
        for method in item:
            kwargs: dict[str, Any] = {"json": {"horizon_days": 2}} if method == "post" else {}
            response = api.request(method.upper(), url, **kwargs)
            body = assert_safe_error(response, 422, "validation_error")
            assert body["error"]["details"] == [{"field": "debug", "message": "not accepted here"}]


def test_access_log_never_records_the_query(
    api: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, "atlas.api"):
        api.get("/api/v1/forecasts?horizon_days=2")
    paths = [r.path for r in caplog.records if getattr(r, "event", None) == "request"]  # type: ignore[attr-defined]
    assert paths == ["/api/v1/forecasts"]


def test_validation_details_are_capped(api: TestClient) -> None:
    unknown = {f"unknown_{i}": 1 for i in range(30)}
    body = assert_safe_error(
        api.post("/api/v1/simulations", json={"parameters": unknown}), 422, "validation_error"
    )
    assert len(body["error"]["details"]) == 20


def test_small_validation_rules(api: TestClient) -> None:
    assert not is_loopback("localhost.example.org")
    with pytest.raises(ConfigError):
        settings(max_concurrent_simulations=True)
    error = api.post(
        "/api/v1/simulations", json={"demand": {"basis": "m3_marginal_quantile", "quantile": 0.5}}
    ).json()["error"]
    assert error["code"] == "validation_error"  # refused by the schema, before M4
    assert error["details"][0]["field"] == "demand.quantile"
