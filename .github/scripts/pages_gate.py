"""The publication gate of the static demonstration (M9): may this run publish to Pages?

The GitHub Pages workflow (.github/workflows/pages.yml) runs this before its publication job.
It names every unmet condition and exits 1 unless all of them hold:

1. the run is on ``main`` (``--ref``, the workflow's ``github.ref``);
2. whoever dispatched it typed the confirmation phrase (``--confirm``);
3. the repository variable ``ATLAS_PAGES_PUBLICATION`` is ``approved`` (``--switch``): a
   setting only a repository administrator can change, outside the code;
4. the committed record ``docs/release/PAGES_PUBLICATION.json`` (a reviewed commit) states
   every owner decision: the project licence, the NYC Open Data terms, the version the site
   shows (which must be the code's), the accepted limitations, the Pages settings and the
   approval itself, with who decided, when, and where it is recorded.

Phase A commits that record with every condition false, so no run can publish, whatever is
dispatched. The protection rules of the ``github-pages`` environment (required reviewers,
``main`` only) are a further, independent check that GitHub itself enforces.

Standard library only (Python 3.11 or later). Usage::

    python .github/scripts/pages_gate.py --record docs/release/PAGES_PUBLICATION.json \\
        --ref "$GITHUB_REF" --confirm "$CONFIRM" --switch "$SWITCH"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

FORMAT = "atlas-pages-publication/1"
SITE = "https://arthur-fay.github.io/demand-forecasting-model/"
PHRASE = "publish the ATLAS static demo"
SWITCH = "approved"
#: Every decision the owner must record before publication (docs/STATIC_DEMO.md section 9).
CONDITIONS = (
    "project_licence_decided",
    "nyc_open_data_terms_confirmed",
    "release_version_decided",
    "limitations_accepted",
    "public_static_profile_approved",
    "pages_settings_configured",
    "publication_approved",
)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def code_version(root: Path) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return str(project["version"])


def record_problems(record: Any, version: str) -> list[str]:
    """Why the committed record does not allow publication (empty: it does)."""
    if not isinstance(record, dict):
        return ["the publication record is not a JSON object"]
    problems: list[str] = []
    if record.get("format") != FORMAT:
        problems.append(f"the publication record is not {FORMAT}")
    if record.get("site") != SITE:
        problems.append(f"the record is for {record.get('site')!r}, not {SITE}")
    conditions = record.get("conditions")
    if not isinstance(conditions, dict):
        conditions = {}
        problems.append("the record states no conditions")
    for name in CONDITIONS:
        if conditions.get(name) is not True:
            problems.append(f"condition not met: {name}")
    unknown = sorted(set(conditions) - set(CONDITIONS))
    if unknown:
        problems.append(f"unknown conditions in the record: {', '.join(unknown)}")
    shown = record.get("release_version")
    if not (isinstance(shown, str) and _VERSION.fullmatch(shown)):
        problems.append("the record names no release version (MAJOR.MINOR.PATCH)")
    elif shown != version:
        problems.append(f"the record approves version {shown}, the code is {version}")
    for field in ("decided_by", "approval_reference"):
        value = record.get(field)
        if not (isinstance(value, str) and value.strip()):
            problems.append(f"the record names no {field}")
    decided_on = record.get("decided_on")
    if not (isinstance(decided_on, str) and _DATE.fullmatch(decided_on)):
        problems.append("the record has no decided_on date (YYYY-MM-DD)")
    return problems


def gate_problems(record: Any, version: str, *, ref: str, confirm: str, switch: str) -> list[str]:
    problems: list[str] = []
    if ref != "refs/heads/main":
        problems.append(f"publication runs on main only, not {ref}")
    if confirm != PHRASE:
        problems.append(f"the confirmation phrase was not typed ({PHRASE!r})")
    if switch != SWITCH:
        problems.append(
            f"the repository variable ATLAS_PAGES_PUBLICATION is not {SWITCH!r} "
            "(Settings > Secrets and variables > Actions > Variables)"
        )
    return problems + record_problems(record, version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--ref", required=True)
    parser.add_argument("--confirm", default="")
    parser.add_argument("--switch", default="")
    args = parser.parse_args(argv)
    try:
        record = json.loads(args.record.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"pages gate: the publication record cannot be read ({exc})", file=sys.stderr)
        return 1
    problems = gate_problems(
        record, code_version(args.root), ref=args.ref, confirm=args.confirm, switch=args.switch
    )
    if problems:
        print("pages gate: publication refused:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("pages gate: every publication condition holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
