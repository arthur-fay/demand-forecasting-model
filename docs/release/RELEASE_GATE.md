# ATLAS 1.0.0-rc.1: release gate

The acceptance decision for the release candidate, with the evidence for each gate. It is the
outcome of the M8 audit: the original, pre-correction assessment (verdict NO-GO for `194fa4a` as
it stood) is preserved in [../audit/M8_BASELINE_ASSESSMENT.md](../audit/M8_BASELINE_ASSESSMENT.md);
the corrections and the measured post-correction checks are in
[../audit/M8_CORRECTIONS.md](../audit/M8_CORRECTIONS.md).

## 1. Recommendation

**CONDITIONAL GO**, for this scope only: approve ATLAS 1.0.0 as a **private-first research
demonstration**. That means merging the M8 pull request, after which the owner tags
`v1.0.0-rc.1` and later `v1.0.0`. "Private-first" refers to the deployment profile: the first
deployment is private, behind accounts or a tunnel. It does not refer to the repository's
visibility. The approval holds on these conditions:

1. **CI and the manifest.** CI is green on the pull request's head commit, including
   `deploy-e2e`, the dashboard job's Firefox and WebKit run and the dependency audit. On the
   approved commit, `python3 docs/release/rc_manifest.py --check` passes.
2. **Limitations accepted.** The owner reads and accepts the disclosed limitations
   ([../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md)) and records the acceptance in their
   approval of the pull request or in the annotation of the `v1.0.0-rc.1` tag. Two of them
   matter most:
   - the failed calibration (gate C), which stays FAILED and is accepted only as a disclosed
     limitation of a research demonstration;
   - the weak selection evidence (gate B).
3. **Licence and data terms, before the tag.** **The repository is already public** (since
   2026-07-18). It has redistributed the code, without a licence, and the derived NYC 311 tables
   since its first commit. So this decision is due now, not at a later publication step. Before
   `v1.0.0-rc.1` is tagged, the owner either:
   - (a) decides the project licence, or records that none is granted, **and** confirms that
     the NYC Open Data terms permit redistributing the derived tables with the attribution the
     [data card](../DATA_CARD.md) gives; or
   - (b) makes the repository private until both are decided.

   Nothing in the repository grants or asserts a licence or a permission (gate M).

**From `v1.0.0-rc.1` to `v1.0.0`.** The same content becomes `v1.0.0` only after three things:

- the research-reproduction workflow (`reproduce-research.yml`) has run on the merge commit,
  with its outcome recorded together with the CPU and the OpenBLAS core it ran on;
- the weekly dependency audit is enabled and green;
- the conditions above still hold.

Any change to the candidate's content makes a new candidate (`rc.2`) with a regenerated manifest
([RELEASE_CANDIDATE.md](RELEASE_CANDIDATE.md)).

**NO-GO** for any operational use of the forecasts, intervals or plans, and for the
`public-demo` profile. A real deployment is **not yet authorised**: it is M6 phase B, prepared in
[VPS_PHASE_B_RUNBOOK.md](VPS_PHASE_B_RUNBOOK.md) and not executed.

## 2. Four different questions, four different answers

| Question | Answer | Why |
|---|---|---|
| Is the **research demonstration** ready as engineering? | **Yes, conditionally** (section 1) | Every verified finding is corrected: 29 with a regression test, 10 in documentation checked against the committed results by scripts and tests. So is every finding of the final independent review (section 7). The checks in section 4 pass. Every research result reproduces byte for byte on the reference CPU class (gate J). |
| Are the results **scientifically suitable for real operational decisions**? | **No** | The forecaster's lead over a simple baseline is not established, and a correctly sized selection test would most likely have kept the baseline. The intervals failed calibration. Every planning parameter is hypothetical. The data end 2026-06-30. |
| Is the **deployment package** ready? | **Yes, for a private-first phase B**, subject to the server's preflight | Build, activation, upgrade, rollback, recovery and monitoring were tested end to end on a disposable Ubuntu 24.04 systemd server, and the rollback and recovery defects found by M8 are corrected. Server hardening, the firewall, certificate issuance and backups are exercised only in phase B. |
| Has a **real production deployment** been verified? | **No** | Nothing has been deployed, and the owner's server has not been inspected. |

## 3. Acceptance matrix

The evidence is from the candidate unless marked *(baseline)*. Those checks ran on `194fa4a`
during the audit, on code the corrections did not change, or were re-checked as stated.

