#!/usr/bin/env python3
"""One-shot repair: delete Spotify-import canonicals mistenanted under 'default'.

Before v0.10.2.9, ``create_track_from_spotify_data`` never set ``user_id``, so
every canonical the Spotify import created landed under the ``"default"``
tenant instead of the importing user's. The user's ``connector_plays`` ledger
rows point at those tracks via ``resolved_track_id``, and re-importing cannot
heal them: the user-scoped lookups never see the ``default``-tenant rows, so
each import creates further mistenanted duplicates.

The repair unhooks the user's ledger from the mistenanted tracks, then deletes
the tracks and lets ``ON DELETE CASCADE`` remove their dependents
(``track_mappings``, ``track_plays`` → ``play_sources``, and the expected-≈0
tables). A subsequent re-import (or ``backfill_stranded_connector_plays.py``
run) re-resolves the ledger onto correctly-tenanted canonicals.

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
    uv run python scripts/cleanup_mistenanted_spotify_tracks.py --user <tenant> --apply
"""

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from itertools import batched
from uuid import UUID

from sqlalchemy import Select, delete, func, select, update
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
    DBResolutionNegative,
    DBTrack,
    DBTrackLike,
    DBTrackMapping,
    DBTrackPlay,
    DBTrackPreference,
    DBTrackTag,
)
from src.infrastructure.persistence.database.user_context import (
    statement_timeout_context,
    user_context,
)

logger = get_logger(__name__)

# The tenant the bug wrote under — Track.user_id's silent default.
MISTENANTED_TENANT = BusinessLimits.DEFAULT_USER_ID

# When the incident window opens. The 457 'default' tracks created before it
# are pre-existing local-dev residue, out of scope and untouched.
DEFAULT_WINDOW_START = "2026-08-08T00:00:00Z"

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
        "track_mappings": _user_scoped(DBTrackMapping, DBTrackMapping.track_id),
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


async def _pointing_tenant_ids(track_ids: Sequence[UUID]) -> list[str]:
    """DISTINCT tenants whose connector_plays still point at candidate ids.

    Unpredicated like ``_pointing_count_stmt(None)`` — same BYPASSRLS
    dependency, same RLS degradation. Used only to name the tenants in the
    phase-3 refusal message.
    """
    tenants: set[str] = set()
    async with get_session() as session:
        for batch in batched(track_ids, BATCH_SIZE, strict=False):
            rows = await session.execute(
                select(DBConnectorPlay.user_id)
                .distinct()
                .where(DBConnectorPlay.resolved_track_id.in_(batch))
            )
            tenants.update(rows.scalars())
    return sorted(tenants)


def format_foreign_tenant_refusal(count: int, tenants: Sequence[str], user: str) -> str:
    """The phase-3 refusal when tenants beyond ``--user``/'default' still point.

    Pure so the message contract is unit-testable: it must name the tenants
    found and tell the operator the per-tenant re-run remedy (phase 2 resets
    only ``--user``'s pointers by design).
    """
    return (
        f"{count} connector_plays rows still point at candidate tracks under "
        f"tenants {list(tenants)} after resetting {user!r}'s — phase 2 resets "
        f"only --user's pointers by design, so deleting now would "
        f"cascade-delete those tenants' raw play ledgers. Re-run this script "
        f"with --user <tenant> --apply for each other tenant listed, then "
        f"retry this run. Aborting before phase 3."
    )


async def _delete_tracks(track_ids: Sequence[UUID], window_start: datetime) -> int:
    """Phase 3: delete the mistenanted tracks; CASCADE removes dependents.

    Runs under the 'default' context. The tenant and window predicates are
    repeated on the DELETE itself so that even a stale id list cannot reach
    outside the enumerated population.
    """
    total = 0
    async with get_session() as session:
        for batch in batched(track_ids, BATCH_SIZE, strict=False):
            result = await session.execute(
                delete(DBTrack).where(
                    DBTrack.user_id == MISTENANTED_TENANT,
                    DBTrack.created_at >= window_start,
                    DBTrack.id.in_(batch),
                )
            )
            total += result.rowcount
    return total


async def _run(*, user: str, window_start: datetime, apply: bool) -> None:
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

    if not apply:
        logger.info("Dry-run only. Re-run with --apply to execute the repair.")
        return

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

    # Third-tenant guard — ONE tenant-unpredicated count. The per-tenant
    # checks above are blind to any tenant the operator never named, yet the
    # CASCADE ignores tenancy and would delete that tenant's ledger rows all
    # the same. See ``_pointing_count_stmt`` for the BYPASSRLS dependency and
    # the RLS-enforced degradation to per-tenant behavior.
    with user_context(user):
        foreign_pointing = await _sum_over_batches(
            track_ids, _pointing_count_stmt(None)
        )
        tenants = await _pointing_tenant_ids(track_ids) if foreign_pointing else []
    if foreign_pointing:
        raise RuntimeError(
            format_foreign_tenant_refusal(foreign_pointing, tenants, user)
        )

    # Phase 3 — delete (tenant: default); CASCADE clears dependents.
    with user_context(MISTENANTED_TENANT):
        deleted = await _delete_tracks(track_ids, window_start)
    logger.info(
        "Phase 3 complete: mistenanted tracks deleted",
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
) -> None:
    """Repair mistenanted Spotify-import tracks (dry-run unless --apply)."""
    # Phase 2's batched UPDATE and phase 3's cascading DELETE both outrun the
    # 30s connection default the way a bulk import does.
    with statement_timeout_context(BusinessLimits.BULK_IMPORT_STATEMENT_TIMEOUT):
        asyncio.run(
            _run(user=user, window_start=parse_window_start(window_start), apply=apply)
        )


if __name__ == "__main__":
    typer.run(main)
