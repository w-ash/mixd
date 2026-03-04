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


class ExportLastfmLikesRequest(BaseModel):
    """Request body for triggering a Last.fm likes export."""

    batch_size: int | None = None
    max_exports: int | None = None


class OperationStartedResponse(BaseModel):
    """Returned immediately when a long-running operation is launched."""

    model_config = ConfigDict(from_attributes=True)

    operation_id: str


class CheckpointStatusSchema(BaseModel):
    """Sync checkpoint status for a single service + entity type."""

    model_config = ConfigDict(from_attributes=True)

    service: str
    entity_type: str
    last_sync_timestamp: datetime | None = None
    has_previous_sync: bool = False
