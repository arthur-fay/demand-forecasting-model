"""The reverse proxy, tested with a real nginx running the rendered production site.

An echo upstream shows exactly what nginx forwards (headers, method, path); the real API
behind the same site shows the integration. Both listen on a Unix socket, as the API does
in production (nginx reaches it through ``/run/atlas/api.sock``). Covered: authentication
on every route, the route and method allow-lists, the simulation key added only by the
proxy, headers that are never forwarded, ambiguous and unparsable paths, body limits,
per-client rate limits (unknown paths included), JSON errors, TLS and host handling, the
public-demo separation, the network allow-list and the access log.
"""

from __future__ import annotations

import base64
import http.client
import http.server
import json
import shutil
import socket
import socketserver
import ssl
import tempfile
import threading
import time
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import pytest

from atlas.deploy.layout import Layout
from atlas.deploy.render import Renderer
from atlas.deploy.routes import load_routes
from atlas.deploy.siteconfig import load_config
from tests.conftest import REPO_ROOT
from tests.m6_helpers import Nginx, free_port, make_tls, need_tools, run, write

if TYPE_CHECKING:
    from atlas.api.reference import ReferenceStore
    from atlas.api.simulation import SimulationService

DOMAIN = "atlas.example.org"
KEY = "f" * 64
USER, PASSWORD = "alice", "correct-horse-battery-staple-1234"
AUTH = {"Authorization": "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()}
SENSITIVE = ("authorization", "cookie", "x-forwarded-for", "x-real-ip", "forwarded")


class Echo(http.server.BaseHTTPRequestHandler):
    """An upstream that answers with what it received."""

    def _answer(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps(
            {
                "method": self.command,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # Headers the API sets too: the proxy must hide these copies, never double them.
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Cache-Control", "public")
        self.send_header("X-Request-ID", self.headers.get("X-Request-ID", ""))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = _answer

    def log_message(self, format: str, *args: Any) -> None:
        return


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


@contextmanager
def socket_directory() -> Iterator[Path]:
    """A short directory for a Unix socket (their paths are limited to 108 bytes)."""
    directory = Path(tempfile.mkdtemp(prefix="atlas-run-"))
    directory.chmod(0o755)
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@contextmanager
def echo_upstream() -> Iterator[Path]:
    """The echo upstream on ``<directory>/api.sock``; yields the directory."""
    with socket_directory() as directory:
        server = UnixHTTPServer(str(directory / "api.sock"), Echo)
        (directory / "api.sock").chmod(0o666)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield directory
        finally:
            server.shutdown()
            server.server_close()


@contextmanager
def live_api(
    store: ReferenceStore, simulator: SimulationService, **changes: Any
) -> Iterator[tuple[Path, int]]:
    """The real API as production runs it: loopback TCP and ``<directory>/api.sock``."""
    import uvicorn

    from atlas.api.app import create_app
    from atlas.api.server import bind_unix, server_config
    from tests.m5_helpers import settings

    with socket_directory() as directory:
        port = free_port()
        app = create_app(
            settings(store.settings.repo_root, **changes), store=store, simulator=simulator
        )
        config = server_config(app, "127.0.0.1", port)
        sockets = [config.bind_socket(), bind_unix(str(directory / "api.sock"))]
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, kwargs={"sockets": sockets}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 30
        while not server.started:
            if time.monotonic() > deadline or not thread.is_alive():
                raise RuntimeError("the test server did not start")
            time.sleep(0.02)
        try:
            yield directory, port
        finally:
            server.should_exit = True
            thread.join(timeout=30)


@dataclass
class Reply:
    status: int
    headers: list[tuple[str, str]]
    body: bytes

    def header(self, name: str) -> list[str]:
        return [value for key, value in self.headers if key.lower() == name.lower()]

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass
class Site:
    nginx: Nginx
    ca: Path
    directory: Path

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        host: str = DOMAIN,
    ) -> Reply:
        context = ssl.create_default_context(cafile=str(self.ca))
        raw = socket.create_connection(("127.0.0.1", self.nginx.https_port), timeout=20)
        connection = http.client.HTTPConnection(host, self.nginx.https_port, timeout=20)
        connection.sock = context.wrap_socket(raw, server_hostname=host)
        given = {name.lower() for name in headers or {}}
        connection.putrequest(method, path, skip_host="host" in given, skip_accept_encoding=True)
        sent = {"Content-Length": str(len(body))} if body is not None else {}
        for name, value in {**sent, **(headers or {})}.items():
            connection.putheader(name, value)
        connection.endheaders(body)
        answer = connection.getresponse()
        reply = Reply(answer.status, answer.getheaders(), answer.read())
        connection.close()
        return reply

    def burst_replies(
        self, method: str, path: str, count: int, body: bytes | None = None
    ) -> list[Reply]:
        """``count`` requests on one keep-alive connection, as fast as possible."""
        context = ssl.create_default_context(cafile=str(self.ca))
        raw = socket.create_connection(("127.0.0.1", self.nginx.https_port), timeout=20)
        connection = http.client.HTTPConnection(DOMAIN, self.nginx.https_port, timeout=20)
        connection.sock = context.wrap_socket(raw, server_hostname=DOMAIN)
        replies = []
        for _ in range(count):
            connection.request(method, path, body=body, headers=AUTH)
            answer = connection.getresponse()
            replies.append(Reply(answer.status, answer.getheaders(), answer.read()))
        connection.close()
        return replies

    def raw(self, request: bytes) -> tuple[str, dict[str, str], bytes]:
        """Send ``request`` as it is (nothing checked by an HTTP client): status line,
        headers (lowercase names) and body of the answer."""
        context = ssl.create_default_context(cafile=str(self.ca))
        with (
            socket.create_connection(("127.0.0.1", self.nginx.https_port), timeout=20) as raw,
            context.wrap_socket(raw, server_hostname=DOMAIN) as tls,
        ):
            tls.sendall(request)
            data = b""
            while chunk := tls.recv(65536):
                data += chunk
        head, _, body = data.partition(b"\r\n\r\n")
        status, *lines = head.decode("latin-1").split("\r\n")
        headers = {k.lower(): v.strip() for k, _, v in (line.partition(":") for line in lines)}
        return status, headers, body

    def burst(self, method: str, path: str, count: int, body: bytes | None = None) -> list[int]:
        return [reply.status for reply in self.burst_replies(method, path, count, body)]

    def http(self, path: str, host: str = DOMAIN, method: str = "GET") -> Reply | None:
        connection = http.client.HTTPConnection("127.0.0.1", self.nginx.http_port, timeout=10)
        try:
            connection.request(method, path, headers={"Host": host})
            answer = connection.getresponse()
            return Reply(answer.status, answer.getheaders(), answer.read())
        except (http.client.RemoteDisconnected, ConnectionResetError):
            return None
        finally:
            connection.close()

    def log(self) -> list[dict[str, Any]]:
        text = (self.directory / "nginx/logs/atlas-access.log").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines()]


def _hash(password: str) -> str:
    result = run(["openssl", "passwd", "-6", "-stdin"], input=password + "\n")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@contextmanager
def site(
    directory: Path, upstream: Path, extra: str = "", profile: str = "private"
) -> Iterator[Site]:
    """The rendered site in a real nginx, its upstream the socket ``upstream/api.sock``."""
    need_tools("nginx", "openssl")
    ca, cert, key = make_tls(directory / "tls")
    text = (
        f"ATLAS_DEPLOY_PROFILE={profile}\nATLAS_DEPLOY_DOMAIN={DOMAIN}\nATLAS_DEPLOY_IPV6=false\n"
        f"ATLAS_DEPLOY_TLS_CERT={cert}\nATLAS_DEPLOY_TLS_KEY={key}\n" + extra
    )
    if profile == "public-demo":
        text += "ATLAS_DEPLOY_SECURITY_REVIEW=test-review-1\n"
    layout = Layout(
        nginx=PurePosixPath(str(directory / "nginx-atlas")),
        acme=PurePosixPath(str(directory / "acme")),
        run=PurePosixPath(str(upstream)),
    )
    write(
        directory / "nginx-atlas/simulation-key.conf",
        f'proxy_set_header X-API-Key "{KEY}";\n',
        0o600,
    )
    write(directory / "nginx-atlas/htpasswd", f"{USER}:{_hash(PASSWORD)}\n", 0o640)
    write(directory / "acme/.well-known/acme-challenge/token123", "token123.thumbprint")
    config = load_config(text, layout)
    rendered = Renderer.for_release(config, layout, REPO_ROOT).nginx()
    with Nginx(directory / "nginx", rendered) as nginx:
        yield Site(nginx, ca, directory)


@pytest.fixture(scope="module")
def echo() -> Iterator[Path]:
    with echo_upstream() as directory:
        yield directory


@pytest.fixture
def private(tmp_path: Path, echo: Path) -> Iterator[Site]:
    with site(tmp_path, echo) as running:
        yield running


def example_path(path: str) -> str:
    return path.replace("{scenario_id}", "normal")


ROUTES = [(r.method, example_path(r.path)) for r in load_routes(REPO_ROOT)]


# --- Authentication and the allow-lists -------------------------------------------------------


def test_every_route_requires_authentication(private: Site) -> None:
    for method, path in ROUTES:
        reply = private.request(method, path, body=b"{}" if method == "POST" else None)
        assert reply.status == 401, (method, path)
        assert reply.json()["error"]["code"] == "unauthorized"
        assert len(reply.header("WWW-Authenticate")) == 1
    wrong = private.request("GET", "/health/live", headers={"Authorization": "Basic YWxpY2U6eA=="})
    assert wrong.status == 401


def test_authenticated_routes_reach_the_api_with_their_methods_only(private: Site) -> None:
    for method, path in ROUTES:
        reply = private.request(
            method, path, headers=AUTH, body=b"{}" if method == "POST" else None
        )
        assert reply.status == 200, (method, path, reply.body)
        assert reply.json()["path"] == path
        assert reply.json()["method"] == method
        other = "GET" if method == "POST" else "POST"
        refused = private.request(
            other, path, headers=AUTH, body=b"{}" if other == "POST" else None
        )
        assert refused.status == 405, (other, path)
        assert refused.json()["error"]["code"] == "method_not_allowed"
    assert private.request("HEAD", "/health/ready", headers=AUTH).status == 200
    assert private.request("DELETE", "/api/v1/system", headers=AUTH).status == 405


@pytest.mark.parametrize(
    "path",
    [
        "/docs",
        "/redoc",
        "/openapi.json",
        "/admin",
        "/api/v2/forecasts",
        "/api/v1/forecasts/",
        "/api/v1/scenarios/Bad-Id",
        "/health",
        "/.env",
    ],
)
def test_everything_else_is_not_found_at_the_proxy(private: Site, path: str) -> None:
    reply = private.request("GET", path, headers=AUTH)
    assert reply.status in (400, 404), path
    assert reply.json()["error"]["code"] in ("not_found", "bad_request")


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/forecasts%2F..%2Fsimulations",
        "/api/v1//system",
        "/api/v1/./system",
        "/api/v1/%2e%2e/health/live",
        "/api/v1/system%5c",
        "/api/v1/forecasts/../system",
        "/api/v1/scenarios/normal%0A",  # PCRE's $ matches before a final line break
        "/api/v1/scenarios/normal%0d",
        "/api/v1/system#/x",  # nginx would route the part before #, forward all of it
        "/.atlas-error-400",
    ],
)
def test_ambiguous_request_targets_are_refused(private: Site, path: str) -> None:
    reply = private.request("GET", path, headers=AUTH)
    assert reply.status == 400, path
    assert reply.json()["error"]["code"] == "bad_request"
    assert reply.header("X-Frame-Options") == ["DENY"]


