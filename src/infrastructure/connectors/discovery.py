"""Dynamic connector discovery and registration.

Scans the connectors package for modules implementing the `get_connector_config()`
interface and builds a cached registry of available connectors.
"""

from collections.abc import Callable
import importlib
import pkgutil
import sys
from typing import cast

from src.config import get_logger
from src.infrastructure.connectors._shared.metric_registry import register_metrics
from src.infrastructure.connectors.protocols import ConnectorConfig

logger = get_logger(__name__)

# Lazy-initialized connector registry cache (initialized on first access)
_connectors_cache: dict[str, ConnectorConfig] | None = None


def _load_connector_config(name: str) -> ConnectorConfig | None:
    """Import a connector module and return its config, or None if it has none.

    An ``ImportError`` propagates: every connector is first-party, so a module
    that cannot import is a bug. Swallowing it would cache a reduced registry
    for the process lifetime and serve required connector pickers empty.
    """
    connector_module = importlib.import_module(name)
    if not hasattr(connector_module, "get_connector_config"):
        return None
    get_config = cast(
        "Callable[[], ConnectorConfig]",
        connector_module.get_connector_config,
    )
    return get_config()


def discover_connectors() -> dict[str, ConnectorConfig]:
    """Discover connector configs and register their metric declarations.

    Loads every package exposing ``get_connector_config()`` — the extension
    point for new connectors. Discovery is the single point where connector
    metrics reach the registry (idempotent), keyed by connector name.
    """
    global _connectors_cache

    # Return cached registry if already populated
    if _connectors_cache is not None:
        return _connectors_cache

    discovered: dict[str, ConnectorConfig] = {}

    # Get the connectors package for introspection
    connectors_package = sys.modules["src.infrastructure.connectors"]
    package_path = connectors_package.__name__

    # Use pkgutil to find all modules and subpackages in the package
    for _, name, _ispkg in pkgutil.iter_modules(
        connectors_package.__path__,
        prefix=f"{package_path}.",
    ):
        module_name = name.split(".")[-1]

        # Skip the __init__ module itself and _shared utilities
        if module_name == "__init__" or module_name.startswith("_"):
            continue

        config = _load_connector_config(name)
        if config is not None:
            discovered[module_name] = config
            logger.debug(f"Registered connector: {module_name}")

    logger.info(
        f"Discovered {len(discovered)} connectors: {', '.join(discovered.keys())}",
    )

    # Register each connector's declared metrics with the metric registry
    for connector_name, config in discovered.items():
        register_metrics(
            connector_name,
            config["metrics"],
            config.get("metric_freshness_hours"),
        )

    # Cache the results for subsequent calls
    _connectors_cache = discovered
    return _connectors_cache
