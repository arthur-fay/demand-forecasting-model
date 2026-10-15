"""Release lifecycle: activation, automatic restoration, rollback, recovery, pruning, plans.

A fake host stands for systemd and nginx, so every decision is tested, including the ones
a real server rarely shows (a rollback that fails, an nginx configuration refused, a lost
SSH session half-way). ``test_activation_with_real_api_processes`` then drives real API
processes from two releases, one of them broken, and verifies them over HTTP.
"""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from atlas.deploy import cli
from atlas.deploy.credentials import SecretStore
from atlas.deploy.layout import Layout
from atlas.deploy.manifest import build_manifest, load_manifest, write_manifest
from atlas.deploy.releases import (
    ACTIVATED,
    IN_PROGRESS,
    REFUSED,
    ROLLBACK_FAILED,
    ROLLED_BACK,
    STOPPED,
    DeployError,
    Deployment,
    compare_manifests,
)
from atlas.deploy.verify import Target, Verification, verify
from tests.m6_helpers import DEPLOY_ENV_PRIVATE, free_port, make_release, write

UID, GID = os.geteuid(), os.getegid()
A, B, C = "a" * 40, "b" * 40, "c" * 40


class FakeHost:
    """systemd and nginx, recorded. ``restart`` fails for the releases in ``fail_restart``."""

    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self.calls: list[str] = []
        self.fail_restart: set[str] = set()
        self.nginx_ok = True

    def current(self) -> str:
        link = self.layout.local(self.layout.current)
        return link.readlink().name if link.is_symlink() else "-"

    def daemon_reload(self) -> tuple[bool, str]:
        self.calls.append("daemon-reload")
        return True, ""

    def enable(self, *units: str) -> tuple[bool, str]:
        self.calls.append("enable")
        return True, ""

    def reset_failed(self, unit: str) -> tuple[bool, str]:
        self.calls.append("reset-failed")
        return True, ""

    def restart(self, unit: str) -> tuple[bool, str]:
        self.calls.append(f"restart {self.current()}")
        ok = self.current() not in self.fail_restart
        return ok, "" if ok else "Job for atlas.service failed"

    def stop(self, unit: str) -> tuple[bool, str]:
        self.calls.append("stop")
        return True, ""

    def main_pid(self, unit: str) -> int | None:
        return 4242

    def show(self, unit: str, properties: Any) -> dict[str, str]:
        return {"ActiveState": "active", "SubState": "running", "NRestarts": "0"}

    def nginx_test(self) -> tuple[bool, str]:
        self.calls.append("nginx -t")
        return self.nginx_ok, "" if self.nginx_ok else "unknown directive"

    def nginx_reload(self) -> tuple[bool, str]:
        self.calls.append("nginx reload")
        return True, ""


def verifier(broken: set[str]) -> Callable[[Target], Verification]:
    def check(target: Target) -> Verification:
        result = Verification()
        name = target.release_dir.name if target.release_dir else "?"
        result.add(
            "readiness", name not in broken, f"{name} {'broken' if name in broken else 'ready'}"
        )
        return result

    return check


def deployment(
    tmp_path: Path, env: str = "", broken: set[str] | None = None
) -> tuple[Deployment, FakeHost]:
    layout = Layout(root=tmp_path)
    write(tmp_path / "etc/atlas/deploy.env", env, 0o644)
    secrets = SecretStore(
        layout, owner=UID, owner_gid=GID, proxy_gid=GID, hasher=lambda p: "$6$s$" + "h" * 86
    )
    secrets.init()
    if "private" in env:
        secrets.add_user("alice")
        for name in ("fullchain.pem", "privkey.pem"):
            write(tmp_path / "etc/letsencrypt/live/atlas.example.org" / name, "x", 0o600)
    host = FakeHost(layout)
    deploy = Deployment(
        layout,
        host,
        verifier(broken or set()),
        owner=UID,
        proxy_gid=GID,
        service_gid=GID,
        chown=False,
    )
    return deploy, host


