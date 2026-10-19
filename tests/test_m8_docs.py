"""Documentation corrections of the M8 audit that a later edit could silently undo.

M8-01: the README's front page presented the original project's refuted claims as current
results ("Forecast accuracy improved by 40.10%", "13 days of advance warning", the next 30
days' "330,941 requests", "Recommended actions"). Since M8 the README opens with an account
of ATLAS and keeps the original project as an archived section, in which every refuted claim
is quoted after "Original:" and followed by what ATLAS found, and every published KPI sits
next to what it actually measures.

M8-08: ``docs/UNCERTAINTY.md`` section 9.6 called the flagged evaluation days "intake drops
that the forecast does not anticipate", although their mean error (prediction minus actual)
is strongly negative: the forecast was far too low on those days.

Licence: M8 had the front page say that the repository had no licence file. The owner has
since added the MIT License, Copyright (c) 2026 arthur-fay, for the original ATLAS code. The
front page must now say what that grant covers and what it does not: the NYC 311 data and the
aggregates derived from them keep their own attribution and terms, third-party components keep
their own licences, and the code licence is not approval to publish the static site (M9).

The tests read the documents and the committed results only.
"""

from __future__ import annotations

import json
import re

from atlas.reproduce import KPI_MEANINGS
from tests.conftest import REPO_ROOT

README = REPO_ROOT / "README.md"
LICENSE = REPO_ROOT / "LICENSE"
UNCERTAINTY = REPO_ROOT / "docs" / "UNCERTAINTY.md"
M3_METRICS = REPO_ROOT / "docs" / "results" / "atlas-m3-intervals-v1" / "full" / "metrics.json"
PAGES_RECORD = REPO_ROOT / "docs" / "release" / "PAGES_PUBLICATION.json"
ARCHIVED = "## The original project (July 2026, archived)"
COPYRIGHT = "Copyright (c) 2026 arthur-fay"

#: The terms of the MIT License after its copyright line: word for word as the SPDX licence list
#: publishes them, and wrapped as GitHub's licence template (choosealicense.com) wraps them.
MIT_TERMS = """\
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

#: Words that name the NYC 311 data or the tables derived from them.
DATA_WORDS = r"\b(?:data|datasets?|tables|aggregates|records|311)\b"

#: Claims of the original README that ATLAS refuted or qualified (finding M8-01).
REFUTED = (
    "The selected regression forecast achieved a 4.93% MAPE",
    "Forecast accuracy improved by 40.10%",
    "The next 30 days are expected to generate approximately 330,941 requests",
    "Five forecast dates are expected to exceed the daily planning capacity",
    "The model provides 13 days of advance warning",
    "The linear regression model was selected because it produced the lowest",
)


def _flat(text: str) -> str:
    """The text with Markdown emphasis removed and all whitespace runs collapsed."""
    return " ".join(text.replace("**", "").split())


def _plain(text: str) -> str:
    """The text as a reader sees it: links reduced to their labels, quote and emphasis marks
    and code spans' backticks dropped, all whitespace runs collapsed."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^> ?", "", text, flags=re.MULTILINE)
    return " ".join(re.sub(r"[*`]", "", text).split())


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _front_section(heading: str) -> str:
    """A ``## `` section of the README's front page, the part before the archived project."""
    front = _readme().split(ARCHIVED, 1)[0]
    assert f"\n{heading}\n" in front, f"the front page has no section {heading}"
    return front.split(f"\n{heading}\n", 1)[1].split("\n## ", 1)[0]


def test_the_readme_opens_with_atlas_and_archives_the_original_project() -> None:
    text = _readme()
    assert text.startswith("# ATLAS"), "the README must open with ATLAS"
    assert ARCHIVED in text, "the original project is not an archived section"
    front = _flat(text.split(ARCHIVED, 1)[0])
    for heading in ("Executive KPIs", "Key Business Insights", "Recommended actions include"):
        assert heading not in _flat(text), heading
    # No legacy headline figure is presented before the archived section.
    for figure in ("4.93", "40.10", "330,941", "11,609", "13 days"):
        assert figure not in front, figure
    # The front page states what ATLAS is not and the status of each result.
    for qualifier in (
        "not an operational tool",
        "not significant",
        "FAILED_CALIBRATION",
        "HYPOTHETICAL",
        "archived projections",
        "not affiliated with or endorsed by",
    ):
        assert qualifier.lower() in front.lower(), qualifier


