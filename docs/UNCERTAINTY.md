# Prediction intervals and probabilistic risk (M3)

M3 adds uncertainty to the M2 forecasts. It answers five questions:

1. What range of daily demand is plausible on each of the next 30 days?
2. How often have such ranges contained the observed outcome in the past?
3. How does the uncertainty change from horizon 1 to horizon 30?
4. What is the probability that demand on a target day exceeds a given threshold?
5. When is the history too short to support a reliable probability or coverage claim?

Sections 1-8 are the protocol. They were written and committed, together with the
configuration `configs/uncertainty/atlas-m3-intervals-v1.json` and the code, **before any
M3 run on the protocol's periods**. The results (section 9) were added afterwards.

```bash
uv run atlas uncertainty run --require-clean --output-dir runs/m3-selection        # selection period only
uv run atlas uncertainty run --require-clean \
    --period m3_selection --period m3_evaluation \
    --sealed-selection runs/m3-selection/selection.json --output-dir runs/m3-full   # sealed evaluation
uv run atlas uncertainty verify runs/m3-full       # re-run; every file must be identical
uv run atlas uncertainty inspect runs/m3-full      # integrity and headline, no recomputation
uv run atlas uncertainty project --status runs/m3-full/calibration_status.json     # future projection
```

## 1. Record types

| Record type | What it is |
|---|---|
| `historical_probabilistic_backtest` | Intervals and exceedance probabilities re-issued at past origins from the residuals observable at each origin, scored against recorded actuals. Not prospective validation. |
| `future_projection_intervals` | The selected intervals and probabilities attached to the M2 future projection from the last observed day. No outcome observed; labelled with the evaluation status. |

## 2. Residuals: the calibration data

The residuals are the out-of-sample errors of the forecasts M2 actually evaluates. M3 does
not re-implement any model: it re-runs the M2 engine (`atlas.backtest.run_backtest`, the
same `forecast_at_origin` path, the same model specifications built from the pinned M2
configuration) at **every** origin from 2024-02-01 to 2026-06-29, refitting at each origin
on data through that origin only, for two point models:

- `atlas_linear_regression`, the model recommended by the complete M2 run
  (`docs/results/atlas-m2-rolling-origin-v1/full/recommendation.json`; the run refuses a
  primary model that is not the M2 recommendation, or a recommendation whose run is not
  complete or whose file was changed);
- `same_weekday_median_5w`, the strong baseline B\* that M2 named as fallback.

A residual is `r = actual - prediction` for one model, origin and horizon whose actual was
observed (record status `ok`), so `y = prediction + r`. (M2's error metrics use the
opposite sign, `prediction - actual`.) A test checks that the re-run reproduces the
committed M2 spot check: exactly for the baseline, and to 1e-9 relative for the regression,
whose least squares run through OpenBLAS kernels that differ across CPUs (see
[DEVELOPMENT.md](DEVELOPMENT.md), "Reproducibility across machines").

## 3. Eligibility and leakage barriers

An interval issued at origin `o` may use a residual only if

    residual.target_date <= c - data_latency,  with calibration origin c <= o.

`c = o` for a rolling scheme; `c` = the first origin of the period for a fixed scheme. The
latency is 0 days, as in M2, where the origin is the last observed day (a target dated `o`
is observed at `o`); the configuration refuses a latency shorter than M2's data delay. A
residual's prediction was made at its own origin, before its target date, so the whole
residual is known at the cutoff.

| Barrier | Mechanism | Test evidence |
|---|---|---|
| One access path | `ResidualStore.calibration_set` is the only way to reach residuals; it slices by target date up to the cutoff | brute-force filter equality (property test over issue origins, calibration origins, latencies, windows, horizons) |
| Cutoff guard | a set whose latest target is after the cutoff raises `LeakageError`; so does a calibration origin after the issue origin | planted leaky slice is caught |
| Point forecasts and thresholds | point predictions come from the M2 records of the issue origin; thresholds and climatology read `series.known_through(data cutoff)` only | historical threshold unchanged when later values change |
| Actuals attached last | `attach_outcomes` runs after every interval and probability exists | frames carry no actual before it |
| Time travel | two series identical through day `d`: every interval and probability issued at origins `<= d` is identical (all columns); later ones differ (positive control) | `test_future_perturbation_leaves_earlier_issues_identical` |
| Latency | recorded actuals changed for targets after `T`: issues up to `T + L` are unchanged with latency `L`, and change with latency 0 | `test_latency_excludes_residuals_observed_too_late` |
| Order of decisions | M2 model selection (development), M3 method selection (M3 selection period), M3 evaluation (later targets); a run beyond the selection period must re-run it and reproduce the seal | seal tests; perturbing everything after the selection period leaves the selection unchanged |

