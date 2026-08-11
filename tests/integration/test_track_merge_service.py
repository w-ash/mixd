"""Integration tests for TrackMergeService with real database operations."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid7

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.play_projection_service import PlayProjectionService
from src.application.use_cases.rebuild_play_history import (
    RebuildPlayHistoryCommand,
    RebuildPlayHistoryUseCase,
)
from src.domain.entities import ConnectorTrackPlay
from src.domain.entities.preference import PreferenceEvent, TrackPreference
from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlay,
    DBConnectorTrack,
    DBPlaySource,
    DBResolutionEvent,
    DBTrack,
    DBTrackLike,
    DBTrackMapping,
    DBTrackPlay,
)
from src.infrastructure.persistence.database.live_rows import INCLUDE_SUPERSEDED
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from src.infrastructure.persistence.repositories.track.connector import (
    TrackConnectorRepository,
    TrackMappingRepository,
)
from src.infrastructure.persistence.repositories.track.preferences import (
    TrackPreferenceRepository,
)
from src.infrastructure.persistence.unit_of_work import DatabaseUnitOfWork
from src.infrastructure.services.track_merge_service import TrackMergeService
from tests.fixtures import make_track


class TestTrackMergeServiceIntegration:
    """Integration tests for track merging functionality with real database."""

    async def test_merge_tracks_moves_references(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """Test that merge_tracks moves all foreign key references and hard-deletes loser."""
        # Create two test tracks
        winner_track_db = DBTrack(
            title="Test Song", artists={"names": ["Test Artist"]}, album="Test Album"
        )
        loser_track_db = DBTrack(
            title="Test Song (Duplicate)",
            artists={"names": ["Test Artist"]},
            album="Test Album",
        )

        db_session.add(winner_track_db)
        db_session.add(loser_track_db)
        await db_session.flush()

        # Track cleanup
        test_data_tracker.add_track(winner_track_db.id)
        test_data_tracker.add_track(loser_track_db.id)

        # Create some plays and likes for the loser track

        play = DBTrackPlay(
            track_id=loser_track_db.id,
            service="spotify",
            played_at=datetime(2025, 1, 1, tzinfo=UTC),
            ms_played=30000,
        )
        like = DBTrackLike(track_id=loser_track_db.id, service="spotify", is_liked=True)

        db_session.add(play)
        db_session.add(like)
        await db_session.flush()

        # Perform merge using UnitOfWork context manager
        uow = DatabaseUnitOfWork(db_session)
        merge_service = TrackMergeService()

        async with uow:
            await merge_service.merge_tracks(winner_track_db.id, loser_track_db.id, uow)

        # Verify foreign key references were moved
        await db_session.refresh(play)
        await db_session.refresh(like)

        assert play.track_id == winner_track_db.id
        assert like.track_id == winner_track_db.id

        # Verify loser track was hard-deleted
        from sqlalchemy import select

        result = await db_session.execute(
            select(DBTrack).where(DBTrack.id == loser_track_db.id)
        )
        deleted_track = result.scalar_one_or_none()
        assert deleted_track is None, "Loser track should be hard-deleted"

    async def test_merge_tracks_validates_input(self, db_session: AsyncSession):
        """Test that merge_tracks validates track IDs."""
        uow = DatabaseUnitOfWork(db_session)
        merge_service = TrackMergeService()

        # Test merging track with itself
        same_id = uuid7()
        with pytest.raises(ValueError, match="Cannot merge track with itself"):
            async with uow:
                await merge_service.merge_tracks(same_id, same_id, uow)

    async def test_merge_tracks_with_nonexistent_tracks(self, db_session: AsyncSession):
        """Test merge behavior with non-existent tracks."""
        uow = DatabaseUnitOfWork(db_session)
        merge_service = TrackMergeService()

        # Test with non-existent track IDs
        fake_id_1 = uuid7()
        fake_id_2 = uuid7()
        with pytest.raises(NotFoundError, match="not found"):
            async with uow:
                await merge_service.merge_tracks(fake_id_1, fake_id_2, uow)


class TestTrackMergePreferences:
    """Preferences must move (or be conflict-resolved) when tracks merge."""

    @staticmethod
    async def _make_pair(session: AsyncSession, tracker) -> tuple[DBTrack, DBTrack]:
        winner = DBTrack(title="Winner", artists={"names": ["A"]})
        loser = DBTrack(title="Loser", artists={"names": ["A"]})
        session.add_all([winner, loser])
        await session.flush()
        tracker.add_track(winner.id)
        tracker.add_track(loser.id)
        return winner, loser

    @staticmethod
    async def _merge(session: AsyncSession, winner_id, loser_id) -> None:
        uow = DatabaseUnitOfWork(session)
        async with uow:
            await TrackMergeService().merge_tracks(winner_id, loser_id, uow)

    async def test_loser_only_preference_moves_to_winner(
        self, db_session: AsyncSession, test_data_tracker
    ):
        winner, loser = await self._make_pair(db_session, test_data_tracker)
        repo = TrackPreferenceRepository(db_session)
        now = datetime.now(UTC)

        await repo.set_preferences(
            [
                TrackPreference(
                    user_id="default",
                    track_id=loser.id,
                    state="star",
                    source="manual",
                    preferred_at=now,
                )
            ],
            user_id="default",
        )

        await self._merge(db_session, winner.id, loser.id)

        fetched = await repo.get_preferences([winner.id], user_id="default")
        assert fetched[winner.id].state == "star"

    @pytest.mark.parametrize(
        (
            "winner_state",
            "winner_source",
            "loser_state",
            "loser_source",
            "expected_state",
            "expected_source",
        ),
        [
            # Loser's manual preference overrides winner's service_import (priority).
            ("yah", "service_import", "nah", "manual", "nah", "manual"),
            # Same source (manual), higher PREFERENCE_ORDER state wins.
            ("yah", "manual", "star", "manual", "star", "manual"),
            # Winner's manual preference preserved over loser's service_import.
            ("star", "manual", "nah", "service_import", "star", "manual"),
            # Same source (service_import), lower state doesn't downgrade higher.
            (
                "star",
                "service_import",
                "yah",
                "service_import",
                "star",
                "service_import",
            ),
        ],
        ids=[
            "manual>service_import",
            "same_source_higher_state",
            "winner_kept",
            "no_downgrade",
        ],
    )
    async def test_preference_conflict_resolution(
        self,
        db_session: AsyncSession,
        test_data_tracker,
        winner_state: str,
        winner_source: str,
        loser_state: str,
        loser_source: str,
        expected_state: str,
        expected_source: str,
    ):
        """Conflict resolution: higher source priority wins; ties broken by PREFERENCE_ORDER."""
        winner, loser = await self._make_pair(db_session, test_data_tracker)
        repo = TrackPreferenceRepository(db_session)
        now = datetime.now(UTC)

        await repo.set_preferences(
            [
                TrackPreference(
                    user_id="default",
                    track_id=winner.id,
                    state=winner_state,  # type: ignore[arg-type]
                    source=winner_source,
                    preferred_at=now,  # type: ignore[arg-type]
                ),
                TrackPreference(
                    user_id="default",
                    track_id=loser.id,
                    state=loser_state,  # type: ignore[arg-type]
                    source=loser_source,
                    preferred_at=now,  # type: ignore[arg-type]
                ),
            ],
            user_id="default",
        )

        await self._merge(db_session, winner.id, loser.id)

        fetched = await repo.get_preferences([winner.id], user_id="default")
        assert fetched[winner.id].state == expected_state
        assert fetched[winner.id].source == expected_source

    async def test_events_move_to_winner(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """Preference events are append-only — all move to winner, no conflict resolution."""
        winner, loser = await self._make_pair(db_session, test_data_tracker)
        repo = TrackPreferenceRepository(db_session)
        now = datetime.now(UTC)

        await repo.add_events(
            [
                PreferenceEvent(
                    user_id="default",
                    track_id=loser.id,
                    old_state=None,
                    new_state="yah",
                    source="manual",
                    preferred_at=now,
                ),
                PreferenceEvent(
                    user_id="default",
                    track_id=loser.id,
                    old_state="yah",
                    new_state="star",
                    source="manual",
                    preferred_at=now,
                ),
            ],
            user_id="default",
        )

        await self._merge(db_session, winner.id, loser.id)

        # Query events table directly — no domain method for raw fetch
        from sqlalchemy import select

        from src.infrastructure.persistence.database.db_models import (
            DBTrackPreferenceEvent,
        )

        result = await db_session.execute(
            select(DBTrackPreferenceEvent).where(
                DBTrackPreferenceEvent.track_id == winner.id
            )
        )
        events = result.scalars().all()
        assert len(events) == 2

    async def test_different_users_no_conflict(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """Preferences for different users never conflict — both survive on winner."""
        winner, loser = await self._make_pair(db_session, test_data_tracker)
        repo = TrackPreferenceRepository(db_session)
        now = datetime.now(UTC)

        await repo.set_preferences(
            [
                TrackPreference(
                    user_id="alice",
                    track_id=winner.id,
                    state="star",
                    source="manual",
                    preferred_at=now,
                )
            ],
            user_id="alice",
        )
        await repo.set_preferences(
            [
                TrackPreference(
                    user_id="bob",
                    track_id=loser.id,
                    state="nah",
                    source="manual",
                    preferred_at=now,
                )
            ],
            user_id="bob",
        )

        await self._merge(db_session, winner.id, loser.id)

        alice = await repo.get_preferences([winner.id], user_id="alice")
        bob = await repo.get_preferences([winner.id], user_id="bob")
        assert alice[winner.id].state == "star"
        assert bob[winner.id].state == "nah"


class TestMergePreservesMappingHistory:
    """A merge is an assertion about identity; it may not destroy the evidence."""

    @staticmethod
    async def _track(db_session: AsyncSession, title: str, tracker) -> DBTrack:
        row = DBTrack(title=title, artists={"names": ["Merge Artist"]})
        db_session.add(row)
        await db_session.flush()
        tracker.add_track(row.id)
        return row

    @staticmethod
    async def _connector_track(
        db_session: AsyncSession, connector: str = "spotify"
    ) -> DBConnectorTrack:
        uid = uuid4().hex[:8]
        row = DBConnectorTrack(
            connector_name=connector,
            connector_track_identifier=f"{connector}_merge_{uid}",
            title=f"CT {uid}",
            artists={"names": ["Merge Artist"]},
            raw_metadata={},
            last_updated=datetime.now(UTC),
        )
        db_session.add(row)
        await db_session.flush()
        return row

    @staticmethod
    def _mapping_row(
        track_id, ct_id, *, confidence: int, connector: str = "spotify"
    ) -> dict[str, object]:
        return {
            "user_id": "default",
            "track_id": track_id,
            "connector_track_id": ct_id,
            "connector_name": connector,
            "match_method": "isrc",
            "confidence": confidence,
        }

    @staticmethod
    async def _all_mappings(db_session: AsyncSession, track_id) -> list[DBTrackMapping]:
        result = await db_session.execute(
            select(DBTrackMapping)
            .where(DBTrackMapping.track_id == track_id)
            .execution_options(**{INCLUDE_SUPERSEDED: True}, populate_existing=True)
        )
        return list(result.scalars().all())

    async def _legacy_same_connector_conflict(
        self, db_session: AsyncSession, winner: DBTrack, loser: DBTrack
    ):
        """Two live mappings on one connector track — a pre-044 shape.

        ``uq_track_mappings_live_connector`` forbids it, so the only way to
        reach the merge's same-connector-track branch is to stand the index
        down for the length of this test's transaction (which the savepoint
        fixture rolls back) and insert the second live row through Core. Worth
        reaching: that branch is the one that used to ``DELETE`` a mapping
        outright, and a database restored from a pre-044 dump is exactly where
        it would fire.
        """
        # Last.fm rather than Spotify: the shape below is already impossible,
        # and a connector with a denormalized id column on ``tracks`` would
        # additionally trip ``uq_tracks_user_spotify_id`` when both tracks'
        # read-path healing claimed the same identifier.
        ct = await self._connector_track(db_session, "lastfm")
        winner_mapping = (
            await TrackMappingRepository(db_session).assert_mappings([
                self._mapping_row(winner.id, ct.id, confidence=90, connector="lastfm")
            ])
        ).created[0]

        _ = await db_session.execute(
            text("DROP INDEX uq_track_mappings_live_connector")
        )
        now = datetime.now(UTC)
        loser_mapping = uuid7()
        db_session.add(
            DBTrackMapping(
                id=loser_mapping,
                user_id="default",
                track_id=loser.id,
                connector_track_id=ct.id,
                connector_name="lastfm",
                match_method="isrc",
                confidence=95,
                created_at=now,
                updated_at=now,
            )
        )
        await db_session.flush()
        return ct, winner_mapping, loser_mapping

    async def test_a_same_connector_track_loser_is_retired_not_deleted(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """Deleting it erased the only record of what the merge revised."""
        winner = await self._track(db_session, "Winner", test_data_tracker)
        loser = await self._track(db_session, "Loser", test_data_tracker)
        _, winner_mapping, loser_mapping = await self._legacy_same_connector_conflict(
            db_session, winner, loser
        )

        uow = DatabaseUnitOfWork(db_session)
        async with uow:
            await TrackMergeService().merge_tracks(winner.id, loser.id, uow)

        rows = {row.id: row for row in await self._all_mappings(db_session, winner.id)}
        assert loser_mapping in rows, "the loser mapping must not be deleted"
        retired = rows[loser_mapping]
        assert retired.superseded_at is not None
        assert retired.supersession_reason == "conflation"
        assert retired.superseded_by_id == winner_mapping
        assert retired.is_primary is False
        assert retired.confidence == 95, "a merge must not re-score a decision"
        assert rows[winner_mapping].confidence == 90
        live = [row for row in rows.values() if row.superseded_at is None]
        assert [row.id for row in live] == [winner_mapping]

    async def test_the_retirement_is_recorded_as_an_event(
        self, db_session: AsyncSession, test_data_tracker
    ):
        winner = await self._track(db_session, "Winner", test_data_tracker)
        loser = await self._track(db_session, "Loser", test_data_tracker)
        _, _, loser_mapping = await self._legacy_same_connector_conflict(
            db_session, winner, loser
        )

        uow = DatabaseUnitOfWork(db_session)
        async with uow:
            await TrackMergeService().merge_tracks(winner.id, loser.id, uow)

        events = (
            (
                await db_session.execute(
                    select(DBResolutionEvent).where(
                        DBResolutionEvent.event_type == "superseded"
                    )
                )
            )
            .scalars()
            .all()
        )
        conflations = [
            event
            for event in events
            if event.payload.get("superseded_mapping_id") == str(loser_mapping)
        ]
        assert len(conflations) == 1
        assert conflations[0].payload["reason"] == "conflation"

    async def test_the_chain_walks_into_the_merged_history(
        self, db_session: AsyncSession, test_data_tracker
    ):
        winner = await self._track(db_session, "Winner", test_data_tracker)
        loser = await self._track(db_session, "Loser", test_data_tracker)
        _, winner_mapping, loser_mapping = await self._legacy_same_connector_conflict(
            db_session, winner, loser
        )

        uow = DatabaseUnitOfWork(db_session)
        async with uow:
            await TrackMergeService().merge_tracks(winner.id, loser.id, uow)

        chain = await TrackConnectorRepository(db_session).get_supersession_chain(
            loser_mapping, user_id="default"
        )
        assert [link.id for link in chain] == [loser_mapping, winner_mapping]

    async def test_superseded_rows_follow_their_track_untouched(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """History moves with the track; rewriting its origin would edit it."""
        winner = await self._track(db_session, "Winner", test_data_tracker)
        loser = await self._track(db_session, "Loser", test_data_tracker)
        ct = await self._connector_track(db_session)
        mapping_repo = TrackMappingRepository(db_session)
        first = (
            await mapping_repo.assert_mappings([
                self._mapping_row(loser.id, ct.id, confidence=70)
            ])
        ).created[0]
        await mapping_repo.assert_mappings([
            self._mapping_row(loser.id, ct.id, confidence=95)
        ])
        before = {
            row.id: (row.origin, row.confidence, row.match_method)
            for row in await self._all_mappings(db_session, loser.id)
        }

        uow = DatabaseUnitOfWork(db_session)
        async with uow:
            await TrackMergeService().merge_tracks(winner.id, loser.id, uow)

        rows = {row.id: row for row in await self._all_mappings(db_session, winner.id)}
        assert first in rows, "the retired row followed its track"
        assert rows[first].superseded_at is not None
        assert rows[first].origin == before[first][0]
        assert rows[first].confidence == before[first][1]
        assert rows[first].match_method == before[first][2]

    async def test_a_different_connector_track_keeps_both_live(
        self, db_session: AsyncSession, test_data_tracker
    ):
        winner = await self._track(db_session, "Winner", test_data_tracker)
        loser = await self._track(db_session, "Loser", test_data_tracker)
        winner_ct = await self._connector_track(db_session)
        loser_ct = await self._connector_track(db_session)
        mapping_repo = TrackMappingRepository(db_session)
        winner_mapping = (
            await mapping_repo.assert_mappings([
                self._mapping_row(winner.id, winner_ct.id, confidence=90)
            ])
        ).created[0]
        loser_mapping = (
            await mapping_repo.assert_mappings([
                self._mapping_row(loser.id, loser_ct.id, confidence=95)
            ])
        ).created[0]

        uow = DatabaseUnitOfWork(db_session)
        async with uow:
            await TrackMergeService().merge_tracks(winner.id, loser.id, uow)

        rows = {row.id: row for row in await self._all_mappings(db_session, winner.id)}
        assert set(rows) == {winner_mapping, loser_mapping}
        assert all(row.superseded_at is None for row in rows.values())
        assert rows[winner_mapping].is_primary is True
        assert rows[loser_mapping].is_primary is False
        assert rows[loser_mapping].confidence == 95


class TestMergeSourceIsUnambiguous:
    """One loser row, one decision — the CTE arms may not both claim it.

    ``all_conflicts`` joins losers to winners, and a data-modifying CTE that
    lets two arms UPDATE the same row applies exactly one of them, chosen
    unpredictably. Collapsing the source to one winner per loser is the fix
    PostgreSQL's own documentation prescribes; these tests pin the two shapes
    that reach it.
    """

    _track = staticmethod(TestMergePreservesMappingHistory._track)
    _connector_track = staticmethod(TestMergePreservesMappingHistory._connector_track)
    _mapping_row = staticmethod(TestMergePreservesMappingHistory._mapping_row)
    _all_mappings = staticmethod(TestMergePreservesMappingHistory._all_mappings)

    @staticmethod
    def _raw_mapping(
        mapping_id,
        track_id,
        ct_id,
        *,
        user_id: str = "default",
        is_primary: bool = True,
    ) -> DBTrackMapping:
        now = datetime.now(UTC)
        return DBTrackMapping(
            id=mapping_id,
            user_id=user_id,
            track_id=track_id,
            connector_track_id=ct_id,
            connector_name="lastfm",
            match_method="isrc",
            confidence=90,
            is_primary=is_primary,
            created_at=now,
            updated_at=now,
        )

    async def test_a_loser_matching_two_winners_retires_into_the_shared_one(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """The same-connector-track arm must win when both arms apply.

        The winner holds two live lastfm mappings on different connector
        tracks, which ``uq_track_mappings_live_connector`` permits. The loser
        shares one of them, so it joins *both* winners: once same-ext, once
        diff-ext. If the diff-ext arm won, the loser row would be moved to the
        winner track still live, and (user, shared_ct, lastfm) would hold two
        live rows — a 23505 that aborts the whole merge.
        """
        winner = await self._track(db_session, "Winner", test_data_tracker)
        loser = await self._track(db_session, "Loser", test_data_tracker)
        shared_ct = await self._connector_track(db_session, "lastfm")
        other_ct = await self._connector_track(db_session, "lastfm")

        winner_shared, winner_other, loser_mapping = uuid7(), uuid7(), uuid7()
        db_session.add_all([
            self._raw_mapping(winner_shared, winner.id, shared_ct.id),
            self._raw_mapping(winner_other, winner.id, other_ct.id, is_primary=False),
        ])
        await db_session.flush()
        # The loser's duplicate live row on shared_ct is the pre-044 shape the
        # index now forbids, so stand it down for this transaction (the
        # savepoint fixture rolls the DDL back with everything else).
        _ = await db_session.execute(
            text("DROP INDEX uq_track_mappings_live_connector")
        )
        db_session.add(
            self._raw_mapping(loser_mapping, loser.id, shared_ct.id, is_primary=False)
        )
        await db_session.flush()

        uow = DatabaseUnitOfWork(db_session)
        async with uow:
            await TrackMergeService().merge_tracks(winner.id, loser.id, uow)

        rows = {row.id: row for row in await self._all_mappings(db_session, winner.id)}
        retired = rows[loser_mapping]
        assert retired.superseded_at is not None, (
            "the shared-connector-track arm must claim the loser, not the diff-ext arm"
        )
        assert retired.superseded_by_id == winner_shared
        live_on_shared = [
            row
            for row in rows.values()
            if row.connector_track_id == shared_ct.id and row.superseded_at is None
        ]
        assert len(live_on_shared) == 1, "one live mapping per connector track"
        assert winner_other in rows, "the unrelated winner mapping is untouched"

    async def test_a_merge_never_pairs_mappings_across_tenants(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """Two tenants' rows joined on connector_name alone before the fix.

        RLS does not backstop this: PDR-002 records a verified probe showing
        the Neon owner role has BYPASSRLS, so ``FORCE ROW LEVEL SECURITY`` is
        skipped and the join predicate is the isolation. A cross-tenant pair
        would stamp one tenant's ``superseded_by_id`` with another's mapping
        id and emit an edge whose successor the owner can never read.
        """
        winner = await self._track(db_session, "Winner", test_data_tracker)
        loser = await self._track(db_session, "Loser", test_data_tracker)
        shared_ct = await self._connector_track(db_session, "lastfm")

        alice_mapping, bob_mapping = uuid7(), uuid7()
        db_session.add_all([
            self._raw_mapping(alice_mapping, winner.id, shared_ct.id, user_id="alice"),
            self._raw_mapping(bob_mapping, loser.id, shared_ct.id, user_id="bob"),
        ])
        await db_session.flush()

        uow = DatabaseUnitOfWork(db_session)
        async with uow:
            await TrackMergeService().merge_tracks(winner.id, loser.id, uow)

        rows = {row.id: row for row in await self._all_mappings(db_session, winner.id)}
        moved = rows[bob_mapping]
        assert moved.superseded_at is None, (
            "bob's mapping does not conflict with alice's — different tenants"
        )
        assert moved.superseded_by_id is None
        assert moved.user_id == "bob", "the row keeps its own owner"

    async def test_the_merge_does_not_depose_an_existing_primary(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """``uq_primary_mapping`` admits one live primary per connector.

        The diff-ext arm used to set ``is_primary = TRUE`` on every winner it
        matched. When the winner track already has a primary that is not the
        one the arm picked, that is a second live primary for
        (user, track, connector) — another 044 index the merge would trip.
        """
        winner = await self._track(db_session, "Winner", test_data_tracker)
        loser = await self._track(db_session, "Loser", test_data_tracker)
        winner_ct_a = await self._connector_track(db_session, "lastfm")
        winner_ct_b = await self._connector_track(db_session, "lastfm")
        loser_ct = await self._connector_track(db_session, "lastfm")

        # uuid7 is time-ordered, so `older` sorts first on the winner tiebreak
        # while `newer` is the one actually holding primacy — the shape that
        # makes an unguarded promotion collide.
        older, newer, loser_mapping = uuid7(), uuid7(), uuid7()
        db_session.add_all([
            self._raw_mapping(older, winner.id, winner_ct_a.id, is_primary=False),
            self._raw_mapping(newer, winner.id, winner_ct_b.id, is_primary=True),
            self._raw_mapping(loser_mapping, loser.id, loser_ct.id, is_primary=False),
        ])
        await db_session.flush()

        uow = DatabaseUnitOfWork(db_session)
        async with uow:
            await TrackMergeService().merge_tracks(winner.id, loser.id, uow)

        rows = {row.id: row for row in await self._all_mappings(db_session, winner.id)}
        primaries = [
            row.id
            for row in rows.values()
            if row.is_primary and row.superseded_at is None
        ]
        assert primaries == [newer], (
            "the incumbent primary stands; the merge promotes nothing over it"
        )


_LEDGER_AT = datetime(2026, 3, 14, 21, 0, tzinfo=UTC)


class TestMergePreservesTheLedger:
    """``connector_plays`` is the one table a merge cannot merely forget.

    Its ``resolved_track_id`` FK is ON DELETE CASCADE, so a row left on the
    loser is destroyed by the merge rather than stranded by it — and
    ``play_sources`` follows through its own CASCADE on ``connector_play_id``,
    taking the membership edges too. Nothing errors; the history is simply
    smaller afterwards, and v0.10.0's guarantee that canonical plays are a
    replayable projection of this table quietly stops holding.
    """

    @staticmethod
    async def _pair(session: AsyncSession, tracker) -> tuple[DBTrack, DBTrack]:
        winner = DBTrack(title="Ledger Winner", artists={"names": ["A"]})
        loser = DBTrack(title="Ledger Loser", artists={"names": ["A"]})
        session.add_all([winner, loser])
        await session.flush()
        tracker.add_track(winner.id)
        tracker.add_track(loser.id)
        return winner, loser

    @staticmethod
    async def _observation(
        session: AsyncSession,
        track_id: UUID,
        *,
        user_id: str = "default",
        played_at: datetime = _LEDGER_AT,
        resolved_at: datetime = _LEDGER_AT,
    ) -> DBConnectorPlay:
        row = DBConnectorPlay(
            user_id=user_id,
            connector_name="lastfm",
            connector_track_identifier=f"TEST_cp_{uuid4().hex[:8]}",
            played_at=played_at,
            raw_metadata={},
            resolved_track_id=track_id,
            resolved_at=resolved_at,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def _canonical(
        session: AsyncSession, track_id: UUID, *, played_at: datetime = _LEDGER_AT
    ) -> DBTrackPlay:
        row = DBTrackPlay(
            track_id=track_id,
            service="lastfm",
            played_at=played_at,
            ms_played=None,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def _merge(session: AsyncSession, winner_id: UUID, loser_id: UUID) -> None:
        uow = DatabaseUnitOfWork(session)
        async with uow:
            await TrackMergeService().merge_tracks(winner_id, loser_id, uow)
        await session.flush()

    async def test_the_losers_observations_move_to_the_winner(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """The regression: before the fix these rows were CASCADE-deleted."""
        winner, loser = await self._pair(db_session, test_data_tracker)
        observation = await self._observation(db_session, loser.id)

        await self._merge(db_session, winner.id, loser.id)

        assert (
            await db_session.execute(
                select(DBConnectorPlay.resolved_track_id).where(
                    DBConnectorPlay.id == observation.id
                )
            )
        ).scalar_one() == winner.id

    async def test_the_membership_edges_still_resolve_at_both_ends(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """``play_sources`` survives only because its ledger row did.

        Both ends have to land on the winner together: the edge is what says
        which observation backs which canonical play, and an edge whose halves
        disagreed about the track would be worse than no edge at all.
        """
        winner, loser = await self._pair(db_session, test_data_tracker)
        observation = await self._observation(db_session, loser.id)
        canonical = await self._canonical(db_session, loser.id)
        db_session.add(
            DBPlaySource(track_play_id=canonical.id, connector_play_id=observation.id)
        )
        await db_session.flush()

        await self._merge(db_session, winner.id, loser.id)

        edge = (
            (
                await db_session.execute(
                    select(DBPlaySource).where(
                        DBPlaySource.connector_play_id == observation.id
                    )
                )
            )
            .scalars()
            .one()
        )
        assert edge.track_play_id == canonical.id
        assert (
            await db_session.execute(
                select(DBTrackPlay.track_id).where(DBTrackPlay.id == edge.track_play_id)
            )
        ).scalar_one() == winner.id
        assert (
            await db_session.execute(
                select(DBConnectorPlay.resolved_track_id).where(
                    DBConnectorPlay.id == edge.connector_play_id
                )
            )
        ).scalar_one() == winner.id

    async def test_a_loser_with_no_observations_merges_unchanged(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """The common path: nothing in the ledger, nothing new to move."""
        winner, loser = await self._pair(db_session, test_data_tracker)
        untouched = await self._observation(db_session, winner.id)

        await self._merge(db_session, winner.id, loser.id)

        assert (
            await db_session.execute(select(DBTrack.id).where(DBTrack.id == loser.id))
        ).scalar_one_or_none() is None
        assert (
            await db_session.execute(
                select(DBConnectorPlay.resolved_track_id).where(
                    DBConnectorPlay.id == untouched.id
                )
            )
        ).scalar_one() == winner.id

    async def test_both_sides_observations_coexist_on_the_winner(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """Re-pointing cannot collide, so there is no conflict arm to test.

        ``uq_connector_plays_deduplication`` is (user_id, connector_name,
        connector_track_identifier, played_at, ms_played) NULLS NOT DISTINCT —
        ``resolved_track_id`` is in no unique key. The move changes no key
        column, and the table already held at most one row per key whichever
        track it pointed at, so the winner cannot be holding a twin. Both sets
        simply survive.
        """
        winner, loser = await self._pair(db_session, test_data_tracker)
        theirs = await self._observation(db_session, winner.id)
        ours = await self._observation(db_session, loser.id)

        await self._merge(db_session, winner.id, loser.id)

        landed = (
            (
                await db_session.execute(
                    select(DBConnectorPlay.id).where(
                        DBConnectorPlay.resolved_track_id == winner.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(landed) == {theirs.id, ours.id}

    async def test_the_resolution_timestamp_is_not_restamped(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """A merge revises *what* the row resolved to, not when it resolved.

        Restamping ``resolved_at`` would date the observation's reconciliation
        to the merge and erase when the resolution actually happened.
        """
        winner, loser = await self._pair(db_session, test_data_tracker)
        resolved_at = _LEDGER_AT + timedelta(days=2)
        observation = await self._observation(
            db_session, loser.id, resolved_at=resolved_at
        )

        await self._merge(db_session, winner.id, loser.id)

        assert (
            await db_session.execute(
                select(DBConnectorPlay.resolved_at).where(
                    DBConnectorPlay.id == observation.id
                )
            )
        ).scalar_one() == resolved_at

    async def test_another_tenants_observations_move_rather_than_die(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """Unscoped, like every other arm of ``move_references_to_track``.

        The cascade this arm prevents is unscoped, so a ``user_id`` predicate
        would leave a second tenant's observations behind to be deleted — the
        same data loss on the rows least likely to be noticed. The arm that
        moves their canonical plays is unscoped for the same reason; scoping
        only this one would split a tenant's history across the ledger and the
        projection derived from it.
        """
        winner, loser = await self._pair(db_session, test_data_tracker)
        theirs = await self._observation(db_session, loser.id, user_id="TEST_other")

        await self._merge(db_session, winner.id, loser.id)

        row = (
            (
                await db_session.execute(
                    select(
                        DBConnectorPlay.resolved_track_id, DBConnectorPlay.user_id
                    ).where(DBConnectorPlay.id == theirs.id)
                )
            )
            .tuples()
            .one()
        )
        assert row == (winner.id, "TEST_other"), "moved, and still their row"


class TestMergeKeepsCanonicalHistoryReplayable:
    """The consequence the ledger exists for, end to end.

    v0.10.0 made canonical plays a projection of ``connector_plays``. If a
    merge destroys observations, a later rebuild re-derives a *smaller*
    history — no error, no warning, just fewer plays than the user had. This
    replays the projection across a merge and asserts it converges on exactly
    the row it built the first time.
    """

    async def test_a_rebuild_after_a_merge_reproduces_the_same_history(
        self, db_session: AsyncSession
    ):
        user_id = f"TEST_merge_replay_{uuid4().hex[:8]}"
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        winner = await track_repo.save_track(
            make_track(
                title="Replay Winner",
                artist="Carwash",
                user_id=user_id,
                connector_track_identifiers={},
            )
        )
        loser = await track_repo.save_track(
            make_track(
                title="Replay Loser",
                artist="Carwash",
                user_id=user_id,
                connector_track_identifiers={},
            )
        )
        observation = ConnectorTrackPlay(
            service="lastfm",
            artist_name="Carwash",
            track_name="Replay Loser",
            played_at=_LEDGER_AT,
            ms_played=None,
            user_id=user_id,
            import_timestamp=datetime.now(UTC),
            import_source="lastfm_api",
            import_batch_id=f"TEST_{uuid4()}",
        )
        connector_repo = uow.get_connector_play_repository()
        _ = await connector_repo.bulk_insert_connector_plays([observation])
        _ = await connector_repo.bulk_update_resolution(
            [(observation, loser.id)], resolved_at=datetime.now(UTC)
        )
        _ = await PlayProjectionService().project_observed_days(
            uow, user_id=user_id, played_at=[_LEDGER_AT]
        )
        before = (
            (
                await db_session.execute(
                    select(DBTrackPlay.id).where(DBTrackPlay.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(before) == 1

        async with uow:
            _ = await TrackMergeService().merge_tracks(winner.id, loser.id, uow)
        await db_session.flush()

        result = await RebuildPlayHistoryUseCase().execute(
            RebuildPlayHistoryCommand(user_id=user_id), get_unit_of_work(db_session)
        )

        assert result.stats["groups_unchanged"] == 1
        assert result.stats["unsourced_deleted"] == 0
        after = (
            (
                await db_session.execute(
                    select(DBTrackPlay.id, DBTrackPlay.track_id).where(
                        DBTrackPlay.user_id == user_id
                    )
                )
            )
            .tuples()
            .all()
        )
        assert after == [(before[0], winner.id)], (
            "the rebuild re-derived the same play, now on the winner — the "
            "history did not shrink"
        )
