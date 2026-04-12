"""Integration tests for TrackRepository.list_tracks tag / namespace filters.

Locks in the AND/OR/namespace filter semantics — the critical subtlety
is that "and" mode must use GROUP BY + HAVING COUNT(DISTINCT) so the
subquery returns only tracks carrying EVERY listed tag.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.persistence.database.db_models import DBTrack
from src.infrastructure.persistence.repositories.track.core import TrackRepository
from src.infrastructure.persistence.repositories.track.tags import TrackTagRepository
from tests.fixtures import make_track_tag


async def _seed_track_with_tags(
    session: AsyncSession,
    *tags: str,
    user_id: str = "default",
) -> DBTrack:
    track = DBTrack(
        title=f"Track-{tags[0] if tags else 'none'}",
        artists={"names": ["Artist"]},
        user_id=user_id,
    )
    session.add(track)
    await session.flush()
    if tags:
        tag_repo = TrackTagRepository(session)
        await tag_repo.add_tags(
            [make_track_tag(track_id=track.id, tag=t, user_id=user_id) for t in tags],
            user_id=user_id,
        )
    return track


class TestTagFilter:
    async def test_or_mode_returns_union(self, db_session: AsyncSession) -> None:
        chill = await _seed_track_with_tags(db_session, "mood:chill")
        hype = await _seed_track_with_tags(db_session, "mood:hype")
        _unmatched = await _seed_track_with_tags(db_session, "banger")

        repo = TrackRepository(db_session)
        page = await repo.list_tracks(
            user_id="default",
            tags=["mood:chill", "mood:hype"],
            tag_mode="or",
        )

        ids = {t.id for t in page["tracks"]}
        assert ids == {chill.id, hype.id}

    async def test_and_mode_returns_intersection(
        self, db_session: AsyncSession
    ) -> None:
        both = await _seed_track_with_tags(db_session, "mood:chill", "energy:low")
        _only_one = await _seed_track_with_tags(db_session, "mood:chill")
        _other = await _seed_track_with_tags(db_session, "energy:low")

        repo = TrackRepository(db_session)
        page = await repo.list_tracks(
            user_id="default",
            tags=["mood:chill", "energy:low"],
            tag_mode="and",
        )

        assert [t.id for t in page["tracks"]] == [both.id]

    async def test_single_tag_and_mode(self, db_session: AsyncSession) -> None:
        """Single-tag filter works identically in both modes."""
        chill = await _seed_track_with_tags(db_session, "mood:chill")

        repo = TrackRepository(db_session)
        page = await repo.list_tracks(
            user_id="default",
            tags=["mood:chill"],
            tag_mode="and",
        )

        assert [t.id for t in page["tracks"]] == [chill.id]


class TestNamespaceFilter:
    async def test_namespace_matches_any_tag_in_namespace(
        self, db_session: AsyncSession
    ) -> None:
        chill = await _seed_track_with_tags(db_session, "mood:chill")
        hype = await _seed_track_with_tags(db_session, "mood:hype")
        _energy = await _seed_track_with_tags(db_session, "energy:high")

        repo = TrackRepository(db_session)
        page = await repo.list_tracks(user_id="default", namespace="mood")

        ids = {t.id for t in page["tracks"]}
        assert ids == {chill.id, hype.id}

    async def test_namespace_excludes_unnamespaced_tags(
        self, db_session: AsyncSession
    ) -> None:
        """``banger`` has namespace=None, so it doesn't match ``namespace="mood"``."""
        _banger = await _seed_track_with_tags(db_session, "banger")

        repo = TrackRepository(db_session)
        page = await repo.list_tracks(user_id="default", namespace="mood")

        assert page["tracks"] == []


class TestMultiUserIsolation:
    async def test_tag_filter_scoped_to_user(self, db_session: AsyncSession) -> None:
        alice_track = await _seed_track_with_tags(
            db_session, "mood:chill", user_id="alice"
        )
        _bob_track = await _seed_track_with_tags(
            db_session, "mood:chill", user_id="bob"
        )

        repo = TrackRepository(db_session)
        page = await repo.list_tracks(user_id="alice", tags=["mood:chill"])

        assert [t.id for t in page["tracks"]] == [alice_track.id]
