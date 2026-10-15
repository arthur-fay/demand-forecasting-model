"""Per-request context: the request id carried by every response, error body and log line."""

from __future__ import annotations

import re
from contextvars import ContextVar, Token
from typing import Final

from starlette.requests import Request

REQUEST_ID_HEADER: Final = "X-Request-ID"
#: A client-supplied request id is reused only if it matches this (no log injection).
REQUEST_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9._-]{8,64}")

_REQUEST_ID: ContextVar[str | None] = ContextVar("atlas_api_request_id", default=None)


def set_request_id(request_id: str | None) -> Token[str | None]:
    """Bind ``request_id`` to the current context; returns a token for :func:`reset_request_id`."""
    return _REQUEST_ID.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _REQUEST_ID.reset(token)


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


def request_id_of(request: Request) -> str | None:
    value = request.scope.get("state", {}).get("request_id")
    return value if isinstance(value, str) else current_request_id()