def test_the_root_licence_is_the_unchanged_mit_licence_of_arthur_fay_2026() -> None:
    assert LICENSE.is_file(), "the repository has no root LICENSE file"
    parts = LICENSE.read_text(encoding="utf-8").split("\n\n", 2)
    assert len(parts) == 3, "LICENSE is not a title, a copyright line and the terms"
    title, notice, terms = parts
    assert title == "MIT License", title
    assert notice == COPYRIGHT, f"not the 2026 copyright of arthur-fay: {notice}"
    assert terms == MIT_TERMS, "the standard text of the MIT License was changed"


def test_the_readme_licenses_only_the_original_code_under_mit() -> None:
    raw = _front_section("## Licence")
    assert re.search(r"\]\((?:\./)?LICENSE\)", raw), "the licence section does not link LICENSE"
    licence = _plain(raw)
    grant = (
        "Original ATLAS source code authored and owned by arthur-fay is licensed under the "
        f"MIT License, {COPYRIGHT}."
    )
    assert grant in licence, "the README does not say what the MIT License covers"
    # The grant claims neither the City's data nor anybody else's work.
    head = "This MIT grant is not a claim of ownership or a new license for "
    limit = re.search(re.escape(head) + r"[^.]*\.", licence)
    assert limit, "the README does not limit the MIT grant"
    for excluded in (
        "NYC 311 source data",
        "derived NYC datasets",
        "third-party packages or fonts",
        "materials of other copyright holders",
    ):
        assert excluded in limit[0], f"the MIT grant does not exclude {excluded}"
    assert "Those remain governed by their own terms and notices." in licence
    # The M8 wording, true until the LICENSE was added, does not come back.
    front = _plain(_readme().split(ARCHIVED, 1)[0]).lower()
    for stale in ("no licence file", "no licence has been granted"):
        assert stale not in front, f"the front page still says: {stale}"


def test_the_nyc_311_data_are_attributed_and_kept_out_of_the_mit_licence() -> None:
    data = _plain(_front_section("## Data and attribution"))
    for attribution in (
        "NYC Open Data, 311 Service Requests from 2020 to Present",
        "dataset erm2-nwe9",
        "published by the City of New York",
    ):
        assert attribution in data, f"the data are not attributed: {attribution}"
    relicensed = "The data and derived historical tables are not being relicensed under the "
    assert f"{relicensed}ATLAS MIT License." in data, "the data are not kept out of the MIT grant"
    assert "governed by the applicable NYC Open Data terms" in data
    # The section names the MIT License only to keep the data out of it, and so does every
    # sentence of the front page that names it next to the data.
    front = _plain(_readme().split(ARCHIVED, 1)[0])
    for sentence in re.split(r"(?<=[.;])\s+", data):
        if re.search(r"\bMIT\b", sentence):
            assert re.search(r"\bnot\b", sentence), f"the data under MIT: {sentence}"
    for sentence in re.split(r"(?<=[.;])\s+", front):
        if re.search(r"\bMIT\b", sentence) and re.search(DATA_WORDS, sentence, re.IGNORECASE):
            assert re.search(r"\bnot\b", sentence), f"the data under MIT: {sentence}"


def test_the_readme_does_not_present_the_licence_as_approval_to_publish() -> None:
    """The code licence settles none of the other M9 conditions. The README's account of the
    static site and of the data terms must match the committed publication record, without
    which the gate stays closed (tests/test_m9_pages_workflow.py)."""
    licence = _plain(_front_section("## Licence"))
    assert "This source-code license does not by itself approve the public website" in licence
    for condition in ("separate data-terms confirmation", "owner publication approval"):
        assert condition in licence, f"the licence section omits: {condition}"
    conditions = json.loads(PAGES_RECORD.read_text(encoding="utf-8"))["conditions"]
    if conditions["publication_approved"] is not True:
        opening = _plain(_readme().split("\n## ", 1)[0])
        assert "it is not published" in opening, "the README does not say the site is unpublished"
        front = _plain(_readme().split(ARCHIVED, 1)[0])
        approved = r"\b(?:has|have|had|was|were|been|is|are)\s+(?:now\s+)?approved\b"
        assert not re.search(approved, front), "the README presents publication as approved"
    if conditions["nyc_open_data_terms_confirmed"] is not True:
        data = _plain(_front_section("## Data and attribution"))
        pending = "dataset-specific conditions remain to be confirmed before publishing"
        assert pending in data, "the README does not say the data terms are unconfirmed"


