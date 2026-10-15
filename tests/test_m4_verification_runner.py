"""Regression tests for the verification of the round-3 fixes: run integrity.

Each test pins one fix of commit ce2b104 (findings r4-run-integrity-02 to -08, r4-suite-01 and
the open part of round-3 run-integrity-05) and fails when that fix is reverted:
- what ``manifest_field_differs`` names (the recomputed or the recorded ``metrics.json``), and
  no manifest blame when ``metrics.json`` itself was altered on disk;
- the manifest copies of data, solver, run id and result files, and the embedded
  configuration, cross-checked by inspect and verify;
- a symlinked ``src/atlas`` or ``src`` never counts as the repository's package;
- an integer beyond Python's digit limit, an output name with a lone surrogate and a recorded
  digest that is not a string are usage errors with their own message;
- a foreign package is a WARNING of ``optimize run`` and of ``inspect``;
- the ``git hash-object`` acceptance path of the ``--require-clean`` blob check;
- a ``metrics.json`` scenario id that is a list or an object is an ERROR, never exit 3;
- verify reports the recomputation's stage fallbacks.

Every command test uses ``configs/optimization/smoke.json``. As in
tests/test_m4_round3_runner.py, the smoke computation is made once per module and reused by
every run and verify (the computation is deterministic; tests/test_m4_runner_cli.py checks
that verify reproduces every file byte for byte); tests that tamper work on a copy.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import atlas
import atlas.decision_analysis as da
import atlas.optimization_runner as runner
from atlas.cli import EXIT_FAILED, EXIT_OK, EXIT_USAGE, main
from atlas.errors import ConfigError
from atlas.experiment import result_fingerprint
from atlas.optimization_runner import (
    RESULT_FILES,
    _require_committed_config,
    running_package_is_repository,
)
from tests.conftest import REPO_ROOT

SMOKE = REPO_ROOT / "configs" / "optimization" / "smoke.json"
ZEROS = "0" * 64

# --- Helpers ---------------------------------------------------------------------------------

_REAL_M4_OUTPUTS = runner.m4_outputs
_RECOMPUTED: dict[str, Any] = {}


def _memoised_m4_outputs(settings: Any, config: Any, inputs: Any) -> Any:
    key = config.fingerprint()
    if key not in _RECOMPUTED:
        _RECOMPUTED[key] = _REAL_M4_OUTPUTS(settings, config, inputs)
    return _RECOMPUTED[key]


@pytest.fixture(autouse=True)
def _computed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "m4_outputs", _memoised_m4_outputs)


def run_cli(
    *args: str, output_format: str = "json", repo_root: Path = REPO_ROOT
) -> tuple[int, str]:
    buffer = io.StringIO()
    code = main(["--repo-root", str(repo_root), "--format", output_format, *args], stdout=buffer)
    return code, buffer.getvalue()


def report_of(output: str, name: str) -> dict[str, Any]:
    reports: list[dict[str, Any]] = json.loads(output)["reports"]
    return next(r for r in reports if r["name"] == name)


def codes(report: dict[str, Any], severity: str) -> list[str]:
    return [i["code"] for i in report["issues"] if i["severity"] == severity]


def messages(report: dict[str, Any], code: str) -> list[str]:
    return [i["message"] for i in report["issues"] if i["code"] == code]


def issue(report: dict[str, Any], code: str) -> dict[str, Any]:
    return next(i for i in report["issues"] if i["code"] == code)


def text_issues(output: str, name: str) -> list[tuple[str, str, str]]:
    """(severity, subject, code) of every issue line in the text block of report ``name``."""
    block = re.search(rf"^\[(?:PASS|FAIL)\] {name}:.*?(?=^\[|\Z)", output, re.MULTILINE | re.DOTALL)
    assert block is not None, name
    found: list[tuple[str, str, str]] = re.findall(
        r"^  (ERROR|WARNING|INFO) +(.+?) \[([a-z_]+)\]", block.group(0), re.MULTILINE
    )
    return found


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def edit_json(path: Path, change: Callable[[dict[str, Any]], object]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    change(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def rehash_metrics(run_dir: Path) -> None:
    """Make the manifest record the (edited) metrics.json: a consistent run record."""

    def rehash(manifest: dict[str, Any]) -> None:
        manifest["outputs"]["metrics.json"] = sha256(run_dir / "metrics.json")
        manifest["result_fingerprint"] = result_fingerprint(manifest["outputs"], RESULT_FILES)

    edit_json(run_dir / "manifest.json", rehash)


def differs(key: str, against: str) -> str:
    return f"The manifest's {key!r} differs from the {against} metrics.json."


@dataclass(frozen=True)
class SmokeRun:
    directory: Path

    def manifest(self) -> dict[str, Any]:
        payload: dict[str, Any] = json.loads(
            (self.directory / "manifest.json").read_text(encoding="utf-8")
        )
        return payload

    def copy(self, target: Path) -> Path:
        shutil.copytree(self.directory, target)
        return target


@pytest.fixture(scope="module")
def smoke_run(tmp_path_factory: pytest.TempPathFactory) -> SmokeRun:
    directory = tmp_path_factory.mktemp("m4v") / "smoke"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runner, "m4_outputs", _memoised_m4_outputs)
        code, output = run_cli(
            "optimize", "run", "--config", str(SMOKE), "--output-dir", str(directory)
        )
    assert code == EXIT_OK, output
    # The control for r4-run-integrity-06: the repository's own package gives no warning.
    assert "running_package_not_repository" not in codes(
        report_of(output, "optimization"), "warning"
    )
    return SmokeRun(directory)


def _forbid_computation(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("nothing may be computed before the refusal")

    monkeypatch.setattr(runner, "load_series", explode)
    monkeypatch.setattr(runner, "m4_outputs", explode)


GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=0"]


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(  # noqa: S603
        [*GIT, "-C", str(root), *args], check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def git_blob(root: Path, spec: str) -> bytes:
    completed = subprocess.run(  # noqa: S603
        [*GIT, "-C", str(root), "cat-file", "blob", spec], check=True, capture_output=True
    )
    return completed.stdout


# --- r4-run-integrity-02: manifest_field_differs names what it compares with --------------------


@pytest.mark.integration
def test_verify_names_the_recomputed_metrics_json_for_a_consistent_run_of_other_code(
    smoke_run: SmokeRun, tmp_path: Path
) -> None:
    # Pins r4-run-integrity-02 (verify): a consistent run whose label came from other code
    # (manifest and metrics.json agree) was reported as "differs from the fingerprinted
    # metrics.json", blaming an edit of the manifest; verify compares with the recomputation.
    run_dir = smoke_run.copy(tmp_path / "run")
    label = "HYPOTHETICAL DECISION SIMULATION (reworded by another commit)"
    edit_json(run_dir / "metrics.json", lambda m: m.__setitem__("label", label))
    edit_json(run_dir / "manifest.json", lambda m: m.__setitem__("label", label))
    rehash_metrics(run_dir)
    code, output = run_cli("optimize", "inspect", str(run_dir))
    assert code == EXIT_OK  # the record itself is consistent
    assert codes(report_of(output, "optimization_inspection"), "error") == []
    code, output = run_cli("optimize", "verify", str(run_dir))
    assert code == EXIT_FAILED
    report = report_of(output, "optimization_verification")
    assert sorted(codes(report, "error")) == [
        "manifest_field_differs",
        "output_differs",
        "result_fingerprint_differs",
    ]
    assert messages(report, "manifest_field_differs") == [differs("label", "recomputed")]
    assert issue(report, "output_differs")["subject"] == "metrics.json"


@pytest.mark.integration
def test_inspect_names_the_recorded_metrics_json(smoke_run: SmokeRun, tmp_path: Path) -> None:
    # Pins r4-run-integrity-02 (inspect): inspect compares the manifest with the metrics.json
    # recorded in the run directory, and says so.
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "manifest.json", lambda m: m.__setitem__("label", "VALIDATED plan"))
    code, output = run_cli("optimize", "inspect", str(run_dir))
    assert code == EXIT_FAILED
    report = report_of(output, "optimization_inspection")
    assert codes(report, "error") == ["manifest_field_differs"]
    assert messages(report, "manifest_field_differs") == [differs("label", "recorded")]


def _metrics_label(m: dict[str, Any]) -> None:
    m["label"] = "VALIDATED staffing plan"


def _metrics_snapshot(m: dict[str, Any]) -> None:
    m["inputs"]["snapshot"]["snapshot_id"] = "nyc-311-validated-2026"


def _metrics_solver(m: dict[str, Any]) -> None:
    m["solver"]["settings"]["method"] = "exact-milp-certified"


def _metrics_feasibility(m: dict[str, Any]) -> None:
    m["scenarios"][1]["feasibility"] = "strict"


def _metrics_fingerprint(m: dict[str, Any]) -> None:
    m["config_fingerprint"] = ZEROS


METRICS_EDITS = [
    _metrics_label,
    _metrics_snapshot,
    _metrics_solver,
    _metrics_feasibility,
    _metrics_fingerprint,
]


@pytest.mark.integration
@pytest.mark.parametrize(
    "change", METRICS_EDITS, ids=["label", "snapshot", "solver", "feasibility", "fingerprint"]
)
def test_an_altered_metrics_json_is_not_reported_as_a_manifest_difference(
    smoke_run: SmokeRun, tmp_path: Path, change: Callable[[dict[str, Any]], None]
) -> None:
    # Pins r4-run-integrity-02 (inspect): with only metrics.json edited (the manifest never
    # touched), inspect also said "The manifest's 'inputs' differs ...". The alteration is
    # the one ERROR; the manifest is not compared with a file that no longer matches it.
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "metrics.json", change)
    code, output = run_cli("optimize", "inspect", str(run_dir))
    assert code == EXIT_FAILED
    report = report_of(output, "optimization_inspection")
    assert codes(report, "error") == ["file_altered"]
    assert issue(report, "file_altered")["subject"] == "metrics.json"


# --- r4-run-integrity-03: more manifest copies are cross-checked -------------------------------


def _forge_snapshot_id(m: dict[str, Any]) -> None:
    m["data"]["snapshot_id"] = "nyc-311-validated-2026"


def _forge_data_fingerprint(m: dict[str, Any]) -> None:
    m["data"]["data_fingerprint"] = ZEROS


def _forge_solver_method(m: dict[str, Any]) -> None:
    m["solver"]["settings"]["method"] = "exact-milp-certified"
    m["solver"]["settings"]["primal_feasibility_tolerance"] = 0.5


def _forge_solver_version(m: dict[str, Any]) -> None:
    m["solver"]["highs"] = "99.0.0"


def _forge_run_id(m: dict[str, Any]) -> None:
    m["run_id"] = "nyc-validated-staffing-plan-2026"


def _forge_result_files(m: dict[str, Any]) -> None:
    m["result_files"] = ["scenarios.csv"]


COPY_FORGERIES: list[tuple[str, Callable[[dict[str, Any]], None], list[str]]] = [
    ("snapshot_id", _forge_snapshot_id, ["data"]),
    # The run id is derived from the data fingerprint, so it no longer matches either.
    ("data_fingerprint", _forge_data_fingerprint, ["data", "run_id"]),
    ("solver_method", _forge_solver_method, ["solver"]),
    ("solver_version", _forge_solver_version, ["solver"]),
    ("run_id", _forge_run_id, ["run_id"]),
    ("result_files", _forge_result_files, ["result_files"]),
]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("forge", "keys"), [f[1:] for f in COPY_FORGERIES], ids=[f[0] for f in COPY_FORGERIES]
)
def test_inspect_refuses_forged_data_solver_run_id_and_result_files(
    smoke_run: SmokeRun,
    tmp_path: Path,
    forge: Callable[[dict[str, Any]], None],
    keys: list[str],
) -> None:
    # Pins r4-run-integrity-03 (inspect): these forged manifests passed inspect with exit 0
    # and "0 error(s)" (inspect --format json even showed the forged run_id).
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "manifest.json", forge)
    code, output = run_cli("optimize", "inspect", str(run_dir))
    assert code == EXIT_FAILED
    report = report_of(output, "optimization_inspection")
    assert codes(report, "error") == ["manifest_field_differs"] * len(keys)
    found = messages(report, "manifest_field_differs")
    assert [m.split(" differs from ")[0] for m in found] == [f"The manifest's {k!r}" for k in keys]
    # data and solver are copies of metrics.json (inputs.snapshot and solver).
    for key, message in zip(keys, found, strict=True):
        if key in ("data", "solver"):
            assert message == differs(key, "recorded")


@pytest.mark.integration
def test_verify_refuses_forged_data_solver_run_id_and_result_files(
    smoke_run: SmokeRun, tmp_path: Path
) -> None:
    # Pins r4-run-integrity-03 (verify): verify certified each of these forgeries as
    # reproduced (exit 0, "0 error(s)"). One manifest carries all four; each is one ERROR.
    run_dir = smoke_run.copy(tmp_path / "run")
    for forge in (_forge_snapshot_id, _forge_solver_method, _forge_run_id, _forge_result_files):
        edit_json(run_dir / "manifest.json", forge)
    code, output = run_cli("optimize", "verify", str(run_dir))
    assert code == EXIT_FAILED
    report = report_of(output, "optimization_verification")
    assert codes(report, "error") == ["manifest_field_differs"] * 4
    found = messages(report, "manifest_field_differs")
    assert [m.split(" differs from ")[0] for m in found] == [
        f"The manifest's {k!r}" for k in ("data", "solver", "result_files", "run_id")
    ]
    assert found[:2] == [differs("data", "recomputed"), differs("solver", "recomputed")]
    assert report["facts"]["files"] == dict.fromkeys(report["facts"]["files"], "identical")


def _forge_budget_value(m: dict[str, Any]) -> None:
    m["config"]["discretionary_budget"]["value"] = 1.0


def _forge_budget_provenance(m: dict[str, Any]) -> None:
    m["config"]["discretionary_budget"]["provenance"] = "VALIDATED"
    m["config"]["discretionary_budget"]["value"] = 1.0


EMBEDDED = (
    "The manifest's embedded configuration does not give its recorded configuration fingerprint."
)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("forge", "verify_error"),
    [
        (_forge_budget_value, "the recorded configuration does not match its recorded fingerprint"),
        (
            _forge_budget_provenance,
            "discretionary_budget.provenance: Input should be 'HYPOTHETICAL'",
        ),
    ],
    ids=["other_valid_config", "validated_budget"],
)
def test_an_embedded_configuration_that_does_not_give_its_fingerprint_is_refused(
    smoke_run: SmokeRun,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    forge: Callable[[dict[str, Any]], None],
    verify_error: str,
) -> None:
    # Pins r4-run-integrity-03 (embedded configuration): a manifest whose configuration claims
    # another (even a non-HYPOTHETICAL) budget passed inspect with exit 0. inspect reports an
    # ERROR; verify, which re-runs that configuration, refuses it as a usage error.
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "manifest.json", forge)
    code, output = run_cli("optimize", "inspect", str(run_dir))
    assert code == EXIT_FAILED
    report = report_of(output, "optimization_inspection")
    assert codes(report, "error") == ["manifest_field_differs"]
    assert messages(report, "manifest_field_differs") == [EMBEDDED]
    capsys.readouterr()
    with pytest.MonkeyPatch.context() as patch:
        _forbid_computation(patch)
        code, _ = run_cli("optimize", "verify", str(run_dir))
    assert code == EXIT_USAGE
    assert verify_error in capsys.readouterr().err


# --- r4-run-integrity-04: a symlinked src/atlas or src --------------------------------------------


def _layout(tmp_path: Path, layout: str) -> Path:
    """A repository root whose src/atlas (or src) is a symlink to the package that runs."""
    root = tmp_path / "repo"
    inside = layout.endswith("inside")
    # Inside: an untracked or ignored directory of the root; outside: a tree outside it.
    base = root / "vendor" if inside else tmp_path / "outside"
    (base / "atlas").mkdir(parents=True)
    (base / "atlas" / "__init__.py").write_text("", encoding="utf-8")
    if layout.startswith("package"):
        (root / "src").mkdir(parents=True)
        (root / "src" / "atlas").symlink_to(base / "atlas", target_is_directory=True)
    else:
        root.mkdir(exist_ok=True)
        (root / "src").symlink_to(base, target_is_directory=True)
    return root


@pytest.mark.parametrize(
    "layout", ["package_inside", "src_inside", "package_outside", "src_outside"]
)
def test_a_symlinked_package_is_not_the_repositorys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str
) -> None:
    # Pins r4-run-integrity-04: running_package_is_repository resolved symlinks on both
    # sides, so a tracked symlink src/atlas (or src) to a tree that git status cannot see
    # counted as the repository's package and --require-clean recorded edited code as clean.
    # The package is imported through the symlink (PYTHONPATH=<root>/src).
    root = _layout(tmp_path, layout)
    monkeypatch.setattr(atlas, "__file__", str(root / "src" / "atlas" / "__init__.py"))
    assert running_package_is_repository(root) is False
    # The control: the same tree as a real directory is the repository's package.
    link = root / "src" / "atlas" if layout.startswith("package") else root / "src"
    target = link.resolve()
    link.unlink()
    shutil.copytree(target, link)
    assert running_package_is_repository(root) is True


@pytest.mark.parametrize("link", ["src/atlas", "src"])
def test_require_clean_refuses_a_committed_symlink_to_the_running_package(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path, link: str
) -> None:
    # Pins r4-run-integrity-04 end to end: a clean checkout whose committed src/atlas (or
    # src) is a symlink to a tree outside git (here the package that runs) was accepted by
    # --require-clean, which then recorded the checkout's commit as the code that ran.
    root = tmp_path / "checkout"
    (root / "configs" / "optimization").mkdir(parents=True)
    (root / "snapshots").mkdir()
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    config = root / "configs" / "optimization" / "smoke.json"
    shutil.copy2(SMOKE, config)
    if link == "src":
        (root / "src").symlink_to(REPO_ROOT / "src", target_is_directory=True)
    else:
        (root / "src").mkdir()
        (root / "src" / "atlas").symlink_to(REPO_ROOT / "src" / "atlas", target_is_directory=True)
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "c")
    assert git(root, "status", "--porcelain") == ""
    _forbid_computation(monkeypatch)
    target = tmp_path / "out"
    code, _ = run_cli(
        "optimize",
        "run",
        "--require-clean",
        "--config",
        str(config),
        "--output-dir",
        str(target),
        repo_root=root,
    )
    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "refusing a reference run: the atlas package that runs is not" in err
    assert not target.exists()


# --- r4-run-integrity-05: malformed run files are usage errors ----------------------------------


def add_big_integer(path: Path) -> None:
    """An extra key holding a 5000-digit integer (beyond Python's 4300-digit limit)."""
    text = path.read_text(encoding="utf-8").rstrip()
    assert text.endswith("}")
    path.write_text(text[:-1] + ', "big": ' + "9" * 5000 + "}\n", encoding="utf-8")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("name", "command", "what"),
    [
        ("manifest.json", "inspect", "run manifest"),
        ("manifest.json", "verify", "run manifest"),
        ("metrics.json", "inspect", "metrics"),
    ],
    ids=["manifest-inspect", "manifest-verify", "metrics-inspect"],
)
def test_an_integer_beyond_the_digit_limit_is_a_usage_error(
    smoke_run: SmokeRun,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    command: str,
    what: str,
) -> None:
    # Pins r4-run-integrity-05: json.loads raises a plain ValueError for such a literal, so
    # inspect and verify ended in "atlas: internal error (please report)", exit 3.
    run_dir = smoke_run.copy(tmp_path / "run")
    add_big_integer(run_dir / name)
    code, _ = run_cli("optimize", command, str(run_dir))
    assert code == EXIT_USAGE
    assert capsys.readouterr().err.startswith(
        f"atlas: error: {run_dir / name}: the {what} cannot be read (Exceeds the limit "
        "(4300 digits) for integer string conversion"
    )


