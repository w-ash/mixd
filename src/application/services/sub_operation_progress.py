"""Bridge between infrastructure progress callbacks and AsyncProgressManager sub-operations.

Creates infrastructure-compatible callbacks (plain async callables) that emit
progress events as sub-operations on the application's AsyncProgressManager.
This keeps infrastructure free of application imports while enabling granular
progress tracking for rate-limited batch processing and phased operations.
"""

from src.config import get_logger
from src.config.constants import NodeType, Phase
from src.domain.entities.progress import (
    OperationStatus,
    ProgressOperation,
    create_progress_event,
)
from src.domain.matching.types import ProgressCallback

from .progress_manager import AsyncProgressManager

logger = get_logger(__name__).bind(service="sub_operation_progress")


async def create_sub_operation(
    progress_manager: AsyncProgressManager,
    description: str,
    total_items: int | None,
    parent_operation_id: str,
    phase: Phase,
    node_type: NodeType,
) -> tuple[str, ProgressCallback]:
    """Create a sub-operation and return an infrastructure-compatible callback.

    Starts a ProgressOperation on the manager with parent metadata,
    then returns a callback that callers can invoke with (completed, total, message)
    to emit progress events.

    Args:
        progress_manager: The application progress manager.
        description: Human-readable sub-operation description.
        total_items: Expected total (None for indeterminate).
        parent_operation_id: ID of the parent workflow operation.
        phase: Phase identifier (e.g., "fetch", "enrich", "save").
        node_type: Node type for context (e.g., "enricher", "source").

    Returns:
        Tuple of (sub_operation_id, callback_fn).
    """
    operation = ProgressOperation(
        description=description,
        total_items=total_items,
        metadata={
            "parent_operation_id": parent_operation_id,
            "phase": phase,
            "node_type": node_type,
        },
    )

    sub_op_id = await progress_manager.start_operation(operation)

    async def callback(completed: int, total: int, message: str) -> None:
        event = create_progress_event(
            operation_id=sub_op_id,
            current=completed,
            total=total,
            message=message,
        )
        await progress_manager.emit_progress(event)

    return sub_op_id, callback


async def complete_sub_operation(
    progress_manager: AsyncProgressManager,
    sub_operation_id: str,
    status: OperationStatus = OperationStatus.COMPLETED,
) -> None:
    """Complete a sub-operation with the given status."""
    await progress_manager.complete_operation(sub_operation_id, status)


async def emit_phase_progress(
    progress_manager: AsyncProgressManager,
    parent_operation_id: str,
    phase: Phase,
    node_type: NodeType,
    message: str,
) -> None:
    """Emit a lightweight phase transition for source/destination nodes.

    Creates a short-lived indeterminate sub-operation that signals a phase
    change (e.g., "Fetching playlist from Spotify") without item-level counting.
    The sub-operation completes immediately after creation.

    Args:
        progress_manager: The application progress manager.
        parent_operation_id: ID of the parent workflow operation.
        phase: Phase identifier (e.g., "fetch", "save", "sync").
        node_type: Node type (e.g., "source", "destination").
        message: Human-readable phase description.
    """
    operation = ProgressOperation(
        description=message,
        total_items=None,
        metadata={
            "parent_operation_id": parent_operation_id,
            "phase": phase,
            "node_type": node_type,
        },
    )

    sub_op_id = await progress_manager.start_operation(operation)

    # Emit a single progress event for the phase
    event = create_progress_event(
        operation_id=sub_op_id,
        current=0,
        total=None,
        message=message,
    )
    await progress_manager.emit_progress(event)

    # Phase sub-operations complete immediately — they're just signals
    await progress_manager.complete_operation(sub_op_id, OperationStatus.COMPLETED)
