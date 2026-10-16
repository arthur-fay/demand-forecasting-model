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
> Every data response says so in a machine-readable `integrity` block. Its
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
14. Independent review

## 1. Quick start

```bash
uv sync --locked                       # installs fastapi, uvicorn (runtime) and httpx2 (tests)
uv run atlas api serve                 # http://127.0.0.1:8000 (loopback only)
curl -s http://127.0.0.1:8000/health/ready
curl -s 'http://127.0.0.1:8000/api/v1/forecasts?horizon_days=7'
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
| `ATLAS_API_ALLOWED_HOSTS` | `localhost,127.0.0.1,::1` | host names and IPv4/IPv6 addresses (IPv6 without brackets); `*.domain.tld` (at least two labels) allowed; bare `*`, `*.com` and malformed labels refused |
| `ATLAS_API_KEY` | none | ≥ 24 printable characters, no spaces; protects `POST /api/v1/simulations` |
| `ATLAS_API_KEY_FILE` | none | (M6) a file holding the key (one line); refused if others can read it, a group other than root can read it, or its group can write it (use 0600/0400; a systemd credential shows as 0440 group root, its ACL); exclusive with `ATLAS_API_KEY` |
| `ATLAS_API_MAX_BODY_BYTES` | 16384 | 1024-1048576 |
| `ATLAS_API_MAX_CONCURRENT_SIMULATIONS` | 2 (at most the CPUs available) | 1-16 worker processes, and no more than the CPUs available to the process: the deadline is wall-clock time, so a worker without its own CPU slows every simulation |
| `ATLAS_API_SIMULATION_TIMEOUT_SECONDS` | 10 | 1-120 |
| `ATLAS_API_DOCS` | `true` | `true/false/1/0/yes/no/on/off`; `false` also hides `/openapi.json` |
| `ATLAS_API_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `ATLAS_API_DASHBOARD` | `true` | (M7) `true/false/...`; serve the committed web dashboard (`web/dist`) at `/` next to the API ([DASHBOARD.md](DASHBOARD.md)) |

`atlas api serve --host H --port P` binds to `127.0.0.1:8000` by default. It **refuses to bind
to any non-loopback address unless `ATLAS_API_KEY` is set**. Remote clients also need their
host name in `ATLAS_API_ALLOWED_HOSTS`. One uvicorn process serves the API; its simulation
workers are its own child processes.

## 2. Architecture

