"""Pydantic v2 schemas for track API endpoints.

Defines the API contract for track listing and detail views.
Domain-to-schema converter functions translate attrs entities and
use case result objects into Pydantic models for JSON serialization.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from src.application.use_cases.get_track_details import (
    ConnectorMappingInfo,
    LikeInfo,
    PlaylistSummary,
    PlaySummary,
    TrackDetailsResult,
)
from src.domain.entities import Playlist
from src.domain.entities.playlist import DB_PSEUDO_CONNECTOR
from src.domain.entities.preference import PreferenceState
from src.domain.entities.tag import normalize_tag
from src.domain.entities.track import Track
from src.interface.api.schemas.common import PaginatedResponse
from src.interface.api.schemas.playlists import ArtistSchema, to_artist_schema

# Raw tag strings are validated + normalized at the Pydantic layer so
# invalid input surfaces as a 422 BEFORE hitting the use case or DB.
TagString = Annotated[str, AfterValidator(normalize_tag)]


class TrackFacetsSchema(BaseModel):
    """Per-facet counts scoped to the current filter set.

    Populated only when `GET /tracks?include_facets=true`.
    Keys within each dimension: preference ∈ star/yah/hmm/nah/unrated,
    liked ∈ true/false, connector ∈ registered connector names.
    """

    model_config = ConfigDict(from_attributes=True)

    preference: dict[str, int]
    liked: dict[str, int]
    connector: dict[str, int]


class PaginatedLibraryTracksResponse(PaginatedResponse["LibraryTrackSchema"]):
    """PaginatedResponse + optional facet counts for /tracks only.

    Keeps the generic PaginatedResponse uncluttered for other list endpoints
    (playlists, workflows) while surfacing `facets` where the contract
    allows it.
    """

    facets: TrackFacetsSchema | None = None


class LibraryTrackSchema(BaseModel):
    """Track in library list views — lightweight with summary fields."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    artists: list[ArtistSchema]
    album: str | None = None
    duration_ms: int | None = None
    isrc: str | None = None
    connector_names: list[str]
    is_liked: bool
    preference: PreferenceState | None = None
    tags: list[str] = Field(default_factory=list)


class SetPreferenceRequest(BaseModel):
    """Request body for PUT /tracks/{id}/preference."""

    state: PreferenceState


class AddTagRequest(BaseModel):
    """Request body for POST /tracks/{id}/tags."""

    tag: TagString


# Cap the batch endpoint at 15,000 track_ids — matches the backlog guardrail
# and prevents a single request from locking the write path for minutes.
BATCH_TAG_MAX_TRACKS = 15_000


class BatchTagRequest(BaseModel):
    """Request body for POST /tracks/tags/batch."""

    track_ids: list[UUID] = Field(max_length=BATCH_TAG_MAX_TRACKS)
    tag: TagString


class TagSummarySchema(BaseModel):
    """Tag with usage count, namespace/value split, and last-used timestamp.

    Used by the autocomplete dropdown and the Tag Management page.
    ``namespace`` is the prefix before the first colon (``mood``,
    ``energy``, ``context``); ``value`` is everything after. Tags
    without a colon have ``namespace=None`` and ``value=tag``.
    """

    tag: str
    namespace: str | None
    value: str
    track_count: int
    last_used_at: datetime


class RenameTagRequest(BaseModel):
    """Body for PATCH /api/v1/tags/{tag}. ``new_tag`` is normalized client-side."""

    new_tag: TagString


class MergeTagsRequest(BaseModel):
    """Body for POST /api/v1/tags/merge. Both fields are normalized client-side."""

    source: TagString
    target: TagString


class TagOperationResult(BaseModel):
    """Generic response for rename / delete / merge — affected track count.

    ``affected_count`` reflects how many ``track_tags`` rows were
    touched (renamed, merged, or deleted), not the number of distinct
    tracks if a user could somehow have multiple rows for the same
    track-tag pair (the UNIQUE constraint prevents this).
    """

    affected_count: int


class AddTagResponse(BaseModel):
    """Response from POST /tracks/{id}/tags."""

    track_id: UUID
    tag: str
    changed: bool


class BatchTagResponse(BaseModel):
    """Response from POST /tracks/tags/batch."""

    tag: str
    requested: int
    tagged: int


class ConnectorMappingSchema(BaseModel):
    """Connector mapping for track detail views with full provenance."""

    model_config = ConfigDict(from_attributes=True)

    mapping_id: UUID
    connector_name: str
    connector_track_id: str
    match_method: str
    confidence: int
    origin: str
    is_primary: bool
    connector_track_title: str
    connector_track_artists: list[str]


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

    id: UUID
    name: str
    description: str | None = None


class TrackDetailSchema(BaseModel):
    """Full track detail with assembled metadata from multiple repositories."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
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
    preference: PreferenceState | None = None
    tags: list[str] = Field(default_factory=list)


class MergeTrackRequest(BaseModel):
    """Request body for merging a duplicate track into a winner."""

    loser_id: UUID


class RelinkMappingRequest(BaseModel):
    """Request body for relinking a mapping to a different track."""

    new_track_id: UUID


class UnlinkMappingResponse(BaseModel):
    """Response after unlinking a mapping."""

    deleted_mapping_id: UUID
    orphan_track_id: UUID | None = None


# --- Domain-to-schema converters ---


def _get_connector_names(track: Track) -> list[str]:
    """Extract connector names from track identifiers, excluding internal 'db'."""
    return [c for c in track.connector_track_identifiers if c != DB_PSEUDO_CONNECTOR]


def to_library_track(
    track: Track,
    *,
    liked_track_ids: set[UUID],
    preference_map: dict[UUID, PreferenceState] | None = None,
    tag_map: dict[UUID, list[str]] | None = None,
) -> LibraryTrackSchema:
    """Convert domain Track to library list schema.

    Args:
        track: Domain Track entity.
        liked_track_ids: Set of track IDs liked on any service (from track_likes table).
        preference_map: Optional {track_id: state} for preference column.
        tag_map: Optional {track_id: [tag, ...]} for tag chips column.
    """
    return LibraryTrackSchema(
        id=track.id,
        title=track.title,
        artists=[to_artist_schema(a) for a in track.artists],
        album=track.album,
        duration_ms=track.duration_ms,
        isrc=track.isrc,
        connector_names=_get_connector_names(track),
        is_liked=track.id in liked_track_ids,
        preference=preference_map.get(track.id) if preference_map else None,
        tags=tag_map.get(track.id, []) if tag_map else [],
    )


def _to_connector_mapping_schema(info: ConnectorMappingInfo) -> ConnectorMappingSchema:
    return ConnectorMappingSchema(
        mapping_id=info.mapping_id,
        connector_name=info.connector_name,
        connector_track_id=info.connector_track_id,
        match_method=info.match_method,
        confidence=info.confidence,
        origin=info.origin,
        is_primary=info.is_primary,
        connector_track_title=info.connector_track_title,
        connector_track_artists=info.connector_track_artists,
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
        id=playlist.id, name=playlist.name, description=playlist.description
    )


def to_track_detail(result: TrackDetailsResult) -> TrackDetailSchema:
    """Convert use case result to track detail schema."""
    track = result.track
    return TrackDetailSchema(
        id=track.id,
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
        preference=result.preference,
        tags=result.tags,
    )
