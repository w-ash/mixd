#!/usr/bin/env python3
"""One-shot repair: delete Spotify-import canonicals mistenanted under 'default'.

Before v0.10.2.9, ``create_track_from_spotify_data`` never set ``user_id``, so
every canonical the Spotify import created landed under the ``"default"``
tenant instead of the importing user's. The user's ``connector_plays`` ledger
rows point at those tracks via ``resolved_track_id``, and re-importing cannot
heal them: the user-scoped lookups never see the ``default``-tenant rows, so
each import creates further mistenanted duplicates.

The repair unhooks the user's ledger from the mistenanted tracks, deletes those
tracks' ``track_mappings`` explicitly, then deletes the tracks and lets
``ON DELETE CASCADE`` remove the rest of their dependents (``track_plays`` →
``play_sources``, ``track_metrics``, and the expected-≈0 tables). A subsequent
re-import (or ``backfill_stranded_connector_plays.py`` run) re-resolves the
ledger onto correctly-tenanted canonicals.

Why the mappings are DELETED, not moved (migration 051)
-------------------------------------------------------
``track_mappings.track_id`` used to be ``ON DELETE CASCADE``, so phase 3's
``delete(DBTrack)`` took the mapping rows with it silently — finding C8: prod
holds ``resolution_events`` naming superseding mappings that no longer exist,
and this script's cascade is what took them. 051 flipped the FK to
``RESTRICT``, so the same delete now raises ``ForeignKeyViolation``. That is
the fence working; phase 3 has to deal with the mappings itself.

Two options exist, and only one is honest here:

- **Move them onto another canonical.** Valid only when another canonical
  *should* own them. None does. These mistenanted tracks are the entire output
  of the buggy import; the connector tracks they map get freshly mapped onto
  correctly-tenanted canonicals by the re-import, and there is no successor
  canonical to move onto at delete time. Where a legitimate successor *does*
  exist — a real user's plays already resolve onto one of these tracks —
  the track is not a cleanup candidate at all: ``reown_cross_tenant_tracks.py``
  (or ``mixd tracks merge``, which moves mappings via
  ``merge_mappings_to_track``) is the remedy, and the phase-3 guards refuse to
  delete until one of them has run.
- **Delete them and leave their ``resolution_events`` standing as history.**
  This is what the script does, deliberately.

What C8 recorded was not "mapping rows were deleted" but "mapping rows vanished
*incidentally*, via a referential action nobody wrote, and nothing said so".
So the deletion is made explicit rather than incidental: phase 3a enumerates the
mappings across every tenant, counts and prints the ones a
``resolution_events.resulting_mapping_id`` names, and **refuses to proceed
unless ``--acknowledge-mapping-history-loss`` is passed**. The events are not
rewritten — an event is a true record of a decision even after its subject is
gone, and editing it would falsify what mixd believed at the time. The
end state is therefore known and consented to: ``events_for_mapping`` on those
ids returns the history, and the mapping row it names is gone.

Every ORM read of ``track_mappings`` here carries
``execution_options(include_superseded=True)``. ``live_rows.py``'s
``do_orm_execute`` listener scopes ORM selects of ``DBTrackMapping`` to live
rows, which is right for the application and exactly wrong for a repair whose
whole subject is retired history: without the opt-out the script would enumerate
half the blocking rows, and phase 3 would then fail on mappings it never
counted. Core ``update``/``delete`` statements are not filtered at all, so the
deletes need no such option.

Two further RESTRICT edges shape the order inside phase 3:
``superseded_by_id`` is ``RESTRICT`` too, and PostgreSQL evaluates RESTRICT
row-at-a-time — it will not accept that the referencing row is deleted by the
same statement (unlike NO ACTION). Intra-set successor pointers are therefore
NULLed first, in the same transaction as the delete. An inbound pointer from a
mapping *outside* the candidate set is a refusal instead: nulling that would
silently convert a real supersession into a retirement-with-no-replacement,
which is the exact ambiguity 051 closed.

Phase order is load-bearing: ``connector_plays.resolved_track_id`` →
``tracks.id`` is ``ON DELETE CASCADE``, and referential actions bypass RLS —
deleting the tracks first would cascade-delete the user's raw play ledger.
Resolutions are therefore reset to NULL FIRST, and the delete refuses to run
until a fresh count of still-pointing ledger rows is zero.

Deliberately kept, in full:
- ``connector_tracks`` — the shared, tenant-less provider cache; its rows are
  valid metadata regardless of which tenant triggered the fetch.
- ``resolution_events`` — append-only history recorded by value, not by FK;
  rewriting it would edit what mixd believed at the time.
- ``resolution_negatives`` ``no_match`` rows — keyed on the connector track
  (``candidate_track_id IS NULL``), so they name no mistenanted canonical and
  their backoff clocks stay honest across the re-import.

Correctness holds with and without BYPASSRLS: every mutating statement carries
an explicit ``user_id`` predicate, and each phase runs under the
``user_context`` whose tenant it touches (tracks live under ``default``, the
ledger under ``--user``), so an RLS-enforced role sees exactly the rows the
predicates name. The one deliberate exception is the phase-3 third-tenant
guard, which drops the predicate to see tenants the operator never named —
see ``_pointing_count_stmt``.

Dry-run by default — prints the full sizing report without writing. Pass
``--apply`` to execute.

Usage:
    uv run python scripts/cleanup_mistenanted_spotify_tracks.py --user <tenant>
    uv run python scripts/cleanup_mistenanted_spotify_tracks.py --user <tenant> --apply \
        --acknowledge-mapping-history-loss
"""

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from itertools import batched
from uuid import UUID

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
import typer

