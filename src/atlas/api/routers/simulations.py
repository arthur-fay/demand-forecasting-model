"""Bounded interactive simulations with the M4 engine."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Depends, Response

from atlas.api import views
from atlas.api.dependencies import (
    get_simulator,
    get_store,
    reject_unknown_query,
    require_api_key,
    require_json,
)
from atlas.api.reference import ReferenceStore
from atlas.api.routers import errors
from atlas.api.schemas import (
    SimulationParametersResponse,
    SimulationRequest,
    SimulationResponse,
)
from atlas.api.simulation import SimulationService, parameter_catalogue

LOGGER: Final = logging.getLogger("atlas.api")

router = APIRouter(
    prefix="/api/v1/simulations",
    tags=["simulations"],
    dependencies=[Depends(reject_unknown_query)],
)

Store = Annotated[ReferenceStore, Depends(get_store)]

EXAMPLES: Final[dict[str, Any]] = {
    "reference": {
        "summary": "Reference defaults (the M4 normal scenario)",
        "value": {},
    },
    "smaller_budget": {
        "summary": "Half the discretionary budget (infeasible: a DIAGNOSTIC relaxed plan)",
        "value": {"parameters": {"discretionary_budget": 150000}},
    },
    "two_weeks_more_surge": {
        "summary": "14 days, more surge capacity, no terminal target",
        "value": {
            "horizon_days": 14,
            "parameters": {"surge_max_per_day": 900, "terminal_backlog_max": None},
        },
    },
    "stress_path": {
        "summary": "Demand at each day's marginal M3 90 % quantile (a stress path, no probability)",
        "value": {"demand": {"basis": "m3_marginal_quantile", "quantile": 0.9}},
    },
}


@router.get(
    "/parameters",
    name="getSimulationParameters",
    response_model=SimulationParametersResponse,
    summary="Defaults and ranges of the simulation inputs",
    responses=errors(422, 503),
)
async def parameters(store: Store) -> dict[str, Any]:
    """Every HYPOTHETICAL input of POST /api/v1/simulations with its unit, its default (the
    M4 reference configuration) and its hard range."""
    data = parameter_catalogue(store)
    return views.envelope(
        store, data, views.simulation_integrity(store, False), views.simulation_sources(store)
    )


@router.post(
    "",
    name="runSimulation",
    response_model=SimulationResponse,
    summary="Run one bounded HYPOTHETICAL simulation",
    dependencies=[Depends(require_api_key), Depends(require_json)],
    responses=errors(401, 413, 415, 422, 500, 503, 504),
)
async def simulate(
    request: Annotated[SimulationRequest, Body(openapi_examples=EXAMPLES)],
    response: Response,
    store: Store,
    simulator: Annotated[SimulationService, Depends(get_simulator)],
) -> dict[str, Any]:
    """Plan one HYPOTHETICAL city-wide unit over 1-30 days of the M2 projection with the M4
    linear programme (certified optimum, tie-broken plan, uniqueness test, infeasibility
    diagnosis, both baseline policies). Inputs are bounded (GET .../parameters); the result
    is deterministic for equal inputs (``simulation_id``). Shadow-price brackets,
    frontiers and sweeps are research analyses: they are served precomputed for the
    reference scenarios and never run on request. The compute time is in the
    ``Server-Timing`` header."""
    data, seconds = await simulator.run(request)
    response.headers["Server-Timing"] = f"compute;dur={1000 * seconds:.1f}"
    LOGGER.info(
        "simulation",
        extra={
            "event": "simulation",
            "simulation_id": data.simulation_id,
            "horizon_days": data.horizon_days,
            "feasibility": data.outcome.feasibility,
            "compute_ms": round(1000 * seconds, 1),
        },
    )
    integrity = views.simulation_integrity(store, data.demand_notice is not None)
    return views.envelope(store, data, integrity, views.simulation_sources(store))
