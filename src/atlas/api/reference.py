"""The committed reference outputs the API serves, loaded and checked once at startup.

Nothing here computes a forecast, an interval or an optimisation: every number comes from
a committed, fingerprinted run, read through the same provenance checks the M4 runner uses
(:func:`atlas.optimization_runner.load_inputs`, :func:`~atlas.optimization_runner.
inspect_optimization_command`). Files the API parses itself are read once into memory,
hashed, compared with the hash their run recorded and parsed from those same bytes, so a
file replaced after the check is never served.

Each component loads independently and records whether it is ready. A component that is
missing or fails a check is not ready: its endpoints answer 503 ``artifact_unavailable``
and ``/health/ready`` names it, while every other component keeps working. The store is
immutable once built and never writes to the repository.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Final

import pandas as pd
from pydantic import ValidationError

from atlas import __version__
from atlas.api.problems import ApiError, unavailable
from atlas.api.schemas import (
    API_VERSION,
    STRESS_QUANTILE_LEVELS,
    ComponentStatus,
    DayPlan,
    HypotheticalParameter,
    IntervalLevelEvidence,
    IntervalPoint,
    ObservedPoint,
    PlanTotals,
    PlanVariant,
    Provenance,
    ScenarioOutcome,
    Source,
)
from atlas.api.settings import REFERENCE_CONFIG, REFERENCE_RUN_DIR, ApiSettings
from atlas.config import Settings
from atlas.decision_inputs import QuantilePath, load_m3_quantile_path
from atlas.errors import AtlasError
from atlas.evaluation import holiday_related_dates
from atlas.events import holidays_between
from atlas.experiment import parse_config as parse_experiment_config
from atlas.experiment import result_fingerprint
from atlas.optimization_config import OptimizationConfig, parse_optimization_config
from atlas.optimization_runner import (
    RECORD_TYPE_DECISION_SIMULATION,
    RESULT_FILES,
    DecisionInputs,
    inspect_optimization_command,
    load_inputs,
)
from atlas.runner import RESULT_FILES as M2_RESULT_FILES
from atlas.runner import SnapshotData, load_series

LOGGER: Final = logging.getLogger("atlas.api")

SNAPSHOT: Final = "snapshot"
FORECAST: Final = "forecast_and_uncertainty_status"
M2_BACKTEST: Final = "m2_backtest"
M3_INTERVALS: Final = "m3_projection_intervals"
M4_REFERENCE: Final = "m4_reference_run"
SIMULATION: Final = "simulation"
COMPONENTS: Final = (SNAPSHOT, FORECAST, M2_BACKTEST, M3_INTERVALS, M4_REFERENCE, SIMULATION)

_DETAIL_LIMIT: Final = 300


class ArtifactError(AtlasError):
    """A committed artifact is missing, altered or inconsistent with its provenance chain."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_verified(path: Path, expected: object, what: str) -> bytes:
    """The bytes of ``path``, which must hash to ``expected`` (the recorded SHA-256)."""
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ArtifactError(f"{what} cannot be read ({type(exc).__name__})") from exc
    if not isinstance(expected, str) or _sha256(payload) != expected:
        raise ArtifactError(f"{what} is not the file its run recorded (SHA-256 differs)")
    return payload


