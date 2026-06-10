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
from src.infrastructure.connectors.protocols import ConnectorConfig

logger = get_logger(__name__)

# Internal connector registry
_connectors: dict[str, ConnectorConfig] = {}

# Lazy-initialized connector registry cache (initialized on first access)
_connectors_cache: dict[str, ConnectorConfig] | None = None


def _load_connector_config(name: str) -> ConnectorConfig | None:
    """Import a connector module and return its config, or None if it has none.

    Holds the import + config resolution so the caller's ``try``/``except
    ImportError`` stays narrow while still covering every statement that can
    raise ``ImportError`` — both ``import_module`` and ``get_config()`` (which
    triggers a connector's deferred optional-dependency imports).
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
            config = _load_connector_config(name)
        except ImportError as e:
            logger.warning(f"Could not import connector module {module_name}: {e}")
        else:
            # Non-fallible registration — kept out of the try so the ImportError
            # guard covers only the import/config resolution, as before.
            if config is not None:
                _connectors[module_name] = config
                logger.debug(f"Registered connector: {module_name}")

    logger.info(
        f"Discovered {len(_connectors)} connectors: {', '.join(_connectors.keys())}",
    )

    # Cache the results for subsequent calls
    _connectors_cache = _connectors.copy()
    return _connectors_cache
