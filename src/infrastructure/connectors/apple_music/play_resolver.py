"""Apple Music-specific connector play resolver.

Resolves ``connector_plays`` rows for service ``"apple"``: extract unique
catalog song ids, delegate bulk lookup/creation to
``AppleMusicInwardResolver``, map canonical tracks back to input order, and
build ``TrackPlay`` objects. Follows the Last.fm resolver's shape (the
simpler one) — Apple's recently-played feed carries no ``ms_played`` and no
private-session flag, so the only exclusion is a genuine failure to identify
the track; unresolved rows keep ``resolved_track_id = NULL`` in the ledger.
"""

from collections.abc import Callable
from uuid import UUID

from src.config import get_logger
from src.domain.entities import (
    ConnectorTrackPlay,
    PlayExclusionReason,
    Track,
    TrackPlay,
)
from src.domain.entities.shared import JsonValue
from src.domain.matching.play_projection import build_play_context
from src.domain.repositories.play import PlayResolutionOutcome, ResolutionMetrics
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.inward_track_resolver import (
    TrackResolutionMetrics,
)
from src.infrastructure.connectors.apple_music.client import AppleMusicAPIClient
from src.infrastructure.connectors.apple_music.inward_resolver import (
    AppleMusicInwardResolver,
)

logger = get_logger(__name__)


def _extract_song_id(connector_play: ConnectorTrackPlay) -> str | None:
    """The catalog song id this play names, or None when it carries none.

    The importer records the id in ``service_metadata["song_id"]``, which
    ``ConnectorTrackPlay``'s "apple" branch also derives the ledger identifier
    from (falling back to ``artist::title`` when absent). The all-digits
    identifier fallback matches reality: catalog and recent-played song ids
    are numeric.
    """
    song_id = connector_play.service_metadata.get("song_id")
    if isinstance(song_id, str) and song_id.strip():
        return song_id.strip()
    identifier = connector_play.connector_track_identifier
    if identifier.isdigit():
        return identifier
    return None


class AppleMusicConnectorPlayResolver:
    """Apple Music-specific connector play resolver."""

    _client: AppleMusicAPIClient
    _inward_resolver: AppleMusicInwardResolver

    def __init__(
        self,
        client: AppleMusicAPIClient | None = None,
        inward_resolver: AppleMusicInwardResolver | None = None,
    ):
        """Initialize with an inward resolver (constructed if not injected)."""
        self._client = client or AppleMusicAPIClient()
        self._inward_resolver = inward_resolver or AppleMusicInwardResolver(
            client=self._client
        )

    async def aclose(self) -> None:
        """Release the client's httpx2 pool (shared with the inward resolver).

        The orchestrator closes factory-built resolvers when the resolution
        phase ends — without this, every import would strand a connection
        pool.
        """
        await self._client.aclose()

    async def resolve_connector_plays(
        self,
        connector_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> PlayResolutionOutcome:
        """Resolve Apple Music connector plays to canonical track plays."""
        _ = progress_callback  # Kept for protocol parity; no phases to report
        if not connector_plays:
            return PlayResolutionOutcome(
                track_plays=[],
                metrics=self._create_empty_metrics(),
                resolutions=(),
            )

        unique_ids = list(
            dict.fromkeys(
                song_id
                for play in connector_plays
                if (song_id := _extract_song_id(play)) is not None
            )
        )

        canonical_tracks_map: dict[str, Track] = {}
        resolution_metrics = TrackResolutionMetrics()
        if unique_ids:
            (
                canonical_tracks_map,
                resolution_metrics,
            ) = await self._inward_resolver.resolve_to_canonical_tracks(
                unique_ids, uow, user_id=user_id
            )

        track_plays: list[TrackPlay] = []
        resolutions: list[tuple[ConnectorTrackPlay, UUID]] = []
        exclusions: list[tuple[ConnectorTrackPlay, PlayExclusionReason]] = []
        filtering_stats: ResolutionMetrics = {
            "raw_plays": len(connector_plays),
            "accepted_plays": 0,
            "error_count": 0,
            "resolution_failures": [],
        }

        for connector_play in connector_plays:
            song_id = _extract_song_id(connector_play)
            resolved_track = canonical_tracks_map.get(song_id) if song_id else None

            if resolved_track is None:
                filtering_stats["error_count"] += 1
                filtering_stats["resolution_failures"].append({
                    "track": f"{connector_play.artist_name} - {connector_play.track_name}",
                    "apple_id": song_id or "",
                    "reason": "track_resolution_failed",
                })
                logger.warning(
                    f"Track not resolved: {connector_play.artist_name} - "
                    f"{connector_play.track_name}"
                )
                exclusions.append((connector_play, "unresolved"))
                continue

            filtering_stats["accepted_plays"] += 1
            resolutions.append((connector_play, resolved_track.id))
            track_plays.append(
                TrackPlay(
                    track_id=resolved_track.id,
                    service="apple",
                    played_at=connector_play.played_at,
                    user_id=user_id,
                    ms_played=connector_play.ms_played,  # None for Apple Music
                    context=self._build_context(connector_play),
                    import_timestamp=connector_play.import_timestamp,
                    import_source=connector_play.import_source or "apple_api",
                    import_batch_id=connector_play.import_batch_id,
                )
            )

        apple_metrics: ResolutionMetrics = {
            **filtering_stats,
            "new_tracks_count": resolution_metrics.created,
            "updated_tracks_count": resolution_metrics.existing,
        }

        logger.info(
            "Processed Apple Music connector plays",
            total_plays=len(connector_plays),
            accepted_plays=filtering_stats["accepted_plays"],
            error_count=filtering_stats["error_count"],
            new_tracks=apple_metrics["new_tracks_count"],
            updated_tracks=apple_metrics["updated_tracks_count"],
        )

        return PlayResolutionOutcome(
            track_plays=track_plays,
            metrics=apple_metrics,
            resolutions=tuple(resolutions),
            exclusions=tuple(exclusions),
        )

    def _build_context(
        self, connector_play: ConnectorTrackPlay
    ) -> dict[str, JsonValue]:
        """Persisted play context via the domain builder (single source)."""
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
        }