```
                  HTTP (uvicorn, one process)
                        │
  server      server.py                  uvicorn with a request-read deadline per connection
                        │
  transport   RequestContextMiddleware   request id, Host check, body limit, in-flight cap,
              (outermost, pure ASGI)     security headers, JSON access log, last-resort 500
              JsonCORSMiddleware         explicit origins only; JSON preflight rejections
              routers/*.py               parameters, status codes, documented responses, cost
              schemas.py                 Pydantic contracts (extra=forbid; requests strict)
              errors.py, problems.py,    one error body and its codes; API key, JSON-only,
              dependencies.py            no unknown or repeated query parameter
                        │
  application views.py                   complete responses: data + integrity + provenance
              reference.py               ReferenceStore: committed outputs, checked once at
                                         startup, immutable, read-only
              simulation.py              request → M4 configuration → worker pool
              settings.py, logs.py       validated settings; structured logging
                        │
  worker      worker.py                  the worker process side (imports domain code only)
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

Each view returns its complete, typed response model (`ForecastResponse`, ...), and each
route is annotated with it, so strict MyPy checks every route against its contract.

**Precomputed versus computed.** Every GET endpoint serves committed, verified outputs from
memory; nothing is recomputed per request. The only computation is
`POST /api/v1/simulations`: one bounded M4 scenario.

**What is loaded at startup** (about 1 s; [§9](#9-performance-measured)). Each item is a
*component* with its own readiness:

| Component | Loaded through | Checks |
|---|---|---|
| `snapshot` | `runner.load_series` | M0 integrity and contract validation of `legacy-v0` (fail-closed) |
| `forecast_and_uncertainty_status` | the reference M4 configuration, `optimization_runner.load_inputs`, `decision_inputs.load_m3_quantile_path` | every pin of the M4 provenance chain: M2 projection SHA-256 and recommending run, result fingerprint, M3 status through M3's own `_status_source`, the same data fingerprint |
| `m2_backtest` | the recommending run's `metrics.json` and manifest | the exact bytes whose SHA-256 the chain verified; result fingerprint of the run's outputs; configuration and data fingerprints; the run's configuration (from its manifest) gives the recorded configuration fingerprint, and its pre-registered significance level (0.05) is read from it |
| `m3_projection_intervals` | `projection_intervals.csv` | the SHA-256 pinned by the configuration; point predictions equal the M2 projection; every quantile present, non-negative and ordered |
| `m4_reference_run` | `optimization_runner.inspect_optimization_command` (no recomputation) | every file against its manifest, result fingerprint, zero failed checks, configuration fingerprint equals the pinned configuration, M2 projection and M3 status equal the current ones, every CSV row labelled |
| `simulation` | the reference configuration and inputs | ready when they are **and** the M4 reference run is verified: the defaults a simulation starts from are then those of the committed run |

`/health/ready` adds a seventh entry, `simulation_workers`, from the running worker pool: ready
while at least one worker process runs and fewer than 3 consecutive simulations lost their
worker.

Files the API parses itself are read into memory once, hashed, compared with the hash their
run recorded, and parsed from those same bytes, so a file replaced after the check is never
served. Files read through the M4 loaders (the M2 projection, the M3 status and the quantile
paths) are hashed and then read by those loaders, exactly as in M4.

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
- A worker that dies is replaced; one whose replacement cannot start (no memory, no process
  or file slot) is retried on its next use, never left dead. Each lost worker is logged
  (`simulation_timeout`, `worker_crashed`, `worker_start_failed`) and counted in
  `/api/v1/system`.
- Workers ignore Ctrl+C (SIGINT reaches the whole process group) and, on SIGTERM, finish
  and answer the simulation in progress before leaving. The API process alone decides when
  a worker stops.
- API threads only wait on pipes, so metadata requests are never queued behind a solve
  (measured in §9).

## 3. Endpoint inventory

| Method | Path | operationId | Data | Cost | Statuses |
|---|---|---|---|---|---|
| GET | `/health/live` | `getLive` | process liveness | static | 200 |
| GET | `/health/ready` | `getReady` | readiness of every component | static | 200, 503 (`ReadyResponse`) |
| GET | `/api/v1/system` | `getSystem` | versions, provenance, scientific status, limits, workers, endpoint costs | precomputed | 200, 503 |
| GET | `/api/v1/models` | `listModels` | the seven M2 models, selection, recommendation and its tests | precomputed | 200, 503 |
| GET | `/api/v1/forecasts` | `getForecast` | the 30-day M2 projection (`projected`) | precomputed | 200, 503 |
| GET | `/api/v1/forecasts/history` | `getHistory` | observed daily demand (`observed`) | precomputed | 200, 503 |
| GET | `/api/v1/forecasts/metrics` | `getForecastMetrics` | M2 backtest accuracy | precomputed | 200, 404, 503 |
| GET | `/api/v1/uncertainty/status` | `getUncertaintyStatus` | M3 calibration status | precomputed | 200, 503 |
| GET | `/api/v1/uncertainty/intervals` | `getUncertaintyIntervals` | M3 projection quantiles (failed calibration) | precomputed | 200, 503 |
| GET | `/api/v1/scenarios` | `listScenarios` | the nine M4 reference scenarios | precomputed | 200, 503 |
| GET | `/api/v1/scenarios/comparison` | `compareScenarios` | scenarios side by side with differences | precomputed | 200, 404, 503 |
| GET | `/api/v1/scenarios/{scenario_id}` | `getScenario` | one scenario with daily plans | precomputed | 200, 404, 503 |
| GET | `/api/v1/simulations/parameters` | `getSimulationParameters` | defaults and ranges of simulation inputs | precomputed | 200, 503 |
| POST | `/api/v1/simulations` | `runSimulation` | one bounded HYPOTHETICAL simulation | bounded computation | 200, 400, 401, 413, 415, 422 (`simulation_rejected`), 500 (`simulation_failed`), 503, 504 |

Every operation can also answer 400 `invalid_host`, 422 `validation_error` (an unknown or
repeated query parameter) and 500 `internal_error`; every `/api/v1` operation can answer
503 `server_busy` and `artifact_unavailable`. The OpenAPI document lists, for each
operation and status, exactly these codes (a test checks that the errors observed on each
operation are documented on it). Not listed per operation: 405 for a method a path does
not serve, and 413 for a GET sent with an oversized body.

*Cost* is the `x-atlas-cost` extension of each operation in the OpenAPI document:
`static` (process state only), `precomputed` (committed results checked at startup) or
`bounded_computation` (one bounded solve in a worker). `GET /api/v1/system` lists the same
inventory, generated from the document. `HEAD` is also answered on `/health/live` and
`/health/ready` (for uptime checkers; not part of the contract).

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
| `uncertainty_interval_status` | the M3 status read from the committed file: `failed_calibration` (one of the three M3 statuses `decision_grade`, `failed_calibration`, `insufficient_evidence`, or `unavailable` when the status file could not be loaded) |
| `uncertainty_decision_grade` | `false` |
| `parameter_provenance` | `HYPOTHETICAL` on every M4 result, otherwise `null` |
| `validated_recommendation` | always `false` (schema `const`) |
| `notices` | the labels ATLAS writes with these numbers: M2's record-type label, the M3 status guidance, the M4 simulation label, the DIAGNOSTIC and stress-path notices |

`provenance` gives the ATLAS and API versions, the snapshot and its data fingerprint, and
every committed source of the data. Each source has its repository-relative directory
(`path`), run id, result and configuration fingerprints (`null` where the source records
none: the snapshot has no run), and `files`: the SHA-256 of each file the response was built
from, by file name in `path`, each verified at startup. For example, a scenario's detail
lists `metrics.json`, `plans.csv` and `scenarios.csv` of the M4 run. A response built from
the M3 projection (the intervals, the q0.9 reference scenario, a quantile stress simulation)
lists it as a source.

```json
{"kind": "m4_reference_run", "path": "docs/results/atlas-m4-optimization-v1",
 "record_type": "hypothetical_decision_simulation", "run_id": "atlas-m4-optimization-v1-…",
 "result_fingerprint": "0dc4eb0f…", "config_fingerprint": "02258d28…",
 "files": {"metrics.json": "edf8a9ec…", "plans.csv": "330f2223…", "scenarios.csv": "9ef8d047…"}}
