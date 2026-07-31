"""Unit tests for LastfmPlayImporter date range calculation and incremental commits.

Also covers the per-day conversion introduced when the day loop stopped
accumulating a span-wide raw-record list: each day's scrobbles become
``ConnectorTrackPlay`` rows as they land, tenancy stamped at construction.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.config.constants import BusinessLimits
from src.domain.entities import ConnectorTrackPlay
from src.domain.exceptions import LastfmAuthRequiredError
from src.infrastructure.connectors.lastfm.play_importer import LastfmPlayImporter

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


def _fake_fetch_day(
    records_per_day: int = 1, *, empty_days: frozenset[int] = frozenset()
):
    """Return a fake _fetch_day_records side-effect producing N records per day.

    ``empty_days`` names days-of-month that scrobbled nothing — the realistic
    gap in any long history, and the case the per-day conversion must skip
    without disturbing checkpoints or counts.
    """

    async def _inner(*, username, day_start, day_end, current_date):
        if current_date.day in empty_days:
            return []
        mid = datetime.combine(current_date, datetime.min.time()).replace(
            hour=12, tzinfo=UTC
        )
        return [_make_play_record(mid) for _ in range(records_per_day)]

    return _inner


@pytest.fixture
def mock_uow():
    """Mock UoW whose checkpoint saves echo the entity back."""
    from tests.fixtures.mocks import make_mock_uow

    uow = make_mock_uow()
    checkpoint_repo = uow.get_checkpoint_repository()
    checkpoint_repo.save_sync_checkpoint = AsyncMock(side_effect=lambda cp: cp)
    return uow


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


class TestIncrementalCommit:
    """Verify commit_batch() is called per day in _fetch_date_range_strategy."""

    async def test_commit_batch_called_per_day(self, importer, mock_uow):
        """3 days of records -> commit_batch called 3 times."""
        from_date = datetime(2024, 1, 1, tzinfo=UTC)
        to_date = datetime(2024, 1, 3, 23, 59, 59, tzinfo=UTC)

        importer._fetch_day_records = AsyncMock(side_effect=_fake_fetch_day(2))

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
        assert mock_uow.commit_batch.await_count == 3

    async def test_day_checkpoints_are_keyed_on_the_mixd_user(self, importer, mock_uow):
        """The checkpoint's ``user_id`` is the mixd user, never the Last.fm account.

        ``sync_checkpoints`` is FORCE-RLS'd on ``user_id``, so an account-keyed
        row is rejected on write and invisible on read. A mock UoW accepts either
        key, so the enforcement itself is proven in
        tests/integration/connectors/lastfm/test_lastfm_checkpoint_rls.py — this
        just pins the key at the call site.
        """
        importer._fetch_day_records = AsyncMock(side_effect=_fake_fetch_day(1))

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

    async def test_no_uow_means_no_commit_batch(self, importer):
        """Without a UoW, no checkpoint or commit_batch calls."""
        from_date = datetime(2024, 1, 1, tzinfo=UTC)
        to_date = datetime(2024, 1, 2, 23, 59, 59, tzinfo=UTC)

        importer._fetch_day_records = AsyncMock(side_effect=_fake_fetch_day(1))

        records = await importer._fetch_date_range_strategy(
            from_date=from_date,
            to_date=to_date,
            user_id="mixd-user-1",
            username="test_user",
            batch_id=_BATCH_ID,
            import_timestamp=_IMPORT_TS,
            uow=None,
        )

        assert len(records) == 2  # 2 days * 1 record, no commit calls


class TestPerDayConversion:
    """The day loop yields ledger rows, not a span-wide list of raw scrobbles.

    Conversion happens as each day lands so a multi-year import never holds the
    raw records for the whole span on top of the connector plays.
    """

    async def test_day_records_become_stamped_connector_plays(self, importer):
        importer._fetch_day_records = AsyncMock(side_effect=_fake_fetch_day(2))

        plays = await importer._fetch_date_range_strategy(
            from_date=datetime(2024, 1, 1, tzinfo=UTC),
            to_date=datetime(2024, 1, 3, 23, 59, 59, tzinfo=UTC),
            user_id="mixd-user-1",
            username="test_user",
            batch_id=_BATCH_ID,
            import_timestamp=_IMPORT_TS,
        )

        assert len(plays) == 6
        assert all(isinstance(play, ConnectorTrackPlay) for play in plays)
        # Tenancy at construction: nothing downstream re-stamps it any more, so
        # a miss here would file every row under the "default" tenant.
        assert {play.user_id for play in plays} == {"mixd-user-1"}
        assert {play.import_batch_id for play in plays} == {_BATCH_ID}
        assert {play.import_source for play in plays} == {"lastfm_api"}
        # One timestamp for the batch, not one per day of a multi-year loop.
        assert {play.import_timestamp for play in plays} == {_IMPORT_TS}

    async def test_empty_day_mid_range_still_checkpoints_and_commits(
        self, importer, mock_uow
    ):
        """A silent day contributes nothing but must not stall the loop."""
        importer._fetch_day_records = AsyncMock(
            side_effect=_fake_fetch_day(1, empty_days=frozenset({2}))
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
        assert mock_uow.commit_batch.await_count == 3
        saved = mock_uow.get_checkpoint_repository().save_sync_checkpoint
        assert saved.await_args.args[0].cursor == "2024-01-03@test_user"

    async def test_range_with_no_plays_at_all_returns_empty(self, importer, mock_uow):
        importer._fetch_day_records = AsyncMock(
            side_effect=_fake_fetch_day(0, empty_days=frozenset({1, 2}))
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
        assert mock_uow.commit_batch.await_count == 2


class TestImportPlaysTotals:
    """End-to-end reported totals for the multi-day path, plus ledger tenancy."""

    @staticmethod
    def _importer(side_effect):
        """Importer whose account resolves from env and whose days are faked."""
        connector = Mock()
        connector.lastfm_username = "test_user"
        storage = AsyncMock()
        storage.load_token.return_value = None
        importer = LastfmPlayImporter(lastfm_connector=connector, token_storage=storage)
        importer._fetch_day_records = AsyncMock(side_effect=side_effect)
        return importer

    @staticmethod
    def _params():
        from src.domain.repositories.play import LastfmImportParams

        return LastfmImportParams(
            from_date=datetime(2024, 1, 1, tzinfo=UTC),
            to_date=datetime(2024, 1, 3, 23, 59, 59, tzinfo=UTC),
        )

    async def test_multi_day_totals_and_tenancy(self, mock_uow):
        importer = self._importer(_fake_fetch_day(2))

        result, plays = await importer.import_plays(
            mock_uow, self._params(), user_id="mixd-user-1"
        )

        assert len(plays) == 6  # 3 days * 2 records
        assert result.summary_metrics.get("raw_plays") == 6
        assert result.summary_metrics.get("imported") == 6
        assert {play.user_id for play in plays} == {"mixd-user-1"}

        # The rows actually handed to the repository carry the tenancy too —
        # connector_plays is RLS-scoped on user_id.
        insert = mock_uow.get_connector_play_repository().bulk_insert_connector_plays
        assert [play.user_id for play in insert.await_args.args[0]] == (
            ["mixd-user-1"] * 6
        )

    async def test_empty_span_reports_zeros_without_saving(self, mock_uow):
        """No scrobbles anywhere in the range: the empty-data path, not a crash."""
        importer = self._importer(_fake_fetch_day(0, empty_days=frozenset({1, 2, 3})))

        result, plays = await importer.import_plays(
            mock_uow, self._params(), user_id="mixd-user-1"
        )

        assert plays == []
        assert result.summary_metrics.get("raw_plays") == 0
        assert result.summary_metrics.get("imported") == 0
        insert = mock_uow.get_connector_play_repository().bulk_insert_connector_plays
        insert.assert_not_awaited()


class TestCheckpointAccountTag:
    """The cursor's account tag guards against resuming another account's position.

    The row is keyed on the mixd user, and nothing clears it when the connected
    Last.fm account changes — so the account the days were fetched for rides
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
    """Verify per-day progress events in _fetch_date_range_strategy."""

    async def test_emit_progress_called_per_day(self, importer):
        """3 days of records -> emit_progress called 3 times with increasing counts."""
        from_date = datetime(2024, 1, 1, tzinfo=UTC)
        to_date = datetime(2024, 1, 3, 23, 59, 59, tzinfo=UTC)

        importer._fetch_day_records = AsyncMock(side_effect=_fake_fetch_day(1))

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
        )

        assert emitter.emit_progress.await_count == 3
        # Verify monotonically increasing current values
        calls = emitter.emit_progress.call_args_list
        currents = [call.args[0].current for call in calls]
        assert currents == [1, 2, 3]

    async def test_no_progress_without_emitter(self, importer):
        """Without progress_emitter, no emit_progress calls."""
        from_date = datetime(2024, 1, 1, tzinfo=UTC)
        to_date = datetime(2024, 1, 1, 23, 59, 59, tzinfo=UTC)

        importer._fetch_day_records = AsyncMock(side_effect=_fake_fetch_day(1))

        # No progress_emitter passed — should not raise
        records = await importer._fetch_date_range_strategy(
            from_date=from_date,
            to_date=to_date,
            user_id="mixd-user-1",
            username="test_user",
            batch_id=_BATCH_ID,
            import_timestamp=_IMPORT_TS,
        )
        assert len(records) == 1


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
