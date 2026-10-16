"""The dashboard in the M6 deployment (M7): routes, nginx site, publication, manifest,
activation and rollback, and post-deployment verification.

The nginx site gets one location for the dashboard's pages, one for its content-hashed
assets (their exact names), one per other top-level file and one for the deployment
descriptor; every one reads the release's published copy (root:www-data, read-only, no
links), has the dashboard's Content-Security-Policy and answers GET and HEAD only. An
activation publishes the copy before the site that reads it, a rollback renders the site
of the restored release (so dashboard and API move together), and pruning removes the
copies of removed releases.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from atlas.dashboard import (
    DASHBOARD_CSP,
    DASHBOARD_HEADERS,
    DASHBOARD_TEXT_CSP,
    DESCRIPTOR_FORMAT,
    DESCRIPTOR_PATH,
    DIST,
    MANIFEST_NAME,
    is_text,
    load_build,
    policy_problems,
    recorded_policy_problems,
)
from atlas.deploy.layout import Layout
from atlas.deploy.manifest import check_release, load_manifest
from atlas.deploy.publish import PublishError, check_published, prune_published, publish
from atlas.deploy.releases import (
    ACTIVATED,
    IN_PROGRESS,
    REFUSED,
    ROLLED_BACK,
    Deployment,
    compare_manifests,
)
from atlas.deploy.render import (
    FIXED_DASHBOARD_HEADERS,
    DashboardPolicy,
    Renderer,
    RenderError,
    dashboard_headers,
    descriptor_for,
)
from atlas.deploy.routes import (
    DASHBOARD,
    PROFILE_ACCESS,
    Route,
    RouteError,
    dashboard_build,
    dashboard_routes,
    load_routes,
)
from atlas.deploy.siteconfig import RESOURCE_PROFILES, load_config
from atlas.deploy.verify import Response, Target, Verification, _dashboard, _text_file
from tests.conftest import REPO_ROOT
from tests.m6_helpers import DEPLOY_ENV_PRIVATE, Nginx, make_release, need_tools, write
from tests.m7_helpers import BUILD, make_dashboard_release
from tests.test_m6_releases import (
    A,
    B,
    FakeHost,
    InterruptedHost,
    deployment,
    name,
    runs,
    verifier,
)

PUBLIC = (
    "ATLAS_DEPLOY_PROFILE=public-demo\nATLAS_DEPLOY_DOMAIN=atlas.example.org\n"
    "ATLAS_DEPLOY_IPV6=false\nATLAS_DEPLOY_SECURITY_REVIEW=SEC-2026-001\n"
)


def renderer(env: str = DEPLOY_ENV_PRIVATE, release: Path = REPO_ROOT) -> Renderer:
    return Renderer.for_release(load_config(env), Layout(), release)


# --- Routes -------------------------------------------------------------------------------------


def test_the_dashboard_routes_come_from_the_build() -> None:
    build = load_build(BUILD)
    routes = dashboard_routes(build, load_routes(REPO_ROOT))
    urls = {route.url for route in routes}
    assert set(build.routes) <= urls
    assert {f.url for f in build.files if f.path != "index.html"} <= urls
    assert DESCRIPTOR_PATH in urls
    assert "/index.html" not in urls
    assert f"/{MANIFEST_NAME}" not in urls
    assert all(route.access == DASHBOARD for route in routes)


def test_both_exposed_profiles_route_the_dashboard() -> None:
    assert DASHBOARD in PROFILE_ACCESS["private"]
    assert DASHBOARD in PROFILE_ACCESS["public-demo"]


@pytest.mark.parametrize("route", ["/api", "/health"])
def test_a_page_may_not_shadow_the_api(route: str) -> None:
    document = json.loads((BUILD / MANIFEST_NAME).read_text("utf-8"))
    document["routes"].append(route)
    build = load_build_from(document)
    api = [Route("/api", "GET", "read", "= /api"), Route("/health", "GET", "read", "= /health")]
    with pytest.raises(RouteError, match="shadow the API"):
        dashboard_routes(build, api)


def load_build_from(document: dict[str, Any]) -> Any:
    from atlas.dashboard import parse_manifest

    return parse_manifest(json.dumps(document).encode())


def test_a_release_without_a_dashboard_has_no_dashboard_routes(tmp_path: Path) -> None:
    release = make_release(tmp_path)
    assert dashboard_build(release) is None
    site = renderer(release=release).nginx()
    assert "Dashboard (M7)" not in site
    assert "location = /atlas-deployment.json" not in site


# --- The nginx site ---------------------------------------------------------------------------


def dashboard_part(site: str) -> str:
    """The dashboard's locations (between the contract's routes and the catch-all)."""
    return site.split("    # Dashboard (M7)")[1].split("    # Anything else: 404")[0]


def test_the_private_site_serves_the_dashboard_behind_authentication(tmp_path: Path) -> None:
    release = make_dashboard_release(tmp_path)
    site = renderer(release=release).nginx()
    part = dashboard_part(site)
    published = f"/opt/atlas/dashboard/{release.name}"
    # One location for the pages, one for the hashed assets, one per other top-level file
    # (the favicon and the licence notices, M8-05) and one for the descriptor.
    top = [f.path for f in load_build(release / DIST).files if "/" not in f.path]
    files = [path for path in top if path != "index.html"]
    assert {"favicon.svg", "third-party-licences.txt", "inter-licence.txt"} <= set(files)
    for path in files:
        assert part.count(f"    location = /{path} {{\n") == 1, path
    n = len(files)
    # Every location reads the published copy, follows no link and answers GET/HEAD only.
    assert part.count("location = ") + part.count('location ~ "') == 3 + n
    assert part.count("return 405; }") == 3 + n
    # The descriptor is answered after authentication (try_files, not a `return`).
    assert "try_files /.atlas-descriptor @atlas_descriptor;" in part
    assert part.count(f"root {published};") == 2 + n
    assert part.count("disable_symlinks on;") == 2 + n
    # The policy of the dashboard on its pages and files (the one for text on the licence
    # notices, M8), the API's on the API and on the descriptor (JSON).
    texts = len([path for path in files if path.endswith(".txt")])
    assert texts == 2
    html_policy = f'add_header Content-Security-Policy "{DASHBOARD_CSP}" always;'
    text_policy = f'add_header Content-Security-Policy "{DASHBOARD_TEXT_CSP}" always;'
    assert part.count(html_policy) == 2 + n - texts
    assert part.count(text_policy) == texts
    descriptor = part.split("location @atlas_descriptor {")[1].split("    }")[0]
    assert "default-src 'none'; frame-ancestors 'none'" in descriptor
    assert DASHBOARD_CSP not in descriptor
    # Its own rate zone, no looser than twice the API's read limit (before authentication).
    assert "limit_req_zone $binary_remote_addr zone=atlas_static:10m rate=20r/s;" in site
    assert all(2 * int(r.read_rate.removesuffix("r/s")) >= 20 for r in RESOURCE_PROFILES.values())
    assert "limit_req zone=atlas_static burst=60 nodelay;" in part
    api = site.split("    # Dashboard (M7)")[0]
    assert DASHBOARD_CSP not in api
    assert "default-src 'none'; frame-ancestors 'none'" in api
    # Authentication covers the dashboard too (server level, never switched off there).
    assert 'auth_basic "ATLAS private research deployment";' in site
    assert "auth_basic off" not in part
    # The private descriptor lets the browser run simulations (nginx adds the key).
    assert '"profile":"private","simulations":"enabled"' in part
    assert "X-API-Key" not in part
    assert "simulation-key.conf" not in part


def location(part: str, url: str) -> str:
    """The body of the exact location of ``url`` in a dashboard part."""
    return part.split(f"    location = {url} {{\n")[1].split("\n    }")[0]


def test_the_licence_notices_get_the_policy_for_text() -> None:
    """A browser shows text/plain in its own viewer, whose inline style the HTML policy
    refuses (a violation on every visit, lines that do not wrap: M8 final review). The
    notices' locations send the policy for text, with every other header of the dashboard;
    the favicon keeps the HTML policy."""
    part = dashboard_part(renderer().nginx())
    others = {k: v for k, v in DASHBOARD_HEADERS.items() if k != "Content-Security-Policy"}
    for url in ("/third-party-licences.txt", "/inter-licence.txt"):
        body = location(part, url)
        assert "default_type text/plain;" in body
        assert "charset utf-8;" in body
        assert body.count("add_header Content-Security-Policy") == 1
        assert f'add_header Content-Security-Policy "{DASHBOARD_TEXT_CSP}" always;' in body
        for header, value in others.items():
            assert f'add_header {header} "{value}" always;' in body, header
        for line in ('X-Content-Type-Options "nosniff"', 'X-Frame-Options "DENY"'):
            assert f"add_header {line} always;" in body
    favicon = location(part, "/favicon.svg")
    assert f'add_header Content-Security-Policy "{DASHBOARD_CSP}" always;' in favicon


def test_a_text_asset_gets_its_own_location_with_the_policy_for_text() -> None:
    """The policy follows the type, as in the API: a plain-text asset would be served from a
    location of its own (the committed build has none)."""
    import dataclasses

    from atlas.dashboard import parse_manifest

    document = json.loads((BUILD / MANIFEST_NAME).read_text("utf-8"))
    document["files"]["assets/notes-0123456789.txt"] = {"sha256": "0" * 64, "bytes": 1}
    build = parse_manifest(json.dumps(document).encode())
    part = dashboard_part(dataclasses.replace(renderer(), dashboard=build).nginx())
    assets = re.findall(r'    location ~ "\^/assets/.*?\n    \}', part, re.DOTALL)
    assert len(assets) == 2
    text = next(block for block in assets if "notes-0123456789" in block)
    assert 'location ~ "^/assets/(?:notes-0123456789\\.txt)$" {' in text
    assert "            text/plain txt;" in text
    assert "        charset_types text/javascript text/css text/plain;" in text
    assert f'add_header Content-Security-Policy "{DASHBOARD_TEXT_CSP}" always;' in text
    code = next(block for block in assets if block is not text)
    assert f'add_header Content-Security-Policy "{DASHBOARD_CSP}" always;' in code
    assert "notes-0123456789" not in code


def test_every_asset_is_listed_by_name_and_immutable() -> None:
    part = dashboard_part(renderer().nginx())
    build = load_build(BUILD)
    assets = [f for f in build.files if f.path.startswith("assets/")]
    line = next(line for line in part.splitlines() if 'location ~ "^/assets/' in line)
    for item in assets:
        assert item.path.removeprefix("assets/").replace(".", r"\.") in line
    assert line.count("|") == len(assets) - 1
    assert 'add_header Cache-Control "max-age=31536000, immutable" always;' in part
    assert "public" not in part.split("Cache-Control", 1)[1].splitlines()[0]
    pages = next(line for line in part.splitlines() if 'location ~ "^/(?:' in line)
    for route in build.routes[1:]:
        assert route.lstrip("/") in pages


def test_the_public_demo_site_serves_a_read_only_dashboard() -> None:
    site = renderer(PUBLIC).nginx()
    part = dashboard_part(site)
    assert '"profile":"public-demo","simulations":"disabled_public_demo"' in part
    assert '"operational_detail":false' in part
    assert "location = /api/v1/simulations " not in site
    assert "auth_basic" not in part


def test_the_site_compresses_text_answers() -> None:
    site = renderer().nginx()
    assert "gzip on;" in site
    assert "gzip_types application/json text/css text/javascript image/svg+xml;" in site
    # gzip_types compares a location's whole default type: a charset is added by nginx's
    # charset filter, never written into the type ("text/html; charset=utf-8" is never
    # compressed).
    part = dashboard_part(site)
    defaults = [line.strip() for line in part.splitlines() if "default_type" in line]
    assert defaults
    assert all(re.fullmatch(r"default_type [a-z0-9+./-]+;", line) for line in defaults), defaults
    pages = part.split('location ~ "^/(?:')[1].split("\n    }")[0]
    assert "default_type text/html;" in pages
    assert "charset utf-8;" in pages


def test_nginx_never_types_its_own_answers_by_the_requested_extension() -> None:
    """Ubuntu's nginx.conf includes mime.types for every site: without the server's own
    empty `types`, a JSON error on /x.js or /x.html would be labelled JavaScript or HTML."""
    server = renderer().nginx().split("    listen 443 ssl;")[1]
    assert "    types { }\n    default_type application/json;\n" in server


@pytest.mark.parametrize("env", [DEPLOY_ENV_PRIVATE, PUBLIC])
def test_nginx_accepts_the_site_with_the_dashboard(tmp_path: Path, env: str) -> None:
    need_tools("nginx", "openssl")
    from tests.m6_helpers import make_tls

    _, cert, key = make_tls(tmp_path / "tls")
    text = env + f"ATLAS_DEPLOY_TLS_CERT={cert}\nATLAS_DEPLOY_TLS_KEY={key}\n"
    layout = Layout(nginx=PurePosixPath(str(tmp_path / "nginx-atlas")))
    write(tmp_path / "nginx-atlas/simulation-key.conf", 'proxy_set_header X-API-Key "x";\n', 0o600)
    write(tmp_path / "nginx-atlas/htpasswd", "alice:$6$s$h\n", 0o640)
    site = Renderer.for_release(load_config(text, layout), layout, REPO_ROOT).nginx()
    result = Nginx(tmp_path / "nginx", site).test()
    assert result.returncode == 0, result.stderr
    assert "[warn]" not in result.stderr  # e.g. a duplicate MIME type, on every reload


# --- Publication --------------------------------------------------------------------------------


def published_layout(tmp_path: Path) -> Layout:
    return Layout(root=tmp_path)


def test_publication_is_a_verified_read_only_copy(tmp_path: Path) -> None:
    release = make_dashboard_release(tmp_path)
    layout = published_layout(tmp_path)
    build = load_build(release / DIST)
    uid = release.stat().st_uid
    host = publish(layout, release.name, release, build, owner=uid, chown=False)
    target = layout.local(host)
    assert host == layout.dashboard(release.name)
    assert check_published(target, build, owner=uid, proxy_gid=None) == []
    for entry in [target, *target.rglob("*")]:
        mode = stat.S_IMODE(entry.lstat().st_mode)
        assert mode == (0o750 if entry.is_dir() else 0o640), entry
    # Publishing again keeps the verified copy as it is.
    before = (target / "index.html").stat().st_ino
    publish(layout, release.name, release, build, owner=uid, chown=False)
    assert (target / "index.html").stat().st_ino == before


def test_an_altered_copy_is_replaced_and_a_planted_file_removed(tmp_path: Path) -> None:
    release = make_dashboard_release(tmp_path)
    layout = published_layout(tmp_path)
    build = load_build(release / DIST)
    uid = release.stat().st_uid
    target = layout.local(publish(layout, release.name, release, build, owner=uid, chown=False))
    (target / "index.html").chmod(0o640)
    (target / "index.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    write(target / "assets/planted-12345678.js", "x", 0o640)
    (target / "favicon.svg").unlink()
    (target / "favicon.svg").symlink_to("/etc/passwd")
    problems = check_published(target, build, owner=uid, proxy_gid=None)
    assert any("altered: index.html" in p for p in problems)
    assert any("planted" in p for p in problems)
    assert any("favicon.svg" in p for p in problems)
    publish(layout, release.name, release, build, owner=uid, chown=False)
    assert check_published(target, build, owner=uid, proxy_gid=None) == []
    assert not (target / "assets/planted-12345678.js").exists()
    assert [p.name for p in layout.local(layout.dashboard_root).iterdir()] == [release.name]


def test_a_failed_swap_keeps_the_previous_copy_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacing a copy that no longer verifies: if moving the new copy into place fails,
    the previous one is put back (nginx never finds the directory missing)."""
    release = make_dashboard_release(tmp_path)
    layout = published_layout(tmp_path)
    build = load_build(release / DIST)
    uid = release.stat().st_uid
    target = layout.local(publish(layout, release.name, release, build, owner=uid, chown=False))
    (target / "favicon.svg").chmod(0o600)  # no longer verifies: a new copy is made
    original = Path.rename

    def failing(self: Path, destination: Any) -> Path:
        if ".new-" in self.name:
            raise OSError("disk full")
        return original(self, destination)

    monkeypatch.setattr(Path, "rename", failing)
    with pytest.raises(PublishError, match="disk full"):
        publish(layout, release.name, release, build, owner=uid, chown=False)
    monkeypatch.undo()
    assert (target / "index.html").is_file()
    assert sorted(p.name for p in layout.local(layout.dashboard_root).iterdir()) == [release.name]


