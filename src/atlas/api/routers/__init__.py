"""HTTP routers (transport layer): parameters, status codes and documented responses only."""

from __future__ import annotations

from typing import Any, Final, Literal

from atlas.api.problems import MEANING, STATUS_OF, ErrorCode
from atlas.api.schemas import API_VERSION, ErrorResponse

#: Prefix of every versioned route.
API_PREFIX: Final = f"/api/{API_VERSION}"
#: OpenAPI extension naming what an operation costs (also listed by GET /api/v1/system).
COST_KEY: Final = "x-atlas-cost"
Cost = Literal["static", "precomputed", "bounded_computation"]

#: Errors any operation can return: an unknown or repeated query parameter, a Host the
#: server does not serve, an unexpected failure.
COMMON: Final[tuple[ErrorCode, ...]] = ("validation_error", "invalid_host", "internal_error")
#: ... and, on every versioned operation, the in-flight cap and an uninitialised store.
VERSIONED: Final[tuple[ErrorCode, ...]] = (*COMMON, "server_busy", "artifact_unavailable")


def errors(*codes: ErrorCode) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses`` for the error codes a route can return, grouped by status."""
    by_status: dict[int, list[ErrorCode]] = {}
    for code in dict.fromkeys(codes):
        by_status.setdefault(STATUS_OF[code], []).append(code)
    return {
        status: {
            "model": ErrorResponse,
            "description": "; ".join(f"{code}: {MEANING[code]}" for code in names) + ".",
        }
        for status, names in sorted(by_status.items())
    }


def cost(kind: Cost) -> dict[str, Any]:
    """``openapi_extra`` of a route: what one call costs (see ``docs/API.md``, section 9).

    static: process state only; precomputed: committed results read and checked at
    startup; bounded_computation: one bounded LP solve in a worker process. Research
    analyses are never run on request.
    """
    return {COST_KEY: kind}
