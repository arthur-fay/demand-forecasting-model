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
from atlas.api.routers import API_PREFIX, VERSIONED, cost, errors
from atlas.api.schemas import (
    SimulationParametersResponse,
    SimulationRequest,
    SimulationResponse,
)
from atlas.api.simulation import SimulationService, parameter_catalogue

LOGGER: Final = logging.getLogger("atlas.api")

router = APIRouter(
    prefix=f"{API_PREFIX}/simulations",
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
    summary="Defaults and ranges of the simulation inputs",
    responses=errors(*VERSIONED),
    openapi_extra=cost("precomputed"),
)
async def parameters(store: Store) -> SimulationParametersResponse:
    """Every HYPOTHETICAL input of POST /api/v1/simulations with its unit, its default (the
    M4 reference configuration) and its hard range."""
    return views.simulation_parameters(store, parameter_catalogue(store))


@router.post(
    "",
    name="runSimulation",
    summary="Run one bounded HYPOTHETICAL simulation",
    dependencies=[Depends(require_api_key), Depends(require_json)],
    responses=errors(
        *VERSIONED,
        "bad_request",
        "unauthorized",
        "payload_too_large",
        "unsupported_media_type",
        "simulation_rejected",
        "simulation_failed",
        "simulations_busy",
        "simulation_timeout",
    ),
    # The key is required only when the server sets ATLAS_API_KEY: {} makes it optional
    # (FastAPI appends this to the APIKeyHeader requirement it derives).
    openapi_extra={**cost("bounded_computation"), "security": [{}]},
)
async def simulate(
    request: Annotated[SimulationRequest, Body(openapi_examples=EXAMPLES)],
    response: Response,
    store: Store,
    simulator: Annotated[SimulationService, Depends(get_simulator)],
) -> SimulationResponse:
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
    return views.simulation(store, data)
