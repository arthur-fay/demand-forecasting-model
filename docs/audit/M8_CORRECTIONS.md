# M8 corrections

What was corrected after the [baseline assessment](M8_BASELINE_ASSESSMENT.md), how each
correction was proved, and what remains. The verdict built on it is in
[../release/RELEASE_GATE.md](../release/RELEASE_GATE.md).

## 1. Measured checks on the release candidate

Measured on the candidate's code at `298d58b` (later commits change documents and add the
release-candidate manifest only). Machine: 4-vCPU Intel Xeon with AVX-512, OpenBLAS core
`SkylakeX` (the reference CPU class), Ubuntu 24.04, Python 3.12.3, uv 0.11.32, Node.js 22.22.2;
2026-10-17. The CI results on the pull request's head commit are recorded in the pull request.

**Static checks and tests**

| Check | Result |
|---|---|
| `ruff format --check`, `ruff check`, strict `mypy` (89 source files), `shellcheck` of `deploy/scripts` | clean |
| `ATLAS_REQUIRE_DEPLOY_TOOLS=1 uv run pytest` (with the coverage gate) | **3,068 passed, 25 skipped**, coverage **96.06 %** (gate 90 %), 32 min. The skips: the 22 container tests (opt-in, run separately below), 2 tests that need an unprivileged user (the run was as root), and the opt-in full rerun of the dependence study |
| Dashboard: Prettier, ESLint, `tsc`; the client types equal the contract | clean |
| Vitest with coverage | **192 of 192** passed; statements 94.92 %, branches 83.44 %, functions 94.05 %, lines 96.37 % (gates 85/75/85/85) |
| `npm run build`, then `git diff --exit-code -- dist`; `verify:dist`; size budgets | the committed build reproduced **byte for byte**; 19 files match the manifest; initial HTML, CSS and JavaScript 91.3 KB gzip (budget 125), initial JavaScript 85.0, largest lazy chunk 20.7, font 48.3, whole build 524.4 KB |
| Playwright, all default projects in Chromium (the API, a phone, nginx private and public demo) | **61 of 61** passed (3.1 min) |
| Playwright in Firefox and WebKit | not installable here (the browser download is blocked); run by CI's dashboard job |
| Deployment end to end in a disposable Ubuntu 24.04 systemd container (`tests/test_m6_container.py`) | **22 of 22** passed (5 min 56 s): bootstrap, build (45 s), activation, private profile through nginx and TLS, dashboard, crash, hang and worker recovery, automatic and explicit rollback, interrupted activation, monitor and alert, reboot, resource limits (service 285 MiB), tunnel return; `systemd-analyze security` exposure 0.9 |

The container test needed three attempts in this sandbox, all on the same commit. The first
two ran without the container on the host network, so its package installs could not reach
PyPI through the sandbox's proxy: 21 failed, 1 passed. The third ran on the host network with
the proxy's CA (`ATLAS_E2E_NETWORK=host`, `ATLAS_E2E_CA_BUNDLE`, as the test documents). There
the monitor reported the sandbox's disk as critical (4.8 % free, below its 5 % threshold),
correctly, and its test failed: 21 passed, 1 failed. After freeing scratch space the run
passed 22 of 22. No code changed between the runs.

**Research reproduction** (`verify` regenerates a run and compares every output file)

| Run | Result |
|---|---|
| `atlas check`, `atlas quality`, `atlas backtest legacy` | pass |
| M2, M3 and M4 smoke runs, then `verify` | byte-identical |
| M4 reference run (`atlas optimize verify`) | **byte-identical**, 8 of 8 files, result fingerprint `0dc4eb0f…` |
| M2 full reference run (`atlas backtest verify .../full`) | **byte-identical**, result fingerprint `d53bc911…` (336 s) |
| M3 full reference run (`atlas uncertainty verify .../full`) | **byte-identical**, result fingerprint `c4d2adbf…` (225 s) |
| API contract (`atlas api openapi`) | equals `docs/api/openapi.json` |

**API, dependencies and secrets**

| Check | Result |
|---|---|
| Schemathesis 4.31.1, every check, phases examples, coverage and fuzzing, seed 42, against the candidate's API | 729 cases over 14 of 14 operations: no server error, no schema breach, no undocumented status. Its two reports of a schema-compliant request refused (422) are the documented cross-field rules: a history window outside the snapshot, a quantile with a point-forecast basis (the same as at the baseline) |
| pip-audit 2.10.1 (`--strict --require-hashes`) on the runtime and the development exports of `uv.lock` | no known vulnerability |
| `npm audit --audit-level=high`; `npm audit --omit=dev --audit-level=moderate` | 0 vulnerabilities |
| Secret scan (key formats, tokens, private keys, credentials in URLs and connection strings, high-entropy assignments; zip and PDF contents included) over every blob reachable from every ref at `298d58b` | **1,337 blobs, no credential**. Matches not seen at the baseline: a test's fake git identity (`atlas-tests@example.invalid`) and two quotations in [M8_AUDIT_EVIDENCE.md](M8_AUDIT_EVIDENCE.md) (a description of key generation, the legacy Power BI path) |

**Earlier evidence kept for reference**

- The repeat of the container test on the baseline's code, which
  [M8_BASELINE_ASSESSMENT.md](M8_BASELINE_ASSESSMENT.md) section 3.1 defers to this section:
  once the machine was quiet, 22 of 22 passed in 5 min 20 s (2026-10-16). The first attempt had
  been refused by the preflight because of the machine's load.
- CI run 38972241374 on `e79e66d`: all jobs green. That covers the quality job, `deploy-e2e`
  with the explicit rollback of M8-37, the dashboard job with Firefox and WebKit (38 passed),
  and both dependency-audit jobs.
- The final independent review's checks (section 7).

## 2. How the corrections were made

- **Work packages.** The 39 findings were split into five packages with disjoint file
  ownership: deployment (`m8/deploy`), API (`m8/api`), CI, supply chain and versioning
  (`m8/ci`), dashboard (`m8/web`) and documentation and statistics (`m8/docs`). Each was
  implemented in its own git worktree from `8f077ed`.
- **One test per defect.** Every substantive correction came with a test shown to fail on the
  unfixed code and pass with the fix; the before-and-after evidence is recorded with each
  finding below. Documentation-only corrections were checked against the committed results by
  scripts kept in [scripts/](scripts/) (`m8_doc_figures.py`, `dm_size_study.py`, with their
  recorded output) and by tests where the wording carries a claim (`tests/test_m8_docs.py`).
- **Two independent reviews per package.** A verification reviewer re-ran each new test
  against the unfixed code to prove that it catches its defect, and ran every affected suite; a
  skeptic read the whole diff for wrong or overstated wording, scope creep, regressions and any
  change to research results. All five packages went through a fix-up round for the issues they
  raised (none was a blocker left open). The reviews are summarised with each finding below.
- **Rules held throughout.** No committed research result, snapshot file, configuration or
  legacy artifact changed (`git diff 194fa4a HEAD -- docs/results snapshots configs '01 Data'
  '02 Notebooks' '03 SQL' '04 Power BI' '05 Screenshots' '06 Exports'` is empty). Where a
  finding concerned a committed result or its generator, the correction is an erratum in the
  documentation, so every committed run still reproduces byte for byte. No dependency was
  added or upgraded: `uv.lock` and `web/package-lock.json` differ from `194fa4a` only in the
  project's own version.

## 3. Integration by the lead

After merging the five packages (merge commits on the release branch):

- the merged deployment tests counted three dashboard locations; with the dashboard's licence
  notices the build has five, so they now derive the count from the build manifest;
- the cross-package handoffs: `web/public` protected from line-ending conversion; the
  deployment and dashboard documents updated for the corrected rollback, recovery and
  `render --apply` behaviour and for the dependency audit; the roadmap's action pins;
- **version 1.0.0** for the candidate: `pyproject.toml`, `atlas.__version__`, `uv.lock` (the
  project's entry only), `web/package.json` and its lock, the regenerated API contract and
  client types, the re-recorded dashboard fixtures and the rebuilt `web/dist` (reproduced byte
  for byte by a second build);
- **Firefox and WebKit** in CI: the API-backed pages and every failure state now also run in
  Playwright's Firefox and WebKit (Safari's engine), behind `ATLAS_E2E_ALL_BROWSERS=1` so that
  a Chromium-only development machine keeps working;
- the release-candidate documents: the model and data cards, the known limitations, the release
  gate, the release-candidate manifest and its tool, and the phase B runbook.

## 4. What was not changed, and why

- **Committed reports keep their original wording**, with errata in the documentation: the M2
  and M4 reports' "FUTURE PROJECTION" label (M8-09), the M3 projection report's description of
  its calibration window (M8-15), and the lossy `spot_check_intervals.csv` (M8-39). Editing
  them would break their recorded hashes and the byte-for-byte reproduction.
- **Generator strings that would alter committed bytes** are unchanged for v1 and listed for a
  future protocol version: "tens of independent observations" in `probabilistic.py` (M8-17),
  the projection label and calibration-window wording in `uncertainty_runner.py` (M8-15), a
  correctly sized reference distribution in `atlas.stats` and a minimum-p coverage gate
  (M8-04, M8-14).
- **The M2 decision stands.** The selection passed an oversized test (M8-04); replacing the
  test after seeing the outcome would be worse science than reporting the weakness, which the
  erratum does with a reproducible size study.
- **The fail-closed wording guard (M8-18)** means a future M3 re-run with another status takes
  the forecast, interval, M4 and simulation components down until the API's fixed wording is
  reviewed. That is intended.
- **The dependency caps** (`optype<0.19`, `truststore<0.10.5`, `starlette<1.8`) stay as they
  are; their review dates (2026-10-16, -26 and -27) are recorded in `pyproject.toml` and
  [../DEVELOPMENT.md](../DEVELOPMENT.md). Lifting one changes the lock and needs the full checks.
- **Owner decisions** are not taken here: the project licence, the NYC Open Data terms, the
  first dispatch of the research-reproduction workflow (possible only once it is on the
  default branch) and every phase B decision.

## 5. Every finding and its outcome

