"""The two mistenancy repair scripts' mutating paths, against a real database.

Both scripts turn on constraints, not logic, so a mocked test would prove
nothing:

- ``reown_cross_tenant_tracks.py`` moves a track's ``user_id`` and has to take
  the track's identity/provider rows with it. Whether a mapping left behind is
  a problem is a fact about ``uq_track_mappings_live_connector``, which only
  exists in the database.
- ``cleanup_mistenanted_spotify_tracks.py`` phase 3 previously leaned on
  ``ON DELETE CASCADE``. Migration 051 made ``track_mappings.track_id``
  ``RESTRICT`` (finding C8), so the delete's *order* is now the correctness
  property — and a RESTRICT that PostgreSQL evaluates row-at-a-time is
  precisely what a fake can't reproduce.

Integration tests build the schema with ``metadata.create_all``, which mirrors
migration 051's referential actions — the first test here asserts that directly,
so a divergence surfaces as a named failure rather than as every other test
quietly passing against a schema production never has.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.cleanup_mistenanted_spotify_tracks import (
    candidate_mapping_ids,
    delete_mappings_and_tracks,
    events_naming_mappings,
    external_supersession_referrers,
)
from scripts.reown_cross_tenant_tracks import (
    MergePair,
    assert_nothing_will_cascade,
    assert_pair_still_valid,
    discover,
    discover_merge_pairs,
    merge_pair,
    reown,
)
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlay,
    DBConnectorTrack,
    DBMatchReview,
    DBPlaySource,
    DBResolutionEvent,
    DBTrack,
    DBTrackMapping,
    DBTrackMetric,
    DBTrackPlay,
)
from src.infrastructure.persistence.database.live_rows import INCLUDE_SUPERSEDED
from src.infrastructure.persistence.repositories.factories import get_unit_of_work

_OWNER = "default"
_USER = "TEST_user_cfe9d062"
_THIRD = "TEST_user_3a71f0c4"
_WINDOW = datetime(2026, 8, 1, tzinfo=UTC)
_CREATED = _WINDOW + timedelta(days=2)
_PLAYED_AT = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


async def _connector_track(session: AsyncSession, *, suffix: str) -> DBConnectorTrack:
    connector_track = DBConnectorTrack(
        connector_name="spotify",
        connector_track_identifier=f"TEST_ct_{suffix}_{uuid4().hex[:8]}",
        title=f"TEST Track {suffix}",
        artists={"names": ["TEST Artist"]},
        raw_metadata={},
    )
    session.add(connector_track)
    await session.flush()
    return connector_track


async def _track(
    session: AsyncSession,
    *,
    user_id: str,
    suffix: str,
    isrc: str | None = None,
    normalized: str | None = None,
    duration_ms: int | None = 200_000,
) -> DBTrack:
    track = DBTrack(
        user_id=user_id,
        title=f"TEST Track {suffix}",
        artists={"names": ["TEST Artist"]},
        artists_text="TEST Artist",
        duration_ms=duration_ms,
        isrc=isrc,
        title_normalized=normalized,
        artist_normalized="test artist" if normalized else None,
        created_at=_CREATED,
    )
    session.add(track)
    await session.flush()
    return track


async def _mapping(
    session: AsyncSession,
    *,
    user_id: str,
    track: DBTrack,
    connector_track: DBConnectorTrack,
    is_primary: bool = True,
    superseded_by: DBTrackMapping | None = None,
) -> DBTrackMapping:
    mapping = DBTrackMapping(
        user_id=user_id,
        track_id=track.id,
        connector_track_id=connector_track.id,
        connector_name="spotify",
        match_method="isrc",
        confidence=95,
        is_primary=is_primary,
        superseded_by_id=None if superseded_by is None else superseded_by.id,
        superseded_at=None if superseded_by is None else _CREATED,
        supersession_reason=None if superseded_by is None else "reassertion",
    )
    session.add(mapping)
    await session.flush()
    return mapping


async def _depend(session: AsyncSession, *, user_id: str, track: DBTrack) -> None:
    """Give ``user_id`` one canonical play and one ledger play on ``track``."""
    session.add(
        DBTrackPlay(
            user_id=user_id,
            track_id=track.id,
            service="spotify",
            played_at=_PLAYED_AT,
            ms_played=200_000,
        )
    )
    session.add(
        DBConnectorPlay(
            user_id=user_id,
            connector_name="spotify",
            connector_track_identifier=f"TEST_cp_{uuid4().hex[:8]}",
            played_at=_PLAYED_AT,
            raw_metadata={},
            resolved_track_id=track.id,
            resolved_at=_PLAYED_AT,
        )
    )
    await session.flush()


async def _owner_of(session: AsyncSession, track_id: UUID) -> str | None:
    return (
        await session.execute(select(DBTrack.user_id).where(DBTrack.id == track_id))
    ).scalar_one_or_none()


class TestRestrictIsLiveInTheTestSchema:
    """The premise every other test here rests on."""

    async def test_deleting_a_track_with_mappings_raises(
        self, db_session: AsyncSession
    ):
        """Migration 051's fence, as ``metadata.create_all`` builds it.

        Before 051 this DELETE succeeded and took the mapping with it — that is
        finding C8, and this assertion is what stops it coming back.
        """
        track = await _track(db_session, user_id=_OWNER, suffix="restrict")
        connector_track = await _connector_track(db_session, suffix="restrict")
        _ = await _mapping(
            db_session, user_id=_OWNER, track=track, connector_track=connector_track
        )

        savepoint = await db_session.begin_nested()
        with pytest.raises(IntegrityError, match="track_mappings"):
            _ = await db_session.execute(delete(DBTrack).where(DBTrack.id == track.id))
        await savepoint.rollback()


class TestReownMovesTheTrackAndWhatBelongsToIt:
    async def test_track_mappings_and_metrics_all_change_owner(
        self, db_session: AsyncSession
    ):
        track = await _track(db_session, user_id=_OWNER, suffix="move")
        connector_track = await _connector_track(db_session, suffix="move")
        live = await _mapping(
            db_session, user_id=_OWNER, track=track, connector_track=connector_track
        )
        retired = await _mapping(
            db_session,
            user_id=_OWNER,
            track=track,
            connector_track=await _connector_track(db_session, suffix="move_old"),
            is_primary=False,
            superseded_by=live,
        )
        metric = DBTrackMetric(
            user_id=_OWNER,
            track_id=track.id,
            connector_name="spotify",
            metric_type="popularity",
            value=42.0,
        )
        db_session.add(metric)
        await _depend(db_session, user_id=_USER, track=track)

        counts = await reown(db_session, user=_USER, owner=_OWNER, track_ids=[track.id])
        await db_session.flush()

        assert counts["tracks"] == 1
        assert counts["track_mappings"] == 2
        assert counts["track_metrics"] == 1
        assert await _owner_of(db_session, track.id) == _USER
        owners = (
            await db_session.execute(
                select(DBTrackMapping.user_id)
                .where(DBTrackMapping.id.in_([live.id, retired.id]))
                .execution_options(**{INCLUDE_SUPERSEDED: True})
            )
        ).scalars()
        assert set(owners) == {_USER}

    async def test_retired_mappings_travel_with_their_successor(
        self, db_session: AsyncSession
    ):
        """A supersession chain split across two tenants is C8 in slow motion:
        the history survives but no single tenant can read all of it."""
        track = await _track(db_session, user_id=_OWNER, suffix="chain")
        live = await _mapping(
            db_session,
            user_id=_OWNER,
            track=track,
            connector_track=await _connector_track(db_session, suffix="chain_new"),
        )
        retired = await _mapping(
            db_session,
            user_id=_OWNER,
            track=track,
            connector_track=await _connector_track(db_session, suffix="chain_old"),
            is_primary=False,
            superseded_by=live,
        )
        await _depend(db_session, user_id=_USER, track=track)

        _ = await reown(db_session, user=_USER, owner=_OWNER, track_ids=[track.id])
        await db_session.flush()

        still_linked = (
            await db_session.execute(
                select(DBTrackMapping.superseded_by_id)
                .where(DBTrackMapping.id == retired.id)
                .execution_options(**{INCLUDE_SUPERSEDED: True})
            )
        ).scalar_one()
        assert still_linked == live.id

    async def test_the_users_own_plays_are_left_exactly_where_they_are(
        self, db_session: AsyncSession
    ):
        """Re-owning is about the track, not about anyone's listening history."""
        track = await _track(db_session, user_id=_OWNER, suffix="plays")
        await _depend(db_session, user_id=_USER, track=track)

        _ = await reown(db_session, user=_USER, owner=_OWNER, track_ids=[track.id])
        await db_session.flush()

        play_owners = (
            await db_session.execute(
                select(DBTrackPlay.user_id).where(DBTrackPlay.track_id == track.id)
            )
        ).scalars()
        assert set(play_owners) == {_USER}


