"""Post-deployment verification against a real API, and the periodic monitor's decisions."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from atlas.deploy import cli, monitor
from atlas.deploy.manifest import load_manifest
from atlas.deploy.monitor import CRITICAL, OK, WARN, Probes, run_checks
from atlas.deploy.verify import Response, Target, Verification, request, verify
from tests.m6_helpers import TCP_HEADER, free_port, make_release, meminfo, tcp_line, write

if TYPE_CHECKING:
    from atlas.api.reference import ReferenceStore
    from atlas.api.simulation import SimulationService

KEY = "e" * 64


@pytest.fixture(scope="module")
def api(api_store: ReferenceStore, api_simulator: SimulationService) -> Iterator[int]:
    from tests.m5_helpers import live_server

    with live_server(api_store, api_simulator, api_key=KEY) as port:
        yield port


def fake_proc(
    tmp_path: Path, port: int, address: str = "127.0.0.1", cwd: Path | None = None
) -> Path:
    proc = tmp_path / "proc"
    write(proc / "net/tcp", TCP_HEADER + tcp_line(0, address, port, 77))
    write(proc / "net/tcp6", TCP_HEADER)
    if cwd is not None:
        (proc / "4242").mkdir(parents=True)
        (proc / "4242/cwd").symlink_to(cwd)
    return proc


def names(result: Verification, ok: bool) -> set[str]:
    return {check.name for check in result.checks if check.ok is ok}


# --- verify --------------------------------------------------------------------------------------


def test_a_healthy_deployment_verifies(tmp_path: Path, api: int) -> None:
    release = make_release(tmp_path)
    target = Target(
        base_url=f"http://127.0.0.1:{api}",
        manifest=load_manifest(release),
        api_key=KEY,
        workers=2,
        release_dir=release,
        main_pid=4242,
        api_port=api,
    )
    result = verify(target, timeout=10, proc=fake_proc(tmp_path, api, cwd=release))
    assert result.ok, result.render()
    assert names(result, True) >= {
        "liveness",
        "readiness",
        "version",
        "data fingerprint",
        "simulation workers",
        "simulation key required",
        "process release",
        "scientific integrity",
        "simulation without key",
        "smoke simulation",
        "loopback only",
    }
    assert "PASSED" in result.render()
    assert result.to_dict()["ok"] is True
    assert result.timings_ms["simulation"] > 0


def test_a_mismatched_release_fails_with_explicit_reasons(tmp_path: Path, api: int) -> None:
    release = make_release(tmp_path)
    manifest = load_manifest(release)
    manifest["atlas_version"] = "9.9.9"
    manifest["snapshot"]["data_fingerprint"] = "0" * 64
    manifest["scientific_status"]["m3_interval_status"] = "decision_grade"
    target = Target(
        base_url=f"http://127.0.0.1:{api}",
        manifest=manifest,
        api_key=None,
        workers=3,
        release_dir=tmp_path,
        main_pid=4242,
        api_port=api,
    )
    result = verify(
        target,
        timeout=5,
        proc=fake_proc(tmp_path, api, address="0.0.0.0", cwd=release),  # noqa: S104
    )
    assert not result.ok
    assert names(result, False) >= {
        "version",
        "data fingerprint",
        "simulation workers",
        "process release",
        "scientific integrity",
        "simulation",
        "loopback only",
    }
    text = result.render()
    assert "FAILED" in text
    assert "remedy:" in text
    assert "9.9.9" in text


def test_a_service_that_never_answers(tmp_path: Path) -> None:
    clock = iter(range(0, 1000, 10))
    result = verify(
        Target(base_url=f"http://127.0.0.1:{free_port()}"),
        timeout=25,
        sleep=lambda seconds: None,
        clock=lambda: float(next(clock)),
    )
    assert not result.ok
    assert [check.name for check in result.checks] == ["liveness"]
    assert "journalctl" in result.checks[0].remedy


def test_the_http_client_never_uses_a_proxy(monkeypatch: pytest.MonkeyPatch, api: int) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    answer = request(f"http://127.0.0.1:{api}/health/live")
    assert answer is not None
    assert answer.status == 200
    assert request("http://127.0.0.1:1/health/live") is None
    assert request("not a url") is None


# --- monitor --------------------------------------------------------------------------------------


def response(status: int, body: Any, ms: float = 5.0) -> Response:
    return Response(status, json.dumps(body).encode(), {}, ms)


def healthy_http(path: str) -> Response | None:
    if path == "/health/live":
        return response(200, {"status": "alive"})
    if path == "/health/ready":
        return response(200, {"status": "ready", "components": []})
    workers = {"workers": 2, "alive": 2, "idle": 2, "completed": 10, "timeouts": 0, "crashes": 0}
    return response(200, {"data": {"simulation_workers": workers}})


def probes(**changes: Any) -> Probes:
    files = {
        "/proc/meminfo": meminfo(2048, 1500, 1024),
        "/proc/loadavg": "0.20 0.30 0.30 1/100 1\n",
    }
    values: dict[str, Any] = {
        "show": lambda unit, props: {
            "ActiveState": "active",
            "SubState": "running",
            "NRestarts": "0",
            "MemoryCurrent": str(400 << 20),
            "MemoryMax": str(768 << 20),
            "CPUUsageNSec": "5000000000",
        },
        "http": healthy_http,
        "journal": lambda since: [],
        "read": files.get,
        "disk": lambda path: (20 << 30, 40 << 30),
        "cert_days": lambda domain, port: 60.0,
        "cpus": lambda: 2,
        "clock": lambda: 1000.0,
    }
    values.update(changes)
    return Probes(**values)


def test_a_healthy_run_is_ok_and_keeps_counters() -> None:
    report, state = run_checks(probes(), {}, tls_domain="atlas.example.org")
    assert report.status == OK, report.findings
    assert state["restarts"] == 0
    assert state["crashes"] == 0
    assert state["cpu_ns"] == 5_000_000_000
    payload = report.to_dict()
    assert payload["event"] == "monitor"
    assert payload["findings"] == []
    assert payload["metrics"]["service_memory_mib"] == 400
    assert payload["metrics"]["tls_days_left"] == 60.0


def test_cpu_use_is_measured_between_runs() -> None:
    report, _ = run_checks(probes(), {"cpu_ns": 4_000_000_000, "ts": 990.0})
    assert report.metrics["service_cpu_cores_used"] == 0.1


@pytest.mark.parametrize(
    ("changes", "state", "level", "check"),
    [
        (
            {"show": lambda u, p: {"ActiveState": "failed", "SubState": "failed"}},
            {},
            CRITICAL,
            "service",
        ),
        ({"http": lambda path: None}, {}, CRITICAL, "liveness"),
        (
            {
                "http": lambda path: (
                    response(
                        503,
                        {
                            "components": [
                                {"component": "snapshot", "ready": False, "detail": "altered"}
                            ]
                        },
                    )
                    if path == "/health/ready"
                    else healthy_http(path)
                )
            },
            {},
            CRITICAL,
            "readiness",
        ),
        (
            {
                "http": lambda path: (
                    response(200, {}, 2500.0) if path == "/health/ready" else healthy_http(path)
                )
            },
            {},
            WARN,
            "latency",
        ),
        ({}, {"crashes": -1}, WARN, "crashes"),
        ({}, {"timeouts": -1}, WARN, "timeouts"),
        ({}, {"restarts": -1}, WARN, "restarts"),
        (
            {"read": {"/proc/meminfo": meminfo(2048, 60, 0), "/proc/loadavg": "0 0 0"}.get},
            {},
            CRITICAL,
            "memory",
        ),
        (
            {"read": {"/proc/meminfo": meminfo(2048, 150, 0), "/proc/loadavg": "9 9 9"}.get},
            {},
            WARN,
            "memory",
        ),
        (
            {"read": {"/proc/meminfo": meminfo(2048, 1500, 0), "/proc/loadavg": "9 9 9"}.get},
            {},
            WARN,
            "cpu",
        ),
        ({"disk": lambda path: (100 << 20, 40 << 30)}, {}, CRITICAL, "disk"),
        ({"disk": lambda path: (800 << 20, 4 << 30)}, {}, WARN, "disk"),
        (
            {"journal": lambda since: [{"level": "ERROR", "event": "unhandled_exception"}]},
            {},
            WARN,
            "logs",
        ),
        (
            {"journal": lambda since: [{"level": "WARNING", "event": "simulation_timeout"}]},
            {},
            WARN,
            "logs",
        ),
        ({"cert_days": lambda d, p: None}, {}, CRITICAL, "tls"),
        ({"cert_days": lambda d, p: 10.0}, {}, WARN, "tls"),
        ({"cert_days": lambda d, p: 3.0}, {}, CRITICAL, "tls"),
        (
            {
                "show": lambda u, p: {
                    "ActiveState": "active",
                    "MemoryCurrent": str(750 << 20),
                    "MemoryMax": str(768 << 20),
                }
            },
            {},
            WARN,
            "memory",
        ),
    ],
)
def test_findings(changes: dict[str, Any], state: dict[str, Any], level: str, check: str) -> None:
    report, _ = run_checks(probes(**changes), state, tls_domain="atlas.example.org")
    assert report.status == level, report.findings
    assert any(f["check"] == check and f["level"] == level for f in report.findings), (
        report.findings
    )


def test_workers_missing_from_the_system_endpoint() -> None:
    def http(path: str) -> Response | None:
        if path == "/api/v1/system":
            return response(200, {"data": {"simulation_workers": {"workers": 2, "alive": 1}}})
        return healthy_http(path)

    report, _ = run_checks(probes(http=http), {})
    assert any(f["check"] == "workers" for f in report.findings)
    report, _ = run_checks(
        probes(http=lambda p: None if p == "/api/v1/system" else healthy_http(p)), {}
    )
    assert any(f["check"] == "workers" for f in report.findings)


def test_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state" / "state.json"
    monitor.save_state(path, {"restarts": 3})
    assert monitor.load_state(path) == {"restarts": 3}
    path.write_text("[]")
    assert monitor.load_state(path) == {}
    assert monitor.load_state(tmp_path / "missing.json") == {}


def test_the_monitor_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(monitor, "system_probes", lambda base, cafile=None: probes())
    out = io.StringIO()
    assert cli.main(["monitor", "--state-dir", str(tmp_path)], stdout=out) == 0
    assert json.loads(out.getvalue())["status"] == OK
    assert (tmp_path / "state.json").is_file()
    monkeypatch.setattr(
        monitor, "system_probes", lambda base, cafile=None: probes(http=lambda p: None)
    )
    monkeypatch.delenv("STATE_DIRECTORY", raising=False)
    out = io.StringIO()
    assert cli.main(["monitor"], stdout=out) == 1  # critical: the alert hook runs
    assert json.loads(out.getvalue())["status"] == CRITICAL


def test_the_real_probes_read_this_machine(api: int) -> None:
    real = monitor.system_probes(f"http://127.0.0.1:{api}")
    assert real.http("/health/live") is not None
    assert real.read("/proc/meminfo")
    assert real.disk("/") is not None
    assert real.disk("/no/such/path") is None
    assert real.cpus() >= 1
    assert real.cert_days("atlas.example.org", free_port()) is None
    assert isinstance(real.show("atlas.service", ["ActiveState"]), dict)
    assert isinstance(real.journal(0.0), list)
    assert Path("/proc/loadavg").exists()


def test_the_monitor_reads_systemd_and_the_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    write(bin_dir / "systemctl", "#!/bin/sh\nprintf 'ActiveState=active\\nNRestarts=2\\n'\n", 0o755)
    lines = [
        json.dumps({"MESSAGE": json.dumps({"level": "ERROR", "event": "unhandled_exception"})}),
        json.dumps({"MESSAGE": "not json"}),
        "garbage",
    ]
    write(bin_dir / "journalctl", "#!/bin/sh\ncat <<'EOF'\n" + "\n".join(lines) + "\nEOF\n", 0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    assert monitor._systemctl_show("atlas.service", ["ActiveState", "NRestarts"]) == {
        "ActiveState": "active",
        "NRestarts": "2",
    }
    assert monitor._journal(0.0) == [{"level": "ERROR", "event": "unhandled_exception"}]
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert monitor._systemctl_show("atlas.service", ["ActiveState"]) == {}
    assert monitor._journal(0.0) == []
