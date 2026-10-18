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
| R13 | LOW | The documentation and two code comments said the api profile was unchanged and that only three things differed between the profiles, and that "the static code" was not in the api build. In fact the api profile shows two added caveats and an M9 entry in the Methods list; the profiles also differ in the deployment, the Provenance wording and the Simulator; and a few static-only constants and messages are in the api build (unreachable). | science, frontend | STATIC_DEMO.md section 1, DASHBOARD.md, `env.d.ts` and `profile.ts` say exactly what changed and what differs | documentation |
| R14 | LOW | The coverage table credited the component tests with checks only the browser tests made (Uncertainty, Decisions, the archived-projection notices, the Forecasting tests panel). | science | `src/m9-static.test.tsx` now checks every page's limitations | `src/m9-static.test.tsx` |
| R15 | LOW | Leftover words in the static profile: the Sources panel said the sources were "served by ATLAS 1.0.0"; the navigation called the Simulator "Bounded what-if runs of the M4 engine"; the Methods note named a deployment history that does not exist before publication. | science | "recorded from ATLAS 1.0.0"; "Recorded M4 reference scenarios"; the note names the `github-pages` environment's deployment record, once the site is published | `src/m9-static.test.tsx` |
| R16 | LOW | Documentation figures and details: 1.09 MB is the export with its manifest (the 24 answers are 1.07 MB); a page's data size depends on the compressor; a result change also requires the dashboard fixtures, not `web/dist`; the static simulator's panels are not all of the Decisions page's; the footer is the substance of the data card's attribution, not its words. | science | Each corrected in STATIC_DEMO.md (the sizes state Python's gzip at level 9; the reviewer's 34.9 KB for Forecasting, measured with another tool, against 35.9 KB, is that difference) | documentation |
| R17 | LOW (before M9) | On the Overview, the recommended model was named without the M8 erratum next to it (it was on the Forecasting page only). | science | A pointer next to the model in both profiles: "its statistical support is weak, as the M8 erratum on the Forecasting page explains" | `src/m9-static.test.tsx` |
| R18 | LOW | Two negative assertions could never fail (a tag that renders on another page; a file the reader refuses before fetching). | frontend | The tamper test now runs on the Overview and on Forecasting and requires every verdict of the tampered file to be absent; the missing-file test requires the H1 verdict to be absent; the vacuous check is replaced by one on every request | `e2e/pages.spec.ts`, `src/m9-static.test.tsx` |
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

Every finding above is corrected. The suites were run again on the corrected head; the
results are in STATIC_DEMO.md section 14 and in the pull request. A second, independent
round then checked the corrections (section 5).

## 5. Second round

Recorded after it runs.
