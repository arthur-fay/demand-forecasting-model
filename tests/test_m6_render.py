"""Rendered deployment files: the systemd units, the runtime environment and the nginx site.

The units are checked by ``systemd-analyze verify`` and ``systemd-analyze security``; the
nginx site by ``nginx -t`` for every profile and stage. Structural tests pin the security
properties (loopback bind, no secret, sandbox, restart policy, route allow-list).
"""

from __future__ import annotations

import configparser
import json
import os
import re
from pathlib import Path, PurePosixPath

import pytest

from atlas.deploy.layout import Layout
from atlas.deploy.render import (
    Rendered,
    Renderer,
    RenderError,
    disable_site,
    enable_site,
    fill,
    safe,
    write_files,
)
from atlas.deploy.routes import (
    COMPUTE,
    LIVENESS,
    READ,
    READINESS,
    RouteError,
    load_routes,
    routed,
    routes_from_openapi,
)
from atlas.deploy.siteconfig import load_config
from tests.conftest import REPO_ROOT
from tests.m6_helpers import DEPLOY_ENV_PRIVATE, Nginx, make_tls, need_tools, run, write

PUBLIC = (
    "ATLAS_DEPLOY_PROFILE=public-demo\nATLAS_DEPLOY_DOMAIN=atlas.example.org\n"
    "ATLAS_DEPLOY_IPV6=false\nATLAS_DEPLOY_SECURITY_REVIEW=review-2026-11-01\n"
)


def renderer(text: str = DEPLOY_ENV_PRIVATE, layout: Layout | None = None) -> Renderer:
    layout = layout or Layout()
    return Renderer.for_release(load_config(text, layout), layout, REPO_ROOT)


def unit(content: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None, delimiters=("=",))
    parser.optionxform = str  # type: ignore[assignment, method-assign]
    parser.read_string(content)
    return parser


def lines(content: str, key: str) -> list[str]:
    return [line.split("=", 1)[1] for line in content.splitlines() if line.startswith(key + "=")]


# --- Templates ----------------------------------------------------------------------------------


def test_fill_refuses_missing_values_and_unsafe_ones() -> None:
    assert fill("a {{x}} b", {"x": "1"}) == "a 1 b"
    with pytest.raises(RenderError, match="no value"):
        fill("{{missing}}", {})
    for value in ("a b", "a;b", 'a"b', "a{b", "$host", "a\nb", "a'b"):
        with pytest.raises(RenderError):
            safe("value", value)
    assert safe("path", "/etc/letsencrypt/live/a.example.org/fullchain.pem")


def test_rendering_is_deterministic() -> None:
    first = [(f.path, f.content) for f in renderer().files()]
    second = [(f.path, f.content) for f in renderer().files()]
    assert first == second
    assert not any("{{" in content for _, content in first)


def test_the_tunnel_profile_renders_no_nginx_site() -> None:
    files = renderer("").files()
    assert all(not str(f.path).startswith("/etc/nginx") for f in files)
    with pytest.raises(RenderError):
        renderer("").nginx()


# --- The systemd unit -----------------------------------------------------------------------------


def test_the_service_unit_is_least_privilege_and_loopback_only() -> None:
    content = renderer().unit("atlas.service.in")
    service = unit(content)["Service"]
    assert service["User"] == "atlas"
    assert service["Group"] == "atlas"
    assert service["ExecStart"] == (
        "/opt/atlas/current/.venv/bin/atlas api serve --host 127.0.0.1 --port 8000 "
        "--uds /run/atlas/api.sock"
    )
    # nginx's way in: the service's own runtime directory, which only systemd creates.
    assert service["RuntimeDirectory"] == "atlas"
    assert service["RuntimeDirectoryMode"] == "0755"
    assert service["Type"] == "notify"
    assert service["NotifyAccess"] == "main"
    assert service["LoadCredential"] == "api-key:/etc/atlas/credentials/api-key"
    assert "ATLAS_API_KEY_FILE=%d/api-key" in lines(content, "Environment")
    for name, value in {
        "NoNewPrivileges": "yes",
        "ProtectSystem": "strict",
        "ProtectHome": "yes",
        "PrivateTmp": "yes",
        "PrivateDevices": "yes",
        "PrivateUsers": "yes",
        "CapabilityBoundingSet": "",
        "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
        "MemoryDenyWriteExecute": "yes",
        "IPAddressDeny": "any",
        "IPAddressAllow": "localhost",
        "UMask": "0077",
        "LimitCORE": "0",
    }.items():
        assert service[name] == value, name
    assert "/etc/atlas/credentials" in service["InaccessiblePaths"]


