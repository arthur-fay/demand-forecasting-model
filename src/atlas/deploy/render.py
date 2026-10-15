"""Render the deployment files from the validated configuration (``atlas-deploy render``).

From ``/etc/atlas/deploy.env`` (:mod:`atlas.deploy.siteconfig`), the templates of the
release (``deploy/templates/``) and its committed OpenAPI contract
(:mod:`atlas.deploy.routes`) this produces, deterministically:

* ``/etc/atlas/atlas.env``: the API's runtime environment (no secret);
* ``atlas.service``, ``atlas-monitor.service``, ``atlas-monitor.timer`` and
  ``atlas-alert@.service`` for systemd;
* ``/etc/systemd/journald.conf.d/60-atlas.conf``: persistent, capped logs;
* ``/etc/nginx/sites-available/atlas.conf``, except for the tunnel profile. Its ``acme``
  stage serves only ACME challenges on port 80 (to obtain the first certificate); the
  ``tls`` stage (the default) is the complete site: the contract's routes and (M7) the
  dashboard's exact pages and files, read from the release's published copy
  (:mod:`atlas.deploy.publish`) with the dashboard's own Content-Security-Policy, and the
  deployment descriptor that tells the dashboard what this profile allows.

Templates use ``{{name}}`` placeholders. Every value inserted comes from the validated
configuration or from constants, and is checked against a character set without
whitespace, quotes, braces, semicolons or ``$``: no value can add a directive. An unknown
placeholder is an error, never an empty string.

Writing (:func:`write_files`) is atomic per file and idempotent: unchanged files are not
touched, and the result lists what changed, so the operator knows what to reload.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from atlas.dashboard import DASHBOARD_HEADERS, DashboardBuild, descriptor
from atlas.deploy.layout import (
    ALERT_SERVICE,
    API_HOST,
    MONITOR_SERVICE,
    MONITOR_TIMER,
    MONITOR_USER,
    SERVICE,
    SERVICE_GROUP,
    SERVICE_USER,
    Layout,
    valid_release_id,
)
from atlas.deploy.routes import (
    COMPUTE,
    DASHBOARD,
    PROFILE_ACCESS,
    READINESS,
    Route,
    StaticRoute,
    dashboard_build,
    dashboard_routes,
    load_routes,
    routed,
)
from atlas.deploy.siteconfig import DeployConfig, runtime_environment

TEMPLATES: Final = Path("deploy") / "templates"
STAGES: Final = ("acme", "tls")
_PLACEHOLDER: Final = re.compile(r"\{\{([a-z0-9_]+)\}\}")
_SAFE: Final = re.compile(r"[A-Za-z0-9._:/,@+=%\-\[\]]*")
NGINX_LOG_DIR: Final = "/var/log/nginx"
#: (M7) Per-client limit of the dashboard's static files, in their own zone so that loading
#: the dashboard (about ten files at once, more with several tabs) never uses up the API's
#: read limit. nginx applies limits before authentication, so this is also the rate at which
#: a client without an account can try passwords on these paths: twice the API's, no more.
#: Their connections still count against the per-client connection cap.
STATIC_RATE: Final = "20r/s"
STATIC_BURST: Final = 60
JOURNAL_DROPIN: Final = "60-atlas.conf"
JOURNAL_MAX_USE: Final = {"low": "200M", "standard": "500M"}

#: nginx's own errors, answered with the API's error body.
NGINX_ERRORS: Final = (
    ("400", "bad_request", "the request target is not accepted by the proxy"),
    ("401", "unauthorized", "authentication is required"),
    ("403", "forbidden", "access from this network is not allowed"),
    ("404", "not_found", "no such endpoint"),
    ("405", "method_not_allowed", "this method is not allowed on this endpoint"),
    ("413", "payload_too_large", "request bodies are limited to 16384 bytes"),
    ("429", "rate_limited", "too many requests from this client; retry shortly"),
)
UPSTREAM_ERROR: Final = ("upstream_unavailable", "the ATLAS API is not answering; retry shortly")


class RenderError(ValueError):
    """A template or value cannot be rendered safely."""


def fill(template: str, values: Mapping[str, str], *, name: str = "template") -> str:
    """Replace every ``{{key}}`` with ``values[key]`` (a missing key is an error)."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise RenderError(f"{name}: no value for {{{{{key}}}}}")
        return values[key]

    return _PLACEHOLDER.sub(replace, template)


