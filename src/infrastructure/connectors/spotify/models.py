"""Pydantic models for Spotify Web API response shapes.

These models validate raw JSON from the Spotify Web API and exist ONLY
in the infrastructure layer — domain models remain attrs.

Key design decisions:
- extra='ignore': forward-compatible when Spotify adds new fields
- Required fields (id, name on track) have no default — ValidationError if absent
- Optional/nullable fields default to None
- popularity, external_ids, linked_from are deprecated in the API but still returned;
  we model them so existing logic continues working

Endpoint coverage:
- GET /tracks  → SpotifyTrack
- GET /playlists/{id}  → SpotifyPlaylist (tracks node: SpotifyPaginatedPlaylistItems)
- GET /playlists/{id}/tracks  → SpotifyPaginatedPlaylistItems (items: SpotifyPlaylistItem)
- POST|DELETE|PUT /playlists/{id}/tracks  → SpotifySnapshotResponse
- POST /me/playlists  → SpotifyPlaylist
"""

from pydantic import BaseModel, ConfigDict, Field


class SpotifyArtist(BaseModel):
    """Simplified artist as embedded in track objects."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default="")
    name: str = Field(default="")


class SpotifyAlbum(BaseModel):
    """Simplified album as embedded in track objects."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = Field(default=None)
    name: str = Field(default="")
    release_date: str | None = Field(default=None)
    release_date_precision: str = Field(default="day")  # "year" | "month" | "day"


class SpotifyExternalIds(BaseModel):
    """External identifier block — deprecated in API but still returned."""

    model_config = ConfigDict(extra="ignore")

    isrc: str | None = Field(default=None)
    ean: str | None = Field(default=None)
    upc: str | None = Field(default=None)


class SpotifyLinkedFrom(BaseModel):
    """Track relinking metadata — deprecated in API but still returned."""

    model_config = ConfigDict(extra="ignore")

    id: str


class SpotifyTrack(BaseModel):
    """Full track object from GET /tracks and embedded in playlist items."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    artists: list[SpotifyArtist] = Field(default_factory=list)
    album: SpotifyAlbum | None = Field(default=None)
    duration_ms: int = Field(default=0)
    popularity: int = Field(default=0)  # deprecated but still returned
    explicit: bool = Field(default=False)
    external_ids: SpotifyExternalIds = Field(default_factory=SpotifyExternalIds)
    linked_from: SpotifyLinkedFrom | None = Field(
        default=None
    )  # deprecated but still returned


class SpotifyOwner(BaseModel):
    """Playlist owner — has display_name. Reused for added_by where display_name is absent."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default="")
    display_name: str | None = Field(default=None)


class SpotifyFollowers(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total: int = Field(default=0)


class SpotifyPlaylistItem(BaseModel):
    """Single entry from GET /playlists/{id}/tracks.

    Uses `track` field (not `item`) — this is the /tracks endpoint response shape.
    The newer /items endpoint uses `item` for generality (tracks + episodes),
    but this codebase calls /tracks which keeps the original `track` field name.
    """

    model_config = ConfigDict(extra="ignore")

    track: SpotifyTrack | None = Field(default=None)
    added_at: str | None = Field(default=None)
    added_by: SpotifyOwner = Field(default_factory=SpotifyOwner)
    is_local: bool = Field(default=False)


class SpotifyPaginatedPlaylistItems(BaseModel):
    """Paginated tracks container — used as SpotifyPlaylist.tracks AND as the
    standalone response from GET /playlists/{id}/tracks.

    Pagination uses a `next` URL cursor (absolute URL), not page numbers.
    """

    model_config = ConfigDict(extra="ignore")

    href: str = Field(default="")
    limit: int = Field(default=20)
    next: str | None = Field(default=None)
    offset: int = Field(default=0)
    previous: str | None = Field(default=None)
    total: int = Field(default=0)
    items: list[SpotifyPlaylistItem] = Field(default_factory=list)


class SpotifyPlaylist(BaseModel):
    """Full playlist object from GET /playlists/{id} and POST /me/playlists."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    description: str | None = Field(default=None)
    owner: SpotifyOwner = Field(default_factory=SpotifyOwner)
    public: bool | None = Field(default=None)  # nullable per API spec
    collaborative: bool = Field(default=False)
    snapshot_id: str | None = Field(default=None)
    images: list[dict] = Field(default_factory=list)
    followers: SpotifyFollowers | None = Field(default=None)
    tracks: SpotifyPaginatedPlaylistItems = Field(
        default_factory=SpotifyPaginatedPlaylistItems
    )


class SpotifySavedTrack(BaseModel):
    """Single entry from GET /me/tracks (liked/saved tracks)."""

    model_config = ConfigDict(extra="ignore")

    track: SpotifyTrack
    added_at: str | None = Field(default=None)


class SpotifySnapshotResponse(BaseModel):
    """Response from playlist write operations: add, remove, reorder, replace."""

    model_config = ConfigDict(extra="ignore")

    snapshot_id: str
