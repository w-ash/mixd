"""Unit tests for UnrejectMappingCandidateUseCase.

Covers the withdrawal happy path (repo call + event record + commit) and the
two not-found edges: an unknown connector track, and a pair with no active
rejection to withdraw.
"""

from uuid import uuid7

import pytest

from src.application.use_cases.unreject_mapping_candidate import (
    UnrejectMappingCandidateCommand,
    UnrejectMappingCandidateResult,
    UnrejectMappingCandidateUseCase,
)
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_connector_track
from tests.fixtures.mocks import make_mock_uow


class TestUnrejectHappyPath:
    async def test_withdraws_and_records_event(self):
        connector_track_id = uuid7()
        candidate_track_id = uuid7()
        ct = make_connector_track("sp_123", connector_name="spotify")

        uow = make_mock_uow()
        connector_repo = uow.get_connector_repository()
        connector_repo.get_connector_track_by_id.return_value = ct

        negative_repo = uow.get_resolution_negative_repository()
        negative_repo.unreject.return_value = True

        command = UnrejectMappingCandidateCommand(
            user_id="test-user",
            connector_track_id=connector_track_id,
            candidate_track_id=candidate_track_id,
        )
        result = await UnrejectMappingCandidateUseCase().execute(command, uow)

        assert isinstance(result, UnrejectMappingCandidateResult)
        assert result.connector_name == "spotify"
        assert result.connector_track_id == connector_track_id
        assert result.candidate_track_id == candidate_track_id

        negative_repo.unreject.assert_awaited_once_with(
            user_id="test-user",
            connector_track_id=connector_track_id,
            candidate_track_id=candidate_track_id,
        )
        recorder = uow.get_resolution_recorder()
        recorder.record.assert_awaited_once()
        (decisions,), kwargs = recorder.record.await_args
        assert kwargs["user_id"] == "test-user"
        assert len(decisions) == 1
        assert decisions[0].event_type == "unrejected"
        assert decisions[0].connector_name == "spotify"
        uow.commit.assert_awaited_once()


class TestUnrejectErrors:
    async def test_raises_not_found_for_missing_connector_track(self):
        uow = make_mock_uow()
        uow.get_connector_repository().get_connector_track_by_id.return_value = None

        command = UnrejectMappingCandidateCommand(
            user_id="test-user",
            connector_track_id=uuid7(),
            candidate_track_id=uuid7(),
        )
        with pytest.raises(NotFoundError, match="Connector track"):
            await UnrejectMappingCandidateUseCase().execute(command, uow)

        uow.commit.assert_not_awaited()
        uow.get_resolution_negative_repository().unreject.assert_not_awaited()

    async def test_raises_not_found_when_no_active_rejection(self):
        ct = make_connector_track("sp_123", connector_name="spotify")
        uow = make_mock_uow()
        uow.get_connector_repository().get_connector_track_by_id.return_value = ct
        uow.get_resolution_negative_repository().unreject.return_value = False

        command = UnrejectMappingCandidateCommand(
            user_id="test-user",
            connector_track_id=uuid7(),
            candidate_track_id=uuid7(),
        )
        with pytest.raises(NotFoundError, match="No active rejection"):
            await UnrejectMappingCandidateUseCase().execute(command, uow)

        uow.commit.assert_not_awaited()
        uow.get_resolution_recorder().record.assert_not_awaited()