| Gate | Status | Evidence | Remaining limits |
|---|---|---|---|
| A. Data integrity | **PASS** | Every snapshot hash, size, header, row count and date range was recomputed independently: 1,277 days, no gap. The reconciliation and every legacy KPI were recomputed. Loading fails closed on tampering. No leakage in 364 perturbation checks *(baseline; the computational code is unchanged)*. `atlas check` and `atlas quality` pass (section 4). | The extraction time was never recorded. The anomaly flags are incomplete (disclosed, M8-10). |
| B. Forecasting validity | **PASS** (for a research demonstration) | All seven models were independently re-implemented, and the full M2 reference run reproduces byte for byte. No use of post-origin data. The test formulas were checked. The overstated statistical claims are corrected by the M8 erratum (M8-04), with reproducible size and dependence studies. PASS uses the pre-registered outcome as computed, the same rule that keeps gate C FAILED. | The lead over B\* is not significant (Holm p 0.054). With a correctly sized test the selection gate would not have been passed (size-corrected p 0.069), and the protocol would most likely have kept B\*. The recommendation therefore stands only under the pre-registered rule as computed. H2's significance after the size correction is not robust to the assumed error dependence. Not suitable for operational decisions. |
| C. Uncertainty calibration and limitations | **FAIL** (calibration) | The intervals' pre-registered status is `FAILED_CALIBRATION`, unchanged. The evaluation was reproduced byte for byte and recomputed independently. The disclosures are corrected wherever intervals or projections appear (M8-08, M8-09, M8-13 to M8-17). So are the construction's finite-sample bias and the degrees of freedom of the HAC coverage intervals (model card, UNCERTAINTY.md section 11). | Accepted only as a prominently disclosed limitation, never as a validated uncertainty capability. |
| D. Optimisation correctness | **PASS** | An independent SciPy formulation reproduces every committed optimum, frontier, sensitivity and diagnosis to about 1e-15, and four hand-derived instances match exactly *(baseline; the M4 code is unchanged)*. The certificates hold. The reference run reproduces byte for byte (section 4). | Every parameter is `HYPOTHETICAL`; plans are optimal for the assumed model only. |
| E. API correctness | **PASS** | The live contract equals the committed one (section 4). Schemathesis generated 729 cases over all 14 operations: no server error or schema breach *(baseline, and again on the corrected API in the final review)*. Reference GETs make no computation. The archived-projection notice is on every projected response (M8-09), and the selection notice now gives the exact size figures. The provenance chain is checked (M8-20). HEAD is consistent with nginx (M8-21). A replaced worker no longer delays the next simulation (M8-22). | One shared API key; no queue (503 when busy). |
| F. Security | **PASS** (no known exploitable defect; not a guarantee) | The key is compared in constant time and checked before the body is read. public-demo cannot compute. Validation and limits are strict. No secret appears in logs, the environment or argv. No credential was found in any of the 1,228 blobs reachable from every ref (section 4). The API docs pages are pinned with SRI and their own policy (M8-23). IPv6 is off by default (M8-25). A release's recorded dashboard policy must meet a fixed minimum (final review). | HTTP Basic without MFA. `public-demo` is refused until a security review. OS libraries are not pinned. |
| G. Deployment package | **PASS** | Container end-to-end test on a disposable Ubuntu 24.04 systemd server (section 4). recover reloads nginx (M8-02). Rollbacks keep each release's own dashboard policy (M8-03). An explicit rollback between two releases is exercised (M8-37). The environment fingerprint is reproducible (M8-24). `render --apply` is current-only and locked (M8-26). | Not executed on the owner's server (gate N). |
| H. Frontend functionality | **PASS** | Every page loads with no console error or CSP violation, the licence notices included (final review). Displayed values equal the API's: 461 compared *(baseline)*, and the corrected status, certificate and date wording re-checked in the final review. Dates are correct in six time zones and now tested in non-UTC zones (M8-29). Failure states never show substitute numbers. Firefox and WebKit run in CI for the API-backed pages and every failure state (section 4). | The axe, keyboard, reflow, phone and nginx tests run in Chromium only. Real Safari (WebKit is its engine) and real devices are not tested. |
| I. Accessibility | **PASS** (automated checks) | axe WCAG 2.2 A/AA on every page and state, including the corrected error summary (M8-27). Keyboard, focus, reduced motion, forced colours, 320 px reflow. | **NOT VERIFIED** with real screen readers or other assistive technology. The automated checks run in Chromium only. |
| J. Reproducibility | **PASS** | The M2, M3 and M4 reference runs reproduce byte for byte on the reference CPU class (AVX-512, OpenBLAS SkylakeX kernels), and the sealed M2 development record with the code of its own commit. The dashboard build reproduces byte for byte. Hash-checked files are protected from CRLF conversion (M8-07). The release-candidate manifest is checked from committed content, independent of line endings. A manual workflow reproduces every committed run (M8-38). The documented commands are verified (M8-12). | Bit-identical regression results need the same CPU class (M4 reproduces regardless). The dashboard rebuild needs an LF checkout. The reproduction workflow first runs after the merge. |
| K. Automated testing | **PASS** | Section 4. | Contention-only flakiness reduced (M8-30). |
| L. Documentation and provenance | **PASS** | README rewritten, with every legacy claim qualified (M8-01). Model card, data card and known limitations. Errata for committed reports. Every result carries a manifest of fingerprints. | The provenance gaps of the source extraction are disclosed, not resolvable. |
| M. Dependency and licensing review | **NOT VERIFIED** (licensing) | Dependencies: 0 known vulnerabilities (pip-audit, npm audit; section 4). A weekly audit was added (M8-06), third-party notices ship (M8-05), the actions run on Node.js 24 (M8-32), and the cooldown is documented as a review rule (M8-33). | **Owner actions** (section 1, condition 3): the public repository has no project licence, and the NYC Open Data terms for the derived tables are unconfirmed. |
| N. Actual VPS deployment readiness | **NOT VERIFIED** | Runbook prepared. The post-bootstrap preflight, bootstrap, build, activation, upgrade, rollback, recovery and monitoring were tested in a disposable container. | Not exercised there: the pre-bootstrap preflight on a fresh server, SSH hardening, ufw, certbot issuance and backups. The owner's server has not been inspected, and phase B needs written authorisation. |

