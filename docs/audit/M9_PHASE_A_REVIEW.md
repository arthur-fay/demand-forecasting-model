# M9 phase A: independent review

The adversarial review of M9 phase A, the static read-only demonstration for GitHub Pages
([../STATIC_DEMO.md](../STATIC_DEMO.md)). It records what the reviewers tried, what they found,
how each finding was corrected and what held up.

## 1. How the review was done

Three reviewers worked independently on the pull request's head (`de8db3e`, four commits on
`7d3639c`, the M8 merge). Each was told to try to disprove that M9 phase A is correct and to
report only what it had verified. They worked read-only: experiments on copies, their own
ports, no change to the repository.

| Reviewer | Scope |
|---|---|
| Science and data | accuracy of the static data against the API; nothing fabricated, filtered or missing; the scientific disclosures next to their results; provenance; every figure and claim of the documentation; the release reasoning |
| Security and publication | whether phase A can publish; the workflow's triggers, permissions, pins and gates; the gate script; credentials and local detail in the export and the build; the policy in the page; the static reader; the stand-in server; the documentation's security sections |
| Frontend and release | GitHub Pages compatibility (subpath, deep links, refresh, 404, canonical addresses, a host without extensionless pages); browser navigation; the api profile's compatibility; accessibility of the new interface; reproducibility; the quality of the new tests; the owner's steps |

No reviewer could make phase A publish, and none found a CRITICAL or HIGH defect. The findings
below are consolidated: one entry per defect, with the reviewers that found it.

## 2. Findings and corrections

