"""Comparing recomputed forecasts with references committed from another machine.

Two kinds of reproducibility are checked in this repository, with different rules:

* **Same environment** (one machine, one process or one CI job): outputs must be
  byte-identical. ``atlas backtest verify`` and ``atlas uncertainty verify`` compare SHA-256
  hashes, and the engine tests compare frames exactly. Nothing here relaxes that.
* **Across environments** (a reference produced on one CPU, recomputed on another): the
  locked numpy wheel bundles OpenBLAS with runtime CPU dispatch. The same code, data and
  package versions select different kernels on different CPUs (AVX-512 on the machine
  that produced the M2 references, AVX2 on some GitHub-hosted runners), and least-squares
  results then differ in the last bits. Measured on the M2 spot check: up to 7 units in the
  last place (1.5e-15 relative, 1.2e-11 requests per day) for the regressions, and none
  for the baselines, which do not call BLAS.

:func:`assert_matches_reference` therefore requires exact equality for models that do
not use BLAS and allows a relative difference of :data:`CROSS_ENVIRONMENT_RTOL` (the M2
policy, ``tests/test_m2_results.py``) for those that do. At a demand of about 10,000
requests per day, 1e-9 is 0.00001 requests: six orders of magnitude above the observed
kernel noise and five orders below a forecast that is wrong by a tenth of a request.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import numpy as np

CROSS_ENVIRONMENT_RTOL: Final = 1e-9


def assert_matches_reference(
    actual: Sequence[float] | np.ndarray,
    expected: Sequence[float] | np.ndarray,
    *,
    uses_blas: bool,
    what: str = "predictions",
) -> None:
    """Assert recomputed values equal a committed reference (see the module docstring).

    Shapes and the positions of missing values must agree exactly in every case.
    ``uses_blas`` False requires bit-identical values; True allows a relative difference of
    :data:`CROSS_ENVIRONMENT_RTOL` and no absolute slack.
    """
    mine = np.asarray(actual, dtype="float64")
    reference = np.asarray(expected, dtype="float64")
    if mine.shape != reference.shape:
        raise AssertionError(f"{what}: shape {mine.shape} differs from reference {reference.shape}")
    if not np.array_equal(np.isnan(mine), np.isnan(reference)):
        raise AssertionError(f"{what}: missing values differ from the reference")
    if not uses_blas:
        np.testing.assert_array_equal(mine, reference, err_msg=f"{what} (exact: no BLAS)")
        return
    np.testing.assert_allclose(
        mine,
        reference,
        rtol=CROSS_ENVIRONMENT_RTOL,
        atol=0.0,
        equal_nan=True,
        err_msg=f"{what} (cross-environment tolerance {CROSS_ENVIRONMENT_RTOL:g} relative)",
    )
