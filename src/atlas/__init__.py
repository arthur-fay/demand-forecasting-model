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
"""

import os

# Least-squares results must not depend on how many threads BLAS happens to use, so ATLAS
# asks for one thread unless the user chose otherwise. This only takes effect when atlas
# is imported before numpy; the run manifest records the settings in force.
for _variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_variable, "1")

__version__ = "0.3.0"
