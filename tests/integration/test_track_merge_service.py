"""Integration tests for TrackMergeService with real database operations."""

from datetime import UTC, datetime
from uuid import uuid4, uuid7

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.preference import PreferenceEvent, TrackPreference
from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBResolutionEvent,
    DBTrack,
    DBTrackLike,
    DBTrackMapping,
    DBTrackPlay,
)
from src.infrastructure.persistence.database.live_rows import INCLUDE_SUPERSEDED
from src.infrastructure.persistence.repositories.track.connector import (
    TrackConnectorRepository,
    TrackMappingRepository,
)
from src.infrastructure.persistence.repositories.track.preferences import (
    TrackPreferenceRepository,
)
from src.infrastructure.persistence.unit_of_work import DatabaseUnitOfWork
from src.infrastructure.services.track_merge_service import TrackMergeService


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
