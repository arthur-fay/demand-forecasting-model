"""M4 configuration rules, the provenance chain of the decision inputs, and the scenarios.

Configuration tests mutate a decoded copy of ``configs/optimization/smoke.json``. The
reference configuration ``atlas-m4-optimization-v1.json`` is only parsed and validated
here, never solved. Tests of the committed M2/M3 results are marked ``integration``; tamper
tests work on a copy of ``docs/results`` inside ``tmp_path``.
"""

from __future__ import annotations

import copy
import csv
import dataclasses
import hashlib
import json
import math
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from atlas.decision_inputs import (
    MARGINAL_QUANTILE_PATH,
    POINT_FORECAST,
    M2Projection,
    M3Status,
    QuantilePath,
    check_same_data,
    load_demand_percentile,
    load_m2_projection,
    load_m3_quantile_path,
    load_m3_status,
)
from atlas.errors import ConfigError
from atlas.optimization_config import (
    OptimizationConfig,
    load_optimization_config,
    parse_optimization_config,
)
from atlas.planning import PlanningProblem, ServiceUnit
from atlas.scenarios import (
    Scenario,
    build_scenarios,
    check_capacity_is_not_a_percentile,
    demand_sha256,
)
from tests.conftest import REPO_ROOT

SMOKE = REPO_ROOT / "configs" / "optimization" / "smoke.json"
REFERENCE = REPO_ROOT / "configs" / "optimization" / "atlas-m4-optimization-v1.json"

M2_PROJECTION_DIR = "docs/results/atlas-m2-rolling-origin-v1/projection"
M2_FULL_DIR = "docs/results/atlas-m2-rolling-origin-v1/full"
M2_DELAY_DIR = "docs/results/atlas-m2-rolling-origin-v1-delay1/development"
M3_STATUS = "docs/results/atlas-m3-intervals-v1/full/calibration_status.json"
M3_PROJECTION_DIR = "docs/results/atlas-m3-intervals-v1/projection"
MODEL = "atlas_linear_regression"
DECIDED_PAIR = "empirical_relative@fixed365"
GAUSSIAN_PAIR = "gaussian_one_step@rolling365"
LEGACY_PERCENTILE = 11608.75

ASSUMPTION = "Synthetic value used only by the M4 configuration tests."
SYNTHETIC_START = date(2030, 1, 1)


# --- Helpers -------------------------------------------------------------------------------


def smoke_dict() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(SMOKE.read_text(encoding="utf-8"))
    return payload


def quantity(value: float, unit: str, assumption: str = ASSUMPTION) -> dict[str, Any]:
    return {"value": value, "unit": unit, "provenance": "HYPOTHETICAL", "assumption": assumption}


def unit_of(payload: dict[str, Any]) -> dict[str, Any]:
    unit: dict[str, Any] = payload["service_units"][0]
    return unit


def rejected(payload: dict[str, Any] | str, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        parse_optimization_config(payload)


def synthetic_config(
    horizon: int,
    scenarios: list[dict[str, Any]],
    *,
    unit: dict[str, Any] | None = None,
    budget: dict[str, Any] | str | None = "keep",
) -> OptimizationConfig:
    """The smoke configuration with a new horizon, scenarios and unit parameters."""
    payload = smoke_dict()
    payload["horizon_days"] = horizon
    payload["scenarios"] = scenarios
    for optional in ("sensitivity", "frontier", "shadow_prices"):
        payload.pop(optional)
    unit_of(payload).update(unit or {})
    if budget != "keep":
        payload["discretionary_budget"] = budget
    return parse_optimization_config(payload)


def scenario(scenario_id: str, **adjustments: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "scenario_id": scenario_id,
        "description": f"Synthetic test scenario {scenario_id} for M4.",
    }
    if adjustments:
        entry["adjustments"] = adjustments
    return entry


def synthetic_dates(horizon: int) -> list[date]:
    return [SYNTHETIC_START + timedelta(days=offset) for offset in range(horizon)]


def independent_demand_sha(dates: list[date], values: list[float]) -> str:
    # The documented canonical form: one "YYYY-MM-DD,repr(float)" line per day.
    text = "".join(f"{d.isoformat()},{float(v)!r}\n" for d, v in zip(dates, values, strict=True))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def quantile_column(model_id: str, pair_id: str, column: str) -> list[float]:
    rows = read_csv_rows(REPO_ROOT / M3_PROJECTION_DIR / "projection_intervals.csv")
    chosen = [r for r in rows if r["model_id"] == model_id and r["pair_id"] == pair_id]
    chosen.sort(key=lambda r: int(r["horizon"]))
    return [float(r[column]) for r in chosen]


def committed_predictions() -> list[float]:
    rows = read_csv_rows(REPO_ROOT / M2_PROJECTION_DIR / "projections.csv")
    chosen = sorted((r for r in rows if r["model_id"] == MODEL), key=lambda r: int(r["horizon"]))
    return [float(r["prediction"]) for r in chosen]


def load_projection(root: Path, backtest_run_dir: str = M2_FULL_DIR) -> M2Projection:
    return load_m2_projection(root, M2_PROJECTION_DIR, MODEL, backtest_run_dir)


def flip_byte(path: Path, needle: bytes) -> None:
    """Change one byte inside the first occurrence of ``needle`` (a digit, 0 <-> 1 etc.)."""
    data = bytearray(path.read_bytes())
    index = data.find(needle)
    assert index >= 0, f"{needle!r} not found in {path}"
    position = index + len(needle) - 1
    data[position] = ord("0") + (data[position] - ord("0") + 1) % 10
    path.write_bytes(bytes(data))


def rewrite_json(path: Path, change: Any) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    change(payload)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@pytest.fixture
def results_copy(tmp_path: Path) -> Path:
    """A fake repository root holding a copy of the committed ``docs/results``."""
    root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "docs" / "results", root / "docs" / "results")
    return root


@pytest.fixture(scope="module")
def committed() -> tuple[M2Projection, M3Status]:
    projection = load_projection(REPO_ROOT)
    status = load_m3_status(REPO_ROOT, M3_STATUS)
    return projection, status


# --- Configuration: the smoke configuration and units --------------------------------------


def test_smoke_configuration_parses_from_mapping_and_text_identically() -> None:
    from_mapping = parse_optimization_config(smoke_dict())
    from_text = parse_optimization_config(SMOKE.read_text(encoding="utf-8"))
    assert from_mapping == from_text
    assert from_mapping.horizon_days == 10
    assert from_mapping.unit.unit_id == "citywide"
    assert [s.scenario_id for s in from_mapping.scenarios] == [
        "normal",
        "reduced_budget",
        "delayed_surge",
        "m3_marginal_q90_stress",
    ]


UNIT_FIELDS_WRONG_UNITS = [
    ("initial_backlog", "requests_per_day"),
    ("regular_hours_per_day", "staff_hours"),
    ("regular_hours_per_day", "hours"),
    ("regular_productivity", "requests_per_hour"),
    ("overtime_productivity", "requests_per_staff_hour_day"),
    ("overtime_max_hours_per_day", "staff_hours"),
    ("overtime_total_hours", "staff_hours_per_day"),
    ("surge_max_per_day", "requests"),
    ("surge_available_from_day", "day"),
    ("regular_wage", "usd"),
    ("overtime_wage", "usd_per_hour"),
    ("surge_price", "usd"),
    ("backlog_penalty", "usd_per_request"),
    ("terminal_penalty", "usd_per_request_day"),
    ("terminal_backlog_max", "requests_per_day"),
    ("backlog_trigger", "staff_hours"),
]