def test_the_restart_policy_cannot_loop() -> None:
    content = renderer().unit("atlas.service.in")
    parsed = unit(content)
    service = parsed["Service"]
    assert service["Restart"] == "on-failure"
    assert service["RestartPreventExitStatus"] == "2"  # an invalid configuration
    assert service["RestartMaxDelaySec"] == "60s"
    assert parsed["Unit"]["StartLimitBurst"] == "6"
    assert parsed["Unit"]["StartLimitIntervalSec"] == "900"
    assert service["WatchdogSec"] == "30"
    assert service["KillMode"] == "mixed"
    assert service["OOMPolicy"] == "continue"
    assert parsed["Install"]["WantedBy"] == "multi-user.target"


@pytest.mark.parametrize(
    ("resources", "workers", "maximum"), [("low", 1, "512M"), ("standard", 2, "768M")]
)
def test_resource_limits_follow_the_profile(resources: str, workers: int, maximum: str) -> None:
    text = DEPLOY_ENV_PRIVATE.replace("RESOURCES=standard", f"RESOURCES={resources}")
    rendered = renderer(text)
    service = unit(rendered.unit("atlas.service.in"))["Service"]
    assert service["MemoryMax"] == maximum
    assert int(service["TasksMax"]) >= 96
    assert f"ATLAS_API_MAX_CONCURRENT_SIMULATIONS={workers}" in rendered.runtime_env()


def test_no_secret_is_rendered(tmp_path: Path) -> None:
    for item in renderer().files():
        assert not re.search(r"[0-9a-f]{64}", item.content), item.path
        assert not re.search(r'X-API-Key "[^"]+"', item.content), item.path
        for line in item.content.splitlines():
            assert not line.startswith(("ATLAS_API_KEY=", "Environment=ATLAS_API_KEY=")), line


def test_the_monitor_runs_unprivileged() -> None:
    service = unit(renderer().unit("atlas-monitor.service.in"))
    assert service["Service"]["User"] == "atlas-monitor"
    assert service["Service"]["SupplementaryGroups"] == "atlas systemd-journal"
    assert "DynamicUser" not in service["Service"]
    assert service["Service"]["ExecStart"].endswith("--tls-domain atlas.example.org")
    assert service["Unit"]["OnFailure"] == "atlas-alert@%n.service"
    tunnel = unit(renderer("").unit("atlas-monitor.service.in"))
    assert "--tls-domain" not in tunnel["Service"]["ExecStart"]
    alert = unit(renderer().unit("atlas-alert@.service.in"))
    assert alert["Unit"]["ConditionFileIsExecutable"] == "/etc/atlas/alert-hook"


def _units_with_local_paths(tmp_path: Path) -> Path:
    """The units rendered for a layout under ``tmp_path`` (so every ExecStart exists)."""
    install = tmp_path / "opt-atlas"
    config = tmp_path / "etc-atlas"
    layout = Layout(install=PurePosixPath(str(install)), config=PurePosixPath(str(config)))
    for name in ("atlas", "atlas-deploy"):
        write(install / "current/.venv/bin" / name, "#!/bin/sh\n", 0o755)
    write(config / "alert-hook", "#!/bin/sh\n", 0o755)
    write(config / "atlas.env", "")
    out = tmp_path / "units"
    out.mkdir()
    for item in renderer(DEPLOY_ENV_PRIVATE, layout).files():
        if item.path.name.endswith((".service", ".timer")):
            write(out / item.path.name, item.content)
    return out


def test_systemd_accepts_the_units(tmp_path: Path) -> None:
    need_tools("systemd-analyze")
    units = _units_with_local_paths(tmp_path)
    names = [
        "atlas.service",
        "atlas-monitor.service",
        "atlas-monitor.timer",
        "atlas-alert@.service",
    ]
    result = run(["systemd-analyze", "verify", "--man=no", *names], cwd=units)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Unknown" not in result.stdout + result.stderr  # no misspelt or unsupported key


