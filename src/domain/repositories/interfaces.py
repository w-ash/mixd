"""Domain repository interfaces following Clean Architecture principles.

These interfaces define the contracts for data access without depending on
infrastructure implementations, following the dependency inversion principle.
Repository interfaces belong in the domain layer according to Clean Architecture.
"""

from collections.abc import Awaitable
from typing import TYPE_CHECKING, Literal, Protocol, Self

if TYPE_CHECKING:
    # Import domain entities for type annotations
    from datetime import datetime
    from typing import Any

    from src.application.services.external_metadata_service import (
        ExternalMetadataService,
    )
    from src.domain.entities import (
        ConnectorPlaylist,
        ConnectorTrack,
        Playlist,
        SyncCheckpoint,
        Track,
        TrackLike,
        TrackList,
        TrackPlay,
    )
    from src.domain.matching.types import MatchResultsById

    # Music service connector protocol for type hints
    class MusicServiceConnector(Protocol):
        """Protocol for music service connectors used in service operations."""

        async def get_liked_tracks(
            self, limit: int | None = None, cursor: str | None = None
        ) -> tuple[list[ConnectorTrack], str | None]: ...
        async def love_track(self, artist_name: str, track_title: str) -> bool: ...
        async def append_tracks_to_playlist(
            self, playlist_id: str, tracks: list["Track"]
        ) -> dict[str, Any]: ...
        async def update_playlist_metadata(
            self, playlist_id: str, metadata: dict[str, Any]
        ) -> dict[str, Any]: ...
        async def get_playlist_details(self, playlist_id: str) -> dict[str, Any]: ...
        async def create_playlist(
            self, name: str, tracks: list["Track"], description: str | None = None
        ) -> str: ...
        async def execute_playlist_operations(
            self,
            playlist_id: str,
            operations: list[Any],
            snapshot_id: str | None = None,
        ) -> str | None: ...
        async def get_playlist_metadata(self, playlist_id: str) -> dict[str, Any]: ...


class TrackRepositoryProtocol(Protocol):
    """Repository interface for track persistence operations."""

    def save_track(self, track: "Track") -> Awaitable["Track"]:
        """Save track."""
        ...

    def find_tracks_by_ids(self, track_ids: list[int]) -> Awaitable[dict[int, "Track"]]:
        """Find multiple tracks by their internal IDs in a single batch operation.

        Args:
            track_ids: List of internal track IDs to retrieve

        Returns:
            Dictionary mapping track IDs to Track objects
        """
        ...


class PlaylistRepositoryProtocol(Protocol):
    """Repository interface for playlist persistence operations."""

    def get_playlist_by_id(self, playlist_id: int) -> Awaitable["Playlist"]:
        """Get playlist by ID."""
        ...

    def save_playlist(self, playlist: "Playlist") -> Awaitable["Playlist"]:
        """Save playlist."""
        ...

    def get_playlist_by_connector(
        self, connector: str, connector_id: str, raise_if_not_found: bool = True
    ) -> Awaitable["Playlist | None"]:
        """Get playlist by connector ID."""
        ...

    def update_playlist(
        self, playlist_id: int, playlist: "Playlist"
    ) -> Awaitable["Playlist"]:
        """Update existing playlist."""
        ...

    def delete_playlist(self, playlist_id: int) -> Awaitable[bool]:
        """Delete playlist by ID.

        Args:
            playlist_id: Internal playlist ID to delete

        Returns:
            True if playlist was deleted, False if it didn't exist
        """
        ...


class LikeRepositoryProtocol(Protocol):
    """Repository interface for like persistence operations."""

    def get_track_likes(
        self, track_id: int, services: list[str] | None = None
    ) -> Awaitable[list["TrackLike"]]:
        """Get likes for a track across services."""
        ...

    def save_track_like(
        self,
        track_id: int,
        service: str,
        is_liked: bool = True,
        last_synced: "datetime | None" = None,
    ) -> Awaitable["TrackLike"]:
        """Save track like."""
        ...

    def get_all_liked_tracks(
        self, service: str, is_liked: bool = True
    ) -> Awaitable[list["TrackLike"]]:
        """Get all liked tracks for a service."""
        ...

    def get_unsynced_likes(
        self,
        source_service: str,
        target_service: str,
        is_liked: bool = True,
        since_timestamp: "datetime | None" = None,
    ) -> Awaitable[list["TrackLike"]]:
        """Get tracks liked in source_service but not in target_service."""
        ...


