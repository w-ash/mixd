"""Failure-surfacing tests for CreateConnectorPlaylistUseCase.

Pins the v0.8.5 behavior: a failed external playlist create must RAISE
ConnectorSyncError rather than return a success-shaped result — so a workflow's
``destination.create`` node fails the run loudly instead of recording a
COMPLETED run with no playlist created. Mirrors the sibling
``update_connector_playlist`` contract.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.use_cases.create_connector_playlist import (
    CreateConnectorPlaylistCommand,
    CreateConnectorPlaylistUseCase,
)
from src.domain.entities.track import TrackList
from src.domain.exceptions import ConnectorSyncError
from tests.fixtures import make_mock_uow, make_tracks

_RESOLVE = (
    "src.application.use_cases.create_connector_playlist.resolve_playlist_connector"
)


def _command() -> CreateConnectorPlaylistCommand:
    return CreateConnectorPlaylistCommand(
        user_id="u1",
        tracklist=TrackList(tracks=make_tracks(count=2)),
        playlist_name="My Playlist",
        connector="spotify",
    )


class TestCreateConnectorFailureSurfacing:
    """A failed external create raises instead of being swallowed."""

    async def test_external_failure_raises_connector_sync_error(self):
        connector = MagicMock()
        connector.create_playlist = AsyncMock(side_effect=RuntimeError("403 forbidden"))
        with (
            patch(_RESOLVE, return_value=connector),
            pytest.raises(ConnectorSyncError, match="spotify playlist sync failed"),
        ):
            await CreateConnectorPlaylistUseCase().execute(_command(), make_mock_uow())

    async def test_connector_sync_error_preserves_cause(self):
        cause = RuntimeError("connection reset")
        connector = MagicMock()
        connector.create_playlist = AsyncMock(side_effect=cause)
        with patch(_RESOLVE, return_value=connector):
            with pytest.raises(ConnectorSyncError) as exc_info:
                await CreateConnectorPlaylistUseCase().execute(
                    _command(), make_mock_uow()
                )
        assert exc_info.value.__cause__ is cause
        assert exc_info.value.connector == "spotify"
