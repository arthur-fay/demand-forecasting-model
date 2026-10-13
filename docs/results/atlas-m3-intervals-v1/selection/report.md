# M3 uncertainty report: atlas-m3-intervals-v1

**HISTORICAL PROBABILISTIC BACKTEST: prediction intervals and exceedance probabilities re-issued at past origins from the residuals observable at each origin, and scored against recorded actuals. Not prospective validation.**

Periods run: m3_selection.

Limitations:

- Historical re-issue, not prospective validation: every interval is re-issued at a past origin from one frozen snapshot (a single final data vintage) and scored against recorded actuals.
- The evaluation period is M2's confirmation period. Its point errors were examined in M2 (and the model was confirmed there), so it is not an untouched test; only the interval and probability results are new.
- Forecast paths from neighbouring origins overlap and errors follow month-long regimes: thousands of scored forecasts are worth only tens of independent observations (see the HAC effective sample sizes). Coverage intervals and p-values are indicative.
- Residual-based intervals assume that the errors of the calibration window describe the errors to come. Level shifts, growth and unusual periods (year-end) break this; split-conformal guarantees require exchangeability, which does not hold here.
- Historical thresholds are percentiles of incoming demand, and hypothetical thresholds are user-supplied numbers. Neither is a measured service capacity of any NYC agency.

## Protocol

- Point models: `atlas_linear_regression`, `same_weekday_median_5w` (primary `atlas_linear_regression`, the M2 recommendation).
- Central intervals 80 %, 95 %; quantile grid 0.025, 0.1, 0.25, 0.5, 0.75, 0.9, 0.975.
- Eligibility: residual.target_date <= calibration_origin - data_latency_days, with calibration_origin = issue origin (rolling) or the period's first origin (fixed); data latency 0 day(s) (M2 data delay 0).
- A horizon is calibrated from its own residuals if they come from at least 100 distinct origins, otherwise from its predeclared horizon bucket, otherwise not at all; an interval whose calibration residuals have a HAC effective sample size below 20 is labelled `thin`.
- Candidates: `empirical_additive@rolling365`, `empirical_relative@rolling365`, `conformal_absolute@rolling365`, `empirical_additive@fixed365`, `empirical_relative@fixed365`, `conformal_absolute@fixed365`.
- Baselines: `gaussian_one_step@rolling365`, `climatology_180d`.

| period | role | first origin | last origin | use |
|---|---|---|---|---|
| m3_selection | selection | 2024-08-01 | 2025-05-31 | Interval-method selection. Starts six months after the first residual origin so that every horizon has at least 100 eligible calibration origins at the first issue; scores only M2 development-period targets (through 2025-06-30). |
| m3_evaluation | evaluation | 2025-06-30 | 2026-05-31 | Historical evaluation of the sealed choice on the M2 confirmation origins (targets 2025-07-01..2026-06-30). Already exposed in M2: not an untouched prospective test. |

Thresholds:

- `trailing180_p75`: 75th percentile (linear interpolation) of the observed daily counts in the 180 days ending at each forecast's data cutoff: the legacy 'planning capacity' rule recomputed at every origin. A high-demand reference derived from incoming requests, not a measured service capacity. (DERIVED_FROM_HISTORY; Historical demand percentile: a high-demand reference computed from past incoming requests, not a measured service capacity.)
- `hypothetical_capacity_12000` = 12,000 requests_per_day, **HYPOTHETICAL**. Assumptions: Illustrative round number set above the legacy demand percentile of 11,609 requests per day. It is not derived from staffing, budgets, closures, backlog or service-level targets (none exist in the repository) and does not represent the capacity of any NYC agency; it only demonstrates how a user-supplied capacity threshold is handled. HYPOTHETICAL threshold: a user-supplied number, not a verified NYC agency service capacity.
- `hypothetical_capacity_13000` = 13,000 requests_per_day, **HYPOTHETICAL**. Assumptions: Second illustrative round number, 1,000 requests per day above the first, chosen without reference to any operational data; it does not represent the capacity of any NYC agency and exists to show that probabilities fall as the threshold rises. HYPOTHETICAL threshold: a user-supplied number, not a verified NYC agency service capacity.