## 4. Dependence

Forecast paths from neighbouring origins overlap, and errors follow regimes lasting weeks.
M3 handles this in three places:

- **Calibration sample size** is counted in distinct forecast origins (paths), not
  residuals. Horizon `h` is calibrated from its own residuals when they come from at least
  **100** distinct origins; otherwise from all residuals of its predeclared M2 horizon
  bucket (day 1, days 2-7, 8-14, 15-30), where the horizons of one path still count once;
  otherwise no interval is issued (`unavailable`). 100 origins put at least two residuals
  beyond each end of a 95 % interval and exceed the split-conformal minimum (19 for the
  symmetric central 95 % interval).\*
- **Calibration support.** Each set carries the HAC effective sample size of its residual
  series (Bartlett, bandwidth `2h`; for a pooled bucket, per-origin means and twice the
  bucket's last horizon).\* Below **20** the
  interval is still issued but labelled `thin` (exploratory).
- **Evaluation.** Coverage, score differences and Brier differences are tested with the M2
  machinery: one value per origin (the mean over the scope's horizons, complete origins
  only), the HLN Diebold-Mariano statistic with rectangular kernel to `h - 1`, a Bartlett
  `2h` sensitivity and the larger (conservative) p-value; intervals use the larger standard
  error. For coverage the tested series is `coverage - level`. The HAC effective sample
  size is reported with every coverage estimate. No test is computed on a gappy origin grid
  or for fewer than `max(3h, 50)` origins. A wider-bandwidth sensitivity is reported for the
  headline coverage.

## 5. Methods

Each method turns a point prediction and a calibration set into an explicit predictive
distribution; quantiles, intervals and probabilities all come from it.

**Candidates** (three methods, each with two calibration schemes, six pairs):

| method | predictive distribution | quantile rule |
|---|---|---|
| `empirical_additive` | `prediction + r_i` | linear interpolation (type 7) |
| `empirical_relative` | `prediction * actual_i / prediction_i` (errors proportional to the level) | linear interpolation |
| `conformal_absolute` | `prediction +/- |r_i|` (symmetric) | split-conformal rank: central `1 - a` interval `prediction +/- |r|_(k)`, `k = ceil((n + 1)(1 - a))`; unavailable if `k > n` |

| scheme | calibration |
|---|---|
| `rolling365` | re-calibrated at every issue origin from the residuals with targets in the 365 days up to the cutoff (an online method, updated only with outcomes observed before each issue date) |
| `fixed365` | calibrated once at the first origin of each period with the same 365-day window, then frozen |

Results of the two schemes are always reported separately.

**Uncertainty baselines** (never selected):

- `gaussian_one_step@rolling365`: Normal(prediction, s²) with `s` the RMS of the eligible
  horizon-1 residuals, at every horizon. What one-step errors alone suggest; M2 showed that
  recursion makes errors grow with the horizon, so this baseline is expected to under-cover
  at long horizons.
- `climatology_180d`: the empirical distribution of the last 180 observed daily counts at
  the data cutoff, ignoring the forecast. The reference for Brier skill.

**Validity.** Quantiles are clipped at 0 (demand is non-negative; this never lowers
coverage) and are non-decreasing in their level by construction; every forecast is checked,
so intervals never cross. Widths are **not** forced to grow with the horizon: each horizon is
calibrated on its own errors, and the report shows how often mean width falls between
horizons. Central intervals: 80 % and 95 %; quantile grid 0.025, 0.1, 0.25, 0.5, 0.75, 0.9,
0.975.

The split-conformal finite-sample guarantee assumes exchangeable residuals. Errors from
overlapping paths, regime changes and growth are not exchangeable, so no coverage is
guaranteed by any method here; it is measured.

## 6. Exceedance probabilities and thresholds

`P(demand on target > T | data at origin)` is read from the predictive distribution, never
inferred from interval endpoints. For a sample distribution of `m` values,
`P = (#{z_i > T} + 1/2) / (m + 1)`, the midpoint of the conformal predictive bounds:
strictly between 0 and 1 and non-increasing in `T`. Gaussian: `1 - Phi((T - mean) / s)`.
Every forecast is checked: probabilities lie in [0, 1] and never increase with the
threshold.

| threshold | provenance | definition |
|---|---|---|
| `trailing180_p75` | `DERIVED_FROM_HISTORY` | 75th percentile (linear interpolation) of the observed daily counts in the 180 days ending at each forecast's data cutoff, recomputed at every origin. It is the legacy "planning capacity" rule (it reproduces 11,609 at 2026-06-30): a high-demand reference, **not** a measured capacity |
| `hypothetical_capacity_12000` | `HYPOTHETICAL` | 12,000 requests per day, an illustrative round number above the legacy percentile |
| `hypothetical_capacity_13000` | `HYPOTHETICAL` | 13,000 requests per day, to show that probabilities fall as the threshold rises |

A hypothetical threshold must carry `"provenance": "HYPOTHETICAL"`, its units
(`requests_per_day`) and its assumptions, or the configuration is rejected. Every report
prints the label next to its results. Nothing in the repository measures the capacity of any
NYC agency.

## 7. Periods, selection and evaluation

| period | origins | targets | role |
|---|---|---|---|
| residual span | 2024-02-01..2026-06-29 | to 2026-06-30 | residuals only |
| `m3_selection` | 2024-08-01..2025-05-31 (304) | 2024-08-02..2025-06-30 | choose the interval method |
| `m3_evaluation` | 2025-06-30..2026-05-31 (336) | 2025-07-01..2026-06-30 | evaluate the sealed choice |

- `m3_selection` starts six months after the first residual origin, so every horizon has at
  least 100 eligible origins at its first issue. It scores only targets of M2's
  development period, where M2 selected the point model. The configuration refuses an M3
  selection period that reaches beyond M2's development targets.
- `m3_evaluation` is M2's confirmation period. **It was already used in M2** (its point
  errors were examined and the point model was confirmed there), so this is a historical
  evaluation of the intervals, not an untouched prospective test.
