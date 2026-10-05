"""Unit tests for the ``manage_playlist_assignments`` two-phase write dispatcher.

``handle_manage_playlist_assignments`` proposes and
``exec_manage_playlist_assignments`` commits through the create /
create-and-apply / delete assignment use cases. The pending-action store is
swapped for a fresh instance per test so proposals don't leak, and
``execute_use_case`` is monkeypatched on the module under test so the commit
path never touches a database; the success paths run the real factory into a
patched use-case ``execute`` to see the Command the dispatcher built.
"""

from uuid import UUID, uuid4

import pytest

from src.application.chat.dispatchers import _common, assignments_write
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.use_cases.apply_playlist_assignments import (
    ApplyPlaylistAssignmentsResult,
)
from src.application.use_cases.create_and_apply_assignment import (
    CreateAndApplyAssignmentCommand,
    CreateAndApplyAssignmentResult,
    CreateAndApplyAssignmentUseCase,
)
from src.application.use_cases.create_playlist_assignment import (
    CreatePlaylistAssignmentCommand,
    CreatePlaylistAssignmentResult,
    CreatePlaylistAssignmentUseCase,
)
from src.application.use_cases.delete_playlist_assignment import (
    DeletePlaylistAssignmentResult,
)
from src.domain.entities.playlist_assignment import PlaylistAssignment
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


def _capture(
    monkeypatch: pytest.MonkeyPatch, use_case: type, result: object
) -> dict[str, object]:
    """Run the dispatcher's real factory into ``use_case``; record its Command.

    Also records the ``user_id`` the runner received, so a commit that drops the
    caller's tenant fails.
    """
    seen: dict[str, object] = {}

    async def _execute(self: object, command: object, uow: object) -> object:
        seen["command"] = command
        return result

    async def _run(factory, user_id: str | None = None):  # runner signature
        seen["user_id"] = user_id
        return await factory(object())

    monkeypatch.setattr(use_case, "execute", _execute)
    monkeypatch.setattr(_common, "execute_use_case", _run)
    return seen


def _make_assignment(cp_id: UUID) -> PlaylistAssignment:
    return PlaylistAssignment(
        user_id="default",
        connector_playlist_id=cp_id,
        action_type="set_preference",
        action_value="star",
    )


def _apply_result() -> ApplyPlaylistAssignmentsResult:
    return ApplyPlaylistAssignmentsResult(
        preferences_applied=3,
        preferences_cleared=0,
        tags_applied=0,
        tags_cleared=0,
        conflicts_logged=0,
        assignments_processed=1,
    )


