"""Domain entities for data integrity monitoring results.

Pure value objects representing the outcome of integrity checks —
no dependencies on infrastructure or application layers.
"""

from typing import Literal

from attrs import define, field

type CheckStatus = Literal["pass", "warn", "fail"]


@define(frozen=True, slots=True)
class IntegrityCheckResult:
    """Result of a single integrity check."""

    name: str
    status: CheckStatus
    count: int
    details: list[dict[str, object]] = field(factory=list)


@define(frozen=True, slots=True)
class IntegrityReport:
    """Aggregate result of all integrity checks."""

    checks: list[IntegrityCheckResult]
    overall_status: CheckStatus

    @property
    def total_issues(self) -> int:
        return sum(c.count for c in self.checks)
