"""Linear least squares with explicit rank, conditioning and estimability diagnostics (M2).

Two fitting methods share one diagnostic computation:

``legacy_min_norm``
    The legacy notebook's ``sklearn.linear_model.LinearRegression()``: centre ``X`` and
    ``y``, solve with LAPACK ``gelsd`` (``numpy.linalg.lstsq``, cut-off
    ``max(n, p) * eps`` as in scikit-learn >= 1.2), intercept ``mean(y) - mean(X) @ b``.
    A rank-deficient design is accepted, exactly as scikit-learn accepts it, and the
    minimum-norm solution is returned. Its coefficients are then **not identifiable** and
    must not be interpreted; :meth:`LinearFit.coefficient_table` refuses to produce them.
``standardized_ols``
    Ordinary least squares on the centred design whose columns are scaled to unit
    standard deviation (training statistics only), solved by SVD. A constant column or a
    numerically rank-deficient design is refused with :class:`ModelFitError`, so every
    accepted fit has identifiable coefficients.

Diagnostics are computed on the centred, column-standardised design ``Z`` for both
methods, so the numerical rank and the condition number do not depend on the units of
the features (lags of about 10,000 next to 0/1 indicators):

* ``rank``: number of singular values of ``Z`` above ``max(n, p) * eps * s_max``
  (``solver_rank`` is the rank LAPACK used on the unscaled design; they should agree);
* ``condition_number``: ``s_max / s_min`` of ``Z`` (infinite when rank-deficient), and
  ``restricted_condition_number``: ``s_max / s_rank`` over the subspace the solution
  actually uses. For a rank-deficient design the first is always infinite and says
  nothing; the second shows whether the retained directions are themselves fragile;
* ``smallest_kept_ratio`` / ``largest_dropped_ratio``: ``s / s_max`` of the weakest
  retained and the strongest discarded singular value (a wide gap = an unambiguous rank);
* ``max_training_leverage``: the largest diagonal element of the hat matrix (with the
  intercept). Close to 1 means one training row alone determines a direction;
* estimability of a prediction row ``x``: with ``N`` the numerical null space of ``Z``,
  the prediction ``x @ b`` is the same for *every* least-squares solution ``b`` only when
  the standardised row has no component in ``N``. A rank-deficient fit can still give
  well-defined predictions (the legacy weekday dummies always sum to one); a row that
  breaks one of the training design's exact linear relations cannot;
* prediction leverage ``1/n + z' (Z'Z)^+ z``: how far a prediction row lies from the
  training rows in the retained directions. Above ``HIGH_LEVERAGE`` the prediction
  depends on very few training rows.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Final

import numpy as np
import pandas as pd

from atlas.errors import ModelFitError

ESTIMABILITY_TOLERANCE: Final = 1e-6
HIGH_LEVERAGE: Final = 0.5
_EPS: Final = float(np.finfo(np.float64).eps)


class FitMethod(StrEnum):
    LEGACY_MIN_NORM = "legacy_min_norm"
    STANDARDIZED_OLS = "standardized_ols"


@dataclass(frozen=True)
class DesignDiagnostics:
    """Rank and conditioning of a training design (intercept handled by centring)."""

    n_rows: int
    n_columns: int
    rank: int
    condition_number: float
    restricted_condition_number: float
    smallest_kept_ratio: float
    largest_dropped_ratio: float
    max_training_leverage: float
    constant_columns: tuple[str, ...]
    solver_rank: int | None = None

    @property
    def identifiable(self) -> bool:
        """True when every coefficient is uniquely determined by the data."""
        return self.rank == self.n_columns and not self.constant_columns


@dataclass(frozen=True)
class LinearFit:
    """A fitted linear model ``y = intercept + X @ coefficients`` and its diagnostics."""

    method: FitMethod
    feature_names: tuple[str, ...]
    coefficients: np.ndarray = field(repr=False)
    intercept: float
    diagnostics: DesignDiagnostics
    in_sample_mae: float
    in_sample_rmse: float
    _center: np.ndarray = field(repr=False)
    _scale: np.ndarray = field(repr=False)
    _null_space: np.ndarray = field(repr=False)  # (p, p - rank), standardised coordinates
    _leverage_map: np.ndarray = field(repr=False)  # (p, rank): V_r / s_r, standardised

    def predict(self, rows: np.ndarray) -> np.ndarray:
        """Predictions for a matrix of rows (or one row) in feature order."""
        matrix = np.atleast_2d(np.asarray(rows, dtype="float64"))
        self._check_width(matrix)
        return np.asarray(self.intercept + matrix @ self.coefficients, dtype="float64")

    def estimability_residual(self, rows: np.ndarray) -> np.ndarray:
        """Relative size of each row's component in the null space (0 = estimable)."""
        matrix = np.atleast_2d(np.asarray(rows, dtype="float64"))
        self._check_width(matrix)
        if self._null_space.shape[1] == 0:
            return np.zeros(len(matrix))
        standardized = (matrix - self._center) / self._scale
        outside = np.linalg.norm(standardized @ self._null_space, axis=1)
        return np.asarray(outside / np.maximum(1.0, np.linalg.norm(standardized, axis=1)))

    def is_estimable(self, rows: np.ndarray) -> np.ndarray:
        """True for rows whose prediction does not depend on the least-squares solution chosen."""
        return np.asarray(self.estimability_residual(rows) <= ESTIMABILITY_TOLERANCE)

    def leverage(self, rows: np.ndarray) -> np.ndarray:
        """Prediction leverage ``1/n + z' (Z'Z)^+ z`` of each row (see the module docstring)."""
        matrix = np.atleast_2d(np.asarray(rows, dtype="float64"))
        self._check_width(matrix)
        standardized = (matrix - self._center) / self._scale
        projected = standardized @ self._leverage_map
        return np.asarray(1.0 / self.diagnostics.n_rows + (projected**2).sum(axis=1))

    def coefficient_table(self) -> pd.Series:
        """Coefficients by feature name, in original units; refused when not identifiable."""
        if not self.diagnostics.identifiable:
            raise ModelFitError(
                "coefficients are not identifiable (rank "
                f"{self.diagnostics.rank} of {self.diagnostics.n_columns}); they must not be "
                "reported or interpreted"
            )
        return pd.Series(self.coefficients, index=list(self.feature_names), name="coefficient")

    def _check_width(self, matrix: np.ndarray) -> None:
        if matrix.shape[1] != len(self.feature_names):
            raise ModelFitError(
                f"expected {len(self.feature_names)} feature columns, got {matrix.shape[1]}"
            )


