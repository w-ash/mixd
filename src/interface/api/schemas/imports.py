"""Pydantic v2 schemas for import and operation endpoints."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


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


class CheckpointStatusSchema(BaseModel):
    """Sync checkpoint status for a single service + entity type."""

    model_config = ConfigDict(from_attributes=True)

    service: str
    entity_type: str
    last_sync_timestamp: datetime | None = None
    has_previous_sync: bool = False
    local_count: int | None = None
    remote_total: int | None = None
