"""Expected failures of the application layer, independent of the web framework.

The application modules (:mod:`atlas.api.reference`, :mod:`atlas.api.views`,
:mod:`atlas.api.simulation`) raise :class:`ApiError` with an HTTP status, a stable code and
a safe message; :mod:`atlas.api.errors` turns it into the one error body. Nothing here
imports FastAPI or Starlette, so the worker processes never load the web stack.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import Any, Final, Literal

#: Every error code the API returns (documented in docs/API.md, section 4).
ErrorCode = Literal[
    "bad_request",
    "invalid_host",
    "cors_rejected",
    "unauthorized",
    "not_found",
    "method_not_allowed",
    "payload_too_large",
    "unsupported_media_type",
    "validation_error",
    "simulation_rejected",
    "simulation_failed",
    "internal_error",
    "artifact_unavailable",
    "simulations_busy",
    "simulation_timeout",
    "server_busy",
    "http_error",
]

#: The HTTP status of each code, and what it means (the OpenAPI response descriptions).
STATUS_OF: Final[dict[ErrorCode, int]] = {
    "bad_request": 400,
    "invalid_host": 400,
    "cors_rejected": 400,
    "unauthorized": 401,
    "not_found": 404,
    "method_not_allowed": 405,
    "payload_too_large": 413,
    "unsupported_media_type": 415,
    "validation_error": 422,
    "simulation_rejected": 422,
    "simulation_failed": 500,
    "internal_error": 500,
    "artifact_unavailable": 503,
    "simulations_busy": 503,
    "server_busy": 503,
    "simulation_timeout": 504,
}
MEANING: Final[dict[ErrorCode, str]] = {
    "bad_request": "the body cannot be read (invalid UTF-8, an oversized number)",
    "invalid_host": "the Host header names a host this server does not serve",
    "cors_rejected": "a CORS preflight from an origin, method or header not allowed",
    "unauthorized": "missing or wrong X-API-Key (when the server sets one)",
    "not_found": "unknown resource",
    "method_not_allowed": "method not allowed on this path",
    "payload_too_large": "request body above the limit",
    "unsupported_media_type": "body is not application/json",
    "validation_error": "the request does not match the schema (or an unknown or repeated "
    "query parameter)",
    "simulation_rejected": "an input M4's own rules refuse",
    "simulation_failed": "no certified result, or the worker died",
    "internal_error": "unexpected failure (details only in the server log)",
    "artifact_unavailable": "the reference data behind this endpoint is not ready",
    "simulations_busy": "every simulation worker is busy; retry after Retry-After",
    "server_busy": "too many requests in flight; retry after Retry-After",
    "simulation_timeout": "the simulation missed its deadline and was stopped",
}

#: Validation messages are cut to this length (they may echo client input).
MAX_DETAIL_MESSAGE: Final = 300
#: At most this many problems are returned.
MAX_DETAILS: Final = 20


class ApiError(Exception):
    """An expected failure with its HTTP status, stable code and safe message."""

    def __init__(
        self,
        status: int,
        code: ErrorCode,
        message: str,
        *,
        details: Sequence[Mapping[str, Any]] = (),
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = [dict(item) for item in details][:MAX_DETAILS]
        self.headers = dict(headers or {})


def error_payload(
    code: ErrorCode,
    message: str,
    request_id: str | None,
    details: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The one error body: ``{"error": {"code", "message", "request_id", "details"}}``."""
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "details": [dict(item) for item in details][:MAX_DETAILS],
        }
    }


def not_found(
    what: str, identifier: str, known: Sequence[str] = (), *, field: str | None = None
) -> ApiError:
    """404 for an unknown ``what``; ``field`` names the request parameter that gave it."""
    details = [{"field": field or what, "message": "known: " + ", ".join(known)}] if known else []
    return ApiError(
        HTTPStatus.NOT_FOUND, "not_found", f"unknown {what} {identifier!r}", details=details
    )


def unavailable(component: str) -> ApiError:
    """A reference component could not be loaded or failed its integrity checks."""
    return ApiError(
        HTTPStatus.SERVICE_UNAVAILABLE,
        "artifact_unavailable",
        f"the reference data for {component!r} is unavailable: it is missing or failed its "
        "integrity checks (see /health/ready)",
    )


def invalid(field: str, message: str, summary: str | None = None) -> ApiError:
    """A 422 ``validation_error`` about one request field (``parameters.x``, ``start``)."""
    return ApiError(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        "validation_error",
        summary or message,
        details=[{"field": field, "message": message[:MAX_DETAIL_MESSAGE]}],
    )
