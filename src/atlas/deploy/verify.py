"""Health-based verification of a running deployment (``atlas-deploy verify``).

Run after every activation (and by hand at any time), against the API on loopback. Each
check names what it expected and what it saw; the deployment is accepted only if every
check passes:

1. **liveness**: ``/health/live`` answers within the start-up deadline;
2. **readiness**: ``/health/ready`` is 200 with every component ready (otherwise the
   components that are not ready, with their details);
3. **identity**: ``/api/v1/system`` reports the release's ATLAS version and data
   fingerprint, and the process serving it runs from the release's directory;
4. **scientific integrity**: the M3 status is the committed one (``failed_calibration``),
   intervals are not decision-grade, M4 values are ``HYPOTHETICAL`` and nothing is a
   validated recommendation;
5. **access control**: a simulation without the key is refused (401) and, with it, one
   bounded smoke simulation answers 200 with its HYPOTHETICAL label. The key is sent only
   to the service itself: the socket listening on the API port must belong to the
   service's main process (any local account could listen there while it is down), and
   the request without the key must have been refused;
6. **exposure**: the API listens on loopback addresses only;
7. **proxy socket**: the Unix socket nginx connects to answers ``/health/live``;
8. (M7) **dashboard**: the API serves the release's dashboard (``/`` is the recorded
   ``index.html``, with the Content-Security-Policy the release records) and its
   deployment descriptor (in the format the release records); in an exposed profile, the
   copy published for nginx holds exactly the recorded files. (A release built before
   1.0.0 records no descriptor format: it is held to this tool's, unchanged since M7.)

Verification talks to the API on loopback and reads the published copy: it does not see
what nginx has loaded. Every activation, rollback and recovery reloads nginx before it
verifies (:mod:`atlas.deploy.releases`).

The HTTP client is the standard library's, never configured with a proxy, and reads at
most 1 MiB per response. Answers are untrusted: a malformed body fails a check, never the
verification itself.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

from atlas.dashboard import DASHBOARD_CSP, DESCRIPTOR_FORMAT, DESCRIPTOR_PATH, MANIFEST_NAME
from atlas.deploy.preflight import parse_listeners

MAX_BODY: Final = 1 << 20
SMOKE_REQUEST: Final = {"horizon_days": 7}
_NO_PROXY: Final = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    remedy: str = ""


@dataclass
class Verification:
    checks: list[Check] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(check.ok for check in self.checks)

    def add(self, name: str, ok: bool, detail: str, remedy: str = "") -> bool:
        self.checks.append(Check(name, ok, detail, remedy))
        return ok

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.ok]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [asdict(check) for check in self.checks],
            "timings_ms": self.timings_ms,
        }

    def render(self) -> str:
        lines = [f"verification: {'PASSED' if self.ok else 'FAILED'}"]
        for check in self.checks:
            lines.append(f"  [{'ok' if check.ok else 'FAIL':4}] {check.name}: {check.detail}")
            if not check.ok and check.remedy:
                lines.append(f"         remedy: {check.remedy}")
        if self.timings_ms:
            timings = ", ".join(f"{k} {v:.0f} ms" for k, v in self.timings_ms.items())
            lines.append(f"  latency: {timings}")
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    headers: Mapping[str, str]
    elapsed_ms: float

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None


def request(
    url: str,
    *,
    method: str = "GET",
    body: Any = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 15.0,
) -> Response | None:
    """One HTTP request (no proxy, no redirect handling); ``None`` if no answer."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    sent = dict(headers or {})
    if data is not None:
        sent.setdefault("Content-Type", "application/json")
    started = time.perf_counter()
    try:
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"not an HTTP URL: {url!r}")
        prepared = urllib.request.Request(url, data=data, method=method, headers=sent)  # noqa: S310
        with _NO_PROXY.open(prepared, timeout=timeout) as answer:
            payload = answer.read(MAX_BODY)
            return Response(answer.status, payload, dict(answer.headers), _ms(started))
    except urllib.error.HTTPError as error:
        payload = error.read(MAX_BODY)
        return Response(error.code, payload, dict(error.headers), _ms(started))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _ms(started: float) -> float:
    return round(1000 * (time.perf_counter() - started), 1)


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._path)
        except OSError:
            sock.close()
            raise
        self.sock = sock


