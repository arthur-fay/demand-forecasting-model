"""Dependence sensitivity of the size-corrected M2 tests (M8 erratum, H2 robustness).

``docs/audit/scripts/dm_size_study.py`` size-corrects the pre-registered HLN Diebold-Mariano
rule under the test's own MA(29) null. ``docs/audit/scripts/dm_dependence_sensitivity.py``
repeats the correction under nulls calibrated to the observed loss differentials, which it
reads from the committed ``m2_loss_differentials.csv`` (rebuilt from a regenerated M2 run whose
``forecasts.csv`` has the hash in the run manifest). These tests pin that the committed
differentials are the ones behind the committed statistics, that the recorded output
``dm_dependence_sensitivity_output.md`` is what the script produces, and that the script will
not rebuild the differentials from any other ``forecasts.csv``.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

from atlas.stats import diebold_mariano
from tests.conftest import REPO_ROOT

SCRIPTS = REPO_ROOT / "docs" / "audit" / "scripts"
SCRIPT = SCRIPTS / "dm_dependence_sensitivity.py"
OUTPUT = SCRIPTS / "dm_dependence_sensitivity_output.md"
DIFFERENTIALS = SCRIPTS / "m2_loss_differentials.csv"
SIZE_STUDY_OUTPUT = SCRIPTS / "dm_size_study_output.md"
RESULTS = REPO_ROOT / "docs" / "results" / "atlas-m2-rolling-origin-v1"
RULE = "pre-registered rule (larger of HLN and Bartlett 60)"


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


def test_the_script_refuses_a_forecasts_file_with_another_hash(tmp_path: Path) -> None:
    script = _script()
    forged = tmp_path / "forecasts.csv"
    forged.write_text("record_type,period,model_id,origin,horizon\n", encoding="utf-8")
    before = DIFFERENTIALS.read_bytes()
    with pytest.raises(SystemExit, match="refusing to use it"):
        script.main(["--rebuild", str(forged), "--write"])
    assert DIFFERENTIALS.read_bytes() == before


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
    series = script.differentials_from_records(_records())
    # Origin k (1-based): |10 k| - |-4 k| = 6 k against B*, |10 k| - |25 k| = -15 k against
    # the seasonal naive. B* misses a horizon at the third origin, so the selection test and
    # H1 drop that origin and H2 keeps it.
    assert series["selection"].tolist() == pytest.approx([6.0, 12.0])
    assert series["H1"].tolist() == pytest.approx([6.0, 12.0])
    assert series["H2"].tolist() == pytest.approx([-15.0, -30.0, -45.0])
    assert series["H2"].index.tolist() == ["2025-06-30", "2025-07-01", "2025-07-02"]
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


def test_the_recorded_output_is_what_the_script_produces() -> None:
    script = _script()
    recorded = _sections(OUTPUT)
    study = script.run_nulls(script.Config(), *script.committed_differentials())
    # The header, the inputs and sections 1-3, rerun in full with the default configuration.
    for title, lines in (
        ("header", script.header_lines(study)),
        ("0", script.inputs_lines(study)),
        ("1", script.acf_lines(study)),
        ("2", script.nulls_lines(study)),
        ("3", script.size_lines(study)),
    ):
        expected = "\n".join(lines).split("\n")  # a table is one multi-line string
        section = recorded[title] if title == "header" else _section(recorded, title)
        assert _stable(section) == [*_stable(expected), ""], title
    for gap in re.findall(r"\d\.\de[+-]\d\d", "\n".join(_section(recorded, "0"))):
        assert float(gap) <= 1e-9
    # Section 4: one cell rerun in full (336 origins, blocks of 30 days).
    calibration = script.calibrate(study, 336, 30)
    rows = _rows(_section(recorded, "4"))
    assert next(r for r in rows if r[0] == "T = 336")[1] == script.calibration_size_cell(
        calibration
    )
    exact = {t.name: study.settings[0].corrected(t) for t in study.tests}
    observed = script.observed_bootstrap_p(study)
    for name in ("H1", "H2"):
        row = script.calibration_row(
            study.test(name), 30, calibration[name], exact[name], observed[(name, 30)]
        )
        assert row in rows, name
    # 'What this shows': the bullets on sections 0-3 rerun in full; the bullets on section 4
    # quote only numbers of the recorded tables (apart from a difference of two of them).
    summary = recorded["What this shows"]
    expected = "\n".join(script.summary_lines(study)).split("\n")
    assert summary[: len(expected)] == expected
    later = [line for line in summary[len(expected) :] if line]
    assert later, "the bullets on section 4 are missing"
    tables = "\n".join(
        line for title, lines in recorded.items() if title != "What this shows" for line in lines
    )
    for line in later:
        for number in re.findall(r"(?<![\d.])\d+\.\d+", re.sub(r"at most \d+\.\d+", "", line)):
            assert number in tables, number


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
