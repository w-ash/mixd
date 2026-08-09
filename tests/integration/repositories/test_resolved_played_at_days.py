"""``get_resolved_played_at_days``: the full-history projection's pre-query.

One ``SELECT DISTINCT`` of UTC calendar days holding ≥1 resolved ledger row —
what lets the rebuild chunk only the days a sparse multi-year history actually
touches. Pins the three things the projection depends on: only *resolved* rows
count, days come back ascending and distinct, and the day boundary is UTC
regardless of the stored offset.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Final
from uuid import uuid4

from src.domain.entities import ConnectorTrackPlay
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_track


def _play(played_at: datetime, *, user_id: str, index: int) -> ConnectorTrackPlay:
    return ConnectorTrackPlay(
        service="lastfm",
        artist_name=f"TEST_DayArtist {index}",
        track_name=f"TEST_DayTrack {index}",
        played_at=played_at,
        ms_played=None,
        user_id=user_id,
        import_timestamp=datetime.now(UTC),
        import_source="lastfm_api",
        import_batch_id=f"TEST_{uuid4()}",
    )


_MARCH_9: Final = datetime(2024, 3, 9, 21, 0, tzinfo=UTC)


class TestResolvedPlayedAtDays:
    async def test_returns_ascending_distinct_days_of_resolved_rows_only(
        self, db_session
    ):
        user_id = f"TEST_days_{uuid4().hex[:8]}"
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_play_repository()
        track = await uow.get_track_repository().save_track(
            make_track(
                title="Come On in My Kitchen",
                artist="Robert Johnson",
                user_id=user_id,
                connector_track_identifiers={},
            )
        )

        resolved = [
            # Two plays on one day, one years earlier — inserted newest-first
            # to prove ordering comes from the query, not insertion.
            _play(_MARCH_9, user_id=user_id, index=0),
            _play(_MARCH_9 + timedelta(hours=1), user_id=user_id, index=1),
            _play(datetime(2011, 6, 2, 8, 30, tzinfo=UTC), user_id=user_id, index=2),
        ]
        unresolved = [
            _play(datetime(2020, 1, 1, 12, 0, tzinfo=UTC), user_id=user_id, index=3)
        ]
        _ = await repo.bulk_insert_connector_plays(resolved + unresolved)
        _ = await repo.bulk_update_resolution(
            [(row, track.id) for row in resolved], resolved_at=datetime.now(UTC)
        )

        days = await repo.get_resolved_played_at_days(user_id=user_id)

        # Ascending, distinct, and the unresolved 2020 row contributes nothing
        # — an unresolved day would make the rebuild fetch chunks that project
        # nothing.
        assert days == [date(2011, 6, 2), date(2024, 3, 9)]

    async def test_day_boundary_is_utc_not_the_stored_offset(self, db_session):
        """A play at 20:30-05:00 is 01:30 UTC the NEXT day — the projection's
        chunk cores are UTC midnights, so the query must agree."""
        user_id = f"TEST_days_{uuid4().hex[:8]}"
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_play_repository()
        track = await uow.get_track_repository().save_track(
            make_track(
                title="Hellhound on My Trail",
                artist="Robert Johnson",
                user_id=user_id,
                connector_track_identifiers={},
            )
        )
        row = _play(
            datetime(2024, 6, 15, 20, 30, tzinfo=timezone(timedelta(hours=-5))),
            user_id=user_id,
            index=0,
        )
        _ = await repo.bulk_insert_connector_plays([row])
        _ = await repo.bulk_update_resolution(
            [(row, track.id)], resolved_at=datetime.now(UTC)
        )

        assert await repo.get_resolved_played_at_days(user_id=user_id) == [
            date(2024, 6, 16)
        ]

    async def test_scoped_to_the_asking_tenant(self, db_session):
        user_a = f"TEST_days_{uuid4().hex[:8]}"
        user_b = f"TEST_days_{uuid4().hex[:8]}"
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_play_repository()
        track = await uow.get_track_repository().save_track(
            make_track(
                title="Cross Road Blues",
                artist="Robert Johnson",
                user_id=user_a,
                connector_track_identifiers={},
            )
        )
        row = _play(_MARCH_9, user_id=user_a, index=0)
        _ = await repo.bulk_insert_connector_plays([row])
        _ = await repo.bulk_update_resolution(
            [(row, track.id)], resolved_at=datetime.now(UTC)
        )

        assert await repo.get_resolved_played_at_days(user_id=user_a) == [
            date(2024, 3, 9)
        ]
        assert await repo.get_resolved_played_at_days(user_id=user_b) == []
