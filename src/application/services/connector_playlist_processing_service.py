"""ConnectorPlaylist processing service for converting external playlist data to domain Playlist.

Provides shared functionality for processing ConnectorPlaylist metadata into Playlist
with PlaylistEntry objects (tracks + position metadata), handling bulk operations,
duplicate preservation, and performance optimization across create and update use cases.
"""

from datetime import datetime

from src.application.connector_protocols import TrackConversionConnector
from src.config import get_logger
from src.domain.entities.playlist import (
    ConnectorPlaylist,
    ConnectorPlaylistItem,
    ConnectorTrackRef,
    Playlist,
    PlaylistEntry,
)
from src.domain.entities.shared import JsonValue
from src.domain.entities.track import ConnectorTrack, Track
from src.domain.repositories.connector import ConnectorRepositoryProtocol
from src.domain.repositories.uow import UnitOfWorkProtocol

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
        self,
        connector_playlist: ConnectorPlaylist | None,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
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
            raise ValueError("connector_playlist cannot be None")
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

        # Get typed connector instance for track conversion
        from src.application.use_cases._shared.connector_resolver import (
            resolve_track_conversion_connector,
        )

        connector_instance = resolve_track_conversion_connector(connector_name, uow)

        unique_connector_tracks, connector_track_by_id = (
            self._extract_unique_connector_tracks(playlist_items, connector_instance)
        )

        track_id_to_domain_track = await self._resolve_and_ingest_tracks(
            unique_connector_tracks, connector_name, uow, user_id=user_id
        )

        playlist_entries, unresolved_count = self._build_playlist_entries(
            playlist_items,
            connector_name,
            track_id_to_domain_track,
            connector_track_by_id,
        )

        logger.info(
            "Created playlist structure: %d positions (%d resolved, %d unresolved)",
            len(playlist_entries),
            len(playlist_entries) - unresolved_count,
            unresolved_count,
            original_count=len(playlist_items),
            unresolved=unresolved_count,
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
                "unresolved_count": unresolved_count,
                "unique_tracks": len(track_id_to_domain_track),
            },
        )

    @staticmethod
    def _extract_unique_connector_tracks(
        playlist_items: list[ConnectorPlaylistItem],
        connector_instance: TrackConversionConnector,
    ) -> tuple[list[ConnectorTrack], dict[str, ConnectorTrack]]:
        """Collect one ConnectorTrack per unique source track, preserving order.

        Uses the full track data already carried in each item's extras — no extra
        API calls, so Spotify order is preserved — falling back to minimal
        metadata. ``connector_track_by_id`` keeps each source track's display data
        so an unmatched position can still be rendered ("Couldn't match: …").
        """
        seen_track_ids: set[str] = set()
        unique_connector_tracks: list[ConnectorTrack] = []
        connector_track_by_id: dict[str, ConnectorTrack] = {}

        for item in playlist_items:
            if item.connector_track_identifier not in seen_track_ids:
                seen_track_ids.add(item.connector_track_identifier)

                # Use track data from extras which contains the full Spotify track data
                full_data = item.extras.get("full_track_data")
                if isinstance(full_data, dict):
                    # Use the complete track data that we stored during playlist fetch
                    track_data = full_data
                else:
                    # Fallback: reconstruct from minimal metadata in extras
                    artist_names = item.extras.get("artist_names", [])
                    track_data: dict[str, JsonValue] = {
                        "id": item.connector_track_identifier,
                        "name": item.extras.get("track_name"),
                        "artists": [
                            {"name": name}
                            for name in (
                                artist_names if isinstance(artist_names, list) else []
                            )
                        ],
                    }

                # Convert to ConnectorTrack using existing track data
                connector_track = connector_instance.convert_track_to_connector(
                    track_data
                )
                unique_connector_tracks.append(connector_track)
                connector_track_by_id[connector_track.connector_track_identifier] = (
                    connector_track
                )

        logger.debug(
            f"Retrieved {len(unique_connector_tracks)} unique tracks from playlist items (no API calls needed)"
        )

        return unique_connector_tracks, connector_track_by_id

    async def _resolve_and_ingest_tracks(
        self,
        unique_connector_tracks: list[ConnectorTrack],
        connector_name: str,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> dict[str, Track]:
        """Resolve unique tracks to domain tracks, ingesting any that are new.

        Bulk-looks up existing tracks, then ingests only the truly-new ones
        (falling back to per-track retry on bulk failure, so one bad row doesn't
        drop the batch). Returns the connector-track-id → domain ``Track`` map.
        """
        connector_repo = uow.get_connector_repository()

        # Bulk lookup existing tracks
        connector_tuples: list[tuple[str, str]] = [
            (connector_name, track.connector_track_identifier)
            for track in unique_connector_tracks
        ]

        existing_tracks_map = await connector_repo.find_tracks_by_connectors(
            connector_tuples, user_id=user_id
        )

        # Separate existing vs new tracks
        track_id_to_domain_track: dict[str, Track] = {}
        new_connector_tracks: list[ConnectorTrack] = []

        for connector_track in unique_connector_tracks:
            lookup_key = (connector_name, connector_track.connector_track_identifier)
            domain_track = existing_tracks_map.get(lookup_key)
            if domain_track:
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
                    connector_name, new_connector_tracks, user_id=user_id
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
                        await self._ingest_single_track(
                            connector_repo,
                            connector_name,
                            connector_track,
                            track_id_to_domain_track,
                            user_id=user_id,
                        )
                    except Exception as individual_error:
                        logger.warning(
                            f"Failed to ingest individual track {connector_track.connector_track_identifier}",
                            error=str(individual_error),
                            track_id=connector_track.connector_track_identifier,
                        )
                        # Continue processing other tracks

        logger.info(
            f"Track processing complete: {len(track_id_to_domain_track)} unique tracks resolved",
            existing=len(existing_tracks_map),
            created=len(new_connector_tracks),
        )

        return track_id_to_domain_track

    def _build_playlist_entries(
        self,
        playlist_items: list[ConnectorPlaylistItem],
        connector_name: str,
        track_id_to_domain_track: dict[str, Track],
        connector_track_by_id: dict[str, ConnectorTrack],
    ) -> tuple[list[PlaylistEntry], int]:
        """Build one PlaylistEntry per source position — resolved or unresolved.

        A position is never skipped: resolved when its track ingested, UNRESOLVED
        otherwise (ingest failures, local/unavailable tracks) with its display data
        preserved for the UI and later re-resolution. Returns the entries plus the
        unresolved count.
        """
        playlist_entries: list[PlaylistEntry] = []
        unresolved_count = 0

        for position, playlist_item in enumerate(playlist_items):
            cid = playlist_item.connector_track_identifier
            added_at = self._parse_added_at(playlist_item.added_at, position)
            domain_track = track_id_to_domain_track.get(cid)
            if domain_track is not None:
                playlist_entries.append(
                    PlaylistEntry(
                        track=domain_track,
                        added_at=added_at,
                        added_by=playlist_item.added_by_id,
                    )
                )
            else:
                unresolved_count += 1
                playlist_entries.append(
                    PlaylistEntry(
                        track=None,
                        added_at=added_at,
                        added_by=playlist_item.added_by_id,
                        connector_track_ref=self._connector_ref(
                            connector_name, cid, connector_track_by_id.get(cid)
                        ),
                    )
                )

        return playlist_entries, unresolved_count

    @staticmethod
    def _parse_added_at(raw: str | None, position: int) -> datetime | None:
        """Parse an ISO ``added_at`` timestamp, tolerating malformed values."""
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except (ValueError, TypeError) as e:
            logger.warning(
                f"Invalid added_at timestamp for position {position}: {raw}",
                error=str(e),
            )
            return None

    @staticmethod
    def _connector_ref(
        connector_name: str, identifier: str, source: ConnectorTrack | None
    ) -> ConnectorTrackRef:
        """Build the display/re-resolution ref for an unresolved position."""
        return ConnectorTrackRef(
            connector_name=connector_name,
            connector_track_identifier=identifier,
            title=source.title if source is not None else None,
            artists=tuple(a.name for a in source.artists) if source is not None else (),
        )

    async def _ingest_single_track(
        self,
        connector_repo: ConnectorRepositoryProtocol,
        connector_name: str,
        connector_track: ConnectorTrack,
        track_id_to_domain_track: dict[str, Track],
        *,
        user_id: str,
    ) -> None:
        """Ingest one connector track and record it in the domain-track mapping.

        Extracted from the individual-retry loop so the protective ``try`` clause
        stays small; the same statements remain guarded by the caller's broad
        ``except``.
        """
        single_track_result = await connector_repo.ingest_external_tracks_bulk(
            connector_name, [connector_track], user_id=user_id
        )
        if single_track_result:
            track = single_track_result[0]
            connector_track_id = track.connector_track_identifiers.get(connector_name)
            if connector_track_id:
                track_id_to_domain_track[connector_track_id] = track
