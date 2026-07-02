"""Connector track/playlist repository + service-provider protocols.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable, Mapping, Sequence
from typing import Protocol, TypedDict, overload
from uuid import UUID

from src.domain.entities import (
    ConnectorPlaylist,
    ConnectorTrack,
    Track,
    TrackMapping,
)
from src.domain.entities.shared import JsonDict, JsonValue


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
