"""End-to-end tests of the read-only M5 endpoints against the committed reference outputs.

Every expected value is read from the committed files with ``json`` / ``csv`` in this
module, never from the API code.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from atlas.api.reference import ReferenceStore
from tests.m5_helpers import (
    M2_FULL,
    M2_PROJECTION,
    M3_FULL,
    M3_PROJECTION,
    M4_RUN,
    assert_envelope,
    client,
    read_csv,
    read_json,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def api(api_store: ReferenceStore) -> Iterator[TestClient]:
    with client(api_store) as test_client:
        yield test_client


def get(api: TestClient, path: str, status: int = 200) -> dict[str, Any]:
    response = api.get(path)
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/json")
    body: dict[str, Any] = response.json()
    return body


# --- health and system ---------------------------------------------------------------


def test_live_and_ready(api: TestClient) -> None:
    assert get(api, "/health/live") == {"status": "alive", "atlas_version": "0.5.0"}
    ready = get(api, "/health/ready")
    assert ready["status"] == "ready"
    assert [c["component"] for c in ready["components"]] == [
        "snapshot",
        "forecast_and_uncertainty_status",
        "m2_backtest",
        "m3_projection_intervals",
        "m4_reference_run",
        "simulation",
    ]
    assert all(c["ready"] and c["detail"] is None for c in ready["components"])


def test_system_describes_status_limits_and_sources(api: TestClient) -> None:
    body = get(api, "/api/v1/system")
    assert_envelope(body, "metadata")
    data = body["data"]
    assert data["milestone"] == "M5"
    assert data["snapshot"] == {
        "snapshot_id": "legacy-v0",
        "data_fingerprint": "5ab8b3a59e107158da9a3a285ea58a22c566f5b0da067e0f3fae6ee5e3d73a4d",
        "first_date": "2023-01-01",
        "last_date": "2026-06-30",
        "n_days": 1277,
    }
    status = data["scientific_status"]
    assert status["forecast_model"] == "atlas_linear_regression"
    assert status["uncertainty_interval_status"] == "failed_calibration"
    assert status["point_forecast_validated"] is False
    assert status["validated_recommendation"] is False
    assert status["parameter_provenance"] == "HYPOTHETICAL"
    assert math.isclose(status["forecast_confirmation_mae_requests_per_day"], 984.9183743068235)
    assert data["limits"]["max_horizon_days"] == 30
    paths = {(e["method"], e["path"]) for e in data["endpoints"]}
    assert ("POST", "/api/v1/simulations") in paths
    computing = [e for e in data["endpoints"] if e["cost"] == "bounded_computation"]
    assert [e["path"] for e in computing] == ["/api/v1/simulations"]
    m4 = read_json(M4_RUN / "manifest.json")
    kinds = {s["kind"]: s for s in body["provenance"]["sources"]}
    assert kinds["m4_reference_run"]["result_fingerprint"] == m4["result_fingerprint"]
    assert data["solver"]["scipy"] == "1.18.1"


# --- M2 ---------------------------------------------------------------------------------


def test_forecast_equals_the_committed_projection(api: TestClient) -> None:
    body = get(api, "/api/v1/forecasts")
    assert_envelope(body, "projected")
    rows = read_csv(M2_PROJECTION / "projections.csv")
    data = body["data"]
    assert data["model_id"] == "atlas_linear_regression"
    assert data["origin"] == "2026-06-30"
    assert data["record_type"] == "future_projection"
    assert [p["target_date"] for p in data["points"]] == [r["target_date"] for r in rows]
    # Exact equality: the projection is served as recorded (%.17g round trip).
    assert [p["prediction"] for p in data["points"]] == [float(r["prediction"]) for r in rows]
    assert {p["status"] for p in data["points"]} == {"projected"}
    holidays = {p["target_date"]: p["federal_holiday"] for p in data["points"]}
    assert holidays["2026-07-03"] == "Independence Day"  # observed on the Friday
    assert sum(name is not None for name in holidays.values()) == 1
    accuracy = data["accuracy_context"]
    assert accuracy["period"] == "confirmation"
    assert math.isclose(accuracy["mae_requests_per_day"], 984.9183743068235)
    assert math.isclose(accuracy["bias_requests_per_day"], -255.92319894851286)
    assert any("not prospectively validated" in n for n in body["integrity"]["notices"])


def test_forecast_horizon_is_bounded(api: TestClient) -> None:
    assert len(get(api, "/api/v1/forecasts?horizon_days=7")["data"]["points"]) == 7
    for bad in ("0", "31", "-1", "x", "1.5", "1e1"):
        error = get(api, f"/api/v1/forecasts?horizon_days={bad}", 422)["error"]
        assert error["code"] == "validation_error"


def test_history_defaults_to_the_last_180_observed_days(api: TestClient) -> None:
    body = get(api, "/api/v1/forecasts/history")
    assert_envelope(body, "observed")
    data = body["data"]
    assert (data["start"], data["end"]) == ("2026-01-02", "2026-06-30")
    assert len(data["points"]) == 180
    totals = {r["request_date"]: int(r["request_count"]) for r in _daily_total()}
    for point in data["points"]:
        assert point["status"] == "observed"
        assert point["requests"] == totals[point["date"]]


def _daily_total() -> list[dict[str, str]]:
    from tests.conftest import REPO_ROOT

    return read_csv(REPO_ROOT / "01 Data" / "daily_total.csv")


def test_history_window_and_limits(api: TestClient) -> None:
    whole = get(api, "/api/v1/forecasts/history?start=2023-01-01&end=2026-06-30")["data"]
    assert len(whole["points"]) == 1277
    days = [date.fromisoformat(p["date"]) for p in whole["points"]]
    assert days == [date(2023, 1, 1) + timedelta(days=k) for k in range(1277)]
    assert sum(p["requests"] for p in whole["points"]) == sum(
        int(r["request_count"]) for r in _daily_total()
    )
    christmas = [p for p in whole["points"] if p["date"] == "2025-12-25"]
    assert christmas[0]["federal_holiday"] == "Christmas Day"
    one = get(api, "/api/v1/forecasts/history?start=2024-02-29&end=2024-02-29")["data"]
    assert [p["date"] for p in one["points"]] == ["2024-02-29"]
    for query in (
        "start=2022-12-31&end=2023-01-05",  # before the snapshot
        "start=2026-06-01&end=2026-07-01",  # after the snapshot (a projected day)
        "start=2026-06-10&end=2026-06-01",  # reversed
        "start=2026-02-30",  # not a date
        "start=2023-01-01T00:00:00&end=2023-01-02",  # a datetime
    ):
        assert get(api, f"/api/v1/forecasts/history?{query}", 422)["error"]["code"] == (
            "validation_error"
        )


def test_models_carry_the_selection_and_its_tests(api: TestClient) -> None:
    body = get(api, "/api/v1/models")
    assert_envelope(body, "historical_evaluation")
    metrics = read_json(M2_FULL / "metrics.json")
    data = body["data"]
    assert [m["model_id"] for m in data["models"]] == [m["model_id"] for m in metrics["models"]]
    assert data["recommended_model"] == metrics["recommendation"]["recommended_model"]
    assert [m["model_id"] for m in data["models"] if m["recommended"]] == [
        "atlas_linear_regression"
    ]
    assert data["recommendation_reason"] == metrics["recommendation"]["reason"]
    tests = data["primary_tests"]
    assert [t["hypothesis"][:2] for t in tests] == ["H1", "H2"]
    h1 = tests[0]
    assert math.isclose(h1["holm_adjusted_p_value"], 0.0535166597203122)
    assert h1["mae_difference_ci_low"] < 0 < h1["mae_difference_ci_high"]
    expected = {
        (r["model_id"], r["period"]): r["mae"]
        for r in metrics["summary"]
        if r["scope"] == "bucket" and r["label"] == "days_1_30"
    }
    for model in data["models"]:
        for period, mae in model["mae_days_1_30_by_period"].items():
            assert mae == expected[(model["model_id"], period)]
    assert any("4.93" in n for n in body["integrity"]["notices"])


def test_metrics_filters(api: TestClient) -> None:
    summary = read_json(M2_FULL / "metrics.json")["summary"]
    every_bucket = get(api, "/api/v1/forecasts/metrics")["data"]["rows"]
    assert len(every_bucket) == sum(r["scope"] == "bucket" for r in summary)
    rows = get(
        api,
        "/api/v1/forecasts/metrics?model_id=seasonal_naive&period=confirmation&scope=horizon",
    )["data"]["rows"]
    expected = [
        r
        for r in summary
        if r["model_id"] == "seasonal_naive"
        and r["period"] == "confirmation"
        and r["scope"] == "horizon"
    ]
    assert len(rows) == 30
    assert [(r["label"], r["mae"], r["rmse"], r["bias"]) for r in rows] == [
        (r["label"], r["mae"], r["rmse"], r["bias"]) for r in expected
    ]
    assert get(api, "/api/v1/forecasts/metrics?model_id=nope", 404)["error"]["code"] == "not_found"
    assert get(api, "/api/v1/forecasts/metrics?period=nope", 404)["error"]["code"] == "not_found"
    for bad in ("model_id=..%2Fx", "model_id=A", "scope=all", "period=" + "x" * 65):
        assert get(api, f"/api/v1/forecasts/metrics?{bad}", 422)["error"]["code"] == (
            "validation_error"
        )


# --- M3 ---------------------------------------------------------------------------------


def test_uncertainty_status_is_failed_calibration(api: TestClient) -> None:
    body = get(api, "/api/v1/uncertainty/status")
    assert_envelope(body, "calibration_assessment")
    record = read_json(M3_FULL / "calibration_status.json")
    decision = record["decision_status"]
    data = body["data"]
    assert data["interval_status"] == decision["interval_status"] == "failed_calibration"
    assert data["decision_grade"] is False
    assert data["statement"] == decision["statement"]
    assert data["note"] == decision["note"]
    primary = decision["by_model"][decision["primary_model"]]
    assert data["failure_reasons"] == primary["intervals"]["reasons"]
    assert [lvl["level"] for lvl in data["interval_levels"]] == [0.8, 0.95]
    assert data["exceedance_status"]["trailing180_p75"] == "failed_calibration"
    assert body["integrity"]["notices"][0] == decision["statement"]


def test_intervals_are_labelled_and_equal_the_committed_file(api: TestClient) -> None:
    body = get(api, "/api/v1/uncertainty/intervals?horizon_days=30")
    assert_envelope(body, "projected")
    rows = [
        r
        for r in read_csv(M3_PROJECTION / "projection_intervals.csv")
        if r["model_id"] == "atlas_linear_regression"
        and r["pair_id"] == "empirical_relative@fixed365"
    ]
    points = body["data"]["points"]
    assert len(points) == len(rows) == 30
    for point, row in zip(points, rows, strict=True):
        assert point["quantiles"]["0.9"] == float(row["q_0.9"])
        assert point["prediction"] == float(row["prediction"])
        levels = sorted(float(k) for k in point["quantiles"])
        values = [point["quantiles"][f"{level:g}"] for level in levels]
        assert values == sorted(values)  # non-crossing, as M3 issued them
    assert body["data"]["interval_status"] == "failed_calibration"
    assert any("not decision-grade" in n for n in body["integrity"]["notices"])


# --- M4 ---------------------------------------------------------------------------------


def test_scenario_list_matches_the_reference_run(api: TestClient) -> None:
    body = get(api, "/api/v1/scenarios")
    assert_envelope(body, "hypothetical_reference_run")
    assert body["integrity"]["parameter_provenance"] == "HYPOTHETICAL"
    metrics = read_json(M4_RUN / "metrics.json")
    manifest = read_json(M4_RUN / "manifest.json")
    data = body["data"]
    assert data["run_id"] == manifest["run_id"]
    assert data["result_fingerprint"] == manifest["result_fingerprint"]
    assert data["config_fingerprint"] == metrics["config_fingerprint"]
    assert [s["scenario_id"] for s in data["scenarios"]] == [
        s["scenario_id"] for s in metrics["scenarios"]
    ]
    for summary, scenario in zip(data["scenarios"], metrics["scenarios"], strict=True):
        evaluation = scenario["reported_plan"]["evaluation"]
        assert summary["feasibility"] == scenario["feasibility"]
        assert summary["discretionary_cost"] == evaluation["discretionary_cost"]
        assert summary["terminal_backlog"] == evaluation["terminal_backlog"]
        assert summary["binding_constraints"] == scenario["binding_constraints"]
        if scenario["feasibility"] == "strict":
            assert summary["diagnosis_reasons"] == []
        else:
            assert summary["plan_label"].startswith("DIAGNOSTIC relaxed_terminal_target")
            assert summary["diagnosis_reasons"] == scenario["diagnosis"]["reasons"]
    feasible = [s["scenario_id"] for s in data["scenarios"] if s["feasibility"] == "strict"]
    assert feasible == ["normal", "increased_costs", "delayed_surge"]
    costs = {s["scenario_id"]: s for s in data["scenarios"]}["increased_costs"]
    assert costs["optimal_plan_unique"] is False
    assert costs["plan_label"] == "one of several equally scored HYPOTHETICAL plans"
    notices = body["integrity"]["notices"]
    assert any("DIAGNOSTIC" in n for n in notices)
    assert any("carries no probability" in n for n in notices)  # the q0.9 stress path


def assert_same_as_recorded(served: Any, recorded: Any) -> None:
    """Every recorded value is served unchanged; the schema may add keys, always null."""
    if isinstance(recorded, dict):
        assert isinstance(served, dict)
        for key, value in served.items():
            if key in recorded:
                assert_same_as_recorded(value, recorded[key])
            else:
                assert value is None or value == [], key
        assert set(recorded) <= set(served)
    elif isinstance(recorded, list):
        assert len(served) == len(recorded)
        for mine, theirs in zip(served, recorded, strict=True):
            assert_same_as_recorded(mine, theirs)
    else:
        assert served == recorded


def test_every_reference_outcome_is_served_unchanged(api: TestClient) -> None:
    metrics = read_json(M4_RUN / "metrics.json")
    for scenario in metrics["scenarios"]:
        served = get(api, f"/api/v1/scenarios/{scenario['scenario_id']}")["data"]["outcome"]
        assert_same_as_recorded(served, scenario)


def test_scenario_detail_has_daily_plans_and_variants(api: TestClient) -> None:
    body = get(api, "/api/v1/scenarios/normal")
    assert_envelope(body, "hypothetical_reference_run")
    data = body["data"]
    metrics = read_json(M4_RUN / "metrics.json")
    expected = next(s for s in metrics["scenarios"] if s["scenario_id"] == "normal")
    assert_same_as_recorded(data["outcome"], expected)
    assert data["outcome"]["comparisons"] == expected["comparisons"]
    assert data["outcome"]["shadow_prices"]["n"] == 123
    plans = [r for r in read_csv(M4_RUN / "plans.csv") if r["scenario_id"] == "normal"]
    assert len(data["daily_plans"]) == len(plans) == 90
    for served, row in zip(data["daily_plans"], plans, strict=True):
        assert served["plan"] == row["plan"]
        assert served["backlog"] == float(row["backlog"])
        assert served["served"] == float(row["served"])
    variants = [r for r in read_csv(M4_RUN / "scenarios.csv") if r["scenario_id"] == "normal"]
    assert [v["plan"] for v in data["plans"]] == [r["plan"] for r in variants]
    assert all(p["provenance"] == "HYPOTHETICAL" for p in data["hypothetical_parameters"])
    scopes = {p["scope"] for p in data["hypothetical_parameters"]}
    assert not any(s.startswith("scenario:") and s != "scenario:normal" for s in scopes)


def test_infeasible_scenario_detail_is_diagnostic(api: TestClient) -> None:
    data = get(api, "/api/v1/scenarios/reduced_budget")["data"]
    outcome = data["outcome"]
    assert outcome["feasibility"] == "relaxed_terminal_target"
    diagnosis = outcome["diagnosis"]
    assert diagnosis["label"] == "DIAGNOSTIC: not a plan for the stated constraints"
    assert round(
        diagnosis["diagnostic_min_discretionary_spend_to_reach_hypothetical_target_usd"]
    ) == (212_474)
    assert all("DIAGNOSTIC" in reason for reason in diagnosis["reasons"])
    assert {p["plan"] for p in data["daily_plans"]} == {
        "relaxed_terminal_target",
        "fixed_capacity",
        "backlog_trigger",
    }


def test_unknown_and_malformed_scenarios(api: TestClient) -> None:
    error = get(api, "/api/v1/scenarios/nope", 404)["error"]
    assert error["code"] == "not_found"
    assert "normal" in error["details"][0]["message"]
    for bad in ("..", "%2E%2E%2Fetc%2Fpasswd", "Normal", "a" * 65, "normal;rm", "1abc"):
        response = api.get(f"/api/v1/scenarios/{bad}")
        assert response.status_code in (404, 422), (bad, response.text)
        assert response.json()["error"]["code"] in ("not_found", "validation_error")


def test_comparison_differences(api: TestClient) -> None:
    body = get(
        api,
        "/api/v1/scenarios/comparison?scenario_id=reduced_budget&scenario_id=increased_costs"
        "&baseline=normal&metric=discretionary_cost&metric=penalised_score",
    )
    data = body["data"]
    assert [r["scenario_id"] for r in data["rows"]] == [
        "normal",
        "reduced_budget",
        "increased_costs",
    ]
    metrics = {s["scenario_id"]: s for s in read_json(M4_RUN / "metrics.json")["scenarios"]}
    base = metrics["normal"]["reported_plan"]["evaluation"]["discretionary_cost"]
    for row in data["rows"]:
        cash = metrics[row["scenario_id"]]["reported_plan"]["evaluation"]["discretionary_cost"]
        assert row["values"]["discretionary_cost"] == cash
        assert row["difference_from_baseline"]["discretionary_cost"] == cash - base
    budget = {r["scenario_id"]: r for r in data["rows"]}["reduced_budget"]
    # A relaxed plan is scored by a different programme: no penalised score is compared.
    assert budget["values"]["penalised_score"] is None
    assert budget["difference_from_baseline"]["penalised_score"] is None
    assert data["units"]["penalised_score"].startswith("usd_equivalent")
    default = get(api, "/api/v1/scenarios/comparison")["data"]
    assert len(default["rows"]) == 9
    assert len(default["metrics"]) == 7
    assert get(api, "/api/v1/scenarios/comparison?baseline=nope", 404)["error"]["code"] == (
        "not_found"
    )
    too_many = "&".join(["scenario_id=normal"] * 10)
    assert get(api, f"/api/v1/scenarios/comparison?{too_many}", 422)["error"]["code"] == (
        "validation_error"
    )
    assert get(api, "/api/v1/scenarios/comparison?metric=cost", 422)["error"]["code"] == (
        "validation_error"
    )


def test_simulation_parameters_catalogue(api: TestClient) -> None:
    body = get(api, "/api/v1/simulations/parameters")
    assert_envelope(body, "hypothetical_simulation")
    config = read_json(M4_RUN / "config.json")
    unit = config["service_units"][0]
    specs = {p["name"]: p for p in body["data"]["parameters"]}
    for name, spec in specs.items():
        reference = config["discretionary_budget"] if name == "discretionary_budget" else unit[name]
        assert spec["default"] == (None if reference is None else reference["value"])
        if reference is not None:
            assert spec["unit"] == reference["unit"]
            assert spec["default_assumption"] == reference["assumption"]
        assert spec["minimum"] <= (spec["default"] or 0) <= spec["maximum"]
    assert specs["surge_total"]["default"] is None
    assert specs["surge_total"]["nullable"]
    assert not specs["regular_wage"]["nullable"]
    assert body["data"]["legacy_demand_percentile_requests_per_day"] == 11608.75


def test_unknown_paths_methods_and_query_parameters(api: TestClient) -> None:
    assert get(api, "/api/v2/forecasts", 404)["error"]["code"] == "not_found"
    response = api.post("/api/v1/forecasts", json={})
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    error = get(api, "/api/v1/scenarios?debug=1&x=2", 422)["error"]
    assert [d["field"] for d in error["details"]] == ["debug", "x"]
    assert get(api, "/health/live?verbose=true", 422)["error"]["code"] == "validation_error"


def test_responses_are_deterministic(api: TestClient) -> None:
    for path in ("/api/v1/scenarios/normal", "/api/v1/forecasts/metrics", "/api/v1/system"):
        first, second = api.get(path), api.get(path)
        assert first.content == second.content
        assert first.headers["x-request-id"] != second.headers["x-request-id"]
