"""The reverse proxy's route allow-list, derived from the committed OpenAPI contract.

nginx routes only the operations the contract documents (``docs/api/openapi.json``, which
CI requires to equal the code), each with exactly its methods; every other path is a 404
at the proxy and never reaches the API. A new endpoint is therefore exposed only when the
contract changes and the proxy configuration is rendered again: never by accident.

Each operation has an access class, from its ``x-atlas-cost``:

``liveness``   ``GET /health/live``: the process answers (no detail).
``readiness``  ``GET /health/ready`` and ``GET /api/v1/system``: component readiness with
               failure details, the simulation workers' state, limits and versions
               (operational detail, although the contract marks ``/system`` precomputed).
``read``       the other precomputed GET endpoints: committed research outputs.
``compute``    ``POST /api/v1/simulations``: one bounded simulation (CPU-bound).

``dashboard`` (M7) the dashboard's static routes, from the release's verified build
               (``web/dist/atlas-dashboard.json``), not from the contract: its pages
               (each answered with ``index.html``), each built file at its exact path,
               and the deployment descriptor (:func:`dashboard_routes`).

The ``private`` profile routes every class behind authentication. The ``public-demo``
profile routes ``liveness``, ``read`` and ``dashboard`` only: computation and operational
detail stay private (:data:`PROFILE_ACCESS`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from atlas.dashboard import (
    CACHE_REVALIDATE,
    CONTENT_TYPES,
    DESCRIPTOR_PATH,
    DIST,
    ENTRY,
    MANIFEST_NAME,
    DashboardBuild,
    load_build,
)

LIVENESS: Final = "liveness"
READINESS: Final = "readiness"
READ: Final = "read"
COMPUTE: Final = "compute"
DASHBOARD: Final = "dashboard"

PROFILE_ACCESS: Final[dict[str, frozenset[str]]] = {
    "private": frozenset({LIVENESS, READINESS, READ, COMPUTE, DASHBOARD}),
    "public-demo": frozenset({LIVENESS, READ, DASHBOARD}),
}
#: Dashboard URLs nginx may embed: lowercase segments, a hashed asset name, nothing else.
_SAFE_STATIC: Final = re.compile(
    r"/(?:[a-z][a-z0-9-]{0,63}(?:\.[a-z0-9]{2,5})?|assets/[A-Za-z0-9_.-]{1,127})?"
)
#: Prefixes that belong to the API: no dashboard URL may use them.
API_PREFIXES: Final = ("/api/", "/health/", "/docs", "/redoc", "/openapi.json")

_PARAMETER: Final = re.compile(r"\{([a-z_]+)\}")
#: A contract path nginx may embed: lowercase segments and named parameters, nothing else.
_SAFE_PATH: Final = re.compile(r"(/([a-z0-9_-]+|\{[a-z_]+\}))+")
#: Operational detail: never routed without authentication (see the module docstring).
OPERATIONAL: Final = frozenset({"/health/ready", "/api/v1/system"})
#: Path parameter patterns nginx may embed (a conservative character set: no quote, no
#: brace other than a repetition count, no whitespace).
_SAFE_PATTERN: Final = re.compile(r"\^?[a-z0-9_\[\]{},\-]+\$?")


class RouteError(ValueError):
    """The contract cannot be turned into a safe allow-list."""


@dataclass(frozen=True)
class Route:
    path: str
    method: str
    access: str
    #: The nginx location: ``= /exact/path`` or a quoted, anchored regular expression.
    location: str

    @property
    def methods(self) -> tuple[str, ...]:
        """The HTTP methods nginx lets through (HEAD with GET, as the API answers it)."""
        return ("GET", "HEAD") if self.method == "GET" else (self.method,)


def _access(path: str, method: str, cost: str) -> str:
    if cost == "bounded_computation":
        return COMPUTE
    if path == "/health/live":
        return LIVENESS
    if path in OPERATIONAL:
        return READINESS
    if cost == "precomputed" and method == "GET":
        return READ
    raise RouteError(f"{method} {path}: unknown cost {cost!r}; classify it before routing it")


def _location(path: str, operation: Mapping[str, Any]) -> str:
    if not _SAFE_PATH.fullmatch(path):
        raise RouteError(f"{path!r}: unexpected characters for an nginx location")
    names = _PARAMETER.findall(path)
    if not names:
        return f"= {path}"
    patterns: dict[str, str] = {}
    for parameter in operation.get("parameters", []):
        if parameter.get("in") == "path":
            pattern = parameter.get("schema", {}).get("pattern")
            if not isinstance(pattern, str) or not _SAFE_PATTERN.fullmatch(pattern):
                raise RouteError(f"{path}: path parameter without a safe pattern")
            patterns[parameter["name"]] = pattern.removeprefix("^").removesuffix("$")
    regex = path
    for name in names:
        if name not in patterns:
            raise RouteError(f"{path}: no pattern for path parameter {name}")
        regex = regex.replace("{" + name + "}", patterns[name])
    return f'~ "^{regex}$"'


def routes_from_openapi(document: Mapping[str, Any]) -> list[Route]:
    """Every operation of the contract as a route (sorted: exact paths first)."""
    found: list[Route] = []
    for path, item in document.get("paths", {}).items():
        for method, operation in item.items():
            verb = method.upper()
            if verb not in ("GET", "POST"):
                raise RouteError(f"{verb} {path}: only GET and POST are routed")
            cost = operation.get("x-atlas-cost")
            if not isinstance(cost, str):
                raise RouteError(f"{verb} {path}: no x-atlas-cost in the contract")
            found.append(Route(path, verb, _access(path, verb, cost), _location(path, operation)))
    locations = [r.location for r in found]
    if len(set(locations)) != len(locations):
        raise RouteError("two operations share a path: one location per path is required")
    return sorted(found, key=lambda r: (r.location.startswith("~"), r.path))


def load_routes(release_dir: Path) -> list[Route]:
    document = json.loads((release_dir / "docs" / "api" / "openapi.json").read_text("utf-8"))
    return routes_from_openapi(document)


def routed(routes: list[Route], profile: str) -> list[Route]:
    """The routes ``profile`` exposes through nginx."""
    allowed = PROFILE_ACCESS[profile]
    return [route for route in routes if route.access in allowed]


@dataclass(frozen=True)
class StaticRoute:
    """One exact dashboard URL: a page (``index.html``), a built file, or the descriptor."""

    url: str
    kind: str
    #: The file below the published build that answers it (``None``: the descriptor).
    file: str | None
    content_type: str
    cache_control: str

    @property
    def access(self) -> str:
        return DASHBOARD


def dashboard_build(release_dir: Path) -> DashboardBuild | None:
    """The release's dashboard build, or ``None`` for a release without one (built before
    M7). A release whose manifest records a dashboard must still have it: that is checked
    against ``release.json`` (:func:`atlas.deploy.manifest.check_release`)."""
    if not (release_dir / DIST / MANIFEST_NAME).exists():
        return None
    return load_build(release_dir / DIST)


def dashboard_routes(build: DashboardBuild, api_routes: Sequence[Route] = ()) -> list[StaticRoute]:
    """Every URL of the dashboard, sorted; refused if one could clash with the API or is
    not a plain path nginx can embed."""
    entry = build.entry
    found = [
        StaticRoute(route, "page", entry.path, entry.content_type, CACHE_REVALIDATE)
        for route in build.routes
    ]
    found += [
        StaticRoute(item.url, "file", item.path, item.content_type, item.cache_control)
        for item in build.files
        if item.path != ENTRY
    ]
    found.append(StaticRoute(DESCRIPTOR_PATH, "descriptor", None, "application/json", "no-store"))
    api_paths = {route.path for route in api_routes}
    seen: set[str] = set()
    for route in found:
        if not _SAFE_STATIC.fullmatch(route.url) or ".." in route.url:
            raise RouteError(f"dashboard URL {route.url!r}: unexpected characters for nginx")
        if route.url.startswith(API_PREFIXES) or route.url in api_paths:
            raise RouteError(f"dashboard URL {route.url!r} would shadow the API")
        if route.url in seen:
            raise RouteError(f"dashboard URL {route.url!r} is listed twice")
        if route.content_type not in (*CONTENT_TYPES.values(), "application/json"):
            raise RouteError(f"dashboard URL {route.url!r}: unexpected content type")
        seen.add(route.url)
    return sorted(found, key=lambda r: r.url)
