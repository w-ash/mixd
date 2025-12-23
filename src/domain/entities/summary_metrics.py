"""Self-describing summary metric domain types for operation results.

Provides composable summary metric objects that carry their own display metadata,
eliminating the need for UI layer to guess labels from generic field names.

These are distinct from:
- TrackMetric: Time-series metrics from external services (e.g., popularity over time)
- OperationResult.metrics: Per-track operational metrics (e.g., similarity scores per track)
"""

from __future__ import annotations

from typing import Literal

from attrs import define, field

SummaryMetricFormat = Literal["count", "percent", "duration"]


@define(frozen=True, slots=True)
class SummaryMetric:
    """Self-describing summary metric with display metadata.

    Encapsulates both the metric value and how it should be displayed,
    enabling generic UI rendering without hardcoded label logic.

    Attributes:
        name: Internal identifier for programmatic access (e.g., "imported_count")
        value: The metric value (count, rate, duration, etc.)
        label: Human-readable display label (e.g., "Likes Imported")
        format: Display format hint ("count", "percent", "duration")
        significance: Display ordering priority (lower = higher priority)
    """

    name: str
    value: int | float
    label: str
    format: SummaryMetricFormat = "count"
    significance: int = 0


@define(slots=True)
class SummaryMetricCollection:
    """Ordered collection of summary metrics with display sorting.

    Manages a collection of SummaryMetric objects and provides sorting by significance
    for consistent display ordering across all operations.
    """

    metrics: list[SummaryMetric] = field(factory=list)

    def add(
        self,
        name: str,
        value: float,
        label: str,
        format: SummaryMetricFormat = "count",
        significance: int = 0,
    ) -> None:
        """Add summary metric to collection with display hints.

        Args:
            name: Internal metric identifier
            value: Metric value
            label: Display label for UI
            format: Format hint for value rendering (default: "count")
            significance: Display order priority (default: 0, lower = higher priority)
        """
        self.metrics.append(
            SummaryMetric(
                name=name,
                value=value,
                label=label,
                format=format,
                significance=significance,
            )
        )

    def sorted(self) -> list[SummaryMetric]:
        """Get summary metrics sorted by significance (lower = higher display priority).

        Returns:
            Sorted list of summary metrics, stable-sorted by significance
        """
        return sorted(self.metrics, key=lambda m: m.significance)
