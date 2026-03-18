"""Domain repository interfaces following Clean Architecture principles.

These interfaces define the contracts for data access without depending on
infrastructure implementations, following the dependency inversion principle.
Repository interfaces belong in the domain layer according to Clean Architecture.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: service_metadata, raw_data dicts, factory patterns

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal, Protocol, Self, TypedDict

from src.domain.entities import (
    ConnectorPlaylist,
    ConnectorTrack,
    ConnectorTrackPlay,
    OperationResult,
    Playlist,
    PlaylistLink,
    SyncCheckpoint,
    Track,
    TrackLike,
    TrackMapping,
    TrackPlay,
)
from src.domain.entities.match_review import MatchReview
from src.domain.entities.playlist_link import SyncDirection, SyncStatus
from src.domain.entities.workflow import (
    RunStatus,
    Workflow,
    WorkflowRun,
    WorkflowRunNode,
    WorkflowVersion,
)
from src.domain.matching.types import (
    MatchResultsById,
    ProgressCallback,
    RawProviderMatch,
)


class TrackListingPage(TypedDict):
    """Result shape for paginated track listing queries."""

    tracks: list[Track]
    total: int | None  # None when count was skipped (cursor-paginated pages)
    liked_track_ids: set[int]
    next_page_key: tuple[Any, int] | None


class TrackRepositoryProtocol(Protocol):
    """Repository interface for track persistence operations."""

    def count_all_tracks(self) -> Awaitable[int]:
        """Count all tracks in the database."""
        ...

    def save_track(self, track: Track) -> Awaitable[Track]:
        """Save track."""
        ...

    def get_by_id(
        self, id_: int, load_relationships: list[str] | None = None
    ) -> Awaitable[Track]:
        """Get track by ID."""
        ...

    def find_tracks_by_ids(self, track_ids: list[int]) -> Awaitable[dict[int, Track]]:
        """Find multiple tracks by their internal IDs in a single batch operation.

        Args:
            track_ids: List of internal track IDs to retrieve

        Returns:
            Dictionary mapping track IDs to Track objects
        """
        ...

    def move_references_to_track(self, from_id: int, to_id: int) -> Awaitable[None]:
        """Move all foreign key references (playlist tracks, plays, likes) from one track to another.

        Handles conflict resolution for likes where both tracks have entries
        for the same service (keeps the most recently synced state).

        Args:
            from_id: Source track ID whose references will be moved.
            to_id: Destination track ID that will receive the references.
        """
        ...

    def merge_mappings_to_track(self, from_id: int, to_id: int) -> Awaitable[None]:
        """Merge connector mappings from one track to another with conflict resolution.

        Handles two cases:
        - Same connector + same external ID: keep the higher-confidence mapping
        - Same connector + different external IDs: keep both, destination's stays primary

        Args:
            from_id: Source track ID whose mappings will be merged.
            to_id: Destination track ID that will receive the mappings.
        """
        ...

    def merge_metrics_to_track(self, from_id: int, to_id: int) -> Awaitable[None]:
        """Merge track metrics from one track to another with conflict resolution.

        For duplicate (connector_name, metric_type) pairs, keeps the most
        recently collected value.

        Args:
            from_id: Source track ID whose metrics will be merged.
            to_id: Destination track ID that will receive the metrics.
        """
        ...

    def hard_delete_track(self, track_id: int) -> Awaitable[None]:
        """Permanently delete a track record from the database.

        This bypasses soft-delete and removes the row entirely. Should only be
        used after all references have been moved away (e.g., during merge).

        Args:
            track_id: ID of the track to permanently delete.
        """
        ...

    def list_tracks(
        self,
        *,
        query: str | None = None,
        liked: bool | None = None,
        connector: str | None = None,
        sort_by: str = "title_asc",
        limit: int = 50,
        offset: int = 0,
        after_value: Any = None,
        after_id: int | None = None,
        include_total: bool = True,
    ) -> Awaitable[TrackListingPage]:
        """List tracks with optional search, filters, sorting, and pagination.

        Supports both offset-based and keyset (cursor) pagination. When
        ``after_value`` and ``after_id`` are provided, keyset pagination
        seeks directly to the next page in O(1). Falls back to OFFSET otherwise.

        Args:
            query: Text search across title, artist, album.
            liked: Filter by canonical liked status (liked on any service).
            connector: Filter by connector mapping presence.
            sort_by: Sort field and direction.
            limit: Maximum tracks to return.
            offset: Number of tracks to skip (ignored when keyset params present).
            after_value: Sort column value of the last row from the previous page.
            after_id: Primary key of the last row from the previous page.
            include_total: Whether to run the count query. False skips it and returns
                total=None (useful for cursor-paginated pages where the frontend
                already has the total from page 1).

        Returns:
            TrackListingPage with tracks, total, liked_track_ids, and next_page_key.
        """
        ...

    def find_tracks_by_title_artist(
        self, pairs: list[tuple[str, str]]
    ) -> Awaitable[dict[tuple[str, str], Track]]:
        """Find existing tracks by (title, first_artist) pairs (case-insensitive).

        Args:
            pairs: List of (title, first_artist_name) tuples to search for.

        Returns:
            Dict keyed by lowercased (title, artist) → Track.
        """
        ...

    def find_tracks_by_isrcs(self, isrcs: list[str]) -> Awaitable[dict[str, Track]]:
        """Batch lookup tracks by ISRC.

        Args:
            isrcs: Normalized ISRC strings to search for.

        Returns:
            Dict keyed by ISRC → Track.
        """
        ...

    def find_tracks_by_mbids(self, mbids: list[str]) -> Awaitable[dict[str, Track]]:
        """Batch lookup tracks by MusicBrainz Recording ID.

        Args:
            mbids: MBID strings to search for.

        Returns:
            Dict keyed by MBID → Track.
        """
        ...

    def find_duplicate_tracks_by_fingerprint(
        self,
    ) -> Awaitable[list[dict[str, object]]]:
        """Find tracks with identical (title, first_artist, album) tuples.

        Returns:
            List of dicts with title, artist, album, track_ids, count.
        """
        ...


class PlaylistRepositoryProtocol(Protocol):
    """Repository interface for playlist persistence operations."""

    def count_all_playlists(self) -> Awaitable[int]:
        """Count all playlists in the database."""
        ...

    def get_playlist_by_id(self, playlist_id: int) -> Awaitable[Playlist]:
        """Get playlist by ID."""
        ...

    def save_playlist(self, playlist: Playlist) -> Awaitable[Playlist]:
        """Save playlist."""
        ...

    def get_playlist_by_connector(
        self, connector: str, connector_id: str, raise_if_not_found: bool = True
    ) -> Awaitable[Playlist | None]:
        """Get playlist by connector ID."""
        ...

    def update_playlist(
        self, playlist_id: int, playlist: Playlist
    ) -> Awaitable[Playlist]:
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

    def list_all_playlists(self) -> Awaitable[list[Playlist]]:
        """Get all playlists with basic metadata for listing.

        Returns playlists with minimal relationship loading for efficient
        listing operations. Suitable for CLI display and management interfaces.

        Returns:
            List of all stored playlists with basic metadata
        """
        ...

    def get_playlists_for_track(self, track_id: int) -> Awaitable[list[Playlist]]:
        """Get all playlists containing a specific track.

        Args:
            track_id: Internal track ID.

        Returns:
            List of playlists that contain the given track.
        """
        ...


class LikeRepositoryProtocol(Protocol):
    """Repository interface for like persistence operations."""

    def count_total_liked(self) -> Awaitable[int]:
        """Count tracks liked on any service (DISTINCT track_id where is_liked=true)."""
        ...

    def count_liked_by_service(self) -> Awaitable[dict[str, int]]:
        """Count liked tracks grouped by service (single query)."""
        ...

    def get_track_likes(
        self, track_id: int, services: list[str] | None = None
    ) -> Awaitable[list[TrackLike]]:
        """Get likes for a track across services."""
        ...

    def save_track_like(
        self,
        track_id: int,
        service: str,
        is_liked: bool = True,
        last_synced: datetime | None = None,
        liked_at: datetime | None = None,
    ) -> Awaitable[TrackLike]:
        """Save track like.

        Args:
            track_id: Internal track ID.
            service: Service name ('spotify', 'lastfm', 'narada').
            is_liked: Whether the track is liked.
            last_synced: When this like was last synced.
            liked_at: When the user originally liked the track. Falls back to now() if not provided.
        """
        ...

    def save_track_likes_batch(
        self,
        likes: list[tuple[int, str, bool, datetime | None, datetime | None]],
    ) -> Awaitable[list[TrackLike]]:
        """Save multiple track likes in bulk.

        Args:
            likes: List of (track_id, service, is_liked, last_synced, liked_at) tuples.

        Returns:
            List of saved TrackLike domain objects.
        """
        ...

    def get_all_liked_tracks(
        self, service: str, is_liked: bool = True, sort_by: str | None = None
    ) -> Awaitable[list[TrackLike]]:
        """Get all liked tracks for a service.

        Args:
            service: Service to get likes from
            is_liked: Filter by like status
            sort_by: Optional sorting method (liked_at_desc, liked_at_asc, title_asc, random)
        """
        ...

    def get_liked_status_batch(
        self,
        track_ids: list[int],
        services: list[str],
    ) -> Awaitable[dict[int, dict[str, bool]]]:
        """Check like status for multiple tracks across services.

        Returns:
            Mapping of track_id → {service: is_liked}.
            Missing entries mean no like record exists (treat as False).
        """
        ...

    def count_liked_tracks(self, service: str, is_liked: bool = True) -> Awaitable[int]:
        """Count tracks with the given like status for a service.

        More efficient than get_all_liked_tracks when only the count is needed,
        as it avoids hydrating domain objects.

        Args:
            service: Service to count likes for
            is_liked: Filter by like status
        """
        ...

    def get_unsynced_likes(
        self,
        source_service: str,
        target_service: str,
        is_liked: bool = True,
        since_timestamp: datetime | None = None,
    ) -> Awaitable[list[TrackLike]]:
        """Get tracks liked in source_service but not in target_service."""
        ...


class CheckpointRepositoryProtocol(Protocol):
    """Repository interface for sync checkpoint persistence operations."""

    def get_sync_checkpoint(
        self, user_id: str, service: str, entity_type: Literal["likes", "plays"]
    ) -> Awaitable[SyncCheckpoint | None]:
        """Get sync checkpoint."""
        ...

    def save_sync_checkpoint(
        self, checkpoint: SyncCheckpoint
    ) -> Awaitable[SyncCheckpoint]:
        """Save sync checkpoint."""
        ...


class MatchMethodStatRow(TypedDict):
    """Aggregated statistics for a single match_method + connector_name combination."""

    match_method: str
    connector_name: str
    total_count: int
    recent_count: int  # within recent_days window
    avg_confidence: float
    min_confidence: int
    max_confidence: int


class FullMappingInfo(TypedDict):
    """Complete mapping data for track detail views including connector track metadata."""

    mapping_id: int
    connector_name: str
    connector_track_id: str
    match_method: str
    confidence: int
    origin: str
    is_primary: bool
    connector_track_title: str
    connector_track_artists: list[str]


class ConnectorRepositoryProtocol(Protocol):
    """Repository interface for connector track mapping operations."""

    def get_full_mappings_for_track(
        self, track_id: int
    ) -> Awaitable[list[FullMappingInfo]]:
        """Get all mappings for a track with full connector track metadata.

        Returns ALL mappings (not just primary), joined with connector track
        title/artists so the UI can show what each service calls the track.

        Args:
            track_id: Internal canonical track ID.

        Returns:
            List of FullMappingInfo dicts with complete provenance data.
        """
        ...

    def count_tracks_by_connector(self) -> Awaitable[dict[str, int]]:
        """Count distinct tracks per connector (excluding internal pseudo-connectors)."""
        ...

    def map_track_to_connector(
        self,
        track: Track,
        connector: str,
        connector_id: str,
        match_method: str,
        confidence: int,
        metadata: dict[str, object] | None = None,
        confidence_evidence: dict[str, object] | None = None,
        auto_set_primary: bool = True,
        origin: str = "automatic",
    ) -> Awaitable[Track]:
        """Map an existing track to a connector.

        Args:
            track: The track to map
            connector: Service name (e.g., "spotify", "lastfm")
            connector_id: External track ID
            match_method: How the match was determined
            confidence: Match confidence score
            metadata: Optional service-specific metadata
            confidence_evidence: Optional evidence for the confidence score
            auto_set_primary: Whether to automatically set this as the primary mapping
            origin: Mapping origin — "automatic" (default) or "manual_override"

        Returns:
            The updated track object
        """
        ...

    def get_connector_mappings(
        self, track_ids: list[int], connector: str | None = None
    ) -> Awaitable[dict[int, dict[str, str]]]:
        """Get primary mappings between tracks and external connectors.

        Returns only primary mappings — for tracks with multiple connector IDs
        (e.g., Spotify relinking), only the active/current ID is returned.

        Args:
            track_ids: Track IDs to get mappings for.
            connector: Optional connector name to filter by.

        Returns:
            Dictionary mapping track_id to {connector_name: external_id} for primary mappings.
        """
        ...

    def get_connector_metadata(
        self, track_ids: list[int], connector: str, metadata_field: str | None = None
    ) -> Awaitable[dict[int, Any]]:
        """Get connector metadata for tracks from primary mappings only.

        Returns metadata from the primary mapping for each track-connector pair.
        For relinked tracks with multiple mappings, only the active mapping's
        metadata is returned.

        Args:
            track_ids: Track IDs to get metadata for.
            connector: Connector name to filter by.
            metadata_field: Optional specific metadata field to retrieve.

        Returns:
            Dictionary mapping track_id to metadata from primary mapping.
        """
        ...

    def find_tracks_by_connectors(
        self, connections: list[tuple[str, str]]
    ) -> Awaitable[dict[tuple[str, str], Track]]:
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
        tracks: list[ConnectorTrack],
    ) -> Awaitable[list[Track]]:
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

    def ensure_primary_mapping(
        self, track_id: int, connector: str, connector_id: str
    ) -> Awaitable[bool]:
        """Ensure a mapping exists and is set as primary for the given track-connector pair.

        This method is used when we know a specific external ID should be the primary
        mapping (e.g., when Spotify returns a track ID in an API response).

        Args:
            track_id: Internal canonical track ID
            connector: Service name (e.g., "spotify")
            connector_id: External track ID that should be primary

        Returns:
            True if primary mapping was successfully set
        """
        ...

    def batch_ensure_primary_mappings(
        self, primaries: list[tuple[int, str, str]]
    ) -> Awaitable[int]:
        """Set primary mappings for multiple track-connector pairs in bulk.

        Each tuple is (track_id, connector_name, connector_track_identifier).

        Args:
            primaries: List of (track_id, connector_name, connector_track_identifier).

        Returns:
            Number of mappings successfully promoted to primary.
        """
        ...

    def set_primary_mapping(
        self, track_id: int, connector_name: str, connector_track_id: int
    ) -> Awaitable[bool]:
        """Set the primary mapping for a track-connector pair.

        This method handles Spotify track relinking and other scenarios where
        multiple connector tracks map to the same canonical track. It ensures
        only one mapping per (track_id, connector_name) is marked as primary.

        Args:
            track_id: Internal canonical track ID
            connector_name: Name of the connector (e.g., "spotify")
            connector_track_id: Database ID of the connector track (not external ID)

        Returns:
            True if the primary mapping was successfully updated, False otherwise
        """
        ...

    def get_mapping_by_id(self, mapping_id: int) -> Awaitable[TrackMapping | None]:
        """Get a single track mapping by its database ID.

        Args:
            mapping_id: Database ID of the mapping row.

        Returns:
            TrackMapping domain entity if found, None otherwise.
        """
        ...

    def delete_mapping(self, mapping_id: int) -> Awaitable[TrackMapping]:
        """Delete a track mapping and return the pre-deletion entity.

        Args:
            mapping_id: Database ID of the mapping to delete.

        Returns:
            The deleted TrackMapping entity (pre-deletion snapshot).

        Raises:
            NotFoundError: If no mapping exists with the given ID.
        """
        ...

    def update_mapping_track(
        self, mapping_id: int, new_track_id: int, origin: str
    ) -> Awaitable[TrackMapping]:
        """Move a mapping to a different canonical track.

        Updates track_id and origin on the mapping, resets is_primary to False
        (primary must be reassigned separately).

        Args:
            mapping_id: Database ID of the mapping to update.
            new_track_id: Target canonical track ID.
            origin: New origin value (e.g., "manual_override").

        Returns:
            Updated TrackMapping entity.

        Raises:
            NotFoundError: If no mapping exists with the given ID.
        """
        ...

    def count_mappings_for_connector_track(
        self, connector_track_id: int
    ) -> Awaitable[int]:
        """Count remaining mappings for a given connector track.

        Used by unlink to detect orphaned connector tracks.

        Args:
            connector_track_id: Database ID of the connector track.

        Returns:
            Number of mappings referencing the connector track.
        """
        ...

    def get_remaining_mappings(
        self, track_id: int, connector_name: str
    ) -> Awaitable[list[TrackMapping]]:
        """Get all mappings for a (track, connector) pair, ordered by confidence desc.

        Used to pick the next primary mapping after removing one.

        Args:
            track_id: Canonical track ID.
            connector_name: Connector name to filter by.

        Returns:
            List of TrackMapping entities, highest confidence first.
        """
        ...

    def get_connector_track_by_id(
        self, connector_track_id: int
    ) -> Awaitable[ConnectorTrack | None]:
        """Get a connector track entity by its database ID.

        Args:
            connector_track_id: Database ID of the connector track.

        Returns:
            ConnectorTrack domain entity if found, None otherwise.
        """
        ...

    def ensure_primary_for_connector(
        self, track_id: int, connector_name: str
    ) -> Awaitable[None]:
        """Ensure a primary mapping exists for a (track, connector) pair.

        If no mappings remain, clears the denormalized ID column.
        If mappings exist but none is primary, promotes the highest-confidence one
        and syncs the denormalized ID.
        If a primary already exists, does nothing.

        Args:
            track_id: Canonical track ID.
            connector_name: Connector name.
        """
        ...

    def find_multiple_primary_violations(self) -> Awaitable[list[dict[str, object]]]:
        """Find tracks with more than one primary mapping per connector.

        Returns:
            List of dicts with track_id, connector_name, primary_count.
        """
        ...

    def find_missing_primary_violations(self) -> Awaitable[list[dict[str, object]]]:
        """Find tracks with mappings for a connector but none marked primary.

        Returns:
            List of dicts with track_id, connector_name, mapping_count.
        """
        ...

    def count_orphaned_connector_tracks(self) -> Awaitable[int]:
        """Count connector tracks with no track_mappings pointing to them."""
        ...

    def get_match_method_stats(
        self, recent_days: int = 30
    ) -> Awaitable[list[MatchMethodStatRow]]:
        """Aggregate match method statistics grouped by method and connector.

        Args:
            recent_days: Window for recent_count (mappings created within this many days).

        Returns:
            Rows ordered by total_count descending.
        """
        ...


class ConnectorPlaylistRepositoryProtocol(Protocol):
    """Repository interface for connector playlist operations."""

    def upsert_model(
        self, connector_playlist: ConnectorPlaylist
    ) -> Awaitable[ConnectorPlaylist]:
        """Upsert a connector playlist directly from a domain model.

        Args:
            connector_playlist: ConnectorPlaylist domain model to upsert

        Returns:
            Updated ConnectorPlaylist model
        """
        ...

    def get_by_connector_id(
        self, connector: str, connector_id: str
    ) -> Awaitable[ConnectorPlaylist | None]:
        """Get connector playlist by connector and external ID.

        Args:
            connector: Connector name (e.g., "spotify")
            connector_id: External playlist ID

        Returns:
            ConnectorPlaylist if found, None otherwise
        """
        ...


class PlaylistLinkRepositoryProtocol(Protocol):
    """Repository interface for playlist link (mapping) operations."""

    def get_links_for_playlist(self, playlist_id: int) -> Awaitable[list[PlaylistLink]]:
        """Get all connector links for a canonical playlist."""
        ...

    def get_link(self, link_id: int) -> Awaitable[PlaylistLink | None]:
        """Get a single playlist link by ID."""
        ...

    def create_link(self, link: PlaylistLink) -> Awaitable[PlaylistLink]:
        """Create a new playlist link. Ensures the DBConnectorPlaylist exists."""
        ...

    def update_sync_status(
        self,
        link_id: int,
        status: SyncStatus,
        *,
        error: str | None = None,
        tracks_added: int | None = None,
        tracks_removed: int | None = None,
    ) -> Awaitable[None]:
        """Update the sync status and optional metrics for a link."""
        ...

    def update_link_direction(
        self, link_id: int, direction: SyncDirection
    ) -> Awaitable[PlaylistLink | None]:
        """Update the sync direction for a link. Returns the updated link, or None if not found."""
        ...

    def count_links_by_connector(self) -> Awaitable[dict[str, int]]:
        """Count linked playlists grouped by connector name."""
        ...

    def delete_link(self, link_id: int) -> Awaitable[bool]:
        """Delete a playlist link. Returns True if deleted."""
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
    ) -> Awaitable[dict[int, float]]:
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


class PlayAggregationResult(TypedDict, total=False):
    """Typed result from play aggregation queries.

    Each key maps track IDs to their aggregated value. All keys are optional
    (total=False) because callers request specific metric subsets.
    """

    total_plays: dict[int, int]
    first_played_dates: dict[int, datetime | None]
    last_played_dates: dict[int, datetime | None]
    period_plays: dict[int, int]


class PlaysRepositoryProtocol(Protocol):
    """Repository interface for play history operations."""

    def count_all_plays(self) -> Awaitable[int]:
        """Count all play records in the database."""
        ...

    def count_plays_by_service(self) -> Awaitable[dict[str, int]]:
        """Count play records grouped by service (single query)."""
        ...

    def bulk_insert_plays(self, plays: list[TrackPlay]) -> Awaitable[tuple[int, int]]:
        """Bulk insert plays.

        Returns:
            tuple[int, int]: (inserted_count, duplicate_count)
        """
        ...

    def get_recent_plays(
        self, limit: int = 100, sort_by: str | None = None
    ) -> Awaitable[list[TrackPlay]]:
        """Get recent plays.

        Args:
            limit: Maximum number of plays to return
            sort_by: Optional sorting method (played_at_desc, total_plays_desc, last_played_desc, title_asc, random)
        """
        ...

    def get_play_aggregations(
        self,
        track_ids: list[int],
        metrics: list[str],
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> Awaitable[PlayAggregationResult]:
        """Get aggregated play data for specified tracks and metrics.

        Args:
            track_ids: List of track IDs to get play data for
            metrics: List of metrics to calculate ["total_plays", "last_played_dates", "period_plays"]
            period_start: Start date for period-based metrics (optional)
            period_end: End date for period-based metrics (optional)

        Returns:
            Typed dictionary mapping metric names to {track_id: value} dictionaries.
        """
        ...

    def find_plays_in_time_range(
        self,
        track_ids: list[int],
        start: datetime,
        end: datetime,
    ) -> Awaitable[list[TrackPlay]]:
        """Find existing plays for given tracks within a time range.

        Used by cross-source deduplication to find candidate matches.
        """
        ...

    def bulk_update_play_source_services(
        self,
        updates: list[tuple[int, dict[str, Any]]],
    ) -> Awaitable[None]:
        """Batch-update cross-source dedup metadata for multiple plays."""
        ...


class ConnectorPlayRepositoryProtocol(Protocol):
    """Repository interface for connector play operations.

    Handles raw play data from external music services before resolution to canonical plays.
    Follows the same clean pattern as other connector repositories with simple resolution tracking.
    """

    def bulk_insert_connector_plays(
        self, connector_plays: list[ConnectorTrackPlay]
    ) -> Awaitable[tuple[int, int]]:
        """Bulk insert connector plays from external API data.

        Args:
            connector_plays: List of ConnectorTrackPlay domain objects from API ingestion

        Returns:
            tuple[int, int]: (inserted_count, duplicate_count)
        """
        ...


class TrackIdentityServiceProtocol(Protocol):
    """Service interface for track identity resolution operations.

    This protocol defines the interface for resolving track identities across
    music services. It abstracts the implementation details of identity resolution
    to support Clean Architecture dependency inversion.
    """

    def get_raw_external_matches(
        self,
        tracks: list[Track],
        connector: str,
        connector_instance: object,
        progress_callback: ProgressCallback | None = None,
        **additional_options: Any,
    ) -> Awaitable[dict[int, RawProviderMatch]]:
        """Get raw matches from external providers without business logic.

        Args:
            tracks: Tracks to get raw matches for (must have database IDs).
            connector: Target connector name.
            connector_instance: Connector implementation.
            progress_callback: Optional async callback invoked with
                (completed_count, total, description) after each matching phase.
            **additional_options: Options forwarded to providers.

        Returns:
            Track IDs mapped to raw provider match data.
        """
        ...

    def get_existing_identity_mappings(
        self, track_ids: list[int], connector: str
    ) -> Awaitable[MatchResultsById]:
        """Retrieve existing identity mappings from database.

        Args:
            track_ids: Track IDs to check for existing mappings.
            connector: Target connector name.

        Returns:
            Track IDs mapped to MatchResult objects for existing identity mappings.
        """
        ...

    def persist_identity_mappings(
        self, matches: MatchResultsById, connector: str
    ) -> Awaitable[None]:
        """Save identity mappings to database.

        Args:
            matches: Track IDs mapped to MatchResult objects.
            connector: Target connector name.
        """
        ...


class ServiceConnectorProvider(Protocol):
    """Provider for accessing individual music service connectors.

    This protocol defines the interface for getting instances of specific
    music service connectors (Spotify, Last.fm, etc.) that can perform
    operations like getting liked tracks or loving tracks.
    """

    def get_connector(self, service_name: str) -> object:
        """Get connector instance for specified music service.

        Args:
            service_name: Name of the service (e.g., "spotify", "lastfm")

        Returns:
            Connector instance for the specified service.
            Callers narrow via capability protocols (PlaylistConnector, etc.).
        """
        ...


class WorkflowRepositoryProtocol(Protocol):
    """Repository interface for workflow persistence operations."""

    def list_workflows(
        self, *, include_templates: bool = True
    ) -> Awaitable[list[Workflow]]:
        """List all workflows, optionally filtering out templates."""
        ...

    def get_workflow_by_id(self, workflow_id: int) -> Awaitable[Workflow]:
        """Get workflow by ID. Raises NotFoundError if not found."""
        ...

    def save_workflow(self, workflow: Workflow) -> Awaitable[Workflow]:
        """Create or update a workflow."""
        ...

    def delete_workflow(self, workflow_id: int) -> Awaitable[bool]:
        """Delete workflow by ID. Returns True if deleted."""
        ...

    def get_workflow_by_source_template(
        self, source_template: str
    ) -> Awaitable[Workflow | None]:
        """Find workflow by source template key. Returns None if not found."""
        ...


class WorkflowRunRepositoryProtocol(Protocol):
    """Repository interface for workflow run history persistence."""

    def create_run(self, run: WorkflowRun) -> Awaitable[WorkflowRun]:
        """Persist a new workflow run record."""
        ...

    def update_run_status(
        self,
        run_id: int,
        status: RunStatus,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        duration_ms: int | None = None,
        output_track_count: int | None = None,
        output_playlist_id: int | None = None,
        error_message: str | None = None,
    ) -> Awaitable[None]:
        """Update run status and optional completion fields."""
        ...

    def save_node_record(self, node: WorkflowRunNode) -> Awaitable[WorkflowRunNode]:
        """Persist a new node execution record."""
        ...

    def update_node_status(
        self,
        run_id: int,
        node_id: str,
        status: RunStatus,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        duration_ms: int | None = None,
        input_track_count: int | None = None,
        output_track_count: int | None = None,
        error_message: str | None = None,
    ) -> Awaitable[None]:
        """Update a node's status and execution metrics."""
        ...

    def get_runs_for_workflow(
        self, workflow_id: int, limit: int = 20, offset: int = 0
    ) -> Awaitable[tuple[list[WorkflowRun], int]]:
        """List runs for a workflow (without nodes loaded) with total count."""
        ...

    def get_run_by_id(self, run_id: int) -> Awaitable[WorkflowRun]:
        """Get a single run with all node records loaded."""
        ...

    def get_latest_run_for_workflow(
        self, workflow_id: int
    ) -> Awaitable[WorkflowRun | None]:
        """Get the most recent run for a workflow, or None."""
        ...

    def get_latest_runs_for_workflows(
        self, workflow_ids: list[int]
    ) -> Awaitable[dict[int, WorkflowRun]]:
        """Batch-fetch the latest run for each workflow ID."""
        ...


