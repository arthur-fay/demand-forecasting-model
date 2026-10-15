# Architecture

Notebook cells are referred to by their 0-based index in the `.ipynb` file ("cell index N").
The notebook's own headings number the analysis steps differently: cell index 15, for
example, is headed "STEP 16".

## 1. The original project (preserved as-is)

The original project is a single Google Colab notebook plus artifacts derived from it.
None of these files are modified by ATLAS; their SHA-256 hashes are part of the
`legacy-v0` manifest and are verified on every run.

```
NYC Open Data Socrata API (dataset erm2-nwe9, live)                    cell index 1-3
  ├─ daily_total     city-wide count per day of created_date
  └─ daily_segments  count per day, agency and borough (five named boroughs only)
        │
        ▼
SQLite database file on the Colab VM + pandas: yearly/YTD/monthly/
  weekday/borough/agency aggregates                                    cell index 6-13
linear regression, 90-day holdout, recursive 30-day forecast           cell index 14-16
"capacity" threshold and risk tiers                                    cell index 17
16 CSV exports -> zip -> manual download -> "01 Data/"                 cell index 18-19
        │
        ▼
Power BI report (04), PDF export (06), screenshots (05), SQL script (03, not executed)
```

Properties of that pipeline that shaped M0:

- The only data entry point was a live, changing API. No extraction time, query hash or
  library versions were recorded, and no code path reads the committed CSVs.
- Data-quality checks printed `PASSED` but never stopped execution.
- Everything ran in one notebook with shared global state and Colab-specific paths.

## 2. The ATLAS package (M0)

M0 adds an importable package that works entirely offline on the committed data.

```
src/atlas/
  config.py       Settings: flag > environment > default; repository discovery
  errors.py       AtlasError, ConfigError, SnapshotError (configuration problems only)
  issues.py       Issue (code, severity, subject, examples) and CheckReport
  contracts.py    Column kinds and the declared contract of each legacy table
  loader.py       Strict CSV reading and parsing against a contract
  snapshot.py     Manifest model, SHA-256 hashing, manifest generation, verification
  registry.py     legacy-v0 specification: tables, artifacts, provenance
  validation.py   Cross-table reconciliation and the known-exception register
  summaries.py    Historical aggregates recomputed from the two base tables
  legacy.py       Legacy output tables re-derived from their inputs (no model fitting)
  reproduce.py    Comparison with the committed tables, KPIs and model metrics
  pipeline.py     Fail-closed stage orchestration
  cli.py          argparse command-line interface (`atlas`)

  M1 additions
  dates.py           ISO 8601 conventions; explicit conversion of legacy weekday numbers
  events.py          US federal holidays by rule; declared events gated by announcement date
  series.py          DemandSeries: validated daily series (the only input to features)
  reconciliation.py  daily_borough_components with the UNASSIGNED aggregate residual
  quality.py         Data-quality flags and the `quality` stage
  features.py        Feature definitions and the training / forecast / recursive builders

  M2 additions
  regression.py      Least squares with rank, conditioning, leverage and estimability
  models.py          Forecaster interface; baselines, exponential smoothing, two regressions
  backtest.py        Rolling-origin engine and future projection (one prediction path)
  metrics.py         Error metrics, buckets, paired comparisons on common forecasts
  stats.py           HLN Diebold-Mariano test, HAC variances, Student t, Holm
  evaluation.py      Protocol: selection, recommendation, sensitivity, decomposition
  report.py          Deterministic Markdown report from the metrics payload
  experiment.py      Versioned configuration, fingerprints, provenance, run persistence
  legacy_refit.py    Exact reproduction of the legacy holdout and 30-day forecast
  runner.py          Fail-closed M2 commands (backtest run/verify/legacy, forecast)

  M3 additions
  uncertainty_config.py  Versioned M3 configuration; ties to the pinned M2 protocol
  calibration.py         Residual store, eligibility by target date and latency, pooling
  intervals.py           Predictive distributions and interval methods
  exceedance.py          Historical and HYPOTHETICAL thresholds; exceedance ordering checks
  scoring.py             Coverage, interval score, pinball, WIS, Brier, reliability, HAC tests
  probabilistic.py       Issue, score, select and judge probabilistic forecasts
  uncertainty_report.py  Deterministic Markdown report from the metrics payload
  uncertainty_runner.py  Fail-closed M3 commands (uncertainty run/verify/inspect/project)

  M4 additions
  planning.py             Planning problem, plan, independent evaluation and constraint check
  optimizer.py            LP matrices, HiGHS solve and status classification, certificate, duals
  decision_analysis.py    Checked solves, diagnostics, relaxed plans, optimal face, shadow-price
                          brackets, policy comparisons, frontier, sweeps and monotonicity
  policies.py             Fixed-capacity and backlog-trigger rules
  decision_inputs.py      Hash-verified M2 projection and accuracy, M3 status and stress paths
  optimization_config.py  Versioned M4 configuration; HYPOTHETICAL quantities with exact units
  scenarios.py            Scenarios as planning problems; demand-percentile guard
  optimization_report.py  Deterministic Markdown report from the metrics payload
  optimization_runner.py  Fail-closed M4 commands (optimize run/verify/inspect)

  M5 additions (atlas.api; see API.md)
  api/app.py              create_app(): lifespan, middleware, routers, error handlers
  api/routers/            HTTP routes (health, system, forecasts, uncertainty, scenarios,
                          simulations): parameters, status codes, documented responses
  api/schemas.py          Pydantic contracts: integrity and provenance envelope, errors
  api/middleware.py       request id, Host check, body limit, security headers, access log
  api/errors.py           ApiError and the one error body
  api/dependencies.py     injected services, API key, JSON-only bodies, no unknown query
  api/reference.py        ReferenceStore: committed M2/M3/M4 outputs, checked at startup
  api/views.py            response data with integrity labels and provenance
  api/simulation.py       request -> M4 configuration -> analyse_scenario in worker processes
  api/settings.py         validated ATLAS_API_* settings
  api/logs.py             JSON-lines logging
  api/server.py           atlas api serve / openapi (loopback unless a key is set)
  api/benchmark.py        atlas api bench (measured latency and memory)
```

