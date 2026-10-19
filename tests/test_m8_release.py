"""Release checks (M8): the version, the CI action pins and the reproduction comparison.

The version is declared by hand in ``pyproject.toml``, ``src/atlas/__init__.py`` and
``web/package.json``; the uv and npm locks, the committed dashboard build and the committed
API contract carry copies. Without these tests a partial bump passes the fast tests and
fails late (build-release.sh in the container test or on a server) or never (a dashboard
left on its old version). The release id, the dashboard manifest and build-release.sh
accept only MAJOR.MINOR.PATCH, so the code carries the version being released and a
release candidate is marked outside it: in the release-candidate manifest and a
``v<version>-rc.<n>`` tag (docs/DEVELOPMENT.md, "Versioning").
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import tomllib
from pathlib import Path
from typing import Any

import pytest

import atlas
from atlas.deploy.layout import valid_release_id
from tests.conftest import REPO_ROOT

#: Semantic-versioning core: three numbers without leading zeros, no suffix.
MAJOR_MINOR_PATCH = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def _json(relative: str) -> dict[str, Any]:
    document = json.loads((REPO_ROOT / relative).read_text(encoding="utf-8"))
    assert isinstance(document, dict), relative
    return document


def _toml(relative: str) -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / relative).read_text(encoding="utf-8"))


def _declared_versions() -> dict[str, object]:
    npm_lock = _json("web/package-lock.json")
    uv_lock = [p for p in _toml("uv.lock")["package"] if p["name"] == "atlas"]
    assert len(uv_lock) == 1
    return {
        "pyproject.toml [project].version": _toml("pyproject.toml")["project"]["version"],
        "src/atlas/__init__.py __version__": atlas.__version__,
        "uv.lock atlas version": uv_lock[0]["version"],
        "web/package.json version": _json("web/package.json")["version"],
        "web/package-lock.json version": npm_lock["version"],
        'web/package-lock.json packages[""].version': npm_lock["packages"][""]["version"],
        "web/dist/atlas-dashboard.json version": _json("web/dist/atlas-dashboard.json")["version"],
        "docs/api/openapi.json info.version": _json("docs/api/openapi.json")["info"]["version"],
    }


def test_every_version_declaration_is_the_same() -> None:
    versions = _declared_versions()
    expected = versions["pyproject.toml [project].version"]
    differing = {name: version for name, version in versions.items() if version != expected}
    assert differing == {}, f"each of these must equal pyproject.toml's {expected!r}"


def test_every_version_declaration_is_major_minor_patch() -> None:
    """No pre-release or build suffix: the deployment and dashboard validators refuse one."""
    malformed = {
        name: version
        for name, version in _declared_versions().items()
        if not isinstance(version, str) or MAJOR_MINOR_PATCH.fullmatch(version) is None
    }
    assert malformed == {}
    assert valid_release_id(f"{atlas.__version__}-0123456789ab")


@pytest.mark.parametrize("label", ["1.0.0-rc.1", "1.0.0rc1", "v1.0.0-rc.1"])
def test_a_release_candidate_label_cannot_be_the_version(label: str) -> None:
    """The candidate label belongs to the tag; as a version it would not even name a release."""
    assert MAJOR_MINOR_PATCH.fullmatch(label) is None
    assert not valid_release_id(f"{label}-0123456789ab")


# --- GitHub Actions pins ------------------------------------------------------------------

#: The first major release of each action whose action.yml runs on Node.js 24; GitHub has
#: deprecated the Node.js 20 runtime of its runners (M8 audit, finding M8-32).
FIRST_NODE24_MAJOR = {
    "actions/checkout": 5,
    "actions/setup-python": 6,
    "actions/setup-node": 5,
    "actions/upload-artifact": 6,
    # (M9) deploy-pages 4.x runs on node20, 5.0.0 on node24; upload-pages-artifact is a
    # composite action whose 4.x calls upload-artifact 4.6.2 (node20), 5.0.0 7.0.0 (node24).
    "actions/deploy-pages": 5,
    "actions/upload-pages-artifact": 5,
    # (M9) download-artifact 4.x to 6.x run on node20, 7.0.0 on node24.
    "actions/download-artifact": 7,
}
_USES = re.compile(r"\s*(?:-\s+)?uses:\s*(?P<action>[^@\s]+)@(?P<ref>\S+)(?:\s+#\s*(?P<tag>\S+))?")


def _action_pins() -> list[tuple[str, str, str, str | None]]:
    """(workflow, action, ref, version comment) for every ``uses:`` in every workflow."""
    pins: list[tuple[str, str, str, str | None]] = []
    for workflow in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            match = _USES.match(line)
            if match:
                pins.append((workflow.name, match["action"], match["ref"], match["tag"]))
    return pins


def test_every_action_is_pinned_to_a_commit_with_its_version() -> None:
    """Every ``uses:`` names a full commit SHA and, in a comment, the release it stands for.

    Whether the SHA really is the commit of that release cannot be checked offline; the
    update procedure in docs/DEVELOPMENT.md ("Dependency maintenance") checks it.
    """
    pins = _action_pins()
    used = {action for _, action, _, _ in pins}
    assert sorted(used - set(FIRST_NODE24_MAJOR)) == [], (
        "add each new action to FIRST_NODE24_MAJOR, with the first major release whose "
        "action.yml runs on Node.js 24"
    )
    assert sorted(set(FIRST_NODE24_MAJOR) - used) == [], (
        "no workflow uses these actions any more; remove them from FIRST_NODE24_MAJOR"
    )
    malformed = [
        pin
        for pin in pins
        if re.fullmatch(r"[0-9a-f]{40}", pin[2]) is None
        or pin[3] is None
        or MAJOR_MINOR_PATCH.fullmatch(pin[3].removeprefix("v")) is None
    ]
    assert malformed == []
    releases = {(action, ref, tag) for _, action, ref, tag in pins}
    assert len(releases) == len(used), "every workflow must pin the same release of an action"


def test_no_action_runs_on_the_deprecated_node20_runtime() -> None:
    """Judged by the version comment; the previous test and the update procedure in
    docs/DEVELOPMENT.md tie the comment to the pinned SHA."""
    old = [
        (workflow, action, tag)
        for workflow, action, _, tag in _action_pins()
        if action not in FIRST_NODE24_MAJOR
        or tag is None
        or int(tag.removeprefix("v").split(".")[0]) < FIRST_NODE24_MAJOR[action]
    ]
    assert old == []


def test_the_other_engines_run_against_the_api_alone_in_ci() -> None:
    """CI's Firefox and WebKit step (M8) runs the API-backed specs with both switches.

    Without ATLAS_E2E_ALL_BROWSERS the firefox and webkit projects do not exist; without
    ATLAS_E2E_API_ONLY Playwright also starts both nginx stacks, which none of their tests use
    (web/playwright.config.ts).
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    name = "- name: The API-backed pages and failure states in Firefox and WebKit (M8)"
    assert workflow.count(name) == 1
    step = workflow.split(name, 1)[1].split("\n      - name:", 1)[0]
    assert 'ATLAS_E2E_ALL_BROWSERS: "1"' in step
    assert 'ATLAS_E2E_API_ONLY: "1"' in step
    assert "npx playwright test --project firefox --project webkit" in step
    config = (REPO_ROOT / "web" / "playwright.config.ts").read_text(encoding="utf-8")
    assert "process.env.ATLAS_E2E_ALL_BROWSERS === '1'" in config
    assert "process.env.ATLAS_E2E_API_ONLY === '1'" in config


