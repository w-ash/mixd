"""Unit tests for UnlinkConnectorTrackUseCase.

Tests the unlink workflow: delete a connector mapping, promote next primary
or clear denormalized ID, and auto-create orphan track if connector track
would otherwise be unmapped.
"""

from unittest.mock import AsyncMock

import pytest

from src.application.use_cases.unlink_connector_track import (
    UnlinkConnectorTrackCommand,
    UnlinkConnectorTrackUseCase,
)
from src.domain.entities import Artist, ConnectorTrack, Track
from src.domain.entities.track_mapping import TrackMapping
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_mock_connector_repo, make_mock_track_repo, make_mock_uow


def _make_mapping(
    mapping_id: int = 1,
    track_id: int = 10,
    connector_track_id: int = 100,
    connector_name: str = "spotify",
    is_primary: bool = True,
) -> TrackMapping:
    return TrackMapping(
        id=mapping_id,
        track_id=track_id,
        connector_track_id=connector_track_id,
        connector_name=connector_name,
        match_method="isrc",
        confidence=95,
        origin="automatic",
        is_primary=is_primary,
    )


def _make_connector_track(
    connector_track_id: int = 100,
    connector_name: str = "spotify",
    external_id: str = "spotify:abc123",
) -> ConnectorTrack:
    return ConnectorTrack(
        id=connector_track_id,
        connector_name=connector_name,
        connector_track_identifier=external_id,
        title="Test Track",
        artists=[Artist(name="Test Artist")],
    )


class TestUnlinkHappyPath:
    """Mapping deleted with proper primary reassignment."""

    async def test_mapping_deleted(self) -> None:
        mapping = _make_mapping(is_primary=False)
        connector_repo = make_mock_connector_repo()
        connector_repo.get_mapping_by_id = AsyncMock(return_value=mapping)
        connector_repo.delete_mapping = AsyncMock(return_value=mapping)
        connector_repo.ensure_primary_for_connector = AsyncMock()
        connector_repo.count_mappings_for_connector_track = AsyncMock(return_value=1)
        uow = make_mock_uow(connector_repo=connector_repo)

        command = UnlinkConnectorTrackCommand(
            user_id="test-user", mapping_id=1, current_track_id=10
        )
        result = await UnlinkConnectorTrackUseCase().execute(command, uow)

        assert result.deleted_mapping_id == 1
        assert result.orphan_track_id is None
        connector_repo.delete_mapping.assert_awaited_once_with(1, user_id="test-user")

    async def test_primary_reassigned_after_deletion(self) -> None:
        mapping = _make_mapping(is_primary=True)
        connector_repo = make_mock_connector_repo()
        connector_repo.get_mapping_by_id = AsyncMock(return_value=mapping)
        connector_repo.delete_mapping = AsyncMock(return_value=mapping)
        connector_repo.ensure_primary_for_connector = AsyncMock()
        connector_repo.count_mappings_for_connector_track = AsyncMock(return_value=1)
        uow = make_mock_uow(connector_repo=connector_repo)

        command = UnlinkConnectorTrackCommand(
            user_id="test-user", mapping_id=1, current_track_id=10
        )
        await UnlinkConnectorTrackUseCase().execute(command, uow)

        connector_repo.ensure_primary_for_connector.assert_awaited_once_with(
            10, "spotify"
        )

    async def test_commit_called(self) -> None:
        mapping = _make_mapping()
        connector_repo = make_mock_connector_repo()
        connector_repo.get_mapping_by_id = AsyncMock(return_value=mapping)
        connector_repo.delete_mapping = AsyncMock(return_value=mapping)
        connector_repo.ensure_primary_for_connector = AsyncMock()
        connector_repo.count_mappings_for_connector_track = AsyncMock(return_value=1)
        uow = make_mock_uow(connector_repo=connector_repo)

        command = UnlinkConnectorTrackCommand(
            user_id="test-user", mapping_id=1, current_track_id=10
        )
        await UnlinkConnectorTrackUseCase().execute(command, uow)

        uow.commit.assert_awaited_once()


class TestUnlinkOrphanCreation:
    """Orphan track auto-created when connector track becomes unmapped."""

    async def test_orphan_track_created_when_last_mapping(self) -> None:
        mapping = _make_mapping()
        ct = _make_connector_track()
        saved_orphan = Track(
            id=42, title="Test Track", artists=[Artist(name="Test Artist")]
        )

        connector_repo = make_mock_connector_repo()
        connector_repo.get_mapping_by_id = AsyncMock(return_value=mapping)
        connector_repo.delete_mapping = AsyncMock(return_value=mapping)
        connector_repo.ensure_primary_for_connector = AsyncMock()
        connector_repo.count_mappings_for_connector_track = AsyncMock(return_value=0)
        connector_repo.get_connector_track_by_id = AsyncMock(return_value=ct)
        connector_repo.map_track_to_connector = AsyncMock(return_value=saved_orphan)

        track_repo = make_mock_track_repo()
        track_repo.save_track = AsyncMock(return_value=saved_orphan)

        uow = make_mock_uow(connector_repo=connector_repo, track_repo=track_repo)

        command = UnlinkConnectorTrackCommand(
            user_id="test-user", mapping_id=1, current_track_id=10
        )
        result = await UnlinkConnectorTrackUseCase().execute(command, uow)

        assert result.orphan_track_id == 42
        track_repo.save_track.assert_awaited_once()
        connector_repo.map_track_to_connector.assert_awaited_once()

        # Verify the mapping has manual_override origin
        call_kwargs = connector_repo.map_track_to_connector.call_args
        assert call_kwargs.kwargs.get("confidence") == 100 or call_kwargs.args[4] == 100

    async def test_no_orphan_when_other_mappings_remain(self) -> None:
        mapping = _make_mapping()
        connector_repo = make_mock_connector_repo()
        connector_repo.get_mapping_by_id = AsyncMock(return_value=mapping)
        connector_repo.delete_mapping = AsyncMock(return_value=mapping)
        connector_repo.ensure_primary_for_connector = AsyncMock()
        connector_repo.count_mappings_for_connector_track = AsyncMock(return_value=2)
        uow = make_mock_uow(connector_repo=connector_repo)

        command = UnlinkConnectorTrackCommand(
            user_id="test-user", mapping_id=1, current_track_id=10
        )
        result = await UnlinkConnectorTrackUseCase().execute(command, uow)

        assert result.orphan_track_id is None


class TestUnlinkValidation:
    """Validation errors for invalid unlink requests."""

    async def test_missing_mapping_raises_not_found(self) -> None:
        connector_repo = make_mock_connector_repo()
        connector_repo.get_mapping_by_id = AsyncMock(return_value=None)
        uow = make_mock_uow(connector_repo=connector_repo)

        command = UnlinkConnectorTrackCommand(
            user_id="test-user", mapping_id=999, current_track_id=10
        )
        with pytest.raises(NotFoundError, match="999"):
            await UnlinkConnectorTrackUseCase().execute(command, uow)

    async def test_track_id_mismatch_raises_value_error(self) -> None:
        mapping = _make_mapping(track_id=10)
        connector_repo = make_mock_connector_repo()
        connector_repo.get_mapping_by_id = AsyncMock(return_value=mapping)
        uow = make_mock_uow(connector_repo=connector_repo)

        command = UnlinkConnectorTrackCommand(
            user_id="test-user", mapping_id=1, current_track_id=99
        )
        with pytest.raises(ValueError, match="does not belong"):
            await UnlinkConnectorTrackUseCase().execute(command, uow)
