# ATLAS — source-code licensing and NYC Open Data attribution

**Status (2026-10-19):** Publication preparation, not legal certification or authorization to publish. The owner confirmed **`arthur-fay`** as the copyright holder and **2026** as the year. The canonical [MIT License](../LICENSE) has been added at the repository root **for original ATLAS source code the owner has the right to license**. NYC Open Data and third-party materials remain subject to their own terms. The M9 publication gate stays closed pending the separate publication decisions.

## Distinguish the rights

| Material | Source and treatment |
| --- | --- |
| Original ATLAS source code authored and owned by `arthur-fay` | **MIT License**, copyright (c) 2026 arthur-fay, in the conventional root [`LICENSE`](../LICENSE). The grant applies only to code and associated documentation the holder has rights to license; it does not transfer ownership. |
| NYC 311 source data and historical aggregates derived from it | NYC Open Data; **not** claimed to be owned by the ATLAS author and **not** relicensed by the ATLAS MIT notice. Subject to the applicable City and source-agency terms. |
| Reproduced third-party code, packages, documentation and fonts | Their existing rights and licenses govern. See notices already shipped with the dashboard. |
| Research results built from the source data | Must preserve data-source and derivation context and the ATLAS scientific caveats. Do not imply that an MIT notice changes rights in underlying City data. |

A root MIT file is a permission grant for code the owner has rights to license, not a claim of ownership of the entire repository. Material from other authors or the City is excluded except insofar as their separate terms permit its use.

## NYC 311 source and modifications

- **Publisher and data source:** City of New York, NYC Open Data; the 311 service requests dataset is attributed to **311**.
- **Dataset title:** *311 Service Requests from 2020 to Present*.
- **Dataset ID:** `erm2-nwe9`.
- **Dataset page:** https://data.cityofnewyork.us/d/erm2-nwe9
- **API source:** https://data.cityofnewyork.us/resource/erm2-nwe9.json
- **ATLAS frozen data window:** 2023-01-01 through 2026-06-30 inclusive.
- **Changes made:** The original project aggregated source service requests into counts by day and by day/agency/five named boroughs, then created derived summary tables. ATLAS registered and verified those artifacts as the frozen `legacy-v0` snapshot, developed retrospective forecasting and uncertainty analyses, and built hypothetical mathematical optimization scenarios and a read-only static demonstration.
- **Important provenance gap:** The original source-data extraction timestamp and official source-data revision were not recorded. `legacy-v0` identifies the ATLAS archive, **not** a version issued by the City. Avoid saying ATLAS displays current 311 observations or that old aggregates will reproduce when re-querying today's dataset.
- **Attribution and non-affiliation:** This independent research project uses NYC Open Data. It is **not affiliated with, sponsored by, or endorsed by** the City of New York, NYC 311, or NYC Open Data.

## Authoritative public-policy sources reviewed on 2026-10-19

1. [NYC Open Data, Terms of Use](https://data.cityofnewyork.us/stories/s/Terms-of-Use/k9k7-3cje): incorporates NYC.gov terms and any additional terms of the data-providing entity; City data are supplied for informational use with no warranties.
2. [NYC Open Data FAQ](https://www.nyc.gov/opendata/get-started/FAQs): states generally that Open Data may be used without restrictions.
3. [NYC Open Data Technical Standards Manual — Public Policies](https://cityofnewyork.github.io/opendatatsm/publicpolicies.html): public datasets generally have no licensing or usage restrictions, but identifying the source, version and modifications may be required when republished.
4. [City of New York, 311 dataset listing](https://data.cityofnewyork.us/d/erm2-nwe9): confirms the exact dataset identifier. Check its current metadata and any provider-specific statements before approving publication.
5. [NYC Open Data, 311 Service Requests Updates](https://www.nyc.gov/opendata/news/all-news/311-Service-Requests-Updates): describes changes to the live dataset, which do not retroactively change the frozen ATLAS snapshot.

**Scope of this review:** These official sources establish the general open-data permission framework. They are not a substitute for checking the dataset's current metadata, the governing NYC.gov terms, and any 311-specific restrictions at publication time. No dataset-specific additional restriction has been substantiated in this preparation, but *not found* is not the same as *confirmed absent*. Do **not** set `nyc_open_data_terms_confirmed` to true solely on the basis of this document.

**Later reviews (2026-10-20).** The owner's [NYC 311 reuse review](release/NYC_311_REUSE_REVIEW_2026-10-20.md) adds the NYC Administrative Code and keeps the provider-specific conditions open. The 1.1.0 preparation tried to read the dataset's metadata and the NYC.gov terms itself; its environment could not reach those sites. Search-engine excerpts point to an older NYC.gov "Data Mine" terms page asking providers of applications to notify the City and display disclaimers; whether it applies here is unknown. Both are recorded, unverified, in the [1.1.0 release gate](release/RELEASE_GATE_1.1.0.md) section 5.

## Publication safeguards and remaining decisions

The read-only GitHub Pages build has been implemented and tested but **not** deployed.

Before publication, the owner and release reviewer must:

1. **Code licence chosen:** the owner confirmed `arthur-fay` (2026) as the copyright holder, the canonical MIT `LICENSE` is at the repository root, and the README and data card distinguish original source code from City data and third-party assets. Recording this decision as `project_licence_decided` remains a separate step (item 6).
2. Finish the applicable City/data-provider terms review and explicitly confirm the public redistribution of the historical aggregates and static data exports.
3. Decide the post-M9 release version. M9 recommended **1.1.0**; the release preparation (pull request #13) carries the code and every generated artifact to 1.1.0 through the reproducible release process and prepares the candidate 1.1.0-rc.1 ([release notes](release/RELEASE_CANDIDATE_1.1.0.md)). The decision is still the owner's to record (`release_version_decided`).
4. Read and accept M8's scientific limitations: the model's apparent advantage is not robustly established, **M3 FAILED_CALIBRATION**, every operating parameter is **HYPOTHETICAL**, and the projection is archived.
5. Review the public static site's shared GitHub Pages origin and its limits; configure Pages, the protected `github-pages` environment, and the manual approval process.
6. Record only *actually fulfilled* conditions in `docs/release/PAGES_PUBLICATION.json` in a separate reviewed pull request. Publishing additionally needs the external repository variable and manual dispatch.

**No release tag, GitHub Pages publication, or changes to owner approval flags are authorized by preparing this attribution record.** This is informational, not legal advice.
