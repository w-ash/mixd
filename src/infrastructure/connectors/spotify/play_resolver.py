"""Spotify-specific connector play resolver with rich metadata preservation.

Handles Spotify's comprehensive metadata including behavioral data, technical metadata,
and sophisticated duration-based filtering.
"""

from collections.abc import Callable
from typing import Any

from src.config import get_logger, settings
from src.config.constants import SpotifyConstants
from src.domain.entities import ConnectorTrackPlay, Track, TrackPlay
from src.domain.repositories import UnitOfWorkProtocol
from src.infrastructure.connectors.spotify import SpotifyConnector

from .utilities import create_track_from_spotify_data

logger = get_logger(__name__)


def should_include_spotify_play(
    ms_played: int,
    track_duration_ms: int | None,
    track_name: str | None = None,
    artist_name: str | None = None,
) -> bool:
    """Apply Spotify-specific play filtering based on duration.

    Spotify duration filtering rules:
    - Rule 1: All plays >= 4 minutes are always included
    - Rule 2: For plays < 4 minutes, use 50% threshold for tracks < 8 minutes
    """
    # Get configuration with type-safe defaults
    threshold_ms = settings.import_settings.play_threshold_ms
    threshold_percentage = settings.import_settings.play_threshold_percentage

    # Rule 1: All plays >= 4 minutes are always included
    if ms_played >= threshold_ms:
        return True

    # Rule 2: For plays < 4 minutes, use 50% threshold for tracks < 8 minutes
    if track_duration_ms is None:
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


