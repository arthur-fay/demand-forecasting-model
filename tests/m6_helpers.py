"""Shared helpers for the M6 deployment tests.

Nothing here touches the machine's own configuration: fake servers are directory trees
under ``tmp_path`` (``/etc/os-release``, ``/proc/meminfo``, ...), releases are copies of the
repository's committed files, and real nginx or openssl runs only on loopback ports with
every path in a temporary directory.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from atlas.deploy.layout import Layout
from atlas.deploy.preflight import System
from tests.conftest import REPO_ROOT

#: CI sets this so that a missing nginx, openssl, shellcheck or systemd-analyze fails the
#: tests instead of skipping them.
REQUIRE_TOOLS = os.environ.get("ATLAS_REQUIRE_DEPLOY_TOOLS") == "1"

UBUNTU_2404 = """\
PRETTY_NAME="Ubuntu 24.04.3 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.3 LTS (Noble Numbat)"
ID=ubuntu
ID_LIKE=debian
"""
CPUINFO = "processor\t: 0\nflags\t\t: fpu sse sse2 ssse3 sse4_1 sse4_2 popcnt avx avx2\n"
GIB = 1 << 30
#: A listener header line, as in /proc/net/tcp.
TCP_HEADER = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  "
    "timeout inode\n"
)


def need_tools(*names: str) -> None:
    """Skip (or, in CI, fail) when a command the test needs is not installed."""
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        message = f"needs {', '.join(missing)}"
        if REQUIRE_TOOLS:
            pytest.fail(message + " (ATLAS_REQUIRE_DEPLOY_TOOLS=1)")
        pytest.skip(message)


def write(path: Path, text: str, mode: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)
    return path


def tcp_line(index: int, address: str, port: int, inode: int, state: str = "0A") -> str:
    """One /proc/net/tcp line for an IPv4 socket."""
    octets = [int(part) for part in address.split(".")]
    hex_address = "".join(f"{octet:02X}" for octet in reversed(octets))
    return (
        f"   {index}: {hex_address}:{port:04X} 00000000:0000 {state} 00000000:00000000 "
        f"00:00000000 00000000     0        0 {inode} 1 0000000000000000 100 0 0 10 0\n"
    )


def meminfo(total_mib: int, available_mib: int, swap_mib: int = 0) -> str:
    return (
        f"MemTotal:       {total_mib * 1024} kB\n"
        f"MemFree:        {available_mib * 1024} kB\n"
        f"MemAvailable:   {available_mib * 1024} kB\n"
        f"SwapTotal:      {swap_mib * 1024} kB\n"
        f"SwapFree:       {swap_mib * 1024} kB\n"
    )


def fake_runner(
    responses: Mapping[str, tuple[int, str]],
) -> Callable[[Sequence[str], float], tuple[int, str]]:
    """A command runner answering from ``responses`` keyed by the command's first words."""

    def run(command: Sequence[str], timeout: float) -> tuple[int, str]:
        for words in (" ".join(command[:n]) for n in range(len(command), 0, -1)):
            if words in responses:
                return responses[words]
        return -1, "not found"

    return run


DEFAULT_COMMANDS = {
    "systemctl --version": (0, "systemd 255 (255.4-1ubuntu8)\n+PAM +AUDIT"),
    "systemctl is-active": (3, "inactive"),
    "/usr/bin/python3.12": (0, "3.12.3"),
    "git version": (0, "git version 2.43.0"),
    "openssl version": (0, "OpenSSL 3.0.13 30 Jan 2024"),
    "nginx -v": (0, "nginx version: nginx/1.24.0 (Ubuntu)"),
    "uv --version": (-1, "not found"),
    "ufw status": (0, "Status: active"),
    "sshd -T": (
        0,
        "passwordauthentication no\npermitrootlogin prohibit-password\n"
        "kbdinteractiveauthentication no\n",
    ),
    "timedatectl show": (0, "yes"),
}


