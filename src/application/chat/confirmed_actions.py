"""Confirmed-mutation executors for write tools.

The commit half of the two-phase pattern: ``handle_*`` dispatchers in
``tool_executor`` *propose* (store a pending action), and after the user
approves, the registry routes the claimed action here. Executors read raw
values back out of ``action.details``, re-validate what could have changed
since propose time (TOCTOU), and run the same use case the web UI calls, so
RLS scoping and validation are identical to a human doing it.
"""

from collections.abc import Mapping
from uuid import UUID

from src.application.chat.pending_actions import PendingAction
from src.application.runner import execute_use_case
from src.application.use_cases.workflow_crud import (
    CreateWorkflowCommand,
    CreateWorkflowUseCase,
    UpdateWorkflowCommand,
    UpdateWorkflowUseCase,
)
from src.domain.entities.shared import JsonValue
from src.domain.entities.workflow import Workflow, WorkflowDef, parse_workflow_def
from src.domain.exceptions import NotFoundError, ToolExecutionError


async def _persist(
    action: PendingAction, user_id: str, definition: WorkflowDef
) -> Workflow:
    """Run the create or update use case the proposal named."""
    if action.details.get("mode") == "update":
        workflow_id = UUID(str(action.details["workflow_id"]))
        update_command = UpdateWorkflowCommand(
            user_id=user_id, workflow_id=workflow_id, definition=definition
        )
        result = await execute_use_case(
            lambda uow: UpdateWorkflowUseCase().execute(update_command, uow),
            user_id=user_id,
        )
        return result.workflow
    create_command = CreateWorkflowCommand(user_id=user_id, definition=definition)
    created = await execute_use_case(
        lambda uow: CreateWorkflowUseCase().execute(create_command, uow),
        user_id=user_id,
    )
    return created.workflow


async def exec_save_workflow(action: PendingAction, user_id: str) -> JsonValue:
    """Persist the proposed workflow definition (create or update).

    Create/Update use cases re-run ``validate_workflow_def`` internally —
    that is the commit-time re-validation; a definition that stopped being
    valid between propose and confirm (e.g. a node removed by a deploy)
    surfaces as an actionable error instead of persisting.
    """
    raw_definition = action.details.get("definition")
    if not isinstance(raw_definition, Mapping):
        raise ToolExecutionError("Pending action is missing its definition")
    definition = parse_workflow_def(raw_definition)

    try:
        workflow = await _persist(action, user_id, definition)
    except NotFoundError as e:
        raise ToolExecutionError(
            "The workflow no longer exists — it may have been deleted since "
            "the save was proposed. Generate and save it as a new workflow."
        ) from e
    except ValueError as e:
        raise ToolExecutionError(
            f"The definition failed validation at save time: {e}"
        ) from e

    return {
        "status": "confirmed",
        "description": action.description,
        "workflow_id": str(workflow.id),
        "name": workflow.definition.name,
        "definition_version": workflow.definition_version,
    }
