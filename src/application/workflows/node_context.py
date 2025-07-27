"""Context management for node operations.

Provides specialized extraction patterns for workflow operations,
implementing efficient path-based access to nested domain structures.
This module decouples data access patterns from orchestration logic.
"""

from dataclasses import dataclass
from typing import Any

from src.config import get_logger
from src.domain.entities.track import TrackList

logger = get_logger(__name__)

# Domain types
type TaskID = str
type DataPath = str | list[str]
type ContextData = dict[str, Any]


@dataclass(frozen=True)
class NodeContext:
    """Context extractor with path-based access."""

    data: dict

    def __init__(self, data: dict) -> None:
        object.__setattr__(self, "data", data)

    def get(self, path: str, default: Any = None) -> Any:
        """Get value from nested context using dot notation."""
        parts = path.split(".")
        current = self.data
        for part in parts:
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def extract_tracklist(self) -> TrackList:
        """Extract primary tracklist from context.

        Supports both workflow contexts (with upstream_task_id) and direct contexts
        (with tracklist key) for testing compatibility.
        """
        # Check for workflow context with upstream task
        if "upstream_task_id" in self.data:
            upstream_id = self.data["upstream_task_id"]
            if upstream_id in self.data and "tracklist" in self.data[upstream_id]:
                return self.data[upstream_id]["tracklist"]

        # Check for direct tracklist (testing/simple contexts)
        if "tracklist" in self.data:
            tracklist = self.data["tracklist"]
            if isinstance(tracklist, TrackList):
                return tracklist

        raise ValueError(
            "Missing required tracklist from upstream node or direct context"
        )

    def collect_tracklists(self, task_ids: list[str]) -> list[TrackList]:
        """Collect tracklists from multiple task results."""
        tracklists = []
        for task_id in task_ids:
            if task_id not in self.data:
                logger.warning(f"Task ID not found in context: {task_id}")
                continue

            task_result = self.data[task_id]
            if not isinstance(task_result, dict):
                logger.warning(f"Invalid task result for {task_id}: not a dictionary")
                continue

            tracklist = task_result.get("tracklist")
            if not isinstance(tracklist, TrackList):
                logger.warning(
                    f"Missing or invalid tracklist in task result: {task_id}",
                )
                continue

            tracklists.append(tracklist)

        if not tracklists:
            # This should raise an exception rather than just logging
            raise ValueError(f"No valid tracklists found in upstream tasks: {task_ids}")

        return tracklists

    # === DRY Helper Functions ===

    def extract_workflow_context(self):
        """Extract workflow context with validation.

        Returns:
            WorkflowContext for UoW execution

        Raises:
            ValueError: If workflow context not found
        """
        workflow_context = self.data.get("workflow_context")
        if not workflow_context:
            raise ValueError("Workflow context not found in context")
        return workflow_context

    def extract_use_cases(self):
        """Extract use case provider with validation.

        Returns:
            UseCaseProvider for getting use case instances

        Raises:
            ValueError: If use case provider not found
        """
        use_cases = self.data.get("use_cases")
        if not use_cases:
            raise ValueError("Use case provider not found in context")
        return use_cases

    def get_connector(self, connector_name: str):
        """Get connector instance with validation.

        Args:
            connector_name: Name of connector to retrieve (e.g., "spotify", "lastfm")

        Returns:
            Connector instance

        Raises:
            ValueError: If connector registry or specific connector not found
        """
        connector_registry = self.data.get("connectors")
        if not connector_registry:
            raise ValueError("No connector registry available")

        available_connectors = connector_registry.list_connectors()
        if connector_name not in available_connectors:
            raise ValueError(
                f"Unsupported connector: {connector_name}. Available: {available_connectors}"
            )

        return connector_registry.get_connector(connector_name)

    @staticmethod
    def format_playlist_result(operation: str, result, tracklist, **extras):
        """Format standard playlist operation result.

        Args:
            operation: Operation name (e.g., "create_internal_playlist")
            result: Use case result object with playlist and track data
            tracklist: Original input tracklist
            **extras: Additional fields to include in result

        Returns:
            Standardized result dictionary
        """
        base_result = {
            "operation": operation,
            "playlist": result.playlist,
            "playlist_name": result.playlist.name,
            "playlist_id": result.playlist.id,
            "tracklist": tracklist,
            "persisted_tracks": result.enriched_tracks,
            "track_count": result.track_count,
            "execution_time_ms": result.execution_time_ms,
        }

        # Merge in any additional fields
        base_result.update(extras)
        return base_result

    @staticmethod
    def format_enrichment_result(operation: str, enriched_tracklist, metrics, **extras):
        """Format standard enrichment operation result.

        Args:
            operation: Operation name (e.g., "spotify_enrichment")
            enriched_tracklist: TrackList with enriched data
            metrics: Dictionary of metrics added
            **extras: Additional fields to include in result

        Returns:
            Standardized enrichment result dictionary
        """
        base_result = {
            "tracklist": enriched_tracklist,
            "operation": operation,
            "metrics_count": sum(len(values) for values in metrics.values()),
        }

        # Merge in any additional fields
        base_result.update(extras)
        return base_result