class WorkflowVersionRepositoryProtocol(Protocol):
    """Repository interface for workflow version history."""

    def create_version(self, version: WorkflowVersion) -> Awaitable[WorkflowVersion]:
        """Persist a new version snapshot."""
        ...

    def list_versions(self, workflow_id: int) -> Awaitable[list[WorkflowVersion]]:
        """List all versions for a workflow, ordered by version desc."""
        ...

    def get_version(self, workflow_id: int, version: int) -> Awaitable[WorkflowVersion]:
        """Get a specific version. Raises NotFoundError if not found."""
        ...

    def get_max_version_number(self, workflow_id: int) -> Awaitable[int]:
        """Return the highest version number for a workflow, or 0 if none exist."""
        ...

    def delete_versions_for_workflow(self, workflow_id: int) -> Awaitable[None]:
        """Delete all versions for a workflow (cascade cleanup)."""
        ...


class MatchReviewRepositoryProtocol(Protocol):
    """Repository interface for match review queue operations."""

    def list_pending_reviews(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "confidence_desc",
    ) -> Awaitable[tuple[list[MatchReview], int]]:
        """List pending reviews with pagination and sorting."""
        ...

    def get_review_by_id(self, review_id: int) -> Awaitable[MatchReview | None]:
        """Get a single review by ID."""
        ...

    def create_review(self, review: MatchReview) -> Awaitable[MatchReview]:
        """Create a new match review entry."""
        ...

    def create_reviews_batch(self, reviews: list[MatchReview]) -> Awaitable[int]:
        """Create multiple review entries, skipping duplicates."""
        ...

    def update_review_status(
        self, review_id: int, status: str
    ) -> Awaitable[MatchReview]:
        """Update a review's status (accept/reject)."""
        ...

    def count_pending(self) -> Awaitable[int]:
        """Count pending reviews."""
        ...

    def count_stale_pending(self, older_than_days: int) -> Awaitable[int]:
        """Count pending reviews older than the given threshold."""
        ...


