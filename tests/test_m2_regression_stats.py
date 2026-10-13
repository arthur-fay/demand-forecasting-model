"""Least squares diagnostics (``atlas.regression``) and inference under overlap (``atlas.stats``).

Expected values never come from the code under test. They come from:

* closed forms: simple-regression hat values ``1/n + (x - xbar)^2 / Sxx``; Hadamard (Walsh)
  designs whose standardised Gram matrix, and therefore singular values, are known exactly;
  the Cauchy (``tan``) and two-degree-of-freedom Student-t quantiles; the binomial form of
  the regularised incomplete beta function for integer parameters; the finite
  Abramowitz & Stegun 26.7.3-26.7.4 sums for the Student-t distribution with integer
  degrees of freedom; the Cornish-Fisher expansion of large-df quantiles;
* published Student-t table values (seven decimals);
* hand calculations, done exactly with :class:`fractions.Fraction` where the data allow;
* plain NumPy / plain-Python references written in this file: the scikit-learn
  ``LinearRegression`` recipe spelt out with ``numpy.linalg.pinv`` (same cut-off), the hat
  matrix ``A pinv(A)`` with an explicit intercept column, a direct implementation of the
  formulas in the ``atlas.stats`` docstring (long-run variance, HLN-corrected
  Diebold-Mariano, Holm step-down).
"""

from __future__ import annotations

import math
import statistics
from fractions import Fraction
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from atlas.errors import ForecastError, ModelFitError
from atlas.regression import (
    ESTIMABILITY_TOLERANCE,
    HIGH_LEVERAGE,
    FitMethod,
    design_diagnostics,
    fit_linear,
)
from atlas.stats import (
    MIN_ORIGINS_FLOOR,
    diebold_mariano,
    holm,
    long_run_variance,
    min_origins,
    regularized_incomplete_beta,
    student_t_quantile,
    student_t_two_sided_p,
)

EPS = float(np.finfo(np.float64).eps)
OLS = FitMethod.STANDARDIZED_OLS
LEGACY = FitMethod.LEGACY_MIN_NORM
BOTH = (OLS, LEGACY)
Z_975 = 1.959963984540054  # standard normal 0.975 quantile


# --- Plain references: regression -------------------------------------------------------


def with_intercept(x: np.ndarray) -> np.ndarray:
    matrix = np.atleast_2d(np.asarray(x, dtype="float64"))
    return np.column_stack([np.ones(len(matrix)), matrix])


def reference_min_norm(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float]:
    """scikit-learn ``LinearRegression`` spelt out: centre, pinv with ``max(n, p) * eps``."""
    rows, columns = x.shape
    center = x.mean(axis=0)
    pinv = np.linalg.pinv(x - center, rtol=max(rows, columns) * EPS)
    coefficients = pinv @ (y - y.mean())
    return coefficients, float(y.mean() - center @ coefficients)