def fake_server(
    tmp_path: Path,
    *,
    os_release: str = UBUNTU_2404,
    total_mib: int = 2048,
    available_mib: int = 1700,
    swap_mib: int = 1024,
    cpus: int = 2,
    cpuinfo: str = CPUINFO,
    machine: str = "x86_64",
    tcp: str = "",
    free_gib: float = 20.0,
    total_gib: float = 40.0,
    loadavg: str = "0.10 0.20 0.15 1/120 4242\n",
    commands: Mapping[str, tuple[int, str]] | None = None,
    euid: int = 0,
    reachable: bool = True,
) -> System:
    """A fake candidate server: a tree of the files the preflight reads, and fake commands."""
    root = tmp_path / "server"
    write(root / "etc/os-release", os_release)
    write(root / "proc/meminfo", meminfo(total_mib, available_mib, swap_mib))
    write(root / "proc/cpuinfo", cpuinfo)
    write(root / "proc/loadavg", loadavg)
    write(root / "proc/net/tcp", TCP_HEADER + tcp)
    write(root / "proc/net/tcp6", TCP_HEADER)
    write(root / "proc/pressure/memory", "some avg10=0.00 avg60=0.50 avg300=0.10 total=1\n")
    write(root / "proc/pressure/cpu", "some avg10=0.00 avg60=1.00 avg300=0.10 total=1\n")
    write(root / "etc/apt/apt.conf.d/20auto-upgrades", 'APT::Periodic::Unattended-Upgrade "1";\n')
    write(root / "usr/bin/python3.12", "#!/bin/sh\n", 0o755)
    for directory in ("opt", "var/cache", "var/log", "tmp"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    return System(
        root=root,
        runner=fake_runner({**DEFAULT_COMMANDS, **(commands or {})}),
        disk_free=lambda path: (int(free_gib * GIB), int(total_gib * GIB)),
        connect=lambda host, port, timeout: reachable,
        cpus=lambda: cpus,
        euid=euid,
        machine=machine,
    )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def run(command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        list(command), capture_output=True, text=True, check=False, timeout=120, **kwargs
    )


#: What a release needs for the manifest, the renderer and the API (the snapshot registers
#: the legacy artifacts too, so they are part of every release).
RELEASE_PATHS = (
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    ".github/ci-requirements.txt",
    "snapshots",
    "01 Data",
    "02 Notebooks",
    "03 SQL",
    "04 Power BI",
    "05 Screenshots",
    "06 Exports",
    "configs",
    "docs/results",
    "docs/api",
    "deploy",
)


def make_release(
    base: Path, *, commit: str = "a" * 40, built_at: str = "2026-10-15T12:00:00+00:00"
) -> Path:
    """A release directory under ``base/opt/atlas/releases`` with a fake environment and a
    manifest (``release.json``), made from the committed files of this checkout."""
    from atlas.deploy.manifest import build_manifest, project_version, write_manifest

    name = f"{project_version(REPO_ROOT)}-{commit[:12]}"
    release = base / "opt/atlas/releases" / name
    release.mkdir(parents=True)
    for relative in RELEASE_PATHS:
        source = REPO_ROOT / relative
        if source.is_dir():
            shutil.copytree(source, release / relative)
        else:
            (release / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, release / relative)
    venv = release / ".venv"
    write(venv / "pyvenv.cfg", "home = /usr/bin\nversion_info = 3.12.3\n")
    write(venv / "bin/atlas", "#!/bin/sh\nexit 0\n", 0o755)
    write(venv / "bin/atlas-deploy", "#!/bin/sh\nexit 0\n", 0o755)
    (venv / "bin/python").symlink_to("/usr/bin/python3.12")
    manifest = build_manifest(
        release,
        release_id=name,
        commit=commit,
        built_at=built_at,
        uv_version="0.11.32",
        checks=["atlas check", "atlas optimize verify"],
    )
    write_manifest(release, manifest)
    for path in [release, *release.rglob("*")]:
        if not path.is_symlink():
            path.chmod(path.stat().st_mode & ~0o022)
    return release


def layout_for(root: Path) -> Layout:
    return Layout(root=root)


DEPLOY_ENV_PRIVATE = """\
ATLAS_DEPLOY_PROFILE=private
ATLAS_DEPLOY_RESOURCES=standard
ATLAS_DEPLOY_DOMAIN=atlas.example.org
ATLAS_DEPLOY_IPV6=false
"""


# --- Real nginx on loopback ports ---------------------------------------------------------------


def make_tls(directory: Path, domain: str = "atlas.example.org") -> tuple[Path, Path, Path]:
    """A throw-away CA and a certificate for ``domain`` signed by it: (ca, cert, key)."""
    need_tools("openssl")
    directory.mkdir(parents=True, exist_ok=True)
    ca_key, ca = directory / "ca.key", directory / "ca.pem"
    key, csr, cert = directory / "site.key", directory / "site.csr", directory / "site.pem"
    ext = write(directory / "ext.cnf", f"subjectAltName=DNS:{domain}\n")
    for command in (
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(ca_key),
            "-out",
            str(ca),
            "-days",
            "2",
            "-subj",
            "/CN=ATLAS test CA",
        ],
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(csr),
            "-subj",
            f"/CN={domain}",
        ],
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(csr),
            "-CA",
            str(ca),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(cert),
            "-days",
            "2",
            "-extfile",
            str(ext),
        ],
    ):
        result = run(command)
        assert result.returncode == 0, result.stderr
    key.chmod(0o600)
    return ca, cert, key