from src.config import get_logger, setup_script_logger
from src.config.constants import BusinessLimits
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlay,
    DBMatchReview,
    DBPlaylistAssignmentMember,
    DBPlaylistTrack,
    DBPlaySource,
    DBResolutionEvent,
    DBResolutionNegative,
    DBTrack,
    DBTrackLike,
    DBTrackMapping,
    DBTrackPlay,
    DBTrackPreference,
    DBTrackTag,
)
from src.infrastructure.persistence.database.live_rows import INCLUDE_SUPERSEDED
from src.infrastructure.persistence.database.user_context import (
    statement_timeout_context,
    user_context,
)

logger = get_logger(__name__)

# The tenant the bug wrote under — Track.user_id's silent default.
MISTENANTED_TENANT = BusinessLimits.DEFAULT_USER_ID

# When the incident window opens. The 'default' tracks created before it are
# pre-existing local-dev residue, out of scope and untouched.
#
# Prod-verified read-only 2026-08-10: the 486 'default' tracks split 424 on
# 2026-05-10 (the residue this bound exists to protect) and 62 across
# 2026-07-27 → 2026-08-06 (the mistenanted poller output). The bound was
# 2026-08-08, which sat *after* every mistenanted row — so a default run
# reported "nothing to repair" while the damage went untouched. Moved to
# 2026-07-27 on the operator's decision (2026-08-11), which is the widest
# window that still excludes all 424 residue rows.
#
# The gap between the two dates is the whole safety margin: verify the
# distribution again before moving it, because widening it far enough to
# reach 2026-05-10 would put a destructive script's default over data it was
# written to protect.
DEFAULT_WINDOW_START = "2026-07-27T00:00:00Z"

# Every IN-list in this script goes through ``itertools.batched(ids, BATCH_SIZE, strict=False)``:
# one statement per ~1,000 ids, never one per id and never one unbounded list.
BATCH_SIZE = 1_000


