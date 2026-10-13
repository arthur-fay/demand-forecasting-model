"""Fail-closed M3 commands: run, verify, inspect and project probabilistic forecasts.

They extend the M2 runner instead of duplicating it: the same snapshot validation
(:func:`atlas.runner.load_series`), the same backtest engine for the residuals, the same
fingerprints, provenance and deterministic files, and the same pre-registration pattern:

* ``atlas uncertainty run`` evaluates only the M3 *selection* period unless other periods
  are named. Any other period needs ``--sealed-selection`` (the ``selection.json`` of an
  earlier selection run of the same configuration on the same data) and must be run
  together with the selection period, whose recomputed choice has to reproduce the seal
  in every decision field. A run with an ERROR is incomplete and writes no
  ``selection.json`` or ``calibration_status.json``;
* the M3 configuration pins the fingerprint of its M2 configuration, and its primary point
  model must be the model recommended by the complete M2 run named in the configuration;
* ``atlas uncertainty verify`` re-runs a recorded run and requires byte-identical files;
* ``atlas uncertainty inspect`` checks a run directory against its manifest and prints its
  headline results without recomputing anything;
* ``atlas uncertainty project`` attaches the selected intervals and exceedance
  probabilities to a future projection, labelled with the evaluation status.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Final

import pandas as pd

from atlas.backtest import (
    BacktestPlan,
    BacktestResult,
    Period,
    PeriodRole,
    TrainingWindow,
    project,
    run_backtest,
)
from atlas.config import Settings
from atlas.errors import ConfigError
from atlas.experiment import (
    MANIFEST_SCHEMA_VERSION,
    ExperimentConfig,
    build_model,
    code_fingerprint,
    csv_bytes,
    environment_info,
    git_info,
    json_bytes,
    load_config,
    parse_config,
    result_fingerprint,
    sha256_bytes,
    utc_now,
    write_run,
)
from atlas.issues import CheckReport, Issue, Severity
from atlas.models import Forecaster
from atlas.probabilistic import (
    DECISION_GRADE,
    RECORD_TYPE_PROBABILISTIC,
    RECORD_TYPE_PROJECTION_INTERVALS,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    EngineSettings,
    ProbabilisticResult,
    issue_forecasts,
    level_key,
    level_quantile_columns,
    run_probabilistic,
    threshold_descriptions,
)
from atlas.runner import (
    RECOMMENDATION_TYPE,
    RUNS_DIRNAME,
    SnapshotData,
    _check_output_dir,
    _flagged_dates,
    _read_json,
    _recommendation_source,
    load_series,
)
from atlas.snapshot import sha256_file
from atlas.uncertainty_config import (
    UncertaintyConfig,
    UncertaintyPeriodConfig,
    check_against_backtest,
    load_uncertainty_config,
    parse_uncertainty_config,
)
from atlas.uncertainty_report import LABEL, render_uncertainty_report

RESULT_FILES: Final = ("forecasts.csv", "intervals.csv", "exceedance.csv", "metrics.json")
SEALED_INTERVAL_SELECTION_TYPE: Final = "sealed_interval_selection"
CALIBRATION_STATUS_TYPE: Final = "calibration_status"
RESIDUAL_PERIOD: Final = "m3_residuals"
PROJECTION_LABEL: Final = (
    "FUTURE PROJECTION WITH INTERVALS: forecasts beyond the last observed day with "
    "prediction intervals and exceedance probabilities calibrated on past errors. No outcome "
    "has been observed; read the calibration status before using them."
)
STATUS_GUIDANCE: Final = {
    DECISION_GRADE: "The selected intervals met every pre-registered calibration criterion.",
    "failed_calibration": (
        "The selected intervals FAILED the pre-registered calibration criteria on the "
        "historical evaluation period: these intervals and probabilities are not decision-grade."
    ),
    "insufficient_evidence": (
        "The historical evaluation was too short to validate coverage: these intervals and "
        "probabilities are exploratory, not decision-grade."
    ),
    "unavailable": "No evaluation status is available: these intervals are unvalidated.",
}


@dataclass(frozen=True)
class M3Context:
    config: UncertaintyConfig
    backtest: ExperimentConfig


def _repo_file(settings: Settings, relative: str, what: str) -> Path:
    path = (settings.repo_root / relative).resolve()
    if not path.is_relative_to(settings.repo_root.resolve()):
        raise ConfigError(f"{what} {relative!r} must lie inside the repository")
    return path


def load_context(settings: Settings, config_path: Path) -> M3Context:
    """The M3 configuration and its pinned M2 configuration, cross-checked."""
    config = load_uncertainty_config(config_path)
    backtest = load_config(_repo_file(settings, config.backtest_config, "backtest_config"))
    return _context(settings, config, backtest)


def _context(
    settings: Settings, config: UncertaintyConfig, backtest: ExperimentConfig
) -> M3Context:
    check_against_backtest(config, backtest)
    if settings.snapshot_id != backtest.snapshot_id:
        raise ConfigError(
            f"the configuration is for snapshot {backtest.snapshot_id!r}, but "
            f"{settings.snapshot_id!r} is selected (--snapshot or ATLAS_SNAPSHOT_ID)"
        )
    return M3Context(config, backtest)


def residual_plan(context: M3Context) -> BacktestPlan:
    """One descriptive period over the residual span, under M2's protocol settings."""
    config, backtest = context.config, context.backtest
    return BacktestPlan(
        periods=(
            Period(
                RESIDUAL_PERIOD,
                PeriodRole.DESCRIPTIVE,
                config.residual_first_origin,
                config.residual_last_origin,
                1,
                allow_incomplete_horizons=True,
            ),
        ),
        max_horizon=backtest.max_horizon,
        min_history_days=backtest.min_history_days,
        training_window=TrainingWindow(backtest.training_window.days),
        data_delay_days=backtest.data_delay_days,
    )