@pytest.mark.parametrize(("field", "wrong_unit"), UNIT_FIELDS_WRONG_UNITS)
def test_unit_parameter_in_the_wrong_unit_is_rejected_naming_the_field(
    field: str, wrong_unit: str
) -> None:
    payload = smoke_dict()
    unit_of(payload)[field]["unit"] = wrong_unit
    rejected(payload, rf"service_units\.0\.{field}\b.*unit")


def test_optional_surge_total_in_the_wrong_unit_is_rejected_and_right_unit_accepted() -> None:
    payload = smoke_dict()
    unit_of(payload)["surge_total"] = quantity(1000, "requests_per_day")
    rejected(payload, r"surge_total.*unit")
    unit_of(payload)["surge_total"] = quantity(1000, "requests")
    assert parse_optimization_config(payload).unit.surge_total is not None


@pytest.mark.parametrize("wrong_unit", ["usd_per_day", "dollars", "USD"])
def test_discretionary_budget_in_the_wrong_unit_is_rejected(wrong_unit: str) -> None:
    payload = smoke_dict()
    payload["discretionary_budget"]["unit"] = wrong_unit
    rejected(payload, r"discretionary_budget\.unit")


@pytest.mark.parametrize(
    ("adjustment", "wrong_unit"),
    [
        ("demand_multiplier", "percent"),
        ("demand_shift_per_day", "requests"),
        ("regular_hours_multiplier", "staff_hours_per_day"),
        ("productivity_multiplier", "requests_per_staff_hour"),
        ("cost_multiplier", "usd"),
        ("discretionary_budget_multiplier", "usd"),
        ("surge_available_from_day", "day"),
    ],
)
def test_scenario_adjustment_in_the_wrong_unit_is_rejected(
    adjustment: str, wrong_unit: str
) -> None:
    payload = smoke_dict()
    payload["scenarios"][0]["adjustments"] = {adjustment: quantity(1, wrong_unit)}
    rejected(payload, rf"scenarios\.0\.adjustments\.{adjustment}\.unit")


@pytest.mark.parametrize("provenance", ["VERIFIED", "DERIVED_FROM_HISTORY", "hypothetical", ""])
def test_provenance_other_than_hypothetical_is_rejected(provenance: str) -> None:
    payload = smoke_dict()
    unit_of(payload)["regular_wage"]["provenance"] = provenance
    rejected(payload, r"regular_wage\.provenance")


def test_missing_provenance_or_assumption_is_rejected() -> None:
    for key in ("provenance", "assumption", "unit", "value"):
        payload = smoke_dict()
        del unit_of(payload)["surge_price"][key]
        rejected(payload, rf"surge_price\.{key}: Field required")


def test_assumption_needs_twenty_characters() -> None:
    payload = smoke_dict()
    unit_of(payload)["initial_backlog"]["assumption"] = "x" * 19
    rejected(payload, r"initial_backlog\.assumption")
    unit_of(payload)["initial_backlog"]["assumption"] = "x" * 20
    assert parse_optimization_config(payload).unit.initial_backlog.assumption == "x" * 20


def test_sweep_and_planning_day_provenance_is_also_enforced() -> None:
    payload = smoke_dict()
    payload["sensitivity"]["sweeps"][0]["provenance"] = "VERIFIED"
    rejected(payload, r"sensitivity\.sweeps\.0\.provenance")
    payload = smoke_dict()
    unit_of(payload)["surge_available_from_day"]["provenance"] = "DERIVED_FROM_HISTORY"
    rejected(payload, r"surge_available_from_day\.provenance")


