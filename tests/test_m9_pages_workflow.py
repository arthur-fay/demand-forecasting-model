"""The publication controls of the static demonstration (M9).

Phase A must not publish: the GitHub Pages workflow builds and tests on every pull request
and push, and publishes only on a manual dispatch on main that passes the gate
(.github/scripts/pages_gate.py), whose committed record states no owner decision yet.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.conftest import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pages.yml"
RECORD = REPO_ROOT / "docs" / "release" / "PAGES_PUBLICATION.json"


def _gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "pages_gate", REPO_ROOT / ".github" / "scripts" / "pages_gate.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = _gate()
VERSION = GATE.code_version(REPO_ROOT)


def _record() -> dict[str, Any]:
    document = json.loads(RECORD.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _approved() -> dict[str, Any]:
    record = _record()
    record["conditions"] = dict.fromkeys(GATE.CONDITIONS, True)
    record.update(
        release_version=VERSION,
        decided_by="arthur-fay",
        decided_on="2026-10-20",
        approval_reference="https://github.com/arthur-fay/demand-forecasting-model/pull/0",
    )
    return record


def _open(record: dict[str, Any], **changes: str) -> list[str]:
    arguments = {"ref": "refs/heads/main", "confirm": GATE.PHRASE, "switch": "approved"}
    arguments.update(changes)
    return list(GATE.gate_problems(record, VERSION, **arguments))


def test_the_committed_record_allows_no_publication() -> None:
    record = _record()
    assert record["format"] == GATE.FORMAT
    assert record["site"] == "https://arthur-fay.github.io/demand-forecasting-model/"
    assert set(record["conditions"]) == set(GATE.CONDITIONS)
    assert not any(record["conditions"].values()), "phase A records no owner decision"
    problems = _open(record)
    for name in GATE.CONDITIONS:
        assert f"condition not met: {name}" in problems
    assert "the record names no release version (MAJOR.MINOR.PATCH)" in problems


def test_a_complete_record_on_main_with_every_switch_passes() -> None:
    assert _open(_approved()) == []


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"ref": "refs/heads/feature/atlas-m9-static-demo"}, "publication runs on main only"),
        ({"ref": "refs/pull/1/merge"}, "publication runs on main only"),
        ({"confirm": ""}, "the confirmation phrase was not typed"),
        ({"confirm": "publish"}, "the confirmation phrase was not typed"),
        ({"switch": ""}, "ATLAS_PAGES_PUBLICATION is not 'approved'"),
        ({"switch": "true"}, "ATLAS_PAGES_PUBLICATION is not 'approved'"),
    ],
)
def test_each_switch_is_required(changes: dict[str, str], problem: str) -> None:
    assert any(problem in p for p in _open(_approved(), **changes))


def _unmet(name: str) -> dict[str, Any]:
    record = _approved()
    record["conditions"][name] = False
    return record


def _without(field: str, value: object) -> dict[str, Any]:
    record = _approved()
    record[field] = value
    return record


@pytest.mark.parametrize(
    ("record", "problem"),
    [
        *[(_unmet(name), f"condition not met: {name}") for name in GATE.CONDITIONS],
        (_without("release_version", "1.0.0-rc.2"), "names no release version"),
        (_without("release_version", "9.9.9"), "the record approves version 9.9.9"),
        (_without("decided_by", " "), "names no decided_by"),
        (_without("approval_reference", None), "names no approval_reference"),
        (_without("decided_on", "20 October"), "no decided_on date"),
        (_without("format", "other/1"), "is not atlas-pages-publication/1"),
        (_without("site", "https://example.org/"), "the record is for"),
        (_without("conditions", None), "the record states no conditions"),
    ],
)
def test_each_owner_decision_is_required(record: dict[str, Any], problem: str) -> None:
    assert any(problem in p for p in _open(record)), _open(record)


def test_a_condition_the_gate_does_not_know_is_refused() -> None:
    record = copy.deepcopy(_approved())
    record["conditions"]["anything_else"] = True
    assert any("unknown conditions" in p for p in _open(record))
    assert GATE.record_problems([], VERSION) == ["the publication record is not a JSON object"]


def test_the_command_refuses_the_committed_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    common = ["--confirm", GATE.PHRASE, "--switch", "approved", "--root", str(REPO_ROOT)]
    assert GATE.main(["--record", str(RECORD), "--ref", "refs/heads/main", *common]) == 1
    assert "publication refused" in capsys.readouterr().err
    approved = tmp_path / "record.json"
    approved.write_text(json.dumps(_approved()), encoding="utf-8")
    assert GATE.main(["--record", str(approved), "--ref", "refs/heads/main", *common]) == 0
    assert "every publication condition holds" in capsys.readouterr().out
    missing = tmp_path / "missing.json"
    assert GATE.main(["--record", str(missing), "--ref", "refs/heads/main", *common]) == 1


# --- The workflow ------------------------------------------------------------------------


def _jobs(text: str) -> dict[str, str]:
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"\n  (?=[a-z][a-z0-9_-]*:\n)", "\n" + body)
    return {part.split(":", 1)[0].strip(): part for part in parts if part.strip()}


def test_the_workflow_never_publishes_on_its_own() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    triggers = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert re.findall(r"^  ([a-z_]+):", triggers, re.MULTILINE) == [
        "pull_request",
        "push",
        "workflow_dispatch",
    ]
    for forbidden in ("schedule", "pull_request_target", "workflow_run", "release:"):
        assert forbidden not in triggers
    jobs = _jobs(text)
    assert list(jobs) == ["build", "gate", "deploy"]
    manual = "if: github.event_name == 'workflow_dispatch' && inputs.publish"
    assert manual in jobs["gate"]
    assert manual in jobs["deploy"]
    assert "needs: [build, gate]" in jobs["deploy"]
    assert "pages_gate.py --record docs/release/PAGES_PUBLICATION.json" in jobs["gate"]
    assert "vars.ATLAS_PAGES_PUBLICATION" in jobs["gate"]
    # The artifact is uploaded only for a publication, and only the deploy job publishes.
    upload = jobs["build"].split("actions/upload-pages-artifact", 1)[0].rsplit("- name:", 1)[1]
    assert manual in upload
    assert "actions/deploy-pages" in jobs["deploy"]
    assert "actions/deploy-pages" not in jobs["build"] + jobs["gate"]


def test_the_workflow_has_minimal_permissions_and_changes_no_setting() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    top = text.split("\njobs:\n", 1)[0]
    assert "permissions:\n  contents: read\n" in top
    jobs = _jobs(text)
    assert "permissions:" not in jobs["build"] + jobs["gate"]
    deploy = jobs["deploy"].split("permissions:", 1)[1].split("environment:", 1)[0]
    granted = re.findall(r"^\s+([a-z-]+): (read|write)$", deploy, re.MULTILINE)
    assert sorted(granted) == [("contents", "read"), ("id-token", "write"), ("pages", "write")]
    assert "name: github-pages" in jobs["deploy"]
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    for forbidden in ("configure-pages", "enablement", "secrets.", "persist-credentials: true"):
        assert forbidden not in code
    assert text.count("persist-credentials: false") == text.count("actions/checkout@")


def test_the_build_job_runs_every_static_check() -> None:
    build = _jobs(WORKFLOW.read_text(encoding="utf-8"))["build"]
    for command in (
        "npm ci",
        "npm audit --audit-level=high",
        "uv run atlas static verify",
        "npm run build:pages",
        "git diff --exit-code -- atlas-static-site.json",
        "npm run verify:pages",
        "npm run size:pages",
        'ATLAS_E2E_PAGES: "1"',
        "npx playwright test --project pages-firefox --project pages-webkit",
    ):
        assert command in build, command