def safe(name: str, value: str) -> str:
    """``value`` if it cannot change the structure of a configuration file."""
    if not _SAFE.fullmatch(value):
        raise RenderError(f"{name}={value!r}: unsafe characters for a configuration file")
    return value


@dataclass(frozen=True)
class Rendered:
    """One rendered file: its host path, content, mode and group (owner: root)."""

    path: PurePosixPath
    content: str
    mode: int = 0o644
    group: str = "root"


def security_headers(indent: int, hsts_max_age: int) -> list[str]:
    """The response headers of the HTTPS server. Repeated in a location that adds a header
    of its own, because nginx then stops inheriting the server's ``add_header`` lines."""
    lines = [
        f'add_header Strict-Transport-Security "max-age={hsts_max_age}" always;',
        'add_header X-Content-Type-Options "nosniff" always;',
        'add_header X-Frame-Options "DENY" always;',
        'add_header Referrer-Policy "no-referrer" always;',
        "add_header Content-Security-Policy \"default-src 'none'; frame-ancestors 'none'\" always;",
        'add_header Cross-Origin-Resource-Policy "same-origin" always;',
        'add_header Cache-Control "no-store" always;',
        'add_header X-Robots-Tag "noindex, nofollow" always;',
    ]
    return [" " * indent + line for line in lines]


def dashboard_headers(indent: int, hsts_max_age: int, cache_control: str) -> list[str]:
    """The response headers of a dashboard location: the server's security headers with
    the dashboard's Content-Security-Policy (and its other headers) and its cache policy."""
    lines = [
        f'add_header Strict-Transport-Security "max-age={hsts_max_age}" always;',
        'add_header X-Content-Type-Options "nosniff" always;',
        'add_header X-Frame-Options "DENY" always;',
        'add_header Referrer-Policy "no-referrer" always;',
        *(f'add_header {name} "{value}" always;' for name, value in DASHBOARD_HEADERS.items()),
        'add_header X-Robots-Tag "noindex, nofollow" always;',
        f'add_header Cache-Control "{cache_control}" always;',
    ]
    return [" " * indent + line for line in lines]


def descriptor_for(profile: str) -> str:
    """The deployment descriptor of an exposed profile (what the dashboard may do)."""
    if profile == "private":
        return descriptor("private", "enabled", operational_detail=True).decode("ascii")
    return descriptor("public-demo", "disabled_public_demo", operational_detail=False).decode(
        "ascii"
    )


def error_body(code: str, message: str) -> str:
    return (
        '{"error":{"code":"' + code + '","message":"' + message + '",'
        '"request_id":"$request_id","details":[]}}'
    )