@pytest.mark.parametrize("bad", [-1.0, -1e-9, math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", ["initial_backlog", "regular_hours_per_day", "regular_wage"])
def test_negative_or_non_finite_values_are_rejected(field: str, bad: float) -> None:
    payload = smoke_dict()
    unit_of(payload)[field]["value"] = bad
    rejected(payload, rf"{field}\.value")


def test_non_finite_budget_sweep_value_and_step_are_rejected() -> None:
    payload = smoke_dict()
    payload["discretionary_budget"]["value"] = math.inf
    rejected(payload, r"discretionary_budget\.value")
    payload = smoke_dict()
    payload["sensitivity"]["sweeps"][5]["values"] = [0.0, math.nan]
    rejected(payload, r"sensitivity\.sweeps\.5\.values")
    payload = smoke_dict()
    payload["shadow_prices"]["finite_difference_steps"]["usd"] = math.inf
    rejected(payload, r"finite_difference_steps\.usd")


def test_zero_values_are_allowed_where_zero_is_meaningful() -> None:
    payload = smoke_dict()
    for field in ("initial_backlog", "regular_wage", "surge_max_per_day", "backlog_penalty"):
        unit_of(payload)[field]["value"] = 0
    payload["discretionary_budget"]["value"] = 0
    config = parse_optimization_config(payload)
    assert config.unit.initial_backlog.value == 0.0
    assert config.discretionary_budget is not None
    assert config.discretionary_budget.value == 0.0


@pytest.mark.parametrize("field", ["regular_productivity", "overtime_productivity"])
def test_zero_productivity_is_rejected(field: str) -> None:
    payload = smoke_dict()
    unit_of(payload)[field]["value"] = 0
    rejected(payload, rf"{field}\.value: Input should be greater than 0")


def test_loose_types_are_rejected() -> None:
    payload = smoke_dict()
    unit_of(payload)["initial_backlog"]["value"] = "2000"
    rejected(payload, r"initial_backlog\.value")
    payload = smoke_dict()
    unit_of(payload)["initial_backlog"]["value"] = True
    rejected(payload, r"initial_backlog\.value")
    payload = smoke_dict()
    payload["horizon_days"] = "10"
    rejected(payload, r"horizon_days")
    payload = smoke_dict()
    unit_of(payload)["surge_available_from_day"]["value"] = 1.5
    rejected(payload, r"surge_available_from_day\.value")
    payload = smoke_dict()
    payload["solver"]["presolve"] = "true"
    rejected(payload, r"solver\.presolve")


@pytest.mark.parametrize(
    "location",
    [
        (),
        ("service_units", 0),
        ("service_units", 0, "regular_wage"),
        ("forecast",),
        ("uncertainty",),
        ("scenarios", 0),
        ("solver",),
        ("sensitivity", "sweeps", 0),
        ("frontier",),
    ],
)
def test_unknown_keys_are_rejected_everywhere(location: tuple[str | int, ...]) -> None:
    payload = smoke_dict()
    target: Any = payload
    for step in location:
        target = target[step]
    target["capacity_requests_per_day"] = 11608.75
    rejected(payload, r"capacity_requests_per_day.*Extra inputs are not permitted")


def test_duplicate_json_keys_are_rejected() -> None:
    text = SMOKE.read_text(encoding="utf-8")
    duplicated = text.replace('"horizon_days": 10,', '"horizon_days": 10,\n  "horizon_days": 5,', 1)
    assert duplicated != text
    rejected(duplicated, r"duplicate key.*horizon_days")
    nested = text.replace(
        '"currency": "usd",', '"currency": "usd",\n  "solver": {"maxiter": 100},', 1
    )
    assert nested != text
    rejected(nested, r"duplicate key.*solver")


def test_invalid_json_text_is_a_config_error() -> None:
    rejected("{not json", r"not valid JSON")


def test_more_than_one_service_unit_is_rejected() -> None:
    payload = smoke_dict()
    payload["service_units"].append(copy.deepcopy(unit_of(payload)))
    rejected(payload, r"service_units")
    payload["service_units"] = []
    rejected(payload, r"service_units")


@pytest.mark.parametrize("unit_id", ["manhattan", "Citywide", "citywide_2", ""])
def test_unit_id_other_than_citywide_is_rejected(unit_id: str) -> None:
    payload = smoke_dict()
    unit_of(payload)["unit_id"] = unit_id
    rejected(payload, r"unit_id")


@pytest.mark.parametrize("horizon", [0, -1, 31, 365])
def test_horizon_outside_one_to_thirty_is_rejected(horizon: int) -> None:
    payload = smoke_dict()
    payload["horizon_days"] = horizon
    rejected(payload, r"horizon_days")


@pytest.mark.parametrize("horizon", [3, 30])
def test_horizon_within_bounds_is_accepted(horizon: int) -> None:
    # delayed_surge starts on day 4, which is allowed for horizon 3 (horizon + 1).
    payload = smoke_dict()
    payload["horizon_days"] = horizon
    assert parse_optimization_config(payload).horizon_days == horizon


def test_duplicate_scenario_ids_are_rejected() -> None:
    payload = smoke_dict()
    payload["scenarios"][1]["scenario_id"] = "normal"
    rejected(payload, r"scenario ids must be unique")


@pytest.mark.parametrize(
    ("where", "change"),
    [
        ("sensitivity.base_scenario", lambda p: p["sensitivity"].update(base_scenario="ghost")),
        ("frontier.scenarios", lambda p: p["frontier"].update(scenarios=["normal", "ghost"])),
        ("frontier.scenarios", lambda p: p["frontier"].update(scenarios=["normal", "normal"])),
        ("shadow_prices.scenarios", lambda p: p["shadow_prices"].update(scenarios=["ghost"])),
    ],
)
def test_analyses_must_name_distinct_configured_scenarios(where: str, change: Any) -> None:
    payload = smoke_dict()
    change(payload)
    rejected(payload, rf"{where} must name distinct configured scenarios")


@pytest.mark.parametrize("values", [[1.0, 1.0], [1.05, 1.0], [0.9, 1.1, 1.0]])
def test_sweep_values_must_be_strictly_increasing(values: list[float]) -> None:
    payload = smoke_dict()
    payload["sensitivity"]["sweeps"][0]["values"] = values
    rejected(payload, r"strictly increasing")


def test_sweep_needs_at_least_two_values() -> None:
    payload = smoke_dict()
    payload["sensitivity"]["sweeps"][0]["values"] = [1.0]
    rejected(payload, r"sweeps\.0\.values")


@pytest.mark.parametrize(
    ("index", "unit"),
    [(0, "usd_per_request_day"), (4, "usd_per_request_day"), (5, "ratio")],
)
def test_sweep_unit_must_match_its_parameter(index: int, unit: str) -> None:
    payload = smoke_dict()
    payload["sensitivity"]["sweeps"][index]["unit"] = unit
    rejected(payload, r"unit must be")


@pytest.mark.parametrize(
    ("index", "values", "message"),
    [
        (0, [-0.5, 1.0], "multipliers must be positive"),
        (0, [0.0, 1.0], "multipliers must be positive"),
        (4, [0.0, 1.0], "multipliers must be positive"),
        (1, [-0.1, 1.0], "must be non-negative"),
        (5, [-1.0, 1.5], "must be non-negative"),
        (2, [1.0, 10.5], "above 10"),
    ],
)
def test_sweep_value_ranges(index: int, values: list[float], message: str) -> None:
    payload = smoke_dict()
    payload["sensitivity"]["sweeps"][index]["values"] = values
    rejected(payload, message)


def test_zero_is_a_valid_start_for_budget_and_penalty_sweeps() -> None:
    payload = smoke_dict()
    payload["sensitivity"]["sweeps"][1]["values"] = [0.0, 1.0]
    payload["sensitivity"]["sweeps"][5]["values"] = [0.0, 1.0]
    config = parse_optimization_config(payload)
    assert config.sensitivity is not None
    assert config.sensitivity.sweeps[1].values == (0.0, 1.0)


def test_sweep_parameter_may_appear_only_once() -> None:
    payload = smoke_dict()
    payload["sensitivity"]["sweeps"].append(copy.deepcopy(payload["sensitivity"]["sweeps"][0]))
    rejected(payload, r"each sweep parameter may appear once")


@pytest.mark.parametrize("value", [-0.25, 0.0, 10.5])
@pytest.mark.parametrize(
    "multiplier",
    [
        "demand_multiplier",
        "regular_hours_multiplier",
        "productivity_multiplier",
        "cost_multiplier",
        "discretionary_budget_multiplier",
    ],
)
def test_scenario_multipliers_must_lie_in_zero_exclusive_to_ten(
    multiplier: str, value: float
) -> None:
    payload = smoke_dict()
    payload["scenarios"][0]["adjustments"] = {multiplier: quantity(value, "ratio")}
    rejected(payload, rf"adjustments\.{multiplier}\.value")


def test_negative_demand_shift_is_rejected() -> None:
    payload = smoke_dict()
    payload["scenarios"][0]["adjustments"] = {
        "demand_shift_per_day": quantity(-100, "requests_per_day")
    }
    rejected(payload, r"demand_shift_per_day\.value")


def test_surge_available_from_day_may_be_horizon_plus_one_but_not_beyond() -> None:
    # horizon 10: day 11 means "never within the horizon", day 12 is meaningless.
    payload = smoke_dict()
    unit_of(payload)["surge_available_from_day"]["value"] = 11
    assert parse_optimization_config(payload).unit.surge_available_from_day.value == 11
    unit_of(payload)["surge_available_from_day"]["value"] = 12
    rejected(payload, r"surge_available_from_day must be within 1\.\.horizon_days \+ 1")
    unit_of(payload)["surge_available_from_day"]["value"] = 0
    rejected(payload, r"surge_available_from_day\.value")


def test_scenario_surge_day_beyond_horizon_plus_one_is_rejected() -> None:
    payload = smoke_dict()
    payload["scenarios"][2]["adjustments"]["surge_available_from_day"]["value"] = 12
    rejected(payload, r"scenario delayed_surge: surge_available_from_day must be within")
    payload["scenarios"][2]["adjustments"]["surge_available_from_day"]["value"] = 11
    assert parse_optimization_config(payload).scenario("delayed_surge") is not None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("primal_feasibility_tolerance", 1e-11),
        ("dual_feasibility_tolerance", 1e-11),
        ("primal_feasibility_tolerance", 1e-4),
        ("maxiter", 99),
        ("method", "highs-ipm"),
        ("presolve", 1),
    ],
)
def test_solver_settings_outside_supported_ranges_are_rejected(key: str, value: Any) -> None:
    payload = smoke_dict()
    payload["solver"][key] = value
    rejected(payload, rf"solver\.{key}")


