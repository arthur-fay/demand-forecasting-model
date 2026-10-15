"""Structured operational logging: one JSON object per line.

Only whitelisted fields are written. Request bodies, query strings, headers (the API key
among them) and client addresses are never logged; a path is truncated. Tracebacks of
unexpected exceptions go to the log, never to a client.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, Final, TextIO

from atlas.api.context import current_request_id

LOGGER_NAMES: Final = ("atlas.api", "uvicorn", "uvicorn.error")
#: Extra fields a log record may carry into the JSON line.
FIELDS: Final = (
    "event",
    "request_id",
    "method",
    "path",
    "status",
    "duration_ms",
    "simulation_id",
    "horizon_days",
    "feasibility",
    "compute_ms",
    "component",
    "ready",
    "code",
)
MAX_PATH: Final = 200


class JsonFormatter(logging.Formatter):
    """Format a record as one JSON object (sorted keys, ISO 8601 UTC timestamp)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        if "request_id" not in payload:
            request_id = current_request_id()
            if request_id is not None:
                payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, default=str)


def configure_logging(level: str, stream: TextIO | None = None) -> None:
    """Send the API and server loggers to ``stream`` (stderr) as JSON lines.

    Idempotent: a previous handler installed by this function is replaced, so repeated
    application factories in one process do not duplicate lines.
    """
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler.set_name("atlas-api-json")
    for name in LOGGER_NAMES:
        logger = logging.getLogger(name)
        for existing in list(logger.handlers):
            if existing.get_name() == "atlas-api-json":
                logger.removeHandler(existing)
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
    # The access log is written by the API's own middleware (without client addresses).
    logging.getLogger("uvicorn.access").disabled = True


def truncated_path(path: str) -> str:
    return path if len(path) <= MAX_PATH else path[:MAX_PATH] + "..."