- **Selection rule** (selection period only): candidates with any unavailable or failed
  forecast are ineligible; the others are ranked by mean weighted interval score (WIS, a
  proper score of the median and the 80 % and 95 % intervals) over horizons 1-30 on the
  forecasts every eligible candidate issued, for the primary model; ties go to the earlier
  candidate. The selection is sealed (`selection.json`). The same method is then applied to
  B\*.
- The evaluation run must include the selection period again and reproduce the seal in
  every decision field, or it is incomplete and writes no `calibration_status.json`.
  Every candidate is also scored on the evaluation period, as a description that decides
  nothing.

## 8. Evaluation and decision criteria

Reported per period, point model, method and scope (each horizon, each bucket, days 1-30):
empirical coverage with its HAC interval and test against the nominal level, mean and
median width (also relative to the prediction), interval (Winkler) score, pinball loss per
quantile and on average, WIS, quantile calibration (share of outcomes at or below each quantile),
sample counts (scheduled, issued, unavailable, thin, bucket-pooled, evaluated, origins,
target days) and the effective sample size. Exceedance: Brier score, Brier skill against
climatology on the same forecasts with a HAC test, mean probability against observed
frequency, and a ten-bin reliability table. Paired WIS comparisons against both baselines
and between the two point models. Ordinary, holiday-related and flagged target days
(descriptive). Width against horizon.

**Decision-grade status** (pre-registered; applied to the primary model and the sealed
method on the evaluation period):

| condition | rule |
|---|---|
| coverage within tolerance | `|coverage - level| <= 5 pp` (80 %) and `<= 3 pp` (95 %), over days 1-30 **and** in every horizon bucket |
| coverage not rejected | HAC test of coverage = level not rejected at 5 % (conservative p) over days 1-30 |
| enough evidence | the test is computed and the HAC effective sample size is at least 30 |

Failing either of the first two is `failed_calibration`; passing both without enough
evidence (or with forecasts that were not issued, or issued on `thin` calibration\*) is
`insufficient_evidence`. Only when all hold is the status `decision_grade`. Tolerances are
inclusive.\* A run that does not reproduce its seal gets no status at all.\* For each threshold, exceedance probabilities are
`decision_grade` only if their Brier score beats climatology with conservative p < 0.05,
the mean probability is within 0.05 of the observed frequency, and the effective sample size
is at least 30 (forecasts without a predictive distribution also leave the evidence
incomplete\*). A status that is not decision-grade is disclosed and kept: no result is
adjusted after the fact to pass.

\* Amended after the independent review (section 10): wording corrected to match the code,
or a rule tightened that did not bind in the reference runs. No reference output changed.

**Pre-registered sensitivity analyses** (descriptive): a data latency of 7 days; ordinary,
holiday-related and flagged target days; wider HAC bandwidths for the headline coverage
test.