def test_a_release_whose_dashboard_was_altered_is_not_published(tmp_path: Path) -> None:
    release = make_dashboard_release(tmp_path)
    build = load_build(release / DIST)
    index = release / DIST / "index.html"
    index.chmod(0o644)
    index.write_text("altered", encoding="utf-8")
    with pytest.raises(PublishError, match="does not match its build"):
        publish(published_layout(tmp_path), release.name, release, build, chown=False)
    assert not published_layout(tmp_path).local(published_layout(tmp_path).dashboard_root).exists()


def test_pruning_removes_copies_of_other_releases_and_leftovers(tmp_path: Path) -> None:
    layout = published_layout(tmp_path)
    root = layout.local(layout.dashboard_root)
    for entry in ("0.7.0-aaaaaaaaaaaa", "0.7.0-bbbbbbbbbbbb", ".0.7.0-cccccccccccc.new-1a2b3c4d"):
        (root / entry).mkdir(parents=True)
    write(root / "notes.txt", "kept: not a release")
    removed = prune_published(layout, {"0.7.0-aaaaaaaaaaaa"})
    assert removed == [".0.7.0-cccccccccccc.new-1a2b3c4d", "0.7.0-bbbbbbbbbbbb"]
    assert sorted(p.name for p in root.iterdir()) == ["0.7.0-aaaaaaaaaaaa", "notes.txt"]


