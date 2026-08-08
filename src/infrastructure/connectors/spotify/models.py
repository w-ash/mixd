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
- GET /tracks?ids=  → SpotifyTracksResponse (positionally aligned with the ids)
- GET /playlists/{id}  → SpotifyPlaylist (items node: SpotifyPaginatedPlaylistItems)
- GET /playlists/{id}/items  → SpotifyPaginatedPlaylistItems (items: SpotifyPlaylistItem)
- POST|DELETE|PUT /playlists/{id}/items  → SpotifySnapshotResponse
- POST /me/playlists  → SpotifyPlaylist
- GET /me/playlists  → SpotifyUserPlaylistsResponse
  (SimplifiedPlaylistObject per item: `items` is a `{href, total}` summary,
  not a full tracks list — the SpotifyPlaylist.items field parses it via
  all-defaults on SpotifyPaginatedPlaylistItems.)
- GET /me/player/recently-played  → SpotifyRecentlyPlayedResponse
"""

from datetime import datetime
from typing import Annotated, ClassVar, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError


def _none_to_empty_list(v: object) -> object:
    """Coerce Spotify's occasional `null` to `[]` on list fields.

    Spotify's Feb 2026 API migration relaxed nullability on several fields
    (see rspotify#550, psst#721). SimplifiedPlaylistObject.images in
    particular can arrive as `null` despite the spec declaring it a list.
    """
    return [] if v is None else v


type NullSafeList[T] = Annotated[list[T], BeforeValidator(_none_to_empty_list)]


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


class SpotifyRestrictions(SpotifyBaseModel):
    """Why a track is not playable here — Spotify's *positive* answer.

    Present only when the request carried a ``market`` (mixd's client always
    does). ``reason`` is ``market``, ``product`` or ``explicit``; all three mean
    "this id is fine, you just cannot play it in this context", which is the
    opposite of a dead identifier and must never be counted as one.
    """

    reason: str | None = Field(default=None)


class SpotifyTrack(SpotifyBaseModel):
    """Full track object from GET /tracks/{id} and embedded in playlist items."""

    id: str
    name: str
    artists: NullSafeList[SpotifyArtist] = Field(default_factory=list)
    album: SpotifyAlbum | None = Field(default=None)
    duration_ms: int = Field(default=0)
    explicit: bool = Field(default=False)
    external_ids: SpotifyExternalIds = Field(default_factory=SpotifyExternalIds)
    # Availability, not existence. Returned alongside the track, so an id that
    # comes back restricted has *answered* — it is not a miss.
    is_playable: bool | None = Field(default=None)
    restrictions: SpotifyRestrictions | None = Field(default=None)

    @property
    def is_market_restricted(self) -> bool:
        """Returned by the API but unplayable here — available, not dead."""
        return self.is_playable is False or self.restrictions is not None


class SpotifyTracksResponse(SpotifyBaseModel):
    """Response body for GET /tracks?ids= — the batch form of GET /tracks/{id}.

    ``tracks`` is POSITIONALLY ALIGNED with the requested ``ids``: entry *i*
    answers id *i*. Two answers are only distinguishable through that
    alignment, so callers must correlate by index and never by the returned
    ``id``:

    - ``null`` at position *i* — that id is dead (unknown to Spotify).
    - an entry whose ``id`` differs from the requested one — Spotify relinked
      the old id to a new track.
    """

    tracks: NullSafeList[SpotifyTrack | None] = Field(default_factory=list)


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
    total: int = Field(default=0)
    items: NullSafeList[SpotifyPlaylistItem] = Field(default_factory=list)


class SpotifyPlaylist(SpotifyBaseModel):
    """Full playlist object from GET /playlists/{id} and POST /me/playlists."""

    id: str
    name: str
    description: str | None = Field(default=None)
    owner: SpotifyOwner = Field(default_factory=SpotifyOwner)
    public: bool | None = Field(default=None)  # nullable per API spec
    collaborative: bool = Field(default=False)
    snapshot_id: str | None = Field(default=None)
    images: NullSafeList[dict[str, str | int | None]] = Field(default_factory=list)
    followers: SpotifyFollowers | None = Field(default=None)
    items: SpotifyPaginatedPlaylistItems = Field(
        default_factory=SpotifyPaginatedPlaylistItems
    )


class SpotifySnapshotResponse(SpotifyBaseModel):
    """Response from playlist write operations: add, remove, reorder, replace."""

    snapshot_id: str


class SpotifyUserPlaylistsResponse(SpotifyBaseModel):
    """Paging wrapper for GET /me/playlists.

    Each item is a SimplifiedPlaylistObject. Feb 2026 rename: `tracks` →
    `items` on the per-playlist body. Our SpotifyPlaylist model already
    targets `items`, so parsing works for both GET /playlists/{id} (full
    items list) and GET /me/playlists (items summary with href + total).
    """

    href: str = Field(default="")
    limit: int = Field(default=20)
    next: str | None = Field(default=None)
    offset: int = Field(default=0)
    total: int = Field(default=0)
    items: list[SpotifyPlaylist] = Field(default_factory=list)


class SpotifyPlayContext(SpotifyBaseModel):
    """Where a play was started from — playlist, album, artist, or show."""

    type: str | None = Field(default=None)
    uri: str | None = Field(default=None)


class SpotifyPlayHistoryItem(SpotifyBaseModel):
    """One entry from GET /me/player/recently-played.

    ``played_at`` is required — a history entry with no timestamp cannot be
    placed on the observation ledger, so it must fail validation rather than
    default to something invented. Pydantic parses Spotify's RFC-3339 "…Z"
    form into an aware datetime.
    """

    track: SpotifyTrack
    played_at: datetime
    context: SpotifyPlayContext | None = Field(default=None)


def _drop_unusable_history_items(v: object) -> object:
    """Discard recently-played entries that cannot become a ledger observation.

    Whole-page validation is the wrong failure mode for this endpoint: Spotify
    retains only the trailing ~50 plays, so one malformed entry rejecting the
    page would lose all the others *unrecoverably*. An entry is unusable when it
    has no ``track.id`` (a local file, or anything else without a Spotify id) —
    without one there is no ``spotify:track:`` URI, so the play could never be
    resolved anyway. Dropping exactly those keeps the other 49.

    Each entry is validated rather than hand-inspected, so the screen can never
    disagree with the model it guards — and the validated object is *kept*, not
    thrown away. Returning the raw entry instead would make Pydantic parse the
    whole page a second time when it builds the annotated ``list[...]``; model
    instances pass straight through (``revalidate_instances`` is left at its
    ``never`` default), so the screen costs one parse rather than two on every
    poll.
    """
    if not isinstance(v, list):
        return v
    # `isinstance` narrows only to `list[Unknown]`; the cast re-establishes a
    # checkable element type at this JSON boundary without a type suppression.
    incoming = cast("list[object]", v)
    usable: list[SpotifyPlayHistoryItem] = []
    for item in incoming:
        try:
            usable.append(SpotifyPlayHistoryItem.model_validate(item))
        except ValidationError:
            continue
    return usable


class SpotifyRecentlyPlayedResponse(SpotifyBaseModel):
    """Response body for GET /me/player/recently-played.

    The payload's ``cursors`` block is deliberately not modelled: the importer
    derives its resume cursor from the newest ``played_at`` instead, which is
    equal by construction and keeps one source of truth. ``extra="ignore"``
    drops it harmlessly.
    """

    items: Annotated[
        list[SpotifyPlayHistoryItem],
        BeforeValidator(_drop_unusable_history_items),
        BeforeValidator(_none_to_empty_list),
    ] = Field(default_factory=list)
