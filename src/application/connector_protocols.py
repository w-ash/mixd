"""Connector capability protocols for typed resolver narrowing.

These protocols define what music service connectors can do (read liked tracks,
love tracks, playlist CRUD, fetch metadata). They live at the application layer
so use cases and connector resolvers can reference them without importing from
infrastructure.

Separated from ``workflows.protocols`` to break a circular import chain:
``_shared`` -> ``connector_resolver`` -> ``workflows.protocols`` -> ``workflows/__init__``
-> node factories -> use cases -> ``_shared``.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: use case results, OperationResult metadata, metric values

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from src.domain.entities import ConnectorPlaylist, ConnectorTrack
from src.domain.entities.track import Track
from src.domain.playlist.diff_engine import PlaylistOperation
from src.domain.repositories.interfaces import TrackRepositoryProtocol


class TrackConversionConnector(Protocol):
    """Connector that can convert raw track data dicts to ConnectorTrack entities.

    Used when processing playlist items that carry full track data in extras
    (e.g., Spotify playlist tracks with embedded track JSON).
    """

    def convert_track_to_connector(self, track_data: dict[str, Any]) -> ConnectorTrack:
        """Convert raw external track data to a ConnectorTrack domain entity."""
        ...


@runtime_checkable
class Closeable(Protocol):
    """Connector that owns resources (httpx pools) requiring explicit cleanup."""

    async def aclose(self) -> None:
        """Release resources held by this connector instance."""
        ...


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
    ) -> dict[UUID, dict[str, Any]]:
        """Retrieve complete track data from the external service for multiple tracks."""
        ...


class LibraryContainsConnector(Protocol):
    """Connector that can check if items exist in a user's saved library."""

    async def check_library_contains(self, uris: list[str]) -> dict[str, bool]:
        """Check which URIs are saved in the user's library.

        Returns a mapping of URI → True/False.
        """
        ...


class LikedTrackConnector(Protocol):
    """Connector that can read a user's liked/saved tracks."""

    async def get_liked_tracks(
        self, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[ConnectorTrack], str | None, int | None]: ...


class LoveTrackConnector(Protocol):
    """Connector that can love/like tracks on behalf of a user."""

    async def love_track(self, artist: str, title: str) -> bool: ...


class PlaylistConnector(Protocol):
    """Connector that supports playlist fetch and CRUD operations."""

    async def get_playlist(self, playlist_id: str) -> ConnectorPlaylist:
        """Fetch complete playlist data from the external service."""
        ...

    async def get_playlist_details(self, playlist_id: str) -> dict[str, Any]: ...
    async def execute_playlist_operations(
        self,
        playlist_id: str,
        operations: list[PlaylistOperation],
        snapshot_id: str | None = None,
        track_repo: TrackRepositoryProtocol | None = None,
    ) -> str | None: ...
    async def append_tracks_to_playlist(
        self,
        playlist_id: str,
        tracks: list[Track],
    ) -> dict[str, Any]: ...
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
