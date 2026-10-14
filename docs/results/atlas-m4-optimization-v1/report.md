# Hypothetical decision simulation: atlas-m4-optimization-v1

**HYPOTHETICAL DECISION SIMULATION: exploratory resource-allocation scenarios computed from an unvalidated point forecast with HYPOTHETICAL operational parameters. Not a validated staffing, budget or service-capacity recommendation for any NYC agency.**

**M3 calibration status: FAILED_CALIBRATION.** The selected intervals FAILED the pre-registered calibration criteria on the evaluation period. Decision-grade status is withheld; the intervals are reported as they are, not adjusted after the fact. No result below is decision-grade, and no probability of meeting any target is computed.

Nothing in this report is a validated staffing, budget or service-capacity recommendation for any NYC agency.

## Inputs

- Demand: M2 future projection `atlas-m2-rolling-origin-v1-78da64e8-5ab8b3a5` from origin 2026-06-30 (model `atlas_linear_regression`), 2026-07-01..2026-07-30 (30 days, mean 11,132 requests/day, total 333,968). FUTURE PROJECTION: forecasts beyond the last observed day. No outcome has been observed; these numbers are not validated by any backtest.
- Historical error of that model (M2 confirmation period, horizons 1-30): MAE 985 requests/day (WAPE 9.2 %), mean error -256 requests/day (prediction - actual; negative is under-forecast). A plan built on this forecast inherits that error.
- Uncertainty: M3 run `atlas-m3-intervals-v1-5afd3206-5ab8b3a5-m3_selection-m3_evaluation`, interval status `failed_calibration`, decision-grade: no.
- Legacy demand percentile (trailing 180-day 75th percentile at the origin, the legacy "planning capacity"): 11,608.75 requests/day. It is a demand statistic and is never used as a capacity.

- Stress path q0.9: HYPOTHETICAL STRESS PATH: each day's marginal M3 quantile, taken day by day. It is not a joint scenario, not a quantile of total demand and carries no probability; the M3 intervals it comes from failed their calibration criteria. On the M3 evaluation period the share of outcomes at or below this quantile was 87.9 % over horizons 1-30.

## Structural assumptions

1. Demand is the daily count of 311 service requests created (intake), not measured workload: one request is one unit of work, whatever its type or agency.
2. One city-wide pool: staff and capacity are fully interchangeable across the agencies and request types that the city-wide count mixes. A split by agency with the same total capacity can only leave as much backlog or more, so pooled backlogs are optimistic.
3. The repository holds no closures, backlog, staffing, service-level or cost data: every operational parameter, the initial backlog included, is HYPOTHETICAL.
4. Quantities are continuous (fractional requests and staff hours); arrivals can be served on the day they arrive; capacity not used on a day is lost.
5. Demand is treated as known when planning (a deterministic model of the M2 point forecast or of a labelled stress path), although the forecast's historical error is large and the M3 intervals failed their calibration criteria; no chance constraint and no probability of meeting any target is computed.
6. The penalised score adds HYPOTHETICAL notional penalty weights to discretionary cash. It ranks plans for one set of weights; it is not a cost and is never compared as one.

## HYPOTHETICAL parameters

