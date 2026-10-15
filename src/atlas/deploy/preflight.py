"""Read-only preflight assessment of a server for an ATLAS deployment (M6).

Run it on the candidate server before anything is installed, with the system Python
(standard library only, Python 3.10 or later; no ATLAS dependency is needed)::

    python3 -I src/atlas/deploy/preflight.py --resources standard
    python3 -I src/atlas/deploy/preflight.py --resources low --json

It inspects, and never changes:

* the Linux distribution and version, the kernel and systemd;
* the CPU architecture, the instruction set the locked NumPy wheels need, and the CPUs;
* total and available memory, swap and memory pressure;
* free storage where releases, caches, logs and temporary files go;
* load, the largest processes and services that compete for resources;
* listening sockets: the ports ATLAS needs and services already exposed;
* Python 3.12, ``venv``, git, nginx, uv and the firewall status;
* whether the locked dependencies can run here: the lock's wheels for this platform, and
  optionally (``--probe-dependencies``) a real install and import in a throw-away
  directory that is deleted afterwards.

It estimates the memory ATLAS will use and compares it with what is available. Every
finding has a status (``pass``, ``info``, ``warn``, ``fail``) and, unless it passes, the
action to take. Any ``fail`` makes the verdict ``UNSUITABLE`` and the exit status 1: the
server must not be used until the finding is resolved. Exit status 0 means the server is
suitable (possibly with warnings); 2 is a usage error.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PASS = "pass"  # noqa: S105 - a finding status, not a password
INFO = "info"
WARN = "warn"
FAIL = "fail"
_ORDER = {PASS: 0, INFO: 1, WARN: 2, FAIL: 3}

SUITABLE = "SUITABLE"
SUITABLE_WITH_WARNINGS = "SUITABLE_WITH_WARNINGS"
UNSUITABLE = "UNSUITABLE"

MIB = 1024 * 1024
GIB = 1024 * MIB

#: Ubuntu LTS releases: the tested target, and those that need more work or are refused.
TESTED_RELEASE = "24.04"
UNTESTED_LTS = ("22.04", "26.04")
#: CPU architectures with manylinux wheels for every locked native package.
ARCHITECTURES = ("x86_64", "aarch64")
#: The x86-64 instructions the locked NumPy wheels (x86-64-v2 baseline) require.
X86_FLAGS = ("ssse3", "sse4_1", "sse4_2", "popcnt")
PYTHON = "3.12"
#: Ports ATLAS needs: the API on loopback, and nginx for ACME and HTTPS.
API_PORT = 8000
PROXY_PORTS = (80, 443)
#: Hosts a release build downloads from (source, locked wheels, certificates).
BUILD_HOSTS = ("github.com", "pypi.org", "files.pythonhosted.org", "acme-v02.api.letsencrypt.org")

#: Memory (MiB) of each ATLAS component, from the M5 measurements (docs/API.md section 9:
#: server 175, each worker 121-123, resource tracker 12) and the M6 measurements under
#: systemd (docs/DEPLOYMENT.md), rounded up.
MEMORY_MIB = {
    "api_server": 190,
    "simulation_worker": 135,
    "resource_tracker": 15,
    "reverse_proxy": 20,
    "monitor": 30,
}
#: A release build checks the new release (snapshot validation and the M4 reference
#: reproduction) while the running release keeps serving.
BUILD_PEAK_MIB = 400
#: A typical Ubuntu server with sshd, journald and the usual agents (for the estimate only;
#: the assessment uses the memory actually in use).
TYPICAL_OS_MIB = 300


@dataclass(frozen=True)
class Profile:
    """A resource profile: how many simulation workers, and what the server needs."""

    name: str
    workers: int
    min_cpus: int
    min_memory_mib: int
    recommended_swap_mib: int
    min_free_opt_mib: int
    min_free_var_mib: int


PROFILES: dict[str, Profile] = {
    # One worker: one simulation at a time. For a 1 GB / 1 vCPU server with swap.
    "low": Profile("low", 1, 1, 900, 1024, 2048, 1024),
    # Two workers (the M5 default): for 2 GB / 2 vCPU and above.
    "standard": Profile("standard", 2, 2, 1800, 1024, 3072, 1536),
}


def service_memory_mib(workers: int) -> int:
    """Steady-state memory of the ATLAS service with ``workers`` simulation workers."""
    m = MEMORY_MIB
    return m["api_server"] + workers * m["simulation_worker"] + m["resource_tracker"]


def deployment_memory_mib(workers: int) -> int:
    """The service, the reverse proxy and the periodic monitor."""
    return service_memory_mib(workers) + MEMORY_MIB["reverse_proxy"] + MEMORY_MIB["monitor"]


@dataclass
class Finding:
    """One observation: its status, what was observed, what is required, what to do."""

    area: str
    check: str
    status: str
    observed: str
    requirement: str = ""
    action: str = ""


@dataclass
class Report:
    profile: str
    findings: list[Finding] = field(default_factory=list)
    estimate: dict[str, Any] = field(default_factory=dict)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    @property
    def verdict(self) -> str:
        statuses = {f.status for f in self.findings}
        if FAIL in statuses:
            return UNSUITABLE
        if WARN in statuses:
            return SUITABLE_WITH_WARNINGS
        return SUITABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "profile": self.profile,
            "estimate": self.estimate,
            "findings": [asdict(f) for f in self.findings],
        }


# --- The system being inspected -----------------------------------------------------------

Runner = Callable[[Sequence[str], float], tuple[int, str]]


def run_command(command: Sequence[str], timeout: float) -> tuple[int, str]:
    """Run a read-only inspection command; ``(-1, reason)`` if it cannot run."""
    executable = shutil.which(command[0])
    if executable is None:
        return -1, "not found"
    try:
        completed = subprocess.run(  # noqa: S603 - fixed inspection commands, no shell
            [executable, *command[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, type(exc).__name__
    return completed.returncode, (completed.stdout + completed.stderr).strip()


@dataclass
class System:
    """Access to the inspected system. ``root`` prefixes every absolute path read, so the
    tests can describe a fake server in a directory; commands go through ``runner``."""

    root: Path = Path("/")
    runner: Runner = run_command
    disk_free: Callable[[Path], tuple[int, int]] | None = None
    connect: Callable[[str, int, float], bool] | None = None
    cpus: Callable[[], int] | None = None
    euid: int = field(default_factory=os.geteuid)
    machine: str = field(default_factory=platform.machine)

    def path(self, absolute: str) -> Path:
        return self.root / absolute.lstrip("/")

    def read(self, absolute: str) -> str | None:
        try:
            return self.path(absolute).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def run(self, *command: str, timeout: float = 10.0) -> tuple[int, str]:
        return self.runner(command, timeout)

    def free_bytes(self, absolute: str) -> tuple[int, int, str]:
        """(free bytes, total bytes, the existing path measured) of ``absolute``'s filesystem."""
        target = Path(absolute)
        while not self.path(str(target)).exists() and target != target.parent:
            target = target.parent
        local = self.path(str(target))
        if self.disk_free is not None:
            free, total = self.disk_free(local)
        else:
            stats = os.statvfs(local)
            free, total = stats.f_bavail * stats.f_frsize, stats.f_blocks * stats.f_frsize
        return free, total, str(target)

    def available_cpus(self) -> int:
        if self.cpus is not None:
            return self.cpus()
        try:
            return max(1, len(os.sched_getaffinity(0)))
        except AttributeError:  # pragma: no cover - platforms without sched_getaffinity
            return max(1, os.cpu_count() or 1)

    def can_connect(self, host: str, port: int, timeout: float = 5.0) -> bool:
        if self.connect is not None:
            return self.connect(host, port, timeout)
        try:  # pragma: no cover - real network access is exercised on servers only
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:  # pragma: no cover
            return False


