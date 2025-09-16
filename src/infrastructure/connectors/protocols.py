"""Service connector protocol definitions and configuration types.

This module defines standardized interfaces and configurations for music service
connectors, ensuring consistent behavior across different implementations. It uses
Python's Protocol and TypedDict features to establish clear contracts while maintaining
loose coupling.

Key components:
- ConnectorConfig: TypedDict for standardized service connector configuration
- PlaylistConnectorProtocol: Interface for playlist-capable connectors
- Forward references to core domain models for type checking

These protocols enable modular connector architecture where components can be
swapped without requiring changes to dependent code.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, runtime_checkable

if TYPE_CHECKING:
    from src.domain.entities import ConnectorPlaylist, Playlist, Track


class ConnectorConfig(TypedDict):
    """Type definition for connector configuration.

    A standardized configuration type for connectors to ensure consistent
    structure across all connector implementations.

    Attributes:
        factory: Factory function to create connector instance
        dependencies: Optional list of connector dependencies
        metrics: Optional mapping of metric names to connector metadata fields
    """

    # Required fields
    factory: Callable[[dict[str, Any]], Any]

    # Optional fields (marked using NotRequired)
    dependencies: list[str]
    metrics: dict[str, str]


@runtime_checkable
class TrackMetadataConnector(Protocol):
    """Protocol defining interface for connectors that can fetch complete external track data.

    Provides a unified interface for all connectors to retrieve complete track records from
    external services, eliminating the inconsistency between different connector methods
    (e.g., Spotify's get_tracks_by_ids vs Last.fm's batch_get_track_info). All implementations
    work with Track domain objects and return complete service data keyed by track.id.

    This protocol follows DDD principles by using domain entities (Track) rather than
    primitive types (external service IDs) in the interface.
    """

    async def get_external_track_data(
        self, tracks: list["Track"]
    ) -> dict[int, dict[str, Any]]:
        """Retrieve complete track data from the external service for multiple tracks.

        Args:
            tracks: List of Track domain objects with IDs and connector mappings.
                   Each track should have connector_track_identifiers populated for this service.

        Returns:
            Dictionary mapping track.id to complete service track data dict containing
            all available fields from the external service (e.g., Spotify track object,
            Last.fm track info). Only tracks with successful data retrieval are included
            in the result.

        Raises:
            Exception: Service-specific errors (network, authentication, rate limits)
        """
        ...


@runtime_checkable
class PlaylistConnectorProtocol(Protocol):
    """Protocol defining interface for playlist connectors.

    Defines the standard interface that all playlist-capable connectors must implement,
    including methods for retrieving, creating, and updating playlists.
    """

    async def get_playlist(self, playlist_id: str) -> "ConnectorPlaylist":
        """Fetch a playlist with its tracks from the service.

        Args:
            playlist_id: The service-specific ID of the playlist to retrieve

        Returns:
            A ConnectorPlaylist containing the playlist metadata and tracks
        """
        ...

    async def create_playlist(
        self, name: str, tracks: list["Track"], description: str | None = None
    ) -> str:
        """Create a playlist on the service and return its ID.

        Args:
            name: The name for the new playlist
            tracks: List of tracks to add to the playlist
            description: Optional description for the playlist

        Returns:
            The service-specific ID of the newly created playlist
        """
        ...

    async def update_playlist(
        self, playlist_id: str, playlist: "Playlist", replace: bool = True
    ) -> None:
        """Update a playlist on the service.

        Args:
            playlist_id: The service-specific ID of the playlist to update
            playlist: The playlist object containing the updated content
            replace: Whether to replace all tracks (True) or append (False)
        """
        ...


# Forward references for type annotations