@pytest.mark.integration
@pytest.mark.parametrize("command", ["inspect", "verify"])
def test_an_output_name_with_a_lone_surrogate_is_a_usage_error(
    smoke_run: SmokeRun, tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    # Pins r4-run-integrity-05: the name length was measured with errors="replace", so
    # "\ud800x" passed as a plain name, was reported as file_missing (exit 1), and the text
    # renderer then crashed printing it (UnicodeEncodeError). A lone surrogate anywhere in a
    # run file is now rejected when the file is read.
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "manifest.json", lambda m: m["outputs"].__setitem__("\ud800x", ZEROS))
    code, _ = run_cli("optimize", command, str(run_dir))
    assert code == EXIT_USAGE
    assert capsys.readouterr().err == (
        f"atlas: error: {run_dir}/manifest.json: the run manifest contains text that is not valid\n"
    )


# --- r4-run-integrity-07: a recorded digest that is not a string -------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("command", ["inspect", "verify"])
@pytest.mark.parametrize("digest", [123, None, [ZEROS]], ids=["int", "null", "list"])
def test_a_recorded_digest_that_is_not_a_string_is_named_as_such(
    smoke_run: SmokeRun,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    digest: object,
    command: str,
) -> None:
    # Pins r4-run-integrity-07: outputs['report.md'] = 123 was reported as "recorded output
    # 'report.md' is not a plain file name", hiding the real problem (no SHA-256).
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "manifest.json", lambda m: m["outputs"].__setitem__("report.md", digest))
    code, _ = run_cli("optimize", command, str(run_dir))
    assert code == EXIT_USAGE
    assert capsys.readouterr().err == (
        f"atlas: error: {run_dir}/manifest.json: recorded output 'report.md' has no SHA-256 "
        "string\n"
    )


