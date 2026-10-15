"""Service-manager integration: readiness, watchdog and stopping notifications, the
readiness summary, and the workers' OOM preference.

A real ``AF_UNIX`` datagram socket stands for systemd's notification socket; a real uvicorn
server with the API reports to it.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from atlas.api.notify import NOTIFY_SOCKET, WATCHDOG_PID, WATCHDOG_USEC, ServiceNotifier
from atlas.api.server import SupervisedServer, readiness_summary, server_config
from atlas.api.worker import WORKER_OOM_SCORE_ADJ, prefer_oom_kill
from tests.m6_helpers import free_port, write

if TYPE_CHECKING:
    from atlas.api.reference import ReferenceStore
    from atlas.api.simulation import SimulationService


@contextmanager
def notify_socket(tmp_path: Path) -> Iterator[tuple[str, socket.socket]]:
    path = str(tmp_path / "notify.sock")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(path)
    sock.settimeout(10)
    try:
        yield path, sock
    finally:
        sock.close()


def receive(sock: socket.socket) -> str:
    return sock.recv(4096).decode()


def test_the_notifier_is_inert_outside_systemd() -> None:
    notifier = ServiceNotifier.from_environment({})
    assert not notifier.enabled
    assert notifier.ready("x") is False
    assert notifier.watchdog() is False


def test_the_environment_is_read_and_removed() -> None:
    env = {NOTIFY_SOCKET: "@abstract", WATCHDOG_USEC: "30000000", WATCHDOG_PID: str(os.getpid())}
    notifier = ServiceNotifier.from_environment(env)
    assert notifier.address == "\0abstract"
    assert notifier.watchdog_seconds == 30.0
    assert env == {}  # the workers spawned later never inherit them
    other = ServiceNotifier.from_environment(
        {NOTIFY_SOCKET: "/run/x", WATCHDOG_USEC: "1", WATCHDOG_PID: "1"}, pid=2
    )
    assert other.watchdog_seconds is None  # the watchdog belongs to another process
    assert ServiceNotifier.from_environment({NOTIFY_SOCKET: "relative"}).address is None


def test_messages_reach_the_socket(tmp_path: Path) -> None:
    with notify_socket(tmp_path) as (path, sock):
        clock = [0.0]
        notifier = ServiceNotifier(path, watchdog_seconds=30.0, clock=lambda: clock[0])
        assert notifier.ready("ready: 7 components\nready")
        assert receive(sock) == "READY=1\nSTATUS=ready: 7 components ready"
        assert notifier.watchdog() is False  # pinged at READY
        clock[0] = 10.0
        assert notifier.watchdog() is True
        assert receive(sock) == "WATCHDOG=1"
        clock[0] = 15.0
        assert notifier.watchdog() is False  # every 10 s, a third of the period
        assert notifier.status("x" * 500)
        assert len(receive(sock)) == len("STATUS=") + 200
        assert notifier.stopping()
        assert receive(sock) == "STOPPING=1\nSTATUS=shutting down"


def test_a_missing_socket_is_logged_once_and_never_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    notifier = ServiceNotifier(str(tmp_path / "gone.sock"))
    assert notifier.ready("x") is False
    assert notifier.status("y") is False
    assert [r.event for r in caplog.records if hasattr(r, "event")] == ["notify_failed"]  # type: ignore[attr-defined]


def test_the_server_reports_readiness_watchdog_and_shutdown(
    tmp_path: Path, api_store: ReferenceStore, api_simulator: SimulationService
) -> None:
    from atlas.api.app import create_app
    from tests.m5_helpers import settings

    app = create_app(settings(), store=api_store, simulator=api_simulator)
    with notify_socket(tmp_path) as (path, sock):
        server = SupervisedServer(
            server_config(app, "127.0.0.1", free_port()),
            ServiceNotifier(path, watchdog_seconds=0.6),
            lambda: readiness_summary(app),
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        ready = receive(sock)
        assert server.started  # READY only once the socket is bound
        assert ready == "READY=1\nSTATUS=ready: 7 components ready"
        assert receive(sock) == "WATCHDOG=1"  # from the event loop's tick
        server.should_exit = True
        thread.join(timeout=30)
        sock.settimeout(0.5)
        messages = []
        while True:
            try:
                messages.append(receive(sock))
            except TimeoutError:
                break
    assert "STOPPING=1\nSTATUS=shutting down" in messages


def test_the_readiness_summary_names_what_is_missing(api_store: ReferenceStore) -> None:
    class State:
        store = None
        simulator = None

    class App:
        state = State()

    assert readiness_summary(App()) == "not initialised"
    App.state.store = api_store  # type: ignore[assignment]
    assert readiness_summary(App()) == "ready: 6 components ready"

    class Broken:
        def health(self) -> object:
            from atlas.api.schemas import ComponentStatus

            return ComponentStatus(component="simulation_workers", ready=False, detail="x")

    App.state.simulator = Broken()  # type: ignore[assignment]
    assert readiness_summary(App()) == "degraded: not ready: simulation_workers"


def test_serve_uses_the_supervised_server(monkeypatch: pytest.MonkeyPatch) -> None:
    import logging

    import uvicorn

    from atlas.api import logs
    from atlas.cli import main
    from tests.conftest import REPO_ROOT

    seen: dict[str, object] = {}

    def fake_run(server: uvicorn.Server, sockets: object = None) -> None:
        seen["server"] = server

    monkeypatch.setattr(uvicorn.Server, "run", fake_run)
    monkeypatch.delenv("ATLAS_API_KEY", raising=False)
    monkeypatch.setenv(NOTIFY_SOCKET, "/run/systemd/notify")
    try:
        assert main(["--repo-root", str(REPO_ROOT), "api", "serve", "--port", "8125"]) == 0
    finally:
        for name in logs.LOGGER_NAMES:
            logger = logging.getLogger(name)
            logger.handlers = [h for h in logger.handlers if h.get_name() != "atlas-api-json"]
            logger.propagate = True
            logger.setLevel(logging.NOTSET)
        logging.getLogger("uvicorn.access").disabled = False
    server = seen["server"]
    assert isinstance(server, SupervisedServer)
    assert server.notifier.address == "/run/systemd/notify"
    assert NOTIFY_SOCKET not in os.environ  # removed before the workers start


# --- Workers


def test_workers_raise_their_own_oom_score(tmp_path: Path) -> None:
    path = write(tmp_path / "oom_score_adj", "0\n")
    assert prefer_oom_kill(path) is True
    assert path.read_text() == f"{WORKER_OOM_SCORE_ADJ}\n"
    write(path, "900\n")
    assert prefer_oom_kill(path) is True
    assert path.read_text() == "900\n"
    assert prefer_oom_kill(tmp_path / "missing") is False
    write(path, "garbage\n")
    assert prefer_oom_kill(path) is False


@pytest.mark.skipif(not Path("/proc/self/oom_score_adj").exists(), reason="Linux only")
def test_running_workers_are_the_preferred_oom_victims(api_simulator: SimulationService) -> None:
    pids = api_simulator.worker_pids()
    assert pids
    deadline = time.monotonic() + 30
    for pid in pids:
        while True:
            value = int(Path(f"/proc/{pid}/oom_score_adj").read_text())
            if value >= WORKER_OOM_SCORE_ADJ or time.monotonic() > deadline:
                break
            time.sleep(0.1)
        assert value >= WORKER_OOM_SCORE_ADJ
        environ = Path(f"/proc/{pid}/environ").read_bytes()
        assert b"NOTIFY_SOCKET=" not in environ
    assert int(Path("/proc/self/oom_score_adj").read_text()) < WORKER_OOM_SCORE_ADJ


def test_a_worker_that_dies_while_idle_is_replaced_by_the_readiness_check(
    api_store: ReferenceStore, caplog: pytest.LogCaptureFixture
) -> None:
    """Killed between simulations (as the OOM killer would): the readiness check replaces
    it, logs it, and no request fails or is counted as a lost simulation."""
    import logging
    import signal

    from atlas.api.simulation import SimulationService

    service = SimulationService(api_store, size=2, timeout_seconds=60.0)
    try:
        service.start()
        assert service.warm_up() == 2
        victim = service.worker_pids()[0]
        os.kill(victim, signal.SIGKILL)
        deadline = time.monotonic() + 10
        while Path(f"/proc/{victim}").exists() and time.monotonic() < deadline:
            if Path(f"/proc/{victim}/stat").read_text().split()[2] == "Z":
                break
            time.sleep(0.05)
        with caplog.at_level(logging.WARNING, "atlas.api"):
            assert service.health().ready
        replaced = [r for r in caplog.records if getattr(r, "event", None) == "worker_died_idle"]
        assert [getattr(r, "worker_pid", None) for r in replaced] == [victim]
        assert victim not in service.worker_pids()
        assert service.warm_up() == 2  # both slots answer
        status = service.status()
        assert (status.crashes, status.consecutive_crashes, status.alive) == (0, 0, 2)
        assert service.health().ready  # nothing more to replace
    finally:
        service.close()
