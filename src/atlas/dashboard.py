"""The built web dashboard (M7): what a build is, how it is verified, how it is served.

The dashboard (``web/``, React and TypeScript) is built by Vite into ``web/dist`` and the
build is committed, so neither a release nor a server ever needs Node.js. CI rebuilds it
from the committed sources and lock and requires the same bytes. Every build carries
``web/dist/atlas-dashboard.json``, its manifest:

* every built file with its SHA-256 and size;
* the client routes (the dashboard's pages), each answered with ``index.html``.

This module (standard library only: the deployment tooling uses it too) parses that
manifest strictly, verifies a build against it byte for byte, and states the rules every
server of the dashboard follows (the API's own mount for local and tunnel use, and the
nginx site rendered by ``atlas-deploy``):

* only the manifest's files and routes exist; every other path is not the dashboard's;
* files are regular files below one directory, named by a strict pattern: no path a
  request could use to leave that directory exists;
* hashed assets (``assets/<name>-<hash>.<ext>``) are immutable; ``index.html`` and the
  other top-level files are revalidated on every load;
* HTML gets its own Content-Security-Policy (:data:`DASHBOARD_CSP`): scripts, styles,
  fonts, images and requests from the same origin only, no inline code, no eval, no
  framing. The API's ``default-src 'none'`` stays on JSON.

The dashboard learns what a deployment allows from a small descriptor served next to it
(:data:`DESCRIPTOR_PATH`, :func:`descriptor`): its profile, whether a browser may run
simulations, and whether operational detail is reachable. It never holds a key.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

#: Where the committed build lives in a checkout or a release.
DIST: Final = PurePosixPath("web/dist")
MANIFEST_NAME: Final = "atlas-dashboard.json"
MANIFEST_FORMAT: Final = "atlas-dashboard/1"
ENTRY: Final = "index.html"
#: The deployment descriptor's URL path (served by whatever serves the dashboard).
DESCRIPTOR_PATH: Final = "/atlas-deployment.json"
DESCRIPTOR_FORMAT: Final = "atlas-deployment/1"

#: The Content-Security-Policy of the dashboard's HTML (and its other files): everything
#: from this origin only, nothing inline, no eval, no plugins, no framing, forms nowhere,
#: and Trusted Types required for every DOM injection sink (the dashboard uses none).
DASHBOARD_CSP: Final = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; manifest-src 'self'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'; object-src 'none'; "
    "require-trusted-types-for 'script'; trusted-types 'none'"
)
#: Response headers of every dashboard answer, with :data:`DASHBOARD_CSP`.
DASHBOARD_HEADERS: Final = {
    "Content-Security-Policy": DASHBOARD_CSP,
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}
#: Cache policies: content-hashed assets never change; everything else is revalidated. Not
#: ``public``: behind accounts, shared caches must not keep them (RFC 9111 section 3.5).
CACHE_IMMUTABLE: Final = "max-age=31536000, immutable"
CACHE_REVALIDATE: Final = "no-cache"

#: The only file types a build may contain, with the type every server sends for them.
CONTENT_TYPES: Final = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}
#: Types worth compressing (woff2 is compressed already).
COMPRESSIBLE: Final = frozenset({".html", ".js", ".css", ".svg"})

#: A top-level file (``index.html``, ``favicon.svg``) or a content-hashed asset.
_TOP: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\.[a-z0-9]{2,5}")
_ASSET: Final = re.compile(
    r"assets/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}-[A-Za-z0-9_-]{8,}\.[a-z0-9]{2,5}"
)
#: A client route: ``/`` or one lowercase segment.
_ROUTE: Final = re.compile(r"/(?:[a-z][a-z0-9-]{0,63})?")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_VERSION: Final = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
MAX_FILES: Final = 200
MAX_BUILD_BYTES: Final = 16 * 1024 * 1024

PROFILES: Final = ("local", "private", "public-demo")
SIMULATIONS: Final = ("enabled", "disabled_public_demo", "disabled_api_key")


class DashboardError(ValueError):
    """A dashboard build or manifest is missing, malformed or does not match."""


@dataclass(frozen=True)
class DashboardFile:
    """One built file: its path below the build directory, hash, size and serving rules."""

    path: str
    sha256: str
    size: int

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.path).suffix

    @property
    def content_type(self) -> str:
        return CONTENT_TYPES[self.suffix]

    @property
    def immutable(self) -> bool:
        """Content-hashed assets (``assets/``) never change under the same name."""
        return self.path.startswith("assets/")

    @property
    def cache_control(self) -> str:
        return CACHE_IMMUTABLE if self.immutable else CACHE_REVALIDATE

    @property
    def url(self) -> str:
        return "/" + self.path

    @property
    def etag(self) -> str:
        return f'"{self.sha256[:32]}"'


@dataclass(frozen=True)
class DashboardBuild:
    """A parsed manifest: the build's version, routes and files."""

    version: str
    routes: tuple[str, ...]
    files: tuple[DashboardFile, ...]
    manifest_sha256: str

    @property
    def entry(self) -> DashboardFile:
        return next(f for f in self.files if f.path == ENTRY)

    def file(self, path: str) -> DashboardFile | None:
        return next((f for f in self.files if f.path == path), None)

    def fingerprint(self) -> str:
        """One SHA-256 over the sorted ``path NUL sha256`` lines of the build's files."""
        digest = hashlib.sha256()
        for item in sorted(self.files, key=lambda f: f.path):
            digest.update(f"{item.path}\0{item.sha256}\n".encode())
        return digest.hexdigest()