```

**Units.** Requests/day for demand and service; requests for backlog; request-days for the
sum of end-of-day backlogs; staff hours; USD for cash. Penalties and the penalised score are
USD-*equivalent* notional weights, never cash. Field names keep M4's names. Each unit is in
the field's OpenAPI description.

**Errors.** One body for every failure:

```json
{"error": {"code": "not_found", "message": "unknown scenario 'unknown'",
           "request_id": "4dff6aefa9ea48ef8fbd39162546fc92",
           "details": [{"field": "scenario_id", "message": "known: normal, increased_demand, ..."}]}}
```

`details[].field` names the request field the same way in every layer: the dotted path
inside the body (`parameters.regular_wage`, `demand.quantile`), the query parameter
(`start`, `baseline`), or `<body>` for the body as a whole. A client can map every error to
a form control with that one rule.

| Code | Status | When |
|---|---|---|
| `bad_request` | 400 | the body cannot be read at all: invalid UTF-8, an integer beyond Python's digit limit, nesting beyond the parser's depth |
| `invalid_host` | 400 | `Host` header not in `ATLAS_API_ALLOWED_HOSTS`, or malformed (a port that is not 1-5 digits) |
| `cors_rejected` | 400 | CORS preflight from an origin, method or header that is not allowed |
| `unauthorized` | 401 | simulation without the configured `X-API-Key` |
| `not_found` | 404 | unknown path, scenario, model or period |
| `method_not_allowed` | 405 | e.g. POST to a GET route |
| `payload_too_large` | 413 | body above `ATLAS_API_MAX_BODY_BYTES` (declared or streamed) |
| `unsupported_media_type` | 415 | simulation body not `application/json` |
| `validation_error` | 422 | schema violation, malformed JSON (`<body>`), duplicate JSON key (with its path), unknown or repeated query parameter, a query integer not written as plain digits, bad date window |
| `simulation_rejected` | 422 | input M4 refuses (a capacity equal to the legacy demand percentile, a surge start after the horizon); `details` name the fields |
| `simulation_failed` | 500 | no certified result (a failed certificate or plan check), or the worker died |
| `internal_error` | 500 | anything unexpected: generic message, traceback only in the server log |
| `artifact_unavailable` | 503 | the component behind the endpoint is not ready (see `/health/ready`) |
| `simulations_busy` | 503 | every worker busy; `Retry-After: 1` |
| `server_busy` | 503 | more than 64 requests in flight (health probes excepted); `Retry-After: 1` |
| `simulation_timeout` | 504 | deadline passed; the worker was replaced |
| `http_error` | other | any other HTTP error the framework raises (not expected; kept so every error has a code) |

Every response the application produces, including errors, has an `X-Request-ID` header.
A client-supplied `X-Request-ID` matching `[A-Za-z0-9._-]{8,64}` is reused; anything else is
replaced. The same id is in error bodies and in the server log.

Two responses come from uvicorn itself and never reach the application, so they are plain
text without a request id: `400 Invalid HTTP request received.` for a request that is not
valid HTTP/1.1, and `503 Service Unavailable` when more than 1,024 connections are open at
once (a backstop far above the in-flight cap).

**Determinism.** GET responses are byte-identical between calls; only the `X-Request-ID`
header differs, and the worker counters in `/api/v1/system` once simulations have run. A simulation's body is a function of its request:
- `simulation_id` is the SHA-256 of the canonical M4 configuration the request becomes;
- equal requests give byte-identical bodies;
- the compute time is in the `Server-Timing` header, not in the body.

## 5. Endpoints

Abbreviated real responses are shown; the complete schemas are in the OpenAPI document.

### `GET /health/live`, `GET /health/ready`

```json
{"status": "alive", "atlas_version": "0.6.0"}
{"status": "ready", "components": [{"component": "snapshot", "ready": true, "detail": null}, ...,
                                   {"component": "simulation_workers", "ready": true, "detail": null}]}