def _json(payload: bytes, what: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ArtifactError(f"{what} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ArtifactError(f"{what} is not a JSON object")
    return value


def _read_json_file(path: Path, what: str) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ArtifactError(f"{what} cannot be read ({type(exc).__name__})") from exc
    return _json(payload, what)


def _csv_rows(payload: bytes, what: str) -> list[dict[str, str]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArtifactError(f"{what} is not UTF-8 text") from exc
    return list(csv.DictReader(io.StringIO(text)))


def _number(value: str | None) -> float | None:
    """A CSV cell as a float (empty: ``None``); exact for the ``%.17g`` cells ATLAS writes."""
    if value is None or value == "":
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite number {value!r}")
    return number


def _flag(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    if value in ("True", "False"):
        return value == "True"
    raise ValueError(f"not a boolean: {value!r}")


def _text(value: str | None) -> str | None:
    return None if value is None or value == "" else value


def holiday_names(start: date, end: date) -> dict[date, str]:
    """Observed federal holidays between ``start`` and ``end`` (M1 rules), by date."""
    return {h.observed: h.name for h in holidays_between(start, end) if start <= h.observed <= end}


def holiday_related(start: date, end: date) -> frozenset[date]:
    """Days of M3's holiday-related stratum (:func:`atlas.evaluation.holiday_related_dates`)."""
    return frozenset(stamp.date() for stamp in holiday_related_dates(start, end))


# --- Loaded components ------------------------------------------------------------------


@dataclass(frozen=True)
class M2Backtest:
    """The complete M2 run that recommended the projected model (metrics only)."""

    run_dir: str
    run_id: str
    result_fingerprint: str
    config_fingerprint: str
    metrics_sha256: str
    alpha: float
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class M3Intervals:
    """The M3 projection quantiles of the primary model and the sealed interval method."""

    run_dir: str
    run_id: str | None
    config_fingerprint: str | None
    sha256: str
    pair_id: str
    points: tuple[IntervalPoint, ...]


@dataclass(frozen=True)
class M3Record:
    """The calibration status file as recorded (beyond the fields M4 extracts)."""

    note: str | None
    failure_reasons: tuple[str, ...]
    levels: tuple[IntervalLevelEvidence, ...]
    exceedance_reasons: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class M4Reference:
    """The committed M4 reference run, inspected without recomputation."""

    run_dir: str
    run_id: str
    result_fingerprint: str
    config_fingerprint: str
    label: str
    horizon: Mapping[str, Any]
    outcomes: Mapping[str, ScenarioOutcome]
    variants: Mapping[str, tuple[PlanVariant, ...]]
    daily: Mapping[str, tuple[DayPlan, ...]]
    hypothetical_parameters: tuple[HypotheticalParameter, ...]
    structural_assumptions: tuple[str, ...]
    sha256: Mapping[str, str]


@dataclass(repr=False)
class ReferenceStore:
    """Every reference component, its readiness, and its provenance (see module doc)."""

    settings: ApiSettings
    status: dict[str, ComponentStatus] = field(default_factory=dict)
    snapshot: SnapshotData | None = None
    config: OptimizationConfig | None = None
    config_sha256: str | None = None
    inputs: DecisionInputs | None = None
    quantile_paths: dict[float, QuantilePath] = field(default_factory=dict)
    m3_record: M3Record | None = None
    m2: M2Backtest | None = None
    m3_intervals: M3Intervals | None = None
    m4: M4Reference | None = None
    history: tuple[ObservedPoint, ...] = ()

    def __repr__(self) -> str:
        states = ", ".join(
            f"{c.component}={'ready' if c.ready else 'not ready'}" for c in self.components()
        )
        return f"ReferenceStore({self.settings.repo_root}: {states})"

    @property
    def ready(self) -> bool:
        return all(item.ready for item in self.status.values())

    def components(self) -> list[ComponentStatus]:
        return [self.status[name] for name in COMPONENTS if name in self.status]

    def require(self, component: str) -> None:
        state = self.status.get(component)
        if state is None or not state.ready:
            raise unavailable(component)

    # Typed accessors: each raises 503 when its component is not ready.

    def require_snapshot(self) -> SnapshotData:
        self.require(SNAPSHOT)
        return _present(self.snapshot)

    def require_inputs(self) -> DecisionInputs:
        self.require(FORECAST)
        return _present(self.inputs)

    def require_m2(self) -> M2Backtest:
        self.require(M2_BACKTEST)
        return _present(self.m2)

    def require_m3_intervals(self) -> M3Intervals:
        self.require(M3_INTERVALS)
        return _present(self.m3_intervals)

    def require_m4(self) -> M4Reference:
        self.require(M4_REFERENCE)
        return _present(self.m4)

    def require_simulation(self) -> tuple[OptimizationConfig, DecisionInputs]:
        self.require(SIMULATION)
        return _present(self.config), _present(self.inputs)

    # Provenance -------------------------------------------------------------------------

    def data_fingerprint(self) -> str | None:
        return None if self.snapshot is None else self.snapshot.fingerprint

    def provenance(self, sources: Sequence[Source]) -> Provenance:
        return Provenance(
            atlas_version=__version__,
            api_version=API_VERSION,
            snapshot_id=self.settings.snapshot_id,
            data_fingerprint=self.data_fingerprint(),
            sources=list(sources),
        )

    def snapshot_source(self) -> Source:
        provenance = {} if self.snapshot is None else self.snapshot.provenance
        return Source(
            kind="legacy_snapshot",
            path=str(provenance.get("table_path", "01 Data/daily_total.csv")),
            record_type="observed_daily_total",
            sha256=provenance.get("table_sha256"),
        )

    def forecast_source(self) -> Source:
        inputs = _present(self.inputs)
        p = inputs.projection.provenance
        return Source(
            kind="m2_projection_run",
            path=str(p.get("run_dir")),
            record_type=str(p.get("record_type")),
            run_id=_opt_str(p.get("run_id")),
            config_fingerprint=_opt_str(p.get("config_fingerprint")),
            sha256=_opt_str(p.get("projections_sha256")),
        )

    def accuracy_source(self) -> Source:
        inputs = _present(self.inputs)
        s = inputs.projection.accuracy.source
        return Source(
            kind="m2_backtest_run",
            path=str(s.get("run_dir")),
            record_type="historical_backtest",
            run_id=_opt_str(s.get("run_id")),
            result_fingerprint=_opt_str(s.get("result_fingerprint")),
            # load_inputs checked that the backtest run used the projection's configuration.
            config_fingerprint=_opt_str(inputs.projection.provenance.get("config_fingerprint")),
            sha256=_opt_str(s.get("metrics_sha256")),
        )

    def status_source(self) -> Source:
        inputs = _present(self.inputs)
        s = inputs.status.provenance
        return Source(
            kind="m3_calibration_status",
            path=str(s.get("path")),
            record_type="calibration_status",
            run_id=_opt_str(s.get("run_id")),
            result_fingerprint=_opt_str(s.get("result_fingerprint")),
            config_fingerprint=inputs.status.config_fingerprint,
            sha256=_opt_str(s.get("sha256")),
        )

    def intervals_source(self) -> Source:
        """The M3 projection intervals: the file pinned by the reference configuration
        (run id and fingerprint once the intervals component has loaded it)."""
        config = _present(self.config)
        intervals = self.m3_intervals
        return Source(
            kind="m3_projection_run",
            path=config.uncertainty.projection_run_dir,
            record_type="future_projection_intervals",
            run_id=None if intervals is None else intervals.run_id,
            config_fingerprint=None if intervals is None else intervals.config_fingerprint,
            sha256=config.uncertainty.projection_intervals_sha256,
        )

    def m4_source(self) -> Source:
        m4 = _present(self.m4)
        return Source(
            kind="m4_reference_run",
            path=m4.run_dir,
            record_type=RECORD_TYPE_DECISION_SIMULATION,
            run_id=m4.run_id,
            result_fingerprint=m4.result_fingerprint,
            config_fingerprint=m4.config_fingerprint,
            sha256=m4.sha256.get("metrics.json"),
        )

    def config_source(self) -> Source:
        config = _present(self.config)
        return Source(
            kind="m4_reference_configuration",
            path=REFERENCE_CONFIG,
            record_type="optimization_config",
            config_fingerprint=config.fingerprint(),
            sha256=self.config_sha256,
        )


def _present[T](value: T | None) -> T:
    if value is None:  # pragma: no cover - guarded by the readiness check before every use
        raise ApiError(503, "artifact_unavailable", "reference data is unavailable")
    return value


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


# --- Loading ----------------------------------------------------------------------------


def _safe_detail(exc: BaseException, root: Path) -> str:
    """A loading failure as a client-safe sentence: no absolute paths, bounded length."""
    if isinstance(exc, AtlasError):
        text = str(exc).replace(str(root.resolve()), "<repo>").replace(str(root), "<repo>")
    elif isinstance(exc, ValidationError):
        text = f"the file does not match the expected structure ({exc.error_count()} problems)"
    else:
        text = f"unexpected {type(exc).__name__} while loading (see the server log)"
    return text if len(text) <= _DETAIL_LIMIT else text[: _DETAIL_LIMIT - 3] + "..."


def load_reference_store(settings: ApiSettings) -> ReferenceStore:
    """Load and check every component (never raises; failures make components not ready)."""
    store = ReferenceStore(settings)
    root = settings.repo_root
    base = Settings(repo_root=root, snapshot_id=settings.snapshot_id)

    def attempt(name: str, depends: Sequence[str], load: Callable[[], None]) -> None:
        missing = [d for d in depends if not store.status.get(d, _NOT_READY).ready]
        if missing:
            store.status[name] = ComponentStatus(
                component=name, ready=False, detail="depends on: " + ", ".join(missing)
            )
            return
        started = time.perf_counter()
        try:
            load()
        except Exception as exc:  # any failure leaves this component unavailable, not the API
            detail = _safe_detail(exc, root)
            LOGGER.warning(
                "reference component not ready: %s",
                detail,
                exc_info=not isinstance(exc, AtlasError | ValidationError),
                extra={
                    "event": "component_not_ready",
                    "component": name,
                    "ready": False,
                    "duration_ms": _elapsed_ms(started),
                },
            )
            store.status[name] = ComponentStatus(component=name, ready=False, detail=detail)
            return
        store.status[name] = ComponentStatus(component=name, ready=True, detail=None)
        LOGGER.info(
            "reference component ready",
            extra={
                "event": "component_ready",
                "component": name,
                "ready": True,
                "duration_ms": _elapsed_ms(started),
            },
        )

    def snapshot() -> None:
        reports, data = load_series(base, "api_snapshot")
        if data is None:
            failed = [r.name for r in reports if not r.ok]
            raise ArtifactError(
                "the legacy-v0 snapshot failed integrity or validation (stages: "
                + (", ".join(failed) or "unknown")
                + ")"
            )
        store.snapshot = data
        store.history = _history_points(data)

    def forecast() -> None:
        try:
            payload = (root / REFERENCE_CONFIG).read_bytes()
            text = payload.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ArtifactError(f"{REFERENCE_CONFIG} cannot be read") from exc
        config = parse_optimization_config(text)
        inputs = load_inputs(base, config, _present(store.snapshot))
        paths = dict(inputs.quantiles)
        for level in STRESS_QUANTILE_LEVELS:
            if level not in paths:
                paths[level] = load_m3_quantile_path(
                    root,
                    config.uncertainty.projection_run_dir,
                    inputs.status,
                    inputs.projection,
                    level,
                )
        store.config, store.config_sha256 = config, _sha256(payload)
        store.inputs, store.quantile_paths = inputs, paths
        store.m3_record = _m3_record(root, inputs)

    def m2_backtest() -> None:
        store.m2 = _load_m2(root, _present(store.inputs), _present(store.config))

    def m3_intervals() -> None:
        store.m3_intervals = _load_m3_intervals(
            root, _present(store.inputs), _present(store.config)
        )

    def m4_reference() -> None:
        store.m4 = _load_m4(root, _present(store.config), _present(store.inputs))

    def simulation() -> None:
        # Simulations start from the reference configuration's defaults. Requiring the M4
        # reference run (which checked that its config fingerprint is this file's) means
        # the defaults served are the ones that produced the committed run.
        _present(store.config)

    attempt(SNAPSHOT, (), snapshot)
    attempt(FORECAST, (SNAPSHOT,), forecast)
    attempt(M2_BACKTEST, (FORECAST,), m2_backtest)
    attempt(M3_INTERVALS, (FORECAST,), m3_intervals)
    attempt(M4_REFERENCE, (FORECAST,), m4_reference)
    attempt(SIMULATION, (FORECAST, M4_REFERENCE), simulation)
    return store


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


_NOT_READY: Final = ComponentStatus(component="", ready=False, detail=None)


def _history_points(data: SnapshotData) -> tuple[ObservedPoint, ...]:
    """Every day of the validated series, in order (consecutive calendar days)."""
    series = data.series
    holidays = holiday_names(series.start, series.end)
    related = holiday_related(series.start, series.end)
    points = []
    values = series.to_series()
    for stamp, value in zip(values.index, values.to_numpy(), strict=True):
        day = pd.Timestamp(stamp).date()
        missing = math.isnan(float(value))
        points.append(
            ObservedPoint(
                date=day,
                requests=None if missing else round(float(value)),
                status="missing" if missing else "observed",
                federal_holiday=holidays.get(day),
                holiday_related=day in related,
            )
        )
    return tuple(points)


def _m3_record(root: Path, inputs: DecisionInputs) -> M3Record:
    status = inputs.status
    path = root / str(status.provenance.get("path"))
    record = _json(
        _read_verified(path, status.provenance.get("sha256"), "M3 calibration status"),
        "M3 calibration status",
    )
    decision = record.get("decision_status") or {}
    model = (decision.get("by_model") or {}).get(status.primary_model) or {}
    intervals = model.get("intervals") or {}
    levels = tuple(
        IntervalLevelEvidence.model_validate(
            {
                "level": item.get("level"),
                "coverage": item.get("coverage"),
                "coverage_ci_low": item.get("coverage_ci_low"),
                "coverage_ci_high": item.get("coverage_ci_high"),
                "effective_sample_size": item.get("effective_sample_size"),
                "evidence_sufficient": item.get("evidence_sufficient"),
                "p_value_conservative": item.get("p_value_conservative"),
                "rejected": item.get("rejected"),
                "scopes_outside_tolerance": list(item.get("scopes_outside_tolerance") or []),
                "test_computed": item.get("test_computed"),
                "tolerance_pct": item.get("tolerance_pct"),
            }
        )
        for item in intervals.get("levels") or []
    )
    exceedance = {
        str(item.get("threshold_label")): tuple(str(r) for r in item.get("reasons") or [])
        for item in model.get("exceedance") or []
    }
    note = decision.get("note")
    return M3Record(
        note=note if isinstance(note, str) else None,
        failure_reasons=tuple(str(r) for r in intervals.get("reasons") or []),
        levels=levels,
        exceedance_reasons=exceedance,
    )


def _load_m2(root: Path, inputs: DecisionInputs, config: OptimizationConfig) -> M2Backtest:
    """The recommending M2 run's metrics: the very file the projection's accuracy came from."""
    source = inputs.projection.accuracy.source
    run_dir = str(source.get("run_dir"))
    if run_dir != config.forecast.backtest_run_dir:
        raise ArtifactError("the M2 accuracy source is not the configured backtest run")
    directory = root / run_dir
    manifest = _read_json_file(directory / "manifest.json", "M2 run manifest")
    outputs = manifest.get("outputs") or {}
    expected = source.get("metrics_sha256")
    if outputs.get("metrics.json") != expected:
        raise ArtifactError("the M2 manifest does not record the checked metrics.json")
    if result_fingerprint(outputs, M2_RESULT_FILES) != manifest.get("result_fingerprint"):
        raise ArtifactError("the M2 outputs do not give the recorded result fingerprint")
    payload = _json(
        _read_verified(directory / "metrics.json", expected, "M2 metrics"), "M2 metrics"
    )
    if payload.get("record_type") != "historical_backtest":
        raise ArtifactError("the M2 metrics are not a historical backtest")
    if payload.get("config_fingerprint") != config.forecast.m2_config_fingerprint:
        raise ArtifactError("the M2 metrics come from a different M2 configuration")
    if payload.get("data_fingerprint") != inputs.data.fingerprint:
        raise ArtifactError("the M2 metrics were computed on different data")
    for key in ("summary", "models", "periods"):
        if not isinstance(payload.get(key), list):
            raise ArtifactError(f"the M2 metrics have no {key!r} list")
    # The pre-registered significance level, from the run's configuration (which must give
    # the configuration fingerprint the metrics carry).
    m2_config = parse_experiment_config(manifest.get("config") or {})
    if m2_config.fingerprint() != config.forecast.m2_config_fingerprint:
        raise ArtifactError("the M2 manifest's configuration does not give its fingerprint")
    if m2_config.selection is None:
        raise ArtifactError("the M2 configuration has no selection rule")
    alpha = m2_config.selection.alpha
    return M2Backtest(
        run_dir=run_dir,
        run_id=str(manifest.get("run_id")),
        result_fingerprint=str(manifest.get("result_fingerprint")),
        config_fingerprint=str(payload.get("config_fingerprint")),
        metrics_sha256=str(expected),
        alpha=alpha,
        payload=payload,
    )


def _load_m3_intervals(
    root: Path, inputs: DecisionInputs, config: OptimizationConfig
) -> M3Intervals:
    run_dir = config.uncertainty.projection_run_dir
    directory = root / run_dir
    manifest = _read_json_file(directory / "manifest.json", "M3 projection manifest")
    pinned = config.uncertainty.projection_intervals_sha256
    if (manifest.get("outputs") or {}).get("projection_intervals.csv") != pinned:
        raise ArtifactError("the M3 projection manifest does not record the pinned intervals")
    rows = _csv_rows(
        _read_verified(directory / "projection_intervals.csv", pinned, "M3 projection intervals"),
        "M3 projection intervals",
    )
    status, projection = inputs.status, inputs.projection
    chosen = sorted(
        (
            r
            for r in rows
            if r.get("model_id") == status.primary_model
            and r.get("pair_id") == status.selected_pair
        ),
        key=lambda r: int(r["horizon"]),
    )
    if [int(r["horizon"]) for r in chosen] != list(range(1, len(projection.predictions) + 1)):
        raise ArtifactError("the M3 intervals do not cover the projected horizons")
    columns = [c for c in (chosen[0].keys() if chosen else ()) if c.startswith("q_")]
    dates = projection.target_dates
    related = holiday_related(dates[0], dates[-1]) if dates else frozenset()
    points = []
    for row, day, prediction in zip(chosen, dates, projection.predictions, strict=True):
        if date.fromisoformat(row["target_date"]) != day or float(row["prediction"]) != prediction:
            raise ArtifactError("the M3 intervals differ from the M2 projection they describe")
        if row.get("status") != "projected" or row.get("support") != "supported":
            raise ArtifactError("some M3 intervals of the primary model were not issued")
        quantiles = {c[2:]: _number(row[c]) for c in columns}
        values = [v for v in quantiles.values() if v is not None]
        # The checks M4's load_m3_quantile_path applies to the levels it uses, on every level.
        if len(values) != len(quantiles) or any(v < 0 for v in values):
            raise ArtifactError("the M3 intervals have a missing or negative quantile")
        if values != sorted(values):
            raise ArtifactError("the M3 interval quantiles are not ordered")
        points.append(
            IntervalPoint(
                target_date=day,
                horizon=int(row["horizon"]),
                prediction=prediction,
                quantiles=dict(zip(quantiles, values, strict=True)),
                status="projected",
                holiday_related=day in related,
            )
        )
    return M3Intervals(
        run_dir=run_dir,
        run_id=_opt_str(manifest.get("run_id")),
        config_fingerprint=_opt_str(manifest.get("config_fingerprint")),
        sha256=pinned,
        pair_id=status.selected_pair,
        points=tuple(points),
    )


_TOTAL_FIELDS: Final = tuple(PlanTotals.model_fields)
_INTEGER_FIELDS: Final = ("days_with_backlog",)


def _totals(row: Mapping[str, str | None]) -> PlanTotals | None:
    if _number(row.get("penalised_score")) is None:
        return None
    values: dict[str, Any] = {}
    for name in _TOTAL_FIELDS:
        number = _number(row.get(name))
        values[name] = int(number) if name in _INTEGER_FIELDS and number is not None else number
    return PlanTotals.model_validate(values)


def _cells(row: Mapping[str, Any]) -> dict[str, str | None]:
    """A row as text cells: file rows already are; frame values are converted (a missing
    value, ``None`` or NaN, becomes ``None``; ``str`` of a float round-trips exactly)."""
    cells: dict[str, str | None] = {}
    for key, value in row.items():
        missing = value is None or (isinstance(value, float) and math.isnan(value))
        cells[str(key)] = None if missing else str(value)
    return cells


def plan_variant(row: Mapping[str, Any]) -> PlanVariant:
    """One ``scenarios.csv`` row (strings from the file, or values from a frame)."""
    cells = _cells(row)
    return PlanVariant(
        plan=str(cells["plan"]),
        feasibility=str(cells["feasibility"]),
        plan_selection=_text(cells.get("plan_selection")),
        unique_optimal_plan=_flag(cells.get("unique_optimal_plan")),
        unique_after_tie_breaks=_flag(cells.get("unique_after_tie_breaks")),
        solver_status=_text(cells.get("solver_status")),
        terminal_backlog_max=_number(cells.get("terminal_backlog_max")),
        terminal_violation=_number(cells.get("terminal_violation")),
        satisfies_budget=_flag(cells.get("satisfies_budget")),
        reported_plan_relation=_text(cells.get("reported_plan_relation")),
        totals=_totals(cells),
    )


def day_plan(row: Mapping[str, Any]) -> DayPlan:
    """One ``plans.csv`` row (strings from the file, or values from a frame)."""
    cells = _cells(row)

    def required(name: str) -> float:
        value = _number(cells.get(name))
        if value is None:
            raise ValueError(f"plans.csv: {name} is empty")
        return value

    return DayPlan(
        plan=str(cells["plan"]),
        day=int(str(cells["day"])),
        date=date.fromisoformat(str(cells["date"])),
        demand=required("demand"),
        work_available=required("work_available"),
        regular_capacity=required("regular_capacity"),
        overtime_hours=required("overtime_hours"),
        overtime_capacity=required("overtime_capacity"),
        surge_requests=required("surge_requests"),
        served=required("served"),
        backlog=required("backlog"),
        extra_capacity=required("extra_capacity"),
        extra_capacity_min_over_optimal_plans=_number(
            cells.get("extra_capacity_min_over_optimal_plans")
        ),
        extra_capacity_max_over_optimal_plans=_number(
            cells.get("extra_capacity_max_over_optimal_plans")
        ),
    )


#: Labels every M4 row must carry (checked on every row the API serves).
M4_ROW_LABELS: Final = {
    "record_type": RECORD_TYPE_DECISION_SIMULATION,
    "parameter_provenance": "HYPOTHETICAL",
    "validated_recommendation": "False",
    "point_forecast_validated": "False",
}


def _check_labels(rows: Sequence[Mapping[str, str]], status: str, what: str) -> None:
    for row in rows:
        for name, value in M4_ROW_LABELS.items():
            if row.get(name) != value:
                raise ArtifactError(f"{what}: a row is not labelled {name}={value}")
        if row.get("m3_interval_status") != status:
            raise ArtifactError(f"{what}: a row does not carry the M3 status {status}")


def _load_m4(root: Path, config: OptimizationConfig, inputs: DecisionInputs) -> M4Reference:
    directory = root / REFERENCE_RUN_DIR
    reports = inspect_optimization_command(directory)
    failed = [issue.code for report in reports for issue in report.errors]
    if failed:
        raise ArtifactError(
            "the M4 reference run failed inspection: " + ", ".join(sorted(set(failed)))
        )
    manifest = _read_json_file(directory / "manifest.json", "M4 run manifest")
    outputs = manifest.get("outputs") or {}
    if result_fingerprint(outputs, RESULT_FILES) != manifest.get("result_fingerprint"):
        raise ArtifactError("the M4 outputs do not give the recorded result fingerprint")
    files = {
        name: _read_verified(directory / name, outputs.get(name), f"M4 {name}")
        for name in ("metrics.json", "plans.csv", "scenarios.csv")
    }
    metrics = _json(files["metrics.json"], "M4 metrics")
    if metrics.get("config_fingerprint") != config.fingerprint():
        raise ArtifactError("the M4 reference run used a different configuration")
    if metrics.get("validated_recommendation") is not False:
        raise ArtifactError("the M4 metrics do not carry validated_recommendation=false")
    forecast = (metrics.get("inputs") or {}).get("forecast") or {}
    if forecast.get("projections_sha256") != inputs.projection.provenance.get("projections_sha256"):
        raise ArtifactError("the M4 reference run used a different M2 projection")
    status = (metrics.get("inputs") or {}).get("uncertainty_status") or {}
    if status.get("interval_status") != inputs.status.interval_status:
        raise ArtifactError("the M4 reference run carries a different M3 status")
    outcomes = {
        str(item.get("scenario_id")): ScenarioOutcome.model_validate(item)
        for item in metrics.get("scenarios") or []
    }
    plans_rows = _csv_rows(files["plans.csv"], "M4 plans.csv")
    variant_rows = _csv_rows(files["scenarios.csv"], "M4 scenarios.csv")
    _check_labels(plans_rows, inputs.status.interval_status, "M4 plans.csv")
    _check_labels(variant_rows, inputs.status.interval_status, "M4 scenarios.csv")
    daily: dict[str, list[DayPlan]] = {sid: [] for sid in outcomes}
    variants: dict[str, list[PlanVariant]] = {sid: [] for sid in outcomes}
    for row in plans_rows:
        daily.setdefault(row["scenario_id"], []).append(day_plan(row))
    for row in variant_rows:
        variants.setdefault(row["scenario_id"], []).append(plan_variant(row))
    if set(daily) != set(outcomes) or set(variants) != set(outcomes):
        raise ArtifactError("the M4 tables and metrics.json describe different scenarios")
    horizon = metrics.get("horizon")
    if not isinstance(horizon, dict):
        raise ArtifactError("the M4 metrics have no horizon")
    return M4Reference(
        run_dir=REFERENCE_RUN_DIR,
        run_id=str(manifest.get("run_id")),
        result_fingerprint=str(manifest.get("result_fingerprint")),
        config_fingerprint=str(metrics.get("config_fingerprint")),
        label=str(metrics.get("label")),
        horizon=horizon,
        outcomes=outcomes,
        variants={k: tuple(v) for k, v in variants.items()},
        daily={k: tuple(v) for k, v in daily.items()},
        hypothetical_parameters=tuple(
            HypotheticalParameter.model_validate(item)
            for item in metrics.get("hypothetical_parameters") or ()
        ),
        structural_assumptions=tuple(str(s) for s in metrics.get("structural_assumptions") or ()),
        sha256={name: _sha256(payload) for name, payload in files.items()},
    )
