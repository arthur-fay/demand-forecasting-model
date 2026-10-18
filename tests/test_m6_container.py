"""M6 end to end on a disposable Ubuntu 24.04 server: a Docker container running systemd.

Opt-in (``ATLAS_E2E_CONTAINER=1``; CI runs it in its own job). Nothing here touches a real
server: the container is created from ``deploy/test/Dockerfile`` and removed afterwards.
In order, the tests run the real deployment package exactly as an operator would:

the bootstrap (dry run, apply, idempotent re-run), a release build from an exact commit
(read-only tree, checks as the service user, idempotent, interrupted builds cleaned up),
the first activation (tunnel), the private profile through nginx and TLS, crash recovery,
the watchdog, a killed worker, a configuration error that must not loop, a graceful stop
during a simulation, a broken release rolled back automatically, an explicit rollback from
a second verified release to the first, an interrupted activation recovered, the monitor
and its alert hook, a reboot, resource use, a return to the tunnel profile (the nginx site
taken out), and the absence of secrets in logs, environments and unit properties.

Environment: ``ATLAS_E2E_BASE_IMAGE`` (default ``ubuntu:24.04``), ``ATLAS_E2E_NETWORK``
(``host`` behind an HTTP proxy; the proxy variables and ``ATLAS_E2E_CA_BUNDLE`` are then
passed in), ``ATLAS_E2E_KEEP=1`` keeps the container for inspection.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

pytestmark = [
    pytest.mark.container,
    pytest.mark.skipif(
        os.environ.get("ATLAS_E2E_CONTAINER") != "1", reason="opt-in: ATLAS_E2E_CONTAINER=1"
    ),
]

NAME = "atlas-m6-e2e"
IMAGE = "atlas-m6-e2e-vps"
DOMAIN = "atlas.example.org"
PASSWORD = "e2e-correct-horse-battery-staple"  # noqa: S105
RELEASES = "/opt/atlas/releases"
DEPLOY = "/opt/atlas/current/.venv/bin/atlas-deploy"
#: The simulation workers (as user atlas: a root shell running this command never matches).
WORKERS = "pgrep -u atlas -f multiprocessing.spawn"


def docker(
    *args: str, check: bool = True, timeout: float = 900
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"docker {' '.join(args[:3])}: {result.stdout}\n{result.stderr}")
    return result


def git(*args: str, cwd: Path = REPO_ROOT, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **(env or {})},
    )
    return result.stdout.strip()


@dataclass
class Server:
    """The container: commands run as root through ``docker exec``."""

    commit: str
    broken: str
    work: Path
    release: str = ""
    broken_release: str = ""
    notes: dict[str, object] = field(default_factory=dict)
    #: A second good commit (the same tree as ``commit``) and its release.
    second: str = ""
    second_release: str = ""

    def sh(
        self, script: str, *, check: bool = True, timeout: float = 900
    ) -> subprocess.CompletedProcess[str]:
        result = docker(
            "exec", NAME, "bash", "-c", ". /root/env.sh; " + script, check=False, timeout=timeout
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"{script}\n--- stdout\n{result.stdout}\n--- stderr\n{result.stderr}"
            )
        return result

    def out(self, script: str) -> str:
        return self.sh(script).stdout.strip()

    def wait(self, script: str, timeout: float = 120) -> str:
        deadline = time.monotonic() + timeout
        while True:
            result = self.sh(script, check=False)
            if result.returncode == 0:
                return result.stdout.strip()
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"timed out waiting for: {script}\n{result.stdout}{result.stderr}"
                )
            time.sleep(1)

    def show(self, *properties: str) -> dict[str, str]:
        text = self.out(f"systemctl show atlas.service -p {','.join(properties)}")
        return dict(line.split("=", 1) for line in text.splitlines())

    def boot(self) -> None:
        self.wait("systemctl is-system-running --wait | grep -E 'running|degraded'", timeout=180)


def _snapshot_commit(work: Path) -> tuple[str, str, str]:
    """The commit to deploy (HEAD, or the working tree as an unreferenced commit), a second
    good commit on top of it (empty: the same tree, another release id) and a broken
    variant (the API reads a missing M4 run, so it starts but is not ready)."""
    index = work / "index"
    shutil.copy(REPO_ROOT / ".git" / "index", index)
    env = {
        "GIT_INDEX_FILE": str(index),
        "GIT_AUTHOR_NAME": "e2e",
        "GIT_AUTHOR_EMAIL": "e2e@example.invalid",
        "GIT_COMMITTER_NAME": "e2e",
        "GIT_COMMITTER_EMAIL": "e2e@example.invalid",
    }
    git("add", "-A", env=env)
    tree = git("write-tree", env=env)
    head = git("rev-parse", "HEAD")
    commit = (
        head
        if tree == git("rev-parse", "HEAD^{tree}")
        else git("commit-tree", tree, "-p", "HEAD", "-m", "e2e snapshot", env=env)
    )
    bare = work / "atlas.git"
    git("init", "-q", "--bare", str(bare))
    git("config", "receive.shallowUpdate", "true", cwd=bare)  # a shallow CI checkout
    git("push", "-q", str(bare), f"{commit}:refs/heads/e2e")
    clone = work / "clone"
    git("clone", "-q", "--branch", "e2e", str(bare), str(clone))
    git(
        "-c",
        "user.name=e2e",
        "-c",
        "user.email=e2e@example.invalid",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "second release",
        cwd=clone,
    )
    second = git("rev-parse", "HEAD", cwd=clone)
    git("push", "-q", "origin", "HEAD:refs/heads/second", cwd=clone)
    settings = clone / "src/atlas/api/settings.py"
    settings.write_text(
        settings.read_text().replace(
            'REFERENCE_RUN_DIR: Final = "docs/results/atlas-m4-optimization-v1"',
            'REFERENCE_RUN_DIR: Final = "docs/results/atlas-m4-optimization-missing"',
        )
    )
    git(
        "-c",
        "user.name=e2e",
        "-c",
        "user.email=e2e@example.invalid",
        "commit",
        "-q",
        "-am",
        "broken release",
        cwd=clone,
    )
    broken = git("rev-parse", "HEAD", cwd=clone)
    git("push", "-q", "origin", "HEAD:refs/heads/broken", cwd=clone)
    git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=bare)
    return commit, second, broken


@pytest.fixture(scope="module")
def server() -> Iterator[Server]:
    if shutil.which("docker") is None:
        pytest.fail("docker is required for the container test")
    work = Path(tempfile.mkdtemp(prefix="atlas-e2e-"))
    base = os.environ.get("ATLAS_E2E_BASE_IMAGE", "ubuntu:24.04")
    network = os.environ.get("ATLAS_E2E_NETWORK", "")
    build_net = ["--network", network] if network else []
    docker(
        "build",
        "-q",
        *build_net,
        "--build-arg",
        f"BASE={base}",
        "-t",
        IMAGE,
        str(REPO_ROOT / "deploy/test"),
        timeout=1800,
    )
    commit, second, broken = _snapshot_commit(work)
    docker("rm", "-f", NAME, check=False)
    run_net = ["--network", network] if network else []
    # systemd as PID 1: its own cgroup namespace on cgroup v2 (GitHub runners); on a
    # cgroup v1 or hybrid host, the host namespace with the hierarchy mounted writable.
    if Path("/sys/fs/cgroup/cgroup.controllers").exists():
        cgroups = ["--cgroupns=private"]
    else:
        cgroups = ["--cgroupns=host", "-v", "/sys/fs/cgroup:/sys/fs/cgroup:rw"]
    docker(
        "run",
        "-d",
        "--name",
        NAME,
        "--hostname",
        "atlas-vps",
        "--privileged",
        *cgroups,
        *run_net,
        IMAGE,
    )
    # Copied in and given to root: the build's git refuses a repository another user owns
    # ("dubious ownership"), and a mount or a copy keeps the CI runner's uid.
    docker("cp", str(work / "atlas.git"), f"{NAME}:/srv/atlas.git")
    docker("exec", NAME, "chown", "-R", "root:root", "/srv/atlas.git")
    env_lines = ["export NO_PROXY=127.0.0.1,localhost,::1 no_proxy=127.0.0.1,localhost,::1"]
    if network == "host":
        for name in ("HTTPS_PROXY", "https_proxy"):
            if os.environ.get(name):
                env_lines.append(f"export {name}={os.environ[name]}")
        bundle = os.environ.get("ATLAS_E2E_CA_BUNDLE")
        if bundle:
            docker("cp", bundle, f"{NAME}:/usr/local/share/ca-certificates/e2e-proxy.crt")
            env_lines.append(
                "export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt "
                "PIP_CERT=/etc/ssl/certs/ca-certificates.crt"
            )
    (work / "env.sh").write_text("\n".join(env_lines) + "\n")
    docker("cp", str(work / "env.sh"), f"{NAME}:/root/env.sh")
    host = Server(commit, broken, work, second=second)
    host.boot()
    host.sh("update-ca-certificates >/dev/null 2>&1 || true")
    archive = work / "checkout.tar"
    with archive.open("wb") as handle:
        subprocess.run(  # noqa: S603
            ["git", "--git-dir", str(work / "atlas.git"), "archive", commit],  # noqa: S607
            stdout=handle,
            check=True,
        )
    docker("cp", str(archive), f"{NAME}:/root/checkout.tar")
    host.sh("mkdir -p /root/atlas && tar -xf /root/checkout.tar -C /root/atlas")
    try:
        yield host
    finally:
        print(json.dumps(host.notes, indent=2, default=str))
        if os.environ.get("ATLAS_E2E_KEEP") != "1":
            docker("rm", "-f", NAME, check=False)
            shutil.rmtree(work, ignore_errors=True)


# --- Host preparation and the release build -----------------------------------------------------


def test_the_bootstrap_dry_run_changes_nothing(server: Server) -> None:
    result = server.sh("cd /root/atlas && deploy/scripts/bootstrap-host.sh --resources standard")
    assert "Dry run: nothing was changed" in result.stdout
    assert "would run: groupadd --system atlas" in result.stdout
    assert server.sh("id atlas", check=False).returncode != 0
    assert server.sh("test -e /opt/atlas", check=False).returncode != 0


def test_the_bootstrap_applies_and_is_idempotent(server: Server) -> None:
    server.sh(
        "cd /root/atlas && deploy/scripts/bootstrap-host.sh --resources standard --apply",
        timeout=1200,
    )
    again = server.sh(
        "cd /root/atlas && deploy/scripts/bootstrap-host.sh --resources standard --apply"
    )
    for line in (
        "already installed",
        "user atlas exists",
        "user atlas-monitor exists",
        "uv 0.11.32 already installed",
        "kept as it is",
    ):
        assert line in again.stdout, again.stdout
    modes = server.out(
        "stat -c '%n %U %G %a' /opt/atlas /opt/atlas/releases /etc/atlas/credentials "
        "/etc/nginx/atlas /var/lib/atlas/deploy /etc/atlas/deploy.env"
    ).splitlines()
    assert modes == [
        "/opt/atlas root root 755",
        "/opt/atlas/releases root atlas 750",
        "/etc/atlas/credentials root root 700",
        "/etc/nginx/atlas root www-data 750",
        "/var/lib/atlas/deploy root root 700",
        "/etc/atlas/deploy.env root root 644",
    ]
    assert server.out("getent passwd atlas | cut -d: -f6,7") == "/nonexistent:/usr/sbin/nologin"
    assert server.out("getent passwd atlas-monitor | cut -d: -f7") == "/usr/sbin/nologin"


def test_the_preflight_executes_the_locked_dependencies(server: Server) -> None:
    """--probe-dependencies: a real locked install and import in a scratch directory."""
    result = server.sh(
        "cd /root/atlas && python3 -I src/atlas/deploy/preflight.py --resources standard "
        "--release-dir . --probe-dependencies --json",
        check=False,
        timeout=900,
    )
    findings = json.loads(result.stdout)["findings"]
    probe = next(f for f in findings if f["check"] == "probe")
    assert probe["status"] == "pass", probe
    server.notes["dependency_probe"] = probe["observed"]
    assert server.out("ls /tmp | grep -c atlas-preflight- || true") == "0"  # cleaned up


def test_a_release_is_built_read_only_and_checked(server: Server) -> None:
    started = time.monotonic()
    result = server.sh(
        f"/root/atlas/deploy/scripts/build-release.sh {server.commit} --repo file:///srv/atlas.git",
        timeout=1800,
    )
    server.notes["build_seconds"] = round(time.monotonic() - started)
    server.release = result.stdout.strip().splitlines()[-1]
    release = f"{RELEASES}/{server.release}"
    for check in ("atlas check", "atlas optimize verify", "api contract"):
        assert check in result.stdout
    assert (
        server.out(
            f"find {release} \\( -perm -g+w -o -perm -o+w -o ! -user root \\) ! -type l | wc -l"
        )
        == "0"
    )
    assert (
        server.sh(
            f"setpriv --reuid=atlas --regid=atlas --clear-groups touch {release}/x", check=False
        ).returncode
        != 0
    )
    assert (
        server.sh(
            f"setpriv --reuid=nobody --regid=nogroup --clear-groups ls {release}", check=False
        ).returncode
        != 0
    )
    assert (
        server.sh(f"test -d {release}/.git", check=False).returncode != 0
    )  # no repository metadata
    server.sh(f"{release}/.venv/bin/atlas-deploy manifest check {release}")
    server.notes["release_size"] = server.out(f"du -sh {release} | cut -f1")
    again = server.sh(
        f"/root/atlas/deploy/scripts/build-release.sh {server.commit} --repo file:///srv/atlas.git"
    )
    assert "exists: checking it against its manifest" in again.stdout


def test_an_interrupted_build_leaves_nothing_behind(server: Server) -> None:
    script = (
        f"/root/atlas/deploy/scripts/build-release.sh {server.broken} --repo file:///srv/atlas.git"
    )
    server.sh(f"nohup {script} > /root/interrupted.log 2>&1 & echo $! > /root/build.pid")
    server.wait(f"ls {RELEASES} | grep -q -- -{server.broken[:12]}", timeout=120)
    server.sh("kill -TERM $(cat /root/build.pid)")
    server.wait("! kill -0 $(cat /root/build.pid) 2>/dev/null", timeout=120)
    assert (
        server.sh(f"ls -A {RELEASES} | grep -v -- {server.release} | wc -l").stdout.strip() == "0"
    )
    assert "incomplete release was removed" in server.out("cat /root/interrupted.log")
    result = server.sh(script, timeout=1800)  # and builds cleanly afterwards
    server.broken_release = result.stdout.strip().splitlines()[-1]


# --- Activation


def test_the_first_activation(server: Server) -> None:
    release = f"{RELEASES}/{server.release}/.venv/bin/atlas-deploy"
    server.sh(f"{release} secrets init && {release} validate --installed")
    result = server.sh(f"{release} activate {server.release}", timeout=600)
    assert "activate: activated" in result.stdout
    assert server.show("ActiveState", "SubState")["ActiveState"] == "active"
    assert server.out("systemctl show atlas -p StatusText --value") == "ready: 7 components ready"
    server.sh(f"{DEPLOY} verify")
    listening = server.out("ss -ltnH | awk '{print $4}'")
    assert "127.0.0.1:8000" in listening
    assert "0.0.0.0:8000" not in listening
    assert "[::]:8000" not in listening
    score = server.out("systemd-analyze security atlas.service --no-pager | tail -1")
    server.notes["exposure"] = score
    assert float(score.split(":")[1].split()[0]) <= 1.5
    users = server.out(
        "ps -o user= --ppid $(systemctl show -p MainPID --value atlas) -p $(systemctl show -p "
        "MainPID --value atlas) | sort -u"
    )
    assert users == "atlas"
    # Monitoring runs from the first activation on, not from the first reboot.
    assert server.out("systemctl is-active atlas-monitor.timer") == "active"
    # nginx's way in: the service's own socket, in a directory only systemd creates.
    assert server.out("stat -c '%U %a %F' /run/atlas") == "atlas 755 directory"
    assert server.out("stat -c '%U %F' /run/atlas/api.sock") == "atlas socket"
    assert server.out("curl -s --unix-socket /run/atlas/api.sock http://localhost/health/live")
    status = json.loads(server.out(f"{DEPLOY} status"))
    assert status["last_verified"]["release"] == server.release


def test_no_secret_is_exposed(server: Server) -> None:
    key = server.out("cat /etc/atlas/credentials/api-key")
    assert len(key) == 64
    leaks = server.out(
        f"(systemctl show atlas; journalctl --no-pager -o export; cat /etc/atlas/*.env; "
        f"for p in $(systemctl show -p MainPID --value atlas) $({WORKERS}); do "
        f"tr '\\0' '\\n' < /proc/$p/environ; done) | grep -c {key} || true"
    )
    assert leaks == "0"
    assert (
        server.sh(
            "setpriv --reuid=atlas --regid=atlas --clear-groups cat /etc/atlas/credentials/api-key",
            check=False,
        ).returncode
        != 0
    )
    for pid in server.out(WORKERS).split():
        assert server.out(f"cat /proc/{pid}/oom_score_adj") == "500"


def test_the_private_profile_through_nginx_and_tls(server: Server) -> None:
    server.sh(
        "set -e; mkdir -p /root/ca && cd /root/ca && "
        "openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem -days 90 -subj "
        "'/CN=e2e CA' 2>/dev/null && "
        f"openssl req -newkey rsa:2048 -nodes -keyout site.key -out site.csr -subj '/CN={DOMAIN}' "
        "2>/dev/null && "
        f"echo 'subjectAltName=DNS:{DOMAIN}' > ext && "
        "openssl x509 -req -in site.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out site.pem "
        "-days 90 -extfile ext 2>/dev/null && "
        "install -d /etc/atlas-e2e-tls && install -m 0644 site.pem "
        "/etc/atlas-e2e-tls/fullchain.pem && "
        "install -m 0600 site.key /etc/atlas-e2e-tls/privkey.pem && "
        "printf 'ATLAS_DEPLOY_PROFILE=private\\nATLAS_DEPLOY_RESOURCES=standard\\n"
        f"ATLAS_DEPLOY_DOMAIN={DOMAIN}\\nATLAS_DEPLOY_IPV6=false\\n"
        "ATLAS_DEPLOY_TLS_CERT=/etc/atlas-e2e-tls/fullchain.pem\\n"
        "ATLAS_DEPLOY_TLS_KEY=/etc/atlas-e2e-tls/privkey.pem\\n' > /etc/atlas/deploy.env"
    )
    server.sh(
        f"echo {PASSWORD} | {DEPLOY} secrets add-user alice --password-stdin && systemctl start "
        "nginx"
    )
    # The monitor checks the served certificate against the system trust store, as it
    # would a Let's Encrypt one: trust the test CA there.
    server.sh(
        "cp /root/ca/ca.pem /usr/local/share/ca-certificates/atlas-e2e-ca.crt && "
        "update-ca-certificates >/dev/null"
    )
    assert "activated" in server.sh(f"{DEPLOY} activate {server.release}", timeout=600).stdout
    curl = f"curl -s --noproxy '*' --cacert /root/ca/ca.pem --resolve {DOMAIN}:443:127.0.0.1"
    url = f"https://{DOMAIN}"
    assert server.out(f"{curl} -o /dev/null -w '%{{http_code}}' {url}/health/ready") == "401"
    assert (
        server.out(
            f"{curl} -o /dev/null -w '%{{http_code}}' -u alice:{PASSWORD} {url}/health/ready"
        )
        == "200"
    )
    simulation = server.out(
        f"{curl} -u alice:{PASSWORD} -X POST {url}/api/v1/simulations -H 'content-type: "
        f"application/json' -d '{{\"horizon_days\": 7}}'"
    )
    assert "HYPOTHETICAL" in json.loads(simulation)["data"]["label"]
    assert (
        server.out(f"{curl} -o /dev/null -w '%{{http_code}}' -u alice:{PASSWORD} {url}/docs")
        == "404"
    )
    assert (
        server.out(
            f"{curl} -o /dev/null -w '%{{http_code}}' --resolve {DOMAIN}:80:127.0.0.1 http://{DOMAIN}/x"
        )
        == "301"
    )
    assert PASSWORD not in server.out(
        "cat /var/log/nginx/atlas-access.log /var/log/nginx/atlas-error.log"
    )


def test_the_dashboard_through_nginx(server: Server) -> None:
    """(M7) The release's dashboard, published for nginx by the activation and served by the
    private site: a read-only copy nginx can read (the release itself stays closed to it),
    the exact built bytes, the dashboard's policy, an account required."""
    published = f"/opt/atlas/dashboard/{server.release}"
    assert server.out(f"stat -c '%U %G %a' /opt/atlas/dashboard {published}").splitlines() == [
        "root www-data 750",
        "root www-data 750",
    ]
    assert server.out(f"find {published} -type l | wc -l") == "0"
    assert server.out(f"find {published} -type f ! -perm 640 | wc -l") == "0"
    as_nginx = "setpriv --reuid=www-data --regid=www-data --init-groups"
    assert server.sh(f"{as_nginx} cat {published}/index.html", check=False).returncode == 0
    release_index = f"{RELEASES}/{server.release}/web/dist/index.html"
    assert server.sh(f"{as_nginx} cat {release_index}", check=False).returncode != 0
    curl = f"curl -s --noproxy '*' --cacert /root/ca/ca.pem --resolve {DOMAIN}:443:127.0.0.1"
    url = f"https://{DOMAIN}"
    assert server.out(f"{curl} -o /dev/null -w '%{{http_code}}' {url}/") == "401"
    headers = server.out(f"{curl} -u alice:{PASSWORD} -D - -o /root/index.html {url}/forecasting")
    assert "require-trusted-types-for 'script'" in headers
    assert server.out("sha256sum < /root/index.html") == server.out(f"sha256sum < {release_index}")
    descriptor = json.loads(server.out(f"{curl} -u alice:{PASSWORD} {url}/atlas-deployment.json"))
    assert descriptor["profile"] == "private"
    assert descriptor["simulations"] == "enabled"
    manifest = json.loads(server.out(f"cat {published}/atlas-dashboard.json"))
    for path in manifest["files"]:
        target = "/" if path == "index.html" else f"/{path}"
        code = server.out(
            f"{curl} -o /dev/null -w '%{{http_code}}' -u alice:{PASSWORD} {url}{target}"
        )
        assert code == "200", path
    status = json.loads(server.out(f"{DEPLOY} status"))
    assert status["manifest"]["dashboard"]["version"] == manifest["version"]