def parse_window_start(raw: str) -> datetime:
    """Parse the ISO-8601 window start; a naive value is read as UTC.

    ``tracks.created_at`` is timezone-aware, so an aware bound is required
    for the comparison — assuming UTC matches how every row was stamped.
    """
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def assert_all_inside_window(
    created_ats: Sequence[datetime], window_start: datetime
) -> None:
    """Hard guard: every candidate row must sit at/after the window start.

    The enumeration query already filters on ``created_at >= window``; this
    re-checks the fetched rows so a wrong query (or a mangled window
    argument) can never widen the candidate set onto the pre-existing
    'default' residue. A single stray row aborts the whole run.
    """
    stray = sum(1 for created_at in created_ats if created_at < window_start)
    if stray:
        raise RuntimeError(
            f"{stray} candidate rows predate the window start {window_start.isoformat()} "
            f"— the pre-incident 'default' residue is out of scope; aborting."
        )


async def _sum_over_batches(
    ids: Sequence[UUID], count_stmt: Callable[[Sequence[UUID]], Select[tuple[int]]]
) -> int:
    """Run one COUNT per id batch and sum — the single counting shape."""
    total = 0
    async with get_session() as session:
        for batch in batched(ids, BATCH_SIZE, strict=False):
            total += (await session.execute(count_stmt(batch))).scalar_one()
    return total


async def _enumerate_candidates(
    window_start: datetime,
) -> tuple[list[UUID], list[datetime]]:
    """Phase 1: the mistenanted track ids, with created_at for the window guard.

    Runs under the 'default' context — the rows live under that tenant.
    """
    async with get_session() as session:
        rows = (
            await session.execute(
                select(DBTrack.id, DBTrack.created_at)
                .where(
                    DBTrack.user_id == MISTENANTED_TENANT,
                    DBTrack.created_at >= window_start,
                )
                .order_by(DBTrack.created_at)
            )
        ).all()
    return [row[0] for row in rows], [row[1] for row in rows]


async def _sizing_report(user: str, track_ids: Sequence[UUID]) -> dict[str, int]:
    """Counts per table the delete would touch (or must find empty).

    Runs under the ``--user`` context: every referencing row this repair
    cares about was written by that tenant's import. ``playlist_tracks``
    carries no user_id (parent-scoped, no RLS) and is counted by track id
    alone.
    """
    report: dict[str, int] = {"tracks_to_delete": len(track_ids)}

    def _user_scoped(
        model: type, column
    ) -> Callable[[Sequence[UUID]], Select[tuple[int]]]:
        return lambda batch: (
            select(func.count())
            .select_from(model)
            .where(model.user_id == user, column.in_(batch))
        )

    cascade_tables = {
        "connector_plays_pointing": _user_scoped(
            DBConnectorPlay, DBConnectorPlay.resolved_track_id
        ),
        # ``include_superseded``: the do_orm_execute listener in
        # ``live_rows.py`` scopes every ORM select of DBTrackMapping to live
        # rows. A repair that has to account for retired history — the whole
        # point of migration 051 — must opt out, or it silently sizes only half
        # the table and phase 3 then fails on rows it never counted.
        "track_mappings": lambda batch: _user_scoped(
            DBTrackMapping, DBTrackMapping.track_id
        )(batch).execution_options(**{INCLUDE_SUPERSEDED: True}),
        # Phase 3 deletes mappings across every tenant, because RESTRICT does
        # not care whose row blocks the delete. The user-scoped count above
        # would read 0 while hundreds of 'default'-owned rows stood in the way.
        "track_mappings_all_tenants": lambda batch: (
            select(func.count())
            .select_from(DBTrackMapping)
            .where(DBTrackMapping.track_id.in_(batch))
            .execution_options(**{INCLUDE_SUPERSEDED: True})
        ),
        "track_plays": _user_scoped(DBTrackPlay, DBTrackPlay.track_id),
        "play_sources": lambda batch: (
            select(func.count())
            .select_from(DBPlaySource)
            .join(DBTrackPlay, DBPlaySource.track_play_id == DBTrackPlay.id)
            .where(
                DBPlaySource.user_id == user,
                DBTrackPlay.user_id == user,
                DBTrackPlay.track_id.in_(batch),
            )
        ),
    }
    # Dependents the import path never writes for freshly created canonicals —
    # each is expected ≈0, and a non-zero count is worth reading before --apply.
    expected_empty_tables = {
        "track_likes_expected_0": _user_scoped(DBTrackLike, DBTrackLike.track_id),
        "playlist_tracks_expected_0": lambda batch: (
            select(func.count())
            .select_from(DBPlaylistTrack)
            .where(DBPlaylistTrack.track_id.in_(batch))
        ),
        "playlist_assignment_members_expected_0": _user_scoped(
            DBPlaylistAssignmentMember, DBPlaylistAssignmentMember.track_id
        ),
        "match_reviews_expected_0": _user_scoped(DBMatchReview, DBMatchReview.track_id),
        "track_preferences_expected_0": _user_scoped(
            DBTrackPreference, DBTrackPreference.track_id
        ),
        "track_tags_expected_0": _user_scoped(DBTrackTag, DBTrackTag.track_id),
        "resolution_negatives_candidate_expected_0": _user_scoped(
            DBResolutionNegative, DBResolutionNegative.candidate_track_id
        ),
    }
    for name, count_stmt in (cascade_tables | expected_empty_tables).items():
        report[name] = await _sum_over_batches(track_ids, count_stmt)
    return report


