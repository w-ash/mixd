"""Spotify recently-played API importer — the live play observation channel.

Polls ``GET /me/player/recently-played`` and writes ledger rows on the
``spotify_api`` channel, complementing the GDPR export (``spotify_export``,
richer but weeks late) and Last.fm (live but identity-weak). The v0.10.0
projection merges all three with no dedup code specific to this importer: the
ledger rows carry a ``spotify:track:`` URI, which is all the existing
``SpotifyConnectorPlayResolver`` and ``CHANNEL_SPECS`` entry need.

Deliberately a separate class from the file importer rather than a mode branch:
different raw type, different params, and — unlike the file path — real
checkpoint semantics.
"""

from datetime import datetime
from typing import override

from src.config import get_logger
from src.domain.entities import ConnectorTrackPlay, OperationResult
from src.domain.entities.progress import ProgressEmitter
from src.domain.entities.shared import JsonValue
from src.domain.exceptions import SpotifyAuthRequiredError
from src.domain.repositories.play import (
    RECENTLY_PLAYED_SCOPE,
    PlayImporterProtocol,
    PlayImportParams,
    SpotifyRecentImportParams,
)
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.domain.services.oauth_grant import missing_from_grant
from src.infrastructure.connectors._shared.token_storage import get_token_storage
from src.infrastructure.connectors.spotify.client import SpotifyAPIClient
from src.infrastructure.connectors.spotify.models import SpotifyPlayHistoryItem
from src.infrastructure.services.base_play_importer import BasePlayImporter

logger = get_logger(__name__).bind(service="spotify_recently_played")

_IMPORT_SOURCE = "spotify_api"
_MS_PER_SECOND = 1000


def _to_ms_epoch(moment: datetime) -> int:
    """Millisecond epoch, the unit Spotify's ``after`` cursor speaks."""
    return int(moment.timestamp() * _MS_PER_SECOND)


