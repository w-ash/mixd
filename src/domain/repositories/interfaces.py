"""Domain repository interfaces following Clean Architecture principles.

These interfaces define the contracts for data access without depending on
infrastructure implementations, following the dependency inversion principle.
Repository interfaces belong in the domain layer according to Clean Architecture.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Literal, Protocol, Self, TypedDict, overload
from uuid import UUID

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
    TrackMetric,
    TrackPlay,
)
from src.domain.entities.match_review import MatchReview
from src.domain.entities.playlist_assignment import (
    PlaylistAssignment,
    PlaylistAssignmentMember,
)
from src.domain.entities.playlist_link import SyncDirection, SyncStatus
from src.domain.entities.preference import (
    PreferenceEvent,
    PreferenceState,
    TrackPreference,
)
from src.domain.entities.shared import JsonDict, JsonValue, SortKey
from src.domain.entities.sourced_metadata import MetadataSource
from src.domain.entities.tag import TagEvent, TrackTag
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


class TrackFacets(TypedDict):
    """Per-facet counts scoped to the currently-applied filters.

    Counts are *contextual with self* — every active filter (including the
    one being counted) is applied. Simpler and faster than peel-away-self
    semantics; revisit if user feedback demands the Algolia shape.
    """

    preference: dict[str, int]  # "star"|"yah"|"hmm"|"nah"|"unrated" → count
    liked: dict[str, int]  # "true"|"false" → count
    connector: dict[str, int]  # connector name → count


class TrackListingPage(TypedDict):
    """Result shape for paginated track listing queries."""

    tracks: list[Track]
    total: int | None  # None when count was skipped (cursor-paginated pages)
    liked_track_ids: set[UUID]
    # Cursor value type depends on the active sort column: str (title,
    # artists_text), int (duration_ms), or datetime (created_at). The
    # application layer's PageCursor encodes/decodes for the wire.
    next_page_key: tuple[str | int | datetime | None, UUID] | None
    # Facet counts over the current filter set. None when not requested.
    facets: TrackFacets | None


class TrackRepositoryProtocol(Protocol):
    """Repository interface for track persistence operations."""

    def save_track(self, track: Track) -> Awaitable[Track]:
        """Save track."""
        ...

    def get_by_id(
        self, id_: UUID, load_relationships: list[str] | None = None
    ) -> Awaitable[Track]:
        """Get track by ID (unscoped — for infrastructure-internal use only)."""
        ...

    def get_track_by_id(
        self,
        track_id: UUID,
        *,
        user_id: str,
        load_relationships: list[str] | None = None,
    ) -> Awaitable[Track]:
        """Get track by ID, scoped to user. Raises NotFoundError if not found or wrong user."""
        ...

    def find_tracks_by_ids(self, track_ids: list[UUID]) -> Awaitable[dict[UUID, Track]]:
        """Find multiple tracks by their internal IDs in a single batch operation.

        Args:
            track_ids: List of internal track IDs to retrieve

        Returns:
            Dictionary mapping track IDs to Track objects
        """
        ...

    def move_references_to_track(self, from_id: UUID, to_id: UUID) -> Awaitable[None]:
        """Move all foreign key references (playlist tracks, plays, likes) from one track to another.

        Handles conflict resolution for likes where both tracks have entries
        for the same service (keeps the most recently synced state).

        Args:
            from_id: Source track ID whose references will be moved.
            to_id: Destination track ID that will receive the references.
        """
        ...

    def merge_mappings_to_track(self, from_id: UUID, to_id: UUID) -> Awaitable[None]:
        """Merge connector mappings from one track to another with conflict resolution.

        Handles two cases:
        - Same connector + same external ID: keep the higher-confidence mapping
        - Same connector + different external IDs: keep both, destination's stays primary

        Args:
            from_id: Source track ID whose mappings will be merged.
            to_id: Destination track ID that will receive the mappings.
        """
        ...

    def merge_metrics_to_track(self, from_id: UUID, to_id: UUID) -> Awaitable[None]:
        """Merge track metrics from one track to another with conflict resolution.

        For duplicate (connector_name, metric_type) pairs, keeps the most
        recently collected value.

        Args:
            from_id: Source track ID whose metrics will be merged.
            to_id: Destination track ID that will receive the metrics.
        """
        ...

    def hard_delete_track(self, track_id: UUID) -> Awaitable[None]:
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
        user_id: str,
        query: str | None = None,
        liked: bool | None = None,
        connector: str | None = None,
        preference: str | None = None,
        tags: Sequence[str] | None = None,
        tag_mode: Literal["and", "or"] = "and",
        namespace: str | None = None,
        sort_by: str = "title_asc",
        limit: int = 50,
        offset: int = 0,
        after_value: SortKey | None = None,
        after_id: UUID | None = None,
        include_total: bool = True,
        include_facets: bool = False,
    ) -> Awaitable[TrackListingPage]:
        """List tracks with optional search, filters, sorting, and pagination.

        Supports both offset-based and keyset (cursor) pagination. When
        ``after_value`` and ``after_id`` are provided, keyset pagination
        seeks directly to the next page in O(1). Falls back to OFFSET otherwise.

        Args:
            query: Text search across title, artist, album.
            liked: Filter by canonical liked status (liked on any service).
            connector: Filter by connector mapping presence.
            tags: Filter to tracks carrying the given tag(s). When set,
                ``tag_mode`` picks intersection ("and") or union ("or") semantics.
            tag_mode: Combine multi-tag filters. ``"and"`` returns tracks
                carrying every listed tag; ``"or"`` returns tracks carrying
                any listed tag. Ignored when ``tags`` is None.
            namespace: Filter to tracks carrying any tag whose namespace
                matches (e.g. ``"mood"`` → any ``mood:*`` tag).
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
        self, pairs: list[tuple[str, str]], *, user_id: str
    ) -> Awaitable[dict[tuple[str, str], Track]]:
        """Find existing tracks by (title, first_artist) pairs (case-insensitive).

        Args:
            pairs: List of (title, first_artist_name) tuples to search for.

        Returns:
            Dict keyed by lowercased (title, artist) → Track.
        """
        ...

    def find_tracks_by_isrcs(
        self, isrcs: list[str], *, user_id: str
    ) -> Awaitable[dict[str, Track]]:
        """Batch lookup tracks by ISRC.

        Args:
            isrcs: Normalized ISRC strings to search for.

        Returns:
            Dict keyed by ISRC → Track.
        """
        ...

    def find_tracks_by_mbids(
        self, mbids: list[str], *, user_id: str
    ) -> Awaitable[dict[str, Track]]:
        """Batch lookup tracks by MusicBrainz Recording ID.

        Args:
            mbids: MBID strings to search for.

        Returns:
            Dict keyed by MBID → Track.
        """
        ...

    def find_duplicate_tracks_by_fingerprint(
        self, *, user_id: str
    ) -> Awaitable[list[dict[str, object]]]:
        """Find tracks with identical (title, first_artist, album) tuples.

        Returns:
            List of dicts with title, artist, album, track_ids, count.
        """
        ...