async def _reset_resolutions(user: str, track_ids: Sequence[UUID]) -> int:
    """Phase 2: NULL the user's ledger pointers at the mistenanted tracks.

    Must complete before any track is deleted — see the module docstring on
    why the CASCADE otherwise destroys the raw ledger. Runs under the
    ``--user`` context; the explicit predicate makes the same statement exact
    under a BYPASSRLS role.

    Scope, by design: ONLY ``--user``'s pointers are reset — each tenant's
    ledger belongs to a run invoked for that tenant. Any OTHER tenant still
    pointing at a candidate track is caught by the phase-3 unpredicated
    guard, which refuses the delete and names the tenants to re-run for.
    """
    total = 0
    async with get_session() as session:
        for batch in batched(track_ids, BATCH_SIZE, strict=False):
            result = await session.execute(
                update(DBConnectorPlay)
                .where(
                    DBConnectorPlay.user_id == user,
                    DBConnectorPlay.resolved_track_id.in_(batch),
                )
                .values(resolved_track_id=None, resolved_at=None)
            )
            total += result.rowcount
    return total


def _pointing_count_stmt(
    tenant: str | None,
) -> Callable[[Sequence[UUID]], Select[tuple[int]]]:
    """COUNT of connector_plays still pointing at candidate ids.

    ``tenant`` scopes the count to one tenant's rows. ``None`` drops the
    ``user_id`` predicate entirely: this script runs as the BYPASSRLS owner,
    so the unpredicated statement counts EVERY tenant's rows — the only shape
    that can see a third tenant the operator never named. Under a future
    RLS-enforced role the same statement degrades to the ambient tenant's
    rows (i.e. the per-tenant behavior) and loses that coverage.
    """

    def _stmt(batch: Sequence[UUID]) -> Select[tuple[int]]:
        stmt = (
            select(func.count())
            .select_from(DBConnectorPlay)
            .where(DBConnectorPlay.resolved_track_id.in_(batch))
        )
        if tenant is not None:
            stmt = stmt.where(DBConnectorPlay.user_id == tenant)
        return stmt

    return _stmt


def _foreign_track_play_count_stmt(
    user: str,
) -> Callable[[Sequence[UUID]], Select[tuple[int]]]:
    """COUNT of OTHER tenants' track_plays on candidate tracks.

    ``track_plays.track_id`` is ``ON DELETE CASCADE``, so a tenant that is not
    ``--user`` and holds canonical plays on a candidate loses them silently when
    phase 3 runs — finding C3 in miniature, and the case
    ``reown_cross_tenant_tracks.py`` exists for. Phase 2 cannot help: it resets
    ``connector_plays`` pointers, and a ``track_play`` has no nullable pointer
    to reset. Unpredicated on tenant except for excluding ``--user``, so it sees
    tenants the operator never named (same BYPASSRLS dependency as
    ``_pointing_count_stmt(None)``).
    """

    def _stmt(batch: Sequence[UUID]) -> Select[tuple[int]]:
        return (
            select(func.count())
            .select_from(DBTrackPlay)
            .where(DBTrackPlay.track_id.in_(batch), DBTrackPlay.user_id != user)
        )

    return _stmt


