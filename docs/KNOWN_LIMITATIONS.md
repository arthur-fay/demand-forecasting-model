# Known limitations of ATLAS 1.0

Everything a reader should know before relying on anything ATLAS shows, in one place. Each item
names the document that explains it. Items marked **(M8)** were found or confirmed by the M8
release audit ([audit/README.md](audit/README.md)); the audit's deferred findings are listed in
section 9.

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
- The NYC Open Data terms of use have not been verified for redistribution of the committed
  derived tables (owner action).
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
  overlapping origins; after a size correction the evidence for the selection is borderline. The
  pre-registered choice stands; its statistical support is weak (M8 erratum in BACKTESTING.md).
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
- The evidence is too thin for any calibration claim either way: about 8-9 effective independent
  observations; the 95 % HAC interval of the 80 % coverage spans 65 to 95 %.
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
- The dashboard was tested in Chromium only (locally and in CI): Firefox and Safari, and real
  screen-reader sessions, are not verified. Automated accessibility checks (axe, keyboard, reflow)
  pass; they do not replace testing with assistive technology.
- Some page chrome states two fixed facts of this release (the snapshot's end date and the number
  of reference scenarios) instead of reading them from the API.

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

## 8. Licensing and attribution

- **No licence has been chosen for the project** (owner decision). The repository includes no
  LICENSE file.
- The shipped dashboard bundles third-party code and a font under permissive licences (MIT, ISC,
  OFL-1.1); their notices ship with the build **(M8)** ([DASHBOARD.md](DASHBOARD.md)).
- Source data: NYC Open Data, 311 Service Requests (`erm2-nwe9`), City of New York; ATLAS is not
  affiliated with or endorsed by the City.

## 9. Deferred M8 findings

Findings of the M8 audit that were not corrected in the release candidate, with the reason, are
listed in [audit/M8_CORRECTIONS.md](audit/M8_CORRECTIONS.md); none of them is CRITICAL or HIGH.
