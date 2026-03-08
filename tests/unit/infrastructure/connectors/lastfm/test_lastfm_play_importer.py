"""Unit tests for LastfmPlayImporter date range calculation."""

from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest

from src.infrastructure.connectors.lastfm.play_importer import LastfmPlayImporter


class TestDateRangeCalculation:
    """Unit tests for _determine_date_range() business logic."""

    @pytest.fixture
    def importer(self):
        """LastfmPlayImporter with mocked connector."""
        with patch(
            "src.infrastructure.connectors.lastfm.play_importer.LastFMConnector"
        ) as mock_connector_class:
            mock_connector = Mock()
            mock_connector.lastfm_username = "test_user"
            mock_connector_class.return_value = mock_connector
            yield LastfmPlayImporter(lastfm_connector=mock_connector)

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
