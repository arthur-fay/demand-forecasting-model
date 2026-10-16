"""The deployment configuration (``/etc/atlas/deploy.env``), validated and refused when unsafe.

One small file of ``KEY=value`` lines says how this server exposes ATLAS. Everything else
(the runtime environment of the API, the systemd unit, the nginx site, the rate limits)
is rendered from it by :mod:`atlas.deploy.render`, so the pieces cannot disagree.

Access profiles (private first):

``tunnel``
    Nothing is exposed: the API listens on loopback only and is reached through an SSH
    tunnel (``ssh -L 8000:127.0.0.1:8000 server``). No nginx site, no TLS. The default and
    the first step of any deployment.
``private``
    nginx serves HTTPS for one domain. Every route, health probes included, requires HTTP
    Basic authentication (strong password hashes only), optionally from allowed networks
    only. Simulations are rate-limited per client and nginx adds the API key itself.
``public-demo``
    Unauthenticated read-only access to the precomputed endpoints, for a later public
    demonstration. Simulations, readiness details and documentation are not routed. It is
    refused unless ``ATLAS_DEPLOY_SECURITY_REVIEW`` names a completed security review.

The parser is deliberately strict: unknown or repeated keys, quotes, shell syntax and
anything that looks like a secret are errors, because a silently ignored line is how a
limit disappears. Secrets never go in this file (see :mod:`atlas.deploy.permissions`).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from atlas.deploy.layout import Layout

PROFILES: Final = ("tunnel", "private", "public-demo")
RESOURCES: Final = ("low", "standard")

PREFIX: Final = "ATLAS_DEPLOY_"
KEYS: Final = (
    "PROFILE",
    "RESOURCES",
    "DOMAIN",
    "API_PORT",
    "TLS_CERT",
    "TLS_KEY",
    "ALLOW_CIDRS",
    "HSTS_MAX_AGE",
    "SECURITY_REVIEW",
    "SIMULATION_TIMEOUT_SECONDS",
    "LOG_LEVEL",
    "API_DOCS",
    "IPV6",
)
_SECRET_WORDS: Final = ("KEY", "SECRET", "PASSWORD", "PASSWD", "TOKEN", "CREDENTIAL")
_VALUE: Final = re.compile(r"[A-Za-z0-9._:/,@+=-]*")
_LABEL: Final = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN: Final = re.compile(rf"(?:{_LABEL}\.)+[a-z][a-z0-9-]{{0,61}}[a-z0-9]")
_REVIEW: Final = re.compile(r"[A-Za-z0-9._:/#-]{4,200}")
_TRUE: Final = ("true", "yes", "on", "1")
_FALSE: Final = ("false", "no", "off", "0")
HSTS_RANGE: Final = (300, 63_072_000)
PORT_RANGE: Final = (1024, 65_535)
TIMEOUT_RANGE: Final = (1.0, 60.0)
LOG_LEVELS: Final = ("DEBUG", "INFO", "WARNING", "ERROR")


class DeployConfigError(ValueError):
    """The deployment configuration is invalid or unsafe (every problem is listed)."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Resources:
    """What one resource profile allows the service and the proxy to use."""

    name: str
    workers: int
    memory_high: str
    memory_max: str
    tasks_max: int
    #: Requests per second and burst of the general per-client limit.
    read_rate: str
    read_burst: int
    #: Simulations per minute and burst of the per-client compute limit.
    compute_rate: str
    compute_burst: int
    connections_per_client: int


RESOURCE_PROFILES: Final = {
    # Measured under systemd with the release venv (docs/DEPLOYMENT.md, section 3):
    # server 180-184 MiB, each worker 120-126 MiB, the resource tracker 12 MiB.
    "low": Resources("low", 1, "400M", "512M", 96, "10r/s", 40, "12r/m", 3, 16),
    "standard": Resources("standard", 2, "640M", "768M", 128, "10r/s", 40, "30r/m", 6, 16),
}


