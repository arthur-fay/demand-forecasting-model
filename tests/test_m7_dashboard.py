"""The dashboard build (M7): its manifest, its verification and the API's own mount.

The committed build (``web/dist``) must verify against its manifest; the manifest parser
refuses anything a server could be tricked by; the API serves exactly the manifest's
files and routes, from memory, with the dashboard's Content-Security-Policy, and leaves
every other path (traversal attempts included) to the API's JSON 404.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from atlas.api.middleware import API_CSP
from atlas.api.settings import load_api_settings
from atlas.dashboard import (
    CACHE_IMMUTABLE,
    DASHBOARD_CSP,
    DASHBOARD_HEADERS,
    DESCRIPTOR_PATH,
    DIST,
    MANIFEST_NAME,
    DashboardError,
    descriptor,
    load_build,
    manifest_record,
    parse_manifest,
    read_verified,
    valid_file_path,
    verify_build,
)
from atlas.errors import ConfigError
from tests.conftest import REPO_ROOT
from tests.m5_helpers import add_api_inputs, client
from tests.m7_helpers import BUILD, copy_build

if TYPE_CHECKING:
    from atlas.api.reference import ReferenceStore

KEY = "k" * 32


def manifest() -> dict[str, Any]:
    document: dict[str, Any] = json.loads((BUILD / MANIFEST_NAME).read_text("utf-8"))
    return document


def encoded(document: dict[str, Any]) -> bytes:
    return json.dumps(document).encode("utf-8")


# --- The committed build ---------------------------------------------------------------------


def test_the_committed_build_matches_its_manifest() -> None:
    build = load_build(BUILD)
    assert verify_build(BUILD, build) == []
    assert build.entry.path == "index.html"
    assert build.routes[0] == "/"
    record = manifest_record(BUILD)
    assert record["files"]["index.html"] == build.entry.sha256
    assert record["csp"] == DASHBOARD_CSP


def test_the_committed_build_references_only_its_own_files() -> None:
    """index.html loads its scripts and styles from the build, from this origin."""
    html = (BUILD / "index.html").read_text("utf-8")
    build = load_build(BUILD)
    for reference in (*_attributes(html, "src"), *_attributes(html, "href")):
        assert reference.startswith("/"), reference
        assert build.file(reference.lstrip("/")) is not None, reference
    assert "<script>" not in html, "nothing inline (CSP)"
    assert "style=" not in html, "nothing inline (CSP)"


def _attributes(html: str, name: str) -> list[str]:
    parts = html.split(f'{name}="')[1:]
    return [part.split('"', 1)[0] for part in parts]


def test_the_build_evaluates_no_strings() -> None:
    """No eval in the shipped code. (Injection sinks are checked where they matter, in a
    real browser under the policy: the end-to-end tests fail on any CSP or Trusted Types
    violation; React's own sink is only reached by rendering a <script> element.)"""
    build = load_build(BUILD)
    scripts = [(BUILD / f.path).read_text("utf-8") for f in build.files if f.suffix == ".js"]
    assert "eval(" not in "\n".join(scripts)


def test_the_build_sends_no_credential() -> None:
    """The browser never holds or sends a key or token: no credential header is ever set
    by the dashboard (private deployments use the browser's own Basic session, and nginx
    adds the simulation key)."""
    build = load_build(BUILD)
    text = [f for f in build.files if f.suffix in (".html", ".js", ".css", ".svg")]
    shipped = "\n".join((BUILD / f.path).read_text("utf-8") for f in text)
    for header in ("x-api-key", "authorization", "bearer "):
        assert header not in shipped.lower(), header
    for credentials in ("credentials:`include`", "credentials:'include'", 'credentials:"include"'):
        assert credentials not in shipped


# --- The manifest parser -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda m: m.update(format="atlas-dashboard/2"), "format"),
        (lambda m: m.update(extra=True), "exactly the keys"),
        (lambda m: m.update(version="1.0"), "MAJOR.MINOR.PATCH"),
        (lambda m: m.update(entry="main.html"), "entry"),
        (lambda m: m.update(routes=["/forecasting"]), "routes"),
        (lambda m: m.update(routes=["/", "/"]), "routes"),
        (lambda m: m.update(routes=["/", "/a/b"]), "routes"),
        (lambda m: m.update(routes=["/", "/Upper"]), "routes"),
        (lambda m: m.update(routes=["/", "/x?y"]), "routes"),
        (lambda m: m["files"].pop("index.html"), "index.html"),
        (lambda m: m.update(files={}), "between 1 and"),
    ],
)
def test_malformed_manifests_are_refused(change: Any, message: str) -> None:
    document = manifest()
    change(document)
    with pytest.raises(DashboardError, match=message):
        parse_manifest(encoded(document))


