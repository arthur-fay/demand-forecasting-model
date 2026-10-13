"""The scientific protocol over backtest records: metrics, selection, sensitivity (M2).

:func:`evaluate` turns a :class:`~atlas.backtest.BacktestResult` into the payload written
to ``metrics.json``. It contains, per period:

* metrics for every horizon, every configured bucket and all horizons together;
* every model against the reference (weekly seasonal naive) and against ``B*``, the best
  baseline of the selection period, on common forecasts, with Diebold-Mariano tests that
  allow for overlapping forecast windows, and an explicit table of every case where a
  regression does worse than a baseline;
* the **selection**, by the rule fixed in the configuration and applied to the selection
  (development) period only, and the **recommendation**, which the later confirmation
  period can only confirm or overturn in favour of ``B*`` (it never picks a new model);
* the primary confirmatory tests (Holm-adjusted) and pre-registered sensitivity analyses;
* comparison windows (the legacy holdout days at every horizon), the observed-input
  decomposition (what feeding predictions back costs) and fit diagnostics.

Every table states its period; descriptive periods never enter selection or tests.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final

import numpy as np
import pandas as pd

from atlas.backtest import RECORD_TYPE_BACKTEST, BacktestResult, PeriodRole
from atlas.events import holidays_between
from atlas.experiment import RECORD_TYPE_LABELS, ExperimentConfig
from atlas.metrics import (
    HorizonBucket,
    compare,
    error_metrics,
    ok_records,
    summarize,
    window_summary,
)
from atlas.models import ModelFamily
from atlas.regression import HIGH_LEVERAGE
from atlas.stats import holm

LEGACY_HEADLINE: Final = (
    "The legacy 4.93 % MAPE is a one-step-ahead (h = 1) score with observed lags on one "
    "90-day block (2026-04-02..2026-06-30), from a single fit. It is a different procedure "
    "from the recursive forecasts evaluated here; only the legacy-holdout comparison window "
    "puts the two side by side on the same target days."
)
# A list, not a mapping: the order must survive the sorted keys of metrics.json.
CATEGORY_DEFINITIONS: Final = (
    {
        "record_type": "legacy_one_step_holdout",
        "definition": "Legacy one-step holdout: single fit, horizon 1, observed lags.",
    },
    {
        "record_type": "historical_backtest",
        "definition": (
            "Historical recursive multi-horizon backtest: refit at every origin with data "
            "through that origin only; horizons 1-30 from the model's own recursion."
        ),
    },
    {
        "record_type": "future_projection",
        "definition": "Future projection: beyond the data, no observed outcome.",
    },
)


# --- Selection and recommendation ---------------------------------------------------------


def _families(result: BacktestResult) -> dict[str, str]:
    return {spec.model_id: spec.family.value for spec in result.models}


def _scope_records(records: pd.DataFrame, period: str, first: int, last: int) -> pd.DataFrame:
    return records[(records["period"] == period) & records["horizon"].between(first, last)]


def _common_maes(
    records: pd.DataFrame, models: Sequence[str], period: str, first: int, last: int
) -> tuple[dict[str, float | None], int, int]:
    """MAE of each model on the (origin, horizon) pairs every listed model got ``ok``."""
    ok = ok_records(_scope_records(records, period, first, last))
    keys = ["origin", "horizon"]
    common: pd.DataFrame | None = None
    for model_id in models:
        pairs = ok.loc[ok["model_id"] == model_id, keys]
        common = pairs if common is None else common.merge(pairs, on=keys)
    maes: dict[str, float | None] = {}
    if common is None or common.empty:
        return dict.fromkeys(models), 0, 0
    for model_id in models:
        cell = ok[ok["model_id"] == model_id].merge(common, on=keys)
        maes[model_id] = error_metrics(cell["prediction"], cell["actual"]).mae
    return maes, len(common), int(common["origin"].nunique())


def _pair_test(
    records: pd.DataFrame,
    model_id: str,
    reference: str,
    period: str,
    first: int,
    last: int,
    stride: int,
) -> dict[str, Any]:
    rows = compare(
        _scope_records(records, period, first, last),
        model_id,
        reference,
        (HorizonBucket("selection_scope", first, last),),
        last,
        {period: stride},
        horizons=False,
    )
    row = next((r for r in rows if r["label"] == "selection_scope"), None)
    return row or {"model_id": model_id, "reference": reference, "period": period}


def select_model(result: BacktestResult, config: ExperimentConfig) -> dict[str, Any] | None:
    """Apply the pre-registered selection rule to the selection period (see module doc).

    1. Candidates whose failure rate in the selection scope exceeds ``max_failure_rate``
       are ineligible (and reported).
    2. Eligible candidates are ranked by MAE on the forecasts *all* of them produced.
    3. ``B*`` is the best eligible baseline, ``R*`` the best eligible regression.
    4. ``R*`` is selected only if its MAE is at least ``regression_margin_pct`` below
       ``B*``'s *and* the selection-period DM test (conservative p-value) is below
       ``alpha`` with ``R*`` ahead; otherwise ``B*`` is selected.
    """
    rule = config.selection
    if rule is None:
        return None
    period = rule.period
    if period not in {p.name for p in result.plan.periods}:
        return None
    families = _families(result)
    first, last = rule.first_horizon, rule.last_horizon
    scope = _scope_records(result.records, period, first, last)
    eligibility = []
    eligible: list[str] = []
    for model_id in rule.candidates:
        cell = scope[scope["model_id"] == model_id]
        failed = int((cell["status"] == "failed").sum())
        rate = failed / len(cell) if len(cell) else 1.0
        ok = rate <= rule.max_failure_rate and len(cell) > 0
        eligibility.append(
            {"model_id": model_id, "scheduled": len(cell), "failed": failed, "eligible": ok}
        )
        if ok:
            eligible.append(model_id)
    maes, n_pairs, n_origins = _common_maes(result.records, eligible, period, first, last)
    order = {model_id: i for i, model_id in enumerate(rule.candidates)}
    scored = sorted(
        ((maes[m], order[m], m) for m in eligible if maes[m] is not None),
        key=lambda item: (item[0], item[1]),
    )
    ranking: list[dict[str, Any]] = [
        {"model_id": m, "family": families[m], "mae": mae} for mae, _, m in scored
    ]
    best_baseline: str | None = next(
        (m for _, _, m in scored if families[m] == ModelFamily.BASELINE.value), None
    )
    best_regression: str | None = next(
        (m for _, _, m in scored if families[m] == ModelFamily.REGRESSION.value), None
    )
    stride = result.plan.stride_by_period().get(period, 1)
    test: dict[str, Any] | None = None
    margin_met = significant = False
    if best_baseline is not None and best_regression is not None:
        test = _pair_test(
            result.records, best_regression, best_baseline, period, first, last, stride
        )
        r_mae, b_mae = maes.get(best_regression), maes.get(best_baseline)
        margin_met = (
            r_mae is not None
            and b_mae is not None
            and r_mae <= (1.0 - rule.regression_margin_pct / 100.0) * b_mae
        )
        p_value = test.get("dm_p_value_conservative")
        difference = test.get("mae_difference")
        significant = (
            p_value is not None
            and difference is not None
            and difference < 0
            and p_value < rule.alpha
        )
    selected: str | None
    if best_regression is not None and margin_met and significant:
        selected = best_regression
        reason = (
            f"{best_regression} beats the best baseline {best_baseline} by at least "
            f"{rule.regression_margin_pct:g} % in MAE and the selection-period DM test is "
            f"significant at {rule.alpha:g}."
        )
    elif best_baseline is not None:
        selected = best_baseline
        reason = (
            f"The best baseline {best_baseline} is selected: the best regression "
            f"({best_regression}) did not beat it by {rule.regression_margin_pct:g} % with a "
            f"significant selection-period DM test."
            if best_regression is not None
            else f"The best baseline {best_baseline} is selected (no eligible regression)."
        )
    else:
        selected = best_regression
        reason = "No eligible baseline; the best eligible regression is selected."
    return {
        "rule": (
            f"Pre-registered: candidates {list(rule.candidates)}; selection period {period}; "
            f"MAE over horizons {first}..{last} on forecasts common to all eligible candidates; "
            f"failure rate above {rule.max_failure_rate:g} makes a candidate ineligible; the "
            f"best regression is selected only if its MAE is at least "
            f"{rule.regression_margin_pct:g} % below the best baseline's and the DM test "
            f"(conservative p) is below {rule.alpha:g}; otherwise the best baseline."
        ),
        "period": period,
        "first_horizon": first,
        "last_horizon": last,
        "eligibility": eligibility,
        "n_common_pairs": n_pairs,
        "n_common_origins": n_origins,
        "ranking": ranking,
        "best_baseline": best_baseline,
        "best_regression": best_regression,
        "regression_vs_best_baseline": test,
        "margin_met": margin_met,
        "significant": significant,
        "selected_model": selected,
        "selected_family": families.get(selected) if selected else None,
        "reason": reason,
    }


def recommend(
    result: BacktestResult, config: ExperimentConfig, selection: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Confirm or overturn the selection on the confirmation period (pre-registered).

    The selected regression stays recommended only if its confirmation MAE (common
    forecasts) is not above ``B*``'s; otherwise ``B*`` is recommended. A selected
    baseline stays recommended. Also runs the primary confirmatory family (Holm):
    H1 ``R*`` vs ``B*``; H2 the selected model vs the reference.
    """
    rule = config.selection
    confirmation = result.plan.period_with_role(PeriodRole.CONFIRMATION)
    if rule is None or confirmation is None:
        return None
    period = confirmation.name
    first, last = rule.first_horizon, rule.last_horizon
    stride = confirmation.stride_days
    selected = selection.get("selected_model")
    best_baseline = selection.get("best_baseline")
    best_regression = selection.get("best_regression")
    families = _families(result)
    tests: list[dict[str, Any]] = []
    if best_regression and best_baseline:
        tests.append(
            {
                "hypothesis": "H1: best development regression vs best development baseline",
                **_pair_test(
                    result.records, best_regression, best_baseline, period, first, last, stride
                ),
            }
        )
    reference = config.reference_model
    if selected and selected != reference:
        tests.append(
            {
                "hypothesis": "H2: selected model vs the seasonal-naive reference",
                **_pair_test(result.records, selected, reference, period, first, last, stride),
            }
        )
    adjusted = holm([t.get("dm_p_value_conservative") for t in tests])
    for test, p_value in zip(tests, adjusted, strict=True):
        test["holm_adjusted_p_value"] = p_value
    recommended = selected
    reason = "The selected model is a baseline; it is recommended as selected."
    if selected and families.get(selected) == ModelFamily.REGRESSION.value and best_baseline:
        maes, _, _ = _common_maes(result.records, [selected, best_baseline], period, first, last)
        if maes[selected] is None or maes[best_baseline] is None:
            recommended = best_baseline
            reason = "No common confirmation forecasts; falling back to the best baseline."
        elif maes[selected] <= maes[best_baseline]:  # type: ignore[operator]
            reason = (
                f"Confirmed: {selected} is not worse than {best_baseline} on the confirmation "
                "period."
            )
        else:
            recommended = best_baseline
            reason = (
                f"Overturned: on the confirmation period {selected} does worse than the best "
                f"development baseline {best_baseline}, so the baseline is recommended."
            )
    return {
        "period": period,
        "selected_model": selected,
        "recommended_model": recommended,
        "recommended_family": families.get(recommended) if recommended else None,
        "reason": reason,
        "primary_tests": tests,
        "multiplicity": "Holm adjustment over the primary family; every other test is "
        "exploratory and unadjusted.",
        "maes_common": _common_maes(
            result.records,
            [m for m in dict.fromkeys([selected, best_baseline, best_regression, reference]) if m],
            period,
            first,
            last,
        )[0],
    }


