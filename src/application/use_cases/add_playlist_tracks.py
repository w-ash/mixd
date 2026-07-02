"""Add tracks to a canonical playlist by appending new entries (manual curation).

Resolves the given track ids to canonical tracks the user owns and inserts one
``PlaylistEntry`` per id — **duplicates allowed**, because the playlist DB models
repeated memberships (distinct ``DBPlaylistTrack.id`` per slot). This is a
different path from the workflow append in
``UpdateCanonicalPlaylistUseCase._append_entries``, which dedupes by track id for
its weekly re-run consumer; manual add must NOT inherit that filter.

Persistence is the standard load → mutate ``entries`` → ``save_playlist`` path:
the repository's consumption matcher preserves every existing row's id/added_at
and assigns fresh records to the appended entries.
"""

from datetime import UTC, datetime
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
class AddPlaylistTracksCommand:
    """Input for appending tracks to a playlist.

    ``track_ids`` is order-significant and may repeat (each occurrence becomes a
    distinct membership). ``position`` is a 0-based insertion index; ``None``
    appends to the end (the only behavior the web UI uses today).
    """

    user_id: str
    playlist_id: UUID
    track_ids: list[UUID] = field(validator=min_len(1))
    position: int | None = None


@define(frozen=True, slots=True)
class AddPlaylistTracksResult:
    """Output: the saved playlist and how many entries were added."""

    playlist: Playlist
    added: int


@define(slots=True)
class AddPlaylistTracksUseCase:
    """Append (or insert) canonical tracks into a playlist, allowing duplicates."""

    async def execute(
        self, command: AddPlaylistTracksCommand, uow: UnitOfWorkProtocol
    ) -> AddPlaylistTracksResult:
        async def transform(playlist: Playlist) -> list[PlaylistEntry]:
            # Batch-fetch the distinct ids, then build entries in request order so
            # duplicates in the request produce duplicate memberships.
            track_repo = uow.get_track_repository()
            found = await track_repo.find_tracks_by_ids(
                list(dict.fromkeys(command.track_ids))
            )

            now = datetime.now(UTC)
            new_entries: list[PlaylistEntry] = []
            for track_id in command.track_ids:
                track = found.get(track_id)
                # find_tracks_by_ids is unscoped — verify ownership so one user
                # can't graft another's track onto their playlist.
                if track is None or track.user_id != command.user_id:
                    raise NotFoundError(f"Track {track_id} not found")
                new_entries.append(PlaylistEntry(track=track, added_at=now))

            entries = list(playlist.entries)
            if command.position is None:
                entries.extend(new_entries)
            else:
                idx = max(0, min(command.position, len(entries)))
                entries[idx:idx] = new_entries
            return entries

        saved = await persist_entry_change(
            command.playlist_id, uow, user_id=command.user_id, transform=transform
        )

        # Every requested id resolved (or transform raised), duplicates allowed:
        # one appended entry per requested id.
        added = len(command.track_ids)
        logger.info(
            "Added tracks to playlist",
            playlist_id=command.playlist_id,
            added=added,
        )
        return AddPlaylistTracksResult(playlist=saved, added=added)
