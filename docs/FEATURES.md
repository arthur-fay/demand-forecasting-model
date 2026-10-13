# Forecast features (M1)

`src/atlas/features.py` is the single place where forecast features are defined and computed.
Training, direct forecasting, recursive forecasting and (in M2) backtesting all call it, so a
model always sees features computed in exactly one way.

```bash
uv run atlas features                  # catalogue of the default feature set
uv run atlas --format json features    # same, with the canonical definitions and fingerprint
```

## Vocabulary

| Term | Meaning |
|---|---|
| target date `t` | The day a feature row describes, or will be used to predict |
| origin `o` | The last day whose demand is known when a forecast is issued |
| horizon `h` | `t = o + h`, with `h >= 1` |
| offset `k` | A feature with offset `k` reads the demand of day `t - k`, in calendar days |
| information cutoff | The last demand date a row may read |

## Builders

| Function | Rows | Demand a row may read |
|---|---|---|
| `training_features(series, origin=o, horizon=h)` | every `t <= o` with enough history | actual demand through `t - h`; labels are the actual demand of `t <= o` |
| `forecast_features(series, origin=o, horizons=(h, ...))` | `t = o + h` | actual demand through `o` |
| `recursive_forecast(series, origin=o, steps=H, predictor=f)` | `t = o+1 .. o+H` | actual demand through `o`, then the predictions for `o+1 .. t-1` |

All three call one private function (`_build`) with the same `FeatureSet`, so:

- the training row for `t` at horizon `h` **equals** the forecast row for `t` issued at origin
  `t - h`;
- a recursive row **equals** the training row whenever the predictions it consumed equal the
  actual values (teacher forcing), with one deliberate exception: declared events. A
  recursive row knows events as of the origin `o`; a horizon-1 training row for the same
  date knows them as of `t − 1`. An event announced between `o` and `t − 1` is therefore
  visible to the training row but not to the forecast. That is the correct leakage
  behaviour, and a test pins it so M2 can account for it.

These properties are tested exactly, not approximately (`tests/test_features.py`).

Training rows do not depend on the origin: row `t` at horizon `h` reads only data through
`t − h`. A caller could therefore build `training_features` once at the latest origin and
slice it for each earlier origin; a test checks this. The M2 backtest deliberately does not
use that shortcut: every origin builds its training matrix from a series that ends at the
origin, so an off-by-one in a slice cannot leak a label.

`recursive_forecast` takes a `predictor` callable (feature row → number). It fits nothing;
the M2 regressions (`atlas.models.RecursiveLinearForecaster`) supply the predictor. The
predictor's output must be finite and non-negative. Features that read no demand
(calendar, trend, holidays, events) are computed once for all steps; demand features are
recomputed per step. A 30-step forecast takes about 0.15 s
(about 5 ms per step), and a full training matrix takes about 15 ms.

## Leakage controls

1. **Structural truncation.** Every builder first calls `DemandSeries.known_through(origin)`.
   Observations after the origin are never passed to a feature. A test checks the data
   handed to the builder, independently of the guard below.
2. **Runtime guard.** Features read demand only through `FeatureContext.read(offset)`, which
   raises `LeakageError` if the day read is after that row's information cutoff. The guard
   does not trust the offsets a feature *declares*: a test feature that declares offset 1 but
   reads offset 0 is rejected. A read *before* the data held returns `NaN` (and the row is
   refused); it never wraps around to other days.
3. **Horizon check.** A feature set whose smallest offset is below the horizon cannot produce
   direct rows for that horizon (`LeakageError`). Use `recursive_forecast`, or a feature set
   whose offsets are all `>= h` (for example `Lag(7)`, `RollingMean(7, lag=7)` for `h <= 7`).
4. **Events.** A declared `KnownEvent` is visible to a row only when it was announced on or
   before the row's origin (`o` for forecasts; `t - h` for training rows). Federal holidays are
   computed by rule and are known in advance. Data-quality flags are never features.
5. **Trend anchor.** `trend_days` counts from a fixed date (2023-01-01), not from the first
   date in the data, so its value does not change with the training window.

## Missing data and short history

- Lags and windows are looked up by **date**, never by row position. A missing observation
  makes every feature that reads it `NaN`; it can never shift a lag onto the wrong day.
- By default a row that reads a missing observation raises `DataQualityError`. With
  `allow_incomplete=True` the row is kept with `NaN` and `FeatureMatrix.complete` marks it.
