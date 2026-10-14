# Hypothetical decision simulation and optimisation (M4)

M4 turns the ATLAS demand forecast into **hypothetical** resource-allocation scenarios: a
linear programme decides, day by day over a planning horizon of 1-30 days, how many
requests to serve, how many overtime hours and surge requests to buy, and how much backlog
to carry, under a discretionary budget and a terminal-backlog target. It compares the
optimised plans with two simple policies under the same assumptions, diagnoses
infeasibility, maps tradeoffs and reports sensitivity and certified shadow prices.

> **Read this first.** Every staffing, productivity, cost, budget, backlog and capacity
> number in M4 is `HYPOTHETICAL`. ATLAS holds no data on closures, backlog, staffing,
> service levels or costs of any NYC agency ([LEGACY_LIMITATIONS.md](LEGACY_LIMITATIONS.md),
> section 2). The demand is an unvalidated point forecast, and the M3 intervals around it
> have the status **FAILED_CALIBRATION** ([UNCERTAINTY.md](UNCERTAINTY.md), section 9.2).
> M4 results are exploratory illustrations of a method. **Nothing here is a validated
> staffing, budget or service-capacity recommendation.**

Sections 1-8 describe the method and were committed, with the code, the tests and the
configuration `configs/optimization/atlas-m4-optimization-v1.json`, **before the first run
of that configuration** (commit `af10ddd`). The independent review (section 10) ran the
reference configuration on that frozen code; the changes to the method it led to are marked
**Amended (R)** below, each with its reason in section 10. The configuration file was not
changed. The results (section 9) come from the amended code.

```bash
uv run atlas optimize run --require-clean --output-dir runs/m4     # reference configuration
uv run atlas optimize verify runs/m4                               # re-run; every file identical
uv run atlas optimize inspect runs/m4                              # integrity and headline only
uv run atlas optimize run --config configs/optimization/smoke.json --output-dir /tmp/m4-smoke
```

## 1. Integrity rules and labels

| Rule | How it is enforced |
|---|---|
| Never a validated recommendation | Record type `hypothetical_decision_simulation`; every CSV row carries `validated_recommendation = false`, `parameter_provenance = HYPOTHETICAL`, `point_forecast_validated = false` and the M3 interval status; the report and the CLI print the label. Even with a decision-grade M3 status the flag stays false (tested). |
| Every operational parameter `HYPOTHETICAL` | Each is a quantity `{value, unit, provenance, assumption}`; the provenance must be `HYPOTHETICAL`, the unit must be the exact expected unit, the assumption at least 20 characters. M4 has no evidence register, so no parameter can be marked verified. |
| M3 status preserved | The `calibration_status.json` of the complete M3 run is loaded through M3's own provenance check; its status (`failed_calibration`) is in the manifest, `metrics.json`, every CSV row, the report and a CLI warning. |
| A demand percentile is never a capacity | Capacity comes only from staff hours × productivity, overtime and surge. A scenario whose regular capacity is within 1 % of the trailing 180-day 75th percentile of demand (11,608.75, the legacy "planning capacity") is rejected. **Amended (R):** a sensitivity sweep may pass through such a capacity as one swept HYPOTHETICAL value; that point is flagged `capacity_within_1pct_of_legacy_demand_percentile` (`near percentile` in the report), never presented as the percentile. |
| No chance constraints | The model is deterministic. M3 quantiles may be used only as an explicitly labelled stress path (`interpretation: hypothetical_stress_not_a_probability`); no probability of meeting any target is computed or stated. |
| Equivalent assumptions | Policies and the LP face the same problem and are scored by one evaluator; a policy that violates a constraint is not compared on score; like-for-like comparisons at equal cash and at equal backlog are computed. |
| No improvement claimed unless computed | Every comparative sentence in the report is generated from a computed difference whose sign selects the wording; a test lints the report for promotional or probabilistic wording. |

## 2. Inputs and their provenance chain

| Input | File | Checks |
|---|---|---|
| Snapshot | `snapshots/legacy-v0` | M0 integrity and validation (fail-closed); its data fingerprint must equal every input's |
| Demand | M2 future projection `docs/results/atlas-m2-rolling-origin-v1/projection` | record type `future_projection`; SHA-256 of `projections.csv` equals the manifest; model = the model the complete M2 run recommended; first `T` horizons, consecutive days, status `projected` |
| Forecast error (context) | complete M2 run `docs/results/atlas-m2-rolling-origin-v1/full` | run id and result fingerprint equal the projection's recommendation source; `recommendation.json` and `metrics.json` match the manifest; confirmation-period MAE and bias over horizons 1-30 |
| Calibration status | `docs/results/atlas-m3-intervals-v1/full/calibration_status.json` | M3's `_status_source`: complete run, file and metrics unchanged, status equal to the run's metrics |
| Stress path (optional) | M3 projection `docs/results/atlas-m3-intervals-v1/projection` | labelled with the same status file (SHA-256); rows of the primary model **and** the sealed interval method; point predictions identical to M2's |
| Demand percentile (guard only) | M3 projection exceedance file | `trailing180_p75`, provenance `DERIVED_FROM_HISTORY` |

The M2 point forecast and the M3 status are reused, not recomputed: M4 consumes the
interfaces' committed outputs, hash-verified, so a run is reproducible offline and does not
depend on BLAS kernels.

## 3. Model

One city-wide service unit `u` (the domain model supports several units sharing one
budget; the configuration accepts exactly one, because no agency-level capacity data
exist). Days `t = 1..T`, `Δt = 1` day.

