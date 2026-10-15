"""The host scripts and the ``atlas-deploy`` command line.

The scripts are linted by shellcheck and checked for the rules a root script must keep
(strict mode, no tracing of secrets, hash-pinned downloads, locked installs, a dry run by
default). The deployment tooling imports the standard library only.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from atlas.deploy import cli
from tests.conftest import REPO_ROOT
from tests.m6_helpers import DEPLOY_ENV_PRIVATE, need_tools, run, write

SCRIPTS = sorted((REPO_ROOT / "deploy" / "scripts").glob("*.sh"))
BUILD = REPO_ROOT / "deploy/scripts/build-release.sh"
BOOTSTRAP = REPO_ROOT / "deploy/scripts/bootstrap-host.sh"


def test_scripts_pass_shellcheck() -> None:
    need_tools("shellcheck")
    result = run(["shellcheck", "--severity=style", *map(str, SCRIPTS)])
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_keep_the_rules_of_a_root_script(script: Path) -> None:
    text = script.read_text(encoding="utf-8")
    assert run(["bash", "-n", str(script)]).returncode == 0
    assert os.access(script, os.X_OK)
    assert "set -Eeuo pipefail" in text
    assert not re.search(r"^\s*set -[a-zA-Z]*x", text, re.MULTILINE)  # no tracing
    assert "curl" not in text
    assert "wget" not in text
    assert "| sh" not in text
    assert "| bash" not in text
    assert "eval " not in text


def test_the_bootstrap_is_a_dry_run_unless_applied() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "APPLY=0" in text
    assert "--apply) APPLY=1" in text
    assert "--require-hashes" in text  # the uv it installs is hash-pinned
    assert "ufw" not in text.replace("ufw rules", "")  # never touches the firewall
    assert "sshd" not in text
    assert "certbot certonly" not in text
    assert "--before-bootstrap" in text  # the preflight runs first


def test_the_build_installs_the_lock_and_checks_as_the_service_user() -> None:
    text = BUILD.read_text(encoding="utf-8")
    assert "sync --locked --no-dev" in text
    assert "UV_PYTHON_DOWNLOADS=never" in text
    assert "setpriv --reuid=atlas" in text
    assert "--no-new-privs" in text
    for check in ("check", "optimize inspect", "optimize verify", "api openapi"):
        assert check in text
    assert "chmod -R u=rX,g=rX,o=" in text
    assert "chown -R root:atlas" in text
    assert re.search(r"\[\[ \$COMMIT =~ \^\[0-9a-f\]\{40\}\$ \]\]", text)
    assert "trap cleanup EXIT" in text


@pytest.mark.parametrize(
    ("script", "args", "message"),
    [
        (BUILD, ["main"], "full 40-character commit"),
        (BUILD, ["abc", "def"], "one commit only"),
        (BUILD, ["--nope"], "unknown option"),
        (BOOTSTRAP, ["--resources", "huge"], "low or standard"),
        (BOOTSTRAP, ["--nope"], "unknown option"),
    ],
)
def test_scripts_refuse_bad_arguments(script: Path, args: list[str], message: str) -> None:
    result = run(["bash", str(script), *args])
    assert result.returncode in (1, 2)
    assert message in result.stderr


def test_scripts_explain_themselves() -> None:
    for script in SCRIPTS:
        result = run(["bash", str(script), "--help"])
        assert result.returncode == 0
        assert "sudo deploy/scripts/" in result.stdout


@pytest.mark.skipif(os.geteuid() == 0, reason="checks the refusal for unprivileged users")
def test_scripts_refuse_to_run_unprivileged() -> None:
    assert "run as root" in run(["bash", str(BOOTSTRAP)]).stderr
    assert "run as root" in run(["bash", str(BUILD), "a" * 40]).stderr


def test_the_deployment_tooling_imports_the_standard_library_only() -> None:
    code = (
        "import sys\n"
        "import atlas.deploy.cli, atlas.deploy.preflight, atlas.deploy.monitor\n"
        "heavy = {'numpy', 'pandas', 'scipy', 'fastapi', 'starlette', 'uvicorn', 'pydantic'}\n"
        "print(sorted(heavy & {name.split('.')[0] for name in sys.modules}))\n"
    )
    result = run([sys.executable, "-c", code], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_the_console_script_is_declared() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    assert project["scripts"]["atlas-deploy"] == "atlas.deploy.cli:main"


def test_ci_runs_the_deployment_checks() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "ATLAS_REQUIRE_DEPLOY_TOOLS" in workflow
    assert "test_m6_container.py" in workflow


# --- The command line --------------------------------------------------------------------------


def rehearsal(tmp_path: Path, env: str = DEPLOY_ENV_PRIVATE) -> Path:
    root = tmp_path / "root"
    write(root / "etc/atlas/deploy.env", env)
    return root


def test_validate(tmp_path: Path) -> None:
    root = rehearsal(tmp_path)
    out = io.StringIO()
    assert (
        cli.main(["--root", str(root), "validate", "--release-dir", str(REPO_ROOT)], stdout=out)
        == 0
    )
    assert "valid (private profile" in out.getvalue()
    out = io.StringIO()
    code = cli.main(
        ["--root", str(root), "--json", "validate", "--installed", "--release-dir", str(REPO_ROOT)],
        stdout=out,
    )
    payload = json.loads(out.getvalue())
    assert code == 1
    assert not payload["ok"]
    assert any("api-key" in p for p in payload["problems"])
    assert any("fullchain.pem" in p for p in payload["problems"])
    assert payload["config"]["api_key"] == "systemd credential (never shown)"


def test_invalid_configurations_exit_2_with_every_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = rehearsal(tmp_path, "ATLAS_DEPLOY_PROFILE=private\nATLAS_DEPLOY_API_KEY=x\n")
    assert cli.main(["--root", str(root), "validate", "--release-dir", str(REPO_ROOT)]) == 2
    err = capsys.readouterr().err
    assert "secrets never go in deploy.env" in err
    assert "needs ATLAS_DEPLOY_DOMAIN" in err
    assert cli.main(["--root", str(tmp_path / "none"), "validate"]) == 2


def test_render(tmp_path: Path) -> None:
    root = rehearsal(tmp_path)
    out = io.StringIO()
    assert (
        cli.main(["--root", str(root), "render", "--release-dir", str(REPO_ROOT)], stdout=out) == 0
    )
    assert "===== /etc/systemd/system/atlas.service" in out.getvalue()
    target = tmp_path / "out"
    assert (
        cli.main(
            [
                "--root",
                str(root),
                "render",
                "--release-dir",
                str(REPO_ROOT),
                "--output-dir",
                str(target),
            ],
            stdout=io.StringIO(),
        )
        == 0
    )
    assert (target / "etc/nginx/sites-available/atlas.conf").is_file()
    out = io.StringIO()
    assert (
        cli.main(
            ["--root", str(root), "render", "--release-dir", str(REPO_ROOT), "--apply"], stdout=out
        )
        == 0
    )
    assert "/etc/systemd/system/atlas.service" in out.getvalue()
    assert (root / "etc/nginx/sites-enabled/atlas.conf").is_symlink()
    assert (
        cli.main(["--root", str(root), "render", "--release-dir", str(tmp_path)]) == 2
    )  # no templates


def test_verify_needs_a_current_release(tmp_path: Path) -> None:
    root = rehearsal(tmp_path)
    assert cli.main(["--root", str(root), "verify"]) == 2


def test_preflight_through_the_command(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(
        ["--json", "preflight", "--resources", "low", "--offline", "--release-dir", str(REPO_ROOT)]
    )
    assert code in (0, 1)
    assert json.loads(capsys.readouterr().out)["profile"] == "low"


def test_unexpected_errors_are_reported_as_internal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(args: object, out: object) -> int:
        raise RuntimeError("bug")

    monkeypatch.setattr(cli, "dispatch", boom)
    assert cli.main(["status"]) == 3
    assert "internal error" in capsys.readouterr().err


def test_the_installed_command_runs() -> None:
    executable = Path(sys.executable).with_name("atlas-deploy")
    result = subprocess.run(  # noqa: S603
        [str(executable), "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert "activate" in result.stdout