| scope | parameter | value | unit | assumption |
|---|---|---|---|---|
| unit:citywide | initial_backlog | 8,000.00 | requests | Illustrative open backlog at the start of the horizon (about 0.7 days of forecast demand); no backlog is observed in the data. |
| unit:citywide | regular_hours_per_day | 4,400.00 | staff_hours_per_day | Illustrative regular staff time per day (for example 550 staff working 8 hours); with the regular productivity it gives 11,000 requests per day, 1.2 % below the forecast mean. |
| unit:citywide | regular_productivity | 2.50 | requests_per_staff_hour | Illustrative number of requests one staff hour resolves; no productivity is measured in the data. |
| unit:citywide | overtime_productivity | 2.25 | requests_per_staff_hour | Illustrative: 10 % below regular productivity, for fatigue. |
| unit:citywide | overtime_max_hours_per_day | 400.00 | staff_hours_per_day | Illustrative daily overtime limit (about 9 % of regular hours). |
| unit:citywide | overtime_total_hours | 6,000.00 | staff_hours | Illustrative overtime limit over the 30 days (half the daily limit on average). |
| unit:citywide | surge_max_per_day | 600.00 | requests_per_day | Illustrative capacity of an external surge contract. |
| unit:citywide | surge_available_from_day | 1.00 | planning_day | Illustrative: the surge contract is available from the first day in the base case. |
| unit:citywide | regular_wage | 40.00 | usd_per_staff_hour | Illustrative loaded cost of a regular staff hour; not an NYC pay rate. |
| unit:citywide | overtime_wage | 60.00 | usd_per_staff_hour | Illustrative overtime cost (1.5 times the regular wage): 26.67 USD per request at overtime productivity. |
| unit:citywide | surge_price | 30.00 | usd_per_request | Illustrative price of one externally processed request (more than overtime per request). |
| unit:citywide | backlog_penalty | 2.00 | usd_per_request_day | Notional weight on each request waiting overnight; a preference for less waiting, not a cash cost. |
| unit:citywide | terminal_penalty | 0.00 | usd_per_request | No extra notional weight on the backlog left after the last day (the terminal target constrains it instead). |
| unit:citywide | terminal_backlog_max | 4,000.00 | requests | Illustrative target for the backlog left after the last day (half of the initial backlog). |
| unit:citywide | backlog_trigger | 4,000.00 | requests | Trigger of the backlog-trigger rule, set equal to the terminal target. |
| budget | discretionary_budget | 300,000.00 | usd | Illustrative cash available for overtime and surge over the 30 days; the regular staff cost is committed and reported separately. |
| scenario:increased_demand | demand_multiplier | 1.10 | ratio | Illustrative uniform demand increase of 10 %. |
| scenario:reduced_capacity | regular_hours_multiplier | 0.90 | ratio | Illustrative 10 % loss of regular staff hours. |
| scenario:increased_costs | cost_multiplier | 1.20 | ratio | Illustrative 20 % increase of every cost rate. |
| scenario:reduced_budget | discretionary_budget_multiplier | 0.50 | ratio | Illustrative 50 % cut of the discretionary budget. |
| scenario:delayed_surge | surge_available_from_day | 8.00 | planning_day | Illustrative one-week delay of the surge contract. |
| scenario:combined_stress | demand_multiplier | 1.05 | ratio | Illustrative demand increase of 5 %. |
| scenario:combined_stress | regular_hours_multiplier | 0.95 | ratio | Illustrative 5 % loss of regular staff hours. |
| scenario:combined_stress | cost_multiplier | 1.10 | ratio | Illustrative 10 % increase of every cost rate. |
| scenario:combined_stress | discretionary_budget_multiplier | 0.75 | ratio | Illustrative 25 % cut of the discretionary budget. |
| scenario:combined_stress | surge_available_from_day | 8.00 | planning_day | Illustrative one-week delay of the surge contract. |
| scenario:m2_bias_shift | demand_shift_per_day | 256.00 | requests_per_day | M2 confirmation-period mean error of the recommended model over horizons 1-30 was -255.9 requests per day (under-forecast); applied here as a HYPOTHETICAL stress, not as a correction. |
| sensitivity:normal | demand_multiplier | 0.9, 0.95, 0.98, 1, 1.02, 1.05, 1.1 | ratio | Sensitivity grid: this parameter varied one value at a time, everything else as in the base scenario. |
| sensitivity:normal | discretionary_budget_multiplier | 0, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3 | ratio | Sensitivity grid: this parameter varied one value at a time, everything else as in the base scenario. |
| sensitivity:normal | regular_hours_multiplier | 0.9, 0.95, 0.98, 1, 1.02, 1.05, 1.1 | ratio | Sensitivity grid: this parameter varied one value at a time, everything else as in the base scenario. |
| sensitivity:normal | productivity_multiplier | 0.9, 0.95, 0.98, 1, 1.02, 1.05, 1.1 | ratio | Sensitivity grid: this parameter varied one value at a time, everything else as in the base scenario. |
| sensitivity:normal | cost_multiplier | 0.8, 0.9, 1, 1.1, 1.2, 1.5 | ratio | Sensitivity grid: this parameter varied one value at a time, everything else as in the base scenario. |
| sensitivity:normal | backlog_penalty | 0, 0.5, 1, 2, 4, 8, 16 | usd_per_request_day | Sensitivity grid: this parameter varied one value at a time, everything else as in the base scenario. |

## Model

Linear programme per day `t` (requests served `x`, overtime hours `o`, surge requests `s`, end-of-day backlog `b`, initial backlog `b0`):

