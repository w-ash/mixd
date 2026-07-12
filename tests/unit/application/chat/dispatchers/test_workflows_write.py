"""Unit tests for the ``manage_workflow`` / ``manage_schedule`` write dispatchers.

Covers both tools across both phases: ``handle_*`` proposes (stores a pending
action, marking the destructive operations with severity/warning) and ``exec_*``
commits through the underlying use case. The pending-action store is swapped for
a fresh instance per test (monkeypatching ``_common.pending_action_store``, which
``propose_action`` reads), and ``execute_use_case`` is monkeypatched on the
module under test so the commit path never touches a database.
"""

from uuid import UUID, uuid4

import pytest

from src.application.chat.dispatchers import _common, workflows_write
from src.application.chat.pending_actions import PendingAction, PendingActionStore
from src.application.chat.protocols import ToolContext
from src.application.use_cases.schedules import (
    DeleteScheduleResult,
    ToggleScheduleResult,
    UpsertScheduleResult,
)
from src.application.use_cases.workflow_crud import (
    DeleteWorkflowResult,
    DuplicateWorkflowResult,
    InstantiateWorkflowResult,
)
from src.application.use_cases.workflow_versions import RevertWorkflowVersionResult
from src.domain.entities.schedule import Schedule
from src.domain.exceptions import NotFoundError, ToolExecutionError
from tests.fixtures import make_workflow, make_workflow_def

_CTX = ToolContext(user_id="default")

# The valid definition the model would hand to instantiate (mirrors the
# save_workflow tests' _VALID_DEF).
_VALID_DEF = {
    "id": "chill-weekend",
    "name": "Chill Weekend",
    "tasks": [
        {"id": "src", "type": "source.liked_tracks", "config": {"limit": 100}},
        {
            "id": "flt",
            "type": "filter.by_play_history",
            "config": {"not_played_in_days": 180},
            "upstream": ["src"],
        },
        {
            "id": "dest",
            "type": "destination.create_playlist",
            "config": {"name": "Chill Weekend"},
            "upstream": ["flt"],
        },
    ],
}


@pytest.fixture
def fresh_store(monkeypatch: pytest.MonkeyPatch) -> PendingActionStore:
    store = PendingActionStore()
    monkeypatch.setattr(_common, "pending_action_store", store)
    return store


def _fake_use_case_runner(result: object):
    async def _run(factory, user_id: str | None = None):  # matches runner signature
        return result

    return _run


def _action(tool_name: str, details: dict[str, object]) -> PendingAction:
    return PendingActionStore().create(
        user_id="default",
        tool_name=tool_name,
        tool_input={},
        description="Manage",
        details=details,
    )


def _make_schedule(**kwargs: object) -> Schedule:
    kwargs.setdefault("workflow_id", uuid4())
    return Schedule(user_id="default", hour=9, minute=30, **kwargs)


# ---------------------------------------------------------------------------
# manage_workflow — propose
# ---------------------------------------------------------------------------


class TestManageWorkflowPropose:
    async def test_instantiate_proposes_pending_confirmation(
        self, fresh_store: PendingActionStore
    ) -> None:
        result = await workflows_write.handle_manage_workflow(
            {"operation": "instantiate", "workflow_def": _VALID_DEF}, _CTX
        )

        assert result["status"] == "pending_confirmation"
        details = result["details"]
        assert details["operation"] == "instantiate"
        assert details["changes"]
        # Round-trips the normalized definition for the executor to re-parse.
        assert details["workflow_def"]["name"] == "Chill Weekend"
        assert "severity" not in details

        action = fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.tool_name == "manage_workflow"

    async def test_duplicate_carries_workflow_id(
        self, fresh_store: PendingActionStore
    ) -> None:
        workflow_id = uuid4()
        result = await workflows_write.handle_manage_workflow(
            {"operation": "duplicate", "workflow_id": str(workflow_id)}, _CTX
        )

        assert result["details"]["operation"] == "duplicate"
        assert result["details"]["workflow_id"] == str(workflow_id)
        assert "severity" not in result["details"]

    async def test_delete_is_destructive(self, fresh_store: PendingActionStore) -> None:
        result = await workflows_write.handle_manage_workflow(
            {"operation": "delete", "workflow_id": str(uuid4())}, _CTX
        )

        details = result["details"]
        assert details["operation"] == "delete"
        assert details["severity"] == "destructive"
        assert "version history" in details["warning"]

    async def test_revert_version_carries_version(
        self, fresh_store: PendingActionStore
    ) -> None:
        workflow_id = uuid4()
        result = await workflows_write.handle_manage_workflow(
            {
                "operation": "revert_version",
                "workflow_id": str(workflow_id),
                "version": 3,
            },
            _CTX,
        )

        assert result["details"]["operation"] == "revert_version"
        assert result["details"]["version"] == 3

    async def test_instantiate_invalid_def_rejected(
        self, fresh_store: PendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="workflow_def"):
            await workflows_write.handle_manage_workflow(
                {"operation": "instantiate", "workflow_def": "not-an-object"}, _CTX
            )

    async def test_revert_missing_version_rejected(
        self, fresh_store: PendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="version"):
            await workflows_write.handle_manage_workflow(
                {"operation": "revert_version", "workflow_id": str(uuid4())}, _CTX
            )

    async def test_bad_workflow_id_rejected(
        self, fresh_store: PendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="workflow_id"):
            await workflows_write.handle_manage_workflow(
                {"operation": "delete", "workflow_id": "not-a-uuid"}, _CTX
            )

    async def test_unknown_operation_rejected(
        self, fresh_store: PendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="operation"):
            await workflows_write.handle_manage_workflow({"operation": "bogus"}, _CTX)


