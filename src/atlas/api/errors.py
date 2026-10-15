"""One error contract for every failure the API reports.

Every error response has the body :class:`~atlas.api.schemas.ErrorResponse`::

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]}}

``code`` is a stable machine-readable identifier (documented in ``docs/API.md``);
``message`` is written for people and never contains a stack trace, a file system path, a
secret or the text of an unexpected exception. ``details[].field`` names the request field
the same way in every layer: the dotted path inside the body (``parameters.regular_wage``)
or the query parameter name (``start``), ``<body>`` for the body as a whole. Unexpected
exceptions are logged with their traceback on the server and answered with a generic
``internal_error`` by :class:`atlas.api.middleware.RequestContextMiddleware`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from atlas.api.context import request_id_of
from atlas.api.problems import (
    MAX_DETAIL_MESSAGE,
    MAX_DETAILS,
    ApiError,
    ErrorCode,
    error_payload,
)

LOGGER: Final = logging.getLogger("atlas.api")

#: Request locations FastAPI puts first in a validation error's ``loc``.
_LOCATIONS: Final = ("body", "query", "path", "header", "cookie")


def error_body(
    request: Request, code: ErrorCode, message: str, details: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    return error_payload(code, message, request_id_of(request), details)


def _response(
    request: Request,
    status: int,
    code: ErrorCode,
    message: str,
    details: Sequence[Mapping[str, Any]] = (),
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        LOGGER.warning(
            "request failed: %s",
            code,
            extra={"event": "api_error", "code": code, "status": status},
        )
    return JSONResponse(
        error_body(request, code, message, details), status_code=status, headers=dict(headers or {})
    )


def field_name(loc: Sequence[Any]) -> str:
    """``("body", "parameters", "x")`` -> ``parameters.x``; the body itself -> ``<body>``."""
    parts = [str(part) for part in loc]
    if parts and parts[0] in _LOCATIONS:
        parts = parts[1:]
    return ".".join(parts) or "<body>"


def _validation_details(errors: Sequence[Any]) -> list[dict[str, Any]]:
    details = []
    for error in list(errors)[:MAX_DETAILS]:
        if error.get("type") == "json_invalid":
            details.append({"field": "<body>", "message": "the body is not valid JSON"})
            continue
        message = str(error.get("msg", "invalid value"))[:MAX_DETAIL_MESSAGE]
        details.append({"field": field_name(error.get("loc", ())), "message": message})
    return details


_HTTP_CODES: Final[dict[int, ErrorCode]] = {
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
        if code == "not_found":
            message = "no such resource"
        elif code == "bad_request":
            message = "the request body could not be read"
        else:
            message = status.phrase
        return _response(request, exc.status_code, code, message, (), exc.headers)