class TestReownDiscoveryAndRefusals:
    async def test_a_cross_tenant_track_is_discovered_with_its_companions(
        self, db_session: AsyncSession
    ):
        track = await _track(db_session, user_id=_OWNER, suffix="discover")
        _ = await _mapping(
            db_session,
            user_id=_OWNER,
            track=track,
            connector_track=await _connector_track(db_session, suffix="discover"),
        )
        await _depend(db_session, user_id=_USER, track=track)

        found = await discover(db_session, user=_USER, from_tenant=_OWNER)

        assert [t.track_id for t in found] == [track.id]
        assert found[0].blockers == ()
        assert found[0].dep_track_plays == 1
        assert found[0].dep_connector_plays == 1
        assert found[0].moving["mappings"] == 1

    async def test_an_isrc_the_target_already_owns_refuses_the_move(
        self, db_session: AsyncSession
    ):
        """``uq_tracks_user_isrc`` would fire — this is a merge, not a re-own."""
        isrc = f"TEST{uuid4().hex[:8].upper()}"
        mine = await _track(db_session, user_id=_USER, suffix="mine", isrc=isrc)
        theirs = await _track(db_session, user_id=_OWNER, suffix="theirs", isrc=isrc)
        await _depend(db_session, user_id=_USER, track=theirs)

        found = await discover(db_session, user=_USER, from_tenant=_OWNER)

        assert len(found) == 1
        reasons = [reason for reason, _ in found[0].blockers]
        assert any("ISRC" in reason for reason in reasons)
        assert found[0].blockers[0][1] == mine.id

    async def test_a_normalization_equal_track_refuses_without_any_shared_id(
        self, db_session: AsyncSession
    ):
        """No constraint would fire here; re-owning would just make a duplicate."""
        normalized = f"test track {uuid4().hex[:8]}"
        _ = await _track(
            db_session, user_id=_USER, suffix="norm_mine", normalized=normalized
        )
        theirs = await _track(
            db_session, user_id=_OWNER, suffix="norm_theirs", normalized=normalized
        )
        await _depend(db_session, user_id=_USER, track=theirs)

        found = await discover(db_session, user=_USER, from_tenant=_OWNER)

        reasons = [reason for reason, _ in found[0].blockers]
        assert any("normalization-equal" in reason for reason in reasons)

    async def test_a_track_the_user_already_owns_is_not_discovered(
        self, db_session: AsyncSession
    ):
        track = await _track(db_session, user_id=_USER, suffix="own")
        await _depend(db_session, user_id=_USER, track=track)

        assert await discover(db_session, user=_USER, from_tenant=None) == []


