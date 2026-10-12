"""ATLAS: reproducible foundation for NYC 311 demand analysis and decision support.

Milestone M0 provides the engineering base: an immutable registry of the legacy
data snapshot, strict offline loading, fail-closed data contracts, and
reproduction of the historical summaries published by the original project.

Milestone M1 adds ISO 8601 calendar conventions, an explicit reconciliation residual,
causal data-quality flags, and one leakage-safe feature builder shared by training,
direct forecasting and recursive forecasting.
"""

__version__ = "0.1.0"