```
b[t] = b[t-1] + d[t] - x[t]              backlog balance (requests)
x[t] <= p*H[t] + q*o[t] + s[t]           capacity (requests/day)
0 <= o[t] <= O_max,  sum o <= O_total    overtime limits (staff hours)
0 <= s[t] <= S_max[t], sum s <= S_total  surge limits (S_max 0 before availability)
sum (c_ot*o[t] + c_s*s[t]) <= D          discretionary budget (USD)
b[T] <= target,  x, b >= 0               terminal backlog target (requests)
minimise  sum (c_ot*o[t] + c_s*s[t]) + w*sum b[t] + w_T*b[T]   (penalised score)
```

Service never exceeds the work available (`x[t] <= b[t-1] + d[t]` follows from the balance and `b >= 0`). Solved with HiGHS dual simplex; every plan is checked independently (constraints recomputed in plain loops), every optimum is certified from its primal and dual values (feasibility, dual signs, stationarity, zero duality gap), and no slack variable exists in the planning model. When no plan meets the HYPOTHETICAL terminal target, the reported plan is labelled `relaxed_terminal_target`: least target violation within the budget first, then the planning objective, then the least cash. When several plans share the optimal score, the reported plan is the least-cash one, and the ranges over all optimal plans are shown in the uniqueness table.

## Scenarios

`plan` is `strict` when a plan meets every HYPOTHETICAL constraint and `relaxed_terminal_target` otherwise; the plan shown is the least-cash optimal one. Binding constraints are read from the plan itself (slack within tolerance).

| scenario | demand basis | demand total (vs point forecast) | regular capacity/day | discretionary budget (USD) | plan | discretionary cash (USD) | request-days | terminal backlog | peak backlog | binding constraints |
|---|---|---|---|---|---|---|---|---|---|---|
| `normal` | point_forecast | 333,968 (0.0 %) | 11,000 | 300,000 | `strict` | 225,276 | 48,728 | 3,712 | 6,624 | capacity (30 days), overtime_daily_limit (7 days), surge_daily_limit (2 days) |
| `increased_demand` | point_forecast | 367,365 (10.0 %) | 11,000 | 300,000 | `relaxed_terminal_target` | 300,000 | 505,359 | 34,115 | 34,115 | budget, capacity (30 days), overtime_daily_limit (12 days), terminal_backlog_violated |
| `reduced_capacity` | point_forecast | 333,968 (0.0 %) | 9,900 | 300,000 | `relaxed_terminal_target` | 300,000 | 502,944 | 33,718 | 33,718 | budget, capacity (30 days), overtime_daily_limit (12 days), terminal_backlog_violated |
| `increased_costs` | point_forecast | 333,968 (0.0 %) | 11,000 | 300,000 | `strict` | 268,996 | 49,396 | 3,712 | 6,624 | capacity (30 days), overtime_daily_limit (7 days), surge_daily_limit (2 days) |
| `reduced_budget` | point_forecast | 333,968 (0.0 %) | 11,000 | 150,000 | `relaxed_terminal_target` | 150,000 | 110,244 | 6,343 | 7,224 | budget, capacity (30 days), overtime_daily_limit (6 days), terminal_backlog_violated |
| `delayed_surge` | point_forecast | 333,968 (0.0 %) | 11,000 | 300,000 | `strict` | 220,163 | 55,539 | 3,712 | 7,224 | capacity (30 days), overtime_daily_limit (8 days) |
| `combined_stress` | point_forecast | 350,666 (5.0 %) | 10,450 | 225,000 | `relaxed_terminal_target` | 225,000 | 575,701 | 37,496 | 37,496 | budget, capacity (30 days), overtime_daily_limit (8 days), terminal_backlog_violated |
| `m2_bias_shift` | point_forecast | 341,648 (2.3 %) | 11,000 | 300,000 | `relaxed_terminal_target` | 300,000 | 111,440 | 8,398 | 8,398 | budget, capacity (30 days), overtime_daily_limit (12 days), terminal_backlog_violated |
| `m3_marginal_q90_stress` | per_day_marginal_quantile_path | 389,862 (16.7 %) | 11,000 | 300,000 | `relaxed_terminal_target` | 300,000 | 813,258 | 56,612 | 56,612 | budget, capacity (30 days), overtime_daily_limit (12 days), terminal_backlog_violated |

- `normal`: dropping the HYPOTHETICAL target does not change the optimal score (it forces nothing).
- `increased_costs`: dropping the HYPOTHETICAL target does not change the optimal score (it forces nothing).
- `delayed_surge`: dropping the HYPOTHETICAL target does not change the optimal score (it forces nothing).
- `increased_demand` has **no plan that meets every HYPOTHETICAL constraint** (DIAGNOSTIC values, not plans):
  - the HYPOTHETICAL terminal-backlog target cannot be reached with any amount of discretionary cash: the least violation is 9,864.5 requests (in the least-violation plan: overtime total (6,000 staff hours) used up; daily overtime limit reached on 15 days; daily surge limit reached on 30 days)
  - least terminal-target violation within the budget: 30,115 requests; with unlimited discretionary cash: 9,865 requests
