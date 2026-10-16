"""Dependence sensitivity of the size-corrected M2 tests (M8 erratum, H2 robustness).

``dm_size_study.py`` size-corrects the pre-registered HLN Diebold-Mariano rule of M2 under the
test's own maintained null hypothesis: the per-origin loss differential is the mean of 30
consecutive independent daily shocks, an exact MA(29). If the real differential stays
positively autocorrelated beyond lag 29, that correction understates the over-rejection and
its size-corrected p-values are too small. This script asks how far the conclusions of the
erratum (selection p 0.069, H1 0.095, H2 0.022 and 0.045 after Holm) depend on that
assumption.

Inputs. ``m2_loss_differentials.csv`` (next to this script) holds the per-origin mean, over
horizons 1-30, of ``|error of the ATLAS regression| - |error of the reference|`` for the three
tests, computed as M2 computes them: the selection test (development period, 486 origins,
reference ``same_weekday_median_5w``), H1 (confirmation period, 336 origins, the same
reference) and H2 (confirmation period, 336 origins, reference ``seasonal_naive``). The script
first checks that ``atlas.stats.diebold_mariano`` and ``bandwidth_sensitivity`` on these series
reproduce the committed statistics in ``docs/results/atlas-m2-rolling-origin-v1/{development,
full}/metrics.json`` within 1e-9. The CSV was rebuilt from a regenerated full M2 run whose
``forecasts.csv`` has the SHA-256 recorded in the run manifest (``--rebuild``, below).

Analyses. Every null hypothesis below has mean zero. A size-corrected p-value is the share of
simulated null series whose pre-registered p-value (``p_value_conservative``, the larger of the
HLN and Bartlett 60 p-values) is at most the committed one, as in ``dm_size_study.py``.

0. The inputs and their checks, with the skewness and excess kurtosis of each differential
   (the block bootstrap keeps its marginal distribution; the other nulls are symmetric).
1. The sample autocorrelations of each differential at lags 29, 30, 45, 60, 90 and 120, and a
   check of whether its dependence beyond lag 29 is consistent with the MA(29) null. The
   statistic is the mean sample autocorrelation over lags 30-120: the lags MA(29) sets to
   zero, up to the widest bandwidth M2 reports. Its null distribution is simulated with the
   same number of origins, so it includes the downward bias of a demeaned sample
   autocorrelation at long lags. The MA(29) draws are those of ``dm_size_study.py`` (same
   seed, streams and order), so the MA(29) row of section 2 reproduces the erratum's
   size-corrected p-values.
2. Size-corrected p-values under nulls calibrated to each observed differential: a null-imposed
   circular block bootstrap (the demeaned series resampled in circular blocks of 30, 45, 60
   and 90 days), which keeps the series' own dependence within a block and its marginal
   distribution; and a Gaussian null whose autocovariance is the series' own sample
   autocovariance with a Bartlett taper at 29, 60, 90 and 120 lags. The MA(29) null is
   repeated for comparison. Holm adjustment of H1 and H2 in every setting
   (``atlas.stats.holm``).
3. The empirical size of the nominal rule (``p_value_conservative < 0.05``) under each null.
4. A calibration of the block bootstrap itself when MA(29) is true: MA(29) series with 486 and
   336 origins, each tested with its own null-imposed block bootstrap. Reported: how often the
   bootstrap rejects at 5 % (its size), and, for MA(29) series whose pre-registered p-value
   equals the committed one, the distribution of their bootstrap p-values next to the exact
   MA(29) size-corrected p-value. A null-imposed bootstrap resamples the demeaned series, so
   the resamples of a series do not depend on its mean, and the bootstrap p-value of a series
   shifted to the committed statistic needs no further draws.

The report ends with "What this shows": statements generated from the numbers above.

The simulations use a vectorised version of the pre-registered rule (autocovariances up to lag
60, the rectangular 29 variance with its Bartlett fallback, the HLN factor and ``t(T - 1)``
p-values from SciPy). The script checks it against ``atlas.stats.diebold_mariano`` within 1e-9
on the observed series and on the first draws of every simulated setting, and stops if they
disagree. The results are deterministic for a given seed and configuration: every setting
draws from its own ``numpy.random.default_rng`` stream. Monte Carlo standard errors are
binomial: ``sqrt(p (1 - p) / N)``.

Limitations. The analyses are post hoc: the nulls and the long-lag statistic were chosen after
the M2 results and a first look at the autocorrelations were known, and they do not replace
the pre-registered decision. A demeaned sample autocovariance is biased downward at long lags,
and with 336-486 origins its long-lag values are noisy (section 1 gives their spread under
MA(29)); a Gaussian null built from it takes that noise and bias as the truth, and the Bartlett
taper shrinks it further, so such a null has less dependence near its taper lag than the sample
and none beyond it. A circular block bootstrap keeps dependence only within a block, weakened by
the joins, and a resample of 336 origins holds only four 90-day blocks; a bootstrap p-value
from one series of 336-486 overlapping observations is itself noisy (section 4 measures how
noisy). None of the nulls models level shifts or other non-stationarity.

Nothing here changes a committed result: the script reads the committed metrics and the
committed differentials and writes only to standard output (and, with ``--rebuild --write``,
the differentials CSV).

Usage, from the repository root (about three minutes on one core with the defaults)::

    uv run python docs/audit/scripts/dm_dependence_sensitivity.py \\
        > docs/audit/scripts/dm_dependence_sensitivity_output.md

Options: ``--replications N`` per null setting (default 20000), ``--calibration-series M``
(default 1000) and ``--calibration-draws B`` (default 1000) for section 4, ``--seed S``
(default 20261016). ``--rebuild FORECASTS_CSV`` rebuilds the differentials from the
``forecasts.csv`` of a full M2 run (``uv run atlas backtest run --period development --period
confirmation --period legacy_window --sealed-selection
docs/results/atlas-m2-rolling-origin-v1/development/selection.json --output-dir DIR``, about
six CPU-minutes), refuses a file whose SHA-256 differs from the manifest's, and checks that the
result equals the committed CSV; ``--write`` writes it instead.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, NoReturn

import numpy as np
import pandas as pd
import scipy
from scipy import stats as scipy_stats

from atlas.metrics import contiguous, paired, per_origin_mean
from atlas.stats import bandwidth_sensitivity, diebold_mariano, holm

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
RESULTS: Final = REPO_ROOT / "docs" / "results" / "atlas-m2-rolling-origin-v1"
MANIFEST: Final = RESULTS / "full" / "manifest.json"
DIFFERENTIALS: Final = Path(__file__).resolve().parent / "m2_loss_differentials.csv"
H: Final = 30
NOMINAL: Final = 0.05
TOLERANCE: Final = 1e-9
FLOAT_FORMAT: Final = "%.17g"  # lossless, as atlas.experiment writes the run outputs
#: Test name: (period, model, reference), as the committed bandwidth sensitivity lists them.
TESTS: Final = {
    "selection": ("development", "atlas_linear_regression", "same_weekday_median_5w"),
    "H1": ("confirmation", "atlas_linear_regression", "same_weekday_median_5w"),
    "H2": ("confirmation", "atlas_linear_regression", "seasonal_naive"),
}
#: How 'What this shows' names the tests in running text.
PROSE: Final = {"selection": "the selection", "H1": "H1", "H2": "H2"}
ACF_LAGS: Final = (29, 30, 45, 60, 90, 120)
LONG_LAGS: Final = (30, 120)  # first and last lag of the long-lag statistic
BLOCKS: Final = (30, 45, 60, 90)
TAPERS: Final = (29, 60, 90, 120)
#: A null "with dependence beyond about a month": blocks or tapers from this many days.
LONG_DEPENDENCE: Final = 45
CHUNK: Final = 1000  # rows per vectorised block; keeps the arrays small
CHECK_ROWS: Final = 3  # draws per simulated setting checked against atlas.stats


@dataclass(frozen=True)
class Config:
    replications: int = 20_000
    calibration_series: int = 1_000
    calibration_draws: int = 1_000
    seed: int = 20261016


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"dm_dependence_sensitivity: {message}")


# --- The committed differentials ------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_forecasts_sha256() -> str:
    manifest: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return str(manifest["outputs"]["forecasts.csv"])


def differentials_from_records(records: pd.DataFrame) -> dict[str, pd.Series]:
    """Per-origin loss differentials of the three tests, as ``atlas.evaluation`` builds them.

    The mean over horizons 1-30 of ``|e_model| - |e_reference|`` on the forecasts both models
    got ``ok``, for origins with all 30 horizons (``atlas.evaluation._loss_differential``).
    """
    out: dict[str, pd.Series] = {}
    for name, (period, model_id, reference) in TESTS.items():
        scope = records[(records["period"] == period) & records["horizon"].between(1, H)]
        cell = paired(scope, model_id, reference)
        loss = (cell["prediction"] - cell["actual"]).abs() - (
            cell["prediction_ref"] - cell["actual"]
        ).abs()
        out[name] = per_origin_mean(cell, loss, H)
    return out


def rebuild_differentials(forecasts: Path) -> dict[str, pd.Series]:
    """The differentials from a regenerated ``forecasts.csv``; refuses any other file."""
    expected = expected_forecasts_sha256()
    actual = sha256_file(forecasts)
    if actual != expected:
        _fail(
            f"{forecasts} has SHA-256 {actual}, not the {expected} that "
            f"{MANIFEST.relative_to(REPO_ROOT)} records for forecasts.csv; refusing to use it"
        )
    columns = ["period", "model_id", "origin", "target_date", "horizon"]
    columns += ["prediction", "actual", "status", "mase_scale"]
    records = pd.read_csv(forecasts, float_precision="round_trip", usecols=columns)
    return differentials_from_records(records)


def differentials_csv(series: dict[str, pd.Series], forecasts_sha256: str) -> str:
    """The committed CSV: a provenance header, then ``test,origin,differential`` rows."""
    lines = [
        "# Per-origin loss differentials of the M2 selection test, H1 and H2",
        "# (atlas-m2-rolling-origin-v1): the mean over horizons 1-30 of |error of the ATLAS",
        "# regression| - |error of the reference|, in requests per day. selection: development",
        "# period vs same_weekday_median_5w; H1: confirmation period vs same_weekday_median_5w;",
        "# H2: confirmation period vs seasonal_naive. Rebuilt by",
        "# docs/audit/scripts/dm_dependence_sensitivity.py --rebuild from a regenerated full",
        f"# run's forecasts.csv, SHA-256 {forecasts_sha256}",
        "# (the hash in docs/results/atlas-m2-rolling-origin-v1/full/manifest.json).",
        "test,origin,differential",
    ]
    for name in TESTS:
        for origin, value in series[name].items():
            lines.append(f"{name},{origin},{FLOAT_FORMAT % float(value)}")
    return "\n".join(lines) + "\n"


def read_differentials(path: Path = DIFFERENTIALS) -> dict[str, tuple[list[str], np.ndarray]]:
    """``{test: (origins, differentials)}`` from the committed CSV."""
    rows = [
        line.split(",")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    if not rows or rows[0] != ["test", "origin", "differential"]:
        _fail(f"{path.name}: unexpected header")
    out: dict[str, tuple[list[str], np.ndarray]] = {}
    for name in TESTS:
        selected = [row for row in rows[1:] if row[0] == name]
        out[name] = ([row[1] for row in selected], np.array([float(row[2]) for row in selected]))
    if sum(len(origins) for origins, _ in out.values()) != len(rows) - 1:
        _fail(f"{path.name}: rows of an unknown test")
    return out


@dataclass(frozen=True)
class Differentials:
    """One test's committed differential and the committed numbers it reproduces."""

    name: str
    period: str
    origins: tuple[str, ...]
    values: np.ndarray
    rule_p: float
    statistic: float
    holm_p: float | None


