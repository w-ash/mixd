"""MusicBrainz provider for track matching.

This provider handles communication with the MusicBrainz API and transforms
MusicBrainz track data into raw provider matches without business logic.
"""

from typing import override
from uuid import UUID

from src.config import get_logger
from src.domain.entities import Track
from src.domain.entities.shared import JsonValue
from src.domain.matching.types import (
    MatchFailure,
    MatchFailureReason,
    RawProviderMatch,
)
from src.infrastructure.connectors._shared.failure_handling import (
    create_and_log_failure,
    handle_track_processing_failure,
)
from src.infrastructure.connectors._shared.matching_provider import (
    BaseMatchingProvider,
)
from src.infrastructure.connectors.musicbrainz.connector import MusicBrainzConnector
from src.infrastructure.connectors.musicbrainz.models import MusicBrainzRecording

logger = get_logger(__name__)


class MusicBrainzProvider(BaseMatchingProvider):
    """MusicBrainz track matching provider."""

    connector_instance: MusicBrainzConnector

    def __init__(self, connector_instance: MusicBrainzConnector) -> None:
        """Initialize with MusicBrainz connector.

        Args:
            connector_instance: MusicBrainz service connector for API calls.
        """
        self.connector_instance = connector_instance

    @property
    @override
    def service_name(self) -> str:
        """Service identifier."""
        return "musicbrainz"

    @override
    async def _match_by_isrc(
        self, tracks: list[Track]
    ) -> tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]:
        """Match tracks using MusicBrainz batch ISRC lookup API.

        Args:
            tracks: Tracks with ISRC to match (pre-validated by the base partition).

        Returns:
            Tuple of (matches dict, failures list).
        """
        matches: dict[UUID, RawProviderMatch] = {}
        failures: list[MatchFailure] = []

        valid_tracks = [track for track in tracks if track.id]
        if valid_tracks:
            try:
                await self._lookup_isrc_batch(valid_tracks, matches, failures)
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

    async def _lookup_isrc_batch(
        self,
        valid_tracks: list[Track],
        matches: dict[UUID, RawProviderMatch],
        failures: list[MatchFailure],
    ) -> None:
        """Perform batch ISRC lookup and map results into matches/failures."""
        # Use MusicBrainz batch optimization
        isrcs = [t.isrc for t in valid_tracks if t.isrc]
        isrc_results = await self.connector_instance.batch_isrc_lookup(isrcs)

        # Map results back to tracks
        for track in valid_tracks:
            track_id = track.id
            if not track_id:
                continue
            if track.isrc and track.isrc in isrc_results:
                mbid = isrc_results[track.isrc]
                if not mbid:
                    failures.append(
                        create_and_log_failure(
                            track_id,
                            MatchFailureReason.NO_RESULTS,
                            self.service_name,
                            "isrc",
                            f"No MusicBrainz MBID for ISRC: {track.isrc}",
                        )
                    )
                    continue
                raw_match = self._create_isrc_raw_match(mbid)
                if raw_match:
                    matches[track_id] = raw_match
                else:
                    failures.append(
                        create_and_log_failure(
                            track_id,
                            MatchFailureReason.INVALID_RESPONSE,
                            self.service_name,
                            "isrc",
                            "Failed to create raw match from MusicBrainz response",
                        )
                    )
            else:
                failures.append(
                    create_and_log_failure(
                        track_id,
                        MatchFailureReason.NO_RESULTS,
                        self.service_name,
                        "isrc",
                        f"No MusicBrainz results for ISRC: {track.isrc}",
                    )
                )

    @override
    async def _match_by_artist_title(
        self, tracks: list[Track]
    ) -> tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]:
        """Match tracks using MusicBrainz artist/title search API.

        Args:
            tracks: Tracks with artist and title to match (pre-validated by the
                base partition).

        Returns:
            Tuple of (matches dict, failures list).
        """
        return await self._match_each(
            tracks, "artist_title", self._match_track_by_artist_title_one
        )

    async def _match_track_by_artist_title_one(
        self, track: Track
    ) -> tuple[RawProviderMatch | None, MatchFailure | None]:
        """Search MusicBrainz by artist/title for one track; return (match, failure)."""
        artist = track.artists[0].name if track.artists else ""
        recording = await self.connector_instance.search_recording(artist, track.title)

        if recording:
            raw_match = self._create_artist_title_raw_match(recording)
            if raw_match:
                return raw_match, None
            return None, create_and_log_failure(
                track.id,
                MatchFailureReason.INVALID_RESPONSE,
                self.service_name,
                "artist_title",
                "Failed to create raw match from MusicBrainz response",
            )
        return None, create_and_log_failure(
            track.id,
            MatchFailureReason.NO_RESULTS,
            self.service_name,
            "artist_title",
            f"No MusicBrainz results for '{artist} - {track.title}'",
        )

    def _create_isrc_raw_match(self, mbid: str) -> RawProviderMatch | None:
        """Create raw match data for ISRC-based matches.

        Args:
            mbid: MusicBrainz recording ID

        Returns:
            Raw provider match data or None if creation fails
        """
        try:
            artists: list[str] = []
            service_data: dict[str, JsonValue] = {
                "mbid": mbid,
                "title": "",
                "artist": "",
                "artists": artists,
                "duration_ms": None,
            }

            return RawProviderMatch(
                connector_id=mbid,
                match_method="isrc",
                service_data=service_data,
            )

        except Exception as e:
            logger.warning(f"Failed to create MusicBrainz ISRC raw match: {e}")
            return None

    def _create_artist_title_raw_match(
        self, recording: MusicBrainzRecording
    ) -> RawProviderMatch | None:
        """Create raw match data from MusicBrainz recording data.

        Args:
            recording: Validated MusicBrainz recording model

        Returns:
            Raw provider match data or None if creation fails
        """
        try:
            service_data: dict[str, JsonValue] = {
                "title": recording.title,
                "mbid": recording.id,
                "artists": [
                    credit.name for credit in recording.artist_credit if credit.name
                ],
                "duration_ms": recording.length,
            }

            return RawProviderMatch(
                connector_id=recording.id,
                match_method="artist_title",
                service_data=service_data,
            )

        except Exception as e:
            logger.warning(f"Failed to create MusicBrainz artist/title raw match: {e}")
            return None
