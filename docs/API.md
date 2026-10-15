# HTTP API (M5)

ATLAS M5 is a FastAPI service over the committed research outputs of M2 (forecasting), M3
(uncertainty) and M4 (decision simulation), plus bounded HYPOTHETICAL simulations with the
M4 linear-programming engine. It is a read-mostly modular monolith inside the `atlas`
package: no database, no message broker, no recomputation of any research experiment.

> **Read this first.** The API changes nothing about what the numbers mean.
> - The forecast is an unvalidated point forecast. On M2's confirmation period its MAE
>   over horizons 1-30 was 985 requests/day, with a bias of -256.
> - The M3 prediction intervals have the status **FAILED_CALIBRATION** (`failed_calibration`).
> - Every M4 operational parameter is **HYPOTHETICAL**.
>
> Every response says so in a machine-readable `integrity` block. Its
> `validated_recommendation` and `point_forecast_validated` flags are fixed to `false` by
> the schema itself. **Nothing the API returns is a validated staffing, budget or
> service-capacity recommendation.**

Contents:
1. Quick start
2. Architecture
3. Endpoint inventory
4. Conventions (envelope, integrity, provenance, errors)
5. Endpoints
6. Interactive simulations
7. Security
8. Reliability and operations
9. Performance (measured)
10. Testing
11. Scientific limitations
12. Integration requirements for M7
13. Readiness for M6 deployment

## 1. Quick start

```bash
uv sync --locked                       # installs fastapi, uvicorn (runtime) and httpx2 (tests)
uv run atlas api serve                 # http://127.0.0.1:8000 (loopback only)
curl -s http://127.0.0.1:8000/health/ready
curl -s http://127.0.0.1:8000/api/v1/forecasts?horizon_days=7
curl -s -X POST http://127.0.0.1:8000/api/v1/simulations \
     -H 'content-type: application/json' -d '{"parameters": {"discretionary_budget": 150000}}'
uv run atlas api openapi > docs/api/openapi.json   # regenerate the committed contract
uv run atlas api bench                 # measure latency and memory on this machine
```

Interactive documentation: `http://127.0.0.1:8000/docs` (Swagger UI) and `/redoc`. The
committed contract is [`docs/api/openapi.json`](api/openapi.json) (OpenAPI 3.1; a test
fails if it differs from the code).

### Configuration

Resolved like the rest of ATLAS: flag, then environment variable, then default. An invalid
value stops startup with exit code 2; it is never replaced by a default.

