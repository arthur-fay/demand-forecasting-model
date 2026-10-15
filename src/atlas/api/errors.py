"""One error contract for every failure the API reports.

Every error response has the body :class:`~atlas.api.schemas.ErrorResponse`::

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]}}

``code`` is a stable machine-readable identifier (documented in ``docs/API.md``);
``message`` is written for people and never contains a stack trace, a file system path, a
secret or the text of an unexpected exception. Unexpected exceptions are logged with their
traceback on the server and answered with a generic ``internal_error`` by
:class:`atlas.api.middleware.RequestContextMiddleware`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from atlas.api.context import request_id_of

#: Validation messages are cut to this length (they may echo client input).
MAX_DETAIL_MESSAGE: Final = 300
#: At most this many validation problems are returned.
MAX_DETAILS: Final = 20


class ApiError(Exception):
    """An expected failure with its HTTP status, stable code and safe message."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: Sequence[Mapping[str, Any]] = (),
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = [dict(item) for item in details]
        self.headers = dict(headers or {})


def not_found(what: str, identifier: str, known: Sequence[str] = ()) -> ApiError:
    details = [{"field": what, "message": "known: " + ", ".join(known)}] if known else []
    return ApiError(
        HTTPStatus.NOT_FOUND,
        "not_found",
        f"unknown {what} {identifier!r}",
        details=details,
    )


def unavailable(component: str) -> ApiError:
    """A reference component could not be loaded or failed its integrity checks."""
    return ApiError(
        HTTPStatus.SERVICE_UNAVAILABLE,
        "artifact_unavailable",
        f"the reference data for {component!r} is unavailable: it is missing or failed its "
        "integrity checks (see /health/ready)",
    )


def error_body(
    request: Request, code: str, message: str, details: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id_of(request),
            "details": [dict(item) for item in details],
        }
    }


def _response(
    request: Request,
    status: int,
    code: str,
    message: str,
    details: Sequence[Mapping[str, Any]] = (),
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        error_body(request, code, message, details), status_code=status, headers=dict(headers or {})
    )


def _location(loc: Sequence[Any]) -> str:
    return ".".join(str(part) for part in loc) or "<body>"


def _validation_details(errors: Sequence[Any]) -> list[dict[str, Any]]:
    details = []
    for error in list(errors)[:MAX_DETAILS]:
        message = str(error.get("msg", "invalid value"))[:MAX_DETAIL_MESSAGE]
        details.append({"field": _location(error.get("loc", ())), "message": message})
    return details


_HTTP_CODES: Final = {
    HTTPStatus.NOT_FOUND: "not_found",
    HTTPStatus.METHOD_NOT_ALLOWED: "method_not_allowed",
    HTTPStatus.BAD_REQUEST: "bad_request",
    HTTPStatus.UNAUTHORIZED: "unauthorized",
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: "payload_too_large",
    HTTPStatus.UNSUPPORTED_MEDIA_TYPE: "unsupported_media_type",
}


def install_error_handlers(app: FastAPI) -> None:
    """Map every exception to :class:`~atlas.api.schemas.ErrorResponse`."""

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _response(request, exc.status, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _response(
            request,
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "validation_error",
            "the request does not match the documented schema",
            _validation_details(exc.errors()),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        status = HTTPStatus(exc.status_code)
        code = _HTTP_CODES.get(status, "http_error")
        message = status.phrase if code != "not_found" else "no such resource"
        return _response(request, exc.status_code, code, message, (), exc.headers)
