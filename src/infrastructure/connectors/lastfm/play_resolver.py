"""Last.fm-specific connector play resolver with metadata preservation.

Handles Last.fm's available metadata including MusicBrainz IDs, track URLs,
and Last.fm ecosystem integration data. Owns the full resolution flow:
extract unique ``artist::title`` identifiers, delegate bulk lookup/creation
to ``LastfmInwardResolver``, map canonical tracks back to input order, and
build ``TrackPlay`` objects with preserved metadata.
"""

from src.application.connector_protocols import Closeable
from src.config import get_logger
from src.domain.entities import (
    ConnectorTrackPlay,
    Track,
)
from src.domain.matching.protocols import CrossDiscoveryProvider
from src.domain.repositories.play import PlayResolutionOutcome
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.connector_play_resolver import (
    build_play_outcome,
    empty_play_metrics,
)
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
    _cross_discovery: CrossDiscoveryProvider | None
    _owns_lastfm_client: bool
    _owns_cross_discovery: bool

    def __init__(
        self,
        cross_discovery: CrossDiscoveryProvider | None = None,
        lastfm_client: LastFMAPIClient | None = None,
        inward_resolver: LastfmInwardResolver | None = None,
        *,
        owns_cross_discovery: bool = False,
    ):
        """Initialize with an inward resolver (constructed if not injected).

        ``owns_cross_discovery`` says the caller keeps no reference to the
        provider, so this resolver is its only teardown.
        """
        self._owns_lastfm_client = lastfm_client is None
        self.lastfm_client = lastfm_client or LastFMAPIClient()
        self._cross_discovery = cross_discovery
        self._owns_cross_discovery = owns_cross_discovery
        self._inward_resolver = inward_resolver or LastfmInwardResolver(
            lastfm_client=self.lastfm_client,
            cross_discovery=cross_discovery,
        )

    async def aclose(self) -> None:
        """Release the httpx2 pools this chain owns.

        The orchestrator closes factory-built resolvers when the resolution
        phase ends — without this, every import strands the Last.fm client's
        pool plus whatever the cross-discovery provider owns. A client built
        here is closed (shared with the inward resolver, so exactly once).
        The cross-discovery provider is closed only under
        ``owns_cross_discovery``. Anything injected without that flag belongs
        to the caller and stays open.
        """
        if self._owns_lastfm_client:
            await self.lastfm_client.aclose()
        if self._owns_cross_discovery and isinstance(self._cross_discovery, Closeable):
            await self._cross_discovery.aclose()

    async def resolve_connector_plays(
        self,
        connector_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> PlayResolutionOutcome:
        """Resolve Last.fm connector plays using existing infrastructure."""
        if not connector_plays:
            return PlayResolutionOutcome(
                track_plays=[],
                metrics=empty_play_metrics({"spotify_enhanced_count": 0}),
                resolutions=(),
            )

        # Step 1: Resolve plays to canonical tracks (input order preserved)
        (
            resolved_tracks,
            resolution_metrics,
        ) = await self._resolve_plays_to_canonical_tracks(
            connector_plays, uow, user_id=user_id
        )

        # Last.fm carries no ms_played and no private-session flag, so its only
        # exclusion is a genuine failure to identify the track.
        return build_play_outcome(
            list(zip(connector_plays, resolved_tracks, strict=True)),
            service="lastfm",
            user_id=user_id,
            default_import_source="lastfm_api",
            resolution_metrics=resolution_metrics,
            # Tracked internally by the inward resolver.
            extra_metrics={"spotify_enhanced_count": 0},
        )

    async def _resolve_plays_to_canonical_tracks(
        self,
        connector_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> tuple[list[Track | None], TrackResolutionMetrics]:
        """Resolve plays to canonical tracks, preserving input order.

        1. Extract unique ``artist::title`` identifiers.
        2. Delegate bulk lookup + creation to ``LastfmInwardResolver``.
        3. Map resolved tracks back to the original play order (``None`` when
           a play's identifier did not resolve).
        """
        unique_identifiers = self._extract_unique_lastfm_identifiers(connector_plays)
        if not unique_identifiers:
            logger.warning("No valid Last.fm track identifiers found in play records")
            # One slot per play, not an empty list: the caller pairs the two
            # strictly, and ``raw_plays`` counts pairs — a short list would
            # report the dropped plays out of existence.
            return [None] * len(connector_plays), TrackResolutionMetrics()

        (
            canonical_tracks_map,
            resolution_metrics,
        ) = await self._inward_resolver.resolve_to_canonical_tracks(
            list(unique_identifiers), uow, user_id=user_id
        )

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
