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
of that configuration**. The results (section 9) and the review (section 10) were added
afterwards.

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
| A demand percentile is never a capacity | Capacity comes only from staff hours × productivity, overtime and surge. A regular capacity within 1 % of the trailing 180-day 75th percentile of demand (11,608.75, the legacy "planning capacity") is rejected. |
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
| `S_max[t]` | surge capacity (0 before the surge start day) | requests/day |
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
sum_t o[t] <= O_total                                       overtime total         staff hours
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
of `1e9` or more are rejected.

Nothing the solver returns is trusted:

1. **Optimality certificate** from the primal and dual values: primal feasibility of every
   row and bound, dual signs (`<=` rows and upper bounds `<= 0`, lower bounds `>= 0`),
   stationarity `c - A_ub'y_ub - A_eq'y_eq - y_lo - y_up = 0` and a zero duality gap
   `c'x = b_ub'y_ub + b_eq'y_eq + lower'y_lo + upper'y_up`. Tolerances: rows and bounds
   `1e-7 + 1e-9 × magnitude`, dual quantities `1e-9 × magnitude` (about 100 times the noise
   observed on realistic instances). Together these prove optimality without the solver's
   status.
2. **Independent plan check** in plain loops, without the LP matrices: non-negativity, the
   balance recursion, available work, capacity, daily limits, totals, terminal target and
   budget, each with tolerance `1e-6 + 1e-9 × magnitude` in its own unit.
3. **Objective agreement**: the solver's objective equals the score recomputed from the plan.
4. Values within tolerance outside a bound (round-off) are snapped onto it and counted;
   anything further out is reported by the check. Signed zeros are normalised before writing.

A failure of any check is an ERROR: the run is marked incomplete.

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
(without the second stage the plan would be arbitrary, for example paid idle overtime).

## 6. Policies and like-for-like comparisons

| Plan | Definition |
|---|---|
| `optimized` | the strict LP (or `relaxed_terminal_target` when infeasible) |
| `optimized_without_terminal_target` | the LP without the HYPOTHETICAL target: shows what the target forces |
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

- **Is the optimal plan unique?** From the reported vertex, the constraints active there
  (bounds and tight rows) determine it. M4 maximises the total distance from those active
  constraints over all plans scoring within `ε = 1e-9 × Σ|c_j x_j|` of the optimum; zero
  distance means no other optimal plan exists. Near-optimal plans move at most `ε / rate`, so
  a positive distance is re-tested with `ε / 100`: a genuine alternative optimum does not
  shrink with `ε`. Ranges over the optimal face of cash, request-days, terminal backlog,
  overtime and surge are reported, and, for a non-unique optimum, the range of extra capacity
  on each day (`plans.csv`). A non-unique plan is labelled "one of several equally scored
  HYPOTHETICAL plans".
- **Shadow prices** are derivatives of the optimal penalised score with respect to one
  right-hand side: per request of demand on day `t` (balance), per request of free regular
  capacity on day `t` (capacity), per USD of budget, per request of terminal target, per
  allowed overtime hour or surge request. Their unit is the penalised score's
  (USD-equivalent), so they are **not wages, prices or values of staff**; no staff-hour
  conversion is reported (adding a staff hour would also add its committed cost). The
  optimal value is convex in every right-hand side, so `backward secant ≤ dual ≤ forward
  secant` must hold for any step: every dual is bracketed with step `0.001` (`locally_unique`
  when both slopes agree, `breakpoint` otherwise: the solver's value is then one subgradient
  among several), at a domain boundary only from above, with an infeasible side as `±inf`.
  A practical step (1 request, 1 staff hour, 100 USD) is reported separately as an average
  effect, split into its cash and notional-penalty parts. Balance duals must be `>= 0` (the
  optimal score never falls when demand rises).
- **Tradeoff frontier** (epsilon constraint): for caps on discretionary cash from 0 to the
  least cash that achieves the least possible penalty, the least notional penalty `w·Σb +
  w_T·b[T]` (then the least cash achieving it), with and without the terminal target, budget
  row dropped. Because the second axis is the penalty actually in the objective, the strict
  optimum must lie on the curve (checked), and the curve must be non-increasing and convex in
  the cap (checked). No point is singled out: it is a HYPOTHETICAL tradeoff, not a budget.
- **Sensitivity sweeps**, one parameter at a time, with the properties proved for the model
  checked on the results:

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

Added after the reference run.

## 10. Independent review

Added after the review.

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
HiGHS versions recorded and current; with a different solver build the vertex chosen among
alternative optima, and so the plan bytes, may differ even when the optimal score does not.