@dataclass(frozen=True)
class DeployConfig:
    profile: str
    resources: Resources
    domain: str | None
    api_port: int
    tls_cert: PurePosixPath | None
    tls_key: PurePosixPath | None
    allow_cidrs: tuple[str, ...]
    hsts_max_age: int
    security_review: str | None
    simulation_timeout_seconds: float
    log_level: str
    api_docs: bool
    ipv6: bool = False

    @property
    def exposed(self) -> bool:
        """Whether nginx serves this deployment beyond the server itself."""
        return self.profile != "tunnel"

    @property
    def simulations_routed(self) -> bool:
        return self.profile in ("tunnel", "private")

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        """The ``Host`` names the API accepts: the domain (via nginx, when the profile
        exposes it) and loopback."""
        local = ("127.0.0.1", "localhost", "::1")
        return (self.domain, *local) if self.domain and self.exposed else local


def parse_env_text(text: str, *, source: str = "deploy.env") -> dict[str, str]:
    """``KEY=value`` lines; comments and blank lines ignored; everything else refused."""
    values: dict[str, str] = {}
    problems: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        name = name.strip()
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            problems.append(f"{source}:{number}: expected KEY=value")
            continue
        value = value.strip()
        if not _VALUE.fullmatch(value):
            problems.append(
                f"{source}:{number}: {name} has characters outside [A-Za-z0-9._:/,@+=-] "
                "(no quotes, spaces or shell syntax)"
            )
            continue
        if name in values:
            problems.append(f"{source}:{number}: {name} is set twice")
            continue
        values[name] = value
    if problems:
        raise DeployConfigError(problems)
    return values


def _integer(raw: str, name: str, bounds: tuple[int, int], problems: list[str]) -> int | None:
    if not raw.isdigit():
        problems.append(f"{name} must be a whole number")
        return None
    value = int(raw)
    if not bounds[0] <= value <= bounds[1]:
        problems.append(f"{name} must be within {bounds[0]}..{bounds[1]}")
        return None
    return value


