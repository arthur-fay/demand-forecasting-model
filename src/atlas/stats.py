"""Inference for comparing forecasts whose windows overlap (M2).

Forecasts issued on consecutive days share most of their target days, so their errors are
strongly dependent; treating the 14,580 forecasts of a 486-origin period as independent
would overstate certainty many times over. This module provides:

* :func:`long_run_variance`: the variance of a mean under serial dependence, with a
  rectangular (truncated) or Bartlett kernel;
* :func:`diebold_mariano`: the Harvey-Leybourne-Newbold (1997) version of the
  Diebold-Mariano (1995) test: rectangular kernel truncated at ``h - 1`` (Bartlett
  fallback if that estimate is not positive), the HLN small-sample factor and a Student-t
  reference with ``T - 1`` degrees of freedom. A Bartlett estimate with bandwidth ``2h``
  is reported as a sensitivity check; ``p_value_conservative`` is the larger p-value and
  the reported interval uses the larger of the two standard errors, so the interval
  excludes zero exactly when the conservative p-value is below ``1 - level``;
* :func:`bandwidth_sensitivity`: the same statistic under wider HAC bandwidths
  (descriptive; never used for a decision);
* :func:`holm` adjustment for a family of tests;
* the Student-t distribution (regularised incomplete beta, no SciPy).

Assumptions to keep in mind: the loss differential is treated as covariance-stationary,
which daily demand with level shifts need not be; with expanding windows the test
compares forecasting *methods* (as in Giacomini and White, 2006) rather than population
models. The p-values are therefore indicative.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, Final, Literal

import numpy as np

from atlas.errors import ForecastError

MIN_ORIGINS_FLOOR: Final = 50
_BETA_EPS: Final = 3e-16
_BETA_MAX_ITER: Final = 400

Kernel = Literal["rectangular", "bartlett"]


# --- Student-t distribution -----------------------------------------------------------


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (modified Lentz method)."""
    tiny = 1e-300
    c = 1.0
    d = 1.0 - (a + b) * x / (a + 1.0)
    d = tiny if abs(d) < tiny else d
    d = 1.0 / d
    result = d
    for m in range(1, _BETA_MAX_ITER + 1):
        m2 = 2 * m
        numerator = m * (b - m) * x / ((a + m2 - 1.0) * (a + m2))
        d = 1.0 + numerator * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + numerator / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        result *= d * c
        numerator = -(a + m) * (a + b + m) * x / ((a + m2) * (a + m2 + 1.0))
        d = 1.0 + numerator * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + numerator / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) < _BETA_EPS:
            return result
    raise ArithmeticError("incomplete beta continued fraction did not converge")  # pragma: no cover


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """``I_x(a, b)`` for ``a, b > 0`` and ``0 <= x <= 1``."""
    if a <= 0 or b <= 0 or not 0.0 <= x <= 1.0:
        raise ForecastError(f"invalid incomplete beta arguments a={a}, b={b}, x={x}")
    if x in (0.0, 1.0):
        return x
    log_front = (
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    front = math.exp(log_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1.0 - front * _beta_continued_fraction(b, a, 1.0 - x) / b


def student_t_two_sided_p(statistic: float, df: float) -> float:
    """``P(|T| >= |statistic|)`` for a Student-t variable with ``df`` degrees of freedom."""
    if df <= 0:
        raise ForecastError("degrees of freedom must be positive")
    if math.isnan(statistic):
        raise ForecastError("the test statistic is not a number")
    if math.isinf(statistic):
        return 0.0
    return regularized_incomplete_beta(df / 2.0, 0.5, df / (df + statistic * statistic))


def student_t_quantile(probability: float, df: float) -> float:
    """Quantile of the Student-t distribution (bisection on the CDF; ``0 < p < 1``)."""
    if not 0.0 < probability < 1.0:
        raise ForecastError("probability must be strictly between 0 and 1")
    if probability == 0.5:
        return 0.0
    tail = probability if probability < 0.5 else 1.0 - probability
    low, high = 0.0, 1.0
    while student_t_two_sided_p(high, df) / 2.0 > tail:
        high *= 2.0
    for _ in range(200):
        middle = (low + high) / 2.0
        if student_t_two_sided_p(middle, df) / 2.0 > tail:
            low = middle
        else:
            high = middle
    value = (low + high) / 2.0
    return value if probability > 0.5 else -value


# --- Long-run variance and the DM test --------------------------------------------------


def long_run_variance(
    values: Sequence[float] | np.ndarray, bandwidth: int, kernel: Kernel = "bartlett"
) -> float:
    """Variance of ``sqrt(T) * mean`` under serial dependence up to ``bandwidth`` lags.

    ``rectangular`` weights every autocovariance up to ``bandwidth`` by 1 (Diebold and
    Mariano's estimator; it can be negative). ``bartlett`` uses ``1 - k / (bandwidth + 1)``
    (Newey and West; never negative).
    """
    series = np.asarray(values, dtype="float64")
    if series.size == 0:
        raise ForecastError("cannot estimate the variance of an empty series")
    if bandwidth < 0:
        raise ForecastError("bandwidth must be >= 0")
    centered = series - series.mean()
    size = centered.size
    variance = float(centered @ centered) / size
    for lag in range(1, min(bandwidth, size - 1) + 1):
        autocovariance = float(centered[lag:] @ centered[:-lag]) / size
        weight = 1.0 if kernel == "rectangular" else 1.0 - lag / (bandwidth + 1)
        variance += 2.0 * weight * autocovariance
    return variance


@dataclass(frozen=True)
class DieboldMariano:
    """HLN-corrected Diebold-Mariano test of ``mean(differences) == 0``.

    ``mean_difference`` < 0 favours the model over its reference (losses are model minus
    reference). ``ci_low``/``ci_high`` bound the mean difference (95 %, Student-t
    quantile) with the larger of the two HAC standard errors (``ci_basis`` names it), so
    they agree with ``p_value_conservative``; ``ci_low_hln``/``ci_high_hln`` use the
    primary HLN standard error alone. ``computed`` is false, with a ``reason``, when the
    sample cannot support the test.
    """

    n: int
    horizon: int
    mean_difference: float | None
    computed: bool
    reason: str | None = None
    estimator: str | None = None
    statistic: float | None = None
    p_value: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    bartlett_statistic: float | None = None
    bartlett_p_value: float | None = None
    p_value_conservative: float | None = None
    ci_basis: str | None = None
    ci_low_hln: float | None = None
    ci_high_hln: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def min_origins(horizon: int) -> int:
    """Smallest number of origins for which the test is reported."""
    return max(3 * horizon, MIN_ORIGINS_FLOOR)


def diebold_mariano(
    differences: Sequence[float] | np.ndarray,
    *,
    horizon: int,
    contiguous: bool = True,
    level: float = 0.95,
) -> DieboldMariano:
    """Test whether the mean loss differential is zero (see the module docstring).

    ``differences`` hold one loss differential per origin, in chronological order, on an
    equally spaced origin grid. ``horizon`` is the number of origin steps over which
    neighbouring forecast windows overlap (``h`` for a single horizon, the largest horizon
    for a bucket average; divided by the origin stride when origins are not daily). When
    the grid has gaps (``contiguous`` false) lags would be mis-measured, so no test is made.
    """
    if isinstance(horizon, bool) or horizon < 1:
        raise ForecastError("horizon must be a positive integer")
    series = np.asarray(differences, dtype="float64")
    size = int(series.size)
    if size and not np.isfinite(series).all():
        return DieboldMariano(size, horizon, None, False, "non-finite loss differential")
    mean = float(series.mean()) if size else None
    if not contiguous:
        return DieboldMariano(size, horizon, mean, False, "origin grid has gaps")
    if size < min_origins(horizon):
        return DieboldMariano(
            size, horizon, mean, False, f"fewer than {min_origins(horizon)} origins"
        )
    if mean is None:  # pragma: no cover - size >= 50 here
        raise AssertionError("empty series")
    magnitude = max(1.0, float(np.abs(series).max()))
    if float(series.max() - series.min()) <= 1e-12 * magnitude:
        # Constant up to rounding: the variance is zero; the computed value is only noise.
        return DieboldMariano(size, horizon, mean, False, "loss differential has no variance")
    bandwidth = min(horizon - 1, size - 1)
    variance = long_run_variance(series, bandwidth, "rectangular")
    estimator = "rectangular"
    if variance <= 0:
        variance = long_run_variance(series, bandwidth, "bartlett")
        estimator = "bartlett_fallback"
    if variance <= 0:
        return DieboldMariano(size, horizon, mean, False, "loss differential has no variance")
    correction = (size + 1 - 2 * horizon + horizon * (horizon - 1) / size) / size
    standard_error = math.sqrt(variance / size) / math.sqrt(correction)
    statistic = mean / standard_error
    df = size - 1
    p_value = student_t_two_sided_p(statistic, df)
    quantile = student_t_quantile(0.5 + level / 2.0, df)
    bartlett = long_run_variance(series, min(2 * horizon, size - 1), "bartlett")
    bartlett_statistic: float | None = None
    bartlett_p: float | None = None
    wider_error, basis = standard_error, estimator
    if bartlett > 0:
        bartlett_error = math.sqrt(bartlett / size) / math.sqrt(correction)
        bartlett_statistic = mean / bartlett_error
        bartlett_p = student_t_two_sided_p(bartlett_statistic, df)
        if bartlett_error > standard_error:
            wider_error, basis = bartlett_error, "bartlett_2h"
    conservative = max(p_value, bartlett_p) if bartlett_p is not None else p_value
    return DieboldMariano(
        n=size,
        horizon=horizon,
        mean_difference=mean,
        computed=True,
        estimator=estimator,
        statistic=statistic,
        p_value=p_value,
        ci_low=mean - quantile * wider_error,
        ci_high=mean + quantile * wider_error,
        bartlett_statistic=bartlett_statistic,
        bartlett_p_value=bartlett_p,
        p_value_conservative=conservative,
        ci_basis=basis,
        ci_low_hln=mean - quantile * standard_error,
        ci_high_hln=mean + quantile * standard_error,
    )


#: ``(kernel, bandwidth as a multiple of h, offset)``: bandwidth = multiple * h + offset.
SENSITIVITY_BANDWIDTHS: Final[tuple[tuple[Kernel, int, int], ...]] = (
    ("rectangular", 1, -1),
    ("rectangular", 2, -1),
    ("bartlett", 2, 0),
    ("bartlett", 3, 0),
    ("bartlett", 4, 0),
)


def bandwidth_sensitivity(
    differences: Sequence[float] | np.ndarray,
    *,
    horizon: int,
    contiguous: bool = True,
    level: float = 0.95,
) -> list[dict[str, Any]]:
    """The DM statistic of :func:`diebold_mariano` under wider HAC bandwidths.

    Truncating at ``h - 1`` assumes that losses on different target days are
    uncorrelated; when the loss differential stays autocorrelated beyond that lag (for
    example through regimes lasting months) the primary standard error is too small.
    Each variant keeps the HLN factor and the ``t(T - 1)`` reference and reports its
    p-value, the half-width of its interval and its effective sample size
    ``T * gamma_0 / LRV``. A variant whose variance estimate is not positive is reported
    with ``None`` values. Descriptive only: no decision uses these numbers. Returns an
    empty list when :func:`diebold_mariano` would not compute the test.
    """
    primary = diebold_mariano(differences, horizon=horizon, contiguous=contiguous, level=level)
    if not primary.computed or primary.mean_difference is None:
        return []
    series = np.asarray(differences, dtype="float64")
    size = int(series.size)
    mean = primary.mean_difference
    centered = series - mean
    gamma0 = float(centered @ centered) / size
    correction = (size + 1 - 2 * horizon + horizon * (horizon - 1) / size) / size
    quantile = student_t_quantile(0.5 + level / 2.0, size - 1)
    rows: list[dict[str, Any]] = []
    for kernel, multiple, offset in SENSITIVITY_BANDWIDTHS:
        bandwidth = min(max(multiple * horizon + offset, 0), size - 1)
        variance = long_run_variance(series, bandwidth, kernel)
        row: dict[str, Any] = {
            "kernel": kernel,
            "bandwidth": bandwidth,
            "p_value": None,
            "ci_half_width": None,
            "effective_sample_size": None,
        }
        if variance > 0:
            error = math.sqrt(variance / size) / math.sqrt(correction)
            row["p_value"] = student_t_two_sided_p(mean / error, size - 1)
            row["ci_half_width"] = quantile * error
            row["effective_sample_size"] = size * gamma0 / variance
        rows.append(row)
    return rows


def holm(p_values: Sequence[float | None]) -> list[float | None]:
    """Holm-Bonferroni adjusted p-values (``None`` entries stay ``None``)."""
    present = sorted((p, i) for i, p in enumerate(p_values) if p is not None)
    adjusted: list[float | None] = [None] * len(p_values)
    running = 0.0
    count = len(present)
    for rank, (p, index) in enumerate(present):
        running = max(running, min(1.0, (count - rank) * p))
        adjusted[index] = running
    return adjusted