class UnitOfWorkProtocol(Protocol):  # noqa: PLR0904
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
        _exc_val: BaseException | None,
        _exc_tb: object,
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

    def get_playlist_link_repository(self) -> PlaylistLinkRepositoryProtocol:
        """Get playlist link repository for managing canonical-to-external playlist mappings."""
        ...

    def get_connector_playlist_repository(
        self,
    ) -> ConnectorPlaylistRepositoryProtocol:
        """Get connector playlist repository for playlist-related operations."""
        ...

    def get_connector_play_repository(self) -> ConnectorPlayRepositoryProtocol:
        """Get connector play repository for play ingestion and resolution operations."""
        ...

    def get_workflow_repository(self) -> WorkflowRepositoryProtocol:
        """Get workflow repository using this unit of work's transaction."""
        ...

    def get_workflow_run_repository(self) -> WorkflowRunRepositoryProtocol:
        """Get workflow run repository using this unit of work's transaction."""
        ...

    def get_workflow_version_repository(self) -> WorkflowVersionRepositoryProtocol:
        """Get workflow version repository using this unit of work's transaction."""
        ...

    def get_match_review_repository(self) -> MatchReviewRepositoryProtocol:
        """Get match review repository for review queue operations."""
        ...

    def get_track_merge_service(self) -> TrackMergeServiceProtocol:
        """Get track merge service using this unit of work's transaction."""
        ...


