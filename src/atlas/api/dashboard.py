"""The API's own dashboard mount (M7): the built dashboard, served from memory.

``atlas api serve`` serves the committed dashboard build (``web/dist``) next to the API,
same origin, so a local checkout and the SSH-tunnel deployment show it at ``/`` without
any other server. (Exposed deployments serve it from nginx instead: see
:mod:`atlas.deploy.render`.)

The build is read once at startup and every file is checked against its manifest
(:func:`atlas.dashboard.read_verified`); requests are answered from that memory by exact
path, so no request can open a file, let alone one outside the build. Only ``GET`` and
``HEAD`` are served. HTML gets the dashboard's Content-Security-Policy; hashed assets are
cached as immutable, the rest is revalidated with an ETag. Every other path continues to
the API unchanged.

The deployment descriptor says ``profile: local``. Simulations are ``enabled`` only when
the API has no key: a browser can never hold one, so with a key (the tunnel profile) the
dashboard disables them and says why.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Final

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from atlas.api.context import current_request_id
from atlas.api.problems import error_payload
from atlas.dashboard import (
    COMPRESSIBLE,
    DESCRIPTOR_PATH,
    DIST,
    DashboardBuild,
    DashboardError,
    compressed,
    descriptor,
    read_verified,
)

LOGGER: Final = logging.getLogger("atlas.api")


@dataclass(frozen=True)
class Asset:
    """One answer: its body (and gzip body), type, cache policy and validator."""

    body: bytes
    gzipped: bytes | None
    content_type: str
    cache_control: str
    etag: str


@dataclass(frozen=True)
class Dashboard:
    """The verified build in memory, by request path."""

    build: DashboardBuild
    assets: dict[str, Asset]

    @property
    def paths(self) -> frozenset[str]:
        return frozenset(self.assets)


def load_dashboard(repo_root: Path, *, simulations_enabled: bool) -> Dashboard:
    """Read and verify ``web/dist`` of ``repo_root`` (a :class:`DashboardError` if it fails)."""
    build, contents = read_verified(repo_root / DIST)
    assets: dict[str, Asset] = {}

    def add(path: str, body: bytes, content_type: str, cache: str, etag: str, gzip: bool) -> None:
        assets[path] = Asset(body, compressed(body) if gzip else None, content_type, cache, etag)

    for item in build.files:
        if item.path == "index.html":
            continue
        add(
            item.url,
            contents[item.path],
            item.content_type,
            item.cache_control,
            item.etag,
            item.suffix in COMPRESSIBLE,
        )
    entry = build.entry
    for route in build.routes:
        add(route, contents[entry.path], entry.content_type, entry.cache_control, entry.etag, True)
    document = descriptor(
        "local",
        "enabled" if simulations_enabled else "disabled_api_key",
        operational_detail=True,
    )
    add(DESCRIPTOR_PATH, document, "application/json", "no-store", '"descriptor"', False)
    return Dashboard(build, assets)


class DashboardMiddleware:
    """Answers the dashboard's exact paths; passes everything else to the API."""

    def __init__(self, app: ASGIApp, *, dashboard: Dashboard | None) -> None:
        self.app = app
        self.dashboard = dashboard

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.dashboard is None:
            await self.app(scope, receive, send)
            return
        asset = self.dashboard.assets.get(str(scope.get("path", "")))
        if asset is None:
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method", ""))
        if method not in ("GET", "HEAD"):
            body = json.dumps(
                error_payload(
                    "method_not_allowed",
                    "the dashboard is read-only: GET and HEAD only",
                    current_request_id(),
                )
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": HTTPStatus.METHOD_NOT_ALLOWED,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        (b"allow", b"GET, HEAD"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        headers = Headers(scope=scope)
        common = [
            (b"content-type", asset.content_type.encode("ascii")),
            (b"cache-control", asset.cache_control.encode("ascii")),
            (b"etag", asset.etag.encode("ascii")),
            (b"vary", b"Accept-Encoding"),
        ]
        if asset.etag in [tag.strip() for tag in headers.get("if-none-match", "").split(",")]:
            await send({"type": "http.response.start", "status": 304, "headers": common})
            await send({"type": "http.response.body", "body": b""})
            return
        body = asset.body
        if asset.gzipped is not None and "gzip" in headers.get("accept-encoding", "").lower():
            body = asset.gzipped
            common.append((b"content-encoding", b"gzip"))
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [*common, (b"content-length", str(len(body)).encode("ascii"))],
            }
        )
        await send({"type": "http.response.body", "body": b"" if method == "HEAD" else body})


def try_load_dashboard(repo_root: Path, *, simulations_enabled: bool) -> Dashboard | None:
    """The dashboard, or ``None`` (logged) when its build is missing or does not verify:
    the API keeps serving, and the dashboard's paths are then the API's 404s."""
    try:
        return load_dashboard(repo_root, simulations_enabled=simulations_enabled)
    except (DashboardError, OSError) as exc:
        LOGGER.warning(
            "dashboard not served: %s",
            exc,
            extra={"event": "dashboard_unavailable"},
        )
        return None