class TestCleanupPhaseThreeUnderRestrict:
    async def test_mappings_are_deleted_first_so_the_track_delete_succeeds(
        self, db_session: AsyncSession
    ):
        """The regression the RESTRICT flip caused, and its fix.

        The chain is deliberately intra-set: ``superseded_by_id`` is RESTRICT
        too, and PostgreSQL will not accept that the referencing row is deleted
        by the same statement — so without the NULLing step this raises even
        after the mappings are handled.
        """
        track = await _track(db_session, user_id=_OWNER, suffix="phase3")
        live = await _mapping(
            db_session,
            user_id=_OWNER,
            track=track,
            connector_track=await _connector_track(db_session, suffix="phase3_new"),
        )
        retired = await _mapping(
            db_session,
            user_id=_OWNER,
            track=track,
            connector_track=await _connector_track(db_session, suffix="phase3_old"),
            is_primary=False,
            superseded_by=live,
        )

        mapping_ids = await candidate_mapping_ids(db_session, [track.id])
        assert set(mapping_ids) == {live.id, retired.id}

        mappings_deleted, tracks_deleted = await delete_mappings_and_tracks(
            db_session, [track.id], mapping_ids, _WINDOW
        )

        assert (mappings_deleted, tracks_deleted) == (2, 1)
        assert await _owner_of(db_session, track.id) is None

    async def test_retired_mappings_are_enumerated_despite_the_live_rows_filter(
        self, db_session: AsyncSession
    ):
        """``live_rows.py`` hides superseded rows from every ORM select.

        Right for the application, fatal here: an enumeration that sees only
        live rows leaves the retired ones behind, and phase 3's track DELETE
        then fails on RESTRICT against rows the script never counted. The
        opt-out is ``execution_options(include_superseded=True)``.
        """
        track = await _track(db_session, user_id=_OWNER, suffix="livefilter")
        live = await _mapping(
            db_session,
            user_id=_OWNER,
            track=track,
            connector_track=await _connector_track(db_session, suffix="lf_new"),
        )
        retired = await _mapping(
            db_session,
            user_id=_OWNER,
            track=track,
            connector_track=await _connector_track(db_session, suffix="lf_old"),
            is_primary=False,
            superseded_by=live,
        )

        unfiltered = (
            await db_session.execute(
                select(DBTrackMapping.id).where(DBTrackMapping.track_id == track.id)
            )
        ).scalars()
        assert set(unfiltered) == {live.id}, "premise: the filter is active"

        assert set(await candidate_mapping_ids(db_session, [track.id])) == {
            live.id,
            retired.id,
        }

    async def test_resolution_events_naming_a_doomed_mapping_are_found_and_kept(
        self, db_session: AsyncSession
    ):
        """The documented decision: the mapping goes, the event stays.

        ``resolution_events`` carries no FK, so nothing in the database would
        have told the operator — the enumeration is the only thing that can.
        """
        track = await _track(db_session, user_id=_OWNER, suffix="events")
        mapping = await _mapping(
            db_session,
            user_id=_OWNER,
            track=track,
            connector_track=await _connector_track(db_session, suffix="events"),
        )
        event = DBResolutionEvent(
            user_id=_OWNER,
            event_type="track_resolved",
            matcher_version="test",
            track_id=track.id,
            resulting_mapping_id=mapping.id,
            payload={},
        )
        db_session.add(event)
        await db_session.flush()

        mapping_ids = await candidate_mapping_ids(db_session, [track.id])
        named = await events_naming_mappings(db_session, mapping_ids)
        assert named == [mapping.id]

        _ = await delete_mappings_and_tracks(
            db_session, [track.id], mapping_ids, _WINDOW
        )

        surviving = (
            await db_session.execute(
                select(DBResolutionEvent.resulting_mapping_id).where(
                    DBResolutionEvent.id == event.id
                )
            )
        ).scalar_one()
        assert surviving == mapping.id

    async def test_a_supersession_pointer_from_outside_the_set_is_reported(
        self, db_session: AsyncSession
    ):
        """Nulling this one would downgrade a real supersession to a
        retirement-with-no-replacement — so it is a refusal, not a fixup."""
        candidate = await _track(db_session, user_id=_OWNER, suffix="ext_candidate")
        survivor = await _track(db_session, user_id=_USER, suffix="ext_survivor")
        successor = await _mapping(
            db_session,
            user_id=_OWNER,
            track=candidate,
            connector_track=await _connector_track(db_session, suffix="ext_new"),
        )
        outsider = await _mapping(
            db_session,
            user_id=_USER,
            track=survivor,
            connector_track=await _connector_track(db_session, suffix="ext_old"),
            is_primary=False,
            superseded_by=successor,
        )

        mapping_ids = await candidate_mapping_ids(db_session, [candidate.id])
        referrers = await external_supersession_referrers(db_session, mapping_ids)

        assert referrers == [outsider.id]

    async def test_an_intra_set_chain_reports_no_external_referrer(
        self, db_session: AsyncSession
    ):
        track = await _track(db_session, user_id=_OWNER, suffix="intra")
        live = await _mapping(
            db_session,
            user_id=_OWNER,
            track=track,
            connector_track=await _connector_track(db_session, suffix="intra_new"),
        )
        _ = await _mapping(
            db_session,
            user_id=_OWNER,
            track=track,
            connector_track=await _connector_track(db_session, suffix="intra_old"),
            is_primary=False,
            superseded_by=live,
        )

        mapping_ids = await candidate_mapping_ids(db_session, [track.id])

        assert await external_supersession_referrers(db_session, mapping_ids) == []


