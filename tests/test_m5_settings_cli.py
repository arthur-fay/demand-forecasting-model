"""API settings, the ``atlas api`` commands, structured logging and the benchmark."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from atlas.api import logs
from atlas.api.benchmark import _stats, run_benchmark
from atlas.api.logs import JsonFormatter, configure_logging
from atlas.api.server import openapi_document
from atlas.api.settings import ApiSettings, load_api_settings
from atlas.cli import EXIT_OK, EXIT_USAGE, main
from atlas.errors import ConfigError
from tests.conftest import REPO_ROOT
from tests.m5_helpers import settings

ENV_ROOT = {"ATLAS_REPO_ROOT": str(REPO_ROOT)}


def test_defaults() -> None:
    resolved = load_api_settings(env=ENV_ROOT)
    assert resolved.repo_root == REPO_ROOT
    assert resolved.cors_origins == ()
    assert resolved.allowed_hosts == ("localhost", "127.0.0.1")
    assert resolved.api_key is None
    assert resolved.max_body_bytes == 16_384
    assert resolved.max_concurrent_simulations == 2
    assert resolved.simulation_timeout_seconds == 10.0
    assert resolved.docs_enabled is True
    assert resolved.log_level == "INFO"
    assert resolved.public_limits()["simulation_requires_api_key"] is False


def test_environment_overrides() -> None:
    env = {
        **ENV_ROOT,
        "ATLAS_API_CORS_ORIGINS": "http://localhost:5173, https://atlas.example.org",
        "ATLAS_API_ALLOWED_HOSTS": "atlas.example.org,*.example.org",
        "ATLAS_API_KEY": "z" * 32,
        "ATLAS_API_MAX_BODY_BYTES": "4096",
        "ATLAS_API_MAX_CONCURRENT_SIMULATIONS": "4",
        "ATLAS_API_SIMULATION_TIMEOUT_SECONDS": "2.5",
        "ATLAS_API_DOCS": "off",
        "ATLAS_API_LOG_LEVEL": "warning",
    }
    resolved = load_api_settings(env=env)
    assert resolved.cors_origins == ("http://localhost:5173", "https://atlas.example.org")
    assert resolved.allowed_hosts == ("atlas.example.org", "*.example.org")
    assert resolved.max_body_bytes == 4096
    assert resolved.max_concurrent_simulations == 4
    assert resolved.simulation_timeout_seconds == 2.5
    assert resolved.docs_enabled is False
    assert resolved.log_level == "WARNING"
    assert "z" * 32 not in repr(resolved)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ATLAS_API_CORS_ORIGINS", "*"),
        ("ATLAS_API_CORS_ORIGINS", "http://localhost:5173/path"),
        ("ATLAS_API_CORS_ORIGINS", "ftp://localhost"),
        ("ATLAS_API_ALLOWED_HOSTS", "*"),
        ("ATLAS_API_ALLOWED_HOSTS", "bad host"),
        ("ATLAS_API_KEY", "short"),
        ("ATLAS_API_MAX_BODY_BYTES", "0"),
        ("ATLAS_API_MAX_BODY_BYTES", "10000000"),
        ("ATLAS_API_MAX_BODY_BYTES", "lots"),
        ("ATLAS_API_MAX_CONCURRENT_SIMULATIONS", "0"),
        ("ATLAS_API_MAX_CONCURRENT_SIMULATIONS", "17"),
        ("ATLAS_API_MAX_CONCURRENT_SIMULATIONS", "1.5"),
        ("ATLAS_API_SIMULATION_TIMEOUT_SECONDS", "0.5"),
        ("ATLAS_API_SIMULATION_TIMEOUT_SECONDS", "inf"),
        ("ATLAS_API_SIMULATION_TIMEOUT_SECONDS", "nan"),
        ("ATLAS_API_DOCS", "maybe"),
        ("ATLAS_API_LOG_LEVEL", "TRACE"),
        ("ATLAS_SNAPSHOT_ID", "../etc"),
    ],
)
def test_invalid_settings_are_refused(name: str, value: str) -> None:
    with pytest.raises(ConfigError):
        load_api_settings(env={**ENV_ROOT, name: value})


def test_settings_reject_booleans_as_numbers() -> None:
    with pytest.raises(ConfigError):
        ApiSettings(repo_root=REPO_ROOT, max_body_bytes=True)
    with pytest.raises(ConfigError):
        ApiSettings(repo_root=REPO_ROOT, allowed_hosts=())


def test_repository_root_is_discovered(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_api_settings(env={}, cwd=tmp_path)
    assert load_api_settings(env={}, cwd=REPO_ROOT / "src").repo_root == REPO_ROOT


# --- CLI ----------------------------------------------------------------------------------


def test_cli_openapi_prints_the_contract() -> None:
    out = io.StringIO()
    assert main(["--repo-root", str(REPO_ROOT), "api", "openapi"], stdout=out) == EXIT_OK
    assert json.loads(out.getvalue()) == openapi_document(settings())


def test_cli_refuses_a_public_bind_without_a_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ATLAS_API_KEY", raising=False)
    code = main(["--repo-root", str(REPO_ROOT), "api", "serve", "--host", "0.0.0.0"])  # noqa: S104
    assert code == EXIT_USAGE
    assert "refusing to bind" in capsys.readouterr().err


def test_cli_serve_runs_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    seen: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        seen.update(kwargs, app=app)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.delenv("ATLAS_API_KEY", raising=False)
    try:
        assert main(["--repo-root", str(REPO_ROOT), "api", "serve", "--port", "8123"]) == EXIT_OK
    finally:
        for name in logs.LOGGER_NAMES:  # undo configure_logging for later tests
            logger = logging.getLogger(name)
            logger.handlers = [h for h in logger.handlers if h.get_name() != "atlas-api-json"]
            logger.propagate = True
            logger.setLevel(logging.NOTSET)
        logging.getLogger("uvicorn.access").disabled = False
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 8123
    assert seen["server_header"] is False
    assert seen["access_log"] is False
    assert seen["proxy_headers"] is False
    assert seen["limit_concurrency"] == 64


def test_cli_bench_validates_its_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--repo-root", str(REPO_ROOT), "api", "bench", "--requests", "0"])
    assert code == EXIT_USAGE
    assert "--requests" in capsys.readouterr().err


# --- Logging ------------------------------------------------------------------------------


def test_json_lines_keep_only_whitelisted_fields() -> None:
    record = logging.LogRecord("atlas.api", logging.INFO, __file__, 1, "request", None, None)
    record.event = "request"
    record.request_id = "abc12345"
    record.status = 200
    record.api_key = "secret-value-never-logged"
    line = json.loads(JsonFormatter().format(record))
    assert line["event"] == "request"
    assert line["request_id"] == "abc12345"
    assert line["status"] == 200
    assert "api_key" not in line
    assert line["ts"].endswith("+00:00")


def test_exceptions_are_logged_with_traceback() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "atlas.api", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )
    line = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in line["exception"]


def test_configure_logging_is_idempotent() -> None:
    stream = io.StringIO()
    try:
        configure_logging("INFO", stream)
        configure_logging("INFO", stream)
        logging.getLogger("atlas.api").info("hello", extra={"event": "test"})
        lines = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert [line["event"] for line in lines] == ["test"]
    finally:
        for name in logs.LOGGER_NAMES:
            logger = logging.getLogger(name)
            logger.handlers = [h for h in logger.handlers if h.get_name() != "atlas-api-json"]
            logger.propagate = True
            logger.setLevel(logging.NOTSET)
        logging.getLogger("uvicorn.access").disabled = False


def test_truncated_path() -> None:
    assert logs.truncated_path("/x") == "/x"
    assert logs.truncated_path("/" + "a" * 500).endswith("...")
    assert len(logs.truncated_path("/" + "a" * 500)) == logs.MAX_PATH + 3


# --- Benchmark ----------------------------------------------------------------------------


def test_stats() -> None:
    assert _stats([3.0, 1.0, 2.0]) == {
        "n": 3,
        "min_ms": 1.0,
        "median_ms": 2.0,
        "p95_ms": 3.0,
        "max_ms": 3.0,
    }


@pytest.mark.integration
def test_benchmark_measures_a_real_server() -> None:
    report = run_benchmark(settings(), requests=2, simulations=1)
    assert report["server"]["workers"] == 2
    assert set(report["get_endpoints"]) >= {"/api/v1/forecasts", "/health/live"}
    for row in report["get_endpoints"].values():
        assert row["n"] == 2
        assert row["min_ms"] > 0
    assert {r["feasibility"] for r in report["simulations"].values()} == {
        "strict",
        "relaxed_terminal_target",
    }
    assert report["isolation"]["while_all_workers_busy"]["n"] == 2
    statuses = report["busy_refusals"]["status_counts"]
    assert set(statuses) <= {"200", "503"}
    assert sum(statuses.values()) == 6