# --- Reliability


def test_a_crashed_server_is_restarted(server: Server) -> None:
    before = server.show("MainPID", "NRestarts")
    server.sh(f"kill -9 {before['MainPID']}")
    server.wait(
        f"test $(systemctl show -p NRestarts --value atlas) -gt {before['NRestarts']} && "
        "systemctl is-active atlas",
        timeout=60,
    )
    server.wait("systemctl show -p StatusText --value atlas | grep -q '^ready'", timeout=60)
    server.sh(f"{DEPLOY} verify")


def test_a_hung_server_is_restarted_by_the_watchdog(server: Server) -> None:
    before = server.show("MainPID", "NRestarts")
    server.sh(f"kill -STOP {before['MainPID']}")
    server.wait(
        f"test $(systemctl show -p NRestarts --value atlas) -gt {before['NRestarts']}", timeout=120
    )
    server.wait("systemctl show -p StatusText --value atlas | grep -q '^ready'", timeout=90)
    assert (
        "watchdog timeout" in server.out("journalctl -u atlas --no-pager -o cat | tail -40").lower()
    )


def test_a_killed_worker_is_replaced(server: Server) -> None:
    """A worker killed between simulations (as the OOM killer would) is replaced by the
    next readiness check (the monitor runs one every minute); no request fails."""
    worker = server.out(f"{WORKERS} | head -1")
    server.sh(f"kill -9 {worker}")
    server.sh("curl -sf --noproxy '*' -o /dev/null http://127.0.0.1:8000/health/ready")
    server.wait(f"test $({WORKERS} | wc -l) -eq 2", timeout=60)
    assert worker not in server.out(WORKERS).split()
    assert "worker_died_idle" in server.out("journalctl -u atlas --no-pager -o cat | tail -50")
    server.sh(f"{DEPLOY} verify")