async def _merge_scenario(
    session: AsyncSession, *, suffix: str, isrc: str | None = None
) -> tuple[DBTrack, DBTrack, DBConnectorTrack]:
    """The prod shape: the user owns one track, ``default`` owns its twin.

    Both hold a live mapping on the *same* connector track — legal today,
    because ``uq_track_mappings_live_connector`` leads with ``user_id`` — and
    the user's plays hang off the foreign row.
    """
    isrc = isrc or f"TEST{uuid4().hex[:8].upper()}"
    normalized = f"test track {suffix}"
    connector_track = await _connector_track(session, suffix=suffix)
    winner = await _track(
        session, user_id=_USER, suffix=suffix, isrc=isrc, normalized=normalized
    )
    loser = await _track(
        session, user_id=_OWNER, suffix=suffix, isrc=isrc, normalized=normalized
    )
    for track_id, owner in ((winner, _USER), (loser, _OWNER)):
        _ = await _mapping(
            session, user_id=owner, track=track_id, connector_track=connector_track
        )
    await _depend(session, user_id=_USER, track=loser)
    return winner, loser, connector_track


async def _only_pair(session: AsyncSession, loser: DBTrack) -> MergePair:
    pairs = await discover_merge_pairs(session, user=_USER, from_tenant=_OWNER)
    return next(pair for pair in pairs if pair.loser.track_id == loser.id)


