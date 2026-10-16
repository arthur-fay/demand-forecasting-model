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
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from atlas.dashboard import DASHBOARD_CSP, DESCRIPTOR_PATH, DIST, MANIFEST_NAME, load_build
from atlas.deploy.layout import Layout
from atlas.deploy.manifest import check_release, load_manifest
from atlas.deploy.publish import PublishError, check_published, prune_published, publish
from atlas.deploy.releases import ACTIVATED, REFUSED, ROLLED_BACK, compare_manifests
from atlas.deploy.render import Renderer
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
from atlas.deploy.verify import Response, Target, Verification, _dashboard
from tests.conftest import REPO_ROOT
from tests.m6_helpers import DEPLOY_ENV_PRIVATE, Nginx, make_release, need_tools, write
from tests.m7_helpers import BUILD, make_dashboard_release
from tests.test_m6_releases import A, B, deployment, name

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
    # The policy of the dashboard on its pages and files, the API's on the API and on the
    # descriptor (JSON).
    assert part.count(f'add_header Content-Security-Policy "{DASHBOARD_CSP}" always;') == 2 + n
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
    assert check_release(release) == []


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


def site_root(tmp_path: Path) -> str:
    site = (tmp_path / "etc/nginx/sites-available/atlas.conf").read_text("utf-8")
    return next(
        line.split()[1].rstrip(";")
        for line in site.splitlines()
        if " root /" in line and "dashboard" in line
    )


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
    assert any(
        "dashboard 0.7.0 published" in line or "published in" in line for line in record["detail"]
    )
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


def render_apply(root: Path, release_dir: Path) -> tuple[int, str]:
    """``atlas-deploy render --apply`` in the rehearsal ``root``: its exit code and output."""
    from atlas.deploy import cli

    out = io.StringIO()
    command = ["--root", str(root), "render", "--release-dir", str(release_dir), "--apply"]
    return cli.main(command, stdout=out), out.getvalue()


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
    if through_current:
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