# --- The release manifest -----------------------------------------------------------------------


def test_the_manifest_identifies_the_dashboard(tmp_path: Path) -> None:
    release = make_dashboard_release(tmp_path)
    record = load_manifest(release)["dashboard"]
    build = load_build(BUILD)
    assert record["version"] == build.version
    assert record["fingerprint"] == build.fingerprint()
    assert record["files"] == {f.path: f.sha256 for f in build.files}
    assert record["csp"] == DASHBOARD_CSP
    # (M8) What a later tool needs to serve this dashboard as this release's code does.
    assert record["text_csp"] == DASHBOARD_TEXT_CSP
    assert record["headers"] == DASHBOARD_HEADERS
    assert record["descriptor_format"] == DESCRIPTOR_FORMAT
    assert record["descriptors"] == {
        "private": descriptor_for("private"),
        "public-demo": descriptor_for("public-demo"),
    }
    assert check_release(release) == []
    # Read back, it gives what this release's code sends with each type of file.
    policy = DashboardPolicy.of(record)
    for content_type in ("text/html; charset=utf-8", "text/javascript; charset=utf-8"):
        assert dict(policy.headers_for(content_type)) == DASHBOARD_HEADERS
    text = dict(policy.headers_for("text/plain; charset=utf-8"))
    assert text == {**DASHBOARD_HEADERS, "Content-Security-Policy": DASHBOARD_TEXT_CSP}


def test_an_altered_or_removed_dashboard_is_refused(tmp_path: Path) -> None:
    release = make_dashboard_release(tmp_path)
    script = next(p for p in (release / DIST / "assets").iterdir() if p.suffix == ".js")
    script.chmod(0o644)
    script.write_text("alert(1)", encoding="utf-8")
    problems = check_release(release)
    assert any("dashboard build does not verify" in p for p in problems)
    assert any(
        f"release file altered, added or removed: web/dist/assets/{script.name}" in p
        for p in problems
    )


def test_a_dashboard_the_manifest_does_not_record_is_refused(tmp_path: Path) -> None:
    release = make_release(tmp_path)
    release.chmod(0o755)
    import shutil

    shutil.copytree(BUILD, release / DIST)
    assert "the release has a dashboard build that its manifest does not record" in check_release(
        release
    )


def test_an_upgrade_plan_shows_a_dashboard_change(tmp_path: Path) -> None:
    old = load_manifest(make_dashboard_release(tmp_path / "a", commit=A))
    new = dict(old) | {"dashboard": dict(old["dashboard"]) | {"fingerprint": "0" * 64}}
    assert compare_manifests(old, old)["dashboard_changed"] is False
    assert compare_manifests(old, new)["dashboard_changed"] is True


# --- Activation and rollback --------------------------------------------------------------------


def dashboard_root(site: str) -> str:
    """The directory a site serves the dashboard from."""
    return next(
        line.split()[1].rstrip(";")
        for line in site.splitlines()
        if " root /" in line and "dashboard" in line
    )


def site_root(tmp_path: Path) -> str:
    return dashboard_root((tmp_path / "etc/nginx/sites-available/atlas.conf").read_text("utf-8"))


def private_deployment(tmp_path: Path, broken: set[str] | None = None) -> Any:
    return deployment(tmp_path, DEPLOY_ENV_PRIVATE, broken)


