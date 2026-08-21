"""Integration coverage for play-event listing and histogram (v0.10.4)."""

from datetime import UTC, datetime, timedelta

from src.domain.entities.operations import TrackPlay
from src.infrastructure.persistence.repositories.track.core import TrackRepository
from src.infrastructure.persistence.repositories.track.plays import TrackPlayRepository
from tests.fixtures import make_track

_BASE = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


async def _seed_track(db_session, title="Played"):
    track = await TrackRepository(db_session).save_track(
        make_track(id=None, title=title)
    )
    assert track.id is not None
    return track


async def _seed_plays(db_session, track_id, offsets_minutes, service="spotify"):
    plays = [
        TrackPlay(
            track_id=track_id,
            user_id="default",
            played_at=_BASE + timedelta(minutes=offset),
            service=service,
        )
        for offset in offsets_minutes
    ]
    _ = await TrackPlayRepository(db_session).bulk_insert_plays(plays)
    return plays


class TestListPlayEvents:
    async def test_orders_newest_first_and_pages_by_keyset(self, db_session) -> None:
        track = await _seed_track(db_session)
        await _seed_plays(db_session, track.id, [0, 10, 20, 30, 40])
        repo = TrackPlayRepository(db_session)

        page1, key1 = await repo.list_play_events(user_id="default", limit=2)
        assert [p.played_at for p in page1] == [
            _BASE + timedelta(minutes=40),
            _BASE + timedelta(minutes=30),
        ]
        assert key1 == (page1[-1].played_at, page1[-1].id)

        page2, key2 = await repo.list_play_events(
            user_id="default", before=key1, limit=2
        )
        assert [p.played_at for p in page2] == [
            _BASE + timedelta(minutes=20),
            _BASE + timedelta(minutes=10),
        ]

        page3, key3 = await repo.list_play_events(
            user_id="default", before=key2, limit=2
        )
        assert len(page3) == 1
        assert key3 is None

    async def test_keyset_is_stable_while_newer_rows_insert(self, db_session) -> None:
        track = await _seed_track(db_session)
        await _seed_plays(db_session, track.id, [0, 10, 20, 30])
        repo = TrackPlayRepository(db_session)

        page1, key1 = await repo.list_play_events(user_id="default", limit=2)
        # A poll lands newer plays mid-walk; the cursor must not re-serve or
        # skip older rows.
        await _seed_plays(db_session, track.id, [50, 60])

        page2, _ = await repo.list_play_events(user_id="default", before=key1, limit=2)
        assert [p.played_at for p in page2] == [
            _BASE + timedelta(minutes=10),
            _BASE,
        ]
        seen = {p.id for p in page1} | {p.id for p in page2}
        assert len(seen) == 4

    async def test_filters_compose(self, db_session) -> None:
        track_a = await _seed_track(db_session, "A")
        track_b = await _seed_track(db_session, "B")
        await _seed_plays(db_session, track_a.id, [0, 10], service="spotify")
        await _seed_plays(db_session, track_a.id, [20], service="lastfm")
        await _seed_plays(db_session, track_b.id, [30], service="spotify")
        repo = TrackPlayRepository(db_session)

        by_track, _ = await repo.list_play_events(
            user_id="default", track_id=track_a.id
        )
        assert len(by_track) == 3

        narrowed, _ = await repo.list_play_events(
            user_id="default",
            track_id=track_a.id,
            service="spotify",
            since=_BASE + timedelta(minutes=5),
            until=_BASE + timedelta(minutes=25),
        )
        assert [p.played_at for p in narrowed] == [_BASE + timedelta(minutes=10)]


class TestPlayHistogram:
    async def test_daily_bins_match_hand_computed_counts(self, db_session) -> None:
        track = await _seed_track(db_session)
        # Three plays on day one, one on day three.
        day_minutes = 24 * 60
        await _seed_plays(db_session, track.id, [0, 60, 120, 2 * day_minutes + 30])
        repo = TrackPlayRepository(db_session)

        bins = await repo.get_play_histogram(user_id="default", bucket="day")

        assert [(b.date().isoformat(), c) for b, c in bins] == [
            ("2026-06-01", 3),
            ("2026-06-03", 1),
        ]

    async def test_histogram_honors_filters(self, db_session) -> None:
        track = await _seed_track(db_session)
        await _seed_plays(db_session, track.id, [0, 10], service="spotify")
        await _seed_plays(db_session, track.id, [20], service="lastfm")
        repo = TrackPlayRepository(db_session)

        bins = await repo.get_play_histogram(
            user_id="default", bucket="day", service="lastfm"
        )

        assert [(b.date().isoformat(), c) for b, c in bins] == [("2026-06-01", 1)]
