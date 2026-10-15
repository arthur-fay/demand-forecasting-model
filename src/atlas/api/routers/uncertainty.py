"""The M3 calibration status and the projection quantiles it governs."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from atlas.api import views
from atlas.api.dependencies import get_store, reject_unknown_query
from atlas.api.reference import ReferenceStore
from atlas.api.routers import errors
from atlas.api.schemas import IntervalsResponse, UncertaintyStatusResponse

router = APIRouter(
    prefix="/api/v1/uncertainty",
    tags=["uncertainty"],
    dependencies=[Depends(reject_unknown_query)],
)

Store = Annotated[ReferenceStore, Depends(get_store)]


@router.get(
    "/status",
    name="getUncertaintyStatus",
    response_model=UncertaintyStatusResponse,
    summary="M3 calibration status (failed_calibration)",
    responses=errors(422, 503),
)
async def status(store: Store) -> dict[str, Any]:
    """The pre-registered M3 decision status, its statement, the coverage evidence and the
    reasons it failed, exactly as the committed calibration_status.json records them."""
    return views.uncertainty_status(store)


@router.get(
    "/intervals",
    name="getUncertaintyIntervals",
    response_model=IntervalsResponse,
    summary="M3 projection quantiles (not calibrated probabilities)",
    responses=errors(422, 503),
)
async def intervals(
    store: Store,
    horizon_days: Annotated[int, Query(ge=1, le=30, description="Days to return.")] = 30,
) -> dict[str, Any]:
    """Marginal predictive quantiles of the projected days from the sealed M3 method. The
    method failed its calibration criteria: these are not probabilities to rely on."""
    return views.intervals(store, horizon_days)