def test_solver_tolerance_at_the_highs_minimum_is_accepted() -> None:
    payload = smoke_dict()
    payload["solver"]["primal_feasibility_tolerance"] = 1e-10
    payload["solver"]["dual_feasibility_tolerance"] = 1e-10
    assert parse_optimization_config(payload).solver.primal_feasibility_tolerance == 1e-10


def test_marginal_quantile_basis_requires_the_stress_interpretation() -> None:
    payload = smoke_dict()
    del payload["scenarios"][3]["demand_basis"]["interpretation"]
    rejected(payload, r"interpretation: Field required")
    payload["scenarios"][3]["demand_basis"]["interpretation"] = "probability_of_demand"
    rejected(payload, r"interpretation")


@pytest.mark.parametrize("q", [0.5, 0.25, 0.0, 1.0, 1.2])
def test_marginal_quantile_must_lie_strictly_between_half_and_one(q: float) -> None:
    payload = smoke_dict()
    payload["scenarios"][3]["demand_basis"]["quantile"] = q
    rejected(payload, r"demand_basis\.m3_marginal_quantile\.quantile")


def test_unknown_demand_basis_kind_is_rejected() -> None:
    payload = smoke_dict()
    payload["scenarios"][3]["demand_basis"]["kind"] = "joint_quantile"
    rejected(payload, r"demand_basis")


@pytest.mark.parametrize(
    ("section", "key"),
    [
        ("forecast", "projections_sha256"),
        ("forecast", "m2_config_fingerprint"),
        ("uncertainty", "calibration_status_sha256"),
    ],
)
def test_pinned_hashes_must_be_lowercase_sha256(section: str, key: str) -> None:
    payload = smoke_dict()
    payload[section][key] = payload[section][key].upper()
    rejected(payload, rf"{section}\.{key}")
    payload[section][key] = "abc"
    rejected(payload, rf"{section}\.{key}")


def test_discretionary_budget_may_be_null() -> None:
    payload = smoke_dict()
    payload["discretionary_budget"] = None
    assert parse_optimization_config(payload).discretionary_budget is None


def test_smoke_hypothetical_parameters_are_counted_by_hand() -> None:
    # 16 unit quantities minus surge_total (null) = 15, the budget = 1, and one adjustment
    # each in reduced_budget and delayed_surge = 2: 18 rows.
    config = parse_optimization_config(smoke_dict())
    rows = config.hypothetical_parameters()
    assert len(rows) == 18
    scopes = {(r["scope"], r["parameter"]) for r in rows}
    assert ("budget", "discretionary_budget") in scopes
    assert ("scenario:reduced_budget", "discretionary_budget_multiplier") in scopes
    assert ("scenario:delayed_surge", "surge_available_from_day") in scopes
    assert ("unit:citywide", "surge_total") not in scopes


# --- Configuration: the reference configuration (parsed, never solved) ---------------------


def test_reference_configuration_loads_and_every_parameter_is_labelled() -> None:
    config = load_optimization_config(REFERENCE)
    assert config.unit.unit_id == "citywide"
    assert 1 <= config.horizon_days <= 30
    rows = config.hypothetical_parameters()
    assert rows
    for row in rows:
        assert row["provenance"] == "HYPOTHETICAL", row
        assert isinstance(row["unit"], str), row
        assert row["unit"], row
        assert isinstance(row["assumption"], str), row
        assert len(row["assumption"]) >= 20, row
        assert math.isfinite(row["value"]), row
        assert row["value"] >= 0, row
    unit_rows = {r["parameter"] for r in rows if r["scope"] == "unit:citywide"}
    expected_unit = {
        name
        for name, value in config.unit
        if name not in ("unit_id", "description") and value is not None
    }
    assert unit_rows == expected_unit


def _reversed_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _reversed_keys(value[k]) for k in reversed(list(value))}
    if isinstance(value, list):
        return [_reversed_keys(v) for v in value]
    return value


@pytest.mark.parametrize("path", [SMOKE, REFERENCE])
def test_fingerprint_is_stable_under_key_order_and_formatting(path: Path) -> None:
    original = load_optimization_config(path)
    decoded = json.loads(path.read_text(encoding="utf-8"))
    reformatted = json.dumps(_reversed_keys(decoded), indent=7, separators=(" ,", " :  "))
    again = parse_optimization_config(reformatted)
    assert again.fingerprint() == original.fingerprint()
    assert len(original.fingerprint()) == 64
    assert again.canonical_json() == original.canonical_json()


def test_fingerprint_treats_integer_and_float_spelling_alike_but_tracks_values() -> None:
    payload = smoke_dict()
    base = parse_optimization_config(payload).fingerprint()
    unit_of(payload)["initial_backlog"]["value"] = 2000.0  # was 2000
    assert parse_optimization_config(payload).fingerprint() == base
    unit_of(payload)["initial_backlog"]["value"] = 2001
    assert parse_optimization_config(payload).fingerprint() != base
    assert load_optimization_config(REFERENCE).fingerprint() != base


def test_missing_configuration_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read configuration"):
        load_optimization_config(tmp_path / "absent.json")


@pytest.mark.integration
@pytest.mark.parametrize("path", [SMOKE, REFERENCE])
def test_pinned_hashes_of_the_configurations_match_the_committed_results(path: Path) -> None:
    config = load_optimization_config(path)

    def sha(relative: str) -> str:
        return hashlib.sha256((REPO_ROOT / relative).read_bytes()).hexdigest()

    forecast, uncertainty = config.forecast, config.uncertainty
    assert forecast.projections_sha256 == sha(f"{forecast.run_dir}/projections.csv")
    assert uncertainty.calibration_status_sha256 == sha(uncertainty.calibration_status)
    intervals = f"{uncertainty.projection_run_dir}/projection_intervals.csv"
    exceedance = f"{uncertainty.projection_run_dir}/projection_exceedance.csv"
    assert uncertainty.projection_intervals_sha256 == sha(intervals)
    assert uncertainty.projection_exceedance_sha256 == sha(exceedance)
    manifest = json.loads((REPO_ROOT / forecast.run_dir / "manifest.json").read_text("utf-8"))
    assert forecast.m2_config_fingerprint == manifest["config_fingerprint"]
    assert forecast.model_id == manifest["recommended_model"] == MODEL


# --- Decision inputs on the committed results ----------------------------------------------


@pytest.mark.integration
def test_m2_projection_is_thirty_consecutive_days_with_the_committed_predictions(
    committed: tuple[M2Projection, M3Status],
) -> None:
    projection, _ = committed
    assert projection.origin == date(2026, 6, 30)
    assert projection.model_id == MODEL
    assert projection.target_dates == tuple(date(2026, 7, 1) + timedelta(days=i) for i in range(30))
    assert list(projection.predictions) == committed_predictions()
    assert projection.predictions[0] == 11123.683380141376
    assert "not validated" in projection.label
    dates, values = projection.first(10)
    assert dates == projection.target_dates[:10]
    assert values == projection.predictions[:10]
    for horizon in (0, 31):
        with pytest.raises(ConfigError, match="30 days"):
            projection.first(horizon)


