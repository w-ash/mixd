"""Integration tests for TrackTagRepository.

Tests real database operations for the tag system — batch insert with
ON CONFLICT DO NOTHING semantics, batch delete with RETURNING, 3-part
UNIQUE enforcement, event logging, autocomplete, multi-user isolation,
and ON DELETE CASCADE — using the db_session fixture with testcontainers
PostgreSQL.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.tag import TagEvent
from src.infrastructure.persistence.database.db_models import DBTrack
from src.infrastructure.persistence.repositories.track.tags import TrackTagRepository
from tests.fixtures import make_track_tag


async def _seed_track(session: AsyncSession, user_id: str = "default") -> DBTrack:
    track = DBTrack(
        title=f"Track {uuid7().hex[:8]}",
        artists=[{"name": "Test Artist"}],
        user_id=user_id,
    )
    session.add(track)
    await session.flush()
    return track


class TestAddTags:
    """INSERT ... ON CONFLICT DO NOTHING ... RETURNING semantics."""

    async def test_insert_single_tag(self, db_session: AsyncSession) -> None:
        track = await _seed_track(db_session)
        repo = TrackTagRepository(db_session)

        result = await repo.add_tags(
            [make_track_tag(track_id=track.id)], user_id="default"
        )

        assert len(result) == 1
        assert result[0].tag == "mood:chill"
        assert result[0].namespace == "mood"
        assert result[0].value == "chill"

    async def test_batch_insert_distinct_tags(self, db_session: AsyncSession) -> None:
        track = await _seed_track(db_session)
        repo = TrackTagRepository(db_session)

        result = await repo.add_tags(
            [
                make_track_tag(track_id=track.id, tag=t)
                for t in ("mood:chill", "energy:high", "banger")
            ],
            user_id="default",
        )

        assert {t.tag for t in result} == {"mood:chill", "energy:high", "banger"}

    async def test_empty_batch_returns_empty(self, db_session: AsyncSession) -> None:
        repo = TrackTagRepository(db_session)
        assert await repo.add_tags([], user_id="default") == []

    async def test_duplicate_silently_skipped(self, db_session: AsyncSession) -> None:
        """Re-adding an existing tag returns an empty list (nothing inserted)."""
        track = await _seed_track(db_session)
        repo = TrackTagRepository(db_session)

        first = await repo.add_tags(
            [make_track_tag(track_id=track.id)], user_id="default"
        )
        second = await repo.add_tags(
            [make_track_tag(track_id=track.id)], user_id="default"
        )

        assert len(first) == 1
        assert second == []

    async def test_batch_with_mix_of_new_and_existing(
        self, db_session: AsyncSession
    ) -> None:
        """Only actually-inserted rows come back — duplicates are dropped."""
        track = await _seed_track(db_session)
        repo = TrackTagRepository(db_session)

        await repo.add_tags(
            [make_track_tag(track_id=track.id, tag="mood:chill")],
            user_id="default",
        )

        result = await repo.add_tags(
            [
                make_track_tag(track_id=track.id, tag="mood:chill"),
                make_track_tag(track_id=track.id, tag="energy:high"),
            ],
            user_id="default",
        )

        assert {t.tag for t in result} == {"energy:high"}

    async def test_same_tag_different_tracks(self, db_session: AsyncSession) -> None:
        tracks = [await _seed_track(db_session) for _ in range(2)]
        repo = TrackTagRepository(db_session)

        result = await repo.add_tags(
            [make_track_tag(track_id=t.id, tag="mood:chill") for t in tracks],
            user_id="default",
        )

        assert len(result) == 2

    async def test_multi_user_isolation(self, db_session: AsyncSession) -> None:
        """UNIQUE(user_id, track_id, tag): different users share track+tag."""
        track = await _seed_track(db_session)
        repo = TrackTagRepository(db_session)

        alice = await repo.add_tags(
            [make_track_tag(track_id=track.id, user_id="alice")],
            user_id="alice",
        )
        bob = await repo.add_tags(
            [make_track_tag(track_id=track.id, user_id="bob")],
            user_id="bob",
        )

        assert len(alice) == 1
        assert len(bob) == 1


class TestRemoveTags:
    """Batch removal via composite (track_id, tag) keys with RETURNING."""

    async def test_remove_existing(self, db_session: AsyncSession) -> None:
        track = await _seed_track(db_session)
        repo = TrackTagRepository(db_session)
        await repo.add_tags([make_track_tag(track_id=track.id)], user_id="default")

        removed = await repo.remove_tags([(track.id, "mood:chill")], user_id="default")

        assert removed == [(track.id, "mood:chill")]
        assert await repo.get_tags([track.id], user_id="default") == {}

    async def test_remove_nonexistent_returns_empty(
        self, db_session: AsyncSession
    ) -> None:
        repo = TrackTagRepository(db_session)
        assert (
            await repo.remove_tags([(uuid7(), "mood:chill")], user_id="default") == []
        )

    async def test_remove_empty_batch(self, db_session: AsyncSession) -> None:
        repo = TrackTagRepository(db_session)
        assert await repo.remove_tags([], user_id="default") == []

    async def test_remove_partial_match(self, db_session: AsyncSession) -> None:
        """Only pairs that actually exist are returned."""
        track = await _seed_track(db_session)
        repo = TrackTagRepository(db_session)
        await repo.add_tags(
            [make_track_tag(track_id=track.id, tag="mood:chill")],
            user_id="default",
        )

        removed = await repo.remove_tags(
            [(track.id, "mood:chill"), (track.id, "energy:high")],
            user_id="default",
        )

        assert removed == [(track.id, "mood:chill")]


class TestGetTags:
    """Batched fetch returning {track_id: [tags]}."""

    async def test_returns_all_tags_for_track(self, db_session: AsyncSession) -> None:
        track = await _seed_track(db_session)
        repo = TrackTagRepository(db_session)
        await repo.add_tags(
            [
                make_track_tag(track_id=track.id, tag="mood:chill"),
                make_track_tag(track_id=track.id, tag="banger"),
            ],
            user_id="default",
        )

        result = await repo.get_tags([track.id], user_id="default")

        assert {t.tag for t in result[track.id]} == {"mood:chill", "banger"}

    async def test_untagged_tracks_are_omitted(self, db_session: AsyncSession) -> None:
        """Tracks with no tags are not present in the result — caller defaults."""
        track = await _seed_track(db_session)
        repo = TrackTagRepository(db_session)

        assert await repo.get_tags([track.id], user_id="default") == {}

    async def test_empty_input_returns_empty(self, db_session: AsyncSession) -> None:
        repo = TrackTagRepository(db_session)
        assert await repo.get_tags([], user_id="default") == {}


class TestEventsAndCascade:
    """Append-only event log and ON DELETE CASCADE behavior."""

    async def test_add_events_bulk(self, db_session: AsyncSession) -> None:
        track = await _seed_track(db_session)
        repo = TrackTagRepository(db_session)
        now = datetime.now(UTC)

        events = [
            TagEvent(
                user_id="default",
                track_id=track.id,
                tag=f"mood:chill-{i}",
                action="add",
                source="manual",
                tagged_at=now,
            )
            for i in range(3)
        ]
        result = await repo.add_events(events, user_id="default")

        assert len(result) == 3
        assert {e.action for e in result} == {"add"}

    async def test_empty_events_returns_empty(self, db_session: AsyncSession) -> None:
        repo = TrackTagRepository(db_session)
        assert await repo.add_events([], user_id="default") == []

    async def test_cascade_removes_tags_and_events(
        self, db_session: AsyncSession
    ) -> None:
        track = await _seed_track(db_session)
        repo = TrackTagRepository(db_session)
        now = datetime.now(UTC)

        await repo.add_tags([make_track_tag(track_id=track.id)], user_id="default")
        await repo.add_events(
            [
                TagEvent(
                    user_id="default",
                    track_id=track.id,
                    tag="mood:chill",
                    action="add",
                    source="manual",
                    tagged_at=now,
                )
            ],
            user_id="default",
        )

        await db_session.delete(track)
        await db_session.flush()

        assert await repo.get_tags([track.id], user_id="default") == {}


class TestQueries:
    """Aggregate and date-range read paths: list_tags / count_by_tag / list_by_tagged_at."""

    async def test_list_tags_sorted_by_count_desc(
        self, db_session: AsyncSession
    ) -> None:
        tracks = [await _seed_track(db_session) for _ in range(3)]
        repo = TrackTagRepository(db_session)

        await repo.add_tags(
            [make_track_tag(track_id=t.id, tag="mood:chill") for t in tracks],
            user_id="default",
        )
        await repo.add_tags(
            [make_track_tag(track_id=tracks[0].id, tag="banger")],
            user_id="default",
        )

        result = await repo.list_tags(user_id="default")

        assert result[0] == ("mood:chill", 3)
        assert ("banger", 1) in result

    async def test_list_tags_filters_by_query(self, db_session: AsyncSession) -> None:
        track = await _seed_track(db_session)
        repo = TrackTagRepository(db_session)
        await repo.add_tags(
            [
                make_track_tag(track_id=track.id, tag="mood:chill"),
                make_track_tag(track_id=track.id, tag="energy:high"),
                make_track_tag(track_id=track.id, tag="banger"),
            ],
            user_id="default",
        )

        result = await repo.list_tags(user_id="default", query="mood")

        assert [r[0] for r in result] == ["mood:chill"]

    async def test_count_by_tag(self, db_session: AsyncSession) -> None:
        tracks = [await _seed_track(db_session) for _ in range(3)]
        repo = TrackTagRepository(db_session)
        await repo.add_tags(
            [make_track_tag(track_id=t.id, tag="mood:chill") for t in tracks],
            user_id="default",
        )
        await repo.add_tags(
            [make_track_tag(track_id=tracks[0].id, tag="banger")],
            user_id="default",
        )

        counts = await repo.count_by_tag(user_id="default")

        assert counts["mood:chill"] == 3
        assert counts["banger"] == 1

    async def test_list_by_tagged_at_ordering_and_boundaries(
        self, db_session: AsyncSession
    ) -> None:
        repo = TrackTagRepository(db_session)
        base = datetime(2025, 6, 1, tzinfo=UTC)

        tags = []
        for i in range(5):
            track = await _seed_track(db_session)
            tags.append(
                make_track_tag(
                    track_id=track.id,
                    tag=f"context:day-{i}",
                    tagged_at=base + timedelta(days=i),
                )
            )
        await repo.add_tags(tags, user_id="default")

        result = await repo.list_by_tagged_at(user_id="default", limit=10)
        assert result[0].tag == "context:day-4"
        assert result[-1].tag == "context:day-0"

        cutoff = base + timedelta(days=2)
        after = await repo.list_by_tagged_at(user_id="default", after=cutoff)
        assert {t.tag for t in after} == {
            "context:day-2",
            "context:day-3",
            "context:day-4",
        }
