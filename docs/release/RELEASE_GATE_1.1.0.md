# ATLAS 1.1.0-rc.1: release gate

The acceptance assessment of the 1.1.0 release candidate, with the evidence for each gate and
every owner approval still required. It is prepared for the owner's review: **it approves
nothing**. The candidate, its manifest and how to verify them are in
[RELEASE_CANDIDATE_1.1.0.md](RELEASE_CANDIDATE_1.1.0.md). The M8 gate of 1.0.0-rc.1,
[RELEASE_GATE.md](RELEASE_GATE.md), is kept as it was issued; where it predates later facts
(the MIT License, M9), this document says so instead of rewriting it.

## 1. Recommendation

**READY FOR OWNER REVIEW** as release candidate 1.1.0-rc.1, on two conditions: CI is green on
the head of pull request #13, and the manifest check passes there (section 4). That is not an
approval. The candidate's scope is the M8 one, a **private-first research demonstration**,
with the M9 static profile prepared but not published.

- **NO-GO for tagging `v1.1.0-rc.1`** until the owner has given the approvals of section 6. One
  of them, the NYC 311 data terms, is still open (gate M), and the repository already
  redistributes the derived tables publicly.
- **NO-GO for publication** of the static demonstration on GitHub Pages: the publication record
  states no decision, and this candidate changes none of it (gate P).
- **NO-GO for any operational use** of the forecasts, intervals or plans, as in M8.
- **NO-GO for a real deployment** (M6 phase B): not authorised.

## 2. Scientific integrity

1.1.0 adds no scientific evidence, so it adds no claim. Every statement below is unchanged
since 1.0.0-rc.1 (section 3 of the release notes lists what is byte-identical). The first six
are the committed results' own status, carried by the dashboard, the API, the static export and
the manifest's `scientific_status` (whose tool refuses results that contradict them); the last is
carried where its row says:

| Statement | Status in 1.1.0 | Where it is recorded |
|---|---|---|
| M2's forecast advantage is not robustly established | **unchanged**: H1 not significant after Holm (adjusted p 0.054) | `docs/results/atlas-m2-rolling-origin-v1/full/recommendation.json`; [BACKTESTING.md](../BACKTESTING.md) |
| The M8 statistical erratum remains valid | **unchanged**: size-corrected selection p 0.069, above the gate; a correctly sized test would most likely have kept B\* | [BACKTESTING.md](../BACKTESTING.md) section 12; `docs/audit/scripts/` |
| M3 has FAILED_CALIBRATION | **unchanged** | `docs/results/atlas-m3-intervals-v1/full/calibration_status.json` |
| Prediction intervals are not validated for operational use | **unchanged**: no interval or probability is decision-grade | the same; every interval response (`uncertainty_decision_grade: false`) |
| Every M4 staffing, cost, productivity and budget assumption is HYPOTHETICAL | **unchanged**: every provenance declaration of the M4 configuration is `HYPOTHETICAL`; `validated_recommendation: false` | `configs/optimization/atlas-m4-optimization-v1.json`; the M4 manifest |
| Archived forecasts are not current predictions | **unchanged**: projection issued from data ending 2026-06-30, never scored | `docs/results/atlas-m2-rolling-origin-v1/projection/manifest.json` |
| Independent research software, not an official NYC operational tool | **unchanged** | README, data card, the dashboard's footer, the manifest's declared status |

## 3. Acceptance matrix

The M8 gates are re-assessed for what changed. Where the evidence is M8's, it carries over
because the content it covers is byte-identical in this candidate (release notes, section 3);
where it is new, it is from this candidate (section 4). Gates O and P are new, for M9.

