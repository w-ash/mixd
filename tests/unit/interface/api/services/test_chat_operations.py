"""Unit tests for the chat ``OperationLauncher`` (``launch_chat_operation``).

Each confirmed long-running chat tool must map to the *same* interface launcher
its REST route uses, with params derived from the ``details`` that
``application/chat/dispatchers/long_ops.py`` stored at propose time. These tests
patch the underlying launchers (no DB, no background work) and assert the mapping
+ the uniform ``operation_started`` envelope.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4, uuid7

import pytest

from src.application.chat.pending_actions import PendingAction
from src.domain.entities.playlist_link import SyncDirection
from src.domain.entities.shared import JsonDict
from src.domain.exceptions import ToolExecutionError
from src.interface.api.schemas.imports import OperationStartedResponse
from src.interface.api.schemas.workflows import WorkflowRunStartedResponse
from src.interface.api.services import chat_operations

_USER = "user-1"
_MOD = "src.interface.api.services.chat_operations"


def _action(
    tool_name: str, details: JsonDict, description: str = "Do the thing"
) -> PendingAction:
    return PendingAction(
        action_id=uuid4(),
        user_id=_USER,
        tool_name=tool_name,
        tool_input={},
        description=description,
        details={"operation": tool_name, **details},
        created_at=datetime.now(UTC),
    )


class _CapturingLaunch:
    """Stand-in for ``launch_sse_operation`` that runs the coro_factory once.

    Records the kwargs the launcher was called with and invokes the factory with
    a fake emitter, so the run_* call built from ``details`` actually fires and
    can be asserted.
    """

    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    async def __call__(
        self,
        *,
        user_id: str,
        operation_type: str,
        coro_factory: object,
        name_prefix: str = "import",
        request_params: JsonDict | None = None,
        initiated_by: str | None = None,
    ) -> OperationStartedResponse:
        self.kwargs = {
            "user_id": user_id,
            "operation_type": operation_type,
            "name_prefix": name_prefix,
            "request_params": request_params,
            "initiated_by": initiated_by,
        }
        emitter = SimpleNamespace(operation_id="op-emit", run_id=uuid7())
        await coro_factory(emitter)  # type: ignore[operator]
        return OperationStartedResponse(operation_id="op-1", run_id="run-1")


class TestRunWorkflow:
    async def test_maps_to_launch_workflow_run(self) -> None:
        workflow_id = uuid4()
        run_id = uuid7()
        fake = AsyncMock(
            return_value=WorkflowRunStartedResponse(operation_id="op-9", run_id=run_id)
        )
        action = _action("run_workflow", {"workflow_id": str(workflow_id)})

        with patch.object(chat_operations, "launch_workflow_run", fake):
            envelope = await chat_operations.launch_chat_operation(action, _USER)

        fake.assert_awaited_once_with(workflow_id, _USER)
        assert envelope == {
            "status": "operation_started",
            "operation_id": "op-9",
            "run_id": str(run_id),
            "description": "Do the thing",
        }


class TestSyncPlaylistLink:
    async def test_threads_token_and_direction(self) -> None:
        link_id = uuid4()
        fake = AsyncMock(
            return_value=OperationStartedResponse(operation_id="op-2", run_id="run-2")
        )
        action = _action(
            "sync_playlist_link",
            {
                "link_id": str(link_id),
                "direction_override": "push",
                "confirm_token": "tok-1",
            },
        )

        with patch.object(chat_operations, "launch_playlist_link_sync", fake):
            envelope = await chat_operations.launch_chat_operation(action, _USER)

        fake.assert_awaited_once_with(
            link_id=link_id,
            user_id=_USER,
            direction_override="push",
            confirm_token="tok-1",
            initiated_by="assistant",
        )
        assert envelope["operation_id"] == "op-2"
        assert envelope["run_id"] == "run-2"

    async def test_missing_overrides_pass_none(self) -> None:
        fake = AsyncMock(
            return_value=OperationStartedResponse(operation_id="op", run_id=None)
        )
        action = _action("sync_playlist_link", {"link_id": str(uuid4())})

        with patch.object(chat_operations, "launch_playlist_link_sync", fake):
            await chat_operations.launch_chat_operation(action, _USER)

        _, kwargs = fake.await_args
        assert kwargs["direction_override"] is None
        assert kwargs["confirm_token"] is None


class TestImportConnectorPlaylists:
    async def test_maps_details_to_use_case_call(self) -> None:
        capturing = _CapturingLaunch()
        run_uc = AsyncMock(return_value=Mock())
        action = _action(
            "import_connector_playlists",
            {
                "connector_name": "spotify",
                "identifiers": ["a", "b"],
                "sync_direction": "push",
                "force": True,
            },
        )

        with (
            patch.object(chat_operations, "launch_sse_operation", capturing),
            patch.object(
                chat_operations,
                "run_import_connector_playlists_as_canonical",
                run_uc,
            ),
            patch.object(chat_operations, "to_operation_result", Mock()),
        ):
            envelope = await chat_operations.launch_chat_operation(action, _USER)

        assert capturing.kwargs["operation_type"] == "import_connector_playlists"
        assert capturing.kwargs["initiated_by"] == "assistant"
        assert capturing.kwargs["request_params"] == {
            "connector_name": "spotify",
            "sync_direction": "push",
        }
        _, kwargs = run_uc.await_args
        assert kwargs["connector_name"] == "spotify"
        assert kwargs["connector_playlist_identifiers"] == ["a", "b"]
        assert kwargs["sync_direction"] is SyncDirection.PUSH
        assert kwargs["force"] is True
        assert envelope["status"] == "operation_started"


class TestApplyPlaylistAssignments:
    async def test_bulk_all_assignments_default_connector(self) -> None:
        capturing = _CapturingLaunch()
        run_uc = AsyncMock(return_value=Mock())
        action = _action(
            "apply_playlist_assignments",
            {"connector_name": None, "assignment_ids": None},
        )

        with (
            patch.object(chat_operations, "launch_sse_operation", capturing),
            patch.object(chat_operations, "run_apply_playlist_assignments", run_uc),
        ):
            await chat_operations.launch_chat_operation(action, _USER)

        assert capturing.kwargs["operation_type"] == "apply_assignments_bulk"
        assert capturing.kwargs["initiated_by"] == "assistant"
        assert capturing.kwargs["name_prefix"] == "apply_bulk"
        _, kwargs = run_uc.await_args
        assert kwargs["assignment_ids"] is None
        # Absent connector falls back to the spotify default, not None.
        assert kwargs["connector_name"] == "spotify"

    async def test_specific_assignment_ids_parsed_to_uuids(self) -> None:
        capturing = _CapturingLaunch()
        run_uc = AsyncMock(return_value=Mock())
        aid = uuid4()
        action = _action(
            "apply_playlist_assignments",
            {"connector_name": "spotify", "assignment_ids": [str(aid)]},
        )

        with (
            patch.object(chat_operations, "launch_sse_operation", capturing),
            patch.object(chat_operations, "run_apply_playlist_assignments", run_uc),
        ):
            await chat_operations.launch_chat_operation(action, _USER)

        assert capturing.kwargs["initiated_by"] == "assistant"
        _, kwargs = run_uc.await_args
        assert kwargs["assignment_ids"] == [aid]
        assert all(isinstance(x, UUID) for x in kwargs["assignment_ids"])


class TestImportData:
    async def test_lastfm_history_incremental_by_default(self) -> None:
        capturing = _CapturingLaunch()
        run_import = AsyncMock(return_value=Mock())
        action = _action(
            "import_data",
            {
                "source": "lastfm_history",
                "username": "ash",
                "limit": 500,
                "force": False,
            },
        )

        with (
            patch.object(chat_operations, "launch_sse_operation", capturing),
            patch.object(chat_operations, "run_import", run_import),
        ):
            await chat_operations.launch_chat_operation(action, _USER)

        assert capturing.kwargs["operation_type"] == "import_lastfm_history"
        assert capturing.kwargs["initiated_by"] == "assistant"
        _, kwargs = run_import.await_args
        assert kwargs["service"] == "lastfm"
        assert kwargs["mode"] == "incremental"
        assert kwargs["limit"] == 500
        assert kwargs["username"] == "ash"

    async def test_lastfm_history_force_uses_full_mode(self) -> None:
        capturing = _CapturingLaunch()
        run_import = AsyncMock(return_value=Mock())
        action = _action(
            "import_data",
            {"source": "lastfm_history", "username": "ash", "force": True},
        )

        with (
            patch.object(chat_operations, "launch_sse_operation", capturing),
            patch.object(chat_operations, "run_import", run_import),
        ):
            await chat_operations.launch_chat_operation(action, _USER)

        assert capturing.kwargs["initiated_by"] == "assistant"
        _, kwargs = run_import.await_args
        assert kwargs["mode"] == "full"

    async def test_spotify_likes_maps_to_likes_import(self) -> None:
        capturing = _CapturingLaunch()
        run_likes = AsyncMock(return_value=Mock())
        action = _action(
            "import_data", {"source": "spotify_likes", "limit": 100, "force": True}
        )

        with (
            patch.object(chat_operations, "launch_sse_operation", capturing),
            patch.object(chat_operations, "run_spotify_likes_import", run_likes),
        ):
            await chat_operations.launch_chat_operation(action, _USER)

        assert capturing.kwargs["operation_type"] == "import_spotify_likes"
        assert capturing.kwargs["initiated_by"] == "assistant"
        _, kwargs = run_likes.await_args
        assert kwargs["limit"] == 100
        assert kwargs["force"] is True

    async def test_unsupported_source_is_rejected(self) -> None:
        # spotify_history never passes the propose-time enum, so it can't reach a
        # stored action; the launcher's belt-and-suspenders guard still rejects
        # any source it doesn't recognise (misclassified spec safety net).
        action = _action("import_data", {"source": "spotify_history"})
        with pytest.raises(ToolExecutionError, match="Unknown import source"):
            await chat_operations.launch_chat_operation(action, _USER)


class TestUnknownTool:
    async def test_unknown_tool_name_raises(self) -> None:
        action = _action("not_a_tool", {})
        with pytest.raises(ToolExecutionError, match="not a launchable"):
            await chat_operations.launch_chat_operation(action, _USER)


class TestLauncherRegistrySync:
    def test_launchers_cover_exactly_the_operation_tools(self) -> None:
        # Every registry tool that launches a long op must have a launcher, and
        # no launcher may exist for a tool that doesn't — otherwise a confirmed
        # op either 500s (missing launcher) or references a dead mapping.
        from src.application.tools.registry import TOOLS

        expected = {spec.name for spec in TOOLS if spec.launches_operation}
        assert set(chat_operations._LAUNCHERS) == expected
