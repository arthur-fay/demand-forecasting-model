# Forecasting and rolling-origin backtesting (M2)

M2 answers one question: **how accurate is the 30-day forecasting procedure the project
actually uses, at each horizon, when it is run as it would have been run in the past?**

The legacy headline, *4.93 % MAPE*, was measured with each day's *observed* previous days as
inputs (horizon 1). The published 30-day forecast instead feeds its own predictions back
recursively. M2 re-runs that recursive procedure from 822 historical forecast origins,
refitting every model at every origin with data through that origin only, and compares it
with simple baselines under identical conditions.

```bash
uv run atlas backtest legacy                               # exact legacy reproduction
uv run atlas backtest run --output-dir runs/dev            # pre-registered development run
uv run atlas backtest verify docs/results/atlas-m2-rolling-origin-v1/full   # reference run
```

**M8 note.** Until M8 the third command verified the sealed development record
(`.../development`). That record reproduces byte for byte only with the code of its own
commit, `a16672f`; with the current code its forecasts reproduce but three reporting files
and the result fingerprint differ, so the command fails. Section 8 gives the recipe for it.
The reference run above verifies with the current code (checked in M8: identical, about 7
minutes on one core).

All numbers below, in tables and in the text, are generated from the committed engine output
in [`docs/results/`](results/) by a script, and tests check the headline tables and the
quoted numbers against `metrics.json`. Nothing here is a prospective validation: every
forecast is a re-forecast of days that were already in the data.

## 1. Findings

1. **The legacy 4.93 % is reproduced exactly, and it is a horizon-1 number.** The M2
   re-implementation of the legacy regression reproduces all 90 published holdout predictions
   and all 30 published future forecasts. On the same 90 days, refitting at every origin
   gives 4.91 % at horizon 1. Run recursively, as the published forecast was, the same model
   scores 5.5-5.9 % at horizons 2-30 on those 90 days, which were an
   unusually easy period. Over the whole confirmation year its recursive MAPE is
   9.0-9.8 % from horizon 7 on.
2. **The faithful legacy model does not beat simple baselines reliably.** In the development
   period it is worse than the same-weekday median (B\*), the same-weekday mean and
   exponential smoothing at every horizon bucket beyond day 1 (MAE 1,041.9 against
   953.0 for B\* over days 1-30), although none of these 12 deficits is
   statistically significant (conservative DM p 0.064-0.601). In the
   confirmation period it is better than every baseline, and better than B\* by
   5.8 % with an interval that includes zero (DM p = 0.26). Its non-estimable and high-leverage
   forecasts, a numerical artefact of its design (section 6.11), do not explain the
   development deficit: on the forecasts that remain it is still 10.2 %
   worse than B\* on the same forecasts (9.3 % on all).
3. **The modernised regression is the most accurate forecaster in both periods, but its lead
   over the best baseline is not statistically established.** It has the lowest MAE, RMSE,
   WAPE, MASE and MAPE at every horizon and bucket of both periods (not the smallest bias). It
   was selected on the development period under the pre-registered rule and confirmed on the
   later period: confirmation MAE 984.9 against 1,112.2 for B\* (11.4 % better) and 1,371.5
   for seasonal naive (28.2 % better). Against seasonal naive the primary test is clear (Holm
   p = 0.007) and stays significant under every HAC bandwidth tried. Against B\* the 95 % interval of the MAE
   difference, −256 to +2 requests per day, includes zero (Holm p = 0.054). Across the HAC
   bandwidths tried, which allow for the month-long regimes in the loss differential, its
   p-value ranges from 0.047 to 0.096, and the confirmation year is
   worth only about 10-14 independent observations for this comparison
   (section 6.2).
   **M8 erratum (section 12).** At this horizon and these sample sizes the test rejects a
   true null more often than its nominal level: under its own assumptions the
   pre-registered rule rejects 9.1 % of null series at a nominal 5 % with 336 origins and
   7.6 % with 486. After a size correction the selection test that chose this model has
   p = 0.069 (nominal 0.044), above its 5 % gate, so its statistical support is weak. H2
   keeps p = 0.022 (Holm 0.045) under the pre-registered rule and the test's own MA(29)
   dependence, but not under three of the five HAC bandwidths, so "stays significant under
   every HAC bandwidth tried" no longer holds. Nor is it robust to the dependence the
   confirmation differentials show: Holm 0.068-0.155 under nulls with dependence of 45 days
   or more (section 12). H1 becomes 0.095. The pre-registered choice stands.
4. **In the confirmation period, almost all of the error growth with horizon comes from
   recursion.** Applying the same fitted regressions to *observed* previous days keeps their
   confirmation MAE nearly flat across horizons (686-749); feeding their own
   predictions back raises it to 1,033-1,086 at day 30 (recursion accounts for
   93 % and 96 % of the growth). In the development
   period it accounts for less: 67 % for the modernised regression and
   43 % for the legacy model, whose observed-input error itself grows
   26 % from day 1 to day 30 (section 6.9).
5. **The lead is not uniform.** The modernised regression loses to the same-weekday median
   in 6 of 28 origin months, including the December 2024
   year-end and the calm summer of 2025. In the confirmation period its lead over B\* comes
   about equally from winter (November-February origins, 51 % of the net
   gain) and spring 2026 (March-May, 60 %; May alone
   31 %).
6. **Recommendation**: `atlas_linear_regression`, by the pre-registered confirm-or-fallback
   rule, with the caveats in findings 3 and 5. The same-weekday median of five weeks is a
   close and much simpler alternative, and it is the fallback the protocol names. The legacy
   regression should not be used: its coefficients are not identifiable, 465 of its
   development forecasts are not even estimable, and it does not beat the baselines
   consistently.

## 2. Three kinds of numbers

| Record type | What it is | Where |
|---|---|---|
| `legacy_one_step_holdout` | The original evaluation, re-computed: one fit through 2026-04-01, each of the next 90 days predicted from its observed previous days (horizon 1) | `atlas backtest legacy`; [results](results/legacy-one-step-holdout/) |
| `historical_backtest` | Rolling-origin re-forecasts at horizons 1-30, refitted at every origin on data through that origin only | `atlas backtest run`; [results](results/atlas-m2-rolling-origin-v1/) |
| `future_projection` | Forecasts beyond 2026-06-30; no outcome observed, not validated. The committed one is an archived projection, not a live forecast (section 10) | `atlas forecast`; [projection](results/atlas-m2-rolling-origin-v1/projection/) |

The backtest also writes a fourth, clearly separated record type,
`diagnostic_observed_inputs` (section 6.9), which reads actuals after the origin and is never
used to compare or choose models.

## 3. Forecasting models

All seven models implement one interface (`atlas.models.Forecaster`): `fit(history)` on a
`DemandSeries` that ends at the forecast origin, then `forecast(steps)`. Every demand input
is computed by the M1 feature builder, so every model inherits its leakage guards.

| Model | Family | Procedure | Forecast for `o + h` |
|---|---|---|---|
| `naive` | baseline | direct | `y(o)` |
| `seasonal_naive` (reference) | baseline | direct | `y(o + h − 7·⌈h/7⌉)`: the latest observed day with the target's weekday |
| `same_weekday_mean_4w` | baseline | direct | mean of the 4 latest observed same-weekday values (`SameWeekdayMean(4, first_week=⌈h/7⌉)`) |
| `same_weekday_median_5w` | baseline | direct | median of the 5 latest observed same-weekday values |
| `seasonal_exponential_smoothing` | baseline | direct | ETS(A,N,A), weekly season: level + seasonal term of the target's weekday |
| `legacy_linear_regression` | regression | recursive | faithful notebook model (below) |
| `atlas_linear_regression` | regression | recursive | `atlas-m2-regression-v1` (below) |

**Horizon-correct baselines.** For `h > 7` the "same weekday last week" is not yet observed
at the origin. The weekly baselines therefore use the most recent *observed* week:
`h = 8` reads `y(o − 6)`, `h = 14` reads `y(o)`, `h = 15` reads `y(o − 6)` again. Tests check
every horizon against closed forms.

**Seasonal exponential smoothing.** Additive level and weekly seasonal terms, initialised from
the first week of the history; `α ∈ {0.05, 0.1, 0.2, 0.3, 0.5, 0.7}` and
`γ ∈ {0.01, 0.05, 0.1, 0.2, 0.3}` (`γ ≤ 1 − α`) chosen at every origin by the smallest
in-sample one-step squared error after a 28-day burn-in (deterministic tie-break). A
missing day updates nothing. The chosen `α`, `γ`, the final level and the seven seasonal
terms are written to `coefficients.csv` for every origin, so each forecast can be
recomputed from them (`level + season_step_k`, `k = (h − 1) mod 7 + 1`). It is the fitted
statistical baseline both design reviewers asked for; no SciPy or statsmodels dependency
is needed.

**Faithful legacy regression** (`legacy-notebook-cell-14`). The 32 notebook columns in the
notebook's order: trend, year, day of year, `is_weekend`, `sin`/`cos(2π·doy/365.25)`, lags
1, 7, 14 and 28, rolling means of 7 and 28 days, rolling standard deviation of 28 days, full
weekday dummies (Monday..Sunday) and full month dummies (1..12), plus an intercept. Fitted as
scikit-learn's `LinearRegression()` does: centre `X` and `y`, minimum-norm least squares
(LAPACK `gelsd`, cut-off `max(n, p)·ε`), intercept `ȳ − x̄·b`. Recursive forecast: each
prediction is clipped at 0 and rounded half to even **before** it is fed back, exactly as
in notebook cell index 16.

- The implementation reproduces **all 90** published holdout predictions and **all 30**
  published future forecasts exactly (`atlas backtest legacy`; CI). The largest distance of
  a raw prediction from its rounded value in the 30-day path is 0.4987, so the check is
  sensitive to the feedback rule: feeding back unrounded values breaks it.
- The design is rank-deficient by construction (rank 29 of 32: weekday dummies + intercept,
  month dummies + intercept, `is_weekend`). Its coefficients are **not identifiable and are
  never reported**; only its predictions are evaluated.
- A fourth exact dependency exists while the training data span only 2023-2024:
  `trend − 365·(year − 2023) − (day_of_year − 1)` counts leap days and is 0 on every day
  until 2025-01-01. Forecasts for 2025 dates made from December 2024 origins are therefore
  not estimable: their value depends on which least-squares solution is chosen. From
  2025-01-01 the same expression is a step that the design must estimate from the first
  few 2025 rows, with training-row leverage close to 1. The backtest keeps all these
  forecasts (they are what the legacy method would have produced), flags them per record
  (`estimable`, `leverage`) and reports metrics with and without them.

**Modernised regression** (`atlas-m2-regression-v1`). Fixed before any backtest run:

- intercept, weekday indicators Tuesday..Sunday (Monday is the reference level);
- annual Fourier terms of order 1 and 2, and a trend from the fixed anchor 2023-01-01;
- federal holidays by rule: the federal day off, and the days before and after the actual
  holiday. The actual-date indicator is left out: it differs from the day-off indicator only
  on weekend holidays, two training days in 2023 and no backtest target, so its coefficient
  would be identified by one event and never tested;
- demand: `lag_1`, `lag_7`, the mean of the previous four same weekdays and the 28-day mean.

