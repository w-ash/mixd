"""Executes playlist synchronization workflows from declarative definitions.

Runs multi-step playlist operations like syncing between music services,
matching tracks across platforms, and enriching metadata. Provides a
business logic layer between user interfaces and the workflow engine.
"""

from datetime import UTC, datetime
from typing import Any

from attrs import define

from src.config import get_logger
from src.domain.entities.operations import WorkflowResult

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class WorkflowCommand:
    """Parameters for executing a playlist synchronization workflow.

    Contains the workflow definition (steps to execute) and runtime
    parameters needed to customize the execution for specific playlists
    or music services.
    """

    workflow_def: dict[str, Any]
    parameters: dict[str, Any]

    def validate(self) -> bool:
        """Check if the workflow command contains required data.

        Returns:
            True if command has valid workflow definition with tasks
        """
        if not self.workflow_def:
            return False
        return "tasks" in self.workflow_def


@define(frozen=True, slots=True)
class WorkflowExecutionResult:
    """Outcome of a playlist workflow execution with performance metrics.

    Contains the execution context, results from workflow tasks, timing
    information, and error details if the workflow failed.
    """

    context: dict[str, Any]
    workflow_result: WorkflowResult
    execution_time_ms: int
    success: bool
    error_message: str | None = None


class WorkflowExecutor:
    """Runs playlist synchronization workflows with error handling and metrics.

    Executes multi-step playlist operations defined declaratively, manages
    the execution lifecycle including timing and error handling, and provides
    a consistent interface for CLI and API clients.
    """

    async def execute(self, command: WorkflowCommand) -> WorkflowExecutionResult:
        """Run a playlist workflow and capture execution metrics.

        Validates the workflow command, executes the defined tasks,
        and returns comprehensive results including timing and any errors.

        Args:
            command: Workflow definition and runtime parameters

        Returns:
            Execution result with context, outcomes, and performance data

        Raises:
            ValueError: If the workflow command fails validation
        """
        if not command.validate():
            raise ValueError(
                "Invalid workflow command: failed business rule validation"
            )

        start_time = datetime.now(UTC)

        try:
            # Import here to avoid circular imports
            from src.application.workflows.prefect import run_workflow

            # Execute workflow - context provides proper session management
            context, workflow_result = await run_workflow(
                command.workflow_def, **command.parameters
            )

            # Calculate execution time
            end_time = datetime.now(UTC)
            execution_time_ms = int((end_time - start_time).total_seconds() * 1000)

            return WorkflowExecutionResult(
                context=context,
                workflow_result=workflow_result,
                execution_time_ms=execution_time_ms,
                success=True,
            )

        except Exception as e:
            logger.exception(f"Workflow execution failed: {e}")
            end_time = datetime.now(UTC)
            execution_time_ms = int((end_time - start_time).total_seconds() * 1000)

            return WorkflowExecutionResult(
                context={},
                workflow_result=WorkflowResult(
                    operation_name=command.workflow_def.get("name", "unknown"),
                    execution_time=execution_time_ms / 1000,
                ),
                execution_time_ms=execution_time_ms,
                success=False,
                error_message=str(e),
            )


# Convenience function for single workflow execution
async def execute_workflow_use_case(
    workflow_def: dict, **parameters
) -> WorkflowExecutionResult:
    """Execute a playlist workflow with simplified parameters.

    Convenience function that wraps workflow execution in a simple interface
    for clients that don't need the full command object pattern.

    Args:
        workflow_def: Declarative workflow definition with tasks
        **parameters: Runtime parameters for workflow customization

    Returns:
        Complete workflow execution result with metrics and outcomes
    """
    command = WorkflowCommand(workflow_def=workflow_def, parameters=parameters)

    executor = WorkflowExecutor()
    return await executor.execute(command)
