"""The committed M4 reference scenarios (precomputed, inspected at startup)."""

from __future__ import annotations

from typing import Annotated, Final, get_args

from fastapi import APIRouter, Depends, Path, Query
from pydantic import StringConstraints

from atlas.api import views
from atlas.api.dependencies import get_store, reject_unknown_query
from atlas.api.reference import ReferenceStore
from atlas.api.routers import API_PREFIX, VERSIONED, cost, errors
from atlas.api.schemas import (
    ComparisonMetric,
    ComparisonResponse,
    ScenarioDetailResponse,
    ScenarioListResponse,
)

router = APIRouter(
    prefix=f"{API_PREFIX}/scenarios",
    tags=["scenarios"],
    dependencies=[Depends(reject_unknown_query)],
)

Store = Annotated[ReferenceStore, Depends(get_store)]
SCENARIO_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
ScenarioId = Annotated[str, StringConstraints(pattern=SCENARIO_PATTERN)]
ALL_METRICS: list[ComparisonMetric] = list(get_args(ComparisonMetric))
#: Most scenario ids one comparison may name (unknown ids are 404, repeats are merged).
MAX_COMPARED: Final = 32


@router.get(
    "",
    name="listScenarios",
    summary="M4 reference scenarios (HYPOTHETICAL)",
    responses=errors(*VERSIONED),
    openapi_extra=cost("precomputed"),
)
async def scenario_list(store: Store) -> ScenarioListResponse:
    """One summary per scenario of the committed M4 reference run: feasibility, the
    reported plan's totals, uniqueness, binding constraints and diagnosis reasons."""
    return views.scenario_list(store)


@router.get(
    "/comparison",
    name="compareScenarios",
    summary="Compare M4 reference scenarios",
    responses=errors(*VERSIONED, "not_found"),
    openapi_extra=cost("precomputed"),
)
async def comparison(
    store: Store,
    scenario_id: Annotated[
        list[ScenarioId],
        Query(
            max_length=MAX_COMPARED,
            description="Scenarios to compare (repeatable; default: all).",
        ),
    ] = [],  # noqa: B006 - FastAPI copies query defaults per request
    baseline: Annotated[str, Query(pattern=SCENARIO_PATTERN)] = "normal",
    metric: Annotated[
        list[ComparisonMetric],
        Query(max_length=len(ALL_METRICS), description="Metrics (default: all)."),
    ] = [],  # noqa: B006
) -> ComparisonResponse:
    """The chosen scenarios side by side, each with its adjustments, terminal target and
    violation, and each metric's difference from the baseline scenario. Differences are
    between HYPOTHETICAL scenarios, not expected outcomes, and are given only when both
    plans meet every constraint (a relaxed plan's values are DIAGNOSTIC)."""
    return views.comparison(store, scenario_id, baseline, metric or ALL_METRICS)


@router.get(
    "/{scenario_id}",
    name="getScenario",
    summary="One M4 reference scenario with its daily plans",
    responses=errors(*VERSIONED, "not_found"),
    openapi_extra=cost("precomputed"),
)
async def scenario_detail(
    store: Store,
    scenario_id: Annotated[str, Path(pattern=SCENARIO_PATTERN, description="e.g. normal")],
) -> ScenarioDetailResponse:
    """The scenario as the M4 runner reported it (certificates, diagnosis, policy
    comparisons, uniqueness, shadow-price summary), every plan variant, and the daily
    schedule of the reported plan and of both policies."""
    return views.scenario_detail(store, scenario_id)
