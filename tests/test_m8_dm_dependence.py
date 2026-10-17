"""Dependence sensitivity of the size-corrected M2 tests (M8 erratum, H2 robustness).

``docs/audit/scripts/dm_size_study.py`` size-corrects the pre-registered HLN Diebold-Mariano
rule under the test's own MA(29) null. ``docs/audit/scripts/dm_dependence_sensitivity.py``
repeats the correction under nulls calibrated to the observed loss differentials, which it
reads from the committed ``m2_loss_differentials.csv`` (rebuilt from a regenerated M2 run whose
``forecasts.csv`` has the hash in the run manifest). These tests pin that the committed
differentials are the ones behind the committed statistics, that the recorded output
``dm_dependence_sensitivity_output.md`` is what the script produces, that it does not count a
value within Monte Carlo error of 0.05 as significant, and that the script will not rebuild
the differentials from any other ``forecasts.csv`` or replace them with a file that does not
reproduce the committed statistics.

The default tests rerun sections 0-3 in full and one cell each of sections 4 and 5 (about
30-40 s). ``ATLAS_M8_FULL_STUDY=1`` adds a test that reruns the whole default configuration
(about four minutes on one core) and compares every line of the recorded output.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

from atlas.evaluation import _loss_differential
from atlas.stats import diebold_mariano
from tests.conftest import REPO_ROOT

SCRIPTS = REPO_ROOT / "docs" / "audit" / "scripts"
SCRIPT = SCRIPTS / "dm_dependence_sensitivity.py"
OUTPUT = SCRIPTS / "dm_dependence_sensitivity_output.md"
DIFFERENTIALS = SCRIPTS / "m2_loss_differentials.csv"
SIZE_STUDY_OUTPUT = SCRIPTS / "dm_size_study_output.md"
RESULTS = REPO_ROOT / "docs" / "results" / "atlas-m2-rolling-origin-v1"
RULE = "pre-registered rule (larger of HLN and Bartlett 60)"
FULL_STUDY = os.environ.get("ATLAS_M8_FULL_STUDY") == "1"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dm_dependence_sensitivity", SCRIPT)
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


def _json(path: Path) -> dict[str, object]:
    payload: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def _sections(path: Path) -> dict[str, list[str]]:
    """A recorded output split at its ``## `` headings: {title, or "header": lines}."""
    out: dict[str, list[str]] = {}
    title = "header"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            title = line[3:]
        out.setdefault(title, []).append(line)
    return out


def _section(sections: dict[str, list[str]], number: str) -> list[str]:
    return next(lines for title, lines in sections.items() if title.startswith(f"{number}. "))


def _rows(lines: list[str]) -> list[list[str]]:
    """The table rows of a section as stripped cells, header rows included."""
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in lines
        if line.startswith("| ")
    ]


#: Machine-dependent parts of the recorded output: the Python patch version, and the
#: floating-point gaps between two implementations (checked against the tolerance instead).
_VOLATILE = (
    (re.compile(r"Python \d+\.\d+\.\d+"), "Python <version>"),
    (re.compile(r"difference( of the pre-registered p-value)? \d\.\de[+-]\d\d"), "<gap>"),
)


def _stable(lines: list[str]) -> list[str]:
    for pattern, placeholder in _VOLATILE:
        lines = [pattern.sub(placeholder, line) for line in lines]
    return lines


def test_the_committed_differentials_reproduce_the_committed_statistics() -> None:
    script = _script()
    tests, gap = script.committed_differentials()
    assert gap <= 1e-9
    assert [(t.name, t.period, t.values.size) for t in tests] == [
        ("selection", "development", 486),
        ("H1", "confirmation", 336),
        ("H2", "confirmation", 336),
    ]
    # Independently of the script's own check: atlas.stats on the CSV against metrics.json.
    full = json.loads((RESULTS / "full" / "metrics.json").read_text(encoding="utf-8"))
    development = json.loads((RESULTS / "development" / "metrics.json").read_text("utf-8"))
    committed = {t["hypothesis"][:2]: t for t in full["recommendation"]["primary_tests"]}
    committed["selection"] = development["selection"]["regression_vs_best_baseline"]
    for name, (_, values) in script.read_differentials(DIFFERENTIALS).items():
        test = diebold_mariano(values, horizon=30)
        assert test.statistic == pytest.approx(committed[name]["dm_statistic"], abs=1e-9)
        assert test.p_value_conservative == pytest.approx(
            committed[name]["dm_p_value_conservative"], abs=1e-9
        )
    # The CSV names its provenance: the forecasts.csv hash of the committed full run.
    outputs = _json(RESULTS / "full" / "manifest.json")["outputs"]
    assert isinstance(outputs, dict)
    header = DIFFERENTIALS.read_text(encoding="utf-8").split("\ntest,origin,differential\n")[0]
    assert f"SHA-256 {outputs['forecasts.csv']}" in header


