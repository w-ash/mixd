"""MusicBrainz provider for track matching.

This provider handles communication with the MusicBrainz API and transforms
MusicBrainz track data into raw provider matches without business logic.
"""

from typing import Any

from src.application.utilities.simple_batching import process_in_batches
from src.config import get_logger
from src.domain.entities import Track
from src.domain.matching.types import RawProviderMatch

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
    ) -> dict[int, RawProviderMatch]:
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
            return {}

        with logger.contextualize(
            operation="match_musicbrainz", track_count=len(tracks)
        ):
            # Group tracks by matching method
            isrc_tracks = [t for t in tracks if t.isrc]
            other_tracks = [t for t in tracks if not t.isrc and t.artists and t.title]

            results = {}

            # Process ISRC tracks first (higher confidence)
            if isrc_tracks:
                logger.info(f"Processing {len(isrc_tracks)} tracks with ISRCs")

                # Extract ISRCs for batch lookup
                isrcs = [t.isrc for t in isrc_tracks if t.isrc is not None]

                # Use native batch lookup which is already optimized
                isrc_results = await self.connector_instance.batch_isrc_lookup(isrcs)

                # Map results back to tracks
                for track in isrc_tracks:
                    if track.id is None or track.isrc is None:
                        continue

                    if track.isrc in isrc_results:
                        mbid = isrc_results[track.isrc]
                        raw_match = self._create_isrc_raw_match(mbid)
                        if raw_match:
                            results[track.id] = raw_match

                logger.info(f"Found {len(isrc_results)} matches from ISRCs")

            # Process remaining tracks using artist/title search
            remaining_tracks = [t for t in other_tracks if t.id not in results]
            if remaining_tracks:
                logger.info(
                    f"Processing {len(remaining_tracks)} tracks with artist/title"
                )
                artist_title_results = await process_in_batches(
                    remaining_tracks,
                    self._process_artist_title_batch,
                    operation_name="match_musicbrainz_artist_title",
                    connector="musicbrainz",
                )
                results.update(artist_title_results)

            logger.info(f"Found {len(results)} matches from {len(tracks)} tracks")
            return results

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

    async def _process_artist_title_batch(
        self, batch: list[Track]
    ) -> dict[int, RawProviderMatch]:
        """Process a batch of tracks using artist/title matching.

        Args:
            batch: List of Track objects with artist and title

        Returns:
            Dictionary mapping track IDs to MatchResult objects
        """
        batch_results = {}
        for track in batch:
            try:
                if not track.id or not track.artists or not track.title:
                    continue

                artist = track.artists[0].name if track.artists else ""
                recording = await self.connector_instance.search_recording(
                    artist, track.title
                )

                if recording and "id" in recording:
                    raw_match = self._create_artist_title_raw_match(
                        recording=recording,
                    )
                    if raw_match:
                        batch_results[track.id] = raw_match

            except Exception as e:
                logger.warning(f"Artist/title match failed: {e}", track_id=track.id)

        return batch_results

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
