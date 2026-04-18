"""Pydantic v2 schemas for playlist API endpoints.

Domain-to-schema conversion functions translate attrs entities into
Pydantic models for JSON serialization. Schemas define the API contract
visible in the OpenAPI spec.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.domain.entities.playlist import Playlist, PlaylistEntry
from src.domain.entities.playlist_link import PlaylistLink
from src.domain.entities.track import Artist, Track


class ArtistSchema(BaseModel):
    """Artist representation in API responses."""

    model_config = ConfigDict(from_attributes=True)

    name: str


class TrackSummarySchema(BaseModel):
    """Minimal track data for playlist entry listings."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
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


# --- Playlist link schemas ---


class ConnectorLinkBriefSchema(BaseModel):
    """Compact link info for playlist list views."""

    connector_name: str
    sync_direction: str
    sync_status: str


class PlaylistLinkSchema(BaseModel):
    """Full link detail for playlist detail views."""

    id: UUID
    connector_name: str
    connector_playlist_id: str
    connector_playlist_name: str | None = None
    sync_direction: str
    sync_status: str
    last_synced: datetime | None = None
    last_sync_error: str | None = None
    last_sync_tracks_added: int | None = None
    last_sync_tracks_removed: int | None = None


class CreateLinkRequest(BaseModel):
    """Request body for POST /playlists/{id}/links."""

    connector: str
    connector_playlist_id: str  # Accepts Spotify URI, URL, or raw ID
    sync_direction: str = "push"


class SyncLinkRequest(BaseModel):
    """Request body for POST /playlists/{id}/links/{link_id}/sync."""

    direction_override: str | None = None
    confirmed: bool = False


class UpdateLinkRequest(BaseModel):
    """Request body for PATCH /playlists/{id}/links/{link_id}."""

    sync_direction: str


class SyncPreviewResponse(BaseModel):
    """Preview of what a sync operation would change (read-only).

    When ``has_comparison_data`` is False, the link has never been synced
    and no locally-cached external playlist exists for diffing.
    """

    tracks_to_add: int
    tracks_to_remove: int
    tracks_unchanged: int
    direction: str
    connector_name: str
    playlist_name: str
    has_comparison_data: bool = True
    safety_flagged: bool = False
    safety_message: str | None = None


class SyncStartedResponse(BaseModel):
    """Response for sync operations that run in the background."""

    operation_id: str


# --- Playlist summary/detail schemas ---


class PlaylistSummarySchema(BaseModel):
    """Compact playlist representation for list views.

    Breaking change from v0.4.3: connector_links changed from list[str]
    to list[ConnectorLinkBriefSchema] with sync direction and status.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None = None
    track_count: int
    connector_links: list[ConnectorLinkBriefSchema]
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
        position=position + 1,
        track=_to_track_summary(entry.track),
        added_at=entry.added_at,
    )


def to_link_brief(link: PlaylistLink) -> ConnectorLinkBriefSchema:
    """Convert domain PlaylistLink to brief schema for list views."""
    return ConnectorLinkBriefSchema(
        connector_name=link.connector_name,
        sync_direction=link.sync_direction.value,
        sync_status=link.sync_status.value,
    )


def to_link_schema(link: PlaylistLink) -> PlaylistLinkSchema:
    """Convert domain PlaylistLink to full schema for detail views."""
    return PlaylistLinkSchema(
        id=link.id,
        connector_name=link.connector_name,
        connector_playlist_id=link.connector_playlist_identifier,
        connector_playlist_name=link.connector_playlist_name,
        sync_direction=link.sync_direction.value,
        sync_status=link.sync_status.value,
        last_synced=link.last_synced,
        last_sync_error=link.last_sync_error,
        last_sync_tracks_added=link.last_sync_tracks_added,
        last_sync_tracks_removed=link.last_sync_tracks_removed,
    )


def _build_connector_links(
    playlist: Playlist,
    links: list[PlaylistLink] | None,
) -> list[ConnectorLinkBriefSchema]:
    """Build connector link briefs from pre-fetched links or playlist identifiers."""
    if links is not None:
        return [to_link_brief(link) for link in links]
    # Fallback: connector names only (no sync info available)
    return [
        ConnectorLinkBriefSchema(
            connector_name=name,
            sync_direction="push",
            sync_status="never_synced",
        )
        for name in playlist.connector_playlist_identifiers
    ]


def to_playlist_summary(
    playlist: Playlist,
    links: list[PlaylistLink] | None = None,
) -> PlaylistSummarySchema:
    """Convert domain Playlist to summary schema for list endpoints.

    Args:
        playlist: The playlist entity.
        links: Optional pre-fetched links. If None, falls back to
            connector_playlist_identifiers keys with default status.
    """
    return PlaylistSummarySchema(
        id=playlist.id,
        name=playlist.name,
        description=playlist.description,
        track_count=playlist.track_count,
        connector_links=_build_connector_links(playlist, links),
        updated_at=playlist.updated_at,
    )


def to_playlist_detail(
    playlist: Playlist,
    links: list[PlaylistLink] | None = None,
) -> PlaylistDetailSchema:
    """Convert domain Playlist to detail schema with all entries."""
    return PlaylistDetailSchema(
        id=playlist.id,
        name=playlist.name,
        description=playlist.description,
        track_count=playlist.track_count,
        connector_links=_build_connector_links(playlist, links),
        updated_at=playlist.updated_at,
        entries=[
            to_playlist_entry(entry, idx) for idx, entry in enumerate(playlist.entries)
        ],
    )