def test_every_refuted_claim_is_quoted_as_original_and_followed_by_a_correction() -> None:
    text = _flat(_readme())
    archived = text.find(_flat(ARCHIVED))
    for claim in REFUTED:
        positions = [m.start() for m in re.finditer(re.escape(claim), text)]
        assert positions, f"the archived section no longer quotes: {claim}"
        for position in positions:
            assert archived != -1, "the archived section is missing"
            assert position > archived, f"outside the archived section: {claim}"
            original = text.rfind("Original:", 0, position)
            assert original != -1, f"not marked as the original claim: {claim}"
            assert "ATLAS:" not in text[original:position], f"not marked as original: {claim}"
            correction = text.find("ATLAS:", position)
            assert correction != -1, f"no correction follows: {claim}"
            assert "Original:" not in text[position:correction], f"no correction: {claim}"


def test_every_archived_kpi_says_what_it_actually_measures() -> None:
    text = _readme()
    assert "### Original KPIs" in text, "the archived KPI table is missing"
    section = text.split("### Original KPIs", 1)[1].split("\n### ", 1)[0]
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in section.splitlines()
        if line.startswith("|")
    ]
    assert rows[0] == ["KPI (as published)", "Result", "What it actually measures"]
    body = rows[2:]  # after the header and the alignment row
    assert len(body) == len(KPI_MEANINGS)
    for kpi, result, meaning in body:
        assert kpi, "a KPI row has no name"
        assert result, kpi
        assert len(meaning) > 20, f"{kpi}: no meaning given"


def test_every_legacy_reproduction_claim_names_the_declared_exception() -> None:
    text = _flat(_readme())
    assert "LEGACY-V0-EX-002" in text, "the README does not name the declared RMSE exception"
    for claim in ("every legacy", "reproduce arithmetically"):
        for match in re.finditer(re.escape(claim), text):
            window = text[match.start() : match.start() + 300]
            assert "LEGACY-V0-EX-002" in window, f"'{claim}' without the RMSE exception"


def _section_9_6() -> str:
    text = UNCERTAINTY.read_text(encoding="utf-8")
    return text.split("### 9.6 ", 1)[1].split("\n### ", 1)[0]


def _flagged_bias() -> float:
    metrics = json.loads(M3_METRICS.read_text(encoding="utf-8"))
    pair = metrics["decided_pair"]
    (row,) = (
        r
        for r in metrics["strata"]
        if (r["period"], r["model_id"], r["pair_id"], r["stratum"])
        == ("m3_evaluation", "atlas_linear_regression", pair, "flagged")
    )
    bias: float = row["bias"]
    return bias


def test_the_flagged_day_errors_are_described_in_their_actual_direction() -> None:
    section = _section_9_6()
    lines = section.splitlines()
    last_table_line = max(i for i, line in enumerate(lines) if line.startswith("|"))
    caption = _flat("\n".join(lines[: next(i for i, s in enumerate(lines) if s.startswith("|"))]))
    prose = _flat("\n".join(lines[last_table_line + 1 :]))
    # The sign convention is stated with the table.
    assert "prediction \N{MINUS SIGN} actual" in caption, (
        "the bias column's sign convention is not stated"
    )
    bias = _flagged_bias()  # committed: about -3,208 requests per day
    direction = "under-forecast" if bias < 0 else "over-forecast"
    assert direction in prose, f"section 9.6 does not call the flagged days an {direction}"
    # The old wording may survive only as the quoted text of the M8 correction.
    old = "intake drops that the forecast does not anticipate"
    for match in re.finditer(re.escape(old), prose):
        assert "M8 correction:" in prose[max(0, match.start() - 200) : match.start()], old