def point_models(context: M3Context) -> tuple[Forecaster, ...]:
    configured = {m.model_id: m for m in context.backtest.models}
    return tuple(build_model(configured[m]) for m in context.config.point_models)


def recommendation_provenance(
    settings: Settings, context: M3Context, data: SnapshotData
) -> dict[str, Any] | None:
    """Check that the primary model is the one the complete M2 run recommended."""
    relative = context.config.recommendation
    if relative is None:
        return None
    path = _repo_file(settings, relative, "recommendation")
    record = _read_json(path, "recommendation")
    if record.get("record_type") != RECOMMENDATION_TYPE:
        raise ConfigError(f"{path} is not a recommendation record")
    if record.get("config_fingerprint") != context.backtest.fingerprint():
        raise ConfigError("the M2 recommendation was made with a different backtest configuration")
    if record.get("data_fingerprint") != data.fingerprint:
        raise ConfigError("the M2 recommendation was made on different data")
    source = _recommendation_source(path, record)
    recommended = (record.get("recommendation") or {}).get("recommended_model")
    if recommended != context.config.primary_model:
        raise ConfigError(
            f"primary_model {context.config.primary_model!r} is not the M2 recommendation "
            f"{recommended!r}"
        )
    return {"path": relative, "recommended_model": recommended, **source}


def default_periods(config: UncertaintyConfig) -> list[str]:
    if config.selection is not None:
        return [config.selection.period]
    return config.period_names


def load_sealed_interval_selection(
    path: Path, config: UncertaintyConfig, data_fingerprint: str
) -> dict[str, Any]:
    sealed = _read_json(path, "sealed interval selection")
    if sealed.get("record_type") != SEALED_INTERVAL_SELECTION_TYPE:
        raise ConfigError(f"{path} is not a sealed interval selection")
    if sealed.get("config_fingerprint") != config.fingerprint():
        raise ConfigError(
            "the sealed interval selection was made with a different configuration; the "
            "protocol must not change after selection"
        )
    if sealed.get("data_fingerprint") != data_fingerprint:
        raise ConfigError("the sealed interval selection was made on different data")
    selection = sealed.get("selection")
    pair_ids = {p.pair_id for p in config.candidates}
    if not isinstance(selection, dict) or selection.get("selected_pair") not in pair_ids:
        raise ConfigError(f"{path} does not select one of the configured candidates")
    if config.selection is None or selection.get("period") != config.selection.period:
        raise ConfigError(f"{path} was not made on the configured selection period")
    ranking = selection.get("ranking")
    if not isinstance(ranking, list) or not ranking:
        raise ConfigError(f"{path} has no ranking")
    return sealed


@dataclass(frozen=True)
class M3Outputs:
    files: dict[str, bytes]
    payload: dict[str, Any]
    outputs: dict[str, str]
    result_fingerprint: str
    backtest: BacktestResult
    probabilistic: ProbabilisticResult


