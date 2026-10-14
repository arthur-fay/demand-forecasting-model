"""Cross-environment comparison of recomputed forecasts with committed references.

Regression tests for the CI failure of arthur-fay/demand-forecasting-model#4: the M2
spot check was recomputed on a runner without AVX-512, where OpenBLAS chose AVX2 kernels
and the regression predictions differed from the committed ones by up to 7 units in the
last place. The comparison must accept that noise, keep exact equality where no BLAS is
involved, and still reject any materially wrong forecast.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from tests.conftest import REPO_ROOT
from tests.numeric import CROSS_ENVIRONMENT_RTOL, assert_matches_reference

# Values reported by the failing CI job (AVX2 kernels) and the committed reference (AVX-512
# kernels) for atlas_linear_regression at origin 2024-02-01, horizons 1-3, and a horizon
# with the largest absolute difference.
CI_AVX2 = [9055.90780119958, 7575.016320019791, 7327.7113907227895, 7640.102510808242]
REFERENCE = [9055.907801199583, 7575.016320019793, 7327.711390722795, 7640.102510808253]
SPOT_CHECK = (
    REPO_ROOT / "docs" / "results" / "atlas-m2-rolling-origin-v1" / "full" / "spot_check.csv"
)


def test_observed_kernel_noise_is_accepted_for_blas_models() -> None:
    assert CI_AVX2 != REFERENCE  # the noise is real: exact equality fails
    assert_matches_reference(CI_AVX2, REFERENCE, uses_blas=True)
    relative = np.abs(np.array(CI_AVX2) - np.array(REFERENCE)) / np.array(REFERENCE)
    assert relative.max() < CROSS_ENVIRONMENT_RTOL / 1e5  # wide margin below the tolerance


def test_models_without_blas_must_match_exactly() -> None:
    with pytest.raises(AssertionError, match="exact"):
        assert_matches_reference(CI_AVX2, REFERENCE, uses_blas=False)
    one_ulp = np.nextafter(1067.0, 2000.0)
    with pytest.raises(AssertionError):
        assert_matches_reference([one_ulp], [1067.0], uses_blas=False)
    assert_matches_reference([1067.0, 980.5], [1067.0, 980.5], uses_blas=False)


@pytest.mark.parametrize(
    "error",
    [
        0.1,  # a tenth of a request per day
        1.0,  # one request
        REFERENCE[0] * 1e-8,  # ten times the tolerance, still far below one request
        -500.0,
    ],
)
def test_materially_wrong_forecasts_are_rejected(error: float) -> None:
    wrong = list(REFERENCE)
    wrong[2] += error
    with pytest.raises(AssertionError, match="cross-environment"):
        assert_matches_reference(wrong, REFERENCE, uses_blas=True)


def test_structure_must_match_exactly() -> None:
    with pytest.raises(AssertionError, match="shape"):
        assert_matches_reference(REFERENCE[:3], REFERENCE, uses_blas=True)
    with pytest.raises(AssertionError, match="missing"):
        assert_matches_reference([np.nan, *REFERENCE[1:]], REFERENCE, uses_blas=True)
    with pytest.raises(AssertionError):
        assert_matches_reference(REFERENCE[::-1], REFERENCE, uses_blas=True)  # wrong order
    assert_matches_reference([np.nan, 2.0], [np.nan, 2.0], uses_blas=True)


def _openblas_dispatch_available() -> bool:
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        return False
    config = np.show_config(mode="dicts")
    blas = config.get("Build Dependencies", {}).get("blas", {}) if isinstance(config, dict) else {}
    return "openblas" in str(blas.get("name", "")).lower()


_SCRIPT = """
import json
from datetime import date
from pathlib import Path
from atlas.backtest import BacktestPlan, Period, PeriodRole, run_backtest
from atlas.config import load_settings
from atlas.runner import load_series
from atlas.uncertainty_runner import load_context, point_models, residual_plan

root = Path({root!r})
settings = load_settings(repo_root=root)
context = load_context(settings, root / "configs/uncertainty/atlas-m3-intervals-v1.json")
_, data = load_series(settings, "cross_environment")
origins = [date(2024, 2, 1), date(2026, 5, 31)]
template = residual_plan(context)
plan = BacktestPlan(
    tuple(Period(f"o{{k}}", PeriodRole.DESCRIPTIVE, o, o, 1, True) for k, o in enumerate(origins)),
    max_horizon=template.max_horizon,
    min_history_days=template.min_history_days,
    training_window=template.training_window,
    data_delay_days=template.data_delay_days,
)
records = run_backtest(data.series, plan, point_models(context)).records
records = records.sort_values(["model_id", "origin", "horizon"])
print(json.dumps({{
    "model_id": records["model_id"].tolist(),
    "origin": [str(o.date()) for o in records["origin"]],
    "horizon": records["horizon"].tolist(),
    "prediction": records["prediction"].tolist(),
}}))
"""


@pytest.mark.integration
@pytest.mark.skipif(not _openblas_dispatch_available(), reason="needs x86-64 OpenBLAS dispatch")
def test_spot_check_holds_under_another_blas_kernel_family() -> None:
    # Prescott (SSE3) kernels run on every x86-64 CPU and differ from the AVX-512 kernels
    # that produced the reference, so this reproduces a different-CPU recomputation here.
    env = {**os.environ, "OPENBLAS_CORETYPE": "Prescott"}
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and script, no shell
        [sys.executable, "-c", _SCRIPT.format(root=str(REPO_ROOT))],
        capture_output=True,
        text=True,
        env=env,
        check=True,
        timeout=600,
    )
    got = pd.DataFrame(json.loads(completed.stdout.strip().splitlines()[-1]))
    spot = pd.read_csv(SPOT_CHECK, float_precision="round_trip")
    spot = spot.drop_duplicates(["model_id", "origin", "horizon"])
    merged = got.merge(spot, on=["model_id", "origin", "horizon"], suffixes=("", "_reference"))
    assert len(merged) == len(got) == 2 * 2 * 30
    for model_id, rows in merged.groupby("model_id"):
        assert_matches_reference(
            rows["prediction"],
            rows["prediction_reference"],
            uses_blas=model_id == "atlas_linear_regression",
            what=str(model_id),
        )
    regression = merged[merged["model_id"] == "atlas_linear_regression"]
    relative = (regression["prediction"] - regression["prediction_reference"]).abs() / regression[
        "prediction_reference"
    ]
    assert relative.max() < CROSS_ENVIRONMENT_RTOL / 1e5
