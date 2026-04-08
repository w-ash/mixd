"""Batch processing result aggregation.

Provides BatchResult class for aggregating success rates and outcomes across batch operations.

For actual batch processing, use the specialized processors:
- DatabaseBatchProcessor: Database operations with transaction safety
- ImportBatchProcessor: File/import operations with memory management
- SimpleBatchProcessor: Basic chunking operations
"""

from enum import Enum
from uuid import UUID

from attrs import define, field


class BatchItemStatus(Enum):
    """Status of an individual item within a batch operation."""

    IMPORTED = "imported"
    PROCESSED = "processed"
    SYNCED = "synced"
    EXPORTED = "exported"
    ERROR = "error"
    SKIPPED = "skipped"


@define(frozen=True, slots=True)
class BatchItemResult:
    """Result of processing a single item in a batch.

    Attributes:
        status: Outcome of the processing attempt.
        track_id: ID of the track that was processed.
        error: Error message if status is ERROR.
        metadata: Additional result data (e.g., reason for skipping).
    """

    status: BatchItemStatus
    track_id: UUID | None = None
    error: str | None = None
    metadata: dict[str, object] = field(factory=dict)


@define(frozen=True, slots=True)
class BatchResult:
    """Success rates and detailed outcomes from processing music items in batches.

    Aggregates results across all batches to show total items processed,
    success/error counts, and processing statistics for user feedback.

    Attributes:
        total_items: Total number of items submitted for processing.
        processed_count: Number of items that were processed (success or failure).
        batch_results: Detailed results from each batch.
    """

    total_items: int
    processed_count: int
    batch_results: list[list[BatchItemResult]] = field(factory=list)

    @property
    def success_count(self) -> int:
        """Items successfully imported, processed, synced, or exported."""
        success_statuses = {
            BatchItemStatus.IMPORTED,
            BatchItemStatus.PROCESSED,
            BatchItemStatus.SYNCED,
            BatchItemStatus.EXPORTED,
        }
        return sum(
            1
            for batch in self.batch_results
            for result in batch
            if result.status in success_statuses
        )

    @property
    def error_count(self) -> int:
        """Items that failed due to API errors or data issues."""
        return self.count_by_status(BatchItemStatus.ERROR)

    @property
    def success_rate(self) -> float:
        """Success rate as percentage (0.0 to 100.0)."""
        if self.processed_count == 0:
            return 0.0
        return round((self.success_count / self.processed_count) * 100, 2)

    def count_by_status(self, status: BatchItemStatus) -> int:
        """Count items with specific status across all batches."""
        return sum(
            1
            for batch in self.batch_results
            for result in batch
            if result.status == status
        )
