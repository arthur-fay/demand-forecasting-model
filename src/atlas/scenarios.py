"""Scenarios of the M4 decision simulation: the configuration turned into planning problems.

A scenario starts from the base service unit of the configuration and the demand of its
basis (the M2 point forecast, or one per-day marginal M3 quantile as a HYPOTHETICAL stress
path), then applies its adjustment factors in a fixed order:

1. demand ``x demand_multiplier`` then ``+ demand_shift_per_day``;
2. regular staff hours ``x regular_hours_multiplier`` (the committed regular cost moves
   with them; the discretionary budget does not);
3. regular and overtime productivity ``x productivity_multiplier``;
4. regular wage, overtime wage and surge price ``x cost_multiplier``;
5. discretionary budget ``x discretionary_budget_multiplier``;
6. surge available from ``surge_available_from_day`` (zero before it).

Every factor is HYPOTHETICAL and recorded with the scenario, together with the SHA-256 of
its demand path. The regular capacity of every scenario is compared with the demand
percentile that the legacy project called "planning capacity" (the trailing 180-day 75th
percentile at the forecast origin): a configured capacity within :data:`PERCENTILE_GUARD`
of it is rejected, because M4 never uses a demand percentile as a service capacity.
Sensitivity sweeps vary capacity on purpose; a swept point that lands inside that band is
computed and flagged (:func:`capacity_near_percentile`), never presented as the
percentile.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from atlas.decision_inputs import MARGINAL_QUANTILE_PATH, POINT_FORECAST, QuantilePath
from atlas.errors import ConfigError
from atlas.optimization_config import (
    MarginalQuantileBasis,
    OptimizationConfig,
    ScenarioConfig,
    ServiceUnitConfig,
)
from atlas.planning import PlanningProblem, ServiceUnit

#: Relative distance below which a regular capacity counts as the legacy demand percentile.
PERCENTILE_GUARD: Final = 0.01


def demand_sha256(dates: Sequence[date], values: Sequence[float]) -> str:
    """SHA-256 of a demand path as canonical ``YYYY-MM-DD,repr(value)`` lines."""
    lines = "".join(
        f"{day.isoformat()},{float(value)!r}\n" for day, value in zip(dates, values, strict=True)
    )
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Scenario:
    """One configured scenario as a planning problem, with its provenance."""

    scenario_id: str
    description: str
    demand_basis: str
    quantile: float | None
    adjustments: Mapping[str, Any]
    problem: PlanningProblem
    triggers: Mapping[str, float]
    demand_sha256: str
    base_demand_total: float

    @property
    def demand_total(self) -> float:
        return math.fsum(d for unit in self.problem.units for d in unit.demand)

    def regular_capacity_mean(self) -> float:
        unit = self.problem.units[0]
        return math.fsum(unit.regular_capacity(d) for d in range(unit.horizon)) / unit.horizon

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "description": self.description,
            "demand_basis": self.demand_basis,
            "quantile": self.quantile,
            "adjustments": dict(self.adjustments),
            "demand_sha256": self.demand_sha256,
            "demand_total_requests": self.demand_total,
            "demand_total_vs_point_forecast_pct": 100
            * (self.demand_total / self.base_demand_total - 1),
            "regular_capacity_mean_requests_per_day": self.regular_capacity_mean(),
            "discretionary_budget_usd": self.problem.discretionary_budget,
        }


def _factor(value: Any) -> float:
    return 1.0 if value is None else float(value.value)


def build_unit(
    config: ServiceUnitConfig,
    demand: Sequence[float],
    scenario: ScenarioConfig,
) -> ServiceUnit:
    adjust = scenario.adjustments
    horizon = len(demand)
    shift = 0.0 if adjust.demand_shift_per_day is None else adjust.demand_shift_per_day.value
    multiplier = _factor(adjust.demand_multiplier)
    hours = config.regular_hours_per_day.value * _factor(adjust.regular_hours_multiplier)
    productivity = _factor(adjust.productivity_multiplier)
    cost = _factor(adjust.cost_multiplier)
    start = (
        config.surge_available_from_day.value
        if adjust.surge_available_from_day is None
        else adjust.surge_available_from_day.value
    )
    surge = config.surge_max_per_day.value
    return ServiceUnit(
        unit_id=config.unit_id,
        demand=tuple(d * multiplier + shift for d in demand),
        initial_backlog=config.initial_backlog.value,
        regular_hours=tuple(hours for _ in range(horizon)),
        regular_productivity=config.regular_productivity.value * productivity,
        overtime_productivity=config.overtime_productivity.value * productivity,
        overtime_max_hours=tuple(config.overtime_max_hours_per_day.value for _ in range(horizon)),
        surge_max=tuple(surge if day + 1 >= start else 0.0 for day in range(horizon)),
        regular_wage=config.regular_wage.value * cost,
        overtime_wage=config.overtime_wage.value * cost,
        surge_price=config.surge_price.value * cost,
        backlog_penalty=config.backlog_penalty.value,
        terminal_penalty=config.terminal_penalty.value,
        overtime_total_hours=(
            None if config.overtime_total_hours is None else config.overtime_total_hours.value
        ),
        surge_total=None if config.surge_total is None else config.surge_total.value,
        terminal_backlog_max=(
            None if config.terminal_backlog_max is None else config.terminal_backlog_max.value
        ),
        surge_capacity_when_available=surge,
    )


def build_scenarios(
    config: OptimizationConfig,
    dates: Sequence[date],
    point: Sequence[float],
    quantiles: Mapping[float, QuantilePath],
    demand_percentile: float,
) -> tuple[Scenario, ...]:
    """Every configured scenario as a :class:`Scenario` (see the module docstring)."""
    horizon = config.horizon_days
    if len(point) != horizon or len(dates) != horizon:
        raise ConfigError("the demand path does not cover the planning horizon")
    base_total = math.fsum(point)
    scenarios = []
    for scenario in config.scenarios:
        basis = scenario.demand_basis
        quantile: float | None = None
        if isinstance(basis, MarginalQuantileBasis):
            path = quantiles.get(basis.quantile)
            if path is None:
                raise ConfigError(f"scenario {scenario.scenario_id}: no quantile path loaded")
            if tuple(path.target_dates[:horizon]) != tuple(dates):
                raise ConfigError(f"scenario {scenario.scenario_id}: quantile dates differ")
            demand: Sequence[float] = path.values[:horizon]
            quantile = basis.quantile
            label = MARGINAL_QUANTILE_PATH
        else:
            demand = point
            label = POINT_FORECAST
        unit = build_unit(config.unit, demand, scenario)
        budget = (
            None
            if config.discretionary_budget is None
            else config.discretionary_budget.value
            * _factor(scenario.adjustments.discretionary_budget_multiplier)
        )
        problem = PlanningProblem(dates[0], (unit,), budget)
        built = Scenario(
            scenario_id=scenario.scenario_id,
            description=scenario.description,
            demand_basis=label,
            quantile=quantile,
            adjustments=scenario.adjustments.applied(),
            problem=problem,
            triggers={unit.unit_id: config.unit.backlog_trigger.value},
            demand_sha256=demand_sha256(dates, unit.demand),
            base_demand_total=base_total,
        )
        check_capacity_is_not_a_percentile(built, demand_percentile)
        scenarios.append(built)
    return tuple(scenarios)


def capacity_near_percentile(problem: PlanningProblem, demand_percentile: float) -> bool:
    """True when some unit's regular capacity on some day lies within
    :data:`PERCENTILE_GUARD` of the legacy demand percentile."""
    return any(
        abs(unit.regular_capacity(day) - demand_percentile) <= PERCENTILE_GUARD * demand_percentile
        for unit in problem.units
        for day in range(unit.horizon)
    )


def check_capacity_is_not_a_percentile(scenario: Scenario, demand_percentile: float) -> None:
    """Reject a regular capacity equal (within 1 %) to the legacy demand percentile."""
    unit = scenario.problem.units[0]
    for day in range(unit.horizon):
        capacity = unit.regular_capacity(day)
        if abs(capacity - demand_percentile) <= PERCENTILE_GUARD * demand_percentile:
            raise ConfigError(
                f"scenario {scenario.scenario_id}: regular capacity {capacity:,.1f} requests/day "
                f"is within {100 * PERCENTILE_GUARD:g} % of the trailing 180-day 75th "
                f"percentile of demand ({demand_percentile:,.2f}), the number the legacy "
                "project called planning capacity. A demand percentile is not a service "
                "capacity; choose a HYPOTHETICAL capacity from staff hours and productivity."
            )