@pytest.mark.integration
def test_m2_accuracy_is_the_confirmation_days_1_30_row(
    committed: tuple[M2Projection, M3Status],
) -> None:
    accuracy = committed[0].accuracy
    assert accuracy.period == "confirmation"
    assert accuracy.bucket == "days_1_30"
    assert accuracy.mae == pytest.approx(984.9, abs=0.05)
    assert accuracy.bias == pytest.approx(-255.9, abs=0.05)
    assert accuracy.n_predictions > 0
    record = accuracy.to_dict()
    assert record["mae_requests_per_day"] == accuracy.mae
    assert "negative = under-forecast" in record["bias_sign"]


@pytest.mark.integration
def test_m2_projection_rejects_a_model_that_was_not_recommended() -> None:
    with pytest.raises(ConfigError, match="not the model the M2 confirmation run recommended"):
        load_m2_projection(REPO_ROOT, M2_PROJECTION_DIR, "same_weekday_median_5w", M2_FULL_DIR)


@pytest.mark.integration
def test_untampered_copy_of_the_results_loads(results_copy: Path) -> None:
    # Control for the tamper tests below: the copy itself is accepted.
    projection = load_projection(results_copy)
    status = load_m3_status(results_copy, M3_STATUS)
    assert projection.predictions == load_projection(REPO_ROOT).predictions
    assert status.interval_status == "failed_calibration"


@pytest.mark.integration
def test_altering_one_byte_of_the_projection_is_detected(results_copy: Path) -> None:
    path = results_copy / M2_PROJECTION_DIR / "projections.csv"
    flip_byte(path, b"11123.683380141376")
    with pytest.raises(ConfigError, match="SHA-256 differs"):
        load_projection(results_copy)


@pytest.mark.integration
def test_altering_the_m2_metrics_is_detected(results_copy: Path) -> None:
    path = results_copy / M2_FULL_DIR / "metrics.json"
    flip_byte(path, b"984.918374306823")
    with pytest.raises(ConfigError, match=r"metrics\.json is not the file its run recorded"):
        load_projection(results_copy)


@pytest.mark.integration
def test_backtest_run_with_a_different_run_id_is_rejected(results_copy: Path) -> None:
    rewrite_json(
        results_copy / M2_FULL_DIR / "manifest.json",
        lambda m: m.update(run_id="atlas-m2-some-other-run"),
    )
    with pytest.raises(ConfigError, match="is not the run"):
        load_projection(results_copy)


@pytest.mark.integration
def test_backtest_run_other_than_the_recommending_run_is_rejected() -> None:
    with pytest.raises(ConfigError, match="is not the run"):
        load_projection(REPO_ROOT, backtest_run_dir=M2_DELAY_DIR)


@pytest.mark.integration
def test_m3_status_is_failed_calibration_and_not_decision_grade(
    committed: tuple[M2Projection, M3Status],
) -> None:
    _, status = committed
    assert status.interval_status == "failed_calibration"
    assert status.decision_grade is False
    assert status.selected_pair == DECIDED_PAIR
    assert status.primary_model == MODEL
    assert status.quantile_calibration["0.9"] == pytest.approx(0.8788, abs=5e-5)
    assert status.to_dict()["decision_grade"] is False


@pytest.mark.integration
def test_edited_calibration_status_is_rejected(results_copy: Path) -> None:
    path = results_copy / M3_STATUS
    rewrite_json(path, lambda r: r["decision_status"].update(interval_status="decision_grade"))
    with pytest.raises(ConfigError, match="cannot be used"):
        load_m3_status(results_copy, M3_STATUS)


@pytest.mark.integration
def test_edited_calibration_status_cannot_be_rehashed_into_agreement(results_copy: Path) -> None:
    path = results_copy / M3_STATUS
    rewrite_json(path, lambda r: r["decision_status"].update(decision_grade=True))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    rewrite_json(
        path.parent / "manifest.json",
        lambda m: m["outputs"].update({"calibration_status.json": digest}),
    )
    with pytest.raises(ConfigError, match="cannot be used"):
        load_m3_status(results_copy, M3_STATUS)


@pytest.mark.integration
def test_quantile_path_uses_the_primary_model_and_the_decided_pair(
    committed: tuple[M2Projection, M3Status],
) -> None:
    projection, status = committed
    path = load_m3_quantile_path(REPO_ROOT, M3_PROJECTION_DIR, status, projection, 0.9)
    decided = quantile_column(MODEL, DECIDED_PAIR, "q_0.9")
    gaussian = quantile_column(MODEL, GAUSSIAN_PAIR, "q_0.9")
    other_model = quantile_column("same_weekday_median_5w", DECIDED_PAIR, "q_0.9")
    assert len(decided) == len(gaussian) == 30
    assert list(path.values) == decided
    assert list(path.values) != gaussian
    assert all(a != b for a, b in zip(path.values, gaussian, strict=True))
    assert list(path.values) != other_model
    assert path.target_dates == projection.target_dates
    assert path.provenance["pair_id"] == DECIDED_PAIR
    assert path.provenance["calibration_status"] == "failed_calibration"


@pytest.mark.integration
def test_quantile_path_rejects_a_status_from_a_different_file(
    committed: tuple[M2Projection, M3Status],
) -> None:
    projection, status = committed
    other = dataclasses.replace(status, provenance={**status.provenance, "sha256": "0" * 64})
    with pytest.raises(ConfigError, match="different calibration status file"):
        load_m3_quantile_path(REPO_ROOT, M3_PROJECTION_DIR, other, projection, 0.9)


@pytest.mark.integration
def test_quantile_path_rejects_a_projection_manifest_naming_another_status(
    results_copy: Path,
) -> None:
    rewrite_json(
        results_copy / M3_PROJECTION_DIR / "manifest.json",
        lambda m: m["status_source"].update(calibration_status_sha256="f" * 64),
    )
    projection = load_projection(results_copy)
    status = load_m3_status(results_copy, M3_STATUS)
    with pytest.raises(ConfigError, match="different calibration status file"):
        load_m3_quantile_path(results_copy, M3_PROJECTION_DIR, status, projection, 0.9)


@pytest.mark.integration
def test_quantile_path_rejects_another_interval_method(
    committed: tuple[M2Projection, M3Status],
) -> None:
    projection, status = committed
    gaussian = dataclasses.replace(status, selected_pair=GAUSSIAN_PAIR)
    with pytest.raises(ConfigError, match="not the sealed one"):
        load_m3_quantile_path(REPO_ROOT, M3_PROJECTION_DIR, gaussian, projection, 0.9)


@pytest.mark.integration
def test_quantile_path_rejects_a_missing_quantile_column(
    committed: tuple[M2Projection, M3Status],
) -> None:
    projection, status = committed
    with pytest.raises(ConfigError, match=r"no quantile 0\.95 \(column q_0\.95\)"):
        load_m3_quantile_path(REPO_ROOT, M3_PROJECTION_DIR, status, projection, 0.95)


@pytest.mark.integration
def test_quantile_path_rejects_point_predictions_that_differ_from_m2(
    committed: tuple[M2Projection, M3Status],
) -> None:
    projection, status = committed
    shifted = (projection.predictions[0] + 1.0, *projection.predictions[1:])
    other = dataclasses.replace(projection, predictions=shifted)
    with pytest.raises(ConfigError, match="point predictions differ"):
        load_m3_quantile_path(REPO_ROOT, M3_PROJECTION_DIR, status, other, 0.9)


