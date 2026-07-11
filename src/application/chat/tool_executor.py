"""Tool dispatchers for the chat assistant.

One ``handle_*`` coroutine per read/query tool, plus the ``propose_*`` helpers
for two-phase mutations (added as write tools land). Each dispatcher is a thin
adapter: read tools project existing query paths into compact summaries; the
registry (``src.application.tools.registry``) binds each to its ``ToolSpec``.
No business logic lives here — where a use case is missing or awkward, it is
fixed at the source.

v0.9.0 ships one tool — ``describe_node`` — so the agentic loop has a real
capability to dispatch before the workflow-generation tools land in v0.9.0
Phase 3. It reads the static node catalog, so it needs neither a UoW nor a
user; later dispatchers thread ``ctx.user_id`` through ``execute_use_case``.
"""

from collections.abc import Mapping

from src.application.chat.protocols import ToolContext
from src.application.workflows.nodes.config_fields import (
    ConfigFieldDef,
    get_node_config_fields,
)
from src.application.workflows.nodes.registry import list_nodes
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.exceptions import ToolExecutionError

# --- describe_node ---------------------------------------------------------

DESCRIBE_NODE_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "node_type": {
            "type": "string",
            "description": (
                "A node type id such as 'source.playlist', 'filter.by_metric', "
                "or 'destination.create_playlist'. Omit to list every node type."
            ),
        },
    },
    "additionalProperties": False,
}


def _field_to_dict(f: ConfigFieldDef) -> JsonDict:
    """Project a config-field definition into a compact model-facing dict."""
    out: JsonDict = {
        "key": f.key,
        "label": f.label,
        "type": f.field_type,
        "required": f.required,
    }
    if f.description is not None:
        out["description"] = f.description
    if f.default is not None:
        out["default"] = f.default
    if f.min is not None:
        out["min"] = f.min
    if f.max is not None:
        out["max"] = f.max
    if f.options:
        out["options"] = [{"value": o.value, "label": o.label} for o in f.options]
    return out


async def handle_describe_node(
    tool_input: Mapping[str, JsonValue],
    ctx: ToolContext,  # shared dispatcher signature; catalog data is user-agnostic
) -> JsonValue:
    """Describe one node type's config fields, or list every node type.

    Unknown node types raise ``ToolExecutionError`` naming the valid types so
    the model can self-correct in the same turn.
    """
    nodes = list_nodes()
    config_fields = get_node_config_fields()

    raw = tool_input.get("node_type")
    node_type = str(raw).strip() if raw is not None else None

    if node_type:
        meta = nodes.get(node_type)
        if meta is None:
            valid = ", ".join(sorted(nodes))
            raise ToolExecutionError(
                f"Unknown node type {node_type!r}. Valid types: {valid}"
            )
        fields = config_fields.get(node_type, ())
        detail: JsonDict = {
            "type": node_type,
            "category": meta["category"],
            "description": meta["description"],
            "config_fields": [_field_to_dict(f) for f in fields],
        }
        required = meta.get("required_connectors")
        if required:
            detail["required_connectors"] = list(required)
        return detail

    catalog: list[JsonValue] = [
        {
            "type": node_id,
            "category": meta["category"],
            "description": meta["description"],
        }
        for node_id, meta in sorted(nodes.items())
    ]
    return {"nodes": catalog}