class PlaylistRepositoryProtocol(Protocol):
    """Repository interface for playlist persistence operations."""

    def get_playlist_by_id(
        self, playlist_id: UUID, *, user_id: str
    ) -> Awaitable[Playlist]:
        """Get playlist by ID. Returns NotFoundError if wrong user (IDOR prevention)."""
        ...

    def save_playlist(self, playlist: Playlist) -> Awaitable[Playlist]:
        """Save playlist."""
        ...

    def save_playlists_batch(
        self, playlists: Sequence[Playlist]
    ) -> Awaitable[list[Playlist]]:
        """Bulk-create N canonical playlists with entries in one round-trip.

        Requires every ``entry.track.id`` to be populated — raises
        ``ValueError`` otherwise. Connector mappings are NOT written here;
        pair with ``PlaylistLinkRepositoryProtocol.create_links_batch``.
        """
        ...

    def get_playlist_by_connector(
        self,
        connector: str,
        connector_id: str,
        *,
        user_id: str,
        raise_if_not_found: bool = True,
    ) -> Awaitable[Playlist | None]:
        """Get playlist by connector ID."""
        ...

    def update_playlist(
        self, playlist_id: UUID, playlist: Playlist, *, user_id: str
    ) -> Awaitable[Playlist]:
        """Update existing playlist, verifying ownership."""
        ...

    def delete_playlist(self, playlist_id: UUID, *, user_id: str) -> Awaitable[bool]:
        """Delete playlist by ID, verifying ownership.

        Args:
            playlist_id: Internal playlist ID to delete
            user_id: Owner's user ID for ownership verification

        Returns:
            True if playlist was deleted, False if it didn't exist
        """
        ...

    def list_all_playlists(self, *, user_id: str) -> Awaitable[list[Playlist]]:
        """Get all playlists with basic metadata for listing.

        Returns playlists with minimal relationship loading for efficient
        listing operations. Suitable for CLI display and management interfaces.

        Returns:
            List of user's stored playlists with basic metadata
        """
        ...

    def get_playlists_for_track(
        self, track_id: UUID, *, user_id: str
    ) -> Awaitable[list[Playlist]]:
        """Get all playlists containing a specific track.

        Args:
            track_id: Internal track ID.

        Returns:
            List of playlists that contain the given track.
        """
        ...


