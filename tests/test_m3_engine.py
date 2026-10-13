"""The probabilistic engine: time-safety, invariance to future data, selection and status.

The engine is driven by the real M2 backtest of two fast baselines on a synthetic series.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from atlas.backtest import BacktestPlan, Period, PeriodRole, run_backtest
from atlas.experiment import json_bytes
from atlas.models import SameWeekdayMedianForecaster, SeasonalNaiveForecaster
from atlas.probabilistic import (
    DECISION_GRADE,
    EXCEEDANCE_COLUMNS,
    FAILED_CALIBRATION,
    INSUFFICIENT_EVIDENCE,
    MODEL_FREE,
    UNAVAILABLE,
    EngineSettings,
    attach_outcomes,
    decision_status,
    exceedance_status,
    interval_status,
    interval_summary,
    issue_forecasts,
    quantile_column,
    run_probabilistic,
    select_interval_method,
    selection_differences,
)
from atlas.series import DemandSeries
from atlas.uncertainty_config import UncertaintyConfig
from tests.m2_helpers import noisy_series, perturbed_after
from tests.m3_helpers import BUCKETS, GRID, make_config

START = date(2023, 1, 1)
CONFIG = make_config()
SMALL = make_config(
    periods=[
        {
            "name": "sel",
            "role": "selection",
            "first_origin": "2023-07-01",
            "last_origin": "2023-08-10",
        },
        {
            "name": "ev",
            "role": "evaluation",
            "first_origin": "2023-11-15",
            "last_origin": "2024-01-20",
        },
    ]
)
Q = [quantile_column(t) for t in GRID]


def backtest_records(series: DemandSeries) -> pd.DataFrame:
    plan = BacktestPlan(
        (
            Period(
                "m3_residuals",
                PeriodRole.DESCRIPTIVE,
                CONFIG.residual_first_origin,
                CONFIG.residual_last_origin,
                1,
                True,
            ),
        ),
        max_horizon=CONFIG.max_horizon,
        min_history_days=40,
    )
    models = [SameWeekdayMedianForecaster("med", 3), SeasonalNaiveForecaster("sn")]
    return run_backtest(series, plan, models).records


def engine(config: UncertaintyConfig = CONFIG, latency: int | None = None) -> EngineSettings:
    return EngineSettings.from_config(config, BUCKETS, 0, latency_days=latency)


def issue(
    records: pd.DataFrame,
    series: DemandSeries,
    config: UncertaintyConfig = CONFIG,
    *,
    latency: int | None = None,
    exceedance: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    pairs = config.pairs()
    return issue_forecasts(
        records,
        series,
        config,
        engine(config, latency),
        periods=config.periods,
        pairs=pairs,
        exceedance_pairs=[p.pair_id for p in pairs] if exceedance else (),
    )


@pytest.fixture(scope="module")
def series() -> DemandSeries:
    return noisy_series(START, 560, seed=21)


@pytest.fixture(scope="module")
def records(series: DemandSeries) -> pd.DataFrame:
    return backtest_records(series)


@pytest.fixture(scope="module")
def issued(records: pd.DataFrame, series: DemandSeries) -> tuple[pd.DataFrame, pd.DataFrame]:
    return issue(records, series)


def test_one_row_per_scheduled_forecast(issued: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    intervals, exceedance = issued
    n_origins = sum((p.last_origin - p.first_origin).days + 1 for p in CONFIG.periods)
    model_pairs = len(CONFIG.pairs()) - 1  # climatology is model-free
    expected = n_origins * CONFIG.max_horizon * (model_pairs * len(CONFIG.point_models) + 1)
    assert len(intervals) == expected
    keys = ["period", "model_id", "pair_id", "origin", "horizon"]
    assert not intervals.duplicated(keys).any()
    assert set(intervals.loc[intervals["model_id"] == MODEL_FREE, "pair_id"]) == {
        "climatology_180d"
    }
    assert len(exceedance) == expected * len(CONFIG.thresholds)
    assert intervals["actual"].isna().all()  # nothing is attached before the end
    assert exceedance["event"].isna().all()


def test_every_interval_uses_only_residuals_observed_at_its_origin(
    issued: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    intervals, _ = issued
    model = intervals[intervals["model_id"] != MODEL_FREE]
    used = model[model["cal_last_target"].notna()]
    assert len(used) > 0.9 * len(model)
    assert (used["cal_last_target"] <= used["cal_cutoff"]).all()
    assert (used["cal_cutoff"] == used["calibration_origin"]).all()  # latency 0
    assert (used["calibration_origin"] <= used["origin"]).all()
    assert (used["cal_first_target"] >= used["cal_cutoff"] - pd.Timedelta(days=89)).all()
    assert (used["data_cutoff"] == used["origin"]).all()
    rolling = model[model["scheme_id"] == "rolling90"]
    assert (rolling["calibration_origin"] == rolling["origin"]).all()
    starts = {p.name: pd.Timestamp(p.first_origin) for p in CONFIG.periods}
    fixed = model[model["scheme_id"] == "fixed90"]
    assert (fixed["calibration_origin"] == fixed["period"].map(starts)).all()


def test_future_perturbation_leaves_earlier_issues_identical(
    records: pd.DataFrame, series: DemandSeries, issued: tuple[pd.DataFrame, pd.DataFrame]
) -> None:
    cutoff = date(2024, 1, 20)
    changed_series = perturbed_after(series, cutoff, seed=5)
    changed_records = backtest_records(changed_series)
    intervals, exceedance = issued
    other_intervals, other_exceedance = issue(changed_records, changed_series)
    for mine, theirs in ((intervals, other_intervals), (exceedance, other_exceedance)):
        early = mine["origin"] <= pd.Timestamp(cutoff)
        pd.testing.assert_frame_equal(
            mine[early].reset_index(drop=True), theirs[early].reset_index(drop=True)
        )
        # Positive control: later issues do see the change.
        late = ~early
        assert not mine[late].reset_index(drop=True).equals(theirs[late].reset_index(drop=True))


def test_latency_excludes_residuals_observed_too_late(
    records: pd.DataFrame, series: DemandSeries
) -> None:
    # Change only the recorded actuals of targets after T. With latency L, issues up to
    # T + L cannot see them; with latency 0 the issues in (T, T + L] do (positive control).
    config = make_config(periods=[CONFIG.periods[1].model_dump(mode="json")], selection=None)
    t, latency = pd.Timestamp("2024-02-10"), 3
    changed = records.copy()
    later = changed["target_date"] > t
    changed.loc[later, "actual"] = changed.loc[later, "actual"] + 3000.0
    for chosen, protected_until in ((latency, t + pd.Timedelta(days=latency)), (0, t)):
        before, _ = issue(records, series, config, latency=chosen, exceedance=False)
        after, _ = issue(changed, series, config, latency=chosen, exceedance=False)
        protected = (before["origin"] <= protected_until) & (before["scheme_id"] == "rolling90")
        pd.testing.assert_frame_equal(before[protected], after[protected])
        window = (
            (before["origin"] > t)
            & (before["origin"] <= t + pd.Timedelta(days=latency))
            & (before["scheme_id"] == "rolling90")
        )
        same = before.loc[window, Q].equals(after.loc[window, Q])
        assert same == (chosen == latency)
        used = before[before["cal_last_target"].notna()]
        assert (used["cal_last_target"] <= used["origin"] - pd.Timedelta(days=chosen)).all()


def test_fixed_scheme_is_frozen_and_rolling_scheme_updates(
    issued: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    intervals, _ = issued
    ev = intervals[(intervals["period"] == "ev") & (intervals["model_id"] == "med")]
    for pair_id, frozen in (
        ("empirical_additive@fixed90", True),
        ("empirical_additive@rolling90", False),
    ):
        rows = ev[(ev["pair_id"] == pair_id) & (ev["status"] == "ok")]
        offsets = rows[Q[-1]] - rows["prediction"]
        spread = offsets.groupby(rows["horizon"]).agg(lambda v: v.max() - v.min())
        if frozen:
            assert (spread < 1e-6).all()
            assert rows["cal_n"].nunique() <= CONFIG.max_horizon
        else:
            assert (spread > 1.0).all()


def test_exceedance_probabilities_are_ordered_and_thresholds_causal(
    issued: tuple[pd.DataFrame, pd.DataFrame], series: DemandSeries
) -> None:
    _, exceedance = issued
    ok = exceedance[exceedance["probability"].notna()]
    assert ok["probability"].between(0, 1).all()
    wide = ok.pivot_table(
        index=["period", "model_id", "pair_id", "origin", "horizon"],
        columns="threshold_label",
        values="probability",
    )
    assert (wide["hypothetical_low"] >= wide["hypothetical_high"]).all()
    assert (wide["hypothetical_low"] > wide["hypothetical_high"]).any()
    historical = ok[ok["threshold_label"] == "trailing180_p75"]
    for origin in pd.DatetimeIndex(historical["origin"].unique())[::40]:
        values = series.to_series().loc[origin - pd.Timedelta(days=179) : origin].to_numpy()
        expected = float(np.quantile(values, 0.75))
        got = historical.loc[historical["origin"] == origin, "threshold_value"].unique()
        assert got.tolist() == [expected]
    provenance = dict(zip(ok["threshold_label"], ok["threshold_provenance"], strict=False))
    assert provenance["hypothetical_high"] == "HYPOTHETICAL"
    assert provenance["trailing180_p75"] == "DERIVED_FROM_HISTORY"


def test_attach_outcomes_sets_actuals_events_and_missing(
    issued: tuple[pd.DataFrame, pd.DataFrame], series: DemandSeries
) -> None:
    intervals, exceedance = (frame.copy() for frame in issued)
    gappy = series.to_series()
    gap = pd.Timestamp("2024-03-05")
    gappy.loc[gap] = np.nan
    gappy_series = DemandSeries.from_observations(
        [ts.date() for ts in gappy.dropna().index], gappy.dropna().to_numpy(), allow_missing=True
    )
    attach_outcomes(intervals, exceedance, gappy_series)
    on_gap = intervals["target_date"] == gap
    assert (
        intervals.loc[on_gap & (intervals["status"] != "unavailable"), "status"] == "missing_actual"
    ).all()
    observed = intervals[~on_gap & (intervals["status"] == "ok")]
    expected = gappy.reindex(pd.DatetimeIndex(observed["target_date"])).to_numpy()
    assert np.array_equal(observed["actual"].to_numpy(), expected)
    ok = exceedance[exceedance["status"] == "ok"]
    assert np.array_equal(
        ok["event"].to_numpy(), (ok["actual"] > ok["threshold_value"]).to_numpy(dtype=float)
    )
    assert exceedance.loc[exceedance["status"] != "ok", "event"].isna().all()


def test_failed_point_forecasts_are_recorded_not_dropped(
    records: pd.DataFrame, series: DemandSeries
) -> None:
    config = make_config(periods=[CONFIG.periods[1].model_dump(mode="json")], selection=None)
    broken = records.copy()
    hit = (broken["model_id"] == "med") & (broken["origin"] == pd.Timestamp("2024-01-10"))
    broken.loc[hit, ["status", "prediction"]] = ["failed", np.nan]
    intervals, _ = issue(broken, series, config, exceedance=False)
    rows = intervals[
        (intervals["model_id"] == "med") & (intervals["origin"] == pd.Timestamp("2024-01-10"))
    ]
    assert len(rows) == CONFIG.max_horizon * (len(CONFIG.pairs()) - 1)
    assert (rows["status"] == "point_failed").all()
    assert rows[Q].isna().all().all()
    # The failed forecast is not a residual either: later calibration sets are one shorter.
    other, _ = issue(records, series, config, exceedance=False)
    later = (
        (intervals["origin"] == pd.Timestamp("2024-01-25"))
        & (intervals["pair_id"] == "empirical_additive@rolling90")
        & (intervals["model_id"] == "med")
    )
    assert (
        other.loc[later, "cal_n"].to_numpy() - intervals.loc[later, "cal_n"].to_numpy() == 1
    ).all()


def test_insufficient_calibration_gives_unavailable_not_invented_intervals(
    records: pd.DataFrame, series: DemandSeries
) -> None:
    calibration = dict(CONFIG.calibration.model_dump(mode="json"), min_calibration_origins=500)
    config = make_config(calibration=calibration)
    intervals, exceedance = issue(records, series, config)
    model = intervals[intervals["model_id"] != MODEL_FREE]
    assert (model["status"] == "unavailable").all()
    assert (model["support"] == "unavailable").all()
    assert model[Q].isna().all().all()
    assert (intervals.loc[intervals["model_id"] == MODEL_FREE, "status"] == "ok").all()
    model_ex = exceedance[exceedance["model_id"] != MODEL_FREE]
    assert model_ex["probability"].isna().all()


# --- Full evaluation runs -------------------------------------------------------------------


@pytest.fixture(scope="module")
def evaluated(records: pd.DataFrame, series: DemandSeries) -> Any:
    return run_probabilistic(
        records, series, CONFIG, buckets=BUCKETS, data_delay_days=0, periods=["sel", "ev"]
    )


def test_selection_is_made_on_the_selection_period_only(
    small_run: Any, series: DemandSeries
) -> None:
    selection = small_run.payload["selection"]
    assert selection["period"] == "sel"
    assert selection["selected_pair"] == selection["ranking"][0]["pair_id"]
    wis = [r["wis"] for r in selection["ranking"]]
    assert wis == sorted(wis)
    # Replace everything after the last selection-period target: the choice is unchanged.
    last_target = SMALL.periods[0].last_origin + timedelta(days=SMALL.max_horizon)
    changed_series = perturbed_after(series, last_target, seed=9)
    changed = small(backtest_records(changed_series), changed_series)
    assert changed.payload["selection"] == selection
    assert changed.payload["decision_status"] != small_run.payload["decision_status"]


def small(records: pd.DataFrame, series: DemandSeries, **kwargs: Any) -> Any:
    return run_probabilistic(
        records, series, SMALL, buckets=BUCKETS, data_delay_days=0, periods=["sel", "ev"], **kwargs
    )


@pytest.fixture(scope="module")
def small_run(records: pd.DataFrame, series: DemandSeries) -> Any:
    return small(records, series)


def test_payload_is_deterministic_and_strict_json(
    small_run: Any, records: pd.DataFrame, series: DemandSeries
) -> None:
    again = small(records, series)
    assert json_bytes(again.payload) == json_bytes(small_run.payload)
    decoded = json.loads(json_bytes(small_run.payload))
    assert decoded["decided_pair"] == small_run.payload["selection"]["selected_pair"]
    assert decoded["decision_status"]["period"] == "ev"


def test_sealed_selection_is_used_and_checked(
    records: pd.DataFrame, series: DemandSeries, small_run: Any
) -> None:
    sealed = dict(small_run.payload["selection"])
    same = small(records, series, sealed_selection=sealed)
    assert same.payload["selection_seal_check"] == {"differences": []}
    assert json_bytes(same.payload["interval_summary"]) == json_bytes(
        small_run.payload["interval_summary"]
    )
    other = next(p.pair_id for p in SMALL.candidates if p.pair_id != sealed["selected_pair"])
    edited = {**sealed, "selected_pair": other}
    result = small(records, series, sealed_selection=edited)
    assert "selected_pair" in result.payload["selection_seal_check"]["differences"]
    assert result.payload["decided_pair"] == other
    alone = run_probabilistic(
        records,
        series,
        SMALL,
        buckets=BUCKETS,
        data_delay_days=0,
        periods=["ev"],
        sealed_selection=sealed,
    )
    assert alone.payload["selection_seal_check"] == {"differences": ["selection period not run"]}
    assert alone.payload["selection"] is None


def test_selection_differences_detects_every_decision_field(small_run: Any) -> None:
    sealed = small_run.payload["selection"]
    assert selection_differences(sealed, sealed) == []
    tweaked = json.loads(json.dumps(sealed))
    tweaked["ranking"][0]["wis"] *= 1.01
    assert selection_differences(tweaked, sealed) == ["ranking"]
    tweaked = {**sealed, "n_common": sealed["n_common"] + 1, "period": "x"}
    assert set(selection_differences(tweaked, sealed)) == {"n_common", "period"}


def test_sensitivity_and_descriptive_blocks(evaluated: Any) -> None:
    payload = evaluated.payload
    latency = payload["sensitivity"]["latency"]
    assert {r["data_latency_days"] for r in latency} == {3}
    assert {r["pair_id"] for r in latency} == {payload["decided_pair"]}
    assert payload["sensitivity"]["coverage_bandwidth"]
    strata = {r["stratum"] for r in payload["strata"]}
    assert strata == {"ordinary", "holiday_related", "flagged"}
    profile = payload["width_profile"][0]
    assert len(profile["mean_width_by_horizon"]) == CONFIG.max_horizon
    assert {r["label"] for r in payload["comparisons"]} >= {"days_1_7"}
    reference = [r for r in payload["exceedance_summary"] if r["pair_id"] == "climatology_180d"]
    assert reference
    assert all("brier_skill_score" not in r for r in reference)


def test_run_refuses_periods_without_actuals(records: pd.DataFrame, series: DemandSeries) -> None:
    short = DemandSeries.from_observations(
        [ts.date() for ts in series.to_series().index[:500]],
        series.to_series().to_numpy()[:500],
    )
    with pytest.raises(Exception, match="needs actuals"):
        run_probabilistic(
            records, short, CONFIG, buckets=BUCKETS, data_delay_days=0, periods=["ev"]
        )


# --- Selection and status rules on hand-made inputs -------------------------------------------


def hand_frame(
    scores: dict[str, list[float]], unavailable: dict[str, int] | None = None
) -> pd.DataFrame:
    """Intervals of the selection period whose WIS is known: interval [y - w, y + w]."""
    rows = []
    origins = pd.date_range("2023-07-01", periods=4, freq="D")
    for pair_id, widths in scores.items():
        for k, origin in enumerate(origins):
            for h in range(1, 8):
                width = widths[k % len(widths)]
                y = 1000.0
                status = "ok"
                if unavailable and k < unavailable.get(pair_id, 0):
                    status = "unavailable"
                row = {
                    "period": "sel",
                    "model_id": "med",
                    "pair_id": pair_id,
                    "role": "candidate",
                    "origin": origin,
                    "horizon": h,
                    "target_date": origin + pd.Timedelta(days=h),
                    "prediction": y,
                    "actual": y,
                    "status": status,
                    "support": "supported",
                    "pooling": "horizon",
                }
                for tau in GRID:
                    row[quantile_column(tau)] = y + width * (tau - 0.5) * 2
                rows.append(row)
    return pd.DataFrame(rows)


def test_selection_ranks_by_wis_with_ties_to_the_earlier_candidate() -> None:
    ids = [p.pair_id for p in CONFIG.candidates]
    frame = hand_frame({ids[0]: [10.0], ids[1]: [5.0], ids[2]: [5.0]})
    selection = select_interval_method(frame, CONFIG)
    assert selection is not None
    assert selection["selected_pair"] == ids[1]
    assert [r["pair_id"] for r in selection["ranking"]] == [ids[1], ids[2], ids[0]]
    # y at the median: WIS = sum over intervals of alpha / 2 * width, / 2.5.
    # width(80 %) = 0.8 * 2w... with w = 5: 80 % width 8, 95 % width 9.5.
    expected = (0.1 * 8.0 + 0.025 * 9.5) / 2.5
    assert selection["ranking"][0]["wis"] == pytest.approx(expected)
    assert selection["ranking"][0]["coverage_80"] == 1.0


def test_selection_excludes_candidates_with_unavailable_forecasts() -> None:
    ids = [p.pair_id for p in CONFIG.candidates]
    frame = hand_frame({ids[0]: [10.0], ids[1]: [1.0], ids[2]: [5.0]}, unavailable={ids[1]: 1})
    selection = select_interval_method(frame, CONFIG)
    assert selection is not None
    assert selection["selected_pair"] == ids[2]
    flags = {e["pair_id"]: e["eligible"] for e in selection["eligibility"]}
    assert flags == {ids[0]: True, ids[1]: False, ids[2]: True}
    nothing = hand_frame({i: [1.0] for i in ids}, unavailable=dict.fromkeys(ids, 1))
    empty = select_interval_method(nothing, CONFIG)
    assert empty is not None
    assert empty["selected_pair"] is None
    assert select_interval_method(nothing.iloc[0:0], CONFIG) is None
    assert select_interval_method(nothing, make_config(selection=None)) is None


def summary_rows(
    coverage: dict[str, tuple[float, float]], *, p: float = 0.5, eff_n: float = 50.0, **extra: Any
) -> list[dict[str, Any]]:
    """One summary row per label with the given 80 % / 95 % coverage."""
    rows = []
    for label, (c80, c95) in coverage.items():
        row = {
            "period": "ev",
            "model_id": "med",
            "pair_id": "x",
            "label": label,
            "n_evaluated": 100,
            "n_unavailable": 0,
            "n_point_failed": 0,
            "coverage_80": c80,
            "coverage_95": c95,
        }
        for key in ("80", "95"):
            row[f"coverage_p_{key}"] = p
            row[f"coverage_test_computed_{key}"] = True
            row[f"coverage_eff_n_{key}"] = eff_n
            row[f"coverage_ci_low_{key}"] = 0.0
            row[f"coverage_ci_high_{key}"] = 1.0
        row.update(extra)
        rows.append(row)
    return rows


GOOD = {
    "days_1_7": (0.81, 0.95),
    "day_1": (0.80, 0.96),
    "days_2_4": (0.79, 0.94),
    "days_5_7": (0.82, 0.95),
}


def status_of(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return interval_status(rows, CONFIG, BUCKETS, period="ev", model_id="med", pair_id="x")


def test_interval_status_decision_grade_only_when_every_criterion_holds() -> None:
    assert status_of(summary_rows(GOOD))["status"] == DECISION_GRADE


def test_interval_status_fails_when_a_bucket_is_outside_tolerance() -> None:
    bad = dict(GOOD, days_5_7=(0.70, 0.95))  # 10 pp below 80 % in one bucket
    result = status_of(summary_rows(bad))
    assert result["status"] == FAILED_CALIBRATION
    assert result["levels"][0]["scopes_outside_tolerance"] == ["days_5_7"]
    edge = dict(GOOD, days_5_7=(0.85, 0.92))  # exactly at +5 pp is inside; 92 % is -3 pp
    assert status_of(summary_rows(edge))["status"] == DECISION_GRADE


def test_interval_status_fails_when_the_test_rejects() -> None:
    result = status_of(summary_rows(GOOD, p=0.01))
    assert result["status"] == FAILED_CALIBRATION
    assert any("rejected" in r for r in result["reasons"])


def test_interval_status_insufficient_evidence() -> None:
    thin = status_of(summary_rows(GOOD, eff_n=12.0))
    assert thin["status"] == INSUFFICIENT_EVIDENCE
    assert any("effective sample size 12.0 < 30" in r for r in thin["reasons"])
    rows = summary_rows(GOOD)
    for row in rows:
        row["coverage_test_computed_80"] = False
        row["coverage_test_reason_80"] = "fewer than 50 origins"
    untested = status_of(rows)
    assert untested["status"] == INSUFFICIENT_EVIDENCE
    assert any("not computed (fewer than 50 origins)" in r for r in untested["reasons"])
    missing = status_of(summary_rows(GOOD, n_unavailable=3))
    assert missing["status"] == INSUFFICIENT_EVIDENCE
    assert status_of([])["status"] == UNAVAILABLE
    # Failing beats insufficient evidence.
    both = status_of(summary_rows(dict(GOOD, days_1_7=(0.6, 0.95)), eff_n=5.0))
    assert both["status"] == FAILED_CALIBRATION


def exceedance_row(**values: Any) -> dict[str, Any]:
    row = {
        "period": "ev",
        "model_id": "med",
        "pair_id": "x",
        "threshold_label": "trailing180_p75",
        "threshold_provenance": "DERIVED_FROM_HISTORY",
        "label": "days_1_7",
        "n_evaluated": 500,
        "n_events": 120,
        "mean_probability": 0.25,
        "event_frequency": 0.24,
        "calibration_gap": 0.01,
        "brier": 0.10,
        "brier_reference": 0.18,
        "brier_skill_score": 0.44,
        "brier_p_conservative": 0.001,
        "brier_eff_n": 60.0,
    }
    row.update(values)
    return row


def exceed_status(**values: Any) -> dict[str, Any]:
    config = make_config(thresholds=[CONFIG.thresholds[0].model_dump(mode="json")])
    rows = [exceedance_row(**values)]
    return exceedance_status(rows, config, period="ev", model_id="med", pair_id="x")[0]


def test_exceedance_status_rules() -> None:
    assert exceed_status()["status"] == DECISION_GRADE
    assert exceed_status(calibration_gap=0.08)["status"] == FAILED_CALIBRATION
    assert exceed_status(brier_skill_score=-0.1)["status"] == FAILED_CALIBRATION
    assert exceed_status(brier_p_conservative=0.2)["status"] == INSUFFICIENT_EVIDENCE
    assert exceed_status(brier_p_conservative=None)["status"] == INSUFFICIENT_EVIDENCE
    assert exceed_status(brier_eff_n=10.0)["status"] == INSUFFICIENT_EVIDENCE
    assert exceed_status(brier_skill_score=None)["status"] == INSUFFICIENT_EVIDENCE
    assert exceed_status(n_evaluated=0)["status"] == UNAVAILABLE
    hypothetical = exceed_status(threshold_provenance="HYPOTHETICAL")
    assert "HYPOTHETICAL" in hypothetical["notice"]


def test_decision_status_needs_an_evaluation_period() -> None:
    rows = summary_rows(GOOD)
    config = make_config(periods=[CONFIG.periods[0].model_dump(mode="json")])
    assert decision_status(rows, [], config, BUCKETS, "x") is None
    status = decision_status(rows, [], CONFIG, BUCKETS, "x")
    assert status is not None
    assert set(status["by_model"]) == {"med", "sn"}
    assert status["by_model"]["sn"]["intervals"]["status"] == UNAVAILABLE
    assert "not an untouched prospective test" in status["note"]


def test_empty_exceedance_frame_is_handled() -> None:
    empty = pd.DataFrame(columns=list(EXCEEDANCE_COLUMNS))
    intervals = hand_frame({"a": [1.0]})
    attach_outcomes(intervals.iloc[0:0].copy(), empty, noisy_series(START, 30, seed=1))


def test_interval_summary_counts_and_scores_exactly() -> None:
    # Four origins x 7 horizons of `a`: 80 % interval [y - 8, y + 8] around y = 1000, 95 %
    # interval [y - 9.5, y + 9.5]. Outcomes: on origin 0 every outcome is 1009 (outside the
    # 80 % interval by 1, inside the 95 %); on origins 1-3 the outcome is 1000.
    frame = hand_frame({"a": [10.0]})
    frame.loc[frame["origin"] == pd.Timestamp("2023-07-01"), "actual"] = 1009.0
    frame.loc[
        (frame["origin"] == pd.Timestamp("2023-07-04")) & (frame["horizon"] == 7), "status"
    ] = "unavailable"
    rows = interval_summary(frame, CONFIG, BUCKETS)
    overall = next(r for r in rows if r["label"] == "days_1_7")
    assert overall["n_scheduled"] == 28
    assert overall["n_unavailable"] == 1
    assert overall["n_evaluated"] == 27
    assert overall["n_origins"] == 4
    assert overall["coverage_80"] == pytest.approx(20 / 27)
    assert overall["coverage_95"] == 1.0
    assert overall["mean_width_80"] == pytest.approx(16.0)
    assert overall["mean_width_95"] == pytest.approx(19.0)
    # Winkler 80 %: width 16, plus (2 / 0.2) * 1 = 10 on the 7 misses.
    assert overall["interval_score_80"] == pytest.approx((16 * 27 + 10 * 7) / 27)
    # Pinball at the median (1000) for y = 1009: 0.5 * 9 = 4.5 on 7 forecasts, else 0.
    assert overall["pinball"]["0.5"] == pytest.approx(4.5 * 7 / 27)
    assert overall["quantile_calibration"]["0.975"] == 1.0
    assert overall["quantile_calibration"]["0.5"] == pytest.approx(20 / 27)
    assert overall["mae"] == pytest.approx(9 * 7 / 27)
    assert overall["bias"] == pytest.approx(-9 * 7 / 27)
    h7 = next(r for r in rows if r["label"] == "h07")
    assert h7["n_evaluated"] == 3
    assert h7["coverage_80"] == pytest.approx(2 / 3)
    assert overall["coverage_test_computed_80"] is False  # 4 origins cannot support a test
