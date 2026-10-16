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
import re
import time
import uuid
from collections.abc import Sequence
from http import HTTPStatus
from typing import Final

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from atlas.api.context import (
    REQUEST_ID_HEADER,
    REQUEST_ID_PATTERN,
    current_request_id,
    reset_request_id,
    set_request_id,
)
from atlas.api.logs import truncated_path
from atlas.api.problems import ErrorCode, error_payload
from atlas.api.settings import API_KEY_HEADER, api_key_matches
from atlas.dashboard import headers_for

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
#: The interactive documentation pages, when the docs are served: each page carries its own
#: policy (:mod:`atlas.api.docs`), and any other answer on these paths gets ``API_CSP``.
DOCS_PATHS: Final = ("/docs", "/redoc")
#: Requests in flight above which others get 503 ``server_busy`` (health probes excepted).
MAX_IN_FLIGHT: Final = 64
HEALTH_PREFIX: Final = "/health/"
#: Response headers a browser client on an allowed origin may read.
EXPOSED_HEADERS: Final = ("X-Request-ID", "Server-Timing", "Retry-After")
_PORT: Final = re.compile(r"[0-9]{1,5}")


def host_name(host: str) -> str | None:
    """The host of a ``Host`` header without its port (IPv6 without brackets); ``None``
    when the header is not ``name[:port]`` or ``[ipv6][:port]``."""
    host = host.strip().lower()
    if host.startswith("["):
        name, bracket, rest = host[1:].partition("]")
        if not bracket or (rest and not (rest[:1] == ":" and _PORT.fullmatch(rest[1:]))):
            return None
        return name
    name, colon, port = host.partition(":")
    if colon and not _PORT.fullmatch(port):
        return None
    return name


def host_allowed(host: str, allowed: Sequence[str]) -> bool:
    """Whether the ``Host`` header names an allowed host (the port is ignored)."""
    name = host_name(host)
    if not name:
        return False
    for pattern in allowed:
        pattern = pattern.lower()
        if pattern.startswith("*."):
            if name.endswith(pattern[1:]) and name != pattern[2:]:
                return True
        elif name == pattern:
            return True
    return False


class JsonCORSMiddleware(CORSMiddleware):
    """Starlette's CORS middleware, with rejected preflights answered by the JSON error
    body (``cors_rejected``) instead of plain text."""

    def preflight_response(self, request_headers: Headers) -> Response:
        response = super().preflight_response(request_headers)
        if response.status_code < HTTPStatus.BAD_REQUEST:
            return response
        rejected = bytes(response.body).decode("utf-8").removeprefix("Disallowed CORS ")
        headers = {
            name: value
            for name, value in response.headers.items()
            if name not in ("content-length", "content-type")
        }
        return JSONResponse(
            error_payload(
                "cors_rejected",
                f"CORS preflight rejected ({rejected}): only the configured origins, GET and "
                "POST, and the documented headers are allowed",
                current_request_id(),
            ),
            status_code=response.status_code,
            headers=headers,
        )


class RequestContextMiddleware:
    """See the module docstring."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_hosts: Sequence[str],
        max_body_bytes: int,
        docs_paths: Sequence[str] = (),
        max_in_flight: int = MAX_IN_FLIGHT,
        cors_origins: Sequence[str] = (),
        api_key: str | None = None,
        protected: Sequence[tuple[str, str]] = (),
        dashboard_paths: frozenset[str] = frozenset(),
    ) -> None:
        self.app = app
        #: (M7) The dashboard's exact paths: they get the dashboard's headers (its own
        #: Content-Security-Policy for HTML and, since M8, one for plain text) and keep
        #: their cache policy.
        self.dashboard_paths = dashboard_paths
        self.api_key = api_key
        self.protected = frozenset(protected)
        self.cors_origins = frozenset(cors_origins)
        self.allowed_hosts = tuple(allowed_hosts)
        self.max_body_bytes = max_body_bytes
        self.docs_paths = frozenset(docs_paths)
        self.max_in_flight = max_in_flight
        self.in_flight = 0  # requests being served (one event loop: no lock needed)

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
        dashboard = path in self.dashboard_paths
        origin = headers.get("origin")
        cors_origin = origin if origin in self.cors_origins else None
        started = time.perf_counter()
        state: dict[str, int | bool] = {"status": 0, "started": False}

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"] = int(message["status"])
                state["started"] = True
                response_headers = MutableHeaders(scope=message)
                # The dashboard's files get its own policy; a JSON answer on a dashboard
                # path (the descriptor, a 405, a refused Host) is the API's and keeps its.
                page = dashboard and not response_headers.get("content-type", "").startswith(
                    "application/json"
                )
                for name, value in SECURITY_HEADERS.items():
                    if not (dashboard and name.lower() in response_headers):
                        response_headers[name] = value
                # A documentation page brings its own policy; nothing else on its path does.
                docs_page = path in self.docs_paths and "content-security-policy" in (
                    response_headers
                )
                if page:
                    # (M8) Plain text (the licence notices) gets the policy for text.
                    content_type = response_headers.get("content-type", "")
                    for name, value in headers_for(content_type).items():
                        response_headers[name] = value
                elif not docs_page:
                    response_headers["Content-Security-Policy"] = API_CSP
                response_headers[REQUEST_ID_HEADER] = request_id
                if cors_origin and "access-control-allow-origin" not in response_headers:
                    # A response made outside the CORS middleware (an error of this one):
                    # an allowed origin may still read its body and request id.
                    response_headers["Access-Control-Allow-Origin"] = cors_origin
                    response_headers["Access-Control-Expose-Headers"] = ", ".join(EXPOSED_HEADERS)
                    response_headers.add_vary_header("Origin")
            await send(message)

        counted = not path.startswith(HEALTH_PREFIX)
        if counted:
            self.in_flight += 1
        try:
            if counted and self.in_flight > self.max_in_flight:
                await self._error(
                    send_with_headers,
                    request_id,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "server_busy",
                    f"more than {self.max_in_flight} requests are in flight; retry shortly",
                    retry_after=True,
                )
                return
            if not host_allowed(headers.get("host", ""), self.allowed_hosts):
                await self._error(
                    send_with_headers,
                    request_id,
                    HTTPStatus.BAD_REQUEST,
                    "invalid_host",
                    "the Host header names a host this server does not serve",
                )
                return
            if (
                self.api_key is not None
                and (str(scope.get("method")), path) in self.protected
                and not api_key_matches(headers.get(API_KEY_HEADER.lower()), self.api_key)
            ):
                # Refused before the body is read: an unauthenticated client cannot hold an
                # in-flight slot by sending a body slowly (the route checks the key again).
                await self._error(
                    send_with_headers,
                    request_id,
                    HTTPStatus.UNAUTHORIZED,
                    "unauthorized",
                    f"a valid {API_KEY_HEADER} header is required for this endpoint",
                    www_authenticate=True,
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
            if counted:
                self.in_flight -= 1
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
    async def _error(
        send: Send,
        request_id: str,
        status: int,
        code: ErrorCode,
        message: str,
        *,
        retry_after: bool = False,
        www_authenticate: bool = False,
    ) -> None:
        if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            LOGGER.warning(
                "request failed: %s",
                code,
                extra={"event": "api_error", "code": code, "status": status},
            )
        body = json.dumps(error_payload(code, message, request_id)).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        if retry_after:
            headers.append((b"retry-after", b"1"))
        if www_authenticate:
            headers.append((b"www-authenticate", API_KEY_HEADER.encode("ascii")))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