## Interval-method selection (selection period only)

Among the candidate method/scheme pairs, those with an unavailable-forecast rate above max_unavailable_rate on the selection period are ineligible; the others are ranked by mean WIS over the selection horizons on the forecasts every eligible candidate issued, for the primary point model; ties go to the earlier candidate. The evaluation period never selects.

| rank | candidate | mean WIS | coverage 80 % | coverage 95 % | mean width 80 % | mean width 95 % |
|---|---|---|---|---|---|---|
| 1 | `empirical_relative@fixed365` | 457.9 | 72.7 % | 91.0 % | 2,138.0 | 3,940.8 |
| 2 | `empirical_relative@rolling365` | 460.2 | 74.8 % | 91.7 % | 2,459.4 | 4,678.4 |
| 3 | `empirical_additive@rolling365` | 462.8 | 73.5 % | 90.9 % | 2,412.4 | 4,674.2 |
| 4 | `empirical_additive@fixed365` | 464.5 | 68.9 % | 89.7 % | 1,982.2 | 3,770.1 |
| 5 | `conformal_absolute@fixed365` | 473.1 | 70.3 % | 89.6 % | 2,122.8 | 3,695.2 |
| 6 | `conformal_absolute@rolling365` | 475.9 | 75.6 % | 90.5 % | 2,394.9 | 4,810.4 |

9,120 common forecasts of `atlas_linear_regression`, horizons 1-30. **Selected: `empirical_relative@fixed365`** (lowest mean WIS on the m3_selection period (457.914)). These selection-period scores are optimistic for the winner.

## Coverage, width and scores by horizon bucket