| # | Severity | Finding | Found by | Correction | Verified by |
|---|---|---|---|---|---|
| R01 | MEDIUM | An address with `//` after the base (`/demand-forecasting-model//forecasting`) made `canonicalise()` throw at load: a blank page instead of "Page not found". Canonicalisation also stripped a trailing slash or `.html` only once. | frontend, security | `appPath` collapses repeated slashes and strips trailing slashes and `.html` until nothing changes; `canonicalise()` never throws (an address it cannot use stays as it is) | `src/router.test.ts`: `//forecasting`, `forecasting//`, `forecasting.html.html`, `//evil.example/x`, query and fragment kept |
| R02 | MEDIUM | The documentation treated the shared `arthur-fay.github.io` origin as a confidentiality question only. It is an integrity one: a same-origin page can open the dashboard and rewrite what it shows after its checks (demonstrated on the stand-in: "FAILED_CALIBRATION" changed), and a user site at the root could register a service worker for the whole origin. | security | STATIC_DEMO.md section 6 states the risk and its demonstration; a new gate condition, `shared_origin_reviewed`, makes the owner review the account's other Pages sites (or give the site an origin of its own) before publication; the static reader refuses to read while a service worker controls the page (this site never registers one), which catches an accidental worker, not a deliberate one, as documented | `tests/test_m9_pages_workflow.py` (the condition required); `src/api/static.test.ts` (no read under a service worker) |
| R03 | MEDIUM | The scans of the build and of the export missed demonstrable cases (17 of 18 samples in `verify-pages.mjs`): files of other types were not scanned, and e-mail and IP addresses, other hosts, `file:` URLs, more path roots, both Windows path styles, credentials in URLs, bearer and authorization headers, JWTs and raw token prefixes were not detected. | security | One scan module for the build, `web/scripts/unsafe.mjs`: an extension allow-list, every text file scanned, the patterns above, and a URL host allow-list (`www.w3.org`, `react.dev`, `github.com`, `scripts.sil.org`). The export's `_UNSAFE` gains the same kinds of patterns | `build/unsafe.test.ts` (30 samples found, the legitimate texts and the committed build and export clean); `tests/test_m9_static_export.py` (new samples); a planted copy refused by `verify-pages` |
| R04 | MEDIUM | The owner's steps could not be followed: recording the approval (step 1) would turn CI red, because a test required every condition to be false; `pages_settings_configured` was to be recorded before the settings were made; the unpublishing step did not say what to do with the variable. | frontend | The test now requires the record and the gate to agree: phase A's record is refused, a record that approves everything must be complete and passes. Section 12 is reordered (decide, enable Pages, protect the environment, then record, then the switch) and says exactly how to unpublish | `tests/test_m9_pages_workflow.py::test_the_committed_record_publishes_only_what_it_approves` |
| R05 | LOW | `verify-pages.mjs` and the build plugin used the `in` operator, so files named `constructor`, `__proto__` or `hasOwnProperty` counted as listed. | security | `Object.hasOwn`, and the files on disk must equal the manifest's | `build/static-site.test.ts` (a file named `constructor` refused); a planted copy refused by `verify-pages` |
| R06 | LOW | The workflow's concurrency group was the bare branch name, so a fork's pull request from a branch called `main` could cancel a run on `main`, a publication included. | security | A pull request's runs are grouped by its number; pushes and dispatches by ref, never cancelled | `tests/test_m9_pages_workflow.py` |
| R07 | LOW | The build was uploaded for publication after the browsers' installation and the tests had run on the runner, and the publication job checked only that the site served the committed manifest. | security | The artifact is uploaded straight after its verification; after a publication, `.github/scripts/pages_check_site.py` fetches every file of the committed manifest from the site and requires its size and SHA-256 | `tests/test_m9_pages_workflow.py` (step order; the check against a local site: all files, every difference named, https only) |
| R08 | LOW | The gate accepted a record with each key given twice (`false`, then `true`), an impossible date (`9999-99-99`) and unknown fields. | security | Repeated keys refused when the record is read; dates parsed; unknown fields refused | `tests/test_m9_pages_workflow.py` |
| R09 | LOW | The stand-in server (test tooling) followed links out of the build directory. | security | Every path is resolved through its links and must stay inside the build | by hand: a linked file and a linked directory outside the build answer 404 |
| R10 | LOW | Section 10 presented the `github-pages` environment's rules as optional, although they are what stops a collaborator from dispatching a changed workflow from another branch. | security | Stated as required, and part of `pages_settings_configured` | documentation |
| R11 | LOW | Two fields of the export's `scientific_status` (`point_forecast_validated`, `validated_recommendation`) were written in, while the documentation said every field was read from the answers. | science | Read from every answer (`check_answer` refuses an answer claiming either first) | `tests/test_m9_static_export.py` |
| R12 | LOW | STATIC_DEMO.md section 15 linked this record before it existed. | science | This document | — |
| R13 | LOW | The documentation and two code comments said the api profile was unchanged and that only three things differed between the profiles, and that "the static code" was not in the api build. In fact the api profile shows an added caveat (the footer's) and an M9 entry in the Methods list (R17 then added a second caveat); the profiles also differ in the deployment, the Provenance wording and the Simulator; and a few static-only constants and messages are in the api build (unreachable). | science, frontend | STATIC_DEMO.md section 1, DASHBOARD.md, `env.d.ts` and `profile.ts` say exactly what changed and what differs | documentation |
| R14 | LOW | The coverage table credited the component tests with checks only the browser tests made (Uncertainty, Decisions, the archived-projection notices, the Forecasting tests panel). | science | `src/m9-static.test.tsx` now checks every page's limitations | `src/m9-static.test.tsx` |
| R15 | LOW | Leftover words in the static profile: the Sources panel said the sources were "served by ATLAS 1.0.0"; the navigation called the Simulator "Bounded what-if runs of the M4 engine"; the Methods note named a deployment history that does not exist before publication. | science | "recorded from ATLAS 1.0.0"; "Recorded M4 reference scenarios"; the note names the `github-pages` environment's deployment record, once the site is published | `src/m9-static.test.tsx` |
| R16 | LOW | Documentation figures and details: 1.09 MB is the export with its manifest (the 24 answers are 1.07 MB); a page's data size depends on the compressor; a result change also requires the dashboard fixtures, not `web/dist`; the static simulator's panels are not all of the Decisions page's; the footer is the substance of the data card's attribution, not its words. | science | Each corrected in STATIC_DEMO.md (the sizes state Python's gzip at level 9; the reviewer's 34.9 KB for Forecasting, measured with another tool, against 35.9 KB, is that difference) | documentation |
| R17 | LOW (before M9) | On the Overview, the recommended model was named without the M8 erratum next to it (it was on the Forecasting page only). | science | A pointer next to the model in both profiles: "its statistical support is weak, as the M8 erratum on the Forecasting page explains" | `src/m9-static.test.tsx` |
| R18 | LOW | Two negative assertions could never fail (a tag that renders on another page; a file the reader refuses before fetching). | frontend | The tamper test now runs on the Overview and on Forecasting and requires every verdict of the tampered file to be absent; the missing-file test requires the tests panel's verdict, which comes from that file, to be absent (the H1 notice of the forecast's own file stays, as S17 now checks); the vacuous check is replaced by one on every request | `e2e/pages.spec.ts`, `src/m9-static.test.tsx` |
| R19 | LOW | `verify-pages` compared the build's manifest with the working tree's copy, which the build itself had just written, so the check proved nothing locally; a build under another base rewrote the committed file. | frontend | `verify-pages` compares with git's committed copy (`git show HEAD:web/atlas-static-site.json`); only a build under the default base writes the record | by hand: a rebuilt, uncommitted manifest is refused until committed |
| R20 | LOW | The static build failed from a checkout path with a space (percent-encoded paths). | frontend | `fileURLToPath` in `vite.config.ts`, `verify-pages.mjs`, `size-report.mjs` and `verify-dist.mjs` | by the reviewer's reproduction, rerun |