def parse_config(values: Mapping[str, str], layout: Layout | None = None) -> DeployConfig:
    """Validate the configuration values; every problem found is reported together."""
    layout = layout or Layout()
    problems: list[str] = []
    for name in values:
        short = name.removeprefix(PREFIX)
        if not name.startswith(PREFIX) or short not in KEYS:
            if any(word in name for word in _SECRET_WORDS):
                problems.append(
                    f"{name}: secrets never go in deploy.env; the API key is a systemd "
                    "credential and passwords live in the nginx htpasswd file"
                )
            else:
                problems.append(f"{name} is not a deployment setting")

    def get(key: str, default: str = "") -> str:
        return values.get(PREFIX + key, default).strip()

    profile = get("PROFILE", "tunnel")
    if profile not in PROFILES:
        problems.append(f"{PREFIX}PROFILE must be one of {', '.join(PROFILES)}")
    resources_name = get("RESOURCES", "standard")
    resources = RESOURCE_PROFILES.get(resources_name)
    if resources is None:
        problems.append(f"{PREFIX}RESOURCES must be one of {', '.join(RESOURCES)}")

    domain = get("DOMAIN").lower() or None
    if domain is not None and not _domain_ok(domain):
        problems.append(
            f"{PREFIX}DOMAIN {domain!r} must be a fully qualified DNS name (not an IP "
            "address, a wildcard or a single label)"
        )
    if profile in ("private", "public-demo") and domain is None:
        problems.append(f"the {profile} profile needs {PREFIX}DOMAIN (the TLS certificate name)")

    port = _integer(get("API_PORT", "8000"), f"{PREFIX}API_PORT", PORT_RANGE, problems)

    tls_cert = tls_key = None
    if domain is not None and profile != "tunnel":
        live = layout.letsencrypt / domain
        tls_cert = PurePosixPath(get("TLS_CERT") or str(live / "fullchain.pem"))
        tls_key = PurePosixPath(get("TLS_KEY") or str(live / "privkey.pem"))
        for name, path in (("TLS_CERT", tls_cert), ("TLS_KEY", tls_key)):
            if not path.is_absolute() or ".." in path.parts:
                problems.append(f"{PREFIX}{name} must be an absolute path without '..'")
    elif get("TLS_CERT") or get("TLS_KEY"):
        problems.append("TLS files apply to the private and public-demo profiles only")

    cidrs: list[str] = []
    for item in (part.strip() for part in get("ALLOW_CIDRS").split(",") if part.strip()):
        try:
            network = ipaddress.ip_network(item, strict=True)
        except ValueError:
            problems.append(f"{PREFIX}ALLOW_CIDRS: {item!r} is not a network (e.g. 203.0.113.0/24)")
            continue
        if network.prefixlen == 0:
            problems.append(
                f"{PREFIX}ALLOW_CIDRS: {item} allows every address; leave the setting empty "
                "instead of pretending to restrict access"
            )
            continue
        cidrs.append(str(network))
    if cidrs and profile == "tunnel":
        problems.append("ALLOW_CIDRS applies to nginx, which the tunnel profile does not use")

    hsts = _integer(get("HSTS_MAX_AGE", "31536000"), f"{PREFIX}HSTS_MAX_AGE", HSTS_RANGE, problems)

    review = get("SECURITY_REVIEW") or None
    if review is not None and not _REVIEW.fullmatch(review):
        problems.append(f"{PREFIX}SECURITY_REVIEW must be a reference such as a URL or an id")
    if profile == "public-demo" and review is None:
        problems.append(
            "the public-demo profile exposes ATLAS without authentication: it is refused until "
            f"{PREFIX}SECURITY_REVIEW names the completed security review that approved it"
        )

    timeout_raw = get("SIMULATION_TIMEOUT_SECONDS", "10")
    timeout: float | None
    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = None
    if timeout is None or not TIMEOUT_RANGE[0] <= timeout <= TIMEOUT_RANGE[1]:
        problems.append(f"{PREFIX}SIMULATION_TIMEOUT_SECONDS must be within 1..60")
        timeout = None

    level = get("LOG_LEVEL", "INFO").upper()
    if level not in LOG_LEVELS:
        problems.append(f"{PREFIX}LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}")

    docs_raw = get("API_DOCS", "false").lower()
    docs = docs_raw in _TRUE
    if docs_raw not in _TRUE + _FALSE:
        problems.append(f"{PREFIX}API_DOCS must be true or false")
    if docs and profile != "tunnel":
        problems.append(
            "the interactive API documentation loads scripts from a CDN and is for local use: "
            f"{PREFIX}API_DOCS=true is accepted with the tunnel profile only"
        )

    # Off unless asked for: nginx's per-client limits count each address, and one IPv6
    # client usually holds a whole /64 of them.
    ipv6_raw = get("IPV6", "false").lower()
    if ipv6_raw not in _TRUE + _FALSE:
        problems.append(f"{PREFIX}IPV6 must be true or false (whether nginx listens on IPv6)")

    if problems or resources is None or port is None or hsts is None or timeout is None:
        raise DeployConfigError(problems)
    return DeployConfig(
        profile=profile,
        resources=resources,
        domain=domain,
        api_port=port,
        tls_cert=tls_cert,
        tls_key=tls_key,
        allow_cidrs=tuple(cidrs),
        hsts_max_age=hsts,
        security_review=review,
        simulation_timeout_seconds=timeout,
        log_level=level,
        api_docs=docs,
        ipv6=ipv6_raw in _TRUE,
    )


def load_config(text: str, layout: Layout | None = None) -> DeployConfig:
    return parse_config(parse_env_text(text), layout)


def _domain_ok(domain: str) -> bool:
    if len(domain) > 253 or domain in ("localhost",) or domain.endswith(".localhost"):
        return False
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return _DOMAIN.fullmatch(domain) is not None
    return False


def runtime_environment(config: DeployConfig) -> dict[str, str]:
    """The ``ATLAS_API_*`` environment of the service (no secret: the key is a credential).

    CORS stays off: the dashboard (M7) is meant to be served from the same origin.
    """
    return {
        "ATLAS_API_ALLOWED_HOSTS": ",".join(config.allowed_hosts),
        "ATLAS_API_MAX_CONCURRENT_SIMULATIONS": str(config.resources.workers),
        "ATLAS_API_SIMULATION_TIMEOUT_SECONDS": f"{config.simulation_timeout_seconds:g}",
        "ATLAS_API_MAX_BODY_BYTES": "16384",
        "ATLAS_API_DOCS": "true" if config.api_docs else "false",
        "ATLAS_API_LOG_LEVEL": config.log_level,
    }
