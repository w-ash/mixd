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
from src.domain.playlist import PlaylistOpsOutcome
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


class TestPushOutcomeGatesSyncStatus:
    """Success keys on the connector's applied/failed outcome, NOT snapshot
    presence (Story 9). A partial push surfaces as ERROR; a fully-applied push
    with no snapshot id is still a success.
    """

    async def test_partial_apply_raises_connector_sync_error(self):
        # failed > 0 → not fully applied → must route to ERROR, never a silent
        # SYNCED-with-0-changes.
        connector = MagicMock()
        connector.execute_playlist_operations = AsyncMock(
            return_value=PlaylistOpsOutcome(snapshot_id="s", requested=2, failed=1)
        )
        uow = MagicMock()
        uow.get_track_repository = MagicMock(return_value=MagicMock())
        ops = [MagicMock(), MagicMock()]

        with patch(
            "src.application.use_cases.update_connector_playlist.resolve_playlist_connector",
            return_value=connector,
        ):
            with pytest.raises(ConnectorSyncError):
                await (
                    UpdateConnectorPlaylistUseCase()._execute_connector_api_operations(
                        MagicMock(), ops, _command(), uow
                    )
                )

    async def test_no_snapshot_but_fully_applied_is_success(self):
        # snapshot_id=None is NOT a failure when every op applied (e.g. a
        # remove-only push) — fixes the prior false-fail + skipped DB update.
        connector = MagicMock()
        connector.execute_playlist_operations = AsyncMock(
            return_value=PlaylistOpsOutcome(snapshot_id=None, requested=2, failed=0)
        )
        uow = MagicMock()
        uow.get_track_repository = MagicMock(return_value=MagicMock())
        ops = [MagicMock(), MagicMock()]

        with patch(
            "src.application.use_cases.update_connector_playlist.resolve_playlist_connector",
            return_value=connector,
        ):
            result = await (
                UpdateConnectorPlaylistUseCase()._run_connector_api_operations(
                    ops, _command(), uow
                )
            )

        assert result["success"] is True
        assert result["metadata"]["snapshot_id"] is None


class TestRunConnectorApiOperationsSuccess:
    """The success path returns the outcome-backed result and no longer issues
    the pre/post ``get_playlist_details`` verification calls (removed ceremony).
    """

    async def test_success_returns_snapshot_result_without_verification_gets(self):
        connector = MagicMock()
        connector.execute_playlist_operations = AsyncMock(
            return_value=PlaylistOpsOutcome(snapshot_id="snap-1", requested=2, failed=0)
        )
        connector.get_playlist_details = AsyncMock()  # must NOT be called now
        uow = MagicMock()
        uow.get_track_repository = MagicMock(return_value=MagicMock())
        ops = [MagicMock(), MagicMock()]

        with patch(
            "src.application.use_cases.update_connector_playlist.resolve_playlist_connector",
            return_value=connector,
        ):
            result = await (
                UpdateConnectorPlaylistUseCase()._run_connector_api_operations(
                    ops, _command(), uow
                )
            )

        assert result["success"] is True
        assert result["api_calls_made"] == 2
        assert result["metadata"]["snapshot_id"] == "snap-1"
        connector.get_playlist_details.assert_not_called()


class TestPersistRaisesOnDbFailure:
    """A DB failure after a successful external push must propagate (not be
    swallowed by a log-only read-back) so the seam can classify it.
    """

    async def test_db_failure_propagates(self):
        connector_repo = MagicMock()
        connector_repo.upsert_model = AsyncMock(side_effect=RuntimeError("db down"))

        with pytest.raises(RuntimeError, match="db down"):
            await UpdateConnectorPlaylistUseCase()._persist_connector_playlist(
                connector_repo, MagicMock(), _command(), 2
            )