def valid_file_path(path: str) -> bool:
    """Whether ``path`` may name a built file (no directory but ``assets/``, no dot
    segments, no unexpected character, an allowed type)."""
    if not (_TOP.fullmatch(path) or _ASSET.fullmatch(path)):
        return False
    return PurePosixPath(path).suffix in CONTENT_TYPES and ".." not in path


def parse_manifest(data: bytes) -> DashboardBuild:
    """The build a manifest describes; anything unexpected is a :class:`DashboardError`."""
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise DashboardError(f"{MANIFEST_NAME} is not JSON") from exc
    if not isinstance(document, dict):
        raise DashboardError(f"{MANIFEST_NAME} is not a JSON object")
    expected = {"format", "version", "entry", "routes", "files"}
    if set(document) != expected:
        raise DashboardError(f"{MANIFEST_NAME} must have exactly the keys {sorted(expected)}")
    if document["format"] != MANIFEST_FORMAT:
        raise DashboardError(f"unsupported dashboard manifest format {document['format']!r}")
    version = document["version"]
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise DashboardError("the dashboard version must be MAJOR.MINOR.PATCH")
    if document["entry"] != ENTRY:
        raise DashboardError(f"the dashboard entry must be {ENTRY}")
    routes = document["routes"]
    if (
        not isinstance(routes, list)
        or not routes
        or not all(isinstance(r, str) and _ROUTE.fullmatch(r) for r in routes)
        or len(set(routes)) != len(routes)
        or "/" not in routes
    ):
        raise DashboardError("the routes must be distinct paths '/' or '/<segment>', '/' included")
    files = document["files"]
    if not isinstance(files, dict) or not 0 < len(files) <= MAX_FILES:
        raise DashboardError(f"the build must list between 1 and {MAX_FILES} files")
    parsed: list[DashboardFile] = []
    total = 0
    for path, entry in files.items():
        if not isinstance(path, str) or not valid_file_path(path):
            raise DashboardError(f"unexpected file name in the build: {path!r}")
        if (
            not isinstance(entry, dict)
            or set(entry) != {"sha256", "bytes"}
            or not isinstance(entry["sha256"], str)
            or not _SHA256.fullmatch(entry["sha256"])
            or not isinstance(entry["bytes"], int)
            or isinstance(entry["bytes"], bool)
            or entry["bytes"] < 0
        ):
            raise DashboardError(f"{path}: expected {{'sha256': <64 hex>, 'bytes': <size>}}")
        total += entry["bytes"]
        parsed.append(DashboardFile(path, entry["sha256"], entry["bytes"]))
    if ENTRY not in files:
        raise DashboardError(f"the build has no {ENTRY}")
    if total > MAX_BUILD_BYTES:
        raise DashboardError("the build is larger than a dashboard should be")
    urls = {f.url for f in parsed}
    if any(route in urls for route in routes) or DESCRIPTOR_PATH in urls:
        raise DashboardError("a route or the descriptor path collides with a built file")
    return DashboardBuild(
        version=version,
        routes=tuple(routes),
        files=tuple(sorted(parsed, key=lambda f: f.path)),
        manifest_sha256=hashlib.sha256(data).hexdigest(),
    )


