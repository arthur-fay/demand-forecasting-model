"""Write or check the ATLAS release-candidate manifest (M8; schema 2 since 1.1.0-rc.1).

The manifest fingerprints the committed content of a release candidate: every tracked file (its
path, its mode and the SHA-256 of its committed content), the dependency locks, every
declaration of the version, the data snapshot, the committed research results, the committed
dashboard build, the static export and site (M9) with the publication record, and the API
contract, together with the scientific status those results record. It is generated as the last
step of preparing a candidate, so the candidate commit is the commit that adds it; a reviewer
checks a checkout against it with ``--check``.

Schema 2 (from 1.1.0-rc.1) adds the sections ``versions``, ``static_site`` and ``candidates``
to schema 1 (1.0.0-rc.1, M8), names the candidate's own release documents after its version
(``RELEASE_GATE_<version>.md``, ``RELEASE_CANDIDATE_<version>.md``) and declares more of its
scientific status. A manifest is checked with the tool of the commit it was generated from:
1.0.0-rc.1's with the tool of the M8 merge (``7d3639c``), which keeps it as it was issued.

What is verified:

- Every section is computed from the content git has committed (``git ls-files -s`` and
  ``git cat-file --batch``), never from the bytes in the working tree, and every path is git's
  own ('/' separators). A checkout whose line endings git converted (``core.autocrlf=true``,
  the Git for Windows default) therefore verifies like any other.
- ``--write`` and ``--check`` first require a clean checkout, by git's own comparison
  (``git status``, which honours ``core.autocrlf`` and ``.gitattributes``): no modified,
  staged, deleted, renamed, unmerged or untracked file apart from the manifest itself, and no
  tracked file hidden from ``git status`` (assume-unchanged or skip-worktree, so a sparse
  checkout is refused). Otherwise the tool names the paths (the first 50, then the total) and
  exits 1. Ignored files (``.venv``, ``node_modules``) are neither fingerprinted nor reported,
  so a candidate is best verified in a fresh clone. When the manifest itself is not the
  committed one, a passing ``--check`` says so.
- ``--check`` names the added, removed or changed files and the mode changes (the first 50,
  then the total), then the sections that differ.
- The scientific status separates what the committed results record (``recorded``) from what
  the release declares (``declared``); the candidate's own status is declared as well. The
  M2, M3 and M4 results must record one data fingerprint, computed from the committed snapshot
  manifest; otherwise the tool refuses to write or check.
- Every declaration of the version (docs/DEVELOPMENT.md, "Versioning") must equal
  ``pyproject.toml``'s: the package, the two locks, the dashboard build, the API contract, the
  static export (its manifest and every recorded answer) and the static site's manifest. The
  static site's manifest must name the committed export manifest, and the export manifest must
  list exactly the committed answers with their SHA-256. Otherwise the tool refuses as well.
- Earlier candidates' manifests committed beside this one (``docs/release/atlas-*-rc.*.json``)
  are recorded by their SHA-256, tree fingerprint and commit, never rewritten.

Usage (Python 3.11 or later, standard library only, and git)::

    python3 docs/release/rc_manifest.py --write   # (re)generate docs/release/<candidate>.json
    python3 docs/release/rc_manifest.py --check   # exit 1 if the checkout differs from it

``--root DIR`` works on the checkout at DIR instead of the one containing this script.

Exit status: 0 the checkout matches the manifest (or the manifest was written); 1 the checkout
is not clean, its manifest is missing or is not a JSON object, or the checkout differs from
it; 2 the tool cannot run (Python older than 3.11, git missing or unable to use the directory,
not the top level of a git checkout, or committed records that are missing or contradict each
other).

The manifest excludes itself from the file map (a file cannot contain its own hash) and records
the commit it was generated from only for information: what identifies the candidate is the
content, not the commit that happened to be checked out.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import posixpath
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = "rc.1"
SCHEMA = "atlas-release-candidate/2"
#: Fingerprinted separately as well as in the file map (what a reviewer compares first).
LOCKS = (
    "uv.lock",
    "pyproject.toml",
    ".python-version",
    "web/package-lock.json",
    "web/package.json",
    "web/.npmrc",
    ".github/ci-requirements.txt",
)
AREAS = (
    "src/atlas",
    "tests",
    "web/src",
    "web/dist",
    "web/e2e",
    "deploy",
    "configs",
    "snapshots",
    "docs/results",
    "docs/api",
    "docs/release",
    "web/static-export",
    "web/build",
    "web/scripts",
    ".github",
    "01 Data",
    "02 Notebooks",
    "03 SQL",
    "04 Power BI",
    "05 Screenshots",
    "06 Exports",
)
PUBLICATION_RECORD = "docs/release/PAGES_PUBLICATION.json"
#: The documents every candidate names besides its own gate and notes (``release_documents``).
DOCUMENTS = {
    "known_limitations": "docs/KNOWN_LIMITATIONS.md",
    "model_card": "docs/MODEL_CARD.md",
    "data_card": "docs/DATA_CARD.md",
    "audit": "docs/audit/README.md",
    "phase_b_runbook": "docs/release/VPS_PHASE_B_RUNBOOK.md",
    "static_demo": "docs/STATIC_DEMO.md",
    "code_and_data_terms": "docs/OPEN_SOURCE_AND_DATA_TERMS.md",
    "nyc_311_reuse_review": "docs/release/NYC_311_REUSE_REVIEW_2026-10-20.md",
    "publication_record": PUBLICATION_RECORD,
}
RESULTS = "docs/results"
DIST = "web/dist"
CONTRACT = "docs/api/openapi.json"
INIT = "src/atlas/__init__.py"
STATIC_EXPORT = "web/static-export"
STATIC_EXPORT_MANIFEST = f"{STATIC_EXPORT}/atlas-static-export.json"
STATIC_SITE = "web/atlas-static-site.json"
#: The manifests of candidates (this one's and earlier ones'), in docs/release.
CANDIDATE_MANIFEST = re.compile(r"docs/release/atlas-[0-9]+\.[0-9]+\.[0-9]+-rc\.[0-9]+\.json")
SNAPSHOT_MANIFEST = "snapshots/legacy-v0/manifest.json"
M2_RECOMMENDATION = "docs/results/atlas-m2-rolling-origin-v1/full/recommendation.json"
M2_PROJECTION = "docs/results/atlas-m2-rolling-origin-v1/projection"
M3_STATUS = "docs/results/atlas-m3-intervals-v1/full/calibration_status.json"
M4_CONFIG = "configs/optimization/atlas-m4-optimization-v1.json"
M4_MANIFEST = "docs/results/atlas-m4-optimization-v1/manifest.json"
#: Run manifests that must record the data fingerprint (every other one that records it must
#: agree with them).
DATA_RECORDS = (
    "docs/results/atlas-m2-rolling-origin-v1/full/manifest.json",
    "docs/results/atlas-m3-intervals-v1/full/manifest.json",
    M4_MANIFEST,
)
GITLINK = "160000"
#: Differences and unclean paths printed before the total.
SHOWN = 50


class ManifestError(Exception):
    """The tool cannot run, or the committed records are missing or inconsistent (exit 2)."""


#: What reading a committed record of an unexpected shape raises (exit 2 as well).
MALFORMED = (AttributeError, KeyError, IndexError, TypeError, ValueError)


def git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(root), *args],  # noqa: S607
            input=stdin,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ManifestError(f"cannot run git: {exc}") from None
    if result.returncode != 0:
        lines = result.stderr.decode("utf-8", "replace").strip().splitlines()
        # The 'fatal:' line says what failed; hints (such as safe.directory's) may follow it.
        fatal = next((line for line in lines if line.startswith("fatal:")), None)
        reason = fatal or (lines[-1] if lines else f"exit status {result.returncode}")
        command = next(a for a in args if not a.startswith("-"))
        raise ManifestError(f"git {command} failed: {reason}")
    return result.stdout


def require_checkout(root: Path) -> None:
    """Fail unless git is available and ``root`` is the top level of a git checkout."""
    if shutil.which("git") is None:
        raise ManifestError("git is required but was not found on PATH")
    if not root.is_dir():
        raise ManifestError(f"{root} is not a directory")
    try:
        up = git(root, "rev-parse", "--show-cdup").strip()
    except ManifestError as exc:
        raise ManifestError(f"{root} is not a usable git checkout ({exc})") from None
    if up:
        raise ManifestError(f"{root} is not the top level of its git checkout")


class Committed:
    """The committed content of a checkout: every index entry (path, mode, blob), read in one
    ``git cat-file --batch`` call. A clean checkout's index is its commit."""

    def __init__(self, root: Path) -> None:
        self.entries: dict[str, tuple[str, str]] = {}
        for record in git(root, "ls-files", "-s", "-z").decode("utf-8").split("\0"):
            if not record:
                continue
            info, path = record.split("\t", 1)
            mode, oid, stage = info.split(" ")
            if stage != "0":
                raise ManifestError(f"{path} has an unresolved merge conflict")
            if mode == GITLINK:
                raise ManifestError(f"{path} is a submodule, whose content cannot be fingerprinted")
            self.entries[path] = (mode, oid)
        oids = sorted({oid for _, oid in self.entries.values()})
        batch = git(root, "cat-file", "--batch", stdin="".join(f"{o}\n" for o in oids).encode())
        self.blobs: dict[str, bytes] = {}
        self.digests: dict[str, str] = {}
        position = 0
        for oid in oids:
            end = batch.index(b"\n", position)
            header = batch[position:end].decode("utf-8").split(" ")
            if header[:2] != [oid, "blob"]:
                raise ManifestError(f"git cat-file cannot read blob {oid}: {' '.join(header)}")
            size = int(header[2])
            self.blobs[oid] = batch[end + 1 : end + 1 + size]
            position = end + 1 + size + 1

    def content(self, path: str) -> bytes:
        if path not in self.entries:
            raise ManifestError(f"{path} is not committed")
        return self.blobs[self.entries[path][1]]

    def text(self, path: str) -> str:
        return self.content(path).decode("utf-8")

    def json(self, path: str) -> Any:
        try:
            return json.loads(self.content(path))
        except ValueError as exc:
            raise ManifestError(f"{path} is not valid JSON: {exc}") from None

    def digest(self, oid: str) -> str:
        if oid not in self.digests:
            self.digests[oid] = hashlib.sha256(self.blobs[oid]).hexdigest()
        return self.digests[oid]

    def sha256(self, path: str) -> str:
        self.content(path)
        return self.digest(self.entries[path][1])

    def under(self, directory: str) -> list[str]:
        return sorted(p for p in self.entries if p.startswith(directory + "/"))


