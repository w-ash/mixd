"""Integration coverage for the denormalized play-aggregate columns on ``tracks``.

``recompute_track_play_aggregates`` is the sole writer of ``play_count`` /
``first_played_at`` / ``last_played_at``. These tests drive it against real
Postgres three ways: the repository method directly, through the play
projection, and out through the track repository's domain mapping.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete as sa_delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.play_projection_service import PlayProjectionService
from src.domain.entities import ConnectorTrackPlay, Track
from src.domain.entities.operations import TrackPlay
from src.infrastructure.persistence.database.db_models import DBTrack, DBTrackPlay
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from src.infrastructure.persistence.repositories.track.core import TrackRepository
from src.infrastructure.persistence.repositories.track.plays import TrackPlayRepository
from tests.fixtures import make_track

_BASE = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


async def _seed_track(
    session: AsyncSession, *, user_id: str = "default", title: str | None = None
) -> Track:
    track = await TrackRepository(session).save_track(
        make_track(id=None, title=title or f"Agg {uuid4().hex[:8]}", user_id=user_id)
    )
    assert track.id is not None
    return track


async def _insert_plays(
    session: AsyncSession,
    track_id: UUID,
    played_ats: list[datetime],
    *,
    user_id: str = "default",
) -> None:
    plays = [
        TrackPlay(track_id=track_id, user_id=user_id, played_at=at, service="lastfm")
        for at in played_ats
    ]
    _ = await TrackPlayRepository(session).bulk_insert_plays(plays)


async def _stored_aggregates(
    session: AsyncSession, track_id: UUID
) -> tuple[int, datetime | None, datetime | None]:
    return (
        (
            await session.execute(
                select(
                    DBTrack.play_count, DBTrack.first_played_at, DBTrack.last_played_at
                ).where(DBTrack.id == track_id)
            )
        )
        .tuples()
        .one()
    )


class TestRecomputeDirect:
    """The repository method against seeded tracks and plays."""

    async def test_sets_count_first_and_last(self, db_session: AsyncSession) -> None:
        track = await _seed_track(db_session)
        assert track.id is not None
        played = [_BASE, _BASE + timedelta(days=1), _BASE + timedelta(days=2)]
        await _insert_plays(db_session, track.id, played)

        affected = await TrackPlayRepository(
            db_session
        ).recompute_track_play_aggregates([track.id], user_id="default")

        assert affected == 1
        assert await _stored_aggregates(db_session, track.id) == (
            3,
            _BASE,
            _BASE + timedelta(days=2),
        )

    async def test_zeroes_out_after_plays_deleted(
        self, db_session: AsyncSession
    ) -> None:
        track = await _seed_track(db_session)
        assert track.id is not None
        await _insert_plays(db_session, track.id, [_BASE, _BASE + timedelta(hours=1)])
        repo = TrackPlayRepository(db_session)
        _ = await repo.recompute_track_play_aggregates([track.id], user_id="default")

        _ = await db_session.execute(
            sa_delete(DBTrackPlay).where(DBTrackPlay.track_id == track.id)
        )
        affected = await repo.recompute_track_play_aggregates(
            [track.id], user_id="default"
        )

        assert affected == 1
        assert await _stored_aggregates(db_session, track.id) == (0, None, None)

    async def test_never_played_track_stays_clean_and_untouched(
        self, db_session: AsyncSession
    ) -> None:
        """The zero-out arm skips rows already at 0/NULL — no phantom writes."""
        track = await _seed_track(db_session)
        assert track.id is not None

        affected = await TrackPlayRepository(
            db_session
        ).recompute_track_play_aggregates([track.id], user_id="default")

        assert affected == 0
        assert await _stored_aggregates(db_session, track.id) == (0, None, None)

    async def test_empty_track_ids_is_a_noop(self, db_session: AsyncSession) -> None:
        affected = await TrackPlayRepository(
            db_session
        ).recompute_track_play_aggregates([], user_id="default")
        assert affected == 0

    async def test_none_rederives_whole_library_matching_group_by(
        self, db_session: AsyncSession
    ) -> None:
        """``track_ids=None`` must converge on exactly a fresh GROUP BY."""
        user = f"TEST_agg_lib_{uuid4().hex[:8]}"
        played_a = await _seed_track(db_session, user_id=user)
        played_b = await _seed_track(db_session, user_id=user)
        stale = await _seed_track(db_session, user_id=user)
        assert played_a.id
        assert played_b.id
        assert stale.id
        await _insert_plays(
            db_session,
            played_a.id,
            [_BASE, _BASE + timedelta(days=3), _BASE + timedelta(days=7)],
            user_id=user,
        )
        await _insert_plays(
            db_session, played_b.id, [_BASE + timedelta(days=1)], user_id=user
        )
        # Plant stale aggregates on the unplayed track — the whole-library
        # pass must heal it via the zero-out arm.
        _ = await db_session.execute(
            update(DBTrack)
            .where(DBTrack.id == stale.id)
            .values(play_count=7, last_played_at=_BASE)
        )

        affected = await TrackPlayRepository(
            db_session
        ).recompute_track_play_aggregates(None, user_id=user)

        assert affected == 3  # two set, one zeroed
        expected = {
            row[0]: (row[1], row[2], row[3])
            for row in (
                await db_session.execute(
                    select(
                        DBTrackPlay.track_id,
                        func.count(),
                        func.min(DBTrackPlay.played_at),
                        func.max(DBTrackPlay.played_at),
                    )
                    .where(DBTrackPlay.user_id == user)
                    .group_by(DBTrackPlay.track_id)
                )
            )
            .tuples()
            .all()
        }
        assert (
            await _stored_aggregates(db_session, played_a.id) == expected[played_a.id]
        )
        assert (
            await _stored_aggregates(db_session, played_b.id) == expected[played_b.id]
        )
        assert await _stored_aggregates(db_session, stale.id) == (0, None, None)

    async def test_scoped_to_user(self, db_session: AsyncSession) -> None:
        """Neither the count nor the zero-out arm may cross tenants."""
        user_a = f"TEST_agg_a_{uuid4().hex[:8]}"
        user_b = f"TEST_agg_b_{uuid4().hex[:8]}"
        track_a = await _seed_track(db_session, user_id=user_a)
        track_b = await _seed_track(db_session, user_id=user_b)
        assert track_a.id
        assert track_b.id
        await _insert_plays(
            db_session, track_a.id, [_BASE, _BASE + timedelta(days=1)], user_id=user_a
        )
        # A cross-tenant play pointing at A's track must not inflate A's count.
        await _insert_plays(
            db_session, track_a.id, [_BASE + timedelta(days=2)], user_id=user_b
        )
        await _insert_plays(
            db_session,
            track_b.id,
            [_BASE, _BASE + timedelta(days=4), _BASE + timedelta(days=5)],
            user_id=user_b,
        )
        repo = TrackPlayRepository(db_session)
        _ = await repo.recompute_track_play_aggregates([track_b.id], user_id=user_b)

        _ = await repo.recompute_track_play_aggregates(None, user_id=user_a)

        assert await _stored_aggregates(db_session, track_a.id) == (
            2,
            _BASE,
            _BASE + timedelta(days=1),
        )
        # B's aggregates survive A's whole-library pass unchanged.
        assert await _stored_aggregates(db_session, track_b.id) == (
            3,
            _BASE,
            _BASE + timedelta(days=5),
        )


class TestAggregatesThroughProjection:
    """The projection's per-chunk recompute, driven from the ledger."""

    @staticmethod
    def _observation(user_id: str, track_name: str) -> ConnectorTrackPlay:
        return ConnectorTrackPlay(
            service="lastfm",
            artist_name="Carwash",
            track_name=track_name,
            played_at=_BASE,
            ms_played=None,
            user_id=user_id,
            import_timestamp=datetime.now(UTC),
            import_source="lastfm_api",
            import_batch_id=f"TEST_{uuid4()}",
        )

    async def test_projection_sets_aggregates(self, db_session: AsyncSession) -> None:
        user = f"TEST_agg_proj_{uuid4().hex[:8]}"
        uow = get_unit_of_work(db_session)
        track = await _seed_track(db_session, user_id=user)
        assert track.id is not None
        observation = self._observation(user, "Projected")
        connector_repo = uow.get_connector_play_repository()
        _ = await connector_repo.bulk_insert_connector_plays([observation])
        _ = await connector_repo.bulk_update_resolution(
            [(observation, track.id)], resolved_at=datetime.now(UTC)
        )

        _ = await PlayProjectionService().project_observed_days(
            uow, user_id=user, played_at=[_BASE]
        )

        assert await _stored_aggregates(db_session, track.id) == (1, _BASE, _BASE)

    async def test_reresolution_moves_aggregates_between_tracks(
        self, db_session: AsyncSession
    ) -> None:
        """Re-pointing a play must zero the old track and set the new one."""
        user = f"TEST_agg_move_{uuid4().hex[:8]}"
        uow = get_unit_of_work(db_session)
        old = await _seed_track(db_session, user_id=user)
        new = await _seed_track(db_session, user_id=user)
        assert old.id is not None
        assert new.id is not None
        observation = self._observation(user, "Moved")
        connector_repo = uow.get_connector_play_repository()
        _ = await connector_repo.bulk_insert_connector_plays([observation])
        _ = await connector_repo.bulk_update_resolution(
            [(observation, old.id)], resolved_at=datetime.now(UTC)
        )
        service = PlayProjectionService()
        _ = await service.project_observed_days(uow, user_id=user, played_at=[_BASE])
        assert await _stored_aggregates(db_session, old.id) == (1, _BASE, _BASE)

        _ = await connector_repo.bulk_update_resolution(
            [(observation, new.id)], resolved_at=datetime.now(UTC)
        )
        _ = await service.project_observed_days(uow, user_id=user, played_at=[_BASE])

        # The play now points at ``new`` — the aggregates must follow it.
        moved_to = (
            await db_session.execute(
                select(DBTrackPlay.track_id).where(DBTrackPlay.user_id == user)
            )
        ).scalar_one()
        assert moved_to == new.id
        assert await _stored_aggregates(db_session, old.id) == (0, None, None)
        assert await _stored_aggregates(db_session, new.id) == (1, _BASE, _BASE)


class TestDomainEntityExposure:
    """The mapper carries the columns onto the domain ``Track``."""

    async def test_track_repository_exposes_aggregates(
        self, db_session: AsyncSession
    ) -> None:
        track = await _seed_track(db_session)
        assert track.id is not None
        await _insert_plays(db_session, track.id, [_BASE, _BASE + timedelta(days=2)])
        _ = await TrackPlayRepository(db_session).recompute_track_play_aggregates(
            [track.id], user_id="default"
        )

        # The recompute is a Core UPDATE — expire the identity map so the
        # reload sees the written columns rather than the flushed defaults.
        db_session.expire_all()
        loaded = await TrackRepository(db_session).get_by_id(track.id)

        assert loaded.play_count == 2
        assert loaded.first_played_at == _BASE
        assert loaded.last_played_at == _BASE + timedelta(days=2)