def test_a_private_activation_publishes_the_dashboard_before_the_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas.deploy import releases

    deploy, host = private_deployment(tmp_path)
    release = make_dashboard_release(tmp_path, commit=A)
    site = tmp_path / "etc/nginx/sites-available/atlas.conf"
    order: list[str] = []

    def publishing(*args: Any, **kwargs: Any) -> PurePosixPath:
        order.append("site already written" if site.exists() else "published first")
        return publish(*args, **kwargs)

    monkeypatch.setattr(releases, "publish", publishing)
    record = deploy.activate(name(A))
    assert order == ["published first"]
    assert record["outcome"] == ACTIVATED, record
    published = deploy.layout.local(deploy.layout.dashboard(name(A)))
    assert (
        check_published(published, load_build(release / DIST), owner=deploy.owner, proxy_gid=None)
        == []
    )
    assert site_root(tmp_path).endswith(f"/opt/atlas/dashboard/{name(A)}")
    version = load_build(release / DIST).version
    assert any(f"dashboard {version} published in " in line for line in record["detail"])
    assert host.calls.index("nginx -t") < host.calls.index("nginx reload")


def test_a_tunnel_activation_publishes_nothing(tmp_path: Path) -> None:
    deploy, _ = deployment(tmp_path)
    make_dashboard_release(tmp_path, commit=A)
    assert deploy.activate(name(A))["outcome"] == ACTIVATED
    assert not deploy.layout.local(deploy.layout.dashboard_root).exists()


def test_a_rollback_moves_the_dashboard_with_the_api(tmp_path: Path) -> None:
    deploy, _ = private_deployment(tmp_path, broken={name(B)})
    make_dashboard_release(tmp_path, commit=A)
    make_dashboard_release(tmp_path, commit=B)
    assert deploy.activate(name(A))["outcome"] == ACTIVATED
    record = deploy.activate(name(B))
    assert record["outcome"] == ROLLED_BACK, record
    assert deploy.current() == name(A)
    # The site serves the restored release's dashboard, as the API runs its code.
    assert site_root(tmp_path).endswith(f"/opt/atlas/dashboard/{name(A)}")


def test_an_explicit_rollback_renders_the_previous_dashboard(tmp_path: Path) -> None:
    deploy, _ = private_deployment(tmp_path)
    make_dashboard_release(tmp_path, commit=A)
    make_dashboard_release(tmp_path, commit=B)
    deploy.activate(name(A))
    deploy.activate(name(B))
    assert site_root(tmp_path).endswith(name(B))
    assert deploy.rollback()["outcome"] == ACTIVATED
    assert site_root(tmp_path).endswith(name(A))


@pytest.mark.parametrize("at", ["reset-failed", "restart"])
def test_recover_completes_an_interrupted_activation(tmp_path: Path, at: str) -> None:
    """An activation that dies after the switch, before or while the service restarts on
    the new release (a lost SSH session, Ctrl-C), has written the new site, but nginx still
    runs the previous one. `recover` completes it (the release's files again, nginx -t, the
    service restarted, nginx reloaded, then verified) before it keeps the release as
    verified: nginx serves the dashboard of the release the API runs (M8-02), and never
    loads it in front of another release's API."""
    deploy, _ = private_deployment(tmp_path)
    host = InterruptedHost(deploy.layout, interrupt=name(B), at=at)
    deploy.host = host
    deploy.verifier = runs(host)
    make_dashboard_release(tmp_path, commit=A)
    make_dashboard_release(tmp_path, commit=B)
    assert deploy.activate(name(A))["outcome"] == ACTIVATED
    with pytest.raises(KeyboardInterrupt):
        deploy.activate(name(B))
    assert deploy.current() == name(B)
    assert host.running == (name(B) if at == "restart" else name(A))
    assert site_root(tmp_path).endswith(f"/opt/atlas/dashboard/{name(B)}")
    assert dashboard_root(host.loaded[-1]).endswith(f"/opt/atlas/dashboard/{name(A)}")
    calls = len(host.calls)
    record = deploy.recover()
    assert record["outcome"] == ACTIVATED, record
    done = host.calls[calls:]
    assert done.index("nginx -t") < done.index(f"restart {name(B)}") < done.index("nginx reload")
    loaded = host.loaded[-1]
    assert dashboard_root(loaded).endswith(f"/opt/atlas/dashboard/{name(B)}")
    for site, running in zip(host.loaded, host.behind, strict=True):
        assert dashboard_root(site).endswith(f"/opt/atlas/dashboard/{running}")
    # What is kept as verified is what nginx serves and the service runs.
    assert loaded == (tmp_path / "etc/nginx/sites-available/atlas.conf").read_text("utf-8")
    assert host.running == name(B)
    good = deploy.good_state()
    assert good is not None
    assert good["release"] == name(B)
    assert deploy.pending() is None


#: A later atlas-deploy whose dashboard has another policy than the releases built before it.
NEWER_CSP = DASHBOARD_CSP + "; upgrade-insecure-requests"
NEWER_TEXT_CSP = DASHBOARD_TEXT_CSP + "; upgrade-insecure-requests"
PERMISSIONS = DASHBOARD_HEADERS["Permissions-Policy"]
NEWER_PERMISSIONS = PERMISSIONS + ", fullscreen=()"
NEWER_FORMAT = "atlas-deployment/2"


def newer_tool(monkeypatch: pytest.MonkeyPatch, *, new_format: bool = True) -> None:
    """Make this process stand for a later atlas-deploy: other Content-Security-Policies
    (for HTML and for text) and Permissions-Policy (and descriptor format), wherever the
    tool's modules hold them."""
    import atlas.dashboard
    from atlas.deploy import manifest, render, verify

    headers = {
        **DASHBOARD_HEADERS,
        "Content-Security-Policy": NEWER_CSP,
        "Permissions-Policy": NEWER_PERMISSIONS,
    }
    for module in (atlas.dashboard, manifest, render, verify):
        monkeypatch.setattr(module, "DASHBOARD_CSP", NEWER_CSP, raising=False)
        monkeypatch.setattr(module, "DASHBOARD_HEADERS", headers, raising=False)
        monkeypatch.setattr(module, "DASHBOARD_TEXT_CSP", NEWER_TEXT_CSP, raising=False)
        if new_format:
            monkeypatch.setattr(module, "DESCRIPTOR_FORMAT", NEWER_FORMAT, raising=False)


def as_each_release_serves(broken: set[str] | None = None) -> Callable[[Target], Verification]:
    """The fake verifier, and the real dashboard check against what each release's own API
    sends: its index.html with the policy of its code (which its manifest records), a
    licence notice with its policy for text, and a descriptor in its own format."""

    def check(target: Target) -> Verification:
        result = verifier(broken or set())(target)
        record = (target.manifest or {})["dashboard"]
        headers = {"content-security-policy": record["csp"]}
        page = answer(200, (BUILD / "index.html").read_bytes(), headers)
        path = _text_file(record)
        assert path is not None
        text = answer(
            200, (BUILD / path).read_bytes(), {"content-security-policy": record["text_csp"]}
        )
        own = json.dumps({"format": record.get("descriptor_format", DESCRIPTOR_FORMAT)})
        descriptor = answer(200, own.encode())
        _dashboard(result, page, descriptor, record, target.dashboard_dir, text=text)
        return result

    return check


def static_locations(dist: Path = BUILD) -> int:
    """The dashboard's locations with its own policy: the pages, the hashed assets and one
    per other top-level file of the build (the favicon and the licence notices)."""
    files = [f for f in load_build(dist).files if "/" not in f.path and f.path != "index.html"]
    return 2 + len(files)


def text_locations(dist: Path = BUILD) -> int:
    """Those of them with the policy for text: one per licence notice."""
    return len([f for f in load_build(dist).files if is_text(f.content_type)])


