"""Unit tests for chat tool dispatchers and the save_workflow executor.

DB-backed paths (get/list/save-update, the confirmed executor) monkeypatch
``execute_use_case`` on the module under test; the pending-action store is
swapped for a fresh instance per test so proposals don't leak across tests.
"""

import json
from uuid import uuid4

import pytest

from src.application.chat import confirmed_actions, tool_executor
from src.application.chat.dispatchers import _common
from src.application.chat.protocols import ToolContext
from src.application.tools import registry
from src.application.use_cases.workflow_crud import (
    CreateWorkflowResult,
    GetWorkflowResult,
    ListWorkflowsResult,
    UpdateWorkflowResult,
)
from src.domain.exceptions import (
    ActionExpiredError,
    NotFoundError,
    ToolExecutionError,
)
from tests.fixtures import (
    InMemoryPendingActionStore,
    make_workflow,
    make_workflow_def,
)

_CTX = ToolContext(user_id="default")

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

_INVALID_DEF = {
    "id": "broken",
    "name": "Broken",
    "tasks": [{"id": "a", "type": "source.bogus", "config": {}}],
}


@pytest.fixture
def fresh_store(monkeypatch: pytest.MonkeyPatch) -> InMemoryPendingActionStore:
    store = InMemoryPendingActionStore()
    # save_workflow proposes through dispatchers._common.propose_action (C5),
    # so the fresh store must replace the one that helper closes over.
    monkeypatch.setattr(_common, "pending_action_store", store)
    return store


def _fake_use_case_runner(result: object):
    async def _run(factory, user_id: str | None = None):  # matches runner signature
        return result

    return _run


async def test_describe_node_lists_all_node_types() -> None:
    result = await registry.execute_tool("describe_node", {}, _CTX)

    assert isinstance(result, dict)
    nodes = result["nodes"]
    assert isinstance(nodes, list)
    assert nodes
    types = {n["type"] for n in nodes}
    assert "source.playlist" in types
    assert {"type", "category", "description"} <= set(nodes[0])


async def test_describe_node_returns_config_fields_for_a_type() -> None:
    result = await registry.execute_tool(
        "describe_node", {"node_type": "source.playlist"}, _CTX
    )

    assert isinstance(result, dict)
    assert result["type"] == "source.playlist"
    assert result["category"] == "source"
    field_keys = {f["key"] for f in result["config_fields"]}
    assert "playlist_id" in field_keys


async def test_describe_node_unknown_type_raises_actionable_error() -> None:
    with pytest.raises(ToolExecutionError) as exc:
        await registry.execute_tool("describe_node", {"node_type": "bogus.node"}, _CTX)

    # The error names valid types so the model can self-correct in-turn.
    assert "source.playlist" in str(exc.value)


async def test_execute_tool_rejects_unknown_tool() -> None:
    with pytest.raises(ToolExecutionError, match="Unknown tool"):
        await registry.execute_tool("no_such_tool", {}, _CTX)


class TestGenerateWorkflowDef:
    async def test_valid_def_echoes_normalized(self) -> None:
        result = await tool_executor.handle_generate_workflow_def(
            {"workflow_def": _VALID_DEF}, _CTX
        )

        assert result["status"] == "valid"
        assert result["task_count"] == 3
        assert result["warnings"] == []
        # Normalized echo: upstream materialized on every task.
        assert result["workflow_def"]["tasks"][0]["upstream"] == []

    async def test_invalid_def_raises_structured_feedback(self) -> None:
        with pytest.raises(ToolExecutionError) as exc:
            await tool_executor.handle_generate_workflow_def(
                {"workflow_def": _INVALID_DEF}, _CTX
            )

        # The failure list round-trips as JSON the model can act on.
        payload = str(exc.value).split("definition: ", 1)[1]
        errors = json.loads(payload)
        assert errors[0]["task_id"] == "a"
        assert errors[0]["field"] == "type"
        assert "message" in errors[0]

    async def test_non_object_input_rejected(self) -> None:
        with pytest.raises(ToolExecutionError, match="JSON object"):
            await tool_executor.handle_generate_workflow_def(
                {"workflow_def": "not a dict"}, _CTX
            )


class TestValidateWorkflowDef:
    async def test_findings_are_a_success_result(self) -> None:
        result = await tool_executor.handle_validate_workflow_def(
            {"workflow_def": _INVALID_DEF}, _CTX
        )

        assert result["valid"] is False
        assert result["errors"][0]["task_id"] == "a"

    async def test_valid_def_reports_valid(self) -> None:
        result = await tool_executor.handle_validate_workflow_def(
            {"workflow_def": _VALID_DEF}, _CTX
        )

        assert result == {"valid": True, "errors": [], "warnings": []}


