"""In-process workflow concurrency guard (no Prefect dependency).

Lives outside ``prefect.py`` so importing the guard does not pull in the
Prefect 3 dependency tree at uvicorn startup. ``run_workflow`` in
``prefect.py`` calls ``acquire_workflow_slot`` / ``release_workflow_slot``
to coordinate with this module's internal state.
"""

import asyncio

_running_workflows: set[str] = set()
_running_lock = asyncio.Lock()


async def is_workflow_running(workflow_id: str) -> bool:
    """Return True if a workflow is currently executing in this process."""
    async with _running_lock:
        return workflow_id in _running_workflows


async def acquire_workflow_slot(workflow_id: str) -> None:
    """Reserve a slot for ``workflow_id`` or raise if one is already running.

    Called by ``run_workflow`` before flow execution begins.
    """
    async with _running_lock:
        if workflow_id in _running_workflows:
            raise WorkflowAlreadyRunningError(workflow_id)
        _running_workflows.add(workflow_id)


async def release_workflow_slot(workflow_id: str) -> None:
    """Release the slot held by ``workflow_id`` (no-op if not held).

    Called by ``run_workflow`` in its ``finally`` block.
    """
    async with _running_lock:
        _running_workflows.discard(workflow_id)


class WorkflowAlreadyRunningError(Exception):
    """Raised when attempting to execute a workflow that is already running."""

    def __init__(self, workflow_id: str) -> None:
        self.workflow_id = workflow_id
        super().__init__(f"Workflow '{workflow_id}' is already running")
