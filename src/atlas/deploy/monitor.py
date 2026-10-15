"""Lightweight monitoring of a deployment (``atlas-deploy monitor``).

``atlas-monitor.timer`` runs it every minute as an unprivileged, sandboxed one-shot unit.
It needs no agent, no database and no outbound network. Each run checks:

* **process availability**: ``atlas.service`` active, its main process, restarts since the
  last run (``NRestarts``; three automatic restarts within an hour are critical);
* **API health**: liveness and readiness, with their latency;
* **simulation workers**: workers alive, and simulations that timed out or lost their
  worker since the last run (from ``/api/v1/system``);
* **resources**: the service's memory against its limit and its CPU use since the last
  run, the server's available memory, swap and load, and free disk space;
* **logs**: warnings and errors the API logged since the last run (``journalctl``);
* **TLS**: days until the served certificate expires (when a domain is given).

It prints one JSON object (into the journal: ``journalctl -u atlas-monitor``) with every
measurement and finding. The status is ``ok``, ``warn`` or ``critical``; a critical run
exits 1, which makes systemd start the optional alert hook (``atlas-alert@.service``).
Counters are kept in a small state file between runs.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import ssl
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from atlas.deploy.layout import SERVICE
from atlas.deploy.preflight import parse_meminfo
from atlas.deploy.verify import Response, request

OK: Final = "ok"
WARN: Final = "warn"
#: Automatic restarts within an hour that make the monitor critical (and alert).
RESTARTS_CRITICAL: Final = 3
RESTART_WINDOW_SECONDS: Final = 3600.0
CRITICAL: Final = "critical"
_RANK: Final = {OK: 0, WARN: 1, CRITICAL: 2}
SHOW_PROPERTIES: Final = (
    "ActiveState",
    "SubState",
    "MainPID",
    "NRestarts",
    "MemoryCurrent",
    "MemoryMax",
    "CPUUsageNSec",
)
DISKS: Final = ("/", "/opt/atlas", "/var/log")
SLOW_READY_MS: Final = 1000.0


@dataclass
class Probes:
    """Everything the monitor reads; the tests replace each one."""

    show: Callable[[str, Sequence[str]], dict[str, str]]
    http: Callable[[str], Response | None]
    journal: Callable[[float], list[dict[str, Any]]]
    read: Callable[[str], str | None]
    disk: Callable[[str], tuple[int, int] | None]
    cert_days: Callable[[str, int], float | None]
    cpus: Callable[[], int]
    clock: Callable[[], float] = time.time


def _systemctl_show(unit: str, properties: Sequence[str]) -> dict[str, str]:
    executable = shutil.which("systemctl")
    if executable is None:
        return {}
    try:
        completed = subprocess.run(  # noqa: S603 - fixed read-only command
            [executable, "show", unit, "-p", ",".join(properties)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    values: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values


def _journal(since: float) -> list[dict[str, Any]]:
    executable = shutil.which("journalctl")
    if executable is None:
        return []
    try:
        completed = subprocess.run(  # noqa: S603 - fixed read-only command
            [
                executable,
                "-u",
                SERVICE,
                "--since",
                f"@{int(since)}",
                "-o",
                "json",
                "--output-fields=MESSAGE",
                "-n",
                "5000",
                "-q",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    entries = []
    for line in completed.stdout.splitlines():
        try:
            record = json.loads(line)
            message = json.loads(record.get("MESSAGE", ""))
        except (ValueError, TypeError, AttributeError):
            continue
        if isinstance(message, dict):
            entries.append(message)
    return entries


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def _disk(path: str) -> tuple[int, int] | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return usage.free, usage.total


def certificate_days(
    domain: str, port: int = 443, *, address: str = "127.0.0.1", cafile: str | None = None
) -> float | None:
    """Days until the certificate served for ``domain`` on this host expires; ``None`` if
    no valid certificate could be obtained (an invalid certificate is not 'valid')."""
    context = ssl.create_default_context(cafile=cafile)
    try:
        with (
            socket.create_connection((address, port), timeout=5) as raw,
            context.wrap_socket(raw, server_hostname=domain) as tls,
        ):
            certificate = tls.getpeercert()
    except (OSError, ssl.SSLError):
        return None
    not_after = certificate.get("notAfter") if certificate else None
    if not isinstance(not_after, str):
        return None
    return (ssl.cert_time_to_seconds(not_after) - time.time()) / 86400


def _cpus() -> int:
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:  # pragma: no cover
        return max(1, os.cpu_count() or 1)


def system_probes(base_url: str, cafile: str | None = None) -> Probes:
    return Probes(
        show=_systemctl_show,
        http=lambda path: request(base_url + path, timeout=10.0),
        journal=_journal,
        read=_read,
        disk=_disk,
        cert_days=lambda domain, port: certificate_days(domain, port, cafile=cafile),
        cpus=_cpus,
    )


@dataclass
class Report:
    findings: list[dict[str, str]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def add(self, level: str, check: str, detail: str) -> None:
        self.findings.append({"level": level, "check": check, "detail": detail})

    @property
    def status(self) -> str:
        return max((f["level"] for f in self.findings), key=_RANK.__getitem__, default=OK)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": "monitor",
            "status": self.status,
            "findings": [f for f in self.findings if f["level"] != OK],
            "metrics": self.metrics,
        }


def _int(value: str | None) -> int | None:
    return int(value) if value is not None and value.isdigit() else None


def run_checks(
    probes: Probes,
    state: Mapping[str, Any],
    *,
    tls_domain: str | None = None,
    tls_port: int = 443,
) -> tuple[Report, dict[str, Any]]:
    """One monitoring run: the report and the state to keep for the next run."""
    report = Report()
    now = probes.clock()
    new_state: dict[str, Any] = {"ts": now}
    _service(report, probes, state, new_state, now)
    _api(report, probes, state, new_state)
    _resources(report, probes)
    _logs(report, probes, state, now)
    if tls_domain:
        days = probes.cert_days(tls_domain, tls_port)
        if days is None:
            report.add(CRITICAL, "tls", f"no valid certificate is served for {tls_domain}")
        else:
            report.metrics["tls_days_left"] = round(days, 1)
            level = CRITICAL if days < 7 else WARN if days < 21 else OK
            report.add(level, "tls", f"certificate expires in {days:.0f} days")
    return report, new_state


def _service(
    report: Report, probes: Probes, state: Mapping[str, Any], new: dict[str, Any], now: float
) -> None:
    shown = probes.show(SERVICE, SHOW_PROPERTIES)
    active = shown.get("ActiveState", "unknown")
    report.metrics["service_state"] = f"{active}/{shown.get('SubState', '?')}"
    if active != "active":
        report.add(CRITICAL, "service", f"{SERVICE} is {active} ({shown.get('SubState', '?')})")
    restarts = _int(shown.get("NRestarts"))
    if restarts is not None:
        new["restarts"] = restarts
        report.metrics["restarts_total"] = restarts
        before = state.get("restarts")
        # Restarts seen in the last hour (kept across runs): a slow crash loop, below the
        # unit's start limit, is critical too, not only a run of warnings.
        recent = [
            float(ts)
            for ts in state.get("restart_times", [])
            if isinstance(ts, int | float) and now - float(ts) < RESTART_WINDOW_SECONDS
        ]
        if isinstance(before, int) and restarts > before:
            recent += [now] * min(restarts - before, RESTARTS_CRITICAL)
            level = CRITICAL if len(recent) >= RESTARTS_CRITICAL else WARN
            report.add(
                level,
                "restarts",
                f"{restarts - before} automatic restart(s) since the last run, "
                f"{len(recent)} in the last hour",
            )
        new["restart_times"] = recent
    memory = _int(shown.get("MemoryCurrent"))
    limit = _int(shown.get("MemoryMax"))
    if memory is not None:
        report.metrics["service_memory_mib"] = memory // (1 << 20)
        if limit:
            share = memory / limit
            report.metrics["service_memory_share_of_limit"] = round(share, 3)
            if share > 0.9:
                report.add(WARN, "memory", f"the service uses {share:.0%} of its memory limit")
    cpu = _int(shown.get("CPUUsageNSec"))
    if cpu is not None:
        new["cpu_ns"] = cpu
        before_cpu, before_ts = state.get("cpu_ns"), state.get("ts")
        if isinstance(before_cpu, int) and isinstance(before_ts, int | float) and now > before_ts:
            share = (cpu - before_cpu) / 1e9 / (now - before_ts)
            if share >= 0:
                report.metrics["service_cpu_cores_used"] = round(share, 3)


def _api(report: Report, probes: Probes, state: Mapping[str, Any], new: dict[str, Any]) -> None:
    live = probes.http("/health/live")
    if live is None or live.status != 200:
        report.add(CRITICAL, "liveness", "no answer" if live is None else f"HTTP {live.status}")
        return
    report.metrics["live_ms"] = live.elapsed_ms
    ready = probes.http("/health/ready")
    if ready is None:
        report.add(CRITICAL, "readiness", "no answer")
    else:
        report.metrics["ready_ms"] = ready.elapsed_ms
        body = ready.json()
        if ready.status != 200:
            missing = [
                f"{c.get('component')}: {c.get('detail')}"
                for c in (body or {}).get("components", [])
                if isinstance(c, dict) and not c.get("ready")
            ]
            report.add(CRITICAL, "readiness", "not ready: " + "; ".join(missing))
        elif ready.elapsed_ms > SLOW_READY_MS:
            report.add(WARN, "latency", f"/health/ready took {ready.elapsed_ms:.0f} ms")
    system = probes.http("/api/v1/system")
    body = system.json() if system is not None and system.status == 200 else None
    workers = (
        (body or {}).get("data", {}).get("simulation_workers") if isinstance(body, dict) else None
    )
    if not isinstance(workers, dict):
        report.add(WARN, "workers", "/api/v1/system did not report the simulation workers")
        return
    report.metrics["system_ms"] = system.elapsed_ms if system is not None else None
    report.metrics["workers"] = {
        k: workers.get(k) for k in ("workers", "alive", "idle", "completed", "timeouts", "crashes")
    }
    if workers.get("alive", 0) < workers.get("workers", 0):
        report.add(
            WARN,
            "workers",
            f"{workers.get('alive')} of {workers.get('workers')} worker processes alive",
        )
    for counter, label in (
        ("timeouts", "simulation(s) stopped at the deadline"),
        ("crashes", "simulation(s) lost their worker"),
    ):
        value = workers.get(counter)
        if isinstance(value, int):
            new[counter] = value
            before = state.get(counter)
            if isinstance(before, int) and value > before:
                report.add(WARN, counter, f"{value - before} {label} since the last run")


def _resources(report: Report, probes: Probes) -> None:
    info = parse_meminfo(probes.read("/proc/meminfo") or "")
    total, available = info.get("MemTotal", 0), info.get("MemAvailable", 0)
    if total:
        share = available / total
        report.metrics["memory_available_share"] = round(share, 3)
        swap_total, swap_free = info.get("SwapTotal", 0), info.get("SwapFree", 0)
        report.metrics["swap_used_mib"] = (swap_total - swap_free) // (1 << 20)
        if share < 0.05:
            report.add(CRITICAL, "memory", f"only {share:.0%} of the server's memory is available")
        elif share < 0.10:
            report.add(WARN, "memory", f"only {share:.0%} of the server's memory is available")
    load = (probes.read("/proc/loadavg") or "").split()
    if len(load) >= 3:
        cpus = probes.cpus()
        five = float(load[1])
        report.metrics["load5_per_cpu"] = round(five / cpus, 2)
        if five > 2 * cpus:
            report.add(WARN, "cpu", f"load average {five:.1f} on {cpus} CPU(s)")
    seen: set[tuple[int, int]] = set()
    for path in DISKS:
        usage = probes.disk(path)
        if usage is None or usage in seen:  # several paths on one filesystem: report it once
            continue
        seen.add(usage)
        free, total_bytes = usage
        share = free / total_bytes if total_bytes else 0.0
        report.metrics[f"disk_free_mib:{path}"] = free // (1 << 20)
        if share < 0.05 or free < 256 << 20:
            report.add(CRITICAL, "disk", f"{path}: {free // (1 << 20)} MiB free ({share:.0%})")
        elif share < 0.15 or free < 1 << 30:
            report.add(WARN, "disk", f"{path}: {free // (1 << 20)} MiB free ({share:.0%})")


def _logs(report: Report, probes: Probes, state: Mapping[str, Any], now: float) -> None:
    since = state.get("ts")
    entries = probes.journal(float(since) if isinstance(since, int | float) else now - 120)
    counts: dict[str, int] = {}
    for entry in entries:
        level = entry.get("level")
        if level in ("WARNING", "ERROR", "CRITICAL"):
            event = str(entry.get("event") or entry.get("message") or "unknown")[:60]
            key = f"{level}:{event}"
            counts[key] = counts.get(key, 0) + 1
    report.metrics["log_warnings_errors"] = counts
    errors = sum(v for k, v in counts.items() if not k.startswith("WARNING"))
    warnings = sum(v for k, v in counts.items() if k.startswith("WARNING"))
    if errors:
        report.add(
            WARN, "logs", f"{errors} error line(s) since the last run: {', '.join(sorted(counts))}"
        )
    elif warnings:
        report.add(
            WARN,
            "logs",
            f"{warnings} warning line(s) since the last run: {', '.join(sorted(counts))}",
        )


def load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def save_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.new")
    temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
