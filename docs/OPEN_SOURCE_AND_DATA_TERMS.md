# ATLAS — source-code licensing and NYC Open Data attribution

**Status (2026-10-19):** Publication-preparation research, not legal certification or approval to publish. The owner has chosen the MIT License for the owner's **original ATLAS source code**. The copyright-holder name for the top-level `LICENSE` file has not yet been confirmed, and no license file has been added. The M9 publication gate must remain closed pending the owner decisions and reviewed changes.

## Distinguish the rights

| Material | Source and treatment |
| --- | --- |
| Original ATLAS source code written for this project | Owner-selected **MIT License**, to be implemented in a conventional root `LICENSE` file once the copyright holder is confirmed. The license does not transfer ownership of the code. |
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
3. [NYC Administrative Code / Local Law 11 of 2012, § 23-502(d)](https://cityofnewyork.github.io/opendatatsm/LocalLaw11of2012.html): public datasets are offered without license or use restrictions; attribution of source, version, and modifications can be required when republished.
4. [City of New York, 311 dataset listing](https://data.cityofnewyork.us/d/erm2-nwe9): confirms the exact dataset identifier. Check its current metadata and any provider-specific statements before approving publication.
5. [NYC Open Data, 311 Service Requests Updates](https://www.nyc.gov/opendata/news/all-news/311-Service-Requests-Updates): describes changes to the live dataset, which do not retroactively change the frozen ATLAS snapshot.

**Scope of this review:** These official sources establish the general open-data permission framework. They are not a substitute for checking the dataset's current metadata, the governing NYC.gov terms, and any 311-specific restrictions at publication time. No dataset-specific additional restriction has been substantiated in this preparation, but *not found* is not the same as *confirmed absent*. Do **not** set `nyc_open_data_terms_confirmed` to true solely on the basis of this document.

## Publication safeguards and remaining decisions

The read-only GitHub Pages build has been implemented and tested but **not** deployed.

Before publication, the owner and release reviewer must:

1. Confirm the copyright holder's correct name; add the canonical MIT license text with copyright year 2026; clarify the boundary with City data and third-party assets in the README and data card.
2. Finish the applicable City/data-provider terms review and explicitly confirm the public redistribution of the historical aggregates and static data exports.
3. Choose the post-M9 release version (M9 recommends **1.1.0**), update the code version and generated artifacts through the reproducible release process, and verify CI.
4. Read and accept M8's scientific limitations: the model's apparent advantage is not robustly established, **M3 FAILED_CALIBRATION**, every operating parameter is **HYPOTHETICAL**, and the projection is archived.
5. Review the public static site's shared GitHub Pages origin and its limits; configure Pages, the protected `github-pages` environment, and the manual approval process.
6. Record only *actually fulfilled* conditions in `docs/release/PAGES_PUBLICATION.json` in a separate reviewed pull request. Publishing additionally needs the external repository variable and manual dispatch.

**No release tag, GitHub Pages publication, or changes to owner approval flags are authorized by preparing this attribution record.** This is informational, not legal advice.
