"""Day-chunked projection of the play ledger onto canonical plays.

One routine serves both writers: import Phase 3 (a batch's affected window)
and the full-history rebuild — a GDPR file import spans years, so its window
IS a rebuild. Canonical state is diffed, never blindly rewritten: an
unchanged group produces zero writes, which is what makes re-imports and
re-runs mechanical no-ops.

Chunking is correctness-aware: each chunk fetches ``_ANCHOR_REACH`` outside its
core, wide enough that every group anchored inside the core is fully visible.
Members sit away from the anchor in raw ``played_at`` for three compounding
reasons — an end-time observation's normalized start shifts back by up to its
``ms_played``, an interrupted listen's later segments trail its start by up to
``MAX_ISLAND_SPAN``, and pairing spreads a group's members across up to
``MAX_ANCHOR_PULL_BACK`` of normalized start. A group is applied by exactly one
chunk: the one whose core contains its earliest member's normalized start. That
ownership rule is batch-boundary invariance operationalized.

Two entry points, same diff-apply body: ``project_full_history`` chunks the
days the resolved ledger actually touches (the rebuild), and
``project_observed_days`` chunks only the days a batch's plays fall in (or
near) — neither pays for the empty years a sparse history straddles.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from attrs import define

from src.application.use_cases._shared.batch_commit import commit_batch
from src.config import get_logger
from src.domain.entities import PlaySource, TrackPlay
from src.domain.entities.progress import ProgressEmitter, create_progress_event
from src.domain.matching.play_projection import (
    CHANNEL_SPECS,
    CROSS_CHANNEL_TOLERANCE_FALLBACK_SECONDS,
    CROSS_CHANNEL_TOLERANCE_SECONDS,
    MAX_ISLAND_SPAN,
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

# Raw-played_at fetch margin around a chunk core, covering both ways a group's
# member can sit later in raw ``played_at`` than the group's anchor: its own
# end-time → start-time normalization shift, and the tail of a listening island
# whose earliest segment holds the anchor. Taken as the max of the two domain
# bounds rather than restated, so tightening one or widening the other cannot
# silently break "every member of every group a chunk owns is fetched by that
# chunk". The cost is only a few extra fetched rows per chunk.
PROJECTION_FETCH_MARGIN = max(MAX_NORMALIZED_START_SHIFT, MAX_ISLAND_SPAN)

# The widest tolerance any single cross-channel pairing can use. Read off the
# domain's own rules instead of restated, so registering a channel with a wider
# ``tolerance_override`` widens every window derived from it automatically.
_WIDEST_PAIR_TOLERANCE_SECONDS = max(
    CROSS_CHANNEL_TOLERANCE_SECONDS,
    CROSS_CHANNEL_TOLERANCE_FALLBACK_SECONDS,
    *(
        spec.tolerance_override
        for spec in CHANNEL_SPECS.values()
        if spec.tolerance_override is not None
    ),
)

# How much EARLIER than an observation's own normalized start its group's
# anchor (the earliest member's normalized start) can sit — and, symmetrically,
# how far past the anchor the group's last member can sit.
#
# A group holds at most one observation per channel (the grouping invariant),
# and is built from pairings that are each within the widest pair tolerance, so
# a chain running through every channel spans at most ``len(CHANNEL_SPECS) - 1``
# tolerances. Ignoring this is a silent-loss bug in both directions: a chunk
# derived from an observation's own timestamp alone misses the group its
# neighbour anchored a day earlier, and a fetch window sized for normalization
# alone misses the member sitting a tolerance past the core's end.
MAX_ANCHOR_PULL_BACK = timedelta(
    seconds=_WIDEST_PAIR_TOLERANCE_SECONDS * (len(CHANNEL_SPECS) - 1)
)

# The greatest distance, measured in raw ``played_at``, between a group's
# anchor and any of its members — normalization moves a member's start back by
# up to the fetch margin, and pairing spreads the members themselves across up
# to the anchor pull-back. Both consumers of the ownership rule are sized by it:
# a chunk fetches this far outside its core to see every member of every group
# it can own, and :func:`observed_day_cores` reaches this far back from a raw
# timestamp to find every day the group it joins can be anchored in.
_ANCHOR_REACH = PROJECTION_FETCH_MARGIN + MAX_ANCHOR_PULL_BACK

# One chunk's core: the ``[start, end)`` window whose owned groups it applies.
type _ChunkCore = tuple[datetime, datetime]

_STAT_KEYS = (
    "groups_created",
    "groups_updated",
    "groups_unchanged",
    "groups_merged",
    "orphaned_deleted",
    "resolution_divergence",
    "same_channel_collapsed",
    "listening_islands_merged",
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
    "listening_islands_merged": "Interrupted Segments Merged",
}


def observed_day_cores(played_at: Iterable[datetime]) -> list[_ChunkCore]:
    """Calendar-day cores covering every group these observations can anchor.

    A batch's affected window is the days it actually touched, not the span
    between its oldest and newest play: one stray 2011 scrobble in an otherwise
    recent incremental import spans 14 years, and tiling that span contiguously
    materializes ~5,000 day-chunks — each costing round trips to find nothing.

    OWNERSHIP INVARIANT — every group that any generated chunk could own is
    owned by exactly one generated chunk. Cores are whole UTC calendar days, so
    they never overlap ("at most one"); ``_ANCHOR_REACH`` is what buys "at least
    one". A group is applied by the chunk whose core contains its
    anchor — the earliest member's NORMALIZED start — and that anchor sits
    earlier than the raw ``played_at`` this function is handed for two
    independent reasons that BOTH have to be paid for:

    1. Normalization: an end-time observation's own start is its ``played_at``
       minus ``ms_played``, clamped to ``MAX_NORMALIZED_START_SHIFT`` (6h).
    2. Islands: a record can be the tail segment of an interrupted listen whose
       earliest segment — up to ``MAX_ISLAND_SPAN`` earlier — holds the anchor.
    3. Pairing: the group's anchor belongs to whichever member starts earliest,
       and an already-stored cross-channel neighbour can hold that anchor up to
       ``MAX_ANCHOR_PULL_BACK`` earlier still.

    Paying only (1) loses plays outright. The failure is real and does not heal:
    a GDPR row at 06:00:10 with ~6h of ``ms_played`` normalizes to 00:00:10 and
    unions with a stored 23:59:50 scrobble from the previous day, so the group
    anchors the previous day while only the current day's core was generated —
    no chunk owns the group and the play never projects.

    Note the asymmetry: only the *earlier* side needs covering, because the
    anchor is a minimum over the group's members and so can never be later than
    the observation's own normalized start.
    """
    days: set[date] = set()
    for moment in played_at:
        # Both endpoints in UTC: cores are UTC midnights, and a tz-aware
        # non-UTC ``played_at`` (accepted at validation, preserved by the GDPR
        # parser) would otherwise name a local date whose UTC core is never
        # generated — 2024-06-15T20:30-05:00 is the 16th in UTC.
        latest = moment.astimezone(UTC).date()
        earliest = (moment - _ANCHOR_REACH).astimezone(UTC).date()
        # Whole range, not just the two endpoints: correctness must not rest on
        # the reach-back happening to be shorter than a day.
        days.update(
            earliest + timedelta(days=offset)
            for offset in range((latest - earliest).days + 1)
        )
    return [
        (midnight, midnight + _CHUNK)
        for midnight in (datetime.combine(day, time.min, UTC) for day in sorted(days))
    ]


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

    async def project_full_history(
        self,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        dry_run: bool = False,
        progress_emitter: ProgressEmitter | None = None,
        operation_id: str | None = None,
        claimed_play_ids: set[UUID] | None = None,
    ) -> dict[str, int] | None:
        """Project every day the resolved ledger touches — the rebuild's entry.

        One ``SELECT DISTINCT`` of active days replaces tiling the span
        between the ledger's bounds: one stray 2011 row in an otherwise
        recent history would otherwise cost ~5,000 empty day-chunks of round
        trips. Returns ``None`` when the ledger holds no resolved rows, so
        the caller can tell "nothing to rebuild" from a projection that ran
        and changed nothing.

        Each active day is bracketed by both its midnight-adjacent moments
        (``time.min`` and ``time.max``) before :func:`observed_day_cores`, so
        every raw ``played_at`` in the day lies between the two and the
        cores' ``_ANCHOR_REACH`` reach-back covers every anchor any of them
        can hold. The span edges need no extra padding — the argument the
        contiguous tiling used to make: every anchor is some member's own
        normalized start, and the reach-back already exceeds the
        normalization clamp plus the pairing spread, so no group can anchor
        earlier than the earliest generated core.

        ``dry_run`` computes the full diff (stats identical to a real run)
        without touching the database; ``claimed_play_ids`` accumulates every
        canonical play id the projection targets so a dry-run caller can
        simulate reconciliation against the would-be state.
        """
        active_days = (
            await uow.get_connector_play_repository().get_resolved_played_at_days(
                user_id=user_id
            )
        )
        if not active_days:
            return None

        day_brackets = [
            moment
            for day in active_days
            for moment in (
                datetime.combine(day, time.min, UTC),
                datetime.combine(day, time.max, UTC),
            )
        ]
        return await self._apply_chunks(
            uow,
            observed_day_cores(day_brackets),
            user_id=user_id,
            dry_run=dry_run,
            progress_emitter=progress_emitter,
            operation_id=operation_id,
            claimed_play_ids=claimed_play_ids,
        )

    async def project_observed_days(
        self,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        played_at: Iterable[datetime],
        dry_run: bool = False,
        progress_emitter: ProgressEmitter | None = None,
        operation_id: str | None = None,
        claimed_play_ids: set[UUID] | None = None,
    ) -> dict[str, int]:
        """Project only the calendar days these observations fall in (or near).

        The batch writer's entry point: identical diff-apply semantics to
        :meth:`project_full_history`, but the chunks come from the plays
        themselves, so a sparse batch costs chunks proportional to the days it
        touched rather than to the span it happens to straddle. See
        :func:`observed_day_cores` for the ownership invariant and why a play
        claims more days than the one its own timestamp names.
        """
        return await self._apply_chunks(
            uow,
            observed_day_cores(played_at),
            user_id=user_id,
            dry_run=dry_run,
            progress_emitter=progress_emitter,
            operation_id=operation_id,
            claimed_play_ids=claimed_play_ids,
        )

    async def _apply_chunks(
        self,
        uow: UnitOfWorkProtocol,
        cores: Sequence[_ChunkCore],
        *,
        user_id: str,
        dry_run: bool,
        progress_emitter: ProgressEmitter | None,
        operation_id: str | None,
        claimed_play_ids: set[UUID] | None,
    ) -> dict[str, int]:
        """Diff-apply each core in turn, one transaction and one event apiece.

        ``operation_id`` must name an operation whose progress counter has not
        advanced past 0: the counter restarts at 1 here, and the progress ledger
        drops every event that goes backwards (see
        ``OperationLedger.validate_progress_event``). Callers give the
        projection its own (sub)operation rather than reusing a preceding
        phase's.
        """
        stats: dict[str, int] = dict.fromkeys(_STAT_KEYS, 0)

        for index, (chunk_start, chunk_end) in enumerate(cores):
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
                        total=len(cores),
                        message=(
                            "Projected plays through "
                            f"{chunk_end.astimezone(UTC).date().isoformat()}"
                        ),
                    )
                )

        logger.info(
            "Play projection complete", user_id=user_id, chunks=len(cores), **stats
        )
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
            chunk_start - _ANCHOR_REACH,
            chunk_end + _ANCHOR_REACH,
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
        stats["listening_islands_merged"] = sum(len(g.segments) for g, _ in owned)

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
            chunk_start - _ANCHOR_REACH,
            chunk_end + _ANCHOR_REACH,
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
