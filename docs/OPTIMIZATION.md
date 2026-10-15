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
**Amended (R)** below, each with its reason in section 10. Two further independent reviews
examined the fixes, and a verification checked the last round's; their changes are marked
**Amended (R2)** and **Amended (R3)**. The configuration file was not changed. The results
(section 9) come from the code after all of them.

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
  divided by the row's coefficient, over the columns the row touches (since R3 only the
  columns the face leaves free, below).
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
  face of the stage before (`plan_selection` in `scenarios.csv` and `sensitivity.csv`
  names the chain).
  Cash, penalty, terminal backlog and request-days of a reported plan therefore do not
  depend on the vertex HiGHS returns. Its day-by-day schedule, and the split between
  overtime and surge, can still differ between tied plans; the uniqueness test (section 7)
  says when.

**Amended (R3).** The third review found that the round-2 tie-break stages, exact caps and
per-point face tests made the analysis fail at large scale, from about `1e5`-`1e6` requests
a day (and in one benchmark control instance at `3.4e4`). HiGHS's presolve called
programmes that contain a known feasible plan infeasible, and the simplex could not clean
up the unscaled solution (status 15); the result was false ERRORs or an aborted run. It
also found that one large column could still widen a row's zero test. Three independent
fixes were benchmarked on every reproduction and on 69 realistic control instances; the
one adopted works as follows:
- **Exact scaling.** When a right-hand side or finite bound exceeds `2**21`, every
  right-hand side and finite bound is divided by a power of two `K`. That is exact in
  floating point. The solution is mapped back (`x = K x'`); the duals are unchanged.
  The scaled solution must pass the certificate on the original programme; if it does
  not, or HiGHS fails on it, the programme is solved unscaled. Below `2**22` the absolute
  tolerance `1e-9` spans more than two units in the last place, and at `2**22` about one;
  `2**21` keeps a factor-two margin. Every programme of the reference run has `K = 1`.
- **Presolve's infeasible verdicts are confirmed** by a re-solve without presolve, and a
  certified optimum overturns them (`retried`). Before, only statuses 9, 10 and 15 were
  re-solved.
- **No fallback aborts the run.** A stage whose every fallback failed is `solver_failed`:
  an ERROR on that scenario, with the other results still written.
- **Caps are loosened on any failure.** The policy and frontier caps are loosened whenever
  any stage of the exact chain is not optimal, not only the first one (this replaces the
  "only then" above).
- **A row's zero test uses only the columns the face leaves free.** Columns fixed at a
  bound by a nonzero reduced cost, or pinned by an earlier face, are left out: their bound
  multipliers absorb any change of the row dual, so their scale (a large capacity dual,
  say) says nothing about it.
- **The kept-optimum check also bounds the loss through counted-as-zero duals.** The loss,
  against the earlier optimum, through every dual the face counted as zero (rows and
  bounds) must stay within `10 ×` the checking tolerance of the score terms that change.
  This is stricter than the tie rule for genuine near-ties within `1e-9`, which then get a
  recorded face rebuild.
- **The face is rebuilt** from exact zeros whenever any face programme fails.

These changes leave every programme of the reference run unscaled and every computed value
unchanged: `plans.csv`, `duals.csv`, `sensitivity.csv` and `frontier.csv` are byte-identical to
the run before them. `report.md`, `metrics.json` and `scenarios.csv` change only through the
wording and the new fields of the same round.

