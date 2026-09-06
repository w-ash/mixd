"""Pydantic v2 schemas for import and operation endpoints."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.domain.entities.operation_run import OperationStatus
from src.domain.entities.shared import JsonDict

# A queue entry's vocabulary is the run vocabulary plus the one pre-run state.
# Defined here (not in the queue service) so the wire schema and the in-memory
# entry share a single definition without a schemas → services import cycle.
type QueueEntryStatus = Literal["queued"] | OperationStatus


class ImportLastfmHistoryRequest(BaseModel):
    """Request body for triggering a Last.fm history import."""

    mode: Literal["recent", "incremental", "full"] = "incremental"
    limit: int | None = None
    from_date: datetime | None = None
    to_date: datetime | None = None


class ImportSpotifyLikesRequest(BaseModel):
    """Request body for triggering a Spotify likes import."""

    limit: int | None = None
    max_imports: int | None = None
    force: bool = False


class ImportSpotifyRecentRequest(BaseModel):
    """Request body for polling Spotify's recently-played API.

    No mode field: the stored cursor makes every poll incremental. ``limit`` is
    clamped to the endpoint's 50-play ceiling downstream. ``force`` ignores the
    cursor for one poll and re-reads the whole retained window — a recovery
    lever for a cursor that ran ahead of what was actually stored; re-ingesting
    seen plays is harmless (the ledger conflict-skips them).
    """

    limit: int | None = None
    force: bool = False


class ImportAppleRecentRequest(BaseModel):
    """Request body for polling Apple Music's recently-played API.

    No mode, no limit: the stored window fingerprint makes every poll
    incremental, and the endpoint's page size is fixed rather than caller-set.
    ``force`` re-seeds that fingerprint for one poll — a recovery lever for a
    fingerprint that ran ahead of what was actually stored, not a re-import
    (Apple's feed carries no timestamps to re-ingest seen plays against).
    """

    force: bool = False


class ExportLastfmLikesRequest(BaseModel):
    """Request body for triggering a Last.fm likes export."""

    batch_size: int | None = None
    max_exports: int | None = None


class OperationStartedResponse(BaseModel):
    """Returned immediately when a long-running operation is launched.

    ``run_id`` is the persistent ``OperationRun`` audit-log row id, present
    when the route writes one via the seam-level recorder. The frontend
    caches it alongside ``operation_id`` so the post-run toast can deep-
    link to ``/settings/imports?run=<run_id>`` without a second round trip.
    """

    model_config = ConfigDict(from_attributes=True)

    operation_id: str
    run_id: str | None = None


class ImportQueueEntrySchema(BaseModel):
    """One file's place in a Spotify GDPR import queue.

    ``operation_id``/``run_id`` are null until the entry starts — a queued file
    has no ``operation_runs`` row yet; the ids appear via the queue GET as the
    sequencer reaches it.

    Everything past ``run_id`` exists so this endpoint can restore the view a
    dropped stream was painting: ``counts`` outlives a settled file's own
    stream, and ``size_bytes`` plus the timestamps are what an estimate needs.
    No summary object — every roll-up is one pass over these entries, and a
    server-side copy would be a second source of truth for the same facts.
    """

    model_config = ConfigDict(from_attributes=True)

    filename: str
    position: int
    status: QueueEntryStatus
    operation_id: str | None = None
    run_id: str | None = None
    # Real bytes written, so an estimate can weight by work rather than file
    # count — a GDPR export's files vary several-fold.
    size_bytes: int = 0
    started_at: datetime | None = None
    settled_at: datetime | None = None
    # The run's ``summary_metrics`` (``track_plays``, ``duplicates``,
    # ``errors``, …), or ``{"error_message": ...}`` on a failure.
    counts: JsonDict | None = None


class ImportQueueSchema(BaseModel):
    """A queue that exists, in upload order.

    The response of every endpoint that either creates the queue or acts on an
    existing one, so nothing here is nullable and there is no "is there a queue"
    flag to branch on.

    ``operation_id`` is the drain's SSE handle — one id for the whole export,
    with each file a sub-operation of it, so the client attaches once instead of
    re-attaching per entry.
    """

    queue_id: str
    operation_id: str
    started_at: datetime
    entries: list[ImportQueueEntrySchema]


class ImportQueueResponse(BaseModel):
    """The user's import queue, in upload order — or the absence of one.

    The GET's shape alone, because it is the only endpoint that can answer with
    "there is no queue". The nesting is the discriminator: ``queue`` is null when
    this user has no queue registered, so absence is stated once instead of as a
    flag plus four fields that have to agree with it. A read of "nothing is
    importing" is a successful answer, not a missing resource, so the GET never
    404s and the client needs no error branch for its idle state.
    """

    queue: ImportQueueSchema | None


class CheckpointStatusSchema(BaseModel):
    """Sync checkpoint status for a single service + entity type."""

    model_config = ConfigDict(from_attributes=True)

    service: str
    entity_type: str
    last_sync_timestamp: datetime | None = None
    has_previous_sync: bool = False
    local_count: int | None = None
    remote_total: int | None = None
    # Adaptive-polling fields, populated only for the polled channel and null
    # elsewhere. `last_sync_timestamp` is when the user last *listened*;
    # `last_polled_at` is when we last *checked* — an idle account's former ages
    # forever while the latter stays current.
    last_polled_at: datetime | None = None
    # Ships alongside the health verdict because the client cannot derive one
    # from a timestamp: 20 hours is healthy at the daily floor and broken at the
    # sole-observer cap.
    effective_interval_seconds: int | None = None
    poll_health: Literal["healthy", "overdue"] | None = None
    # A poll came back with a saturated window, so plays may already have been
    # lost. Surfaced rather than logged: the remedy (add a second observer) is
    # the user's call.
    possible_gap: bool = False
