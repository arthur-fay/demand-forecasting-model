"""Settings of the ATLAS HTTP API (M5).

Resolved like the rest of ATLAS: explicit arguments, then environment variables, then
defaults (:func:`load_api_settings`). Every limit has a hard range, so a typo in an
environment variable cannot remove a limit; an invalid value is a
:class:`~atlas.errors.ConfigError`, never a silent default.

The API serves only what the pinned M4 reference configuration names (the M2 projection
and accuracy, the M3 calibration status and projection) and the committed M4 reference run.
None of these locations is configurable through the API settings: a request can never
choose a file.

The one secret is the optional API key protecting the simulation endpoint. It is kept out
of ``repr`` and never logged.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from atlas.config import DEFAULT_SNAPSHOT_ID, ENV_SNAPSHOT_ID, load_settings
from atlas.errors import ConfigError

ENV_CORS_ORIGINS: Final = "ATLAS_API_CORS_ORIGINS"
ENV_ALLOWED_HOSTS: Final = "ATLAS_API_ALLOWED_HOSTS"
ENV_API_KEY: Final = "ATLAS_API_KEY"
#: The request header carrying the API key.
API_KEY_HEADER: Final = "X-API-Key"
ENV_MAX_BODY_BYTES: Final = "ATLAS_API_MAX_BODY_BYTES"
ENV_MAX_SIMULATIONS: Final = "ATLAS_API_MAX_CONCURRENT_SIMULATIONS"
ENV_SIMULATION_TIMEOUT: Final = "ATLAS_API_SIMULATION_TIMEOUT_SECONDS"
ENV_DOCS: Final = "ATLAS_API_DOCS"
ENV_LOG_LEVEL: Final = "ATLAS_API_LOG_LEVEL"

#: The pinned M4 reference configuration: every input the API serves is named by it.
REFERENCE_CONFIG: Final = "configs/optimization/atlas-m4-optimization-v1.json"
#: The committed M4 reference run (precomputed, verified results; never recomputed).
REFERENCE_RUN_DIR: Final = "docs/results/atlas-m4-optimization-v1"

#: (default, lowest, highest) of each numeric limit.
MAX_BODY_BYTES: Final = (16_384, 1_024, 1_048_576)
MAX_CONCURRENT_SIMULATIONS: Final = (2, 1, 16)
SIMULATION_TIMEOUT_SECONDS: Final = (10.0, 1.0, 120.0)
LOG_LEVELS: Final = ("DEBUG", "INFO", "WARNING", "ERROR")
#: An API key shorter than this is refused (it would be guessable).
MIN_API_KEY_LENGTH: Final = 24

_ORIGIN: Final = re.compile(r"https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?")
_LABEL: Final = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
#: A host name or IPv4 address; a wildcard needs at least two labels after ``*.``.
_HOST: Final = re.compile(rf"{_LABEL}(?:\.{_LABEL})*|\*\.{_LABEL}(?:\.{_LABEL})+")
_API_KEY: Final = re.compile(r"[\x21-\x7e]+")
_TRUE: Final = ("1", "true", "yes", "on")
_FALSE: Final = ("0", "false", "no", "off")


@dataclass(frozen=True)
class ApiSettings:
    """Validated settings of one API process (see the module docstring)."""

    repo_root: Path
    snapshot_id: str = DEFAULT_SNAPSHOT_ID
    cors_origins: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")
    api_key: str | None = field(default=None, repr=False)
    max_body_bytes: int = MAX_BODY_BYTES[0]
    max_concurrent_simulations: int = MAX_CONCURRENT_SIMULATIONS[0]
    simulation_timeout_seconds: float = SIMULATION_TIMEOUT_SECONDS[0]
    docs_enabled: bool = True
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        for origin in self.cors_origins:
            if not _ORIGIN.fullmatch(origin):
                raise ConfigError(
                    f"invalid CORS origin {origin!r}: use scheme://host[:port] with http or "
                    "https, no path and no wildcard"
                )
        for host in self.allowed_hosts:
            if not (_HOST.fullmatch(host) or _ipv6(host)):
                raise ConfigError(
                    f"invalid allowed host {host!r}: use a host name, an IPv4 or IPv6 address "
                    "(no brackets), optionally with a leading '*.' for subdomains (a bare '*' "
                    "is not accepted)"
                )
        if not self.allowed_hosts:
            raise ConfigError("at least one allowed host is required")
        if self.api_key is not None and (
            len(self.api_key) < MIN_API_KEY_LENGTH or not _API_KEY.fullmatch(self.api_key)
        ):
            raise ConfigError(
                f"{ENV_API_KEY} must be at least {MIN_API_KEY_LENGTH} printable characters "
                "without spaces"
            )
        _within("max_body_bytes", self.max_body_bytes, MAX_BODY_BYTES)
        _within(
            "max_concurrent_simulations",
            self.max_concurrent_simulations,
            MAX_CONCURRENT_SIMULATIONS,
        )
        _within(
            "simulation_timeout_seconds",
            self.simulation_timeout_seconds,
            SIMULATION_TIMEOUT_SECONDS,
        )
        if self.log_level not in LOG_LEVELS:
            raise ConfigError(f"log level must be one of {', '.join(LOG_LEVELS)}")

    @property
    def simulation_requires_key(self) -> bool:
        return self.api_key is not None


def api_key_matches(presented: str | None, expected: str) -> bool:
    """Whether ``presented`` is the configured API key (constant-time comparison)."""
    return presented is not None and hmac.compare_digest(
        presented.encode("utf-8"), expected.encode("utf-8")
    )


def available_cpus() -> int:
    """CPUs this process may run on (its affinity set, where the platform has one)."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:  # pragma: no cover - platforms without sched_getaffinity
        return max(1, os.cpu_count() or 1)