@pytest.mark.integration
def test_quantile_path_rejects_tampered_intervals(results_copy: Path) -> None:
    flip_byte(results_copy / M3_PROJECTION_DIR / "projection_intervals.csv", b"12209.913652741079")
    projection = load_projection(results_copy)
    status = load_m3_status(results_copy, M3_STATUS)
    with pytest.raises(ConfigError, match="SHA-256 differs"):
        load_m3_quantile_path(results_copy, M3_PROJECTION_DIR, status, projection, 0.9)


@pytest.mark.integration
def test_demand_percentile_is_the_legacy_planning_capacity(
    committed: tuple[M2Projection, M3Status],
) -> None:
    _, status = committed
    assert load_demand_percentile(REPO_ROOT, M3_PROJECTION_DIR, status) == LEGACY_PERCENTILE


@pytest.mark.integration
def test_same_data_check_accepts_the_committed_inputs(
    committed: tuple[M2Projection, M3Status],
) -> None:
    projection, status = committed
    check_same_data(projection, status)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("data_fingerprint", "different data"),
        ("backtest_config_fingerprint", "different M2 configuration"),
        ("recommendation_sha256", "different M2 recommendation"),
        ("primary_model", "is not the projected model"),
    ],
)
def test_same_data_check_rejects_mismatches(
    committed: tuple[M2Projection, M3Status], field: str, message: str
) -> None:
    projection, status = committed
    other = dataclasses.replace(status, **{field: "0" * 64})
    with pytest.raises(ConfigError, match=message):
        check_same_data(projection, other)


@pytest.mark.integration
@pytest.mark.parametrize("escape", ["../x", "../../etc", "/etc"])
def test_paths_escaping_the_repository_are_rejected(
    committed: tuple[M2Projection, M3Status], escape: str
) -> None:
    projection, status = committed
    inside = "must lie inside the repository"
    with pytest.raises(ConfigError, match=inside):
        load_m2_projection(REPO_ROOT, escape, MODEL, M2_FULL_DIR)
    with pytest.raises(ConfigError, match=inside):
        load_m2_projection(REPO_ROOT, M2_PROJECTION_DIR, MODEL, escape)
    with pytest.raises(ConfigError, match=inside):
        load_m3_status(REPO_ROOT, escape)
    with pytest.raises(ConfigError, match=inside):
        load_m3_quantile_path(REPO_ROOT, escape, status, projection, 0.9)
    with pytest.raises(ConfigError, match=inside):
        load_demand_percentile(REPO_ROOT, escape, status)


# --- Scenarios: smoke configuration on the committed inputs --------------------------------


@pytest.fixture(scope="module")
def smoke_scenarios(
    committed: tuple[M2Projection, M3Status],
) -> tuple[tuple[Scenario, ...], list[float], list[float]]:
    projection, status = committed
    config = load_optimization_config(SMOKE)
    dates, point = projection.first(config.horizon_days)
    path = load_m3_quantile_path(REPO_ROOT, M3_PROJECTION_DIR, status, projection, 0.9)
    percentile = load_demand_percentile(REPO_ROOT, M3_PROJECTION_DIR, status)
    built = build_scenarios(config, dates, point, {0.9: path}, percentile)
    return built, list(point), quantile_column(MODEL, DECIDED_PAIR, "q_0.9")[:10]


@pytest.mark.integration
def test_smoke_scenarios_on_the_committed_inputs(
    smoke_scenarios: tuple[tuple[Scenario, ...], list[float], list[float]],
) -> None:
    built, point, q90 = smoke_scenarios
    by_id = {s.scenario_id: s for s in built}
    assert list(by_id) == ["normal", "reduced_budget", "delayed_surge", "m3_marginal_q90_stress"]
    dates = [date(2026, 7, 1) + timedelta(days=i) for i in range(10)]
    for item in built:
        assert item.problem.start == date(2026, 7, 1)
        assert item.problem.horizon == 10
        # 38 USD/h x 4300 h/day x 10 days = 1,634,000 USD committed regular cost.
        assert item.problem.regular_cost == pytest.approx(1_634_000.0)
        # 4300 h/day x 2.4 requests/h = 10,320 requests/day.
        assert item.regular_capacity_mean() == pytest.approx(10_320.0)
        assert item.triggers == {"citywide": 3000.0}
    normal = by_id["normal"]
    assert list(normal.problem.units[0].demand) == point
    assert normal.demand_basis == POINT_FORECAST
    assert normal.quantile is None
    assert normal.demand_sha256 == independent_demand_sha(dates, point)
    assert normal.problem.discretionary_budget == 200_000.0
    # 200,000 USD x 0.25 = 50,000 USD.
    assert by_id["reduced_budget"].problem.discretionary_budget == pytest.approx(50_000.0)
    assert by_id["delayed_surge"].problem.units[0].surge_max == (0.0,) * 3 + (500.0,) * 7
    assert normal.problem.units[0].surge_max == (500.0,) * 10
    stress = by_id["m3_marginal_q90_stress"]
    assert stress.demand_basis == MARGINAL_QUANTILE_PATH
    assert stress.quantile == 0.9
    assert list(stress.problem.units[0].demand) == q90
    expected_pct = 100 * (math.fsum(q90) / math.fsum(point) - 1)
    assert stress.to_dict()["demand_total_vs_point_forecast_pct"] == pytest.approx(expected_pct)
    assert stress.to_dict()["demand_total_vs_point_forecast_pct"] > 0
    assert stress.demand_sha256 != normal.demand_sha256


# --- Scenarios: synthetic inputs -----------------------------------------------------------

POINT3 = [1000.0, 2000.0, 3000.0]
FAR_PERCENTILE = 50_000.0


def build(
    config: OptimizationConfig,
    point: list[float] | None = None,
    quantiles: dict[float, QuantilePath] | None = None,
    percentile: float = FAR_PERCENTILE,
) -> dict[str, Scenario]:
    values = POINT3 if point is None else point
    dates = synthetic_dates(len(values))
    built = build_scenarios(config, dates, values, quantiles or {}, percentile)
    return {s.scenario_id: s for s in built}


def test_demand_multiplier_is_applied_before_the_shift() -> None:
    config = synthetic_config(
        3,
        [
            scenario(
                "both",
                demand_multiplier=quantity(1.1, "ratio"),
                demand_shift_per_day=quantity(100, "requests_per_day"),
            )
        ],
    )
    demand = build(config)["both"].problem.units[0].demand
    # 1000 x 1.1 + 100 = 1200 (not (1000 + 100) x 1.1 = 1210); 2300; 3400.
    assert demand == pytest.approx((1200.0, 2300.0, 3400.0))


def test_regular_hours_multiplier_moves_regular_cost_but_not_the_budget() -> None:
    config = synthetic_config(
        3, [scenario("base"), scenario("half", regular_hours_multiplier=quantity(0.5, "ratio"))]
    )
    built = build(config)
    base, half = built["base"].problem, built["half"].problem
    assert half.units[0].regular_hours == (2150.0,) * 3  # 4300 x 0.5
    # 38 USD/h x 4300 h x 3 days = 490,200; halved: 245,100.
    assert base.regular_cost == pytest.approx(490_200.0)
    assert half.regular_cost == pytest.approx(245_100.0)
    assert half.discretionary_budget == base.discretionary_budget == 200_000.0
    assert built["half"].regular_capacity_mean() == pytest.approx(5160.0)  # 2150 x 2.4