OLS on standardised columns (training mean and standard deviation), solved by SVD, with
full rank required (a rank-deficient or constant-column design is a recorded failure, never
a silent minimum-norm fit). Recursive, clipped at 0, not rounded. Coefficients are
identifiable and saved for every origin (`coefficients.csv`). Excluded a priori: codings
that are collinear with the indicators (`is_weekend`, numeric weekday, month, day of year,
ISO week), `lag_364` (it would need a second year of history and delay the first origin to
2025), rolling standard deviations (scale, not level) and declared events (none declared).

**Not included.** A statistical model with a trend (Holt-Winters with damped trend), and
anything needing new dependencies or external data.

## 4. Rolling-origin protocol (pre-registered)

Configuration: `configs/backtest/atlas-m2-rolling-origin-v1.json`, fingerprint
`78da64e8c2ce75d9318cf855137d015e9692dbd50517a70d00243d7f731b1753` (pinned by a test).

| Item | Value |
|---|---|
| Data | `legacy-v0` `daily_total`, 2023-01-01..2026-06-30, 1,277 days, complete calendar; integrity and validation must pass first |
| Horizons | 1..30 days |
| Origins | every day (stride 1) |
| Training window | expanding from 2023-01-01; refit of every model at every origin |
| Minimum history | 365 days before the first origin (it has 397); the regressions also need ≥ 365 complete training rows |
| Development (selection) | origins 2024-02-01..2025-05-31 (486), targets 2024-02-02..2025-06-30 |
| Embargo | origins 2025-06-01..2025-06-29 are not evaluated: their targets would straddle the two periods |
| Confirmation | origins 2025-06-30..2026-05-31 (336), targets 2025-07-01..2026-06-30 |
| Legacy window (descriptive) | origins 2026-03-03..2026-06-29 (119); only targets 2026-04-02..06-30 are tabulated, 90 per horizon; horizons past the data are recorded as `beyond_snapshot` |

At each origin `o`:

1. `history = series.through(o)`: a new series object that ends at `o`;
2. each model is fitted on `history` alone. The regressions use horizon-1 training rows
   for every target from 2023-01-29 to `o`, built from observed demand; the baselines use
   M1 direct rows (data through `o` only);
3. the model forecasts `o + 1 .. o + 30`; the regressions feed every prediction back into
   their lag and rolling features through M1 `recursive_forecast`;
4. the output is checked against the forecasting contract;
5. only after every forecast of the run exists are the actual values attached, by target
   date.

**Failure policy.** A model reports anticipated conditions as `ForecastError` with a code;
the engine records them per model, origin and horizon and carries on. Any other exception,
including `LeakageError`, stops the run.

| Condition | Weekly and naive baselines | Recursive regressions |
|---|---|---|
| Input day missing | only the horizons that read it fail (`unavailable_feature`) | every horizon of that origin fails (`missing_input`); nothing is imputed |
| Input day missing (exponential smoothing) | the day is skipped (no update) and counted in `n_dropped_rows`; the forecasts are kept. It fails (`missing_input`) only when a day of the first week, used for initialisation, is missing | – |
| Training row reads a missing day | – | the row is dropped and counted (`n_dropped_rows`) |
| Too little history | `insufficient_history` for the affected horizons | `insufficient_history` or `insufficient_training_rows` |
| Rank-deficient or constant design | – | `rank_deficient` / `constant_column` (modernised model); accepted and flagged (legacy) |
| Target day has no observation | prediction kept, status `missing_actual` | same |
| Target after the snapshot | status `beyond_snapshot` (legacy window only) | same |
| Leakage, wrong dates, negative or NaN output, any other error | run stops (bug) | run stops (bug) |

Every scheduled `(period, model, origin, horizon)` has exactly one record; the engine checks
the count. `legacy-v0` has no missing days, so this backtest has no failures.

## 5. Evaluation

**Metrics** (`atlas.metrics`), with `e = prediction − actual`: MAE; RMSE; bias = mean `e`
(positive = over-forecast) with a 95 % HAC interval; bias % = `Σe / Σactual`; WAPE =
`Σ|e| / Σ|actual|`; MASE = mean `|e| / scale`, where `scale` is the in-sample MAE of the
one-step weekly seasonal naive over all data through the origin (so MASE < 1 is *not* skill
against seasonal naive); MAPE as a secondary metric. Reported per horizon, per bucket (day 1,
days 2-7, 8-14, 15-30) and over days 1-30, with the number of origins, forecasts, failures
and missing actuals.

**Paired comparisons** use only forecasts that *both* models produced: the improvement
`100 · (1 − MAE_model / MAE_reference)`, the MAE difference with a 95 % HAC interval, and a
Diebold-Mariano test, against two references:

- `seasonal_naive` (required by the brief); and
- **B\***, the best baseline of the development period, fixed there and reused unchanged in
  the confirmation period. In this backtest seasonal naive is one of the weakest baselines
  (section 6.3), so B\* is the more demanding comparator.

**Dependence.** Forecasts from neighbouring origins share target days: the 14,580
development forecasts of one model are not 14,580 independent observations. Each test uses
one loss differential per origin (the mean over the bucket's horizons, only for origins
with every horizon present), the Harvey-Leybourne-Newbold statistic (rectangular kernel to
`h_max − 1`, Bartlett fallback, small-sample factor, Student t with `T − 1` degrees of
freedom), a Bartlett sensitivity with bandwidth `2·h_max`, and reports the larger p-value.
The 95 % intervals (of the MAE difference and of the bias) use the larger of the two
standard errors, so an interval excludes zero exactly when the reported p-value is below
0.05.\* It is not computed for fewer than `max(3·h_max, 50)` origins or on a grid with
gaps. The HAC effective sample size (`T·γ₀ / long-run variance`, at most `T`) shows how
many independent observations a comparison is worth. p-values are indicative: they assume a
covariance-stationary loss differential, and with expanding windows they compare forecasting
*methods* rather than population models. Truncating at `h_max − 1` assumes that losses on
different target days are uncorrelated; regimes lasting months violate that, so a
bandwidth sensitivity is reported for the selection and primary tests (section 6.2).\*

\* Amended after the independent review (section 9): the intervals previously used the HLN
standard error alone and could exclude zero while the printed conservative p-value was
above 0.05; the bandwidth sensitivity is new. Neither changes a forecast, the selection or
the recommendation.

**M8 erratum (section 12).** "Conservative p" in this document, in the code
(`p_value_conservative`) and in the results means the larger of the HLN and Bartlett `2h`
p-values. It is conservative only relative to the HLN p-value alone, and it is not
conservative in size. With `h = 30` and 336-486 origins the HLN statistic with a
`t(T − 1)` reference over-rejects under its own maintained assumption (losses on different
target days uncorrelated): the pre-registered rule rejects 7.6 % (486 origins) and 9.1 %
(336 origins) of simulated null series at a nominal 5 %, and its Bartlett leg almost never
binds. The wider bandwidths of section 6.2, with the same reference, reject 11.0-17.6 %.
The formulas are correct; with 3,000 origins the same rule rejects 5.5 %. The selection,
H1 and H2 statements below carry M8 notes where this changes them.

**Selection** (development period only; rule fixed before any run):

1. candidates: the five baselines and `atlas_linear_regression`. The legacy model is
   evaluated and compared but never recommended: its coefficients are not identifiable;
2. a candidate with any failure in the development period is ineligible;
3. candidates are ranked by MAE over horizons 1-30 on the forecasts all eligible candidates
   produced;
4. B\* = best baseline, R\* = best regression. R\* is selected only if its MAE is at least
   3 % below B\*'s **and** the development DM test (conservative p-value) is below 0.05 with
   R\* ahead. Otherwise B\* is selected.

The development run writes `selection.json`. Running any other period requires it (same
configuration and data fingerprints, valid structure), and such a run must include the
development period again: the selection recomputed there must equal the sealed one in
every decision field (rule, eligibility, ranking and its MAEs, B\*, R\*, the test, margin,
significance and choice), or the run is marked incomplete and writes no recommendation.
The period rules (one selection and one confirmation period, disjoint targets) are checked
for the whole configuration, whichever periods a run names. `atlas forecast` accepts a
recommendation only inside the complete run directory that wrote it. These checks were
tightened after the independent review (section 9).

**Recommendation** (confirmation period, rule fixed in advance): a selected regression stays
recommended only if its confirmation MAE on common forecasts is not above B\*'s; otherwise B\*
is recommended. A selected baseline stays recommended. The confirmation period never picks a
new model.

**Primary confirmatory tests** (Holm-adjusted): H1, R\* versus B\*; H2, the selected model
versus seasonal naive; both on the confirmation period, horizons 1-30. Every other p-value
is exploratory and unadjusted; development p-values are optimistic because the choice was
made on those data.

**Pre-registered secondary analyses** (descriptive; they never select): target days covered
by a warning-level M1 data-quality flag removed; legacy identification boundary (origins
2024-12-02..2025-01-31) removed; holiday-related targets versus other days; targets
reachable at every horizon (one common set of days for all horizons); MAE by origin month;
the ten target days contributing most to each model-versus-B\* difference; regression
metrics with and without non-estimable or high-leverage forecasts (paired with B\* on the
same forecasts since the review); and a one-day reporting delay
(`configs/backtest/atlas-m2-rolling-origin-v1-delay1.json`, development only).

**Observed-input decomposition** (diagnostic only). At every origin, the fitted regression
is also applied to the *observed* previous days instead of its own predictions: the legacy
holdout's procedure, with coefficients `h − 1` days old. It reads actuals after the origin,
so it is stored separately (`observed_inputs.csv`, record type
`diagnostic_observed_inputs`) and never used to compare or choose. At `h = 1` the engine
asserts that it equals the recursive forecast.

## 6. Results

Periods: development 486 origins (2024-02-01..2025-05-31), confirmation 336 origins
(2025-06-30..2026-05-31), each with horizons 1-30: 102,060 and 70,560 records; 0 failures,
0 missing actuals. Reference run: `docs/results/atlas-m2-rolling-origin-v1/full/`
(result fingerprint `d53bc91196296d43e6ca43aac859f5e9d7ea9dd88ac8fdb4c30fdcab39315a8a`).

### 6.1 Selection on the development period

| rank | candidate | family | MAE days 1-30 (development) |
|---|---:|---:|---:|
| 1 | ATLAS regression | regression | 817.9 |
| 2 | same-weekday median 5w (B*) | baseline | 953.0 |
| 3 | exp. smoothing | baseline | 989.1 |
| 4 | same-weekday mean 4w | baseline | 990.9 |
| 5 | seasonal naive (SN) | baseline | 1,129.1 |
| 6 | naive | baseline | 1,272.3 |

The best regression beats the best baseline by 14.2 % (MAE difference −135.1, 95 % HAC
interval −266.4 to −3.8; DM p 0.044 HLN, 0.018 Bartlett; conservative p 0.044; HAC
effective sample size 27). Both conditions of the rule hold, so `atlas_linear_regression` is
selected. This development p-value is optimistic, because the selection was made on the
same data; it does not depend on the HAC bandwidth (p 0.002-0.044 across the
bandwidths of section 6.2). **M8 erratum (section 12):** that holds for the nominal
p-values only. With 486 origins the size-corrected 5 % critical value of the pre-registered
rule is 2.21, not 1.965; the selection statistic, |t| = 2.022, has a size-corrected p of
0.069, and 0.035-0.069 across the five bandwidths, each corrected for its own size. With a
correctly sized test the gate would most likely not have been passed, and step 4 of the
rule would have selected B\*. The sealed decision stands (section 12). The record was
sealed (`development/selection.json`, commit `10343e2`) before any confirmation-period run
of the protocol; the confirmation-period computations made earlier, during design review
and by the test suite, are listed in section 9.