class TestManagePlaylistAssignmentsPropose:
    async def test_create_proposes_pending_confirmation(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        cp_id = uuid4()
        result = await assignments_write.handle_manage_playlist_assignments(
            {
                "operation": "create",
                "connector_playlist_id": str(cp_id),
                "action_type": "set_preference",
                "action_value": "star",
            },
            _CTX,
        )

        assert result["status"] == "pending_confirmation"
        details = result["details"]
        assert details["operation"] == "create"
        assert details["connector_playlist_id"] == str(cp_id)
        assert details["action_type"] == "set_preference"
        assert details["action_value"] == "star"
        assert details["changes"]

        action = await fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.tool_name == "manage_playlist_assignments"

    async def test_bad_action_type_rejected(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="action_type"):
            await assignments_write.handle_manage_playlist_assignments(
                {
                    "operation": "create",
                    "connector_playlist_id": str(uuid4()),
                    "action_type": "delete_everything",
                    "action_value": "x",
                },
                _CTX,
            )

    async def test_delete_missing_assignment_id_rejected(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="assignment_id"):
            await assignments_write.handle_manage_playlist_assignments(
                {"operation": "delete"}, _CTX
            )


class TestExecManagePlaylistAssignments:
    async def _action(self, details: dict[str, object]) -> PendingAction:
        store = InMemoryPendingActionStore()
        return await store.create(
            user_id="default",
            tool_name="manage_playlist_assignments",
            tool_input={},
            description="Assignment op",
            details=details,
        )

    async def test_create_commits_and_projects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cp_id = uuid4()
        seen = _capture(
            monkeypatch,
            CreatePlaylistAssignmentUseCase,
            CreatePlaylistAssignmentResult(
                assignment=_make_assignment(cp_id), created=True
            ),
        )
        action = await self._action({
            "operation": "create",
            "connector_playlist_id": str(cp_id),
            "action_type": "add_tag",
            "action_value": "mood:chill",
        })

        out = await assignments_write.exec_manage_playlist_assignments(action, "user-7")

        command = seen["command"]
        assert isinstance(command, CreatePlaylistAssignmentCommand)
        assert command.connector_playlist_id == cp_id
        assert command.action_type == "add_tag"
        assert command.raw_action_value == "mood:chill"
        assert command.user_id == "user-7"
        assert seen["user_id"] == "user-7"
        assert out["status"] == "confirmed"
        assert out["created"] is True
        # The projection echoes the assignment the use case returned.
        assert out["assignment"]["action_type"] == "set_preference"
        assert out["assignment"]["action_value"] == "star"

    async def test_create_and_apply_commits_through_the_apply_use_case(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cp_id = uuid4()
        seen = _capture(
            monkeypatch,
            CreateAndApplyAssignmentUseCase,
            CreateAndApplyAssignmentResult(
                assignment=_make_assignment(cp_id),
                apply_result=_apply_result(),
            ),
        )
        action = await self._action({
            "operation": "create_and_apply",
            "connector_playlist_id": str(cp_id),
            "action_type": "set_preference",
            "action_value": "star",
        })

        out = await assignments_write.exec_manage_playlist_assignments(action, "user-7")

        command = seen["command"]
        assert isinstance(command, CreateAndApplyAssignmentCommand)
        assert command.connector_playlist_id == cp_id
        assert command.action_type == "set_preference"
        assert command.raw_action_value == "star"
        assert command.user_id == "user-7"
        assert out["status"] == "confirmed"
        assert out["applied"] == {
            "preferences_applied": 3,
            "tags_applied": 0,
            "assignments_processed": 1,
        }

    async def test_delete_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assignment_id = uuid4()
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _fake_runner(DeletePlaylistAssignmentResult(deleted=True)),
        )
        action = await self._action({
            "operation": "delete",
            "assignment_id": str(assignment_id),
        })

        out = await assignments_write.exec_manage_playlist_assignments(
            action, "default"
        )

        assert out["status"] == "confirmed"
        assert out["deleted"] is True
        assert out["assignment_id"] == str(assignment_id)

    async def test_invalid_value_at_confirm_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory: object, user_id: str | None = None) -> object:
            raise ValueError("action_value for set_preference must be one of ...")

        monkeypatch.setattr(_common, "execute_use_case", _raise)
        action = await self._action({
            "operation": "create",
            "connector_playlist_id": str(uuid4()),
            "action_type": "set_preference",
            "action_value": "bogus",
        })

        with pytest.raises(ToolExecutionError, match="failed validation"):
            await assignments_write.exec_manage_playlist_assignments(action, "default")

    async def test_delete_gone_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory: object, user_id: str | None = None) -> object:
            raise NotFoundError("gone")

        monkeypatch.setattr(_common, "execute_use_case", _raise)
        action = await self._action({
            "operation": "delete",
            "assignment_id": str(uuid4()),
        })

        with pytest.raises(ToolExecutionError, match="no longer exists"):
            await assignments_write.exec_manage_playlist_assignments(action, "default")

    async def test_malformed_assignment_id_at_commit_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory: object, user_id: str | None = None) -> object:
            raise AssertionError("not reached")

        monkeypatch.setattr(_common, "execute_use_case", _raise)
        action = await self._action({
            "operation": "delete",
            "assignment_id": "not-a-uuid",
        })

        with pytest.raises(ToolExecutionError, match="must be a UUID string"):
            await assignments_write.exec_manage_playlist_assignments(action, "default")
