"""The application factory: one FastAPI app with injected services.

``create_app(settings, store=None, simulator=None)`` builds the app. Without injected
services, the lifespan loads the reference store (every committed artifact, checked) and
starts the simulation worker processes, and stops them on shutdown. Injected services are
used as given and never stopped by the app (their creator owns them), which lets tests
share one store and one worker pool across many apps.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI

from atlas import __version__
from atlas.api.dashboard import DashboardMiddleware, try_load_dashboard
from atlas.api.docs import install_docs
from atlas.api.errors import install_error_handlers
from atlas.api.middleware import (
    DOCS_PATHS,
    EXPOSED_HEADERS,
    JsonCORSMiddleware,
    RequestContextMiddleware,
)
from atlas.api.reference import ReferenceStore, load_reference_store
from atlas.api.routers import (
    API_PREFIX,
    forecasts,
    health,
    scenarios,
    simulations,
    system,
    uncertainty,
)
from atlas.api.settings import ApiSettings, load_api_settings
from atlas.api.simulation import SimulationService

LOGGER: Final = logging.getLogger("atlas.api")

TITLE: Final = "ATLAS API"
DESCRIPTION: Final = """\
Read-only access to the ATLAS NYC 311 demand-forecasting research outputs (M2 forecasts,
M3 uncertainty, M4 decision simulation) and bounded HYPOTHETICAL simulations with the M4
linear-programming engine.

**Read before using any number.**

* Every response states what its numbers are (`integrity.data_status`): observed history,
  a projection, a historical evaluation, or a HYPOTHETICAL simulation.
* The projection is an archived projection issued from the frozen snapshot, not a live
  forecast: no outcome after the snapshot's last observed day is in it, and it has never
  been scored.
* The point forecast is not prospectively validated (`point_forecast_validated: false`).
* The M3 prediction intervals have the status **failed_calibration**: no interval or
  probability is decision-grade, and none is presented as one.
* Every M4 operational parameter is **HYPOTHETICAL**. Nothing returned is a validated
  staffing, budget or service-capacity recommendation (`validated_recommendation: false`
  is fixed by the schema).

Errors share one body: `{"error": {"code", "message", "request_id", "details"}}`.
"""
TAGS: Final = [
    {"name": "health", "description": "Liveness and readiness."},
    {"name": "system", "description": "Versions, provenance, scientific status and limits."},
    {"name": "forecasts", "description": "M2 models, projection, history and accuracy."},
    {"name": "uncertainty", "description": "M3 calibration status and projection quantiles."},
    {"name": "scenarios", "description": "The committed M4 reference run (HYPOTHETICAL)."},
    {"name": "simulations", "description": "Bounded HYPOTHETICAL simulations (M4 engine)."},
]


def create_app(
    settings: ApiSettings | None = None,
    *,
    store: ReferenceStore | None = None,
    simulator: SimulationService | None = None,
) -> FastAPI:
    """The ATLAS API (see the module docstring)."""
    resolved = settings if settings is not None else load_api_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned: SimulationService | None = None
        if app.state.store is None:
            app.state.store = load_reference_store(resolved)
            LOGGER.info(
                "reference data loaded",
                extra={"event": "startup", "ready": app.state.store.ready},
            )
        # Build the OpenAPI document now: generated lazily, its first request would stall
        # the event loop for up to a second.
        app.openapi()
        if app.state.simulator is None:
            owned = SimulationService(
                app.state.store,
                size=resolved.max_concurrent_simulations,
                timeout_seconds=resolved.simulation_timeout_seconds,
            )
            owned.start()
            app.state.simulator = owned
        try:
            yield
        finally:
            if owned is not None:
                owned.close()
                app.state.simulator = None

    docs = resolved.docs_enabled
    app = FastAPI(
        title=TITLE,
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
        # The documentation pages are this package's own (pinned files, SRI and a policy).
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
        # Stable operation ids (the route names) for generated clients.
        generate_unique_id_function=lambda route: route.name,
    )
    if docs:
        install_docs(app, "/openapi.json")
    app.state.settings = resolved
    app.state.store = store
    app.state.simulator = simulator
    install_error_handlers(app)
    for module in (health, system, forecasts, uncertainty, scenarios, simulations):
        app.include_router(module.router)
    app.add_middleware(
        JsonCORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
        expose_headers=list(EXPOSED_HEADERS),
        allow_credentials=False,
        max_age=600,
    )
    # (M7) The committed dashboard build, verified and held in memory, at its exact paths.
    dashboard = (
        try_load_dashboard(resolved.repo_root, simulations_enabled=resolved.api_key is None)
        if resolved.dashboard_enabled
        else None
    )
    app.state.dashboard = dashboard
    app.add_middleware(DashboardMiddleware, dashboard=dashboard)
    # Added last, so it is the outermost middleware: it sees every request and response.
    app.add_middleware(
        RequestContextMiddleware,
        allowed_hosts=resolved.allowed_hosts,
        max_body_bytes=resolved.max_body_bytes,
        docs_paths=DOCS_PATHS if docs else (),
        cors_origins=resolved.cors_origins,
        api_key=resolved.api_key,
        protected=[("POST", f"{API_PREFIX}/simulations")],
        dashboard_paths=dashboard.paths if dashboard is not None else frozenset(),
    )
    return app
