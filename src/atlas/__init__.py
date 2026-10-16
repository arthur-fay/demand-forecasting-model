"""ATLAS: reproducible foundation for NYC 311 demand analysis and decision support.

Milestone M0 provides the engineering base: an immutable registry of the legacy
data snapshot, strict offline loading, fail-closed data contracts, and
reproduction of the historical summaries published by the original project.

Milestone M1 adds ISO 8601 calendar conventions, an explicit reconciliation residual,
causal data-quality flags, and one leakage-safe feature builder shared by training,
direct forecasting and recursive forecasting.

Milestone M2 adds forecasting models behind one typed interface, a rolling-origin
backtesting engine, forecast-evaluation statistics and reproducible experiment records.

Milestone M3 adds horizon-specific prediction intervals and threshold-exceedance
probabilities calibrated only on residuals observed by each issue date, with a
pre-registered selection, a dependence-aware evaluation and a decision-grade status.

Milestone M4 adds a hypothetical decision simulation: a linear programme (SciPy HiGHS) that
turns the M2 forecast into resource-allocation scenarios under explicitly HYPOTHETICAL
operational parameters, compared with simple policies, with infeasibility diagnostics,
tradeoff frontiers, sensitivity sweeps and certified shadow prices. Every result carries the
M3 calibration status and is labelled as not a validated recommendation.

Milestone M5 adds an HTTP API (FastAPI, :mod:`atlas.api`) that serves the committed M2-M4
reference outputs with their provenance and integrity labels, and runs bounded HYPOTHETICAL
simulations with the M4 engine in isolated worker processes.

Milestone M6 (phase A) adds a portable, verified deployment package (:mod:`atlas.deploy`
and ``deploy/``): a read-only server preflight, systemd units and an nginx site rendered
from one validated configuration, immutable releases with fingerprinted manifests, and
activation with health verification and automatic rollback. Nothing is deployed by it.

Milestone M7 adds the interactive web dashboard (``web/``, React and TypeScript, built to the
committed ``web/dist``): every view validated against the API contract, the scientific
limitations next to the numbers they qualify, bounded HYPOTHETICAL simulations where the
deployment allows them. :mod:`atlas.dashboard` verifies the build; the API serves it
(:mod:`atlas.api.dashboard`) and nginx serves a verified per-release copy
(:mod:`atlas.deploy.publish`).
"""

import os

# Least-squares results must not depend on how many threads BLAS happens to use, so ATLAS
# asks for one thread unless the user chose otherwise. This only takes effect when atlas
# is imported before numpy; the run manifest records the settings in force.
for _variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_variable, "1")

__version__ = "1.0.0"