**Amended (R3, verification).** A further independent check of these fixes found two more
gaps:
- **Statuses the dual simplex cannot conclude.** HiGHS can return statuses other than 9, 10
  and 15, such as 0 (not set) and 4 (solve error), on valid programmes. This happens with
  terminal penalties of `1e8` or more, even at NYC scale, and it aborted the run. Every
  status other than 7 and 8 is now re-solved without presolve. A status that is still
  inconclusive is re-solved by the interior-point method with crossover (`highs-ipm`, named
  in the programme's message); its optimum is used only if the certificate holds on the
  original programme.
- **The planning programme itself is not a fallback.** If no method solves it, the run
  still stops with `solver_failed` and writes no results, as before; only the analyses
  built on it fail soft.

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
- **"Relaxed" means removed.** A daily limit is lifted to all the work of the horizon (at most
  `5e8` a day, see below),
  a total is dropped, and a delayed surge becomes available from the first day. The reason
  now says so, and gives the violation that would remain "without it". The metrics key is
  `least_violation_unlimited_cash_with_one_limit_removed_requests`.
- **A surge configured to start after the horizon** is now tested as well, through its
  configured daily capacity. Before, it was never tested, and the reason could say that no
  limit lowers the violation.
- **One tolerance for "out of reach".** Whether the target is out of reach at any cash
  uses the smallest per-unit tolerance of the plan check, so a total violation within it is
  within every unit's tolerance however it is shared (for the single unit the configuration
  allows, this is exactly the check's tolerance). A scenario whose relaxed plan passes the
  plan check (BORDERLINE) always counts as within reach, so it is never also "out of reach
  at any cash". A removal counts as lowering the violation if it reaches that tolerance, or
  if it lowers the violation by more than `10 ×` the checking tolerance (half the
  violation, if that is smaller).
- **A failed removal is an ERROR.** A removal programme that is not optimal, or that the
  solver refuses, is now an ERROR. The reason names it as not computed, and then claims
  only that no removal that could be computed lowers the violation; it is no longer
  silently left out. A lifted daily limit is capped at half the input magnitude bound
  (`5e8`). If the relaxed plan still misses the target and uses that cap fully on a day
  whose capacity binds, the removal is not exact, and it is reported as not computed too.
  Idle capacity parked at the cap does not count.
- **Configured limits and targets** are printed as configured (1.5 staff hours, not 2;
  9.999997, not 10).
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
  relaxed plan's planning-objective stage: least violation, then the planning objective),
  not from the reported plan. The plan is called unique when no
  optimal plan lies more than `0.001` (requests or staff hours, summed over the active
  constraints) from it: the test is exact up to that threshold, not to zero. If a face plan
  does not score the optimum (the zero tests loosened the face), the face is rebuilt from
  exact zeros and the rebuild is recorded (`face_rebuilt`); a failed face programme is
  recorded as an ERROR instead of aborting the run. (R3: any failed face programme triggers
  the rebuild, a recorded `face_rebuilt` WARNING; it is an ERROR only if the rebuilt face
  fails too, and an incomplete test reports no verdict: `unique` is empty, not "no".) **Amended (R3):** the tie-breaks pin cash,
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
  backlog, then request-days), so penalty, cash, terminal backlog and request-days in
  `frontier.csv` do not depend on the vertex; the overtime and surge totals can, and their
  ranges are reported where they do.
  Each point gets the uniqueness test. `unique_after_tie_breaks` is false where other plans
  tie on all four values with a different daily schedule, and the ranges of the overtime and
  surge totals over those plans are reported. **Amended (R3):** a cap whose least penalty
  is certified, but whose tie-break stage failed after every fallback, is a `solver_failed`
  point: it has its least penalty, no totals, and an ERROR. So is a cap the solver gave no
  verdict on. Neither is called infeasible. `on_frontier_check` is `not_computed` (with an
  ERROR) when the point at the strict plan's cash could not be computed. If a curve's
  least-penalty programme fails, the report says so rather than "no plan meets the target",
  which is said only when that programme is infeasible
  (`least_penalty_programme_status`). Points without a plan are counted
  (`points_solver_failed`), not reported as unique.
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
result fingerprint `0dc4eb0fcc4623c19d8eb5fbd0dc453bd3f75453c2b69e35d3aa470436af3c82`, code
commit `50268dc` (clean tree, `--require-clean`), SciPy 1.18.1 / HiGHS 1.12.0, 23 seconds.
`optimize verify` reproduces every file byte for byte, also under the Haswell and Prescott
OpenBLAS kernels (tested). **0 errors; 7 warnings**: M3 status FAILED_CALIBRATION, and six
scenarios without a plan that meets every HYPOTHETICAL constraint. No stage needed a
fallback (`checks.stage_fallbacks` 0). The configuration is the one committed before the
first run (`af10ddd`); the code has the fixes of the three review rounds and of their
verification (section 10).

**Every number below is computed from HYPOTHETICAL parameters and an unvalidated point
forecast (MAE 985 requests/day over these horizons). It illustrates the method; it is not a
staffing, budget or capacity recommendation, and no number is a probability.**

