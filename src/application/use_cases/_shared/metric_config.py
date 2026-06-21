"""The metric-registry access contract, shared across the application layer.

A leaf protocol (no workflow or infrastructure dependencies) so any use case or
service can depend on metric access without importing infrastructure or the
workflow-specific protocol bundle — and without the import cycle that bundle
forced. The concrete implementation is ``MetricConfigProviderImpl`` in
``infrastructure/connectors/_shared/metric_registry.py``.
"""

from typing import Protocol


class MetricConfigProvider(Protocol):
    """Abstracts metric registry access so application never imports infrastructure."""

    def get_connector_metrics(self, connector: str) -> list[str]:
        """Return metric names supported by a connector."""
        ...

    def get_field_name(self, metric: str) -> str:
        """Map metric name to the connector field name."""
        ...

    def get_metric_freshness(self, metric: str) -> float:
        """Return freshness period in hours for a metric."""
        ...

    def get_all_connectors_metrics(self) -> dict[str, list[str]]:
        """Return all registered connectors and their metric names."""
        ...

    def get_all_field_mappings(self) -> dict[str, str]:
        """Return mapping of all metric names to their field names."""
        ...
