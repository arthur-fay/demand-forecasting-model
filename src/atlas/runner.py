"""Fail-closed M2 commands: backtest, verify, legacy refit check, future projection.

Each command first runs the M0 integrity and validation stages on the selected snapshot
(:func:`atlas.pipeline.validated_tables`). If either fails, the M2 stage is reported as
skipped and nothing is computed. Configuration problems raise
:class:`~atlas.errors.ConfigError` (exit code 2).

Pre-registration is enforced here. ``atlas backtest run`` evaluates only the selection
(development) period unless other periods are named, and any other period of a protocol
with a selection rule needs ``--sealed-selection``: the ``selection.json`` written by an
earlier development run of the *same* configuration on the *same* data. The sealed
choice is then used unchanged; if the development period is run again it must
reproduce the sealed choice exactly.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from atlas.backtest import (
    RECORD_TYPE_BACKTEST,
    RECORD_TYPE_LEGACY_HOLDOUT,
    RECORD_TYPE_PROJECTION,
    BacktestResult,
    PeriodRole,
    TrainingWindow,
    project,
    run_backtest,
)
from atlas.config import Settings
from atlas.errors import ConfigError, DataQualityError
from atlas.evaluation import evaluate
from atlas.experiment import (
    MANIFEST_SCHEMA_VERSION,
    RECORD_TYPE_LABELS,
    ExperimentConfig,
    code_fingerprint,
    csv_bytes,
    environment_info,
    git_info,
    json_bytes,
    load_config,
    parse_config,
    result_fingerprint,
    series_fingerprint,
    sha256_bytes,
    utc_now,
    write_run,
)
from atlas.issues import CheckReport, Issue, Severity
from atlas.legacy_refit import LegacyHoldout, legacy_holdout_rows, legacy_refit_check
from atlas.loader import LoadedTable
from atlas.pipeline import validated_tables
from atlas.quality import snapshot_flags, warning_flag_dates
from atlas.report import render_report
from atlas.series import DemandSeries
from atlas.snapshot import load_manifest, sha256_file
from atlas.validation import load_exceptions

RESULT_FILES: Final = (
    "forecasts.csv",
    "fits.csv",
    "coefficients.csv",
    "observed_inputs.csv",
    "metrics.json",
)
RUNS_DIRNAME: Final = "runs"
SEALED_SELECTION_TYPE: Final = "sealed_selection"
RECOMMENDATION_TYPE: Final = "recommendation"
LEGACY_TABLES: Final = ("forecast_test_results", "future_30_day_forecast", "model_metrics")


@dataclass(frozen=True)
class SnapshotData:
    series: DemandSeries
    tables: dict[str, LoadedTable]
    provenance: dict[str, Any]

    @property
    def fingerprint(self) -> str:
        return str(self.provenance["data_fingerprint"])


def load_series(
    settings: Settings, stage_name: str
) -> tuple[list[CheckReport], SnapshotData | None]:
    """Integrity -> validation, then the validated ``daily_total`` as a series."""
    reports, tables = validated_tables(settings, stage_name)
    if tables is None:
        return reports, None
    total = tables.get("daily_total")
    if total is None:
        reports.append(
            CheckReport(
                name=stage_name,
                issues=(
                    Issue(
                        "not_applicable",
                        Severity.ERROR,
                        stage_name,
                        f"Snapshot {settings.snapshot_id!r} has no daily_total table.",
                    ),
                ),
            )
        )
        return reports, None
    series = DemandSeries.from_frame(total.frame, name="daily_total", allow_missing=True)
    entry = load_manifest(settings.manifest_path).file("daily_total")
    provenance: dict[str, Any] = {
        "snapshot_id": settings.snapshot_id,
        "manifest_sha256": sha256_file(settings.manifest_path),
        "table": "daily_total",
        "table_path": entry.path,
        "table_sha256": entry.sha256,
        "series_fingerprint": series_fingerprint(series),
        "first_date": series.start,
        "last_date": series.end,
        "n_days": len(series),
        "n_missing_days": len(series.missing_dates),
    }
    canonical = json.dumps(
        {k: provenance[k] for k in ("snapshot_id", "table_sha256", "series_fingerprint")},
        sort_keys=True,
    )
    provenance["data_fingerprint"] = sha256_bytes(canonical.encode("utf-8"))
    return reports, SnapshotData(series, tables, provenance)


def _check_snapshot(settings: Settings, config: ExperimentConfig) -> None:
    if settings.snapshot_id != config.snapshot_id:
        raise ConfigError(
            f"the configuration is for snapshot {config.snapshot_id!r}, but "
            f"{settings.snapshot_id!r} is selected (--snapshot or ATLAS_SNAPSHOT_ID)"
        )


def _read_json(path: Path, what: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read {what} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{what} {path} is not a JSON object")
    return payload


def default_periods(config: ExperimentConfig) -> list[str]:
    """The selection period alone when the protocol has one, otherwise every period."""
    if config.selection is not None:
        return [config.selection.period]
    return config.period_names


def load_sealed_selection(
    path: Path, config: ExperimentConfig, data_fingerprint: str
) -> dict[str, Any]:
    """A ``selection.json`` from a development run of this configuration and data."""
    sealed = _read_json(path, "sealed selection")
    if sealed.get("record_type") != SEALED_SELECTION_TYPE:
        raise ConfigError(f"{path} is not a sealed selection record")
    if sealed.get("config_fingerprint") != config.fingerprint():
        raise ConfigError(
            "the sealed selection was made with a different configuration "
            f"({sealed.get('config_fingerprint')}); the protocol must not change after selection"
        )
    if sealed.get("data_fingerprint") != data_fingerprint:
        raise ConfigError("the sealed selection was made on different data")
    selection = sealed.get("selection")
    if not isinstance(selection, dict) or not selection.get("selected_model"):
        raise ConfigError(f"{path} contains no selection")
    return sealed


def _flagged_dates(settings: Settings, data: SnapshotData) -> list[Any]:
    segments = data.tables.get("daily_segments")
    total = data.tables.get("daily_total")
    if segments is None or total is None:
        return []
    register = load_exceptions(settings.exceptions_path, settings.snapshot_id)
    try:
        flags = snapshot_flags(total.frame, segments.frame, register)
    except DataQualityError:
        return []
    return warning_flag_dates(flags)


def _legacy_holdout_block(data: SnapshotData) -> tuple[dict[str, Any] | None, LegacyHoldout | None]:
    if any(name not in data.tables for name in LEGACY_TABLES):
        return None, None
    report, holdout = legacy_refit_check(
        data.series, *(data.tables[name].frame for name in LEGACY_TABLES)
    )
    if not report.ok:
        return None, holdout
    return legacy_holdout_rows(holdout, data.tables["model_metrics"].frame), holdout


# --- Backtest -------------------------------------------------------------------------


@dataclass(frozen=True)
class BacktestOutputs:
    files: dict[str, bytes]
    payload: dict[str, Any]
    outputs: dict[str, str]
    result_fingerprint: str


def backtest_outputs(
    result: BacktestResult,
    config: ExperimentConfig,
    data: SnapshotData,
    *,
    sealed: Mapping[str, Any] | None,
    flagged_dates: Sequence[Any],
    legacy_block: Mapping[str, Any] | None,
) -> BacktestOutputs:
    """Every deterministic output file of a backtest, plus their hashes."""
    payload = evaluate(
        result,
        config,
        data_fingerprint=data.fingerprint,
        sealed_selection=sealed.get("selection") if sealed else None,
        flagged_dates=flagged_dates,
        legacy_holdout=legacy_block,
    )
    metrics = json_bytes(payload)
    files = {
        "forecasts.csv": csv_bytes(result.records),
        "fits.csv": csv_bytes(result.fits),
        "coefficients.csv": csv_bytes(result.coefficients),
        "observed_inputs.csv": csv_bytes(result.observed_inputs),
        "metrics.json": metrics,
        # Rendered from the persisted JSON, so the report can always be regenerated from it.
        "report.md": render_report(json.loads(metrics)).encode("utf-8"),
        "config.json": json_bytes(config.model_dump(mode="json")),
    }
    outputs = {name: sha256_bytes(content) for name, content in files.items()}
    return BacktestOutputs(files, payload, outputs, result_fingerprint(outputs, RESULT_FILES))


def _run_id(config: ExperimentConfig, data: SnapshotData, periods: Sequence[str]) -> str:
    return f"{config.experiment_id}-{config.fingerprint()[:8]}-{data.fingerprint[:8]}-" + "-".join(
        periods
    )


def _backtest_issues(result: BacktestResult, config: ExperimentConfig) -> list[Issue]:
    issues: list[Issue] = []
    records = result.records
    for model_id in [spec.model_id for spec in result.models]:
        failed = records[(records["model_id"] == model_id) & (records["status"] == "failed")]
        if len(failed):
            codes = failed["failure_code"].value_counts().sort_index()
            issues.append(
                Issue(
                    "model_failures",
                    Severity.WARNING,
                    model_id,
                    f"{len(failed)} forecast(s) failed and are reported, not dropped: "
                    + ", ".join(f"{code} x{n}" for code, n in codes.items()),
                    count=len(failed),
                )
            )
    decision = [p.name for p in result.plan.periods if p.role is not PeriodRole.DESCRIPTIVE]
    reference_failed = records[
        (records["model_id"] == config.reference_model)
        & records["period"].isin(decision)
        & (records["status"] == "failed")
    ]
    if len(reference_failed):
        issues.append(
            Issue(
                "reference_incomplete",
                Severity.ERROR,
                config.reference_model,
                "The reference model failed on some forecasts, so comparisons with it are "
                "incomplete; the run is marked INCOMPLETE.",
                count=len(reference_failed),
            )
        )
    nonestimable = int(result.fits["n_nonestimable"].fillna(0).sum())
    if nonestimable:
        issues.append(
            Issue(
                "nonestimable_predictions",
                Severity.INFO,
                "regressions",
                f"{nonestimable} forecast-period row(s) are not estimable from a rank-deficient "
                "training design: their values depend on the minimum-norm solution. They are "
                "kept in the metrics and reported separately.",
                count=nonestimable,
            )
        )
    return issues


def _selection_matches(sealed: Mapping[str, Any], recomputed: Mapping[str, Any] | None) -> bool:
    if recomputed is None:
        return True
    keys = ("selected_model", "best_baseline", "best_regression")
    rank = [r["model_id"] for r in sealed.get("ranking", [])]
    return all(sealed.get(k) == recomputed.get(k) for k in keys) and rank == [
        r["model_id"] for r in recomputed.get("ranking", [])
    ]


def run_backtest_command(
    settings: Settings,
    config_path: Path,
    *,
    output_dir: Path | None = None,
    periods: Sequence[str] | None = None,
    sealed_selection: Path | None = None,
    require_clean: bool = False,
) -> list[CheckReport]:
    """``atlas backtest run``: validate the data, backtest, evaluate and persist the run."""
    config = load_config(config_path)
    _check_snapshot(settings, config)
    chosen = list(periods) if periods else default_periods(config)
    plan = config.plan(chosen)
    git = git_info(settings.repo_root)
    if require_clean and (not git.get("available") or git.get("dirty")):
        raise ConfigError(
            "refusing a reference run: the code, configuration or tests have uncommitted "
            "changes (or git is unavailable)"
        )
    needs_seal = config.selection is not None and any(
        p.role is not PeriodRole.SELECTION for p in plan.periods
    )
    if needs_seal and sealed_selection is None:
        raise ConfigError(
            "periods other than the selection period need --sealed-selection: the "
            "selection.json of a development run of this configuration (pre-registration)"
        )
    reports, data = load_series(settings, "backtest")
    if data is None:
        return reports
    sealed = (
        load_sealed_selection(sealed_selection, config, data.fingerprint)
        if sealed_selection is not None
        else None
    )
    flagged = _flagged_dates(settings, data) if config.sensitivity.exclude_flagged_targets else []
    legacy_block: dict[str, Any] | None = None
    if any(w.kind == "legacy_holdout" and w.period in chosen for w in config.comparison_windows):
        legacy_block, _ = _legacy_holdout_block(data)
    started = time.perf_counter()
    result = run_backtest(data.series, plan, config.build_models())
    runtime = time.perf_counter() - started
    outputs = backtest_outputs(
        result, config, data, sealed=sealed, flagged_dates=flagged, legacy_block=legacy_block
    )
    payload = outputs.payload
    run_id = _run_id(config, data, chosen)
    issues = _backtest_issues(result, config)
    if sealed is not None and not _selection_matches(
        sealed["selection"], payload.get("selection_recomputed")
    ):
        issues.append(
            Issue(
                "selection_differs_from_seal",
                Severity.ERROR,
                "selection",
                "Re-running the development period does not reproduce the sealed selection.",
            )
        )
    files = dict(outputs.files)
    selection_period_run = config.selection is not None and config.selection.period in chosen
    if selection_period_run and sealed is None and payload.get("selection"):
        files["selection.json"] = json_bytes(
            {
                "record_type": SEALED_SELECTION_TYPE,
                "experiment_id": config.experiment_id,
                "config_fingerprint": config.fingerprint(),
                "data_fingerprint": data.fingerprint,
                "result_fingerprint": outputs.result_fingerprint,
                "run_id": run_id,
                "selection": payload["selection"],
            }
        )
    if payload.get("recommendation"):
        files["recommendation.json"] = json_bytes(
            {
                "record_type": RECOMMENDATION_TYPE,
                "experiment_id": config.experiment_id,
                "config_fingerprint": config.fingerprint(),
                "data_fingerprint": data.fingerprint,
                "result_fingerprint": outputs.result_fingerprint,
                "recommendation": payload["recommendation"],
            }
        )
    directory = output_dir or settings.repo_root / RUNS_DIRNAME / run_id
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "record_type": RECORD_TYPE_BACKTEST,
        "label": RECORD_TYPE_LABELS[RECORD_TYPE_BACKTEST],
        "complete": not any(i.severity is Severity.ERROR for i in issues),
        "run_id": run_id,
        "experiment_id": config.experiment_id,
        "periods_run": chosen,
        "config_fingerprint": config.fingerprint(),
        "config": config.model_dump(mode="json"),
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
        "status_counts": result.status_counts(),
        "informational_not_fingerprinted": {
            "created_at_utc": utc_now(),
            "runtime_seconds": round(runtime, 1),
        },
    }
    write_run(directory, files, manifest)
    reports.append(
        CheckReport(
            name="backtest",
            issues=tuple(issues),
            facts={
                "record_type": RECORD_TYPE_BACKTEST,
                "run_dir": str(directory),
                "run_id": run_id,
                "periods": chosen,
                "result_fingerprint": outputs.result_fingerprint,
                "config_fingerprint": config.fingerprint(),
                "data_fingerprint": data.fingerprint,
                "records": len(result.records),
                "origins": len(plan.origins()),
                "selection": _selection_facts(payload),
                "buckets": _bucket_table(payload),
                "runtime_seconds": round(runtime, 1),
            },
        )
    )
    return reports


def _selection_facts(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    selection = payload.get("selection")
    if not selection:
        return None
    recommendation = payload.get("recommendation") or {}
    return {
        "period": selection.get("period"),
        "best_baseline": selection.get("best_baseline"),
        "best_regression": selection.get("best_regression"),
        "selected_model": selection.get("selected_model"),
        "recommended_model": recommendation.get("recommended_model"),
        "recommendation_reason": recommendation.get("reason"),
    }


def _bucket_table(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Compact rows for the text renderer: MAE and improvement per model, period, bucket."""
    versus = {
        (r["model_id"], r["period"], r["label"]): r["mae_improvement_pct"]
        for r in payload["comparison_to_reference"]
    }
    return [
        {
            "period": r["period"],
            "label": r["label"],
            "model_id": r["model_id"],
            "n_predictions": r["n_predictions"],
            "mae": r["mae"],
            "mae_improvement_pct": versus.get((r["model_id"], r["period"], r["label"])),
        }
        for r in payload["summary"]
        if r["scope"] == "bucket"
    ]


