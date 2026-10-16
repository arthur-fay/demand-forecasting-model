"""The size of the HLN Diebold-Mariano rule at M2's horizon and sample sizes (M8 erratum).

M2 selects a model and tests H1 and H2 with ``atlas.stats.diebold_mariano``: the
Harvey-Leybourne-Newbold statistic of one loss differential per origin, a rectangular HAC
variance truncated at ``h - 1``, a Student t reference with ``T - 1`` degrees of freedom, and
the pre-registered decision p-value ``p_value_conservative`` (the larger of the HLN and
Bartlett ``2h`` p-values). With ``h = 30`` and only 336-486 overlapping origins this rule
rejects a true null hypothesis more often than its nominal 5 %, even when the differential
follows exactly the test's own maintained assumption (a moving average of order ``h - 1``).

These tests pin that known finite-sample property of the test as M2 uses it. They do not
show a bug in its formulas: with 3,000 origins the same rule is close to its nominal level.
The size study behind the M8 erratum is ``docs/audit/scripts/dm_size_study.py``; its
recorded output is ``docs/audit/scripts/dm_size_study_output.md``, and the last tests check
that ``docs/BACKTESTING.md`` quotes it and marks every statement it corrects.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from types import ModuleType

import numpy as np

from atlas.stats import diebold_mariano
from tests.conftest import REPO_ROOT

H = 30
NOMINAL = 0.05
SEED = 20261016
SCRIPT = REPO_ROOT / "docs" / "audit" / "scripts" / "dm_size_study.py"
OUTPUT = REPO_ROOT / "docs" / "audit" / "scripts" / "dm_size_study_output.md"
DOC = REPO_ROOT / "docs" / "BACKTESTING.md"


def _null_series(rng: np.random.Generator, origins: int) -> np.ndarray:
    """Mean of 30 consecutive independent daily shocks: an exact MA(29) with mean zero."""
    daily = rng.standard_normal(origins + H - 1)
    return np.convolve(daily, np.full(H, 1.0 / H), mode="valid")


def _rejection_rate(origins: int, replications: int) -> float:
    rng = np.random.default_rng([SEED, origins])
    rejected = 0
    for _ in range(replications):
        test = diebold_mariano(_null_series(rng, origins), horizon=H)
        assert test.computed
        assert test.p_value_conservative is not None
        rejected += test.p_value_conservative < NOMINAL
    return rejected / replications


def test_the_pre_registered_rule_over_rejects_at_336_origins() -> None:
    # 4,000 replications: the binomial standard error is 0.0034 at a true size of 5 % and
    # 0.0045 at the 9.1 % the size study measured. The bound 0.07 is 5.8 standard errors
    # above the nominal level ((0.07 - 0.05) / 0.0034) and 4.6 below the measured size
    # ((0.091 - 0.07) / 0.0045).
    assert _rejection_rate(336, 4_000) > 0.07


def test_the_same_rule_is_close_to_nominal_with_many_origins() -> None:
    # The control: the over-rejection is a finite-sample property. The size study measured
    # 5.5 % with 3,000 origins; with 4,000 replications the standard error is 0.0036, so the
    # bounds lie at least 4 standard errors from it.
    assert 0.035 < _rejection_rate(3_000, 4_000) < 0.07


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dm_size_study", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve the module by name
    bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # leave no __pycache__ in docs/
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = bytecode
        del sys.modules[spec.name]
    return module


def test_the_size_study_reads_the_committed_statistics() -> None:
    study = _script()
    tests = {t.name: t for t in study.committed_tests()}
    assert [(t.name, t.origins, t.overlap) for t in tests.values()] == [
        ("selection", 486, H),
        ("H1", 336, H),
        ("H2", 336, H),
    ]
    assert round(abs(tests["selection"].statistic), 3) == 2.022
    assert round(tests["selection"].p_values[study.RULE], 4) == 0.0437
    assert round(tests["H1"].holm_p, 4) == 0.0535
    families = {family for family, _, _, _ in study.m3_tests()}
    assert families == {"coverage", "WIS", "Brier"}
    # The simulation path still runs against the current atlas.stats.
    p_values, computed = study.simulate(336, H, "gaussian", 50, SEED, variants=True)
    assert set(p_values) == set(computed) == set(study.COLUMNS)
    assert all(np.all((v >= 0.0) & (v <= 1.0)) for v in p_values.values())
    # A draw without a p-value counts as a non-rejection.
    assert all(np.all(p_values[name][~computed[name]] == 1.0) for name in study.COLUMNS)
    assert computed[study.RULE].all()


def _rows(section: str) -> list[list[str]]:
    """Table rows of one ``## `` section of the recorded output, as stripped cells."""
    text = OUTPUT.read_text(encoding="utf-8")
    body = text.split(f"## {section}", 1)[1].split("\n## ", 1)[0]
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in body.splitlines()
        if line.startswith("| ")
    ]
    return rows[1:]  # the first row is the header