### 6.2 Confirmation: recommendation and primary tests

| test | comparison | forecasts | MAE difference [95 % CI] | improvement | DM p (HLN) | DM p (Bartlett 2h) | Holm p (conservative) | eff. n |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| H1 | ATLAS regression vs same-weekday median 5w (B*) | 10,080 | -127.3 [-256.4, 1.9] | 11.4 % | 0.054 | 0.047 | 0.054 | 13.8 |
| H2 | ATLAS regression vs seasonal naive (SN) | 10,080 | -386.6 [-645.0, -128.2] | 28.2 % | 0.003 | 0.003 | 0.007 | 21.2 |

The selected regression's confirmation MAE (984.9) is below B\*'s (1,112.2), so the
pre-registered rule keeps it: **recommended model `atlas_linear_regression`**. H2 is
significant after Holm adjustment; H1 is not (0.054). Re-running the development period in
the reference run reproduced the sealed selection in every decision field.

**Bandwidth sensitivity (post hoc, added after the independent review).** The primary
variance truncates at lag `h − 1`, which assumes losses on different target days are
uncorrelated. The H1 loss differential stays autocorrelated far beyond lag 29 (its sign
follows month-long regimes, section 6.11), so the primary interval is at the optimistic
end. The same statistics under five bandwidths, the primary one first (HLN factor and
`t(T − 1)` throughout):

| test | period | comparison | HAC kernel, bandwidth | p | CI half-width | eff. n |
|---|---:|---:|---:|---:|---:|---:|
| selection | development | ATLAS regression vs same-weekday median 5w (B*) | rectangular 29 | 0.044 | 131.3 | 19.6 |
| selection | development | ATLAS regression vs same-weekday median 5w (B*) | rectangular 59 | 0.002 | 85.5 | 46.2 |
| selection | development | ATLAS regression vs same-weekday median 5w (B*) | bartlett 60 | 0.018 | 111.4 | 27.2 |
| selection | development | ATLAS regression vs same-weekday median 5w (B*) | bartlett 90 | 0.006 | 96.7 | 36.1 |
| selection | development | ATLAS regression vs same-weekday median 5w (B*) | bartlett 120 | 0.002 | 86.1 | 45.6 |
| H1 | confirmation | ATLAS regression vs same-weekday median 5w (B*) | rectangular 29 | 0.054 | 129.2 | 13.0 |
| H1 | confirmation | ATLAS regression vs same-weekday median 5w (B*) | rectangular 59 | 0.096 | 150.1 | 9.6 |
| H1 | confirmation | ATLAS regression vs same-weekday median 5w (B*) | bartlett 60 | 0.047 | 125.3 | 13.8 |
| H1 | confirmation | ATLAS regression vs same-weekday median 5w (B*) | bartlett 90 | 0.071 | 138.1 | 11.4 |
| H1 | confirmation | ATLAS regression vs same-weekday median 5w (B*) | bartlett 120 | 0.091 | 147.8 | 9.9 |
| H2 | confirmation | ATLAS regression vs seasonal naive (SN) | rectangular 29 | 0.003 | 258.4 | 20.8 |
| H2 | confirmation | ATLAS regression vs seasonal naive (SN) | rectangular 59 | 0.018 | 319.1 | 13.6 |
| H2 | confirmation | ATLAS regression vs seasonal naive (SN) | bartlett 60 | 0.003 | 255.9 | 21.2 |
| H2 | confirmation | ATLAS regression vs seasonal naive (SN) | bartlett 90 | 0.007 | 282.0 | 17.4 |
| H2 | confirmation | ATLAS regression vs seasonal naive (SN) | bartlett 120 | 0.010 | 293.6 | 16.1 |

For H1 the p-value ranges from 0.047 to 0.096. Only the Bartlett `2h`
estimate, the pre-registered sensitivity, falls below 0.05, and the pre-registered
conservative rule already takes the larger of it and the primary p-value
(0.054); every wider bandwidth gives a larger p-value. H2 stays significant
(p at most 0.018, below 0.025 before and so below 0.05 after Holm adjustment).
The development selection test becomes more, not less, significant under wider bandwidths
(0.002-0.044). These numbers decide nothing; the selection and the recommendation use
the pre-registered test.

**M8 erratum (section 12).** The p-values in this table are nominal, and every bandwidth
over-rejects with the `t(T − 1)` reference, the wider ones most (11.0-17.6 % at a nominal
5 %). Corrected for its own size, each bandwidth gives:

| test | rectangular 29 | rectangular 59 | Bartlett 60 | Bartlett 90 | Bartlett 120 |
|---|---:|---:|---:|---:|---:|
| selection (development, 486 origins) | 0.069 | 0.042 | 0.060 | 0.042 | 0.035 |
| H1 (confirmation, 336 origins) | 0.096 | 0.213 | 0.123 | 0.179 | 0.233 |
| H2 (confirmation, 336 origins) | 0.023 | 0.113 | 0.036 | 0.066 | 0.094 |

The rectangular 29 column is the HLN p-value alone; the pre-registered rule (the larger of
the HLN and Bartlett 60 p-values) gives 0.069, 0.095 and 0.022 (section 12). The two differ
by at most 0.001, because the Bartlett leg almost never binds under the null. Under
rectangular 59, 1.78 % (486 origins) and 5.35 % (336 origins) of the simulated series give
no p-value, because their long-run variance estimate is not positive; they count as
non-rejections (section 12).

So H2 does not stay significant under every bandwidth: it is above 0.05 under rectangular
59, Bartlett 90 and Bartlett 120 even before the Holm adjustment. The selection test's
size-corrected p-value is above 0.05 under the pre-registered bandwidth and under Bartlett
60, and below it (0.035-0.042) under the three widest; wider bandwidths still give the
smaller p-values, but the selection is not robustly significant. H1 is not significant
under any bandwidth, which reinforces its disclosed result.

### 6.3 Accuracy by horizon bucket (MAE, requests per day)

Development:

| model | day 1 | days 2-7 | days 8-14 | days 15-30 | days 1-30 | 1-30 vs SN | 1-30 vs B* |
|---|---:|---:|---:|---:|---:|---:|---:|
| naive | 900.7 | 1,219.9 | 1,260.1 | 1,320.5 | 1,272.3 | -12.7 % | -33.5 % |
| seasonal naive (SN) | 1,052.8 | 1,054.2 | 1,099.2 | 1,175.1 | 1,129.1 | ref | -18.5 % |
| same-weekday mean 4w | 917.7 | 917.6 | 982.7 | 1,026.6 | 990.9 | +12.2 % | -4.0 % |
| same-weekday median 5w (B*) | 912.1 | 912.4 | 954.1 | 970.2 | 953.0 | +15.6 % | B* |
| exp. smoothing | 768.0 | 923.9 | 948.8 | 1,045.1 | 989.1 | +12.4 % | -3.8 % |
| legacy regression | 736.0 | 953.5 | 1,055.3 | 1,088.4 | 1,041.9 | +7.7 % | -9.3 % |
| ATLAS regression | 663.9 | 806.3 | 819.5 | 831.1 | 817.9 | +27.6 % | +14.2 % |

Confirmation:

| model | day 1 | days 2-7 | days 8-14 | days 15-30 | days 1-30 | 1-30 vs SN | 1-30 vs B* |
|---|---:|---:|---:|---:|---:|---:|---:|
| naive | 969.7 | 1,351.5 | 1,512.1 | 1,627.0 | 1,523.2 | -11.1 % | -37.0 % |
| seasonal naive (SN) | 1,225.7 | 1,205.4 | 1,354.8 | 1,450.3 | 1,371.5 | ref | -23.3 % |
| same-weekday mean 4w | 1,101.7 | 1,099.7 | 1,161.2 | 1,198.5 | 1,166.8 | +14.9 % | -4.9 % |
| same-weekday median 5w (B*) | 1,060.6 | 1,063.9 | 1,117.8 | 1,131.0 | 1,112.2 | +18.9 % | B* |
| exp. smoothing | 831.5 | 1,083.4 | 1,256.9 | 1,391.4 | 1,279.8 | +6.7 % | -15.1 % |
| legacy regression | 724.5 | 970.7 | 1,052.3 | 1,094.1 | 1,047.3 | +23.6 % | +5.8 % |
| ATLAS regression | 694.6 | 929.8 | 984.0 | 1,024.2 | 984.9 | +28.2 % | +11.4 % |

`vs SN` and `vs B*` compare the same forecasts (positive = better than the comparator).

### 6.4 Every metric, confirmation period

| model | bucket | origins | forecasts | MAE | RMSE | bias [95 % CI] | WAPE % | MASE | MAPE % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| seasonal naive (SN) | day 1 | 336 | 336 | 1,225.7 | 2,001.7 | -6.9 [-316.3, 302.5] | 11.54 | 1.268 | 11.16 |
| seasonal naive (SN) | days 2-7 | 336 | 2,016 | 1,205.4 | 1,983.3 | -22.5 [-375.8, 330.7] | 11.34 | 1.250 | 10.96 |
| seasonal naive (SN) | days 8-14 | 336 | 2,352 | 1,354.8 | 2,126.2 | -47.3 [-536.0, 441.3] | 12.72 | 1.413 | 12.45 |
| seasonal naive (SN) | days 15-30 | 336 | 5,376 | 1,450.3 | 2,192.5 | -74.2 [-507.0, 358.6] | 13.58 | 1.511 | 13.23 |
| seasonal naive (SN) | days 1-30 | 336 | 10,080 | 1,371.5 | 2,130.4 | -55.4 [-379.3, 268.6] | 12.86 | 1.428 | 12.53 |
| same-weekday median 5w (B*) | day 1 | 336 | 336 | 1,060.6 | 1,715.0 | -174.8 [-449.3, 99.8] | 9.99 | 1.099 | 9.58 |
| same-weekday median 5w (B*) | days 2-7 | 336 | 2,016 | 1,063.9 | 1,716.3 | -178.2 [-622.6, 266.1] | 10.01 | 1.106 | 9.61 |
| same-weekday median 5w (B*) | days 8-14 | 336 | 2,352 | 1,117.8 | 1,746.6 | -204.8 [-764.3, 354.7] | 10.49 | 1.165 | 10.06 |
| same-weekday median 5w (B*) | days 15-30 | 336 | 5,376 | 1,131.0 | 1,725.1 | -232.3 [-815.0, 350.4] | 10.59 | 1.179 | 10.12 |
| same-weekday median 5w (B*) | days 1-30 | 336 | 10,080 | 1,112.2 | 1,728.0 | -213.2 [-712.8, 286.4] | 10.43 | 1.158 | 9.99 |
| legacy regression | day 1 | 336 | 336 | 724.5 | 1,177.4 | -80.3 [-221.8, 61.2] | 6.82 | 0.754 | 6.55 |
| legacy regression | days 2-7 | 336 | 2,016 | 970.7 | 1,579.9 | -188.6 [-552.8, 175.5] | 9.13 | 1.010 | 8.69 |
| legacy regression | days 8-14 | 336 | 2,352 | 1,052.3 | 1,694.9 | -289.7 [-846.9, 267.4] | 9.88 | 1.099 | 9.36 |
| legacy regression | days 15-30 | 336 | 5,376 | 1,094.1 | 1,727.4 | -349.8 [-1,024.5, 324.9] | 10.24 | 1.144 | 9.69 |
| legacy regression | days 1-30 | 336 | 10,080 | 1,047.3 | 1,675.5 | -294.5 [-885.1, 296.0] | 9.82 | 1.093 | 9.31 |
| ATLAS regression | day 1 | 336 | 336 | 694.6 | 1,145.6 | -74.5 [-209.0, 60.1] | 6.54 | 0.724 | 6.23 |
| ATLAS regression | days 2-7 | 336 | 2,016 | 929.8 | 1,544.4 | -177.8 [-542.4, 186.8] | 8.75 | 0.969 | 8.26 |
| ATLAS regression | days 8-14 | 336 | 2,352 | 984.0 | 1,626.4 | -253.1 [-779.1, 272.9] | 9.23 | 1.029 | 8.69 |
| ATLAS regression | days 15-30 | 336 | 5,376 | 1,024.2 | 1,657.4 | -297.8 [-934.8, 339.2] | 9.59 | 1.073 | 9.03 |
| ATLAS regression | days 1-30 | 336 | 10,080 | 984.9 | 1,613.4 | -255.9 [-821.6, 309.7] | 9.24 | 1.030 | 8.70 |

