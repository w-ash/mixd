"""Batch processing result aggregation and protocols.

Provides BatchResult class for aggregating success rates and outcomes across batch operations,
plus common protocols used by batch processing systems.

For actual batch processing, use the specialized processors:
- DatabaseBatchProcessor: Database operations with transaction safety
- ImportBatchProcessor: File/import operations with memory management
- SimpleBatchProcessor: Basic chunking operations
"""

from __future__ import annotations

from typing import Any, Protocol

from attrs import define, field

# Removed RepositoryProvider import - was unused after refactor


# Protocols for dependency injection
class ConfigProvider(Protocol):
    """Supplies batch sizes and API rate limits from app configuration."""

    def get(self, key: str, default: Any = None) -> Any:
        """Gets configuration value by key.

        Args:
            key: Configuration key to look up.
            default: Value to return if key is not found.

        Returns:
            The configuration value or default if not found.
        """
        ...


class Logger(Protocol):
    """Records batch processing progress and errors for debugging."""

    def info(self, message: str, **kwargs: Any) -> None:
        """Log informational message."""
        ...

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug message."""
        ...

    def exception(self, message: str, **kwargs: Any) -> None:
        """Log error with exception details."""
        ...


@define(frozen=True, slots=True)
class BatchResult:
    """Success rates and detailed outcomes from processing music items in batches.

    Aggregates results across all batches to show total items processed,
    success/error counts, and processing statistics for user feedback.

    Attributes:
        total_items: Total number of items submitted for processing.
        processed_count: Number of items that were processed (success or failure).
        batch_results: Detailed results from each batch, containing status and data.
    """

    total_items: int
    processed_count: int
    batch_results: list[list[dict]] = field(factory=list)

    @property
    def success_count(self) -> int:
        """Items successfully imported, processed, or synced."""
        return (
            self.get_status_count("imported")
            + self.get_status_count("processed")
            + self.get_status_count("synced")
        )

    @property
    def error_count(self) -> int:
        """Items that failed due to API errors or data issues."""
        return self.get_status_count("error")

    @property
    def success_rate(self) -> float:
        """Success rate as percentage (0.0 to 100.0)."""
        if self.processed_count == 0:
            return 0.0
        return round((self.success_count / self.processed_count) * 100, 2)

    def get_status_count(self, status: str) -> int:
        """Counts items with specific status across all batches."""
        count = 0
        for batch in self.batch_results:
            for result in batch:
                if result.get("status") == status:
                    count += 1
        return count


# BatchProcessor classes have been separated by use case:
#
# DatabaseBatchProcessor: Database operations with transaction safety
# ImportBatchProcessor: File/import operations with memory management
# SimpleBatchProcessor: Basic chunking operations
