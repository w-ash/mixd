"""Spotify-specific connector play resolver with rich metadata preservation.

Handles Spotify's comprehensive metadata including behavioral data, technical metadata,
and sophisticated duration-based filtering.
"""

from collections.abc import Callable
from enum import StrEnum

from src.config import get_logger, settings
from src.config.constants import MatchMethod, SpotifyConstants
from src.domain.entities import ConnectorTrackPlay, Track, TrackPlay
from src.domain.entities.operations import TrackContextFields
from src.domain.entities.shared import JsonValue
from src.domain.repositories.play import ResolutionMetrics
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors.spotify import SpotifyConnector
from src.infrastructure.connectors.spotify.inward_resolver import (
    FallbackHint,
    SpotifyInwardResolver,
)

logger = get_logger(__name__)


class SkipReason(StrEnum):
    """Why a resolved Spotify play was excluded from the accepted set."""

    DURATION = "duration"
    INCOGNITO = "incognito"


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
    ) -> tuple[list[TrackPlay], ResolutionMetrics]:
        """Resolve Spotify connector plays with full metadata preservation."""
        _ = progress_callback  # Keep for future progress tracking integration
        if not connector_plays:
            return [], self._create_empty_metrics()

        # Step 1: Extract unique Spotify track IDs + fallback hints
        unique_spotify_ids, fallback_hints = self._extract_ids_and_hints(
            connector_plays
        )
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
        filtering_stats: ResolutionMetrics = {
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
                filtering_stats["resolution_failures"].append({
                    "track": f"{connector_play.artist_name} - {connector_play.track_name}",
                    "spotify_id": spotify_id or "",
                    "reason": "track_resolution_failed",
                })
                logger.warning(
                    f"Track not resolved: {connector_play.artist_name} - {connector_play.track_name}"
                )
                continue

            skip = self._should_skip(connector_play, canonical_track)
            if skip is SkipReason.DURATION:
                filtering_stats["duration_excluded"] += 1
                duration_info = (
                    f"{canonical_track.duration_ms / 60000:.2f}"
                    if canonical_track.duration_ms
                    else "?"
                )
                # A DURATION skip implies ms_played is not None (the guard lives
                # in _should_skip); `or 0` only satisfies the type checker.
                ms_played = connector_play.ms_played or 0
                logger.debug(
                    f"Skipped (duration): {connector_play.track_name} - "
                    + f"{ms_played / 60000:.2f}/{duration_info}min"
                )
                continue
            if skip is SkipReason.INCOGNITO:
                filtering_stats["incognito_excluded"] += 1
                logger.debug(f"Skipped (incognito): {connector_play.track_name}")
                continue

            filtering_stats["accepted_plays"] += 1
            track_plays.append(
                TrackPlay(
                    track_id=canonical_track.id,
                    service="spotify",
                    played_at=connector_play.played_at,
                    ms_played=connector_play.ms_played,
                    context=self._build_context(connector_play, spotify_id),
                    import_timestamp=connector_play.import_timestamp,
                    import_source=connector_play.import_source or "spotify_export",
                    import_batch_id=connector_play.import_batch_id,
                )
            )

        spotify_metrics = self._assemble_metrics(
            filtering_stats,
            canonical_track_metrics,
            unique_ids_count=len(unique_spotify_ids),
            tracks_resolved=len(canonical_tracks_map),
        )

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

    def _extract_ids_and_hints(
        self, connector_plays: list[ConnectorTrackPlay]
    ) -> tuple[list[str], dict[str, FallbackHint]]:
        """Extract unique Spotify track IDs + fallback hints in a single pass."""
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
        return list(unique_ids_set), fallback_hints

    def _should_skip(
        self, connector_play: ConnectorTrackPlay, canonical_track: Track
    ) -> SkipReason | None:
        """Decide whether a resolved play should be excluded (and why).

        Applies Spotify duration filtering, then the incognito exclusion.
        Returns ``None`` when the play should be accepted.
        """
        if connector_play.ms_played is not None and not should_include_spotify_play(
            connector_play.ms_played,
            canonical_track.duration_ms,
            connector_play.track_name,
            connector_play.artist_name,
        ):
            return SkipReason.DURATION

        if connector_play.service_metadata.get("incognito_mode", False):
            return SkipReason.INCOGNITO

        return None

    def _build_context(
        self, connector_play: ConnectorTrackPlay, spotify_id: str | None
    ) -> dict[str, JsonValue]:
        """Build the persisted play context, preserving ALL Spotify metadata.

        NOTE: the key set here is persisted into ``track_plays.context`` JSON —
        any key change is user-visible data drift. Keep byte-identical.
        """
        incognito_mode = connector_play.service_metadata.get("incognito_mode", False)
        return {
            # Core track identification
            TrackContextFields.TRACK_NAME: connector_play.track_name,
            TrackContextFields.ARTIST_NAME: connector_play.artist_name,
            TrackContextFields.ALBUM_NAME: connector_play.album_name,
            # Spotify's rich behavioral metadata
            TrackContextFields.PLATFORM: connector_play.service_metadata.get(
                "platform"
            ),
            TrackContextFields.COUNTRY: connector_play.service_metadata.get("country"),
            TrackContextFields.REASON_START: connector_play.service_metadata.get(
                "reason_start"
            ),
            TrackContextFields.REASON_END: connector_play.service_metadata.get(
                "reason_end"
            ),
            TrackContextFields.SHUFFLE: connector_play.service_metadata.get("shuffle"),
            "skipped": connector_play.service_metadata.get("skipped"),
            TrackContextFields.OFFLINE: connector_play.service_metadata.get("offline"),
            TrackContextFields.INCOGNITO_MODE: incognito_mode,
            # Spotify identifiers and technical metadata
            TrackContextFields.SPOTIFY_TRACK_URI: connector_play.service_metadata.get(
                "track_uri"
            ),
            "spotify_track_id": spotify_id,
            # Resolution tracking
            "resolution_method": self._inward_resolver.get_resolution_method(spotify_id)
            if spotify_id
            else MatchMethod.PLAY_RESOLVER,
            "architecture_version": "connector_plays_deferred_resolution",
            # Preserve any additional Spotify metadata
            **{
                k: v
                for k, v in connector_play.service_metadata.items()
                if k
                not in [
                    TrackContextFields.PLATFORM,
                    TrackContextFields.COUNTRY,
                    TrackContextFields.REASON_START,
                    TrackContextFields.REASON_END,
                    TrackContextFields.SHUFFLE,
                    "skipped",
                    TrackContextFields.OFFLINE,
                    TrackContextFields.INCOGNITO_MODE,
                    "track_uri",
                ]
            },
        }

    def _assemble_metrics(
        self,
        filtering_stats: ResolutionMetrics,
        canonical_track_metrics: dict[str, int],
        *,
        unique_ids_count: int,
        tracks_resolved: int,
    ) -> ResolutionMetrics:
        """Combine per-play filtering stats with canonical-resolution counts."""
        return {
            **filtering_stats,
            "new_tracks_count": canonical_track_metrics["new_tracks_count"],
            "updated_tracks_count": canonical_track_metrics["updated_tracks_count"],
            "unique_tracks_processed": unique_ids_count,
            "tracks_resolved": tracks_resolved,
            "fallback_resolved": len(self._inward_resolver.fallback_resolved_ids),
            "redirect_resolved": len(self._inward_resolver.redirect_resolved_ids),
        }

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

    def _create_empty_metrics(self) -> ResolutionMetrics:
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
