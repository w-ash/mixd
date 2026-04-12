"""Integration tests for TrackMergeService with real database operations."""

from datetime import UTC, datetime
from uuid import uuid7

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.preference import PreferenceEvent, TrackPreference
from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.database.db_models import (
    DBTrack,
    DBTrackLike,
    DBTrackPlay,
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
