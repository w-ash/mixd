"""Metrics registry for connector metrics.

This module provides a central registry for connector metrics resolvers, enabling
consistent access to metrics across the application without introducing circular
dependencies. It implements a clean registration pattern that allows connectors
to publish their metric capabilities at runtime.

Key Components:
- MetricResolverProtocol: Interface that metric resolvers must implement
- register_metric_resolver: Function to register a resolver implementation
- get_metrics_for_connector: Function to look up metrics by connector name
- METRIC_FRESHNESS: Configuration for how frequently metrics should be updated
"""

from typing import Any, ClassVar, Protocol, runtime_checkable


@runtime_checkable
class MetricResolverProtocol(Protocol):
    """Protocol for metric resolver implementations.

    Defines the interface that all metric resolvers must implement to be
    registered with the metrics registry. Enforces a consistent pattern for
    resolving metrics across different connectors.

    Attributes:
        CONNECTOR: Class variable identifying the connector name
    """

    CONNECTOR: ClassVar[str]  # Changed from CONNECTOR: str to match implementation

    async def resolve(
        self, track_ids: list[int], metric_name: str, uow: Any
    ) -> dict[int, Any]:
        """Resolve metrics for tracks.

        Args:
            track_ids: List of internal track IDs to resolve metrics for
            metric_name: Name of the metric to resolve
            uow: UnitOfWork for database access

        Returns:
            Dictionary mapping track IDs to their metric values
        """
        ...


# Global registries for metrics
metric_resolvers: dict[str, MetricResolverProtocol] = {}
connector_metrics: dict[str, list[str]] = {
    "lastfm": [],
    "spotify": [],
}

# Metric freshness configuration is now centralized in metrics_config.py


def register_metric_resolver(
    metric_name: str, resolver: MetricResolverProtocol
) -> None:
    """Register a metric resolver implementation.

    Associates a metric name with a resolver implementation, allowing the
    metric to be resolved across the application.

    Args:
        metric_name: Name of the metric to register
        resolver: Implementation of MetricResolverProtocol that can resolve this metric
    """
    metric_resolvers[metric_name] = resolver

    # Also update connector metrics registry for reverse lookup
    if hasattr(resolver, "CONNECTOR") and resolver.CONNECTOR:
        connector = resolver.CONNECTOR
        if connector not in connector_metrics:
            connector_metrics[connector] = []
        if metric_name not in connector_metrics[connector]:
            connector_metrics[connector].append(metric_name)
