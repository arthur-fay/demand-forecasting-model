"""FastAPI dependencies: injected services, the API key, and strict request checks."""

from __future__ import annotations

import hmac
import json
from http import HTTPStatus
from typing import Annotated, Any, Final

from fastapi import Depends, Request
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.security import APIKeyHeader

from atlas.api.errors import ApiError
from atlas.api.reference import ReferenceStore
from atlas.api.settings import ApiSettings
from atlas.api.simulation import SimulationService

API_KEY_HEADER: Final = "X-API-Key"
_API_KEY = APIKeyHeader(
    name=API_KEY_HEADER,
    auto_error=False,
    description="Required for POST /api/v1/simulations when the server sets ATLAS_API_KEY.",
)


def get_settings(request: Request) -> ApiSettings:
    settings: ApiSettings = request.app.state.settings
    return settings


def get_store(request: Request) -> ReferenceStore:
    store: ReferenceStore | None = request.app.state.store
    if store is None:  # the lifespan has not run (or failed): nothing is loaded
        raise ApiError(
            HTTPStatus.SERVICE_UNAVAILABLE, "artifact_unavailable", "the API is not initialised"
        )
    return store


def get_simulator(request: Request) -> SimulationService:
    simulator: SimulationService | None = request.app.state.simulator
    if simulator is None:
        raise ApiError(
            HTTPStatus.SERVICE_UNAVAILABLE, "artifact_unavailable", "simulations are unavailable"
        )
    return simulator


def require_api_key(
    settings: Annotated[ApiSettings, Depends(get_settings)],
    key: Annotated[str | None, Depends(_API_KEY)],
) -> None:
    """When an API key is configured, the request must present it (constant-time compare)."""
    expected = settings.api_key
    if expected is None:
        return
    if key is None or not hmac.compare_digest(key.encode("utf-8"), expected.encode("utf-8")):
        raise ApiError(
            HTTPStatus.UNAUTHORIZED,
            "unauthorized",
            f"a valid {API_KEY_HEADER} header is required for this endpoint",
            headers={"WWW-Authenticate": API_KEY_HEADER},
        )


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    duplicated = sorted({key for key in keys if keys.count(key) > 1})
    if duplicated:
        raise ApiError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "validation_error",
            "the JSON body repeats a key",
            details=[{"field": key[:64], "message": "duplicate key"} for key in duplicated[:20]],
        )
    return dict(pairs)


async def require_json(request: Request) -> None:
    """A JSON body (``application/json``) whose objects do not repeat a key."""
    content_type = request.headers.get("content-type", "")
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise ApiError(
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            "unsupported_media_type",
            "the request body must be application/json",
        )
    body = await request.body()
    try:
        json.loads(body, object_pairs_hook=_reject_duplicates)
    except ValueError:
        return  # malformed JSON is reported by the schema validation (422)


def _query_names(dependant: Dependant) -> set[str]:
    """The query parameters a route declares, its sub-dependencies included."""
    names: set[str] = set()
    pending = [dependant]
    while pending:
        current = pending.pop()
        names.update(param.alias for param in current.query_params)
        pending.extend(current.dependencies)
    return names


def reject_unknown_query(request: Request) -> None:
    """Query parameters the route does not declare are an error, not silently ignored."""
    route = request.scope.get("route")
    if not isinstance(route, APIRoute):  # pragma: no cover - always set for API routes
        return
    allowed = _query_names(route.dependant)
    unknown = sorted({name for name in request.query_params if name not in allowed})
    if unknown:
        raise ApiError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "validation_error",
            "unknown query parameter",
            details=[{"field": name[:64], "message": "not accepted here"} for name in unknown[:20]],
        )