# --- r4-run-integrity-06: a foreign package is a WARNING of run and inspect ---------------------


@pytest.mark.integration
def test_a_run_with_a_foreign_package_warns_and_so_does_inspect(
    smoke_run: SmokeRun, repo_copy: Path, tmp_path: Path
) -> None:
    # Pins r4-run-integrity-06: with --repo-root at another checkout (byte-identical code,
    # but not the package that runs), the run recorded running_package_is_repository_src
    # false silently, and inspect of that run passed with 0 warnings.
    shutil.copytree(REPO_ROOT / "src" / "atlas", repo_copy / "src" / "atlas")
    config = json.loads(SMOKE.read_text(encoding="utf-8"))
    for relative in (
        config["forecast"]["run_dir"],
        config["forecast"]["backtest_run_dir"],
        str(Path(config["uncertainty"]["calibration_status"]).parent),
        config["uncertainty"]["projection_run_dir"],
    ):
        shutil.copytree(REPO_ROOT / relative, repo_copy / relative)
    shutil.copy2(REPO_ROOT / "uv.lock", repo_copy / "uv.lock")
    target = tmp_path / "run"
    code, output = run_cli(
        "optimize",
        "run",
        "--config",
        str(SMOKE),
        "--output-dir",
        str(target),
        repo_root=repo_copy,
    )
    assert code == EXIT_OK, output
    report = report_of(output, "optimization")
    assert codes(report, "error") == []
    found = issue(report, "running_package_not_repository")
    assert (found["severity"], found["subject"]) == ("warning", "code")
    assert found["message"] == (
        f"The atlas package that ran is not {repo_copy / 'src' / 'atlas'}: the recorded "
        "commit and code hash do not identify the code that ran (check --repo-root, "
        "ATLAS_REPO_ROOT and PYTHONPATH)."
    )
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["code"]["running_package_is_repository_src"] is False
    assert manifest["complete"] is True  # a WARNING: the results are kept
    code, output = run_cli("optimize", "inspect", str(target))
    assert code == EXIT_OK
    inspection = report_of(output, "optimization_inspection")
    assert codes(inspection, "error") == []
    assert codes(inspection, "warning") == ["recorded_package_not_repository"]
    # The repository's own run records true, and inspect does not warn about it.
    code, output = run_cli("optimize", "inspect", str(smoke_run.directory))
    assert code == EXIT_OK
    assert codes(report_of(output, "optimization_inspection"), "warning") == []