def _isolated(script: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Points the script at a copy of the committed CSV, so no test can change the original."""
    copy = tmp_path / "csv" / DIFFERENTIALS.name
    copy.parent.mkdir()
    copy.write_bytes(DIFFERENTIALS.read_bytes())
    monkeypatch.setattr(script, "DIFFERENTIALS", copy)
    return copy


def test_the_script_refuses_a_forecasts_file_with_another_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    copy = _isolated(script, tmp_path, monkeypatch)
    forged = tmp_path / "forecasts.csv"
    forged.write_text("record_type,period,model_id,origin,horizon\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="refusing to use it"):
        script.main(["--rebuild", str(forged), "--write"])
    assert copy.read_bytes() == DIFFERENTIALS.read_bytes()
    assert sorted(p.name for p in copy.parent.iterdir()) == [copy.name]


def _records() -> pd.DataFrame:
    """Forecasts of three models on three origins per period; one origin lacks a horizon."""
    errors = {"atlas_linear_regression": 10.0, "same_weekday_median_5w": -4.0}
    errors["seasonal_naive"] = 25.0
    rows = []
    for period, first in (("development", "2024-02-01"), ("confirmation", "2025-06-30")):
        for k, origin in enumerate(pd.date_range(first, periods=3).strftime("%Y-%m-%d")):
            for model_id, error in errors.items():
                for horizon in range(1, 32):  # horizon 31 is outside the tested 1-30
                    missing = k == 2 and horizon == 7 and model_id == "same_weekday_median_5w"
                    actual = 1000.0 + horizon
                    rows.append(
                        {
                            "period": period,
                            "model_id": model_id,
                            "origin": origin,
                            "target_date": origin,
                            "horizon": horizon,
                            "prediction": actual + error * (k + 1) + (horizon == 31) * 1e6,
                            "actual": actual,
                            "status": "failed" if missing else "ok",
                            "mase_scale": 1.0,
                        }
                    )
    return pd.DataFrame(rows)


def test_the_differentials_are_rebuilt_as_m2_builds_them(tmp_path: Path) -> None:
    script = _script()
    records = _records()
    series = script.differentials_from_records(records)
    # Origin k (1-based): |10 k| - |-4 k| = 6 k against B*, |10 k| - |25 k| = -15 k against
    # the seasonal naive. B* misses a horizon at the third origin, so the selection test and
    # H1 drop that origin and H2 keeps it.
    assert series["selection"].tolist() == pytest.approx([6.0, 12.0])
    assert series["H1"].tolist() == pytest.approx([6.0, 12.0])
    assert series["H2"].tolist() == pytest.approx([-15.0, -30.0, -45.0])
    assert series["H2"].index.tolist() == ["2025-06-30", "2025-07-01", "2025-07-02"]
    # The script's mirror agrees with M2's own function on the same records.
    for name, (period, model_id, reference) in script.TESTS.items():
        m2 = _loss_differential(records, model_id, reference, period, 1, 30)
        pd.testing.assert_series_equal(series[name], m2, check_exact=True)
    # The CSV round trip is lossless.
    path = tmp_path / "differentials.csv"
    path.write_text(script.differentials_csv(series, "0" * 64), encoding="utf-8")
    read = script.read_differentials(path)
    for name, values in series.items():
        assert read[name][0] == values.index.tolist()
        assert np.array_equal(read[name][1], values.to_numpy())


def test_the_rebuild_mode_compares_with_the_committed_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    forecasts = tmp_path / "forecasts.csv"
    _records().to_csv(forecasts, index=False)
    digest = script.sha256_file(forecasts)
    committed = tmp_path / "m2_loss_differentials.csv"
    monkeypatch.setattr(script, "expected_forecasts_sha256", lambda: digest)
    monkeypatch.setattr(script, "DIFFERENTIALS", committed)
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    text = script.differentials_csv(script.rebuild_differentials(forecasts), digest)
    committed.write_text(text, encoding="utf-8")
    assert script.main(["--rebuild", str(forecasts)]) == 0
    changed = text.replace("\nselection,2024-02-01,6\n", "\nselection,2024-02-01,7\n")
    assert changed != text
    committed.write_text(changed, encoding="utf-8")
    assert script.main(["--rebuild", str(forecasts)]) == 1


def test_the_write_mode_replaces_the_csv_only_with_one_that_reproduces_the_statistics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    copy = _isolated(script, tmp_path, monkeypatch)
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)  # the CSV path is shown relative to it
    forecasts = tmp_path / "forecasts.csv"
    _records().to_csv(forecasts, index=False)
    digest = script.sha256_file(forecasts)
    monkeypatch.setattr(script, "expected_forecasts_sha256", lambda: digest)
    # The hash matches, but three synthetic origins are not the committed grid.
    with pytest.raises(SystemExit, match="not the committed development grid"):
        script.main(["--rebuild", str(forecasts), "--write"])
    assert copy.read_bytes() == DIFFERENTIALS.read_bytes()
    assert sorted(p.name for p in copy.parent.iterdir()) == [copy.name]
    # A file that passes the check replaces the old one.
    checked: list[Path] = []
    monkeypatch.setattr(script, "committed_differentials", checked.append)
    assert script.main(["--rebuild", str(forecasts), "--write"]) == 0
    assert copy.read_text(encoding="utf-8") == script.differentials_csv(
        script.rebuild_differentials(forecasts), digest
    )
    assert checked[0] != copy
    assert sorted(p.name for p in copy.parent.iterdir()) == [copy.name]


def test_values_within_monte_carlo_error_of_the_level_are_shown_and_named() -> None:
    script = _script()
    study = script.Study(script.Config(), [], 0.0, script.RuleCheck(), (0, 0.0), {}, [])
    # Holm doubles the smaller p-value, so H2's Holm value has twice its standard error.
    values = {"selection": 0.0486, "H1": 0.1026, "H2": 0.0251}
    values["H1, Holm"], values["H2, Holm"] = script._holm_pair(values["H1"], values["H2"])
    assert values["H2, Holm"] == pytest.approx(0.0502)
    errors = script._errors(study, values)
    assert errors["H2"] == pytest.approx(np.sqrt(0.0251 * 0.9749 / 20_000))
    assert errors["H2, Holm"] == pytest.approx(2 * errors["H2"])
    assert errors["H1, Holm"] == pytest.approx(errors["H1"])
    # 0.0502 is not below 0.05 but within two standard errors (0.0044) of it.
    assert script._p(values["H2, Holm"], errors["H2, Holm"]) == "0.05020"
    assert script._p(values["selection"], errors["selection"]) == "0.04860"
    assert script._p(0.0448, 2 * np.sqrt(0.0224 * 0.9776 / 20_000)) == "0.045"
    assert script._count([0.0448, 0.0502], [0.0021, 0.0022], " 0.05") == (
        "1 below 0.05; 0.05020 within 2 MC SE of 0.05"
    )
    # When the larger p-value exceeds the doubled smaller one, Holm keeps it, and its error;
    # otherwise both Holm values are the doubled smaller one (90-day blocks: 0.1549 twice).
    kept = {"selection": 0.2, "H1": 0.08, "H2": 0.01, "H1, Holm": 0.08, "H2, Holm": 0.02}
    assert script._errors(study, kept)["H1, Holm"] == pytest.approx(np.sqrt(0.08 * 0.92 / 20_000))
    doubled = {"selection": 0.0833, "H1": 0.1413, "H2": 0.07745}
    doubled["H1, Holm"], doubled["H2, Holm"] = script._holm_pair(0.1413, 0.07745)
    assert doubled["H1, Holm"] == doubled["H2, Holm"] == pytest.approx(0.1549)
    assert script._errors(study, doubled)["H1, Holm"] == pytest.approx(
        2 * np.sqrt(0.07745 * 0.92255 / 20_000)
    )


def test_the_recorded_summary_does_not_count_a_boundary_value_as_significant() -> None:
    # Under the 30-day block bootstrap H2's Holm-adjusted p-value is 0.0502: not below 0.05,
    # and within Monte Carlo error of it. The report must say so wherever it is counted.
    sections = _sections(OUTPUT)
    rows = _rows(_section(sections, "2"))
    blocks30 = next(r for r in rows if r[0] == "block bootstrap, 30 days")
    assert blocks30[5] == "0.05020"
    summary = "\n".join(sections["What this shows"])
    h2 = next(line for line in summary.splitlines() if line.startswith("- H2:"))
    assert "0.050 under" not in h2
    assert "0.05020 under the block bootstrap with blocks of 30 days" in h2
    assert "not clearly under every such null" in h2
    assert "within 2 MC SE of 0.05 under the block bootstrap with blocks of 30 days" in h2
    assert "## Limitations" in OUTPUT.read_text(encoding="utf-8")


def test_the_erratum_sentence_needs_the_default_configuration() -> None:
    script = _script()
    config = script.Config(replications=1_000)
    study = script.run_nulls(config, *script.committed_differentials())
    first = next(line for line in script.summary_lines(study) if line.startswith("- Under"))
    assert "these are the erratum's values" not in first.lower()
    assert "come from the default configuration (20,000 replications, seed 20261016)" in first


def test_the_recorded_output_is_what_the_script_produces() -> None:
    """Sections 0-3, 'What this shows' on them and 'Limitations' are rerun in full.

    Sections 4 and 5 take minutes; one cell of each is rerun here (336 origins with blocks of
    30 days, and with taper 120). The other cells of their tables are bound only by the
    opt-in full rerun below, and the bullets on them must quote numbers of the recorded tables.
    """
    script = _script()
    recorded = _sections(OUTPUT)
    study = script.run_nulls(script.Config(), *script.committed_differentials())
    for title, lines in (
        ("header", script.header_lines(study)),
        ("0", script.inputs_lines(study)),
        ("1", script.acf_lines(study)),
        ("2", script.nulls_lines(study)),
        ("3", script.size_lines(study)),
        ("Limitations", script.limitations_lines(study)),
    ):
        expected = "\n".join(lines).split("\n")  # a table is one multi-line string
        section = recorded[title] if title in recorded else _section(recorded, title)
        tail = [] if title == "Limitations" else [""]  # the last section ends the file
        assert _stable(section) == [*_stable(expected), *tail], title
    for gap in re.findall(r"\d\.\de[+-]\d\d", "\n".join(_section(recorded, "0"))):
        assert float(gap) <= 1e-9
    exact = {t.name: study.settings[0].corrected(t) for t in study.tests}
    # Section 4: one cell rerun in full (336 origins, blocks of 30 days).
    calibration = script.calibrate(study, 336, 30)
    rows = _rows(_section(recorded, "4"))
    assert next(r for r in rows if r[0] == "T = 336")[1] == script.calibration_size_cell(
        calibration
    )
    observed = script.observed_p(study, "block bootstrap")
    for name in ("H1", "H2"):
        row = script.calibration_row(
            study.test(name), "30 days", calibration[name], exact[name], observed[(name, 30)]
        )
        assert row in rows, name
    # Section 5: one cell rerun in full (336 origins, taper 120).
    gaussian = script.calibrate_gaussian(study, 336, 120)
    rows = _rows(_section(recorded, "5"))
    assert next(r for r in rows if r[0] == "T = 336")[4] == script.calibration_size_cell(gaussian)
    observed = script.observed_p(study, "Gaussian")
    for name in ("H1", "H2"):
        row = script.calibration_row(
            study.test(name),
            "taper 120",
            gaussian[name],
            exact[name],
            observed[(name, 120)],
            below=True,
        )
        assert row in rows, name
    # 'What this shows': the bullets on sections 0-3 rerun in full; the bullets on sections 4
    # and 5 quote only numbers of the recorded tables (apart from differences and bounds).
    summary = recorded["What this shows"]
    expected = "\n".join(script.summary_lines(study)).split("\n")
    assert summary[: len(expected)] == expected
    later = [line for line in summary[len(expected) :] if line]
    assert len(later) >= 2, "the bullets on sections 4 and 5 are missing"
    tables = "\n".join(
        line
        for title, lines in recorded.items()
        if title not in ("What this shows", "Limitations")
        for line in lines
    )
    for line in later:
        for number in re.findall(r"(?<![\d.])\d+\.\d+", re.sub(r"at most \d+\.\d+", "", line)):
            assert number in tables, number


@pytest.mark.skipif(not FULL_STUDY, reason="opt-in: ATLAS_M8_FULL_STUDY=1 (about four minutes)")
def test_the_full_default_run_reproduces_the_recorded_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _script()
    assert script.main([]) == 0
    produced = capsys.readouterr().out.splitlines()
    recorded = OUTPUT.read_text(encoding="utf-8").splitlines()
    assert _stable(produced) == _stable(recorded)


def test_the_ma29_null_reproduces_the_erratum() -> None:
    # The MA(29) null of the dependence study uses the size study's draws, so its
    # size-corrected p-values are those the erratum quotes from the size study.
    sizes = _sections(SIZE_STUDY_OUTPUT)
    corrected = {r[0]: r[5].split(" ")[0] for r in _rows(_section(sizes, "3")) if r[2] == RULE}
    holm = {r[0]: r[4] for r in _rows(_section(sizes, "4")) if r[1] == "gaussian"}
    rows = _rows(_section(_sections(OUTPUT), "2"))
    ma29 = next(r for r in rows if r[0].startswith("MA(29)"))
    assert [cell.split(" ")[0] for cell in ma29[1:4]] == [
        corrected["selection"],
        corrected["H1"],
        corrected["H2"],
    ]
    assert (ma29[4], ma29[5]) == (holm["H1"], holm["H2"])


def test_backtesting_quotes_the_recorded_dependence_study() -> None:
    # The erratum (BACKTESTING.md section 12) quotes the study's section 2: bind its figures
    # to the recorded output, as test_m8_dm_size.py does for the size study.
    doc = " ".join((REPO_ROOT / "docs" / "BACKTESTING.md").read_text(encoding="utf-8").split())
    erratum = doc.split("## 12. M8 erratum", 1)[1]
    rows = {r[0]: r for r in _rows(_section(_sections(OUTPUT), "2"))[1:]}

    def value(null: str, column: int) -> float:
        return float(rows[null][column].split(" ")[0])

    def span(values: list[float]) -> str:
        return f"{min(values):.3f}-{max(values):.3f}"

    blocks = [f"block bootstrap, {days} days" for days in (30, 45, 60, 90)]
    gaussian = [f"Gaussian, Bartlett taper {taper}" for taper in (29, 60, 90, 120)]
    longer = blocks[1:] + gaussian[1:]  # dependence of 45 days or more
    selection, h1, h2_holm = 1, 2, 5
    assert f"{value(gaussian[0], h2_holm):.3f}" == "0.036"
    assert "is 0.036 (Gaussian, taper 29) and 0.050 (30-day blocks, within Monte Carlo" in erratum
    assert rows[blocks[0]][h2_holm] == "0.05020"  # quoted as 0.050, at the threshold
    assert f"Holm-adjusted p-value is {span([value(n, h2_holm) for n in longer])}" in erratum
    assert f"any of the eight ({span([value(n, h1) for n in blocks + gaussian])})" in erratum
    assert f"under every block bootstrap ({span([value(n, selection) for n in blocks])})" in erratum
    assert f"the Gaussian null with taper 29 ({value(gaussian[0], selection):.3f})" in erratum
    assert f"tapers 60-120 ({span([value(n, selection) for n in gaussian[1:]])})" in erratum