async def _pointing_tenant_ids(track_ids: Sequence[UUID]) -> list[str]:
    """DISTINCT tenants whose plays still reference candidate ids.

    Covers both ledgers — ``connector_plays.resolved_track_id`` and
    ``track_plays.track_id`` — because both cascade from ``tracks``.
    Unpredicated like ``_pointing_count_stmt(None)``: same BYPASSRLS
    dependency, same RLS degradation. Used only to name the tenants in the
    phase-3 refusal message.
    """
    tenants: set[str] = set()
    async with get_session() as session:
        for batch in batched(track_ids, BATCH_SIZE, strict=False):
            for stmt in (
                select(DBConnectorPlay.user_id)
                .distinct()
                .where(DBConnectorPlay.resolved_track_id.in_(batch)),
                select(DBTrackPlay.user_id)
                .distinct()
                .where(DBTrackPlay.track_id.in_(batch)),
            ):
                rows = await session.execute(stmt)
                tenants.update(rows.scalars())
    return sorted(tenants)


def format_foreign_tenant_refusal(count: int, tenants: Sequence[str], user: str) -> str:
    """The phase-3 refusal when tenants beyond ``--user``/'default' still point.

    Pure so the message contract is unit-testable: it must name the tenants
    found and both remedies — the per-tenant re-run (phase 2 resets only
    ``--user``'s pointers by design) and the re-own script, which is the right
    answer when the pointing rows are a real user's plays that legitimately
    depend on the track rather than another mistenanted import's residue.
    """
    return (
        f"{count} play rows still point at candidate tracks under "
        f"tenants {list(tenants)} after resetting {user!r}'s — phase 2 resets "
        f"only --user's connector_plays pointers by design, so deleting now "
        f"would cascade-delete those tenants' play ledgers. Two remedies, and "
        f"which one applies depends on whose rows they are: if a listed tenant "
        f"is a real user whose plays legitimately resolve onto these tracks, "
        f"run scripts/reown_cross_tenant_tracks.py --user <that tenant> "
        f"--apply, which re-owns the tracks onto them and takes them out of "
        f"this candidate set entirely; if the rows are another mistenanted "
        f"import's residue, re-run this script with --user <tenant> --apply "
        f"for each tenant listed. Then retry this run. Aborting before phase 3."
    )


def format_mapping_history_refusal(
    mapping_count: int, event_named: Sequence[UUID]
) -> str:
    """The phase-3a refusal when mapping history would be deleted unconsented.

    Pure so the contract is unit-testable: it must state that the rows are
    deleted (not moved), name how many ``resolution_events`` rows are left
    pointing at nothing, and name the flag that consents to it.
    """
    shown = ", ".join(str(mapping_id) for mapping_id in event_named[:5])
    elided = len(event_named) - 5
    suffix = f" (+{elided} more)" if elided > 0 else ""
    return (
        f"Phase 3 would delete {mapping_count} track_mappings rows outright — "
        f"there is no successor canonical to move them onto (see the module "
        f"docstring). {len(event_named)} of them are named by a "
        f"resolution_events.resulting_mapping_id: {shown}{suffix}. Those events "
        f"are deliberately left standing as history and will name mapping ids "
        f"that no longer resolve. That is the documented outcome, not an "
        f"accident — pass --acknowledge-mapping-history-loss to consent to it. "
        f"Aborting before phase 3."
    )


