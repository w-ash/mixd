"""Unit tests for ListPlaysUseCase and GetPlaysHistogramUseCase."""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest

from src.application.pagination import decode_cursor
from src.application.use_cases.get_plays_histogram import (
    GetPlaysHistogramCommand,
    GetPlaysHistogramUseCase,
)
from src.application.use_cases.list_plays import ListPlaysCommand, ListPlaysUseCase
from src.domain.entities import Track, TrackPlay
from src.domain.entities.track import Artist
from tests.fixtures.mocks import (
    make_mock_plays_repo,
    make_mock_track_repo,
    make_mock_uow,
)


def _play(track_id, played_at, service="spotify"):
    return TrackPlay(
        track_id=track_id,
        service=service,
        played_at=played_at,
        user_id="u1",
        source_services=["spotify"],
    )


def _naive(moment: datetime) -> datetime:
    """Drop the offset the way a bare ``datetime`` query param arrives."""
    return moment.replace(tzinfo=None)


def _track(track_id, title="Song"):
    return Track(
        id=track_id, title=title, artists=[Artist(name="Artist")], user_id="u1"
    )


class TestListPlaysUseCase:
    @pytest.mark.asyncio
    async def test_rows_join_track_metadata_and_encode_next_cursor(self):
        track_id = uuid7()
        newest = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        plays = [_play(track_id, newest), _play(track_id, newest - timedelta(hours=1))]
        next_key = (plays[-1].played_at, plays[-1].id)
        plays_repo = make_mock_plays_repo(list_play_events=(plays, next_key))
        track_repo = make_mock_track_repo()
        track_repo.find_tracks_by_ids.return_value = {track_id: _track(track_id)}
        uow = make_mock_uow(plays_repo=plays_repo, track_repo=track_repo)

        result = await ListPlaysUseCase().execute(
            ListPlaysCommand(user_id="u1", limit=2), uow
        )

        assert [r.title for r in result.plays] == ["Song", "Song"]
        assert result.plays[0].artists == "Artist"
        assert result.next_cursor is not None
        decoded = decode_cursor(result.next_cursor)
        assert decoded.sort_column == "played_at"
        assert decoded.last_id == plays[-1].id

    @pytest.mark.asyncio
    async def test_cursor_round_trip_feeds_before_tuple(self):
        track_id = uuid7()
        newest = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        play = _play(track_id, newest)
        plays_repo = make_mock_plays_repo(
            list_play_events=([play], (play.played_at, play.id))
        )
        track_repo = make_mock_track_repo()
        track_repo.find_tracks_by_ids.return_value = {track_id: _track(track_id)}
        uow = make_mock_uow(plays_repo=plays_repo, track_repo=track_repo)

        first = await ListPlaysUseCase().execute(
            ListPlaysCommand(user_id="u1", limit=1), uow
        )
        _ = await ListPlaysUseCase().execute(
            ListPlaysCommand(user_id="u1", limit=1, encoded_cursor=first.next_cursor),
            uow,
        )

        second_call = plays_repo.list_play_events.await_args_list[1]
        assert second_call.kwargs["before"] == (play.played_at, play.id)

    @pytest.mark.asyncio
    async def test_missing_track_falls_back_to_placeholder(self):
        track_id = uuid7()
        play = _play(track_id, datetime(2026, 8, 1, tzinfo=UTC))
        plays_repo = make_mock_plays_repo(list_play_events=([play], None))
        track_repo = make_mock_track_repo()
        track_repo.find_tracks_by_ids.return_value = {}
        uow = make_mock_uow(plays_repo=plays_repo, track_repo=track_repo)

        result = await ListPlaysUseCase().execute(ListPlaysCommand(user_id="u1"), uow)

        assert result.plays[0].title == "Unknown track"
        assert result.next_cursor is None


