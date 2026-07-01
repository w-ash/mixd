"""Reorder a canonical playlist's entries via a complete ordered id list.

Full-list semantics: the client sends every current ``entry_id`` in the desired
order. Anything but an exact permutation of the playlist's current entries (a
missing id, an extra id, a duplicate, or an entry removed meanwhile) is a stale
view → ``NotFoundError`` (404) so the client refetches. Single-track "move to
position N" is a deferred optimization, not needed while the web UI sends the
whole list.

Persistence is the standard load → mutate ``entries`` → ``save_playlist`` path:
every entry is matched by id, so the repository renumbers ``sort_key`` only and
preserves each row's id/added_at.
"""

from uuid import UUID

from attrs import define, field

from src.application.use_cases._shared.playlist_resolver import require_playlist
from src.config import get_logger
from src.domain.entities.playlist import Playlist
from src.domain.exceptions import NotFoundError
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class ReorderPlaylistEntriesCommand:
    """Input: the playlist and the complete ordered list of its entry ids."""

    user_id: str
    playlist_id: UUID
    entry_ids: list[UUID] = field(factory=list)


@define(frozen=True, slots=True)
class ReorderPlaylistEntriesResult:
    """Output: the saved playlist in its new order."""

    playlist: Playlist


@define(slots=True)
class ReorderPlaylistEntriesUseCase:
    """Apply a full-list reordering of a playlist's entries by id."""

    async def execute(
        self, command: ReorderPlaylistEntriesCommand, uow: UnitOfWorkProtocol
    ) -> ReorderPlaylistEntriesResult:
        async with uow:
            playlist = await require_playlist(
                str(command.playlist_id), uow, user_id=command.user_id
            )

            entries_by_id = {entry.id: entry for entry in playlist.entries}
            # Exact-permutation gate: equal length AND equal id set rules out
            # duplicates, missing ids, and extras in one check.
            if len(command.entry_ids) != len(playlist.entries) or set(
                command.entry_ids
            ) != set(entries_by_id):
                raise NotFoundError(
                    "Reorder entry_ids do not match the playlist's current entries"
                )

            reordered = [entries_by_id[entry_id] for entry_id in command.entry_ids]

            playlist_repo = uow.get_playlist_repository()
            saved = await playlist_repo.save_playlist(playlist.with_entries(reordered))
            await uow.commit()

            logger.info(
                "Reordered playlist entries",
                playlist_id=command.playlist_id,
                count=len(reordered),
            )
            return ReorderPlaylistEntriesResult(playlist=saved)