class TestWhatTheMergeServiceMoves:
    """The finding merge mode was built around, and the fix that closed it."""

    async def test_a_bare_merge_carries_the_users_ledger_rows_across(
        self, db_session: AsyncSession
    ):
        """``connector_plays.resolved_track_id`` is ON DELETE CASCADE, so a
        merge that left the ledger behind destroyed the user's raw
        observations, and ``play_sources`` followed through its own CASCADE.

        ``merge_pair`` used to re-point them itself for exactly this reason.
        The service does it now, so the workaround is gone and this test is
        what says the service still does — including for a cross-tenant pair,
        where the observations belong to the target user and the loser track
        does not.
        """
        winner, loser, _ = await _merge_scenario(db_session, suffix="bare")
        play = (
            await db_session.execute(
                select(DBConnectorPlay.id).where(
                    DBConnectorPlay.resolved_track_id == loser.id
                )
            )
        ).scalar_one()
        canonical = (
            await db_session.execute(
                select(DBTrackPlay.id).where(DBTrackPlay.track_id == loser.id)
            )
        ).scalar_one()
        db_session.add(
            DBPlaySource(user_id=_USER, track_play_id=canonical, connector_play_id=play)
        )
        await db_session.flush()

        uow = get_unit_of_work(db_session)
        _ = await uow.get_track_merge_service().merge_tracks(winner.id, loser.id, uow)
        await db_session.flush()

        assert (
            await db_session.execute(
                select(DBConnectorPlay.resolved_track_id).where(
                    DBConnectorPlay.id == play
                )
            )
        ).scalar_one() == winner.id
        # The edge survives because its ledger row did — and both of its ends
        # still resolve, the canonical play having moved to the winner too.
        assert (
            await db_session.execute(
                select(DBPlaySource.track_play_id).where(
                    DBPlaySource.connector_play_id == play
                )
            )
        ).scalar_one() == canonical
        assert (
            await db_session.execute(
                select(DBTrackPlay.track_id).where(DBTrackPlay.id == canonical)
            )
        ).scalar_one() == winner.id