class SpotifyRecentlyPlayedImporter(
    BasePlayImporter[SpotifyPlayHistoryItem, SpotifyRecentImportParams],
    PlayImporterProtocol,
):
    """Imports the trailing window of Spotify plays as ``spotify_api`` ledger rows."""

    operation_name: str

    def __init__(self, client: SpotifyAPIClient | None = None) -> None:
        """Initialize with an optional injected client (tests supply a double)."""
        self.operation_name = "Spotify Recently Played Import"
        self._client = client or SpotifyAPIClient()
        # A client we built is ours to close; an injected one belongs to the
        # caller. The importer is created per import (never cached on the UoW),
        # so without this every poll would strand an httpx2 connection pool —
        # and the scheduled poller runs this on a loop.
        self._owns_client = client is None

    @override
    async def import_plays(
        self,
        uow: UnitOfWorkProtocol,
        params: PlayImportParams,
        *,
        user_id: str,
        progress_emitter: ProgressEmitter | None = None,
    ) -> tuple[OperationResult, list[ConnectorTrackPlay]]:
        """Poll recently-played and ingest the new plays as connector plays.

        Args:
            uow: Unit of work for database operations
            params: Spotify recently-played selectors (page limit)
            user_id: The mixd user whose grant is used and whose checkpoint
                records the resume position
            progress_emitter: Optional progress emitter

        Returns:
            Tuple of (operation_result, connector_plays_list)

        Raises:
            TypeError: If params is not SpotifyRecentImportParams.
            SpotifyAuthRequiredError: If the user has no Spotify grant, or the
                grant predates the recently-played scope.
        """
        if not isinstance(params, SpotifyRecentImportParams):
            raise TypeError(
                f"SpotifyRecentlyPlayedImporter requires SpotifyRecentImportParams, "
                f"got {type(params).__name__}"
            )

        try:
            # Precheck BEFORE import_data: the base class re-raises auth errors,
            # but failing here keeps the diagnosis precise (missing scope, not
            # "the API returned nothing") and costs no API call.
            await self._require_recently_played_grant(user_id)

            logger.info("Starting Spotify recently-played import", limit=params.limit)

            result, connector_plays = await self.import_data(
                params,
                uow=uow,
                user_id=user_id,
                progress_emitter=progress_emitter,
            )
        finally:
            await self._aclose_owned_client()

        logger.info(
            "Spotify recently-played import complete",
            connector_plays_ingested=len(connector_plays),
        )

        return result, connector_plays

    async def _aclose_owned_client(self) -> None:
        """Release the HTTP pool when this importer created the client."""
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    async def _require_recently_played_grant(user_id: str) -> None:
        """Raise unless this user's stored Spotify grant covers recently-played."""
        token = await get_token_storage().load_token("spotify", user_id)
        if token is None:
            raise SpotifyAuthRequiredError()
        if missing_from_grant(token.get("scope"), (RECENTLY_PLAYED_SCOPE,)):
            raise SpotifyAuthRequiredError(
                "Spotify needs re-connecting to grant listening-history access. "
                "Reconnect it in the web UI or run `mixd connector connect spotify`."
            )

    @override
    async def _fetch_data(
        self,
        params: SpotifyRecentImportParams,
        *,
        uow: UnitOfWorkProtocol,
        user_id: str,
        batch_id: str,
        import_timestamp: datetime,
        progress_emitter: ProgressEmitter | None = None,
        operation_id: str | None = None,
    ) -> list[SpotifyPlayHistoryItem]:
        """Fetch plays newer than the stored cursor (all of them on a first poll)."""
        _ = batch_id, import_timestamp, progress_emitter, operation_id

        after_ms = (
            None if params.force else await self._resolve_after_cursor(user_id, uow)
        )
        response = await self._client.get_recently_played(
            after_ms=after_ms, limit=params.limit
        )

        if response is None:
            # _SUPPRESS_ERRORS turns any transport/status failure into None, so
            # this is the only place a buried failure can be caught. Treating it
            # as an empty success would advance nothing but report "up to date",
            # and the ~50-play window makes skipped plays unrecoverable.
            raise RuntimeError(
                "Spotify returned no recently-played response — the connection "
                "may need re-authorizing, or the API is unavailable."
            )

        logger.info(
            "Fetched recently-played window",
            plays=len(response.items),
            resumed=after_ms is not None,
        )
        return list(response.items)

    @staticmethod
    async def _resolve_after_cursor(
        user_id: str, uow: UnitOfWorkProtocol
    ) -> int | None:
        """Read the stored ms-epoch cursor, or None to take the whole window."""
        checkpoint = await uow.get_checkpoint_repository().get_sync_checkpoint(
            user_id=user_id, service="spotify", entity_type="plays"
        )
        if checkpoint is None or checkpoint.cursor is None:
            return None
        try:
            return int(checkpoint.cursor)
        except ValueError:
            logger.warning(
                "Ignoring unparseable recently-played cursor; taking full window",
                cursor=checkpoint.cursor,
            )
            return None

    @override
    async def _process_data(
        self,
        raw_data: list[SpotifyPlayHistoryItem],
        *,
        user_id: str,
        batch_id: str,
        import_timestamp: datetime,
    ) -> list[ConnectorTrackPlay]:
        """Convert history items to ledger rows on the ``spotify_api`` channel."""
        return [
            ConnectorTrackPlay(
                artist_name=self._primary_artist(item),
                track_name=item.track.name,
                played_at=item.played_at,
                service="spotify",
                user_id=user_id,
                album_name=item.track.album.name if item.track.album else None,
                # The API reports no listening duration. Survivorship takes the
                # first non-null ms_played, so a synthetic stand-in (e.g. the
                # track duration) would win merges and corrupt listening-time
                # stats — leave it null and let the export fill it in later.
                ms_played=None,
                service_metadata=self._service_metadata(item),
                import_timestamp=import_timestamp,
                import_source=_IMPORT_SOURCE,
                import_batch_id=batch_id,
            )
            for item in raw_data
        ]

    @staticmethod
    def _primary_artist(item: SpotifyPlayHistoryItem) -> str:
        """First credited artist, matching how the export names a play."""
        return item.track.artists[0].name if item.track.artists else ""

    @staticmethod
    def _service_metadata(item: SpotifyPlayHistoryItem) -> dict[str, JsonValue]:
        """Channel-native metadata.

        ``track_uri`` is load-bearing: ``ConnectorTrackPlay`` derives its
        connector identifier from it, which is what lets the existing Spotify
        resolver handle these rows unchanged. ``duration_ms`` is carried for the
        future END-shift calibration correction (v0.10.1 Epic B7) — it is data
        on the ledger, never a stand-in for ``ms_played``.
        """
        return {
            "track_uri": f"spotify:track:{item.track.id}",
            "duration_ms": item.track.duration_ms,
            "context_type": item.context.type if item.context else None,
            "context_uri": item.context.uri if item.context else None,
        }

    @override
    async def _handle_checkpoints(
        self,
        raw_data: list[SpotifyPlayHistoryItem],
        params: SpotifyRecentImportParams,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> None:
        """Advance the cursor to the newest play seen this poll.

        No-op on an empty poll: there is nothing newer than the stored cursor,
        and rewriting it would only risk moving it backwards.
        """
        _ = params
        if not raw_data:
            return

        newest = max(item.played_at for item in raw_data)
        checkpoint_repo = uow.get_checkpoint_repository()
        checkpoint = await checkpoint_repo.get_or_create_sync_checkpoint(
            user_id=user_id, service="spotify", entity_type="plays"
        )
        # Derived from the play rather than read off response.cursors.after so
        # the value is deterministic (they are equal by construction). Re-polling
        # the boundary play is harmless — the ledger's dedup constraint
        # conflict-skips it (migration 040 made that work for null ms_played).
        _ = await checkpoint_repo.save_sync_checkpoint(
            checkpoint.with_update(timestamp=newest, cursor=str(_to_ms_epoch(newest)))
        )

        logger.info(
            "Advanced recently-played checkpoint", newest_played_at=newest.isoformat()
        )
