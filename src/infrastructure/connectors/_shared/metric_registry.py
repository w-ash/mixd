"""Dynamic registry for connector metrics.

Holds the process-wide mapping of connectors to their metrics, metric-to-field
mappings, human-readable labels and descriptions, and per-metric freshness.
Connector discovery
(``src.infrastructure.connectors.discovery.discover_connectors``) registers
each connector's ``MetricSpec`` declarations from its
``ConnectorConfig["metrics"]`` entry; this module holds no service-specific
configuration of its own.
"""

from collections.abc import Mapping

from src.infrastructure.connectors.protocols import MetricSpec

# ============================================================================
# DYNAMIC METRIC REGISTRIES
# ============================================================================

# Dynamic registries populated by connector discovery
_connector_metrics: dict[str, list[str]] = {}
_field_mappings: dict[str, str] = {}
_metric_freshness: dict[str, float] = {}
_metric_labels: dict[str, str] = {}
_metric_descriptions: dict[str, str] = {}

# Default freshness period in hours
DEFAULT_METRIC_FRESHNESS = 24.0

# ============================================================================
# CONFIGURATION ACCESS FUNCTIONS
# ============================================================================


def get_metric_freshness(metric_name: str) -> float:
    """Get freshness period for a metric in hours.

    Args:
        metric_name: Name of the metric to get freshness for

    Returns:
        Number of hours after which the metric should be considered stale
    """
    return _metric_freshness.get(metric_name, DEFAULT_METRIC_FRESHNESS)


def get_field_name(metric_name: str) -> str:
    """Get the connector field name for a given metric.

    Args:
        metric_name: Name of the metric to get field name for

    Returns:
        Field name in the connector's API response structure
    """
    return _field_mappings.get(metric_name, metric_name)


def get_connector_metrics(connector_name: str) -> list[str]:
    """Get list of metrics supported by a connector.

    Args:
        connector_name: Name of the connector

    Returns:
        List of metric names supported by the connector
    """
    return _connector_metrics.get(connector_name, [])


def get_metric_label(metric_name: str) -> str:
    """Get the human-readable label for a metric.

    Args:
        metric_name: Name of the metric to get the label for

    Returns:
        Declared label, or the metric name when none was declared
    """
    return _metric_labels.get(metric_name, metric_name)


def get_metric_description(metric_name: str) -> str:
    """Get the human-readable description for a metric.

    Args:
        metric_name: Name of the metric to get the description for

    Returns:
        Declared description, or an empty string when none was declared
    """
    return _metric_descriptions.get(metric_name, "")


# ============================================================================
# REGISTRATION
# ============================================================================


def register_metrics(
    connector: str,
    specs: Mapping[str, MetricSpec],
    freshness_hours: float | None = None,
) -> None:
    """Register a connector's metrics: names, field mappings, labels, freshness.

    Idempotent — re-registration with the same values is a no-op.

    Args:
        connector: Connector name the metrics belong to (e.g. "lastfm")
        specs: Maps metric names to their declared ``MetricSpec``
        freshness_hours: Staleness threshold applied to every metric in
            ``specs``; ``None`` keeps ``DEFAULT_METRIC_FRESHNESS``
    """
    for metric_name, spec in specs.items():
        metrics = _connector_metrics.setdefault(connector, [])
        if metric_name not in metrics:
            metrics.append(metric_name)
        _field_mappings[metric_name] = spec.field
        _metric_labels[metric_name] = spec.label
        _metric_descriptions[metric_name] = spec.description
        if freshness_hours is not None:
            _metric_freshness[metric_name] = freshness_hours


def get_all_connectors_metrics() -> dict[str, list[str]]:
    """Get mapping of all registered connectors to their metrics.

    Returns:
        Dict mapping connector names to lists of their metric names
    """
    return dict(_connector_metrics)


def get_all_field_mappings() -> dict[str, str]:
    """Get mapping of all metric names to their field names.

    Returns:
        Dict mapping metric names to connector field names
    """
    return dict(_field_mappings)


class MetricConfigProviderImpl:
    """Concrete implementation of MetricConfigProvider protocol.

    Wraps the module-level registry functions so that application code
    can receive this via dependency injection instead of importing the
    infrastructure functions directly. Construction runs connector
    discovery (cached after the first call), which populates the
    registries — a provider never reads before registration.
    """

    def __init__(self) -> None:
        from src.infrastructure.connectors.discovery import discover_connectors

        discover_connectors()

    def get_connector_metrics(self, connector: str) -> list[str]:
        return get_connector_metrics(connector)

    def get_field_name(self, metric: str) -> str:
        return get_field_name(metric)

    def get_metric_freshness(self, metric: str) -> float:
        return get_metric_freshness(metric)

    def get_all_connectors_metrics(self) -> dict[str, list[str]]:
        return get_all_connectors_metrics()

    def get_all_field_mappings(self) -> dict[str, str]:
        return get_all_field_mappings()

    def get_metric_label(self, metric: str) -> str:
        return get_metric_label(metric)

    def get_metric_description(self, metric: str) -> str:
        return get_metric_description(metric)
