"""LastFM provider for track matching.

This provider handles communication with the LastFM API and transforms
LastFM track data into raw provider matches without business logic.
"""

from typing import Any, override

from src.config import get_logger
from src.domain.entities import Track
from src.domain.matching.types import (
    MatchFailure,
    MatchFailureReason,
    ProviderMatchResult,
    RawProviderMatch,
)
from src.infrastructure.connectors._shared.failure_handling import (
    create_and_log_failure,
    handle_track_processing_failure,
    log_failure_summary,
)
from src.infrastructure.connectors._shared.matching_provider import (
    BaseMatchingProvider,
)

logger = get_logger(__name__)


class LastFMProvider(BaseMatchingProvider):
    """LastFM track matching provider.

    Note: LastFM uses a batch API that handles all tracks at once, so it overrides
    fetch_raw_matches_for_tracks() instead of using the template method pattern.
    """

    connector_instance: Any

    def __init__(self, connector_instance: Any) -> None:
        """Initialize with LastFM connector.

        Args:
            connector_instance: LastFM service connector for API calls.
        """
        self.connector_instance = connector_instance

    @property
    @override
    def service_name(self) -> str:
        """Service identifier."""
        return "lastfm"

    @override
    async def _match_by_isrc(
        self, tracks: list[Track]
    ) -> tuple[dict[int, RawProviderMatch], list[MatchFailure]]:
        """Not used - LastFM uses batch API instead.

        LastFM API processes all tracks in a single batch call regardless of
        whether they have ISRC or not, so this method is not called.
        """
        raise NotImplementedError(
            "LastFM uses batch API - this method should not be called"
        )

    @override
    async def _match_by_artist_title(
        self, tracks: list[Track]
    ) -> tuple[dict[int, RawProviderMatch], list[MatchFailure]]:
        """Not used - LastFM uses batch API instead.

        LastFM API processes all tracks in a single batch call regardless of
        whether they have ISRC or not, so this method is not called.
        """
        raise NotImplementedError(
            "LastFM uses batch API - this method should not be called"
        )

    @override
    async def fetch_raw_matches_for_tracks(
        self,
        tracks: list[Track],
        **additional_options: Any,
    ) -> ProviderMatchResult:
        """Fetch raw track matches from LastFM.

        Args:
            tracks: Tracks to match against LastFM catalog.
            **additional_options: Additional options (unused).

        Returns:
            ProviderMatchResult with successful matches and structured failure information.
        """
        # Acknowledge additional options to satisfy linter
        _ = additional_options

        if not tracks:
            return ProviderMatchResult()

        with logger.contextualize(operation="match_lastfm", tracks_count=len(tracks)):
            logger.info(f"Matching {len(tracks)} tracks to LastFM")

            matches: dict[int, RawProviderMatch] = {}
            failures: list[MatchFailure] = []

            try:
                # Get batch track info from LastFM
                logger.info(f"Fetching LastFM metadata for {len(tracks)} tracks")

                track_infos = await self.connector_instance.get_external_track_data(
                    tracks=tracks
                )
                logger.info(
                    f"LastFM API completed: retrieved {len(track_infos)} track metadata results"
                )

                # Process results and classify failures
                processed_track_ids: set[int] = set()
                for track_id, track_info in track_infos.items():
                    processed_track_ids.add(track_id)

                    if track_info and track_info.get("lastfm_url"):
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

            # Log summary
            log_failure_summary(self.service_name, len(matches), len(failures))
            logger.info(f"Found {len(matches)} matches from {len(tracks)} tracks")

            return ProviderMatchResult(matches=matches, failures=failures)

    def _create_raw_match(self, track_info: Any) -> RawProviderMatch | None:
        """Create raw match data from LastFM track data.

        This method extracts and formats data from LastFM API without applying
        any business logic, confidence scoring, or match decisions.

        Args:
            track_info: LastFM track info response.

        Returns:
            Raw provider match data, or None if creation fails.
        """
        try:
            # Extract service data without any business logic
            service_data = {
                "title": track_info.get("lastfm_title"),
                "artist": track_info.get("lastfm_artist_name"),
                "artists": [track_info.get("lastfm_artist_name")]
                if track_info.get("lastfm_artist_name")
                else [],
                "duration_ms": track_info.get("lastfm_duration"),
                # LastFM specific data
                "lastfm_user_playcount": track_info.get("lastfm_user_playcount"),
                "lastfm_global_playcount": track_info.get("lastfm_global_playcount"),
                "lastfm_listeners": track_info.get("lastfm_listeners"),
                "lastfm_user_loved": track_info.get("lastfm_user_loved"),
            }

            # Determine match method based on available data
            # Note: This is data classification, not business logic
            match_method = "mbid" if track_info.get("lastfm_mbid") else "artist_title"

            # Return raw data - no confidence calculation or business logic
            return RawProviderMatch(
                connector_id=track_info.get("lastfm_url"),
                match_method=match_method,
                service_data=service_data,
            )

        except Exception as e:
            logger.warning(f"Failed to create LastFM raw match: {e}")
            return None