def test_a_configuration_error_does_not_loop(server: Server) -> None:
    server.sh(
        "cp /etc/atlas/atlas.env /root/atlas.env.good && "
        "sed -i 's/^ATLAS_API_MAX_CONCURRENT_SIMULATIONS=.*/"
        "ATLAS_API_MAX_CONCURRENT_SIMULATIONS=99/' /etc/atlas/atlas.env"
    )
    since = server.out("date +%s")
    assert server.sh("systemctl restart atlas", check=False).returncode != 0
    time.sleep(15)  # longer than several restart delays (2 s, growing)
    state = server.show("ActiveState", "NRestarts", "ExecMainStatus")
    assert state["ActiveState"] == "failed"
    assert state["ExecMainStatus"] == "2"
    # Exit status 2 is never restarted. (systemd resets NRestarts on an explicit start,
    # so the journal is the evidence.)
    assert state["NRestarts"] == "0"
    journal = server.out(f"journalctl -u atlas --since @{since} --no-pager -o cat")
    assert "Scheduled restart job" not in journal
    assert "exceeds the" in journal  # the reason is in the log
    # Activating renders the environment again from deploy.env: the error is gone.
    assert "activated" in server.sh(f"{DEPLOY} activate {server.release}", timeout=600).stdout


def test_a_stop_finishes_the_simulation_in_flight(server: Server) -> None:
    key = server.out("cat /etc/atlas/credentials/api-key")
    cursor = server.out("journalctl -u atlas -n 0 --show-cursor -q").split("cursor: ", 1)[1]
    tracker = server.out("pgrep -u atlas -f resource_tracker || true")
    server.sh(
        f"(curl -s --noproxy '*' -o /root/sim.json -w '%{{http_code}}' -X POST "
        "http://127.0.0.1:8000/api/v1/simulations "
        f"-H 'X-API-Key: {key}' -H 'content-type: application/json' -d '{{\"horizon_days\": 30}}' "
        "> /root/sim.status) & "
        "sleep 0.15; systemctl stop atlas; wait"
    )
    assert server.out("cat /root/sim.status") == "200"
    # A clean exit: uvicorn finishes the request, stops the workers, then re-raises the
    # SIGTERM it caught (exit status 15, which systemd counts as a success).
    state = server.show("Result", "ExecMainCode", "ExecMainStatus", "ActiveState")
    assert state["Result"] == "success", state
    assert state["ActiveState"] == "inactive"
    assert (state["ExecMainCode"], state["ExecMainStatus"]) in {("1", "0"), ("2", "15")}, state
    journal = server.out(f"journalctl -u atlas --after-cursor '{cursor}' --no-pager -o cat")
    requests = [json.loads(line) for line in journal.splitlines() if '"event": "request"' in line]
    assert any(r["path"] == "/api/v1/simulations" and r["status"] == 200 for r in requests), journal
    assert "worker_crashed" not in journal
    # After the main process exits, KillMode=mixed SIGKILLs what is left: at most the
    # multiprocessing resource tracker (it holds no state), never a simulation worker.
    killed = set(re.findall(r"Killing process (\d+) .*SIGKILL", journal))
    assert killed <= set(tracker.split()), journal
    server.notes["stop_killed_leftovers"] = sorted(killed)
    server.sh("systemctl start atlas")