def format_external_supersession_refusal(referrers: Sequence[UUID]) -> str:
    """The phase-3a refusal when an outside mapping supersedes into the set.

    Pure for the same reason. Nulling such a pointer would turn a real
    supersession into a retirement-with-no-replacement, which migration 051
    exists to make impossible.
    """
    shown = ", ".join(str(mapping_id) for mapping_id in referrers[:5])
    elided = len(referrers) - 5
    suffix = f" (+{elided} more)" if elided > 0 else ""
    return (
        f"{len(referrers)} track_mappings rows OUTSIDE the candidate set point "
        f"at a candidate track's mapping via superseded_by_id: {shown}{suffix}. "
        f"Deleting the successor would either fail on the RESTRICT FK or, if "
        f"the pointer were NULLed, silently downgrade a real supersession to a "
        f"retirement with no replacement. Neither is acceptable — resolve those "
        f"chains by hand first. Aborting before phase 3."
    )


async def candidate_mapping_ids(
    session: AsyncSession, track_ids: Sequence[UUID]
) -> list[UUID]:
    """Phase 3a: every mapping on a candidate track, across every tenant.

    Deliberately unpredicated on ``user_id``: RESTRICT does not care which
    tenant owns the blocking row, and in production these mappings sit under
    'default' while ``--user`` is someone else. Same BYPASSRLS dependency as
    ``_pointing_count_stmt(None)`` — under an RLS-enforced role this would see
    only the ambient tenant's rows and the delete would then fail loudly on the
    ones it could not see, which is the safe direction to degrade in.
    """
    mapping_ids: list[UUID] = []
    for batch in batched(track_ids, BATCH_SIZE, strict=False):
        rows = await session.execute(
            select(DBTrackMapping.id)
            .where(DBTrackMapping.track_id.in_(batch))
            .execution_options(**{INCLUDE_SUPERSEDED: True})
        )
        mapping_ids.extend(rows.scalars())
    return mapping_ids


async def events_naming_mappings(
    session: AsyncSession, mapping_ids: Sequence[UUID]
) -> list[UUID]:
    """DISTINCT mapping ids that a ``resolution_events`` row names.

    ``resolution_events`` has no foreign keys by design, so nothing in the
    database stops the delete — this query is the only thing that can see the
    history about to be orphaned.
    """
    named: set[UUID] = set()
    for batch in batched(mapping_ids, BATCH_SIZE, strict=False):
        rows = await session.execute(
            select(DBResolutionEvent.resulting_mapping_id)
            .distinct()
            .where(DBResolutionEvent.resulting_mapping_id.in_(batch))
        )
        named.update(row for row in rows.scalars() if row is not None)
    return sorted(named)


async def external_supersession_referrers(
    session: AsyncSession, mapping_ids: Sequence[UUID]
) -> list[UUID]:
    """Mappings outside the candidate set that supersede into it."""
    inside = set(mapping_ids)
    referrers: set[UUID] = set()
    for batch in batched(mapping_ids, BATCH_SIZE, strict=False):
        rows = await session.execute(
            select(DBTrackMapping.id)
            .where(DBTrackMapping.superseded_by_id.in_(batch))
            .execution_options(**{INCLUDE_SUPERSEDED: True})
        )
        referrers.update(row for row in rows.scalars() if row not in inside)
    return sorted(referrers)