### 9.1 Scenarios (reported plan)

| Scenario | Strict model | Discretionary cash (USD) | Request-days | Terminal backlog | Overtime h | Surge requests | Optimal plan unique |
|---|---|---|---|---|---|---|---|
| `normal` | feasible | 225,276 | 48,728 | 3,712 | 2,988 | 1,534 | yes |
| `increased_costs` | feasible | 268,996 | 49,396 | 3,712 | 3,136 | 1,200 | **no**: cash 268,996-270,331, request-days 48,728-49,396 over the optimal plans; extra capacity not determined on 4 days |
| `delayed_surge` | feasible | 220,163 | 55,539 | 3,712 | 3,669 | 0 | yes |
| `reduced_budget` | infeasible (relaxed plan) | 150,000 | 110,244 | 6,343 | 2,500 | 0 | yes |
| `m2_bias_shift` | infeasible (relaxed plan) | 300,000 | 111,440 | 8,398 | 5,000 | 0 | yes |
| `increased_demand` | infeasible (relaxed plan) | 300,000 | 505,359 | 34,115 | 5,000 | 0 | yes |
| `reduced_capacity` | infeasible (relaxed plan) | 300,000 | 502,944 | 33,718 | 5,000 | 0 | yes |
| `combined_stress` | infeasible (relaxed plan) | 225,000 | 575,701 | 37,496 | 3,409 | 0 | yes |
| `m3_marginal_q90_stress` | infeasible (relaxed plan) | 300,000 | 813,258 | 56,612 | 5,000 | 0 | yes |

- The values are those of the tie-broken reported plan (least cash, then terminal backlog,
  then request-days among the optimal plans). Every reported plan, `increased_costs`
  included, is unique after the tie-breaks: in `increased_costs` only one optimal plan
  has the least cash.
- In the three feasible scenarios the HYPOTHETICAL target (4,000) does not bind: the plans
  end at 3,712 because waiting is penalised, and dropping the target changes neither the
  optimal score nor the reported plan. Regular capacity binds on all 30 days; the budget,
  the overtime total and the terminal target do not (their shadow prices are 0).
- **Why the six scenarios are infeasible** (DIAGNOSTIC values, not plans or requests):
  - In `reduced_budget` and `m2_bias_shift` the target is reachable, but only with at least
    212,474 and 424,433 USD of discretionary cash under these prices. The budgets are
    150,000 and 300,000.
  - In `increased_demand`, `reduced_capacity`, `combined_stress` and the q0.9 stress path,
    no amount of cash reaches it. With the daily and total limits, at least 9,865, 9,468,
    13,866 and 32,362 requests stay above the target.
  - With unlimited cash and one limit removed at a time:
    - In `increased_demand` and `reduced_capacity`, removing the daily surge limit alone,
      or the overtime total alone, would reach the target.
    - In `combined_stress`, removing the daily surge limit alone would reach it. Without
      the surge delay 9,666 requests would remain, and without the overtime total 366.
    - On the q0.9 path, removing the daily surge limit alone would reach it. Without the
      overtime total 18,862 requests would remain.
    - Removing the daily overtime limit alone lowers none of them.
    - "Removed" means raised above all the work of the horizon, far beyond the 600 surge
      requests a day. So these results say which limits keep the target out of reach, not
      how much more of anything would be needed.
  - The relaxed plans spend the whole budget, all of it on overtime (the cheaper source per
    request), and are labelled `relaxed_terminal_target` everywhere.
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

- **Non-unique optima.** `increased_costs` is the only scenario with several optimal plans:
  at 1.2 × the prices, overtime costs exactly 32 USD per request (72 USD/h at 2.25
  requests/h), the same as the penalty of 16 nights of waiting at 2 USD. So the amount and
  timing of overtime and surge on four days can change without changing the score. Five
  sweep points are not unique either:
  - cost × 1.2: the same tie;
  - discretionary budget × 0.75: another plan with exactly the optimal score moves surge
    and overtime between days, with the same totals (section 9.5);
  - backlog penalty `w = 0`: only cash counts (`w_T = 0`), so the timing of service is
    free (request-days 60,442-224,834 over the optimal plans);
  - `w = 8` and `w = 16`: the whole budget is spent, and with `w_T = 0` plans with the same
    request-days end at different backlogs (2,255-2,421 requests).

  In each case the reported plan is the least-cash optimal plan and, among those, the one
  with the least terminal backlog and then the least request-days. For `increased_costs`
  and cost × 1.2 the least-cash plan has the most request-days of the range (49,396 of
  48,728-49,396), because with `w_T = 0` the score fixes cash plus twice the request-days.