class CheckpointRepositoryProtocol(Protocol):
    """Repository interface for sync checkpoint persistence operations."""

    def get_sync_checkpoint(
        self, user_id: str, service: str, entity_type: Literal["likes", "plays"]
    ) -> Awaitable["SyncCheckpoint | None"]:
        """Get sync checkpoint."""
        ...

    def save_sync_checkpoint(
        self, checkpoint: "SyncCheckpoint"
    ) -> Awaitable["SyncCheckpoint"]:
        """Save sync checkpoint."""
        ...


class ConnectorRepositoryProtocol(Protocol):
    """Repository interface for connector track mapping operations."""

    @property
    def session(self) -> "Any":
        """Database session for transaction coordination.

        Used by services that need to create nested transactions for batch operations.
        This follows the pattern where use cases manage transaction scope through
        shared sessions, and services coordinate complex operations using savepoints.
        """
        ...

    def find_track_by_connector(
        self, connector: str, connector_id: str
    ) -> Awaitable["Track | None"]:
        """Find track by connector ID."""
        ...

    def find_connector_track_id(
        self, connector: str, connector_id: str
    ) -> Awaitable["int | None"]:
        """Find connector track database ID by connector name and ID."""
        ...

    def ingest_external_track(
        self,
        connector: str,
        connector_id: str,
        metadata: dict | None,
        title: str,
        artists: list[str],
        album: str | None = None,
        duration_ms: int | None = None,
        release_date: "datetime | None" = None,
        isrc: str | None = None,
        added_at: str | None = None,
    ) -> Awaitable["Track | None"]:
        """Ingest a single track from external source."""
        ...

    def map_track_to_connector(
        self,
        track: "Track",
        connector: str,
        connector_id: str,
        match_method: str,
        confidence: int,
        metadata: dict | None = None,
        confidence_evidence: dict | None = None,
    ) -> Awaitable["Track"]:
        """Map an existing track to a connector."""
        ...

    def get_metadata_timestamps(
        self, track_ids: list[int], connector: str
    ) -> Awaitable[dict[int, "datetime"]]:
        """Get most recent metadata collection timestamps for tracks.

        Args:
            track_ids: Track IDs to check timestamps for.
            connector: Connector name to filter by.

        Returns:
            Dictionary mapping track_id to most recent collected_at timestamp.
        """
        ...

    def get_connector_mappings(
        self, track_ids: list[int], connector: str | None = None
    ) -> Awaitable[dict[int, dict[str, str]]]:
        """Get mappings between tracks and external connectors.

        Args:
            track_ids: Track IDs to get mappings for.
            connector: Optional connector name to filter by.

        Returns:
            Dictionary mapping track_id to connector mapping information.
        """
        ...

    def get_connector_metadata(
        self, track_ids: list[int], connector: str, metadata_field: str | None = None
    ) -> Awaitable[dict[int, "Any"]]:
        """Get connector metadata for tracks.

        Args:
            track_ids: Track IDs to get metadata for.
            connector: Connector name to filter by.
            metadata_field: Optional specific metadata field to retrieve.

        Returns:
            Dictionary mapping track_id to metadata.
        """
        ...

    def save_mapping_confidence(
        self,
        track_id: int,
        connector: str,
        connector_id: str,
        confidence: int,
        match_method: str | None = None,
        confidence_evidence: dict | None = None,
        metadata: dict[str, "Any"] | None = None,
    ) -> Awaitable[bool]:
        """Save confidence information to the track mapping.

        Args:
            track_id: Track ID.
            connector: Connector name.
            connector_id: External connector track ID.
            confidence: Confidence score.
            match_method: Method used for matching.
            confidence_evidence: Evidence supporting the confidence score.
            metadata: Additional metadata.

        Returns:
            True if saved successfully.
        """
        ...

    def find_tracks_by_connectors(
        self, connections: list[tuple[str, str]]
    ) -> Awaitable[dict[tuple[str, str], "Track"]]:
        """Find tracks by connector name and ID in bulk.

        Args:
            connections: List of (connector, connector_id) tuples

        Returns:
            Dictionary mapping (connector, connector_id) tuples to Track objects
        """
        ...

    def ingest_external_tracks_bulk(
        self,
        connector: str,
        tracks: list["ConnectorTrack"],
    ) -> Awaitable[list["Track"]]:
        """Bulk ingest multiple tracks from external connector.

        This is the primary method for track ingestion, optimized for bulk operations.
        Single-track operations are implemented as a special case of this method.

        Args:
            connector: Connector name (e.g., "spotify")
            tracks: List of connector tracks to ingest

        Returns:
            List of successfully ingested Track objects
        """
        ...

    def get_mapping_info(
        self, track_id: int, connector: str, connector_id: str
    ) -> Awaitable[dict]:
        """Get mapping information including confidence and method.

        Args:
            track_id: Internal track ID
            connector: Connector name
            connector_id: External connector track ID

        Returns:
            Dictionary containing mapping metadata
        """
        ...

    def set_primary_mapping(
        self, track_id: int, connector_track_id: int, connector_name: str
    ) -> Awaitable[bool]:
        """Set the primary mapping for a track-connector pair.

        This method handles Spotify track relinking and other scenarios where
        multiple connector tracks map to the same canonical track. It ensures
        only one mapping per (track_id, connector_name) is marked as primary.

        Args:
            track_id: Internal canonical track ID
            connector_track_id: Database ID of the connector track (not external ID)
            connector_name: Name of the connector (e.g., "spotify")

        Returns:
            True if the primary mapping was successfully updated, False otherwise
        """
        ...


