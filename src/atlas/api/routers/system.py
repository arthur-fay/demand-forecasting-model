"""Service metadata and the M2 model catalogue."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from atlas.api import views
from atlas.api.dependencies import get_settings, get_store, reject_unknown_query
from atlas.api.reference import ReferenceStore
from atlas.api.routers import API_PREFIX, COST_KEY, cost, errors
from atlas.api.schemas import EndpointInfo, ModelsResponse, SystemResponse
from atlas.api.settings import ApiSettings
from atlas.api.simulation import SimulationService

router = APIRouter(prefix=API_PREFIX, dependencies=[Depends(reject_unknown_query)])

Store = Annotated[ReferenceStore, Depends(get_store)]


def endpoint_inventory(document: Mapping[str, Any]) -> list[EndpointInfo]:
    """Every operation of the app's OpenAPI document, with its cost (``x-atlas-cost``)."""
    return [
        EndpointInfo(
            method=method.upper(),
            path=path,
            cost=operation[COST_KEY],
            # No research analysis is run on request: every operation is fit for an
            # interactive client (see docs/API.md, section 9).
            interactive=True,
            summary=operation["summary"],
        )
        for path, item in document["paths"].items()
        for method, operation in item.items()
    ]


@router.get(
    "/system",
    name="getSystem",
    tags=["system"],
    summary="Versions, provenance, scientific status and limits",
    responses=errors(422),
    openapi_extra=cost("precomputed"),
)
async def system(
    request: Request, store: Store, settings: Annotated[ApiSettings, Depends(get_settings)]
) -> SystemResponse:
    """Everything a client needs to label results honestly: the snapshot, the source of
    every reference run, the M3 calibration status, the HYPOTHETICAL status of every M4
    parameter, the component readiness, the request limits, the simulation workers and
    what each endpoint costs."""
    simulator: SimulationService | None = request.app.state.simulator
    return views.system(
        store,
        settings,
        # The app's own contract (generated once, then cached by FastAPI).
        endpoint_inventory(request.app.openapi()),
        None if simulator is None else simulator.status(),
    )


@router.get(
    "/models",
    name="listModels",
    tags=["forecasts"],
    summary="Forecasting models evaluated by M2",
    responses=errors(422, 503),
    openapi_extra=cost("precomputed"),
)
async def models(store: Store) -> ModelsResponse:
    """Every M2 model, the development selection and the confirmation-period
    recommendation with its pre-registered tests, and each model's MAE over horizons 1-30
    in every M2 period."""
    return views.models(store)