def name(commit: str) -> str:
    from atlas.deploy.manifest import project_version
    from tests.m6_helpers import REPO_ROOT

    return f"{project_version(REPO_ROOT)}-{commit[:12]}"


# --- Activation ---------------------------------------------------------------------------------


def test_a_first_activation(tmp_path: Path) -> None:
    deploy, host = deployment(tmp_path)
    make_release(tmp_path, commit=A)
    record = deploy.activate(name(A))
    assert record["outcome"] == ACTIVATED, record
    assert deploy.current() == name(A)
    assert host.calls == ["daemon-reload", "enable", "reset-failed", f"restart {name(A)}"]
    assert (tmp_path / "etc/systemd/system/atlas.service").is_file()
    assert [r["outcome"] for r in deploy.history()] == [IN_PROGRESS, ACTIVATED]
    assert not (tmp_path / "opt/atlas/.current.new").exists()


def test_a_private_activation_installs_and_reloads_nginx(tmp_path: Path) -> None:
    deploy, host = deployment(tmp_path, DEPLOY_ENV_PRIVATE)
    make_release(tmp_path, commit=A)
    assert deploy.activate(name(A))["outcome"] == ACTIVATED
    assert "nginx -t" in host.calls
    assert host.calls[-1] == "nginx reload"
    assert (tmp_path / "etc/nginx/sites-enabled/atlas.conf").is_symlink()


def test_a_failed_activation_restores_the_previous_release(tmp_path: Path) -> None:
    deploy, host = deployment(tmp_path, broken={name(B)})
    make_release(tmp_path, commit=A)
    make_release(tmp_path, commit=B, built_at="2026-10-16T12:00:00+00:00")
    deploy.activate(name(A))
    record = deploy.activate(name(B))
    assert record["outcome"] == ROLLED_BACK
    assert record["restored"] == name(A)
    assert deploy.current() == name(A)
    assert any("broken" in line for line in record["detail"])
    assert host.calls[-2:] == [f"restart {name(A)}"] or f"restart {name(A)}" in host.calls[-3:]
    # The broken release is kept for inspection; its files are unchanged.
    assert (tmp_path / "opt/atlas/releases" / name(B) / "release.json").is_file()


def test_a_release_that_does_not_start_is_rolled_back(tmp_path: Path) -> None:
    deploy, host = deployment(tmp_path)
    make_release(tmp_path, commit=A)
    make_release(tmp_path, commit=B)
    deploy.activate(name(A))
    host.fail_restart = {name(B)}
    record = deploy.activate(name(B))
    assert record["outcome"] == ROLLED_BACK
    assert deploy.current() == name(A)
    assert "systemctl restart atlas.service failed" in record["detail"][0]


def test_a_failed_first_deployment_is_stopped(tmp_path: Path) -> None:
    deploy, host = deployment(tmp_path, broken={name(A)})
    make_release(tmp_path, commit=A)
    record = deploy.activate(name(A))
    assert record["outcome"] == STOPPED
    assert host.calls[-1] == "stop"


def test_a_failed_restoration_is_reported_and_the_service_stopped(tmp_path: Path) -> None:
    deploy, host = deployment(tmp_path, broken={name(B)})
    make_release(tmp_path, commit=A)
    make_release(tmp_path, commit=B)
    deploy.activate(name(A))
    host.fail_restart = {name(A), name(B)}
    record = deploy.activate(name(B))
    assert record["outcome"] == ROLLBACK_FAILED
    # A previous release that no longer passes its checks is not restored at all.
    (tmp_path / "opt/atlas/releases" / name(A) / "uv.lock").write_text("tampered")
    host.fail_restart = set()
    record = deploy.activate(name(B))
    assert record["outcome"] == ROLLBACK_FAILED
    assert any("cannot restore" in line for line in record["detail"])
    assert host.calls[-1] == "stop"