# ---------------------------------------------------------------------------
# manage_workflow — exec
# ---------------------------------------------------------------------------


class TestExecManageWorkflow:
    async def test_instantiate_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        workflow = make_workflow(definition=make_workflow_def(name="Chill Weekend"))
        monkeypatch.setattr(
            workflows_write,
            "execute_use_case",
            _fake_use_case_runner(InstantiateWorkflowResult(workflow=workflow)),
        )
        action = _action(
            "manage_workflow",
            {"operation": "instantiate", "workflow_def": _VALID_DEF},
        )

        result = await workflows_write.exec_manage_workflow(action, "default")

        assert result["status"] == "confirmed"
        assert result["operation"] == "instantiate"
        assert result["workflow_id"] == str(workflow.id)

    async def test_duplicate_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        workflow = make_workflow()
        monkeypatch.setattr(
            workflows_write,
            "execute_use_case",
            _fake_use_case_runner(DuplicateWorkflowResult(workflow=workflow)),
        )
        action = _action(
            "manage_workflow",
            {"operation": "duplicate", "workflow_id": str(uuid4())},
        )

        result = await workflows_write.exec_manage_workflow(action, "default")

        assert result["operation"] == "duplicate"
        assert result["workflow_id"] == str(workflow.id)

    async def test_delete_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        workflow_id = uuid4()
        monkeypatch.setattr(
            workflows_write,
            "execute_use_case",
            _fake_use_case_runner(DeleteWorkflowResult(workflow_id=workflow_id)),
        )
        action = _action(
            "manage_workflow",
            {"operation": "delete", "workflow_id": str(workflow_id)},
        )

        result = await workflows_write.exec_manage_workflow(action, "default")

        assert result["operation"] == "delete"
        assert result["workflow_id"] == str(workflow_id)

    async def test_revert_version_commits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workflow = make_workflow()
        monkeypatch.setattr(
            workflows_write,
            "execute_use_case",
            _fake_use_case_runner(RevertWorkflowVersionResult(workflow=workflow)),
        )
        action = _action(
            "manage_workflow",
            {
                "operation": "revert_version",
                "workflow_id": str(workflow.id),
                "version": 2,
            },
        )

        result = await workflows_write.exec_manage_workflow(action, "default")

        assert result["operation"] == "revert_version"
        assert result["workflow_id"] == str(workflow.id)

    async def test_not_found_at_confirm_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory, user_id: str | None = None):
            raise NotFoundError("gone")

        monkeypatch.setattr(workflows_write, "execute_use_case", _raise)
        action = _action(
            "manage_workflow",
            {"operation": "delete", "workflow_id": str(uuid4())},
        )

        with pytest.raises(ToolExecutionError, match="no longer exists"):
            await workflows_write.exec_manage_workflow(action, "default")


# ---------------------------------------------------------------------------
# manage_schedule — propose
# ---------------------------------------------------------------------------