class ConnectorPlaylistRepositoryProtocol(Protocol):
    """Repository interface for connector playlist operations."""

    def upsert_model(
        self, connector_playlist: "ConnectorPlaylist"
    ) -> Awaitable["ConnectorPlaylist"]:
        """Upsert a connector playlist directly from a domain model.

        Args:
            connector_playlist: ConnectorPlaylist domain model to upsert

        Returns:
            Updated ConnectorPlaylist model
        """
        ...

    def get_by_connector_id(
        self, connector: str, connector_id: str
    ) -> Awaitable["ConnectorPlaylist | None"]:
        """Get connector playlist by connector and external ID.

        Args:
            connector: Connector name (e.g., "spotify")
            connector_id: External playlist ID

        Returns:
            ConnectorPlaylist if found, None otherwise
        """
        ...


class MetricsRepositoryProtocol(Protocol):
    """Repository interface for track metrics operations."""

    def save_track_metrics(
        self,
        metrics: list[tuple[int, str, str, float]],
    ) -> Awaitable[int]:
        """Save metrics for multiple tracks efficiently.

        Args:
            metrics: List of (track_id, metric_name, metric_source, metric_value) tuples

        Returns:
            Number of metrics saved
        """
        ...

    def get_track_metrics(
        self,
        track_ids: list[int],
        metric_type: str = "play_count",
        connector: str = "lastfm",
        max_age_hours: float = 24.0,
    ) -> Awaitable[dict[int, "Any"]]:
        """Get cached metrics with TTL awareness.

        Args:
            track_ids: List of track IDs to get metrics for
            metric_type: Type of metric to retrieve
            connector: Connector that provided the metrics
            max_age_hours: Maximum age of metrics to accept (in hours)

        Returns:
            Dictionary mapping track IDs to their metric values
        """
        ...


class PlaysRepositoryProtocol(Protocol):
    """Repository interface for play history operations."""

    def bulk_insert_plays(self, plays: list["TrackPlay"]) -> Awaitable[int]:
        """Bulk insert plays."""
        ...

    def get_recent_plays(self, limit: int = 100) -> Awaitable[list["TrackPlay"]]:
        """Get recent plays."""
        ...

    def get_play_aggregations(
        self,
        track_ids: list[int],
        metrics: list[str],
        period_start: "datetime | None" = None,
        period_end: "datetime | None" = None,
    ) -> Awaitable[dict[str, dict[int, "Any"]]]:
        """Get aggregated play data for specified tracks and metrics.

        Args:
            track_ids: List of track IDs to get play data for
            metrics: List of metrics to calculate ["total_plays", "last_played_dates", "period_plays"]
            period_start: Start date for period-based metrics (optional)
            period_end: End date for period-based metrics (optional)

        Returns:
            Dictionary mapping metric names to {track_id: value} dictionaries
        """
        ...


