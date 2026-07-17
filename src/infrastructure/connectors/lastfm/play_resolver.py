"""Last.fm-specific connector play resolver with metadata preservation.

Handles Last.fm's available metadata including MusicBrainz IDs, track URLs,
and Last.fm ecosystem integration data. Owns the full resolution flow:
extract unique ``artist::title`` identifiers, delegate bulk lookup/creation
to ``LastfmInwardResolver``, map canonical tracks back to input order, and
build ``TrackPlay`` objects with preserved metadata.
"""

from collections.abc import Callable
from uuid import UUID

from src.config import get_logger
from src.domain.entities import ConnectorTrackPlay, Track, TrackPlay
from src.domain.entities.shared import JsonValue
from src.domain.matching.play_projection import build_play_context
from src.domain.matching.protocols import CrossDiscoveryProvider
from src.domain.repositories.play import PlayResolutionOutcome, ResolutionMetrics
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.inward_track_resolver import (
    TrackResolutionMetrics,
)
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.identifiers import make_lastfm_identifier
from src.infrastructure.connectors.lastfm.inward_resolver import LastfmInwardResolver

logger = get_logger(__name__)


class LastfmConnectorPlayResolver:
    """Last.fm-specific connector play resolver.

    Preserves Last.fm's available metadata:
    - MusicBrainz IDs for enhanced matching
    - Album information when available
    - Track URLs for Last.fm ecosystem integration
    - Love status and streamability flags
    """

    lastfm_client: LastFMAPIClient
    _inward_resolver: LastfmInwardResolver

    def __init__(
        self,
        cross_discovery: CrossDiscoveryProvider | None = None,
        lastfm_client: LastFMAPIClient | None = None,
        inward_resolver: LastfmInwardResolver | None = None,
    ):
        """Initialize with an inward resolver (constructed if not injected)."""
        self.lastfm_client = lastfm_client or LastFMAPIClient()
        self._inward_resolver = inward_resolver or LastfmInwardResolver(
            lastfm_client=self.lastfm_client,
            cross_discovery=cross_discovery,
        )

    async def resolve_connector_plays(
        self,
        connector_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> PlayResolutionOutcome:
        """Resolve Last.fm connector plays using existing infrastructure."""
        if not connector_plays:
            return PlayResolutionOutcome(
                track_plays=[],
                metrics=self._create_empty_metrics(),
                resolutions=(),
            )

        # Step 1: Resolve plays to canonical tracks (input order preserved)
        (
            resolved_tracks,
            resolution_metrics,
        ) = await self._resolve_plays_to_canonical_tracks(
            connector_plays, uow, user_id=user_id, progress_callback=progress_callback
        )

        # Step 2: Create TrackPlay objects with Last.fm metadata preservation
        track_plays: list[TrackPlay] = []
        resolutions: list[tuple[ConnectorTrackPlay, UUID]] = []
        filtering_stats: ResolutionMetrics = {
            "raw_plays": len(connector_plays),
            "accepted_plays": 0,
            "error_count": 0,
            "resolution_failures": [],
        }

        for connector_play, resolved_track in zip(
            connector_plays, resolved_tracks, strict=False
        ):
            if resolved_track is None:
                filtering_stats["error_count"] += 1
                filtering_stats["resolution_failures"].append({
                    "track": f"{connector_play.artist_name} - {connector_play.track_name}",
                    "reason": "track_resolution_failed",
                })
                logger.warning(
                    f"Track not resolved: {connector_play.artist_name} - {connector_play.track_name}"
                )
                continue

            filtering_stats["accepted_plays"] += 1
            resolutions.append((connector_play, resolved_track.id))
            track_plays.append(
                TrackPlay(
                    track_id=resolved_track.id,
                    service="lastfm",
                    played_at=connector_play.played_at,
                    user_id=user_id,
                    ms_played=connector_play.ms_played,  # Will be None for Last.fm
                    context=self._build_context(connector_play),
                    import_timestamp=connector_play.import_timestamp,
                    import_source=connector_play.import_source or "lastfm_api",
                    import_batch_id=connector_play.import_batch_id,
                )
            )

        # Combine filtering stats with resolution metrics
        lastfm_metrics: ResolutionMetrics = {
            **filtering_stats,
            "new_tracks_count": resolution_metrics.created,
            "updated_tracks_count": resolution_metrics.existing,
            "spotify_enhanced_count": 0,  # Tracked internally by inward resolver
        }

        logger.info(
            "Processed Last.fm connector plays with metadata preservation",
            total_plays=len(connector_plays),
            accepted_plays=filtering_stats["accepted_plays"],
            error_count=filtering_stats["error_count"],
            new_tracks=lastfm_metrics["new_tracks_count"],
            updated_tracks=lastfm_metrics["updated_tracks_count"],
            spotify_enhanced=lastfm_metrics["spotify_enhanced_count"],
        )

        return PlayResolutionOutcome(
            track_plays=track_plays,
            metrics=lastfm_metrics,
            resolutions=tuple(resolutions),
        )

    async def _resolve_plays_to_canonical_tracks(
        self,
        connector_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> tuple[list[Track | None], TrackResolutionMetrics]:
        """Resolve plays to canonical tracks, preserving input order.

        1. Extract unique ``artist::title`` identifiers.
        2. Delegate bulk lookup + creation to ``LastfmInwardResolver``.
        3. Map resolved tracks back to the original play order (``None`` when
           a play's identifier did not resolve).
        """
        if progress_callback:
            progress_callback(10, 100, "Extracting unique Last.fm track identifiers...")

        unique_identifiers = self._extract_unique_lastfm_identifiers(connector_plays)
        if not unique_identifiers:
            logger.warning("No valid Last.fm track identifiers found in play records")
            return [], TrackResolutionMetrics()

        if progress_callback:
            progress_callback(
                30, 100, f"Resolving {len(unique_identifiers)} unique tracks..."
            )

        (
            canonical_tracks_map,
            resolution_metrics,
        ) = await self._inward_resolver.resolve_to_canonical_tracks(
            list(unique_identifiers), uow, user_id=user_id
        )

        if progress_callback:
            progress_callback(80, 100, "Creating resolved track list...")

        resolved_tracks: list[Track | None] = []
        for connector_play in connector_plays:
            identifier = make_lastfm_identifier(
                connector_play.artist_name, connector_play.track_name
            )
            canonical_track = canonical_tracks_map.get(identifier)

            if canonical_track:
                resolved_tracks.append(canonical_track)
            else:
                logger.warning(
                    f"Failed to resolve Last.fm track: {connector_play.artist_name} - {connector_play.track_name}"
                )
                resolved_tracks.append(None)

        resolved_count = sum(1 for t in resolved_tracks if t is not None)

        if progress_callback:
            progress_callback(
                100,
                100,
                f"Resolution complete: {resolved_count}/{len(resolved_tracks)} tracks resolved",
            )

        logger.info(
            f"Last.fm resolution complete: {resolved_count}/{len(connector_plays)} tracks resolved"
        )

        return resolved_tracks, resolution_metrics

    def _extract_unique_lastfm_identifiers(
        self, connector_plays: list[ConnectorTrackPlay]
    ) -> set[str]:
        """Extract unique Last.fm track identifiers (artist + title combinations)."""
        unique_identifiers: set[str] = set()

        for connector_play in connector_plays:
            if connector_play.artist_name and connector_play.track_name:
                identifier = make_lastfm_identifier(
                    connector_play.artist_name, connector_play.track_name
                )
                unique_identifiers.add(identifier)
            else:
                logger.warning(
                    f"Skipping record with missing artist/track: artist='{connector_play.artist_name}', track='{connector_play.track_name}'"
                )

        logger.debug(
            f"Extracted {len(unique_identifiers)} unique Last.fm identifiers from {len(connector_plays)} play records"
        )

        return unique_identifiers

    def _build_context(
        self, connector_play: ConnectorTrackPlay
    ) -> dict[str, JsonValue]:
        """Build the persisted play context.

        Delegates to the domain builder — the single implementation the
        projection also uses, so imported and rebuilt plays cannot drift.
        """
        return build_play_context(connector_play)

    def _create_empty_metrics(self) -> ResolutionMetrics:
        """Create empty metrics dictionary."""
        return {
            "raw_plays": 0,
            "accepted_plays": 0,
            "error_count": 0,
            "resolution_failures": [],
            "new_tracks_count": 0,
            "updated_tracks_count": 0,
            "spotify_enhanced_count": 0,
        }
