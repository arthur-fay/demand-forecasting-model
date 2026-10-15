"""Service-manager notifications (the systemd ``sd_notify`` protocol), standard library only.

Under a ``Type=notify`` systemd unit (M6 deployment) the server reports:

* ``READY=1`` once the reference data is loaded, the simulation workers are started and
  the listening socket is bound, so ``systemctl start`` returns only when the API serves;
* ``WATCHDOG=1`` from the event loop at a third of ``WatchdogSec``, so a hung event loop
  is detected by the service manager, which stops and restarts the service;
* ``STATUS=...`` with the readiness summary, shown by ``systemctl status``;
* ``STOPPING=1`` when a graceful shutdown begins.

Without ``NOTIFY_SOCKET`` (every run outside such a unit, the tests of M5 among them) every
call does nothing. The variables are removed from the environment once read, as
``sd_notify(unset_environment=1)`` does, so the simulation worker processes never inherit
them: only the server process speaks for the service.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from collections.abc import Callable, MutableMapping
from typing import Final

LOGGER: Final = logging.getLogger("atlas.api")

NOTIFY_SOCKET: Final = "NOTIFY_SOCKET"
WATCHDOG_USEC: Final = "WATCHDOG_USEC"
WATCHDOG_PID: Final = "WATCHDOG_PID"
#: Pings per watchdog period (systemd recommends at least two).
PINGS_PER_PERIOD: Final = 3
#: A status line longer than this is cut (it is a summary, not a report).
MAX_STATUS: Final = 200


def _address(raw: str) -> str | None:
    """The socket address of ``NOTIFY_SOCKET``: an absolute path, or ``@name`` for a
    Linux abstract socket; anything else is ignored."""
    if raw.startswith("@") and len(raw) > 1:
        return "\0" + raw[1:]
    if raw.startswith("/"):
        return raw
    return None


class ServiceNotifier:
    """Sends notifications to the service manager (see the module docstring)."""

    def __init__(
        self,
        address: str | None,
        watchdog_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.address = address
        self.watchdog_seconds = watchdog_seconds
        self._clock = clock
        self._last_ping: float | None = None
        self._failed = False

    @classmethod
    def from_environment(
        cls, environ: MutableMapping[str, str] | None = None, *, pid: int | None = None
    ) -> ServiceNotifier:
        """Read (and remove) the service manager's variables from ``environ``."""
        env: MutableMapping[str, str] = os.environ if environ is None else environ
        raw_socket = env.pop(NOTIFY_SOCKET, "")
        raw_usec = env.pop(WATCHDOG_USEC, "")
        raw_pid = env.pop(WATCHDOG_PID, "")
        watchdog: float | None = None
        if raw_usec.isdigit() and int(raw_usec) > 0:
            own = os.getpid() if pid is None else pid
            if not raw_pid or (raw_pid.isdigit() and int(raw_pid) == own):
                watchdog = int(raw_usec) / 1_000_000
        return cls(_address(raw_socket), watchdog)

    @property
    def enabled(self) -> bool:
        return self.address is not None

    def send(self, *assignments: str) -> bool:
        """Send ``KEY=value`` lines in one datagram; ``False`` if nothing was delivered.

        A failure is logged once and never raised: the service manager's absence must not
        take the API down (the manager itself notices missing readiness or pings).
        """
        if self.address is None:
            return False
        payload = "\n".join(assignments).encode("utf-8", "replace")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.connect(self.address)
                sock.sendall(payload)
        except OSError as exc:
            if not self._failed:
                self._failed = True
                LOGGER.warning(
                    "service manager notification failed (%s)",
                    type(exc).__name__,
                    extra={"event": "notify_failed"},
                )
            return False
        return True

    def ready(self, status: str) -> bool:
        self._last_ping = self._clock()
        return self.send("READY=1", _status(status))

    def status(self, status: str) -> bool:
        return self.send(_status(status))

    def stopping(self) -> bool:
        return self.send("STOPPING=1", _status("shutting down"))

    def watchdog(self) -> bool:
        """Ping if a third of the watchdog period has passed since the last ping."""
        if self.watchdog_seconds is None or self.address is None:
            return False
        now = self._clock()
        due = self.watchdog_seconds / PINGS_PER_PERIOD
        if self._last_ping is not None and now - self._last_ping < due:
            return False
        self._last_ping = now
        return self.send("WATCHDOG=1")


def _status(text: str) -> str:
    line = " ".join(text.split())  # one line: a newline would start another assignment
    return "STATUS=" + (line if len(line) <= MAX_STATUS else line[: MAX_STATUS - 3] + "...")