- `reduced_capacity` has **no plan that meets every HYPOTHETICAL constraint** (DIAGNOSTIC values, not plans):
  - the HYPOTHETICAL terminal-backlog target cannot be reached with any amount of discretionary cash: the least violation is 9,467.8 requests (in the least-violation plan: overtime total (6,000 staff hours) used up; daily overtime limit reached on 15 days; daily surge limit reached on 30 days)
  - least terminal-target violation within the budget: 29,718 requests; with unlimited discretionary cash: 9,468 requests
- `reduced_budget` has **no plan that meets every HYPOTHETICAL constraint** (DIAGNOSTIC values, not plans):
  - under this deterministic demand path and the HYPOTHETICAL prices, no plan spending less than 212,474 USD on overtime and surge reaches the HYPOTHETICAL terminal-backlog target (62,474 USD above the HYPOTHETICAL budget; DIAGNOSTIC, not a budget request)
  - least terminal-target violation within the budget: 2,343 requests; with unlimited discretionary cash: 0 requests
- `combined_stress` has **no plan that meets every HYPOTHETICAL constraint** (DIAGNOSTIC values, not plans):
  - the HYPOTHETICAL terminal-backlog target cannot be reached with any amount of discretionary cash: the least violation is 13,866.2 requests (in the least-violation plan: overtime total (6,000 staff hours) used up; surge unavailable on the first 7 days; daily overtime limit reached on 15 days; daily surge limit reached on 23 days)
  - least terminal-target violation within the budget: 33,496 requests; with unlimited discretionary cash: 13,866 requests
- `m2_bias_shift` has **no plan that meets every HYPOTHETICAL constraint** (DIAGNOSTIC values, not plans):
  - under this deterministic demand path and the HYPOTHETICAL prices, no plan spending less than 424,433 USD on overtime and surge reaches the HYPOTHETICAL terminal-backlog target (124,433 USD above the HYPOTHETICAL budget; DIAGNOSTIC, not a budget request)
  - least terminal-target violation within the budget: 4,398 requests; with unlimited discretionary cash: 0 requests
- `m3_marginal_q90_stress` has **no plan that meets every HYPOTHETICAL constraint** (DIAGNOSTIC values, not plans):
  - the HYPOTHETICAL terminal-backlog target cannot be reached with any amount of discretionary cash: the least violation is 32,361.7 requests (in the least-violation plan: overtime total (6,000 staff hours) used up; daily overtime limit reached on 15 days; daily surge limit reached on 30 days)
  - least terminal-target violation within the budget: 52,612 requests; with unlimited discretionary cash: 32,362 requests

## Policies under the same assumptions

Every plan faces the same demand, capacities, prices, limits and budget and is scored by the same evaluator. `fixed_capacity` uses regular staff only; `backlog_trigger` buys the cheaper extra capacity whenever the backlog would exceed its HYPOTHETICAL trigger, without planning ahead. A plan that violates the HYPOTHETICAL terminal target is not comparable on score. Relation: the reported plan compared with the policy on discretionary cash, request-days and terminal backlog (lower is better on each): `dominates`, `dominated`, `tradeoff` or `equal`.

