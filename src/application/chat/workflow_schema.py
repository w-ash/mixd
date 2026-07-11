"""Node catalog → chat-tool bridge for WorkflowDef.

Hosts the shared ``workflow_def_to_dict`` serializer used wherever a
``WorkflowDef`` crosses into model-facing JSON (system-prompt context, tool
results, pending-action details). ``build_workflow_def_schema()`` — the
mechanical node-catalog → JSON Schema derivation — lands here with the
workflow-generation tools (v0.9.0 Phase 3).
"""

from src.domain.entities.shared import JsonValue
from src.domain.entities.workflow import WorkflowDef


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
