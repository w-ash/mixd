"""Pydantic v2 schemas for track API endpoints.

Defines the API contract for track listing and detail views.
Domain-to-schema converter functions translate attrs entities and
use case result objects into Pydantic models for JSON serialization.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from src.application.use_cases.get_track_details import (
    ConnectorMappingInfo,
    LikeInfo,
    PlaylistSummary,
    PlaySummary,
    TrackDetailsResult,
)
from src.config.constants import ConnectorConstants
from src.domain.entities import Playlist
from src.domain.entities.track import Track
from src.interface.api.schemas.playlists import ArtistSchema, to_artist_schema


class LibraryTrackSchema(BaseModel):
    """Track in library list views — lightweight with summary fields."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    artists: list[ArtistSchema]
    album: str | None = None
    duration_ms: int | None = None
    isrc: str | None = None
    connector_names: list[str]
    is_liked: bool


class ConnectorMappingSchema(BaseModel):
    """Connector mapping for track detail views."""

    model_config = ConfigDict(from_attributes=True)

    connector_name: str
    connector_track_id: str


class LikeStatusSchema(BaseModel):
    """Per-service like status."""

    model_config = ConfigDict(from_attributes=True)

    is_liked: bool
    liked_at: datetime | None = None


class PlaySummarySchema(BaseModel):
    """Aggregated play statistics."""

    model_config = ConfigDict(from_attributes=True)

    total_plays: int
    first_played: datetime | None = None
    last_played: datetime | None = None


class PlaylistBriefSchema(BaseModel):
    """Minimal playlist reference in track detail views."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None


class TrackDetailSchema(BaseModel):
    """Full track detail with assembled metadata from multiple repositories."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    artists: list[ArtistSchema]
    album: str | None = None
    duration_ms: int | None = None
    release_date: datetime | None = None
    isrc: str | None = None
    connector_mappings: list[ConnectorMappingSchema]
    like_status: dict[str, LikeStatusSchema]
    play_summary: PlaySummarySchema
    playlists: list[PlaylistBriefSchema]


# --- Domain-to-schema converters ---


def _get_connector_names(track: Track) -> list[str]:
    """Extract connector names from track identifiers, excluding internal 'db'."""
    return [
        c
        for c in track.connector_track_identifiers
        if c != ConnectorConstants.DB_PSEUDO_CONNECTOR
    ]


def to_library_track(track: Track, *, liked_track_ids: set[int]) -> LibraryTrackSchema:
    """Convert domain Track to library list schema.

    Args:
        track: Domain Track entity.
        liked_track_ids: Set of track IDs liked on any service (from track_likes table).
    """
    return LibraryTrackSchema(
        id=track.id or 0,
        title=track.title,
        artists=[to_artist_schema(a) for a in track.artists],
        album=track.album,
        duration_ms=track.duration_ms,
        isrc=track.isrc,
        connector_names=_get_connector_names(track),
        is_liked=(track.id or 0) in liked_track_ids,
    )


def _to_connector_mapping_schema(info: ConnectorMappingInfo) -> ConnectorMappingSchema:
    return ConnectorMappingSchema(
        connector_name=info.connector_name,
        connector_track_id=info.connector_track_id,
    )


def _to_like_status_schema(info: LikeInfo) -> LikeStatusSchema:
    return LikeStatusSchema(is_liked=info.is_liked, liked_at=info.liked_at)


def _to_play_summary_schema(summary: PlaySummary) -> PlaySummarySchema:
    return PlaySummarySchema(
        total_plays=summary.total_plays,
        first_played=summary.first_played,
        last_played=summary.last_played,
    )


def _to_playlist_brief_schema(summary: PlaylistSummary) -> PlaylistBriefSchema:
    return PlaylistBriefSchema(
        id=summary.id, name=summary.name, description=summary.description
    )


def playlist_to_brief_schema(playlist: Playlist) -> PlaylistBriefSchema:
    """Convert a domain Playlist entity to PlaylistBriefSchema."""
    return PlaylistBriefSchema(
        id=playlist.id or 0, name=playlist.name, description=playlist.description
    )


def to_track_detail(result: TrackDetailsResult) -> TrackDetailSchema:
    """Convert use case result to track detail schema."""
    track = result.track
    return TrackDetailSchema(
        id=track.id or 0,
        title=track.title,
        artists=[to_artist_schema(a) for a in track.artists],
        album=track.album,
        duration_ms=track.duration_ms,
        release_date=track.release_date,
        isrc=track.isrc,
        connector_mappings=[
            _to_connector_mapping_schema(m) for m in result.connector_mappings
        ],
        like_status={
            svc: _to_like_status_schema(info)
            for svc, info in result.like_status.items()
        },
        play_summary=_to_play_summary_schema(result.play_summary),
        playlists=[_to_playlist_brief_schema(p) for p in result.playlists],
    )
