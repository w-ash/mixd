"""Concrete NodeExecutionObserver implementations.

ProgressNodeObserver bridges the node lifecycle protocol to the existing
AsyncProgressManager, emitting progress events for CLI Rich progress bars.

NullNodeObserver is the null-object default — eliminates None checks in the
orchestration loop when no observer is provided.
"""

from src.application.services.progress_manager import AsyncProgressManager
from src.config.logging import get_logger
from src.domain.entities.progress import ProgressStatus, create_progress_event
from src.domain.entities.workflow import NodeExecutionEvent

from .protocols import NodeResult

logger = get_logger(__name__)


def _format_node_display_name(node_type: str) -> str:
    """Convert dotted node type to human-readable title (e.g. 'enricher.spotify' → 'Enricher Spotify')."""
    return node_type.replace("_", " ").replace(".", " ").title()


class NullNodeObserver:
    """No-op observer — eliminates None checks when no observer is provided."""

    async def on_node_starting(self, event: NodeExecutionEvent) -> None:
        pass

    async def on_node_completed(
        self, event: NodeExecutionEvent, result: NodeResult
    ) -> None:
        pass

    async def on_node_failed(self, event: NodeExecutionEvent, error: Exception) -> None:
        pass


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

    async def on_node_starting(self, event: NodeExecutionEvent) -> None:
        pass  # Progress bars show completion, not start

    async def on_node_completed(
        self, event: NodeExecutionEvent, result: NodeResult
    ) -> None:
        display_name = _format_node_display_name(event.task_def.type)

        progress_event = create_progress_event(
            operation_id=self._workflow_operation_id,
            current=event.execution_order,
            total=event.total_nodes,
            message=f"Completed {display_name}",
            status=ProgressStatus.IN_PROGRESS,
        )
        await self._progress_manager.emit_progress(progress_event)

    async def on_node_failed(self, event: NodeExecutionEvent, error: Exception) -> None:
        display_name = _format_node_display_name(event.task_def.type)

        progress_event = create_progress_event(
            operation_id=self._workflow_operation_id,
            current=event.execution_order,
            total=event.total_nodes,
            message=f"Failed {display_name}: {error}",
            status=ProgressStatus.FAILED,
        )
        await self._progress_manager.emit_progress(progress_event)