| scenario | policy | reported plan | constraints the policy violates | comparable on score | relation | reported plan's cash vs policy | request-days vs policy | terminal backlog vs policy | LP at the policy's cash: request-days vs policy | LP at the policy's backlog: cash vs policy |
|---|---|---|---|---|---|---|---|---|---|---|
| `normal` | `fixed_capacity` | `strict` | terminal_backlog | no | `tradeoff` | 225,276 USD more | 215,416 request-days fewer | 8,256 requests fewer | the same request-days | the same USD |
| `normal` | `backlog_trigger` | `strict` | none | yes | `tradeoff` | 8,802 USD more | 62,850 request-days fewer | 288 requests fewer | 57,280 request-days fewer | 4,000 USD less |
| `increased_demand` | `fixed_capacity` | `relaxed_terminal_target` | terminal_backlog | no | `tradeoff` | 300,000 USD more | 272,700 request-days fewer | 11,250 requests fewer | the same request-days | the same USD |
| `increased_demand` | `backlog_trigger` | `relaxed_terminal_target` | terminal_backlog | no | `tradeoff` | the same USD | 12,843 request-days more | 476 requests fewer | 4,257 request-days fewer | 4,173 USD less |
| `reduced_capacity` | `fixed_capacity` | `relaxed_terminal_target` | terminal_backlog | no | `tradeoff` | 300,000 USD more | 272,700 request-days fewer | 11,250 requests fewer | the same request-days | the same USD |
| `reduced_capacity` | `backlog_trigger` | `relaxed_terminal_target` | terminal_backlog | no | `tradeoff` | the same USD | 14,601 request-days more | 450 requests fewer | 2,499 request-days fewer | 2,583 USD less |
| `increased_costs` | `fixed_capacity` | `strict` | terminal_backlog | no | `tradeoff` | 268,996 USD more | 214,749 request-days fewer | 8,256 requests fewer | the same request-days | the same USD |
| `increased_costs` | `backlog_trigger` | `strict` | none | yes | `tradeoff` | 9,228 USD more | 62,182 request-days fewer | 288 requests fewer | 57,280 request-days fewer | 4,800 USD less |
| `reduced_budget` | `fixed_capacity` | `relaxed_terminal_target` | terminal_backlog | no | `tradeoff` | 150,000 USD more | 153,900 request-days fewer | 5,625 requests fewer | the same request-days | the same USD |
| `reduced_budget` | `backlog_trigger` | `relaxed_terminal_target` | terminal_backlog | no | `dominates` | the same USD | 16,473 request-days fewer | 150 requests fewer | 17,865 request-days fewer | 4,000 USD less |
| `delayed_surge` | `fixed_capacity` | `strict` | terminal_backlog | no | `tradeoff` | 220,163 USD more | 208,605 request-days fewer | 8,256 requests fewer | the same request-days | the same USD |
| `delayed_surge` | `backlog_trigger` | `strict` | none | yes | `tradeoff` | 7,690 USD more | 59,039 request-days fewer | 288 requests fewer | 54,136 request-days fewer | the same USD |
| `combined_stress` | `fixed_capacity` | `relaxed_terminal_target` | terminal_backlog | no | `tradeoff` | 225,000 USD more | 201,150 request-days fewer | 7,670 requests fewer | the same request-days | the same USD |
| `combined_stress` | `backlog_trigger` | `relaxed_terminal_target` | terminal_backlog | no | `dominates` | the same USD | 732 request-days fewer | 52 requests fewer | 732 request-days fewer | 976 USD less |
| `m2_bias_shift` | `fixed_capacity` | `relaxed_terminal_target` | terminal_backlog | no | `tradeoff` | 300,000 USD more | 271,744 request-days fewer | 11,250 requests fewer | the same request-days | the same USD |
| `m2_bias_shift` | `backlog_trigger` | `relaxed_terminal_target` | terminal_backlog | no | `dominates` | the same USD | 29,304 request-days fewer | 201 requests fewer | 42,200 request-days fewer | 5,356 USD less |
| `m3_marginal_q90_stress` | `fixed_capacity` | `relaxed_terminal_target` | terminal_backlog | no | `tradeoff` | 300,000 USD more | 272,700 request-days fewer | 11,250 requests fewer | the same request-days | the same USD |
| `m3_marginal_q90_stress` | `backlog_trigger` | `relaxed_terminal_target` | terminal_backlog | no | `tradeoff` | the same USD | 15,975 request-days more | 525 requests fewer | 1,125 request-days fewer | 1,350 USD less |

The last two columns compare like with like: the LP restricted to the policy's discretionary cash (least request-days), and the LP restricted to the policy's request-days and terminal backlog (least cash), both without the terminal target that the policies do not plan for. Differences are computed, not assumed; the same value means the policy is already as good on that measure.

## Is the optimal plan unique?

An active-set test over the exact optimal face (complementary slackness with the certified duals; prices tied to within solver accuracy count as tied). When the plan is not unique, the daily schedule shown is one of several equally scored HYPOTHETICAL plans and its day-by-day allocation is not determined by the model; the ranges are over all optimal plans.