@pytest.mark.parametrize(
    "line",
    [
        b"GET /api/v1/system%00 HTTP/1.1",
        b"get /api/v1/system HTTP/1.1",
        b"OPTIONS * HTTP/1.1",
    ],
)
def test_unparsable_requests_get_the_json_400(private: Site, line: bytes) -> None:
    """nginx refuses these while parsing the request line: no named location can answer
    them (they have no URI), the internal error location does."""
    status, headers, body = private.raw(
        line + f"\r\nHost: {DOMAIN}\r\nConnection: close\r\n\r\n".encode()
    )
    assert status.split()[1] == "400", status
    assert headers["content-type"] == "application/json"
    assert json.loads(body)["error"]["code"] == "bad_request"


def test_unknown_paths_are_answered_after_authentication_and_limits(private: Site) -> None:
    assert private.request("GET", "/admin").status == 401  # not even a 404 without account
    assert private.request("GET", "/admin", headers=AUTH).status == 404
    statuses = private.burst("GET", "/admin", 90)
    assert statuses.count(429) >= 30  # probing unknown paths is rate limited too


# --- What reaches the API -----------------------------------------------------------------------


def test_the_proxy_adds_the_key_to_simulations_only(private: Site) -> None:
    bogus = {**AUTH, "X-API-Key": "client-supplied-key-0000000000"}
    simulation = private.request("POST", "/api/v1/simulations", headers=bogus, body=b"{}").json()
    assert simulation["headers"]["x-api-key"] == KEY  # the client's value is replaced
    read = private.request("GET", "/api/v1/system", headers=bogus).json()
    assert "x-api-key" not in read["headers"]