def _ipv6(host: str) -> bool:
    try:
        ipaddress.IPv6Address(host)
    except ValueError:
        return False
    return True


def _within(name: str, value: float, limits: tuple[float, float, float]) -> None:
    _, low, high = limits
    if isinstance(value, bool) or not low <= value <= high:
        raise ConfigError(f"{name} must be within {low:g}..{high:g}, got {value!r}")


def _list(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _number(environ: Mapping[str, str], name: str, kind: type[int] | type[float]) -> float | None:
    raw = environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return kind(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _flag(environ: Mapping[str, str], name: str) -> bool | None:
    raw = environ.get(name, "").strip().lower()
    if not raw:
        return None
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(f"{name} must be one of {', '.join(_TRUE + _FALSE)}, got {raw!r}")


def load_api_settings(
    *,
    repo_root: Path | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> ApiSettings:
    """Resolve the API settings from arguments, then ``ATLAS_API_*`` variables, then defaults."""
    environ = os.environ if env is None else env
    base = load_settings(
        repo_root=repo_root,
        snapshot_id=environ.get(ENV_SNAPSHOT_ID, "").strip() or None,
        env=environ,
        cwd=cwd,
    )
    defaults = ApiSettings(repo_root=base.repo_root, snapshot_id=base.snapshot_id)
    hosts = _list(environ.get(ENV_ALLOWED_HOSTS, ""))
    body = _number(environ, ENV_MAX_BODY_BYTES, int)
    slots = _number(environ, ENV_MAX_SIMULATIONS, int)
    cpus = available_cpus()
    if slots is not None and int(slots) > cpus:
        # The deadline is wall-clock time: workers beyond the CPUs slow every simulation.
        raise ConfigError(
            f"{ENV_MAX_SIMULATIONS}={int(slots)} exceeds the {cpus} CPUs available to this "
            "process: each simulation worker needs its own CPU to meet the deadline"
        )
    timeout = _number(environ, ENV_SIMULATION_TIMEOUT, float)
    docs = _flag(environ, ENV_DOCS)
    return ApiSettings(
        repo_root=base.repo_root,
        snapshot_id=base.snapshot_id,
        cors_origins=_list(environ.get(ENV_CORS_ORIGINS, "")),
        allowed_hosts=hosts or defaults.allowed_hosts,
        api_key=environ.get(ENV_API_KEY, "") or None,
        max_body_bytes=defaults.max_body_bytes if body is None else int(body),
        max_concurrent_simulations=(
            min(defaults.max_concurrent_simulations, cpus) if slots is None else int(slots)
        ),
        simulation_timeout_seconds=defaults.simulation_timeout_seconds
        if timeout is None
        else timeout,
        docs_enabled=defaults.docs_enabled if docs is None else docs,
        log_level=environ.get(ENV_LOG_LEVEL, "").strip().upper() or defaults.log_level,
    )