class TestManageSchedulePropose:
    async def test_upsert_proposes_pending_confirmation(
        self, fresh_store: PendingActionStore
    ) -> None:
        workflow_id = uuid4()
        result = await workflows_write.handle_manage_schedule(
            {
                "operation": "upsert",
                "workflow_id": str(workflow_id),
                "hour": 9,
                "minute": 30,
                "day_of_week": 1,
                "timezone": "America/Los_Angeles",
            },
            _CTX,
        )

        assert result["status"] == "pending_confirmation"
        details = result["details"]
        assert details["operation"] == "upsert"
        assert details["workflow_id"] == str(workflow_id)
        assert details["sync_target"] is None
        assert details["hour"] == 9
        assert details["minute"] == 30
        assert details["day_of_week"] == 1
        assert details["timezone"] == "America/Los_Angeles"
        assert "severity" not in details

        action = fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.tool_name == "manage_schedule"

    async def test_upsert_defaults_daily_utc(
        self, fresh_store: PendingActionStore
    ) -> None:
        result = await workflows_write.handle_manage_schedule(
            {"operation": "upsert", "sync_target": "lastfm:plays"}, _CTX
        )

        details = result["details"]
        assert details["sync_target"] == "lastfm:plays"
        assert details["workflow_id"] is None
        assert details["hour"] == 0
        assert details["day_of_week"] is None
        assert details["timezone"] == "UTC"

    async def test_toggle_carries_enabled(
        self, fresh_store: PendingActionStore
    ) -> None:
        result = await workflows_write.handle_manage_schedule(
            {"operation": "toggle", "workflow_id": str(uuid4()), "enabled": False},
            _CTX,
        )

        assert result["details"]["operation"] == "toggle"
        assert result["details"]["enabled"] is False
        assert "Disable" in result["description"]

    async def test_delete_is_destructive(self, fresh_store: PendingActionStore) -> None:
        result = await workflows_write.handle_manage_schedule(
            {"operation": "delete", "sync_target": "lastfm:plays"}, _CTX
        )

        details = result["details"]
        assert details["operation"] == "delete"
        assert details["severity"] == "destructive"
        assert details["warning"]

    async def test_neither_target_rejected(
        self, fresh_store: PendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="exactly one"):
            await workflows_write.handle_manage_schedule({"operation": "delete"}, _CTX)

    async def test_both_targets_rejected(self, fresh_store: PendingActionStore) -> None:
        with pytest.raises(ToolExecutionError, match="exactly one"):
            await workflows_write.handle_manage_schedule(
                {
                    "operation": "delete",
                    "workflow_id": str(uuid4()),
                    "sync_target": "lastfm:plays",
                },
                _CTX,
            )

    async def test_toggle_missing_enabled_rejected(
        self, fresh_store: PendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="enabled"):
            await workflows_write.handle_manage_schedule(
                {"operation": "toggle", "workflow_id": str(uuid4())}, _CTX
            )


# ---------------------------------------------------------------------------
# manage_schedule — exec
# ---------------------------------------------------------------------------


class TestExecManageSchedule:
    async def test_upsert_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        schedule = _make_schedule()
        monkeypatch.setattr(
            workflows_write,
            "execute_use_case",
            _fake_use_case_runner(
                UpsertScheduleResult(schedule=schedule, created=True)
            ),
        )
        action = _action(
            "manage_schedule",
            {
                "operation": "upsert",
                "workflow_id": str(schedule.workflow_id),
                "sync_target": None,
                "hour": 9,
                "minute": 30,
                "day_of_week": None,
                "timezone": "UTC",
            },
        )

        result = await workflows_write.exec_manage_schedule(action, "default")

        assert result["status"] == "confirmed"
        assert result["operation"] == "upsert"
        assert result["created"] is True
        assert result["schedule"]["schedule_id"] == str(schedule.id)

    async def test_toggle_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        schedule = _make_schedule(status="disabled")
        monkeypatch.setattr(
            workflows_write,
            "execute_use_case",
            _fake_use_case_runner(ToggleScheduleResult(schedule=schedule)),
        )
        action = _action(
            "manage_schedule",
            {
                "operation": "toggle",
                "workflow_id": str(schedule.workflow_id),
                "sync_target": None,
                "enabled": False,
            },
        )

        result = await workflows_write.exec_manage_schedule(action, "default")

        assert result["operation"] == "toggle"
        assert result["schedule"]["status"] == "disabled"

    async def test_delete_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        schedule_id = uuid4()
        monkeypatch.setattr(
            workflows_write,
            "execute_use_case",
            _fake_use_case_runner(DeleteScheduleResult(schedule_id=schedule_id)),
        )
        action = _action(
            "manage_schedule",
            {
                "operation": "delete",
                "workflow_id": None,
                "sync_target": "lastfm:plays",
            },
        )

        result = await workflows_write.exec_manage_schedule(action, "default")

        assert result["operation"] == "delete"
        assert result["schedule_id"] == str(schedule_id)

    async def test_value_error_at_confirm_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory, user_id: str | None = None):
            raise ValueError("unknown timezone")

        monkeypatch.setattr(workflows_write, "execute_use_case", _raise)
        action = _action(
            "manage_schedule",
            {
                "operation": "upsert",
                "workflow_id": str(uuid4()),
                "sync_target": None,
                "hour": 0,
                "minute": 0,
                "day_of_week": None,
                "timezone": "Bad/Zone",
            },
        )

        with pytest.raises(ToolExecutionError, match="validation at confirm time"):
            await workflows_write.exec_manage_schedule(action, "default")


def test_specs_expose_both_write_tools() -> None:
    names = {spec["name"] for spec in workflows_write.SPECS}
    assert names == {"manage_workflow", "manage_schedule"}
    for spec in workflows_write.SPECS:
        assert spec["kind"] == "write"
        assert callable(spec["executor"])
