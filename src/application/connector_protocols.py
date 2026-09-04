"""Connector capability protocols for typed resolver narrowing.

These protocols define what music service connectors can do (read liked tracks,
love tracks, playlist CRUD, fetch metadata). They live at the application layer
so use cases and connector resolvers can reference them without importing from
infrastructure.

Separated from ``workflows.protocols`` to break a circular import chain:
``_shared`` -> ``connector_resolver`` -> ``workflows.protocols`` -> ``workflows/__init__``
-> node factories -> use cases -> ``_shared``.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Protocol, runtime_checkable
from uuid import UUID

from src.domain.entities import ConnectorPlaylist, ConnectorTrack
from src.domain.entities.shared import JsonValue
from src.domain.entities.track import Track
from src.domain.playlist.diff_engine import PlaylistOperation, PlaylistOpsOutcome
from src.domain.repositories.track import TrackRepositoryProtocol


@runtime_checkable
class TrackConversionConnector(Protocol):
    """Connector that can convert raw track data dicts to ConnectorTrack entities.

    Used when processing playlist items that carry full track data in extras
    (e.g., Spotify playlist tracks with embedded track JSON).
    """

    def convert_track_to_connector(
        self, track_data: Mapping[str, JsonValue]
    ) -> ConnectorTrack:
        """Convert raw external track data to a ConnectorTrack domain entity."""
        ...


@runtime_checkable
class Closeable(Protocol):
    """Connector that owns resources (httpx2 pools) requiring explicit cleanup."""

    async def aclose(self) -> None:
        """Release resources held by this connector instance."""
        ...


@runtime_checkable
class TrackMetadataConnector(Protocol):
    """Protocol for connectors that can fetch complete external track data.

    Provides a unified interface for all connectors to retrieve complete track
    records from external services. Defined at application/domain boundary so
    application code can reference it without importing from infrastructure.
    """

    async def get_external_track_data(
        self,
        tracks: list[Track],
        progress_callback: Callable[[int, int, str], Awaitable[None]] | None = None,
    ) -> dict[UUID, Mapping[str, JsonValue]]:
        """Retrieve complete track data from the external service for multiple tracks."""
        ...


@runtime_checkable
class LibraryContainsConnector(Protocol):
    """Connector that can check if items exist in a user's saved library."""

    async def check_library_contains(self, uris: list[str]) -> dict[str, bool]:
        """Check which URIs are saved in the user's library.

        Returns a mapping of URI → True/False.
        """
        ...


@runtime_checkable
class LikedTrackConnector(Protocol):
    """Connector that can read a user's liked/saved tracks."""

    async def get_liked_tracks(
        self, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[ConnectorTrack], str | None, int | None]: ...


@runtime_checkable
class DiscogsCollectionConnector(Protocol):
    """Connector exposing a raw, read-only view of a Discogs collection.

    Raw by design (v0.11.1 Collection Snapshot): no domain entities exist for
    Discogs releases yet — matching and import are v0.13.1's job — so pages
    cross this seam as boundary-validated plain JSON mappings, the same shape
    ``TrackMetadataConnector`` uses for external track data.
    """

    async def get_stored_username(self) -> str | None:
        """Discogs username recorded at connect time; None when not connected."""
        ...

    async def fetch_username(self) -> str | None:
        """Live identity-probe username, backfilled onto the stored token.

        The fallback when the stored token lacks ``account_name``. None when
        Discogs could not be reached; raises the auth-required error when no
        usable token is stored.
        """
        ...

    async def get_collection_page_data(
        self, username: str, *, page: int = 1, per_page: int = 10
    ) -> Mapping[str, JsonValue] | None:
        """One boundary-validated collection page as plain JSON data.

        None means the fetch could not complete (suppressed transport failure).
        """
        ...

    async def save_collection_count(self, count: int) -> None:
        """Refresh the cached collection count the status probe renders."""
        ...


