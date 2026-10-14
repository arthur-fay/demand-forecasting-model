"""Markdown report of an M3 run, rendered deterministically from its metrics payload.

Like the M2 report, it contains no timestamps, paths or git data, so the same results
always give the same bytes, and it can be regenerated from ``metrics.json`` alone.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from atlas.report import _fmt, _p, _table

KEY_HORIZONS: Final = (1, 2, 7, 14, 21, 30)
LABEL: Final = (
    "HISTORICAL PROBABILISTIC BACKTEST: prediction intervals and exceedance probabilities "
    "re-issued at past origins from the residuals observable at each origin, and scored "
    "against recorded actuals. Not prospective validation."
)


def _pct(value: Any, digits: int = 1) -> str:
    return "n/a" if value is None else f"{100.0 * value:.{digits}f} %"


def _ci_pct(row: Mapping[str, Any], key: str) -> str:
    low, high = row.get(f"coverage_ci_low_{key}"), row.get(f"coverage_ci_high_{key}")
    if low is None or high is None:
        return f"{_pct(row.get(f'coverage_{key}'))} [n/a]"
    return f"{_pct(row.get(f'coverage_{key}'))} [{100 * low:.1f}, {100 * high:.1f}]"


def _interval(low: Any, high: Any) -> str:
    return f"[{_fmt(low)}, {_fmt(high)}]"


def _levels(payload: Mapping[str, Any]) -> list[str]:
    return [f"{level * 100:g}" for level in payload["protocol"]["levels"]]


def _protocol(payload: Mapping[str, Any]) -> list[str]:
    protocol = payload["protocol"]
    lines = ["## Protocol", ""]
    lines.append(
        f"- Point models: {', '.join(f'`{m}`' for m in protocol['point_models'])} (primary "
        f"`{protocol['primary_model']}`, the M2 recommendation)."
    )
    lines.append(
        f"- Central intervals {', '.join(k + ' %' for k in _levels(payload))}; quantile grid "
        f"{', '.join(f'{q:g}' for q in protocol['quantile_levels'])}."
    )
    lines.append(
        f"- Eligibility: {protocol['eligibility_rule']}; data latency "
        f"{protocol['data_latency_days']} day(s) (M2 data delay {protocol['data_delay_days']})."
    )
    lines.append(
        f"- A horizon is calibrated from its own residuals if they come from at least "
        f"{protocol['min_calibration_origins']} distinct origins, otherwise "
        + (
            "from its predeclared horizon bucket, "
            if protocol["pooling"] == "horizon_then_bucket"
            else ""
        )
        + "otherwise not at all; an interval whose calibration residuals have a HAC effective "
        f"sample size below {protocol['min_effective_calibration']:g} is labelled `thin`."
    )
    lines.append(f"- Candidates: {', '.join(f'`{c}`' for c in protocol['candidates'])}.")
    lines.append(f"- Baselines: {', '.join(f'`{c}`' for c in protocol['baselines'])}.")
    rows = [
        [p["name"], p["role"], str(p["first_origin"]), str(p["last_origin"]), p["description"]]
        for p in protocol["periods"]
    ]
    lines += ["", *_table(["period", "role", "first origin", "last origin", "use"], rows), ""]
    lines += ["Thresholds:", ""]
    for threshold in payload["thresholds"]:
        if threshold["provenance"] == "HYPOTHETICAL":
            lines.append(
                f"- `{threshold['label']}` = {threshold['value']:,.0f} {threshold['units']}, "
                f"**HYPOTHETICAL**. Assumptions: {threshold['assumptions']} "
                f"{threshold['notice']}"
            )
        else:
            lines.append(
                f"- `{threshold['label']}`: {threshold['description']} "
                f"({threshold['provenance']}; {threshold['notice']})"
            )
    return [*lines, ""]


def _selection(payload: Mapping[str, Any]) -> list[str]:
    selection = payload.get("selection")
    lines = ["## Interval-method selection (selection period only)", ""]
    if not selection:
        decided = payload.get("decided_pair")
        lines.append(
            f"The selection period was not run here; the sealed choice `{decided}` is used."
            if decided
            else "No selection rule in this configuration."
        )
        return [*lines, ""]
    lines.append(selection["rule"])
    lines.append("")
    keys = _levels(payload)
    rows = [
        [
            str(rank),
            f"`{r['pair_id']}`",
            _fmt(r["wis"]),
            *(_pct(r.get(f"coverage_{k}")) for k in keys),
            *(_fmt(r.get(f"mean_width_{k}")) for k in keys),
        ]
        for rank, r in enumerate(selection["ranking"], start=1)
    ]
    headers = [
        "rank",
        "candidate",
        "mean WIS",
        *(f"coverage {k} %" for k in keys),
        *(f"mean width {k} %" for k in keys),
    ]
    lines += _table(headers, rows)
    lines += [
        "",
        f"{selection['n_common']:,} common forecasts of `{selection['point_model']}`, horizons "
        f"{selection['first_horizon']}-{selection['last_horizon']}. **Selected: "
        f"`{selection['selected_pair']}`** ({selection['reason']}). These selection-period "
        "scores are optimistic for the winner.",
    ]
    check = payload.get("selection_seal_check")
    if check is not None:
        differences = check.get("differences") or []
        lines.append(
            "Re-running the selection period reproduced the sealed selection in every decision "
            "field."
            if not differences
            else "**The re-run selection DIFFERS from the seal** in: " + ", ".join(differences)
        )
    return [*lines, ""]


def _status(payload: Mapping[str, Any]) -> list[str]:
    status = payload.get("decision_status")
    if not status:
        return []
    lines = ["## Decision status (evaluation period)", ""]
    lines.append(
        f"**{status['interval_status'].upper()}** for `{status['primary_model']}` with "
        f"`{status['selected_pair']}`. {status['statement']}"
    )
    lines += ["", status["note"], ""]
    for model_id, block in status["by_model"].items():
        intervals = block["intervals"]
        lines.append(f"`{model_id}` intervals: **{intervals['status']}**")
        for reason in intervals["reasons"]:
            lines.append(f"- {reason}")
        rows = [
            [
                f"{lv['level'] * 100:g} %",
                _pct(lv["coverage"]),
                f"[{_pct(lv['coverage_ci_low'])}, {_pct(lv['coverage_ci_high'])}]",
                f"+/-{lv['tolerance_pct']:g} pp",
                ", ".join(lv["scopes_outside_tolerance"]) or "none",
                _p(lv["p_value_conservative"]),
                _fmt(lv["effective_sample_size"]),
            ]
            for lv in intervals["levels"]
        ]
        if rows:
            lines += [
                "",
                *_table(
                    [
                        "level",
                        "coverage days 1-30",
                        "95 % HAC interval",
                        "tolerance",
                        "scopes outside tolerance",
                        "p (coverage = level)",
                        "eff. n",
                    ],
                    rows,
                ),
            ]
        exceed_rows = [
            [
                f"`{e['threshold_label']}`",
                e.get("threshold_provenance", "n/a"),
                f"**{e['status']}**",
                _fmt(e.get("brier"), 4),
                _fmt(e.get("brier_reference"), 4),
                _fmt(e.get("brier_skill_score"), 3),
                _p(e.get("p_value_conservative")),
                _fmt(e.get("mean_probability"), 3),
                _fmt(e.get("event_frequency"), 3),
                _fmt(e.get("effective_sample_size")),
            ]
            for e in block["exceedance"]
        ]
        if exceed_rows:
            lines += [
                "",
                *_table(
                    [
                        "threshold",
                        "provenance",
                        "status",
                        "Brier",
                        "Brier climatology",
                        "skill",
                        "p",
                        "mean p",
                        "observed freq.",
                        "eff. n",
                    ],
                    exceed_rows,
                ),
            ]
        lines.append("")
    return lines


def _focus_pairs(payload: Mapping[str, Any]) -> list[str]:
    decided = payload.get("decided_pair")
    focus = [decided] if decided else list(payload["protocol"]["candidates"])
    return [*focus, *payload["protocol"]["baselines"]]


def _coverage_tables(payload: Mapping[str, Any]) -> list[str]:
    keys = _levels(payload)
    focus = _focus_pairs(payload)
    lines = ["## Coverage, width and scores by horizon bucket", ""]
    lines.append(
        "Coverage with its 95 % HAC interval (one value per origin, overlap = the bucket's "
        "last horizon); widths and scores in requests per day. `eff. n` is the HAC effective "
        "sample size of the per-origin coverage series."
    )
    for period in payload["periods_run"]:
        rows = []
        for row in payload["interval_summary"]:
            if row["period"] != period or row["scope"] != "bucket" or row["pair_id"] not in focus:
                continue
            rows.append(
                [
                    f"`{row['model_id']}`",
                    f"`{row['pair_id']}`",
                    row["label"],
                    _fmt(row["n_evaluated"]),
                    *(_ci_pct(row, k) for k in keys),
                    *(_fmt(row.get(f"mean_width_{k}")) for k in keys),
                    _fmt(row.get(f"interval_score_{keys[-1]}")),
                    _fmt(row.get("wis")),
                    _fmt(row.get(f"coverage_eff_n_{keys[-1]}")),
                ]
            )
        headers = [
            "model",
            "method",
            "bucket",
            "forecasts",
            *(f"coverage {k} %" for k in keys),
            *(f"width {k} %" for k in keys),
            f"Winkler {keys[-1]} %",
            "WIS",
            f"eff. n ({keys[-1]} %)",
        ]
        lines += ["", f"### {period}", "", *_table(headers, rows)]
    return [*lines, ""]


def _horizon_table(payload: Mapping[str, Any]) -> list[str]:
    decided = payload.get("decided_pair")
    if not decided:
        return []
    keys = _levels(payload)
    lines = [f"## By horizon: `{decided}`", ""]
    for period in payload["periods_run"]:
        rows = [
            [
                f"`{r['model_id']}`",
                str(r["first_horizon"]),
                *(_pct(r.get(f"coverage_{k}")) for k in keys),
                *(_fmt(r.get(f"mean_width_{k}")) for k in keys),
                _fmt(r.get("wis")),
                _fmt(r.get("mae")),
            ]
            for r in payload["interval_summary"]
            if r["period"] == period
            and r["scope"] == "horizon"
            and r["pair_id"] == decided
            and r["first_horizon"] in KEY_HORIZONS
        ]
        headers = [
            "model",
            "h",
            *(f"coverage {k} %" for k in keys),
            *(f"width {k} %" for k in keys),
            "WIS",
            "MAE",
        ]
        lines += [f"### {period}", "", *_table(headers, rows), ""]
    return lines


def _candidates(payload: Mapping[str, Any]) -> list[str]:
    keys = _levels(payload)
    all_label = f"days_1_{payload['protocol']['max_horizon']}"
    lines = ["## Every candidate and baseline, all horizons", ""]
    lines.append(
        "Descriptive. Only the selection period chooses; evaluation-period numbers of "
        "candidates that were not selected decide nothing."
    )
    for period in payload["periods_run"]:
        rows = [
            [
                f"`{r['model_id']}`",
                f"`{r['pair_id']}`",
                r["role"],
                *(_pct(r.get(f"coverage_{k}")) for k in keys),
                *(_fmt(r.get(f"mean_width_{k}")) for k in keys),
                _fmt(r.get("wis")),
                _fmt(r.get("mean_pinball")),
                _fmt(r.get("n_unavailable")),
                _fmt(r.get("n_thin")),
            ]
            for r in payload["interval_summary"]
            if r["period"] == period and r["label"] == all_label
        ]
        headers = [
            "model",
            "method",
            "role",
            *(f"coverage {k} %" for k in keys),
            *(f"width {k} %" for k in keys),
            "WIS",
            "mean pinball",
            "unavailable",
            "thin",
        ]
        lines += ["", f"### {period}", "", *_table(headers, rows)]
    return [*lines, ""]


def _comparisons(payload: Mapping[str, Any]) -> list[str]:
    decided = payload.get("decided_pair")
    rows = []
    for r in payload["comparisons"]:
        if not r["label"].startswith("days_1_"):
            continue
        if decided and r["pair_id"] != decided:
            continue
        rows.append(
            [
                r["period"],
                f"`{r['model_id']}` `{r['pair_id']}`",
                f"`{r['reference_model']}` `{r['reference_pair']}`",
                _fmt(r["n_common"]),
                _fmt(r["wis"]),
                _fmt(r["wis_reference"]),
                _fmt(r.get("wis_skill_pct")) + " %",
                _interval(r.get("wis_difference_ci_low"), r.get("wis_difference_ci_high")),
                _p(r.get("wis_p_conservative")),
                _fmt(r.get("wis_eff_n")),
            ]
        )
    lines = ["## Against the uncertainty baselines and across point models (days 1-30)", ""]
    lines.append(
        "WIS skill = 1 - WIS / reference WIS on common forecasts (positive = better). The "
        "interval bounds the mean WIS difference (HAC, larger of the two standard errors)."
    )
    lines += [
        "",
        *_table(
            [
                "period",
                "forecaster",
                "reference",
                "common",
                "WIS",
                "reference WIS",
                "skill",
                "difference 95 % CI",
                "p",
                "eff. n",
            ],
            rows,
        ),
    ]
    return [*lines, ""]


def _exceedance(payload: Mapping[str, Any]) -> list[str]:
    lines = ["## Exceedance probabilities", ""]
    lines.append(
        "`P(demand > threshold)` from each method's predictive distribution. Brier skill is "
        "against the climatology reference on the same forecasts. Hypothetical thresholds "
        "are labelled; no threshold is a measured service capacity."
    )
    rows = [
        [
            r["period"],
            f"`{r['model_id']}`",
            f"`{r['pair_id']}`",
            f"`{r['threshold_label']}`"
            + (" (HYPOTHETICAL)" if r["threshold_kind"] == "hypothetical" else ""),
            r["label"],
            _fmt(r["n_evaluated"]),
            _fmt(r["n_events"]),
            _fmt(r["mean_probability"], 3),
            _fmt(r["event_frequency"], 3),
            _fmt(r["brier"], 4),
            _fmt(r.get("brier_skill_score"), 3),
            _p(r.get("brier_p_conservative")),
            _fmt(r.get("brier_eff_n")),
        ]
        for r in payload["exceedance_summary"]
        if r["label"].startswith("days_1_")
    ]
    lines += [
        "",
        *_table(
            [
                "period",
                "model",
                "method",
                "threshold",
                "scope",
                "forecasts",
                "events",
                "mean p",
                "observed freq.",
                "Brier",
                "skill",
                "p",
                "eff. n",
            ],
            rows,
        ),
        "",
    ]
    decided = payload.get("decided_pair")
    primary = payload["protocol"]["primary_model"]
    for r in payload["exceedance_summary"]:
        if (
            r.get("reliability")
            and r["model_id"] == primary
            and r["pair_id"] == decided
            and r["threshold_kind"] == "historical_quantile"
        ):
            table = [
                [
                    f"{b['bin_low']:.1f}-{b['bin_high']:.1f}",
                    _fmt(b["n"]),
                    _fmt(b["mean_probability"], 3),
                    _fmt(b["observed_frequency"], 3),
                ]
                for b in r["reliability"]
            ]
            lines += [
                f"Reliability, {r['period']}, `{primary}` `{decided}`, `{r['threshold_label']}`:",
                "",
                *_table(["probability bin", "forecasts", "mean p", "observed freq."], table),
                "",
            ]
    return lines


def _strata(payload: Mapping[str, Any]) -> list[str]:
    if not payload.get("strata"):
        return []
    keys = _levels(payload)
    rows = [
        [
            r["period"],
            f"`{r['model_id']}`",
            f"`{r['pair_id']}`",
            r["stratum"],
            _fmt(r["n_evaluated"]),
            _fmt(r["n_target_days"]),
            *(_pct(r.get(f"coverage_{k}")) for k in keys),
            _fmt(r.get(f"mean_width_{keys[-1]}")),
            _fmt(r.get("wis")),
            _fmt(r.get("mae")),
            _fmt(r.get("bias")),
            _fmt(r.get("brier_historical"), 4),
        ]
        for r in payload["strata"]
    ]
    lines = ["## Ordinary days, holidays and flagged days (days 1-30)", ""]
    lines.append(
        "Holiday-related: federal holidays (actual and observed) and the days around them. "
        "Flagged: target days with a warning-level M1 data-quality flag. A day can be both. "
        "Descriptive: the strata leave gaps in the origin grid, so no test is computed, and "
        "the number of distinct target days is small."
    )
    lines += [
        "",
        *_table(
            [
                "period",
                "model",
                "method",
                "stratum",
                "forecasts",
                "target days",
                *(f"coverage {k} %" for k in keys),
                f"width {keys[-1]} %",
                "WIS",
                "MAE",
                "bias",
                "Brier (historical)",
            ],
            rows,
        ),
    ]
    return [*lines, ""]


def _widths(payload: Mapping[str, Any]) -> list[str]:
    rows = [
        [
            r["period"],
            f"`{r['model_id']}`",
            f"`{r['pair_id']}`",
            f"{r['level'] * 100:g} %",
            _fmt(r["mean_width_by_horizon"][0]),
            _fmt(r["mean_width_by_horizon"][-1]),
            _fmt(r["n_decreasing_steps"]),
            _pct(r["share_of_origins_nondecreasing"]),
        ]
        for r in payload["width_profile"]
    ]
    lines = ["## Width against horizon", ""]
    lines.append(
        "Widths are not forced to grow with the horizon. `decreasing steps` counts horizons "
        "where the mean width falls; the last column is the share of origins whose width "
        "never falls along the path."
    )
    lines += [
        "",
        *_table(
            [
                "period",
                "model",
                "method",
                "level",
                "mean width h=1",
                "mean width h=max",
                "decreasing steps",
                "non-decreasing paths",
            ],
            rows,
        ),
    ]
    return [*lines, ""]


def _sensitivity(payload: Mapping[str, Any]) -> list[str]:
    sensitivity = payload.get("sensitivity") or {}
    keys = _levels(payload)
    lines: list[str] = []
    latency = [r for r in sensitivity.get("latency", []) if r["label"].startswith("days_1_")]
    if latency:
        rows = [
            [
                str(r["data_latency_days"]),
                r["period"],
                f"`{r['model_id']}`",
                *(_pct(r.get(f"coverage_{k}")) for k in keys),
                _fmt(r.get("wis")),
            ]
            for r in latency
        ]
        lines += [
            "## Sensitivity: longer data latency (descriptive)",
            "",
            *_table(
                ["latency (days)", "period", "model", *(f"coverage {k} %" for k in keys), "WIS"],
                rows,
            ),
            "",
        ]
    bandwidth = sensitivity.get("coverage_bandwidth") or []
    if bandwidth:
        rows = [
            [
                f"{r['level'] * 100:g} %",
                f"{r['kernel']} {r['bandwidth']}",
                _p(r["p_value"]),
                _pct(r["ci_half_width"]),
                _fmt(r["effective_sample_size"]),
            ]
            for r in bandwidth
        ]
        lines += [
            "## Sensitivity: HAC bandwidth of the coverage test (descriptive)",
            "",
            *_table(["level", "kernel, bandwidth", "p", "CI half-width", "eff. n"], rows),
            "",
        ]
    return lines


def _support(payload: Mapping[str, Any]) -> list[str]:
    decided = payload.get("decided_pair")
    rows = [
        [
            r["period"],
            f"`{r['model_id']}`",
            f"`{r['pair_id']}`",
            _fmt(r["n_scheduled"]),
            _fmt(r["n_horizon_specific"]),
            _fmt(r["n_bucket_pooled"]),
            _fmt(r["n_unavailable"]),
            _fmt(r["n_thin"]),
            f"{_fmt(r['cal_n_min'], 0)}-{_fmt(r['cal_n_max'], 0)}",
            _fmt(r["cal_n_effective_median"]),
        ]
        for r in payload["calibration_support"]
        if decided is None or r["pair_id"] == decided or "gaussian" in r["pair_id"]
    ]
    lines = ["## Calibration support", ""]
    lines.append(
        "How each interval was calibrated. `thin`: issued, but its calibration residuals are "
        "worth fewer independent observations than the configured minimum (exploratory)."
    )
    lines += [
        "",
        *_table(
            [
                "period",
                "model",
                "method",
                "scheduled",
                "horizon-specific",
                "bucket-pooled",
                "unavailable",
                "thin",
                "residuals per set",
                "median eff. n",
            ],
            rows,
        ),
    ]
    return [*lines, ""]


def render_uncertainty_report(payload: Mapping[str, Any]) -> str:
    lines = [f"# M3 uncertainty report: {payload['experiment_id']}", "", f"**{LABEL}**", ""]
    lines.append(f"Periods run: {', '.join(payload['periods_run'])}.")
    lines += ["", "Limitations:", ""]
    lines += [f"- {item}" for item in payload["limitations"]]
    lines.append("")
    sections: Sequence[list[str]] = (
        _protocol(payload),
        _selection(payload),
        _status(payload),
        _coverage_tables(payload),
        _horizon_table(payload),
        _comparisons(payload),
        _candidates(payload),
        _exceedance(payload),
        _strata(payload),
        _widths(payload),
        _sensitivity(payload),
        _support(payload),
    )
    for section in sections:
        lines += section
    return "\n".join(lines).rstrip("\n") + "\n"
