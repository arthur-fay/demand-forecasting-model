# Known limitations of the original forecasting and capacity-risk claims

The original README, dashboard and PDF publish headline numbers that ATLAS reproduces
**arithmetically**: they follow exactly from the committed data (see
[DATA_SNAPSHOT.md](DATA_SNAPSHOT.md)). Reproducing a number does not validate what it is
claimed to mean. This page states what each headline number does and does not show.

Evidence labels: **[tested]** checked by the M0 test suite against the committed data;
**[code]** visible in the legacy notebook source; **[audit]** found during the read-only
audit and not re-tested in M0.

## 1. "Model MAPE 4.93 %" is not 30-day forecast accuracy

- The 90-day holdout (2026-04-02 to 2026-06-30) predicted each day using the **actual**
  values of the previous day, week and month (lag and rolling features built from the
  observed series). It is a one-step-ahead (h = 1) score on a single block. **[code]** (cells 14-15)
- The published 30-day forecast is produced differently. It comes from a model refitted on all
  data and is run **recursively**, so from day 2 onward its inputs are its own predictions.
  **Its accuracy at horizons 2 to 30 was never measured.** **[code]** (cell 16)
- The comparison baseline used the value seven days earlier, while the model used the value one
  day earlier, so the two did not have the same information. **[tested]**
- "40.10 % improvement" is the relative reduction of MAPE (8.23 to 4.93, a difference of 3.30
  points), not a 40 % gain in accuracy. **[tested]**

**Do not quote 4.93 % as validated 30-day accuracy.** The only accurate description is:
"one-step-ahead (h = 1) holdout MAPE with observed lags, single 90-day block, legacy model".
A rolling-origin backtest that evaluates every horizon from 1 to 30 is planned for M2.

## 2. "Planning capacity 11,609 per day" is not operational capacity

- 11,609 is the 75th percentile of the last 180 days of **incoming requests** (2026-01-02 to
  2026-06-30). By construction 45 of those 180 days (25 %) lie above it. **[tested]**
- The repository contains no data on closures, backlog, staffing, service-level targets or
  cost. Nothing in it measures the ability of any agency to resolve requests. **[audit]**
- A single city-wide number mixes 17 agency codes whose staff and services are not
  interchangeable. **[audit]**

**Do not present the demand percentile as service capacity.** Any staffing, budget or
optimisation parameter introduced later is hypothetical unless supported by evidence, and
will be labelled `HYPOTHETICAL`.

## 3. Shortfall days, gap and "advance warning" are within the noise

- The five "High" days exceed the percentile by 12, 84, 58, 202 and 210 requests. Every one
  of these is smaller than the model's own one-step MAE of 514.3, and that MAE is optimistic
  for the horizons concerned (13 to 28 days ahead). **[tested]**
- No forecast interval or probability is used anywhere. The risk tiers (95 / 100 / 110 % of
  the percentile) have no documented basis. **[code]**
- "Total gap 566" adds up positive daily excesses. It ignores the fact that over the same 30
  days forecast demand (330,941) is below 30 times the percentile (348,270). **[tested]**
- "13 days advance warning" is the number of days from the data cutoff (2026-06-30) to the
  first flagged day. It is a horizon index, not a validated lead time. The repository's dated
  files (Power BI file 2026-07-17, PDF and commit 2026-07-18) are all later than that first
  flagged day, 2026-07-13. **[audit]**

## 4. Other modelling limitations

- The regression's inputs are linearly dependent (full weekday and month dummy sets plus an
  intercept, an `is_weekend` flag, and overlapping trend terms). Predictions are still defined,
  but the coefficients are not identifiable or interpretable. **[audit]**
- There are no holiday or event features, although the forecast window contains July 4.
  **[code]**
- The legacy pipeline cannot be re-run offline. It pulls from a live API, depends on
  Google Colab, and records no environment versions. M0 replaces re-running it with
  verification of the frozen outputs. **[code]**

## What M0 does and does not change

M0 does **not** change any legacy file or number. It makes the numbers verifiable, labels each
one with its actual meaning (`atlas check` prints these labels next to the values), and
records the limitations above. Correcting the methodology is the job of later milestones;
see [ROADMAP.md](ROADMAP.md).
