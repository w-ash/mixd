"""Integration between Prefect workflows and Rich CLI progress tracking.

Provides utilities to automatically set up Rich progress bars for workflow execution,
enabling dual progress display (CLI + Prefect UI) without requiring manual setup
in every workflow execution.
"""

from typing import Any

from src.config import get_logger

logger = get_logger(__name__)


async def run_workflow_with_progress(
    workflow_def: dict, show_progress: bool = True, **parameters
) -> tuple[dict, Any]:
    """Execute a Prefect workflow with automatic Rich progress bar setup.

    Convenience function that combines workflow execution with progress tracking
    setup. Automatically displays Rich progress bars for CLI users while maintaining
    Prefect UI artifacts for web dashboard users.

    Args:
        workflow_def: JSON workflow definition with tasks and dependencies
        show_progress: Whether to display Rich progress bars (default: True)
        **parameters: Dynamic parameters passed to workflow tasks

    Returns:
        Tuple of (execution context with all task results, structured final result)

    Example:
        # Execute workflow with automatic progress bars
        context, result = await run_workflow_with_progress(
            workflow_def=my_workflow,
            playlist_id="spotify:playlist:123",
            show_progress=True
        )
    """
    # Import here to avoid circular imports
    from .prefect import run_workflow

    logger.info(
        f"Executing workflow with progress tracking: {workflow_def.get('name', 'unnamed')}"
    )

    if show_progress:
        # Execute with Rich progress context using unified display context
        from src.interface.cli.console import live_display_context

        async with live_display_context(show_live=True) as display_context:
            progress_manager = display_context.get_progress_manager()
            # Pass progress_manager to workflow for CLI progress tracking
            context, result = await run_workflow(
                workflow_def, progress_manager=progress_manager, **parameters
            )
    else:
        # Execute without progress display
        context, result = await run_workflow(
            workflow_def, progress_manager=None, **parameters
        )

    logger.info(
        f"Workflow completed successfully: {result.operation_name} "
        f"({result.execution_time:.2f}s, {len(result.tracks)} tracks)"
    )

    return context, result
