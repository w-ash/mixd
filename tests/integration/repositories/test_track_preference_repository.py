"""Integration tests for TrackPreferenceRepository.

Tests real database operations for the preference system — batch upsert,
batch delete, event logging, constraint enforcement — using the db_session
fixture with testcontainers PostgreSQL.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.preference import PreferenceEvent, TrackPreference
from src.infrastructure.persistence.database.db_models import DBTrack
from src.infrastructure.persistence.repositories.track.preferences import (
    TrackPreferenceRepository,
)


async def _seed_track(session: AsyncSession, user_id: str = "default") -> DBTrack:
    """Insert a track and return it."""
    track = DBTrack(
        title=f"Track {uuid7().hex[:8]}",
        artists=[{"name": "Test Artist"}],
        user_id=user_id,
    )
    session.add(track)
    await session.flush()
    return track


class TestSetPreferences:
    """Batch upsert semantics."""

    async def test_create_single_preference(self, db_session: AsyncSession) -> None:
        track = await _seed_track(db_session)
        repo = TrackPreferenceRepository(db_session)

        pref = TrackPreference(
            user_id="default",
            track_id=track.id,
            state="star",
            source="manual",
            preferred_at=datetime.now(UTC),
        )
        result = await repo.set_preferences([pref], user_id="default")

        assert len(result) == 1
        assert result[0].state == "star"
        assert result[0].source == "manual"

    async def test_batch_create(self, db_session: AsyncSession) -> None:
        tracks = [await _seed_track(db_session) for _ in range(3)]
        repo = TrackPreferenceRepository(db_session)
        now = datetime.now(UTC)

        prefs = [
            TrackPreference(
                user_id="default",
                track_id=t.id,
                state=state,
                source="manual",
                preferred_at=now,
            )
            for t, state in zip(tracks, ["star", "yah", "nah"], strict=True)
        ]
        result = await repo.set_preferences(prefs, user_id="default")

        assert len(result) == 3
        fetched = await repo.get_preferences([t.id for t in tracks], user_id="default")
        assert {p.state for p in fetched.values()} == {"star", "yah", "nah"}

    async def test_empty_batch_returns_empty(self, db_session: AsyncSession) -> None:
        repo = TrackPreferenceRepository(db_session)
        result = await repo.set_preferences([], user_id="default")
        assert result == []

    async def test_upsert_idempotency(self, db_session: AsyncSession) -> None:
        """Same preference twice produces no duplicate row."""
        track = await _seed_track(db_session)
        repo = TrackPreferenceRepository(db_session)
        now = datetime.now(UTC)

        pref = TrackPreference(
            user_id="default",
            track_id=track.id,
            state="yah",
            source="manual",
            preferred_at=now,
        )
        await repo.set_preferences([pref], user_id="default")
        await repo.set_preferences([pref], user_id="default")

        fetched = await repo.get_preferences([track.id], user_id="default")
        assert len(fetched) == 1
        assert fetched[track.id].state == "yah"

    async def test_upsert_updates_state(self, db_session: AsyncSession) -> None:
        track = await _seed_track(db_session)
        repo = TrackPreferenceRepository(db_session)
        now = datetime.now(UTC)

        pref1 = TrackPreference(
            user_id="default",
            track_id=track.id,
            state="hmm",
            source="manual",
            preferred_at=now,
        )
        await repo.set_preferences([pref1], user_id="default")

        pref2 = TrackPreference(
            user_id="default",
            track_id=track.id,
            state="star",
            source="manual",
            preferred_at=now,
        )
        await repo.set_preferences([pref2], user_id="default")

        fetched = await repo.get_preferences([track.id], user_id="default")
        assert fetched[track.id].state == "star"


class TestMultiUser:
    """Multi-user isolation for preferences."""

    async def test_different_users_same_track(self, db_session: AsyncSession) -> None:
        track = await _seed_track(db_session)
        repo = TrackPreferenceRepository(db_session)
        now = datetime.now(UTC)

        await repo.set_preferences(
            [
                TrackPreference(
                    user_id="alice",
                    track_id=track.id,
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
                    track_id=track.id,
                    state="nah",
                    source="manual",
                    preferred_at=now,
                )
            ],
            user_id="bob",
        )

        alice = await repo.get_preferences([track.id], user_id="alice")
        bob = await repo.get_preferences([track.id], user_id="bob")

        assert alice[track.id].state == "star"
        assert bob[track.id].state == "nah"


class TestRemovePreferences:
    """Batch removal."""

    async def test_remove_existing(self, db_session: AsyncSession) -> None:
        track = await _seed_track(db_session)
        repo = TrackPreferenceRepository(db_session)

        await repo.set_preferences(
            [
                TrackPreference(
                    user_id="default",
                    track_id=track.id,
                    state="yah",
                    source="manual",
                    preferred_at=datetime.now(UTC),
                )
            ],
            user_id="default",
        )
        removed = await repo.remove_preferences([track.id], user_id="default")

        assert removed == 1
        fetched = await repo.get_preferences([track.id], user_id="default")
        assert fetched == {}

    async def test_remove_nonexistent(self, db_session: AsyncSession) -> None:
        repo = TrackPreferenceRepository(db_session)
        removed = await repo.remove_preferences([uuid7()], user_id="default")
        assert removed == 0

    async def test_remove_empty_batch(self, db_session: AsyncSession) -> None:
        repo = TrackPreferenceRepository(db_session)
        removed = await repo.remove_preferences([], user_id="default")
        assert removed == 0


class TestCascadeDelete:
    """ON DELETE CASCADE: deleting a track removes its preferences and events."""

    async def test_cascade_removes_preference_and_events(
        self, db_session: AsyncSession
    ) -> None:
        track = await _seed_track(db_session)
        repo = TrackPreferenceRepository(db_session)
        now = datetime.now(UTC)

        await repo.set_preferences(
            [
                TrackPreference(
                    user_id="default",
                    track_id=track.id,
                    state="star",
                    source="manual",
                    preferred_at=now,
                )
            ],
            user_id="default",
        )
        await repo.add_events(
            [
                PreferenceEvent(
                    user_id="default",
                    track_id=track.id,
                    old_state=None,
                    new_state="star",
                    source="manual",
                    preferred_at=now,
                )
            ],
            user_id="default",
        )

        await db_session.delete(track)
        await db_session.flush()

        fetched = await repo.get_preferences([track.id], user_id="default")
        assert fetched == {}


class TestCountByState:
    async def test_mixed_states(self, db_session: AsyncSession) -> None:
        repo = TrackPreferenceRepository(db_session)
        now = datetime.now(UTC)

        prefs = []
        for state, count in [("star", 3), ("yah", 2), ("nah", 1)]:
            for _ in range(count):
                track = await _seed_track(db_session)
                prefs.append(
                    TrackPreference(
                        user_id="default",
                        track_id=track.id,
                        state=state,
                        source="manual",
                        preferred_at=now,
                    )
                )
        await repo.set_preferences(prefs, user_id="default")

        counts = await repo.count_by_state(user_id="default")
        assert counts.get("star") == 3
        assert counts.get("yah") == 2
        assert counts.get("nah") == 1


class TestListByPreferredAt:
    async def test_ordering_and_boundaries(self, db_session: AsyncSession) -> None:
        repo = TrackPreferenceRepository(db_session)
        base = datetime(2025, 6, 1, tzinfo=UTC)

        prefs = []
        for i in range(5):
            track = await _seed_track(db_session)
            prefs.append(
                TrackPreference(
                    user_id="default",
                    track_id=track.id,
                    state="yah",
                    source="manual",
                    preferred_at=base + timedelta(days=i),
                )
            )
        await repo.set_preferences(prefs, user_id="default")

        results = await repo.list_by_preferred_at(
            user_id="default",
            after=base + timedelta(days=1),
            before=base + timedelta(days=4),
        )

        assert len(results) == 3
        assert results[0].preferred_at > results[-1].preferred_at


class TestAddEvents:
    """Batch event logging."""

    async def test_event_preserves_source_timestamp(
        self, db_session: AsyncSession
    ) -> None:
        track = await _seed_track(db_session)
        repo = TrackPreferenceRepository(db_session)
        source_time = datetime(2024, 3, 15, tzinfo=UTC)

        events = [
            PreferenceEvent(
                user_id="default",
                track_id=track.id,
                old_state=None,
                new_state="yah",
                source="service_import",
                preferred_at=source_time,
            )
        ]
        result = await repo.add_events(events, user_id="default")

        assert len(result) == 1
        assert result[0].preferred_at == source_time
        assert result[0].old_state is None
        assert result[0].new_state == "yah"

    async def test_nullable_new_state_for_removal(
        self, db_session: AsyncSession
    ) -> None:
        track = await _seed_track(db_session)
        repo = TrackPreferenceRepository(db_session)

        result = await repo.add_events(
            [
                PreferenceEvent(
                    user_id="default",
                    track_id=track.id,
                    old_state="yah",
                    new_state=None,
                    source="manual",
                    preferred_at=datetime.now(UTC),
                )
            ],
            user_id="default",
        )

        assert result[0].new_state is None

    async def test_batch_insert(self, db_session: AsyncSession) -> None:
        track = await _seed_track(db_session)
        repo = TrackPreferenceRepository(db_session)
        now = datetime.now(UTC)

        events = [
            PreferenceEvent(
                user_id="default",
                track_id=track.id,
                old_state=None,
                new_state=state,
                source="manual",
                preferred_at=now,
            )
            for state in ("hmm", "yah", "star")
        ]
        result = await repo.add_events(events, user_id="default")
        assert len(result) == 3


class TestGetPreferences:
    async def test_returns_dict_by_track_id(self, db_session: AsyncSession) -> None:
        repo = TrackPreferenceRepository(db_session)
        now = datetime.now(UTC)

        tracks = [await _seed_track(db_session) for _ in range(3)]
        prefs = [
            TrackPreference(
                user_id="default",
                track_id=t.id,
                state=state,
                source="manual",
                preferred_at=now,
            )
            for t, state in zip(tracks, ["hmm", "yah", "star"], strict=True)
        ]
        await repo.set_preferences(prefs, user_id="default")

        result = await repo.get_preferences([t.id for t in tracks], user_id="default")

        assert len(result) == 3
        assert result[tracks[0].id].state == "hmm"
        assert result[tracks[2].id].state == "star"

    async def test_empty_input(self, db_session: AsyncSession) -> None:
        repo = TrackPreferenceRepository(db_session)
        assert await repo.get_preferences([], user_id="default") == {}


class TestListByState:
    async def test_filters_correctly(self, db_session: AsyncSession) -> None:
        repo = TrackPreferenceRepository(db_session)
        now = datetime.now(UTC)

        prefs = []
        for state in ["star", "star", "nah"]:
            track = await _seed_track(db_session)
            prefs.append(
                TrackPreference(
                    user_id="default",
                    track_id=track.id,
                    state=state,
                    source="manual",
                    preferred_at=now,
                )
            )
        await repo.set_preferences(prefs, user_id="default")

        stars = await repo.list_by_state("star", user_id="default")
        assert len(stars) == 2
        assert all(p.state == "star" for p in stars)
