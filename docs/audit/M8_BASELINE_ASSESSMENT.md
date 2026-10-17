# M8 baseline assessment (before any correction)

This is the original M8 assessment of ATLAS, written before any code, test or documentation was
changed. It is preserved as it was; what was corrected afterwards, and how, is recorded in
[M8_CORRECTIONS.md](M8_CORRECTIONS.md), and the final verdict in
[../release/RELEASE_GATE.md](../release/RELEASE_GATE.md).

- The full register of findings, with evidence, reproductions and verification votes, is in
  [M8_FINDINGS_REGISTER.md](M8_FINDINGS_REGISTER.md).
- The positive evidence (what held up) and the limits of this audit environment are in
  [M8_AUDIT_EVIDENCE.md](M8_AUDIT_EVIDENCE.md).

## 1. Baseline under audit

| Item | Value |
|---|---|
| Commit | `194fa4a078ff7b65ada8d509d15d01309339abd9` (`main`, merge of arthur-fay/demand-forecasting-model#9, M7) |
| Tree | `00c00c998b68e9c3ad59ce97420acd5c3a36381d` |
| Version | 0.7.0 (`pyproject.toml`, `atlas.__version__`, `web/package.json`, the OpenAPI contract, the dashboard build) |
| `uv.lock` | SHA-256 `658f41e170922dad73f53ce5ad1a9917eac486f38601ad0988e74c1ba297b991` |
| `web/package-lock.json` | SHA-256 `190c5f45054ebffe6b49f8eb5ae121fab9a59bc631d33d99751bd90c0a24c591` |
| `docs/api/openapi.json` | SHA-256 `03a05f2372cab8f9a8bcec1306696f47bd0ab8be10423c997b83f3ba9643efcb` |
| `snapshots/legacy-v0/manifest.json` | SHA-256 `3a57e7627d5e4eb5ba73cf2127a936a3d58131a79d52d1c2b8a0a695ad75fc5f` |
| `web/dist/atlas-dashboard.json` | SHA-256 `1da6e2c5da51cb949a82e420ede29ab4e79fd9e4688c1256a63e00c3002f5451` |
| `.github/ci-requirements.txt` (hash-pinned uv) | SHA-256 `239f731eda909713f470adac2a90fddff2804bb5074456a49411115b07dfb18f` |

Preconditions, checked before starting: arthur-fay/demand-forecasting-model#9 is merged into
`main` as `194fa4a`; the three post-merge CI jobs of run 38934624918 (`quality`, `deploy-e2e`,
`dashboard`) all succeeded; local `main` equals `origin/main`.

Audit environment: a disposable Ubuntu 24.04 container, 4 vCPUs, 15 GiB memory; Python 3.12.3
with uv 0.11.32; Node.js 22.22.2 (the CI version, downloaded and checked against its SHA-256);
nginx 1.24.0, shellcheck 0.9.0, OpenSSL 3.0.13, Docker 29.8.2; Chromium 1194 for Playwright
1.56.1. No Firefox or WebKit build is available here. Outbound access is limited to package
registries and GitHub through a proxy; nothing touched the owner's VPS, DNS or any deployment.

## 2. Inventory

| Component | Contents at the baseline |
|---|---|
| Legacy project (preserved) | `01 Data` (16 CSV tables), notebook, SQL script, Power BI file, PDF, 3 screenshots; byte-identical to the original commit `db46703` |
| Data snapshot | `legacy-v0`: 16 tables, 80,582 data rows, 7 artifacts, SHA-256 manifest; NYC Open Data 311 (`erm2-nwe9`), 2023-01-01 to 2026-06-30, extraction time not recorded |
| Python package `atlas` | 88 modules, 38,537 lines: snapshot and contracts (M0), quality and features (M1), backtesting (M2), intervals and exceedance (M3), LP decision simulation (M4), FastAPI API (M5), deployment tool (M6), dashboard manifest (M7) |
| Runtime dependencies | numpy 2.5.4, pandas 3.0.6, scipy 1.18.1 (HiGHS), pydantic 2.14.1, fastapi 0.141.1, starlette 1.7.0, uvicorn 0.54.0, h11 0.16.0 (43 locked packages with hashes) |
| Tests | 80 Python test modules (43,579 lines); 17 Vitest files; 8 Playwright specs |
| Dashboard | `web/` (React 19, TypeScript, Vite 8); committed build `web/dist` (17 files, manifest of SHA-256s); 428 locked npm entries |
| Committed research results | `docs/results/`: legacy one-step holdout (3 files); M2 `atlas-m2-rolling-origin-v1` development, full and projection (17 files) and the `-delay1` sensitivity (5 files); M3 `atlas-m3-intervals-v1` selection, full and projection (17 files); M4 `atlas-m4-optimization-v1` (9 files) |
| API contract | `docs/api/openapi.json`, 14 operations (12 under `/api/v1`, 2 health probes) |
| Deployment package (M6 phase A) | `src/atlas/deploy`, `deploy/scripts` (bootstrap, release build), `deploy/templates` (systemd, nginx, journald), a test Dockerfile; never deployed |
| Documentation | README and 21 Markdown documents in `docs/` (8,865 lines) |
| CI | one workflow, three jobs (`quality`, `deploy-e2e`, `dashboard`), actions pinned to commit SHAs |

## 3. Baseline verification (measured here, on the unchanged commit)

| Check | Result |
|---|---|
| `ruff format --check .` / `ruff check .` | 190 files formatted; all checks passed |
| `mypy` (strict) | no issues in 88 source files |
| `pytest` (full suite, `ATLAS_REQUIRE_DEPLOY_TOOLS=1`, coverage gate 90 %) | **2,942 passed, 24 skipped**, coverage **96.01 %**, 57 min 15 s on the loaded audit machine. The 24 skips: 22 opt-in container tests (run separately below) and 2 tests that need a root / non-root user respectively |
| `shellcheck --severity=style deploy/scripts/*.sh` | clean |
| `atlas check` | PASS: integrity, validation, reproduction; 14 of 14 legacy KPIs reproduced with their meanings |
| `atlas quality` | PASS: 28 flags (0 missing periods, 11 surges, 4 low-volume days, 8 intake interruptions, 4 catch-up surges, 1 unassigned share) |
| `atlas backtest legacy` | PASS: 90 of 90 holdout predictions and 30 of 30 forecasts reproduced |
| M2 smoke run and `backtest verify` | PASS, byte-identical (maximum prediction difference 0.0) |
| M3 smoke run and `uncertainty verify` | PASS, byte-identical |
| M4 smoke run, `optimize verify`, `optimize inspect` | PASS, byte-identical |
| Committed M4 reference run: `optimize verify` and `inspect` | PASS, reproduced byte for byte (1 min 49 s) |
| Full M2 reference run, re-run by the forecasting auditor | byte-identical to the committed results (result fingerprint `d53bc911…`, 197,610 records) |
| Full M3 reference pipeline (selection, sealed full run, projection), re-run by the uncertainty auditor | every result file byte-identical (fingerprints `2e673489…`, `c4d2adbf…`) |
| `atlas api openapi` against `docs/api/openapi.json` | identical |
| Prettier, ESLint (type-aware, jsx-a11y), `tsc` strict | clean |
| Contract types regenerated (`npm run gen:api`) | identical to the committed file |
| Vitest with coverage | **166 of 166** in 17 files; statements 94.66 %, branches 82.79 %, functions 93.84 %, lines 96.17 % |
| Reproducible dashboard build (`npm ci`, `npm run build`) | `web/dist` byte-identical; `verify:dist`: 17 files match the manifest; all five size budgets met |
| `npm audit` | 0 vulnerabilities (all severities, 428 entries) |
| `pip-audit` on the exported lock (supply-chain auditor) | 0 known vulnerabilities in the 40 platform-applicable packages |
| Playwright (Chromium): API, phone, nginx private, nginx public demo | **56 of 56**. The 51 API and phone tests pass as they are. The 5 nginx tests failed here with `ERR_CONNECTION_RESET` because this sandbox exports an `HTTPS_PROXY` that Chromium applied to the test host name; with the proxy variables unset they pass 5 of 5 (CI has no proxy) |
| Disposable Ubuntu 24.04 systemd deployment test (`test_m6_container.py`) | see section 3.1 |
| CI on `194fa4a` (run 38934624918) | 3 of 3 jobs green; every job warns that `actions/checkout`, `setup-python` and `setup-node` target the deprecated Node.js 20 |

### 3.1 The container deployment test

The first run in this environment failed every step, correctly: the read-only preflight refused
the "server" because the shared machine's five-minute load average was 8.25 on 4 CPUs (above its
2.8 limit) while ten audit agents were working. That is the preflight doing its job, not a
defect. The test was repeated once the machine was quiet; its result is recorded in
[M8_CORRECTIONS.md](M8_CORRECTIONS.md) section 1 together with the post-fix runs.

