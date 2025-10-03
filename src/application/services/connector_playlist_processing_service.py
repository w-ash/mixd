"""ConnectorPlaylist processing service for converting external playlist data to domain Playlist.

Provides shared functionality for processing ConnectorPlaylist metadata into Playlist
with PlaylistEntry objects (tracks + position metadata), handling bulk operations,
duplicate preservation, and performance optimization across create and update use cases.
"""

from datetime import datetime

from src.config import get_logger, settings
from src.domain.entities.playlist import Playlist, PlaylistEntry
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


class ConnectorPlaylistProcessingService:
    """Service for processing ConnectorPlaylist data into domain Playlist.

    Handles the conversion of ConnectorPlaylist metadata (with external track IDs
    and position-specific data) into a Playlist containing PlaylistEntry objects
    from the database. Optimizes performance by only processing unique tracks
    while preserving duplicate positions and position-specific metadata.

    CLEAN ARCHITECTURE - PlaylistEntry Pattern:
    Creates PlaylistEntry objects that properly encapsulate track + position metadata
    (added_at, added_by). PlaylistEntry enables clean separation: Track = song identity,
    PlaylistEntry = membership.

    This service maintains DRY principles by providing shared functionality
    for both CreateCanonicalPlaylistUseCase and UpdateCanonicalPlaylistUseCase.
    """

    async def process_connector_playlist(
        self, connector_playlist, uow: UnitOfWorkProtocol
    ) -> Playlist:
        """Process ConnectorPlaylist data to create Playlist with entries.

        Extracts ConnectorPlaylist metadata, processes unique tracks for persistence,
        then creates a Playlist with PlaylistEntry objects preserving all playlist
        positions including duplicates with their position-specific metadata.

        Args:
            connector_playlist: ConnectorPlaylist entity with items and metadata
            uow: Database transaction manager and repository access

        Returns:
            Playlist with PlaylistEntry objects (track + position metadata)

        Raises:
            ValueError: If connector_playlist is invalid or missing required data
        """
        if not connector_playlist:
            raise ValueError("ConnectorPlaylist is required for processing")

        playlist_items = connector_playlist.items
        connector_name = connector_playlist.connector_name

        if not playlist_items:
            logger.warning(f"Empty ConnectorPlaylist from {connector_name}")
            return Playlist(
                name=connector_playlist.name,
                entries=[],
                description=connector_playlist.description,
                metadata={
                    "connector_playlist_processed": True,
                    "original_item_count": 0,
                    "preserved_track_count": 0,
                    "unique_tracks": 0,
                },
            )

        logger.info(
            f"Processing ConnectorPlaylist with {len(playlist_items)} items from {connector_name}",
            unique_tracks=len({
                item.connector_track_identifier for item in playlist_items
            }),
            duplicates=len(playlist_items)
            - len({item.connector_track_identifier for item in playlist_items}),
        )

        # Step 1: Use track data directly from playlist items (preserves Spotify order)
        # Instead of making additional API calls that scramble ordering,
        # extract unique track data from playlist items that already contain full track info

        # Get connector instance for track conversion
        connector_provider = uow.get_service_connector_provider()
        connector_instance = connector_provider.get_connector(connector_name)

        # Collect unique tracks while preserving the data we already have
        seen_track_ids = set()
        unique_connector_tracks = []

        # First pass: collect unique track data from playlist items
        for item in playlist_items:
            if item.connector_track_identifier not in seen_track_ids:
                seen_track_ids.add(item.connector_track_identifier)

                # Use track data from extras which contains the full Spotify track data
                if "full_track_data" in item.extras:
                    # Use the complete track data that we stored during playlist fetch
                    track_data = item.extras["full_track_data"]
                else:
                    # Fallback: reconstruct from minimal metadata in extras
                    track_data = {
                        "id": item.connector_track_identifier,
                        "name": item.extras.get("track_name"),
                        "artists": [
                            {"name": name}
                            for name in item.extras.get("artist_names", [])
                        ],
                    }

                # Convert to ConnectorTrack using existing track data
                connector_track = connector_instance.convert_track_to_connector(
                    track_data
                )
                unique_connector_tracks.append(connector_track)

        logger.debug(
            f"Retrieved {len(unique_connector_tracks)} unique tracks from playlist items (no API calls needed)"
        )

        # Step 3: Efficiently persist unique tracks (reusing CreateConnectorPlaylistUseCase pattern)
        connector_repo = uow.get_connector_repository()

        # Bulk lookup existing tracks
        connector_tuples = [
            (connector_name, track.connector_track_identifier)
            for track in unique_connector_tracks
        ]
        existing_tracks_map = await connector_repo.find_tracks_by_connectors(
            connector_tuples
        )

        # Separate existing vs new tracks
        track_id_to_domain_track = {}
        new_connector_tracks = []

        for connector_track in unique_connector_tracks:
            lookup_key = (connector_name, connector_track.connector_track_identifier)
            if lookup_key in existing_tracks_map:
                domain_track = existing_tracks_map[lookup_key]
                track_id_to_domain_track[connector_track.connector_track_identifier] = (
                    domain_track
                )
            else:
                new_connector_tracks.append(connector_track)

        # Ingest only truly new tracks
        if new_connector_tracks:
            logger.info(f"Creating {len(new_connector_tracks)} new tracks in database")
            try:
                newly_created_tracks = await connector_repo.ingest_external_tracks_bulk(
                    connector_name, new_connector_tracks
                )

                # Add to mapping
                for track in newly_created_tracks:
                    connector_track_id = track.connector_track_identifiers.get(
                        connector_name
                    )
                    if connector_track_id:
                        track_id_to_domain_track[connector_track_id] = track
            except Exception as e:
                logger.warning(
                    f"Failed to ingest {len(new_connector_tracks)} tracks, attempting individual retry",
                    error=str(e),
                    connector=connector_name,
                )

                # Retry individual tracks to handle partial conflicts
                for connector_track in new_connector_tracks:
                    try:
                        single_track_result = (
                            await connector_repo.ingest_external_tracks_bulk(
                                connector_name, [connector_track]
                            )
                        )
                        if single_track_result:
                            track = single_track_result[0]
                            connector_track_id = track.connector_track_identifiers.get(
                                connector_name
                            )
                            if connector_track_id:
                                track_id_to_domain_track[connector_track_id] = track
                    except Exception as individual_error:
                        logger.warning(
                            f"Failed to ingest individual track {connector_track.connector_track_identifier}",
                            error=str(individual_error),
                            track_id=connector_track.connector_track_identifier,
                        )
                        # Continue processing other tracks

        # Debug: Check if we have all expected tracks
        expected_track_count = len(unique_connector_tracks)
        actual_track_count = len(track_id_to_domain_track)

        if expected_track_count != actual_track_count:
            missing_track_ids = [
                connector_track.connector_track_identifier
                for connector_track in unique_connector_tracks
                if connector_track.connector_track_identifier
                not in track_id_to_domain_track
            ]

            logger.warning(
                f"Track mapping incomplete: expected {expected_track_count}, got {actual_track_count}. "
                f"Missing from domain mapping: {missing_track_ids[: settings.batch.truncation_limit]}{'...' if len(missing_track_ids) > settings.batch.truncation_limit else ''}"
            )

        logger.info(
            f"Track processing complete: {len(track_id_to_domain_track)} unique tracks available",
            existing=len(existing_tracks_map),
            created=len(new_connector_tracks),
        )

        # Step 4: Create PlaylistEntry for each position, preserving duplicates and metadata
        # CLEAN ARCHITECTURE: PlaylistEntry properly models "track membership in playlist"
        # by combining track identity with position-specific metadata (added_at, added_by).
        playlist_entries = []
        missing_tracks = []

        for position, playlist_item in enumerate(playlist_items):
            if playlist_item.connector_track_identifier in track_id_to_domain_track:
                domain_track = track_id_to_domain_track[
                    playlist_item.connector_track_identifier
                ]

                # Parse added_at timestamp (ISO format from Spotify)
                added_at = None
                if playlist_item.added_at:
                    try:
                        added_at = datetime.fromisoformat(playlist_item.added_at)
                    except (ValueError, TypeError) as e:
                        logger.warning(
                            f"Invalid added_at timestamp for position {position}: {playlist_item.added_at}",
                            error=str(e),
                        )

                # Create PlaylistEntry with track + position metadata
                entry = PlaylistEntry(
                    track=domain_track,
                    added_at=added_at,
                    added_by=playlist_item.added_by_id,
                )
                playlist_entries.append(entry)
            else:
                missing_tracks.append((
                    position,
                    playlist_item.connector_track_identifier,
                ))
                # Better error message - track was missing from our domain mapping, not API
                logger.warning(
                    f"Position {position}: Track {playlist_item.connector_track_identifier} missing from domain track mapping "
                    f"(was in API response: {playlist_item.connector_track_identifier in [t.connector_track_identifier for t in unique_connector_tracks]})"
                )

        if missing_tracks:
            logger.warning(
                f"Skipped {len(missing_tracks)} playlist positions due to missing track data",
                missing_count=len(missing_tracks),
                total_positions=len(playlist_items),
            )

        logger.info(
            f"Created playlist structure: {len(playlist_entries)} entries preserved",
            original_count=len(playlist_items),
            preserved_count=len(playlist_entries),
            duplicates_preserved=len(playlist_entries) - len(track_id_to_domain_track),
        )

        # Return Playlist with PlaylistEntry objects (track + position metadata)
        return Playlist(
            name=connector_playlist.name,
            entries=playlist_entries,
            description=connector_playlist.description,
            connector_playlist_identifiers={
                connector_name: connector_playlist.connector_playlist_identifier
            },
            metadata={
                "connector_playlist_processed": True,
                "original_item_count": len(playlist_items),
                "preserved_entry_count": len(playlist_entries),
                "unique_tracks": len(track_id_to_domain_track),
            },
        )