- **Shadow prices.** 369 shadow prices of `normal`, `increased_costs` and `delayed_surge`
  were bracketed by certified finite differences. Every bracket holds; none is at a
  breakpoint, none failed, and none has an unresolved backward step. Seven are at a domain
  boundary: the surge limits that are zero before day 8 in `delayed_surge`. They are flagged
  `limit_is_zero_unavailable` and are not binding.
- **Frontier.**
  - `normal`: extra discretionary cash beyond 435,458 USD lowers the notional penalty no
    further, with or without the target. The curve is non-increasing and convex, and the
    strict plan lies on it. At three of the cash caps, with and without the target, other
    plans tie on penalty, cash, terminal backlog and request-days with a different daily
    schedule; their overtime and surge totals are the same.
  - `combined_stress`: with the target kept no cash cap is feasible; without it the penalty
    stops falling at 851,400 USD.

  These are DIAGNOSTIC tradeoff points under HYPOTHETICAL prices, not budgets.

### 9.4 Sensitivity (`normal`, one parameter at a time)

All 14 proved monotonicity and feasibility properties hold on the results.

- **Feasibility on the grid.** The strict model is:
  - feasible at the forecast demand and infeasible at 1.02 ×;
  - feasible at the configured regular hours and productivity, and infeasible at 0.98 ×;
  - infeasible at half the discretionary budget and feasible at 0.75 ×;
  - feasible at 1.2 × the prices and infeasible at 1.5 ×.
- **The switch points lie between grid points.** A bisection with the same code puts them
  at 1.0098 × demand, 0.9901 × regular hours and 0.9904 × productivity. The bisection is
  not part of the run; `tests/test_m4_results.py` recomputes it. The budget switches at 0.708 × (212,474 / 300,000 USD, the least spend that
  reaches the target), and the prices at 1.412 × (300,000 / 212,474). So the margins of
  the configured `normal` scenario are about 1 % of demand or capacity.
- **The penalty weight `w` trades cash against waiting.** From `w = 0` to `w = 16`, cash
  rises from 212,474 to 300,000 USD (the whole budget), and the request-days of the
  tie-broken plans fall from 60,442 to 27,272. At `w = 0` the score does not determine
  request-days (60,442-224,834 over the optimal plans); the tie-breaks report the least.
- **Near-percentile flags.** Two swept capacities (regular hours or productivity × 1.05,
  11,550 requests/day) fall within 1 % of the legacy demand percentile and are flagged
  `near percentile`.

### 9.5 Verification of the run

- Certificates: 3,292 programmes are certified during the run (every stage, face,
  relaxation, sweep point and finite-difference re-solve). The largest violations are
  `5.2e-7` (primal), `1.8e-6` (dual signs), `4.4e-7` (stationarity) and
  `5.2e-7` (duality gap) **of their tolerances**, so at least 5 × 10^5 times below
  them; no stage needed a fallback.