# --- Verify -----------------------------------------------------------------------------


def verify_command(settings: Settings, run_dir: Path) -> list[CheckReport]:
    """``atlas backtest verify``: re-run a recorded backtest and compare its result files.

    Succeeds only when every result file is byte-identical (same result fingerprint). A
    numeric comparison of the predictions, and any difference in code, git commit or
    environment, are reported as diagnostics.
    """
    manifest = _read_json(run_dir / "manifest.json", "run manifest")
    if manifest.get("record_type") != RECORD_TYPE_BACKTEST:
        raise ConfigError(f"{run_dir} is not a historical backtest run")
    config = parse_config(manifest["config"])
    if config.fingerprint() != manifest.get("config_fingerprint"):
        raise ConfigError("the recorded configuration does not match its recorded fingerprint")
    settings = replace(settings, snapshot_id=config.snapshot_id)
    periods = list(manifest.get("periods_run") or default_periods(config))
    plan = config.plan(periods)
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
                        f"{data.fingerprint}. A run can only be verified on its own data.",
                    ),
                ),
            )
        )
        return reports
    sealed = manifest.get("sealed_selection")
    flagged = manifest.get("flagged_target_dates") or []
    legacy_block: dict[str, Any] | None = None
    if any(w.kind == "legacy_holdout" and w.period in periods for w in config.comparison_windows):
        legacy_block, _ = _legacy_holdout_block(data)
    result = run_backtest(data.series, plan, config.build_models())
    outputs = backtest_outputs(
        result,
        config,
        data,
        sealed=sealed,
        flagged_dates=[pd.Timestamp(d).date() for d in flagged],
        legacy_block=legacy_block,
    )
    recorded = manifest.get("outputs", {})
    issues: list[Issue] = []
    files: dict[str, str] = {}
    for name in RESULT_FILES:
        same = recorded.get(name) == outputs.outputs[name]
        files[name] = "identical" if same else "different"
        if not same:
            issues.append(
                Issue(
                    "output_differs",
                    Severity.ERROR,
                    name,
                    f"SHA-256 {outputs.outputs[name]} differs from the recorded "
                    f"{recorded.get(name)}.",
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
    # Every file kept in the run directory must still be the file the run wrote.
    altered: list[str] = []
    for name, expected in sorted(recorded.items()):
        path = run_dir / name
        if path.is_file() and sha256_file(path) != expected:
            altered.append(name)
            issues.append(
                Issue(
                    "file_altered",
                    Severity.ERROR,
                    name,
                    f"{path} no longer matches the SHA-256 recorded in manifest.json.",
                )
            )
    now_code = code_fingerprint(settings.repo_root)
    now_git = git_info(settings.repo_root)
    now_env = environment_info()
    differences = {
        "code_sha256": [manifest.get("code", {}).get("code_sha256"), now_code["code_sha256"]],
        "git_commit": [manifest.get("git", {}).get("commit"), now_git.get("commit")],
        "packages": [manifest.get("environment", {}).get("packages"), now_env["packages"]],
    }
    reports.append(
        CheckReport(
            name="verification",
            issues=tuple(issues),
            facts={
                "run_dir": str(run_dir),
                "recorded_result_fingerprint": manifest.get("result_fingerprint"),
                "result_fingerprint": outputs.result_fingerprint,
                "identical": not issues,
                "files": files,
                "altered_on_disk": altered,
                "forecast_difference": _numeric_difference(
                    run_dir / "forecasts.csv", result.records
                ),
                "provenance_differences": {k: v for k, v in differences.items() if v[0] != v[1]},
            },
        )
    )
    return reports


def _numeric_difference(path: Path, records: pd.DataFrame) -> dict[str, Any] | None:
    """Largest absolute prediction difference against a recorded forecasts.csv, if present."""
    if not path.is_file():
        return None
    recorded = pd.read_csv(path, float_precision="round_trip")
    if len(recorded) != len(records):
        return {"comparable": False, "reason": "different number of records"}
    old = recorded["prediction"].to_numpy(dtype="float64")
    new = records["prediction"].to_numpy(dtype="float64")
    both = ~(np.isnan(old) | np.isnan(new))
    return {
        "comparable": True,
        "same_failures": bool((np.isnan(old) == np.isnan(new)).all()),
        "max_abs_prediction_difference": (
            float(np.abs(old[both] - new[both]).max()) if both.any() else 0.0
        ),
    }


# --- Legacy refit ---------------------------------------------------------------------


def legacy_refit_command(
    settings: Settings, *, output_dir: Path | None = None
) -> list[CheckReport]:
    """``atlas backtest legacy``: refit the legacy model and compare with its outputs."""
    reports, data = load_series(settings, "legacy_refit")
    if data is None:
        return reports
    missing = [name for name in LEGACY_TABLES if name not in data.tables]
    if missing:
        reports.append(
            CheckReport(
                name="legacy_refit",
                issues=(
                    Issue(
                        "not_applicable",
                        Severity.ERROR,
                        "legacy_refit",
                        f"Snapshot {settings.snapshot_id!r} lacks {missing}.",
                    ),
                ),
            )
        )
        return reports
    report, holdout = legacy_refit_check(
        data.series, *(data.tables[name].frame for name in LEGACY_TABLES)
    )
    if output_dir is not None:
        rows = legacy_holdout_rows(holdout, data.tables["model_metrics"].frame)
        files = {
            "legacy_one_step_holdout.csv": csv_bytes(holdout.records()),
            "legacy_one_step_holdout.json": json_bytes(rows | {"check": dict(report.facts)}),
        }
        write_run(
            output_dir,
            files,
            {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "record_type": RECORD_TYPE_LEGACY_HOLDOUT,
                "label": RECORD_TYPE_LABELS[RECORD_TYPE_LEGACY_HOLDOUT],
                "data": data.provenance,
                "code": code_fingerprint(settings.repo_root),
                "git": git_info(settings.repo_root),
                "environment": environment_info(),
                "outputs": {name: sha256_bytes(content) for name, content in files.items()},
                "informational_not_fingerprinted": {"created_at_utc": utc_now()},
            },
        )
        report = replace(report, facts={**report.facts, "output_dir": str(output_dir)})
    reports.append(report)
    return reports


# --- Projection -----------------------------------------------------------------------


def projection_command(
    settings: Settings,
    config_path: Path,
    *,
    output_dir: Path | None = None,
    recommendation: Path | None = None,
) -> list[CheckReport]:
    """``atlas forecast``: future projection from the last observed day (not validated).

    With ``--recommendation`` (the ``recommendation.json`` of a confirmation run of the
    same configuration and data) only the recommended model is projected; otherwise
    every configured model is, for comparison.
    """
    config = load_config(config_path)
    _check_snapshot(settings, config)
    reports, data = load_series(settings, "projection")
    if data is None:
        return reports
    models = config.build_models()
    recommended: str | None = None
    if recommendation is not None:
        record = _read_json(recommendation, "recommendation")
        if record.get("record_type") != RECOMMENDATION_TYPE:
            raise ConfigError(f"{recommendation} is not a recommendation record")
        if record.get("config_fingerprint") != config.fingerprint():
            raise ConfigError("the recommendation was made with a different configuration")
        if record.get("data_fingerprint") != data.fingerprint:
            raise ConfigError("the recommendation was made on different data")
        recommended = record.get("recommendation", {}).get("recommended_model")
        models = tuple(m for m in models if m.spec.model_id == recommended)
        if not models:
            raise ConfigError(f"recommended model {recommended!r} is not configured")
    result = project(
        data.series,
        models,
        max_horizon=config.max_horizon,
        training_window=TrainingWindow(config.training_window.days),
    )
    git = git_info(settings.repo_root)
    files = {
        "projections.csv": csv_bytes(result.records),
        "fits.csv": csv_bytes(result.fits),
        "coefficients.csv": csv_bytes(result.coefficients),
        "config.json": json_bytes(config.model_dump(mode="json")),
    }
    lines = [
        f"# Future projection: {config.experiment_id}",
        "",
        f"**{RECORD_TYPE_LABELS[RECORD_TYPE_PROJECTION]}**",
        "",
        f"- origin (last observed day): {result.origin}",
        f"- targets: {result.records['target_date'].min().date()}.."
        f"{result.records['target_date'].max().date()}",
        f"- models: {', '.join(s.model_id for s in result.models)}"
        + (
            f" (recommended by the backtest: `{recommended}`)"
            if recommended
            else " (all configured models, for comparison; no recommendation record given)"
        ),
        f"- data fingerprint: `{data.fingerprint}`",
        "",
        "Backtest accuracy describes past re-forecasts, not these numbers. No prediction "
        "interval is provided (milestone M3).",
        "",
    ]
    files["report.md"] = ("\n".join(lines) + "\n").encode("utf-8")
    outputs = {name: sha256_bytes(content) for name, content in files.items()}
    run_id = f"{config.experiment_id}-{config.fingerprint()[:8]}-{data.fingerprint[:8]}"
    directory = output_dir or settings.repo_root / RUNS_DIRNAME / "projections" / run_id
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "record_type": RECORD_TYPE_PROJECTION,
        "label": RECORD_TYPE_LABELS[RECORD_TYPE_PROJECTION],
        "run_id": run_id,
        "origin": result.origin,
        "recommended_model": recommended,
        "config_fingerprint": config.fingerprint(),
        "config": config.model_dump(mode="json"),
        "data": data.provenance,
        "models": [s.to_dict() | {"spec_fingerprint": s.fingerprint()} for s in result.models],
        "code": code_fingerprint(settings.repo_root),
        "git": git,
        "environment": environment_info(),
        "outputs": outputs,
        "informational_not_fingerprinted": {"created_at_utc": utc_now()},
    }
    write_run(directory, files, manifest)
    failed = result.records[result.records["status"] == "failed"]
    issues = [
        Issue(
            "projection_without_outcomes",
            Severity.INFO,
            "projection",
            RECORD_TYPE_LABELS[RECORD_TYPE_PROJECTION],
        )
    ]
    if len(failed):
        issues.append(
            Issue(
                "projection_failed",
                Severity.ERROR,
                "projection",
                f"{len(failed)} projected value(s) failed: "
                + ", ".join(sorted(set(failed["failure_code"].dropna()))),
                count=len(failed),
            )
        )
    totals = result.records.groupby("model_id", sort=False)["prediction"].sum(min_count=1)
    reports.append(
        CheckReport(
            name="projection",
            issues=tuple(issues),
            facts={
                "record_type": RECORD_TYPE_PROJECTION,
                "run_dir": str(directory),
                "origin": result.origin.isoformat(),
                "max_horizon": result.max_horizon,
                "recommended_model": recommended,
                "totals": {str(k): None if pd.isna(v) else float(v) for k, v in totals.items()},
            },
        )
    )
    return reports
