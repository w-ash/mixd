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
import time
from typing import TYPE_CHECKING, Any, ClassVar

from attrs import define
import backoff

from src.domain.entities.progress import (
    NullProgressEmitter,
    OperationStatus,
    ProgressEmitter,
    ProgressOperation,
    ProgressStatus,
    create_progress_event,
)

if TYPE_CHECKING:
    from src.domain.entities.playlist import ConnectorPlaylist
    from src.domain.entities.track import ConnectorTrack

from src.config import get_logger, settings
from src.infrastructure.connectors._shared.error_classification import (
    DefaultErrorClassifier,
    ErrorClassifierProtocol,
    create_backoff_handler,
    create_giveup_handler,
    should_giveup_on_error,
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
    def error_classifier(self) -> ErrorClassifierProtocol:
        """Get error classifier for this connector. Override for service-specific classification."""
        return DefaultErrorClassifier()

    async def process_tracks_concurrent(
        self,
        tracks,
        process_func,
        progress_emitter: ProgressEmitter | None = None,
    ):
        """Process tracks concurrently using proven asyncio pattern.

        Simple, fast concurrent implementation that all connectors inherit.
        Uses modern asyncio patterns for optimal performance with rate limiting.

        Args:
            tracks: List of tracks to process
            process_func: Async function that processes a single track
            progress_emitter: Progress emitter for tracking operation status

        Returns:
            List of results from processing each track
        """
        if progress_emitter is None:
            progress_emitter = NullProgressEmitter()

        import asyncio

        if not tracks:
            return []

        # Start progress tracking
        operation = ProgressOperation(
            description=f"Process {len(tracks)} tracks with {self.connector_name}",
            total_items=len(tracks),
        )
        operation_id = await progress_emitter.start_operation(operation)

        batch_start_time = time.time()
        logger.info(
            f"Processing {len(tracks)} tracks concurrently with {self.connector_name}",
            track_count=len(tracks),
            connector=self.connector_name,
            batch_start_time=batch_start_time,
        )

        # Create concurrent tasks using the proven pattern - LOG EACH TASK CREATION
        task_creation_start = time.time()
        tasks = []
        for idx, track in enumerate(tracks):
            task_creation_time = time.time()
            task = asyncio.create_task(process_func(track))
            tasks.append(task)

            logger.debug(
                f"Created task {idx + 1}/{len(tracks)} for {self.connector_name}",
                task_idx=idx + 1,
                total_tasks=len(tracks),
                track_id=getattr(track, "id", None),
                task_creation_time=task_creation_time,
                milliseconds_since_batch_start=round(
                    (task_creation_time - batch_start_time) * 1000, 1
                ),
            )

        task_creation_duration = time.time() - task_creation_start
        logger.info(
            f"Created {len(tasks)} concurrent tasks for {self.connector_name}",
            task_creation_duration_ms=round(task_creation_duration * 1000, 1),
            connector=self.connector_name,
        )

        # Process results as they complete (maintains rate limiting and progress tracking)
        results = []
        completed_count = 0
        await_loop_start = time.time()

        logger.info(
            f"Starting awaiting of {len(tasks)} tasks for {self.connector_name}",
            await_start_time=await_loop_start,
            milliseconds_since_batch_start=round(
                (await_loop_start - batch_start_time) * 1000, 1
            ),
        )

        for task in asyncio.as_completed(tasks):
            task_await_start = time.time()
            try:
                result = await task
                task_completion_time = time.time()
                task_duration = task_completion_time - task_await_start

                results.append(result)
                completed_count += 1

                logger.info(
                    f"Task {completed_count}/{len(tasks)} completed for {self.connector_name}",
                    task_completed=completed_count,
                    total_tasks=len(tasks),
                    task_await_duration_ms=round(task_duration * 1000, 1),
                    task_completion_time=task_completion_time,
                    milliseconds_since_batch_start=round(
                        (task_completion_time - batch_start_time) * 1000, 1
                    ),
                    milliseconds_since_await_start=round(
                        (task_completion_time - await_loop_start) * 1000, 1
                    ),
                    connector=self.connector_name,
                )

                # Emit progress event every 10 items
                if completed_count % 10 == 0:
                    await progress_emitter.emit_progress(
                        create_progress_event(
                            operation_id=operation_id,
                            current=completed_count,
                            total=len(tracks),
                            message=f"Processed {completed_count}/{len(tracks)} tracks with {self.connector_name}",
                            status=ProgressStatus.IN_PROGRESS,
                        )
                    )

            except Exception as e:
                task_completion_time = time.time()
                logger.error(
                    f"Track processing failed in {self.connector_name}",
                    error=str(e),
                    error_type=type(e).__name__,
                    task_completion_time=task_completion_time,
                    milliseconds_since_batch_start=round(
                        (task_completion_time - batch_start_time) * 1000, 1
                    ),
                )
                # Continue processing other tracks
                completed_count += 1
                results.append(None)

        successful_results = [r for r in results if r is not None]
        logger.info(
            f"Completed concurrent processing: {len(successful_results)}/{len(tracks)} successful",
            connector=self.connector_name,
            success_count=len(successful_results),
            total_count=len(tracks),
        )

        # Complete progress tracking
        final_status = (
            OperationStatus.COMPLETED
            if len(successful_results) == len(tracks)
            else OperationStatus.FAILED
        )
        await progress_emitter.complete_operation(operation_id, final_status)

        return results

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

    def create_service_aware_retry(
        self, backoff_strategy=backoff.expo, **backoff_kwargs
    ):
        """Create a retry decorator that uses this connector's error classifier.

        Args:
            backoff_strategy: Backoff strategy function (backoff.expo, backoff.constant, etc.)
            **backoff_kwargs: Additional arguments passed to backoff decorator

        Returns:
            Configured backoff decorator with service-specific error handling

        Example:
            @self.create_service_aware_retry(max_tries=3, base=1.0, max_value=30.0)
            async def api_method(self):
                # Method will use service-specific error classification
                pass
        """
        # Set up default backoff parameters using connector config
        defaults = {
            "max_tries": int(self.get_connector_config("RETRY_COUNT") or 3) + 1,
            "jitter": backoff.full_jitter,
        }

        # Add strategy-specific defaults
        if backoff_strategy == backoff.expo:
            defaults.update({
                "base": float(self.get_connector_config("RETRY_BASE_DELAY") or 1.0),
                "max_value": float(
                    self.get_connector_config("RETRY_MAX_DELAY") or 30.0
                ),
            })
        elif backoff_strategy == backoff.constant:
            # For constant backoff, use interval instead of base/max_value
            defaults.update({
                "interval": float(self.get_connector_config("RETRY_BASE_DELAY") or 1.0),
            })

        # Merge with provided kwargs, giving precedence to explicit values
        backoff_config = {**defaults, **backoff_kwargs}

        # Create service-specific handlers (these work for all strategies)
        backoff_config["giveup"] = should_giveup_on_error(self.error_classifier)
        backoff_config["on_backoff"] = create_backoff_handler(
            self.error_classifier, self.connector_name
        )
        backoff_config["on_giveup"] = create_giveup_handler(
            self.error_classifier, self.connector_name
        )

        # Return configured decorator
        return backoff.on_exception(backoff_strategy, Exception, **backoff_config)

    async def get_playlist(self, playlist_id: str) -> "ConnectorPlaylist":
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
    def convert_track_to_connector(self, track_data: dict) -> "ConnectorTrack":
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
