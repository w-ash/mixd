"""Unit tests for chat tool dispatchers (v0.9.0: describe_node)."""

import pytest

from src.application.chat.protocols import ToolContext
from src.application.tools import registry
from src.domain.exceptions import ToolExecutionError

_CTX = ToolContext(user_id="default")


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