def test_a_broken_release_is_rolled_back_automatically(server: Server) -> None:
    result = server.sh(f"{DEPLOY} activate {server.broken_release}", check=False, timeout=600)
    assert result.returncode == 1
    assert "rolled_back" in result.stdout
    assert "m4_reference_run" in result.stdout
    assert server.out("readlink /opt/atlas/current") == f"releases/{server.release}"
    server.sh(f"{DEPLOY} verify")


def test_an_explicit_rollback(server: Server) -> None:
    """A switch between two verified releases: a second release (another commit, the same
    tree) is built and activated, then `atlas-deploy rollback` returns to the release that
    was active before it, with its site, its dashboard copy and a passing verification."""
    result = server.sh(
        f"/root/atlas/deploy/scripts/build-release.sh {server.second} --repo file:///srv/atlas.git",
        timeout=1800,
    )
    server.second_release = result.stdout.strip().splitlines()[-1]
    assert server.second_release != server.release
    assert (
        "activated" in server.sh(f"{DEPLOY} activate {server.second_release}", timeout=600).stdout
    )
    assert server.out("readlink /opt/atlas/current") == f"releases/{server.second_release}"
    site = server.out("cat /etc/nginx/sites-available/atlas.conf")
    assert f"root /opt/atlas/dashboard/{server.second_release};" in site
    assert "activated" in server.sh(f"{DEPLOY} rollback", timeout=600).stdout
    assert server.out("readlink /opt/atlas/current") == f"releases/{server.release}"
    status = json.loads(server.out(f"{DEPLOY} status"))
    assert status["current"] == server.release
    assert status["last_verified"]["release"] == server.release
    assert status["manifest"]["scientific_status"]["m3_interval_status"] == "failed_calibration"
    site = server.out("cat /etc/nginx/sites-available/atlas.conf")
    assert f"root /opt/atlas/dashboard/{server.release};" in site
    assert f"/opt/atlas/dashboard/{server.second_release};" not in site
    published = f"/opt/atlas/dashboard/{server.release}"
    release_index = f"{RELEASES}/{server.release}/web/dist/index.html"
    assert server.out(f"sha256sum < {published}/index.html") == server.out(
        f"sha256sum < {release_index}"
    )
    server.sh(f"{DEPLOY} verify")