@pytest.mark.parametrize(
    ("name", "most"),
    [
        ("atlas.service", 1.5),  # 0.9 measured
        ("atlas-monitor.service", 1.5),  # 1.3 measured
        # The operator's alert hook keeps the network and the journal: 1.3 measured.
        ("atlas-alert@.service", 1.5),
    ],
)
def test_every_sandbox_scores_low(tmp_path: Path, name: str, most: float) -> None:
    need_tools("systemd-analyze")
    units = _units_with_local_paths(tmp_path)
    if name.endswith("@.service"):  # a template is scored through one of its instances
        instance = units / name.replace("@.", "@x.")
        instance.write_text((units / name).read_text())
        name = instance.name
    result = run(["systemd-analyze", "security", "--offline=yes", "--no-pager", str(units / name)])
    match = re.search(rf"Overall exposure level for {re.escape(name)}: ([0-9.]+)", result.stdout)
    if match is None:  # pragma: no cover - systemd older than 250 (no --offline)
        pytest.skip(result.stdout + result.stderr)
    assert float(match.group(1)) <= most, result.stdout


# --- Routes ------------------------------------------------------------------------------------


def test_routes_are_exactly_the_contract() -> None:
    document = json.loads((REPO_ROOT / "docs/api/openapi.json").read_text("utf-8"))
    routes = load_routes(REPO_ROOT)
    assert {(r.method, r.path) for r in routes} == {
        (method.upper(), path) for path, item in document["paths"].items() for method in item
    }
    access = {r.path: r.access for r in routes}
    assert access["/api/v1/simulations"] == COMPUTE
    assert access["/health/live"] == LIVENESS
    assert access["/health/ready"] == READINESS
    # Operational detail (workers, limits, component details): never public.
    assert access["/api/v1/system"] == READINESS
    assert {
        a
        for p, a in access.items()
        if p not in ("/api/v1/simulations", "/health/live", "/health/ready", "/api/v1/system")
    } == {READ}
    scenario = next(r for r in routes if "{" in r.path)
    assert scenario.location == '~ "^/api/v1/scenarios/[a-z][a-z0-9_]{0,63}$"'
    public = {r.path for r in routed(routes, "public-demo")}
    assert "/api/v1/simulations" not in public
    assert "/health/ready" not in public
    assert "/api/v1/system" not in public
    assert "/api/v1/forecasts" in public
    assert "/health/live" in public


@pytest.mark.parametrize(
    "document",
    [
        {"paths": {"/x": {"delete": {"x-atlas-cost": "static"}}}},
        {"paths": {"/x": {"get": {}}}},
        {"paths": {"/x": {"get": {"x-atlas-cost": "expensive"}}}},
        {"paths": {"/x y": {"get": {"x-atlas-cost": "precomputed"}}}},
        {"paths": {"/a/{id}": {"get": {"x-atlas-cost": "precomputed", "parameters": []}}}},
        {
            "paths": {
                "/a/{id}": {
                    "get": {
                        "x-atlas-cost": "precomputed",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "schema": {"pattern": '^.*"; deny all; "$'},
                            }
                        ],
                    }
                }
            }
        },
    ],
)
def test_contracts_that_cannot_be_routed_safely_are_refused(document: dict[str, object]) -> None:
    with pytest.raises(RouteError):
        routes_from_openapi(document)


# --- nginx


def test_the_private_site_routes_the_contract_behind_authentication() -> None:
    site = renderer().nginx()
    assert 'auth_basic "ATLAS private research deployment";' in site
    assert "auth_basic_user_file /etc/nginx/atlas/htpasswd;" in site
    # Every route, and the internal location answering unparsable requests with JSON.
    assert site.count("location = ") + site.count('location ~ "') == len(load_routes(REPO_ROOT)) + 1
    assert "    location = /.atlas-error-400 {\n        internal;" in site
    assert "location = /docs" not in site
    assert "openapi.json" not in site
    # The key is added in the simulation location only, and nowhere else.
    blocks = re.split(r"\n    # ", site)
    with_key = [b for b in blocks if "include /etc/nginx/atlas/simulation-key.conf;" in b]
    assert len(with_key) == 1
    assert with_key[0].startswith("POST /api/v1/simulations")
    # Every location sends an allow-list of headers (nginx does not inherit
    # proxy_set_header): no client header is passed on.
    for block in re.findall(r"location (?:= |~ )[^{]*\{(.*?)\n    \}", site, re.S):
        if "proxy_pass" in block:
            assert "proxy_pass_request_headers off;" in block
            for header in (
                "Host atlas.example.org",
                "X-Request-ID $request_id",
                "Content-Type $content_type",
                "Accept $http_accept",
            ):
                assert f"proxy_set_header {header};" in block
    assert "server unix:/run/atlas/api.sock;" in site
    assert "127.0.0.1:8000" not in site
    # Unknown paths are answered after authentication and the rate limits, not before.
    assert "location / {\n        root /nonexistent;\n        try_files" in site
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in site
    assert 'Strict-Transport-Security "max-age=31536000"' in site


