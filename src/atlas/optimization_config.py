"""Versioned configuration of an M4 decision simulation (``configs/optimization/``).

Every operational number is a :class:`Quantity`: a value with an exact unit, the
provenance label ``HYPOTHETICAL`` and a written assumption. A value in the wrong unit, a
missing label or a missing assumption is rejected, and nothing else is accepted: ATLAS has
no evidence register for staffing, productivity, costs, budgets, backlogs or service
capacity of any NYC agency, so no such number can be presented as verified.

The configuration names its inputs (the committed M2 projection, the complete M2 run that
recommended the model, and the complete M3 run whose calibration status travels with every
result), one city-wide service unit, the discretionary budget, the scenarios with their
exact adjustment factors, the sensitivity sweeps, the tradeoff frontiers and the solver
settings. As in M2 and M3, unknown keys, duplicate keys and loose types are rejected, and
the fingerprint is the SHA-256 of the canonical JSON form.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from atlas.errors import ConfigError
from atlas.experiment import _reject_duplicate_keys
from atlas.planning import MAX_HORIZON_DAYS

OPTIMIZATION_SCHEMA_VERSION: Final = 1
HYPOTHETICAL: Final = "HYPOTHETICAL"
_ID: Final = r"^[a-z0-9][a-z0-9._-]{0,63}$"
_LABEL: Final = r"^[a-z][a-z0-9_]{0,63}$"
_SHA256: Final = r"^[0-9a-f]{64}$"


class _Config(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)


class _Quantity(_Config):
    """A HYPOTHETICAL number with its unit and the assumption behind it."""

    value: float = Field(ge=0.0, allow_inf_nan=False)
    provenance: Literal["HYPOTHETICAL"]
    assumption: str = Field(min_length=20)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Requests(_Quantity):
    unit: Literal["requests"]


class RequestsPerDay(_Quantity):
    unit: Literal["requests_per_day"]


class StaffHours(_Quantity):
    unit: Literal["staff_hours"]


class StaffHoursPerDay(_Quantity):
    unit: Literal["staff_hours_per_day"]


class RequestsPerStaffHour(_Quantity):
    unit: Literal["requests_per_staff_hour"]
    value: float = Field(gt=0.0, allow_inf_nan=False)


class UsdPerStaffHour(_Quantity):
    unit: Literal["usd_per_staff_hour"]


class UsdPerRequest(_Quantity):
    unit: Literal["usd_per_request"]


class UsdPerRequestDay(_Quantity):
    unit: Literal["usd_per_request_day"]


class Usd(_Quantity):
    unit: Literal["usd"]


class Ratio(_Quantity):
    """A multiplicative factor applied to a base value (1.0 = unchanged)."""

    unit: Literal["ratio"]
    value: float = Field(gt=0.0, le=10.0, allow_inf_nan=False)


class PlanningDay(_Config):
    """A 1-based day of the planning horizon (``horizon + 1``: never within the horizon)."""

    value: int = Field(ge=1, le=MAX_HORIZON_DAYS + 1)
    unit: Literal["planning_day"]
    provenance: Literal["HYPOTHETICAL"]
    assumption: str = Field(min_length=20)


class ForecastSourceConfig(_Config):
    """The committed M2 projection and the complete M2 run that recommended its model.

    The SHA-256 of ``projections.csv`` and the fingerprint of the M2 configuration are
    pinned here, so a reference run cannot silently use an edited projection (the
    configuration is fingerprinted and covered by ``--require-clean``).
    """

    kind: Literal["m2_projection_run"]
    run_dir: str
    projections_sha256: str = Field(pattern=_SHA256)
    m2_config_fingerprint: str = Field(pattern=_SHA256)
    backtest_run_dir: str
    model_id: str = Field(pattern=_ID)


class UncertaintySourceConfig(_Config):
    """The complete M3 run whose calibration status is attached to every M4 result, and
    its projection (stress paths and the demand-percentile guard); hashes pinned."""

    calibration_status: str
    calibration_status_sha256: str = Field(pattern=_SHA256)
    projection_run_dir: str
    projection_intervals_sha256: str = Field(pattern=_SHA256)
    projection_exceedance_sha256: str = Field(pattern=_SHA256)


class ServiceUnitConfig(_Config):
    """One hypothetical city-wide service unit (all parameters HYPOTHETICAL)."""

    unit_id: Literal["citywide"]
    description: str = Field(min_length=20)
    initial_backlog: Requests
    regular_hours_per_day: StaffHoursPerDay
    regular_productivity: RequestsPerStaffHour
    overtime_productivity: RequestsPerStaffHour
    overtime_max_hours_per_day: StaffHoursPerDay
    overtime_total_hours: StaffHours | None
    surge_max_per_day: RequestsPerDay
    surge_total: Requests | None
    surge_available_from_day: PlanningDay
    regular_wage: UsdPerStaffHour
    overtime_wage: UsdPerStaffHour
    surge_price: UsdPerRequest
    backlog_penalty: UsdPerRequestDay
    terminal_penalty: UsdPerRequest
    terminal_backlog_max: Requests | None
    backlog_trigger: Requests


class AdjustmentsConfig(_Config):
    """Scenario changes to the base unit and budget; every factor is HYPOTHETICAL.

    Multipliers scale the base value (regular hours scale the committed regular cost too,
    but never the discretionary budget, which only ``discretionary_budget_multiplier``
    changes); ``demand_shift_per_day`` is added to every day's demand after the multiplier;
    ``cost_multiplier`` scales the regular wage, the overtime wage and the surge price.
    """

    demand_multiplier: Ratio | None = None
    demand_shift_per_day: RequestsPerDay | None = None
    regular_hours_multiplier: Ratio | None = None
    productivity_multiplier: Ratio | None = None
    cost_multiplier: Ratio | None = None
    discretionary_budget_multiplier: Ratio | None = None
    surge_available_from_day: PlanningDay | None = None

    def applied(self) -> dict[str, Any]:
        return {
            name: value.to_dict() if hasattr(value, "to_dict") else value.model_dump(mode="json")
            for name, value in self
            if value is not None
        }


class PointForecastBasis(_Config):
    kind: Literal["point_forecast"] = "point_forecast"


class MarginalQuantileBasis(_Config):
    """Demand = each day's marginal M3 quantile: a HYPOTHETICAL stress path, never a
    probability statement (``interpretation`` must say so explicitly)."""

    kind: Literal["m3_marginal_quantile"]
    quantile: float = Field(gt=0.5, lt=1.0)
    interpretation: Literal["hypothetical_stress_not_a_probability"]


DemandBasis = Annotated[PointForecastBasis | MarginalQuantileBasis, Field(discriminator="kind")]


class ScenarioConfig(_Config):
    scenario_id: str = Field(pattern=_LABEL)
    description: str = Field(min_length=20)
    demand_basis: DemandBasis = PointForecastBasis()
    adjustments: AdjustmentsConfig = AdjustmentsConfig()


SWEEP_PARAMETERS: Final = {
    "demand_multiplier": "ratio",
    "discretionary_budget_multiplier": "ratio",
    "regular_hours_multiplier": "ratio",
    "productivity_multiplier": "ratio",
    "cost_multiplier": "ratio",
    "backlog_penalty": "usd_per_request_day",
}


class SweepConfig(_Config):
    """One parameter varied over strictly increasing HYPOTHETICAL values, the rest fixed."""

    parameter: Literal[
        "demand_multiplier",
        "discretionary_budget_multiplier",
        "regular_hours_multiplier",
        "productivity_multiplier",
        "cost_multiplier",
        "backlog_penalty",
    ]
    unit: Literal["ratio", "usd_per_request_day"]
    values: tuple[float, ...] = Field(min_length=2, max_length=41)
    provenance: Literal["HYPOTHETICAL"]

    @model_validator(mode="after")
    def _valid(self) -> SweepConfig:
        if self.unit != SWEEP_PARAMETERS[self.parameter]:
            raise ValueError(
                f"sweep {self.parameter}: unit must be {SWEEP_PARAMETERS[self.parameter]!r}"
            )
        if any(not b > a for a, b in zip(self.values, self.values[1:], strict=False)):
            raise ValueError(f"sweep {self.parameter}: values must be strictly increasing")
        lowest = (
            0.0
            if self.parameter in ("backlog_penalty", "discretionary_budget_multiplier")
            else None
        )
        if lowest is None and self.values[0] <= 0:
            raise ValueError(f"sweep {self.parameter}: multipliers must be positive")
        if lowest is not None and self.values[0] < lowest:
            raise ValueError(f"sweep {self.parameter}: values must be non-negative")
        if self.unit == "ratio" and self.values[-1] > 10:
            raise ValueError(f"sweep {self.parameter}: multipliers above 10 are not supported")
        return self


class SensitivityConfig(_Config):
    base_scenario: str = Field(pattern=_LABEL)
    sweeps: tuple[SweepConfig, ...] = Field(min_length=1)


class FrontierConfig(_Config):
    """Epsilon-constraint tradeoff curves (discretionary cash against the notional backlog
    penalty)."""

    scenarios: tuple[str, ...] = Field(min_length=1)
    points: int = Field(ge=3, le=41)


class FiniteDifferenceSteps(_Config):
    requests: float = Field(gt=0.0, allow_inf_nan=False)
    staff_hours: float = Field(gt=0.0, allow_inf_nan=False)
    usd: float = Field(gt=0.0, allow_inf_nan=False)


class ShadowPriceConfig(_Config):
    scenarios: tuple[str, ...] = Field(min_length=1)
    finite_difference_steps: FiniteDifferenceSteps


class SolverConfig(_Config):
    """HiGHS dual simplex settings. No time limit: results must not depend on machine load;
    the iteration limit is the deterministic guard. HiGHS rejects tolerances below 1e-10."""

    method: Literal["highs-ds"] = "highs-ds"
    presolve: bool = True
    primal_feasibility_tolerance: float = Field(default=1e-9, ge=1e-10, le=1e-5)
    dual_feasibility_tolerance: float = Field(default=1e-9, ge=1e-10, le=1e-5)
    maxiter: int = Field(default=10_000, ge=100, le=1_000_000)


class OptimizationConfig(_Config):
    """A complete, versioned M4 decision simulation (see ``configs/optimization/``)."""

    schema_version: Literal[1]
    experiment_id: str = Field(pattern=_ID)
    description: str
    snapshot_id: str = Field(pattern=_ID)
    horizon_days: int = Field(ge=1, le=MAX_HORIZON_DAYS)
    currency: Literal["usd"]
    forecast: ForecastSourceConfig
    uncertainty: UncertaintySourceConfig
    service_units: tuple[ServiceUnitConfig, ...] = Field(min_length=1, max_length=1)
    discretionary_budget: Usd | None
    scenarios: tuple[ScenarioConfig, ...] = Field(min_length=1)
    sensitivity: SensitivityConfig | None = None
    frontier: FrontierConfig | None = None
    shadow_prices: ShadowPriceConfig | None = None
    solver: SolverConfig = SolverConfig()

    @model_validator(mode="after")
    def _consistent(self) -> OptimizationConfig:
        ids = [s.scenario_id for s in self.scenarios]
        if len(set(ids)) != len(ids):
            raise ValueError(f"scenario ids must be unique: {ids}")
        named: list[tuple[str, tuple[str, ...]]] = []
        if self.sensitivity is not None:
            named.append(("sensitivity.base_scenario", (self.sensitivity.base_scenario,)))
            parameters = [s.parameter for s in self.sensitivity.sweeps]
            if len(set(parameters)) != len(parameters):
                raise ValueError(f"each sweep parameter may appear once: {parameters}")
        if self.frontier is not None:
            named.append(("frontier.scenarios", self.frontier.scenarios))
        if self.shadow_prices is not None:
            named.append(("shadow_prices.scenarios", self.shadow_prices.scenarios))
        for where, chosen in named:
            unknown = sorted(set(chosen) - set(ids))
            if unknown or len(set(chosen)) != len(chosen):
                raise ValueError(f"{where} must name distinct configured scenarios: {unknown}")
        horizon = self.horizon_days
        for unit in self.service_units:
            if unit.surge_available_from_day.value > horizon + 1:
                raise ValueError("surge_available_from_day must be within 1..horizon_days + 1")
        for scenario in self.scenarios:
            day = scenario.adjustments.surge_available_from_day
            if day is not None and day.value > horizon + 1:
                raise ValueError(
                    f"scenario {scenario.scenario_id}: surge_available_from_day must be within "
                    "1..horizon_days + 1"
                )
        if self.discretionary_budget is None:
            # A budget multiplier would be reported as applied although it changes nothing.
            scaled = [
                s.scenario_id
                for s in self.scenarios
                if s.adjustments.discretionary_budget_multiplier is not None
            ]
            swept = self.sensitivity is not None and any(
                s.parameter == "discretionary_budget_multiplier" for s in self.sensitivity.sweeps
            )
            if scaled or swept:
                raise ValueError(
                    "discretionary_budget is null, so no scenario or sweep may scale it "
                    f"(scenarios {scaled}, budget sweep {swept})"
                )
        return self

    @property
    def unit(self) -> ServiceUnitConfig:
        return self.service_units[0]

    def scenario(self, scenario_id: str) -> ScenarioConfig:
        return next(s for s in self.scenarios if s.scenario_id == scenario_id)

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def hypothetical_parameters(self) -> list[dict[str, Any]]:
        """Every HYPOTHETICAL number of the configuration with its unit and assumption."""
        rows: list[dict[str, Any]] = []
        for unit in self.service_units:
            for name, value in unit:
                if isinstance(value, _Quantity | PlanningDay):
                    rows.append(
                        {"scope": f"unit:{unit.unit_id}", "parameter": name} | value.model_dump()
                    )
        if self.discretionary_budget is not None:
            rows.append(
                {"scope": "budget", "parameter": "discretionary_budget"}
                | self.discretionary_budget.model_dump()
            )
        for scenario in self.scenarios:
            for name, value in scenario.adjustments:
                if value is not None:
                    rows.append(
                        {"scope": f"scenario:{scenario.scenario_id}", "parameter": name}
                        | value.model_dump()
                    )
        if self.sensitivity is not None:
            for sweep in self.sensitivity.sweeps:
                rows.append(
                    {
                        "scope": f"sensitivity:{self.sensitivity.base_scenario}",
                        "parameter": sweep.parameter,
                        "value": ", ".join(f"{v:g}" for v in sweep.values),
                        "unit": sweep.unit,
                        "provenance": sweep.provenance,
                        "assumption": "Sensitivity grid: this parameter varied one value at a "
                        "time, everything else as in the base scenario.",
                    }
                )
        return rows


def parse_optimization_config(payload: Mapping[str, Any] | str) -> OptimizationConfig:
    """Validate an M4 configuration given as JSON text or a decoded mapping (strict)."""
    try:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        json.loads(text, object_pairs_hook=_reject_duplicate_keys)
        return OptimizationConfig.model_validate_json(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"configuration is not valid JSON: {exc}") from exc
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigError(f"invalid optimization configuration: {details}") from exc


def load_optimization_config(path: Path) -> OptimizationConfig:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read configuration {path}: {exc}") from exc
    return parse_optimization_config(text)
