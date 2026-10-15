"""Concurrency, isolation, deadlines and failures of the simulation worker pool.

These tests run a real uvicorn server (on a free loopback port) and real worker processes,
and talk to them over HTTP with the standard library, as a dashboard would.
"""

from __future__ import annotations

import http.client
import json
import logging
import multiprocessing
import os
import signal
import socket
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from atlas.api.reference import ReferenceStore
from atlas.api.schemas import SimulationRequest
from atlas.api.simulation import (
    SimulationPool,
    SimulationService,
    Timeout,
    prepare,
    worker_context,
)
from tests.m5_helpers import client, live_server
from tests.m5_workers import dying_worker, failing_worker, silent_worker, slow_worker

pytestmark = pytest.mark.integration


def call(
    port: int, method: str, path: str, body: Any = None, timeout: float = 120
) -> tuple[int, dict[str, str], bytes, float]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers = {"Host": "localhost"}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    started = time.perf_counter()
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        data = response.read()
        headers_out = {k.lower(): v for k, v in response.getheaders()}
        return response.status, headers_out, data, time.perf_counter() - started
    finally:
        connection.close()


@pytest.fixture(scope="module")
def server(api_store: ReferenceStore, api_simulator: SimulationService) -> Iterator[int]:
    with live_server(api_store, api_simulator) as port:
        yield port


def test_concurrent_reads_are_consistent(server: int) -> None:
    paths = ["/api/v1/forecasts", "/api/v1/scenarios/normal", "/api/v1/forecasts/metrics"] * 10
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda p: (p, call(server, "GET", p)), paths))
    bodies: dict[str, set[bytes]] = {}
    for path, (status, _, data, _) in results:
        assert status == 200
        bodies.setdefault(path, set()).add(data)
    assert all(len(variants) == 1 for variants in bodies.values())


def test_simultaneous_simulations_are_bounded_and_identical(server: int) -> None:
    body = {"horizon_days": 21, "parameters": {"backlog_penalty": 3.0}}
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(lambda _: call(server, "POST", "/api/v1/simulations", body), range(6))
        )
    statuses = [status for status, _, _, _ in results]
    assert set(statuses) <= {200, 503}
    assert statuses.count(200) >= 2  # two workers
    done = {data for status, _, data, _ in results if status == 200}
    assert len(done) == 1  # every accepted simulation gives the same bytes
    for status, headers, data, _ in results:
        if status == 503:
            assert headers["retry-after"] == "1"
            assert json.loads(data)["error"]["code"] == "simulations_busy"


