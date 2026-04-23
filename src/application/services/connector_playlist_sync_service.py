"""Connector playlist cache management.

Three entry points, one private primitive. Split follows Command-Query
Separation (CQS) so each caller's intent is unambiguous:

- ``sync_connector_playlist`` — per-playlist fetch + upsert for single-item callers
- ``get_current_connector_playlists`` — **Query**: read-through, returns the
  current ``ConnectorPlaylist`` for each id (cache-first, fetch on miss).
  The caller cannot observe cache hits vs network fetches; that's an
  implementation detail.
- ``ensure_connector_playlist_cache`` — **Command**: mutates the cache when
  entries are stale, returns metrics only (no playlist data). For callers
  who want "warm the cache" intent without wanting the data back.

None of these commit — callers own the transaction boundary so they can
compose additional writes (canonical Playlist, PlaylistLink, tag
application) inside the same UoW.
"""

import asyncio
from collections.abc import Callable, Sequence

from attrs import define

from src.application.connector_protocols import PlaylistFetchProgress
from src.application.use_cases._shared import resolve_playlist_connector
from src.config import get_logger
from src.config.settings import settings
from src.domain.entities.playlist import ConnectorPlaylist
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)

OnPageFactory = Callable[[str], PlaylistFetchProgress | None]
"""Factory mapping a connector_playlist_identifier to its per-page callback.

Returning ``None`` opts that specific playlist out of progress emission,
e.g. for cache-only reads where pagination doesn't happen. The callback
type itself lives on ``PlaylistConnector`` in connector_protocols."""


@define(frozen=True, slots=True)
class RefreshFailure:
    """One connector playlist that failed during fetch or upsert."""

    connector_playlist_identifier: str
    message: str


@define(frozen=True, slots=True)
class EnsureCacheOutcome:
    """Result of ``ensure_connector_playlist_cache``.

    Metrics only — by design. Callers that want the playlist data must
    use ``get_current_connector_playlists`` instead.
    """

    fetched: Sequence[str] = ()
    cache_hit: Sequence[str] = ()
    failed: Sequence[RefreshFailure] = ()


def has_fresh_cache(
    cached_by_id: dict[str, ConnectorPlaylist], connector_id: str
) -> bool:
    """True when our cache holds a non-NULL snapshot for the playlist."""
    cached = cached_by_id.get(connector_id)
    return cached is not None and cached.snapshot_id is not None


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


async def get_current_connector_playlists(
    connector_name: str,
    connector_playlist_ids: Sequence[str],
    uow: UnitOfWorkProtocol,
    *,
    cached_by_id: dict[str, ConnectorPlaylist] | None = None,
    on_page_factory: OnPageFactory | None = None,
    force: bool = False,
) -> tuple[dict[str, ConnectorPlaylist], list[RefreshFailure]]:
    """Return the current ConnectorPlaylist for each requested id.

    Read-through semantics: if the cache has a fresh snapshot we use it,
    otherwise we fetch from the connector and upsert into the cache. From
    the caller's perspective this is a pure read — every id either lands
    in the returned dict (success) or in the failures list. No "skipped"
    dimension exists because the caller can't act on one; they need data.

    Pass a pre-loaded ``cached_by_id`` when the caller already has it
    (saves a redundant ``list_by_connector`` query). Pass ``on_page_factory``
    to emit per-page progress during network fetches — cache hits never
    invoke it.

    When ``force`` is True every id is fetched fresh regardless of cache
    state — used by the user-driven "Re-fetch" / ``--refresh`` paths so
    a known-stale cache can be repopulated without manual intervention.

    Does NOT commit — caller owns the transaction boundary.
    """
    cp_repo = uow.get_connector_playlist_repository()
    if cached_by_id is None:
        cached_by_id = {
            cp.connector_playlist_identifier: cp
            for cp in await cp_repo.list_by_connector(connector_name)
        }

    unique_ids = list(dict.fromkeys(connector_playlist_ids))

    by_id: dict[str, ConnectorPlaylist] = {}
    to_fetch: list[str] = []
    for cid in unique_ids:
        if not force and has_fresh_cache(cached_by_id, cid):
            by_id[cid] = cached_by_id[cid]
        else:
            to_fetch.append(cid)

    fetched_by_id, failures = await _fetch_and_upsert_batch(
        connector_name, to_fetch, uow, on_page_factory=on_page_factory
    )
    by_id.update(fetched_by_id)

    return by_id, failures


