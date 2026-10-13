"""Versioned experiment configuration, fingerprints, provenance and run persistence (M2).

An experiment is a JSON file validated by :class:`ExperimentConfig` (``schema_version``
1; unknown keys, duplicate keys and loose dates are rejected). Its fingerprint is the
SHA-256 of its canonical JSON form, so two configurations that differ in any value have
different fingerprints, while formatting and key order do not matter.

A run is persisted as a small directory of plain files (no database or tracking
server):

=================  =====================================================================
``manifest.json``  record type, run id, config and fingerprint, data provenance and
                   fingerprints, model identities, git commit, Python and package versions,
                   SHA-256 of every output, and the result fingerprint
``config.json``    the exact configuration used
``forecasts.csv``  one row per model, origin and horizon (origin, target date, horizon,
                   prediction, actual, status, failure reason)
``fits.csv``       one row per model and origin (training window, rank, conditioning, ...)
``metrics.json``   every metric, comparison and the selection, machine-readable
``report.md``      the same results for people
=================  =====================================================================

Every file of a backtest says ``historical_backtest``; every file of a projection says
``future_projection``. Outputs are written deterministically (fixed float precision,
sorted rows, no timestamps), so the result fingerprint depends only on the code, the
configuration and the data. ``created_at_utc`` and ``runtime_seconds`` in the manifest
are informational and excluded from every fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from importlib import metadata
from pathlib import Path
from typing import Annotated, Any, Final, Literal

import numpy as np
import pandas as pd
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from atlas import __version__
from atlas.backtest import (
    RECORD_TYPE_BACKTEST,
    RECORD_TYPE_LEGACY_HOLDOUT,
    RECORD_TYPE_OBSERVED_INPUTS,
    RECORD_TYPE_PROJECTION,
    BacktestPlan,
    Period,
    PeriodRole,
    TrainingWindow,
)
from atlas.dates import as_date
from atlas.errors import ConfigError
from atlas.metrics import HorizonBucket
from atlas.models import (
    Forecaster,
    NaiveForecaster,
    SameWeekdayMeanForecaster,
    SameWeekdayMedianForecaster,
    SeasonalExponentialSmoothing,
    SeasonalNaiveForecaster,
    atlas_linear_regression,
    legacy_linear_regression,
)
from atlas.series import DemandSeries

CONFIG_SCHEMA_VERSION: Final = 1
MANIFEST_SCHEMA_VERSION: Final = 1
FLOAT_FORMAT: Final = "%.17g"
TRACKED_PACKAGES: Final = ("numpy", "pandas", "pydantic")
RECORD_TYPE_LABELS: Final = {
    RECORD_TYPE_LEGACY_HOLDOUT: (
        "LEGACY ONE-STEP HOLDOUT: the original evaluation, re-computed. One fit through "
        "2026-04-01, then each of the 90 following days predicted from its observed "
        "previous days (horizon 1, observed lags). Not a 30-day forecast."
    ),
    RECORD_TYPE_BACKTEST: (
        "HISTORICAL BACKTEST: rolling-origin re-forecasts of past days from a frozen data "
        "snapshot, refitted at every origin with data through that origin only and scored "
        "against the recorded actuals. Not prospective validation."
    ),
    RECORD_TYPE_PROJECTION: (
        "FUTURE PROJECTION: forecasts beyond the last observed day. No outcome has been "
        "observed; these numbers are not validated by any backtest."
    ),
    RECORD_TYPE_OBSERVED_INPUTS: (
        "DIAGNOSTIC (observed inputs): each origin's fitted regression applied to observed "
        "lags after the origin. It reads future actuals by construction, so it is never "
        "used to compare or choose models; it only measures what recursion costs."
    ),
}
_ID_PATTERN: Final = r"^[a-z0-9][a-z0-9._-]{0,63}$"


# --- Configuration --------------------------------------------------------------------


def _strict_date(value: object) -> date:
    if isinstance(value, datetime):
        raise ValueError("expected a YYYY-MM-DD date, not a timestamp")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return as_date(value)
    raise ValueError(f"expected a YYYY-MM-DD date, got {type(value).__name__}")


IsoDate = Annotated[date, BeforeValidator(_strict_date)]


class _Config(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class PeriodConfig(_Config):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    role: Literal["selection", "confirmation", "descriptive"]
    first_origin: IsoDate
    last_origin: IsoDate
    origin_stride_days: int = Field(default=1, ge=1, le=366)
    allow_incomplete_horizons: bool = False


class TrainingWindowConfig(_Config):
    kind: Literal["expanding", "sliding"] = "expanding"
    days: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _days_match_kind(self) -> TrainingWindowConfig:
        if (self.kind == "sliding") != (self.days is not None):
            raise ValueError("a sliding window needs 'days'; an expanding window takes none")
        return self


class HorizonBucketConfig(_Config):
    label: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    first: int = Field(ge=1)
    last: int = Field(ge=1)


class _ModelConfig(_Config):
    model_id: str = Field(pattern=_ID_PATTERN)


class NaiveConfig(_ModelConfig):
    type: Literal["naive"]


class SeasonalNaiveConfig(_ModelConfig):
    type: Literal["seasonal_naive"]
    season_days: int = Field(default=7, ge=1, le=366)


class SameWeekdayMeanConfig(_ModelConfig):
    type: Literal["same_weekday_mean"]
    weeks: int = Field(ge=1, le=52)


class SameWeekdayMedianConfig(_ModelConfig):
    type: Literal["same_weekday_median"]
    weeks: int = Field(ge=1, le=52)


class ExponentialSmoothingConfig(_ModelConfig):
    type: Literal["seasonal_exponential_smoothing"]
    season_days: int = Field(default=7, ge=2, le=366)
    burn_in_days: int = Field(default=28, ge=0)


class LegacyRegressionConfig(_ModelConfig):
    type: Literal["legacy_linear_regression"]
    min_training_rows: int = Field(ge=1)


class AtlasRegressionConfig(_ModelConfig):
    type: Literal["atlas_linear_regression"]
    min_training_rows: int = Field(ge=1)
    feature_set: Literal["atlas-m2-regression-v1"] = "atlas-m2-regression-v1"


ModelConfig = Annotated[
    NaiveConfig
    | SeasonalNaiveConfig
    | SameWeekdayMeanConfig
    | SameWeekdayMedianConfig
    | ExponentialSmoothingConfig
    | LegacyRegressionConfig
    | AtlasRegressionConfig,
    Field(discriminator="type"),
]


class SelectionConfig(_Config):
    """The pre-registered selection rule (see :mod:`atlas.evaluation`)."""

    period: str
    candidates: tuple[str, ...] = Field(min_length=1)
    metric: Literal["mae"] = "mae"
    first_horizon: int = Field(ge=1)
    last_horizon: int = Field(ge=1)
    max_failure_rate: float = Field(ge=0.0, le=1.0)
    regression_margin_pct: float = Field(ge=0.0, le=100.0)
    alpha: float = Field(gt=0.0, lt=1.0)


class OriginExclusionConfig(_Config):
    label: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    first_origin: IsoDate
    last_origin: IsoDate
    reason: str


class SensitivityConfig(_Config):
    """Pre-registered secondary analyses. They describe; they never select."""

    exclude_flagged_targets: bool = False
    holiday_stratum: bool = False
    origin_exclusions: tuple[OriginExclusionConfig, ...] = ()


class ComparisonWindowConfig(_Config):
    label: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: Literal["plain", "legacy_holdout"] = "plain"
    description: str
    period: str
    first_target: IsoDate
    last_target: IsoDate


class ExperimentConfig(_Config):
    """A complete, versioned backtest specification (see ``configs/backtest/``)."""

    schema_version: Literal[1]
    experiment_id: str = Field(pattern=_ID_PATTERN)
    description: str
    snapshot_id: str = Field(pattern=_ID_PATTERN)
    target_table: Literal["daily_total"]
    max_horizon: int = Field(ge=1, le=366)
    min_history_days: int = Field(ge=1)
    data_delay_days: int = Field(default=0, ge=0, le=60)
    observed_input_diagnostic: bool = False
    training_window: TrainingWindowConfig = TrainingWindowConfig()
    periods: tuple[PeriodConfig, ...] = Field(min_length=1)
    horizon_buckets: tuple[HorizonBucketConfig, ...] = Field(min_length=1)
    models: tuple[ModelConfig, ...] = Field(min_length=1)
    reference_model: str
    selection: SelectionConfig | None = None
    sensitivity: SensitivityConfig = SensitivityConfig()
    comparison_windows: tuple[ComparisonWindowConfig, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> ExperimentConfig:
        ids = [model.model_id for model in self.models]
        if len(set(ids)) != len(ids):
            raise ValueError(f"model ids must be unique: {ids}")
        reference = next((m for m in self.models if m.model_id == self.reference_model), None)
        if not isinstance(reference, SeasonalNaiveConfig) or reference.season_days != 7:
            raise ValueError("reference_model must be a seasonal_naive model with season_days 7")
        periods = {period.name: period for period in self.periods}
        if self.selection is not None:
            selection = self.selection
            chosen = periods.get(selection.period)
            if chosen is None or chosen.role != "selection":
                raise ValueError("selection.period must name the period with role 'selection'")
            unknown = sorted(set(selection.candidates) - set(ids))
            if unknown or len(set(selection.candidates)) != len(selection.candidates):
                raise ValueError(f"selection candidates must be distinct model ids: {unknown}")
            if not 1 <= selection.first_horizon <= selection.last_horizon <= self.max_horizon:
                raise ValueError(
                    "selection horizons must satisfy 1 <= first <= last <= max_horizon"
                )
        labels = [bucket.label for bucket in self.horizon_buckets]
        if len(set(labels)) != len(labels):
            raise ValueError(f"horizon bucket labels must be unique: {labels}")
        for bucket in self.horizon_buckets:
            if not bucket.first <= bucket.last <= self.max_horizon:
                raise ValueError(f"horizon bucket {bucket.label} is outside 1..max_horizon")
        for window in self.comparison_windows:
            if window.last_target < window.first_target:
                raise ValueError(f"comparison window {window.label} ends before it starts")
            if window.period not in periods:
                raise ValueError(f"comparison window {window.label}: unknown period")
        for exclusion in self.sensitivity.origin_exclusions:
            if exclusion.last_origin < exclusion.first_origin:
                raise ValueError(f"origin exclusion {exclusion.label} ends before it starts")
        # The plan of *all* periods carries the period rules (at most one selection and one
        # confirmation period, disjoint targets), so a run of any subset inherits them.
        try:
            self.plan()
        except ConfigError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def period_names(self) -> list[str]:
        return [p.name for p in self.periods]

    def period_role(self, name: str) -> str:
        return next(p.role for p in self.periods if p.name == name)

    def plan(self, periods: Sequence[str] | None = None) -> BacktestPlan:
        """The backtest plan, optionally restricted to the named periods."""
        unknown = sorted(set(periods or ()) - set(self.period_names))
        if unknown:
            raise ConfigError(f"unknown period(s) {unknown}; configured: {self.period_names}")
        chosen = [p for p in self.periods if periods is None or p.name in periods]
        return BacktestPlan(
            periods=tuple(
                Period(
                    p.name,
                    PeriodRole(p.role),
                    p.first_origin,
                    p.last_origin,
                    p.origin_stride_days,
                    p.allow_incomplete_horizons,
                )
                for p in chosen
            ),
            max_horizon=self.max_horizon,
            min_history_days=self.min_history_days,
            training_window=TrainingWindow(self.training_window.days),
            data_delay_days=self.data_delay_days,
            observed_input_diagnostic=self.observed_input_diagnostic,
        )

    def buckets(self) -> tuple[HorizonBucket, ...]:
        return tuple(HorizonBucket(b.label, b.first, b.last) for b in self.horizon_buckets)

    def build_models(self) -> tuple[Forecaster, ...]:
        return tuple(build_model(model) for model in self.models)


def build_model(config: ModelConfig) -> Forecaster:
    """The :class:`~atlas.models.Forecaster` described by one model configuration."""
    match config:
        case NaiveConfig():
            return NaiveForecaster(config.model_id)
        case SeasonalNaiveConfig():
            return SeasonalNaiveForecaster(config.model_id, config.season_days)
        case SameWeekdayMeanConfig():
            return SameWeekdayMeanForecaster(config.model_id, config.weeks)
        case SameWeekdayMedianConfig():
            return SameWeekdayMedianForecaster(config.model_id, config.weeks)
        case ExponentialSmoothingConfig():
            return SeasonalExponentialSmoothing(
                config.model_id, config.season_days, config.burn_in_days
            )
        case LegacyRegressionConfig():
            return legacy_linear_regression(config.model_id, config.min_training_rows)
        case AtlasRegressionConfig():
            return atlas_linear_regression(config.model_id, config.min_training_rows)
    raise AssertionError(f"unhandled model configuration {config!r}")  # pragma: no cover


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    duplicated = sorted({key for key in keys if keys.count(key) > 1})
    if duplicated:
        raise ConfigError(f"duplicate key(s) in configuration: {duplicated}")
    return dict(pairs)


def parse_config(payload: Mapping[str, Any] | str) -> ExperimentConfig:
    """Validate a configuration given as JSON text or a decoded mapping (strict types)."""
    try:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        json.loads(text, object_pairs_hook=_reject_duplicate_keys)
        return ExperimentConfig.model_validate_json(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"configuration is not valid JSON: {exc}") from exc
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigError(f"invalid experiment configuration: {details}") from exc


def load_config(path: Path) -> ExperimentConfig:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read configuration {path}: {exc}") from exc
    return parse_config(text)


# --- Provenance ---------------------------------------------------------------------

CODE_PATHS: Final = ("src/atlas", "pyproject.toml", "uv.lock")
GIT_SCOPE: Final = ("src", "configs", "tests", "pyproject.toml", "uv.lock")
THREAD_VARIABLES: Final = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def series_fingerprint(series: DemandSeries) -> str:
    """SHA-256 of the series as canonical ``YYYY-MM-DD,value`` lines (empty = missing)."""
    values = series.to_series()
    lines = []
    for stamp, value in zip(pd.DatetimeIndex(values.index), values.to_numpy(), strict=True):
        rendered = "" if math.isnan(value) else repr(float(value))
        lines.append(f"{stamp.date().isoformat()},{rendered}\n")
    return sha256_bytes("".join(lines).encode("utf-8"))


def code_fingerprint(repo_root: Path) -> dict[str, Any]:
    """SHA-256 over the package source, ``pyproject.toml`` and ``uv.lock`` (git-independent).

    Hashes sorted ``(relative path, bytes)`` pairs of every ``*.py`` file under
    ``src/atlas`` plus the two project files; caches are ignored.
    """
    digest = hashlib.sha256()
    files = 0
    for relative in CODE_PATHS:
        path = repo_root / relative
        candidates = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        for candidate in candidates:
            if not candidate.is_file() or "__pycache__" in candidate.parts:
                continue
            name = candidate.relative_to(repo_root).as_posix()
            digest.update(name.encode("utf-8") + b"\0" + candidate.read_bytes() + b"\0")
            files += 1
    lock = repo_root / "uv.lock"
    return {
        "code_sha256": digest.hexdigest(),
        "files": files,
        "uv_lock_sha256": sha256_bytes(lock.read_bytes()) if lock.is_file() else None,
    }


def _blas_info() -> dict[str, Any]:
    config = np.show_config(mode="dicts")  # numpy >= 1.26 returns the build configuration
    dependencies = config.get("Build Dependencies", {}) if isinstance(config, dict) else {}
    result: dict[str, Any] = {}
    for name in ("blas", "lapack"):
        entry = dependencies.get(name, {})
        if isinstance(entry, dict):
            result[name] = {k: entry.get(k) for k in ("name", "version")}
    return result


def environment_info() -> dict[str, Any]:
    packages: dict[str, str | None] = {}
    for name in TRACKED_PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:  # pragma: no cover - always installed
            packages[name] = None
    return {
        "atlas": __version__,
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
        "numpy_blas_lapack": _blas_info(),
        "thread_settings": {name: os.environ.get(name) for name in THREAD_VARIABLES},
    }


def git_info(repo_root: Path) -> dict[str, Any]:
    """Commit and working-tree state of ``repo_root`` (``available: false`` without git).

    ``dirty`` counts modified *and untracked* files under the code, configuration and
    test paths. ``--no-optional-locks`` keeps ``git status`` from writing to the index.
    """
    executable = shutil.which("git")
    if executable is None:
        return {"available": False, "commit": None, "dirty": None}

    def run(*args: str) -> str | None:
        # Absolute executable, fixed argument list, no shell, no user input.
        try:
            completed = subprocess.run(  # noqa: S603
                [executable, "--no-optional-locks", "-C", str(repo_root), *args],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return completed.stdout.strip() if completed.returncode == 0 else None

    commit = run("rev-parse", "HEAD")
    if commit is None:
        return {"available": False, "commit": None, "dirty": None}
    status = run("status", "--porcelain", "--untracked-files=all", "--", *GIT_SCOPE)
    return {
        "available": True,
        "commit": commit,
        "dirty": None if status is None else bool(status),
        "dirty_paths": None if status is None else len(status.splitlines()),
    }


# --- Deterministic serialisation ------------------------------------------------------


def jsonable(value: Any) -> Any:
    """Plain JSON values: numpy scalars unwrapped, non-finite floats -> null, dates -> ISO."""
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, np.bool_ | bool):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating | float):
        number = float(value)
        if not math.isfinite(number):
            return None
        return 0.0 if number == 0 else number  # no "-0.0"
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def json_bytes(payload: Any) -> bytes:
    """Strict JSON (no NaN or Infinity), sorted keys, lossless floats."""
    text = json.dumps(jsonable(payload), indent=1, sort_keys=True, allow_nan=False)
    return (text + "\n").encode("utf-8")


def csv_bytes(frame: pd.DataFrame) -> bytes:
    """CSV with lossless floats (17 significant digits), ISO dates and ``\\n`` line ends."""
    normalized = frame.copy()
    for column in normalized.columns:
        if normalized[column].dtype == "float64":
            normalized[column] = normalized[column] + 0.0  # -0.0 -> 0.0
    text = normalized.to_csv(
        index=False, float_format=FLOAT_FORMAT, date_format="%Y-%m-%d", lineterminator="\n"
    )
    return text.encode("utf-8")


def result_fingerprint(outputs: Mapping[str, str], files: Sequence[str]) -> str:
    """SHA-256 over the hashes of the deterministic result files, in a fixed order."""
    joined = "".join(f"{name}:{outputs[name]}\n" for name in files)
    return sha256_bytes(joined.encode("utf-8"))


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def write_run(directory: Path, files: Mapping[str, bytes], manifest: Mapping[str, Any]) -> None:
    """Write ``files`` then ``manifest.json`` atomically into a new directory.

    Everything is written into a temporary sibling that is renamed into place at the
    end, so an interrupted run never leaves a directory that looks complete.
    """
    if directory.exists():
        raise ConfigError(f"output directory {directory} already exists; choose another")
    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{directory.name}.", dir=directory.parent))
    try:
        for name, payload in files.items():
            (staging / name).write_bytes(payload)
        (staging / "manifest.json").write_bytes(json_bytes(manifest))
        staging.rename(directory)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