# --- The comparison the manual reproduction workflow uses ---------------------------------

M2_PROJECTION = REPO_ROOT / "docs" / "results" / "atlas-m2-rolling-origin-v1" / "projection"
LEGACY_HOLDOUT = REPO_ROOT / "docs" / "results" / "legacy-one-step-holdout"


def _compare(committed: Path, regenerated: Path) -> int:
    """Exit status of .github/scripts/same_outputs.py for two run directories."""
    path = REPO_ROOT / ".github" / "scripts" / "same_outputs.py"
    spec = importlib.util.spec_from_file_location("same_outputs", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    status = module.main(["same_outputs.py", str(committed), str(regenerated)])
    assert isinstance(status, int)
    return status


def _copy_run(source: Path, target: Path) -> Path:
    shutil.copytree(source, target)
    return target


def test_same_outputs_accepts_an_identical_regeneration(tmp_path: Path) -> None:
    regenerated = _copy_run(M2_PROJECTION, tmp_path / "regenerated")
    assert _compare(M2_PROJECTION, regenerated) == 0


def test_same_outputs_refuses_a_different_output_hash(tmp_path: Path) -> None:
    regenerated = _copy_run(LEGACY_HOLDOUT, tmp_path / "regenerated")
    manifest = json.loads((regenerated / "manifest.json").read_text(encoding="utf-8"))
    manifest["outputs"]["legacy_one_step_holdout.csv"] = "0" * 64
    (regenerated / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert _compare(LEGACY_HOLDOUT, regenerated) == 1


def test_same_outputs_refuses_an_altered_committed_file(tmp_path: Path) -> None:
    committed = _copy_run(LEGACY_HOLDOUT, tmp_path / "committed")
    data = committed / "legacy_one_step_holdout.json"
    data.write_bytes(data.read_bytes().replace(b"\n", b"\r\n"))
    assert _compare(committed, LEGACY_HOLDOUT) == 1


def test_same_outputs_refuses_a_regenerated_file_that_its_manifest_does_not_describe(
    tmp_path: Path,
) -> None:
    """The regenerated manifest is not trusted on its own: each file it names is hashed."""
    regenerated = _copy_run(LEGACY_HOLDOUT, tmp_path / "regenerated")
    data = regenerated / "legacy_one_step_holdout.csv"
    data.write_bytes(data.read_bytes().replace(b"\n", b"\r\n"))
    assert _compare(LEGACY_HOLDOUT, regenerated) == 1
    data.unlink()
    assert _compare(LEGACY_HOLDOUT, regenerated) == 1