def m3_outputs(
    context: M3Context,
    data: SnapshotData,
    periods: Sequence[str],
    *,
    sealed: Mapping[str, Any] | None,
    flagged_dates: Sequence[date],
    recommendation: Mapping[str, Any] | None,
) -> M3Outputs:
    """Residual backtest, probabilistic forecasts and every deterministic output file."""
    config, backtest = context.config, context.backtest
    models = point_models(context)
    result = run_backtest(data.series, residual_plan(context), models)
    probabilistic = run_probabilistic(
        result.records,
        data.series,
        config,
        buckets=backtest.buckets(),
        data_delay_days=backtest.data_delay_days,
        periods=periods,
        sealed_selection=sealed.get("selection") if sealed else None,
        flagged_dates=flagged_dates,
    )
    payload = {
        **probabilistic.payload,
        "data_fingerprint": data.fingerprint,
        "backtest": {
            "experiment_id": backtest.experiment_id,
            "config_fingerprint": backtest.fingerprint(),
            "residual_first_origin": config.residual_first_origin,
            "residual_last_origin": config.residual_last_origin,
            "n_records": len(result.records),
            "status_counts": result.status_counts(),
        },
        "recommendation": recommendation,
        "models": [s.to_dict() | {"spec_fingerprint": s.fingerprint()} for s in result.models],
    }
    metrics = json_bytes(payload)
    files = {
        "forecasts.csv": csv_bytes(result.records),
        "intervals.csv": csv_bytes(probabilistic.intervals),
        "exceedance.csv": csv_bytes(probabilistic.exceedance),
        "metrics.json": metrics,
        "report.md": render_uncertainty_report(json.loads(metrics)).encode("utf-8"),
        "config.json": json_bytes(config.model_dump(mode="json")),
        "backtest_config.json": json_bytes(backtest.model_dump(mode="json")),
    }
    outputs = {name: sha256_bytes(content) for name, content in files.items()}
    return M3Outputs(
        files,
        payload,
        outputs,
        result_fingerprint(outputs, RESULT_FILES),
        result,
        probabilistic,
    )


def _issues(outputs: M3Outputs, context: M3Context) -> list[Issue]:
    issues: list[Issue] = []
    records = outputs.backtest.records
    failed = records[records["status"] == "failed"]
    if len(failed):
        issues.append(
            Issue(
                "model_failures",
                Severity.WARNING,
                "residuals",
                f"{len(failed)} backtest forecast(s) failed; their intervals are recorded as "
                "point_failed, not dropped.",
                count=len(failed),
            )
        )
    payload = outputs.payload
    decided = payload.get("decided_pair")
    intervals = outputs.probabilistic.intervals
    unavailable = intervals[intervals["status"] == STATUS_UNAVAILABLE]
    if len(unavailable):
        chosen = unavailable[unavailable["pair_id"] == decided] if decided else unavailable
        issues.append(
            Issue(
                "intervals_unavailable",
                Severity.WARNING if len(chosen) else Severity.INFO,
                "calibration",
                f"{len(unavailable)} interval(s) were not issued for lack of eligible residuals "
                f"({len(chosen)} of the selected method); they are reported, not dropped.",
                count=len(unavailable),
            )
        )
    check = payload.get("selection_seal_check")
    if check is not None and check.get("differences"):
        issues.append(
            Issue(
                "selection_differs_from_seal",
                Severity.ERROR,
                "selection",
                "Re-running the selection period does not reproduce the sealed interval "
                "selection (differs in: " + ", ".join(check["differences"]) + ").",
            )
        )
    status = payload.get("decision_status")
    if status is not None and not status["decision_grade"]:
        issues.append(
            Issue(
                "decision_grade_withheld",
                Severity.WARNING,
                context.config.primary_model,
                f"{status['interval_status']}: {status['statement']}",
            )
        )
    return issues


def decision_files(
    outputs: M3Outputs,
    context: M3Context,
    data: SnapshotData,
    run_id: str,
    periods: Sequence[str],
    *,
    sealed: Mapping[str, Any] | None,
    issues: Sequence[Issue],
) -> tuple[bool, dict[str, bytes], list[Issue]]:
    """Whether the run is complete, its decision files, and an issue if any was withheld.

    ``selection.json`` (a selection run without a seal) and ``calibration_status.json`` (a
    run with a decision status) are deterministic functions of the outputs, so ``verify``
    regenerates and compares them like every other file.
    """
    config, payload = context.config, outputs.payload
    complete = not any(i.severity is Severity.ERROR for i in issues)
    selection_period = config.selection.period if config.selection is not None else None
    write_selection = (
        selection_period in periods and sealed is None and bool(payload.get("selection"))
    )
    write_status = payload.get("decision_status") is not None
    withheld = [
        name
        for name, wanted in (
            ("selection.json", write_selection),
            ("calibration_status.json", write_status),
        )
        if wanted and not complete
    ]
    notices = (
        [
            Issue(
                "decision_files_withheld",
                Severity.WARNING,
                "selection",
                "The run is incomplete, so " + " and ".join(withheld) + " was not written.",
            )
        ]
        if withheld
        else []
    )
    common = {
        "experiment_id": config.experiment_id,
        "config_fingerprint": config.fingerprint(),
        "data_fingerprint": data.fingerprint,
        "backtest_config_fingerprint": context.backtest.fingerprint(),
        "result_fingerprint": outputs.result_fingerprint,
        "run_id": run_id,
    }
    files: dict[str, bytes] = {}
    if complete and write_selection:
        files["selection.json"] = json_bytes(
            {
                "record_type": SEALED_INTERVAL_SELECTION_TYPE,
                **common,
                "selection": payload["selection"],
            }
        )
    if complete and write_status:
        files["calibration_status.json"] = json_bytes(
            {
                "record_type": CALIBRATION_STATUS_TYPE,
                **common,
                "decided_pair": payload["decided_pair"],
                "decision_status": payload["decision_status"],
                "recommendation": payload.get("recommendation"),
            }
        )
    return complete, files, notices