| Gate | Status | Evidence | Remaining limits |
|---|---|---|---|
| A. Data integrity | **PASS** | `snapshots/legacy-v0/` byte-identical to 1.0.0-rc.1. `atlas check` and `atlas quality` pass on the candidate. | Extraction time and City data revision never recorded (disclosed). |
| B. Forecasting validity | **PASS** (for a research demonstration), as in M8 | The M2 results and code are unchanged; the full M2 reference run reproduces byte for byte (section 4). | The lead over B\* is not significant; the M8 erratum stands (section 2). Not suitable for operational decisions. |
| C. Uncertainty calibration | **FAIL** (calibration), as in M8 | `FAILED_CALIBRATION`, unchanged; the M3 runs reproduce byte for byte (section 4). | Accepted only as a disclosed limitation. |
| D. Optimisation correctness | **PASS**, as in M8 | The M4 reference run reproduces byte for byte (section 4). | Every parameter `HYPOTHETICAL`. |
| E. API correctness | **PASS** | The serving code is unchanged since M8 (in `src/atlas/api`, only the new `static_export.py`); the live contract equals the committed one, which differs from 1.0.0's only in `info.version`. M8's Schemathesis run (729 cases) carries over and was not repeated. | One shared API key; no queue (as in M8). |
| F. Security | **PASS** (no known exploitable defect; not a guarantee) | M8's assessment carries over for the unchanged API and deployment code. M9's review covered the static profile ([../audit/M9_PHASE_A_REVIEW.md](../audit/M9_PHASE_A_REVIEW.md)). The static build's scan finds no credential, local detail or foreign link; known vulnerabilities: section 4. | As M8 and STATIC_DEMO.md section 13: no framing protection or headers on a static host, a shared Pages origin. |
| G. Deployment package | **PASS**, as in M8 | `deploy/` byte-identical; the container end-to-end test runs in CI (`deploy-e2e`). | Not executed on the owner's server (gate N). |
| H. Frontend functionality | **PASS** | Vitest and Playwright (Chromium) on both profiles (section 4); Firefox and WebKit in CI. | Real Safari, real devices not tested. |
| I. Accessibility | **PASS** (automated checks) | axe WCAG 2.2 A/AA on every page of both profiles (section 4). | **NOT VERIFIED** with real assistive technology. |
| J. Reproducibility | **PASS** | Every committed research run reproduced byte for byte on the reference CPU class; both dashboard builds and the static export reproduce byte for byte; the manifest checks from committed content (section 4). | Bit-identical regressions need the SkylakeX kernels; the manual workflow `reproduce-research.yml` has to pass on the merge commit (section 6). |
| K. Automated testing | **PASS** | Section 4. | CI on the final head is recorded in pull request #13. |
| L. Documentation and provenance | **PASS** | The 1.1.0 release notes and this gate; the current documents state the 1.1.0 status; the M8 records kept as issued; the data card, README and dashboard attribute the data. | The source extraction's provenance gaps are disclosed, not resolvable. |
| M. Dependency and licensing review | **Code licence: DECIDED** (MIT, original ATLAS code only). **NYC data terms: NOT VERIFIED.** **Dependencies: PASS** | Dependencies: no package changed; audits in section 4. Licence: root `LICENSE`, MIT, Copyright (c) 2026 arthur-fay; it does not cover NYC 311 data, derived City aggregates, or third-party code and fonts. Data terms: section 5. | The dataset-specific (311 provider) conditions are unconfirmed. One dependency cap passed its review date (2026-10-16) and two fall due within a week (section 6). |
| N. VPS deployment readiness | **NOT VERIFIED**, as in M8 | The runbook is unchanged and not executed. | Phase B needs written authorisation. |
| O. Static demonstration (M9) | **PASS** as prepared and tested; **NOT PUBLISHED** | The export reproduces and verifies; the static build reproduces, equals the committed site manifest and passes `verify-pages` and its size budgets; its browser suite passes (section 4). | The real GitHub Pages host has never served it (STATIC_DEMO.md section 13). |
| P. Publication controls | **CLOSED, as required** | `PAGES_PUBLICATION.json` is byte-identical to M9's: every condition false, `release_version` null; the gate refuses it with every switch set (`tests/test_m9_pages_workflow.py`). This preparation enabled no Pages, set no repository variable, changed no environment and dispatched no workflow. | Only the owner can open it (section 6). |

## 4. Measured checks on the candidate