### Data flow

```
snapshots/legacy-v0/manifest.json ──► integrity     every file: size, SHA-256; tables also
                                         │ pass       header, row count, date range
"01 Data/*.csv" ──► loader (contracts) ──► validation  types, nulls, ranges, keys, calendar,
                                         │ pass       totals vs segments, declared exceptions
                                         ▼
                                     reproduction   6 aggregate tables, 6 legacy output
                                                    tables, 14 KPIs, 10 model metrics
```

### Design decisions

| Decision | Reason |
|---|---|
| Legacy files stay at their original paths; the manifest only describes them | M0 must not move or modify the original artifacts. |
| Every CSV field is read as text and parsed by declared kind | Results do not depend on pandas type inference, which differs between pandas versions. Only an empty field is null; null-like text (`NaN`, `NA`, `None`, `null`, `N/A`) is rejected in every column, including text columns. |
| Problems are collected as `Issue`s, not raised | A report shows every problem in a run. Exceptions are reserved for unusable configuration; anything else that escapes is a bug and exits with code 3. |
| Stages are fail-closed and skip downstream work | Nothing is computed from unverified or invalid data. |
| Known exceptions must match observed statistics exactly | A documented defect is accepted only in its documented form; any drift fails, and a declaration that matches nothing is reported as stale. |
| Rounded legacy values must be a valid rounding of the exact value, at the published precision | Works for both SQLite half-up and Python half-even rounding without hiding real differences. |
| Legacy conventions are reproduced, not "fixed" (Sunday = 0 weekdays, active-day averages, the demand-percentile "capacity") | The goal of M0 is to prove what the original numbers are. Corrections belong to later milestones. |
| Minimal runtime dependencies (pandas, pydantic); argparse rather than a CLI framework | Small attack surface and fewer version conflicts on a small VPS. |
| `uv.lock` committed; `pandas>=3.0,<3.1` | Reproducible installs; one pandas minor version. |

### M1 data flow

```
validated daily_total ─► DemandSeries ─► features.py ─► training rows   (t <= o, data through t-h)
                              │                      ├► forecast rows   (t = o+h, data through o)
                              │                      └► recursive rows  (data through o + predictions)
                              ▼
validated daily_total + daily_segments ─► reconciliation.py ─► five boroughs + UNASSIGNED residual
                                       └─► quality.py ─► flags (missing, surge, low volume,
                                                         interruption, catch-up, unassigned share)
```

`atlas quality` runs integrity → validation → quality. It does not depend on the reproduction
of legacy outputs, and `atlas check` is unchanged.