@pytest.mark.parametrize(
    "name",
    [
        "../index.html",
        "assets/../../etc/passwd.js",
        "/etc/passwd",
        "assets/sub/x-12345678.js",
        "assets/x.js",  # not content-hashed: could not be cached as immutable
        "index.php",
        "x.html.bak",
        ".env",
        "assets/x-12345678.map",
        "Index.html",
        "assets/x 12345678.js",
        "assets/x-12345678.js\n",
    ],
)
def test_file_names_a_server_could_be_tricked_by_are_refused(name: str) -> None:
    assert not valid_file_path(name)
    document = manifest()
    document["files"][name] = {"sha256": "0" * 64, "bytes": 1}
    with pytest.raises(DashboardError, match="unexpected file name"):
        parse_manifest(encoded(document))


@pytest.mark.parametrize(
    "entry",
    [
        {"sha256": "x" * 64, "bytes": 1},
        {"sha256": "0" * 63, "bytes": 1},
        {"sha256": "0" * 64, "bytes": -1},
        {"sha256": "0" * 64, "bytes": True},
        {"sha256": "0" * 64},
        {"sha256": "0" * 64, "bytes": 1, "mode": 755},
    ],
)
def test_file_entries_are_checked(entry: dict[str, Any]) -> None:
    document = manifest()
    document["files"]["favicon.svg"] = entry
    with pytest.raises(DashboardError):
        parse_manifest(encoded(document))


def test_a_route_may_not_shadow_a_file_or_the_descriptor() -> None:
    document = manifest()
    document["files"]["forecasting.svg"] = {"sha256": "0" * 64, "bytes": 1}
    document["routes"].append("/forecasting.svg")
    with pytest.raises(DashboardError):
        parse_manifest(encoded(document))


def test_not_json_is_refused() -> None:
    with pytest.raises(DashboardError, match="not JSON"):
        parse_manifest(b"\xff")
    with pytest.raises(DashboardError, match="object"):
        parse_manifest(b"[]")


# --- Verification ------------------------------------------------------------------------------


def test_an_altered_missing_or_added_file_is_detected(tmp_path: Path) -> None:
    build_dir = copy_build(tmp_path / "dist")
    build = load_build(build_dir)
    script = next(f for f in build.files if f.path.endswith(".js"))
    (build_dir / script.path).write_text("alert(1)", encoding="utf-8")
    (build_dir / "favicon.svg").unlink()
    (build_dir / "assets" / "planted-12345678.js").write_text("x", encoding="utf-8")
    problems = verify_build(build_dir, build)
    assert f"dashboard file altered: {script.path}" in problems
    assert "dashboard file missing: favicon.svg" in problems
    assert "dashboard file not in the manifest: assets/planted-12345678.js" in problems
    with pytest.raises(DashboardError):
        read_verified(build_dir)


def test_a_link_is_never_a_dashboard_file(tmp_path: Path) -> None:
    build_dir = copy_build(tmp_path / "dist")
    (build_dir / "favicon.svg").unlink()
    (build_dir / "favicon.svg").symlink_to(REPO_ROOT / "pyproject.toml")
    problems = verify_build(build_dir, load_build(build_dir))
    assert any("favicon.svg" in p and "regular file" in p for p in problems)


def test_a_missing_or_linked_manifest_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DashboardError, match="missing"):
        load_build(tmp_path)
    (tmp_path / MANIFEST_NAME).symlink_to(BUILD / MANIFEST_NAME)
    with pytest.raises(DashboardError, match="regular file"):
        load_build(tmp_path)


def test_the_descriptor_is_canonical_and_checked() -> None:
    text = descriptor("local", "enabled", operational_detail=True)
    assert json.loads(text) == {
        "format": "atlas-deployment/1",
        "operational_detail": True,
        "profile": "local",
        "simulations": "enabled",
    }
    with pytest.raises(DashboardError):
        descriptor("tunnel", "enabled", operational_detail=True)
    with pytest.raises(DashboardError):
        descriptor("local", "maybe", operational_detail=True)