## 4. Scientific and deployment limitations already disclosed at the baseline

These were documented before M8 and are not counted as new defects:

- The M2 recommended model (`atlas_linear_regression`) is ahead of the best simple baseline B\*
  (same-weekday median of five weeks) by 11.4 % MAE on the confirmation period, but the 95 %
  interval of the difference includes zero (Holm-adjusted p 0.054): the lead is not
  statistically established.
- The M3 prediction intervals have the pre-registered status `FAILED_CALIBRATION` (95 % coverage
  more than 3 points below nominal at horizons 8-30, and only about 8-9 effective independent
  observations).
- Every M4 operational parameter is `HYPOTHETICAL`; no real NYC staffing, cost or capacity data
  exist. M4 plans are optimal only for the hypothetical model.
- The forecasts are archived projections for 2026-07-01..30 from data ending 2026-06-30; the
  archived July 2026 forecast has never been scored.
- The snapshot's extraction time and the NYC Open Data terms of use are recorded as unverified.
- Nothing has been deployed (M6 phase B pending); the public-demo profile is refused until a
  security review.
- There is no LICENSE file (owner decision).
- Real screen-reader sessions, Firefox and Safari were never tested.

## 5. Findings (severity-ranked)

No CRITICAL finding. One HIGH, eight MEDIUM and thirty LOW, after merging the findings that two
or three lenses discovered independently (44 verified lens findings in total).

