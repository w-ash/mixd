"""Base classes for music service API connectors.

Provides shared functionality for integrating with external music services like Spotify,
Last.fm, MusicBrainz, etc. Child connectors inherit from these base classes to get
standardized configuration loading and metric resolution.

Classes:
    BaseMetricResolver: Retrieves track metrics (popularity, play counts) from connector metadata
    BaseAPIConnector: Abstract base for service-specific API clients (inherit for Spotify, Last.fm)

Example:
    ```python
    class SpotifyConnector(BaseAPIConnector):
        @property
        def connector_name(self) -> str:
            return "spotify"

        # Implement service-specific methods...
    ```
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: _api_call variadic dispatch, generic API responses

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, Self

from attrs import define, field
from loguru import logger as _loguru_logger
from tenacity import AsyncRetrying

from src.config import get_logger, settings
from src.domain.entities.playlist import ConnectorPlaylist
from src.domain.entities.track import ConnectorTrack
from src.domain.repositories.interfaces import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.error_classifier import (
    ErrorClassifier,
    classify_unknown_error,
)
from src.infrastructure.connectors._shared.metric_registry import (
    MetricResolveFn,
    MetricResolverProtocol,
    register_metric_config,
    register_metric_resolver,
)

# Get contextual logger
logger = get_logger(__name__).bind(service="connectors")


@define(slots=True)
class BaseAPIClient:
    """Shared base for API clients with retry, context propagation, and error suppression.

    Subclasses set _SUPPRESS_ERRORS and initialize _retry_policy in __attrs_post_init__.
    The _api_call() helper replaces @resilient_operation + the _with_retries layer.
    """

    _SUPPRESS_ERRORS: ClassVar[tuple[type[BaseException], ...]] = ()
    _retry_policy: AsyncRetrying = field(init=False, repr=False)

    async def _api_call[T](
        self,
        operation: str,
        impl: Callable[..., Awaitable[T]],
        *args: Any,
    ) -> T | None:
        """Execute API call with retry policy, context propagation, and error suppression.

        Operation name propagates via loguru contextvars into ALL nested log calls
        (httpx hooks, tenacity callbacks, _impl methods).
        """
        with _loguru_logger.contextualize(operation=operation):
            try:
                return await self._retry_policy(impl, *args)
            except Exception as exc:
                if isinstance(exc, self._SUPPRESS_ERRORS):
                    return None
                raise

    async def aclose(self) -> None:
        """Close underlying resources. Override for clients with connection pools."""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


@define(frozen=True, slots=True)
class BaseMetricResolver:
    """Retrieves track metrics from connector metadata stored in database.

    Looks up track metrics like Spotify popularity or Last.fm play counts by querying
    the connector_metadata table. Child classes define which metadata fields map to
    which metric names via FIELD_MAP and CONNECTOR class variables.

    Attributes:
        FIELD_MAP: Maps metric names to connector metadata field names
        CONNECTOR: Service identifier (e.g., "spotify", "lastfm")
    """

    # To be defined by subclasses - maps metric names to connector metadata fields
    FIELD_MAP: ClassVar[dict[str, str]] = {}

    # Connector name to be overridden by subclasses
    CONNECTOR: ClassVar[str] = ""

    async def resolve(
        self,
        track_ids: list[int],
        metric_name: str,
        uow: UnitOfWorkProtocol,
        resolve_fn: MetricResolveFn,
    ) -> dict[int, Any]:
        """Retrieve metric values for multiple tracks from database.

        Uses a callback injected by the application layer to perform the actual
        metric resolution, avoiding a circular import from infrastructure to
        application.

        Args:
            track_ids: Internal track IDs to get metrics for
            metric_name: Name of metric to retrieve (e.g., "spotify_popularity")
            uow: Database unit of work for transaction management
            resolve_fn: Application-layer callback that handles cache lookup,
                API fetching, and persistence of metric values.

        Returns:
            Track ID to metric value mapping
        """
        return await resolve_fn(
            track_ids=track_ids,
            metric_name=metric_name,
            connector=self.CONNECTOR,
            field_map=self.FIELD_MAP,
            uow=uow,
        )


@define(slots=True)
class BaseAPIConnector(ABC):
    """Abstract base for music service API clients.

    Inherit from this class to create connectors for specific services like Spotify, Last.fm,
    MusicBrainz, etc. Provides common configuration loading, batch processing setup, and
    delegation patterns for playlist/track operations.

    Child classes must implement:
        - connector_name property (returns service name like "spotify")
        - Service-specific API methods as needed

    Automatically provides:
        - Configuration loading with service-specific prefixes
        - Pre-configured batch processor with retry logic
        - Generic playlist/track conversion that delegates to service methods
    """

    @property
    @abstractmethod
    def connector_name(self) -> str:
        """Service identifier for this connector (e.g., 'spotify', 'lastfm')."""

    @property
    def error_classifier(self) -> ErrorClassifier:
        """Get error classifier for this connector. Override for service-specific classification."""
        return _DefaultClassifier()

    def get_connector_config(self, key: str, default: object = None) -> Any:
        """Load configuration value from modern settings structure.

        Args:
            key: Configuration key without service prefix
            default: Fallback value if setting not found

        Returns:
            Configuration value from settings.api

        Example:
            If connector_name="spotify" and key="BATCH_SIZE":
            Returns settings.api.spotify_batch_size
        """
        # Map common keys to modern settings structure
        key_mapping = {
            "BATCH_SIZE": "batch_size",
            "CONCURRENCY": "concurrency",
            "RETRY_COUNT": "retry_count",
            "RETRY_BASE_DELAY": "retry_base_delay",
            "RETRY_MAX_DELAY": "retry_max_delay",
            "REQUEST_DELAY": "request_delay",
            "RAPID_TASK_CREATION": "rapid_task_creation",
        }

        # Convert key to modern format
        modern_key = key_mapping.get(key, key.lower())
        setting_name = f"{self.connector_name.lower()}_{modern_key}"

        # Get value from modern settings
        return getattr(settings.api, setting_name, default)

    async def get_playlist(self, playlist_id: str) -> ConnectorPlaylist:
        """Fetch playlist from service by delegating to service-specific method.

        Automatically calls the appropriate method based on connector_name:
        - spotify connector -> calls get_spotify_playlist()
        - lastfm connector -> calls get_lastfm_playlist()
        - etc.

        Args:
            playlist_id: Service-specific playlist identifier

        Returns:
            Playlist with tracks converted to standard format

        Raises:
            NotImplementedError: If service doesn't support playlists
        """
        # Try connector-specific method first (more efficient)
        method_name = f"get_{self.connector_name}_playlist"
        if hasattr(self, method_name):
            method = getattr(self, method_name)
            return await method(playlist_id)

        # Fallback: connector doesn't support playlists
        raise NotImplementedError(
            f"Playlist operations not supported by {self.connector_name} connector"
        )

    @abstractmethod
    def convert_track_to_connector(self, track_data: dict[str, Any]) -> ConnectorTrack:
        """Convert service-specific track data to ConnectorTrack domain model.

        Each connector must implement this method to handle conversion from their
        service's API response format to the standardized ConnectorTrack domain model.

        Args:
            track_data: Raw track data from the service's API

        Returns:
            ConnectorTrack with standardized fields and service-specific metadata

        Example:
            # In SpotifyConnector
            def convert_track_to_connector(self, track_data: dict[str, Any]) -> ConnectorTrack:
                from .conversions import convert_spotify_track_to_connector
                return convert_spotify_track_to_connector(track_data)
        """


class _DefaultClassifier:
    """Fallback error classifier for connectors without a custom one."""

    def classify_error(self, exception: Exception) -> tuple[str, str, str]:
        return classify_unknown_error(exception)


def register_metrics(
    metric_resolver: MetricResolverProtocol,
    field_map: dict[str, str],
    freshness_map: dict[str, float] | None = None,
) -> None:
    """Register metric resolver for all metrics defined in field_map.

    Connects metric names to resolver instances so the application layer can
    look up track metrics like popularity or play counts.

    Args:
        metric_resolver: Resolver instance that can fetch metric values
        field_map: Maps metric names to connector metadata field names
        freshness_map: Optional per-metric freshness hours (from settings)
    """
    for metric_name, field_name in field_map.items():
        register_metric_resolver(metric_name, metric_resolver)
        freshness_hours = freshness_map.get(metric_name) if freshness_map else None
        register_metric_config(metric_name, field_name, freshness_hours)