# --- The API's dashboard mount -----------------------------------------------------------------


def test_every_page_is_the_index_with_the_dashboard_policy(api_store: ReferenceStore) -> None:
    build = load_build(BUILD)
    index = (BUILD / "index.html").read_bytes()
    with client(api_store) as test_client:
        for route in build.routes:
            answer = test_client.get(route)
            assert answer.status_code == 200, route
            assert answer.content == index
            assert answer.headers["content-type"] == "text/html; charset=utf-8"
            assert answer.headers["cache-control"] == "no-cache"
            for name, value in DASHBOARD_HEADERS.items():
                assert answer.headers.get_list(name) == [value], name
            assert answer.headers["x-frame-options"] == "DENY"
            assert answer.headers["x-content-type-options"] == "nosniff"
            assert answer.headers["x-request-id"]


def test_every_built_file_is_served_as_built(api_store: ReferenceStore) -> None:
    build = load_build(BUILD)
    with client(api_store) as test_client:
        for item in build.files:
            if item.path == "index.html":
                continue
            answer = test_client.get(item.url, headers={"Accept-Encoding": "identity"})
            assert answer.status_code == 200, item.path
            assert hashlib.sha256(answer.content).hexdigest() == item.sha256
            assert answer.headers["content-type"] == item.content_type
            assert answer.headers["cache-control"] == item.cache_control
            assert answer.headers["etag"] == item.etag
            if item.path.startswith("assets/"):
                assert answer.headers["cache-control"] == CACHE_IMMUTABLE


def test_compression_conditional_requests_and_head(api_store: ReferenceStore) -> None:
    build = load_build(BUILD)
    script = next(f for f in build.files if f.path.endswith(".js") and f.size > 2000)
    with client(api_store) as test_client:
        zipped = test_client.get(script.url, headers={"Accept-Encoding": "gzip"})
        assert zipped.headers["content-encoding"] == "gzip"
        assert hashlib.sha256(zipped.content).hexdigest() == script.sha256  # decoded by httpx
        assert "Accept-Encoding" in zipped.headers["vary"]
        unchanged = test_client.get(script.url, headers={"If-None-Match": script.etag})
        assert unchanged.status_code == 304
        assert unchanged.content == b""
        head = test_client.head("/")
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["content-security-policy"] == DASHBOARD_CSP


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("gzip", True),
        ("gzip, deflate, br", True),
        ("br;q=1.0, gzip;q=0.8", True),
        ("x-gzip", True),
        ("*", True),
        ("gzip;q=0", False),
        ("gzip;q=0.0, identity", False),
        ("*;q=0", False),
        ("gzip;q=0, *", False),
        ("identity", False),
        ("", False),
        ("gzip;q=x", False),
        ("deflate, br", False),
    ],
)
def test_gzip_only_for_clients_that_accept_it(value: str, accepted: bool) -> None:
    from atlas.api.dashboard import accepts_gzip

    assert accepts_gzip(value) is accepted


def test_a_client_refusing_gzip_gets_the_plain_body(api_store: ReferenceStore) -> None:
    build = load_build(BUILD)
    script = next(f for f in build.files if f.path.endswith(".js") and f.size > 2000)
    with client(api_store) as test_client:
        plain = test_client.get(script.url, headers={"Accept-Encoding": "gzip;q=0, identity"})
    assert "content-encoding" not in plain.headers
    assert hashlib.sha256(plain.content).hexdigest() == script.sha256


def test_the_compressed_bodies_are_deterministic() -> None:
    from atlas.api.dashboard import load_dashboard

    first = load_dashboard(REPO_ROOT, simulations_enabled=True)
    second = load_dashboard(REPO_ROOT, simulations_enabled=True)
    for path, asset in first.assets.items():
        assert asset.gzipped == second.assets[path].gzipped
        if asset.gzipped is not None:
            assert gzip.decompress(asset.gzipped) == asset.body


def test_the_dashboard_is_read_only(api_store: ReferenceStore) -> None:
    with client(api_store) as test_client:
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            answer = test_client.request(method, "/")
            assert answer.status_code == 405, method
            assert answer.headers["allow"] == "GET, HEAD"
            assert answer.json()["error"]["code"] == "method_not_allowed"
            # JSON answers on dashboard paths are the API's: its policy, not the page's.
            assert answer.headers["content-security-policy"] == API_CSP


