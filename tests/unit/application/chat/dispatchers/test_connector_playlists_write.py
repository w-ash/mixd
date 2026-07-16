"""Unit tests for the ``manage_connector_playlist`` two-phase write dispatcher.

``handle_manage_connector_playlist`` proposes (stores a non-destructive pending
action) and ``exec_manage_connector_playlist`` commits through the refresh use
case. The pending-action store is swapped for a fresh instance per test so
proposals don't leak, and ``execute_use_case`` is monkeypatched on the module
under test so the commit path never touches a database or a connector.
"""

from uuid import UUID

import pytest

from src.application.chat.dispatchers import _common, connector_playlists_write
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.services.connector_playlist_sync_service import RefreshFailure
from src.application.use_cases.refresh_connector_playlists import (
    RefreshConnectorPlaylistsResult,
)
from src.domain.entities.shared import ConnectorPlaylistIdentifier
from src.domain.exceptions import NotFoundError, ToolExecutionError
from tests.fixtures import InMemoryPendingActionStore

_CTX = ToolContext(user_id="default")


@pytest.fixture
def fresh_store(monkeypatch: pytest.MonkeyPatch) -> InMemoryPendingActionStore:
    store = InMemoryPendingActionStore()
    monkeypatch.setattr(_common, "pending_action_store", store)
    return store


def _fake_runner(result: object):
    async def _run(factory: object, user_id: str | None = None) -> object:
        return result

    return _run


class TestManageConnectorPlaylistPropose:
    async def test_proposes_pending_confirmation(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        result = await connector_playlists_write.handle_manage_connector_playlist(
            {
                "operation": "refresh",
                "connector": "spotify",
                "identifiers": ["abc", "def"],
            },
            _CTX,
        )

        assert result["status"] == "pending_confirmation"
        details = result["details"]
        assert details["operation"] == "refresh"
        assert details["connector"] == "spotify"
        assert details["identifiers"] == ["abc", "def"]
        assert details["force"] is False
        # Non-destructive: no severity marker on a cache refresh.
        assert "severity" not in details
        assert details["changes"]

        action = await fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.tool_name == "manage_connector_playlist"

    async def test_missing_identifiers_rejected(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="identifiers"):
            await connector_playlists_write.handle_manage_connector_playlist(
                {"operation": "refresh", "connector": "spotify"}, _CTX
            )

    async def test_unknown_operation_rejected(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="refresh"):
            await connector_playlists_write.handle_manage_connector_playlist(
                {
                    "operation": "delete",
                    "connector": "spotify",
                    "identifiers": ["abc"],
                },
                _CTX,
            )


class TestExecManageConnectorPlaylist:
    async def _action(self) -> PendingAction:
        store = InMemoryPendingActionStore()
        return await store.create(
            user_id="default",
            tool_name="manage_connector_playlist",
            tool_input={},
            description="Refresh",
            details={
                "operation": "refresh",
                "connector": "spotify",
                "identifiers": ["abc", "def"],
                "force": True,
            },
        )

    async def test_commits_through_use_case(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = RefreshConnectorPlaylistsResult(
            succeeded=["abc"],
            skipped_unchanged=[],
            failed=[
                RefreshFailure(
                    connector_playlist_identifier=ConnectorPlaylistIdentifier("def"),
                    message="not found",
                )
            ],
        )
        monkeypatch.setattr(_common, "execute_use_case", _fake_runner(result))

        out = await connector_playlists_write.exec_manage_connector_playlist(
            await self._action(), "default"
        )

        assert out["status"] == "confirmed"
        assert out["result"]["succeeded"] == 1
        assert out["result"]["skipped_unchanged"] == 0
        assert out["result"]["failed"][0]["identifier"] == "def"
        assert out["result"]["failed"][0]["message"] == "not found"

    async def test_not_found_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory: object, user_id: str | None = None) -> object:
            raise NotFoundError("gone")

        monkeypatch.setattr(_common, "execute_use_case", _raise)

        with pytest.raises(ToolExecutionError, match="could not be found"):
            await connector_playlists_write.exec_manage_connector_playlist(
                await self._action(), "default"
            )