def test_credentials_and_forwarding_headers_are_never_forwarded(private: Site) -> None:
    sent = {
        **AUTH,
        "Cookie": "session=1",
        "X-Forwarded-For": "198.51.100.7",
        "X-Forwarded-Host": "evil.example",
        "X-Forwarded-Proto": "http",
        "X-Forwarded-Port": "8080",
        "X-Forwarded-Prefix": "/evil",
        "X-Original-URL": "/api/v1/simulations",
        "Proxy-Authorization": "Basic eDp4",
        "X-Real-IP": "198.51.100.7",
        "Forwarded": "for=198.51.100.7",
        "X-Request-ID": "client-chosen-id-123",
        "User-Agent": "probe/1.0",
        "Accept": "application/json",
    }
    reply = private.request("GET", "/api/v1/models", headers=sent)
    received = reply.json()["headers"]
    for name in SENSITIVE:
        assert name not in received, name
    # An allow-list: nothing else the client sent is passed on.
    assert set(received) == {"host", "x-request-id", "connection", "accept"}, received
    assert received["accept"] == "application/json"
    assert received["host"] == DOMAIN
    assert received["x-request-id"] != "client-chosen-id-123"
    assert reply.header("X-Request-ID") == [received["x-request-id"]]
    assert len(received["x-request-id"]) == 32


