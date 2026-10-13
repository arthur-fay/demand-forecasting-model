"""Versioned configuration of an M3 uncertainty experiment (prediction intervals and risk).

An M3 experiment builds on one M2 backtest configuration: it names that configuration and
pins its fingerprint, takes the point models and horizon buckets from it, and states
everything M3 adds:

* the span of origins whose recursive forecast errors (residuals) are generated with the
  M2 engine, and the data latency after which a residual counts as observed;
* the interval methods, the calibration schemes (rolling or fixed) and the candidate
  pairs among which one is selected, plus the simple uncertainty baselines;
* the M3 periods (``selection``, then a later ``evaluation`` period on disjoint targets),
  the selection rule and the criteria for decision-grade status, all fixed in advance;
* the exceedance thresholds: demand percentiles computed from history at each origin, and
  user-supplied thresholds that must carry the ``HYPOTHETICAL`` provenance label.

Like the M2 configuration, unknown keys, duplicate keys and loose types are rejected, and
the fingerprint is the SHA-256 of the canonical JSON form.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from atlas.errors import ConfigError
from atlas.experiment import ExperimentConfig, IsoDate, _reject_duplicate_keys

UNCERTAINTY_SCHEMA_VERSION: Final = 1
_ID: Final = r"^[a-z0-9][a-z0-9._-]{0,63}$"
_LABEL: Final = r"^[a-z][a-z0-9_]{0,63}$"
#: Two levels closer than this are the same level (config floats such as 0.975).
LEVEL_TOLERANCE: Final = 1e-9


class _Config(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


# --- Calibration --------------------------------------------------------------------


class CalibrationConfig(_Config):
    """Which residuals may calibrate an interval issued at origin ``o``.

    A residual (``actual - prediction`` of a past backtest forecast) is eligible only if its
    target date is ``<= c - data_latency_days``, where ``c <= o`` is the calibration origin
    (``o`` itself for a rolling scheme). Horizon ``h`` is calibrated from horizon-``h``
    residuals when they come from at least ``min_calibration_origins`` distinct forecast
    origins; otherwise, with ``horizon_then_bucket`` pooling, from all residuals of the
    predeclared horizon bucket containing ``h`` (again counted in distinct origins: the
    horizons of one forecast path are one unit, not several); otherwise no interval is
    issued. An issued interval whose calibration residuals have a HAC effective sample size
    below ``min_effective_calibration`` is labelled ``thin`` (exploratory).
    """

    data_latency_days: int = Field(ge=0, le=60)
    min_calibration_origins: int = Field(ge=1)
    min_effective_calibration: float = Field(ge=0.0)
    pooling: Literal["horizon_then_bucket", "horizon_only"] = "horizon_then_bucket"


class SchemeConfig(_Config):
    """``rolling``: re-calibrated at every issue origin from the residuals observed by then.
    ``fixed``: calibrated once at the first origin of each period and then frozen.
    ``window_days`` limits the residuals to target dates within the last ``window_days``
    days before the cutoff (``None``: every eligible residual since the residual span began).
    """

    scheme_id: str = Field(pattern=_ID)
    kind: Literal["rolling", "fixed"]
    window_days: int | None = Field(default=None, ge=1, le=3660)


class EmpiricalQuantileConfig(_Config):
    """Empirical quantiles of the calibration residuals (linear interpolation).

    ``additive``: ``y = prediction + r``; ``relative``: ``y = prediction * (1 + r / p)``,
    where ``p`` is the calibration forecast's own prediction (errors scale with the level).
    """

    type: Literal["empirical_quantile"]
    method_id: str = Field(pattern=_ID)
    score: Literal["additive", "relative"]


class SplitConformalConfig(_Config):
    """Split-conformal interval from absolute residuals with the finite-sample rank
    ``ceil((n + 1) * level)``; symmetric around the prediction."""

    type: Literal["split_conformal"]
    method_id: str = Field(pattern=_ID)
    score: Literal["absolute"] = "absolute"


class GaussianOneStepConfig(_Config):
    """Baseline: Normal(prediction, s^2) with ``s`` the RMS of the eligible *horizon-1*
    residuals, the same at every horizon (what one-step errors alone would suggest)."""

    type: Literal["gaussian_one_step"]
    method_id: str = Field(pattern=_ID)


class ClimatologyConfig(_Config):
    """Model-free baseline: the empirical distribution of the last ``window_days`` observed
    daily counts at the forecast's data cutoff, the same for every horizon."""

    type: Literal["climatology"]
    method_id: str = Field(pattern=_ID)
    window_days: int = Field(ge=7, le=3660)
    min_observations: int = Field(ge=2)


