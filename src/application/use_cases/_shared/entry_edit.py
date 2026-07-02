"""Load → transform entries → save envelope for manual playlist editing.

Shared by add/remove/reorder (the entry-edit trio): each use case keeps its
pure entries transform and its Command/Result surface; this envelope owns the
ownership-gated load, persistence, and commit.
"""

from collections.abc import Awaitable, Callable
from uuid import UUID

from src.application.use_cases._shared.playlist_resolver import require_playlist
from src.domain.entities.playlist import Playlist, PlaylistEntry
from src.domain.repositories.uow import UnitOfWorkProtocol


async def persist_entry_change(
    playlist_id: UUID,
    uow: UnitOfWorkProtocol,
    *,
    user_id: str,
    transform: Callable[
        [Playlist], list[PlaylistEntry] | Awaitable[list[PlaylistEntry]]
    ],
) -> Playlist:
    """Apply an entries transform to an owned playlist and persist it.

    The transform receives the loaded playlist and returns the complete new
    entries list — plain for pure edits (remove/reorder), awaitable when it
    needs repository reads inside the transaction (add). It may raise (stale
    ids → ``NotFoundError``), in which case nothing is saved. Persistence is
    the standard load → mutate entries → ``save_playlist`` path: the
    repository's consumption matcher preserves every surviving entry's
    id/added_at.

    Raises:
        NotFoundError: If the playlist is not found (or is another user's),
            or the transform rejects stale ids.
    """
    async with uow:
        playlist = await require_playlist(str(playlist_id), uow, user_id=user_id)
        raw = transform(playlist)
        entries = await raw if isinstance(raw, Awaitable) else raw
        saved = await uow.get_playlist_repository().save_playlist(
            playlist.with_entries(entries)
        )
        await uow.commit()
        return saved