def test_the_dashboard_follows_the_release(server: Server) -> None:
    """(M7) After the automatic and the explicit rollbacks, the site serves the dashboard of
    the release the API runs."""
    site = server.out("cat /etc/nginx/sites-available/atlas.conf")
    assert f"root /opt/atlas/dashboard/{server.release};" in site
    assert f"/opt/atlas/dashboard/{server.broken_release};" not in site
    assert f"/opt/atlas/dashboard/{server.second_release};" not in site


def test_an_interrupted_activation_is_recovered(server: Server) -> None:
    broken = server.broken_release
    server.sh(
        f'echo \'{{"action": "activate", "outcome": "in_progress", "release": "{broken}", '
        f'"previous": "{server.release}", "ts": '
        f'"2026-10-15T00:00:00+00:00"}}\' >> /var/lib/atlas/deploy/history.jsonl && '
        f"ln -sfn releases/{broken} /opt/atlas/current && systemctl restart atlas"
    )
    # Nothing else may change the deployment until the interrupted activation is completed.
    blocked = server.sh(f"{DEPLOY} activate {server.release}", check=False, timeout=600)
    assert blocked.returncode != 0
    assert "atlas-deploy recover" in blocked.stdout
    result = server.sh(f"{DEPLOY} recover", check=False, timeout=600)
    assert "rolled_back" in result.stdout, result.stdout
    assert server.out("readlink /opt/atlas/current") == f"releases/{server.release}"
    server.sh(f"{DEPLOY} verify")


