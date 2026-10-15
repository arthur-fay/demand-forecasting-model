"""The dashboard through a real nginx running the rendered production site (M7).

The release's dashboard is published as an activation does it, the site is rendered for
that release and served by nginx with TLS, and the real API (with its workers) answers
behind the Unix socket. Covered: authentication in the private profile, the exact files
and pages with their bytes, types, cache policies and the dashboard's own
Content-Security-Policy (once, never the API's), compression, methods, every way out of
the published directory (unlisted files, links, traversal), the API next to it with its
own policy, a simulation from the browser's point of view, and the public demo.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from atlas.api.middleware import API_CSP
from atlas.dashboard import DASHBOARD_CSP, DESCRIPTOR_PATH, DIST, MANIFEST_NAME, load_build
from tests.m7_helpers import BUILD
from tests.m7_stack import DOMAIN, PASSWORD, USER, Stack, stack
from tests.test_m6_proxy import Reply, Site

if TYPE_CHECKING:
    from atlas.api.reference import ReferenceStore
    from atlas.api.simulation import SimulationService

import base64

AUTH = {"Authorization": "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()}
BUILT = load_build(BUILD)


@pytest.fixture(scope="module")
def private(
    tmp_path_factory: pytest.TempPathFactory,
    api_store: ReferenceStore,
    api_simulator: SimulationService,
) -> Iterator[tuple[Site, Stack]]:
    with stack(tmp_path_factory.mktemp("private"), api_store, api_simulator) as running:
        yield Site(running.nginx, running.ca, running.directory), running


@pytest.fixture(scope="module")
def public(
    tmp_path_factory: pytest.TempPathFactory,
    api_store: ReferenceStore,
    api_simulator: SimulationService,
) -> Iterator[Site]:
    with stack(
        tmp_path_factory.mktemp("public"), api_store, api_simulator, profile="public-demo"
    ) as running:
        yield Site(running.nginx, running.ca, running.directory)


def once(reply: Reply, name: str) -> str:
    values = reply.header(name)
    assert len(values) == 1, (name, values)
    return values[0]


# --- The private profile -----------------------------------------------------------------------


def test_the_dashboard_needs_an_account(private: tuple[Site, Stack]) -> None:
    site, _ = private
    for path in ("/", "/simulator", DESCRIPTOR_PATH, BUILT.files[0].url):
        reply = site.request("GET", path)
        assert reply.status == 401, path
        assert reply.json()["error"]["code"] == "unauthorized"


def test_every_page_is_the_index_with_the_dashboard_policy(private: tuple[Site, Stack]) -> None:
    site, _ = private
    index = (BUILD / "index.html").read_bytes()
    for route in BUILT.routes:
        reply = site.request("GET", route, headers=AUTH)
        assert reply.status == 200, route
        assert reply.body == index
        assert once(reply, "Content-Type") == "text/html; charset=utf-8"
        assert once(reply, "Content-Security-Policy") == DASHBOARD_CSP
        assert once(reply, "Cache-Control") == "no-cache"
        assert once(reply, "X-Frame-Options") == "DENY"
        assert once(reply, "X-Content-Type-Options") == "nosniff"
        assert once(reply, "Cross-Origin-Opener-Policy") == "same-origin"
        assert once(reply, "Strict-Transport-Security").startswith("max-age=")


def test_every_file_is_served_as_built(private: tuple[Site, Stack]) -> None:
    site, _ = private
    for item in BUILT.files:
        if item.path == "index.html":
            continue
        reply = site.request("GET", item.url, headers=AUTH)
        assert reply.status == 200, item.path
        assert hashlib.sha256(reply.body).hexdigest() == item.sha256, item.path
        assert once(reply, "Content-Type") == item.content_type, item.path
        assert once(reply, "Cache-Control") == item.cache_control, item.path
        assert once(reply, "Content-Security-Policy") == DASHBOARD_CSP


def test_text_is_compressed_for_clients_that_ask(private: tuple[Site, Stack]) -> None:
    site, _ = private
    script = max((f for f in BUILT.files if f.path.endswith(".js")), key=lambda f: f.size)
    reply = site.request("GET", script.url, headers={**AUTH, "Accept-Encoding": "gzip"})
    assert once(reply, "Content-Encoding") == "gzip"
    assert hashlib.sha256(gzip.decompress(reply.body)).hexdigest() == script.sha256
    assert len(reply.body) < script.size / 2
    plain = site.request("GET", script.url, headers=AUTH)
    assert plain.header("Content-Encoding") == []


def test_the_descriptor_lets_the_browser_simulate(private: tuple[Site, Stack]) -> None:
    site, _ = private
    reply = site.request("GET", DESCRIPTOR_PATH, headers=AUTH)
    assert reply.status == 200
    assert once(reply, "Cache-Control") == "no-store"
    assert reply.json() == {
        "format": "atlas-deployment/1",
        "operational_detail": True,
        "profile": "private",
        "simulations": "enabled",
    }


def test_the_dashboard_is_read_only(private: tuple[Site, Stack]) -> None:
    site, _ = private
    head = site.request("HEAD", "/", headers=AUTH)
    assert head.status == 200
    assert head.body == b""
    for method in ("POST", "PUT", "DELETE"):
        reply = site.request(method, "/", headers=AUTH, body=b"{}")
        assert reply.status == 405, method
        assert reply.json()["error"]["code"] == "method_not_allowed"


@pytest.mark.parametrize(
    "path",
    [
        "/index.html",
        f"/{MANIFEST_NAME}",
        "/assets/",
        "/assets",
        "/assets/index.html",
        "/assets/missing-12345678.js",
        "/forecasting/",
        "/Forecasting",
        "/favicon.ico",
        "/web/dist/index.html",
        "/release.json",
    ],
)
def test_nothing_else_is_served(private: tuple[Site, Stack], path: str) -> None:
    site, _ = private
    reply = site.request("GET", path, headers=AUTH)
    assert reply.status == 404, path
    assert reply.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "path",
    [
        "/assets/../index.html",
        "/assets/%2e%2e/index.html",
        "/assets/..%2Findex.html",
        "//index.html",
        "/assets//favicon.svg",
        "/./forecasting",
    ],
)
def test_no_path_leaves_the_published_directory(private: tuple[Site, Stack], path: str) -> None:
    site, _ = private
    reply = site.request("GET", path, headers=AUTH)
    assert reply.status == 400, path
    assert reply.json()["error"]["code"] == "bad_request"


def test_an_unlisted_file_or_a_link_is_never_served(private: tuple[Site, Stack]) -> None:
    site, running = private
    planted = running.published / "assets" / "planted-12345678.js"
    planted.write_text("alert(1)", encoding="utf-8")
    favicon = running.published / "favicon.svg"
    original = favicon.read_bytes()
    favicon.unlink()
    favicon.symlink_to(running.release / "pyproject.toml")
    try:
        assert site.request("GET", "/assets/planted-12345678.js", headers=AUTH).status == 404
        linked = site.request("GET", "/favicon.svg", headers=AUTH)
        assert linked.status in (403, 404)
        assert b"[project]" not in linked.body
        assert linked.json()["error"]["code"] in ("forbidden", "not_found")
    finally:
        planted.unlink()
        favicon.unlink()
        favicon.write_bytes(original)
        favicon.chmod(0o640)
    assert site.request("GET", "/favicon.svg", headers=AUTH).status == 200


def test_the_api_keeps_its_own_policy_next_to_the_dashboard(private: tuple[Site, Stack]) -> None:
    site, _ = private
    reply = site.request("GET", "/api/v1/forecasts", headers=AUTH)
    assert reply.status == 200
    assert once(reply, "Content-Security-Policy") == API_CSP
    assert once(reply, "Cache-Control") == "no-store"


def test_a_simulation_from_the_dashboard_needs_no_key_in_the_browser(
    private: tuple[Site, Stack],
) -> None:
    site, _ = private
    body = json.dumps({"horizon_days": 7}).encode()
    reply = site.request(
        "POST",
        "/api/v1/simulations",
        headers={**AUTH, "Content-Type": "application/json"},
        body=body,
    )
    assert reply.status == 200, reply.body[:300]
    assert "HYPOTHETICAL" in reply.json()["data"]["label"]


def test_the_published_copy_is_the_release_build(private: tuple[Site, Stack]) -> None:
    _, running = private
    copied = {
        p.relative_to(running.published).as_posix()
        for p in running.published.rglob("*")
        if p.is_file()
    }
    assert copied == {f.path for f in BUILT.files} | {MANIFEST_NAME}
    assert (running.release / DIST / "index.html").read_bytes() == (
        running.published / "index.html"
    ).read_bytes()


def test_the_access_log_records_dashboard_requests_without_queries(
    private: tuple[Site, Stack],
) -> None:
    site, _ = private
    site.request("GET", "/decisions?scenario=reduced_budget", headers=AUTH)
    last = site.log()[-1]
    # A page is logged as the file that answered it (try_files), and never with its query.
    assert last["path"] == "/index.html"
    assert last["user"] == USER
    assert all("scenario" not in json.dumps(e) for e in site.log())
    assert all(PASSWORD not in json.dumps(e) for e in site.log())


# --- The public demo ---------------------------------------------------------------------------


def test_the_public_demo_serves_a_read_only_dashboard(public: Site) -> None:
    page = public.request("GET", "/")
    assert page.status == 200
    assert once(page, "Content-Security-Policy") == DASHBOARD_CSP
    descriptor = public.request("GET", DESCRIPTOR_PATH).json()
    assert descriptor["profile"] == "public-demo"
    assert descriptor["simulations"] == "disabled_public_demo"
    assert descriptor["operational_detail"] is False
    for item in BUILT.files:
        url = "/" if item.path == "index.html" else item.url
        assert public.request("GET", url).status == 200, item.path


def test_the_public_demo_exposes_no_computation_or_operational_detail(public: Site) -> None:
    assert public.request("GET", "/api/v1/forecasts").status == 200
    for method, path in (
        ("POST", "/api/v1/simulations"),
        ("GET", "/api/v1/system"),
        ("GET", "/health/ready"),
    ):
        reply = public.request(
            method, path, headers={"Content-Type": "application/json"}, body=b"{}"
        )
        assert reply.status in (404, 405), (method, path)


def test_the_site_answers_for_its_domain_only(public: Site) -> None:
    with pytest.raises(OSError):  # no certificate for another name
        public.request("GET", "/", host="other.example.org")
    assert DOMAIN in public.nginx.site.read_text("utf-8")
