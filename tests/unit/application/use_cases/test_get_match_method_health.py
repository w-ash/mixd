"""Unit tests for GetMatchMethodHealthUseCase.

Tests the match method health aggregation with mocked connector repository,
verifying correct category/description mapping, total computation, and grouping.
"""

import pytest

from src.application.use_cases.get_match_method_health import (
    GetMatchMethodHealthCommand,
    GetMatchMethodHealthUseCase,
)
from src.domain.repositories.interfaces import MatchMethodStatRow
from tests.fixtures import make_mock_uow


@pytest.fixture
def mock_uow():
    return make_mock_uow()


def _make_row(**overrides) -> MatchMethodStatRow:
    """Build a MatchMethodStatRow with sensible defaults."""
    defaults: MatchMethodStatRow = {
        "match_method": "direct_import",
        "connector_name": "spotify",
        "total_count": 100,
        "recent_count": 10,
        "avg_confidence": 95.0,
        "min_confidence": 90,
        "max_confidence": 100,
    }
    return {**defaults, **overrides}


class TestMatchMethodHealthHappyPath:
    """Rows are mapped to stats with categories, total is computed."""

    async def test_rows_mapped_with_categories(self, mock_uow):
        rows = [
            _make_row(match_method="direct_import", connector_name="spotify", total_count=500),
            _make_row(match_method="isrc_match", connector_name="spotify", total_count=60),
        ]
        mock_uow.get_connector_repository().get_match_method_stats.return_value = rows

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(), mock_uow
        )

        assert len(result.stats) == 2
        assert result.stats[0].category == "Primary Import"
        assert result.stats[0].description == "Standard Spotify import"
        assert result.stats[1].category == "Identity Resolution"
        assert result.stats[1].description == "ISRC dedup across services"

    async def test_total_mappings_is_sum_of_totals(self, mock_uow):
        rows = [
            _make_row(total_count=200),
            _make_row(match_method="artist_title", connector_name="lastfm", total_count=150),
        ]
        mock_uow.get_connector_repository().get_match_method_stats.return_value = rows

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(), mock_uow
        )

        assert result.total_mappings == 350

    async def test_recent_days_passed_through(self, mock_uow):
        mock_uow.get_connector_repository().get_match_method_stats.return_value = []

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(recent_days=7), mock_uow
        )

        assert result.recent_days == 7
        mock_uow.get_connector_repository().get_match_method_stats.assert_called_once_with(
            recent_days=7
        )


class TestMatchMethodHealthEmptyDB:
    """Empty result when no mappings exist."""

    async def test_empty_result(self, mock_uow):
        mock_uow.get_connector_repository().get_match_method_stats.return_value = []

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(), mock_uow
        )

        assert result.stats == []
        assert result.total_mappings == 0
        assert result.by_category == {}


class TestMatchMethodHealthCategoryGrouping:
    """by_category property groups stats correctly."""

    async def test_groups_by_category(self, mock_uow):
        rows = [
            _make_row(match_method="direct_import", total_count=500),
            _make_row(match_method="artist_title", connector_name="lastfm", total_count=300),
            _make_row(match_method="isrc_match", total_count=60),
            _make_row(match_method="search_fallback", total_count=20),
        ]
        mock_uow.get_connector_repository().get_match_method_stats.return_value = rows

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(), mock_uow
        )

        groups = result.by_category
        assert len(groups["Primary Import"]) == 2
        assert len(groups["Identity Resolution"]) == 1
        assert len(groups["Error Recovery"]) == 1


class TestMatchMethodHealthUnknownMethod:
    """Unknown match_method gets 'Unknown' category and empty description."""

    async def test_unknown_method(self, mock_uow):
        rows = [_make_row(match_method="some_future_method", total_count=5)]
        mock_uow.get_connector_repository().get_match_method_stats.return_value = rows

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(), mock_uow
        )

        assert result.stats[0].category == "Unknown"
        assert result.stats[0].description == ""
