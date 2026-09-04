"""Batch processing result aggregation.

Per-item outcome types shared by batch import/export flows.

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