@dataclass(frozen=True)
class Renderer:
    """Renders one deployment: a configuration, the server layout and a release's assets."""

    config: DeployConfig
    layout: Layout
    release_dir: Path
    routes: tuple[Route, ...]
    #: (M7) The release's dashboard build (``None``: a release built before M7).
    dashboard: DashboardBuild | None = None
    #: Where nginx reads it: the release's published copy (:mod:`atlas.deploy.publish`).
    dashboard_dir: PurePosixPath | None = None

    @classmethod
    def for_release(cls, config: DeployConfig, layout: Layout, release_dir: Path) -> Renderer:
        build = dashboard_build(release_dir)
        published = (
            layout.dashboard(release_dir.name)
            if valid_release_id(release_dir.name)
            # Rendering from a checkout (no release): its own build directory.
            else PurePosixPath(str(release_dir.resolve() / "web" / "dist"))
        )
        return cls(
            config,
            layout,
            release_dir,
            tuple(load_routes(release_dir)),
            build,
            published if build is not None else None,
        )

    def template(self, name: str) -> str:
        try:
            return (self.release_dir / TEMPLATES / name).read_text(encoding="utf-8")
        except OSError as exc:
            raise RenderError(f"template {name} cannot be read from {self.release_dir}") from exc

    # --- Values -----------------------------------------------------------------------------

    def values(self) -> dict[str, str]:
        config, layout = self.config, self.layout
        values = {
            "service_user": SERVICE_USER,
            "service_group": SERVICE_GROUP,
            "monitor_user": MONITOR_USER,
            "service": SERVICE,
            "current": str(layout.current),
            "executable": str(layout.executable),
            "deploy_executable": str(layout.deploy_executable),
            "runtime_env": str(layout.runtime_env),
            "api_key": str(layout.api_key),
            "api_host": API_HOST,
            "api_port": str(config.api_port),
            "api_socket": str(layout.api_socket),
            "runtime_dir_name": layout.runtime_dir.name,
            "credentials": str(layout.credentials),
            "nginx_dir": str(layout.nginx),
            "state": str(layout.state),
            "resources": config.resources.name,
            "workers": str(config.resources.workers),
            "memory_high": config.resources.memory_high,
            "memory_max": config.resources.memory_max,
            "tasks_max": str(config.resources.tasks_max),
            "alert_hook": str(layout.alert_hook),
            "journal_max_use": JOURNAL_MAX_USE[config.resources.name],
        }
        values = {key: safe(key, value) for key, value in values.items()}
        values["monitor_tls"] = (
            f" --tls-domain {safe('domain', config.domain)}"
            if config.exposed and config.domain
            else ""
        )
        return values

    # --- Files --------------------------------------------------------------------------------

    def runtime_env(self) -> str:
        lines = [
            "# ATLAS API runtime environment. Rendered by `atlas-deploy render` from",
            "# /etc/atlas/deploy.env: do not edit. No secret belongs here: the simulation key",
            "# is a systemd credential (LoadCredential), read through ATLAS_API_KEY_FILE.",
        ]
        for key, value in runtime_environment(self.config).items():
            lines.append(f"{key}={safe(key, value)}")
        return "\n".join(lines) + "\n"

    def unit(self, template: str) -> str:
        return fill(self.template(template), self.values(), name=template)

    def nginx(self, stage: str = "tls") -> str:
        config = self.config
        if not config.exposed or config.domain is None:
            raise RenderError("the tunnel profile has no nginx site")
        if stage not in STAGES:
            raise RenderError(f"unknown stage {stage!r}; use one of {', '.join(STAGES)}")
        exposed = routed(list(self.routes), config.profile)
        if config.profile != "private" and any(r.access in (COMPUTE, READINESS) for r in exposed):
            raise RenderError("only the private profile routes computation or readiness")
        domain = safe("domain", config.domain)
        resources = config.resources
        values = self.values() | {
            "profile": config.profile,
            "domain": domain,
            "read_rate": safe("read_rate", resources.read_rate),
            "static_rate": STATIC_RATE,
            "read_burst": str(resources.read_burst),
            "compute_rate": safe("compute_rate", resources.compute_rate),
            "connections_per_client": str(resources.connections_per_client),
            "acme_root": safe("acme", str(self.layout.acme)),
            "log_dir": NGINX_LOG_DIR,
            "listen_80_v6_default": "    listen [::]:80 default_server;\n" if config.ipv6 else "",
            "listen_80_v6": "    listen [::]:80;\n" if config.ipv6 else "",
        }
        if stage == "acme":
            values |= {
                "default_tls_server": "",
                "http_fallback": "        return 404;",
                "tls_server": "",
            }
        else:
            values |= {
                "default_tls_server": self._default_tls_server(),
                "http_fallback": f"        return 301 https://{domain}$request_uri;",
                "tls_server": fill(
                    self.template("nginx-atlas-tls.conf.in"),
                    values | self._tls_values(exposed),
                    name="nginx-atlas-tls.conf.in",
                ),
            }
        return fill(self.template("nginx-atlas.conf.in"), values, name="nginx-atlas.conf.in")

    def _default_tls_server(self) -> str:
        lines = ["server {", "    listen 443 ssl default_server;"]
        if self.config.ipv6:
            lines.append("    listen [::]:443 ssl default_server;")
        # No certificate for an unknown name; and a request whose Host names another site
        # after a handshake for this one (SNI and Host disagree) gets no answer either.
        lines += [
            "    server_name _;",
            "    server_tokens off;",
            "    access_log off;",
            "    ssl_reject_handshake on;",
            "    return 444;",
            "}",
        ]
        return "\n" + "\n".join(lines) + "\n"

    def _tls_values(self, exposed: list[Route]) -> dict[str, str]:
        config = self.config
        if config.tls_cert is None or config.tls_key is None:
            raise RenderError("the tls stage needs certificate and key paths")
        return {
            "listen_443_v6": "    listen [::]:443 ssl;\n" if config.ipv6 else "",
            "tls_cert": safe("tls_cert", str(config.tls_cert)),
            "tls_key": safe("tls_key", str(config.tls_key)),
            "hsts_max_age": str(config.hsts_max_age),
            "simulation_timeout": f"{config.simulation_timeout_seconds:g}",
            "proxy_read_timeout": str(int(config.simulation_timeout_seconds) + 20),
            "access_control": self._access_control(),
            "bad_request_body": error_body(*NGINX_ERRORS[0][1:]),
            "error_locations": self._error_locations(),
            "locations": "\n".join(self._location(route) for route in exposed) + "\n",
            "dashboard_locations": self._dashboard_locations(),
        }

    def dashboard_static_routes(self) -> list[StaticRoute]:
        """The dashboard URLs this deployment's site serves (none without a build)."""
        if self.dashboard is None or DASHBOARD not in PROFILE_ACCESS[self.config.profile]:
            return []
        return dashboard_routes(self.dashboard, self.routes)

    def _dashboard_locations(self) -> str:
        """The dashboard's locations: its pages (each answered with ``index.html``), its
        content-hashed assets (an alternation of their exact names), its other top-level
        files, and the descriptor. Nothing else of the published directory is reachable,
        nginx follows no link in it, and only GET and HEAD are answered."""
        routes = self.dashboard_static_routes()
        if not routes or self.dashboard_dir is None:
            return ""
        root = safe("dashboard_dir", str(self.dashboard_dir))
        hsts = self.config.hsts_max_age
        method = "        if ($request_method !~ ^(GET|HEAD)$) { return 405; }"
        serve = [
            f"        limit_req zone=atlas_static burst={STATIC_BURST} nodelay;",
            f"        root {root};",
            "        disable_symlinks on;",
        ]
        pages = [r for r in routes if r.kind == "page"]
        assets = [r for r in routes if r.kind == "file" and r.url.startswith("/assets/")]
        others = [r for r in routes if r.kind == "file" and not r.url.startswith("/assets/")]
        blocks = [
            "",
            "    # Dashboard (M7): exactly the pages and files of this release's verified build,",
            f"    # read from its published copy {root}. The descriptor says",
            f"    # what the {self.config.profile} profile allows the dashboard to do.",
        ]
        if pages:
            names = "|".join(
                safe("dashboard_page", r.url.lstrip("/")) for r in pages if r.url != "/"
            )
            pattern = f"^/(?:{names})?$" if names else "^/$"
            entry = pages[0]
            blocks.append(
                "\n".join(
                    [
                        "    # Pages (web/src/pages.ts), each answered with index.html.",
                        f'    location ~ "{pattern}" {{',
                        method,
                        *serve,
                        "        types { }",
                        f'        default_type "{entry.content_type}";',
                        f"        try_files /{safe('dashboard_file', entry.file or '')} =404;",
                        *dashboard_headers(8, hsts, entry.cache_control),
                        "    }",
                    ]
                )
            )
        if assets:
            # Names are [A-Za-z0-9_.-] only (atlas.dashboard): '.' is the one regex character.
            names = "|".join(
                safe("dashboard_asset", r.url.removeprefix("/assets/")).replace(".", r"\.")
                for r in assets
            )
            types = sorted(
                {r.content_type.split(";")[0]: r.url.rsplit(".", 1)[-1] for r in assets}.items()
            )
            blocks.append(
                "\n".join(
                    [
                        "    # Content-hashed assets: immutable under their names.",
                        f'    location ~ "^/assets/(?:{names})$" {{',
                        method,
                        *serve,
                        "        types {",
                        *(f"            {mime} {extension};" for mime, extension in types),
                        "        }",
                        "        default_type application/octet-stream;",
                        "        charset utf-8;",
                        "        charset_types text/javascript text/css;",
                        *dashboard_headers(8, hsts, assets[0].cache_control),
                        "    }",
                    ]
                )
            )
        for route in others:
            blocks.append(
                "\n".join(
                    [
                        f"    location = {safe('dashboard_url', route.url)} {{",
                        method,
                        *serve,
                        "        types { }",
                        f'        default_type "{route.content_type}";',
                        *dashboard_headers(8, hsts, route.cache_control),
                        "    }",
                    ]
                )
            )
        descriptor_route = next(r for r in routes if r.kind == "descriptor")
        # The descriptor's body is answered from a named location reached by try_files: a
        # `return` here would answer before authentication and the rate limits.
        blocks.append(
            "\n".join(
                [
                    f"    location = {safe('dashboard_url', descriptor_route.url)} {{",
                    method,
                    "        root /nonexistent;",
                    "        try_files /.atlas-descriptor @atlas_descriptor;",
                    "    }",
                    "    location @atlas_descriptor {",
                    "        default_type application/json;",
                    # JSON, like the API's answers: their headers and policy (no-store).
                    *security_headers(8, hsts),
                    f"        return 200 '{descriptor_for(self.config.profile)}';",
                    "    }",
                ]
            )
        )
        return "\n".join(blocks) + "\n"

    def _access_control(self) -> str:
        config = self.config
        lines: list[str] = [""]
        if config.allow_cidrs:
            lines.append("    # Only these networks may connect (ATLAS_DEPLOY_ALLOW_CIDRS).")
            lines += [f"    allow {safe('cidr', cidr)};" for cidr in config.allow_cidrs]
            lines.append("    deny all;")
        if config.profile == "private":
            lines.append("    # Every route needs an account (strong password hashes only).")
            lines.append('    auth_basic "ATLAS private research deployment";')
            lines.append(f"    auth_basic_user_file {self.layout.htpasswd};")
        else:
            review = safe("security_review", config.security_review or "")
            lines.append(
                "    # public-demo: no authentication; only the read-only routes below exist."
            )
            lines.append(f"    # Approved by security review: {review}")
        return "\n".join(lines) + "\n"

    def _error_locations(self) -> str:
        """Named locations answering nginx's own errors with the API's JSON error body.

        They switch the allow-list and authentication off (``allow all``,
        ``auth_basic off``): the request that reaches them was already refused, and
        refusing it again would replace the JSON body with nginx's default page.
        """
        private = self.config.profile == "private"
        hsts = self.config.hsts_max_age
        blocks: list[str] = []
        entries = [(status, code, message) for status, code, message in NGINX_ERRORS]
        entries.append(("503", *UPSTREAM_ERROR))
        for status, code, message in entries:
            name = "upstream" if code == UPSTREAM_ERROR[0] else status
            if status == "400":
                # A request nginx cannot parse (a NUL byte, ``OPTIONS *``, a lowercase
                # method) has no URI: a named location cannot answer it, an internal URI can.
                lines = ["    location = /.atlas-error-400 {", "        internal;"]
                lines.append("        allow all;")
            else:
                lines = [f"    location @atlas_{name} {{", "        allow all;"]
            if private:
                lines.append("        auth_basic off;")
            # (A 401 keeps the WWW-Authenticate challenge set by auth_basic.)
            lines.append("        default_type application/json;")
            if status in ("429", "503"):
                retry = "1" if status == "429" else "5"
                lines.append(f'        add_header Retry-After "{retry}" always;')
            if any("add_header" in line for line in lines):
                lines += security_headers(8, hsts)
            lines.append(f"        return {status} '{error_body(code, message)}';")
            lines.append("    }")
            blocks.append("\n".join(lines))
        return "\n" + "\n".join(blocks) + "\n"

    def _proxy_headers(self, *, with_key: bool) -> list[str]:
        """The request headers sent to the API, in every location: nginx does not inherit
        ``proxy_set_header`` into a location that sets any of its own.

        An allow-list: no client header is passed on (``proxy_pass_request_headers off``),
        so credentials, cookies, client-supplied keys and every forwarding header
        (``X-Forwarded-*``, ``Forwarded``, ``X-Real-IP``, ...) never reach the API. nginx
        itself still sends the body's length.
        """
        domain = self.config.domain or ""
        lines = [
            "proxy_pass_request_headers off;",
            # A fixed Host: the API's allow-list sees only the configured name.
            f"proxy_set_header Host {safe('domain', domain)};",
            # nginx's own request id: the API reuses it, so both logs share one id.
            "proxy_set_header X-Request-ID $request_id;",
            'proxy_set_header Connection "close";',
            "proxy_set_header Content-Type $content_type;",
            "proxy_set_header Accept $http_accept;",
        ]
        if with_key:
            # The simulation key, added by the proxy for authenticated users only (the
            # file holds one proxy_set_header line and is readable by root only).
            lines.append(f"include {self.layout.nginx_key};")
        return lines

    def _location(self, route: Route) -> str:
        resources = self.config.resources
        methods = "|".join(route.methods)
        lines = [
            # (route paths are checked by atlas.deploy.routes: segments and parameters only)
            f"    # {route.method} {route.path} ({route.access})",
            f"    location {route.location} {{",
            f"        if ($request_method !~ ^({methods})$) {{ return 405; }}",
        ]
        if route.access == COMPUTE:
            # Both zones: a location's own limit_req lines replace the server's.
            lines.append(f"        limit_req zone=atlas_read burst={resources.read_burst} nodelay;")
            lines.append(
                f"        limit_req zone=atlas_compute burst={resources.compute_burst} nodelay;"
            )
        lines += [
            "        " + line for line in self._proxy_headers(with_key=route.access == COMPUTE)
        ]
        lines += ["        proxy_pass http://atlas_api;", "    }"]
        return "\n".join(lines)

    # --- Everything ----------------------------------------------------------------------------

    def files(self, stage: str = "tls") -> list[Rendered]:
        layout = self.layout
        files = [
            Rendered(layout.runtime_env, self.runtime_env()),
            Rendered(layout.unit(SERVICE), self.unit("atlas.service.in")),
            Rendered(layout.unit(MONITOR_SERVICE), self.unit("atlas-monitor.service.in")),
            Rendered(layout.unit(MONITOR_TIMER), self.unit("atlas-monitor.timer.in")),
            Rendered(layout.unit(ALERT_SERVICE), self.unit("atlas-alert@.service.in")),
            Rendered(layout.journald / JOURNAL_DROPIN, self.unit("journald-atlas.conf.in")),
        ]
        if self.config.exposed:
            files.append(Rendered(layout.nginx_site, self.nginx(stage)))
        return files


