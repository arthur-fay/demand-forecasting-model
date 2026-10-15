"""Inputs of the M4 decision simulation from the M2 forecasts and the M3 uncertainty runs.

M4 never re-labels what M2 and M3 established. It reads their committed run directories
and checks each link of the provenance chain before any number is used:

* **M2 future projection** (``atlas forecast``; record type ``future_projection``): the
  SHA-256 of ``projections.csv`` must equal the run manifest, the model must be the one
  the complete M2 confirmation run recommended, and the first ``T`` horizons must be
  consecutive projected days. These are point forecasts with no observed outcome; M2's
  own label ("not validated by any backtest") travels with them.
* **M2 backtest accuracy** (the complete M2 run that made the recommendation): its
  ``metrics.json`` must match its manifest, and its result fingerprint must be the one
  the projection's recommendation came from. The confirmation-period error of the
  recommended model (MAE and bias over days 1-30) is copied as context, never used to
  adjust the demand.
* **M3 calibration status** (``calibration_status.json`` of a complete M3 evaluation
  run, checked with :func:`atlas.uncertainty_runner._status_source`): its decision status
  (``failed_calibration`` for the reference run) is attached to every M4 output.
* **M3 projection with intervals** (record type ``future_projection_intervals``): only
  needed for a per-day marginal quantile stress path. Its status source must be the same
  status file (SHA-256), its rows are selected by the primary model *and* the decided
  interval method, and their point prediction must equal the M2 projection exactly.

Data fingerprints of all of these must agree (one snapshot). A per-day marginal quantile
path is not a joint scenario and carries no probability: summing daily quantiles does not
give a quantile of the total, and M3 found these quantiles miscalibrated.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Final

import pandas as pd

from atlas.backtest import RECORD_TYPE_PROJECTION
from atlas.errors import ConfigError
from atlas.experiment import RECORD_TYPE_LABELS, result_fingerprint
from atlas.probabilistic import DECISION_GRADE, RECORD_TYPE_PROJECTION_INTERVALS
from atlas.runner import RECOMMENDATION_TYPE, _read_json, _recommendation_source
from atlas.runner import RESULT_FILES as M2_RESULT_FILES
from atlas.snapshot import sha256_file
from atlas.uncertainty_runner import CALIBRATION_STATUS_TYPE, _status_source

M2_CONFIRMATION_PERIOD: Final = "confirmation"
M2_HEADLINE_BUCKET: Final = "days_1_30"
#: Basis labels of a demand path.
POINT_FORECAST: Final = "point_forecast"
MARGINAL_QUANTILE_PATH: Final = "per_day_marginal_quantile_path"
MARGINAL_QUANTILE_NOTICE: Final = (
    "HYPOTHETICAL STRESS PATH: each day's marginal M3 quantile, taken day by day. It is not "
    "a joint scenario, not a quantile of total demand and carries no probability; the M3 "
    "intervals it comes from failed their calibration criteria."
)


def _inside(repo_root: Path, relative: str, what: str) -> Path:
    path = (repo_root / relative).resolve()
    if not path.is_relative_to(repo_root.resolve()):
        raise ConfigError(f"{what} {relative!r} must lie inside the repository")
    return path


def _checked_file(directory: Path, name: str, manifest: Mapping[str, Any], what: str) -> Path:
    path = directory / name
    if not path.is_file():
        raise ConfigError(f"{what}: {path} is missing")
    recorded = (manifest.get("outputs") or {}).get(name)
    if recorded != sha256_file(path):
        raise ConfigError(f"{what}: {name} is not the file its run recorded (SHA-256 differs)")
    return path


@dataclass(frozen=True)
class ForecastAccuracy:
    """Historical error of the recommended model on M2's confirmation period (context only).

    ``bias`` follows M2's sign (prediction - actual): negative means under-forecast.
    """

    model_id: str
    period: str
    bucket: str
    mae: float
    bias: float
    wape_pct: float
    n_predictions: int
    source: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "period": self.period,
            "bucket": self.bucket,
            "mae_requests_per_day": self.mae,
            "bias_requests_per_day": self.bias,
            "bias_sign": "prediction - actual (negative = under-forecast)",
            "wape_pct": self.wape_pct,
            "n_predictions": self.n_predictions,
            "source": dict(self.source),
        }


@dataclass(frozen=True)
class M2Projection:
    """The point forecasts of the recommended model from a committed M2 projection run."""

    origin: date
    model_id: str
    target_dates: tuple[date, ...]
    predictions: tuple[float, ...]
    label: str
    provenance: Mapping[str, Any]
    data_fingerprint: str
    accuracy: ForecastAccuracy

    def first(self, horizon: int) -> tuple[tuple[date, ...], tuple[float, ...]]:
        if not 1 <= horizon <= len(self.predictions):
            raise ConfigError(
                f"the M2 projection has {len(self.predictions)} days; {horizon} were requested"
            )
        return self.target_dates[:horizon], self.predictions[:horizon]


def _consecutive(origin: date, dates: list[date], what: str) -> None:
    expected = [origin + timedelta(days=offset) for offset in range(1, len(dates) + 1)]
    if dates != expected:
        raise ConfigError(f"{what}: target dates are not the consecutive days after {origin}")


def load_m2_projection(
    repo_root: Path, run_dir: str, model_id: str, backtest_run_dir: str
) -> M2Projection:
    """Point forecasts of ``model_id`` from the committed M2 projection run ``run_dir``.

    ``backtest_run_dir`` is the complete M2 run whose recommendation the projection used.
    """
    directory = _inside(repo_root, run_dir, "forecast run_dir")
    manifest = _read_json(directory / "manifest.json", "manifest of the M2 projection")
    what = f"M2 projection {run_dir}"
    if manifest.get("record_type") != RECORD_TYPE_PROJECTION:
        raise ConfigError(f"{what} is not a {RECORD_TYPE_PROJECTION} run")
    if manifest.get("recommended_model") is None or manifest.get("recommended_model") != model_id:
        raise ConfigError(
            f"{what}: model {model_id!r} is not the model the M2 confirmation run recommended "
            f"({manifest.get('recommended_model')!r})"
        )
    path = _checked_file(directory, "projections.csv", manifest, what)
    frame = pd.read_csv(
        path,
        dtype={"model_id": str, "status": str, "record_type": str},
        float_precision="round_trip",
    )
    frame = frame[frame["model_id"] == model_id]
    if frame.empty:
        raise ConfigError(f"{what} has no rows for {model_id!r}")
    if set(frame["record_type"]) != {RECORD_TYPE_PROJECTION} or set(frame["status"]) != {
        "projected"
    }:
        raise ConfigError(f"{what}: every row must be a projected {RECORD_TYPE_PROJECTION}")
    frame = frame.sort_values("horizon")
    if list(frame["horizon"]) != list(range(1, len(frame) + 1)):
        raise ConfigError(f"{what}: horizons are not 1..{len(frame)}")
    origin = date.fromisoformat(str(manifest.get("origin")))
    if set(frame["origin"]) != {origin.isoformat()}:
        raise ConfigError(f"{what}: rows come from a different origin than the manifest")
    dates = [date.fromisoformat(str(v)) for v in frame["target_date"]]
    _consecutive(origin, dates, what)
    predictions = [float(v) for v in frame["prediction"]]
    if not all(math.isfinite(v) and v >= 0 for v in predictions):
        raise ConfigError(f"{what}: predictions must be finite and non-negative")
    data_fingerprint = str((manifest.get("data") or {}).get("data_fingerprint"))
    accuracy = _m2_accuracy(repo_root, backtest_run_dir, manifest, model_id, data_fingerprint)
    provenance = {
        "kind": "m2_projection_run",
        "run_dir": run_dir,
        "record_type": RECORD_TYPE_PROJECTION,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": sha256_file(directory / "manifest.json"),
        "projections_sha256": sha256_file(path),
        "config_fingerprint": manifest.get("config_fingerprint"),
        "origin": origin.isoformat(),
        "model_id": model_id,
        "recommendation_source": manifest.get("recommendation_source"),
        "label": RECORD_TYPE_LABELS[RECORD_TYPE_PROJECTION],
    }
    return M2Projection(
        origin=origin,
        model_id=model_id,
        target_dates=tuple(dates),
        predictions=tuple(predictions),
        label=RECORD_TYPE_LABELS[RECORD_TYPE_PROJECTION],
        provenance=provenance,
        data_fingerprint=data_fingerprint,
        accuracy=accuracy,
    )


def _m2_accuracy(
    repo_root: Path,
    backtest_run_dir: str,
    projection: Mapping[str, Any],
    model_id: str,
    data_fingerprint: str,
) -> ForecastAccuracy:
    """Confirmation-period error of the recommended model, from the recommending run."""
    source = projection.get("recommendation_source") or {}
    run_id = source.get("run_id")
    if not isinstance(run_id, str):
        raise ConfigError("the M2 projection names no recommending run (recommendation_source)")
    directory = _inside(repo_root, backtest_run_dir, "forecast backtest_run_dir")
    manifest = _read_json(directory / "manifest.json", "manifest of the M2 backtest")
    problems = []
    if manifest.get("run_id") != run_id:
        problems.append(f"it is not the run {run_id!r} that made the projection's recommendation")
    if manifest.get("complete") is not True:
        problems.append("the run is not complete")
    if manifest.get("result_fingerprint") != source.get("result_fingerprint"):
        problems.append("its result fingerprint is not the one the projection recorded")
    elif result_fingerprint(manifest.get("outputs") or {}, M2_RESULT_FILES) != manifest.get(
        "result_fingerprint"
    ):
        problems.append("its recorded outputs do not give its result fingerprint")
    if (manifest.get("data") or {}).get("data_fingerprint") != data_fingerprint:
        problems.append("it was run on different data")
    if manifest.get("config_fingerprint") != projection.get("config_fingerprint"):
        problems.append("it used a different M2 configuration than the projection")
    recommendation = directory / "recommendation.json"
    record: dict[str, Any] = {}
    if not recommendation.is_file() or sha256_file(recommendation) != source.get(
        "recommendation_sha256"
    ):
        problems.append("its recommendation.json is missing or not the one the projection used")
    else:
        record = _read_json(recommendation, "recommendation")
        if record.get("record_type") != RECOMMENDATION_TYPE:
            problems.append("its recommendation.json is not a recommendation record")
        elif (record.get("recommendation") or {}).get("recommended_model") != model_id:
            problems.append("its recommendation names a different model")
    if problems:
        raise ConfigError(f"M2 run {directory}: " + "; ".join(problems))
    # The recommendation must come, unchanged, from the complete run (M2's own check).
    _recommendation_source(recommendation, record)
    metrics_path = _checked_file(directory, "metrics.json", manifest, f"M2 run {directory}")
    metrics = _read_json(metrics_path, "M2 metrics")
    rows = [
        row
        for row in metrics.get("summary", [])
        if row.get("model_id") == model_id
        and row.get("period") == M2_CONFIRMATION_PERIOD
        and row.get("scope") == "bucket"
        and row.get("label") == M2_HEADLINE_BUCKET
    ]
    if len(rows) != 1:
        raise ConfigError(f"M2 metrics have no unique {M2_HEADLINE_BUCKET} confirmation row")
    row = rows[0]
    return ForecastAccuracy(
        model_id=model_id,
        period=M2_CONFIRMATION_PERIOD,
        bucket=M2_HEADLINE_BUCKET,
        mae=float(row["mae"]),
        bias=float(row["bias"]),
        wape_pct=float(row["wape_pct"]),
        n_predictions=int(row["n_predictions"]),
        source={
            "run_dir": backtest_run_dir,
            "run_id": run_id,
            "result_fingerprint": manifest.get("result_fingerprint"),
            "metrics_sha256": sha256_file(metrics_path),
        },
    )


@dataclass(frozen=True)
class M3Status:
    """The pre-registered M3 decision status, from a complete M3 evaluation run."""

    interval_status: str
    decision_grade: bool
    statement: str
    selected_pair: str
    primary_model: str
    exceedance: Mapping[str, str]
    data_fingerprint: str
    provenance: Mapping[str, Any]
    quantile_calibration: Mapping[str, float] = field(default_factory=dict)
    config_fingerprint: str | None = None
    backtest_config_fingerprint: str | None = None
    result_fingerprint: str | None = None
    recommendation_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "interval_status": self.interval_status,
            "decision_grade": self.decision_grade,
            "statement": self.statement,
            "selected_pair": self.selected_pair,
            "primary_model": self.primary_model,
            "exceedance_status": dict(self.exceedance),
            "evaluation_share_at_or_below_quantile_days_1_30": dict(self.quantile_calibration),
            "data_fingerprint": self.data_fingerprint,
            "source": dict(self.provenance),
        }


def load_m3_status(repo_root: Path, status_path: str) -> M3Status:
    """The decision status of a complete M3 run (unchanged since the run, see module doc)."""
    path = _inside(repo_root, status_path, "uncertainty calibration_status")
    record = _read_json(path, "M3 calibration status")
    if record.get("record_type") != CALIBRATION_STATUS_TYPE:
        raise ConfigError(f"{status_path} is not an M3 calibration status record")
    manifest = _status_source(path, record)
    decision = record.get("decision_status") or {}
    by_model = (decision.get("by_model") or {}).get(decision.get("primary_model")) or {}
    exceedance = {
        str(item.get("threshold_label")): str(item.get("status"))
        for item in by_model.get("exceedance", [])
    }
    interval_status = str(decision.get("interval_status", "unavailable"))
    metrics = _read_json(path.parent / "metrics.json", "M3 metrics")
    calibration: dict[str, float] = {}
    for row in metrics.get("interval_summary", []):
        if (
            row.get("period") == decision.get("period")
            and row.get("model_id") == decision.get("primary_model")
            and row.get("pair_id") == record.get("decided_pair")
            and row.get("scope") == "bucket"
            and row.get("label") == M2_HEADLINE_BUCKET
        ):
            calibration = {str(k): float(v) for k, v in row["quantile_calibration"].items()}
    return M3Status(
        interval_status=interval_status,
        decision_grade=bool(decision.get("decision_grade")) and interval_status == DECISION_GRADE,
        statement=str(decision.get("statement", "")),
        selected_pair=str(record.get("decided_pair")),
        primary_model=str(decision.get("primary_model")),
        exceedance=exceedance,
        data_fingerprint=str(record.get("data_fingerprint")),
        provenance={
            "path": status_path,
            "sha256": sha256_file(path),
            "run_id": record.get("run_id"),
            "result_fingerprint": record.get("result_fingerprint"),
            "evaluation_period": decision.get("period"),
            "manifest_record_type": manifest.get("record_type"),
        },
        quantile_calibration=calibration,
        config_fingerprint=record.get("config_fingerprint"),
        backtest_config_fingerprint=record.get("backtest_config_fingerprint"),
        result_fingerprint=record.get("result_fingerprint"),
        recommendation_sha256=(record.get("recommendation") or {}).get("recommendation_sha256"),
    )


@dataclass(frozen=True)
class QuantilePath:
    """One per-day marginal quantile of the M3 projection (a HYPOTHETICAL stress path)."""

    quantile: float
    target_dates: tuple[date, ...]
    values: tuple[float, ...]
    provenance: Mapping[str, Any]


def _m3_projection_manifest(
    repo_root: Path, run_dir: str, status: M3Status, origin: date | None
) -> tuple[Path, dict[str, Any]]:
    """The M3 projection run directory and manifest, checked against the status."""
    directory = _inside(repo_root, run_dir, "uncertainty projection_run_dir")
    manifest = _read_json(directory / "manifest.json", "manifest of the M3 projection")
    problems = []
    if manifest.get("record_type") != RECORD_TYPE_PROJECTION_INTERVALS:
        problems.append(f"it is not a {RECORD_TYPE_PROJECTION_INTERVALS} run")
    source = manifest.get("status_source") or {}
    if source.get("calibration_status_sha256") != status.provenance.get("sha256"):
        problems.append("it was labelled with a different calibration status file")
    if source.get("result_fingerprint") != status.result_fingerprint:
        problems.append("its status comes from a different M3 run")
    if manifest.get("calibration_status") != status.interval_status:
        problems.append("its calibration status differs from the status file")
    if manifest.get("decided_pair") != status.selected_pair:
        problems.append("its interval method is not the sealed one")
    if manifest.get("config_fingerprint") != status.config_fingerprint:
        problems.append("it used a different M3 configuration")
    if manifest.get("backtest_config_fingerprint") != status.backtest_config_fingerprint:
        problems.append("it used a different M2 configuration")
    if (manifest.get("data") or {}).get("data_fingerprint") != status.data_fingerprint:
        problems.append("it was made on different data than the status")
    if origin is not None and str(manifest.get("origin")) != origin.isoformat():
        problems.append("its origin differs from the M2 projection")
    if problems:
        raise ConfigError(f"M3 projection {run_dir} cannot be used: " + "; ".join(problems))
    return directory, manifest


def load_m3_quantile_path(
    repo_root: Path,
    run_dir: str,
    status: M3Status,
    projection: M2Projection,
    quantile: float,
) -> QuantilePath:
    """The marginal ``quantile`` of each projected day for the primary model and method."""
    directory, manifest = _m3_projection_manifest(repo_root, run_dir, status, projection.origin)
    what = f"M3 projection {run_dir}"
    path = _checked_file(directory, "projection_intervals.csv", manifest, what)
    column = f"q_{quantile:g}"
    frame = pd.read_csv(
        path,
        dtype={"model_id": str, "pair_id": str, "status": str, "support": str},
        float_precision="round_trip",
    )
    if column not in frame.columns:
        raise ConfigError(f"{what} has no quantile {quantile:g} (column {column})")
    rows = frame[
        (frame["model_id"] == status.primary_model) & (frame["pair_id"] == status.selected_pair)
    ].sort_values("horizon")
    if status.primary_model != projection.model_id:
        raise ConfigError("the M3 primary model is not the M2 projection's model")
    if list(rows["horizon"]) != list(range(1, len(projection.predictions) + 1)):
        raise ConfigError(f"{what}: the primary model's horizons do not match the projection")
    if set(rows["status"]) != {"projected"} or set(rows["support"]) != {"supported"}:
        raise ConfigError(f"{what}: some intervals of the primary model were not issued")
    dates = [date.fromisoformat(str(v)) for v in rows["target_date"]]
    if tuple(dates) != projection.target_dates:
        raise ConfigError(f"{what}: target dates differ from the M2 projection")
    if tuple(float(v) for v in rows["prediction"]) != projection.predictions:
        raise ConfigError(f"{what}: point predictions differ from the M2 projection")
    values = tuple(float(v) for v in rows[column])
    if not all(math.isfinite(v) and v >= 0 for v in values):
        raise ConfigError(f"{what}: quantile {quantile:g} must be finite and non-negative")
    return QuantilePath(
        quantile=quantile,
        target_dates=tuple(dates),
        values=values,
        provenance={
            "kind": "m3_projection_run",
            "run_dir": run_dir,
            "record_type": RECORD_TYPE_PROJECTION_INTERVALS,
            "run_id": manifest.get("run_id"),
            "manifest_sha256": sha256_file(directory / "manifest.json"),
            "projection_intervals_sha256": sha256_file(path),
            "model_id": status.primary_model,
            "pair_id": status.selected_pair,
            "quantile": quantile,
            "calibration_status": status.interval_status,
            "notice": MARGINAL_QUANTILE_NOTICE,
        },
    )


def check_same_data(projection: M2Projection, status: M3Status) -> None:
    """The M2 projection and the M3 status must describe the same data, the same M2
    configuration, the same M2 recommendation and the same model."""
    if projection.data_fingerprint != status.data_fingerprint:
        raise ConfigError("the M2 projection and the M3 status were made on different data")
    if projection.provenance.get("config_fingerprint") != status.backtest_config_fingerprint:
        raise ConfigError("the M3 status was made with a different M2 configuration")
    source = projection.provenance.get("recommendation_source") or {}
    if source.get("recommendation_sha256") != status.recommendation_sha256:
        raise ConfigError("the M3 status rests on a different M2 recommendation")
    if status.primary_model != projection.model_id:
        raise ConfigError(
            f"the M3 primary model {status.primary_model!r} is not the projected model "
            f"{projection.model_id!r}"
        )


#: The M3 threshold that reproduces the legacy "planning capacity" (a demand percentile).
LEGACY_PERCENTILE_LABEL: Final = "trailing180_p75"


def load_demand_percentile(repo_root: Path, run_dir: str, status: M3Status) -> float:
    """The trailing 180-day 75th percentile of observed demand at the projection origin.

    Read from the M3 projection's exceedance file (provenance ``DERIVED_FROM_HISTORY``):
    the number the legacy project called planning capacity. M4 uses it only to refuse a
    HYPOTHETICAL capacity that equals it.
    """
    directory, manifest = _m3_projection_manifest(repo_root, run_dir, status, None)
    what = f"M3 projection {run_dir}"
    path = _checked_file(directory, "projection_exceedance.csv", manifest, what)
    frame = pd.read_csv(
        path,
        dtype={"threshold_label": str, "threshold_provenance": str},
        float_precision="round_trip",
    )
    rows = frame[
        (frame["threshold_label"] == LEGACY_PERCENTILE_LABEL)
        & (frame["model_id"] == status.primary_model)
        & (frame["pair_id"] == status.selected_pair)
    ]
    values = sorted({float(v) for v in rows["threshold_value"]})
    if len(values) != 1 or set(rows["threshold_provenance"]) != {"DERIVED_FROM_HISTORY"}:
        raise ConfigError(f"{what}: no unique {LEGACY_PERCENTILE_LABEL} threshold at the origin")
    return values[0]