# --- Comparisons and sensitivity ------------------------------------------------------------


def _comparisons(
    records: pd.DataFrame,
    reference: str,
    model_ids: Iterable[str],
    buckets: tuple[HorizonBucket, ...],
    max_horizon: int,
    strides: Mapping[str, int],
    *,
    horizons: bool = True,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model_id in model_ids:
        if model_id != reference:
            rows.extend(
                compare(
                    records, model_id, reference, buckets, max_horizon, strides, horizons=horizons
                )
            )
    return rows


def underperformance(
    result: BacktestResult, buckets: tuple[HorizonBucket, ...], periods: Sequence[str]
) -> list[dict[str, Any]]:
    """Every (regression, baseline, period, bucket) where the regression's MAE is higher."""
    families = _families(result)
    regressions = [m for m, f in families.items() if f == ModelFamily.REGRESSION.value]
    baselines = [m for m, f in families.items() if f == ModelFamily.BASELINE.value]
    records = result.records[result.records["period"].isin(periods)]
    strides = result.plan.stride_by_period()
    rows = []
    for regression in regressions:
        for baseline in baselines:
            for row in compare(
                records,
                regression,
                baseline,
                buckets,
                result.plan.max_horizon,
                strides,
                horizons=False,
            ):
                if row["mae_difference"] is not None and row["mae_difference"] > 0:
                    rows.append(
                        {
                            k: row[k]
                            for k in (
                                "model_id",
                                "reference",
                                "period",
                                "label",
                                "n_common",
                                "mae",
                                "mae_reference",
                                "mae_improvement_pct",
                                "dm_p_value_conservative",
                            )
                        }
                    )
    return rows


def holiday_related_dates(first: date, last: date) -> set[pd.Timestamp]:
    """Federal holidays (actual or observed) and the days before and after the actual date."""
    days: set[pd.Timestamp] = set()
    for holiday in holidays_between(first - timedelta(days=2), last + timedelta(days=2)):
        for day in (
            holiday.actual,
            holiday.observed,
            holiday.actual - timedelta(days=1),
            holiday.actual + timedelta(days=1),
        ):
            days.add(pd.Timestamp(day))
    return days


@dataclass(frozen=True)
class _ViewContext:
    buckets: tuple[HorizonBucket, ...]
    max_horizon: int
    strides: Mapping[str, int]
    reference: str
    best_baseline: str | None
    model_ids: Sequence[str]


def _bucket_view(
    records: pd.DataFrame, *, label: str, description: str, context: _ViewContext
) -> dict[str, Any]:
    buckets, max_horizon, strides = context.buckets, context.max_horizon, context.strides
    reference, best_baseline = context.reference, context.best_baseline
    model_ids = context.model_ids
    summary = [
        r for r in summarize(records, buckets, max_horizon, strides) if r["scope"] == "bucket"
    ]
    view: dict[str, Any] = {
        "label": label,
        "description": description,
        "n_records_ok": int((records["status"] == "ok").sum()),
        "summary": [
            {
                k: r[k]
                for k in ("model_id", "period", "label", "n_predictions", "mae", "rmse", "bias")
            }
            for r in summary
        ],
        "vs_reference": [
            {
                k: r[k]
                for k in (
                    "model_id",
                    "period",
                    "label",
                    "n_common",
                    "mae_improvement_pct",
                    "dm_p_value_conservative",
                )
            }
            for r in _comparisons(
                records, reference, model_ids, buckets, max_horizon, strides, horizons=False
            )
        ],
    }
    if best_baseline is not None:
        view["vs_best_baseline"] = [
            {
                k: r[k]
                for k in (
                    "model_id",
                    "period",
                    "label",
                    "n_common",
                    "mae_improvement_pct",
                    "dm_p_value_conservative",
                )
            }
            for r in _comparisons(
                records, best_baseline, model_ids, buckets, max_horizon, strides, horizons=False
            )
        ]
    return view


def _target_aligned(result: BacktestResult) -> pd.DataFrame:
    """Records whose target is reachable at every horizon from the period's origins."""
    keep = []
    for period in result.plan.periods:
        origins = pd.DatetimeIndex([pd.Timestamp(o) for o in period.origins()])
        targets = None
        for horizon in range(1, result.plan.max_horizon + 1):
            reachable = set(origins + pd.Timedelta(days=horizon))
            targets = reachable if targets is None else targets & reachable
        mask = (result.records["period"] == period.name) & result.records["target_date"].isin(
            sorted(targets or set())
        )
        keep.append(mask)
    combined = np.logical_or.reduce([m.to_numpy() for m in keep]) if keep else []
    return result.records[combined]


def _by_origin_month(records: pd.DataFrame) -> list[dict[str, Any]]:
    ok = ok_records(records).copy()
    ok["origin_month"] = ok["origin"].dt.strftime("%Y-%m")
    rows = []
    for _, cell in ok.groupby(["period", "model_id", "origin_month"], sort=True):
        metrics = error_metrics(cell["prediction"], cell["actual"])
        rows.append(
            {
                "period": str(cell["period"].iloc[0]),
                "model_id": str(cell["model_id"].iloc[0]),
                "origin_month": str(cell["origin_month"].iloc[0]),
                "n": metrics.n,
                "mae": metrics.mae,
                "bias": metrics.bias,
            }
        )
    return rows


def _influence(
    records: pd.DataFrame, model_id: str, reference: str, period: str, top: int = 10
) -> list[dict[str, Any]]:
    """Target days contributing most to ``MAE(model) - MAE(reference)`` on common forecasts."""
    ok = ok_records(records[records["period"] == period])
    keys = ["origin", "horizon"]
    a = ok.loc[ok["model_id"] == model_id, [*keys, "target_date", "prediction", "actual"]]
    b = ok.loc[ok["model_id"] == reference, [*keys, "prediction"]]
    joined = a.merge(b, on=keys, suffixes=("", "_ref"))
    if joined.empty:
        return []
    joined["contribution"] = (
        (joined["prediction"] - joined["actual"]).abs()
        - (joined["prediction_ref"] - joined["actual"]).abs()
    ) / len(joined)
    by_target = joined.groupby("target_date")["contribution"].agg(["sum", "size"])
    total = float(joined["contribution"].sum())
    order = by_target["sum"].abs().sort_values(ascending=False, kind="stable").index
    ranked = by_target.reindex(order).head(top)
    return [
        {
            "target_date": target.date().isoformat(),
            "forecasts": int(size),
            "contribution_to_mae_difference": float(value),
            "share_of_difference": float(value / total) if total else None,
        }
        for target, value, size in zip(
            pd.DatetimeIndex(ranked.index), ranked["sum"], ranked["size"], strict=True
        )
    ]


def _regression_reliability(records: pd.DataFrame) -> list[dict[str, Any]]:
    """Regression metrics with and without non-estimable or high-leverage forecasts."""
    ok = ok_records(records)
    ok = ok[ok["estimable"].notna()]
    rows = []
    for _, cell in ok.groupby(["period", "model_id"], sort=True):
        reliable = cell[(cell["estimable"].astype(bool)) & (cell["leverage"] <= HIGH_LEVERAGE)]
        everything = error_metrics(cell["prediction"], cell["actual"])
        kept = error_metrics(reliable["prediction"], reliable["actual"])
        rows.append(
            {
                "period": str(cell["period"].iloc[0]),
                "model_id": str(cell["model_id"].iloc[0]),
                "n_all": everything.n,
                "mae_all": everything.mae,
                "n_nonestimable": int((~cell["estimable"].astype(bool)).sum()),
                "n_high_leverage": int((cell["leverage"] > HIGH_LEVERAGE).sum()),
                "n_reliable": kept.n,
                "mae_reliable_only": kept.mae,
            }
        )
    return rows


def _observed_input_decomposition(result: BacktestResult) -> list[dict[str, Any]]:
    """Per model, period and horizon: recursive MAE vs the same fit on observed inputs."""
    diagnostic = result.observed_inputs
    if diagnostic.empty:
        return []
    keys = ["period", "model_id", "origin", "horizon"]
    ok = ok_records(result.records)[[*keys, "prediction", "actual"]]
    joined = ok.merge(
        diagnostic[[*keys, "prediction_observed_inputs"]].dropna(), on=keys, validate="one_to_one"
    )
    rows = []
    for _, cell in joined.groupby(["period", "model_id", "horizon"], sort=True):
        recursive = error_metrics(cell["prediction"], cell["actual"])
        observed = error_metrics(cell["prediction_observed_inputs"], cell["actual"])
        rows.append(
            {
                "period": str(cell["period"].iloc[0]),
                "model_id": str(cell["model_id"].iloc[0]),
                "horizon": int(cell["horizon"].iloc[0]),
                "n": recursive.n,
                "mae_recursive": recursive.mae,
                "mae_observed_inputs": observed.mae,
                "mape_pct_recursive": recursive.mape_pct,
                "mape_pct_observed_inputs": observed.mape_pct,
            }
        )
    return rows


def _fit_summary(fits: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for _, group in fits.groupby(["model_id", "period"], sort=True):
        ok = group[group["status"] == "ok"]
        restricted = ok["restricted_condition_number"].dropna().astype("float64")
        restricted = restricted[np.isfinite(restricted)]
        identifiable = ok["identifiable"].dropna().astype(bool)
        rows.append(
            {
                "model_id": str(group["model_id"].iloc[0]),
                "period": str(group["period"].iloc[0]),
                "n_origins": len(group),
                "n_failed": int((group["status"] == "failed").sum()),
                "failure_codes": {
                    str(k): int(v)
                    for k, v in group["failure_code"].dropna().value_counts().sort_index().items()
                },
                "n_features": _int(ok["n_features"].max()),
                "n_train_rows_min": _int(ok["n_train_rows"].min()),
                "n_train_rows_max": _int(ok["n_train_rows"].max()),
                "n_dropped_rows_total": _int(ok["n_dropped_rows"].sum()),
                "rank_min": _int(ok["rank"].min()),
                "rank_max": _int(ok["rank"].max()),
                "n_rank_deficient_fits": int((~identifiable).sum()),
                "solver_rank_disagreements": int(
                    (ok["solver_rank"].notna() & (ok["solver_rank"] != ok["rank"])).sum()
                ),
                "restricted_condition_median": _float(restricted.median()),
                "restricted_condition_max": _float(restricted.max()),
                "max_training_leverage_max": _float(ok["max_training_leverage"].max()),
                "n_nonestimable_predictions": _int(ok["n_nonestimable"].sum(min_count=1)),
                "n_high_leverage_predictions": _int(ok["n_high_leverage"].sum(min_count=1)),
                "n_clipped_predictions": _int(ok["n_clipped"].sum(min_count=1)),
                "in_sample_mae_median": _float(ok["in_sample_mae"].median()),
            }
        )
    return rows


def _int(value: Any) -> int | None:
    return None if value is None or pd.isna(value) else int(value)


def _float(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


# --- Payload ----------------------------------------------------------------------------------


def evaluate(
    result: BacktestResult,
    config: ExperimentConfig,
    *,
    data_fingerprint: str,
    sealed_selection: Mapping[str, Any] | None = None,
    flagged_dates: Sequence[date] = (),
    legacy_holdout: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The complete, deterministic metrics payload of a backtest.

    ``sealed_selection`` is the selection recorded by an earlier development run; when
    given it is used for every comparison against ``B*`` and for the recommendation, and
    a selection recomputed in this run (if the selection period was run) is reported next
    to it for verification.
    """
    records = result.records
    buckets = config.buckets()
    reference = config.reference_model
    model_ids = [spec.model_id for spec in result.models]
    strides = result.plan.stride_by_period()
    horizon = result.plan.max_horizon
    computed = select_model(result, config)
    selection = dict(sealed_selection) if sealed_selection is not None else computed
    best_baseline = selection.get("best_baseline") if selection else None
    decision_periods = [p.name for p in result.plan.periods if p.role is not PeriodRole.DESCRIPTIVE]
    decision_records = records[records["period"].isin(decision_periods)]
    payload: dict[str, Any] = {
        "record_type": RECORD_TYPE_BACKTEST,
        "label": RECORD_TYPE_LABELS[RECORD_TYPE_BACKTEST],
        "categories": CATEGORY_DEFINITIONS,
        "legacy_headline_note": LEGACY_HEADLINE,
        "experiment_id": config.experiment_id,
        "config_fingerprint": config.fingerprint(),
        "data_fingerprint": data_fingerprint,
        "max_horizon": horizon,
        "training_window": result.plan.training_window.kind,
        "training_window_days": result.plan.training_window.days,
        "data_delay_days": result.plan.data_delay_days,
        "reference_model": reference,
        "periods": [
            {
                "name": p.name,
                "role": p.role.value,
                "first_origin": p.first_origin,
                "last_origin": p.last_origin,
                "origin_stride_days": p.stride_days,
                "allow_incomplete_horizons": p.allow_incomplete_horizons,
                "n_origins": len(p.origins()),
                "first_target": p.first_origin + timedelta(days=1),
                "last_target": p.origins()[-1] + timedelta(days=horizon),
            }
            for p in result.plan.periods
        ],
        "models": [s.to_dict() | {"spec_fingerprint": s.fingerprint()} for s in result.models],
        "status_counts": result.status_counts(),
        "summary": summarize(records, buckets, horizon, strides),
        "comparison_to_reference": _comparisons(
            records, reference, model_ids, buckets, horizon, strides
        ),
        "selection": selection,
        "selection_recomputed": computed if sealed_selection is not None else None,
    }
    payload["comparison_to_best_baseline"] = (
        _comparisons(records, best_baseline, model_ids, buckets, horizon, strides)
        if best_baseline
        else []
    )
    payload["underperformance"] = underperformance(result, buckets, decision_periods)
    payload["recommendation"] = recommend(result, config, selection) if selection else None
    sensitivity: list[dict[str, Any]] = []
    view = _ViewContext(buckets, horizon, strides, reference, best_baseline, model_ids)
    if config.sensitivity.exclude_flagged_targets:
        flagged = {pd.Timestamp(d) for d in flagged_dates}
        kept = decision_records[~decision_records["target_date"].isin(sorted(flagged))]
        entry = _bucket_view(
            kept,
            label="exclude_flagged_targets",
            description="Target days covered by a warning-level M1 data-quality flag removed.",
            context=view,
        )
        entry["excluded_target_dates"] = sorted(d.date().isoformat() for d in flagged)
        sensitivity.append(entry)
    for exclusion in config.sensitivity.origin_exclusions:
        span = decision_records["origin"].between(
            pd.Timestamp(exclusion.first_origin), pd.Timestamp(exclusion.last_origin)
        )
        entry = _bucket_view(
            decision_records[~span],
            label=exclusion.label,
            description=exclusion.reason,
            context=view,
        )
        entry["excluded_origins"] = [
            exclusion.first_origin.isoformat(),
            exclusion.last_origin.isoformat(),
        ]
        sensitivity.append(entry)
    if config.sensitivity.holiday_stratum and not decision_records.empty:
        holidays = holiday_related_dates(
            decision_records["target_date"].min().date(),
            decision_records["target_date"].max().date(),
        )
        related = decision_records["target_date"].isin(sorted(holidays))
        for label, mask, text in (
            (
                "holiday_related_targets",
                related,
                "Targets on a federal holiday, its observed day, or the day before or after.",
            ),
            ("other_targets", ~related, "Every other target day."),
        ):
            sensitivity.append(
                _bucket_view(decision_records[mask], label=label, description=text, context=view)
            )
    aligned = _target_aligned(result)
    sensitivity.append(
        _bucket_view(
            aligned[aligned["period"].isin(decision_periods)],
            label="target_aligned",
            description=(
                "Only targets reachable at every horizon, so all horizons share one set of days."
            ),
            context=view,
        )
    )
    payload["sensitivity"] = sensitivity
    payload["by_origin_month"] = _by_origin_month(decision_records)
    influence = []
    if selection and best_baseline:
        others = [
            m for m in (selection.get("best_regression"), reference) if m and m != best_baseline
        ]
        for model_id in others:
            for period in decision_periods:
                influence.append(
                    {
                        "model_id": model_id,
                        "reference": best_baseline,
                        "period": period,
                        "top_target_dates": _influence(records, model_id, best_baseline, period),
                    }
                )
    payload["influence"] = influence
    payload["regression_reliability"] = _regression_reliability(records)
    windows = []
    for window in config.comparison_windows:
        if window.period not in {p.name for p in result.plan.periods}:
            continue
        rows = window_summary(
            records,
            first_target=pd.Timestamp(window.first_target),
            last_target=pd.Timestamp(window.last_target),
            period=window.period,
        )
        days = (window.last_target - window.first_target).days + 1
        scored = {(r["model_id"], r["horizon"]): r for r in rows}
        complete = all(
            (row := scored.get((model_id, h))) is not None and row["n_targets"] == days == row["n"]
            for model_id in model_ids
            for h in range(1, horizon + 1)
        )
        entry = {
            "label": window.label,
            "kind": window.kind,
            "description": window.description,
            "period": window.period,
            "first_target": window.first_target,
            "last_target": window.last_target,
            "n_target_days": days,
            "complete_at_every_horizon": complete,
            "rows": rows,
        }
        if window.kind == "legacy_holdout" and legacy_holdout is not None:
            entry["legacy_one_step_holdout"] = legacy_holdout
        windows.append(entry)
    payload["comparison_windows"] = windows
    payload["observed_input_decomposition"] = _observed_input_decomposition(result)
    payload["fit_summary"] = _fit_summary(result.fits)
    return payload
