"""Exception hierarchy for conditions that stop a command before checks can run.

Data-quality findings are not exceptions: they are reported as
:class:`atlas.issues.Issue` objects so that every problem in a run is visible at once.
The exception is :class:`DataQualityError`, raised by M1 library functions (series
construction, reconciliation, features) whose result would be meaningless on bad input;
it carries the same ``Issue`` objects.
"""

from __future__ import annotations

from atlas.issues import Issue


class AtlasError(Exception):
    """Base class for ATLAS errors."""


class ConfigError(AtlasError):
    """Invalid or incomplete configuration (paths, snapshot identifiers, environment)."""


class SnapshotError(AtlasError):
    """A snapshot manifest or exception register is missing or malformed."""


class DataQualityError(AtlasError):
    """Input data violates a contract that the requested computation depends on.

    Carries the individual findings so callers can report every problem at once.
    """

    def __init__(self, message: str, issues: tuple[Issue, ...] = ()) -> None:
        super().__init__(message)
        self.issues = issues


class FeatureError(AtlasError):
    """A feature definition or feature request is invalid."""


class InsufficientHistoryError(FeatureError):
    """A feature needs observations from before the start of the available history."""


class LeakageError(FeatureError):
    """A feature would read a value that is not known at the forecast origin."""
