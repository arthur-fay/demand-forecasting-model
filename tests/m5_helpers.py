"""Shared helpers for the M5 API tests.

Expected values come from the committed files under ``docs/results`` and
``configs/optimization``, read here with ``json`` and ``csv`` independently of the API code.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from atlas.api.app import create_app
from atlas.api.reference import ReferenceStore
from atlas.api.settings import ApiSettings
from atlas.api.simulation import SimulationService
from tests.conftest import REPO_ROOT

RESULTS = REPO_ROOT / "docs" / "results"
M2_FULL = RESULTS / "atlas-m2-rolling-origin-v1" / "full"
M2_PROJECTION = RESULTS / "atlas-m2-rolling-origin-v1" / "projection"
M3_FULL = RESULTS / "atlas-m3-intervals-v1" / "full"
M3_PROJECTION = RESULTS / "atlas-m3-intervals-v1" / "projection"
M4_RUN = RESULTS / "atlas-m4-optimization-v1"
M4_CONFIG = REPO_ROOT / "configs" / "optimization" / "atlas-m4-optimization-v1.json"
HOSTS = ("testserver", "localhost", "127.0.0.1")
# The labels every API data response must carry.
NEVER_TRUE = ("point_forecast_validated", "validated_recommendation")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def settings(root: Path = REPO_ROOT, **changes: Any) -> ApiSettings:
    base = ApiSettings(repo_root=root, allowed_hosts=HOSTS)
    return replace(base, **changes)


@contextmanager
def client(
    store: ReferenceStore | None,
    simulator: SimulationService | None = None,
    *,
    raise_server_exceptions: bool = True,
    **changes: Any,
) -> Iterator[TestClient]:
    """A test client of an app with the given (shared) services injected."""
    root = store.settings.repo_root if store is not None else REPO_ROOT
    app = create_app(settings(root, **changes), store=store, simulator=simulator)
    with TestClient(app, raise_server_exceptions=raise_server_exceptions) as test_client:
        yield test_client


def tree_hashes(*roots: Path) -> dict[str, str]:
    """SHA-256 of every file below ``roots`` (to prove nothing was written)."""
    hashes = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


#: What the API reads, relative to the repository root (the snapshot inputs come from
#: the ``repo_copy`` fixture).
API_INPUTS = (
    "configs/optimization",
    "docs/results/atlas-m2-rolling-origin-v1/full",
    "docs/results/atlas-m2-rolling-origin-v1/projection",
    "docs/results/atlas-m3-intervals-v1/full",
    "docs/results/atlas-m3-intervals-v1/projection",
    "docs/results/atlas-m4-optimization-v1",
)


def add_api_inputs(target: Path) -> Path:
    for relative in API_INPUTS:
        shutil.copytree(REPO_ROOT / relative, target / relative)
    return target


def assert_envelope(body: dict[str, Any], data_status: str) -> None:
    """The integrity labels every data response must carry."""
    integrity = body["integrity"]
    assert integrity["data_status"] == data_status
    for name in NEVER_TRUE:
        assert integrity[name] is False
    assert integrity["uncertainty_interval_status"] == "failed_calibration"
    assert integrity["uncertainty_decision_grade"] is False
    assert integrity["notices"]
    provenance = body["provenance"]
    assert provenance["snapshot_id"] == "legacy-v0"
    assert provenance["data_fingerprint"] == (
        "5ab8b3a59e107158da9a3a285ea58a22c566f5b0da067e0f3fae6ee5e3d73a4d"
    )
    for source in provenance["sources"]:
        assert not Path(source["path"]).is_absolute()
        assert ".." not in Path(source["path"]).parts
        # Every hash names its file, and is the hash of that committed file.
        for name, sha256 in source["files"].items():
            path = REPO_ROOT / source["path"] / name
            assert hashlib.sha256(path.read_bytes()).hexdigest() == sha256, (source, name)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@contextmanager
def live_server(
    store: ReferenceStore,
    simulator: SimulationService | None = None,
    *,
    read_seconds: float | None = None,
    **changes: Any,
) -> Iterator[int]:
    """A real uvicorn server, configured like ``atlas api serve``, on a free loopback port
    (in a thread); yields the port. ``read_seconds`` shortens the request-read deadline."""
    import uvicorn

    from atlas.api.server import DeadlineH11Protocol, server_config

    port = free_port()
    app = create_app(
        settings(store.settings.repo_root, **changes), store=store, simulator=simulator
    )
    config = server_config(app, "127.0.0.1", port)
    if read_seconds is not None:
        config.http = type("TestProtocol", (DeadlineH11Protocol,), {"read_seconds": read_seconds})
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("the test server did not start")
        time.sleep(0.02)
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=30)
