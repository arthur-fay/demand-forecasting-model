"""Exception hierarchy for conditions that stop a command before checks can run.

Data-quality findings are not exceptions: they are reported as
:class:`atlas.issues.Issue` objects so that every problem in a run is visible at once.
"""


class AtlasError(Exception):
    """Base class for ATLAS errors."""


class ConfigError(AtlasError):
    """Invalid or incomplete configuration (paths, snapshot identifiers, environment)."""


class SnapshotError(AtlasError):
    """A snapshot manifest or exception register is missing or malformed."""