class TrackMergeServiceProtocol(Protocol):
    """Service interface for track merging operations."""

    def merge_tracks(
        self, winner_id: int, loser_id: int, uow: UnitOfWorkProtocol
    ) -> Awaitable[Track]:
        """Merge two canonical tracks by moving references and soft-deleting loser.

        Args:
            winner_id: Track ID that will keep all references.
            loser_id: Track ID that will be soft-deleted.
            uow: Unit of work for transaction management.

        Returns:
            Winner track after merge.
        """
        ...


class PlayImporterProtocol(Protocol):
    """Protocol for play import services in infrastructure layer.

    Infrastructure classes implement this to provide service-specific play import
    capabilities (Last.fm API, Spotify file export, etc.). Application layer
    orchestrates via PlayImportOrchestrator without knowing the concrete service.
    """

    async def import_plays(
        self, uow: UnitOfWorkProtocol, **params: Any
    ) -> tuple[OperationResult, list[ConnectorTrackPlay]]:
        """Import plays and return result with connector plays for resolution."""
        ...


class ResolutionMetrics(TypedDict, total=False):
    """Metrics produced by play resolution.

    All keys are optional (total=False) because different resolvers
    (Spotify, Last.fm) emit different subsets of metrics.
    """

    raw_plays: int
    accepted_plays: int
    duration_excluded: int
    incognito_excluded: int
    error_count: int
    resolution_failures: list[dict[str, str]]
    new_tracks_count: int
    updated_tracks_count: int
    unique_tracks_processed: int
    tracks_resolved: int
    spotify_enhanced_count: int


class PlayResolverProtocol(Protocol):
    """Protocol for play resolution services in infrastructure layer.

    Resolves raw ConnectorTrackPlay objects to canonical TrackPlay objects
    by looking up or creating canonical tracks for each external ID.
    """

    async def resolve_connector_plays(
        self,
        connector_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> tuple[list[TrackPlay], ResolutionMetrics]:
        """Resolve connector plays to canonical track plays.

        Args:
            connector_plays: Raw plays from external service.
            uow: Unit of work for database operations.
            progress_callback: Optional progress reporting.

        Returns:
            Tuple of (resolved track plays, resolution metrics).
        """
        ...
