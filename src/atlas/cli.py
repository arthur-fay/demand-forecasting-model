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
from atlas.features import default_feature_set
from atlas.issues import CheckReport, Severity
from atlas.optimization_runner import (
    inspect_optimization_command,
    run_optimization_command,
    verify_optimization_command,
)
from atlas.pipeline import Stage, run, run_quality
from atlas.registry import spec_for
from atlas.runner import (
    legacy_refit_command,
    projection_command,
    run_backtest_command,
    verify_command,
)
from atlas.snapshot import build_manifest, manifest_to_json
from atlas.uncertainty_runner import (
    inspect_uncertainty_command,
    run_uncertainty_command,
    uncertainty_projection_command,
    verify_uncertainty_command,
)

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
        description=(
            "ATLAS: verify, validate and reproduce the registered data snapshot (M0); "
            "assess data quality and describe forecast features (M1); rolling-origin "
            "backtests and labelled future projections (M2); prediction intervals and "
            "exceedance probabilities (M3); HYPOTHETICAL decision simulation and "
            "optimisation (M4)."
        ),
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
    commands.add_parser(
        "quality",
        help="Integrity, validation, then the reconciled residual and data-quality flags (M1).",
    )
    commands.add_parser(
        "features", help="Describe the default feature set (definitions only; reads no data)."
    )

    backtest = commands.add_parser(
        "backtest", help="Rolling-origin backtests of the forecasting models (M2)."
    )
    backtest_commands = backtest.add_subparsers(dest="backtest_command", required=True)
    run_parser = backtest_commands.add_parser(
        "run", help="Integrity, validation, then a rolling-origin backtest written to a run dir."
    )
    _add_config_argument(run_parser)
    run_parser.add_argument(
        "--output-dir", type=Path, default=None, help="Run directory (default: runs/<run id>)."
    )
    run_parser.add_argument(
        "--period",
        action="append",
        default=None,
        help="Run this configured period (repeatable; default: the selection period only).",
    )
    run_parser.add_argument(
        "--sealed-selection",
        type=Path,
        default=None,
        help="selection.json of a development run; required for any other period.",
    )
    run_parser.add_argument(
        "--require-clean",
        action="store_true",
        help="Refuse to run if code, configs or tests have uncommitted changes (reference runs).",
    )
    verify = backtest_commands.add_parser(
        "verify", help="Re-run a recorded backtest; pass only if every result file is identical."
    )
    verify.add_argument("run_dir", type=Path, help="Directory of a recorded backtest run.")
    legacy = backtest_commands.add_parser(
        "legacy",
        help="Refit the legacy model and compare with its published holdout and forecast.",
    )
    legacy.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Also write the 90 one-step holdout records and their metrics here.",
    )
    forecast = commands.add_parser(
        "forecast",
        help="FUTURE PROJECTION beyond the last observed day (same code path; not validated).",
    )
    _add_config_argument(forecast)
    forecast.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Run directory (default: runs/projections/<run id>).",
    )
    forecast.add_argument(
        "--recommendation",
        type=Path,
        default=None,
        help="recommendation.json of a confirmation run: project only the recommended model.",
    )
    _add_uncertainty_parser(commands)
    _add_optimization_parser(commands)
    return parser


DEFAULT_CONFIG = Path("configs/backtest/atlas-m2-rolling-origin-v1.json")
DEFAULT_UNCERTAINTY_CONFIG = Path("configs/uncertainty/atlas-m3-intervals-v1.json")
DEFAULT_OPTIMIZATION_CONFIG = Path("configs/optimization/atlas-m4-optimization-v1.json")


def _add_optimization_parser(commands: Any) -> None:
    optimize = commands.add_parser(
        "optimize",
        help="HYPOTHETICAL decision simulation: scenarios, LP plans, sensitivity (M4).",
    )
    sub = optimize.add_subparsers(dest="optimize_command", required=True)
    run_parser = sub.add_parser(
        "run",
        help="Integrity, validation, verified M2/M3 inputs, then every scenario and analysis.",
    )
    run_parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"M4 configuration (default: {DEFAULT_OPTIMIZATION_CONFIG} in the repository).",
    )
    run_parser.add_argument(
        "--output-dir", type=Path, default=None, help="Run directory (default: runs/<run id>)."
    )
    run_parser.add_argument(
        "--require-clean",
        action="store_true",
        help="Refuse to run if code, configs or tests have uncommitted changes (reference runs).",
    )
    verify = sub.add_parser(
        "verify", help="Re-run a recorded M4 run; pass only if every result file is identical."
    )
    verify.add_argument("run_dir", type=Path, help="Directory of a recorded M4 run.")
    inspect = sub.add_parser(
        "inspect", help="Check an M4 run directory against its manifest and print its results."
    )
    inspect.add_argument("run_dir", type=Path, help="Directory of a recorded M4 run.")


