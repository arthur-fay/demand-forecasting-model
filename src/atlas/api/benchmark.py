"""``atlas api bench``: measured latency and memory of a real local API server.

The server runs as its own process (``python -m atlas api serve`` on a free loopback
port), exactly as in local use, and this process acts as the client over HTTP with the
standard library. Reported numbers are measurements of this machine, never estimates:

* startup time until ``/health/ready`` answers 200;
* sequential latency (min, median, p95, max) of every GET endpoint;
* simulation latency for four representative requests, with the worker's compute time
  from ``Server-Timing``;
* the latency of a metadata endpoint while every simulation worker is busy (isolation);
* how many simultaneous simulations above the worker count were refused with 503;
* resident memory of the server and of each worker process (Linux ``/proc``).
"""

from __future__ import annotations

import http.client
import json
import os
import platform
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Final

from atlas import __version__
from atlas.api.settings import ApiSettings
from atlas.errors import ConfigError

GET_ENDPOINTS: Final = (
    "/health/live",
    "/health/ready",
    "/api/v1/system",
    "/api/v1/models",
    "/api/v1/forecasts",
    "/api/v1/forecasts/history",
    "/api/v1/forecasts/history?start=2023-01-01&end=2026-06-30",
    "/api/v1/forecasts/metrics",
    "/api/v1/forecasts/metrics?scope=horizon",
    "/api/v1/uncertainty/status",
    "/api/v1/uncertainty/intervals",
    "/api/v1/scenarios",
    "/api/v1/scenarios/normal",
    "/api/v1/scenarios/comparison",
    "/api/v1/simulations/parameters",
)
SIMULATIONS: Final = {
    "reference_defaults_30_days": {},
    "budget_100k_infeasible": {"parameters": {"discretionary_budget": 100_000}},
    "q0.9_stress_path": {"demand": {"basis": "m3_marginal_quantile", "quantile": 0.9}},
    "14_days_no_target": {"horizon_days": 14, "parameters": {"terminal_backlog_max": None}},
}
STARTUP_TIMEOUT: Final = 120.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _request(
    port: int, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
) -> tuple[int, float, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    payload = None if body is None else json.dumps(body).encode("utf-8")
    sent = {"Host": "localhost", **(headers or {})}
    if payload is not None:
        sent["Content-Type"] = "application/json"
    started = time.perf_counter()
    try:
        connection.request(method, path, body=payload, headers=sent)
        response = connection.getresponse()
        data = response.read()
        elapsed = 1000 * (time.perf_counter() - started)
        return response.status, elapsed, {k.lower(): v for k, v in response.getheaders()}, data
    finally:
        connection.close()


def _stats(values: Sequence[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, max(0, round(0.95 * len(ordered)) - 1))]
    return {
        "n": len(ordered),
        "min_ms": round(ordered[0], 2),
        "median_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(p95, 2),
        "max_ms": round(ordered[-1], 2),
    }


def _rss_kib(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


def _children(pid: int) -> list[int]:
    children = []
    for entry in Path("/proc").iterdir() if Path("/proc").is_dir() else ():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        except OSError:
            continue
        if int(fields[1]) == pid:
            children.append(int(entry.name))
    return sorted(children)


def _wait_ready(port: int, process: subprocess.Popen[bytes]) -> float:
    started = time.perf_counter()
    while time.perf_counter() - started < STARTUP_TIMEOUT:
        if process.poll() is not None:
            raise ConfigError("the API server exited during startup")
        try:
            status, _, _, _ = _request(port, "GET", "/health/ready")
            if status == 200:
                return time.perf_counter() - started
            if status == 503:
                raise ConfigError("the API started but a reference component is not ready")
        except OSError:
            pass
        time.sleep(0.1)
    raise ConfigError("the API server did not become ready in time")


def run_benchmark(
    settings: ApiSettings, *, requests: int = 50, simulations: int = 10
) -> dict[str, Any]:
    """Start a server, measure it, stop it; return the measurements (see module doc)."""
    if not 1 <= requests <= 10_000 or not 1 <= simulations <= 1_000:
        raise ConfigError("--requests must be within 1..10000 and --simulations within 1..1000")
    port = _free_port()
    env = {**os.environ, "ATLAS_API_LOG_LEVEL": "WARNING", "ATLAS_API_DOCS": "false"}
    env.pop("ATLAS_API_KEY", None)
    command = [
        sys.executable,
        "-m",
        "atlas",
        "--repo-root",
        str(settings.repo_root),
        "--snapshot",
        settings.snapshot_id,
        "api",
        "serve",
        "--port",
        str(port),
    ]
    started = time.perf_counter()
    process = subprocess.Popen(  # noqa: S603 - fixed command: this interpreter, this package
        command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        ready_seconds = _wait_ready(port, process)
        report: dict[str, Any] = {
            "atlas_version": __version__,
            "machine": {
                "cpus": os.cpu_count(),
                "platform": platform.platform(),
                "python": platform.python_version(),
                "processor": platform.processor() or platform.machine(),
            },
            "server": {
                "workers": settings.max_concurrent_simulations,
                "spawn_to_ready_seconds": round(time.perf_counter() - started, 3),
                "health_ready_wait_seconds": round(ready_seconds, 3),
            },
        }
        report["simulation_first_request_ms"] = round(
            _request(port, "POST", "/api/v1/simulations", {})[1], 1
        )
        for _ in range(settings.max_concurrent_simulations):  # warm every worker
            _request(port, "POST", "/api/v1/simulations", {})
        report["get_endpoints"] = _measure_gets(port, requests)
        report["simulations"] = _measure_simulations(port, simulations)
        report["isolation"] = _measure_isolation(port, requests, settings)
        report["busy_refusals"] = _measure_refusals(port, settings)
        pids = _children(process.pid)
        report["memory_rss_mib"] = {
            "server": _mib(_rss_kib(process.pid)),
            "workers": [_mib(_rss_kib(pid)) for pid in pids],
        }
        return report
    finally:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _mib(kib: int | None) -> float | None:
    return None if kib is None else round(kib / 1024, 1)


def _measure_gets(port: int, requests: int) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for path in GET_ENDPOINTS:
        _request(port, "GET", path)
        times, sizes = [], set()
        for _ in range(requests):
            status, elapsed, _, data = _request(port, "GET", path)
            if status != 200:
                raise ConfigError(f"GET {path} answered {status}")
            times.append(elapsed)
            sizes.add(len(data))
        results[path] = {**_stats(times), "bytes": max(sizes)}
    return results


def _measure_simulations(port: int, count: int) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for name, body in SIMULATIONS.items():
        totals, computes, outcome = [], [], None
        for _ in range(count):
            status, elapsed, headers, data = _request(port, "POST", "/api/v1/simulations", body)
            if status != 200:
                raise ConfigError(f"simulation {name} answered {status}")
            totals.append(elapsed)
            timing = headers.get("server-timing", "compute;dur=nan").split("dur=")[1]
            computes.append(float(timing))
            outcome = json.loads(data)["data"]["outcome"]["feasibility"]
        results[name] = {
            "request": body,
            "feasibility": outcome,
            "total": _stats(totals),
            "compute": _stats(computes),
            "response_bytes": len(data),
        }
    return results


def _measure_isolation(port: int, requests: int, settings: ApiSettings) -> dict[str, Any]:
    idle = [_request(port, "GET", "/api/v1/forecasts")[1] for _ in range(requests)]
    stop = threading.Event()

    def keep_busy() -> int:
        done = 0
        while not stop.is_set():
            _request(port, "POST", "/api/v1/simulations", {})
            done += 1
        return done

    with ThreadPoolExecutor(max_workers=settings.max_concurrent_simulations) as pool:
        futures = [pool.submit(keep_busy) for _ in range(settings.max_concurrent_simulations)]
        time.sleep(0.5)
        loaded = [_request(port, "GET", "/api/v1/forecasts")[1] for _ in range(requests)]
        stop.set()
        completed = sum(f.result() for f in futures)
    return {
        "endpoint": "/api/v1/forecasts",
        "idle": _stats(idle),
        "while_all_workers_busy": _stats(loaded),
        "simulations_completed_meanwhile": completed,
    }


def _measure_refusals(port: int, settings: ApiSettings) -> dict[str, Any]:
    attempts = settings.max_concurrent_simulations + 4
    with ThreadPoolExecutor(max_workers=attempts) as pool:
        statuses = list(
            pool.map(
                lambda _: _request(port, "POST", "/api/v1/simulations", {})[0], range(attempts)
            )
        )
    return {
        "simultaneous_requests": attempts,
        "status_counts": {str(s): statuses.count(s) for s in sorted(set(statuses))},
    }