The development table and every other model are in
[`report.md`](results/atlas-m2-rolling-origin-v1/full/report.md). Bias is the mean of
prediction minus actual: negative values mean under-forecasting, and every model
under-forecast the confirmation year on average, which grew faster than its history
suggested. MASE is scaled by the in-sample one-step weekly seasonal naive error, so values
above 1 do not mean "worse than seasonal naive" (see the improvement columns instead).

### 6.5 Against seasonal naive and against B\* (confirmation, common forecasts)

| model | bucket | common forecasts | MAE improvement | MAE difference [95 % CI] | DM p | eff. n |
|---|---:|---:|---:|---:|---:|---:|
| naive | day 1 | 336 | 20.9 % | -256.0 [-453.9, -58.1] | 0.011 | 232.0 |
| naive | days 2-7 | 2,016 | -12.1 % | 146.1 [29.3, 262.9] | 0.014 | 270.5 |
| naive | days 8-14 | 2,352 | -11.6 % | 157.3 [29.6, 285.0] | 0.016 | 242.2 |
| naive | days 15-30 | 5,376 | -12.2 % | 176.7 [62.9, 290.6] | 0.002 | 336.0 |
| naive | days 1-30 | 10,080 | -11.1 % | 151.7 [37.7, 265.6] | 0.009 | 336.0 |
| same-weekday mean 4w | day 1 | 336 | 10.1 % | -123.9 [-283.2, 35.3] | 0.127 | 204.7 |
| same-weekday mean 4w | days 2-7 | 2,016 | 8.8 % | -105.6 [-242.1, 30.9] | 0.129 | 87.5 |
| same-weekday mean 4w | days 8-14 | 2,352 | 14.3 % | -193.6 [-349.9, -37.4] | 0.015 | 78.6 |
| same-weekday mean 4w | days 15-30 | 5,376 | 17.4 % | -251.8 [-465.5, -38.1] | 0.021 | 33.7 |
| same-weekday mean 4w | days 1-30 | 10,080 | 14.9 % | -204.7 [-348.5, -61.0] | 0.005 | 57.0 |
| same-weekday median 5w (B*) | day 1 | 336 | 13.5 % | -165.1 [-322.1, -8.0] | 0.039 | 226.7 |
| same-weekday median 5w (B*) | days 2-7 | 2,016 | 11.7 % | -141.4 [-265.1, -17.7] | 0.025 | 83.4 |
| same-weekday median 5w (B*) | days 8-14 | 2,352 | 17.5 % | -237.1 [-403.7, -70.4] | 0.005 | 65.4 |
| same-weekday median 5w (B*) | days 15-30 | 5,376 | 22.0 % | -319.2 [-582.0, -56.5] | 0.017 | 23.1 |
| same-weekday median 5w (B*) | days 1-30 | 10,080 | 18.9 % | -259.4 [-444.7, -74.0] | 0.006 | 34.0 |
| exp. smoothing | day 1 | 336 | 32.2 % | -394.2 [-566.4, -222.0] | <0.001 | 206.3 |
| exp. smoothing | days 2-7 | 2,016 | 10.1 % | -121.9 [-195.9, -47.9] | 0.001 | 176.5 |
| exp. smoothing | days 8-14 | 2,352 | 7.2 % | -97.9 [-167.7, -28.1] | 0.006 | 217.2 |
| exp. smoothing | days 15-30 | 5,376 | 4.1 % | -58.9 [-118.3, 0.5] | 0.052 | 336.0 |
| exp. smoothing | days 1-30 | 10,080 | 6.7 % | -91.8 [-147.0, -36.6] | 0.001 | 336.0 |
| legacy regression | day 1 | 336 | 40.9 % | -501.2 [-678.4, -323.9] | <0.001 | 201.2 |
| legacy regression | days 2-7 | 2,016 | 19.5 % | -234.7 [-367.0, -102.4] | <0.001 | 96.8 |
| legacy regression | days 8-14 | 2,352 | 22.3 % | -302.5 [-493.2, -111.9] | 0.002 | 63.9 |
| legacy regression | days 15-30 | 5,376 | 24.6 % | -356.2 [-580.7, -131.7] | 0.002 | 28.1 |
| legacy regression | days 1-30 | 10,080 | 23.6 % | -324.2 [-529.8, -118.6] | 0.002 | 27.3 |
| ATLAS regression | day 1 | 336 | 43.3 % | -531.1 [-710.5, -351.7] | <0.001 | 200.6 |
| ATLAS regression | days 2-7 | 2,016 | 22.9 % | -275.6 [-411.3, -140.0] | <0.001 | 88.3 |
| ATLAS regression | days 8-14 | 2,352 | 27.4 % | -370.9 [-583.9, -157.9] | <0.001 | 44.9 |
| ATLAS regression | days 15-30 | 5,376 | 29.4 % | -426.1 [-706.4, -145.8] | 0.003 | 22.5 |
| ATLAS regression | days 1-30 | 10,080 | 28.2 % | -386.6 [-645.0, -128.2] | 0.003 | 21.2 |

| model | bucket | common forecasts | MAE improvement | MAE difference [95 % CI] | DM p | eff. n |
|---|---:|---:|---:|---:|---:|---:|
| naive | day 1 | 336 | 8.6 % | -90.9 [-264.4, 82.5] | 0.303 | 221.1 |
| naive | days 2-7 | 2,016 | -27.0 % | 287.6 [104.4, 470.7] | 0.002 | 138.5 |
| naive | days 8-14 | 2,352 | -35.3 % | 394.4 [218.4, 570.3] | <0.001 | 142.2 |
| naive | days 15-30 | 5,376 | -43.9 % | 496.0 [182.5, 809.4] | 0.002 | 57.5 |
| naive | days 1-30 | 10,080 | -37.0 % | 411.0 [197.2, 624.9] | <0.001 | 101.1 |
| seasonal naive (SN) | day 1 | 336 | -15.6 % | 165.1 [8.0, 322.1] | 0.039 | 226.7 |
| seasonal naive (SN) | days 2-7 | 2,016 | -13.3 % | 141.4 [17.7, 265.1] | 0.025 | 83.4 |
| seasonal naive (SN) | days 8-14 | 2,352 | -21.2 % | 237.1 [70.4, 403.7] | 0.005 | 65.4 |
| seasonal naive (SN) | days 15-30 | 5,376 | -28.2 % | 319.2 [56.5, 582.0] | 0.017 | 23.1 |
| seasonal naive (SN) | days 1-30 | 10,080 | -23.3 % | 259.4 [74.0, 444.7] | 0.006 | 34.0 |
| same-weekday mean 4w | day 1 | 336 | -3.9 % | 41.1 [-24.9, 107.2] | 0.222 | 237.3 |
| same-weekday mean 4w | days 2-7 | 2,016 | -3.4 % | 35.8 [-49.7, 121.3] | 0.411 | 57.8 |
| same-weekday mean 4w | days 8-14 | 2,352 | -3.9 % | 43.4 [-15.5, 102.4] | 0.148 | 76.2 |
| same-weekday mean 4w | days 15-30 | 5,376 | -6.0 % | 67.4 [-7.0, 141.9] | 0.076 | 29.6 |
| same-weekday mean 4w | days 1-30 | 10,080 | -4.9 % | 54.6 [-4.4, 113.7] | 0.070 | 39.4 |
| exp. smoothing | day 1 | 336 | 21.6 % | -229.1 [-384.3, -73.9] | 0.004 | 173.8 |
| exp. smoothing | days 2-7 | 2,016 | -1.8 % | 19.5 [-126.8, 165.8] | 0.793 | 100.8 |
| exp. smoothing | days 8-14 | 2,352 | -12.4 % | 139.2 [-9.9, 288.2] | 0.067 | 82.7 |
| exp. smoothing | days 15-30 | 5,376 | -23.0 % | 260.3 [-12.3, 533.0] | 0.061 | 33.1 |
| exp. smoothing | days 1-30 | 10,080 | -15.1 % | 167.6 [-4.3, 339.5] | 0.056 | 55.6 |
| legacy regression | day 1 | 336 | 31.7 % | -336.1 [-481.9, -190.3] | <0.001 | 168.9 |
| legacy regression | days 2-7 | 2,016 | 8.8 % | -93.2 [-225.3, 38.8] | 0.166 | 68.7 |
| legacy regression | days 8-14 | 2,352 | 5.9 % | -65.5 [-184.0, 53.1] | 0.278 | 50.9 |
| legacy regression | days 15-30 | 5,376 | 3.3 % | -37.0 [-202.7, 128.7] | 0.661 | 22.8 |
| legacy regression | days 1-30 | 10,080 | 5.8 % | -64.9 [-177.0, 47.3] | 0.256 | 21.4 |
| ATLAS regression | day 1 | 336 | 34.5 % | -366.0 [-513.8, -218.2] | <0.001 | 174.3 |
| ATLAS regression | days 2-7 | 2,016 | 12.6 % | -134.2 [-249.8, -18.6] | 0.023 | 63.1 |
| ATLAS regression | days 8-14 | 2,352 | 12.0 % | -133.8 [-243.5, -24.2] | 0.017 | 33.4 |
| ATLAS regression | days 15-30 | 5,376 | 9.4 % | -106.9 [-242.9, 29.2] | 0.123 | 20.4 |
| ATLAS regression | days 1-30 | 10,080 | 11.4 % | -127.3 [-256.4, 1.9] | 0.054 | 13.8 |

These p-values are exploratory and unadjusted, except for the two primary tests above.
The interval uses the larger of the two HAC standard errors, so it excludes zero exactly
when the p-value is below 0.05. `eff. n` is the HAC effective sample size (Bartlett
bandwidth `2h`, capped at the number of origins): how many independent observations the
dependent per-origin differences are worth. Wider bandwidths give smaller values (section
6.2).

### 6.6 Accuracy by horizon (MAE)

Development:

| h | naive | seasonal naive (SN) | same-weekday mean 4w | same-weekday median 5w (B*) | exp. smoothing | legacy regression | ATLAS regression |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 900.7 | 1,052.8 | 917.7 | 912.1 | 768.0 | 736.0 | 663.9 |
| 2 | 1,233.1 | 1,052.8 | 916.4 | 910.9 | 876.6 | 876.5 | 777.7 |
| 3 | 1,273.8 | 1,052.0 | 915.8 | 910.2 | 924.7 | 931.2 | 806.6 |
| 7 | 1,054.8 | 1,054.8 | 917.9 | 913.4 | 941.3 | 1,002.4 | 811.8 |
| 8 | 1,225.0 | 1,099.0 | 984.0 | 954.4 | 972.7 | 1,022.7 | 816.4 |
| 14 | 1,097.4 | 1,097.4 | 980.3 | 952.5 | 951.2 | 1,064.7 | 820.2 |
| 15 | 1,205.0 | 1,148.8 | 1,019.5 | 951.6 | 981.5 | 1,075.5 | 824.9 |
| 21 | 1,142.0 | 1,142.0 | 1,009.3 | 944.5 | 997.9 | 1,094.3 | 828.7 |
| 28 | 1,190.6 | 1,190.6 | 1,031.0 | 983.7 | 1,066.7 | 1,081.2 | 833.6 |
| 30 | 1,439.1 | 1,239.5 | 1,065.1 | 1,013.8 | 1,133.9 | 1,072.7 | 835.7 |

Confirmation:

| h | naive | seasonal naive (SN) | same-weekday mean 4w | same-weekday median 5w (B*) | exp. smoothing | legacy regression | ATLAS regression |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 969.7 | 1,225.7 | 1,101.7 | 1,060.6 | 831.5 | 724.5 | 694.6 |
| 2 | 1,335.1 | 1,216.0 | 1,100.2 | 1,060.3 | 988.7 | 876.8 | 855.3 |
| 3 | 1,372.2 | 1,208.1 | 1,098.9 | 1,062.4 | 1,061.4 | 950.0 | 913.9 |
| 7 | 1,200.7 | 1,200.7 | 1,103.4 | 1,070.4 | 1,135.4 | 1,016.0 | 961.0 |
| 8 | 1,431.6 | 1,369.8 | 1,165.0 | 1,115.6 | 1,213.1 | 1,030.0 | 967.9 |
| 14 | 1,352.7 | 1,352.7 | 1,160.8 | 1,122.1 | 1,263.0 | 1,070.4 | 996.9 |
| 15 | 1,506.6 | 1,491.2 | 1,196.8 | 1,133.1 | 1,319.4 | 1,079.6 | 1,000.3 |
| 21 | 1,484.6 | 1,484.6 | 1,207.6 | 1,141.9 | 1,384.1 | 1,102.6 | 1,022.7 |
| 28 | 1,412.7 | 1,412.7 | 1,190.1 | 1,129.8 | 1,343.2 | 1,089.8 | 1,032.8 |
| 30 | 1,740.9 | 1,451.8 | 1,218.5 | 1,138.0 | 1,439.2 | 1,086.1 | 1,033.1 |

The weekly baselines are flat within each week of horizons and step at 8, 15, 22 and 29,
where the most recent observed same weekday moves a week further back. Naive is good only
at day 1.

### 6.7 Where a regression does worse than a baseline

Every bucket and decision period in which a regression's MAE is higher than a baseline's on
the same forecasts:

| period | bucket | regression | baseline | MAE | baseline MAE | difference | DM p |
|---|---:|---:|---:|---:|---:|---:|---:|
| development | days 2-7 | legacy regression | same-weekday mean 4w | 953.5 | 917.6 | -3.9 % | 0.474 |
| development | days 8-14 | legacy regression | same-weekday mean 4w | 1,055.3 | 982.7 | -7.4 % | 0.208 |
| development | days 15-30 | legacy regression | same-weekday mean 4w | 1,088.4 | 1,026.6 | -6.0 % | 0.357 |
| development | days 1-30 | legacy regression | same-weekday mean 4w | 1,041.9 | 990.9 | -5.1 % | 0.371 |
| development | days 2-7 | legacy regression | same-weekday median 5w (B*) | 953.5 | 912.4 | -4.5 % | 0.386 |
| development | days 8-14 | legacy regression | same-weekday median 5w (B*) | 1,055.3 | 954.1 | -10.6 % | 0.064 |
| development | days 15-30 | legacy regression | same-weekday median 5w (B*) | 1,088.4 | 970.2 | -12.2 % | 0.094 |
| development | days 1-30 | legacy regression | same-weekday median 5w (B*) | 1,041.9 | 953.0 | -9.3 % | 0.125 |
| development | days 2-7 | legacy regression | exp. smoothing | 953.5 | 923.9 | -3.2 % | 0.601 |
| development | days 8-14 | legacy regression | exp. smoothing | 1,055.3 | 948.8 | -11.2 % | 0.103 |
| development | days 15-30 | legacy regression | exp. smoothing | 1,088.4 | 1,045.1 | -4.1 % | 0.519 |
| development | days 1-30 | legacy regression | exp. smoothing | 1,041.9 | 989.1 | -5.3 % | 0.416 |

The modernised regression is not worse than any baseline in any bucket of either period.
The faithful legacy regression is worse than three baselines in every development bucket
beyond day 1 (no deficit significant: conservative p 0.064-0.601), and
better than all of them in the confirmation period.

### 6.8 The legacy 4.93 % in context: the same 90 target days

The descriptive `legacy_window` period re-forecasts the 90 legacy holdout days
(2026-04-02..2026-06-30) from 119 origins, so every horizon is scored on exactly those 90
days.

| procedure | h | forecasts | MAE | RMSE | MAPE % |
|---|---:|---:|---:|---:|---:|
| published legacy one-step holdout (single fit, observed lags) | 1 | 90 | 514.30 | 676.70 | 4.93 |
| M2 re-computation of the legacy one-step holdout | 1 | 90 | 514.30 | 676.70 | 4.93 |
| published 7-day seasonal baseline y(t-7) | 1 | 90 | 851.16 | 1,107.24 | 8.23 |
| backtest legacy regression | 1 | 90 | 514.33 | 678.07 | 4.91 |
| backtest legacy regression | 2 | 90 | 582.40 | 784.77 | 5.62 |
| backtest legacy regression | 7 | 90 | 596.99 | 793.17 | 5.76 |
| backtest legacy regression | 14 | 90 | 586.40 | 766.57 | 5.64 |
| backtest legacy regression | 30 | 90 | 576.31 | 762.49 | 5.54 |
| backtest ATLAS regression | 1 | 90 | 493.57 | 653.69 | 4.72 |
| backtest ATLAS regression | 2 | 90 | 551.46 | 744.96 | 5.34 |
| backtest ATLAS regression | 7 | 90 | 549.98 | 754.24 | 5.32 |
| backtest ATLAS regression | 14 | 90 | 550.30 | 743.23 | 5.31 |
| backtest ATLAS regression | 30 | 90 | 546.46 | 733.70 | 5.27 |
| backtest same-weekday median 5w (B*) | 1 | 90 | 719.26 | 967.67 | 6.86 |
| backtest same-weekday median 5w (B*) | 2 | 90 | 719.26 | 967.67 | 6.86 |
| backtest same-weekday median 5w (B*) | 7 | 90 | 719.26 | 967.67 | 6.86 |
| backtest same-weekday median 5w (B*) | 14 | 90 | 785.70 | 1,026.94 | 7.46 |
| backtest same-weekday median 5w (B*) | 30 | 90 | 940.93 | 1,190.17 | 8.91 |
| backtest seasonal naive (SN) | 1 | 90 | 851.16 | 1,107.24 | 8.23 |
| backtest seasonal naive (SN) | 2 | 90 | 851.16 | 1,107.24 | 8.23 |
| backtest seasonal naive (SN) | 7 | 90 | 851.16 | 1,107.24 | 8.23 |
| backtest seasonal naive (SN) | 14 | 90 | 785.57 | 1,052.08 | 7.42 |
| backtest seasonal naive (SN) | 30 | 90 | 1,012.49 | 1,235.00 | 9.61 |

- The engine's seasonal naive reproduces the published 7-day baseline exactly (MAE 851.16,
  RMSE 1,107.24, MAPE 8.23 %) at every horizon from 1 to 7; a test pins this.
- Refitting at every origin barely changes the horizon-1 result (MAPE 4.91 % against 4.93
  %): the difference between the published number and the forecast actually delivered is
  the horizon, not the refit schedule.
- These 90 days are easy compared with the rest of the confirmation year: for the legacy
  model, MAE at horizon 30 is 576 here and 1,086 over all confirmation targets.

### 6.9 What recursion costs (observed-input diagnostic)

| period | model | h | MAE recursive | MAE observed inputs | MAPE % recursive | MAPE % observed inputs |
|---|---:|---:|---:|---:|---:|---:|
| development | legacy regression | 1 | 736.0 | 736.0 | 7.72 | 7.72 |
| development | legacy regression | 7 | 1,002.4 | 798.8 | 10.56 | 8.39 |
| development | legacy regression | 14 | 1,064.7 | 864.4 | 11.15 | 9.06 |
| development | legacy regression | 30 | 1,072.7 | 926.2 | 11.05 | 9.57 |
| development | ATLAS regression | 1 | 663.9 | 663.9 | 6.80 | 6.80 |
| development | ATLAS regression | 7 | 811.8 | 678.6 | 8.23 | 6.92 |
| development | ATLAS regression | 14 | 820.2 | 693.0 | 8.29 | 7.04 |
| development | ATLAS regression | 30 | 835.7 | 720.2 | 8.36 | 7.23 |
| confirmation | legacy regression | 1 | 724.5 | 724.5 | 6.55 | 6.55 |
| confirmation | legacy regression | 7 | 1,016.0 | 721.5 | 9.05 | 6.50 |
| confirmation | legacy regression | 14 | 1,070.4 | 728.5 | 9.53 | 6.55 |
| confirmation | legacy regression | 30 | 1,086.1 | 748.9 | 9.59 | 6.71 |
| confirmation | ATLAS regression | 1 | 694.6 | 694.6 | 6.23 | 6.23 |
| confirmation | ATLAS regression | 7 | 961.0 | 687.6 | 8.50 | 6.14 |
| confirmation | ATLAS regression | 14 | 996.9 | 691.5 | 8.80 | 6.16 |
| confirmation | ATLAS regression | 30 | 1,033.1 | 709.7 | 9.11 | 6.31 |

At horizon 1 the two coincide (the engine asserts this at every origin). Share of the
growth from day 1 to day 30 that comes from recursion, `1 − (obs₃₀ − obs₁) / (rec₃₀ − rec₁)`:

| model | development | confirmation |
|---|---:|---:|
| legacy regression | 43 % (observed-input MAE 736.0 → 926.2) | 93 % (724.5 → 748.9) |
| ATLAS regression | 67 % (663.9 → 720.2) | 96 % (694.6 → 709.7) |

In the confirmation period the observed-input error stays close to the horizon-1 error
and nearly all of the growth comes from feeding predictions back, which the legacy
evaluation never measured. In the development period the observed-input error grows too,
most for the legacy model (26 %): the coefficients are `h − 1` days
old at horizon `h`, and its leap-day artefact (section 6.11) affects whole windows of
targets. Recursion is then responsible for only part of the growth.

### 6.10 Sensitivity analyses (pre-registered)