# --- Monitoring, reboot, resources


def test_the_monitor_reports_and_alerts(server: Server) -> None:
    server.sh("systemctl start atlas-monitor.service")
    report = json.loads(
        server.out(
            'journalctl -u atlas-monitor --no-pager -o cat | grep \'"event": "monitor"\' | tail -1'
        )
    )
    assert report["status"] in ("ok", "warn"), report
    assert report["metrics"]["workers"]["alive"] == 2
    server.notes["monitor_metrics"] = report["metrics"]
    server.sh(
        "printf '#!/bin/sh\\necho \"ATLAS ALERT for $1\"\\n' > /etc/atlas/alert-hook && chmod 755 "
        "/etc/atlas/alert-hook"
    )
    server.sh("systemctl stop atlas && (systemctl start atlas-monitor.service || true)")
    server.wait(
        "journalctl --no-pager -o cat | grep -q 'ATLAS ALERT for atlas-monitor.service'", timeout=60
    )
    server.sh("systemctl start atlas && rm /etc/atlas/alert-hook")


def test_the_deployment_survives_a_reboot(server: Server) -> None:
    docker("restart", NAME, timeout=300)
    server.boot()
    server.wait("systemctl is-active atlas && systemctl is-active atlas-monitor.timer", timeout=120)
    server.wait("systemctl show -p StatusText --value atlas | grep -q '^ready'", timeout=90)
    server.sh(f"{DEPLOY} verify")