| ID | Severity | Gate | Finding |
|---|---|---|---|
| [M8-01](M8_FINDINGS_REGISTER.md#m8-01) | HIGH | L | README front page presents refuted legacy claims as current results |
| [M8-02](M8_FINDINGS_REGISTER.md#m8-02) | MEDIUM | G | `atlas-deploy recover` records an interrupted activation as verified without reloading nginx |
| [M8-03](M8_FINDINGS_REGISTER.md#m8-03) | MEDIUM | G | Rollback verification and nginx rendering use the running tool's dashboard CSP/descriptor, not the restored release's |
| [M8-04](M8_FINDINGS_REGISTER.md#m8-04) | MEDIUM | B | The HLN-DM test is oversized at h=30 with T=336-486; the selection gate's significance and the bandwidth-robustness claims do not survive size correction |
| [M8-05](M8_FINDINGS_REGISTER.md#m8-05) | MEDIUM | M | Shipped dashboard bundle omits third-party licence notices (MIT/ISC) and the Inter OFL text |
| [M8-06](M8_FINDINGS_REGISTER.md#m8-06) | MEDIUM | M | No vulnerability scanning or update monitoring for the deployed Python runtime |
| [M8-07](M8_FINDINGS_REGISTER.md#m8-07) | MEDIUM | J | Hash-verified research results and the API contract are not protected from CRLF conversion |
| [M8-08](M8_FINDINGS_REGISTER.md#m8-08) | MEDIUM | C | UNCERTAINTY.md §9.6 calls the flagged target days 'intake drops'; demand on all 18 was far above the forecast |
| [M8-09](M8_FINDINGS_REGISTER.md#m8-09) | MEDIUM | C | API, M4 report and OPTIMIZATION.md call the archived July 2026 projection a 'FUTURE PROJECTION' with no archived/not-live disclosure |
| [M8-10](M8_FINDINGS_REGISTER.md#m8-10) | LOW | L | Anomaly flags miss most of the winter 2025-26 anomaly cluster; docs overstate robustness to anomalies |
| [M8-11](M8_FINDINGS_REGISTER.md#m8-11) | LOW | L | Stale status and stale statements after the M7 merge (ROADMAP, DASHBOARD, DATA_SNAPSHOT, README notice heading) |
| [M8-12](M8_FINDINGS_REGISTER.md#m8-12) | LOW | J | The first documented M2 verify command fails with the current code |
| [M8-13](M8_FINDINGS_REGISTER.md#m8-13) | LOW | C | M3 conclusions rest on p-values not adjusted for multiplicity, one printed next to the disclosed M2 limitation |
| [M8-14](M8_FINDINGS_REGISTER.md#m8-14) | LOW | C | The coverage gate's 'conservative' p-value is the lenient choice for a calibration claim |
| [M8-15](M8_FINDINGS_REGISTER.md#m8-15) | LOW | C | Projection artefacts misdescribe the calibration window; projection CSV rows carry no status |
| [M8-16](M8_FINDINGS_REGISTER.md#m8-16) | LOW | C | `fixed365` is documented as a 365-day calibration but held 153-182 residuals in the selection period |
| [M8-17](M8_FINDINGS_REGISTER.md#m8-17) | LOW | C | Effective-sample-size wording is inconsistent and overstates the evidence in places |
| [M8-18](M8_FINDINGS_REGISTER.md#m8-18) | LOW | H | The 'failed' calibration wording is hard-coded in the API and dashboard instead of derived from the served status |
| [M8-19](M8_FINDINGS_REGISTER.md#m8-19) | LOW | H | Certificate tolerance ratios are shown as a 'gap' and plans called an 'exact optimum' |
| [M8-20](M8_FINDINGS_REGISTER.md#m8-20) | LOW | A | Unverified run_id from the M2/M3 projection manifests is served as provenance |
| [M8-21](M8_FINDINGS_REGISTER.md#m8-21) | LOW | E | HEAD on versioned GETs returns 405 while the nginx allow-list passes HEAD |
| [M8-22](M8_FINDINGS_REGISTER.md#m8-22) | LOW | E | After a worker replacement the next simulation goes to the cold worker while a warm one is idle |
| [M8-23](M8_FINDINGS_REGISTER.md#m8-23) | LOW | F | /docs and /redoc (on by default) load floating CDN bundles without SRI or CSP on the API/dashboard origin |
| [M8-24](M8_FINDINGS_REGISTER.md#m8-24) | LOW | J | release.json environment_fingerprint differs between two builds of the same commit |
| [M8-25](M8_FINDINGS_REGISTER.md#m8-25) | LOW | F | IPv6 is enabled by default although per-client limits are keyed per /128 and the guide says to keep it off |
| [M8-26](M8_FINDINGS_REGISTER.md#m8-26) | LOW | G | `atlas-deploy render --apply` installs a site for a non-current release without the lock, a history record or verification |
| [M8-27](M8_FINDINGS_REGISTER.md#m8-27) | LOW | I | Simulator error-summary links fail WCAG 2.2 target size (2.5.8) with two or more field errors |
| [M8-28](M8_FINDINGS_REGISTER.md#m8-28) | LOW | H | Legacy-window backtest misdescribed on the Forecasting page (targets past the data; ranking lacks its descriptive-only caveat) |
| [M8-29](M8_FINDINGS_REGISTER.md#m8-29) | LOW | K | Time-zone safety of dashboard dates is untested (tests only run in UTC) |
| [M8-30](M8_FINDINGS_REGISTER.md#m8-30) | LOW | K | Vitest view tests are timing-flaky under CPU load (1 s default async waits) |
| [M8-31](M8_FINDINGS_REGISTER.md#m8-31) | LOW | L | Dashboard 'Methods' links point at the mutable main branch, not the release |
| [M8-32](M8_FINDINGS_REGISTER.md#m8-32) | LOW | K | Pinned GitHub Actions target the deprecated Node.js 20 runtime (CI warning on every job) |
| [M8-33](M8_FINDINGS_REGISTER.md#m8-33) | LOW | M | The two-week dependency cooldown is described as enforced but is a manual rule; its caps never expire |
| [M8-34](M8_FINDINGS_REGISTER.md#m8-34) | LOW | L | 'Decision-intelligence' marketing language in package metadata and the dashboard heading |
| [M8-35](M8_FINDINGS_REGISTER.md#m8-35) | LOW | G | A pre-release version string (1.0.0rc1 / 1.0.0-rc.1) is refused by three validators; no versioning policy |
| [M8-36](M8_FINDINGS_REGISTER.md#m8-36) | LOW | K | No test ties the version declarations together |
| [M8-37](M8_FINDINGS_REGISTER.md#m8-37) | LOW | K | The container e2e 'explicit rollback' targets the release that is already current |
| [M8-38](M8_FINDINGS_REGISTER.md#m8-38) | LOW | J | Reproduction of the committed M2/M3 reference results is not automated, and the docs do not say so |
| [M8-39](M8_FINDINGS_REGISTER.md#m8-39) | LOW | L | spot_check_intervals.csv is a lossy (1-2 ULP) copy with no documented provenance or generator |

Only M8-04 concerns the substance of a published research conclusion, and its correction is a
statement about the strength of the evidence, not a change to any committed number: the
pre-registered decision stands.

## 6. What held up (summary)

The full list, with evidence, is in [M8_AUDIT_EVIDENCE.md](M8_AUDIT_EVIDENCE.md) (163 checks).

- **Data (M0-M1).** Every SHA-256, size, header, row count and date range in the snapshot
  manifest recomputed independently (0 mismatches); 1,277 days, no duplicates or gaps; the
  reconciliation residual (13,279 requests, 0.1077 %) and every legacy KPI recomputed from the
  CSVs; loading fails closed on every single-file tamper tried; an independent future-perturbation
  test found no leakage (364 checks, 0 failures); declared events are gated by announcement date.
- **Forecasting (M2).** An independent NumPy re-implementation of all seven models reproduces the
  committed forecasts (maximum difference 3.9e-8); the full reference run reproduces byte for
  byte; perturbing every post-origin actual by ×10 leaves every prediction bit-identical; the
  MASE scale uses only data through the cutoff; the DM/HLN, Holm and effective-sample-size
  formulas match independent implementations.
- **Uncertainty (M3).** All 288,000 interval rows rebuilt from forecasts and raw data by
  independent code (relative difference ≤ 8.6e-16), with a positive control proving the
  eligibility check has power; coverage, HAC intervals, p-values and effective sample sizes
  recomputed; `FAILED_CALIBRATION` follows the pre-registered rule, cannot be overridden by any
  code path, and reaches every API response, dashboard view and M4 output.
- **Optimisation (M4).** The LP written down from the code matches the documentation and is
  unit-consistent; an independent SciPy formulation reproduces every committed optimum, frontier
  point, sensitivity point and diagnosis to about 1e-15; four hand-derived instances match
  exactly (objective, plan, every dual, finite-difference brackets); 287 fuzzed API simulations
  and 450 random small instances produced no failure; certificates hold with maximum ratios
  ≤ 1.8e-6 of tolerance.
- **API (M5).** The live contract equals the committed one; Schemathesis ran 729 cases over all
  14 operations with no server error or schema breach; the API key is compared in constant time
  and checked before the body is read; no path, method or header variant runs a simulation
  without the key; a public-demo deployment cannot compute (the key is always loaded and nginx
  routes no compute location); 750 instrumented GET requests made 0 solver calls; tampered
  artifacts drop readiness to 503.
- **Deployment (M6).** Rendered units and nginx sites pass `systemd-analyze` and `nginx -t`; TLS,
  HSTS, Basic authentication on every location, route allow-lists generated from the contract,
  rate and body limits; secrets never in the environment, argv or logs; builds verify wheel
  hashes and the exact commit; activation verifies and rolls back automatically.
- **Dashboard (M7).** Six pages load with no console error or CSP violation at 1280 and 375 px;
  461 displayed table rows equal the API's values; dates are correct in six time zones; every
  disclosed limitation sits next to its numbers; failure states never show substitute numbers;
  the simulator fails closed; a tampered build file is refused at startup.
- **Supply chain.** Both lockfiles complete and matching their registries; 0 known
  vulnerabilities (pip-audit, npm audit); no install script runs on Linux; CI permissions are
  read-only and every action is pinned to a SHA that equals its tag; no credential in any of the
  979 blobs of the history.

## 7. Release-gate status at the baseline

| Gate | Baseline status | Reason |
|---|---|---|
| A. Data integrity | PASS | all integrity, coverage and reconciliation figures reproduce; LOW items only (M8-10, M8-20) |
| B. Forecasting validity | FAIL | the forecasts and evaluation are sound and reproduce exactly, but the documented statistical support for the selection is overstated (M8-04) |
| C. Uncertainty calibration and limitations | FAIL | the intervals are `FAILED_CALIBRATION` (unchanged, by design), and two disclosures are wrong or missing (M8-08, M8-09) |
| D. Optimisation correctness | PASS | independently reproduced; LOW wording items (M8-19) |
| E. API correctness | PASS | contract-accurate and robust; the archived-projection disclosure is missing (M8-09, counted under C) |
| F. Security | PASS | no exploitable defect found (LOW items M8-23, M8-25); this is not a claim of zero vulnerabilities |
| G. Deployment package | FAIL | frontend/backend consistency is not guaranteed after an interrupted activation or a CSP-changing rollback (M8-02, M8-03) |
| H. Frontend functionality | PASS | LOW items only |
| I. Accessibility | PASS (automated) | axe, keyboard and reflow checks pass; one LOW target-size defect (M8-27); real screen readers NOT VERIFIED |
| J. Reproducibility | FAIL | research results reproduce byte for byte on Linux, but CRLF checkouts break every hash check (M8-07) and the first documented M2 verify command fails (M8-12) |
| K. Automated testing | PASS | 2,942 + 166 + 56 tests, 96 % coverage; LOW gaps |
| L. Documentation and provenance | FAIL | the README front page presents refuted legacy claims (M8-01) |
| M. Dependency and licensing | FAIL | third-party notices missing from the shipped bundle (M8-05); no Python vulnerability monitoring (M8-06); the project licence is an open owner decision |
| N. Actual VPS deployment readiness | NOT VERIFIED | no VPS has been inspected (M6 phase B) |

## 8. Baseline verdict

**NO-GO for a v1.0 release of commit `194fa4a` as it stands.** The engineering underneath is
strong: every committed research result reproduces byte for byte, the mathematics checks out
under independent re-implementation, and no critical or high-severity security defect was found.
The blocking problems are one HIGH documentation defect (the README's front page), two MEDIUM
deployment-consistency defects, two MEDIUM licensing and supply-chain gaps, one MEDIUM
reproducibility gap and three MEDIUM scientific-disclosure defects. Each can be corrected without
changing any committed research result.