```

`/health/ready` answers 503 with the same body when a component is not ready. The `detail`
is a safe summary, for example `"the M4 reference run failed inspection: file_altered"` or
`"the last 3 simulations lost their worker"`, with paths relative to the repository and no
traceback.

### `GET /api/v1/system`

Returns:
- `snapshot`: `legacy-v0`, 2023-01-01..2026-06-30, 1,277 days;
- `components`;
- `scientific_status`: the forecast model and its confirmation MAE, the M3 status and
  statement, the M4 label, fixed `false` flags, and the limitations (including the H1
  notice below);
- `limits`: body size, workers, deadline, whether a key is required, the history and
  horizon caps;
- `simulation_workers`: pool size, processes alive, idle workers, and counters of answered,
  timed-out and crashed simulations since startup;
- `solver`: SciPy 1.18.1, HiGHS 1.12.0;
- `endpoints`: every operation with its `cost` (generated from the OpenAPI document).

### `GET /api/v1/models`

Returns:
- the seven M2 models (family, procedure, parameters, spec fingerprint);
- each model's MAE over horizons 1-30 per M2 period;
- `selected_on_development` and `recommended`;
- the development `selection`;
- `primary_tests`, the two Holm-adjusted confirmation tests behind the recommendation, each
  with `significant_after_holm` (adjusted p below the pre-registered 0.05).

H1, the recommended regression against the best baseline, has an MAE difference of
-127 requests/day, an interval of -256 to +2, and an adjusted p of 0.054: it is **not**
significant after the Holm adjustment. The integrity notices of every response that serves
M2 numbers (`/models`, `/forecasts/metrics`, `/forecasts`, `/uncertainty/intervals`) and the
limitations in `/system` say so in a sentence computed from these numbers: *"On M2's
confirmation period the recommended model's lead over the best baseline (H1) is not
statistically established: MAE difference -127 requests/day (interval -256 to +2),
Holm-adjusted p 0.054, not below 0.05."* The notices of `/models` and `/forecasts/metrics`
also carry M2's backtest label and the legacy 4.93 % MAPE note.

### `GET /api/v1/forecasts?horizon_days=1..30`

```json
"data": {"model_id": "atlas_linear_regression", "record_type": "future_projection",
         "origin": "2026-06-30", "horizon_days": 3, "unit": "requests_per_day",
         "points": [{"target_date": "2026-07-01", "horizon": 1,
                     "prediction": 11123.683380141376, "status": "projected",
                     "federal_holiday": null, "holiday_related": false}, ...],
         "accuracy_context": {"period": "confirmation", "bucket": "days_1_30",
                              "mae_requests_per_day": 984.92, "bias_requests_per_day": -255.92,
                              "bias_sign": "prediction - actual (negative = under-forecast)",
                              "wape_pct": 9.24, "n_predictions": 10080}}
