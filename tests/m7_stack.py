"""The dashboard behind a real nginx, as production runs it (M7 end-to-end tests).

A release with the committed dashboard build is published like an activation would
(``atlas.deploy.publish``), the production site is rendered for it (``atlas.deploy.render``)
and served by a real nginx with TLS on loopback ports, with the real API behind its Unix
socket and real simulation workers. pytest uses :func:`stack` directly
(``tests/test_m7_proxy.py``); the browser tests (``web/e2e``) start this module as a
process:

    uv run python -m tests.m7_stack --profile private --https-port 8443 --http-port 8081

It runs until it receives SIGTERM or SIGINT. Browsers reach it as
``https://atlas.example.org:<port>`` (map the name to 127.0.0.1, accept the test CA);
the private profile's account is ``alice`` / :data:`PASSWORD`.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from atlas.dashboard import DIST, load_build
from atlas.deploy.layout import Layout
from atlas.deploy.publish import publish
from atlas.deploy.render import Renderer
from atlas.deploy.siteconfig import load_config
from tests.m6_helpers import Nginx, make_tls, need_tools, write
from tests.m7_helpers import make_dashboard_release

if TYPE_CHECKING:
    from atlas.api.reference import ReferenceStore
    from atlas.api.simulation import SimulationService

DOMAIN = "atlas.example.org"
KEY = "e" * 64
USER = "alice"
PASSWORD = "correct-horse-battery-staple-1234"  # noqa: S105 - a throw-away test account


@dataclass
class Stack:
    nginx: Nginx
    ca: Path
    directory: Path
    release: Path
    published: Path
    layout: Layout

    @property
    def url(self) -> str:
        return f"https://{DOMAIN}:{self.nginx.https_port}"


def _hash(password: str) -> str:
    from tests.m6_helpers import run

    result = run(["openssl", "passwd", "-6", "-stdin"], input=password + "\n")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@contextmanager
def stack(
    directory: Path,
    store: ReferenceStore,
    simulator: SimulationService,
    *,
    profile: str = "private",
    http_port: int | None = None,
    https_port: int | None = None,
) -> Iterator[Stack]:
    """The site of ``profile`` in a real nginx in front of the real API."""
    from tests.test_m6_proxy import live_api

    need_tools("nginx", "openssl")
    ca, cert, key = make_tls(directory / "tls")
    release = make_dashboard_release(directory)
    with live_api(
        store, simulator, api_key=KEY, allowed_hosts=(DOMAIN, "127.0.0.1", "localhost")
    ) as (
        socket_dir,
        _,
    ):
        layout = Layout(
            install=PurePosixPath(str(directory / "opt/atlas")),
            nginx=PurePosixPath(str(directory / "nginx-atlas")),
            acme=PurePosixPath(str(directory / "acme")),
            run=PurePosixPath(str(socket_dir)),
        )
        text = (
            f"ATLAS_DEPLOY_PROFILE={profile}\nATLAS_DEPLOY_DOMAIN={DOMAIN}\n"
            f"ATLAS_DEPLOY_IPV6=false\nATLAS_DEPLOY_TLS_CERT={cert}\nATLAS_DEPLOY_TLS_KEY={key}\n"
        )
        if profile == "public-demo":
            text += "ATLAS_DEPLOY_SECURITY_REVIEW=test-review-1\n"
        write(
            directory / "nginx-atlas/simulation-key.conf",
            f'proxy_set_header X-API-Key "{KEY}";\n',
            0o600,
        )
        write(directory / "nginx-atlas/htpasswd", f"{USER}:{_hash(PASSWORD)}\n", 0o640)
        build = load_build(release / DIST)
        published = layout.local(
            publish(layout, release.name, release, build, owner=release.stat().st_uid, chown=False)
        )
        site = Renderer.for_release(load_config(text, layout), layout, release).nginx()
        with Nginx(directory / "nginx", site, http_port=http_port, https_port=https_port) as nginx:
            yield Stack(nginx, ca, directory, release, published, layout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ATLAS dashboard behind nginx, for e2e tests")
    parser.add_argument("--profile", choices=("private", "public-demo"), default="private")
    parser.add_argument("--https-port", type=int, required=True)
    parser.add_argument("--http-port", type=int, required=True)
    parser.add_argument("--directory", type=Path, default=None)
    args = parser.parse_args(argv)

    import tempfile

    from atlas.api.reference import load_reference_store
    from atlas.api.settings import ApiSettings
    from atlas.api.simulation import SimulationService
    from tests.conftest import REPO_ROOT

    store = load_reference_store(ApiSettings(repo_root=REPO_ROOT))
    simulator = SimulationService(store, size=2, timeout_seconds=30.0)
    simulator.start()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        with (
            tempfile.TemporaryDirectory(prefix="atlas-e2e-", dir=args.directory) as directory,
            stack(
                Path(directory),
                store,
                simulator,
                profile=args.profile,
                http_port=args.http_port,
                https_port=args.https_port,
            ) as running,
        ):
            print(
                json.dumps({"url": running.url, "ca": str(running.ca), "profile": args.profile}),
                flush=True,
            )
            stop.wait()
    finally:
        simulator.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