@runtime_checkable
class TidalFavoritesConnector(Protocol):
    """Connector exposing a raw, read-only view of Tidal favorites.

    Raw by design (v0.11.3 Favorites Snapshot, the same posture as
    ``DiscogsCollectionConnector``): no Tidal domain entities exist yet —
    matching and import are v0.13.x's job — so pages and track details cross
    this seam as boundary-validated plain JSON mappings. Tidal has no
    stored-username equivalent (the connect scope carries no identity), so
    unlike Discogs there is no username on this seam — display headers use
    the service name.
    """

    async def get_collection_items_page(
        self, cursor: str | None = None
    ) -> Mapping[str, JsonValue] | None:
        """One boundary-validated favorites page as plain JSON data.

        ``{"items": [{"id", "added_at"}], "next_cursor", "total"}`` —
        ``total`` when Tidal serves ``meta.total``, else None; ``next_cursor``
        None on the last page. None means the fetch could not complete
        (suppressed transport failure).
        """
        ...

    async def get_track_display_data(
        self, track_id: str
    ) -> Mapping[str, JsonValue] | None:
        """Display facts for one track: ``{"title", "artists": [...]}``.

        The favorites relationship carries identifiers + ``addedAt`` only,
        so rendering a row costs one of these per track. None when the track
        cannot be fetched or no longer exists.
        """
        ...

    async def save_favorites_count(self, count: int) -> None:
        """Refresh the cached favorites count the status probe renders."""
        ...


@runtime_checkable
class LoveTrackConnector(Protocol):
    """Connector that can love/like tracks on behalf of a user."""

    async def love_tracks(self, items: Sequence[tuple[str, str]]) -> list[bool]:
        """Love every ``(artist, title)`` pair, one result per input.

        Results keep input order. A per-item failure is ``False``, not an
        exception — a batch is never abandoned because one track failed.
        """
        ...


@runtime_checkable
class UserPlaylistsConnector(Protocol):
    """Connector that can enumerate the authenticated user's own playlists.

    Distinct from ``PlaylistConnector`` because listing every playlist the
    user has access to is a materially different capability than fetching
    or mutating a single known-ID playlist — some connectors may only
    support the latter.
    """

    async def fetch_user_playlists(self) -> list[ConnectorPlaylist]:
        """Return every playlist the authenticated user owns or follows."""
        ...


PlaylistFetchProgress = Callable[[int, int], Awaitable[None]]
"""Narrow pagination-progress callback: ``(fetched_so_far, total)``.

Passed into ``PlaylistConnector.get_playlist`` when the caller wants
per-page progress signals during track pagination. The connector invokes
it once per page (not per track). Designed so infrastructure takes no
dependency on the application's ``ProgressEmitter`` — the caller wraps
the emitter in a closure and passes it here.
"""


@runtime_checkable
class PlaylistConnector(Protocol):
    """Connector that supports playlist fetch and CRUD operations."""

    async def get_playlist(
        self,
        playlist_id: str,
        *,
        on_page: PlaylistFetchProgress | None = None,
    ) -> ConnectorPlaylist:
        """Fetch complete playlist data from the external service.

        When ``on_page`` is provided, the connector invokes it once per
        paginated page of tracks with ``(fetched_so_far, total)`` so the
        caller can emit progress events for long fetches.
        """
        ...

    async def get_playlist_details(self, playlist_id: str) -> dict[str, JsonValue]: ...
    async def execute_playlist_operations(
        self,
        playlist_id: str,
        operations: list[PlaylistOperation],
        snapshot_id: str | None = None,
        track_repo: TrackRepositoryProtocol | None = None,
    ) -> PlaylistOpsOutcome: ...
    async def append_tracks_to_playlist(
        self,
        playlist_id: str,
        tracks: list[Track],
    ) -> dict[str, JsonValue]: ...
    async def update_playlist_metadata(
        self,
        playlist_id: str,
        metadata_updates: dict[str, str],
    ) -> None: ...
    async def create_playlist(
        self,
        name: str,
        tracks: list[Track],
        description: str | None = None,
    ) -> str: ...

    def parse_playlist_identifier(self, raw_input: str) -> str:
        """Normalize a user-supplied playlist identifier to a raw service id.

        Accepts whatever forms the service publishes (URL, URI, bare id).

        Raises:
            ValueError: If the input is empty or cannot be parsed.
        """
        ...
