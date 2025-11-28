"""MusicBrainz provider for track matching.

This provider handles communication with the MusicBrainz API and transforms
MusicBrainz track data into raw provider matches without business logic.
"""

from typing import Any

from src.config import get_logger
from src.domain.entities import Track
from src.domain.matching.types import (
    MatchFailure,
    MatchFailureReason,
    RawProviderMatch,
)
from src.infrastructure.connectors._shared.base_matching_provider import (
    BaseMatchingProvider,
)
from src.infrastructure.connectors._shared.failure_utils import (
    create_and_log_failure,
    handle_track_processing_failure,
)

logger = get_logger(__name__)


class MusicBrainzProvider(BaseMatchingProvider):
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

    async def _match_by_isrc(
        self, tracks: list[Track]
    ) -> tuple[dict[int, RawProviderMatch], list[MatchFailure]]:
        """Match tracks using MusicBrainz batch ISRC lookup API.

        Args:
            tracks: Tracks with ISRC to match.

        Returns:
            Tuple of (matches dict, failures list).
        """
        matches: dict[int, RawProviderMatch] = {}
        failures: list[MatchFailure] = []

        # Validate tracks and collect valid ISRCs
        valid_tracks = []
        for track in tracks:
            if not track.id:
                continue

            if not track.isrc:
                failures.append(
                    create_and_log_failure(
                        track.id,
                        MatchFailureReason.NO_ISRC,
                        self.service_name,
                        "isrc",
                        "Track missing ISRC code",
                    )
                )
                continue

            valid_tracks.append(track)

        if valid_tracks:
            try:
                # Use MusicBrainz batch optimization
                isrcs = [t.isrc for t in valid_tracks if t.isrc]
                isrc_results = await self.connector_instance.batch_isrc_lookup(isrcs)

                # Map results back to tracks
                for track in valid_tracks:
                    if track.isrc and track.isrc in isrc_results:
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
                    if track.id
                )

        return matches, failures

    async def _match_by_artist_title(
        self, tracks: list[Track]
    ) -> tuple[dict[int, RawProviderMatch], list[MatchFailure]]:
        """Match tracks using MusicBrainz artist/title search API.

        Args:
            tracks: Tracks with artist and title to match.

        Returns:
            Tuple of (matches dict, failures list).
        """
        matches: dict[int, RawProviderMatch] = {}
        failures: list[MatchFailure] = []

        for track in tracks:
            if not track.id:
                continue

            # Validate track has artist and title
            if not track.artists or not track.title:
                failures.append(
                    create_and_log_failure(
                        track.id,
                        MatchFailureReason.NO_METADATA,
                        self.service_name,
                        "artist_title",
                        "Track missing artist or title data",
                    )
                )
                continue

            # Call MusicBrainz API
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
                                "artist_title",
                                "Failed to create raw match from MusicBrainz response",
                            )
                        )
                else:
                    failures.append(
                        create_and_log_failure(
                            track.id,
                            MatchFailureReason.NO_RESULTS,
                            self.service_name,
                            "artist_title",
                            f"No MusicBrainz results for '{artist} - {track.title}'",
                        )
                    )

            except Exception as e:
                failures.append(
                    handle_track_processing_failure(
                        track.id, self.service_name, "artist_title", e
                    )
                )

        return matches, failures

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