def pyproject(tree: Committed) -> dict[str, Any]:
    import tomllib  # Python 3.11 or later; main() checks the version first

    return tomllib.loads(tree.text("pyproject.toml"))


def version(tree: Committed) -> str:
    return str(pyproject(tree)["project"]["version"])


def manifest_name(tree: Committed) -> str:
    return f"docs/release/atlas-{version(tree)}-{CANDIDATE}.json"


def release_documents(release: str) -> dict[str, str]:
    """The candidate's documents: its own release gate and notes, named after its version, then
    DOCUMENTS. (The M8 candidate, 1.0.0-rc.1, was issued with ``RELEASE_GATE.md`` and
    ``RELEASE_CANDIDATE.md``; they stay as its historical record.)"""
    return {
        "release_gate": f"docs/release/RELEASE_GATE_{release}.md",
        "release_candidate": f"docs/release/RELEASE_CANDIDATE_{release}.md",
        **DOCUMENTS,
    }


def _state(code: str) -> str:
    """The state a ``git status --porcelain=v1`` code (index, then working tree) stands for."""
    if code == "??":
        return "untracked"
    if "U" in code or code in ("AA", "DD"):
        return "unmerged"
    if "D" in code:
        return "deleted"
    if "R" in code:
        return "renamed"
    if "C" in code:
        return "copied"
    # 'A ' staged, ' A' intended (git add -N)
    return "added, not committed" if "A" in code else "modified"