@pytest.mark.parametrize(
    ("damage", "fragment"),
    [
        (
            lambda root, rel: (rel / "docs/results/atlas-m4-optimization-v1/plans.csv").write_text(
                "x"
            ),
            "research artifact altered",
        ),
        (
            lambda root, rel: (rel / "src").mkdir() or (rel / "src/evil.py").write_text(""),
            "release file altered",
        ),
        (lambda root, rel: (rel / "uv.lock").chmod(0o666), "writable by group or others"),
        (
            lambda root, rel: (root / "etc/atlas/deploy.env").write_text(
                "ATLAS_DEPLOY_PROFILE=open\n"
            ),
            "deploy.env",
        ),
        (lambda root, rel: (root / "etc/atlas/deploy.env").chmod(0o666), "mode"),
        (lambda root, rel: (root / "etc/atlas/credentials/api-key").chmod(0o644), "api-key"),
        (
            lambda root, rel: (root / "etc/nginx/atlas/simulation-key.conf").unlink(),
            "simulation-key.conf",
        ),
    ],
)
def test_unsafe_releases_and_configurations_are_refused_without_touching_the_service(
    tmp_path: Path, damage: Callable[[Path, Path], object], fragment: str
) -> None:
    deploy, host = deployment(tmp_path, DEPLOY_ENV_PRIVATE)
    release = make_release(tmp_path, commit=A)
    damage(tmp_path, release)
    record = deploy.activate(name(A))
    assert record["outcome"] == REFUSED
    assert any(fragment in line for line in record["detail"]), record["detail"]
    assert host.calls == []
    assert deploy.current() is None


def test_unknown_or_malformed_releases_are_refused(tmp_path: Path) -> None:
    deploy, _ = deployment(tmp_path)
    assert deploy.activate("0.6.0-000000000000")["outcome"] == REFUSED
    assert deploy.activate("../../etc")["detail"] == ["invalid release id '../../etc'"]


def test_an_nginx_configuration_refused_by_nginx_restores_the_files(tmp_path: Path) -> None:
    deploy, host = deployment(tmp_path, DEPLOY_ENV_PRIVATE)
    make_release(tmp_path, commit=A)
    deploy.activate(name(A))
    site = tmp_path / "etc/nginx/sites-available/atlas.conf"
    site.write_text("# the site that works\n")
    host.nginx_ok = False
    make_release(tmp_path, commit=B)
    calls = len(host.calls)
    record = deploy.activate(name(B))
    assert record["outcome"] == REFUSED  # refused before the switch
    assert any("nginx refused" in line for line in record["detail"])
    assert site.read_text() == "# the site that works\n"
    assert deploy.current() == name(A)
    assert not any(call.startswith("restart") for call in host.calls[calls:])


def test_one_deployment_at_a_time(tmp_path: Path) -> None:
    deploy, _ = deployment(tmp_path)
    make_release(tmp_path, commit=A)
    with deploy.locked(), pytest.raises(DeployError, match="another deployment"):
        deploy.activate(name(A))


# --- Rollback, recovery, pruning ------------------------------------------------------------------


def test_rollback_returns_to_the_previous_release(tmp_path: Path) -> None:
    deploy, _ = deployment(tmp_path)
    for commit, day in ((A, 15), (B, 16)):
        make_release(tmp_path, commit=commit, built_at=f"2026-10-{day}T12:00:00+00:00")
        deploy.activate(name(commit))
    record = deploy.rollback()
    assert record["outcome"] == ACTIVATED
    assert record["action"] == "rollback"
    assert deploy.current() == name(A)
    assert deploy.rollback(to=name(B))["outcome"] == ACTIVATED
    assert deploy.current() == name(B)


def test_rollback_without_a_previous_release(tmp_path: Path) -> None:
    deploy, _ = deployment(tmp_path)
    make_release(tmp_path, commit=A)
    deploy.activate(name(A))
    with pytest.raises(DeployError, match="no previous release"):
        deploy.rollback()


def test_an_interrupted_activation_is_recovered(tmp_path: Path) -> None:
    deploy, _host = deployment(tmp_path, broken={name(B)})
    make_release(tmp_path, commit=A)
    make_release(tmp_path, commit=B)
    deploy.activate(name(A))
    # A lost session: the record says in progress, the link already points to B.
    deploy.record("activate", IN_PROGRESS, release=name(B), previous=name(A))
    deploy.switch(name(B))
    assert deploy.pending() is not None
    record = deploy.recover()
    assert record["outcome"] == ROLLED_BACK
    assert deploy.current() == name(A)
    assert deploy.pending() is None
    assert deploy.recover() == {"outcome": "nothing_to_recover"}