class TestSaveWorkflowPropose:
    async def test_create_proposes_pending_confirmation(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        result = await tool_executor.handle_save_workflow(
            {"workflow_def": _VALID_DEF}, _CTX
        )

        assert result["status"] == "pending_confirmation"
        assert result["details"]["mode"] == "create"
        assert result["details"]["task_count"] == 3
        assert "Create workflow 'Chill Weekend'" in result["description"]
        # The proposal is claimable by its owner — i.e. actually stored.
        from uuid import UUID

        action = await fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.tool_name == "save_workflow"
        assert action.details["definition"]["name"] == "Chill Weekend"

    async def test_update_carries_change_summary(
        self, fresh_store: InMemoryPendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        existing = make_workflow(definition=make_workflow_def(name="Old Name"))
        monkeypatch.setattr(
            tool_executor,
            "execute_use_case",
            _fake_use_case_runner(GetWorkflowResult(workflow=existing)),
        )

        result = await tool_executor.handle_save_workflow(
            {"workflow_def": _VALID_DEF, "workflow_id": str(existing.id)}, _CTX
        )

        assert result["details"]["mode"] == "update"
        assert result["details"]["workflow_id"] == str(existing.id)
        assert "Update workflow 'Old Name'" in result["description"]
        assert result["details"]["changes"]  # non-empty human-readable summary

    async def test_invalid_def_stores_nothing(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError):
            await tool_executor.handle_save_workflow(
                {"workflow_def": _INVALID_DEF}, _CTX
            )

        # No claimable action exists — the store never saw a create.
        with pytest.raises(ActionExpiredError):
            await fresh_store.claim(uuid4(), "default")

    async def test_bad_workflow_id_rejected(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="workflow_id must be a UUID"):
            await tool_executor.handle_save_workflow(
                {"workflow_def": _VALID_DEF, "workflow_id": "not-a-uuid"}, _CTX
            )


class TestListAndGetWorkflows:
    async def test_list_projects_compact_shape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wf = make_workflow()
        monkeypatch.setattr(
            tool_executor,
            "execute_use_case",
            _fake_use_case_runner(ListWorkflowsResult(workflows=[wf], total_count=1)),
        )

        result = await tool_executor.handle_list_user_workflows({}, _CTX)

        assert result["total_count"] == 1
        entry = result["workflows"][0]
        assert entry["workflow_id"] == str(wf.id)
        assert entry["task_count"] == len(wf.definition.tasks)
        assert "definition" not in entry  # compact: no full defs in listings

    async def test_get_returns_full_definition(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wf = make_workflow()
        monkeypatch.setattr(
            tool_executor,
            "execute_use_case",
            _fake_use_case_runner(GetWorkflowResult(workflow=wf)),
        )

        result = await tool_executor.handle_get_workflow(
            {"workflow_id": str(wf.id)}, _CTX
        )

        assert result["workflow_id"] == str(wf.id)
        assert result["definition"]["tasks"][0]["type"] == "source.liked_tracks"

    async def test_get_unknown_id_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory, user_id: str | None = None):
            raise NotFoundError("nope")

        monkeypatch.setattr(tool_executor, "execute_use_case", _raise)

        with pytest.raises(ToolExecutionError, match="list_user_workflows"):
            await tool_executor.handle_get_workflow({"workflow_id": str(uuid4())}, _CTX)


class TestExecSaveWorkflow:
    async def _propose(
        self, store: InMemoryPendingActionStore, details_extra: dict
    ) -> object:
        return await store.create(
            user_id="default",
            tool_name="save_workflow",
            tool_input={},
            description="Save it",
            details={"definition": dict(_VALID_DEF), **details_extra},
        )

    async def test_create_commits_through_use_case(
        self, fresh_store: InMemoryPendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = make_workflow()
        monkeypatch.setattr(
            confirmed_actions,
            "execute_use_case",
            _fake_use_case_runner(CreateWorkflowResult(workflow=saved)),
        )
        action = await self._propose(fresh_store, {"mode": "create"})

        result = await confirmed_actions.exec_save_workflow(action, "default")

        assert result["status"] == "confirmed"
        assert result["workflow_id"] == str(saved.id)
        assert result["definition_version"] == saved.definition_version

    async def test_update_commits_through_use_case(
        self, fresh_store: InMemoryPendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = make_workflow(definition_version=2)
        monkeypatch.setattr(
            confirmed_actions,
            "execute_use_case",
            _fake_use_case_runner(UpdateWorkflowResult(workflow=saved)),
        )
        action = await self._propose(
            fresh_store, {"mode": "update", "workflow_id": str(saved.id)}
        )

        result = await confirmed_actions.exec_save_workflow(action, "default")

        assert result["definition_version"] == 2

    async def test_deleted_workflow_at_confirm_time_is_actionable(
        self, fresh_store: InMemoryPendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory, user_id: str | None = None):
            raise NotFoundError("gone")

        monkeypatch.setattr(confirmed_actions, "execute_use_case", _raise)
        action = await self._propose(
            fresh_store, {"mode": "update", "workflow_id": str(uuid4())}
        )

        with pytest.raises(ToolExecutionError, match="no longer exists"):
            await confirmed_actions.exec_save_workflow(action, "default")

    async def test_validation_failure_at_confirm_time_is_actionable(
        self, fresh_store: InMemoryPendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory, user_id: str | None = None):
            raise ValueError("Task 'a' has unknown node type")

        monkeypatch.setattr(confirmed_actions, "execute_use_case", _raise)
        action = await self._propose(fresh_store, {"mode": "create"})

        with pytest.raises(ToolExecutionError, match="save time"):
            await confirmed_actions.exec_save_workflow(action, "default")