def _run_id(config: UncertaintyConfig, data: SnapshotData, periods: Sequence[str]) -> str:
    return f"{config.experiment_id}-{config.fingerprint()[:8]}-{data.fingerprint[:8]}-" + "-".join(
        periods
    )


def run_uncertainty_command(
    settings: Settings,
    config_path: Path,
    *,
    output_dir: Path | None = None,
    periods: Sequence[str] | None = None,
    sealed_selection: Path | None = None,
    require_clean: bool = False,
) -> list[CheckReport]:
    """``atlas uncertainty run``: residual backtest, intervals, evaluation, persisted run."""
    context = load_context(settings, config_path)
    config = context.config
    chosen = list(periods) if periods else default_periods(config)
    unknown = sorted(set(chosen) - set(config.period_names))
    if unknown:
        raise ConfigError(f"unknown period(s) {unknown}; configured: {config.period_names}")
    chosen = [name for name in config.period_names if name in chosen]
    if output_dir is not None:
        _check_output_dir(output_dir)
    git = git_info(settings.repo_root)
    if require_clean and (not git.get("available") or git.get("dirty")):
        raise ConfigError(
            "refusing a reference run: the code, configuration or tests have uncommitted "
            "changes (or git is unavailable)"
        )
    selection_period = config.selection.period if config.selection is not None else None
    needs_seal = selection_period is not None and any(n != selection_period for n in chosen)
    if needs_seal and sealed_selection is None:
        raise ConfigError(
            "periods other than the selection period need --sealed-selection: the "
            "selection.json of a selection run of this configuration (pre-registration)"
        )
    if needs_seal and selection_period not in chosen:
        raise ConfigError(
            f"periods other than the selection period must be run together with "
            f"{selection_period!r} (add --period {selection_period}): the selection "
            "recomputed in the same run must reproduce the sealed one"
        )
    reports, data = load_series(settings, "uncertainty")
    if data is None:
        return reports
    recommendation = recommendation_provenance(settings, context, data)
    run_id = _run_id(config, data, chosen)
    directory = output_dir or settings.repo_root / RUNS_DIRNAME / run_id
    _check_output_dir(directory)
    sealed = (
        load_sealed_interval_selection(sealed_selection, config, data.fingerprint)
        if sealed_selection is not None
        else None
    )
    flagged = _flagged_dates(settings, data) if config.sensitivity.strata else []
    started = time.perf_counter()
    outputs = m3_outputs(
        context,
        data,
        chosen,
        sealed=sealed,
        flagged_dates=flagged,
        recommendation=recommendation,
    )
    runtime = time.perf_counter() - started
    payload = outputs.payload
    issues = _issues(outputs, context)
    complete, decisions, withheld = decision_files(
        outputs, context, data, run_id, chosen, sealed=sealed, issues=issues
    )
    issues += withheld
    files = {**outputs.files, **decisions}
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "record_type": RECORD_TYPE_PROBABILISTIC,
        "label": LABEL,
        "complete": complete,
        "run_id": run_id,
        "experiment_id": config.experiment_id,
        "periods_run": chosen,
        "config_fingerprint": config.fingerprint(),
        "config": config.model_dump(mode="json"),
        "backtest_config_fingerprint": context.backtest.fingerprint(),
        "backtest_config": context.backtest.model_dump(mode="json"),
        "recommendation": recommendation,
        "sealed_selection": sealed,
        "data": data.provenance,
        "flagged_target_dates": flagged,
        "models": payload["models"],
        "code": code_fingerprint(settings.repo_root),
        "git": git,
        "environment": environment_info(),
        "outputs": {name: sha256_bytes(content) for name, content in files.items()},
        "result_files": list(RESULT_FILES),
        "result_fingerprint": outputs.result_fingerprint,
        "informational_not_fingerprinted": {
            "created_at_utc": utc_now(),
            "runtime_seconds": round(runtime, 1),
        },
    }
    write_run(directory, files, manifest)
    reports.append(
        CheckReport(
            name="uncertainty",
            issues=tuple(issues),
            facts={
                "record_type": RECORD_TYPE_PROBABILISTIC,
                "run_dir": str(directory),
                "run_id": run_id,
                "periods": chosen,
                "complete": complete,
                "result_fingerprint": outputs.result_fingerprint,
                "config_fingerprint": config.fingerprint(),
                "data_fingerprint": data.fingerprint,
                "runtime_seconds": round(runtime, 1),
                **headline(payload),
            },
        )
    )
    return reports