def _add_uncertainty_parser(commands: Any) -> None:
    uncertainty = commands.add_parser(
        "uncertainty", help="Prediction intervals and exceedance probabilities (M3)."
    )
    sub = uncertainty.add_subparsers(dest="uncertainty_command", required=True)
    run_parser = sub.add_parser(
        "run",
        help="Integrity, validation, residual backtest, then intervals scored on past origins.",
    )
    run_parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"M3 configuration (default: {DEFAULT_UNCERTAINTY_CONFIG} in the repository).",
    )
    run_parser.add_argument(
        "--output-dir", type=Path, default=None, help="Run directory (default: runs/<run id>)."
    )
    run_parser.add_argument(
        "--period",
        action="append",
        default=None,
        help="Run this M3 period (repeatable; default: the selection period only).",
    )
    run_parser.add_argument(
        "--sealed-selection",
        type=Path,
        default=None,
        help="selection.json of an M3 selection run; required for any other period.",
    )
    run_parser.add_argument(
        "--require-clean",
        action="store_true",
        help="Refuse to run if code, configs or tests have uncommitted changes (reference runs).",
    )
    verify = sub.add_parser(
        "verify", help="Re-run a recorded M3 run; pass only if every result file is identical."
    )
    verify.add_argument("run_dir", type=Path, help="Directory of a recorded M3 run.")
    inspect = sub.add_parser(
        "inspect", help="Check a run directory against its manifest and print its results."
    )
    inspect.add_argument("run_dir", type=Path, help="Directory of a recorded M3 run.")
    project_parser = sub.add_parser(
        "project",
        help="FUTURE PROJECTION with the selected intervals (labelled with the M3 status).",
    )
    project_parser.add_argument(
        "--status",
        type=Path,
        required=True,
        help="calibration_status.json of a complete M3 evaluation run.",
    )
    project_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Run directory (default: runs/projections/<run id>).",
    )


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"Experiment configuration (default: {DEFAULT_CONFIG} in the repository).",
    )


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
    elif report.name == "quality" and "residual" in facts:
        r = facts["residual"]
        stream.write(
            f"  {r['label']} residual (city-wide minus five-borough segments): "
            f"{_format_value(r['total'])} requests ({r['share_of_total_pct']}%) on "
            f"{r['dates_positive']} of {r['dates']} dates, max {_format_value(r['max'])} on "
            f"{r['max_date']} ({r['exception_id']})\n"
        )
        counts = ", ".join(f"{code} {n}" for code, n in facts["flag_counts"].items())
        stream.write(f"  flags: {counts}\n")
    elif report.name in _M2_RENDERERS:
        _M2_RENDERERS[report.name](facts, stream)
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


def _render_backtest(facts: Any, stream: TextIO) -> None:
    if "run_dir" not in facts:
        return
    stream.write(
        f"  HISTORICAL BACKTEST {facts['run_id']}: periods {', '.join(facts['periods'])}; "
        f"{_format_value(facts['origins'])} origins, {_format_value(facts['records'])} records\n"
        f"  result fingerprint {facts['result_fingerprint']}\n  written to {facts['run_dir']}\n"
    )
    stream.write("  MAE by bucket (improvement over seasonal naive on common forecasts):\n")
    for row in facts["buckets"]:
        versus = row["mae_improvement_pct"]
        tag = "reference" if versus is None else f"{versus:+.1f}%"
        mae = "n/a" if row["mae"] is None else f"{row['mae']:,.1f}"
        stream.write(
            f"    {row['period']:13} {row['label']:11} {row['model_id']:31} MAE {mae:>9} "
            f"({tag}) n={_format_value(row['n_predictions'])}\n"
        )
    selection = facts["selection"]
    if selection:
        stream.write(
            f"  selection ({selection['period']}): best baseline {selection['best_baseline']}, "
            f"best regression {selection['best_regression']}, selected "
            f"{selection['selected_model']}\n"
        )
        if selection["recommended_model"]:
            stream.write(
                f"  recommended after confirmation: {selection['recommended_model']} "
                f"({selection['recommendation_reason']})\n"
            )


