"""Spotify provider for track matching.

This provider handles communication with the Spotify API and transforms
Spotify track data into our domain MatchResult objects.
"""

from typing import override
from uuid import UUID

from src.config import create_matching_config, get_logger
from src.domain.entities import Track
from src.domain.entities.shared import JsonValue
from src.domain.matching.algorithms import select_best_by_title_similarity
from src.domain.matching.config import MatchingConfig
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
)
from src.infrastructure.connectors.spotify.client import SpotifyAPIClient
from src.infrastructure.connectors.spotify.models import SpotifyTrack

logger = get_logger(__name__)


class SpotifyProvider(BaseMatchingProvider):
    """Spotify track matching provider."""

    connector_instance: SpotifyAPIClient

    _matching_config: MatchingConfig

    def __init__(self, connector_instance: SpotifyAPIClient) -> None:
        """Initialize with Spotify connector.

        Args:
            connector_instance: Spotify service connector for API calls.
        """
        self.connector_instance = connector_instance
        self._matching_config = create_matching_config()

    @property
    @override
    def service_name(self) -> str:
        """Service identifier."""
        return "spotify"

    @override
    async def _match_by_isrc(
        self, tracks: list[Track]
    ) -> tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]:
        """Match tracks using Spotify ISRC search API.

        Args:
            tracks: Tracks with ISRC to match (pre-validated by the base partition).

        Returns:
            Tuple of (matches dict, failures list).
        """
        return await self._match_each(tracks, "isrc", self._match_track_by_isrc)

    async def _match_track_by_isrc(
        self, track: Track
    ) -> tuple[RawProviderMatch | None, MatchFailure | None]:
        """Search Spotify by ISRC for one track; return (match, failure)."""
        result = await self.connector_instance.search_by_isrc(track.isrc or "")
        if result and result.id:
            raw_match = self._create_raw_match(result, "isrc")
            if raw_match:
                return raw_match, None
            return None, create_and_log_failure(
                track.id,
                MatchFailureReason.INVALID_RESPONSE,
                self.service_name,
                "isrc",
                "Failed to create raw match from Spotify response",
            )
        return None, create_and_log_failure(
            track.id,
            MatchFailureReason.NO_RESULTS,
            self.service_name,
            "isrc",
            f"No Spotify results for ISRC: {track.isrc}",
        )

    @override
    async def _match_by_artist_title(
        self, tracks: list[Track]
    ) -> tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]:
        """Match tracks using Spotify artist/title search API.

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
        """Search Spotify by artist/title for one track; return (match, failure).

        Fetches multiple candidates and picks the best by title similarity.
        """
        artist_name = track.artists[0].name if track.artists else ""
        candidates = await self.connector_instance.search_track(
            artist_name, track.title
        )
        if not candidates:
            return None, create_and_log_failure(
                track.id,
                MatchFailureReason.NO_RESULTS,
                self.service_name,
                "artist_title",
                f"No Spotify results for '{artist_name} - {track.title}'",
            )

        # Rank candidates by title similarity, pick the best match
        best_result = select_best_by_title_similarity(
            track.title,
            candidates,
            lambda c: c.name,
            self._matching_config,
        )

        if best_result is None:
            return None, create_and_log_failure(
                track.id,
                MatchFailureReason.NO_RESULTS,
                self.service_name,
                "artist_title",
                f"No valid Spotify candidates for '{artist_name} - {track.title}'",
            )

        best = best_result.candidate

        if best.id:
            raw_match = self._create_raw_match(best, "artist_title")
            if raw_match:
                return raw_match, None
            return None, create_and_log_failure(
                track.id,
                MatchFailureReason.INVALID_RESPONSE,
                self.service_name,
                "artist_title",
                "Failed to create raw match from Spotify response",
            )
        return None, create_and_log_failure(
            track.id,
            MatchFailureReason.INVALID_RESPONSE,
            self.service_name,
            "artist_title",
            "Best Spotify candidate missing track ID",
        )

    def _create_raw_match(
        self, spotify_track: SpotifyTrack, match_method: str
    ) -> RawProviderMatch | None:
        """Create raw match data from Spotify track data.

        This method extracts and formats data from Spotify API without applying
        any business logic, confidence scoring, or match decisions.

        Args:
            spotify_track: Validated Spotify track model.
            match_method: Match method used ("isrc" or "artist_title").

        Returns:
            Raw provider match data, or None if creation fails.
        """
        try:
            # Extract service data without any business logic
            service_data: dict[str, JsonValue] = {
                "title": spotify_track.name,
                "artist": spotify_track.artists[0].name
                if spotify_track.artists
                else "",
                "album": spotify_track.album.name if spotify_track.album else None,
                "artists": [a.name for a in spotify_track.artists],
                "duration_ms": spotify_track.duration_ms,
                "release_date": spotify_track.album.release_date
                if spotify_track.album
                else None,
                "isrc": spotify_track.external_ids.isrc,
            }

            # Return raw data - no confidence calculation or business logic
            return RawProviderMatch(
                connector_id=spotify_track.id,
                match_method=match_method,
                service_data=service_data,
            )

        except Exception as e:
            logger.warning(f"Failed to create Spotify raw match: {e}")
            return None