```

- The predictions are the committed `projections.csv` values, exactly.
- `federal_holiday` names the observed federal holiday (M1 rules): 2026-07-03 is
  Independence Day observed.
- `holiday_related` marks M3's holiday-related stratum: the actual and observed dates and
  the days before and after the actual date (2026-07-03, -04 and -05). Interval coverage was
  markedly lower on that stratum in M3: 64.1 % at nominal 80 % and 83.3 % at 95 %, against
  84.8 % and 94.3 % on ordinary days, over only 33 distinct target days.

### `GET /api/v1/forecasts/history?start=YYYY-MM-DD&end=YYYY-MM-DD`

- Observed city-wide daily totals, each with `status: observed`, `federal_holiday` and
  `holiday_related`.
- Default window: the last 180 observed days.
- At most 1,500 days per request, within 2023-01-01..2026-06-30. A date outside the snapshot
  is a 422 naming that parameter.
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
- The coverage evidence per level, with interval limits and effective sample sizes.
- The exceedance statuses.
- The empirical share of outcomes at or below each nominal quantile: 0.879 at the
  nominal 0.9.

### `GET /api/v1/uncertainty/intervals?horizon_days=1..30`

- The sealed M3 method's marginal quantiles (`0.025` .. `0.975`) for each projected day, from
  the committed projection, each with `holiday_related`.
- The status guidance (*"... not decision-grade"*) is the first notice. The label is the
  API's own: *"FUTURE PROJECTION WITH INTERVALS: marginal quantiles of each projected day
  from the sealed M3 interval method (built from past forecast errors), which FAILED its
  pre-registered calibration criteria (failed_calibration): they are not calibrated
  quantiles and carry no probability to rely on."* (M3's general projection label, which
  describes the method as calibrated, is not used here.)

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

It also carries the run id and its result and configuration fingerprints. The q0.9 stress
scenario's demand comes from the M3 projection, which is therefore listed as a source.

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
- `plans`: every plan variant from `scenarios.csv`, with its terminal target and violation.
- `daily_plans`: the daily schedule from `plans.csv`, for the reported plan and both policies.
- `hypothetical_parameters`: the run's HYPOTHETICAL parameters with their assumptions.
- `structural_assumptions`.

`scenario_id` must match `^[a-z][a-z0-9_]{0,63}$`; it is a dictionary key, never a path.

### `GET /api/v1/scenarios/comparison?scenario_id=...&baseline=normal&metric=...`

- Up to 32 scenario ids (default: all; repeats are merged) and up to seven metrics (default:
  all). The baseline is included first.
- Each row has the scenario's description and HYPOTHETICAL adjustments, its feasibility,
  `values_status` (`plan_meets_every_constraint` or `DIAGNOSTIC`), the terminal target and
  the reported plan's `terminal_violation`, its values, and the difference from the baseline.
- A difference is given **only when both plans meet every constraint**. A relaxed plan misses
  the target, so its values are DIAGNOSTIC; its difference from a feasible plan would read as
  a funding need, which it is not. With a relaxed baseline every difference is `null`.
- `penalised_score` is reported only for strictly feasible scenarios: a relaxed plan's score
  belongs to a different programme.
- The caveat says all this in one sentence a dashboard can show.

### `GET /api/v1/simulations/parameters`

- Every simulation input with its unit, its default (the reference configuration's value) and
  that value's documented assumption.
- Its hard `minimum`/`maximum`, and whether `null` removes it.
- The horizon range and its first date, and the demand options (bases, stress quantile levels,
  multiplier and shift ranges with units, the stress-path notice), all typed in the contract.
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

- **Every field is optional.** An omitted parameter takes the reference configuration's
  value. The response marks it `reference_default`, with a neutral assumption (*"Reference
  value of the M4 configuration: illustrative and HYPOTHETICAL, not an estimate ..."*), and
  keeps the configuration's own rationale in `reference_assumption`. That rationale
  describes the 30-day reference with every other value at its default (for example *"1.2 %
  below the forecast mean"*) and may not hold for the simulation at hand.
- **Horizon totals are not prorated.** `overtime_total_hours`, `surge_total` and
  `discretionary_budget` apply to the whole horizon as given; a reference default keeps its
  30-day value on a shorter horizon, and `limitations` then says so.
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
5. `limitations` are worded from this simulation's own values: for example the forecast's
   historical MAE (985 requests/day) against this simulation's largest daily overtime
   capacity, and which horizon totals were not prorated.

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
                  "assumption": "Reference value of the M4 configuration: illustrative and HYPOTHETICAL, ...",
                  "reference_assumption": "Illustrative open backlog ..."}, ...],
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
| Unbounded computation | Strict ranges on every simulation input; one bounded scenario per request (no sweeps, frontiers or shadow prices); `max_concurrent_simulations` worker processes and no queue (503); a deadline after which the worker process is **killed** (504) |
| Connection and request exhaustion | Every request (headers and body) must arrive within 10 s of the connection opening or of the previous response, otherwise the connection is closed, so idle or slow sockets cannot hold the server; at most 64 requests in flight, beyond which the API answers JSON 503 `server_busy` (health probes are never refused); a request whose body arrives slowly holds its slot for at most the 10 s read deadline; uvicorn's own connection cap (1,024) is only a backstop |
| Oversized or hostile bodies | `Content-Length` and streamed bodies capped at 16 KiB (413) before parsing; JSON only (415); duplicate keys at any depth (422), checked in linear time off the event loop; deep nesting and invalid UTF-8 give 400/422, never 500; strict Pydantic models (`extra=forbid`, no type coercion, no NaN/inf) |
| Ambiguous queries | Unknown query parameters and repeated values of a non-list parameter are 422 (never ignored or last-wins); integers must be plain digits (`+5`, `5.0`, `1_0` are refused); dates exactly `YYYY-MM-DD` |
| Filesystem access | No path is built from request data. Scenario ids, models and periods are regex-checked dictionary keys. Every file read is fixed by the pinned configuration and read once at startup. The API never writes (tested by hashing the repository before and after) |
| Shell, eval, outbound URLs | No shell, eval or outbound URL in any request path. The only processes started are the fixed-argument simulation workers (multiprocessing *spawn*, re-spawned after a timeout or crash) and the multiprocessing resource tracker; the developer command `atlas api bench` starts a server with fixed arguments |
| Information leakage | One error body; unexpected exceptions get a generic 500, with the traceback only in the server log under the request id. Readiness details have no absolute paths. No `Server` header |
| Host header and DNS rebinding | Explicit allowed hosts (default `localhost`, `127.0.0.1`, `::1`); bracketed IPv6 hosts are parsed, the port must be 1-5 digits, wildcards need two labels (`*.example.org`); others get 400 |
| Cross-origin use | CORS off by default. Explicit origins only (no `*`), GET/POST, no credentials; rejected preflights get the JSON `cors_rejected` error. A JSON content type is required for POST, so a cross-site form cannot submit a simulation without a preflight. Errors made before the CORS layer (413, invalid host, the last-resort 500, `server_busy`) still carry CORS headers for an allowed origin, so a dashboard can read them |
| Access control | Optional `ATLAS_API_KEY` for `POST /simulations`: `X-API-Key`, constant-time comparison, at least 24 characters, never logged or returned. It is checked by the outer middleware before the body is read (so an unauthenticated client cannot hold a request slot with a slow body) and again by the route. `serve` refuses a non-loopback bind without it |
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
  M4 file or an edited configuration value takes down the M4 reference component and
  simulations (whose defaults must be those of the verified run); a configuration that cannot
  be read takes down every component loaded through it (all but the snapshot). An edited
  snapshot takes down everything except liveness and system metadata
  (`tests/test_m5_reliability.py`).
- **Immutable artifacts.** The store is built once, held in memory and never written. Files
  are opened read-only; the API's own parsers read each file once (see §2).
- **Logging** (`atlas api serve`) writes one JSON object per line on stderr:
  - `ts`, `level`, `logger`, `message`, `event`, `request_id`;
  - for requests: `method`, `path` (without the query, truncated at 200 characters),
    `status`, `duration_ms`;
  - for simulations: `simulation_id`, `horizon_days`, `feasibility`, `compute_ms`;
  - component readiness at startup (`component_ready`, `component_not_ready`, with
    `component` and `duration_ms`);
  - every 5xx error the application answers, including `server_busy` and the last-resort
    500 (`api_error`, with its `code`), and `unhandled_exception` with the traceback;
  - `startup` (with `ready`), `simulation` (each answered simulation), `simulation_error`
    (an unexpected worker error, with its traceback) and `workers_not_started`;
  - worker events: `simulation_timeout` and `worker_crashed` (with `worker_pid` and
    `exit_code`), `worker_start_failed`, `simulation_failed`.

  Example:

```json
{"duration_ms": 312.4, "event": "request", "level": "INFO", "logger": "atlas.api", "message": "request", "method": "POST", "path": "/api/v1/simulations", "request_id": "…", "status": 200, "ts": "2026-10-15T10:42:00.123+00:00"}
```

- **Exit status.** `atlas api serve` exits 0 after a graceful shutdown, by Ctrl+C or SIGTERM,
  with JSON-only logs; 2 for invalid settings or a refused bind.
- **Shutdown.** The lifespan stops the worker processes. They are also daemon processes, so
  they never outlive the server; a worker whose server disappears sees its pipe close and
  leaves.

## 9. Performance (measured)

Measured with `uv run atlas api bench --requests 100 --simulations 20` on commit `ce5f0ec`:
- **Machine:** a 4-core Intel Xeon at 2.10 GHz, 16 GB RAM, Linux, Python 3.12.3, SciPy 1.18.1
  with HiGHS 1.12.0; quiet (load average below 1).
- **Server:** `atlas api serve` as its own process, with 2 simulation workers.
- **Client:** the same machine over loopback, Python's `http.client`, one connection per
  request. Body sizes are in bytes (1 KB = 1,000 bytes).

Re-run the command to measure another machine; the numbers below are this machine's only.

**Startup.** The process takes 2.84 s from start until `/health/ready` answers 200. That
includes loading and checking every committed artifact (about 1 s, the snapshot validation
alone about 0.9 s) and building the OpenAPI document. The first simulation took 1.42 s
because its worker was still importing ATLAS. Workers announce themselves when their imports
are done; import time is never counted against the deadline.

**Read endpoints** (100 sequential requests each, after one warm-up request):

| Endpoint | Median | p95 | Max | Body |
|---|---|---|---|---|
| `/health/live` | 1.2 ms | 1.5 ms | 2.2 ms | 42 B |
| `/health/ready` | 1.5 ms | 2.5 ms | 5.2 ms | 0.5 KB |
| `/api/v1/system` | 2.5 ms | 3.2 ms | 10.4 ms | 7.2 KB |
| `/api/v1/models` | 2.5 ms | 3.0 ms | 3.4 ms | 9.2 KB |
| `/api/v1/forecasts` | 2.3 ms | 2.8 ms | 3.1 ms | 7.4 KB |
| `/api/v1/forecasts/history` (180 days) | 2.5 ms | 3.6 ms | 6.9 ms | 20.2 KB |
| `/api/v1/forecasts/history` (all 1,277 days) | 2.8 ms | 4.2 ms | 6.6 ms | 136.1 KB |
| `/api/v1/forecasts/metrics` (buckets) | 3.1 ms | 4.4 ms | 12.7 ms | 37.9 KB |
| `/api/v1/forecasts/metrics?scope=horizon` | 6.9 ms | 10.8 ms | 11.4 ms | 209.1 KB |
| `/api/v1/uncertainty/status` | 1.7 ms | 4.6 ms | 17.1 ms | 3.9 KB |
| `/api/v1/uncertainty/intervals` | 2.1 ms | 6.0 ms | 12.2 ms | 12.4 KB |
| `/api/v1/scenarios` | 2.6 ms | 3.5 ms | 11.8 ms | 12.9 KB |
| `/api/v1/scenarios/normal` | 2.9 ms | 4.9 ms | 8.3 ms | 59.7 KB |
| `/api/v1/scenarios/comparison` (all) | 3.1 ms | 3.7 ms | 4.6 ms | 12.7 KB |
| `/api/v1/simulations/parameters` | 2.6 ms | 3.3 ms | 8.0 ms | 8.0 KB |

**Simulations** (20 sequential requests each, warm workers). *Compute* is the worker's time
from `Server-Timing`; *total* is the client's.

| Request | Result | Compute median (p95, max) | Total median (max) | Body |
|---|---|---|---|---|
| `{}` (reference defaults, 30 days) | strict | 327 ms (411, 488) | 337 ms (498) | 62.6 KB |
| `discretionary_budget: 100000` | relaxed (diagnosis) | 383 ms (518, 547) | 405 ms (563) | 63.3 KB |
| q0.9 stress path | relaxed (diagnosis) | 375 ms (473, 536) | 385 ms (546) | 64.7 KB |
| 14 days, no terminal target | strict | 196 ms (245, 249) | 205 ms (258) | 43.6 KB |
| a slow corner case (30 days, non-unique optimum) | strict | 803 ms (932, 976) | 818 ms (988) | 63.0 KB |

The last request is one of the slowest valid inputs the performance review found: a
non-unique optimal face adds the per-day ranges (about 60 more solves). Across the input
space, the review ran 1,644 random and corner-case valid requests in-process, under
contention from a second sweep: median 0.24-0.27 s, p95 0.69-0.74 s, slowest 1.5 s, all
certified. The 10 s deadline is therefore about 7-10 times the slowest simulation observed.

**Isolation.** `/api/v1/forecasts` had a median of 2.2 ms (p95 3.3 ms) when idle, and
1.8 ms (p95 3.2 ms) while both workers computed simulations continuously. Metadata is not
blocked by solves.

**Bounded concurrency.** Six simultaneous simulations against 2 workers gave two 200s and
four immediate 503 `simulations_busy` responses: no queue, no unbounded work.

**Memory (RSS).**
- The server process: 175 MiB (FastAPI and the reference data).
- Each simulation worker: 121-123 MiB (NumPy, SciPy, pandas and the ATLAS engine; no web
  stack).
- The `multiprocessing` resource tracker: 12 MiB.

A default deployment (2 workers) therefore needs about 0.43 GiB; each extra worker about
0.12 GiB, and one CPU (§1).

**What is interactive.**
- Every GET endpoint answers in single-digit milliseconds (p95 at most 11 ms) and suits
  interactive use.
- A simulation typically takes 0.2-0.4 s of compute and at worst about 1-1.5 s: enough for a
  debounced control (send on release, not on every slider step).
- With the default 2 workers the API runs 2 simulations at a time, a few per second in
  total. A third concurrent request is refused, not queued.
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
| `test_m5_endpoints.py` | every GET endpoint against the committed files (read independently with `json`/`csv`); exact values; holiday flags; H1 notice and Holm flags; sources and fingerprints; comparison rules (DIAGNOSTIC rows, no differences unless both plans are strict); the endpoint inventory equals the OpenAPI document; filters; windows; errors; determinism |
| `test_m5_simulation.py` | request semantics; neutral wording of reference defaults; limitations computed from the request; reproduction of six reference scenarios exactly; property-based invariants (balance, capacity, limits, budget, target, cost arithmetic, labels) on random bounded inputs; one field-naming rule for every error layer; HTTP validation of hostile bodies |
| `test_m5_security.py` | hosts (IPv6, ports, wildcards), CORS (JSON rejections, headers on middleware errors), security headers and the exact docs exemption, body limits (declared and streamed), JSON-only, duplicate keys in linear time, deep nesting, strict and non-repeating query parameters, history dates far out of range, the in-flight cap, HEAD probes, API key, generic 500 with a logged traceback, bind policy |
| `test_m5_concurrency.py` | a real uvicorn server and worker processes: parallel reads, bounded parallel simulations with identical results, metadata latency while every worker computes, idle and slow connections closed, deadline kill and replacement (logged), busy refusal, crashes counted and reported by readiness, recovery after failed restarts, Ctrl+C and SIGTERM, the HTTP mapping of uncertified and unexpected worker results (the worker's own fail-closed rule is in `test_m5_guards.py`), the API key checked before a slow body |
| `test_m5_reliability.py` | twelve kinds of damaged or missing artifacts; degraded startup; no writes |
| `test_m5_guards.py` | the guards ordinary damage never reaches: self-consistent forged M4 runs (a claimed validated recommendation, unlabelled rows), a file swapped after its check, cross-references between the M2, M3 and M4 runs, readiness over presence, and the worker failing closed on an uncertified result (mutation-checked, §14) |
| `test_m5_contract.py` | committed OpenAPI equals the code; every operation documents its errors and cost; every response field required; fixed integrity flags; optional API key; request bounds equal the validation; units equal M4's; the M4 wording rules (no unnegated "probability", no "recommend" outside a not-a-recommendation phrase or the M2 model recommendation, no "calibrated" unless negated, no promotional terms) over every string of every response and every description in the contract |
| `test_m5_settings_cli.py` | settings ranges, environment parsing, `atlas api serve/openapi/bench`, JSON logging, a real benchmark run |

CI runs the whole suite, including the M5 tests, on pushes to `main` and on pull requests
(and on demand). It also
regenerates the OpenAPI document and requires it to equal the committed one.

## 11. Scientific limitations

The API inherits every limitation of M2-M4 and adds none of its own claims:
- The forecast is an unvalidated point forecast. Its accuracy context is historical (M2's
  confirmation period, already used to confirm the model), not prospective. The recommended
  model's lead over the best baseline is not significant after the Holm adjustment
  (p = 0.054); every response that serves M2 numbers says so in its notices.
- The M3 intervals **failed** their pre-registered calibration criteria. No interval,
  quantile or probability the API serves is decision-grade. Quantile stress paths are marginal
  per day, not joint, and carry no probability.
- M4 is deterministic, with one pooled city-wide unit and continuous quantities. **Every
  operational parameter is HYPOTHETICAL**, including the reference defaults offered to
  simulation clients.
  - The penalised score ranks plans; it is not a cost.
  - Relaxed plans and required spends are DIAGNOSTIC values, never budget requests; the
    comparison endpoint gives no difference involving one.
  - Simulations do not compute shadow prices.
- Demand is the count of 311 requests created (intake), not workload. The legacy "planning
  capacity" is a demand percentile, never a capacity.
- The API serves the committed reference runs as they are. It does not re-run, re-select or
  adjust anything at request time, and it attaches the recorded status instead of improving it.

## 12. Integration requirements for M7

> **M7** implements these requirements: see [DASHBOARD.md](DASHBOARD.md). It adds one thing to
> the API: `atlas api serve` serves the verified dashboard build from memory at `/`, its
> pages, its hashed files and `/atlas-deployment.json` (GET and HEAD only, the dashboard's
> own Content-Security-Policy on HTML, gzip and ETags), before the API's routes; every other
> path, every API response and the contract are unchanged. `ATLAS_API_DASHBOARD=false` turns
> it off; a damaged build is logged (`dashboard_unavailable`) and not served. The section is
> kept as written for M5.

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
- Mark `holiday_related` days on interval bands (an M3 stratum with markedly lower coverage) and name
  `federal_holiday` days.
- Show comparison rows with `values_status: DIAGNOSTIC` as diagnostics, never as plan
  differences.

**Simulation controls**
- Build the controls from `GET /api/v1/simulations/parameters`: defaults, ranges, units,
  assumptions and nullability. Send only the fields the user changed; omitted means the
  reference default.
- Debounce slider input. A simulation typically takes 0.2-0.4 s of compute, at worst about
  1.5 s.

**Simulation errors**
- 503 `simulations_busy` or `server_busy`: retry after `Retry-After`.
- 504: say the simulation was stopped.
- 422 `simulation_rejected` or `validation_error`: show the message, and attach each
  `details[].message` to the control named by `details[].field`.
- Show `limitations` and the parameters' `reference_assumption` with the result.

**Caching.** Responses say `Cache-Control: no-store`. Equal simulation requests give equal
results within one deployment: the client may memoise by the canonical request body together
with `provenance.atlas_version` and the solver versions of `/api/v1/system` (an upgrade may
change results). `simulation_id` identifies only the resolved M4 configuration, not the
whole response: a value sent explicitly and the same value left to its default give
different ids (their assumptions differ), while removing a limit that the reference leaves
unlimited (`surge_total: null`) gives the same id with a different parameter `source`;
`0` and `-0.0` give the same id.

**Deployment settings for the dashboard**
- Same origin as the API is simplest. Otherwise set `ATLAS_API_CORS_ORIGINS` to the
  dashboard's origin.
- Add `X-API-Key` only from a trusted server-side component: a browser cannot keep a
  secret.
- Correlate client and server logs with `X-Request-ID`.

## 13. Readiness for M6 deployment

> **M6 phase A** implements what this section asked for: see
> [DEPLOYMENT.md](DEPLOYMENT.md). It also added five things to the API: `ATLAS_API_KEY_FILE`
> (the key as a systemd credential), systemd readiness and watchdog notifications in
> `atlas api serve` (`READY=1` after startup, `WATCHDOG=1` from the event loop; inert
> outside a `Type=notify` unit), workers that raise their own OOM score, the replacement of
> a worker that died while idle (by the readiness check, logged as `worker_died_idle`), and
> `atlas api serve --uds PATH`, which also listens on a Unix socket (nginx's way in). The
> section is kept as written for M5.

What is in place:
- one process to supervise: `atlas api serve`, with `uvicorn` and a graceful shutdown;
- liveness and readiness probes;
- structured JSON logs on stderr;
- configuration from environment variables with hard ranges;
- refusal of unauthenticated public binds;
- a host allow-list;
- bounded memory, about 0.12 GiB per worker process and 0.18 GiB for the server (§9);
- no writes and no outbound network;
- a measured startup time;
- a reproducible lock (`uv sync --locked`).

What M6 must add:
- a reverse proxy with TLS, HSTS, request-rate limits and request-size limits, which buffers
  requests and enforces header and body read timeouts and a per-client connection limit
  (the API closes slow connections after 10 s, but a single process cannot rate-limit
  clients);
- a non-root system user with a read-only checkout;
- a systemd unit with restart policy and resource limits; with the default
  `KillMode=control-group` the workers finish their current simulation on SIGTERM, and
  `KillMode=mixed` leaves their shutdown to the server;
- `ATLAS_API_ALLOWED_HOSTS` and `ATLAS_API_KEY`, the key from a secret store;
- log shipping and rotation;
- an uptime check on `/health/ready` (GET or HEAD), which also reports the worker pool.

`proxy_headers` is off, so client addresses are not trusted from `X-Forwarded-For`. The
access log does not record them.

## 14. Independent review

Seven independent reviewers examined the first complete M5 commit (`6f8b9b6`), each in its
own checkout, through one lens: scientific integrity, architecture, security (black-box and
white-box attacks on a running server), correctness and concurrency, tests (mutation testing:
107 planted mutants), performance and reproducibility, and documentation and contract
accuracy (on `02bcdd8`, after the first fixes).

| Lens | Major | Minor | Nit |
|---|---|---|---|
| Scientific integrity | 0 | 5 | 1 |
| Architecture | 0 | 9 | 6 |
| Security | 2 | 2 | 2 |
| Correctness and concurrency | 2 | 6 | 3 |
| Tests (mutation) | 5 | 8 | 1 |
| Performance | 0 | 4 | 1 |
| Documentation and contract | 0 | 11 | 7 |

No reviewer found a critical defect: no path presents FAILED_CALIBRATION intervals as
decision-grade or an M4 plan as a recommendation, every number the docs quote matched the
live output, and the documented reference reuse reproduced exactly. The nine major reports
(seven distinct problems) were:
- 64 idle connections locking out every endpoint (security, correctness);
- a quadratic duplicate-key check on the event loop (security, tests);
- a worker slot left dead after a failed restart;
- four test gaps: the worker's fail-closed rule, the guards against a self-consistent forged
  M4 run, the verified-bytes check, and provenance completeness.

Every finding was addressed (`02bcdd8` to `ce5f0ec`); the fixes are listed in the commit
messages, and each has a test. A skeptic then re-checked each major report against the
branch: six no longer reproduced, and three were test gaps, closed by
`tests/test_m5_guards.py`. Twelve mutants planted in the guards it covers were then run
against it: eleven were killed; the twelfth deletes one of two checks of the M2
configuration fingerprint (against the run's manifest and its `metrics.json`), and the other
check still refuses the run.
