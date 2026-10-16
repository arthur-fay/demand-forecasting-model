# M8 accessibility and browser verification

What was verified about the dashboard's accessibility and browser behaviour for the 1.0.0
release candidate, how, and what was not. The target is WCAG 2.2 AA. Automated checks and
keyboard tests are evidence about the markup and the interaction; **they are not a substitute
for testing with people and with assistive technology, which has not been done.**

## 1. What is verified, and where

| Check | How | Result |
|---|---|---|
| Every page loads with no console error and no CSP or Trusted Types violation | Playwright (committed suite) and the audit's own scripts, at 1280 and 375 CSS px | pass |
| axe, WCAG 2.0-2.2 A and AA rules | committed suite: every page, light and dark, with tables and disclosures open, a simulation result, a failed run, the phone layout, and (M8) the error summary with several invalid fields; audit: the public-demo simulator, an error state, the legacy window, a DIAGNOSTIC scenario at 375 px | no violation |
| Target size (2.5.8) | M8-27: the simulator's error-summary links were 16 px high with two or more field errors; now 24 px, checked by axe and by measuring each link | pass after the correction |
| Keyboard | tab order through all six pages (16 to 51 stops each), focus always visible, skip link first and into the main content, no keyboard trap, charts operable as sliders (Home, End, arrows) with a spoken reading | pass |
| Contrast | every visible text node on six pages in both colour schemes with disclosures open, against blended backgrounds; control borders, focus ring, series and status colours computed from the design tokens | 0 failures against 4.5:1 (3:1 large text); non-text ≥ 3:1 |
| Reflow and zoom | 320 CSS px, 200 % zoom, a Pixel 7 viewport: no page-level horizontal scroll (only labelled, focusable table regions scroll) | pass |
| Motion and colour | reduced motion turns the page transition off; forced colours keep the current page marked | pass |
| Not colour alone | every status (FAILED_CALIBRATION, HYPOTHETICAL, DIAGNOSTIC, not significant) has an icon and a word; every chart has a data table and a line pattern per series | pass |
| Dates across time zones | M8-29: the unit tests now run in a non-UTC zone and reload the date module in three zones; Playwright checks the projection start, the last observed day and the observed Independence Day in Pacific/Kiritimati and America/Los_Angeles | pass |
| Scientific warnings in view | positions measured on desktop and phone: the status labels appear in the first screen of each page and next to the numbers they qualify | pass |

## 2. Browsers

| Browser | Status | Evidence |
|---|---|---|
| Chromium (Playwright 1.56.1, build 1194) | **verified** | the whole Playwright suite (API, phone, nginx private and public demo) locally and in CI |
| Firefox (Playwright's build) | **verified in CI** for the API-backed pages and every failure state | new in M8: the `firefox` project (`ATLAS_E2E_ALL_BROWSERS=1`), run by the dashboard CI job; results in [M8_CORRECTIONS.md](M8_CORRECTIONS.md) section 1 |
| WebKit (Playwright's build, Safari's engine) | **verified in CI** for the same specs | the `webkit` project, same job |
| Safari itself (macOS, iOS) | **not verified** | WebKit is its engine, not the browser; no Apple device was available |
| Edge, Samsung Internet, mobile browsers on real devices | **not verified** | Edge shares Chromium's engine; nothing was run on a real phone (only an emulated Pixel 7 viewport in Chromium) |

The Firefox and WebKit runs cover page loading, the scientific labels, the scenarios, the
simulator against the real M4 engine, the served files, the licence notices, the time zones and
every injected failure (slow, unavailable, network, timeout, refused integrity, busy, rate
limited). The keyboard, axe, reflow and phone specs run in Chromium only.

## 3. Not verified

- **Screen readers** (NVDA, JAWS, VoiceOver, TalkBack): no session was run. Only the ARIA
  attributes, live-region text and axe results were checked.
- **People with disabilities**: no usability testing.
- **Real Safari**, other browsers and real mobile devices (section 2).

These are disclosed in [../DASHBOARD.md](../DASHBOARD.md) and
[../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md). The accessibility gate
([../release/RELEASE_GATE.md](../release/RELEASE_GATE.md), gate I) passes on the automated
evidence only and records assistive technology as not verified.
