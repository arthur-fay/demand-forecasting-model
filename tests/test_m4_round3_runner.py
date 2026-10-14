"""Regression tests for the third independent review: run integrity and CLI text.

Each test pins one fix of commit a7ca6a6 (run-integrity-01 to -06, test-strength-12 to -14)
and fails when that fix is reverted: manifest labels and copies that disagree with the
fingerprinted ``metrics.json``, deleted result files, the atlas package that runs versus the
repository root, malformed or undecodable run files and unsafe output names (usage errors,
never internal errors), the ``--require-clean`` blob check (CRLF under ``core.autocrlf`` and a
root below the git toplevel), stage fallbacks after the run, and what the verify, inspect and
run text says.

Every command test uses ``configs/optimization/smoke.json``. The smoke run is made once per
module through the CLI; tests that tamper with it work on a copy. ``optimize verify`` re-runs
the whole computation, which is deterministic (tests/test_m4_runner_cli.py checks that verify
reproduces every file byte for byte), so here it is computed once per configuration and
reused: a verify test costs only its comparisons.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import atlas.optimization_runner as runner
from atlas.cli import EXIT_FAILED, EXIT_OK, EXIT_USAGE, _render_optimization, _small, main
from atlas.errors import ConfigError
from atlas.experiment import result_fingerprint
from atlas.optimization_report import _q, render_optimization_report
from atlas.optimization_runner import (
    RESULT_FILES,
    _require_committed_config,
    running_package_is_repository,
)
from tests.conftest import REPO_ROOT

SMOKE = REPO_ROOT / "configs" / "optimization" / "smoke.json"
TERMINAL_MAX = 3000.0  # the smoke configuration's terminal-backlog target (requests)
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
    return next(r for r in json.loads(output)["reports"] if r["name"] == name)


def codes(report: dict[str, Any], severity: str) -> list[str]:
    return [i["code"] for i in report["issues"] if i["severity"] == severity]


def issue(report: dict[str, Any], code: str) -> dict[str, Any]:
    return next(i for i in report["issues"] if i["code"] == code)


def text_issues(output: str, name: str) -> list[tuple[str, str, str]]:
    """(severity, subject, code) of every issue line in the text block of report ``name``."""
    block = re.search(rf"^\[(?:PASS|FAIL)\] {name}:.*?(?=^\[|\Z)", output, re.MULTILINE | re.DOTALL)
    assert block is not None, name
    return re.findall(r"^  (ERROR|WARNING|INFO) +(.+?) \[([a-z_]+)\]", block.group(0), re.MULTILINE)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def edit_json(path: Path, change: Callable[[dict[str, Any]], object]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    change(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


@dataclass(frozen=True)
class SmokeRun:
    code: int
    text: str
    directory: Path

    def manifest(self) -> dict[str, Any]:
        return json.loads((self.directory / "manifest.json").read_text(encoding="utf-8"))

    def metrics(self) -> dict[str, Any]:
        return json.loads((self.directory / "metrics.json").read_text(encoding="utf-8"))

    def copy(self, target: Path) -> Path:
        shutil.copytree(self.directory, target)
        return target


@pytest.fixture(scope="module")
def smoke_run(tmp_path_factory: pytest.TempPathFactory) -> SmokeRun:
    directory = tmp_path_factory.mktemp("m4r3") / "smoke"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runner, "m4_outputs", _memoised_m4_outputs)
        code, text = run_cli(
            "optimize",
            "run",
            "--config",
            str(SMOKE),
            "--output-dir",
            str(directory),
            output_format="text",
        )
    assert code == EXIT_OK, text
    return SmokeRun(code, text, directory)


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


# --- test-strength-12, run-integrity-03, test-strength-14: malformed run files ------------------


@pytest.mark.integration
@pytest.mark.parametrize("command", ["verify", "inspect"])
@pytest.mark.parametrize("key", ["code", "git", "solver"])
def test_a_manifest_section_that_is_not_an_object_is_a_usage_error(
    smoke_run: SmokeRun,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    key: str,
    command: str,
) -> None:
    # Pins the R9 shape check for each of code/git/solver: without it verify calls .get on a
    # string (AttributeError, exit 3 "internal error") and inspect passes the run.
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "manifest.json", lambda m: m.__setitem__(key, "x"))
    code, _ = run_cli("optimize", command, str(run_dir))
    assert code == EXIT_USAGE
    assert f"manifest.json: '{key}' is not an object" in capsys.readouterr().err


@pytest.mark.integration
@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("scenarios", [1], "'scenarios' is not a list of objects"),
        ("inputs", "x", "'inputs' is not an object"),
    ],
    ids=["scenarios", "inputs"],
)
def test_malformed_top_level_metrics_values_are_usage_errors(
    smoke_run: SmokeRun,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    key: str,
    value: object,
    message: str,
) -> None:
    # Pins the R9 metrics.json shape checks (test-strength-12: removing the 'inputs' check
    # survived): a malformed metrics.json is refused as a usage error before any headline.
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "metrics.json", lambda m: m.__setitem__(key, value))
    code, _ = run_cli("optimize", "inspect", str(run_dir))
    assert code == EXIT_USAGE
    assert f"metrics.json: {message}" in capsys.readouterr().err


def _set_reported_plan(metrics: dict[str, Any]) -> None:
    metrics["scenarios"][0]["reported_plan"] = "x"


def _set_diagnosis(metrics: dict[str, Any]) -> None:
    relaxed = next(s for s in metrics["scenarios"] if s["diagnosis"])
    relaxed["diagnosis"] = "x"


def _set_uncertainty_status(metrics: dict[str, Any]) -> None:
    metrics["inputs"]["uncertainty_status"] = "x"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (_set_reported_plan, ["file_altered"]),
        (_set_diagnosis, ["file_altered"]),
        # The manifest's copy of the inputs no longer equals metrics.json either.
        (_set_uncertainty_status, ["file_altered", "manifest_field_differs"]),
    ],
    ids=["reported_plan", "diagnosis", "uncertainty_status"],
)
def test_nested_malformed_metrics_values_report_the_alteration_instead_of_crashing(
    smoke_run: SmokeRun,
    tmp_path: Path,
    change: Callable[[dict[str, Any]], None],
    expected: list[str],
) -> None:
    # Pins run-integrity-03 / test-strength-14: headline() called .get on nested values that
    # are not objects, so inspect crashed (exit 3) instead of reporting the file_altered
    # ERROR it had already found.
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "metrics.json", change)
    code, output = run_cli("optimize", "inspect", str(run_dir))
    assert code == EXIT_FAILED
    report = report_of(output, "optimization_inspection")
    assert codes(report, "error") == expected
    assert issue(report, "file_altered")["subject"] == "metrics.json"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("name", "command", "what"),
    [
        ("manifest.json", "verify", "run manifest"),
        ("manifest.json", "inspect", "run manifest"),
        ("metrics.json", "inspect", "metrics"),
    ],
    ids=["manifest-verify", "manifest-inspect", "metrics-inspect"],
)
def test_undecodable_run_files_are_usage_errors(
    smoke_run: SmokeRun,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    command: str,
    what: str,
) -> None:
    # Pins run-integrity-03: bytes that are not UTF-8 raised UnicodeDecodeError (exit 3).
    run_dir = smoke_run.copy(tmp_path / "run")
    (run_dir / name).write_bytes(b"\xff\xfe{}")
    code, _ = run_cli("optimize", command, str(run_dir))
    assert code == EXIT_USAGE
    assert f"{name}: the {what} is not valid UTF-8" in capsys.readouterr().err


@pytest.mark.integration
@pytest.mark.parametrize("command", ["verify", "inspect"])
@pytest.mark.parametrize(
    "name",
    ["sub/x.csv", "a\\b.csv", "..", "a" * 300, "é" * 128],
    ids=["slash", "backslash", "dotdot", "300_chars", "256_utf8_bytes"],
)
def test_a_recorded_output_that_is_not_a_plain_file_name_is_a_usage_error(
    smoke_run: SmokeRun,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    command: str,
) -> None:
    # Pins run-integrity-03: an output name with a path separator reached outside the run
    # directory (or counted as a missing file), and a name over NAME_MAX (255 bytes; 128
    # two-byte characters are 256) made is_file raise ENAMETOOLONG (exit 3).
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "manifest.json", lambda m: m["outputs"].__setitem__(name, ZEROS))
    code, _ = run_cli("optimize", command, str(run_dir))
    assert code == EXIT_USAGE
    assert "is not a plain file name" in capsys.readouterr().err


# --- run-integrity-01: the manifest's copies must equal metrics.json ---------------------------


def _forge_status(manifest: dict[str, Any]) -> None:
    status = manifest["inputs"]["uncertainty_status"]
    status["interval_status"] = "calibrated"
    status["decision_grade"] = True


def _forge_feasibility(manifest: dict[str, Any]) -> None:
    manifest["scenarios"]["reduced_budget"]["feasibility"] = "strict"


FORGERIES: list[tuple[str, Callable[[dict[str, Any]], object], str]] = [
    (
        "validated_recommendation",
        lambda m: m.__setitem__("validated_recommendation", True),
        "The manifest's 'validated_recommendation' differs",
    ),
    (
        "label",
        lambda m: m.__setitem__("label", "VALIDATED staffing recommendation for NYC"),
        "The manifest's 'label' differs",
    ),
    ("inputs", _forge_status, "The manifest's 'inputs' differs"),
    (
        "config_fingerprint",
        lambda m: m.__setitem__("config_fingerprint", ZEROS),
        "The manifest's 'config_fingerprint' differs",
    ),
    ("scenarios", _forge_feasibility, "The manifest's 'scenarios' (demand, adjustments"),
]


@pytest.mark.integration
@pytest.mark.parametrize(("field", "forge", "message"), FORGERIES, ids=[f[0] for f in FORGERIES])
def test_inspect_refuses_manifest_labels_that_contradict_metrics(
    smoke_run: SmokeRun,
    tmp_path: Path,
    field: str,
    forge: Callable[[dict[str, Any]], object],
    message: str,
) -> None:
    # Pins run-integrity-01: a hand-edited, unfingerprinted manifest claiming a validated,
    # calibrated or strictly feasible result passed inspect with 0 errors.
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "manifest.json", forge)
    code, output = run_cli("optimize", "inspect", str(run_dir))
    assert code == EXIT_FAILED
    report = report_of(output, "optimization_inspection")
    assert codes(report, "error") == ["manifest_field_differs"]
    assert issue(report, "manifest_field_differs")["message"].startswith(message)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("field", "forge", "message"),
    [f for f in FORGERIES if f[0] != "config_fingerprint"],
    ids=[f[0] for f in FORGERIES if f[0] != "config_fingerprint"],
)
def test_verify_refuses_manifest_labels_that_contradict_the_recomputation(
    smoke_run: SmokeRun,
    tmp_path: Path,
    field: str,
    forge: Callable[[dict[str, Any]], object],
    message: str,
) -> None:
    # Pins run-integrity-01: verify certified such a manifest as reproduced (exit 0).
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "manifest.json", forge)
    code, output = run_cli("optimize", "verify", str(run_dir))
    assert code == EXIT_FAILED
    report = report_of(output, "optimization_verification")
    assert codes(report, "error") == ["manifest_field_differs"]
    assert issue(report, "manifest_field_differs")["message"].startswith(message)


@pytest.mark.integration
def test_verify_refuses_a_forged_config_fingerprint_before_computing(
    smoke_run: SmokeRun, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # verify checks config_fingerprint against the recorded configuration before anything
    # else, so this forgery is a usage error there (manifest_field_differs in inspect).
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "manifest.json", lambda m: m.__setitem__("config_fingerprint", ZEROS))
    code, _ = run_cli("optimize", "verify", str(run_dir))
    assert code == EXIT_USAGE
    assert "does not match its recorded fingerprint" in capsys.readouterr().err


@pytest.mark.integration
@pytest.mark.parametrize("command", ["inspect", "verify"])
def test_a_deleted_result_file_is_an_error(
    smoke_run: SmokeRun, tmp_path: Path, command: str
) -> None:
    # Pins run-integrity-01: an M4 run always writes every result file, yet a deleted
    # scenarios.csv passed both commands (inspect said only "not kept", verify said nothing).
    run_dir = smoke_run.copy(tmp_path / "run")
    (run_dir / "scenarios.csv").unlink()
    code, output = run_cli("optimize", command, str(run_dir), output_format="text")
    assert code == EXIT_FAILED
    name = {"inspect": "optimization_inspection", "verify": "optimization_verification"}
    assert text_issues(output, name[command]) == [("ERROR", "scenarios.csv", "file_missing")]
    assert "  missing: scenarios.csv\n" in output
    assert "not kept" not in output


# --- run-integrity-02: the atlas package that runs ---------------------------------------------


@pytest.mark.integration
def test_the_manifest_records_that_the_running_package_is_the_repository(
    smoke_run: SmokeRun, tmp_path: Path
) -> None:
    # Pins run-integrity-02: the code hash and commit describe --repo-root, so the manifest
    # must say whether the package that ran is <repo-root>/src/atlas.
    assert running_package_is_repository(REPO_ROOT) is True
    other = tmp_path / "other"
    shutil.copytree(REPO_ROOT / "src" / "atlas", other / "src" / "atlas")
    assert running_package_is_repository(other) is False  # a byte-identical copy elsewhere
    assert running_package_is_repository(tmp_path) is False
    assert smoke_run.manifest()["code"]["running_package_is_repository_src"] is True


def test_require_clean_refuses_when_the_running_package_is_another_tree(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Pins run-integrity-02: with --repo-root at a clean checkout whose configuration is
    # committed, --require-clean recorded that checkout's commit for code imported elsewhere.
    root = tmp_path / "checkout"
    (root / "configs" / "optimization").mkdir(parents=True)
    (root / "snapshots").mkdir()
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    config = root / "configs" / "optimization" / "smoke.json"
    shutil.copy2(SMOKE, config)
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "c")
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
    assert str(root.resolve() / "src" / "atlas") in err
    assert not target.exists()


@pytest.mark.integration
def test_verify_with_another_tree_as_repository_root_is_an_error(
    smoke_run: SmokeRun, repo_copy: Path
) -> None:
    # Pins run-integrity-02: verify with --repo-root at another checkout (same data, same
    # code bytes, but not the package that runs) passed with no provenance ERROR.
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
    code, output = run_cli("optimize", "verify", str(smoke_run.directory), repo_root=repo_copy)
    assert code == EXIT_FAILED
    report = report_of(output, "optimization_verification")
    assert codes(report, "error") == ["running_package_not_repository"]
    assert report["facts"]["files"] == dict.fromkeys(report["facts"]["files"], "identical")


@pytest.mark.integration
def test_a_manifest_recording_another_package_is_a_warning(
    smoke_run: SmokeRun, tmp_path: Path
) -> None:
    # Pins run-integrity-02: a run made with a package outside its repository root keeps its
    # results, but its commit and code hash do not identify the code; verify warns.
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(
        run_dir / "manifest.json",
        lambda m: m["code"].__setitem__("running_package_is_repository_src", False),
    )
    code, output = run_cli("optimize", "verify", str(run_dir))
    assert code == EXIT_OK
    report = report_of(output, "optimization_verification")
    assert codes(report, "error") == []
    assert codes(report, "warning") == ["recorded_package_not_repository"]


# --- run-integrity-04: the --require-clean blob check -------------------------------------------


@pytest.mark.parametrize(
    ("crlf", "below_toplevel"),
    [(True, False), (False, True), (True, True)],
    ids=["crlf_autocrlf", "subdirectory_root", "crlf_in_subdirectory_root"],
)
def test_a_committed_configuration_is_accepted(
    tmp_path: Path, crlf: bool, below_toplevel: bool
) -> None:
    # Pins run-integrity-04: 'git hash-object' applies core.autocrlf (a committed CRLF file
    # hashed as LF), and 'HEAD:<path>' is relative to the git toplevel, not to a repository
    # root below it: both refused a file byte-identical to the committed blob.
    toplevel = tmp_path / "mono"
    root = toplevel / "atlas" if below_toplevel else toplevel
    config = root / "configs" / "m4.json"
    config.parent.mkdir(parents=True)
    newline = b"\r\n" if crlf else b"\n"
    config.write_bytes(b"{" + newline + b'  "a": 1' + newline + b"}" + newline)
    git(toplevel, "init", "-q")
    git(toplevel, "-c", "core.autocrlf=false", "add", "-A")
    git(toplevel, "-c", "core.autocrlf=false", "commit", "-q", "-m", "c")
    if crlf:
        git(toplevel, "config", "core.autocrlf", "true")
        # The defect's precondition: git's filtered hash is not the committed blob.
        assert git(root, "hash-object", "--", "configs/m4.json") != git(
            root, "rev-parse", "HEAD:./configs/m4.json"
        )
    _require_committed_config(root, config)
    # The comparison is still live in this layout: other content is refused.
    config.write_bytes(b'{"edited": true}' + newline)
    with pytest.raises(ConfigError, match="differs from the committed file"):
        _require_committed_config(root, config)


def test_a_staged_but_uncommitted_configuration_is_not_in_the_commit(tmp_path: Path) -> None:
    # Pins run-integrity-04: a failed HEAD lookup was reported as "differs from the committed
    # file"; it now says the file is not in the commit at HEAD.
    root = tmp_path / "repo"
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "other.json").write_text("{}", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "c")
    config = root / "configs" / "m4.json"
    config.write_text("{}", encoding="utf-8")
    git(root, "add", "configs/m4.json")
    with pytest.raises(ConfigError, match="is not in the commit at HEAD"):
        _require_committed_config(root, config)


# --- run-integrity-05: stage fallbacks after the run -------------------------------------------


@pytest.mark.integration
def test_inspect_warns_on_recorded_stage_fallbacks(smoke_run: SmokeRun, tmp_path: Path) -> None:
    # Pins run-integrity-05: the stage_fallback WARNING existed only in the run's console
    # output; inspect printed 0 warnings for a metrics.json that records fallbacks.
    assert smoke_run.metrics()["checks"]["stage_fallbacks"] == 0
    code, output = run_cli("optimize", "inspect", str(smoke_run.directory))
    assert code == EXIT_OK
    assert codes(report_of(output, "optimization_inspection"), "warning") == []
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "metrics.json", lambda m: m["checks"].__setitem__("stage_fallbacks", 2))
    # A consistent run record: the manifest hashes the new metrics.json.
    manifest = smoke_run.manifest()
    manifest["outputs"]["metrics.json"] = sha256(run_dir / "metrics.json")
    manifest["result_fingerprint"] = result_fingerprint(manifest["outputs"], RESULT_FILES)
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    code, output = run_cli("optimize", "inspect", str(run_dir))
    assert code == EXIT_OK
    report = report_of(output, "optimization_inspection")
    assert codes(report, "error") == []
    assert codes(report, "warning") == ["stage_fallback"]
    message = issue(report, "stage_fallback")["message"]
    assert message.startswith("metrics.json records 2 stage fallback(s)")


@pytest.mark.integration
def test_the_report_has_a_run_checks_section(smoke_run: SmokeRun) -> None:
    # Pins run-integrity-05: report.md never mentioned failed checks or stage fallbacks.
    report = (smoke_run.directory / "report.md").read_text(encoding="utf-8")
    assert "## Run checks" in report
    assert "- Failed checks (ERRORs): 0." in report
    assert "- Stage fallbacks: 0." in report
    payload = smoke_run.metrics()
    payload["checks"]["stage_fallbacks"] = 3
    rendered = render_optimization_report(payload)
    assert "- Stage fallbacks: 3. A lexicographic stage" in rendered


# --- run-integrity-06: what the verify and inspect text says ------------------------------------


@pytest.mark.integration
def test_verify_names_an_edited_manifest_entry_not_an_on_disk_change(
    smoke_run: SmokeRun, tmp_path: Path
) -> None:
    # Pins run-integrity-06(b): only the manifest's hash of scenarios.csv was edited; the file
    # on disk equals the recomputation, yet verify said "altered on disk: scenarios.csv".
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "manifest.json", lambda m: m["outputs"].__setitem__("scenarios.csv", ZEROS))
    code, output = run_cli("optimize", "verify", str(run_dir), output_format="text")
    assert code == EXIT_FAILED
    assert sorted(text_issues(output, "optimization_verification")) == [
        ("ERROR", "scenarios.csv", "manifest_entry_differs"),
        ("ERROR", "scenarios.csv", "output_differs"),
    ]
    assert (
        "  manifest entry differs from the file on disk (which equals the recomputation): "
        "scenarios.csv\n"
    ) in output
    assert "altered on disk" not in output


@pytest.mark.integration
def test_verify_prints_a_differing_recorded_fingerprint_as_such(
    smoke_run: SmokeRun, tmp_path: Path
) -> None:
    # Pins run-integrity-06(a): with only the recorded result fingerprint edited, verify
    # printed "recomputation DIFFERENT" over eight "identical" files.
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "manifest.json", lambda m: m.__setitem__("result_fingerprint", ZEROS))
    code, output = run_cli("optimize", "verify", str(run_dir), output_format="text")
    assert code == EXIT_FAILED
    assert text_issues(output, "optimization_verification") == [
        ("ERROR", "manifest.json", "result_fingerprint_differs")
    ]
    real = smoke_run.manifest()["result_fingerprint"]
    assert "  recomputed files identical to the recorded hashes: " in output
    assert f"  result fingerprint {real} (the manifest records {ZEROS})\n" in output
    assert "DIFFERENT" not in output


@pytest.mark.integration
def test_inspect_says_the_manifest_marks_the_run_incomplete(
    smoke_run: SmokeRun, tmp_path: Path
) -> None:
    # Pins run-integrity-06(c): a flipped complete flag printed "an independent check failed"
    # next to "metrics.json records 0 failed check(s)".
    run_dir = smoke_run.copy(tmp_path / "run")
    edit_json(run_dir / "manifest.json", lambda m: m.__setitem__("complete", False))
    code, output = run_cli("optimize", "inspect", str(run_dir), output_format="text")
    assert code == EXIT_FAILED
    assert "[run_incomplete]: The manifest marks the run incomplete.\n" in output
    assert "  INCOMPLETE RUN: the manifest marks the run incomplete; see the errors" in output
    assert "independent check failed" not in output


# --- test-strength-13: sub-unit formatting and the CLI 'over target' suffix -------------------


@pytest.mark.integration
def test_the_cli_headline_gives_the_excess_over_target_of_each_relaxed_scenario(
    smoke_run: SmokeRun,
) -> None:
    # Pins test-strength-13 (M48): without the suffix the CLI printed a relaxed plan's
    # terminal backlog (7,270) with no sign that it misses the 3,000 target.
    scenarios = smoke_run.metrics()["scenarios"]
    relaxed = [s for s in scenarios if s["feasibility"] != "strict"]
    assert len(relaxed) == 2
    for scenario in scenarios:
        line = next(
            x
            for x in smoke_run.text.splitlines()
            if x.startswith(f"    {scenario['scenario_id']} ")
        )
        backlog = scenario["reported_plan"]["evaluation"]["terminal_backlog"]
        if scenario["feasibility"] == "strict":
            assert line.endswith(f" {backlog:,.0f}")
            continue
        excess = scenario["diagnosis"]["least_terminal_violation_within_budget_requests"]
        assert excess == pytest.approx(backlog - TERMINAL_MAX, abs=1e-6)
        assert line.endswith(f" {backlog:,.0f} ({excess:,.0f} over target)")
    assert smoke_run.text.count(" over target)") == len(relaxed)


@pytest.mark.parametrize(("value", "text"), [(0.7, "0.7"), (0.999, "0.999")])
def test_sub_unit_values_keep_three_significant_digits(value: float, text: str) -> None:
    # Pins test-strength-13 (M47, M50): with the threshold at 0.5, a violation of 0.7
    # requests printed as "1" in the report (_q) and the CLI (_small).
    assert _q(value) == text
    assert _small(value) == text
    stream = io.StringIO()
    facts = {
        "label": "L",
        "m3_interval_status": "failed_calibration",
        "result_fingerprint": "f",
        "scenarios": [
            {
                "scenario_id": "s",
                "feasibility": "relaxed_terminal_target",
                "discretionary_cost": 1.0,
                "backlog_request_days": 2.0,
                "terminal_backlog": 3.0,
                "terminal_violation": value,
            }
        ],
    }
    _render_optimization(facts, stream)
    assert stream.getvalue().endswith(f" 3 ({text} over target)\n")