| Symbol | Meaning | Unit |
|---|---|---|
| `d[t]` | demand (forecast arrivals) | requests/day |
| `b0` | initial backlog | requests |
| `H[t]`, `p` | regular staff hours, regular productivity | staff hours/day, requests/staff hour |
| `q`, `O_max`, `O_total` | overtime productivity, daily and horizon overtime limits | requests/staff hour, staff hours/day, staff hours |
| `S_max[t]`, `S_total` | surge capacity (0 before the surge start day), optional horizon surge limit | requests/day, requests |
| `c_reg`, `c_ot`, `c_s` | regular wage, overtime wage, surge price | USD/staff hour, USD/staff hour, USD/request |
| `D` | discretionary budget (overtime + surge cash over the horizon) | USD |
| `target` | terminal-backlog target | requests |
| `w`, `w_T` | notional backlog penalty, notional terminal penalty | USD/request-day, USD/request |

Decision variables, all `>= 0`: requests served `x[t]`, overtime hours `o[t]`, surge requests
`s[t]`, end-of-day backlog `b[t]`.

```
b[t] - b[t-1] + x[t] = d[t]          (b[0] = b0)            backlog balance        requests
x[t] - q*o[t] - s[t] <= p*H[t]                              capacity               requests/day
o[t] <= O_max,  s[t] <= S_max[t]                            daily limits (bounds)
sum_t o[t] <= O_total                    (if set)           overtime total         staff hours
sum_t s[t] <= S_total                    (if set)           surge total            requests
sum_t (c_ot*o[t] + c_s*s[t]) <= D                           discretionary budget   USD
b[T] <= target                                              terminal target        requests

minimise  sum_t (c_ot*o[t] + c_s*s[t]) + w * sum_t b[t] + w_T * b[T]     (penalised score)
```

- **Service never exceeds the available work**: from the balance, `x[t] = b[t-1] + d[t] - b[t]
  <= b[t-1] + d[t]` because `b[t] >= 0`. It is not a separate row (it would only add
  degeneracy) but it is checked explicitly for every plan.
- **Units**: each term of the objective is USD (`USD/staff hour × staff hours`, `USD/request ×
  requests`, `USD/request-day × requests × 1 day`). The day-`T` backlog waits overnight like
  every other day's backlog and also carries `w_T`.
- **Penalised score**: discretionary cash plus notional penalties. The penalties are
  HYPOTHETICAL weights expressing a preference for less waiting; they are not cash, never
  count against the budget and are always reported separately. The committed regular staff
  cost `F = Σ c_reg·H[t]` is reported but not part of the score: it does not depend on any
  decision. The score ranks plans for one set of weights; it is never presented as a cost.
- **Feasibility**: `x = o = s = 0` with the backlog carried satisfies every row but the
  terminal target, so the strict model can only be infeasible through the target (with the
  limits, the totals, the delayed surge and the budget). It is never unbounded (every cost
  and weight is `>= 0`).
- **No slack variables.** The planning programme has exactly `4 × T` columns, and every row
  is a named model constraint (tested). Relaxations exist only in clearly labelled
  diagnostic programmes (section 5).
- Quantities are continuous; arrivals may be served on their day; unused capacity is lost.

## 4. Solver and verification of every solution

SciPy 1.18.1 `linprog(method="highs-ds")` (HiGHS 1.12.0 dual simplex), presolve on, primal
and dual feasibility tolerances `1e-9`, an iteration limit of 10,000 and **no time limit**
(it would make results depend on machine load). Any `OptimizeWarning` (an option silently
replaced by a default) is an error. The outcome is classified on the HiGHS model status read
from the solver message, not only on SciPy's integer code (SciPy reports a HiGHS *model error*
with its "infeasible" code): 7 optimal, 8 infeasible; 9, 10 and 15 (inconclusive) are re-solved
once without presolve; anything else is an error. Inputs with non-finite values or magnitudes
of `1e9` or more are rejected. **Amended (R):** right-hand sides of derived analysis rows
(policy caps on request-days, frontier caps, optimal-face rows), which sum inputs over the
horizon, are bounded at `1e15` instead. **Amended (R2):** nonzero matrix coefficients of
magnitude `1e-9` or less are rejected with a request to rescale: HiGHS drops them silently
(its `small_matrix_value`), so it would solve a different programme from the one certified.

Nothing the solver returns is trusted:

1. **Optimality certificate** from the primal and dual values: primal feasibility of every
   row and bound, dual signs (`<=` rows and upper bounds `<= 0`, lower bounds `>= 0`),
   stationarity `c - A_ub'y_ub - A_eq'y_eq - y_lo - y_up = 0` and a zero duality gap
   `c'x = b_ub'y_ub + b_eq'y_eq + lower'y_lo + upper'y_up`. Tolerances: rows and bounds
   `1e-7 + 1e-9 × magnitude`, dual quantities `1e-9 × magnitude`. Together these prove
   optimality without the solver's status. **Amended (R):** a non-finite primal or dual value
   fails the certificate (a NaN used to slip through Python's `max`); the margin observed on
   the reference run is in section 9 (the earlier "about 100 times the noise" was wrong).
2. **Independent plan check** in plain loops, without the LP matrices: non-negativity, the
   balance recursion, available work, capacity, daily limits, totals, terminal target and
   budget, each with tolerance `1e-6 + 1e-9 × magnitude` in its own unit.
3. **Objective agreement**: the solver's objective equals the score recomputed from the plan.
4. Values within tolerance outside a bound (round-off) are snapped onto it and counted;
   anything further out is reported by the check. Signed zeros are normalised before writing.

A failure of any check is an ERROR: the run is marked incomplete. **Amended (R):** this now
covers every programme M4 solves, including sweep points, the soft-target re-solves of
infeasible sweep points, the first stages of the policy comparisons, the optimal-face
programmes and every finite-difference re-solve (each certified). A reproduced failure
fails `optimize verify` too, and `optimize inspect` reads the failed-check count from the
fingerprinted `metrics.json`, not only the manifest flag.

**Lexicographic stages (amended (R)).** Several results are lexicographic (the reported
plan, the relaxed plan, the policy comparisons, the frontier points): a later objective is
minimised among the optimal plans of an earlier one. The frozen code capped the earlier
objective at its optimum plus `1e-6 + 1e-9 ×` its value; every later stage used that slack
completely, so the reported plans sat that far from the earlier optimum (a binding budget
was left a millionth of a dollar unspent and so not reported as binding), and on near-tied
prices the cap row, parallel to the objective, made HiGHS fail. Each later stage is now
solved on the **exact optimal face** of the earlier one, built by complementary slackness
from the certified duals (bounds with a nonzero reduced cost fixed, rows with a nonzero dual
made equalities; in this first amendment values within `1e-9 × max(1, max|c|)` counted
as zero, per column and per row since R2). Each stage is checked to keep the earlier
optimum. In this first amendment the epsilon cap was used only when the solver failed on the
face programme, and recorded as `lexicographic_epsilon_fallback` in four programme summaries
only; R2 (below) widened both the fallback and its record (`stage_fallback`).

**Amended (R2).** The second review found that one large objective weight widened the tie
tolerance of every other column, that a later stage was not checked to keep the earlier
optimum in every path, and that most fallbacks were recorded nowhere. Now:

- **Per-column and per-row zero tests.** A reduced cost counts as zero when it is within
  `1e-9 × max(1, |c_j| + Σ_i |a_ij y_i|)`, the scale of its own column in the
  stationarity check; a row dual when it is within `1e-9 ×` the largest such column scale
  divided by the row's coefficient, over the columns the row touches.
- **The face contains the earlier optimum `x*`.** Every row and bound is widened to `x*`'s
  own activity (tight rows are pinned there), so an optimum that is feasible only to within
  the solver's tolerance stays on its face. The widening is at most that tolerance, and the
  plan check still judges every plan against the configured limits.
- **Every stage keeps the earlier optimum.** A face plan must score the earlier optimum to
  within `10 ×` the checking tolerance. If it does not (the zero tests let in a plan that
  scores worse), the stage is re-solved on the face built from exact zeros only
  (`zero_threshold`). Only if that fails too is the epsilon cap used (`epsilon_cap`).
- **Every fallback is recorded.** It appears on its stage (`stage_fallback` in the programme
  summaries), per scenario and sweep point (`stage_fallbacks`), in the run total
  `checks.stage_fallbacks` of `metrics.json`, and as a WARNING `stage_fallback`. This
  covers the reported, relaxed and diagnostic plans, the policy comparisons, the frontier,
  the sweeps, the finite-difference splits and the optimal-face rebuilds (`face_rebuilt`).
  None was needed on the reference run.
- **Exact caps first.** The policy comparisons cap the LP at the policy's own cash,
  request-days and terminal backlog; the frontier at each cash cap. The policy plan sits
  exactly on its caps, and at large scale HiGHS can call that programme infeasible at its
  own tolerance. Only then is the programme re-solved with epsilon caps, recorded as
  `policy_epsilon_cap` or `frontier_epsilon_cap`.
- **Ties are broken explicitly.** After the optimal score, the reported plan minimises
  discretionary cash, then the terminal backlog, then request-days, each on the optimal
  face of the stage before (`plan_selection` in the CSVs names the chain for every plan).
  Cash, penalty, terminal backlog and request-days of a reported plan therefore do not
  depend on the vertex HiGHS returns. Its day-by-day schedule, and the split between
  overtime and surge, can still differ between tied plans; the uniqueness test (section 7)
  says when.

## 5. Infeasibility, relaxed plans and diagnostics

When the strict model is infeasible, M4 reports a WARNING and computes, as DIAGNOSTIC
values (never as plans for the stated constraints):

- the **least terminal-target violation** reachable with unlimited discretionary cash and
  within the budget: soft targets `b[T] - v <= target`, `v >= 0`, minimise `Σ v` (requests;
  per unit, so units without a target are not counted);
- the **least discretionary spend that would reach the HYPOTHETICAL target** (USD) and the
  budget shortfall, or "no amount reaches it";
- the analytic lower bound on the terminal backlog, `b[t] = max(0, b[t-1] + d[t] - max
  capacity[t])`, exact when no horizon total is set and only a lower bound otherwise.

The reported plan is then the **relaxed plan** `relaxed_terminal_target`: lexicographic —
least violation within the budget first, then the planning objective among those plans
(without the second stage the plan would be arbitrary, for example paid idle overtime),
**amended (R):** then the least discretionary cash among those (**R2:** then the least
terminal backlog and request-days), with its own uniqueness test and binding constraints
read from the plan.

**Amended (R).** HiGHS decides infeasibility at its tolerance (`1e-9`), the plan check at
`1e-6 + 1e-9 × magnitude`. When the relaxed plan passes the plan check with nothing ignored,
the diagnosis says **BORDERLINE** (the scenario lies on the feasibility boundary) instead
of leaving the reasons empty. The infeasible verdict itself is confirmed independently: the
certified soft-target programme within the budget must need a violation above `1e-10`
requests, otherwise the verdict is an ERROR. Violations and spends below one unit are printed
with three significant digits, never as `0`. The reasons say "DIAGNOSTIC, not a budget
request". Which limits they name is amended below: this first amendment named the limits
binding in the least-violation plan, which depends on the vertex HiGHS returns.

**Amended (R2).** Limits that bind in "the" least-violation plan depend on which of many such
plans HiGHS returns, and a binding limit need not be a cause. When no amount of cash reaches
the target, the reasons now name each configured limit whose relaxation **alone** lowers
the least violation with unlimited cash, with the violation that would remain. The limits
are the daily overtime limit, the daily surge limit, the surge delay, the overtime total and
the surge total, per unit. Each relaxation is its own certified programme
(`least_violation_unlimited_cash_with_one_limit_relaxed_requests` in `metrics.json`). If none
lowers it alone, the reasons say that the limits and the regular capacity bind together.
Below 1 USD of shortfall the least spend is printed to nine significant digits instead of
whole USD (the shortfall keeps three), so the spend no longer appears to be below the budget.

**Amended (R3).** The third review found the wording and tolerances of these reasons loose:
- **"Relaxed" means removed.** A daily limit is lifted above all the work of the horizon,
  a total is dropped, and a delayed surge becomes available from the first day. The reason
  now says so, and gives the violation that would remain "without it". The metrics key is
  `least_violation_unlimited_cash_with_one_limit_removed_requests`.
- **A surge configured to start after the horizon** is now tested as well, through its
  configured daily capacity. Before, it was never tested, and the reason could say that no
  limit lowers the violation.
- **One tolerance for "out of reach".** Whether the target is out of reach at any cash
  uses the plan check's tolerance, summed over the units with a target, so a scenario is
  no longer both "out of reach at any cash" and BORDERLINE. A removal counts as lowering
  the violation if it reaches that tolerance, or if it lowers the violation by more than
  `10 ×` the checking tolerance (half the violation, if that is smaller).
- **A failed removal is an ERROR.** A removal programme that is not optimal, or that the
  solver refuses (for example, a lifted limit beyond the input magnitude bound), is now an
  ERROR. The reason names it as not computed; it is no longer silently left out.
- **Limits below one unit and the analytic bound.** Limits below one unit keep three
  significant digits. The analytic-bound reason gives the excess over the target.

## 6. Policies and like-for-like comparisons

| Plan | Definition |
|---|---|
| `optimized` | the strict LP (or `relaxed_terminal_target` when infeasible). **Amended (R):** the least-cash plan among the optimal plans. **Amended (R2):** then the least terminal backlog, then the least request-days (section 4), because the least-cash plan alone left terminal backlog and request-days to the vertex HiGHS returns |
| `optimized_without_terminal_target` | the LP without the HYPOTHETICAL target: shows what the target forces. **Amended (R):** compared on the optimal score; when the target does not change it, the row shows the reported plan and says `same_optimal_score_target_forces_nothing`. **Amended (R2):** only when the tie-broken plan without the target is also the reported plan (`terminal_target_changes_reported_plan` false); a target can leave the score unchanged and still exclude cheaper equally scored plans, and the row then shows the tie-broken plan without the target |
| `fixed_capacity` | regular staff only: `x[t] = min(b[t-1] + d[t], p·H[t])`, no overtime or surge |
| `backlog_trigger` | when the backlog would exceed a HYPOTHETICAL trigger, buy the cheaper extra capacity (USD per request; overtime on a tie) up to the daily limits, the remaining totals and the remaining budget; myopic |
| `lp_same_cash_as_<policy>` | least request-days with at most the policy's discretionary cash (target dropped) |
| `lp_same_backlog_as_<policy>` | least cash with at most the policy's request-days and terminal backlog (target dropped) |

The policy plans are feasible for the last two programmes, so the LP can only do as well or
better there; this is checked, and so is "strict optimum's score ≤ score of any policy plan
that satisfies every constraint". The reported plan is compared with each policy on
discretionary cash, request-days and terminal backlog: `dominates`, `dominated`, `tradeoff`
or `equal`. A policy that misses the target is reported as violating it and not compared on
score.

## 7. Uniqueness, shadow prices, frontier, sensitivity

- **Is the optimal plan unique?** From the vertex the solver returned (amended (R2), below),
  the constraints active there (bounds and tight rows) determine it. M4 maximises the total
  distance from those active constraints over all optimal plans; zero distance means no
  other optimal plan exists (in practice at most `0.001`, amended (R2), below).
  **Amended (R):** the optimal plans are the exact optimal face built by complementary
  slackness (see "Lexicographic stages", section 4), not the plans within `ε = 1e-9 ×
  Σ|c_j x_j|` of the optimum re-tested at `ε / 100`: that test missed a genuine alternative
  optimum when a near-flat direction also existed, and its near-parallel row made HiGHS fail
  on near-ties. Prices tied to within the dual tolerance count as tied (per column and row
  since R2, section 4). Every face programme
  is certified and its plan checked and confirmed to score the optimum. Ranges over the
  optimal face of cash, request-days, terminal backlog, overtime and surge are reported, and,
  for a non-unique optimum, the range of extra capacity on each day (`plans.csv`). The
  relaxed plan and every sweep point get the same test. A non-unique plan is labelled "one of
  several equally scored HYPOTHETICAL plans". **Amended (R2):** the distance is measured from
  the vertex the solver returned for the programme tested (the planning programme, or the
  relaxed plan's last stage), not from the reported plan. The plan is called unique when no
  optimal plan lies more than `0.001` (requests or staff hours, summed over the active
  constraints) from it: the test is exact up to that threshold, not to zero. If a face plan
  does not score the optimum (the zero tests loosened the face), the face is rebuilt from
  exact zeros and the rebuild is recorded (`face_rebuilt`); a failed face programme is
  recorded as an ERROR instead of aborting the run. **Amended (R3):** the tie-breaks pin cash,
  terminal backlog and request-days, but the overtime/surge split and the daily schedule can
  stay open (for example when overtime and surge cost the same per request). The reported
  plan itself therefore gets the same test on the face of its last tie-break stage. It is
  reported as `unique_after_tie_breaks` in `scenarios.csv`, as
  `reported_plan_after_tie_breaks` in `metrics.json`, and as "unique after tie-breaks" in
  the report. The report's count of undetermined days is named for what it counts: days
  whose extra capacity (overtime plus surge, in requests) is not determined.
- **Shadow prices** are derivatives of the optimal penalised score with respect to one
  right-hand side: per request of demand on day `t` (balance), per request of free regular
  capacity on day `t` (capacity), per USD of budget, per request of terminal target, per
  allowed overtime hour or surge request. Their unit is the penalised score's
  (USD-equivalent), so they are **not wages, prices or values of staff**; no staff-hour
  conversion is reported (adding a staff hour would also add its committed cost). The
  optimal value is convex in every right-hand side, so `backward secant ≤ dual ≤ forward
  secant` must hold for any step: every dual of the strictly feasible scenarios listed
  under `shadow_prices` (an infeasible scenario has no strict duals) is bracketed with step
  `0.001` (`locally_unique` when both slopes agree, `breakpoint`
  otherwise: the solver's value is then one subgradient among several), at a domain boundary
  only from above, with an infeasible side as `±inf`. A practical step (1 request, 1 staff
  hour, 100 USD) is reported separately as an average effect, split into its cash and
  notional-penalty parts. Balance duals must be `>= 0` (the optimal score never falls when
  demand rises). **Amended (R):** the local step is the larger of `0.001` and `1e-7 ×` the
  right-hand side, and slopes are compared with a tolerance that includes the rounding noise
  of two optimal values (`2 × 64 ulp × Σ|c_j x_j|` divided by the step), so the statuses do
  not change when every quantity is scaled (an absolute step gave false breakpoints and
  bracket ERRORs at ×100-×300); every re-solve is certified; the cash/penalty split compares
  the least-cash optimal plans before and after the step; a daily limit of zero (surge not
  yet available) is flagged `limit_is_zero_unavailable` in `duals.csv`. **Amended (R2):** the
  noise allowance divides by the step actually taken (a backward step is cut to the
  right-hand side when that is smaller), so a right-hand side below the step no longer gives
  false bracket ERRORs; a slope whose re-solve fails is `solver_failed` (an ERROR, counted
  in `metrics.json`), not `breakpoint`; the cash/penalty split compares the tie-broken plans
  before and after the step, each checked to keep its optimum; the slack and `binding` columns
  of `duals.csv` describe the reported plan (the duals are optimal for every optimal plan, so
  complementary slackness holds there too), and a zero limit is never `binding`.
  **Amended (R3):** a failed practical-step re-solve also gives `solver_failed` (only the
  local slopes counted before). A backward step cut to a tiny right-hand side, whose noise
  allowance exceeds the slope tolerance, cannot resolve a slope. Such a secant is now marked
  `backward_unresolved` and left out of the bracket and convexity tests; before, the widened
  allowance made any two slopes "agree" (`locally_unique`).
- **Tradeoff frontier** (epsilon constraint): for caps on discretionary cash from 0 to the
  least cash that achieves the least possible penalty, the least notional penalty `w·Σb +
  w_T·b[T]` (then the least cash achieving it), with and without the terminal target, budget
  row dropped. Because the second axis is the penalty actually in the objective, the strict
  optimum must lie on the curve (checked), and the curve must be non-increasing and convex in
  the cap (checked). No point is singled out: it is a HYPOTHETICAL tradeoff, not a budget.
  **Amended (R2):** each point breaks ties like the reported plan (least cash, then terminal
  backlog, then request-days), so the values in `frontier.csv` do not depend on the vertex.
  Each point gets the uniqueness test. `unique_after_tie_breaks` is false where other plans
  tie on all four values with a different daily schedule, and the ranges of the overtime and
  surge totals over those plans are reported.
- **Sensitivity sweeps**, one parameter at a time, with the properties proved for the model
  checked on the results (**amended (R2):** the values of each point are those of its
  tie-broken plan):

| Parameter | Proved property (checked) |
|---|---|
| demand multiplier | score non-decreasing; once infeasible, stays infeasible (from a plan for higher demand, `x = min(x', b[t-1] + d[t])` is feasible for lower demand with pointwise lower backlog) |
| discretionary budget | score non-increasing; feasibility can only be gained (the feasible set grows) |
| regular hours (budget unchanged) | score non-increasing; feasibility can only be gained (more free capacity; the committed cost is outside the score) |
| productivity (regular and overtime) | score non-increasing; feasibility can only be gained |
| cost rates | score non-decreasing; feasibility can only be lost |
| backlog penalty `w` | score non-decreasing; request-days non-increasing; cash + terminal penalty non-decreasing (exchange argument); feasibility unchanged. Cash alone is not asserted: with `w_T > 0` it can fall as `w` rises |

## 8. Scenarios, parameters and exposure log

The reference configuration has one HYPOTHETICAL city-wide unit (regular capacity 4,400
staff hours × 2.5 requests per hour = 11,000 requests/day, 1.2 % below the forecast mean of
11,132 and 5.2 % below the legacy demand percentile), an initial backlog of 8,000 requests, a
terminal target of 4,000, overtime up to 400 hours a day and 6,000 in total at 2.25 requests
per hour and 60 USD per hour (26.67 USD per request), surge up to 600 requests a day at 30 USD,
a discretionary budget of 300,000 USD and a notional penalty of 2 USD per request-day. Every
value and its assumption is listed in the configuration and in the report.

| Scenario | Exact HYPOTHETICAL change |
|---|---|
| `normal` | none |
| `increased_demand` | demand × 1.10 |
| `reduced_capacity` | regular hours × 0.90 (budget unchanged) |
| `increased_costs` | regular wage, overtime wage and surge price × 1.20 |
| `reduced_budget` | discretionary budget × 0.50 |
| `delayed_surge` | surge from day 8 |
| `combined_stress` | demand × 1.05, hours × 0.95, costs × 1.10, budget × 0.75, surge from day 8 |
| `m2_bias_shift` | demand + 256 requests/day (M2's confirmation-year under-forecast, had it persisted) |
| `m3_marginal_q90_stress` | demand = each day's marginal M3 90 % quantile: a stress path, not a joint scenario, no probability (the M3 90 % quantile was at or above the outcome on 87.9 % of evaluation forecasts, not 90 %) |

Sweeps on `normal`: demand × 0.90-1.10, discretionary budget × 0-3, regular hours × 0.90-1.10,
productivity × 0.90-1.10, cost rates × 0.8-1.5, backlog penalty 0-16 USD per request-day.
Frontiers: `normal` and `combined_stress`. Shadow prices: every strictly feasible scenario.

**Exposure log.** The parameter values were chosen after seeing the M2 forecast (mean 11,132
requests/day), its confirmation-year error (MAE 985, bias −256 requests/day), the legacy
demand percentile (11,609) and the M3 results, so that the scenarios exercise binding and
slack constraints and both kinds of infeasibility; they carry no evidential weight. During
development the code ran only on synthetic problems and on `configs/optimization/smoke.json`
(10 days, different values). The reference configuration was committed together with this
document, the code and the tests before its first run; nothing in it was changed afterwards.

## 9. Results

Reference run `atlas-m4-optimization-v1-02258d28-5ab8b3a5`, committed in
[`docs/results/atlas-m4-optimization-v1/`](results/atlas-m4-optimization-v1/report.md):
configuration fingerprint `02258d28d0e30759946e5aafbb83a8492d5db905c5fb77da921b191d4ce22e8a`,
result fingerprint `bc7ec821cdc1198c32b888276bd3220f8e56701ac1a9463df1d6a969b8ef8dea`, code
commit `a885a69` (clean tree, `--require-clean`), SciPy 1.18.1 / HiGHS 1.12.0, 20 seconds.
`optimize verify` reproduces every file byte for byte, also under the Haswell and Prescott
OpenBLAS kernels (tested). **0 errors; 7 warnings**: M3 status FAILED_CALIBRATION, and six
scenarios without a plan that meets every HYPOTHETICAL constraint.

**Every number below is computed from HYPOTHETICAL parameters and an unvalidated point
forecast (MAE 985 requests/day over these horizons). It illustrates the method; it is not a
staffing, budget or capacity recommendation, and no number is a probability.**

### 9.1 Scenarios (reported plan)

| Scenario | Strict model | Discretionary cash (USD) | Request-days | Terminal backlog | Overtime h | Surge requests | Optimal plan unique |
|---|---|---|---|---|---|---|---|
| `normal` | feasible | 225,276 | 48,728 | 3,712 | 2,988 | 1,534 | yes |
| `increased_costs` | feasible | 268,996 | 49,396 | 3,712 | 3,136 | 1,200 | **no**: cash 268,996-270,331, request-days 48,728-49,396 over the optimal plans; 4 days not determined |
| `delayed_surge` | feasible | 220,163 | 55,539 | 3,712 | 3,669 | 0 | yes |
| `reduced_budget` | infeasible (relaxed plan) | 150,000 | 110,244 | 6,343 | 2,500 | 0 | yes |
| `m2_bias_shift` | infeasible (relaxed plan) | 300,000 | 111,440 | 8,398 | 5,000 | 0 | yes |
| `increased_demand` | infeasible (relaxed plan) | 300,000 | 505,359 | 34,115 | 5,000 | 0 | yes |
| `reduced_capacity` | infeasible (relaxed plan) | 300,000 | 502,944 | 33,718 | 5,000 | 0 | yes |
| `combined_stress` | infeasible (relaxed plan) | 225,000 | 575,701 | 37,496 | 3,409 | 0 | yes |
| `m3_marginal_q90_stress` | infeasible (relaxed plan) | 300,000 | 813,258 | 56,612 | 5,000 | 0 | yes |

- In the three feasible scenarios the HYPOTHETICAL target (4,000) does not bind: the plans
  end at 3,712 because waiting is penalised, and dropping the target leaves the optimal score
  unchanged. Regular capacity binds on all 30 days; the budget, the overtime total and the
  terminal target do not (their shadow prices are 0).
- **Why the six scenarios are infeasible** (DIAGNOSTIC values, not plans or requests):
  in `reduced_budget` and `m2_bias_shift` the target is reachable, but only with at least
  212,474 and 424,433 USD of discretionary cash under these prices (the budgets are 150,000 and
  300,000). In `increased_demand`, `reduced_capacity`, `combined_stress` and the q0.9 stress
  path no amount of cash reaches it: with the daily and total limits, at least 9,865, 9,468,
  13,866 and 32,362 requests stay above the target. The relaxed plans spend the whole budget
  (all on overtime, the cheaper source per request) and are labelled
  `relaxed_terminal_target` everywhere.
- The q0.9 stress path is each day's marginal 90 % M3 quantile (16.7 % more demand than the
  point forecast); the M3 intervals failed calibration, so it carries no probability.

### 9.2 Policies under the same assumptions

The fixed-capacity rule misses the HYPOTHETICAL target in every scenario (11,968 requests left
in `normal`). The backlog-trigger rule meets every constraint in the three feasible
scenarios; there the reported plan is a **tradeoff**, not an improvement on every measure: in
`normal` it spends 8,802 USD more discretionary cash and has 62,850 fewer request-days and 288
fewer requests left at the end. Compared like for like, the LP at the rule's own cash has
57,280 fewer request-days, and the LP at the rule's request-days and terminal backlog needs
4,000 USD less (`increased_costs`: 57,280 fewer, 4,800 USD less; `delayed_surge`: 54,136
fewer, the same cash). In `reduced_budget`, `combined_stress` and `m2_bias_shift` the relaxed
plan dominates the rule (same cash, fewer request-days, lower terminal backlog); in
`increased_demand`, `reduced_capacity` and the q0.9 path it has more request-days but a lower
terminal backlog. These differences come from a myopic rule with an arbitrary trigger and from
hypothetical prices; they say nothing about any real agency.

### 9.3 Uniqueness, shadow prices, frontier

- Only `increased_costs` (and four sweep points) has several optimal plans: at 1.2 × the
  prices overtime costs exactly 32 USD per request (72 USD/h at 2.25 requests/h), the same as
  the penalty of 16 nights of waiting at 2 USD, so the amount and timing of overtime and surge
  on four days can change without changing the score. The reported plan is the least-cash
  one; the ranges are reported.
- 369 shadow prices of `normal`, `increased_costs` and `delayed_surge` were bracketed by
  certified finite differences: every bracket holds, none at a breakpoint, 7 at a domain
  boundary (surge limits that are zero before day 8 in `delayed_surge`, flagged
  `limit_is_zero_unavailable`).
- Frontier (`normal`): extra discretionary cash beyond 435,458 USD lowers the notional
  penalty no further, with or without the target; the curve is non-increasing and convex, and
  the strict plan lies on it. `combined_stress`: with the target kept no cash cap is
  feasible; without it the penalty stops falling at 851,400 USD. These are DIAGNOSTIC
  tradeoff points under HYPOTHETICAL prices, not budgets.

### 9.4 Sensitivity (`normal`, one parameter at a time)

All 14 proved monotonicity and feasibility properties hold on the results. The strict model
becomes infeasible at 2 % more demand, at 2 % fewer regular hours or 2 % lower productivity, at
half the discretionary budget (feasible again at 0.75) and at 1.5 × the prices. The penalty
weight `w` trades cash against waiting: from `w = 0` to `w = 16` cash rises from 212,474 to
300,000 USD (the whole budget) and request-days fall from 156,673 to 27,272; at `w = 0` the
request-days are not determined (60,442-224,834 over the optimal plans). Two swept capacities
(regular hours or productivity × 1.05, 11,550 requests/day) fall within 1 % of the legacy
demand percentile and are flagged `near percentile`.

### 9.5 Verification of the run

- Certificates: the largest violations in the 27 programme summaries of `metrics.json` are
  `7.4e-8` (primal), `5.4e-8` (stationarity) and `3.1e-8` (duality gap) **of their
  tolerances**, i.e. about 10^7 times below them; no value was snapped onto a bound and no
  lexicographic stage needed the epsilon fallback.
- An independent re-implementation by the reviewers (own variable layout, own LP with
  `highs-ipm` and with `highs-ds` without presolve, own policies and diagnostics, without
  importing the M4 analysis code) agrees with this run on all 180 scenario, policy and
  diagnostic values within `1e-6` relative, on all 369 duals and on every frontier and sweep
  value. Its own optimal-face check, at a slack of `1e-9`, flagged three more sweep points; at
  a slack of `1e-11` their ranges vanish (near-optimal plans, not ties), as the exact face
  says. The sweep point `discretionary_budget_multiplier = 0.75`, non-unique here with zero
  aggregate ranges, has another plan with exactly the optimal score (322,786.97) that moves 77
  surge requests from day 6 to day 3 and 34 overtime hours from day 5 to day 8.

## 10. Independent review

Before the freeze, four reviewers examined the formulation design (units, constraints,
diagnostics, integrity rules); sections 1-8 include their changes. After the freeze (commit
`af10ddd`), six independent reviewers examined the code, configuration, documents and tests,
each from one angle: **formulation against code**, an **independent re-implementation** of the
model and of all nine scenarios, **numerical robustness**, **scientific integrity** of every
output, **mutation testing** of the test suite (108 planted bugs) and **edge cases and
failure paths**. Each major or critical finding was then given to a separate skeptic told to
refute it by reproducing it from scratch. Every finding verified against the frozen code was
upheld; later verifiers, who ran after the fixes had landed, reported each defect as no
longer reproducible and re-ran their own reproductions against the fix. Every confirmed
defect was fixed, with a regression test that fails on the frozen code
(`tests/test_m4_review_fixes.py`, `tests/test_m4_mutation_kills.py`), and the reference
configuration was run for the first time on the fixed code (section 9).

| Defect (lenses that found it) | Severity | Fix |
|---|---|---|
| Numbers of a non-unique optimum depended on the vertex HiGHS returned: the "what the target forces" row showed a tradeoff that was only another optimal vertex, the cash/penalty split of shadow prices and the policy deltas came from an arbitrary vertex, sweep points had no uniqueness test (formulation, independent, integrity) | critical | the reported plan is the least-cash optimal plan; the target effect compares optimal scores; the split compares least-cash plans; every sweep point and relaxed plan has a uniqueness test and ranges |
| Lexicographic stages used up their epsilon cap (a binding budget reported as not binding by 1e-6 USD), and near-parallel cap and face rows made HiGHS fail on near-tied prices, aborting the run (numerics; found again by a regression test) | major | stages solved on the exact optimal face (complementary slackness); epsilon cap only as a recorded fallback (0 used) |
| Uniqueness test: missed a genuine alternative optimum when a near-flat direction also existed; failed on near-ties (numerics) | major | exact complementary-slackness face, every face plan certified and checked to score the optimum |
| Failed checks of sweep points, soft-target re-solves and policy first stages never became errors; finite-difference re-solves were never certified (formulation, numerics, edge cases) | major | every programme is checked and every failure is an ERROR; re-solves certified |
| The certificate accepted NaN duals (Python's `max` skips NaN) (numerics) | major | fails closed on any non-finite value |
| Absolute finite-difference step: false breakpoints and bracket ERRORs when every input is scaled by 100-300 (numerics) | major | relative step and value-noise allowance; scale-invariance test |
| An infeasible verdict was never confirmed independently; at the tolerance boundary the reasons were empty or said "0.0 requests" (integrity, numerics) | major | confirmation by the certified soft-target programme; BORDERLINE reason; three significant digits |
| Diagnosis text hard-coded "(daily limits, totals and delayed surge bind)"; relaxed plans showed no binding constraints although they spend the budget; an unlimited-cash diagnostic plan was described as "budget spent" (independent, formulation, integrity, edge cases; the last found reading the fixed run) | major | reasons and binding constraints read from the plan; an exceeded budget is `budget_exceeded`, never binding |
| Sweeps could reach a capacity within 1 % of the legacy demand percentile without notice (independent, formulation, integrity, edge cases) | major | flagged `near percentile` (protocol amended, section 1) |
| `verify` passed a run whose failed checks reproduce; `inspect` trusted the unhashed `complete` flag; `--require-clean` accepted an untracked configuration; a null budget could be "scaled" by a scenario or sweep (edge cases) | major | all four closed (sections 4 and 12); malformed manifests are usage errors; the intervals pin is always checked |
| The report's scenario table showed the solver's vertex instead of the reported least-cash plan where the optimum is not unique (found reading the fixed run) | major | the table reads the reported plan; `metrics.json` names the programme summary `strict_programme` |
| Labels and units: capacity ratio against the unscaled forecast; sensitivity duals without units; `verify` without the label; budget-request phrasing; q0.9 rows without the "no probability" notice; an uncomputed comparative sentence; "n/a USD"; the surge total missing from the documented model; prose for blocks that were not configured; zero surge limits read as binding (integrity, formulation, independent, edge cases) | minor | each wording, column and label corrected |
| No objective-agreement check for the frontier's penalty objective; the input-magnitude guard fired on derived rows; the certificate margin overstated in this document (independent, formulation, numerics) | minor | check added; derived rows bounded separately; margin measured (section 9) |
| 26 of the 108 planted bugs survived the suite: guards whose failure path was never exercised (objective agreement, bracket lower side, balance-dual sign, monotonicity and frontier checks, policy invariants, snapshot data check, verify on a changed report, `--require-clean` without git), unpinned tolerances (dual signs, binding slack, snapping) (mutation) | major/minor | 22 new tests kill 23 of them; two are equivalent mutants and one concerned code since removed (the epsilon re-test of the old face) |

What the reviewers verified as sound: the LP rows, bounds, coefficients and units against
section 3 (no slack column, service never above the available work, the budget excludes the
regular cost everywhere); all scores, diagnostics, policies, frontiers and sweeps against
the independent re-implementation (section 9.5); byte-identical determinism across runs and
OpenBLAS kernels; labels on every CSV row; multi-unit problems through every analysis; the
CLI in every configuration variant; and that diagnostic and relaxed plans are never presented
as strictly feasible.

## 11. Limitations

- **Hypothetical throughout.** The results illustrate a method; their numbers depend on
  invented parameters, and the regular capacity sits close to the forecast mean, where
  backlog is very sensitive to capacity (see the regular-hours sweep).
- **Forecast error dominates.** The point forecast's historical MAE over 30-day horizons
  (985 requests/day) is about the size of the daily overtime capacity; plans computed for one
  demand path can be far from optimal for the demand that occurs.
- **Deterministic model.** Demand is treated as known. With calibrated predictive
  distributions a stochastic or robust formulation could be considered; with the current
  FAILED_CALIBRATION intervals no probabilistic guarantee can be given, so none is attempted.
- **One pooled unit.** Fully interchangeable capacity across agencies is the most favourable
  assumption; real service is split by agency and request type.
- **Linear and continuous.** No fixed costs, minimum shifts, integer staff or learning; the
  terminal target is a single number at the end of the horizon, not a service level.
- **Local shadow prices.** Valid for small changes of one right-hand side; not unique at
  breakpoints; in USD-equivalent units that mix cash with notional weights.

## 12. Reproduction

```bash
uv sync --locked
uv run atlas optimize run --output-dir runs/m4             # about 30 seconds
uv run atlas optimize verify runs/m4                       # byte-identical result files
```

Runs are deterministic on one machine (no randomness, no time limit, serial dual simplex) and
work offline: every input is a committed, hash-verified file. `verify` reports the SciPy and
HiGHS versions recorded and current; with a different solver build the plan chosen among
alternative tie-broken optima (the daily schedule where the plan is not unique), and so the
plan bytes, may differ even when the optimal score does not. `verify` fails when the
recomputation has a failed check (even if it reproduces the recorded bytes) or when the
manifest's `complete` flag disagrees with it; `inspect` fails when the fingerprinted
`metrics.json` records a failed check; `--require-clean` also requires the configuration to
be a tracked file under `configs/`. **Amended (R2):** the file is matched literally (a file
named like a glob no longer matches a tracked one) and its content must equal the blob
committed at `HEAD` (`git status` misses ignored files and trusts `skip-worktree`). A
malformed manifest or `metrics.json` is a usage error (exit 2) in `verify` and `inspect`.

**Amended (R3).** The third review closed further gaps:
- **The code that ran.** The commit and code hash describe the repository root, but the
  imported `atlas` package could come from elsewhere: another checkout on `PYTHONPATH`, an
  editable install of another tree, or `--repo-root` pointing elsewhere. The manifest now
  records whether the running package is `<repo-root>/src/atlas`. `--require-clean`
  refuses a run when it is not, and `verify` makes that an ERROR.
- **Manifest copies.** The manifest's copies of the label, `validated_recommendation`, the
  configuration fingerprint, the inputs (with the M3 status) and the per-scenario demand
  and feasibility must equal the fingerprinted `metrics.json`. An ERROR otherwise, in both
  commands.
- **Missing or edited files.** A recorded result file that is missing is an ERROR (an M4 run
  always writes all eight). `verify` distinguishes a file altered on disk from a manifest
  entry edited while the file still equals the recomputation.
- **Malformed files.** Nested malformed values, undecodable bytes and recorded output
  names that are not plain file names are usage errors, not internal errors.
- **The `--require-clean` blob check.** It now compares the raw bytes with the committed
  blob (`core.autocrlf` no longer refuses an identical file), and resolves the path from the
  given repository root, which may be a subdirectory of a larger repository.
- **Fallbacks after the run.** `inspect` shows the run's stage fallbacks as a WARNING, and
  the report has a "Run checks" section with the counts of failed checks and fallbacks.