| analysis | period | model | forecasts | MAE (days 1-30) | vs B* | DM p |
|---|---:|---:|---:|---:|---:|---:|
| exclude_flagged_targets | development | ATLAS regression | 14,235 | 774.0 | +15.0 % | n/a |
| exclude_flagged_targets | development | legacy regression | 14,235 | 1,005.7 | -10.5 % | n/a |
| exclude_flagged_targets | confirmation | ATLAS regression | 9,540 | 857.6 | +12.6 % | n/a |
| exclude_flagged_targets | confirmation | legacy regression | 9,540 | 922.3 | +6.1 % | n/a |
| legacy_identification_boundary | development | ATLAS regression | 12,750 | 699.6 | +12.2 % | n/a |
| legacy_identification_boundary | development | legacy regression | 12,750 | 892.7 | -12.1 % | n/a |
| legacy_identification_boundary | confirmation | ATLAS regression | 10,080 | 984.9 | +11.4 % | 0.054 |
| legacy_identification_boundary | confirmation | legacy regression | 10,080 | 1,047.3 | +5.8 % | 0.256 |
| holiday_related_targets | development | ATLAS regression | 1,170 | 1,036.2 | +25.8 % | n/a |
| holiday_related_targets | development | legacy regression | 1,170 | 1,366.5 | +2.2 % | n/a |
| holiday_related_targets | confirmation | ATLAS regression | 858 | 1,102.7 | +21.9 % | n/a |
| holiday_related_targets | confirmation | legacy regression | 858 | 1,274.3 | +9.7 % | n/a |
| other_targets | development | ATLAS regression | 13,410 | 798.8 | +12.6 % | n/a |
| other_targets | development | legacy regression | 13,410 | 1,013.6 | -10.9 % | n/a |
| other_targets | confirmation | ATLAS regression | 9,222 | 974.0 | +10.2 % | n/a |
| other_targets | confirmation | legacy regression | 9,222 | 1,026.2 | +5.4 % | n/a |
| target_aligned | development | ATLAS regression | 13,710 | 833.3 | +13.7 % | 0.076 |
| target_aligned | development | legacy regression | 13,710 | 1,049.7 | -8.7 % | 0.168 |
| target_aligned | confirmation | ATLAS regression | 9,210 | 1,037.3 | +10.1 % | 0.030 |
| target_aligned | confirmation | legacy regression | 9,210 | 1,106.1 | +4.1 % | 0.396 |

`n/a` p-values: the test is not computed when an exclusion leaves gaps in the origin grid or
incomplete horizon buckets. In every analysis the modernised regression stays ahead of B\*
(10-26 %), and its advantage is largest on holiday-related days. Holidays are not
the only information it has that the baselines lack: its annual Fourier terms and trend
have no counterpart in any baseline either. The faithful legacy model stays behind B\* in
development in every analysis (−12.1 to −8.7 %), including after removing the origins
affected by its leap-day artefact, except on holiday-related targets, where it is
+2.2 % ahead (no test computed).

One-day reporting delay (development period; forecasts issued at `o` see data through
`o − 1`):

| model | day 1, no delay | day 1, 1-day delay | days 1-30, no delay | days 1-30, 1-day delay |
|---|---:|---:|---:|---:|
| naive | 900.7 | 1,233.2 | 1,272.3 | 1,291.3 |
| seasonal naive (SN) | 1,052.8 | 1,052.8 | 1,129.1 | 1,135.0 |
| same-weekday mean 4w | 917.7 | 917.7 | 990.9 | 996.1 |
| same-weekday median 5w (B*) | 912.1 | 912.1 | 953.0 | 956.1 |
| exp. smoothing | 768.0 | 877.4 | 989.1 | 1,000.9 |
| legacy regression | 736.0 | 875.5 | 1,041.9 | 1,053.3 |
| ATLAS regression | 663.9 | 776.1 | 817.9 | 823.0 |

The delay costs the models that rely on the latest days (naive, exponential smoothing, both
regressions) about 110-330 requests per day at horizon 1; the weekly baselines
are unaffected. Over horizons 1-30 the best regression, B\* and the selection do not
change; lower down, the ranking changes (exp. smoothing 3 → 4; same-weekday mean 4w 4 → 3). The
delay configuration's description says it "never selects or recommends"; because it
carries the same selection block, its development run does write a `selection.json`. That
file only documents the ranking under delay: it cannot seal any other run, whose
configuration fingerprint differs.

### 6.11 Legacy diagnostics and accuracy by origin month

| model | period | rank | rank-deficient fits | restricted cond. median / max | max training leverage | non-estimable | high leverage | in-sample MAE (median) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ATLAS regression | confirmation | 18-18 of 18 | 0 | 7.0 / 7.2 | 0.1137 | 0 | 0 | 597.0 |
| ATLAS regression | development | 18-18 of 18 | 0 | 6.7 / 7.3 | 0.2592 | 0 | 0 | 550.3 |
| legacy regression | confirmation | 29-29 of 32 | 336 | 4,612.5 / 5,276.4 | 0.1408 | 0 | 0 | 612.5 |
| legacy regression | development | 28-29 of 32 | 486 | 40.4 / 19,722.9 | 1.0000 | 465 | 69 | 554.3 |

| period | model | forecasts | MAE | non-estimable | high leverage | kept | MAE kept | B* on kept | removed | MAE removed | B* on removed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| confirmation | ATLAS regression | 10,080 | 984.9 | 0 | 0 | 10,080 | 984.9 | 1,112.2 | 0 | n/a | n/a |
| confirmation | legacy regression | 10,080 | 1,047.3 | 0 | 0 | 10,080 | 1,047.3 | 1,112.2 | 0 | n/a | n/a |
| development | ATLAS regression | 14,580 | 817.9 | 0 | 0 | 14,580 | 817.9 | 953.0 | 0 | n/a | n/a |
| development | legacy regression | 14,580 | 1,041.9 | 465 | 69 | 14,046 | 986.2 | 895.2 | 534 | 2,509.5 | 2,472.6 |

| period | origin month | B* MAE | B* bias | legacy MAE | legacy bias | ATLAS MAE | ATLAS bias | ATLAS vs B* |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| development | 2024-02 | 671.3 | 360.2 | 805.0 | 493.1 | 479.6 | 286.5 |  |
| development | 2024-03 | 753.4 | -306.1 | 661.1 | -56.3 | 659.5 | 100.6 |  |
| development | 2024-04 | 675.4 | -193.5 | 793.5 | 429.1 | 619.2 | 232.9 |  |
| development | 2024-05 | 1,045.7 | -812.3 | 857.3 | -226.2 | 643.5 | -350.6 |  |
| development | 2024-06 | 803.9 | -126.9 | 763.3 | -186.5 | 774.1 | -310.6 |  |
| development | 2024-07 | 772.0 | 574.9 | 701.9 | 464.9 | 756.1 | 543.2 |  |
| development | 2024-08 | 764.4 | -339.9 | 801.6 | 217.5 | 809.0 | 108.9 | **worse than B\*** |
| development | 2024-09 | 932.6 | -393.8 | 1,170.3 | 543.6 | 831.6 | -9.6 |  |
| development | 2024-10 | 957.3 | -206.0 | 969.2 | 282.1 | 803.2 | 72.2 |  |
| development | 2024-11 | 1,202.2 | 0.3 | 1,235.9 | -467.0 | 1,087.4 | -155.8 |  |
| development | 2024-12 | 2,075.9 | -1,031.0 | 2,085.8 | -1,511.3 | 2,166.4 | -1,618.6 | **worse than B\*** |
| development | 2025-01 | 1,985.6 | 1,055.3 | 2,056.4 | 1,715.2 | 1,106.5 | -39.3 |  |
| development | 2025-02 | 693.9 | 282.0 | 1,329.4 | 1,294.0 | 452.9 | 192.5 |  |
| development | 2025-03 | 475.0 | 96.3 | 925.6 | 902.7 | 538.7 | 439.0 | **worse than B\*** |
| development | 2025-04 | 637.3 | -346.5 | 749.7 | 567.7 | 624.9 | 305.7 |  |
| development | 2025-05 | 741.8 | -383.3 | 761.7 | 505.0 | 670.8 | 308.6 |  |
| confirmation | 2025-06 | 430.3 | -321.5 | 461.4 | 226.9 | 523.7 | 197.0 | **worse than B\*** |
| confirmation | 2025-07 | 343.1 | -28.7 | 399.9 | 135.5 | 481.3 | 309.1 | **worse than B\*** |
| confirmation | 2025-08 | 390.4 | 187.2 | 651.0 | 510.9 | 539.2 | 356.1 | **worse than B\*** |
| confirmation | 2025-09 | 666.4 | -477.2 | 584.4 | 136.3 | 582.6 | -110.3 |  |
| confirmation | 2025-10 | 1,035.8 | -406.8 | 903.9 | -374.4 | 967.0 | -137.1 |  |
| confirmation | 2025-11 | 1,317.0 | -249.8 | 1,269.8 | -615.3 | 1,265.8 | -168.9 |  |
| confirmation | 2025-12 | 1,708.1 | 780.4 | 1,551.8 | 761.8 | 1,379.6 | 542.6 |  |
| confirmation | 2026-01 | 2,716.1 | -2,365.6 | 2,577.3 | -2,018.3 | 2,494.2 | -2,252.6 |  |
| confirmation | 2026-02 | 1,587.6 | 168.4 | 1,839.7 | -1,616.9 | 1,478.4 | -1,254.2 |  |
| confirmation | 2026-03 | 840.8 | 609.4 | 648.9 | -119.4 | 590.7 | -1.3 |  |
| confirmation | 2026-04 | 639.7 | 318.4 | 487.9 | 13.6 | 490.4 | 105.1 |  |
| confirmation | 2026-05 | 1,034.0 | -832.6 | 675.7 | -185.1 | 607.6 | -295.7 |  |

- Every legacy fit is rank-deficient (by construction). Until 2024-12-31 the rank is 28:
  `trend − 365·(year − 2023) − (day_of_year − 1)` is identically 0, so 465 forecasts of
  2025 dates from December 2024 origins are **not estimable**. From 2025-01-01 the same
  expression becomes a step that is estimated from the first few 2025 days (training
  leverage up to 1.0000; restricted condition number up to 19,723). The legacy forecasts
  then over-predict for months: its mean bias is +1,715 for January 2025 origins,
  +1,294 for February and +903 for March. The modernised
  regression's is −39 and +192 in January and February, but
  +439 in March 2025 (B\*: +96), which is why it loses that
  month.
- Removing the non-estimable and high-leverage forecasts lowers the legacy development MAE
  from 1,041.9 to 986.2, but B\* on exactly the same 14,046
  forecasts scores 895.2: the legacy model is still 10.2 % worse
  (9.3 % on all forecasts). The 534 removed forecasts are hard
  for every model (legacy 2,509.5, B\* 2,472.6), so these diagnostics
  do not explain the legacy model's development deficit. February and March 2025 origins
  contribute 41 % and 32 % of it; no diagnostic removes
  them.
- December and January origins are the hardest for every model (Christmas, New Year and
  winter surges inside the horizon).
- The modernised regression is worse than B\* for origins in 6 of
  28 months:
  - 2024-08, 2024-12, 2025-03 (development), including the December 2024 year-end
    (2,166.4 against 2,075.9);
  - 2025-06, 2025-07, 2025-08 (confirmation), by 22-40 % in calm, low-error months
    (2025-06 holds a single origin).

  Its overall lead is the sum of many months, not of the hardest ones alone: December and
  January origins give 40 % of the confirmation lead; spring 2026 gives
  60 %. It is not uniformly better.

## 7. Leakage prevention

Barriers, each independently sufficient for its kind of leak:

| Barrier | Mechanism | Evidence |
|---|---|---|
| History object ends at the origin | `series.through(o)`; later days are absent from the object | engine time-travel tests (A/B series differing only after day `d`: every record before `d` is identical, expanding and sliding windows, hypothesis) |
| M1 truncation and runtime guard | every feature read is checked against the row's information cutoff | M1 tests; M2 test: a model asking for a leaking row raises `LeakageError`, which stops the backtest |
| Actuals attached last | `_attach_actuals` after every forecast exists | spy model sees only data through the cutoff; positive controls change exactly the expected horizons |
| Only `ForecastError` is recorded | leakage and other bugs stop the run instead of becoming "failed" rows | tests with leaky, raising and contract-breaking models |
| No cached slicing across origins | training rows built per origin from the truncated series | shock test: wild changes after the origin leave the recursive path bit-identical |
| Pre-registration | config and selection rule committed (`a16672f`) before any run; selection sealed (`10343e2`) before any confirmation run of the protocol | git history; period rules checked for the whole configuration; any run beyond the development period must re-run it and reproduce the seal in every decision field (`selection_differs_from_seal`); incomplete runs write no recommendation |

Recursive feedback is proven by:
- a counterfactual-parity test (each step's feature row equals the row built from the actual
  history followed by the fed-back predictions);
- an exact clip-and-round test (raw 2.5 → 2, 3.5 → 4, −4.2 → 0);
- a plain-Python re-implementation of notebook cell 16 that must equal the legacy forecast;
- the 30/30 published-forecast reproduction, which fails if unrounded values are fed back.

The test files are `tests/test_m2_*.py`. They were checked against planted bugs (mutation
testing, before and after the independent review; see the PR description).

## 8. Reproducing the results

```bash
uv sync --locked
uv run atlas backtest legacy                    # 90/90 holdout, 30/30 forecast
uv run atlas backtest run --require-clean --output-dir runs/v1-development
uv run atlas backtest run --require-clean \
    --period development --period confirmation --period legacy_window \
    --sealed-selection docs/results/atlas-m2-rolling-origin-v1/development/selection.json \
    --output-dir runs/v1-full
uv run atlas backtest verify runs/v1-full       # re-run; every file must be identical
uv run atlas backtest run --require-clean \
    --config configs/backtest/atlas-m2-rolling-origin-v1-delay1.json --output-dir runs/v1-delay1
uv run atlas forecast --recommendation runs/v1-full/recommendation.json
```

`verify` recomputes everything from the snapshot with the current code, including the
data-quality flags, and compares every regenerated file (result files, `report.md`,
`config.json`). The committed reference run verifies with the current code:

```bash
uv run atlas backtest verify docs/results/atlas-m2-rolling-origin-v1/full   # about 7 minutes
```

The sealed development run in `docs/results/.../development/` was produced
with the code of commit `a16672f`; the reporting code has changed since (section 9), so it
verifies byte for byte only with that commit's code, while the reference run reproduces its
forecasts and its selection with the current code. A checkout of `a16672f` does not contain
`docs/results/` (the record was committed afterwards, in `10343e2`), so verify the copy in
the current checkout with that commit's code and its own lock file
(**M8 correction**; the earlier recipe pointed at a worktree that lacks the directory):

```bash
git worktree add ../atlas-a16672f a16672f
cd ../atlas-a16672f && uv sync --locked
uv run atlas backtest verify <current checkout>/docs/results/atlas-m2-rolling-origin-v1/development
```

Checked in M8 (2026-10-16, from an export of `a16672f` set up with its own lock file):
identical, result fingerprint `fbb948ec…dcea43`. With the
current code the same directory fails `verify`: its forecasts, fits and observed inputs
reproduce, but `coefficients.csv`, `metrics.json` and `report.md` and therefore the result
fingerprint differ.

| Item | Value |
|---|---|
| Protocol configuration | `configs/backtest/atlas-m2-rolling-origin-v1.json`, fingerprint `78da64e8…1753` |
| Data | `legacy-v0` `daily_total` (SHA-256 `16a338f4…d65c`), data fingerprint `5ab8b3a5…3a4d` |
| Development result fingerprint | `fbb948ec6804f123f2c068d8f8abeac9383d0ca643f5ef086eb9fbe853dcea43` (code commit `a16672f`) |
| Reference (full) result fingerprint | `d53bc91196296d43e6ca43aac859f5e9d7ea9dd88ac8fdb4c30fdcab39315a8a` (code commit `28708bd`) |
| Delay sensitivity result fingerprint | `79d4f7962d2ed022a1b8081028efa84e9a176cd91aa2f9a28c35379f79b7f22d` |
| Run time | about 3 minutes for the development period, 6 for the full protocol (one core) |

Each manifest records the configuration, data and code fingerprints, git commit and
working-tree state, Python, package and BLAS versions, and the SHA-256 of every output.
`docs/results/` keeps the manifests, metrics, reports and selection records. The large
`forecasts.csv` (about 200,000 rows) is not committed: `atlas backtest verify` regenerates
it and checks it against the recorded hash. `spot_check.csv` holds every model's forecasts
at the first and last origin of each period, and a test recomputes them. Results are
byte-identical on one machine. Another BLAS build may differ in the last bits of a
regression prediction; the legacy model rounds to integers, but at the spot-check origins
no raw prediction lies within 0.0015 of a rounding boundary.

## 9. Exposure log