async def delete_mappings_and_tracks(
    session: AsyncSession,
    track_ids: Sequence[UUID],
    mapping_ids: Sequence[UUID],
    window_start: datetime,
) -> tuple[int, int]:
    """Phase 3: delete the candidate mappings, then the tracks they blocked.

    One session, therefore one transaction: a failure on the track DELETE must
    not leave the mappings gone. Order inside it is forced by the RESTRICT FKs:

    1. NULL ``superseded_by_id`` on the candidate rows. PostgreSQL evaluates
       RESTRICT row-at-a-time and will not accept that the referencing row is
       being deleted by the same statement, so an intra-set chain would
       otherwise abort the delete. Callers must have already refused on
       *external* referrers — nulling one of those would destroy information.
    2. DELETE the mappings — explicitly, not as a cascade. This is the C8 loss
       made deliberate and consented to; see the module docstring.
    3. DELETE the tracks, with the tenant and window predicates repeated so
       even a stale id list cannot reach outside the enumerated population.
       Everything else still cascades (``track_plays`` → ``play_sources``,
       ``track_metrics``, ``match_reviews``, ``resolution_negatives``).
    """
    mappings_deleted = 0
    tracks_deleted = 0
    for batch in batched(mapping_ids, BATCH_SIZE, strict=False):
        _ = await session.execute(
            update(DBTrackMapping)
            .where(
                DBTrackMapping.id.in_(batch),
                DBTrackMapping.superseded_by_id.is_not(None),
            )
            .values(superseded_by_id=None)
        )
    for batch in batched(mapping_ids, BATCH_SIZE, strict=False):
        result = await session.execute(
            delete(DBTrackMapping).where(DBTrackMapping.id.in_(batch))
        )
        mappings_deleted += result.rowcount
    for batch in batched(track_ids, BATCH_SIZE, strict=False):
        result = await session.execute(
            delete(DBTrack).where(
                DBTrack.user_id == MISTENANTED_TENANT,
                DBTrack.created_at >= window_start,
                DBTrack.id.in_(batch),
            )
        )
        tracks_deleted += result.rowcount
    return mappings_deleted, tracks_deleted


async def _run(
    *,
    user: str,
    window_start: datetime,
    apply: bool,
    acknowledge_mapping_history_loss: bool,
) -> None:
    setup_script_logger("cleanup_mistenanted_spotify_tracks")

    # Phase 1 — enumerate + window guard (tenant: default).
    with user_context(MISTENANTED_TENANT):
        track_ids, created_ats = await _enumerate_candidates(window_start)
    assert_all_inside_window(created_ats, window_start)
    logger.info(
        "Mistenanted tracks enumerated",
        count=len(track_ids),
        window_start=window_start.isoformat(),
        user_id=user,
        mode="APPLY" if apply else "DRY-RUN",
    )
    if not track_ids:
        logger.info("Nothing to repair — no 'default' tracks inside the window.")
        return

    # Sizing report (tenant: --user) — printed on every run, dry or applied.
    with user_context(user):
        report = await _sizing_report(user, track_ids)
    logger.info("Sizing report", **report)

    # Phase 3a — mapping accounting, BEFORE phase 2 mutates anything. Migration
    # 051 made these rows a hard blocker, and the operator has to consent to
    # their deletion; discovering that after the ledger reset would leave the
    # database half-repaired for no reason.
    async with get_session() as session:
        mapping_ids = await candidate_mapping_ids(session, track_ids)
        event_named = (
            await events_naming_mappings(session, mapping_ids) if mapping_ids else []
        )
        external_referrers = (
            await external_supersession_referrers(session, mapping_ids)
            if mapping_ids
            else []
        )
    logger.info(
        "Phase 3a: mapping accounting",
        mappings_to_delete=len(mapping_ids),
        mappings_named_by_resolution_events=len(event_named),
        external_supersession_referrers=len(external_referrers),
        acknowledged=acknowledge_mapping_history_loss,
    )

    if not apply:
        logger.info("Dry-run only. Re-run with --apply to execute the repair.")
        if event_named and not acknowledge_mapping_history_loss:
            logger.warning(
                "--apply will additionally require --acknowledge-mapping-history-loss",
                reason=format_mapping_history_refusal(len(mapping_ids), event_named),
            )
        return

    if external_referrers:
        raise RuntimeError(format_external_supersession_refusal(external_referrers))
    if event_named and not acknowledge_mapping_history_loss:
        raise RuntimeError(
            format_mapping_history_refusal(len(mapping_ids), event_named)
        )

    # Phase 2 — reset resolutions FIRST (tenant: --user). Load-bearing order:
    # deleting tracks first would CASCADE into the user's raw ledger.
    with user_context(user):
        reset = await _reset_resolutions(user, track_ids)
    logger.info("Phase 2 complete: ledger pointers reset", connector_plays_reset=reset)

    # Phase 3 guard — refuse to delete while ANY ledger row still points at a
    # candidate. Checked per tenant under that tenant's context so the guard
    # is exact for an RLS-enforced role and merely redundant for BYPASSRLS.
    for tenant in (user, MISTENANTED_TENANT):
        with user_context(tenant):
            still_pointing = await _sum_over_batches(
                track_ids, _pointing_count_stmt(tenant)
            )
        if still_pointing:
            raise RuntimeError(
                f"{still_pointing} connector_plays rows under tenant {tenant!r} "
                f"still point at candidate tracks after the reset — deleting now "
                f"would cascade-delete them. Aborting before phase 3."
            )

    # Third-tenant guard — ONE tenant-unpredicated count per ledger. The
    # per-tenant checks above are blind to any tenant the operator never named,
    # yet the CASCADE ignores tenancy and would delete that tenant's play rows
    # all the same. ``track_plays`` is included because phase 2 has no pointer
    # to reset there and a foreign tenant's canonical plays are precisely
    # finding C3 — see ``_foreign_track_play_count_stmt``.
    with user_context(user):
        foreign_pointing = await _sum_over_batches(
            track_ids, _pointing_count_stmt(None)
        ) + await _sum_over_batches(track_ids, _foreign_track_play_count_stmt(user))
        tenants = await _pointing_tenant_ids(track_ids) if foreign_pointing else []
    if foreign_pointing:
        raise RuntimeError(
            format_foreign_tenant_refusal(foreign_pointing, tenants, user)
        )

    # Phase 3 — delete the mappings explicitly, then the tracks (tenant:
    # default); CASCADE clears everything that is not a mapping.
    with user_context(MISTENANTED_TENANT):
        async with get_session() as session:
            mappings_deleted, deleted = await delete_mappings_and_tracks(
                session, track_ids, mapping_ids, window_start
            )
    logger.info(
        "Phase 3 complete: mappings and mistenanted tracks deleted",
        mappings_deleted=mappings_deleted,
        mappings_expected=len(mapping_ids),
        mapping_history_orphaned=len(event_named),
        tracks_deleted=deleted,
        expected=len(track_ids),
    )
    if deleted != len(track_ids):
        logger.warning(
            "Deleted count differs from enumerated count — rows changed between "
            "enumeration and delete; re-run to converge.",
            tracks_deleted=deleted,
            enumerated=len(track_ids),
        )