@pytest.mark.integration
def test_inspect_warns_about_a_manifest_that_records_a_foreign_package(
    smoke_run: SmokeRun, tmp_path: Path
) -> None:
    # Pins r4-run-integrity-06 (inspect): only verify reported the recorded flag.
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(
        run_dir / "manifest.json",
        lambda m: m["code"].__setitem__("running_package_is_repository_src", False),
    )
    code, output = run_cli("optimize", "inspect", str(run_dir))
    assert code == EXIT_OK
    report = report_of(output, "optimization_inspection")
    assert codes(report, "error") == []
    found = issue(report, "recorded_package_not_repository")
    assert (found["severity"], found["subject"]) == ("warning", "manifest.json")
    assert found["message"] == (
        "The run was made with an atlas package outside its repository root: its recorded "
        "commit and code hash do not identify the code that ran."
    )
    assert codes(report, "warning") == ["recorded_package_not_repository"]


# --- r4-run-integrity-08: the hash-object acceptance path of --require-clean -------------------


@pytest.mark.parametrize("checkout", ["refreshed_index", "autocrlf_clone"])
def test_a_crlf_checkout_of_an_lf_blob_is_accepted(tmp_path: Path, checkout: str) -> None:
    # Pins r4-run-integrity-08: the Windows-style checkout (LF blob, core.autocrlf=true,
    # CRLF working file, git status clean) is accepted only through git's filtered
    # hash-object; replacing that path with "and True" survived every runner test.
    origin = tmp_path / "origin"
    config = origin / "configs" / "m4.json"
    config.parent.mkdir(parents=True)
    config.write_bytes(b'{\n  "a": 1\n}\n')
    git(origin, "init", "-q")
    git(origin, "-c", "core.autocrlf=false", "add", "-A")
    git(origin, "-c", "core.autocrlf=false", "commit", "-q", "-m", "c")
    if checkout == "autocrlf_clone":
        root = tmp_path / "win"
        git(tmp_path, "-c", "core.autocrlf=true", "clone", "-q", str(origin), str(root))
        git(root, "config", "core.autocrlf", "true")
        config = root / "configs" / "m4.json"
    else:
        root = origin
        git(root, "config", "core.autocrlf", "true")
        config.write_bytes(b'{\r\n  "a": 1\r\n}\r\n')
        git(root, "add", "-A")  # refreshes the index; the content is the committed blob
    assert git(root, "status", "--porcelain") == ""
    # The precondition: the raw bytes are not the blob, git's filtered hash is.
    assert config.read_bytes() == b'{\r\n  "a": 1\r\n}\r\n'
    assert git_blob(root, "HEAD:./configs/m4.json") == b'{\n  "a": 1\n}\n'
    assert git(root, "hash-object", "--", "configs/m4.json") == git(
        root, "rev-parse", "HEAD:./configs/m4.json"
    )
    _require_committed_config(root, config)
    # The comparison is still live: other content with the same line endings is refused.
    config.write_bytes(b'{\r\n  "a": 2\r\n}\r\n')
    with pytest.raises(ConfigError, match="differs from the committed file"):
        _require_committed_config(root, config)