def test_resource_use_stays_within_the_limits(server: Server) -> None:
    key = server.out("cat /etc/atlas/credentials/api-key")
    server.sh(
        "for i in $(seq 1 10); do curl -s --noproxy '*' -o /dev/null -X POST "
        "http://127.0.0.1:8000/api/v1/simulations "
        f"-H 'X-API-Key: {key}' -H 'content-type: application/json' -d "
        f'"{{\\"horizon_days\\": 30}}"; done'
    )
    memory = int(server.show("MemoryCurrent")["MemoryCurrent"])
    processes = server.out(
        f"for p in $(systemctl show -p MainPID --value atlas) $({WORKERS}); do "
        "awk '/VmRSS/ {print $2}' /proc/$p/status; done"
    ).split()
    server.notes["service_memory_mib"] = memory // (1 << 20)
    server.notes["process_rss_mib"] = [int(kib) // 1024 for kib in processes]
    server.notes["nginx_rss_mib"] = server.out(
        "ps -o rss= -C nginx | awk '{s+=$1} END {print int(s/1024)}'"
    )
    assert memory < 640 << 20  # below MemoryHigh of the standard profile
    assert int(server.show("TasksCurrent")["TasksCurrent"]) < 128


def test_returning_to_the_tunnel_profile_closes_the_site(server: Server) -> None:
    curl = f"curl -s --noproxy '*' --cacert /root/ca/ca.pem --resolve {DOMAIN}:443:127.0.0.1"
    live = (
        f"{curl} -o /dev/null -w '%{{http_code}}' -u alice:{PASSWORD} https://{DOMAIN}/health/live"
    )
    assert server.out(live) == "200"
    server.sh(
        "printf 'ATLAS_DEPLOY_PROFILE=tunnel\\nATLAS_DEPLOY_RESOURCES=standard\\n' "
        "> /etc/atlas/deploy.env"
    )
    assert "activated" in server.sh(f"{DEPLOY} activate {server.release}", timeout=600).stdout
    assert server.sh("test -e /etc/nginx/sites-enabled/atlas.conf", check=False).returncode != 0
    assert server.sh(f"{live} || true").stdout.strip() in ("000", "")  # nothing answers
    assert server.out("systemctl is-active nginx") == "active"  # nginx itself untouched
    server.sh(f"{DEPLOY} verify")
