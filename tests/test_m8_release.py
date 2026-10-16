"""Release checks (M8): the version and the CI action pins.

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

import json
import re
import tomllib
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
    pins = _action_pins()
    assert {action for _, action, _, _ in pins} == set(FIRST_NODE24_MAJOR)
    malformed = [
        pin
        for pin in pins
        if re.fullmatch(r"[0-9a-f]{40}", pin[2]) is None
        or pin[3] is None
        or MAJOR_MINOR_PATCH.fullmatch(pin[3].removeprefix("v")) is None
    ]
    assert malformed == []
    # One release of each action across all workflows.
    assert len({(action, ref, tag) for _, action, ref, tag in pins}) == len(FIRST_NODE24_MAJOR)


def test_no_action_runs_on_the_deprecated_node20_runtime() -> None:
    old = [
        (workflow, action, tag)
        for workflow, action, _, tag in _action_pins()
        if tag is None or int(tag.removeprefix("v").split(".")[0]) < FIRST_NODE24_MAJOR[action]
    ]
    assert old == []
