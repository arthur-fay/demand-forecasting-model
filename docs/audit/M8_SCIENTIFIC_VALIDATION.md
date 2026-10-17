# M8 scientific and mathematical validation

How the science of ATLAS M0-M4 was checked in M8, by auditors who wrote their own code rather
than trusting the package, and what the checks showed. The full evidence of each check is in
[M8_AUDIT_EVIDENCE.md](M8_AUDIT_EVIDENCE.md); the findings and their corrections are in
[M8_FINDINGS_REGISTER.md](M8_FINDINGS_REGISTER.md) and [M8_CORRECTIONS.md](M8_CORRECTIONS.md).

| Milestone | Verdict | What decides it |
|---|---|---|
| M0-M1 data | **sound** | every figure recomputed independently; no leakage; disclosures corrected (M8-10) |
| M2 forecasting | **sound protocol, weak evidence** | forecasts and evaluation reproduced independently and byte for byte; the selection test is oversized (M8-04 erratum); the lead over B\* is not established |
| M3 uncertainty | **correctly computed, FAILED_CALIBRATION** | 288,000 interval rows rebuilt independently; the status follows the pre-registered rule and stays FAILED |
| M4 optimisation | **mathematically correct, HYPOTHETICAL** | an independent LP reproduces every committed number to about 1e-15; hand-derived instances match exactly |

## 1. Data (M0-M1)

- **Integrity.** Every SHA-256, size, header, row count and date range of the 16 tables and 7
  artifacts recomputed: 0 mismatches; the CSVs are byte-identical to the original commit.
  Single-file tampering (a count, line endings, a duplicate or missing date, a negative count, a
  float, a BOM, a lowercase borough) is refused at the stage that should refuse it. Every M2-M5
  load path goes through integrity and validation.
- **Coverage.** 1,277 days, none missing or duplicated, the leap day present, the last day
  complete; minimum 5,596 (2023-12-25), maximum 22,806 (2026-02-24).
- **Reconciliation.** The city-wide total exceeds the five-borough segments by 13,279 requests
  (0.1077 %) on 1,273 days, never the other way; the decomposition into borough components plus
  the unassigned residual sums to the total on every day.
- **Legacy figures.** Every KPI and metric recomputed from the CSVs reproduces within its
  published precision (one declared RMSE rounding); `atlas check` prints what each one means.
- **Leakage.** A perturbation test replaced every value after seven origins with random values,
  NaN, zero or 1e12 and compared training rows, direct rows and 30-step recursive paths: 364
  checks, 0 differences. Declared events are visible only from their announcement date. The
  quality flags are causal and never enter the features.
- **Corrected (M8-10, M8-11).** The anomaly detectors miss part of the winter 2025-26 cluster
  (three of the five largest days carry no flag) and an outage within four weeks of another; the
  documentation now says so instead of claiming robustness.

## 2. Forecasting (M2)

- **Independent re-implementation.** All seven models were re-implemented in NumPy without
  importing ATLAS and run at the six spot-check origins and 14 interior origins: the baselines,
  the legacy regression and exponential smoothing match exactly; the ATLAS regression to 3.9e-8.
- **Reproduction.** The full reference run (development, confirmation and legacy window;
  197,610 records) was regenerated: byte-identical, result fingerprint `d53bc911…`. The sealed
  development run verifies with the code of its own commit.
- **Leakage.** Multiplying every actual after an origin by 10 leaves every prediction of all
  seven models bit-identical. The MASE scale uses data through the cutoff only.
- **Protocol.** Development and confirmation periods do not overlap (an embargo month between
  them); the selection was sealed before the confirmation run (commit and manifest times); the
  full run's recomputed selection equals the seal.
- **Statistics.** The DM/HLN statistic, its p-values, the HAC variance, the effective sample
  sizes and the Holm adjustment match independent implementations (to 1e-12 or better).
