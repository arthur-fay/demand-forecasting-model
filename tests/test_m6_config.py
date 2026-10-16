"""Deployment configuration (``deploy.env``) and secret delivery (``ATLAS_API_KEY_FILE``).

Unsafe configurations are refused with every problem listed; the rendered runtime
environment is accepted by the API's own settings parser; the API key is read from a
private file and never appears in the environment, ``repr`` or messages.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from atlas.api import settings as api_settings
from atlas.api.server import check_bind
from atlas.api.settings import (
    ENV_API_KEY,
    ENV_API_KEY_FILE,
    load_api_settings,
    read_api_key_file,
)
from atlas.deploy.siteconfig import (
    RESOURCE_PROFILES,
    DeployConfigError,
    load_config,
    parse_env_text,
    runtime_environment,
)
from atlas.errors import ConfigError
from tests.conftest import REPO_ROOT
from tests.m6_helpers import DEPLOY_ENV_PRIVATE, write

KEY = "0123456789abcdef" * 4
ENV_ROOT = {"ATLAS_REPO_ROOT": str(REPO_ROOT)}


def problems_of(text: str) -> list[str]:
    with pytest.raises(DeployConfigError) as caught:
        load_config(text)
    return caught.value.problems


# --- deploy.env -------------------------------------------------------------------------------


def test_the_default_is_the_private_tunnel_profile() -> None:
    config = load_config("")
    assert config.profile == "tunnel"
    assert not config.exposed
    assert config.resources.workers == 2
    assert config.api_port == 8000
    assert config.api_docs is False
    assert config.allowed_hosts == ("127.0.0.1", "localhost", "::1")
    # nginx stays off IPv6 unless asked: limits count per address (M8-25).
    assert config.ipv6 is False
    assert load_config("ATLAS_DEPLOY_IPV6=true\n").ipv6 is True


def test_the_example_file_is_valid() -> None:
    config = load_config((REPO_ROOT / "deploy" / "deploy.env.example").read_text("utf-8"))
    assert config.profile == "tunnel"
    assert config.ipv6 is False


def test_a_private_deployment() -> None:
    config = load_config(
        DEPLOY_ENV_PRIVATE + "ATLAS_DEPLOY_ALLOW_CIDRS=203.0.113.0/24,2001:db8::/48\n"
    )
    assert config.exposed
    assert config.simulations_routed
    assert config.domain == "atlas.example.org"
    assert str(config.tls_cert) == "/etc/letsencrypt/live/atlas.example.org/fullchain.pem"
    assert str(config.tls_key) == "/etc/letsencrypt/live/atlas.example.org/privkey.pem"
    assert config.allow_cidrs == ("203.0.113.0/24", "2001:db8::/48")
    assert config.allowed_hosts[0] == "atlas.example.org"
    assert config.hsts_max_age == 31_536_000


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("ATLAS_DEPLOY_PROFILE=public\n", "PROFILE must be one of"),
        ("ATLAS_DEPLOY_PROFILE=private\n", "needs ATLAS_DEPLOY_DOMAIN"),
        (
            "ATLAS_DEPLOY_PROFILE=public-demo\nATLAS_DEPLOY_DOMAIN=a.example.org\n",
            "security review",
        ),
        ("ATLAS_DEPLOY_DOMAIN=203.0.113.7\n", "fully qualified DNS name"),
        ("ATLAS_DEPLOY_DOMAIN=localhost\n", "fully qualified DNS name"),
        ("ATLAS_DEPLOY_DOMAIN=intranet\n", "fully qualified DNS name"),
        ("ATLAS_DEPLOY_DOMAIN=*.example.org\n", "characters outside"),
        ("ATLAS_DEPLOY_API_PORT=80\n", "API_PORT must be within"),
        ("ATLAS_DEPLOY_API_PORT=eighty\n", "whole number"),
        ("ATLAS_DEPLOY_HSTS_MAX_AGE=60\n", "HSTS_MAX_AGE must be within"),
        ("ATLAS_DEPLOY_SIMULATION_TIMEOUT_SECONDS=600\n", "SIMULATION_TIMEOUT_SECONDS"),
        ("ATLAS_DEPLOY_SIMULATION_TIMEOUT_SECONDS=soon\n", "SIMULATION_TIMEOUT_SECONDS"),
        ("ATLAS_DEPLOY_LOG_LEVEL=TRACE\n", "LOG_LEVEL"),
        ("ATLAS_DEPLOY_RESOURCES=huge\n", "RESOURCES must be one of"),
        ("ATLAS_DEPLOY_IPV6=maybe\n", "IPV6"),
        ("ATLAS_DEPLOY_API_DOCS=maybe\n", "API_DOCS"),
        (DEPLOY_ENV_PRIVATE + "ATLAS_DEPLOY_API_DOCS=true\n", "tunnel profile only"),
        (DEPLOY_ENV_PRIVATE + "ATLAS_DEPLOY_ALLOW_CIDRS=0.0.0.0/0\n", "allows every address"),
        (DEPLOY_ENV_PRIVATE + "ATLAS_DEPLOY_ALLOW_CIDRS=10.0.0.1/8\n", "is not a network"),
        ("ATLAS_DEPLOY_ALLOW_CIDRS=10.0.0.0/8\n", "tunnel profile does not use"),
        ("ATLAS_DEPLOY_TLS_CERT=/etc/x.pem\n", "TLS files apply"),
        (DEPLOY_ENV_PRIVATE + "ATLAS_DEPLOY_TLS_CERT=relative.pem\n", "absolute path"),
        (DEPLOY_ENV_PRIVATE + "ATLAS_DEPLOY_TLS_KEY=/etc/../root/key.pem\n", "absolute path"),
        ("ATLAS_DEPLOY_SECURITY_REVIEW=x\n", "SECURITY_REVIEW must be a reference"),
        ("ATLAS_DEPLOY_WORKERS=4\n", "not a deployment setting"),
        ("ATLAS_API_KEY=" + KEY + "\n", "secrets never go in deploy.env"),
        ("ATLAS_DEPLOY_ADMIN_PASSWORD=hunter2hunter2\n", "secrets never go in deploy.env"),
    ],
)
def test_unsafe_or_invalid_configurations_are_refused(text: str, fragment: str) -> None:
    problems = problems_of(text)
    assert any(fragment in problem for problem in problems), problems


@pytest.mark.parametrize(
    "line",
    [
        'ATLAS_DEPLOY_PROFILE="private"',
        "ATLAS_DEPLOY_DOMAIN=$(hostname)",
        "ATLAS_DEPLOY_DOMAIN=a.example.org; rm -rf /",
        "export ATLAS_DEPLOY_PROFILE=private",
        "ATLAS_DEPLOY_PROFILE private",
        "ATLAS_DEPLOY_DOMAIN=a.example.org # comment",
    ],
)
def test_shell_syntax_and_quotes_are_refused(line: str) -> None:
    with pytest.raises(DeployConfigError):
        parse_env_text(line + "\n")


def test_repeated_keys_are_refused_and_every_problem_is_listed() -> None:
    with pytest.raises(DeployConfigError) as caught:
        parse_env_text("ATLAS_DEPLOY_PROFILE=private\nATLAS_DEPLOY_PROFILE=tunnel\nbad line\n")
    assert len(caught.value.problems) == 2
    problems = problems_of("ATLAS_DEPLOY_PROFILE=private\nATLAS_DEPLOY_API_PORT=1\n")
    assert len(problems) == 2  # the missing domain and the port, together


def test_a_public_demo_needs_a_completed_review() -> None:
    config = load_config(
        "ATLAS_DEPLOY_PROFILE=public-demo\nATLAS_DEPLOY_DOMAIN=demo.example.org\n"
        "ATLAS_DEPLOY_SECURITY_REVIEW=https://example.org/reviews/atlas-2026-11\n"
    )
    assert config.exposed
    assert not config.simulations_routed


@pytest.mark.parametrize("profile", ["tunnel", "private"])
@pytest.mark.parametrize("resources", sorted(RESOURCE_PROFILES))
def test_the_rendered_environment_is_accepted_by_the_api(
    profile: str, resources: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_settings, "available_cpus", lambda: 2)
    text = f"ATLAS_DEPLOY_PROFILE={profile}\nATLAS_DEPLOY_RESOURCES={resources}\n"
    if profile == "private":
        text += "ATLAS_DEPLOY_DOMAIN=atlas.example.org\n"
    env = runtime_environment(load_config(text))
    assert not any("KEY" in name for name in env)  # no secret in the environment
    settings = load_api_settings(env={**ENV_ROOT, **env})
    assert settings.max_concurrent_simulations == RESOURCE_PROFILES[resources].workers
    assert settings.docs_enabled is False
    assert settings.cors_origins == ()
    if profile == "private":
        assert "atlas.example.org" in settings.allowed_hosts


def test_the_standard_profile_is_refused_on_one_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_settings, "available_cpus", lambda: 1)
    env = runtime_environment(load_config("ATLAS_DEPLOY_RESOURCES=standard\n"))
    with pytest.raises(ConfigError, match="exceeds the 1 CPUs"):
        load_api_settings(env={**ENV_ROOT, **env})
    low = runtime_environment(load_config("ATLAS_DEPLOY_RESOURCES=low\n"))
    assert load_api_settings(env={**ENV_ROOT, **low}).max_concurrent_simulations == 1


# --- ATLAS_API_KEY_FILE -------------------------------------------------------------------------


def key_file(tmp_path: Path, content: str = KEY + "\n", mode: int = 0o600) -> Path:
    return write(tmp_path / "api-key", content, mode)


def test_the_key_is_read_from_a_private_file(tmp_path: Path) -> None:
    path = key_file(tmp_path)
    resolved = load_api_settings(env={**ENV_ROOT, ENV_API_KEY_FILE: str(path)})
    assert resolved.api_key == KEY
    assert resolved.simulation_requires_key
    assert KEY not in repr(resolved)
    check_bind("0.0.0.0", 8000, resolved)  # noqa: S104
    assert read_api_key_file(key_file(tmp_path, KEY + "\r\n", 0o400)) == KEY


@pytest.mark.parametrize(
    ("content", "mode", "fragment"),
    [
        (KEY, 0o644, "readable by other users"),
        (KEY, 0o620, "writable by its group"),
        ("", 0o600, "is empty"),
        ("\n", 0o600, "is empty"),
        ("short\n", 0o600, "at least 24"),
        ("with space " + KEY + "\n", 0o600, "at least 24"),
        ("é" * 30, 0o600, "ASCII"),
        ("k" * 5000, 0o600, "too large"),
    ],
)
def test_unsafe_key_files_are_refused(
    tmp_path: Path, content: str, mode: int, fragment: str
) -> None:
    path = key_file(tmp_path, content, mode)
    with pytest.raises(ConfigError, match=fragment):
        load_api_settings(env={**ENV_ROOT, ENV_API_KEY_FILE: str(path)})


def test_group_read_is_accepted_for_the_root_group_only(tmp_path: Path) -> None:
    """A systemd credential shows as 0440 group root (its ACL mask); a key file another
    group could read is refused."""
    path = key_file(tmp_path, mode=0o440)
    if os.geteuid() == 0:
        os.chown(path, -1, 0)
        assert read_api_key_file(path) == KEY
        os.chown(path, -1, 1)
    elif os.getegid() == 0:  # pragma: no cover - a non-root user in group root
        pytest.skip("the test user's group is root")
    with pytest.raises(ConfigError, match="by its group"):
        read_api_key_file(path)


def test_key_file_errors_never_echo_the_content(tmp_path: Path) -> None:
    secret = "s3cr3t s3cr3t s3cr3t s3cr3t"  # invalid: contains spaces  # noqa: S105
    path = key_file(tmp_path, secret)
    with pytest.raises(ConfigError) as caught:
        load_api_settings(env={**ENV_ROOT, ENV_API_KEY_FILE: str(path)})
    assert "s3cr3t" not in str(caught.value)


def test_missing_key_files_and_directories_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot be read"):
        load_api_settings(env={**ENV_ROOT, ENV_API_KEY_FILE: str(tmp_path / "missing")})
    with pytest.raises(ConfigError, match="regular file"):
        load_api_settings(env={**ENV_ROOT, ENV_API_KEY_FILE: str(tmp_path)})


def test_a_key_and_a_key_file_together_are_refused(tmp_path: Path) -> None:
    env = {**ENV_ROOT, ENV_API_KEY: KEY, ENV_API_KEY_FILE: str(key_file(tmp_path))}
    with pytest.raises(ConfigError, match="not both"):
        load_api_settings(env=env)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every file")
def test_an_unreadable_key_file_is_refused(tmp_path: Path) -> None:
    path = key_file(tmp_path, mode=0o000)
    with pytest.raises(ConfigError, match="cannot be read"):
        load_api_settings(env={**ENV_ROOT, ENV_API_KEY_FILE: str(path)})
