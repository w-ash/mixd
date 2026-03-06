"""Concrete NodeExecutionObserver implementations.

ProgressNodeObserver bridges the node lifecycle protocol to the existing
AsyncProgressManager, emitting progress events for CLI Rich progress bars.
"""

from src.application.services.progress_manager import AsyncProgressManager
from src.config.logging import get_logger
from src.domain.entities.progress import ProgressStatus, create_progress_event
from src.domain.entities.workflow import WorkflowTaskDef

from .protocols import NodeResult

logger = get_logger(__name__)


class ProgressNodeObserver:
    """Emits progress events via AsyncProgressManager on node lifecycle transitions.

    Replaces the inline progress emission that was previously in execute_node().
    """

    _progress_manager: AsyncProgressManager
    _workflow_operation_id: str

    def __init__(
        self,
        progress_manager: AsyncProgressManager,
        workflow_operation_id: str,
    ) -> None:
        self._progress_manager = progress_manager
        self._workflow_operation_id = workflow_operation_id

    async def on_node_starting(
        self,
        task_def: WorkflowTaskDef,
        execution_order: int,
        total_nodes: int,
        input_track_count: int | None,
    ) -> None:
        pass  # Progress bars show completion, not start

    async def on_node_completed(
        self,
        task_def: WorkflowTaskDef,
        result: NodeResult,
        execution_order: int,
        total_nodes: int,
        duration_ms: int,
        input_track_count: int | None,
        output_track_count: int,
    ) -> None:
        display_name = task_def.type.replace("_", " ").replace(".", " ").title()

        event = create_progress_event(
            operation_id=self._workflow_operation_id,
            current=execution_order,
            total=total_nodes,
            message=f"Completed {display_name}",
            status=ProgressStatus.IN_PROGRESS,
        )
        await self._progress_manager.emit_progress(event)

    async def on_node_failed(
        self,
        task_def: WorkflowTaskDef,
        error: Exception,
        execution_order: int,
        total_nodes: int,
        duration_ms: int,
    ) -> None:
        display_name = task_def.type.replace("_", " ").replace(".", " ").title()

        event = create_progress_event(
            operation_id=self._workflow_operation_id,
            current=execution_order,
            total=total_nodes,
            message=f"Failed {display_name}: {error}",
            status=ProgressStatus.FAILED,
        )
        await self._progress_manager.emit_progress(event)
