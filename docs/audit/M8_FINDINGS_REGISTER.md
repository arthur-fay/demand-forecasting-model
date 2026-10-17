# M8 audit findings register (baseline, before any correction)

Generated from the verified audit records of commit `194fa4a` (main after the M7 merge). Nothing in this file was changed after the corrections began; the outcome of each finding is recorded separately in [M8_CORRECTIONS.md](M8_CORRECTIONS.md).

**How each finding was established.** One independent auditor per lens searched for defects and had to support every claim with its own evidence (commands, scripts, computed numbers, file:line citations). Every MEDIUM or higher finding was then given to two further independent agents: a *reproducer*, who re-ran the check from scratch, and a *skeptic*, whose job was to refute it (reachability, prior disclosure, inflated severity). LOW findings had one reproducer. A disagreement would have gone to a third agent; none was needed. The verifiers' calibrated severities and the lead auditor's reconciliation are shown with each finding.

Paths shown as `<audit-scratch>/...` or `<verifier-scratch>/...` are the auditors' scratch scripts. They are not part of the repository; each reproduction says what the script does, and the decisive checks were turned into regression tests during the fix phase.

| ID | Severity | Gate | Finding | Found by |
|---|---|---|---|---|
| [M8-01](#m8-01) | HIGH | L | README front page presents refuted legacy claims as current results | claims#1, data#1 |
| [M8-02](#m8-02) | MEDIUM | G | `atlas-deploy recover` records an interrupted activation as verified without reloading nginx | deploy#1 |
| [M8-03](#m8-03) | MEDIUM | G | Rollback verification and nginx rendering use the running tool's dashboard CSP/descriptor, not the restored release's | deploy#2 |
| [M8-04](#m8-04) | MEDIUM | B | The HLN-DM test is oversized at h=30 with T=336-486; the selection gate's significance and the bandwidth-robustness claims do not survive size correction | forecast#1 |
| [M8-05](#m8-05) | MEDIUM | M | Shipped dashboard bundle omits third-party licence notices (MIT/ISC) and the Inter OFL text | supplychain#1, claims#2 |
| [M8-06](#m8-06) | MEDIUM | M | No vulnerability scanning or update monitoring for the deployed Python runtime | supplychain#2 |
| [M8-07](#m8-07) | MEDIUM | J | Hash-verified research results and the API contract are not protected from CRLF conversion | release#1 |
| [M8-08](#m8-08) | MEDIUM | C | UNCERTAINTY.md §9.6 calls the flagged target days 'intake drops'; demand on all 18 was far above the forecast | uncertainty#1 |
| [M8-09](#m8-09) | MEDIUM | C | API, M4 report and OPTIMIZATION.md call the archived July 2026 projection a 'FUTURE PROJECTION' with no archived/not-live disclosure | api#1, claims#5, optimization#2 |
| [M8-10](#m8-10) | LOW | L | Anomaly flags miss most of the winter 2025-26 anomaly cluster; docs overstate robustness to anomalies | data#2 |
| [M8-11](#m8-11) | LOW | L | Stale status and stale statements after the M7 merge (ROADMAP, DASHBOARD, DATA_SNAPSHOT, README notice heading) | data#3, claims#3 |
| [M8-12](#m8-12) | LOW | J | The first documented M2 verify command fails with the current code | forecast#2 |
| [M8-13](#m8-13) | LOW | C | M3 conclusions rest on p-values not adjusted for multiplicity, one printed next to the disclosed M2 limitation | uncertainty#2 |
| [M8-14](#m8-14) | LOW | C | The coverage gate's 'conservative' p-value is the lenient choice for a calibration claim | uncertainty#3 |
| [M8-15](#m8-15) | LOW | C | Projection artefacts misdescribe the calibration window; projection CSV rows carry no status | uncertainty#4 |
| [M8-16](#m8-16) | LOW | C | `fixed365` is documented as a 365-day calibration but held 153-182 residuals in the selection period | uncertainty#5 |
| [M8-17](#m8-17) | LOW | C | Effective-sample-size wording is inconsistent and overstates the evidence in places | uncertainty#6, claims#6 |
| [M8-18](#m8-18) | LOW | H | The 'failed' calibration wording is hard-coded in the API and dashboard instead of derived from the served status | uncertainty#7 |
| [M8-19](#m8-19) | LOW | H | Certificate tolerance ratios are shown as a 'gap' and plans called an 'exact optimum' | optimization#1 |
| [M8-20](#m8-20) | LOW | A | Unverified run_id from the M2/M3 projection manifests is served as provenance | api#2 |
| [M8-21](#m8-21) | LOW | E | HEAD on versioned GETs returns 405 while the nginx allow-list passes HEAD | api#3 |
| [M8-22](#m8-22) | LOW | E | After a worker replacement the next simulation goes to the cold worker while a warm one is idle | api#4 |
| [M8-23](#m8-23) | LOW | F | /docs and /redoc (on by default) load floating CDN bundles without SRI or CSP on the API/dashboard origin | api#5, supplychain#3 |
| [M8-24](#m8-24) | LOW | J | release.json environment_fingerprint differs between two builds of the same commit | deploy#3, release#5 |
| [M8-25](#m8-25) | LOW | F | IPv6 is enabled by default although per-client limits are keyed per /128 and the guide says to keep it off | deploy#4 |
| [M8-26](#m8-26) | LOW | G | `atlas-deploy render --apply` installs a site for a non-current release without the lock, a history record or verification | deploy#5 |
| [M8-27](#m8-27) | LOW | I | Simulator error-summary links fail WCAG 2.2 target size (2.5.8) with two or more field errors | dashboard#1 |
| [M8-28](#m8-28) | LOW | H | Legacy-window backtest misdescribed on the Forecasting page (targets past the data; ranking lacks its descriptive-only caveat) | dashboard#2 |
| [M8-29](#m8-29) | LOW | K | Time-zone safety of dashboard dates is untested (tests only run in UTC) | dashboard#3 |
| [M8-30](#m8-30) | LOW | K | Vitest view tests are timing-flaky under CPU load (1 s default async waits) | dashboard#4, release#3 |
| [M8-31](#m8-31) | LOW | L | Dashboard 'Methods' links point at the mutable main branch, not the release | dashboard#5, claims#7 |
| [M8-32](#m8-32) | LOW | K | Pinned GitHub Actions target the deprecated Node.js 20 runtime (CI warning on every job) | supplychain#4 |
| [M8-33](#m8-33) | LOW | M | The two-week dependency cooldown is described as enforced but is a manual rule; its caps never expire | supplychain#5 |
| [M8-34](#m8-34) | LOW | L | 'Decision-intelligence' marketing language in package metadata and the dashboard heading | claims#4 |
| [M8-35](#m8-35) | LOW | G | A pre-release version string (1.0.0rc1 / 1.0.0-rc.1) is refused by three validators; no versioning policy | release#2 |
| [M8-36](#m8-36) | LOW | K | No test ties the version declarations together | release#4 |
| [M8-37](#m8-37) | LOW | K | The container e2e 'explicit rollback' targets the release that is already current | release#6 |
| [M8-38](#m8-38) | LOW | J | Reproduction of the committed M2/M3 reference results is not automated, and the docs do not say so | release#7 |
| [M8-39](#m8-39) | LOW | L | spot_check_intervals.csv is a lossy (1-2 ULP) copy with no documented provenance or generator | release#8 |

Gates: A data integrity, B forecasting validity, C uncertainty, D optimisation, E API, F security, G deployment package, H frontend, I accessibility, J reproducibility, K automated testing, L documentation and provenance, M dependencies and licensing, N VPS deployment readiness.


## M8-01

**README front page presents refuted legacy claims as current results**

- Severity: **HIGH** (gate L); fix complexity: small; alters published research results: no
- Found by: claims#1 (HIGH: README front page still presents legacy claims that ATLAS's own analysis refutes; the M0 notice qualifies only 2 of them); data#1 (MEDIUM: README still presents legacy headline claims that ATLAS's own analysis refutes, with only two of them qualified)
- Files: `README.md:1`, `README.md:108-119`, `README.md:110-116`, `README.md:13-16`, `README.md:156-158`, `README.md:158`, `README.md:170-174`, `README.md:3`, `README.md:66-72`, `README.md:76`, `README.md:86`, `README.md:89-106`, `docs/BACKTESTING.md:68-73`, `docs/BACKTESTING.md:925-929`, `docs/BACKTESTING.md:926-929`, `docs/DATA_SNAPSHOT.md:121-122`, `docs/LEGACY_LIMITATIONS.md:33-38`, `docs/LEGACY_LIMITATIONS.md:45-50`, `docs/LEGACY_LIMITATIONS.md:68-72`, `docs/LEGACY_LIMITATIONS.md:85-86`, `docs/LEGACY_LIMITATIONS.md:85-88`, `docs/ROADMAP.md:279-281`, `docs/ROADMAP.md:54-55`, `src/atlas/api/views.py:264`

**Description.** The README is unchanged below the notice since the original commit (apart from the notebook filename). The first things a reader sees are:
- the legacy title 'Demand & Workload Forecasting Model with Capacity-Risk Alerts' and the legacy intro (lines 1, 3);
- the legacy Power BI screenshots; the second one shows a 'Recommended Action' column ('Add capacity and prioritize high-impact requests');
- 'Executive KPIs' and 'Key Business Insights' with no per-claim qualifier.

The notice (lines 13-16) corrects only the 4.93 % MAPE and the 'planning capacity'. The body still asserts claims that ATLAS documents as wrong or forbidden:
1. 'Forecast accuracy improved by 40.10%' (line 111). LEGACY_LIMITATIONS.md:33-34 says it is a relative MAPE reduction, 'not a 40 % gain in accuracy'.
2. 'The model provides 13 days of advance warning' (line 115) and 'Advance Warning 13 days' (line 106). LEGACY_LIMITATIONS.md:85-88 calls this a horizon index, not a lead time, and notes that the report files postdate the warned date.
3. 'The linear regression model was selected because it produced the lowest holdout-period error' (line 158). BACKTESTING.md:68-73 says 'The legacy regression should not be used'.
4. 'Recommended actions include: High: Add capacity...' (lines 170-174) and 'Risk-level classification and recommended actions' (line 86). This contradicts the project-wide rule, linted on every API string, that nothing is a recommendation.
5. R² 0.5787 (line 156). ROADMAP.md:54-55 says test-set R² is not reported.
6. 'The next 30 days are expected to generate approximately 330,941 requests' (line 112). This is an archived July 2026 projection, and ATLAS's own projection for the same days is 333,968 (BACKTESTING.md:926).
7. 'workload' throughout. The API states 'Demand is the count of requests created (intake), not measured workload'.
8. 'NYPD ... 44.55%' (line 117). DATA_SNAPSHOT.md:121-122: a share of the five-borough subset only.

The README's M3 paragraph (lines 23-27) also says the intervals are 'calibrated' without stating their FAILED_CALIBRATION result. ROADMAP.md:279-281 records this README as an M0 deferral ('the claims themselves are not rewritten in M0'); it was never revisited.

**Evidence.** `git diff` between README.md at db4670335 and at HEAD: the only body changes are the notebook filename (2 lines). `cat -n README.md` gives the lines quoted. `atlas check` (scratch copy) prints each KPI with its corrected meaning, e.g. 'mape_improvement_pct = 40.1 [Relative reduction of MAPE ... not a gain in accuracy points]' and 'advance_warning_days = 13 [... a horizon index, not a validated lead time]'. The README shows none of these labels. pdfinfo: the legacy PDF was created 2026-07-18, after the 2026-07-13 'shortfall'. The Capacity_Risk_Details.png image linked at README.md:72 shows the 'Recommended Action' column.

**Reproduction.** cd . && git show db4670335dac76628e6f2cd7b1806b2cd536abfb:README.md | diff - README.md | grep -v '^> \|^>$'; sed -n '89,175p' README.md; uv run --frozen atlas check (or the scratch copy at scratchpad/audit/claims/tree with ATLAS_REPO_ROOT set); pdfinfo '06 Exports/Project2_Demand_Forecasting_Report.pdf'

**Consequence.** A hiring manager reading top-down meets the refuted headline claims (40 % accuracy gain, 13-day advance warning, recommended staffing actions, legacy model 'selected') before, or instead of, ATLAS's findings. This undermines the release's main selling point: scientific honesty. It also contradicts the API and dashboard, which forbid recommendations and label demand as intake.

**Recommended repair.** README prose only; no legacy artifact changes.
1. Retitle and rewrite the intro for ATLAS, e.g. '# ATLAS: reproducible NYC 311 demand-forecasting research'. Intro: 'A research demonstration rebuilt from a July 2026 portfolio project: rolling-origin backtests (the selected regression's 30-day MAE was 985 requests/day; its 11 % lead over the best simple baseline is not significant after Holm, p = 0.054), prediction intervals that FAILED their pre-registered calibration test, and a linear-programming simulation whose operational parameters are all HYPOTHETICAL. Forecasts are archived projections from data ending 2026-06-30.'
2. State FAILED_CALIBRATION in the M3 paragraph.
3. Move the legacy body under '## The original project (July 2026, preserved as published; several claims superseded, see LEGACY_LIMITATIONS.md)'.
4. Rename 'Executive KPIs' to 'Original KPIs (as published)' and add a 'What ATLAS found' column, or reword inline:
   - 'Forecast accuracy improved by 40.10%' -> 'MAPE fell from 8.23 % to 4.93 % (a 40.1 % relative reduction) on a one-step-ahead holdout with observed lags, not 30-day accuracy'.
   - 'The model provides 13 days of advance warning' -> 'The first forecast day above the demand percentile was 13 days after the data cutoff (a horizon index, not a validated lead time; this report was produced on 2026-07-18)'.
   - 'was selected because it produced the lowest holdout-period error' -> 'was chosen on one 90-day one-step holdout; ATLAS's rolling-origin backtest found it does not reliably beat simple baselines and should not be used'.
   - 'Recommended actions include' -> 'The original dashboard attached suggested actions to each tier (no capacity data supports them; ATLAS makes no recommendations)'.
   - 'Planning Capacity'/'Capacity Shortfall Days' -> 'Days above the 180-day 75th-percentile demand threshold'.
   - 'The next 30 days are expected to generate approximately 330,941' -> 'The archived legacy projection for 2026-07-01..30 totals 330,941 (ATLAS M2 projection: 333,968; neither scored)'.
   - Drop or annotate R².
   - Use 'requests (intake)' instead of 'workload'.
   - Add 'of the five-borough subset' to the shares.
5. Optionally lead with a screenshot of the M7 dashboard rather than the legacy Power BI images.

**Independent second report (data#1).** The legacy part of the README is the landing page a hiring manager reads. Its 'Executive KPIs' table and 'Key Business Insights' still state:
- 'Forecast accuracy improved by 40.10% over the baseline model';
- 'The next 30 days are expected to generate approximately 330,941 requests';
- 'Five forecast dates are expected to exceed the daily planning capacity';
- 'The model provides 13 days of advance warning before the first expected shortfall';
- 'The linear regression model was selected because it produced the lowest holdout-period error'.
The top banner (README.md:13-16) qualifies only the 4.93% MAPE and 'planning capacity'. The repository's own documents contradict the rest:
- LEGACY_LIMITATIONS.md:33-34: 40.10% is a relative MAPE reduction, 'not a 40 % gain in accuracy'.
- LEGACY_LIMITATIONS.md:68-72: the five 'High' days exceed the percentile by 12-210, less than the h=1 MAE of 514.3.
- LEGACY_LIMITATIONS.md:85-86: '13 days advance warning' is 'a horizon index, not a validated lead time'.
- LEGACY_LIMITATIONS.md:45-50: in development the legacy model is worse than three simple baselines beyond day 1.
- BACKTESTING.md:926-929: the July 2026 forecast was never scored, and the [...]

**Verification.**

- claims#1 reproduce: **CONFIRMED**, severity HIGH. The core finding holds. Below its notice, the README is still the July 2026 legacy text, unchanged apart from the notebook link. Its title, intro, "Executive KPIs", "Key Business Insights", "Forecasting Method" and "Capacity-Risk Logic" sections state, without any per-claim qualifier, several claims that ATLAS's own docs refute:
- the 40.10 % "accuracy" gain;
- 13 days of "advance warning";
- the legacy regression "selected";
- "recommended actions" (add capacity);
- "the next 30 days";
- "workload";
- borough and agency shares that do not mention the five-borough subset.

I reproduced every quoted line, cross-reference, the atlas check labels, the 333,968 against 330,941 totals, the PDF date and the screenshot column. ROADMAP.md:279-281 records the README rewrite as an M0-only deferral, and no later milestone revisited it.

Points where the finding overstates:
1. "The first things a reader sees" is not quite right. The 60-line notice (lines 5-64) comes before the screenshots and KPIs. It also contains a general "Read before relying on the headline figures" warning with a link, and states that the body is the unchanged original (lines 7-8, 54-56).
2. R² is not something ATLAS calls wrong. ROADMAP only says ATLAS does not report it. R² is, however, the same h=1 holdout statistic the MAPE caveat applies to.
3. The "recommendation" lint applies to API strings and M4 reports, not [...]
- claims#1 challenge: **CONFIRMED**, severity HIGH. I tried to refute the finding and could not; I confirm it.

Every quoted README line and every counter-citation checks out. The README body is byte-identical to the July 2026 original except for the notebook filename. Its "Key Business Insights" still state, in the present tense and with no qualifier, claims ATLAS documents as wrong or forbidden:
- "Forecast accuracy improved by 40.10%";
- "The model provides 13 days of advance warning";
- the legacy regression "was selected" (BACKTESTING says it should not be used);
- "Recommended actions include: High: Add capacity";
- "the next 30 days" for an archived July 2026 projection.

The project's own API wording lint fails the README at :86, :140 and :170 (recommendation wording) and at :24 ("calibrated", which views.py:98-99 deliberately avoids because the intervals FAILED_CALIBRATION). So the README contradicts a standard the project enforces in tests on the API.

I looked for a legitimate reason to keep the body unqualified and found none. The README is not part of the hash-verified legacy-v0 snapshot. No test pins its text. The original survives in git history. The only recorded justification, ROADMAP.md:279-281, scopes the deferral explicitly to M0 ("not rewritten in M0"), and M1 through M7 never revisited it.

Parts of the finding I reject or weaken:
- **"Before or instead of" is overstated.** A reader meets a 60-line notice [...]
- data#1 reproduce: **CONFIRMED**, severity MEDIUM, already disclosed. The core of the finding reproduces exactly.
- The landing-page KPI table and the "Key Business Insights" bullets still present "Forecast accuracy improved by 40.10%", "13 days of advance warning", "next 30 days are expected ... 330,941" and "five dates expected to exceed", none qualified where they appear.
- The banner restates the caveat for only two figures: the 4.93% MAPE and "planning capacity".
- The repository's own LEGACY_LIMITATIONS.md and src/atlas/reproduce.py KPI_MEANINGS say explicitly that these wordings are wrong. The "40% gain in accuracy" reading and the "lead time" reading are both rejected there, and the shortfall excesses are within the noise.
- My independent recomputation confirms every number. So the problem is interpretive, not arithmetic.

Two overstatements in the finding, neither of which changes the verdict:
1. The claimed consequence says a reader meets these claims "before any qualifier". That is not true. The banner at README.md:13-16 comes first, calls them "the headline figures", and links to LEGACY_LIMITATIONS.md, which covers every claim. The qualification is therefore indirect and incomplete rather than absent.
2. "Refutes" is too strong for two of the items:
   - 330,941 is unvalidated rather than refuted; the M2 projection of 333,968 is close to it.
   - The "selected because lowest holdout error" sentence is literally true as history (4.93 [...]
- data#1 challenge: **PARTIALLY_CONFIRMED**, severity LOW, already disclosed. The core observation holds. The landing-page README still states several legacy interpretations as current fact: an "accuracy improved by 40.10%", "the next 30 days", "13 days of advance warning", and five expected shortfall days. The project's own LEGACY_LIMITATIONS.md and reproduce.py KPI_MEANINGS say these readings are wrong. Only two of them carry a specific correction in place.

As a defect, though, it is weak:
1. It is already disclosed accurately and specifically. ROADMAP.md:279-281 names exactly this limitation, and preserving the legacy material unchanged is a recorded design decision (ARCHITECTURE.md:152 and 158).
2. The README itself warns before the claims ("Read before relying on the headline figures", plus a link). The finding's stated consequence, that readers meet the claims "before any qualifier", is factually wrong.
3. Two of the five listed claims are not refuted by ATLAS:
   - The selection rationale is a true historical description, and M2 also recommends a regression.
   - 330,941 is an unscored projection.
   - "Improved over the 7-day baseline" is directionally supported in M2 confirmation (23.6%, p=0.002). Only the "40.10% accuracy" wording is wrong.
4. Nothing is hidden, no number is wrong, and no code, API or dashboard path is affected. It is purely a presentation and wording issue under gate L.

What remains is real but small:
- The banner does not [...]

**Lead reconciliation.** Found independently by two lenses. Claims verifiers: HIGH/HIGH; data verifiers: MEDIUM/LOW (citing the banner and ROADMAP disclosure). Lead: HIGH for the v1.0 documentation gate, because the README is the landing page of a portfolio release whose purpose is scientific honesty and it states refuted claims (40.10 % 'accuracy improvement', '13 days of advance warning', 'Recommended actions', 'the next 30 days are expected...') as Key Business Insights; the banner qualifies only two of them. README-only fix; no research artifact changes.

## M8-02

**`atlas-deploy recover` records an interrupted activation as verified without reloading nginx**

- Severity: **MEDIUM** (gate G); fix complexity: small; alters published research results: no
- Found by: deploy#1 (MEDIUM: `atlas-deploy recover` marks an interrupted activation ACTIVATED without reloading nginx, so nginx keeps serving the previous release's dashboard, routes and configuration in front of the new API)
- Files: `docs/DEPLOYMENT.md:495`, `docs/DEPLOYMENT.md:584-586`, `src/atlas/deploy/releases.py:540-559`, `src/atlas/deploy/releases.py:750-786`, `src/atlas/deploy/verify.py:229-273`, `tests/test_m6_container.py:632-647`, `tests/test_m6_releases.py:542-547`

**Description.** An activation renders and installs the new site, switches the link, restarts atlas.service (a blocking call of up to 200 s that waits for READY), and only then reloads nginx (releases.py:557). If the activation process dies during the restart, systemd's restart job still completes, so the API comes up on the new release. Two things kill it there: a lost SSH session (SIGHUP, the documented case for recover at DEPLOYMENT.md:495) and Ctrl-C (KeyboardInterrupt, a BaseException that `_start` does not catch at releases.py:542). The nginx reload never runs. recover() (releases.py:764-777) then only checks that the pending release is current and calls the verifier. The verifier talks to the API on loopback and checks the published copy on disk, never what nginx has loaded. It records ACTIVATED and saves the release and the new deploy.env as the last verified state, without installing or reloading anything. This contradicts DEPLOYMENT.md:584-586 ('the browser always gets the dashboard built with the API it talks to').

**Evidence.** Script repro_recover.py (FakeHost subclass that records the site text nginx loads on reload, and raises SystemExit inside the first nginx_reload to stand in for SIGHUP). Output: activate A: activated, nginx loaded /opt/atlas/dashboard/0.7.0-aaaaaaaaaaaa. Activate B interrupted: current link 0.7.0-bbbbbbbbbbbb, site on disk -> dashboard/B, nginx loaded -> dashboard/A. recover: activated ['verified after an interrupted activation'], host calls during recover: [] (no reload), last verified state saved as 0.7.0-bbbbbbbbbbbb, MISMATCH True. Config variant (repro_recover_config.py: same release, ATLAS_DEPLOY_ALLOW_CIDRS added, same interruption): recover: activated; site on disk has allow-list: True; nginx loaded config has allow-list: False; last verified deploy.env has ALLOW_CIDRS: True. Coverage gap: test_an_interrupted_activation_that_verifies_is_kept checks only the outcome, and the container test interrupts only a broken release (which goes the rolled_back path and does reload nginx).

**Reproduction.** S=<audit-scratch>/deploy; cd $S && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$S/tree/src:$S/tree .venv/bin/python $S/repro_recover.py $S/runs (and repro_recover_config.py likewise). $S/tree is `git archive HEAD` of 194fa4a.

**Consequence.** After a routine recovery, browsers get release A's dashboard bundle (A's copy is kept because prune protects the last-good release) and A's route allow-list, while the API runs release B. If the contract changed, new endpoints 404 at the proxy and old UI code talks to a new API. For a configuration change, a tightened ALLOW_CIDRS, a public-demo -> private switch or new rate limits are not enforced by nginx. Meanwhile history.jsonl, last-good.json, `atlas-deploy status` and `verify` all say the state was verified. Neither verify nor the monitor looks at nginx's loaded configuration. The mismatch lasts until something reloads nginx: certbot's deploy hook, a reboot or another activation.

**Recommended repair.** In recover(), when the pending release is current, complete the activation instead of only verifying it. Re-run `self.install(release, config)` (idempotent: publish, render, nginx -t, daemon-reload, site removal for tunnel), then `nginx_reload()` for exposed profiles (or reuse `_serve`), then verify. Add a FakeHost regression test that tracks the loaded site and dies inside nginx_reload (repro_recover.py is a ready template). Optionally make `_start` treat KeyboardInterrupt after the switch like other failures.

**Verification.**

- deploy#1 reproduce: **CONFIRMED**, severity MEDIUM. The behaviour reproduces exactly as claimed. I killed a real activation process with real SIGHUP and SIGINT while restart was blocking. In both cases recover() recorded ACTIVATED and saved the release and deploy.env as the last verified state without making any host call. nginx was left with the previous release's site, which serves A's dashboard root, or lacks the new allow-list in the config variant. The verifier cannot detect this, because it only probes the API on loopback, the unix socket and the published copy on disk.

Scope and corrections to the finding:
(a) Only exposed profiles (private, public-demo) are affected. For tunnel, install() removes the site and reloads nginx before the switch (releases.py:429-440).
(b) The window is the duration of `systemctl restart`. The guide puts it at about 3-6 s; the timeout allows up to 200 s. It is the main blocking step after the switch. An interruption before the switch, or before the API comes up on the new release, goes through the _fail/restore path, which does reload nginx.
(c) DEPLOYMENT.md:584-586 is written about an explicit rollback, so the contradiction there is indirect. The clearer contradictions are DEPLOYMENT.md:559 ("until `atlas-deploy recover` completes it") and the module docstring at releases.py:14-18, which says activation includes reloading nginx before the state is kept as verified.

Not verified here (no [...]
- deploy#1 challenge: **CONFIRMED**, severity MEDIUM. I tried to refute the finding and could not. The path is reachable through the documented CLI flow: `sudo atlas-deploy activate`, a lost SSH session or Ctrl-C, then `sudo atlas-deploy recover`.

The window is narrow but real. It opens after the link switch, once the restart job is queued, and closes when nginx is reloaded. Interruptions outside it are handled safely:
- Before the switch, current != release, so `_fail` restores the last verified state and reloads nginx.
- Between the switch and the restart, the identity check fails and the release is rolled back with a reload.
- After the reload, nothing is skipped.

So the defect needs an interruption during the 3-6 s restart (up to 200 s). That restart is a large share of an activation's wall time. A systemd job keeps running after the systemctl client dies, which is standard systemd behaviour; I could not test it in a real systemd here (no image or apt access). The repo's own container test models an interrupted activation exactly that way: link switched, service restarted, nginx not reloaded.

I reproduced the result with a real SIGHUP kill and with KeyboardInterrupt. recover makes no host calls, records ACTIVATED, and saves the new release and deploy.env as the last verified state. Meanwhile, nginx's loaded configuration still serves the previous release's dashboard root and assets, and in the configuration case it lacks [...]

**Lead reconciliation.** Both verifiers CONFIRMED MEDIUM (real SIGHUP/SIGINT reproduction). Directly violates the M7/M6 guarantee that browser assets and backend move together.

## M8-03

**Rollback verification and nginx rendering use the running tool's dashboard CSP/descriptor, not the restored release's**

- Severity: **MEDIUM** (gate G); fix complexity: small; alters published research results: no
- Found by: deploy#2 (MEDIUM: Rollback verification and nginx rendering use the running tool's dashboard CSP and descriptor, not the restored release's, so a failed upgrade that changes the CSP cannot be rolled back (rollback_failed, service stopped and disabled))
- Files: `docs/DEPLOYMENT.md:528-538`, `src/atlas/dashboard.py:317-331`, `src/atlas/dashboard.py:54-66`, `src/atlas/deploy/render.py:142-154`, `src/atlas/deploy/render.py:170-176`, `src/atlas/deploy/render.py:37`, `src/atlas/deploy/verify.py:46`, `src/atlas/deploy/verify.py:536-540`

**Description.** release.json records each release's own dashboard CSP (dashboard.py:330, record['csp']), but nothing uses it. `_dashboard` in verify compares the CSP served by the restored release's API (that release's own code) with the importing tool's DASHBOARD_CSP constant (verify.py:536), and the descriptor format with the tool's DESCRIPTOR_FORMAT (verify.py:539). The nginx site rendered for a restored release likewise takes DASHBOARD_HEADERS and the descriptor from the running tool (render.py:150, 170-176). DEPLOYMENT.md:528 tells operators to plan and activate an upgrade with the new release's own tool, and that tool also performs the automatic restoration. So once a release changes the dashboard CSP, headers or descriptor format, restoring or explicitly rolling back to an older release fails the 'dashboard' check, and the older dashboard is served with headers it was not built for.

**Evidence.** repro_csp_tool_skew.py: release A records csp == CSP_OLD: True; same tool -> dashboard check ok: True; newer tool (constant patched to CSP_OLD + '; upgrade-insecure-requests') -> dashboard check ok: False ['/ lacks the dashboard\'s Content-Security-Policy']; nginx site rendered for A sends CSP_NEW: True; the recorded csp is not used anywhere in verification. End to end with the package's Deployment (repro_csp_rollback.py): A activated; failed upgrade to B with the new tool -> outcome rollback_failed, restored 0.7.0-aaaaaaaaaaaa, detail "restore of 0.7.0-aaaaaaaaaaaa: dashboard: / lacks the dashboard's Content-Security-Policy", host calls tail ['nginx reload', 'stop', 'disable'].

**Reproduction.** S=<audit-scratch>/deploy; cd $S && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$S/tree/src:$S/tree .venv/bin/python $S/repro_csp_rollback.py $S/runs (the newer tool is simulated by patching atlas.deploy.verify.DASHBOARD_CSP; verifier answers come from each release's own CSP).

**Consequence.** Latent in v1.0, because no deployed release has changed the CSP yet. The first post-v1.0 upgrade that tightens or changes the dashboard CSP (or Permissions-Policy or descriptor format) and then fails verification turns an automatic rollback into an outage: the service is stopped and disabled. An explicit `rollback --to <older>` is undone. In every profile the API-served dashboard check fails the same way.

**Recommended repair.** Verify the dashboard against the release's recorded policy: `record.get('csp', DASHBOARD_CSP)` and, if recorded, the descriptor format. Render the nginx dashboard headers from the restored release's record (store the full header set and descriptor format in manifest_record for future releases). Add a regression test that patches the tool constants and restores an older release.

**Verification.**

- deploy#2 reproduce: **CONFIRMED**, severity MEDIUM. The defect reproduces independently against real API processes, not only through a patched module constant. The verify and render code uses the running tool's dashboard constants and ignores the per-release csp recorded at dashboard.py:330. So when the tool and the release differ in the dashboard CSP (and, by the same code path, in DASHBOARD_HEADERS or DESCRIPTOR_FORMAT):
(a) A failed upgrade run with the newer tool cannot restore the older release. The result is rollback_failed with the service stopped and disabled.
(b) An explicit `rollback --to <older>` is undone.
(c) The older release's nginx site is rendered with the newer tool's headers and descriptor.
This contradicts the documented rollback guarantees (DEPLOYMENT.md:545-551 and 582-586, "the browser always gets the dashboard built with the API it talks to"), and nothing in the docs discloses it.

One nuance against the finding: DEPLOYMENT.md:528 tells operators to use the new release's tool only for the M6-to-M7 transition. The general upgrade procedure (DEPLOYMENT.md:324-325, 504-509) runs the current (old) release's tool. On that path, the failure shows up as a healthy CSP-changing upgrade being rejected and rolled back (my scenario 3): safe, but it blocks the upgrade. The natural workaround is the M6-to-M7 precedent of running the new tool, and that leads straight to scenarios 1 and 2. This does not weaken the core [...]
- deploy#2 challenge: **CONFIRMED**, severity MEDIUM. I tried to refute this on four grounds: reachability, consequence, disclosure and severity. None of them refutes it.

Reachability:
- The CLI uses the real verify (cli.py:194), and `_dashboard` runs in every profile whenever release.json has a dashboard record. Every M7+ release has one.
- The automatic restoration runs inside the process of whichever tool started the activation.
- A release that changes the CSP forces the operator onto the new release's tool. The default `atlas-deploy` is the current (old) release's, and with it such an upgrade always fails verification and rolls back (my scenario 2a). Using the new tool is also the documented M6→M7 precedent (DEPLOYMENT.md:528).
- From then on, any failure of the new release makes the restore of the older release fail its own dashboard check. Outcome: rollback_failed, with the service stopped and disabled.
- Once the new release is active, the default `atlas-deploy rollback` is undone, because the restored state's tool is the new one.
- I reproduced all of this with real API servers running each release's code, not only with patched constants. `record["csp"]` is recorded but dead data.

Consequence, accurately bounded:
- The failure is fail-closed (an outage, not unverified serving) and is availability-only, with no security exposure.
- Recovery is one documented command. After the failed restore, `current` points at the [...]

**Lead reconciliation.** Both verifiers CONFIRMED MEDIUM. Latent until the first release that changes the CSP, then a failed upgrade becomes an outage (rollback_failed).

## M8-04

**The HLN-DM test is oversized at h=30 with T=336-486; the selection gate's significance and the bandwidth-robustness claims do not survive size correction**

- Severity: **MEDIUM** (gate B); fix complexity: medium; alters published research results: yes
- Found by: forecast#1 (MEDIUM: The DM/HLN test is oversized at h=30, T=336-486: the 'conservative' p and the bandwidth-robustness claims do not hold, and the selection gate's significance does not survive size correction)
- Files: `docs/BACKTESTING.md:233-244`, `docs/BACKTESTING.md:316-321`, `docs/BACKTESTING.md:363-369`, `docs/BACKTESTING.md:47-48`, `docs/results/atlas-m2-rolling-origin-v1/full/metrics.json (bandwidth_sensitivity)`, `docs/results/atlas-m2-rolling-origin-v1/full/report.md:776-780`, `src/atlas/stats.py:195-265`, `src/atlas/stats.py:253`, `src/atlas/stats.py:283-330`

**Description.** diebold_mariano compares the HLN statistic with t(T−1) critical values. The statistic uses a rectangular HAC truncated at h−1 = 29. bandwidth_sensitivity compares rectangular-59 and Bartlett-60/90/120 statistics with the same t(T−1) reference (stats.py:324). With h=30 and only T=336 (confirmation) or T=486 (development) origins, the long-run-variance estimate is noisy and the test over-rejects. Wider bandwidths with a standard-normal/t reference over-reject even more (the fixed-b critical values grow with the bandwidth). The formulas are implemented correctly; the problem is finite-sample size. BACKTESTING.md nevertheless presents the following as evidence of robustness: 'conservative' p-values; the development selection p 'does not depend on the HAC bandwidth (p 0.002-0.044)'; 'The development selection test becomes more, not less, significant under wider bandwidths'; and H2 'stays significant under every HAC bandwidth tried'.

**Evidence.** (1) Size under the test's own maintained assumption, using atlas.stats directly (dm_cv_atlas.py, N=2000, H0, per-origin mean of 30 iid daily losses = exact MA(29)). The rule 'p_value_conservative < 0.05' rejects 6.5% (normal) / 7.3% (t3) at T=486 and 9.3% / 9.6% at T=336. bandwidth_sensitivity variants reject 10.2-14.0% (T=486) and 12.9-17.8% (T=336). (2) Size-corrected 95% critical values of |stat| (dm_cv.py, N=3000; nominal t is 1.965). T=486, MA(29) / daily AR(1) φ=0.9: rect29 2.31/2.39, rect59 3.02/2.96, bart60 2.61/2.69, bart90 2.75/2.81, bart120 2.93/3.08. The committed selection statistics are 2.022, 3.105, 2.382, 2.745, 3.083, so the primary rect29 test and bart60 are below the corrected cut-off and the rest are borderline. T=336, MA(29): rect29 2.39, rect59 3.28, bart60 2.68, bart90 2.90, bart120 3.15. The committed H2 statistics are 2.943, 2.383, 2.972, 2.697, 2.590, so H2 is not significant at rect59, bart90 or bart120. (3) Null-imposed circular block bootstrap of the actual per-origin loss differentials from the reproduced forecasts.csv (dm_bootstrap.py, B=4000, block length 30/60/90): selection p 0.129/0.115/0.082, H1 0.104/0.135/0.145, H2 0.031/0.055/0.079 (Holm with H1 ≥ 0.06). (4) The real dependence is stronger than MA(29): the H1 differential's ACF is 0.37 at lag 29 and 0.27 at lag 90, so the distortions above are lower bounds.

**Reproduction.** Scripts are in <audit-scratch>/forecast/. dm_check.py checks formula correctness against scipy and an own HLN. dm_cv_atlas.py runs a Monte Carlo of atlas.stats.diebold_mariano / bandwidth_sensitivity under MA(29). dm_cv.py computes size-corrected critical values and compares them with the committed statistics recovered from metrics.json. dm_bootstrap.py runs a block bootstrap on <scratch>/m2-full/forecasts.csv. Run: cd <scratch> && PYTHONPATH=<scratch>/tree/src .venv/bin/python dm_cv_atlas.py (dm_cv.py and dm_bootstrap.py run with -I). dm_bootstrap.py needs the full run output first: atlas backtest run --period development --period confirmation --period legacy_window --sealed-selection docs/results/atlas-m2-rolling-origin-v1/development/selection.json --output-dir <scratch>/m2-full.

**Consequence.** The pre-registered selection gate ('DM conservative p < 0.05 with R* ahead') was passed with |t| = 2.02 against an effective cut-off of 1.965. Under a correctly sized test the gate would most likely fail, and rule step 4 would then select the fallback B* (same_weekday_median_5w). The robustness narrative in BACKTESTING.md (findings 3, 6.1, 6.2) and the 'conservative' label overstate the statistical support for the selection and for H2. The disclosed limitation that H1 is not significant after Holm is reinforced, not weakened. The point-accuracy lead (verified in all 70 cells) is unaffected. A hiring manager reading the doc would take away 'selection significant, robust to bandwidth', which the evidence does not support.

**Recommended repair.** Keep the sealed pre-registered decision; changing it post hoc would be worse science. Then: (a) state in BACKTESTING.md sections 5, 6.1 and 6.2 and in finding 3 that the HLN-DM with h=30 and T=336-486 over-rejects (about 7-9% at nominal 5% under its own MA(h−1) assumption), and that the development selection statistic falls below a size-corrected critical value. (b) In bandwidth_sensitivity, replace the t(T−1) reference with fixed-b critical values (Kiefer-Vogelsang 2005 for Bartlett; or Coroneo-Iacone fixed-smoothing), or with a simulated MA(h−1) critical value. Otherwise drop the 'does not depend on bandwidth / more significant / stays significant under every bandwidth' sentences. (c) Rename 'conservative p' to 'larger of the HLN and Bartlett-2h p-values'. (d) Add a Monte Carlo size regression test. Check whether the same t(T−1)-with-wide-bandwidth sensitivity in M3 (UNCERTAINTY.md:431) needs the same correction.

**Verification.**

- forecast#1 reproduce: **CONFIRMED**, severity MEDIUM. The formulas are implemented correctly. At h=30 with T=336-486, though, the HLN-DM test with a t(T-1) reference over-rejects under its own maintained MA(h-1) null: about 7.3-7.5% at T=486 and 8.8-9.2% at T=336 at nominal 5%. The wider-bandwidth variants with the same t reference over-reject more, 11-18%. I reproduced this independently by calling the audited functions in my own Monte Carlo.

Consequences I confirmed:
- The 'conservative' p is not conservative in size; its Bartlett-2h leg never binds under the null.
- The pre-registered gate statistic, 2.022, has a size-corrected p of 0.07-0.09.
- The documented robustness claims are not supported once each bandwidth is size-corrected. "H2 stays significant under every HAC bandwidth tried" fails at r59, b90 and b120. "Does not depend on the HAC bandwidth (p 0.002-0.044)" becomes 0.036-0.086, with the primary above 0.05.
- None of this is disclosed. The docs only call the p-values "indicative" for stationarity and selection-bias reasons.

Secondary details are overstated but do not change the core finding:
- The finder's T=486 critical value is slightly high.
- The ACF "lower bound" argument does not apply to the selection test.
- The "more significant at wider bandwidths" direction survives correction.

Severity is MEDIUM (gate B), as claimed, not HIGH:
- The disclosed headline (the lead over B* is not significant after Holm) [...]
- forecast#1 challenge: **CONFIRMED**, severity MEDIUM. The finding's core holds under my own independent checks.
- The HLN-DM rule with t(T-1) over-rejects at h=30 under its own maintained MA(h-1) assumption: about 7-9% at T=486 and 7-10% at T=336, for both the primary test and the pre-registered 'conservative' rule.
- The wider-bandwidth variants over-reject more (11-18%), which matches fixed-b theory: my Monte Carlo cut-offs agree with the Kiefer-Vogelsang Bartlett cut-offs.
- The committed selection statistic (2.022) has a size-corrected p of 0.062-0.070. So the pre-registered gate, which made atlas_linear_regression the recommended model, passes only because the test is oversized.
- H2's 'significant under every bandwidth' claim fails after correction at rect59, bart90 and bart120.

The code path is real: the CLI backtest, committed results, docs and dashboard all use it. The doc claims are not already disclosed; the 'indicative' hedge does not cover them, and they contradict the oversize.

Points against the finding, which reduce its scope but not its validity:
- The formulas are correct, and HLN-DM with t(T-1) is standard practice.
- The headline already says the lead over B* is not statistically established, and H1 is not significant.
- For the selection test, dependence beyond h-1 is negative, so the 'lower bound' sub-claim is wrong there. Wider-bandwidth selection p-values stay around 0.02-0.06 after correction, so the [...]

**Lead reconciliation.** Both verifiers CONFIRMED MEDIUM with their own Monte Carlo. The sealed pre-registered decision stands (changing it post hoc would be worse science); the documentation must stop presenting the selection as robustly significant.

## M8-05

**Shipped dashboard bundle omits third-party licence notices (MIT/ISC) and the Inter OFL text**

- Severity: **MEDIUM** (gate M); fix complexity: small; alters published research results: no
- Found by: supplychain#1 (MEDIUM: Dashboard bundle (committed in a public repo and served by nginx) ships MIT/ISC code and the OFL font without the required licence notices); claims#2 (MEDIUM: License notices are missing from the shipped dashboard: React, zod and d3 MIT/ISC notices are stripped and the Inter OFL text is absent)
- Files: `docs/DASHBOARD.md:102-105`, `docs/DASHBOARD.md:103-113`, `web/dist/assets/index-D84JVxAN.js`, `web/dist/assets/inter-latin-wght-normal-Dx4kXJAl.woff2`, `web/dist/atlas-dashboard.json`, `web/package.json:29-36`, `web/package.json:5`, `web/vite.config.ts:24-37`, `web/vite.config.ts:26-37`

**Description.** web/dist is committed to the public GitHub repository (visibility: public) and served by the API and nginx. It contains minified copies of react, react-dom and scheduler (MIT), zod (MIT) and ten d3 packages (ISC), plus the Inter variable font (OFL-1.1). The MIT and ISC licences require the copyright and permission notice in all copies or substantial portions. OFL-1.1 condition 2 requires the copyright notice and the licence with every copy of the font. The build strips the notices, Vite's `build.license` is off, and no THIRD-PARTY/NOTICE file exists in web/dist or anywhere in the repository. No document records a licence inventory of the shipped dependencies. This is separate from the disclosed missing project LICENSE: these are obligations owed to upstream authors, whatever licence the owner picks.

**Evidence.** Every file in web/dist/assets has 0 matches for '@license|/*!|Copyright|MIT License'. The React source in node_modules (react-dom.production.js) starts with '/** @license React ... licensed under the MIT license'. `grep -rIl -i 'license|OFL' web/dist` returns nothing. A scratch build with `license: { fileName: 'THIRD-PARTY-LICENSES.md' }` produced a 12121-byte file listing 14 packages (d3-* x10 ISC, react, react-dom, scheduler, zod MIT); all other assets stayed byte-identical. The Inter font is not in that list because it is a CSS asset. fontTools on the woff2 shows nameID 0 'Copyright 2016 The Inter Project Authors' and nameID 14 'https://openfontlicense.org', but no licence text (no nameID 13). grep for MIT/ISC/OFL/licence in README.md and docs/*.md finds no licence inventory. The repository is public (list_repos: visibility public).

**Reproduction.** cd web && for f in dist/assets/*.js; do grep -c -E '@license|Copyright' $f; done (all 0); head -c 300 node_modules/react-dom/cjs/react-dom.production.js. In a scratch copy (git archive HEAD), add `license: { fileName: 'THIRD-PARTY-LICENSES.md' }` to the build block of web/vite.config.ts and run `npx vite build --outDir <scratch>/dist-license`; diff -r against web/dist shows only the new file and its atlas-dashboard.json entry. Font check: scratch/scripts/fontnames.py (fontTools).

**Consequence.** Each public push and each served page redistributes third-party code without the notices its licences require. This is a small but real compliance defect for a portfolio piece meant to look professional, and a reviewer running a licence scanner on the published artefact will flag it.

**Recommended repair.** Set `build.license` in web/vite.config.ts (for example fileName 'THIRD-PARTY-LICENSES.md' or '.vite/license.md') so every build emits the bundled packages' notices. Add the OFL-1.1 text and Inter copyright to web/public so they ship with the font. Rebuild and recommit web/dist, update the atlas-dashboard.json manifest, and list the shipped runtime licences in DASHBOARD.md. This does not choose the project's own licence.

**Independent second report (claims#2).** The committed and deployed build web/dist bundles minified copies of:
- react 19.3.0, react-dom 19.3.0, scheduler 0.28.0 and zod 4.6.5 (MIT);
- d3-array, d3-color, d3-format, d3-interpolate, d3-path, d3-scale, d3-shape, d3-time, d3-time-format and internmap (ISC);
- the Inter variable font from @fontsource-variable/inter 5.3.0 (OFL-1.1).

The only condition of MIT and ISC is that the copyright and permission notice appear in all copies or substantial portions. OFL-1.1 condition 2 requires the copyright notice and the licence with each copy. The source packages carry '@license React ... Copyright (c) Meta Platforms ... MIT' headers, but the production build strips every legal comment. No LICENSE, NOTICE or third-party-licences file exists anywhere in the repository or in web/dist. The font's name table holds the copyright (name ID 0) and only a licence URL (ID 14, https://openfontlicense.org); the licence text (ID 13) is absent. The repository itself has no LICENSE (disclosed; an owner decision). This finding is about third-party obligations, which apply regardless of that decision: the repo publishes web/dist on GitHub, and every deployment serves it to browsers.

**Verification.**

- supplychain#1 reproduce: **CONFIRMED**, severity MEDIUM. The core claim reproduces exactly. The repo is public and tracks web/dist. Those files, which the API and nginx also serve, contain minified React/react-dom/scheduler (MIT), zod (MIT) and nine d3-* packages plus internmap (ISC). The upstream copyright and permission notices that MIT and ISC require "in all copies" are not in the bundle, and no notice file exists anywhere in the repo. Vite's build.license is off by default and is not set. Enabling it produces a 14-package notice file and leaves every other asset unchanged.

Two minor overstatements:
- The Inter woff2 does keep its copyright notice in its name table (nameID 0) plus a licence URL (nameID 14). Only the OFL licence text is missing, so OFL compliance is partial rather than absent. My memory is that the OFL FAQ is lenient about embedded web fonts, but I could not check that offline, so I did not rely on it.
- "Ten d3 packages" is really 9 d3-* plus internmap.

Neither changes the substance. The existing disclosure (missing project LICENSE, ROADMAP.md:277) is about the owner's own licence choice, not obligations to upstream authors, so this is not already disclosed.

Severity: MEDIUM, at the low end. It is a real, ongoing licence-compliance gap in a public redistribution, and gate M is the licensing review gate. But the practical legal risk is small, the fix is small, and it does not affect correctness, security or [...]
- supplychain#1 challenge: **CONFIRMED**, severity MEDIUM. I tried to refute the finding on four grounds, and none of them holds.

**Is the code actually distributed?** Yes. web/dist is tracked in git and the repository is public, so the minified copies are redistributed to anyone. The local API serves them, and the nginx deployment package is built to serve them too.

**Are the notices really required and really missing?** Yes. The MIT and ISC licences both require the copyright and permission notice in all copies or substantial portions. A minified copy of react-dom, zod or d3 is a substantial portion. The upstream sources carry these notices, and every file in web/dist has none. Vite's licence-file option defaults to off, and the config does not enable it.

**Is it already disclosed?** No. The ROADMAP entry about a missing LICENSE covers only the project's own licence, which the owner has not chosen. No document lists the third-party licences of what ships, and CI has no licence check.

**Is the severity inflated?** I keep MEDIUM, at its low end. It is not cosmetic: it breaks the conditions of the licences on code that is redistributed publicly. It is not HIGH either. Enforcement risk for MIT/ISC attribution is negligible, the owner and a few account holders are the only users of the private deployment, and leaving the notices out of minified bundles is common practice. It is a fix-or-explicitly-defer item for gate M, not a release [...]
- claims#2 reproduce: **CONFIRMED**, severity MEDIUM. The core claims all reproduce from my own commands:
- The shipped web/dist (committed to git and served by every deployment) bundles minified react/react-dom/scheduler/zod (MIT), ten d3 or related ISC packages, and the unmodified Inter OFL-1.1 font.
- No asset carries any copyright or permission notice.
- The repository has no LICENSE, NOTICE or third-party-licences file.
- The font has the copyright in nameID 0 but only a licence URL, with no nameID 13 licence text.

MIT and ISC require the notice in all copies or substantial portions, and OFL condition 2 requires the licence text with each copy. This is a real third-party compliance gap. It is separate from the disclosed lack of a project LICENSE (ROADMAP.md:277), which does not mention third-party notices, so already_disclosed=false.

The claimed fix check also holds: Vite 8.3.1 `build.license` emits a 12,121-byte notice file for exactly those 14 packages, leaves all assets byte-identical, and does not cover Inter.

Severity: I agree with MEDIUM. For a private-first research demo the legal risk is low and nothing breaks at runtime, so it is not release-blocking (HIGH). But gate M exists for this, the committed repo's only copy of this third-party code is the stripped bundle, and a technical reviewer of a v1.0 portfolio piece would notice. It should be fixed or explicitly deferred.

The lead should know the proposed repair [...]
- claims#2 challenge: **CONFIRMED**, severity MEDIUM. The core claim holds under my own checks. The committed web/dist ships minified copies of 4 MIT packages and 10 ISC packages. Their notice condition requires the copyright and permission notice in copies or substantial portions. No licence text exists anywhere in web/dist or in the repository. The build strips the source '@license' headers, and the d3 ESM sources have no headers to begin with.

The distribution is real:
- web/dist is tracked in git on a GitHub remote.
- The M6 release packaging copies it for nginx to serve.
- The optional public-demo profile would serve it to anyone.

The repository documentation does not disclose this. ROADMAP.md:277 covers only the project's own code licence and the NYC Open Data attribution. Third-party notice duties apply whatever the owner decides about the code licence.

Corrections to the finding:
(a) The d3 code lives in data-C2g4oQuV.js, not index-D84JVxAN.js.
(b) "Deployed build" overstates things: no deployment has happened yet (M6 phase B is pending). The obligation applies at redistribution, meaning the GitHub copy and any future deployment.
(c) The Inter part is the weakest. The copyright notice is in the font's own metadata (name ID 0) and a licence URL is in name ID 14; only the full OFL text is missing. The file is byte-identical to what Fontsource itself redistributes. It is strictly non-compliant with OFL condition 2, but [...]

**Lead reconciliation.** Found independently by two lenses; all four votes CONFIRMED MEDIUM. Separate from the project's own (undecided) licence.

## M8-06

**No vulnerability scanning or update monitoring for the deployed Python runtime**

- Severity: **MEDIUM** (gate M); fix complexity: small; alters published research results: no
- Found by: supplychain#2 (MEDIUM: No vulnerability scanning or update monitoring for the deployed Python runtime; the only gate is npm audit at high severity on push/PR)
- Files: `.github/workflows/ci.yml:186-187`, `.github/workflows/ci.yml:3-7`, `docs/DASHBOARD.md:107-113`, `docs/DASHBOARD.md:484`, `docs/DEPLOYMENT.md:121`, `pyproject.toml:44-50`

**Description.** The component that faces the network once deployed is the Python API: fastapi 0.141.1, starlette 1.7.0, uvicorn 0.54.0, h11 0.16.0 and pydantic 2.14.1 behind nginx. Nothing in CI, the tests or the docs checks these packages against vulnerability databases. CI runs only `npm audit --audit-level=high`, which covers the dashboard toolchain and lets moderate and low advisories in the shipped browser dependencies pass. CI triggers only on push to main, PRs and manual dispatch; there is no schedule, so a vulnerability disclosed after the last push goes unnoticed. The repository has no Dependabot or Renovate configuration and no SBOM, and no document says how often, or by whom, locked Python dependencies are re-audited and upgraded after deployment. DEPLOYMENT.md:121 relies on unattended-upgrades, which patches nginx, OpenSSL and the OS Python, not the uv.lock wheels in the release venv. The three cooldown constraints in pyproject.toml:50 have no expiry, so they will keep blocking starlette>=1.8 even if a security fix lands there.

**Evidence.** `grep -rn -i 'pip-audit|vulnerab|osv|safety|audit' docs/*.md README.md .github/workflows/ci.yml` finds only DASHBOARD.md:109/469 and ci.yml:186-187, all about npm audit. ci.yml `on:` lists push [main], pull_request and workflow_dispatch, with no schedule. `ls .github` shows ci-requirements.txt and workflows only. No dependabot.yml or renovate.json has ever existed in history. Today pip-audit (PyPI service) finds 0 vulnerabilities in the 40 applicable packages and npm audit finds 0, so nothing is vulnerable now; the gap is that nothing would notice later.

**Reproduction.** cd <repo> && grep -n -E 'audit|schedule|cron' .github/workflows/ci.yml; ls .github; git log --all --format=%h -- .github/dependabot.yml renovate.json (empty). Current state: `uv export --frozen --format requirements-txt --no-emit-project > req.txt && uvx pip-audit -r req.txt --no-deps --disable-pip`.

**Consequence.** A long-lived private deployment will drift onto vulnerable FastAPI/Starlette/uvicorn/h11 versions with no signal; these HTTP stack libraries have a history of request-parsing and DoS advisories. Moderate advisories in react, zod or d3 would also pass CI silently. For a v1.0 'secure, professional' claim, the maintenance story is missing rather than wrong.

**Recommended repair.** Add a pip-audit step on the hash-pinned export to the quality job (alongside npm audit), and add a weekly `schedule:` trigger running both audits. Optionally add .github/dependabot.yml for the github-actions, pip/uv and npm ecosystems (security updates only, or a cooldown via `cooldown.default-days: 14`). Document in DEPLOYMENT.md or DEVELOPMENT.md how often the lock is re-audited and rebuilt into a release, and when the cooldown constraints are lifted. Consider lowering the npm gate for production dependencies (`npm audit --omit=dev --audit-level=moderate`).

**Verification.**

- supplychain#2 reproduce: **CONFIRMED**, severity MEDIUM. Every factual claim in the finding reproduced:
- CI runs only on push/PR/dispatch with no schedule.
- The only vulnerability gate is `npm audit --audit-level=high`. I showed directly that a moderate-only advisory passes it (exit 0).
- No Python vulnerability scanning exists anywhere: not in CI, the tests, deploy tooling or the monitor.
- No Dependabot/Renovate config or SBOM has ever existed in history.
- The documentation never says how or when locked Python wheels are re-audited or upgraded after deployment. The threat model's supply-chain row (DEPLOYMENT.md:636) covers integrity, not known vulnerabilities.
- unattended-upgrades cannot update the uv.lock wheels in the release venv (build-release.sh:120-126).
- The `starlette<1.8` cooldown constraint has no expiry, and I showed it blocks the 1.7.0 -> 1.8.0 upgrade.

The finder's own caveat also holds: pip-audit (PyPI) and npm audit find nothing vulnerable today. The defect is a missing maintenance and monitoring process, not a present vulnerability. The docs do not disclose it: DASHBOARD.md:484 only notes that the npm cooldown is a manual review rule, so already_disclosed is false.

Severity calibration:
- Practical exposure is reduced. nginx with TLS and HTTP Basic sits in front of every route, the API is loopback-only behind a header allow-list with 16 KiB bodies, nginx re-frames requests, and public-demo is not approved. A [...]
- supplychain#2 challenge: **PARTIALLY_CONFIRMED**, severity LOW. The core facts are correct, and I reproduced them.

- Nothing in CI, the tests or the docs checks the locked Python packages against an advisory database.
- CI has no schedule, so an advisory published after the last push would not be noticed.
- npm audit lets moderate advisories through.
- No Dependabot or Renovate configuration, SBOM or written re-audit policy has ever existed in the repository.
- The security table at DEPLOYMENT.md:636 does not list this as a residual risk, so it is not disclosed.
- DEPLOYMENT.md:121 overstates what unattended-upgrades covers: the release venv's wheels need a rebuild just like a container image does.

Three parts of the finding are weaker or overstated:

1. **Cooldown claim.** The finding says the constraint "will keep blocking starlette>=1.8". Starlette 1.8.0 is only 3 days old, so the constraint is doing exactly what the two-week policy says it should. At most this is a future maintenance note.
2. **Consequence.** The claimed consequence ("will drift onto vulnerable versions with no signal") is prospective. pip-audit and npm audit both show 0 known vulnerabilities today, and nothing is deployed.
3. **Severity under the real threat model.** In the private profile, nginx sits in front with TLS, Basic authentication on every route, a two-header allow-list, 16k body caps, buffering and rate limits. Most request-parsing and DoS advisory [...]

**Lead reconciliation.** Reproducer MEDIUM, skeptic LOW (no current vulnerability; pip-audit reports 0). Lead: MEDIUM, because the network-facing component of a deployable release has no monitoring at all and the cooldown caps never expire.

## M8-07

**Hash-verified research results and the API contract are not protected from CRLF conversion**

- Severity: **MEDIUM** (gate J); fix complexity: small; alters published research results: no
- Found by: release#1 (MEDIUM: Hash-verified research results and the API contract are not protected from CRLF conversion: on a core.autocrlf=true checkout the API is never ready and inspect/verify fail)
- Files: `.gitattributes`, `docs/DEVELOPMENT.md`, `src/atlas/api/reference.py`, `tests/test_snapshot.py`

**Description.** `.gitattributes` turns off line-ending conversion for `*.csv`, `*.ipynb`, `*.sql`, `snapshots/**`, `web/dist/**` and `web/src/test/fixtures/**` (.gitattributes:5-17). It does not cover the JSON/Markdown files in docs/results, which every run manifest fingerprints and the API verifies at startup (api/reference.py:97). Nor does it cover docs/api/openapi.json, which CI (ci.yml:106) and build-release.sh:167 compare byte for byte. DEVELOPMENT.md:344-351 says the attributes exist so that `core.autocrlf=true` (the Git for Windows default) cannot make every SHA-256 check fail, but the protection covers only part of the files that are checked. tests/test_snapshot.py:261 checks attributes only for snapshot-registered paths.

**Evidence.** `git check-attr text` gives 'unspecified' for docs/results/atlas-m4-optimization-v1/metrics.json, report.md, docs/api/openapi.json and configs/*.json. A scratch clone with `-c core.autocrlf=true` shows `git status --porcelain` with 0 changes, yet 29 of the 40 committed manifest-listed files no longer match their manifest SHA-256 (every config.json, metrics.json, report.md, selection.json, recommendation.json and calibration_status.json). docs/api/openapi.json gains 6111 CR characters. `atlas optimize inspect docs/results/atlas-m4-optimization-v1` FAILs with 3 errors ('config.json/metrics.json/report.md [file_altered]'). An in-process API via TestClient returns /health/ready 503: 'forecast_and_uncertainty_status … M2 run <repo>/docs/results/atlas-m2-rolling-origin-v1/full: its recommendation.json is missing or not the one the projection used'. /api/v1/scenarios returns 503 artifact_unavailable. The same script on an LF export answers 200 and 200.

**Reproduction.** S=<audit-scratch>/release; git clone -q -c core.autocrlf=true . $S/crlf; .venv/bin/python -I $S/hash_manifests_arg.py $S/crlf (recomputes every manifest output hash); PYTHONPATH=$S/crlf/src .venv/bin/python $S/api_ready.py $S/crlf (create_app plus TestClient against /health/ready and /api/v1/scenarios); PYTHONPATH=$S/crlf/src python -m atlas --repo-root $S/crlf optimize inspect docs/results/atlas-m4-optimization-v1

**Consequence.** A reviewer who clones the portfolio repository on Windows with default Git settings sees every data endpoint and dashboard view fail with 503, and inspect/verify report altered files. The failure is fail-closed (no wrong numbers are served), but the documented safeguard does not cover the files it exists to protect. The OpenAPI contract check and the M5-merge byte-identity test also fail on such a checkout.

**Recommended repair.** Add `docs/results/** -text`, `docs/api/openapi.json -text` and `configs/** -text` to .gitattributes. Extend the attribute test so it covers every file named in any docs/results manifest's `outputs`, plus the contract. Update DEVELOPMENT.md:344-351 to list them.

**Verification.**

- release#1 reproduce: **CONFIRMED**, severity MEDIUM. Every substantive part of the finding reproduced:
- The .gitattributes rules leave out docs/results, docs/api/openapi.json and configs.
- DEVELOPMENT.md:344-351 says SHA-256 checks are protected under core.autocrlf=true, but that is only true for snapshot, web/dist and fixture files.
- On an autocrlf=true checkout, git reports a clean tree, yet 29 of the 40 committed manifest-listed results fail their hashes.
- optimize inspect and uncertainty inspect FAIL with file_altered errors.
- The API never becomes ready (503 on /health/ready and /api/v1/scenarios).
- The byte-level contract comparisons used by ci.yml:106 and build-release.sh:167 fail.
- The M6 test that results are byte-identical to the M5 merge fails.

One sub-claim needs a nuance. The pytest contract test (test_m5_contract.py:40-45) still passes on a CRLF checkout, because Path.read_text() turns CRLF into LF when reading. Only the shell diff/cmp contract checks fail. This narrows the claim but does not change the conclusion.

Severity: MEDIUM, gate J. The failure is fail-closed: no wrong numbers are served. CI (ubuntu-24.04) and the Linux deployment target are not affected. The harm is to reproducibility and documentation accuracy:
- A reviewer who clones with Git for Windows defaults, a plausible hiring-manager scenario, gets an API and dashboard that are not ready, plus failing verify and inspect commands.
- The [...]
- release#1 challenge: **CONFIRMED**, severity MEDIUM. The core defect reproduces exactly as claimed. In a checkout with core.autocrlf=true (the Git for Windows installer default), git rewrites 29 of the 40 hash-verified research outputs and the OpenAPI contract, while `git status` stays clean. As a result, the API fails closed: 8 of the 12 parameterless GET endpoints return 503 and readiness is 503, so the dashboard has no data. `optimize inspect` reports `file_altered`, and the M6 byte-identity test fails. The proposed three-line .gitattributes repair fixes all of this, which I checked myself.

Points where I narrowed the finding:
(a) The pytest OpenAPI contract test still passes on a CRLF checkout. Only the shell byte comparisons fail: the ci.yml diff step and the build-release.sh cmp.
(b) CI runs only on Ubuntu, and the real deployment builds on a Linux server from `git archive` with a clean HOME. Gate N and CI are therefore unaffected, and no wrong numbers are ever served.
(c) DEVELOPMENT.md does not falsely claim that docs/results is protected; it states its scope accurately. Saying "the documented safeguard does not cover the files it exists to protect" goes too far. It is more accurate to say that the project's own rule (byte-verified files must be `-text`) is applied inconsistently.

The project clearly treats autocrlf as an environment it supports: it has a dedicated 'Line endings on Windows' section and explicit [...]

**Lead reconciliation.** Both verifiers CONFIRMED MEDIUM: on a core.autocrlf=true checkout (Git for Windows default) 29 of 40 manifest-listed files fail their hashes and the API never becomes ready.

## M8-08

**UNCERTAINTY.md §9.6 calls the flagged target days 'intake drops'; demand on all 18 was far above the forecast**

- Severity: **MEDIUM** (gate C); fix complexity: small; alters published research results: no
- Found by: uncertainty#1 (MEDIUM: UNCERTAINTY.md §9.6 says the flagged target days were 'intake drops'; demand on all 18 was far above the forecast)
- Files: `docs/UNCERTAINTY.md:419-423`, `docs/results/atlas-m3-intervals-v1/full/metrics.json (strata, flagged)`, `src/atlas/probabilistic.py:583`

**Description.** §9.6 reads: '...on the 18 target days with an M1 data-quality flag (26.5 % and 70.2 %; those are intake drops that the forecast does not anticipate, mean error −3,208)'. M3's error is prediction − actual (probabilistic.py:583; the same table calls holidays' +303.7 'over-forecast'). So −3,208 means the forecast was about 3,200 requests per day too LOW. The flagged days are agency-level flags: a DHS interruption of about 109 requests per day, DOB and EDC outages, and the city-wide surge of 2026-02-24. On those days city-wide demand surged, it did not drop. The doc describes the opposite of what happened in the worst-covered stratum, and frames forecast misses as data artefacts.

**Evidence.** flagged_days.py on the reproduced evaluation intervals (primary model, selected pair): 540 forecasts on 18 days; mean actual 13,428 vs mean forecast 10,220; mean(prediction−actual) = −3,207.8 (equal to the committed strata bias); actual exceeded the forecast on 18 of 18 days (2026-02-24: 22,806 vs 10,339); 29.8 % of these forecasts fell above q_0.975 and 0.0 % below q_0.025.

**Reproduction.** Run <audit-scratch>/uncertainty/full_repro.sh to regenerate m3-full, then `.venv/bin/python -I <audit-scratch>/uncertainty/flagged_days.py <scratch>/m3-full`. The script reads flagged_target_dates from the committed manifest and compares actuals with forecasts and quantiles.

**Consequence.** A reader (or hiring manager) is told the coverage collapse on flagged days comes from intake drops in the data. In fact the intervals and forecasts under-predicted demand surges, and the upper tail was far too light. This misstates a scientific result in the published results section.

**Recommended repair.** Rewrite the sentence, for example: 'on the 18 target days with an M1 data-quality flag (26.5 % and 70.2 %) demand was far above the forecast (it under-forecast by about 3,200 per day; 30 % of outcomes fell above the 97.5 % quantile, none below the 2.5 % quantile). These days include the 2026-02-24 series maximum and agency-level interruptions too small to move the city-wide total.' State the bias sign convention in the §9.6 table header.

**Verification.**

- uncertainty#1 reproduce: **CONFIRMED**, severity MEDIUM. The finding holds as described.
- **Numbers:** the published §9.6 figures are correct. I reproduced them bit-for-bit.
- **Explanatory sentence:** it is wrong in direction. Under the repo's own documented convention (error = prediction − actual), the −3,208 bias means the forecast was too low. On these 18 days city-wide demand was far above both the forecast and the same-weekday reference. Four of the days are flagged city-wide surges, including the series maximum. The other 14 carry agency-level interruptions: DHS about 109 per day and DOB about 270 on one day. Those drops are an order of magnitude smaller than the 3,208 miss and point the opposite way.
- **What the sentence suggests:** that the coverage collapse is a data artefact (drops). In fact the intervals' upper tail was far too light during demand surges: 29.8 % of outcomes landed above q_0.975 and none below q_0.025. That matters downstream, because M4 uses the upper quantiles.
- **Not disclosed elsewhere:** the low flagged-day coverage itself is disclosed (§9.6, §9.9). The cause and direction are not stated correctly anywhere for M3. DATA_QUALITY.md describes the flags correctly, which makes §9.6 contradict it.
- **Severity:** I keep MEDIUM on gate L. A published scientific narrative is wrong and contradicts the document's own sign convention, which hurts the scientific-honesty goal. It is not HIGH: the numbers, [...]
- uncertainty#1 challenge: **CONFIRMED**, severity MEDIUM. The core claim holds under my own checks, using raw data, my own script on the reproduced intervals, and the committed metrics. The §9.6 sentence calls the 18 flagged evaluation days "intake drops that the forecast does not anticipate". In fact the forecast was too low on every one of those days, and outcomes broke through the upper tail (29.8 % above q0.975, none below q0.025). The doc's own sign convention (lines 47-48, 420, 477) makes "−3,208" an under-forecast, so the sentence contradicts the table next to it.

The finder's description has two small inaccuracies, and correcting them makes the finding stronger:
- 4 of the 18 days are city-wide `surge` flags (2025-10-16, 2025-10-31, 2026-01-30, 2026-02-24), not just 2026-02-24.
- No EDC flag falls in the evaluation period.

So "intake drops" is false outright for 4 days. For the other 14 days the label matches the flag type (DHS/DOB `intake_interruption`), but it is still misleading: those agency drops (about 120/day and 250 on one day) are tiny and push the other way, while city-wide demand was far above the forecast.

Points against the finding that I checked:
- The code path is not in doubt: it is committed published results, and the numbers reproduce exactly.
- It is not already disclosed. The disclosed FAILED_CALIBRATION and small-sample caveats do not cover a wrong-direction interpretation, and DATA_QUALITY.md [...]

**Lead reconciliation.** Both verifiers CONFIRMED MEDIUM. Documentation only; the committed metrics are correct.

## M8-09

**API, M4 report and OPTIMIZATION.md call the archived July 2026 projection a 'FUTURE PROJECTION' with no archived/not-live disclosure**

- Severity: **MEDIUM** (gate C); fix complexity: small; alters published research results: no
- Found by: api#1 (MEDIUM: The API labels the July 2026 forecast as a 'FUTURE PROJECTION' and never says it is archived and not live); claims#5 (LOW: The API calls the July 2026 projection a 'FUTURE PROJECTION ... No outcome has been observed' and never says it is archived); optimization#2 (LOW: M4 report, OPTIMIZATION.md and API simulation limitations call the July 2026 horizon a 'FUTURE PROJECTION' without the archived/not-live disclosure)
- Files: `README.md:36-37`, `docs/API.md:8-17`, `docs/DASHBOARD.md:221`, `docs/OPTIMIZATION.md:46-59`, `docs/OPTIMIZATION.md:498-500`, `docs/results/atlas-m2-rolling-origin-v1/projection/report.md:3`, `docs/results/atlas-m4-optimization-v1/report.md:11`, `src/atlas/api/simulation.py:139-142`, `src/atlas/api/views.py`, `src/atlas/api/views.py:100-105`, `src/atlas/api/views.py:252`, `src/atlas/api/views.py:529`, `src/atlas/api/views.py:634`, `src/atlas/experiment.py:99`, `web/src/components/Tag.tsx:105-107`, `web/src/views/common.tsx:41-60`

**Description.** The disclosed limitation says forecasts are archived projections from data ending 2026-06-30, not live. The dashboard carries this ('Archived projection' tag; DASHBOARD.md:221 'Archived forecasts are not live'). The API does not, although it is the source of every number and is used directly (curl, Swagger, generated clients).

What the API says instead:
- Notice and label of /api/v1/forecasts, `scientific_status.forecast_label` in /api/v1/system, and the label of /uncertainty/intervals: 'FUTURE PROJECTION: forecasts beyond the last observed day. No outcome has been observed …' and 'FUTURE PROJECTION WITH INTERVALS …'.
- The projected target dates are 2026-07-01..2026-07-30, now 2.5–3.5 months in the past (today is 2026-10-16).
- No response, limitation list or simulation limitation says 'archived', 'not live' or gives the issue date as such; only `origin` appears in the data.
- docs/API.md's 'Read this first' box does not say it either.

**Evidence.** - `grep -rn -i 'archiv\|not live\|live data' src/atlas/api docs/API.md` gives no matches. The same grep finds the disclosure in docs/DASHBOARD.md:14,127,128,221,474 and web/src (Tag.tsx:105-107, App.tsx:213-214).
- The live GET /api/v1/forecasts?horizon_days=1 integrity notices are: ['FUTURE PROJECTION: forecasts beyond the last observed day. No outcome has been observed; these numbers are not validated by any backtest.', MAE notice, H1 not-significant notice, M3 FAILED notice].
- The /api/v1/system limitations list 5 items, none about the projection being archived.

**Reproduction.** `uv run atlas api serve --port 9160`, then `curl -s 'http://127.0.0.1:9160/api/v1/forecasts?horizon_days=1' | jq .integrity.notices` and `curl -s http://127.0.0.1:9160/api/v1/system | jq .data.scientific_status`. Then `grep -rn -i archiv src/atlas/api docs/API.md`.

**Consequence.** A hiring manager or third-party client reading the API sees a 'future' forecast for dates already past, with nothing saying it is a frozen archive and not a live service. The dashboard and the API disagree on how the same numbers are labelled. The disclosed limitation is missing from one of the places that shows the numbers.

**Recommended repair.** Add one API-level notice built from the loaded projection, without touching committed results. For example: 'ARCHIVED PROJECTION: issued from data ending {origin} for {first}..{last}; not a live forecast; outcomes after {origin} are not in the snapshot.'
- Add it to the forecast, intervals, scenario and simulation notices and to the /system limitations.
- Optionally add an `archived: true` / `issued_from` field to ScientificStatus.
- Add the sentence to docs/API.md's 'Read this first' box.
- Add a contract test asserting that the notice is present on every projected response.

**Independent second report (claims#5).** Disclosed limitation: forecasts are archived projections from data ending 2026-06-30. The dashboard states this itself ('An archived projection, not a live forecast ... it says nothing about today'). The API does not:
- GET /api/v1/forecasts, /uncertainty/intervals, /system and the scenario responses label the numbers only 'FUTURE PROJECTION: forecasts beyond the last observed day. No outcome has been observed; these numbers are not validated by any backtest.'
- No API string contains 'archived', 'not live' or the issue date as a warning.

At release (2026-10) and at any later private deployment, the target dates 2026-07-01..30 are in the past. A JSON client, or the public-demo API, therefore receives past dates labelled 'FUTURE' with 'No outcome has been observed', which is true only relative to the frozen snapshot. README.md:36-37 says 'Every data response says what its numbers are'.

**Independent second report (optimization#2).** All M4 plans cover 2026-07-01..2026-07-30, projected from data ending 2026-06-30. The release (2026-10-16) is about three months after that horizon. The dashboard tags these series 'Archived projection' (docs/DASHBOARD.md:221, 'Archived forecasts are not live'). The M4 surfaces do not:
- The committed report.md:11 says 'FUTURE PROJECTION: forecasts beyond the last observed day. No outcome has been observed'.
- OPTIMIZATION.md never uses the words archived or not live.
- Every interactive simulation's `limitations` (simulation.py:139-142) says only that the demand is an unvalidated point forecast treated as known.
Nothing is numerically wrong, and 'no outcome has been observed' is true of the frozen snapshot. A reader of the M4 report or an API consumer is not told that the planning horizon is in the past and that the forecast is an archived artefact.

**Verification.**

- api#1 reproduce: **CONFIRMED**, severity MEDIUM. Every factual claim in the finding reproduces.
- The API labels the July 2026 numbers 'FUTURE PROJECTION' / 'FUTURE PROJECTION WITH INTERVALS' in the forecast and intervals notices and labels, and in scientific_status.forecast_label.
- No API response, OpenAPI description, /system limitation or simulation limitation says the projection is archived or not live, or that it was issued from data ending 2026-06-30.
- The 'Read this first' box in docs/API.md does not say so either.
- The disclosure exists only in the dashboard (Tag.tsx, App.tsx), DASHBOARD.md and ROADMAP.md.
So one of the surfaces that serves the numbers does not carry a disclosed limitation, which the audit brief requires. The API's wording ('future') also differs from the dashboard's ('archived') for the same numbers. The dashboard even prints the API's 'FUTURE PROJECTION WITH INTERVALS' text beside its own 'Archived projection' tag (Uncertainty.tsx:170).

Severity, calibrated myself:
- Not HIGH. No number is wrong. The label is technically qualified ('beyond the last observed day', 'no outcome has been observed'), and origin and snapshot last_date are machine-readable in the responses. docs/API.md section 11 says the API serves committed runs as they are. In the private-first threat model the main human-facing surface is the dashboard, and it carries the disclosure.
- Still above cosmetic. 'FUTURE' on dates [...]
- api#1 challenge: **PARTIALLY_CONFIRMED**, severity LOW. Every fact in the finding holds; I reproduced each one. Neither the API responses (forecast, intervals, system, scenarios, simulations) nor docs/API.md say 'archived' or 'not live'. The API still serves the committed M2 'FUTURE PROJECTION' label for target dates now 2.5 to 3.5 months in the past, while the dashboard relabels the same data 'Archived projection, not a live forecast'. That is a real gap against the audit's rule that every place showing the numbers carries the disclosed limitation. The API already rewrites a committed label when it would mislead (INTERVALS_LABEL, views.py:97-105), so the proposed repair fits its own design.

I lowered the severity from MEDIUM to LOW because the claimed consequence is overstated:
1. No statement is false. 'FUTURE PROJECTION' is defined as 'forecasts beyond the last observed day', relative to the data, and 'No outcome has been observed' is true for the snapshot.
2. The staleness is machine-readable in every projected response: origin 2026-06-30 is documented as the last observed day, and the target dates are explicit. /system gives snapshot.last_date, and the history, metrics and models notices call the snapshot 'frozen'. So 'nothing saying it is a frozen archive' is only true of the forecast-family responses themselves.
3. At repository level the not-live nature is disclosed: README frozen snapshot and committed outputs, API.md 'no [...]
- claims#5 reproduce: **CONFIRMED**, severity LOW. The finding's central claim holds, and I reproduced it myself. Every API response and the OpenAPI document label the July 2026 numbers "FUTURE PROJECTION" (on /forecasts and /system adding "No outcome has been observed"). Nothing in the API says the projection is archived or not live, and no notice gives the issue date as a warning. Today is 2026-10-16, so every target date (2026-07-01..30) is already in the past.

The dashboard adds the "archived projection, not a live forecast" warning on the client side, so API-only consumers (curl through the tunnel, public-demo JSON clients) depend on the frozen-snapshot wording alone.

This is a real gap between the API and the dashboard and docs. It sits awkwardly with README.md:36-37 ("Every data response says what its numbers are"), and docs/API.md does not record it as a limitation. For those reasons I set already_disclosed=false: the underlying limitation (archived projections) is disclosed, but this API-side omission is not.

One sub-claim is inaccurate. The scenarios responses do not carry the "FUTURE PROJECTION" label; they carry the M4 HYPOTHETICAL label. They still lack any archived signal, though, so the core claim stands.

I agree with LOW (gate E/L) and would not raise it, for three reasons:
- The wording is literally true relative to the snapshot.
- Each response includes origin=2026-06-30, and /system includes [...]
- optimization#2 reproduce: **CONFIRMED**, severity LOW. Every factual claim in the finding holds up when I check it myself:
- The M4 report, OPTIMIZATION.md and the API simulation limitations never say the July 2026 demand is an archived projection rather than a live one.
- report.md:11 says "FUTURE PROJECTION", in a report generated on 2026-10-15 about a horizon that ended 2026-07-30.
- I ran the API simulation: it returns dates 2026-07-01..2026-07-30 and its six limitations do not mention it.

The gap is a little wider than the finding claims. The API /forecasts and /system responses, README.md and docs/API.md never use "archived" or "not live" either; only the dashboard frontend and DASHBOARD/ROADMAP/BACKTESTING do. One sub-claim is overstated: OPTIMIZATION.md shows no dated plans, so the "dated plans taken as current" risk sits mainly in report.md and the API.

LOW is the right severity:
- No number is wrong.
- "No outcome has been observed" is true for the frozen snapshot.
- The explicit dates and snapshot range let a careful reader work out that the horizon is in the past.
- The dashboard footer on every page, the M4 views included, carries the disclosure.
- Nothing contradicts the disclosure outright; it is just missing on these surfaces. "FUTURE PROJECTION" is relative to the snapshot, not to the release date.

Still, this is a disclosed limitation that does not reach every place showing the related numbers, which the audit [...]

**Lead reconciliation.** Found by three lenses. Votes: MEDIUM, LOW, LOW, LOW. Facts undisputed. Lead: MEDIUM, because the same disclosed limitation is missing from a whole surface (every API response and the M4 documentation), not from one string; the dashboard alone carries it.

## M8-10

**Anomaly flags miss most of the winter 2025-26 anomaly cluster; docs overstate robustness to anomalies**

- Severity: **LOW** (gate L); fix complexity: small; alters published research results: no
- Found by: data#2 (LOW: Anomaly flags miss most of the winter 2025-26 anomaly cluster; docs and the M2 flagged-target sensitivity overstate robustness to anomalies)
- Files: `docs/BACKTESTING.md:917-919`, `docs/DATA_QUALITY.md:117`, `docs/DATA_QUALITY.md:87-91`, `src/atlas/evaluation.py:873-882`, `src/atlas/quality.py:193-226`, `src/atlas/quality.py:255-283`, `src/atlas/quality.py:617-623`

**Description.** The surge score uses the median and MAD of the eight preceding same-weekday values. Around the holiday season that window mixes holiday-week lows with December peaks, which inflates the scale enough to hide large surges. The interruption detector needs the 10th percentile of the previous 28 days to be at least 50. It is therefore blind to a second outage within four weeks of a first one. DATA_QUALITY.md:90 says 'The median and MAD resist earlier anomalies', and the surge table lists only flagged days. The M2 sensitivity 'exclude_flagged_targets' (BACKTESTING.md:917-919: 'excluding them changes the size but not the direction of any comparison') removes the 44 warning-flag dates. In the confirmation year these are dominated by the DHS outage. DHS is 1.42% of five-borough volume. The most anomalous city-wide days stay in.

**Evidence.** surge.py and an inline script compute the same 16 surge days as the package, so the implementation is faithful.
- Series-wide, 61 days are at least 25% above their same-weekday median and only 16 are flagged.
- In 2025-07..2026-06 there are 24 such days and only 4 are flagged as surges.
- 3 of the 5 largest days of the whole series are unflagged:
  - 2026-01-27: 19,225, +70% vs 11,306. Score 4.40 because the prior Tuesdays (8,354 to 14,078) give MAD 1,215 and scale 1,801.
  - 2026-01-28: 16,982, +62%, score 2.58.
  - 2025-12-15: 16,388, +43%, score 3.76.
- runner._flagged_dates returns 44 dates, equal to flagged_target_dates in docs/results/atlas-m2-rolling-origin-v1/full/manifest.json. Of these, 18 fall in the confirmation year and 13 of those are DHS 2026-01-31..02-12. The 2026-01-24..01-29 cluster is not excluded; 10 of the 24 at-least-25% days are excluded, 6 only because they coincide with the DHS outage.
- agencies.py: DHS falls to 0, 0, 3, 3 on 2026-02-22..25 (normally 60-100 per day). This is not flagged because the 28-day reference includes the first outage.

**Reproduction.** PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -I <audit-scratch>/data/scripts/surge.py (prints every scored day for 2026-01-20..02-05 and the flagged set) and .../scripts/agencies.py (DHS daily counts 2026-01-20..03-15).

**Consequence.** The data-quality flags are not a complete anomaly inventory. M2's 'excluding flagged targets' result and M3's 'flagged' stratum therefore do not show robustness to city-wide anomalies, although readers may take them that way. Primary metrics are unaffected because they keep all days.

**Recommended repair.** Disclosure only, so pinned results are unchanged. In DATA_QUALITY.md, replace 'The median and MAD resist earlier anomalies' with the detectors' known power limits: MAD inflation over the holiday season, and an interruption shortly after another being undetectable. List the unflagged extreme days, at least 2026-01-27, 2026-01-28 and 2025-12-15. Qualify BACKTESTING.md:917-919 to say the sensitivity covers M1-flagged days only, and that the flagged set is dominated by agency-level DHS days. Optionally pre-declare a future secondary sensitivity based on relative deviation.

**Verification.**

- data#2 reproduce: **CONFIRMED**, severity LOW. Every number in the finding reproduces independently: 16 surge days, 61/16, 24/4, the top-5 days and their scores, MAD and scale, 44 dates equal to the manifest, 18/13 DHS, 6 excluded only through DHS, 1.42%, and the DHS dips of 0, 0, 3, 3 with q10 = 0.

Both mechanisms are real properties of the code:
- At quality.py:213 the MAD inflates when holiday lows and December peaks share the 8-week window.
- At quality.py:276-277 the q10>=50 gate disarms the interruption detector for about 7 weeks after an outage, until 2026-03-24.

None of this is a coding bug; the implementation does what it documents. The problem is in the documentation:
- DATA_QUALITY.md:90 claims robustness without stating the detectors' power limits, and the second DHS outage goes unmentioned.
- BACKTESTING.md:917-919 is literally scoped to "(M1 flags)", but it sits under "Year-end and anomalies" with no note that the flagged set is dominated by agency-level DHS days, while the most extreme city-wide days stay in.

I also found a related misstatement, which supports the "readers may misread the flagged stratum" consequence. UNCERTAINTY.md:421-422 calls the 18 flagged M3 evaluation days "intake drops". Under the repository's own bias convention, the -3,208 mean error is an under-forecast, and my computation shows all 18 days had city-wide volume above reference (mean +32%).

Severity stays LOW:
- The primary [...]

## M8-11

**Stale status and stale statements after the M7 merge (ROADMAP, DASHBOARD, DATA_SNAPSHOT, README notice heading)**

- Severity: **LOW** (gate L); fix complexity: small; alters published research results: no
- Found by: data#3 (LOW: Stale or imprecise statements in the data documentation and dashboard methods text); claims#3 (LOW: Stale status after the M7 merge: ROADMAP and DASHBOARD say M7 is unmerged and awaiting review; the README notice is headed '(milestone M0)')
- Files: `README.md:40-41`, `README.md:5`, `README.md:7-9`, `docs/DASHBOARD.md:10`, `docs/DATA_QUALITY.md:126-127`, `docs/DATA_SNAPSHOT.md:123-125`, `docs/DATA_SNAPSHOT.md:15`, `docs/DATA_SNAPSHOT.md:160-170`, `docs/DEVELOPMENT.md:79`, `docs/ROADMAP.md:218`, `docs/ROADMAP.md:3-6`, `web/src/views/Provenance.tsx:30`

**Description.** Five statements are stale or imprecise:
1. DATA_SNAPSHOT.md:15 offers `uv run pytest -m integration --no-cov` as 'the same facts as automated tests'. The integration marker now selects 41 test files, 31 of them M2-M7 suites (long M4/M5 runs), not the snapshot facts.
2. DATA_SNAPSHOT.md:123-125 says breaking down the residual 'requires a new extraction (M1)'. M1 did not do it, and ROADMAP.md:36-38 defers it as not authorised.
3. DATA_SNAPSHOT.md:160-170 says the HPD gap and extreme days are 'not checked by these commands' and need an anomaly-flagging policy 'planned for M1'. M1 implemented it: `atlas quality` flags HPD 2024-04-11..14 and 2026-02-24.
4. DATA_QUALITY.md:126-127 says the HPD catch-up 'offsets about 72 %' of the missing volume. That uses a weekday-blind reference (4 x 1,902 = 7,608 for Thu-Sun), although HPD weekends run about 1,485 vs about 2,500 on Mondays.
5. Provenance.tsx:30 claims 'reproduction of every legacy figure'. The RMSE is a declared non-reproduction (676.69 vs 676.70), and M0 treats the model predictions as inputs.

**Evidence.** - `grep -rl pytest.mark.integration tests | wc -l` returns 41, of which 31 match test_m[2-7].
- hpd.py:
  - Prior-4-week same-weekday medians: Thu 2,213.5, Fri 1,883.5, Sat 1,194, Sun 1,009.5, so 6,300.5 expected.
  - Monday 04-15 expected 1,999.5, so the excess is 7,398 - 1,999.5 = 5,398.5, which offsets about 86% (vs 72% with the docs' method).
- known_exceptions.json LEGACY-V0-EX-002 records 676.70 committed vs 676.69 recomputed.
- `atlas quality` output lists the HPD intake_interruption/catch_up_surge flags and the 2026-02-24 surge.

**Reproduction.** .venv/bin/python -I <audit-scratch>/data/scripts/hpd.py ; grep -rl 'pytest.mark.integration' tests | wc -l ; sed -n 120,170p docs/DATA_SNAPSHOT.md

**Consequence.** Readers get outdated pointers: work described as planned is already done, deferred work is attributed to M1, and a 'reproduce the facts' command runs for a long time. One quantitative statement understates the catch-up share. The impact is cosmetic and minor, but it is visible to reviewers checking provenance.

**Recommended repair.** Point DATA_SNAPSHOT.md:15 at the M0 test files, or at `atlas check` alone. Change '(M1)' to 'deferred; see ROADMAP'. Rewrite the 'Other observations' section to reference `atlas quality` and DATA_QUALITY.md. Either state the HPD estimate's reference ('about 72-86 % depending on the reference') or use the same-weekday reference. Change the dashboard text to 'reproduction of every legacy figure within published precision, except one declared RMSE rounding'.

**Independent second report (claims#3).** On main at 194fa4a (the merge of PR #9, feature/atlas-m7-dashboard), several status statements are wrong or self-contradictory:
- ROADMAP.md:4-6: 'M7 (the web dashboard) is implemented on `feature/atlas-m7-dashboard` and awaits review.'
- ROADMAP.md:218: '## M7: Interactive web dashboard (implemented; awaiting approval)'.
- DASHBOARD.md:10: 'Status: implemented on `feature/atlas-m7-dashboard`, awaiting review.'
- README.md:5: the bold lead 'ATLAS engineering foundation (milestone M0).' introduces a notice that now covers M0-M7.
- README.md:40-41: 'a private-first, verified deployment of the API on one small Ubuntu 24.04 server' reads as an executed deployment, yet line 44 says 'Nothing has been deployed'.
- DEVELOPMENT.md:79: 'Nothing is deployed publicly' implies a private deployment exists; DEPLOYMENT.md:9 says nothing has been deployed to any server.
- README.md:7-9: says only the notebook link was corrected and the tree 'shows the original layout', but the tree's notebook filename was corrected too.

**Verification.**

- data#3 reproduce: **PARTIALLY_CONFIRMED**, severity LOW. Items 1, 2 and 3 reproduce exactly.
- DATA_SNAPSHOT.md has not been updated since M0, so it still describes as "planned for M1" work that M1 delivered. That includes the HPD and 2026-02-24 flags, which `atlas quality` outputs.
- It also attributes to M1 an extraction that ROADMAP defers.
- Its "same facts as automated tests" command now selects 612 tests, about 91% of them M2-M5. The integration marker definition in pyproject.toml is broad enough that this was predictable.

Item 4 holds only in a weaker form. The docs' 72% is computed transparently with a stated, weekday-blind reference. The finder's 86% depends on choosing a 4-week same-weekday window. ATLAS's own 8-week same-weekday convention gives about 69%. So the claim that the docs understate the share is refuted. A note that the share depends on the reference would still be a modest improvement.

Item 5 is a mild scope overstatement on the M0-M1 card. The RMSE exception is declared in the linked doc, and M2 does reproduce 676.70 and all published predictions. So "every legacy figure" is close to true at project level.

None of this affects data integrity, the numbers in results, or security. These are stale or imprecise prose pointers in the documentation and in one dashboard sentence, which makes LOW (gate L) the right severity. Overall: three of five sub-claims fully confirmed, one partially confirmed, and one [...]
- claims#3 reproduce: **CONFIRMED**, severity LOW. I reproduced every quoted line exactly at the cited place on HEAD 194fa4a, the merge commit of PR #9.

The core claim holds. The canonical status documents still describe M7 as unmerged and awaiting review:
- ROADMAP.md:4-6
- ROADMAP.md:218, the only milestone heading not marked "(merged)"
- DASHBOARD.md:10

The README notice is still headed "(milestone M0)" (README.md:5), although it now covers M0-M7. The merge commit's tree is identical to the branch tip, which confirms the text was written before the merge and never updated. README.md:7-9 also understates the edits made to the legacy README: the git diff shows that the tree's notebook file name was corrected as well as the link.

Two sub-items are weaker than the finding says:
- README.md:40-41 is ambiguous, not contradictory. Its bold label reads "deployment package", and the same paragraph says at line 44 that nothing has been deployed.
- DEVELOPMENT.md:79 ("Nothing is deployed publicly") is literally true. It is only less precise than DEPLOYMENT.md:9.

These are loose wording, not real internal contradictions. Every factual observation in the finding still reproduces.

On impact: none of this reaches the dashboard build (web/dist has no stale status text), and it has no effect on correctness, security or the scientific numbers. It is a provenance and professionalism defect in the documents a reviewer reads first. It is [...]

## M8-12

**The first documented M2 verify command fails with the current code**

- Severity: **LOW** (gate J); fix complexity: small; alters published research results: no
- Found by: forecast#2 (LOW: The first documented M2 verify command fails with the current code; BACKTESTING.md contradicts itself)
- Files: `docs/BACKTESTING.md:15`, `docs/BACKTESTING.md:817-822`, `docs/results/atlas-m2-rolling-origin-v1/development/manifest.json`

**Description.** The quick-start block at the top of BACKTESTING.md tells readers to run `uv run atlas backtest verify docs/results/atlas-m2-rolling-origin-v1/development`. Section 8 of the same document says that directory was produced by commit a16672f and verifies byte for byte only at that commit, because the reporting code changed after the review. With HEAD (194fa4a) the command exits 1 with 4 errors. The underlying forecasts reproduce; only the reporting files differ.

**Evidence.** atlas --format json backtest verify <copy of the development dir> with HEAD code: verification ok=false. output_differs coefficients.csv (82af9a58… vs recorded 958dc80a…), metrics.json (dbf3a1e6… vs 97e8dc31…), report.md (b6f6b71c… vs b8d2b0b7…); result_fingerprint_differs (95f538df… vs fbb948ec…). forecasts.csv, fits.csv, observed_inputs.csv and config.json are identical. The same directory verified PASS (fingerprint fbb948ec…) with code exported from a16672f, and the full reference run verifies identically with HEAD.

**Reproduction.** S=<scratch>; git -C . archive HEAD | tar -x -C $S/tree; cd $S/tree && PYTHONPATH=$S/tree/src .venv/bin/python -m atlas --repo-root $S/tree --format json backtest verify $S/tree/docs/results/atlas-m2-rolling-origin-v1/development (log: <scratch>/dev_verify.log). Control: git archive a16672f into $S/tree_a16672f, then verify a copy of the development dir there (log: <scratch>/dev_verify_a16672f.log, PASS).

**Consequence.** The first reproducibility command a reader or reviewer copies from the M2 document prints FAIL, which undermines the 'reproducible' claim even though the results do reproduce. No published number is wrong.

**Recommended repair.** Change BACKTESTING.md:15 to a command that passes with the current code: verify a freshly produced run (`atlas backtest verify runs/dev`), or regenerate and verify the full reference run. Alternatively annotate that the sealed development record verifies only at a16672f (git archive a16672f). Optionally add a scheduled or release CI job that regenerates and verifies the full reference run (~5 CPU-min).

**Verification.**

- forecast#2 reproduce: **CONFIRMED**, severity LOW. The behaviour reproduces exactly as claimed. With the current code, the first reproducibility command in docs/BACKTESTING.md:15 exits 1 with 4 errors. The same directory verifies cleanly with the a16672f code, and the result fingerprint matches the recorded fbb948ec. The forecasts, fits, observed inputs and config reproduce byte for byte at HEAD; only the reporting files differ: coefficients.csv, metrics.json, report.md and the fingerprint. So no published forecast number is shown to be wrong.

BACKTESTING.md does contradict itself. Section 8 (lines 817-822) accurately says the sealed development record verifies only at a16672f, but the quick start at line 15 presents the same command with no caveat. The section 8 recipe is also incomplete: a worktree at a16672f does not contain the development directory, because it was added in 10343e2.

The underlying fact is disclosed in section 8. The defect being reported is the undisclosed, contradictory quick-start command, so already_disclosed=false.

Severity LOW, gate J:
- it is a documentation and reproducibility-UX defect;
- scientific integrity is intact, since the sealed record does verify at its own commit;
- it is still worth fixing before a v1.0 portfolio release, because the first command a reviewer copies prints FAIL.

The proposed repair is appropriate: point line 15 at a fresh run or the full reference run, or annotate it. [...]

## M8-13

**M3 conclusions rest on p-values not adjusted for multiplicity, one printed next to the disclosed M2 limitation**

- Severity: **LOW** (gate C); fix complexity: small; alters published research results: no
- Found by: uncertainty#2 (MEDIUM: M3 results state conclusions from unadjusted p-values, and print an ATLAS-vs-B* p<0.001 next to the disclosed limitation without its Holm caveat)
- Files: `docs/UNCERTAINTY.md:329-349`, `docs/UNCERTAINTY.md:381-383`, `docs/UNCERTAINTY.md:462-464`, `docs/results/atlas-m3-intervals-v1/full/metrics.json (comparisons)`

**Description.** M2 pre-registered a Holm-adjusted family, and the project discloses that the M2 model's lead over B* is not significant after Holm. M3 reports 10 paired WIS tests in §9.4, three Brier tests in §9.5 and 250 comparison rows × 3 metrics in metrics.json, with no multiplicity control and no statement that they are unadjusted. It then draws conclusions from single p-values:
- 'Against climatology ... (p 0.008, effective sample size 12): the forecast carries information about the coming month that the recent distribution of demand does not' (346-347);
- 'Probabilities for the historical high-demand threshold beat climatology in Brier score' (462, p 0.038, effective n 11);
- 'the ATLAS regression's intervals beat B*'s by 8.5 % in WIS (p < 0.001 ...), consistent with M2's point accuracy ranking' (348-349).
The last compares the same two models on the same (M2 confirmation) period as M2's H1. It does not mention that H1 is not significant after Holm, nor that the same comparison on the M3 selection period gives p 0.27.

**Evidence.** Holm over the 10 tests of the §9.4 table, from committed metrics.json comparisons (days_1_30): 0.0002→0.002 (ATLAS vs B*), 0.0011→0.010, 0.0083→0.0665 (ATLAS vs climatology, no longer < 0.05). Brier over the 3 thresholds: 0.0375→0.113. M2 recommendation.json H1 (confirmation): MAE difference −127.3, CI −256.4 to +1.9, Holm p 0.0535, effective n 13.8. M3 evaluation ATLAS vs B* (same method) WIS p 0.000204, effective n 54.1. Selection period: p 0.266.

**Reproduction.** `.venv/bin/python -I` with a short script that loads docs/results/atlas-m3-intervals-v1/full/metrics.json, filters comparisons with label=='days_1_30' and pair_id=='empirical_relative@fixed365', applies Holm to wis_p_conservative, and reads docs/results/atlas-m2-rolling-origin-v1/full/recommendation.json primary_tests (commands and output are in this audit's transcript).

**Consequence.** A reader can conclude that the regression is significantly better than B*, or that the intervals demonstrably beat climatology. The first undercuts the disclosed 'not significant after Holm' limitation; the second does not survive a multiplicity adjustment within the table M3 itself prints. This is inconsistent with the project's own M2 standard of scientific honesty.

**Recommended repair.** Add one sentence in §8/§9.4 stating that the M3 comparisons are descriptive and not adjusted for multiplicity (or print Holm-adjusted p within the shown family). Soften 'carries information' and 'beat climatology' to 'suggests'. Next to the ATLAS-vs-B* WIS line, add: 'M2's pre-registered test of the same pair on point accuracy (H1) is not significant after Holm (p 0.054); the selection-period WIS difference was not significant (p 0.27).' No re-run is needed.

**Verification.**

- uncertainty#2 reproduce: **PARTIALLY_CONFIRMED**, severity LOW. Confirmed:
- All the finder's numbers reproduce exactly.
- M3 has no multiplicity or "descriptive/unadjusted" statement anywhere (UNCERTAINTY.md, report.md, metrics.json), while M2 states its policy explicitly.
- docs/UNCERTAINTY.md:348-349 puts a WIS p<0.001 next to "consistent with M2's point accuracy ranking". It does not say that M2's H1, on exactly the same 10,080 forecasts (which I recomputed: p 0.0535), is not significant, or that the selection-period WIS comparison gave p 0.27. That context is missing next to the project's headline disclosed limitation.

Overstated by the finder:
- The ATLAS-vs-B* WIS result is not fragile. It survives Holm in every family (0.001 to 0.005) and every HAC bandwidth (p ≤ 0.00085). "The intervals beat B*'s in WIS" is a supported claim about the probabilistic score. It does not contradict the disclosure, which concerns point accuracy (MAE). Only the missing contrast with H1 is a defect.
- "Does not survive a multiplicity adjustment within the table M3 prints" depends on the family chosen:
  - The finder's family mixes the selection and evaluation periods (10 tests); there ATLAS vs climatology goes 0.008 -> 0.067.
  - The more natural family is the 5 evaluation-period tests the claims are about; there it stays significant (0.033), and it is robust across bandwidths. "Carries information" against a 180-day climatology is also unsurprising [...]
- uncertainty#2 challenge: **PARTIALLY_CONFIRMED**, severity LOW. Core defect, confirmed: M3 prints about 13 headline tests (750 p-values in metrics.json) and draws prose conclusions from them. Nowhere in docs/UNCERTAINTY.md, report.md or metrics.json does it say these p-values are unadjusted for multiplicity. M2 says this explicitly (BACKTESTING.md:279 and :500, plus recommendation.json's "multiplicity" field), so M3 is inconsistent with the project's own M2 standard.

The ATLAS-vs-B* WIS line (348-349) is a closely related comparison of the same pair on the same confirmation period. It carries no cross-reference to the disclosed limitation that M2's H1 is not significant (p 0.054, which is also the raw value). The project asks for that limitation wherever related numbers appear.

Several parts of the finding do not hold up. The ATLAS-vs-B* p<0.001 survives any Holm family, so multiplicity is not its problem. The sentence claims only consistency with M2's ranking, which is true, and the contrary selection-period p 0.266 sits in the same table a few lines above. The climatology conclusion fails Holm only under a 10-test family that pools the selection period; in the evaluation-period family it survives (0.033), and it is substantively almost certain because climatology ignores the strong weekday pattern. The Brier claim follows the protocol's pre-registered per-threshold p < 0.05 criterion and is surrounded by heavy caveats (p 0.038, eff n [...]

**Lead reconciliation.** Finder MEDIUM; both verifiers PARTIALLY_CONFIRMED LOW.

## M8-14

**The coverage gate's 'conservative' p-value is the lenient choice for a calibration claim**

- Severity: **LOW** (gate C); fix complexity: small; alters published research results: no
- Found by: uncertainty#3 (LOW: The coverage gate's 'conservative' p-value is the lenient choice for a calibration claim)
- Files: `docs/UNCERTAINTY.md:205`, `docs/UNCERTAINTY.md:92-95`, `src/atlas/probabilistic.py:1156-1158`, `src/atlas/scoring.py:146`, `src/atlas/stats.py:253`

**Description.** The 'coverage not rejected' criterion uses p_value_conservative = max(p_rectangular, p_Bartlett). The interval uses the larger standard error. This choice is conservative in M2, where rejection supports a positive claim (model beats baseline). In M3, non-rejection supports the positive claim (coverage is consistent with nominal), so taking the larger p makes the gate easier to pass, the opposite of conservative. The documentation still calls it 'conservative'. It does not bind in the reference run: the smaller p-values are 0.989 and 0.445, so the status is unchanged.

**Evidence.** conservative_direction.py (calls atlas.stats.diebold_mariano read-only) on 4,000 synthetic AR(0.97) per-origin coverage series of length 336 with h=30: 133 series where one pre-registered HAC estimator rejects nominal coverage at 5 % but the gate's p does not (for example seed 38: p_rect 0.0688, p_Bartlett 0.0486, gate p 0.0688). Reference run: p_rect 0.9893 vs p_Bartlett 0.9890 (80 %); 0.4584 vs 0.4451 (95 %).

**Reproduction.** `cd <scratch> && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python <audit-scratch>/uncertainty/conservative_direction.py`

**Consequence.** With other data (for example a future snapshot) the decision-grade gate could pass a coverage series that one of its own pre-registered estimators rejects. The 'conservative' label misinforms reviewers.

**Recommended repair.** Now: correct the wording in UNCERTAINTY.md §4/§8 (for the coverage test the larger p is the lenient choice; it did not bind: the smaller p-values are 0.989/0.445). In a future protocol version, reject when either estimator rejects (min p). Do not change the frozen v1 protocol after the fact.

**Verification.**

- uncertainty#3 reproduce: **CONFIRMED**, severity LOW. The behaviour reproduces independently. The M3 coverage gate counts coverage as rejected only when the larger of the two pre-registered HAC p-values is below 0.05, which means both estimators must reject.

For this gate, non-rejection is the pass condition. Taking max p lowers power against miscalibration, so it makes the gate easier to pass.

My simulations show that this can bind. Some series pass every other gate condition (tolerance and eff_n ≥ 30), and the Bartlett-2h estimator, one of the protocol's own estimators, rejects them, yet the gate still passes them. In those settings, roughly 15-20 % of passes depend on the max-p choice.

One nuance on the wording. In standard testing language a "conservative" test is one whose size is at or below nominal, and the max-p rule does achieve that. So the label is not mathematically wrong. It is still misleading in UNCERTAINTY.md:205, where a reader would take "conservative" to mean a stricter calibration gate, and that is the opposite of the effect. The finder's claim that the label "misinforms reviewers" is fair as a wording and clarity issue, not a math error.

The choice does not bind in the reference run (smaller p-values 0.989 and 0.445, from committed sensitivity rows). The status is FAILED_CALIBRATION and eff_n is about 8-9 in any case. The protocol is frozen and sealed, and fixing it means a wording change now and a [...]

## M8-15

**Projection artefacts misdescribe the calibration window; projection CSV rows carry no status**

- Severity: **LOW** (gate C); fix complexity: small; alters published research results: no
- Found by: uncertainty#4 (LOW: Projection artefacts misdescribe the calibration ('every residual observed by then', 'calibrated on past errors'); the CSV rows carry no status)
- Files: `docs/API.md:408-409`, `docs/results/atlas-m3-intervals-v1/projection/projection_intervals.csv`, `docs/results/atlas-m3-intervals-v1/projection/report.md:3`, `docs/results/atlas-m3-intervals-v1/projection/report.md:8`, `src/atlas/uncertainty_runner.py:101-105`, `src/atlas/uncertainty_runner.py:1011`, `src/atlas/uncertainty_runner.py:819-822`

**Description.** The projection report and the `project` docstring say the sealed method is 'calibrated at the origin on every residual observed by then'. With fixed365 it uses only the residuals with targets in the preceding 365 days. M3's PROJECTION_LABEL, printed in bold as the report's first line and stored as the manifest label, says the intervals and probabilities are 'calibrated on past errors'. docs/API.md:408-409 itself notes that this label 'describes the method as calibrated' and replaces it in the API. Each row of projection_intervals.csv and projection_exceedance.csv carries support=supported and status=projected but no calibration-status column, so the FAILED_CALIBRATION flag lives only in manifest.json and report.md.

**Evidence.** projection_intervals.csv row h=1: cal_n=365, cal_first_target=2025-07-01, cal_last_target=2026-06-30, while residuals exist from target 2024-02-02 (forecasts.csv). UNCERTAINTY.md:439 states the 365-day window correctly, contradicting report.md:8. The report.md header line 3 contains 'calibrated on past errors' directly above 'Calibration status: FAILED_CALIBRATION'.

**Reproduction.** `head -2 docs/results/atlas-m3-intervals-v1/projection/projection_intervals.csv; sed -n 1,8p docs/results/atlas-m3-intervals-v1/projection/report.md; sed -n 404,410p docs/API.md`

**Consequence.** A reader of the projection report or of a CSV extract gets a false description of the calibration window. A CSV consumer sees probabilities and 'supported' quantiles without the FAILED_CALIBRATION flag. The API and dashboard are not affected.

**Recommended repair.** Fix the f-string at uncertainty_runner.py:1011 and the docstring at 820-821 to 'calibrated at the origin on the residuals with targets in the preceding <window> days'. Reword PROJECTION_LABEL to 'built from past forecast errors' for future runs. Optionally add a calibration_status column to the projection CSVs in a future run. For v1.0, add an erratum line in UNCERTAINTY.md §9.8 instead of regenerating the committed artefacts, whose CSV hashes are pinned by M4 and the API.

**Verification.**

- uncertainty#4 reproduce: **CONFIRMED**, severity LOW. Every part of the finding reproduces.

- **Window description:** I checked the code (calibration.py:198 with window_days=365) and the committed data (cal_first_target=2025-07-01 when residuals reach back to 2024-02-02). Together they show that "every residual observed by then" in report.md:8 and in the docstring at uncertainty_runner.py:821 is wrong.
- **Projection label:** The repo's own M4/M5 wording lint rejects the label at report.md:3 and manifest.json:242 ("calibrated on past errors" and an unnegated "probabilities"), and docs/API.md:409 admits the label is misleading.
- **CSV status:** The projection CSV rows carry no calibration-status column.

**Severity: LOW, not higher.**
- The FAILED_CALIBRATION banner in report.md sits right below the label, in bold, and says "not decision-grade".
- The API and dashboard swap in an accurate label (views.py:100).
- UNCERTAINTY.md:439 gives the correct 365-day window.
- No number is wrong; only the wording in a secondary artefact is.
- Leaving the status out of the CSVs is a provenance and documentation weakness, not a correctness bug: the manifest that sits next to them, and is pinned, carries the status.

**Why already_disclosed is false.** UNCERTAINTY.md gives the correct window but never says the report's wording is wrong. API.md only explains why the API does not reuse the label. Neither is an erratum for the committed report [...]

## M8-16

**`fixed365` is documented as a 365-day calibration but held 153-182 residuals in the selection period**

- Severity: **LOW** (gate C); fix complexity: small; alters published research results: no
- Found by: uncertainty#5 (LOW: `fixed365` is documented as a 365-day calibration, but on the selection period it held only 153-182 residuals)
- Files: `docs/UNCERTAINTY.md:116`, `docs/UNCERTAINTY.md:253-255`, `docs/results/atlas-m3-intervals-v1/full/metrics.json (calibration_support)`

**Description.** §5 defines fixed365 as 'calibrated once at the first origin of each period with the same 365-day window'. §9.1 says the selected method was 'calibrated once per period on the residuals of the preceding 365 days and then frozen'. Residuals start at origin 2024-02-01, so on m3_selection (anchor 2024-08-01) every fixed365 set held 153-182 residuals (about 6 months). The rolling365 sets grew from 153 to 365 (median 319). The method was therefore selected under a half-length window and evaluated with a full one, and the selection compared schemes with unequal window lengths. The generated report.md table shows '153-182 residuals per set', but the prose does not mention it.

**Evidence.** metrics.json calibration_support for the primary model: m3_selection empirical_relative@fixed365 cal_n min/median/max 153/167.5/182, first_cal_target 2024-02-02; rolling365 153/319/365; m3_evaluation 365/365/365. full/report.md:410.

**Reproduction.** Load docs/results/atlas-m3-intervals-v1/full/metrics.json and print calibration_support rows (cal_n_min, cal_n_median, cal_n_max, first_cal_target) per period and pair.

**Consequence.** The protocol description overstates the calibration data behind the selection, and hides that the fixed and rolling candidates were compared under different window lengths.

**Recommended repair.** Amend §5 and §9.1: '365 days' is the window cap; in m3_selection the fixed sets held 153-182 residuals (targets from 2024-02-02) and the rolling sets grew from 153 to 365.

**Verification.**

- uncertainty#5 reproduce: **CONFIRMED**, severity LOW. Every factual claim in the finding reproduces, both from the committed metrics.json and from my own date arithmetic:
- In the selection period, fixed365 calibrated once at 2024-08-01 on 153-182 residuals per horizon (183-h), because residuals start at origin 2024-02-01.
- The rolling candidates in the same comparison grew from 153 to 365 residuals (median 319).
- In the evaluation period, the sealed fixed365 used a full 365.

So the method was selected while its calibration set was about half the length the name and the §5/§9.1 prose imply. The candidates it was ranked against had different and growing set sizes. §9.1's phrase "the residuals of the preceding 365 days" is at best literally true under the 'window cap' reading and is misleading as written.

The disclosure is only partial and implicit: §2 and §7 give the dates, and the generated report table gives 153-182 for the selected pair only. I therefore set already_disclosed=false.

Severity stays LOW (gate L), for three reasons:
- This is a precision problem in the protocol description, not a code or math error. The code does what its docstring says (window_days is a cap).
- The pre-registered design deliberately starts selection six months in, to guarantee at least 100 calibration origins.
- The headline outcome (FAILED_CALIBRATION on the evaluation period, where fixed365 had a full 365) is already disclosed and does not [...]

## M8-17

**Effective-sample-size wording is inconsistent and overstates the evidence in places**

- Severity: **LOW** (gate C); fix complexity: small; alters published research results: no
- Found by: uncertainty#6 (LOW: The effective-sample-size statements contradict each other across UNCERTAINTY.md and the LIMITATIONS text); claims#6 (LOW: Effective-sample-size wording overstates the evidence ('tens of independent observations' where 8-9 were measured) and mislabels the bandwidth)
- Files: `docs/BACKTESTING.md:352`, `docs/BACKTESTING.md:502-504`, `docs/BACKTESTING.md:905-908`, `docs/UNCERTAINTY.md:270-272`, `docs/UNCERTAINTY.md:286-292`, `docs/UNCERTAINTY.md:465-467`, `docs/UNCERTAINTY.md:525-526`, `docs/UNCERTAINTY.md:525-527`, `docs/results/atlas-m3-intervals-v1/full/report.md:11`, `src/atlas/probabilistic.py:1430-1432`, `src/atlas/probabilistic.py:1431`

**Description.** §9.9(5) says 'no coverage or probability estimate here rests on 30 or more effective observations (8 to 16 for the primary model)'. The §9.3 table directly above lists eff. n (95 %) 242.0 (day 1), 47.1 (days 2-7) and 31.7 (days 8-14). Note that days 8-14 is one of the two buckets that trigger FAILED_CALIBRATION, and its own HAC interval of 84.9-98.6 % contains 95 %. §11 and the LIMITATIONS constant say the paths are 'worth only tens of independent observations', while §9.2 says 'about 8 to 9'. These are different quantities: the effective n of 30-day path means is 8-9, while the Bernoulli-equivalent n implied by the published intervals is about 29 (80 %) and 47 (95 %). The text never says which one it means.

**Evidence.** Committed metrics.json, evaluation primary pair: coverage_eff_n_95 day_1 241.96, days_2_7 47.06, days_8_14 31.72; coverage_eff_n_80 day_1 253.79, days_2_7 37.01. Implied Bernoulli-equivalent n from the committed intervals: 29.1 (80 %), 47.0 (95 %).

**Reproduction.** Read interval_summary rows (period m3_evaluation, primary model, empirical_relative@fixed365, scope bucket) from docs/results/atlas-m3-intervals-v1/full/metrics.json; compute p(1-p)/SE² with SE = (ci_high-ci_low)/(2·t_{0.975,335}).

**Consequence.** There are minor internal contradictions in the headline evidence statement. A careful reviewer will find them and question the rigour of the rest.

**Recommended repair.** Scope §9.9(5) to 'the days 1-30 headline coverage and every probability estimate'. In §11, say 'about 8-9 independent 30-day paths' to match §9.2. Leave the LIMITATIONS constant as is, because changing it changes metrics.json and the fingerprints; correct it only for future runs.

**Independent second report (claims#6).** UNCERTAINTY.md:525-526 (Limitations): '336 overlapping 30-day paths are worth only tens of independent observations'. The same document's results put the headline coverage at 'about 8 to 9 independent observations' (line 271) and 'no coverage or probability estimate here rests on 30 or more effective observations (8 to 16 for the primary model)' (line 467). The generic sentence in src/atlas/probabilistic.py:1431 says the same 'tens'; it is printed in the committed M3 reports. Separately, BACKTESTING.md:905-907 says the confirmation origins are worth 'about 14-27 independent observations ... under the primary bandwidth'. Those values (13.8, 21.2, 21.4, 27.3) are the Bartlett-2h effective sizes (BACKTESTING.md:502-504). Under the primary rectangular-29 bandwidth, H1's effective size is 13.0 (BACKTESTING.md:352).

**Verification.**

- uncertainty#6 reproduce: **CONFIRMED**, severity LOW. The main defect reproduces exactly.
- Section 9.9(5) makes a blanket claim that no coverage estimate in the doc rests on 30 or more effective observations. Section 9.3 directly above prints values of 242.0, 47.1 and 31.7 (evaluation) and 257.5 and 31.1 (selection). The committed metrics.json agrees with the table, so the sentence is wrong and the table is right.
- Section 11 says "tens of independent observations" for the 336 overlapping 30-day paths. Section 9.2 says "about 8 to 9" for the same paths. The section 11 sentence was written at the freeze, before results existed, and never updated.

Caveats and where I differ from the finder:
(a) The claim that "the text never says which one it means" is overstated. Section 4 (docs/UNCERTAINTY.md:91-96) and section 9.2 state that eff n is the HAC effective sample size of the per-origin mean series, which matches the code. The Bernoulli-equivalent n of about 29 and 47 is the finder's own derived quantity; the doc never uses it. It does explain how "tens" can be read loosely.
(b) The contradiction from the LIMITATIONS constant is weak. Its sentence is generic ("thousands of scored forecasts"), and across buckets the HAC eff n runs from about 9 to 250. "Only tens" is a loose summary of that, not a clear error. The clear contradiction is between section 11 and section 9.2.
(c) The proposed repair needs a correction. "Every probability [...]
- claims#6 reproduce: **CONFIRMED**, severity LOW. I read the cited code myself and recomputed the numbers from the committed JSON. Both parts of the finding hold.

1. The Limitations bullet in UNCERTAINTY.md:525-526 says the 336 overlapping 30-day paths are worth "only tens" of independent observations. The measured HAC effective sizes for the 30-day headline coverage are 8.1 and 9.0 for the primary model, and 7.2 to 10.5 for every method. This understates how thin the evidence is, and contradicts lines 271 and 467 of the same document.
   - The probabilistic.py sentence printed in the committed reports uses the same wording.
   - That sentence is generic ("thousands of scored forecasts"), and across all reported scopes the effective sizes have a median of about 44. So it is imprecise rather than clearly false.
2. BACKTESTING.md:905-907 attributes 14-27 to "the primary bandwidth". The code (metrics.py:186) and the doc's own definition (BACKTESTING.md:502-504) show these are Bartlett-2h values. The doc names rectangular 29 as the primary bandwidth (line 342), under which H1's effective size is 13.0 (line 352).

Severity is LOW:
- The direction of the statement ("small effective samples") is right.
- The exact figures and the FAILED_CALIBRATION / non-significance status are disclosed accurately in the body text.
- The bandwidth mislabel changes a lower bound by less than one observation (13.0 vs 13.8).
- No decision, gate [...]

## M8-18

**The 'failed' calibration wording is hard-coded in the API and dashboard instead of derived from the served status**

- Severity: **LOW** (gate H); fix complexity: small; alters published research results: no
- Found by: uncertainty#7 (LOW: The 'failed' wording is hard-coded in the API and dashboard instead of derived from the served M3 status)
- Files: `src/atlas/api/schemas.py:510-515`, `src/atlas/api/views.py:100-105`, `src/atlas/decision_inputs.py:54-58`, `web/src/views/Uncertainty.tsx:272`, `web/src/views/Uncertainty.tsx:529-533`, `web/src/views/Uncertainty.tsx:64-71`

**Description.** Several texts assert that the intervals FAILED regardless of the status the API actually serves: the Uncertainty page prose ('they failed that test'), the page header ('They failed that test'), the panel title 'Why the intervals are not decision-grade', the API's INTERVALS_LABEL ('which FAILED ... (failed_calibration)'), the IntervalPoint.quantiles schema description and MARGINAL_QUANTILE_NOTICE. Tags and status fields are taken from the data, but these strings are not. No startup check or test ties them to the status value: the M5 tests assert that the reference status IS failed_calibration, not that the wording follows it. For the frozen v1.0 snapshot they are consistent; after any re-run or new snapshot, a status of insufficient_evidence or decision_grade would be served next to text saying it failed.

**Evidence.** grep finds the hard-coded strings at the cited lines. IntervalStatusTag and Callout tone use the data (Uncertainty.tsx:145-146, 162), but the prose does not. reference.py loads the status from the SHA-pinned file without checking that it matches these strings.

**Reproduction.** `grep -n "failed that test\|They failed\|FAILED its\|failed their calibration" web/src/views/Uncertainty.tsx src/atlas/api/views.py src/atlas/api/schemas.py src/atlas/decision_inputs.py`

**Consequence.** This is latent: it is harmless for v1.0, but any re-run of M3 (the owner plans new data and private deployment later) would make the API and dashboard contradict their own status fields.

**Recommended repair.** Derive these texts from interval_status (as BandChart already does for its callout), or add one contract test or startup assertion that fails if the served status differs from failed_calibration while the FAILED wording is present.

**Verification.**

- uncertainty#7 reproduce: **CONFIRMED**, severity LOW. The finding holds as described. The 'failed' texts in the API (INTERVALS_LABEL, MARGINAL_QUANTILE_NOTICE, the quantiles schema description, the OpenAPI description) and in the dashboard are constants. The status fields, tags and guidance are computed from the served interval_status. No test or startup check links the two. When I changed only the status, the API and the dashboard served a status of insufficient_evidence or decision_grade next to text saying the intervals FAILED; in the API this happens inside a single integrity.notices list.

Mitigating factors, and why I keep it at LOW:
- For the committed v1.0 snapshot, all texts are correct and consistent. I verified this with the reference server.
- calibration_status.json is pinned by SHA-256 in configs/optimization/*.json:18. Tampering makes the components unavailable (test_m5_reliability.py:105-107), so the texts can only diverge after a deliberate, re-pinned M3 re-run.
- After such a re-run, the existing tests would fail and force a human to look: test_m5_endpoints pins failed_calibration, and test_m7_fixtures fails until the fixtures are re-recorded. docs/DASHBOARD.md section 15 ("Results changing under the dashboard ... the views reviewed for the new numbers and wording") documents this generic review step.

That doc is a process note, not a statement of this specific coupling. It does not mention the API's [...]

## M8-19

**Certificate tolerance ratios are shown as a 'gap' and plans called an 'exact optimum'**

- Severity: **LOW** (gate H); fix complexity: small; alters published research results: no
- Found by: optimization#1 (LOW: Certificate fields are unitless tolerance ratios but are shown as a 'gap' and the plans called 'exact optimum')
- Files: `src/atlas/api/schemas.py:556-563`, `src/atlas/optimizer.py:733-749`, `web/src/views/Decisions.tsx:458-463`, `web/src/views/plan.tsx:241-246`

**Description.** certify() returns primal, dual_sign, stationarity and duality_gap as the largest violation in units of its own tolerance (optimizer.py:737-741: 1.0 means exactly at the tolerance). It also returns the absolute primal_objective and dual_objective. The API schema `Certificate` has no field descriptions, and docs/API.md does not explain the fields. The dashboard prints `(gap ${certificate.duality_gap.toExponential(1)})` after 'the solver's optimum passed the primal and dual feasibility and duality-gap certificate'. A reader will take this as the duality gap; it is a dimensionless ratio. It is also the certificate of the last tie-break stage (least request-days on the optimal face), not of the penalised-score programme (`strict_programme`). Decisions.tsx says every result is checked 'with a zero duality gap' and that this 'makes each plan the exact optimum'. The certification is tolerance-based (1e-7 absolute + 1e-9 relative). For non-unique optima the plan is one of several optima, and DIAGNOSTIC plans are optima of a different (lexicographic) programme.

**Evidence.** metrics.json scenarios[0].reported_plan.certificate = {duality_gap: 6.078569270020705e-09, primal_objective: 48728.00420845966, dual_objective: 48728.00420845964}. The dashboard would display 'gap 6.1e-9'. The actual absolute gap of that programme is about 2.2e-11 request-days (relative about 4.5e-16). The same value comes out of the API simulation path (api_default.py: cert 6.078569270020705e-09 for the default request). The penalised-score certificate is metrics.json scenarios[0].strict_programme.certificate (duality_gap 0.0). grep finds no explanation of these fields in docs/API.md or schemas.py.

**Reproduction.** .venv/bin/python -c "import json;m=json.load(open('docs/results/atlas-m4-optimization-v1/metrics.json'));print(m['scenarios'][0]['reported_plan']['certificate'])"; then read web/src/views/plan.tsx:241-246. Scratch script api_default.py (<audit-scratch>/optimization/api_default.py) shows the same value via the API simulation path.

**Consequence.** A technical reader (hiring manager, reviewer) misreads a tolerance ratio as a duality gap. The 'exact optimum' wording overstates a tolerance-based, possibly non-unique optimum. No number is wrong, and the plan's correctness is unaffected.

**Recommended repair.** Add Field descriptions to the API Certificate model, e.g. 'largest violation in units of its tolerance (<= 1 passes); tolerances 1e-7 + 1e-9 x magnitude'. Label the dashboard value '(largest violation 6.1e-9 of its tolerance)', or show |primal_objective - dual_objective| as the gap. Reword Decisions.tsx to 'optimal to within 1e-9 relative tolerance' and 'an optimum (one of several when not unique)'. Regenerate openapi.gen.ts and rebuild web/dist.

**Verification.**

- optimization#1 reproduce: **CONFIRMED**, severity LOW. The core defect reproduces exactly. The dashboard labels `certificate.duality_gap` as "gap" (plan.tsx:245). That value is a dimensionless ratio of the absolute gap to its tolerance (optimizer.py:814-817, docstring 737-741). For the default and normal scenario, the dashboard shows "gap 6.1e-9". I recomputed the true absolute gap: 1.46e-11 request-days, relative 3e-16. The API schema (schemas.py:556-563) and docs/API.md give no description of these fields, so an API consumer or dashboard reader cannot tell. The certificate shown is also that of the last tie-break programme (least request-days), not of the penalised-score programme. This is not false (the tie-broken plan is the reported plan), but it is undocumented.

The "exact optimum" / "zero duality gap" wording in Decisions.tsx:459-462 overstates a check that passes within tolerances (1e-7 + 1e-9 x magnitude). The non-uniqueness sub-point is largely mitigated: the plan view labels non-unique plans per plan, all reference plans are unique after tie-breaks, and DIAGNOSTIC plans are explained as least-miss optima in the same panel.

No number is wrong, the certificate logic is correct, and plan correctness is unaffected. This is a labelling/documentation precision issue on gate H (and L). It fits LOW for a portfolio research demo, though a technically literate reviewer could notice it.

It is not already disclosed: [...]

## M8-20

**Unverified run_id from the M2/M3 projection manifests is served as provenance**

- Severity: **LOW** (gate A); fix complexity: small; alters published research results: no
- Found by: api#2 (LOW: Unverified run_id from the M2 and M3 projection manifests is served as provenance; the manifest hashes recorded by the M4 run are never compared)
- Files: `docs/API.md:639-642`, `src/atlas/api/reference.py:300-310`, `src/atlas/api/reference.py:340-350`, `src/atlas/api/reference.py:684`, `src/atlas/api/reference.py:806-811`, `src/atlas/decision_inputs.py:182`, `src/atlas/decision_inputs.py:444`

**Description.** Where the run_id comes from:
- load_m2_projection reads `run_id` from docs/results/atlas-m2-rolling-origin-v1/projection/manifest.json, a file that is not hash-checked.
- _load_m3_intervals reads `run_id` from the M3 projection manifest the same way.
- The API serves both as `provenance.sources[].run_id`.

The committed M4 reference run records both manifests' identity: `inputs.forecast.manifest_sha256`/`run_id` and `inputs.stress_paths[*].manifest_sha256`/`run_id`. But _load_m4 compares only `projections_sha256` and the M3 `interval_status`. An edited manifest therefore passes every check: readiness stays 200 and the edited identifier is served.

This contradicts docs/API.md §8: 'Any break in the M2/M3 provenance chain takes down the forecast, uncertainty, M4 and simulation components together.' No number is affected; the projections and intervals CSVs are hash-checked.

**Evidence.** - tamper.py on copies of the tree:
  - `m2_proj_manifest_run_id`: ready 200, every component ready. 'FORGED-RUN-ID' is served by /system, /forecasts, /uncertainty/intervals, /scenarios, /scenarios/normal, /simulations/parameters and POST /simulations.
  - `m3_proj_manifest_run_id`: ready 200. The forged id is served by /uncertainty/intervals and /scenarios.
- forged_show.py printed `m2_projection_run run_id= FORGED-RUN-ID` and `m3_projection_run run_id= FORGED-M3-RUN-ID` next to the verified file hashes. It also showed 'M4 recorded M2 projection manifest_sha256 6baad8e2813d65f4 current 57a04f76d0606221', so the mismatch is detectable from data already committed.
- By contrast, changing the M4 manifest run_id is refused (`manifest_field_differs`), and so is changing the M2 full manifest run_id.

**Reproduction.** scratchpad/audit/api/tamper.py <scratch> m2_proj_manifest_run_id m3_proj_manifest_run_id. It copies `git archive HEAD`, sets run_id='FORGED-RUN-ID' in the manifest, starts the app in-process with TestClient, then records /health/ready and which endpoints contain the forged string. forged_show.py prints the served sources and compares the M4-recorded manifest hash.

**Consequence.** The provenance block the docs present as the auditable chain can name run identities that never produced the data while readiness is green. This is a claim-versus-behaviour gap in a release whose main selling point is verified provenance. It requires write access to the committed results, and no served number changes.

**Recommended repair.** In _load_m4, also require:
- `forecast['manifest_sha256'] == inputs.projection.provenance['manifest_sha256']` and `forecast['run_id'] == provenance['run_id']`;
- for every recorded stress path, its `manifest_sha256`/`run_id` against the M3 projection manifest. Alternatively, hash-check the M3 manifest in _load_m3_intervals against the stress-path record.

Add both cases to the parametrised tamper list in tests/test_m5_reliability.py.

**Verification.**

- api#2 reproduce: **CONFIRMED**, severity LOW. Every element of the finding reproduces with my own independent scripts:
- An edited run_id in the M2 projection manifest or the M3 projection manifest passes every readiness check (ready 200), and the API serves the forged id as `provenance.sources[].run_id` next to genuinely verified file hashes.
- The committed M4 metrics already record both manifests' SHA-256 and run_id, but nothing compares them. Comparable edits to the M2 full manifest and the M4 manifest are refused, which shows the projection manifests are an inconsistent gap rather than a design choice.
- The served numbers are unaffected, because the projections and intervals CSVs are hash-pinned. Exploiting this needs write access to the committed results, which under the threat model (loopback API, read-only immutable artifacts) already means the repository is compromised.
- The impact is a claim-versus-behaviour gap with docs/API.md §8 ("any break in the M2/M3 provenance chain takes down ..."), and the provenance block can name a run identity that did not produce the data.

That is a real but minor integrity and documentation defect, so I agree with LOW under gate A, with a documentation aspect under gate L. It is not disclosed anywhere in the repository.

The proposed repair is sound and cheap:
- In _load_m4, compare `inputs.forecast.manifest_sha256` and `run_id` against `inputs.projection.provenance`, and [...]

## M8-21

**HEAD on versioned GETs returns 405 while the nginx allow-list passes HEAD**

- Severity: **LOW** (gate E); fix complexity: small; alters published research results: no
- Found by: api#3 (LOW: HEAD on every versioned GET returns 405, but the nginx allow-list passes HEAD 'as the API answers it')
- Files: `docs/API.md:193-194`, `src/atlas/api/routers/forecasts.py`, `src/atlas/api/routers/health.py:32,67`, `src/atlas/api/routers/scenarios.py`, `src/atlas/api/routers/simulations.py`, `src/atlas/api/routers/system.py`, `src/atlas/api/routers/uncertainty.py`, `src/atlas/deploy/routes.py:86-89`

**Description.** Only /health/live and /health/ready have explicit HEAD handlers; FastAPI APIRoute does not add HEAD to GET routes. The deployment allow-list in Route.methods returns ('GET','HEAD') for every GET operation, with the docstring 'HEAD with GET, as the API answers it'. Through nginx, HEAD on the 12 versioned GET operations reaches the API and gets its 405. docs/API.md correctly says HEAD is answered only on the health endpoints, so the API documentation and the deployment's assumption disagree. The proxy tests use a fake upstream that answers HEAD everywhere (tests/test_m6_proxy.py:75), so the mismatch is untested against the real API.

**Evidence.** `curl -I` on :9160: /health/live 200, /health/ready 200; /api/v1/system 405, /api/v1/forecasts 405, /api/v1/scenarios/normal 405 (`allow: GET`). get_probe.py: 'HEAD forecasts 405 Method Not Allowed'.

**Reproduction.** `uv run atlas api serve --port 9160`; `for p in /health/live /api/v1/system /api/v1/forecasts; do curl -s -o /dev/null -w "$p %{http_code}\n" -I http://127.0.0.1:9160$p; done`

**Consequence.** Uptime checkers, link checkers or caches that use HEAD on read endpoints get 405 through the deployed proxy. A routing rule rests on a false premise. Impact is small.

**Recommended repair.** Choose one:
- Answer HEAD for GET routes in the API: a small ASGI shim that runs GET and drops the body for paths whose route allows GET, or `api_route(..., methods=['GET','HEAD'])` hidden from the schema.
- Make Route.methods return only (method,) for versioned operations and fix the docstring.

Add a real-API HEAD check.

**Verification.**

- api#3 reproduce: **CONFIRMED**, severity LOW. The behaviour reproduces exactly, both against the real API and through the rendered production nginx site. The deployment allow-list passes GET|HEAD for every GET operation because of a stated premise ("as the API answers it"). For the 11 versioned GET operations that premise is false: FastAPI's APIRoute registers only GET, and HEAD handlers exist only for /health/live and /health/ready. docs/API.md:193-194 describes the API's behaviour accurately, so the API documentation is right. The defect is that the deployment code's docstring and allow-list contradict it, and no test catches this: the proxy tests use an echo upstream that answers HEAD everywhere, and the real-API proxy test sends no HEAD. No document flags this mismatch as a limitation, so already_disclosed is false.

Impact is small. The result is a clean JSON 405 with `allow: GET`. Nothing is exposed and there is no integrity issue. No component of ATLAS relies on HEAD for versioned routes: the uptime checks documented in docs/API.md:894 use /health/ready, which works. The only costs are an unnecessary upstream round trip and a misleading routing rule that tests miss. LOW (gate E, partly G) is the right severity.

One minor inaccuracy in the finding: the count is 11 versioned GET operations, not 12. It does not change the verdict. Either proposed repair is reasonable: answer HEAD on the GET routes, or narrow [...]

## M8-22

**After a worker replacement the next simulation goes to the cold worker while a warm one is idle**

- Severity: **LOW** (gate E); fix complexity: small; alters published research results: no
- Found by: api#4 (LOW: After a worker replacement the next simulation goes to the cold, still-importing worker while a warm worker sits idle)
- Files: `src/atlas/api/simulation.py:414`, `src/atlas/api/simulation.py:548-557`, `src/atlas/api/simulation.py:574-595`, `src/atlas/deploy/render.py:348`

**Description.** SimulationPool keeps idle workers in a list used as a stack: acquire() pops from the end and release() appends to the end. After a timeout or crash, exchange() restarts the worker (a fresh spawn) and releases it to the end of the list, so the next request picks it even when another worker is warm and idle. That request first waits for the new process to import ATLAS (connection.poll(STARTUP_SECONDS=120)). The wait is not counted against the simulation deadline; it is bounded only by 120 s and, behind nginx, by proxy_read_timeout = deadline + 20 s. Server-Timing reports only the compute time, so the delay is invisible to clients.

**Evidence.** lifo_probe.py on :9162 (2 workers, 1 s deadline), twice:
- a warm small request took 0.72 s / 0.57 s;
- the slow request took 1.05 s (504);
- the next small request took 4.35 s / 4.59 s with `server-timing: compute;dur=638/678`;
- the one after took 0.84 s / 0.78 s.
The earlier timeout_probe.py showed the same: repeated 504s took 3.5 s each (2.5 s of import wait plus the 1 s deadline).

**Reproduction.** Start the server with ATLAS_API_KEY set and ATLAS_API_SIMULATION_TIMEOUT_SECONDS=1 on :9162. Run scratchpad/audit/api/lifo_probe.py <scratch>: warm small request, then a heavy request (horizon 30, multiplier 2, shift 5000, initial_backlog 200000, terminal_backlog_max 0, budget 0) that times out, then two small requests, timing each and reading Server-Timing.

**Consequence.** Every worker loss adds roughly 2.5–4 s (more under memory pressure on a small VPS) to an unrelated user's next simulation. The documented deadline is not an end-to-end bound. In the worst case (a slow import) nginx answers 504 while the API keeps waiting.

**Recommended repair.** In acquire(), prefer a worker with `ready=True` (fall back to any idle worker), or put a restarted worker at the front of the list (FIFO). Optionally bound the startup wait for a request (for example, a few seconds, then 503 simulations_busy) instead of 120 s.

**Verification.**

- api#4 reproduce: **CONFIRMED**, severity LOW. The mechanism reproduces exactly as described. The pool is a stack, and a worker replaced after a timeout or crash goes back on top. So the next simulation is sent to the cold, still-importing process even when another worker is warm and idle. That request then waits up to STARTUP_SECONDS=120 for the import, outside the deadline and outside Server-Timing.

I showed this two ways:
- Over HTTP: after a 504, the next request took 1.1-1.3 s for 0.19-0.27 s of compute. A request sent 0.3 s later got the warm worker and finished first.
- Directly on the pool: the acquire after a replacement returned the ready=False worker while a ready=True worker was idle, and a 0.5 s deadline exchange took 4.1 s without timing out.

The claimed size of the penalty (2.5-4 s) did not reproduce on this idle machine; I measured about 1 s. It depends on load and import time. The nginx worst case is a correct inference from the code but I did not demonstrate it, and it applies to cold workers in general.

Severity stays LOW:
- It costs latency for one request after each rare worker loss. The default 10 s deadline is about 7-10x the slowest observed simulation, so timeouts should be rare.
- There is no correctness, integrity or security impact.
- The docs already state that import time is not counted against the deadline and that Server-Timing is compute only.
- The proposed fix is cheap and well placed: [...]

## M8-23

**/docs and /redoc (on by default) load floating CDN bundles without SRI or CSP on the API/dashboard origin**

- Severity: **LOW** (gate F); fix complexity: small; alters published research results: no
- Found by: api#5 (LOW: Swagger UI and ReDoc, on by default, load floating-major CDN bundles without SRI, exempt from CSP, on the API and dashboard origin); supplychain#3 (LOW: API docs pages (on by default) run floating, unpinned third-party scripts from jsDelivr with no SRI and no CSP, on the API and dashboard origin)
- Files: `.env.example:27`, `docs/API.md:629-630`, `docs/API.md:631-632`, `docs/DEPLOYMENT.md:353`, `src/atlas/api/app.py:108-118`, `src/atlas/api/app.py:115-117`, `src/atlas/api/middleware.py:176-178`, `src/atlas/api/middleware.py:48-50,170-173`, `src/atlas/api/middleware.py:49-51`, `src/atlas/api/settings.py:85`

**Description.** `docs_enabled` defaults to True. /docs and /redoc are FastAPI's default pages. They load `https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js` and `.../redoc@2/bundles/redoc.standalone.js` (a floating major version, no integrity attribute), a favicon from fastapi.tiangolo.com and Google Fonts. The middleware exempts these paths from the API CSP. They are served from the same origin as the API and the M7 dashboard. Whenever docs are enabled (the local default, and the tunnel profile with ATLAS_DEPLOY_API_DOCS), the key a user types into Swagger's 'Authorize' lives on a page running unpinned third-party script. The docs mention the CDN but not the floating versions or the missing SRI.

**Evidence.** `curl -s http://127.0.0.1:9160/docs | grep -o -E '(src|href)="[^"]+"'` returned swagger-ui-dist@5 CSS and JS from cdn.jsdelivr.net and the favicon from fastapi.tiangolo.com. /redoc returned redoc@2 and fonts.googleapis.com. `curl -I /docs` shows no Content-Security-Policy, only X-Frame-Options.

**Reproduction.** `uv run atlas api serve --port 9160`; `curl -s http://127.0.0.1:9160/docs | grep -o -E '(src|href)="[^"]+"'`; `curl -s -I http://127.0.0.1:9160/docs | grep -i content-security`

**Consequence.** A supply-chain compromise or a breaking major-version change at the CDN would run in the API's origin for anyone opening /docs. This is limited by docs being off in the exposed profiles.

**Recommended repair.** Serve the docs pages through fastapi.openapi.docs.get_swagger_ui_html / get_redoc_html with exact pinned versions and SRI hashes, or self-host the assets. Alternatively default ATLAS_API_DOCS to false. Add a CSP on the docs paths that allows only that exact origin.

**Independent second report (supplychain#3).** `ApiSettings.docs_enabled` defaults to True, so plain `atlas api serve` and any deployment with ATLAS_API_DOCS unset serve /docs and /redoc. These are FastAPI's default pages, which load `https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js` and `redoc@2/bundles/redoc.standalone.js`. Both are floating major versions with no Subresource Integrity, and the pages also fetch a favicon from fastapi.tiangolo.com. The middleware deliberately exempts DOCS_PATHS from the API's `default-src 'none'` CSP. The pages share the API's origin, which also serves the dashboard by default (dashboard_enabled=True). The OpenAPI document declares an X-API-Key scheme, so Swagger UI offers an Authorize dialog where an operator would type the simulation key. Whatever jsDelivr serves for swagger-ui-dist 5.x on that day runs with full access to that origin and anything typed into it. Every Python and npm dependency is otherwise hash-pinned and held to a cooldown; these two scripts are not. M6 mitigates the deployed case (ATLAS_DEPLOY_API_DOCS=false by default; nginx returns 404 for /docs in the private and public profiles). API.md:631 says only that the page 'loads its scripts from a CDN'; it [...]

**Verification.**

- api#5 reproduce: **CONFIRMED**, severity LOW. Every technical claim reproduces independently:
- Docs default to on (settings.py:85).
- /docs and /redoc are FastAPI's stock pages and load floating-major CDN bundles (swagger-ui-dist@5, redoc@2) with no integrity attribute, plus third-party favicon and font requests.
- The middleware deliberately sends no CSP on those paths.
- They share the origin with the JSON API and the M7 dashboard.
- Swagger exposes an Authorize field for the X-API-Key.

A compromised or broken jsDelivr package would therefore run unrestricted in the API/dashboard origin for anyone who opens /docs. It could read a typed key and issue authenticated simulations, and with no CSP nothing limits exfiltration.

Severity stays LOW because the realistic exposure is narrow:
- The deploy configuration defaults docs to false and refuses them outside the SSH-tunnel profile (siteconfig.py:267-275).
- nginx returns 404 for the docs in the private and public-demo profiles.
- The dashboard keeps no cookies or storage, so the only secret at risk is a key the user types in voluntarily.
- The risk lives mainly in local development and an opted-in tunnel.

already_disclosed is false. The docs disclose CDN loading for Swagger UI only. They do not say the versions float, that there is no SRI, that the CSP is exempted, or that ReDoc and Google Fonts are loaded, and API.md:68 presents the on-by-default setting without that [...]
- supplychain#3 reproduce: **CONFIRMED**, severity LOW. Every factual claim in the finding reproduced on my own server, port 9300:
- Docs are on by default.
- Stock FastAPI pages load floating major-version scripts from jsDelivr: swagger-ui-dist@5 and redoc@2.
- There is no SRI and no CSP on those paths, and the exemption is deliberate in the middleware and tests.
- The pages share an origin with the dashboard.
- The OpenAPI document declares an X-API-Key scheme, which gives Swagger UI an Authorize field.

I also found something the finder did not report. Today @5 resolves to swagger-ui-dist 5.33.3, published one day ago. These pages therefore escape the project's two-week cooldown and hash-pinning policy, whereas every locked Python and npm dependency is held to it. The redoc page also pulls from fonts.googleapis.com.

LOW is still the right severity:
- The deployed path is well mitigated in code. Docs default to false, true is refused outside the tunnel profile, and nginx exposes only the contract routes.
- The dashboard keeps no secrets in browser storage (web/src has no localStorage, sessionStorage or cookie use, and structure.test.ts enforces this).
- The exposure is therefore a developer or tunnel operator who opens /docs and types the simulation key into Authorize while jsDelivr or the npm package is compromised. That is low likelihood, and the impact is limited to a loopback-only research API.
- It is not release-blocking, [...]

## M8-24

**release.json environment_fingerprint differs between two builds of the same commit**

- Severity: **LOW** (gate J); fix complexity: small; alters published research results: no
- Found by: deploy#3 (LOW: environment_fingerprint differs between two builds of the same commit (uv_cache.json timestamp and RECORD of the editable project)); release#5 (LOW: release.json's environment_fingerprint is not reproducible: two builds of the same commit at the same path differ)
- Files: `deploy/scripts/build-release.sh`, `deploy/scripts/build-release.sh:120-131`, `docs/DEPLOYMENT.md:513-519`, `src/atlas/deploy/manifest.py`, `src/atlas/deploy/manifest.py:98-111`

**Description.** uv writes `.venv/lib/python3.12/site-packages/atlas-0.7.0.dist-info/uv_cache.json` with the build-time timestamp of the project, and RECORD lists that file's hash. environment_fingerprint hashes every non-bytecode file of .venv, so two builds of the same commit, same lock, same uv and same Python never share an environment fingerprint. Everything else in the manifest is reproducible. No doc claims the environment fingerprint is reproducible, but the release identity of a rebuilt release is not comparable across builds.

**Evidence.** build_sim.sh (git archive HEAD into .../opt/atlas/releases/0.7.0-194fa4a078ff; uv 0.11.32 sync --locked --no-dev --python /usr/bin/python3.12 with the build's environment variables; compileall; build_manifest with a fixed built_at), run twice. Build 1: env 3bece2d8..., build 2: env d21dd38b.... Source 2d23ef9a..., research bf6b3064..., dashboard 824a73f0... identical. Manifest keys differing: ['environment_fingerprint']. venv files 4394 vs 4394; differing: ['.venv/.../atlas-0.7.0.dist-info/RECORD', '.venv/.../atlas-0.7.0.dist-info/uv_cache.json'].

**Reproduction.** S=<audit-scratch>/deploy; $S/build_sim.sh $S 1; $S/build_sim.sh $S 2; compare $S/build/manifest-1.json with manifest-2.json and venv-1.json with venv-2.json (both kept; the venvs themselves were deleted).

**Consequence.** The manifest cannot attest that two servers, or a release rebuilt from the history after pruning (DEPLOYMENT.md section 12), run the same installed environment. The reproducibility evidence for a v1.0 release is weaker than the rest of the manifest suggests.

**Recommended repair.** Exclude `*.dist-info/uv_cache.json` and the editable project's `RECORD` (or all RECORD files) from environment_fingerprint. Add a test that builds twice, or that the fingerprint ignores those files. Optionally document that the fingerprint is per Python patch version.

**Independent second report (release#5).** environment_fingerprint (manifest.py:98-111) hashes every non-bytecode file under .venv. uv's editable install of the project writes atlas-<version>.dist-info/uv_cache.json with build timestamps, and the dist-info RECORD line hashes that file. As a result, the fingerprint changes on every rebuild even with an identical commit, uv version, lock, Python and path. Within one host it works as a tamper check, but it cannot serve as a reproducible build identity in an RC manifest, and two servers building the same commit record different values.

**Verification.**

- deploy#3 reproduce: **CONFIRMED**, severity LOW. The behaviour reproduces exactly as claimed, with my own script and in a different directory. Two builds of commit 194fa4a with the same lock, uv 0.11.32 and Python 3.12 differ only in environment_fingerprint. The cause is uv writing the extraction-time ctime of pyproject.toml/src into the editable project's dist-info/uv_cache.json, which RECORD then hashes. Every other part of the manifest (source, research, dashboard, lock, contract) is the same between the builds.

The finder's description has one small inaccuracy: the value is the files' ctime at extraction, not literally a build timestamp. In practice that is the same thing, and it is worse in one way, because tar cannot restore a ctime.

The impact is limited:
- check_release (manifest.py:346) compares the fingerprint only with the release's own manifest, so tamper detection still works. Both of my builds passed their own check.
- plan-upgrade, status and history (releases.py) never record or compare the environment fingerprint, so no deployment, recovery or rollback path breaks.
- No document claims the fingerprint is reproducible.

What remains true: release.json cannot show that two servers, or a release rebuilt from history after a prune (DEPLOYMENT.md section 12), have the same installed environment, although the rest of the manifest suggests reproducibility. The fingerprint also depends on the absolute release path [...]
- release#5 reproduce: **CONFIRMED**, severity LOW. The behaviour reproduces exactly as described. Rebuilding the same commit at the same path with the pinned uv, the locked dependencies and the same Python gives a different environment_fingerprint every time. The only cause is uv's editable-install cache file atlas-<ver>.dist-info/uv_cache.json, which holds build-time timestamps, plus the RECORD line that hashes that file. I confirmed the proposed repair removes the nondeterminism.

The severity stays LOW (gate J) because the functional impact is limited:
- The value is used only for same-host tamper detection in check_release (manifest.py:346). That still works.
- build-release.sh:100-106 keeps an existing complete release instead of rebuilding it, so no false refusal arises on one host.
- plan-upgrade and the verification code do not compare environment fingerprints across releases.

The defect is that release.json carries a value that looks like a build identity but cannot be reproduced from (commit, uv.lock, uv, Python, path). Two hosts, or a rebuild after deletion, record different values. That matters only if M8 treats the environment fingerprint as an RC or reproducibility identity, and it undercuts any claim that the environment is reproducible by hash.

Even with the repair, cross-host equality would still depend on the same Python patch version and install path, because pyvenv.cfg, the interpreter symlink and the [...]

## M8-25

**IPv6 is enabled by default although per-client limits are keyed per /128 and the guide says to keep it off**

- Severity: **LOW** (gate F); fix complexity: small; alters published research results: no
- Found by: deploy#4 (LOW: IPv6 is enabled by default although per-client limits are keyed per /128 address and the guide says to keep IPv6 off)
- Files: `deploy/deploy.env.example:32-33`, `deploy/templates/nginx-atlas.conf.in:8-12`, `docs/DEPLOYMENT.md:767-768`, `src/atlas/deploy/siteconfig.py:277`

**Description.** ATLAS_DEPLOY_IPV6 defaults to true. Every limit_req_zone and limit_conn_zone is keyed on $binary_remote_addr, i.e. one bucket per IPv6 address. Per-client rate limits are the only brute-force and abuse control on Basic auth (no lockout, DEPLOYMENT.md section 13). DEPLOYMENT.md:767-768 says 'Keep ATLAS_DEPLOY_IPV6=false unless IPv6 clients are needed', which contradicts the default.

**Evidence.** Rendering a private deploy.env without the IPV6 key: config.ipv6 True; site listens on [::]:80 default_server, [::]:443 ssl default_server, [::]:80 and [::]:443 ssl; zones are `limit_req_zone $binary_remote_addr zone=atlas_read ... atlas_compute ... atlas_static` and `limit_conn_zone $binary_remote_addr`.

**Reproduction.** PYTHONPATH=<tree>/src python -c 'from atlas.deploy.siteconfig import load_config; ...' rendering 'ATLAS_DEPLOY_PROFILE=private\nATLAS_DEPLOY_DOMAIN=atlas.example.org\n' with Renderer.for_release(...).nginx() and grepping 'listen [::]' and 'limit_'.

**Consequence.** With the default configuration, a client holding an IPv6 /64 gets effectively unlimited request, connection and simulation budgets. Password guessing stays infeasible against 192-bit generated passwords. Exhausting the small server (CPU-bound simulations within MemoryMax and the worker count) becomes easy, and the guide's advice is silently not the default.

**Recommended repair.** Make IPV6 default to false, matching the guide and the tests, which set false. Alternatively, key the zones on a /64 for IPv6 (a `map` from $remote_addr to a prefix key) and keep the default.

**Verification.**

- deploy#4 reproduce: **CONFIRMED**, severity LOW. Every factual claim in the finding holds, checked against the code and confirmed by my own render and nginx run.
- ATLAS_DEPLOY_IPV6 defaults to true (siteconfig.py:277/296/118, deploy.env.example:33, DEPLOYMENT.md:348).
- The private and public-demo sites then listen on [::]:80 and [::]:443.
- All four limit zones are keyed on $binary_remote_addr, i.e. the full address.
- DEPLOYMENT.md:767-768 tells operators to keep IPv6 false unless needed, which contradicts the default and the example's own comment.

The per-address bucketing was shown live with nginx using IPv4 source addresses. The IPv6 /64 case could not be run because the kernel has no IPv6. It follows from nginx's documented semantics, which this verification did not check directly.

Severity stays LOW, not higher:
- Per-address IPv6 limiting is disclosed as an accepted residual risk in three places.
- Simulations need an authenticated Basic account, and their concurrency is capped by the worker pool and MemoryMax.
- A single IPv6 host can make roughly 20x more SHA-512-crypt checks per second than one IPv4 address. That only matters if it knows a valid user name.
- Per-address limits are never a DDoS defence, and the public-demo profile already requires DDoS protection.
- No real deployment exists.

The finding's consequence wording ("exhausting the small server becomes easy") slightly overstates the case. The worst [...]

## M8-26

**`atlas-deploy render --apply` installs a site for a non-current release without the lock, a history record or verification**

- Severity: **LOW** (gate G); fix complexity: small; alters published research results: no
- Found by: deploy#5 (LOW: `atlas-deploy render --apply` installs a site for any installed release, including a non-current one, without the deployment lock, a history record or verification)
- Files: `src/atlas/deploy/cli.py:282-349`

**Description.** `render --apply --release-dir /opt/atlas/releases/<B>` is accepted while current points to A. `_publish_for_site` only requires an installed release whose build matches its release.json. It publishes B's dashboard and writes B's nginx site (B's dashboard copy and B's route allow-list) and atlas.env, without taking `Deployment.locked()`, without recording history and without verification. The next nginx reload, which the command itself tells the operator to run (or a certbot renewal), puts B's browser assets in front of A's API. The error message at cli.py:296-299 even suggests `--release-dir /opt/atlas/releases/<id>`.

**Evidence.** repro_render_noncurrent.py (rehearsal root with releases A and B, current -> A): cli.main(['--root', root, 'render', '--release-dir', B, '--apply']) -> exit 0; current -> releases/0.7.0-aaaaaaaaaaaa; site root is B: True; history written: False.

**Reproduction.** S=<audit-scratch>/deploy; cd $S && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$S/tree/src:$S/tree .venv/bin/python $S/repro_render_noncurrent.py $S/runs

**Consequence.** An operator mistake or a concurrent run produces an unrecorded dashboard/API mismatch, which the 'verified state' bookkeeping does not know about. It can race with an activation because no lock is taken.

**Recommended repair.** For `--apply` with the tls stage in an exposed profile, refuse a release directory that does not resolve to `current` (or require an explicit flag). Take the deployment lock and append a history record. Point the error message at `atlas-deploy activate <id>`.

**Verification.**

- deploy#5 reproduce: **CONFIRMED**, severity LOW. The finding reproduces in full, and I added more consequences of my own.
- `atlas-deploy render --apply` accepts any installed release, including a non-current one.
- It publishes that release's dashboard and writes its nginx site, units and env.
- It takes no deployment lock and writes no history record. I proved the missing lock by holding the lock: render still succeeds, while rollback is refused.
- The resulting mismatch is invisible to `verify`, which checks the current release's published copy and reaches the API directly, never through the site file. It is also invisible to `status`.
- A later `prune` can delete the very copy the site serves.
- The documentation contradicts this behaviour ("a lock prevents concurrent deployments", "history.jsonl: every deployment step"). It also advertises `--release-dir /opt/atlas/releases/<id>` without warning that the release should be the current one. So this is not already disclosed.

The severity stays LOW:
- Only root can run the command, and the documented workflow uses `render --apply` only for the acme stage with the current release's tool.
- The bad state needs an explicit operator mistake (or two concurrent root operators) and becomes live only after an nginx reload.
- The next activate or rollback re-renders a correct site.
- There is no security exposure: profile access rules and the refusal to route computation in [...]

## M8-27

**Simulator error-summary links fail WCAG 2.2 target size (2.5.8) with two or more field errors**

- Severity: **LOW** (gate I); fix complexity: small; alters published research results: no
- Found by: dashboard#1 (LOW: Simulator error-summary links fail WCAG 2.2 target size (2.5.8) when two or more field errors are shown)
- Files: `web/e2e/a11y.spec.ts:42-60`, `web/src/styles/components.css:177-180`, `web/src/views/Simulator.tsx:351-372`

**Description.** The client-side error summary lists one link per invalid field, inside a callout `ul` with no extra spacing. At desktop width each link is 16 px tall on a 20 px pitch, so with two or more errors the links fail the 24×24 target-size minimum. They are also outside the spacing exception: axe measures 16.4 px of safe space instead of 24, and they are standalone list items, not inline text. The committed axe test scans only a server-side failed run, which has a single error box and no stacked links, so this state is not covered. That contradicts DASHBOARD.md's claim that axe's WCAG 2.2 rules (target size included) hold on a failed run.

**Evidence.** axe2.mjs (@axe-core/playwright, tag wcag22aa) at 1280×900:
- 1 error: link box [384,475,418,16], 0 violations.
- 2 errors: boxes [384,475,418,16] and [384,495,303,16], target-size (serious): 'Target has insufficient size (302.5px by 16px ...)', 'Safe clickable space has a diameter of 16.4px instead of at least 24px'.
- 3 errors: the same violation.
- At 375 px the links wrap to 36 px tall, so there is no violation.
Screenshot: shots/sim-errors-1280-3.png. The links themselves work: clicking one focuses its field (errlink.mjs).

**Reproduction.** Run `uv run --frozen atlas api serve --port 9240`, then `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node <scratch>/axe2.mjs`. On /simulator the script fills horizon 'abc' and multiplier '9', clicks Run simulation, and runs AxeBuilder.withTags(['wcag22aa']).

**Consequence.** Users with motor impairments can mis-tap between the adjacent error links. It is a WCAG 2.2 AA failure in a state the documentation says is covered.

**Recommended repair.** Give the summary links a 24 px target: for example `.callout li a { display: inline-block; min-height: 24px; line-height: 24px; }`, or `.callout li + li { margin-top: 8px }`. Extend e2e/a11y.spec.ts to scan the simulator with two client-side field errors.

**Verification.**

- dashboard#1 reproduce: **CONFIRMED**, severity LOW. The core claim reproduces exactly:
- With two or more client-side field errors at desktop width, the error-summary links are 16px-tall targets on a 20.1px pitch.
- axe-core 4.13 (wcag22aa) reports a serious target-size violation, with the box sizes and the 16.4px figure the finder reported.
- With one error, or at 375px where the links wrap to 36px, axe reports nothing.
- The committed axe test covers only the server-side 504 failure state, so this state is untested.

I am not marking the verdict down for two refinements:
1. Against the strict 2.5.8 wording, the finder's exact pair is a borderline pass (0.14px margin), because axe's per-fragment heuristic is stricter than the bounding-box text. However, other reachable pairs with similar link widths fail the wording outright while passing axe. The defect is real, and it is not fully caught by axe alone.
2. The DASHBOARD.md claim that axe passes on "a failed run" is literally true for the tested 504 run, so it is not strictly contradicted. The broader WCAG 2.2 AA target claim is not met in this state, though.

I kept LOW severity. The gap appears only in a transient error state of the simulator. Each error is also shown next to its own field, and the fields themselves are 36px-tall targets, so users have a large-target path. The fix is a one-line CSS change plus a test.

## M8-28

**Legacy-window backtest misdescribed on the Forecasting page (targets past the data; ranking lacks its descriptive-only caveat)**

- Severity: **LOW** (gate H); fix complexity: small; alters published research results: no
- Found by: dashboard#2 (LOW: Legacy-window backtest is misdescribed: 'Target days … Jul 29, 2026' beyond the data, and the model ranking lacks its descriptive-only caveat)
- Files: `docs/results/atlas-m2-rolling-origin-v1/full/metrics.json:50719-50723`, `src/atlas/evaluation.py:842-843`, `web/src/views/Forecasting.tsx:283-290`, `web/src/views/Forecasting.tsx:388-470`, `web/src/views/Forecasting.tsx:650-656`

**Description.** **(a) Target days.** The Forecasting page's Reproducibility table shows 'Target days Mar 4, 2026 – Jul 29, 2026' for the legacy window. That value is the committed M2 period metadata, `last_target` = last origin + 30 (evaluation.py:843). It ignores the truncation at the end of the series that `allow_incomplete_horizons` permits. No legacy-window target after Jun 30, 2026 exists or was scored. Yet the same page says the days from Jul 1 'are not in the frozen snapshot, so it has never been scored'.

**(b) Missing caveat.** The 'Models against baselines' panel has its own period selector. With 'Legacy window' chosen it ranks the models by 'Mean absolute error over horizons 1-30' (ATLAS 543 against B* 798). The 'descriptive only; horizons may be incomplete' callout appears only in the separate horizon panel (line 283), or in a collapsed disclosure's notes.

**Evidence.** - API /api/v1/forecasts/metrics?scope=bucket returns periods[legacy_window] = {first_origin 2026-03-03, last_origin 2026-06-29, first_target 2026-03-04, last_target 2026-07-29}.
- The rows' n values show truncation: atlas_linear_regression days_15_30 n=1560 = Σ_{h=15..30}(120−h), against 1904 for complete horizons; days_1_30 n=3135 against 3570.
- The dashboard table renders 'Legacy window | descriptive | Mar 3, 2026 – Jun 29, 2026 | Mar 4, 2026 – Jul 29, 2026 | 119' (tables.json).
- legacy.mjs selects Legacy window in the Models panel: the visible text has bars 543·recommended and 798·best baseline, and /descriptive only|incomplete/ does not match outside the closed disclosure (printed false).

**Reproduction.** 1. Run `uv run --frozen atlas api serve --port 9240` and open /forecasting.
2. Read the Reproducibility table's Legacy window row.
3. In 'Models against baselines', choose Legacy window.
4. Alternatively, run <scratch>/legacy.mjs and `curl -s 'http://127.0.0.1:9240/api/v1/forecasts/metrics?scope=bucket' | jq '.data.periods'`.

**Consequence.** This is a small provenance and honesty inconsistency in the scientific tables. The table implies scoring on days the data does not contain, which contradicts the archived-projection disclaimer. A reader comparing periods in the ranking sees an easier, partly truncated window's larger lead, with no period caveat next to the bars. Headline numbers are not affected.

**Recommended repair.** In the dashboard (no change to published results):
- Show the scored target range, clamping `last_target` to `snapshot_last_date`, or label the column 'Nominal target range'.
- Render the same legacy-window Callout in ModelComparisonPanel when `period === 'legacy_window'`.
- Optionally document in API.md that `last_target` is nominal.

**Verification.**

- dashboard#2 reproduce: **CONFIRMED**, severity LOW. Both parts of the finding reproduce exactly as described.

(a) The Reproducibility table labels the column "Target days" and shows a range ending Jul 29, 2026. That value is computed nominally as last origin + 30. The committed run scored no legacy-window target after 2026-06-30: 435 records per model are beyond_snapshot, and the n values match truncation arithmetically. The same page says Jul 1-30 are outside the snapshot and were never scored, so the table contradicts the page's own disclaimer. The API does not pass `allow_incomplete_horizons` to the client, so nothing on the page signals the truncation.

(b) The model ranking can be switched to Legacy window. It then shows the 543 vs 798 bars under a "horizons 1-30" subtitle, and no visible descriptive-only or incomplete-horizons caveat appears. That caveat exists only in a different panel and inside a collapsed disclosure.

Limits of the claim:
- Headline confirmation and development numbers are unaffected.
- The period's role, "descriptive", is shown elsewhere on the same page (Reproducibility table).
- BACKTESTING.md accurately states that targets beyond the snapshot are recorded as beyond_snapshot.
- I make no claim that the truncation itself inflates ATLAS's lead.

This is therefore a small provenance and labelling inconsistency in a research dashboard, not a scientific error. LOW (gate L, also touching H) is the [...]

## M8-29

**Time-zone safety of dashboard dates is untested (tests only run in UTC)**

- Severity: **LOW** (gate K); fix complexity: small; alters published research results: no
- Found by: dashboard#3 (LOW: Time-zone safety of dates is untested: the 'whatever the time zone' test only runs in UTC, and a mutation removing UTC formatting passes CI-equivalent tests)
- Files: `/.github/workflows/ci.yml:201,231`, `web/playwright.config.ts`, `web/src/lib/format.test.ts:42-46`, `web/src/lib/format.ts:107-135`

**Description.** The implementation is correct today: I verified it in six emulated time zones. But no automated test would catch a regression. The unit test named 'formats calendar dates in UTC, whatever the time zone' runs in the process time zone, and CI uses ubuntu-24.04 runners (UTC) with no TZ override. No Playwright project sets timezoneId (grep finds no TZ, timeZone or timezoneId in web/e2e or playwright.config.ts). An off-by-one-day date bug (a 2026-07-01 projection shown as Jun 30 in the Americas) is the classic failure for this kind of page.

**Evidence.** In the scratch tree I removed every `timeZone: 'UTC',` from src/lib/format.ts with sed (`grep -c timeZone` now prints 0) and ran `npx vitest run src/lib/format.test.ts src/charts/charts.test.tsx src/views/views.test.tsx`:
- With TZ=UTC, format.test.ts passed. The only failure was the unrelated, load-flaky views heading wait.
- With TZ=America/Los_Angeles, it failed: 'formats calendar dates in UTC, whatever the time zone'.
- Unmutated, all 20 tests passed under UTC and America/Los_Angeles.

**Reproduction.** `git archive HEAD | tar -x -C <scratch>/tree`; `cd <scratch>/tree/web && npm ci --ignore-scripts`; `sed -i "s/  timeZone: 'UTC',//" src/lib/format.ts`; then `TZ=UTC npx vitest run src/lib/format.test.ts` (passes) and `TZ=America/Los_Angeles npx vitest run src/lib/format.test.ts` (fails).

**Consequence.** A future change to date formatting or parsing could shift every date shown (projection start, history end, holiday markers) by a day for users west of UTC, and CI would stay green.

**Recommended repair.** Pin a non-UTC zone in the test run: `"test": "TZ=America/Los_Angeles vitest run"`, or set process.env.TZ in vitest globalSetup. Add a Playwright project or test with `timezoneId: 'Pacific/Kiritimati'` (and one negative-offset zone) that asserts 'Projected from Jun 30, 2026', 'Day 1 Jul 1, 2026' and the Fri Jul 3 holiday reading.

**Verification.**

- dashboard#3 reproduce: **CONFIRMED**, severity LOW. The finding reproduces in full. The implementation is correct today: unmutated code passes in UTC, a negative-offset zone and a positive-offset zone. But the only test whose name promises time-zone independence runs in whatever zone the process has, and CI does not set one. CI runs on ubuntu-24.04 (UTC), and Playwright has no timezoneId and no date assertions.

Removing every timeZone: 'UTC' leaves the whole CI-equivalent unit suite green under UTC: 166/166, coverage gate passes. The same mutation fails immediately under America/Los_Angeles. A second kind of regression, parsing dates in local time, slips past UTC and also past Los Angeles, and is caught only east of UTC (Kiritimati). So the finder's proposed repair is incomplete: pinning only TZ=America/Los_Angeles would miss this second kind. Robust coverage needs at least one negative-offset zone and one positive-offset zone, for example running Vitest twice, or Playwright projects with timezoneId 'America/Los_Angeles' and 'Pacific/Kiritimati'.

DASHBOARD.md presents UTC date formatting as tested, which overstates the coverage slightly. This is not disclosed as a limitation.

Severity is LOW (gate K). No defect exists today; the gap is in regression protection for an off-by-one-day class of bug on a portfolio dashboard. The data is archived, so a regression would mislabel dates but would not compromise integrity or security.

## M8-30

**Vitest view tests are timing-flaky under CPU load (1 s default async waits)**

- Severity: **LOW** (gate K); fix complexity: small; alters published research results: no
- Found by: dashboard#4 (LOW: Vitest view tests are flaky under CPU load (1 s default waits for async queries)); release#3 (MEDIUM: The Vitest suite fails under CPU contention: most async queries rely on testing-library's 1 s default while a page render takes several seconds)
- Files: `web/src/App.test.tsx:22`, `web/src/test/setup.ts`, `web/src/views/review.test.tsx`, `web/src/views/review.test.tsx:50`, `web/src/views/round2-checks.test.tsx`, `web/src/views/round2-views.test.tsx`, `web/src/views/views.test.tsx`, `web/src/views/views.test.tsx:22`, `web/vite.config.ts`

**Description.** Many view tests wait for lazily loaded pages with findByRole or findByText and Testing Library's default 1000 ms timeout. src/test/setup.ts does not configure asyncUtilTimeout. vite.config.ts raises only the per-test timeout to 20 s, citing 'a loaded CI runner'. On a busy machine the first lazy import plus render exceeds 1 s, and different tests fail from run to run.

**Evidence.** Load average was 6.6-9.4 on 4 vCPUs (other auditors running):
- A full `npx vitest run` in the scratch tree gave 'Tests 3 failed | 163 passed (166)': review.test.tsx 'is not taken from the first test the API happens to list', round2-checks.test.tsx 'says a test M3 could not compute…', and views.test.tsx 'forecasting: … every filter'.
- Re-running those three files: 'Tests 3 failed | 15 passed (18)', with a different round2-checks test failing ('Unable to find role="button" and name "Run simulation"').
- An unmutated run under TZ=Pacific/Kiritimati failed with 'Unable to find role="heading" and name "Forecasting"'.
- In isolation (4 sequential runs, and 2 runs pinned with taskset -c 0) all passed.
- `grep -rn "findByRole('heading'" src | grep -v timeout` finds 9 waits on the default timeout.

**Reproduction.** In the scratch tree with node_modules: `cd <scratch>/tree/web && npx vitest run`, run while other CPU-heavy jobs are active (or run several vitest invocations in parallel). Expect intermittent 'Unable to find …' failures from findBy* queries.

**Consequence.** Spurious red CI or release-gate runs, especially when the suite runs alongside Playwright or the 30-minute Python suite. They erode trust in the gate, and genuine failures may be dismissed as flakes.

**Recommended repair.** In src/test/setup.ts add `configure({ asyncUtilTimeout: 5000 })` from @testing-library/react. Alternatively, pass `{ timeout: 5000 }` to the initial page-heading waits, matching the existing 5 s waits.

**Independent second report (release#3).** web/vite.config.ts:46-49 says 'A whole page rendered from fixtures takes 3-4 s with coverage on a busy machine'. Of the 90 findBy*/findAllBy* calls, only about 30 pass an explicit timeout (5000 ms). The rest use testing-library's default asyncUtilTimeout of 1000 ms, and setup.ts never raises it. Examples: views.test.tsx:22 (`findByRole('heading', { name: 'Forecasting', level: 1 })` right after navigation), review.test.tsx:50 and :71.

**Verification.**

- dashboard#4 reproduce: **CONFIRMED**, severity LOW. The finding is confirmed.

- **Cause:** the first wait after rendering a lazy-loaded page often uses Testing Library's 1 s default timeout. Nothing in setup.ts or vite.config.ts raises it.
- **Effect:** under CPU contention the lazy import plus render takes more than 1 s, and different tests fail from run to run. I reproduced this myself:
  - the failures happen with the Suspense fallback still on screen;
  - instrumentation timed three default-timeout waits at over 1000 ms under load;
  - even at moderate load in CI mode the slowest wait used 79% of the budget.
- **Limit of the proposed fix:** a 5 s default halves the failures under extreme contention (8 to 4), but it does not remove them. At load around 24 on 4 vCPUs, the repo's existing explicit 5 s waits also fail. Making the waits consistent helps, but nothing fully protects against extreme oversubscription.

The consequence is overstated for CI. In CI, Vitest runs alone in its own job and never alongside Playwright or the Python suite. In every run I did at load 6-13 (2x to 3x oversubscription), all 166 tests passed, including a coverage-mode run. The realistic risk is a spurious red result on a noisy CI runner, or on a local or release-gate run done at the same time as other heavy jobs. This only makes tests flaky: the product is unaffected and the tests still detect real defects.

Severity is LOW (gate K): it is [...]
- release#3 reproduce: **CONFIRMED**, severity MEDIUM. The finding reproduces independently and the cause is confirmed.
- About 59 % of the async queries (53 of 90) wait only testing-library's 1000 ms default. Setup never raises it.
- A first page render from fixtures regularly takes more than 1 s on a contended 4-vCPU host. The repository's own config comment admits 3-4 s.
- The failing queries always land on calls without a timeout (review.test.tsx:50, :93; views.test.tsx:22).
- In alternating runs under the same load, the unmodified tree failed every time (4 of 4 runs, 1-2 tests each). The one-line global `configure({ asyncUtilTimeout: 10_000 })` passed every time (4 of 4, 29/29). The full coverage suite went from 2 failures to 166/166.
- A timeout in one test can apparently cause a failure in the next one (review.test.tsx:62), because the singleton ResourceStore accepts writes from loads that finish after `clear()`. That makes the flakiness worse than one isolated test.
- The problem was seen at modest load too: the first, cold run failed at load 2.75-5.4. CI always runs cold.

Severity: MEDIUM, not HIGH.
- It is a reliability defect in the test gate (gate K), not a product defect, and the fix is trivial.
- GitHub-hosted runners are usually far less loaded than this audit host, so a red CI there is plausible but less frequent than here. I could not observe actual CI history, because I contacted no external services.
- It still [...]
- release#3 challenge: **PARTIALLY_CONFIRMED**, severity LOW. The technical claim holds.
- 53 of the 90 findBy calls rely on the 1 s default.
- The first lazy-route render in each worker can exceed 1 s under CPU contention, and I reproduced the failures myself: 2-3 of 29 tests in the three named files, and 2 of 166 in the full coverage run.
- Raising asyncUtilTimeout alone makes the suite pass under even heavier load. The repair is correct and costs one line.
- It also goes against the repository's own stated intent: commit e6468ae and the vite.config.ts comment raised the test timeout specifically for "a busy runner", but left the per-query waits at 1 s.
- Nothing in the docs discloses it.

What I could not confirm is the headline consequence, a red CI release gate.
- The only two CI runs that include the dashboard job are green.
- On the dedicated ubuntu-24.04 runner the affected tests finish whole in about 0.5 s, about 2x headroom under the 1 s wait.
- A red CI run needs roughly a 2x slowdown of the runner, i.e. noisy-neighbour degradation on a hosted VM. That is possible but not observed and probably rare.
- Even at about 6.7x slowdown on this host, only 2 of 166 tests failed.

The problem is confined to test-harness robustness. It does not touch product correctness, security, the scientific claims or deployment. A local reproducer on a quiet machine would very likely see a pass, and anyone on a loaded machine sees a spurious failure [...]

**Lead reconciliation.** release#3 finder MEDIUM; votes MEDIUM, LOW, LOW. CI is green; contention-only. Lead: LOW.

## M8-31

**Dashboard 'Methods' links point at the mutable main branch, not the release**

- Severity: **LOW** (gate L); fix complexity: small; alters published research results: no
- Found by: dashboard#5 (LOW: Methods & provenance page links to documentation on GitHub's moving `main` branch, not the released version); claims#7 (LOW: Dashboard 'Methods' links point at the mutable main branch, not the release the numbers come from)
- Files: `web/src/views/Provenance.tsx:24`, `web/src/views/Provenance.tsx:280-296`, `web/src/views/Provenance.tsx:290`

**Description.** `const REPOSITORY = 'https://github.com/arthur-fay/demand-forecasting-model/blob/main/docs'`. Every 'Methods' link (DATA_SNAPSHOT.md, BACKTESTING.md, UNCERTAINTY.md, OPTIMIZATION.md, API.md, DASHBOARD.md) points at `main`. A deployed v1.0 dashboard shows fingerprints of a specific release but links to whatever the method documents say later. If the repository is private, viewers of the deployment get GitHub 404s.

**Evidence.** In the committed bundle, `grep -o 'https\?://[a-z0-9.-]*' web/dist/assets/Provenance-CiRCJuIt.js` returns https://github.com. keys.mjs lists the six links ('DATA_SNAPSHOT.md (opens GitHub)', …) on /provenance. Source line Provenance.tsx:24.

**Reproduction.** Open /provenance and inspect the link hrefs, or run `grep -n REPOSITORY web/src/views/Provenance.tsx`.

**Consequence.** Weaker provenance: the methods a viewer reads may not match the results shown once main moves on after the release.

**Recommended repair.** Pin the links to the release tag, derived reproducibly from package.json's version: for example `define: { __ATLAS_DOCS_REF__: JSON.stringify('v' + version) }` and `blob/${__ATLAS_DOCS_REF__}/docs`. Alternatively, ship the docs with the release. Tag the release accordingly.

**Independent second report (claims#7).** The Methods & provenance page links every milestone's documentation to `https://github.com/arthur-fay/demand-forecasting-model/blob/main/docs/<DOC>.md`. A deployed v1.0 dashboard serves frozen, fingerprinted results, but its method links resolve to whatever main holds later. The M8 documentation work will already change these files after the release. Elsewhere the provenance design pins everything to fingerprints and commits (release.json records the commit). If the repository is private, these links also 404 for dashboard viewers without GitHub access.

**Verification.**

- dashboard#5 reproduce: **CONFIRMED**, severity LOW. The finding is confirmed in the source (Provenance.tsx:24 and :290), in the committed build (web/dist/assets/Provenance-CiRCJuIt.js) and in the rendered page served by the real API. All six Methods links point to the moving `main` branch, not to a fixed release. The same page shows fixed release facts (0.7.0, snapshot fingerprint 5ab8b3a59e107158). Git history shows the method documents keep changing after their milestone (API.md was edited in both M6 and M7), so after v1.0 a viewer could read methods that no longer match the numbers on screen. No release tag exists to pin to, and the docs do not mention this anywhere.

The 404 part of the finding is conditional and I could not verify it, because checking GitHub was out of bounds. DEPLOYMENT.md's anonymous clone suggests the repository is public, in which case the links resolve and the problem is drift only.

I agree with LOW: it weakens documentation provenance (gate L), but it does not affect data, results, security or deployment. Any fix needs a release tag to exist (none does yet) and a rebuild of the committed web/dist, including its manifest.
- claims#7 reproduce: **CONFIRMED**, severity LOW. The core claim reproduces exactly. Both the source (Provenance.tsx:24, 290-291) and the committed, fingerprinted build (web/dist/assets/Provenance-CiRCJuIt.js) hard-code `blob/main/docs`. The rendered Provenance page shows six '(opens GitHub)' links, all pointing at main.

The drift risk is real, not hypothetical. Git history shows the linked docs changing on main after their milestone was frozen: API.md in M5, M6 and M7, and UNCERTAINTY.md in a post-M3 fix PR. A deployed release that pins its commit in release.json and fingerprints every result will therefore send viewers to documentation that may describe different code or results. Nothing in the docs discloses this.

One part of the finder's framing is slightly loose. If the M8 doc changes merge before the v1.0 tag and the dashboard is rebuilt, main matches the release at release time, and drift starts only with later commits. This does not change the finding.

One caveat on the proposed repair. Because web/dist is committed, a build cannot embed its own commit hash, so the workable options are:
- a version-derived tag such as `v${version}`, which must then actually be created (no tags exist today), or
- serving the docs with the build.

Severity stays LOW. No number is wrong and nothing is a security issue. It is a provenance-consistency gap and a possible broken-link problem in a private deployment, so it fits gate L.

## M8-32

**Pinned GitHub Actions target the deprecated Node.js 20 runtime (CI warning on every job)**

- Severity: **LOW** (gate K); fix complexity: small; alters published research results: no
- Found by: supplychain#4 (LOW: Pinned GitHub Actions target the deprecated Node 20 runtime; every CI job is now forced onto Node 24 with a deprecation warning)
- Files: `.github/workflows/ci.yml:171`, `.github/workflows/ci.yml:235`, `.github/workflows/ci.yml:25`, `.github/workflows/ci.yml:32`, `docs/ROADMAP.md:271-273`

**Description.** The four actions pinned by SHA declare `runs.using: node20` in their action.yml at those commits: checkout v4.4.0, setup-python v5.6.0, setup-node v4.4.0 and upload-artifact v4.6.2. GitHub deprecated Node 20 for Actions; the runner source (actions/runner main, Constants.cs) gives a Node 24 default date of 16 June 2026 and a Node 20 removal date of 23 September 2026, both in the past. The latest main run therefore forces these actions onto a runtime they were not built for and emits a warning annotation. Newer majors use node24: checkout v5.0.0, setup-python v6.0.0, setup-node v5.0.0, upload-artifact v6.0.0; the latest tags are v7.x. ROADMAP.md:271 says 'CI action pinning (resolved in M5)' but sets no update mechanism, so the pins age silently.

**Evidence.** Shallow-fetching each pinned SHA and reading action.yml shows `using: node20` for all four; v5.0.0 checkout, v6.0.0 setup-python, v5.0.0 setup-node and v6.0.0 upload-artifact show node24. Job 117086877714 of CI run 38934624918 (the 194fa4a merge, 2026-10-16) logged '##[warning]Node.js 20 is deprecated. The following actions target Node.js 20 but are being forced to run on Node.js 24: actions/checkout@11d5960..., actions/setup-node@49933ea..., actions/setup-python@a26af69...'. setup-node's post step logged '[DEP0040] punycode' and '[DEP0169] url.parse()' DeprecationWarnings. actions/runner src/Runner.Common/Constants.cs has Node24DefaultDate = "June 16th, 2026" and Node20RemovalDate = "September 23rd, 2026".

**Reproduction.** git init t && git -C t fetch --depth 1 https://github.com/actions/checkout 11d5960a326750d5838078e36cf38b85af677262 && git -C t show FETCH_HEAD:action.yml | grep using (repeat for the other three). Read the tail of job 117086877714's log on run 38934624918.

**Consequence.** CI works today only because the runner remaps node20 to node24. Each run of a v1.0 portfolio repository shows deprecation warnings, and a later runner change could break the pinned actions with no automated prompt to update.

**Recommended repair.** Re-pin to node24 releases by full SHA with version comments (checkout v5+, setup-python v6+, setup-node v5+, upload-artifact v6+), checked with git ls-remote as in M5. Add the github-actions ecosystem to a Dependabot config so the pins are refreshed (see the update-policy finding). Update the ROADMAP note.

**Verification.**

- supplychain#4 reproduce: **CONFIRMED**, severity LOW. I re-ran every factual claim of the finding myself, and each one holds.
- All four SHA-pinned actions declare node20.
- They are the newest releases of their majors, so a major-version bump is the only fix.
- The runner's Node 24 default date (2026-06-16) and Node 20 removal date (2026-09-23) have both passed.
- The CI run for the audited commit 194fa4a shows the forced-to-Node-24 deprecation warning in every one of its three jobs.
- No Dependabot or Renovate config exists, and no document discloses the issue.

One nuance: upload-artifact does not appear in the warning because its failure-only step was skipped. It is still node20 and would be forced if it ran.

There is also a small stale-documentation point the finding underplays: the ROADMAP 'resolved in M5' note lists only two of the four pinned actions.

LOW is the right severity, not higher. CI currently passes because the runner remaps the actions to node24. Nothing is insecure: SHA pinning remains intact, and the risk is a supply-chain hygiene and maintenance problem, not exposure. The practical effects are visible deprecation annotations on a portfolio repository and possible future breakage if GitHub stops the remapping, with nothing in place to prompt an update.

What I could not verify: the runner's exact post-removal-date code path (whether it will ever hard-fail node20 instead of forcing it). My git grep over the [...]

**Lead reconciliation.** Also observed by the lead in the post-merge CI log of 194fa4a (job 117086877714).

## M8-33

**The two-week dependency cooldown is described as enforced but is a manual rule; its caps never expire**

- Severity: **LOW** (gate M); fix complexity: small; alters published research results: no
- Found by: supplychain#5 (LOW: The two-week release-age cooldown is described as enforced but is a manual rule; its three constraints never expire)
- Files: `docs/DASHBOARD.md:484`, `docs/ROADMAP.md:274-276`, `pyproject.toml:44-50`

**Description.** ROADMAP.md:274-276 says 'Since M4, new packages are held to releases at least two weeks old through `tool.uv.constraint-dependencies`'. That setting only caps three named packages (optype<0.19, starlette<1.8, truststore<0.10.5). It does not hold any other new or upgraded package to a release age; the history complied only because of manual review. DASHBOARD.md:484 states this honestly for npm ('a review rule, not enforced by a tool'), but the Python wording overstates the mechanism. The caps also never lapse: starlette 1.8.0 (published 2026-10-13) passes the two-week mark on 2026-10-27 but stays blocked until someone edits pyproject.toml, including if it carries a security fix. The pinned uv 0.11.32 already supports a tool-enforced cooldown (`exclude-newer` accepts friendly or ISO-8601 durations such as '2 weeks').

**Evidence.** scratch/uvlock_entry_age.py over every uv.lock revision: the 10 packages younger than 14 days at entry all entered at M0 (09376eb, disclosed); none from M2-M7. So the practice held, but only by review. `uv help lock` (0.11.32) documents --exclude-newer as accepting 'a "friendly" duration (e.g., 24 hours, 1 week, 30 days), or an ISO 8601 duration'. PyPI publish dates: starlette 1.8.0 2026-10-13, truststore 0.10.5 2026-10-12, optype 0.19.0 2026-10-02 (optype's cooldown has already passed and its cap still stands).

**Reproduction.** Run scratch/uvlock_entry_age.py (replays `git show <rev>:uv.lock` for each lock revision and compares upload-time with the commit date); `uv help lock | grep -A12 'exclude-newer <'`; curl https://pypi.org/pypi/starlette/json for release dates.

**Consequence.** A future `uv lock --upgrade` or a newly added dependency can pull a release published minutes earlier despite the documented policy, while the stale caps can silently hold back fixes. The impact is limited to maintenance and documentation accuracy.

**Recommended repair.** Either set `exclude-newer = "2 weeks"` under [tool.uv] (first check that `uv lock --locked` stays stable as time passes, otherwise use a dated RFC 3339 value bumped on each upgrade) and drop the per-package caps, or reword ROADMAP.md:274-276 to say the cooldown is a review rule like npm's and give each constraint an explicit review date.

**Verification.**

- supplychain#5 reproduce: **CONFIRMED**, severity LOW. The core claim holds. ROADMAP.md:274-276 describes `tool.uv.constraint-dependencies` as the general way new packages are held to two-week-old releases. DEVELOPMENT.md:101-104 says the same, and DASHBOARD.md:110-114 implies that Python, unlike npm, has an enforcing setting. In fact the setting is three static, package-specific caps. Running `uv lock --upgrade` on HEAD today pulls hypothesis, pluggy and ruff releases that are 0.1 to 1.4 days old. The lock history shows the policy held from M2 to M7 only because of manual review.

The caps never expire: starlette 1.8.0 and truststore 0.10.5 stay blocked past 2026-10-26/27, and optype 0.19.0 past 2026-10-16T23:14Z, until someone edits pyproject.toml. One detail in the finding is slightly off: optype's cooldown had not quite passed at audit time (13.8 days old). Every other number reproduced exactly: the M0 young-package list, the PyPI dates, and uv 0.11.32's support for duration values in exclude-newer.

The repair works mechanically: relative exclude-newer records `exclude-newer-span = "P2W"` and `--locked` passes. But adopting it now would downgrade 8 M0-era packages, numpy among them, so the result hashes would have to be checked again.

Impact is limited to documentation accuracy and maintenance hygiene. The current lock is fine apart from the already-disclosed M0 entries, and nothing is exploitable. So LOW, under gate M (it [...]

## M8-34

**'Decision-intelligence' marketing language in package metadata and the dashboard heading**

- Severity: **LOW** (gate L); fix complexity: small; alters published research results: no
- Found by: claims#4 (LOW: 'Decision-intelligence' marketing language in package metadata and the dashboard's main heading)
- Files: `pyproject.toml:4`, `web/e2e/helpers.ts:5`, `web/src/App.test.tsx:21`, `web/src/views/Overview.tsx:381`

**Description.** pyproject.toml:4 describes the package as 'ATLAS decision-intelligence engine: ...'. The dashboard's landing H1 (Overview.tsx:381) is 'Decision-intelligence research on NYC 311 demand'. 'Decision intelligence' is a commercial category term for decision-support systems. ATLAS makes no decisions: M4 is an exploratory LP over HYPOTHETICAL parameters, and every surface says it is 'never a recommendation'. The heading is the first line of the dashboard. The pyproject description is what package indexes, `pip show` and GitHub metadata derived from it display. The phrase sits beside a body text that is otherwise careful.

**Evidence.** grep -rn -i 'decision-intelligence' finds pyproject.toml:4 and web/src/views/Overview.tsx:381. Unit and e2e tests also assert the heading: web/src/App.test.tsx:21,130,275; web/src/round2-shell.test.tsx:85,220; web/e2e/helpers.ts:5; keyboard.spec.ts:6,41; failures.spec.ts:39. The rendered Overview (Chromium dump) shows 'Decision-intelligence research on NYC 311 demand' as the H1.

**Reproduction.** cd . && grep -rn -i 'decision-intelligence' pyproject.toml web/src web/e2e

**Consequence.** It overstates what the software is, and conflicts with the 'exploratory, hypothetical, not a recommendation' framing a reviewer reads everywhere else.

**Recommended repair.** pyproject.toml:4: 'ATLAS: reproducible research on NYC 311 daily demand: data foundation and quality checks, rolling-origin backtests, prediction intervals (failed calibration), a HYPOTHETICAL linear-programming simulation, an HTTP API, a deployment package and a web dashboard (milestones M0-M7).' Overview H1: 'Forecasting research on NYC 311 demand' (or 'NYC 311 demand: forecasts, uncertainty and hypothetical scenarios'). Update the test and e2e heading literals in the same change and rebuild web/dist.

**Verification.**

- claims#4 reproduce: **CONFIRMED**, severity LOW. The factual core reproduces exactly:
- pyproject.toml:4 calls ATLAS a "decision-intelligence engine", and the installed distribution metadata shows that text.
- The dashboard's landing H1 at web/src/views/Overview.tsx:381 is "Decision-intelligence research on NYC 311 demand". It renders as the only H1 of the committed web/dist build in Chromium.
- Every test and e2e line the finder listed hard-codes that literal.

The phrase is inconsistent with the rest of the project's framing:
- The README, the HTML title and the docs describe forecasting research with a HYPOTHETICAL LP simulation that is "never a recommendation", and no doc qualifies the "decision-intelligence" label.
- "Engine" in particular claims more than an exploratory research demonstration delivers.

The severity stays LOW for these reasons:
- It is a wording and framing issue, not a false number or a hidden limitation.
- The dashboard H1 says "research", and the very next paragraph plus the M4 panel state HYPOTHETICAL and "never recommendations".
- "Decision intelligence" can reasonably describe decision-support research, so the overstatement is about tone, not substance.

One part of the finding's consequence is wrong: GitHub does not derive repository metadata from pyproject.toml. That point is minor and does not change the verdict.

A fix touches pyproject.toml:4, Overview.tsx:381 and the 9 test/e2e literals, [...]

## M8-35

**A pre-release version string (1.0.0rc1 / 1.0.0-rc.1) is refused by three validators; no versioning policy**

- Severity: **LOW** (gate G); fix complexity: small; alters published research results: no
- Found by: release#2 (MEDIUM: A pre-release version (1.0.0-rc.1 or PEP 440 1.0.0rc1) is refused by three independent validators, while the dashboard build accepts it)
- Files: `deploy/scripts/build-release.sh`, `src/atlas/dashboard.py`, `src/atlas/deploy/layout.py`, `tests/test_m6_manifest.py`, `web/build/atlas-manifest.ts`

**Description.** Three places require plain MAJOR.MINOR.PATCH: build-release.sh:96 (`[[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "unexpected project version"`), the release id regex in layout.py:47 (`[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{12}`), and the dashboard manifest validator in dashboard.py:91/183-184. The Vite plugin (web/build/atlas-manifest.ts) writes any package.json version without validating it. The semver spelling '1.0.0-rc.1' (package.json) and the PEP 440 canonical '1.0.0rc1' (what uv records for pyproject) also differ, so no single RC literal works in both ecosystems. Separately, tests/test_m6_manifest.py:39 derives the version with `release.name.split('-')[0]`, which assumes the version contains no hyphen. No versioning or RC policy is documented, and the clone has no git tags.

**Evidence.** Script rc_version.py: '1.0.0' gives release_id_ok=True, dashboard_manifest_ok=True, build_release_sh_ok=True. '1.0.0-rc.1' and '1.0.0rc1' are False on all three. packaging normalises '1.0.0-rc.1' to '1.0.0rc1'. In a scratch tree, package.json version set to 1.0.0-rc.1 plus `npx vite build --outDir $S/dist-rc`: the build succeeds and writes "version": "1.0.0-rc.1". `atlas.dashboard.load_build($S/dist-rc)` then raises DashboardError 'the dashboard version must be MAJOR.MINOR.PATCH'.

**Reproduction.** S=<audit-scratch>/release; .venv/bin/python $S/rc_version.py; then in $S/tree/web, temporarily set the package.json version to 1.0.0-rc.1, run `npx vite build --outDir $S/dist-rc --emptyOutDir`, and call load_build on it.

**Consequence.** Numbering the M8 release candidate 1.0.0-rc.1 (or 1.0.0rc1) breaks CI: test_m7_dashboard loads the committed dist, and the m6 tests' make_release raises ManifestError. It also makes build-release.sh die on the server. Only a plain 1.0.0 can be built and deployed, so the RC label has to live outside the version, and nothing documents that.

**Recommended repair.** Decide and document a versioning policy. Simplest: give the RC the version 1.0.0 and put the RC status in a git tag (v1.0.0-rc.1) and the release notes. Alternatively, widen the three validators to one explicit shared pattern (for example PEP 440 `N.N.N(rcN)?`), map it to semver for package.json, and fix test_m6_manifest.py:39.

**Verification.**

- release#2 reproduce: **CONFIRMED**, severity LOW. Every factual claim reproduces:
- Three independent validators (build-release.sh:96, layout.py:47, dashboard.py:91/183) accept only plain N.N.N.
- The Vite plugin writes any package.json version and the build succeeds. The Python loader then rejects that build.
- The semver spelling (1.0.0-rc.1) and the PEP 440 canonical spelling uv records (1.0.0rc1) differ, and both are rejected.
- No versioning or RC policy is documented, and there are no tags.
- test_m6_manifest.py:39's split('-')[0] does assume a hyphen-free version. Today that is moot, because valid_release_id rejects any such id before that line runs. It only matters if the validators are widened to semver.

So the verdict is CONFIRMED. I calibrate severity down from MEDIUM to LOW:
- The constraint fails closed and fails early. The Python validators all agree with each other, and an RC version would be caught in CI (test_m7_dashboard's load_build, the m6 make_release/build_manifest path) long before any server. No bad release can be built or deployed silently.
- It does not affect correctness, security or scientific honesty.
- The repair is cheap and needs no code change: version the RC 1.0.0 and carry the RC status in a git tag (v1.0.0-rc.1) and in the release notes. Then add a one-line versioning policy to DEPLOYMENT.md/DEVELOPMENT.md. Optionally add a matching version check to the Vite plugin so `npm run build` fails [...]
- release#2 challenge: **PARTIALLY_CONFIRMED**, severity LOW. The mechanics are accurate and reproducible, but the finding overstates them as a MEDIUM gate-G defect.

The three "independent validators" agree with each other. That is a consistent, deliberate strictness, not an inconsistency. Release ids are also directory names under /opt/atlas/releases and /opt/atlas/dashboard, and a tight regex there is defensive.

The only real inconsistency is that the Vite plugin does not check the version early. The Python loader still rejects it in CI (test_m7_dashboard loads the committed dist) and again at deploy, so nothing can be built or deployed wrongly. The failure is loud and closed, with a clear message.

The npm vs PEP 440 spelling mismatch comes from the two ecosystems, not from ATLAS. The split('-') fragility in test_m6_manifest.py:39 only matters after the validators are widened. Nothing in the repo plans a pre-release label: M8 is "not started", and any N.N.N version such as 1.0.0 builds and deploys. So gate G (deployment package) is not blocked, and the consequence is speculative unless the owner chooses an RC version string.

What survives is a documentation and policy gap. No versioning or RC policy exists, and DEPLOYMENT.md:109 and DASHBOARD.md:60 do not say the version must be MAJOR.MINOR.PATCH. That is worth fixing cheaply before tagging an RC: document "version 1.0.0, RC status in the git tag v1.0.0-rc.1 and the release notes", [...]

**Lead reconciliation.** Finder MEDIUM; both verifiers LOW.

## M8-36

**No test ties the version declarations together**

- Severity: **LOW** (gate K); fix complexity: small; alters published research results: no
- Found by: release#4 (LOW: No check ties the version declarations together: a pyproject-only bump passes the tests and fails only on the server)
- Files: `deploy/scripts/build-release.sh`, `pyproject.toml`, `src/atlas/__init__.py`, `src/atlas/deploy/verify.py`, `tests/test_cli.py`, `web/package.json`

**Description.** The version is declared independently in pyproject.toml:3, src/atlas/__init__.py:49 and web/package.json:3. Several derived copies follow from those: uv.lock, package-lock, the OpenAPI info.version, web/dist and the fixtures. CI catches some drift: pyproject vs uv.lock through `uv sync --locked`; __init__ vs openapi/fixtures through the contract and fixture tests; package.json vs dist through the rebuild diff. But no test compares pyproject with __init__, and nothing ever compares the dashboard version with the backend version. test_cli.py:120 compares `--version` with __version__, which is tautological. The mismatch is caught only at build time on the server (build-release.sh:145-147) or by post-deployment verification (verify.py:310-314).

**Evidence.** Scratch tree $S/tree_v: pyproject version set to 1.0.0, then `uv lock --offline` ('Updated atlas v0.7.0 -> v1.0.0') and `uv sync --locked`. importlib.metadata.version('atlas') is 1.0.0 while `atlas --version` prints 'atlas 0.7.0'. tests/test_cli.py, test_m6_manifest.py::test_the_manifest_identifies_the_release (which builds release '1.0.0-aaaaaaaaaaaa' with atlas_version 1.0.0), test_m6_scripts.py and test_m7_dashboard.py: 130 passed, 1 skipped. The OpenAPI diff and test_m5_endpoints.py:51 also stay green because __version__ is unchanged.

**Reproduction.** S=<audit-scratch>/release; git archive HEAD | tar -x -C $S/tree_v; sed -i 's/^version = "0.7.0"/version = "1.0.0"/' $S/tree_v/pyproject.toml; cd $S/tree_v && UV_PROJECT_ENVIRONMENT=$S/venv_v uv lock --offline && UV_PROJECT_ENVIRONMENT=$S/venv_v uv sync --locked; $S/venv_v/bin/python -m pytest tests/test_cli.py tests/test_m6_manifest.py::test_the_manifest_identifies_the_release tests/test_m6_scripts.py tests/test_m7_dashboard.py -q --no-cov -p no:cacheprovider; $S/venv_v/bin/atlas --version

**Consequence.** An incomplete M8 version bump passes CI and is caught only when build-release.sh runs on the VPS ('the release reports 0.7.0, not 1.0.0'). A dashboard left on its old version is never caught, so the Provenance page would show a dashboard version different from the API's atlas_version.

**Recommended repair.** Add one pytest asserting that pyproject [project].version == atlas.__version__ == web/package.json version == web/dist/atlas-dashboard.json version == docs/api/openapi.json info.version. Alternatively, derive __version__ from importlib.metadata so there is a single source.

**Verification.**

- release#4 reproduce: **PARTIALLY_CONFIRMED**, severity LOW. Most of the finding reproduces.

- No fast test ties the pyproject [project].version to atlas.__version__. A pyproject-only bump, with uv.lock refreshed, passes every unit, contract and fixture test I ran, and the OpenAPI diff stays clean.
- test_cli.py:120 only checks that `--version` echoes `__version__`, so it cannot catch this drift.
- Nothing anywhere ties the dashboard version (web/package.json and web/dist) to the backend version. A complete backend bump that leaves the dashboard at 0.7.0 passes the tests, the build-release version check and the verify.py identity check. The Provenance page would then show atlas-dashboard 0.7.0 next to ATLAS 1.0.0.

Not confirmed: the consequence that an incomplete bump "passes CI and is caught only when build-release.sh runs on the VPS". CI's deploy-e2e job runs build-release.sh on the HEAD commit through test_m6_container.py on every push and PR. My mirror of the check at lines 145-147 shows it dies with "the release reports 0.7.0, not 1.0.0", so the mismatch is caught in CI. The catch is slow and late: a container job of up to 60 minutes, not a unit test, and a local `uv run pytest` without ATLAS_E2E_CONTAINER would miss it. It is still not "only on the server".

The remaining real gaps are two:
- the slow feedback for the pyproject/`__init__` mismatch;
- dashboard/backend version drift, which nothing detects. No document sets a rule [...]

## M8-37

**The container e2e 'explicit rollback' targets the release that is already current**

- Severity: **LOW** (gate K); fix complexity: medium; alters published research results: no
- Found by: release#6 (LOW: The container e2e 'explicit rollback' targets the release that is already current, so it never exercises a real B->A switch)
- Files: `docs/DEPLOYMENT.md`, `tests/test_m6_container.py`

**Description.** test_a_broken_release_is_rolled_back_automatically (test_m6_container.py:608-614) leaves current == server.release. test_an_explicit_rollback (line 617-621) then runs `atlas-deploy rollback --to {server.release}`, which re-activates the current release. test_the_dashboard_follows_the_release (624-629) asserts the site root is server.release, which was already true before the 'rollback'. DEPLOYMENT.md:727 and :839 describe these as tests of 'the automatic and explicit rollbacks'. A switch between two healthy releases on a real systemd/nginx host, with the dashboard moving too, is covered only by fake-host unit tests (test_m6_releases.py:505, test_m7_deploy.py:398-418).

**Evidence.** Read from the test sequence: line 612 asserts `readlink /opt/atlas/current == releases/{server.release}`; line 618 rolls back `--to {server.release}`; line 620 asserts current == server.release. The only other release built in the fixture is the broken one (lines 129-175, _snapshot_commit). I did not run the container test (the lead runs it).

**Reproduction.** Read tests/test_m6_container.py:600-630 and _snapshot_commit (around lines 128-176).

**Consequence.** A regression in the real rollback path (symlink switch, site re-render, nginx reload, dashboard copy) between two good releases would pass the end-to-end gate, so the rollback guarantee rests on fake-host unit tests.

**Recommended repair.** In the container fixture, build a second good release (for example a commit that changes only a documentation file). Activate it, then run `atlas-deploy rollback` without --to, and assert current, the site's dashboard root, the published copy and `atlas-deploy verify`.

**Verification.**

- release#6 reproduce: **PARTIALLY_CONFIRMED**, severity LOW. The central factual claim holds. The container's 'explicit rollback' re-activates the release that is already current, so its assertions pass even against a rollback that switches nothing (shown with a mutant). The `atlas-deploy rollback` command's own logic is therefore not tested end to end on a real systemd/nginx host: target selection via last_good, the no-`--to` form, and a switch between two verified releases. The docs (DEPLOYMENT.md:727/839, DASHBOARD.md:467) describe the test as covering an explicit rollback without saying it is a same-release re-activation, which overstates the coverage.

The claimed consequence is overstated, though. The finding says 'never exercises a real B->A switch' and that the rollback guarantee 'rests on fake-host unit tests'. In fact the automatic-rollback test in the same container run drives the same `_start` machinery across two different releases on real systemd and nginx: symlink switch, site re-render, nginx -t and reload, restart, verify, plus the dashboard root check.

What is left uncovered end to end is small:
- the roughly five lines of `rollback()` target selection, which is host-independent logic over history.jsonl;
- the healthy-to-healthy case.

Both are covered by fake-host unit tests, and those tests kill the no-op mutant.

Hence PARTIALLY_CONFIRMED. LOW severity on gate K is right: it is a test-coverage and wording gap, not [...]

## M8-38

**Reproduction of the committed M2/M3 reference results is not automated, and the docs do not say so**

- Severity: **LOW** (gate J); fix complexity: medium; alters published research results: no
- Found by: release#7 (LOW: Reproduction of the committed M2/M3 reference results is never automated, and the docs do not say so)
- Files: `.github/workflows/ci.yml`, `docs/DEVELOPMENT.md`

**Description.** CI re-verifies only the smoke runs and the M4 reference (ci.yml:83-102). No test or CI step runs `backtest verify`, `uncertainty verify` or a projection regeneration on docs/results/atlas-m2-*/** or atlas-m3-intervals-v1/** (the grep for verify calls in tests/ finds only smoke runs and copies). Automated coverage of the published M2/M3 numbers is limited to the 6-origin spot check (test_m2_results.py:153), consistency tests between the docs and metrics.json, and the tolerance check of the M2 projection (test_m3_config_runner.py:505-533). DEVELOPMENT.md:315-323 lists what CI runs but never says that the reference directories are not re-derived. Byte-identical verification across CPU families is impossible by design (DEVELOPMENT.md:230-241).

**Evidence.** I ran them by hand at 194fa4a on AVX-512 and all pass: M2 full 19m31s, M3 full 13m45s, M3 selection 5m52s, M2 delay1 3m53s; the M2/M3 projections and the legacy holdout regenerate identically. The M2 development directory fails with current code, as disclosed in BACKTESTING.md:817-822. This shows the gap carries no defect today; it exists only as a process gap.

**Reproduction.** cd . && .venv/bin/atlas backtest verify docs/results/atlas-m2-rolling-origin-v1/full; .venv/bin/atlas uncertainty verify docs/results/atlas-m3-intervals-v1/full (outputs in <audit-scratch>/release/m2fullverify.txt and m3fullverify.txt)

**Consequence.** A future change to the evaluation, interval or reporting code could silently stop reproducing the published M2/M3 headline numbers (Holm-adjusted tests, coverage, FAILED_CALIBRATION status) without failing CI. The v1.0 reproducibility claim for those results rests on manual runs that are not recorded anywhere.

**Recommended repair.** Add a workflow_dispatch or scheduled job that regenerates the M2 full and M3 full runs and compares metrics.json and the result CSVs within CROSS_ENVIRONMENT_RTOL (or byte for byte on a pinned AVX-512 runner). Record the manual verification (date, commit, CPU class) in the RC manifest, and state the gap in DEVELOPMENT.md.

**Verification.**

- release#7 reproduce: **CONFIRMED**, severity LOW. Every factual claim in the finding reproduces.
- CI and the release build re-verify only the smoke runs and the M4 reference.
- No test runs verify on the committed M2/M3 references, or regenerates their projections from them.
- Automated numeric coverage of the published M2 numbers is limited to the 6-origin spot check and a tolerance check of the projection; the published M3 numbers have none.
- DEVELOPMENT.md lists the CI scope correctly but never states the gap.

The finder's evidence that nothing is wrong today also holds independently. On an AVX-512 host the M2 full and M3 full verifies are byte-identical, and both projections regenerate identically.

Two small inaccuracies, neither of which changes the verdict:
- The claimed consequence partly overstates the reach to "reporting code". Report rendering is covered: tests/test_m2_results.py:128 and test_m3_results.py:108 re-render report.md from the committed metrics.json. Evaluation, interval and status computation is not covered.
- Some verification is recorded in the docs at earlier commits, so "not recorded anywhere" is too strong. Nothing ties a verification to the release commit.

Severity LOW is right for a private-first research demo. This is a process gap, and the published Holm results, coverage figures and FAILED_CALIBRATION status reproduce exactly today. A scheduled or dispatch job, or a recorded manual run in [...]

## M8-39

**spot_check_intervals.csv is a lossy (1-2 ULP) copy with no documented provenance or generator**

- Severity: **LOW** (gate L); fix complexity: small; alters published research results: yes
- Found by: release#8 (LOW: docs/results/atlas-m3-intervals-v1/full/spot_check_intervals.csv is a lossy (1-2 ULP) copy of the reference intervals, with no documented provenance, fingerprint or generator)
- Files: `docs/results/atlas-m3-intervals-v1/full/spot_check_intervals.csv`, `tests/test_m3_results.py`

**Description.** The file sits beside the M3 reference manifest but is not among its `outputs`. No code in src/ generates it, no document describes it, and its only test (test_m3_results.py:150-170) checks eligibility and quantile ordering, never the values. Its values are exactly what you get by reading the reference intervals.csv with pandas' default (non-round-trip) float parser, which is lossy at 17 significant digits. So the file misstates the reference run's values in the last bits, including for models that use no BLAS.

**Evidence.** I regenerated the M3 full run into scratch (`atlas uncertainty run --period m3_selection --period m3_evaluation --sealed-selection …/selection/selection.json`). Its intervals.csv, forecasts.csv and exceedance.csv match the committed manifest hashes exactly. All 600 spot rows exist in it. Compared after reading the regenerated file with float_precision='round_trip': 1149 cells differ (max relative 2.35e-16), and only 94/600 spot lines are verbatim lines of intervals.csv. Example: spot prediction 9702.4547124149412 vs reference 9702.4547124149394. Compared after reading with pandas' default parser: 0 cells differ. Differences also occur in non-BLAS pairs (same_weekday_median_5w 112/120 rows, model_free climatology 60/120), so this is not kernel noise.

**Reproduction.** S=<audit-scratch>/release; .venv/bin/python -I $S/spot_intervals_cmp.py $S/m3full docs/results/atlas-m3-intervals-v1/full; then the inline parser comparison (float_precision None vs 'round_trip') in the log of this audit.

**Consequence.** The differences are numerically negligible, but a committed 'reference' file is not a faithful extract of the run it accompanies. An exact comparison against it (as the M2 spot check does for non-BLAS models) would fail spuriously, and its provenance cannot be reconstructed from the repository.

**Recommended repair.** Regenerate it by slicing the raw lines of the reference intervals.csv (or reading with float_precision='round_trip'). Document it next to spot_check.csv in UNCERTAINTY.md, and add a test that recomputes those origins (or record its SHA-256). Any change must also update the M5-merge byte-identity pin in test_m6_manifest.py:181.

**Verification.**

- release#8 reproduce: **CONFIRMED**, severity LOW. Every factual claim in the finding reproduced on my own regeneration of the reference run, which is byte-identical to the committed manifest.

- The committed spot_check_intervals.csv is exactly csv_bytes(pd.read_csv(reference slice)) using pandas' default, non-round-trip parser. I reproduced it byte for byte. It therefore misstates 1149 reference values by 1-2 ULP, and only 94/600 of its lines are verbatim reference lines.
- No code, documentation, manifest entry or hash produces or pins it. Its only test ignores the values: it still passes when every value is shifted by 5000.

One small nuance. For non-BLAS models the differences are in the quantiles and cal_n_effective, not in the point predictions (0 differ). The "exact comparison would fail spuriously" consequence therefore applies to interval values, not to baseline predictions. This does not change the verdict.

The values are within 2.4e-16 relative error. Nothing consumes the file: not the API, the dashboard or the M4 inputs. No published number or claim is wrong. The defect is about provenance and faithfulness of a supporting artifact, so LOW on gate L (secondarily J) is the right calibration.

No repository document discloses it.
