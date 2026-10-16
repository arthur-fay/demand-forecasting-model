# Data conventions, reconciliation and quality flags (M1)

M1 extends the M0 validation system. It reuses the M0 `Issue` and `CheckReport` types, the
M0 reconciliation check and exception register, and the M0 loader, which now also checks
in-memory tables (`atlas.loader.validate_frame`). No legacy file is modified.

```bash
uv run atlas quality                  # integrity -> validation -> quality
uv run atlas --format json quality    # includes every flag as a record
```

`atlas check` (the M0 entry point) is unchanged, and its output is identical to M0's. CI runs
both commands.

## 1. Calendar conventions for new ATLAS data

All data produced by ATLAS uses ISO 8601 (`src/atlas/dates.py`):

- weekdays are Monday = 1 ... Sunday = 7;
- weeks are ISO weeks, paired with the ISO year (2024-12-30 is 2025-W01; 2026-12-31 is
  2026-W53);
- dates are calendar days with no time of day or time zone. `YYYY-MM-DD` is the only
  accepted text form: `20240229`, `2024-W09-4` and timestamps with a time are rejected.

Legacy tables keep their conventions and are never rewritten. Two differ from ISO and are
converted explicitly with `to_iso_weekday`:

| Table | Column | Legacy convention | ISO conversion |
|---|---|---|---|
| `weekday_workload` | `weekday_number` | SQLite `%w`: Sunday = 0 ... Saturday = 6 | `0 → 7`, otherwise unchanged |
| `date_dimension` | `weekday_number` | Monday = 1 ... Sunday = 7 | already ISO |
| `date_dimension` | `week_of_year` | ISO week, paired with the calendar year | use `iso_week` for the ISO year |

A test converts both legacy tables and checks the result against the ISO weekday names.

## 2. City-wide versus five-borough reconciliation

`daily_total` counts every request. `daily_segments` contains only the five named boroughs.
M0 measures the difference and accepts it only as declared exception `LEGACY-V0-EX-001`.

M1 (`src/atlas/reconciliation.py`) adds an explicit component table,
`daily_borough_components` (contract in `contracts.py`):

```
daily_total(d) = BRONX(d) + BROOKLYN(d) + MANHATTAN(d) + QUEENS(d) + STATEN ISLAND(d) + UNASSIGNED(d)
```

- `UNASSIGNED` is an **aggregate residual** with `component_kind = unassigned_residual`.
  It is the measured number of requests outside the five-borough filter.
- It exists only at city-wide daily level. There is no agency or record-level breakdown, and
  none is invented. The agency table is not altered.
- Borough components are explicit, including zeros, so every date has six rows that sum
  exactly to `daily_total`.
- **Fail closed.** The table is built only when the M0 reconciliation check has no error,
  that is when segments never exceed the total and any gap matches the declared exception
  exactly. Otherwise `DataQualityError` is raised with the M0 findings.
- The quality stage also checks that the residual equals the M0 statistics (total gap and
  maximum gap).

Result on `legacy-v0`:

| Measure | Value |
|---|---|
| Residual total | 13,279 requests (0.1077 % of 12,331,111) |
| Dates with a positive residual | 1,273 of 1,277 |
| Largest residual | 2,434 on 2023-09-15 (also the only date above 2 % of the total) |
| Negative residuals | none |

## 3. Data-quality flags

`src/atlas/quality.py`. Flags **annotate** periods. They never edit, fill or drop data.

Every detector is **causal**: whether a day is flagged depends only on observations on or
before that day. A test perturbs all data after a cut-off date and checks that no flagged day
on or before it changes. The *end date* of a multi-day flag naturally depends on when the
condition stops. Flags are not used as forecast features.