def test_an_interrupted_activation_that_verifies_is_kept(tmp_path: Path) -> None:
    deploy, _ = deployment(tmp_path)
    make_release(tmp_path, commit=A)
    deploy.record("activate", IN_PROGRESS, release=name(A), previous=None)
    deploy.switch(name(A))
    assert deploy.recover()["outcome"] == ACTIVATED


def test_pruning_keeps_the_current_and_rollback_releases(tmp_path: Path) -> None:
    deploy, _ = deployment(tmp_path)
    for index, commit in enumerate((A, B, C, "d" * 40, "e" * 40)):
        make_release(tmp_path, commit=commit, built_at=f"2026-10-1{index}T12:00:00+00:00")
    deploy.activate(name(A))
    deploy.activate(name(B))
    (tmp_path / "opt/atlas/releases/.build-x1y2").mkdir()  # an interrupted build
    removed = deploy.prune(keep=2)
    kept = sorted(p.name for p in (tmp_path / "opt/atlas/releases").iterdir())
    assert sorted(kept) == sorted([name(A), name(B), name("d" * 40), name("e" * 40)])
    assert sorted(removed) == sorted([name(C), ".build-x1y2"])
    assert deploy.current() == name(B)


def test_status_and_upgrade_plans(tmp_path: Path) -> None:
    deploy, _ = deployment(tmp_path)
    make_release(tmp_path, commit=A)
    release_b = make_release(tmp_path, commit=B)
    deploy.activate(name(A))
    status = deploy.status()
    assert status["current"] == name(A)
    assert status["manifest"]["git_commit"] == A
    assert status["service"]["ActiveState"] == "active"
    plan = deploy.plan(name(B))
    assert plan["blocking_problems"] == []
    assert plan["changes"]["git_commit"] == [A, B]
    assert plan["changes"]["research_artifacts_changed"] == []
    assert plan["host_files_changing"] == []
    assert any("activate" in command for command in plan["commands"])
    (release_b / "release.json").unlink()
    assert deploy.plan(name(B))["blocking_problems"]


def test_compare_manifests_lists_what_an_operator_must_review(tmp_path: Path) -> None:
    old = load_manifest(make_release(tmp_path, commit=A))
    new = json.loads(json.dumps(old))
    new["dependencies"]["runtime_packages"]["numpy"] = "9.9.9"
    new["research_artifacts"]["files"]["docs/results/x.json"] = "0" * 64
    changes = compare_manifests(old, new)
    assert changes["packages_changed"]["numpy"][1] == "9.9.9"
    assert changes["research_artifacts_changed"] == ["docs/results/x.json"]
    assert changes["api_contract_changed"] is False


def test_the_cli_drives_a_rehearsal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _deploy, host = deployment(tmp_path)
    make_release(tmp_path, commit=A)
    monkeypatch.setattr(cli, "SystemHost", lambda: host)
    monkeypatch.setattr(cli, "verify", verifier(set()))
    out = io.StringIO()
    assert cli.main(["--root", str(tmp_path), "activate", name(A)], stdout=out) == 0
    assert "activated" in out.getvalue()
    out = io.StringIO()
    assert cli.main(["--root", str(tmp_path), "status"], stdout=out) == 0
    assert json.loads(out.getvalue())["current"] == name(A)
    assert cli.main(["--root", str(tmp_path), "plan-upgrade", name(A)], stdout=io.StringIO()) == 0
    assert cli.main(["--root", str(tmp_path), "recover"], stdout=io.StringIO()) == 0
    assert cli.main(["--root", str(tmp_path), "prune"], stdout=io.StringIO()) == 0
    assert cli.main(["--root", str(tmp_path), "rollback"]) == 2  # nothing to roll back to
    assert cli.main(["activate", name(A)]) == 2 or os.geteuid() == 0  # not root on /


