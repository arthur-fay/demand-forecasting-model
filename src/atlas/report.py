"""Markdown report of a backtest, rendered deterministically from its metrics payload (M2).

The report contains no timestamps, paths or git data (those live in ``manifest.json``),
so the same results always give the same bytes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

import numpy as np

KEY_HORIZONS: Final = (1, 2, 7, 14, 21, 30)

CAVEATS: Final = (
    "All numbers are historical re-forecasts from one frozen snapshot (`legacy-v0`, a single "
    "final data vintage). Values of recent days may have been revised after each origin, "
    "which favours methods that lean on the latest days; the size of this effect is unknown.",
    "Neither period is an untouched prospective sample: the whole series was examined by the "
    "original author and in M0/M1 before any model was specified, and the confirmation "
    "period contains the legacy holdout.",
    "Forecasts from neighbouring origins share target days. Counts of forecasts overstate "
    "the information; p-values use HAC variances and are indicative. Selection-period "
    "p-values are optimistic because the selection was made on the same data.",
    "MASE below 1 is not skill against seasonal naive (its scale is the in-sample one-step "
    "error); use the improvement columns, which compare the same forecasts.",
    "MAPE is secondary and not comparable with the legacy 4.93 % except in the "
    "legacy-holdout window table, where target days and formula are the same.",
)


def _fmt(value: Any, digits: int = 1) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _p(value: Any) -> str:
    if value is None:
        return "n/a"
    return "<0.001" if value < 0.001 else f"{value:.3f}"


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def _index(
    rows: Sequence[Mapping[str, Any]], *keys: str
) -> dict[tuple[Any, ...], Mapping[str, Any]]:
    return {tuple(row[k] for k in keys): row for row in rows}


def _selection(payload: Mapping[str, Any]) -> list[str]:
    selection = payload.get("selection")
    lines = ["", "## Selection (development period, pre-registered rule)", ""]
    if not selection:
        return [*lines, "No selection rule in this configuration."]
    lines += [selection["rule"], ""]
    lines += _table(
        ["candidate", "scheduled", "failed", "eligible"],
        [
            [r["model_id"], _fmt(r["scheduled"]), _fmt(r["failed"]), _fmt(r["eligible"])]
            for r in selection["eligibility"]
        ],
    )
    lines += [
        "",
        f"Ranking on {_fmt(selection['n_common_pairs'])} forecasts common to every eligible "
        f"candidate ({_fmt(selection['n_common_origins'])} origins):",
        "",
    ]
    lines += _table(
        ["rank", "model", "family", "MAE"],
        [
            [str(i + 1), r["model_id"], r["family"], _fmt(r["mae"])]
            for i, r in enumerate(selection["ranking"])
        ],
    )
    test = selection.get("regression_vs_best_baseline")
    if test:
        lines += [
            "",
            f"Best regression `{selection['best_regression']}` vs best baseline "
            f"`{selection['best_baseline']}`: MAE difference {_fmt(test.get('mae_difference'))} "
            f"(95 % HAC CI, conservative, {_fmt(test.get('mae_difference_ci_low'))} to "
            f"{_fmt(test.get('mae_difference_ci_high'))}), improvement "
            f"{_fmt(test.get('mae_improvement_pct'))} %, DM p (conservative) "
            f"{_p(test.get('dm_p_value_conservative'))}; margin met: "
            f"{_fmt(selection['margin_met'])}; significant: {_fmt(selection['significant'])}.",
        ]
    lines += ["", f"**Selected: `{selection['selected_model']}`.** {selection['reason']}"]
    check = payload.get("selection_seal_check")
    if check is not None:
        differences = check["differences"]
        lines += [
            "",
            "The selection was sealed before this run; recomputing it from this run's "
            "development records reproduces it in every decision field: "
            + (
                "yes." if not differences else "**no** (differs in " + ", ".join(differences) + ")."
            ),
        ]
    return lines


def _recommendation(payload: Mapping[str, Any]) -> list[str]:
    recommendation = payload.get("recommendation")
    if not recommendation:
        return []
    lines = [
        "",
        f"## Recommendation (confirmation period `{recommendation['period']}`)",
        "",
        f"**Recommended: `{recommendation['recommended_model']}`.** {recommendation['reason']}",
        "",
        recommendation["multiplicity"],
        "",
    ]
    lines += _table(
        [
            "hypothesis",
            "model",
            "vs",
            "MAE diff",
            "95 % CI (conservative)",
            "improvement %",
            "DM p (conservative)",
            "Holm p",
        ],
        [
            [
                t["hypothesis"],
                t.get("model_id", ""),
                t.get("reference", ""),
                _fmt(t.get("mae_difference")),
                f"{_fmt(t.get('mae_difference_ci_low'))} to "
                f"{_fmt(t.get('mae_difference_ci_high'))}",
                _fmt(t.get("mae_improvement_pct")),
                _p(t.get("dm_p_value_conservative")),
                _p(t.get("holm_adjusted_p_value")),
            ]
            for t in recommendation["primary_tests"]
        ],
    )
    return lines


def _period_tables(payload: Mapping[str, Any], period: str) -> list[str]:
    summary = [r for r in payload["summary"] if r["period"] == period]
    versus_ref = _index(payload["comparison_to_reference"], "model_id", "period", "label")
    versus_best = _index(payload["comparison_to_best_baseline"], "model_id", "period", "label")
    best = (payload.get("selection") or {}).get("best_baseline")
    models = [m["model_id"] for m in payload["models"]]
    labels = list(dict.fromkeys(r["label"] for r in summary if r["scope"] == "bucket"))
    lines = [
        "",
        f"## {period}: accuracy by horizon bucket",
        "",
        "MAE, RMSE and bias in requests per day (bias = mean of prediction minus actual, "
        "with a 95 % HAC interval from the larger of the HLN and Bartlett-2h standard "
        "errors); WAPE and MAPE in %. `vs SN` is the MAE improvement over "
        "seasonal naive on common forecasts"
        + (f", `vs B*` over the best development baseline (`{best}`)" if best else "")
        + "; positive = better. `p` = DM p-value (conservative; n/a when too few origins).",
        "",
    ]
    rows = []
    for label in labels:
        for model in models:
            cell = next(
                (r for r in summary if r["label"] == label and r["model_id"] == model), None
            )
            if cell is None:
                continue
            ref = versus_ref.get((model, period, label))
            vs_best = versus_best.get((model, period, label))
            rows.append(
                [
                    label,
                    model,
                    _fmt(cell["n_origins"]),
                    _fmt(cell["n_predictions"]),
                    _fmt(cell["n_failed"]),
                    _fmt(cell["mae"]),
                    _fmt(cell["rmse"]),
                    f"{_fmt(cell['bias'])} [{_fmt(cell.get('bias_ci_low'))}, "
                    f"{_fmt(cell.get('bias_ci_high'))}]",
                    _fmt(cell["wape_pct"], 2),
                    _fmt(cell["mase"], 3),
                    _fmt(cell["mape_pct"], 2),
                    _fmt(ref["mae_improvement_pct"]) if ref else "ref",
                    _p(ref["dm_p_value_conservative"]) if ref else "",
                    _fmt(vs_best["mae_improvement_pct"])
                    if vs_best
                    else ("B*" if best == model else ""),
                    _p(vs_best["dm_p_value_conservative"]) if vs_best else "",
                ]
            )
    lines += _table(
        [
            "bucket",
            "model",
            "origins",
            "forecasts",
            "failed",
            "MAE",
            "RMSE",
            "bias [95 % CI]",
            "WAPE %",
            "MASE",
            "MAPE %",
            "vs SN %",
            "p",
            "vs B* %",
            "p",
        ],
        rows,
    )
    lines += ["", f"### {period}: MAE by horizon", ""]
    horizon_rows = []
    for horizon in sorted({r["first_horizon"] for r in summary if r["scope"] == "horizon"}):
        row = [str(horizon)]
        for model in models:
            cell = next(
                (
                    r
                    for r in summary
                    if r["scope"] == "horizon"
                    and r["model_id"] == model
                    and r["first_horizon"] == horizon
                ),
                None,
            )
            row.append(_fmt(cell["mae"]) if cell else "n/a")
        horizon_rows.append(row)
    lines += _table(["h", *models], horizon_rows)
    return lines


def _windows(payload: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for window in payload["comparison_windows"]:
        lines += [
            "",
            f"## Comparison window: {window['label']} ({window['period']})",
            "",
            f"{window['description']} Targets {window['first_target']}..{window['last_target']} "
            f"({window['n_target_days']} days); every horizon scored on all of them: "
            f"{_fmt(window['complete_at_every_horizon'])}. Here MAPE uses the same formula and "
            "the same target days as the legacy number; the procedures differ as labelled.",
            "",
        ]
        legacy = window.get("legacy_one_step_holdout")
        rows = []
        if legacy:
            for row in legacy["rows"]:
                rows.append(
                    [
                        row["label"],
                        "1",
                        _fmt(row["n"]),
                        _fmt(row["mae"], 2),
                        _fmt(row["rmse"], 2),
                        _fmt(row["mape_pct"], 2),
                    ]
                )
        for row in window["rows"]:
            if row["horizon"] in KEY_HORIZONS:
                rows.append(
                    [
                        f"backtest `{row['model_id']}` (refit per origin)",
                        str(row["horizon"]),
                        _fmt(row["n"]),
                        _fmt(row["mae"], 2),
                        _fmt(row["rmse"], 2),
                        _fmt(row["mape_pct"], 2),
                    ]
                )
        lines += _table(["procedure", "h", "forecasts", "MAE", "RMSE", "MAPE %"], rows)
    return lines


def _decomposition(payload: Mapping[str, Any]) -> list[str]:
    rows = payload["observed_input_decomposition"]
    if not rows:
        return []
    lines = [
        "",
        "## What recursion costs (observed-input diagnostic)",
        "",
        "Each origin's fitted regression applied to the *observed* previous days instead of its "
        "own predictions (the legacy holdout's procedure, with coefficients h - 1 days old). It "
        "reads actuals after the origin, so it is diagnostic only. At h = 1 both are the same.",
        "",
    ]
    lines += _table(
        [
            "period",
            "model",
            "h",
            "forecasts",
            "MAE recursive",
            "MAE observed inputs",
            "MAPE % recursive",
            "MAPE % observed inputs",
        ],
        [
            [
                r["period"],
                r["model_id"],
                str(r["horizon"]),
                _fmt(r["n"]),
                _fmt(r["mae_recursive"]),
                _fmt(r["mae_observed_inputs"]),
                _fmt(r["mape_pct_recursive"], 2),
                _fmt(r["mape_pct_observed_inputs"], 2),
            ]
            for r in rows
            if r["horizon"] in KEY_HORIZONS
        ],
    )
    return lines


def _underperformance(payload: Mapping[str, Any]) -> list[str]:
    rows = payload["underperformance"]
    lines = ["", "## Where a regression does worse than a baseline", ""]
    if not rows:
        return [*lines, "No regression has a higher MAE than any baseline in any bucket."]
    lines += [
        "Every bucket and period in which a regression's MAE on common forecasts is higher "
        "than a baseline's (exploratory, unadjusted p-values).",
        "",
    ]
    lines += _table(
        [
            "period",
            "bucket",
            "regression",
            "baseline",
            "forecasts",
            "MAE",
            "baseline MAE",
            "improvement %",
            "p",
        ],
        [
            [
                r["period"],
                r["label"],
                r["model_id"],
                r["reference"],
                _fmt(r["n_common"]),
                _fmt(r["mae"]),
                _fmt(r["mae_reference"]),
                _fmt(r["mae_improvement_pct"]),
                _p(r["dm_p_value_conservative"]),
            ]
            for r in rows
        ],
    )
    return lines


def _sensitivity(payload: Mapping[str, Any]) -> list[str]:
    lines = ["", "## Sensitivity analyses (pre-registered, descriptive)", ""]
    for view in payload["sensitivity"]:
        lines += [f"### {view['label']}", "", view["description"], ""]
        versus = view.get("vs_best_baseline") or view["vs_reference"]
        reference = "best baseline" if view.get("vs_best_baseline") else "seasonal naive"
        summary = _index(view["summary"], "model_id", "period", "label")
        rows = []
        for r in versus:
            cell = summary.get((r["model_id"], r["period"], r["label"]))
            rows.append(
                [
                    r["period"],
                    r["label"],
                    r["model_id"],
                    _fmt(r["n_common"]),
                    _fmt(cell["mae"]) if cell else "n/a",
                    _fmt(r["mae_improvement_pct"]),
                    _p(r["dm_p_value_conservative"]),
                ]
            )
        lines += _table(
            ["period", "bucket", "model", "forecasts", "MAE", f"vs {reference} %", "p"], rows
        )
        lines.append("")
    return lines


def _fits(payload: Mapping[str, Any]) -> list[str]:
    lines = ["", "## Fit diagnostics", ""]
    lines += _table(
        [
            "model",
            "period",
            "fits",
            "failed",
            "train rows",
            "rank",
            "rank-deficient fits",
            "restricted cond. (median / max)",
            "max train leverage",
            "non-estimable",
            "high leverage",
            "clipped",
        ],
        [
            [
                r["model_id"],
                r["period"],
                _fmt(r["n_origins"]),
                _fmt(r["n_failed"]),
                f"{_fmt(r['n_train_rows_min'])}..{_fmt(r['n_train_rows_max'])}",
                f"{_fmt(r['rank_min'])}..{_fmt(r['rank_max'])} of {_fmt(r['n_features'])}",
                _fmt(r["n_rank_deficient_fits"]),
                f"{_fmt(r['restricted_condition_median'])} / {_fmt(r['restricted_condition_max'])}",
                _fmt(r["max_training_leverage_max"], 3),
                _fmt(r["n_nonestimable_predictions"]),
                _fmt(r["n_high_leverage_predictions"]),
                _fmt(r["n_clipped_predictions"]),
            ]
            for r in payload["fit_summary"]
            if r["rank_max"] is not None
        ],
    )
    reliability = payload["regression_reliability"]
    if reliability:
        comparator = next((r.get("comparator") for r in reliability if r.get("comparator")), None)
        lines += [
            "",
            "Regression MAE with and without non-estimable or high-leverage forecasts"
            + (
                f"; `B*` (`{comparator}`) on exactly the same kept and removed forecasts:"
                if comparator
                else ":"
            ),
            "",
        ]
        lines += _table(
            [
                "period",
                "model",
                "forecasts",
                "MAE",
                "non-estimable",
                "high leverage",
                "kept",
                "MAE kept",
                "B* kept",
                "removed",
                "MAE removed",
                "B* removed",
            ],
            [
                [
                    r["period"],
                    r["model_id"],
                    _fmt(r["n_all"]),
                    _fmt(r["mae_all"]),
                    _fmt(r["n_nonestimable"]),
                    _fmt(r["n_high_leverage"]),
                    _fmt(r["n_reliable"]),
                    _fmt(r["mae_reliable_only"]),
                    _fmt(r.get("comparator_mae_reliable_pairs")),
                    _fmt(r.get("n_removed_pairs")),
                    _fmt(r.get("mae_removed_pairs")),
                    _fmt(r.get("comparator_mae_removed_pairs")),
                ]
                for r in reliability
            ],
        )
    return lines


def _bandwidth(payload: Mapping[str, Any]) -> list[str]:
    rows = payload.get("bandwidth_sensitivity") or []
    if not rows:
        return []
    lines = [
        "",
        "## Bandwidth sensitivity of the selection and primary tests (post hoc)",
        "",
        "Added after the independent review; descriptive only (the selection and the "
        "recommendation use the pre-registered test). The primary HAC variance truncates "
        "at lag h - 1; wider bandwidths allow for dependence beyond shared target days. "
        "HLN factor and t(T - 1) throughout; eff. n = T * gamma_0 / long-run variance.",
        "",
    ]
    lines += _table(
        [
            "test",
            "period",
            "model",
            "vs",
            "mean diff",
            "kernel",
            "bandwidth",
            "p",
            "CI ±",
            "eff. n",
        ],
        [
            [
                r["test"],
                r["period"],
                r["model_id"],
                r["reference"],
                _fmt(r["mean_difference"]),
                v["kernel"],
                str(v["bandwidth"]),
                _p(v["p_value"]),
                _fmt(v["ci_half_width"]),
                _fmt(v["effective_sample_size"]),
            ]
            for r in rows
            for v in r["variants"]
        ],
    )
    return lines


def render_report(payload: Mapping[str, Any]) -> str:
    """The full Markdown report (deterministic in ``payload``)."""
    lines = [
        f"# Backtest report: {payload['experiment_id']}",
        "",
        f"**{payload['label']}**",
        "",
        payload["legacy_headline_note"],
        "",
        "Result categories used in ATLAS:",
        "",
        *(f"- `{c['record_type']}`: {c['definition']}" for c in payload["categories"]),
        "",
        f"- config fingerprint: `{payload['config_fingerprint']}`",
        f"- data fingerprint: `{payload['data_fingerprint']}`",
        f"- horizons 1..{payload['max_horizon']}; training window {payload['training_window']}; "
        f"data delay {payload['data_delay_days']} day(s); reference `{payload['reference_model']}`",
        "- provenance (git commit, versions, run time): see `manifest.json`",
        "",
        "## Periods",
        "",
    ]
    lines += _table(
        ["period", "role", "origins", "first origin", "last origin", "targets"],
        [
            [
                p["name"],
                p["role"],
                _fmt(p["n_origins"]),
                str(p["first_origin"]),
                str(p["last_origin"]),
                f"{p['first_target']}..{p['last_target']}",
            ]
            for p in payload["periods"]
        ],
    )
    lines += ["", "## Forecast status", ""]
    statuses = sorted(
        {s for models in payload["status_counts"].values() for c in models.values() for s in c}
    )
    lines += _table(
        ["period", "model", *statuses],
        [
            [period, model, *(_fmt(counts.get(s, 0)) for s in statuses)]
            for period, models in sorted(payload["status_counts"].items())
            for model, counts in sorted(models.items())
        ],
    )
    lines += _selection(payload)
    lines += _recommendation(payload)
    for period in payload["periods"]:
        lines += _period_tables(payload, period["name"])
    lines += _underperformance(payload)
    lines += _windows(payload)
    lines += _decomposition(payload)
    lines += _sensitivity(payload)
    lines += _bandwidth(payload)
    lines += _fits(payload)
    lines += ["", "## Caveats", "", *(f"- {c}" for c in CAVEATS)]
    return "\n".join(lines) + "\n"
