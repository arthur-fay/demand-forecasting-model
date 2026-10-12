"""Command-line interface: ``atlas`` (or ``python -m atlas``).

Exit codes: 0 = all requested checks passed, 1 = a check failed, 2 = usage or
configuration error, 3 = unexpected internal error (a bug; the traceback is printed).

Every stage that ran is printed, so prerequisite stages (and the documented exceptions
they accepted) stay visible for every command.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from atlas import __version__
from atlas.config import Settings, load_settings
from atlas.errors import AtlasError
from atlas.issues import CheckReport, Severity
from atlas.pipeline import Stage, run
from atlas.registry import spec_for
from atlas.snapshot import build_manifest, manifest_to_json

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_INTERNAL = 3

_STAGES = {
    "snapshot": Stage.INTEGRITY,
    "validate": Stage.VALIDATION,
    "reproduce": Stage.REPRODUCTION,
    "check": Stage.REPRODUCTION,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas",
        description="ATLAS M0: verify, validate and reproduce the registered data snapshot.",
    )
    parser.add_argument("--version", action="version", version=f"atlas {__version__}")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (default: $ATLAS_REPO_ROOT, else discovered from the cwd).",
    )
    parser.add_argument(
        "--snapshot",
        default=None,
        help="Snapshot id (default: $ATLAS_SNAPSHOT_ID or legacy-v0).",
    )
    parser.add_argument(
        "--format", choices=("text", "json"), default="text", help="Output format (default: text)."
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    snapshot = commands.add_parser("snapshot", help="Snapshot integrity and manifest commands.")
    snapshot_commands = snapshot.add_subparsers(dest="snapshot_command", required=True)
    snapshot_commands.add_parser("verify", help="Verify SHA-256, size, header and row counts.")
    snapshot_commands.add_parser(
        "manifest",
        help="Print the manifest computed from the files on disk (never writes a file).",
    )

    commands.add_parser("validate", help="Integrity plus data contracts and declared exceptions.")
    commands.add_parser(
        "reproduce", help="Integrity, validation, then reproduce historical summaries and KPIs."
    )
    commands.add_parser("check", help="Run every stage (CI entry point; same as reproduce).")
    return parser


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


def render_text(reports: Sequence[CheckReport], stream: TextIO) -> None:
    for report in reports:
        if report.skipped_reason:
            stream.write(f"[SKIP] {report.name}: {report.skipped_reason}\n\n")
            continue
        status = "PASS" if report.ok else "FAIL"
        stream.write(
            f"[{status}] {report.name}: {len(report.errors)} error(s), "
            f"{len(report.warnings)} warning(s)\n"
        )
        for issue in sorted(report.issues, key=lambda i: (i.severity != Severity.ERROR, i.subject)):
            tag = f" ({issue.exception_id})" if issue.exception_id else ""
            stream.write(
                f"  {issue.severity.value.upper():7} {issue.subject} [{issue.code}]{tag}: "
                f"{issue.message}"
            )
            stream.write(f" [count={issue.count}]\n" if issue.count != 1 else "\n")
            for example in issue.examples:
                stream.write(f"            - {example}\n")
        _render_facts(report, stream)
        stream.write("\n")


def _render_facts(report: CheckReport, stream: TextIO) -> None:
    facts = report.facts
    if report.name == "integrity":
        stream.write(
            f"  snapshot {facts['snapshot_id']}: {facts['tables']} tables, {facts['artifacts']} "
            f"artifacts, {_format_value(facts['data_rows'])} data rows, "
            f"provenance {facts['provenance_completeness']}\n"
        )
    elif report.name == "validation" and isinstance(facts.get("reconciliation"), dict):
        r = facts["reconciliation"]
        stream.write(
            f"  city-wide total {_format_value(r['total_requests'])} vs five-borough segments "
            f"{_format_value(r['segment_requests'])}: gap {_format_value(r['total_gap'])} "
            f"({r['gap_share_pct']}%) on {r['dates_with_gap']} of {r['dates_compared']} dates\n"
        )
    elif report.name == "reproduction" and "historical_totals" in facts:
        totals = facts["historical_totals"]
        stream.write(
            f"  history {totals['first_date']}..{totals['last_date']}: "
            f"{_format_value(totals['total_requests'])} requests over {totals['days']} days, "
            f"mean {_format_value(totals['average_daily_requests'])}/day\n"
        )
        stream.write(f"  tables reproduced: {', '.join(facts['tables_reproduced']) or 'none'}\n")
        stream.write(
            "  legacy outputs cross-checked against base data: "
            f"{', '.join(facts['legacy_outputs_cross_checked']) or 'none'}\n"
        )
        stream.write("  legacy KPIs (arithmetic reproduction; meaning in brackets):\n")
        for kpi in facts["kpis"]:
            mark = "ok " if kpi["ok"] else "BAD"
            stream.write(
                f"    {mark} {kpi['name']} = {_format_value(kpi['committed'])} [{kpi['meaning']}]\n"
            )


def _manifest(settings: Settings, stream: TextIO) -> int:
    manifest = build_manifest(settings.repo_root, spec_for(settings.snapshot_id))
    stream.write(manifest_to_json(manifest))
    return EXIT_OK


def main(argv: Sequence[str] | None = None, stdout: TextIO | None = None) -> int:
    out = stdout if stdout is not None else sys.stdout
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = load_settings(repo_root=args.repo_root, snapshot_id=args.snapshot)
        if args.command == "snapshot" and args.snapshot_command == "manifest":
            return _manifest(settings, out)
        reports = run(settings, through=_STAGES[args.command])
    except AtlasError as exc:
        sys.stderr.write(f"atlas: error: {exc}\n")
        return EXIT_USAGE
    except Exception:  # an unexpected failure is a bug, not a data-quality result
        sys.stderr.write("atlas: internal error (please report):\n" + traceback.format_exc())
        return EXIT_INTERNAL

    if args.format == "json":
        payload = {
            "snapshot": settings.snapshot_id,
            "ok": all(report.ok for report in reports),
            "reports": [report.to_dict() for report in reports],
        }
        out.write(json.dumps(payload, indent=2, default=str) + "\n")
    else:
        render_text(reports, out)
    return EXIT_OK if all(report.ok for report in reports) else EXIT_FAILED
