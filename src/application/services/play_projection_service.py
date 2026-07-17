"""Day-chunked projection of the play ledger onto canonical plays.

One routine serves both writers: import Phase 3 (a batch's affected window)
and the full-history rebuild — a GDPR file import spans years, so its window
IS a rebuild. Canonical state is diffed, never blindly rewritten: an
unchanged group produces zero writes, which is what makes re-imports and
re-runs mechanical no-ops.

Chunking is correctness-aware: each chunk fetches with a margin wide enough
that every group whose earliest normalized start falls inside the chunk core
is fully visible (members can sit up to the fetch margin away in raw
``played_at`` — an end-time observation's normalized start shifts back by up
to its ``ms_played``). A group is applied by exactly one chunk: the one whose
core contains its earliest member's normalized start. That ownership rule is
batch-boundary invariance operationalized.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from uuid import UUID

from attrs import define

from src.application.use_cases._shared.batch_commit import commit_batch
from src.config import get_logger
from src.domain.entities import PlaySource, TrackPlay
from src.domain.entities.progress import ProgressEmitter, create_progress_event
from src.domain.matching.play_projection import (
    MAX_NORMALIZED_START_SHIFT,
    PlayGroup,
    ProjectedPlay,
    channel_for,
    normalized_start_time,
    project_ledger_entries,
)
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)

# One projection transaction per chunk — bounds memory on a 198k-row history
# and gives the rebuild resumable progress.
_CHUNK = timedelta(days=1)

# Raw-played_at fetch margin around a chunk core. Equal by construction to the
# domain's clamp on the end-time → start-time normalization shift, so every
# group's members are guaranteed visible to the chunk that owns its earliest
# normalized start; the cost is only a few extra fetched rows per chunk.
PROJECTION_FETCH_MARGIN = MAX_NORMALIZED_START_SHIFT

_STAT_KEYS = (
    "groups_created",
    "groups_updated",
    "groups_unchanged",
    "groups_merged",
    "orphaned_deleted",
    "resolution_divergence",
    "same_channel_collapsed",
)

# Single source of truth for rendering projection stats in user-facing
# summaries — both the import orchestrator and the rebuild use case read
# from here so the same event is never labeled two different ways.
PROJECTION_STAT_LABELS: Mapping[str, str] = {
    "groups_created": "Plays Created",
    "groups_updated": "Plays Updated",
    "groups_merged": "Plays Merged",
    "groups_unchanged": "Plays Unchanged",
    "orphaned_deleted": "Orphans Deleted",
    "resolution_divergence": "Resolution Divergence",
    "same_channel_collapsed": "Same-Channel Collapsed",
}


def _group_earliest_start(group: PlayGroup) -> datetime:
    return min(normalized_start_time(m, channel_for(m)) for m in group.members)


def _projected_fields(play: ProjectedPlay) -> dict[str, object]:
    return {
        "track_id": play.track_id,
        "service": play.service,
        "played_at": play.played_at,
        "ms_played": play.ms_played,
        "context": dict(play.context) if play.context is not None else None,
        "source_services": list(play.source_services),
        "import_source": play.import_source,
        "import_batch_id": play.import_batch_id,
    }


def _differs(existing: TrackPlay, play: ProjectedPlay) -> bool:
    return (
        existing.track_id != play.track_id
        or existing.service != play.service
        or existing.played_at != play.played_at
        or existing.ms_played != play.ms_played
        or (dict(existing.context) if existing.context else None)
        != (dict(play.context) if play.context else None)
        or list(existing.source_services or []) != list(play.source_services)
        or existing.import_source != play.import_source
        or existing.import_batch_id != play.import_batch_id
    )


@define(slots=True)
class PlayProjectionService:
    """Applies the deterministic ledger projection to canonical plays."""

    async def project_range(
        self,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        start: datetime,
        end: datetime,
        dry_run: bool = False,
        progress_emitter: ProgressEmitter | None = None,
        operation_id: str | None = None,
        claimed_play_ids: set[UUID] | None = None,
    ) -> dict[str, int]:
        """Project the ledger over [start, end) and diff-apply per day-chunk.

        ``dry_run`` computes the full diff (stats identical to a real run)
        without touching the database — no writes are issued at all.
        ``claimed_play_ids``, when provided, accumulates every canonical play
        id the projection targets (adopted, linked, or merged) so a dry-run
        caller can simulate reconciliation against the would-be state.
        """
        stats: dict[str, int] = dict.fromkeys(_STAT_KEYS, 0)
        if start >= end:
            return stats

        chunk_starts: list[datetime] = []
        cursor = start
        while cursor < end:
            chunk_starts.append(cursor)
            cursor += _CHUNK

        for index, chunk_start in enumerate(chunk_starts):
            chunk_end = min(chunk_start + _CHUNK, end)
            chunk_stats = await self._project_chunk(
                uow,
                user_id=user_id,
                chunk_start=chunk_start,
                chunk_end=chunk_end,
                dry_run=dry_run,
                claimed_play_ids=claimed_play_ids,
            )
            for key, value in chunk_stats.items():
                stats[key] += value
            if not dry_run:
                await commit_batch(uow)

            if progress_emitter is not None and operation_id is not None:
                await progress_emitter.emit_progress(
                    create_progress_event(
                        operation_id=operation_id,
                        current=index + 1,
                        total=len(chunk_starts),
                        message=(
                            f"Projected plays through {chunk_end.date().isoformat()}"
                        ),
                    )
                )

        logger.info("Play projection complete", user_id=user_id, **stats)
        return stats

    async def _project_chunk(
        self,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        chunk_start: datetime,
        chunk_end: datetime,
        dry_run: bool,
        claimed_play_ids: set[UUID] | None = None,
    ) -> dict[str, int]:
        stats: dict[str, int] = dict.fromkeys(_STAT_KEYS, 0)
        connector_repo = uow.get_connector_play_repository()
        plays_repo = uow.get_plays_repository()

        entries = await connector_repo.find_resolved_in_window(
            chunk_start - PROJECTION_FETCH_MARGIN,
            chunk_end + PROJECTION_FETCH_MARGIN,
            user_id=user_id,
        )
        if not entries:
            return stats

        result = project_ledger_entries(entries)

        # Chunk ownership: apply only groups whose earliest normalized start
        # falls inside this chunk's core.
        owned: list[tuple[PlayGroup, ProjectedPlay]] = [
            (group, play)
            for group, play in zip(result.groups, result.plays, strict=True)
            if chunk_start <= _group_earliest_start(group) < chunk_end
        ]
        if not owned:
            return stats

        stats["resolution_divergence"] = sum(1 for _, p in owned if p.divergent)
        stats["same_channel_collapsed"] = sum(len(g.absorbed) for g, _ in owned)

        member_ids = [mid for group, _ in owned for mid in group.member_ids]
        sources = await plays_repo.get_play_sources_for_connector_plays(
            member_ids, user_id=user_id
        )
        source_by_member: dict[UUID, UUID] = {
            s.connector_play_id: s.track_play_id for s in sources
        }

        window_plays = await plays_repo.get_plays_by_ids(
            list(set(source_by_member.values())), user_id=user_id
        )
        plays_by_id = {p.id: p for p in window_plays}

        # Adoption index: identical pre-existing rows without membership are
        # claimed instead of duplicated (keeps convergence robust to legacy
        # rows and cascade-rebuilt neighbors).
        adoptable = await plays_repo.find_plays_in_window(
            chunk_start - PROJECTION_FETCH_MARGIN,
            chunk_end + PROJECTION_FETCH_MARGIN,
            user_id=user_id,
        )
        sourced_play_ids = set(source_by_member.values())
        adoption_index: dict[
            tuple[UUID | None, str, datetime, int | None], TrackPlay
        ] = {
            (p.track_id, p.service, p.played_at, p.ms_played): p
            for p in adoptable
            if p.id not in sourced_play_ids
        }

        inserts: list[TrackPlay] = []
        membership_upserts: list[PlaySource] = []
        updates: list[tuple[UUID, Mapping[str, object]]] = []
        delete_candidates: set[UUID] = set()
        # Chunk-scoped claim registry. No two groups may target the same
        # canonical row: a later claim means the grouping split, and the row
        # stays with the first group while the later one falls through to
        # adopt-or-insert. No two groups may materialize the same dedup tuple
        # either: bulk insert's ON CONFLICT/batch dedup would silently drop
        # the second row while its membership edges reference the phantom id
        # (an FK violation) — the second group joins the first row instead.
        claimed_targets: set[UUID] = set()
        claimed_by_tuple: dict[tuple[UUID, str, datetime, int | None], UUID] = {}

        for group, play in owned:
            dedup_key = (play.track_id, play.service, play.played_at, play.ms_played)
            linked = {
                source_by_member[mid]
                for mid in group.member_ids
                if mid in source_by_member
            } - claimed_targets

            if not linked:
                already = claimed_by_tuple.get(dedup_key)
                if already is not None:
                    target_id = already
                    stats["groups_unchanged"] += 1
                else:
                    adopted = adoption_index.pop(dedup_key, None)
                    if adopted is None and play.ms_played:
                        # Legacy pre-v0.10 canonical rows store the raw END
                        # timestamp for end-time channels; probe the shifted
                        # key so a re-import heals such a row in place
                        # instead of inserting a duplicate alongside it.
                        shift = min(
                            timedelta(milliseconds=play.ms_played),
                            MAX_NORMALIZED_START_SHIFT,
                        )
                        adopted = adoption_index.pop(
                            (
                                play.track_id,
                                play.service,
                                play.played_at + shift,
                                play.ms_played,
                            ),
                            None,
                        )
                    if adopted is not None:
                        target_id = adopted.id
                        if _differs(adopted, play):
                            updates.append((target_id, _projected_fields(play)))
                            stats["groups_updated"] += 1
                        else:
                            stats["groups_unchanged"] += 1
                    else:
                        new_play = TrackPlay(
                            track_id=play.track_id,
                            service=play.service,
                            played_at=play.played_at,
                            user_id=play.user_id,
                            ms_played=play.ms_played,
                            context=play.context,
                            source_services=list(play.source_services),
                            import_timestamp=play.import_timestamp,
                            import_source=play.import_source,
                            import_batch_id=play.import_batch_id,
                        )
                        inserts.append(new_play)
                        target_id = new_play.id
                        stats["groups_created"] += 1
            elif len(linked) == 1:
                target_id = next(iter(linked))
                existing = plays_by_id.get(target_id)
                if existing is None or _differs(existing, play):
                    updates.append((target_id, _projected_fields(play)))
                    stats["groups_updated"] += 1
                else:
                    stats["groups_unchanged"] += 1
            else:
                # Two rows merging: keep the lowest id, repoint, drop the rest.
                target_id = min(linked)
                updates.append((target_id, _projected_fields(play)))
                delete_candidates.update(linked - {target_id})
                stats["groups_merged"] += 1

            claimed_targets.add(target_id)
            _ = claimed_by_tuple.setdefault(dedup_key, target_id)
            if claimed_play_ids is not None:
                claimed_play_ids.add(target_id)

            membership_upserts.extend(
                PlaySource(
                    user_id=play.user_id,
                    track_play_id=target_id,
                    connector_play_id=mid,
                )
                for mid in group.member_ids
                if source_by_member.get(mid) != target_id
            )

        if dry_run:
            # Mirror the real DELETE's still-has-membership guard: a doomed
            # play keeping an edge from an observation this chunk does not
            # repoint (e.g. a neighboring chunk's group) is never deleted.
            orphaned = 0
            if delete_candidates:
                repoint_target = {
                    s.connector_play_id: s.track_play_id for s in membership_upserts
                }
                candidate_edges = await plays_repo.get_play_sources_for_plays(
                    list(delete_candidates), user_id=user_id
                )
                surviving = {
                    edge.track_play_id
                    for edge in candidate_edges
                    if repoint_target.get(edge.connector_play_id, edge.track_play_id)
                    == edge.track_play_id
                }
                orphaned = len(delete_candidates - surviving)
            stats["orphaned_deleted"] = orphaned
            return stats

        if inserts:
            _ = await plays_repo.bulk_insert_plays(inserts)
        if updates:
            await plays_repo.bulk_update_plays(updates)
        if membership_upserts:
            await plays_repo.bulk_upsert_play_sources(membership_upserts)
        if delete_candidates:
            stats["orphaned_deleted"] = await plays_repo.delete_plays_without_sources(
                list(delete_candidates), user_id=user_id
            )

        return stats

    async def full_range(
        self, uow: UnitOfWorkProtocol, *, user_id: str
    ) -> tuple[datetime, datetime] | None:
        """The [start, end) window covering every resolved ledger row.

        Rebuild's helper: derives the projection span from the ledger itself.
        """
        bounds = (
            await uow.get_connector_play_repository().get_resolved_played_at_bounds(
                user_id=user_id
            )
        )
        if bounds is None:
            return None
        low, high = bounds
        return (low - PROJECTION_FETCH_MARGIN, high + PROJECTION_FETCH_MARGIN)