# --- r4-suite-01: a scenario id that is a list or an object ------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("output_format", ["json", "text"])
@pytest.mark.parametrize("scenario_id", [[1], {"a": 1}], ids=["list", "object"])
def test_an_unhashable_scenario_id_in_an_altered_metrics_json_is_reported(
    smoke_run: SmokeRun, tmp_path: Path, scenario_id: object, output_format: str
) -> None:
    # Pins r4-suite-01 (the finding's reproduction): metrics.json scenarios[0].scenario_id =
    # [1] or {'a': 1} made inspect exit 3 (TypeError: unhashable type) before it reported
    # the file_altered ERROR it had found.
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(
        run_dir / "metrics.json",
        lambda m: m["scenarios"][0].__setitem__("scenario_id", scenario_id),
    )
    code, output = run_cli("optimize", "inspect", str(run_dir), output_format=output_format)
    assert code == EXIT_FAILED
    if output_format == "json":
        assert codes(report_of(output, "optimization_inspection"), "error") == ["file_altered"]
    else:
        assert text_issues(output, "optimization_inspection") == [
            ("ERROR", "metrics.json", "file_altered")
        ]


@pytest.mark.integration
@pytest.mark.parametrize("scenario_id", [[1], {"a": 1}], ids=["list", "object"])
def test_an_unhashable_scenario_id_in_a_consistent_record_is_a_manifest_difference(
    smoke_run: SmokeRun, tmp_path: Path, scenario_id: object
) -> None:
    # Pins r4-suite-01 (the comparison itself): with the manifest re-hashed to the edited
    # metrics.json, the scenario comparison keyed a dict on the list and crashed (exit 3);
    # it is the 'scenarios' copy that differs.
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(
        run_dir / "metrics.json",
        lambda m: m["scenarios"][0].__setitem__("scenario_id", scenario_id),
    )
    rehash_metrics(run_dir)
    code, output = run_cli("optimize", "inspect", str(run_dir))
    assert code == EXIT_FAILED
    report = report_of(output, "optimization_inspection")
    assert codes(report, "error") == ["manifest_field_differs"]
    assert messages(report, "manifest_field_differs") == [
        "The manifest's 'scenarios' (demand, adjustments, feasibility) differ from the "
        "recorded metrics.json."
    ]


