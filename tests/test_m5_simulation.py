"""Interactive simulations: request semantics, M4 reproduction, invariants and the HTTP path.

The reproduction tests are the strongest check that the API reuses the M4 engine rather
than re-implementing it: a simulation request equal to a configured reference scenario
must give exactly the committed reference plan (``plans.csv``, ``metrics.json``).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from atlas.api.problems import ApiError
from atlas.api.reference import ReferenceStore
from atlas.api.schemas import SimulationRequest
from atlas.api.simulation import (
    CLIENT_ASSUMPTION,
    REFERENCE_ASSUMPTION,
    REMOVED_ASSUMPTION,
    SimulationService,
    build_configuration,
    prepare,
    worker_context,
)
from atlas.api.worker import compute
from tests.m5_helpers import M4_RUN, assert_envelope, client, read_csv, read_json

pytestmark = pytest.mark.integration

TOL = 1e-6


def request(**body: Any) -> SimulationRequest:
    return SimulationRequest.model_validate(body)


def run(store: ReferenceStore, **body: Any) -> dict[str, Any]:
    prepared = prepare(store, request(**body))
    status, payload = compute(worker_context(store), prepared.config_json)
    assert status == "ok", payload
    assert isinstance(payload, dict)
    return payload


def reference(scenario_id: str) -> dict[str, Any]:
    metrics = read_json(M4_RUN / "metrics.json")
    found: dict[str, Any] = next(s for s in metrics["scenarios"] if s["scenario_id"] == scenario_id)
    return found


def reference_days(scenario_id: str) -> list[dict[str, str]]:
    return [r for r in read_csv(M4_RUN / "plans.csv") if r["scenario_id"] == scenario_id]


# --- Request -> configuration ------------------------------------------------------------


def test_omitted_fields_take_reference_values(api_store: ReferenceStore) -> None:
    config = read_json(M4_RUN / "config.json")
    payload, resolved = build_configuration(api_store.config, request())  # type: ignore[arg-type]
    unit = payload["service_units"][0]
    assert unit == config["service_units"][0]
    assert payload["discretionary_budget"] == config["discretionary_budget"]
    assert {p.source for p in resolved} == {"reference_default"}
    # The configuration keeps each reference entry; the response words it neutrally and
    # keeps the documented rationale apart (it describes the 30-day reference).
    for item in resolved:
        entry = (
            config["discretionary_budget"]
            if item.name == "discretionary_budget"
            else config["service_units"][0][item.name]
        )
        if entry is None:
            assert item.reference_assumption is None
            assert item.value is None
        else:
            assert item.assumption == REFERENCE_ASSUMPTION
            assert item.reference_assumption == entry["assumption"]
    assert payload["sensitivity"] is None
    assert payload["frontier"] is None
    assert payload["shadow_prices"] is None
    assert [s["scenario_id"] for s in payload["scenarios"]] == ["interactive"]
    # Pins are kept: the simulation reads exactly the inputs of the reference run.
    assert payload["forecast"] == config["forecast"]
    assert payload["uncertainty"] == config["uncertainty"]


def test_client_values_and_removed_limits(api_store: ReferenceStore) -> None:
    body = request(
        parameters={"overtime_wage": 70.0, "surge_total": None, "discretionary_budget": None},
        demand={"multiplier": 1.05, "shift_per_day": 10.0},
    )
    payload, resolved = build_configuration(api_store.config, body)  # type: ignore[arg-type]
    unit = payload["service_units"][0]
    assert unit["overtime_wage"] == {
        "value": 70.0,
        "unit": "usd_per_staff_hour",
        "provenance": "HYPOTHETICAL",
        "assumption": CLIENT_ASSUMPTION,
    }
    assert unit["surge_total"] is None
    assert payload["discretionary_budget"] is None
    by_name = {p.name: p for p in resolved}
    assert by_name["overtime_wage"].source == "request"
    assert by_name["discretionary_budget"].source == "removed_by_request"
    assert by_name["discretionary_budget"].assumption == REMOVED_ASSUMPTION
    assert by_name["surge_total"].source == "removed_by_request"
    assert by_name["overtime_wage"].reference_assumption is None
    adjustments = payload["scenarios"][0]["adjustments"]
    assert adjustments["demand_multiplier"]["value"] == 1.05
    assert adjustments["demand_shift_per_day"]["value"] == 10.0


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"parameters": {"regular_wage": None}}, "cannot be null"),
        ({"demand": {"basis": "m3_marginal_quantile"}}, "required"),
        ({"demand": {"quantile": 0.9}}, "only allowed"),
    ],
)
def test_contradictory_requests_are_rejected(
    api_store: ReferenceStore, body: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(ApiError) as caught:
        prepare(api_store, request(**body))
    assert caught.value.status == 422
    assert fragment in caught.value.message


def test_a_demand_percentile_is_never_a_capacity(api_store: ReferenceStore) -> None:
    # 4,643.5 staff hours x 2.5 requests/hour = 11,608.75: the legacy "planning capacity".
    with pytest.raises(ApiError) as caught:
        prepare(api_store, request(parameters={"regular_hours_per_day": 4643.5}))
    assert caught.value.code == "simulation_rejected"
    assert "percentile" in caught.value.message
    assert "11,608.75" in caught.value.message
    assert not caught.value.message.startswith("scenario interactive")
    assert [d["field"] for d in caught.value.details] == [
        "parameters.regular_hours_per_day",
        "parameters.regular_productivity",
    ]
    # Just outside the 1 % band it is an ordinary HYPOTHETICAL capacity.
    prepare(api_store, request(parameters={"regular_hours_per_day": 4700.0}))


def test_surge_start_beyond_the_horizon_is_rejected(api_store: ReferenceStore) -> None:
    prepare(api_store, request(horizon_days=10, parameters={"surge_available_from_day": 11}))
    with pytest.raises(ApiError) as caught:
        prepare(api_store, request(horizon_days=10, parameters={"surge_available_from_day": 12}))
    assert caught.value.code == "simulation_rejected"
    assert caught.value.message == "surge_available_from_day must be within 1..horizon_days + 1"
    assert caught.value.details == [
        {"field": "parameters.surge_available_from_day", "message": caught.value.message}
    ]


def test_negative_zero_is_the_same_simulation(api_store: ReferenceStore) -> None:
    zero = prepare(api_store, request(parameters={"terminal_penalty": 0, "initial_backlog": 0}))
    negative = prepare(
        api_store,
        request(parameters={"terminal_penalty": -0.0, "initial_backlog": -0.0}),
    )
    assert zero.config.fingerprint() == negative.config.fingerprint()
    assert "-0.0" not in negative.config_json
    assert all(str(p.value) != "-0.0" for p in negative.parameters)
    shifted = prepare(api_store, request(demand={"shift_per_day": -0.0}))
    assert shifted.config.fingerprint() == prepare(api_store, request()).config.fingerprint()


def test_limitations_describe_this_simulation(api_store: ReferenceStore) -> None:
    from atlas.api.simulation import limitations

    def texts(**body: Any) -> list[str]:
        prepared = prepare(api_store, request(**body))
        return limitations(prepared.config, prepared.parameters, 30, 984.9183743068235)

    reference = texts()
    # 400 h/day x 2.25 requests/h = 900 requests/day of overtime at most.
    assert any("MAE over horizons 1-30 was 985 requests/day, 1.1 times" in t for t in reference)
    assert any("(900 requests/day)" in t for t in reference)
    assert not any("prorated" in t for t in reference)
    none = texts(parameters={"overtime_max_hours_per_day": 0})
    assert any("this simulation has no overtime capacity" in t for t in none)
    short = texts(horizon_days=7)
    prorated = [t for t in short if "not prorated" in t]
    assert prorated == [
        "Horizon totals are not prorated: overtime_total_hours, discretionary_budget keep "
        "their 30-day reference values over this 7-day horizon."
    ]
    # A total the client sets (or removes) is the client's choice: no notice for it.
    chosen = texts(
        horizon_days=7, parameters={"overtime_total_hours": 1000, "discretionary_budget": None}
    )
    assert not any("prorated" in t for t in chosen)


def test_simulation_id_is_the_configuration_fingerprint(api_store: ReferenceStore) -> None:
    prepared = prepare(api_store, request(parameters={"backlog_penalty": 3.0}))
    expected = hashlib.sha256(prepared.config_json.encode("utf-8")).hexdigest()
    assert prepared.config.fingerprint() == expected
    again = prepare(api_store, request(parameters={"backlog_penalty": 3.0}))
    assert again.config.fingerprint() == expected
    other = prepare(api_store, request(parameters={"backlog_penalty": 3.5}))
    assert other.config.fingerprint() != expected


# --- Reproduction of the committed reference scenarios ---------------------------------

#: Requests equal to configured reference scenarios (same arithmetic as the M4 scenario).
REPRODUCED = {
    "normal": {},
    "reduced_budget": {"parameters": {"discretionary_budget": 150_000.0}},
    "increased_demand": {"demand": {"multiplier": 1.1}},
    "delayed_surge": {"parameters": {"surge_available_from_day": 8}},
    "m2_bias_shift": {"demand": {"shift_per_day": 256.0}},
    "m3_marginal_q90_stress": {"demand": {"basis": "m3_marginal_quantile", "quantile": 0.9}},
}


@pytest.mark.parametrize("scenario_id", sorted(REPRODUCED))
def test_simulation_reproduces_the_reference_scenario(
    api_store: ReferenceStore, scenario_id: str
) -> None:
    payload = run(api_store, **REPRODUCED[scenario_id])
    outcome, expected = payload["outcome"], reference(scenario_id)
    for key in (
        "feasibility",
        "optimal_score",
        "demand_sha256",
        "demand_total_requests",
        "reported_plan",
        "strict_programme",
        "without_terminal_target",
        "relaxed",
        "diagnosis",
        "optimal_face",
        "reported_plan_after_tie_breaks",
        "binding_constraints",
        "comparisons",
        "terminal_target_changes_optimal_score",
        "terminal_target_changes_reported_plan",
        "problems",
    ):
        assert outcome[key] == expected[key], key
    # Shadow-price brackets and frontiers are research analyses: never run on request.
    assert outcome["shadow_prices"]["computed"] is False
    assert outcome["frontier"] is None
    days = reference_days(scenario_id)
    assert len(payload["plans"]) == len(days)
    for mine, theirs in zip(payload["plans"], days, strict=True):
        assert mine["plan"] == theirs["plan"]
        for column in ("demand", "served", "overtime_hours", "surge_requests", "backlog"):
            assert mine[column] == float(theirs[column]), (column, mine["day"])


# --- Invariants on random bounded inputs ------------------------------------------------

PARAMETERS = st.fixed_dictionaries(
    {},
    optional={
        "initial_backlog": st.floats(0, 50_000),
        # Kept below the legacy demand percentile band (4,585.6-4,701.6 hours at 2.5/hour).
        "regular_hours_per_day": st.floats(3_000, 4_500),
        "overtime_max_hours_per_day": st.floats(0, 1_000),
        "overtime_total_hours": st.none() | st.floats(0, 10_000),
        "surge_max_per_day": st.floats(0, 2_000),
        "surge_total": st.none() | st.floats(0, 20_000),
        "surge_available_from_day": st.integers(1, 11),
        "overtime_wage": st.sampled_from([0.0, 30.0, 60.0, 90.0]),
        "surge_price": st.sampled_from([0.0, 25.0, 30.0, 45.0]),
        "backlog_penalty": st.sampled_from([0.0, 0.5, 2.0, 8.0]),
        "terminal_penalty": st.sampled_from([0.0, 5.0]),
        "terminal_backlog_max": st.none() | st.floats(0, 20_000),
        "backlog_trigger": st.floats(0, 20_000),
        "discretionary_budget": st.none() | st.floats(0, 500_000),
    },
)


def check_invariants(body: dict[str, Any], payload: dict[str, Any]) -> None:
    outcome = payload["outcome"]
    parameters = body.get("parameters", {})
    assert outcome["problems"] == []
    plan = outcome["reported_plan"]
    assert plan["certificate"]["holds"] is True
    evaluation = plan["evaluation"]
    # Cost arithmetic of the reported plan.
    assert math.isclose(
        evaluation["discretionary_cost"],
        evaluation["overtime_cost"] + evaluation["surge_cost"],
        rel_tol=1e-12,
        abs_tol=1e-9,
    )
    assert math.isclose(
        evaluation["operating_cost"],
        evaluation["regular_cost"] + evaluation["discretionary_cost"],
        rel_tol=1e-12,
    )
    assert math.isclose(
        evaluation["penalised_score"],
        evaluation["discretionary_cost"]
        + evaluation["backlog_penalty"]
        + evaluation["terminal_penalty"],
        rel_tol=1e-12,
        abs_tol=1e-9,
    )
    strict = outcome["feasibility"] == "strict"
    assert (outcome["diagnosis"] is None) is strict
    reported = "optimized" if strict else "relaxed_terminal_target"
    days = [row for row in payload["plans"] if row["plan"] == reported]
    assert len(days) == body.get("horizon_days", 30)
    surge_day = parameters.get("surge_available_from_day", 1)
    previous = parameters.get("initial_backlog", 8000.0)
    for row in days:
        scale = TOL + 1e-9 * max(1.0, row["work_available"])
        assert min(row["served"], row["overtime_hours"], row["surge_requests"]) >= -scale
        assert row["backlog"] >= -scale
        # Backlog balance and service never above the work or the capacity.
        assert abs(row["backlog"] - (previous + row["demand"] - row["served"])) <= scale
        assert row["served"] <= row["work_available"] + scale
        capacity = row["regular_capacity"] + row["overtime_capacity"] + row["surge_requests"]
        assert row["served"] <= capacity + scale
        if "overtime_max_hours_per_day" in parameters:
            assert row["overtime_hours"] <= parameters["overtime_max_hours_per_day"] + scale
        if row["day"] < surge_day:
            assert row["surge_requests"] == 0.0
        previous = row["backlog"]
    budget = parameters.get("discretionary_budget", 300_000.0)
    if budget is not None:
        assert evaluation["discretionary_cost"] <= budget + TOL + 1e-9 * budget
    target = parameters.get("terminal_backlog_max", 4_000.0)
    if strict and target is not None:
        assert evaluation["terminal_backlog"] <= target + TOL + 1e-9 * target
    if not strict:
        assert target is not None  # without a target the model is always feasible
        assert evaluation["terminal_backlog"] > target
        assert outcome["diagnosis"]["label"] == "DIAGNOSTIC: not a plan for the stated constraints"
        assert outcome["diagnosis"]["reasons"]
    for comparison in outcome["comparisons"]:
        if comparison["relation_of_reported_plan"] == "dominates":
            assert comparison["request_days_difference"] <= TOL or (
                comparison["discretionary_cost_difference"] <= TOL
            )


@settings(
    max_examples=12,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(parameters=PARAMETERS, horizon=st.integers(1, 30))
def test_invariants_hold_for_bounded_inputs(
    api_store: ReferenceStore, parameters: dict[str, Any], horizon: int
) -> None:
    body: dict[str, Any] = {"horizon_days": horizon, "parameters": parameters}
    if parameters.get("surge_available_from_day", 1) > horizon + 1:
        with pytest.raises(ApiError, match="surge_available_from_day"):
            run(api_store, **body)
        return
    check_invariants(body, run(api_store, **body))


def test_no_target_means_strictly_feasible(api_store: ReferenceStore) -> None:
    body: dict[str, Any] = {
        "parameters": {"terminal_backlog_max": None, "discretionary_budget": 0.0}
    }
    payload = run(api_store, **body)
    assert payload["outcome"]["feasibility"] == "strict"
    check_invariants(body, payload)


def test_zero_prices_give_a_tied_but_labelled_optimum(api_store: ReferenceStore) -> None:
    body: dict[str, Any] = {
        "horizon_days": 7,
        "parameters": {"overtime_wage": 0.0, "surge_price": 0.0, "backlog_penalty": 0.0},
    }
    payload = run(api_store, **body)
    check_invariants(body, payload)
    outcome = payload["outcome"]
    assert outcome["optimal_face"]["unique"] in (True, False)


# --- Through HTTP and the worker pool ----------------------------------------------------


@pytest.fixture(scope="module")
def api(api_store: ReferenceStore, api_simulator: SimulationService) -> Iterator[TestClient]:
    with client(api_store, api_simulator) as test_client:
        yield test_client


def post(api: TestClient, body: Any, status: int = 200, **kwargs: Any) -> dict[str, Any]:
    response = api.post("/api/v1/simulations", json=body, **kwargs)
    assert response.status_code == status, response.text
    result: dict[str, Any] = response.json()
    return result


def test_http_simulation_is_labelled_and_deterministic(api: TestClient) -> None:
    first = api.post("/api/v1/simulations", json={"horizon_days": 14})
    second = api.post("/api/v1/simulations", json={"horizon_days": 14})
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert first.headers["server-timing"].startswith("compute;dur=")
    assert first.headers["cache-control"] == "no-store"
    body = first.json()
    assert_envelope(body, "hypothetical_simulation")
    assert body["integrity"]["parameter_provenance"] == "HYPOTHETICAL"
    data = body["data"]
    assert data["horizon_days"] == 14
    assert (data["first_date"], data["last_date"]) == ("2026-07-01", "2026-07-14")
    assert all(p["provenance"] == "HYPOTHETICAL" for p in data["parameters"])
    assert data["demand_notice"] is None
    assert any("recommendation" in limit for limit in data["limitations"])
    assert len(data["daily_plans"]) == 14 * 3


def test_http_stress_path_carries_its_notice(api: TestClient) -> None:
    body = post(api, {"demand": {"basis": "m3_marginal_quantile", "quantile": 0.75}})
    assert "carries no probability" in body["data"]["demand_notice"]
    assert any("carries no probability" in n for n in body["integrity"]["notices"])
    assert body["data"]["demand_basis"] == "per_day_marginal_quantile_path"


@pytest.mark.parametrize(
    "body",
    [
        [],
        "text",
        {"horizon_days": 0},
        {"horizon_days": 31},
        {"horizon_days": 7.0},
        {"horizon_days": "7"},
        {"horizon_days": True},
        {"unknown": 1},
        {"parameters": {"unknown": 1}},
        {"parameters": {"initial_backlog": -1}},
        {"parameters": {"initial_backlog": 1e12}},
        {"parameters": {"initial_backlog": "100"}},
        {"parameters": {"regular_productivity": 0}},
        {"parameters": {"overtime_wage": 1e-12}},
        {"parameters": {"surge_available_from_day": 0}},
        {"parameters": {"surge_available_from_day": 2.5}},
        {"demand": {"multiplier": 0}},
        {"demand": {"multiplier": 3}},
        {"demand": {"shift_per_day": -100}},
        {"demand": {"basis": "m3_marginal_quantile", "quantile": 0.5}},
        {"demand": {"basis": "actuals"}},
    ],
)
def test_http_rejects_invalid_bodies(api: TestClient, body: Any) -> None:
    error = post(api, body, 422)["error"]
    assert error["code"] in ("validation_error", "simulation_rejected")
    assert error["request_id"]
    text = json.dumps(error)
    assert "Traceback" not in text
    assert "/home/" not in text


def test_http_rejects_non_finite_json(api: TestClient) -> None:
    for literal in ("NaN", "Infinity", "-Infinity"):
        response = api.post(
            "/api/v1/simulations",
            content=f'{{"parameters": {{"initial_backlog": {literal}}}}}',
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422, literal


def test_http_rejection_of_a_percentile_capacity(api: TestClient) -> None:
    error = post(api, {"parameters": {"regular_hours_per_day": 4643.5}}, 422)["error"]
    assert error["code"] == "simulation_rejected"
    assert "percentile" in error["message"]
    assert {d["field"] for d in error["details"]} == {
        "parameters.regular_hours_per_day",
        "parameters.regular_productivity",
    }


def test_http_error_fields_follow_one_convention(api: TestClient) -> None:
    """Every layer names a body field by its dotted path without a location prefix."""
    schema = post(api, {"parameters": {"regular_wage": -1}}, 422)["error"]
    assert [d["field"] for d in schema["details"]] == ["parameters.regular_wage"]
    application = post(api, {"parameters": {"regular_wage": None}}, 422)["error"]
    assert [d["field"] for d in application["details"]] == ["parameters.regular_wage"]
    m4 = post(api, {"horizon_days": 5, "parameters": {"surge_available_from_day": 20}}, 422)
    assert [d["field"] for d in m4["error"]["details"]] == ["parameters.surge_available_from_day"]
    assert "Value error" not in m4["error"]["message"]
    nested = api.post(
        "/api/v1/simulations",
        content=b'{"parameters": {"regular_wage": 50, "regular_wage": 60}}',
        headers={"content-type": "application/json"},
    ).json()["error"]
    assert [d["field"] for d in nested["details"]] == ["parameters.regular_wage"]
    malformed = api.post(
        "/api/v1/simulations", content=b"{", headers={"content-type": "application/json"}
    )
    assert malformed.status_code == 422
    assert malformed.json()["error"]["details"] == [
        {"field": "<body>", "message": "the body is not valid JSON"}
    ]


def test_http_simulation_sources_and_assumptions(api: TestClient) -> None:
    body = post(
        api, {"horizon_days": 7, "demand": {"basis": "m3_marginal_quantile", "quantile": 0.975}}
    )
    kinds = [s["kind"] for s in body["provenance"]["sources"]]
    assert kinds == [
        "m4_reference_configuration",
        "m2_projection_run",
        "m2_backtest_run",
        "m3_calibration_status",
        "m3_projection_run",
    ]
    data = body["data"]
    defaults = [p for p in data["parameters"] if p["source"] == "reference_default"]
    assert defaults
    assert all(p["assumption"] == REFERENCE_ASSUMPTION or p["value"] is None for p in defaults)
    assert any("not prorated" in limit for limit in data["limitations"])
    point = post(api, {"horizon_days": 7})["provenance"]["sources"]
    assert "m3_projection_run" not in [s["kind"] for s in point]


def test_capacity_ratio_is_capacity_over_forecast(api: TestClient) -> None:
    config = read_json(M4_RUN / "config.json")["service_units"][0]
    capacity = config["regular_hours_per_day"]["value"] * config["regular_productivity"]["value"]
    data = post(api, {"horizon_days": 5})["data"]
    plan = data["daily_plans"][0]["plan"]
    demand = [p["demand"] for p in data["daily_plans"] if p["plan"] == plan]
    assert len(demand) == 5
    expected = capacity / (sum(demand) / 5)
    assert data["capacity_to_point_forecast_mean_ratio"] == pytest.approx(expected, rel=1e-12)