# --- Writing --------------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteResult:
    path: PurePosixPath
    changed: bool


def write_files(
    files: Iterable[Rendered],
    layout: Layout,
    *,
    chown: bool = True,
    group_ids: Mapping[str, int] | None = None,
) -> list[WriteResult]:
    """Write each file atomically (temporary file, fsync, rename); unchanged files are left
    alone. ``chown`` sets owner root and the file's group (on a server, as root)."""
    results: list[WriteResult] = []
    for item in files:
        target = layout.local(item.path)
        data = item.content.encode("utf-8")
        try:
            current = target.read_bytes()
        except OSError:
            current = None
        same_mode = current is not None and (target.stat().st_mode & 0o7777) == item.mode
        if current == data and same_mode:
            results.append(WriteResult(item.path, changed=False))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary).chmod(item.mode)
            if chown:
                gid = (group_ids or {}).get(item.group)
                os.chown(temporary, 0, -1 if gid is None else gid)
            Path(temporary).replace(target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        results.append(WriteResult(item.path, changed=True))
    return results


def enable_site(layout: Layout) -> bool:
    """Link the site into ``sites-enabled`` (idempotent); ``True`` if the link changed."""
    link = layout.local(layout.nginx_site_enabled)
    target = str(layout.nginx_site)
    if link.is_symlink() and str(link.readlink()) == target:
        return False
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".{link.name}.new")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    temporary.replace(link)
    return True


def disable_site(layout: Layout) -> bool:
    """Take the site out of nginx: remove its ``sites-enabled`` link and its file
    (idempotent); ``True`` if anything was removed. A profile that does not expose the
    API (tunnel) must not leave an earlier profile's site serving."""
    removed = False
    for path in (layout.nginx_site_enabled, layout.nginx_site):
        target = layout.local(path)
        if target.is_symlink() or target.exists():
            target.unlink()
            removed = True
    return removed
