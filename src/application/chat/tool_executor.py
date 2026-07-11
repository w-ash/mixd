"""Tool dispatchers for the chat assistant.

One ``handle_*`` coroutine per tool. Each dispatcher is a thin adapter: read
tools project existing query paths into compact summaries; write tools
*propose* — they store a pending action and return a ``pending_confirmation``
payload, and the actual mutation runs only through the confirmed executor in
``confirmed_actions``. The registry (``src.application.tools.registry``)
binds each to its ``ToolSpec``. No business logic lives here — where a use
case is missing or awkward, it is fixed at the source.

Validation is a feedback loop, not a gate: ``generate_workflow_def`` and
``save_workflow`` raise ``ToolExecutionError`` carrying the structured
failure list, which the loop converts into an error tool result the model
self-corrects from within the same turn.
"""

from collections.abc import Mapping
import json
from uuid import UUID

from src.application.chat.pending_actions import pending_action_store
from src.application.chat.protocols import ToolContext
from src.application.chat.workflow_schema import workflow_def_to_dict
from src.application.runner import execute_use_case
from src.application.use_cases.workflow_crud import (
    GetWorkflowCommand,
    GetWorkflowUseCase,
    ListWorkflowsCommand,
    ListWorkflowsUseCase,
    generate_change_summary,
)
from src.application.workflows.definition.validation import (
    is_validation_error,
    validate_workflow_def_detailed,
)
from src.application.workflows.nodes.config_fields import (
    ConfigFieldDef,
    get_node_config_fields,
)
from src.application.workflows.nodes.registry import list_nodes
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.entities.workflow import Workflow, WorkflowDef, parse_workflow_def
from src.domain.exceptions import NotFoundError, ToolExecutionError

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


# --- shared helpers for the workflow tools ----------------------------------


def _propose_action(
    ctx: ToolContext,
    tool_name: str,
    tool_input: Mapping[str, JsonValue],
    description: str,
    details: JsonDict,
) -> JsonDict:
    """Store a pending action and return the pending_confirmation payload.

    The contract the frontend keys on: ``status``/``action_id``/``description``
    /``details``. ``details`` keeps raw values — the confirmed executor reads
    them back directly.
    """
    action = pending_action_store.create(
        user_id=ctx.user_id,
        tool_name=tool_name,
        tool_input=dict(tool_input),
        description=description,
        details=details,
    )
    return {
        "status": "pending_confirmation",
        "action_id": str(action.action_id),
        "description": description,
        "details": details,
    }


def _parse_workflow_input(
    tool_input: Mapping[str, JsonValue],
) -> tuple[WorkflowDef, list[dict[str, str]]]:
    """Parse ``workflow_def`` and split validation findings into (def, warnings).

    Validation *errors* raise ``ToolExecutionError`` carrying the structured
    ``[{task_id, field, message}]`` list so the model can fix every item and
    retry in the same turn. Warnings pass through for the caller to surface.
    """
    raw = tool_input.get("workflow_def")
    if not isinstance(raw, Mapping):
        raise ToolExecutionError("workflow_def must be a JSON object")
    try:
        workflow_def = parse_workflow_def(raw)
    except Exception as e:  # parse is lenient; guard against pathological input
        raise ToolExecutionError(f"Could not parse workflow_def: {e}") from e
    findings = validate_workflow_def_detailed(workflow_def)
    errors = [f for f in findings if is_validation_error(f)]
    if errors:
        raise ToolExecutionError(
            "The workflow definition failed validation. Fix every item and "
            f"retry with the corrected complete definition: {json.dumps(errors)}"
        )
    return workflow_def, [f for f in findings if not is_validation_error(f)]


def _parse_workflow_id(raw: JsonValue) -> UUID:
    try:
        return UUID(str(raw))
    except ValueError as e:
        raise ToolExecutionError(
            f"workflow_id must be a UUID from list_user_workflows, got {raw!r}"
        ) from e


async def _get_owned_workflow(ctx: ToolContext, workflow_id: UUID) -> Workflow:
    command = GetWorkflowCommand(user_id=ctx.user_id, workflow_id=workflow_id)
    try:
        result = await execute_use_case(
            lambda uow: GetWorkflowUseCase().execute(command, uow),
            user_id=ctx.user_id,
        )
    except NotFoundError as e:
        raise ToolExecutionError(
            f"No workflow with id {workflow_id} — call list_user_workflows "
            "for the saved workflows and their ids."
        ) from e
    return result.workflow


# --- workflow read tools -----------------------------------------------------

LIST_USER_WORKFLOWS_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

GET_WORKFLOW_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "workflow_id": {
            "type": "string",
            "description": "UUID of the saved workflow, from list_user_workflows.",
        },
    },
    "required": ["workflow_id"],
    "additionalProperties": False,
}