def test_cost_multiplier_scales_the_three_cost_rates_only() -> None:
    config = synthetic_config(3, [scenario("dear", cost_multiplier=quantity(1.5, "ratio"))])
    unit = build(config)["dear"].problem.units[0]
    assert unit.regular_wage == pytest.approx(57.0)  # 38 x 1.5
    assert unit.overtime_wage == pytest.approx(85.5)  # 57 x 1.5
    assert unit.surge_price == pytest.approx(42.0)  # 28 x 1.5
    assert unit.backlog_penalty == 1.5
    assert unit.terminal_penalty == 0.0
    assert unit.regular_productivity == 2.4


def test_productivity_multiplier_scales_both_productivities() -> None:
    config = synthetic_config(3, [scenario("slow", productivity_multiplier=quantity(0.5, "ratio"))])
    unit = build(config)["slow"].problem.units[0]
    assert unit.regular_productivity == pytest.approx(1.2)  # 2.4 x 0.5
    assert unit.overtime_productivity == pytest.approx(1.1)  # 2.2 x 0.5
    assert unit.regular_hours == (4300.0,) * 3
    assert unit.regular_wage == 38.0


def test_budget_multiplier_and_a_missing_budget() -> None:
    adjusted = scenario("cut", discretionary_budget_multiplier=quantity(0.25, "ratio"))
    config = synthetic_config(3, [adjusted])
    assert build(config)["cut"].problem.discretionary_budget == pytest.approx(50_000.0)
    unlimited = synthetic_config(3, [adjusted], budget=None)
    assert build(unlimited)["cut"].problem.discretionary_budget is None


def test_unit_parameters_are_copied_without_adjustment() -> None:
    config = synthetic_config(3, [scenario("base")])
    unit = build(config)["base"].problem.units[0]
    assert unit.initial_backlog == 2000.0
    assert unit.overtime_max_hours == (500.0,) * 3
    assert unit.overtime_total_hours == 3000.0
    assert unit.surge_total is None
    assert unit.terminal_backlog_max == 3000.0
    assert unit.demand == tuple(POINT3)


@settings(max_examples=40, deadline=None)
@given(
    horizon=st.integers(min_value=1, max_value=5),
    data=st.data(),
)
def test_surge_is_zero_before_the_one_based_start_day(horizon: int, data: st.DataObject) -> None:
    start = data.draw(st.integers(min_value=1, max_value=horizon + 1))
    from_unit = data.draw(st.booleans())
    day = {
        "value": start,
        "unit": "planning_day",
        "provenance": "HYPOTHETICAL",
        "assumption": ASSUMPTION,
    }
    if from_unit:
        config = synthetic_config(horizon, [scenario("s")], unit={"surge_available_from_day": day})
    else:
        config = synthetic_config(horizon, [scenario("s", surge_available_from_day=day)])
    point = [1000.0] * horizon
    surge = build(config, point)["s"].problem.units[0].surge_max
    # Planning day k (1-based) is index k - 1: the first start - 1 days have no surge.
    assert surge == (0.0,) * (start - 1) + (500.0,) * (horizon - start + 1)


def test_scenario_surge_day_overrides_the_unit_start() -> None:
    later = {
        "value": 3,
        "unit": "planning_day",
        "provenance": "HYPOTHETICAL",
        "assumption": ASSUMPTION,
    }
    earlier = dict(later, value=2)
    # Unit: from day 3; scenario: from day 2 -> only planning day 1 (index 0) has no surge.
    config = synthetic_config(
        4,
        [scenario("s", surge_available_from_day=earlier)],
        unit={"surge_available_from_day": later},
    )
    assert build(config, [1.0] * 4)["s"].problem.units[0].surge_max == (0.0, 500.0, 500.0, 500.0)


@settings(max_examples=40, deadline=None)
@given(
    demand=st.lists(
        st.floats(min_value=0, max_value=20_000, allow_nan=False), min_size=1, max_size=5
    ),
    multiplier=st.sampled_from([0.5, 0.9, 1.0, 1.25, 2.0]),
    shift=st.sampled_from([0.0, 1.0, 250.0, 1000.0]),
)
def test_adjusted_demand_and_its_hash_follow_the_documented_formula(
    demand: list[float], multiplier: float, shift: float
) -> None:
    config = synthetic_config(
        len(demand),
        [
            scenario(
                "s",
                demand_multiplier=quantity(multiplier, "ratio"),
                demand_shift_per_day=quantity(shift, "requests_per_day"),
            )
        ],
    )
    built = build(config, demand)["s"]
    expected = [d * multiplier + shift for d in demand]
    assert list(built.problem.units[0].demand) == expected
    assert built.demand_sha256 == independent_demand_sha(synthetic_dates(len(demand)), expected)
    assert built.demand_total == pytest.approx(math.fsum(expected))


def test_demand_sha256_is_deterministic_and_tracks_demand_and_dates() -> None:
    dates = synthetic_dates(3)
    first = demand_sha256(dates, POINT3)
    assert first == demand_sha256(list(dates), list(POINT3))
    assert first == independent_demand_sha(dates, POINT3)
    # Integer and float spellings of one value hash alike (float repr is used).
    assert demand_sha256(dates, [1000, 2000, 3000]) == first
    assert demand_sha256(dates, [1000.0, 2000.0, 3000.0000001]) != first
    shifted = [d + timedelta(days=1) for d in dates]
    assert demand_sha256(shifted, POINT3) != first
    config = synthetic_config(3, [scenario("a"), scenario("b")])
    built = build(config)
    assert built["a"].demand_sha256 == built["b"].demand_sha256 == first
    assert build(config, [1000.0, 2000.0, 3001.0])["a"].demand_sha256 != first


def test_scenario_to_dict_reports_the_adjusted_problem() -> None:
    config = synthetic_config(
        3,
        [
            scenario(
                "s",
                demand_multiplier=quantity(1.2, "ratio"),
                regular_hours_multiplier=quantity(0.5, "ratio"),
                discretionary_budget_multiplier=quantity(2.0, "ratio"),
            )
        ],
    )
    record = build(config)["s"].to_dict()
    assert set(record) == {
        "scenario_id",
        "description",
        "demand_basis",
        "quantile",
        "adjustments",
        "demand_sha256",
        "demand_total_requests",
        "demand_total_vs_point_forecast_pct",
        "regular_capacity_mean_requests_per_day",
        "discretionary_budget_usd",
    }
    assert record["scenario_id"] == "s"
    assert record["demand_basis"] == POINT_FORECAST
    assert record["quantile"] is None
    # (1000 + 2000 + 3000) x 1.2 = 7200 requests; +20 % against 6000.
    assert record["demand_total_requests"] == pytest.approx(7200.0)
    assert record["demand_total_vs_point_forecast_pct"] == pytest.approx(20.0)
    # 4300 x 0.5 h/day x 2.4 requests/h = 5160 requests/day.
    assert record["regular_capacity_mean_requests_per_day"] == pytest.approx(5160.0)
    assert record["discretionary_budget_usd"] == pytest.approx(400_000.0)
    assert set(record["adjustments"]) == {
        "demand_multiplier",
        "regular_hours_multiplier",
        "discretionary_budget_multiplier",
    }
    assert record["adjustments"]["demand_multiplier"] == quantity(1.2, "ratio")
    json.dumps(record)  # serialisable as it stands


