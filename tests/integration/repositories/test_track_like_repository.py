"""Integration tests for TrackLikeRepository.

Likes are presence rows: liking upserts a ``(user, track, service)`` row and
unliking deletes it. Covers the like/unlike round-trip, the grouped counts
(one GROUP BY, every requested service present, scoped to the owning user),
the per-track service sets, and the source-minus-target anti-join.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

from src.domain.entities.track import Artist, Track
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


def _new_track(user_id: str) -> Track:
    """Track with id=None for DB insertion (repo assigns the ID)."""
    return Track(
        id=None,
        user_id=user_id,
        title=f"Track_{uuid7()}",
        artists=[Artist(name="Test Artist")],
    )


class TestLikeUnlikeRoundTrip:
    """A like is a row; an unlike removes it."""

    async def test_like_then_unlike_is_insert_then_delete(self, db_session):
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        like_repo = uow.get_like_repository()

        user_id = f"rt-user-{uuid7()}"
        track = await track_repo.save_track(_new_track(user_id))

        saved = await like_repo.save_track_likes_batch(
            [(track.id, "spotify", None)], user_id=user_id
        )
        assert [like.service for like in saved] == ["spotify"]
        assert saved[0].liked_at is not None
        assert await like_repo.get_track_likes(track.id, user_id=user_id)

        deleted = await like_repo.delete_track_likes_batch(
            [(track.id, "spotify")], user_id=user_id
        )
        assert deleted == 1
        assert await like_repo.get_track_likes(track.id, user_id=user_id) == []

    async def test_relike_is_an_upsert_on_the_same_row(self, db_session):
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        like_repo = uow.get_like_repository()

        user_id = f"rt-user-{uuid7()}"
        track = await track_repo.save_track(_new_track(user_id))
        liked_at = datetime(2024, 1, 1, tzinfo=UTC)

        await like_repo.save_track_likes_batch(
            [(track.id, "spotify", None)], user_id=user_id
        )
        await like_repo.save_track_likes_batch(
            [(track.id, "spotify", liked_at)], user_id=user_id
        )

        likes = await like_repo.get_track_likes(track.id, user_id=user_id)
        assert len(likes) == 1
        assert likes[0].liked_at == liked_at

    async def test_delete_ignores_missing_rows_and_other_users(self, db_session):
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        like_repo = uow.get_like_repository()

        owner = f"rt-owner-{uuid7()}"
        track = await track_repo.save_track(_new_track(owner))
        await like_repo.save_track_likes_batch(
            [(track.id, "spotify", None)], user_id=owner
        )

        assert (
            await like_repo.delete_track_likes_batch(
                [(track.id, "spotify")], user_id=f"rt-other-{uuid7()}"
            )
            == 0
        )
        assert (
            await like_repo.delete_track_likes_batch(
                [(track.id, "lastfm")], user_id=owner
            )
            == 0
        )
        assert await like_repo.delete_track_likes_batch([], user_id=owner) == 0
        assert len(await like_repo.get_track_likes(track.id, user_id=owner)) == 1


class TestGetLikedStatusBatch:
    """Per-track set of services that carry a like row."""

    async def test_returns_services_with_rows_only(self, db_session):
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        like_repo = uow.get_like_repository()

        user_id = f"status-user-{uuid7()}"
        liked = await track_repo.save_track(_new_track(user_id))
        unliked = await track_repo.save_track(_new_track(user_id))
        await like_repo.save_track_likes_batch(
            [(liked.id, "spotify", None), (liked.id, "mixd", None)],
            user_id=user_id,
        )

        status = await like_repo.get_liked_status_batch(
            [liked.id, unliked.id], ["spotify", "mixd", "lastfm"], user_id=user_id
        )

        assert status == {liked.id: {"spotify", "mixd"}}


class TestGetUnsyncedLikes:
    """Source likes with no matching target row (anti-join)."""

    async def test_returns_source_likes_missing_on_target(self, db_session):
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        like_repo = uow.get_like_repository()

        user_id = f"unsync-user-{uuid7()}"
        synced = await track_repo.save_track(_new_track(user_id))
        pending = await track_repo.save_track(_new_track(user_id))
        await like_repo.save_track_likes_batch(
            [
                (synced.id, "mixd", None),
                (synced.id, "lastfm", None),
                (pending.id, "mixd", None),
            ],
            user_id=user_id,
        )

        unsynced = await like_repo.get_unsynced_likes("mixd", "lastfm", user_id=user_id)

        assert [like.track_id for like in unsynced] == [pending.id]

    async def test_since_timestamp_excludes_older_source_rows(self, db_session):
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        like_repo = uow.get_like_repository()

        user_id = f"unsync-user-{uuid7()}"
        track = await track_repo.save_track(_new_track(user_id))
        await like_repo.save_track_likes_batch(
            [(track.id, "mixd", None)], user_id=user_id
        )

        future = datetime.now(UTC) + timedelta(days=1)
        assert (
            await like_repo.get_unsynced_likes(
                "mixd", "lastfm", user_id=user_id, since_timestamp=future
            )
            == []
        )

    async def test_other_users_target_rows_do_not_count(self, db_session):
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        like_repo = uow.get_like_repository()

        owner = f"unsync-owner-{uuid7()}"
        other = f"unsync-other-{uuid7()}"
        track = await track_repo.save_track(_new_track(owner))
        await like_repo.save_track_likes_batch(
            [(track.id, "mixd", None)], user_id=owner
        )
        await like_repo.save_track_likes_batch(
            [(track.id, "lastfm", None)], user_id=other
        )

        unsynced = await like_repo.get_unsynced_likes("mixd", "lastfm", user_id=owner)

        assert [like.track_id for like in unsynced] == [track.id]


class TestCountLikedTracksByService:
    """Grouped like counts across several services in one query."""

    async def test_counts_per_service(self, db_session):
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        like_repo = uow.get_like_repository()

        user_id = f"count-user-{uuid7()}"
        first = await track_repo.save_track(_new_track(user_id))
        second = await track_repo.save_track(_new_track(user_id))

        await like_repo.save_track_likes_batch(
            [
                (first.id, "spotify", None),
                (second.id, "spotify", None),
                (first.id, "lastfm", None),
            ],
            user_id=user_id,
        )

        counts = await like_repo.count_liked_tracks_by_service(
            ["spotify", "lastfm"], user_id=user_id
        )

        assert counts == {"spotify": 2, "lastfm": 1}

    async def test_service_without_likes_maps_to_zero(self, db_session):
        """A service absent from the GROUP BY is still a key, valued 0."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        like_repo = uow.get_like_repository()

        user_id = f"count-user-{uuid7()}"
        track = await track_repo.save_track(_new_track(user_id))
        await like_repo.save_track_likes_batch(
            [(track.id, "spotify", None)], user_id=user_id
        )

        counts = await like_repo.count_liked_tracks_by_service(
            ["spotify", "tidal"], user_id=user_id
        )

        assert counts == {"spotify": 1, "tidal": 0}

    async def test_deleted_like_is_absent_from_counts(self, db_session):
        """An unliked track has no row, so it does not count."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        like_repo = uow.get_like_repository()

        user_id = f"count-user-{uuid7()}"
        track = await track_repo.save_track(_new_track(user_id))
        await like_repo.save_track_likes_batch(
            [(track.id, "spotify", None)], user_id=user_id
        )
        await like_repo.delete_track_likes_batch(
            [(track.id, "spotify")], user_id=user_id
        )

        assert await like_repo.count_liked_tracks_by_service(
            ["spotify"], user_id=user_id
        ) == {"spotify": 0}
        assert await like_repo.count_liked_tracks("spotify", user_id=user_id) == 0

    async def test_scoped_to_user(self, db_session):
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        like_repo = uow.get_like_repository()

        owner = f"count-owner-{uuid7()}"
        track = await track_repo.save_track(_new_track(owner))
        await like_repo.save_track_likes_batch(
            [(track.id, "spotify", None)], user_id=owner
        )

        counts = await like_repo.count_liked_tracks_by_service(
            ["spotify"], user_id=f"count-other-{uuid7()}"
        )

        assert counts == {"spotify": 0}

    async def test_empty_services_short_circuits(self, db_session):
        uow = get_unit_of_work(db_session)
        like_repo = uow.get_like_repository()

        assert (
            await like_repo.count_liked_tracks_by_service([], user_id="default") == {}
        )