## 3. What held up

The reviewers verified, among other things:

- **Data.** All 24 exported answers are byte-identical to what a normally configured API
  returns (lifespan started, documentation and dashboard on). The 12 answers the dashboard's
  fixtures share are byte-identical to them. The requests the static pages can make are
  exactly the 24 exported. No page makes a request outside `data/`, a POST or a request to
  `/api/`.
- **No silent fallback.** An unknown request, a missing file, a changed file (one flipped Holm
  flag) and a browser without Web Crypto are each refused and shown as an error, never
  replaced.
- **Disclosures.** On the built static site: H1 "Not significant after Holm" (p 0.054); the
  full M2 SELECTION EVIDENCE notice next to the selection; FAILED_CALIBRATION; HYPOTHETICAL
  and the six DIAGNOSTIC scenarios; the archived-projection notice and callout; "Research
  software"; the recorded scenarios labelled as recorded, and the nine certificates hold.
- **Release reasoning.** `7d3639c` and `ccb93ea` have the same tree; `rc_manifest.py --check`
  passes on `7d3639c` (fingerprint `35b5b79f…`, 441 files) and reports the M9 changes on M9.
  M9 does not touch the rc.1 manifest, the release-candidate documents, the contract, the
  results, the snapshot or the legacy data.
- **Publication controls.** Only `pull_request`, `push` to `main` and `workflow_dispatch`
  start the workflow; the gate and the publication run only on a dispatch with `publish`;
  inputs reach scripts only through the environment (every injection attempt through the
  confirmation fails closed); minimal permissions; every action pinned by commit; no secret;
  no Pages setting changed; the committed record fails with every condition unmet.
- **The browser.** No console error, CSP or Trusted Types violation on any page or canonical
  form; the policy before any script; no inline script or style; no cookie or storage; the
  served descriptor can never claim the static profile.
- **Compatibility and reproducibility.** The api build is byte for byte the committed one;
  the M7 browser suite maps to exactly its spec files (anchoring `testMatch` was needed);
  the static build is identical across time zones, locales and checkout paths; a build under
  `/` works; a host without extensionless pages still shows every page.
- **Accessibility.** The recorded-scenario panel's headings, label and focus; axe finds
  nothing at 320 px with every table open.

## 4. After the corrections

Every finding above was corrected on `5dc7837`, and the suites were run again on it. A second,
independent round then checked the corrections and looked for what the first had missed
(section 5): R03, R04, R09, R13 and R14 held only in part, and are completed by S02 to S07,
S17 and S18. R06's claim that pushes are "never cancelled" was too strong (S09).

