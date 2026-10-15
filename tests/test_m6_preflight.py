"""The read-only VPS preflight (M6): it accepts a suitable server and refuses an unsuitable
one with an actionable explanation; it changes nothing and needs only the standard library.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from atlas.deploy import preflight
from atlas.deploy.preflight import (
    FAIL,
    INFO,
    PASS,
    SUITABLE,
    SUITABLE_WITH_WARNINGS,
    UNSUITABLE,
    WARN,
    Report,
    assess,
    lock_compatibility,
    parse_listeners,
    runtime_packages,
    wheel_supported,
)
from tests.conftest import REPO_ROOT
from tests.m6_helpers import TCP_HEADER, fake_server, run, tcp_line

PREFLIGHT = REPO_ROOT / "src" / "atlas" / "deploy" / "preflight.py"


def finding(report: Report, check: str) -> preflight.Finding:
    (found,) = [f for f in report.findings if f.check == check]
    return found


def failing(report: Report) -> list[str]:
    return [f.check for f in report.findings if f.status == FAIL]


def test_a_suitable_server_passes(tmp_path: Path) -> None:
    report = assess(fake_server(tmp_path), "standard", release_dir=REPO_ROOT)
    assert report.verdict == SUITABLE, [f for f in report.findings if f.status != PASS]
    assert finding(report, "distribution").status == PASS
    assert finding(report, "locked wheels").status == PASS
    assert report.estimate["recommended_profile"] == "standard"
    assert report.estimate["atlas_service"] == 190 + 2 * 135 + 15


@pytest.mark.parametrize(
    ("os_release", "expected"),
    [
        ('ID=debian\nPRETTY_NAME="Debian 12"\nVERSION_ID="12"\n', FAIL),
        ('ID=ubuntu\nPRETTY_NAME="Ubuntu 25.04"\nVERSION_ID="25.04"\nVERSION="25.04"\n', FAIL),
        (
            'ID=ubuntu\nPRETTY_NAME="Ubuntu 20.04 LTS"\nVERSION_ID="20.04"\nVERSION="20.04 LTS"\n',
            FAIL,
        ),
        (
            'ID=ubuntu\nPRETTY_NAME="Ubuntu 26.04 LTS"\nVERSION_ID="26.04"\nVERSION="26.04 LTS"\n',
            WARN,
        ),
    ],
)
def test_distributions(tmp_path: Path, os_release: str, expected: str) -> None:
    report = assess(fake_server(tmp_path, os_release=os_release), "standard")
    found = finding(report, "distribution")
    assert found.status == expected
    assert found.action  # every refusal says what to do


def test_missing_os_release_is_refused(tmp_path: Path) -> None:
    system = fake_server(tmp_path)
    (system.root / "etc/os-release").unlink()
    assert finding(assess(system), "distribution").status == FAIL


def test_unsupported_architecture_is_refused(tmp_path: Path) -> None:
    report = assess(fake_server(tmp_path, machine="armv7l"), "low", release_dir=REPO_ROOT)
    assert report.verdict == UNSUITABLE
    assert "architecture" in failing(report)
    assert "locked wheels" in failing(report)  # no wheel of NumPy etc. for armv7l


def test_a_cpu_without_x86_64_v2_is_refused(tmp_path: Path) -> None:
    qemu64 = "processor\t: 0\nflags\t\t: fpu sse sse2 ssse3\n"
    report = assess(fake_server(tmp_path, cpuinfo=qemu64), "low")
    found = finding(report, "instruction set")
    assert found.status == FAIL
    assert "sse4_2" in found.observed
    assert "qemu64" in found.action


def test_resource_profiles(tmp_path: Path) -> None:
    small = {"total_mib": 961, "available_mib": 700, "swap_mib": 1024, "cpus": 1}
    standard = assess(fake_server(tmp_path / "a", **small), "standard")
    assert standard.verdict == UNSUITABLE
    assert {"available CPUs", "total"} <= set(failing(standard))
    low = assess(fake_server(tmp_path / "b", **small), "low")
    assert low.verdict in (SUITABLE, SUITABLE_WITH_WARNINGS), failing(low)
    assert low.estimate["recommended_profile"] == "low"
    assert low.estimate["workers"] == 1


def test_memory_is_checked_against_the_estimate(tmp_path: Path) -> None:
    busy = assess(fake_server(tmp_path / "a", available_mib=300, swap_mib=0), "standard")
    assert finding(busy, "available").status == FAIL
    swapping = assess(fake_server(tmp_path / "b", available_mib=500, swap_mib=1024), "standard")
    assert finding(swapping, "available").status == WARN


def test_no_swap_on_a_small_server_is_a_warning(tmp_path: Path) -> None:
    report = assess(
        fake_server(tmp_path, total_mib=961, available_mib=750, swap_mib=0, cpus=1), "low"
    )
    swap = finding(report, "swap")
    assert swap.status == WARN
    assert "swapfile" in swap.action
    assert finding(report, "release build").status == WARN


def test_storage(tmp_path: Path) -> None:
    full = assess(fake_server(tmp_path / "a", free_gib=1.0, total_gib=10.0), "standard")
    assert any(f.area == "storage" and f.status == FAIL for f in full.findings)
    tight = assess(fake_server(tmp_path / "b", free_gib=5.0, total_gib=40.0), "standard")
    assert any(f.area == "storage" and f.status == WARN for f in tight.findings)


def test_ports_held_by_other_services_are_refused(tmp_path: Path) -> None:
    tcp = tcp_line(0, "0.0.0.0", 80, 101) + tcp_line(1, "127.0.0.1", 8000, 102)  # noqa: S104
    report = assess(fake_server(tmp_path, tcp=tcp), "standard")
    assert {"port 80", "API port 8000"} <= set(failing(report))
    assert finding(report, "port 443").status == PASS


def test_exposed_listeners_are_reported(tmp_path: Path) -> None:
    tcp = tcp_line(0, "0.0.0.0", 3306, 201) + tcp_line(1, "0.0.0.0", 22, 202)  # noqa: S104
    report = assess(fake_server(tmp_path, tcp=tcp), "standard")
    found = finding(report, "other exposed listeners")
    assert found.status == WARN
    assert "3306" in found.observed
    assert ":22 " not in found.observed


def test_listener_parsing() -> None:
    tcp = TCP_HEADER + tcp_line(0, "127.0.0.1", 8000, 5) + tcp_line(1, "0.0.0.0", 80, 6)  # noqa: S104
    tcp += tcp_line(2, "10.0.0.5", 5432, 7, state="01")  # established: not a listener
    tcp6 = TCP_HEADER + (
        "   0: 00000000000000000000000001000000:01BB 00000000000000000000000000000000:0000 0A "
        "00000000:00000000 00:00000000 00000000     0        0 8 1 0 100 0 0 10 0\n"
    )
    listeners = parse_listeners(tcp, tcp6)
    assert [(item.address, item.port) for item in listeners] == [
        ("0.0.0.0", 80),  # noqa: S104
        ("::1", 443),
        ("127.0.0.1", 8000),
    ]
    assert [item.loopback for item in listeners] == [False, True, True]


def test_missing_software(tmp_path: Path) -> None:
    commands = {"/usr/bin/python3.12": (1, "No module named 'ensurepip'"), "git version": (-1, "")}
    report = assess(fake_server(tmp_path / "a", commands=commands), "standard")
    assert {"Python 3.12", "git"} <= set(failing(report))
    assert "python3.12-venv" in finding(report, "Python 3.12").action
    before = assess(
        fake_server(tmp_path / "b", commands=commands), "standard", before_bootstrap=True
    )
    assert finding(before, "Python 3.12").status == INFO
    assert finding(before, "git").status == INFO
    assert before.verdict != UNSUITABLE


def test_systemd_and_security_posture(tmp_path: Path) -> None:
    commands = {
        "systemctl --version": (0, "systemd 245"),
        "ufw status": (0, "Status: inactive"),
        "sshd -T": (0, "passwordauthentication yes\npermitrootlogin yes\n"),
        "timedatectl show": (0, "no"),
    }
    report = assess(fake_server(tmp_path, commands=commands), "standard")
    assert finding(report, "systemd").status == WARN
    assert finding(report, "firewall (ufw)").status == WARN
    ssh = finding(report, "ssh")
    assert ssh.status == WARN
    assert "passwordauthentication yes" in ssh.observed
    assert finding(report, "clock synchronised").status == WARN
    no_systemd = assess(fake_server(tmp_path / "b", commands={"systemctl --version": (-1, "")}))
    assert finding(no_systemd, "systemd").status == FAIL


def test_unprivileged_runs_say_what_they_could_not_inspect(tmp_path: Path) -> None:
    report = assess(fake_server(tmp_path, euid=1000), "standard")
    assert finding(report, "firewall (ufw)").status == INFO
    assert "root" in finding(report, "ssh").observed


def test_competing_services_and_load(tmp_path: Path) -> None:
    commands = {"systemctl is-active docker.service": (0, "active")}
    report = assess(
        fake_server(tmp_path, commands=commands, loadavg="9.0 9.0 9.0 1/1 1\n"), "standard"
    )
    assert "docker" in finding(report, "competing services").observed
    assert finding(report, "load average (5 min)").status == FAIL


def test_outbound_access(tmp_path: Path) -> None:
    offline = assess(fake_server(tmp_path / "a", reachable=False), "standard")
    assert finding(offline, "outbound HTTPS").status == WARN
    skipped = assess(fake_server(tmp_path / "b"), "standard", offline=True)
    assert finding(skipped, "outbound HTTPS").status == INFO


# --- The lock ---------------------------------------------------------------------------------


def test_runtime_packages_exclude_the_development_group() -> None:
    lock = preflight._load_toml((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert lock is not None
    names = {p["name"] for p in runtime_packages(lock)}
    assert {"numpy", "scipy", "pandas", "fastapi", "uvicorn", "pydantic-core"} <= names
    assert not names & {"pytest", "mypy", "ruff", "hypothesis", "httpx2", "pandas-stubs"}


@pytest.mark.parametrize(
    ("machine", "glibc", "compatible"),
    [("x86_64", (2, 39), True), ("aarch64", (2, 39), True), ("x86_64", (2, 17), False)],
)
def test_lock_compatibility(machine: str, glibc: tuple[int, int], compatible: bool) -> None:
    lock = preflight._load_toml((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert lock is not None
    missing, from_source = lock_compatibility(lock, machine, glibc)
    assert (not missing and not from_source) is compatible


@pytest.mark.parametrize(
    ("filename", "supported"),
    [
        ("numpy-2.5.4-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl", True),
        ("numpy-2.5.4-cp313-cp313-manylinux_2_27_x86_64.whl", False),
        ("numpy-2.5.4-cp312-cp312-musllinux_1_2_x86_64.whl", False),
        ("numpy-2.5.4-cp312-cp312-win_amd64.whl", False),
        ("x-1.0-cp313-cp313t-manylinux_2_17_x86_64.whl", False),
        ("x-1.0-cp39-abi3-manylinux2014_x86_64.whl", True),
        ("x-1.0-py3-none-any.whl", True),
        ("x-1.0-cp312-cp312-manylinux_2_40_x86_64.whl", False),
        ("broken.whl", False),
    ],
)
def test_wheel_tags(filename: str, supported: bool) -> None:
    assert wheel_supported(filename, "x86_64", (2, 39), "3.12") is supported


def test_an_unreadable_lock_is_refused(tmp_path: Path) -> None:
    report = assess(fake_server(tmp_path), "standard", release_dir=tmp_path / "nothing")
    assert finding(report, "locked wheels").status == FAIL
    unchecked = assess(fake_server(tmp_path / "b"), "standard")
    assert finding(unchecked, "locked wheels").status == INFO


def test_the_probe_reports_what_it_needs(tmp_path: Path) -> None:
    report = Report(profile="standard")
    preflight.probe_dependencies(report, None, None, tmp_path)
    assert report.findings[0].status == FAIL


# --- Output and entry points ---------------------------------------------------------------------


def test_text_and_json_output(tmp_path: Path) -> None:
    system = fake_server(tmp_path, available_mib=200, swap_mib=0)
    out = io.StringIO()
    with redirect_stdout(out):
        code = preflight.main(["--resources", "standard", "--offline"], system=system)
    assert code == 1
    text = out.getvalue()
    assert "UNSUITABLE" in text
    assert "action:" in text
    assert "Memory estimate" in text
    out = io.StringIO()
    with redirect_stdout(out):
        code = preflight.main(["--json", "--resources", "low"], system=fake_server(tmp_path / "b"))
    payload = json.loads(out.getvalue())
    assert code == 0
    assert payload["verdict"] in (SUITABLE, SUITABLE_WITH_WARNINGS)
    assert payload["estimate"]["unit"] == "MiB"


def test_the_preflight_runs_standalone_with_the_standard_library_only() -> None:
    """``python3 -I preflight.py``: no ATLAS install, no dependency, no site-packages."""
    result = run([sys.executable, "-I", "-S", str(PREFLIGHT), "--offline", "--json"])
    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)
    assert payload["verdict"] in (SUITABLE, SUITABLE_WITH_WARNINGS, UNSUITABLE)
    assert {f["area"] for f in payload["findings"]} >= {"os", "cpu", "memory", "storage"}


def test_the_preflight_on_this_machine_changes_nothing(tmp_path: Path) -> None:
    """A real run (read-only): the repository is unchanged and nothing is created."""
    from tests.m5_helpers import tree_hashes

    before = tree_hashes(REPO_ROOT / "docs", REPO_ROOT / "src", REPO_ROOT / "deploy")
    report = assess(preflight.System(), "low", release_dir=REPO_ROOT, offline=True)
    assert report.findings
    assert tree_hashes(REPO_ROOT / "docs", REPO_ROOT / "src", REPO_ROOT / "deploy") == before