async def handle_list_user_workflows(
    tool_input: Mapping[str, JsonValue],
    ctx: ToolContext,
) -> JsonValue:
    """Compact listing — names and ids, not full definitions."""
    command = ListWorkflowsCommand(user_id=ctx.user_id)
    result = await execute_use_case(
        lambda uow: ListWorkflowsUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    workflows: list[JsonValue] = [
        {
            "workflow_id": str(w.id),
            "name": w.definition.name,
            "description": w.definition.description,
            "task_count": len(w.definition.tasks),
        }
        for w in result.workflows
    ]
    return {"workflows": workflows, "total_count": result.total_count}


async def handle_get_workflow(
    tool_input: Mapping[str, JsonValue],
    ctx: ToolContext,
) -> JsonValue:
    workflow_id = _parse_workflow_id(tool_input.get("workflow_id"))
    workflow = await _get_owned_workflow(ctx, workflow_id)
    return {
        "workflow_id": str(workflow.id),
        "name": workflow.definition.name,
        "description": workflow.definition.description,
        "definition_version": workflow.definition_version,
        "definition": workflow_def_to_dict(workflow.definition),
    }


# --- workflow generation + validation ----------------------------------------

VALIDATE_WORKFLOW_DEF_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "workflow_def": {
            "type": "object",
            "description": (
                "A complete workflow definition object (id, name, tasks) to "
                "check against the node catalog and DAG rules."
            ),
        },
    },
    "required": ["workflow_def"],
    "additionalProperties": False,
}


async def handle_generate_workflow_def(
    tool_input: Mapping[str, JsonValue],
    ctx: ToolContext,  # shared dispatcher signature; generation is stateless
) -> JsonValue:
    """Validate a generated definition and echo it normalized for the preview.

    The tool's input *is* the definition (schema-guided generation): the
    derived JSON Schema steers the model, this dispatcher enforces. The echo
    is parse→re-serialize normalized so the frontend always sees canonical
    shape (``upstream``/``config`` present on every task).
    """
    workflow_def, warnings = _parse_workflow_input(tool_input)
    return {
        "status": "valid",
        "workflow_def": workflow_def_to_dict(workflow_def),
        "warnings": warnings,
        "task_count": len(workflow_def.tasks),
    }


async def handle_validate_workflow_def(
    tool_input: Mapping[str, JsonValue],
    ctx: ToolContext,  # shared dispatcher signature; validation is stateless
) -> JsonValue:
    """Report findings on a definition the model did not just author.

    Unlike ``generate_workflow_def``, findings come back as a *success*
    result: this tool reports on a def the user owns — an error result would
    push the model to "fix" it unasked.
    """
    raw = tool_input.get("workflow_def")
    if not isinstance(raw, Mapping):
        raise ToolExecutionError("workflow_def must be a JSON object")
    findings = validate_workflow_def_detailed(parse_workflow_def(raw))
    errors = [f for f in findings if is_validation_error(f)]
    warnings = [f for f in findings if not is_validation_error(f)]
    return {"valid": not errors, "errors": errors, "warnings": warnings}


# --- save_workflow (two-phase write) ------------------------------------------

SAVE_WORKFLOW_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "workflow_def": {
            "type": "object",
            "description": (
                "The complete workflow definition to persist — the exact "
                "object last accepted by generate_workflow_def."
            ),
        },
        "workflow_id": {
            "type": "string",
            "description": (
                "UUID of an existing workflow to update. Omit to create a new workflow."
            ),
        },
    },
    "required": ["workflow_def"],
    "additionalProperties": False,
}


async def handle_save_workflow(
    tool_input: Mapping[str, JsonValue],
    ctx: ToolContext,
) -> JsonValue:
    """Propose a save — nothing persists until the user confirms.

    Validates first so an invalid definition can never sit in a pending
    action; the confirmed executor (``exec_save_workflow``) re-validates at
    commit time via the use cases' own gate.
    """
    workflow_def, _warnings = _parse_workflow_input(tool_input)
    normalized = workflow_def_to_dict(workflow_def)
    task_count = len(workflow_def.tasks)

    raw_id = tool_input.get("workflow_id")
    if raw_id is None:
        description = (
            f"Create workflow '{workflow_def.name}' "
            f"with {task_count} task{'s' if task_count != 1 else ''}"
        )
        details: JsonDict = {
            "mode": "create",
            "name": workflow_def.name,
            "task_count": task_count,
            "definition": normalized,
        }
    else:
        workflow_id = _parse_workflow_id(raw_id)
        existing = await _get_owned_workflow(ctx, workflow_id)
        changes = generate_change_summary(existing.definition, workflow_def)
        description = f"Update workflow '{existing.definition.name}': {changes}"
        details = {
            "mode": "update",
            "workflow_id": str(workflow_id),
            "name": workflow_def.name,
            "task_count": task_count,
            "changes": changes,
            "definition": normalized,
        }
    return _propose_action(ctx, "save_workflow", tool_input, description, details)