class TestCrossTenantMerge:
    async def test_the_users_plays_and_ledger_land_on_the_track_they_own(
        self, db_session: AsyncSession
    ):
        winner, loser, _ = await _merge_scenario(db_session, suffix="merge")
        pair = await _only_pair(db_session, loser)
        assert pair.is_mergeable

        counts = await merge_pair(db_session, get_unit_of_work(db_session), pair)
        await db_session.flush()

        assert await _owner_of(db_session, loser.id) is None
        assert await _owner_of(db_session, winner.id) == _USER
        moved_plays = (
            await db_session.execute(
                select(DBTrackPlay.user_id).where(DBTrackPlay.track_id == winner.id)
            )
        ).scalars()
        assert list(moved_plays) == [_USER]
        moved_ledger = (
            await db_session.execute(
                select(DBConnectorPlay.user_id).where(
                    DBConnectorPlay.resolved_track_id == winner.id
                )
            )
        ).scalars()
        assert list(moved_ledger) == [_USER]

    async def test_a_third_tenants_mapping_on_the_loser_refuses_the_pair(
        self, db_session: AsyncSession
    ):
        """C3's root cause is several tenants sharing one canonical, so the
        loser can hold rows belonging to neither end of the pair. Re-owning
        them to this user is the cross-tenant leak the repair exists to close,
        and no merge of this pair has anything to say about them."""
        _, loser, connector_track = await _merge_scenario(db_session, suffix="third")
        _ = await _mapping(
            db_session, user_id=_THIRD, track=loser, connector_track=connector_track
        )

        pair = await _only_pair(db_session, loser)

        assert not pair.is_mergeable
        assert any("neither end of this pair" in reason for reason in pair.refusals)

    async def test_no_mapping_survives_the_merge_owned_by_the_old_tenant(
        self, db_session: AsyncSession
    ):
        """The trap: ``merge_mappings_to_track``'s conflict CTE is same-tenant,
        so a foreign live mapping would reach the non-conflicting arm and
        survive live on this user's track under ``default`` — the mistenanted
        mapping the repair exists to remove, re-created by the repair.
        """
        winner, loser, _ = await _merge_scenario(db_session, suffix="tenancy")
        pair = await _only_pair(db_session, loser)

        counts = await merge_pair(db_session, get_unit_of_work(db_session), pair)
        await db_session.flush()

        assert counts["mappings_conflated"] == 1
        rows = (
            await db_session.execute(
                select(DBTrackMapping.user_id, DBTrackMapping.track_id)
                .where(DBTrackMapping.track_id == winner.id)
                .execution_options(**{INCLUDE_SUPERSEDED: True})
            )
        ).all()
        assert {owner for owner, _ in rows} == {_USER}
        assert len(rows) == 2

    async def test_the_conflated_mapping_is_retired_into_the_users_own_row(
        self, db_session: AsyncSession
    ):
        """Retired, not deleted: a merge revises an identity assertion, and the
        row it revises is the only evidence the assertion was ever made."""
        winner, loser, _ = await _merge_scenario(db_session, suffix="retire")
        survivor = (
            await db_session.execute(
                select(DBTrackMapping.id).where(
                    DBTrackMapping.track_id == winner.id,
                    DBTrackMapping.user_id == _USER,
                )
            )
        ).scalar_one()
        pair = await _only_pair(db_session, loser)

        _ = await merge_pair(db_session, get_unit_of_work(db_session), pair)
        await db_session.flush()

        retired = (
            await db_session.execute(
                select(
                    DBTrackMapping.superseded_by_id,
                    DBTrackMapping.supersession_reason,
                    DBTrackMapping.is_primary,
                )
                .where(DBTrackMapping.superseded_at.is_not(None))
                .execution_options(**{INCLUDE_SUPERSEDED: True})
            )
        ).all()
        assert retired == [(survivor, "conflation", False)]

    async def test_the_conflation_is_recorded_as_an_event(
        self, db_session: AsyncSession
    ):
        """Through the same recorder the merge service uses — a supersession
        the log did not record is a mapping that changed for no reason anyone
        can reconstruct."""
        winner, loser, _ = await _merge_scenario(db_session, suffix="event")
        pair = await _only_pair(db_session, loser)

        _ = await merge_pair(db_session, get_unit_of_work(db_session), pair)
        await db_session.flush()

        events = (
            await db_session.execute(
                select(DBResolutionEvent.user_id, DBResolutionEvent.event_type).where(
                    DBResolutionEvent.track_id == winner.id
                )
            )
        ).all()
        assert events == [(_USER, "superseded")]

    async def test_retired_history_on_the_loser_follows_it_to_the_winner(
        self, db_session: AsyncSession
    ):
        winner, loser, _ = await _merge_scenario(db_session, suffix="history")
        live = (
            await db_session.execute(
                select(DBTrackMapping.id).where(
                    DBTrackMapping.track_id == loser.id,
                    DBTrackMapping.user_id == _OWNER,
                )
            )
        ).scalar_one()
        old = await _mapping(
            db_session,
            user_id=_OWNER,
            track=loser,
            connector_track=await _connector_track(db_session, suffix="history_old"),
            is_primary=False,
        )
        old_id = old.id
        _ = await db_session.execute(
            update(DBTrackMapping)
            .where(DBTrackMapping.id == old_id)
            .values(
                superseded_by_id=live,
                superseded_at=_CREATED,
                supersession_reason="rematch",
            )
        )
        await db_session.flush()
        pair = await _only_pair(db_session, loser)

        _ = await merge_pair(db_session, get_unit_of_work(db_session), pair)
        await db_session.flush()

        followed = (
            await db_session.execute(
                select(DBTrackMapping.track_id, DBTrackMapping.user_id)
                .where(DBTrackMapping.id == old_id)
                .execution_options(**{INCLUDE_SUPERSEDED: True})
            )
        ).one()
        assert followed == (winner.id, _USER)


