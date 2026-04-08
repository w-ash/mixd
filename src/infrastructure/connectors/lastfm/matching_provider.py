"""LastFM provider for track matching.

This provider handles communication with the LastFM API and transforms
LastFM track data into raw provider matches without business logic.

Satisfies the ``MatchProvider`` protocol structurally (service_name +
fetch_raw_matches_for_tracks). Does NOT inherit ``BaseMatchingProvider``
because Last.fm's batch API doesn't partition by ISRC / artist-title —
inheriting would violate LSP (the abstract template-method hooks would
raise ``NotImplementedError``).
"""

# pyright: reportAny=false

from typing import Any
from uuid import UUID

from src.config import get_logger
from src.config.logging import logging_context
from src.domain.entities import Track
from src.domain.entities.shared import JsonValue
from src.domain.matching.types import (
    MatchFailure,
    MatchFailureReason,
    ProgressCallback,
    ProviderMatchResult,
    RawProviderMatch,
)
from src.infrastructure.connectors._shared.failure_handling import (
    create_and_log_failure,
    handle_track_processing_failure,
    log_failure_summary,
)
from src.infrastructure.connectors.lastfm.connector import LastFMConnector
from src.infrastructure.connectors.lastfm.conversions import LastFMTrackInfo

logger = get_logger(__name__)


class LastFMProvider:
    """LastFM track matching provider.

    Satisfies ``MatchProvider`` protocol structurally. Uses Last.fm's batch
    API which processes all tracks at once, so there is no ISRC / artist-title
    partitioning step.
    """

    connector_instance: LastFMConnector

    def __init__(self, connector_instance: LastFMConnector) -> None:
        """Initialize with LastFM connector.

        Args:
            connector_instance: LastFM service connector for API calls.
        """
        self.connector_instance = connector_instance

    @property
    def service_name(self) -> str:
        """Service identifier."""
        return "lastfm"

    async def fetch_raw_matches_for_tracks(
        self,
        tracks: list[Track],
        progress_callback: ProgressCallback | None = None,
        **additional_options: Any,
    ) -> ProviderMatchResult:
        """Fetch raw track matches from LastFM.

        Args:
            tracks: Tracks to match against LastFM catalog.
            progress_callback: Optional async callback invoked with
                (completed_count, total, description) after matching completes.
            **additional_options: Additional options (unused).

        Returns:
            ProviderMatchResult with successful matches and structured failure information.
        """
        # Acknowledge additional options to satisfy linter
        _ = additional_options

        if not tracks:
            return ProviderMatchResult()

        with logging_context(operation="match_lastfm", tracks_count=len(tracks)):
            logger.info(f"Matching {len(tracks)} tracks to LastFM")

            matches: dict[UUID, RawProviderMatch] = {}
            failures: list[MatchFailure] = []

            try:
                # Get batch track info from LastFM
                logger.info(f"Fetching LastFM metadata for {len(tracks)} tracks")

                track_infos = await self.connector_instance.get_track_info_batch(
                    tracks=tracks
                )
                logger.info(
                    f"LastFM API completed: retrieved {len(track_infos)} track metadata results"
                )

                # Process results and classify failures
                processed_track_ids: set[UUID] = set()
                for track_id, track_info in track_infos.items():
                    processed_track_ids.add(track_id)

                    if track_info and track_info.lastfm_url:
                        raw_match = self._create_raw_match(track_info)
                        if raw_match:
                            matches[track_id] = raw_match
                        else:
                            failures.append(
                                create_and_log_failure(
                                    track_id,
                                    MatchFailureReason.INVALID_RESPONSE,
                                    self.service_name,
                                    "batch_lookup",
                                    "Failed to create raw match from LastFM response",
                                )
                            )
                    else:
                        failures.append(
                            create_and_log_failure(
                                track_id,
                                MatchFailureReason.NO_RESULTS,
                                self.service_name,
                                "batch_lookup",
                                "No LastFM data available for track",
                            )
                        )

                # Handle tracks that weren't returned by LastFM API
                failures.extend(
                    create_and_log_failure(
                        track.id,
                        MatchFailureReason.NO_RESULTS,
                        self.service_name,
                        "batch_lookup",
                        "Track not found in LastFM batch response",
                    )
                    for track in tracks
                    if track.id and track.id not in processed_track_ids
                )

            except Exception as e:
                # Batch API failed - all tracks failed
                failures.extend(
                    handle_track_processing_failure(
                        track.id, self.service_name, "batch_lookup", e
                    )
                    for track in tracks
                    if track.id
                )

            # Report progress after batch processing
            if progress_callback is not None:
                await progress_callback(
                    len(tracks),
                    len(tracks),
                    f"LastFM batch matching complete ({len(matches)} matched)",
                )

            # Log summary
            log_failure_summary(self.service_name, len(matches), len(failures))
            logger.info(f"Found {len(matches)} matches from {len(tracks)} tracks")

            return ProviderMatchResult(matches=matches, failures=failures)

    def _create_raw_match(self, track_info: LastFMTrackInfo) -> RawProviderMatch | None:
        """Create raw match data from LastFM track data.

        This method extracts and formats data from LastFM API without applying
        any business logic, confidence scoring, or match decisions.

        Args:
            track_info: Typed LastFMTrackInfo from Last.fm operations.

        Returns:
            Raw provider match data, or None if creation fails.
        """
        try:
            # Extract service data without any business logic
            service_data: dict[str, JsonValue] = {
                "title": track_info.lastfm_title,
                "artist": track_info.lastfm_artist_name,
                "artists": [track_info.lastfm_artist_name]
                if track_info.lastfm_artist_name
                else [],
                "duration_ms": track_info.lastfm_duration,
                # LastFM specific data
                "lastfm_user_playcount": track_info.lastfm_user_playcount,
                "lastfm_global_playcount": track_info.lastfm_global_playcount,
                "lastfm_listeners": track_info.lastfm_listeners,
                "lastfm_user_loved": track_info.lastfm_user_loved,
            }

            # Determine match method based on available data
            # Note: This is data classification, not business logic
            match_method = "mbid" if track_info.lastfm_mbid else "artist_title"

            # Return raw data - no confidence calculation or business logic
            if not track_info.lastfm_url:
                return None

            return RawProviderMatch(
                connector_id=track_info.lastfm_url,
                match_method=match_method,
                service_data=service_data,
            )

        except Exception as e:
            logger.warning(f"Failed to create LastFM raw match: {e}")
            return None