| scenario | unique | days not determined | discretionary cash (USD) | request-days | terminal backlog | overtime hours | surge requests |
|---|---|---|---|---|---|---|---|
| `normal` | yes | 0 | 225,276..225,276 | 48,728..48,728 | 3,712..3,712 | 2,988..2,988 | 1,534..1,534 |
| `increased_demand` | yes | 0 | 300,000..300,000 | 505,359..505,359 | 34,115..34,115 | 5,000..5,000 | 0..0 |
| `reduced_capacity` | yes | 0 | 300,000..300,000 | 502,944..502,944 | 33,718..33,718 | 5,000..5,000 | 0..0 |
| `increased_costs` | no | 4 | 268,996..270,331 | 48,728..49,396 | 3,712..3,712 | 2,988..3,136 | 1,200..1,534 |
| `reduced_budget` | yes | 0 | 150,000..150,000 | 110,244..110,244 | 6,343..6,343 | 2,500..2,500 | 0..0 |
| `delayed_surge` | yes | 0 | 220,163..220,163 | 55,539..55,539 | 3,712..3,712 | 3,669..3,669 | 0..0 |
| `combined_stress` | yes | 0 | 225,000..225,000 | 575,701..575,701 | 37,496..37,496 | 3,409..3,409 | 0..0 |
| `m2_bias_shift` | yes | 0 | 300,000..300,000 | 111,440..111,440 | 8,398..8,398 | 5,000..5,000 | 0..0 |
| `m3_marginal_q90_stress` | yes | 0 | 300,000..300,000 | 813,258..813,258 | 56,612..56,612 | 5,000..5,000 | 0..0 |

## Shadow prices (horizon-wide constraints)

