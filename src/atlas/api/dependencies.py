"""FastAPI dependencies: injected services, the API key, and strict request checks."""

from __future__ import annotations

import json
from http import HTTPStatus
from types import UnionType
from typing import Annotated, Any, Union, get_args, get_origin

from fastapi import Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.security import APIKeyHeader

from atlas.api.problems import ApiError
from atlas.api.reference import ReferenceStore
from atlas.api.settings import API_KEY_HEADER, ApiSettings, api_key_matches
from atlas.api.simulation import SimulationService

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
    if not api_key_matches(key, expected):
        raise ApiError(
            HTTPStatus.UNAUTHORIZED,
            "unauthorized",
            f"a valid {API_KEY_HEADER} header is required for this endpoint",
            headers={"WWW-Authenticate": API_KEY_HEADER},
        )


class _Members(list[tuple[str, Any]]):
    """A JSON object as parsed: its members in order, repeated keys included."""


def _duplicate_paths(document: Any) -> list[str]:
    """Dotted paths (``parameters.regular_wage``) of every key an object repeats."""
    found: dict[str, None] = {}
    pending: list[tuple[Any, str]] = [(document, "")]
    while pending:  # iterative: the nesting depth is bounded only by the body limit
        value, path = pending.pop()
        if isinstance(value, _Members):
            seen: set[str] = set()
            for key, item in value:
                where = f"{path}.{key}" if path else key
                if key in seen:
                    found[where] = None
                seen.add(key)
                pending.append((item, where))
        elif isinstance(value, list):
            pending.extend(
                (item, f"{path}.{i}" if path else str(i)) for i, item in enumerate(value)
            )
    return sorted(found)


def repeated_keys(body: bytes) -> list[str]:
    """Dotted paths of the keys a JSON body repeats (``[]`` for malformed JSON)."""
    try:
        document = json.loads(body, object_pairs_hook=_Members)
    except (ValueError, RecursionError):
        return []  # malformed JSON is reported by the body parser (400 or 422)
    return _duplicate_paths(document)


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
    # Linear, and off the event loop: a body of many keys never delays other requests.
    repeated = await run_in_threadpool(repeated_keys, body)
    if repeated:
        raise ApiError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "validation_error",
            "the JSON body repeats a key",
            details=[{"field": path[:128], "message": "duplicate key"} for path in repeated[:20]],
        )


def _is_list(annotation: Any) -> bool:
    if get_origin(annotation) is Annotated:
        return _is_list(get_args(annotation)[0])
    if get_origin(annotation) in (Union, UnionType):
        return any(_is_list(arg) for arg in get_args(annotation))
    return annotation is list or get_origin(annotation) is list


def _query_names(dependant: Dependant) -> dict[str, bool]:
    """The query parameters a route declares (sub-dependencies included): name -> whether
    it may repeat (a list parameter)."""
    names: dict[str, bool] = {}
    pending = [dependant]
    while pending:
        current = pending.pop()
        for param in current.query_params:
            names[param.alias] = _is_list(param.field_info.annotation)
        pending.extend(current.dependencies)
    return names


def reject_unknown_query(request: Request) -> None:
    """Query parameters the route does not declare, and repeated values of a parameter
    that is not a list, are errors (never silently ignored or last-wins)."""
    route = request.scope.get("route")
    if not isinstance(route, APIRoute):  # pragma: no cover - always set for API routes
        return
    declared = _query_names(route.dependant)
    unknown = sorted({name for name in request.query_params if name not in declared})
    repeated = sorted(
        name
        for name, many in declared.items()
        if not many and len(request.query_params.getlist(name)) > 1
    )
    problems = [{"field": name[:64], "message": "not accepted here"} for name in unknown]
    problems += [{"field": name, "message": "given more than once"} for name in repeated]
    if problems:
        raise ApiError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "validation_error",
            "unknown or repeated query parameter",
            details=problems[:20],
        )