async def ensure_connector_playlist_cache(
    connector_name: str,
    connector_playlist_ids: Sequence[str],
    uow: UnitOfWorkProtocol,
    *,
    cached_by_id: dict[str, ConnectorPlaylist] | None = None,
    force: bool = False,
) -> EnsureCacheOutcome:
    """Ensure each id has a fresh-cached ConnectorPlaylist; return metrics.

    Command-side of the CQS split: mutates the cache when entries are
    stale and reports counts (``fetched`` / ``cache_hit`` / ``failed``).
    Does NOT return the playlists themselves — callers who need the data
    should call ``get_current_connector_playlists`` instead. Forcing that
    split makes it impossible to accidentally discard cache-hit data at
    the API boundary (the bug class this function used to have).

    When ``force`` is True every id is fetched fresh regardless of cache
    state — backs the user-driven ``--refresh`` / "Re-fetch" affordances.

    Does NOT commit — caller owns the transaction boundary.
    """
    cp_repo = uow.get_connector_playlist_repository()
    if cached_by_id is None:
        cached_by_id = {
            cp.connector_playlist_identifier: cp
            for cp in await cp_repo.list_by_connector(connector_name)
        }

    unique_ids = list(dict.fromkeys(connector_playlist_ids))

    cache_hit: list[str] = []
    to_fetch: list[str] = []
    for cid in unique_ids:
        if not force and has_fresh_cache(cached_by_id, cid):
            cache_hit.append(cid)
        else:
            to_fetch.append(cid)

    fetched_by_id, failures = await _fetch_and_upsert_batch(
        connector_name, to_fetch, uow
    )

    return EnsureCacheOutcome(
        fetched=list(fetched_by_id.keys()),
        cache_hit=cache_hit,
        failed=failures,
    )


async def _fetch_and_upsert_batch(
    connector_name: str,
    ids_to_fetch: Sequence[str],
    uow: UnitOfWorkProtocol,
    *,
    on_page_factory: OnPageFactory | None = None,
) -> tuple[dict[str, ConnectorPlaylist], list[RefreshFailure]]:
    """Bounded-concurrent fetch + sequential DB upsert.

    TaskGroup gives us structured cancellation; Semaphore bounds the
    concurrency against the connector's rate limits. DB upserts run
    sequentially because SQLAlchemy async sessions aren't
    concurrency-safe. ``on_page_factory`` is invoked lazily per id to
    build a per-playlist pagination callback (returns ``None`` if the
    caller doesn't want progress for that id).
    """
    if not ids_to_fetch:
        return {}, []

    connector = resolve_playlist_connector(connector_name, uow)
    semaphore = asyncio.Semaphore(settings.api.spotify.concurrency)

    async def _fetch_one(
        cid: str,
    ) -> tuple[ConnectorPlaylist | None, str | None]:
        async with semaphore:
            try:
                on_page = on_page_factory(cid) if on_page_factory else None
                # Preserve call signature for mock assertions: only pass
                # ``on_page`` when the caller opted into progress emission.
                if on_page is None:
                    cp = await connector.get_playlist(cid)
                else:
                    cp = await connector.get_playlist(cid, on_page=on_page)
            except Exception as exc:
                logger.warning(
                    "Failed to fetch connector playlist",
                    connector=connector_name,
                    connector_playlist_id=cid,
                    exc_info=True,
                )
                return None, str(exc)
            else:
                return cp, None

    async with asyncio.TaskGroup() as tg:
        tasks = [(cid, tg.create_task(_fetch_one(cid))) for cid in ids_to_fetch]

    fetched: list[tuple[str, ConnectorPlaylist]] = []
    failed: list[RefreshFailure] = []
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

    cp_repo = uow.get_connector_playlist_repository()
    result: dict[str, ConnectorPlaylist] = {}
    for cid, cp in fetched:
        stored = await cp_repo.upsert_model(cp)
        result[cid] = stored

    return result, failed
