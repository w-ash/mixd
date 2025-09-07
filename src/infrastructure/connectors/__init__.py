"""Service connectors for external music platforms and APIs.

Configures asyncio infrastructure for optimal I/O performance with external APIs.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib
import pkgutil
import sys

from src.config import get_logger, settings

# Import main connector classes for re-export
from src.infrastructure.connectors.lastfm import (
    LastFMConnector,
    LastFmMetricResolver,
    LastFMTrackInfo,
)
from src.infrastructure.connectors.musicbrainz import MusicBrainzConnector
from src.infrastructure.connectors.protocols import ConnectorConfig
from src.infrastructure.connectors.spotify import (
    SpotifyConnector,
    convert_spotify_playlist_to_connector,
    convert_spotify_track_to_connector,
)

logger = get_logger(__name__)


class _NaradaEventLoopPolicy(asyncio.DefaultEventLoopPolicy):
    """Custom event loop policy that configures optimal I/O executors for connector operations."""

    def new_event_loop(self):
        """Create a new event loop with properly configured default executor."""
        loop = super().new_event_loop()

        # Configure default executor for I/O-heavy operations
        required_max_workers = settings.api.lastfm_concurrency
        executor = ThreadPoolExecutor(
            max_workers=required_max_workers, thread_name_prefix="narada_io"
        )
        loop.set_default_executor(executor)

        logger.info(
            "Configured new event loop with I/O executor",
            max_workers=required_max_workers,
            thread_name_prefix="narada_io",
            executor_id=id(executor),
        )

        return loop


# Configure I/O infrastructure globally using event loop policy
# This ensures all event loops created by asyncio.run() have proper executor configuration
asyncio.set_event_loop_policy(_NaradaEventLoopPolicy())
logger.info("Configured global asyncio event loop policy for I/O concurrency")

# Connector registry cache
_connectors: dict[str, ConnectorConfig] = {}


def discover_connectors() -> dict[str, ConnectorConfig]:
    """Discover and register connector configurations.

    Dynamically loads connector modules from the integrations package
    that implement the `get_connector_config()` interface. This creates a
    clean extension point for new connectors without factory code changes.

    Returns:
        dict[str, ConnectorConfig]: Dictionary mapping connector names to their configurations
    """
    global _connectors, _CONNECTORS_CACHE, CONNECTORS

    # Return cached registry if already populated
    if _CONNECTORS_CACHE is not None:
        return _CONNECTORS_CACHE

    # Clear internal cache for fresh discovery
    _connectors = {}

    # Get our own module for introspection
    module = sys.modules[__name__]
    package_path = module.__name__

    # Use pkgutil to find all modules and subpackages in the package
    for _, name, _ispkg in pkgutil.iter_modules(
        module.__path__,
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
    _CONNECTORS_CACHE = _connectors.copy()  # pyright: ignore[reportConstantRedefinition]
    CONNECTORS = _CONNECTORS_CACHE  # Legacy compatibility  # pyright: ignore[reportConstantRedefinition]
    return _CONNECTORS_CACHE


# Lazy-initialized connector registry (initialized on first access)
_CONNECTORS_CACHE: dict[str, ConnectorConfig] | None = None

# Legacy export - initialized by discover_connectors()
CONNECTORS: dict[str, ConnectorConfig] | None = None


# Define public API with explicit exports
__all__ = [
    "CONNECTORS",
    "LastFMConnector",
    "LastFMTrackInfo",
    "LastFmMetricResolver",
    "MusicBrainzConnector",
    "SpotifyConnector",
    "convert_spotify_playlist_to_connector",
    "convert_spotify_track_to_connector",
    "discover_connectors",
]
