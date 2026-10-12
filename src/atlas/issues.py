"""Issue and report types shared by integrity, validation and reproduction checks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

MAX_EXAMPLES = 5


class Severity(StrEnum):
    """How an issue affects the outcome of a check.

    ``ERROR`` fails the check. ``WARNING`` is reported but does not fail it.
    ``INFO`` records an accepted, documented condition (for example a declared
    known exception whose observed statistics match the declaration exactly).
    """

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class Issue:
    """A single finding produced by a check."""

    code: str
    severity: Severity
    subject: str
    message: str
    count: int = 1
    examples: tuple[str, ...] = ()
    exception_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "subject": self.subject,
            "message": self.message,
            "count": self.count,
            "examples": list(self.examples),
            "exception_id": self.exception_id,
        }


def examples(values: Iterable[object], limit: int = MAX_EXAMPLES) -> tuple[str, ...]:
    """Return up to ``limit`` values rendered as strings, preserving order."""
    rendered: list[str] = []
    for value in values:
        if len(rendered) >= limit:
            break
        rendered.append(str(value))
    return tuple(rendered)


@dataclass(frozen=True)
class CheckReport:
    """Outcome of one stage (integrity, validation or reproduction)."""

    name: str
    issues: tuple[Issue, ...] = ()
    facts: Mapping[str, Any] = field(default_factory=dict)
    skipped_reason: str | None = None

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.WARNING)

    @property
    def ok(self) -> bool:
        """True when the stage ran and produced no errors."""
        return self.skipped_reason is None and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "skipped_reason": self.skipped_reason,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [issue.to_dict() for issue in self.issues],
            "facts": dict(self.facts),
        }
