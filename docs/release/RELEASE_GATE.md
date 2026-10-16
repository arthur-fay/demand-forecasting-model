# ATLAS 1.0.0-rc.1: release gate

The acceptance decision for the release candidate, with the evidence for each gate. It is the
outcome of the M8 audit: the original, pre-correction assessment (verdict NO-GO for `194fa4a` as
it stood) is preserved in [../audit/M8_BASELINE_ASSESSMENT.md](../audit/M8_BASELINE_ASSESSMENT.md);
the corrections and the measured post-correction checks are in
[../audit/M8_CORRECTIONS.md](../audit/M8_CORRECTIONS.md).

## 1. Recommendation

**CONDITIONAL GO**, for this scope only: approve ATLAS 1.0.0 as a **private-first research
demonstration** (merge the M8 pull request; the owner tags `v1.0.0-rc.1` and later `v1.0.0`),
on these conditions:

1. CI is green on the pull request's head commit, including `deploy-e2e`, the dashboard job's
   Firefox and WebKit run and the dependency audit.
2. The owner reads and accepts the disclosed limitations ([../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md)),
   in particular the failed calibration (gate C), which stays FAILED and is accepted only as a
   disclosed limitation of a research demonstration.
3. Before the repository or a deployment is presented as open source or redistributes the data
   publicly: the owner decides the project licence and confirms the NYC Open Data terms for the
   committed derived tables (gate M). Until then nothing in the repository grants or asserts a
   licence.

**NO-GO** for any operational use of the forecasts, intervals or plans, and for the
`public-demo` profile. A real deployment is **not yet authorised**: it is M6 phase B, prepared in
[VPS_PHASE_B_RUNBOOK.md](VPS_PHASE_B_RUNBOOK.md) and not executed.

## 2. Four different questions, four different answers

| Question | Answer | Why |
|---|---|---|
| Is the **research demonstration** ready as engineering? | **Yes, conditionally** (section 1) | every verified defect is corrected with a test; all checks pass; every research result reproduces byte for byte |
| Are the results **scientifically suitable for real operational decisions**? | **No** | the forecaster's lead over a simple baseline is not established and its selection evidence is weak; the intervals failed calibration; every planning parameter is hypothetical; the data end 2026-06-30 |
| Is the **deployment package** ready? | **Yes, for a private-first phase B**, subject to the server's preflight | tested end to end on a disposable Ubuntu 24.04 systemd server; the rollback and recovery defects found by M8 are corrected |
| Has a **real production deployment** been verified? | **No** | nothing has been deployed; the owner's server has not been inspected |

## 3. Acceptance matrix

