"""Re-resolve a playlist's unresolved entries against existing track mappings.

When a connector track had no canonical match at import time it is kept as an
*unresolved* entry (right position + display snapshot, ``track_id IS NULL``). If a
mapping appears later — a match-review accepted, metadata enrichment, a re-import
elsewhere — this use case hydrates those rows in place, without re-fetching the
remote or touching membership/order.

DRY by construction: it *consumes* existing mappings via the shared
``find_tracks_by_connectors`` lookup (it never *creates* them — that is
``ResolveMatchReviewUseCase``'s job) and persists through the identity-preserving
``update_playlist`` path. Idempotent: a re-run with nothing newly mappable is a
zero-change no-op.
"""

from uuid import UUID

import attrs
from attrs import define

from src.config import get_logger
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class RepairUnresolvedEntriesCommand:
    user_id: str
    playlist_id: UUID


@define(frozen=True, slots=True)
class RepairUnresolvedEntriesResult:
    repaired: int
    still_unresolved: int


@define(slots=True)
class RepairUnresolvedEntriesUseCase:
    """Hydrate now-mappable unresolved entries of one canonical playlist."""

    async def execute(
        self, command: RepairUnresolvedEntriesCommand, uow: UnitOfWorkProtocol
    ) -> RepairUnresolvedEntriesResult:
        async with uow:
            playlist_repo = uow.get_playlist_repository()
            # Raises NotFoundError if the playlist is missing or not owned.
            playlist = await playlist_repo.get_playlist_by_id(
                command.playlist_id, user_id=command.user_id
            )

            unresolved = playlist.unresolved_entries
            if not unresolved:
                return RepairUnresolvedEntriesResult(repaired=0, still_unresolved=0)

            refs = [
                (ref.connector_name, ref.connector_track_identifier)
                for e in unresolved
                if (ref := e.connector_track_ref) is not None
            ]
            resolved = await uow.get_connector_repository().find_tracks_by_connectors(
                refs, user_id=command.user_id
            )

            repaired = 0
            new_entries = list(playlist.entries)
            for i, entry in enumerate(new_entries):
                ref = entry.connector_track_ref
                if entry.track is not None or ref is None:
                    continue
                track = resolved.get((
                    ref.connector_name,
                    ref.connector_track_identifier,
                ))
                if track is not None:
                    # Attach the now-known track and drop the stale ref, keeping the
                    # entry's id/added_at/position. The persisted row is rewritten
                    # (its membership key flips unresolved→resolved, so the repo
                    # replaces it rather than updating in place), but the position
                    # and added_at ride on the entry, so membership stays faithful.
                    new_entries[i] = attrs.evolve(
                        entry, track=track, connector_track_ref=None
                    )
                    repaired += 1

            if repaired:
                await playlist_repo.update_playlist(
                    command.playlist_id,
                    attrs.evolve(playlist, entries=new_entries),
                    user_id=command.user_id,
                )
                await uow.commit()
                logger.info(
                    "Repaired unresolved playlist entries",
                    playlist_id=command.playlist_id,
                    repaired=repaired,
                )

            return RepairUnresolvedEntriesResult(
                repaired=repaired, still_unresolved=len(unresolved) - repaired
            )
