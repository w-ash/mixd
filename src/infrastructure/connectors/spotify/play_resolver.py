"""Spotify-specific connector play resolver with rich metadata preservation.

Handles Spotify's comprehensive metadata including behavioral data, technical metadata,
and sophisticated duration-based filtering.
"""

# pyright: reportAny=false
# Legitimate Any: API response data, framework types

from collections.abc import Callable
from typing import Any

from src.config import get_logger, settings
from src.config.constants import MatchMethod, SpotifyConstants
from src.domain.entities import ConnectorTrackPlay, Track, TrackPlay
from src.domain.repositories import UnitOfWorkProtocol
from src.infrastructure.connectors.spotify import SpotifyConnector
from src.infrastructure.connectors.spotify.inward_resolver import (
    FallbackHint,
    SpotifyInwardResolver,
)

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
        logger.warning(f"Missing duration for filtering: {track_info}")
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

    spotify_connector: SpotifyConnector
    _inward_resolver: SpotifyInwardResolver

    def __init__(self, spotify_connector: SpotifyConnector | None = None):
        """Initialize with Spotify connector for track resolution."""
        self.spotify_connector = spotify_connector or SpotifyConnector()
        self._inward_resolver = SpotifyInwardResolver(
            spotify_connector=self.spotify_connector
        )

    async def resolve_connector_plays(
        self,
        connector_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> tuple[list[TrackPlay], dict[str, Any]]:
        """Resolve Spotify connector plays with full metadata preservation."""
        _ = progress_callback  # Keep for future progress tracking integration
        if not connector_plays:
            return [], self._create_empty_metrics()

        # Step 1: Extract unique Spotify track IDs + fallback hints in a single pass
        unique_ids_set: set[str] = set()
        fallback_hints: dict[str, FallbackHint] = {}
        for cp in connector_plays:
            sid = self._extract_spotify_id_from_connector_play(cp)
            if sid:
                unique_ids_set.add(sid)
                if sid not in fallback_hints and cp.artist_name and cp.track_name:
                    fallback_hints[sid] = FallbackHint(
                        artist_name=cp.artist_name, track_name=cp.track_name
                    )
        unique_spotify_ids = list(unique_ids_set)

        if not unique_spotify_ids:
            logger.warning("No valid Spotify track IDs found in connector plays")
            return [], self._create_empty_metrics()

        # Step 2: Resolve Spotify track IDs to canonical tracks
        (
            canonical_tracks_map,
            canonical_track_metrics,
        ) = await self._resolve_spotify_ids_to_canonical_tracks(
            unique_spotify_ids, uow, user_id=user_id, fallback_hints=fallback_hints
        )

        # Step 3: Create TrackPlay objects with Spotify's rich metadata
        track_plays: list[TrackPlay] = []
        filtering_stats: dict[str, Any] = {
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
                    f"Track not resolved: {connector_play.artist_name} - {connector_play.track_name}"
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
                logger.debug(
                    f"Skipped (duration): {connector_play.track_name} - "
                    + f"{connector_play.ms_played / 60000:.2f}/{duration_info}min"
                )
                continue

            # Filter out incognito plays
            incognito_mode = connector_play.service_metadata.get(
                "incognito_mode", False
            )
            if incognito_mode:
                filtering_stats["incognito_excluded"] += 1
                logger.debug(f"Skipped (incognito): {connector_play.track_name}")
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
                "resolution_method": self._inward_resolver.get_resolution_method(
                    spotify_id
                )
                if spotify_id
                else MatchMethod.PLAY_RESOLVER,
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
            "fallback_resolved": len(self._inward_resolver.fallback_resolved_ids),
            "redirect_resolved": len(self._inward_resolver.redirect_resolved_ids),
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

    def _extract_spotify_id_from_connector_play(
        self, connector_play: ConnectorTrackPlay
    ) -> str | None:
        """Extract Spotify track ID from ConnectorTrackPlay metadata."""
        # Try service metadata first
        track_uri = connector_play.service_metadata.get("track_uri")
        if isinstance(track_uri, str):
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
        self,
        spotify_ids: list[str],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        fallback_hints: dict[str, FallbackHint] | None = None,
    ) -> tuple[dict[str, Track], dict[str, int]]:
        """Resolve Spotify track IDs to canonical tracks.

        Delegates to SpotifyInwardResolver which handles:
        - Bulk lookup of existing connector mappings
        - Batch Spotify API fetch for missing tracks
        - Fallback search for dead IDs using artist+title hints
        """
        tracks_map, metrics = await self._inward_resolver.resolve_to_canonical_tracks(
            spotify_ids, uow, user_id=user_id, fallback_hints=fallback_hints
        )
        return tracks_map, {
            "new_tracks_count": metrics.created,
            "updated_tracks_count": metrics.existing,
        }

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
            "fallback_resolved": 0,
            "redirect_resolved": 0,
        }
