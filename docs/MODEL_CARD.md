# Model card: ATLAS 1.0

ATLAS is a research demonstration. It contains three models that build on each other: a daily
demand forecaster (M2), prediction intervals around it (M3) and a hypothetical decision model
(M4). This card describes all three, what they were evaluated on, what the evaluation showed and
what they must not be used for. The data are described in the [data card](DATA_CARD.md); every
known limitation is collected in [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

| | Forecaster (M2) | Prediction intervals (M3) | Decision model (M4) |
|---|---|---|---|
| Identifier | `atlas_linear_regression` (`atlas-m2-regression-v1`) | `empirical_relative@fixed365` | `atlas-m4-optimization-v1` (linear programme) |
| Status | recommended by a pre-registered rule; lead over the best simple baseline **not statistically established** | **`FAILED_CALIBRATION`** (pre-registered criteria) | every operational parameter **`HYPOTHETICAL`** |
| Use in ATLAS | archived 30-day projection, backtest metrics | quantile bands, exceedance statuses | reference scenarios, bounded simulations |
| Validated for operational decisions | no | no | no |

## 1. Model details

**Forecaster.** An ordinary least-squares regression on standardised columns, solved by SVD, with
full rank required. Inputs, computed by the M1 feature builder from data up to the forecast origin
only:

- an intercept and weekday indicators (Tuesday to Sunday; Monday is the reference);
- annual Fourier terms of order 1 and 2 and a linear trend from 2023-01-01;
- federal holidays by rule (the day off, and the days before and after the holiday);
- demand: the previous day (`lag_1`), the same weekday a week earlier (`lag_7`), the mean of the
  previous four same weekdays and the 28-day mean.

Forecasts for horizons 1 to 30 are made recursively: each prediction (clipped at zero, not rounded)
is fed back into the demand inputs of the next day. The model is refitted at every origin on an
expanding window. Its coefficients are identifiable and saved for every origin
(`coefficients.csv`). Specification: [BACKTESTING.md](BACKTESTING.md) section 3; configuration
`configs/backtest/atlas-m2-rolling-origin-v1.json`, fingerprint `78da64e8…1b1753`.

**Prediction intervals.** Empirical quantiles (type 7) of relative forecast errors, for each
horizon separately, calibrated once per period and then frozen. The calibration set holds the
residuals whose targets fall in the 365 days up to the cutoff and were already observed at the
issue date. 365 is a maximum: such sets held 365 residuals in the evaluation and 153-182 in the
selection period, when residuals had existed for only six months (M8 correction). A horizon is
pooled with its horizon bucket only when fewer than 100 origins can calibrate it, which never
happened in the committed runs. Levels 80 % and 95 %; exceedance probabilities come from the
same predictive distribution. Specification: [UNCERTAINTY.md](UNCERTAINTY.md) sections 2-6.

**Decision model.** A linear programme over one city-wide service unit and days `t = 1..T`:
requests served, overtime hours, surge requests and end-of-day backlog per day, subject to a
backlog balance, daily capacity, overtime and surge limits, a discretionary budget and a terminal
backlog target. It minimises discretionary cash plus notional (hypothetical) waiting penalties.
Solved with HiGHS (dual simplex) and certified (primal and dual feasibility, duality gap) for
every plan. Specification: [OPTIMIZATION.md](OPTIMIZATION.md) section 3.

**Owner and version.** Arthur Fay. ATLAS 1.0.0 (release candidate under review; see
[release/RELEASE_GATE.md](release/RELEASE_GATE.md)). Built with Python 3.12, NumPy 2.5, pandas
3.0 and SciPy 1.18 (HiGHS); no other modelling library.

## 2. Intended use

**Intended.** A reproducible demonstration of forecasting methodology, honest evaluation,
uncertainty assessment and decision simulation, for technical review (for example by employers or
peers), teaching, and as a base for further research on the same frozen data.

**Out of scope, and unsafe:**

- staffing, budgeting or service decisions for NYC 311 or any agency;
- presenting the archived July 2026 projection as a live forecast, or as validated;
- using the M3 intervals or exceedance probabilities as calibrated probabilities;
- presenting an M4 plan as a recommended, optimal or real staffing plan (it is optimal only for
  hypothetical parameters);
- inferring need, service quality or equity from request counts.

## 3. Training and evaluation data

The `legacy-v0` snapshot: city-wide daily counts of NYC 311 service requests from NYC Open Data
(dataset `erm2-nwe9`), 2023-01-01 to 2026-06-30, 1,277 days with no gaps (the [data
card](DATA_CARD.md) has the details and the provenance gaps).

Rolling-origin evaluation, pre-registered before any run ([BACKTESTING.md](BACKTESTING.md)
section 4):

| Period | Origins | Targets | Role |
|---|---|---|---|
| Development | 2024-02-01..2025-05-31 (486) | to 2025-06-30 | model selection, sealed before confirmation |
| Embargo | 2025-06-01..2025-06-29 | | not evaluated |
| Confirmation | 2025-06-30..2026-05-31 (336) | 2025-07-01..2026-06-30 | primary tests, Holm-adjusted |
| Legacy window | 2026-03-03..2026-06-29 | 2026-04-02..06-30 | descriptive only |

Every origin refits every model on data through that origin; actual values are attached only
after all forecasts of a run exist. M3 uses the M2 confirmation period, already exposed and
labelled as such, for its evaluation.

## 4. Metrics

Mean absolute error (MAE, requests per day) is primary; RMSE, bias, WAPE, MASE (scaled by the
in-sample weekly naive error through each origin) and MAPE are secondary. Comparisons use the same
forecasts for both models. Tests: Diebold-Mariano with the Harvey-Leybourne-Newbold correction and
a HAC variance, Holm-adjusted over the two pre-registered primary hypotheses. Intervals: coverage
with dependence-aware (HAC) intervals and effective sample sizes, interval width and the weighted
interval score (WIS).

## 5. Results

### 5.1 Forecaster (M2)

MAE in requests per day, horizons 1-30 (the full tables by horizon and bucket are in
[BACKTESTING.md](BACKTESTING.md) section 6):

| Model | Development | Confirmation |
|---|---:|---:|
| `atlas_linear_regression` | 817.9 | 984.9 |
| same-weekday median of 5 weeks (B\*, the best simple baseline) | 953.0 | 1,112.2 |
| exponential smoothing (ETS, weekly) | 989.1 | 1,279.8 |
| seasonal naive | 1,129.1 | 1,371.5 |
| legacy regression (the original project's model) | 1,041.9 | 1,047.3 |

Primary confirmation tests (pre-registered, Holm-adjusted):

| Hypothesis | MAE difference [95 % interval] | Improvement | Holm-adjusted p | Effective n |
|---|---:|---:|---:|---:|
| H1: ATLAS regression vs B\* | −127.3 [−256.4, +1.9] | 11.4 % | **0.054 (not significant)** | 13.8 |
| H2: ATLAS regression vs seasonal naive | −386.6 [−645.0, −128.2] | 28.2 % | 0.007 | 21.2 |

What the evidence supports:

- The model is the most accurate of the seven compared in both periods (lowest MAE, RMSE, WAPE,
  MASE and MAPE at every horizon bucket), but **its lead over the simple same-weekday median is not
  statistically established**: the interval includes zero, and the confirmation year is worth only
  about 10-14 independent observations for this comparison.
- It is 28.2 % more accurate than seasonal naive (H2, Holm-adjusted p 0.007 as computed). After
  the M8 size correction this result is borderline, not clear (next item).
- **M8 erratum.** The development selection test that chose this model (HLN-DM, conservative
  p 0.044 against a 5 % gate) over-rejects at a 30-day horizon with a few hundred overlapping
  origins. Under its own assumptions the pre-registered rule rejects a true null 7.6 % of the time
  with 486 origins and 9.1 % with 336, instead of 5 %. After a size correction:
  - the selection p-value is 0.069, so the gate would not have been passed, and a correctly
    sized test would most likely have kept B\*;
  - H1 becomes 0.095, and H2 0.022 (Holm 0.045).

  These corrected values assume the test's own short-memory dependence (an MA(29) differential).
  The confirmation differentials stay positively autocorrelated beyond 29 days.
  In the audit's block bootstrap, which keeps the data's own dependence, H2's p-value was
  0.031-0.079 and its Holm-adjusted value at least 0.06 ([audit/M8_FINDINGS_REGISTER.md](audit/M8_FINDINGS_REGISTER.md),
  M8-04). So H2's significance is not robust either.
  The pre-registered choice stands, because changing it after the fact would be worse science,
  but its statistical support is weak. Details: [BACKTESTING.md](BACKTESTING.md), M8 erratum, and
  the reproducible studies in `docs/audit/scripts/`.
- The lead is not uniform: the model loses to B\* in 6 of 28 origin months. In the confirmation
  period, almost all of its error growth with horizon comes from the recursion.
- The original project's "4.93 % MAPE" is a horizon-1 score on observed inputs over 90 easy days;
  run recursively, its own model scores 9.0-9.8 % MAPE from horizon 7 on over the confirmation year
  ([LEGACY_LIMITATIONS.md](LEGACY_LIMITATIONS.md)).

**Archived projection.** Fitted on all data through 2026-06-30, the model projects 2026-07-01 to
2026-07-30 (total 333,968 requests). It is an archived projection, not a live forecast, and has
never been scored: no outcome after 2026-06-30 is in the snapshot.

### 5.2 Prediction intervals (M3)

On the M3 evaluation period (the M2 confirmation targets), horizons 1-30:

| Level | Coverage | 95 % HAC interval of the coverage | Effective n |
|---|---:|---:|---:|
| 80 % | 79.9 % | 65.3 to 94.5 % | 8.1 |
| 95 % | 92.1 % | 84.3 to 99.8 % | 9.0 |

The 95 % intervals covered 91.8 % and 91.7 % of outcomes at horizons 8-14 and 15-30, more than the
pre-registered 3 points below nominal, so the status is **`FAILED_CALIBRATION`**. Independently of
that, about 8-9 effective observations are far too few for any calibration claim. Exceedance
probabilities share this status. The intervals are shown in ATLAS only with that label
([UNCERTAINTY.md](UNCERTAINTY.md) section 9).

The M8 audit noted two properties of the construction and the evaluation. Neither was changed;
both are recorded for a future protocol version:

- **Construction bias.** The type-7 quantiles carry no finite-sample correction. Even with
  exchangeable errors, they lower the expected coverage of a 95 % interval to about 94.5 % at
  365 residuals. That is more than the 0.2-0.3 points by which the two 95 % buckets missed the
  tolerance. With a corrected construction the status might have been `insufficient_evidence`
  rather than failed, and neither status is decision-grade.
- **HAC degrees of freedom.** The coverage intervals above use `t(335)` critical values despite
  8-9 effective observations. They are therefore narrower than a `t(ESS − 1)` reference would
  give (±14.6 instead of ±17.5 points at 80 %), and they are indicative only.

Details are in [audit/M8_SCIENTIFIC_VALIDATION.md](audit/M8_SCIENTIFIC_VALIDATION.md) section 3.

### 5.3 Decision model (M4)

Nine reference scenarios over the archived July 2026 projection (including an M2-bias shift and an
M3 upper-quantile stress path); three have a strictly feasible plan, six are infeasible under their
hypothetical limits and are reported with labelled `DIAGNOSTIC` relaxed plans. Every optimum is
certified, and an independent re-implementation in the M8 audit reproduced every committed optimum,
frontier and sensitivity point to about 1e-15 ([OPTIMIZATION.md](OPTIMIZATION.md) section 9). The
results describe the hypothetical model only: no real NYC staffing, cost or capacity data exist,
so no plan is a recommendation.

## 6. Factors that affect performance

- **Calendar.** Weekday pattern and federal holidays are modelled; NYC and NYS observances that
  are not federal holidays, and declared events, are not.
- **Anomalies.** Intake interruptions, catch-up surges and storms are not modelled. The M1
  data-quality flags are incomplete (they miss part of the winter 2025-26 anomaly cluster;
  [DATA_QUALITY.md](DATA_QUALITY.md)). The worst-covered M3 days are flagged days on which demand
  was far above the forecast.
- **Regimes.** The loss differential against B\* follows month-long regimes; the model's lead comes
  mostly from winter and spring 2026.
- **Recursion.** Errors compound with horizon because predictions are fed back.
- **Reporting changes.** Agency coding changes exist in the data (for example DOE stops on
  2026-03-19); they are immaterial for city-wide totals but would matter for agency-level work.

## 7. Ethical considerations

- 311 requests measure **reporting**, not need: who reports, through which channel and how often
  varies by neighbourhood, language and access. A forecast of request counts says nothing about
  unmet need or service equity.
- The snapshot holds city-wide and borough/agency aggregate counts only; it contains no personal
  data.
- A polished interface makes numbers look authoritative. Every number ATLAS shows carries its
  status next to it (not significant after Holm, `FAILED_CALIBRATION`, `HYPOTHETICAL`, archived),
  and the API refuses to label anything a validated recommendation.
- The decision model encodes value judgements (penalty weights on waiting) as hypothetical
  parameters; real use would need accountable owners for those choices.

## 8. Caveats and recommendations

1. Treat the forecaster as a well-tested research model whose advantage over a much simpler rule
   is not established. For any real use, the same-weekday median of five weeks is a close,
   transparent alternative.
2. Do not use the intervals or exceedance probabilities as probabilities. Recalibration would need
   new data and a new pre-registered evaluation.
3. Do not use the M4 plans. Real use would need an evidence register of real parameters, agency
   level units and validated uncertainty.
4. Any new evaluation needs data after 2026-06-30 (a new snapshot) and a new pre-registered
   protocol; the confirmation period has been exposed.

## 9. Reproducibility and provenance

In the M8 audit every committed result was regenerated with the locked environment and compared
byte for byte: the full M2 reference run (result fingerprint `d53bc911…`), the full M3 pipeline
(`2e673489…`, `c4d2adbf…`) and the M4 reference run (`0dc4eb0f…`). All runs record the
configuration, data (`5ab8b3a5…`), code and environment fingerprints in their manifests.

Byte-identical reproduction has two conditions:

- **CPU class.** The runs that involve the regression (M2, the M3 pipeline built on its forecasts,
  the legacy holdout) need the CPU class of the reference runs. The locked NumPy's OpenBLAS then
  chooses its SkylakeX (AVX-512) kernels. On other CPUs the last bits differ, and `verify`
  reports a difference. The M4 run reproduces either way.
- **Own commit.** The sealed M2 development record verifies only with the code and lock of its
  own commit, `a16672f`.

How to reproduce: [DEVELOPMENT.md](DEVELOPMENT.md). The release-candidate manifest:
[release/RELEASE_CANDIDATE.md](release/RELEASE_CANDIDATE.md).