## 9. Results

Reference runs (both with `--require-clean`):

| run | code commit | periods | result fingerprint |
|---|---|---|---|
| [selection](results/atlas-m3-intervals-v1/selection/) | `cfdf634` (freeze) | `m3_selection` | `2e67348990ba27eb811f73eddbad5d27d746a21644a2cc6c788b9296e3437194` |
| [full](results/atlas-m3-intervals-v1/full/) | `ff44f54` (seal; same code fingerprint) | `m3_selection`, `m3_evaluation` | `c4d2adbf128e9f5e136c3e19d6aaba281257f6721f8a2d1804ac22eb27f8dfaf` |
| [projection](results/atlas-m3-intervals-v1/projection/) | `ff44f54` | from 2026-06-30 | - |

Protocol configuration fingerprint `5afd32068ebc63b5838cfea545326916f0c4dd835ed294a301acd89d8622244b`. Every interval scheduled in both periods
was issued (no `unavailable`, no failed point forecast, no missing actual). Tables are
generated from the committed `metrics.json` and checked by `tests/test_m3_results.py`; the
full report is [`report.md`](results/atlas-m3-intervals-v1/full/report.md).

### 9.1 Selection (selection period only)

| rank | candidate | mean WIS | coverage 80 % | coverage 95 % | width 80 % | width 95 % |
|---|---|---|---|---|---|---|
| 1 | `empirical_relative@fixed365` | 457.9 | 72.7 % | 91.0 % | 2,138 | 3,941 |
| 2 | `empirical_relative@rolling365` | 460.2 | 74.8 % | 91.7 % | 2,459 | 4,678 |
| 3 | `empirical_additive@rolling365` | 462.8 | 73.5 % | 90.9 % | 2,412 | 4,674 |
| 4 | `empirical_additive@fixed365` | 464.5 | 68.9 % | 89.7 % | 1,982 | 3,770 |
| 5 | `conformal_absolute@fixed365` | 473.1 | 70.3 % | 89.6 % | 2,123 | 3,695 |
| 6 | `conformal_absolute@rolling365` | 475.9 | 75.6 % | 90.5 % | 2,395 | 4,810 |

All six candidates under-covered on the selection period (80 % intervals contained 68.9 to
75.6 % of outcomes, 95 % intervals 89.6 to 91.7 %), and their mean WIS differ by 4 % at
most. The rule selected **`empirical_relative@fixed365`**: empirical quantiles of relative
errors, calibrated once per period on the residuals of the preceding 365 days and then
frozen. It was sealed in commit `ff44f54` before any evaluation-period computation, and the
evaluation run reproduced it in every decision field.

### 9.2 Decision status: FAILED_CALIBRATION

| level | coverage, days 1-30 | 95 % HAC interval | buckets outside tolerance | p (coverage = level) | eff. n |
|---|---|---|---|---|---|
| 80 % | 79.9 % | 65.3 to 94.5 % | none | 0.989 | 8.1 |
| 95 % | 92.1 % | 84.3 to 99.8 % | days_8_14, days_15_30 | 0.458 | 9.0 |

**The selected intervals are not decision-grade.** On the evaluation period:

- the 95 % intervals cover 91.8 % and 91.7 % of outcomes at horizons 8-14 and 15-30, more
  than 3 percentage points below 95 % (the pre-registered tolerance), so the status is
  `failed_calibration`;
- independently of that, the evidence is too thin for any coverage claim: the 336
  overlapping 30-day paths are worth about 8 to 9 independent observations (HAC effective
  sample size, below the required 30), and the 95 % HAC intervals of the coverage span
  about 29 points at 80 % and 16 points at 95 %. A normal-approximation interval can
  extend past 100 %; that only shows how little the data constrain coverage;
- the HAC tests do not reject nominal coverage (p 0.99 and 0.46), but with this little
  information that is not evidence of calibration.

The status of B\*'s intervals with the same method is `insufficient_evidence` (coverage
within tolerance everywhere, effective sample sizes 7 to 9); it is descriptive and does not
change the primary status. Nothing was re-tuned after these results.

### 9.3 Coverage, width and scores by horizon

Evaluation period, ATLAS regression, `empirical_relative@fixed365`:

