"""Pydantic v2 schemas for artist API endpoints.

Naming note: the credit schema on a track is already ``ArtistSchema``
(``schemas/playlists.py``) — a credited *name* on one track, not an entity. The
canonical artist rows this module serves are ``ArtistSummarySchema`` and
``ArtistDetailSchema`` so both survive in one OpenAPI component namespace
without FastAPI module-qualifying either name.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.use_cases.get_artist_detail import GetArtistDetailResult
from src.application.use_cases.list_artists import ListArtistsResult
from src.domain.entities.artist import Artist, ArtistKind
from src.interface.api.schemas.common import PaginatedResponse


class ArtistSummarySchema(BaseModel):
    """One canonical artist in list views."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    mbid: str | None = None
    kind: ArtistKind | None = None
    track_count: int = 0
    is_favorited: bool = False
    connectors: list[str] = Field(default_factory=list)


class PaginatedArtistsResponse(PaginatedResponse[ArtistSummarySchema]):
    """Standard paginated envelope for ``GET /artists``."""


class ArtistConnectorMappingSchema(BaseModel):
    """One live mapping on an artist, with the service's own page for it."""

    model_config = ConfigDict(from_attributes=True)

    connector_name: str
    connector_artist_identifier: str
    name: str
    is_primary: bool = False
    match_method: str = ""
    confidence: int = 0
    external_url: str | None = None


class RelatedProjectSchema(BaseModel):
    """A name a connector payload links to this artist.

    ``relation`` is ``alias``, ``same_as`` or ``member`` — see
    ``GetArtistDetailUseCase`` for where each comes from.
    """

    model_config = ConfigDict(from_attributes=True)

    name: str
    relation: str
    connector_name: str
    identifier: str | None = None


class ArtistDetailSchema(BaseModel):
    """Full artist detail: identity, mappings, counts, favorite state."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    mbid: str | None = None
    kind: ArtistKind | None = None
    track_count: int = 0
    is_favorited: bool = False
    connectors: list[str] = Field(default_factory=list)
    connector_mappings: list[ArtistConnectorMappingSchema] = Field(default_factory=list)
    related: list[RelatedProjectSchema] = Field(default_factory=list)


class FavoriteArtistResponse(BaseModel):
    """Result of a favorite write — ``changed`` is False on a repeat."""

    model_config = ConfigDict(from_attributes=True)

    artist_id: UUID
    is_favorited: bool
    changed: bool


class EnrichArtistsRequest(BaseModel):
    """Body for ``POST /artists/enrich``."""

    limit: int | None = Field(default=None, ge=1, le=10_000)
    refresh_older_than_days: int = Field(default=30, ge=0, le=3650)


def to_artist_summary(artist: Artist, result: ListArtistsResult) -> ArtistSummarySchema:
    """Project one listed artist plus its batched side-map values."""
    return ArtistSummarySchema(
        id=artist.id,
        name=artist.name,
        mbid=artist.mbid,
        kind=artist.kind,
        track_count=result.track_counts.get(artist.id, 0),
        is_favorited=artist.id in result.favorited_ids,
        connectors=result.connector_names.get(artist.id, []),
    )


def to_artist_detail(result: GetArtistDetailResult) -> ArtistDetailSchema:
    """Convert the detail use case result to the API schema."""
    mappings = [
        ArtistConnectorMappingSchema.model_validate(m)
        for m in result.connector_mappings
    ]
    return ArtistDetailSchema(
        id=result.artist.id,
        name=result.artist.name,
        mbid=result.artist.mbid,
        kind=result.artist.kind,
        track_count=result.track_count,
        is_favorited=result.is_favorited,
        # Distinct services, in mapping order — the badge row on the detail
        # header shows the same vocabulary the list page's column does.
        connectors=list(dict.fromkeys(m.connector_name for m in mappings)),
        connector_mappings=mappings,
        related=[RelatedProjectSchema.model_validate(r) for r in result.related],
    )
