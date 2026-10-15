"""Liveness and readiness (unversioned: for process supervisors and load balancers)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from atlas import __version__
from atlas.api.dependencies import get_store, reject_unknown_query
from atlas.api.reference import ReferenceStore
from atlas.api.routers import COMMON, cost, errors
from atlas.api.schemas import ComponentStatus, LiveResponse, ReadyResponse
from atlas.api.simulation import WORKERS, SimulationService

router = APIRouter(prefix="/health", tags=["health"], dependencies=[Depends(reject_unknown_query)])


@router.get(
    "/live",
    name="getLive",
    summary="Process liveness",
    responses=errors(*COMMON),
    openapi_extra=cost("static"),
)
async def live() -> LiveResponse:
    """The process answers. Says nothing about the reference data (see ``/health/ready``)."""
    return LiveResponse(status="alive", atlas_version=__version__)


@router.head("/live", include_in_schema=False)
async def live_head() -> Response:
    """HEAD for uptime checkers: the status of ``GET /health/live``, no body."""
    return Response(status_code=200)


@router.get(
    "/ready",
    name="getReady",
    response_model=ReadyResponse,
    summary="Readiness of every reference component",
    responses={
        **errors(*COMMON),
        503: {"model": ReadyResponse, "description": "A component is not ready."},
    },
    openapi_extra=cost("static"),
)
async def ready(
    request: Request, store: Annotated[ReferenceStore, Depends(get_store)]
) -> JSONResponse:
    """200 when every committed reference component loaded and passed its integrity checks
    and the simulation workers run; 503 (same body) naming each component that is not
    ready (the workers after 3 consecutive crashes)."""
    simulator: SimulationService | None = request.app.state.simulator
    workers = (
        ComponentStatus(component=WORKERS, ready=False, detail="the simulation service is absent")
        if simulator is None
        else simulator.health()
    )
    components = [*store.components(), workers]
    ok = all(c.ready for c in components)
    body = ReadyResponse(status="ready" if ok else "not_ready", components=components)
    return JSONResponse(body.model_dump(mode="json"), status_code=200 if ok else 503)


@router.head("/ready", include_in_schema=False)
async def ready_head(
    request: Request, store: Annotated[ReferenceStore, Depends(get_store)]
) -> Response:
    """HEAD for uptime checkers: the status of ``GET /health/ready``, no body."""
    response = await ready(request, store)
    return Response(status_code=response.status_code)