def headline(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Compact facts for the text renderer: selection, status and days-1..H coverage."""
    selection = payload.get("selection") or {}
    status = payload.get("decision_status") or {}
    decided = payload.get("decided_pair")
    focus = {decided, *payload["protocol"]["baselines"]}
    all_label = f"days_1_{payload['protocol']['max_horizon']}"
    keys = [level_key(level) for level in payload["protocol"]["levels"]]
    rows = [
        {
            "period": r["period"],
            "model_id": r["model_id"],
            "pair_id": r["pair_id"],
            "n_evaluated": r["n_evaluated"],
            "wis": r.get("wis"),
            **{f"coverage_{k}": r.get(f"coverage_{k}") for k in keys},
        }
        for r in payload["interval_summary"]
        if r["label"] == all_label and r["pair_id"] in focus
    ]
    return {
        "selected_pair": decided,
        "selection_period": selection.get("period"),
        "interval_status": status.get("interval_status"),
        "status_statement": status.get("statement"),
        "levels": keys,
        "coverage": rows,
    }


# --- Verify and inspect --------------------------------------------------------------------


def _recorded_context(settings: Settings, manifest: Mapping[str, Any]) -> M3Context:
    config = parse_uncertainty_config(manifest["config"])
    if config.fingerprint() != manifest.get("config_fingerprint"):
        raise ConfigError("the recorded configuration does not match its recorded fingerprint")
    backtest = parse_config(manifest["backtest_config"])
    return _context(settings, config, backtest)


def verify_uncertainty_command(settings: Settings, run_dir: Path) -> list[CheckReport]:
    """``atlas uncertainty verify``: re-run a recorded M3 run and compare every file."""
    manifest = _read_json(run_dir / "manifest.json", "run manifest")
    if manifest.get("record_type") != RECORD_TYPE_PROBABILISTIC:
        raise ConfigError(f"{run_dir} is not a historical probabilistic backtest run")
    snapshot = (manifest.get("backtest_config") or {}).get("snapshot_id", settings.snapshot_id)
    settings = replace(settings, snapshot_id=snapshot)
    context = _recorded_context(settings, manifest)
    periods = list(manifest.get("periods_run") or default_periods(context.config))
    reports, data = load_series(settings, "verification")
    if data is None:
        return reports
    recorded_data = manifest.get("data", {}).get("data_fingerprint")
    if data.fingerprint != recorded_data:
        reports.append(
            CheckReport(
                name="verification",
                issues=(
                    Issue(
                        "data_fingerprint_mismatch",
                        Severity.ERROR,
                        "verification",
                        f"The run used data {recorded_data}; the snapshot now gives "
                        f"{data.fingerprint}.",
                    ),
                ),
            )
        )
        return reports
    issues: list[Issue] = []
    flagged = _flagged_dates(settings, data) if context.config.sensitivity.strata else []
    recorded_flags = [str(d) for d in manifest.get("flagged_target_dates") or []]
    if [d.isoformat() for d in flagged] != recorded_flags:
        issues.append(
            Issue(
                "flagged_dates_differ",
                Severity.ERROR,
                "flagged_target_dates",
                f"The code now flags {len(flagged)} target date(s); the run recorded "
                f"{len(recorded_flags)}.",
            )
        )
    outputs = m3_outputs(
        context,
        data,
        periods,
        sealed=manifest.get("sealed_selection"),
        flagged_dates=flagged,
        recommendation=recommendation_provenance(settings, context, data),
    )
    recorded = manifest.get("outputs", {})
    _, decisions, _ = decision_files(
        outputs,
        context,
        data,
        str(manifest.get("run_id")),
        periods,
        sealed=manifest.get("sealed_selection"),
        issues=_issues(outputs, context),
    )
    regenerated = {
        **outputs.outputs,
        **{name: sha256_bytes(content) for name, content in decisions.items()},
    }
    for name in ("selection.json", "calibration_status.json"):
        if name in recorded and name not in regenerated:
            issues.append(
                Issue(
                    "decision_file_unexpected",
                    Severity.ERROR,
                    name,
                    f"The run records {name}, but re-running it does not produce one.",
                )
            )
    files: dict[str, str] = {}
    for name, digest in regenerated.items():
        same = recorded.get(name) == digest
        files[name] = "identical" if same else "different"
        if not same:
            issues.append(
                Issue(
                    "output_differs",
                    Severity.ERROR,
                    name,
                    f"SHA-256 {digest} differs from the recorded {recorded.get(name)}.",
                )
            )
    if manifest.get("result_fingerprint") != outputs.result_fingerprint:
        issues.append(
            Issue(
                "result_fingerprint_differs",
                Severity.ERROR,
                "manifest.json",
                f"Result fingerprint {outputs.result_fingerprint} differs from the recorded "
                f"{manifest.get('result_fingerprint')}.",
            )
        )
    altered = _altered_files(run_dir, recorded)
    issues += [
        Issue("file_altered", Severity.ERROR, name, f"{name} no longer matches manifest.json.")
        for name in altered
    ]
    now_code = code_fingerprint(settings.repo_root)
    now_git = git_info(settings.repo_root)
    differences = {
        "code_sha256": [manifest.get("code", {}).get("code_sha256"), now_code["code_sha256"]],
        "git_commit": [manifest.get("git", {}).get("commit"), now_git.get("commit")],
    }
    reports.append(
        CheckReport(
            name="uncertainty_verification",
            issues=tuple(issues),
            facts={
                "run_dir": str(run_dir),
                "recorded_result_fingerprint": manifest.get("result_fingerprint"),
                "result_fingerprint": outputs.result_fingerprint,
                "identical": not issues,
                "files": files,
                "altered_on_disk": altered,
                "provenance_differences": {k: v for k, v in differences.items() if v[0] != v[1]},
            },
        )
    )
    return reports


def _altered_files(run_dir: Path, recorded: Mapping[str, Any]) -> list[str]:
    """Files kept in the run directory that no longer match their recorded SHA-256."""
    return [
        name
        for name, expected in sorted(recorded.items())
        if (run_dir / name).is_file() and sha256_file(run_dir / name) != expected
    ]


def inspect_uncertainty_command(run_dir: Path) -> list[CheckReport]:
    """``atlas uncertainty inspect``: integrity of a run directory and its headline results.

    Recomputes nothing. Large files that are not kept (for example ``intervals.csv``) are
    listed as absent; ``verify`` regenerates and checks them.
    """
    manifest = _read_json(run_dir / "manifest.json", "run manifest")
    if manifest.get("record_type") != RECORD_TYPE_PROBABILISTIC:
        raise ConfigError(f"{run_dir} is not a historical probabilistic backtest run")
    recorded = manifest.get("outputs", {})
    altered = _altered_files(run_dir, recorded)
    absent = sorted(name for name in recorded if not (run_dir / name).is_file())
    issues = [
        Issue("file_altered", Severity.ERROR, name, f"{name} no longer matches manifest.json.")
        for name in altered
    ]
    if manifest.get("complete") is not True:
        issues.append(
            Issue(
                "run_incomplete",
                Severity.ERROR,
                "manifest.json",
                "The run is marked incomplete: its results are not a valid protocol run.",
            )
        )
    if "metrics.json" in absent:
        issues.append(
            Issue("metrics_missing", Severity.ERROR, "metrics.json", "metrics.json is absent.")
        )
        return [CheckReport(name="uncertainty_inspection", issues=tuple(issues))]
    payload = _read_json(run_dir / "metrics.json", "metrics")
    return [
        CheckReport(
            name="uncertainty_inspection",
            issues=tuple(issues),
            facts={
                "run_dir": str(run_dir),
                "run_id": manifest.get("run_id"),
                "complete": manifest.get("complete"),
                "periods": manifest.get("periods_run"),
                "result_fingerprint": manifest.get("result_fingerprint"),
                "files_checked": sorted(set(recorded) - set(absent)),
                "files_absent": absent,
                "altered_on_disk": altered,
                **headline(payload),
            },
        )
    ]


# --- Projection with intervals ---------------------------------------------------------------


def _status_source(path: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    """The calibration status must come, unchanged, from a complete M3 evaluation run."""
    manifest = _read_json(path.parent / "manifest.json", "manifest of the M3 run")
    problems = []
    if manifest.get("record_type") != RECORD_TYPE_PROBABILISTIC:
        problems.append("its directory holds no M3 run manifest")
    if manifest.get("complete") is not True:
        problems.append("the run that wrote it is not complete")
    if manifest.get("outputs", {}).get(path.name) != sha256_file(path):
        problems.append("the file is not the one the run recorded")
    if manifest.get("result_fingerprint") != record.get("result_fingerprint"):
        problems.append("its result fingerprint differs from the run's")
    # The status must say what the run's metrics say; metrics.json is part of the result
    # fingerprint, so an edited status file cannot be re-hashed into agreement.
    metrics_path = path.parent / "metrics.json"
    outputs = manifest.get("outputs", {})
    if not metrics_path.is_file() or outputs.get("metrics.json") != sha256_file(metrics_path):
        problems.append("the run's metrics.json is missing or altered")
    elif result_fingerprint(outputs, RESULT_FILES) != manifest.get("result_fingerprint"):
        problems.append("the run's result files do not give its result fingerprint")
    else:
        metrics = _read_json(metrics_path, "metrics of the M3 run")
        if record.get("decision_status") != metrics.get("decision_status") or record.get(
            "decided_pair"
        ) != metrics.get("decided_pair"):
            problems.append("its decision status differs from the run's metrics.json")
    if problems:
        raise ConfigError(f"{path} cannot be used: " + "; ".join(problems))
    return dict(manifest)


def uncertainty_projection_command(
    settings: Settings, status_path: Path, *, output_dir: Path | None = None
) -> list[CheckReport]:
    """``atlas uncertainty project``: the selected intervals on a future projection.

    The point forecasts are those of ``atlas forecast`` (the same
    :func:`~atlas.backtest.project` path); the intervals use the sealed method calibrated at
    the last observed day with every residual observed by then. The residual backtest is
    recomputed and must reproduce the evaluation run's ``forecasts.csv`` byte for byte.
    """
    if output_dir is not None:
        _check_output_dir(output_dir)
    record = _read_json(status_path, "calibration status")
    if record.get("record_type") != CALIBRATION_STATUS_TYPE:
        raise ConfigError(f"{status_path} is not a calibration status record")
    manifest = _status_source(status_path, record)
    snapshot = (manifest.get("backtest_config") or {}).get("snapshot_id", settings.snapshot_id)
    settings = replace(settings, snapshot_id=snapshot)
    context = _recorded_context(settings, manifest)
    config = context.config
    reports, data = load_series(settings, "uncertainty_projection")
    if data is None:
        return reports
    if record.get("data_fingerprint") != data.fingerprint:
        raise ConfigError("the calibration status was made on different data")
    run_id = f"{config.experiment_id}-{config.fingerprint()[:8]}-{data.fingerprint[:8]}"
    directory = output_dir or settings.repo_root / RUNS_DIRNAME / "projections" / run_id
    _check_output_dir(directory)
    models = point_models(context)
    residuals = run_backtest(data.series, residual_plan(context), models)
    issues: list[Issue] = []
    if sha256_bytes(csv_bytes(residuals.records)) != manifest.get("outputs", {}).get(
        "forecasts.csv"
    ):
        issues.append(
            Issue(
                "residuals_differ",
                Severity.ERROR,
                "forecasts.csv",
                "The recomputed residual backtest differs from the evaluation run's; the "
                "intervals would not be calibrated on the evaluated residuals.",
            )
        )
        reports.append(CheckReport(name="uncertainty_projection", issues=tuple(issues)))
        return reports
    projection = project(
        data.series,
        models,
        max_horizon=config.max_horizon,
        training_window=TrainingWindow(context.backtest.training_window.days),
    )
    origin = projection.origin
    # Status "projected" (no actual): a point prediction, never a residual.
    points = projection.records.assign(period="projection")
    decided = str(record.get("decided_pair"))
    pairs = [config.pair(decided), *config.baselines]
    pair_ids = [p.pair_id for p in pairs]
    settings_engine = EngineSettings.from_config(
        config, context.backtest.buckets(), context.backtest.data_delay_days
    )
    period = UncertaintyPeriodConfig(
        name="projection", role="evaluation", first_origin=origin, last_origin=origin
    )
    intervals, exceedance = issue_forecasts(
        pd.concat([residuals.records, points], ignore_index=True),
        data.series,
        config,
        settings_engine,
        periods=[period],
        pairs=pairs,
        exceedance_pairs=pair_ids,
        record_type=RECORD_TYPE_PROJECTION_INTERVALS,
    )
    for frame in (intervals, exceedance):
        frame.drop(columns=["actual"], inplace=True)
        frame["status"] = frame["status"].replace(STATUS_OK, "projected")
    exceedance.drop(columns=["event"], inplace=True)
    status = record.get("decision_status") or {}
    interval_status = str(status.get("interval_status", "unavailable"))
    files = {
        "projection_intervals.csv": csv_bytes(intervals),
        "projection_exceedance.csv": csv_bytes(exceedance),
        "report.md": _projection_report(
            config, origin, intervals, exceedance, decided, interval_status, data
        ).encode("utf-8"),
    }
    outputs = {name: sha256_bytes(content) for name, content in files.items()}
    write_run(
        directory,
        files,
        {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "record_type": RECORD_TYPE_PROJECTION_INTERVALS,
            "label": PROJECTION_LABEL,
            "run_id": run_id,
            "origin": origin,
            "decided_pair": decided,
            "calibration_status": interval_status,
            "status_guidance": STATUS_GUIDANCE.get(interval_status),
            "status_source": {
                "run_id": manifest.get("run_id"),
                "result_fingerprint": manifest.get("result_fingerprint"),
                "calibration_status_sha256": sha256_file(status_path),
            },
            "config_fingerprint": config.fingerprint(),
            "config": config.model_dump(mode="json"),
            "backtest_config_fingerprint": context.backtest.fingerprint(),
            "data": data.provenance,
            "thresholds": threshold_descriptions(config),
            "models": [
                s.to_dict() | {"spec_fingerprint": s.fingerprint()} for s in projection.models
            ],
            "code": code_fingerprint(settings.repo_root),
            "git": git_info(settings.repo_root),
            "environment": environment_info(),
            "outputs": outputs,
            "informational_not_fingerprinted": {"created_at_utc": utc_now()},
        },
    )
    issues.append(
        Issue(
            "projection_without_outcomes",
            Severity.INFO,
            "projection",
            PROJECTION_LABEL,
        )
    )
    if interval_status != DECISION_GRADE:
        issues.append(
            Issue(
                "not_decision_grade",
                Severity.WARNING,
                "projection",
                STATUS_GUIDANCE.get(interval_status, STATUS_GUIDANCE["unavailable"]),
            )
        )
    not_issued = intervals[intervals["status"] != "projected"]
    if len(not_issued):
        issues.append(
            Issue(
                "projection_intervals_unavailable",
                Severity.WARNING,
                "projection",
                f"{len(not_issued)} projected interval(s) could not be issued.",
                count=len(not_issued),
            )
        )
    primary = intervals[
        (intervals["model_id"] == config.primary_model) & (intervals["pair_id"] == decided)
    ]
    columns = level_quantile_columns(config)
    reports.append(
        CheckReport(
            name="uncertainty_projection",
            issues=tuple(issues),
            facts={
                "record_type": RECORD_TYPE_PROJECTION_INTERVALS,
                "run_dir": str(directory),
                "origin": origin.isoformat(),
                "decided_pair": decided,
                "calibration_status": interval_status,
                "rows": [
                    {
                        "target_date": pd.Timestamp(row["target_date"]).date().isoformat(),
                        "prediction": row["prediction"],
                        **{
                            f"interval_{key}": [row[low], row[high]]
                            for key, (low, high) in columns.items()
                        },
                    }
                    for _, row in primary.iterrows()
                ],
            },
        )
    )
    return reports


def _projection_report(
    config: UncertaintyConfig,
    origin: date,
    intervals: pd.DataFrame,
    exceedance: pd.DataFrame,
    decided: str,
    interval_status: str,
    data: SnapshotData,
) -> str:
    columns = level_quantile_columns(config)
    lines = [
        f"# Future projection with intervals: {config.experiment_id}",
        "",
        f"**{PROJECTION_LABEL}**",
        "",
        f"**Calibration status: {interval_status.upper()}.** "
        + STATUS_GUIDANCE.get(interval_status, STATUS_GUIDANCE["unavailable"]),
        "",
        f"- origin (last observed day): {origin}",
        f"- method: `{decided}`, calibrated at the origin on every residual observed by then",
        f"- data fingerprint: `{data.fingerprint}`",
        "",
    ]
    for threshold in threshold_descriptions(config):
        lines.append(f"- threshold `{threshold['label']}`: {threshold['notice']}")
    lines.append("")
    for model_id in config.point_models:
        chosen = intervals[(intervals["model_id"] == model_id) & (intervals["pair_id"] == decided)]
        probabilities = exceedance[
            (exceedance["model_id"] == model_id) & (exceedance["pair_id"] == decided)
        ]
        labels = [t.label for t in config.thresholds]
        header = [
            "date",
            "h",
            "forecast",
            *(f"{key} % interval" for key in columns),
            *(f"P(> {label})" for label in labels),
        ]
        rows = []
        for _, row in chosen.iterrows():
            target = pd.Timestamp(row["target_date"])
            chances = probabilities[pd.DatetimeIndex(probabilities["target_date"]) == target]
            by_label = dict(zip(chances["threshold_label"], chances["probability"], strict=True))
            rows.append(
                "| "
                + " | ".join(
                    [
                        target.date().isoformat(),
                        str(row["horizon"]),
                        f"{row['prediction']:,.0f}",
                        *(_range(row[low], row[high]) for low, high in columns.values()),
                        *(
                            "n/a" if pd.isna(by_label.get(label)) else f"{by_label[label]:.2f}"
                            for label in labels
                        ),
                    ]
                )
                + " |"
            )
        lines += [
            f"## `{model_id}`" + (" (primary)" if model_id == config.primary_model else ""),
            "",
            "| " + " | ".join(header) + " |",
            "|" + "|".join("---" for _ in header) + "|",
            *rows,
            "",
        ]
    return "\n".join(lines).rstrip("\n") + "\n"


def _range(low: float, high: float) -> str:
    return "n/a" if pd.isna(low) or pd.isna(high) else f"{low:,.0f}-{high:,.0f}"
