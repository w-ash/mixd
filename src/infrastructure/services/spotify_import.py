"""Refactored Spotify import service using BaseImportService template method pattern."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from src.application.utilities.results import ImportResultData, ResultFactory
from src.config import get_config, get_logger
from src.domain.entities import OperationResult, TrackPlay
from src.domain.repositories.interfaces import (
    ConnectorRepositoryProtocol,
    PlaysRepositoryProtocol,
)
from src.infrastructure.connectors.spotify import SpotifyConnector
from src.infrastructure.connectors.spotify_personal_data import (
    SpotifyPlayRecord,
    parse_spotify_personal_data,
)
from src.infrastructure.services.base_import import BaseImportService
from src.infrastructure.services.spotify_play_resolver import SpotifyPlayResolver

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


class SpotifyImportService(BaseImportService):
    """Service for importing Spotify personal data exports using template method pattern."""

    def __init__(
        self,
        plays_repository: PlaysRepositoryProtocol,
        connector_repository: ConnectorRepositoryProtocol,
    ) -> None:
        """Initialize with repository access following Clean Architecture."""
        super().__init__(plays_repository)
        self.operation_name = "Spotify Import"
        self.spotify_connector = SpotifyConnector()
        self.resolver = SpotifyPlayResolver(
            spotify_connector=self.spotify_connector,
            connector_repository=connector_repository,
        )

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
        **additional_options,
    ) -> list[TrackPlay]:
        """Process Spotify play records into TrackPlay objects with track resolution."""
        _ = additional_options  # Reserved for future extensibility
        if progress_callback:
            progress_callback(60, 100, f"Resolving {len(raw_data)} track URIs...")

        # Use enhanced resolver for comprehensive track resolution
        resolution_results = await self.resolver.resolve_with_fallback(raw_data)

        if progress_callback:
            progress_callback(75, 100, "Creating play records with filtering...")

        track_plays = []
        resolution_stats = {
            "direct_id": 0,
            "relinked_id": 0,
            "search_match": 0,
            "preserved_metadata": 0,
            "validation_failed": 0,
            "total_with_track_id": 0,
        }

        # Play filtering metrics
        filtering_stats = {
            "raw_plays": len(raw_data),
            "accepted_plays": 0,  # Plays that passed all filters and will be imported
            "duration_excluded": 0,  # Plays excluded for being too short
            "incognito_excluded": 0,  # Plays excluded for being in incognito mode
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
                continue  # Skip this play record

            resolution = resolution_results.get(record.track_uri)

            if resolution:
                track_id = resolution.track_id
                resolution_method = resolution.resolution_method
                confidence = resolution.confidence

                # Get track duration for play filtering
                track_duration_ms = None
                if resolution.metadata and "spotify_data" in resolution.metadata:
                    track_duration_ms = resolution.metadata["spotify_data"].get(
                        "duration_ms"
                    )

                # Apply play filtering: 4 minutes OR 50% of track, whichever is shorter
                if not should_include_play(record.ms_played, track_duration_ms):
                    filtering_stats["duration_excluded"] += 1
                    logger.debug(
                        "Filtered out short play",
                        track_uri=record.track_uri,
                        ms_played=record.ms_played,
                        track_duration_ms=track_duration_ms,
                    )
                    continue  # Skip this play record

                # Update statistics
                resolution_stats[resolution_method] += 1
                if track_id is not None:
                    resolution_stats["total_with_track_id"] += 1

                # Handle failed track resolutions
                if resolution_method == "validation_failed":
                    failed_imports.append({
                        "track_uri": record.track_uri,
                        "track_name": record.track_name,
                        "artist_name": record.artist_name,
                        "album_name": record.album_name,
                        "failure_reason": resolution.failure_reason,
                        "timestamp": record.timestamp.isoformat(),
                    })
                    logger.warning(
                        "Track resolution failed during import",
                        track_uri=record.track_uri,
                        track_name=record.track_name,
                        failure_reason=resolution.failure_reason,
                    )
                    # Still count as processed, but not as successful play
                    continue  # Skip creating TrackPlay for failed resolutions

                filtering_stats["accepted_plays"] += 1

                # Create enhanced context with resolution info
                context = {
                    # Behavioral data
                    "platform": record.platform,
                    "country": record.country,
                    "reason_start": record.reason_start,
                    "reason_end": record.reason_end,
                    "shuffle": record.shuffle,
                    "skipped": record.skipped,
                    "offline": record.offline,
                    "incognito_mode": record.incognito_mode,
                    # Original track metadata
                    "spotify_track_uri": record.track_uri,
                    "track_name": record.track_name,
                    "artist_name": record.artist_name,
                    "album_name": record.album_name,
                    # Resolution tracking
                    "resolution_method": resolution_method,
                    "resolution_confidence": confidence,
                }

                # Add resolution-specific metadata
                if resolution.metadata:
                    context["resolution_metadata"] = resolution.metadata

                track_play = TrackPlay(
                    track_id=track_id,
                    service="spotify",
                    played_at=record.timestamp,
                    ms_played=record.ms_played,
                    context=context,
                    import_timestamp=import_timestamp,
                    import_source="spotify_export",
                    import_batch_id=batch_id,
                )

                track_plays.append(track_play)

                if track_id is None:
                    logger.debug(
                        "Created play record without track ID",
                        uri=record.track_uri,
                        track=record.track_name,
                        method=resolution_method,
                    )
            else:
                # This should never happen with comprehensive resolver, but handle gracefully
                logger.warning(f"No resolution result for {record.track_uri}")
                resolution_stats["preserved_metadata"] += 1

        # Store resolution and filtering stats for result creation
        self._resolution_stats = resolution_stats
        self._resolution_results = resolution_results
        self._filtering_stats = filtering_stats
        self._failed_imports = failed_imports

        # Log resolution summary with warnings for unresolved tracks
        unresolved_count = (
            resolution_stats["preserved_metadata"]
            + resolution_stats["validation_failed"]
        )
        total_processed = len(raw_data)

        if unresolved_count > 0:
            logger.warning(
                f"Unable to resolve {unresolved_count} tracks from {total_processed} total plays",
                preserved_metadata=resolution_stats["preserved_metadata"],
                validation_failed=resolution_stats["validation_failed"],
                unresolved_rate_percent=round(
                    (unresolved_count / total_processed) * 100, 1
                ),
            )

        logger.info(
            "Track resolution completed",
            total_tracks=total_processed,
            direct_id=resolution_stats["direct_id"],
            relinked_id=resolution_stats["relinked_id"],
            search_match=resolution_stats["search_match"],
            resolved_tracks=resolution_stats["total_with_track_id"],
            unresolved_tracks=unresolved_count,
            resolution_rate_percent=round(
                (resolution_stats["total_with_track_id"] / total_processed) * 100, 1
            )
            if total_processed > 0
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
