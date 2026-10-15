"""``atlas api serve`` and ``atlas api openapi``: run the API locally, export its contract.

The server is one uvicorn process (the simulation worker processes are the API's own;
several uvicorn workers would multiply them). By default it binds to the loopback
interface only. Binding to any other address is refused unless an API key protects the
simulation endpoint (``ATLAS_API_KEY``): the API is designed for local development and
protected deployment (M6), never for an unauthenticated public bind.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
from typing import TYPE_CHECKING, Any, Final, TextIO

import h11
from uvicorn.protocols.http.h11_impl import H11Protocol

from atlas.api.app import create_app
from atlas.api.logs import configure_logging
from atlas.api.settings import ENV_API_KEY, ApiSettings
from atlas.errors import ConfigError

if TYPE_CHECKING:
    import uvicorn

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8000
#: A backstop only: above this many open connections uvicorn answers a plain-text 503
#: itself. Requests in flight are capped lower, with a JSON 503, by the API middleware;
#: idle and slow connections are closed by :class:`DeadlineH11Protocol`.
MAX_CONNECTIONS: Final = 1024
KEEP_ALIVE_SECONDS: Final = 5
#: A request (headers and body) must arrive completely within this many seconds of the
#: connection opening or of the previous response.
REQUEST_READ_SECONDS: Final = 10.0


class DeadlineH11Protocol(H11Protocol):
    """uvicorn's HTTP/1.1 protocol with a deadline for receiving each request.

    uvicorn arms no timer before a connection's first request, so a client that connects
    and sends nothing, or sends its headers or body slowly, would hold the connection
    forever. Here every request must be received completely within ``read_seconds``;
    otherwise the connection is closed.
    """

    read_seconds: float = REQUEST_READ_SECONDS

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._read_deadline: asyncio.TimerHandle | None = None

    def _arm_read_deadline(self) -> None:
        self._cancel_read_deadline()
        self._read_deadline = self.loop.call_later(self.read_seconds, self._read_timed_out)

    def _cancel_read_deadline(self) -> None:
        if self._read_deadline is not None:
            self._read_deadline.cancel()
            self._read_deadline = None

    def _read_timed_out(self) -> None:
        self._read_deadline = None
        if not self.transport.is_closing() and self.conn.their_state in (h11.IDLE, h11.SEND_BODY):
            self.transport.close()

    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        super().connection_made(transport)
        self._arm_read_deadline()

    def connection_lost(self, exc: Exception | None) -> None:
        self._cancel_read_deadline()
        super().connection_lost(exc)

    def handle_events(self) -> None:
        super().handle_events()
        if self.conn.their_state not in (h11.IDLE, h11.SEND_BODY):
            self._cancel_read_deadline()  # the request is complete (or the connection ends)

    def on_response_complete(self) -> None:
        super().on_response_complete()
        if not self.transport.is_closing() and self.conn.their_state is h11.IDLE:
            self._arm_read_deadline()  # the next request on this keep-alive connection


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def check_bind(host: str, port: int, settings: ApiSettings) -> None:
    if not 1 <= port <= 65_535:
        raise ConfigError(f"port must be within 1..65535, got {port}")
    if not is_loopback(host) and settings.api_key is None:
        raise ConfigError(
            f"refusing to bind to {host}: the API is for local use unless {ENV_API_KEY} "
            "protects the simulation endpoint (and ATLAS_API_ALLOWED_HOSTS names the host "
            "clients use). Public deployment belongs behind the M6 reverse proxy."
        )


def server_config(app: Any, host: str, port: int) -> uvicorn.Config:
    """The uvicorn configuration of ``atlas api serve`` (also used by the tests)."""
    import uvicorn

    return uvicorn.Config(
        app,
        host=host,
        port=port,
        http=DeadlineH11Protocol,
        log_config=None,
        access_log=False,
        server_header=False,
        proxy_headers=False,
        limit_concurrency=MAX_CONNECTIONS,
        timeout_keep_alive=KEEP_ALIVE_SECONDS,
        timeout_graceful_shutdown=10,
    )


def serve(settings: ApiSettings, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Run the API until interrupted (blocking)."""
    import uvicorn

    check_bind(host, port, settings)
    configure_logging(settings.log_level)
    uvicorn.Server(server_config(create_app(settings), host, port)).run()


def openapi_document(settings: ApiSettings) -> dict[str, Any]:
    """The OpenAPI document of the API (no reference data is loaded to build it)."""
    return create_app(settings).openapi()


def write_openapi(settings: ApiSettings, stream: TextIO) -> None:
    stream.write(json.dumps(openapi_document(settings), indent=2, sort_keys=True) + "\n")