- A row that would read before the first observation raises `InsufficientHistoryError`.
  Nothing is padded or back-filled. `training_features` starts at the first date with enough
  history (`series.start + feature_set.max_offset`) unless `first_target` is given.

## Default feature set (`atlas-m1-default`, 34 features)

History needed: 364 days. Fingerprint (SHA-256 of the canonical definitions):
`f69f30a2674b71922520b7ab6b87fddffd4a5038f0fb9a5f992209f4fabc8f33`. A test pins it, so any
change to a definition is a deliberate, reviewed change.

| Feature | Kind | Reads | Definition |
|---|---|---|---|
| `iso_weekday` | calendar | – | ISO 8601 weekday of `t`, Monday = 1 ... Sunday = 7 |
| `is_weekend` | calendar | – | 1 if `t` is Saturday or Sunday |
| `is_iso_weekday_2` .. `_7` | calendar | – | Weekday indicators; Monday is the reference level |
| `month` | calendar | – | 1 .. 12 |
| `day_of_month` | calendar | – | 1 .. 31 |
| `day_of_year` | calendar | – | 1 .. 365, or 366 in leap years |
| `iso_week` | calendar | – | ISO week 1 .. 53 (2024-12-30 is week 1 of ISO year 2025) |
| `is_month_start`, `is_month_end` | calendar | – | 1 on the first or last day of the month (29 February counts as month end) |
| `year_sin_k`, `year_cos_k` (k = 1, 2) | calendar | – | `sin` or `cos` of `2πk · (day_of_year − 1) / days_in_year`; restarts exactly at 0 on 1 January in every year |
| `trend_days` | trend | – | Days from 2023-01-01 to `t` |
| `lag_k` (k = 1, 7, 14, 28, 364) | lag | `t-k` | Demand `k` days before `t` (364 = same weekday, 52 weeks earlier) |
| `rolling_mean_7`, `rolling_mean_28` | rolling | `t-w .. t-1` | Mean of the `w` days before `t` |
| `same_weekday_mean_4w` | rolling | `t-28, t-21, t-14, t-7` | Mean of the previous four values on the same weekday |
| `rolling_std_7`, `rolling_std_28` | variability | `t-w .. t-1` | Sample standard deviation (ddof = 1) of the same windows |
| `is_holiday` | event | – | `t` is the actual date of a US federal holiday |
| `is_observed_holiday` | event | – | `t` is the federal day off (Saturday holidays move to Friday, Sunday holidays to Monday) |
| `is_day_before_holiday` | event | – | `t + 1` is the *actual* date of a federal holiday |
| `is_day_after_holiday` | event | – | `t − 1` was the *actual* date of a federal holiday |
| `is_declared_event` | event | – | A declared event announced on or before the origin covers `t` |

All values are `float64`. Columns follow this order. Rows are indexed by `target_date`.

Eve and day-after are relative to the **actual** holiday date. When Independence Day falls
on a Saturday (2026), Friday 07-03 is both `is_observed_holiday` and
`is_day_before_holiday`, and Sunday 07-05 is `is_day_after_holiday`. Juneteenth is applied
from 2021, the year it was enacted. Holiday rules cover 2000–2100.

Feature parameters are type-checked: enum fields accept their string values (for example,
`HolidayIndicator("observed")`), while floats where integers are required, a `datetime`
trend anchor, or unknown enum values raise `FeatureError`. `RollingStd(w, ddof=0)` is named
`rolling_std_w_ddof_0`, so it cannot collide with the default `ddof=1`.

Differences from the legacy notebook (cell index 14), all deliberate:

| Legacy | M1 |
|---|---|
| Weekdays 0 = Monday (`dayofweek`), full dummy sets plus `is_weekend` and an intercept: collinear | ISO numbering; Monday is the reference level for the indicators |
| `day_of_year / 365.25`: exceeds 1 on 31 December of a leap year | `(day_of_year − 1) / days_in_year`, in `[0, 1)` |
| Trend counted from the first date of the data | Fixed anchor 2023-01-01 |
| Lags by row position after `sort_values` | Lags by calendar date |
| No holiday or event features | Federal holidays by rule, declared events gated by announcement date |
| Separate code paths for training and the recursive forecast | One implementation for both |

The choice of features for any model is an M2 decision. The default set is a well-tested
starting point, not a validated model specification. M2 made that choice in
`atlas-m2-regression-v1`; see [BACKTESTING.md](BACKTESTING.md).

## Feature classes added in M2