def test_security_headers_are_set_once(private: Site) -> None:
    for reply in (
        private.request("GET", "/api/v1/system", headers=AUTH),
        private.request("GET", "/api/v1/system"),
        private.request("GET", "/nothing", headers=AUTH),
    ):
        assert reply.header("X-Frame-Options") == ["DENY"]
        assert reply.header("Cache-Control") == ["no-store"]
        assert reply.header("Strict-Transport-Security") == ["max-age=31536000"]
        assert reply.header("X-Content-Type-Options") == ["nosniff"]
        assert reply.header("Content-Security-Policy") == [
            "default-src 'none'; frame-ancestors 'none'"
        ]
        assert reply.header("Cross-Origin-Resource-Policy") == ["same-origin"]
        assert all("nginx/" not in value for value in reply.header("Server"))


def test_oversized_bodies_are_refused_before_the_api(private: Site) -> None:
    reply = private.request("POST", "/api/v1/simulations", headers=AUTH, body=b"x" * 17_000)
    assert reply.status == 413
    assert reply.json()["error"]["code"] == "payload_too_large"


def test_the_access_log_is_structured_and_holds_no_secret(private: Site) -> None:
    private.request(
        "GET", "/api/v1/forecasts?horizon_days=3", headers={**AUTH, "X-API-Key": "k" * 30}
    )
    entries = private.log()
    assert entries
    last = entries[-1]
    assert last["path"] == "/api/v1/forecasts"  # no query string
    assert last["user"] == USER
    assert last["status"] == 200
    assert len(last["request_id"]) == 32
    text = (private.directory / "nginx/logs/atlas-access.log").read_text()
    for secret in (PASSWORD, AUTH["Authorization"].split()[1], KEY, "k" * 30, "horizon_days"):
        assert secret not in text


# --- Limits


def test_per_client_rate_limits_answer_429(private: Site) -> None:
    replies = private.burst_replies("GET", "/health/live", 90)
    statuses = [reply.status for reply in replies]
    assert statuses[:40] == [200] * 40  # the burst
    assert statuses.count(429) >= 30
    # A refused reply from inside the burst (a new request later could find the bucket
    # refilled: 10 per second).
    limited = next(reply for reply in reversed(replies) if reply.status == 429)
    assert limited.json()["error"]["code"] == "rate_limited"
    assert limited.header("Retry-After") == ["1"]
    assert limited.header("X-Frame-Options") == ["DENY"]


def test_simulations_have_their_own_tighter_limit(tmp_path: Path, echo: Path) -> None:
    with site(tmp_path, echo) as running:
        statuses = running.burst("POST", "/api/v1/simulations", 12, body=b"{}")
    assert statuses[:7] == [200] * 7  # one per 2 s, burst 6
    assert statuses.count(429) >= 4


def test_an_unavailable_api_is_a_json_503(tmp_path: Path) -> None:
    with socket_directory() as nothing, site(tmp_path, nothing) as running:  # no socket
        reply = running.request("GET", "/api/v1/system", headers=AUTH)
    assert reply.status == 503
    assert reply.json()["error"]["code"] == "upstream_unavailable"
    assert reply.header("Retry-After") == ["5"]


# --- TLS and host names


def test_unknown_names_get_no_certificate_and_no_answer(private: Site) -> None:
    with pytest.raises(ssl.SSLError):
        private.request("GET", "/health/live", host="other.example.org")
    assert private.http("/", host="other.example.org") is None  # 444: connection closed
    # A handshake for this site, then a request for another one: no answer, no version.
    with pytest.raises((http.client.RemoteDisconnected, ConnectionResetError)):
        private.request("GET", "/api/v1/models", headers={**AUTH, "Host": "evil.example"})