class TrackIdentityServiceProtocol(Protocol):
    """Service interface for track identity resolution operations.

    This protocol defines the interface for resolving track identities across
    music services. It abstracts the implementation details of identity resolution
    to support Clean Architecture dependency inversion.
    """

    def resolve_track_identities(
        self,
        track_list: "TrackList",
        connector: str,
        connector_instance: "Any",
        **additional_options: "Any",
    ) -> Awaitable["MatchResultsById"]:
        """Resolve track identities between internal tracks and external connector tracks.

        Args:
            track_list: Tracks to resolve identities for.
            connector: Target connector name.
            connector_instance: Connector implementation.
            **additional_options: Options forwarded to providers.

        Returns:
            Track IDs mapped to MatchResult objects containing identity mappings.
        """
        ...


class ServiceConnectorProvider(Protocol):
    """Provider for accessing individual music service connectors.

    This protocol defines the interface for getting instances of specific
    music service connectors (Spotify, Last.fm, etc.) that can perform
    operations like getting liked tracks or loving tracks.
    """

    def get_connector(self, service_name: str) -> "MusicServiceConnector":
        """Get connector instance for specified music service.

        Args:
            service_name: Name of the service (e.g., "spotify", "lastfm")

        Returns:
            Connector instance that implements MusicServiceConnector protocol
        """
        ...


class UnitOfWorkProtocol(Protocol):
    """Unit of Work interface for transaction boundary management.

    This protocol follows Clean Architecture principles by allowing the application
    layer to control transaction boundaries while keeping the implementation details
    in the infrastructure layer. Each UnitOfWork instance manages a single database
    transaction and provides access to all repositories sharing that transaction.
    """

    async def __aenter__(self) -> Self:
        """Enter async context manager."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit async context manager with automatic commit/rollback."""
        ...

    async def commit(self) -> None:
        """Explicitly commit the current transaction."""
        ...

    async def rollback(self) -> None:
        """Explicitly rollback the current transaction."""
        ...

    def get_track_repository(self) -> TrackRepositoryProtocol:
        """Get track repository using this unit of work's transaction."""
        ...

    def get_playlist_repository(self) -> PlaylistRepositoryProtocol:
        """Get playlist repository using this unit of work's transaction."""
        ...

    def get_like_repository(self) -> LikeRepositoryProtocol:
        """Get like repository using this unit of work's transaction."""
        ...

    def get_checkpoint_repository(self) -> CheckpointRepositoryProtocol:
        """Get checkpoint repository using this unit of work's transaction."""
        ...

    def get_connector_repository(self) -> ConnectorRepositoryProtocol:
        """Get connector repository using this unit of work's transaction."""
        ...

    def get_metrics_repository(self) -> MetricsRepositoryProtocol:
        """Get metrics repository using this unit of work's transaction."""
        ...

    def get_plays_repository(self) -> PlaysRepositoryProtocol:
        """Get plays repository using this unit of work's transaction."""
        ...

    def get_track_identity_service(self) -> TrackIdentityServiceProtocol:
        """Get track identity service using this unit of work's transaction."""
        ...

    def get_service_connector_provider(self) -> ServiceConnectorProvider:
        """Get service connector provider for accessing music service connectors."""
        ...

    def get_connector_playlist_repository(
        self,
    ) -> "ConnectorPlaylistRepositoryProtocol":
        """Get connector playlist repository for playlist-related operations."""
        ...

    def get_external_metadata_service(self) -> "ExternalMetadataService":
        """Get external metadata service using this unit of work's transaction."""
        ...


# RepositoryProvider deleted - violated Interface Segregation Principle
# Use cases should depend on specific repository interfaces they need
