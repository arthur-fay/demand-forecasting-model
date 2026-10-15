"""The M2 projection, the observed history and the M2 backtest metrics."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from atlas.api import views
from atlas.api.dependencies import get_store, reject_unknown_query
from atlas.api.reference import ReferenceStore
from atlas.api.routers import API_PREFIX, VERSIONED, cost, errors
from atlas.api.schemas import (
    ForecastResponse,
    HistoryResponse,
    HorizonDaysQuery,
    IsoDate,
    MetricsResponse,
)
from atlas.planning import MAX_HORIZON_DAYS

router = APIRouter(
    prefix=f"{API_PREFIX}/forecasts",
    tags=["forecasts"],
    dependencies=[Depends(reject_unknown_query)],
)

Store = Annotated[ReferenceStore, Depends(get_store)]
ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"


@router.get(
    "",
    name="getForecast",
    summary="The future projection (projected, not validated)",
    responses=errors(*VERSIONED),
    openapi_extra=cost("precomputed"),
)
async def forecast(
    store: Store,
    horizon_days: Annotated[HorizonDaysQuery, Query(description="Days to return.")] = (
        MAX_HORIZON_DAYS
    ),
) -> ForecastResponse:
    """Point forecasts of the recommended M2 model for the days after the last observed
    day, each with ``status: projected``, and the model's historical error on M2's
    confirmation period as context."""
    return views.forecast(store, horizon_days)


@router.get(
    "/history",
    name="getHistory",
    summary="Observed daily demand (legacy-v0 snapshot)",
    responses=errors(*VERSIONED),
    openapi_extra=cost("precomputed"),
)
async def history(
    store: Store,
    start: Annotated[
        IsoDate | None, Query(description="First day, YYYY-MM-DD (default: 180 days before end).")
    ] = None,
    end: Annotated[
        IsoDate | None, Query(description="Last day, YYYY-MM-DD (default: last observed day).")
    ] = None,
) -> HistoryResponse:
    """City-wide 311 requests created per day, each with ``status: observed``; at most
    1,500 days per request, within the snapshot's dates (``GET /api/v1/system``)."""
    return views.history(store, start, end)


@router.get(
    "/metrics",
    name="getForecastMetrics",
    summary="M2 backtest accuracy by model, period and horizon",
    responses=errors(*VERSIONED, "not_found"),
    openapi_extra=cost("precomputed"),
)
async def metrics(
    store: Store,
    model_id: Annotated[str | None, Query(pattern=ID_PATTERN, description="One model.")] = None,
    period: Annotated[
        str | None, Query(pattern=ID_PATTERN, description="development, confirmation, ...")
    ] = None,
    scope: Annotated[
        Literal["bucket", "horizon"],
        Query(description="bucket: horizon groups (day_1, days_1_30, ...); horizon: h01..h30."),
    ] = "bucket",
) -> MetricsResponse:
    """Historical rolling-origin accuracy (MAE, RMSE, bias, WAPE, MASE, MAPE) of every M2
    model. These are scores of past forecasts, not prospective validation."""
    return views.metrics(store, model_id=model_id, period=period, scope=scope)