MethodConfig = Annotated[
    EmpiricalQuantileConfig | SplitConformalConfig | GaussianOneStepConfig | ClimatologyConfig,
    Field(discriminator="type"),
]
CANDIDATE_TYPES: Final = ("empirical_quantile", "split_conformal")


class PairConfig(_Config):
    """A method used with a calibration scheme (no scheme for climatology)."""

    method: str
    scheme: str | None = None

    @property
    def pair_id(self) -> str:
        return self.method if self.scheme is None else f"{self.method}@{self.scheme}"


# --- Periods, selection, thresholds, criteria ---------------------------------------------


class UncertaintyPeriodConfig(_Config):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    role: Literal["selection", "evaluation"]
    first_origin: IsoDate
    last_origin: IsoDate
    description: str = ""


class IntervalSelectionConfig(_Config):
    """Pre-registered choice of one candidate on the selection period.

    Candidates with more unavailable forecasts than ``max_unavailable_rate`` are ineligible.
    The others are ranked by the mean weighted interval score (WIS, a proper score of the
    central intervals and the median) over ``first_horizon..last_horizon`` on the forecasts
    every eligible candidate issued, for ``point_model``; ties go to the earlier candidate.
    """

    period: str
    point_model: str
    criterion: Literal["wis"] = "wis"
    first_horizon: int = Field(ge=1)
    last_horizon: int = Field(ge=1)
    max_unavailable_rate: float = Field(ge=0.0, le=1.0)


class HistoricalThresholdConfig(_Config):
    """A high-demand reference: the ``quantile`` of the observed daily counts in the last
    ``window_days`` days through each forecast's data cutoff (linear interpolation, as the
    legacy "planning capacity"). A statistic of incoming demand, never service capacity."""

    kind: Literal["historical_quantile"]
    label: str = Field(pattern=_LABEL)
    window_days: int = Field(ge=7, le=3660)
    quantile: float = Field(gt=0.0, lt=1.0)
    min_observations: int = Field(ge=2)
    description: str


class HypotheticalThresholdConfig(_Config):
    """A user-supplied capacity threshold. It must say ``"provenance": "HYPOTHETICAL"``,
    give its units and state its assumptions: nothing in the repository measures the
    capacity of any NYC agency, so such a number is never presented as one."""

    kind: Literal["hypothetical"]
    label: str = Field(pattern=_LABEL)
    value: float = Field(gt=0.0)
    units: Literal["requests_per_day"]
    provenance: Literal["HYPOTHETICAL"]
    assumptions: str = Field(min_length=20)


ThresholdConfig = Annotated[
    HistoricalThresholdConfig | HypotheticalThresholdConfig, Field(discriminator="kind")
]


class CoverageCriterionConfig(_Config):
    level: float = Field(gt=0.0, lt=1.0)
    tolerance_pct: float = Field(gt=0.0, lt=50.0)


class DecisionCriteriaConfig(_Config):
    """Pre-registered conditions for calling the selected intervals decision-grade.

    For every level: |empirical coverage - level| within ``tolerance_pct`` over all horizons
    and in every horizon bucket, and the HAC test of coverage = level not rejected at
    ``alpha`` (conservative p-value) over all horizons. Failing either is
    ``failed_calibration``. Passing both with a HAC effective sample size below
    ``min_effective_sample_size`` is ``insufficient_evidence``.

    For exceedance probabilities of each historical threshold: Brier score below the
    ``exceedance_reference`` baseline with conservative DM p < ``alpha``, mean probability
    within ``exceedance_max_calibration_gap`` of the observed frequency, and the same
    effective-sample-size requirement.
    """

    coverage: tuple[CoverageCriterionConfig, ...] = Field(min_length=1)
    alpha: float = Field(gt=0.0, lt=1.0)
    min_effective_sample_size: float = Field(ge=1.0)
    exceedance_max_calibration_gap: float = Field(gt=0.0, lt=1.0)
    exceedance_reference: str


class UncertaintySensitivityConfig(_Config):
    """Descriptive analyses; they never select and never change a status."""

    latency_days: tuple[int, ...] = ()
    strata: bool = True


