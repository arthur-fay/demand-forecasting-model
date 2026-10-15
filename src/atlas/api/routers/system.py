"""Service metadata and the M2 model catalogue."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from atlas.api import views
from atlas.api.dependencies import get_settings, get_store, reject_unknown_query
from atlas.api.reference import ReferenceStore
from atlas.api.routers import errors
from atlas.api.schemas import ModelsResponse, SystemResponse
from atlas.api.settings import ApiSettings

router = APIRouter(prefix="/api/v1", dependencies=[Depends(reject_unknown_query)])

Store = Annotated[ReferenceStore, Depends(get_store)]


@router.get(
    "/system",
    name="getSystem",
    response_model=SystemResponse,
    tags=["system"],
    summary="Versions, provenance, scientific status and limits",
    responses=errors(422),
)
async def system(
    store: Store, settings: Annotated[ApiSettings, Depends(get_settings)]
) -> dict[str, Any]:
    """Everything a client needs to label results honestly: the snapshot, the source of
    every reference run, the M3 calibration status, the HYPOTHETICAL status of every M4
    parameter, the component readiness, the request limits and which endpoints compute."""
    return views.system(store, settings)


@router.get(
    "/models",
    name="listModels",
    response_model=ModelsResponse,
    tags=["forecasts"],
    summary="Forecasting models evaluated by M2",
    responses=errors(422, 503),
)
async def models(store: Store) -> dict[str, Any]:
    """The seven M2 models, the development selection and the confirmation-period
    recommendation, with each model's MAE over horizons 1-30 in every M2 period."""
    return views.models(store)