| horizons | forecasts | coverage 80 % | coverage 95 % | width 80 % | width 95 % | Winkler 95 % | WIS | eff. n (95 %) |
|---|---|---|---|---|---|---|---|---|
| day 1 | 336 | 83.9 % | 94.9 % | 2,269 | 4,669 | 6,752 | 359.7 | 242.0 |
| days 2-7 | 2,016 | 81.2 % | 93.0 % | 2,820 | 5,688 | 9,137 | 491.8 | 47.1 |
| days 8-14 | 2,352 | 80.6 % | 91.8 % | 2,812 | 5,661 | 9,874 | 527.0 | 31.7 |
| days 15-30 | 5,376 | 78.9 % | 91.7 % | 2,790 | 5,688 | 9,858 | 541.0 | 12.6 |
| days 1-30 | 10,080 | 79.9 % | 92.1 % | 2,784 | 5,648 | 9,614 | 521.8 | 9.0 |

Selection period (optimistic for the winner, which was chosen there):

| horizons | forecasts | coverage 80 % | coverage 95 % | width 80 % | width 95 % | Winkler 95 % | WIS | eff. n (95 %) |
|---|---|---|---|---|---|---|---|---|
| day 1 | 304 | 76.0 % | 92.4 % | 1,926 | 3,361 | 5,850 | 344.5 | 257.5 |
| days 2-7 | 1,824 | 72.8 % | 91.3 % | 2,118 | 3,928 | 8,095 | 450.4 | 31.1 |
| days 8-14 | 2,128 | 73.5 % | 91.1 % | 2,170 | 3,969 | 8,420 | 457.4 | 19.0 |
| days 15-30 | 4,864 | 72.0 % | 90.8 % | 2,145 | 3,969 | 8,723 | 468.1 | 11.8 |
| days 1-30 | 9,120 | 72.7 % | 91.0 % | 2,138 | 3,941 | 8,431 | 457.9 | 10.1 |

- **Across horizons** (question 3), the 95 % interval widens from 4,669 requests per day at
  horizon 1 to 5,389-5,806 from horizon 2 on, and coverage falls from 94.9 % at day 1
  to 91.7 % at days 15-30. The width is not forced to grow: the mean width falls at 14 of
  the 29 steps between horizons at 95 % (18 at 80 %), and no single origin's path widens
  monotonically, because each horizon has its own calibration sample.
- **Asymmetry.** Outcomes fell below the predictive median on 38.6 % of evaluation
  forecasts, above the 97.5 % quantile on 3.7 % and below the 2.5 % quantile on 4.2 %: the
  intervals were centred too low (the confirmation year grew faster than its history; M2,
  section 6.4) and both tails were too light.
- **Width.** A 95 % interval is on average 54 % of the point forecast wide and asymmetric:
  its median bounds are 16 % below and 28 % (day 1) to 38 % (from day 7) above the point
  forecast; for the 80 % interval, 12 % below and 15 % above.

### 9.4 Against the uncertainty baselines and B\*

Evaluation period, days 1-30:

| point model | method | coverage 80 % | coverage 95 % | width 95 % | WIS |
|---|---|---|---|---|---|
| ATLAS regression | `empirical_relative@fixed365` | 79.9 % | 92.1 % | 5,648 | 521.8 |
| ATLAS regression | `gaussian_one_step@rolling365` | 77.0 % | 86.7 % | 3,858 | 554.6 |
| same-weekday median 5w (B*) | `empirical_relative@fixed365` | 79.3 % | 94.7 % | 6,479 | 570.0 |
| same-weekday median 5w (B*) | `gaussian_one_step@rolling365` | 81.3 % | 90.7 % | 5,574 | 599.7 |
| none (model-free) | `climatology_180d` | 72.5 % | 87.5 % | 5,406 | 614.6 |

