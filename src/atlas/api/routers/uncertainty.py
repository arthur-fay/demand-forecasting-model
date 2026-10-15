"""The M3 calibration status and the projection quantiles it governs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from atlas.api import views
from atlas.api.dependencies import get_store, reject_unknown_query
from atlas.api.reference import ReferenceStore
from atlas.api.routers import API_PREFIX, VERSIONED, cost, errors
from atlas.api.schemas import HorizonDaysQuery, IntervalsResponse, UncertaintyStatusResponse
from atlas.planning import MAX_HORIZON_DAYS

router = APIRouter(
    prefix=f"{API_PREFIX}/uncertainty",
    tags=["uncertainty"],
    dependencies=[Depends(reject_unknown_query)],
)

Store = Annotated[ReferenceStore, Depends(get_store)]


@router.get(
    "/status",
    name="getUncertaintyStatus",
    summary="M3 calibration status",
    responses=errors(*VERSIONED),
    openapi_extra=cost("precomputed"),
)
async def status(store: Store) -> UncertaintyStatusResponse:
    """The pre-registered M3 decision status, its statement, the coverage evidence and the
    reasons it failed, exactly as the committed calibration_status.json records them."""
    return views.uncertainty_status(store)


@router.get(
    "/intervals",
    name="getUncertaintyIntervals",
    summary="M3 projection quantiles (failed calibration)",
    responses=errors(*VERSIONED),
    openapi_extra=cost("precomputed"),
)
async def intervals(
    store: Store,
    horizon_days: Annotated[HorizonDaysQuery, Query(description="Days to return.")] = (
        MAX_HORIZON_DAYS
    ),
) -> IntervalsResponse:
    """Marginal quantiles of the projected days from the sealed M3 method, with the M3
    holiday-related flag. The method failed its calibration criteria: the quantiles carry
    no probability to rely on."""
    return views.intervals(store, horizon_days)