| Variable | Default | Range / format |
|---|---|---|
| `ATLAS_REPO_ROOT` | discovered from the working directory | checkout with `snapshots/` |
| `ATLAS_SNAPSHOT_ID` | `legacy-v0` | `[a-z0-9][a-z0-9._-]{0,63}` |
| `ATLAS_API_CORS_ORIGINS` | none (no cross-origin access) | comma-separated `http(s)://host[:port]`; no `*`, no path |
| `ATLAS_API_ALLOWED_HOSTS` | `localhost,127.0.0.1` | host names; `*.domain` allowed, bare `*` refused |
| `ATLAS_API_KEY` | none | ≥ 24 printable characters, no spaces; protects `POST /api/v1/simulations` |
| `ATLAS_API_MAX_BODY_BYTES` | 16384 | 1024-1048576 |
| `ATLAS_API_MAX_CONCURRENT_SIMULATIONS` | 2 | 1-16 worker processes |
| `ATLAS_API_SIMULATION_TIMEOUT_SECONDS` | 10 | 1-120 |
| `ATLAS_API_DOCS` | `true` | `true/false/1/0/yes/no/on/off`; `false` also hides `/openapi.json` |
| `ATLAS_API_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

`atlas api serve --host H --port P` binds to `127.0.0.1:8000` by default. It **refuses to bind
to any non-loopback address unless `ATLAS_API_KEY` is set**. Remote clients also need their
host name in `ATLAS_API_ALLOWED_HOSTS`. One uvicorn process serves the API; its simulation
workers are its own child processes.

## 2. Architecture

```
                  HTTP (uvicorn, one process)
                        │
  transport   RequestContextMiddleware   request id, Host check, body limit, security
              (outermost, pure ASGI)     headers, JSON access log, last-resort 500
              CORSMiddleware             explicit origins only
              routers/*.py               parameters, status codes, documented responses
              schemas.py                 Pydantic contracts (extra=forbid; requests strict)
              errors.py, dependencies.py one error body; API key, JSON-only, no unknown query
                        │
  application views.py                   response data + integrity + provenance
              reference.py               ReferenceStore: committed outputs, checked once at
                                         startup, immutable, read-only
              simulation.py              request → M4 configuration → worker process
              settings.py, logs.py       validated settings; structured logging
                        │
  domain      atlas (M0-M4, unchanged)   loader/series, decision_inputs, optimization_config,
                                         scenarios, decision_analysis, optimization_runner
```

**Dependency injection.** `create_app(settings, store=None, simulator=None)` builds the app.
Its services are on `app.state` and reach handlers through `Depends(get_store)`,
`Depends(get_simulator)` and `Depends(get_settings)`. Without injected services, the
lifespan does two things:
- it loads the reference store;
- it starts the simulation workers, and stops them on shutdown.

Tests inject one shared store and worker pool, or a deliberately broken store.

**Precomputed versus computed.** Every GET endpoint serves committed, verified outputs from
memory; nothing is recomputed per request. The only computation is
`POST /api/v1/simulations`: one bounded M4 scenario.

**What is loaded at startup** (about 1 s; [§9](#9-performance-measured)). Each item is a
*component* with its own readiness:

| Component | Loaded through | Checks |
|---|---|---|
| `snapshot` | `runner.load_series` | M0 integrity and contract validation of `legacy-v0` (fail-closed) |
| `forecast_and_uncertainty_status` | the reference M4 configuration, `optimization_runner.load_inputs`, `decision_inputs.load_m3_quantile_path` | every pin of the M4 provenance chain: M2 projection SHA-256 and recommending run, result fingerprint, M3 status through M3's own `_status_source`, the same data fingerprint |
| `m2_backtest` | the recommending run's `metrics.json` | the exact bytes whose SHA-256 the chain verified; result fingerprint of the run's outputs; configuration and data fingerprints |
| `m3_projection_intervals` | `projection_intervals.csv` | the SHA-256 pinned by the configuration; point predictions equal the M2 projection |
| `m4_reference_run` | `optimization_runner.inspect_optimization_command` (no recomputation) | every file against its manifest, result fingerprint, zero failed checks, configuration fingerprint equals the pinned configuration, M2 projection and M3 status equal the current ones, every CSV row labelled |
| `simulation` | the reference configuration and inputs | ready exactly when they are |

Files the API parses itself are read into memory once. They are hashed, compared with the
hash their run recorded, and parsed from those same bytes, so a file replaced after the
check is never served.

**Why simulations run in worker processes.** Every M4 solve calls
`warnings.catch_warnings(record=True)` (in `optimizer._linprog`), which is process-global.
With another thread emitting warnings, 300 of 300 solves failed in a probe, and concurrent
solves corrupt the warning filters. Threads also gave no speedup: the cost is mostly
Python-level certification.

So solves never run in an API thread. A pool of `max_concurrent_simulations` *spawned*
processes does them (spawned, not forked: the API process is multi-threaded):
- Each process solves one simulation at a time.
- There is no queue: a request that finds no idle worker gets **503 `simulations_busy`**
  with `Retry-After: 1`.
- A simulation that misses its deadline gets **504 `simulation_timeout`**. Its worker process
  is **killed and replaced**, so abandoned work never keeps running.
- API threads only wait on pipes, so metadata requests are never queued behind a solve
  (measured in §9).

## 3. Endpoint inventory

| Method | Path | operationId | Data | Cost | Statuses |
|---|---|---|---|---|---|
| GET | `/health/live` | `getLive` | process liveness | static | 200, 422 |
| GET | `/health/ready` | `getReady` | readiness of every component | static | 200, 503, 422 |
| GET | `/api/v1/system` | `getSystem` | versions, provenance, scientific status, limits, endpoint costs | precomputed | 200, 422 |
| GET | `/api/v1/models` | `listModels` | the seven M2 models, selection, recommendation and its tests | precomputed | 200, 422, 503 |
| GET | `/api/v1/forecasts` | `getForecast` | the 30-day M2 projection (`projected`) | precomputed | 200, 422, 503 |
| GET | `/api/v1/forecasts/history` | `getHistory` | observed daily demand (`observed`) | precomputed | 200, 422, 503 |
| GET | `/api/v1/forecasts/metrics` | `getForecastMetrics` | M2 backtest accuracy | precomputed | 200, 404, 422, 503 |
| GET | `/api/v1/uncertainty/status` | `getUncertaintyStatus` | M3 calibration status | precomputed | 200, 422, 503 |
| GET | `/api/v1/uncertainty/intervals` | `getUncertaintyIntervals` | M3 projection quantiles (not calibrated) | precomputed | 200, 422, 503 |
| GET | `/api/v1/scenarios` | `listScenarios` | the nine M4 reference scenarios | precomputed | 200, 422, 503 |
| GET | `/api/v1/scenarios/comparison` | `compareScenarios` | scenarios side by side with differences | precomputed | 200, 404, 422, 503 |
| GET | `/api/v1/scenarios/{scenario_id}` | `getScenario` | one scenario with daily plans | precomputed | 200, 404, 422, 503 |
| GET | `/api/v1/simulations/parameters` | `getSimulationParameters` | defaults and ranges of simulation inputs | precomputed | 200, 422, 503 |
| POST | `/api/v1/simulations` | `runSimulation` | one bounded HYPOTHETICAL simulation | bounded computation | 200, 401, 413, 415, 422, 500, 503, 504 |

The routes follow the suggested names. Two were added:
- `forecasts/history`: the dashboard needs the observed series next to the projection;
- `simulations/parameters`: a client can build its controls from the API's own defaults and
  ranges instead of copying them.

Scenario comparison is a sub-resource of `scenarios`.

## 4. Conventions

**Envelope.** Every data response is

```json
{"integrity": {...}, "provenance": {...}, "data": {...}}
```

`integrity` (schema `Integrity`):

| Field | Meaning |
|---|---|
| `record_type` | the ATLAS record type of the source (`future_projection`, `historical_backtest`, `hypothetical_decision_simulation`, ...) |
| `data_status` | `observed`, `projected`, `historical_evaluation`, `calibration_assessment`, `hypothetical_reference_run`, `hypothetical_simulation` or `metadata` |
| `point_forecast_validated` | always `false` (schema `const`) |
| `uncertainty_interval_status` | the M3 status read from the committed file: `failed_calibration` (one of four M3 values, or `unavailable`) |
| `uncertainty_decision_grade` | `false` |
| `parameter_provenance` | `HYPOTHETICAL` on every M4 result, otherwise `null` |
| `validated_recommendation` | always `false` (schema `const`) |
| `notices` | the labels ATLAS writes with these numbers: M2's record-type label, the M3 status guidance, the M4 simulation label, the DIAGNOSTIC and stress-path notices |

`provenance` gives the ATLAS and API versions, the snapshot and its data fingerprint, and
every committed source of the data. Each source has its repository-relative path, run id,
result and configuration fingerprints, and the SHA-256 of the file served.

**Units.** Requests/day for demand and service; requests for backlog; request-days for the
sum of end-of-day backlogs; staff hours; USD for cash. Penalties and the penalised score are
USD-*equivalent* notional weights, never cash. Field names keep M4's names. Each unit is in
the field's OpenAPI description.

**Errors.** One body for every failure:

```json
{"error": {"code": "not_found", "message": "unknown scenario 'unknown'",
           "request_id": "4dff6aefa9ea48ef8fbd39162546fc92",
           "details": [{"field": "scenario", "message": "known: normal, increased_demand, ..."}]}}
```

| Code | Status | When |
|---|---|---|
| `bad_request` | 400 | unparseable body (malformed JSON, invalid UTF-8) |
| `invalid_host` | 400 | `Host` header not in `ATLAS_API_ALLOWED_HOSTS` |
| `unauthorized` | 401 | simulation without the configured `X-API-Key` |
| `not_found` | 404 | unknown path, scenario, model or period |
| `method_not_allowed` | 405 | e.g. POST to a GET route |
| `payload_too_large` | 413 | body above `ATLAS_API_MAX_BODY_BYTES` (declared or streamed) |
| `unsupported_media_type` | 415 | simulation body not `application/json` |
| `validation_error` | 422 | schema violation, unknown query parameter, duplicate JSON key, bad date window |
| `simulation_rejected` | 422 | input M4 refuses (a capacity equal to the legacy demand percentile, a surge start after the horizon) |
| `simulation_failed` | 500 | no certified result (a failed certificate or plan check), or the worker died |
| `internal_error` | 500 | anything unexpected: generic message, traceback only in the server log |
| `artifact_unavailable` | 503 | the component behind the endpoint is not ready (see `/health/ready`) |
| `simulations_busy` | 503 | every worker busy; `Retry-After: 1` |
| `simulation_timeout` | 504 | deadline passed; the worker was replaced |

Every response, including errors, has an `X-Request-ID` header. A client-supplied
`X-Request-ID` matching `[A-Za-z0-9._-]{8,64}` is reused; anything else is replaced. The same
id is in error bodies and in the server log.

**Determinism.** GET responses are byte-identical between calls; only the `X-Request-ID`
header differs. A simulation's body is a function of its request:
- `simulation_id` is the SHA-256 of the canonical M4 configuration the request becomes;
- equal requests give byte-identical bodies;
- the compute time is in the `Server-Timing` header, not in the body.

## 5. Endpoints

Abbreviated real responses are shown; the complete schemas are in the OpenAPI document.

### `GET /health/live`, `GET /health/ready`

```json
{"status": "alive", "atlas_version": "0.5.0"}
{"status": "ready", "components": [{"component": "snapshot", "ready": true, "detail": null}, ...]}
```

`/health/ready` answers 503 with the same body when a component is not ready. The `detail`
is a safe summary, for example `"the M4 reference run failed inspection: file_altered"`,
with paths relative to the repository and no traceback.

### `GET /api/v1/system`

Returns:
- `snapshot`: `legacy-v0`, 2023-01-01..2026-06-30, 1,277 days;
- `components`;
- `scientific_status`: the forecast model and its confirmation MAE, the M3 status and
  statement, the M4 label, and fixed `false` flags;
- `limits`: body size, workers, deadline, whether a key is required, the history and
  horizon caps;
- `solver`: SciPy 1.18.1, HiGHS 1.12.0;
- `endpoints`: each endpoint's `cost` and whether it is interactive.

### `GET /api/v1/models`

Returns:
- the seven M2 models (family, procedure, parameters, spec fingerprint);
- each model's MAE over horizons 1-30 per M2 period;
- `selected_on_development` and `recommended`;
- the development `selection`;
- `primary_tests`, the two Holm-adjusted confirmation tests behind the recommendation.

H1, the recommended regression against the best baseline, has an MAE difference of
-127 requests/day. Its 95 % interval is -256 to +2, and its adjusted p is 0.054. Read it before
calling the model better. The integrity notices carry M2's backtest label and the legacy
4.93 % MAPE note.

### `GET /api/v1/forecasts?horizon_days=1..30`

```json
"data": {"model_id": "atlas_linear_regression", "record_type": "future_projection",
         "origin": "2026-06-30", "horizon_days": 3, "unit": "requests_per_day",
         "points": [{"target_date": "2026-07-01", "horizon": 1,
                     "prediction": 11123.683380141376, "status": "projected",
                     "federal_holiday": null}, ...],
         "accuracy_context": {"period": "confirmation", "bucket": "days_1_30",
                              "mae_requests_per_day": 984.92, "bias_requests_per_day": -255.92,
                              "bias_sign": "prediction - actual (negative = under-forecast)",
                              "wape_pct": 9.24, "n_predictions": 10080}}
```

- The predictions are the committed `projections.csv` values, exactly.
- `federal_holiday` flags the observed federal holidays (M1 rules). 2026-07-03 is
  Independence Day observed, and holiday days had markedly lower interval coverage in M3.

### `GET /api/v1/forecasts/history?start=YYYY-MM-DD&end=YYYY-MM-DD`

- Observed city-wide daily totals, each with `status: observed` and `federal_holiday`.
- Default window: the last 180 observed days.
- At most 1,500 days per request, within 2023-01-01..2026-06-30.
- Dates must be exactly `YYYY-MM-DD`.

Observed and projected days never mix in one response: history ends on 2026-06-30 and
the projection starts on 2026-07-01.

### `GET /api/v1/forecasts/metrics?model_id=&period=&scope=bucket|horizon`

- One row per model, period and horizon bucket (default `scope=bucket`) or horizon
  (`h01`..`h30`), with `n`, MAE, RMSE, bias (prediction - actual), bias %, WAPE, MASE and
  MAPE.
- The rows are copied from the recommending M2 run's `metrics.json`.
- It also returns the periods: development (selection), confirmation, and legacy_window
  (descriptive only).

### `GET /api/v1/uncertainty/status`

- `interval_status: failed_calibration`, `decision_grade: false`.
- The pre-registered statement and the exposure note: the evaluation period had already been
  used to confirm the point model.
- `failure_reasons`, for example *"95 % coverage outside +/-3 pp of 95 % in: days_8_14,
  days_15_30"*.
- The coverage evidence per level, with confidence limits and effective sample sizes.
- The exceedance statuses.
- The empirical share of outcomes at or below each nominal quantile: 0.879 at the
  nominal 0.9.

### `GET /api/v1/uncertainty/intervals?horizon_days=1..30`

- The sealed M3 method's marginal quantiles (`0.025` .. `0.975`) for each projected day, from
  the committed projection.
- Labelled with M3's projection label and the status guidance (*"not decision-grade"*).
- They are **not calibrated probabilities**.

### `GET /api/v1/scenarios`

One summary per M4 reference scenario:
- `feasibility`: `strict`, or `relaxed_terminal_target`;
- `plan_label`, for example *"one of several equally scored HYPOTHETICAL plans"* for
  `increased_costs`, and *"DIAGNOSTIC relaxed_terminal_target plan ..."* for the six
  infeasible scenarios;
- the reported plan's cash, request-days, terminal and peak backlog, overtime and surge;
- uniqueness, before and after tie-breaks;
- binding constraints;
- the diagnosis reasons.

It also carries the run id and its result and configuration fingerprints.

### `GET /api/v1/scenarios/{scenario_id}`

- `outcome`: the scenario exactly as `metrics.json` records it. A test checks every recorded
  value is served unchanged; added schema keys are `null`. That includes:
  - the reported plan with its certificate and independent check;
  - the strict programme;
  - the programme without the target;
  - the diagnosis;
  - the optimal faces;
  - the policy comparisons;
  - the shadow-price summary;
  - the frontier summary.
- `plans`: every plan variant from `scenarios.csv`.
- `daily_plans`: the daily schedule from `plans.csv`, for the reported plan and both policies.
- `hypothetical_parameters`: the run's HYPOTHETICAL parameters with their assumptions.
- `structural_assumptions`.

`scenario_id` must match `^[a-z][a-z0-9_]{0,63}$`; it is a dictionary key, never a path.

### `GET /api/v1/scenarios/comparison?scenario_id=...&baseline=normal&metric=...`

- Up to nine scenarios (default: all) and up to seven metrics (default: all).
- Each row has its values and the difference from the baseline. The baseline is included
  first.
- `penalised_score` is reported only for strictly feasible scenarios: a relaxed plan's score
  belongs to a different programme.
- The caveat says the differences are between HYPOTHETICAL scenarios, not expected outcomes.

### `GET /api/v1/simulations/parameters`

- Every simulation input with its unit, its default (the reference configuration's value) and
  that value's documented assumption.
- Its hard `minimum`/`maximum`, and whether `null` removes it.
- The demand options.
- The legacy demand percentile (11,608.75 requests/day), which a capacity must not equal.

## 6. Interactive simulations

`POST /api/v1/simulations` plans one HYPOTHETICAL city-wide unit with the M4 engine over the
first 1-30 days of the M2 projection.

### Request

```json
{
  "horizon_days": 14,
  "demand": {"basis": "point_forecast", "multiplier": 1.0, "shift_per_day": 0.0},
  "parameters": {"discretionary_budget": 150000, "surge_max_per_day": 900,
                 "terminal_backlog_max": null}
}
```

- **Every field is optional.** An omitted parameter takes the reference configuration's value,
  with its documented assumption.
- An explicit `null` removes a limit. This works only for `overtime_total_hours`,
  `surge_total`, `terminal_backlog_max` and `discretionary_budget`; for the others, `null`
  is a 422.
- **Strict validation:**
  - unknown fields, strings for numbers, booleans for numbers and fractional integers are
    rejected;
  - non-finite numbers (`NaN`, `Infinity`) are rejected;
  - duplicate JSON keys are rejected;
  - so is any body that is not `application/json`.
- **Ranges** (also served by `/simulations/parameters`):

| Parameter | Unit | Range |
|---|---|---|
| `horizon_days` | days | 1-30 |
| `demand.basis` | | `point_forecast` or `m3_marginal_quantile` (with `quantile` 0.75, 0.9 or 0.975: a labelled stress path, no probability) |
| `demand.multiplier` | ratio | 0.5-2 |
| `demand.shift_per_day` | requests/day | 0-5,000 |
| `initial_backlog` | requests | 0-200,000 |
| `regular_hours_per_day` | staff hours/day | 0-12,000 |
| `regular_productivity`, `overtime_productivity` | requests/staff hour | 0.1-10 |
| `overtime_max_hours_per_day` | staff hours/day | 0-5,000 |
| `overtime_total_hours` | staff hours | 0-150,000 or `null` |
| `surge_max_per_day` | requests/day | 0-50,000 |
| `surge_total` | requests | 0-1,500,000 or `null` |
| `surge_available_from_day` | planning day | 1-31, at most `horizon_days + 1` |
| `regular_wage`, `overtime_wage` | USD/staff hour | 0 or 0.01-1,000 |
| `surge_price` | USD/request | 0 or 0.01-1,000 |
| `backlog_penalty` | USD-equivalent/request-day | 0 or 0.01-1,000 |
| `terminal_penalty` | USD-equivalent/request | 0 or 0.01-10,000 |
| `terminal_backlog_max` | requests | 0-1,000,000 or `null` |
| `backlog_trigger` | requests | 0-1,000,000 |
| `discretionary_budget` | USD | 0-100,000,000 or `null` |

The ranges keep every programme at the scale the M4 solver layer was reviewed at:
- right-hand sides far below its `1e9` input bound;
- productivities ≥ 0.1;
- money and penalty rates either 0 or ≥ 0.01. HiGHS silently drops a nonzero coefficient of
  `1e-9` or less, which M4 rejects.

### What happens

1. The request becomes an M4 configuration: the pinned reference configuration with the
   client's values. Each value is labelled `HYPOTHETICAL`, with the assumption *"Value
   supplied by an API client for an interactive simulation"*. There is one scenario
   (`interactive`) and no sweeps, frontiers or shadow-price brackets.
2. **M4's own parser and scenario builder validate it.** The rule *"a demand percentile is
   never a capacity"* applies unchanged: a regular capacity within 1 % of 11,608.75
   requests/day is a 422 `simulation_rejected`.
3. A worker process runs `decision_analysis.analyse_scenario`, which computes:
   - the certified optimum and the tie-broken reported plan;
   - the programme without the target;
   - the uniqueness tests;
   - for an infeasible scenario, the diagnosis and the DIAGNOSTIC relaxed plan;
   - both baseline policies, with LP comparisons at equal cash and equal backlog.

   The result is serialised by the M4 runner's own builders (`_scenario_metrics`,
   `plans_frame`, `scenario_frame`).
4. **Fail closed:** if any programme fails a certificate or the independent plan check, the
   API returns 500 `simulation_failed` and no plan.

Shadow-price brackets (about 2.8 s per scenario), frontiers (about 2.1 s) and sweeps are
research analyses. They are served precomputed for the reference scenarios and never run on
request.

### Response (abbreviated)

```json
"data": {
  "simulation_id": "298d000cfc8a287ed2441b5d70f077e762ee5de1f05ad0076bf219ed4dbf2574",
  "label": "HYPOTHETICAL DECISION SIMULATION: ... Not a validated staffing, budget or service-capacity recommendation for any NYC agency.",
  "horizon_days": 7, "first_date": "2026-07-01", "last_date": "2026-07-07",
  "demand_basis": "point_forecast", "demand_notice": null,
  "parameters": [{"name": "initial_backlog", "value": 8000.0, "unit": "requests",
                  "provenance": "HYPOTHETICAL", "source": "reference_default",
                  "assumption": "Illustrative open backlog ..."}, ...],
  "outcome": {"feasibility": "relaxed_terminal_target",
              "binding_constraints": ["budget", "capacity (7 days)", "terminal_backlog_violated"],
              "reported_plan": {"certificate": {"holds": true, ...}, "independent_check_passed": true,
                                "evaluation": {"discretionary_cost": 20000.0, "overtime_cost": 20000.0,
                                               "surge_cost": 0.0, "regular_cost": 1232000.0,
                                               "backlog_request_days": 41955.26, "terminal_backlog": 6644.17, ...}},
              "diagnosis": {"label": "DIAGNOSTIC: not a plan for the stated constraints",
                            "diagnostic_min_discretionary_spend_to_reach_hypothetical_target_usd": 90511.2,
                            "reasons": ["... no plan spending less than 90,511 USD on overtime and surge reaches the HYPOTHETICAL terminal-backlog target (70,511 USD above the HYPOTHETICAL budget; DIAGNOSTIC, not a budget request)"]},
              "comparisons": [{"policy": "fixed_capacity", "relation_of_reported_plan": "...", ...}, ...]},
  "plans": [...], "daily_plans": [{"plan": "relaxed_terminal_target", "day": 1, "date": "2026-07-01",
                                   "demand": 11123.68, "served": 11750.0, "overtime_hours": 333.33,
                                   "surge_requests": 0.0, "backlog": 7373.68, ...}, ...],
  "structural_assumptions": [...], "limitations": [...]
}
```

That response is for
`{"horizon_days": 7, "parameters": {"discretionary_budget": 20000, "backlog_penalty": 3}}`.

**Reuse, verified.** A request equal to a configured reference scenario reproduces the
committed M4 reference result exactly. This holds for:
- `{}` (`normal`);
- `discretionary_budget: 150000` (`reduced_budget`);
- `demand.multiplier: 1.1` (`increased_demand`);
- `surge_available_from_day: 8` (`delayed_surge`);
- `demand.shift_per_day: 256` (`m2_bias_shift`);
- the 0.9 stress path (`m3_marginal_q90_stress`).

The reported plan, programmes, diagnosis, faces, comparisons and every value of `plans.csv`
are equal (`tests/test_m5_simulation.py`).

## 7. Security

The API is designed for **local development and protected deployment**. It has no user
accounts and no administrative endpoints. The only computation is the bounded simulation,
which can be protected with a key.

| Threat | Control |
|---|---|
| Unbounded computation | Strict ranges on every simulation input; one bounded scenario per request (no sweeps, frontiers or shadow prices); `max_concurrent_simulations` worker processes and no queue (503); a deadline after which the worker process is **killed** (504); uvicorn `limit_concurrency` 64 |
| Oversized or hostile bodies | `Content-Length` and streamed bodies capped at 16 KiB (413) before parsing; JSON only (415); duplicate keys (422); deep nesting and invalid UTF-8 give 400/422, never 500; strict Pydantic models (`extra=forbid`, no type coercion, no NaN/inf) |
| Filesystem access | No path is built from request data. Scenario ids, models and periods are regex-checked dictionary keys. Every file read is fixed by the pinned configuration and read once at startup. The API never writes (tested by hashing the repository before and after) |
| Shell, eval, outbound URLs | None in any request path. The only subprocess is the developer command `atlas api bench`, which starts this interpreter with fixed arguments |
| Information leakage | One error body; unexpected exceptions get a generic 500, with the traceback only in the server log under the request id. Readiness details have no absolute paths. No `Server` header |
| Host header and DNS rebinding | Explicit allowed hosts (default `localhost`, `127.0.0.1`); others get 400 |
| Cross-origin use | CORS off by default. Explicit origins only (no `*`), GET/POST, no credentials. A JSON content type is required for POST, so a cross-site form cannot submit a simulation without a preflight |
| Access control | Optional `ATLAS_API_KEY` for `POST /simulations`: `X-API-Key`, constant-time comparison, at least 24 characters, never logged or returned. `serve` refuses a non-loopback bind without it |
| Response handling | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, and `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` on JSON |
| Log injection and privacy | JSON lines with whitelisted fields only. No bodies, query strings, headers, keys or client addresses. Client request ids accepted only if `[A-Za-z0-9._-]{8,64}` |

**Assumptions and what is deferred to M6.**
- TLS termination, HSTS, per-client rate limiting and network exposure belong to the M6
  reverse proxy.
- GET endpoints are unauthenticated: they serve committed research outputs that are already
  in the repository.
- The API key is a single shared secret. It is not a user system.
- The Swagger UI page loads its scripts from a CDN in the viewer's browser. Set
  `ATLAS_API_DOCS=false` where that is unwanted.

## 8. Reliability and operations

- **Startup never fails on data.** A missing or altered artifact makes its component, and the
  components built on it, not ready. `/health/ready` answers 503 naming them; their endpoints
  answer 503 `artifact_unavailable`; everything else keeps serving.
- **Fail closed.** Any break in the M2/M3 provenance chain takes down the forecast,
  uncertainty, M4 and simulation components together, because they all rest on it. An edited
  M4 file or configuration takes down only the M4 reference component. An edited snapshot
  takes down everything except liveness and system metadata
  (`tests/test_m5_reliability.py`).
- **Immutable artifacts.** The store is built once, held in memory and never written. Each
  committed file is opened read-only, once.
- **Logging** (`atlas api serve`) writes one JSON object per line on stderr:
  - `ts`, `level`, `logger`, `message`, `event`, `request_id`;
  - for requests: `method`, `path` (without the query, truncated at 200 characters),
    `status`, `duration_ms`;
  - for simulations: `simulation_id`, `horizon_days`, `feasibility`, `compute_ms`;
  - component readiness at startup.

  Example:

```json
{"duration_ms": 312.4, "event": "request", "level": "INFO", "logger": "atlas.api", "message": "request", "method": "POST", "path": "/api/v1/simulations", "request_id": "…", "status": 200, "ts": "2026-10-15T10:42:00.123+00:00"}
```

- **Shutdown.** The lifespan stops the worker processes. They are also daemon processes, so
  they never outlive the server.

## 9. Performance (measured)

Measured with `uv run atlas api bench --requests 100 --simulations 20` on commit `e839f9a`:
- **Machine:** a 4-core Intel Xeon at 2.10 GHz, 16 GB RAM, Linux, Python 3.12.3, SciPy 1.18.1
  with HiGHS 1.12.0.
- **Server:** `atlas api serve` as its own process, with 2 simulation workers.
- **Client:** the same machine over loopback, Python's `http.client`, one connection per
  request.

Re-run the command to measure another machine; the numbers below are this machine's only.

**Startup.** The process takes 2.34 s from start until `/health/ready` answers 200. That
includes about 1 s to load and check every committed artifact (the snapshot validation alone
is about 0.9 s).

The first simulation took 1.49 s because its worker was still importing ATLAS. Workers
announce themselves when their imports are done. Import time is never counted against the
deadline.

**Read endpoints** (100 sequential requests each, after one warm-up request):

| Endpoint | Median | p95 | Max | Body |
|---|---|---|---|---|
| `/health/live` | 1.4 ms | 2.0 ms | 2.7 ms | 42 B |
| `/health/ready` | 1.7 ms | 2.1 ms | 3.3 ms | 0.4 KB |
| `/api/v1/system` | 2.5 ms | 2.9 ms | 3.1 ms | 6.8 KB |
| `/api/v1/models` | 2.0 ms | 2.8 ms | 6.9 ms | 8.9 KB |
| `/api/v1/forecasts` | 2.0 ms | 2.9 ms | 3.9 ms | 6.3 KB |
| `/api/v1/forecasts/history` (180 days) | 1.9 ms | 2.9 ms | 6.5 ms | 15.9 KB |
| `/api/v1/forecasts/history` (all 1,277 days) | 3.0 ms | 3.6 ms | 8.2 ms | 105.5 KB |
| `/api/v1/forecasts/metrics` (buckets) | 3.0 ms | 3.4 ms | 4.0 ms | 37.7 KB |
| `/api/v1/forecasts/metrics?scope=horizon` | 6.3 ms | 7.8 ms | 15.2 ms | 208.9 KB |
| `/api/v1/uncertainty/status` | 1.9 ms | 4.0 ms | 6.4 ms | 3.9 KB |
| `/api/v1/uncertainty/intervals` | 1.7 ms | 3.2 ms | 4.9 ms | 11.2 KB |
| `/api/v1/scenarios` | 2.5 ms | 2.9 ms | 3.7 ms | 12.5 KB |
| `/api/v1/scenarios/normal` | 2.8 ms | 3.1 ms | 6.8 ms | 59.2 KB |
| `/api/v1/scenarios/comparison` (all) | 2.4 ms | 3.0 ms | 4.1 ms | 8.7 KB |
| `/api/v1/simulations/parameters` | 2.0 ms | 3.5 ms | 5.3 ms | 7.5 KB |

**Simulations** (20 sequential requests each, warm workers). *Compute* is the worker's time
from `Server-Timing`; *total* is the client's.

| Request | Result | Compute median (max) | Total median (max) | Body |
|---|---|---|---|---|
| `{}` (reference defaults, 30 days) | strict | 289 ms (386) | 300 ms (396) | 57.5 KB |
| `discretionary_budget: 100000` | relaxed (diagnosis) | 334 ms (395) | 345 ms (406) | 58.4 KB |
| q0.9 stress path | relaxed (diagnosis) | 369 ms (493) | 382 ms (506) | 59.2 KB |
| 14 days, no terminal target | strict | 197 ms (243) | 207 ms (254) | 38.6 KB |

**Isolation.** `/api/v1/forecasts` had a median of 2.6 ms (p95 3.3 ms) when idle, and
2.2 ms (p95 3.6 ms) while both workers computed simulations continuously. Metadata is
not blocked by solves.

**Bounded concurrency.** Six simultaneous simulations against 2 workers gave two 200s and
four immediate 503 `simulations_busy` responses: no queue, no unbounded work.

**Memory (RSS).**
- The server process: 176 MiB (FastAPI and the reference data).
- Each simulation worker: 134-136 MiB (NumPy, SciPy, pandas and the ATLAS engine).
- The `multiprocessing` resource tracker: 12 MiB.

A default deployment (2 workers) therefore needs about 0.5 GiB.

**What is interactive.**
- Every GET endpoint answers in single-digit milliseconds and suits interactive use.
- A simulation takes about 0.2-0.5 s, enough for a debounced control, not for every
  keystroke.
- With the default 2 workers the API runs 2 simulations at a time, about 5 per second in
  total. A third concurrent request is refused, not queued.
- The 10 s deadline is more than 20 times the slowest measured simulation.
- The expensive research analyses never run on request. A full reference run takes about
  20 s and a scenario with shadow-price brackets and a frontier about 5 s; both are served
  precomputed.

## 10. Testing

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy
uv run pytest                                          # full suite with the 90 % coverage gate
uv run pytest tests/test_m5_*.py --no-cov              # the M5 tests only
```

| Module | What it checks |
|---|---|
| `test_m5_endpoints.py` | every GET endpoint against the committed files (read independently with `json`/`csv`); exact values; filters; windows; errors; determinism |
| `test_m5_simulation.py` | request semantics; reproduction of six reference scenarios exactly; property-based invariants (balance, capacity, limits, budget, target, cost arithmetic, labels) on random bounded inputs; HTTP validation of hostile bodies |
| `test_m5_security.py` | hosts, CORS, security headers, body limits (declared and streamed), JSON-only, duplicate keys, deep nesting, API key, generic 500 with a logged traceback, bind policy |
| `test_m5_concurrency.py` | a real uvicorn server and worker processes: parallel reads, bounded parallel simulations with identical results, metadata latency while every worker computes, deadline kill and replacement, busy refusal, a dying worker, uncertified results |
| `test_m5_reliability.py` | eleven kinds of damaged or missing artifacts; degraded startup; no writes |
| `test_m5_contract.py` | committed OpenAPI equals the code; every operation documents its errors; fixed integrity flags; request bounds equal the validation; units equal M4's; no promotional or probabilistic wording in anything the API says |
| `test_m5_settings_cli.py` | settings ranges, environment parsing, `atlas api serve/openapi/bench`, JSON logging, a real benchmark run |

CI runs the whole suite, including the M5 tests, on every push and pull request. It also
regenerates the OpenAPI document and requires it to equal the committed one.

## 11. Scientific limitations

The API inherits every limitation of M2-M4 and adds none of its own claims:
- The forecast is an unvalidated point forecast. Its accuracy context is historical (M2's
  confirmation period, already used to confirm the model), not prospective. The recommended
  model's lead over the best baseline is not significant after the Holm adjustment
  (p = 0.054).
- The M3 intervals **failed** their pre-registered calibration criteria. No interval,
  quantile or probability the API serves is decision-grade. Quantile stress paths are marginal
  per day, not joint, and carry no probability.
- M4 is deterministic, with one pooled city-wide unit and continuous quantities. **Every
  operational parameter is HYPOTHETICAL**, including the reference defaults offered to
  simulation clients.
  - The penalised score ranks plans; it is not a cost.
  - Relaxed plans and required spends are DIAGNOSTIC values, never budget requests.
  - Simulations do not compute shadow prices.
- Demand is the count of 311 requests created (intake), not workload. The legacy "planning
  capacity" is a demand percentile, never a capacity.
- The API serves the committed reference runs as they are. It does not re-run, re-select or
  adjust anything at request time, and it attaches the recorded status instead of improving it.

## 12. Integration requirements for M7

**Contract**
- Generate the client from [`docs/api/openapi.json`](api/openapi.json). The operationIds are
  stable (`getForecast`, `runSimulation`, ...).
- Pin the API version `v1` and treat unknown response fields as additive.

**Labels**
- Display `integrity.notices` with the data they belong to. Never present
  `data_status: projected` or `hypothetical_*` data as observed.
- Label M4 numbers HYPOTHETICAL, and relaxed plans DIAGNOSTIC.
- Show `uncertainty_interval_status` in upper case (`FAILED_CALIBRATION`) next to any interval
  or quantile band.

**Charts**
- Join history (`observed`, to 2026-06-30) and forecast (`projected`, from 2026-07-01) by
  date without merging their styles.
- Flag `federal_holiday` days.

**Simulation controls**
- Build the controls from `GET /api/v1/simulations/parameters`: defaults, ranges, units,
  assumptions and nullability. Send only the fields the user changed; omitted means the
  reference default.
- Debounce slider input. A simulation takes about 0.3 s of compute.

**Simulation errors**
- 503 `simulations_busy`: retry after `Retry-After`.
- 504: say the simulation was stopped.
- 422 `simulation_rejected`: show the message; it explains the M4 rule.

**Caching.** Responses say `Cache-Control: no-store`. Equal simulation requests give equal
results: the client may memoise by request, keyed by `simulation_id`.

**Deployment settings for the dashboard**
- Same origin as the API is simplest. Otherwise set `ATLAS_API_CORS_ORIGINS` to the
  dashboard's origin.
- Add `X-API-Key` only from a trusted server-side component: a browser cannot keep a
  secret.
- Correlate client and server logs with `X-Request-ID`.

## 13. Readiness for M6 deployment

What is in place:
- one process to supervise: `atlas api serve`, with `uvicorn` and a graceful shutdown;
- liveness and readiness probes;
- structured JSON logs on stderr;
- configuration from environment variables with hard ranges;
- refusal of unauthenticated public binds;
- a host allow-list;
- bounded memory, about 0.1 GiB per worker process (§9);
- no writes and no outbound network;
- a measured startup time;
- a reproducible lock (`uv sync --locked`).

What M6 must add:
- a reverse proxy with TLS, HSTS, request-rate limits and request-size limits;
- a non-root system user with a read-only checkout;
- a systemd unit with restart policy and resource limits;
- `ATLAS_API_ALLOWED_HOSTS` and `ATLAS_API_KEY`, the key from a secret store;
- log shipping and rotation;
- an uptime check on `/health/ready`.

`proxy_headers` is off, so client addresses are not trusted from `X-Forwarded-For`. The
access log does not record them.
