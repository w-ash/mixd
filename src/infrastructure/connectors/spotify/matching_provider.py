"""Spotify provider for track matching.

This provider handles communication with the Spotify API and transforms
Spotify track data into our domain MatchResult objects.
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


class SpotifyProvider(BaseMatchingProvider):
    """Spotify track matching provider."""

    def __init__(self, connector_instance: Any) -> None:
        """Initialize with Spotify connector.

        Args:
            connector_instance: Spotify service connector for API calls.
        """
        self.connector_instance = connector_instance

    @property
    def service_name(self) -> str:
        """Service identifier."""
        return "spotify"

    async def _match_by_isrc(
        self, tracks: list[Track]
    ) -> tuple[dict[int, RawProviderMatch], list[MatchFailure]]:
        """Match tracks using Spotify ISRC search API.

        Args:
            tracks: Tracks with ISRC to match.

        Returns:
            Tuple of (matches dict, failures list).
        """
        matches: dict[int, RawProviderMatch] = {}
        failures: list[MatchFailure] = []

        for track in tracks:
            if not track.id:
                continue

            # Validate track has ISRC
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

            # Call Spotify API
            try:
                result = await self.connector_instance.search_by_isrc(track.isrc)
                if result and result.get("id"):
                    raw_match = self._create_raw_match(result, "isrc")
                    if raw_match:
                        matches[track.id] = raw_match
                    else:
                        failures.append(
                            create_and_log_failure(
                                track.id,
                                MatchFailureReason.INVALID_RESPONSE,
                                self.service_name,
                                "isrc",
                                "Failed to create raw match from Spotify response",
                            )
                        )
                else:
                    failures.append(
                        create_and_log_failure(
                            track.id,
                            MatchFailureReason.NO_RESULTS,
                            self.service_name,
                            "isrc",
                            f"No Spotify results for ISRC: {track.isrc}",
                        )
                    )
            except Exception as e:
                failures.append(
                    handle_track_processing_failure(
                        track.id, self.service_name, "isrc", e
                    )
                )

        return matches, failures

    async def _match_by_artist_title(
        self, tracks: list[Track]
    ) -> tuple[dict[int, RawProviderMatch], list[MatchFailure]]:
        """Match tracks using Spotify artist/title search API.

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

            # Call Spotify API
            try:
                artist_name = track.artists[0].name if track.artists else ""
                result = await self.connector_instance.search_track(
                    artist_name, track.title
                )
                if result and result.get("id"):
                    raw_match = self._create_raw_match(result, "artist_title")
                    if raw_match:
                        matches[track.id] = raw_match
                    else:
                        failures.append(
                            create_and_log_failure(
                                track.id,
                                MatchFailureReason.INVALID_RESPONSE,
                                self.service_name,
                                "artist_title",
                                "Failed to create raw match from Spotify response",
                            )
                        )
                else:
                    failures.append(
                        create_and_log_failure(
                            track.id,
                            MatchFailureReason.NO_RESULTS,
                            self.service_name,
                            "artist_title",
                            f"No Spotify results for '{artist_name} - {track.title}'",
                        )
                    )
            except Exception as e:
                failures.append(
                    handle_track_processing_failure(
                        track.id, self.service_name, "artist_title", e
                    )
                )

        return matches, failures

    def _create_raw_match(
        self, spotify_track: dict[str, Any], match_method: str
    ) -> RawProviderMatch | None:
        """Create raw match data from Spotify track data.

        This method extracts and formats data from Spotify API without applying
        any business logic, confidence scoring, or match decisions.

        Args:
            spotify_track: Spotify API response.
            match_method: Match method used ("isrc" or "artist_title").

        Returns:
            Raw provider match data, or None if creation fails.
        """
        try:
            spotify_id = spotify_track["id"]

            # Extract service data without any business logic
            service_data = {
                "title": spotify_track.get("name"),
                "album": spotify_track.get("album", {}).get("name"),
                "artists": [
                    artist.get("name", "")
                    for artist in spotify_track.get("artists", [])
                ],
                "duration_ms": spotify_track.get("duration_ms"),
                "release_date": spotify_track.get("album", {}).get("release_date"),
                "popularity": spotify_track.get("popularity"),
                "isrc": spotify_track.get("external_ids", {}).get("isrc"),
            }

            # Return raw data - no confidence calculation or business logic
            return RawProviderMatch(
                connector_id=spotify_id,
                match_method=match_method,
                service_data=service_data,
            )

        except Exception as e:
            logger.warning(f"Failed to create Spotify raw match: {e}")
            return None