Run on 2026-10-20 in the preparation environment: a 4-core Intel Xeon at 2.80 GHz with AVX-512,
OpenBLAS core **SkylakeX** (the reference CPU class), Python 3.12.3, uv 0.11.32, Node.js 22.22.3
with npm 10.9.8, Playwright 1.56.1 (Chromium 141), nginx 1.24.0, OpenSSL 3.0.13, shellcheck
0.9.0 and systemd-analyze 255. The runs shared the machine with each other, so the wall times
are indicative only. CI's runs of the final head are recorded in pull request #13.

**Research reproduction** ([DEVELOPMENT.md](../DEVELOPMENT.md), "Reproducing the committed
reference results"; the commands of `reproduce-research.yml`). The runs started on `75217b3`;
the later commits of the candidate change no research code (`src/atlas` is identical), so their
outcome is the candidate's. Every result is identical to the committed one:

| Run | Check | Wall time | Result |
|---|---|---|---|
| M2 reference run (`full`) | `backtest verify` | 6.0 min | identical, fingerprint `d53bc911…5a8a` |
| M2 delay sensitivity | `backtest verify` | 3.2 min | identical, fingerprint `79d4f796…f22d` |
| M2 sealed development run, code and lock of `a16672f` | `backtest verify` | 3.8 min (after `uv sync`) | identical, fingerprint `fbb948ec…ea43` |
| M2 projection | `forecast`, `same_outputs.py` | 3 s | 5 of 5 files identical |
| Legacy one-step holdout | `backtest legacy`, `same_outputs.py` | 2 s | 2 of 2 files identical |
| M3 selection run | `uncertainty verify` | 3.2 min | identical, fingerprint `2e673489…7194` |
| M3 reference run (`full`) | `uncertainty verify` | 4.2 min | identical, fingerprint `c4d2adbf…dfaf` |
| M3 projection | `uncertainty project`, `same_outputs.py` | 2.2 min | 3 of 3 files identical |
| M4 reference run | `optimize verify`, `optimize inspect` | 26 s | identical, fingerprint `0dc4eb0f…3c82`; HYPOTHETICAL, FAILED_CALIBRATION carried |

Each `verify` also reports that the commit recorded in a run's provenance (and, where the code
has changed since, its code fingerprint) differs from the current one, as it must for a run
made by earlier code; that is information, not a difference in the results.

**Every other check:**

