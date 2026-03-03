"""Dynamic connector discovery and registration.

Scans the connectors package for modules implementing the `get_connector_config()`
interface and builds a cached registry of available connectors.
"""

# pyright: reportAny=false

import importlib
import pkgutil
import sys

from src.config import get_logger
from src.infrastructure.connectors.protocols import ConnectorConfig

logger = get_logger(__name__)

# Internal connector registry
_connectors: dict[str, ConnectorConfig] = {}

# Lazy-initialized connector registry cache (initialized on first access)
_connectors_cache: dict[str, ConnectorConfig] | None = None


def discover_connectors() -> dict[str, ConnectorConfig]:
    """Discover and register connector configurations.

    Dynamically loads connector modules from the integrations package
    that implement the `get_connector_config()` interface. This creates a
    clean extension point for new connectors without factory code changes.

    Returns:
        dict[str, ConnectorConfig]: Dictionary mapping connector names to their configurations
    """
    global _connectors, _connectors_cache

    # Return cached registry if already populated
    if _connectors_cache is not None:
        return _connectors_cache

    # Clear internal cache for fresh discovery
    _connectors = {}

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

        try:
            # Import the module/subpackage
            connector_module = importlib.import_module(name)

            # Check if module implements connector interface
            if hasattr(connector_module, "get_connector_config"):
                # Register the connector by name
                config = connector_module.get_connector_config()
                _connectors[module_name] = config
                logger.debug(f"Registered connector: {module_name}")
        except ImportError as e:
            logger.warning(f"Could not import connector module {module_name}: {e}")

    logger.info(
        f"Discovered {len(_connectors)} connectors: {', '.join(_connectors.keys())}",
    )

    # Cache the results for subsequent calls
    _connectors_cache = _connectors.copy()
    return _connectors_cache
