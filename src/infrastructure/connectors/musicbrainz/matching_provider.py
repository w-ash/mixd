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
)
from src.infrastructure.connectors._shared.matching_provider import (
    BaseMatchingProvider,
    IsrcThenArtistTitle,
    MatchStrategy,
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
    def _match_strategy(self) -> MatchStrategy:
        """ISRC first, artist/title for the rest and for ISRC misses."""
        return IsrcThenArtistTitle(
            match_by_isrc=self._match_by_isrc,
            match_by_artist_title=self._match_by_artist_title,
        )

    async def _match_by_isrc(
        self, tracks: list[Track]
    ) -> tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]:
        """Match tracks via the connector's per-ISRC batch lookup.

        The batch result carries only answered ISRCs: a code mapped to a
        recording matches, a code mapped to ``None`` fails as ``NO_RESULTS``,
        and a code ABSENT from the result went unanswered (its lookup
        errored) — its tracks fail as ``API_ERROR``, never ``NO_RESULTS``.
        A failure outside the connector's per-code loop (rate-limiter or
        session setup, a connector closed mid-run) fails every code the same
        way and leaves the artist/title fallback free to run.
        """
        isrc_by_track: dict[UUID, str] = {
            track.id: track.isrc for track in tracks if track.id and track.isrc
        }
        codes = list(dict.fromkeys(isrc_by_track.values()))
        whole_call_failed = False
        try:
            isrc_results = await self.connector_instance.batch_isrc_lookup(codes)
        except Exception as exc:
            logger.warning(
                f"MusicBrainz ISRC lookup failed for all {len(codes)} code(s): {exc}"
            )
            isrc_results = {}
            whole_call_failed = True
        recording_by_isrc = {
            code: recording for code, recording in isrc_results.items() if recording
        }
        failed_isrcs = {
            code for code in isrc_by_track.values() if code not in isrc_results
        }

        return self._correlate_by_code(
            tracks,
            code_of=isrc_by_track,
            candidates_by_code=recording_by_isrc,
            failed_codes=failed_isrcs,
            make_match=lambda _track, recording: self._create_isrc_raw_match(recording),
            service_label="MusicBrainz",
            method="isrc",
            code_label="ISRC",
            # The connector looks codes up one at a time, so an omitted code
            # failed alone — unless the whole call raised before the loop.
            batched=whole_call_failed,
        )

    async def _match_by_artist_title(
        self, tracks: list[Track]
    ) -> tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]:
        """Match tracks using MusicBrainz artist/title search API.

        Args:
            tracks: Tracks with artist and title to match (pre-validated by the
                strategy partition).

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

    def _create_isrc_raw_match(
        self, recording: MusicBrainzRecording
    ) -> RawProviderMatch:
        """Create raw match data for an ISRC-based match — no business logic.

        The /isrc/{isrc} lookup already returns title/artist-credit/length —
        carry them into service_data so confidence scoring compares real
        metadata (and the duration-based ISRC suspect check can run).

        Args:
            recording: Validated MusicBrainz recording from the ISRC lookup

        Returns:
            Raw provider match data
        """
        artists: list[str] = [
            credit.name for credit in recording.artist_credit if credit.name
        ]
        service_data: dict[str, JsonValue] = {
            "mbid": recording.id,
            "title": recording.title,
            "artist": artists[0] if artists else "",
            "artists": artists,
            "duration_ms": recording.length,
        }
        return RawProviderMatch(
            connector_id=recording.id,
            match_method="isrc",
            service_data=service_data,
        )

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
