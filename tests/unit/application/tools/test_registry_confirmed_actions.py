"""Error handling in the confirmed-mutation execute path (registry).

``execute_confirmed_action`` runs a claimed pending action through its
registered executor (or the operation launcher). Like ``execute_tool``, it must
wrap that call in a blanket guard (K1): a non-``ToolExecutionError`` escaping an
executor would surface as a JSON-RPC protocol error over MCP or a 500 on the
chat path, with the confirm token already burned. These tests drive fake specs
so no live LLM or database is needed.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.application.chat.pending_actions import PendingAction
from src.application.tools import registry
from src.application.tools.registry import ToolSpec, execute_confirmed_action
from src.domain.entities.shared import JsonValue
from src.domain.exceptions import ToolExecutionError


async def _dummy_dispatch(tool_input: object, ctx: object) -> JsonValue:
    return {}  # write tools require a dispatch; never called on the confirm path


def _action(tool_name: str) -> PendingAction:
    return PendingAction(
        action_id=uuid4(),
        user_id="default",
        tool_name=tool_name,
        tool_input={},
        description="x",
        details={},
        created_at=datetime.now(UTC),
    )


def _register(monkeypatch: pytest.MonkeyPatch, spec: ToolSpec) -> None:
    monkeypatch.setitem(registry._SPECS_BY_NAME, spec.name, spec)


async def test_executor_keyerror_becomes_tool_execution_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raises(action: PendingAction, user_id: str) -> JsonValue:
        raise KeyError("definition")  # e.g. a missing action.details key

    _register(
        monkeypatch,
        ToolSpec(
            name="boom",
            description="d",
            input_schema={"type": "object"},
            dispatch=_dummy_dispatch,
            kind="write",
            executor=_raises,
        ),
    )
    with pytest.raises(ToolExecutionError) as exc:
        await execute_confirmed_action(_action("boom"), "default")
    assert "boom failed" in str(exc.value)
    assert isinstance(exc.value.__cause__, KeyError)


async def test_executor_tool_execution_error_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raises(action: PendingAction, user_id: str) -> JsonValue:
        raise ToolExecutionError("actionable message for the model")

    _register(
        monkeypatch,
        ToolSpec(
            name="boom2",
            description="d",
            input_schema={"type": "object"},
            dispatch=_dummy_dispatch,
            kind="write",
            executor=_raises,
        ),
    )
    with pytest.raises(ToolExecutionError, match="actionable message for the model"):
        await execute_confirmed_action(_action("boom2"), "default")
