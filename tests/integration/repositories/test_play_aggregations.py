"""Integration coverage for ``TrackPlayRepository.get_play_aggregations``.

Every existing ``period_plays`` test mocks the repository, so the real SQL path
had never been executed — which is how the ``dict(Result)`` defect at the bottom
of the period branch survived. These tests drive actual Postgres.
"""

from datetime import UTC, datetime, timedelta

from src.domain.entities.operations import TrackPlay
from src.infrastructure.persistence.repositories.track.core import TrackRepository
from src.infrastructure.persistence.repositories.track.plays import TrackPlayRepository
from tests.fixtures import make_track


async def _seed_track_with_plays(db_session, played_at_offsets: list[int]):
    """Persist one track plus a play per day-offset (negative == days ago)."""
    track_repo = TrackRepository(db_session)
    track = await track_repo.save_track(make_track(id=None, title="Aggregated"))
    assert track.id is not None

    now = datetime.now(UTC)
    plays = [
        TrackPlay(
            track_id=track.id,
            user_id="default",
            played_at=now + timedelta(days=offset),
            service="lastfm",
        )
        for offset in played_at_offsets
    ]
    await TrackPlayRepository(db_session).bulk_insert_plays(plays)
    return track


class TestPeriodPlayAggregation:
    async def test_period_plays_returns_counts_in_window(self, db_session) -> None:
        """The regression guard: this branch raised TypeError before the fix."""
        track = await _seed_track_with_plays(db_session, [-1, -2, -20])
        repo = TrackPlayRepository(db_session)
        now = datetime.now(UTC)

        result = await repo.get_play_aggregations(
            [track.id],
            ["period_plays"],
            user_id="default",
            period_start=now - timedelta(days=7),
            period_end=now,
        )

        # Two plays fall inside the 7-day window; the 20-day-old one does not.
        assert result["period_plays"][track.id] == 2

    async def test_period_plays_backfills_tracks_with_no_plays(
        self, db_session
    ) -> None:
        track = await _seed_track_with_plays(db_session, [-30])
        repo = TrackPlayRepository(db_session)
        now = datetime.now(UTC)

        result = await repo.get_play_aggregations(
            [track.id],
            ["period_plays"],
            user_id="default",
            period_start=now - timedelta(days=7),
            period_end=now,
        )

        assert result["period_plays"][track.id] == 0

    async def test_total_and_period_together(self, db_session) -> None:
        """Both branches in one call — base metrics and the period query."""
        track = await _seed_track_with_plays(db_session, [-1, -2, -20])
        repo = TrackPlayRepository(db_session)
        now = datetime.now(UTC)

        result = await repo.get_play_aggregations(
            [track.id],
            ["total_plays", "period_plays"],
            user_id="default",
            period_start=now - timedelta(days=7),
            period_end=now,
        )

        assert result["total_plays"][track.id] == 3
        assert result["period_plays"][track.id] == 2
