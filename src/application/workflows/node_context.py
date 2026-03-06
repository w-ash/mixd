"""Context management for node operations.

Provides specialized extraction patterns for workflow operations,
implementing efficient path-based access to nested domain structures.
This module decouples data access patterns from orchestration logic.
"""

# pyright: reportExplicitAny=false, reportAny=false

from typing import Any, cast

from attrs import define

from src.config import get_logger
from src.config.constants import NodeType, Phase
from src.domain.entities.track import TrackList

from .protocols import NodeResult, UseCaseProvider, WorkflowContext

logger = get_logger(__name__)

# Domain types
type TaskID = str


@define(frozen=True, slots=True)
class NodeContext:
    """Context extractor with path-based access."""

    data: dict[str, Any]

    def __init__(self, data: dict[str, Any]) -> None:
        object.__setattr__(self, "data", data)

    def extract_tracklist(self) -> TrackList:
        """Extract primary tracklist from context.

        Supports both workflow contexts (with upstream_task_id) and direct contexts
        (with tracklist key) for testing compatibility.
        """
        # Check for workflow context with upstream task — data dict is Prefect's
        # heterogeneous context (task results, metadata, workflow_context), inherently Any-valued
        if "upstream_task_id" in self.data:
            upstream_id = str(self.data["upstream_task_id"])
            upstream_data = self.data.get(upstream_id)
            if isinstance(upstream_data, dict) and "tracklist" in upstream_data:
                result: object = upstream_data["tracklist"]  # type: ignore[reportAny]  # dict narrowed from Any
                if isinstance(result, TrackList):
                    return result

        # Check for direct tracklist (testing/simple contexts)
        raw_tracklist: object = self.data.get("tracklist")
        if isinstance(raw_tracklist, TrackList):
            return raw_tracklist

        raise ValueError(
            "Missing required tracklist from upstream node or direct context"
        )

    def collect_tracklists(self, task_ids: list[str]) -> list[TrackList]:
        """Collect tracklists from multiple task results."""
        tracklists: list[TrackList] = []
        for task_id in task_ids:
            if task_id not in self.data:
                logger.warning(f"Task ID not found in context: {task_id}")
                continue

            task_result: object = self.data[task_id]  # Prefect context dict
            if not isinstance(task_result, dict):
                logger.warning(f"Invalid task result for {task_id}: not a dictionary")
                continue

            if "tracklist" not in task_result:
                logger.warning(f"Task result for {task_id} missing 'tracklist' key")
                continue

            node_result = cast(NodeResult, task_result)
            tracklists.append(node_result["tracklist"])

        if not tracklists:
            # This should raise an exception rather than just logging
            raise ValueError(f"No valid tracklists found in upstream tasks: {task_ids}")

        return tracklists

    # === DRY Helper Functions ===

    def extract_workflow_context(self) -> WorkflowContext:
        """Extract workflow context with validation.

        Returns:
            WorkflowContext for UoW execution

        Raises:
            ValueError: If workflow context not found
        """
        workflow_context: WorkflowContext = self.data.get("workflow_context")  # type: ignore[reportAny]  # Prefect context dict
        if not workflow_context:
            raise ValueError("Workflow context not found in context")
        return workflow_context

    def extract_use_cases(self) -> UseCaseProvider:
        """Extract use case provider via workflow context.

        Returns:
            UseCaseProvider for getting use case instances

        Raises:
            ValueError: If workflow context or use case provider not found
        """
        return self.extract_workflow_context().use_cases

    def get_connector(self, connector_name: str) -> object:
        """Get connector instance via workflow context's connector registry.

        Args:
            connector_name: Name of connector to retrieve (e.g., "spotify", "lastfm")

        Returns:
            Connector instance — callers narrow via capability protocols

        Raises:
            ValueError: If connector registry or specific connector not found
        """
        registry = self.extract_workflow_context().connectors
        available_connectors = registry.list_connectors()
        if connector_name not in available_connectors:
            raise ValueError(
                f"Unsupported connector: {connector_name}. Available: {available_connectors}"
            )

        return registry.get_connector(connector_name)

    async def emit_phase_progress(
        self, phase: Phase, node_type: NodeType, message: str
    ) -> None:
        """Emit a lightweight phase progress event if progress tracking is active.

        No-ops silently when progress_manager or workflow_operation_id are absent
        (e.g., CLI runs without SSE progress).
        """
        progress_manager = self.data.get("progress_manager")
        workflow_operation_id = self.data.get("workflow_operation_id")
        if progress_manager and workflow_operation_id:
            from src.application.services.sub_operation_progress import (
                emit_phase_progress,
            )

            await emit_phase_progress(
                progress_manager,
                workflow_operation_id,
                phase=phase,
                node_type=node_type,
                message=message,
            )