| period | point model | reference | WIS | reference WIS | skill | WIS difference, 95 % CI | p | eff. n |
|---|---|---|---|---|---|---|---|---|
| selection | ATLAS regression | ATLAS regression `gaussian_one_step@rolling365` | 457.9 | 471.9 | 3.0 % | -22.3 to -5.6 | 0.001 | 14.4 |
| selection | ATLAS regression | none (model-free) `climatology_180d` | 457.9 | 530.6 | 13.7 % | -172.1 to 26.8 | 0.152 | 14.1 |
| selection | same-weekday median 5w (B*) | same-weekday median 5w (B*) `gaussian_one_step@rolling365` | 533.4 | 534.7 | 0.2 % | -54.3 to 51.8 | 0.963 | 12.3 |
| selection | same-weekday median 5w (B*) | none (model-free) `climatology_180d` | 533.4 | 530.6 | -0.5 % | -135.8 to 141.5 | 0.968 | 13.8 |
| selection | ATLAS regression | same-weekday median 5w (B*) `empirical_relative@fixed365` | 457.9 | 533.4 | 14.2 % | -208.9 to 57.8 | 0.266 | 22.6 |
| evaluation | ATLAS regression | ATLAS regression `gaussian_one_step@rolling365` | 521.8 | 554.6 | 5.9 % | -107.6 to 42.1 | 0.390 | 10.9 |
| evaluation | ATLAS regression | none (model-free) `climatology_180d` | 521.8 | 614.6 | 15.1 % | -161.6 to -24.0 | 0.008 | 12.3 |
| evaluation | same-weekday median 5w (B*) | same-weekday median 5w (B*) `gaussian_one_step@rolling365` | 570.0 | 599.7 | 5.0 % | -64.2 to 4.7 | 0.090 | 17.0 |
| evaluation | same-weekday median 5w (B*) | none (model-free) `climatology_180d` | 570.0 | 614.6 | 7.3 % | -107.2 to 17.9 | 0.161 | 15.9 |
| evaluation | ATLAS regression | same-weekday median 5w (B*) `empirical_relative@fixed365` | 521.8 | 570.0 | 8.5 % | -73.4 to -22.9 | <0.001 | 54.1 |

- The Gaussian one-step baseline, whose width does not grow with the horizon, covers only
  86.7 % at 95 % on the evaluation period (85.7 % at days 15-30). The selected method's WIS
  is 5.9 % lower, a difference that is not significant (p 0.39).
- Against climatology the selected method is 15.1 % better in WIS (p 0.008, effective
  sample size 12): the forecast carries information about the coming month that the recent
  distribution of demand does not.
- With the same method, the ATLAS regression's intervals beat B\*'s by 8.5 % in WIS
  (p < 0.001, effective sample size 54), consistent with M2's point accuracy ranking.
- Every candidate, scored on the evaluation period as a description (the selection was
  sealed before):

| method | coverage 80 % | coverage 95 % | width 95 % | WIS |
|---|---|---|---|---|
| `empirical_additive@rolling365` | 77.5 % | 91.1 % | 5,546 | 547.9 |
| `empirical_relative@rolling365` | 78.4 % | 90.9 % | 5,671 | 547.7 |
| `conformal_absolute@rolling365` | 77.2 % | 93.4 % | 5,738 | 539.3 |
| `empirical_additive@fixed365` | 78.9 % | 92.1 % | 5,361 | 520.1 |
| `empirical_relative@fixed365` | 79.9 % | 92.1 % | 5,648 | 521.8 |
| `conformal_absolute@fixed365` | 78.6 % | 94.7 % | 6,001 | 526.7 |
| `gaussian_one_step@rolling365` | 77.0 % | 86.7 % | 3,858 | 554.6 |

  The two fixed-calibration schemes did better than the rolling (online) ones on this
  period; the selected pair is within 0.4 % of the best evaluation-period WIS. Re-calibrating
  every day on the latest 365 days did not adapt fast enough to help.

### 9.5 Exceedance probabilities (question 4)

Evaluation period, ATLAS regression, `empirical_relative@fixed365`, days 1-30:

| threshold | mean threshold | events | mean p | observed freq. | Brier | Brier climatology | skill | p | eff. n | status |
|---|---|---|---|---|---|---|---|---|---|---|
| `trailing180_p75` | 10,815 | 3,894 | 0.333 | 0.386 | 0.2004 | 0.2554 | 0.215 | 0.038 | 11.0 | failed_calibration |
| `hypothetical_capacity_12000` (HYPOTHETICAL) | 12,000 | 1,337 | 0.120 | 0.133 | 0.1107 | 0.1217 | 0.090 | 0.144 | 12.8 | insufficient_evidence |
| `hypothetical_capacity_13000` (HYPOTHETICAL) | 13,000 | 630 | 0.070 | 0.062 | 0.0582 | 0.0617 | 0.057 | 0.132 | 16.2 | insufficient_evidence |

- **Historical threshold** (75th percentile of the last 180 days, recomputed at each
  origin): demand exceeded it on 38.6 % of the forecast days, not 25 %, because demand grew
  through the year. The predicted probabilities averaged 0.333: the right direction, but
  too low. The Brier score is 21.5 % better than climatology's constant ≈ 0.25
  (conservative p 0.038), yet the mean-probability gap (0.054) is just outside the
  pre-registered 0.05 and the effective sample size is 11, so the status is
  `failed_calibration`.
