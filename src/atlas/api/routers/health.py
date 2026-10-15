"""Liveness and readiness (unversioned: for process supervisors and load balancers)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from atlas import __version__
from atlas.api.dependencies import get_store, reject_unknown_query
from atlas.api.reference import ReferenceStore
from atlas.api.routers import errors
from atlas.api.schemas import LiveResponse, ReadyResponse

router = APIRouter(prefix="/health", tags=["health"], dependencies=[Depends(reject_unknown_query)])


@router.get(
    "/live",
    name="getLive",
    response_model=LiveResponse,
    summary="Process liveness",
    responses=errors(422),
)
async def live() -> LiveResponse:
    """The process answers. Says nothing about the reference data (see ``/health/ready``)."""
    return LiveResponse(status="alive", atlas_version=__version__)


@router.get(
    "/ready",
    name="getReady",
    response_model=ReadyResponse,
    summary="Readiness of every reference component",
    responses={
        **errors(422),
        503: {"model": ReadyResponse, "description": "A component is not ready."},
    },
)
async def ready(store: Annotated[ReferenceStore, Depends(get_store)]) -> JSONResponse:
    """200 when every committed reference component loaded and passed its integrity checks;
    503 (same body) naming each component that did not."""
    body = ReadyResponse(
        status="ready" if store.ready else "not_ready", components=store.components()
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=200 if store.ready else 503)