def _read(path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def _close(mine: float | None, committed: float | None, label: str, gaps: list[float]) -> None:
    if (
        mine is None
        or committed is None
        or not math.isclose(mine, committed, rel_tol=0.0, abs_tol=TOLERANCE)
    ):
        _fail(f"{label}: {mine} does not reproduce the committed {committed} within {TOLERANCE:g}")
    gaps.append(abs(mine - committed))


def committed_differentials(path: Path = DIFFERENTIALS) -> tuple[list[Differentials], float]:
    """The committed series, checked against the committed metrics (largest gap returned)."""
    development = _read(RESULTS / "development" / "metrics.json")
    full = _read(RESULTS / "full" / "metrics.json")
    primary = {t["hypothesis"][:2]: t for t in full["recommendation"]["primary_tests"]}
    sources = {
        "selection": development["selection"]["regression_vs_best_baseline"],
        "H1": primary["H1"],
        "H2": primary["H2"],
    }
    sensitivity = {row["test"].split(":")[0]: row for row in full["bandwidth_sensitivity"]}
    periods = {p["name"]: p for p in full["periods"]}
    gaps: list[float] = []
    tests: list[Differentials] = []
    for name, (origins, values) in read_differentials(path).items():
        period = TESTS[name][0]
        source, row = sources[name], sensitivity[name]
        for record in (source, row):
            if (record["period"], record["model_id"], record["reference"]) != TESTS[name]:
                _fail(f"{name}: the committed metrics name another comparison")
        grid = periods[period]
        if (
            not contiguous(pd.DatetimeIndex(origins), grid["origin_stride_days"])
            or (origins[0], origins[-1]) != (grid["first_origin"], grid["last_origin"])
            or len(origins) != grid["n_origins"]
            or len(origins) != source["dm_n_origins"]
        ):
            _fail(f"{name}: the origins are not the committed {period} grid")
        test = diebold_mariano(values, horizon=H)
        _close(test.statistic, source["dm_statistic"], f"{name} statistic", gaps)
        _close(test.p_value, source["dm_p_value"], f"{name} HLN p", gaps)
        _close(test.bartlett_p_value, source["dm_bartlett_p_value"], f"{name} Bartlett p", gaps)
        _close(
            test.p_value_conservative,
            source["dm_p_value_conservative"],
            f"{name} conservative p",
            gaps,
        )
        _close(test.mean_difference, row["mean_difference"], f"{name} mean", gaps)
        variants = bandwidth_sensitivity(values, horizon=H)
        for mine, committed in zip(variants, row["variants"], strict=True):
            label = f"{name} {committed['kernel']} {committed['bandwidth']}"
            if (mine["kernel"], mine["bandwidth"]) != (committed["kernel"], committed["bandwidth"]):
                _fail(f"{label}: unexpected bandwidth")
            _close(mine["p_value"], committed["p_value"], f"{label} p", gaps)
        tests.append(
            Differentials(
                name=name,
                period=period,
                origins=tuple(origins),
                values=values,
                rule_p=float(source["dm_p_value_conservative"]),
                statistic=float(source["dm_statistic"]),
                holm_p=source.get("holm_adjusted_p_value"),
            )
        )
    return tests, max(gaps)


# --- The pre-registered rule, vectorised ----------------------------------------------------


def autocovariances(rows: np.ndarray, max_lag: int) -> np.ndarray:
    """Sample autocovariances (demeaned, divided by T) of each row, lags 0..max_lag."""
    centered = rows - rows.mean(axis=1, keepdims=True)
    size = centered.shape[1]
    out = np.empty((centered.shape[0], max_lag + 1))
    for lag in range(max_lag + 1):
        out[:, lag] = np.einsum("ij,ij->i", centered[:, lag:], centered[:, : size - lag]) / size
    return out


def _long_run_variance(gamma: np.ndarray, bandwidth: int, kernel: str) -> np.ndarray:
    lags = np.arange(1, bandwidth + 1)
    weights = np.ones(bandwidth) if kernel == "rectangular" else 1.0 - lags / (bandwidth + 1)
    result: np.ndarray = gamma[:, 0] + 2.0 * gamma[:, 1 : bandwidth + 1] @ weights
    return result


def rule_p_values(rows: np.ndarray) -> np.ndarray:
    """``p_value_conservative`` of ``atlas.stats.diebold_mariano(row, horizon=30)`` per row."""
    size = rows.shape[1]
    gamma = autocovariances(rows, 2 * H)
    mean = rows.mean(axis=1)
    correction = (size + 1 - 2 * H + H * (H - 1) / size) / size
    primary = _long_run_variance(gamma, H - 1, "rectangular")
    primary = np.where(primary > 0, primary, _long_run_variance(gamma, H - 1, "bartlett"))
    bartlett = _long_run_variance(gamma, 2 * H, "bartlett")
    p_values = [
        2.0 * scipy_stats.t.sf(np.abs(mean / np.sqrt(variance / size / correction)), size - 1)
        for variance in (primary, bartlett)
    ]
    result: np.ndarray = np.maximum(p_values[0], p_values[1])
    return result


class RuleCheck:
    """Compares the vectorised rule with ``atlas.stats.diebold_mariano`` on chosen rows."""

    def __init__(self) -> None:
        self.count = 0
        self.largest = 0.0

    def __call__(self, rows: np.ndarray, p_values: np.ndarray, label: str) -> None:
        for row, p in zip(rows, p_values, strict=True):
            test = diebold_mariano(row, horizon=H)
            if not test.computed or test.p_value_conservative is None:
                _fail(f"{label}: atlas.stats computes no test for a simulated series")
            gap = abs(float(p) - test.p_value_conservative)
            if gap > TOLERANCE:
                _fail(f"{label}: the vectorised rule differs from atlas.stats by {gap:.3g}")
            self.count += 1
            self.largest = max(self.largest, gap)


def null_p_values(
    draw: Callable[[int], np.ndarray], replications: int, check: RuleCheck, label: str
) -> np.ndarray:
    """Rule p-values of ``replications`` null series, drawn ``CHUNK`` rows at a time."""
    out = np.empty(replications)
    for start in range(0, replications, CHUNK):
        rows = draw(min(CHUNK, replications - start))
        out[start : start + rows.shape[0]] = rule_p_values(rows)
        if start == 0:
            check(rows[:CHECK_ROWS], out[:CHECK_ROWS], label)
    return out


# --- Null series ----------------------------------------------------------------------------


def ma29_draws(rng: np.random.Generator, origins: int) -> Callable[[int], np.ndarray]:
    """MA(29) null series, drawn exactly as ``dm_size_study.null_series`` draws them."""
    kernel = np.full(H, 1.0 / H)

    def draw(count: int) -> np.ndarray:
        daily = rng.standard_normal((count, origins + H - 1))
        return np.stack([np.convolve(row, kernel, mode="valid") for row in daily])

    return draw


def block_bootstrap_draws(
    rng: np.random.Generator, values: np.ndarray, block: int
) -> Callable[[int], np.ndarray]:
    """Null-imposed circular block bootstrap of ``values`` (demeaned, then resampled)."""
    centered = values - values.mean()
    size = centered.size
    wrapped = np.concatenate([centered, centered[: block - 1]])
    windows = np.lib.stride_tricks.sliding_window_view(wrapped, block)  # the block from s
    blocks = -(-size // block)

    def draw(count: int) -> np.ndarray:
        starts = rng.integers(0, size, size=(count, blocks))
        return windows[starts].reshape(count, blocks * block)[:, :size]

    return draw


def tapered_covariance(values: np.ndarray, taper: int) -> np.ndarray:
    """Toeplitz covariance from the Bartlett-tapered sample autocovariance of ``values``."""
    size = values.size
    gamma = autocovariances(values[None, :], size - 1)[0]
    lags = np.arange(size)
    weights = np.clip(1.0 - lags / (taper + 1), 0.0, None)
    covariance: np.ndarray = (gamma * weights)[np.abs(lags[:, None] - lags[None, :])]
    return covariance


def gaussian_draws(
    rng: np.random.Generator, values: np.ndarray, taper: int
) -> Callable[[int], np.ndarray]:
    """Mean-zero Gaussian series with the tapered sample autocovariance of ``values``."""
    try:
        factor = np.linalg.cholesky(tapered_covariance(values, taper))
    except np.linalg.LinAlgError:
        _fail(f"the taper-{taper} covariance is not positive definite")

    def draw(count: int) -> np.ndarray:
        result: np.ndarray = rng.standard_normal((count, values.size)) @ factor.T
        return result

    return draw


# --- The analyses ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Setting:
    """One null hypothesis and, per test, the rule p-values of its simulated series."""

    family: str
    parameter: int  # the block length or taper in days; 29 for MA(29)
    label: str
    p_values: dict[str, np.ndarray]

    def corrected(self, test: Differentials) -> float:
        return float(np.mean(self.p_values[test.name] <= test.rule_p))

    def size(self, name: str) -> float:
        return float(np.mean(self.p_values[name] < NOMINAL))


@dataclass
class Study:
    """Everything the report prints, so that its sections can be rerun and compared."""

    config: Config
    tests: list[Differentials]
    input_gap: float
    check: RuleCheck  # keeps checking in section 4
    checked: tuple[int, float]  # series checked in sections 1-3, largest difference
    ma29_gamma: dict[int, np.ndarray]  # origins -> autocovariances of the MA(29) draws
    settings: list[Setting]
    #: ``(origins, block)`` -> bootstrap p-values of the calibration series (section 4).
    calibration: dict[tuple[int, int], dict[str, np.ndarray]] = field(default_factory=dict)

    def test(self, name: str) -> Differentials:
        return next(t for t in self.tests if t.name == name)

    def calibrated(self) -> list[Setting]:
        """The eight nulls built from the observed series (all but MA(29))."""
        return [s for s in self.settings if s.family != "MA(29)"]


def run_nulls(config: Config, tests: list[Differentials], input_gap: float) -> Study:
    """Sections 1-3: the MA(29) draws, the block bootstrap and the tapered Gaussian nulls."""
    seed, replications = config.seed, config.replications
    check = RuleCheck()
    for origins in sorted({t.values.size for t in tests}, reverse=True):
        observed = np.stack([t.values for t in tests if t.values.size == origins])
        check(observed, rule_p_values(observed), f"observed series, T = {origins}")
    ma29_p: dict[str, np.ndarray] = {}
    ma29_gamma: dict[int, np.ndarray] = {}
    for origins in sorted({t.values.size for t in tests}, reverse=True):
        # dm_size_study.py: stream [seed, origins, 0] for Gaussian shocks, series in order.
        draw = ma29_draws(np.random.default_rng([seed, origins, 0]), origins)
        p_values = np.empty(replications)
        gamma = np.empty((replications, ACF_LAGS[-1] + 1))
        for start in range(0, replications, CHUNK):
            rows = draw(min(CHUNK, replications - start))
            stop = start + rows.shape[0]
            p_values[start:stop] = rule_p_values(rows)
            gamma[start:stop] = autocovariances(rows, ACF_LAGS[-1])
            if start == 0:
                check(rows[:CHECK_ROWS], p_values[:CHECK_ROWS], f"MA(29), T = {origins}")
        ma29_gamma[origins] = gamma
        ma29_p.update({t.name: p_values for t in tests if t.values.size == origins})
    settings = [Setting("MA(29)", H - 1, "MA(29), the erratum's null", ma29_p)]
    for block in BLOCKS:
        p_by_test = {
            t.name: null_p_values(
                block_bootstrap_draws(np.random.default_rng([seed, 2, block, i]), t.values, block),
                replications,
                check,
                f"block bootstrap {block}, {t.name}",
            )
            for i, t in enumerate(tests)
        }
        label = f"block bootstrap, {block} days"
        settings.append(Setting("block bootstrap", block, label, p_by_test))
    for taper in TAPERS:
        p_by_test = {
            t.name: null_p_values(
                gaussian_draws(np.random.default_rng([seed, 3, taper, i]), t.values, taper),
                replications,
                check,
                f"Gaussian taper {taper}, {t.name}",
            )
            for i, t in enumerate(tests)
        }
        label = f"Gaussian, Bartlett taper {taper}"
        settings.append(Setting("Gaussian", taper, label, p_by_test))
    checked = (check.count, check.largest)
    return Study(config, tests, input_gap, check, checked, ma29_gamma, settings)


def calibrate(study: Study, origins: int, block: int) -> dict[str, np.ndarray]:
    """Section 4, one cell: the null-imposed block bootstrap applied to MA(29) series.

    For each of ``calibration_series`` MA(29) series (mean zero), ``calibration_draws``
    resamples of the demeaned series. Returned (and stored): the bootstrap p-value of the
    series itself (``"null"``) and, per committed test with ``origins`` origins, the bootstrap
    p-value of the same series shifted so that its pre-registered p-value is the committed one
    (the share of resamples whose p-value is at most the committed p-value).
    """
    config = study.config
    members = [t for t in study.tests if t.values.size == origins]
    series = ma29_draws(np.random.default_rng([config.seed, 4, origins]), origins)(
        config.calibration_series
    )
    observed = rule_p_values(series)
    study.check(series[:CHECK_ROWS], observed[:CHECK_ROWS], f"calibration, T = {origins}")
    rng = np.random.default_rng([config.seed, 5, origins, block])
    out = {name: np.empty(len(series)) for name in ("null", *(t.name for t in members))}
    for i, row in enumerate(series):
        resamples = block_bootstrap_draws(rng, row, block)(config.calibration_draws)
        p_values = rule_p_values(resamples)
        if i == 0:
            label = f"calibration bootstrap {block}, T = {origins}"
            study.check(resamples[:CHECK_ROWS], p_values[:CHECK_ROWS], label)
        out["null"][i] = np.mean(p_values <= observed[i])
        for t in members:
            out[t.name][i] = np.mean(p_values <= t.rule_p)
    study.calibration[(origins, block)] = out
    return out


def run_calibration(study: Study) -> None:
    for origins in sorted({t.values.size for t in study.tests}, reverse=True):
        for block in BLOCKS:
            calibrate(study, origins, block)


# --- Report helpers -------------------------------------------------------------------------


def binomial_se(p: float, n: int) -> float:
    return math.sqrt(p * (1.0 - p) / n)


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def _pct(value: float) -> str:
    return f"{100.0 * value:.1f} %"


def _range(values: Sequence[float], digits: int = 3) -> str:
    low, high = f"{min(values):.{digits}f}", f"{max(values):.{digits}f}"
    return low if low == high else f"{low}-{high}"


def _pct_range(values: Sequence[float]) -> str:
    return f"{_range([100.0 * v for v in values], 1)} %"


def _holm_pair(h1: float, h2: float) -> tuple[float, float]:
    adjusted = holm([h1, h2])
    if adjusted[0] is None or adjusted[1] is None:  # both are present
        raise AssertionError("holm dropped a p-value")
    return adjusted[0], adjusted[1]


def _corrected(study: Study, setting: Setting) -> dict[str, float]:
    """Size-corrected p-values of a setting, with ``H1, Holm`` and ``H2, Holm``."""
    out = {t.name: setting.corrected(t) for t in study.tests}
    out["H1, Holm"], out["H2, Holm"] = _holm_pair(out["H1"], out["H2"])
    return out


def long_lag_statistic(gamma: np.ndarray) -> np.ndarray:
    """Mean sample autocorrelation over lags 30-120, per row of autocovariances."""
    first, last = LONG_LAGS
    result: np.ndarray = (gamma[:, first : last + 1] / gamma[:, :1]).mean(axis=1)
    return result


def long_lag(study: Study, test: Differentials) -> tuple[float, float, float]:
    """The observed long-lag statistic, its MA(29) mean and the share of MA(29) at or above."""
    observed = float(long_lag_statistic(autocovariances(test.values[None, :], LONG_LAGS[1]))[0])
    null = long_lag_statistic(study.ma29_gamma[test.values.size])
    return observed, float(np.mean(null)), float(np.mean(null >= observed))


def calibration_size_cell(values: dict[str, np.ndarray]) -> str:
    size = float(np.mean(values["null"] < NOMINAL))
    return f"{_pct(size)} (MC SE {100 * binomial_se(size, values['null'].size):.1f})"


def calibration_row(
    test: Differentials, block: int, values: np.ndarray, exact: float, observed: float
) -> list[str]:
    q1, median, q3 = np.quantile(values, [0.25, 0.5, 0.75])
    return [
        test.name,
        f"{block} days",
        f"{exact:.3f}",
        f"{median:.3f} [{q1:.3f}, {q3:.3f}]",
        f"{observed:.3f}",
        f"{float(np.mean(values >= observed)):.3f}",
    ]


# --- The report -----------------------------------------------------------------------------


def header_lines(study: Study) -> Iterator[str]:
    config = study.config
    yield "# Dependence sensitivity of the size-corrected M2 tests (M8 erratum)"
    yield ""
    yield (
        "Generated by `docs/audit/scripts/dm_dependence_sensitivity.py` (read its docstring for "
        "the method and its limitations) from the per-origin loss differentials in "
        "`m2_loss_differentials.csv`, rebuilt from a regenerated full M2 run whose "
        "forecasts.csv has the SHA-256 recorded in the run manifest "
        f"({expected_forecasts_sha256()}). Overlap h = {H}; {config.replications:,} "
        "replications per null setting; section 4: "
        f"{config.calibration_series:,} MA(29) series per number of origins, "
        f"{config.calibration_draws:,} bootstrap resamples each; seed {config.seed}; Python "
        f"{platform.python_version()}, NumPy {np.__version__}, SciPy {scipy.__version__}, "
        f"pandas {pd.__version__}. Monte Carlo standard errors (MC SE) are binomial. A "
        "size-corrected p-value is the share of simulated null series whose pre-registered "
        "p-value (the larger of the HLN and Bartlett 60 p-values) is at most the committed one."
    )


def inputs_lines(study: Study) -> Iterator[str]:
    yield "## 0. Inputs"
    yield ""
    rows = []
    for t in study.tests:
        period, model_id, reference = TESTS[t.name]
        rows.append(
            [
                t.name,
                f"{period}, {t.origins[0]} to {t.origins[-1]}",
                f"{model_id} vs {reference}",
                str(t.values.size),
                f"{t.values.mean():.2f}",
                f"{abs(t.statistic):.4f}",
                f"{t.rule_p:.4f}",
                f"{float(scipy_stats.skew(t.values)):.2f}",
                f"{float(scipy_stats.kurtosis(t.values)):.2f}",
            ]
        )
    yield _table(
        [
            "test",
            "origins",
            "comparison",
            "T",
            "mean differential",
            "absolute HLN t",
            "pre-registered p",
            "skewness",
            "excess kurtosis",
        ],
        rows,
    )
    yield ""
    yield (
        "- `atlas.stats.diebold_mariano` and `bandwidth_sensitivity` on the committed "
        "differentials reproduce the committed statistics, the HLN, Bartlett 60 and "
        "pre-registered p-values, the mean differential and the p-value of every bandwidth "
        f"variant; largest absolute difference {study.input_gap:.1e} (tolerance "
        f"{TOLERANCE:g})."
    )
    yield (
        "- The vectorised rule used for the simulations agrees with "
        f"`atlas.stats.diebold_mariano` on the {study.checked[0]} series checked in sections 1-3 "
        "(the observed series and the first draws of every simulated setting; section 4 checks "
        "its own draws the same way); largest absolute difference of the pre-registered p-value "
        f"{study.checked[1]:.1e} (tolerance {TOLERANCE:g})."
    )
    yield (
        "- Skewness and excess kurtosis: moment estimates of the per-origin differential. The "
        "block bootstrap keeps this marginal distribution; the MA(29) and Gaussian nulls are "
        "symmetric."
    )


def acf_lines(study: Study) -> Iterator[str]:
    first, last = LONG_LAGS
    yield "## 1. Autocorrelation beyond lag 29"
    yield ""
    yield (
        "Sample autocorrelations of the demeaned differentials. Under MA(29) the true "
        f"autocorrelation is 1/30 = {1 / 30:.3f} at lag 29 and 0 beyond. The MA(29) rows give "
        "the mean and the 95 % quantile of the sample autocorrelation over the "
        f"{study.config.replications:,} MA(29) series of section 2 with the same number of "
        "origins, and the share of them at or above the observed value (one-sided). Last "
        f"column: the long-lag statistic, the mean autocorrelation over lags {first}-{last}."
    )
    yield ""
    header = ["test", "row", *(f"lag {k}" for k in ACF_LAGS), f"mean, lags {first}-{last}"]
    rows = []
    for t in study.tests:
        gamma = autocovariances(t.values[None, :], ACF_LAGS[-1])
        observed = [*(gamma[0, ACF_LAGS] / gamma[0, 0]), long_lag(study, t)[0]]
        null_gamma = study.ma29_gamma[t.values.size]
        null = [null_gamma[:, k] / null_gamma[:, 0] for k in ACF_LAGS]
        null.append(long_lag_statistic(null_gamma))
        label = f"{t.name} (T = {t.values.size})"
        rows.append([label, "observed", *(f"{v:.3f}" for v in observed)])
        rows.append(
            [label, "MA(29): mean [95 % quantile]"]
            + [f"{float(np.mean(d)):.3f} [{float(np.quantile(d, 0.95)):.3f}]" for d in null]
        )
        rows.append(
            [label, "MA(29): share at or above observed"]
            + [f"{float(np.mean(d >= v)):.3f}" for d, v in zip(null, observed, strict=True)]
        )
    yield _table(header, rows)


def nulls_lines(study: Study) -> Iterator[str]:
    n = study.config.replications
    yield "## 2. Size-corrected p-values under nulls calibrated to the observed dependence"
    yield ""
    yield (
        "First row: the committed nominal p-values (Holm as committed for H1 and H2). Every other "
        "row: the size-corrected p-value under that null, with its MC SE; Holm: `atlas.stats.holm` "
        "over the size-corrected H1 and H2 p-values (the MC SE of a Holm value is at most twice "
        "that of the p-value it comes from). The block bootstrap and Gaussian nulls are built "
        "from each test's own differential."
    )
    yield ""
    h1, h2 = study.test("H1"), study.test("H2")
    rows = [
        [
            "nominal (committed)",
            *(f"{study.test(name).rule_p:.4f}" for name in TESTS),
            f"{h1.holm_p:.4f}" if h1.holm_p is not None else "n/a",
            f"{h2.holm_p:.4f}" if h2.holm_p is not None else "n/a",
        ]
    ]
    for setting in study.settings:
        values = _corrected(study, setting)
        rows.append(
            [
                setting.label,
                *(
                    f"{values[name]:.3f} (MC SE {binomial_se(values[name], n):.3f})"
                    for name in TESTS
                ),
                f"{values['H1, Holm']:.3f}",
                f"{values['H2, Holm']:.3f}",
            ]
        )
    yield _table(
        ["null", "selection (T = 486)", "H1 (T = 336)", "H2 (T = 336)", "H1, Holm", "H2, Holm"],
        rows,
    )
    yield ""
    yield "Ranges over the nulls built from the observed series, and how many are below 0.05:"
    yield ""
    calibrated = study.calibrated()
    for family in ("block bootstrap", "Gaussian", None):
        chosen = [s for s in calibrated if family is None or s.family == family]
        corrected = [_corrected(study, s) for s in chosen]
        parts = []
        for name in (*TESTS, "H1, Holm", "H2, Holm"):
            column = [c[name] for c in corrected]
            below = sum(p < NOMINAL for p in column)
            parts.append(f"{name} {_range(column)} ({below} below)")
        label = f"all {len(chosen)}" if family is None else f"{family} ({len(chosen)})"
        yield f"- {label}: " + "; ".join(parts) + "."


def size_lines(study: Study) -> Iterator[str]:
    n = study.config.replications
    yield "## 3. Size of the nominal rule under each null"
    yield ""
    yield (
        "Share of the null series that the pre-registered rule rejects at a nominal 5 % "
        "(`p_value_conservative < 0.05`). Under the block bootstrap and Gaussian nulls the null "
        "differs per test, so each test has its own size."
    )
    yield ""
    rows = []
    for s in study.settings:
        sizes = [s.size(name) for name in TESTS]
        rows.append([s.label, *(f"{_pct(v)} (MC SE {100 * binomial_se(v, n):.1f})" for v in sizes)])
    yield _table(["null", "selection (T = 486)", "H1 (T = 336)", "H2 (T = 336)"], rows)


def observed_bootstrap_p(study: Study) -> dict[tuple[str, int], float]:
    """Section 2's block-bootstrap p-value of each test, keyed by ``(test, block)``."""
    return {
        (t.name, s.parameter): s.corrected(t)
        for s in study.settings
        if s.family == "block bootstrap"
        for t in study.tests
    }


def calibration_lines(study: Study) -> Iterator[str]:
    config = study.config
    exact = {t.name: study.settings[0].corrected(t) for t in study.tests}
    observed = observed_bootstrap_p(study)
    yield "## 4. Calibration of the block bootstrap when MA(29) is true"
    yield ""
    yield (
        f"{config.calibration_series:,} MA(29) series (mean zero) per number of origins, each "
        "tested with its own null-imposed circular block bootstrap "
        f"({config.calibration_draws:,} resamples; near 0.05 a bootstrap p-value then has a "
        f"resampling standard error of {binomial_se(NOMINAL, config.calibration_draws):.3f}). "
        "Size: how often the bootstrap p-value is below 0.05; a calibrated method rejects 5 %."
    )
    yield ""
    origins_list = sorted({t.values.size for t in study.tests}, reverse=True)
    rows = [
        [f"T = {o}", *(calibration_size_cell(study.calibration[(o, b)]) for b in BLOCKS)]
        for o in origins_list
    ]
    yield _table(["origins", *(f"blocks of {b} days" for b in BLOCKS)], rows)
    yield ""
    yield (
        "The same MA(29) series shifted so that their pre-registered p-value equals the "
        "committed one. Exact MA(29) p: the size-corrected p-value of the committed statistic "
        "under MA(29) (section 2). Bootstrap p under MA(29): the median and quartiles of the "
        "bootstrap p-values of these series. Observed bootstrap p: that of the real differential "
        "(section 2). Last column: the share of the MA(29) series whose bootstrap p-value is at "
        "least the observed one."
    )
    yield ""
    rows = [
        calibration_row(
            t,
            b,
            study.calibration[(t.values.size, b)][t.name],
            exact[t.name],
            observed[(t.name, b)],
        )
        for t in study.tests
        for b in BLOCKS
    ]
    yield _table(
        [
            "test",
            "blocks",
            "exact MA(29) p",
            "bootstrap p under MA(29): median [quartiles]",
            "observed bootstrap p",
            "share of MA(29) series at or above observed",
        ],
        rows,
    )


def _listed(values: Sequence[int]) -> str:
    words = [str(v) for v in values]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _describe(settings: Sequence[Setting]) -> str:
    """For example ``the Gaussian nulls with tapers 60, 90 and 120``."""
    blocks = [s.parameter for s in settings if s.family == "block bootstrap"]
    tapers = [s.parameter for s in settings if s.family == "Gaussian"]
    parts = []
    if blocks:
        parts.append(f"the block bootstrap with blocks of {_listed(blocks)} days")
    if len(tapers) == 1:
        parts.append(f"the Gaussian null with taper {tapers[0]}")
    elif tapers:
        parts.append(f"the Gaussian nulls with tapers {_listed(tapers)}")
    return " and ".join(parts)


def summary_lines(study: Study) -> Iterator[str]:
    """'What this shows', from sections 0-3; ``calibration_summary_lines`` adds section 4's."""
    config = study.config
    ma29 = _corrected(study, study.settings[0])
    calibrated = study.calibrated()
    values = {s.label: _corrected(study, s) for s in calibrated}
    yield "## What this shows"
    yield ""
    yield (
        f"- Under the MA(29) null, with the first {config.replications:,} draws of "
        f"`dm_size_study.py` (seed {config.seed}): the selection {ma29['selection']:.3f}, H1 "
        f"{ma29['H1']:.3f}, H2 {ma29['H2']:.3f}, and H2 {ma29['H2, Holm']:.3f} after Holm. With "
        "20,000 replications and seed 20261016 these are the erratum's values."
    )
    parts = []
    for t in study.tests:
        observed, null_mean, share = long_lag(study, t)
        if share < NOMINAL:
            verdict = "more than MA(29) produces at the 5 % level"
        elif observed > null_mean:
            verdict = "above the MA(29) mean but within its range"
        else:
            verdict = "below the MA(29) mean"
        parts.append(f"{PROSE[t.name]} {observed:.3f} ({share:.3f}), {verdict}")
    yield (
        f"- Mean autocorrelation over lags {LONG_LAGS[0]}-{LONG_LAGS[1]} (in brackets: the share "
        "of MA(29) series of the same length that reach it): " + "; ".join(parts) + "."
    )
    ma29_size = max(study.settings[0].size(name) for name in TESTS)
    sizes = sorted((s.size(name), name, s.label) for s in calibrated for name in TESTS)
    (low, low_test, low_null), (high, high_test, high_null) = sizes[0], sizes[-1]
    yield (
        f"- At a nominal 5 % the pre-registered rule rejects at most {_pct(ma29_size)} of the "
        f"MA(29) series; under the nulls built from the observed series it rejects from "
        f"{_pct(low)} ({PROSE[low_test]}, {low_null}) to {_pct(high)} ({PROSE[high_test]}, "
        f"{high_null})."
    )
    h1 = [v["H1"] for v in values.values()]
    h1_below = sum(p < NOMINAL for p in h1)
    yield (
        f"- H1: {_range(h1)} under the {len(h1)} nulls built from the observed series "
        f"({h1_below} below 0.05; MA(29) {ma29['H1']:.3f})"
        + (": not significant under any of them." if h1_below == 0 else ".")
    )
    short = [s for s in calibrated if s.parameter < LONG_DEPENDENCE]
    long = [s for s in calibrated if s.parameter >= LONG_DEPENDENCE]
    h2 = [v["H2"] for v in values.values()]
    h2_long = [values[s.label]["H2, Holm"] for s in long]
    long_below = sum(p < NOMINAL for p in h2_long)
    holm_se = max(2.0 * binomial_se(p, config.replications) for p in [*h2, ma29["H2"]])
    yield (
        f"- H2: {_range(h2)} under the {len(h2)} nulls built from the observed series "
        f"({sum(p < NOMINAL for p in h2)} below 0.05). After Holm (MC SE at most "
        f"{holm_se:.3f}): {ma29['H2, Holm']:.3f} under MA(29); "
        + " and ".join(f"{values[s.label]['H2, Holm']:.3f} under {_describe([s])}" for s in short)
        + f", the nulls with dependence up to about a month; and {_range(h2_long)} under the "
        f"{len(long)} nulls with dependence over {LONG_DEPENDENCE} days or more ({long_below} "
        "below 0.05)."
        + (
            " Its significance after the size correction and Holm therefore holds only if the "
            "differential has no material dependence beyond about a month."
            if long_below == 0 and ma29["H2, Holm"] < NOMINAL
            else ""
        )
    )
    selection = {s.label: values[s.label]["selection"] for s in calibrated}
    passing = [s for s in calibrated if selection[s.label] < NOMINAL]
    failing = [s for s in calibrated if selection[s.label] >= NOMINAL]
    family_ranges = " and ".join(
        f"{_range([selection[s.label] for s in calibrated if s.family == family])} under the "
        f"{family} nulls"
        for family in ("block bootstrap", "Gaussian")
    )
    text = (
        f"- The selection: {family_ranges}. A correctly sized gate fails under MA(29) "
        f"({ma29['selection']:.3f}) and under {len(failing)} of the {len(calibrated)} nulls "
        f"built from the observed series ({_range([selection[s.label] for s in failing])})"
    )
    if passing:
        observed, null_mean, _ = long_lag(study, study.test("selection"))
        text += (
            f", and passes under {_describe(passing)} "
            f"({_range([selection[s.label] for s in passing])}). Under these the rule rejects "
            f"only {_pct_range([s.size('selection') for s in passing])} of the null series"
            + (
                ": they carry the development differential's negative autocorrelation beyond "
                "lag 29 (section 1) into the null."
                if observed < null_mean
                else "."
            )
        )
    else:
        text += "."
    yield text


def calibration_summary_lines(study: Study) -> Iterator[str]:
    """The bullets of 'What this shows' that need section 4."""
    exact = {t.name: study.settings[0].corrected(t) for t in study.tests}
    observed = observed_bootstrap_p(study)
    sizes = {
        o: [float(np.mean(study.calibration[(o, b)]["null"] < NOMINAL)) for b in BLOCKS]
        for o in sorted({t.values.size for t in study.tests}, reverse=True)
    }
    medians = {
        t.name: [float(np.median(study.calibration[(t.values.size, b)][t.name])) for b in BLOCKS]
        for t in study.tests
    }
    excess = max(m - exact[name] for name, column in medians.items() for m in column)
    reach = {
        (t.name, b): float(
            np.mean(study.calibration[(t.values.size, b)][t.name] >= observed[(t.name, b)])
        )
        for t in study.tests
        for b in BLOCKS
    }
    yield (
        "- When MA(29) is true the block bootstrap rejects "
        + " and ".join(f"{_pct_range(v)} of the series with {o} origins" for o, v in sizes.items())
        + " at a nominal 5 %. For MA(29) series with the committed statistic its median p-value "
        "is "
        + ", ".join(
            f"{_range(medians[name])} for {PROSE[name]} (exact {exact[name]:.3f})" for name in TESTS
        )
        + f", at most {excess:.3f} above the exact value"
        + (
            ": when MA(29) holds the method does not raise p-values materially"
            if excess <= 0.01
            else ""
        )
        + ". A single series' bootstrap p-value varies, though: the share of MA(29) series "
        "whose bootstrap p-value reaches the observed one is "
        + ", ".join(
            f"{_range([reach[(name, b)] for b in BLOCKS])} for {PROSE[name]}" for name in TESTS
        )
        + "."
    )
    unusual = [b for b in BLOCKS if reach[("selection", b)] < NOMINAL]
    skew = {t.name: float(scipy_stats.skew(t.values)) for t in study.tests}
    observed_lag, null_mean, _ = long_lag(study, study.test("selection"))
    if unusual and observed_lag < null_mean:
        bootstrap = [s for s in study.calibrated() if s.family == "block bootstrap"]
        yield (
            f"- Under {_describe([s for s in bootstrap if s.parameter in unusual])}"
            + " the selection's bootstrap p-values ("
            + _range([observed[("selection", b)] for b in unusual])
            + ") are larger than the bootstrap gives MA(29) series with the same statistic, "
            "although its dependence beyond lag 29 is below the MA(29) mean (section 1). The "
            "block bootstrap also keeps the marginal distribution, which is skewed for the "
            f"selection ({skew['selection']:.2f}) and H2 ({skew['H2']:.2f}), while the MA(29) "
            "and Gaussian nulls are symmetric; this study does not separate that from the other "
            "explanations (the shape of the dependence within a month, level shifts)."
        )


def report(study: Study, emit: Callable[[str], None]) -> None:
    sections: list[Iterator[str]] = [
        header_lines(study),
        inputs_lines(study),
        acf_lines(study),
        nulls_lines(study),
        size_lines(study),
        calibration_lines(study),
    ]
    for lines in sections:
        for line in lines:
            emit(line)
        emit("")
    for line in summary_lines(study):
        emit(line)
    for line in calibration_summary_lines(study):
        emit(line)


# --- Command line ---------------------------------------------------------------------------


def rebuild(forecasts: Path, *, write: bool) -> int:
    text = differentials_csv(rebuild_differentials(forecasts), expected_forecasts_sha256())
    name = DIFFERENTIALS.relative_to(REPO_ROOT)
    if write:
        DIFFERENTIALS.write_text(text, encoding="utf-8")
        committed_differentials()  # the written file must reproduce the committed metrics
        print(f"wrote {name}")
        return 0
    if DIFFERENTIALS.read_text(encoding="utf-8") != text:
        print(f"{name} differs from the differentials rebuilt from {forecasts}")
        return 1
    print(f"{name} equals the differentials rebuilt from {forecasts}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    defaults = Config()
    parser.add_argument("--replications", type=int, default=defaults.replications)
    parser.add_argument("--calibration-series", type=int, default=defaults.calibration_series)
    parser.add_argument("--calibration-draws", type=int, default=defaults.calibration_draws)
    parser.add_argument("--seed", type=int, default=defaults.seed)
    parser.add_argument("--rebuild", type=Path, metavar="FORECASTS_CSV")
    parser.add_argument("--write", action="store_true", help="with --rebuild: write the CSV")
    args = parser.parse_args(argv)
    if args.write and args.rebuild is None:
        parser.error("--write needs --rebuild")
    if args.rebuild is not None:
        return rebuild(args.rebuild, write=args.write)
    if args.replications < 1000 or min(args.calibration_series, args.calibration_draws) < 100:
        parser.error("use at least 1000 replications and 100 calibration series and draws")
    config = Config(args.replications, args.calibration_series, args.calibration_draws, args.seed)
    started = time.perf_counter()
    study = run_nulls(config, *committed_differentials())
    print(f"sections 1-3 took {time.perf_counter() - started:.0f} s", file=sys.stderr)
    run_calibration(study)
    print(f"all sections took {time.perf_counter() - started:.0f} s", file=sys.stderr)
    report(study, print)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
