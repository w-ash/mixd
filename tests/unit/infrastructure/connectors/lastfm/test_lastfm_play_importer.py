"""Unit tests for LastfmPlayImporter windowed fetching and incremental commits.

The fetch loop pulls ``_IMPORT_WINDOW_DAYS``-day windows (one paginated
``user.getRecentTracks`` call each) and persists each window's rows before
committing its resume cursor — the checkpoint-never-leads-persisted-data
invariant, now at window granularity: a crash loses at most one window and
zero persisted rows.
"""

from datetime import UTC, date, datetime, timedelta
from typing import ClassVar
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.config.constants import BusinessLimits, LastFMConstants
from src.domain.entities import ConnectorTrackPlay, SyncCheckpoint
from src.domain.exceptions import LastfmAuthRequiredError
from src.domain.repositories.play import LastfmImportParams
from src.infrastructure.connectors.lastfm.client import LastFMPartialFetchError
from src.infrastructure.connectors.lastfm.play_importer import (
    _IMPORT_WINDOW_DAYS,
    LastfmPlayImporter,
)

_BATCH_ID = "test-batch"
_IMPORT_TS = datetime(2024, 6, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def importer():
    """LastfmPlayImporter with mocked connector."""
    with patch(
        "src.infrastructure.connectors.lastfm.play_importer.LastFMConnector"
    ) as mock_connector_class:
        mock_connector = Mock()
        mock_connector.lastfm_username = "test_user"
        mock_connector_class.return_value = mock_connector
        yield LastfmPlayImporter(lastfm_connector=mock_connector)


def _make_play_record(ts: datetime):
    """Build a minimal PlayRecord for testing."""
    from src.domain.entities import PlayRecord

    return PlayRecord(
        artist_name="Test Artist",
        track_name="Test Track",
        played_at=ts,
        service="lastfm",
    )


def _window_days(window_start: datetime, window_end: datetime) -> list[date]:
    days: list[date] = []
    day = window_start.date()
    while day <= window_end.date():
        days.append(day)
        day += timedelta(days=1)
    return days


def _fake_fetch_window(
    records_per_day: int = 1, *, empty_days: frozenset[int] = frozenset()
):
    """Fake ``_fetch_window_records`` producing N records for each day it spans.

    ``empty_days`` names days-of-month that scrobbled nothing — the realistic
    gap in any long history, and the case the conversion must skip without
    disturbing checkpoints or counts.
    """

    async def _inner(*, username, window_start, window_end):
        records = []
        for day in _window_days(window_start, window_end):
            if day.day in empty_days:
                continue
            mid = datetime.combine(day, datetime.min.time()).replace(
                hour=12, tzinfo=UTC
            )
            # One minute apart, so a day's records are distinct rows under the
            # ledger's dedup constraint — identical timestamps would collapse
            # into one stored row and muddle what "imported" means.
            records.extend(
                _make_play_record(mid + timedelta(minutes=index))
                for index in range(records_per_day)
            )
        return records

    return _inner


def _crashing_fetch_window(crash_on_day: int, records_per_day: int = 2):
    """Like ``_fake_fetch_window`` but blows up on the window spanning that day.

    Stands in for the machine restart / API outage — or the client's own
    partial-page ``LastFMPartialFetchError`` — that the checkpoint exists for.
    """
    healthy = _fake_fetch_window(records_per_day)

    async def _inner(*, username, window_start, window_end):
        if any(
            day.day == crash_on_day for day in _window_days(window_start, window_end)
        ):
            raise RuntimeError(f"Last.fm went away on day {crash_on_day}")
        return await healthy(
            username=username, window_start=window_start, window_end=window_end
        )

    return _inner


def _persisted_plays(uow):
    """Every ledger row handed to the repository, across all per-window calls."""
    insert = uow.get_connector_play_repository().bulk_insert_connector_plays
    return [play for call in insert.await_args_list for play in call.args[0]]


def _dedup_key(play: ConnectorTrackPlay):
    """The ledger's ``uq_connector_plays_deduplication`` tuple."""
    return (
        play.user_id,
        play.connector_name,
        play.connector_track_identifier,
        play.played_at,
        play.ms_played,
    )


def _ledger_backed_uow(stored: set | None = None):
    """Mock UoW whose ledger insert honours the ON CONFLICT dedup constraint.

    Returns ``(uow, stored)``. Seed ``stored`` to stage a crash-resume, where
    the re-fetched cursor window's rows are no-ops — the only setup in which
    the reported import counts can be caught lying.
    """
    from tests.fixtures.mocks import make_mock_uow

    uow = make_mock_uow()
    already: set = set() if stored is None else stored

    async def _insert(plays):
        inserted = duplicates = 0
        for play in plays:
            key = _dedup_key(play)
            if key in already:
                duplicates += 1
            else:
                already.add(key)
                inserted += 1
        return (inserted, duplicates)

    uow.get_connector_play_repository().bulk_insert_connector_plays = AsyncMock(
        side_effect=_insert
    )
    uow.get_checkpoint_repository().save_sync_checkpoint = AsyncMock(
        side_effect=lambda cp: cp
    )
    return uow, already


@pytest.fixture
def mock_uow():
    """Mock UoW with a dedup-honouring ledger and echoing checkpoint saves."""
    return _ledger_backed_uow()[0]


@pytest.fixture
def journalling_uow():
    """Mock UoW recording the interleaving of ledger writes and cursor saves.

    Returns ``(uow, journal)`` where journal entries are
    ``("persist" | "checkpoint", "YYYY-MM-DD")`` in the order they happened —
    the only view that can prove the checkpoint never leads the data.
    """
    from tests.fixtures.mocks import make_mock_uow

    uow = make_mock_uow()
    journal: list[tuple[str, str]] = []

    async def _insert(plays):
        materialized = list(plays)
        journal.extend(
            ("persist", day)
            for day in sorted({
                play.played_at.date().isoformat() for play in materialized
            })
        )
        return (len(materialized), 0)

    async def _save(checkpoint):
        journal.append(("checkpoint", checkpoint.cursor.split("@")[0]))
        return checkpoint

    uow.get_connector_play_repository().bulk_insert_connector_plays = AsyncMock(
        side_effect=_insert
    )
    uow.get_checkpoint_repository().save_sync_checkpoint = AsyncMock(side_effect=_save)
    return uow, journal


class TestDateRangeCalculation:
    """Unit tests for _determine_date_range() business logic."""

    def test_no_dates_defaults_to_30_days(self, importer):
        """New user with no history gets 30-day default range."""
        start, end = importer._determine_date_range(None, None, None)
        assert (end - start).days == 30

    def test_explicit_date_range_honored(self, importer):
        """User-specified date range is used as-is."""
        explicit_start = datetime(2024, 1, 1, tzinfo=UTC)
        explicit_end = datetime(2024, 1, 31, tzinfo=UTC)

        start, end = importer._determine_date_range(explicit_start, explicit_end, None)

        assert start == explicit_start
        assert end == explicit_end

    def test_start_date_only_defaults_end_to_today(self, importer):
        """Start date without end date uses today as end."""
        recent_start = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        start, end = importer._determine_date_range(recent_start, None, None)

        assert start == recent_start
        assert end.date() == datetime.now(UTC).date()


class TestWindowedFetching:
    """Window arithmetic: fetch calls, clamped bounds, per-window commits."""

    async def test_span_inside_one_window_fetches_and_commits_once(
        self, importer, mock_uow
    ):
        """3 days fit one 7-day window: one fetch, one commit, all records."""
        from_date = datetime(2024, 1, 1, tzinfo=UTC)
        to_date = datetime(2024, 1, 3, 23, 59, 59, tzinfo=UTC)

        importer._fetch_window_records = AsyncMock(side_effect=_fake_fetch_window(2))

        records = await importer._fetch_date_range_strategy(
            from_date=from_date,
            to_date=to_date,
            user_id="mixd-user-1",
            username="test_user",
            batch_id=_BATCH_ID,
            import_timestamp=_IMPORT_TS,
            uow=mock_uow,
        )

        assert len(records) == 6  # 3 days * 2 records
        assert importer._fetch_window_records.await_count == 1
        assert mock_uow.commit_batch.await_count == 1

    async def test_ten_day_range_makes_exactly_two_clamped_fetches(
        self, importer, mock_uow
    ):
        """10 days at window=7 → two fetches; first/last clamp to the range."""
        assert _IMPORT_WINDOW_DAYS == 7  # the arithmetic below assumes it
        from_date = datetime(2024, 1, 1, 8, 30, tzinfo=UTC)
        to_date = datetime(2024, 1, 10, 21, 15, tzinfo=UTC)

        importer._fetch_window_records = AsyncMock(side_effect=_fake_fetch_window(1))

        records = await importer._fetch_date_range_strategy(
            from_date=from_date,
            to_date=to_date,
            user_id="mixd-user-1",
            username="test_user",
            batch_id=_BATCH_ID,
            import_timestamp=_IMPORT_TS,
            uow=mock_uow,
        )

        assert len(records) == 10
        calls = importer._fetch_window_records.await_args_list
        assert len(calls) == 2
        # First window starts at the requested from_date, not midnight.
        assert calls[0].kwargs["window_start"] == from_date
        assert calls[0].kwargs["window_end"] == datetime(
            2024, 1, 7, 23, 59, 59, 999999, tzinfo=UTC
        )
        # Last window ends at the requested to_date, not end-of-day.
        assert calls[1].kwargs["window_start"] == datetime(2024, 1, 8, tzinfo=UTC)
        assert calls[1].kwargs["window_end"] == to_date
        assert mock_uow.commit_batch.await_count == 2

    async def test_window_checkpoints_are_keyed_on_the_mixd_user(
        self, importer, mock_uow
    ):
        """The checkpoint's ``user_id`` is the mixd user, never the Last.fm account.

        ``sync_checkpoints`` is FORCE-RLS'd on ``user_id``, so an account-keyed
        row is rejected on write and invisible on read. A mock UoW accepts either
        key, so the enforcement itself is proven in
        tests/integration/connectors/lastfm/test_lastfm_checkpoint_rls.py — this
        just pins the key at the call site.
        """
        importer._fetch_window_records = AsyncMock(side_effect=_fake_fetch_window(1))

        _ = await importer._fetch_date_range_strategy(
            from_date=datetime(2024, 1, 1, tzinfo=UTC),
            to_date=datetime(2024, 1, 1, 23, 59, 59, tzinfo=UTC),
            user_id="mixd-user-1",
            username="test_user",
            batch_id=_BATCH_ID,
            import_timestamp=_IMPORT_TS,
            uow=mock_uow,
        )

        saved = mock_uow.get_checkpoint_repository().save_sync_checkpoint
        checkpoint = saved.await_args.args[0]
        assert checkpoint.user_id == "mixd-user-1"
        assert checkpoint.service == "lastfm"
        # The account rides along in the cursor so an account switch is detectable.
        assert checkpoint.cursor == "2024-01-01@test_user"

    def test_a_uow_less_call_is_not_expressible(self, importer):
        """``uow`` has no default: this loop IS the persistence path.

        ``persists_plays_during_fetch`` is unconditionally True here, so the
        base class skips the run-end save. A uow-less call would fetch the whole
        span, write nothing, and still report success — the signature is what
        stops that, so its shape is the thing worth pinning.
        """
        with pytest.raises(TypeError, match="uow"):
            _ = importer._fetch_date_range_strategy(
                from_date=datetime(2024, 1, 1, tzinfo=UTC),
                to_date=datetime(2024, 1, 2, 23, 59, 59, tzinfo=UTC),
                user_id="mixd-user-1",
                username="test_user",
                batch_id=_BATCH_ID,
                import_timestamp=_IMPORT_TS,
            )

    async def test_window_under_the_ceiling_fetches_once(self, importer):
        """A normal window is one fetch — subdivision only triggers at the cap."""
        records = [_make_play_record(datetime(2024, 1, 1, 12, 0, tzinfo=UTC))]
        importer.lastfm_connector.get_recent_tracks = AsyncMock(return_value=records)

        fetched = await importer._fetch_window_records(
            username="test_user",
            window_start=datetime(2024, 1, 1, tzinfo=UTC),
            window_end=datetime(2024, 1, 7, 23, 59, 59, tzinfo=UTC),
        )

        assert fetched == records
        assert importer.lastfm_connector.get_recent_tracks.await_count == 1


class TestCeilingWindowSubdivision:
    """A ceiling-hit window re-fetches per day instead of persisting truncation.

    The API returns newest-first truncated at ``FULL_HISTORY_LIMIT``, so the
    dropped records are the window's OLDEST — and the forward-only cursor never
    revisits a checkpointed window. Subdivision (the degenerate one-day window,
    same fetch path) keeps dense accounts importable; a single day that still
    ceilings raises rather than persisting a truncated day.
    """

    @staticmethod
    def _ceiling_then_daily_fetch(records_per_day: int):
        """Multi-day spans come back ceiling-size; one-day spans return real rows."""

        async def _fetch(*, username, limit, from_time, to_time):
            if from_time.date() == to_time.date():
                mid = datetime.combine(from_time.date(), datetime.min.time()).replace(
                    hour=12, tzinfo=UTC
                )
                return [
                    _make_play_record(mid + timedelta(minutes=index))
                    for index in range(records_per_day)
                ]
            return [_make_play_record(from_time)] * LastFMConstants.FULL_HISTORY_LIMIT

        return AsyncMock(side_effect=_fetch)

    async def test_ceiling_window_refetches_per_day_with_clamped_bounds(self, importer):
        """The window fetch ceilings, then each day is fetched with bounds
        clamped to the original window's edges — no day outside it, no gap."""
        importer.lastfm_connector.get_recent_tracks = self._ceiling_then_daily_fetch(2)
        window_start = datetime(2024, 1, 1, 8, 30, tzinfo=UTC)
        window_end = datetime(2024, 1, 3, 21, 15, tzinfo=UTC)

        records = await importer._fetch_window_records(
            username="test_user", window_start=window_start, window_end=window_end
        )

        assert len(records) == 6  # 3 days * 2, the full re-fetched window
        calls = importer.lastfm_connector.get_recent_tracks.await_args_list
        spans = [(call.kwargs["from_time"], call.kwargs["to_time"]) for call in calls]
        assert spans == [
            (window_start, window_end),  # the original whole-window fetch
            (window_start, datetime(2024, 1, 1, 23, 59, 59, 999999, tzinfo=UTC)),
            (
                datetime(2024, 1, 2, tzinfo=UTC),
                datetime(2024, 1, 2, 23, 59, 59, 999999, tzinfo=UTC),
            ),
            (datetime(2024, 1, 3, tzinfo=UTC), window_end),
        ]

    async def test_subdivided_window_persists_every_day_and_checkpoints_once(
        self, importer, mock_uow
    ):
        """Through the strategy loop: the subdivided fetches all land in the
        window's persist, and the window still commits exactly once."""
        importer.lastfm_connector.get_recent_tracks = self._ceiling_then_daily_fetch(1)

        plays = await importer._fetch_date_range_strategy(
            from_date=datetime(2024, 1, 1, tzinfo=UTC),
            to_date=datetime(2024, 1, 3, 23, 59, 59, tzinfo=UTC),
            user_id="mixd-user-1",
            username="test_user",
            batch_id=_BATCH_ID,
            import_timestamp=_IMPORT_TS,
            uow=mock_uow,
        )

        assert len(plays) == 3
        assert {
            play.played_at.date().isoformat() for play in _persisted_plays(mock_uow)
        } == {"2024-01-01", "2024-01-02", "2024-01-03"}
        assert mock_uow.commit_batch.await_count == 1

    async def test_single_day_ceiling_raises(self, importer):
        importer.lastfm_connector.get_recent_tracks = AsyncMock(
            return_value=[_make_play_record(datetime(2024, 1, 1, 12, 0, tzinfo=UTC))]
            * LastFMConstants.FULL_HISTORY_LIMIT
        )

        with pytest.raises(LastFMPartialFetchError, match="ceiling"):
            _ = await importer._fetch_window_records(
                username="test_user",
                window_start=datetime(2024, 1, 1, tzinfo=UTC),
                window_end=datetime(2024, 1, 1, 23, 59, 59, tzinfo=UTC),
            )

    async def test_no_checkpoint_advances_past_a_ceiling_raise(
        self, importer, journalling_uow
    ):
        """Window ceilings, its first subdivided day ceilings too — the raise
        must leave neither rows nor cursor for that window."""
        uow, journal = journalling_uow
        importer.lastfm_connector.get_recent_tracks = AsyncMock(
            return_value=[_make_play_record(datetime(2024, 1, 1, 12, 0, tzinfo=UTC))]
            * LastFMConstants.FULL_HISTORY_LIMIT
        )

        with pytest.raises(LastFMPartialFetchError):
            _ = await importer._fetch_date_range_strategy(
                from_date=datetime(2024, 1, 1, tzinfo=UTC),
                to_date=datetime(2024, 1, 3, 23, 59, 59, tzinfo=UTC),
                user_id="mixd-user-1",
                username="test_user",
                batch_id=_BATCH_ID,
                import_timestamp=_IMPORT_TS,
                uow=uow,
            )

        assert journal == []


class TestPerWindowConversion:
    """The window loop yields ledger rows, not a span-wide list of raw scrobbles.

    Conversion happens as each window lands so a multi-year import never holds
    the raw records for the whole span on top of the connector plays.
    """

    async def test_window_records_become_stamped_connector_plays(
        self, importer, mock_uow
    ):
        importer._fetch_window_records = AsyncMock(side_effect=_fake_fetch_window(2))

        plays = await importer._fetch_date_range_strategy(
            from_date=datetime(2024, 1, 1, tzinfo=UTC),
            to_date=datetime(2024, 1, 3, 23, 59, 59, tzinfo=UTC),
            user_id="mixd-user-1",
            username="test_user",
            batch_id=_BATCH_ID,
            import_timestamp=_IMPORT_TS,
            uow=mock_uow,
        )

        assert len(plays) == 6
        assert all(isinstance(play, ConnectorTrackPlay) for play in plays)
        # Tenancy at construction: nothing downstream re-stamps it any more, so
        # a miss here would file every row under the "default" tenant.
        assert {play.user_id for play in plays} == {"mixd-user-1"}
        assert {play.import_batch_id for play in plays} == {_BATCH_ID}
        assert {play.import_source for play in plays} == {"lastfm_api"}
        # One timestamp for the batch, not one per window of a multi-year loop.
        assert {play.import_timestamp for play in plays} == {_IMPORT_TS}

    async def test_empty_day_mid_window_still_checkpoints_and_commits(
        self, importer, mock_uow
    ):
        """A silent day contributes nothing but must not stall the loop."""
        importer._fetch_window_records = AsyncMock(
            side_effect=_fake_fetch_window(1, empty_days=frozenset({2}))
        )

        plays = await importer._fetch_date_range_strategy(
            from_date=datetime(2024, 1, 1, tzinfo=UTC),
            to_date=datetime(2024, 1, 3, 23, 59, 59, tzinfo=UTC),
            user_id="mixd-user-1",
            username="test_user",
            batch_id=_BATCH_ID,
            import_timestamp=_IMPORT_TS,
            uow=mock_uow,
        )

        assert len(plays) == 2  # days 1 and 3
        assert mock_uow.commit_batch.await_count == 1
        saved = mock_uow.get_checkpoint_repository().save_sync_checkpoint
        assert saved.await_args.args[0].cursor == "2024-01-03@test_user"

    async def test_range_with_no_plays_at_all_returns_empty(self, importer, mock_uow):
        importer._fetch_window_records = AsyncMock(
            side_effect=_fake_fetch_window(0, empty_days=frozenset({1, 2}))
        )

        plays = await importer._fetch_date_range_strategy(
            from_date=datetime(2024, 1, 1, tzinfo=UTC),
            to_date=datetime(2024, 1, 2, 23, 59, 59, tzinfo=UTC),
            user_id="mixd-user-1",
            username="test_user",
            batch_id=_BATCH_ID,
            import_timestamp=_IMPORT_TS,
            uow=mock_uow,
        )

        assert plays == []
        assert mock_uow.commit_batch.await_count == 1


class TestImportPlaysTotals:
    """End-to-end reported totals for the multi-window path, plus ledger tenancy."""

    @staticmethod
    def _importer(side_effect):
        """Importer whose account resolves from env and whose windows are faked."""
        connector = Mock()
        connector.lastfm_username = "test_user"
        storage = AsyncMock()
        storage.load_token.return_value = None
        importer = LastfmPlayImporter(lastfm_connector=connector, token_storage=storage)
        importer._fetch_window_records = AsyncMock(side_effect=side_effect)
        return importer

    @staticmethod
    def _params():
        from src.domain.repositories.play import LastfmImportParams

        return LastfmImportParams(
            from_date=datetime(2024, 1, 1, tzinfo=UTC),
            to_date=datetime(2024, 1, 3, 23, 59, 59, tzinfo=UTC),
        )

    async def test_multi_day_totals_and_tenancy(self, mock_uow):
        importer = self._importer(_fake_fetch_window(2))

        result, plays = await importer.import_plays(
            mock_uow, self._params(), user_id="mixd-user-1"
        )

        assert len(plays) == 6  # 3 days * 2 records
        assert result.summary_metrics.get("raw_plays") == 6
        assert result.summary_metrics.get("imported") == 6
        assert {play.user_id for play in plays} == {"mixd-user-1"}

        # The rows actually handed to the repository carry the tenancy too —
        # connector_plays is RLS-scoped on user_id. Persistence is per window
        # (the checkpoint invariant), so the span is the union of those calls.
        assert [play.user_id for play in _persisted_plays(mock_uow)] == (
            ["mixd-user-1"] * 6
        )

    async def test_empty_span_reports_zeros_without_saving(self, mock_uow):
        """No scrobbles anywhere in the range: the empty-data path, not a crash."""
        importer = self._importer(
            _fake_fetch_window(0, empty_days=frozenset({1, 2, 3}))
        )

        result, plays = await importer.import_plays(
            mock_uow, self._params(), user_id="mixd-user-1"
        )

        assert plays == []
        assert result.summary_metrics.get("raw_plays") == 0
        assert result.summary_metrics.get("imported") == 0
        insert = mock_uow.get_connector_play_repository().bulk_insert_connector_plays
        insert.assert_not_awaited()


class TestCheckpointDurabilityInvariant:
    """The resume cursor may never point past rows that were never written.

    The loop once committed a cursor per chunk while the plays were persisted
    only after the whole span: a crash at day 300 of a 5,100-day full-history
    import left a day-300 cursor with ZERO rows in the ledger, and the resumed
    run started at day 300 — days 1-299 were lost silently and forever. The
    same invariant now holds per window: rows first, cursor second, one
    durable commit per window.
    """

    # Three 7-day windows: Jan 1-7, Jan 8-14, Jan 15-21.
    _SPAN: ClassVar[dict[str, object]] = {
        "from_date": datetime(2024, 1, 1, tzinfo=UTC),
        "to_date": datetime(2024, 1, 21, 23, 59, 59, tzinfo=UTC),
        "user_id": "mixd-user-1",
        "username": "test_user",
        "batch_id": _BATCH_ID,
        "import_timestamp": _IMPORT_TS,
    }

    async def test_checkpoint_never_leads_persisted_plays(
        self, importer, journalling_uow
    ):
        """Crash in window 2: window 1 persisted then checkpointed; nothing of
        window 2 — neither rows nor cursor — ever landed."""
        uow, journal = journalling_uow
        importer._fetch_window_records = AsyncMock(
            side_effect=_crashing_fetch_window(10)
        )

        with pytest.raises(RuntimeError):
            _ = await importer._fetch_date_range_strategy(uow=uow, **self._SPAN)

        assert journal == [
            *(("persist", f"2024-01-0{d}") for d in range(1, 8)),
            ("checkpoint", "2024-01-07"),
        ]

    async def test_resume_after_crash_loses_no_window_and_refetches_only_the_cursor_window(
        self, importer, journalling_uow
    ):
        """The two runs together cover the span, overlapping only from the
        cursor day forward — the resumed run re-fetches the cursor day (late
        scrobbles) and everything after, never the already-durable windows."""
        uow, journal = journalling_uow
        importer._fetch_window_records = AsyncMock(
            side_effect=_crashing_fetch_window(10)
        )

        with pytest.raises(RuntimeError):
            _ = await importer._fetch_date_range_strategy(uow=uow, **self._SPAN)

        before = {day for kind, day in journal if kind == "persist"}
        cursor = [day for kind, day in journal if kind == "checkpoint"][-1]
        assert cursor == "2024-01-07"

        journal.clear()
        importer._fetch_window_records = AsyncMock(side_effect=_fake_fetch_window(2))
        _ = await importer._fetch_date_range_strategy(
            checkpoint=SyncCheckpoint(
                user_id="mixd-user-1",
                service="lastfm",
                entity_type="plays",
                last_timestamp=datetime.fromisoformat(cursor).replace(tzinfo=UTC),
                cursor=f"{cursor}@test_user",
            ),
            uow=uow,
            **self._SPAN,
        )

        after = {day for kind, day in journal if kind == "persist"}
        full_span = {f"2024-01-{d:02d}" for d in range(1, 22)}
        assert before | after == full_span
        # The cursor day is always re-processed to catch late scrobbles; the
        # ledger's ON CONFLICT dedupe is what makes that free. Only IT
        # overlaps: the resumed windows start there, not at the span start.
        assert before & after == {"2024-01-07"}

    async def test_clean_run_persists_every_row_exactly_once(self, mock_uow):
        """Per-window persistence writes what the run-end save used to, no more.

        With the rows already durable the base pipeline must skip its own save
        — otherwise a full-history import re-streams the entire span for zero
        rows gained.
        """
        connector = Mock()
        connector.lastfm_username = "test_user"
        storage = AsyncMock()
        storage.load_token.return_value = None
        importer = LastfmPlayImporter(lastfm_connector=connector, token_storage=storage)
        importer._fetch_window_records = AsyncMock(side_effect=_fake_fetch_window(2))

        result, plays = await importer.import_plays(
            mock_uow,
            LastfmImportParams(
                from_date=datetime(2024, 1, 1, tzinfo=UTC),
                to_date=datetime(2024, 1, 3, 23, 59, 59, tzinfo=UTC),
            ),
            user_id="mixd-user-1",
        )

        persisted = _persisted_plays(mock_uow)
        assert [play.id for play in persisted] == [play.id for play in plays]
        assert result.summary_metrics.get("imported") == len(plays)


class TestReportedCountsAreLedgerTruth:
    """A run reports what the ledger ACCEPTED, not the size of the span.

    ``persists_plays_during_fetch`` skips the run-end save, and the run-end
    result used to fabricate ``(len(track_plays), 0)`` in its place. On a
    crash-resume the re-fetched cursor window is ON CONFLICT no-ops, so those
    rows were reported as freshly imported when nothing was written at all.
    """

    @staticmethod
    def _importer():
        connector = Mock()
        connector.lastfm_username = "test_user"
        storage = AsyncMock()
        storage.load_token.return_value = None
        importer = LastfmPlayImporter(lastfm_connector=connector, token_storage=storage)
        importer._fetch_window_records = AsyncMock(side_effect=_fake_fetch_window(2))
        return importer

    @staticmethod
    def _params(last_day: int):
        return LastfmImportParams(
            from_date=datetime(2024, 1, 1, tzinfo=UTC),
            to_date=datetime(2024, 1, last_day, 23, 59, 59, tzinfo=UTC),
        )

    async def test_clean_run_counts_every_row_as_imported(self):
        uow, _stored = _ledger_backed_uow()

        result, plays = await self._importer().import_plays(
            uow, self._params(3), user_id="mixd-user-1"
        )

        assert len(plays) == 6
        assert result.summary_metrics.get("imported") == 6
        # The metric is only emitted when non-zero — nothing was re-written.
        assert not result.summary_metrics.get("duplicates")

    async def test_resume_reports_the_re_fetched_days_as_duplicates(self):
        """Days 1-2 already landed; the explicit-range re-run fetches days 1-3.

        The re-fetched days contribute duplicates, never imports — the whole
        point of the ON CONFLICT write is that re-running is free, and the
        statistics have to say so.
        """
        uow, stored = _ledger_backed_uow()
        _, first_run_plays = await self._importer().import_plays(
            uow, self._params(2), user_id="mixd-user-1"
        )
        assert len(stored) == len(first_run_plays) == 4

        # Explicit range re-fetches days 1-3 in one window; day 3 is new.
        result, plays = await self._importer().import_plays(
            uow, self._params(3), user_id="mixd-user-1"
        )

        assert len(plays) == 6  # days 1-3 re-fetched by the explicit range
        assert result.summary_metrics.get("imported") == 2  # only day 3 is new
        assert result.summary_metrics.get("duplicates") == 4


class TestCheckpointAccountTag:
    """The cursor's account tag guards against resuming another account's position.

    The row is keyed on the mixd user, and nothing clears it when the connected
    Last.fm account changes — so the account the windows were fetched for rides
    along in the cursor.
    """

    @staticmethod
    def _checkpoint(cursor):
        from src.domain.entities import SyncCheckpoint

        return SyncCheckpoint(
            user_id="mixd-user-1",
            service="lastfm",
            entity_type="plays",
            last_timestamp=datetime(2024, 1, 5, tzinfo=UTC),
            cursor=cursor,
        )

    @pytest.mark.parametrize(
        ("cursor", "account", "expected"),
        [
            ("2024-01-05@alice", "alice", True),
            ("2024-01-05@Alice", "alice", True),  # Last.fm names are case-insensitive
            ("2024-01-05@alice", "bob", False),
            ("2024-01-05", "alice", True),  # pre-tag cursor: accepted as-is
            (None, "alice", True),
        ],
    )
    def test_account_match(self, importer, cursor, account, expected):
        checkpoint = self._checkpoint(cursor)
        assert importer._checkpoint_matches_account(checkpoint, account) is expected

    def test_chunk_start_reads_the_date_half_of_a_tagged_cursor(self, importer):
        start = importer._resolve_chunk_start(
            self._checkpoint("2024-01-05@alice"),
            explicit_range=False,
            requested_start=datetime(2024, 1, 1, tzinfo=UTC).date(),
        )
        assert start == datetime(2024, 1, 5, tzinfo=UTC).date()

    async def test_mismatched_account_is_treated_as_no_checkpoint(self, importer):
        """A different account must not resume — that would skip its own history."""
        from tests.fixtures.mocks import make_mock_uow

        uow = make_mock_uow()
        uow.get_checkpoint_repository().get_sync_checkpoint = AsyncMock(
            return_value=self._checkpoint("2024-01-05@alice")
        )

        resolved = await importer._resolve_checkpoint(
            user_id="mixd-user-1", account="bob", uow=uow
        )

        assert resolved is None


class TestProgressEmission:
    """Verify per-window progress events in _fetch_date_range_strategy."""

    async def test_emit_progress_called_per_window(self, importer, mock_uow):
        """10 days → 2 windows → 2 events with increasing counts."""
        from_date = datetime(2024, 1, 1, tzinfo=UTC)
        to_date = datetime(2024, 1, 10, 23, 59, 59, tzinfo=UTC)

        importer._fetch_window_records = AsyncMock(side_effect=_fake_fetch_window(1))

        emitter = AsyncMock()
        emitter.emit_progress = AsyncMock()

        await importer._fetch_date_range_strategy(
            from_date=from_date,
            to_date=to_date,
            user_id="mixd-user-1",
            username="test_user",
            batch_id=_BATCH_ID,
            import_timestamp=_IMPORT_TS,
            progress_emitter=emitter,
            operation_id="test-op-123",
            uow=mock_uow,
        )

        assert emitter.emit_progress.await_count == 2
        # Verify monotonically increasing current values and honest totals.
        calls = emitter.emit_progress.call_args_list
        currents = [call.args[0].current for call in calls]
        assert currents == [1, 2]
        assert {call.args[0].total for call in calls} == {2}

    async def test_no_progress_without_emitter(self, importer, mock_uow):
        """Without progress_emitter, no emit_progress calls."""
        from_date = datetime(2024, 1, 1, tzinfo=UTC)
        to_date = datetime(2024, 1, 1, 23, 59, 59, tzinfo=UTC)

        importer._fetch_window_records = AsyncMock(side_effect=_fake_fetch_window(1))

        # No progress_emitter passed — should not raise
        records = await importer._fetch_date_range_strategy(
            from_date=from_date,
            to_date=to_date,
            user_id="mixd-user-1",
            username="test_user",
            batch_id=_BATCH_ID,
            import_timestamp=_IMPORT_TS,
            uow=mock_uow,
        )
        assert len(records) == 1


class TestPartialWindowNeverCheckpoints:
    """A partial fetch fails the window instead of checkpointing a hole.

    The client raises ``LastFMPartialFetchError`` on a failed page (pinned in
    test_lastfm_client_partial_fetch.py); here the importer contract: the
    error propagates, and neither rows nor cursor land for that window.
    """

    async def test_partial_fetch_propagates_and_saves_nothing(
        self, importer, journalling_uow
    ):
        from src.infrastructure.connectors.lastfm.client import (
            LastFMPartialFetchError,
        )

        uow, journal = journalling_uow
        importer.lastfm_connector.get_recent_tracks = AsyncMock(
            side_effect=LastFMPartialFetchError("page 3/9 failed after retries")
        )

        with pytest.raises(LastFMPartialFetchError):
            _ = await importer._fetch_date_range_strategy(
                from_date=datetime(2024, 1, 1, tzinfo=UTC),
                to_date=datetime(2024, 1, 3, 23, 59, 59, tzinfo=UTC),
                user_id="mixd-user-1",
                username="test_user",
                batch_id=_BATCH_ID,
                import_timestamp=_IMPORT_TS,
                uow=uow,
            )

        assert journal == []


class TestUsernameResolution:
    """Last.fm username resolution precedence (v0.8.5 cross-tenant leak fix).

    Order: stored-token ``account_name`` for THIS user_id > explicit request
    username > ``LASTFM_USERNAME`` env. Token-first is the security guarantee — a
    web user with a connected account can never read env / another tenant's data.
    """

    @staticmethod
    def _build(*, env_username=None, token=None):
        connector = Mock()
        connector.lastfm_username = env_username
        storage = AsyncMock()
        storage.load_token.return_value = token
        importer = LastfmPlayImporter(lastfm_connector=connector, token_storage=storage)
        return importer, storage

    async def test_stored_token_account_name_beats_request_and_env(self):
        # The security assertion: a connected account always wins.
        importer, storage = self._build(
            env_username="env_user", token={"account_name": "alice"}
        )
        result = await importer._resolve_username("bob", "user-1")
        assert result == "alice"
        storage.load_token.assert_awaited_once_with("lastfm", "user-1")

    async def test_request_username_used_when_no_token(self):
        importer, _ = self._build(env_username="env_user", token=None)
        assert await importer._resolve_username("bob", "user-1") == "bob"

    async def test_env_used_when_no_token_and_no_request(self):
        importer, _ = self._build(env_username="env_user", token=None)
        assert await importer._resolve_username(None, "user-1") == "env_user"

    async def test_local_dev_sentinel_falls_through_to_env(self):
        """CLI/local-dev keys on DEFAULT_USER_ID, which holds no token.

        Replaces the old ``user_id=None`` case: ``import_plays`` now requires a
        real ``user_id`` (v0.10.1), so the un-keyed branch is unreachable. The
        sentinel is looked up like any other tenant and simply misses.
        """
        importer, storage = self._build(env_username="env_user")
        resolved = await importer._resolve_username(
            None, BusinessLimits.DEFAULT_USER_ID
        )
        assert resolved == "env_user"
        storage.load_token.assert_awaited_once_with(
            "lastfm", BusinessLimits.DEFAULT_USER_ID
        )

    async def test_token_without_account_name_falls_through_to_env(self):
        importer, _ = self._build(env_username="env_user", token={"session_key": "sk"})
        assert await importer._resolve_username(None, "user-1") == "env_user"

    async def test_raises_when_nothing_resolves(self):
        importer, _ = self._build(env_username=None, token=None)
        with pytest.raises(LastfmAuthRequiredError):
            await importer._resolve_username(None, "user-1")