| ID | Severity | Outcome | Work package(s) | Regression test(s) or check |
|---|---|---|---|---|
| [M8-01](#m8-01) | HIGH | fixed | m8/docs | `tests/test_m8_docs.py::test_the_readme_opens_with_atlas_and_archives_the_original_project`; `tests/test_m8_docs.py::test_every_refuted_claim_is_quoted_as_original_and_followed_by_a_correction`; `tests/test_m8_docs.py::test_every_archived_kpi_says_what_it_actually_measures`; `tests/test_m8_docs.py::test_every_legacy_reproduction_claim_names_the_declared_exception` |
| [M8-02](#m8-02) | MEDIUM | fixed | m8/deploy | `tests/test_m7_deploy.py::test_recover_completes_an_interrupted_activation[reset-failed|restart]`; `tests/test_m6_releases.py::test_recover_has_the_service_and_nginx_load_the_configuration_it_keeps[reset-failed|restart]`; tests/test_m6_releases.py: InterruptedHost gains `at` |
| [M8-03](#m8-03) | MEDIUM | fixed | m8/deploy | `tests/test_m7_deploy.py::test_a_newer_tool_restores_an_older_release_with_its_own_policy`; `tests/test_m7_deploy.py::test_an_explicit_rollback_by_a_newer_tool_serves_the_older_policy`; `tests/test_m7_deploy.py::test_a_release_that_records_only_its_csp_keeps_it`; `tests/test_m7_deploy.py::test_a_recorded_policy_that_could_alter_the_site_is_refused` |
| [M8-04](#m8-04) | MEDIUM | fixed | m8/api, m8/docs | `tests/test_m8_dm_size.py::test_the_size_study_reads_the_committed_statistics`; `tests/test_m8_dm_size.py::test_the_recorded_output_comes_from_the_default_run`; `tests/test_m8_dm_size.py`; `tests/test_m5_endpoints.py::test_the_selection_test_is_served_with_its_size_caveat` |
| [M8-05](#m8-05) | MEDIUM | fixed | m8/web | `tests/test_m7_dashboard.py::test_the_build_ships_and_the_api_serves_the_licence_notices`; `web/build/notices.test.ts`; web/src/views/m8-audit.test.tsx 'M8-05: ... are linked from the footer of every page'; web/e2e/dashboard.spec.ts 'the footer opens the licence notices of the bundled code and of the font' (+1) |
| [M8-06](#m8-06) | MEDIUM | fixed | m8/ci | documentation check |
| [M8-07](#m8-07) | MEDIUM | fixed | m8/ci | `tests/test_snapshot.py::test_results_configs_and_contract_are_protected_from_line_ending_conversion`; `tests/test_snapshot.py::test_a_crlf_checkout_writes_every_byte_verified_file_unchanged` |
| [M8-08](#m8-08) | MEDIUM | fixed | m8/docs | `tests/test_m8_docs.py::test_the_flagged_day_errors_are_described_in_their_actual_direction` |
| [M8-09](#m8-09) | MEDIUM | fixed (API notices and documentation; the committed M2/M4 reports keep their original label, with an erratum) | m8/api, m8/docs | `tests/test_m5_contract.py` |
| [M8-10](#m8-10) | LOW | fixed | m8/docs | documentation check |
| [M8-11](#m8-11) | LOW | fixed (across the dashboard, documentation and lead handoffs) | m8/docs, m8/web | web/src/views/m8-audit.test.tsx M8-31 test |
| [M8-12](#m8-12) | LOW | fixed | m8/docs | documentation check |
| [M8-13](#m8-13) | LOW | fixed | m8/docs | documentation check |
| [M8-14](#m8-14) | LOW | fixed | m8/docs | documentation check |
| [M8-15](#m8-15) | LOW | fixed | m8/docs | documentation check |
| [M8-16](#m8-16) | LOW | fixed | m8/docs | documentation check |
| [M8-17](#m8-17) | LOW | fixed | m8/docs | documentation check |
| [M8-18](#m8-18) | LOW | fixed (API: a startup guard on its fixed wording; dashboard: text derived from the served status) | m8/api, m8/web | web/src/views/m8-audit.test.tsx 'never says the intervals failed when the status is insufficient_evidence | unavailable | decision_grade'; web/src/views/m8-audit.test.tsx 'says they failed when the status is FAILED_CALIBRATION'; web/src/views/m8-audit.test.tsx 'says the result could not be loaded when the status does not load'; web/src/views/m8-audit.test.tsx 'names the intervals elsewhere by the status ...' and 'names the intervals of the M4 stress paths by their served status' (+1) |
| [M8-19](#m8-19) | LOW | fixed (API field descriptions; dashboard wording) | m8/api, m8/web | web/src/views/m8-audit.test.tsx 'shows its largest violation as a fraction of the tolerance, not as a duality gap'; web/src/views/m8-audit.test.tsx 'shows the largest of its four checks, whichever it is'; web/src/views/m8-audit.test.tsx 'calls each plan an optimum within the solver tolerance, not the exact optimum'; web/src/views/m8-audit.test.tsx 'states the tolerances of each check as the optimiser applies them' (+1) |
| [M8-20](#m8-20) | LOW | fixed | m8/api | tests/test_m5_reliability.py::test_a_forged_projection_run_id_is_never_served[m2_projection_manifest_run_id_edited-m2_projection_run] and [m3_projection_manifest_run_id_edited-m3_projection_run]. These replace test_a_forged_projection_run_id_makes_readiness_fail. Readiness is 503 and names the manifest; /scenarios and /simulations/parameters answer 503; /forecasts, /uncertainty/intervals and /system answer 200 without 'FORGED-RUN-ID', and the projection source's run_id is null.; `tests/test_m5_guards.py::test_a_projection_manifest_swapped_between_reads_is_not_served` |
| [M8-21](#m8-21) | LOW | fixed | m8/deploy | `tests/test_m6_render.py::test_head_is_routed_only_where_the_api_answers_it`; `tests/test_m6_proxy.py::test_the_api_behind_the_proxy` |
| [M8-22](#m8-22) | LOW | fixed | m8/api | `tests/test_m5_concurrency.py::test_after_sequential_use_a_replaced_worker_is_taken_last`; `tests/test_m5_concurrency.py::test_warm_up_pings_every_worker`; `tests/test_m5_concurrency.py::test_a_replaced_worker_waits_while_a_warm_one_is_idle` |
| [M8-23](#m8-23) | LOW | fixed | m8/api | tests/test_m5_security.py::test_docs_pages_load_pinned_files_under_their_own_csp[/docs] and [/redoc] |
| [M8-24](#m8-24) | LOW | fixed | m8/deploy | `tests/test_m6_manifest.py::test_two_builds_of_one_commit_have_one_environment_fingerprint`; `tests/test_m6_manifest.py::test_a_manifest_written_before_m8_is_checked_as_it_was_written` |
| [M8-25](#m8-25) | LOW | fixed | m8/deploy | `tests/test_m6_config.py::test_the_default_is_the_private_tunnel_profile`; `tests/test_m6_config.py::test_the_example_file_is_valid`; `tests/test_m6_render.py::test_nginx_listens_on_ipv6_only_when_asked` |
| [M8-26](#m8-26) | LOW | fixed | m8/deploy | `tests/test_m7_deploy.py::test_render_apply_installs_the_acme_site_before_the_first_activation`; `tests/test_m7_deploy.py::test_render_apply_installs_the_current_release_only`; tests/test_m7_deploy.py::render_apply helper takes a stage |
| [M8-27](#m8-27) | LOW | fixed | m8/web | web/e2e/a11y.spec.ts 'the error summary of several invalid fields has no WCAG 2.2 AA violation' |
| [M8-28](#m8-28) | LOW | fixed | m8/web | web/src/views/m8-audit.test.tsx 'M8-28: ... shows the target days that were scored, never past the end of the snapshot'; web/src/views/m8-audit.test.tsx 'M8-28: ... ranks the models in the legacy window with its descriptive-only caveat' |
| [M8-29](#m8-29) | LOW | fixed | m8/web | `web/src/lib/format.test.ts`; web/e2e/dashboard.spec.ts 'in the Pacific/Kiritimati time zone ...' and 'in the America/Los_Angeles time zone ...' |
| [M8-30](#m8-30) | LOW | fixed | m8/web | web/src/views/m8-audit.test.tsx 'M8-30: ... allow a page 5 s to render ...' |
| [M8-31](#m8-31) | LOW | fixed | m8/web | web/src/views/m8-audit.test.tsx 'M8-31: the Methods links say they open the current documentation on main, and where the release has its own' |
| [M8-32](#m8-32) | LOW | fixed | m8/ci | `tests/test_m8_release.py::test_every_action_is_pinned_to_a_commit_with_its_version`; `tests/test_m8_release.py::test_no_action_runs_on_the_deprecated_node20_runtime` |
| [M8-33](#m8-33) | LOW | fixed | m8/ci, m8/docs | documentation check |
| [M8-34](#m8-34) | LOW | fixed (package description and dashboard heading) | m8/ci, m8/docs, m8/web | web/src/views/m8-audit.test.tsx 'M8-34: the Overview heading describes the research without marketing language' |
| [M8-35](#m8-35) | LOW | fixed | m8/ci | `tests/test_m8_release.py::test_every_version_declaration_is_major_minor_patch`; `tests/test_m8_release.py::test_a_release_candidate_label_cannot_be_the_version` |
| [M8-36](#m8-36) | LOW | fixed | m8/ci | `tests/test_m8_release.py::test_every_version_declaration_is_the_same`; `tests/test_m8_release.py::test_every_version_declaration_is_major_minor_patch` |
| [M8-37](#m8-37) | LOW | fixed (validated by the container test run recorded in section 1) | m8/deploy | `tests/test_m6_container.py::test_an_explicit_rollback`; `tests/test_m6_container.py::test_the_dashboard_follows_the_release` |
| [M8-38](#m8-38) | LOW | fixed (manual workflow added; it can be dispatched only once the workflow file is on the default branch, so its first run is an owner action) | m8/ci | `tests/test_m8_release.py::test_same_outputs_refuses_a_regenerated_file_that_its_manifest_does_not_describe`; `tests/test_m8_release.py::test_same_outputs_accepts_an_identical_regeneration`; `tests/test_m8_release.py::test_same_outputs_refuses_a_different_output_hash`; `tests/test_m8_release.py::test_same_outputs_refuses_an_altered_committed_file` |
| [M8-39](#m8-39) | LOW | fixed | m8/docs | documentation check |


## 6. Details

Generated from the work packages' structured reports; paths under `<scratch>` were the
implementers' scratch files and are not part of the repository.

### M8-01

**README front page presents refuted legacy claims as current results** (HIGH, gate L)

*Work package `m8/docs`: fixed.* Reviews: verify needs change, skeptic needs change; a fix-up round addressed the requested changes.

- **Defect.** README.md's front page was the July 2026 legacy text: legacy title and intro, Power BI screenshots, 'Executive KPIs' and 'Key Business Insights' presented as current results, including refuted claims (40.10 % 'accuracy' gain, 13 days of 'advance warning', 'the next 30 days ... 330,941', five dates 'exceed capacity', the regression 'selected because ... lowest holdout error', 'Recommended actions'). The M0 notice qualified only two figures.
- **Change.** The README now opens with ATLAS v1.0 (release candidate under review, canonical wording C): what it is and is not; what it measured, with the qualifier beside every number. Those numbers are: M2 confirmation MAE 984.9 against 1,112.2 for B* (11.4 % lower, 95 % interval of the difference -256 to +2 includes zero, Holm p 0.054, not significant) and 1,371.5 for seasonal naive (28.2 % lower, Holm p 0.007); the M8 erratum in short canonical wording B (7.6-9.1 % false rejections, size-corrected p 0.069); M3 FAILED_CALIBRATION; M4 certified optimal only for a HYPOTHETICAL model; byte-for-byte reproduction, re-checked in M8. It then covers how to run and verify; a documentation map linking the lead's MODEL_CARD, DATA_CARD, KNOWN_LIMITATIONS, release/RELEASE_GATE and audit/README plus every existing doc; the data attribution (NYC Open Data erm2-nwe9, City of New York; not affiliated or endorsed; terms of use not verified, an owner action); and the licence status (none yet, owner decision). No test counts are quoted. The original project is kept as 'The original project (July 2026, archived)' with its screenshots, KPI table and files, each claim qualified in place: a 'What it actually measures' column from KPI_MEANINGS and LEGACY_LIMITATIONS, insights rewritten as 'Original -> ATLAS', the actions described as the original report's illustrative rules on a demand percentile, and 'workload' translated as intake. The author section is kept. LEGACY_LIMITATIONS.md now points to the archived section. Fix-up: Both archived bullets now say 'over the (whole) confirmation year (targets 2025-07-01 to 2026-06-30)'. The h13-28 figure is aligned with full/metrics.json: legacy recursive MAE 1,067-1,104 (verified 1,066.5-1,103.7; h7-30 MAPE 9.05-9.78 %). H2 now reads 'Holm-adjusted p 0.007, nominal; 0.045 after the M8 size correction below, and above 0.05 under three of the five HAC bandwidths once each is size-corrected'. Item 2 carries canonical wording B including the bandwidth-robustness clause. The M0 row and item 6 name the declared RMSE exception (676.70 published, 676.69 recomputed, LEGACY-V0-EX-002). Item 4 says 'certified optimal within small numerical tolerances (primal feasibility to 1e-7 plus 1e-9 of the magnitude; dual feasibility and the duality gap to 1e-9 relative ...)', verified against CERT_ABS/CERT_REL in optimizer.certify. The M3 row says the intervals failed the pre-registered decision-grade test (FAILED_CALIBRATION). The confirmation year in item 1 is no longer called 'the following year'. The original 'Skills Demonstrated' list is restored as a qualified 'Original skills list' (e.g. 'capacity planning' qualified as a threshold at a demand percentile). New tests/test_m8_docs.py pins the correction.
- **Tests.** tests/test_m8_docs.py::test_the_readme_opens_with_atlas_and_archives_the_original_project; tests/test_m8_docs.py::test_every_refuted_claim_is_quoted_as_original_and_followed_by_a_correction; tests/test_m8_docs.py::test_every_archived_kpi_says_what_it_actually_measures; tests/test_m8_docs.py::test_every_legacy_reproduction_claim_names_the_declared_exception
- **Before and after.** Run against the 8f077ed README.md (scratch runner that points the test module at that file), all four README tests fail: 'the README must open with ATLAS', 'the archived section is missing', 'the archived KPI table is missing', 'the README does not name the declared RMSE exception'. Against the pre-fix-up branch README (f1a1419), only the RMSE-exception test fails. On d12f6b2 all pass. Relative links: 116 checked, 0 problems; only the four lead-owned documents are skipped.
- **Residual.** The README links docs/MODEL_CARD.md, DATA_CARD.md, KNOWN_LIMITATIONS.md and release/RELEASE_GATE.md, which the lead must add before merge. The README says the code carries 1.0.0, which becomes true when the lead bumps the version. The tests pin structure and markers; they cannot pin the meaning of every sentence.


### M8-02

**`atlas-deploy recover` records an interrupted activation as verified without reloading nginx** (MEDIUM, gate G)

*Work package `m8/deploy`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** An activation interrupted after the switch, while atlas.service restarts (lost SSH session or Ctrl-C), never reloads nginx. `recover` then only ran the verifier, which reaches the API on loopback and never sees what nginx has loaded. It recorded ACTIVATED and kept the release and its deploy.env as the last verified state while nginx still served the previous release's dashboard, routes and access rules.
- **Change.** New `Deployment._complete()`, used by `recover()` when the pending release is current and passes its checks. It runs `install()` again (idempotent: publish, render, nginx -t, daemon-reload), then the new `_expose()` (split out of `_serve`: nginx reload in exposed profiles, then verify), and only then keeps the release as verified. Any failure or unexpected exception goes to `_fail()`, which restores the last verified state. The running service is not restarted. I did not add KeyboardInterrupt handling to `_start`. A SIGHUP from a lost SSH session is not catchable that way, so `recover` has to be correct in any case. Running a long restoration after the operator pressed Ctrl-C would also surprise them. Docstrings in releases.py were updated. Fix-up: `Deployment._complete` (releases.py) now repeats every activation step after the switch: install again (idempotent: publish, render, nginx -t, daemon-reload), then `_serve` (reset-failed, restart, nginx reload, verify). Only after that is the release kept; any failure goes to `_fail`. The `_expose` split from 8994c45 is folded back into `_serve`, with a comment that nginx is reloaded before verifying. Docstrings and DEPLOYMENT.md sections 11 and 15 are updated. KeyboardInterrupt handling in `_start` was not added: a SIGHUP from a lost session cannot be caught that way, so recover has to be correct on its own.
- **Tests.** tests/test_m7_deploy.py::test_recover_completes_an_interrupted_activation[reset-failed|restart] (replaces ..._interrupted_during_the_restart). Asserts the order nginx -t < restart B < nginx reload, that every site nginx loaded has the dashboard root of the release then running, that the loaded site equals the site on disk, that the service runs B, and good_state B.; tests/test_m6_releases.py::test_recover_has_the_service_and_nginx_load_the_configuration_it_keeps[reset-failed|restart] (replaces ..._nginx_load_the_configuration_it_keeps_as_verified). Change: ALLOW_CIDRS plus SIMULATION_TIMEOUT_SECONDS=20. Asserts nginx loaded the allow-list and that the service's loaded atlas.env equals the installed one.; tests/test_m6_releases.py: InterruptedHost gains `at` ('reset-failed' or 'restart') and records the running release, its environment and the release behind each nginx reload; new `runs(host)` verifier adds the real verifier's 'process release' check
- **Before and after.** 8f077ed sources: m7 [reset-failed] fails with `'rolled_back' == 'activated'`; m7 [restart] fails with `ValueError: 'nginx -t' is not in list`; both m6 cases fail because 'allow 203.0.113.0/24;' is not in the loaded site. 8994c45 releases.py: m7 [reset-failed] fails with 'rolled_back' == 'activated'; m6 [reset-failed] fails because the service environment has SIMULATION_TIMEOUT_SECONDS=10, not 20; m7 [restart] fails with `'restart 0.7.0-bbbbbbbbbbbb' is not in list`. All four pass at a8a77a6.
- **Residual.** recover now restarts a service that may already run the release, which costs one restart (about 3-6 s of downtime, per DEPLOYMENT.md section 10). Not run on real systemd here. In the container test's interrupted-activation case, recover now installs and restarts the broken release, verification fails and it is rolled back; the expected 'rolled_back' output is unchanged. The lead must run that test.


### M8-03

**Rollback verification and nginx rendering use the running tool's dashboard CSP/descriptor, not the restored release's** (MEDIUM, gate G)

*Work package `m8/deploy`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** `verify._dashboard` compared the restored release's API with the running tool's DASHBOARD_CSP and DESCRIPTOR_FORMAT. The nginx site of a restored or rolled-back release was rendered with the tool's DASHBOARD_HEADERS and descriptor. A later tool whose dashboard policy changed therefore could not restore an older release (rollback_failed: service stopped and disabled) and served it with headers it was not built for.
- **Change.** manifest.dashboard_record now also records `headers` (the full DASHBOARD_HEADERS), `descriptor_format` and `descriptors` (the descriptor of each exposed profile). `descriptor_for` moved to atlas.deploy.routes to avoid an import cycle; render re-imports it. render.py gains `DashboardPolicy` and `recorded_dashboard()`. `Renderer.for_release` reads the release's release.json record in exposed profiles and renders the dashboard headers and descriptor from it. The record is validated against strict character sets because it is written into nginx; an unsafe or malformed record raises RenderError. `dashboard_headers()` takes the header list. verify._dashboard uses `record.get('csp', DASHBOARD_CSP)` and `record.get('descriptor_format', DESCRIPTOR_FORMAT)`. Fallback for 0.7.0 records (csp only): the tool's other headers and descriptors, which have not changed since M7. Output for the current tool is byte-identical, because header order and values are the same. Fix-up: As in 8994c45: release.json records `headers`, `descriptor_format` and per-profile `descriptors`. `DashboardPolicy`/`recorded_dashboard` in render.py render the release's own headers and descriptor, validated against strict character sets. verify uses the recorded csp and descriptor_format, falling back to the tool's values. Fix-up: `newer_tool()` also changes the Permissions-Policy, and `assert_site_serves` checks that each release's site carries its own Permissions-Policy on all 3 dashboard locations. The csp-only test checks that a 0.7.0-style record takes the tool's other headers. The render.py docstring now reads 'narrow enough that no value can leave its quoted string or add a directive'. DEPLOYMENT.md section 10 says verification checks only the CSP and the descriptor format.
- **Tests.** tests/test_m7_deploy.py::test_a_newer_tool_restores_an_older_release_with_its_own_policy (now also checks Permissions-Policy); tests/test_m7_deploy.py::test_an_explicit_rollback_by_a_newer_tool_serves_the_older_policy (now also checks Permissions-Policy); tests/test_m7_deploy.py::test_a_release_that_records_only_its_csp_keeps_it (now also checks the tool's other headers); tests/test_m7_deploy.py::test_a_recorded_policy_that_could_alter_the_site_is_refused (6 cases, unchanged)
- **Before and after.** 8f077ed sources: the restore test fails with `'rollback_failed' == 'rolled_back'`, the explicit rollback with `'rollback_failed' == 'activated'`, the csp-only test with `assert 0 == 3`. Reviewer's mutant (render the tool's headers with only the CSP replaced): the 8994c45 tests pass with it (10 passed); the a8a77a6 tests catch it (2 failed: the two newer-tool tests). All pass at a8a77a6.
- **Residual.** Releases recorded by 0.7.0 take their non-CSP headers and descriptors from the tool (documented). Verification compares only the CSP and the descriptor format with the record.


### M8-04

**The HLN-DM test is oversized at h=30 with T=336-486; the selection gate's significance and the bandwidth-robustness claims do not survive size correction** (MEDIUM, gate B)

*Work package `m8/docs`: fixed.* Reviews: verify accept, skeptic needs change; a fix-up round addressed the requested changes.

- **Defect.** atlas.stats.diebold_mariano (HLN, rectangular HAC to h-1, t(T-1)) and bandwidth_sensitivity over-reject at h=30 with T=336-486 even under the test's own MA(29) null. BACKTESTING.md still called the larger p-value 'conservative' and claimed that the selection does not depend on the HAC bandwidth, that it 'becomes more significant' under wider bandwidths and that H2 'stays significant under every HAC bandwidth'.
- **Change.** New docs/audit/scripts/dm_size_study.py simulates the MA(29) null through the unchanged atlas.stats functions: 20,000 replications per setting, seed 20261016, Gaussian and Student t(3) shocks, about 100 s. It reads the committed selection, H1 and H2 statistics from development/ and full/metrics.json and cross-checks them, and also treats M3's tests on the same grid. Its recorded output, docs/audit/scripts/dm_size_study_output.md, reruns byte-identically. Size of the pre-registered rule at a nominal 5 %: 7.6 % (T=486) and 9.1 % (T=336), MC SE 0.2 points; the Bartlett leg prevents only 2 of 1,526 and 6 of 1,831 rejections; the wider bandwidths reject 11.0-17.6 %; the control at T=3,000 rejects 5.5 %. Size-corrected critical |t| of the rule: 2.21 and 2.39, against nominal 1.965 and 1.967. Size-corrected p: selection 0.069 (t3: 0.067), 0.035-0.069 across bandwidths; H1 0.095 (0.096-0.233 across bandwidths); H2 0.022 (Holm 0.045), but 0.113, 0.066 and 0.094 under rectangular 59, Bartlett 90 and Bartlett 120. BACKTESTING.md gains section 12 'M8 erratum' with these figures, canonical wording B with the exact numbers, why the sealed decision stands (with a correctly sized gate B* would most likely have been selected) and the limits of the correction. Marked M8 notes amend finding 3, section 5 (the meaning of 'conservative'), 6.1, 6.2 (a per-bandwidth size-corrected table), the exposure-log sentence, the amendment log and section 10 (the size-corrected minimum detectable difference, about 157). UNCERTAINTY.md 9.7 uses the same t reference, as checked in probabilistic.coverage_bandwidth_sensitivity: a caveat was added (its conclusion holds; size-corrected 0.990 and 0.483). ARCHITECTURE.md and LEGACY_LIMITATIONS.md carry short pointers. atlas.stats and every committed result are unchanged. Fix-up: dm_size_study.py now returns a per-column computed mask. The report states the convention in its header (no p-value counts as a non-rejection). Section 1 gives the share of draws without a p-value and the size among computed draws. Section 3 gives the size-corrected p-values among computed draws. The recorded output was regenerated: every earlier figure is unchanged, and only the new lines were added. Rectangular 59 has no p-value in 1.78 % (T=486) and 5.35 % (T=336) of Gaussian draws. Among computed draws its sizes are 13.8 % and 17.1 %, and its p-values are selection 0.042, H1 0.225, H2 0.119. No other rule or variant lacks a p-value. BACKTESTING section 12's method paragraph states the convention and these figures. Section 6.2 gains a sentence explaining that its rectangular-29 column is HLN alone, against the rule's 0.069/0.095/0.022. Section 12 now says 'sealed before any confirmation-period run of the protocol (section 7; the earlier confirmation-period computations are listed in section 9)'. It also says the recommendation passed the test-free confirmation rule but reached it only through the selection gate, so it inherits the gate's weak support. The test comment's standard-error count was corrected (4.6, derived in the comment). The study test now checks the convention and the mask.
- **Tests.** tests/test_m8_dm_size.py::test_the_size_study_reads_the_committed_statistics (now also checks the computed mask and the p = 1 convention); tests/test_m8_dm_size.py::test_the_recorded_output_comes_from_the_default_run (now also checks the disclosed convention); tests/test_m8_dm_size.py (the other four tests unchanged)
- **Before and after.** Earlier fail-before/pass-after evidence is unchanged and was re-checked by both reviewers: test_backtesting_quotes_the_recorded_size_study and test_every_unsupported_robustness_statement_carries_an_m8_note fail on the 8f077ed BACKTESTING.md and pass now. diff of the regenerated dm_size_study_output.md against the f1a1419 copy: only the header sentence, four section-1 bullets and one section-3 bullet were added; every pre-existing line is identical. The simulations took 98 s and 105 s.
- **Residual.** The correction assumes the test's own MA(29) null; the real H1 differential is autocorrelated beyond lag 29. atlas.stats keeps the t(T-1) reference (code change not allowed). A size-correct reference is recommended for a future protocol.

*Work package `m8/api`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** /api/v1/models serves selection.significant = true and M2's recorded reason 'the selection-period DM test is significant at 0.05' with no caveat. That presents the development selection as significant although the HLN-DM test is oversized at h = 30 with 336-486 origins.
- **Change.** Searched every string the API sends with a scratch script (all 13 GET paths, the 9 scenario details, a point and a quantile simulation, and the OpenAPI document: 11,671 strings in 25 bodies, regex signific|robust|bandwidth|conservative|HLN|Diebold|DM). Hits after the change: /models data.selection.reason (the statement above), /models data.selection.rule (the pre-registered rule text, not a claim), /uncertainty/status exceedance_reasons ('Brier improvement ... not significant (conservative p 0.144/0.132)', M3 and negative), and the new notice. No string states that the selection is robust to the bandwidth. Next to the significance statement, /models now carries the short form of canonical wording B: views.SELECTION_EVIDENCE_NOTICE, which starts 'M2 SELECTION EVIDENCE: ...', added through _m2_integrity(selection=True). SelectionInfo.significant now has a description that points to that notice. Documented in docs/API.md §5 /models and §11.
- **Tests.** tests/test_m5_endpoints.py::test_the_selection_test_is_served_with_its_size_caveat (unchanged, passes)
- **Before and after.** On 8f077ed the test fails with "AssertionError: assert [] == ['M2 SELECTION EVIDENCE: ...']". It passes on m8/api.
- **Residual.** As in the first round: the rounded figures are literals. If the documentation figures change, views.SELECTION_EVIDENCE_NOTICE, its test and API.md must follow.


### M8-05

**Shipped dashboard bundle omits third-party licence notices (MIT/ISC) and the Inter OFL text** (MEDIUM, gate M)

*Work package `m8/web`: fixed.* Reviews: verify needs change, skeptic needs change; a fix-up round addressed the requested changes.

- **Defect.** web/dist (committed and served) shipped minified react, react-dom, scheduler, zod (MIT), nine d3 packages and internmap (ISC) with every notice stripped, and the Inter variable font without its OFL-1.1 text. No notice or third-party licence file existed anywhere.
- **Change.** vite.config.ts sets Vite 8.3.1's built-in build.license with fileName 'third-party-licences.txt'. The documented default is .vite/license.md. A fileName ending in .json gives JSON; any other name gives the markdown list, checked in the plugin source at node_modules/vite/dist/node/chunks/node.js. A lowercase top-level name is needed because atlas.dashboard's file-name pattern refuses '.vite/' and upper case. The build writes 12,121 bytes covering the 14 bundled packages; no other asset changed. The Inter package's own LICENSE (Inter copyright plus the OFL-1.1 text) is copied unchanged to web/public/inter-licence.txt. Both names live in a new module, web/src/notices.ts. The manifest plugin lists both files, so the API and nginx serve them by exact name. src/atlas/dashboard.py now allows '.txt' as 'text/plain; charset=utf-8'. It is not added to COMPRESSIBLE, because nginx's gzip_types leaves text/plain uncompressed too. The CSP is unchanged. The footer of every page now has the line 'Software in this dashboard: third-party licences (React, zod and d3: MIT and ISC) and the Inter font licence (SIL Open Font License 1.1)', with links to both files. DASHBOARD.md section 2 has the licence inventory (package, version, licence, copyright, how it ships). It does not choose the project's own licence.
- **Tests.** tests/test_m7_dashboard.py::test_the_build_ships_and_the_api_serves_the_licence_notices; web/build/notices.test.ts (3 tests); web/src/views/m8-audit.test.tsx 'M8-05: ... are linked from the footer of every page'; web/e2e/dashboard.spec.ts 'the footer opens the licence notices of the bundled code and of the font'; tests/test_m7_deploy.py::test_the_private_site_serves_the_dashboard_behind_authentication (changed: counts derived from the build; one exact location per notice file)
- **Before and after.** On 8f077ed the pytest test fails with 'AssertionError: third-party-licences.txt is not in atlas-dashboard.json'. notices.test.ts fails with 'ENOENT: dist/third-party-licences.txt'. The footer test fails with 'Unable to find an accessible element with the role "link" and name /third-party licences/i'. All pass on the branch. Size budgets on the new build (none raised): initial JS 85.0/110 KB, initial HTML+CSS+JS 91.3/125 KB, largest lazy chunk 20.7/30 KB, font 48.3/50 KB, whole build 524.1/1000 KB raw.
- **Residual.** After m8/web and m8/deploy are merged, three new tests from m8/deploy fail with 'assert 5 == 3': they hard-code 3 dashboard-CSP locations. A verified follow-up patch is in the handoffs. web/public/*.txt and favicon.svg are not marked -text yet, so a CRLF checkout could change the rebuilt dist (handoff). The notice file is Vite's markdown format served as text/plain.


### M8-06

**No vulnerability scanning or update monitoring for the deployed Python runtime** (MEDIUM, gate M)

*Work package `m8/ci`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** Nothing checked the locked Python runtime (fastapi, starlette, uvicorn, h11, pydantic) against advisory data. CI had no schedule, and the only gate was npm audit at high severity, so moderate advisories in shipped browser dependencies passed.
- **Change.** New .github/workflows/dependency-audit.yml with permissions contents: read and actions pinned like ci.yml. Triggers: weekly cron '17 6 * * 1', workflow_dispatch, and pull_request on uv.lock, pyproject.toml, web/package.json, web/package-lock.json, .github/ci-requirements.txt and the workflow itself. Python job: installs the hash-pinned uv from ci-requirements.txt and runs `uv export --quiet --frozen --format requirements-txt --no-emit-project` with --no-dev and with --only-dev. Each export is then checked by `uv tool run --exclude-newer 2026-10-02T00:00:00Z --from pip-audit==2.10.1 pip-audit --strict --require-hashes --disable-pip --progress-spinner off --desc on --aliases on -r ...`. pip-audit 2.10.1 was published 2026-06-10; its own dependencies resolve as of a fixed date. Any vulnerability fails the job; the dev step runs even if the runtime step fails. npm job: `npm audit --omit=dev --audit-level=moderate`, then `npm audit --audit-level=high`. DEVELOPMENT.md 'Dependency maintenance' covers the audit cadence (weekly, per pull request, before every release), GitHub disabling schedules after 60 days without activity, handling an advisory (including a fix inside the cooldown, and that a deployed release's .venv is not touched by unattended-upgrades), and --ignore-vuln with a reason and review date. Fix-up: The workflow is unchanged from 6ef0e6c: weekly cron, workflow_dispatch and PR paths; a pip-audit 2.10.1 job on hash-pinned runtime and dev exports; and an npm audit job at moderate severity for shipped dependencies and high for all. Review fixes (nits):
- The 60-day rule is now qualified in the workflow header and in DEVELOPMENT.md. It reads 'In a public repository GitHub disables scheduled workflows after 60 days without repository activity, and in a fork by default', and the owner checks that the workflow is enabled before every release. Source: github/docs data/reusables/actions/scheduled-workflows-disabled.md at dae83f7, which says 'In a public repository, scheduled workflows are automatically disabled when no repository activity has occurred in 60 days'.
- 'on every push' became 'on pushes to main and on pull requests'.
- tzdata is now 'only on Windows and WebAssembly' (its exported marker is emscripten or win32). The two dev packages are named: colorama for Windows and httpx2-jsfetch for WebAssembly.
- New 'Numeric packages' paragraph: a lock change that upgrades numpy (with its bundled OpenBLAS), pandas or scipy is merged only after reproduce-research.yml passes, and every release needs a pass on its own commit. 'Handling an advisory' refers to it.
- **Before and after.** Before: no Python vulnerability check and no schedule. After: the implementer's local replay of the exact commands found no known vulnerabilities in the runtime or dev exports (exit 0), and the h11==0.14.0 control reported PYSEC-2026-348 (exit 1). Both reviewers reproduced this. In this round only comments and wording changed. actionlint 1.7.9 with shellcheck is clean on dependency-audit.yml, and the PyYAML parse passes.
- **Residual.** The workflow has not run on GitHub. The OSV service was unreachable from the sandbox, so only the PyPI advisory data was used. The schedule is disabled after 60 days without activity in a public repository; this is documented.


### M8-07

**Hash-verified research results and the API contract are not protected from CRLF conversion** (MEDIUM, gate J)

*Work package `m8/ci`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** .gitattributes protected only snapshot files, web/dist and the frontend fixtures. docs/results/** (hashed in every run manifest and checked by verify, inspect, the API readiness check and the M4 inputs), configs/** (part of every release's research fingerprint) and docs/api/openapi.json (compared byte for byte by CI and build-release.sh) were still converted under core.autocrlf=true, while git status stayed clean.
- **Change.** Added `docs/results/** -text`, `docs/api/openapi.json -text` and `configs/** -text`, each with a comment. Added two tests. (1) Every tracked file under atlas.deploy.manifest.RESEARCH_PATHS, every output named in any docs/results/**/manifest.json (committed or not) and CONTRACT must have `text: unset`. (2) A `git -c core.autocrlf=true archive --worktree-attributes` of the same paths must be byte-identical to an autocrlf=false archive and must match every manifest output hash (at least 40 checked), with no CR in the contract. Rewrote DEVELOPMENT.md 'Line endings on Windows' to list every protected class of file and the tests.
- **Tests.** tests/test_snapshot.py::test_results_configs_and_contract_are_protected_from_line_ending_conversion; tests/test_snapshot.py::test_a_crlf_checkout_writes_every_byte_verified_file_unchanged (positive control added)
- **Before and after.** With 8f077ed's .gitattributes in the working tree, both tests fail (2 failed, 1 passed for -k 'crlf or line_ending'). With the branch file, all 27 tests in test_snapshot.py pass. To exercise the control, I temporarily mutated the test so that both exports use core.autocrlf=false (an export that converts nothing), then restored it. The test then fails with 'AssertionError: autocrlf converted nothing; assert 0 == 272'. The 272 CRs match both reviewers' clone measurements of README.md.
- **Residual.** The test assumes git archive applies the same conversion as a checkout. The positive control now proves that conversion is active. Local core.attributesFile or .git/info/attributes overrides remain out of scope. Unprotected text files such as README and src are still converted on Windows. They are not hash-verified.


### M8-08

**UNCERTAINTY.md §9.6 calls the flagged target days 'intake drops'; demand on all 18 was far above the forecast** (MEDIUM, gate C)

*Work package `m8/docs`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** UNCERTAINTY.md 9.6 called the 18 flagged evaluation days 'intake drops that the forecast does not anticipate'. Under the document's own convention (error = prediction - actual) the mean error of -3,208 is an under-forecast: demand was far above the forecast.
- **Change.** The sentence is rewritten with verified figures. From committed metrics.json: bias -3,207.8, MAE 3,234.7, coverage 26.5 % and 70.2 %. From the M3 full run regenerated in M8, byte-identical to the manifest: mean outcome 13,428 against a mean forecast of 10,220; outcome above the forecast in 520 of 540 forecasts and above the day's average forecast on 18 of 18 days; 29.8 % of outcomes above q0.975, none below q0.025. The 18 days are four city-wide surges, the DOB interruption (13 requests against 281.5 expected) and 13 DHS days (12 against 1,417 expected), an order of magnitude below the miss and in the other direction. A marked M8 correction note records the old wording. The table caption states the sign convention, because the header row itself is generated by tests/test_m3_results.py::doc_tables. docs/audit/scripts/m8_doc_figures.py reproduces every figure. Fix-up: The prose fix and caption are unchanged from the branch. New test: it reads the committed flagged-stratum bias from full/metrics.json (decided pair, m3_evaluation, primary model) and requires the 9.6 prose after the table to name the matching direction ('under-forecast' when negative). The caption before the table must state the sign convention ('prediction − actual'). The old phrase may survive only inside the 'M8 correction:' note.
- **Tests.** tests/test_m8_docs.py::test_the_flagged_day_errors_are_described_in_their_actual_direction
- **Before and after.** Against the 8f077ed UNCERTAINTY.md the test fails with 'the bias column's sign convention is not stated'. Against the current document with only the 9.6 prose replaced by the 8f077ed prose (scratch hybrid), it fails with 'section 9.6 does not call the flagged days an under-forecast'. On d12f6b2 it passes.
- **Residual.** None beyond the documented figures. The table header row is generated by tests/test_m3_results.py, so the convention sits in the caption.


### M8-09

**API, M4 report and OPTIMIZATION.md call the archived July 2026 projection a 'FUTURE PROJECTION' with no archived/not-live disclosure** (MEDIUM, gate C)

*Work package `m8/docs`: partially fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** OPTIMIZATION.md and the committed M4 report describe the July 2026 horizon as a 'FUTURE PROJECTION' with no archived or not-live disclosure. Other docs I own described the projections in the same way.
- **Change.** Canonical wording A was added to OPTIMIZATION.md's 'Read this first' box. Section 9 has an M8 erratum: the committed report.md keeps the original 'FUTURE PROJECTION ... No outcome has been observed' wording and is not edited, because its hash is part of the reference run. Wording A was also added in BACKTESTING.md (record-type table and section 10 projection bullet, noting that the committed projection report keeps its label), in UNCERTAINTY.md (record-type table and section 9.8) and in the README. The dates are the committed ones (origin 2026-06-30, targets 2026-07-01..30).
- **Before and after.** Before: 9.8 erratum covered the method wording, the 'calibrated on past errors' label and the CSV status only. After: also the FUTURE PROJECTION wording of the committed label.
- **Residual.** Documentation part only; API notices, /system limitations and docs/API.md belong to other work packages (handoff).

*Work package `m8/api`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** No API response said that the July 2026 projection is archived and not live. The API called it a 'FUTURE PROJECTION ... No outcome has been observed' for target dates that are now in the past, and the /system limitations and docs/API.md said nothing about it either.
- **Change.** Added views.archived_projection_notice(store), which builds canonical wording A from the loaded data: the snapshot's last observed day (inputs.data.series.end) and the projection's first and last target dates. The notice goes into the existing integrity.notices of forecasts, uncertainty intervals, the scenario list, scenario detail, scenario comparison, simulation results and simulation parameters, and first in /system scientific_status.limitations, so no schema changed. The API description (OpenAPI info) gained one date-free bullet saying the same. New guard reference.check_fixed_wording: the forecast component loads only when the projection origin equals the snapshot's last observed day, so the notice is true by construction. docs/API.md: new bullet in the 'Read this first' box, plus §4 notices, §5 /system and /forecasts, and §11 limitations. Fix-up: src/atlas/api/app.py: the bullet now reads '... issued from the frozen snapshot. It is not a live forecast, and it has never been scored: no outcome after the snapshot's last observed day is in the snapshot.' openapi.json was regenerated; the info description is not part of openapi.gen.ts.
- **Tests.** tests/test_m5_contract.py (OpenAPI equals the code; the archived-notice contract test) passes unchanged
- **Before and after.** Wording nit; `uv run atlas api openapi | diff - docs/api/openapi.json` is identical after regeneration.
- **Residual.** None beyond the first round (the M2 'FUTURE PROJECTION' label is still served next to the notice, by design).


### M8-10

**Anomaly flags miss most of the winter 2025-26 anomaly cluster; docs overstate robustness to anomalies** (LOW, gate L)

*Work package `m8/docs`: fixed.* Reviews: verify needs change, skeptic accept; a fix-up round addressed the requested changes.

- **Defect.** DATA_QUALITY.md said 'The median and MAD resist earlier anomalies' without the detectors' power limits, and the flags miss most of the winter 2025-26 anomaly cluster. BACKTESTING.md's exclude-flagged sensitivity read as robustness to anomalies.
- **Change.** DATA_QUALITY.md 3 adds a marked 'Known limits of the detectors' note. MAD inflation over the holiday season: 2026-01-27, 19,225 requests (+70 %), eight preceding Tuesdays 8,354-14,078, MAD 1,215, scale 1,801.4, score +4.40. An interruption shortly after another is undetectable: DHS 0, 0, 3, 3 on 2026-02-22..25, with a 28-day 10th percentile of 0. Of 61 days at least 25 % above reference, 16 are flagged (24 and 4 in 2025-07..2026-06). A table lists the unflagged extremes 2026-01-27, 2026-01-28 and 2025-12-15, verified as 3 of the 5 largest days. BACKTESTING.md section 10 qualifies the sensitivity: it covers the 44 M1-flagged target days only; in the confirmation year 18, 13 of them DHS 2026-01-31..02-12 (DHS is about 1.4 % of five-borough volume); the extreme city-wide days stay in. All figures are reproduced by docs/audit/scripts/m8_doc_figures.py. Fix-up: The sentence now reads: 'Three of the five largest days of the series, 2026-01-27, 2026-01-28 and 2025-12-15, carry no flag and stay in (the largest, 2026-02-24, is flagged and excluded)'. This is verified against the recorded m8_doc_figures top-5 table: 2026-02-24 surge flag True; the other three False.
- **Before and after.** Before: 'The most extreme city-wide days of that winter ... carry no flag'. After: the wording above, consistent with DATA_QUALITY.md section 3.
- **Residual.** Disclosure only; detectors and results unchanged.


### M8-11

**Stale status and stale statements after the M7 merge (ROADMAP, DASHBOARD, DATA_SNAPSHOT, README notice heading)** (LOW, gate L)

*Work package `m8/web`: partially fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** docs/DASHBOARD.md:10 still said M7 was 'implemented on feature/atlas-m7-dashboard, awaiting review'. The Methods card claimed 'reproduction of every legacy figure', which is a slight overstatement: one RMSE rounding is a declared exception.
- **Change.** The DASHBOARD.md status now reads 'merged into main (merge commit 194fa4a) and reviewed in the M8 release audit (docs/audit); section 17 lists the corrections'. 'Nothing has been deployed' and the untested statements are kept: real screen readers, users with disabilities, Firefox, Safari. The Methods card now says 'reproduction of every legacy figure within its published precision (one RMSE rounding is a declared exception)'. A new section 17 lists every corrected dashboard finding with its test; sections 10 and 13 are updated with measured figures and test counts. This is the dashboard part only. Fix-up: From a215e8c (accepted): the status now reads 'merged into main (merge commit 194fa4a) and reviewed in the M8 release audit'. 'Nothing has been deployed' and the untested statements are kept: screen readers, Firefox, Safari. The Methods sentence now says 'within its published precision (one RMSE rounding is a declared exception)'.

Fix-up: DASHBOARD.md section 10 is rewrapped to the document's width. That covers the reviewer's 117-character line, plus two 103- and 104-character lines that predate the branch in the same section. The measured whole-build size is now 524.4 KB and the Vitest count 191. Only the dashboard part is fixed.
- **Tests.** web/src/views/m8-audit.test.tsx M8-31 test (also asserts the corrected Methods sentence)
- **Before and after.** The Methods assertion fails on 8f077ed and passes on the branch. The DASHBOARD.md status line is documentation and has no test.
- **Residual.** The other parts of M8-11 (ROADMAP.md, README.md, DATA_SNAPSHOT.md, DATA_QUALITY.md, DEVELOPMENT.md) belong to the docs work package.

*Work package `m8/docs`: partially fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** Several statements were stale: ROADMAP said M7 is unmerged and 'Nothing from M8 onward'; the README notice was headed '(milestone M0)'; DATA_SNAPSHOT.md pointed at `pytest -m integration` for 'the same facts', attributed the deferred residual breakdown to M1, and said anomaly flagging was 'planned for M1'; DATA_QUALITY.md gave a single 72 % HPD catch-up share.
- **Change.** ROADMAP.md: M0-M7 merged (M7 in 194fa4a); M7 heading 'merged'; a new 'M8: Final audit, corrections and release candidate (under review)' section linking docs/audit/README.md (with canonical wording C, for the lead to finalise). README: the '(milestone M0)' notice is gone with the rewrite. DATA_SNAPSHOT.md: the command names the four M0 modules (verified: 115 passed in 24 s), with a note that `-m integration` now selects 41 modules, 31 of them M2-M7 (verified by grep); the residual breakdown is 'deferred ... ROADMAP'; 'Other observations' points to `atlas quality` and DATA_QUALITY.md. DATA_QUALITY.md: the HPD share depends on the reference, about 72 % weekday-blind and about 69-86 % with same-weekday medians (68.9 % with 8 weeks, 85.7 % with 4 weeks, verified).
- **Before and after.** As in the branch report; re-verified by both reviewers (115 passed for the new DATA_SNAPSHOT recipe; 41 integration modules, 31 M2-M7; HPD 72.2/68.9/85.7 %).
- **Residual.** DASHBOARD.md:10, DEVELOPMENT.md:79 and web/src/views/Provenance.tsx:30 are outside my files (handoff). The lead finalises the ROADMAP M8 section.


### M8-12

**The first documented M2 verify command fails with the current code** (LOW, gate J)

*Work package `m8/docs`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** BACKTESTING.md's quick start gave `atlas backtest verify docs/results/atlas-m2-rolling-origin-v1/development`, which fails with the current code. Section 8's recipe (a worktree at a16672f) points at a checkout that lacks the development directory, which was added in 10343e2.
- **Change.** The quick start now verifies the full reference run, with a marked M8 note explaining the change. Section 8 shows the passing command and a corrected recipe: `git worktree add ../atlas-a16672f a16672f; cd ../atlas-a16672f && uv sync --locked; uv run atlas backtest verify <current checkout>/docs/results/atlas-m2-rolling-origin-v1/development`. The recipe was checked from an export of a16672f with its own lock: identical, fingerprint fbb948ec…dcea43. The note also states what differs with the current code.
- **Before and after.** Reviewers: the new quick start verifies the full run (d53bc911…). The corrected recipe from an a16672f export verifies identical (fbb948ec…dcea43).
- **Residual.** No CI job automates it (M8-38 is the lead's).


### M8-13

**M3 conclusions rest on p-values not adjusted for multiplicity, one printed next to the disclosed M2 limitation** (LOW, gate C)

*Work package `m8/docs`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** UNCERTAINTY.md drew conclusions from M3 p-values not adjusted for multiplicity ('carries information', 'beat climatology'). It printed the ATLAS-vs-B* WIS p<0.001 without the disclosed M2 H1 caveat.
- **Change.** A section 9 M8 note says the M3 comparisons are descriptive and unadjusted. In 9.4 the climatology conclusion is softened to 'suggests ... evidence is weak': Holm over the five evaluation WIS tests 0.033, size-corrected 0.034, both together 0.138. The ATLAS-vs-B* line gains the H1 caveat: H1 is not significant after Holm (p 0.054), and the selection-period WIS p is 0.266; the WIS difference itself survives Holm (0.001) and the size correction (0.0075; 0.0375 with both). In 9.5 and 9.9(4) the Brier P75 result is qualified (p 0.038 unadjusted, 0.113 after Holm, 0.076 size-corrected). Section 11 has a limitation bullet. All values come from the recorded dm_size_study output (section 5 Holm table).
- **Before and after.** Figures re-verified by the reviewers against dm_size_study_output section 5. That section is unchanged by the regeneration.
- **Residual.** The committed M3 report.md is unchanged by design.


### M8-14

**The coverage gate's 'conservative' p-value is the lenient choice for a calibration claim** (LOW, gate C)

*Work package `m8/docs`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** The M3 coverage gate uses the larger ('conservative') p-value. For a test whose pass condition is non-rejection, that is the lenient choice, yet the documentation calls it conservative.
- **Change.** Sections 4 and 8 of UNCERTAINTY.md carry an M8 note. For coverage, the larger p is lenient: coverage counts as rejected only when both estimators reject. The note records that it did not bind (smaller p-values 0.989 and 0.445, against 0.989 and 0.458 used, verified from metrics.json sensitivity rows) and that the status is failed_calibration on the tolerance criterion anyway. The frozen v1 rule is kept; a later protocol should reject when either estimator rejects.
- **Before and after.** As in the branch report (non-binding: 0.989/0.445 against 0.989/0.458 used).
- **Residual.** The frozen v1 rule and code are unchanged.


### M8-15

**Projection artefacts misdescribe the calibration window; projection CSV rows carry no status** (LOW, gate C)

*Work package `m8/docs`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** The committed M3 projection report says 'calibrated at the origin on every residual observed by then', although it uses a 365-day window. Its label, also in the manifest, says 'calibrated on past errors'. The projection CSV rows carry no calibration status.
- **Change.** UNCERTAINTY.md 9.8 adds an 'M8 erratum on the committed projection artefacts' (they stay as committed, hash-checked by M4 and the API). It states the actual window, verified as h=1 cal_n 365, targets 2025-07-01..2026-06-30, while residuals exist from 2024-02-02. It notes that 'calibrated on past errors' describes intervals that FAILED calibration and that the API serves its own label (API.md section 5). It records that projection_intervals.csv and projection_exceedance.csv rows carry status=projected but no calibration status, which is only in manifest.json and report.md. It adds canonical wording A. Fix-up: The 9.8 erratum from the branch is kept. The FUTURE PROJECTION label clause was added (see M8-09).
- **Before and after.** As before, plus the label clause, verified in projection/report.md and manifest.json.
- **Residual.** Wording for future runs in src/atlas/uncertainty_runner.py and PROJECTION_LABEL (handoff); the committed artefacts stay.


### M8-16

**`fixed365` is documented as a 365-day calibration but held 153-182 residuals in the selection period** (LOW, gate C)

*Work package `m8/docs`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** fixed365 is documented as a 365-day calibration, but in m3_selection each fixed set held only 153-182 residuals, while the rolling sets grew from 153 to 365.
- **Change.** A marked M8 correction in UNCERTAINTY.md section 5 and section 9.1 explains that 365 days is a maximum. Residuals begin at origin 2024-02-01, so in m3_selection the fixed365 sets held 153-182 residuals (median 167.5, targets from 2024-02-02) and the rolling365 sets 153-365 (median 319). In m3_evaluation every set held 365. These values are verified from calibration_support for both point models.
- **Before and after.** Verified by both reviewers from calibration_support.
- **Residual.** None.


### M8-17

**Effective-sample-size wording is inconsistent and overstates the evidence in places** (LOW, gate C)

*Work package `m8/docs`: fixed.* Reviews: verify accept, skeptic needs change; a fix-up round addressed the requested changes.

- **Defect.** Effective-sample-size statements contradicted each other or overstated the evidence. UNCERTAINTY 9.9(5) gave a blanket 'no coverage or probability estimate rests on 30 or more', against 242.0, 47.1 and 31.7 in the table; section 11 said 'only tens' where 9.2 says 8-9. BACKTESTING section 10 attributed Bartlett-2h effective sizes (14-27) to 'the primary bandwidth'.
- **Change.** UNCERTAINTY 9.9(5) is scoped to the days 1-30 estimates of the decision-grade criteria (8.1 to 16.2 for the primary model), noting that shorter buckets are worth more (242.0 at day 1, 31.7 at days 8-14). Section 11 now says 'about 8 to 9 independent observations for the days 1-30 coverage', with a note that the committed reports' generic text keeps 'tens'. BACKTESTING section 10 gives a marked correction: 14-27 are the Bartlett 2h eff. n of section 6.5; under the primary rectangular bandwidth H1 and H2 are worth 13.0 and 20.8, and 9.6-17.4 under the wider ones. All values are from metrics.json. Fix-up: BACKTESTING section 10 now reads: under the primary rectangular bandwidth H1 and H2 are worth 13.0 and 20.8; under rectangular 59, Bartlett 90 and Bartlett 120, 9.6-17.4; under Bartlett 60, 13.8 and 21.2 (section 6.2); so not every wider bandwidth gives fewer. This also corrects the old 'fewer under wider ones'. Values are verified against the 6.2 table. UNCERTAINTY 9.9(5) now says 'the scope of the evidence criterion (section 8)'.
- **Before and after.** Before: '...and under the wider ones 9.6-17.4' and '...the scope of the decision-grade criteria'. After: the wording above. The test_m2_results, test_m3_results and test_m4_results modules pass (41 passed).
- **Residual.** src/atlas/probabilistic.py LIMITATIONS 'tens of independent observations' remains for v1. It could be changed for future runs only (handoff).


### M8-18

**The 'failed' calibration wording is hard-coded in the API and dashboard instead of derived from the served status** (LOW, gate H)

*Work package `m8/web`: partially fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** The dashboard wrote 'failed' and 'uncalibrated' into the Uncertainty page (header, status prose, chart summary and name, coverage panel title, navigation summary) and 'FAILED_CALIBRATION' into the Decisions and Simulator callouts and the Methods card, whatever M3 status the API actually served.
- **Change.** Uncertainty.tsx has an OUTCOME table keyed by the served interval_status (decision_grade, failed_calibration, insufficient_evidence, unavailable). It supplies the page header, the 'What this means' prose and the band chart's accessible summary; the header says 'The status below says how they did.' while loading. bandsWord() gives 'calibrated' only for decision_grade, for the panel title, chart name and toggle label. The coverage panel title comes from the served decision_grade. A new helper, intervalsWithStatus(integrity), names the stress-path intervals on Decisions and the Simulator from the served integrity status. The Methods M3 card appends 'Result: <STATUS>.' from the served status. The navigation summary is now 'M3 intervals and their calibration'. The unused IntervalStatusCallout, which had fixed 'failed' wording, is removed. This is the dashboard part only; the API strings are another work package's. Fix-up: From a215e8c (accepted by both reviewers): the OUTCOME table, the served-status helpers on Decisions, the Simulator and Methods, and the navigation summary.

Fix-up in b56c308:
- bandsWord() returns 'calibrated' for decision_grade, 'uncalibrated' for failed_calibration and insufficient_evidence, and null otherwise.
- With an unavailable or unloaded status the panel title and the toggle say 'quantile bands', the chart is named 'Archived projection with marginal quantile bands', and the table caption says 'quantiles of unknown calibration'.
- outcomeHeader() reads 'Its result could not be loaded here.' when the status resource is in error.
- The FAILED_CALIBRATION wording is byte-for-byte unchanged.
- DASHBOARD.md sections 6 and 17 describe the rule.

Only the dashboard part is fixed; the API's own strings belong to another work package.
- **Tests.** web/src/views/m8-audit.test.tsx 'never says the intervals failed when the status is insufficient_evidence | unavailable | decision_grade' (now also asserts the chart name and panel title for each status); web/src/views/m8-audit.test.tsx 'says they failed when the status is FAILED_CALIBRATION' (now also pins the unchanged chart name); web/src/views/m8-audit.test.tsx 'says the result could not be loaded when the status does not load' (new); web/src/views/m8-audit.test.tsx 'names the intervals elsewhere by the status ...' and 'names the intervals of the M4 stress paths by their served status'
- **Before and after.** 8f077ed: the 3 'never says failed' tests and the 2 'names ... by served status' tests fail, as both reviewers reproduced.

a215e8c: with its Uncertainty.tsx and Decisions.tsx swapped into this branch, the 'unavailable' test fails on the chart name ('uncalibrated'), and the new load-error test fails because the header says 'The status below says how they did.'. Both pass at c2a29dc. The full log is in the scratchpad (before-a215e8c.log).
- **Residual.** Wherever the dashboard shows the API's own fixed strings (INTERVALS_LABEL, MARGINAL_QUANTILE_NOTICE), they are shown verbatim; changing them is the API work package's job.

*Work package `m8/api`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** The 'FAILED' calibration wording in the API is fixed text with no check against the served M3 status: INTERVALS_LABEL, the API description, the interval summary and descriptions in the contract, and M4's MARGINAL_QUANTILE_NOTICE as the API uses it. A re-run with another status would be served next to text saying it failed.
- **Change.** Startup assertion: reference.WORDED_INTERVAL_STATUS = 'failed_calibration' and check_fixed_wording(inputs), called in the forecast component. Any other committed status makes the forecast chain not ready, with the detail 'the M3 status is X, but the API's interval wording states failed_calibration: review that wording before serving this run'. INTERVALS_LABEL now interpolates the constant. Chosen over deriving the text because most of the wording is static OpenAPI text that cannot be derived. Documented in API.md §2, §5 intervals and §8. Fix-up: INTERVALS_LABEL states '(failed_calibration)' literally again, so its bytes are identical to 9fab63d's output. A comment says it is served only while the status is reference.WORDED_INTERVAL_STATUS (check_fixed_wording) and that the two are reviewed together. The guard test now asserts that the label contains the worded status, so the label and the constant cannot drift apart.
- **Tests.** tests/test_m5_guards.py::test_a_status_the_fixed_wording_does_not_state_is_not_served (extended with the label/constant tie)
- **Before and after.** Refactor of a nit. The extended test passes on 9fab63d and on 8077c65. It does not fix a defect; it locks the tie. Fixtures re-record unchanged.
- **Residual.** This fails closed on purpose: a legitimate future M3 re-run with another status takes the forecast, intervals, M4 and simulation components down until the wording is reviewed. The dashboard's own hard-coded prose (web/src/views/Uncertainty.tsx) is not covered (handoff).


### M8-19

**Certificate tolerance ratios are shown as a 'gap' and plans called an 'exact optimum'** (LOW, gate H)

*Work package `m8/web`: partially fixed.* Reviews: verify needs change, skeptic needs change; a fix-up round addressed the requested changes.

- **Defect.** The plan view printed a dimensionless certificate ratio as '(gap 6.1e-9)'. Decisions said every result had 'a zero duality gap' and called each plan 'the exact optimum', although certification is tolerance-based and an optimum may be one of several.
- **Change.** The zod Certificate schema also reads dual_sign and stationarity; both are in the contract already, so openapi.gen.ts is unchanged. plan.tsx shows the largest of the four ratios: 'passed the primal and dual feasibility and duality-gap certificate within its tolerances (largest violation 6.1e-9 of its tolerance)'. Decisions now reads: 'checked for primal and dual feasibility and for its duality gap, each within small tolerances (1e-7 absolute and 1e-9 relative) ... That makes each plan an optimum of an assumed problem within those tolerances, and one of several when the optimum is not unique (such plans are labelled).' The tolerances come from CERT_ABS and CERT_REL in optimizer.py and OPTIMIZATION.md:135. This is the dashboard part only. Fix-up: From a215e8c (accepted): the schema reads dual_sign and stationarity, and plan.tsx shows 'largest violation X of its tolerance', the largest of the four ratios.

Fix-up in b56c308: Decisions now says the answer is 'checked for primal feasibility (each row and bound within 1e-7 + 1e-9 × its magnitude), for dual feasibility and for its duality gap (each within 1e-9 × its magnitude)'. This matches optimizer.py certify() (CERT_ABS + CERT_REL × magnitude for primal; CERT_REL × max(1, magnitude) for dual signs, stationarity and the gap) and OPTIMIZATION.md section 4. It avoids 'zero duality gap', which an existing test forbids. 'an optimum of an assumed problem within those tolerances, and one of several when the optimum is not unique' is kept. DASHBOARD.md section 17 is updated. Only the dashboard part is fixed.
- **Tests.** web/src/views/m8-audit.test.tsx 'shows its largest violation as a fraction of the tolerance, not as a duality gap'; web/src/views/m8-audit.test.tsx 'shows the largest of its four checks, whichever it is' (new: reduced_budget, where stationarity 6.58e-8 > gap 5.47e-9, expects '6.6e-8' and not '5.5e-9'); web/src/views/m8-audit.test.tsx 'calls each plan an optimum within the solver tolerance, not the exact optimum'; web/src/views/m8-audit.test.tsx 'states the tolerances of each check as the optimiser applies them' (new)
- **Before and after.** Tolerance test: with a215e8c's Decisions.tsx it fails ('Expected element to have text content: /primal feasibility \(each row and bound within 1e-7 \+ 1e-9 × its magnitude\)/'); it passes at c2a29dc.

Largest-of-four test: on 8f077ed it fails (the text there is 'gap 5.5e-9'). Under a mutation that shows only duality_gap it fails with 'largest violation 5.5e-9 of its tolerance' (mutation-gap-only.log); it passes at c2a29dc.

The two original M8-19 tests fail on 8f077ed and pass on the branch.
- **Residual.** The certificate's Field descriptions in the API schema and in docs/API.md (the API part of M8-19) are handed off.

*Work package `m8/api`: fixed.* Reviews: verify accept, skeptic needs change; a fix-up round addressed the requested changes.

- **Defect.** The API's Certificate fields (primal, dual_sign, stationarity, duality_gap) are unitless tolerance ratios but had no description. Readers take duality_gap for the duality gap itself.
- **Change.** Field descriptions on every Certificate field: each ratio is the largest violation in units of its tolerance and at most 1 passes. The tolerances in the text are built from atlas.optimizer.CERT_ABS and CERT_REL (1e-7, 1e-9) together with the magnitude each one uses in certify(). duality_gap is described as not the absolute gap, which is primal_objective - dual_objective; the objectives' units are given. A class description was added, and Programme.certificate now says that reported_plan's certificate is that of the last tie-break stage while strict_programme carries the penalised-score programme's (checked against metrics.json objectives backlog_days and weighted). openapi.json and openapi.gen.ts regenerated; API.md §5 scenario detail explains the fields. Fix-up: docs/API.md §5 now says: tolerance 1e-7 + 1e-9 × the magnitude of the row or bound for primal, and 1e-9 × max(1, the magnitude of the quantity checked) for the other three ('the contract gives each'); the gap itself is |primal_objective - dual_objective|. I checked this against optimizer.py:778-817 (CERT_ABS 1e-7, CERT_REL 1e-9). The schema descriptions were already correct and are unchanged.
- **Tests.** Documentation only. The existing tests/test_m5_contract.py::test_certificate_fields_say_they_are_tolerance_ratios still passes.
- **Before and after.** Documentation corrected; there is no executable behaviour to test.
- **Residual.** Dashboard wording (plan.tsx 'gap', Decisions.tsx 'exact optimum') remains a web handoff from the first round.


### M8-20

**Unverified run_id from the M2/M3 projection manifests is served as provenance** (LOW, gate A)

*Work package `m8/api`: fixed.* Reviews: verify needs change, skeptic needs change; a fix-up round addressed the requested changes.

- **Defect.** The run_ids from the M2 and M3 projection manifests were served as provenance without any check. An edited manifest left readiness at 200 and a forged id was served, although the M4 run records each manifest's SHA-256 and run_id.
- **Change.** In reference._load_m4: (manifest_sha256, run_id) of the M2 projection provenance must equal the M4-recorded inputs.forecast. The set of recorded stress_paths must equal the decision inputs' quantile paths, and each path's (manifest_sha256, run_id) must equal its record. Otherwise ArtifactError is raised: m4_reference_run and simulation become not ready, readiness returns 503 and names the cause, like the existing 'different M2 projection' and 'different M3 status' cross-checks. docs/API.md §2 and §8 say this, including the residual. Fix-up: 1. docs/API.md §8: 'Fail closed' now covers the pinned chain (the files the reference configuration pins and the checks of M4's input loader) and points to the M4 record check for the projection manifests.
2. ReferenceStore._projection_run_id(): forecast_source() and intervals_source() serve a projection run's id only while the M4 reference component, the only check of that id, is ready. Otherwise the id is null, so a forged id never reaches /forecasts, /uncertainty/intervals or /system. Source.run_id gained a description saying so; openapi.json and openapi.gen.ts were regenerated.
3. _load_m3_intervals reads the M3 manifest once, as bytes. Their SHA-256 must equal the manifest_sha256 recorded by the stress paths, which _load_m4 compares with the M4 record. A manifest changed between the reads therefore makes m3_projection_intervals not ready.
4. docs/API.md §2 table, §4 provenance and §10 tables updated.
- **Tests.** tests/test_m5_reliability.py::test_a_forged_projection_run_id_is_never_served[m2_projection_manifest_run_id_edited-m2_projection_run] and [m3_projection_manifest_run_id_edited-m3_projection_run]. These replace test_a_forged_projection_run_id_makes_readiness_fail. Readiness is 503 and names the manifest; /scenarios and /simulations/parameters answer 503; /forecasts, /uncertainty/intervals and /system answer 200 without 'FORGED-RUN-ID', and the projection source's run_id is null.; tests/test_m5_guards.py::test_a_projection_manifest_swapped_between_reads_is_not_served (new). load_inputs is monkeypatched to rewrite the M3 manifest run_id after the real load. M4 stays ready and m3_projection_intervals is not ready, with 'M3 projection manifest changed'.
- **Before and after.** On 9fab63d, both parametrised cases fail with 'AssertionError: /api/v1/forecasts' and '/api/v1/uncertainty/intervals' ("run_id":"FORGED-RUN-ID" was served), and the swap test fails with 'assert not True' (M3 intervals ready). On 8f077ed the parametrised cases fail too. The guards module cannot be collected on 8f077ed (ImportError: WORDED_INTERVAL_STATUS, which 9fab63d added). All pass on 8077c65. The existing tamper CASES (M4_CHAIN not ready) still pass.
- **Residual.** decision_inputs (not owned) still hashes and parses each projection manifest in two separate reads. The M4 comparison of both the SHA-256 and the run id catches a split between those reads for the served ids. When the reference configuration has no stress path, nothing records the M3 manifest; the committed configuration has one (q0.9).


### M8-21

**HEAD on versioned GETs returns 405 while the nginx allow-list passes HEAD** (LOW, gate E)

*Work package `m8/deploy`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** Route.methods allowed ('GET','HEAD') for every GET operation ('as the API answers it'). The API answers HEAD only on /health/live and /health/ready and returns 405 on the 11 versioned GET operations, so nginx passed HEAD to an API that refused it.
- **Change.** New `routes.HEAD_ANSWERED = {'/health/live', '/health/ready'}`. Route.methods returns ('GET','HEAD') only for those paths and (method,) otherwise. Docstrings corrected. nginx now answers HEAD on versioned reads with its own JSON 405, like any other wrong method. Dashboard static locations keep GET/HEAD; the API's dashboard mount answers HEAD.
- **Tests.** tests/test_m6_render.py::test_head_is_routed_only_where_the_api_answers_it; tests/test_m6_proxy.py::test_the_api_behind_the_proxy (real nginx in front of the real API, every GET route)
- **Before and after.** 8f077ed (both reviewers): test_head_is_routed_only... fails, and test_the_api_behind_the_proxy fails with `AssertionError: /api/v1/forecasts`. Both pass at a8a77a6 (test_m6_proxy.py is in the 503-passed run).
- **Residual.** A HEAD request to a versioned endpoint gets nginx's JSON 405, before authentication, as other wrong methods do.


### M8-22

**After a worker replacement the next simulation goes to the cold worker while a warm one is idle** (LOW, gate E)

*Work package `m8/api`: fixed.* Reviews: verify needs change, skeptic accept; a fix-up round addressed the requested changes.

- **Defect.** SimulationPool used its idle list as a stack. A worker replaced after a timeout or crash went back on top, so the next simulation waited for the cold process to import ATLAS (outside its deadline) while a warm worker sat idle.
- **Change.** SimulationPool.acquire() now returns the most recently released idle worker whose `ready` flag is set (one that has completed its startup handshake). With none, it pops the last as before. No queue is added and Busy/close semantics are unchanged. API.md §9 explains this. Fix-up: 1. SimulationPool.release() appends a warm worker at the end of the idle list. A worker that has not answered since its process started (replaced, or its start failed) goes to the front, so a replaced worker is the last choice and the earliest replacement is taken first. acquire() keeps its preference for the warm worker released last.
2. SimulationService.warm_up() now takes every idle worker before the first ping. This was the reviewers' pre-existing nit: it used to ping the preferred worker size times and the others never.
3. docs/API.md §9 now describes the actual rule, and the §10 test table is updated.
- **Tests.** tests/test_m5_concurrency.py::test_after_sequential_use_a_replaced_worker_is_taken_last (new). It reproduces what a running server does: a pool of 2 with slow_worker; it waits until both handshakes are pending (both imports done); it uses one worker sequentially (two acquires return the same worker) and times it out at 0.2 s. It then asserts that neither worker is ready, that the next acquire() is the other worker, that the warm worker is preferred again, that the replaced one is taken only last, and that a third acquire raises Busy.; tests/test_m5_concurrency.py::test_warm_up_pings_every_worker (new): after warm_up() == 2 on a 2-worker service, both workers are flagged ready and both are idle.; tests/test_m5_concurrency.py::test_a_replaced_worker_waits_while_a_warm_one_is_idle (existing test from 9fab63d; unchanged and still passing)
- **Before and after.** On 8f077ed and on the previous m8/api head 9fab63d, test_after_sequential_use_a_replaced_worker_is_taken_last fails with 'assert <_Worker ...> is <_Worker ...>' at `chosen is other` (the replaced worker is returned). test_warm_up_pings_every_worker fails with 'assert [False, True] == [True, True]'. Both pass on 8077c65.

End to end against `atlas api serve` (2 workers, timeout 10 s; scratch script crash_e2e.py): after three sequential 14-day simulations, the worker that had served them was SIGKILLed, and the next request on it answered 500. With 9fab63d's pool, the following simulation took 1.15 s total for compute;dur=232.9. With 8077c65 it took 0.24 s for compute;dur=232.1.
- **Residual.** A worker that died while idle and still carries the warm flag is still preferred until replace_dead_idle() (run by the readiness check) or the request itself finds it dead. That behaviour is unchanged. A never-used worker whose import has not finished (a replacement within the first second or so after startup) can still be chosen ahead of nothing better. Also unchanged: benchmark.py:203 'warm every worker' sends sequential requests, which reach one worker. I left that measurement tool alone (see notes).


### M8-23

**/docs and /redoc (on by default) load floating CDN bundles without SRI or CSP on the API/dashboard origin** (LOW, gate F)

*Work package `m8/api`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** /docs and /redoc (on by default) were FastAPI's stock pages. They loaded floating-major CDN bundles (swagger-ui-dist@5, redoc@2) without SRI, plus a favicon from fastapi.tiangolo.com and Google Fonts, on the API and dashboard origin and with no CSP, and Swagger UI's Authorize dialog takes the simulation key.
- **Change.** New src/atlas/api/docs.py with the package's own pages, installed when ATLAS_API_DOCS is on; FastAPI's docs_url and redoc_url are now None. The pages pin swagger-ui-dist 5.33.1 (published 2026-10-01) and redoc 2.5.4 (published 2026-09-10) on jsDelivr. SRI sha384 values were computed from the npm registry tarballs after checking each tarball against the registry's dist.integrity sha512, and the documented tar|openssl command reproduces them. The pages request no fonts or favicon (data:,), and validatorUrl is null. Each page sends its own CSP: default-src 'none'; script-src only the exact pinned file, plus Swagger UI's inline init script by sha256; style-src the pinned CSS (Swagger UI) or 'unsafe-inline' (ReDoc, required); img-src data:; connect-src 'self'; worker-src blob: (ReDoc search); base-uri, form-action and frame-ancestors 'none'. The middleware now gives API_CSP to any answer on a docs path that does not carry its own policy; before, a 405 there had none. The unused /docs/oauth2-redirect page is gone. Browser check: a scratch Playwright script (not committed) ran headless Chromium against `atlas api serve` on port 9473, with the CDN URLs answered from the tarball bytes. Swagger UI rendered with no CSP violation, and 'Try it out' made GET /api/v1/system and got 200. ReDoc rendered (20 tag sections); its only violation is the refused cdn.redoc.ly footer logo. With a byte appended to each file, both pages refused to run them ("Failed to find a valid digest in the 'integrity' attribute"). Experiments showed Swagger UI needs no 'unsafe-inline', so it was removed. ReDoc fails to render without inline styles, its search worker is refused without worker-src blob:, and both pages need img-src data:. Documented in API.md §1 and §7. Fix-up: test_docs_pages_load_pinned_files_under_their_own_csp now asserts the exact set of directives per page; default-src, base-uri, form-action and frame-ancestors equal 'none'; img-src is data:; for /docs, style-src is exactly the pinned CSS; for /redoc, style-src is 'unsafe-inline' and worker-src is blob: (only on /redoc). docs.py is unchanged.
- **Tests.** tests/test_m5_security.py::test_docs_pages_load_pinned_files_under_their_own_csp[/docs] and [/redoc] (tightened)
- **Before and after.** These tests fail on 8f077ed (stock FastAPI pages). The tightened checks pass on 9fab63d and 8077c65, since the policies were already right; the tightening guards against regressions.
- **Residual.** As in the first round: the pages need jsDelivr at view time, and live CDN bytes could not be compared because the sandbox proxy returns 403.


### M8-24

**release.json environment_fingerprint differs between two builds of the same commit** (LOW, gate J)

*Work package `m8/deploy`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** environment_fingerprint hashed every non-bytecode file of .venv, including the project's dist-info uv_cache.json (uv writes the project's file times into it at each build) and the RECORD that hashes it. Two builds of one commit therefore never shared a fingerprint.
- **Change.** Fingerprint version 2 (ENVIRONMENT_FINGERPRINT_VERSION) leaves out `*.dist-info/uv_cache.json` and the RECORD of a .dist-info that holds a uv_cache.json. build_manifest records `environment_fingerprint_version: 2`. check_release computes the fingerprint with the version the manifest records; a manifest without one, written by 0.7.0, is checked with version 1 (every file), so a newer tool can still restore or roll back to older releases. An unknown version is reported. I did not drop the version marker: without it, the 1.0.0 tool would refuse every 0.7.0 release (rollback_failed). The documented consequence is that a 0.7.0 tool refuses 1.0.0 releases, so the 0.7.0 to 1.0.0 upgrade is run with the new release's own tool, as was done for M6 to M7. Fix-up: As in 8994c45: fingerprint version 2 leaves out `*.dist-info/uv_cache.json` and the RECORD of a .dist-info that holds one. Manifests without the version field (0.7.0) are checked with version 1, and an unknown version is reported. Fix-up: the docstrings (module and environment_fingerprint) and DEPLOYMENT.md section 10 add the platform (architecture and C library, which select the wheels) and the Python patch version to the conditions. 'On one server or on two' is gone. The measurement is described as one x86-64 machine with the build's uv settings (uv 0.11.32, copy mode, compiled bytecode, /usr/bin/python3.12), and two servers are said to share the fingerprint only if they match in all of these. The test also checks that another distribution's RECORD still counts.
- **Tests.** tests/test_m6_manifest.py::test_two_builds_of_one_commit_have_one_environment_fingerprint (extended: uvicorn-0.54.0.dist-info/RECORD altered -> 'environment (.venv) differs'); tests/test_m6_manifest.py::test_a_manifest_written_before_m8_is_checked_as_it_was_written
- **Before and after.** 8f077ed sources: test_two_builds... fails at the build_manifest equality (environment_fingerprint c893... != 2ecc...). The reviewer's every-RECORD mutant now fails test_two_builds (1 failed); the 8994c45 test let it pass. Passes at a8a77a6. The measurement figures (4,390 files; v1 6765f9e3... vs 7b5277f7...; v2 16d4ec55...) are the implementer's scratch outputs in scratchpad/m824, which I re-read; uv --version reports 0.11.32 and uname -m reports x86_64.
- **Residual.** The fingerprint still depends on install path, uv version, Python patch version and platform (documented). A 0.7.0 tool refuses 1.0.0 releases, so the upgrade uses the new release's own tool (documented).


### M8-25

**IPv6 is enabled by default although per-client limits are keyed per /128 and the guide says to keep it off** (LOW, gate F)

*Work package `m8/deploy`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** ATLAS_DEPLOY_IPV6 defaulted to true (parser default and deploy.env.example), although every per-client limit is keyed on $binary_remote_addr, so one IPv6 client holding a /64 counts as many clients, and DEPLOYMENT.md says to keep IPv6 off.
- **Change.** siteconfig: `DeployConfig.ipv6` defaults to False and the parser default is 'false', with a comment explaining why. deploy.env.example now shows `# ATLAS_DEPLOY_IPV6=false` with the reason, and the domain comment says to publish an AAAA record only with IPv6 on. The docs (sections 6, 9 and 17) state the new default.
- **Tests.** tests/test_m6_config.py::test_the_default_is_the_private_tunnel_profile; tests/test_m6_config.py::test_the_example_file_is_valid; tests/test_m6_render.py::test_nginx_listens_on_ipv6_only_when_asked
- **Before and after.** 8f077ed (both reviewers): `assert True is False` x2, and 'listen [::]' is present without the key. All pass at a8a77a6.
- **Residual.** A deploy.env that relied on the old default loses its IPv6 listeners at the next activation. No deployment exists.


### M8-26

**`atlas-deploy render --apply` installs a site for a non-current release without the lock, a history record or verification** (LOW, gate G)

*Work package `m8/deploy`: fixed.* Reviews: verify accept, skeptic needs change; a fix-up round addressed the requested changes.

- **Defect.** `atlas-deploy render --apply --release-dir <B>` published B's dashboard and wrote B's site (dashboard root and route allow-list), units and atlas.env while current was A. It took no deployment lock, wrote no history record and ran no verification, so the next nginx reload put B's browser assets and routes in front of A's API.
- **Change.** cli: new `_applied_release()`, called under `Deployment.locked()`. It refuses while an activation is pending ('run atlas-deploy recover first'), refuses a directory that is not an installed release (a checkout is still accepted in a rehearsal), and refuses an installed release that is not current, naming `atlas-deploy activate <id>`. After writing, it appends a `render`/`applied` record (release, stage, changed) to history.jsonl. `_publish_for_site` no longer repeats the installed check. On a real server this also refuses a checkout for every stage and profile; that is a deliberate tightening. The documented ACME step uses the current release's tool and is unaffected. Fix-up: cli.py `_applied_release(ctx, deployment, release_dir, stage)` runs under `Deployment.locked()`. It refuses a pending activation, a directory that is not an installed release (a checkout is accepted only in a rehearsal) and a non-current release (naming `atlas-deploy activate <id>`). New: while no release is current, it accepts the acme stage of an installed release. It returns the resolved directory, and `_render` builds the Renderer and files from it inside the lock, so one release is checked, rendered, published, written and recorded. The complete site before the first activation is refused with 'no release is active ... --stage acme ... activate <id> (in an exposed profile, once the ACME site has obtained the certificate: docs/DEPLOYMENT.md section 7)'. The argparse help is updated. DEPLOYMENT.md section 6 and a new section 9 row are updated.
- **Tests.** tests/test_m7_deploy.py::test_render_apply_installs_the_acme_site_before_the_first_activation (new). With no current release: acme --apply exits 0 and writes the ACME-only site (no dashboard root, no proxy_pass), publishes nothing and records render/acme. tls --apply exits 2 with the new message and changes nothing. Once a release is current, acme for another release is refused and for the current one accepted.; tests/test_m7_deploy.py::test_render_apply_installs_the_current_release_only (unchanged); tests/test_m7_deploy.py::render_apply helper takes a stage
- **Before and after.** 8f077ed sources: the new ACME test fails with `FileNotFoundError: .../history.jsonl` (no lock or record), and test_render_apply_installs_the_current_release_only fails with `assert 0 == 2`. 8994c45 cli.py: the ACME test fails with `assert 2 == 0` (acme refused before the first activation, as the skeptic's probe showed). Both pass at a8a77a6.
- **Residual.** render --apply still reloads nothing by itself; it prints the commands. Rendering inside the lock has no dedicated race test; the reviewer called the race practically unreachable, and existing tests cover the behaviour, including --release-dir through /opt/atlas/current.


### M8-27

**Simulator error-summary links fail WCAG 2.2 target size (2.5.8) with two or more field errors** (LOW, gate I)

*Work package `m8/web`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** When the simulator shows two or more client-side field errors at desktop width, each error-summary link is a 16 px target on a 20 px pitch. That fails WCAG 2.2 SC 2.5.8 (axe target-size). The committed axe test only scanned a server-side failed run.
- **Change.** The error summary list gets the class 'error-summary'. The CSS makes its links display:inline-block with min-height 24px and 2px block padding. A new Playwright test fills two invalid fields (horizon 'abc', multiplier '9'), runs axe's WCAG 2.0-2.2 A/AA rules, and checks that each link's bounding box is at least 24 px high. DASHBOARD.md sections 8 and 9 are updated.
- **Tests.** web/e2e/a11y.spec.ts 'the error summary of several invalid fields has no WCAG 2.2 AA violation' (project api)
- **Before and after.** Without the CSS rule the test fails with 'Expected: >= 24, Received: 16'; the verify reviewer reproduced this. It passes on the branch, and it passed again in my api+mobile run (55 passed).
- **Residual.** Only Chromium was checked, and only at desktop width. The phone layout was already compliant.


### M8-28

**Legacy-window backtest misdescribed on the Forecasting page (targets past the data; ranking lacks its descriptive-only caveat)** (LOW, gate H)

*Work package `m8/web`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** The Forecasting page's Reproducibility table showed the legacy window's target days as 'Mar 4, 2026 – Jul 29, 2026'. That is the API's nominal last origin + 30, past the snapshot's last day 2026-06-30, so no day after it was scored. The 'Models against baselines' ranking could show legacy-window MAE without its 'descriptive only; horizons may be incomplete' caveat.
- **Change.** The Reproducibility panel reads snapshot_last_date from the history answer, which other panels on the page already load. lastScored() cuts each period's last target at that date. targetsNote() adds a caption naming the cut periods: 'Target days are the days scored, up to the snapshot's last day (Jun 30, 2026): the last origins of the legacy window reach past it, so their later horizons are incomplete.' If the snapshot date has not loaded, the caption says the target days are nominal. The legacy-window callout is now one LegacyWindowCallout component, shown in both the horizon panel and the ranking panel when that period is selected. No API or result changes.
- **Tests.** web/src/views/m8-audit.test.tsx 'M8-28: ... shows the target days that were scored, never past the end of the snapshot'; web/src/views/m8-audit.test.tsx 'M8-28: ... ranks the models in the legacy window with its descriptive-only caveat'
- **Before and after.** Both tests fail on the 8f077ed sources ('Jul 29' in the row; no caveat in the ranking panel) and pass on the branch. Both reviewers reproduced this.
- **Residual.** The API still serves the nominal last_target. A note in docs/API.md is handed off.


### M8-29

**Time-zone safety of dashboard dates is untested (tests only run in UTC)** (LOW, gate K)

*Work package `m8/web`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** The 'formats calendar dates in UTC, whatever the time zone' test ran only in the process time zone. CI is UTC, so removing the UTC formatting, or parsing dates in local time, left every test green. No Playwright test set timezoneId.
- **Change.** vite.config.ts sets test.env TZ 'America/Los_Angeles', so every Vitest run uses it, including CI's 'npm run test:coverage'. format.test.ts gains a guard that the zone is set (process.env.TZ and the offset 420). It also reloads the date module (vi.resetModules) in Pacific/Pago_Pago, America/Los_Angeles and Pacific/Kiritimati and checks formatDate, formatWeekdayDate ('Fri, Jul 3, 2026'), formatShortDate, formatMonthYear, dateValue, isoDay and addDays. The Playwright project api gains two tests, in Pacific/Kiritimati and America/Los_Angeles, on the Forecasting page. Each checks the browser's zone, the tags 'Observed to Jun 30, 2026' and 'Projected from Jun 30, 2026', the callout 'covers Jul 1, 2026 to Jul 30, 2026.', and in the table: Tue Jun 30 observed, Wed Jul 1 projected, Fri Jul 3 Independence Day (not on Jul 2 or Jul 4).
- **Tests.** web/src/lib/format.test.ts (guard test plus 3 per-zone tests); web/e2e/dashboard.spec.ts 'in the Pacific/Kiritimati time zone ...' and 'in the America/Los_Angeles time zone ...'
- **Before and after.** Mutation A removes the UTC formatting; mutation B makes dateValue parse in local time. On the 8f077ed tree both mutations pass the old 6 format tests. On the branch each mutation fails 3 Vitest tests. In Playwright, A fails the Los Angeles test and B fails the Kiritimati test. Both reviewers reproduced this. Both zone tests passed again in my api run.
- **Residual.** The east-of-UTC Vitest coverage relies on Node honouring a runtime change of process.env.TZ. A guard assertion checks that it does.


### M8-30

**Vitest view tests are timing-flaky under CPU load (1 s default async waits)** (LOW, gate K)

*Work package `m8/web`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** Most findBy*/waitFor queries in the view tests used Testing Library's default 1 s asyncUtilTimeout. On a loaded machine that is shorter than a lazy page import plus render, so tests failed from run to run.
- **Change.** src/test/setup.ts calls configure({ asyncUtilTimeout: 5000 }), with a comment. The per-test timeout stays at 20 s; the vite.config.ts comment now points to the setup file.
- **Tests.** web/src/views/m8-audit.test.tsx 'M8-30: ... allow a page 5 s to render ...'
- **Before and after.** The test fails on the 8f077ed setup (1000 !== 5000) and passes on the branch. The full suite passes 191/191.
- **Residual.** Under extreme oversubscription a 5 s wait reduces failures but does not remove them (the audit's verifier note). I did not repeat the load experiment.


### M8-31

**Dashboard 'Methods' links point at the mutable main branch, not the release** (LOW, gate L)

*Work package `m8/web`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** The Methods & provenance links pointed at GitHub's moving main branch, while the same page shows one release's fingerprints. Nothing said they might not match the release.
- **Change.** The links are labelled honestly rather than pinned. Each link reads '<DOC> on the main branch (opens GitHub)'. A paragraph says the links open the current documentation on main, which may have changed since this release; that this release's documentation is in the docs/ directory of the release (or checkout) serving the dashboard; and that a server release records its commit in release.json. Why not pin: web/dist is committed, so a build cannot embed the commit it is part of. A v${version} tag link would 404 throughout the candidate stage, because the only planned candidate tag is v1.0.0-rc.1, created by the owner after approval. A source comment and DASHBOARD.md sections 3, 14 and 17 explain this.
- **Tests.** web/src/views/m8-audit.test.tsx 'M8-31: the Methods links say they open the current documentation on main, and where the release has its own'
- **Before and after.** The test fails on 8f077ed (accessible name 'BACKTESTING.md (opens GitHub)', and no explanatory text) and passes on the branch.
- **Residual.** The links still drift as main moves, but they now say so. They can be pinned once tags exist.


### M8-32

**Pinned GitHub Actions target the deprecated Node.js 20 runtime (CI warning on every job)** (LOW, gate K)

*Work package `m8/ci`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** All four SHA-pinned actions declared runs.using: node20 (checkout v4.4.0, setup-python v5.6.0, setup-node v4.4.0, upload-artifact v4.6.2). Every CI job therefore warned that they were being forced onto Node.js 24.
- **Change.** Re-pinned each action to its newest node24 release published on or before 2026-10-02. Tag SHAs came from `git ls-remote --tags`; each commit was fetched shallowly into a scratch repo to read runs.using and the commit date (all tags are lightweight). checkout v7.0.1 3d3c42e5aac5ba805825da76410c181273ba90b1 (2026-07-17, node24). setup-python v7.0.0 5fda3b95a4ea91299a34e894583c3862153e4b97 (2026-07-19, node24). setup-node v7.0.0 820762786026740c76f36085b0efc47a31fe5020 (2026-07-13, node24). upload-artifact v7.0.1 043fb46d1a93c77aae656e7c1c64a875d1fc6a0a (2026-04-10, node24). Excluded as too new: setup-node v7.1.0 and upload-artifact v7.0.2, both 2026-10-07. Breaking changes, checked against ci.yml: checkout v6 persists credentials to a separate file and v7 blocks fork checkouts under pull_request_target or workflow_run; CI uses persist-credentials: false and neither event. setup-node v5/v6 auto-cache and v6 drops always-auth; CI sets cache: npm explicitly and never used always-auth. setup-python only adds an optional pip-version input. upload-artifact v7 adds an optional archive input that defaults to true. No inputs needed changing. Added two pin tests to tests/test_m8_release.py and named the pinned releases in DEVELOPMENT.md. Fix-up: The pins are unchanged from e62f37f: checkout v7.0.1 3d3c42e5, setup-python v7.0.0 5fda3b95, setup-node v7.0.0 82076278, upload-artifact v7.0.1 043fb46d, all node24. Review fixes (nits):
- test_every_action_is_pinned_to_a_commit_with_its_version gains a docstring. It says the SHA-to-release link cannot be checked offline and is checked by the update procedure.
- A new or dropped action now fails with a message naming FIRST_NODE24_MAJOR, and a mixed release fails with 'every workflow must pin the same release of an action'.
- test_no_action_runs_on_the_deprecated_node20_runtime says it judges the version comment. An action missing from the table counts as old instead of raising KeyError.
- DEVELOPMENT.md: an action is dated by its GitHub release publication date. The release commit's date is used only when no release exists, and the date used is recorded. It also discloses that the current pins were checked against their release-commit dates (2026-04-10 to 2026-07-19, all more than ten weeks before 2026-10-02), because the Releases pages were not reachable. The owner confirms the publication dates there.
- **Tests.** tests/test_m8_release.py::test_every_action_is_pinned_to_a_commit_with_its_version; tests/test_m8_release.py::test_no_action_runs_on_the_deprecated_node20_runtime
- **Before and after.** With 8f077ed's ci.yml in the working tree, both pin tests fail: 'every workflow must pin the same release of an action; assert 7 == 4' and 'Left contains 8 more items, first extra item: (ci.yml, actions/checkout, v4.4.0)'. Both pass on the branch. A temporary workflow using actions/cache made the first test fail with 'add each new action to FIRST_NODE24_MAJOR, with the first major release whose action.yml runs on Node.js 24'; the file was removed afterwards. git ls-remote confirms that checkout v7.0.1 and setup-python v7.0.0 are the newest tags of their actions.
- **Residual.** Release publication dates could not be read here. github.com/actions/*/releases pages, the releases.atom feed and api.github.com all return 403 through the proxy, and the GitHub MCP is limited to arthur-fay/demand-forecasting-model. The pins therefore remain dated by commit. The owner should confirm the dates on the Releases pages; the documentation says so. The workflows have not run on GitHub-hosted runners.


### M8-33

**The two-week dependency cooldown is described as enforced but is a manual rule; its caps never expire** (LOW, gate M)

*Work package `m8/docs`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** ROADMAP said that, since M4, new packages are held to two-week-old releases through tool.uv.constraint-dependencies. The setting only caps three named packages; the cooldown is a manual review rule.
- **Change.** The ROADMAP known-limitations entry now says the two-week cooldown is a review rule, not enforced by a tool. constraint-dependencies only caps optype, starlette and truststore, each held until its review date; it holds back no other package. It points to DEVELOPMENT.md and marks the old wording as corrected.
- **Before and after.** As in the branch report.
- **Residual.** Its accuracy depends on DEVELOPMENT.md (CI work package) giving the review dates before merge.

*Work package `m8/ci`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** The two-week cooldown was described as enforced by tool.uv.constraint-dependencies. In fact that setting only caps three packages, the caps never expire, and the rule is a manual review.
- **Change.** pyproject.toml [tool.uv] comment (caps and lock unchanged) now states that the cooldown is a review rule. It gives each cap's PyPI publication date and review date (the day the release turns two weeks old): optype 0.19.0 published 2026-10-02T23:14Z, review 2026-10-16. truststore 0.10.5 published 2026-10-12T22:27Z, review 2026-10-26. starlette 1.8.0 published 2026-10-13T07:54Z, review 2026-10-27. The comment also says the owner removes a cap, runs `uv lock --upgrade-package` and the full checks, or records why it stays. DEVELOPMENT.md: the Install paragraph now calls the cooldown a review rule, and a new 'Dependency maintenance' section covers the cooldown for Python, npm and Actions, a caps table with review dates, who lifts caps (the owner), re-audit cadence, security fixes inside the cooldown, and why exclude-newer is not adopted (it would downgrade eight M0-era packages per the audit). Fix-up: The pyproject comment and the caps table are unchanged from 6ef0e6c (review dates: optype 2026-10-16, truststore 2026-10-26, starlette 2026-10-27; caps and lock unchanged). This round only refines the policy text in DEVELOPMENT.md 'Dependency maintenance': the cooldown date for actions is the release publication date, numeric-package upgrades need reproduce-research.yml, and the 60-day wording is qualified.
- **Before and after.** `uv lock --check --offline` resolves 44 packages and `uv sync --locked` passes. uv.lock and pyproject.toml are not changed in this round.
- **Residual.** Caps do not lapse on their own; the owner lifts them. The optype review date is today, 2026-10-16.


### M8-34

**'Decision-intelligence' marketing language in package metadata and the dashboard heading** (LOW, gate L)

*Work package `m8/web`: partially fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** The Overview H1 was 'Decision-intelligence research on NYC 311 demand', a commercial term that overstates an exploratory research demonstration.
- **Change.** The H1 is now 'NYC 311 demand: forecasts, uncertainty and hypothetical scenarios'. All literals in App.test.tsx, round2-shell.test.tsx, e2e/helpers.ts, failures.spec.ts and keyboard.spec.ts are updated. index.html has no such wording. This is the dashboard part only; pyproject.toml:4 is outside my files (handoff).
- **Tests.** web/src/views/m8-audit.test.tsx 'M8-34: the Overview heading describes the research without marketing language'
- **Before and after.** The test fails on 8f077ed and passes on the branch.
- **Residual.** pyproject.toml:4 still says 'decision-intelligence engine' (handoff).

*Work package `m8/docs`: not reproduced.* Reviews: verify accept, skeptic accept.

- **Defect.** 'Decision-intelligence' marketing wording appears in package metadata and the dashboard heading.
- **Change.** Docs part: `grep -rn -i 'decision-intelligence\|decision intelligence' README.md docs/*.md` finds no occurrence in any document I own, before or after; the rewritten README does not introduce it. No change was needed.
- **Before and after.** grep -rn -i 'decision.intelligence' README.md docs/*.md is empty on 8f077ed and on d12f6b2.
- **Residual.** pyproject.toml:4 and the web/ heading belong to other work packages.

*Work package `m8/ci`: partially fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** The pyproject description called ATLAS a 'decision-intelligence engine', a marketing term that overstates what an exploratory research demonstration with a HYPOTHETICAL LP simulation does.
- **Change.** New description: 'ATLAS: a research demonstration of reproducible NYC 311 daily demand forecasting, uncertainty assessment (its prediction intervals failed calibration) and a HYPOTHETICAL decision simulation, with an HTTP API, a web dashboard and a deployment package.' Only the pyproject part is owned here.
- **Before and after.** Before: 'decision-intelligence engine' on pyproject.toml line 4. After: the neutral description, with uv.lock unchanged (`uv lock --check --offline` passes).
- **Residual.** The dashboard H1 at web/src/views/Overview.tsx:381 and its test and e2e literals still say 'Decision-intelligence research'. They belong to the web package (handoff).


### M8-35

**A pre-release version string (1.0.0rc1 / 1.0.0-rc.1) is refused by three validators; no versioning policy** (LOW, gate G)

*Work package `m8/ci`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** Three validators (build-release.sh, the release id regex, the dashboard manifest) accept only MAJOR.MINOR.PATCH, but no versioning or release-candidate policy was documented, and the PEP 440 and npm pre-release spellings differ.
- **Change.** Documented canonical wording C in DEVELOPMENT.md 'Versioning'. The code carries the version being released (1.0.0 for a candidate of 1.0.0). Candidate status is carried by the RC manifest and by a tag v1.0.0-rc.1 that only the owner creates after approval. The validators keep MAJOR.MINOR.PATCH. Tests require every declaration to be MAJOR.MINOR.PATCH and valid_release_id(version + '-0123456789ab'), and show that candidate labels (1.0.0-rc.1, 1.0.0rc1, v1.0.0-rc.1) are not valid versions or release ids. No validator was changed.
- **Tests.** tests/test_m8_release.py::test_every_version_declaration_is_major_minor_patch; tests/test_m8_release.py::test_a_release_candidate_label_cannot_be_the_version
- **Before and after.** Before: no policy. After: __version__ = '1.0.0rc1' fails test_every_version_declaration_is_major_minor_patch (implementer's and reviewers' mutation checks). The tests pass at 0.7.0 in this round.
- **Residual.** The policy depends on the owner creating the tag. The RC manifest is written by another package.


### M8-36

**No test ties the version declarations together** (LOW, gate K)

*Work package `m8/ci`: fixed.* Reviews: verify needs change, skeptic needs change; a fix-up round addressed the requested changes.

- **Defect.** The version is declared independently in pyproject.toml, src/atlas/__init__.py and web/package.json, with copies in the locks, web/dist and the OpenAPI contract. No fast test tied pyproject to __init__, and nothing tied the dashboard to the backend.
- **Change.** New tests/test_m8_release.py. It requires eight declarations to be equal: pyproject [project].version, atlas.__version__, the uv.lock atlas entry, web/package.json, web/package-lock.json root version and packages[''].version, web/dist/atlas-dashboard.json and openapi info.version. DEVELOPMENT.md 'Versioning' lists every file a version change touches and the commands to update them. Fix-up: The tests are unchanged from 74fac06 (eight declarations must be equal). Review fix (minor): DEVELOPMENT.md 'Versioning' is now a numbered procedure.
1. Edit `version` in pyproject.toml and `__version__` in src/atlas/__init__.py, then run `uv lock`.
2. In web/, run `npm version <version> --no-git-tag-version`, which sets package.json and both lock fields. Do not edit package.json by hand first: npm then stops with 'Version not changed' and leaves the lock at the old version.
3. Regenerate openapi.json and the dashboard fixtures.
4. Rebuild and commit web/dist, and update the tests and documents that quote the version.
The handoff to the lead is corrected the same way.
- **Tests.** tests/test_m8_release.py::test_every_version_declaration_is_the_same; tests/test_m8_release.py::test_every_version_declaration_is_major_minor_patch
- **Before and after.** The old procedure fails. On scratch copies of web/package.json and package-lock.json with npm 10.9.7, after a hand edit to 1.0.0, `npm version 1.0.0 --no-git-tag-version` gives 'npm error Version not changed', exit 1, and leaves the lock at 0.7.0 0.7.0. Without the hand edit (the new procedure), it exits 0 and gives package.json 1.0.0 and lock 1.0.0 1.0.0. The version tests pass at 0.7.0. The mutation evidence for the tests is in the implementer's and reviewers' reports.
- **Residual.** tests/test_m5_endpoints.py:51 and docs/DEPLOYMENT.md:739 hard-code 0.7.0. Both are outside this package (handoff to the lead).


### M8-37

**The container e2e 'explicit rollback' targets the release that is already current** (LOW, gate K)

*Work package `m8/deploy`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** The container e2e 'explicit rollback' ran `rollback --to <release>` while that release was already current. It re-activated the current release and never exercised a switch between two healthy releases on real systemd and nginx.
- **Change.** Only tests/test_m6_container.py changed, following its existing helpers. `_snapshot_commit` now returns (commit, second, broken). In the clone it makes an empty commit 'second release' on top of the deployed commit (same tree, new release id), pushes it as refs/heads/second, and the broken commit follows on top. Server gains `second` and `second_release` (with defaults), and the fixture passes `second=second`. test_an_explicit_rollback builds the second release with build-release.sh, activates it, and asserts current and the site's dashboard root point to it. It then runs `atlas-deploy rollback` without --to and asserts that current, status.current and status.last_verified are the first release, that the site's root is /opt/atlas/dashboard/<first> and no longer names the second, that the published copy's index.html sha256 equals the release's web/dist/index.html, and that `atlas-deploy verify` passes. test_the_dashboard_follows_the_release also asserts that the second release is not in the site. The rollback target is the first release because history has the broken release's rolled_back record (restored = first) before the second's activation. The module docstring was updated.
- **Tests.** tests/test_m6_container.py::test_an_explicit_rollback (rewritten in dd443e0); tests/test_m6_container.py::test_the_dashboard_follows_the_release (one assertion added)
- **Before and after.** Not run (needs Docker; the lead runs it). `pytest --collect-only` collects 22 tests. Both reviewers accepted it after reading the code.
- **Residual.** Adds one release build to the e2e job; the skeptic measured 4.5 min of the 60-min limit on 194fa4a. Because of the M8-02 change, the later interrupted-activation case now restarts the broken release once more inside recover before rolling back; the expected output is unchanged.


### M8-38

**Reproduction of the committed M2/M3 reference results is not automated, and the docs do not say so** (LOW, gate J)

*Work package `m8/ci`: fixed.* Reviews: verify accept, skeptic needs change; a fix-up round addressed the requested changes.

- **Defect.** CI re-verified only the smoke runs and the M4 reference run. Nothing re-ran the committed M2/M3 reference runs or regenerated their projections, and DEVELOPMENT.md did not disclose the gap.
- **Change.** New .github/workflows/reproduce-research.yml: workflow_dispatch only, contents: read, pinned actions, fetch-depth 0, 120-minute timeout per job, one fail-fast:false matrix job per committed run. A first step logs the CPU and warns when AVX-512 is missing. Jobs: `atlas backtest verify` on M2 full and on M2 delay1/development. The sealed M2 development record is verified with its own code: `git archive a16672f | tar -x`, a copy of the record, `uv sync --locked`, then verify there. `atlas uncertainty verify` on M3 selection and full. `atlas optimize verify` and `inspect` on M4. The M2 projection, M3 projection and legacy holdout are regenerated and compared through both manifests by the new .github/scripts/same_outputs.py (same record_type, same output SHA-256s, committed files still match). DEVELOPMENT.md gains 'Reproducing the committed reference results', covering the gap, the workflow, the commands, the measured times and fingerprints, and the cross-CPU caveat. Fix-up: The workflow is unchanged from ce5be7e: dispatch-only, contents: read, one job per committed run, and a16672f's code for the sealed M2 development run. Review fixes:
- (minor) The CPU step no longer calls numpy.show_runtime(), which printed only SIMD flags and a 'pip install threadpoolctl' warning. It logs the CPU, AVX-512 and the OpenBLAS core, read with `OPENBLAS_VERBOSE=2 uv run python -c 'import numpy'` from stderr (here 'Core: SkylakeX'), to the log and to $GITHUB_STEP_SUMMARY.
- When the core is not SkylakeX, it emits a ::warning:: that a failure is inconclusive and does not count as the release's reproduction record. The m4 job is exempt because its result does not depend on the kernels.
- DEVELOPMENT.md: a pass is conclusive on any runner. A failure is conclusive only with the SkylakeX core, or in the M4 job; elsewhere, re-run the job or use a SkylakeX machine. Record the date, commit, CPU, core and outcome with the release. Verify reports only which hashes differ, because forecasts.csv is not committed.
- (nit) The header names the a16672f exception, and `atlas optimize inspect` is added to the by-hand list.
- (nit) same_outputs.py now also hashes every regenerated output against its own manifest; a missing file or a mismatch fails.
- **Tests.** tests/test_m8_release.py::test_same_outputs_refuses_a_regenerated_file_that_its_manifest_does_not_describe (new); tests/test_m8_release.py::test_same_outputs_accepts_an_identical_regeneration; tests/test_m8_release.py::test_same_outputs_refuses_a_different_output_hash; tests/test_m8_release.py::test_same_outputs_refuses_an_altered_committed_file
- **Before and after.** same_outputs.py:
- The new test fails on 1ab8393's script: the regenerated CSV, rewritten with CRLF while its manifest stays unchanged, gives '2 recorded output(s); PASS'. It passes after the fix (4 of 4 same_outputs tests).

Local replay of the extracted CPU and regenerate steps (bash -e, and bash -eo pipefail):
- SkylakeX kernels: m2-projection 5 of 5 identical, PASS (3 s); legacy-holdout 2 of 2 identical, PASS (3 s); m4 verify and inspect PASS, fingerprint 0dc4eb0f...3c82 (26 s). No warning.
- OPENBLAS_CORETYPE=Haswell: the step logs 'OpenBLAS core: Haswell'.
  - m2-projection emits the warning and fails with 3 of 5 files different (coefficients max relative 6.8e-14, projections 3.8e-15, fits 1.8e-15).
  - legacy-holdout differs in both files (predictions max relative 7.2e-12).
  - m4 emits no warning and passes, with the same fingerprint.

The new step's job summary renders as a one-row table.
- **Residual.** The workflow has not been dispatched on GitHub. GitHub-hosted runners without the SkylakeX core will fail the regression jobs inconclusively; the workflow warns and the documentation explains it. I did not check whether other AVX-512 cores (for example Cooperlake on Zen 4) give byte-identical results, so the rule is conservative and accepts only SkylakeX. The full M2 and M3 verify runs were not rerun in this round; the implementer's and reviewers' runs at 8f077ed and 1ab8393 stand, and this round changed no code they execute.


### M8-39

**spot_check_intervals.csv is a lossy (1-2 ULP) copy with no documented provenance or generator** (LOW, gate L)

*Work package `m8/docs`: fixed.* Reviews: verify accept, skeptic accept.

- **Defect.** docs/results/atlas-m3-intervals-v1/full/spot_check_intervals.csv is a lossy (1-2 ULP) copy of 600 reference rows. No document describes it, no generator exists and no hash pins it.
- **Change.** A marked note in UNCERTAINTY.md section 9 says what the file is. It holds 600 rows: the first and last origins of each period (2024-08-01, 2025-05-31, 2025-06-30, 2026-05-31), horizons 1-30, and five model/pair combinations. It is not in the manifest, no hash pins it, and test_m3_results checks only eligibility and ordering. Its bytes equal atlas.experiment.csv_bytes applied to those rows read with pandas' default float parser (verified True). Against the regenerated reference, 1,149 float fields differ by at most 2 ULP (2.35e-16 relative) and 94 of 600 lines are verbatim; all other fields are identical. docs/audit/scripts/m8_doc_figures.py reproduces the comparison. Fix-up: Now reads 'at most 2.35e-16 relative', as recorded in m8_doc_figures_output.md line 24.
- **Before and after.** Before: '2.3e-16'. After: '2.35e-16'.
- **Residual.** The file stays lossy and unpinned (lead decision, handoff).

## 7. Final independent review of the candidate

After the corrections were integrated, two rounds of independent review examined the
candidate (`e79e66d` and `537563c`). Six reviewer roles took part:

- science;
- release assessment;
- security;
- infrastructure and deployment;
- frontend;
- release engineering.

Every finding was reproduced by a separate verifier, and every MEDIUM finding was also
challenged by a skeptic. None was CRITICAL or HIGH. Overlapping findings are grouped below,
and each row names the reviews that raised it. The code corrections were made the same way as
the first round: work packages in isolated worktrees, each reviewed by a verification
reviewer and a skeptic, with a fix-up round. The packages were:

- `m8/final-rcmanifest`;
- `m8/final-dmdependence`;
- `m8/final-deploy`;
- `m8/final-web`.

The lead corrected the documents.

| Finding | Severity (finder; verifiers) | Resolution | Regression test or check |
|---|---|---|---|
| The release-candidate manifest tool hashed working-tree bytes and built paths with the platform's separator, so `--check` failed on an unmodified Windows (`core.autocrlf=true`) checkout (R2-release#1, R2-infra#3, R1-assessment#3) | MEDIUM; MEDIUM, MEDIUM | `docs/release/rc_manifest.py` fingerprints the committed content through git (`git ls-files -s`, `git cat-file --batch`), with git's own paths and the file modes, and requires a clean checkout | `tests/test_m8_rc_manifest.py::test_a_crlf_checkout_matches_the_manifest` (fails with the `537563c` tool: "MISMATCH in: locks, source") |
| The manifest tool's blind spots: untracked files read from disk, modes not fingerprinted, unnamed mismatches, status fields written as literals, no test, a traceback outside git (R2-release#3) | LOW; LOW | every section reads committed blobs; an unclean checkout is refused with each path named; per-file map with named differences; status read from the committed results, declared policy under `declared`; one-line errors | `tests/test_m8_rc_manifest.py` (7 tests; 13 of 13 single mutations of the tool caught) |
| The `.gitattributes` rules for `web/dist`, `web/public` and the dashboard fixtures were untested (R2-release#2) | LOW; LOW | the line-ending tests cover them | `tests/test_snapshot.py::test_results_configs_and_contract_are_protected_from_line_ending_conversion`, `::test_a_crlf_checkout_writes_every_byte_verified_file_unchanged` (removing any of the three rules makes the attribute test fail; the export test fails for `web/dist` and `web/public`, while the fixtures are single-line JSON, which conversion leaves unchanged) |
| The erratum's "H2 remains significant after the size correction and Holm (0.045)" rests on the MA(29) null; the confirmation differentials are autocorrelated beyond lag 29 (R1-science#1) | MEDIUM; MEDIUM, LOW | a committed post hoc study, [scripts/dm_dependence_sensitivity.py](scripts/dm_dependence_sensitivity.py), with its [output](scripts/dm_dependence_sensitivity_output.md) and the differentials it uses: H2's Holm p is 0.068-0.155 under the six nulls with dependence of 45 days or more, so its significance is not robust; H1 is not significant under any null; the selection gate fails under MA(29) and every block bootstrap. BACKTESTING section 12, finding 3, the model card, README, known limitations and the validation report say so | `tests/test_m8_dm_dependence.py` (the committed differentials reproduce the committed statistics; the recorded output is the script's; BACKTESTING quotes it); `ATLAS_M8_FULL_STUDY=1` reruns the whole study |
| The licence and data-terms condition, the runbook, README, RC checklist and data card assumed the repository was not yet public; it has been public since 2026-07-18 (R1-assessment#1, R2-infra#5) | MEDIUM; MEDIUM, LOW | the release gate's condition 3 now applies before the `v1.0.0-rc.1` tag: decide the licence (or record that none is granted) and confirm the NYC Open Data terms, or make the repository private; every document states the repository's actual visibility | documentation; GitHub reports the repository public |
| The served selection notice gave rough ranges (7-10 %, 0.06-0.09), and the release gate, README and notice understated the erratum's consequence (R1-science#3, R1-assessment#2) | LOW, MEDIUM; LOW, LOW | the notice gives the recorded figures (7.6-9.1 %, 0.069) and the consequence (a correctly sized test would most likely have kept the same-weekday median); gate B and README say the recommendation stands only under the pre-registered rule as computed; the contract description of `selection.significant` aligned | `tests/test_m5_endpoints.py::test_the_selection_test_is_served_with_its_size_caveat`, `::test_the_selection_caveat_quotes_the_recorded_size_study` |
| The dashboard's confirmation tests panel showed "significant at 0.05" and H2 "Yes" without the size caveat (R1-science#4) | LOW; LOW | the panel shows the API's notices, the selection caveat directly under the selection | `web/src/views/m8-audit.test.tsx` "M8 final review: the confirmation tests panel" |
| The licence notices were served with the HTML policy: a CSP violation on every visit and no line wrapping (up to 1,050 px of sideways scroll at 320 px) (R2-frontend#1) | LOW; LOW | a policy for text (`DASHBOARD_TEXT_CSP`) on the notices, in the API and nginx, recorded in `release.json` | `tests/test_m7_dashboard.py::test_plain_text_gets_the_policy_for_text_and_every_other_file_the_html_one`; `tests/test_m7_deploy.py::test_the_licence_notices_get_the_policy_for_text`; e2e footer-licence test (violations, console errors, overflow at 375 px) |
| A release's recorded dashboard policy was checked for characters only, and verification accepted any policy the record agreed with (R1-security#1) | LOW; LOW | a floor on header names and on the HTML and text policies, applied by the release check, rendering and verification | `tests/test_m7_deploy.py::test_a_recorded_policy_below_the_floor_is_refused` (30 cases), `::test_verification_refuses_a_permissive_policy_its_record_agrees_with`, `::test_every_release_so_far_meets_the_floor` |
| No browser test injected the rate-limited failure that the browser report claimed (R2-frontend#3) | LOW; LOW | `failures.spec.ts` injects a 429 with `Retry-After`, in Chromium, Firefox and WebKit | e2e "a simulation rate_limited is explained" (fails when the explanation is removed) |
| The Firefox/WebKit CI step also started both nginx stacks, which none of its tests use (R2-frontend#4) | LOW; LOW | `ATLAS_E2E_API_ONLY=1` starts the API alone; the CI step sets it | `tests/test_m8_release.py::test_the_other_engines_run_against_the_api_alone_in_ci` |
| Browser-coverage statements still said "Chromium only" (R1-science#6, R1-assessment#4, R2-frontend#2) | LOW; LOW | known limitations, DASHBOARD.md, RELEASE_CANDIDATE.md and DEVELOPMENT.md state what runs where | documentation |
| The model card described the M3 intervals as pooled by bucket over 365 days; the construction's finite-sample bias and the t(335) degrees of freedom were disclosed only in the audit report (R1-science#2, R1-assessment#5) | LOW; LOW | the model card describes the per-horizon construction (153-182 residuals in the selection period); both properties are disclosed in the model card and UNCERTAINTY.md section 11 | documentation |
| BACKTESTING section 10 said the flagged-target sensitivity removes 44 target days; 31 are in the backtest's target range (R1-science#5) | LOW; LOW | corrected; the figures script reproduces the count | [scripts/m8_doc_figures_output.md](scripts/m8_doc_figures_output.md) section 4 |
| Overstated readiness reasons, unqualified byte-for-byte claims and a pointer to a deferred list that did not exist (R1-assessment#6, R2-release#4) | LOW; LOW | the release gate counts 29 corrections with a regression test and 10 in documentation; every byte-for-byte claim names the CPU class and the own-commit condition; the known limitations point to section 4 | documentation |
| Baseline-only evidence in the post-correction column, and the container result without a committed record (R1-assessment#7) | LOW; LOW | baseline evidence marked; the measured runs recorded in section 1 | section 1 |
| The secret-scan scope (979 blobs) was the baseline's (R1-security#2) | LOW; LOW | re-run at `298d58b`, the candidate's code: 1,337 blobs reachable from every ref, no credential | the scan (section 1) |
| Runbook: the pre-bootstrap preflight returns UNSUITABLE on every fresh server without `--before-bootstrap`; wrong load threshold; missing steps (R2-infra#1) | LOW; LOW (partly) | `--before-bootstrap` before the bootstrap, the definitive preflight after it; load warns above 0.7 and fails above 1.5 × CPUs; the `atlas-deploy` link, ufw and certbot steps; gate N names what the container test does not exercise | documentation (the option and both verdicts were checked by the verifier) |
| "Rebuild the current release after a python3.12 update" re-verifies nothing (R2-infra#2) | LOW; LOW | the runbook and DEPLOYMENT.md give the build's own reproduction command, run as the service user | the command, run on the candidate: exit 0, 8 of 8 M4 files identical |
| DEPLOYMENT.md section 10 misdescribed how the 0.7.0 tool fails on a 1.0.0 release, and omitted the roll-forward and IPv6 consequences (R2-infra#4) | LOW; LOW | corrected | documentation |
| RELEASE_CANDIDATE.md's command list and owner checklist were incomplete; API.md showed version 0.6.0 (R2-release#5) | LOW; LOW | every CI check listed by job; the checklist adds the research reproduction and the dependency audit before `v1.0.0`; API.md shows 1.0.0 | `tests/test_m5_endpoints.py::test_live_and_ready` (the served version) |
| The measured-results sections were placeholders and the manifest did not exist (R2-release#6) | LOW; LOW | section 1 and the release gate's section 4 filled with the final runs; the manifest generated last | `python3 docs/release/rc_manifest.py --check` |

**What the final review supersedes in the preserved baseline documents.** The baseline
documents are kept as they were written, so two pieces of their M8-04 evidence are corrected
here instead:

- **Evidence (3)** in [M8_FINDINGS_REGISTER.md](M8_FINDINGS_REGISTER.md). Its block-bootstrap
  figures came from scratch scripts with 4,000 resamples. The committed study, with 20,000,
  gives, for blocks of 30, 60 and 90 days:

  | | 30 days | 60 days | 90 days |
  |---|---:|---:|---:|
  | selection | 0.128 | 0.115 | 0.083 |
  | H1 | 0.103 | 0.123 | 0.141 |
  | H2 | 0.025 | 0.048 | 0.077 |
  | H2 after Holm | 0.050 | 0.096 | 0.155 |

- **Evidence (4)** in the register, and the M2 claims in
  [M8_AUDIT_EVIDENCE.md](M8_AUDIT_EVIDENCE.md). These quote the H1 differential's
  autocorrelation as 0.37 at lag 29 and 0.27 at lag 90. Those are lag-pair Pearson
  correlations. The study and the HAC variance use the demeaned autocorrelation divided by T,
  which gives 0.291, 0.136, 0.087 and 0.100 at lags 29, 45, 60 and 90.
  - H1's mean autocorrelation over lags 30-120 (0.126) is more than MA(29) produces: 1.5 % of
    MA(29) series reach it.
  - H2's (0.042) is in the same direction but within the MA(29) range: 8.0 % reach it.

Two residuals recorded in section 6 change:

- **M8-04's residual** "the rounded figures are literals" no longer holds. The notice's figures
  are tied to the recorded size study by
  `tests/test_m5_endpoints.py::test_the_selection_caveat_quotes_the_recorded_size_study`.
- **M8-04's residual on the MA(29) assumption** is now quantified by the dependence study.