def test_metadata_is_served_while_every_worker_computes(server: int) -> None:
    body = {"demand": {"basis": "m3_marginal_quantile", "quantile": 0.975}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(call, server, "POST", "/api/v1/simulations", body) for _ in range(2)]
        time.sleep(0.05)
        latencies = []
        for _ in range(20):
            status, _, _, elapsed = call(server, "GET", "/health/live")
            assert status == 200
            latencies.append(elapsed)
        outcomes = [f.result()[0] for f in futures]
    assert outcomes == [200, 200]
    # The solves run in other processes: the API answers without waiting for them.
    assert sorted(latencies)[len(latencies) // 2] < 0.25


def test_timeout_stops_the_worker_and_frees_the_slot(
    api_store: ReferenceStore, caplog: pytest.LogCaptureFixture
) -> None:
    service = SimulationService(api_store, size=1, timeout_seconds=1.0, worker_target=slow_worker)
    try:
        service.start()
        assert service.warm_up() == 1
        (before,) = service.worker_pids()
        with live_server(api_store, service) as port, caplog.at_level(logging.WARNING, "atlas.api"):
            started = time.perf_counter()
            status, _, data, _ = call(port, "POST", "/api/v1/simulations", {"horizon_days": 3})
            assert status == 504
            assert json.loads(data)["error"]["code"] == "simulation_timeout"
            assert time.perf_counter() - started < 10
        (after,) = service.worker_pids()
        assert after != before
        with pytest.raises(ProcessLookupError):
            os.kill(before, 0)  # the stopped computation no longer runs
        assert service.idle_workers == 1
        events = [r for r in caplog.records if getattr(r, "event", None) == "simulation_timeout"]
        assert [getattr(r, "worker_pid", None) for r in events] == [before]
        status_now = service.status()
        assert (status_now.timeouts, status_now.crashes, status_now.completed) == (1, 0, 0)
        assert service.health().ready  # a timeout is the input's, not the workers' fault
    finally:
        service.close()


def test_busy_pool_refuses_at_once(api_store: ReferenceStore) -> None:
    service = SimulationService(api_store, size=1, timeout_seconds=5.0, worker_target=slow_worker)
    try:
        service.start()
        assert service.warm_up() == 1
        with live_server(api_store, service) as port, ThreadPoolExecutor(1) as pool:
            first = pool.submit(call, port, "POST", "/api/v1/simulations", {"horizon_days": 3})
            time.sleep(0.5)
            status, headers, data, elapsed = call(
                port, "POST", "/api/v1/simulations", {"horizon_days": 3}
            )
            assert status == 503
            assert json.loads(data)["error"]["code"] == "simulations_busy"
            assert headers["retry-after"] == "1"
            assert elapsed < 1.0
            assert first.result()[0] == 504
    finally:
        service.close()


def test_a_dead_worker_is_replaced(
    api_store: ReferenceStore, caplog: pytest.LogCaptureFixture
) -> None:
    service = SimulationService(api_store, size=1, timeout_seconds=30.0, worker_target=dying_worker)
    try:
        service.start()
        with (
            client(api_store, service, raise_server_exceptions=False) as test_client,
            caplog.at_level(logging.WARNING, "atlas.api"),
        ):
            for attempt in range(1, 4):
                response = test_client.post("/api/v1/simulations", json={"horizon_days": 3})
                assert response.status_code == 500
                assert response.json()["error"]["code"] == "simulation_failed"
                ready = test_client.get("/health/ready")
                workers = {c["component"]: c for c in ready.json()["components"]}
                # Three consecutive crashes make the workers (and the server) not ready.
                assert ready.status_code == (503 if attempt == 3 else 200)
                assert workers["simulation_workers"]["ready"] is (attempt < 3)
            system = test_client.get("/api/v1/system").json()["data"]["simulation_workers"]
            assert (system["crashes"], system["consecutive_crashes"]) == (3, 3)
            assert service.warm_up() == 1  # a fresh worker answers
        crashed = [r for r in caplog.records if getattr(r, "event", None) == "worker_crashed"]
        assert [getattr(r, "exit_code", None) for r in crashed] == [3, 3, 3]
    finally:
        service.close()


def test_uncertified_and_unexpected_worker_results(api_store: ReferenceStore) -> None:
    service = SimulationService(
        api_store, size=1, timeout_seconds=30.0, worker_target=failing_worker
    )
    try:
        service.start()
        with client(api_store, service, raise_server_exceptions=False) as test_client:
            failed = test_client.post("/api/v1/simulations", json={"horizon_days": 3})
            assert failed.status_code == 500
            assert failed.json()["error"]["code"] == "simulation_failed"
            assert "no certified result" in failed.json()["error"]["message"]
            error = test_client.post("/api/v1/simulations", json={"horizon_days": 3})
            assert error.status_code == 500
            assert error.json()["error"]["code"] == "internal_error"
            assert "Traceback" not in error.text
            rejected = test_client.post("/api/v1/simulations", json={"horizon_days": 3})
            assert rejected.status_code == 422
            assert rejected.json()["error"]["code"] == "simulation_rejected"
    finally:
        service.close()


def test_pool_exchange_and_close(api_store: ReferenceStore) -> None:
    pool = SimulationPool(worker_context(api_store), 1, slow_worker)
    try:
        worker = pool.acquire()
        assert pool.idle == 0
        assert pool.exchange(worker, None, 60) == ("ok", "pong")
        assert pool.idle == 1
        worker = pool.acquire()
        with pytest.raises(Timeout):
            pool.exchange(worker, "{}", 0.2)
        assert pool.idle == 1
    finally:
        pool.close()
    assert pool.idle == 0
    assert all(pid is None or pid > 0 for pid in pool.pids())


def test_closed_service_refuses(api_store: ReferenceStore) -> None:
    service = SimulationService(api_store, size=1, timeout_seconds=5.0)
    service.close()  # never started: nothing to stop
    assert service.worker_pids() == []


def test_a_worker_that_never_starts_is_replaced(
    api_store: ReferenceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas.api import simulation

    monkeypatch.setattr(simulation, "STARTUP_SECONDS", 1.0)
    service = SimulationService(
        api_store, size=1, timeout_seconds=30.0, worker_target=silent_worker
    )
    try:
        service.start()
        (before,) = service.worker_pids()
        with client(api_store, service, raise_server_exceptions=False) as test_client:
            response = test_client.post("/api/v1/simulations", json={"horizon_days": 3})
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "simulation_failed"
        (after,) = service.worker_pids()
        assert after != before
    finally:
        service.close()


def test_import_time_does_not_count_against_the_deadline(api_store: ReferenceStore) -> None:
    # A fresh worker imports ATLAS (about a second) before it can answer; with a 1 s deadline
    # the first simulation must still succeed.
    service = SimulationService(api_store, size=1, timeout_seconds=1.0)
    try:
        service.start()
        with client(api_store, service) as test_client:
            response = test_client.post("/api/v1/simulations", json={"horizon_days": 2})
        assert response.status_code == 200, response.text
    finally:
        service.close()


def _new_workers(before: set[int]) -> list[int]:
    return [
        p.pid
        for p in multiprocessing.active_children()
        if p.name == "atlas-simulation-worker" and p.pid not in before
    ]


def test_shutdown_during_a_simulation_starts_no_new_worker(api_store: ReferenceStore) -> None:
    before = {p.pid for p in multiprocessing.active_children()}
    service = SimulationService(api_store, size=1, timeout_seconds=30.0, worker_target=slow_worker)
    service.start()
    assert service.warm_up() == 1
    with live_server(api_store, service) as port, ThreadPoolExecutor(1) as pool:
        pending = pool.submit(call, port, "POST", "/api/v1/simulations", {"horizon_days": 3})
        time.sleep(0.5)
        service.close()
        status, _, data, _ = pending.result()
    assert status == 500
    assert json.loads(data)["error"]["code"] == "simulation_failed"
    assert service.worker_pids() == []
    time.sleep(0.5)
    assert _new_workers(before) == []  # no process was started after close (none orphaned)
    with client(api_store, service) as test_client:
        stopped = test_client.post("/api/v1/simulations", json={"horizon_days": 3})
    assert stopped.status_code == 503
    assert stopped.json()["error"]["code"] == "artifact_unavailable"


def _open_idle(port: int, count: int) -> list[socket.socket]:
    sockets = []
    for _ in range(count):
        sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        sockets.append(sock)
    return sockets


def _closed_by_server(sock: socket.socket) -> bool:
    sock.settimeout(5)
    try:
        return sock.recv(1) == b""
    except (ConnectionResetError, TimeoutError):
        return False


def test_idle_and_slow_connections_are_closed(api_store: ReferenceStore) -> None:
    with live_server(api_store, read_seconds=1.0) as port:
        idle = _open_idle(port, 80)  # more than the old 64-connection cap
        slow = socket.create_connection(("127.0.0.1", port), timeout=10)
        slow.sendall(b"GET /health/live HTTP/1.1\r\nHost: localhost\r\n")  # never finished
        try:
            status, _, _, _ = call(port, "GET", "/health/live")
            assert status == 200  # open sockets do not lock the server out
            assert all(_closed_by_server(sock) for sock in [*idle, slow])
        finally:
            for sock in [*idle, slow]:
                sock.close()
        # A keep-alive connection that keeps sending requests is not cut off.
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            for _ in range(3):
                connection.request("GET", "/health/live", headers={"Host": "localhost"})
                response = connection.getresponse()
                response.read()
                assert response.status == 200
                time.sleep(0.6)
        finally:
            connection.close()


class _FlakyContext:
    """A multiprocessing context whose first ``failures`` pipes fail (no file slot left)."""

    def __init__(self, real: Any, failures: int) -> None:
        self._real = real
        self.failures = failures

    def Pipe(self, duplex: bool = True) -> Any:
        if self.failures > 0:
            self.failures -= 1
            raise OSError(24, "Too many open files")
        return self._real.Pipe(duplex=duplex)

    def Process(self, *args: Any, **kwargs: Any) -> Any:
        return self._real.Process(*args, **kwargs)


def test_a_worker_whose_restart_fails_recovers_later(
    api_store: ReferenceStore, caplog: pytest.LogCaptureFixture
) -> None:
    service = SimulationService(api_store, size=1, timeout_seconds=60.0)
    try:
        service.start()
        assert service.warm_up() == 1
        (worker,) = service._pool._workers  # type: ignore[union-attr]
        worker._mp = _FlakyContext(worker._mp, failures=2)
        os.kill(worker.process.pid, signal.SIGKILL)  # the worker dies...
        worker.process.join(5)
        body = {"horizon_days": 3}
        with (
            client(api_store, service, raise_server_exceptions=False) as test_client,
            caplog.at_level(logging.WARNING, "atlas.api"),
        ):
            # ...and its replacement cannot start, twice: each request fails cleanly.
            for _ in range(2):
                response = test_client.post("/api/v1/simulations", json=body)
                assert response.status_code == 500
                assert response.json()["error"]["code"] == "simulation_failed"
            ready = test_client.get("/health/ready").json()["components"]
            workers = {c["component"]: c for c in ready}["simulation_workers"]
            assert workers["ready"] is False
            assert "could be started" in workers["detail"]
            # The slot is not dead: the next request starts a process and computes.
            assert test_client.post("/api/v1/simulations", json=body).status_code == 200
            assert test_client.get("/health/ready").status_code == 200
        failed = [r for r in caplog.records if getattr(r, "event", None) == "worker_start_failed"]
        assert len(failed) == 2
    finally:
        service.close()


def test_workers_ignore_ctrl_c_and_finish_before_sigterm(api_store: ReferenceStore) -> None:
    pool = SimulationPool(worker_context(api_store), 1)
    try:
        worker = pool.acquire()
        assert pool.exchange(worker, None, 120) == ("ok", "pong")
        pid = worker.process.pid
        os.kill(pid, signal.SIGINT)  # Ctrl+C reaches the whole process group
        time.sleep(0.3)
        assert worker.process.is_alive()
        prepared = prepare(api_store, SimulationRequest.model_validate({"horizon_days": 30}))
        worker = pool.acquire()
        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(pool.exchange, worker, prepared.config_json, 60)
            time.sleep(0.05)
            os.kill(pid, signal.SIGTERM)  # a service manager stops the control group
            status, _ = future.result()
        assert status == "ok"  # the simulation in flight was finished and answered
        worker.process.join(10)
        assert worker.process.exitcode == 0  # then the worker left
    finally:
        pool.close()


def test_the_lifespan_stops_the_workers_it_started(api_store: ReferenceStore) -> None:
    before = {p.pid for p in multiprocessing.active_children()}
    with client(api_store) as test_client:  # no simulator injected: the app owns one
        started = set(test_client.app.state.simulator.worker_pids())  # type: ignore[attr-defined]
        assert started
    time.sleep(0.5)
    assert not set(_new_workers(before)) & started
