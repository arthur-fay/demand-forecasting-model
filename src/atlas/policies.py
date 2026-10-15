"""Simple decision rules compared with the optimised plan under the same assumptions (M4).

Both policies act on exactly the same :class:`~atlas.planning.PlanningProblem` as the
linear programme (same demand, capacities, prices, limits and budget) and are scored by
the same :func:`~atlas.planning.evaluate_plan` and checked by the same
:func:`~atlas.planning.check_plan`. A policy plan that violates a constraint (typically the
HYPOTHETICAL terminal-backlog target) is reported as such and is not comparable with the
optimised plan on score.

``fixed_capacity``
    Regular staff only: no overtime, no surge. Each day serves as much of the available
    work as regular capacity allows, ``x[t] = min(b[t-1] + d[t], p * H[t])``.

``backlog_trigger``
    A transparent, myopic rule. Days are processed in order (units in problem order within
    a day). When the work left after regular capacity would exceed the unit's trigger
    level, extra capacity is bought to bring the end-of-day backlog down to the trigger,
    cheaper source first (overtime at ``c_ot / q`` USD per request against surge at ``c_s``;
    overtime on a tie), within the daily limits, the remaining horizon totals and the
    remaining discretionary budget. Nothing is bought below the trigger and nothing is
    saved for later days. Bought capacity is always fully used.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Final

from atlas.planning import Plan, PlanningError, PlanningProblem, UnitPlan

FIXED_CAPACITY: Final = "fixed_capacity"
BACKLOG_TRIGGER: Final = "backlog_trigger"
POLICIES: Final = (FIXED_CAPACITY, BACKLOG_TRIGGER)


def fixed_capacity_plan(problem: PlanningProblem) -> Plan:
    """Regular capacity only (see the module docstring)."""
    units = []
    for unit in problem.units:
        backlog: list[float] = []
        served: list[float] = []
        level = unit.initial_backlog
        for day in range(problem.horizon):
            work = level + unit.demand[day]
            done = min(work, unit.regular_capacity(day))
            level = work - done
            served.append(done)
            backlog.append(level)
        zeros = tuple(0.0 for _ in range(problem.horizon))
        units.append(UnitPlan(unit.unit_id, tuple(served), zeros, zeros, tuple(backlog)))
    return Plan(tuple(units))


def backlog_trigger_plan(problem: PlanningProblem, triggers: Mapping[str, float]) -> Plan:
    """The backlog-trigger rule (see the module docstring); one trigger (requests) per unit."""
    missing = sorted({u.unit_id for u in problem.units} - set(triggers))
    if missing:
        raise PlanningError(f"no backlog trigger for unit(s) {missing}")
    for unit_id, trigger in triggers.items():
        if not math.isfinite(trigger) or trigger < 0:
            raise PlanningError(f"trigger of {unit_id} must be finite and non-negative")
    remaining_budget = (
        math.inf if problem.discretionary_budget is None else problem.discretionary_budget
    )
    levels = {u.unit_id: u.initial_backlog for u in problem.units}
    hours_left = {
        u.unit_id: math.inf if u.overtime_total_hours is None else u.overtime_total_hours
        for u in problem.units
    }
    surge_left = {
        u.unit_id: math.inf if u.surge_total is None else u.surge_total for u in problem.units
    }
    decisions: dict[str, dict[str, list[float]]] = {
        u.unit_id: {"served": [], "overtime_hours": [], "surge": [], "backlog": []}
        for u in problem.units
    }
    for day in range(problem.horizon):
        for unit in problem.units:
            uid = unit.unit_id
            work = levels[uid] + unit.demand[day]
            regular = unit.regular_capacity(day)
            shortfall = max(0.0, work - regular - triggers[uid])
            bought_hours = 0.0
            bought_surge = 0.0
            sources = [
                ("overtime", unit.overtime_cost_per_request),
                ("surge", unit.surge_price),
            ]
            # Stable sort: overtime first on a tie.
            for source, price in sorted(sources, key=lambda item: item[1]):
                if shortfall <= 0:
                    break
                if source == "overtime":
                    hours = min(unit.overtime_max_hours[day], hours_left[uid])
                    available = hours * unit.overtime_productivity
                else:
                    available = min(unit.surge_max[day], surge_left[uid])
                affordable = math.inf if price == 0 else remaining_budget / price
                take = max(0.0, min(shortfall, available, affordable))
                if take <= 0:
                    continue
                if source == "overtime":
                    hours = take / unit.overtime_productivity
                    bought_hours += hours
                    hours_left[uid] -= hours
                    remaining_budget -= hours * unit.overtime_wage
                else:
                    bought_surge += take
                    surge_left[uid] -= take
                    remaining_budget -= take * unit.surge_price
                shortfall -= take
            capacity = regular + bought_hours * unit.overtime_productivity + bought_surge
            done = min(work, capacity)
            levels[uid] = work - done
            record = decisions[uid]
            record["served"].append(done)
            record["overtime_hours"].append(bought_hours)
            record["surge"].append(bought_surge)
            record["backlog"].append(levels[uid])
    return Plan(
        tuple(
            UnitPlan(
                unit.unit_id,
                tuple(decisions[unit.unit_id]["served"]),
                tuple(decisions[unit.unit_id]["overtime_hours"]),
                tuple(decisions[unit.unit_id]["surge"]),
                tuple(decisions[unit.unit_id]["backlog"]),
            )
            for unit in problem.units
        )
    )
