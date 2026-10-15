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
   bounded smoke simulation answers 200 with its HYPOTHETICAL label;
6. **exposure**: the API listens on loopback addresses only.

The HTTP client is the standard library's, never configured with a proxy, and reads at
most 1 MiB per response.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

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
        _simulation(verification, base, target.api_key, requester)
    _exposure(verification, target.api_port, proc)
    for name, answer in (("system", system),):
        if answer is not None:
            verification.timings_ms[name] = answer.elapsed_ms
    return verification


def _readiness(verification: Verification, answer: Response | None) -> None:
    body = answer.json() if answer is not None else None
    components = body.get("components", []) if isinstance(body, dict) else []
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
    data = body.get("data", {})
    provenance = body.get("provenance", {})
    manifest = target.manifest
    if manifest is not None:
        expected_version = manifest.get("atlas_version")
        verification.add(
            "version",
            data.get("atlas_version") == expected_version,
            f"running {data.get('atlas_version')}, release {expected_version}",
            "The service is not running the activated release; check the current link.",
        )
        expected_data = manifest.get("snapshot", {}).get("data_fingerprint")
        verification.add(
            "data fingerprint",
            provenance.get("data_fingerprint") == expected_data,
            f"served {str(provenance.get('data_fingerprint'))[:16]}..., release "
            f"{str(expected_data)[:16]}...",
            "The served data is not the release's committed snapshot.",
        )
    workers = data.get("simulation_workers") or {}
    if target.workers is not None:
        verification.add(
            "simulation workers",
            workers.get("workers") == target.workers and workers.get("alive") == target.workers,
            f"{workers.get('alive')} of {workers.get('workers')} alive, "
            f"{target.workers} configured",
            "journalctl -u atlas | grep worker",
        )
    limits = data.get("limits") or {}
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
        integrity = body.get("integrity", {})
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
            data = body.get("data", {})
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


def _simulation(
    verification: Verification, base: str, key: str | None, requester: Requester
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
    verification.add(
        "simulation without key",
        refused is not None and refused.status == 401,
        "refused with 401"
        if refused is not None and refused.status == 401
        else f"answered {None if refused is None else refused.status}",
        "The simulation endpoint must require the key.",
    )
    answer = requester(
        url, method="POST", body=SMOKE_REQUEST, headers={"X-API-Key": key}, timeout=60.0
    )
    body = answer.json() if answer is not None else None
    data = body.get("data", {}) if isinstance(body, dict) else {}
    label = str(data.get("label", ""))
    ok = (
        answer is not None
        and answer.status == 200
        and "HYPOTHETICAL" in label
        and isinstance(body, dict)
        and body.get("integrity", {}).get("validated_recommendation") is False
    )
    if answer is not None:
        verification.timings_ms["simulation"] = answer.elapsed_ms
    verification.add(
        "smoke simulation",
        ok,
        f"{data.get('horizon_days')}-day simulation {str(data.get('simulation_id', ''))[:12]}..., "
        f"{data.get('outcome', {}).get('feasibility')}"
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
