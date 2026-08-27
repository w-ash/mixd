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

from src.config import get_logger
from src.domain.entities import (
    ConnectorTrackPlay,
    Track,
)
from src.domain.repositories.play import PlayResolutionOutcome
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.connector_play_resolver import (
    build_play_outcome,
    empty_play_metrics,
)
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
                metrics=empty_play_metrics(),
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

        return build_play_outcome(
            [
                (play, canonical_tracks_map.get(_extract_song_id(play) or ""))
                for play in connector_plays
            ],
            service="apple",
            user_id=user_id,
            default_import_source="apple_api",
            resolution_metrics=resolution_metrics,
            failure_detail=lambda play: {"apple_id": _extract_song_id(play) or ""},
        )
