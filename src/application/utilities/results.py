"""Unified result factory to eliminate duplicate result creation patterns.

This module provides standardized result creation for all service operations,
replacing the duplicate result creation methods scattered across services.
"""

from datetime import datetime
from typing import Any

from attrs import define, field

from src.domain.entities.operations import OperationResult


@define(frozen=True, slots=True)
class ImportResultData:
    """Data structure for import operation results."""

    raw_data_count: int
    imported_count: int
    batch_id: str
    filtered_count: int = 0  # Plays filtered out (too short, incognito, etc.)
    duplicate_count: int = 0  # Plays that already existed in database
    error_count: int = 0
    new_tracks_count: int = 0  # Canonical tracks created during import
    updated_tracks_count: int = 0  # Existing canonical tracks with new plays
    checkpoint_timestamp: datetime | None = None
    tracks: list[Any] = field(factory=list)


@define(frozen=True, slots=True)
class SyncResultData:
    """Data structure for sync operation results."""

    imported_count: int = 0
    exported_count: int = 0
    filtered_count: int = 0
    error_count: int = 0
    batch_id: str = ""
    already_liked: int = 0
    candidates: int = 0
    tracks: list[Any] = field(factory=list)

    @property
    def total_processed(self) -> int:
        """Calculate total processed items."""
        return (
            self.imported_count
            + self.exported_count
            + self.filtered_count
            + self.error_count
        )

    @property
    def success_count(self) -> int:
        """Calculate successful operations (imported + exported)."""
        return self.imported_count + self.exported_count


class ResultFactory:
    """Factory for creating standardized OperationResult instances.

    Eliminates duplicate result creation patterns across services and provides
    consistent structure for all operation results.
    """

    @staticmethod
    def create_import_result(
        operation_name: str,
        import_data: ImportResultData,
        execution_time: float = 0.0,
    ) -> OperationResult:
        """Create standardized import operation result.

        Args:
            operation_name: Name of the import operation
            import_data: Import statistics and metadata
            execution_time: Operation execution time in seconds

        Returns:
            OperationResult with standardized import summary metrics
        """
        result = OperationResult(
            operation_name=operation_name,
            tracks=import_data.tracks,
            execution_time=execution_time,
        )

        # Add metadata (batch_id, checkpoint, etc.)
        result.metadata["batch_id"] = import_data.batch_id
        if import_data.checkpoint_timestamp:
            result.metadata["checkpoint_timestamp"] = (
                import_data.checkpoint_timestamp.isoformat()
            )

        # Add summary metrics with display order
        result.summary_metrics.add(
            "raw_plays", import_data.raw_data_count, "Raw Plays Found", significance=0
        )
        result.summary_metrics.add(
            "imported",
            import_data.imported_count,
            "Track Plays Created",
            significance=1,
        )

        if import_data.filtered_count > 0:
            result.summary_metrics.add(
                "filtered",
                import_data.filtered_count,
                "Filtered (Too Short)",
                significance=2,
            )
        if import_data.duplicate_count > 0:
            result.summary_metrics.add(
                "duplicates",
                import_data.duplicate_count,
                "Filtered (Duplicates)",
                significance=3,
            )
        if import_data.new_tracks_count > 0:
            result.summary_metrics.add(
                "new_tracks",
                import_data.new_tracks_count,
                "New Tracks",
                significance=4,
            )
        if import_data.updated_tracks_count > 0:
            result.summary_metrics.add(
                "updated_tracks",
                import_data.updated_tracks_count,
                "Updated Tracks",
                significance=5,
            )
        if import_data.error_count > 0:
            result.summary_metrics.add(
                "errors", import_data.error_count, "Errors", significance=6
            )

        # Calculate success rate
        attempted = (
            import_data.imported_count
            + import_data.duplicate_count
            + import_data.error_count
        )
        if attempted > 0:
            success_rate = (import_data.imported_count / attempted) * 100
            result.summary_metrics.add(
                "success_rate",
                success_rate,
                "Success Rate",
                format="percent",
                significance=7,
            )

        return result

    @staticmethod
    def create_error_result(
        operation_name: str,
        error_message: str,
        batch_id: str = "",
        execution_time: float = 0.0,
    ) -> OperationResult:
        """Create standardized error result.

        Args:
            operation_name: Name of the failed operation
            error_message: Description of the error
            batch_id: Batch identifier for tracking
            execution_time: Operation execution time before failure

        Returns:
            OperationResult representing the error state
        """
        result = OperationResult(
            operation_name=operation_name,
            execution_time=execution_time,
        )

        # Add metadata
        result.metadata["batch_id"] = batch_id
        result.metadata["errors"] = [error_message]

        # Add error summary metric
        result.summary_metrics.add("errors", 1, "Errors", significance=0)

        return result