def unclean(root: Path) -> list[tuple[str, str]]:
    """``(state, path)`` of everything that keeps the checkout from being its commit.

    ``git status`` compares the working tree with the commit the way git itself does, so files
    git converts at checkout (``core.autocrlf``) are clean. Ignored files are not listed. Index
    entries marked assume-unchanged or skip-worktree are listed as well, because ``git status``
    does not compare them.
    """
    found: list[tuple[str, str]] = []
    fields = git(
        root,
        *("--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=all"),
    ).decode("utf-8")
    records = iter(fields.split("\0"))
    for record in records:
        if record:
            code, path = record[:2], record[3:]
            if "R" in code or "C" in code:
                path = f"{next(records)} -> {path}"
            found.append((_state(code), path))
    # ls-files -v tags: 'S' skip-worktree, lower case assume-unchanged ('H' cached and 'M'
    # unmerged are compared by git status above).
    for record in git(root, "ls-files", "-v", "-z").decode("utf-8").split("\0"):
        tag, path = record[:1], record[2:]
        if tag in ("S", "s"):
            found.append(("hidden from git status (skip-worktree)", path))
        elif tag.islower():
            found.append(("hidden from git status (assume-unchanged)", path))
    return found


def fingerprint(entries: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(entries):
        digest.update(f"{path}\0{entries[path]}\n".encode())
    return digest.hexdigest()


def file_map(tree: Committed, exclude: str) -> dict[str, str]:
    """``path -> 'mode sha256'`` of every committed file except ``exclude``."""
    return {
        path: f"{mode} {tree.digest(oid)}"
        for path, (mode, oid) in sorted(tree.entries.items())
        if path != exclude
    }


def run_manifests(tree: Committed) -> list[str]:
    return [p for p in tree.under(RESULTS) if posixpath.basename(p) == "manifest.json"]


def research_artifacts(tree: Committed) -> list[dict[str, Any]]:
    runs = []
    for manifest in run_manifests(tree):
        record = tree.json(manifest)
        run_dir = posixpath.dirname(manifest)
        files = {
            p: tree.sha256(p)
            for p in tree.under(run_dir)
            if posixpath.dirname(p) == run_dir and p != manifest
        }
        runs.append(
            {
                "directory": run_dir,
                "manifest_sha256": tree.sha256(manifest),
                "record_type": record.get("record_type"),
                "run_id": record.get("run_id"),
                "config_fingerprint": record.get("config_fingerprint"),
                "result_fingerprint": record.get("result_fingerprint"),
                "files": files,
            }
        )
    return runs


def dashboard_build(tree: Committed) -> dict[str, Any]:
    manifest = f"{DIST}/atlas-dashboard.json"
    return {
        "manifest": manifest,
        "manifest_sha256": tree.sha256(manifest),
        "version": tree.json(manifest).get("version"),
        "files": {p: tree.sha256(p) for p in tree.under(DIST)},
    }


def api_contract(tree: Committed) -> dict[str, Any]:
    contract = tree.json(CONTRACT)
    operations = sum(
        1
        for item in contract["paths"].values()
        for method in item
        if method in ("get", "post", "put", "patch", "delete")
    )
    return {
        "path": CONTRACT,
        "sha256": tree.sha256(CONTRACT),
        "info_version": contract["info"]["version"],
        "openapi": contract["openapi"],
        "operations": operations,
    }


def data_fingerprint(tree: Committed, snapshot_id: str) -> tuple[str, list[str]]:
    """The one data fingerprint the committed results record, and the records that state it.

    The M2 recommendation and every run manifest that records a data fingerprint (the M2, M3
    and M4 ones must) have to agree, and each run must name the committed snapshot manifest.
    """
    snapshot_sha256 = tree.sha256(SNAPSHOT_MANIFEST)
    reference = tree.json(M2_RECOMMENDATION).get("data_fingerprint")
    if not isinstance(reference, str):
        raise ManifestError(f"{M2_RECOMMENDATION} records no data fingerprint")
    recorded = {M2_RECOMMENDATION: reference}
    for path in sorted({*run_manifests(tree), *DATA_RECORDS}):
        data = tree.json(path).get("data")
        if not isinstance(data, dict) or "data_fingerprint" not in data:
            if path in DATA_RECORDS:
                raise ManifestError(f"{path} records no data fingerprint")
            continue
        recorded[path] = data["data_fingerprint"]
        if (data.get("snapshot_id"), data.get("manifest_sha256")) != (snapshot_id, snapshot_sha256):
            raise ManifestError(f"{path} was not computed from the committed {SNAPSHOT_MANIFEST}")
    differing = [f"{p} records {v}" for p, v in sorted(recorded.items()) if v != reference]
    if differing:
        raise ManifestError(
            f"the committed results record different data fingerprints: {M2_RECOMMENDATION} "
            f"records {reference}, but {'; '.join(differing)}"
        )
    return reference, sorted(recorded)


def snapshot(tree: Committed) -> dict[str, Any]:
    manifest = tree.json(SNAPSHOT_MANIFEST)
    provenance = manifest["provenance"]
    recorded, records = data_fingerprint(tree, manifest["snapshot_id"])
    return {
        "snapshot_id": manifest["snapshot_id"],
        "manifest": SNAPSHOT_MANIFEST,
        "manifest_sha256": tree.sha256(SNAPSHOT_MANIFEST),
        "data_fingerprint": recorded,
        "data_fingerprint_recorded_in": records,
        "source": f"{provenance['source_publisher']}: {provenance['source_name']} "
        f"(dataset {provenance['source_dataset_id']})",
        "window": [
            provenance["extraction_window_start"],
            provenance["extraction_window_end_exclusive"],
        ],
        "extracted_at": provenance["extracted_at"],
        "completeness": provenance["completeness"],
        "terms_of_use": provenance["terms_of_use"],
        "files": len(manifest["files"]),
        "data_rows": sum(int(f.get("rows") or 0) for f in manifest["files"]),
    }


def provenances(node: Any) -> list[str]:
    """Every ``provenance`` value declared anywhere in a configuration.

    These are declarations, not distinct parameters: in the M4 configuration they cover the
    base quantities, each scenario adjustment and each sensitivity sweep, so a parameter that
    several scenarios adjust is counted once for each.
    """
    if isinstance(node, dict):
        found = [str(node["provenance"])] if "provenance" in node else []
        return found + [v for child in node.values() for v in provenances(child)]
    if isinstance(node, list):
        return [v for child in node for v in provenances(child)]
    return []


def projection(tree: Committed) -> dict[str, Any]:
    manifest = tree.json(f"{M2_PROJECTION}/manifest.json")
    rows = list(csv.DictReader(io.StringIO(tree.text(f"{M2_PROJECTION}/projections.csv"))))
    targets = sorted(row["target_date"] for row in rows)
    return {
        "run_dir": M2_PROJECTION,
        "record_type": manifest["record_type"],
        "model_ids": sorted({row["model_id"] for row in rows}),
        "origin": manifest["origin"],
        "data_last_date": manifest["data"]["last_date"],
        "target_dates": [targets[0], targets[-1]] if targets else [],
        "rows": len(rows),
        "label": manifest["label"],
    }


def scientific_status(tree: Committed) -> dict[str, Any]:
    recommendation = tree.json(M2_RECOMMENDATION)
    tests = {
        t["hypothesis"].split(":")[0]: {
            "holm_adjusted_p_value": round(t["holm_adjusted_p_value"], 4),
            "mae_difference_ci": [
                round(t["mae_difference_ci_low"], 1),
                round(t["mae_difference_ci_high"], 1),
            ],
        }
        for t in recommendation["recommendation"]["primary_tests"]
    }
    calibration = tree.json(M3_STATUS)
    m4 = tree.json(M4_MANIFEST)
    declarations = Counter(provenances(tree.json(M4_CONFIG)))
    if not declarations:
        raise ManifestError(f"{M4_CONFIG} declares no parameter provenance")
    return {
        "recorded": {
            "sources": {
                "m2_recommendation": M2_RECOMMENDATION,
                "m2_projection": f"{M2_PROJECTION}/manifest.json",
                "m3_status": M3_STATUS,
                "m4_config": M4_CONFIG,
                "m4_manifest": M4_MANIFEST,
            },
            "m2_recommended_model": recommendation["recommendation"]["recommended_model"],
            "m2_primary_tests": tests,
            "m2_projection": projection(tree),
            "m3_interval_status": calibration["decision_status"]["interval_status"],
            "m3_decision_grade": calibration["decision_status"]["decision_grade"],
            # How many provenance declarations carry each value (not a count of parameters).
            "m4_provenance_declarations": dict(sorted(declarations.items())),
            "m4_record_type": m4["record_type"],
            "m4_validated_recommendation": m4["validated_recommendation"],
        },
        "declared": {
            "m2_selection_evidence": "weak: the development selection test over-rejects at "
            "h=30 (M8 erratum, docs/BACKTESTING.md)",
            "m2_advantage": "not established: the lead over the best simple baseline B* is not "
            "significant after Holm, and a correctly sized selection test would most likely "
            "have kept B* (docs/BACKTESTING.md section 12)",
            "forecast": "the M2 projection is archived and has never been scored against "
            "observed demand",
            "m3_intervals": "FAILED_CALIBRATION: no prediction interval or exceedance "
            "probability is validated for operational use (docs/UNCERTAINTY.md)",
            "m4_parameters": "every staffing, cost, productivity and budget parameter is "
            "HYPOTHETICAL (docs/OPTIMIZATION.md)",
            "nature": "independent research software on the frozen legacy-v0 snapshot: not an "
            "official or operational NYC tool, and not affiliated with or endorsed by the City "
            "of New York, NYC 311 or NYC Open Data",
            "validated_recommendation": False,
        },
    }


def versions(tree: Committed, release: str) -> dict[str, Any]:
    """Every declaration of the version, which must all be ``release`` (docs/DEVELOPMENT.md,
    "Versioning"): the three made by hand and every copy of them."""
    import tomllib  # Python 3.11 or later; main() checks the version first

    init = re.findall(r'^__version__ = "([^"]*)"$', tree.text(INIT), flags=re.MULTILINE)
    lock = [
        p.get("version")
        for p in tomllib.loads(tree.text("uv.lock"))["package"]
        if p.get("name") == "atlas"
    ]
    npm = tree.json("web/package-lock.json")
    export = tree.json(STATIC_EXPORT_MANIFEST)
    answers = sorted(
        {
            str(tree.json(f"{STATIC_EXPORT}/{name}")["provenance"]["atlas_version"])
            for name in export["files"]
        }
    )
    declared: dict[str, Any] = {
        "pyproject.toml [project].version": release,
        "src/atlas/__init__.py __version__": init[0] if len(init) == 1 else init,
        "uv.lock atlas version": lock[0] if len(lock) == 1 else lock,
        "web/package.json version": tree.json("web/package.json").get("version"),
        "web/package-lock.json version": npm.get("version"),
        'web/package-lock.json packages[""].version': npm["packages"][""].get("version"),
        f"{DIST}/atlas-dashboard.json version": tree.json(f"{DIST}/atlas-dashboard.json").get(
            "version"
        ),
        f"{CONTRACT} info.version": tree.json(CONTRACT)["info"]["version"],
        f"{STATIC_EXPORT_MANIFEST} atlas_version": export.get("atlas_version"),
        f"{STATIC_EXPORT} answers' provenance.atlas_version": (
            answers[0] if len(answers) == 1 else answers
        ),
        f"{STATIC_SITE} version": tree.json(STATIC_SITE).get("version"),
    }
    differing = [f"{name} is {value!r}" for name, value in declared.items() if value != release]
    if differing:
        raise ManifestError(
            f"the committed version declarations differ from pyproject.toml's {release!r}: "
            f"{'; '.join(differing)}"
        )
    return declared


def static_site(tree: Committed) -> dict[str, Any]:
    """The static export, the static site's manifest and the publication record (M9).

    The export manifest must list exactly the committed answers, with their SHA-256, and the
    site's manifest must name the committed export manifest: the static build refuses anything
    else, and so does this tool. The publication record is recorded as committed, whatever it
    states; only the owner changes it (docs/STATIC_DEMO.md, sections 10 and 12).
    """
    export = tree.json(STATIC_EXPORT_MANIFEST)
    site = tree.json(STATIC_SITE)
    export_sha256 = tree.sha256(STATIC_EXPORT_MANIFEST)
    committed = {
        p: tree.sha256(p) for p in tree.under(STATIC_EXPORT) if p != STATIC_EXPORT_MANIFEST
    }
    listed = {f"{STATIC_EXPORT}/{name}": entry["sha256"] for name, entry in export["files"].items()}
    if listed != committed:
        raise ManifestError(f"{STATIC_EXPORT_MANIFEST} does not list the committed answers")
    if site.get("export_manifest_sha256") != export_sha256:
        raise ManifestError(f"{STATIC_SITE} names another export than {STATIC_EXPORT_MANIFEST}")
    record = tree.json(PUBLICATION_RECORD)
    conditions = record["conditions"]
    return {
        "export": {
            "manifest": STATIC_EXPORT_MANIFEST,
            "manifest_sha256": export_sha256,
            "format": export.get("format"),
            "snapshot": export.get("snapshot"),
            "scientific_status": export.get("scientific_status"),
            "files": committed,
        },
        "site": {
            "manifest": STATIC_SITE,
            "manifest_sha256": tree.sha256(STATIC_SITE),
            "format": site.get("format"),
            "base": site.get("base"),
            "content_security_policy": site.get("content_security_policy"),
            "files": len(site["files"]),
        },
        "publication": {
            "record": PUBLICATION_RECORD,
            "sha256": tree.sha256(PUBLICATION_RECORD),
            "conditions": dict(sorted(conditions.items())),
            "every_condition_true": all(value is True for value in conditions.values()),
            "release_version": record.get("release_version"),
        },
    }


def candidates(tree: Committed, own: str) -> dict[str, Any]:
    """The other candidates' manifests committed with this one, by path. Each identifies its own
    content and is checked with the tool of the commit it was generated from, not this one."""
    found = {}
    for path in tree.under("docs/release"):
        if path == own or CANDIDATE_MANIFEST.fullmatch(path) is None:
            continue
        record = tree.json(path)
        source = record.get("source")
        found[path] = {
            "sha256": tree.sha256(path),
            "schema": record.get("schema"),
            "candidate": record.get("candidate"),
            "generated_from_commit": record.get("generated_from_commit"),
            "tree_fingerprint": source.get("tree_fingerprint")
            if isinstance(source, dict)
            else None,
            "tracked_files": source.get("tracked_files") if isinstance(source, dict) else None,
        }
    return found


def toolchain(tree: Committed) -> dict[str, Any]:
    project = pyproject(tree)["project"]
    package = tree.json("web/package.json")
    uv = next(
        (
            line.split("==", 1)[1].split()[0]
            for line in tree.text(".github/ci-requirements.txt").splitlines()
            if line.startswith("uv==")
        ),
        None,
    )
    if uv is None:
        raise ManifestError(".github/ci-requirements.txt pins no uv version")
    return {
        "python": tree.text(".python-version").strip(),
        "requires_python": project["requires-python"],
        "uv": uv,
        "node_for_building_and_testing_only": package["engines"]["node"],
        "declared": {
            "server": "Ubuntu 24.04 LTS, x86-64-v2 or aarch64, 1 vCPU/1 GB + 1 GB swap "
            "minimum, 2 vCPU/2 GB recommended, nginx and Python 3.12 from Ubuntu "
            "(docs/DEPLOYMENT.md section 3)",
        },
    }


def build(tree: Committed, commit: str | None) -> dict[str, Any]:
    release = version(tree)
    own = manifest_name(tree)
    files = file_map(tree, exclude=own)
    areas = {
        area: fingerprint({p: h for p, h in files.items() if p.startswith(area + "/")})
        for area in AREAS
    }
    documents = release_documents(release)
    for document in documents.values():
        tree.content(document)
    return {
        "schema": SCHEMA,
        "name": "ATLAS",
        "version": release,
        "candidate": f"{release}-{CANDIDATE}",
        "declared": {
            "status": "release candidate under review: not released, not tagged, not deployed, "
            "not published",
        },
        "generated_from_commit": commit,
        "source": {
            "tracked_files": len(files),
            "tree_fingerprint": fingerprint(files),
            "fingerprint_rule": "sha256 over the sorted lines 'path NUL mode SP sha256 LF', one "
            "per tracked file except this manifest, where mode and sha256 are those of the "
            "content git committed (git ls-files -s, git cat-file), not of the working tree",
            "files": files,
            "areas": areas,
        },
        "locks": {p: tree.sha256(p) for p in LOCKS},
        "versions": versions(tree, release),
        "toolchain": toolchain(tree),
        "snapshot": snapshot(tree),
        "research_artifacts": research_artifacts(tree),
        "dashboard_build": dashboard_build(tree),
        "static_site": static_site(tree),
        "api_contract": api_contract(tree),
        "scientific_status": scientific_status(tree),
        "documents": documents,
        "candidates": candidates(tree, own),
    }


def comparable(document: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in document.items() if k != "generated_from_commit"}


def differences(recorded: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Every file added, removed, changed (content) or with a changed mode."""
    source = recorded.get("source")
    old = source.get("files") if isinstance(source, dict) else None
    new = current["source"]["files"]
    if not isinstance(old, dict):
        return ["the manifest records no file map"]
    found = []
    for path in sorted(set(old) | set(new)):
        if path not in new:
            found.append(f"removed: {path}")
        elif path not in old:
            found.append(f"added: {path}")
        elif old[path] != new[path]:
            old_mode, _, old_sha256 = str(old[path]).partition(" ")
            new_mode, _, new_sha256 = new[path].partition(" ")
            if old_sha256 != new_sha256:
                found.append(f"changed: {path}")
            if old_mode != new_mode:
                found.append(f"mode changed: {path} ({old_mode} -> {new_mode})")
    return found


def report(lines: list[str]) -> None:
    for line in lines[:SHOWN]:
        print(f"  {line}", file=sys.stderr)
    if len(lines) > SHOWN:
        print(f"  ... and {len(lines) - SHOWN} more ({len(lines)} in total)", file=sys.stderr)


def run(root: Path, write: bool) -> int:
    require_checkout(root)
    states = unclean(root)
    tree: Committed | None
    try:
        tree = Committed(root)
        name = manifest_name(tree)
    except (ManifestError, *MALFORMED):
        if not states:
            raise
        # An unresolved merge or a staged removal of pyproject.toml can leave the committed
        # content unreadable: the checkout is refused below, naming every path.
        tree, name = None, "the manifest"
    dirty = [f"{state}: {p}" for state, p in states if p != name]
    own = [state for state, p in states if p == name]
    if dirty or tree is None:
        action = "write" if write else "check"
        print(
            f"refusing to {action}: the checkout is not clean ({len(dirty)} path(s) besides "
            f"{name}); commit or remove them, or use a fresh clone",
            file=sys.stderr,
        )
        report(dirty)
        return 1
    path = root / name
    if write:
        commit = git(root, "rev-parse", "HEAD").decode("utf-8").strip()
        text = json.dumps(build(tree, commit), indent=2, sort_keys=True) + "\n"
        path.write_bytes(text.encode("utf-8"))
        print(f"wrote {name}")
        return 0
    if not path.is_file():
        print(f"MISMATCH: {name} does not exist", file=sys.stderr)
        return 1
    try:
        document = json.loads(path.read_bytes())
    except ValueError as exc:
        print(f"MISMATCH: {name} is not valid JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(document, dict):
        print(f"MISMATCH: {name} is not a JSON object", file=sys.stderr)
        return 1
    recorded = comparable(document)
    current = comparable(build(tree, None))
    if recorded == current:
        source = current["source"]
        print(f"OK: the checkout matches {name}")
        print(
            f"tree fingerprint {source['tree_fingerprint']} over {source['tracked_files']} "
            "tracked files (the manifest excluded)"
        )
        if own:
            print(f"note: the manifest is {own[0]}: the checkout matches it, not a committed one")
        return 0
    print(f"MISMATCH: the checkout differs from {name}", file=sys.stderr)
    report(differences(recorded, current))
    sections = sorted(k for k in set(recorded) | set(current) if recorded.get(k) != current.get(k))
    print(f"  sections that differ: {', '.join(sections)}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    # Run with whatever python3 a reviewer has, outside uv: tomllib needs 3.11.
    if sys.version_info < (3, 11):  # noqa: UP036
        found = ".".join(str(part) for part in sys.version_info[:3])
        print(f"rc_manifest: error: Python 3.11 or later is required, not {found}", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="generate the manifest")
    mode.add_argument("--check", action="store_true", help="compare the checkout with it")
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="the checkout to fingerprint (default: the one containing this script)",
    )
    args = parser.parse_args(argv)
    try:
        return run(args.root.resolve(), write=args.write)
    except ManifestError as exc:
        print(f"rc_manifest: error: {exc}", file=sys.stderr)
    except MALFORMED as exc:
        print(f"rc_manifest: error: a committed record is malformed ({exc!r})", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
