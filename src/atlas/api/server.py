"""``atlas api serve`` and ``atlas api openapi``: run the API locally, export its contract.

The server is one uvicorn process (the simulation worker processes are the API's own;
several uvicorn workers would multiply them). By default it binds to the loopback
interface only. Binding to any other address is refused unless an API key protects the
simulation endpoint (``ATLAS_API_KEY``): the API is designed for local development and
protected deployment (M6), never for an unauthenticated public bind.
"""

from __future__ import annotations

import ipaddress
import json
from typing import Any, Final, TextIO

from atlas.api.app import create_app
from atlas.api.logs import configure_logging
from atlas.api.settings import ENV_API_KEY, ApiSettings
from atlas.errors import ConfigError

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8000
#: uvicorn answers 503 itself above this many concurrent connections and tasks.
MAX_CONNECTIONS: Final = 64
KEEP_ALIVE_SECONDS: Final = 5


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


def serve(settings: ApiSettings, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Run the API until interrupted (blocking)."""
    import uvicorn

    check_bind(host, port, settings)
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=host,
        port=port,
        log_config=None,
        access_log=False,
        server_header=False,
        proxy_headers=False,
        limit_concurrency=MAX_CONNECTIONS,
        timeout_keep_alive=KEEP_ALIVE_SECONDS,
        timeout_graceful_shutdown=10,
    )


def openapi_document(settings: ApiSettings) -> dict[str, Any]:
    """The OpenAPI document of the API (no reference data is loaded to build it)."""
    return create_app(settings).openapi()


def write_openapi(settings: ApiSettings, stream: TextIO) -> None:
    stream.write(json.dumps(openapi_document(settings), indent=2, sort_keys=True) + "\n")
