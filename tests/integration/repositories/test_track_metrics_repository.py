"""Integration tests for TrackMetricsRepository's write path.

``track_metrics.user_id`` has no column default (v0.12.0.2), so the upsert
must write the tenant carried by each ``TrackMetric``; a save that omitted it
would fail with ``NotNullViolation``. Covers the write round-trip, the
``(track_id, connector_name, metric_type)`` upsert collapse, and the tenant
landing on the row the mapper reads back.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.track import ArtistCredit, Track, TrackMetric
from src.infrastructure.persistence.database.models import DBTrackMetric
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import TEST_USER_ID


async def _insert_track(db_session: AsyncSession, user_id: str) -> Track:
    track_repo = get_unit_of_work(db_session).get_track_repository()
    return await track_repo.save_track(
        Track(
            id=None,
            user_id=user_id,
            title=f"Track_{uuid7()}",
            artists=[ArtistCredit(credited_name="Test Artist")],
        )
    )


class TestSaveTrackMetrics:
    """Tenanted upsert round-trip."""

    async def test_round_trip_writes_the_entity_tenant(
        self, db_session: AsyncSession
    ) -> None:
        uow = get_unit_of_work(db_session)
        track = await _insert_track(db_session, TEST_USER_ID)
        collected_at = datetime.now(UTC) - timedelta(hours=1)
        metrics_repo = uow.get_metrics_repository()

        saved = await metrics_repo.save_track_metrics([
            TrackMetric(
                track_id=track.id,
                connector_name="lastfm",
                metric_type="lastfm_user_playcount",
                value=42.0,
                user_id=TEST_USER_ID,
                collected_at=collected_at,
            )
        ])

        assert saved == 1
        row = (
            await db_session.execute(
                select(DBTrackMetric).where(DBTrackMetric.track_id == track.id)
            )
        ).scalar_one()
        assert row.user_id == TEST_USER_ID
        assert row.value == 42.0
        cached = await metrics_repo.get_track_metrics(
            [track.id],
            metric_type="lastfm_user_playcount",
            connector="lastfm",
            max_age_hours=24,
        )
        assert cached == {track.id: 42.0}

    async def test_rows_are_tenanted_per_entity(self, db_session: AsyncSession) -> None:
        """A batch spanning tenants writes each row under its own entity's tenant."""
        uow = get_unit_of_work(db_session)
        other_user = f"metrics-user-{uuid7()}"
        mine = await _insert_track(db_session, TEST_USER_ID)
        theirs = await _insert_track(db_session, other_user)

        await uow.get_metrics_repository().save_track_metrics([
            TrackMetric(
                track_id=mine.id,
                connector_name="lastfm",
                metric_type="lastfm_user_playcount",
                value=1.0,
                user_id=TEST_USER_ID,
            ),
            TrackMetric(
                track_id=theirs.id,
                connector_name="lastfm",
                metric_type="lastfm_user_playcount",
                value=2.0,
                user_id=other_user,
            ),
        ])

        rows = (
            await db_session.execute(
                select(DBTrackMetric.track_id, DBTrackMetric.user_id).where(
                    DBTrackMetric.track_id.in_([mine.id, theirs.id])
                )
            )
        ).all()
        assert dict(rows) == {mine.id: TEST_USER_ID, theirs.id: other_user}

    async def test_repeat_sample_collapses_to_latest(
        self, db_session: AsyncSession
    ) -> None:
        uow = get_unit_of_work(db_session)
        track = await _insert_track(db_session, TEST_USER_ID)
        metrics_repo = uow.get_metrics_repository()
        first = datetime(2026, 9, 1, tzinfo=UTC)

        def sample(value: float, at: datetime) -> TrackMetric:
            return TrackMetric(
                track_id=track.id,
                connector_name="lastfm",
                metric_type="lastfm_user_playcount",
                value=value,
                user_id=TEST_USER_ID,
                collected_at=at,
            )

        await metrics_repo.save_track_metrics([sample(1.0, first)])
        await metrics_repo.save_track_metrics([sample(2.0, first + timedelta(hours=1))])

        rows = (
            (
                await db_session.execute(
                    select(DBTrackMetric).where(DBTrackMetric.track_id == track.id)
                )
            )
            .scalars()
            .all()
        )
        assert [(r.value, r.user_id) for r in rows] == [(2.0, TEST_USER_ID)]
