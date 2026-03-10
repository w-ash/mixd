"""Unit tests for RelinkConnectorTrackUseCase.

Tests the relink workflow: move a connector mapping from one canonical
track to another, with primary reassignment and validation guards.
"""

from unittest.mock import AsyncMock

import pytest

from src.application.use_cases.relink_connector_track import (
    RelinkConnectorTrackCommand,
    RelinkConnectorTrackUseCase,
)
from src.domain.entities.track_mapping import TrackMapping
from src.domain.exceptions import NotFoundError
from tests.fixtures import (
    make_mock_connector_repo,
    make_mock_track_repo,
    make_mock_uow,
    make_track,
)


def _make_mapping(
    mapping_id: int = 1,
    track_id: int = 10,
    connector_track_id: int = 100,
    connector_name: str = "spotify",
    confidence: int = 95,
    is_primary: bool = True,
    origin: str = "automatic",
) -> TrackMapping:
    return TrackMapping(
        id=mapping_id,
        track_id=track_id,
        connector_track_id=connector_track_id,
        connector_name=connector_name,
        match_method="isrc",
        confidence=confidence,
        origin=origin,
        is_primary=is_primary,
    )


class TestRelinkHappyPath:
    """Mapping moves to new track with proper primary reassignment."""

    async def test_mapping_moves_to_new_track(self) -> None:
        mapping = _make_mapping(track_id=10)
        connector_repo = make_mock_connector_repo()
        connector_repo.get_mapping_by_id = AsyncMock(return_value=mapping)
        connector_repo.update_mapping_track = AsyncMock(
            return_value=TrackMapping(
                id=1, track_id=20, connector_track_id=100,
                connector_name="spotify", match_method="isrc",
                confidence=95, origin="manual_override", is_primary=False,
            )
        )
        connector_repo.ensure_primary_for_connector = AsyncMock()
        track_repo = make_mock_track_repo()
        track_repo.get_by_id = AsyncMock(return_value=make_track(id=20))
        uow = make_mock_uow(connector_repo=connector_repo, track_repo=track_repo)

        command = RelinkConnectorTrackCommand(
            mapping_id=1, new_track_id=20, current_track_id=10
        )
        result = await RelinkConnectorTrackUseCase().execute(command, uow)

        assert result.old_track_id == 10
        assert result.new_track_id == 20
        connector_repo.update_mapping_track.assert_awaited_once_with(
            1, 20, "manual_override"
        )

    async def test_primary_reassigned_on_both_tracks(self) -> None:
        mapping = _make_mapping(track_id=10, is_primary=True)
        connector_repo = make_mock_connector_repo()
        connector_repo.get_mapping_by_id = AsyncMock(return_value=mapping)
        connector_repo.update_mapping_track = AsyncMock(
            return_value=_make_mapping(track_id=20, is_primary=False, origin="manual_override")
        )
        connector_repo.ensure_primary_for_connector = AsyncMock()
        track_repo = make_mock_track_repo()
        track_repo.get_by_id = AsyncMock(return_value=make_track(id=20))
        uow = make_mock_uow(connector_repo=connector_repo, track_repo=track_repo)

        command = RelinkConnectorTrackCommand(
            mapping_id=1, new_track_id=20, current_track_id=10
        )
        await RelinkConnectorTrackUseCase().execute(command, uow)

        # ensure_primary called for both old and new track
        calls = connector_repo.ensure_primary_for_connector.await_args_list
        assert len(calls) == 2
        assert calls[0].args == (10, "spotify")
        assert calls[1].args == (20, "spotify")

    async def test_commit_called(self) -> None:
        mapping = _make_mapping(track_id=10)
        connector_repo = make_mock_connector_repo()
        connector_repo.get_mapping_by_id = AsyncMock(return_value=mapping)
        connector_repo.update_mapping_track = AsyncMock(
            return_value=_make_mapping(track_id=20, origin="manual_override", is_primary=False)
        )
        connector_repo.ensure_primary_for_connector = AsyncMock()
        track_repo = make_mock_track_repo()
        track_repo.get_by_id = AsyncMock(return_value=make_track(id=20))
        uow = make_mock_uow(connector_repo=connector_repo, track_repo=track_repo)

        command = RelinkConnectorTrackCommand(
            mapping_id=1, new_track_id=20, current_track_id=10
        )
        await RelinkConnectorTrackUseCase().execute(command, uow)

        uow.commit.assert_awaited_once()


class TestRelinkValidation:
    """Validation errors for invalid relink requests."""

    async def test_self_relink_raises_value_error(self) -> None:
        mapping = _make_mapping(track_id=10)
        connector_repo = make_mock_connector_repo()
        connector_repo.get_mapping_by_id = AsyncMock(return_value=mapping)
        uow = make_mock_uow(connector_repo=connector_repo)

        command = RelinkConnectorTrackCommand(
            mapping_id=1, new_track_id=10, current_track_id=10
        )
        with pytest.raises(ValueError, match="same track"):
            await RelinkConnectorTrackUseCase().execute(command, uow)

    async def test_missing_mapping_raises_not_found(self) -> None:
        connector_repo = make_mock_connector_repo()
        connector_repo.get_mapping_by_id = AsyncMock(return_value=None)
        uow = make_mock_uow(connector_repo=connector_repo)

        command = RelinkConnectorTrackCommand(
            mapping_id=999, new_track_id=20, current_track_id=10
        )
        with pytest.raises(NotFoundError, match="999"):
            await RelinkConnectorTrackUseCase().execute(command, uow)

    async def test_missing_target_track_raises_not_found(self) -> None:
        mapping = _make_mapping(track_id=10)
        connector_repo = make_mock_connector_repo()
        connector_repo.get_mapping_by_id = AsyncMock(return_value=mapping)
        track_repo = make_mock_track_repo()
        track_repo.get_by_id = AsyncMock(side_effect=NotFoundError("Track 20 not found"))
        uow = make_mock_uow(connector_repo=connector_repo, track_repo=track_repo)

        command = RelinkConnectorTrackCommand(
            mapping_id=1, new_track_id=20, current_track_id=10
        )
        with pytest.raises(NotFoundError, match="20"):
            await RelinkConnectorTrackUseCase().execute(command, uow)

    async def test_track_id_mismatch_raises_value_error(self) -> None:
        mapping = _make_mapping(track_id=10)
        connector_repo = make_mock_connector_repo()
        connector_repo.get_mapping_by_id = AsyncMock(return_value=mapping)
        uow = make_mock_uow(connector_repo=connector_repo)

        command = RelinkConnectorTrackCommand(
            mapping_id=1, new_track_id=20, current_track_id=99
        )
        with pytest.raises(ValueError, match="does not belong"):
            await RelinkConnectorTrackUseCase().execute(command, uow)