- **Hypothetical thresholds** (HYPOTHETICAL, 12,000 and 13,000 requests per day; not NYC
  agency capacities): probabilities are close to the observed frequencies, with Brier skill
  of 9 % and 6 % that is not significant (p 0.14 and 0.13): `insufficient_evidence`.
- Probabilities fall as the threshold rises in every one of the forecasts (checked at issue
  time and in the tests).

Reliability of the historical-threshold probabilities (evaluation period):

| probability bin | forecasts | mean p | observed freq. |
|---|---|---|---|
| 0.0-0.1 | 1,318 | 0.082 | 0.123 |
| 0.1-0.2 | 2,713 | 0.155 | 0.247 |
| 0.2-0.3 | 2,107 | 0.235 | 0.346 |
| 0.3-0.4 | 261 | 0.340 | 0.452 |
| 0.4-0.5 | 719 | 0.461 | 0.349 |
| 0.5-0.6 | 1,119 | 0.553 | 0.519 |
| 0.6-0.7 | 1,039 | 0.648 | 0.685 |
| 0.7-0.8 | 628 | 0.743 | 0.820 |
| 0.8-0.9 | 140 | 0.824 | 0.871 |
| 0.9-1.0 | 36 | 0.953 | 0.917 |

Probabilities between 0.1 and 0.4 were about 0.1 too low (more exceedances than predicted),
those between 0.4 and 0.5 about 0.1 too high, and those above 0.5 within 0.08 of the
observed frequencies.

### 9.6 Ordinary days, holidays and flagged days

Evaluation period, ATLAS regression, `empirical_relative@fixed365`, days 1-30:

| target days | forecasts | distinct days | coverage 80 % | coverage 95 % | width 95 % | MAE | bias | Brier (P75) |
|---|---|---|---|---|---|---|---|---|
| ordinary | 8,682 | 314 | 84.8 % | 94.3 % | 5,666 | 833.4 | -127.6 | 0.1832 |
| holiday-related | 858 | 33 | 64.1 % | 83.3 % | 5,533 | 1,102.7 | 303.7 | 0.1609 |
| flagged | 540 | 18 | 26.5 % | 70.2 % | 5,539 | 3,234.7 | -3,207.8 | 0.5402 |

Coverage is close to nominal on ordinary days (84.8 % and 94.3 %) and much lower on
holiday-related days (64.1 % and 83.3 %, over-forecast by about 300 per day) and on the 18
target days with an M1 data-quality flag (26.5 % and 70.2 %; those are intake drops that the
forecast does not anticipate, mean error −3,208). The strata leave gaps in the origin grid,
so no test is computed; 33 holiday-related and 18 flagged days are too few for a reliable
statement either way.

### 9.7 Sensitivity analyses (pre-registered, descriptive)

- **Data latency of 7 days** (residuals eligible a week later): evaluation coverage 79.7 %
  and 92.1 %, WIS 522.2 (521.8 with latency 0). The intervals do not depend on the most
  recent week of residuals.
- **HAC bandwidth** of the headline coverage test: p from 0.989 to 0.991 (80 %) and 0.445 to
  0.533 (95 %); effective sample sizes 5.2-8.1 and 6.0-9.0. No bandwidth changes a
  conclusion.

### 9.8 Future projection

