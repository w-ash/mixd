"""Domain exception types.

Typed exceptions for domain-level error conditions. These replace stringly-typed
ValueError messages so the API layer can map specific exception types to
specific HTTP status codes without parsing error message text.
"""

from uuid import UUID


class DomainError(Exception):
    """Base class for domain-level errors."""


class NotFoundError(Exception):
    """Raised when a requested entity does not exist."""


class TracklistInvariantError(DomainError):
    """Raised when a tracklist violates workflow invariants."""


class OptimisticLockError(DomainError):
    """Raised when a concurrent modification is detected (stale version)."""

    def __init__(self, entity_id: UUID, expected_version: int) -> None:
        super().__init__(
            f"Concurrent modification detected for entity {entity_id} "
            f"(expected version {expected_version})"
        )
        self.entity_id = entity_id
        self.expected_version = expected_version


class ConfirmationRequiredError(DomainError):
    """Raised when a destructive operation requires explicit user confirmation."""

    def __init__(
        self, message: str, *, removals: int, total: int, remaining: int
    ) -> None:
        super().__init__(message)
        self.removals = removals
        self.total = total
        self.remaining = remaining


class WorkflowAlreadyRunningError(DomainError):
    """Raised when a workflow already has an active (pending/running) run.

    Enforced at the database via the ``uq_workflow_runs_active`` partial unique
    index: the run repository maps that constraint's ``IntegrityError`` to this
    exception so the API can answer 409 across every instance in a multi-machine
    deploy (an in-process guard could not). ``workflow_id`` is kept as a string
    for the JSON error body.
    """

    def __init__(self, workflow_id: str) -> None:
        super().__init__(f"Workflow '{workflow_id}' is already running")
        self.workflow_id = workflow_id