Derivative of the optimal penalised score with respect to one right-hand side, in USD-equivalent units (cash plus notional penalty), valid only locally. The duals of `normal`, `increased_costs`, `delayed_surge` (the scenarios listed under `shadow_prices`) are bracketed by one-sided finite differences of re-solved, certified programmes (step: the larger of 0.001 natural units and 1e-7 of the right-hand side): `locally_unique` when both slopes agree, `breakpoint` when they differ (the solver's value is then one subgradient among several); other duals show `not_computed`. The cash and penalty parts split the practical forward step. Daily rows are in `duals.csv`.

| scenario | constraint | binding | dual | units | backward | forward | status | cash part | penalty part |
|---|---|---|---|---|---|---|---|---|---|
| `normal` | overtime_total_limit | no | 0.000 | usd_equivalent_per_overtime_hour_allowed | 0.000 | 0.000 | locally_unique | 0.000 | 0.000 |
| `normal` | terminal_backlog | no | 0.000 | usd_equivalent_per_request | 0.000 | 0.000 | locally_unique | 0.000 | 0.000 |
| `normal` | budget | no | 0.000 | usd_equivalent_per_usd | 0.000 | 0.000 | locally_unique | 0.000 | 0.000 |
| `increased_costs` | overtime_total_limit | no | 0.000 | usd_equivalent_per_overtime_hour_allowed | 0.000 | 0.000 | locally_unique | 0.000 | 0.000 |
| `increased_costs` | terminal_backlog | no | 0.000 | usd_equivalent_per_request | 0.000 | 0.000 | locally_unique | 0.000 | 0.000 |
| `increased_costs` | budget | no | 0.000 | usd_equivalent_per_usd | 0.000 | 0.000 | locally_unique | 0.000 | 0.000 |
| `delayed_surge` | overtime_total_limit | no | 0.000 | usd_equivalent_per_overtime_hour_allowed | 0.000 | 0.000 | locally_unique | 0.000 | 0.000 |
| `delayed_surge` | terminal_backlog | no | 0.000 | usd_equivalent_per_request | 0.000 | 0.000 | locally_unique | 0.000 | 0.000 |
| `delayed_surge` | budget | no | 0.000 | usd_equivalent_per_usd | 0.000 | 0.000 | locally_unique | 0.000 | 0.000 |

- `normal`: 123 duals bracketed (all brackets hold); 123 locally unique, 0 at a breakpoint, 0 at a domain boundary or with an infeasible side.
- `increased_costs`: 123 duals bracketed (all brackets hold); 123 locally unique, 0 at a breakpoint, 0 at a domain boundary or with an infeasible side.
- `delayed_surge`: 123 duals bracketed (all brackets hold); 116 locally unique, 0 at a breakpoint, 7 at a domain boundary or with an infeasible side.

## Tradeoff frontier

Least notional backlog penalty achievable for each cap on discretionary cash (budget row dropped, daily limits and totals kept), with the least cash that achieves it, the request-days and the terminal backlog of that plan (`frontier.csv`). A HYPOTHETICAL TRADEOFF under HYPOTHETICAL prices and a point forecast; no point on it is singled out as a budget.

- `normal`: with the terminal target kept, extra cash beyond 435,458 USD lowers the penalty no further; without the target, extra cash beyond 435,458 USD lowers the penalty no further; under this deterministic demand path and the HYPOTHETICAL prices; the curve is non-increasing: yes, convex: yes; the strict plan lies on it: passed. DIAGNOSTIC tradeoff points, not budgets.
- `combined_stress`: with the terminal target kept, no plan meets the HYPOTHETICAL target at any cash cap; without the target, extra cash beyond 851,400 USD lowers the penalty no further; under this deterministic demand path and the HYPOTHETICAL prices; the curve is non-increasing: yes, convex: yes; the strict plan lies on it: not_applicable. DIAGNOSTIC tradeoff points, not budgets.

## Sensitivity

One HYPOTHETICAL parameter at a time on scenario `normal` (`sensitivity.csv` has every value). Properties proved for the model and checked on the results (`down`: non-increasing as the parameter rises; feasibility `lost`: once infeasible, stays infeasible):

| parameter | quantity | direction | holds | pairs compared |
|---|---|---|---|---|
| demand_multiplier | penalised_score | up | yes | 3 |
| demand_multiplier | feasibility | lost | yes | 6 |
| discretionary_budget_multiplier | penalised_score | down | yes | 5 |
| discretionary_budget_multiplier | feasibility | gained | yes | 8 |
| regular_hours_multiplier | penalised_score | down | yes | 3 |
| regular_hours_multiplier | feasibility | gained | yes | 6 |
| productivity_multiplier | penalised_score | down | yes | 3 |
| productivity_multiplier | feasibility | gained | yes | 6 |
| cost_multiplier | penalised_score | up | yes | 4 |
| cost_multiplier | feasibility | lost | yes | 5 |
| backlog_penalty | penalised_score | up | yes | 6 |
| backlog_penalty | backlog_request_days | down | yes | 6 |
| backlog_penalty | cash_plus_terminal_penalty | up | yes | 6 |
| backlog_penalty | feasibility | unchanged | yes | 6 |

Regular staff hours change capacity at a cost outside the score (the committed regular cost), so their effect on cash is reported, not asserted. The backlog penalty sweep changes only the daily weight `w`; cash alone is not asserted to rise with it when a terminal penalty is set.

Values of the least-cash optimal plan at each point (`unique`: whether the optimal plan is unique; otherwise `sensitivity.csv` has the ranges over all optimal plans). An infeasible point shows the least terminal-target violation within the budget. A swept capacity that lands within 1 % of the legacy demand percentile is marked `near percentile`: it is a swept HYPOTHETICAL capacity, not that percentile.

| parameter | value | strict model | regular capacity / mean demand of the point | discretionary cash (USD) | request-days | terminal backlog | least violation if infeasible | flags |
|---|---|---|---|---|---|---|---|---|
| demand_multiplier | 0.90 | optimal | 1.098 | 0 | 17,664 | 0 | n/a | - |
| demand_multiplier | 0.95 | optimal | 1.040 | 12,636 | 42,032 | 100 | n/a | - |
| demand_multiplier | 0.98 | optimal | 1.008 | 113,467 | 43,910 | 1,493 | n/a | - |
| demand_multiplier | 1.00 | optimal | 0.988 | 225,276 | 48,728 | 3,712 | n/a | - |
| demand_multiplier | 1.02 | infeasible | 0.969 | n/a | n/a | n/a | 3,397 | - |
| demand_multiplier | 1.05 | infeasible | 0.941 | n/a | n/a | n/a | 13,416 | - |
| demand_multiplier | 1.10 | infeasible | 0.898 | n/a | n/a | n/a | 30,115 | - |
| discretionary_budget_multiplier | 0.00 | infeasible | 0.988 | n/a | n/a | n/a | 7,968 | - |
| discretionary_budget_multiplier | 0.25 | infeasible | 0.988 | n/a | n/a | n/a | 5,155 | - |
| discretionary_budget_multiplier | 0.50 | infeasible | 0.988 | n/a | n/a | n/a | 2,343 | - |
| discretionary_budget_multiplier | 0.75 | optimal | 0.988 | 225,000 | 48,893 | 3,712 | n/a | not unique |
| discretionary_budget_multiplier | 1.00 | optimal | 0.988 | 225,276 | 48,728 | 3,712 | n/a | - |
| discretionary_budget_multiplier | 1.25 | optimal | 0.988 | 225,276 | 48,728 | 3,712 | n/a | - |
| discretionary_budget_multiplier | 1.50 | optimal | 0.988 | 225,276 | 48,728 | 3,712 | n/a | - |
| discretionary_budget_multiplier | 2.00 | optimal | 0.988 | 225,276 | 48,728 | 3,712 | n/a | - |
| discretionary_budget_multiplier | 3.00 | optimal | 0.988 | 225,276 | 48,728 | 3,712 | n/a | - |
| regular_hours_multiplier | 0.90 | infeasible | 0.889 | n/a | n/a | n/a | 29,718 | - |
| regular_hours_multiplier | 0.95 | infeasible | 0.939 | n/a | n/a | n/a | 13,218 | - |
| regular_hours_multiplier | 0.98 | infeasible | 0.968 | n/a | n/a | n/a | 3,318 | - |
| regular_hours_multiplier | 1.00 | optimal | 0.988 | 225,276 | 48,728 | 3,712 | n/a | - |
| regular_hours_multiplier | 1.02 | optimal | 1.008 | 112,697 | 44,692 | 1,541 | n/a | - |
| regular_hours_multiplier | 1.05 | optimal | 1.038 | 11,336 | 43,203 | 221 | n/a | near percentile |
| regular_hours_multiplier | 1.10 | optimal | 1.087 | 0 | 17,461 | 0 | n/a | - |
| productivity_multiplier | 0.90 | infeasible | 0.889 | n/a | n/a | n/a | 30,843 | - |
| productivity_multiplier | 0.95 | infeasible | 0.939 | n/a | n/a | n/a | 13,780 | - |
| productivity_multiplier | 0.98 | infeasible | 0.968 | n/a | n/a | n/a | 3,543 | - |
| productivity_multiplier | 1.00 | optimal | 0.988 | 225,276 | 48,728 | 3,712 | n/a | - |
| productivity_multiplier | 1.02 | optimal | 1.008 | 111,193 | 44,584 | 1,541 | n/a | - |
| productivity_multiplier | 1.05 | optimal | 1.038 | 10,797 | 43,203 | 221 | n/a | near percentile |
| productivity_multiplier | 1.10 | optimal | 1.087 | 0 | 17,461 | 0 | n/a | - |
| cost_multiplier | 0.80 | optimal | 0.988 | 199,421 | 38,828 | 2,812 | n/a | - |
| cost_multiplier | 0.90 | optimal | 0.988 | 202,748 | 48,728 | 3,712 | n/a | - |
| cost_multiplier | 1.00 | optimal | 0.988 | 225,276 | 48,728 | 3,712 | n/a | - |
| cost_multiplier | 1.10 | optimal | 0.988 | 247,803 | 48,728 | 3,712 | n/a | - |
| cost_multiplier | 1.20 | optimal | 0.988 | 268,996 | 49,396 | 3,712 | n/a | not unique |
| cost_multiplier | 1.50 | infeasible | 0.988 | n/a | n/a | n/a | 468 | - |
| backlog_penalty | 0.00 | optimal | 0.988 | 212,474 | 156,673 | 4,000 | n/a | not unique |
| backlog_penalty | 0.50 | optimal | 0.988 | 214,474 | 56,242 | 4,000 | n/a | - |
| backlog_penalty | 1.00 | optimal | 0.988 | 214,634 | 55,954 | 4,000 | n/a | - |
| backlog_penalty | 2.00 | optimal | 0.988 | 225,276 | 48,728 | 3,712 | n/a | - |
| backlog_penalty | 4.00 | optimal | 0.988 | 262,230 | 34,160 | 2,421 | n/a | - |
| backlog_penalty | 8.00 | optimal | 0.988 | 300,000 | 27,272 | 2,255 | n/a | not unique |
| backlog_penalty | 16.00 | optimal | 0.988 | 300,000 | 27,272 | 2,255 | n/a | not unique |

## Limitations

- Demand is an unvalidated point forecast (M2 future projection). Over horizons 1-30 that model's historical error was 985 requests/day on average (mean absolute error), and no plan here accounts for it.
- Every operational parameter is HYPOTHETICAL: ATLAS has no data on closures, backlog, staffing, productivity, costs or budgets of any NYC agency.
- The M3 intervals failed their calibration criteria; quantile stress paths are per-day marginal quantiles, not joint scenarios, and carry no probability.
- The model is deterministic: it plans as if demand were known. No chance constraint, service-level guarantee or probability of meeting a target is computed or implied.
- The penalised score mixes discretionary cash with notional penalty weights; shadow prices are in the same USD-equivalent units and are not wages, prices or values of staff.
- A pooled city-wide unit is the most favourable assumption about how work can be shared; any split by agency would leave as much backlog or more.

Nothing in this report is a validated staffing, budget or service-capacity recommendation for any NYC agency.
