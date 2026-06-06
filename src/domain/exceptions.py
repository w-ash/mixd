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


class ScheduleAlreadyExistsError(DomainError):
    """Raised when a user already has a schedule for the same target.

    Enforced at the database via the partial unique indexes
    ``uq_schedules_workflow_target`` / ``uq_schedules_sync_target`` (one schedule
    per ``(user_id, workflow_id)`` and per ``(user_id, sync_target)``). The
    schedule repository maps that constraint's ``IntegrityError`` to this
    exception so the API can answer 409 — mirrors ``WorkflowAlreadyRunningError``.
    ``target`` is a human-readable identifier for the JSON error body.
    """

    def __init__(self, target: str) -> None:
        super().__init__(f"A schedule for '{target}' already exists")
        self.target = target


class ScheduleInvariantError(DomainError):
    """Raised when a schedule write violates a DB CHECK constraint.

    The ``schedules`` table's CHECK constraints (the exclusive target arc and the
    hour/minute/day_of_week ranges) live
    only in migration 025, so a write that breaks one surfaces as a raw
    ``IntegrityError`` rather than a friendly error. The repository maps those
    constraints' ``IntegrityError`` to this exception so the API can answer 422
    (a malformed schedule is a validation failure) instead of a 500. ``constraint``
    is the violated constraint name for triage.
    """

    def __init__(self, constraint: str) -> None:
        super().__init__(f"Schedule violates constraint '{constraint}'")
        self.constraint = constraint