def unix_request(path: Path, url_path: str, *, timeout: float = 10.0) -> Response | None:
    """``GET url_path`` over the Unix socket ``path``; ``None`` if no answer."""
    started = time.perf_counter()
    connection = _UnixConnection(str(path), timeout)
    try:
        connection.request("GET", url_path, headers={"Host": "localhost"})
        answer = connection.getresponse()
        payload = answer.read(MAX_BODY)
        return Response(answer.status, payload, dict(answer.headers), _ms(started))
    except (OSError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def _obj(value: Any) -> dict[str, Any]:
    """``value`` if it is a JSON object, else an empty one (answers are untrusted)."""
    return value if isinstance(value, dict) else {}


Requester = Callable[..., "Response | None"]


@dataclass
class Target:
    """What the deployment is expected to be."""

    base_url: str = "http://127.0.0.1:8000"
    manifest: Mapping[str, Any] | None = None
    api_key: str | None = None
    workers: int | None = None
    #: The release directory the service's main process must run from.
    release_dir: Path | None = None
    main_pid: int | None = None
    api_port: int = 8000
    smoke_simulation: bool = True
    #: The Unix socket nginx connects to (checked when set).
    api_socket: Path | None = None
    #: (M7) The dashboard copy published for nginx (checked when set).
    dashboard_dir: Path | None = None


def wait_live(
    target: Target,
    verification: Verification,
    *,
    timeout: float,
    requester: Requester = request,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    deadline = clock() + timeout
    last = "no answer"
    while True:
        answer = requester(f"{target.base_url}/health/live", timeout=5.0)
        if answer is not None and answer.status == 200:
            verification.timings_ms["health_live"] = answer.elapsed_ms
            return verification.add("liveness", True, "/health/live answered 200")
        last = "no answer" if answer is None else f"HTTP {answer.status}"
        if clock() >= deadline:
            return verification.add(
                "liveness",
                False,
                f"/health/live: {last} after {timeout:.0f} s",
                "journalctl -u atlas -n 100 (the service may have failed to start)",
            )
        sleep(1.0)


def verify(
    target: Target,
    *,
    timeout: float = 90.0,
    requester: Requester = request,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    proc: Path = Path("/proc"),
) -> Verification:
    """Run every check against ``target`` (see the module docstring)."""
    verification = Verification()
    if not wait_live(
        target, verification, timeout=timeout, requester=requester, sleep=sleep, clock=clock
    ):
        return verification
    base = target.base_url
    _readiness(verification, requester(f"{base}/health/ready"))
    system = requester(f"{base}/api/v1/system")
    _identity(verification, system, target, proc)
    _integrity(
        verification,
        system,
        requester(f"{base}/api/v1/uncertainty/status"),
        requester(f"{base}/api/v1/scenarios"),
        target,
    )
    if target.smoke_simulation:
        owner = _listener_owner(target, proc)
        _simulation(verification, base, target.api_key, requester, owner)
    _exposure(verification, target.api_port, proc)
    if target.api_socket is not None:
        _proxy_socket(verification, target.api_socket)
    record = _obj((target.manifest or {}).get("dashboard"))
    if record:
        _dashboard(
            verification,
            requester(f"{base}/"),
            requester(f"{base}{DESCRIPTOR_PATH}"),
            record,
            target.dashboard_dir,
        )
    for name, answer in (("system", system),):
        if answer is not None:
            verification.timings_ms[name] = answer.elapsed_ms
    return verification


def _readiness(verification: Verification, answer: Response | None) -> None:
    body = answer.json() if answer is not None else None
    components = _obj(body).get("components")
    components = components if isinstance(components, list) else []
    missing = [
        f"{c.get('component')} ({c.get('detail')})"
        for c in components
        if isinstance(c, dict) and not c.get("ready")
    ]
    if answer is not None and answer.status == 200 and components and not missing:
        verification.add("readiness", True, f"{len(components)} components ready")
        verification.timings_ms["health_ready"] = answer.elapsed_ms
        return
    detail = "no answer" if answer is None else f"HTTP {answer.status}"
    verification.add(
        "readiness",
        False,
        f"{detail}; not ready: {', '.join(missing) or 'unknown'}",
        "A damaged or missing artifact in the release; rebuild it (the previous release "
        "is restored automatically by an activation).",
    )


def _identity(
    verification: Verification, answer: Response | None, target: Target, proc: Path
) -> None:
    body = answer.json() if answer is not None else None
    if answer is None or answer.status != 200 or not isinstance(body, dict):
        verification.add("identity", False, "/api/v1/system did not answer 200")
        return
    data = _obj(body.get("data"))
    provenance = _obj(body.get("provenance"))
    manifest = target.manifest
    if manifest is not None:
        expected_version = manifest.get("atlas_version")
        verification.add(
            "version",
            data.get("atlas_version") == expected_version,
            f"running {data.get('atlas_version')}, release {expected_version}",
            "The service is not running the activated release; check the current link.",
        )
        expected_data = _obj(manifest.get("snapshot")).get("data_fingerprint")
        verification.add(
            "data fingerprint",
            provenance.get("data_fingerprint") == expected_data,
            f"served {str(provenance.get('data_fingerprint'))[:16]}..., release "
            f"{str(expected_data)[:16]}...",
            "The served data is not the release's committed snapshot.",
        )
    workers = _obj(data.get("simulation_workers"))
    if target.workers is not None:
        verification.add(
            "simulation workers",
            workers.get("workers") == target.workers and workers.get("alive") == target.workers,
            f"{workers.get('alive')} of {workers.get('workers')} alive, "
            f"{target.workers} configured",
            "journalctl -u atlas | grep worker",
        )
    limits = _obj(data.get("limits"))
    if target.api_key is not None:
        verification.add(
            "simulation key required",
            limits.get("simulation_requires_api_key") is True,
            f"simulation_requires_api_key={limits.get('simulation_requires_api_key')}",
            "The service did not load its key credential (LoadCredential / ATLAS_API_KEY_FILE).",
        )
    if target.release_dir is not None and target.main_pid is not None:
        try:
            cwd = (proc / str(target.main_pid) / "cwd").readlink()
        except OSError as exc:
            verification.add(
                "process release", False, f"cannot read the process cwd ({type(exc).__name__})"
            )
        else:
            expected = target.release_dir.resolve()
            verification.add(
                "process release",
                cwd == expected,
                f"main process {target.main_pid} runs in {cwd}",
                "Restart the service after switching the current link.",
            )


def _integrity(
    verification: Verification,
    system: Response | None,
    status: Response | None,
    scenarios: Response | None,
    target: Target,
) -> None:
    expected = (
        (target.manifest or {})
        .get("scientific_status", {})
        .get("m3_interval_status", "failed_calibration")
    )
    problems: list[str] = []
    for name, answer in (
        ("system", system),
        ("uncertainty/status", status),
        ("scenarios", scenarios),
    ):
        body = answer.json() if answer is not None else None
        if answer is None or answer.status != 200 or not isinstance(body, dict):
            problems.append(f"{name} did not answer 200")
            continue
        integrity = _obj(body.get("integrity"))
        if integrity.get("validated_recommendation") is not False:
            problems.append(f"{name}: validated_recommendation is not false")
        if integrity.get("point_forecast_validated") is not False:
            problems.append(f"{name}: point_forecast_validated is not false")
        if integrity.get("uncertainty_interval_status") != expected:
            problems.append(
                f"{name}: interval status {integrity.get('uncertainty_interval_status')}, "
                f"committed {expected}"
            )
        if integrity.get("uncertainty_decision_grade") is not False:
            problems.append(f"{name}: intervals reported as decision-grade")
        if name == "scenarios" and integrity.get("parameter_provenance") != "HYPOTHETICAL":
            problems.append("scenarios: M4 values are not labelled HYPOTHETICAL")
        if name == "uncertainty/status":
            data = _obj(body.get("data"))
            if data.get("interval_status") != expected or data.get("decision_grade") is not False:
                problems.append("uncertainty/status: the M3 status differs from the committed one")
    verification.add(
        "scientific integrity",
        not problems,
        "; ".join(problems)
        if problems
        else f"M3 {str(expected).upper()}, M4 HYPOTHETICAL, validated_recommendation false",
        "Do not serve this release: its integrity labels differ from the committed results.",
    )


def _listener_owner(target: Target, proc: Path) -> str | None:
    """Why the API port's listener is not provably the service (``None``: it is)."""
    if target.main_pid is None:
        return "the service's main process is unknown"

    def read(name: str) -> str | None:
        try:
            return (proc / "net" / name).read_text(encoding="utf-8")
        except OSError:
            return None

    inodes = {
        item.inode
        for item in parse_listeners(read("tcp"), read("tcp6"))
        if item.port == target.api_port
    }
    if not inodes:
        return f"nothing listens on port {target.api_port}"
    try:
        descriptors = list((proc / str(target.main_pid) / "fd").iterdir())
        held = {str(d.readlink()) for d in descriptors if d.is_symlink()}
    except OSError as exc:
        return f"the main process's sockets cannot be read ({type(exc).__name__})"
    if not all(f"socket:[{inode}]" in held for inode in inodes):
        return f"port {target.api_port} is held by another process than {target.main_pid}"
    return None


def _simulation(
    verification: Verification,
    base: str,
    key: str | None,
    requester: Requester,
    not_owned: str | None = None,
) -> None:
    url = f"{base}/api/v1/simulations"
    if key is None:
        verification.add(
            "simulation",
            False,
            "no API key available to run the smoke simulation",
            "Run as root (the key is /etc/atlas/credentials/api-key).",
        )
        return
    refused = requester(url, method="POST", body=SMOKE_REQUEST)
    without_key = refused is not None and refused.status == 401
    verification.add(
        "simulation without key",
        without_key,
        "refused with 401"
        if without_key
        else f"answered {None if refused is None else refused.status}",
        "The simulation endpoint must require the key.",
    )
    if not_owned is not None or not without_key:
        verification.add(
            "smoke simulation",
            False,
            "not run: the key is not sent to "
            + (not_owned or "a listener that does not require it"),
            "Check what listens on the API port: ss -ltnp; systemctl status atlas",
        )
        return
    answer = requester(
        url, method="POST", body=SMOKE_REQUEST, headers={"X-API-Key": key}, timeout=60.0
    )
    body = answer.json() if answer is not None else None
    data = _obj(_obj(body).get("data"))
    label = str(data.get("label", ""))
    ok = (
        answer is not None
        and answer.status == 200
        and "HYPOTHETICAL" in label
        and _obj(_obj(body).get("integrity")).get("validated_recommendation") is False
    )
    if answer is not None:
        verification.timings_ms["simulation"] = answer.elapsed_ms
    verification.add(
        "smoke simulation",
        ok,
        f"{data.get('horizon_days')}-day simulation {str(data.get('simulation_id', ''))[:12]}..., "
        f"{_obj(data.get('outcome')).get('feasibility')}"
        if ok
        else f"answered {None if answer is None else answer.status}",
        "journalctl -u atlas | grep simulation",
    )


def _exposure(verification: Verification, port: int, proc: Path) -> None:
    def read(name: str) -> str | None:
        try:
            return (proc / "net" / name).read_text(encoding="utf-8")
        except OSError:
            return None

    listeners = [item for item in parse_listeners(read("tcp"), read("tcp6")) if item.port == port]
    public = [f"{item.address}:{item.port}" for item in listeners if not item.loopback]
    verification.add(
        "loopback only",
        bool(listeners) and not public,
        f"listening on {', '.join(f'{i.address}:{i.port}' for i in listeners) or 'nothing'}",
        "The API must bind 127.0.0.1 only; check ExecStart in the unit."
        if public
        else "Is the service running?",
    )


def _header(answer: Response, name: str) -> str | None:
    return next((v for k, v in answer.headers.items() if k.lower() == name.lower()), None)


def _dashboard(
    verification: Verification,
    page: Response | None,
    descriptor: Response | None,
    record: Mapping[str, Any],
    published: Path | None,
) -> None:
    files = _obj(record.get("files"))
    expected = files.get("index.html")
    # What the release's own code sends (release.json), not what this tool's would: a later
    # tool restores and rolls back to older releases.
    policy = record.get("csp", DASHBOARD_CSP)
    descriptor_format = record.get("descriptor_format", DESCRIPTOR_FORMAT)
    digest = hashlib.sha256(page.body).hexdigest() if page is not None else None
    problems = []
    if page is None or page.status != 200:
        problems.append("/ did not answer 200")
    else:
        if digest != expected:
            problems.append("/ is not the release's index.html")
        if _header(page, "Content-Security-Policy") != policy:
            problems.append("/ lacks the dashboard's Content-Security-Policy")
    body = _obj(descriptor.json()) if descriptor is not None else {}
    if descriptor is None or descriptor.status != 200 or body.get("format") != descriptor_format:
        problems.append(f"{DESCRIPTOR_PATH} did not answer the descriptor")
    verification.add(
        "dashboard",
        not problems,
        "; ".join(problems) or f"dashboard {record.get('version')} served with its policy",
        "The API serves web/dist of the release (ATLAS_API_DASHBOARD); rebuild the release.",
    )
    if published is None:
        return
    found: dict[str, str] = {}
    try:
        for path in sorted(published.rglob("*")):
            relative = path.relative_to(published).as_posix()
            # As publishing checks it (atlas.dashboard.verify_build): a link, also to a
            # directory, is never part of the copy; a plain directory holds its files.
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                found[relative] = "not a regular file"
            elif path.is_file() and relative != MANIFEST_NAME:
                found[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        found = {"<unreadable>": type(exc).__name__}
    differ = sorted(p for p in set(found) | set(files) if found.get(p) != files.get(p))
    verification.add(
        "published dashboard",
        not differ,
        f"{published}: "
        + (
            f"{len(found)} files as recorded"
            if not differ
            else f"differs in {', '.join(differ[:5])}"
        ),
        "atlas-deploy activate <release> publishes it again from the release.",
    )


def _proxy_socket(verification: Verification, path: Path) -> None:
    answer = unix_request(path, "/health/live")
    verification.add(
        "proxy socket",
        answer is not None and answer.status == 200,
        f"{path}: /health/live answered " + ("nothing" if answer is None else str(answer.status)),
        "nginx reaches the API through this socket; check RuntimeDirectory and --uds in the unit",
    )