class UncertaintyConfig(_Config):
    """A complete, versioned M3 specification (see ``configs/uncertainty/``)."""

    schema_version: Literal[1]
    experiment_id: str = Field(pattern=_ID)
    description: str
    backtest_config: str
    backtest_config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    recommendation: str | None = None
    max_horizon: int = Field(ge=1, le=366)
    point_models: tuple[str, ...] = Field(min_length=1)
    primary_model: str
    residual_first_origin: IsoDate
    residual_last_origin: IsoDate
    levels: tuple[float, ...] = Field(min_length=1)
    quantile_levels: tuple[float, ...] = Field(min_length=3)
    calibration: CalibrationConfig
    schemes: tuple[SchemeConfig, ...] = Field(min_length=1)
    methods: tuple[MethodConfig, ...] = Field(min_length=1)
    candidates: tuple[PairConfig, ...] = Field(min_length=1)
    baselines: tuple[PairConfig, ...] = ()
    periods: tuple[UncertaintyPeriodConfig, ...] = Field(min_length=1)
    selection: IntervalSelectionConfig | None = None
    thresholds: tuple[ThresholdConfig, ...] = ()
    decision_criteria: DecisionCriteriaConfig
    sensitivity: UncertaintySensitivityConfig = UncertaintySensitivityConfig()

    @model_validator(mode="after")
    def _consistent(self) -> UncertaintyConfig:
        problems: list[str] = []
        if len(set(self.point_models)) != len(self.point_models):
            problems.append("point_models must be distinct")
        if self.primary_model not in self.point_models:
            problems.append("primary_model must be one of point_models")
        if self.residual_last_origin < self.residual_first_origin:
            problems.append("residual_last_origin precedes residual_first_origin")
        problems += _level_problems(self.levels, self.quantile_levels)
        scheme_ids = [s.scheme_id for s in self.schemes]
        method_ids = [m.method_id for m in self.methods]
        for kind, ids in (("scheme", scheme_ids), ("method", method_ids)):
            if len(set(ids)) != len(ids):
                problems.append(f"{kind} ids must be unique: {ids}")
        methods = {m.method_id: m for m in self.methods}
        pairs = [*self.candidates, *self.baselines]
        pair_ids = [p.pair_id for p in pairs]
        if len(set(pair_ids)) != len(pair_ids):
            problems.append(f"candidate and baseline pairs must be unique: {pair_ids}")
        for pair in self.candidates:
            method = methods.get(pair.method)
            if method is None or method.type not in CANDIDATE_TYPES:
                problems.append(
                    f"candidate {pair.pair_id}: method must be one of {CANDIDATE_TYPES}"
                )
            if pair.scheme not in scheme_ids:
                problems.append(f"candidate {pair.pair_id}: unknown scheme {pair.scheme!r}")
        for pair in self.baselines:
            method = methods.get(pair.method)
            if method is None or method.type in CANDIDATE_TYPES:
                problems.append(f"baseline {pair.pair_id}: method must be a baseline type")
            elif method.type == "climatology" and pair.scheme is not None:
                problems.append(f"baseline {pair.pair_id}: climatology takes no scheme")
            elif method.type == "gaussian_one_step" and pair.scheme not in scheme_ids:
                problems.append(f"baseline {pair.pair_id}: unknown scheme {pair.scheme!r}")
        problems += self._period_problems()
        if self.selection is not None:
            rule = self.selection
            if rule.period not in {p.name for p in self.periods if p.role == "selection"}:
                problems.append("selection.period must name the period with role 'selection'")
            if rule.point_model != self.primary_model:
                problems.append("selection.point_model must be the primary_model")
            if not 1 <= rule.first_horizon <= rule.last_horizon <= self.max_horizon:
                problems.append("selection horizons must satisfy 1 <= first <= last <= max")
        labels = [t.label for t in self.thresholds]
        if len(set(labels)) != len(labels):
            problems.append(f"threshold labels must be unique: {labels}")
        criteria = self.decision_criteria
        criterion_levels = sorted(c.level for c in criteria.coverage)
        if len(criterion_levels) != len(self.levels) or not all(
            abs(a - b) <= LEVEL_TOLERANCE
            for a, b in zip(criterion_levels, sorted(self.levels), strict=True)
        ):
            problems.append("decision_criteria.coverage must give one tolerance per level")
        reference = methods.get(criteria.exceedance_reference)
        if (
            reference is None
            or reference.type != "climatology"
            or criteria.exceedance_reference not in {p.pair_id for p in self.baselines}
        ):
            problems.append("decision_criteria.exceedance_reference must be a climatology baseline")
        if any(d < self.calibration.data_latency_days for d in self.sensitivity.latency_days):
            problems.append("sensitivity latencies must not be shorter than the primary latency")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def _period_problems(self) -> list[str]:
        problems: list[str] = []
        names = [p.name for p in self.periods]
        if len(set(names)) != len(names):
            problems.append(f"period names must be unique: {names}")
        for role in ("selection", "evaluation"):
            if sum(p.role == role for p in self.periods) > 1:
                problems.append(f"at most one {role} period is allowed")
        for period in self.periods:
            if period.last_origin < period.first_origin:
                problems.append(f"period {period.name}: last_origin precedes first_origin")
            if not (
                self.residual_first_origin
                <= period.first_origin
                <= period.last_origin
                <= self.residual_last_origin
            ):
                problems.append(f"period {period.name} lies outside the residual span")
        selection = self.period_with_role("selection")
        evaluation = self.period_with_role("evaluation")
        if selection is not None and evaluation is not None:
            last_target = selection.last_origin + timedelta(days=self.max_horizon)
            if last_target > evaluation.first_origin:
                problems.append(
                    f"periods {selection.name} and {evaluation.name} overlap: the selection "
                    f"period scores targets through {last_target}"
                )
        return problems

    # --- accessors ------------------------------------------------------------------

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def period_names(self) -> list[str]:
        return [p.name for p in self.periods]

    def period(self, name: str) -> UncertaintyPeriodConfig:
        for period in self.periods:
            if period.name == name:
                return period
        raise ConfigError(f"unknown period {name!r}; configured: {self.period_names}")

    def period_with_role(self, role: str) -> UncertaintyPeriodConfig | None:
        return next((p for p in self.periods if p.role == role), None)

    def method(self, method_id: str) -> MethodConfig:
        return next(m for m in self.methods if m.method_id == method_id)

    def scheme(self, scheme_id: str) -> SchemeConfig:
        return next(s for s in self.schemes if s.scheme_id == scheme_id)

    def pairs(self) -> tuple[PairConfig, ...]:
        return (*self.candidates, *self.baselines)

    def pair(self, pair_id: str) -> PairConfig:
        for pair in self.pairs():
            if pair.pair_id == pair_id:
                return pair
        raise ConfigError(f"unknown candidate or baseline {pair_id!r}")

    def tolerance_pct(self, level: float) -> float:
        return next(
            c.tolerance_pct
            for c in self.decision_criteria.coverage
            if abs(c.level - level) <= LEVEL_TOLERANCE
        )

    def historical_thresholds(self) -> tuple[HistoricalThresholdConfig, ...]:
        return tuple(t for t in self.thresholds if isinstance(t, HistoricalThresholdConfig))