| Flag | Subject | Rule (defaults in `QualityThresholds`) |
|---|---|---|
| `missing_period` | city-wide | A run of dates with no observation. These are kept as gaps; feature rows that read them fail unless explicitly allowed. |
| `surge` | city-wide | Robust same-weekday score `≥ +5` **and** at least 25 % above the reference |
| `low_volume` | city-wide | Score `≤ −5` **and** at least 25 % below the reference: a potential intake interruption, or a holiday |
| `intake_interruption` | each agency and borough | The run starts on a day `≤ 10 %` of the segment's 10th-percentile day over the previous 28 days, provided that percentile is `≥ 50`. It continues while each following day stays `≤ 10 %` of the same starting reference. |
| `catch_up_surge` | each agency and borough | Within 3 days after an interruption (and before the next interruption starts), the first day `≥ 1.5 ×` the segment's 28-day median before the interruption: a backlog entered late |
| `unassigned_share` | the `UNASSIGNED` residual | The residual is above the share declared for the reconciliation exception (2 % of the day's total for `legacy-v0`) |

**Robust same-weekday score.** For day *d*, the reference is the median of the eight preceding
same-weekday values (*d*−7, …, *d*−56). The scale is `max(1.4826 × MAD, 5 % of the
reference)`. Days without eight complete predecessors are not scored. Using the same weekday
removes the strong weekly cycle. The median and MAD resist earlier anomalies. The 5 % floor
prevents a near-constant history from producing huge scores.

**Known limits of the detectors (M8 correction).** The median and MAD resist a single
earlier anomaly, but the detectors have power limits, and the flags are not a complete
inventory of anomalous days:

- *MAD inflation.* When the eight-week window mixes holiday-season lows with December and
  January peaks, the MAD, and with it the scale, grows enough to hide large surges. On
  2026-01-27 the eight preceding Tuesdays ranged from 8,354 to 14,078 (MAD 1,215, scale
  1,801), so 19,225 requests, 70 % above the reference of 11,306, scored only +4.40.
- *An interruption shortly after another one is undetectable.* The interruption rule needs
  the 10th percentile of the previous 28 days to be at least 50, and an outage pulls that
  percentile down. DHS fell to 0, 0, 3 and 3 requests on 2026-02-22..25, ten days after its
  flagged interruption of 2026-01-31..02-12; the percentile over the preceding 28 days was
  0, so these days carry no flag.

Over the whole series, 61 days lie at least 25 % above their same-weekday reference and 16
are flagged as surges; in 2025-07..2026-06 the counts are 24 and 4. Three of the five
largest days of the series carry no flag:

| day | requests | reference | above reference | score |
|---|---:|---:|---:|---:|
| 2026-01-27 | 19,225 | 11,306 | 70 % | +4.40 |
| 2026-01-28 | 16,982 | 10,486.5 | 62 % | +2.58 |
| 2025-12-15 | 16,388 | 11,447.5 | 43 % | +3.76 |

(Computed in M8 with `atlas.quality.same_weekday_scores` on `legacy-v0`.) Analyses that
leave out flagged days therefore leave out the flagged days only, not every anomalous day;
see [BACKTESTING.md](BACKTESTING.md), section 10.

**Interruption rule.** A segment absent from `daily_segments` on a date has zero requests (M0
contract). Requiring the reference percentile to be at least 50 means sparse, bursty segments
are never flagged. For a Poisson count with mean 50, a day at or below 5 has probability about
6 × 10⁻¹⁶, so ordinary sampling noise cannot trigger the rule. Real counts are over-dispersed,
so this is an illustration, not a false-alarm rate.

**Holidays.** Flags touching a federal holiday, its observed day, its eve or the day after
carry the holiday context in `holiday`, in day order. A `low_volume` or
`intake_interruption` flag is reported as `info` (expected) only when **every** day of it is
holiday-related. Every other flag is a `warning`, so an outage that starts before Christmas
Eve and runs into it is not hidden as "expected". Severity describes the whole run, so a run
that later extends past the holiday becomes a `warning`. No flag fails the stage. A
city-wide `surge` on the day of one or more segments' `catch_up_surge` is linked to each of
them in `explanation`.

The thresholds were fixed before looking at individual results. They are design choices, not
estimates: no labelled set of true incidents exists to calibrate them against.

### Results on `legacy-v0` (28 flags; pinned by `tests/test_quality.py`)

| Flag | Count | Notes |
|---|---|---|
| `missing_period` | 0 | Every date from 2023-01-01 to 2026-06-30 is present |
| `low_volume` | 4 | 2023-11-23 Thanksgiving, 2023-12-25 Christmas, 2024-01-01 New Year's Day, 2024-05-27 Memorial Day: all `info` |
| `surge` | 11 | Largest: 2023-09-29 (17,962 vs reference 9,434; score +18.1). Others include 2024-04-15 (HPD catch-up), 2025-06-23/24 and 2026-02-24 (22,806, the series maximum) |
| `intake_interruption` | 8 | HPD 2024-04-11..14 (zero requests for 4 days; typically about 1,900 per day); HPD 2024-07-13; DHS 2026-01-31..02-12 (12 requests in 13 days vs about 109 per day); DOB 2025-12-14; EDC on 4 occasions, one on Christmas Day (`info`) |
| `catch_up_surge` | 4 | HPD 2024-04-15 (7,398, about 3.9 × typical); HPD 2024-07-14 (3,403); EDC 2023-03-03 and 2023-03-16 |
| `unassigned_share` | 1 | 2023-09-15: 2,434 requests (25.54 % of the day) outside the five boroughs; the only day above 2 % |

What the flags show, and what they do not show:

- **HPD 2024-04-11..14** is the clearest intake interruption. HPD reports zero for four days,
  then 7,398 on 2024-04-15. That day is also the second-largest city-wide surge (score +15.0).
  The catch-up offsets about 72 % of the missing volume (about 7,600 expected over the four
  days; 7,398 − 1,902 ≈ 5,500 excess on the catch-up day). (**M8 correction:** the share
  depends on the reference. The 72 % uses the weekday-blind 28-day median, 1,902 per day,
  for all five days, although HPD receives fewer requests at weekends and more on Mondays.
  With same-weekday medians it is about 69 % (the eight preceding same weekdays, as the
  surge score uses) or about 86 % (the four preceding ones), so about 69-86 %.) The pattern
  is consistent with a recording or intake problem rather than a real change in demand.
  Models in M2 should treat these days explicitly (for example, exclude them from evaluation
  or score them separately). Any such decision is M2's, and the data stays unchanged.
- **DHS 2026-01-31..02-12** is a sustained collapse rather than a short outage. It may be a
  reporting change; the data cannot tell. A second drop, 2026-02-22..25, is not flagged
  (M8; see the detector limits in this section).
- City-wide surges may reflect real events (for example severe weather) or recording
  problems. ATLAS does not verify external events, and the flags make no claim about causes.
- Borough series show no interruption, so the interruptions are agency-specific.
