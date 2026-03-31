"""Tests for the CheckDataIntegrityUseCase."""

import pytest

from src.application.use_cases.check_data_integrity import (
    CheckDataIntegrityCommand,
    CheckDataIntegrityUseCase,
)
from tests.fixtures import make_mock_uow


class TestAllChecksPass:
    """When no anomalies exist, all checks pass."""

    @pytest.fixture
    def uow(self):
        return make_mock_uow()

    @pytest.mark.asyncio
    async def test_all_pass_overall_status(self, uow):
        result = await CheckDataIntegrityUseCase().execute(
            CheckDataIntegrityCommand(user_id="test-user"), uow
        )
        assert result.overall_status == "pass"
        assert result.total_issues == 0

    @pytest.mark.asyncio
    async def test_six_checks_returned(self, uow):
        result = await CheckDataIntegrityUseCase().execute(
            CheckDataIntegrityCommand(user_id="test-user"), uow
        )
        assert len(result.checks) == 6
        names = {c.name for c in result.checks}
        assert names == {
            "multiple_primary_mappings",
            "missing_primary_mappings",
            "orphaned_connector_tracks",
            "duplicate_tracks",
            "stale_pending_reviews",
            "pending_reviews",
        }


class TestFailStatus:
    """Multiple primary mappings trigger fail status."""

    @pytest.mark.asyncio
    async def test_multiple_primaries_causes_fail(self):
        uow = make_mock_uow()
        connector_repo = uow.get_connector_repository()
        connector_repo.find_multiple_primary_violations.return_value = [
            {"track_id": 1, "connector_name": "spotify", "primary_count": 2}
        ]

        result = await CheckDataIntegrityUseCase().execute(
            CheckDataIntegrityCommand(user_id="test-user"), uow
        )
        assert result.overall_status == "fail"

        multi_check = next(
            c for c in result.checks if c.name == "multiple_primary_mappings"
        )
        assert multi_check.status == "fail"
        assert multi_check.count == 1
        assert multi_check.details[0]["track_id"] == 1


class TestWarnStatus:
    """Non-critical anomalies trigger warn status."""

    @pytest.mark.asyncio
    async def test_missing_primaries_causes_warn(self):
        uow = make_mock_uow()
        connector_repo = uow.get_connector_repository()
        connector_repo.find_missing_primary_violations.return_value = [
            {"track_id": 5, "connector_name": "lastfm", "mapping_count": 3}
        ]

        result = await CheckDataIntegrityUseCase().execute(
            CheckDataIntegrityCommand(user_id="test-user"), uow
        )
        assert result.overall_status == "warn"

    @pytest.mark.asyncio
    async def test_orphaned_tracks_causes_warn(self):
        uow = make_mock_uow()
        connector_repo = uow.get_connector_repository()
        connector_repo.count_orphaned_connector_tracks.return_value = 10

        result = await CheckDataIntegrityUseCase().execute(
            CheckDataIntegrityCommand(user_id="test-user"), uow
        )
        orphan_check = next(
            c for c in result.checks if c.name == "orphaned_connector_tracks"
        )
        assert orphan_check.status == "warn"
        assert orphan_check.count == 10

    @pytest.mark.asyncio
    async def test_duplicates_causes_warn(self):
        uow = make_mock_uow()
        track_repo = uow.get_track_repository()
        track_repo.find_duplicate_tracks_by_fingerprint.return_value = [
            {
                "title": "Song",
                "artist": "Artist",
                "album": "Album",
                "count": 2,
                "track_ids": [1, 2],
            }
        ]

        result = await CheckDataIntegrityUseCase().execute(
            CheckDataIntegrityCommand(user_id="test-user"), uow
        )
        dup_check = next(c for c in result.checks if c.name == "duplicate_tracks")
        assert dup_check.status == "warn"
        assert dup_check.count == 1

    @pytest.mark.asyncio
    async def test_stale_reviews_causes_warn(self):
        uow = make_mock_uow()
        review_repo = uow.get_match_review_repository()
        review_repo.count_stale_pending.return_value = 5

        result = await CheckDataIntegrityUseCase().execute(
            CheckDataIntegrityCommand(user_id="test-user"), uow
        )
        stale_check = next(
            c for c in result.checks if c.name == "stale_pending_reviews"
        )
        assert stale_check.status == "warn"
        assert stale_check.count == 5


class TestPendingReviewsInformational:
    """Pending review count is always 'pass' regardless of count."""

    @pytest.mark.asyncio
    async def test_high_pending_count_still_passes(self):
        uow = make_mock_uow()
        review_repo = uow.get_match_review_repository()
        review_repo.count_pending.return_value = 100

        result = await CheckDataIntegrityUseCase().execute(
            CheckDataIntegrityCommand(user_id="test-user"), uow
        )
        pending_check = next(c for c in result.checks if c.name == "pending_reviews")
        assert pending_check.status == "pass"
        assert pending_check.count == 100
        # Overall should still be pass since pending_reviews is informational
        assert result.overall_status == "pass"


class TestFailOverridesWarn:
    """Fail takes precedence over warn in overall status."""

    @pytest.mark.asyncio
    async def test_fail_and_warn_together(self):
        uow = make_mock_uow()
        connector_repo = uow.get_connector_repository()
        connector_repo.find_multiple_primary_violations.return_value = [
            {"track_id": 1, "connector_name": "spotify", "primary_count": 2}
        ]
        connector_repo.count_orphaned_connector_tracks.return_value = 5

        result = await CheckDataIntegrityUseCase().execute(
            CheckDataIntegrityCommand(user_id="test-user"), uow
        )
        assert result.overall_status == "fail"