class SpotifyConnectorPlayResolver:
    """Spotify-specific connector play resolver with rich metadata preservation.

    Preserves Spotify's comprehensive metadata:
    - Track duration for sophisticated filtering
    - Platform, country, reason_start/end behavioral data
    - Shuffle, skip, offline status
    - Full Spotify URI preservation
    - ISRC and detailed album information
    """

    def __init__(self, spotify_connector: SpotifyConnector | None = None):
        """Initialize with Spotify connector for track resolution."""
        self.spotify_connector = spotify_connector or SpotifyConnector()

    async def resolve_connector_plays(
        self,
        connector_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> tuple[list[TrackPlay], dict[str, Any]]:
        """Resolve Spotify connector plays with full metadata preservation."""
        _ = progress_callback  # Keep for future progress tracking integration
        if not connector_plays:
            return [], self._create_empty_metrics()

        # Step 1: Extract unique Spotify track IDs
        unique_spotify_ids = self._extract_unique_spotify_ids(connector_plays)

        if not unique_spotify_ids:
            logger.warning("No valid Spotify track IDs found in connector plays")
            return [], self._create_empty_metrics()

        # Step 2: Resolve Spotify track IDs to canonical tracks
        (
            canonical_tracks_map,
            canonical_track_metrics,
        ) = await self._resolve_spotify_ids_to_canonical_tracks(unique_spotify_ids, uow)

        # Step 3: Create TrackPlay objects with Spotify's rich metadata
        track_plays = []
        filtering_stats = {
            "raw_plays": len(connector_plays),
            "accepted_plays": 0,
            "duration_excluded": 0,
            "incognito_excluded": 0,
            "error_count": 0,
            "resolution_failures": [],
        }

        for connector_play in connector_plays:
            spotify_id = self._extract_spotify_id_from_connector_play(connector_play)
            canonical_track = (
                canonical_tracks_map.get(spotify_id) if spotify_id else None
            )

            if not canonical_track or not canonical_track.id:
                filtering_stats["error_count"] += 1
                failure_info = {
                    "track": f"{connector_play.artist_name} - {connector_play.track_name}",
                    "spotify_id": spotify_id,
                    "reason": "track_resolution_failed",
                }
                filtering_stats["resolution_failures"].append(failure_info)
                logger.warning(
                    f"WARNING: Track not resolved: {connector_play.artist_name} - {connector_play.track_name}"
                )
                continue

            # Apply Spotify-specific duration filtering
            if connector_play.ms_played is not None and not should_include_spotify_play(
                connector_play.ms_played,
                canonical_track.duration_ms,
                connector_play.track_name,
                connector_play.artist_name,
            ):
                filtering_stats["duration_excluded"] += 1
                duration_info = (
                    f"{canonical_track.duration_ms / 60000:.2f}"
                    if canonical_track.duration_ms
                    else "?"
                )
                logger.info(
                    f"Skipped (duration): {connector_play.track_name} - "
                    f"{connector_play.ms_played / 60000:.2f}/{duration_info}min"
                )
                continue

            # Filter out incognito plays
            incognito_mode = connector_play.service_metadata.get(
                "incognito_mode", False
            )
            if incognito_mode:
                filtering_stats["incognito_excluded"] += 1
                logger.info(f"Skipped (incognito): {connector_play.track_name}")
                continue

            # Create TrackPlay with Spotify's RICH metadata preservation
            filtering_stats["accepted_plays"] += 1

            # Preserve ALL Spotify metadata - this is the valuable behavioral data
            context = {
                # Core track identification
                "track_name": connector_play.track_name,
                "artist_name": connector_play.artist_name,
                "album_name": connector_play.album_name,
                # Spotify's rich behavioral metadata
                "platform": connector_play.service_metadata.get("platform"),
                "country": connector_play.service_metadata.get("country"),
                "reason_start": connector_play.service_metadata.get("reason_start"),
                "reason_end": connector_play.service_metadata.get("reason_end"),
                "shuffle": connector_play.service_metadata.get("shuffle"),
                "skipped": connector_play.service_metadata.get("skipped"),
                "offline": connector_play.service_metadata.get("offline"),
                "incognito_mode": incognito_mode,
                # Spotify identifiers and technical metadata
                "spotify_track_uri": connector_play.service_metadata.get("track_uri"),
                "spotify_track_id": spotify_id,
                # Resolution tracking
                "resolution_method": "spotify_connector_play_resolver",
                "architecture_version": "connector_plays_deferred_resolution",
                # Preserve any additional Spotify metadata
                **{
                    k: v
                    for k, v in connector_play.service_metadata.items()
                    if k
                    not in [
                        "platform",
                        "country",
                        "reason_start",
                        "reason_end",
                        "shuffle",
                        "skipped",
                        "offline",
                        "incognito_mode",
                        "track_uri",
                    ]
                },
            }

            track_play = TrackPlay(
                track_id=canonical_track.id,
                service="spotify",
                played_at=connector_play.played_at,
                ms_played=connector_play.ms_played,
                context=context,
                import_timestamp=connector_play.import_timestamp,
                import_source=connector_play.import_source or "spotify_export",
                import_batch_id=connector_play.import_batch_id,
            )

            track_plays.append(track_play)

        # Combine metrics
        spotify_metrics = {
            **filtering_stats,
            "new_tracks_count": canonical_track_metrics["new_tracks_count"],
            "updated_tracks_count": canonical_track_metrics["updated_tracks_count"],
            "unique_tracks_processed": len(unique_spotify_ids),
            "tracks_resolved": len(canonical_tracks_map),
        }

        logger.info(
            "Processed Spotify connector plays with rich metadata preservation",
            total_plays=len(connector_plays),
            unique_tracks=len(unique_spotify_ids),
            resolved_tracks=len(canonical_tracks_map),
            accepted_plays=filtering_stats["accepted_plays"],
            duration_excluded=filtering_stats["duration_excluded"],
            incognito_excluded=filtering_stats["incognito_excluded"],
            error_count=filtering_stats["error_count"],
            new_tracks=canonical_track_metrics["new_tracks_count"],
            updated_tracks=canonical_track_metrics["updated_tracks_count"],
        )

        return track_plays, spotify_metrics

    def _extract_unique_spotify_ids(
        self, connector_plays: list[ConnectorTrackPlay]
    ) -> list[str]:
        """Extract unique Spotify track IDs from connector plays."""
        unique_ids = set()
        for connector_play in connector_plays:
            spotify_id = self._extract_spotify_id_from_connector_play(connector_play)
            if spotify_id:
                unique_ids.add(spotify_id)
        return list(unique_ids)

    def _extract_spotify_id_from_connector_play(
        self, connector_play: ConnectorTrackPlay
    ) -> str | None:
        """Extract Spotify track ID from ConnectorTrackPlay metadata."""
        # Try service metadata first
        track_uri = connector_play.service_metadata.get("track_uri")
        if track_uri:
            return self._extract_spotify_id_from_uri(track_uri)

        # Fallback to connector_track_identifier
        if connector_play.connector_track_identifier.startswith("spotify:track:"):
            return self._extract_spotify_id_from_uri(
                connector_play.connector_track_identifier
            )

        return None

    def _extract_spotify_id_from_uri(self, spotify_uri: str) -> str | None:
        """Extract Spotify track ID from Spotify URI."""
        if not spotify_uri:
            return None

        try:
            parts = spotify_uri.split(":")
            if (
                len(parts) != SpotifyConstants.URI_PARTS_COUNT
                or parts[0] != "spotify"
                or parts[1] != "track"
            ):
                return None

            track_id = parts[2]
            if (
                len(track_id) == SpotifyConstants.TRACK_ID_LENGTH
                and track_id.replace("_", "a").replace("-", "a").isalnum()
            ):
                return track_id

        except Exception as e:
            logger.debug(f"Error parsing Spotify URI {spotify_uri}: {e}")

        return None

    async def _resolve_spotify_ids_to_canonical_tracks(
        self, spotify_ids: list[str], uow: UnitOfWorkProtocol
    ) -> tuple[dict[str, Track], dict[str, int]]:
        """Resolve Spotify track IDs to canonical tracks."""
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

        existing_canonical_track_ids = {
            track.id for track in existing_canonical_tracks.values() if track.id
        }
        updated_tracks_count = len(existing_canonical_track_ids)

        # Phase 2: Create missing tracks
        missing_spotify_ids = [
            sid
            for sid in spotify_ids
            if ("spotify", sid) not in existing_canonical_tracks
        ]

        new_canonical_track_ids = set()

        if missing_spotify_ids:
            spotify_metadata = await self.spotify_connector.get_tracks_by_ids(
                missing_spotify_ids
            )

            for spotify_id in missing_spotify_ids:
                if spotify_id not in spotify_metadata:
                    logger.warning(f"No Spotify metadata for {spotify_id}")
                    continue

                try:
                    spotify_track_data = spotify_metadata[spotify_id]
                    track_data = create_track_from_spotify_data(
                        spotify_id, spotify_track_data
                    )
                    canonical_track = await uow.get_track_repository().save_track(
                        track_data
                    )

                    # Determine primary vs non-primary mappings for relinking
                    response_id = spotify_track_data.get(
                        "id"
                    )  # API response ID (should be primary)
                    requested_id = spotify_id  # Original requested ID from user's data
                    linked_from = spotify_track_data.get("linked_from")

                    # The response ID is always the one that should be primary (market-appropriate)
                    primary_id = (
                        response_id or spotify_id
                    )  # Fallback to requested ID if no response ID

                    # Always map the primary ID first
                    await uow.get_connector_repository().map_track_to_connector(
                        canonical_track,
                        "spotify",
                        primary_id,
                        "direct_import",
                        confidence=100,
                        metadata=spotify_track_data,
                        auto_set_primary=True,
                    )

                    # If relinking occurred, map the original requested ID as non-primary
                    if (
                        linked_from
                        and "id" in linked_from
                        and requested_id != primary_id
                    ):
                        original_track_id = linked_from["id"]
                        logger.debug(
                            f"Handling Spotify relinking: {original_track_id} -> {primary_id}"
                        )
                        await uow.get_connector_repository().map_track_to_connector(
                            canonical_track,
                            "spotify",
                            original_track_id,
                            "direct_import",
                            confidence=100,
                            metadata=linked_from,
                            auto_set_primary=False,  # Original ID is non-primary reference
                        )

                    existing_canonical_tracks["spotify", spotify_id] = canonical_track

                    if canonical_track.id:
                        new_canonical_track_ids.add(canonical_track.id)

                except Exception as e:
                    logger.error(f"Failed to create track for {spotify_id}: {e}")
                    continue

        # Build final mapping
        canonical_tracks_map = {}
        for spotify_id in spotify_ids:
            canonical_track = existing_canonical_tracks.get(("spotify", spotify_id))
            if canonical_track:
                canonical_tracks_map[spotify_id] = canonical_track

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

    def _create_empty_metrics(self) -> dict[str, Any]:
        """Create empty metrics dictionary."""
        return {
            "raw_plays": 0,
            "accepted_plays": 0,
            "duration_excluded": 0,
            "incognito_excluded": 0,
            "error_count": 0,
            "resolution_failures": [],
            "new_tracks_count": 0,
            "updated_tracks_count": 0,
            "unique_tracks_processed": 0,
            "tracks_resolved": 0,
        }
