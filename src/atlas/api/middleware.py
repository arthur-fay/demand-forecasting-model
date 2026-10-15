"""The outermost API middleware (pure ASGI): request id, host check, body limit, security
headers, access log and the last-resort error response.

It wraps every other middleware and the routers, so every response, including errors
produced before a route runs, carries the request id and the security headers, and an
exception that nothing else handled is answered with a generic ``internal_error`` (its
traceback goes to the log only).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Sequence
from http import HTTPStatus
from typing import Final

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from atlas.api.context import (
    REQUEST_ID_HEADER,
    REQUEST_ID_PATTERN,
    reset_request_id,
    set_request_id,
)
from atlas.api.logs import truncated_path

LOGGER: Final = logging.getLogger("atlas.api")

#: Headers set on every response.
SECURITY_HEADERS: Final = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
#: JSON responses never load anything; the interactive documentation pages need scripts.
API_CSP: Final = "default-src 'none'; frame-ancestors 'none'"
DOCS_PATHS: Final = ("/docs", "/redoc")


def host_allowed(host: str, allowed: Sequence[str]) -> bool:
    """Whether the ``Host`` header names an allowed host (the port is ignored)."""
    name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
    name = name.lower()
    for pattern in allowed:
        pattern = pattern.lower()
        if pattern.startswith("*."):
            if name.endswith(pattern[1:]) and name != pattern[2:]:
                return True
        elif name == pattern:
            return True
    return False


class RequestContextMiddleware:
    """See the module docstring."""

    def __init__(self, app: ASGIApp, *, allowed_hosts: Sequence[str], max_body_bytes: int) -> None:
        self.app = app
        self.allowed_hosts = tuple(allowed_hosts)
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        incoming = headers.get(REQUEST_ID_HEADER.lower(), "")
        request_id = incoming if REQUEST_ID_PATTERN.fullmatch(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        token = set_request_id(request_id)
        path = str(scope.get("path", ""))
        started = time.perf_counter()
        state: dict[str, int | bool] = {"status": 0, "started": False}

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"] = int(message["status"])
                state["started"] = True
                response_headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    response_headers[name] = value
                if not path.startswith(DOCS_PATHS):
                    response_headers["Content-Security-Policy"] = API_CSP
                response_headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            if not host_allowed(headers.get("host", ""), self.allowed_hosts):
                await self._error(
                    send_with_headers,
                    request_id,
                    HTTPStatus.BAD_REQUEST,
                    "invalid_host",
                    "the Host header names a host this server does not serve",
                )
                return
            declared = headers.get("content-length")
            if declared is not None and (
                not declared.isdigit() or int(declared) > self.max_body_bytes
            ):
                await self._error(
                    send_with_headers,
                    request_id,
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    "payload_too_large",
                    f"request bodies are limited to {self.max_body_bytes} bytes",
                )
                return
            await self.app(scope, self._limited(receive), send_with_headers)
        except Exception as exc:  # the last resort: nothing inside handled it
            LOGGER.error(
                "unhandled exception",
                exc_info=exc,
                extra={"event": "unhandled_exception", "request_id": request_id},
            )
            if state["started"]:
                raise
            await self._error(
                send_with_headers,
                request_id,
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                "internal server error; the request id identifies the server log entry",
            )
        finally:
            LOGGER.info(
                "request",
                extra={
                    "event": "request",
                    "request_id": request_id,
                    "method": scope.get("method"),
                    "path": truncated_path(path),
                    "status": state["status"],
                    "duration_ms": round(1000 * (time.perf_counter() - started), 3),
                },
            )
            reset_request_id(token)

    def _limited(self, receive: Receive) -> Receive:
        """``receive`` that stops a body (chunked or not) at the limit with a 413."""
        seen = 0
        limit = self.max_body_bytes

        async def limited() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise HTTPException(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return message

        return limited

    @staticmethod
    async def _error(send: Send, request_id: str, status: int, code: str, message: str) -> None:
        body = json.dumps(
            {"error": {"code": code, "message": message, "request_id": request_id, "details": []}}
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