# --- Parsers ---------------------------------------------------------------------------------


def parse_os_release(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and name and not name.startswith("#"):
            values[name] = value.strip().strip('"').strip("'")
    return values


def parse_meminfo(text: str) -> dict[str, int]:
    """``/proc/meminfo`` in bytes."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            factor = 1024 if len(parts) > 1 and parts[1].lower() == "kb" else 1
            values[name.strip()] = int(parts[0]) * factor
    return values


def cpu_flags(cpuinfo: str) -> set[str]:
    for line in cpuinfo.splitlines():
        name, _, value = line.partition(":")
        if name.strip() in ("flags", "Features"):
            return set(value.split())
    return set()


def parse_pressure(text: str) -> float | None:
    """The ``some avg60`` value of a ``/proc/pressure/*`` file."""
    match = re.search(r"^some .*avg60=([0-9.]+)", text, re.MULTILINE)
    return float(match.group(1)) if match else None


@dataclass(frozen=True)
class Listener:
    address: str
    port: int
    inode: str

    @property
    def loopback(self) -> bool:
        return self.address.startswith("127.") or self.address == "::1"


def _ipv4(hex_address: str) -> str:
    raw = bytes.fromhex(hex_address)
    return ".".join(str(b) for b in reversed(raw))


def _ipv6(hex_address: str) -> str:
    raw = bytes.fromhex(hex_address)
    words = [raw[i : i + 4][::-1] for i in range(0, 16, 4)]
    joined = b"".join(words)
    return socket.inet_ntop(socket.AF_INET6, joined)


def parse_listeners(tcp: str | None, tcp6: str | None) -> list[Listener]:
    """TCP sockets in the LISTEN state from ``/proc/net/tcp`` and ``tcp6``."""
    found: list[Listener] = []
    for text, decode in ((tcp, _ipv4), (tcp6, _ipv6)):
        for line in (text or "").splitlines()[1:]:
            parts = line.split()
            if len(parts) < 10 or parts[3] != "0A":
                continue
            address, _, port = parts[1].partition(":")
            try:
                found.append(Listener(decode(address), int(port, 16), parts[9]))
            except ValueError:
                continue
    return sorted(set(found), key=lambda item: (item.port, item.address))


def socket_owners(system: System) -> dict[str, str]:
    """Socket inode -> ``pid/command`` for the processes whose descriptors are visible
    (all processes when run as root, otherwise only the caller's)."""
    owners: dict[str, str] = {}
    proc = system.path("/proc")
    try:
        pids = [p for p in proc.iterdir() if p.name.isdigit()]
    except OSError:
        return owners
    for pid in pids:
        try:
            command = (pid / "comm").read_text(encoding="utf-8").strip()
            descriptors = list((pid / "fd").iterdir())
        except OSError:
            continue
        for descriptor in descriptors:
            try:
                target = str(descriptor.readlink())
            except OSError:
                continue
            if target.startswith("socket:["):
                owners[target[8:-1]] = f"{pid.name}/{command}"
    return owners


def largest_processes(system: System, count: int = 5) -> list[tuple[str, int]]:
    """The ``count`` processes with the largest resident memory (command, bytes)."""
    sizes: list[tuple[str, int]] = []
    try:
        pids = [p for p in system.path("/proc").iterdir() if p.name.isdigit()]
    except OSError:
        return sizes
    for pid in pids:
        try:
            status = (pid / "status").read_text(encoding="utf-8")
        except OSError:
            continue
        name = re.search(r"^Name:\s*(\S+)", status, re.MULTILINE)
        rss = re.search(r"^VmRSS:\s*(\d+)\s*kB", status, re.MULTILINE)
        if name and rss:
            sizes.append((name.group(1), int(rss.group(1)) * 1024))
    return sorted(sizes, key=lambda item: -item[1])[:count]


# --- Lock compatibility ------------------------------------------------------------------------


def _load_toml(text: str) -> dict[str, Any] | None:
    try:
        tomllib = importlib.import_module("tomllib")
    except ImportError:  # pragma: no cover - Python 3.10
        return None
    loaded: dict[str, Any] = tomllib.loads(text)
    return loaded


def runtime_packages(lock: Mapping[str, Any], project: str = "atlas") -> list[dict[str, Any]]:
    """The packages ``project`` needs at run time (its dependencies, transitively; the
    development group excluded), as recorded in ``uv.lock``."""
    packages = {p["name"]: p for p in lock.get("package", [])}
    pending = [d["name"] for d in packages.get(project, {}).get("dependencies", [])]
    seen: dict[str, dict[str, Any]] = {}
    while pending:
        name = pending.pop()
        if name in seen or name not in packages:
            continue
        seen[name] = packages[name]
        pending.extend(d["name"] for d in packages[name].get("dependencies", []))
    return [seen[name] for name in sorted(seen)]


_MANYLINUX_LEGACY = {"manylinux1": (2, 5), "manylinux2010": (2, 12), "manylinux2014": (2, 17)}


def wheel_supported(filename: str, machine: str, glibc: tuple[int, int], python: str) -> bool:
    """Whether a wheel file can be installed on this platform for CPython ``python``."""
    stem = filename.removesuffix(".whl")
    parts = stem.split("-")
    if len(parts) < 5:
        return False
    python_tags, abi_tags, platform_tags = parts[-3], parts[-2], parts[-1]
    major, minor = (int(x) for x in python.split("."))
    cpython = f"cp{major}{minor}"
    python_ok = False
    for tag in python_tags.split("."):
        if tag in (cpython, "py3", f"py{major}{minor}"):
            python_ok = True
        elif tag.startswith("cp3") and tag[3:].isdigit() and "abi3" in abi_tags.split("."):
            python_ok = python_ok or int(tag[3:]) <= minor
    if not python_ok:
        return False
    if any(abi.endswith("t") and abi.startswith("cp") for abi in abi_tags.split(".")):
        return False  # free-threaded build
    for tag in platform_tags.split("."):
        if tag == "any":
            return True
        legacy = next((v for k, v in _MANYLINUX_LEGACY.items() if tag == f"{k}_{machine}"), None)
        if legacy is not None and legacy <= glibc:
            return True
        match = re.fullmatch(r"manylinux_(\d+)_(\d+)_(\w+)", tag)
        if (
            match
            and match.group(3) == machine
            and (int(match.group(1)), int(match.group(2))) <= glibc
        ):
            return True
    return False


def lock_compatibility(
    lock: Mapping[str, Any], machine: str, glibc: tuple[int, int], python: str = PYTHON
) -> tuple[list[str], list[str]]:
    """(packages without a compatible wheel, packages that would be built from source).

    A package that publishes platform wheels has compiled code: without a wheel for this
    platform it cannot be installed in practice (building NumPy or SciPy needs compilers,
    Fortran and gigabytes of memory), so it is ``missing``. A package with only a source
    distribution would be built from source.
    """
    missing: list[str] = []
    from_source: list[str] = []
    for package in runtime_packages(lock):
        wheels = [Path(w.get("url", "")).name for w in package.get("wheels", [])]
        label = f"{package['name']} {package.get('version', '?')}"
        if any(wheel_supported(w, machine, glibc, python) for w in wheels):
            continue
        native = any(not w.removesuffix(".whl").endswith("-any") for w in wheels)
        if package.get("sdist") and not native:
            from_source.append(label)
        else:
            missing.append(label)
    return missing, from_source


def glibc_version() -> tuple[int, int] | None:
    try:
        text = os.confstr("CS_GNU_LIBC_VERSION") or ""
    except (ValueError, OSError):  # pragma: no cover - not glibc
        return None
    match = re.search(r"(\d+)\.(\d+)", text)
    return (int(match.group(1)), int(match.group(2))) if match else None


# --- Checks ----------------------------------------------------------------------------------


def _mib(value: float) -> str:
    return f"{value / MIB:,.0f} MiB"


def _gib(value: float) -> str:
    return f"{value / GIB:,.1f} GiB"


def check_os(system: System, report: Report) -> None:
    text = system.read("/etc/os-release")
    if text is None:
        report.add(
            Finding(
                "os",
                "distribution",
                FAIL,
                "/etc/os-release is missing",
                f"Ubuntu {TESTED_RELEASE} LTS",
                "Use an Ubuntu LTS server image.",
            )
        )
        return
    info = parse_os_release(text)
    name = info.get("PRETTY_NAME", info.get("ID", "unknown"))
    version = info.get("VERSION_ID", "")
    lts = "LTS" in info.get("VERSION", "") or "LTS" in name
    if info.get("ID") != "ubuntu":
        report.add(
            Finding(
                "os",
                "distribution",
                FAIL,
                name,
                f"Ubuntu {TESTED_RELEASE} LTS",
                "The deployment assets target Ubuntu LTS (apt packages, AppArmor, systemd "
                "defaults); reinstall the server with Ubuntu 24.04 LTS.",
            )
        )
    elif not lts:
        report.add(
            Finding(
                "os",
                "distribution",
                FAIL,
                name,
                "an LTS release",
                "Interim Ubuntu releases are supported for 9 months only; use 24.04 LTS.",
            )
        )
    elif version == TESTED_RELEASE:
        report.add(Finding("os", "distribution", PASS, name, f"Ubuntu {TESTED_RELEASE} LTS"))
    elif version in UNTESTED_LTS:
        report.add(
            Finding(
                "os",
                "distribution",
                WARN,
                name,
                f"Ubuntu {TESTED_RELEASE} LTS (tested)",
                f"Untested release: it does not ship Python {PYTHON}, so the build needs a "
                f"uv-managed Python {PYTHON} (see the operations guide). Prefer "
                f"{TESTED_RELEASE}.",
            )
        )
    else:
        report.add(
            Finding(
                "os",
                "distribution",
                FAIL,
                name,
                f"Ubuntu {TESTED_RELEASE} LTS",
                "This release is out of standard support or unknown; use 24.04 LTS.",
            )
        )
    report.add(Finding("os", "kernel", INFO, platform.release()))
    code, output = system.run("systemctl", "--version")
    match = re.search(r"systemd (\d+)", output) if code == 0 else None
    if match is None:
        report.add(
            Finding(
                "os",
                "systemd",
                FAIL,
                "systemctl is not available",
                "systemd 250 or later",
                "ATLAS is supervised by systemd; use a standard Ubuntu server (not a "
                "container without an init system).",
            )
        )
    elif int(match.group(1)) < 250:
        report.add(
            Finding(
                "os",
                "systemd",
                WARN,
                f"systemd {match.group(1)}",
                "systemd 250 or later",
                "Older systemd ignores some sandboxing and restart settings of the unit.",
            )
        )
    else:
        report.add(Finding("os", "systemd", PASS, f"systemd {match.group(1)}", "250 or later"))


def check_cpu(system: System, report: Report, profile: Profile) -> None:
    machine = system.machine
    if machine not in ARCHITECTURES:
        report.add(
            Finding(
                "cpu",
                "architecture",
                FAIL,
                machine,
                " or ".join(ARCHITECTURES),
                "The locked NumPy, SciPy and pandas wheels exist only for x86_64 and aarch64 "
                "Linux; choose such a server.",
            )
        )
    else:
        report.add(Finding("cpu", "architecture", PASS, machine, " or ".join(ARCHITECTURES)))
    if machine == "x86_64":
        flags = cpu_flags(system.read("/proc/cpuinfo") or "")
        missing = [flag for flag in X86_FLAGS if flag not in flags]
        if missing:
            report.add(
                Finding(
                    "cpu",
                    "instruction set",
                    FAIL,
                    "missing: " + ", ".join(missing),
                    "x86-64-v2 (" + ", ".join(X86_FLAGS) + ")",
                    "The locked NumPy refuses to import on this CPU. Many providers expose a "
                    "generic virtual CPU ('qemu64'); ask for the host CPU model or choose "
                    "another plan.",
                )
            )
        else:
            report.add(Finding("cpu", "instruction set", PASS, "x86-64-v2 available"))
    cpus = system.available_cpus()
    if cpus < profile.min_cpus:
        report.add(
            Finding(
                "cpu",
                "available CPUs",
                FAIL,
                str(cpus),
                f"at least {profile.min_cpus} for the {profile.name} profile",
                "Each simulation worker needs its own CPU to meet the simulation deadline; "
                "use --resources low (one worker) or a larger server.",
            )
        )
    else:
        report.add(
            Finding("cpu", "available CPUs", PASS, str(cpus), f"at least {profile.min_cpus}")
        )


def check_memory(system: System, report: Report, profile: Profile) -> dict[str, int]:
    info = parse_meminfo(system.read("/proc/meminfo") or "")
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", 0)
    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    need = deployment_memory_mib(profile.workers) * MIB
    if not total:
        report.add(Finding("memory", "total", FAIL, "/proc/meminfo unreadable", action="-"))
        return info
    if total < profile.min_memory_mib * MIB:
        report.add(
            Finding(
                "memory",
                "total",
                FAIL,
                _mib(total),
                f"at least {profile.min_memory_mib} MiB for the {profile.name} profile",
                "Use a server with more memory, or the low profile (one worker) on a 1 GB "
                "server with swap.",
            )
        )
    else:
        report.add(
            Finding("memory", "total", PASS, _mib(total), f"at least {profile.min_memory_mib} MiB")
        )
    headroom = max(100 * MIB, total // 10)
    if available < need + headroom:
        status = FAIL if available + swap_free < need + headroom else WARN
        report.add(
            Finding(
                "memory",
                "available",
                status,
                f"{_mib(available)} available now ({_mib(total - available)} in use)",
                f"{_mib(need)} for ATLAS plus {_mib(headroom)} headroom",
                "Stop or remove services competing for memory (see 'largest processes'), "
                "add swap, or use the low profile.",
            )
        )
    else:
        report.add(
            Finding(
                "memory",
                "available",
                PASS,
                f"{_mib(available)} available now",
                f"{_mib(need)} for ATLAS plus {_mib(headroom)} headroom",
            )
        )
    small = total < 2 * profile.recommended_swap_mib * MIB
    if swap_total == 0 and small:
        report.add(
            Finding(
                "memory",
                "swap",
                WARN,
                "no swap",
                f"at least {profile.recommended_swap_mib} MiB of swap below 2 GiB of memory",
                "Add a swap file so release builds and memory spikes do not invoke the OOM "
                "killer: fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap "
                "/swapfile && swapon /swapfile (and add it to /etc/fstab).",
            )
        )
    else:
        report.add(Finding("memory", "swap", PASS if swap_total else INFO, _mib(swap_total)))
    if available + swap_free < need + BUILD_PEAK_MIB * MIB:
        report.add(
            Finding(
                "memory",
                "release build",
                WARN,
                f"{_mib(available + swap_free)} available including swap",
                f"{_mib(need + BUILD_PEAK_MIB * MIB)} to build a release while ATLAS runs",
                "Build releases while the service is stopped, or add swap.",
            )
        )
    pressure = parse_pressure(system.read("/proc/pressure/memory") or "")
    if pressure is not None:
        report.add(
            Finding(
                "memory",
                "pressure (avg60)",
                WARN if pressure > 10 else PASS,
                f"{pressure:.1f} %",
                "below 10 %",
                "Processes are stalling on memory now; find the cause before deploying."
                if pressure > 10
                else "",
            )
        )
    return info


def check_storage(system: System, report: Report, profile: Profile) -> None:
    targets = (
        ("/opt/atlas", profile.min_free_opt_mib, "releases (about 330 MiB each, three kept)"),
        ("/var/cache/atlas", profile.min_free_var_mib, "the uv download cache and logs"),
        ("/var/log", 512, "the journal and nginx logs"),
        ("/tmp", 512, "temporary files of builds and the dependency probe"),  # noqa: S108
    )
    measured: dict[str, tuple[int, int]] = {}
    for target, minimum, purpose in targets:
        free, total, where = system.free_bytes(target)
        key = target if where == target else f"{target} (on {where})"
        # Several targets often share one filesystem: require the sum of their minimums.
        previous = measured.get(where)
        measured[where] = (free, (previous[1] if previous else 0) + minimum)
        required = measured[where][1]
        used_fraction = 1 - free / total if total else 1.0
        if free < required * MIB:
            status = FAIL
            action = f"Free disk space on {where} or use a larger disk."
        elif used_fraction > 0.85:
            status = WARN
            action = f"{where} is {used_fraction:.0%} full; plan for log and release growth."
        else:
            status, action = PASS, ""
        report.add(
            Finding(
                "storage",
                key,
                status,
                f"{_gib(free)} free of {_gib(total)}",
                f"at least {required} MiB free for {purpose}",
                action,
            )
        )


def check_pressure(system: System, report: Report) -> None:
    cpus = system.available_cpus()
    loadavg = (system.read("/proc/loadavg") or "").split()
    if len(loadavg) >= 3:
        five = float(loadavg[1])
        status = FAIL if five > 1.5 * cpus else WARN if five > 0.7 * cpus else PASS
        report.add(
            Finding(
                "processes",
                "load average (5 min)",
                status,
                f"{five:.2f} on {cpus} CPU(s)",
                f"below {0.7 * cpus:.1f}",
                ""
                if status == PASS
                else "The server is already busy; simulations would miss their deadline.",
            )
        )
    for name in ("cpu", "io"):
        value = parse_pressure(system.read(f"/proc/pressure/{name}") or "")
        if value is not None and value > 20:
            report.add(
                Finding(
                    "processes",
                    f"{name} pressure (avg60)",
                    WARN,
                    f"{value:.1f} %",
                    "below 20 %",
                    f"Processes are stalling on {name}; find the cause before deploying.",
                )
            )
    top = largest_processes(system)
    if top:
        report.add(
            Finding(
                "processes",
                "largest processes",
                INFO,
                ", ".join(f"{name} {_mib(size)}" for name, size in top),
            )
        )
    competing = []
    for unit in ("apache2", "docker", "containerd", "mysql", "mariadb", "postgresql", "snapd"):
        code, output = system.run("systemctl", "is-active", f"{unit}.service")
        if code == 0 and output.strip() == "active":
            competing.append(unit)
    if competing:
        report.add(
            Finding(
                "processes",
                "competing services",
                WARN,
                ", ".join(competing),
                "a server dedicated to ATLAS",
                "These services use memory and may hold ports 80/443; docker also bypasses "
                "ufw rules for published ports. Remove what this server does not need.",
            )
        )


def check_network(system: System, report: Report, offline: bool) -> None:
    listeners = parse_listeners(system.read("/proc/net/tcp"), system.read("/proc/net/tcp6"))
    owners = socket_owners(system)

    def owner(item: Listener) -> str:
        return owners.get(item.inode, "owner not visible (run as root)")

    api = [item for item in listeners if item.port == API_PORT]
    if api:
        named = ", ".join(f"{i.address}:{i.port} ({owner(i)})" for i in api)
        ours = all("atlas" in owner(i) for i in api)
        report.add(
            Finding(
                "network",
                f"API port {API_PORT}",
                INFO if ours else FAIL,
                named,
                f"{API_PORT} free for the ATLAS API (loopback only)",
                "" if ours else f"Stop the service on port {API_PORT}.",
            )
        )
    else:
        report.add(Finding("network", f"API port {API_PORT}", PASS, "free"))
    for port in PROXY_PORTS:
        held = [item for item in listeners if item.port == port]
        names = {owner(item) for item in held}
        if not held:
            report.add(Finding("network", f"port {port}", PASS, "free (nginx will listen)"))
        elif all("nginx" in name for name in names):
            report.add(
                Finding(
                    "network",
                    f"port {port}",
                    WARN,
                    "nginx already listens",
                    "no other site on this nginx, or a reviewed coexistence",
                    "ATLAS adds one nginx site with its own default server; review "
                    "/etc/nginx/sites-enabled for conflicts.",
                )
            )
        else:
            report.add(
                Finding(
                    "network",
                    f"port {port}",
                    FAIL,
                    ", ".join(sorted(names)),
                    f"port {port} for nginx",
                    f"Stop the service holding port {port}.",
                )
            )
    exposed = [
        f"{i.address}:{i.port} ({owner(i)})"
        for i in listeners
        if not i.loopback and i.port not in (22, *PROXY_PORTS)
    ]
    if exposed:
        report.add(
            Finding(
                "network",
                "other exposed listeners",
                WARN,
                ", ".join(exposed),
                "only SSH, HTTP and HTTPS reachable from outside",
                "Close these services or block them in the firewall (default deny incoming).",
            )
        )
    else:
        report.add(Finding("network", "other exposed listeners", PASS, "none"))
    if system.euid == 0:
        code, output = system.run("ufw", "status")
        active = code == 0 and "Status: active" in output
        report.add(
            Finding(
                "network",
                "firewall (ufw)",
                PASS if active else WARN,
                "active" if active else ("inactive" if code == 0 else output or "unavailable"),
                "default deny incoming; allow OpenSSH, 80 and 443 only",
                "" if active else "Configure ufw as described in the operations guide.",
            )
        )
    else:
        report.add(
            Finding("network", "firewall (ufw)", INFO, "not inspected (run as root to inspect)")
        )
    if offline:
        report.add(Finding("network", "outbound HTTPS", INFO, "not checked (--offline)"))
        return
    unreachable = [host for host in BUILD_HOSTS if not system.can_connect(host, 443)]
    report.add(
        Finding(
            "network",
            "outbound HTTPS",
            WARN if unreachable else PASS,
            ("unreachable: " + ", ".join(unreachable)) if unreachable else "reachable",
            "github.com, PyPI and Let's Encrypt over HTTPS for builds and certificates",
            "Allow outbound HTTPS (or build releases through a proxy)." if unreachable else "",
        )
    )


def _python(system: System) -> str | None:
    for candidate in (f"/usr/bin/python{PYTHON}", f"python{PYTHON}"):
        if candidate.startswith("/"):
            if system.path(candidate).exists():
                return candidate
        elif system.root == Path("/") and shutil.which(candidate):
            return candidate
    return None


def check_tools(system: System, report: Report, before_bootstrap: bool = False) -> str | None:
    """Python 3.12 with venv, git, openssl, nginx and uv. ``before_bootstrap``: the packages
    the bootstrap script installs are reported, not refused, when missing."""

    def missing(check: str, observed: str, requirement: str, package: str) -> None:
        if before_bootstrap:
            report.add(
                Finding("software", check, INFO, f"{observed} (the bootstrap installs {package})")
            )
        else:
            report.add(
                Finding(
                    "software",
                    check,
                    FAIL,
                    observed,
                    requirement,
                    f"apt install {package} (or run deploy/scripts/bootstrap-host.sh --apply)",
                )
            )

    python = _python(system)
    if python is None:
        missing(
            f"Python {PYTHON}",
            "not installed",
            f"Python {PYTHON} (the version CI tests and the lock targets)",
            f"python{PYTHON} python{PYTHON}-venv",
        )
    else:
        code, output = system.run(
            python, "-I", "-c", "import ensurepip, sys, venv; print(sys.version.split()[0])"
        )
        if code == 0:
            report.add(Finding("software", f"Python {PYTHON}", PASS, f"{python} {output}"))
        else:
            missing(
                f"Python {PYTHON}",
                f"{python} without venv/ensurepip",
                "venv and ensurepip (to install the hash-pinned uv)",
                f"python{PYTHON}-venv",
            )
    for tool, purpose in (
        ("git", "fetching the exact commit of a release"),
        ("openssl", "generating secrets and password hashes"),
        ("nginx", "the reverse proxy"),
    ):
        code, output = system.run(tool, "-v" if tool == "nginx" else "version")
        if code < 0:
            missing(tool, "not installed", f"{tool} for {purpose}", tool)
        else:
            report.add(Finding("software", tool, PASS, output.splitlines()[0] if output else "ok"))
    code, output = system.run("uv", "--version")
    report.add(
        Finding(
            "software",
            "uv",
            INFO,
            output if code == 0 else "not on PATH (the bootstrap installs a hash-pinned uv)",
        )
    )
    return python


def check_lock(system: System, report: Report, release_dir: Path | None) -> None:
    if release_dir is None:
        report.add(
            Finding(
                "dependencies",
                "locked wheels",
                INFO,
                "not checked (pass --release-dir pointing to an ATLAS checkout)",
            )
        )
        return
    try:
        text = (release_dir / "uv.lock").read_text(encoding="utf-8")
    except OSError:
        report.add(
            Finding(
                "dependencies",
                "locked wheels",
                FAIL,
                f"{release_dir / 'uv.lock'} cannot be read",
                "an ATLAS checkout with uv.lock",
                "Pass --release-dir pointing to a checkout of the repository.",
            )
        )
        return
    lock = _load_toml(text)
    glibc = glibc_version() if system.root == Path("/") else (2, 39)
    if lock is None or glibc is None:
        report.add(
            Finding(
                "dependencies",
                "locked wheels",
                WARN,
                "cannot be checked here (needs Python 3.11+ and glibc)",
                action="Run the preflight with Python 3.12, or use --probe-dependencies.",
            )
        )
        return
    missing, from_source = lock_compatibility(lock, system.machine, glibc)
    count = len(runtime_packages(lock))
    if missing:
        report.add(
            Finding(
                "dependencies",
                "locked wheels",
                FAIL,
                "no wheel for this platform: " + ", ".join(missing),
                f"a compatible wheel of every runtime package ({system.machine}, glibc "
                f"{glibc[0]}.{glibc[1]}, CPython {PYTHON})",
                "The locked dependencies cannot be installed here; use x86_64 or aarch64 "
                "Ubuntu 24.04.",
            )
        )
    elif from_source:
        report.add(
            Finding(
                "dependencies",
                "locked wheels",
                WARN,
                "would be built from source: " + ", ".join(from_source),
                "wheels only",
                "Building needs compilers and much more memory; prefer a supported platform.",
            )
        )
    else:
        report.add(
            Finding(
                "dependencies",
                "locked wheels",
                PASS,
                f"all {count} runtime packages have a wheel for {system.machine}, glibc "
                f"{glibc[0]}.{glibc[1]}, CPython {PYTHON}",
            )
        )


PROBE_CODE = """\
import resource, time
started = time.perf_counter()
import numpy, pandas, scipy, fastapi, uvicorn, pydantic
from scipy.optimize import linprog
result = linprog([1.0, 2.0], A_ub=[[-1.0, -1.0]], b_ub=[-1.0], method="highs")
assert result.status == 0 and abs(result.fun - 1.0) < 1e-9, result
print(round(time.perf_counter() - started, 3), resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
"""


def probe_dependencies(
    report: Report, python: str | None, release_dir: Path | None, work_dir: Path | None
) -> None:
    """Install the locked runtime dependencies into a throw-away directory and import them.

    Uses the hash-pinned uv of ``.github/ci-requirements.txt`` (installed into the same
    throw-away directory). Everything is deleted afterwards; nothing outside it changes.
    """
    if python is None or release_dir is None:
        report.add(
            Finding(
                "dependencies",
                "probe",
                FAIL,
                "needs Python 3.12 and --release-dir",
                action="Install python3.12-venv and pass --release-dir.",
            )
        )
        return
    started = time.perf_counter()
    release_dir = release_dir.resolve()  # the commands below run in the scratch directory
    with tempfile.TemporaryDirectory(prefix="atlas-preflight-", dir=work_dir) as scratch:
        base = Path(scratch)
        project = base / "project"
        project.mkdir()
        for name in ("pyproject.toml", "uv.lock", ".python-version"):
            shutil.copy2(release_dir / name, project / name)
        tools = base / "tools"
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(base),
            "UV_CACHE_DIR": str(base / "cache"),
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_NO_PROGRESS": "1",
        }
        for name in (
            "HTTPS_PROXY",
            "https_proxy",
            "SSL_CERT_FILE",
            "PIP_CERT",
            "REQUESTS_CA_BUNDLE",
            "UV_NATIVE_TLS",
        ):
            if name in os.environ:
                env[name] = os.environ[name]
        steps: list[list[str]] = [
            [python, "-I", "-m", "venv", str(tools)],
            [
                str(tools / "bin" / "python"),
                "-I",
                "-m",
                "pip",
                "install",
                "--quiet",
                "--disable-pip-version-check",
                "--require-hashes",
                "-r",
                str(release_dir / ".github" / "ci-requirements.txt"),
            ],
            [
                str(tools / "bin" / "uv"),
                "sync",
                "--locked",
                "--no-dev",
                "--no-install-project",
                "--python",
                python if python.startswith("/") else shutil.which(python) or python,
                "--project",
                str(project),
            ],
            [str(project / ".venv" / "bin" / "python"), "-I", "-c", PROBE_CODE],
        ]
        output = ""
        for command in steps:
            try:
                completed = subprocess.run(  # noqa: S603 - fixed commands in a scratch dir
                    command,
                    capture_output=True,
                    text=True,
                    timeout=900,
                    check=False,
                    env=env,
                    cwd=project,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                report.add(
                    Finding(
                        "dependencies",
                        "probe",
                        FAIL,
                        f"{Path(command[0]).name}: {type(exc).__name__}",
                        "the locked runtime dependencies install and import",
                        "Check outbound HTTPS to PyPI and the free space in the temp dir.",
                    )
                )
                return
            output = (completed.stdout + completed.stderr).strip()
            if completed.returncode != 0:
                report.add(
                    Finding(
                        "dependencies",
                        "probe",
                        FAIL,
                        f"{Path(command[0]).name} failed: {output[-400:]}",
                        "the locked runtime dependencies install and import",
                        "Read the error: a CPU without x86-64-v2, a missing python3.12-venv "
                        "or no access to PyPI are the usual causes.",
                    )
                )
                return
    seconds, rss_kib = output.split()[-2:]
    report.add(
        Finding(
            "dependencies",
            "probe",
            PASS,
            f"installed and imported in {time.perf_counter() - started:.0f} s; an LP solved "
            f"with HiGHS; import time {float(seconds):.1f} s, peak memory "
            f"{int(rss_kib) // 1024} MiB",
            "the locked runtime dependencies install and import",
        )
    )


def check_security(system: System, report: Report) -> None:
    auto = system.read("/etc/apt/apt.conf.d/20auto-upgrades") or ""
    enabled = re.search(r'Unattended-Upgrade\s+"1"', auto) is not None
    report.add(
        Finding(
            "security",
            "unattended security upgrades",
            PASS if enabled else WARN,
            "enabled" if enabled else "not enabled",
            "automatic security updates",
            ""
            if enabled
            else "apt install unattended-upgrades && dpkg-reconfigure -plow unattended-upgrades",
        )
    )
    if system.euid == 0:
        code, output = system.run("sshd", "-T")
        if code == 0:
            settings = dict(
                line.split(" ", 1) for line in output.lower().splitlines() if " " in line
            )
            weak = [
                f"{name} {settings.get(name)}"
                for name, safe in (
                    ("passwordauthentication", ("no",)),
                    ("permitrootlogin", ("no", "prohibit-password", "without-password")),
                    ("kbdinteractiveauthentication", ("no",)),
                )
                if settings.get(name) not in safe
            ]
            report.add(
                Finding(
                    "security",
                    "ssh",
                    WARN if weak else PASS,
                    ", ".join(weak) if weak else "key-only authentication",
                    "key-only SSH, no password or root password login",
                    "Set PasswordAuthentication no, KbdInteractiveAuthentication no and "
                    "PermitRootLogin no in /etc/ssh/sshd_config.d/ (keep a working key "
                    "session open while testing)."
                    if weak
                    else "",
                )
            )
    else:
        report.add(Finding("security", "ssh", INFO, "not inspected (run as root to inspect)"))
    code, output = system.run("timedatectl", "show", "-p", "NTPSynchronized", "--value")
    synced = code == 0 and output.strip() == "yes"
    report.add(
        Finding(
            "security",
            "clock synchronised",
            PASS if synced else WARN,
            (output.strip().splitlines() or ["unknown"])[0],
            "NTP synchronised (TLS certificates and logs depend on the clock)",
            "" if synced else "timedatectl set-ntp true",
        )
    )
    existing = system.path("/opt/atlas").exists()
    report.add(
        Finding(
            "security",
            "existing ATLAS installation",
            INFO,
            "found at /opt/atlas (the bootstrap is idempotent)" if existing else "none",
        )
    )


def estimate(profile: Profile, meminfo: Mapping[str, int]) -> dict[str, Any]:
    workers = profile.workers
    total = meminfo.get("MemTotal", 0)
    in_use = total - meminfo.get("MemAvailable", 0) if total else None
    return {
        "unit": "MiB",
        "workers": workers,
        "components": {
            "operating_system_in_use_now": None if in_use is None else in_use // MIB,
            "operating_system_typical": TYPICAL_OS_MIB,
            "reverse_proxy": MEMORY_MIB["reverse_proxy"],
            "api_server": MEMORY_MIB["api_server"],
            "simulation_workers": workers * MEMORY_MIB["simulation_worker"],
            "resource_tracker": MEMORY_MIB["resource_tracker"],
            "monitor_transient": MEMORY_MIB["monitor"],
        },
        "atlas_service": service_memory_mib(workers),
        "atlas_total": deployment_memory_mib(workers),
        "release_build_peak_extra": BUILD_PEAK_MIB,
        "recommended_profile": recommend(meminfo.get("MemTotal", 0)),
    }


def recommend(total_bytes: int, cpus: int | None = None) -> str:
    standard = PROFILES["standard"]
    enough_cpus = cpus is None or cpus >= standard.min_cpus
    return "standard" if total_bytes >= standard.min_memory_mib * MIB and enough_cpus else "low"


def assess(
    system: System,
    profile_name: str = "standard",
    *,
    release_dir: Path | None = None,
    offline: bool = False,
    probe: bool = False,
    work_dir: Path | None = None,
    before_bootstrap: bool = False,
) -> Report:
    """Run every check (read-only) and return the report."""
    profile = PROFILES[profile_name]
    report = Report(profile=profile.name)
    check_os(system, report)
    check_cpu(system, report, profile)
    meminfo = check_memory(system, report, profile)
    check_storage(system, report, profile)
    check_pressure(system, report)
    check_network(system, report, offline)
    python = check_tools(system, report, before_bootstrap)
    check_lock(system, report, release_dir)
    if probe:
        probe_dependencies(report, python, release_dir, work_dir)
    check_security(system, report)
    report.estimate = estimate(profile, meminfo)
    report.estimate["recommended_profile"] = recommend(
        meminfo.get("MemTotal", 0), system.available_cpus()
    )
    return report


def render(report: Report) -> str:
    lines = [
        f"ATLAS preflight (profile: {report.profile}): {report.verdict}",
        "",
    ]
    width = max((len(f"{f.area}: {f.check}") for f in report.findings), default=10)
    for finding in sorted(report.findings, key=lambda f: -_ORDER[f.status]):
        label = f"{finding.area}: {finding.check}".ljust(width)
        lines.append(f"[{finding.status.upper():4}] {label}  {finding.observed}")
        if finding.status in (WARN, FAIL):
            if finding.requirement:
                lines.append(f"       {'':{width}}  required: {finding.requirement}")
            if finding.action:
                lines.append(f"       {'':{width}}  action:   {finding.action}")
    est = report.estimate
    components = est.get("components", {})
    lines += [
        "",
        f"Memory estimate (MiB) with {est.get('workers')} simulation worker(s):",
        *(f"  {name:32} {value}" for name, value in components.items() if value is not None),
        f"  {'ATLAS service (systemd unit)':32} {est.get('atlas_service')}",
        f"  {'ATLAS total (with nginx, monitor)':32} {est.get('atlas_total')}",
        f"  {'extra during a release build':32} {est.get('release_build_peak_extra')}",
        f"Recommended profile for this server: {est.get('recommended_profile')}",
    ]
    if report.verdict == UNSUITABLE:
        lines += ["", "UNSUITABLE: resolve every [FAIL] finding before deploying ATLAS here."]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-preflight",
        description="Read-only assessment of a server for an ATLAS deployment.",
    )
    parser.add_argument("--resources", choices=sorted(PROFILES), default="standard")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=None,
        help="An ATLAS checkout whose uv.lock is checked against this platform.",
    )
    parser.add_argument(
        "--probe-dependencies",
        action="store_true",
        help="Install the locked runtime dependencies in a temporary directory and import "
        "them (downloads about 120 MB; the directory is deleted).",
    )
    parser.add_argument("--work-dir", type=Path, default=None, help="Parent of the probe dir.")
    parser.add_argument("--offline", action="store_true", help="Skip outbound HTTPS checks.")
    parser.add_argument(
        "--before-bootstrap",
        action="store_true",
        help="Report (not refuse) missing packages that the bootstrap script installs.",
    )
    return parser


def main(argv: Iterable[str] | None = None, system: System | None = None) -> int:
    args = build_parser().parse_args(None if argv is None else list(argv))
    report = assess(
        system or System(),
        args.resources,
        release_dir=args.release_dir,
        offline=args.offline,
        probe=args.probe_dependencies,
        work_dir=args.work_dir,
        before_bootstrap=args.before_bootstrap,
    )
    if args.json:
        sys.stdout.write(json.dumps(report.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(render(report))
    return 1 if report.verdict == UNSUITABLE else 0


if __name__ == "__main__":  # pragma: no cover - the standalone entry point
    raise SystemExit(main())