def main(
    user: str = typer.Option(
        ...,
        help="The tenant whose Spotify import created the mistenanted tracks — "
        "their connector_plays ledger is the one unhooked in phase 2.",
    ),
    window_start: str = typer.Option(
        default=DEFAULT_WINDOW_START,
        help="Only 'default' tracks created at/after this instant are touched "
        "(ISO-8601; naive values read as UTC).",
    ),
    apply: bool = typer.Option(
        default=False,
        help="Execute the repair. Without it the script only prints the sizing report.",
    ),
    acknowledge_mapping_history_loss: bool = typer.Option(
        default=False,
        help="Consent to phase 3 deleting the candidate tracks' track_mappings "
        "outright, leaving any resolution_events that name them standing as "
        "history with an unresolvable mapping id. Required only when such "
        "events exist.",
    ),
) -> None:
    """Repair mistenanted Spotify-import tracks (dry-run unless --apply)."""
    # Phase 2's batched UPDATE and phase 3's cascading DELETE both outrun the
    # 30s connection default the way a bulk import does.
    with statement_timeout_context(BusinessLimits.BULK_IMPORT_STATEMENT_TIMEOUT):
        asyncio.run(
            _run(
                user=user,
                window_start=parse_window_start(window_start),
                apply=apply,
                acknowledge_mapping_history_loss=acknowledge_mapping_history_loss,
            )
        )


if __name__ == "__main__":
    typer.run(main)
