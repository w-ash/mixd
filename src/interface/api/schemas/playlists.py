"""Pydantic v2 schemas for playlist API endpoints.

Domain-to-schema conversion functions translate attrs entities into
Pydantic models for JSON serialization. Schemas define the API contract
visible in the OpenAPI spec.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.domain.entities.playlist import Playlist, PlaylistEntry
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection
from src.domain.entities.track import Artist, Track


def direction_label(sync_direction: str, connector_name: str) -> str:
    """The one user-facing sync-direction phrase, leading with what gets overwritten.

    Single source of truth for the wording so CLI, web, and API all agree
    (the v0.8.7 direction-vocabulary unification). ``pull`` = the connector is the
    source of truth and Mixd is replaced; ``push`` = Mixd is the source of truth.
    """
    connector = connector_name.replace("_", " ").title()
    if sync_direction == SyncDirection.PUSH.value:
        return f"Mixd → {connector} (replaces {connector})"
    return f"{connector} → Mixd (replaces Mixd)"


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
    """A track's membership in a playlist with position metadata.

    ``is_resolved`` is False for an unresolved entry — a source position whose
    connector track has no canonical match yet. Its ``track`` then carries the
    display snapshot (title/artists) with ``id=None`` so the UI can render it
    ("Couldn't match: …") and offer a repair action without losing the slot.
    """

    model_config = ConfigDict(from_attributes=True)

    # Stable membership identity (mirrors DBPlaylistTrack.id). The client keys
    # rows by this and addresses remove/reorder by it — distinct from ``position``
    # (the volatile 1-based display index), so two identical-track entries are
    # individually addressable.
    id: UUID
    position: int
    track: TrackSummarySchema
    added_at: datetime | None = None
    is_resolved: bool = True


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
    connector_playlist_identifier: str
    connector_playlist_name: str | None = None
    sync_direction: str
    # Unified user-facing phrase for ``sync_direction`` (one vocabulary across
    # surfaces) — e.g. "Spotify → Mixd (replaces Mixd)".
    direction_label: str
    sync_status: str
    last_synced: datetime | None = None
    last_sync_error: str | None = None
    last_sync_tracks_added: int | None = None
    last_sync_tracks_removed: int | None = None
    last_sync_tracks_unmatched: int | None = None


class CreateLinkRequest(BaseModel):
    """Request body for POST /playlists/{id}/links."""

    connector: str
    connector_playlist_identifier: str  # Accepts Spotify URI, URL, or raw ID
    # PULL by default — a freshly linked playlist keeps pulling from the connector.
    sync_direction: str = "pull"


class SyncLinkRequest(BaseModel):
    """Request body for POST /playlists/{id}/links/{link_id}/sync.

    ``confirm_token`` is the staleness token from a prior preview, echoed back to
    proceed with a destructive sync. Omit it for a normal (non-destructive) sync;
    a destructive one without a matching token returns 409 CONFIRMATION_REQUIRED.
    """

    direction_override: str | None = None
    confirm_token: str | None = None


class UpdateLinkRequest(BaseModel):
    """Request body for PATCH /playlists/{id}/links/{link_id}."""

    sync_direction: str


class RepairUnresolvedResponse(BaseModel):
    """Result of re-resolving a playlist's unresolved entries."""

    repaired: int
    still_unresolved: int


class SyncPreviewResponse(BaseModel):
    """Preview of what a sync operation would change (read-only).

    When ``has_comparison_data`` is False, the link has never been synced
    and no locally-cached external playlist exists for diffing.
    """

    tracks_to_add: int
    tracks_to_remove: int
    tracks_unchanged: int
    direction: str
    direction_label: str = ""
    connector_name: str
    playlist_name: str
    has_comparison_data: bool = True
    safety_flagged: bool = False
    safety_message: str | None = None
    safety_removals: int = 0
    safety_total: int = 0
    safety_remaining: int = 0
    confirm_token: str = ""


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


class AddTracksRequest(BaseModel):
    """Request body for POST /playlists/{id}/tracks.

    ``track_ids`` is order-significant and may repeat (manual add allows
    duplicates). ``position`` is a 0-based insert index; omit to append.
    """

    track_ids: list[UUID]
    position: int | None = None


class RemoveEntriesRequest(BaseModel):
    """Request body for batch DELETE /playlists/{id}/tracks."""

    entry_ids: list[UUID]


class ReorderEntriesRequest(BaseModel):
    """Request body for PATCH /playlists/{id}/tracks/reorder (full ordered list)."""

    entry_ids: list[UUID]


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
    if entry.track is not None:
        return PlaylistEntrySchema(
            id=entry.id,
            position=position + 1,
            track=_to_track_summary(entry.track),
            added_at=entry.added_at,
            is_resolved=entry.is_resolved,
        )
    # Unresolved: synthesize a summary from the connector ref so the position
    # still renders (with id=None marking it unresolved).
    ref = entry.connector_track_ref
    return PlaylistEntrySchema(
        id=entry.id,
        position=position + 1,
        track=TrackSummarySchema(
            id=None,
            title=entry.display_title,
            artists=[ArtistSchema(name=a) for a in (ref.artists if ref else ())],
        ),
        added_at=entry.added_at,
        is_resolved=entry.is_resolved,
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
        connector_playlist_identifier=link.connector_playlist_identifier,
        connector_playlist_name=link.connector_playlist_name,
        sync_direction=link.sync_direction.value,
        direction_label=direction_label(link.sync_direction.value, link.connector_name),
        sync_status=link.sync_status.value,
        last_synced=link.last_synced,
        last_sync_error=link.last_sync_error,
        last_sync_tracks_added=link.last_sync_tracks_added,
        last_sync_tracks_removed=link.last_sync_tracks_removed,
        last_sync_tracks_unmatched=link.last_sync_tracks_unmatched,
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
