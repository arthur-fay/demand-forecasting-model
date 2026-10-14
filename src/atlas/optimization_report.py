"""Deterministic Markdown report of an M4 decision simulation, from its metrics payload.

Every number comes from ``metrics.json``; every comparative sentence is generated from a
computed difference whose sign decides the wording ("fewer" or "more", "less" or
"more"). The report never calls a plan a recommendation, never calls the penalised score
a cost, and never attaches a probability to any outcome.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Final

NOT_A_RECOMMENDATION: Final = (
    "Nothing in this report is a validated staffing, budget or service-capacity "
    "recommendation for any NYC agency."
)
LIMITATIONS: Final = (
    "Every operational parameter is HYPOTHETICAL: ATLAS has no data on closures, backlog, "
    "staffing, productivity, costs or budgets of any NYC agency.",
    "The M3 intervals failed their calibration criteria; quantile stress paths are "
    "per-day marginal quantiles, not joint scenarios, and carry no probability.",
    "The model is deterministic: it plans as if demand were known. No chance constraint, "
    "service-level guarantee or probability of meeting a target is computed or implied.",
    "The penalised score mixes discretionary cash with notional penalty weights; shadow "
    "prices are in the same USD-equivalent units and are not wages, prices or values of staff.",
    "A pooled city-wide unit is the most favourable assumption about how work can be "
    "shared; any split by agency would leave as much backlog or more.",
)


def _n(value: Any, digits: int = 0) -> str:
    """A number with thousands separators; never a signed zero; ``n/a`` for missing values."""
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int | float):
        if value != value:  # NaN
            return "n/a"
        if value in (float("inf"), float("-inf")):
            return "+inf" if value > 0 else "-inf"
        text = f"{value:,.{digits}f}"
        if text.startswith("-") and not any(ch in "123456789" for ch in text):
            text = text[1:]  # a negative value that rounds to zero is shown as zero
        return text
    return str(value)


def _q(value: Any, digits: int = 0) -> str:
    """Like :func:`_n`, but a nonzero value below one unit keeps three significant digits
    (a violation of 0.29 requests never prints as 0)."""
    if isinstance(value, float) and value == value and 0 < abs(value) < 1:
        return f"{value:.3g}"
    return _n(value, digits)


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return [*lines, ""]


def _difference(value: float | None, unit: str, fewer: str, more: str, digits: int = 0) -> str:
    """A signed difference in words; ``the same`` when it rounds to zero at ``digits``."""
    if value is None or value != value:  # missing or NaN: no direction
        return "n/a"
    shown = _n(abs(value), digits)
    if not math.isinf(value) and not any(ch in "123456789" for ch in shown):
        return f"the same {unit}"
    word = fewer if value < 0 else more
    return f"{shown} {unit} {word}"


def _reported(scenario: Mapping[str, Any]) -> Mapping[str, Any]:
    """The reported plan: the tie-broken optimal strict plan, or the relaxed plan."""
    return (scenario.get("reported_plan") or {}).get("evaluation") or {}


def render_optimization_report(payload: Mapping[str, Any]) -> str:
    """The report of a run: a pure function of its ``metrics.json`` payload."""
    inputs = payload["inputs"]
    status = inputs["uncertainty_status"]
    forecast = inputs["forecast"]
    accuracy = inputs["forecast_accuracy"]
    horizon = payload["horizon"]
    lines = [
        f"# Hypothetical decision simulation: {payload['experiment_id']}",
        "",
        f"**{payload['label']}**",
        "",
        f"**M3 calibration status: {status['interval_status'].upper()}.** {status['statement']} "
        "No result below is decision-grade, and no probability of meeting any target is "
        "computed.",
        "",
        NOT_A_RECOMMENDATION,
        "",
        "## Inputs",
        "",
        f"- Demand: M2 future projection `{forecast['run_id']}` from origin {forecast['origin']} "
        f"(model `{forecast['model_id']}`), {horizon['first_date']}..{horizon['last_date']} "
        f"({horizon['days']} days, mean {_n(horizon['forecast_mean_requests_per_day'])} "
        f"requests/day, total {_n(horizon['forecast_total_requests'])}). {forecast['label']}",
        f"- Historical error of that model (M2 confirmation period, horizons 1-30): MAE "
        f"{_n(accuracy['mae_requests_per_day'])} requests/day (WAPE "
        f"{_n(accuracy['wape_pct'], 1)} %), mean error {_n(accuracy['bias_requests_per_day'])} "
        "requests/day (prediction - actual; negative is under-forecast). A plan built on this "
        "forecast inherits that error.",
        f"- Uncertainty: M3 run `{status['source']['run_id']}`, interval status "
        f"`{status['interval_status']}`, decision-grade: {_n(status['decision_grade'])}.",
        f"- Legacy demand percentile (trailing 180-day 75th percentile at the origin, the "
        f'legacy "planning capacity"): '
        f"{_n(inputs['legacy_demand_percentile_requests_per_day'], 2)} requests/day. It is a "
        "demand statistic and is never used as a capacity.",
        "",
    ]
    for quantile, path in inputs.get("stress_paths", {}).items():
        share = status["evaluation_share_at_or_below_quantile_days_1_30"].get(quantile)
        shown = _n(None if share is None else 100 * share, 1)
        lines.append(
            f"- Stress path q{quantile}: {path['notice']} On the M3 evaluation period the share "
            f"of outcomes at or below this quantile was {shown} % over horizons 1-30."
        )
    if inputs.get("stress_paths"):
        lines.append("")
    lines += ["## Structural assumptions", ""]
    lines += [f"{i}. {text}" for i, text in enumerate(payload["structural_assumptions"], 1)]
    lines += ["", "## HYPOTHETICAL parameters", ""]
    lines += _table(
        ["scope", "parameter", "value", "unit", "assumption"],
        [
            [p["scope"], p["parameter"], _n(p["value"], 2), p["unit"], p["assumption"]]
            for p in payload["hypothetical_parameters"]
        ],
    )
    lines += _model_section()
    lines += _scenario_section(payload["scenarios"])
    lines += _policy_section(payload["scenarios"])
    lines += _face_section(payload["scenarios"])
    lines += _dual_section(payload["scenarios"])
    lines += _frontier_section(payload["scenarios"])
    lines += _sensitivity_section(payload)
    lines += _checks_section(payload.get("checks") or {})
    lines += ["## Limitations", ""]
    lines.append(
        "- Demand is an unvalidated point forecast (M2 future projection). Over horizons "
        f"1-30 that model's historical error was {_n(accuracy['mae_requests_per_day'])} "
        "requests/day on average (mean absolute error), and no plan here accounts for it."
    )
    lines += [f"- {text}" for text in LIMITATIONS]
    lines += ["", NOT_A_RECOMMENDATION, ""]
    return "\n".join(lines)


def _checks_section(checks: Mapping[str, Any]) -> list[str]:
    errors = checks.get("errors")
    fallbacks = checks.get("stage_fallbacks")
    return [
        "## Run checks",
        "",
        f"- Failed checks (ERRORs): {_n(errors)}. Any failed certificate, plan check, "
        "objective agreement, bracket or proved property is an ERROR and marks the run "
        "incomplete.",
        f"- Stage fallbacks: {_n(fallbacks)}. A lexicographic stage or optimal-face test "
        "that needed a fallback is a WARNING; `stage_fallbacks` in `metrics.json` lists them "
        "per scenario and sweep point.",
        "",
    ]


def _model_section() -> list[str]:
    return [
        "## Model",
        "",
        "Linear programme per day `t` (requests served `x`, overtime hours `o`, surge "
        "requests `s`, end-of-day backlog `b`, initial backlog `b0`):",
        "",
        "```",
        "b[t] = b[t-1] + d[t] - x[t]              backlog balance (requests)",
        "x[t] <= p*H[t] + q*o[t] + s[t]           capacity (requests/day)",
        "0 <= o[t] <= O_max,  sum o <= O_total    overtime limits (staff hours)",
        "0 <= s[t] <= S_max[t], sum s <= S_total  surge limits (S_max 0 before availability)",
        "sum (c_ot*o[t] + c_s*s[t]) <= D          discretionary budget (USD)",
        "b[T] <= target,  x, b >= 0               terminal backlog target (requests)",
        "minimise  sum (c_ot*o[t] + c_s*s[t]) + w*sum b[t] + w_T*b[T]   (penalised score)",
        "```",
        "",
        "Service never exceeds the work available (`x[t] <= b[t-1] + d[t]` follows from "
        "the balance and `b >= 0`). Solved with HiGHS dual simplex; every plan is checked "
        "independently (constraints recomputed in plain loops), every optimum is certified "
        "from its primal and dual values (feasibility, dual signs, stationarity, zero "
        "duality gap), and no slack variable exists in the planning model. When no plan "
        "meets the HYPOTHETICAL terminal target, the reported plan is labelled "
        "`relaxed_terminal_target`: least target violation within the budget first, then "
        "the planning objective. When several plans share the optimal score, ties are broken "
        "by the least discretionary cash, then the least terminal backlog, then the least "
        "request-days, so the reported cash, terminal backlog and request-days are "
        "determined; whether the overtime/surge split and the daily schedule are too is "
        "tested (`unique after tie-breaks` in the uniqueness table, with the ranges over "
        "all optimal plans).",
        "",
    ]


def _scenario_section(scenarios: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = ["## Scenarios", ""]
    rows = []
    for s in scenarios:
        e = _reported(s)
        terminal = _n(e.get("terminal_backlog"))
        diagnosis = s.get("diagnosis")
        if diagnosis is not None:
            over = diagnosis["least_terminal_violation_within_budget_requests"]
            terminal += f" ({_q(over)} over target)"
        rows.append(
            [
                f"`{s['scenario_id']}`",
                s["demand_basis"],
                f"{_n(s['demand_total_requests'])} "
                f"({_n(s['demand_total_vs_point_forecast_pct'], 1)} %)",
                _n(s["regular_capacity_mean_requests_per_day"]),
                _n(s["discretionary_budget_usd"]),
                f"`{s['feasibility']}`",
                _n(e.get("discretionary_cost")),
                _n(e.get("backlog_request_days")),
                terminal,
                _n(e.get("peak_backlog")),
                ", ".join(s["binding_constraints"]) or "-",
            ]
        )
    lines.append(
        "`plan` is `strict` when a plan meets every HYPOTHETICAL constraint and "
        "`relaxed_terminal_target` otherwise; the plan shown is the tie-broken optimal one. "
        "Binding constraints are read from the plan itself (slack within tolerance; "
        "`terminal_backlog_violated` marks a relaxed plan above its target)."
    )
    lines.append("")
    lines += _table(
        [
            "scenario",
            "demand basis",
            "demand total, requests (vs point forecast)",
            "regular capacity (requests/day)",
            "discretionary budget (USD)",
            "plan",
            "discretionary cash (USD)",
            "request-days",
            "terminal backlog (requests)",
            "peak backlog (requests)",
            "binding constraints",
        ],
        rows,
    )
    for s in scenarios:
        changes = s.get("terminal_target_changes_optimal_score")
        changes_plan = s.get("terminal_target_changes_reported_plan")
        if changes is not None:
            if changes:
                effect = "changes the optimal score"
            elif changes_plan:
                effect = (
                    "does not change the optimal score but changes the reported plan "
                    "(equally scored plans with less cash or backlog miss the target)"
                )
            else:
                effect = "changes neither the optimal score nor the reported plan"
            lines.append(f"- `{s['scenario_id']}`: dropping the HYPOTHETICAL target {effect}.")
    for s in scenarios:
        diagnosis = s.get("diagnosis")
        if not diagnosis:
            continue
        lines.append(
            f"- `{s['scenario_id']}` has **no plan that meets every HYPOTHETICAL constraint** "
            "(DIAGNOSTIC values, not plans):"
        )
        lines += [f"  - {reason}" for reason in diagnosis["reasons"]]
        lines.append(
            "  - least terminal-target violation within the budget: "
            f"{_q(diagnosis['least_terminal_violation_within_budget_requests'])} requests; "
            "with unlimited discretionary cash: "
            f"{_q(diagnosis['least_terminal_violation_unlimited_discretionary_cash_requests'])} "
            "requests"
        )
    lines.append("")
    return lines


def _policy_section(scenarios: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        "## Policies under the same assumptions",
        "",
        "Every plan faces the same demand, capacities, prices, limits and budget and is "
        "scored by the same evaluator. `fixed_capacity` uses regular staff only; "
        "`backlog_trigger` buys the cheaper extra capacity whenever the backlog would exceed "
        "its HYPOTHETICAL trigger, without planning ahead. A plan that violates the "
        "HYPOTHETICAL terminal target is not comparable on score. Relation: the reported "
        "plan compared with the policy on discretionary cash, request-days and terminal "
        "backlog (lower is better on each): `dominates`, `dominated`, `tradeoff` or `equal`.",
        "",
    ]
    rows = []
    for s in scenarios:
        for c in s["comparisons"]:
            rows.append(
                [
                    f"`{s['scenario_id']}`",
                    f"`{c['policy']}`",
                    f"`{c['reported_plan']}`",
                    ", ".join(c["policy_constraints_violated"]) or "none",
                    _n(c["comparable_on_score"]),
                    f"`{c['relation_of_reported_plan']}`",
                    _difference(c["discretionary_cost_difference"], "USD", "less", "more"),
                    _difference(c["request_days_difference"], "request-days", "fewer", "more"),
                    _difference(c["terminal_backlog_difference"], "requests", "fewer", "more"),
                    _difference(
                        c.get("request_days_difference_at_same_cash"),
                        "request-days",
                        "fewer",
                        "more",
                    ),
                    _difference(c.get("cash_difference_at_same_backlog"), "USD", "less", "more"),
                ]
            )
    lines += _table(
        [
            "scenario",
            "policy",
            "reported plan",
            "constraints the policy violates",
            "comparable on score",
            "relation",
            "reported plan's cash vs policy",
            "request-days vs policy",
            "terminal backlog vs policy",
            "LP at the policy's cash: request-days vs policy",
            "LP at the policy's backlog: cash vs policy",
        ],
        rows,
    )
    lines += [
        "The last two columns compare like with like: the LP restricted to the policy's "
        "discretionary cash (least request-days), and the LP restricted to the policy's "
        "request-days and terminal backlog (least cash), both without the terminal target "
        "that the policies do not plan for. Differences are computed, not assumed; the same "
        "value means the policy is already as good on that measure.",
        "",
    ]
    return lines


def _face_section(scenarios: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        "## Is the optimal plan unique?",
        "",
        "An active-set test from the optimal vertex the solver returned, over the optimal "
        "face built by complementary slackness from the certified duals (a reduced cost or "
        "dual within 1e-9 of its own scale counts as zero, so prices tied that closely count "
        "as tied). `yes` means no other optimal plan differs by more than 0.001 requests or "
        "staff hours in that distance. For an infeasible scenario the test is on its relaxed "
        "plan. When the plan is not unique, its day-by-day allocation in `plans.csv` is one "
        "of several equally scored HYPOTHETICAL plans; the ranges are over all optimal "
        "plans, and `days with extra capacity not determined` counts the days whose "
        "overtime plus surge (requests) differs between them. `unique after tie-breaks` "
        "applies the same test to the reported plan within the tie-breaks (same score, "
        "cash, terminal backlog and request-days): `no` means its overtime/surge split or "
        "daily schedule is still one of several.",
        "",
    ]
    rows = []
    for s in scenarios:
        face = s.get("optimal_face")
        if not face:
            continue
        ranges = {r["quantity"]: r for r in face["ranges"]}

        def span(name: str, ranges: Mapping[str, Any] = ranges) -> str:
            r = ranges.get(name)
            return "n/a" if r is None else f"{_n(r['low'])}..{_n(r['high'])}"

        rows.append(
            [
                f"`{s['scenario_id']}`",
                "strict" if s["feasibility"] == "strict" else "relaxed",
                _n(face["unique"]),
                _n((s.get("reported_plan_after_tie_breaks") or {}).get("unique")),
                _n(face["undetermined_days"]),
                span("discretionary_cash"),
                span("backlog_request_days"),
                span("terminal_backlog"),
                span("overtime_hours"),
                span("surge_requests"),
            ]
        )
    lines += _table(
        [
            "scenario",
            "plan tested",
            "unique",
            "unique after tie-breaks",
            "days with extra capacity not determined",
            "discretionary cash (USD)",
            "request-days",
            "terminal backlog (requests)",
            "overtime (staff hours)",
            "surge (requests)",
        ],
        rows,
    )
    return lines


def _dual_section(scenarios: Sequence[Mapping[str, Any]]) -> list[str]:
    bracketed = [f"`{s['scenario_id']}`" for s in scenarios if s["shadow_prices"]["computed"]]
    if bracketed:
        coverage = (
            f"The duals of {', '.join(bracketed)} (the strictly feasible scenarios among "
            "those listed under `shadow_prices`; an infeasible scenario has no strict duals) "
            "are bracketed by one-sided finite differences of re-solved, certified "
            "programmes (step: the larger of 0.001 natural units and 1e-7 of the right-hand "
            "side): `locally_unique` when both slopes agree, `breakpoint` when they differ "
            "(the solver's value is then one subgradient among several); other duals show "
            "`not_computed`. The cash and penalty parts split the practical forward step "
            "between the tie-broken plans before and after it. Slack and `binding` describe "
            "the reported plan."
        )
    else:
        coverage = (
            "No dual was bracketed by finite differences (no strictly feasible scenario is "
            "listed under `shadow_prices`): these are unchecked solver values "
            "(`not_computed`)."
        )
    lines = [
        "## Shadow prices (horizon-wide constraints)",
        "",
        "Derivative of the optimal penalised score with respect to one right-hand side, in "
        f"USD-equivalent units (cash plus notional penalty), valid only locally. {coverage} "
        "Daily rows are in `duals.csv`.",
        "",
    ]
    rows = []
    for s in scenarios:
        prices = s["shadow_prices"]
        for h in prices["headline"]:
            rows.append(
                [
                    f"`{s['scenario_id']}`",
                    h["constraint"],
                    _n(h["binding"]),
                    _n(h["solver_dual_subgradient"], 3),
                    h["dual_units"],
                    _n(h["fd_backward"], 3),
                    _n(h["fd_forward"], 3),
                    h["fd_status"],
                    _n(h["practical_forward_cash_part"], 3),
                    _n(h["practical_forward_penalty_part"], 3),
                ]
            )
    lines += _table(
        [
            "scenario",
            "constraint",
            "binding",
            "dual",
            "units",
            "backward",
            "forward",
            "status",
            "cash part",
            "penalty part",
        ],
        rows,
    )
    checked = [s for s in scenarios if s["shadow_prices"]["computed"]]
    for s in checked:
        p = s["shadow_prices"]
        lines.append(
            f"- `{s['scenario_id']}`: {p['n']} duals bracketed "
            f"({'all brackets hold' if p['brackets_ok'] else 'A BRACKET FAILED'}); "
            f"{p['locally_unique']} locally unique, {p['breakpoint']} at a breakpoint, "
            f"{p['domain_boundary_or_infeasible_side']} at a domain boundary or with an "
            f"infeasible side, {p.get('backward_unresolved', 0)} with a backward step too "
            "small to resolve the slope (a right-hand side below the step), "
            f"{p.get('solver_failed', 0)} not computed (solver failure)."
        )
    lines.append("")
    return lines


def _tied_points(frontier: Mapping[str, Any]) -> str:
    tied = frontier.get("points_not_unique_after_tie_breaks")
    if not tied:
        return "every point is unique after the tie-breaks"
    open_totals = frontier.get("points_with_overtime_or_surge_totals_open")
    return (
        f"at {_n(tied)} points other plans tie on penalty, cash, terminal backlog and "
        f"request-days with a different daily schedule; the overtime or surge totals differ "
        f"too at {_n(open_totals)} of them"
    )


def _frontier_section(scenarios: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        "## Tradeoff frontier",
        "",
        "Least notional backlog penalty achievable for each cap on discretionary cash "
        "(budget row dropped, daily limits and totals kept), with the least cash, then the "
        "least terminal backlog and request-days among the plans that achieve it "
        "(`frontier.csv`). The day-by-day schedule, and at some points the overtime and "
        "surge totals, can still differ between such plans; `frontier.csv` flags those "
        "points (`unique_after_tie_breaks`) with the ranges of the totals. "
        "A HYPOTHETICAL TRADEOFF under HYPOTHETICAL prices and a point forecast; no point on "
        "it is singled out as a budget.",
        "",
    ]
    for s in scenarios:
        f = s.get("frontier")
        if not f:
            continue
        least = f["least_cash_for_least_penalty"]
        kept = (
            "with the terminal target kept, extra cash beyond "
            f"{_n(least.get('kept'))} USD lowers the penalty no further"
            if least.get("kept") is not None
            else "with the terminal target kept, no plan meets the HYPOTHETICAL target at "
            "any cash cap"
        )
        dropped = (
            f"without the target, extra cash beyond {_n(least.get('dropped'))} USD lowers the "
            "penalty no further"
            if least.get("dropped") is not None
            else "without the target, no point could be computed"
        )
        lines.append(
            f"- `{s['scenario_id']}`: {kept}; {dropped}; "
            "under this deterministic demand path and the HYPOTHETICAL prices; the "
            f"curve is non-increasing: {_n(f['monotone_ok'])}, convex: {_n(f['convex_ok'])}; "
            f"the strict plan lies on it: {f['on_frontier_check']}; "
            f"{_tied_points(f)}. DIAGNOSTIC tradeoff points, not budgets."
        )
    lines.append("")
    return lines


def _sensitivity_section(payload: Mapping[str, Any]) -> list[str]:
    sensitivity = payload["sensitivity"]
    if sensitivity["base_scenario"] is None:
        return ["## Sensitivity", "", "No sensitivity sweeps were configured.", ""]
    lines = [
        "## Sensitivity",
        "",
        f"One HYPOTHETICAL parameter at a time on scenario `{sensitivity['base_scenario']}` "
        "(`sensitivity.csv` has every value). Properties proved for the model and checked on "
        "the results (`down`: non-increasing as the parameter rises; feasibility `lost`: "
        "once infeasible, stays infeasible):",
        "",
    ]
    lines += _table(
        ["parameter", "quantity", "direction", "holds", "pairs compared"],
        [
            [
                m["parameter"],
                m["quantity"],
                m["direction"],
                _n(m["holds"]),
                _n(m["compared_pairs"]),
            ]
            for m in sensitivity["monotonicity"]
        ],
    )
    lines += [
        "Regular staff hours change capacity at a cost outside the score (the committed "
        "regular cost), so their effect on cash is reported, not asserted. The backlog "
        "penalty sweep changes only the daily weight `w`; cash alone is not asserted to "
        "rise with it when a terminal penalty is set.",
        "",
        "Values of the tie-broken optimal plan at each point (flag `not unique`: other "
        "optimal plans exist; `sensitivity.csv` has their ranges). An infeasible point shows "
        "the least terminal-target violation within the budget. "
        "A swept capacity that lands within 1 % of the legacy demand percentile is marked "
        "`near percentile`: it is a swept HYPOTHETICAL capacity, not that percentile.",
        "",
    ]
    rows = []
    for p in sensitivity.get("points", []):
        flags = []
        if p["unique_optimal_plan"] is False:
            flags.append("not unique")
        if p["capacity_within_1pct_of_legacy_demand_percentile"]:
            flags.append("near percentile")
        rows.append(
            [
                p["parameter"],
                _n(p["value"], 2),
                p["status"],
                _n(p["capacity_to_scenario_demand_mean_ratio"], 3),
                _n(p["discretionary_cost"]),
                _n(p["backlog_request_days"]),
                _n(p["terminal_backlog"]),
                _q(p["least_terminal_violation_within_budget"]),
                ", ".join(flags) or "-",
            ]
        )
    lines += _table(
        [
            "parameter",
            "value",
            "strict model",
            "regular capacity / mean demand of the point",
            "discretionary cash (USD)",
            "request-days",
            "terminal backlog (requests)",
            "least violation if infeasible (requests)",
            "flags",
        ],
        rows,
    )
    return lines