class LikeRepositoryProtocol(Protocol):
    """Repository interface for like persistence operations."""

    def get_track_likes(
        self, track_id: UUID, *, user_id: str, services: list[str] | None = None
    ) -> Awaitable[list[TrackLike]]:
        """Get likes for a track across services."""
        ...

    def save_track_like(
        self,
        track_id: UUID,
        service: str,
        *,
        user_id: str,
        is_liked: bool = True,
        last_synced: datetime | None = None,
        liked_at: datetime | None = None,
    ) -> Awaitable[TrackLike]:
        """Save track like.

        Args:
            track_id: Internal track ID.
            service: Service name ('spotify', 'lastfm', 'mixd').
            user_id: Owner's user ID.
            is_liked: Whether the track is liked.
            last_synced: When this like was last synced.
            liked_at: When the user originally liked the track. Falls back to now() if not provided.
        """
        ...

    def save_track_likes_batch(
        self,
        likes: list[tuple[UUID, str, bool, datetime | None, datetime | None]],
        *,
        user_id: str,
    ) -> Awaitable[list[TrackLike]]:
        """Save multiple track likes in bulk.

        Args:
            likes: List of (track_id, service, is_liked, last_synced, liked_at) tuples.
            user_id: Owner's user ID.

        Returns:
            List of saved TrackLike domain objects.
        """
        ...

    def get_all_liked_tracks(
        self,
        service: str,
        *,
        user_id: str,
        is_liked: bool = True,
        sort_by: str | None = None,
    ) -> Awaitable[list[TrackLike]]:
        """Get all liked tracks for a service.

        Args:
            service: Service to get likes from
            user_id: Owner's user ID.
            is_liked: Filter by like status
            sort_by: Optional sorting method (liked_at_desc, liked_at_asc, title_asc, random)
        """
        ...

    def get_liked_status_batch(
        self,
        track_ids: list[UUID],
        services: list[str],
        *,
        user_id: str,
    ) -> Awaitable[dict[UUID, dict[str, bool]]]:
        """Check like status for multiple tracks across services.

        Returns:
            Mapping of track_id → {service: is_liked}.
            Missing entries mean no like record exists (treat as False).
        """
        ...

    def count_liked_tracks(
        self, service: str, *, user_id: str, is_liked: bool = True
    ) -> Awaitable[int]:
        """Count tracks with the given like status for a service.

        More efficient than get_all_liked_tracks when only the count is needed,
        as it avoids hydrating domain objects.

        Args:
            service: Service to count likes for
            user_id: Owner's user ID.
            is_liked: Filter by like status
        """
        ...

    def get_unsynced_likes(
        self,
        source_service: str,
        target_service: str,
        *,
        user_id: str,
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

    mapping_id: UUID
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
        self, track_id: UUID, *, user_id: str
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

    def map_tracks_to_connectors(
        self,
        mappings: list[
            tuple[
                Track,
                str,
                str,
                str,
                int,
                dict[str, object] | None,
                dict[str, object] | None,
            ]
        ],
    ) -> Awaitable[list[Track]]:
        """Batch-map multiple tracks to connectors in a single operation.

        Args:
            mappings: List of (track, service_name, external_id, match_method,
                    confidence, metadata, confidence_evidence) tuples.

        Returns:
            List of Track objects updated with external service connections.
        """
        ...

    def get_connector_mappings(
        self, track_ids: list[UUID], connector: str | None = None
    ) -> Awaitable[dict[UUID, dict[str, str]]]:
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

    @overload
    def get_connector_metadata(
        self,
        track_ids: list[UUID],
        connector: str,
        metadata_field: None = ...,
    ) -> Awaitable[dict[UUID, JsonDict]]: ...

    @overload
    def get_connector_metadata(
        self,
        track_ids: list[UUID],
        connector: str,
        metadata_field: str,
    ) -> Awaitable[dict[UUID, JsonValue]]: ...

    def get_connector_metadata(
        self,
        track_ids: list[UUID],
        connector: str,
        metadata_field: str | None = None,
    ) -> Awaitable[dict[UUID, JsonDict] | dict[UUID, JsonValue]]:
        """Get connector metadata for tracks from primary mappings only.

        Returns metadata from the primary mapping for each track-connector pair.
        When ``metadata_field`` is None, returns the full ``JsonDict`` per track.
        When set, extracts that specific field's value (which is itself a ``JsonValue``).
        """
        ...

    def find_tracks_by_connectors(
        self, connections: list[tuple[str, str]], *, user_id: str
    ) -> Awaitable[dict[tuple[str, str], Track]]:
        """Find tracks by connector name and ID in bulk.

        Args:
            connections: List of (connector, connector_id) tuples
            user_id: Owner's user ID.

        Returns:
            Dictionary mapping (connector, connector_id) tuples to Track objects
        """
        ...

    def ingest_external_tracks_bulk(
        self,
        connector: str,
        tracks: list[ConnectorTrack],
        *,
        user_id: str,
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

    def ensure_connector_tracks(
        self,
        connector_name: str,
        tracks_data: Sequence[Mapping[str, object]],
    ) -> Awaitable[dict[tuple[str, str], UUID]]:
        """Ensure connector_tracks rows exist, returning a (name, external_id) -> UUID map.

        Each dict in tracks_data must have keys: connector_id, title, artists (list[str]).
        Optional keys: album, duration_ms, isrc, release_date, raw_metadata.

        Upserts on (connector_name, connector_track_identifier). Idempotent — safe
        to call for tracks that already have connector_tracks rows.

        Args:
            connector_name: Service name (e.g., "spotify", "lastfm").
            tracks_data: List of dicts with connector track metadata.

        Returns:
            Mapping of (connector_name, external_id) to database UUID.
        """
        ...

    def ensure_primary_mapping(
        self, track_id: UUID, connector: str, connector_id: str
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
        self, primaries: list[tuple[UUID, str, str]]
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
        self, track_id: UUID, connector_name: str, connector_track_id: UUID
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

    def get_mapping_by_id(
        self, mapping_id: UUID, *, user_id: str
    ) -> Awaitable[TrackMapping | None]:
        """Get a single track mapping by its database ID, scoped to user.

        Args:
            mapping_id: Database ID of the mapping row.
            user_id: Authenticated user ID for ownership scoping.

        Returns:
            TrackMapping domain entity if found and owned by user, None otherwise.
        """
        ...

    def delete_mapping(
        self, mapping_id: UUID, *, user_id: str
    ) -> Awaitable[TrackMapping]:
        """Delete a track mapping and return the pre-deletion entity.

        Args:
            mapping_id: Database ID of the mapping to delete.
            user_id: Authenticated user ID for ownership scoping.

        Returns:
            The deleted TrackMapping entity (pre-deletion snapshot).

        Raises:
            NotFoundError: If no mapping exists with the given ID.
        """
        ...

    def update_mapping_track(
        self, mapping_id: UUID, new_track_id: UUID, origin: str
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
        self, connector_track_id: UUID
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
        self, track_id: UUID, connector_name: str
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
        self, connector_track_id: UUID
    ) -> Awaitable[ConnectorTrack | None]:
        """Get a connector track entity by its database ID.

        Args:
            connector_track_id: Database ID of the connector track.

        Returns:
            ConnectorTrack domain entity if found, None otherwise.
        """
        ...

    def ensure_primary_for_connector(
        self, track_id: UUID, connector_name: str
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
        self, *, user_id: str, recent_days: int = 30
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

    def list_by_connector(self, connector: str) -> Awaitable[list[ConnectorPlaylist]]:
        """List every cached playlist for a connector.

        Connector playlists are a cross-user cache — the browse UI reads
        from here after the fetch-and-upsert pass, so all users see the
        same Spotify playlist metadata without re-fetching.
        """
        ...

    def bulk_upsert_models(
        self, connector_playlists: Sequence[ConnectorPlaylist]
    ) -> Awaitable[list[ConnectorPlaylist]]:
        """Bulk upsert N connector playlists in a single round-trip.

        Returns domain models with IDs populated. ``upsert_model`` is the
        one-element degenerate case.
        """
        ...


class PlaylistLinkRepositoryProtocol(Protocol):
    """Repository interface for playlist link (mapping) operations."""

    def get_links_for_playlist(
        self, playlist_id: UUID
    ) -> Awaitable[list[PlaylistLink]]:
        """Get all connector links for a canonical playlist."""
        ...

    def list_by_user_connector(
        self, user_id: str, connector_name: str
    ) -> Awaitable[list[PlaylistLink]]:
        """Every playlist link for a given user on a given connector.

        Used by the Spotify browser to compute per-playlist import status
        (not-imported / imported / mapped) via set lookup against
        ``connector_playlist_identifier``.
        """
        ...

    def get_link(self, link_id: UUID) -> Awaitable[PlaylistLink | None]:
        """Get a single playlist link by ID."""
        ...

    def create_link(self, link: PlaylistLink) -> Awaitable[PlaylistLink]:
        """Create a new playlist link. Ensures the DBConnectorPlaylist exists."""
        ...

    def create_links_batch(
        self, links: Sequence[PlaylistLink]
    ) -> Awaitable[list[PlaylistLink]]:
        """Bulk-insert N playlist links. Returns only links actually inserted;
        duplicates (by (playlist_id, connector_name)) are skipped silently."""
        ...

    def update_sync_status(
        self,
        link_id: UUID,
        status: SyncStatus,
        *,
        error: str | None = None,
        tracks_added: int | None = None,
        tracks_removed: int | None = None,
    ) -> Awaitable[None]:
        """Update the sync status and optional metrics for a link."""
        ...

    def update_link_direction(
        self, link_id: UUID, direction: SyncDirection
    ) -> Awaitable[PlaylistLink | None]:
        """Update the sync direction for a link. Returns the updated link, or None if not found."""
        ...

    def delete_link(self, link_id: UUID) -> Awaitable[bool]:
        """Delete a playlist link. Returns True if deleted."""
        ...


class MetricsRepositoryProtocol(Protocol):
    """Repository interface for track metrics operations."""

    def save_track_metrics(
        self,
        metrics: list[TrackMetric],
    ) -> Awaitable[int]:
        """Save metrics for multiple tracks efficiently.

        Args:
            metrics: List of ``TrackMetric`` entities — bool-valued metrics
                must be coerced to ``float`` at the construction boundary
                (the DB column is ``float``).

        Returns:
            Number of metrics saved
        """
        ...

    def get_track_metrics(
        self,
        track_ids: list[UUID],
        metric_type: str = "play_count",
        connector: str = "lastfm",
        max_age_hours: float = 24.0,
    ) -> Awaitable[dict[UUID, float]]:
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

    total_plays: dict[UUID, int]
    first_played_dates: dict[UUID, datetime | None]
    last_played_dates: dict[UUID, datetime | None]
    period_plays: dict[UUID, int]


type PlaySortBy = Literal[
    "total_plays_desc",
    "last_played_desc",
    "title_asc",
    "random",
    "played_at_desc",
    "first_played_asc",
]


class PlaysRepositoryProtocol(Protocol):
    """Repository interface for play history operations."""

    def bulk_insert_plays(self, plays: list[TrackPlay]) -> Awaitable[tuple[int, int]]:
        """Bulk insert plays.

        Returns:
            tuple[int, int]: (inserted_count, duplicate_count)
        """
        ...

    def get_recent_plays(
        self, *, user_id: str, limit: int = 100, sort_by: PlaySortBy | None = None
    ) -> Awaitable[list[TrackPlay]]:
        """Get recent plays.

        Args:
            user_id: Owner's user ID.
            limit: Maximum number of plays to return
            sort_by: Optional sorting method
        """
        ...

    def get_play_aggregations(
        self,
        track_ids: list[UUID],
        metrics: list[str],
        *,
        user_id: str,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> Awaitable[PlayAggregationResult]:
        """Get aggregated play data for specified tracks and metrics.

        Args:
            track_ids: List of track IDs to get play data for
            metrics: List of metrics to calculate ["total_plays", "last_played_dates", "period_plays"]
            user_id: Owner's user ID.
            period_start: Start date for period-based metrics (optional)
            period_end: End date for period-based metrics (optional)

        Returns:
            Typed dictionary mapping metric names to {track_id: value} dictionaries.
        """
        ...

    def find_plays_in_time_range(
        self,
        track_ids: list[UUID],
        start: datetime,
        end: datetime,
        *,
        user_id: str,
    ) -> Awaitable[list[TrackPlay]]:
        """Find existing plays for given tracks within a time range.

        Used by cross-source deduplication to find candidate matches.
        """
        ...

    def bulk_update_play_source_services(
        self,
        updates: Sequence[tuple[UUID, Mapping[str, object]]],
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
        **additional_options: object,
    ) -> Awaitable[dict[UUID, RawProviderMatch]]:
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
        self, track_ids: list[UUID], connector: str
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
        self, *, user_id: str, include_templates: bool = True
    ) -> Awaitable[list[Workflow]]:
        """List user's workflows + shared templates."""
        ...

    def get_workflow_by_id(
        self, workflow_id: UUID, *, user_id: str
    ) -> Awaitable[Workflow]:
        """Get workflow by ID. Raises NotFoundError if not found or wrong user."""
        ...

    def save_workflow(self, workflow: Workflow) -> Awaitable[Workflow]:
        """Create or update a workflow."""
        ...

    def delete_workflow(self, workflow_id: UUID, *, user_id: str) -> Awaitable[bool]:
        """Delete workflow by ID, verifying ownership. Returns True if deleted."""
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
        run_id: UUID,
        status: RunStatus,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        duration_ms: int | None = None,
        output_track_count: int | None = None,
        output_playlist_id: UUID | None = None,
        error_message: str | None = None,
    ) -> Awaitable[None]:
        """Update run status and optional completion fields."""
        ...

    def save_node_record(self, node: WorkflowRunNode) -> Awaitable[WorkflowRunNode]:
        """Persist a new node execution record."""
        ...

    def update_node_status(
        self,
        run_id: UUID,
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
        self, workflow_id: UUID, limit: int = 20, offset: int = 0
    ) -> Awaitable[tuple[list[WorkflowRun], int]]:
        """List runs for a workflow (without nodes loaded) with total count."""
        ...

    def get_run_by_id(self, run_id: UUID) -> Awaitable[WorkflowRun]:
        """Get a single run with all node records loaded."""
        ...

    def get_latest_run_for_workflow(
        self, workflow_id: UUID
    ) -> Awaitable[WorkflowRun | None]:
        """Get the most recent run for a workflow, or None."""
        ...

    def get_latest_runs_for_workflows(
        self, workflow_ids: list[UUID]
    ) -> Awaitable[dict[UUID, WorkflowRun]]:
        """Batch-fetch the latest run for each workflow ID."""
        ...


class WorkflowVersionRepositoryProtocol(Protocol):
    """Repository interface for workflow version history."""

    def create_version(self, version: WorkflowVersion) -> Awaitable[WorkflowVersion]:
        """Persist a new version snapshot."""
        ...

    def list_versions(self, workflow_id: UUID) -> Awaitable[list[WorkflowVersion]]:
        """List all versions for a workflow, ordered by version desc."""
        ...

    def get_version(
        self, workflow_id: UUID, version: int
    ) -> Awaitable[WorkflowVersion]:
        """Get a specific version. Raises NotFoundError if not found."""
        ...

    def get_max_version_number(self, workflow_id: UUID) -> Awaitable[int]:
        """Return the highest version number for a workflow, or 0 if none exist."""
        ...

    def delete_versions_for_workflow(self, workflow_id: UUID) -> Awaitable[None]:
        """Delete all versions for a workflow (cascade cleanup)."""
        ...


class MatchReviewRepositoryProtocol(Protocol):
    """Repository interface for match review queue operations."""

    def list_pending_reviews(
        self,
        *,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "confidence_desc",
    ) -> Awaitable[tuple[list[MatchReview], int]]:
        """List pending reviews with pagination and sorting."""
        ...

    def get_review_by_id(
        self, review_id: UUID, *, user_id: str
    ) -> Awaitable[MatchReview | None]:
        """Get a single review by ID, verifying ownership."""
        ...

    def create_review(self, review: MatchReview) -> Awaitable[MatchReview]:
        """Create a new match review entry."""
        ...

    def create_reviews_batch(self, reviews: list[MatchReview]) -> Awaitable[int]:
        """Create multiple review entries, skipping duplicates."""
        ...

    def update_review_status(
        self, review_id: UUID, status: str
    ) -> Awaitable[MatchReview]:
        """Update a review's status (accept/reject)."""
        ...

    def count_pending(self, *, user_id: str) -> Awaitable[int]:
        """Count pending reviews."""
        ...

    def count_stale_pending(
        self, older_than_days: int, *, user_id: str
    ) -> Awaitable[int]:
        """Count pending reviews older than the given threshold."""
        ...


class DashboardAggregates(TypedDict):
    """Result shape for the single-query dashboard stats aggregation."""

    total_tracks: int
    total_plays: int
    total_playlists: int
    total_liked: int
    tracks_by_connector: dict[str, int]
    liked_by_connector: dict[str, int]
    plays_by_connector: dict[str, int]
    playlists_by_connector: dict[str, int]
    preference_counts: dict[PreferenceState, int]


class PreferenceRepositoryProtocol(Protocol):
    """Repository interface for track preference persistence.

    Batch-first: single-item operations are the degenerate case of batches.
    Callers with one track pass a one-element sequence.
    """

    def get_preferences(
        self, track_ids: Sequence[UUID], *, user_id: str
    ) -> Awaitable[dict[UUID, TrackPreference]]:
        """Get preferences for a set of tracks. Returns {track_id: preference}."""
        ...

    def set_preferences(
        self, preferences: Sequence[TrackPreference], *, user_id: str
    ) -> Awaitable[list[TrackPreference]]:
        """Upsert preferences. UNIQUE on (user_id, track_id)."""
        ...

    def remove_preferences(
        self,
        track_ids: Sequence[UUID],
        *,
        user_id: str,
        source: MetadataSource | None = None,
    ) -> Awaitable[int]:
        """Remove preferences for a set of tracks. Returns the count removed.

        When ``source`` is provided, only preferences matching that source
        are removed — used by the playlist-metadata-mapping flow to clear
        only its own contributions without touching manual preferences.
        """
        ...

    def add_events(
        self, events: Sequence[PreferenceEvent], *, user_id: str
    ) -> Awaitable[list[PreferenceEvent]]:
        """Append preference change events. Events are never updated."""
        ...

    def list_by_state(
        self,
        state: PreferenceState,
        *,
        user_id: str,
        limit: int = 50,
    ) -> Awaitable[list[TrackPreference]]:
        """List preferences filtered by state, ordered by preferred_at desc."""
        ...

    def count_by_state(self, *, user_id: str) -> Awaitable[dict[PreferenceState, int]]:
        """Count preferences grouped by state."""
        ...

    def list_by_preferred_at(
        self,
        *,
        user_id: str,
        before: datetime | None = None,
        after: datetime | None = None,
        limit: int = 50,
    ) -> Awaitable[list[TrackPreference]]:
        """List preferences within a date range, ordered by preferred_at desc."""
        ...


class TagRepositoryProtocol(Protocol):
    """Repository interface for track tag persistence.

    Batch-first: single-item operations pass a one-element sequence. The
    UNIQUE key is three-part ``(user_id, track_id, tag)`` (unlike
    preferences' two-part key), because a track can carry many tags.
    ``add_tags`` uses ON CONFLICT DO NOTHING at the DB layer and returns
    only the rows actually inserted, so callers can build event rows for
    real changes only.
    """

    def get_tags(
        self, track_ids: Sequence[UUID], *, user_id: str
    ) -> Awaitable[dict[UUID, list[TrackTag]]]:
        """Get tags for a set of tracks. Returns {track_id: [tags]}."""
        ...

    def add_tags(
        self, tags: Sequence[TrackTag], *, user_id: str
    ) -> Awaitable[list[TrackTag]]:
        """Bulk insert tags with ON CONFLICT DO NOTHING.

        Returns only the tags actually inserted — duplicates are silently
        skipped. Callers should write one ``TagEvent`` per returned row.
        """
        ...

    def remove_tags(
        self,
        pairs: Sequence[tuple[UUID, str]],
        *,
        user_id: str,
        source: MetadataSource | None = None,
    ) -> Awaitable[list[tuple[UUID, str]]]:
        """Remove (track_id, tag) pairs. Returns the pairs actually removed.

        Missing rows are silently skipped (idempotent). Callers should
        write one ``TagEvent`` per returned pair.

        When ``source`` is provided, only tags matching that source are
        removed — used by the playlist-metadata-mapping flow to clear
        only its own contributions without touching manual tags.
        """
        ...

    def add_events(
        self, events: Sequence[TagEvent], *, user_id: str
    ) -> Awaitable[list[TagEvent]]:
        """Append tag add/remove events. Events are never updated."""
        ...

    def list_tags(
        self,
        *,
        user_id: str,
        query: str | None = None,
        limit: int = 100,
    ) -> Awaitable[list[tuple[str, int]]]:
        """List tags with track counts, sorted by count desc.

        When ``query`` is set, results are filtered via the trigram index
        (GIN on ``tag``) for autocomplete. Returns ``[(tag, count)]``.
        Track-side filtering by tag (for the Library page) flows through
        ``TrackRepositoryProtocol.list_tracks`` so pagination, sort, and
        hydration happen in one query.
        """
        ...

    def count_by_tag(self, *, user_id: str) -> Awaitable[dict[str, int]]:
        """Count tag usage across all of a user's tracks."""
        ...

    def list_by_tagged_at(
        self,
        *,
        user_id: str,
        before: datetime | None = None,
        after: datetime | None = None,
        limit: int = 50,
    ) -> Awaitable[list[TrackTag]]:
        """List tags within a date range, ordered by tagged_at desc."""
        ...


class PlaylistAssignmentRepositoryProtocol(Protocol):
    """Repository interface for playlist assignment persistence.

    Batch-first: single-item operations are the degenerate case. Assignments
    are created once and deleted individually; membership snapshots are
    replaced wholesale (DELETE-by-assignment + INSERT) on every apply.
    """

    def list_for_user(self, *, user_id: str) -> Awaitable[list[PlaylistAssignment]]:
        """All assignments for a user, across every connector playlist."""
        ...

    def list_for_ids(
        self, assignment_ids: Sequence[UUID], *, user_id: str
    ) -> Awaitable[list[PlaylistAssignment]]:
        """Fetch the given subset of assignments in one round-trip."""
        ...

    def list_for_connector_playlist(
        self, connector_playlist_id: UUID, *, user_id: str
    ) -> Awaitable[list[PlaylistAssignment]]:
        """All assignments bound to one connector playlist (may have many)."""
        ...

    def list_for_connector_playlist_ids(
        self, connector_playlist_ids: Sequence[UUID], *, user_id: str
    ) -> Awaitable[dict[UUID, list[PlaylistAssignment]]]:
        """Batch-fetch assignments for many connector playlists in one query.

        Returns ``{connector_playlist_id: [assignments]}`` — playlists with no
        assignments are absent from the result. Powers the Spotify picker's
        per-row badge / overflow-menu state without N+1 calls.
        """
        ...

    def create_assignments(
        self, assignments: Sequence[PlaylistAssignment], *, user_id: str
    ) -> Awaitable[list[PlaylistAssignment]]:
        """Insert assignments. UNIQUE on (connector_playlist_id, action_type, action_value)."""
        ...

    def delete_assignment(
        self, assignment_id: UUID, *, user_id: str
    ) -> Awaitable[bool]:
        """Delete one assignment. Returns True if a row was removed."""
        ...

    def get_members_for_assignments(
        self, assignment_ids: Sequence[UUID], *, user_id: str
    ) -> Awaitable[dict[UUID, list[PlaylistAssignmentMember]]]:
        """Batch member load for many assignments in one query."""
        ...

    def replace_members(
        self,
        assignment_id: UUID,
        members: Sequence[PlaylistAssignmentMember],
        *,
        user_id: str,
    ) -> Awaitable[list[PlaylistAssignmentMember]]:
        """DELETE all members for this assignment, INSERT the new set."""
        ...

    def replace_members_for_assignments(
        self,
        snapshots: Mapping[UUID, Sequence[PlaylistAssignmentMember]],
        *,
        user_id: str,
    ) -> Awaitable[int]:
        """Batch member replace for many assignments: one DELETE + one INSERT."""
        ...


class StatsRepositoryProtocol(Protocol):
    """Cross-table read-only aggregation queries."""

    def get_dashboard_aggregates(
        self, *, user_id: str
    ) -> Awaitable[DashboardAggregates]:
        """Compute all dashboard counts in minimal round trips."""
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

    def get_preference_repository(self) -> PreferenceRepositoryProtocol:
        """Get preference repository for track preference operations."""
        ...

    def get_tag_repository(self) -> TagRepositoryProtocol:
        """Get tag repository for track tag operations."""
        ...

    def get_playlist_assignment_repository(
        self,
    ) -> PlaylistAssignmentRepositoryProtocol:
        """Get playlist assignment repository for connector-playlist → metadata bindings."""
        ...

    def get_stats_repository(self) -> StatsRepositoryProtocol:
        """Get cross-table stats repository for dashboard aggregation."""
        ...

    def get_track_merge_service(self) -> TrackMergeServiceProtocol:
        """Get track merge service using this unit of work's transaction."""
        ...


class TrackMergeServiceProtocol(Protocol):
    """Service interface for track merging operations."""

    def merge_tracks(
        self, winner_id: UUID, loser_id: UUID, uow: UnitOfWorkProtocol
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
        self,
        uow: UnitOfWorkProtocol,
        **params: object,
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
    fallback_resolved: int
    redirect_resolved: int
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
        *,
        user_id: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> tuple[list[TrackPlay], ResolutionMetrics]:
        """Resolve connector plays to canonical track plays.

        Args:
            connector_plays: Raw plays from external service.
            uow: Unit of work for database operations.
            user_id: Authenticated user ID for data scoping.
            progress_callback: Optional progress reporting.

        Returns:
            Tuple of (resolved track plays, resolution metrics).
        """
        ...
