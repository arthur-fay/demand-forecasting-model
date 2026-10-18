# M8 audit: what held up, and what could not be verified (baseline)

Positive evidence recorded by each lens on commit `194fa4a`, before any correction, and each lens's own list of what it could not verify in the audit environment. The overall assessment of each auditor is quoted at the end of its section.


## M5 API (`api`)

### Checks that held up (21)

- **Live contract equals the committed contract.** The /openapi.json from the live server on :9160, compared with docs/api/openapi.json in Python, gave `equal True`. It has 14 operations, each with x-atlas-cost (12 precomputed, 2 static, 1 bounded_computation).
- **Schemathesis property testing of every operation.** `uvx --from schemathesis schemathesis run ... --max-examples 40 --checks all --phases examples,coverage,fuzzing --seed 42` (v4.31.1) generated 729 cases across 14 of 14 operations. It found no server error and no breach of response schema, status code or content type. Only 2 failures, both 'API rejected schema-compliant request': (1) `start=2000-01-01` outside the snapshot dates; (2) `quantile` sent with basis point_forecast. Both are documented cross-field rules, not contract errors. Output: scratchpad/audit/api/schemathesis.txt.
- **API key handling on POST /api/v1/simulations.** Server on :9161 with a 28-character key. Missing, empty, wrong, prefix, Latin-1 (30 bytes of 0xE9), 8000-character and comma-joined keys: all 401 `unauthorized` with `WWW-Authenticate: X-API-Key`. A key with leading or trailing whitespace is accepted (h11 strips optional whitespace). With duplicate headers both layers use the first value: wrong,right gives 401; right,wrong gives 200. hmac.compare_digest is used (settings.py:121-125). The key appears in 0 of 170 log lines. The CLI refuses short keys ('at least 24 printable characters'), never echoing the value.
- **No path, method or header variant runs a simulation without the key.** `/api/v1/%73imulations` and `?x=1` gave 401. `/api/v1/simulations/` and `%2F` gave a 307 redirect (no execution). `//api…`, `/api/v1//…`, `/./`, `/../`, `;x`, `%00`, absolute-form, `/Simulations` and `/API/…` gave 404. HEAD, OPTIONS, PUT, PATCH, GET, DELETE, lowercase `post` and X-HTTP-Method-Override all gave 405. The route-level require_api_key is a second layer (dependencies.py:51-65).
- **Public-demo cannot compute at the API level, independently of nginx.** deploy/templates/atlas.service.in:28,32 set ATLAS_API_KEY_FILE and LoadCredential=api-key in every profile, so the API always requires the key. render.py raises 'only the private profile routes computation or readiness' for any other profile. routes.py PROFILE_ACCESS for public-demo is {liveness, read, dashboard}. The nginx key include is added only in compute locations (render.py _proxy_headers with_key). The API's own descriptor on :9161 says `simulations: disabled_api_key`.
- **Bind policy.** `atlas api serve --host` with 0.0.0.0, ::, 192.168.1.10, localhost.localdomain and ::ffff:127.0.0.1, each without a key: every one refused with 'refusing to bind … unless ATLAS_API_KEY protects the simulation endpoint'.
- **Simulation body validation.** NaN, Infinity, -Infinity and 1e309 gave 422 'finite number'. A 4301- or 5000-digit integer gave 400 bad_request. Negative values, productivity 0, rate 0.005, string numbers, `true`/`7.0` as horizon, extra fields at top level and nested, duplicate keys at top level and nested, array/null/string bodies and 5000-deep arrays all gave 422. 3000-deep objects gave 413. Invalid UTF-8 gave 400. text/plain, a missing content type and json-patch gave 415. A 17 KB body and a 40 KB chunked body gave 413. An unknown query on POST gave 422. No 500 in any case (validate_probe.py, chunked.py).
- **GET parameter validation and path traversal.** history: start>end, 0001-01-01, 9999-12-31, 2023-02-30, 2023-1-1, a time suffix, a '+' sign, fullwidth digits, an empty value, %00, a repeated start, 2000 unknown parameters and a 60 KB value all gave 422 with a field name. A 200 KB request line was reset by h11. horizon_days 0/31/-1/1e1/9999999 gave 422; `07` is accepted. scenario_id `..%2F..%2Fetc%2Fpasswd` and `../../etc/passwd` gave 404; `%2e%2e`, NORMAL, normal%00 and 65 characters gave 422 (pattern). 33 comparison ids gave 422 (max 32). get_probe.py.
- **Host allow-list and CORS.** Host evil.example, '127.0.0.1.', '[::1', ':abc', 'x@127.0.0.1', '127.0.0.1/x' and an empty Host gave 400 `invalid_host`. localhost, LOCALHOST and [::1]:80 gave 200. Rejected CORS preflights (bad origin, the null origin, DELETE, header x-evil) gave a JSON 400 `cors_rejected`. A 401 from the middleware carries ACAO only for the allowed origin.
- **No queue for simulations: overflow gets 503 at once.** 8 concurrent POSTs on :9161 (2 workers): 2 gave 200 (2.3–2.6 s) and 6 gave 503 `simulations_busy` with `Retry-After: 1` in 0.05–0.08 s.
- **The deadline kills and replaces the worker.** On :9162 (1 s deadline), a heavy valid input gave 504 `simulation_timeout` in 1.05 s. The log shows `simulation_timeout` with worker_pid and exit_code -9, and the worker pid changed (5655 to a new pid). /api/v1/system then reported timeouts=3 and alive=2, and the next request succeeded.
- **A worker crash mid-simulation is reported and counted.** Killing the busy worker with SIGKILL during 4 simulations gave 500 `simulation_failed` each time. /health/ready stayed 200 after 1 and 2 crashes and became 503 with 'the last 3 simulations lost their worker' after 3, as documented. An idle worker killed with SIGKILL was replaced by the next readiness probe (`worker_died_idle`, 12–24 ms).
- **Request-read deadline against slow clients.** An idle connection, partial headers, a short body (Content-Length 100, 1 byte sent), headers dribbled 1/s and a body dribbled 1/s were closed after 10.0, 10.0, 10.0, 11.0 and 11.0 s (slow_probe.py).
- **In-flight cap and memory under concurrent GETs.** 150 threads x 20 GETs on :9160: 1278 of 2400 non-health requests got JSON 503 `server_busy`; /health/live answered 600 of 600 with 200. Server RSS went from 188,628 KB to 189,848 KB (+1.2 MB). Worker RSS was 122–131 MB after 106 simulations (unchanged).
- **Read-only requests never trigger computation.** In-process instrumentation (instrument_gets.py) patched atlas.optimizer.linprog, prepare, build_scenarios, load_reference_store, worker.compute, SimulationPool.exchange and SpawnProcess.start. 750 GETs (15 endpoints including dashboard paths, 50 each) gave counters `{}`: 0 solver calls, 0 scenario builds, 0 process starts, 0 store reloads. One POST gave prepare=1, build_scenarios=1, exchange=1. 200 sequential GETs per endpoint: medians 1.6–22.6 ms under load average about 13.
- **Committed artifacts are verified at startup and the API fails closed.** tamper.py on copies of `git archive HEAD`:
- One digit changed in M4 plans.csv: m4_reference_run 'file_altered', /scenarios 503, POST 503.
- One digit changed in M2 projections.csv, M3 projection_intervals.csv or M2 metrics.json: the forecast component is down with 'SHA-256 differs', and every dependent component and endpoint answers 503.
- calibration_status.json edited from failed_calibration to decision_grade: refused ('not the one the run recorded').
- Snapshot daily_total.csv edited: everything except liveness and system answers 503.
- regular_wage changed in the configuration: 'the M4 reference run used a different configuration'.
- M4 manifest run_id changed: 'manifest_field_differs'.
- One digit changed in sensitivity.csv: 'file_altered'.
- report.md deleted: 'file_missing'.
- M2 full manifest run_id changed: refused.
- M3 projection manifest config_fingerprint changed: refused.
Readiness details show `<repo>` instead of absolute paths.
- **Error bodies and logs disclose nothing sensitive.** An injected RuntimeError whose message contains a path and 'ATLAS_API_KEY=abc' was answered with the generic 500 `internal_error`, the API CSP and the client's request id. The traceback appears only in the `unhandled_exception` log line. Across 8,536 log lines from 3 servers: no query strings, no key, no non-JSON lines, longest path 83 characters.
- **Simulations are deterministic.** The same q0.9 stress request three times gave identical SHA-256 of the full response body (3d597dc8…). regular_wage -0.0 and 0 give the same simulation_id (3d3dfbef3ae6e2f2).
- **Random valid corner cases give no 500.** corner_sweep.py: 250 random min/max/null corner inputs, 201 accepted and computed in-process. 201 ok, 0 failed, 0 error. Compute median 0.83 s, p95 2.4 s, max 4.06 s. Re-timed: 4.31–4.58 s while the reference request took 1.44–1.76 s (documented quiet median 0.33 s). This was under load average 15–17, so it is still within the 10 s deadline.
- **Targeted security tests.** `UV_PYTHON=3.12 uv run --frozen pytest tests/test_m5_security.py tests/test_m5_guards.py -q --no-cov -p no:cacheprovider` gave 83 passed in 75.74 s, exit 0. `git status --porcelain` was empty afterwards.
- **The disclosed limitations travel with the numbers in API responses.** /forecasts, /uncertainty/intervals, /models and /scenarios carry `point_forecast_validated: false`, `uncertainty_interval_status: failed_calibration` and the sentence that H1 is not significant after Holm (p 0.054). The M4 endpoints carry `parameter_provenance: HYPOTHETICAL` and `validated_recommendation: false`. The /system limitations list all three (except the archived-projection disclosure; see the MEDIUM finding).

### Not verifiable here (6)