def reference_ols_predictions(x_train: np.ndarray, y: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """Predictions of ordinary least squares with an explicit intercept column."""
    beta = np.linalg.lstsq(with_intercept(x_train), y, rcond=None)[0]
    return with_intercept(rows) @ beta


def hat_diagonal(x: np.ndarray) -> np.ndarray:
    """Diagonal of ``A pinv(A)`` with ``A = [1, X]`` (projection onto the column space)."""
    a = with_intercept(x)
    return np.diag(a @ np.linalg.pinv(a, rtol=1e-10))


def new_row_leverage(x_train: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """``a' (A'A)^+ a = ||pinv(A)' a||^2`` with ``a = [1, x]`` and ``A = [1, X]``."""
    pinv = np.linalg.pinv(with_intercept(x_train), rtol=1e-10)
    return np.asarray(((with_intercept(rows) @ pinv) ** 2).sum(axis=1))


def hadamard_columns() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Three mutually orthogonal, zero-mean +-1 columns of length 8 (std exactly 1)."""
    t = np.arange(8)
    return (
        np.where(t < 4, 1.0, -1.0),
        np.where(t // 2 % 2 == 0, 1.0, -1.0),
        np.where(t % 2 == 0, 1.0, -1.0),
    )


def weekday_design(weeks: int = 10, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """``[trend, d_0 .. d_6]``: the full dummy set sums to the intercept (rank 7 of 8)."""
    rows = 7 * weeks
    t = np.arange(rows)
    dummies = (t[:, None] % 7 == np.arange(7)[None, :]).astype("float64")
    x = np.column_stack([t.astype("float64"), dummies])
    rng = np.random.default_rng(seed)
    effects = np.array([5.0, -3.0, 2.0, 0.0, 7.0, -10.0, 1.5])
    y = 200.0 + 1.5 * t + dummies @ effects + rng.normal(0.0, 2.0, rows)
    return x, y


def weekday_rows(trend: list[float], weights: list[list[float]]) -> np.ndarray:
    return np.column_stack([np.asarray(trend, dtype="float64"), np.asarray(weights, "float64")])


def sum_relation_design(seed: int = 3, rows: int = 30) -> tuple[np.ndarray, np.ndarray]:
    """Integer columns with ``x3 = x1 + x2 + 10`` exactly (rank 2 of 3 after centring)."""
    rng = np.random.default_rng(seed)
    x1 = rng.integers(-50, 50, rows).astype("float64")
    x2 = rng.integers(0, 200, rows).astype("float64")
    x = np.column_stack([x1, x2, x1 + x2 + 10.0])
    y = 4.0 + 0.5 * x1 - 2.0 * x2 + rng.normal(0.0, 1.0, rows)
    return x, y


def random_design(seed: int, rows: int, columns: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    scales = 10.0 ** rng.uniform(-2.0, 3.0, columns)
    x = (rng.normal(size=(rows, columns)) + rng.normal(size=columns)) * scales
    y = 50.0 + x @ (rng.uniform(-3.0, 3.0, columns) / scales) + rng.normal(0.0, 1.0, rows)
    return x, y


def fit_error_code(x: object, y: object, method: FitMethod) -> str:
    with pytest.raises(ModelFitError) as excinfo:
        fit_linear(x, y, method=method)  # type: ignore[arg-type]
    return excinfo.value.code


# --- Plain references: statistics ---------------------------------------------------------


def t_two_sided_reference(statistic: float, df: int) -> float:
    """``P(|T| >= |t|)`` for integer ``df`` (Abramowitz & Stegun 26.7.3 / 26.7.4)."""
    theta = math.atan(abs(statistic) / math.sqrt(df))
    sine, cos2 = math.sin(theta), math.cos(theta) ** 2
    if df % 2 == 1:
        term = total = math.cos(theta)
        for k in range(1, (df - 3) // 2 + 1):
            term *= 2 * k / (2 * k + 1) * cos2
            total += term
        central = 2.0 / math.pi * (theta + (sine * total if df > 1 else 0.0))
    else:
        term = total = 1.0
        for k in range(1, (df - 2) // 2 + 1):
            term *= (2 * k - 1) / (2 * k) * cos2
            total += term
        central = sine * total
    return 1.0 - central


def t_quantile_reference(probability: float, df: int) -> float:
    """Upper quantile (``probability > 0.5``) by bisection on the A&S reference."""
    tail = 2.0 * (1.0 - probability)
    low, high = 0.0, 1.0
    while t_two_sided_reference(high, df) > tail:
        high *= 2.0
    for _ in range(200):
        middle = (low + high) / 2.0
        if t_two_sided_reference(middle, df) > tail:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def p_tolerance(statistic: float, df: float) -> float:
    """Absolute tolerance for a two-sided p-value computed through ``x = df / (df + t^2)``.

    Near ``t = 0``, ``1 - x = t^2 / (df + t^2)`` keeps only ``eps * df / t^2`` relative
    precision, so ``1 - p`` (about ``|t|``) is good to ``eps * df / |t|``, and for
    ``t^2 < eps * df`` x rounds to one. That is floating-point conditioning, not an error,
    and it only concerns p-values within ``|t|`` of one.
    """
    size = abs(statistic)
    return 1e-11 + (min(size, 10 * EPS * max(df, 1.0) / size) if size else 0.0)


def binomial_tail(a: int, b: int, x: float) -> float:
    """``I_x(a, b) = P(Binomial(a + b - 1, x) >= a)`` for positive integers ``a``, ``b``."""
    n = a + b - 1
    return math.fsum(math.comb(n, j) * x**j * (1.0 - x) ** (n - j) for j in range(a, n + 1))


def lrv_reference(values: list[float], bandwidth: int, kernel: str) -> float:
    """Long-run variance written straight from the definition (plain Python)."""
    size = len(values)
    mean = math.fsum(values) / size
    centred = [v - mean for v in values]
    total = math.fsum(c * c for c in centred) / size
    for lag in range(1, min(bandwidth, size - 1) + 1):
        gamma = math.fsum(centred[t] * centred[t - lag] for t in range(lag, size)) / size
        weight = 1.0 if kernel == "rectangular" else 1.0 - lag / (bandwidth + 1)
        total += 2.0 * weight * gamma
    return total


def lrv_exact(values: list[Fraction], bandwidth: int, kernel: str) -> Fraction:
    """The same estimator in exact rational arithmetic."""
    size = len(values)
    mean = sum(values, Fraction(0)) / size
    centred = [v - mean for v in values]
    total = sum((c * c for c in centred), Fraction(0)) / size
    for lag in range(1, min(bandwidth, size - 1) + 1):
        gamma = sum((centred[t] * centred[t - lag] for t in range(lag, size)), Fraction(0)) / size
        weight = Fraction(1) if kernel == "rectangular" else 1 - Fraction(lag, bandwidth + 1)
        total += 2 * weight * gamma
    return total


def hln_correction(size: int, horizon: int) -> float:
    return (size + 1 - 2 * horizon + horizon * (horizon - 1) / size) / size


def dm_reference(values: list[float], horizon: int, level: float = 0.95) -> dict[str, object]:
    """HLN-corrected DM test written from the ``atlas.stats`` docstring (plain Python)."""
    size = len(values)
    mean = math.fsum(values) / size
    if size < max(3 * horizon, 50):
        return {"computed": False, "mean": mean}
    variance = lrv_reference(values, horizon - 1, "rectangular")
    estimator = "rectangular"
    if variance <= 0:
        variance = lrv_reference(values, horizon - 1, "bartlett")
        estimator = "bartlett_fallback"
    if variance <= 0:
        return {"computed": False, "mean": mean}
    correction = hln_correction(size, horizon)
    error = math.sqrt(variance / size / correction)
    statistic = mean / error
    p_value = t_two_sided_reference(statistic, size - 1)
    quantile = t_quantile_reference(0.5 + level / 2.0, size - 1)
    bartlett = lrv_reference(values, min(2 * horizon, size - 1), "bartlett")
    bartlett_statistic = mean / math.sqrt(bartlett / size / correction)
    bartlett_p = t_two_sided_reference(bartlett_statistic, size - 1)
    return {
        "computed": True,
        "mean": mean,
        "estimator": estimator,
        "statistic": statistic,
        "p_value": p_value,
        "ci_low": mean - quantile * error,
        "ci_high": mean + quantile * error,
        "bartlett_statistic": bartlett_statistic,
        "bartlett_p_value": bartlett_p,
        "p_value_conservative": max(p_value, bartlett_p),
    }


def holm_reference(p_values: list[float | None]) -> list[float | None]:
    """Holm step-down: ``adj_(k) = max_{j <= k} min(1, (m - j + 1) p_(j))`` over present values."""
    present = [(p, i) for i, p in enumerate(p_values) if p is not None]
    ordered = sorted(present)
    m = len(ordered)
    result: list[float | None] = [None] * len(p_values)
    for k, (_, index) in enumerate(ordered):
        result[index] = max(min(1.0, (m - j) * ordered[j][0]) for j in range(k + 1))
    return result


# === regression.py ========================================================================


@settings(max_examples=20, deadline=None)
@given(
    seed=st.integers(0, 2**32 - 1),
    columns=st.integers(1, 6),
    extra_rows=st.integers(0, 40),
)
def test_both_methods_recover_an_exact_linear_relation(
    seed: int, columns: int, extra_rows: int
) -> None:
    rng = np.random.default_rng(seed)
    rows = 3 * columns + 5 + extra_rows
    scales = 10.0 ** rng.uniform(-3.0, 4.0, columns)
    x = (rng.normal(size=(rows, columns)) + 3.0 * rng.normal(size=columns)) * scales
    beta = rng.uniform(-5.0, 5.0, columns) / scales
    intercept = float(rng.uniform(-100.0, 100.0))
    y = x @ beta + intercept
    size = max(1.0, float(np.abs(y).max()))
    for method in BOTH:
        fit = fit_linear(x, y, method=method)
        np.testing.assert_allclose(fit.coefficients * scales, beta * scales, rtol=0, atol=1e-8)
        assert abs(fit.intercept - intercept) <= 1e-8 * size
        assert fit.in_sample_mae <= 1e-9 * size
        assert fit.in_sample_rmse <= 1e-9 * size
        assert fit.diagnostics.rank == columns
        assert fit.diagnostics.solver_rank == columns
        assert fit.diagnostics.identifiable
        assert math.isfinite(fit.diagnostics.condition_number)
        new = rng.normal(size=(4, columns)) * scales
        np.testing.assert_allclose(fit.predict(new), new @ beta + intercept, atol=1e-7 * size)
        assert fit.is_estimable(new).all()
    table = fit_linear(x, y, method=OLS).coefficient_table()
    assert list(table.index) == [f"x{i}" for i in range(columns)]
    np.testing.assert_allclose(table.to_numpy() * scales, beta * scales, rtol=0, atol=1e-8)


def test_in_sample_errors_and_coefficients_by_hand() -> None:
    # x = 0..3, y = 0, 2, 1, 3: slope Sxy/Sxx = 4/5, intercept 1.5 - 0.8 * 1.5 = 0.3,
    # residuals -0.3, 0.9, -0.9, 0.3 -> MAE 0.6, RMSE sqrt(1.8 / 4).
    x = np.array([[0.0], [1.0], [2.0], [3.0]])
    y = np.array([0.0, 2.0, 1.0, 3.0])
    for method in BOTH:
        fit = fit_linear(x, y, method=method, feature_names=["slope"])
        assert fit.coefficients[0] == pytest.approx(0.8, abs=1e-14)
        assert fit.intercept == pytest.approx(0.3, abs=1e-14)
        assert fit.in_sample_mae == pytest.approx(0.6, abs=1e-14)
        assert fit.in_sample_rmse == pytest.approx(math.sqrt(0.45), abs=1e-14)
        assert fit.coefficient_table().to_dict() == pytest.approx({"slope": 0.8}, abs=1e-14)
        assert fit.predict(np.array([10.0])) == pytest.approx([8.3], abs=1e-12)
        assert fit.method is method


def test_methods_accept_their_string_values_and_reject_unknown_ones() -> None:
    x = np.array([[0.0], [1.0], [2.0], [3.0]])
    y = np.array([0.0, 2.0, 1.0, 3.0])
    assert fit_linear(x, y, method="legacy_min_norm").method is LEGACY  # type: ignore[arg-type]
    assert fit_linear(x, y, method="standardized_ols").method is OLS  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="ordinary_least_squares"):
        fit_linear(x, y, method="ordinary_least_squares")  # type: ignore[arg-type]


def test_dataframe_input_provides_feature_names() -> None:
    frame = pd.DataFrame({"lag_1": [1.0, 4.0, 2.0, 8.0, 5.0], "trend": [0.0, 1.0, 2.0, 3.0, 4.0]})
    y = pd.Series(3.0 + 2.0 * frame["lag_1"] - 0.5 * frame["trend"])
    fit = fit_linear(frame, y, method=OLS)
    assert fit.feature_names == ("lag_1", "trend")
    table = fit.coefficient_table()
    assert list(table.index) == ["lag_1", "trend"]
    assert table.to_numpy() == pytest.approx([2.0, -0.5], abs=1e-12)
    renamed = fit_linear(frame, y, method=OLS, feature_names=["a", "b"])
    assert renamed.feature_names == ("a", "b")


@pytest.mark.parametrize(
    "design",
    ["duplicate", "weekday_dummies", "sum_relation"],
)
def test_legacy_min_norm_equals_the_pinv_recipe_on_rank_deficient_designs(design: str) -> None:
    if design == "duplicate":
        x, y = random_design(11, 40, 3)
        x = np.column_stack([x, x[:, 1]])
    elif design == "weekday_dummies":
        x, y = weekday_design()
    else:
        x, y = sum_relation_design()
    fit = fit_linear(x, y, method=LEGACY)
    coefficients, intercept = reference_min_norm(x, y)
    scale = float(np.abs(coefficients).max())
    np.testing.assert_allclose(fit.coefficients, coefficients, rtol=0, atol=1e-9 * scale)
    assert fit.intercept == pytest.approx(intercept, rel=1e-9)
    columns = x.shape[1]
    assert fit.diagnostics.n_rows == len(x)
    assert fit.diagnostics.n_columns == columns
    assert fit.diagnostics.rank == columns - 1
    assert fit.diagnostics.solver_rank == columns - 1
    assert not fit.diagnostics.identifiable
    assert fit.diagnostics.condition_number == math.inf
    assert math.isfinite(fit.diagnostics.restricted_condition_number)
    assert fit.diagnostics.largest_dropped_ratio <= max(len(x), columns) * EPS
    assert fit.diagnostics.smallest_kept_ratio > 1e-6
    assert fit.diagnostics.constant_columns == ()
    residuals = y - (intercept + x @ coefficients)
    assert fit.in_sample_mae == pytest.approx(float(np.abs(residuals).mean()), rel=1e-9)
    assert fit.in_sample_rmse == pytest.approx(float(np.sqrt((residuals**2).mean())), rel=1e-9)


def test_legacy_predictions_on_a_duplicated_column_equal_ols_on_the_deduplicated_design() -> None:
    x, y = random_design(5, 40, 2)
    doubled = np.column_stack([x, x[:, 0]])
    fit = fit_linear(doubled, y, method=LEGACY)
    rows = np.array([[1.0, 2.0], [-30.0, 4.5], [0.0, 0.0], [250.0, -9.0]])
    expected = reference_ols_predictions(x, y, rows)
    duplicated_rows = np.column_stack([rows, rows[:, 0]])
    np.testing.assert_allclose(fit.predict(duplicated_rows), expected, rtol=1e-10, atol=1e-9)
    assert fit.is_estimable(duplicated_rows).all()
    broken = duplicated_rows.copy()
    broken[:, 2] += 1.0
    assert not fit.is_estimable(broken).any()
    # The reduced design itself is full rank: standardized OLS gives the same predictions.
    reduced = fit_linear(x, y, method=OLS)
    np.testing.assert_allclose(reduced.predict(rows), expected, rtol=1e-10, atol=1e-9)


def test_legacy_weekday_dummies_predict_like_a_reference_coding_for_estimable_rows() -> None:
    x, y = weekday_design()
    fit = fit_linear(x, y, method=LEGACY)
    reduced = np.delete(x, 1, axis=1)  # drop d_0: the usual reference coding
    one_hot = np.eye(7)
    rows = weekday_rows([70.0, 71.0, 72.0, 73.0, 74.0, 75.0, 76.0], one_hot.tolist())
    expected = reference_ols_predictions(reduced, y, np.delete(rows, 1, axis=1))
    np.testing.assert_allclose(fit.predict(rows), expected, rtol=1e-10)
    assert fit.is_estimable(rows).all()
    # Estimability is the linear relation sum(d) == 1, not "exactly one dummy set".
    half = weekday_rows([80.0], [[0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0]])
    assert fit.is_estimable(half).all()
    half_reduced = np.delete(half, 1, axis=1)
    np.testing.assert_allclose(
        fit.predict(half), reference_ols_predictions(reduced, y, half_reduced), rtol=1e-10
    )
    unestimable = weekday_rows(
        [80.0, 80.0, 80.0],
        [[0.0] * 7, [1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 2.0]],
    )
    assert not fit.is_estimable(unestimable).any()
    with pytest.raises(ModelFitError, match="not identifiable"):
        fit.coefficient_table()


def test_estimability_residual_equals_the_null_space_component() -> None:
    # Null space of the standardised design: n = D (1, 1, -1) with D = diag(std), so for a
    # standardised row z, z . n = (x1 - m1) + (x2 - m2) - (x3 - m3) = x1 + x2 - x3 + 10.
    x, y = sum_relation_design()
    fit = fit_linear(x, y, method=LEGACY)
    mean, std = x.mean(axis=0), x.std(axis=0)
    norm = math.sqrt(float((std**2).sum()))
    rows = np.array(
        [
            [3.0, 7.0, 20.0],  # satisfies x3 = x1 + x2 + 10
            [3.0, 7.0, 10.0],  # breaks it by 10 (x3 = x1 + x2: the intercept is part of it)
            [-40.0, 150.0, 120.0],  # satisfies, far from the training mean
            [0.0, 0.0, 0.0],  # breaks it by 10
            [1.0, 1.0, 100.0],  # breaks it by -88
        ]
    )
    standardized = (rows - mean) / std
    breaks = np.abs(rows[:, 0] + rows[:, 1] - rows[:, 2] + 10.0)
    expected = breaks / norm / np.maximum(1.0, np.linalg.norm(standardized, axis=1))
    np.testing.assert_allclose(fit.estimability_residual(rows), expected, rtol=1e-9, atol=1e-13)
    assert fit.is_estimable(rows).tolist() == [True, False, True, False, False]


def test_estimability_threshold_is_one_in_a_million_of_the_row_norm() -> None:
    x, y = sum_relation_design()
    fit = fit_linear(x, y, method=LEGACY)
    mean, std = x.mean(axis=0), x.std(axis=0)
    norm = math.sqrt(float((std**2).sum()))
    assert ESTIMABILITY_TOLERANCE == 1e-6
    # Moving x3 away from the mean row by delta gives a residual delta / ||n|| (row norm < 1).
    below = mean + np.array([0.0, 0.0, 0.5e-6 * norm])
    above = mean + np.array([0.0, 0.0, 2.0e-6 * norm])
    residual = fit.estimability_residual(np.vstack([below, above]))
    assert residual == pytest.approx([0.5e-6, 2.0e-6], rel=1e-6, abs=0)
    assert fit.is_estimable(np.vstack([below, above])).tolist() == [True, False]


def test_full_rank_fits_have_no_null_space() -> None:
    x, y = random_design(2, 30, 4)
    for method in BOTH:
        fit = fit_linear(x, y, method=method)
        rows = np.vstack([x[:3], 1e6 * np.ones((1, 4))])
        assert fit.estimability_residual(rows).tolist() == [0.0, 0.0, 0.0, 0.0]
        assert fit.is_estimable(rows).all()


@pytest.mark.parametrize("scaled", [False, True])
@pytest.mark.parametrize("method", BOTH)
def test_diagnostics_of_a_design_with_known_singular_values(
    scaled: bool, method: FitMethod
) -> None:
    # Standardised columns h1, 0.96 h1 + 0.28 h2, h3: Z'Z = 8 [[1, .96, 0], [.96, 1, 0],
    # [0, 0, 1]] with eigenvalues 8 * 1.96, 8, 8 * 0.04 -> s_max / s_min = 1.4 / 0.2 = 7.
    # Scaling and shifting columns must not change any of it (diagnostics are unit-free).
    h1, h2, h3 = hadamard_columns()
    x = np.column_stack([h1, 0.96 * h1 + 0.28 * h2, h3])
    if scaled:
        x = x * np.array([1000.0, 0.01, 5.0]) + np.array([7.0, -3.0, 1e4])
    y = np.array([3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0])
    diagnostics = fit_linear(x, y, method=method).diagnostics
    assert diagnostics.n_rows == 8
    assert diagnostics.n_columns == 3
    assert diagnostics.rank == 3
    assert diagnostics.solver_rank == 3
    assert diagnostics.identifiable
    assert diagnostics.condition_number == pytest.approx(7.0, rel=1e-10)
    assert diagnostics.restricted_condition_number == pytest.approx(7.0, rel=1e-10)
    assert diagnostics.smallest_kept_ratio == pytest.approx(1.0 / 7.0, rel=1e-10)
    assert diagnostics.largest_dropped_ratio == 0.0
    assert diagnostics.constant_columns == ()


def test_diagnostics_of_a_rank_deficient_design_with_known_singular_values() -> None:
    # Standardised columns h1, h2, h1 (the third is 3 h1 + 2): Z'Z = 8 [[1, 0, 1], [0, 1, 0],
    # [1, 0, 1]] with eigenvalues 16, 8, 0 -> singular values 4, 2 sqrt(2), 0.
    h1, h2, _ = hadamard_columns()
    x = np.column_stack([h1, h2, 3.0 * h1 + 2.0])
    y = np.array([3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0])
    fit = fit_linear(x, y, method=LEGACY)
    diagnostics = fit.diagnostics
    assert diagnostics.rank == 2
    assert diagnostics.solver_rank == 2
    assert not diagnostics.identifiable
    assert diagnostics.condition_number == math.inf
    assert diagnostics.restricted_condition_number == pytest.approx(math.sqrt(2.0), rel=1e-12)
    assert diagnostics.smallest_kept_ratio == pytest.approx(1.0 / math.sqrt(2.0), rel=1e-12)
    assert 0.0 <= diagnostics.largest_dropped_ratio <= 1e-15
    assert fit_error_code(x, y, OLS) == "rank_deficient"


def test_a_small_singular_value_above_the_documented_cut_off_is_kept() -> None:
    # Columns h1 and h1 + delta h2: s_min / s_max = delta / 2 (to first order) = 5e-13,
    # far above max(n, p) * eps = 1.8e-15, so the design has full numerical rank
    # (a looser cut-off such as 1e-12 or sqrt(eps) would drop it).
    h1, h2, _ = hadamard_columns()
    delta = 1e-12
    x = np.column_stack([h1, h1 + delta * h2])
    y = np.array([3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0])
    diagnostics, *_ = design_diagnostics(x, ["a", "b"])
    assert diagnostics.rank == 2
    assert diagnostics.condition_number == pytest.approx(2.0 / delta, rel=1e-2)
    assert diagnostics.smallest_kept_ratio == pytest.approx(delta / 2.0, rel=1e-2, abs=0)
    assert diagnostics.largest_dropped_ratio == 0.0
    assert fit_linear(x, y, method=OLS).diagnostics.identifiable


def test_the_rank_cut_off_is_relative_to_the_largest_singular_value() -> None:
    # Columns u, u + delta v, u with orthogonal balanced +-1 vectors u, v (n = 4096):
    # Z'Z = n (J + delta^2 e2 e2'), singular values sqrt(3n), delta sqrt(2n/3), 0, so the
    # ratios are 1, delta sqrt(2)/3 and 0. With delta = 2e-13 the middle ratio (9.4e-14) is
    # below max(n, p) * eps = 9.1e-13 and is dropped, although the singular value itself
    # (1.0e-11) is far above an absolute cut-off of max(n, p) * eps.
    rows = 4096
    t = np.arange(rows)
    u = np.where(t < rows // 2, 1.0, -1.0)
    v = np.where(t % 2 == 0, 1.0, -1.0)
    delta = 2e-13
    x = np.column_stack([u, u + delta * v, u])
    y = 2.0 * u + np.where(t % 3 == 0, 1.0, 0.0)
    fit = fit_linear(x, y, method=LEGACY)
    diagnostics = fit.diagnostics
    assert diagnostics.rank == 1
    assert diagnostics.solver_rank == 1
    assert diagnostics.condition_number == math.inf
    assert diagnostics.restricted_condition_number == pytest.approx(1.0, rel=1e-12)
    assert diagnostics.smallest_kept_ratio == pytest.approx(1.0, rel=1e-12)
    # The largest discarded ratio is the delta direction, not the exact duplicate (0).
    expected = delta * math.sqrt(2) / 3
    assert diagnostics.largest_dropped_ratio == pytest.approx(expected, rel=1e-2, abs=0)
    assert fit_error_code(x, y, OLS) == "rank_deficient"


def test_solver_rank_is_the_rank_of_the_unscaled_legacy_solve() -> None:
    # On the raw centred design the second column is below gelsd's cut-off
    # max(n, p) * eps * s_max (column scales 1e16 and 1); standardised, both are kept.
    rng = np.random.default_rng(17)
    u = rng.integers(-20, 20, 30).astype("float64")
    v = rng.integers(-20, 20, 30).astype("float64")
    x = np.column_stack([1e16 * u, v])
    y = 5.0 + 3.0 * u + 2.0 * v
    legacy = fit_linear(x, y, method=LEGACY)
    assert legacy.diagnostics.rank == 2
    assert legacy.diagnostics.solver_rank == 1
    ols = fit_linear(x, y, method=OLS)
    assert ols.diagnostics.solver_rank == 2
    assert ols.coefficients == pytest.approx([3e-16, 2.0], rel=1e-9, abs=0)
    assert ols.intercept == pytest.approx(5.0, rel=1e-9)


@pytest.mark.parametrize("value", [0.0, 5.0, 0.1, 1e6, -2.5])
def test_constant_column_is_refused_by_standardized_ols(value: float) -> None:
    x, y = random_design(4, 25, 2)
    design = np.column_stack([x[:, 0], np.full(25, value), x[:, 1]])
    with pytest.raises(ModelFitError, match="constant") as excinfo:
        fit_linear(design, y, method=OLS, feature_names=["a", "level", "b"])
    assert excinfo.value.code == "constant_column"
    assert "level" in str(excinfo.value)


@pytest.mark.parametrize("method", BOTH)
def test_a_column_varying_only_by_rounding_is_constant(method: FitMethod) -> None:
    # 0.3 and 0.1 * 3 differ by one unit in the last place: the column is constant up to
    # floating-point rounding (relative spread ~1e-16) and must be treated as constant,
    # not standardised into a +-1 column of pure rounding noise.
    x, y = random_design(6, 24, 2)
    level = np.where(np.arange(24) % 2 == 0, 0.3, 0.1 * 3)
    assert len(set(level.tolist())) == 2
    design = np.column_stack([x[:, 0], level, x[:, 1]])
    if method is OLS:
        assert fit_error_code(design, y, OLS) == "constant_column"
    else:
        fit = fit_linear(design, y, method=LEGACY, feature_names=["a", "level", "b"])
        assert fit.diagnostics.constant_columns == ("level",)
        assert fit.diagnostics.rank == 2


def test_constant_column_is_accepted_and_flagged_by_legacy_min_norm() -> None:
    x, y = random_design(4, 25, 2)
    design = np.column_stack([x[:, 0], np.full(25, 0.1), x[:, 1]])
    fit = fit_linear(design, y, method=LEGACY, feature_names=["a", "level", "b"])
    assert fit.diagnostics.constant_columns == ("level",)
    assert fit.diagnostics.rank == 2
    assert not fit.diagnostics.identifiable
    assert fit.diagnostics.condition_number == math.inf
    assert abs(fit.coefficients[1]) < 1e-9
    rows = np.array([[1.0, 2.0], [-10.0, 0.5]])
    expected = reference_ols_predictions(x, y, rows)
    same_level = np.column_stack([rows[:, 0], np.full(2, 0.1), rows[:, 1]])
    np.testing.assert_allclose(fit.predict(same_level), expected, rtol=1e-10)
    assert fit.is_estimable(same_level).all()
    other_level = np.column_stack([rows[:, 0], np.full(2, 0.2), rows[:, 1]])
    assert not fit.is_estimable(other_level).any()
    with pytest.raises(ModelFitError, match="not identifiable"):
        fit.coefficient_table()


def test_all_constant_design_legacy_predicts_the_training_mean() -> None:
    x = np.column_stack([np.full(6, 2.0), np.full(6, -1.0)])
    y = np.array([1.0, 2.0, 4.0, 8.0, 16.0, 32.0])
    fit = fit_linear(x, y, method=LEGACY)
    diagnostics = fit.diagnostics
    assert diagnostics.constant_columns == ("x0", "x1")
    assert diagnostics.rank == 0
    assert diagnostics.solver_rank == 0
    assert diagnostics.condition_number == math.inf
    assert diagnostics.restricted_condition_number == math.inf
    assert diagnostics.smallest_kept_ratio == 0.0
    assert diagnostics.max_training_leverage == pytest.approx(1.0 / 6.0, rel=1e-12)
    assert fit.predict(np.array([2.0, -1.0])) == pytest.approx([10.5], rel=1e-12)
    assert fit.coefficients.tolist() == [0.0, 0.0]
    assert fit_error_code(x, y, OLS) == "constant_column"


@pytest.mark.parametrize("design", ["duplicate", "weekday_dummies", "sum_relation"])
def test_rank_deficient_design_is_refused_by_standardized_ols(design: str) -> None:
    if design == "duplicate":
        x, y = random_design(11, 40, 3)
        x = np.column_stack([x, x[:, 2]])
    elif design == "weekday_dummies":
        x, y = weekday_design()
    else:
        x, y = sum_relation_design()
    with pytest.raises(ModelFitError, match="rank") as excinfo:
        fit_linear(x, y, method=OLS)
    assert excinfo.value.code == "rank_deficient"


@pytest.mark.parametrize("method", BOTH)
@pytest.mark.parametrize(("rows", "columns"), [(0, 2), (1, 1), (3, 3), (2, 4), (5, 5)])
def test_too_few_training_rows_are_refused(rows: int, columns: int, method: FitMethod) -> None:
    rng = np.random.default_rng(rows * 10 + columns)
    x = rng.normal(size=(rows, columns))
    y = rng.normal(size=rows)
    assert fit_error_code(x, y, method) == "insufficient_training_rows"


@pytest.mark.parametrize("method", BOTH)
@pytest.mark.parametrize("columns", [1, 3, 6])
def test_one_more_row_than_columns_is_enough(columns: int, method: FitMethod) -> None:
    rng = np.random.default_rng(columns)
    x = rng.normal(size=(columns + 1, columns))
    y = rng.normal(size=columns + 1)
    fit = fit_linear(x, y, method=method)
    assert fit.diagnostics.rank == columns
    assert fit.in_sample_mae == pytest.approx(0.0, abs=1e-9)  # n = p + 1 interpolates
    # Every training row determines one direction: all hat values are 1.
    assert fit.leverage(x) == pytest.approx(np.ones(columns + 1), abs=1e-9)


def test_design_diagnostics_refuses_too_few_rows() -> None:
    with pytest.raises(ModelFitError) as excinfo:
        design_diagnostics(np.ones((2, 2)), ["a", "b"])
    assert excinfo.value.code == "insufficient_training_rows"


@pytest.mark.parametrize("method", BOTH)
@pytest.mark.parametrize(
    ("where", "value"),
    [("x", math.nan), ("x", math.inf), ("x", -math.inf), ("y", math.nan), ("y", math.inf)],
)
def test_non_finite_inputs_are_refused(where: str, value: float, method: FitMethod) -> None:
    x, y = random_design(8, 20, 2)
    if where == "x":
        x[7, 1] = value
    else:
        y[3] = value
    assert fit_error_code(x, y, method) == "non_finite"


@pytest.mark.parametrize("method", BOTH)
def test_misaligned_inputs_are_refused(method: FitMethod) -> None:
    x, y = random_design(9, 20, 2)
    assert fit_error_code(x, y[:-1], method) == "fit_error"
    assert fit_error_code(x, np.column_stack([y, y]), method) == "fit_error"
    assert fit_error_code(x[:, 0], y, method) == "fit_error"
    with pytest.raises(ModelFitError, match="feature_names"):
        fit_linear(x, y, method=method, feature_names=["only_one"])


@pytest.mark.parametrize("method", BOTH)
def test_rows_of_the_wrong_width_are_refused(method: FitMethod) -> None:
    x, y = random_design(10, 20, 3)
    fit = fit_linear(x, y, method=method)
    for rows in (np.ones((2, 2)), np.ones(4), np.ones((1, 5))):
        with pytest.raises(ModelFitError, match="expected 3 feature columns"):
            fit.predict(rows)
        with pytest.raises(ModelFitError, match="expected 3 feature columns"):
            fit.is_estimable(rows)
        with pytest.raises(ModelFitError, match="expected 3 feature columns"):
            fit.estimability_residual(rows)
        with pytest.raises(ModelFitError, match="expected 3 feature columns"):
            fit.leverage(rows)


@pytest.mark.parametrize("method", BOTH)
def test_leverage_of_a_simple_regression_by_hand(method: FitMethod) -> None:
    # h(x) = 1/n + (x - xbar)^2 / Sxx with xbar = 16/5 and Sxx = 314/5.
    x = np.array([[0.0], [1.0], [2.0], [3.0], [10.0]])
    y = np.array([1.0, 3.0, 2.0, 5.0, 4.0])
    fit = fit_linear(x, y, method=method)

    def hand(value: int) -> float:
        return float(Fraction(1, 5) + (value - Fraction(16, 5)) ** 2 / Fraction(314, 5))

    expected = [hand(v) for v in (0, 1, 2, 3, 10)]
    assert fit.leverage(x) == pytest.approx(expected, rel=1e-12)
    assert sum(expected) == pytest.approx(2.0)  # trace of the hat matrix = 1 + rank
    assert fit.diagnostics.max_training_leverage == pytest.approx(hand(10), rel=1e-12)
    assert fit.diagnostics.max_training_leverage > HIGH_LEVERAGE
    new = np.array([[20.0], [3.2]])
    assert fit.leverage(new) == pytest.approx([hand(20), 0.2], rel=1e-12)


@pytest.mark.parametrize("design", ["random", "weekday_dummies", "sum_relation", "constant"])
def test_training_leverage_is_the_hat_matrix_diagonal(design: str) -> None:
    if design == "random":
        x, y = random_design(21, 25, 4)
    elif design == "weekday_dummies":
        x, y = weekday_design(weeks=4)
    elif design == "sum_relation":
        x, y = sum_relation_design(rows=15)
    else:
        x, y = random_design(22, 12, 2)
        x = np.column_stack([x, np.full(12, 3.0)])
    fit = fit_linear(x, y, method=LEGACY)
    expected = hat_diagonal(x)
    assert fit.leverage(x) == pytest.approx(expected, abs=1e-9)
    assert fit.diagnostics.max_training_leverage == pytest.approx(expected.max(), abs=1e-9)
    # The trace of a projection is its rank: 1 (intercept) + the numerical rank.
    assert expected.sum() == pytest.approx(1 + fit.diagnostics.rank, abs=1e-9)


def test_prediction_leverage_is_the_pseudo_inverse_quadratic_form() -> None:
    x, y = random_design(23, 30, 3)
    rows = np.random.default_rng(24).normal(size=(5, 3)) * x.std(axis=0) * 3 + x.mean(axis=0)
    expected = new_row_leverage(x, rows)
    for method in BOTH:
        fit = fit_linear(x, y, method=method)
        assert fit.leverage(rows) == pytest.approx(expected, rel=1e-9)
    # Rank-deficient design: for estimable rows the quadratic form does not depend on the
    # generalised inverse, so it must agree with (A'A)^+ on the raw design.
    weekly, demand = weekday_design(weeks=6)
    legacy = fit_linear(weekly, demand, method=LEGACY)
    estimable = weekday_rows([42.0, 50.0, 100.0, -7.0], np.eye(7)[[0, 3, 6, 2]].tolist())
    assert legacy.is_estimable(estimable).all()
    assert legacy.leverage(estimable) == pytest.approx(
        new_row_leverage(weekly, estimable), rel=1e-9
    )


def test_a_row_that_alone_determines_a_direction_has_leverage_one() -> None:
    trend = np.arange(10.0)
    spike = np.zeros(10)
    spike[4] = 1.0
    x = np.column_stack([trend, spike])
    y = np.array([2.0, 4.0, 5.0, 8.0, 30.0, 12.0, 13.0, 15.0, 17.0, 20.0])
    for method in BOTH:
        fit = fit_linear(x, y, method=method)
        assert fit.diagnostics.max_training_leverage == pytest.approx(1.0, abs=1e-12)
        assert fit.leverage(x)[4] == pytest.approx(1.0, abs=1e-12)
        # The spike row is fitted exactly: its residual is zero.
        assert fit.predict(x[4])[0] == pytest.approx(30.0, abs=1e-9)


def test_coefficient_table_is_produced_only_for_identifiable_fits() -> None:
    x, y = random_design(30, 20, 2)
    full = fit_linear(x, y, method=LEGACY, feature_names=["a", "b"])
    assert list(full.coefficient_table().index) == ["a", "b"]
    assert full.coefficient_table().name == "coefficient"
    doubled = fit_linear(np.column_stack([x, x[:, 0]]), y, method=LEGACY)
    with pytest.raises(ModelFitError, match="rank 2 of 3"):
        doubled.coefficient_table()


# === stats.py =============================================================================


TABLE = [
    (0.975, 1, 12.7062047),
    (0.975, 5, 2.5705818),
    (0.975, 10, 2.2281389),
    (0.975, 30, 2.0422725),
    (0.995, 20, 2.8453397),
    (0.95, 1, 6.3137515),
    (0.9, 1, 3.0776835),
    (0.995, 1, 63.6567412),
    (0.975, 2, 4.3026527),
    (0.99, 3, 4.5407029),
    (0.975, 60, 2.0002978),
    (0.975, 120, 1.9799304),
]


@pytest.mark.parametrize(("probability", "df", "value"), TABLE)
def test_student_t_quantile_matches_published_tables(
    probability: float, df: int, value: float
) -> None:
    assert student_t_quantile(probability, df) == pytest.approx(value, abs=1e-6)
    assert student_t_quantile(1.0 - probability, df) == pytest.approx(-value, abs=1e-6)
    assert student_t_two_sided_p(value, df) == pytest.approx(2 * (1 - probability), abs=1e-6)
    assert student_t_quantile(probability, df) == pytest.approx(
        t_quantile_reference(probability, df), abs=1e-9
    )


@pytest.mark.parametrize("probability", [0.6, 0.75, 0.9, 0.975, 0.999, 0.25, 0.01])
def test_student_t_quantile_closed_forms_for_one_and_two_degrees_of_freedom(
    probability: float,
) -> None:
    cauchy = math.tan(math.pi * (probability - 0.5))
    two = (2 * probability - 1) / math.sqrt(2 * probability * (1 - probability))
    assert student_t_quantile(probability, 1) == pytest.approx(cauchy, rel=1e-10)
    assert student_t_quantile(probability, 2) == pytest.approx(two, rel=1e-10)


@settings(max_examples=20, deadline=None)
@given(
    statistic=st.floats(-60.0, 60.0, allow_nan=False),
    df=st.integers(1, 150),
)
def test_two_sided_p_matches_the_finite_sum_closed_form(statistic: float, df: int) -> None:
    expected = t_two_sided_reference(statistic, df)
    assert student_t_two_sided_p(statistic, df) == pytest.approx(
        expected, abs=p_tolerance(statistic, df)
    )
    assert student_t_two_sided_p(-statistic, df) == student_t_two_sided_p(statistic, df)


@pytest.mark.parametrize(
    ("statistic", "df", "expected"),
    [
        (1.0, 1, 0.5),  # Cauchy: 1 - (2 / pi) atan(1)
        (math.sqrt(3.0), 1, 1.0 / 3.0),  # 1 - (2 / pi)(pi / 3)
        (1.0, 2, 1.0 - 1.0 / math.sqrt(3.0)),  # 1 - t / sqrt(2 + t^2)
        (4.0, 2, 1.0 - 4.0 / math.sqrt(18.0)),
        (0.0, 7, 1.0),
    ],
)
def test_two_sided_p_by_hand(statistic: float, df: int, expected: float) -> None:
    assert student_t_two_sided_p(statistic, df) == pytest.approx(expected, abs=1e-14)


def test_two_sided_p_is_symmetric_and_decreasing() -> None:
    grid = [k / 4 for k in range(41)]
    for df in (1, 3, 10, 59, 500):
        values = [student_t_two_sided_p(t, df) for t in grid]
        assert values[0] == 1.0
        assert all(a > b for a, b in pairwise(values))
        assert [student_t_two_sided_p(-t, df) for t in grid] == values
    # Heavier tails for fewer degrees of freedom.
    for statistic in (1.0, 2.0, 3.0):
        by_df = [student_t_two_sided_p(statistic, df) for df in (1, 2, 5, 10, 30, 100)]
        assert all(a > b for a, b in pairwise(by_df))
    assert student_t_two_sided_p(math.inf, 4) == 0.0
    assert student_t_two_sided_p(-math.inf, 4) == 0.0


def test_student_t_quantile_is_monotone_and_antisymmetric() -> None:
    probabilities = [0.001, 0.01, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99, 0.999]
    for df in (1, 4, 29, 200):
        values = [student_t_quantile(p, df) for p in probabilities]
        assert all(a < b for a, b in pairwise(values))
        assert student_t_quantile(0.5, df) == 0.0
        for p in (0.6, 0.9, 0.975):
            assert student_t_quantile(1 - p, df) == pytest.approx(
                -student_t_quantile(p, df), rel=1e-12
            )
            assert student_t_two_sided_p(student_t_quantile(p, df), df) == pytest.approx(
                2 * (1 - p), abs=1e-12
            )
    by_df = [student_t_quantile(0.975, df) for df in range(1, 61)]
    assert all(a > b for a, b in pairwise(by_df))
    assert by_df[-1] > Z_975


def test_large_degrees_of_freedom_approach_the_normal() -> None:
    # Cornish-Fisher: t_p(nu) = z + (z^3 + z) / (4 nu) + (5 z^5 + 16 z^3 + 3 z) / (96 nu^2) + ...
    z = Z_975
    for df in (1e4, 1e5):
        expansion = z + (z**3 + z) / (4 * df) + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)
        assert student_t_quantile(0.975, df) == pytest.approx(expansion, abs=1e-9)
    assert student_t_quantile(0.975, 1e7) == pytest.approx(z, abs=1e-6)
    for statistic in (0.5, z, 3.0):
        normal = math.erfc(statistic / math.sqrt(2.0))
        assert student_t_two_sided_p(statistic, 1e7) == pytest.approx(normal, abs=1e-6)


@pytest.mark.parametrize("probability", [0.0, 1.0, -0.1, 1.5, math.nan])
def test_student_t_quantile_rejects_probabilities_outside_the_open_interval(
    probability: float,
) -> None:
    with pytest.raises(ForecastError, match="probability"):
        student_t_quantile(probability, 5)


@pytest.mark.parametrize("df", [0, -1, -0.5])
def test_non_positive_degrees_of_freedom_are_refused(df: float) -> None:
    with pytest.raises(ForecastError, match="degrees of freedom"):
        student_t_two_sided_p(1.0, df)
    with pytest.raises(ForecastError, match="degrees of freedom"):
        student_t_quantile(0.9, df)


def test_two_sided_p_of_a_nan_statistic_is_not_significant() -> None:
    try:
        p_value = student_t_two_sided_p(math.nan, 10)
    except ForecastError:
        return
    assert math.isnan(p_value)


@pytest.mark.parametrize("x", [0.0, 0.05, 0.3, 0.5, 0.77, 0.999, 1.0])
def test_incomplete_beta_closed_forms(x: float) -> None:
    assert regularized_incomplete_beta(1.0, 1.0, x) == pytest.approx(x, abs=1e-14)
    for a in (0.5, 2.0, 3.7, 12.0):
        assert regularized_incomplete_beta(a, 1.0, x) == pytest.approx(x**a, abs=1e-13)
        assert regularized_incomplete_beta(1.0, a, x) == pytest.approx(
            1.0 - (1.0 - x) ** a, abs=1e-13
        )
    arcsine = 2.0 / math.pi * math.asin(math.sqrt(x))
    assert regularized_incomplete_beta(0.5, 0.5, x) == pytest.approx(arcsine, abs=1e-13)
    assert regularized_incomplete_beta(2.0, 2.0, x) == pytest.approx(3 * x**2 - 2 * x**3, abs=1e-14)


@settings(max_examples=20, deadline=None)
@given(
    a=st.integers(1, 30),
    b=st.integers(1, 30),
    x=st.floats(0.0, 1.0, allow_nan=False),
)
def test_incomplete_beta_equals_the_binomial_tail_for_integer_parameters(
    a: int, b: int, x: float
) -> None:
    assert regularized_incomplete_beta(a, b, x) == pytest.approx(binomial_tail(a, b, x), abs=1e-12)


@settings(max_examples=20, deadline=None)
@given(
    a=st.floats(0.05, 60.0),
    b=st.floats(0.05, 60.0),
    # Dyadic x so that 1 - x is exact (otherwise the identity fails by rounding of 1 - x).
    x=st.integers(0, 2**20).map(lambda k: k / 2**20),
)
def test_incomplete_beta_reflection_identity(a: float, b: float, x: float) -> None:
    value = regularized_incomplete_beta(a, b, x)
    assert 0.0 <= value <= 1.0
    assert value == pytest.approx(1.0 - regularized_incomplete_beta(b, a, 1.0 - x), abs=1e-12)


def test_incomplete_beta_is_increasing_in_x_and_exact_at_the_ends() -> None:
    for a, b in ((0.5, 0.5), (2.0, 7.0), (30.0, 0.5), (100.0, 100.0)):
        grid = [k / 50 for k in range(51)]
        values = [regularized_incomplete_beta(a, b, x) for x in grid]
        assert values[0] == 0.0
        assert values[-1] == 1.0
        assert all(u <= v for u, v in pairwise(values))


@pytest.mark.parametrize(
    ("a", "b", "x"),
    [
        (0.0, 1.0, 0.5),
        (-1.0, 1.0, 0.5),
        (1.0, 0.0, 0.5),
        (1.0, -2.0, 0.5),
        (1.0, 1.0, -0.1),
        (1.0, 1.0, 1.1),
        (1.0, 1.0, math.nan),
    ],
)
def test_incomplete_beta_rejects_invalid_arguments(a: float, b: float, x: float) -> None:
    with pytest.raises(ForecastError, match="invalid incomplete beta"):
        regularized_incomplete_beta(a, b, x)


# --- long_run_variance -------------------------------------------------------------------

# values 1, 2, 4, 3, 5: mean 3, centred -2, -1, 1, 0, 2; gamma_0 = 10/5 = 2,
# gamma_1 = (2 - 1 + 0 + 0)/5 = 0.2, gamma_2 = (-2 + 0 + 2)/5 = 0, gamma_3 = (0 - 2)/5 = -0.4,
# gamma_4 = -4/5 = -0.8.
HAND_SERIES = [1.0, 2.0, 4.0, 3.0, 5.0]


@pytest.mark.parametrize(
    ("bandwidth", "kernel", "expected"),
    [
        (0, "rectangular", Fraction(2)),
        (0, "bartlett", Fraction(2)),
        (1, "rectangular", Fraction(12, 5)),  # 2 + 2 (0.2)
        (1, "bartlett", Fraction(11, 5)),  # 2 + 2 (1/2)(0.2)
        (3, "rectangular", Fraction(8, 5)),  # 2 + 2 (0.2 + 0 - 0.4)
        (3, "bartlett", Fraction(21, 10)),  # 2 + 2 (0.75 * 0.2 + 0.25 * -0.4)
        # Full bandwidth: gamma_0 + 2 sum(gamma_k) = (sum of centred values)^2 / n = 0.
        (4, "rectangular", Fraction(0)),
        (10, "rectangular", Fraction(0)),  # bandwidth beyond the length: lags stop at n - 1
        # Bartlett weights keep the requested bandwidth: 1 - k / 11 for k = 1..4.
        (10, "bartlett", Fraction(42, 55)),  # 2 + 2 (10*0.2 + 8*-0.4 + 7*-0.8) / 11 = 8.4/11
    ],
)
def test_long_run_variance_by_hand(bandwidth: int, kernel: str, expected: Fraction) -> None:
    assert lrv_exact([Fraction(v) for v in HAND_SERIES], bandwidth, kernel) == expected
    value = long_run_variance(HAND_SERIES, bandwidth, kernel)  # type: ignore[arg-type]
    assert value == pytest.approx(float(expected), abs=1e-14)


def test_long_run_variance_defaults_to_bartlett_and_accepts_arrays() -> None:
    assert long_run_variance(np.array(HAND_SERIES), 3) == pytest.approx(2.1, abs=1e-14)
    assert long_run_variance([7.5], 3, "rectangular") == 0.0
    # The rectangular estimate can be negative (alternating series): 1 + 2 (-3/4) = -1/2.
    assert long_run_variance([1.0, -1.0, 1.0, -1.0], 1, "rectangular") == pytest.approx(-0.5)
    assert long_run_variance([1.0, -1.0, 1.0, -1.0], 1, "bartlett") == pytest.approx(0.25)


@settings(max_examples=20, deadline=None)
@given(
    seed=st.integers(0, 2**32 - 1),
    size=st.integers(1, 40),
    bandwidth=st.integers(0, 50),
)
def test_long_run_variance_matches_the_plain_reference(
    seed: int, size: int, bandwidth: int
) -> None:
    rng = np.random.default_rng(seed)
    values = [float(v) for v in np.cumsum(rng.normal(size=size)) * 10.0]
    scale = max(1.0, max(abs(v) for v in values)) ** 2
    for kernel in ("rectangular", "bartlett"):
        expected = lrv_reference(values, bandwidth, kernel)
        actual = long_run_variance(values, bandwidth, kernel)  # type: ignore[arg-type]
        assert actual == pytest.approx(expected, abs=1e-12 * scale)
    assert long_run_variance(values, bandwidth, "bartlett") >= -1e-12 * scale


def test_long_run_variance_rejects_empty_series_and_negative_bandwidth() -> None:
    with pytest.raises(ForecastError, match="empty"):
        long_run_variance([], 1)
    with pytest.raises(ForecastError, match="bandwidth"):
        long_run_variance([1.0, 2.0], -1)


# --- diebold_mariano ---------------------------------------------------------------------


def square_wave(size: int = 60, shift: float = 0.25) -> list[float]:
    """Blocks of three -1 then three +1, shifted: mean exactly ``shift`` for ``size % 6 == 0``."""
    return [(1.0 if (t // 3) % 2 else -1.0) + shift for t in range(size)]


def alternating(size: int = 60, shift: float = 0.03125) -> list[float]:
    return [(1.0 if t % 2 == 0 else -1.0) + shift for t in range(size)]


def assert_matches_reference(values: list[float], horizon: int, level: float = 0.95) -> None:
    result = diebold_mariano(values, horizon=horizon, level=level)
    expected = dm_reference(values, horizon, level)
    assert result.n == len(values)
    assert result.horizon == horizon
    assert result.mean_difference == pytest.approx(expected["mean"], rel=1e-12, abs=1e-15)
    assert result.computed is expected["computed"]
    if not result.computed:
        assert result.statistic is None
        assert result.p_value is None
        return
    assert result.estimator == expected["estimator"]
    for name in ("statistic", "bartlett_statistic", "ci_low", "ci_high"):
        assert getattr(result, name) == pytest.approx(expected[name], rel=1e-9, abs=1e-12)
    smallest = min(abs(float(expected[n])) for n in ("statistic", "bartlett_statistic"))  # type: ignore[arg-type]
    tolerance = 1e-10 + p_tolerance(smallest, len(values) - 1)
    for name in ("p_value", "bartlett_p_value", "p_value_conservative"):
        assert getattr(result, name) == pytest.approx(expected[name], abs=tolerance)


def test_diebold_mariano_square_wave_by_hand() -> None:
    # Centred values are exactly +-1 (blocks of three): gamma_k = (pairs alike - pairs unlike)/60.
    values = square_wave()
    size, horizon = 60, 3
    exact = [Fraction(v) for v in values]
    variance = lrv_exact(exact, horizon - 1, "rectangular")
    # lag 1: 40 alike, 19 unlike pairs; lag 2: 20 alike, 38 unlike pairs -> 1 + 2 (21 - 18)/60.
    assert variance == Fraction(1) + 2 * (Fraction(40 - 19, 60) + Fraction(20 - 38, 60))
    bartlett = lrv_exact(exact, 2 * horizon, "bartlett")
    correction = (size + 1 - 2 * horizon + Fraction(horizon * (horizon - 1), size)) / size
    error = math.sqrt(float(variance / size / correction))
    statistic = 0.25 / error
    bartlett_statistic = 0.25 / math.sqrt(float(bartlett / size / correction))
    quantile = t_quantile_reference(0.975, size - 1)
    result = diebold_mariano(values, horizon=horizon)
    assert result.computed
    assert result.estimator == "rectangular"
    assert result.mean_difference == 0.25
    assert result.statistic == pytest.approx(statistic, rel=1e-12)
    assert result.p_value == pytest.approx(t_two_sided_reference(statistic, 59), abs=1e-12)
    assert result.ci_low == pytest.approx(0.25 - quantile * error, rel=1e-10)
    assert result.ci_high == pytest.approx(0.25 + quantile * error, rel=1e-10)
    assert result.bartlett_statistic == pytest.approx(bartlett_statistic, rel=1e-12)
    bartlett_p = t_two_sided_reference(bartlett_statistic, 59)
    assert result.bartlett_p_value == pytest.approx(bartlett_p, abs=1e-12)
    # Here the main estimate is the less significant one, so it is the conservative p-value.
    assert result.p_value is not None
    assert result.bartlett_p_value is not None
    assert result.p_value > result.bartlett_p_value
    assert result.p_value_conservative == result.p_value


def test_diebold_mariano_falls_back_to_bartlett_when_the_rectangular_estimate_is_negative() -> None:
    # Alternating +-1 around 1/32, h = 2: gamma_0 = 1, gamma_1 = -59/60, so the rectangular
    # estimate 1 - 2 * 59/60 < 0; Bartlett (bandwidth 1): 1 + 2 * (1/2) * (-59/60) = 1/60.
    # Sensitivity, Bartlett bandwidth 2h = 4: gamma_k = (-1)^k (60 - k)/60 ->
    # 1 + 2 * (-4/5 * 59 + 3/5 * 58 - 2/5 * 57 + 1/5 * 56) / 60 = 1/5.
    values = alternating()
    size, horizon = 60, 2
    exact = [Fraction(v) for v in values]
    assert lrv_exact(exact, 1, "rectangular") < 0
    assert lrv_exact(exact, 1, "bartlett") == Fraction(1, 60)
    assert lrv_exact(exact, 4, "bartlett") == Fraction(1, 5)
    correction = hln_correction(size, horizon)
    error = math.sqrt(1 / 60 / size / correction)
    statistic = (1 / 32) / error
    bartlett_statistic = (1 / 32) / math.sqrt(1 / 5 / size / correction)
    result = diebold_mariano(values, horizon=horizon)
    assert result.computed
    assert result.estimator == "bartlett_fallback"
    assert result.mean_difference == 1 / 32
    assert result.statistic == pytest.approx(statistic, rel=1e-12)
    assert result.p_value == pytest.approx(t_two_sided_reference(statistic, 59), abs=1e-12)
    assert result.bartlett_statistic == pytest.approx(bartlett_statistic, rel=1e-12)
    bartlett_p = t_two_sided_reference(bartlett_statistic, 59)
    assert result.bartlett_p_value == pytest.approx(bartlett_p, abs=1e-12)
    # Here the sensitivity estimate is the less significant one.
    assert result.p_value is not None
    assert bartlett_p > result.p_value
    assert result.p_value_conservative == result.bartlett_p_value


def test_diebold_mariano_at_horizon_one_is_the_one_sample_t_test() -> None:
    # h = 1: bandwidth 0 and HLN factor (T - 1)/T, so the statistic is mean / (s / sqrt(T))
    # with the sample standard deviation s (ddof 1).
    values = [math.sin(1.3 * t) + 0.15 for t in range(55)]
    size = len(values)
    mean = math.fsum(values) / size
    error = statistics.stdev(values) / math.sqrt(size)
    quantile = t_quantile_reference(0.975, size - 1)
    result = diebold_mariano(values, horizon=1)
    assert result.estimator == "rectangular"
    assert result.statistic == pytest.approx(mean / error, rel=1e-12)
    assert result.p_value == pytest.approx(t_two_sided_reference(mean / error, 54), abs=1e-12)
    assert result.ci_low == pytest.approx(mean - quantile * error, rel=1e-10)
    assert result.ci_high == pytest.approx(mean + quantile * error, rel=1e-10)
    flipped = diebold_mariano([-v for v in values], horizon=1)
    assert flipped.statistic == pytest.approx(-mean / error, rel=1e-12)
    assert flipped.p_value == result.p_value


def test_diebold_mariano_confidence_level_sets_the_quantile() -> None:
    values = square_wave()
    wide = diebold_mariano(values, horizon=3)
    narrow = diebold_mariano(values, horizon=3, level=0.8)
    assert wide.ci_low is not None
    assert narrow.ci_low is not None
    ratio = (0.25 - narrow.ci_low) / (0.25 - wide.ci_low)
    expected = t_quantile_reference(0.9, 59) / t_quantile_reference(0.975, 59)
    assert ratio == pytest.approx(expected, rel=1e-10)
    assert narrow.statistic == wide.statistic


@settings(max_examples=20, deadline=None)
@given(
    seed=st.integers(0, 2**32 - 1),
    size=st.integers(40, 100),
    horizon=st.integers(1, 34),
    persistence=st.sampled_from([-0.7, 0.0, 0.8]),
)
def test_diebold_mariano_matches_the_plain_reference(
    seed: int, size: int, horizon: int, persistence: float
) -> None:
    rng = np.random.default_rng(seed)
    shocks = rng.normal(size=size)
    values: list[float] = []
    previous = 0.0
    for shock in shocks:
        previous = persistence * previous + float(shock)
        values.append(previous + 0.2)
    assert_matches_reference(values, horizon)


def test_diebold_mariano_is_not_computed_on_an_origin_grid_with_gaps() -> None:
    values = square_wave(120)
    result = diebold_mariano(values, horizon=3, contiguous=False)
    assert not result.computed
    assert result.reason == "origin grid has gaps"
    assert result.n == 120
    assert result.mean_difference == 0.25
    assert result.statistic is None
    assert result.p_value is None
    assert result.p_value_conservative is None
    assert result.estimator is None
    empty = diebold_mariano([], horizon=1, contiguous=False)
    assert empty.n == 0
    assert empty.mean_difference is None


@pytest.mark.parametrize(
    ("horizon", "required"),
    [(1, 50), (16, 50), (17, 51), (20, 60), (30, 90)],
)
def test_diebold_mariano_needs_max_of_3h_and_50_origins(horizon: int, required: int) -> None:
    assert MIN_ORIGINS_FLOOR == 50
    assert min_origins(horizon) == required
    values = [math.sin(0.7 * t) + 0.05 * (t % 5) for t in range(required)]
    short = diebold_mariano(values[:-1], horizon=horizon)
    assert not short.computed
    assert short.reason == f"fewer than {required} origins"
    assert short.n == required - 1
    assert short.mean_difference == pytest.approx(math.fsum(values[:-1]) / (required - 1))
    assert short.p_value is None
    assert diebold_mariano(values, horizon=horizon).computed


@pytest.mark.parametrize("value", [0.0, 0.25, -3.0, 1024.0])
def test_diebold_mariano_is_not_computed_for_a_constant_differential(value: float) -> None:
    result = diebold_mariano([value] * 64, horizon=2)
    assert not result.computed
    assert result.reason == "loss differential has no variance"
    assert result.mean_difference == value
    assert result.p_value is None


def test_diebold_mariano_constant_differential_with_an_inexact_mean_has_no_variance() -> None:
    result = diebold_mariano([0.1] * 60, horizon=1)
    assert not result.computed
    assert result.reason == "loss differential has no variance"


def test_diebold_mariano_does_not_report_a_test_for_a_non_finite_differential() -> None:
    values = square_wave()
    values[30] = math.nan
    try:
        result = diebold_mariano(values, horizon=1)
    except ForecastError:
        return
    assert not result.computed


@pytest.mark.parametrize("horizon", [0, -1, True, False])
def test_diebold_mariano_rejects_invalid_horizons(horizon: int) -> None:
    with pytest.raises(ForecastError, match="horizon"):
        diebold_mariano(square_wave(), horizon=horizon)


def test_diebold_mariano_to_dict_lists_every_field() -> None:
    result = diebold_mariano(square_wave(), horizon=3)
    record = result.to_dict()
    assert record["n"] == 60
    assert record["horizon"] == 3
    assert record["computed"] is True
    assert record["estimator"] == "rectangular"
    assert record["p_value_conservative"] == result.p_value_conservative
    assert set(record) == {
        "n",
        "horizon",
        "mean_difference",
        "computed",
        "reason",
        "estimator",
        "statistic",
        "p_value",
        "ci_low",
        "ci_high",
        "bartlett_statistic",
        "bartlett_p_value",
        "p_value_conservative",
    }


# --- holm --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("p_values", "expected"),
    [
        # m = 4 present values (None is not a test): 4 * 0.01, 3 * 0.03, max(0.09, 2 * 0.04), 0.5.
        ([0.01, 0.04, 0.03, None, 0.5], [0.04, 0.09, 0.09, None, 0.5]),
        ([0.4, 0.7], [0.8, 0.8]),  # step-down maximum keeps the order
        ([0.6, 0.9], [1.0, 1.0]),  # capped at one
        ([0.02, 0.5, 0.02], [0.06, 0.5, 0.06]),  # ties get the same adjustment
        ([None, 0.03], [None, 0.03]),
        ([0.03], [0.03]),
        ([None, None], [None, None]),
        ([], []),
    ],
)
def test_holm_by_hand(p_values: list[float | None], expected: list[float | None]) -> None:
    adjusted = holm(p_values)
    assert len(adjusted) == len(expected)
    for value, target in zip(adjusted, expected, strict=True):
        if target is None:
            assert value is None
        else:
            assert value == pytest.approx(target, abs=1e-15)


@settings(max_examples=20, deadline=None)
@given(
    st.lists(
        st.one_of(st.none(), st.floats(0.0, 1.0, allow_nan=False)),
        min_size=0,
        max_size=12,
    )
)
def test_holm_matches_the_step_down_reference_and_is_monotone(
    p_values: list[float | None],
) -> None:
    adjusted = holm(p_values)
    expected = holm_reference(p_values)
    assert [a is None for a in adjusted] == [p is None for p in p_values]
    for value, target in zip(adjusted, expected, strict=True):
        assert value == target or value == pytest.approx(target, abs=1e-15)
    present = [(p, a) for p, a in zip(p_values, adjusted, strict=True) if p is not None]
    for p, a in present:
        assert a is not None
        assert p <= a <= 1.0
    for p, a in present:
        for q, b in present:
            assert a is not None
            assert b is not None
            if p <= q:
                assert a <= b