def assert_site_serves(
    tmp_path: Path,
    release: str,
    csp: str,
    permissions: str,
    descriptor_format: str,
    text_csp: str = DASHBOARD_TEXT_CSP,
) -> None:
    site = (tmp_path / "etc/nginx/sites-available/atlas.conf").read_text("utf-8")
    assert dashboard_root(site).endswith(f"/opt/atlas/dashboard/{release}")
    part = dashboard_part(site)
    locations, texts = static_locations(), text_locations()
    assert part.count(f'add_header Content-Security-Policy "{csp}" always;') == locations - texts
    assert part.count(f'add_header Content-Security-Policy "{text_csp}" always;') == texts
    assert part.count("add_header Content-Security-Policy") == locations + 1  # + the descriptor's
    assert part.count(f'add_header Permissions-Policy "{permissions}" always;') == locations
    assert part.count("add_header Permissions-Policy") == locations
    assert f'"format":"{descriptor_format}"' in part


def test_a_newer_tool_restores_an_older_release_with_its_own_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed upgrade run by a later atlas-deploy whose dashboard policy differs restores
    the older release: verified against the policy that release records, and its site sent
    with its own headers and descriptor (M8-03). The restoration used to fail its dashboard
    check, so the service was stopped and disabled."""
    deploy, _ = private_deployment(tmp_path)
    deploy.verifier = as_each_release_serves({name(B)})
    make_dashboard_release(tmp_path, commit=A)
    assert deploy.activate(name(A))["outcome"] == ACTIVATED
    newer_tool(monkeypatch)
    make_dashboard_release(tmp_path, commit=B)  # built by the later tool; it does not verify
    record = deploy.activate(name(B))
    assert record["outcome"] == ROLLED_BACK, record
    assert deploy.current() == name(A)
    assert_site_serves(tmp_path, name(A), DASHBOARD_CSP, PERMISSIONS, DESCRIPTOR_FORMAT)


def test_an_explicit_rollback_by_a_newer_tool_serves_the_older_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy, _ = private_deployment(tmp_path)
    deploy.verifier = as_each_release_serves()
    make_dashboard_release(tmp_path, commit=A)
    assert deploy.activate(name(A))["outcome"] == ACTIVATED
    newer_tool(monkeypatch)
    make_dashboard_release(tmp_path, commit=B)
    assert deploy.activate(name(B))["outcome"] == ACTIVATED
    assert_site_serves(
        tmp_path, name(B), NEWER_CSP, NEWER_PERMISSIONS, NEWER_FORMAT, NEWER_TEXT_CSP
    )
    record = deploy.rollback()
    assert record["outcome"] == ACTIVATED, record
    assert deploy.current() == name(A)
    assert_site_serves(tmp_path, name(A), DASHBOARD_CSP, PERMISSIONS, DESCRIPTOR_FORMAT)


def rewrite_dashboard_record(release: Path, **changes: object) -> dict[str, Any]:
    """Change the dashboard record in ``release``'s manifest (``None`` removes a key)."""
    path = release / "release.json"
    manifest = json.loads(path.read_text("utf-8"))
    for key, value in changes.items():
        if value is None:
            manifest["dashboard"].pop(key, None)
        else:
            manifest["dashboard"][key] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    record: dict[str, Any] = manifest["dashboard"]
    return record


def test_a_release_that_records_only_its_csp_keeps_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Releases built before 1.0.0 (M7) record only their Content-Security-Policy: a later
    tool serves and verifies them with it (their other headers and descriptors are the
    tool's, unchanged since M7). Their code sent it with every file, so a text file would
    get it too (they have none): never the later tool's policy for text."""
    release = make_dashboard_release(tmp_path)
    record = rewrite_dashboard_record(
        release, headers=None, descriptor_format=None, descriptors=None, text_csp=None
    )
    newer_tool(monkeypatch, new_format=False)
    site = Renderer.for_release(load_config(DEPLOY_ENV_PRIVATE), Layout(), release).nginx()
    policy = f'Content-Security-Policy "{DASHBOARD_CSP}" always;'
    assert dashboard_part(site).count(policy) == static_locations(release / DIST)
    assert policy in location(dashboard_part(site), "/inter-licence.txt")
    assert "upgrade-insecure-requests" not in site
    assert "'unsafe-inline'" not in site  # the policy for text is not this release's
    assert "fullscreen" in site  # its other headers are the tool's
    verification = Verification()
    _dashboard(verification, good_page(), good_descriptor(), record, None)
    assert verification.ok, verification.render()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("headers", {"X-Test": 'x" always;\n    return 200 "y'}),
        ("headers", {"X Test": "x"}),
        ("headers", ["Content-Security-Policy"]),
        ("csp", "default-src 'none'; img-src $host"),
        ("descriptors", {"private": "{}'; return 200 '{}", "public-demo": "{}"}),
        ("descriptors", {"public-demo": "{}"}),
    ],
)
def test_a_recorded_policy_that_could_alter_the_site_is_refused(
    tmp_path: Path, key: str, value: object
) -> None:
    """What a release records is written into its nginx site: only plain header names and
    values, and compact JSON descriptors, are accepted."""
    release = make_dashboard_release(tmp_path)
    rewrite_dashboard_record(release, **{key: value})
    with pytest.raises(RenderError, match="dashboard"):
        Renderer.for_release(load_config(DEPLOY_ENV_PRIVATE), Layout(), release).nginx()


def _script(source: str) -> Any:
    return pytest.param(
        "csp",
        DASHBOARD_CSP.replace("script-src 'self'", f"script-src 'self' {source}"),
        id=f"script-src {source}",
    )


#: Records a later tool must not send, though every character is one nginx embeds safely
#: (M8 final review): headers the dashboard does not send, or that a dashboard location
#: sets itself, and policies below the floor.
BELOW_THE_FLOOR = [
    pytest.param("headers", {**DASHBOARD_HEADERS, "Set-Cookie": "id=1; Path=/"}, id="cookie"),
    pytest.param("headers", {**DASHBOARD_HEADERS, "Access-Control-Allow-Origin": "*"}, id="cors"),
    pytest.param(
        "headers", {**DASHBOARD_HEADERS, "Strict-Transport-Security": "max-age=0"}, id="hsts"
    ),
    pytest.param("headers", {**DASHBOARD_HEADERS, "Cache-Control": "public"}, id="cache"),
    pytest.param(
        "headers",
        {**DASHBOARD_HEADERS, "content-security-policy": "default-src *"},
        id="a second policy",
    ),
    pytest.param(
        "headers", {**DASHBOARD_HEADERS, "PERMISSIONS-POLICY": "camera=*"}, id="named twice"
    ),
    pytest.param(
        "csp", DASHBOARD_CSP.replace("default-src 'none'", "default-src *"), id="default-src"
    ),
    pytest.param(
        "csp",
        DASHBOARD_CSP.replace("default-src 'none'", "default-src 'none' https:"),
        id="default-src 'none' and more",
    ),
    pytest.param(
        "csp",
        DASHBOARD_CSP.replace("frame-ancestors 'none'", "frame-ancestors *"),
        id="frame-ancestors",
    ),
    pytest.param("csp", DASHBOARD_CSP.replace("script-src 'self'; ", ""), id="no script-src"),
    *(
        _script(source)
        for source in (
            "'unsafe-inline'",
            "'unsafe-eval'",
            "'UNSAFE-INLINE'",
            "*",
            "https:",
            "data:",
            "cdn.example.org",
            "https://cdn.example.org",
            "'nonce-abcdef'",
            "'strict-dynamic'",
        )
    ),
    pytest.param("csp", DASHBOARD_CSP + "; script-src-elem https:", id="script-src-elem"),
    pytest.param("csp", DASHBOARD_CSP + "; worker-src *", id="worker-src"),
    pytest.param(
        "csp",
        DASHBOARD_CSP.replace("require-trusted-types-for 'script'; ", ""),
        id="no trusted types",
    ),
    pytest.param("csp", "script-src *; " + DASHBOARD_CSP, id="script-src twice"),
    pytest.param("csp", DASHBOARD_CSP + ", script-src *", id="two policies"),
    pytest.param("text_csp", DASHBOARD_TEXT_CSP + "; script-src 'self'", id="text script-src"),
    pytest.param(
        "text_csp",
        DASHBOARD_TEXT_CSP.replace("default-src 'none'", "default-src 'self'"),
        id="text default-src",
    ),
    pytest.param(
        "text_csp",
        DASHBOARD_TEXT_CSP.replace("frame-ancestors 'none'", "frame-ancestors *"),
        id="text frame-ancestors",
    ),
]


