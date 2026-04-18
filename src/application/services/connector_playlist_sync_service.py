"""Connector playlist cache management.

``sync_connector_playlist`` (per-playlist) and
``batch_refresh_connector_playlists`` (bounded-concurrent batch) fetch
external playlist state and upsert it into ``DBConnectorPlaylist``.

Neither function commits — callers own the transaction boundary, which
lets them compose additional writes (canonical Playlist, PlaylistLink,
tag application) in the same UoW before committing once.
"""

import asyncio
from collections.abc import Sequence

from attrs import define

from src.application.use_cases._shared import resolve_playlist_connector
from src.config import get_logger
from src.config.settings import settings
from src.domain.entities.playlist import ConnectorPlaylist
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


async def sync_connector_playlist(
    connector_name: str,
    playlist_id: str,
    uow: UnitOfWorkProtocol,
) -> ConnectorPlaylist:
    """Fetch one external playlist and upsert it into ``DBConnectorPlaylist``.

    Raises ``ValueError`` if the connector returns no playlist. Does NOT
    commit — caller owns the transaction boundary.
    """
    connector_instance = resolve_playlist_connector(connector_name, uow)
    connector_playlist = await connector_instance.get_playlist(playlist_id)

    if not connector_playlist:
        raise ValueError(f"Playlist not found on {connector_name}: {playlist_id}")

    cp_repo = uow.get_connector_playlist_repository()
    stored_playlist = await cp_repo.upsert_model(connector_playlist)

    logger.info(
        "Synced connector playlist to database",
        connector=connector_name,
        playlist_id=playlist_id,
        db_id=stored_playlist.id,
        track_count=len(stored_playlist.items),
    )

    return stored_playlist


@define(frozen=True, slots=True)
class RefreshedPlaylist:
    """One connector playlist whose cache was successfully updated."""

    connector_playlist_identifier: str
    connector_playlist: ConnectorPlaylist


@define(frozen=True, slots=True)
class RefreshFailure:
    """One connector playlist that failed during fetch or upsert."""

    connector_playlist_identifier: str
    message: str


def has_fresh_cache(
    cached_by_id: dict[str, ConnectorPlaylist], connector_id: str
) -> bool:
    """True when our cache holds a non-NULL snapshot for the playlist."""
    cached = cached_by_id.get(connector_id)
    return cached is not None and cached.snapshot_id is not None


async def batch_refresh_connector_playlists(
    connector_name: str,
    connector_playlist_ids: Sequence[str],
    uow: UnitOfWorkProtocol,
    *,
    cached_by_id: dict[str, ConnectorPlaylist] | None = None,
) -> tuple[list[RefreshedPlaylist], list[str], list[RefreshFailure]]:
    """Bounded-concurrent fetch + upsert for a batch of connector playlists.

    Returns ``(succeeded, skipped_unchanged, failed)``. Does NOT commit —
    caller owns the transaction boundary.

    Pass a pre-loaded ``cached_by_id`` dict to skip a redundant
    ``cp_repo.list_by_connector`` query when the caller already has it.
    """
    cp_repo = uow.get_connector_playlist_repository()
    if cached_by_id is None:
        cached_by_id = {
            cp.connector_playlist_identifier: cp
            for cp in await cp_repo.list_by_connector(connector_name)
        }

    unique_ids = list(dict.fromkeys(connector_playlist_ids))

    skipped: list[str] = []
    to_fetch: list[str] = []
    for connector_id in unique_ids:
        if has_fresh_cache(cached_by_id, connector_id):
            skipped.append(connector_id)
        else:
            to_fetch.append(connector_id)

    fetched: list[tuple[str, ConnectorPlaylist]] = []
    failed: list[RefreshFailure] = []

    if to_fetch:
        connector = resolve_playlist_connector(connector_name, uow)
        semaphore = asyncio.Semaphore(settings.api.spotify.concurrency)

        async def _fetch_one(
            cid: str,
        ) -> tuple[ConnectorPlaylist | None, str | None]:
            async with semaphore:
                try:
                    return await connector.get_playlist(cid), None
                except Exception as exc:
                    logger.warning(
                        "Failed to fetch connector playlist",
                        connector=connector_name,
                        connector_playlist_id=cid,
                        exc_info=True,
                    )
                    return None, str(exc)

        async with asyncio.TaskGroup() as tg:
            tasks = [(cid, tg.create_task(_fetch_one(cid))) for cid in to_fetch]

        for cid, task in tasks:
            cp, err = task.result()
            if cp is not None:
                fetched.append((cid, cp))
            else:
                failed.append(
                    RefreshFailure(
                        connector_playlist_identifier=cid,
                        message=err or "unknown fetch error",
                    )
                )

    # Sequential DB upsert — async session is not concurrency-safe.
    succeeded: list[RefreshedPlaylist] = []
    for cid, cp in fetched:
        stored = await cp_repo.upsert_model(cp)
        succeeded.append(
            RefreshedPlaylist(
                connector_playlist_identifier=cid,
                connector_playlist=stored,
            )
        )

    return succeeded, skipped, failed