def design_diagnostics(
    x: np.ndarray, feature_names: Sequence[str]
) -> tuple[DesignDiagnostics, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Diagnostics of a design plus its centring, scaling, null space and leverage map."""
    n_rows, n_columns = x.shape
    if n_rows <= n_columns:
        raise ModelFitError(
            f"{n_rows} rows cannot support {n_columns} columns and an intercept",
            "insufficient_training_rows",
        )
    center = x.mean(axis=0)
    centered = x - center
    spread = centered.std(axis=0)
    # A column is constant when its spread is negligible relative to its magnitude.
    constant = spread <= 1e-12 * np.maximum(1.0, np.abs(center))
    scale = np.where(constant, 1.0, spread)
    standardized = np.where(constant, 0.0, centered / scale)
    # Callers guarantee n_rows > n_columns, so the reduced SVD has a square p x p ``vt``.
    u, singular, vt = np.linalg.svd(standardized, full_matrices=False)
    largest = float(singular.max()) if singular.size else 0.0
    tolerance = max(n_rows, n_columns) * _EPS * largest
    rank = int((singular > tolerance).sum())
    if rank == n_columns and rank > 0:
        condition = float(singular.max() / singular.min())
    else:
        condition = float("inf")
    kept = singular[:rank]
    dropped = singular[rank:]
    null_space = vt[rank:].T  # orthonormal basis of the numerical null space
    leverage_map = vt[:rank].T / kept if rank else np.zeros((n_columns, 0))
    training_leverage = 1.0 / n_rows + (u[:, :rank] ** 2).sum(axis=1)
    diagnostics = DesignDiagnostics(
        n_rows=n_rows,
        n_columns=n_columns,
        rank=rank,
        condition_number=condition,
        restricted_condition_number=float(largest / kept[-1]) if rank else float("inf"),
        smallest_kept_ratio=float(kept[-1] / largest) if rank else 0.0,
        largest_dropped_ratio=float(dropped[0] / largest) if dropped.size and largest else 0.0,
        max_training_leverage=float(training_leverage.max()),
        constant_columns=tuple(name for name, c in zip(feature_names, constant, strict=True) if c),
    )
    return diagnostics, center, scale, null_space, leverage_map


def _lstsq(design: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, int]:
    """Minimum-norm least squares (LAPACK gelsd, cut-off ``max(n, p) * eps``) and its rank."""
    try:
        solution, _, rank, _ = np.linalg.lstsq(design, target, rcond=None)
    except np.linalg.LinAlgError as exc:
        raise ModelFitError(f"least squares failed: {exc}", "linalg_error") from exc
    return np.asarray(solution, dtype="float64"), int(rank)


def fit_linear(
    x: np.ndarray | pd.DataFrame,
    y: np.ndarray | pd.Series,
    *,
    method: FitMethod,
    feature_names: Sequence[str] | None = None,
) -> LinearFit:
    """Fit ``y`` on ``x`` with an intercept; see the module docstring for the two methods."""
    method = FitMethod(method)
    if isinstance(x, pd.DataFrame):
        feature_names = tuple(str(c) for c in x.columns) if feature_names is None else feature_names
    matrix = np.asarray(x, dtype="float64")
    target = np.asarray(y, dtype="float64")
    if matrix.ndim != 2 or target.ndim != 1 or len(matrix) != len(target):
        raise ModelFitError(f"design {matrix.shape} and target {target.shape} do not align")
    names = (
        tuple(feature_names)
        if feature_names is not None
        else tuple(f"x{i}" for i in range(matrix.shape[1]))
    )
    if len(names) != matrix.shape[1]:
        raise ModelFitError("feature_names must match the number of columns")
    if not np.isfinite(matrix).all() or not np.isfinite(target).all():
        raise ModelFitError(
            "the training design and target must be finite (drop incomplete rows)", "non_finite"
        )
    if len(matrix) <= matrix.shape[1]:
        raise ModelFitError(
            f"{len(matrix)} training rows cannot determine {matrix.shape[1]} coefficients and "
            "an intercept",
            "insufficient_training_rows",
        )

    try:
        diagnostics, center, scale, null_space, leverage_map = design_diagnostics(matrix, names)
    except np.linalg.LinAlgError as exc:  # SVD did not converge
        raise ModelFitError(f"singular value decomposition failed: {exc}", "linalg_error") from exc
    target_mean = float(target.mean())
    if method is FitMethod.LEGACY_MIN_NORM:
        coefficients, solver_rank = _lstsq(matrix - center, target - target_mean)
        diagnostics = replace(diagnostics, solver_rank=solver_rank)
    else:
        if diagnostics.constant_columns:
            raise ModelFitError(
                f"constant feature column(s) {list(diagnostics.constant_columns)}: their "
                "coefficients are not identifiable",
                "constant_column",
            )
        if not diagnostics.identifiable:
            raise ModelFitError(
                f"rank-deficient design (rank {diagnostics.rank} of {diagnostics.n_columns}); "
                "coefficients are not identifiable",
                "rank_deficient",
            )
        standardized = (matrix - center) / scale
        solution, solver_rank = _lstsq(standardized, target - target_mean)
        diagnostics = replace(diagnostics, solver_rank=solver_rank)
        coefficients = solution / scale
    coefficients = np.asarray(coefficients, dtype="float64")
    intercept = float(target_mean - center @ coefficients)
    residuals = target - (intercept + matrix @ coefficients)
    return LinearFit(
        method=method,
        feature_names=names,
        coefficients=coefficients,
        intercept=intercept,
        diagnostics=diagnostics,
        in_sample_mae=float(np.abs(residuals).mean()),
        in_sample_rmse=float(np.sqrt((residuals**2).mean())),
        _center=center,
        _scale=scale,
        _null_space=null_space,
        _leverage_map=leverage_map,
    )
