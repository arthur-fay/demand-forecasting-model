# Known limitations of ATLAS 1.1

Everything a reader should know before relying on anything ATLAS shows, in one place. Each item
names the document that explains it. ATLAS 1.1.0 adds the M9 static demonstration to 1.0 and
changes no research result, so every limitation of 1.0 still applies
([release/RELEASE_CANDIDATE_1.1.0.md](release/RELEASE_CANDIDATE_1.1.0.md)). Items marked **(M8)**
were found or confirmed by the M8 release audit ([audit/README.md](audit/README.md)). What the
audit deliberately left unchanged is listed in section 10.

## 1. What ATLAS is not

- **Not an operational tool.** It is a research demonstration on frozen data. No output is a
  validated forecast, a calibrated probability or a staffing, budget or service recommendation;
  the API fixes `validated_recommendation` and `point_forecast_validated` to `false`.
- **Not live.** The forecasts are an archived projection for 2026-07-01 to 2026-07-30 from data
  ending 2026-06-30. Nothing is fetched from NYC Open Data at run time, and the archived projection
  has never been scored.
- **Not deployed.** The deployment package (M6 phase A) is tested in disposable containers only;
  a real deployment (phase B) needs the owner's authorisation
  ([release/VPS_PHASE_B_RUNBOOK.md](release/VPS_PHASE_B_RUNBOOK.md)).

## 2. Data ([DATA_CARD.md](DATA_CARD.md))

- One frozen snapshot (`legacy-v0`), 2023-01-01 to 2026-06-30, city-wide daily counts plus
  five-borough agency segments. Its extraction time, query hash and raw responses were never
  recorded; re-querying the live source may not reproduce the counts.
