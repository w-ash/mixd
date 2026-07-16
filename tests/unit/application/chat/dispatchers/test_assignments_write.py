"""Unit tests for the ``manage_playlist_assignments`` two-phase write dispatcher.

``handle_manage_playlist_assignments`` proposes and
``exec_manage_playlist_assignments`` commits through the create /
create-and-apply / delete assignment use cases. The pending-action store is
swapped for a fresh instance per test so proposals don't leak, and
``execute_use_case`` is monkeypatched on the module under test so the commit
path never touches a database.
"""

from uuid import UUID, uuid4

import pytest

from src.application.chat.dispatchers import _common, assignments_write
from src.application.chat.pending_actions import PendingAction, PendingActionStore
from src.application.chat.protocols import ToolContext
from src.application.use_cases.apply_playlist_assignments import (
    ApplyPlaylistAssignmentsResult,
)
from src.application.use_cases.create_and_apply_assignment import (
    CreateAndApplyAssignmentResult,
)
from src.application.use_cases.create_playlist_assignment import (
    CreatePlaylistAssignmentResult,
)
from src.application.use_cases.delete_playlist_assignment import (
    DeletePlaylistAssignmentResult,
)
from src.domain.entities.playlist_assignment import PlaylistAssignment
from src.domain.exceptions import NotFoundError, ToolExecutionError

_CTX = ToolContext(user_id="default")


@pytest.fixture
def fresh_store(monkeypatch: pytest.MonkeyPatch) -> PendingActionStore:
    store = PendingActionStore()
    monkeypatch.setattr(_common, "pending_action_store", store)
    return store


def _fake_runner(result: object):
    async def _run(factory: object, user_id: str | None = None) -> object:
        return result

    return _run


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
        self, fresh_store: PendingActionStore
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

        action = fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.tool_name == "manage_playlist_assignments"

    async def test_bad_action_type_rejected(
        self, fresh_store: PendingActionStore
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
        self, fresh_store: PendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="assignment_id"):
            await assignments_write.handle_manage_playlist_assignments(
                {"operation": "delete"}, _CTX
            )


class TestExecManagePlaylistAssignments:
    def _action(self, details: dict[str, object]) -> PendingAction:
        store = PendingActionStore()
        return store.create(
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
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _fake_runner(
                CreatePlaylistAssignmentResult(
                    assignment=_make_assignment(cp_id), created=True
                )
            ),
        )
        action = self._action({
            "operation": "create",
            "connector_playlist_id": str(cp_id),
            "action_type": "set_preference",
            "action_value": "star",
        })

        out = await assignments_write.exec_manage_playlist_assignments(
            action, "default"
        )

        assert out["status"] == "confirmed"
        assert out["created"] is True
        assert out["assignment"]["action_type"] == "set_preference"
        assert out["assignment"]["action_value"] == "star"

    async def test_create_and_apply_commits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cp_id = uuid4()
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _fake_runner(
                CreateAndApplyAssignmentResult(
                    assignment=_make_assignment(cp_id),
                    apply_result=_apply_result(),
                )
            ),
        )
        action = self._action({
            "operation": "create_and_apply",
            "connector_playlist_id": str(cp_id),
            "action_type": "set_preference",
            "action_value": "star",
        })

        out = await assignments_write.exec_manage_playlist_assignments(
            action, "default"
        )

        assert out["status"] == "confirmed"
        assert out["applied"]["preferences_applied"] == 3
        assert out["applied"]["assignments_processed"] == 1

    async def test_delete_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assignment_id = uuid4()
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _fake_runner(DeletePlaylistAssignmentResult(deleted=True)),
        )
        action = self._action({
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
        action = self._action({
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
        action = self._action({"operation": "delete", "assignment_id": str(uuid4())})

        with pytest.raises(ToolExecutionError, match="no longer exists"):
            await assignments_write.exec_manage_playlist_assignments(action, "default")