# --- round-3 run-integrity-05 (open part): verify reports the recomputation's fallbacks ---------


@dataclass(frozen=True)
class FallbackRun:
    directory: Path
    report: dict[str, Any]  # the 'optimization' report of the run (JSON)
    outputs: Any  # the computation, which verify recomputes identically


@pytest.fixture(scope="module")
def fallback_run(tmp_path_factory: pytest.TempPathFactory) -> FallbackRun:
    """A smoke run in which every 7th face solve is relabelled as a zero_threshold fallback
    (deterministic, as r4-work/run-integrity/fb_verify.py): its plans are unchanged."""
    original = da.minimise_on_face
    calls = itertools.count(1)

    def relabel(*args: Any, **kwargs: Any) -> Any:
        restricted, solution, flag = original(*args, **kwargs)
        if next(calls) % 7 == 0 and flag is None:
            return restricted, solution, "zero_threshold"
        return restricted, solution, flag

    computed: list[Any] = []

    def compute(settings: Any, config: Any, inputs: Any) -> Any:
        computed.append(_REAL_M4_OUTPUTS(settings, config, inputs))
        return computed[-1]

    directory = tmp_path_factory.mktemp("m4v-fallback") / "run"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(da, "minimise_on_face", relabel)
        patch.setattr(runner, "m4_outputs", compute)
        code, output = run_cli(
            "optimize", "run", "--config", str(SMOKE), "--output-dir", str(directory)
        )
    assert code == EXIT_OK, output
    assert len(computed) == 1
    return FallbackRun(directory, report_of(output, "optimization"), computed[0])


@pytest.mark.integration
def test_verify_reports_the_recomputations_stage_fallbacks(
    fallback_run: FallbackRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pins round-3 run-integrity-05 (left open in round 4): for a run whose metrics.json
    # records stage fallbacks, verify printed "0 error(s), 0 warning(s)". It reports the
    # recomputation's stage_fallback WARNINGs, the same ones the run printed.
    metrics = json.loads((fallback_run.directory / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["checks"]["stage_fallbacks"] > 0
    expected = [i for i in fallback_run.report["issues"] if i["code"] == "stage_fallback"]
    assert len(expected) >= 4  # each scenario and some sweep points
    assert {i["severity"] for i in expected} == {"warning"}
    monkeypatch.setattr(runner, "m4_outputs", lambda *_args: fallback_run.outputs)
    code, output = run_cli("optimize", "verify", str(fallback_run.directory))
    assert code == EXIT_OK
    report = report_of(output, "optimization_verification")
    assert codes(report, "error") == []
    assert report["facts"]["identical"] is True
    assert [i for i in report["issues"] if i["severity"] == "warning"] == expected