@pytest.mark.parametrize(("key", "value"), BELOW_THE_FLOOR)
def test_a_recorded_policy_below_the_floor_is_refused(
    tmp_path: Path, key: str, value: object
) -> None:
    """Since M8-03 a later tool sends and verifies the policy a release records, not its
    own: the record must meet the floor M7 enforced. Refused by the release check (before
    anything changes, in every profile), by rendering, and by verification even when the
    API sends what the record says."""
    release = make_dashboard_release(tmp_path)
    record = rewrite_dashboard_record(release, **{key: value})
    assert any("recorded dashboard policy refused" in p for p in check_release(release))
    with pytest.raises(RenderError, match="dashboard policy is refused"):
        Renderer.for_release(load_config(DEPLOY_ENV_PRIVATE), Layout(), release).nginx()
    page = answer(
        200, (BUILD / "index.html").read_bytes(), {"content-security-policy": record["csp"]}
    )
    notice = "inter-licence.txt"
    text = answer(
        200, (BUILD / notice).read_bytes(), {"content-security-policy": record["text_csp"]}
    )
    verification = Verification()
    _dashboard(verification, page, good_descriptor(), record, None, text=text)
    assert [c.name for c in verification.failures] == ["dashboard"]
    assert "recorded policy refused" in verification.failures[0].detail


@pytest.mark.parametrize("header", ["Content-Security-Policy", "content-security-policy"])
def test_a_policy_among_the_recorded_headers_is_replaced_by_the_recorded_one(
    tmp_path: Path, header: str
) -> None:
    """Each location sends one Content-Security-Policy, the one the release records (``csp``,
    or ``text_csp`` for text), whatever the case or value of one among its headers."""
    release = make_dashboard_release(tmp_path)
    others = {k: v for k, v in DASHBOARD_HEADERS.items() if k != "Content-Security-Policy"}
    rewrite_dashboard_record(release, headers={**others, header: "default-src *"})
    site = Renderer.for_release(load_config(DEPLOY_ENV_PRIVATE), Layout(), release).nginx()
    part = dashboard_part(site)
    assert "default-src *" not in site
    assert part.lower().count("add_header content-security-policy") == static_locations() + 1
    assert part.count(f'"{DASHBOARD_CSP}"') == static_locations() - text_locations()
    assert part.count(f'"{DASHBOARD_TEXT_CSP}"') == text_locations()


#: The Content-Security-Policy 0.7.0 (M7) records, as src/atlas/dashboard.py had it at
#: 194fa4a (the last commit of M7); 0.7.0 records nothing else of its policy.
POLICY_0_7_0 = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; manifest-src 'self'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'; object-src 'none'; "
    "require-trusted-types-for 'script'; trusted-types 'none'"
)


def test_every_release_so_far_meets_the_floor(tmp_path: Path) -> None:
    """0.7.0's record and 1.0.0's are served as before; this tool's own policies meet the
    floor, and neither of them meets the other's (scripts are allowed from the origin in
    HTML only)."""
    assert recorded_policy_problems({"csp": POLICY_0_7_0}) == []
    assert DashboardPolicy.of({"csp": POLICY_0_7_0}).text_csp == POLICY_0_7_0
    record = load_manifest(make_dashboard_release(tmp_path))["dashboard"]  # as 1.0.0 writes it
    assert recorded_policy_problems(record) == []
    assert DashboardPolicy.of(record).text_csp == DASHBOARD_TEXT_CSP
    assert policy_problems(DASHBOARD_CSP) == []
    assert policy_problems(DASHBOARD_TEXT_CSP, text=True) == []
    assert policy_problems(DASHBOARD_CSP, text=True) == ["allows scripts (script-src 'self')"]
    assert "has no script-src" in policy_problems(DASHBOARD_TEXT_CSP)
    # The headers a release may record never include one a location sets itself.
    assert not {name.lower() for name in DASHBOARD_HEADERS} & FIXED_DASHBOARD_HEADERS
    fixed = " ".join(dashboard_headers(0, 1, "no-cache", ())).lower()
    assert all(f"add_header {name} " in fixed for name in FIXED_DASHBOARD_HEADERS)


def test_a_release_with_an_altered_dashboard_is_refused_before_anything_changes(
    tmp_path: Path,
) -> None:
    deploy, host = private_deployment(tmp_path)
    release = make_dashboard_release(tmp_path, commit=A)
    index = release / DIST / "index.html"
    index.chmod(0o644)
    index.write_text("altered", encoding="utf-8")
    record = deploy.activate(name(A))
    assert record["outcome"] == REFUSED
    assert "restart" not in " ".join(host.calls)
    assert not deploy.layout.local(deploy.layout.dashboard_root).exists()


def test_pruning_removes_the_dashboards_of_pruned_releases(tmp_path: Path) -> None:
    deploy, _ = private_deployment(tmp_path)
    commits = ["1" * 40, "2" * 40, "3" * 40, "4" * 40]
    for index, commit in enumerate(commits):
        make_dashboard_release(tmp_path, commit=commit)
        manifest_path = tmp_path / "opt/atlas/releases" / name(commit) / "release.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        manifest["build"]["built_at"] = f"2026-10-1{index}T00:00:00+00:00"
        manifest_path.chmod(0o644)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        assert deploy.activate(name(commit))["outcome"] == ACTIVATED
    removed = deploy.prune(keep=2)
    assert f"dashboard/{name(commits[0])}" in removed
    left = sorted(p.name for p in deploy.layout.local(deploy.layout.dashboard_root).iterdir())
    assert name(commits[0]) not in left
    assert name(commits[-1]) in left


def test_status_reports_the_dashboard(tmp_path: Path) -> None:
    deploy, _ = private_deployment(tmp_path)
    make_dashboard_release(tmp_path, commit=A)
    deploy.activate(name(A))
    status = deploy.status()
    assert status["manifest"]["dashboard"]["fingerprint"] == load_build(BUILD).fingerprint()