## 5. Second round

**How.** Six reviewers and a completeness critic worked independently on `5dc7837`. Each
reviewer was given the round-1 corrections of its area to check (does each hold, with
evidence?) and asked for any defect the first round had missed; the critic then asked what
no reviewer had covered. They worked read-only, on copies, with their own ports.

| Reviewer | Scope |
|---|---|
| Router | canonical addresses and the stand-in server, in Chromium (59 malformed addresses) |
| Integrity | the scans of the build and the export, the verifiers, the stand-in's file access, the static reader |
| Publication | the gate, the workflow's order, groups and checks, the owner's steps |
| Science and documentation | the disclosures next to their results, the tests that claim them, every figure and pointer |
| Frontend and build | the api profile's compatibility, the verifiers, the navigation, the custom-domain route |
| Python, workflow and documentation | the Python scripts and their tests, the export's patterns, the documentation of both |
| Critic | the release reasoning, response headers, the browser coverage, the owner's timing, CI's record |

**Round-1 corrections that held**, each re-tested: R01 (59 malformed addresses, none blank
or with `//` left), R02 (a real worker of another page of the origin: every panel refused),
R05, R06 for forks, R07, R08, R10, R11 (the code), R12, R15, R16 (sizes re-measured), R17
(present in both builds), R18 (mutations of the checked code make each assertion fail), R19
and R20 (a checkout path with a space). No reviewer could make phase A publish.

**Findings**, consolidated (one entry per defect). Two are MEDIUM; none is CRITICAL or HIGH.

