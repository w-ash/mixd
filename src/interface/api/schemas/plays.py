"""Pydantic schemas for the play-history endpoints (v0.10.4)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PlayEventSchema(BaseModel):
    """One play event with its track's display metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    track_id: UUID
    title: str
    artists: str
    played_at: datetime
    ms_played: int | None
    service: str
    source_services: list[str]


class PlayListResponse(BaseModel):
    """List shape: data array plus opaque next-page cursor."""

    data: list[PlayEventSchema]
    limit: int
    next_cursor: str | None


class HistogramBinSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bucket_start: datetime
    count: int


class PlayHistogramResponse(BaseModel):
    """Binned play counts; ``bucket`` names the server-chosen bin size."""

    bins: list[HistogramBinSchema]
    bucket: Literal["day", "week", "month"]
