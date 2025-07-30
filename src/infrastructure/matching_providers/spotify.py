"""Spotify provider for track matching.

This provider handles communication with the Spotify API and transforms
Spotify track data into our domain MatchResult objects.
"""

from typing import Any

from src.application.utilities.simple_batching import process_in_batches
from src.config import get_logger
from src.domain.entities import Track
from src.domain.matching.types import RawProviderMatch

logger = get_logger(__name__)


class SpotifyProvider:
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

    async def fetch_raw_matches_for_tracks(
        self,
        tracks: list[Track],
        **additional_options: Any,
    ) -> dict[int, RawProviderMatch]:
        """Fetch raw track matches from Spotify using ISRC and search APIs.

        Prioritizes ISRC matches for higher confidence, then falls back to
        artist/title search for remaining tracks.

        Args:
            tracks: Tracks to match against Spotify catalog.
            **additional_options: Additional options (unused).

        Returns:
            Track IDs mapped to raw match data without business logic applied.
        """
        # Acknowledge additional options to satisfy linter
        _ = additional_options

        if not tracks:
            return {}

        with logger.contextualize(operation="match_spotify", track_count=len(tracks)):
            # Group tracks by matching method for processing efficiency
            isrc_tracks = [t for t in tracks if t.isrc]
            other_tracks = [t for t in tracks if not t.isrc and t.artists and t.title]

            results = {}

            # Process ISRC tracks first (higher confidence)
            if isrc_tracks:
                logger.info(f"Processing {len(isrc_tracks)} tracks with ISRCs")
                isrc_results = await process_in_batches(
                    isrc_tracks,
                    self._process_isrc_batch,
                    operation_name="match_spotify_isrc",
                    connector="spotify",
                )
                results.update(isrc_results)

            # Process remaining tracks using artist/title search
            remaining_tracks = [t for t in other_tracks if t.id not in results]
            if remaining_tracks:
                logger.info(
                    f"Processing {len(remaining_tracks)} tracks with artist/title"
                )
                artist_title_results = await process_in_batches(
                    remaining_tracks,
                    self._process_artist_title_batch,
                    operation_name="match_spotify_artist_title",
                    connector="spotify",
                )
                results.update(artist_title_results)

            logger.info(f"Found {len(results)} matches from {len(tracks)} tracks")
            return results

    async def _process_isrc_batch(
        self, batch: list[Track]
    ) -> dict[int, RawProviderMatch]:
        """Process tracks using ISRC lookup.

        Args:
            batch: Tracks with ISRC codes.

        Returns:
            Track IDs mapped to raw match data.
        """
        batch_results = {}
        for track in batch:
            try:
                if not track.id or not track.isrc:
                    continue

                spotify_track = await self.connector_instance.search_by_isrc(track.isrc)
                if spotify_track and spotify_track.get("id"):
                    raw_match = self._create_raw_match(
                        spotify_track=spotify_track,
                        match_method="isrc",
                    )
                    if raw_match:
                        batch_results[track.id] = raw_match

            except Exception as e:
                logger.warning(f"ISRC match failed: {e}", track_id=track.id)

        return batch_results

    async def _process_artist_title_batch(
        self, batch: list[Track]
    ) -> dict[int, RawProviderMatch]:
        """Process tracks using artist/title search.

        Args:
            batch: Tracks with artist and title data.

        Returns:
            Track IDs mapped to raw match data.
        """
        batch_results = {}
        for track in batch:
            try:
                if not track.id or not track.artists or not track.title:
                    continue

                artist = track.artists[0].name if track.artists else ""
                spotify_track = await self.connector_instance.search_track(
                    artist, track.title
                )

                if spotify_track and spotify_track.get("id"):
                    raw_match = self._create_raw_match(
                        spotify_track=spotify_track,
                        match_method="artist_title",
                    )
                    if raw_match:
                        batch_results[track.id] = raw_match

            except Exception as e:
                logger.warning(f"Artist/title match failed: {e}", track_id=track.id)

        return batch_results

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
