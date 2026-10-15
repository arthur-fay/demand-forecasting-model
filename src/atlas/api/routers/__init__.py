"""HTTP routers (transport layer): parameters, status codes and documented responses only."""

from __future__ import annotations

from typing import Any, Final

from atlas.api.schemas import ErrorResponse

_DESCRIPTIONS: Final = {
    400: "Invalid Host header (invalid_host).",
    401: "Missing or wrong API key (unauthorized).",
    404: "Unknown resource (not_found).",
    413: "Request body above the limit (payload_too_large).",
    415: "Body is not application/json (unsupported_media_type).",
    422: "Invalid request or rejected simulation input (validation_error, simulation_rejected).",
    500: "Unexpected failure or no certified result (internal_error, simulation_failed).",
    503: "Reference data unavailable, or all simulation workers busy "
    "(artifact_unavailable, simulations_busy).",
    504: "Simulation stopped at its deadline (simulation_timeout).",
}


def errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses`` for the error statuses a route can return."""
    return {code: {"model": ErrorResponse, "description": _DESCRIPTIONS[code]} for code in codes}