- An independent re-implementation by the first reviewers agreed with the first reference
  run (code `a885a69`). It has its own variable layout, its own LP (`highs-ipm`, and
  `highs-ds` without presolve), and its own policies and diagnostics, and does not import
  the M4 analysis code. It agreed on all 180 scenario, policy and diagnostic values within
  `1e-6` relative, on all 369 duals, and on every frontier penalty and sweep value it
  compared. It did not compare the frontier's terminal backlog, overtime and surge, which in
  that run were one vertex of a tied face. The current run differs from that run:
  - in labels, wording and new columns (`plan_selection`, `unique_after_tie_breaks`, the
    frontier's tie flags and ranges);
  - at the `w = 0` sweep point, whose values are now those of the tie-broken plan:
    request-days 60,442 instead of 156,673 (the low end of the range 60,442-224,834 that
    both runs computed), peak backlog 7,224 instead of 8,400, and 29 days with backlog
    instead of 30;
  - at one frontier point (`normal`, target dropped, cap 174,183 USD), at the same penalty
    and cash: terminal backlog 5,517 instead of 5,586, overtime 2,579 instead of 2,303 h,
    and surge 648 instead of 1,200. This is the tie-broken plan of a tied face; the third
    review's verification re-derived it with the reviewers' own LP;
  - in `duals.csv`: the slack of four `increased_costs` rows, now at the reported plan, and
    the seven zero surge limits of `delayed_surge`, no longer `binding`;
  - in the last digits (at most `2.4e-13` relative) of one `combined_stress`
    policy-comparison plan and of four frontier points.
- The second reviewers re-checked all 9 scenarios and every sweep point with their own LP:
  optimum, least cash, relaxed values and uniqueness verdicts all agree.
- The re-implementation's own optimal-face check, at a slack of `1e-9`, flagged three more
  sweep points. At a slack of `1e-11` their ranges vanish: they are near-optimal plans, not
  ties, as the exact face says.
- The sweep point `discretionary_budget_multiplier = 0.75`, non-unique here with zero
  aggregate ranges, has another plan with exactly the optimal score (322,786.97). It moves
  77 surge requests from day 3 to day 6, and 34 overtime hours from day 8 to day 5.

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

**Second review (of the fixes).** Four further independent reviewers examined the fixed code
(commit `e3eb4e9`) and the first reference run, each from one angle: **regressions** the
fixes introduced, the **mathematics of the optimal face**, the **claims** in this document
and the outputs, and the **scientific integrity** of every output. They reported 35
findings, 14 of them major. A separate skeptic re-checked each major finding and upheld all
14, four of them at a lower severity. The findings were fixed in commit `0794ba7` (code,
with regression tests in `tests/test_m4_review_round2.py` and updated earlier tests), in
`66de697` and `a7ca6a6` (the method text, marked **Amended (R2)** in sections 4-7 and 12)
and in the results commit (section 9).

| Defect (reviewers that found it) | Severity | Fix |
|---|---|---|
| The least-cash plan still left terminal backlog, request-days, the frontier's terminal backlog, overtime and surge, and `duals.csv` slack to the vertex HiGHS returned; the "forces nothing" row could show a plan that is not the least-cash plan without the target (integrity, regressions) | major | explicit tie-breaks (cash, terminal backlog, request-days) for every reported, relaxed, policy-comparison, frontier and sweep plan; `frontier.csv` flags points that stay open; `duals.csv` describes the reported plan; the "forces nothing" row only when the target changes neither the score nor the tie-broken plan |
| Diagnosis reasons named limits that bind only at an arbitrary vertex, attributed aggregated counts to every unit, and added delayed-surge text whether or not the delay mattered (integrity, regressions, claims) | major | each limit named only if relaxing it alone lowers the least violation (section 5) |
| Exact policy caps made the same-backlog LP falsely infeasible at large scale (an ERROR on a feasible scenario) (regressions) | major | exact caps first, epsilon caps as a recorded fallback |
| One large objective weight widened the face's tie tolerance for every column, so real price differences counted as ties; the finite-difference split and some stages were not checked to keep the optimum (face mathematics) | major | per-column and per-row zero tests; every stage checked to keep the optimum, with a rebuild from exact zeros |
| A failed tie-break stage of a feasible scenario was written as "infeasible" without an ERROR (claims) | major | every stage that is not optimal is a problem; the earlier optimum lies on its face |
| Most epsilon-cap fallbacks were recorded nowhere, so "0 used" could not be checked from the artifacts (regressions, claims) | major | every fallback recorded per stage, per scenario and run-wide, with a WARNING |
| `scenarios.csv` said an infeasible strict programme had a unique optimal plan selected by least cash; sub-unit violations printed as `0`; this document miscounted the non-unique sweep points and presented grid points as feasibility thresholds (integrity, claims) | major | blank fields for infeasible rows; three significant digits below one unit; section 9 rewritten |
| `--require-clean` accepted an ignored file named like a glob of a tracked one, and a `skip-worktree` edit (regressions) | minor (skeptic) | literal pathspec and blob-hash comparison (section 12) |
| An optimum feasible only at solver tolerance made every face programme infeasible (ERROR, "not unique"); finite-difference noise used the nominal step when the backward step was cut to the right-hand side; a NaN slope was a "breakpoint" (regressions) | minor | face widened to the optimum's own activity; noise per step taken; `solver_failed` |
| Coefficients HiGHS drops (`<= 1e-9`) turned into run ERRORs (face mathematics) | minor | rejected up front with a request to rescale |
| Malformed `manifest.json` or `metrics.json` gave internal errors; `verify` printed "results DIFFERENT" with every file identical when only an on-disk copy was altered (regressions) | minor | usage errors (exit 2); "recomputation identical; altered on disk" |
| Wording and units: least spend rounded while the shortfall was printed to 3 digits; "no dual was bracketed" when the bracketed scenario had no horizon-wide row; the bracketed scenarios called "the scenarios listed"; uniqueness called "exact" and "complete"; the distance described as measured from the reported vertex; a zero surge limit still `binding`; the frontier described as cash against request-days; a stale "100 times the noise" comment (regressions, face mathematics, claims, integrity) | minor | each corrected; the uniqueness threshold (0.001) is stated wherever uniqueness is |

**Corrections to the record of the first review.** Five statements of the first round's
record overstated its fixes, as the second review found:
- the least-cash plan was said to remove the vertex dependence of a non-unique optimum
  (the critical row), but terminal backlog, request-days and the frontier's totals still
  depended on the vertex;
- section 9.5 said the re-implementation agreed on every frontier value, but its
  frontier comparison covered penalties only;
- the epsilon cap was a "recorded fallback" only in four programme summaries, not
  everywhere;
- zero surge limits were still marked `binding` in `duals.csv`;
- `--require-clean` still had the two bypasses listed.

All five are fixed now, by the tie-breaks, the rewritten section 9, the fallback record,
the binding rule and the blob check.

**Third review (of the second round's fixes).** Five further independent reviewers examined
commit `0794ba7` and its reference run, each from one angle: the **lexicographic solve
layer and optimal face**, the **diagnosis, finite differences and duals**, **run integrity**,
the **claims** of every output and of sections 1-8, and **mutation testing** (59 planted
bugs in the code changed in round 2). They reported 38 findings, 12 of them major. A skeptic
re-checked each major finding by reproducing it; it upheld 3 as major and 9 as minor. Every
finding was fixed, with regression tests (`tests/test_m4_round3_*.py`); the method changes
are marked **Amended (R3)** in sections 4, 5, 7 and 12.

| Defect (reviewers that found it) | Severity | Fix |
|---|---|---|
| The round-2 tie-break stages, exact caps and per-point face tests failed at large scale: presolve called programmes that contain a known feasible plan infeasible, and the simplex failed on unscaled values (status 15). This gave false ERRORs from about 1e5-1e6 requests a day (once at 3.4e4 on a benchmark control instance), aborted runs from about 300 times the reference scale, and frontier points shown as "optimal" without values (solve layer; a regression from round 2) | major (skeptics; two findings) | exact power-of-two scaling, confirmation of presolve's infeasible verdicts, fail-soft fallbacks, honest frontier statuses (sections 4 and 7, R3), chosen from three independent fixes by a judged benchmark. The benchmark has 34 reproductions and 69 realistic control instances: all pass, against 5 of 34 and 68 of 69 at `66de697` |
| A row's zero threshold set by an unrelated large column counted a genuine budget dual as zero, so the reported least-cash plan was not optimal, with no warning (solve layer; pre-existing since round 1) | minor (skeptic) | the row test uses only free columns, and the kept-optimum check bounds the loss through every dual counted as zero |
| A later stage under exact policy caps could still be called infeasible (diagnosis) | minor | caps loosened on any failing stage |
| `--require-clean` and the code hash described the `--repo-root` checkout, not the `atlas` package that ran (another checkout on `PYTHONPATH`, an editable install of another tree), so modified code could be recorded as a clean commit (integrity; pre-existing since `af10ddd`, the same pattern exists in the M2 and M3 runners) | major | the manifest records whether the running package is `<repo-root>/src/atlas`; `--require-clean` refuses otherwise and `verify` makes it an ERROR (M2 and M3 are unchanged; see section 11) |
| Sections 4-7 still described the round-1 method (a global tie tolerance, a metrics field that no longer existed, the old diagnosis and "forces nothing" rules) (solve layer, diagnosis, claims, mutation) | minor (skeptics) | those passages corrected and the round-2 changes recorded |
| A surge configured to start after the horizon was never tested by the diagnosis, which then said no limit lowers the violation; three tolerances disagreed, so a scenario could be "out of reach at any cash" and BORDERLINE at once; a failed limit relaxation was silently "does not lower it", and a lifted limit beyond the magnitude bound aborted the run; limits below one unit printed as `0`; "relaxed" did not say "removed" (diagnosis, claims, mutation) | minor | section 5 (R3) |
| A failed practical re-solve was not `solver_failed`; a backward step cut to a tiny right-hand side made any two slopes "agree" (diagnosis) | minor | `solver_failed` for any failed slope; `backward_unresolved` |
| "So every reported total is determined": the overtime/surge split and daily schedule could still depend on the vertex, and the reported plan itself was never tested after the tie-breaks (claims) | minor | the reported plan is tested on the face of its last tie-break stage (`unique_after_tie_breaks`); the sentence narrowed |
| `verify` and `inspect` passed a manifest whose labels, M3 status or feasibility contradicted `metrics.json`, and a run with a result file deleted; nested malformed values and undecodable bytes gave internal errors; the blob check refused an identical CRLF file under `core.autocrlf` and a root below the git top level; fallbacks were invisible after the run; `verify` text called an edited manifest entry "altered on disk" (integrity) | minor | section 12 (R3) |
| 34 non-equivalent surviving mutants: binding-target and target-trim wiring, policy-comparison, frontier and relaxed tie-breaks, the kept-optimum check and face rebuild, the limit-relaxation tolerance and construction, fallback accounting, the epsilon cap's value, the face's row test, finite-difference statuses, the headline `binding`, shape checks and sub-unit formatting (mutation) | minor | new tests kill each of them (the suite also has the regenerated reference files as a golden test again) |

**Verification of the third round's fixes.** Five further reviewers re-ran every
reproduction of the third round against the fixed code (`3f3660a`) and reviewed the
changes; a skeptic re-checked each major finding. Of 47 checked findings, 38 were fixed, 8
partly (gaps in tests or wording) and 1 not (a test of the epsilon cap's value). Among 31 new
findings, three were upheld as major:
- HiGHS statuses 0 (not set) and 4 (solve error) aborted runs with big-M terminal penalties
  (`1e8` and more), even at NYC scale. They are now re-solved without presolve and then by
  the interior-point method, certified (section 4, R3 verification).
- The committed reference results were stale; the results commit regenerates them.
- A draft of section 9.5 overstated what the independent re-implementation had checked. It
  was corrected before it was committed.

The other findings (wording, untested branches, edge cases of the new checks) were fixed,
with mutation-checked tests (`tests/test_m4_verification_*.py`).

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
- **Provenance of earlier milestones.** The M2 and M3 runners record the commit and code
  hash of the repository root without checking that the imported `atlas` package is that
  root's (section 12, R3). M4 left them unchanged, as it left all M0-M3 behaviour unchanged.
  Their documented reference runs use `uv run` inside the checkout, which imports that
  checkout's own package.

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
- **Fallbacks after the run.** `inspect` shows the run's stage fallbacks as a WARNING,
  `verify` reports the recomputation's fallback WARNINGs, and the report has a "Run checks"
  section with the counts of failed checks and fallbacks.
- **Verification follow-ups (R3).**
  - `run` warns when the running package is not the repository's, and `inspect` warns on a
    manifest that records it.
  - A symlinked `src/atlas` never counts as the repository's package.
  - The manifest's copies of the data provenance, the solver settings and versions, the
    run id and the result-file list are cross-checked as well. `inspect` also checks the
    embedded configuration against its fingerprint.
  - In `verify` an ERROR names the recomputation it compares with.
  - Integers beyond Python's digit limit and output names that do not encode are usage
    errors.
