"""The publication controls of the static demonstration (M9).

Phase A must not publish: the GitHub Pages workflow builds and tests on every pull request
and push, and publishes only on a manual dispatch on main that passes the gate
(.github/scripts/pages_gate.py), whose committed record states no owner decision yet. The
tests hold for the record as it stands, so the owner's later approval (every condition
true, in a reviewed commit) passes them too, while any incomplete record is refused.
After a publication, .github/scripts/pages_check_site.py compares the site with the build.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import http.server
import importlib.util
import json
import re
import threading
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.conftest import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pages.yml"
RECORD = REPO_ROOT / "docs" / "release" / "PAGES_PUBLICATION.json"


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / ".github" / "scripts" / f"{name}.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = _script("pages_gate")
CHECK = _script("pages_check_site")
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


def test_the_committed_record_publishes_only_what_it_approves() -> None:
    """Phase A's record (no decision) is refused; a record that approves everything must be
    complete and well formed, and is the only one that passes."""
    record = _record()
    assert record["format"] == GATE.FORMAT
    assert record["site"] == "https://arthur-fay.github.io/demand-forecasting-model/"
    assert set(record["conditions"]) == set(GATE.CONDITIONS)
    problems = _open(record)
    unmet = [name for name in GATE.CONDITIONS if record["conditions"][name] is not True]
    if unmet:
        for name in unmet:
            assert f"condition not met: {name}" in problems
    else:
        assert problems == [], problems
    assert GATE.load_record(RECORD) == record


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
    extra = _approved()
    extra["approved"] = True
    assert any("unknown fields in the record: approved" in p for p in _open(extra))


@pytest.mark.parametrize("day", ["9999-99-99", "2026-02-30", "2026-13-01"])
def test_an_impossible_date_is_refused(day: str) -> None:
    assert any("no decided_on date" in p for p in _open(_without("decided_on", day)))


def test_a_key_given_twice_is_refused(tmp_path: Path) -> None:
    """A reviewer reading the first `false` must not be overruled by a later `true`."""
    conditions = ", ".join(f'"{name}": false, "{name}": true' for name in GATE.CONDITIONS)
    approved = _approved()
    text = json.dumps(approved).replace(json.dumps(approved["conditions"]), "{" + conditions + "}")
    record = tmp_path / "record.json"
    record.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="repeated keys"):
        GATE.load_record(record)
    common = ["--confirm", GATE.PHRASE, "--switch", "approved", "--root", str(REPO_ROOT)]
    assert GATE.main(["--record", str(record), "--ref", "refs/heads/main", *common]) == 1


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
    # The artifact is uploaded only for a publication, straight after its verification and
    # before anything else runs; only the deploy job publishes, then checks every file.
    before, _ = jobs["build"].split("actions/upload-pages-artifact", 1)
    upload = before.rsplit("- name:", 1)[1]
    assert manual in upload
    steps = re.findall(r"- name: (.+)", jobs["build"])
    verify = next(i for i, name in enumerate(steps) if name.startswith("Every file as recorded"))
    assert steps[verify + 1].startswith("Hand the verified build to the publication job")
    assert "playwright" not in before.split("npm run verify:pages", 1)[1].split("- name:")[1]
    assert "actions/deploy-pages" in jobs["deploy"]
    assert "actions/deploy-pages" not in jobs["build"] + jobs["gate"]
    assert "pages_check_site.py --manifest web/atlas-static-site.json" in jobs["deploy"]
    # A pull request's group is its own: a fork's branch named main cannot cancel main's run.
    top = text.split("\njobs:\n", 1)[0]
    assert "format('pr-{0}', github.event.pull_request.number) || github.ref" in top


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


# --- The check of a published site -------------------------------------------------------


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def site(tmp_path: Path) -> Iterator[tuple[Path, Path, str]]:
    """A tiny static site served on this machine, and its committed manifest."""
    root = tmp_path / "site"
    (root / "data").mkdir(parents=True)
    files = {"index.html": b"<!doctype html>", "data/models.json": b'{"a": 1}'}
    for name, body in files.items():
        (root / name).write_bytes(body)
    manifest = {
        "format": "atlas-static-site/1",
        "files": {
            name: {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
            for name, body in files.items()
        },
    }
    committed = tmp_path / "atlas-static-site.json"
    committed.write_text(json.dumps(manifest), encoding="utf-8")
    (root / "atlas-static-site.json").write_bytes(committed.read_bytes())
    handler = functools.partial(_Quiet, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield root, committed, f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


def _check(committed: Path, url: str, *extra: str) -> int:
    arguments = ["--manifest", str(committed), "--url", url, "--attempts", "1", "--wait", "0"]
    return int(CHECK.main([*arguments, *extra]))


def test_a_site_serving_the_committed_build_passes(site: tuple[Path, Path, str]) -> None:
    _, committed, url = site
    assert _check(committed, url, "--allow-http") == 0
    assert _check(committed, url.rstrip("/"), "--allow-http") == 0


def test_every_differing_file_is_named(
    site: tuple[Path, Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    root, committed, url = site
    (root / "data" / "models.json").write_bytes(b'{"a": 2}')
    (root / "index.html").unlink()
    assert _check(committed, url, "--allow-http") == 1
    err = capsys.readouterr().err
    assert "- data/models.json" in err
    assert "- index.html" in err
    (root / "atlas-static-site.json").write_text("{}", encoding="utf-8")
    assert _check(committed, url, "--allow-http") == 1
    assert "- atlas-static-site.json" in capsys.readouterr().err


def test_only_an_https_site_is_checked_for_real(site: tuple[Path, Path, str]) -> None:
    _, committed, url = site
    assert _check(committed, url) == 1
    assert _check(committed, "ftp://example.org/", "--allow-http") == 1