def test_the_verification_target_names_the_published_copy(tmp_path: Path) -> None:
    deploy, _ = private_deployment(tmp_path)
    make_dashboard_release(tmp_path, commit=A)
    target = deploy.target(name(A), deploy.config())
    assert target.dashboard_dir == deploy.layout.local(deploy.layout.dashboard(name(A)))


# --- Verification -------------------------------------------------------------------------------


def answer(status: int, body: bytes, headers: dict[str, str] | None = None) -> Response:
    return Response(status, body, headers or {}, 1.0)


def record() -> dict[str, Any]:
    from atlas.dashboard import manifest_record

    return manifest_record(BUILD)


def good_page() -> Response:
    return answer(
        200, (BUILD / "index.html").read_bytes(), {"content-security-policy": DASHBOARD_CSP}
    )


def good_descriptor() -> Response:
    return answer(200, json.dumps({"format": "atlas-deployment/1"}).encode())


def test_verification_accepts_the_served_dashboard(tmp_path: Path) -> None:
    verification = Verification()
    _dashboard(verification, good_page(), good_descriptor(), record(), None)
    assert verification.ok, verification.render()


@pytest.mark.parametrize(
    ("page", "descriptor", "problem"),
    [
        (None, good_descriptor(), "/ did not answer 200"),
        (
            answer(200, b"<html>other</html>", {"content-security-policy": DASHBOARD_CSP}),
            good_descriptor(),
            "not the release's index.html",
        ),
        (
            answer(200, (BUILD / "index.html").read_bytes()),
            good_descriptor(),
            "Content-Security-Policy",
        ),
        (good_page(), answer(404, b"{}"), "did not answer the descriptor"),
        (good_page(), answer(200, b"not json"), "did not answer the descriptor"),
    ],
)
def test_verification_names_what_is_wrong(
    page: Response | None, descriptor: Response, problem: str
) -> None:
    verification = Verification()
    _dashboard(verification, page, descriptor, record(), None)
    assert not verification.ok
    assert problem in verification.failures[0].detail


@pytest.mark.parametrize(
    "permissive",
    [
        DASHBOARD_CSP.replace("script-src 'self'", "script-src 'self' 'unsafe-inline' https:"),
        DASHBOARD_CSP.replace("default-src 'none'", "default-src *"),
        DASHBOARD_CSP.replace("frame-ancestors 'none'", "frame-ancestors *"),
    ],
)
def test_verification_refuses_a_permissive_policy_its_record_agrees_with(
    permissive: str,
) -> None:
    """Verification compares the API's policy with the release's record (M8-03): a release
    whose code and record agree on a weaker policy than the floor (which M7's check held
    every release to, as this tool's own policy) is refused all the same."""
    page = answer(200, (BUILD / "index.html").read_bytes(), {"content-security-policy": permissive})
    verification = Verification()
    _dashboard(verification, page, good_descriptor(), record() | {"csp": permissive}, None)
    assert not verification.ok
    detail = verification.failures[0].detail
    assert "recorded policy refused: Content-Security-Policy:" in detail
    assert "lacks the dashboard's Content-Security-Policy" not in detail  # the API agrees


def text_record() -> dict[str, Any]:
    """The dashboard record of a release with a policy for its text files (from 1.0.0)."""
    return record() | {"text_csp": DASHBOARD_TEXT_CSP}


def notice(policy: str | None = DASHBOARD_TEXT_CSP, body: bytes | None = None) -> Response:
    """The API's answer to the first licence notice, with ``policy``."""
    content = (BUILD / "inter-licence.txt").read_bytes() if body is None else body
    return answer(200, content, {} if policy is None else {"content-security-policy": policy})


def test_verification_checks_the_policy_of_the_text_files() -> None:
    assert _text_file(record()) is None  # a release before 1.0.0: nothing to check
    assert _text_file(text_record()) == "inter-licence.txt"
    verification = Verification()
    _dashboard(verification, good_page(), good_descriptor(), text_record(), None, text=notice())
    assert verification.ok, verification.render()


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        (None, "/inter-licence.txt did not answer 200"),
        (answer(404, b"{}"), "/inter-licence.txt did not answer 200"),
        (notice(DASHBOARD_CSP), "lacks the policy of the dashboard's text files"),
        (notice(None), "lacks the policy of the dashboard's text files"),
        (notice(body=b"altered"), "is not the release's inter-licence.txt"),
    ],
)
def test_verification_names_what_is_wrong_with_a_text_file(
    text: Response | None, problem: str
) -> None:
    verification = Verification()
    _dashboard(verification, good_page(), good_descriptor(), text_record(), None, text=text)
    assert not verification.ok
    assert problem in verification.failures[0].detail


def test_verify_requests_a_licence_notice_where_the_text_policy_is_recorded() -> None:
    from atlas.deploy.verify import verify

    seen: list[str] = []

    def requester(url: str, **_: Any) -> Response | None:
        seen.append(url)
        if url.endswith("/health/live"):
            return answer(200, b"{}")
        if url.endswith("/inter-licence.txt"):
            return notice()
        if url.endswith("/"):
            return good_page()
        if url.endswith(DESCRIPTOR_PATH):
            return good_descriptor()
        return None

    manifest = {"atlas_version": "1.0.0", "dashboard": text_record()}
    result = verify(
        Target(manifest=manifest, smoke_simulation=False), requester=requester, timeout=1
    )
    assert any(c.name == "dashboard" and c.ok for c in result.checks), result.render()
    assert "http://127.0.0.1:8000/inter-licence.txt" in seen


def test_verification_checks_the_published_copy(tmp_path: Path) -> None:
    release = make_dashboard_release(tmp_path)
    layout = Layout(root=tmp_path)
    target = layout.local(
        publish(
            layout,
            release.name,
            release,
            load_build(release / DIST),
            owner=release.stat().st_uid,
            chown=False,
        )
    )
    verification = Verification()
    _dashboard(verification, good_page(), good_descriptor(), record(), target)
    assert verification.ok, verification.render()
    (target / "favicon.svg").unlink()
    # A link is never part of the copy, a link to a directory included.
    (target / "assets" / "linked").symlink_to(tmp_path)
    verification = Verification()
    _dashboard(verification, good_page(), good_descriptor(), record(), target)
    assert [c.name for c in verification.failures] == ["published dashboard"]
    assert "favicon.svg" in verification.failures[0].detail
    assert "assets/linked" in verification.failures[0].detail


def test_verify_runs_the_dashboard_check_for_a_release_that_has_one() -> None:
    from atlas.deploy.verify import verify

    manifest = {"atlas_version": "0.7.0", "dashboard": record()}
    seen: list[str] = []

    def requester(url: str, **_: Any) -> Response | None:
        seen.append(url)
        if url.endswith("/health/live"):
            return answer(200, b"{}")
        if url.endswith("/"):
            return good_page()
        if url.endswith(DESCRIPTOR_PATH):
            return good_descriptor()
        return None

    result = verify(
        Target(manifest=manifest, smoke_simulation=False), requester=requester, timeout=1
    )
    assert any(c.name == "dashboard" and c.ok for c in result.checks), result.render()
    assert "http://127.0.0.1:8000/" in seen


def test_the_digest_of_the_recorded_index_is_the_built_one() -> None:
    assert (
        record()["files"]["index.html"]
        == hashlib.sha256((BUILD / "index.html").read_bytes()).hexdigest()
    )