def test_the_public_demo_site_routes_no_computation() -> None:
    site = renderer(PUBLIC).nginx()
    assert "auth_basic" not in site
    assert "/api/v1/simulations;" not in site.replace("/api/v1/simulations/parameters", "")
    assert "simulation-key.conf" not in site
    assert "location = /health/ready" not in site
    assert "location = /api/v1/system" not in site
    assert "review-2026-11-01" in site


def test_an_allow_list_denies_every_other_network() -> None:
    site = renderer(DEPLOY_ENV_PRIVATE + "ATLAS_DEPLOY_ALLOW_CIDRS=203.0.113.0/24\n").nginx()
    assert "allow 203.0.113.0/24;\n    deny all;" in site


def test_the_acme_stage_proxies_nothing() -> None:
    site = renderer().nginx("acme")
    assert "proxy_pass" not in site
    assert "ssl_certificate" not in site
    assert "/.well-known/acme-challenge/" in site
    with pytest.raises(RenderError):
        renderer().nginx("plain")


@pytest.mark.parametrize("profile", ["private", "public-demo"])
@pytest.mark.parametrize("stage", ["acme", "tls"])
def test_nginx_accepts_the_site(tmp_path: Path, profile: str, stage: str) -> None:
    need_tools("nginx", "openssl")
    _, cert, key = make_tls(tmp_path / "tls")
    text = (DEPLOY_ENV_PRIVATE if profile == "private" else PUBLIC) + (
        f"ATLAS_DEPLOY_TLS_CERT={cert}\nATLAS_DEPLOY_TLS_KEY={key}\n"
    )
    layout = Layout(
        nginx=PurePosixPath(str(tmp_path / "nginx-atlas")),
        acme=PurePosixPath(str(tmp_path / "acme")),
    )
    write(tmp_path / "nginx-atlas/simulation-key.conf", 'proxy_set_header X-API-Key "x";\n', 0o600)
    write(tmp_path / "nginx-atlas/htpasswd", "", 0o640)
    result = Nginx(tmp_path / "nginx", renderer(text, layout).nginx(stage)).test()
    assert result.returncode == 0, result.stderr


# --- Writing ------------------------------------------------------------------------------------


def test_writing_is_atomic_and_idempotent(tmp_path: Path) -> None:
    layout = Layout(root=tmp_path)
    files = renderer().files()
    first = write_files(files, layout, chown=False)
    assert all(result.changed for result in first)
    assert all(result.changed is False for result in write_files(files, layout, chown=False))
    env = tmp_path / "etc/atlas/atlas.env"
    assert oct(env.stat().st_mode & 0o777) == "0o644"
    env.write_text("tampered\n")
    again = write_files(files, layout, chown=False)
    assert [str(r.path) for r in again if r.changed] == ["/etc/atlas/atlas.env"]
    assert not list(tmp_path.rglob(".*.tmp*"))
    assert not [p for p in tmp_path.rglob(".*") if p.is_file()]


def test_a_failed_write_leaves_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = Layout(root=tmp_path)
    target = write(tmp_path / "etc/atlas/atlas.env", "old\n")

    def broken(*args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", broken)
    with pytest.raises(OSError, match="disk full"):
        write_files([Rendered(PurePosixPath("/etc/atlas/atlas.env"), "new\n")], layout, chown=False)
    assert target.read_text() == "old\n"
    assert [p.name for p in target.parent.iterdir()] == ["atlas.env"]


def test_enabling_the_site_is_idempotent(tmp_path: Path) -> None:
    layout = Layout(root=tmp_path)
    assert enable_site(layout) is True
    assert enable_site(layout) is False
    link = tmp_path / "etc/nginx/sites-enabled/atlas.conf"
    assert str(link.readlink()) == "/etc/nginx/sites-available/atlas.conf"
    write(tmp_path / "etc/nginx/sites-available/atlas.conf", "server {}\n")
    assert disable_site(layout) is True
    assert not link.is_symlink()
    assert not (tmp_path / "etc/nginx/sites-available/atlas.conf").exists()
    assert disable_site(layout) is False