M2 needs four more building blocks. They are computed by the same `_build` and checked by the
same runtime guard as every other feature; none of them is in the default set, whose pinned
fingerprint is unchanged.

| Class | Name | Definition | Used by |
|---|---|---|---|
| `Calendar(CalendarField.YEAR)` | `year` | Calendar year of `t` | legacy model |
| `MonthIndicator(m)` | `is_month_m` | 1 if `t` is in calendar month `m` | legacy model (full set of 12) |
| `LegacyAnnualCycle(f)` | `legacy_annual_sin`, `legacy_annual_cos` | `sin`/`cos` of `2π · day_of_year / 365.25`, the notebook's convention (drifts by up to a day a year; exceeds a full cycle on 31 December of a leap year). Kept only for exact reproduction | legacy model |
| `SameWeekdayMedian(k, first_week=w)` | `same_weekday_median_kw` | Median of `y[t-7w] .. y[t-7(w+k-1)]`; `NaN` if any is missing | median baseline |

The two M2 model feature sets are built from these classes plus the M1 ones:

- `legacy-notebook-cell-14` (`atlas.models.legacy_feature_set`): the 32 legacy columns in
  the notebook's order;
- `atlas-m2-regression-v1` (`atlas.models.atlas_regression_feature_set`): 18 non-collinear
  features.

Both fingerprints are pinned by tests. See [BACKTESTING.md](BACKTESTING.md).

## Custom features and feature sets

Every feature is a frozen dataclass with typed parameters, a `name`, a `kind`, a
`description`, the `offsets` it reads and a `compute(context)` method. Available building
blocks: `Lag`, `RollingMean`, `RollingStd`, `SameWeekdayMean`, `Calendar`,
`WeekdayIndicator`, `AnnualFourier`, `Trend`, `HolidayIndicator`, `DeclaredEventIndicator`.

```python
from atlas.features import FeatureSet, Lag, RollingMean, SameWeekdayMean, training_features

weekly = FeatureSet((Lag(7), Lag(14), SameWeekdayMean(4), RollingMean(7, lag=7)), name="h7")
assert weekly.supports_horizon(7)
train = training_features(series, origin=origin, horizon=7, feature_set=weekly)
```

## Verification

| Property | Test (`tests/`) |
|---|---|
| Closed forms of every lag, mean and standard deviation | `test_features.py::test_closed_forms_on_linear_series`, `test_hand_computed_window` |
| Agreement with an independent pure-Python implementation | `test_matches_independent_reference_on_noisy_series` (`tests/synthetic.py`) |
| Future-data perturbation invariance (property-based) | `test_training_rows_ignore_data_after_origin`, `test_forecast_and_recursive_rows_ignore_data_after_origin` |
| Control: the last known day *does* change features | `test_perturbing_the_last_known_day_does_change_features` |
| A training row never reads its own target | `test_training_row_never_reads_its_own_target` |
| Runtime guard independent of declared offsets | `test_runtime_guard_catches_a_feature_that_misdeclares_its_offsets` |
| Training / direct / recursive parity | `test_training_rows_equal_forecast_rows_at_each_origin`, `test_direct_horizon_training_parity`, `test_recursive_rows_equal_training_rows_when_predictions_are_exact` |
| Gaps by date, insufficient history, invalid timestamps | `test_lags_are_by_calendar_date_not_row_position`, `test_gap_raises_unless_incomplete_rows_are_allowed`, `test_insufficient_history`, `test_invalid_origin_timestamps_are_rejected`, `test_series.py` |
| Leap years, ISO weeks and year boundaries | `test_leap_day_lags_and_calendar`, `test_year_boundaries_and_iso_weeks` |
| Determinism | `test_identical_inputs_give_identical_bytes`, `test_input_row_order_does_not_matter`, `test_feature_set_fingerprint_is_pinned` |
| Declared-event gating per builder and horizon | `test_training_rows_see_events_only_from_their_own_origin`, `test_direct_rows_see_events_only_as_of_the_origin`, `test_declared_event_visible_only_after_announcement`, `test_recursive_and_training_rows_differ_only_by_event_knowledge` |
| Every window feature alone refuses a gap | `test_each_window_feature_alone_refuses_a_gap` |

The suite was checked by mutation testing: deliberate bugs were planted one at a time (off-by-one
windows, wrong `ddof`, swapped holiday rules, removed truncation, leaked event knowledge,
weakened guards). Each must make at least one test fail. Mutants that survived the first
round led to the targeted tests above.
