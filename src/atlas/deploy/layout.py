"""The filesystem layout of an ATLAS deployment (M6).

::

    /opt/atlas/                         root:root   0755
        releases/<id>/                  root:atlas  0550  immutable: checkout + .venv + release.json
        current -> releases/<id>        the active release (an atomically replaced link)
        dashboard/                      root:www-data 0750  (M7) dashboards for nginx
            <id>/                       root:www-data 0750  a copy of releases/<id>/web/dist
        tools/uv/                       root:root   0755  the hash-pinned uv used to build
    /etc/atlas/                         root:root   0755
        deploy.env                      root:root   0644  the deployment configuration (no secret)
        atlas.env                       root:root   0644  rendered runtime environment (no secret)
        credentials/                    root:root   0700
            api-key                     root:root   0600  delivered by systemd (LoadCredential)
    /etc/nginx/atlas/                   root:www-data 0750
        htpasswd                        root:www-data 0640  HTTP Basic users (strong hashes only)
        simulation-key.conf             root:root   0600  the key nginx adds to simulations
    /var/lib/atlas/deploy/              root:root   0700  history, locks, the last verified state
    /run/atlas/                         atlas:atlas 0755  the API's socket for nginx (api.sock)
    /var/cache/atlas/uv/                root:root   0700  download cache of release builds
    /var/www/atlas-acme/                root:root   0755  ACME HTTP-01 challenge files

Every path is absolute on the server. :class:`Layout` keeps those *host* paths (written
into rendered files) apart from the *local* paths the tooling reads and writes, which are
the host paths under ``root``: ``/`` on a server, a temporary directory in tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Final

SERVICE_USER: Final = "atlas"
SERVICE_GROUP: Final = "atlas"
#: The account of the periodic monitor (separate from the service's).
MONITOR_USER: Final = "atlas-monitor"
PROXY_GROUP: Final = "www-data"
SERVICE: Final = "atlas.service"
MONITOR_SERVICE: Final = "atlas-monitor.service"
MONITOR_TIMER: Final = "atlas-monitor.timer"
ALERT_SERVICE: Final = "atlas-alert@.service"
API_HOST: Final = "127.0.0.1"
#: Release directory names: ``<version>-<first 12 hex digits of the commit>``.
RELEASE_ID: Final = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{12}")
RELEASE_MANIFEST: Final = "release.json"


def valid_release_id(release_id: str) -> bool:
    return RELEASE_ID.fullmatch(release_id) is not None


_P = PurePosixPath


@dataclass(frozen=True)
class Layout:
    """Host paths of a deployment, and where the tooling finds them locally."""

    root: Path = Path("/")
    install: PurePosixPath = field(default=_P("/opt/atlas"))
    config: PurePosixPath = field(default=_P("/etc/atlas"))
    state: PurePosixPath = field(default=_P("/var/lib/atlas/deploy"))
    cache: PurePosixPath = field(default=_P("/var/cache/atlas/uv"))
    nginx: PurePosixPath = field(default=_P("/etc/nginx/atlas"))
    nginx_sites: PurePosixPath = field(default=_P("/etc/nginx/sites-available"))
    nginx_enabled: PurePosixPath = field(default=_P("/etc/nginx/sites-enabled"))
    systemd: PurePosixPath = field(default=_P("/etc/systemd/system"))
    journald: PurePosixPath = field(default=_P("/etc/systemd/journald.conf.d"))
    acme: PurePosixPath = field(default=_P("/var/www/atlas-acme"))
    letsencrypt: PurePosixPath = field(default=_P("/etc/letsencrypt/live"))
    run: PurePosixPath = field(default=_P("/run/atlas"))

    def local(self, host: PurePosixPath) -> Path:
        """Where the tooling reads or writes the host path ``host``."""
        return self.root / str(host).lstrip("/")

    # --- Releases ---------------------------------------------------------------------

    @property
    def releases(self) -> PurePosixPath:
        return self.install / "releases"

    @property
    def current(self) -> PurePosixPath:
        return self.install / "current"

    @property
    def tools(self) -> PurePosixPath:
        return self.install / "tools"

    @property
    def dashboard_root(self) -> PurePosixPath:
        """(M7) Where nginx reads the dashboards: one verified copy per release, readable by
        the proxy group (a release itself is readable by the service group only)."""
        return self.install / "dashboard"

    def dashboard(self, release_id: str) -> PurePosixPath:
        if not valid_release_id(release_id):
            raise ValueError(f"invalid release id {release_id!r}")
        return self.dashboard_root / release_id

    def release(self, release_id: str) -> PurePosixPath:
        if not valid_release_id(release_id):
            raise ValueError(f"invalid release id {release_id!r}")
        return self.releases / release_id

    @property
    def executable(self) -> PurePosixPath:
        """The ``atlas`` command of the active release (resolved when the unit starts)."""
        return self.current / ".venv" / "bin" / "atlas"

    @property
    def deploy_executable(self) -> PurePosixPath:
        return self.current / ".venv" / "bin" / "atlas-deploy"

    # --- Configuration and secrets ------------------------------------------------------

    @property
    def deploy_env(self) -> PurePosixPath:
        return self.config / "deploy.env"

    @property
    def runtime_env(self) -> PurePosixPath:
        return self.config / "atlas.env"

    @property
    def credentials(self) -> PurePosixPath:
        return self.config / "credentials"

    @property
    def api_key(self) -> PurePosixPath:
        return self.credentials / "api-key"

    @property
    def alert_hook(self) -> PurePosixPath:
        """An optional executable the operator installs to be alerted (see the guide)."""
        return self.config / "alert-hook"

    @property
    def htpasswd(self) -> PurePosixPath:
        return self.nginx / "htpasswd"

    @property
    def nginx_key(self) -> PurePosixPath:
        return self.nginx / "simulation-key.conf"

    @property
    def nginx_site(self) -> PurePosixPath:
        return self.nginx_sites / "atlas.conf"

    @property
    def nginx_site_enabled(self) -> PurePosixPath:
        return self.nginx_enabled / "atlas.conf"

    # --- State ----------------------------------------------------------------------------

    @property
    def history(self) -> PurePosixPath:
        return self.state / "history.jsonl"

    @property
    def lock(self) -> PurePosixPath:
        return self.state / "lock"

    @property
    def build_lock(self) -> PurePosixPath:
        """Held by ``build-release.sh`` while it writes a release."""
        return self.state / "build.lock"

    @property
    def good_state(self) -> PurePosixPath:
        """The last verified state: its release and the ``deploy.env`` it was verified with."""
        return self.state / "last-good.json"

    # --- Runtime --------------------------------------------------------------------------

    @property
    def runtime_dir(self) -> PurePosixPath:
        """The service's own runtime directory (systemd ``RuntimeDirectory=atlas``)."""
        return self.run

    @property
    def api_socket(self) -> PurePosixPath:
        """nginx reaches the API here: only the service can create this path."""
        return self.runtime_dir / "api.sock"

    def unit(self, name: str) -> PurePosixPath:
        return self.systemd / name


PRODUCTION: Final = Layout()
