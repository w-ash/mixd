"""Integration tests for TrackTagRepository.

Tests real database operations for the tag system — batch insert with
ON CONFLICT DO NOTHING semantics, batch delete with RETURNING, 3-part
UNIQUE enforcement, event logging, autocomplete, multi-user isolation,
and ON DELETE CASCADE — using the db_session fixture with testcontainers
PostgreSQL.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.tag import TagEvent
from src.infrastructure.persistence.database.db_models import (
    DBTrackTag,
    DBTrackTagEvent,
)
from src.infrastructure.persistence.repositories.track.tags import TrackTagRepository
from tests.fixtures import make_track_tag, seed_db_track


class TestAddTags:
    """INSERT ... ON CONFLICT DO NOTHING ... RETURNING semantics."""

    async def test_insert_single_tag(self, db_session: AsyncSession) -> None:
        track = await seed_db_track(db_session)
        repo = TrackTagRepository(db_session)

        result = await repo.add_tags(
            [make_track_tag(track_id=track.id)], user_id="default"
        )

        assert len(result) == 1
        assert result[0].tag == "mood:chill"
        assert result[0].namespace == "mood"
        assert result[0].value == "chill"

    async def test_batch_insert_distinct_tags(self, db_session: AsyncSession) -> None:
        track = await seed_db_track(db_session)
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
        track = await seed_db_track(db_session)
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
        track = await seed_db_track(db_session)
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
        tracks = [await seed_db_track(db_session) for _ in range(2)]
        repo = TrackTagRepository(db_session)

        result = await repo.add_tags(
            [make_track_tag(track_id=t.id, tag="mood:chill") for t in tracks],
            user_id="default",
        )

        assert len(result) == 2

    async def test_multi_user_isolation(self, db_session: AsyncSession) -> None:
        """UNIQUE(user_id, track_id, tag): different users share track+tag."""
        track = await seed_db_track(db_session)
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
        track = await seed_db_track(db_session)
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
        track = await seed_db_track(db_session)
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
        track = await seed_db_track(db_session)
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
        track = await seed_db_track(db_session)
        repo = TrackTagRepository(db_session)

        assert await repo.get_tags([track.id], user_id="default") == {}

    async def test_empty_input_returns_empty(self, db_session: AsyncSession) -> None:
        repo = TrackTagRepository(db_session)
        assert await repo.get_tags([], user_id="default") == {}


class TestEventsAndCascade:
    """Append-only event log and ON DELETE CASCADE behavior."""

    async def test_add_events_bulk(self, db_session: AsyncSession) -> None:
        track = await seed_db_track(db_session)
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
        track = await seed_db_track(db_session)
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
    """Aggregate and date-range read paths: list_tags / list_by_tagged_at."""

    async def test_list_tags_sorted_by_count_desc(
        self, db_session: AsyncSession
    ) -> None:
        tracks = [await seed_db_track(db_session) for _ in range(3)]
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
        result_by_tag = {tag: (count, last) for tag, count, last in result}

        assert result[0][0] == "mood:chill"
        assert result_by_tag["mood:chill"][0] == 3
        assert result_by_tag["banger"][0] == 1

    async def test_list_tags_filters_by_query(self, db_session: AsyncSession) -> None:
        track = await seed_db_track(db_session)
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

    async def test_list_tags_returns_last_used_at(
        self, db_session: AsyncSession
    ) -> None:
        """``last_used_at`` is the max ``tagged_at`` across rows for that tag."""
        repo = TrackTagRepository(db_session)
        old = datetime(2025, 1, 1, tzinfo=UTC)
        recent = datetime(2026, 4, 1, tzinfo=UTC)

        track_a = await seed_db_track(db_session)
        track_b = await seed_db_track(db_session)
        await repo.add_tags(
            [
                make_track_tag(track_id=track_a.id, tag="mood:chill", tagged_at=old),
                make_track_tag(track_id=track_b.id, tag="mood:chill", tagged_at=recent),
            ],
            user_id="default",
        )

        result = await repo.list_tags(user_id="default")
        last_used = {tag: last for tag, _, last in result}
        assert last_used["mood:chill"] == recent

    async def test_list_by_tagged_at_ordering_and_boundaries(
        self, db_session: AsyncSession
    ) -> None:
        repo = TrackTagRepository(db_session)
        base = datetime(2025, 6, 1, tzinfo=UTC)

        tags = []
        for i in range(5):
            track = await seed_db_track(db_session)
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


class TestRenameTag:
    """Bulk rename across all of a user's tracks; idempotent on existing target."""

    async def test_rename_moves_all_rows(self, db_session: AsyncSession) -> None:
        tracks = [await seed_db_track(db_session) for _ in range(3)]
        repo = TrackTagRepository(db_session)
        await repo.add_tags(
            [make_track_tag(track_id=t.id, tag="mood:chill") for t in tracks],
            user_id="default",
        )

        affected = await repo.rename_tag(
            user_id="default", source="mood:chill", target="mood:ambient"
        )

        assert affected == 3
        new_rows = await db_session.scalars(
            select(DBTrackTag).where(DBTrackTag.tag == "mood:ambient")
        )
        old_rows = await db_session.scalars(
            select(DBTrackTag).where(DBTrackTag.tag == "mood:chill")
        )
        assert len(new_rows.all()) == 3
        assert len(old_rows.all()) == 0

    async def test_rename_normalizes_inputs(self, db_session: AsyncSession) -> None:
        track = await seed_db_track(db_session)
        repo = TrackTagRepository(db_session)
        await repo.add_tags(
            [make_track_tag(track_id=track.id, tag="mood:chill")],
            user_id="default",
        )

        affected = await repo.rename_tag(
            user_id="default", source="Mood:Chill", target="Mood:Ambient"
        )

        assert affected == 1
        rows = await db_session.scalars(
            select(DBTrackTag).where(DBTrackTag.user_id == "default")
        )
        rows_list = rows.all()
        assert len(rows_list) == 1
        assert rows_list[0].tag == "mood:ambient"

    async def test_rename_idempotent_when_target_exists(
        self, db_session: AsyncSession
    ) -> None:
        """Tracks already carrying the target lose the source row without duplication."""
        tracks = [await seed_db_track(db_session) for _ in range(3)]
        repo = TrackTagRepository(db_session)
        # All 3 tracks have source; track[0] also has target already.
        await repo.add_tags(
            [make_track_tag(track_id=t.id, tag="mood:chill") for t in tracks],
            user_id="default",
        )
        await repo.add_tags(
            [make_track_tag(track_id=tracks[0].id, tag="mood:ambient")],
            user_id="default",
        )

        affected = await repo.rename_tag(
            user_id="default", source="mood:chill", target="mood:ambient"
        )

        assert affected == 3
        # Final state: each track has exactly one mood:ambient, no mood:chill.
        rows_per_track = await repo.get_tags([t.id for t in tracks], user_id="default")
        for t in tracks:
            tag_set = {r.tag for r in rows_per_track[t.id]}
            assert tag_set == {"mood:ambient"}

    async def test_rename_writes_remove_and_add_events(
        self, db_session: AsyncSession
    ) -> None:
        tracks = [await seed_db_track(db_session) for _ in range(2)]
        repo = TrackTagRepository(db_session)
        await repo.add_tags(
            [make_track_tag(track_id=t.id, tag="mood:chill") for t in tracks],
            user_id="default",
        )

        await repo.rename_tag(
            user_id="default", source="mood:chill", target="mood:ambient"
        )

        events = (
            await db_session.scalars(
                select(DBTrackTagEvent).where(DBTrackTagEvent.user_id == "default")
            )
        ).all()
        # 2 tracks × (1 remove for source + 1 add for target) = 4 events.
        actions_by_tag = [(e.action, e.tag) for e in events]
        assert actions_by_tag.count(("remove", "mood:chill")) == 2
        assert actions_by_tag.count(("add", "mood:ambient")) == 2

    async def test_rename_skips_add_event_when_target_already_present(
        self, db_session: AsyncSession
    ) -> None:
        """If a track already had target, rename writes only the `remove` event for it."""
        track = await seed_db_track(db_session)
        repo = TrackTagRepository(db_session)
        await repo.add_tags(
            [
                make_track_tag(track_id=track.id, tag="mood:chill"),
                make_track_tag(track_id=track.id, tag="mood:ambient"),
            ],
            user_id="default",
        )

        await repo.rename_tag(
            user_id="default", source="mood:chill", target="mood:ambient"
        )

        events = (
            await db_session.scalars(
                select(DBTrackTagEvent).where(DBTrackTagEvent.user_id == "default")
            )
        ).all()
        actions_by_tag = [(e.action, e.tag) for e in events]
        assert ("remove", "mood:chill") in actions_by_tag
        assert ("add", "mood:ambient") not in actions_by_tag

    async def test_rename_no_op_on_missing_source(
        self, db_session: AsyncSession
    ) -> None:
        repo = TrackTagRepository(db_session)
        affected = await repo.rename_tag(
            user_id="default", source="never:tagged", target="anywhere"
        )
        assert affected == 0

    async def test_rename_no_op_when_source_equals_target(
        self, db_session: AsyncSession
    ) -> None:
        track = await seed_db_track(db_session)
        repo = TrackTagRepository(db_session)
        await repo.add_tags(
            [make_track_tag(track_id=track.id, tag="mood:chill")],
            user_id="default",
        )

        affected = await repo.rename_tag(
            user_id="default", source="mood:chill", target="mood:chill"
        )

        assert affected == 0

    async def test_rename_isolated_per_user(self, db_session: AsyncSession) -> None:
        track = await seed_db_track(db_session, user_id="alice")
        repo = TrackTagRepository(db_session)
        await repo.add_tags(
            [make_track_tag(track_id=track.id, tag="mood:chill", user_id="alice")],
            user_id="alice",
        )
        await repo.add_tags(
            [make_track_tag(track_id=track.id, tag="mood:chill", user_id="bob")],
            user_id="bob",
        )

        affected = await repo.rename_tag(
            user_id="alice", source="mood:chill", target="mood:ambient"
        )

        assert affected == 1
        bob_tags = await repo.get_tags([track.id], user_id="bob")
        assert {t.tag for t in bob_tags[track.id]} == {"mood:chill"}


