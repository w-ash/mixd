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

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from attrs import define

from src.config import get_logger, settings
from src.domain.entities.playlist import ConnectorPlaylist
from src.domain.entities.track import ConnectorTrack
from src.infrastructure.connectors._shared.error_classification import (
    ErrorClassifier,
    classify_unknown_error,
)
from src.infrastructure.connectors._shared.metrics import (
    MetricResolverProtocol,
    register_metric_resolver,
)

# Get contextual logger
logger = get_logger(__name__).bind(service="connectors")


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
        uow: Any,  # UnitOfWorkProtocol - avoiding import for infrastructure layer
    ) -> dict[int, Any]:
        """Retrieve metric values for multiple tracks from database.

        Args:
            track_ids: Internal track IDs to get metrics for
            metric_name: Name of metric to retrieve (e.g., "spotify_popularity")
            uow: Database unit of work for transaction management

        Returns:
            Track ID to metric value mapping
        """
        # Import at runtime to avoid circular dependencies
        from src.application.services.metrics_application_service import (
            MetricsApplicationService,
        )

        metrics_service = MetricsApplicationService()
        return await metrics_service.resolve_metrics(
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

    def get_connector_config(self, key: str, default=None):
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
    def convert_track_to_connector(self, track_data: dict) -> ConnectorTrack:
        """Convert service-specific track data to ConnectorTrack domain model.

        Each connector must implement this method to handle conversion from their
        service's API response format to the standardized ConnectorTrack domain model.

        Args:
            track_data: Raw track data from the service's API

        Returns:
            ConnectorTrack with standardized fields and service-specific metadata

        Example:
            # In SpotifyConnector
            def convert_track_to_connector(self, track_data: dict) -> ConnectorTrack:
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
) -> None:
    """Register metric resolver for all metrics defined in field_map.

    Connects metric names to resolver instances so the application layer can
    look up track metrics like popularity or play counts.

    Args:
        metric_resolver: Resolver instance that can fetch metric values
        field_map: Maps metric names to connector metadata field names
    """
    from src.infrastructure.connectors._shared.metrics import register_metric_config

    for metric_name, field_name in field_map.items():
        register_metric_resolver(metric_name, metric_resolver)
        # Register the field mapping so get_field_name() works correctly
        register_metric_config(metric_name, field_name)