def _first(cell: str) -> str:
    return cell.split(" ")[0]


def test_the_recorded_output_comes_from_the_default_run() -> None:
    header = OUTPUT.read_text(encoding="utf-8").split("\n## ", 1)[0]
    assert "20,000 replications per setting; seed 20261016" in header
    assert "counts as a non-rejection" in header  # the convention for draws without a p-value


def test_backtesting_quotes_the_recorded_size_study() -> None:
    doc = " ".join(DOC.read_text(encoding="utf-8").split())
    assert "## 12. M8 erratum" in doc, "BACKTESTING.md has no M8 erratum section"
    erratum = doc.split("## 12. M8 erratum", 1)[1]
    rule = "pre-registered rule (larger of HLN and Bartlett 60)"
    size = {r[0]: r[1:] for r in _rows("1. Empirical size")}
    sizes = f"{_first(size[rule][0])} % | {_first(size[rule][1])} %"
    assert f"| pre-registered rule (`p_value_conservative < 0.05`) | {sizes} |" in erratum
    critical = {r[0]: r[1:] for r in _rows("2. Size-corrected")}
    values = f"are {_first(critical[rule][0])} (486 origins) and {_first(critical[rule][1])} (336"
    assert values in erratum
    corrected: dict[tuple[str, str], str] = {}
    for test, _, variant, _, _, gaussian, _ in _rows("3. Committed"):
        corrected[(test, variant)] = _first(gaussian)
    holm = {(r[0], r[1]): r[4] for r in _rows("4. Holm")}
    assert f"| 0.044 | {corrected[('selection', rule)]} (" in erratum
    assert f"| 0.054 | {corrected[('H1', rule)]} (" in erratum
    assert f"| 0.003 | {corrected[('H2', rule)]} (" in erratum
    assert f"| {holm[('H2', 'gaussian')]} (" in erratum
    assert f"selection evidence is borderline (p {corrected[('selection', rule)]})" in erratum
    # The per-bandwidth table of section 6.2.
    variants = [
        "HLN alone (rectangular 29)",
        "rectangular 59",
        "bartlett 60",
        "bartlett 90",
        "bartlett 120",
    ]
    for test, label in (
        ("selection", "selection (development, 486 origins)"),
        ("H1", "H1 (confirmation, 336 origins)"),
        ("H2", "H2 (confirmation, 336 origins)"),
    ):
        cells = " | ".join(corrected[(test, v)] for v in variants)
        assert f"| {label} | {cells} |" in doc, test


#: Statements of the pre-M8 text that the size study does not support. Each stays where it
#: was written (the document keeps its history) and must sit next to an M8 note.
UNSUPPORTED = (
    "does not depend on the HAC bandwidth",
    "stays significant under every HAC bandwidth tried",
    "H2 stays significant",
    "becomes more, not less, significant under wider bandwidths",
    "which works against significance",
)


def test_every_unsupported_robustness_statement_carries_an_m8_note() -> None:
    doc = " ".join(DOC.read_text(encoding="utf-8").split())
    for statement in UNSUPPORTED:
        positions = [m.start() for m in re.finditer(re.escape(statement), doc)]
        assert positions, statement
        for position in positions:
            window = doc[max(0, position - 900) : position + 900]
            assert "M8 erratum" in window, statement
