"""Node catalog → chat-tool bridge for WorkflowDef.

``build_workflow_def_schema()`` derives the ``generate_workflow_def`` tool's
input schema mechanically from the node registry + config-field definitions,
so the schema is in lockstep with the catalog by construction. Constraints
imposed by Anthropic structured outputs (see the v0.9.x Pre-Work table):
``anyOf`` per node type (``oneOf`` is rejected), numeric ranges as prose in
``description`` (``minimum``/``maximum`` are rejected), ``enum`` rather than
``const``, no ``strict``. ``additionalProperties: false`` on every object.
Iteration is sorted so the rendered schema — part of the cached tool prefix —
stays byte-stable across processes.

Also hosts the shared ``workflow_def_to_dict`` serializer used wherever a
``WorkflowDef`` crosses into model-facing JSON (system-prompt context, tool
results, pending-action details).
"""

import functools

from src.application.workflows.nodes.config_fields import (
    ConfigFieldDef,
    get_node_config_fields,
)
from src.application.workflows.nodes.registry import list_nodes
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.entities.workflow import WorkflowDef

_FIELD_TYPE_TO_JSON: dict[str, str] = {
    "string": "string",
    "number": "number",
    "boolean": "boolean",
}


def _field_description(field: ConfigFieldDef) -> str:
    """Field description with defaults and numeric ranges rendered as prose."""
    parts: list[str] = []
    if field.description:
        text = field.description.strip()
        parts.append(text if text.endswith(".") else f"{text}.")
    if field.min is not None and field.max is not None:
        parts.append(f"Must be between {field.min:g} and {field.max:g}.")
    elif field.min is not None:
        parts.append(f"Must be at least {field.min:g}.")
    elif field.max is not None:
        parts.append(f"Must be at most {field.max:g}.")
    if field.default is not None:
        parts.append(f"Defaults to {field.default}.")
    return " ".join(parts)


def _field_schema(field: ConfigFieldDef) -> JsonDict:
    schema: JsonDict
    if field.field_type == "select":
        schema = {
            "type": "string",
            "enum": [option.value for option in field.options],
        }
    else:
        schema = {"type": _FIELD_TYPE_TO_JSON[field.field_type]}
    description = _field_description(field)
    if description:
        schema["description"] = description
    return schema


def _task_branch(
    node_id: str, description: str, fields: tuple[ConfigFieldDef, ...]
) -> JsonDict:
    """One ``anyOf`` branch: the task shape for a single node type."""
    config_properties: JsonDict = {f.key: _field_schema(f) for f in fields}
    config: JsonDict = {
        "type": "object",
        "properties": config_properties,
        "additionalProperties": False,
    }
    required_fields = [f.key for f in fields if f.required]
    if required_fields:
        config["required"] = required_fields
    task_required = ["id", "type"]
    if required_fields:
        task_required.append("config")
    return {
        "type": "object",
        "description": description,
        "properties": {
            "id": {
                "type": "string",
                "description": "Unique task id within this workflow.",
            },
            "type": {"enum": [node_id]},
            "config": config,
            "upstream": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Task ids whose output feeds this task. Sources take none."
                ),
            },
        },
        "required": task_required,
        "additionalProperties": False,
    }


@functools.cache
def build_workflow_def_schema() -> JsonDict:
    """The ``generate_workflow_def`` input schema, derived from the catalog."""
    all_fields = get_node_config_fields()
    branches = [
        _task_branch(node_id, meta["description"], all_fields.get(node_id, ()))
        for node_id, meta in sorted(list_nodes().items())
    ]
    return {
        "type": "object",
        "properties": {
            "workflow_def": {
                "type": "object",
                "description": (
                    "The complete workflow definition — always the full "
                    "definition, never a patch."
                ),
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Short kebab-case slug for the workflow.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Human-readable workflow name.",
                    },
                    "description": {
                        "type": "string",
                        "description": "One sentence on what the workflow does.",
                    },
                    "version": {
                        "type": "string",
                        "description": 'Definition schema version; use "1.0".',
                    },
                    "tasks": {
                        "type": "array",
                        "description": (
                            "The DAG tasks; edges are each task's upstream list."
                        ),
                        "items": {"anyOf": branches},
                    },
                },
                "required": ["id", "name", "tasks"],
                "additionalProperties": False,
            },
        },
        "required": ["workflow_def"],
        "additionalProperties": False,
    }


def workflow_def_to_dict(workflow_def: WorkflowDef) -> dict[str, JsonValue]:
    """Serialize a WorkflowDef to the canonical JSON shape.

    The inverse of ``parse_workflow_def`` — round-tripping through both
    normalizes a lenient input (missing ``upstream``/``config`` filled with
    defaults). ``result_key`` is included only when set, keeping the common
    case compact for the model.
    """
    tasks: list[JsonValue] = []
    for task in workflow_def.tasks:
        task_dict: dict[str, JsonValue] = {
            "id": task.id,
            "type": task.type,
            "config": dict(task.config),
            "upstream": list(task.upstream),
        }
        if task.result_key is not None:
            task_dict["result_key"] = task.result_key
        tasks.append(task_dict)
    return {
        "id": workflow_def.id,
        "name": workflow_def.name,
        "description": workflow_def.description,
        "version": workflow_def.version,
        "tasks": tasks,
    }