def _render_verification(facts: Any, stream: TextIO) -> None:
    if "identical" not in facts:
        return
    verdict = "identical" if facts["identical"] else "DIFFERENT"
    stream.write(
        f"  results {verdict}: " + ", ".join(f"{k} {v}" for k, v in facts["files"].items())
    )
    stream.write(f"\n  result fingerprint {facts['result_fingerprint']}\n")
    if facts["altered_on_disk"]:
        stream.write(f"  altered on disk: {', '.join(facts['altered_on_disk'])}\n")
    if facts["forecast_difference"]:
        stream.write(f"  forecast comparison: {facts['forecast_difference']}\n")
    for name, (then, now) in facts["provenance_differences"].items():
        stream.write(f"  provenance differs: {name} recorded {then}, now {now}\n")


def _render_legacy_refit(facts: Any, stream: TextIO) -> None:
    if "holdout" not in facts:
        return
    holdout, recursive = facts["holdout"], facts["recursive_forecast"]
    stream.write(
        f"  1. LEGACY ONE-STEP HOLDOUT {holdout['first_date']}..{holdout['last_date']} "
        f"({holdout['procedure']}): {holdout['predictions_reproduced']} of "
        f"{holdout['predictions_published']} predictions reproduced\n"
    )
    for row in holdout["metrics"]:
        mark = "ok " if row["ok"] else "BAD"
        stream.write(
            f"     {mark} {row['metric']}: recomputed {row['recomputed']:.4f}, "
            f"published {row['committed']}\n"
        )
    stream.write(
        f"  2. legacy recursive forecast {recursive['first_date']}..{recursive['last_date']}: "
        f"{recursive['forecasts_reproduced']} of {recursive['forecasts_published']} reproduced; "
        f"fed-back inputs equal: {recursive['fed_back_inputs_equal_published']}; largest "
        f"|raw - rounded| {recursive['max_abs_raw_minus_rounded']:.4f}\n"
    )


def _render_projection(facts: Any, stream: TextIO) -> None:
    if "run_dir" not in facts:
        return
    stream.write(
        f"  FUTURE PROJECTION from {facts['origin']} (1..{facts['max_horizon']} days); "
        f"NOT VALIDATED; written to {facts['run_dir']}\n"
    )
    for model_id, total in facts["totals"].items():
        shown = "n/a" if total is None else f"{total:,.0f}"
        stream.write(f"    {model_id:31} {facts['max_horizon']}-day total {shown}\n")


def _render_uncertainty(facts: Any, stream: TextIO) -> None:
    if "coverage" not in facts:
        return
    if "run_dir" in facts:
        stream.write(f"  HISTORICAL PROBABILISTIC BACKTEST {facts['run_id']}\n")
        stream.write(f"  run directory {facts['run_dir']}\n")
    if facts.get("complete") is False:
        stream.write(
            "  INCOMPLETE RUN: no decision file was written; results are not a protocol run\n"
        )
    if facts.get("files_absent"):
        stream.write(f"  not kept (regenerated by verify): {', '.join(facts['files_absent'])}\n")
    stream.write(f"  result fingerprint {facts['result_fingerprint']}\n")
    if facts.get("selected_pair"):
        stream.write(f"  selected interval method: {facts['selected_pair']}\n")
    if facts.get("interval_status"):
        stream.write(
            f"  evaluation status: {facts['interval_status'].upper()} - "
            f"{facts['status_statement']}\n"
        )
    keys = facts["levels"]
    stream.write("  coverage over all horizons (forecasts, WIS):\n")
    for row in facts["coverage"]:
        shares = ", ".join(
            "n/a" if row[f"coverage_{k}"] is None else f"{k}% -> {100 * row[f'coverage_{k}']:.1f}%"
            for k in keys
        )
        wis = "n/a" if row["wis"] is None else f"{row['wis']:,.1f}"
        stream.write(
            f"    {row['period']:16} {row['model_id']:24} {row['pair_id']:34} {shares} "
            f"(n={_format_value(row['n_evaluated'])}, WIS {wis})\n"
        )


def _render_uncertainty_verification(facts: Any, stream: TextIO) -> None:
    if "identical" not in facts:
        return
    verdict = "identical" if facts["identical"] else "DIFFERENT"
    stream.write(
        f"  results {verdict}: " + ", ".join(f"{k} {v}" for k, v in facts["files"].items())
    )
    stream.write(f"\n  result fingerprint {facts['result_fingerprint']}\n")
    for name, (then, now) in facts["provenance_differences"].items():
        stream.write(f"  provenance differs: {name} recorded {then}, now {now}\n")


