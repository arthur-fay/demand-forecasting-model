"""The publication gate of the static demonstration (M9): may this run publish to Pages?

The GitHub Pages workflow (.github/workflows/pages.yml) runs this before its publication job.
It names every unmet condition and exits 1 unless all of them hold:

1. the run is on ``main`` (``--ref``, the workflow's ``github.ref``);
2. whoever dispatched it typed the confirmation phrase (``--confirm``);
3. the repository variable ``ATLAS_PAGES_PUBLICATION`` is ``approved`` (``--switch``): a
   setting only a repository administrator can change, outside the code;
4. the committed record ``docs/release/PAGES_PUBLICATION.json`` (a reviewed commit) states
   every owner decision: the project licence, the NYC Open Data terms, the version the site
   shows (which must be the code's), the accepted limitations, the review of the origin the
   site shares, the Pages and environment settings and the approval itself, with who
   decided, when, and where it is recorded. The record is read strictly: a repeated key, an
   unknown field, a date that is impossible, before M9 or after today (UTC), and notes that
   are not plain text are refused, so what a reviewer reads is what counts.

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
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

FORMAT = "atlas-pages-publication/1"
SITE = "https://arthur-fay.github.io/demand-forecasting-model/"
PHRASE = "publish the ATLAS static demo"
SWITCH = "approved"
#: Every decision the owner must record before publication (docs/STATIC_DEMO.md section 10).
CONDITIONS = (
    "project_licence_decided",
    "nyc_open_data_terms_confirmed",
    "release_version_decided",
    "limitations_accepted",
    "public_static_profile_approved",
    "shared_origin_reviewed",
    "pages_settings_configured",
    "publication_approved",
)
#: The record's fields (any other is refused).
FIELDS = frozenset(
    {
        "format",
        "site",
        "conditions",
        "release_version",
        "decided_by",
        "decided_on",
        "approval_reference",
        "notes",
    }
)
#: No decision can predate the record: M9 phase A, which committed it undecided.
FIRST_DECISION_DAY = date(2026, 10, 18)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def code_version(root: Path) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return str(project["version"])


def record_problems(record: Any, version: str, today: date | None = None) -> list[str]:
    """Why the committed record does not allow publication (empty: it does)."""
    if not isinstance(record, dict):
        return ["the publication record is not a JSON object"]
    problems: list[str] = []
    unknown_fields = sorted(set(record) - FIELDS)
    if unknown_fields:
        problems.append(f"unknown fields in the record: {', '.join(unknown_fields)}")
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
    day = _day(decided_on)
    if day is None:
        problems.append("the record has no decided_on date (YYYY-MM-DD)")
    else:
        last = today if today is not None else datetime.now(UTC).date()
        if not FIRST_DECISION_DAY <= day <= last:
            problems.append(
                f"the record's decided_on {decided_on} is not between "
                f"{FIRST_DECISION_DAY.isoformat()} and today ({last.isoformat()})"
            )
    if "notes" in record and not isinstance(record["notes"], str):
        problems.append("the record's notes are not plain text")
    return problems


def _day(value: object) -> date | None:
    if not (isinstance(value, str) and _DATE.fullmatch(value)):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise ValueError(f"repeated keys: {', '.join(repeated)}")
    return dict(pairs)


def load_record(path: Path) -> Any:
    """The record, refusing a key given twice anywhere in it."""
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique)


def gate_problems(
    record: Any,
    version: str,
    *,
    ref: str,
    confirm: str,
    switch: str,
    today: date | None = None,
) -> list[str]:
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
    return problems + record_problems(record, version, today)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--ref", required=True)
    parser.add_argument("--confirm", default="")
    parser.add_argument("--switch", default="")
    args = parser.parse_args(argv)
    try:
        record = load_record(args.record)
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
