"""Worker-process targets for the M5 pool tests (importable by spawned children).

Each has the signature of :func:`atlas.api.simulation.worker_main`, announces itself and
answers pings like it, so a test can make a worker slow, silent or dead without changing
the API code.
"""

from __future__ import annotations

import os
import time
from multiprocessing.connection import Connection
from typing import Any


def _loop(connection: Connection, on_job: Any) -> None:
    connection.send(("ready", None))
    while True:
        try:
            job = connection.recv()
        except (EOFError, OSError):
            return
        if job is None:
            return
        if not isinstance(job, str):
            connection.send(("ok", "pong"))
            continue
        on_job(connection, job)


def slow_worker(connection: Connection, context: Any) -> None:
    """Takes 30 s for every simulation (a computation that misses any deadline)."""

    def on_job(conn: Connection, job: str) -> None:
        time.sleep(30)
        conn.send(("failed", "too late"))

    _loop(connection, on_job)


def dying_worker(connection: Connection, context: Any) -> None:
    """Exits without answering (a crashed process)."""

    def on_job(conn: Connection, job: str) -> None:
        os._exit(3)

    _loop(connection, on_job)


def failing_worker(connection: Connection, context: Any) -> None:
    """Reports a computation without a certified result, then an unexpected error."""
    replies = iter(
        [("failed", "a programme failed an independent check"), ("error", "Traceback x")]
    )

    def on_job(conn: Connection, job: str) -> None:
        conn.send(next(replies, ("rejected", "scenario interactive: rejected")))

    _loop(connection, on_job)


def silent_worker(connection: Connection, context: Any) -> None:
    """Never announces itself (a worker that hangs while importing)."""
    time.sleep(60)