| M1 decision | Reason |
|---|---|
| One private builder behind training, direct and recursive features | Parity by construction; tests check it exactly |
| Truncate at the origin, then guard every read against the row's cutoff | Two independent barriers against look-ahead; the guard does not trust declared offsets |
| Lags by calendar date; gaps raise unless explicitly allowed | A missing day can never shift a lag; nothing is imputed silently |
| Fixed trend anchor | Feature values do not depend on the training-window length |
| Residual as one labelled aggregate component, built only when M0 accepts the gap | Totals reconcile by construction without inventing agency records |
| Flags are causal annotations, never edits or features | Usable in rolling-origin evaluation; the data stays as recorded |
| M1 tables checked by the M0 loader (`validate_frame`) | One set of contract rules for files and in-memory tables |
| Pinned feature-set fingerprint and pinned legacy flag inventory | Definition or detection changes must be deliberate |

### M2 data flow

```
validated daily_total ─► DemandSeries ─► for each origin o (chronological):
                                           history = series.through(o)        (barrier 0)
                                           for each model: forecast_at_origin(model, history, 30)
                                             fit:      training_features(history, h=1)   (M1)
                                             forecast: direct M1 rows, or recursive_forecast (M1)
                                         ─► records without actuals
                         after all forecasts ─► actuals attached by target date ─► records
records ─► evaluation (selection on development; confirmation; sensitivity) ─► metrics.json
                                                                           └► report.md
```

`atlas forecast` calls the same `forecast_at_origin` at the last observed day and writes
records of type `future_projection`, which carry no actual values.

| M2 decision | Reason |
|---|---|
| Every model is fitted on a `DemandSeries` that *ends* at the origin | Later observations are absent from the object, not hidden; the M1 truncation and runtime guard still apply inside |
| One function, `forecast_at_origin`, for backtests and projections | The backtest exercises the operational prediction path |
| Only `ForecastError` becomes a recorded failure | `LeakageError` and other bugs stop the run instead of becoming "failed" rows |
| Actuals attached after every forecast exists | Model code never sees a target value |
| Faithful legacy model kept separate from the modernised regression | Reproduction (exact, 120 published values) and improvement are different claims |
| Pre-registered selection, sealed before confirmation; every later run re-runs the development period and must reproduce the seal; period rules validated for the whole configuration | The confirmation period cannot influence the choice, and an edited seal or a partial run cannot bypass it |
| HLN Diebold-Mariano with HAC variance; effective sample size reported | Forecast windows from neighbouring origins overlap |
| Plain files per run (manifest, CSV, JSON, Markdown); no database | Lightweight, diffable, verifiable byte for byte |
| No process pool | About six minutes serially; fewer moving parts, coverage of all code |

### M3 data flow

```
M3 config ─► pinned M2 config + M2 recommendation (complete run, primary model checked)
validated daily_total ─► run_backtest (M2 engine, every origin of the residual span)
records ─► ResidualStore (r = actual - prediction, status ok only)
for each M3 period, origin o (chronological), model, method/scheme:
    calibration set = residuals with target <= c - latency  (c = o rolling; period start fixed)
    predictive distribution ─► quantiles (non-crossing, >= 0) + P(Y > T) for each threshold
after every forecast exists ─► actuals attached by target date
selection period ─► WIS ranking ─► sealed selection.json
evaluation period ─► coverage, scores, Brier, reliability, strata ─► decision status
                                                              └► calibration_status.json
```

| M3 decision | Reason |
|---|---|
| Residuals from the M2 engine itself, not from a re-implementation | The intervals describe the errors of the forecasts M2 evaluated; a test checks the spot check |
| One access path to residuals, sliced by target date, with a cutoff guard | An earlier forecast can never be calibrated with a later observation |
| Sample sizes in distinct forecast paths; HAC effective size per set | Overlapping paths are not independent observations |
| Explicit predictive distribution per method | Quantiles, intervals and probabilities are mutually consistent; probabilities are never read off interval endpoints |
| Selection on M2's development targets, evaluation on the confirmation period, labelled as exposed | Selection, calibration and evaluation use different data, without pretending the later period is untouched |
| Status withheld rather than results adjusted | A failed or under-powered calibration is disclosed as such |

### M4 data flow