- **Finding M8-04 (MEDIUM), corrected by an erratum.** At a 30-day horizon with 336-486
  overlapping origins, the HLN-DM test with a `t(T − 1)` reference over-rejects under its own
  assumptions. A reproducible Monte Carlo study (20,000 replications per setting, run through
  the unchanged `atlas.stats` code; [scripts/dm_size_study.py](scripts/dm_size_study.py)) gives:

  | | 486 origins | 336 origins |
  |---|---:|---:|
  | size of the pre-registered rule at a nominal 5 % | 7.6 % | 9.1 % |
  | size-corrected 5 % critical value of the statistic | 2.21 | 2.39 |

  | test | nominal p | size-corrected p |
  |---|---:|---:|
  | development selection (gate 5 %) | 0.044 | 0.069 |
  | H1, ATLAS regression vs B\* | 0.054 | 0.095 |
  | H2, ATLAS regression vs seasonal naive | 0.003 (Holm 0.007) | 0.022 (Holm 0.045) |

  With a correctly sized gate the protocol would most likely have kept the simpler B\*. The
  pre-registered decision stands, because replacing the test after seeing its outcome would be
  worse science; the documents now say that the selection's support is weak and that the
  earlier "robust to the bandwidth" statements do not hold ([../BACKTESTING.md](../BACKTESTING.md)
  section 12). A test (`tests/test_m8_dm_size.py`) pins the over-rejection and the recorded study.

  These corrected values assume the test's own null: no dependence beyond lag 29. The final
  independent review found the confirmation differentials autocorrelated beyond it. The
  committed study bears this out for H1: its mean autocorrelation over lags 30-120 is 0.126,
  and only 1.5 % of MA(29) series reach it. H2's (0.042) is in the same direction but within
  the MA(29) range (8.0 % reach it), and the data cannot show which null is right. A post hoc study ([scripts/dm_dependence_sensitivity.py](scripts/dm_dependence_sensitivity.py),
  with its [recorded output](scripts/dm_dependence_sensitivity_output.md) and
  `tests/test_m8_dm_dependence.py`) repeats the correction under eight nulls built from the
  committed loss differentials: circular block bootstraps with blocks of 30-90 days, and
  Gaussian nulls with each differential's own Bartlett-tapered autocovariance. It also checks
  how both methods behave when MA(29) is true. It finds:

  - **H2** stays significant after Holm only under MA(29) (0.045) and the taper-29 Gaussian
    null (0.036). It is at the threshold under 30-day blocks (0.050), and at 0.068-0.155 under
    the six nulls with dependence of 45 days or more. Its significance after the size
    correction is not robust.
  - **H1** is not significant under any null (0.094-0.164).
  - **The selection gate** fails under MA(29), the taper-29 Gaussian null and every block
    bootstrap (0.068-0.130). It passes only under the long-taper Gaussian nulls (0.014-0.049),
    which give p-values below the exact ones when MA(29) holds. So "most likely B\*" stands.
- **Model-selection bias.** Six candidates were compared on the development period, which the
  documents state; the confirmation period was used once for the primary tests and has since
  been exposed (M3 reused it, labelled as such). A new untouched evaluation needs data after
  2026-06-30.

## 3. Uncertainty (M3)

- **Reproduction.** The selection run, the sealed full run and the projection were regenerated:
  every result file byte-identical (fingerprints `2e673489…`, `c4d2adbf…`).
- **Independent reconstruction.** All 268,800 residual-based interval rows were rebuilt from the
  forecasts and the raw series by code that does not import ATLAS: worst relative difference
  8.6e-16; climatology rows to 1.8e-12. A positive control that admits one more day of residuals
  changes 112,524 rows, so the eligibility check (`target date <= issue date − latency`) has
  power.
- **Coverage and dependence.** Coverage, HAC intervals, p-values and effective sample sizes
  recomputed: maximum differences 0 (coverage), 5.9e-15 (intervals), 1.7e-12 (p). Headline: 80 %
  coverage 79.9 % (65.3-94.5 %, effective n 8.1); 95 % coverage 92.1 % (84.3-99.8 %, effective
  n 9.0).
- **Status.** 95 % coverage is 91.8 % at horizons 8-14 and 91.7 % at 15-30, more than 3 points
  below nominal, so the pre-registered status is `FAILED_CALIBRATION`; it is pinned by hash,
  cross-checked against the metrics and the manifest, and cannot be overridden by any code path.
  It reaches every API response, dashboard view and M4 output. **It stays FAILED**; it is
  accepted only as a disclosed limitation of a research demonstration.