| Check | Result |
|---|---|
| Version declarations | all twelve equal 1.1.0 (`tests/test_m8_release.py`, and the manifest tool's `versions`) |
| Locks | `uv lock` and `npm version` changed only ATLAS's own entries; `uv sync --locked` and `npm ci` install the same packages as 1.0.0 |
| Static checks: ruff format, ruff check, strict mypy (90 source files), shellcheck of `deploy/scripts`; Prettier, ESLint, tsc | clean |
| pytest, the whole suite with the coverage gate (`ATLAS_REQUIRE_DEPLOY_TOOLS=1`, as in CI), on `75217b3` | 3,193 passed, 25 skipped (22 opt-in container tests, 2 that need an unprivileged user, 1 opt-in study rerun); coverage 96.06 % |
| pytest after the release documents and the tool's schema 2 | FINAL_PYTEST_PLACEHOLDER |
| `atlas check`, `atlas quality`, `atlas backtest legacy`; the M2, M3 and M4 smoke runs and their `verify` (and M4's `inspect`) | pass |
| API contract | `atlas api openapi` equals `docs/api/openapi.json`; `npm run gen:api` leaves `openapi.gen.ts` unchanged |
| A real server (`atlas api serve`) | `/health/live` reports `atlas_version` 1.1.0; `/health/ready` ready; `/api/v1/scenarios`; one simulation answered as `hypothetical_simulation` with `validated_recommendation: false` |
| Dashboard fixtures | re-recorded; each differs from 1.0.0's only in the version; `tests/test_m7_fixtures.py` passes |
| Static export | `atlas static verify`: files and reproduction PASS; 24 answers, each differing from 1.0.0's only in `atlas_version` |
| api build (`web/dist`) | reproduced byte for byte by a second build; `verify:dist` PASS (18 files, version 1.1.0); differs from 1.0.0's only in the version and the content-hashed chunk names; within every size budget |
| Static build (`dist-pages`) | `atlas-static-site.json` identical across two builds and to the committed one; `verify-pages` PASS: 49 files under `/demand-forecasting-model/`, 25 data files, no credential, local detail or foreign link; size budgets as M9's (initial JavaScript 88.0 KB of 110, all data 181.7 KB of 300, gzip) |
| Vitest, the whole suite with coverage | 316 passed (25 files); 95.24 % statements, 84.52 % branches, 94.03 % functions, 96.56 % lines |
| Playwright, api profile and both nginx stacks (Chromium) | 61 passed |
| Playwright, static profile: `pages` and `pages-mobile` (Chromium) | 51 passed, 1 skipped (back and forward on the phone, as in M9), including the new check that every page attributes the data |
| Playwright, Firefox and WebKit | CI only |
| Deployment end to end in a disposable systemd container | CI only (`deploy-e2e`); `deploy/` is unchanged |
| pip-audit 2.10.1 (runtime: 19 exported packages; development: 29) | no known vulnerability |
| `npm audit --omit=dev --audit-level=moderate`; `npm audit --audit-level=high` | 0 vulnerabilities |
| Release-candidate manifest | `rc_manifest.py --check` in a fresh clone of the pushed candidate; its tree fingerprint is published in pull request #13 |

Two environment notes, neither a defect of the candidate: the nginx-stack browser tests reach
`atlas.example.org`, which the browser maps to 127.0.0.1, and in an environment with an HTTPS
proxy that name must be exempt from it (`NO_PROXY`), or the proxy refuses it; and the container
test needs a Docker daemon, which the preparation environment did not have.

## 5. NYC 311 data-use status (gate M)

**Pending. Nothing here confirms permission, and nothing here is legal advice.**

- **What the owner's review of 2026-10-20 found**
  ([NYC_311_REUSE_REVIEW_2026-10-20.md](NYC_311_REUSE_REVIEW_2026-10-20.md); its own status:
  "evidence gathered; data-specific approval not yet recorded", a preliminary assessment): the
  City's general open-data policy (the Administrative Code, the NYC Open Data FAQ, the Technical
  Standards Manual) supports public reuse, including in an application, subject to the
  attribution, source, version and modification requirements and to any applicable terms: the City
  may require a republisher to identify the source, the version and the modifications. The
  portal's Terms of Use incorporate the NYC.gov terms and **any additional terms of the entity
  that provides a dataset**. (Where the review speaks of "the published static demo", it means the
  static build as it would be published: nothing has been published.)
- **What is not established**: whether the provider of dataset `erm2-nwe9` (311) sets additional
  conditions, and what the dataset's current Licensing and Attribution metadata says.
- **This preparation's independent check could not be made.** On 2026-10-20 the preparation
  environment tried to read the dataset's metadata (`data.cityofnewyork.us/api/views/erm2-nwe9.json`
  and the dataset's About page), the NYC.gov terms, the NYC Open Data site, the federal catalog
  entry (`catalog.data.gov`), the Administrative Code (`codelibrary.amlegal.com`) and an archived
  copy (`web.archive.org`). The environment's network policy refused every one of these hosts,
  so none of these pages was read.
- **Possible additional conditions were found, and are unverified.** They come from search-engine
  excerpts and from secondary sources quoting the City's terms, not from the pages themselves,
  which could not be opened, so neither their current content nor whether they are still in force
  is confirmed. Two NYC.gov addresses carry them in the search index:
  `https://www.nyc.gov/html/datamine/html/data/terms.html` ("NYC DataPortal Terms of Use", from
  the City's "Data Mine"; its excerpt names users "providing software applications using data
  supplied on the NYC.gov Data Mine"), and `https://www.nyc.gov/html/data/terms.html`, indexed as
  the terms of use of the NYC OpenData portal. According to the excerpts, and to a 2011
  OpenStreetMap mailing-list thread and a 2013 DataHub catalog entry that quote or summarise them,
  those terms say that users providing **software applications** that use the data must **notify
  the City** and **include specified disclaimers at the site where the application can be accessed
  or downloaded** (that the City cannot vouch for the accuracy or completeness of the data, and
  that the application uses data modified from its original source), and that the City may
  **require a user to stop using** a dataset. The excerpts also show that some City agencies ask
  for their own disclaimer when their data are used in an application (for example NYC DOT).
  Whether these terms are current or superseded, whether they govern the portal as it is today,
  and whether 311 sets such a condition for `erm2-nwe9` are all unknown.
- **If they apply, they apply now, not only at publication.** The public repository already
  offers for download software that uses the data: the ATLAS package and API, the committed
  dashboard build `web/dist` and the static export with its recorded answers. A notice to the
  City and the prescribed disclaimers would then be due before the `v1.1.0-rc.1` tag (or the
  repository made private, as the M8 gate's condition 3 allows; section 6), and the static site
  would need them before publication.
- **What the dashboard shows today** (both profiles, every page's footer): "Data: NYC Open Data,
  311 Service Requests (dataset erm2-nwe9), frozen and hash-verified as the legacy-v0 snapshot",
  "ATLAS is an independent research project, not affiliated with or endorsed by the City of New
  York or NYC Open Data", and that nothing is a validated staffing, budget or service-capacity
  recommendation. The Overview names the source with "city-wide daily totals" and the days covered
  (2023-01-01 to 2026-06-30); every page carries its scientific limitations. It does **not** link
  the dataset's page (the static build's scan refuses a link to any host but four: `www.w3.org`,
  `react.dev`, `github.com` and `scripts.sil.org`, in `web/scripts/unsafe.mjs`), does not say that
  the City's data revision and the extraction time were not recorded, and does not use any
  City-prescribed disclaimer. Those are owner decisions for the publication pull request (section
  6): each would change the dashboard's sources, both builds and, for a link, the scan's allowed
  hosts. A browser test (`web/e2e/pages.spec.ts`) keeps today's attribution on every page.
- **The README and the data card** attribute the data, describe the aggregation, state the
  provenance gaps and keep the data out of the MIT License (`tests/test_m8_docs.py` keeps the
  README's wording).

`nyc_open_data_terms_confirmed` therefore stays **false**. Closing it needs the owner to read the
dataset's About page (its Licensing and Attribution section, metadata and attachments), the
governing NYC.gov terms and both pages above, or to obtain the City's confirmation through its
Open Data help desk, and then to record the conclusion with dated evidence, in a separate
reviewed pull request (the review's section "Remaining verification").

## 6. Owner approvals still required

Nothing below is done by this preparation; each is the owner's alone.

**Before approving the candidate (merging pull request #13):**

- [ ] Read this gate and the release notes, including every gate that is FAIL or NOT VERIFIED.
- [ ] Accept the disclosed limitations ([../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md)) and
  record the acceptance in the approval of the pull request (or later in the tag's annotation).
- [ ] CI is green on the pull request's head commit: `ci.yml` (including `deploy-e2e` and the
  dashboard job's Firefox and WebKit run), `pages.yml` part A, and `dependency-audit.yml`.
- [ ] `python3 docs/release/rc_manifest.py --check` passes on the commit to approve, in a fresh
  clone, and its tree fingerprint equals the one published in the pull request.
- [ ] Decide the dependency caps: `optype<0.19` passed its review date (2026-10-16);
  `truststore<0.10.5` and `starlette<1.8` fall due on 2026-10-26 and 2026-10-27
  ([../DEVELOPMENT.md](../DEVELOPMENT.md), "Dependency maintenance"). Lifting a cap changes the
  lock, and so the content: before approval, the manifest is then regenerated last (the
  candidate stays `1.1.0-rc.1`); after approval, it makes `1.1.0-rc.2`.

**Before tagging `v1.1.0-rc.1`** (the M8 gate's condition 3, for this candidate):

- [ ] **The NYC 311 data terms** (section 5): confirm that the applicable City and provider terms
  permit the public repository's redistribution of the derived tables with the attribution the
  data card gives, and whether a condition on applications that use the data (a notice to the
  City, prescribed disclaimers) applies to the repository; if one does, meet it before the tag.
  Otherwise make the repository private until they are confirmed. The code licence half of the
  M8 condition is met (MIT).
- [ ] Merge the pull request (a merge commit keeps the candidate commit reachable), then tag
  `v1.1.0-rc.1` on the approved commit. Separately, decide the `v1.0.0-rc.1` tag on
  `7d3639c`: the M8 gate's own conditions apply to it, its condition 3 (these data terms)
  included ([RELEASE_GATE.md](RELEASE_GATE.md) section 1).

**Before tagging `v1.1.0`:**

- [ ] Dispatch `reproduce-research.yml` on the merge commit; every job must pass. A failure on a
  runner whose OpenBLAS core is not SkylakeX is inconclusive: re-run it, or run its commands on a
  SkylakeX machine. Record the date, the commit, the CPU, the OpenBLAS core and the outcome.
- [ ] Confirm that `dependency-audit.yml` is enabled, and run it once by hand; it must be green.
- [ ] The conditions above still hold. After a conclusive reproduction failure, the content does
  not become `v1.1.0`.
- [ ] Tag `v1.1.0` on the same commit. Any change to the content makes a new candidate.

**Before publishing the static demonstration** (separately, and only if the owner decides to;
in the order of [../STATIC_DEMO.md](../STATIC_DEMO.md) section 12):

- [ ] The site's attribution (section 5): whether to link the dataset's page (which needs the
  host in `ALLOWED_HOSTS`, `web/scripts/unsafe.mjs`), to state that the City's data revision and
  the extraction time were not recorded, and to use any City-prescribed disclaimer or send any
  notice to the City; if anything changes, both profiles are rebuilt and the site is checked
  against the review's "Remaining verification" item 4.
- [ ] The `github-pages` environment first (required reviewers, `main` only), then Pages with
  "GitHub Actions" as its source (steps 2 and 3).
- [ ] Every condition of [`PAGES_PUBLICATION.json`](PAGES_PUBLICATION.json), set to true only if it
  holds, with `release_version` `1.1.0`, `decided_by`, `decided_on` and `approval_reference`, in
  a separate reviewed pull request (step 4; sections 9 to 11): the code licence recorded, the NYC
  data terms confirmed (section 5), the version decided, the limitations accepted for a public
  site, the static profile approved, the shared origin reviewed, the Pages settings configured
  and publication approved.
- [ ] The repository variable `ATLAS_PAGES_PUBLICATION`, then the manual dispatch on `main` with
  the confirmation phrase, and the `github-pages` deployment approved within 24 hours (steps 5
  and 6).
- [ ] The run's checks and the real site checked (steps 7 and 8).

**Deployment:** authorise or defer M6 phase B (the runbook's section 1 decisions).

## 7. What has not been verified

- The NYC 311 dataset-specific terms (section 5).
- CI on the candidate's final head: it runs on the push and is recorded in pull request #13.
- Firefox and WebKit: CI only (Playwright's builds; not real Safari).
- The manual research-reproduction workflow on GitHub, and the weekly dependency audit as a
  scheduled run: owner actions (section 6). Their commands were run locally (section 4).
- The real GitHub Pages host, a real server, real phones and screen readers (as in M8 and M9).
- Schemathesis was not re-run: the API's serving code is unchanged since M8, whose run carries
  over.

## 8. Outstanding risks

1. **Over-interpretation**, as in M8: the interface is polished, the numbers are not validated.
2. **Data terms**: the provider conditions are unknown (section 5), on a repository that is
   already public.
3. **Statistical evidence**: unchanged (section 2); a future protocol needs a correctly sized,
   dependence-robust test fixed before any run.
4. **Toolchain upkeep**: one dependency cap past its review date and two due within a week
   (section 6).
5. **Environment drift**: bit-identical regressions need the reference CPU class.
6. **Static hosting** (if ever published): the limits of STATIC_DEMO.md section 13.
