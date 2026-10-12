# Known limitations of the original forecasting and capacity-risk claims

The original README, dashboard and PDF publish headline numbers that ATLAS reproduces
**arithmetically**: they reproduce from the committed data within their published precision,
except the regression RMSE (declared exception `LEGACY-V0-EX-002`; see
[DATA_SNAPSHOT.md](DATA_SNAPSHOT.md)). Reproducing a number does not validate what it is
claimed to mean. This page states what each headline number does and does not show.

Evidence labels:

| Label | Meaning |
|---|---|
| **[tested]** | Checked by the M0 test suite against the committed data (`tests/test_legacy_claims.py` and `tests/test_reproduce.py`) |
| **[code]** | Visible in the legacy notebook source; cells are 0-based indices in the `.ipynb` file, so cell index N is headed "STEP N+1" |
| **[repo]** | Checkable from repository files or git metadata, but not covered by an automated test |
| **[expected]** | A reasoned expectation that has not been measured |

## 1. "Model MAPE 4.93 %" is not 30-day forecast accuracy

- **[code]** (cell index 14-15) The 90-day holdout, 2026-04-02 to 2026-06-30, predicted each
  day using the **actual** previous-day, previous-week and previous-month values: its lag and
  rolling features were built from the observed series. It is a one-step-ahead (h = 1) score
  on a single block.
- **[code]** (cell index 16) The published 30-day forecast comes from a different model
  object, refitted on all data including the holdout, and is run **recursively**.
  - From day 2 onward its inputs include its own predictions: first the previous-day lag and
    the rolling statistics, later all lags.
  - **Neither the refitted model nor its accuracy at horizons 2 to 30 was ever evaluated.**
- **[tested]** The comparison baseline used the value seven days earlier, while **[code]**
  (cell index 14) the model also used the value one day earlier. The two did not have the
  same information.
- **[tested]** "40.10 % improvement" is the relative reduction of MAPE (8.23 to 4.93, a
  difference of 3.30 points), not a 40 % gain in accuracy.

**Do not quote 4.93 % as validated 30-day accuracy.** The accurate description is:
"one-step-ahead (h = 1) holdout MAPE with observed lags, single 90-day block, legacy model
before refitting". A rolling-origin backtest that evaluates every horizon from 1 to 30 is
planned for M2.

## 2. "Planning capacity 11,609 per day" is not operational capacity

- **[tested]** 11,609 is the 75th percentile of the last 180 days of **incoming requests**
  (2026-01-02 to 2026-06-30). By construction 45 of those 180 days (25 %) lie above it.
- **[repo]** No table in the repository contains closures, backlog, staffing,
  service-level targets or cost. Nothing measures the ability of any agency to resolve
  requests.
- **[repo]** A single city-wide number mixes services that are not interchangeable. The
  five-borough segments contain 17 agency codes, two of them intake-system codes (`3-1-1`,
  `NYC311-PRD`).

**Do not present the demand percentile as service capacity.** Any staffing, budget or
optimisation parameter introduced later is hypothetical unless supported by evidence, and will
be labelled `HYPOTHETICAL`.

## 3. Shortfall days, gap and "advance warning" are within the noise

- **[tested]** The five "High" days exceed the percentile by 12, 84, 58, 202 and 210
  requests, 13 to 28 days ahead. Every one of these is smaller than the model's one-step
  holdout MAE of 514.3.
- **[expected]** That MAE probably understates the error at 13 to 28 days ahead. This is not
  measured; see M2.
- **[code]** (cell index 17) No forecast interval or probability is used anywhere. The risk
  tiers (95 / 100 / 110 % of the percentile) have no documented basis.
- **[tested]** "Total gap 566" adds up positive daily excesses. Over the same 30 days,
  forecast demand (330,941) is below 30 times the percentile (348,270).
- **[tested]** "13 days advance warning" is the number of days from the data cutoff
  (2026-06-30) to the first flagged day. It is a horizon index, not a validated lead time.
- **[repo]** The repository's dated files are all later than that first flagged day,
  2026-07-13: Power BI zip members 2026-07-17, PDF creation and commit 2026-07-18.

## 4. Other modelling limitations

- **[code]** (cell index 14) The regression's inputs are linearly dependent: full weekday and
  month dummy sets plus an intercept, an `is_weekend` flag, and overlapping trend terms.
  Predictions are still defined, but the coefficients are not identifiable or interpretable.
- **[code]** (cell index 14) There are no holiday or event features, although the forecast
  window contains July 4.
- **[code]** (cell index 0-3, 19) The legacy pipeline cannot be re-run offline: it pulls
  from a live API, depends on Google Colab, and records no environment versions. M0 replaces
  re-running it with verification of the frozen outputs.

## What M0 does and does not change

M0 does **not** change any legacy file or number. It:

- makes the numbers verifiable;
- labels each one with its actual meaning (`atlas check` prints these labels next to the
  values);
- records the limitations above.

Correcting the methodology is the job of later milestones; see [ROADMAP.md](ROADMAP.md).