# --- Real API processes -------------------------------------------------------------------------


class ProcessHost(FakeHost):
    """Runs the real API (``atlas api serve``) from the current release, as systemd would."""

    def __init__(self, layout: Layout, port: int) -> None:
        super().__init__(layout)
        self.port = port
        self.process: subprocess.Popen[bytes] | None = None

    def restart(self, unit: str) -> tuple[bool, str]:
        self.stop(unit)
        self.calls.append(f"restart {self.current()}")
        current = self.layout.local(self.layout.current)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "ATLAS_REPO_ROOT": str(current),
            "ATLAS_API_KEY_FILE": str(self.layout.local(self.layout.api_key)),
            "ATLAS_API_MAX_CONCURRENT_SIMULATIONS": "1",
            "ATLAS_API_DOCS": "false",
        }
        self.process = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "atlas", "api", "serve", "--port", str(self.port)],
            cwd=current,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, ""

    def stop(self, unit: str) -> tuple[bool, str]:
        if self.process is not None and self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            self.process.wait(timeout=60)
        self.process = None
        return True, ""

    def main_pid(self, unit: str) -> int | None:
        return None if self.process is None else self.process.pid


def test_activation_with_real_api_processes(tmp_path: Path) -> None:
    """Release A serves; release B (a damaged M4 run, its manifest rewritten to match) starts
    but is not ready: verification fails over HTTP and A is restored and verified again."""
    layout = Layout(root=tmp_path)
    port = free_port()
    write(
        tmp_path / "etc/atlas/deploy.env",
        f"ATLAS_DEPLOY_RESOURCES=low\nATLAS_DEPLOY_API_PORT={port}\n",
        0o644,
    )
    SecretStore(layout, owner=UID, owner_gid=GID, proxy_gid=GID).init()
    make_release(tmp_path, commit=A)
    broken = make_release(tmp_path, commit=B)
    metrics = broken / "docs/results/atlas-m4-optimization-v1/metrics.json"
    metrics.chmod(0o644)
    metrics.write_text(metrics.read_text().replace('"HYPOTHETICAL"', '"VERIFIED"', 1))
    manifest = load_manifest(broken)
    rebuilt = build_manifest(
        broken,
        release_id=broken.name,
        commit=B,
        built_at=manifest["build"]["built_at"],
        uv_version="0.11.32",
        checks=[],
    )
    write_manifest(broken, rebuilt)

    host = ProcessHost(layout, port)
    deploy = Deployment(
        layout,
        host,
        lambda target: verify(target, timeout=60),
        owner=UID,
        proxy_gid=GID,
        service_gid=GID,
        chown=False,
    )
    try:
        first = deploy.activate(name(A))
        assert first["outcome"] == ACTIVATED, first
        second = deploy.activate(name(B))
        assert second["outcome"] == ROLLED_BACK, second
        assert any("readiness" in line and "m4_reference_run" in line for line in second["detail"])
        assert deploy.current() == name(A)
        assert host.process is not None
        assert host.process.poll() is None
        again = verify(deploy.target(name(A), deploy.config()), timeout=30)
        assert again.ok, again.render()
        names = {check.name for check in again.checks}
        assert {
            "scientific integrity",
            "smoke simulation",
            "simulation without key",
            "loopback only",
            "process release",
        } <= names
    finally:
        host.stop("atlas.service")


# --- The real host commands (fake binaries on PATH) -------------------------------------------