@pytest.mark.parametrize(
    "path",
    [
        "/index.html",
        f"/{MANIFEST_NAME}",
        "/web/dist/index.html",
        "/assets/../pyproject.toml",
        "/assets/%2e%2e/pyproject.toml",
        "/assets/",
        "/assets",
        "/forecasting/",
        "/Forecasting",
        "/favicon.ico",
        "/assets/index.html",
        "/src/main.tsx",
        "/../../etc/passwd",
    ],
)
def test_nothing_but_the_build_is_served(api_store: ReferenceStore, path: str) -> None:
    with client(api_store) as test_client:
        answer = test_client.get(path)
    assert answer.status_code == 404, path
    assert answer.json()["error"]["code"] == "not_found"
    assert answer.headers["content-security-policy"] == API_CSP


def test_the_api_keeps_its_own_headers(api_store: ReferenceStore) -> None:
    with client(api_store) as test_client:
        answer = test_client.get("/api/v1/forecasts")
    assert answer.headers["content-security-policy"] == API_CSP
    assert answer.headers["cache-control"] == "no-store"
    assert "cross-origin-opener-policy" not in answer.headers


@pytest.mark.parametrize(
    ("changes", "simulations"),
    [({}, "enabled"), ({"api_key": KEY}, "disabled_api_key")],
)
def test_the_descriptor_says_whether_a_browser_may_simulate(
    api_store: ReferenceStore, changes: dict[str, Any], simulations: str
) -> None:
    with client(api_store, **changes) as test_client:
        answer = test_client.get(DESCRIPTOR_PATH)
    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "no-store"
    assert answer.headers["content-security-policy"] == API_CSP
    # The ETag names this body: another key setting gives another tag.
    assert answer.headers["etag"] != '"descriptor"'
    assert answer.headers["etag"].strip('"') == hashlib.sha256(answer.content).hexdigest()[:16]
    assert answer.json() == {
        "format": "atlas-deployment/1",
        "operational_detail": True,
        "profile": "local",
        "simulations": simulations,
    }


def test_the_dashboard_can_be_turned_off(api_store: ReferenceStore) -> None:
    with client(api_store, dashboard_enabled=False) as test_client:
        assert test_client.get("/").status_code == 404
        assert test_client.get(DESCRIPTOR_PATH).status_code == 404
        assert test_client.get("/api/v1/forecasts").status_code == 200


def test_a_damaged_build_is_not_served_and_the_api_keeps_working(
    repo_copy: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from atlas.api.reference import load_reference_store
    from tests.m5_helpers import settings

    root = add_api_inputs(repo_copy)
    build_dir = copy_build(root / DIST)
    (build_dir / "index.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    store = load_reference_store(settings(root))
    with caplog.at_level(logging.WARNING, logger="atlas.api"), client(store) as test_client:
        assert test_client.get("/").status_code == 404
        assert test_client.get("/api/v1/forecasts").status_code == 200
    assert any(getattr(r, "event", None) == "dashboard_unavailable" for r in caplog.records)


def test_the_host_allow_list_covers_the_dashboard(api_store: ReferenceStore) -> None:
    with client(api_store) as test_client:
        answer = test_client.get("/", headers={"Host": "evil.example"})
    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == "invalid_host"


def test_the_dashboard_is_not_part_of_the_api_contract() -> None:
    contract = json.loads((REPO_ROOT / "docs/api/openapi.json").read_text("utf-8"))
    assert "/" not in contract["paths"]
    assert DESCRIPTOR_PATH not in contract["paths"]


@pytest.mark.parametrize(
    ("value", "expected"), [("", True), ("true", True), ("0", False), ("off", False)]
)
def test_the_dashboard_setting(tmp_path: Path, value: str, expected: bool) -> None:
    settings = load_api_settings(repo_root=REPO_ROOT, env={"ATLAS_API_DASHBOARD": value})
    assert settings.dashboard_enabled is expected


def test_an_invalid_dashboard_setting_is_refused() -> None:
    with pytest.raises(ConfigError, match="ATLAS_API_DASHBOARD"):
        load_api_settings(repo_root=REPO_ROOT, env={"ATLAS_API_DASHBOARD": "maybe"})