- The NYC Open Data terms have not been confirmed for redistribution of the committed derived
  tables, although the public repository already redistributes them. The general NYC Open Data
  policies were reviewed on 2026-10-19 and again by the owner on 2026-10-20; any
  dataset-specific terms of the agency that supplies `erm2-nwe9` are unverified, the governing
  NYC.gov terms were never read in full (the 1.1.0 preparation could not reach the City's sites),
  and possible conditions on applications that use the data are unverified (owner action;
  section 9).
- City-wide totals and borough segments differ by 13,279 requests (0.11 %); shares are of the
  five-borough subset.
- 311 counts measure reporting, not need, and reporting differs between communities.
- The anomaly flags are incomplete: they miss part of the winter 2025-26 cluster and an outage
  that follows another within four weeks **(M8)** ([DATA_QUALITY.md](DATA_QUALITY.md)).
- No NYC/NYS observances other than federal holidays, no declared events, no weather.

## 3. Forecasting (M2, [BACKTESTING.md](BACKTESTING.md))

- The recommended model's lead over the same-weekday median (B\*) is **not statistically
  established**: confirmation MAE 984.9 against 1,112.2, 95 % interval of the difference
  −256 to +2, Holm-adjusted p 0.054; about 10-14 effective independent observations.
- **(M8)** The development selection test over-rejects at a 30-day horizon with a few hundred
  overlapping origins: the nominal 5 % rule rejects a true null 7.6-9.1 % of the time. After a
  size correction the selection p-value is 0.069, above its 5 % gate, so a correctly sized test
  would most likely have kept B\*. The recommendation stands only under the pre-registered rule
  as computed, and its statistical support is weak (M8 erratum in BACKTESTING.md).
- **(M8)** The advantage over seasonal naive (H2) is borderline once the test's size is
  corrected. It is significant after Holm (0.045) under the test's own assumption of no
  dependence beyond 29 days. Under nulls that keep dependence of 45 days or more, its Holm-adjusted p-value is 0.068-0.155
  ([audit/scripts/dm_dependence_sensitivity_output.md](audit/scripts/dm_dependence_sensitivity_output.md)).
- The confirmation period has been exposed: a new, untouched evaluation needs data after
  2026-06-30.
- The lead is not uniform (6 of 28 origin months lost to B\*), and recursion drives most of the
  error growth with horizon.
- The original project's headline figures (4.93 % MAPE, "13 days of advance warning", "planning
  capacity") do not mean what they were presented as ([LEGACY_LIMITATIONS.md](LEGACY_LIMITATIONS.md)).

## 4. Uncertainty (M3, [UNCERTAINTY.md](UNCERTAINTY.md))

- The prediction intervals have the pre-registered status **`FAILED_CALIBRATION`**: 95 % coverage
  was more than 3 points below nominal at horizons 8-30. This is a failed criterion, accepted
  only as a disclosed limitation of a research demonstration, never as a validated uncertainty
  capability.
- The evidence is too thin for any calibration claim either way. There are about 8-9 effective
  independent observations, and the 95 % HAC interval of the 80 % coverage spans 65 to 95 %. That
  interval uses `t(335)` critical values; a `t(ESS − 1)` reference (about 7-8 degrees of
  freedom) would make it wider still, ±17.5 instead of ±14.6 points **(M8)**.
- The interval construction itself (empirical type-7 quantiles without a finite-sample
  correction) lowers the expected coverage of a 95 % interval by about half a point at the
  calibration sizes used, which is more than the margin by which the two failing buckets missed
  the tolerance **(M8)**; a corrected construction might have been `insufficient_evidence`
  rather than failed, which is not decision-grade either
  ([audit/M8_SCIENTIFIC_VALIDATION.md](audit/M8_SCIENTIFIC_VALIDATION.md) section 3).
- Exceedance probabilities come from the same uncalibrated distribution and share its status.
- The M3 comparisons with baselines are descriptive and not adjusted for multiplicity **(M8)**.

## 5. Decision simulation (M4, [OPTIMIZATION.md](OPTIMIZATION.md))

- Every operational parameter (hours, productivity, wages, surge price, budget, targets, penalty
  weights) is **`HYPOTHETICAL`**; no real NYC staffing, cost or capacity data exist.
- Plans are certified optimal for the hypothetical linear programme only (continuous quantities,
  one city-wide unit, no shift rules, deterministic demand). Six of nine reference scenarios are
  infeasible under their limits and are shown with labelled `DIAGNOSTIC` relaxed plans.
- No chance constraints or stochastic programming: with failed intervals, no probabilistic
  guarantee could be supported.

## 6. API and dashboard (M5, M7)

- One optional shared API key protects simulations; no user accounts or roles. There is no queue:
  a full worker pool answers 503.
- Browsers:
  - the whole browser test suite runs in Chromium, locally and in CI;
  - the API-backed pages and every injected failure state also run in Playwright's Firefox and
    WebKit (Safari's engine) in CI **(M8)**;
  - the accessibility (axe), keyboard, reflow, phone-layout and nginx tests run in Chromium only;
  - real Safari, real mobile devices and screen-reader sessions are not verified.
- Automated accessibility checks pass. They do not replace testing with assistive technology or
  with people with disabilities.
- Some page chrome states two fixed facts of this release (the snapshot's end date and the number
  of reference scenarios) instead of reading them from the API.

- **(M9) The static demonstration** ([STATIC_DEMO.md](STATIC_DEMO.md) section 13):
  - prepared and tested against a local stand-in only; GitHub Pages is not enabled and has not
    been exercised (extensionless pages, its 404 handling, headers and caching);
  - no live simulation, by design: the recorded reference scenarios only;
  - a static host sends no headers of the site's own, so there is no framing protection
    (`frame-ancestors`, `X-Frame-Options`) and no `Cross-Origin-Opener-Policy` (a site that
    opened the dashboard can later send its tab to a look-alike page); the policy is in the
    page;
  - only the HTML pages carry `noindex`: the JSON answers and the licence texts can be
    indexed apart from the pages that qualify them;
  - the project site shares the `arthur-fay.github.io` origin with the owner's other Pages
    sites (or, if the user site gets a custom domain, that domain's), so what it shows is only
    as trustworthy as every one of them (a page of the same origin can rewrite it after its
    checks); the owner reviews this before publication;
  - axe and reflow are checked in Chromium only; Firefox and WebKit run the functional and
    keyboard tests; real Safari, real phones and screen readers are untested;
  - publication needs the NYC Open Data terms confirmed and the code-licence decision recorded
    (section 9).

## 7. Deployment and security (M6, [DEPLOYMENT.md](DEPLOYMENT.md) section 17)

- One server, one process: no high availability; an upgrade restarts the API for a few seconds.
- HTTP Basic accounts: shared secrets without MFA or roles; fine for a private demonstration, not
  for a public service. The `public-demo` profile is refused until a security review, DDoS
  protection and a log-retention decision.
- Operating-system libraries (Python, OpenSSL, nginx) are not pinned; Ubuntu updates can change
  them under a running release.
- Commits are not signed; builds trust GitHub for the commit content (SHA-checked) and PyPI for
  the wheels (hash-checked).
- No automated tool can show the absence of vulnerabilities: the audit found no exploitable defect
  and the dependency audits report none known today, which is not a guarantee.

## 8. Reproducibility

- Byte-identical reproduction of the runs that involve the regression (M2, M3 and the legacy
  holdout) needs the CPU class of the reference runs. That means OpenBLAS's SkylakeX (AVX-512)
  kernels; on other CPUs the last bits differ. The M4 run reproduces either way **(M8)**.
- The sealed M2 development record verifies only with the code and lock of its own commit,
  `a16672f`.
- The research reproduction is a manual CI workflow. It can run only once it is on the default
  branch, so its first run is an owner action after the merge.
- The dashboard build reproduces byte for byte from a checkout with LF line endings. Its HTML
  entry file is converted on a Windows checkout with `core.autocrlf=true`
  ([release/RELEASE_CANDIDATE.md](release/RELEASE_CANDIDATE.md)).
- The snapshot cannot be re-derived from the live source (section 2).

## 9. Licensing and attribution

- **Code licence chosen; City data terms still pending.** The owner chose the
  [MIT License](../LICENSE), Copyright (c) 2026 arthur-fay, for original ATLAS code
  the owner may license. The MIT grant is **not** a licence for NYC 311 data or
  derived City aggregates, nor for other owners' materials. The repository is
  **public** (since 2026-07-18), and these historical aggregates are already
  redistributed. General NYC Open Data reuse policies have been reviewed,
  but any dataset/provider-specific conditions have not been confirmed. The
  public website remains blocked pending the M9 owner publication decisions
  ([OPEN_SOURCE_AND_DATA_TERMS.md](OPEN_SOURCE_AND_DATA_TERMS.md);
  [STATIC_DEMO.md](STATIC_DEMO.md) section 9), and the confirmation is also due before the
  `v1.1.0-rc.1` tag, or the repository is made private
  ([release/RELEASE_GATE_1.1.0.md](release/RELEASE_GATE_1.1.0.md) section 6).
- **(1.1.0) Possible conditions on applications, unverified.** Secondary sources quoting
  NYC.gov terms say that providers of software applications using the data must notify the
  City and display prescribed disclaimers, and that the City may require that use stop. Whether
  these terms are current and apply to `erm2-nwe9` is unknown: the 1.1.0 preparation could not
  reach the City's sites to check. If they apply, they apply to this public repository too
  ([release/RELEASE_GATE_1.1.0.md](release/RELEASE_GATE_1.1.0.md) section 5;
  [release/NYC_311_REUSE_REVIEW_2026-10-20.md](release/NYC_311_REUSE_REVIEW_2026-10-20.md)).
- The shipped dashboard bundles third-party code and a font under permissive licences (MIT, ISC,
  OFL-1.1); their notices ship with the build **(M8)** ([DASHBOARD.md](DASHBOARD.md)).
- Source data: NYC Open Data, 311 Service Requests (`erm2-nwe9`), City of New York; ATLAS is not
  affiliated with or endorsed by the City.
- **(M9)** Publishing the static demonstration on GitHub Pages would redistribute the derived
  data on a public web page as well. It stays blocked until the data terms are confirmed and
  the owner records the code-licence decision and approves publication
  ([STATIC_DEMO.md](STATIC_DEMO.md) sections 9 and 10).

## 10. What the M8 audit left unchanged

Every M8 finding was corrected; none was deferred. What the audit deliberately left as it was is
listed with the reasons in [audit/M8_CORRECTIONS.md](audit/M8_CORRECTIONS.md) section 4. That
covers the committed reports' original wording (corrected by errata), the generator strings that
would alter committed bytes, the M2 decision, and the owner decisions. So are the limitations
the final independent review recorded rather than changed
([audit/M8_CORRECTIONS.md](audit/M8_CORRECTIONS.md) section 7).