def test_the_system_host_runs_the_exact_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas.deploy.releases import SystemHost

    bin_dir = tmp_path / "bin"
    log = tmp_path / "calls.log"
    for tool, output in (("systemctl", "MainPID=4242\nActiveState=active"), ("nginx", "")):
        write(
            bin_dir / tool, f'#!/bin/sh\necho "{tool} $*" >> {log}\nprintf "{output}\\n"\n', 0o755
        )
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    host = SystemHost()
    assert host.daemon_reload() == (True, "MainPID=4242\nActiveState=active")
    assert host.enable("atlas.service", "atlas-monitor.timer")[0]
    assert host.reset_failed("atlas.service")[0]
    assert host.restart("atlas.service")[0]
    assert host.stop("atlas.service")[0]
    assert host.show("atlas.service", ["MainPID", "ActiveState"]) == {
        "MainPID": "4242",
        "ActiveState": "active",
    }
    assert host.main_pid("atlas.service") == 4242
    assert host.nginx_test()[0]
    assert host.nginx_reload()[0]
    assert log.read_text().splitlines() == [
        "systemctl daemon-reload",
        "systemctl enable atlas.service atlas-monitor.timer",
        "systemctl reset-failed atlas.service",
        "systemctl restart atlas.service",
        "systemctl stop atlas.service",
        "systemctl show atlas.service -p MainPID,ActiveState",
        "systemctl show atlas.service -p MainPID",
        "nginx -t -q",
        "systemctl reload nginx",
    ]
    write(bin_dir / "systemctl", "#!/bin/sh\necho failed >&2\nexit 1\n", 0o755)
    assert host.restart("atlas.service") == (False, "failed")
    assert host.show("atlas.service", ["MainPID"]) == {}
    assert host.main_pid("atlas.service") is None
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert host.restart("atlas.service") == (False, "systemctl not found")


def test_a_repeated_activation_is_not_blocked_by_the_start_limit(tmp_path: Path) -> None:
    """Six activations in a row: each clears the failure state before its restart."""
    deploy, host = deployment(tmp_path)
    make_release(tmp_path, commit=A)
    for _ in range(6):
        assert deploy.activate(name(A))["outcome"] == ACTIVATED
    restarts = [i for i, call in enumerate(host.calls) if call.startswith("restart")]
    assert len(restarts) == 6
    assert all(host.calls[i - 1] == "reset-failed" for i in restarts)


def test_an_unexpected_error_after_the_switch_restores_the_previous_release(
    tmp_path: Path,
) -> None:
    deploy, _ = deployment(tmp_path)
    make_release(tmp_path, commit=A)
    make_release(tmp_path, commit=B)
    deploy.activate(name(A))

    def exploding(target: Target) -> Verification:
        if target.release_dir is not None and target.release_dir.name == name(B):
            raise OSError("the verifier crashed")
        return verifier(set())(target)

    deploy.verifier = exploding
    record = deploy.activate(name(B))
    assert record["outcome"] == ROLLED_BACK
    assert "unexpected error after the switch: OSError" in record["detail"][0]
    assert deploy.current() == name(A)
    assert deploy.history()[-1]["outcome"] == ROLLED_BACK  # never left in progress


def test_failures_before_the_switch_change_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import atlas.deploy.releases as releases

    deploy, host = deployment(tmp_path)
    make_release(tmp_path, commit=A)
    make_release(tmp_path, commit=B)
    deploy.activate(name(A))
    unit = tmp_path / "etc/systemd/system/atlas.service"
    before = unit.read_text()
    calls = len(host.calls)

    real_write = releases.write_files
    failures = [1]

    def full_disk(files: Any, layout: Any, **kwargs: Any) -> Any:
        if failures:  # the first write fails half-way; the restoration succeeds
            failures.pop()
            unit.write_text("half-written")
            raise OSError(28, "No space left on device")
        return real_write(files, layout, **kwargs)

    monkeypatch.setattr(releases, "write_files", full_disk)
    record = deploy.activate(name(B))
    monkeypatch.setattr(releases, "write_files", real_write)
    assert record["outcome"] == REFUSED
    assert "could not be written" in record["detail"][0]
    assert unit.read_text() == before  # put back
    assert deploy.current() == name(A)
    assert not any(call.startswith("restart") for call in host.calls[calls:])

    monkeypatch.setattr(deploy, "rendered", lambda *a, **k: (_ for _ in ()).throw(KeyError("x")))
    record = deploy.activate(name(B))
    assert record["outcome"] == REFUSED
    assert "installing the files failed: KeyError" in record["detail"][0]
    assert deploy.current() == name(A)