- **Exceedance probabilities.** Reproduced to 2.2e-16; strictly inside (0, 1); monotone in the
  threshold; user thresholds must be labelled `HYPOTHETICAL`.
- **Corrected by errata (M8-08, M8-13 to M8-17, M8-39).** The flagged target days were
  under-forecast (demand far above the forecast), not "intake drops"; the M3 comparisons are
  descriptive and unadjusted for multiplicity; the coverage gate's "conservative" p is the
  lenient direction for a calibration claim; `fixed365` held 153-182 residuals in the selection
  period; the effective-sample-size statements are now consistent.
- **Noted, not changed.** The empirical quantiles use type 7 without an `(n + 1)`
  finite-sample correction, which lowers the expected coverage of a 95 % interval to about
  94.5 % at n = 365 even under exchangeability (about 93.8 % at n = 153). That construction bias
  is larger than the 0.2-0.3 points by which the two 95 % buckets missed the 3-point tolerance,
  so with a finite-sample correction the status might have been `insufficient_evidence` rather
  than `FAILED_CALIBRATION`. Neither is decision-grade: the effective sample size (8-9) is far
  below the pre-registered 30, and the pre-registered result stands as computed. Separately, the
  HAC intervals of the coverage use `t(335)` critical values despite 8-9 effective observations,
  which makes them narrower than a `t(ESS − 1)` reference would (±14.6 instead of ±17.5 points
  at 80 %); the documents call these p-values indicative. Both are recorded for a future
  protocol version.

## 4. Optimisation (M4)

- **Formulation.** The linear programme written down from the code equals
  [../OPTIMIZATION.md](../OPTIMIZATION.md) section 3: `4T` columns, no slack, every row a named
  constraint; every coefficient is unit-consistent (requests per day, staff hours, USD).
- **Committed plans.** Backlog balance (maximum residual 3.6e-12), capacity, daily limits, the
  overtime total, non-negativity, available work and the budget hold on all 27 committed plans.
- **Independent solve.** A SciPy formulation with a different variable layout and solver
  (HiGHS IPM) reproduces the three strict optima (relative differences ≤ 3.6e-16), the least
  violations, minimum spends and relaxed scores of the six infeasible scenarios, all 37 frontier
  points, all 43 sensitivity points and the bisected switch points.
- **Known answers.** Four tiny instances derived by hand (cheapest-first service, a binding
  budget, an infeasible terminal target, ties) were solved through the package: objective,
  plan, every dual, the finite-difference brackets, the infeasibility diagnosis and the
  uniqueness verdicts all match.
- **Certificates.** 3,292 certificate checks in a re-run all hold, with maximum ratios to the
  tolerance of 5.2e-7 (primal), 1.8e-6 (dual sign), 4.4e-7 (stationarity) and 5.2e-7 (duality
  gap). Shadow prices have the documented signs and units and equal finite differences.
- **Robustness.** 287 fuzzed API simulations, 7 extreme requests, exact power-of-two scaling
  and 450 random small instances: no failure, and every optimum equals the independent LP.
- **Claims.** Infeasible scenarios are reported only with labelled `DIAGNOSTIC` relaxed plans,
  never subtracted from feasible ones; every "optimal" is qualified as optimal for the assumed,
  `HYPOTHETICAL` model. Corrected wording: the certificate ratio is a fraction of its tolerance,
  not a "gap", and a plan is "an optimum within tolerance", one of several when not unique
  (M8-19).

## 5. Conclusion

The mathematics and the computations of ATLAS hold up under independent re-implementation, and
every committed result reproduces byte for byte. The scientific weaknesses are in the strength
of the evidence, not in the arithmetic: the forecaster's advantage over a simple rule is not
established and its selection rests on an oversized test, the intervals failed calibration, and
the decision model's parameters are invented. After the M8 corrections the documents say so
wherever the numbers appear. ATLAS is a sound research demonstration and is not suitable for
operational decisions.
