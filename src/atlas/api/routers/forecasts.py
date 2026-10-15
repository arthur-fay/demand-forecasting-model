"""The M2 projection, the observed history and the M2 backtest metrics."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query

from atlas.api import views
from atlas.api.dependencies import get_store, reject_unknown_query
from atlas.api.reference import ReferenceStore
from atlas.api.routers import errors
from atlas.api.schemas import ForecastResponse, HistoryResponse, IsoDate, MetricsResponse

router = APIRouter(
    prefix="/api/v1/forecasts", tags=["forecasts"], dependencies=[Depends(reject_unknown_query)]
)

Store = Annotated[ReferenceStore, Depends(get_store)]
ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"


@router.get(
    "",
    name="getForecast",
    response_model=ForecastResponse,
    summary="The 30-day future projection (projected, not validated)",
    responses=errors(422, 503),
)
async def forecast(
    store: Store,
    horizon_days: Annotated[int, Query(ge=1, le=30, description="Days to return.")] = 30,
) -> dict[str, Any]:
    """Point forecasts of the recommended M2 model for the days after the last observed
    day, each with ``status: projected``, and the model's historical error on M2's
    confirmation period as context."""
    return views.forecast(store, horizon_days)


@router.get(
    "/history",
    name="getHistory",
    response_model=HistoryResponse,
    summary="Observed daily demand (legacy-v0 snapshot)",
    responses=errors(422, 503),
)
async def history(
    store: Store,
    start: Annotated[
        IsoDate | None, Query(description="First day, YYYY-MM-DD (default: 180 days before end).")
    ] = None,
    end: Annotated[
        IsoDate | None, Query(description="Last day, YYYY-MM-DD (default: last observed day).")
    ] = None,
) -> dict[str, Any]:
    """City-wide 311 requests created per day, each with ``status: observed``; at most
    1,500 days per request, within the snapshot (2023-01-01..2026-06-30)."""
    return views.history(store, start, end)


@router.get(
    "/metrics",
    name="getForecastMetrics",
    response_model=MetricsResponse,
    summary="M2 backtest accuracy by model, period and horizon",
    responses=errors(404, 422, 503),
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
) -> dict[str, Any]:
    """Historical rolling-origin accuracy (MAE, RMSE, bias, WAPE, MASE, MAPE) of every M2
    model. These are scores of past forecasts, not prospective validation."""
    return views.metrics(store, model_id=model_id, period=period, scope=scope)