class TestCrossTenantMergeGuards:
    async def test_a_row_the_merge_would_cascade_away_aborts_it(
        self, db_session: AsyncSession
    ):
        """``match_reviews`` is CASCADE and the merge moves none — the guard is
        the only thing between the operator and a silent delete."""
        _, loser, connector_track = await _merge_scenario(db_session, suffix="guard")
        pair = await _only_pair(db_session, loser)
        db_session.add(
            DBMatchReview(
                user_id=_OWNER,
                track_id=loser.id,
                connector_name="spotify",
                connector_track_id=connector_track.id,
                confidence=70,
                match_weight=0.7,
                match_method="isrc",
                status="pending",
            )
        )
        await db_session.flush()

        with pytest.raises(ValueError, match="CASCADE-deleted"):
            _ = await assert_nothing_will_cascade(db_session, pair)

    async def test_a_winner_the_user_no_longer_owns_aborts_the_merge(
        self, db_session: AsyncSession
    ):
        """The plan is a snapshot; ownership is re-read under a row lock before
        anything is written."""
        winner, loser, _ = await _merge_scenario(db_session, suffix="stale")
        pair = await _only_pair(db_session, loser)
        _ = await db_session.execute(
            update(DBTrack).where(DBTrack.id == winner.id).values(user_id="somebody")
        )
        await db_session.flush()

        with pytest.raises(ValueError, match="refusing to merge into a row"):
            _ = await assert_pair_still_valid(db_session, pair)

    async def test_a_pair_that_is_not_the_same_recording_is_never_mergeable(
        self, db_session: AsyncSession
    ):
        """A cross-tenant collision between two *different* recordings is an
        identity conflict, not a duplicate — it must be reported, not merged."""
        _, loser, _ = await _merge_scenario(db_session, suffix="conflict")
        _ = await db_session.execute(
            update(DBTrack).where(DBTrack.id == loser.id).values(duration_ms=400_000)
        )
        await db_session.flush()

        pair = await _only_pair(db_session, loser)

        assert not pair.is_mergeable
        assert "identity conflict" in pair.refusals[0]