def _render_uncertainty_projection(facts: Any, stream: TextIO) -> None:
    if "run_dir" not in facts:
        return
    stream.write(
        f"  FUTURE PROJECTION WITH INTERVALS from {facts['origin']} ({facts['decided_pair']}); "
        f"calibration status {facts['calibration_status'].upper()}; NOT VALIDATED; written to "
        f"{facts['run_dir']}\n"
    )
    for row in facts["rows"]:
        bounds = "  ".join(
            f"{key.removeprefix('interval_')}%: {_bound(value[0])}-{_bound(value[1])}"
            for key, value in row.items()
            if key.startswith("interval_")
        )
        stream.write(f"    {row['target_date']}  {_bound(row['prediction']):>9}  {bounds}\n")


def _render_optimization(facts: Any, stream: TextIO) -> None:
    if "scenarios" not in facts:
        return
    if "run_dir" in facts:
        stream.write(f"  {facts['label']}\n  run directory {facts['run_dir']}\n")
    if facts.get("complete") is False:
        stream.write(
            "  INCOMPLETE RUN: the manifest marks the run incomplete; see the errors above\n"
        )
    if facts.get("files_absent"):
        stream.write(f"  missing: {', '.join(facts['files_absent'])}\n")
    grade = "decision-grade" if facts.get("m3_decision_grade") else "not decision-grade"
    stream.write(
        f"  M3 calibration status {str(facts['m3_interval_status']).upper()}: {grade}"
        f"\n  result fingerprint {facts['result_fingerprint']}\n"
    )
    stream.write(
        "  scenario                    plan                      cash USD  request-days"
        "  terminal backlog (requests)\n"
    )
    for row in facts["scenarios"]:
        over = row.get("terminal_violation")
        excess = "" if over is None else f" ({_m4_number(over, small=True)} over target)"
        stream.write(
            f"    {row['scenario_id']!s:26} {row['feasibility']!s:24} "
            f"{_m4_number(row['discretionary_cost']):>9} "
            f"{_m4_number(row['backlog_request_days']):>13} "
            f"{_m4_number(row['terminal_backlog']):>9}{excess}\n"
        )


def _render_optimization_verification(facts: Any, stream: TextIO) -> None:
    if "identical" not in facts:
        return
    grade = "decision-grade" if facts.get("m3_decision_grade") else "not decision-grade"
    stream.write(
        f"  {facts['label']}\n  M3 calibration status "
        f"{str(facts['m3_interval_status']).upper()}: {grade}\n"
    )
    same = all(v == "identical" for v in facts["files"].values())
    stream.write(
        f"  recomputed files {'identical to' if same else 'DIFFERENT from'} the recorded hashes: "
        + ", ".join(f"{k} {v}" for k, v in facts["files"].items())
        + f"\n  result fingerprint {facts['result_fingerprint']}"
        + (
            ""
            if facts.get("recorded_result_fingerprint") == facts["result_fingerprint"]
            else f" (the manifest records {facts.get('recorded_result_fingerprint')})"
        )
        + "\n"
    )
    if facts.get("altered_on_disk"):
        stream.write(f"  altered on disk: {', '.join(facts['altered_on_disk'])}\n")
    if facts.get("manifest_entries_edited"):
        stream.write(
            "  manifest entry differs from the file on disk (which equals the "
            f"recomputation): {', '.join(facts['manifest_entries_edited'])}\n"
        )
    if facts.get("files_absent"):
        stream.write(f"  missing: {', '.join(facts['files_absent'])}\n")
    if facts.get("recomputed_errors"):
        stream.write(f"  failed checks in the recomputation: {facts['recomputed_errors']}\n")
    for name, (then, now) in facts["provenance_differences"].items():
        stream.write(f"  provenance differs: {name} recorded {then}, now {now}\n")


def _small(value: Any) -> str:
    """Like :func:`_bound`, but a nonzero value below one keeps three significant digits."""
    if isinstance(value, float) and value == value and 0 < abs(value) < 1:
        return f"{value:.3g}"
    return _bound(value)


def _bound(value: Any) -> str:
    return "n/a" if value is None or value != value else f"{value:,.0f}"


