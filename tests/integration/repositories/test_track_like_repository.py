"""Integration tests for TrackLikeRepository's grouped counting.

Covers count_liked_tracks_by_service: the per-service counts come from one
GROUP BY, every requested service appears in the result, and the counts stay
scoped to the owning user.
"""

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
                (first.id, "spotify", True, None, None),
                (second.id, "spotify", True, None, None),
                (first.id, "lastfm", True, None, None),
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
            [(track.id, "spotify", True, None, None)], user_id=user_id
        )

        counts = await like_repo.count_liked_tracks_by_service(
            ["spotify", "tidal"], user_id=user_id
        )

        assert counts == {"spotify": 1, "tidal": 0}

    async def test_unliked_rows_excluded(self, db_session):
        """is_liked=False rows do not count towards the liked total."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        like_repo = uow.get_like_repository()

        user_id = f"count-user-{uuid7()}"
        track = await track_repo.save_track(_new_track(user_id))
        await like_repo.save_track_likes_batch(
            [(track.id, "spotify", False, None, None)], user_id=user_id
        )

        assert await like_repo.count_liked_tracks_by_service(
            ["spotify"], user_id=user_id
        ) == {"spotify": 0}
        assert await like_repo.count_liked_tracks_by_service(
            ["spotify"], user_id=user_id, is_liked=False
        ) == {"spotify": 1}

    async def test_scoped_to_user(self, db_session):
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        like_repo = uow.get_like_repository()

        owner = f"count-owner-{uuid7()}"
        track = await track_repo.save_track(_new_track(owner))
        await like_repo.save_track_likes_batch(
            [(track.id, "spotify", True, None, None)], user_id=owner
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
