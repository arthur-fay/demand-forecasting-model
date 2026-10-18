"""(M9) A static, read-only export of the API's reference answers for a static host.

A static host (GitHub Pages) serves files and runs nothing: no Python, no API, no M4
engine. The dashboard's static profile (``web/``, ``npm run build:pages``) therefore reads
recorded answers of the API instead of asking it. This module records them.

**What a file is.** Every exported file is the exact body the API answers to one GET
request, recorded in-process from the API itself (:func:`atlas.api.app.create_app`) over
the reference store, whose components check every committed research file against the
SHA-256 its run recorded before serving it (:mod:`atlas.api.reference`). Nothing is
computed, filtered, rounded or reworded here: the integrity block, the notices, the
provenance and the numbers are the API's, byte for byte. A request the API does not answer
with 200 stops the export; nothing is ever written in place of an answer.

**What is exported.** The answers the dashboard's read-only pages ask for (:data:`FIXED`),
the observed history at the windows the Forecasting page offers (:data:`HISTORY_WINDOWS`),
the M2 accuracy by horizon for every period of the M2 run, and every M4 reference scenario
of the committed run, each with its daily plans.

**What is not** (:data:`NOT_EXPORTED`): ``GET /api/v1/system`` (operational metadata: the
component readiness, limits, worker state, solver versions and endpoint inventory, which
the public-demo profile keeps private too), ``/health/*``, the OpenAPI document and its
pages, and ``POST /api/v1/simulations``, a computation that needs the Python engine.

**Determinism.** No clock, no random value and no environment-dependent setting enters an
answer or the manifest: the settings are fixed here (:func:`export_settings`), and the API's
GET bodies are deterministic (docs/API.md section 4). Exporting the same commit twice gives
the same bytes; :func:`verify_static_export` relies on it.

**Checks before anything is written** (:func:`check_answer`): the answer's integrity
labels (never a validated forecast or recommendation; every M4 answer ``HYPOTHETICAL``;
one M3 interval status throughout), and its public safety: every source path relative and
inside the repository, every source file hashing to the SHA-256 its answer names (checked
again here, independently of the API's own startup check), and no text that looks like a
local path, a host, an address, a credential or an e-mail address.

The manifest (``atlas-static-export.json``, :data:`EXPORT_FORMAT`) lists every file with its
request, size and SHA-256 and the integrity of its answer, the sources behind them, the
scientific status the answers record and what was left out, and why. The dashboard's static
build embeds the file list and refuses, in the browser, a file whose SHA-256 differs.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Final
from urllib.parse import urlsplit

from atlas import __version__
from atlas.api.schemas import API_VERSION
from atlas.api.settings import ApiSettings
from atlas.issues import CheckReport, Issue, Severity, examples

EXPORT_FORMAT: Final = "atlas-static-export/1"
MANIFEST_NAME: Final = "atlas-static-export.json"
#: Where the committed export lives in a checkout (read by ``web/vite.config.ts``).
DEFAULT_OUTPUT: Final = PurePosixPath("web/static-export")
#: The host name the in-process requests carry (the only one the export's API allows).
HOST: Final = "atlas-static-export.invalid"

#: Answers recorded as they are, in this order: (file name, request).
FIXED: Final[tuple[tuple[str, str], ...]] = (
    ("models.json", "/api/v1/models"),
    ("forecast.json", "/api/v1/forecasts?horizon_days=30"),
    ("history.json", "/api/v1/forecasts/history"),
    ("metrics-bucket.json", "/api/v1/forecasts/metrics?scope=bucket"),
    ("uncertainty-status.json", "/api/v1/uncertainty/status"),
    ("intervals.json", "/api/v1/uncertainty/intervals?horizon_days=30"),
    ("scenarios.json", "/api/v1/scenarios"),
    ("comparison.json", "/api/v1/scenarios/comparison"),
    ("simulation-parameters.json", "/api/v1/simulations/parameters"),
)
#: The history windows of the Forecasting page besides the API's default (the last 180
#: days, ``history.json``): the last 90 and 365 days and the whole snapshot. The page asks
#: for ``start`` = the snapshot's last day minus (days - 1), or its first day for all
#: (``web/src/views/Forecasting.tsx``, WINDOWS); ``tests/test_m9_static_export.py`` and
#: ``web/src/api/static.test.ts`` check that the two agree.
HISTORY_WINDOWS: Final[tuple[int | None, ...]] = (90, 365, None)

#: What the export leaves out, and why (recorded in the manifest).
NOT_EXPORTED: Final[tuple[tuple[str, str], ...]] = (
    (
        "GET /api/v1/system",
        "operational metadata (component readiness, limits, simulation workers, solver "
        "versions, endpoint inventory); not public, as in the public-demo profile",
    ),
    ("GET /health/live, GET /health/ready", "the liveness and readiness of a running server"),
    ("GET /openapi.json, /docs, /redoc", "the contract is in the repository (docs/api)"),
    (
        "POST /api/v1/simulations",
        "a computation: it needs the Python M4 engine, which a static host cannot run",
    ),
)

_FILE_NAME: Final = re.compile(r"[a-z0-9][a-z0-9_.-]{0,95}\.json")
_M4_PREFIXES: Final = ("/api/v1/scenarios", "/api/v1/simulations")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")

#: Text that never belongs in a public answer: (name, pattern). Each is reviewed: none of
#: them occurs in the committed answers, and a new occurrence stops the export.
_UNSAFE: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "an absolute local path",
        re.compile(
            r"(?<![\w.:/])/(?:home|root|Users|tmp|var|etc|opt|srv|mnt|private|run|usr|nix|"
            r"Volumes|workspace|__w)/"
        ),
    ),
    (
        "a Windows path",
        re.compile(r"\b[A-Za-z]:(?:\\|/(?:Users|Windows|Program Files)|/[\w .$-]+/[\w.$-])"),
    ),
    ("a network share", re.compile(r"\\\\[A-Za-z0-9.-]+\\")),
    ("a file URL", re.compile(r"file:/", re.IGNORECASE)),
    ("a URL with credentials", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]*:[^/\s@]+@", re.I)),
    ("a local host name", re.compile(r"\blocalhost\b|\.local\b|\.internal\b", re.IGNORECASE)),
    (
        "an internal host name",
        re.compile(
            r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:localdomain|corp|lan|intranet|home\.arpa)\b", re.I
        ),
    ),
    ("an IP address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\[?::1\]?")),
    ("an IPv6 address", re.compile(r"\b(?:[0-9a-f]{1,4}:){5,7}[0-9a-f]{1,4}\b", re.IGNORECASE)),
    ("an e-mail address", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "a token",
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_\w{30,}|(?:AKIA|ASIA)[0-9A-Z]{16}|"
            r"xox[abprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9_-]{20,}|"
            r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.)"
        ),
    ),
    (
        "a credential word",
        re.compile(
            r"x-api-key|api[_ -]?key|password|passwd|secret|bearer|authorization|token",
            re.IGNORECASE,
        ),
    ),
)


class StaticExportError(Exception):
    """The export cannot be made: an answer is missing, refused or unsafe to publish."""


@dataclass(frozen=True)
class Answer:
    """One recorded answer: the file it is written to, its request and its exact body."""

    file: str
    request: str
    body: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    def document(self) -> dict[str, Any]:
        value = json.loads(self.body)
        if not isinstance(value, dict):
            raise StaticExportError(f"{self.request}: the answer is not a JSON object")
        return value


def export_settings(repo_root: Path, snapshot_id: str) -> ApiSettings:
    """The API settings of an export: fixed, so no environment variable changes an answer
    (no key, no dashboard mount, no documentation pages, one allowed host)."""
    return ApiSettings(
        repo_root=repo_root,
        snapshot_id=snapshot_id,
        allowed_hosts=(HOST,),
        api_key=None,
        docs_enabled=False,
        dashboard_enabled=False,
    )


# --- Recording ----------------------------------------------------------------------------


async def _asgi_get(app: Any, request: str) -> tuple[int, dict[str, str], bytes]:
    """One GET through the app's full middleware stack, in-process (no socket)."""
    target = urlsplit(request)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": target.path,
        "raw_path": target.path.encode("ascii"),
        "query_string": target.query.encode("ascii"),
        "root_path": "",
        "headers": [(b"host", HOST.encode("ascii")), (b"accept", b"application/json")],
        "client": ("192.0.2.1", 50000),
        "server": (HOST, 80),
    }
    status = 0
    headers: dict[str, str] = {}
    chunks: list[bytes] = []
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()  # no disconnect: the app answers first
        raise AssertionError  # pragma: no cover - never reached

    async def send(message: Mapping[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = int(message["status"])
            for name, value in message.get("headers", []):
                headers[bytes(name).decode("latin-1").lower()] = bytes(value).decode("latin-1")
        elif message["type"] == "http.response.body":
            chunks.append(bytes(message.get("body", b"")))

    await app(scope, receive, send)
    return status, headers, b"".join(chunks)


class Recorder:
    """The API over the committed reference store, asked one GET at a time."""

    def __init__(self, settings: ApiSettings) -> None:
        # Imported here: the web stack loads only for this command.
        from atlas.api.app import create_app
        from atlas.api.reference import load_reference_store
        from atlas.api.simulation import SimulationService

        store = load_reference_store(settings)
        if not store.ready:
            failed = [
                f"{name}: {state.detail or 'not ready'}"
                for name, state in sorted(store.status.items())
                if not state.ready
            ]
            raise StaticExportError(
                "the reference data did not pass the API's checks, so nothing is exported: "
                + "; ".join(failed)
            )
        # Never started: no export request runs a simulation (the app needs the service).
        self._simulator = SimulationService(store, size=1, timeout_seconds=1.0)
        self._app = create_app(settings, store=store, simulator=self._simulator)
        # One event loop for every request, as a server has.
        self._loop = asyncio.Runner()

    def close(self) -> None:
        self._loop.close()
        self._simulator.close()

    def get(self, file: str, request: str) -> Answer:
        if not _FILE_NAME.fullmatch(file):
            raise StaticExportError(f"{file!r} is not a valid export file name")
        status, headers, body = self._loop.run(_asgi_get(self._app, request))
        if status != 200:
            raise StaticExportError(f"GET {request} answered HTTP {status}: {body[:300]!r}")
        if not headers.get("content-type", "").startswith("application/json"):
            raise StaticExportError(f"GET {request} did not answer JSON")
        return Answer(file=file, request=request, body=body)


def plan_and_record(recorder: Recorder) -> list[Answer]:
    """Every exported answer, in a fixed order (the derived requests follow the answers
    they are derived from)."""
    answers = [recorder.get(file, request) for file, request in FIXED]
    by_file = {a.file: a for a in answers}

    history = by_file["history.json"].document()["data"]
    first = date.fromisoformat(history["snapshot_first_date"])
    last = date.fromisoformat(history["snapshot_last_date"])
    for days in HISTORY_WINDOWS:
        start = first if days is None else last - timedelta(days=days - 1)
        name = "history-all.json" if days is None else f"history-{days}d.json"
        answers.append(recorder.get(name, f"/api/v1/forecasts/history?start={start}"))

    periods = by_file["metrics-bucket.json"].document()["data"]["periods"]
    for period in periods:
        name = str(period["name"])
        answers.append(
            recorder.get(
                f"metrics-horizon-{name}.json",
                f"/api/v1/forecasts/metrics?scope=horizon&period={name}",
            )
        )

    scenarios = by_file["scenarios.json"].document()["data"]["scenarios"]
    for scenario in scenarios:
        identifier = str(scenario["scenario_id"])
        answers.append(
            recorder.get(f"scenario-{identifier}.json", f"/api/v1/scenarios/{identifier}")
        )
    return answers


# --- Checks -------------------------------------------------------------------------------


def _strings(value: object) -> list[str]:
    """Every string in a JSON value, keys included."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [s for key, item in value.items() for s in (str(key), *_strings(item))]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


def unsafe_text(document: object, repo_root: Path) -> list[str]:
    """What in ``document`` must not be published: '<what>: <text>' for each finding."""
    found: list[str] = []
    root = str(repo_root.resolve())
    for text in _strings(document):
        if root in text:
            found.append(f"the repository's local path: {text[:120]}")
        for name, pattern in _UNSAFE:
            match = pattern.search(text)
            if match is not None:
                found.append(f"{name} ({match.group(0)!r}): {text[:120]}")
        address = _compressed_ipv6(text)
        if address is not None:
            found.append(f"an IPv6 address ({address!r}): {text[:120]}")
    return found


#: A candidate for a compressed IPv6 address (with ``::``), confirmed by ``ipaddress``.
_IPV6_CANDIDATE: Final = re.compile(
    r"(?<![\w:.])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![\w:.])", re.I
)


def _compressed_ipv6(text: str) -> str | None:
    for match in _IPV6_CANDIDATE.finditer(text):
        candidate = match.group(0)
        groups = [group for group in candidate.split(":") if group]
        if "::" not in candidate or len(groups) < 2:
            continue
        try:
            ipaddress.IPv6Address(candidate)
        except ValueError:
            continue
        return candidate
    return None


def _source_problems(document: Mapping[str, Any], repo_root: Path) -> list[str]:
    problems: list[str] = []
    root = repo_root.resolve()
    for source in document["provenance"]["sources"]:
        path = PurePosixPath(str(source["path"]))
        if path.is_absolute() or ".." in path.parts or "\\" in str(path):
            problems.append(f"source path {str(path)!r} is not repository-relative")
            continue
        for name, sha256 in source["files"].items():
            target = (root / str(path) / str(name)).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                problems.append(f"source file {path}/{name} is not a file of the repository")
                continue
            if not (isinstance(sha256, str) and _SHA256.fullmatch(sha256)):
                problems.append(f"source file {path}/{name} has no SHA-256")
            elif hashlib.sha256(target.read_bytes()).hexdigest() != sha256:
                problems.append(f"source file {path}/{name} differs from the SHA-256 named")
    return problems


def check_answer(answer: Answer, repo_root: Path, interval_status: str) -> list[str]:
    """Why ``answer`` cannot be published (empty: it can)."""
    document = answer.document()
    problems: list[str] = []
    for key in ("integrity", "provenance", "data"):
        if key not in document:
            problems.append(f"no {key} block")
    if problems:
        return problems
    integrity = document["integrity"]
    for flag in ("validated_recommendation", "point_forecast_validated"):
        if integrity.get(flag) is not False:
            problems.append(f"integrity.{flag} is not false")
    if integrity.get("uncertainty_interval_status") != interval_status:
        problems.append("its M3 interval status differs from the uncertainty status answer")
    if not integrity.get("notices"):
        problems.append("it carries no notice")
    if answer.request.startswith(_M4_PREFIXES) and (
        integrity.get("parameter_provenance") != "HYPOTHETICAL"
    ):
        problems.append("an M4 answer that is not labelled HYPOTHETICAL")
    if document["provenance"].get("atlas_version") != __version__:
        problems.append("its ATLAS version is not this package's")
    problems.extend(_source_problems(document, repo_root))
    problems.extend(f"unsafe to publish, {item}" for item in unsafe_text(document, repo_root))
    return problems


# --- The manifest -------------------------------------------------------------------------


def _sources(answers: Sequence[Answer]) -> list[dict[str, Any]]:
    """Every source behind the answers, once, with every file any answer names."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for answer in answers:
        for source in answer.document()["provenance"]["sources"]:
            key = (str(source["kind"]), str(source["path"]))
            seen = merged.get(key)
            if seen is None:
                merged[key] = {**source, "files": dict(sorted(source["files"].items()))}
                continue
            for field in ("record_type", "run_id", "result_fingerprint", "config_fingerprint"):
                if seen[field] != source[field]:
                    raise StaticExportError(
                        f"two answers name source {key[1]} with different {field} values"
                    )
            for name, sha256 in source["files"].items():
                if seen["files"].setdefault(name, sha256) != sha256:
                    raise StaticExportError(f"two answers name {key[1]}/{name} with two hashes")
            seen["files"] = dict(sorted(seen["files"].items()))
    return [merged[key] for key in sorted(merged)]


def _scientific_status(by_file: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """What the exported answers record (read from them; nothing is asserted here)."""
    status = by_file["uncertainty-status.json"]["data"]
    models = by_file["models.json"]
    forecast = by_file["forecast.json"]["data"]
    scenarios = by_file["scenarios.json"]["data"]["scenarios"]
    h1 = next(
        (t for t in models["data"]["primary_tests"] if str(t["hypothesis"]).startswith("H1")),
        None,
    )
    notices = models["integrity"]["notices"]
    integrity = [document["integrity"] for document in by_file.values()]
    return {
        # True if any answer claimed it (check_answer refuses such an answer first).
        "point_forecast_validated": any(
            i["point_forecast_validated"] is not False for i in integrity
        ),
        "validated_recommendation": any(
            i["validated_recommendation"] is not False for i in integrity
        ),
        "uncertainty_interval_status": status["interval_status"],
        "uncertainty_decision_grade": status["decision_grade"],
        "m2_recommended_model": models["data"]["recommended_model"],
        "m2_h1_significant_after_holm": None if h1 is None else h1["significant_after_holm"],
        "m2_h1_holm_adjusted_p_value": None if h1 is None else h1["holm_adjusted_p_value"],
        "m2_selection_evidence_notice": any(
            str(n).startswith("M2 SELECTION EVIDENCE") for n in notices
        ),
        "projection_record_type": forecast["record_type"],
        "projection_origin": forecast["origin"],
        "projection_first_target": forecast["points"][0]["target_date"],
        "projection_last_target": forecast["points"][-1]["target_date"],
        "projection_archived_notice": any(
            str(n).startswith("ARCHIVED PROJECTION")
            for n in by_file["forecast.json"]["integrity"]["notices"]
        ),
        "m4_parameter_provenance": by_file["scenarios.json"]["integrity"]["parameter_provenance"],
        "m4_scenarios": len(scenarios),
        "m4_scenarios_meeting_every_constraint": sum(
            1 for s in scenarios if s["feasibility"] == "strict"
        ),
    }


def build_manifest(answers: Sequence[Answer]) -> dict[str, Any]:
    by_file = {a.file: a.document() for a in answers}
    history = by_file["history.json"]
    files = {}
    for answer in answers:
        integrity = by_file[answer.file]["integrity"]
        files[answer.file] = {
            "request": answer.request,
            "sha256": answer.sha256,
            "bytes": len(answer.body),
            "data_status": integrity["data_status"],
            "record_type": integrity["record_type"],
        }
    return {
        "format": EXPORT_FORMAT,
        "atlas_version": __version__,
        "api_version": API_VERSION,
        "description": (
            "Recorded GET answers of the ATLAS API over the committed research outputs, "
            "byte for byte, for a static read-only dashboard. Archived research results: "
            "nothing here is a live forecast or a validated recommendation."
        ),
        "snapshot": {
            "snapshot_id": history["provenance"]["snapshot_id"],
            "data_fingerprint": history["provenance"]["data_fingerprint"],
            "first_date": history["data"]["snapshot_first_date"],
            "last_date": history["data"]["snapshot_last_date"],
        },
        "scientific_status": _scientific_status(by_file),
        "files": files,
        "sources": _sources(answers),
        "not_exported": [{"request": r, "reason": why} for r, why in NOT_EXPORTED],
    }


def manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


# --- Export and verification --------------------------------------------------------------


def record(repo_root: Path, snapshot_id: str) -> tuple[list[Answer], dict[str, Any]]:
    """Record and check every answer (raises :class:`StaticExportError` on any problem)."""
    recorder = Recorder(export_settings(repo_root, snapshot_id))
    try:
        answers = plan_and_record(recorder)
    finally:
        recorder.close()
    names = [a.file for a in answers]
    if len(set(names)) != len(names) or MANIFEST_NAME in names:
        raise StaticExportError("two exported answers share a file name")
    interval_status = str(
        next(a for a in answers if a.file == "uncertainty-status.json").document()["data"][
            "interval_status"
        ]
    )
    problems = [
        f"{answer.request}: {problem}"
        for answer in answers
        for problem in check_answer(answer, repo_root, interval_status)
    ]
    if problems:
        raise StaticExportError(
            f"{len(problems)} problem(s), nothing written: " + "; ".join(problems[:10])
        )
    return answers, build_manifest(answers)


def _owned(directory: Path) -> list[Path]:
    """The files of an existing export (refuses a directory holding anything else)."""
    if not directory.exists():
        return []
    if not directory.is_dir() or directory.is_symlink():
        raise StaticExportError(f"{directory} is not a directory")
    entries = sorted(directory.iterdir())
    if entries and not (directory / MANIFEST_NAME).is_file():
        raise StaticExportError(
            f"{directory} is not empty and holds no {MANIFEST_NAME}: refusing to replace it"
        )
    strangers = [
        p.name
        for p in entries
        if not (p.is_file() and not p.is_symlink()) or not _FILE_NAME.fullmatch(p.name)
    ]
    if strangers:
        raise StaticExportError(
            f"{directory} holds files an export never writes ({', '.join(strangers[:5])}): "
            "refusing to replace it"
        )
    return entries


def write_static_export(repo_root: Path, snapshot_id: str, output: Path) -> CheckReport:
    """Export into ``output`` (created, or an earlier export replaced)."""
    answers, manifest = record(repo_root, snapshot_id)
    for stale in _owned(output):
        stale.unlink()
    output.mkdir(parents=True, exist_ok=True)
    for answer in answers:
        (output / answer.file).write_bytes(answer.body)
    (output / MANIFEST_NAME).write_bytes(manifest_bytes(manifest))
    return CheckReport(
        name="static export",
        facts={
            "output": str(output),
            "files": len(answers),
            "bytes": sum(len(a.body) for a in answers),
        },
    )


def _directory_issues(directory: Path) -> tuple[list[Issue], dict[str, Any] | None]:
    path = directory / MANIFEST_NAME
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [_error("manifest", f"{path} cannot be read as JSON ({type(exc).__name__})")], None
    if not isinstance(manifest, dict) or manifest.get("format") != EXPORT_FORMAT:
        return [_error("manifest", f"{path} is not an {EXPORT_FORMAT} manifest")], None
    listed: Mapping[str, Any] = manifest.get("files", {})
    present = {p.name for p in directory.iterdir()} - {MANIFEST_NAME}
    issues: list[Issue] = []
    unlisted = sorted(present - set(listed))
    missing = sorted(set(listed) - present)
    altered = sorted(
        name
        for name in set(listed) & present
        if (directory / name).is_symlink()
        or hashlib.sha256((directory / name).read_bytes()).hexdigest() != listed[name]["sha256"]
        or (directory / name).stat().st_size != listed[name]["bytes"]
    )
    for code, names, text in (
        ("unlisted", unlisted, "files the manifest does not list"),
        ("missing", missing, "files the manifest lists are missing"),
        ("altered", altered, "files differ from their recorded SHA-256 or size"),
    ):
        if names:
            issues.append(_error(code, text, names))
    return issues, manifest


def _error(code: str, message: str, names: Sequence[str] = ()) -> Issue:
    return Issue(
        code=f"static_export_{code}",
        severity=Severity.ERROR,
        subject="static export",
        message=message,
        count=max(1, len(names)),
        examples=examples(names),
    )


def verify_static_export(repo_root: Path, snapshot_id: str, directory: Path) -> list[CheckReport]:
    """Check ``directory`` against its manifest, then export again and require the same
    bytes for every file and for the manifest."""
    issues, manifest = _directory_issues(directory)
    reports = [
        CheckReport(
            name="static export files",
            issues=tuple(issues),
            facts={"directory": str(directory), "files": len((manifest or {}).get("files", {}))},
        )
    ]
    if manifest is None:
        return reports
    try:
        answers, regenerated = record(repo_root, snapshot_id)
    except StaticExportError as exc:
        reports.append(
            CheckReport(name="static export reproduction", issues=(_error("refused", str(exc)),))
        )
        return reports
    differing = sorted(
        a.file
        for a in answers
        if not (directory / a.file).is_file() or (directory / a.file).read_bytes() != a.body
    )
    extra = sorted(set(manifest.get("files", {})) - {a.file for a in answers})
    found: list[Issue] = []
    if differing:
        found.append(_error("differs", "files differ from a new export", differing))
    if extra:
        found.append(_error("extra", "files a new export does not produce", extra))
    if (directory / MANIFEST_NAME).read_bytes() != manifest_bytes(regenerated):
        found.append(_error("manifest_differs", "the manifest differs from a new export's"))
    reports.append(
        CheckReport(
            name="static export reproduction",
            issues=tuple(found),
            facts={"files": len(answers), "identical": not found},
        )
    )
    return reports