[`projection/report.md`](results/atlas-m3-intervals-v1/projection/report.md) attaches the
sealed intervals to the M2 projection from 2026-06-30 (its point forecasts are identical to
M2's). They are calibrated at the origin on the residuals with targets in the preceding 365
days and are labelled **FAILED_CALIBRATION: not decision-grade**. Example:

| date | forecast | 80 % interval | 95 % interval | P(> 11,609, historical) | P(> 12,000, HYPOTHETICAL) |
|---|---:|---:|---:|---:|---:|
| 2026-07-01 | 11,124 | 10,202-12,210 | 9,523-14,022 | 0.25 | 0.15 |
| 2026-07-02 | 11,082 | 9,938-12,548 | 9,111-14,722 | 0.24 | 0.15 |
| 2026-07-03 | 9,623 | 8,601-11,048 | 7,808-13,380 | 0.07 | 0.05 |
| 2026-07-06 | 12,301 | 11,021-14,218 | 9,924-17,657 | 0.75 | 0.60 |
| 2026-07-07 | 11,945 | 10,722-13,820 | 9,645-17,011 | 0.61 | 0.45 |
| 2026-07-14 | 11,750 | 10,527-13,795 | 9,520-16,864 | 0.57 | 0.39 |
| 2026-07-30 | 11,281 | 10,116-13,268 | 9,209-16,223 | 0.37 | 0.26 |

### 9.9 Answers to the five questions

1. **Plausible range.** For the recommended regression, a 95 % interval from about 16 %
   below to 28-38 % above the point forecast (80 %: 12 % below to 15 % above), wider from
   day 2 on; but these intervals failed their calibration criteria and are exploratory.
2. **Historical coverage.** 79.9 % (80 % intervals) and 92.1 % (95 % intervals) over the
   evaluation year, with 95 % coverage about 3 points short from day 8 on and much lower
   on holidays and flagged days. The uncertainty of these coverages is ±15 and ±8 points.
3. **By horizon.** Width rises from day 1 to day 2 and then stays roughly level; coverage
   declines slowly with the horizon.
4. **Exceedance.** Probabilities for the historical high-demand threshold beat climatology
   in Brier score but were too low on average; for the hypothetical thresholds they were
   about right but not demonstrably better than climatology.
5. **When the history is insufficient.** Always, for decision-grade claims at present: no
   coverage or probability estimate here rests on 30 or more effective observations
   (8 to 16 for the primary model). Per forecast, the run labels an interval `unavailable` when fewer than 100
   distinct origins can calibrate it (none in these periods) and `thin` when its calibration
   residuals are worth fewer than 20 independent observations (96 B\* forecasts early in the
   selection period, rolling schemes only).

## 10. Exposure log

What was known when this protocol was designed:

- Everything M2 published: the point errors of every model on both periods, including the
  confirmation year's under-forecasting bias (mean error -256 requests per day for the
  recommended model) and its error growth with the horizon; the small effective sample
  sizes of the M2 comparisons.
- The legacy percentile 11,609 (used to set the hypothetical thresholds above it).
- During implementation, the M3 code was exercised only on synthetic series and on
  `configs/uncertainty/smoke.json`, whose origins and targets lie in early 2024 (M2
  development era). No interval, coverage or probability was computed on the M3 selection
  or evaluation periods before the configuration was committed.

| When | What | Period |
|---|---|---|
| Freeze, `cfdf634` | code, tests, configuration and sections 1-8 committed and pushed | none |
| After freeze | selection run at `cfdf634`; the seal committed and pushed as `ff44f54` | `m3_selection` |
| After seal | full run (selection re-run + evaluation), `verify`, projection at `ff44f54` | both |

No configuration, method, threshold or criterion changed after the freeze.

**Amendments after the independent review.** An adversarial review of the frozen code
recomputed the time-safety invariants on all 288,000 interval rows, 400 intervals, 300
probabilities and every headline number with its own code (largest difference 7e-12), and
planted 39 bugs. Leakage mutants were all caught. Its confirmed findings were fixed:

- `verify` now regenerates and compares `selection.json` and `calibration_status.json`;
  before, a status file edited together with its manifest hash passed `verify`, and
  `project` would have labelled a projection with it. `project` now also requires the
  status to equal the run's `metrics.json`, and `inspect` reports an incomplete run as an
  error.
- Decision rules made explicit where the code was looser than intended: tolerances are
  compared with a 1e-9 allowance, so a coverage exactly at the tolerance counts as inside
  (binary rounding had made 75.0 % at the 80 % level count as outside); `thin` intervals
  and forecasts without a predictive distribution now make the status at most
  `insufficient_evidence`; a run that does not reproduce its seal writes no decision
  status.
- Tests were added for the surviving planted bugs: the coverage test's offset by the level,
  the Gaussian baseline's horizon-1 calibration, the calibration effective-size bandwidth,
  the complete-origins rule, the seal's eligibility field, the reference-Brier pairing, the
  status boundaries, and the projection's checks of the status record.
- Wording corrected: the split-conformal minimum (19, not 39), the pooled-bucket bandwidth,
  "at or below" for quantile calibration, and exceedance criteria applying to every
  threshold. Brier scores on the selection period are in-sample for the selected method.

None of these changes alters a reference output: `atlas uncertainty verify` on the full
reference run is byte-identical with the amended code, the decision files included.

## 11. Limitations

- **Not prospective.** One frozen snapshot and a single final data vintage; the evaluation
  period was used in M2.
- **Small effective samples.** 336 overlapping 30-day paths are worth only tens of
  independent observations. Coverage close to nominal can be consistent with substantial
  miscalibration.
- **Exchangeability fails.** Level shifts, growth and year-end make past errors an imperfect
  guide to future errors; a 365-day window spans one annual cycle at most.
- **Thresholds.** The historical threshold is a demand percentile and the hypothetical
  thresholds are illustrative numbers; none is a measured service capacity.