class Nginx:
    """A real nginx serving a rendered ATLAS site on loopback ports, every path temporary.

    The rendered text is used as it is, except the listen addresses (loopback test ports)
    and the log directory: everything under test is the production configuration.
    """

    def __init__(
        self,
        directory: Path,
        site: str,
        *,
        http_port: int | None = None,
        https_port: int | None = None,
    ) -> None:
        need_tools("nginx")
        self.directory = directory
        self.http_port = http_port or free_port()
        self.https_port = https_port or free_port()
        for name in ("tmp", "logs"):
            (directory / name).mkdir(parents=True, exist_ok=True)
        site = (
            site.replace(
                "listen 80 default_server;", f"listen 127.0.0.1:{self.http_port} default_server;"
            )
            .replace("listen 80;", f"listen 127.0.0.1:{self.http_port};")
            .replace(
                "listen 443 ssl default_server;",
                f"listen 127.0.0.1:{self.https_port} ssl default_server;",
            )
            .replace("listen 443 ssl;", f"listen 127.0.0.1:{self.https_port} ssl;")
            .replace("/var/log/nginx", str(directory / "logs"))
        )
        assert "listen [::]" not in site, "render the test site with ATLAS_DEPLOY_IPV6=false"
        self.site = write(directory / "site.conf", site)
        temp = directory / "tmp"
        user = "user root;\n" if os.geteuid() == 0 else ""
        self.conf = write(
            directory / "nginx.conf",
            f"{user}worker_processes 1;\npid {directory / 'nginx.pid'};\n"
            f"error_log {directory / 'logs' / 'main-error.log'} warn;\n"
            "events { worker_connections 128; }\n"
            "http {\n"
            "  default_type application/octet-stream;\n"
            f"  client_body_temp_path {temp}/body; proxy_temp_path {temp}/proxy;\n"
            f"  fastcgi_temp_path {temp}/fastcgi; uwsgi_temp_path {temp}/uwsgi;\n"
            f"  scgi_temp_path {temp}/scgi;\n"
            f"  include {self.site};\n"
            "}\n",
        )

    def command(self, *extra: str) -> list[str]:
        return [
            "nginx",
            "-c",
            str(self.conf),
            "-p",
            str(self.directory),
            "-e",
            str(self.directory / "logs" / "startup.log"),
            *extra,
        ]

    def test(self) -> subprocess.CompletedProcess[str]:
        return run(self.command("-t"))

    def __enter__(self) -> Nginx:
        result = run(self.command())
        assert result.returncode == 0, result.stderr
        return self

    def __exit__(self, *exc: object) -> None:
        run(self.command("-s", "quit"))
        pid = self.directory / "nginx.pid"
        for _ in range(100):
            if not pid.exists():
                break
            time.sleep(0.05)

    def reload(self) -> None:
        result = run(self.command("-s", "reload"))
        assert result.returncode == 0, result.stderr
        time.sleep(0.3)
