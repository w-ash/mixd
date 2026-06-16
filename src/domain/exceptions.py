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


class EnrichmentFailedError(DomainError):
    """Raised when a workflow enricher produces no metrics — a *total* failure
    (e.g. a Last.fm outage), as opposed to a partial one where some metrics
    still land.

    Replaces the old swallow-into-a-success-shaped-result behavior that let a
    COMPLETED run silently overwrite the Curator's playlist with 0 tracks. The
    executor's degrade path (``_RECOVERABLE_CATEGORIES``) catches it and passes
    the upstream tracklist through, so the run finishes visibly *degraded*
    rather than fake-successful. ``enricher`` is the node label for triage.
    """

    def __init__(self, enricher: str, reason: str) -> None:
        super().__init__(f"{enricher} enrichment failed: {reason}")
        self.enricher = enricher
        self.reason = reason


class EmptyOverwriteError(DomainError):
    """Raised when a destination node is asked to *overwrite* a playlist with an
    empty tracklist.

    A 0-track overwrite is destructive and almost always an upstream failure
    (an enrichment outage degraded, then a metric filter dropped every track),
    not an intentional "clear the playlist". The destination refuses it so the
    run fails loudly with the playlist intact, instead of silently wiping it.
    Append mode is unaffected — adding nothing is a harmless no-op.
    """

    def __init__(self, target: str) -> None:
        super().__init__(
            f"Refusing to overwrite '{target}' with 0 tracks — the pipeline "
            f"produced no tracks (likely an upstream enrichment/filter failure)"
        )
        self.target = target


class SpotifyAuthRequiredError(DomainError):
    """Raised when a Spotify access token is needed but none is stored.

    ``SpotifyTokenManager.get_valid_token`` is server-safe by construction: it
    never launches the interactive browser OAuth flow (an ``HTTPServer`` on
    127.0.0.1:8888 that would block the FastAPI worker forever and leak threads,
    since it runs inside the per-request auth flow). Instead it raises this, which
    the SSE seam surfaces as a clean terminal error and the CLI prints with a
    connect hint. Interactive connect (CLI ``mixd connector connect spotify`` /
    the web OAuth callback) calls ``run_browser_auth`` / ``exchange_code`` directly.
    """

    def __init__(self) -> None:
        super().__init__(
            "Spotify is not connected. Connect it in the web UI or run "
            "`mixd connector connect spotify`."
        )


class LastfmAuthRequiredError(DomainError):
    """Raised when a Last.fm import is requested but no account is resolvable.

    The Last.fm username is resolved token-first: the stored OAuth token's
    ``account_name`` for *this* user wins over an explicit request username, which
    wins over the ``LASTFM_USERNAME`` env fallback (CLI/local-dev only). A web user
    with no connected Last.fm account and no env must fail cleanly here rather than
    silently importing whatever ``LASTFM_USERNAME`` points at — a cross-tenant leak.
    Surfaced as a clean terminal SSE error (via the seam) and a CLI connect hint.
    """

    def __init__(self) -> None:
        super().__init__(
            "Last.fm is not connected. Connect it in the web UI or run "
            "`mixd connector connect lastfm`."
        )


class ConnectorNotConnectedError(DomainError):
    """Raised by the import-route pre-flight when a connector has no stored token.

    A token-presence gate (HTTP 409 ``CONNECTOR_NOT_CONNECTED``) so a token-less
    user gets an immediate, actionable error instead of a background operation that
    starts and then fails. The frontend also gates the trigger button on connected
    state; this is the server-side backstop.
    """

    def __init__(self, connector: str) -> None:
        super().__init__(f"{connector.title()} is not connected.")
        self.connector = connector


class ConnectorSyncError(DomainError):
    """Raised when pushing a playlist to an external connector (Spotify/Apple
    Music) fails at the API layer.

    The push did not land, so the local link must NOT be marked SYNCED.
    ``SyncPlaylistLinkUseCase``'s ERROR branch catches this and sets
    ``SyncStatus.ERROR``; the CLI prints the failure and the web surfaces it —
    replacing the old swallow that returned a success-shaped result and printed
    "Sync complete" on a failed push. ``connector`` is the service name.
    """

    def __init__(self, connector: str, reason: str) -> None:
        super().__init__(f"{connector} playlist sync failed: {reason}")
        self.connector = connector
        self.reason = reason


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
