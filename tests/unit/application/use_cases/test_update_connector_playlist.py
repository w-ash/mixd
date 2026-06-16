"""Characterization tests for UpdateConnectorPlaylistUseCase failure surfacing.

The use case (~1k lines) had zero direct tests. These pin the v0.8.5 behavior
change: a failed connector API call must RAISE ConnectorSyncError rather than
return a success-shaped result — so SyncPlaylistLinkUseCase marks the link
ERROR (not SYNCED) and the CLI/web never print "Sync complete" on a failed push.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.use_cases.update_connector_playlist import (
    UpdateConnectorPlaylistCommand,
    UpdateConnectorPlaylistUseCase,
)
from src.domain.entities.shared import ConnectorPlaylistIdentifier
from src.domain.entities.track import TrackList
from src.domain.exceptions import ConnectorSyncError
from tests.fixtures import make_tracks


def _command() -> UpdateConnectorPlaylistCommand:
    return UpdateConnectorPlaylistCommand(
        user_id="u1",
        connector_playlist_identifier=ConnectorPlaylistIdentifier("sp-1"),
        new_tracklist=TrackList(tracks=make_tracks(count=2)),
        connector="spotify",
    )


class TestConnectorApiFailureSurfacing:
    """A connector API failure raises instead of being swallowed."""

    async def test_api_failure_raises_connector_sync_error(self):
        # The connector raised (429, network, auth, ...) — the use case must not
        # convert that into a {"success": False} dict the seam can't read.
        with patch.object(
            UpdateConnectorPlaylistUseCase,
            "_run_connector_api_operations",
            new=AsyncMock(side_effect=RuntimeError("429 rate limited")),
        ):
            with pytest.raises(
                ConnectorSyncError, match="spotify playlist sync failed"
            ):
                await (
                    UpdateConnectorPlaylistUseCase()._execute_connector_api_operations(
                        MagicMock(), [MagicMock()], _command(), MagicMock()
                    )
                )

    async def test_connector_sync_error_preserves_cause(self):
        cause = RuntimeError("connection reset")
        with patch.object(
            UpdateConnectorPlaylistUseCase,
            "_run_connector_api_operations",
            new=AsyncMock(side_effect=cause),
        ):
            with pytest.raises(ConnectorSyncError) as exc_info:
                await (
                    UpdateConnectorPlaylistUseCase()._execute_connector_api_operations(
                        MagicMock(), [MagicMock()], _command(), MagicMock()
                    )
                )
        assert exc_info.value.__cause__ is cause
        assert exc_info.value.connector == "spotify"

    async def test_no_operations_is_a_success_noop(self):
        # An empty op list is a legitimate no-op, not a failure — no raise.
        result = (
            await UpdateConnectorPlaylistUseCase()._execute_connector_api_operations(
                MagicMock(), [], _command(), MagicMock()
            )
        )
        assert result["success"] is True
        assert result["api_calls_made"] == 0