class TestMergeTags:
    """Same primitive as rename — a thin alias verified once for parity."""

    async def test_merge_collapses_into_existing_target(
        self, db_session: AsyncSession
    ) -> None:
        tracks = [await seed_db_track(db_session) for _ in range(3)]
        repo = TrackTagRepository(db_session)
        await repo.add_tags(
            [make_track_tag(track_id=t.id, tag="context:gym") for t in tracks],
            user_id="default",
        )
        await repo.add_tags(
            [make_track_tag(track_id=tracks[0].id, tag="context:workout")],
            user_id="default",
        )

        affected = await repo.merge_tags(
            user_id="default", source="context:gym", target="context:workout"
        )

        assert affected == 3
        for t in tracks:
            tags = await repo.get_tags([t.id], user_id="default")
            assert {tag.tag for tag in tags[t.id]} == {"context:workout"}


class TestDeleteTag:
    """Bulk delete + cascade to event log."""

    async def test_delete_removes_all_rows_for_tag(
        self, db_session: AsyncSession
    ) -> None:
        tracks = [await seed_db_track(db_session) for _ in range(3)]
        repo = TrackTagRepository(db_session)
        await repo.add_tags(
            [make_track_tag(track_id=t.id, tag="TODO:check") for t in tracks],
            user_id="default",
        )
        await repo.add_tags(
            [make_track_tag(track_id=tracks[0].id, tag="mood:chill")],
            user_id="default",
        )

        affected = await repo.delete_tag(user_id="default", tag="TODO:check")

        assert affected == 3
        remaining = (
            await db_session.scalars(
                select(DBTrackTag).where(DBTrackTag.user_id == "default")
            )
        ).all()
        assert {r.tag for r in remaining} == {"mood:chill"}

    async def test_delete_cascades_to_event_log(self, db_session: AsyncSession) -> None:
        track = await seed_db_track(db_session)
        repo = TrackTagRepository(db_session)
        await repo.add_tags(
            [make_track_tag(track_id=track.id, tag="mood:chill")],
            user_id="default",
        )
        # Seed an event row for this tag (typically written by add_tags callers).
        await repo.add_events(
            [
                TagEvent(
                    user_id="default",
                    track_id=track.id,
                    tag="mood:chill",
                    action="add",
                    source="manual",
                    tagged_at=datetime.now(UTC),
                )
            ],
            user_id="default",
        )

        await repo.delete_tag(user_id="default", tag="mood:chill")

        # Both the tag rows AND the event rows should be gone.
        remaining_events = (
            await db_session.scalars(
                select(DBTrackTagEvent).where(
                    DBTrackTagEvent.user_id == "default",
                    DBTrackTagEvent.tag == "mood:chill",
                )
            )
        ).all()
        assert remaining_events == []

    async def test_delete_no_op_on_missing_tag(self, db_session: AsyncSession) -> None:
        repo = TrackTagRepository(db_session)
        affected = await repo.delete_tag(user_id="default", tag="never:tagged")
        assert affected == 0

    async def test_delete_isolated_per_user(self, db_session: AsyncSession) -> None:
        track = await seed_db_track(db_session, user_id="alice")
        repo = TrackTagRepository(db_session)
        await repo.add_tags(
            [make_track_tag(track_id=track.id, tag="mood:chill", user_id="alice")],
            user_id="alice",
        )
        await repo.add_tags(
            [make_track_tag(track_id=track.id, tag="mood:chill", user_id="bob")],
            user_id="bob",
        )

        affected = await repo.delete_tag(user_id="alice", tag="mood:chill")

        assert affected == 1
        bob_tags = await repo.get_tags([track.id], user_id="bob")
        assert {t.tag for t in bob_tags[track.id]} == {"mood:chill"}