def load_build(dist: Path) -> DashboardBuild:
    """The manifest of the build in ``dist`` (not yet verified against the files)."""
    path = dist / MANIFEST_NAME
    try:
        info = path.lstat()
    except OSError as exc:
        raise DashboardError(f"{MANIFEST_NAME} is missing from {dist}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise DashboardError(f"{MANIFEST_NAME} in {dist} is not a regular file")
    return parse_manifest(path.read_bytes())


def _files_on_disk(dist: Path) -> set[str]:
    found: set[str] = set()
    for item in dist.rglob("*"):
        relative = item.relative_to(dist).as_posix()
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            found.add(relative + " (not a regular file)")
        elif item.is_file() and relative != MANIFEST_NAME:
            found.add(relative)
    return found


def verify_build(dist: Path, build: DashboardBuild) -> list[str]:
    """Differences between the files in ``dist`` and the manifest (empty: they agree).

    Every listed file must be a regular file (never a link) with the recorded size and
    hash, and nothing else may be in the directory: what a server publishes is exactly
    what the manifest names.
    """
    problems: list[str] = []
    listed = {f.path for f in build.files}
    on_disk = _files_on_disk(dist)
    problems += [f"dashboard file not in the manifest: {p}" for p in sorted(on_disk - listed)]
    for item in build.files:
        path = dist / item.path
        try:
            info = path.lstat()
        except OSError:
            problems.append(f"dashboard file missing: {item.path}")
            continue
        if not stat.S_ISREG(info.st_mode):
            problems.append(f"dashboard file is not a regular file: {item.path}")
            continue
        data = path.read_bytes()
        if len(data) != item.size or hashlib.sha256(data).hexdigest() != item.sha256:
            problems.append(f"dashboard file altered: {item.path}")
    return problems[:50]


def read_verified(dist: Path) -> tuple[DashboardBuild, dict[str, bytes]]:
    """The build and every file's bytes, each checked against the manifest (a server then
    serves from memory: no request ever opens a file)."""
    build = load_build(dist)
    problems = verify_build(dist, build)
    if problems:
        raise DashboardError("; ".join(problems[:5]))
    contents: dict[str, bytes] = {}
    for item in build.files:
        data = (dist / item.path).read_bytes()
        if hashlib.sha256(data).hexdigest() != item.sha256:  # replaced since the check
            raise DashboardError(f"dashboard file altered: {item.path}")
        contents[item.path] = data
    return build, contents


def compressed(data: bytes) -> bytes:
    """Deterministic gzip (no timestamp) of a file served with ``Content-Encoding: gzip``."""
    return gzip.compress(data, compresslevel=9, mtime=0)


def descriptor(profile: str, simulations: str, *, operational_detail: bool) -> bytes:
    """The deployment descriptor (canonical JSON) a dashboard reads at start."""
    if profile not in PROFILES:
        raise DashboardError(f"unknown deployment profile {profile!r}")
    if simulations not in SIMULATIONS:
        raise DashboardError(f"unknown simulation availability {simulations!r}")
    document: Mapping[str, Any] = {
        "format": DESCRIPTOR_FORMAT,
        "profile": profile,
        "simulations": simulations,
        "operational_detail": operational_detail,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def manifest_record(dist: Path) -> dict[str, Any]:
    """What a release manifest records about its dashboard (verified first)."""
    build = load_build(dist)
    problems = verify_build(dist, build)
    if problems:
        raise DashboardError("; ".join(problems[:5]))
    return {
        "path": str(DIST),
        "version": build.version,
        "manifest_sha256": build.manifest_sha256,
        "fingerprint": build.fingerprint(),
        "routes": list(build.routes),
        "files": {f.path: f.sha256 for f in build.files},
        "csp": DASHBOARD_CSP,
    }