def test_http_redirects_to_https_and_serves_acme_only(private: Site) -> None:
    moved = private.http("/api/v1/system?x=1")
    assert moved is not None
    assert moved.status == 301
    assert moved.header("Location") == [f"https://{DOMAIN}/api/v1/system?x=1"]
    token = private.http("/.well-known/acme-challenge/token123")
    assert token is not None
    assert token.status == 200
    assert token.body == b"token123.thumbprint"
    posted = private.http("/.well-known/acme-challenge/token123", method="POST")
    assert posted is not None
    assert posted.status == 405


def test_tls_is_recent(private: Site) -> None:
    context = ssl.create_default_context(cafile=str(private.ca))
    with (
        socket.create_connection(("127.0.0.1", private.nginx.https_port), timeout=10) as raw,
        context.wrap_socket(raw, server_hostname=DOMAIN) as tls,
    ):
        assert tls.version() in ("TLSv1.2", "TLSv1.3")

    def client(maximum: ssl.TLSVersion) -> ssl.SSLContext:
        # Security level 0 lets the client offer old protocols: only the server can refuse.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=str(private.ca))
        context.set_ciphers("DEFAULT:@SECLEVEL=0")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            context.minimum_version = ssl.TLSVersion.TLSv1
            context.maximum_version = maximum
        return context

    with socket.create_connection(("127.0.0.1", private.nginx.https_port), timeout=10) as raw:
        client(ssl.TLSVersion.TLSv1_2).wrap_socket(raw, server_hostname=DOMAIN).close()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        old = client(ssl.TLSVersion.TLSv1_1)
    with (
        socket.create_connection(("127.0.0.1", private.nginx.https_port), timeout=10) as raw,
        pytest.raises(ssl.SSLError),
    ):
        old.wrap_socket(raw, server_hostname=DOMAIN)


# --- Other profiles -------------------------------------------------------------------------------


def test_the_network_allow_list(tmp_path: Path, echo: Path) -> None:
    with site(tmp_path, echo, "ATLAS_DEPLOY_ALLOW_CIDRS=203.0.113.0/24\n") as running:
        reply = running.request("GET", "/api/v1/system", headers=AUTH)
    assert reply.status == 403
    assert reply.json()["error"]["code"] == "forbidden"


def test_the_public_demo_exposes_precomputed_reads_only(tmp_path: Path, echo: Path) -> None:
    with site(tmp_path, echo, profile="public-demo") as running:
        assert running.request("GET", "/api/v1/forecasts").status == 200
        # Operational detail (workers, limits, component details) is not public.
        assert running.request("GET", "/api/v1/system").status == 404
        assert running.request("GET", "/health/live").status == 200
        assert running.request("GET", "/health/ready").status == 404
        assert running.request("POST", "/api/v1/simulations", body=b"{}").status == 404
        assert running.request("POST", "/api/v1/forecasts", body=b"{}").status == 405
        assert running.request("GET", "/docs").status == 404
        assert "authorization" not in running.request("GET", "/api/v1/models").json()["headers"]


# --- The real API behind the proxy


def test_the_api_behind_the_proxy(
    tmp_path: Path, api_store: ReferenceStore, api_simulator: SimulationService
) -> None:
    with (
        live_api(
            api_store,
            api_simulator,
            api_key=KEY,
            allowed_hosts=(DOMAIN, "127.0.0.1", "localhost"),
            docs_enabled=False,
        ) as (run_dir, port),
        site(tmp_path, run_dir) as running,
    ):
        system = running.request("GET", "/api/v1/system", headers=AUTH)
        assert system.status == 200
        body = system.json()
        assert body["integrity"]["validated_recommendation"] is False
        assert body["integrity"]["uncertainty_interval_status"] == "failed_calibration"
        assert body["data"]["limits"]["simulation_requires_api_key"] is True
        simulation = running.request(
            "POST",
            "/api/v1/simulations",
            headers={**AUTH, "Content-Type": "application/json"},
            body=b'{"horizon_days": 3}',
        )
        assert simulation.status == 200, simulation.body
        assert "HYPOTHETICAL" in simulation.json()["data"]["label"]
        assert running.log()[-1]["request_id"] == simulation.header("X-Request-ID")[0]
        # Without the proxy, the key is still required.
        direct = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        direct.request(
            "POST", "/api/v1/simulations", body=b"{}", headers={"Content-Type": "application/json"}
        )
        assert direct.getresponse().status == 401
        direct.close()