What was computed, on which period, before the protocol was frozen (commit `a16672f`).
Reviewer figures are never quoted as results; the confirmation levels below are quoted only
to document what was seen (they equal the engine's, section 6.3).

| When | Who | What | Period |
|---|---|---|---|
| Prototype | implementer | Legacy reproduction of the published holdout (2026-04-02..06-30) and July 2026 forecast | legacy window (published numbers) |
| Prototype | implementer | 30-day MAE of six models at five single origins (2024-02-01, 2024-12-15, 2025-01-03, 2025-05-31, **2026-05-31**) | four development, **one confirmation** origin |
| Prototype | implementer | Smoke runs on 2024-02..03 origins; timing run on 2024-03-01..20 | development |
| Design review | statistics reviewer | Development-period errors of the baselines. **Confirmation-period MAE and RMSE of the four weekly baselines, B\* included** (naive 1,523.2, seasonal naive 1,371.5, mean 4w 1,166.8, median 5w 1,112.2), and confirmation-period DM statistics for baseline pairs under four HAC estimators (Bartlett without lags, rectangular to `h − 1`, Bartlett `2h` and `3h`), computed for a dependence check. The reviewer's report did not quote the levels, but they were computed and displayed in the session | development; **confirmation (baselines only)** |
| Design review | leakage reviewer | Independent legacy refits at December 2024 to May 2025 origins, and the legacy model's 30-day MAE at **three confirmation origins** (2025-12-15, 2026-01-01, 2026-01-02) | development; three confirmation origins |
| Freeze commit | test suite | A short copy of the protocol run at confirmation origins 2025-07-01..05 (all models, 1,050 forecasts, metrics and a recommendation computed, accuracy neither asserted nor printed when tests pass) and legacy-window origins 2026-06-20..29; seasonal naive on the 90 legacy holdout days | five confirmation origins; legacy window |
| After freeze | protocol | Development run → sealed selection → full run, delay sensitivity, legacy refit, projection | as configured |
| After review | protocol | Full run, delay sensitivity, legacy refit and projection regenerated with the amended reporting code (below) | as configured |

The primary-test variance (HLN rectangular kernel to `h − 1`, a Bartlett `2h` sensitivity
and the conservative p) replaced the originally designed Bartlett estimator after the
statistics reviewer had computed confirmation-period DM statistics for baseline pairs under
these estimators. The change was motivated by development-period arguments; it is listed
here because it was made with that exposure. The conservative rule takes the larger of the
two p-values, which works against significance. (**M8 erratum, section 12:** only relative
to the HLN p-value alone; the Bartlett leg almost never binds, and the rule's size is above
5 % at these sample sizes.)

Changes made to model specifications before the freeze, and why:
- the modernised regression dropped the actual-date holiday indicator (identifiability: two
  distinguishing training days), not because of accuracy;
- exponential smoothing was added on reviewers' advice.

No model specification, selection rule or configuration changed after the freeze.

**Amendments after the independent review** (reporting and enforcement only):
- the 95 % intervals use the larger of the two DM standard errors, so they agree with the
  reported conservative p-value (before, some intervals excluded zero while the printed p
  was above 0.05; the headline intervals are unchanged because the HLN error is the larger
  one there);
- the bandwidth sensitivity of section 6.2 and the paired B\* columns of the reliability
  table were added; exponential-smoothing parameters are now written to `coefficients.csv`;
- the period rules are checked for the whole configuration, every run beyond the
  development period must re-run it and reproduce the seal in every decision field, an
  incomplete run writes no `selection.json` or `recommendation.json`, and `atlas forecast`
  accepts a recommendation only from its complete run;
- `verify` recomputes the data-quality flags and compares every regenerated file;
- several statements in this document were corrected (legacy MAPE ranges, the role of the
  legacy diagnostics, the observed-input decomposition, the sources of the modernised
  regression's lead, the delay ranking, this exposure log).

The regenerated reference run reproduces the earlier one exactly where these changes do
not reach: `forecasts.csv`, `fits.csv` and `observed_inputs.csv` are byte-identical
(SHA-256 `forecasts.csv` `0e512044…`, `fits.csv` `5443998e…`, `observed_inputs.csv` `d20ee7ba…` in both runs), the recomputed selection equals the seal, and the recommendation is
unchanged.

**Amendments in M8 (release audit; documentation only).** No configuration, code path,
committed result or decision changed. The M8 audit found that the Diebold-Mariano test, as
used here, over-rejects at this horizon and these sample sizes; section 12 records the
measurements, and M8 notes mark each statement it changes (finding 3, sections 5, 6.1 and
6.2, this log and section 10). The quick start at the top now verifies the
reference run, and the section 8 recipe for the sealed development record was corrected.
Section 10 now states which effective sample sizes it quotes and what the flagged-target
sensitivity covers, and the projection is described as archived.

## 10. Limitations and failure cases

- **Not prospective.** Every forecast re-forecasts days already in the snapshot. All
  periods, including confirmation, were examined before the models were specified: by the
  original author (the legacy holdout lies in the confirmation year), in M0/M1, and in the
  exposures above.
- **One final data vintage.** The 311 records were extracted once, after 2026-06-30; values
  for recent days may have been revised later. At each historical origin the models see
  final values, not what was known then. The one-day-delay sensitivity does not change the
  best regression, B\* or the selection, but the size of revision effects is unknown.
- **Small effective samples.** 336 confirmation origins with 30-day windows are worth about
  14-27 independent observations for the main comparisons (the two regressions
  against seasonal naive and B\*, days 1-30) under the primary bandwidth, and fewer under
  wider ones. (**M8 correction:** 14-27 are the `eff. n` of section 6.5, computed with the
  Bartlett `2h` bandwidth. Under the primary rectangular bandwidth H1 and H2 are worth
  13.0 and 20.8; under rectangular 59, Bartlett 90 and Bartlett 120, 9.6-17.4; under
  Bartlett 60, 13.8 and 21.2 (section 6.2), so not every wider bandwidth gives fewer.)
  For H1 an observed MAE difference has to exceed
  about 129 requests per day (the half-width of its interval, roughly
  12 % of B\*'s MAE) to be significant at 5 % (125-150 across the
  bandwidths of section 6.2): the primary figures sit at the optimistic end of that range.
  Smaller real differences cannot be resolved with this history. (**M8 erratum:** with
  the size-corrected critical value of section 12, 2.39 instead of 1.967, the difference
  would have to exceed about 157 requests per day.)
- **The test over-rejects at this sample size (M8 erratum).** Under its own assumptions the
  pre-registered Diebold-Mariano rule rejects 7.6-9.1 % of true null hypotheses at a
  nominal 5 % with 336-486 origins, and the wider bandwidths more. The selection test's
  size-corrected p-value is 0.069; see section 12.
- **Not uniformly better.** The recommended regression loses to the same-weekday median in
  6 of 28 origin months, including the December 2024 year-end
  and summer 2025 (section 6.11). Its confirmation lead comes about equally from winter and
  spring 2026, not from a few hard months.
- **Year-end and anomalies.** December and January origins are the weakest for every model.
  Days with intake interruptions or surges (M1 flags) are kept in the primary metrics;
  excluding them changes the size but not the direction of any comparison. (**M8 note:**
  this sensitivity removes only the target days covered by a warning-level M1 flag. Of the
  44 flagged dates in the run manifest, 31 fall in the backtest's target range (13 are earlier
  than its first target, 2024-02-02): 13 development and 18 confirmation target days. In
  the confirmation year they are the 18 days, 13 of them the agency-level DHS interruption of
  2026-01-31..02-12; DHS is about 1.4 % of five-borough volume. Three of the five largest
  days of the series, 2026-01-27, 2026-01-28 and 2025-12-15, carry no flag and stay in (the
  largest, 2026-02-24, is flagged and excluded)
  ([DATA_QUALITY.md](DATA_QUALITY.md), section 3; the counts are reproduced by
  [`docs/audit/scripts/m8_doc_figures.py`](audit/scripts/m8_doc_figures.py)). So the result
  shows robustness to the M1-flagged days, not to city-wide anomalies.)
- **Seasonality from one to three years of history.** Training data span 13 months at the
  first development origin and 41 months at the last confirmation origin, so annual
  effects are estimated from one to three cycles.
- **Not covered.** Prediction intervals (M3), other series (agency, borough), any model
  needing new dependencies, and scoring the archived July 2026 forecast (no outcome data).
- **Projection.** `docs/results/atlas-m2-rolling-origin-v1/projection/` is a future
  projection from 2026-06-30 for the recommended model. Its total (333,968 requests for
  2026-07-01..30, against 330,941 published by the legacy notebook) is not validated, and no
  uncertainty is attached. (**M8 note:** it is an ARCHIVED PROJECTION: issued from the
  frozen snapshot, whose last observed day is 2026-06-30, for the target days 2026-07-01 to
  2026-07-30. It is not a live forecast, and it has never been scored: no outcome after
  2026-06-30 is in the snapshot. Its committed `report.md` keeps the original "FUTURE
  PROJECTION" label.)

## 11. M3 readiness

- **Inputs for intervals exist.** `forecasts.csv` holds out-of-sample errors per model,
  origin and horizon for 822 origins. M3 can calibrate horizon-specific intervals from them,
  for example empirical quantiles of the recommended model's recursive errors by horizon.
- **Dependence must be respected.** Errors are correlated across horizons of one path and
  across neighbouring origins; coverage must be assessed with blocks of origins, not
  forecast by forecast.
- **Calibration and evaluation need separate data.** The confirmation period cannot both
  calibrate and evaluate intervals. M3 needs a nested scheme (for example calibrate on
  development errors, evaluate on confirmation) fixed in advance, like M2's protocol.
- **Error growth comes mostly from recursion.** Interval width must grow with horizon, as
  the recursive errors do (section 6.9); one-step residuals would under-cover. In the
  development period part of the growth also comes from the ageing of the coefficients.
- **Known weak spots** for interval calibration: year-end, holidays, and the confirmation
  year's under-forecasting bias.

## 12. M8 erratum: the size of the Diebold-Mariano test

The M8 release audit (finding M8-04 in the
[findings register](audit/M8_FINDINGS_REGISTER.md#m8-04)) found that the test used for the
selection and for the primary tests rejects a true null hypothesis more often than its
nominal level at this horizon and these sample sizes. The formulas are implemented
correctly; the problem is the finite-sample size of the HLN statistic with a `t(T − 1)`
reference when `h = 30` and there are only 336-486 overlapping origins. Nothing below
changes a committed result, the code or a decision; the statements it affects carry M8
notes where they stand.

**How it was measured.** [`docs/audit/scripts/dm_size_study.py`](audit/scripts/dm_size_study.py)
simulates the test's own maintained null hypothesis: the per-origin loss differential is the
mean of 30 consecutive independent daily shocks, an exact MA(29), so losses on different
target days are uncorrelated and the mean difference is zero. Every simulated series goes
through the unchanged `atlas.stats.diebold_mariano` and `bandwidth_sensitivity`, with the
committed numbers of origins (486 in the development period, 336 in the confirmation
period). 20,000 replications per setting, fixed seed, Gaussian shocks and, as a heavy-tailed
check, Student t shocks with 3 degrees of freedom. The script reads the committed statistics
from the development and full `metrics.json`; its output is recorded in
[`dm_size_study_output.md`](audit/scripts/dm_size_study_output.md) (about two minutes on one
core). Monte Carlo standard errors are at most 0.3 percentage points for the sizes and 0.003
for the p-values quoted here. A simulated series for which a bandwidth gives no p-value
counts as a non-rejection, as a decision rule would treat a test that cannot be computed.
This happens only under rectangular 59, whose long-run variance estimate can be negative:
for 1.78 % of the Gaussian series with 486 origins and 5.35 % with 336. Counting only the
series where it is computed would raise its sizes to 13.8 % and 17.1 % and its
size-corrected p-values to 0.042 (selection, unchanged), 0.225 (H1) and 0.119 (H2); no
conclusion below changes.

**Size at a nominal 5 %** (Gaussian shocks; with Student t shocks every value is within 0.5
points):

| rule or bandwidth | 486 origins | 336 origins |
|---|---:|---:|
| pre-registered rule (`p_value_conservative < 0.05`) | 7.6 % | 9.1 % |
| HLN alone (rectangular 29) | 7.6 % | 9.2 % |
| rectangular 59 | 13.6 % | 16.2 % |
| Bartlett 60 | 11.0 % | 12.8 % |
| Bartlett 90 | 12.3 % | 14.8 % |
| Bartlett 120 | 13.9 % | 17.6 % |

- The Bartlett leg of the pre-registered rule almost never binds: of the 1,831 null series
  the HLN p-value rejects with 336 origins, it keeps 6 from being rejected (2 of 1,526 with
  486 origins).
- With 3,000 origins the same rule rejects 5.5 % (4,000 replications, Monte Carlo standard
  error 0.4 points): the over-rejection is a finite-sample property.
- Size-corrected 5 % critical values of `|t|` for the pre-registered rule are 2.21 (486
  origins) and 2.39 (336 origins), instead of the nominal 1.965 and 1.967; for the wider
  bandwidths they are 2.50-2.92 and 2.72-3.51.

**The committed statistics, size-corrected** (the share of simulated null series whose
p-value is at most the committed one; Gaussian shocks, Student t shocks in brackets; per
bandwidth, see the M8 table in section 6.2):

| test | origins | absolute t | nominal p | size-corrected p | size-corrected Holm p |
|---|---:|---:|---:|---:|---:|
| selection: ATLAS regression vs B\* (development) | 486 | 2.022 | 0.044 | 0.069 (0.067) | not in a family |
| H1: ATLAS regression vs B\* (confirmation) | 336 | 1.938 | 0.054 | 0.095 (0.093) | 0.095 (0.093) |
| H2: ATLAS regression vs seasonal naive (confirmation) | 336 | 2.943 | 0.003 | 0.022 (0.020) | 0.045 (0.040) |

**What the evidence supports.** The pre-registered development selection test (HLN-DM,
conservative p 0.044) passed its 5 % gate, but at a 30-day horizon with 336-486 overlapping
origins this test over-rejects under its own assumptions (7.6 % and 9.1 % false rejections
at a nominal 5 %). After a size correction the selection evidence is borderline (p 0.069),
above its 5 % gate. The pre-registered choice stands, but its statistical support is weak.
The statements of sections 6.1 and 6.2 that the selection and H2 are robust to the HAC
bandwidth do not hold once each bandwidth is size-corrected. The disclosed H1 result (not
significant after Holm) is unchanged; corrected, it is further from significance (0.095).

H2 stays below 0.05 after the size correction and the Holm adjustment (0.045) only if the
loss differential has no material dependence beyond about a month. A post hoc study repeats
the correction under eight nulls built from the committed differentials themselves:

- under the six that keep dependence of 45 days or more (block bootstraps with blocks of
  45-90 days, Gaussian nulls with Bartlett tapers 60-120), H2's Holm-adjusted p-value is
  0.068-0.155;
- under the two that keep only about a month's dependence, it is 0.036 (Gaussian, taper 29)
  and 0.050 (30-day blocks, within Monte Carlo error of 0.05).

H2's significance after the size correction is therefore not robust to the dependence
assumption ([dm_dependence_sensitivity_output.md](audit/scripts/dm_dependence_sensitivity_output.md),
section 2). H1 is not significant under any of the eight (0.094-0.164). The point-accuracy
lead over seasonal naive, 28.2 %, is unaffected.

**Why the decision stands.** The selection rule, its test and its 5 % gate were fixed before
any run, and the selection was sealed before any confirmation-period run of the protocol
(section 7; the earlier confirmation-period computations are listed in section 9).
Replacing the test now, knowing that the gate passed narrowly, would choose the rule with
knowledge of its outcome; that would be worse science than reporting the weakness. The
recommendation also had to pass the confirmation rule (the selected model's confirmation
MAE is not above B\*'s, section 6.2), which uses no test; but the regression reached that
rule only through the selection gate, so the recommendation inherits the gate's weak
support, and its confirmation lead is already disclosed as not statistically established.
With a correctly sized gate the protocol would most likely have selected B\*, the median of
the last five same weekdays, which finding 6 already names as a close and much simpler
alternative. In the dependence study the gate fails under the test's own MA(29) null (0.069),
under the Gaussian null with taper 29 (0.068) and under every block bootstrap (0.083-0.130).
It passes only under the Gaussian nulls with tapers 60-120 (0.014-0.049). Those nulls take
the development differential's negative autocorrelation at lags 30-60 as the truth, although
that autocorrelation is within the range MA(29) produces. When MA(29) holds they give p-values
below the exact ones, so a pass under them is weak evidence (sections 1, 2 and 5 of the
study).

**Limits of the correction.** It assumes the test's own null, an MA(29) differential, and so
shows what the test delivers when its assumption holds. H1's mean autocorrelation over lags
30-120 (0.126) is more than MA(29) produces: only 1.5 % of MA(29) series of the same length
reach it. H2's (0.042) is in the same direction but within the MA(29) range (8.0 % reach
it). The post hoc dependence study gives the figures above
([dm_dependence_sensitivity_output.md](audit/scripts/dm_dependence_sensitivity_output.md),
sections 1-2). The data cannot show which null is right. Values within Monte Carlo error of
0.05, and the ends of its ranges, carry that error. M3 uses the same test on the same grid; the M8
notes in [UNCERTAINTY.md](UNCERTAINTY.md) give what the correction does there.

**For a future protocol version** (not applied to v1, whose results stay as committed): a
reference distribution that is correct for the chosen bandwidth, such as simulated MA(h − 1)
critical values as here or fixed-b critical values for the Bartlett variants, fixed before
any run, and a decision rule whose "conservative" label describes its size.
