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
committed M2 spot check exactly.

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
  beyond each end of a 95 % interval and exceed the split-conformal minimum of 39.
- **Calibration support.** Each set carries the HAC effective sample size of its residual
  series (Bartlett, bandwidth `2h`; per-origin means for a pooled bucket). Below **20** the
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
quantile and on average, WIS, quantile calibration (share of outcomes below each quantile),
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
evidence (or with forecasts that were not issued) is `insufficient_evidence`. Only when all
hold is the status `decision_grade`. For each threshold, exceedance probabilities are
`decision_grade` only if their Brier score beats climatology with conservative p < 0.05,
the mean probability is within 0.05 of the observed frequency, and the effective sample size
is at least 30. A status that is not decision-grade is disclosed and kept: no result is
adjusted after the fact to pass.

**Pre-registered sensitivity analyses** (descriptive): a data latency of 7 days; ordinary,
holiday-related and flagged target days; wider HAC bandwidths for the headline coverage
test.

## 9. Results

The sealed runs are added in a later commit, after this protocol.

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
