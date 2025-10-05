"""MusicBrainz provider for track matching.

This provider handles communication with the MusicBrainz API and transforms
MusicBrainz track data into raw provider matches without business logic.
"""

from typing import Any

from src.config import get_logger
from src.domain.entities import Track
from src.domain.matching.types import (
    MatchFailureReason,
    ProviderMatchResult,
    RawProviderMatch,
)
from src.infrastructure.connectors._shared.failure_logging import log_failure_summary
from src.infrastructure.connectors._shared.failure_utils import (
    create_and_log_failure,
    handle_track_processing_failure,
    merge_results,
    validate_track_for_method,
)

logger = get_logger(__name__)


class MusicBrainzProvider:
    """MusicBrainz track matching provider."""

    def __init__(self, connector_instance: Any) -> None:
        """Initialize with MusicBrainz connector.

        Args:
            connector_instance: MusicBrainz service connector for API calls.
        """
        self.connector_instance = connector_instance

    @property
    def service_name(self) -> str:
        """Service identifier."""
        return "musicbrainz"

    async def fetch_raw_matches_for_tracks(
        self,
        tracks: list[Track],
        **additional_options: Any,
    ) -> ProviderMatchResult:
        """Fetch raw track matches from MusicBrainz using batch ISRC and search APIs.

        Prioritizes batch ISRC lookup for efficiency, then falls back to
        individual artist/title searches.

        Args:
            tracks: Tracks to match against MusicBrainz catalog.
            **additional_options: Additional options (unused).

        Returns:
            Track IDs mapped to raw match data without business logic applied.
        """
        # Acknowledge additional options to satisfy linter
        _ = additional_options

        if not tracks:
            return ProviderMatchResult()

        with logger.contextualize(
            operation="match_musicbrainz", track_count=len(tracks)
        ):
            # Group tracks by matching method
            isrc_tracks = [t for t in tracks if t.isrc]
            artist_title_tracks = [
                t for t in tracks if not t.isrc and t.artists and t.title
            ]

            # Handle unprocessable tracks
            unprocessable_failures = [
                create_and_log_failure(
                    track_id=t.id,
                    reason=MatchFailureReason.NO_METADATA,
                    service=self.service_name,
                    method="unknown",
                    details="Track missing artist or title data",
                )
                for t in tracks
                if t.id and not t.isrc and (not t.artists or not t.title)
            ]

            # Process tracks by method using MusicBrainz batch optimization for ISRC
            isrc_result = (
                await self._process_isrc_batch(isrc_tracks)
                if isrc_tracks
                else ProviderMatchResult()
            )
            remaining_tracks = [
                t for t in artist_title_tracks if t.id not in isrc_result.matches
            ]
            artist_result = (
                await self._process_tracks_by_method(remaining_tracks, "artist_title")
                if remaining_tracks
                else ProviderMatchResult()
            )

            # Merge all results
            final_result = merge_results(
                isrc_result,
                artist_result,
                ProviderMatchResult(failures=unprocessable_failures),
            )

            # Log summary
            log_failure_summary(
                self.service_name, len(final_result.matches), len(final_result.failures)
            )
            logger.info(
                f"Found {len(final_result.matches)} matches from {len(tracks)} tracks"
            )

            return final_result

    async def _process_isrc_batch(self, tracks: list[Track]) -> ProviderMatchResult:
        """Process ISRC tracks using MusicBrainz batch optimization."""
        matches = {}
        failures = []

        # Validate tracks and collect valid ISRCs
        valid_tracks = []
        for track in tracks:
            if not track.id:
                continue

            validation_failure = validate_track_for_method(
                track,
                "isrc",
                self.service_name,
                lambda t: bool(t.isrc),
                MatchFailureReason.NO_ISRC,
                "Track missing ISRC code",
            )
            if validation_failure:
                failures.append(validation_failure)
                continue

            valid_tracks.append(track)

        if valid_tracks:
            try:
                # Use MusicBrainz batch optimization
                isrcs = [t.isrc for t in valid_tracks if t.isrc]
                isrc_results = await self.connector_instance.batch_isrc_lookup(isrcs)

                # Map results back to tracks
                for track in valid_tracks:
                    if track.isrc in isrc_results:
                        mbid = isrc_results[track.isrc]
                        raw_match = self._create_isrc_raw_match(mbid)
                        if raw_match:
                            matches[track.id] = raw_match
                        else:
                            failures.append(
                                create_and_log_failure(
                                    track.id,
                                    MatchFailureReason.INVALID_RESPONSE,
                                    self.service_name,
                                    "isrc",
                                    "Failed to create raw match from MusicBrainz response",
                                )
                            )
                    else:
                        failures.append(
                            create_and_log_failure(
                                track.id,
                                MatchFailureReason.NO_RESULTS,
                                self.service_name,
                                "isrc",
                                f"No MusicBrainz results for ISRC: {track.isrc}",
                            )
                        )

            except Exception as e:
                # Batch API failed, record failures for all tracks
                failures.extend(
                    handle_track_processing_failure(
                        track.id, self.service_name, "isrc", e
                    )
                    for track in valid_tracks
                )

        return ProviderMatchResult(matches=matches, failures=failures)

    async def _process_tracks_by_method(
        self, tracks: list[Track], method: str
    ) -> ProviderMatchResult:
        """Process tracks using specified method with structured failure handling."""
        matches = {}
        failures = []

        for track in tracks:
            if not track.id:
                continue

            # Validate track for artist/title method
            validation_failure = validate_track_for_method(
                track,
                method,
                self.service_name,
                lambda t: bool(t.artists and t.title),
                MatchFailureReason.NO_METADATA,
                "Track missing artist or title data",
            )
            if validation_failure:
                failures.append(validation_failure)
                continue

            # Attempt API call
            try:
                artist = track.artists[0].name if track.artists else ""
                recording = await self.connector_instance.search_recording(
                    artist, track.title
                )

                if recording and "id" in recording:
                    raw_match = self._create_artist_title_raw_match(recording=recording)
                    if raw_match:
                        matches[track.id] = raw_match
                    else:
                        failures.append(
                            create_and_log_failure(
                                track.id,
                                MatchFailureReason.INVALID_RESPONSE,
                                self.service_name,
                                method,
                                "Failed to create raw match from MusicBrainz response",
                            )
                        )
                else:
                    failures.append(
                        create_and_log_failure(
                            track.id,
                            MatchFailureReason.NO_RESULTS,
                            self.service_name,
                            method,
                            f"No MusicBrainz results for '{artist} - {track.title}'",
                        )
                    )

            except Exception as e:
                failures.append(
                    handle_track_processing_failure(
                        track.id, self.service_name, method, e
                    )
                )

        return ProviderMatchResult(matches=matches, failures=failures)

    def _create_isrc_raw_match(self, mbid: str) -> RawProviderMatch | None:
        """Create raw match data for ISRC-based matches.

        This method creates raw provider data without applying any business logic,
        confidence scoring, or match decisions.

        Args:
            mbid: MusicBrainz recording ID

        Returns:
            Raw provider match data or None if creation fails
        """
        try:
            # Create minimal service data for ISRC matches - no business logic
            service_data = {
                "mbid": mbid,
                "title": "",  # ISRC matches don't provide track metadata directly
                "artist": "",
                "artists": [],
                "duration_ms": None,
            }

            # Return raw data - no confidence calculation or business logic
            return RawProviderMatch(
                connector_id=mbid,
                match_method="isrc",
                service_data=service_data,
            )

        except Exception as e:
            logger.warning(f"Failed to create MusicBrainz ISRC raw match: {e}")
            return None

    def _create_artist_title_raw_match(
        self, recording: dict[str, Any]
    ) -> RawProviderMatch | None:
        """Create raw match data from MusicBrainz recording data.

        This method extracts and formats data from MusicBrainz API without applying
        any business logic, confidence scoring, or match decisions.

        Args:
            recording: MusicBrainz recording data

        Returns:
            Raw provider match data or None if creation fails
        """
        try:
            mbid = recording["id"]

            # Extract service data without any business logic
            service_data = {
                "title": recording.get("title", ""),
                "mbid": mbid,
                "artists": [],
                "duration_ms": recording.get("length"),  # MusicBrainz duration
            }

            # Add artists if available
            if "artist-credit" in recording:
                service_data["artists"] = [
                    credit["name"]
                    for credit in recording.get("artist-credit", [])
                    if isinstance(credit, dict) and "name" in credit
                ]

            # Return raw data - no confidence calculation or business logic
            return RawProviderMatch(
                connector_id=mbid,
                match_method="artist_title",
                service_data=service_data,
            )

        except Exception as e:
            logger.warning(f"Failed to create MusicBrainz artist/title raw match: {e}")
            return None
