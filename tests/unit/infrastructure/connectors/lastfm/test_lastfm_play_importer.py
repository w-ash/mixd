"""Unit tests for LastfmPlayImporter date range calculation and incremental commits."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.infrastructure.connectors.lastfm.play_importer import LastfmPlayImporter


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


def _fake_fetch_day(records_per_day: int = 1):
    """Return a fake _fetch_day_records side-effect producing N records per day."""

    async def _inner(*, username, day_start, day_end, current_date):
        mid = datetime.combine(current_date, datetime.min.time()).replace(
            hour=12, tzinfo=UTC
        )
        return [_make_play_record(mid) for _ in range(records_per_day)]

    return _inner


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

    @pytest.fixture
    def mock_uow(self):
        from tests.fixtures.mocks import make_mock_uow

        uow = make_mock_uow()
        checkpoint_repo = uow.get_checkpoint_repository()
        checkpoint_repo.save_sync_checkpoint = AsyncMock(side_effect=lambda cp: cp)
        return uow

    async def test_commit_batch_called_per_day(self, importer, mock_uow):
        """3 days of records -> commit_batch called 3 times."""
        from_date = datetime(2024, 1, 1, tzinfo=UTC)
        to_date = datetime(2024, 1, 3, 23, 59, 59, tzinfo=UTC)

        importer._fetch_day_records = AsyncMock(side_effect=_fake_fetch_day(2))

        records = await importer._fetch_date_range_strategy(
            from_date=from_date,
            to_date=to_date,
            username="test_user",
            uow=mock_uow,
        )

        assert len(records) == 6  # 3 days * 2 records
        assert mock_uow.commit_batch.await_count == 3

    async def test_no_uow_means_no_commit_batch(self, importer):
        """Without a UoW, no checkpoint or commit_batch calls."""
        from_date = datetime(2024, 1, 1, tzinfo=UTC)
        to_date = datetime(2024, 1, 2, 23, 59, 59, tzinfo=UTC)

        importer._fetch_day_records = AsyncMock(side_effect=_fake_fetch_day(1))

        records = await importer._fetch_date_range_strategy(
            from_date=from_date,
            to_date=to_date,
            username="test_user",
            uow=None,
        )

        assert len(records) == 2  # 2 days * 1 record, no commit calls


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
            username="test_user",
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
            username="test_user",
        )
        assert len(records) == 1