| Gate | Status | Evidence (post-correction) | Remaining limits |
|---|---|---|---|
| A. Data integrity | **PASS** | every snapshot hash, size, header, row count and date range recomputed independently; 1,277 days, no gap; reconciliation and every legacy KPI recomputed; fail-closed on tampering; no leakage in 364 perturbation checks; `atlas check` and `atlas quality` pass | extraction time never recorded; anomaly flags incomplete (disclosed, M8-10) |
| B. Forecasting validity | **PASS** (for a research demonstration) | independent re-implementation of all seven models; the full M2 reference run reproduces byte for byte; no use of post-origin data; formulas of the tests checked; the overstated statistical claims corrected by the M8 erratum (M8-04) with a reproducible size study | lead over B\* not significant (Holm p 0.054); selection evidence weak (size-corrected p 0.069); not suitable for operational decisions |
| C. Uncertainty calibration and limitations | **FAIL** (calibration) | the intervals' pre-registered status is `FAILED_CALIBRATION`, unchanged; the evaluation was reproduced byte for byte and recomputed independently; every disclosure now correct and present wherever intervals or projections appear (M8-08, M8-09, M8-13 to M8-17) | accepted only as a prominently disclosed limitation; never a validated uncertainty capability |
| D. Optimisation correctness | **PASS** | an independent SciPy formulation reproduces every committed optimum, frontier, sensitivity and diagnosis to about 1e-15; four hand-derived instances match exactly; certificates hold; the reference run reproduces byte for byte | every parameter `HYPOTHETICAL`; plans are optimal for the assumed model only |
| E. API correctness | **PASS** | live contract equals the committed one; Schemathesis (729 cases) found no server error or schema breach; reference GETs make no computation; the archived-projection notice on every projected response (M8-09); provenance chain checked (M8-20); HEAD consistent with nginx (M8-21); a replaced worker no longer delays the next simulation (M8-22) | one shared API key; no queue (503 when busy) |
| F. Security | **PASS** (no known exploitable defect; not a guarantee) | key compared in constant time and checked before the body is read; public-demo cannot compute; strict validation and limits; no secret in logs, environment or argv; no credential in the history; API docs pages pinned with SRI and a policy (M8-23); IPv6 off by default (M8-25) | HTTP Basic without MFA; `public-demo` refused until a security review; OS libraries not pinned |
| G. Deployment package | **PASS** | container end-to-end test on a disposable Ubuntu 24.04 systemd server (section 4); recover reloads nginx (M8-02); rollbacks keep each release's own dashboard policy (M8-03); explicit rollback between two releases exercised (M8-37); reproducible environment fingerprint (M8-24); `render --apply` current-only and locked (M8-26) | not executed on the owner's server (gate N) |
| H. Frontend functionality | **PASS** | every page loads with no console error or CSP violation; 461 displayed values equal the API's; dates correct in six time zones and now tested in non-UTC zones (M8-29); failure states never show substitute numbers; Firefox and WebKit run in CI (section 4) | real Safari not tested (WebKit is its engine) |
| I. Accessibility | **PASS** (automated checks) | axe WCAG 2.2 A/AA on every page and state, including the corrected error summary (M8-27); keyboard, focus, reduced motion, forced colours, 320 px reflow | **NOT VERIFIED** with real screen readers or other assistive technology |
| J. Reproducibility | **PASS** | M2, M3 and M4 reference runs reproduce byte for byte; the dashboard build reproduces byte for byte; hash-checked files protected from CRLF conversion (M8-07); a manual workflow reproduces every committed run (M8-38); documented commands verified (M8-12) | bit-identical BLAS results need the same CPU class; the reproduction workflow first runs after the merge |
| K. Automated testing | **PASS** | section 4 | contention-only flakiness reduced (M8-30) |
| L. Documentation and provenance | **PASS** | README rewritten with every legacy claim qualified (M8-01); model card, data card, known limitations; errata for committed reports; every result carries a manifest of fingerprints | provenance gaps of the source extraction disclosed, not resolvable |
| M. Dependency and licensing review | **NOT VERIFIED** (licensing) | dependencies: 0 known vulnerabilities (pip-audit, npm audit), weekly audit added (M8-06), third-party notices shipped (M8-05), actions on Node.js 24 (M8-32), cooldown documented as a review rule (M8-33) | **owner actions:** no project licence; NYC Open Data terms for the derived tables unconfirmed |
| N. Actual VPS deployment readiness | **NOT VERIFIED** | runbook prepared; preflight, bootstrap and every step tested in a disposable container | the owner's server has not been inspected; phase B needs written authorisation |

## 4. Measured checks on the candidate

To be completed with the final run on the candidate commit (see M8_CORRECTIONS.md section 1).

## 5. Outstanding risks

1. **Over-interpretation.** The interface and documentation are polished; the numbers are not
   validated. Every number carries its status, and the README leads with what was not shown.
2. **Statistical evidence.** The M2 lead is not established; its selection passed an oversized
   test. A future protocol needs a correctly sized test fixed before any run.
3. **Single shared secrets.** HTTP Basic accounts and one simulation key; adequate for a private
   demonstration only.
4. **Toolchain upkeep.** Weekly audits now report advisories; acting on them (rebuild and
   redeploy a release) is a manual operation, and the three dependency caps reach their review
   dates in October 2026.
5. **Environment drift.** BLAS kernels differ between CPU classes, so bit-identical
   reproduction of the regressions needs a comparable CPU; the reproduction workflow reports the
   CPU it ran on.
6. **Licensing and data terms.** Unresolved owner decisions (gate M).
7. **Phase B unknowns.** Provider CPU flags, DNS, TLS issuance, firewall and real reboots are
   untested until the real server (runbook section 9).

## 6. Rollback

Rolling back the candidate in the repository means not merging the pull request, or reverting
its merge commit: M8 changes no research result, snapshot file or legacy artifact. On a server,
rollback is `atlas-deploy rollback` or automatic on failed verification
([RELEASE_CANDIDATE.md](RELEASE_CANDIDATE.md) section 5).
