# NYC 311 data reuse: M9 publication review (20 October 2026)

**Decision status: EVIDENCE GATHERED; DATA-SPECIFIC APPROVAL NOT YET RECORDED.**
This is a research record for the owner to review before any ATLAS public GitHub Pages deployment. It is not legal advice, an assertion of rights over City data, or authorization to publish. The publication gate at `docs/release/PAGES_PUBLICATION.json` stays closed (all conditions false).

## Dataset and ATLAS transformation

- **Dataset:** *311 Service Requests from 2020 to Present*, NYC Open Data ID **`erm2-nwe9`**, data provided by **311**, listed by NYC Open Data under City of New York / Office of Technology and Innovation (OTI).
- **Official dataset page:** https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2020-to-Present/erm2-nwe9/about_data
- **API:** https://data.cityofnewyork.us/resource/erm2-nwe9.json
- **ATLAS source-data window:** 2023-01-01 through 2026-06-30 inclusive, retained as the frozen `legacy-v0` snapshot. These are **not current figures**. The original extraction timestamp and official City revision/version were **not recorded**; do not invent them.
- **Transformations:** Service requests aggregated into citywide counts per date, segmented counts by date, agency and the five named boroughs, and derived historical summary tables; then used for retrospective model assessment, archived projections, failed-calibration prediction intervals and hypothetical optimization reference scenarios. The published static demo includes selected aggregated responses, not raw individual complaint narratives, addresses or identifiers. No City endorsement is claimed.
- **Version statement:** `legacy-v0` is an ATLAS archive identifier, **not** an official NYC source-data version. Current City data can be amended, and a fresh query may produce different historical counts.

## Official evidence reviewed

**A. NYC Administrative Code §23-502(d) and §23-504 (current codification).**
Source: https://codelibrary.amlegal.com/codes/newyorkcity/latest/NYCadmin/0-0-0-130572
The current law provides that public datasets are made available without restrictions on their use, while allowing the City to require redistributors to identify the source, version and modifications; it also disclaims City warranties. This is primary support for the general ability to republish and transform officially released public datasets.

**B. NYC Open Data FAQ, 'Are there restrictions on how I can use Open Data?'**
Source: https://www.nyc.gov/opendata/get-started/FAQs
The City's answer states that there are no restrictions on Open Data use. This is general guidance, not a finding about any supplementary dataset-specific terms.

**C. Official 2023 NYC Open Data Technical Standards Manual, section 2.3.2 (Data Ownership).**
Source: https://opendata.cityofnewyork.us/wp-content/uploads/NYC_OpenData_TechnicalStandardsManual.pdf
The manual states that published agency datasets become public resources available without restrictions or licensing requirements. The underlying records remain the responsibility of originating City agencies. Distinguish ownership of ATLAS original code from the provenance and rights governing the data.

**D. NYC Open Data Terms of Use (portal).**
Source: https://data.cityofnewyork.us/stories/s/Terms-of-Use/k9k7-3cje
The terms incorporate NYC.gov Terms of Use/Privacy Policy and **any additional terms defined by entities providing specific datasets**. The City provides datasets for informational purposes and without warranties. This is why the owner should confirm that no applicable additional provider condition has been overlooked, even given the generally permissive City policy.

**E. NYC 311's official dataset listing.**
Source: https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2020-to-Present/erm2-nwe9/about_data
Confirms dataset identity, NYC311 as provider, and daily updates. Its catalog listing is not proof of a specific Creative Commons/Public Domain license designation. A search of accessible metadata did **not** conclusively verify any extra 311-provider-specific terms; lack of a discoverable restriction must not be presented as an independently confirmed absence.

**F. U.S. Data.gov catalog entry (corroborating metadata).**
Source: https://catalog.data.gov/dataset/311-service-requests-from-2010-to-present
Lists this City's dataset, its identifier and public access status. The catalog's harvesting and dates need not equal ATLAS's frozen extraction date; do not conflate them.

**G. NYC 311 updates and changed dataset structure.**
Source: https://www.nyc.gov/opendata/news/all-news/311-Service-Requests-Updates
Documents live updates and the 2010–2019 / 2020–present split. The original ATLAS archive is fixed; the public live dataset evolves.

## Preliminary assessment

- **General reuse:** **SUPPORTED** by NYC law, the City's FAQ and current technical standards, including public reuse in an application, subject to attribution/source/version/modification requirements and applicable terms.
- **MIT License:** **ALREADY CHOSEN AND MERGED** (root `LICENSE`, Copyright (c) 2026 arthur-fay) for the owner's original ATLAS code only. It does not relicense NYC data, derivative historical data tables or third-party libraries and fonts.
- **Data-source credit and modification disclosure:** **PREPARED** in `README.md`, `docs/DATA_CARD.md` and `docs/OPEN_SOURCE_AND_DATA_TERMS.md`; confirm the actual static site's visible footer and provenance views before publishing.
- **Specific 311-provider conditions:** **NOT FULLY VERIFIED**. The dataset listing and general Terms of Use alone do not establish that none exist. Keep `nyc_open_data_terms_confirmed = false` until the owner explicitly reviews the actual dataset metadata and linked source-agency notices, or obtains confirmation via the City's Open Data help desk.
- **Publication:** **NOT AUTHORIZED**. No release tag, version 1.1.0 candidate approval, Pages environment enablement or website publication follows from this review.

## Remaining verification / owner decision

1. Open the 311 dataset's **About Data** page on the official NYC portal and review its **Licensing and Attribution** area (if present), metadata, attached supporting documents and any linked 311-specific terms.
2. Review the linked **NYC.gov Terms of Use** and ask the City Open Data team for clarification if any requirement remains unclear. The official help process is https://www.nyc.gov/opendata/get-started/FAQs and https://www.nyc.gov/opendata/news/all-news/311-Service-Requests-Updates (which points to nyc.gov/askopendata).
3. Confirm whether the City requires particular wording identifying the source, City version and modifications. Since ATLAS did not record the City-issued data revision or extraction timestamp, **disclose that omission** rather than inventing a version.
4. Verify the actual website content contains a clear source link, historical time window, transformations, non-affiliation statement and scientific caveats.
5. **Only after completing that review**, record the owner's factual conclusion and the dated links/evidence. Only then may a separately reviewed publication-approval PR set `nyc_open_data_terms_confirmed = true`. No automatic approval based on this document.

## Parallel release-engineering work (separately authorized to prepare, not publish)

The repository's documented post-M9 recommendation is **ATLAS 1.1.0**, with its own new candidate (`v1.1.0-rc.1`) and candidate manifest; 1.0.0-rc.1 remains an immutable historical M8 candidate. The version-preparation branch may regenerate lockfiles, API contract, dashboard fixtures, M9 static export and both dashboard builds as prescribed in `docs/DEVELOPMENT.md` `Versioning`, but **must not tag, publish, merge or change the publication gate**.

**Stop condition:** If an official policy, the code/data distinction, or any content in the static export contradicts the intended disclosure, stop and request owner approval rather than setting release/publication flags to true.
