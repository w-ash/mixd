"""Pydantic v2 schemas for playlist API endpoints.

Domain-to-schema conversion functions translate attrs entities into
Pydantic models for JSON serialization. Schemas define the API contract
visible in the OpenAPI spec.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from src.domain.entities.playlist import Playlist, PlaylistEntry
from src.domain.entities.track import Artist, Track


class ArtistSchema(BaseModel):
    """Artist representation in API responses."""

    model_config = ConfigDict(from_attributes=True)

    name: str


class TrackSummarySchema(BaseModel):
    """Minimal track data for playlist entry listings."""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    title: str
    artists: list[ArtistSchema]
    album: str | None = None
    duration_ms: int | None = None


class PlaylistEntrySchema(BaseModel):
    """A track's membership in a playlist with position metadata."""

    model_config = ConfigDict(from_attributes=True)

    position: int
    track: TrackSummarySchema
    added_at: datetime | None = None


class PlaylistSummarySchema(BaseModel):
    """Compact playlist representation for list views."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    track_count: int
    connector_links: list[str]
    updated_at: datetime | None = None


class PlaylistDetailSchema(PlaylistSummarySchema):
    """Full playlist with entries for detail views."""

    entries: list[PlaylistEntrySchema]


class CreatePlaylistRequest(BaseModel):
    """Request body for POST /playlists."""

    name: str
    description: str | None = None


class UpdatePlaylistRequest(BaseModel):
    """Request body for PATCH /playlists/{id}."""

    name: str | None = None
    description: str | None = None


# --- Domain-to-schema converters ---


def to_artist_schema(artist: Artist) -> ArtistSchema:
    return ArtistSchema(name=artist.name)


def _to_track_summary(track: Track) -> TrackSummarySchema:
    return TrackSummarySchema(
        id=track.id,
        title=track.title,
        artists=[to_artist_schema(a) for a in track.artists],
        album=track.album,
        duration_ms=track.duration_ms,
    )


def to_playlist_entry(entry: PlaylistEntry, position: int) -> PlaylistEntrySchema:
    return PlaylistEntrySchema(
        position=position,
        track=_to_track_summary(entry.track),
        added_at=entry.added_at,
    )


def to_playlist_summary(playlist: Playlist) -> PlaylistSummarySchema:
    """Convert domain Playlist to summary schema for list endpoints."""
    return PlaylistSummarySchema(
        id=playlist.id or 0,
        name=playlist.name,
        description=playlist.description,
        track_count=playlist.track_count,
        connector_links=list(playlist.connector_playlist_identifiers.keys()),
        updated_at=playlist.updated_at,
    )


def to_playlist_detail(playlist: Playlist) -> PlaylistDetailSchema:
    """Convert domain Playlist to detail schema with all entries."""
    return PlaylistDetailSchema(
        id=playlist.id or 0,
        name=playlist.name,
        description=playlist.description,
        track_count=playlist.track_count,
        connector_links=list(playlist.connector_playlist_identifiers.keys()),
        updated_at=playlist.updated_at,
        entries=[
            to_playlist_entry(entry, idx) for idx, entry in enumerate(playlist.entries)
        ],
    )