## 4. Measured checks on the candidate

To be completed with the final run on the candidate commit (see M8_CORRECTIONS.md section 1).

## 5. Outstanding risks

1. **Over-interpretation.** The interface and documentation are polished; the numbers are not
   validated. Every number carries its status, and the README leads with what was not shown.
2. **Statistical evidence.** The M2 lead is not established, and its selection passed an
   oversized test that a correctly sized version would most likely have failed. A future
   protocol needs a correctly sized test, robust to long-range error dependence, fixed before
   any run.
3. **Single shared secrets.** HTTP Basic accounts and one simulation key; adequate for a private
   demonstration only.
4. **Toolchain upkeep.** Weekly audits now report advisories; acting on them (rebuild and
   redeploy a release) is a manual operation, and the three dependency caps reach their review
   dates in October 2026.
5. **Environment drift.** BLAS kernels differ between CPU classes, so bit-identical
   reproduction of the regressions needs a comparable CPU. The reproduction workflow reports the
   CPU it ran on.
6. **Licensing and data terms.** Unresolved owner decisions (gate M), on a repository that is
   already public.
7. **Phase B unknowns.** Provider CPU flags, DNS, TLS issuance, firewall and real reboots are
   untested until the real server (runbook section 9).

## 6. Rollback

Rolling back the candidate in the repository means not merging the pull request, or reverting
its merge commit: M8 changes no research result, snapshot file or legacy artifact. On a server,
rollback is `atlas-deploy rollback` or automatic on failed verification
([RELEASE_CANDIDATE.md](RELEASE_CANDIDATE.md) section 5).

## 7. Final independent review

After the corrections, two rounds of independent review examined the candidate. Six reviewer
roles took part: science, release assessment, security, infrastructure, frontend and release
engineering. Every finding was reproduced by a separate verifier, and each MEDIUM one was also
challenged by a skeptic. None was CRITICAL or HIGH. Four were raised as MEDIUM:

- one was confirmed at MEDIUM by both verifiers: the release-candidate manifest failed on
  Windows checkouts;
- two were confirmed by both, rated MEDIUM by one and LOW by the other: H2's robustness after
  the size correction, and the licence condition, which assumed the repository was not yet
  public;
- one was rated LOW by both: the prominence of the M2 erratum.

The rest were LOW. All are corrected, each with a regression test where code changed. The
findings and their resolutions are in
[../audit/M8_CORRECTIONS.md](../audit/M8_CORRECTIONS.md) section 7.