def _m4_number(value: Any, small: bool = False) -> str:
    """An M4 headline value from ``metrics.json``: ``n/a`` for anything that is not a number,
    so a malformed run file is reported by ``inspect``, never a crash of the renderer."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "n/a"
    return _small(value) if small else _bound(value)


_M2_RENDERERS = {
    "backtest": _render_backtest,
    "verification": _render_verification,
    "legacy_refit": _render_legacy_refit,
    "projection": _render_projection,
    "uncertainty": _render_uncertainty,
    "uncertainty_inspection": _render_uncertainty,
    "uncertainty_verification": _render_uncertainty_verification,
    "uncertainty_projection": _render_uncertainty_projection,
    "optimization": _render_optimization,
    "optimization_inspection": _render_optimization,
    "optimization_verification": _render_optimization_verification,
}


def _features(output_format: str, stream: TextIO) -> int:
    feature_set = default_feature_set()
    if output_format == "json":
        payload = {
            "name": feature_set.name,
            "fingerprint": feature_set.fingerprint(),
            "max_offset": feature_set.max_offset,
            "features": [feature.spec() | {"kind": feature.kind.value} for feature in feature_set],
        }
        stream.write(json.dumps(payload, indent=2) + "\n")
        return EXIT_OK
    stream.write(
        f"feature set {feature_set.name}: {len(feature_set)} features, history needed "
        f"{feature_set.max_offset} days, fingerprint {feature_set.fingerprint()[:16]}\n"
    )
    for feature in feature_set:
        reads = feature.offsets
        offsets = f"t-{max(reads)}..t-{min(reads)}" if reads else ""
        stream.write(
            f"  {feature.name:24} {feature.kind.value:12} {offsets:14} {feature.description}\n"
        )
    return EXIT_OK


def _manifest(settings: Settings, stream: TextIO) -> int:
    manifest = build_manifest(settings.repo_root, spec_for(settings.snapshot_id))
    stream.write(manifest_to_json(manifest))
    return EXIT_OK


def _config_path(args: argparse.Namespace, settings: Settings) -> Path:
    path: Path = args.config if args.config is not None else settings.repo_root / DEFAULT_CONFIG
    return path


def _backtest(args: argparse.Namespace, settings: Settings) -> list[CheckReport]:
    if args.backtest_command == "run":
        return run_backtest_command(
            settings,
            _config_path(args, settings),
            output_dir=args.output_dir,
            periods=args.period,
            sealed_selection=args.sealed_selection,
            require_clean=args.require_clean,
        )
    if args.backtest_command == "verify":
        return verify_command(settings, args.run_dir)
    return legacy_refit_command(settings, output_dir=args.output_dir)


def _uncertainty(args: argparse.Namespace, settings: Settings) -> list[CheckReport]:
    if args.uncertainty_command == "run":
        config = (
            args.config
            if args.config is not None
            else settings.repo_root / DEFAULT_UNCERTAINTY_CONFIG
        )
        return run_uncertainty_command(
            settings,
            config,
            output_dir=args.output_dir,
            periods=args.period,
            sealed_selection=args.sealed_selection,
            require_clean=args.require_clean,
        )
    if args.uncertainty_command == "verify":
        return verify_uncertainty_command(settings, args.run_dir)
    if args.uncertainty_command == "inspect":
        return inspect_uncertainty_command(args.run_dir)
    return uncertainty_projection_command(settings, args.status, output_dir=args.output_dir)


def _optimize(args: argparse.Namespace, settings: Settings) -> list[CheckReport]:
    if args.optimize_command == "run":
        config = (
            args.config
            if args.config is not None
            else settings.repo_root / DEFAULT_OPTIMIZATION_CONFIG
        )
        return run_optimization_command(
            settings, config, output_dir=args.output_dir, require_clean=args.require_clean
        )
    if args.optimize_command == "verify":
        return verify_optimization_command(settings, args.run_dir)
    return inspect_optimization_command(args.run_dir)


def main(argv: Sequence[str] | None = None, stdout: TextIO | None = None) -> int:
    out = stdout if stdout is not None else sys.stdout
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "features":
            return _features(args.format, out)
        settings = load_settings(repo_root=args.repo_root, snapshot_id=args.snapshot)
        if args.command == "snapshot" and args.snapshot_command == "manifest":
            return _manifest(settings, out)
        if args.command == "quality":
            reports = run_quality(settings)
        elif args.command == "backtest":
            reports = _backtest(args, settings)
        elif args.command == "uncertainty":
            reports = _uncertainty(args, settings)
        elif args.command == "optimize":
            reports = _optimize(args, settings)
        elif args.command == "forecast":
            reports = projection_command(
                settings,
                _config_path(args, settings),
                output_dir=args.output_dir,
                recommendation=args.recommendation,
            )
        else:
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
