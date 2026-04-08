"""Pydantic models for Spotify Web API response shapes.

These models validate raw JSON from the Spotify Web API and exist ONLY
in the infrastructure layer — domain models remain attrs.

Key design decisions:
- extra='ignore': forward-compatible when Spotify adds new fields
- Required fields (id, name on track) have no default — ValidationError if absent
- Optional/nullable fields default to None
- external_ids: DEPRECATED (Feb 2026 migration guide lists as removed).
  Still returned empirically as of March 2026. Guarded with defaults
  so removal won't crash — ISRC will just be None.
- popularity and linked_from removed in Feb 2026

Endpoint coverage:
- GET /tracks/{id}  → SpotifyTrack
- GET /playlists/{id}  → SpotifyPlaylist (items node: SpotifyPaginatedPlaylistItems)
- GET /playlists/{id}/items  → SpotifyPaginatedPlaylistItems (items: SpotifyPlaylistItem)
- POST|DELETE|PUT /playlists/{id}/items  → SpotifySnapshotResponse
- POST /me/playlists  → SpotifyPlaylist
"""

# Legitimate Any: API response data, framework types

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


class SpotifyBaseModel(BaseModel):
    """Base model for all Spotify API response shapes.

    Declares the shared model_config once — all subclasses inherit it.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")


class SpotifyArtist(SpotifyBaseModel):
    """Simplified artist as embedded in track objects."""

    id: str = Field(default="")
    name: str = Field(default="")


class SpotifyAlbum(SpotifyBaseModel):
    """Simplified album as embedded in track objects."""

    id: str | None = Field(default=None)
    name: str = Field(default="")
    release_date: str | None = Field(default=None)
    release_date_precision: str = Field(default="day")  # "year" | "month" | "day"


class SpotifyExternalIds(SpotifyBaseModel):
    """External identifier block — deprecated in API but still returned."""

    isrc: str | None = Field(default=None)
    ean: str | None = Field(default=None)
    upc: str | None = Field(default=None)


class SpotifyTrack(SpotifyBaseModel):
    """Full track object from GET /tracks/{id} and embedded in playlist items."""

    id: str
    name: str
    artists: list[SpotifyArtist] = Field(default_factory=list)
    album: SpotifyAlbum | None = Field(default=None)
    duration_ms: int = Field(default=0)
    explicit: bool = Field(default=False)
    external_ids: SpotifyExternalIds = Field(default_factory=SpotifyExternalIds)


class SpotifyOwner(SpotifyBaseModel):
    """Playlist owner — has display_name. Reused for added_by where display_name is absent."""

    id: str = Field(default="")
    display_name: str | None = Field(default=None)


class SpotifyFollowers(SpotifyBaseModel):
    total: int = Field(default=0)


class SpotifyPlaylistItem(SpotifyBaseModel):
    """Single entry from GET /playlists/{id}/items.

    Uses `item` field — the /items endpoint response shape (Feb 2026 API).
    """

    item: SpotifyTrack | None = Field(default=None)
    added_at: str | None = Field(default=None)
    added_by: SpotifyOwner = Field(default_factory=SpotifyOwner)
    is_local: bool = Field(default=False)


class SpotifyPaginatedPlaylistItems(SpotifyBaseModel):
    """Paginated items container for GET /playlists/{id}/items.

    Pagination uses a `next` URL cursor (absolute URL), not page numbers.
    """

    href: str = Field(default="")
    limit: int = Field(default=20)
    next: str | None = Field(default=None)
    offset: int = Field(default=0)
    previous: str | None = Field(default=None)
    total: int = Field(default=0)
    items: list[SpotifyPlaylistItem] = Field(default_factory=list)


class SpotifyPlaylist(SpotifyBaseModel):
    """Full playlist object from GET /playlists/{id} and POST /me/playlists."""

    id: str
    name: str
    description: str | None = Field(default=None)
    owner: SpotifyOwner = Field(default_factory=SpotifyOwner)
    public: bool | None = Field(default=None)  # nullable per API spec
    collaborative: bool = Field(default=False)
    snapshot_id: str | None = Field(default=None)
    images: list[dict[str, Any]] = Field(default_factory=list)
    followers: SpotifyFollowers | None = Field(default=None)
    items: SpotifyPaginatedPlaylistItems = Field(
        default_factory=SpotifyPaginatedPlaylistItems
    )


class SpotifySnapshotResponse(SpotifyBaseModel):
    """Response from playlist write operations: add, remove, reorder, replace."""

    snapshot_id: str