```
snapshot ─► integrity, validation (fail-closed) ─► data fingerprint
M2 projection run ─► SHA-256, record type, recommended model, same data ─► demand d[1..T]
M2 recommending run ─► run id, result fingerprint ─► confirmation MAE and bias (context)
M3 calibration_status.json ─► M3 provenance check ─► status attached to every result
M3 projection run (stress only) ─► same status file, primary model + sealed method ─► q path
config (HYPOTHETICAL quantities) + d ─► scenarios ─► planning problems
for each scenario:
    strict LP (HiGHS) ─► certificate + independent plan check + objective agreement
        optimal:    duals ─► finite-difference brackets; optimal face (uniqueness, ranges)
        infeasible: diagnostics (least violation, least spend) ─► relaxed plan (lexicographic)
    policies (fixed, backlog trigger) ─► same evaluator; LP at equal cash / equal backlog
    frontier (cash cap ─► least notional penalty), on-frontier check
sweeps on the base scenario ─► proved monotonicity checked
─► scenarios.csv, plans.csv, duals.csv, sensitivity.csv, frontier.csv, metrics.json, report.md
```

| M4 decision | Reason |
|---|---|
| Consume committed, hash-verified M2/M3 outputs instead of recomputing them | The same forecast and status as the reference runs, offline, and no BLAS-dependent bits in the LP inputs |
| Discretionary budget only; committed regular cost outside the score | The score then reflects decisions only, and a budget below the payroll cannot create an artificial infeasibility |
| No slack in the planning LP; relaxations only in labelled diagnostic programmes | A relaxed result can never be mistaken for a feasible plan |
| Certificate from primal and dual values, plus a matrix-free plan check | Optimality and feasibility are proved, not taken from the solver status |
| Lexicographic second stages for diagnostics, comparisons and frontier points | A single-objective optimum leaves cash and timing arbitrary on its optimal face |
| Frontier on the notional penalty, not on request-days | The weighted optimum provably lies on it, so the check cannot fail on a correct run |
| Shadow prices bracketed by one-sided finite differences | Duals are subgradients; a bracket failure is a bug, unequal slopes are a breakpoint, not an error |
| HiGHS model status parsed from the message; no time limit | SciPy's integer status conflates model errors with infeasibility; a time limit is not reproducible |
| Every CSV row labelled (HYPOTHETICAL, not validated, M3 status) | Rows copied out of context still carry their caveats |

### M5 data flow

```
startup:  snapshot ─► integrity, validation (M0) ─────────────────────► history
          reference M4 config ─► load_inputs (M2 projection + accuracy, M3 status,
                                 quantile paths, every pin) ──────────► forecast, status
          M2 recommending run ─► metrics.json (bytes verified) ──────► models, metrics
          M3 projection ─► projection_intervals.csv (pinned) ────────► intervals
          M4 reference run ─► optimize inspect + verified bytes ─────► scenarios
          (each a component; a failed one answers 503, the rest serve)
request:  GET  ─► views (in memory) ─► data + integrity + provenance
          POST /simulations ─► M4 config (client values, HYPOTHETICAL) ─► parse + build
                (422 on M4's rules) ─► idle worker process (else 503) ─► analyse_scenario
                ─► M4 runner serialisation ─► validated response (504 + kill at deadline)
```

| M5 decision | Reason |
|---|---|
| Serve committed, verified outputs; compute only one bounded scenario per request | The research runs take seconds to minutes; their results are fixed and fingerprinted |
| Reuse M4's loaders, parser, scenario builder, analysis and serialisation | One implementation of the science; simulations reproduce the reference run exactly (tested) |
| Spawned worker processes for solves, no queue, kill at deadline | The M4 solve layer's warnings capture is process-global; processes also give real deadlines and keep the API responsive |
| `validated_recommendation` and `point_forecast_validated` are schema constants | A response cannot claim validation, whatever the code path |
| Per-component readiness, verified bytes parsed once | Damaged artifacts fail closed without taking down unrelated endpoints |

### Extending to new snapshots

A new snapshot needs:

1. a specification in `registry.py` (`SNAPSHOT_SPECS`) and contracts for its tables
   (`SNAPSHOT_CONTRACTS`);
2. a manifest generated once with `atlas snapshot manifest` and committed under
   `snapshots/<id>/`;
3. optionally a known-exception register.

The reproduction rules in `reproduce.py` are specific to the `legacy-v0` tables and run only
for snapshots listed in `registry.REPRODUCIBLE_SNAPSHOTS`; for any other snapshot the stage
reports "not applicable". Registered manifests are never regenerated to describe changed
data: a changed dataset is a new snapshot id.
