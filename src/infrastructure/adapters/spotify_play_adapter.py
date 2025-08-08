"""Adapter for processing Spotify personal data export files.

Handles the complete pipeline for importing listening history from Spotify's
JSON export files (MyData/endsong_*.json):
1. Parse export files into SpotifyPlayRecord objects
2. Resolve Spotify track IDs to canonical Track entities via Web API
3. Apply play filtering rules (duration thresholds, incognito exclusion)
4. Transform into canonical TrackPlay domain objects

This is a pure adapter focused on data transformation, not orchestration.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import get_logger, settings
from src.domain.entities import Artist, Track, TrackPlay
from src.infrastructure.connectors.spotify import SpotifyConnector
from src.infrastructure.connectors.spotify_personal_data import (
    SpotifyPlayRecord,
    parse_spotify_personal_data,
)

logger = get_logger(__name__)


def should_include_play(
    ms_played: int,
    track_duration_ms: int | None,
    track_name: str | None = None,
    artist_name: str | None = None,
) -> bool:
    """Apply play filtering: 4+ minutes always included, otherwise 50% for tracks < 8min.

    Args:
        ms_played: Duration the user actually listened
        track_duration_ms: Total track duration from API, or None if unknown

    Returns:
        True if play should be included, False if it should be filtered out
    """
    # Get configuration with type-safe defaults
    threshold_ms = settings.import_settings.play_threshold_ms
    threshold_percentage = settings.import_settings.play_threshold_percentage

    # Rule 1: All plays >= 4 minutes are always included
    if ms_played >= threshold_ms:
        return True

    # Rule 2: For plays < 4 minutes, use 50% threshold for tracks < 8 minutes
    if track_duration_ms is None:
        # This should rarely happen - log warning since it indicates track resolution issues
        track_info = (
            f"{artist_name} - {track_name}"
            if artist_name and track_name
            else "unknown track"
        )
        logger.warning(f"WARNING: Missing duration for filtering: {track_info}")
        return False  # < 4 minutes and no duration info = exclude

    # For tracks >= 8 minutes, 4-minute threshold already failed above, so exclude
    if track_duration_ms >= threshold_ms * 2:  # 8 minutes
        return False

    # For tracks < 8 minutes, use 50% threshold
    percentage_threshold = int(track_duration_ms * threshold_percentage)
    return ms_played >= percentage_threshold


class SpotifyPlayAdapter:
    """Pure adapter for Spotify play data - no orchestration, only transformations."""

    def __init__(self) -> None:
        """Initialize Spotify adapter with connector."""
        self.spotify_connector = SpotifyConnector()

    async def parse_file(self, file_path: Path) -> list[SpotifyPlayRecord]:
        """Parse Spotify JSON export file into domain play records.

        Args:
            file_path: Path to the Spotify export JSON file

        Returns:
            List of parsed SpotifyPlayRecord objects

        Raises:
            Exception: If file parsing fails
        """
        try:
            play_records = parse_spotify_personal_data(file_path)
            logger.info(
                "Parsed Spotify export",
                file_path=str(file_path),
                count=len(play_records),
            )
            return play_records
        except Exception as e:
            logger.error(
                "Failed to parse Spotify export file",
                file_path=str(file_path),
                error=str(e),
            )
            raise

    async def process_records(
        self,
        records: list[SpotifyPlayRecord],
        batch_id: str,
        import_timestamp: datetime,
        uow: Any,
    ) -> tuple[list[TrackPlay], dict[str, int]]:
        """Process Spotify play records into canonical TrackPlay objects.

        Args:
            records: Spotify play records to process
            batch_id: Import batch identifier
            import_timestamp: When the import was started
            uow: Unit of work for transaction control

        Returns:
            tuple[list[TrackPlay], dict[str, int]]: (track_plays, filtering_stats)
        """
        if not records:
            return (
                [],
                {
                    "raw_plays": 0,
                    "accepted_plays": 0,
                    "duration_excluded": 0,
                    "incognito_excluded": 0,
                },
            )

        # Step 1: Extract unique Spotify track IDs from play records
        unique_spotify_ids = self._extract_unique_spotify_ids(records)

        if not unique_spotify_ids:
            logger.warning("No valid Spotify track IDs found in play records")
            return [], {}

        # Step 2: Resolve Spotify track IDs to canonical tracks
        (
            canonical_tracks_map,
            canonical_track_metrics,
        ) = await self._resolve_spotify_ids_to_canonical_tracks(unique_spotify_ids, uow)

        # Step 3: Create canonical play records with filtering
        track_plays = []
        filtering_stats = {
            "raw_plays": len(records),
            "accepted_plays": 0,
            "duration_excluded": 0,
            "incognito_excluded": 0,
            "error_count": 0,  # Track resolution failures
            # Add canonical track metrics (zero overhead - already calculated)
            "new_tracks_count": canonical_track_metrics["new_tracks_count"],
            "updated_tracks_count": canonical_track_metrics["updated_tracks_count"],
        }

        for record in records:
            # Filter out incognito mode plays
            if record.incognito_mode:
                filtering_stats["incognito_excluded"] += 1
                logger.info(f"Skipped (incognito): {record.track_name}")
                continue

            # Find the canonical track for this play record
            spotify_id = self._extract_spotify_id_from_uri(record.track_uri)
            canonical_track = (
                canonical_tracks_map.get(spotify_id) if spotify_id else None
            )

            if not canonical_track or not canonical_track.id:
                filtering_stats["error_count"] += 1
                logger.warning(
                    f"WARNING: Track not resolved: {record.artist_name} - {record.track_name}"
                )
                continue

            # Apply play filtering
            if not should_include_play(
                record.ms_played,
                canonical_track.duration_ms,
                record.track_name,
                record.artist_name,
            ):
                filtering_stats["duration_excluded"] += 1
                duration_info = (
                    f"{canonical_track.duration_ms / 60000:.2f}"
                    if canonical_track.duration_ms
                    else "?"
                )
                logger.info(
                    f"Skipped (duration): {record.track_name} - {record.ms_played / 60000:.2f}/{duration_info}min"
                )
                continue

            # Create canonical TrackPlay object
            filtering_stats["accepted_plays"] += 1

            context = {
                "platform": record.platform,
                "country": record.country,
                "reason_start": record.reason_start,
                "reason_end": record.reason_end,
                "shuffle": record.shuffle,
                "skipped": record.skipped,
                "offline": record.offline,
                "incognito_mode": record.incognito_mode,
                "spotify_track_uri": record.track_uri,
                "track_name": record.track_name,
                "artist_name": record.artist_name,
                "album_name": record.album_name,
                "resolution_method": "match_and_identify_tracks_use_case",
                "architecture_version": "clean_architecture_consolidated",
            }

            track_play = TrackPlay(
                track_id=canonical_track.id,
                service="spotify",
                played_at=record.timestamp,
                ms_played=record.ms_played,
                context=context,
                import_timestamp=import_timestamp,
                import_source="spotify_export",
                import_batch_id=batch_id,
            )

            track_plays.append(track_play)

        # Log processing summary
        logger.info(
            "Processed Spotify play records batch",
            total_records=len(records),
            unique_tracks=len(unique_spotify_ids),
            resolved_tracks=len(canonical_tracks_map),
            accepted_plays=filtering_stats["accepted_plays"],
            duration_excluded=filtering_stats["duration_excluded"],
            incognito_excluded=filtering_stats["incognito_excluded"],
            error_count=filtering_stats["error_count"],
            new_tracks=filtering_stats["new_tracks_count"],
            updated_tracks=filtering_stats["updated_tracks_count"],
        )

        return (track_plays, filtering_stats)

    def _extract_unique_spotify_ids(
        self, play_records: list[SpotifyPlayRecord]
    ) -> list[str]:
        """Extract unique Spotify track IDs from play records."""
        if not play_records:
            return []

        unique_ids = set()

        for record in play_records:
            try:
                spotify_id = self._extract_spotify_id_from_uri(record.track_uri)
                if spotify_id:
                    unique_ids.add(spotify_id)
            except Exception as e:
                logger.warning(
                    f"Failed to extract Spotify ID from URI {record.track_uri}: {e}"
                )
                continue

        unique_ids_list = list(unique_ids)
        logger.debug(
            f"Extracted {len(unique_ids_list)} unique Spotify IDs from {len(play_records)} play records"
        )

        return unique_ids_list

    def _extract_spotify_id_from_uri(self, spotify_uri: str) -> str | None:
        """Extract Spotify track ID from Spotify URI."""
        if not spotify_uri:
            return None

        try:
            # Expected format: "spotify:track:3tI6o5tSlbB2trBl5UKJ1z"
            parts = spotify_uri.split(":")
            if len(parts) != 3 or parts[0] != "spotify" or parts[1] != "track":
                logger.debug(f"Invalid Spotify URI format: {spotify_uri}")
                return None

            track_id = parts[2]

            # Validate Spotify track ID format (22 characters, alphanumeric)
            if (
                len(track_id) == 22
                and track_id.replace("_", "a").replace("-", "a").isalnum()
            ):
                return track_id
            else:
                logger.debug(f"Invalid Spotify track ID format: {track_id}")
                return None

        except Exception as e:
            logger.debug(f"Error parsing Spotify URI {spotify_uri}: {e}")
            return None

    async def _resolve_spotify_ids_to_canonical_tracks(
        self, spotify_ids: list[str], uow
    ) -> tuple[dict[str, Track], dict[str, int]]:
        """Resolve Spotify track IDs to canonical tracks using two-phase approach.

        Phase 1: Bulk lookup existing mappings (States 1 & 2: perfect match, relinking)
        Phase 2: Create missing tracks using existing repository methods (States 3 & 4: cross-service, new tracks)
        """
        if not spotify_ids:
            return {}, {"new_tracks_count": 0, "updated_tracks_count": 0}

        logger.info(
            f"Resolving {len(spotify_ids)} Spotify track IDs to canonical tracks"
        )

        # Phase 1: Bulk lookup existing mappings
        connections = [("spotify", spotify_id) for spotify_id in spotify_ids]
        existing_canonical_tracks = (
            await uow.get_connector_repository().find_tracks_by_connectors(connections)
        )

        logger.debug(f"Found {len(existing_canonical_tracks)} existing track mappings")

        # Track existing canonical tracks (zero overhead - reuse existing data)
        existing_canonical_track_ids = {
            track.id for track in existing_canonical_tracks.values() if track.id
        }
        updated_tracks_count = len(existing_canonical_track_ids)

        # Phase 2: Create missing tracks using existing methods
        missing_spotify_ids = [
            sid
            for sid in spotify_ids
            if ("spotify", sid) not in existing_canonical_tracks
        ]

        # Track new canonical tracks (zero overhead - reuse existing logic)
        new_canonical_track_ids = set()

        if missing_spotify_ids:
            logger.info(f"Need to create {len(missing_spotify_ids)} new track mappings")

            # Batch fetch metadata for all missing tracks
            spotify_metadata = await self.spotify_connector.get_tracks_by_ids(
                missing_spotify_ids
            )

            for spotify_id in missing_spotify_ids:
                if spotify_id not in spotify_metadata:
                    logger.warning(f"No Spotify metadata for {spotify_id}")
                    continue

                try:
                    spotify_track_data = spotify_metadata[spotify_id]

                    # Create track from Spotify data
                    track_data = self._create_track_from_spotify_data(
                        spotify_id, spotify_track_data
                    )

                    # Use existing idempotent save_track method (handles ISRC/Spotify ID deduplication)
                    # This will either create new track (State 4) or return existing (State 3)
                    canonical_track = await uow.get_track_repository().save_track(
                        track_data
                    )

                    # Create Spotify connector track + mapping
                    await uow.get_connector_repository().map_track_to_connector(
                        canonical_track,
                        "spotify",
                        spotify_id,
                        "direct_import",
                        confidence=100,
                        metadata=spotify_track_data,
                    )

                    # Handle Spotify track relinking if present
                    linked_from = spotify_track_data.get("linked_from")
                    if linked_from and "id" in linked_from and canonical_track.id:
                        current_track_id = spotify_track_data.get(
                            "id"
                        )  # The ID Spotify returned
                        original_track_id = linked_from[
                            "id"
                        ]  # The original ID we requested

                        if current_track_id and current_track_id != original_track_id:
                            logger.debug(
                                f"Handling Spotify relinking: {original_track_id} -> {current_track_id}"
                            )
                            # Ensure the current (returned) track ID is set as primary
                            await uow.get_connector_repository().ensure_primary_mapping(
                                canonical_track.id, "spotify", current_track_id
                            )

                    existing_canonical_tracks["spotify", spotify_id] = canonical_track

                    # Track new canonical track (zero overhead - reuse existing variable)
                    if canonical_track.id:
                        new_canonical_track_ids.add(canonical_track.id)

                    logger.debug(
                        f"Created canonical track {canonical_track.id} for Spotify ID {spotify_id}"
                    )

                except Exception as e:
                    logger.error(f"Failed to create track for {spotify_id}: {e}")
                    # Continue processing other tracks - partial failure OK
                    continue

        # Build final mapping: spotify_id -> canonical_track
        canonical_tracks_map = {}
        for spotify_id in spotify_ids:
            canonical_track = existing_canonical_tracks.get(("spotify", spotify_id))
            if canonical_track:
                canonical_tracks_map[spotify_id] = canonical_track

        # Calculate final canonical track metrics (zero overhead - reuse existing sets)
        new_tracks_count = len(new_canonical_track_ids)
        canonical_track_metrics = {
            "new_tracks_count": new_tracks_count,
            "updated_tracks_count": updated_tracks_count,
        }

        logger.info(
            f"Successfully resolved {len(canonical_tracks_map)} out of {len(spotify_ids)} Spotify tracks",
            resolution_rate=f"{len(canonical_tracks_map) / len(spotify_ids) * 100:.1f}%"
            if spotify_ids
            else "0%",
            new_tracks=new_tracks_count,
            updated_tracks=updated_tracks_count,
        )

        return canonical_tracks_map, canonical_track_metrics

    def _create_track_from_spotify_data(
        self, spotify_id: str, spotify_data: dict
    ) -> Track:
        """Create a Track domain object from Spotify API data."""
        # Validate required fields
        title = spotify_data.get("name")
        if not title:
            raise ValueError(f"Missing track title for Spotify ID {spotify_id}")

        artists_data = spotify_data.get("artists", [])
        if not artists_data:
            raise ValueError(f"Missing artists for Spotify ID {spotify_id}")

        # Create Artist objects
        artists = []
        for artist_data in artists_data:
            artist_name = artist_data.get("name")
            if artist_name:
                artists.append(Artist(name=artist_name))

        if not artists:
            raise ValueError(f"No valid artist names found for Spotify ID {spotify_id}")

        # Extract optional fields
        album = spotify_data.get("album", {}).get("name")
        duration_ms = spotify_data.get("duration_ms")
        isrc = spotify_data.get("external_ids", {}).get("isrc")

        # Create Track object with Spotify connector ID
        track = Track(
            title=title,
            artists=artists,
            album=album,
            duration_ms=duration_ms,
            isrc=isrc,
        ).with_connector_track_id("spotify", spotify_id)

        return track