def stress_scenario(q: float = 0.9, **adjustments: dict[str, Any]) -> dict[str, Any]:
    entry = scenario("stress", **adjustments)
    entry["demand_basis"] = {
        "kind": "m3_marginal_quantile",
        "quantile": q,
        "interpretation": "hypothetical_stress_not_a_probability",
    }
    return entry


def quantile_path(values: list[float], dates: list[date] | None = None) -> QuantilePath:
    days = synthetic_dates(len(values)) if dates is None else dates
    return QuantilePath(quantile=0.9, target_dates=tuple(days), values=tuple(values), provenance={})


def test_quantile_scenario_uses_the_stress_path_with_adjustments_on_top() -> None:
    config = synthetic_config(
        3, [scenario("normal"), stress_scenario(demand_multiplier=quantity(2.0, "ratio"))]
    )
    longer = quantile_path([1500.0, 2500.0, 3500.0, 9999.0], synthetic_dates(4))
    built = build(config, quantiles={0.9: longer})
    stress = built["stress"]
    assert stress.demand_basis == MARGINAL_QUANTILE_PATH
    assert stress.quantile == 0.9
    # The first three days of the path, each x 2.
    assert stress.problem.units[0].demand == (3000.0, 5000.0, 7000.0)
    # 15,000 / 6,000 - 1 = +150 % against the point forecast.
    assert stress.to_dict()["demand_total_vs_point_forecast_pct"] == pytest.approx(150.0)
    assert built["normal"].problem.units[0].demand == tuple(POINT3)


def test_quantile_scenario_without_a_loaded_path_is_rejected() -> None:
    config = synthetic_config(3, [stress_scenario()])
    with pytest.raises(ConfigError, match="no quantile path loaded"):
        build(config)
    other_level = QuantilePath(0.95, tuple(synthetic_dates(3)), (1.0, 2.0, 3.0), {})
    with pytest.raises(ConfigError, match="no quantile path loaded"):
        build(config, quantiles={0.95: other_level})


def test_quantile_scenario_with_other_dates_is_rejected() -> None:
    config = synthetic_config(3, [stress_scenario()])
    shifted = [d + timedelta(days=1) for d in synthetic_dates(3)]
    with pytest.raises(ConfigError, match="quantile dates differ"):
        build(config, quantiles={0.9: quantile_path([1.0, 2.0, 3.0], shifted)})


def test_demand_path_must_cover_the_horizon() -> None:
    config = synthetic_config(3, [scenario("s")])
    with pytest.raises(ConfigError, match="does not cover the planning horizon"):
        build_scenarios(config, synthetic_dates(3), [1.0, 2.0], {}, FAR_PERCENTILE)
    with pytest.raises(ConfigError, match="does not cover the planning horizon"):
        build_scenarios(config, synthetic_dates(2), POINT3, {}, FAR_PERCENTILE)


# --- The demand-percentile guard -----------------------------------------------------------


def hours_for(capacity: float) -> dict[str, Any]:
    # regular productivity 2.0 requests/h: hours = capacity / 2.
    return {
        "regular_productivity": quantity(2.0, "requests_per_staff_hour"),
        "regular_hours_per_day": quantity(capacity / 2, "staff_hours_per_day"),
    }


def test_capacity_equal_to_the_legacy_percentile_is_refused() -> None:
    # 5804.5 h x 2.0 = 11,609 requests/day, 0.25 from 11,608.75 (limit 116.09).
    config = synthetic_config(3, [scenario("s")], unit=hours_for(11_609.0))
    with pytest.raises(ConfigError, match=r"within 1 % of the trailing 180-day 75th percentile"):
        build(config, percentile=LEGACY_PERCENTILE)


@pytest.mark.parametrize("factor", [0.98, 1.02, 0.5, 2.0])
def test_capacity_two_percent_or_more_from_the_percentile_is_accepted(factor: float) -> None:
    capacity = LEGACY_PERCENTILE * factor
    config = synthetic_config(3, [scenario("s")], unit=hours_for(capacity))
    built = build(config, percentile=LEGACY_PERCENTILE)["s"]
    assert built.regular_capacity_mean() == pytest.approx(capacity)


def test_percentile_guard_boundary_is_inclusive_at_one_percent() -> None:
    # Percentile 10,000: |10,100 - 10,000| = 100 = 1 % (refused); 10,101 is 1.01 % (accepted).
    refused = synthetic_config(3, [scenario("s")], unit=hours_for(10_100.0))
    with pytest.raises(ConfigError, match="within 1 %"):
        build(refused, percentile=10_000.0)
    accepted = synthetic_config(3, [scenario("s")], unit=hours_for(10_101.0))
    assert build(accepted, percentile=10_000.0)["s"].regular_capacity_mean() == 10_101.0
    below = synthetic_config(3, [scenario("s")], unit=hours_for(9_900.0))
    with pytest.raises(ConfigError, match="within 1 %"):
        build(below, percentile=10_000.0)


def test_percentile_guard_applies_after_scenario_adjustments() -> None:
    # Base 10,320 requests/day is 11.1 % below 11,608.75; x 1.125 productivity = 11,610.
    config = synthetic_config(
        3, [scenario("normal"), scenario("fast", productivity_multiplier=quantity(1.125, "ratio"))]
    )
    with pytest.raises(ConfigError, match="scenario fast: regular capacity"):
        build(config, percentile=LEGACY_PERCENTILE)
    hours = synthetic_config(
        3, [scenario("more", regular_hours_multiplier=quantity(1.125, "ratio"))]
    )
    with pytest.raises(ConfigError, match="scenario more"):
        build(hours, percentile=LEGACY_PERCENTILE)


def _direct_scenario(regular_hours: tuple[float, ...]) -> Scenario:
    horizon = len(regular_hours)
    unit = ServiceUnit(
        unit_id="citywide",
        demand=(1000.0,) * horizon,
        initial_backlog=0.0,
        regular_hours=regular_hours,
        regular_productivity=2.0,
        overtime_productivity=2.0,
        overtime_max_hours=(0.0,) * horizon,
        surge_max=(0.0,) * horizon,
        regular_wage=1.0,
        overtime_wage=1.0,
        surge_price=1.0,
        backlog_penalty=1.0,
    )
    return Scenario(
        scenario_id="direct",
        description="Directly built scenario for the guard.",
        demand_basis=POINT_FORECAST,
        quantile=None,
        adjustments={},
        problem=PlanningProblem(SYNTHETIC_START, (unit,), None),
        triggers={"citywide": 0.0},
        demand_sha256="0" * 64,
        base_demand_total=1000.0 * horizon,
    )


def test_percentile_guard_checks_every_day() -> None:
    # Day 0: 4000 x 2 = 8000 (31 % away); day 1: 5804.5 x 2 = 11,609 (refused).
    with pytest.raises(ConfigError, match=r"11,609\.0 requests/day"):
        check_capacity_is_not_a_percentile(_direct_scenario((4000.0, 5804.5)), LEGACY_PERCENTILE)
    check_capacity_is_not_a_percentile(_direct_scenario((4000.0, 5000.0)), LEGACY_PERCENTILE)
