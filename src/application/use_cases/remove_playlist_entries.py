"""Remove one or more entries from a canonical playlist by entry identity.

Addresses memberships by ``PlaylistEntry.id`` (not track id or position), so
removing one of two identical-track entries leaves the other intact — the entry
id is the only thing that distinguishes otherwise value-equal memberships.

Persistence is the standard load → mutate ``entries`` → ``save_playlist`` path:
the dropped entries simply aren't in the target list, so the repository's
consumption matcher deletes their records while preserving every survivor's
id/added_at.
"""

from uuid import UUID

from attrs import define, field
from attrs.validators import min_len

from src.application.use_cases._shared.entry_edit import persist_entry_change
from src.config import get_logger
from src.domain.entities.playlist import Playlist, PlaylistEntry
from src.domain.exceptions import NotFoundError
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class RemovePlaylistEntriesCommand:
    """Input: the playlist and the entry ids to drop (single or batch)."""

    user_id: str
    playlist_id: UUID
    entry_ids: list[UUID] = field(validator=min_len(1))


@define(frozen=True, slots=True)
class RemovePlaylistEntriesResult:
    """Output: the saved playlist and how many entries were removed."""

    playlist: Playlist
    removed: int


@define(slots=True)
class RemovePlaylistEntriesUseCase:
    """Delete playlist entries addressed by their stable membership id."""

    async def execute(
        self, command: RemovePlaylistEntriesCommand, uow: UnitOfWorkProtocol
    ) -> RemovePlaylistEntriesResult:
        target_ids = set(command.entry_ids)

        def transform(playlist: Playlist) -> list[PlaylistEntry]:
            existing_ids = {entry.id for entry in playlist.entries}
            missing = target_ids - existing_ids
            if missing:
                # A stale entry id (already removed elsewhere) → 404 so the client
                # refetches the now-authoritative list.
                raise NotFoundError(
                    "Playlist entries not found: " + ", ".join(str(m) for m in missing)
                )
            return [entry for entry in playlist.entries if entry.id not in target_ids]

        saved = await persist_entry_change(
            command.playlist_id, uow, user_id=command.user_id, transform=transform
        )

        # The stale-id gate guarantees every target id matched exactly one entry.
        removed = len(target_ids)
        logger.info(
            "Removed playlist entries",
            playlist_id=command.playlist_id,
            removed=removed,
        )
        return RemovePlaylistEntriesResult(playlist=saved, removed=removed)