| # | Severity | Finding | Found by | Correction | Verified by |
|---|---|---|---|---|---|
| S01 | MEDIUM | Canonicalisation could move the address out of the site: stripping `.html` from `...html` (or `%2e%2e.html`, `.%2e.html`) left a dot segment, which the URL resolved to the origin's root, where the Overview rendered; `forecasting/...html` showed the Overview instead of "Page not found". A test commented "a path outside the base is no page" asserted `/forecasting`, a page. Already in `de8db3e` | router | `appPath` returns no app path (`''`) for a path outside the base, or when what is left holds a dot segment (`%2e` read as a dot); `canonicalise` rewrites only when the new address reads back as itself | `src/router.test.ts` (each form; they fail on the old code); `e2e/pages.spec.ts` (the address stays the one requested, under the base, "Page not found") |
| S02 | MEDIUM | R04 was incomplete: a test still required the gate to refuse the committed record, so the owner's complete approval (section 12, step 4) turned CI red, and no all-true record could pass | publication, Python | The command test follows the record (refused while a decision is open, accepted once complete); a phase-A record written for the test is refused with every switch set, naming each condition. Phase A's refusal rests on the committed record, as STATIC_DEMO.md section 10 now says | `tests/test_m9_pages_workflow.py::test_the_command_follows_the_record`; the reviewer's probe (a complete approval) now passes |
| S03 | LOW | The build's host allow-list saw only `http(s)`, `ws(s)` and `ftp` with `://`: protocol-relative links (`//evil.example` in `src`, CSS `url()`, a string), `https:host`, `https:/host`, `https:///host`, a tab after the slashes and other schemes passed; a planted `//evil.example` reached the build and verify-pages passed | integrity | Any scheme with two slashes; the special schemes with fewer; a protocol-relative host where a link, source, `url()`, `@import` or string begins | `build/unsafe.test.ts` (each sample); no false finding in either committed build |
| S04 | LOW | Further scan gaps: compressed IPv6 (`2001:db8::abcd`) in both scans; Windows paths only under five directories; `LOCALHOST`; unquoted credential assignments; credential URLs with an empty user; the site manifest and file names never scanned; a `.woff2` never checked | integrity, Python | Compressed IPv6 (confirmed by `node:net` and `ipaddress`); any drive path; case-insensitive local hosts; unquoted values that look like secrets; an optional user; names and the manifest scanned; a font must carry `wOF2`. The documentation says the scans are patterns, a check on mistakes | `build/unsafe.test.ts`, `build/verifiers.test.ts`, `tests/test_m9_static_export.py` (samples; times, ratios and CSS pseudo-elements not flagged) |
| S05 | LOW | `verify-dist.mjs` still used `in`: a file named `constructor` in `web/dist` passed | integrity | `Object.hasOwn`; a link reported before any read; the directory may be given | `build/verifiers.test.ts` (four names, a link; they fail on the old script) |
| S06 | LOW | `verify-pages.mjs` read a listed path before checking it was a file: a link to a directory crashed it (EISDIR) without its report | frontend | Every file is checked (`lstat`) before it is read, once, and reported if it is no regular file or unreadable | `build/verifiers.test.ts` |
| S07 | LOW | The stand-in served `404.html` and checked its start-up files without resolving links (a `404.html` linked out of the build was served), and decoded `%2F`, `%2E` before its checks, contrary to its header | integrity, router | Both start-up files are resolved through `file()` (inside the build, or the server stops); an encoded dot, slash or backslash answers 404.html | by hand: a `404.html` linked outside stops the server; `%2F`, `%2E`, `%5C` answer 404 |
| S08 | LOW | Reading `navigator.serviceWorker` throws in a sandboxed frame (reproduced in Chromium) and where storage is blocked; every read then failed with a raw exception | integrity, critic | The check is in a `try`: where the browser refuses to say, no worker can control the page, and the read proceeds | `src/api/static.test.ts` (no service worker; a throwing getter) |
| S09 | LOW | Pushes and dispatches on `main` shared a group: GitHub keeps one pending run per group, so a push could replace a publication waiting for its review, contrary to the comment and R06 | publication, Python | Each dispatch has a group of its own; the comment says what GitHub does with pending pushes | `tests/test_m9_pages_workflow.py` |
| S10 | LOW | What was published was verified only on the build job's runner, which had run the dependencies' install scripts; a file added after verification would have been published unnoticed (the site check reads only the listed files) | publication | On the publication job's own runner, the downloaded artifact must be exactly the committed build (`pages_check_artifact.py`) before deploy-pages runs; `actions/download-artifact` v8.0.1, pinned, Node.js 24 | `tests/test_m9_pages_workflow.py` (order of steps; the check: altered, missing, extra file and directory, a link, a path outside, the manifest) |
| S11 | LOW | The site check crashed on a cut-off answer (`IncompleteRead`) instead of retrying, and could run for hours against a slow site, past the job's timeout | publication, Python | `http.client.HTTPException` and `ValueError` count as a difference to fetch again; a per-request timeout of 10 s and a deadline of 8 minutes; the job's timeout raised to 25 minutes | `tests/test_m9_pages_workflow.py` (a truncated answer, then a good one; the deadline) |
| S12 | LOW | The gate accepted any calendar date (`0001-01-01`, `9999-12-31`) and notes of any type (an object with `"publication_approved": false`) | publication | `decided_on` between 2026-10-18 and the day of the run (UTC); `notes` text or absent | `tests/test_m9_pages_workflow.py` |
| S13 | LOW | The owner's steps: Pages was enabled (step 2) before the environment was protected (step 3); no instruction when the site check fails after publication, nor to update `release_version` with a version bump; the artifact is kept one day while the review can wait longer | publication, critic | The environment is created and protected first; step 6 asks for approval within 24 hours (or a new dispatch); step 7 says to unpublish when the check fails; step 10 updates the record with a version bump | documentation |
| S14 | LOW | If the user site has a custom domain, GitHub serves every project site without its own domain under it: the origin and the address change | integrity | Section 6 and the `shared_origin_reviewed` review include it | documentation |
| S15 | LOW | Since R19, the documented route to a custom domain (`ATLAS_PAGES_BASE=/`, rebuild, commit) could not work, and verify-pages told the owner to "rebuild and commit" | science, frontend | Section 6 gives the real procedure (change `DEFAULT_PAGES_BASE` and the test defaults, rebuild, commit); verify-pages names a build for another base | `build/verifiers.test.ts` |
| S16 | LOW | The Overview's erratum pointer (R17) kept only the erratum's softest clause and named "the M8 erratum on the Forecasting page", which no page shows under that name; the integrity table did not list it | science, frontend | It states the conclusion (a correctly sized test would most likely have kept the same-weekday median baseline) and names the M2 SELECTION EVIDENCE notice and its panel; DASHBOARD.md section 6 lists it | `src/m9-static.test.tsx`, `src/views/m8-audit.test.tsx` (api profile, and the link leads to the notice), `e2e/pages.spec.ts` |
| S17 | LOW | Tests that could not fail: the Decisions assertions matched fixed text (they passed with every M4 file missing); the R11 test could not tell reading the flags from writing them in; a test comment misdescribed what a missing file removes | science, Python | Six DIAGNOSTIC and three "Meets every constraint" tags of the recorded scenarios; `_scientific_status` with one answer claiming each flag; the comment corrected and the remaining notice asserted | `src/m9-static.test.tsx`, `tests/test_m9_static_export.py` (fails with the flags hard-coded) |
| S18 | LOW | R13's corrected text still said the static wording was not in the api build; R13's own finding was anachronistic; DASHBOARD.md credited the Overview's pointer (a lazy chunk) for the initial load's growth | science | `env.d.ts`, STATIC_DEMO.md and DASHBOARD.md list what static-only wording remains, never shown; R13 corrected above; the size explanation corrected | documentation |
| S19 | LOW | No browser test checked the Overview's pointer or the navigation's line, and the api profile's pointer had no test, though section 7 said both suites check each conclusion | science | Both asserted on the built site; the api profile's pointer in a view test | `e2e/pages.spec.ts`, `src/views/m8-audit.test.tsx` |
| S20 | LOW | In the static navigation, "M4 reference scenarios" (Decisions) and "Recorded M4 reference scenarios" (Simulator) read as if the pages showed different things | frontend | The Simulator's line is "Simulations off here; explore recorded results" | `src/m9-static.test.tsx`, `e2e/pages.spec.ts` |
| S21 | LOW | Figures and pointers: section 4's form "lists every assumption" (it lists the reference assumption of each adjustable input); gzip figures without their compressor; a stale step number; section 3's list of checks older than the patterns; the gate's docstring pointed at section 9; circular pointers to test counts that existed nowhere, and CI's results not in the pull request | science, Python, critic | Each corrected; STATIC_DEMO.md section 14 gives the counts, and the pull request CI's runs | documentation |
| S22 | LOW | Section 11 offered publishing as 1.0.0 with a candidate `rc.2`, which no tool can make: `rc_manifest.py` names its candidate `rc.1`, and its `--write` would overwrite the M8 manifest | critic | Section 11 recommends 1.1.0 and says what an `rc.2` would first need | documentation |
| S23 | LOW | The header table said the loss of COOP had no consequence; a site that opened the dashboard can send its tab to a look-alike page (demonstrated locally; nginx's COOP cuts that handle) | critic | Section 6's table, section 13 and KNOWN_LIMITATIONS.md state it | documentation |
| S24 | LOW | Only the HTML pages carry `noindex`; nginx sends `X-Robots-Tag` on every response, and a project site cannot publish `robots.txt` | critic | Section 6 (a table row and the text), section 13, KNOWN_LIMITATIONS.md; accepted under `public_static_profile_approved` | documentation |
| S25 | LOW | DEVELOPMENT.md "Versioning" had no step for the static export and build, so following it broke the static build | critic | Steps 4 and 5 export again, rebuild both profiles and commit the export and the site manifest | documentation |
| S26 | LOW | The documentation credited Firefox and WebKit with the whole browser suite; they ran `pages.spec.ts` only, and keyboard use in Gecko and WebKit was untested | critic | The keyboard test is its own spec, run in all three engines (in WebKit, with Option+Tab where Tab skips links); the tables say what runs where; axe, reflow and the phone layout stay Chromium only | `playwright.config.ts`, `e2e/pages-keyboard.spec.ts`; CI |

**After the corrections.** The suites were run again on the corrected head; their results are
in STATIC_DEMO.md section 14, and CI's runs in the pull request.
