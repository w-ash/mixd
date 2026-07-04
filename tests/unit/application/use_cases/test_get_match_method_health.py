"""Unit tests for GetMatchMethodHealthUseCase.

Tests the match method health aggregation with mocked connector repository,
verifying correct category/description mapping, total computation, and grouping.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.application.use_cases.get_match_method_health import (
    GetMatchMethodHealthCommand,
    GetMatchMethodHealthUseCase,
)
from src.domain.repositories.connector import MatchMethodStatRow
from tests.fixtures import make_mock_uow


@pytest.fixture
def mock_uow():
    return make_mock_uow()


def _make_review_stub(*, created_at: datetime) -> MagicMock:
    """Minimal stand-in exposing only the ``created_at`` attribute the use case reads."""
    review = MagicMock()
    review.created_at = created_at
    return review


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
        "band_reject": 0,
        "band_review": 0,
        "band_accept": 10,
        "band_certain": 90,
    }
    return {**defaults, **overrides}


class TestMatchMethodHealthHappyPath:
    """Rows are mapped to stats with categories, total is computed."""

    async def test_rows_mapped_with_categories(self, mock_uow):
        rows = [
            _make_row(
                match_method="direct_import", connector_name="spotify", total_count=500
            ),
            _make_row(
                match_method="isrc_match", connector_name="spotify", total_count=60
            ),
        ]
        mock_uow.get_connector_repository().get_match_method_stats.return_value = rows

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(user_id="test-user"), mock_uow
        )

        assert len(result.stats) == 2
        assert result.stats[0].category == "Primary Import"
        assert result.stats[0].description == "Standard Spotify import"
        assert result.stats[1].category == "Identity Resolution"
        assert result.stats[1].description == "ISRC dedup across services"

    async def test_total_mappings_is_sum_of_totals(self, mock_uow):
        rows = [
            _make_row(total_count=200),
            _make_row(
                match_method="artist_title", connector_name="lastfm", total_count=150
            ),
        ]
        mock_uow.get_connector_repository().get_match_method_stats.return_value = rows

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(user_id="test-user"), mock_uow
        )

        assert result.total_mappings == 350

    async def test_recent_days_passed_through(self, mock_uow):
        mock_uow.get_connector_repository().get_match_method_stats.return_value = []

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(user_id="test-user", recent_days=7), mock_uow
        )

        assert result.recent_days == 7
        mock_uow.get_connector_repository().get_match_method_stats.assert_called_once_with(
            user_id="test-user", recent_days=7
        )


class TestMatchMethodHealthEmptyDB:
    """Empty result when no mappings exist."""

    async def test_empty_result(self, mock_uow):
        mock_uow.get_connector_repository().get_match_method_stats.return_value = []

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(user_id="test-user"), mock_uow
        )

        assert result.stats == []
        assert result.total_mappings == 0
        assert result.by_category == {}


class TestMatchMethodHealthCategoryGrouping:
    """by_category property groups stats correctly."""

    async def test_groups_by_category(self, mock_uow):
        rows = [
            _make_row(match_method="direct_import", total_count=500),
            _make_row(
                match_method="artist_title", connector_name="lastfm", total_count=300
            ),
            _make_row(match_method="isrc_match", total_count=60),
            _make_row(match_method="search_fallback", total_count=20),
        ]
        mock_uow.get_connector_repository().get_match_method_stats.return_value = rows

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(user_id="test-user"), mock_uow
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
            GetMatchMethodHealthCommand(user_id="test-user"), mock_uow
        )

        assert result.stats[0].category == "Unknown"
        assert result.stats[0].description == ""


class TestMatchMethodHealthBands:
    """Confidence-band counts pass through from the repo row to the stat."""

    async def test_band_fields_pass_through(self, mock_uow):
        rows = [_make_row(band_reject=1, band_review=2, band_accept=3, band_certain=4)]
        mock_uow.get_connector_repository().get_match_method_stats.return_value = rows

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(user_id="test-user"), mock_uow
        )

        stat = result.stats[0]
        assert stat.band_reject == 1
        assert stat.band_review == 2
        assert stat.band_accept == 3
        assert stat.band_certain == 4


class TestMatchMethodHealthDrift:
    """Drift-signals panel assembles from the review-repo + connector-repo queries."""

    async def test_fallback_share_computed_per_connector(self, mock_uow):
        rows = [
            _make_row(
                match_method="direct_import",
                connector_name="spotify",
                recent_count=90,
            ),
            _make_row(
                match_method="search_fallback",
                connector_name="spotify",
                recent_count=10,
            ),
            _make_row(
                match_method="artist_title", connector_name="lastfm", recent_count=5
            ),
        ]
        mock_uow.get_connector_repository().get_match_method_stats.return_value = rows

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(user_id="test-user"), mock_uow
        )

        shares = {s.connector_name: s for s in result.drift.fallback_shares}
        assert shares["spotify"].recent_total == 100
        assert shares["spotify"].recent_fallback == 10
        assert shares["spotify"].fallback_share == 0.1
        assert shares["lastfm"].recent_fallback == 0
        assert shares["lastfm"].fallback_share == 0.0

    async def test_fallback_share_includes_stale_id_variant(self, mock_uow):
        """search_fallback* covers both search_fallback and its stale-id variant."""
        rows = [
            _make_row(
                match_method="search_fallback_stale_id",
                connector_name="spotify",
                recent_count=3,
            ),
            _make_row(
                match_method="direct_import",
                connector_name="spotify",
                recent_count=7,
            ),
        ]
        mock_uow.get_connector_repository().get_match_method_stats.return_value = rows

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(user_id="test-user"), mock_uow
        )

        share = result.drift.fallback_shares[0]
        assert share.recent_fallback == 3
        assert share.recent_total == 10

    async def test_no_recent_mappings_yields_zero_share(self, mock_uow):
        rows = [_make_row(recent_count=0)]
        mock_uow.get_connector_repository().get_match_method_stats.return_value = rows

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(user_id="test-user"), mock_uow
        )

        assert result.drift.fallback_shares[0].fallback_share == 0.0

    async def test_review_inflow_and_pending_depth_passed_through(self, mock_uow):
        review_repo = mock_uow.get_match_review_repository()
        review_repo.count_created_since.side_effect = lambda days, *, user_id: {
            7: 2,
            30: 9,
        }[days]
        # review_pending_depth is now taken from list_pending_reviews's total
        # (reused instead of a second identical count_pending() query).
        review_repo.list_pending_reviews.return_value = ([], 4)
        review_repo.count_pending_by_method.return_value = {
            "isrc_suspect": 3,
            "artist_title": 1,
        }

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(user_id="test-user"), mock_uow
        )

        assert result.drift.review_inflow_7d == 2
        assert result.drift.review_inflow_30d == 9
        assert result.drift.review_pending_depth == 4
        assert result.drift.review_pending_by_method == {
            "isrc_suspect": 3,
            "artist_title": 1,
        }
        assert result.drift.isrc_suspect_pending_count == 3

    async def test_isrc_suspect_pending_count_defaults_to_zero(self, mock_uow):
        review_repo = mock_uow.get_match_review_repository()
        review_repo.count_pending_by_method.return_value = {"artist_title": 1}

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(user_id="test-user"), mock_uow
        )

        assert result.drift.isrc_suspect_pending_count == 0

    async def test_oldest_pending_age_computed_from_oldest_review(self, mock_uow):
        review_repo = mock_uow.get_match_review_repository()
        oldest = _make_review_stub(created_at=datetime.now(UTC) - timedelta(days=5))
        review_repo.list_pending_reviews.return_value = ([oldest], 1)

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(user_id="test-user"), mock_uow
        )

        assert result.drift.review_oldest_pending_days == pytest.approx(5.0, abs=0.1)
        review_repo.list_pending_reviews.assert_called_once_with(
            user_id="test-user", limit=1, sort_by="created_at_asc"
        )

    async def test_oldest_pending_age_none_when_queue_empty(self, mock_uow):
        review_repo = mock_uow.get_match_review_repository()
        review_repo.list_pending_reviews.return_value = ([], 0)

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(user_id="test-user"), mock_uow
        )

        assert result.drift.review_oldest_pending_days is None

    async def test_divergence_and_stale_denorm_counts_passed_through(self, mock_uow):
        connector_repo = mock_uow.get_connector_repository()
        connector_repo.count_confidence_evidence_divergence.return_value = 7
        connector_repo.count_stale_denormalized_ids.return_value = 12

        result = await GetMatchMethodHealthUseCase().execute(
            GetMatchMethodHealthCommand(user_id="test-user"), mock_uow
        )

        assert result.drift.confidence_evidence_divergence_count == 7
        assert result.drift.stale_denormalized_ids_count == 12