def central_bounds(level: float) -> tuple[float, float]:
    """Lower and upper quantile levels of the central interval of ``level``."""
    tail = (1.0 - level) / 2.0
    return tail, 1.0 - tail


def level_index(grid: tuple[float, ...], level: float) -> int:
    """Position of ``level`` in the quantile grid (``ConfigError`` if absent)."""
    for position, value in enumerate(grid):
        if abs(value - level) <= LEVEL_TOLERANCE:
            return position
    raise ConfigError(f"quantile level {level} is not in the grid {grid}")


def _level_problems(levels: tuple[float, ...], grid: tuple[float, ...]) -> list[str]:
    problems: list[str] = []
    if any(not 0.0 < g < 1.0 or not math.isfinite(g) for g in grid):
        problems.append("quantile_levels must lie strictly between 0 and 1")
    elif any(b - a <= LEVEL_TOLERANCE for a, b in itertools.pairwise(grid)):
        problems.append("quantile_levels must be strictly increasing")
    elif not any(abs(g - 0.5) <= LEVEL_TOLERANCE for g in grid):
        problems.append("quantile_levels must contain the median 0.5")
    if any(not 0.0 < level < 1.0 for level in levels):
        problems.append("levels must lie strictly between 0 and 1")
    elif any(b - a <= LEVEL_TOLERANCE for a, b in itertools.pairwise(levels)):
        problems.append("levels must be strictly increasing")
    else:
        for level in levels:
            for bound in central_bounds(level):
                if not any(abs(g - bound) <= LEVEL_TOLERANCE for g in grid):
                    problems.append(f"quantile_levels must contain {bound:g} for level {level:g}")
    return problems