Coverage with its 95 % HAC interval (one value per origin, overlap = the bucket's last horizon); widths and scores in requests per day. `eff. n` is the HAC effective sample size of the per-origin coverage series.

### m3_selection

| model | method | bucket | forecasts | coverage 80 % | coverage 95 % | width 80 % | width 95 % | Winkler 95 % | WIS | eff. n (95 %) |
|---|---|---|---|---|---|---|---|---|---|---|
| `atlas_linear_regression` | `empirical_relative@fixed365` | day_1 | 304 | 76.0 % [70.3, 81.6] | 92.4 % [89.2, 95.7] | 1,925.6 | 3,361.3 | 5,850.4 | 344.5 | 257.5 |
| `atlas_linear_regression` | `empirical_relative@fixed365` | days_2_7 | 1,824 | 72.8 % [63.5, 82.1] | 91.3 % [84.1, 98.5] | 2,118.0 | 3,928.1 | 8,095.2 | 450.4 | 31.1 |
| `atlas_linear_regression` | `empirical_relative@fixed365` | days_8_14 | 2,128 | 73.5 % [62.5, 84.5] | 91.1 % [81.9, 100.4] | 2,169.6 | 3,969.2 | 8,420.1 | 457.4 | 19.0 |
| `atlas_linear_regression` | `empirical_relative@fixed365` | days_15_30 | 4,864 | 72.0 % [59.8, 84.3] | 90.8 % [79.3, 102.3] | 2,144.9 | 3,969.4 | 8,723.1 | 468.1 | 11.8 |
| `atlas_linear_regression` | `empirical_relative@fixed365` | days_1_30 | 9,120 | 72.7 % [60.1, 85.2] | 91.0 % [80.1, 102.0] | 2,138.0 | 3,940.8 | 8,431.0 | 457.9 | 10.1 |
| `atlas_linear_regression` | `gaussian_one_step@rolling365` | day_1 | 304 | 84.5 % [79.6, 89.5] | 93.8 % [90.7, 96.8] | 2,513.8 | 3,844.5 | 6,051.6 | 352.8 | 237.9 |
| `atlas_linear_regression` | `gaussian_one_step@rolling365` | days_2_7 | 1,824 | 78.1 % [69.1, 87.1] | 90.5 % [82.6, 98.3] | 2,513.8 | 3,844.5 | 8,833.1 | 459.2 | 30.0 |
| `atlas_linear_regression` | `gaussian_one_step@rolling365` | days_8_14 | 2,128 | 77.8 % [66.3, 89.3] | 89.7 % [78.9, 100.5] | 2,513.8 | 3,844.5 | 9,453.8 | 471.6 | 18.1 |
| `atlas_linear_regression` | `gaussian_one_step@rolling365` | days_15_30 | 4,864 | 78.2 % [63.8, 92.7] | 88.9 % [75.2, 102.7] | 2,513.8 | 3,844.5 | 9,935.9 | 484.2 | 11.7 |
| `atlas_linear_regression` | `gaussian_one_step@rolling365` | days_1_30 | 9,120 | 78.3 % [64.3, 92.3] | 89.6 % [76.8, 102.3] | 2,513.8 | 3,844.5 | 9,473.4 | 471.9 | 10.2 |
| `same_weekday_median_5w` | `empirical_relative@fixed365` | day_1 | 304 | 68.1 % [61.3, 74.9] | 85.5 % [80.3, 90.7] | 2,279.3 | 3,842.1 | 9,380.0 | 517.8 | 178.3 |
| `same_weekday_median_5w` | `empirical_relative@fixed365` | days_2_7 | 1,824 | 67.9 % [57.4, 78.3] | 85.5 % [77.4, 93.6] | 2,285.1 | 3,851.5 | 9,363.8 | 517.7 | 31.1 |
| `same_weekday_median_5w` | `empirical_relative@fixed365` | days_8_14 | 2,128 | 74.1 % [61.6, 86.5] | 88.0 % [78.9, 97.0] | 2,519.5 | 4,205.2 | 9,905.0 | 534.4 | 18.4 |
| `same_weekday_median_5w` | `empirical_relative@fixed365` | days_15_30 | 4,864 | 67.7 % [49.1, 86.3] | 86.6 % [72.8, 100.5] | 2,441.1 | 4,206.4 | 9,445.9 | 539.9 | 9.2 |
| `same_weekday_median_5w` | `empirical_relative@fixed365` | days_1_30 | 9,120 | 69.2 % [51.4, 87.0] | 86.7 % [73.2, 100.2] | 2,422.8 | 4,123.0 | 9,534.4 | 533.4 | 8.6 |
| `same_weekday_median_5w` | `gaussian_one_step@rolling365` | day_1 | 304 | 78.0 % [71.6, 84.3] | 88.5 % [84.0, 93.0] | 3,149.8 | 4,817.2 | 8,712.7 | 509.9 | 192.6 |
| `same_weekday_median_5w` | `gaussian_one_step@rolling365` | days_2_7 | 1,824 | 77.7 % [67.6, 87.9] | 88.4 % [81.4, 95.5] | 3,149.8 | 4,817.2 | 8,857.1 | 512.2 | 30.7 |
| `same_weekday_median_5w` | `gaussian_one_step@rolling365` | days_8_14 | 2,128 | 78.2 % [65.9, 90.6] | 88.6 % [79.6, 97.5] | 3,149.8 | 4,817.2 | 10,191.2 | 542.4 | 18.7 |
| `same_weekday_median_5w` | `gaussian_one_step@rolling365` | days_15_30 | 4,864 | 76.9 % [60.2, 93.6] | 88.2 % [77.1, 99.4] | 3,149.8 | 4,817.2 | 9,811.7 | 541.3 | 10.2 |
| `same_weekday_median_5w` | `gaussian_one_step@rolling365` | days_1_30 | 9,120 | 77.4 % [60.5, 94.3] | 88.4 % [76.8, 99.9] | 3,149.8 | 4,817.2 | 9,672.7 | 534.7 | 8.2 |
| `model_free` | `climatology_180d` | day_1 | 304 | 77.3 % [71.5, 83.1] | 93.1 % [89.9, 96.3] | 3,186.9 | 5,587.1 | 8,359.7 | 508.4 | 247.9 |
| `model_free` | `climatology_180d` | days_2_7 | 1,824 | 77.1 % [68.2, 86.0] | 92.2 % [88.0, 96.4] | 3,186.9 | 5,587.1 | 8,601.6 | 514.7 | 46.0 |
| `model_free` | `climatology_180d` | days_8_14 | 2,128 | 76.5 % [64.2, 88.7] | 91.4 % [86.2, 96.7] | 3,186.9 | 5,587.1 | 8,796.7 | 522.9 | 30.1 |
| `model_free` | `climatology_180d` | days_15_30 | 4,864 | 75.2 % [59.0, 91.5] | 90.6 % [83.5, 97.7] | 3,186.9 | 5,587.1 | 9,265.6 | 541.3 | 12.5 |
| `model_free` | `climatology_180d` | days_1_30 | 9,120 | 76.0 % [60.4, 91.5] | 91.2 % [84.8, 97.6] | 3,186.9 | 5,587.1 | 8,993.2 | 530.6 | 10.5 |

## By horizon: `empirical_relative@fixed365`

### m3_selection

| model | h | coverage 80 % | coverage 95 % | width 80 % | width 95 % | WIS | MAE |
|---|---|---|---|---|---|---|---|
| `atlas_linear_regression` | 1 | 76.0 % | 92.4 % | 1,925.6 | 3,361.3 | 344.5 | 713.7 |
| `atlas_linear_regression` | 2 | 72.4 % | 91.4 % | 2,050.8 | 3,898.4 | 424.4 | 870.6 |
| `atlas_linear_regression` | 7 | 73.4 % | 90.8 % | 2,147.7 | 3,923.9 | 457.0 | 911.2 |
| `atlas_linear_regression` | 14 | 71.7 % | 91.1 % | 2,116.9 | 3,964.5 | 460.1 | 920.9 |
| `atlas_linear_regression` | 21 | 70.4 % | 90.8 % | 2,082.1 | 3,953.2 | 466.1 | 924.8 |
| `atlas_linear_regression` | 30 | 74.7 % | 91.1 % | 2,272.2 | 4,087.5 | 470.7 | 924.7 |
| `same_weekday_median_5w` | 1 | 68.1 % | 85.5 % | 2,279.3 | 3,842.1 | 517.8 | 1,023.7 |
| `same_weekday_median_5w` | 2 | 68.1 % | 85.5 % | 2,283.7 | 3,845.2 | 517.8 | 1,023.1 |
| `same_weekday_median_5w` | 7 | 67.8 % | 85.5 % | 2,285.6 | 3,856.3 | 517.5 | 1,020.5 |
| `same_weekday_median_5w` | 14 | 72.7 % | 88.2 % | 2,478.4 | 4,042.0 | 538.2 | 1,051.4 |
| `same_weekday_median_5w` | 21 | 70.4 % | 87.2 % | 2,419.7 | 4,072.7 | 523.3 | 1,029.9 |
| `same_weekday_median_5w` | 30 | 63.8 % | 85.5 % | 2,568.3 | 4,537.1 | 559.3 | 1,119.1 |

## Against the uncertainty baselines and across point models (days 1-30)

WIS skill = 1 - WIS / reference WIS on common forecasts (positive = better). The interval bounds the mean WIS difference (HAC, larger of the two standard errors).

| period | forecaster | reference | common | WIS | reference WIS | skill | difference 95 % CI | p | eff. n |
|---|---|---|---|---|---|---|---|---|---|
| m3_selection | `atlas_linear_regression` `empirical_relative@fixed365` | `atlas_linear_regression` `gaussian_one_step@rolling365` | 9,120 | 457.9 | 471.9 | 3.0 % | [-22.3, -5.6] | 0.001 | 14.4 |
| m3_selection | `atlas_linear_regression` `empirical_relative@fixed365` | `model_free` `climatology_180d` | 9,120 | 457.9 | 530.6 | 13.7 % | [-172.1, 26.8] | 0.152 | 14.1 |
| m3_selection | `same_weekday_median_5w` `empirical_relative@fixed365` | `same_weekday_median_5w` `gaussian_one_step@rolling365` | 9,120 | 533.4 | 534.7 | 0.2 % | [-54.3, 51.8] | 0.963 | 12.3 |
| m3_selection | `same_weekday_median_5w` `empirical_relative@fixed365` | `model_free` `climatology_180d` | 9,120 | 533.4 | 530.6 | -0.5 % | [-135.8, 141.5] | 0.968 | 13.8 |
| m3_selection | `atlas_linear_regression` `empirical_relative@fixed365` | `same_weekday_median_5w` `empirical_relative@fixed365` | 9,120 | 457.9 | 533.4 | 14.2 % | [-208.9, 57.8] | 0.266 | 22.6 |

## Every candidate and baseline, all horizons

Descriptive. Only the selection period chooses; evaluation-period numbers of candidates that were not selected decide nothing.

### m3_selection

| model | method | role | coverage 80 % | coverage 95 % | width 80 % | width 95 % | WIS | mean pinball | unavailable | thin |
|---|---|---|---|---|---|---|---|---|---|---|
| `atlas_linear_regression` | `empirical_additive@rolling365` | candidate | 73.5 % | 90.9 % | 2,412.4 | 4,674.2 | 462.8 | 275.8 | 0 | 0 |
| `atlas_linear_regression` | `empirical_relative@rolling365` | candidate | 74.8 % | 91.7 % | 2,459.4 | 4,678.4 | 460.2 | 274.3 | 0 | 0 |
| `atlas_linear_regression` | `conformal_absolute@rolling365` | candidate | 75.6 % | 90.5 % | 2,394.9 | 4,810.4 | 475.9 | 281.3 | 0 | 0 |
| `atlas_linear_regression` | `empirical_additive@fixed365` | candidate | 68.9 % | 89.7 % | 1,982.2 | 3,770.1 | 464.5 | 277.4 | 0 | 0 |
| `atlas_linear_regression` | `empirical_relative@fixed365` | candidate | 72.7 % | 91.0 % | 2,138.0 | 3,940.8 | 457.9 | 274.1 | 0 | 0 |
| `atlas_linear_regression` | `conformal_absolute@fixed365` | candidate | 70.3 % | 89.6 % | 2,122.8 | 3,695.2 | 473.1 | 281.5 | 0 | 0 |
| `atlas_linear_regression` | `gaussian_one_step@rolling365` | baseline | 78.3 % | 89.6 % | 2,513.8 | 3,844.5 | 471.9 | 278.9 | 0 | 0 |
| `same_weekday_median_5w` | `empirical_additive@rolling365` | candidate | 72.6 % | 87.8 % | 2,775.6 | 5,201.5 | 546.4 | 322.5 | 0 | 32 |
| `same_weekday_median_5w` | `empirical_relative@rolling365` | candidate | 73.9 % | 89.2 % | 2,844.9 | 5,019.0 | 528.3 | 314.8 | 0 | 32 |
| `same_weekday_median_5w` | `conformal_absolute@rolling365` | candidate | 74.0 % | 89.8 % | 2,804.7 | 5,300.5 | 537.1 | 318.5 | 0 | 32 |
| `same_weekday_median_5w` | `empirical_additive@fixed365` | candidate | 68.0 % | 84.4 % | 2,303.5 | 3,896.3 | 554.0 | 324.3 | 0 | 0 |
| `same_weekday_median_5w` | `empirical_relative@fixed365` | candidate | 69.2 % | 86.7 % | 2,422.8 | 4,123.0 | 533.4 | 316.0 | 0 | 0 |
| `same_weekday_median_5w` | `conformal_absolute@fixed365` | candidate | 71.1 % | 87.0 % | 2,414.7 | 4,310.0 | 541.8 | 319.8 | 0 | 0 |
| `same_weekday_median_5w` | `gaussian_one_step@rolling365` | baseline | 77.4 % | 88.4 % | 3,149.8 | 4,817.2 | 534.7 | 318.7 | 0 | 0 |
| `model_free` | `climatology_180d` | baseline | 76.0 % | 91.2 % | 3,186.9 | 5,587.1 | 530.6 | 315.4 | 0 | 0 |

## Exceedance probabilities

`P(demand > threshold)` from each method's predictive distribution. Brier skill is against the climatology reference on the same forecasts. Hypothetical thresholds are labelled; no threshold is a measured service capacity.

| period | model | method | threshold | scope | forecasts | events | mean p | observed freq. | Brier | skill | p | eff. n |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| m3_selection | `atlas_linear_regression` | `empirical_relative@fixed365` | `trailing180_p75` | days_1_30 | 9,120 | 2,346 | 0.273 | 0.257 | 0.1788 | 0.064 | 0.550 | 15.7 |
| m3_selection | `atlas_linear_regression` | `empirical_relative@fixed365` | `hypothetical_capacity_12000` (HYPOTHETICAL) | days_1_30 | 9,120 | 681 | 0.037 | 0.075 | 0.0681 | 0.070 | 0.081 | 14.0 |
| m3_selection | `atlas_linear_regression` | `empirical_relative@fixed365` | `hypothetical_capacity_13000` (HYPOTHETICAL) | days_1_30 | 9,120 | 487 | 0.018 | 0.053 | 0.0514 | 0.040 | 0.272 | 10.1 |
| m3_selection | `atlas_linear_regression` | `gaussian_one_step@rolling365` | `trailing180_p75` | days_1_30 | 9,120 | 2,346 | 0.317 | 0.257 | 0.1822 | 0.046 | 0.636 | 16.0 |
| m3_selection | `atlas_linear_regression` | `gaussian_one_step@rolling365` | `hypothetical_capacity_12000` (HYPOTHETICAL) | days_1_30 | 9,120 | 681 | 0.026 | 0.075 | 0.0693 | 0.054 | 0.308 | 13.1 |
| m3_selection | `atlas_linear_regression` | `gaussian_one_step@rolling365` | `hypothetical_capacity_13000` (HYPOTHETICAL) | days_1_30 | 9,120 | 487 | 0.002 | 0.053 | 0.0528 | 0.015 | 0.779 | 9.8 |
| m3_selection | `same_weekday_median_5w` | `empirical_relative@fixed365` | `trailing180_p75` | days_1_30 | 9,120 | 2,346 | 0.327 | 0.257 | 0.2072 | -0.084 | 0.666 | 14.6 |
| m3_selection | `same_weekday_median_5w` | `empirical_relative@fixed365` | `hypothetical_capacity_12000` (HYPOTHETICAL) | days_1_30 | 9,120 | 681 | 0.073 | 0.075 | 0.0763 | -0.042 | 0.817 | 19.5 |
| m3_selection | `same_weekday_median_5w` | `empirical_relative@fixed365` | `hypothetical_capacity_13000` (HYPOTHETICAL) | days_1_30 | 9,120 | 487 | 0.029 | 0.053 | 0.0529 | 0.013 | 0.843 | 22.7 |
| m3_selection | `same_weekday_median_5w` | `gaussian_one_step@rolling365` | `trailing180_p75` | days_1_30 | 9,120 | 2,346 | 0.315 | 0.257 | 0.2000 | -0.047 | 0.767 | 14.7 |
| m3_selection | `same_weekday_median_5w` | `gaussian_one_step@rolling365` | `hypothetical_capacity_12000` (HYPOTHETICAL) | days_1_30 | 9,120 | 681 | 0.064 | 0.075 | 0.0746 | -0.019 | 0.889 | 20.5 |
| m3_selection | `same_weekday_median_5w` | `gaussian_one_step@rolling365` | `hypothetical_capacity_13000` (HYPOTHETICAL) | days_1_30 | 9,120 | 487 | 0.019 | 0.053 | 0.0530 | 0.010 | 0.830 | 18.0 |
| m3_selection | `model_free` | `climatology_180d` | `trailing180_p75` | days_1_30 | 9,120 | 2,346 | 0.251 | 0.257 | 0.1911 | n/a | n/a | n/a |
| m3_selection | `model_free` | `climatology_180d` | `hypothetical_capacity_12000` (HYPOTHETICAL) | days_1_30 | 9,120 | 681 | 0.070 | 0.075 | 0.0732 | n/a | n/a | n/a |
| m3_selection | `model_free` | `climatology_180d` | `hypothetical_capacity_13000` (HYPOTHETICAL) | days_1_30 | 9,120 | 487 | 0.049 | 0.053 | 0.0536 | n/a | n/a | n/a |

Reliability, m3_selection, `atlas_linear_regression` `empirical_relative@fixed365`, `trailing180_p75`:

| probability bin | forecasts | mean p | observed freq. |
|---|---|---|---|
| 0.0-0.1 | 2,869 | 0.054 | 0.114 |
| 0.1-0.2 | 2,309 | 0.138 | 0.182 |
| 0.2-0.3 | 680 | 0.259 | 0.238 |
| 0.3-0.4 | 553 | 0.350 | 0.268 |
| 0.4-0.5 | 877 | 0.454 | 0.448 |
| 0.5-0.6 | 460 | 0.553 | 0.420 |
| 0.6-0.7 | 464 | 0.651 | 0.455 |
| 0.7-0.8 | 792 | 0.745 | 0.542 |
| 0.8-0.9 | 98 | 0.824 | 0.480 |
| 0.9-1.0 | 18 | 0.935 | 0.833 |

## Ordinary days, holidays and flagged days (days 1-30)

Holiday-related: federal holidays (actual and observed) and the days around them. Flagged: target days with a warning-level M1 data-quality flag. A day can be both. Descriptive: the strata leave gaps in the origin grid, so no test is computed, and the number of distinct target days is small.

| period | model | method | stratum | forecasts | target days | coverage 80 % | coverage 95 % | width 95 % | WIS | MAE | bias | Brier (historical) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| m3_selection | `atlas_linear_regression` | `empirical_relative@fixed365` | ordinary | 8,139 | 297 | 75.2 % | 92.9 % | 3,953.1 | 420.3 | 858.2 | 3.0 | 0.1758 |
| m3_selection | `atlas_linear_regression` | `empirical_relative@fixed365` | holiday_related | 846 | 30 | 59.3 % | 82.0 % | 3,834.0 | 589.3 | 1,132.4 | -22.8 | 0.1594 |
| m3_selection | `atlas_linear_regression` | `empirical_relative@fixed365` | flagged | 135 | 6 | 1.5 % | 34.8 % | 3,869.7 | 1,901.3 | 2,927.1 | -2,927.1 | 0.4815 |
| m3_selection | `atlas_linear_regression` | `gaussian_one_step@rolling365` | ordinary | 8,139 | 297 | 81.3 % | 91.6 % | 3,854.4 | 436.4 | 858.2 | 3.0 | 0.1799 |
| m3_selection | `atlas_linear_regression` | `gaussian_one_step@rolling365` | holiday_related | 846 | 30 | 61.1 % | 79.9 % | 3,774.3 | 582.1 | 1,132.4 | -22.8 | 0.1610 |
| m3_selection | `atlas_linear_regression` | `gaussian_one_step@rolling365` | flagged | 135 | 6 | 5.9 % | 28.9 % | 3,692.4 | 1,921.0 | 2,927.1 | -2,927.1 | 0.4578 |
| m3_selection | `same_weekday_median_5w` | `empirical_relative@fixed365` | ordinary | 8,139 | 297 | 72.6 % | 89.7 % | 4,105.3 | 472.2 | 956.6 | -154.3 | 0.1947 |
| m3_selection | `same_weekday_median_5w` | `empirical_relative@fixed365` | holiday_related | 846 | 30 | 47.4 % | 68.9 % | 4,327.2 | 882.2 | 1,593.6 | 625.1 | 0.2787 |
| m3_selection | `same_weekday_median_5w` | `empirical_relative@fixed365` | flagged | 135 | 6 | 0.0 % | 14.1 % | 3,911.9 | 2,036.7 | 3,361.1 | -3,361.1 | 0.5189 |
| m3_selection | `same_weekday_median_5w` | `gaussian_one_step@rolling365` | ordinary | 8,139 | 297 | 81.2 % | 90.7 % | 4,839.8 | 480.2 | 956.6 | -154.3 | 0.1888 |
| m3_selection | `same_weekday_median_5w` | `gaussian_one_step@rolling365` | holiday_related | 846 | 30 | 53.3 % | 75.4 % | 4,645.8 | 812.1 | 1,593.6 | 625.1 | 0.2563 |
| m3_selection | `same_weekday_median_5w` | `gaussian_one_step@rolling365` | flagged | 135 | 6 | 0.0 % | 28.1 % | 4,526.7 | 2,078.5 | 3,361.1 | -3,361.1 | 0.5223 |
| m3_selection | `model_free` | `climatology_180d` | ordinary | 8,139 | 297 | 78.0 % | 93.0 % | 5,621.6 | 491.1 | 951.6 | -184.9 | 0.1856 |
| m3_selection | `model_free` | `climatology_180d` | holiday_related | 846 | 30 | 64.9 % | 80.0 % | 5,320.2 | 689.2 | 1,387.5 | 125.1 | 0.1942 |
| m3_selection | `model_free` | `climatology_180d` | flagged | 135 | 6 | 23.7 % | 50.4 % | 5,175.8 | 1,918.7 | 3,032.2 | -3,032.2 | 0.5015 |

## Width against horizon

Widths are not forced to grow with the horizon. `decreasing steps` counts horizons where the mean width falls; the last column is the share of origins whose width never falls along the path.

| period | model | method | level | mean width h=1 | mean width h=max | decreasing steps | non-decreasing paths |
|---|---|---|---|---|---|---|---|
| m3_selection | `atlas_linear_regression` | `empirical_relative@fixed365` | 80 % | 1,925.6 | 2,272.2 | 11 | 0.0 % |
| m3_selection | `atlas_linear_regression` | `empirical_relative@fixed365` | 95 % | 3,361.3 | 4,087.5 | 10 | 0.0 % |
| m3_selection | `atlas_linear_regression` | `gaussian_one_step@rolling365` | 80 % | 2,513.8 | 2,513.8 | 0 | 100.0 % |
| m3_selection | `atlas_linear_regression` | `gaussian_one_step@rolling365` | 95 % | 3,844.5 | 3,844.5 | 0 | 100.0 % |
| m3_selection | `same_weekday_median_5w` | `empirical_relative@fixed365` | 80 % | 2,279.3 | 2,568.3 | 8 | 0.0 % |
| m3_selection | `same_weekday_median_5w` | `empirical_relative@fixed365` | 95 % | 3,842.1 | 4,537.1 | 2 | 0.0 % |
| m3_selection | `same_weekday_median_5w` | `gaussian_one_step@rolling365` | 80 % | 3,149.8 | 3,149.8 | 0 | 100.0 % |
| m3_selection | `same_weekday_median_5w` | `gaussian_one_step@rolling365` | 95 % | 4,817.2 | 4,817.2 | 0 | 100.0 % |
| m3_selection | `model_free` | `climatology_180d` | 80 % | 3,186.9 | 3,186.9 | 0 | 100.0 % |
| m3_selection | `model_free` | `climatology_180d` | 95 % | 5,587.1 | 5,587.1 | 0 | 100.0 % |

## Sensitivity: longer data latency (descriptive)

| latency (days) | period | model | coverage 80 % | coverage 95 % | WIS |
|---|---|---|---|---|---|
| 7 | m3_selection | `atlas_linear_regression` | 73.9 % | 91.2 % | 456.1 |
| 7 | m3_selection | `same_weekday_median_5w` | 69.4 % | 86.9 % | 532.8 |

## Calibration support

How each interval was calibrated. `thin`: issued, but its calibration residuals are worth fewer independent observations than the configured minimum (exploratory).

| period | model | method | scheduled | horizon-specific | bucket-pooled | unavailable | thin | residuals per set | median eff. n |
|---|---|---|---|---|---|---|---|---|---|
| m3_selection | `atlas_linear_regression` | `empirical_relative@fixed365` | 9,120 | 9,120 | 0 | 0 | 0 | 153-182 | 50.2 |
| m3_selection | `atlas_linear_regression` | `gaussian_one_step@rolling365` | 9,120 | 9,120 | 0 | 0 | 0 | 182-365 | 283.8 |
| m3_selection | `same_weekday_median_5w` | `empirical_relative@fixed365` | 9,120 | 9,120 | 0 | 0 | 0 | 153-182 | 36.7 |
| m3_selection | `same_weekday_median_5w` | `gaussian_one_step@rolling365` | 9,120 | 9,120 | 0 | 0 | 0 | 182-365 | 184.9 |
