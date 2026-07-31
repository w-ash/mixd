"""Unit tests for ListResolutionNegativesUseCase.

Covers the two things the use case actually decides: that ``kind`` selects which
half of the negative cache is read, and that a narrowing id list reaches the
repository verbatim rather than being defaulted away.
"""

from uuid import uuid7

from src.application.use_cases.list_resolution_negatives import (
    ListResolutionNegativesCommand,
    ListResolutionNegativesUseCase,
)
from src.domain.repositories.resolution import NegativeListing
from tests.fixtures.mocks import make_mock_uow


class TestListResolutionNegatives:
    async def test_lists_negatives_for_kind(self) -> None:
        listing = NegativeListing(
            connector_track_id=uuid7(),
            connector_name="spotify",
            connector_track_identifier="sp_123",
            track_id=uuid7(),
            track_title="Some Song",
        )
        uow = make_mock_uow()
        negative_repo = uow.get_resolution_negative_repository()
        negative_repo.list_negatives.return_value = [listing]

        result = await ListResolutionNegativesUseCase().execute(
            ListResolutionNegativesCommand(user_id="test-user", kind="rejected"), uow
        )

        assert result.listings == [listing]
        negative_repo.list_negatives.assert_awaited_once_with(
            user_id="test-user", kind="rejected", connector_track_ids=None
        )

    async def test_passes_narrowing_ids_through(self) -> None:
        connector_track_ids = [uuid7(), uuid7()]
        uow = make_mock_uow()
        negative_repo = uow.get_resolution_negative_repository()
        negative_repo.list_negatives.return_value = []

        result = await ListResolutionNegativesUseCase().execute(
            ListResolutionNegativesCommand(
                user_id="test-user",
                kind="dead_id",
                connector_track_ids=connector_track_ids,
            ),
            uow,
        )

        assert result.listings == []
        negative_repo.list_negatives.assert_awaited_once_with(
            user_id="test-user",
            kind="dead_id",
            connector_track_ids=connector_track_ids,
        )