# --- Loading and cross-checks with the M2 configuration -------------------------------------


def parse_uncertainty_config(payload: Mapping[str, Any] | str) -> UncertaintyConfig:
    """Validate an M3 configuration given as JSON text or a decoded mapping (strict)."""
    try:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        json.loads(text, object_pairs_hook=_reject_duplicate_keys)
        return UncertaintyConfig.model_validate_json(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"configuration is not valid JSON: {exc}") from exc
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigError(f"invalid uncertainty configuration: {details}") from exc


def load_uncertainty_config(path: Path) -> UncertaintyConfig:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read configuration {path}: {exc}") from exc
    return parse_uncertainty_config(text)


def check_against_backtest(config: UncertaintyConfig, backtest: ExperimentConfig) -> None:
    """Rules that tie M3 to its M2 protocol; raises :class:`ConfigError` listing problems.

    * the M2 configuration is the pinned one, with the same horizon and known point models;
    * the data latency is at least M2's data delay (a forecast issued at ``o`` sees data
      through ``o - delay`` only, so a residual is not observed before that either);
    * the M3 selection period scores only targets of M2's selection (development) period,
      and the M3 evaluation period issues forecasts only at M2 confirmation origins. The
      confirmation period was already used by M2; M3 never treats it as untouched.
    """
    problems: list[str] = []
    if backtest.fingerprint() != config.backtest_config_fingerprint:
        problems.append(
            f"the backtest configuration has fingerprint {backtest.fingerprint()}, not the "
            f"pinned {config.backtest_config_fingerprint}"
        )
    if backtest.max_horizon != config.max_horizon:
        problems.append(
            f"max_horizon {config.max_horizon} differs from the backtest's {backtest.max_horizon}"
        )
    known = {m.model_id for m in backtest.models}
    unknown = sorted(set(config.point_models) - known)
    if unknown:
        problems.append(f"point models {unknown} are not configured in the backtest")
    if config.calibration.data_latency_days < backtest.data_delay_days:
        problems.append(
            f"data_latency_days {config.calibration.data_latency_days} is shorter than the "
            f"backtest's data delay {backtest.data_delay_days}"
        )
    first_history = config.residual_first_origin
    periods = {p.name: p for p in backtest.periods}
    m2_selection = next((p for p in periods.values() if p.role == "selection"), None)
    m2_confirmation = next((p for p in periods.values() if p.role == "confirmation"), None)
    m3_selection = config.period_with_role("selection")
    m3_evaluation = config.period_with_role("evaluation")
    if m3_selection is not None:
        if m2_selection is None:
            problems.append("the backtest has no selection period for M3 selection")
        else:
            last_target = m3_selection.last_origin + timedelta(days=config.max_horizon)
            m2_last_target = m2_selection.last_origin + timedelta(days=backtest.max_horizon)
            if m3_selection.first_origin < m2_selection.first_origin or (
                last_target > m2_last_target
            ):
                problems.append(
                    "the M3 selection period must score only targets of the M2 selection "
                    f"period ({m2_selection.first_origin}..{m2_last_target})"
                )
    if m3_evaluation is not None:
        if m2_confirmation is None:
            problems.append("the backtest has no confirmation period for M3 evaluation")
        elif not (
            m2_confirmation.first_origin
            <= m3_evaluation.first_origin
            <= m3_evaluation.last_origin
            <= m2_confirmation.last_origin
        ):
            problems.append(
                "the M3 evaluation period must lie within the M2 confirmation origins "
                f"({m2_confirmation.first_origin}..{m2_confirmation.last_origin})"
            )
    if first_history < _earliest_origin(backtest):
        problems.append(
            f"residual_first_origin {first_history} precedes every M2 origin; the residual "
            "span must start at or after the first M2 origin"
        )
    if problems:
        raise ConfigError(
            "uncertainty configuration does not fit its backtest: " + "; ".join(problems)
        )


def _earliest_origin(backtest: ExperimentConfig) -> date:
    return min(p.first_origin for p in backtest.periods)