def render_apply(root: Path, release_dir: Path, stage: str = "tls") -> tuple[int, str]:
    """``atlas-deploy render --apply`` in the rehearsal ``root``: its exit code and output."""
    from atlas.deploy import cli

    out = io.StringIO()
    command = ["--root", str(root), "render", "--release-dir", str(release_dir)]
    return cli.main([*command, "--stage", stage, "--apply"], stdout=out), out.getvalue()


@pytest.mark.parametrize("through_current", [False, True])
def test_render_apply_publishes_the_dashboard_its_site_serves(
    tmp_path: Path, through_current: bool
) -> None:
    """`atlas-deploy render --apply` of an exposed site publishes (and verifies) the
    release's dashboard first, so the site never points at a missing or unverified copy;
    /opt/atlas/current names its release."""
    from tests.test_m6_scripts import rehearsal

    root = rehearsal(tmp_path)
    release = make_dashboard_release(root)
    (root / "opt/atlas/current").symlink_to(release)
    code, output = render_apply(root, root / "opt/atlas/current" if through_current else release)
    assert code == 0, output
    published = root / "opt/atlas/dashboard" / release.name
    build = load_build(release / DIST)
    uid = release.stat().st_uid
    assert check_published(published, build, owner=uid, proxy_gid=None) == []
    site = (root / "etc/nginx/sites-available/atlas.conf").read_text("utf-8")
    assert f"root /opt/atlas/dashboard/{release.name};" in site


def test_render_apply_publishes_only_an_installed_release_as_recorded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A directory named like a release elsewhere (a checkout, a copy) is refused, and so
    is an installed release whose build was rebuilt: what is published is what
    release.json records, as `activate` checks it. Nothing is published or written."""
    from tests.test_m6_scripts import rehearsal

    root = rehearsal(tmp_path)
    elsewhere = make_dashboard_release(tmp_path / "srv")
    assert render_apply(root, elsewhere)[0] == 2
    assert "is not an installed release" in capsys.readouterr().err
    release = make_dashboard_release(root)
    (root / "opt/atlas/current").symlink_to(release)
    dist = release / DIST
    (dist / "index.html").write_text("<!doctype html><p>rebuilt</p>\n", encoding="utf-8")
    data = (dist / "index.html").read_bytes()
    document = json.loads((dist / MANIFEST_NAME).read_text("utf-8"))
    document["files"]["index.html"] = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }
    (dist / MANIFEST_NAME).write_text(json.dumps(document), encoding="utf-8")
    assert render_apply(root, release)[0] == 2
    assert "differs from the manifest" in capsys.readouterr().err
    assert not (root / "opt/atlas/dashboard").exists()
    assert not (root / "etc/nginx/sites-available/atlas.conf").exists()


def test_render_apply_installs_the_current_release_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`render --apply` rewrites the site nginx serves next to the running API: only the
    current release's, under the deployment lock, and recorded in the history. Another
    release is refused (activate it instead), so nginx is never made to serve a dashboard
    and routes that the API does not run (M8-26)."""
    from tests.test_m6_scripts import rehearsal

    root = rehearsal(tmp_path)
    current = make_dashboard_release(root, commit=A)
    other = make_dashboard_release(root, commit=B)
    (root / "opt/atlas/current").symlink_to(f"releases/{current.name}")
    site = root / "etc/nginx/sites-available/atlas.conf"
    history = root / "var/lib/atlas/deploy/history.jsonl"
    assert render_apply(root, other)[0] == 2
    error = capsys.readouterr().err
    assert "is not the current release" in error
    assert f"atlas-deploy activate {other.name}" in error
    assert not site.exists()
    assert not (root / "opt/atlas/dashboard").exists()
    assert not history.exists()
    code, output = render_apply(root, current)
    assert code == 0, output
    assert dashboard_root(site.read_text("utf-8")).endswith(f"/opt/atlas/dashboard/{current.name}")
    last = json.loads(history.read_text("utf-8").splitlines()[-1])
    assert (last["action"], last["outcome"], last["release"]) == ("render", "applied", current.name)
    # Not while another deployment runs, nor while an activation is unfinished.
    layout = Layout(root=root)
    deploy = Deployment(layout, FakeHost(layout), verifier(set()))
    with deploy.locked():
        assert render_apply(root, current)[0] == 2
    assert "another deployment holds the lock" in capsys.readouterr().err
    deploy.record("activate", IN_PROGRESS, op="op-x", release=other.name, previous=current.name)
    assert render_apply(root, current)[0] == 2
    assert "atlas-deploy recover" in capsys.readouterr().err


def test_render_apply_installs_the_acme_site_before_the_first_activation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """On a new server (no release current) in an exposed profile, the first activation
    needs the certificate that the ACME site obtains: `render --stage acme --apply` of an
    installed release is accepted then (that site serves only ACME challenges, nothing
    from any release), locked and recorded. The complete site is not: it is activated
    (M8-26)."""
    from tests.test_m6_scripts import rehearsal

    root = rehearsal(tmp_path)
    release = make_dashboard_release(root, commit=A)
    site = root / "etc/nginx/sites-available/atlas.conf"
    history = root / "var/lib/atlas/deploy/history.jsonl"
    code, output = render_apply(root, release, "acme")
    assert code == 0, output
    acme = site.read_text("utf-8")
    assert "location ^~ /.well-known/acme-challenge/" in acme
    assert "/opt/atlas/dashboard" not in acme
    assert "proxy_pass" not in acme
    assert not (root / "opt/atlas/dashboard").exists()
    records = [json.loads(line) for line in history.read_text("utf-8").splitlines()]
    assert [(r["action"], r["release"], r["stage"]) for r in records] == [
        ("render", release.name, "acme")
    ]
    assert render_apply(root, release)[0] == 2
    error = capsys.readouterr().err
    assert "no release is active" in error
    assert "--stage acme" in error
    assert f"atlas-deploy activate {release.name}" in error
    assert site.read_text("utf-8") == acme
    assert not (root / "opt/atlas/dashboard").exists()
    assert len(history.read_text("utf-8").splitlines()) == 1
    # Once a release is current, only its own files are rendered, at either stage.
    other = make_dashboard_release(root, commit=B)
    (root / "opt/atlas/current").symlink_to(f"releases/{release.name}")
    assert render_apply(root, other, "acme")[0] == 2
    assert "is not the current release" in capsys.readouterr().err
    assert render_apply(root, release, "acme")[0] == 0


@pytest.mark.parametrize("command", ["render", "validate", "plan-upgrade"])
def test_a_malformed_dashboard_manifest_is_an_error_not_a_crash(
    tmp_path: Path, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A release whose build file is damaged cannot be rendered: an error naming the release
    (exit 2), never an internal error (exit 3)."""
    from atlas.deploy import cli
    from tests.test_m6_scripts import rehearsal

    root = rehearsal(tmp_path)
    release = make_dashboard_release(root)
    (release / DIST / MANIFEST_NAME).write_text('{"format":"atlas-dashboard/1"}', "utf-8")
    target = [release.name] if command == "plan-upgrade" else ["--release-dir", str(release)]
    assert cli.main(["--root", str(root), command, *target], stdout=io.StringIO()) == 2
    error = capsys.readouterr().err
    assert f"{release}: the dashboard build is invalid: atlas-dashboard.json must" in error
    assert "internal error" not in error
