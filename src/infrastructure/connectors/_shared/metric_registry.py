"""Dynamic metrics system for connector metrics.

This module provides a fully dynamic approach to connector metrics registration,
supporting the modular connector architecture where each service registers its own metrics.

Key Components:
- MetricResolverProtocol: Interface for metric resolver implementations
- Dynamic registration functions for runtime metric configuration
- Helper functions for metric configuration access
- No hardcoded service-specific configuration

Each connector registers its own metrics, field mappings, and freshness policies
through the registration functions. This eliminates static dependencies between
shared utilities and specific service implementations.
"""

from typing import ClassVar, Protocol, runtime_checkable

from src.config import get_logger
from src.domain.entities.shared import MetricValue
from src.domain.repositories.interfaces import UnitOfWorkProtocol

logger = get_logger(__name__).bind(service="connectors")

# ============================================================================
# DYNAMIC METRIC REGISTRIES
# ============================================================================

# Dynamic registries populated by connectors at runtime
_connector_metrics: dict[str, list[str]] = {}
_field_mappings: dict[str, str] = {}
_metric_freshness: dict[str, float] = {}

# Default freshness period in hours
DEFAULT_METRIC_FRESHNESS = 24.0

# ============================================================================
# DYNAMIC REGISTRATION SYSTEM
# ============================================================================


class MetricResolveFn(Protocol):
    """Typed callback for metric resolution, injected by the application layer.

    Replaces the previous ``Callable[..., Awaitable[dict[int, Any]]]`` — callers
    now see exact parameter names and types.
    """

    async def __call__(
        self,
        *,
        track_ids: list[int],
        metric_name: str,
        connector: str,
        field_map: dict[str, str],
        uow: UnitOfWorkProtocol,
    ) -> dict[int, MetricValue]: ...


@runtime_checkable
class MetricResolverProtocol(Protocol):
    """Protocol for metric resolver implementations.

    Defines the interface that all metric resolvers must implement to be
    registered with the metrics registry. Enforces a consistent pattern for
    resolving metrics across different connectors.

    Attributes:
        CONNECTOR: Class variable identifying the connector name
    """

    CONNECTOR: ClassVar[str]

    async def resolve(
        self,
        track_ids: list[int],
        metric_name: str,
        uow: UnitOfWorkProtocol,
        resolve_fn: MetricResolveFn,
    ) -> dict[int, MetricValue]:
        """Resolve metrics for tracks.

        Args:
            track_ids: List of internal track IDs to resolve metrics for
            metric_name: Name of the metric to resolve
            uow: UnitOfWork for database access
            resolve_fn: Callback provided by the application layer to perform
                the actual metric resolution (cache lookup, API fetch, persistence).

        Returns:
            Dictionary mapping track IDs to their metric values
        """
        ...


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


# ============================================================================
# REGISTRATION FUNCTIONS
# ============================================================================


def register_metric_resolver(
    metric_name: str, resolver: MetricResolverProtocol
) -> None:
    """Register a metric resolver and update the connector→metrics index.

    Args:
        metric_name: Name of the metric to register
        resolver: Implementation of MetricResolverProtocol that can resolve this metric
    """
    if hasattr(resolver, "CONNECTOR") and resolver.CONNECTOR:
        connector = resolver.CONNECTOR
        if connector not in _connector_metrics:
            _connector_metrics[connector] = []
        if metric_name not in _connector_metrics[connector]:
            _connector_metrics[connector].append(metric_name)


def register_metric_config(
    metric_name: str,
    field_name: str | None = None,
    freshness_hours: float | None = None,
) -> None:
    """Register metric configuration including field mapping and freshness.

    Args:
        metric_name: Name of the metric to configure
        field_name: API field name for this metric (defaults to metric_name)
        freshness_hours: Hours after which metric is stale (defaults to 24.0)
    """
    if field_name is not None:
        _field_mappings[metric_name] = field_name

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
    infrastructure functions directly.
    """

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
