"""The publication controls of the static demonstration (M9).

Phase A must not publish: the GitHub Pages workflow builds and tests on every pull request
and push, and publishes only on a manual dispatch on main that passes the gate
(.github/scripts/pages_gate.py), whose committed record states no owner decision yet. The
tests hold for the record as it stands, so the owner's later approval (every condition
true, in a reviewed commit) passes them too, while any incomplete record is refused: phase
A's refusal rests on the committed record, not on a test. Before a publication,
.github/scripts/pages_check_artifact.py compares the artifact with the committed build;
after it, .github/scripts/pages_check_site.py compares the site with it.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import http.server
import importlib.util
import json
import re
import tarfile
import threading
import time
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
ARTIFACT = _script("pages_check_artifact")
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
        decided_on=GATE.FIRST_DECISION_DAY.isoformat(),
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


def test_a_decision_day_before_m9_or_after_today_is_refused() -> None:
    before = _without("decided_on", "2026-10-17")
    assert any("is not between 2026-10-18 and today" in p for p in _open(before))
    day = GATE.FIRST_DECISION_DAY
    assert GATE.record_problems(_approved(), VERSION, day) == []
    later = _without("decided_on", "2026-10-19")
    assert any("is not between" in p for p in GATE.record_problems(later, VERSION, day))
    assert any("is not between" in p for p in _open(_without("decided_on", "9999-12-31")))


@pytest.mark.parametrize("notes", [{"publication_approved": False}, ["x"], 1, None])
def test_notes_are_plain_text(notes: object) -> None:
    assert "the record's notes are not plain text" in _open(_without("notes", notes))
    record = _approved()
    del record["notes"]
    assert _open(record) == []


def _phase_a() -> dict[str, Any]:
    """The record as M9 phase A committed it: no decision."""
    record = _approved()
    record["conditions"] = dict.fromkeys(GATE.CONDITIONS, False)
    record.update(release_version=None, decided_by=None, decided_on=None, approval_reference=None)
    return record


def test_the_command_follows_the_record(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    common = ["--confirm", GATE.PHRASE, "--switch", "approved", "--root", str(REPO_ROOT)]
    # The committed record: refused while any decision is open (phase A), accepted once the
    # owner's complete approval is committed.
    expected = 0 if _open(GATE.load_record(RECORD)) == [] else 1
    assert GATE.main(["--record", str(RECORD), "--ref", "refs/heads/main", *common]) == expected
    capsys.readouterr()
    undecided = tmp_path / "phase-a.json"
    undecided.write_text(json.dumps(_phase_a()), encoding="utf-8")
    assert GATE.main(["--record", str(undecided), "--ref", "refs/heads/main", *common]) == 1
    err = capsys.readouterr().err
    assert "publication refused" in err
    for name in GATE.CONDITIONS:
        assert f"condition not met: {name}" in err
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
    # The publication job checks the artifact on its own runner before it publishes it,
    # and the published site after.
    deploy = re.findall(r"- name: (.+)", jobs["deploy"])
    assert deploy == [
        "Check out repository (the committed site manifest and the checks)",
        "Download the artifact to publish",
        "The artifact is the committed build (every file, size and SHA-256, nothing else)",
        "Publish the artifact the build job verified",
        "The published site serves every file of the committed build, byte for byte",
    ]
    assert "name: github-pages" in jobs["deploy"].split("actions/download-artifact", 1)[1]
    assert "pages_check_artifact.py --manifest web/atlas-static-site.json" in jobs["deploy"]
    assert "pages_check_site.py --manifest web/atlas-static-site.json" in jobs["deploy"]
    assert "npm" not in "\n".join(line.split("#", 1)[0] for line in jobs["deploy"].splitlines())
    # A pull request's group is its own (a fork's branch named main cannot cancel main's
    # run), and so is a dispatch's (no push can replace a pending publication).
    top = text.split("\njobs:\n", 1)[0]
    assert "format('pr-{0}', github.event.pull_request.number)" in top
    assert (
        "github.event_name == 'workflow_dispatch' && format('dispatch-{0}', github.run_id)" in top
    )


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
        "npx vitest run build/verifiers.test.ts",
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


def test_a_cut_off_answer_is_fetched_again(tmp_path: Path) -> None:
    """A truncated body (IncompleteRead) is a difference to retry, not a crash."""
    body = b'{"a": 1}'
    manifest = {"files": {"data/x.json": {"sha256": hashlib.sha256(body).hexdigest(), "bytes": 8}}}
    committed = json.dumps(manifest).encode()
    answers = {"cut": 1}

    class Handler(_Quiet):
        def do_GET(self) -> None:
            payload = committed if self.path.endswith("atlas-static-site.json") else body
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.path.endswith("x.json") and answers["cut"] > 0:
                answers["cut"] -= 1
                self.wfile.write(payload[:3])
                self.close_connection = True
                return
            self.wfile.write(payload)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        path = tmp_path / "atlas-static-site.json"
        path.write_bytes(committed)
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        arguments = ["--manifest", str(path), "--url", url, "--allow-http", "--wait", "0"]
        assert CHECK.main([*arguments, "--attempts", "2"]) == 0
        answers["cut"] = 1
        assert CHECK.main([*arguments, "--attempts", "1"]) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_the_check_stops_at_its_deadline(
    site: tuple[Path, Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    root, committed, url = site
    (root / "index.html").unlink()
    start = time.monotonic()
    arguments = ["--manifest", str(committed), "--url", url, "--allow-http"]
    assert CHECK.main([*arguments, "--attempts", "50", "--wait", "1", "--deadline", "2"]) == 1
    assert time.monotonic() - start < 10
    assert "stopped at the deadline" in capsys.readouterr().err


# --- The check of the artifact before publication ----------------------------------------


@pytest.fixture
def build(tmp_path: Path) -> tuple[Path, Path]:
    """A tiny static build and its committed manifest."""
    root = tmp_path / "build"
    (root / "assets").mkdir(parents=True)
    files = {"index.html": b"<!doctype html>", "assets/app.js": b"x()"}
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
    return root, committed


def _tar(root: Path, archive: Path, extra: list[tarfile.TarInfo] | None = None) -> Path:
    """The archive upload-pages-artifact writes (``tar --directory root -cf archive .``)."""
    with tarfile.open(archive, "w") as tar:
        tar.add(root, arcname=".")
        for member in extra or []:
            tar.addfile(member)
    return archive


def _artifact(committed: Path, archive: Path) -> int:
    return int(ARTIFACT.main(["--manifest", str(committed), "--artifact", str(archive)]))


def test_the_committed_build_as_an_artifact_passes(
    build: tuple[Path, Path], tmp_path: Path
) -> None:
    root, committed = build
    assert _artifact(committed, _tar(root, tmp_path / "artifact.tar")) == 0


def test_every_difference_of_the_artifact_is_named(
    build: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, committed = build
    (root / "assets" / "app.js").write_bytes(b"y()")
    (root / "index.html").unlink()
    (root / "extra.html").write_bytes(b"<p>not built</p>")
    (root / "more").mkdir()
    link = tarfile.TarInfo("./assets/link.js")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    outside = tarfile.TarInfo("../outside.txt")
    assert _artifact(committed, _tar(root, tmp_path / "a.tar", [link, outside])) == 1
    err = capsys.readouterr().err
    for problem in (
        "altered: assets/app.js",
        "missing: index.html",
        "not in the committed build: extra.html",
        "a directory the build does not have: more",
        "not a regular file: assets/link.js",
        "a path outside the site: ../outside.txt",
    ):
        assert problem in err, problem


def test_the_artifact_must_carry_the_committed_manifest(
    build: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, committed = build
    (root / "atlas-static-site.json").write_text("{}", encoding="utf-8")
    assert _artifact(committed, _tar(root, tmp_path / "a.tar")) == 1
    assert "atlas-static-site.json differs from the committed" in capsys.readouterr().err
    (root / "atlas-static-site.json").unlink()
    assert _artifact(committed, _tar(root, tmp_path / "b.tar")) == 1
    assert "missing: atlas-static-site.json" in capsys.readouterr().err
    (tmp_path / "c.tar").write_bytes(b"not a tar")
    assert _artifact(committed, tmp_path / "c.tar") == 1
    assert "cannot be read" in capsys.readouterr().err