- **Performance and deadline margins on a quiet machine (the docs' latency table, and 'slowest valid simulation 1.5 s, deadline 7-10x').** Load average was 13-17 on 4 cores during my session because other auditors ran concurrently. The reference simulation took 1.44-1.76 s against a documented 0.33 s. I could only show that the slowest corner case I found (4.3-4.6 s under contention) still meets the 10 s deadline.
- **HEAD and timeout behaviour through the real nginx (whether 405 passes through, and proxy_read_timeout against a slow worker import).** This belongs to the deployment lens. I did not start nginx and judged it only from render.py and the templates.
- **Systemd credential permissions on a real host (LoadCredential ACL shown as 0440 group root) and the effect of the worker OOM score.** There is no systemd service manager or VPS in this environment. The key-file mode checks were read in code only.
- **Timing side-channel on the API key comparison.** hmac.compare_digest is used (code-verified). Empirical timing measurement is meaningless under this machine's load.
- **Full test suite, test_m5_concurrency.py and test_m6_container.py.** The lead auditor runs these. I ran only tests/test_m5_security.py and tests/test_m5_guards.py (83 passed).
- **Request smuggling or desync with Transfer-Encoding plus Content-Length.** h11 accepted a request carrying both headers (200). Whether nginx normalises or rejects such a request before it reaches the API was not tested here (deployment lens).

### Auditor's assessment

> Overall, the M5 API is in good shape for a v1.0 private-first research demonstration. I found no CRITICAL or HIGH defect in this lens. I tested by reading the code and by attacking three live servers I started myself on 127.0.0.1: port 9160 had no key, 9161 had a key plus a CORS origin, and 9162 had a key and a 1 s deadline. Everything I tested held up:
> - **Contract:** the live /openapi.json is byte-equal to docs/api/openapi.json. Schemathesis 4.31.1 ran 729 cases across all 14 operations with every check enabled. It found no 5xx, no response that breaks the schema and no undocumented status. Its only two failures are documented cross-field rules (quantile only with basis m3_marginal_quantile; history dates within the snapshot).
> - **Authentication:** the key is compared in constant time (hmac.compare_digest) and checked twice, once in the middleware before the body is read and once in the route. Missing, empty, wrong, prefix, Latin-1, 8 KB and comma-joined keys all get 401. Path and method variants get 401, 404, 405 or a 307 redirect, never a simulation.
> - **Public-demo:** at the API level it cannot compute anything. The systemd unit always loads the key credential, the nginx renderer refuses compute routes outside the private profile, and the API enforces the key on its own.
> - **Input validation:** strict models reject every hostile input I tried. Oversized and streamed bodies are capped at 16 KiB.
> - **Resource limits and timeouts all work:** there is no queue (503 plus Retry-After), the deadline kill gives 504 and the worker is replaced, a slow request is cut at 10 s, the cap of 64 requests in flight holds with health probes exempt, and memory stayed flat under load.
> - **Read-only requests never compute:** an instrumented run of 750 GET requests made 0 solver calls, 0 scenario builds and 0 process starts.
> - **Integrity checks fail closed:** every committed numeric file, the calibration status, the snapshot and the configuration are hash-checked, and readiness drops to 503 when any of them is altered. Error bodies are generic, and the logs are JSON-only with no key or query string.
> - **Tests:** tests/test_m5_security.py and tests/test_m5_guards.py pass (83 passed in 75.7 s), and the repository stayed clean (empty `git status --porcelain`) after my session.
> 
> I report five real but bounded findings.
> 1. **MEDIUM, scientific honesty:** the API still labels the July 2026 forecast "FUTURE PROJECTION … No outcome has been observed", with no "archived / not live" notice. Today is 2026-10-16. The dashboard adds that disclosure itself, but direct API clients, Swagger and docs/API.md never see it.
> 2. **LOW, provenance:** the API serves the `run_id` from the M2 and M3 projection manifests without checking it. The M4 reference run recorded those manifests' hashes, but `_load_m4` never compares them. This contradicts the "fail closed on any break in the provenance chain" claim in API.md §8.
> 3. **LOW:** HEAD on every versioned GET returns 405, while the nginx allow-list passes HEAD "as the API answers it".
> 4. **LOW:** the worker pool hands the next simulation to a just-replaced worker that is still importing, even when a warm worker is idle. The next request takes 4.4–4.6 s instead of about 0.7 s, and that startup wait is limited only by the 120 s startup allowance, not by the deadline.
> 5. **LOW:** /docs and /redoc are on by default. They load floating-major CDN bundles (swagger-ui-dist@5, redoc@2) with no SRI and no CSP, on the same origin as the API and the dashboard.
> 
> Minor observations that are not worth a finding:
> - A trailing slash (`/api/v1/simulations/`, `/api/v1/forecasts/`) gets a 307 with an empty body and a Location built from Host. Behind nginx such paths are 404s, so this only matters locally.
> - uvicorn answers malformed requests (a space in the target, a duplicate or missing Host, Content-Length -1) with plain-text `400 Invalid HTTP request received.`, outside the single JSON error shape.
> - A Content-Type of `application/json; charset=latin-1` is accepted and the body is read as UTF-8.
> - A Host with port 99999 is accepted (the port is not range-checked; harmless).
> - `GET /api/v1/system` still reports `milestone: "M5"` while the API version is 0.7.0 and the project is at M7/M8.
> - Duplicate X-API-Key headers use the first value, consistently in both layers.
> - `Vary: Origin` is sent even with CORS off.
> - On this machine, under load average 13–17 from concurrent auditors, the slowest valid corner case I found took 4.3–4.6 s of compute. That is still within the 10 s deadline, but I could not check the documented quiet-machine figures (slowest 1.5 s).
> 
> My scratch scripts are in <audit-scratch>/api/: raw.py, auth_probe2.py, validate_probe.py, get_probe.py, chunked.py, conc_probe.py, timeout_probe.py, lifo_probe.py, crash_probe.py, slow_probe.py, flood.py, get_timing.py, instrument_gets.py, tamper.py, forged_show.py, err500.py, corner_sweep.py and slow_case.py. Their outputs are in schemathesis.txt, tamper_results.txt, corner_sweep.txt, pytest_security.txt and server916*.log. All three servers are stopped and ports 9160–9162 are free.

## M6 deployment (`deploy`)

### Checks that held up (13)

- **Requested deployment test files: tests/test_m6_render.py, test_m6_proxy.py, test_m6_secrets.py, test_m7_deploy.py (ATLAS_REQUIRE_DEPLOY_TOOLS=1, /usr/sbin on PATH, --no-cov -p no:cacheprovider).** 134 passed in 149.82s, exit 0 (log: <audit-scratch>/deploy/pytest_requested.log)
- **systemd sandboxing of rendered units (systemd 255, same major version as Ubuntu 24.04).** systemd-analyze security --offline=yes: atlas.service 0.9 SAFE; atlas-monitor.service 1.3 OK; atlas-alert@ 1.3 OK. Unit has User=atlas, NoNewPrivileges, empty CapabilityBoundingSet/AmbientCapabilities, ProtectSystem=strict, PrivateUsers/Devices/IPC/Tmp, SystemCallFilter=@system-service ~@privileged @resources, IPAddressDeny=any + IPAddressAllow=localhost, MemoryHigh/MemoryMax/TasksMax per profile, UMask=0077, InaccessiblePaths for credentials, nginx dir and deploy state. systemd-analyze verify reports only the missing /opt/atlas/current ExecStart binaries (expected outside a server).
- **Shell scripts.** shellcheck 0.9.0 -S style on deploy/scripts/bootstrap-host.sh and build-release.sh: no findings; bash -n OK. build-release.sh: set -Eeuo pipefail, 40-hex commit enforced, --repo restricted to https:// file:/// or an absolute path, flock build lock, staging 0700, cleanup trap, checks run as atlas via setpriv --no-new-privs with env -i
- **nginx -t on rendered sites (package Renderer, own -p prefix).** private and public-demo TLS sites: 'test is successful' (nginx 1.24) in <audit-scratch>/deploy/probe/nginx-*/
- **Live nginx behaviour on ports 9200/9201 (probe_nginx.py, echo upstream on a Unix socket).** private: no-auth GET of /, /simulator, /health/live, /health/ready, /atlas-deployment.json, /favicon.svg, /api/v1/system, /nope all 401; wrong password 401; with account 200. POST /api/v1/simulations: upstream received only host, x-request-id, connection, content-type, x-api-key (the proxy's key; the client's X-API-Key, Cookie, Origin and X-Forwarded-For were dropped). Unknown SNI: TLS handshake refused. Right SNI with foreign Host: no answer (444). HTTP: 301 to https with path and query kept; unknown Host on :80: 444. Responses carry HSTS, X-Frame-Options DENY, CSP default-src 'none', Cache-Control no-store, Server: nginx (no version). public-demo: POST and GET /api/v1/simulations, /api/v1/system, /health/ready, /docs, /openapi.json are JSON 404; /health/live, /api/v1/forecasts and / are 200; descriptor is {operational_detail:false, profile:public-demo, simulations:disabled_public_demo}
- **CSRF surface of the proxy-injected key (private profile).** With the real FastAPI app (TestClient), POST /api/v1/simulations with a valid key returns 415 without a Content-Type and with text/plain; only application/json is accepted. A cross-site simple request therefore cannot run a simulation, and nginx routes no OPTIONS preflight (405).
- **Rollback consistency (automatic and explicit), the crucial check.** Code path: releases.py _restore_good -> _start(restore) -> install(restore) runs publish() of the restored release's copy, renders the site from that release (Renderer.for_release -> layout.dashboard(<restored id>)) and runs nginx -t; _serve then restarts the API on the switched link and reloads nginx. The API loads web/dist into memory from its resolved repo root (config.load_settings resolves; api/dashboard.read_verified). test_a_rollback_moves_the_dashboard_with_the_api and test_an_explicit_rollback_renders_the_previous_dashboard pass. Exception: recover (finding 1)
- **Interrupted activation at each step (analysed with FakeHost scripts and code reading).** Interrupt before the switch (during publish, write_files, nginx -t or daemon-reload): recover sees current != pending, restores the good state, re-installs, restarts, reloads, verifies: consistent. Interrupt after the switch but before the restart: verification's 'process release' check (cwd) fails, so the good state is restored: consistent. Interrupt after the nginx reload: recover verifies: consistent. Inconsistent only when the interrupt falls between the restart and the nginx reload (finding 1). Leftover .new-/.old- dashboard copies and .build-* directories are removed by prune.
- **Credentials.** Key: secrets.token_hex(32), written atomically (mkstemp, fchmod 0600, fchown before rename) to /etc/atlas/credentials/api-key 0600 (directory 0700) and to nginx simulation-key.conf 0600 root, checked to match with hmac.compare_digest. The service gets it only via LoadCredential plus ATLAS_API_KEY_FILE=%d/api-key (a path, not the key). htpasswd is root:www-data 0640 with $6$ hashes; passwords go to openssl on stdin; weak schemes are refused. deploy.env refuses secret-looking keys. .env.example and deploy.env.example contain no secrets, and the default profile is tunnel.
- **Release build reproducibility (simulated build-release.sh dependency and manifest steps, run twice at the same path with separate uv caches; pinned uv 0.11.32 equals .github/ci-requirements.txt).** uv sync --locked --no-dev succeeded. All 44 locked packages have wheels (no sdist builds). The project build backend was uv's built-in one (no uv_build download in the cache). Identical across both builds: source fingerprint 2d23ef9a..., research fingerprint bf6b3064..., dashboard fingerprint 824a73f0..., lock sha, runtime packages, scientific status. The manifest covers web/dist through source_files and the dashboard record. Only environment_fingerprint differed (finding 3).
- **Atomicity and TOCTOU.** write_files: mkstemp in the target dir, fsync, chmod/chown, rename. switch() and enable_site(): new symlink renamed over the old. publish(): staging dir, O_EXCL files, per-file hash re-check against the build manifest while copying (no TOCTOU on the root-owned read-only source), verified, then renamed. history append is fsynced. Every target directory is root-owned, so there is no unprivileged symlink race.
- **Failure handling before the switch.** nginx -t failure, a daemon-reload failure, or a write OSError (disk full) restores the previous files and the site link (tests pass). A missing or mismatched credential, a missing or other-readable TLS key, a release file writable by group/others, a set-id bit, an escaping symlink or an altered dashboard is refused before anything changes (Deployment.check).
- **Resource limits per profile.** low: 1 worker, MemoryHigh 400M, MemoryMax 512M, TasksMax 96, 12 simulations/min burst 3; standard: 2 workers, 640M/768M, 128, 30/min burst 6; monitor MemoryMax 96M, CPUQuota 25%; journald capped 200M/500M, 30 days. Matches DEPLOYMENT.md section 3 and the rendered units.

### Not verifiable here (6)

- **Real VPS behaviour (M6 phase B): DNS, Let's Encrypt HTTP-01 issuance and renewal with the deploy hook, ufw on a real interface, provider kernel and CPU flags (x86-64-v2), IPv6 on a public network, a real reboot, unattended-upgrades replacing nginx or Python under a running release, performance and watchdog margins on a shared 1-vCPU server with swap.** No server, DNS or certificate authority may be contacted; only local loopback experiments were possible
- **End-to-end behaviour under systemd as PID 1 (LoadCredential, PrivateUsers with the Unix socket, MemoryDenyWriteExecute with numpy/scipy/HiGHS, Type=notify and watchdog, OOMPolicy, KillMode=mixed), and tests/test_m6_container.py.** Reserved for the lead auditor (30+ minute container job); I only ran systemd-analyze offline on rendered units
- **The real build-release.sh as root (chown root:atlas, setpriv checks as atlas, the M4 byte-for-byte reproduction, `git fetch` from GitHub).** It must not touch /opt or /var on this machine, and GitHub fetch is not an allowed external service; I simulated only the uv sync --locked and manifest steps in scratch
- **The exact signal timing of a lost SSH session during `sudo atlas-deploy activate` (SIGHUP while systemctl restart blocks).** Simulated by raising SystemExit inside the fake nginx_reload. That systemd's restart job survives the killed systemctl client is standard systemd behaviour, but I did not observe it on a real host here
- **Browser handling of cached HTTP Basic credentials on cross-site requests, Firefox and Safari.** Only Chromium is installed. The CSRF conclusion rests on the API's 415 for non-JSON bodies and nginx not routing OPTIONS, both verified
- **Monitor and alert hook delivery (mail or webhook) and the alert rate under a sustained outage.** Needs a running systemd host and an operator-installed hook

### Auditor's assessment

> The M6/M7 deployment package is well engineered for a private research demonstration. Nothing I found would expose the system or compromise its integrity. Automatic and explicit rollbacks keep the browser assets and the API on the same release: `_restore_good` -> `_start(restore)` -> `install(restore)` publishes and renders the restored release's own site, and `_serve` reloads nginx. The API reads its dashboard into memory at startup from its resolved repo root. The sandbox is strong (atlas.service exposure 0.9 SAFE, monitor and alert unit 1.3). In a real nginx 1.24 the private profile asks for an account on every path I probed, including the dashboard pages, the favicon, the descriptor and both health routes. The public-demo profile answers 404 for POST/GET /api/v1/simulations, /api/v1/system, /health/ready and the docs. The proxy drops client keys, cookies and forwarding headers and adds the simulation key itself. The four requested test files pass: 134 passed.
> 
> I am trying to disprove readiness, and I found one real defect on the crucial consistency path:
> - **Interrupted activation, MEDIUM.** Suppose an activation is interrupted (lost SSH session or Ctrl-C) after `systemctl restart atlas` but before `systemctl reload nginx`. `atlas-deploy recover` then marks it ACTIVATED and saves it as the last verified state, but it never reloads nginx. nginx keeps serving the previous release's dashboard copy, routes and configuration in front of the new API. This includes a new ALLOW_CIDRS that is never enforced. I reproduced this with the package's own Deployment.
> 
> A latent rollback hazard:
> - **Cross-version rollback, MEDIUM.** Verification and rendering use the running tool's dashboard constants (DASHBOARD_CSP, descriptor format, headers), not the restored release's recorded CSP. The first future upgrade that changes the CSP and then fails to verify will fail its automatic restoration. The result is rollback_failed, with the service stopped and disabled.
> 
> Three LOW items:
> - The environment fingerprint is not reproducible across two builds of the same commit.
> - The IPv6 default is `true`, which contradicts the guide, and per-client limits are per /128 address.
> - `render --apply` installs a site for a non-current release without the lock, a history record or verification.
> 
> Neither MEDIUM changes published results. Both are small fixes, and both are deferrable only with an explicit note. I recommend fixing the recover issue before v1.0, because phase B depends on recover being correct.
> 
> Minor observations, not reported as findings:
> - **Halted service still serves the dashboard.** After `_halt` (failed_and_stopped or rollback_failed), the nginx site stays enabled. It keeps serving the halted release's verified static dashboard, with JSON 503 for the API. The claim "nothing unverified serves" is strictly true only of the API.
> - **Mixed code after switch.** The service's `sys.path` reaches third-party packages through the unresolved `/opt/atlas/current/.venv` link, while its own src comes from the resolved release path (atlas.pth). After the switch and before the restart, an old process or a simulation worker it spawns could therefore load the new release's libraries with the old code. The window is under a second normally, or lasts until recover in an interrupted state. Recover catches it through the process-cwd check.
> - **release.json write is not atomic.** It uses `write_manifest` -> `write_text`, and `last-good.json` is replaced without fsync. Both windows are narrow.
> - **Doc drift.** The verification list in DEPLOYMENT.md section 8 omits the M7 dashboard check, although section 19 mentions it.
> - **Test container base image not pinned.** deploy/test/Dockerfile uses `ubuntu:24.04` without a digest (test-only).
> - **Wrong-method requests skip rate limits.** A 405 from the method `if` is answered before auth and before rate limits (documented). It also lets unauthenticated clients tell routed paths (405) apart from unrouted ones (401). The contract is public, so this reveals nothing new.
> - **Weak chosen passwords pass.** The 20-character minimum for chosen passwords has no entropy check (e.g. 20 identical characters is accepted).
> - **Key rotation window.** If the API restarts between `secrets rotate` and the next activation, it holds the new key while nginx (not yet reloaded) still sends the old one. Simulations through nginx then get 401 until the operator activates, as documented.
> - **Build trust holds.** Commits are fetched over HTTPS and SHA-checked (unsigned, as documented). Wheels are pinned by hash in uv.lock. The pinned uv 0.11.32 satisfies the project's unpinned `uv_build>=0.11,<0.14` build backend with its built-in backend: no download was observed.
> 
> All my scripts, rendered files and logs are in <audit-scratch>/deploy. I did not change anything in the repository; its git status is clean apart from ignored caches that already existed.

## M0-M1 data (`data`)

### Checks that held up (16)

- **Every SHA-256, byte size, header, row count and date range in snapshots/legacy-v0/manifest.json, recomputed independently.** Ad-hoc hashlib/csv script over all 16 tables and 7 artifacts: 0 mismatches. Row total is 80,582, matching DATA_SNAPSHOT.md:27. All 16 CSVs are byte-identical (git blob) to origin commit db46703. .gitattributes marks *.csv, *.ipynb, *.sql and snapshots/** as -text. Base-table hash prefixes are pinned in tests/test_snapshot.py:31-34.
- **Provenance and possible extraction truncation.** The manifest records dataset erm2-nwe9, endpoint, window 2023-01-01..2026-07-01 (exclusive), extracted_at=null and completeness=incomplete. The notebook (cells 1-3) shows the exact SoQL queries with $limit 5000 (daily_total returned 1,277 rows) and 500000 (segments returned 76,458 rows), so neither query hit its row limit. The 'earliest timestamp 2026-07-17' claim holds: pbix zip members are dated 2026-07-17/18, the PDF CreationDate is 20260718131055, and the commit is 2026-07-18.
- **Temporal coverage.** coverage.py results:
- 1,277 rows, 0 duplicate dates, 0 missing dates, sorted.
- 2024-02-29 present (9,026); 0 zero days.
- Minimum 5,596 (2023-12-25), maximum 22,806 (2026-02-24), total 12,331,111, mean 9,656.3125. All match DATA_SNAPSHOT.md:157-158.
- The last day is not partial: 2026-06-30 = 11,311 vs the previous five Tuesdays 10,440-12,512.
- daily_segments: 0 duplicate keys, minimum count 1, 5 boroughs, 17 agencies, all 1,277 dates present.
- **HPD 2024-04-11..14 intake gap.** HPD counts are 1835 on 04-10, then 0, 0, 0, 0, then 7,398 on 04-15 and 1,967 on 04-16. `atlas quality` reports intake_interruption 2024-04-11..14 and catch_up_surge 2024-04-15 (7,398 vs 1,902 = 3.89x), plus a city-wide surge on 04-15 with score +15.0. All match DATA_QUALITY.md:118-127.
- **Reconciliation and UNASSIGNED residual, recomputed independently.** Segment sum 12,317,832 and residual 13,279 (0.1077%). 1,273 dates have a gap and 4 reconcile (2023-03-12, 2024-01-14, 2024-04-03, 2024-10-25). There are 0 negative residuals. The maximum is 2,434 on 2023-09-15 (25.54%), the only day above 2%. build_borough_components gives 7,662 rows (6 per date) whose sums equal daily_total on every date. Shares NYPD 44.55% and Brooklyn 30.38% are confirmed. All match DATA_SNAPSHOT.md:107-116, DATA_QUALITY.md:62-67 and known_exceptions.json.
- **Legacy KPIs recomputed from 01 Data.** kpis.py results:
- Totals and growth: 12,331,111; 9,656.31/day; YTD 1,758,816 to 1,994,584 = +13.40%.
- Holdout: actuals and the y(t-7) baseline re-derived from daily_total. MAPE 4.93 / 8.23, improvement 40.10%, MAE 514.30, RMSE 676.6857 (declared exception LEGACY-V0-EX-002), R2 0.5787.
- Forecast: 330,941 total, mean 11,031.37.
- Capacity: p75 of the last 180 days = 11,608.75 (published 11,609), with 45/180 days above. Five days exceed it by 12/84/58/202/210, first on 2026-07-13, maximum utilisation 101.81%, gap 566.
- Weekdays: Tuesday is highest at 10,283.30.
- **`atlas check` / `atlas quality` outputs match the docs.** Both exit 0. Check: PASS on integrity, validation and reproduction, with 14/14 KPIs ok. Quality: 28 flags (missing 0, surge 11, low_volume 4, intake_interruption 8, catch_up_surge 4, unassigned_share 1). Every count and every example in DATA_QUALITY.md:113-120 matches. surge.py re-derives the same 16 surge days (11 runs) and 4 low-volume days with my own robust-score code. `atlas check` output is byte-identical at the M0 merge a50892d and at HEAD, and the quality flag set is identical at the M1 merge 879c250 and at HEAD.
- **Fail-closed loading on tampered copies (tamper.py, pipeline plus M2 load_series).** Each tamper was refused at the stage shown:
- count +1 or CRLF line endings: integrity fails (checksum).
- rehashed manifest plus one count +1: validation fails (exception_drift).
- duplicate date: duplicate_key.
- dropped date: missing_dates.
- negative count: out_of_range.
- 9026.0 written as a float: malformed_value.
- BOM or extra date 2026-07-01: integrity fails (header or date range).
- lowercase borough: invalid_category.
- agency 'NAN': null_token.
- zero-count day: segments_exceed_total.
- known_exceptions.json deleted: unexplained_reconciliation_gap.
- manifest deleted: SnapshotError, CLI exit 2.
In every one of these cases load_series also refused, and the CLI exits 1 on a failing check or quality.
- **Every M2-M5 data load path is gated by integrity and validation.** The M2 runner, M3 uncertainty_runner, M4 optimization_runner and the M5 API snapshot component all call runner.load_series, which calls pipeline.validated_tables (integrity, then validation). Committed run manifests and the API cross-check data_fingerprint 5ab8b3a5…3a4d, which I recomputed from the scratch copy.
- **Independent future-perturbation leakage test.** leakage.py setup:
- Origins: 2024-02-28, 2024-02-29, 2024-12-31, 2025-07-03, 2025-12-24, 2026-01-29, 2026-06-29.
- Every value after each origin was replaced by random, NaN, zero or 1e12.
- Compared:
  - training rows h=1 (default 34 features) and h=7 (a custom set with lags, rolling mean/std, same-weekday mean/median, holiday eve/after/observed, trend, Fourier, declared events);
  - direct rows h=1..7;
  - a 30-step recursive forecast and its rows;
  - 30-day paths of atlas_linear_regression, legacy_linear_regression, seasonal_naive, same_weekday_median_5w, same_weekday_mean_4w and naive via forecast_at_origin.
Result: 364 checks, 0 differences. Control: perturbing the origin day itself does change the h=1 row at every origin.
- **Declared-event gating by announcement date.** An event covering 07-04..06, announced 2025-07-02:
- invisible to forecasts at origin 07-01;
- visible from origin 07-02;
- visible in h=1 training rows (cutoff t-1);
- invisible in h=7 training rows for 07-04..06 (cutoff 06-27..29).
An event announced on the origin day (07-01) is visible to the recursive forecast from 07-01.
- **Quality flags are causal and never used as features.** quality_causal.py perturbed totals and segments after 7 cutoffs, including 2024-04-12 inside the HPD run and 2024-04-15, and randomly dropped segment rows. Flagged (code, subject, day) sets on or before each cutoff were identical every time. The detectors use no centred or backward-filling windows (grep). features.py, models.py and backtest.py do not import atlas.quality; flags reach only evaluation.py:873 (sensitivity) and probabilistic.py strata.
- **Federal holiday rules.** Observed dates for 2023-2026 from atlas.events.federal_holidays match the OPM list exactly, including 2023-01-02, 2023-11-10 and 2026-07-03.
- **Feature-set fingerprints and test references.** default_feature_set gives 34 features, max offset 364, fingerprint f69f30a2…8f33, as in FEATURES.md:87-88. All 25 test names cited in the FEATURES.md verification table exist in tests/test_features.py.
- **In-scope test files pass.** In a scratch export (to avoid writing .hypothesis into the repo): 11 test files, 359 passed, 2 skipped. The 2 skips are git-history tests, because the export has no .git; I verified their property separately with git rev-parse at db46703.
- **Extraction-day/DST and low-day checks against docs.** There are no zero days. The 4 low_volume days are all holidays and report as info. DST Sundays sit within ordinary week-to-week noise (-6.7% to +17.7% vs adjacent Sundays). No doc contradicts this.

### Not verifiable here (5)

- **Re-deriving legacy-v0 from NYC Open Data (erm2-nwe9).** Contacting external services other than package registries is prohibited. The dataset is also live and revisable, so a re-query would not be expected to match exactly; the manifest discloses this. I could only confirm from the notebook that the queries are fully specified and did not hit their $limit.
- **NYC Open Data terms of use and attribution requirements for redistributing 01 Data.** No network access to the terms. The manifest says they were not verified; this belongs to the licensing gate (M).
- **Whether flagged or extreme days are real events or recording artefacts (2023-09-15 unassigned 2,434; Jan-Feb 2026 surges; DHS 2026-01-31..02-12 and 02-22..25; DOE disappearing after 2026-03-19).** This needs external records (weather, agency system changes, 311 release notes), which are not available offline. The docs correctly make no causal claim.
- **Mutation-testing claim in FEATURES.md:194-197 and the claim that M1 thresholds were fixed before looking at results.** No mutation-run artefacts are committed, and the claim is about process history. I checked only that the cited tests exist and pass.
- **The two git-history tests in tests/test_snapshot.py run in the real checkout.** I ran the in-scope tests in a scratch export with no .git (2 skipped) to avoid writing .hypothesis or caches into the shared repo. I verified the same property directly with read-only git rev-parse against db46703.

### Auditor's assessment

> Overall: the M0-M1 data layer is in very good shape for a v1.0 research demonstration. I found no CRITICAL or HIGH issue. I recomputed every integrity, coverage, reconciliation and legacy-KPI figure the docs publish with my own pandas/numpy code, and each one matches exactly. Loading fails closed on every tampered copy I built that a single file edit could produce. My own future-perturbation test on the real series found no leakage in the feature builders or in the six M2 models (364 checks, 0 failures), and declared events are correctly gated by announcement date. The quality flags are causal, are not imported by features/models/backtest, and are used only for evaluation sensitivity and M3 strata.
> 
> The three findings are documentation and disclosure problems, not engine defects:
> 1. MEDIUM, gate L: README.md still presents the legacy headline interpretations without inline qualification. These are "40.10% accuracy improvement", "13 days advance warning", "five forecast dates expected to exceed capacity", "next 30 days ... 330,941" and "selected because lowest holdout error". The repository's own LEGACY_LIMITATIONS/BACKTESTING refute them, and the banner qualifies only the MAPE and "planning capacity". The numbers themselves reproduce exactly; their stated meaning is the problem.
> 2. LOW, gate A: the robust same-weekday surge detector has little power in the holiday and winter season. 3 of the 5 largest days in the series are unflagged. Only 4 of 24 confirmation-year days at least 25% above the same-weekday median are flagged as surges, and a second DHS outage (2026-02-22..25) is missed. So "median and MAD resist earlier anomalies" and the M2 flagged-target sensitivity overstate robustness to anomalies.
> 3. LOW, gate L: stale or imprecise statements in the data docs and one dashboard line.
> 
> Minor observations not worth a finding:
> - ROADMAP.md:4-5 still says M7 "is implemented on feature/atlas-m7-dashboard and awaits review", although it is merged at 194fa4a.
> - FEATURES.md performance figures are optimistic on this machine: training matrix about 70 ms vs "about 15 ms"; 30-step recursion 0.46 s vs "about 0.15 s".
> - The docs never state the day-boundary convention. Days are NYC local calendar days of created_date (Socrata floating timestamps), so DST days have 23 or 25 hours. Measured DST Sundays differ from adjacent Sundays by -6.7% to +17.7%, which is indistinguishable from normal noise, so the effect is immaterial.
> - Agency coding changes exist but are immaterial for city-wide totals:
>   - DOE stops on 2026-03-19.
>   - OOS starts on 2025-09-17.
>   - NYC311-PRD appears only 2026-03-02..04-11.
> - The API and dashboard do not surface data-quality flags; history is shown unannotated.
> - Files are read up to three times between hash verification and parsing (possible change between check and use). This does not matter under the stated threat model.
> 
> A consistent forgery passes integrity, validation, `atlas quality` and the M2-M5 `load_series` path. I built one by bumping a daily_total count and the matching segment count, then rehashing the manifest. That path is by design not gated by reproduction. It is still caught by:
> - `atlas check` (reproduction mismatch);
> - the base-hash prefixes pinned in tests/test_snapshot.py;
> - the data_fingerprint checks against committed results (optimization_runner.load_inputs:230, api/reference.py:607).
> Defense in depth is therefore adequate.
> 
> Scripts are in <audit-scratch>/data/scripts: coverage.py, surge.py, hpd.py, leakage.py, quality_causal.py, tamper.py, kpis.py, agencies.py. Tampered trees are under .../audit/data/tamper/. Nothing in the repository was modified (git status clean).

## M2 forecasting (`forecast`)

### Checks that held up (12)

- **Independent re-implementation (NumPy, no atlas import) of all 7 models reproduces committed forecasts.** indep.py and indep_ets.py at the 6 spot_check.csv origins (2024-02-01, 2025-05-31, 2025-06-30, 2026-05-31, 2026-03-03, 2026-06-29): legacy regression, seasonal naive, same-weekday median 5w and mean 4w, naive and ETS match exactly (max diff 0.0); ATLAS regression max diff 3.4e-8. Also at 14 interior origins of the fresh run's forecasts.csv (e.g. 2024-06-10, 2024-12-15, 2024-12-20, 2025-01-02, 2025-01-19, 2025-11-04, 2026-02-23): legacy, seasonal naive and B* exact; ATLAS 3.9e-8. Every target_date equals origin + h.
- **Full M2 reference run reproduces byte-for-byte.** atlas backtest run --period development --period confirmation --period legacy_window --sealed-selection <development/selection.json> --output-dir <scratch>/m2-full: complete=True, result fingerprint d53bc91196296d43e6ca43aac859f5e9d7ea9dd88ac8fdb4c30fdcab39315a8a = committed. coefficients.csv, config.json, fits.csv, forecasts.csv, metrics.json, observed_inputs.csv, recommendation.json and report.md are all identical to the committed manifest hashes. 197,610 records. 7m09s user CPU (17 min wall under load average ~17).
- **Sealed development run verifies with the code of its own commit.** git archive a16672f into scratch, then atlas backtest verify on a copy of docs/results/atlas-m2-rolling-origin-v1/development: [PASS] verification, result fingerprint fbb948ec…dcea43, forecasts/fits/coefficients/observed_inputs/metrics identical (3m59s CPU).
- **Legacy reproduction and projection.** atlas backtest legacy: 90/90 holdout (MAE 514.2965, RMSE 676.7048, MAPE 4.9274) and 30/30 forecast; largest |raw−rounded| 0.4987. legacy_one_step_holdout.csv/.json byte-identical to docs/results/legacy-one-step-holdout. atlas forecast --recommendation full/recommendation.json: projections.csv, coefficients.csv, fits.csv, report.md and config.json byte-identical. Targets 2026-07-01..2026-07-30, total 333,968 (independent: 333,967.76); legacy published total 330,941.
- **Smoke backtest + verify.** atlas backtest run --config configs/backtest/smoke.json then atlas backtest verify: PASS, all 7 files identical, fingerprint 2896d3a4…3cc3033, max_abs_prediction_difference 0.0.
- **No leakage of post-origin actuals.** shock.py runs run_backtest at origins 2024-12-20 and 2025-09-15 with every actual after the origin multiplied by 10: predictions of all 7 models are bit-identical while the attached actuals change. Code review: history = series.through(cutoff) (backtest.py:196-201,605); training rows end at the origin (features.py:891-899); standardisation uses training mean/sd (regression.py:150-158); recursive_forecast reads actuals through the origin plus fed-back predictions only (features.py:998-1047).
- **MASE scale uses only data through the cutoff.** backtest.py:607 uses series.known_through(cutoff). Independent mean|y_t−y_{t−7}| through 2024-02-01 / 2025-06-30 / 2026-05-31 = 816.9846 / 949.5348 / 1023.7226, equal to the mase_scale in spot_check.csv.
- **Period boundaries and seal ordering.** Development origins 2024-02-01..2025-05-31 score targets through 2025-06-30; confirmation targets start 2025-07-01 (BacktestPlan check at backtest.py:244-252). Commit/manifest times: config+engine a16672f 16:41:23, development run created 16:44:36 (161.8 s), seal 10343e2 16:45:03, confirmation results b3222ae 17:05:13. These git timestamps are self-attested by the author. The full run's recomputed selection equals the seal (selection_seal_check.differences = []).
- **DM/HLN, ESS and Holm formulas.** dm_check.py: at h=1 atlas DM equals scipy ttest_1samp (stat and p equal to 1e-12). For h=7 and h=30 at T=120/336/486 it equals an independent HLN implementation to 1e-14 (statistic, p, Bartlett p, CI). Independent recomputation from forecasts.csv: selection/H1/H2 statistics −2.0221/−1.9376/−2.9434, p 0.0437/0.0535/0.0035, Bartlett-60 ESS 27.2/13.8/21.2, Holm H2 0.00695, H1 0.0535. All match metrics.json and BACKTESTING.md 6.1-6.2.
- **Documented M2 claims against metrics.json.** ATLAS regression has the lowest MAE, RMSE, WAPE, MASE and MAPE in all 70 horizon/bucket cells of both periods (0 violations). Legacy confirmation MAPE h7-30 is 9.05-9.78 (doc 9.0-9.8). Legacy-window MAPE h2-30 is 5.54-5.92 (doc 5.5-5.9). Recursion shares are 43/67/93/96%. Confirmation lead shares by origin month (Nov-Feb 50.6%, Mar-May 59.5%, May 30.9%, Dec+Jan 39.9%) recomputed by hand. The exclude-flagged sensitivity changes no comparison direction. H1 loss-differential ACF at lag 29/45/60/90 is 0.37/0.21/0.18/0.27, which supports the 'autocorrelated far beyond lag 29' statement.
- **Projection and record-type labelling.** projection/report.md is headed 'FUTURE PROJECTION … not validated'. The API attaches projection.label, point_forecast_notice and selection_test_notice (H1 not significant after Holm) to every M2-derived response (views.py:259-260, 313, 529, 634). The dashboard shows 'Archived projection: not observed, not validated' (web/src/views/common.tsx:188) and 'Not validated' tags.
- **Targeted M2 tests.** tests/test_m2_results.py and tests/test_m2_recursion.py in the exported scratch tree: 63 passed in 59.7 s (--no-cov -p no:cacheprovider).

### Not verifiable here (5)

- **How many model specifications, feature sets or configurations were tried before the freeze commit a16672f, and exactly what was seen of the confirmation period beyond the exposure log.** M2 enters git as a single commit (a16672f); earlier iterations and review sessions are not in the repository. Commit timestamps are self-attested by the author. The exposure log (BACKTESTING.md section 9) can be read but not independently confirmed.
- **Whether the data values at historical origins equal what was known at those dates (data vintage / revisions).** The snapshot has one final vintage with no extraction timestamp. Raw API responses and earlier vintages are not retained, and contacting NYC Open Data is out of scope. This limitation is already disclosed.
- **Cross-BLAS / cross-platform reproducibility of the regression predictions and the rounding-sensitive legacy path.** Only one machine and one numpy 2.5.4 / pandas 3.0.6 / BLAS build is available. Byte identity was verified on this environment only.
- **The delay-1 sensitivity run (docs/results/atlas-m2-rolling-origin-v1-delay1) byte-for-byte.** Not re-run, to limit CPU use on a heavily shared machine (load average ~17). Its manifest-hash consistency is covered by tests/test_m2_results.py, which passed.
- **Whether the same oversized-test issue changes any M3 conclusion.** Outside this lens (M3 uses a similar HAC/t(T−1) bandwidth sensitivity, UNCERTAINTY.md:431). Flagged for the M3 auditor; not checked.

### Auditor's assessment

> The M2 engine is computationally correct and fully reproducible here. I wrote a NumPy re-implementation that does not import atlas. It covers the ATLAS regression, the legacy min-norm regression with round-before-feedback, seasonal naive, the same-weekday median and mean, naive, and ETS(A,N,A). It reproduces the committed forecasts at all 6 spot-check origins, at 14 interior origins (including the non-estimable Dec 2024 / Jan 2025 legacy origins) and for the 2026-07-01..07-30 projection. Baselines, ETS and the legacy model match exactly; the ATLAS regression matches to 4e-8. The full reference run, re-executed from an exported tree, gave byte-identical outputs: result fingerprint d53bc911…, all 8 files identical, 7m09s CPU. The sealed development run verifies byte-for-byte with the code of its own commit a16672f. The legacy holdout and the projection are also byte-identical. I found no leakage. Truncation, training-window end, standardisation, MASE scale (training data only), recursion (predictions fed back, never actuals), target date = origin + h, weekday alignment, holiday features, data-delay mapping and the development/confirmation boundary are all correct. A shock test (every post-origin actual multiplied by 10) leaves all 7 models' forecasts bit-identical. The documented numbers I checked match metrics.json.
> 
> The real problem is statistical inference. The DM/HLN formula is implemented correctly; it matches scipy and my own implementation to 1e-14. But with h = 30 and T = 336-486 the test is oversized, so its p-values come out too small. Under the test's own MA(h−1) assumption the decision rule rejects a true null 6.5-9.6% of the time instead of 5%. The wider-bandwidth checks are worse (10-18%), because they are compared against t(T−1) critical values. As a result, three documented claims are artefacts of using the wrong critical values: the development selection test "does not depend on the HAC bandwidth", it "becomes more significant under wider bandwidths", and H2 "stays significant under every HAC bandwidth tried". The selection statistic, 2.02, is below the size-corrected 5% critical value (about 2.31-2.39). A null-imposed block bootstrap of the actual development loss differential gives p 0.08-0.13. So the pre-registered rule's significance gate would most likely have failed under a correctly sized test, and the rule would then have selected its fallback, the same-weekday median of 5 weeks (B*). This does not overturn the disclosed headline: H1 (the regression versus B*) not being significant is reinforced. The regression's point-accuracy lead (lowest MAE/RMSE/WAPE/MASE/MAPE in all 70 horizon/bucket cells of both periods) is real. But the "significant selection" and "robust H2" statements overstate the evidence, and the doc's "conservative" label is wrong. Second issue: the first verify command in BACKTESTING.md fails with the current code, which contradicts the document's own section 8.
> 
> Minor observations, not reported as findings:
> - Section 10 (BACKTESTING.md:905-907) says "about 14-27 independent observations … under the primary bandwidth". Those values are the Bartlett-2h effective sizes (13.8/21.2/21.4/27.3); under the primary rectangular-29 bandwidth, H1's effective size is 13.0.
> - `holm()` drops `None` entries, so the family shrinks if a primary test is not computed. Both were computed here, so there is no impact.
> - Day-1 bucket DM tests use bandwidth 0-2 even though h=1 losses are clearly persistent across origins. Those p-values are labelled exploratory.
> - CI never regenerates the committed reference metrics. Only 6 spot-check origins, the smoke run and doc-versus-metrics consistency are tested. A full-run verify (~5 CPU-min) in CI or a release checklist would close this gap; I ran it here and it passed.
> - The comment in tests/test_m2_results.py attributes PRE_REVIEW_OUTPUTS to commit 10343e2. The reference results were committed in b3222ae.
> - The M3 docs (UNCERTAINTY.md:431) use the same t(T−1)-with-wide-bandwidth sensitivity. This is outside my lens and I did not verify it, but it is likely affected the same way.
> 
> Everything else checked held up:
> - Pre-registration timeline (self-attested git timestamps): a16672f at 16:41, development run at 16:44:36, seal 10343e2 at 16:45, results b3222ae at 17:05.
> - Selection recomputation equals the seal.
> - Holm values are correct.
> - Projection labels say "future projection / not validated", in the API and the dashboard too.
> - The disclosed limitations (H1 not significant after Holm, archived projection, one data vintage, exposure log) appear wherever the numbers are shown.
> 
> For v1.0 the forecasting area is release-ready on correctness and reproducibility. The inference wording needs a MEDIUM-level correction or disclosure before the selection is presented as statistically supported.

## M7 dashboard (`dashboard`)

### Checks that held up (16)

- **Every page loads with zero console errors and zero CSP violations at 1280 px and 375 px; screenshots taken.** Script pages.mjs (scratch dir audit/dashboard). 12 loads (6 routes × 2 widths). Each had msgs=[], csp=[] (a securitypolicyviolation listener was installed by addInitScript), no error states, no horizontal scroll. Titles were correct, e.g. 'Overview · ATLAS'. Screenshots are in shots/1280-*.png and shots/375-*.png. A CSP listener during phone interactions (menu, touch tooltip, a simulation run, all disclosures open; csp-interact.mjs) also recorded no violations.
- **The committed build is reproducible and matches its manifest.** git archive HEAD into the scratch tree, then npm ci --ignore-scripts, then npm run build (tsc + vite 8.3.1). `diff -r` against the committed dist printed nothing (REBUILD_IDENTICAL; log in rebuild.log). Recomputing SHA-256 and size of every dist file against atlas-dashboard.json: 17 of 17 match, with no unlisted or missing files.
- **A tampered dist file is refused by the API at startup.** In tree2 I appended '/*x*/' to assets/Panel-DeQ_DYPs.js and started the API (ATLAS_REPO_ROOT=tree2, port 9241, serve_tree.sh). Results: GET / → 404 application/json; GET /assets/Panel-DeQ_DYPs.js → 404; GET /api/v1/system → 200. Log: {"event": "dashboard_unavailable", "message": "dashboard not served: dashboard file altered: assets/Panel-DeQ_DYPs.js"}.
- **The API sends the dashboard CSP and security headers, and no inline code exists.** curl -I on 127.0.0.1:9240. The pages, assets and favicon carry the CSP "default-src 'none'; script-src 'self'; style-src 'self'; ... frame-ancestors 'none'; object-src 'none'; require-trusted-types-for 'script'; trusted-types 'none'", plus X-Frame-Options DENY, Referrer-Policy no-referrer, nosniff, COOP/CORP same-origin and Permissions-Policy. Assets are cached max-age=31536000, immutable; HTML is no-cache. The descriptor is no-store with the JSON CSP. /index.html and /atlas-dashboard.json return 404. POST / → 405 with Allow: GET, HEAD. A foreign Host header → 400. If-None-Match → 304. dist/index.html has a single external module script and no inline script or style. The only external origin in the bundle is a github.com link (no fetch).
- **nginx dashboard headers and profile behaviour (private and public-demo).** nginxsite.py renders the production site with Renderer.for_release for each profile and runs it in real nginx 1.24 on ports 9242/9243 and 9245/9246. Every dashboard route carries HSTS, nosniff, DENY, no-referrer, the full CSP, COOP/CORP and Permissions-Policy. Unknown paths return 404 JSON with the API CSP. Descriptors: private {"profile":"private","simulations":"enabled"}; public {"operational_detail":false,"profile":"public-demo","simulations":"disabled_public_demo"}. The private site returns 401 without an account. Public-demo answers POST /api/v1/simulations with 404. In the browser (nginxbrowser.mjs) the badges read 'PUBLIC DEMO · READ-ONLY' and 'PRIVATE DEPLOYMENT', with no CSP violations; with no API behind, the error 'The API is not answering' appears.
- **Data fidelity: displayed table values equal the API.** tables.mjs switches every chart to its table view and dumps every table. compare.py compares them against curl responses saved in api/*.json. 461 rows checked, 0 mismatches: the 210-row history+projection table, the 30×7 horizon MAE table, the 30-row quantile table (also checked monotonic), the comparison table, the normal scenario's daily plan and the policies' backlogs. Spot checks: MAE 984.918→'985'; H1 −127.25 (CI −256.44 to +1.94, Holm p 0.0535)→'−127', '−256 to +2', '0.054'; day 1 11123.68→'11,124'; coverage 0.79901→'79.9 %'; delayed_surge cash diff −5112.51→'−$5,113'; normal $225,275.80→'$225,276'. Snapshot days 1,277 equals Jan 1 2023 to Jun 30 2026 inclusive.
- **No time-zone date shift.** tz.mjs and tzticks.mjs ran with timezoneId UTC, America/Los_Angeles, America/New_York, Pacific/Kiritimati, Pacific/Pago_Pago and Asia/Kolkata. The browser itself showed `new Date('2026-07-01')` as Tue Jun 30 in LA and Pago Pago. The dashboard nevertheless showed identical text in every zone: tags 'OBSERVED TO JUN 30, 2026' / 'PROJECTED FROM JUN 30, 2026'; 'Day 1 Jul 1, 2026: 11,124'; the keyboard readings 'Tue, Jun 30, 2026 ... 11,311' and 'Fri, Jul 3, 2026 ... Federal holiday: Independence Day'; the table rows; and every x-axis tick on the Overview, Forecasting (all history), Uncertainty and Decisions charts. Diffs against UTC were empty. The code uses Date.UTC parsing, Intl formatters with timeZone 'UTC', and d3 scaleUtc.
- **Holiday markers, units and horizons are correct.** The table holiday rows are Mon Jan 19 (MLK), Mon Feb 16 (Washington's Birthday), Mon May 25 (Memorial Day), Fri Jun 19 (Juneteenth) and Fri Jul 3 (observed Independence Day; Jul 4 2026 is a Saturday). The band chart marks the holiday-related Jul 3, 4 and 5, matching the API's holiday_related flags. The horizon chart x axis covers 1-30 (ticks 5…30). Units are labelled requests/day, requests, request-days, staff hours, USD, and % for coverage. Bias is shown with a sign (−74, −178).
- **Scientific warnings are visible above the fold and next to their numbers (desktop and Pixel 7).** fold.mjs measures the y-position of warning words in main. Overview, phone: HYPOTHETICAL at 222 px, NOT VALIDATED at 570, NOT SIGNIFICANT at 737 (viewport 839); desktop: FAILED_CALIBRATION at 403, NOT SIGNIFICANT at 428, DIAGNOSTIC at 431 (viewport 800). Uncertainty: FAILED_CALIBRATION at 277/338. Decisions: HYPOTHETICAL at 81/82, DIAGNOSTIC at 462/623. Simulator: HYPOTHETICAL at 81/82, FAILED_CALIBRATION at 290/380. Every data panel prints the API's integrity notices, always visible (Notices component). DIAGNOSTIC rows carry a tag in every table and chart, and the DIAGNOSTIC plan callout says spends are never budget requests.
- **Keyboard: tab order, visible focus, skip link, no trap, chart sliders.** keys.mjs tabs through all six pages: 16 to 51 stops each, wrapping back to the start (no trap), and every stop has a computed outline or stroke (noOutline = []). The first stop is 'Skip to content'; Enter followed by Tab lands inside main. Chart sliders work with End, ArrowLeft and Home, and aria-valuetext reads the date and values (tz.mjs).
- **Text and non-text contrast in light and dark mode.** contrast.mjs walks every visible text node on six pages in each colour scheme, with all disclosures open, and blends ancestor backgrounds: 0 failures against 4.5:1 (3:1 for large text). From tokens.css: control-border is 3.67/3.27 (light, surface/sunken) and 3.66/3.87 (dark); focus is 6.46/8.25; series colours are ≥3.12 on the light surface (series-2 lowest) and ≥4.48 in dark; status inks on their washes are ≥5.63 (light) and ≥6.28 (dark).
- **Reflow at 320 px and 200 % zoom; Pixel 7 touch targets.** reflow.mjs at 320×256, 640×450 and Pixel 7: no document-level horizontal scroll on any page with all disclosures open. The only overflow is inside the focusable, labelled table-wrap scroll regions. Small targets on the phone are inline table-cell links or links with a 20 px line height that axe accepts under the spacing exception, plus 18 px checkboxes inside clickable labels. axe WCAG 2.2 AA (axe.mjs) found 0 violations on the public-demo simulator (light and dark), the Overview with a 503 error state, Forecasting with the legacy window and all history, and a DIAGNOSTIC scenario at 375 px.
- **Loading, slow, error, timeout, offline, malformed and contract states.** states.mjs, states2.mjs and states3.mjs:
- slow: 'Still waiting for the server' at 3.5 s;
- timeout: 'The request timed out ... No answer within 20 s' after 21.0 s;
- 500: 'Server error' with the request id and Retry;
- 503 artifact_unavailable: 'This component is unavailable';
- 429 with Retry-After 30: 'Retry in 30 s.';
- HTML 502: 'The server answered HTTP 502';
- malformed JSON or an HTML 200: 'The answer is not JSON', with no Retry;
- a missing field: contract error naming 'data.points';
- a 'NaN' string: contract error;
- point_forecast_validated=true: 'Answer refused';
- 302: network error (redirect: 'error');
- an injected '<img onerror>' in model_id: rendered as text, no dialog;
- offline scenario switch: network error, then Retry recovers;
- offline lazy chunk: page-level 'Reload'.
No error state shows substitute numbers.
- **Simulator validation, stale marking, cancel, double submit.** sim.mjs against the real M4 engine. Client-side rejections with no POST: 0 and 31 ('between 1 and 30 days'), 7.5 ('whole number'), 'abc', '' ('Enter a value'), a 5,000-character number ('finite'), '0x10', '1,000', multiplier 2.0001, wage 0.005 ('0 or at least 0.01'). Accepted: the boundaries 1 and 2, and '1e1' and '  7 ' (parsed as numbers). The server-only rule (capacity equal to the legacy p75, 4643.5 h × 2.5) returns 422, mapped onto both fields with 'Showing the previous result'. Changing an input after a run shows 'The inputs have changed' (data-stale=true); changing it back clears the message. Cancel shows 'Simulation cancelled.' and focus returns to Run. A forced second click while running sends no extra POST (sim2.mjs).
- **Profiles fail closed; the browser holds no key.** states2.mjs and pubsim.mjs. Descriptor 404, an invalid value ('maybe'), non-JSON, or a hang: Run disabled, 0 POSTs, 'could not be read' callout. Public-demo: all 6 fieldsets disabled, 0 enabled inputs, 0 POSTs even with requestSubmit; no /api/v1/system request anywhere. disabled_api_key: Run disabled with an explanation. A grep of the bundle finds no Authorization or X-API-Key header and no localStorage, sessionStorage, IndexedDB or cookie access; fetch uses credentials 'same-origin' and redirect 'error'.
- **DASHBOARD.md quantitative claims spot-checked.** 20 s read timeout, 150 s simulation timeout and 2.5 s slow threshold match client.ts, endpoints.ts and resource.ts. A full Vitest run counts 166 tests, matching DASHBOARD.md:462. Untested Firefox, Safari and screen readers are disclosed (DASHBOARD.md:335, 481).

### Not verifiable here (6)

- **Firefox and Safari/WebKit rendering and behaviour (e.g. CSS :has() for segmented-control focus rings, Intl currency formatting with maximumFractionDigits 0, Trusted Types enforcement differences).** Only Chromium is installed (/opt/pw-browsers); already disclosed as untested in DASHBOARD.md.
- **Real screen-reader sessions (NVDA, JAWS, VoiceOver, TalkBack).** No assistive technology is available in this container. Only the ARIA attributes, live-region text and axe results were checked; already disclosed.
- **The private profile end to end with a real API behind nginx (Basic-auth session, nginx adding the simulation key).** Running tests/m7_stack.py needs a Unix socket path under 108 bytes, which is impossible inside the scratch directory, and its live_api picks a random port outside my assigned range. I served the rendered nginx sites with no API behind them (the dashboard and its headers verified, API calls 503). The lead auditor's nginx Playwright projects cover the full stack.
- **The committed Playwright suite and the full pytest suite.** Excluded by the audit rules (run by the lead auditor). I ran only ad-hoc scripts and, in a scratch copy, Vitest.
- **Performance figures in DASHBOARD.md (cold-load timings, throttled 4G).** The machine was heavily loaded by concurrent auditors (load average 7-9 on 4 vCPUs), so timings would not be representative. Bundle sizes do match (index JS 85.81 kB gzip, as reported by Vite).
- **Real deployment over the internet (TLS certificates from ACME, real DNS, a VPS).** Out of scope and forbidden (no external servers); M6 phase B is pending by disclosure.

### Auditor's assessment

> I found no CRITICAL, HIGH or MEDIUM defect in the M7 dashboard. Every independent check I ran passed:
> - All six pages load at 1280 px and 375 px with no console errors, no CSP or Trusted Types violations, and no horizontal scroll.
> - A rebuild from the committed lock in a scratch copy produced a byte-identical web/dist. The 17 files match atlas-dashboard.json.
> - A dist file with one appended byte is refused at API startup (the dashboard returns 404 and a dashboard_unavailable warning is logged). The API keeps serving.
> - 461 table rows across the pages match the API's own responses, value by value (rounding, sign, units, dates). Dates do not shift in any of six emulated time zones, including Pacific/Kiritimati (UTC+14) and Pacific/Pago_Pago (UTC−11). The code formats calendar dates in UTC, using Date.UTC and scaleUtc.
> - Federal holidays are marked on the right days: Jan 19, Feb 16, May 25, Jun 19, and the observed Independence Day on Fri Jul 3 2026.
> - Each disclosed limitation sits next to its numbers: FAILED_CALIBRATION, not significant after Holm, HYPOTHETICAL, archived projection, and DIAGNOSTIC versus plans that meet every constraint. On every page the relevant tag or callout appears in the first screen at 1280 px and on a Pixel 7. I found no contradiction of a disclosure.
> - Failure states behave correctly: slow loads, a 20 s timeout, 500/502/503, 429 with Retry-After, malformed JSON, contract violations, integrity-claim refusal, redirects, and offline. No error state shows substitute numbers.
> - The simulator fails closed. It is disabled while the deployment descriptor loads, when it cannot be read or is invalid, in public-demo, and when the API has a key. The browser never sends a credential.
> - Through real nginx (both profiles, rendered by the deploy code and run in my scratch directory), every dashboard response carries the full header set. Public-demo refuses POST /api/v1/simulations with 404.
> 
> The defects I did find are all LOW:
> - **Accessibility:** the simulator's error-summary links fail the WCAG 2.2 target-size rule (2.5.8) once two or more client-side field errors are shown at desktop width. The committed axe test only scans a server-side failed run.
> - **Legacy window (provenance/labelling):** the Reproducibility table says its target days run to Jul 29, 2026, past the end of the data. The model-ranking panel also shows legacy-window MAE without its "descriptive only, incomplete horizons" caveat.
> - **Tests (two items):**
>   - The "whatever the time zone" date test only runs in CI's UTC zone. It passes when the UTC formatting is removed, so the time-zone safety is not actually tested.
>   - Several Vitest view tests are timing-flaky under CPU load. They rely on the 1 s default wait for asynchronous queries.
> - **Provenance links:** the Methods page links to documents on GitHub's moving `main` branch, not to the released version.
> 
> Smaller observations, not worth a finding:
> - The snapshot end date "30 Jun 2026" is hard-coded in the sidebar (App.tsx:213) and in a tag title (Tag.tsx:107).
> - The calibration chart's legend draws "Perfect calibration" as a dashed line, while the line is drawn dotted and the text calls it "dotted".
> - The H1 interval (−256 to +2) is shown without its confidence level. The API does not report one.
> - Cancel aborts only the browser's request. The server finishes the solve and holds a worker until then (no disconnect handling in src/atlas/api). DASHBOARD.md:193-195 says "aborts the request", which is accurate but does not mention this.
> - A contract-violating `scenario_id` (the schema accepts any string) would make `api.scenario` throw synchronously and crash the whole Decisions page instead of showing a contained error. This is only reachable with a misbehaving API.
> - Dashboard version 0.7.0 is baked into the manifest and footer. A v1.0 release needs a rebuild and a recommit of dist.
> 
> Overall, the dashboard is release-ready for a private-first v1.0 research demonstration, provided the LOW items are fixed or explicitly deferred.

## Supply chain and CI (`supplychain`)

### Checks that held up (23)

- **uv.lock is current with pyproject.toml.** In an exported copy (git archive HEAD), `uv lock --locked` with uv 0.11.32 printed 'Resolved 44 packages' and exit 0.
- **Every Python package in uv.lock is hash-pinned, comes from PyPI, and matches PyPI.** scratch/uvlock_check.py: 43 packages from registry https://pypi.org/simple, 1 editable (atlas), 0 files without sha256. scratch/scripts/uvlock_vs_pypi.py compared all 706 lock file hashes with the PyPI JSON API: 0 mismatches, 0 yanked, 0 PyPI-reported vulnerabilities.
- **No Python dependency needs a source build on linux x86_64 cp312.** uvlock_check.py found no package without a usable wheel. `uv sync --locked --no-dev --no-install-project --no-build --python 3.12` into a scratch venv installed the 18 runtime wheels, exit 0. `uv pip install -e . -v` logged 'Performing direct build' (uv's integrated backend), so uv_build is not downloaded.
- **Known vulnerabilities, Python.** `uv export --frozen --format requirements-txt --no-emit-project` then `uvx pip-audit -r req-all.txt --no-deps --disable-pip` (pip-audit 2.10.1, PyPI service): 'No known vulnerabilities found'. 40 packages were audited. The 3 skipped are platform-gated: colorama (win32), httpx2-jsfetch (emscripten), tzdata (win32/emscripten).
- **Known vulnerabilities, JavaScript, all severities.** `npm audit --json` in web/: info 0, low 0, moderate 0, high 0, critical 0. Dependencies: prod 16, dev 413, optional 28, total 428.
- **package-lock.json integrity, sources and registry signatures.** scratch/npmlock_check.py: 428 entries, all with sha512 `integrity`, all `resolved` from https://registry.npmjs.org/, no git/http/file/link entries. scratch/scripts/npm_registry_check.py (407 packuments): 0 integrity mismatches, 0 tarball-URL mismatches, 428/428 ECDSA signatures valid against registry.npmjs.org/-/npm/v1/keys, all versions published before 2026-10-01 (newest chai 6.3.0 on 2026-09-30), 119 with provenance attestations.
- **npm install scripts.** Only node_modules/fsevents 2.3.2 and node_modules/vite/node_modules/fsevents 2.3.3 have hasInstallScript. The registry manifest shows 'install': 'node-gyp rebuild', os ['darwin'], dev and optional, so they are skipped on Linux. A clean `npm ci --ignore-scripts` in a scratch copy installed 404 packages, and `npm ls --all` exited 0.
- **Committed dashboard build is reproducible from sources and the lock.** `npx vite build` after the clean `npm ci --ignore-scripts` in the scratch copy, then `diff -r web/dist <scratch>/dist-clean`: identical.
- **Shipped JS runtime dependencies and their licences.** `npm ls --omit=dev`: react 19.3.0, react-dom 19.3.0, scheduler 0.28.0, zod 4.6.5 (MIT); d3-scale, d3-shape, d3-array, d3-color, d3-format, d3-interpolate, d3-path, d3-time, d3-time-format, internmap (ISC); @fontsource-variable/inter 5.3.0 (OFL-1.1). No copyleft.
- **Python runtime licences.** scratch/scripts/pylic.py on the 19 runtime packages: MIT, BSD-3-Clause, PSF-2.0, BSD+Apache dual (dateutil), numpy 'BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0'. No GPL/AGPL. MPL-2.0 appears only in dev tools (hypothesis, pathspec).
- **GitHub Actions: SHA pinning matches the tag comments.** git ls-remote: actions/checkout v4.4.0 = 11d5960a326750d5838078e36cf38b85af677262; setup-python v5.6.0 = a26af69be951a213d495a4c3e4e4022e16d87065; setup-node v4.4.0 = 49933ea5288caeca8642d1e84afbd3f7d6820020; upload-artifact v4.6.2 = ea165f8d65b6e75b540449e92b4886f43607fa02. All four equal ci.yml lines 25/32/171/235. No third-party actions.
- **GitHub Actions: token scope, credentials and injection.** ci.yml:9-10 `permissions: contents: read`, with no job-level override. `persist-credentials: false` at lines 27, 130, 163. No pull_request_target or workflow_run. The only `${{ }}` is in the concurrency group (line 13), not in any run step. No `secrets.` usage.
- **CI npm cache cannot poison installs.** setup-node `cache: npm` keys on web/package-lock.json and caches ~/.npm. `npm ci` checks every tarball's sha512 against the lock, and PR-scoped caches are not readable from main.
- **Hash-pinned uv bootstrap matches PyPI.** .github/ci-requirements.txt: 19 sha256 entries, exactly the 19 files PyPI lists for uv 0.11.32 (uploaded 2026-07-23). The same file is used by deploy/scripts/bootstrap-host.sh:136-137 with --require-hashes.
- **Secret scan of the full history.** scratch/scripts/secretscan.py read all 979 blobs (438 paths) reachable from --all, expanding zip members and PDF streams. Every remote branch head (git ls-remote origin) is an ancestor of main. 0 hits for AWS, GitHub ghp_/github_pat_, private keys, Slack, Google, Stripe, sk-, JWT, npm or PyPI tokens. All password/key assignments are obvious fakes ('correct-horse-battery-staple-1234', 'e2e-correct-horse-battery-staple', 'hunter2hunter2', '0123456789abcdef...', 'secret-value-never-logged', '$apr1$abcdefgh$0123...'). Paths deleted from history are only superseded web/dist/assets files.
- **Legacy binaries contain no credentials or personal data.** pbixray on the .pbix: 8 Power Query sources, all `Csv.Document(File.Contents("C:\Users\Admin\Downloads\PROJECT NUMBER 2\Project_2_PowerBI_Data\<name>.csv"))`; no database connection strings, no M parameters. PDF metadata: Creator/Producer PDFium, CreationDate 2026-07-18. Notebook: Colab /content paths, the public NYC Open Data URL, no tokens. The CSVs are aggregates only (date, agency, borough, counts).
- **Dangerous default: non-loopback bind without a key.** `python -m atlas api serve --host 0.0.0.0 --port 9280` printed 'refusing to bind to 0.0.0.0 ...' and exited 2; `--host ::` did the same (src/atlas/api/server.py:158-166).
- **Dangerous defaults with a key on 0.0.0.0.** Port 9281 with ATLAS_API_KEY set: Host 127.0.0.1 got 200. Host 192.0.2.2 (the machine's IP), Host evil.example, and a request through the machine's IP each got 400. POST /api/v1/simulations without the key got 401. CORS preflight from https://evil.example got 400. No CORS origins by default; FastAPI debug off; uvicorn proxy_headers=False and server_header=False.
- **Secrets are generated strongly and stored with safe modes.** src/atlas/deploy/credentials.py:57-62 uses secrets.token_hex(32) and secrets.token_urlsafe(24) with openssl SHA-512-crypt. write_atomic sets 0600 before the rename. read_api_key_file refuses group/other-readable files (settings.py:202-208). .gitignore excludes .env, .env.*, *.pem, *.key and secrets/. No secret-named file has ever been tracked.
- **Example configuration files contain no secrets.** .env.example leaves every variable commented, with ATLAS_API_KEY empty; an empty value resolves to no key, so the loopback-only rule applies. deploy/deploy.env.example says 'NO SECRET BELONGS HERE' and defaults to the tunnel profile with ATLAS_DEPLOY_API_DOCS=false.
- **The documented npm cooldown claim holds.** DASHBOARD.md:107-113 says the 426 locked packages were all published before 1 Oct 2026. The lock has 426 unique name@version pairs (428 entries), and registry publish times confirm all were before 2026-10-01.
- **Python packages added after M4 respected the two-week cooldown.** scratch/uvlock_entry_age.py replayed every uv.lock revision. Only the 10 packages that entered at M0 (09376eb) were under 14 days old, which ROADMAP.md:274 discloses. No package entering at M2-M7 was under 14 days old.
- **Dependency confusion is not possible for the project packages.** uv.lock pins `atlas` as source {editable='.'}; pyproject.toml has the 'Private :: Do Not Upload' classifier; web/package.json has "private": true.

### Not verifiable here (7)

- **OSV database cross-check for Python packages.** pip-audit with `--vulnerability-service osv` failed: the egress proxy returned 403 for api.osv.dev. Only the PyPI vulnerability service was used (0 findings; the PyPI JSON for all 43 locked versions also lists no vulnerabilities).
- **`npm audit signatures` (TUF-rooted signatures plus provenance attestations).** npm failed with 'Failed to download' (statusCode 403) while fetching TUF metadata from the Sigstore CDN through the proxy. Instead I verified all 428 registry ECDSA signatures against keys from https://registry.npmjs.org/-/npm/v1/keys (trust root: TLS to the registry, not TUF). The 119 Sigstore provenance attestations were not verified.
- **GitHub repository security settings (Dependabot alerts, secret-scanning push protection, branch protection, required checks).** These are repository settings not visible in the checkout. The GitHub REST API for the actions/* repositories returned 403 in this session. Only the workflow file and CI logs (through the GitHub MCP) could be inspected.
- **Integrity of the Chromium download by `npx playwright install --with-deps chromium` in CI.** playwright-core 1.56.1's registry code has no checksum verification (it relies on TLS to cdn.playwright.dev or playwright.download.prss.microsoft.com). I could not observe a CI download. This is CI-only; the browser is never shipped.
- **Behaviour of the fsevents install script (`node-gyp rebuild`) on macOS developer machines.** No macOS environment. On Linux it is skipped (os: darwin), and a full `npm ci --ignore-scripts` build was shown to reproduce web/dist exactly.
- **Whether any third-party release just outside the cooldown (e.g., starlette 1.8.0) contains a security fix that the constraints block.** No access to upstream changelogs or advisories beyond PyPI and npm metadata. PyPI lists no vulnerabilities for starlette 1.7.0.
- **Full internal contents of the Power BI DataModel beyond the Power Query expressions and metadata.** pbixray decoded the XPress9 DataModel's Power Query sources (8 queries, local CSV paths only) and the metadata. Table rows were not individually reviewed, but they are loaded from the same aggregate CSVs committed in '01 Data'.

### Auditor's assessment

> The supply chain at commit 194fa4a is in good shape. I found no release-blocking security defect in this area. Both locks are complete and match their registries:
> - uv.lock: 43 registry packages and 706 files. Every SHA-256 equals PyPI's, and nothing is yanked.
> - package-lock.json: 428 entries. Every one has a sha512 `integrity`, resolves from registry.npmjs.org, and carries a valid npm registry ECDSA signature.
> - Neither lock has a git, http or file source.
> 
> pip-audit, using the PyPI service, reports 0 known vulnerabilities across the 40 platform-applicable Python packages. `npm audit` reports 0 vulnerabilities at every severity across 428 dependencies (16 prod, 413 dev).
> 
> Runtime installs need no source builds:
> - `uv sync --locked --no-dev --no-build` installs the 18 runtime wheels.
> - The project itself is built by uv's integrated backend, so uv_build is not downloaded.
> - The only npm install scripts are the two macOS-only fsevents copies (`node-gyp rebuild`), which never run on Linux.
> - A clean `npm ci --ignore-scripts` followed by `vite build` reproduces the committed web/dist byte for byte.
> 
> CI hygiene is sound:
> - Workflow-level `permissions: contents: read`.
> - `persist-credentials: false` on all three checkouts.
> - No `pull_request_target`, and no `${{ github.event.* }}` inside `run:` steps.
> - All 4 actions are pinned to full SHAs that equal their tags (checked with git ls-remote).
> - uv is bootstrapped from 19 hash-pinned files that equal PyPI's.
> 
> A scan of all 979 blobs reachable from every ref (every remote branch head is an ancestor of main) found no credentials. All test passwords and keys are obvious fakes, and the only deleted paths in history are superseded hashed dist assets.
> 
> Dangerous defaults hold up under test:
> - `atlas api serve --host 0.0.0.0` (or `::`) without a key exits 2 with a refusal.
> - With a key, a request carrying a non-allowed Host header (including the machine's own IP) gets 400.
> - A CORS preflight from an unlisted origin gets 400.
> - POST /simulations without the key gets 401.
> - Secrets are generated with Python's `secrets` module (256-bit API key, 192-bit proxy passwords, SHA-512-crypt hashes).
> 
> Five evidenced findings, none above MEDIUM:
> 1. **MEDIUM:** the committed and served dashboard bundle strips every MIT/ISC notice (React, react-dom, scheduler, zod, the d3 packages) and ships the OFL Inter font without its licence text. The repository is public, and no third-party notice exists anywhere.
> 2. **MEDIUM:** nothing scans or monitors the deployed Python runtime for vulnerabilities. There is no pip-audit, no scheduled run, no Dependabot or Renovate, no SBOM and no update cadence; the only gate is `npm audit --audit-level=high` on push/PR.
> 3. **LOW:** /docs and /redoc are on by default and exempt from the CSP. They load floating `swagger-ui-dist@5` and `redoc@2` from jsDelivr with no SRI, on the same origin as the API and the dashboard, and Swagger offers an X-API-Key Authorize dialog.
> 4. **LOW:** all pinned actions declare node20. The latest main CI run warns that they "are being forced to run on Node.js 24", and the runner source puts the Node 20 removal date at 23 Sept 2026.
> 5. **LOW:** ROADMAP describes the two-week cooldown as enforced by `constraint-dependencies`, but that setting only caps three named packages. Those caps never expire, and the pinned uv already supports `exclude-newer` durations.
> 
> Minor observations, not worth a finding:
> - eslint 9.39.5 (dev-only) is marked deprecated ("no longer supported") on the registry, and `npm ci` prints a warning for it.
> - The legacy .pbix DataModel embeds local Power Query paths such as `C:\Users\Admin\Downloads\PROJECT NUMBER 2\...`. They are generic, not personal data.
> - The notebook holds only Colab `/content/` paths, the PDF metadata is just PDFium, and the legacy CSVs are aggregates with no personal data.
> - Licences are all permissive. Copyleft appears only in dev tooling (MPL-2.0 hypothesis, pathspec, axe-core, lightningcss; CC-BY-4.0 caniuse-lite) and none of it ships. The Python runtime is MIT/BSD/PSF/Apache (numpy adds 0BSD/Zlib/CC0), and the shipped JS is MIT/ISC plus OFL-1.1.
> - deploy/test/Dockerfile uses an unpinned `ubuntu:24.04` tag and the container test runs `--privileged`. Both are acceptable for a disposable CI stand-in.
> - `npx playwright install` downloads Chromium with no checksum verification (no hashing in playwright-core's registry code). This is CI only and never ships.
> - The concurrency group cancels in-progress runs on main, so a quickly superseded main commit may never get a complete CI run.
> - web/.npmrc does not set `ignore-scripts`; the clean build shows it is not needed on Linux.
> - DASHBOARD.md's "426 locked packages" is correct as unique name@version pairs (428 lock entries), and its claim that all were published before 1 Oct 2026 checks out against registry publish times.
> - I did not propose a licence for the project itself (owner decision).

## M3 uncertainty (`uncertainty`)

### Checks that held up (12)

- **Committed M3 results are reproducible byte-for-byte at HEAD 194fa4a.** full_repro.sh (scratch) ran the selection run, then the sealed full run (`--sealed-selection docs/results/.../selection/selection.json`), then the projection. Result fingerprints 2e67348990ba... (selection) and c4d2adbf128e... (full) equal the committed ones. SHA-256 is identical for metrics.json, report.md, selection.json, calibration_status.json, config.json, backtest_config.json, projection_intervals.csv, projection_exceedance.csv and projection report.md. The manifests differ only in code_sha256, git commit, environment and timestamps. Timings on the loaded box: selection 10m18s wall/3m40s CPU; full 11m56s/4m44s; projection about 5 min.
- **`atlas uncertainty verify` / `inspect` on the committed runs; smoke run and verify.** verify on docs/results/atlas-m3-intervals-v1/full: PASS, every file identical including calibration_status.json; only the code/git provenance differs; 4m32s (verify_committed_full.log). inspect on selection and full: PASS, 0 errors. Smoke: `uncertainty run --config configs/uncertainty/smoke.json` 35 s, then `verify` PASS identical in 60 s.
- **No residual with target after (calibration origin - latency) is used; calibration-origin rule; quantile rules.** recon_quantiles.py, an independent reimplementation that does not import atlas, rebuilt all 268,800 residual-based rows of the full run: 0 metadata violations, 0 rows differing by more than 1e-9, worst relative difference 8.6e-16 for every pair. The 19,200 climatology rows match to 1.8e-12. Positive control with the cutoff one day later: 112,524 of 127,680 selection rows change (worst 6.6 %). This confirms type-7 empirical quantiles without an (n+1) correction, split-conformal rank ceil(round((n+1)(1-a),9)), the Gaussian RMS of horizon-1 residuals and clipping at 0. Evaluation sets use n=365, selection sets 153-365. No bucket pooling or thin sets for the primary model.
- **Coverage, HAC intervals, p-values and effective sample size recomputed independently.** recompute_coverage_hac.py (own Newey-West and HLN code, scipy.stats.t) over 5 pairs × 2 models × 2 periods × 5 scopes × 2 levels against the committed metrics.json. Maximum absolute differences: coverage 0, CI 5.9e-15, p 1.7e-12, effective n 2.8e-14. Headline: 80 % coverage 0.7990, CI 0.6530-0.9450, p 0.9893, effective n 8.14; 95 % coverage 0.9207, CI 0.8432-0.9983, p 0.4584, effective n 9.04. Bandwidth sensitivity gives effective n 5.2-8.1 and 6.0-9.0. Batch-means effective n is 4.7-11.6 (batch sizes 30-84).
- **Exceedance probabilities: derivation, range, monotonicity, thresholds, Brier statistics.** recon_exceedance.py: trailing180_p75 recomputed at 640 origins, maximum difference 0.0 (11,608.75 at 2026-06-30). Selected-pair probabilities (k+0.5)/(m+1) reproduced to 2.2e-16 and climatology to 1.1e-16. All 288,000 probabilities lie strictly in (0,1) (minimum 6e-9, maximum 0.99999999990). 0 monotonicity violations. event equals actual > threshold everywhere. Brier, skill, gap, p and effective n match the committed exceedance_summary to 8.3e-14.
- **Decision status computed as pre-registered; FAILED_CALIBRATION cannot be overridden.** 95 % coverage on days_8_14 is 0.9179 and on days_15_30 is 0.9167, both outside ±3 pp, which gives failed_calibration (probabilistic.py:1147-1166). B* is insufficient_evidence: every bucket is within tolerance and effective n is 7.24/9.33, matching the claim in UNCERTAINTY.md:278. status_edges.py probes: exact tolerance, p=0.05 and effective n=30 behave inclusively as documented; thin or not-computed gives insufficient. decision_grade requires status==decision_grade (probabilistic.py:1366, decision_inputs.py:337). The status file is SHA-pinned in configs/optimization/*.json (optimization_runner.py:181-210) and must equal metrics.json, which is part of the result fingerprint (uncertainty_runner.py:783-811). The seal check shows differences [], and the status is withheld when the seal is broken (probabilistic.py:1546, 1609-1614).
- **FAILED_CALIBRATION travels with every interval, quantile and stress path.** Live API on 127.0.0.1:9080 (stopped afterwards). /uncertainty/status, /uncertainty/intervals, /forecasts, /simulations/parameters and POST /simulations with basis m3_marginal_quantile q0.9 all return integrity.uncertainty_interval_status=failed_calibration and decision_grade=false, with the status guidance in the notices. /intervals data carries interval_status and the FAILED label. The API exposes no exceedance probabilities, only per-threshold statuses. Dashboard: Uncertainty.tsx, Overview.tsx, Simulator.tsx:790, Decisions.tsx:491 and Provenance.tsx:42 show tags and callouts taken from the API. M4: plans, scenarios, duals, frontier and sensitivity CSVs (810/78/369/37/43 rows) all have m3_interval_status=failed_calibration, and report.md:5,13,16 states it. The quoted 87.9 % q0.9 share was verified (0.8788).
- **HYPOTHETICAL labelling of user thresholds.** HypotheticalThresholdConfig requires provenance Literal['HYPOTHETICAL'], units requests_per_day and assumptions of at least 20 characters (uncertainty_config.py:180-190). The notice is carried into metrics, calibration_status, the projection manifest and report, the dashboard ExceedancePanel caption and UNCERTAINTY.md §9.5/9.8.
- **UNCERTAINTY.md numeric prose matches the metrics.** Verified from metrics.json and my own recomputation: 95 % width 4,669 at h1 and 5,389-5,806 from h2; 14/29 decreasing steps (18 at 80 %); share of monotone paths 0; quantile shares 0.386/0.037/0.042; relative width 0.543; median bounds 16 % below and 28-38 % above (80 %: 10-12 %/11-15 %); latency sensitivity 79.7/92.1/522.2; bandwidth p 0.989-0.991 and 0.445-0.533; WIS 521.82, Winkler 9,614, widths 2,784/5,648; Gaussian 86.7 %/85.7 %; best evaluation WIS within 0.33 %; 96 thin B* rows; reliability-table statements.
- **Pre-registration order, and post-seal code changes only tighten the rules.** git: cfdf634 (config and protocol, 21:07:50) is before the selection run (created 21:10:25, runtime 137.7 s). The seal ff44f54 (21:10:43) is before the full run (created 21:13:54, runtime 179.7 s). `git diff ff44f54 HEAD` on the M3 decision code shows only 3e01ea4: a thin-calibration test, inclusive tolerance, a not-issued test and seal-broken handling. verify at HEAD is byte-identical.
- **M3 test suites.** `uv run --frozen pytest tests/test_m3_results.py tests/test_m3_calibration.py tests/test_m3_intervals_scoring.py -q --no-cov -p no:cacheprovider`: 74 passed in 2 s. test_m3_engine.py + test_m3_config_runner.py: 75 passed in 8m53s. git status stayed clean.
- **Committed spot-check rows equal the regenerated intervals.** All 600 rows of spot_check_intervals.csv are found in the reproduced intervals.csv. Quantiles, prediction and cal_n_effective agree to at most 2.3e-16 relative; only the 17th-digit string formatting differs.

### Not verifiable here (6)

- **That no M3 interval, coverage or probability was computed on the protocol periods before the freeze commit cfdf634.** Only git commit times and the run manifests' self-reported timestamps are available. The order is consistent (freeze, then selection run, then seal, then full run), but the absence of earlier exploratory runs cannot be proven from the repository.
- **Byte-identical reproduction on other CPUs and BLAS builds.** Only this x86_64 container was available. Reproduction here is byte-identical, but the docs note that OpenBLAS kernels differ across CPUs for the regression (1e-9 relative tolerance).
- **Status propagation through nginx/TLS/Basic auth and the public-demo profile.** The API was checked directly on 127.0.0.1:9080. No deployment stack was started in this lens (nginx and the M6 container are other auditors' scope).
- **That the committed web/dist bundle renders the same M3 wording as web/src, and the behaviour in Firefox, Safari and screen readers.** Out of this lens and/or unavailable (only Chromium is installed). I reviewed only web/src text and logic statically.
- **Robustness of the residuals to 311 data revisions (data vintages).** There is a single frozen snapshot (legacy-v0). The M3 evaluation is a historical re-issue on the final vintage; this limitation is disclosed.
- **That the M2 point predictions behind the residuals were themselves leakage-free.** My reconstruction treats forecasts.csv (the M2 engine output) as given and verifies M3's use of it. M2 engine leakage is another lens. The M3 synthetic time-travel tests in test_m3_engine.py pass.

### Auditor's assessment

> I found no computational, leakage or status-logic defect in M3. I re-ran the full M3 reference pipeline here at HEAD 194fa4a: the selection run (10m18s wall, 3m40s CPU on a shared, loaded 4-CPU box), the sealed full run (11m56s wall, 4m44s CPU) and the projection (about 5 min). Every result file is byte-identical to docs/results/atlas-m3-intervals-v1. The result fingerprints are 2e6734... and c4d2ad..., and selection.json, calibration_status.json, metrics.json, report.md and both projection CSVs plus their report match exactly. The manifests differ only in code fingerprint, git commit, environment and timestamps. I also ran `atlas uncertainty verify` on the committed full run (PASS, identical, 4m32s) and `inspect` on both committed runs (PASS). The smoke config runs in 35 s and verifies identical in 60 s.
> 
> My own code (no atlas imports) checked the following:
> - **Quantiles:** it rebuilt all 288,000 interval rows from forecasts.csv and the raw daily series under the documented rule (target_date <= c - latency; c = origin for rolling, the period's first origin for fixed). It used type-7 empirical quantiles, the split-conformal rank ceil((n+1)(1-a)), the Gaussian horizon-1 RMS and climatology. The largest relative difference was 8.6e-16. A positive control that admits one more day of residuals changes 112,524 of 127,680 rows, so the check has power.
> - **Coverage and HAC:** it recomputed coverage, the HLN/Newey-West HAC intervals, p-values (scipy t) and effective sample sizes, and the Brier score, skill and calibration gap. All match metrics.json to 1e-12 or better.
> - **Headline numbers confirmed:** 79.9 % and 92.1 % coverage; 95 % HAC intervals 65.3-94.5 % and 84.3-99.8 %; p 0.989 and 0.458; effective n 8.14 and 9.04 (Bartlett, bandwidth 2h = 60). A batch-means cross-check gives an effective n of 4.7-11.6, so the "8-9" order is plausible.
> - **Exceedance:** probabilities are monotone in the threshold, strictly inside (0, 1), and come from the predictive sample, not from interval endpoints.
> - **Status:** FAILED_CALIBRATION follows the pre-registered rules: 95 % coverage is 91.79 % on days 8-14 and 91.67 % on days 15-30, both more than 3 pp below. B* being `insufficient_evidence` is also correct. No code path can override the status: it is pinned by SHA-256, cross-checked against metrics.json and the run manifest, and `decision_grade` requires the status to equal `decision_grade`.
> - **Propagation:** the status reaches every API response (checked live on port 9080, including a q0.9 stress-path simulation), every dashboard view, all five M4 CSVs, the M4 report and the projection manifest and report. The API never serves exceedance probabilities, only their statuses.
> - **Tests:** all 149 M3 tests pass.
> 
> The problems are in interpretation and wording, not computation. Two are MEDIUM:
> - UNCERTAINTY.md §9.6 describes the flagged target days as "intake drops". In fact demand on all 18 of those days was far above the forecast: 29.8 % of outcomes were above the 97.5 % quantile and none below the 2.5 % quantile.
> - Several M3 conclusions rest on p-values with no multiplicity adjustment. One is printed next to the disclosed M2 limitation without referring to it: "ATLAS intervals beat B* by 8.5 % WIS, p<0.001", while M2's H1 is not significant after Holm (p 0.054). After Holm, the claim against climatology goes from p 0.008 to 0.067.
> 
> The LOW findings are wording and robustness issues:
> - The coverage gate calls the larger HAC p-value "conservative". For a calibration claim that is the lenient direction; it does not bind here.
> - `fixed365` is described as using a 365-day window, but in the selection period it held only 153-182 residuals.
> - The projection report says "every residual observed by then" (it is the last 365 days). The projection label says "calibrated on past errors", and the projection CSV rows carry no status.
> - The effective-sample-size statements contradict each other across sections.
> - The "failed" wording is hard-coded in the API and dashboard instead of being derived from the status.
> 
> None of these needs a re-run. All can be fixed in the docs or in strings for future runs. M3 is fit for a v1.0 research demonstration once the two MEDIUM documentation items are fixed. The intervals are not, and must not be presented as, calibrated.
> 
> Smaller observations that did not merit a finding:
> - **Finite-sample bias:** the empirical methods use type-7 quantiles without an (n+1) correction. Even under exchangeability this gives about 94.5 % nominal coverage at n=365 (about 93.8 % at n=153). The docs disclose only "type 7", and the 0.21 pp bucket miss is smaller than this construction bias. It should not be changed after the fact.
> - **HAC critical values:** the intervals use t(335) critical values despite an effective n of 8-9. With t(ESS-1) the half-widths would grow from ±14.6 to ±17.5 pp (80 %) and from ±7.8 to ±9.1 pp (95 %). The docs call these p-values "indicative".
> - **Effective n units:** the effective n counts 30-day path-equivalents. The Bernoulli-equivalent n implied by the published intervals is about 29 (80 %) and 47 (95 %), so the pre-registered "≥30" threshold mixes units.
> - **Exceedance bounds:** the estimator (k+0.5)/(m+1) has a floor of 0.0014 and a ceiling of 0.9986 at m=365, even for impossible thresholds. Only configured thresholds are used and the API takes no threshold input, so the impact is minimal.
> - **`interval_status` edge cases:**
>   - It would ignore a missing bucket row. This cannot happen today, because the summary always emits every scope.
>   - It labels a bucket with no evaluated forecasts as failed rather than insufficient. That is fail-closed but mislabelled.
>   - Exceedance rows dropped for an unavailable historical threshold do not count as incomplete evidence. There are 0 such rows in the reference runs.
> - **README:** the M3 paragraph says "calibrated only on errors observed by each issue date" without the result; the M6 and M7 paragraphs do state FAILED_CALIBRATION.
> - **Dead code:** `IntervalStatusCallout` in web/src/views/common.tsx is unused.
> - **Spot check:** spot_check_intervals.csv is not hash-pinned. It equals the regenerated rows to 1 ulp; the string formatting differs.
> 
> All scratch artefacts are in <audit-scratch>/uncertainty. The repository stayed clean and every process I started has been stopped.

## M4 optimisation (`optimization`)

### Checks that held up (19)

- **LP as implemented equals OPTIMIZATION.md section 3 and is unit-consistent.** optimizer.py:294-335 builds balance equality (b[t]-b[t-1]+x[t]=d[t]+b0 on day 1), capacity x-q*o-s<=p*H, daily limits as bounds, optional totals, budget row with c_ot/c_s, hard terminal row; objective_vector (206-235) puts w on every b and w+w_T on b[T]; Layout.plan_size=4T with no slack column. ROW_UNITS (112-124) give rhs/dual units consistent with section 3 and section 7.
- **Backlog conservation, capacity, daily limits, overtime total, non-negativity, available work and budget hold on every committed plan (27 scenario/plan combinations, 810 rows).** Independent script (no atlas import) <audit-scratch>/optimization/check_plans.py rebuilds the parameters from the config. Max balance residual 3.6e-12. Capacity, overtime/surge daily, overtime total and budget excess all 0. work_available, regular_capacity, overtime_capacity and extra_capacity columns exact. scenarios.csv totals (cash, overtime, surge, request-days, terminal backlog, served, score) equal the plan sums to <=2.4e-16 relative.
- **Committed optima reproduced by an independently built scipy linprog formulation (different variable layout, highs-ipm).** indep_lp.py: normal 322731.805939 (rel diff 3.6e-16), increased_costs 367786.965444 (0), delayed_surge 331242.174272 (1.8e-16). For all 6 infeasible scenarios: least violation within budget, relaxed lexicographic score, unlimited-cash least violation (9,864.5 / 9,467.8 / 13,866.2 / 32,361.7) and min spend (212,473.66 / 424,432.87) match the committed values.
- **Hand-derived tiny instances solved through atlas' own API match.** tiny.py. A (2 days, cheapest-first): score 15, plan x=(10,9), o=(1,1), s=(1,0), b=(0,1); duals balance (5,4), capacity (-5,-4), overtime limit (-2,-1); all FD brackets locally_unique. B (binding budget D=10): score 15.3333, o2=2/3, budget dual -1/3, overtime-limit d1 dual -8/3, balance d1 dual 20/3 (all as derived). C (target 0, budget 10): infeasible; least violation within budget 1.2, unlimited 0, min spend 16, shortfall 6, relaxed plan s1=0.8, b=(0.2,1.2), score 15.6. D (unreachable at any cash): least violation 1.0, analytic bound 1.0 exact, reasons name the daily overtime and surge limits.
- **Uniqueness and tie-break machinery behaves correctly on known ties.** tiny_ties.py. E (overtime and surge both 3 USD/request): not unique, overtime range 0-4, cash fixed at 12, unique_after_tie_breaks False. F (w=0, timing free): not unique, request-days range 0-2, tie-broken plan serves early (request-days 0), unique after tie-breaks. Full analyse_scenario with brackets and frontier: 0 problems.
- **Shadow prices: sign convention, units and finite differences.** fd_check.py on my own LP (step 0.01) for normal: balance d1 34, balance d15 24.6667, capacity d1 -34, capacity d15 -24.6667, overtime limit d1 -16.5, surge limit d1 -4. Backward and forward secants equal the committed duals.csv values. Convention (<= rows and upper bounds <= 0, balance >= 0) confirmed. duals.csv: 369 duals, 362 locally_unique and 7 domain_boundary_below (zero surge limits of delayed_surge), all bracket_ok True.
- **Optimality certificate holds and the documented margins are accurate.** count_certs.py wraps certify during a scratch run of the reference configuration: 3,292 certify calls, all hold; maxima primal 5.17e-7, dual_sign 1.78e-6, stationarity 4.44e-7, duality_gap 5.17e-7 of tolerance, exactly as OPTIMIZATION.md section 9.5. Committed metrics.json certificates are all holds=True with ratios <= 1.3e-8.
- **Committed results reproduce byte-for-byte.** `uv run --frozen atlas optimize verify docs/results/atlas-m4-optimization-v1`: PASS, all 8 files identical, 29.7 s wall (provenance notes only: code_sha256 and git_commit differ from recorded 50268dc). Scratch re-run of the reference configuration (76.6 s under load): all 8 result files cmp-identical to the committed ones. `optimize inspect`: PASS, 3.5 s.
- **Smoke configuration runs and verifies.** `atlas optimize run --config configs/optimization/smoke.json` into scratch: PASS, 0 errors, 3 expected warnings, 7.2 s wall. `optimize verify` on it: PASS, all files identical, 20.6 s wall under load.
- **Infeasibility diagnostics and relaxed/DIAGNOSTIC plans are labelled and never subtracted from feasible plans.** scenarios.csv: infeasible `optimized` rows blank with solver_status infeasible; relaxed rows plan=relaxed_terminal_target; no-target rows terminal_target_dropped; policies violates_terminal_backlog. Comparisons involve the relaxed plan only against policies of the same infeasible scenario. Sensitivity shows only the least violation for infeasible points. Report and CLI say 'DIAGNOSTIC values, not plans'.
- **Limit-removal diagnosis claims (section 9.1).** relax_check.py, unlimited-cash least violation with one limit removed: increased_demand none 9,864.5, daily overtime 9,864.5, daily surge 0, overtime total 0, surge delay 9,864.5; reduced_capacity likewise; combined_stress surge 0, overtime total 366.2, delay 9,666.2; q0.9 surge 0, overtime total 18,861.7. Matches report.md and OPTIMIZATION.md.
- **Frontier, sensitivity and proved monotonicity properties.** frontier_check.py: all 37 points' least penalty equal my LP; least cash for least penalty 435,458.463 (normal, both variants) and 851,400 (combined_stress, target dropped); combined_stress with target infeasible at every cap. sens_check.py: all 43 sweep scores and feasibility verdicts equal my LP (worst rel 5.8e-16); bisection 1.009828, 0.990054, 0.990382, 0.708246, 1.411940 match section 9.4.
- **Policy rules re-simulated independently.** policy_sim.py re-implements fixed_capacity and backlog_trigger from their documented definitions for all 9 scenarios; max abs difference to the committed plans.csv is 0.0. Like-for-like comparisons (policy_check.py) equal the committed values: normal 57,280.3 request-days fewer and 4,000.00 USD less; increased_costs 4,800; delayed_surge 54,136.4 and 0.
- **API simulation path (src/atlas/api/simulation.py + worker.compute) is correct and robust within the published bounds.** fuzz_api.py: 360 random requests, 287 accepted (73 refused by validation); every accepted one 'ok' with no failures; optimal score equals my LP to <=1.5e-15. 7 hand-built extreme requests (budget 1e8, productivity 0.1, multiplier 2 + shift 5000, rates 0.01/1000/1e4): all ok and matching. Default request reproduces the committed normal scenario (225,275.80 / 48,728.00 / 3,711.64); q0.9 and budget 150,000 reproduce their committed scenarios. CPU time per simulation <= 1.3 s.
- **Exact power-of-two scaling path (R3).** scaled.py: the normal scenario with every quantity ×1000 (K=256) and ×3000 (K=512): certified, score/k = 322731.805939 (rel <= 3.7e-16 vs my unscaled LP), duals unchanged (34, -16.5, -24.6667). Full analysis with 123 brackets and frontier: 0 problems, 0 fallbacks, all locally_unique.
- **Random stress of the full analysis.** rand_small.py, 450 random 1-6 day integer instances (heavy ties and degeneracy; budget, totals, target, w_T, zero limits). analyse_scenario with shadow steps, 5-point frontier and policies: 0 problems, 0 fallbacks, 0 exceptions, strict feasibility and optimum always equal my independent LP.
- **Configuration frozen before first run; solver versions as documented.** git log of configs/optimization/atlas-m4-optimization-v1.json shows only af10ddd (freeze). solver_versions() = scipy 1.18.1, HiGHS 1.12.0, matching OPTIMIZATION.md and uv.lock.
- **Claims: no hypothetical result presented as a real NYC recommendation.** Grep of report.md, docs/*.md, src/atlas/api/*.py and web/src for optimal/recommend/should/staffing/saves/best: every M4 'optimal' sits under the HYPOTHETICAL banner or qualifier ('for these assumed values', 'not a recommendation'). validated_recommendation=False on every CSV row and in the manifest. The only 'Recommended actions' text is in the legacy README section (outside M4).
- **Targeted M4 tests.** `uv run --frozen pytest tests/test_m4_lp_math.py tests/test_m4_planning_policies.py tests/test_m4_round3_solver.py tests/test_m4_verification_solver.py -q --no-cov -p no:cacheprovider`: 232 passed (232 s wall under load); git status clean afterwards.

### Not verifiable here (5)

- **The first and second reviewers' independent re-implementation and review records (OPTIMIZATION.md sections 9.5 and 10: 180 values, 108 and 59 planted mutants, 51 agents).** No artefacts of those reviews are committed. I replaced this with my own independent re-implementation, which agrees on every value I compared (scenarios, relaxed plans, diagnoses, policies, frontier, sweeps, face ranges, duals).
- **Byte-identical determinism under the Haswell/Prescott OpenBLAS kernels and on other machines or CPU families.** I ran the reference configuration and verify on this one machine only; tests/test_m4_results.py::test_byte_identical_under_another_blas_kernel_family is an integration test I did not run (the lead runs the full suite).
- **Uncontended timings (reference run '23 seconds', simulation latency vs the 10 s default timeout on a small VPS).** The machine was shared with other auditors (load average about 14 on 4 cores): wall times were 29.7 s verify, 76.6 s instrumented run and up to 18 s per fuzzed simulation. I measured CPU time instead (<= 1.3 s per simulation). Real VPS latency could not be measured.
- **Dashboard rendering of the certificate text and the M4 views in a real browser.** Out of this lens's scope (the lead runs Playwright). I verified the wording from the source and the value it would display from metrics.json and the API simulation path.
- **Multi-unit problems sharing one budget.** The domain model supports several units, but the configuration and the API accept exactly one, so no published result depends on it. I did not exercise multi-unit problems.

### Auditor's assessment

> I found no release-blocking defect in M4. The LP, its units, its certification and the committed numbers held up under every independent check I ran. The only two findings are LOW wording/labelling issues.
> 
> **How I checked.**
> - I wrote the LP down from src/atlas/optimizer.py:242-356 and compared it with OPTIMIZATION.md §3. They match.
>   - Variables: x, o, s, b per day, 4T columns, no slack column.
>   - Balance equality: b[t] − b[t−1] + x[t] = d[t], with b0 moved to the right-hand side on day 1.
>   - Capacity: x − q·o − s ≤ p·H.
>   - Daily limits as variable bounds. Optional overtime and surge totals. Budget row (c_ot·o + c_s·s ≤ D). Hard terminal row b[T] ≤ target.
>   - Objective: c_ot·o + c_s·s + w·b[t], plus w_T on b[T].
>   - Every coefficient is unit-consistent: requests/day; staff_hours × requests/staff_hour; USD/staff_hour × staff_hours; USD/request; USD/request-day × requests × 1 day.
> - I built my own scipy LP with a different (day-major) layout and no atlas imports. It reproduces every committed number to about 1e-15 relative:
>   - the 3 strict optima and least-cash plans;
>   - the 6 infeasible scenarios' least violations within budget and unlimited, minimum spends, and relaxed lexicographic scores;
>   - all 37 frontier points;
>   - all 43 sensitivity points, and the bisected switch points 1.0098 / 0.9901 / 0.9904 / 0.708 / 1.412;
>   - the policy like-for-like comparisons (57,280 request-days fewer; 4,000 / 4,800 USD less; and so on);
>   - the limit-removal diagnoses (9,666 and 366 for combined_stress; 18,862 for q0.9);
>   - the optimal-face ranges (increased_costs cash 268,996-270,331; w=0 request-days 60,442-224,834; w=8/16 terminal backlog 2,255-2,421; budget×0.75 alternative optimum moving 77 surge requests and 34 overtime hours).
> - I re-simulated both policy rules from their documented definitions and matched the committed plans exactly.
> - Four tiny hand-derived instances, solved through the project's own API, matched my derivations exactly: objective, plan, every dual, finite-difference brackets, diagnosis (least violation 1.2, minimum spend 16, shortfall 6, relaxed plan) and uniqueness verdicts on tie instances.
> - `atlas optimize verify` on the committed results passed and was byte-identical (29.7 s wall). A scratch re-run of the reference configuration was byte-identical on all 8 result files. The smoke config passed run (7.2 s) and verify.
> - The §9.5 certificate claims reproduce exactly when I instrument `certify`: 3,292 certificate checks, all holding, with maximum ratios 5.17e-7 primal, 1.78e-6 dual sign, 4.44e-7 stationarity and 5.17e-7 duality gap.
> - Robustness:
>   - 287 fuzzed API simulations within the published bounds: 0 failures, every optimum equal to my LP to ≤1.5e-15.
>   - 7 extreme in-bounds requests: all correct.
>   - The exact power-of-two scaling path (normal scenario ×1000 and ×3000): exact, with duals unchanged and 0 problems in the full analysis.
>   - 450 random small instances through the full analysis (brackets, frontier, policies, faces): 0 errors, 0 fallbacks, 0 mismatches.
>   - 232 targeted M4 LP and solver tests pass.
> 
> **Claims hygiene.** HYPOTHETICAL, DIAGNOSTIC, FAILED_CALIBRATION and not-a-recommendation labels appear on every CSV row, in the report, the CLI output, the API limitations and the dashboard. I found no unqualified operational recommendation. Relaxed plans are always labelled and never compared against strictly feasible plans.
> 
> **The two LOW findings.**
> - The certificate ratios are exposed without units or meaning, and the dashboard shows one as "gap 6.1e-9" while calling plans an "exact optimum".
> - M4 surfaces call the July 2026 horizon a "FUTURE PROJECTION" without the archived / not-live disclosure the dashboard carries.
> 
> **Minor observations, not findings.**
> - OPTIMIZATION.md §4's first paragraph still says statuses other than 9/10/15 are errors; the later R3-verification amendment in the same section supersedes it. The document is an amendment log, which makes it hard to read as the current spec.
> - The certificate margins and the "3,292" count are not stored in any committed artifact or test. I could only reproduce them by instrumenting `certify`.
> - Decisions.tsx describes the score without the terminal penalty w_T, which API users can set.
> - `verify` passes with "provenance differs" notes for code_sha256 and git_commit (expected: M5-M7 changed src/atlas, yet the bytes reproduce).
> - The API's money-rate floor of 0.01 is conservative rather than strictly needed for the 1e-9 coefficient guard.
> - Wall times of up to about 18 s seen during fuzzing were machine contention (load about 14 on 4 cores). CPU time per simulation was ≤1.3 s, well under the default 10 s timeout.

## Documentation and claims (`claims`)

### Checks that held up (16)

- **README legacy KPIs, model metrics, risk table and shares equal the committed legacy CSVs and are reproduced by `atlas check`.** README.md:93-106,110-119,148-156,164-168 compared with 01 Data/project_kpis.csv, model_metrics.csv, capacity_risk_summary.csv, agency_workload.csv, borough_workload.csv and weekday_workload.csv: 12,331,111 / 9,656.31 / 330,941 / 11,031.37 / 4.93 / 8.23 / 40.1 / 13.4 / 11,609 / 5 / 566 / 101.81 / 2026-07-13 / 13; MAE 514.3, RMSE 676.7, R2 0.5787; High 5/100.97/566, Medium 13/97.23, Low 12/90.16; NYPD 44.55, BROOKLYN 30.38, Tuesday 10,283.3. `atlas check` on a scratch copy: PASS, all 14 KPIs ok, with their meaning labels.
- **Legacy artifacts and legacy README body unchanged since the original commit.** `git diff --stat db46703 HEAD -- '01 Data' '02 Notebooks' '03 SQL' '04 Power BI' '05 Screenshots' '06 Exports'` is empty. Diff of README.md against db46703: only the added notice plus 2 changed lines (notebook filename in the tree and in the link).
- **M2 headline claims in BACKTESTING.md, LEGACY_LIMITATIONS.md and API.md trace to docs/results/atlas-m2-rolling-origin-v1/full/metrics.json.** Script scratchpad/audit/claims/m2_check.py: confirmation MAEs 984.918 / 1112.172 / 1371.533. H1 diff -127.25 [-256.44, 1.94], DM p 0.0535, Bartlett 0.0465, eff n 13.8. H2 -386.6 [-645.0, -128.2]. Lead shares: Nov-Feb 50.6 %, Dec-Jan 39.9 %, Mar-May 59.5 %, May 30.9 % (docs: 51/40/60/31). Months worse than B*: 3 development + 3 confirmation = 6 of 28. Legacy-window legacy MAPE h2-30: 5.54-5.92 (docs 5.5-5.9). Confirmation legacy MAPE h>=7: 9.05-9.78 (docs 9.0-9.8). MAE h14-30: 1,070-1,104 (docs about 1,070-1,100). Projection total 333,967.8 (docs 333,968).
- **M3 numbers in UNCERTAINTY.md trace to atlas-m3-intervals-v1 metrics and calibration_status.** Coverage 0.7990/0.921, CI 65.3-94.5, eff n 8.14/9.0. 95 % width h1 4,669, h2-30 5,389-5,806, decreasing steps 14 (95 %) and 18 (80 %). Exceedance mean p 0.3326 vs freq 0.3863, Brier skill 0.215, p 0.0375, eff n 10.97/12.79/16.18. Quantile 0.5 calibration 38.6 % (dashboard table).
- **M4 results table OPTIMIZATION.md section 9.1 equals scenarios.csv.** All 9 reported/relaxed plans match: e.g. normal 225,276 / 48,728 / 3,712 / 2,988 / 1,534, unique; increased_costs 268,996 / 49,396, unique_optimal_plan False; m3_marginal_q90_stress 300,000 / 813,258 / 56,612. Docs cite 5.2e-7..1.8e-6 of tolerance, so at least 5e5 times below; 1/1.8e-6 = 5.6e5.
- **Every API response states what its numbers are.** API on 127.0.0.1:9320; fetched system, models, forecasts, history, metrics, uncertainty status/intervals, scenarios, comparison, scenario detail, simulation parameters and POST /simulations. All have validated_recommendation=false and uncertainty_interval_status=failed_calibration. The H1 'not statistically established ... Holm-adjusted p 0.054' notice is on forecasts, metrics, models and intervals. HYPOTHETICAL is on every M4 response. Scan of 151 response strings: 'recommend' only negated or for the M2 model. No 'advance', 'shortfall', 'risk', 'guarantee', 'production' or 'real-time'.
- **Dashboard labels carry the disclosed limitations next to the numbers.** Chromium dump of all 6 pages (scratchpad/audit/claims/page_*.txt). Overview H1 tile: 'NOT SIGNIFICANT AFTER HOLM'. M3 tile: FAILED_CALIBRATION. M4 tile: HYPOTHETICAL. Forecast panel: 'An archived projection, not a live forecast'. Infeasible scenarios tagged DIAGNOSTIC. The footer attributes 'NYC Open Data, 311 Service Requests (dataset erm2-nwe9)'. index.html title and meta are neutral ('ATLAS research dashboard ... with their limitations').
- **Committed result files are fingerprinted.** Script scratchpad/audit/claims/manifests.py: every file in the 9 docs/results directories matches its manifest SHA-256. The only unlisted files are spot_check.csv and spot_check_intervals.csv, which tests/test_m2_results.py and tests/test_m3_config_runner.py recompute.
- **Snapshot provenance recorded honestly.** snapshots/legacy-v0/manifest.json provenance records source, publisher, dataset erm2-nwe9, endpoint, the SoQL method (it matches notebook cells 1-3: created_date in [2023-01-01, 2026-07-01), borough filter on segments), extracted_at null with a note, completeness 'incomplete', known gaps and 'terms of use not verified'. DATA_SNAPSHOT.md:153-166 matches.
- **Test counts quoted in docs match collection.** pytest --collect-only: test_m7_fixtures 15, test_m7_dashboard 93, test_m7_deploy 44, test_m7_proxy 32; M6 modules 39/57/31/39/20/14/38/10/47/22/22, all equal to DASHBOARD.md section 13 and DEPLOYMENT.md section 16. Whole suite: 2,966 collected. Vitest 166 (DASHBOARD.md) equals the lead's 166/166.
- **npm supply-chain statements.** package-lock.json: 426 unique name@version entries (DASHBOARD.md:111 says 426). `npm audit --package-lock-only` on a copy of the lock: 0 vulnerabilities.
- **Dashboard build reproducible from source.** Scratch rebuild of web/ with the locked node_modules: every file in dist/assets, index.html and favicon.svg is byte-identical to the committed web/dist.
- **Relative links and anchors resolve.** 95 relative links in README.md, docs/*.md and docs/results/**/report.md checked: none broken.
- **Runtime dependency licences are permissive.** Python runtime closure: numpy, pandas, scipy, starlette, uvicorn, click and idna BSD; fastapi, pydantic, h11 and anyio MIT; typing-extensions PSF; dateutil BSD/Apache. Browser bundle: react, react-dom, scheduler and zod MIT; d3-* and internmap ISC; Inter OFL-1.1. No copyleft.
- **Legacy PDF and screenshots consistent with LEGACY_LIMITATIONS.** pdftotext of 06 Exports PDF: High days with gaps 12/84/58/202/210, total 330,941 vs 348,270 (= 30 x 11,609). pdfinfo CreationDate 2026-07-18, after the first 'shortfall' 2026-07-13, as LEGACY_LIMITATIONS.md:87-88 states.
- **No absolute security or production claims.** grep over README and docs for secure, enterprise, production-ready, real-time, guarantee: only section headings ('secure installation') and 'no coverage is guaranteed'. DEPLOYMENT.md:9 and README.md:44 state that nothing has been deployed.

### Not verifiable here (7)

- **The GitHub repository description string ('Demand forecasting and capacity-risk analytics project...') and whether the repository is public.** Repository metadata lives on GitHub; the audit rules forbid contacting external services other than package registries. The original commit message 'Add complete demand forecasting and capacity-risk analytics project' suggests the description is the legacy framing, but I did not check it.
- **NYC Open Data terms of use and the required attribution or non-endorsement wording for redistributing the derived aggregates (01 Data, 80,582 rows) on GitHub and in deployments.** External legal terms, not verifiable offline; I did not invent permissions. The repo itself says they are unverified (snapshot manifest terms_of_use; DATA_SNAPSHOT.md:162; ROADMAP.md:277-278). Owner decisions: a code licence; whether redistribution of derived data is permitted; attribution and non-endorsement text; also the exact dataset title (the manifest says '311 Service Requests from 2020 to Present').
- **Byte-for-byte `atlas backtest verify` of the full M2 reference run (the dashboard says 'The run is reproduced byte for byte by atlas backtest verify'); CI verifies only the M2/M3 smoke runs and the M4 reference.** I started it in a scratch copy but stopped it after ~4 minutes: other auditors were running the same verify (and the M3 verify) on this 4-core machine at the same time. Left to the reproducibility lens.
- **That all 426 npm packages were published before 2026-10-01 (DASHBOARD.md:110-114 cooldown claim).** Not queried against the registry's publish times; outside the core of this lens.
- **Whether the Inter woff2 metadata (copyright plus licence URL, no licence text) satisfies OFL-1.1 condition 2 on its own.** A legal judgement. The finding recommends shipping the full OFL text, which removes the question.
- **Legacy notebook markdown text.** The notebook has 20 code cells and no markdown cells, so only code comments and printed strings could be audited (cell 1 dataset name and window, cell 17 risk tiers and actions).
- **Python suite pass/coverage figures quoted in docs.** I was not to run the full suite (the lead's task). I verified collection counts only (2,966 tests; the per-module counts match the docs).

### Auditor's assessment

> The ATLAS documentation from M2 to M7, the API strings and the dashboard labels are careful and numerically accurate. I traced more than 60 numeric claims to committed results or to the snapshot: M2 MAEs, H1/H2 tests, month shares, legacy MAPE ranges, M3 coverage/width/exceedance figures, all nine M4 scenario rows, API accuracy context, snapshot reconciliation and documented test counts. Every one matched within its stated rounding. Every API response I fetched (13 endpoints, including a live simulation) carries the right integrity labels: validated_recommendation=false, failed_calibration, the H1 "not significant after Holm" notice on M2-serving responses, and HYPOTHETICAL on M4 responses. "Recommend" appears only in negated phrases or for the M2 model recommendation. In Chromium, the rendered dashboard shows the qualifier next to every number (archived projection, NOT VALIDATED, Not significant after Holm, FAILED_CALIBRATION, HYPOTHETICAL, DIAGNOSTIC). Every committed result file matches its manifest SHA-256. The snapshot provenance is recorded honestly, including what is missing: extraction time, terms of use. The legacy artifacts are byte-unchanged since the original commit.
> 
> Readiness fails mainly at the front door. The top-level README is the first thing a hiring manager reads. It still opens with the legacy title and intro, then the legacy Power BI screenshots (with a "Recommended Action" column), "Executive KPIs" and "Key Business Insights". These state, without qualifiers, claims that ATLAS's own docs refute or forbid: "Forecast accuracy improved by 40.10%", "13 days of advance warning", "selected because it produced the lowest holdout-period error" (M2 says the legacy model should not be used), "Recommended actions", R² 0.5787 and "the next 30 days are expected to generate 330,941 requests". The M0 notice qualifies only the MAPE and the "planning capacity". I judge it insufficient for a v1.0 scientifically honest portfolio release (HIGH; a README-only fix that changes no research artifact). Secondary issues:
> - **Stale status:** ROADMAP and DASHBOARD.md still say M7 is on a feature branch awaiting review; the README notice is headed "(milestone M0)".
> - **Licence notices:** the committed and deployed dashboard bundle ships minified React, zod and d3 with every MIT/ISC notice stripped, and without the Inter OFL licence text (MEDIUM). Vite 8.3.1's build.license fixes most of it; I confirmed in a scratch build that it changes no asset byte.
> - **Wording:** "decision-intelligence engine/research" in pyproject and the Overview H1; the API's projection label says "FUTURE PROJECTION … No outcome has been observed" with no "archived" or issue-date notice; "tens of independent observations" where the measured figure is 8-9; dashboard method links point at mutable main.
> 
> Minor observations not worth findings:
> - README.md:7-9 says only the notebook link was corrected, but the tree's notebook filename was corrected too.
> - The README M7 paragraph says "Every number is the API's", but the chrome hard-codes the snapshot end date and "nine reference scenarios" (disclosed in ROADMAP).
> - The M2 projection report says "No prediction interval is provided (milestone M3)". It is stale, but it is a committed result.
> - The legacy SQL comment "Confirm daily segmented totals reconcile to daily totals" is false (1,273 of 1,277 dates differ). This is documented in DATA_SNAPSHOT as LEGACY-V0-EX-001; legacy files must not change.
> - The notebook has no markdown cells (20 code cells), so the notebook audit covered code comments only.
> - The /system endpoint still reports milestone "M5" (a schema Literal).
> - Owner decisions still open for v1.0 (already disclosed, not new defects): a code licence; whether redistributing the derived NYC 311 aggregates (01 Data, 80,582 rows) is consistent with the NYC Open Data terms of use, which the repo states are unverified; the attribution and non-endorsement wording; and whether to bump versions from 0.7.0.

## Release engineering and testing (`release`)

### Checks that held up (15)

- **Version declarations agree (0.7.0) across the Python package, API contract, system endpoint, dashboard and dist manifest.** git grep: pyproject.toml:3, src/atlas/__init__.py:49, uv.lock:120, web/package.json:3, web/package-lock.json:3,9, docs/api/openapi.json:4893 info.version, web/dist/atlas-dashboard.json:3, index-D84JVxAN.js and Provenance-CiRCJuIt.js, atlas_version in all 14 web/src/test/fixtures, tests/test_m5_endpoints.py:51. The /api/v1/system fixture reports atlas_version 0.7.0 and api_version v1. load_build/manifest_record on web/dist: version 0.7.0, verify_build problems [].
- **SHA-256 of every file listed in every docs/results manifest.** Script <audit-scratch>/release/hash_manifests.py: total=58, ok=40, mismatch=0, absent=18 (forecasts/fits/coefficients/observed_inputs/intervals/exceedance CSVs, left out by design). Cross-references hold: the snapshot manifest SHA 3a57e762… equals data.manifest_sha256 in every manifest; the M2 projection manifest 6baad8e2… and M3 projection manifest f9568b4f… equal the M4 inputs; the M3 recommendation path SHA 2fa912c3… matches; daily_total.csv equals 16a338f4…. Two committed files are in no manifest: full/spot_check.csv and full/spot_check_intervals.csv.
- **Committed M4 reference reproduces byte for byte at 194fa4a.** `atlas optimize verify docs/results/atlas-m4-optimization-v1`: PASS, all 8 files identical, result fingerprint 0dc4eb0f…, exit 0 (1m00s). Recorded code_sha256 b579fc3e… vs now 9d67b400… (diagnostic only).
- **Committed M2 full, M2 delay1, M3 full and M3 selection runs reproduce byte for byte with the current code (AVX-512 host).** backtest verify full: PASS, all 7 files identical including the uncommitted forecasts.csv, fingerprint d53bc911… (19m31s under load). uncertainty verify full: PASS, 8 files identical, c4d2adbf… (13m45s). uncertainty verify selection: PASS, 2e673489… (5m52s). backtest verify delay1: PASS, 79d4f796… (3m53s). All exit 0.
- **M2 projection, M3 projection and legacy holdout regenerate identically.** `atlas forecast --recommendation …/full/recommendation.json`: coefficients.csv, config.json, fits.csv, projections.csv and report.md byte-identical; the manifest differs only in code/env/commit/timestamp. `atlas uncertainty project --status …/full/calibration_status.json`: projection_exceedance.csv 6eea63e3…, projection_intervals.csv 0e648e0a… and report.md e361024f… all identical to the committed files. `atlas backtest legacy --output-dir`: both legacy_one_step_holdout files identical.
- **Disclosed exception: M2 development run does not verify with current code but does at its own commit.** At 194fa4a: FAIL, coefficients.csv, metrics.json and report.md differ; forecasts/fits/observed_inputs identical. With the code of a16672f exported to scratch (its code_fingerprint is ebb30a06…, matching the manifest): PASS, fingerprint fbb948ec…, exit 0. Matches BACKTESTING.md:817-822 and :880.
- **Python 3.13 reproducibility.** /usr/bin/python3.13 (3.13.16): `uv sync --locked` into a scratch venv installs the same pins. M4 optimize verify PASS, all files identical. M2 spot-check recomputation bit-identical for all 7 models (script spot_bits.py; the same holds on 3.12.3). `atlas api openapi` byte-identical to docs/api/openapi.json. 203 tests (test_cli, test_m5_endpoints, test_m5_contract, test_m4_lp_math, test_m3_results, test_features) passed with filterwarnings=error.
- **Dashboard build reproducible from sources and package-lock.** Scratch copy of HEAD plus a copy of web/node_modules, Node 22.22.2, `npm run build`: `diff -r web/dist <repo>/web/dist` shows no difference.
- **Release manifest content and integrity.** build_manifest on a scratch release built with `uv sync --locked --no-dev --python /usr/bin/python3.12`. check_release returned []. It records git_commit, atlas_version, python 3.12.3, lock_sha256 658f41e1… with runtime packages, snapshot manifest SHA and data fingerprint, 77 research files with a tree fingerprint, 403 source files (web/package-lock.json and web/dist included), api_contract_sha256, the dashboard record (manifest SHA, fingerprint, files, routes, CSP) and scientific_status. The source fingerprint 2d23ef9a… was identical across two rebuilds. uv -v showed 'Performing direct build' (no unpinned uv_build download).
- **Lockfiles consistent.** `uv lock --check --offline` in scratch: Resolved 44 packages, no change. CI uses `uv sync --locked`, `npm ci` and a hash-pinned uv 0.11.32 (.github/ci-requirements.txt); the actions are SHA-pinned.
- **Skips cannot silently hide important tests in CI.** 2966 tests collected. Skip sites listed: m6_helpers.need_tools fails instead of skipping when ATLAS_REQUIRE_DEPLOY_TOOLS=1 (m6_helpers.py:48-54). test_m6_container skips unless ATLAS_E2E_CONTAINER=1 (22 tests, run in the deploy-e2e job). Root-only skips (test_m6_config.py:250, test_m6_scripts.py:98) do not apply to CI's non-root runner. Git-history skips are covered by fetch-depth 0. No silent early returns found. Playwright: forbidOnly in CI, retries 0, no test.skip.
- **Test-quality basics.** pyproject: filterwarnings=['error'], --strict-markers, --strict-config, branch coverage with fail_under=90. AST scan of tests/test_*.py found one assertion-free test, test_m3_engine.py:621; the rest of the scan's hits use pytest.raises helpers or check=True. Vitest coverage with a 15 s async timeout: Statements 94.66 %, Branches 82.79 %, Functions 93.84 %, Lines 96.17 % against thresholds 85/75/85/85. No wall-clock values in API responses (grep: datetime.now only in deploy/experiment provenance), so the byte-identical fixtures cannot age.
- **E2E covers both nginx profiles; public demo never computes.** web/playwright.config.ts projects nginx-private (8443) and nginx-public (8444). e2e/nginx-public.spec.ts asserts no POST and no /api/v1/system request. tests/test_m7_proxy.py:275-300 checks the public profile refuses computation.
- **Rollback is documented and unit-tested, including the dashboard.** DEPLOYMENT.md:557-590; test_m6_releases.py:505 (rollback and rollback --to); test_m7_deploy.py:398-418 (the site root follows A after an automatic and an explicit B->A rollback); releases.py install() publishes the dashboard per release id before writing the site.
- **Snapshot and legacy reproduction.** `atlas check`: integrity, validation and reproduction all PASS, with the two documented INFO exceptions LEGACY-V0-EX-001/002.

### Not verifiable here (7)

- **Full pytest suite, Playwright end-to-end and tests/test_m6_container.py (including the real rollback and build-release.sh on a systemd host).** Reserved for the lead auditor by the task rules (long-running, fixed ports and containers). I judged the explicit-rollback finding from reading the test, without running it.
- **Byte or tolerance reproduction on a non-AVX-512 CPU (the GitHub runners' AVX2 kernels).** This host has AVX-512. I did not run test_cross_environment.py's Prescott subprocess check. All my byte-identity results hold for this CPU class only.
- **Actual CI run durations and margins against the 45-minute job timeouts, and whether the Vitest flake occurs on GitHub runners.** Contacting GitHub or any external service was outside the allowed network scope.
- **Running ATLAS natively on Windows.** I could only simulate a core.autocrlf=true checkout on Linux. Other Windows behaviour (multiprocessing spawn, file permissions) is untested.
- **Remote git tags or release history.** Only the local clone was inspected (no tags present); `git ls-remote` would contact GitHub.
- **Timing-sensitive pytest concurrency tests (test_m5_concurrency.py:96-104, :143) under load.** They bind OS-assigned ports outside my assigned range 9360-9379, so I did not run them. The SIGTERM test that needs no port passed 8/8 times under load.
- **Firefox/WebKit, real screen readers.** Disclosed limitation; not installed in this environment.

### Auditor's assessment

> Overall: in this area ATLAS is close to ready for a v1.0 research demonstration. I found no defect that compromises the published numbers. I re-derived every committed research artifact at commit 194fa4a on this AVX-512 host:
> - M2 full, M2 delay1, M3 selection, M3 full and M4 pass their `verify` commands byte for byte.
> - The M2 and M3 projections and the legacy holdout regenerate to identical bytes.
> - M2 development fails `verify` with the current code (coefficients.csv, metrics.json and report.md differ; forecasts.csv is identical). It passes at its own commit a16672f, whose code fingerprint ebb30a… matches the manifest. BACKTESTING.md:817-822 and :880 disclose exactly this.
> 
> All 40 manifest-listed files that are committed match their SHA-256. The other 18 are large files left out on purpose, and the verify runs regenerate them to the recorded hashes. The committed web/dist rebuilds byte-identically from source and package-lock. On Python 3.13.16, `uv sync --locked` works and the following are bit-identical: the M2 spot check, the M4 reference and the OpenAPI document; a 203-test subset also passes.
> 
> Versions agree at 0.7.0 everywhere: pyproject, `__init__`, uv.lock, package.json/lock, OpenAPI info.version, the dist manifest and JS chunks, 14 fixtures and the literal in test_m5_endpoints.py:51.
> 
> Release-blocking or meaningful problems (all MEDIUM):
> 1. `.gitattributes` protects the snapshot, web/dist and the fixtures from CRLF conversion, but not docs/results JSON/MD or docs/api/openapi.json. On a core.autocrlf=true clone (the Git for Windows default), 29 of 40 result files fail their hashes, the API returns 503 on every data endpoint, and `optimize inspect` fails. `git status` still shows a clean tree.
> 2. A pre-release version (1.0.0-rc.1, or the PEP 440 form 1.0.0rc1) is refused in three places: build-release.sh:96, layout.py:47 and dashboard.py:91. Yet the Vite build happily writes it. In practice the RC can only be numbered 1.0.0.
> 3. The Vitest suite fails under CPU contention. About 60 of 90 findBy queries use testing-library's 1 s default. Under load, 2-7 tests fail per run (4/4 runs). With a global asyncUtilTimeout of 15 s, all 166 pass and coverage is well above the thresholds.
> 
> LOW findings:
> - No test ties the version declarations together. A bump to pyproject alone passes the tests and only fails on the server.
> - release.json's environment_fingerprint differs between two builds of the same commit at the same path, because uv's editable-install metadata contains timestamps.
> - The container e2e "explicit rollback" targets the release that is already current, so it is a no-op.
> - The M2/M3 reference reproduction is never automated, and the docs never say so.
> - docs/results/atlas-m3-intervals-v1/full/spot_check_intervals.csv is a lossy copy of the reference intervals (off by 1-2 ULP). It is undocumented, unfingerprinted and has no generator.
> 
> Notes for the RC manifest and version bump:
> - The release manifest (deploy/manifest.py) is already strong: commit, version, Python, lock SHA and runtime packages, snapshot manifest SHA and data fingerprint, every research file (77) and every source file (403, including web/package-lock.json and web/dist), contract SHA, dashboard record and scientific status. `check_release` returned [] for a release I built in scratch, and the source fingerprint was identical across rebuilds. uv 0.11.32 builds the project directly ("Performing direct build"), with no unpinned uv_build download.
> - For an RC I would add: the date, commit and host CPU class of the manual M2/M3 reference verification; the CI run id; API info.version and api_version; the Node/npm toolchain behind web/dist; and the code commit that produced each result (manifests show 0.2.0/0.3.0/0.4.0 and commits 28708bd, a16672f, ff44f54, cfdf634, 50268dc). Note that `scientific_status` hard-codes m4_parameter_provenance, point_forecast_validated and validated_recommendation (manifest.py:184-187).
> - Files a 1.0.0 bump must touch: pyproject.toml:3, src/atlas/__init__.py:49, uv.lock (atlas entry, line 120), web/package.json:3, web/package-lock.json:3,9, docs/api/openapi.json (regenerate), web/dist (rebuild: the version is baked into the index and Provenance chunks, which renames their hashed file names and changes atlas-dashboard.json), the 14 fixtures (`ATLAS_UPDATE_DASHBOARD_FIXTURES=1`), tests/test_m5_endpoints.py:51 and the DEPLOYMENT.md:739 mention. test_m6_manifest.py:39 splits the release id on '-', so it breaks with a hyphenated version.
> - Any fix that changes docs/results must also update the pin test test_m6_manifest.py:181 (byte-identity with the M5 merge commit 3f48f20).
> 
> Test-suite health:
> - 2966 tests collected. Skips: run as root, 24 skip (22 opt-in container tests plus 2 that do not run as root). On a non-root CI runner, only the 22 container tests skip in the quality job, and the deploy-e2e job runs them. `ATLAS_REQUIRE_DEPLOY_TOOLS=1` turns a missing nginx, openssl, shellcheck or systemd-analyze into a failure.
> - Conditional skips: git history (test_snapshot.py:44,247 and test_m6_manifest.py:185; CI uses fetch-depth 0), OpenBLAS dispatch (test_cross_environment.py:119), systemd-analyze older than 250 (test_m6_render.py:228), and euid/egid (test_m6_config.py:224,250, test_m6_scripts.py:98).
> - pytest runs with filterwarnings=error, --strict-markers and --strict-config, and a 90 % branch-coverage gate. 34 `pragma: no cover` lines, mostly defensible; the whole simulation worker loop is excluded (api/worker.py:148) and tested through the pool.
> - An AST scan found only one assertion-free test, test_m3_engine.py:621 (a "does not crash" check).
> - Playwright covers the API, a phone viewport and both nginx profiles; the public demo asserts no POST and no /api/v1/system.
> - Possibly timing-sensitive pytest tests, not reproduced: test_m5_concurrency.py:96-104 (median /health/live latency < 0.25 s while workers compute), :143 (0.5 s sleep, then elapsed < 1 s) and :398 (50 ms before SIGTERM; 8/8 passes under load).
> - Hypothesis runs randomised (no derandomize), so property tests are non-deterministic by design.
> 
> Minor observations:
> - ROADMAP.md:3-6 still says M7 "is implemented on feature/atlas-m7-dashboard and awaits review", although 194fa4a merged it.
> - README.md:5 still titles the ATLAS notice "milestone M0".
> - web/.npmrc says "CI and releases install with npm ci", but releases need no Node.js.
> - This clone has no git tags, and I found no CHANGELOG or versioning policy.
> - CI audits npm (`npm audit`) but not Python dependencies (no pip-audit or uv audit).
> - requires-python ">=3.12" admits 3.13+, which CI never tests. 3.13 worked in my checks.
