"""Refactored Spotify import service using BasePlayImporter template method pattern."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from src.application.use_cases.match_and_identify_tracks import (
    MatchAndIdentifyTracksCommand,
    MatchAndIdentifyTracksUseCase,
)
from src.application.utilities.results import ImportResultData, ResultFactory
from src.config import get_config, get_logger
from src.domain.entities import Artist, OperationResult, Track, TrackPlay
from src.domain.entities.track import TrackList
from src.domain.repositories.interfaces import (
    ConnectorRepositoryProtocol,
    PlaysRepositoryProtocol,
)
from src.infrastructure.connectors.spotify import SpotifyConnector
from src.infrastructure.connectors.spotify_personal_data import (
    SpotifyPlayRecord,
    parse_spotify_personal_data,
)
from src.infrastructure.services.base_play_importer import BasePlayImporter

logger = get_logger(__name__)


def should_include_play(ms_played: int, track_duration_ms: int | None) -> bool:
    """Apply play filtering: 4 minutes OR 50% of track, whichever is shorter.

    Args:
        ms_played: Duration the user actually listened
        track_duration_ms: Total track duration from API, or None if unknown

    Returns:
        True if play should be included, False if it should be filtered out
    """
    # Get configuration with type-safe defaults
    threshold_ms: int = get_config("PLAY_THRESHOLD_MS") or 240000  # 4 minutes fallback
    threshold_percentage: float = get_config("PLAY_THRESHOLD_PERCENTAGE") or 0.5  # 50%

    if track_duration_ms is None:
        # Fallback to time-based threshold if no duration available
        return ms_played >= threshold_ms

    # Calculate 50% threshold
    percentage_threshold = int(track_duration_ms * threshold_percentage)

    # Use whichever is shorter: 4 minutes or 50% of track
    effective_threshold = min(threshold_ms, percentage_threshold)

    return ms_played >= effective_threshold


class SpotifyPlayImporter(BasePlayImporter):
    """Service for importing Spotify personal data exports using template method pattern."""

    def __init__(
        self,
        plays_repository: PlaysRepositoryProtocol,
        connector_repository: ConnectorRepositoryProtocol,
    ) -> None:
        """Initialize Spotify import service with required repositories."""
        super().__init__(plays_repository)
        self.operation_name = "Spotify Import"
        self.spotify_connector = SpotifyConnector()
        self.connector_repository = connector_repository
        self.match_and_identify_use_case = MatchAndIdentifyTracksUseCase()

    # Public interface method - delegate to template method

    async def import_from_file(
        self,
        file_path: Path,
        import_batch_id: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> OperationResult:
        """Import Spotify play data from a JSON export file.

        Args:
            file_path: Path to the Spotify export JSON file
            import_batch_id: Optional batch ID for tracking related imports
            progress_callback: Optional callback for progress updates (current, total, message)

        Returns:
            OperationResult with play processing statistics and affected tracks
        """
        return await self.import_data(
            file_path=file_path,
            import_batch_id=import_batch_id,
            progress_callback=progress_callback,
        )

    # Template method implementations

    async def _fetch_data(
        self,
        progress_callback: Callable[[int, int, str], None] | None = None,
        file_path: Path | None = None,
        **additional_options,
    ) -> list[SpotifyPlayRecord]:
        """Fetch raw play data from Spotify JSON export file."""
        _ = additional_options  # Reserved for future extensibility
        if file_path is None:
            raise ValueError("file_path is required for Spotify import")

        if progress_callback:
            progress_callback(20, 100, "Parsing Spotify export file...")

        try:
            play_records = parse_spotify_personal_data(file_path)
            logger.info(
                "Parsed Spotify export",
                file_path=str(file_path),
                count=len(play_records),
            )
            return play_records
        except Exception as e:
            logger.error(
                "Failed to parse Spotify export file",
                file_path=str(file_path),
                error=str(e),
            )
            # Re-raise so template method can handle error consistently
            raise

    async def _process_data(
        self,
        raw_data: list[SpotifyPlayRecord],
        batch_id: str,
        import_timestamp: datetime,
        progress_callback: Callable[[int, int, str], None] | None = None,
        uow: Any | None = None,
        **additional_options,
    ) -> list[TrackPlay]:
        """Process Spotify play records into canonical TrackPlay objects.

        Corrected approach: Resolve track identities for plays, not convert plays to tracks.
        This method creates canonical plays that reference canonical tracks.
        """
        _ = additional_options  # Reserved for future extensibility

        if progress_callback:
            progress_callback(
                60, 100, f"Processing {len(raw_data)} Spotify play records..."
            )

        # Step 1: Extract unique Spotify track IDs from play records
        if progress_callback:
            progress_callback(65, 100, "Extracting unique Spotify track IDs...")

        unique_spotify_ids = self._extract_unique_spotify_ids(raw_data)

        if not unique_spotify_ids:
            logger.warning("No valid Spotify track IDs found in play records")
            return []

        # Step 2: Resolve Spotify track IDs to canonical tracks (handles relinking automatically)
        if progress_callback:
            progress_callback(
                70, 100, f"Resolving {len(unique_spotify_ids)} track identities..."
            )

        # Use provided UnitOfWork or create one if not provided (for backward compatibility)
        if uow is None:
            from src.infrastructure.persistence.database.db_connection import (
                get_session,
            )
            from src.infrastructure.persistence.repositories.factories import (
                get_unit_of_work,
            )
            
            async with get_session() as session:
                uow = get_unit_of_work(session)
                canonical_tracks_map = (
                    await self._resolve_spotify_ids_to_canonical_tracks(
                        unique_spotify_ids, uow
                    )
                )
        else:
            canonical_tracks_map = await self._resolve_spotify_ids_to_canonical_tracks(
                unique_spotify_ids, uow
            )

        # Step 3: Create canonical play records with filtering
        if progress_callback:
            progress_callback(
                75, 100, "Creating canonical play records with filtering..."
            )

        track_plays = []
        filtering_stats = {
            "raw_plays": len(raw_data),
            "accepted_plays": 0,
            "duration_excluded": 0,
            "incognito_excluded": 0,
        }

        # Track failed imports for detailed reporting
        failed_imports = []

        for record in raw_data:
            # Filter out incognito mode plays - these don't represent user's actual listening history
            if record.incognito_mode:
                filtering_stats["incognito_excluded"] += 1
                logger.debug(
                    "Filtered out incognito mode play",
                    track_uri=record.track_uri,
                    track_name=record.track_name,
                )
                continue

            # Find the canonical track for this play record
            spotify_id = self._extract_spotify_id_from_uri(record.track_uri)
            canonical_track = (
                canonical_tracks_map.get(spotify_id) if spotify_id else None
            )

            if not canonical_track or not canonical_track.id:
                failed_imports.append({
                    "track_uri": record.track_uri,
                    "track_name": record.track_name,
                    "artist_name": record.artist_name,
                    "album_name": record.album_name,
                    "failure_reason": "canonical_track_not_resolved",
                    "timestamp": record.timestamp.isoformat(),
                })
                logger.debug(
                    "Play record skipped - canonical track not resolved",
                    track_uri=record.track_uri,
                    track_name=record.track_name,
                )
                continue

            # Get track duration for play filtering
            track_duration_ms = canonical_track.duration_ms

            # Apply play filtering: 4 minutes OR 50% of track, whichever is shorter
            if not should_include_play(record.ms_played, track_duration_ms):
                filtering_stats["duration_excluded"] += 1
                logger.debug(
                    "Filtered out short play",
                    track_uri=record.track_uri,
                    ms_played=record.ms_played,
                    track_duration_ms=track_duration_ms,
                )
                continue

            # Create canonical TrackPlay object referencing canonical track
            filtering_stats["accepted_plays"] += 1

            # Create enhanced context with play metadata
            context = {
                # Behavioral data from Spotify
                "platform": record.platform,
                "country": record.country,
                "reason_start": record.reason_start,
                "reason_end": record.reason_end,
                "shuffle": record.shuffle,
                "skipped": record.skipped,
                "offline": record.offline,
                "incognito_mode": record.incognito_mode,
                # Original Spotify metadata for reference
                "spotify_track_uri": record.track_uri,
                "track_name": record.track_name,
                "artist_name": record.artist_name,
                "album_name": record.album_name,
                # Resolution tracking
                "resolution_method": "match_and_identify_tracks_use_case",
                "architecture_version": "clean_architecture_phase_3",
            }

            track_play = TrackPlay(
                track_id=canonical_track.id,  # Reference to canonical track
                service="spotify",
                played_at=record.timestamp,
                ms_played=record.ms_played,
                context=context,
                import_timestamp=import_timestamp,
                import_source="spotify_export",
                import_batch_id=batch_id,
            )

            track_plays.append(track_play)

        # Note: Transaction management handled by template method - no commit/rollback here

        # Store stats for result creation
        resolution_stats = {
            "resolved_tracks": len(canonical_tracks_map),
            "total_unique_tracks": len(unique_spotify_ids),
            "total_plays_processed": len(track_plays),
        }

        self._resolution_stats = resolution_stats
        self._filtering_stats = filtering_stats
        self._failed_imports = failed_imports

        # Log comprehensive summary
        total_processed = len(raw_data)
        failed_count = len(failed_imports)

        if failed_count > 0:
            logger.warning(
                f"Unable to process {failed_count} play records from {total_processed} total",
                failed_imports=failed_count,
                failure_rate_percent=round((failed_count / total_processed) * 100, 1),
            )

        logger.info(
            "Spotify play import completed using clean architecture",
            total_play_records=total_processed,
            canonical_tracks_resolved=len(canonical_tracks_map),
            canonical_plays_created=len(track_plays),
            resolution_rate_percent=round(
                (len(canonical_tracks_map) / len(unique_spotify_ids)) * 100, 1
            )
            if unique_spotify_ids
            else 0,
        )

        # Log filtering summary
        total_excluded = (
            filtering_stats["duration_excluded"] + filtering_stats["incognito_excluded"]
        )
        logger.info(
            "Applied play filtering",
            raw_plays=filtering_stats["raw_plays"],
            accepted_plays=filtering_stats["accepted_plays"],
            duration_excluded=filtering_stats["duration_excluded"],
            incognito_excluded=filtering_stats["incognito_excluded"],
            total_excluded=total_excluded,
            acceptance_rate_percent=round(
                (filtering_stats["accepted_plays"] / filtering_stats["raw_plays"])
                * 100,
                1,
            )
            if filtering_stats["raw_plays"] > 0
            else 0,
        )

        return track_plays

    def _extract_unique_spotify_ids(
        self, play_records: list[SpotifyPlayRecord]
    ) -> list[str]:
        """Extract unique Spotify track IDs from play records.

        Parses Spotify URIs from play records and extracts the track IDs,
        deduplicating to avoid processing the same track multiple times.

        Args:
            play_records: Spotify play records containing track URIs

        Returns:
            List of unique Spotify track IDs (22-character identifiers)
        """
        if not play_records:
            return []

        unique_ids = set()

        for record in play_records:
            try:
                spotify_id = self._extract_spotify_id_from_uri(record.track_uri)
                if spotify_id:
                    unique_ids.add(spotify_id)
            except Exception as e:
                logger.warning(
                    f"Failed to extract Spotify ID from URI {record.track_uri}: {e}"
                )
                continue

        unique_ids_list = list(unique_ids)
        logger.debug(
            f"Extracted {len(unique_ids_list)} unique Spotify IDs from {len(play_records)} play records"
        )

        return unique_ids_list

    def _extract_spotify_id_from_uri(self, spotify_uri: str) -> str | None:
        """Extract Spotify track ID from Spotify URI.

        Parses URIs like "spotify:track:3tI6o5tSlbB2trBl5UKJ1z" and extracts
        the track ID portion "3tI6o5tSlbB2trBl5UKJ1z".

        Args:
            spotify_uri: Spotify URI string

        Returns:
            Spotify track ID if valid, None if invalid or missing
        """
        if not spotify_uri:
            return None

        try:
            # Expected format: "spotify:track:3tI6o5tSlbB2trBl5UKJ1z"
            parts = spotify_uri.split(":")
            if len(parts) != 3 or parts[0] != "spotify" or parts[1] != "track":
                logger.debug(f"Invalid Spotify URI format: {spotify_uri}")
                return None

            track_id = parts[2]

            # Validate Spotify track ID format (22 characters, alphanumeric)
            if (
                len(track_id) == 22
                and track_id.replace("_", "a").replace("-", "a").isalnum()
            ):
                return track_id
            else:
                logger.debug(f"Invalid Spotify track ID format: {track_id}")
                return None

        except Exception as e:
            logger.debug(f"Error parsing Spotify URI {spotify_uri}: {e}")
            return None

    async def _resolve_spotify_ids_to_canonical_tracks(
        self, spotify_ids: list[str], uow
    ) -> dict[str, Track]:
        """Resolve Spotify track IDs to canonical tracks using existing architecture.

        This is the DRY approach - reuse all existing infrastructure:
        1. Fetch Spotify data (with relinking detection)
        2. Create domain Track objects
        3. Use MatchAndIdentifyTracksUseCase to find/create canonical tracks
        4. Return mapping of Spotify ID -> canonical Track

        Args:
            spotify_ids: List of Spotify track IDs to resolve
            uow: Unit of work for transaction control

        Returns:
            Dict mapping Spotify track IDs to canonical Track objects with database IDs
        """
        if not spotify_ids:
            return {}

        logger.info(
            f"Resolving {len(spotify_ids)} Spotify track IDs to canonical tracks"
        )

        # Step 1: Fetch Spotify data (connector already handles relinking transparently)
        spotify_tracks_data = await self.spotify_connector.get_tracks_by_ids(
            spotify_ids
        )

        # Step 2: Create domain Track objects for tracks we have data for
        tracks_for_resolution = []
        spotify_id_to_track_map = {}

        for spotify_id in spotify_ids:
            spotify_data = spotify_tracks_data.get(spotify_id)
            if not spotify_data:
                logger.debug(f"No Spotify data found for track ID {spotify_id}")
                continue

            try:
                # Create domain Track object (without database ID)
                track = self._create_track_from_spotify_data(spotify_id, spotify_data)
                tracks_for_resolution.append(track)
                spotify_id_to_track_map[spotify_id] = track
                logger.debug(f"Created domain track for Spotify ID {spotify_id}")
            except Exception as e:
                logger.warning(
                    f"Failed to create track from Spotify data for {spotify_id}: {e}"
                )
                continue

        if not tracks_for_resolution:
            logger.warning("No valid tracks created from Spotify data")
            return {}

        # Step 3: Use existing MatchAndIdentifyTracksUseCase to resolve to canonical tracks
        # This handles all the complex logic: matching, relinking, database persistence
        tracklist = TrackList(tracks=tracks_for_resolution)
        command = MatchAndIdentifyTracksCommand(
            tracklist=tracklist,
            connector="spotify",
            connector_instance=self.spotify_connector,
            max_age_hours=None,
        )

        _identity_result = await self.match_and_identify_use_case.execute(command, uow)

        # Step 4: Build final mapping from Spotify ID to canonical Track (with database ID)
        canonical_tracks_map = {}

        # After identity resolution, we need to get the tracks with their database IDs
        # The MatchAndIdentifyTracksUseCase may have created new tracks or found existing ones

        # Simple approach: Use the repository to look up tracks by Spotify connector IDs
        # This ensures we get the canonical tracks with database IDs
        connections = [("spotify", spotify_id) for spotify_id in spotify_ids]

        try:
            resolved_tracks = (
                await uow.get_connector_repository().find_tracks_by_connectors(
                    connections
                )
            )

            for spotify_id in spotify_ids:
                connection_key = ("spotify", spotify_id)
                if connection_key in resolved_tracks:
                    canonical_track = resolved_tracks[connection_key]
                    if canonical_track and canonical_track.id:
                        canonical_tracks_map[spotify_id] = canonical_track
                        logger.debug(
                            f"Found canonical track {canonical_track.id} for Spotify ID {spotify_id}"
                        )
                    else:
                        logger.debug(
                            f"Track resolution failed for Spotify ID {spotify_id} - no canonical track found"
                        )
                else:
                    logger.debug(f"No mapping found for Spotify ID {spotify_id}")

        except Exception as e:
            logger.error(f"Error looking up resolved tracks: {e}")
            # Fallback: return empty map, which will cause play records to be skipped
            return {}

        logger.info(
            f"Successfully resolved {len(canonical_tracks_map)} out of {len(spotify_ids)} Spotify tracks",
            resolution_rate=f"{len(canonical_tracks_map) / len(spotify_ids) * 100:.1f}%"
            if spotify_ids
            else "0%",
        )

        return canonical_tracks_map

    def _create_track_from_spotify_data(
        self, spotify_id: str, spotify_data: dict
    ) -> Track:
        """Create a Track domain object from Spotify API data.

        Creates a domain Track object for use with MatchAndIdentifyTracksUseCase.
        The use case will handle database persistence and canonical track resolution.

        Args:
            spotify_id: Spotify track identifier
            spotify_data: Raw Spotify API response data

        Returns:
            Track domain object (without database ID - will be resolved by use case)

        Raises:
            ValueError: If required track data is missing or invalid
        """
        # Validate required fields
        title = spotify_data.get("name")
        if not title:
            raise ValueError(f"Missing track title for Spotify ID {spotify_id}")

        artists_data = spotify_data.get("artists", [])
        if not artists_data:
            raise ValueError(f"Missing artists for Spotify ID {spotify_id}")

        # Create Artist objects
        artists = []
        for artist_data in artists_data:
            artist_name = artist_data.get("name")
            if artist_name:
                artists.append(Artist(name=artist_name))

        if not artists:
            raise ValueError(f"No valid artist names found for Spotify ID {spotify_id}")

        # Extract optional fields
        album = spotify_data.get("album", {}).get("name")
        duration_ms = spotify_data.get("duration_ms")
        isrc = spotify_data.get("external_ids", {}).get("isrc")

        # Create Track object with Spotify connector ID
        track = Track(
            title=title,
            artists=artists,
            album=album,
            duration_ms=duration_ms,
            isrc=isrc,
        ).with_connector_track_id("spotify", spotify_id)

        return track

    async def _handle_checkpoints(
        self, raw_data: list[SpotifyPlayRecord], **additional_options
    ) -> None:
        """Handle checkpoint updates for Spotify imports.

        For file imports, checkpoints are not relevant since we process complete files.
        This is a no-op implementation.
        """
        _ = raw_data  # Reserved for future checkpoint tracking
        _ = additional_options  # Reserved for future extensibility
        # No checkpoints needed for file-based imports

    def _create_success_result(
        self,
        raw_data: list[Any],
        track_plays: list[TrackPlay],
        imported_count: int,
        batch_id: str,
    ) -> OperationResult:
        """Override to include Spotify-specific metrics using ResultFactory."""
        _ = track_plays  # Used in resolution results processing below
        # Don't create placeholder tracks for import operations - track details aren't meaningful for play imports
        affected_tracks = []

        # Calculate error count from failed imports
        error_count = (
            len(self._failed_imports) if hasattr(self, "_failed_imports") else 0
        )

        import_data = ImportResultData(
            raw_data_count=len(raw_data),
            imported_count=imported_count,
            batch_id=batch_id,
            error_count=error_count,
            tracks=affected_tracks,  # Use affected tracks instead of track_plays
        )

        result = ResultFactory.create_import_result(
            operation_name=self.operation_name,
            import_data=import_data,
        )

        # Add comprehensive resolution statistics for user feedback
        if hasattr(self, "_resolution_stats"):
            resolution_stats = self._resolution_stats
            total_raw = len(raw_data)
            unresolved_count = (
                resolution_stats["preserved_metadata"]
                + resolution_stats["validation_failed"]
            )

            result.play_metrics.update({
                "resolution_summary": {
                    "total_plays_processed": total_raw,
                    "successfully_resolved": resolution_stats["total_with_track_id"],
                    "unable_to_resolve": unresolved_count,
                    "resolution_rate_percent": round(
                        (resolution_stats["total_with_track_id"] / total_raw) * 100, 1
                    )
                    if total_raw > 0
                    else 0,
                },
                "resolution_breakdown": {
                    "direct_api_lookup": resolution_stats["direct_id"],
                    "relinked_tracks": resolution_stats["relinked_id"],
                    "search_fallback": resolution_stats["search_match"],
                    "metadata_preserved": resolution_stats["preserved_metadata"],
                    "validation_failed": resolution_stats["validation_failed"],
                },
                "resolution_stats": resolution_stats,  # Keep for backwards compatibility
            })

        # Add play filtering metrics
        if hasattr(self, "_filtering_stats"):
            filtering_stats = self._filtering_stats
            result.play_metrics.update({
                "filtering_summary": {
                    "raw_plays": filtering_stats["raw_plays"],
                    "plays_accepted": filtering_stats["accepted_plays"],
                    "plays_filtered": filtering_stats["raw_plays"]
                    - filtering_stats["accepted_plays"],
                    "acceptance_rate_percent": round(
                        (
                            filtering_stats["accepted_plays"]
                            / filtering_stats["raw_plays"]
                        )
                        * 100,
                        1,
                    )
                    if filtering_stats["raw_plays"] > 0
                    else 0,
                },
                "filtering_breakdown": {
                    "duration_too_short": filtering_stats["duration_excluded"],
                    "incognito_mode": filtering_stats["incognito_excluded"],
                },
                "filtering_stats": filtering_stats,  # Keep for backwards compatibility
            })

        # Add failed imports for detailed user reporting
        if hasattr(self, "_failed_imports") and self._failed_imports:
            result.play_metrics.update({
                "failed_imports": self._failed_imports,
                "failed_import_count": len(self._failed_imports),
            })

        return result
