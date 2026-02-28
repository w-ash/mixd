"""Service connector configuration types.

This module defines standardized configuration for music service connectors,
ensuring consistent behavior across different implementations.
"""

from collections.abc import Callable
from typing import Any, TypedDict


class ConnectorConfig(TypedDict):
    """Type definition for connector configuration.

    A standardized configuration type for connectors to ensure consistent
    structure across all connector implementations.

    Attributes:
        factory: Factory function to create connector instance
        dependencies: Optional list of connector dependencies
        metrics: Optional mapping of metric names to connector metadata fields
    """

    # Required fields
    factory: Callable[[dict[str, Any]], Any]

    # Optional fields (marked using NotRequired)
    dependencies: list[str]
    metrics: dict[str, str]
