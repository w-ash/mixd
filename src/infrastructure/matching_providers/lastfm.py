"""LastFM provider for track matching.

This provider handles communication with the LastFM API and transforms
LastFM track data into raw provider matches without business logic.
"""

from typing import Any

from src.config import get_logger
from src.domain.entities import Track
from src.domain.matching.types import RawProviderMatch

logger = get_logger(__name__)


class LastFMProvider:
    """LastFM track matching provider."""

    def __init__(self, connector_instance: Any) -> None:
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
        **additional_options: Any,
    ) -> dict[int, RawProviderMatch]:
        """Fetch raw track matches from LastFM.

        Args:
            tracks: Tracks to match against LastFM catalog.
            **additional_options: Additional options (unused).

        Returns:
            Track IDs mapped to raw match data without business logic applied.
        """
        # Acknowledge additional options to satisfy linter
        _ = additional_options

        if not tracks:
            return {}

        with logger.contextualize(operation="match_lastfm", tracks_count=len(tracks)):
            logger.info(f"Matching {len(tracks)} tracks to LastFM")

            try:
                # Get batch track info from LastFM
                logger.info(f"Fetching LastFM metadata for {len(tracks)} tracks")

                track_infos = await self.connector_instance.batch_get_track_info(
                    tracks=tracks,
                    lastfm_username=self.connector_instance.lastfm_username,
                )
                logger.info(
                    f"LastFM API completed: retrieved {len(track_infos)} track metadata results"
                )
            except Exception as e:
                logger.error(f"LastFM API failed: {type(e).__name__}: {e!s}")
                return {}

            # Convert to match results
            results = {}
            for track_id, track_info in track_infos.items():
                if track_info and track_info.lastfm_url:
                    # Find the original track
                    track = next((t for t in tracks if t.id == track_id), None)
                    if not track:
                        continue

                    # Create raw match data
                    raw_match = self._create_raw_match(track_info)
                    if raw_match:
                        results[track_id] = raw_match

            logger.info(f"Found {len(results)} matches from {len(tracks)} tracks")
            return results

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
            match_method = (
                "mbid"
                if hasattr(track_info, "musicbrainz_id") and track_info.musicbrainz_id
                else "artist_title"
            )

            # Return raw data - no confidence calculation or business logic
            return RawProviderMatch(
                connector_id=track_info.lastfm_url,
                match_method=match_method,
                service_data=service_data,
            )

        except Exception as e:
            logger.warning(f"Failed to create LastFM raw match: {e}")
            return None