class TestGetPlaysHistogramUseCase:
    @pytest.mark.asyncio
    async def test_explicit_range_picks_bucket_from_span(self):
        since = datetime(2026, 6, 1, tzinfo=UTC)
        until = datetime(2026, 7, 1, tzinfo=UTC)
        plays_repo = make_mock_plays_repo(get_play_histogram=[(since, 5)])
        uow = make_mock_uow(plays_repo=plays_repo)

        result = await GetPlaysHistogramUseCase().execute(
            GetPlaysHistogramCommand(user_id="u1", since=since, until=until), uow
        )

        assert result.bucket == "day"
        assert result.bins[0].count == 5
        assert plays_repo.get_play_histogram.await_args.kwargs["bucket"] == "day"

    @pytest.mark.asyncio
    async def test_open_range_rebins_when_history_is_narrow(self):
        # First (month) probe reveals a two-month span -> rebin daily.
        recent = datetime.now(UTC) - timedelta(days=60)
        plays_repo = make_mock_plays_repo(
            get_play_histogram=[(recent.replace(tzinfo=None), 3)]
        )
        uow = make_mock_uow(plays_repo=plays_repo)

        result = await GetPlaysHistogramUseCase().execute(
            GetPlaysHistogramCommand(user_id="u1"), uow
        )

        assert result.bucket == "day"
        assert plays_repo.get_play_histogram.await_count == 2

    @pytest.mark.asyncio
    async def test_open_range_multi_year_stays_monthly(self):
        old = (datetime.now(UTC) - timedelta(days=1500)).replace(tzinfo=None)
        plays_repo = make_mock_plays_repo(get_play_histogram=[(old, 7)])
        uow = make_mock_uow(plays_repo=plays_repo)

        result = await GetPlaysHistogramUseCase().execute(
            GetPlaysHistogramCommand(user_id="u1"), uow
        )

        assert result.bucket == "month"
        assert plays_repo.get_play_histogram.await_count == 1


class TestNaiveDatetimesAreNormalized:
    """A bare ``datetime`` query param and ``datetime.fromisoformat`` over
    LLM-supplied text both hand the Commands offset-free values, while the use
    case compares them against ``datetime.now(UTC)``. Every arm below raised
    ``TypeError`` — an unhandled 500 — before the Command converter landed."""

    @pytest.mark.asyncio
    async def test_naive_since_with_open_until(self):
        plays_repo = make_mock_plays_repo(get_play_histogram=[])
        uow = make_mock_uow(plays_repo=plays_repo)

        result = await GetPlaysHistogramUseCase().execute(
            GetPlaysHistogramCommand(
                user_id="u1", since=_naive(datetime(2026, 6, 1, tzinfo=UTC))
            ),
            uow,
        )

        assert result.bucket in {"day", "week", "month"}

    @pytest.mark.asyncio
    async def test_naive_until_with_open_since(self):
        # The rebin probe subtracts an aware bin start from ``until``.
        recent = (datetime.now(UTC) - timedelta(days=60)).replace(tzinfo=None)
        plays_repo = make_mock_plays_repo(get_play_histogram=[(recent, 3)])
        uow = make_mock_uow(plays_repo=plays_repo)

        result = await GetPlaysHistogramUseCase().execute(
            GetPlaysHistogramCommand(user_id="u1", until=_naive(datetime.now(UTC))), uow
        )

        assert result.bucket == "day"

    @pytest.mark.asyncio
    async def test_naive_pair_picks_the_same_bucket_as_the_aware_pair(self):
        naive_since = _naive(datetime(2026, 6, 1, tzinfo=UTC))
        naive_until = _naive(datetime(2026, 7, 1, tzinfo=UTC))
        plays_repo = make_mock_plays_repo(get_play_histogram=[])
        uow = make_mock_uow(plays_repo=plays_repo)

        result = await GetPlaysHistogramUseCase().execute(
            GetPlaysHistogramCommand(
                user_id="u1", since=naive_since, until=naive_until
            ),
            uow,
        )

        assert result.bucket == "day"

    def test_commands_attach_utc_and_leave_aware_values_alone(self):
        aware = datetime(2026, 6, 1, tzinfo=UTC)
        for command in (
            GetPlaysHistogramCommand(user_id="u1", since=_naive(aware), until=aware),
            ListPlaysCommand(user_id="u1", since=_naive(aware), until=aware),
        ):
            assert command.since is not None
            assert command.since.tzinfo is UTC
            # A value that already carries an offset passes through untouched.
            assert command.until == aware

    def test_naive_bounds_reach_the_repository_as_aware(self):
        """A naive ``since`` on the feed does not raise — psycopg would
        reinterpret it against the session TimeZone and return wrong rows."""
        aware = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        command = ListPlaysCommand(user_id="u1", since=_naive(aware))

        assert command.since == aware
