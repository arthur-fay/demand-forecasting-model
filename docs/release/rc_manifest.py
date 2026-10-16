"""Write or check the ATLAS release-candidate manifest (M8).

The manifest fingerprints everything a release candidate consists of: every tracked file, the
dependency locks, the data snapshot, the committed research results, the committed dashboard
build and the API contract, together with the scientific status those results carry. It is
generated as the last step of preparing a candidate, so the candidate commit is the commit that
adds it; a reviewer checks a checkout against it with ``--check``.

Usage (from the repository root; standard library only)::

    python3 docs/release/rc_manifest.py --write   # (re)generate docs/release/<candidate>.json
    python3 docs/release/rc_manifest.py --check   # exit 1 if the checkout differs from it

The manifest excludes itself from the tree fingerprint (a file cannot contain its own hash) and
records the commit it was generated from only for information: what identifies the candidate is
the content, not the commit that happened to be checked out.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = "rc.1"
SCHEMA = "atlas-release-candidate/1"
#: Fingerprinted separately as well as in the tree (what a reviewer compares first).
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
    ".github",
    "01 Data",
    "02 Notebooks",
    "03 SQL",
    "04 Power BI",
    "05 Screenshots",
    "06 Exports",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def tracked_files() -> list[str]:
    return sorted(p for p in git("ls-files", "-z").split("\0") if p)


def fingerprint(entries: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(entries):
        digest.update(f"{path}\0{entries[path]}\n".encode())
    return digest.hexdigest()


def load_json(path: str) -> Any:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def manifest_path() -> Path:
    return ROOT / "docs" / "release" / f"atlas-{version()}-{CANDIDATE}.json"


def research_artifacts() -> list[dict[str, Any]]:
    runs = []
    for manifest in sorted((ROOT / "docs" / "results").glob("**/manifest.json")):
        record = json.loads(manifest.read_text(encoding="utf-8"))
        run_dir = manifest.parent
        files = {
            str(p.relative_to(ROOT)): sha256(p)
            for p in sorted(run_dir.iterdir())
            if p.is_file() and p.name != "manifest.json"
        }
        runs.append(
            {
                "directory": str(run_dir.relative_to(ROOT)),
                "manifest_sha256": sha256(manifest),
                "record_type": record.get("record_type"),
                "run_id": record.get("run_id"),
                "config_fingerprint": record.get("config_fingerprint"),
                "result_fingerprint": record.get("result_fingerprint"),
                "files": files,
            }
        )
    return runs


def dashboard_build() -> dict[str, Any]:
    build = load_json("web/dist/atlas-dashboard.json")
    dist = ROOT / "web" / "dist"
    files = {str(p.relative_to(ROOT)): sha256(p) for p in sorted(dist.rglob("*")) if p.is_file()}
    return {
        "manifest": "web/dist/atlas-dashboard.json",
        "manifest_sha256": sha256(dist / "atlas-dashboard.json"),
        "version": build.get("version"),
        "files": files,
    }


def api_contract() -> dict[str, Any]:
    contract = load_json("docs/api/openapi.json")
    operations = sum(
        1
        for item in contract["paths"].values()
        for method in item
        if method in ("get", "post", "put", "patch", "delete")
    )
    return {
        "path": "docs/api/openapi.json",
        "sha256": sha256(ROOT / "docs/api/openapi.json"),
        "info_version": contract["info"]["version"],
        "openapi": contract["openapi"],
        "operations": operations,
    }


def snapshot() -> dict[str, Any]:
    manifest = load_json("snapshots/legacy-v0/manifest.json")
    provenance = manifest["provenance"]
    recommendation = load_json("docs/results/atlas-m2-rolling-origin-v1/full/recommendation.json")
    return {
        "snapshot_id": manifest["snapshot_id"],
        "manifest": "snapshots/legacy-v0/manifest.json",
        "manifest_sha256": sha256(ROOT / "snapshots/legacy-v0/manifest.json"),
        "data_fingerprint": recommendation["data_fingerprint"],
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


def scientific_status() -> dict[str, Any]:
    recommendation = load_json("docs/results/atlas-m2-rolling-origin-v1/full/recommendation.json")
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
    calibration = load_json("docs/results/atlas-m3-intervals-v1/full/calibration_status.json")
    return {
        "m2_recommended_model": recommendation["recommendation"]["recommended_model"],
        "m2_primary_tests": tests,
        "m2_selection_evidence": "weak: the development selection test over-rejects at h=30 "
        "(M8 erratum, docs/BACKTESTING.md)",
        "m3_interval_status": calibration["decision_status"]["interval_status"],
        "m3_decision_grade": calibration["decision_status"]["decision_grade"],
        "m4_parameters": "HYPOTHETICAL",
        "forecast": "archived projection 2026-07-01..2026-07-30 from data ending 2026-06-30; "
        "never scored",
        "validated_recommendation": False,
    }


def toolchain() -> dict[str, Any]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    package = load_json("web/package.json")
    uv = next(
        line.split("==", 1)[1].split()[0]
        for line in (ROOT / ".github/ci-requirements.txt").read_text().splitlines()
        if line.startswith("uv==")
    )
    return {
        "python": (ROOT / ".python-version").read_text().strip(),
        "requires_python": project["requires-python"],
        "uv": uv,
        "node_for_building_and_testing_only": package["engines"]["node"],
        "server": "Ubuntu 24.04 LTS, x86-64-v2 or aarch64, 1 vCPU/1 GB + 1 GB swap minimum, "
        "2 vCPU/2 GB recommended, nginx and Python 3.12 from Ubuntu (docs/DEPLOYMENT.md "
        "section 3)",
    }


def build(commit: str | None) -> dict[str, Any]:
    self_path = str(manifest_path().relative_to(ROOT))
    files = {
        p: sha256(ROOT / p)
        for p in tracked_files()
        if p != self_path and (ROOT / p).is_file() and not (ROOT / p).is_symlink()
    }
    areas = {
        area: fingerprint({p: h for p, h in files.items() if p == area or p.startswith(area + "/")})
        for area in AREAS
    }
    return {
        "schema": SCHEMA,
        "name": "ATLAS",
        "version": version(),
        "candidate": f"{version()}-{CANDIDATE}",
        "status": "release candidate under review: not released, not tagged, not deployed",
        "generated_from_commit": commit,
        "source": {
            "tracked_files": len(files),
            "tree_fingerprint": fingerprint(files),
            "fingerprint_rule": "sha256 over sorted 'path NUL sha256(file) LF' of every tracked "
            "file except this manifest",
            "areas": areas,
        },
        "locks": {p: sha256(ROOT / p) for p in LOCKS},
        "toolchain": toolchain(),
        "snapshot": snapshot(),
        "research_artifacts": research_artifacts(),
        "dashboard_build": dashboard_build(),
        "api_contract": api_contract(),
        "scientific_status": scientific_status(),
        "documents": {
            "release_gate": "docs/release/RELEASE_GATE.md",
            "release_candidate": "docs/release/RELEASE_CANDIDATE.md",
            "known_limitations": "docs/KNOWN_LIMITATIONS.md",
            "model_card": "docs/MODEL_CARD.md",
            "data_card": "docs/DATA_CARD.md",
            "audit": "docs/audit/README.md",
            "phase_b_runbook": "docs/release/VPS_PHASE_B_RUNBOOK.md",
        },
    }


def comparable(document: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in document.items() if k != "generated_from_commit"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="generate the manifest")
    mode.add_argument("--check", action="store_true", help="compare the checkout with it")
    args = parser.parse_args(argv)
    path = manifest_path()
    if args.write:
        commit = git("rev-parse", "HEAD").strip()
        path.write_text(json.dumps(build(commit), indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
        return 0
    if not path.is_file():
        print(f"{path.relative_to(ROOT)} does not exist", file=sys.stderr)
        return 1
    recorded = comparable(json.loads(path.read_text(encoding="utf-8")))
    current = comparable(build(None))
    if recorded == current:
        print(f"OK: the checkout matches {path.relative_to(ROOT)}")
        print(f"tree fingerprint {current['source']['tree_fingerprint']}")
        return 0
    differing = sorted(k for k in set(recorded) | set(current) if recorded.get(k) != current.get(k))
    print(f"MISMATCH in: {', '.join(differing)}", file=sys.stderr)
    for key in differing:
        if key == "source":
            old, new = recorded.get(key, {}), current.get(key, {})
            for area, value in new.get("areas", {}).items():
                if old.get("areas", {}).get(area) != value:
                    print(f"  area changed: {area}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
