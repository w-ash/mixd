"""Base classes for music service API connectors.

Provides shared functionality for integrating with external music services like Spotify,
Last.fm, MusicBrainz, etc. Child connectors inherit from these base classes to get
standardized configuration loading and metric resolution.

Classes:
    BaseMetricResolver: Retrieves track metrics (play counts, explicit flags) from connector metadata
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
from collections.abc import Awaitable, Callable, Mapping
import time
from typing import ClassVar, Self

from attrs import define, field
from tenacity import AsyncRetrying

from src.config import get_logger
from src.config.logging import logging_context
from src.config.telemetry import record_api_call
from src.domain.entities.playlist import ConnectorPlaylist
from src.domain.entities.shared import JsonValue, MetricValue
from src.domain.entities.track import ConnectorTrack
from src.domain.repositories.uow import UnitOfWorkProtocol
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
from src.infrastructure.connectors._shared.rate_limiting import (
    ConnectorRateLimiter,
    get_connector_rate_limiter,
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

    @property
    def service_name(self) -> str:
        """Settings key for this client's service, e.g. ``"spotify"``."""
        return service_name_for_client(type(self))

    async def _api_call[T](
        self,
        operation: str,
        impl: Callable[..., Awaitable[T]],
        *args: object,
        suppress: tuple[type[BaseException], ...] | None = None,
    ) -> T | None:
        """Execute API call with rate limiting, retry policy, context, and suppression.

        Operation name propagates via structlog contextvars into ALL nested log calls
        (httpx2 hooks, tenacity callbacks, _impl methods).

        Pacing wraps ``impl`` rather than the whole retry loop, so tenacity takes a
        token per attempt — retries are paced, not exempt from pacing. Services
        without a configured ``rate_limit`` get the unwrapped ``impl``.

        ``suppress`` overrides the class-level ``_SUPPRESS_ERRORS`` for this one
        call; ``None`` keeps the class default. Pass ``()`` when the caller must
        see retry-exhausted transport failures instead of a ``None`` that is
        indistinguishable from "the API answered with nothing" — the Last.fm
        enrichment reads depend on that distinction (v0.10.2.9 F5).

        Recorded time covers pacing waits and every retry attempt.
        """
        limiter = get_connector_rate_limiter(self.service_name)
        attempt = impl if limiter is None else _paced(limiter, impl)
        suppressed_types = self._SUPPRESS_ERRORS if suppress is None else suppress
        started = time.perf_counter_ns()
        with logging_context(operation=operation):
            try:
                return await self._retry_policy(attempt, *args)
            except Exception as exc:
                if isinstance(exc, suppressed_types):
                    return None
                raise
            finally:
                record_api_call(time.perf_counter_ns() - started)

    async def aclose(self) -> None:
        """Close underlying resources. Override for clients with connection pools."""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


def service_name_for_client(client_class: type[BaseAPIClient]) -> str:
    """Derive a client's service key from its connector package.

    ``src.infrastructure.connectors.spotify.client`` → ``"spotify"``. The
    connector package name is already the service identity everywhere else
    (``settings.api.spotify``, ``RetryConfig.service_name``), so no per-client
    declaration is needed. A package renamed out of step with the settings
    field silently drops that service's pacing — the mapping for every live
    client is pinned in
    ``tests/unit/infrastructure/connectors/test_base_api_call_rate_limit.py``.
    """
    package, _, _module = client_class.__module__.rpartition(".")
    return package.rpartition(".")[2]


def _paced[T](
    limiter: ConnectorRateLimiter, impl: Callable[..., Awaitable[T]]
) -> Callable[..., Awaitable[T]]:
    """Wrap an API implementation so every invocation first takes a token."""

    async def paced_impl(*args: object) -> T:
        await limiter.acquire()
        return await impl(*args)

    return paced_impl


@define(frozen=True, slots=True)
class BaseMetricResolver:
    """Retrieves track metrics from connector metadata stored in database.

    Looks up track metrics like Last.fm play counts by querying
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
    ) -> dict[int, MetricValue]:
        """Retrieve metric values for multiple tracks from database.

        Uses a callback injected by the application layer to perform the actual
        metric resolution, avoiding a circular import from infrastructure to
        application.

        Args:
            track_ids: Internal track IDs to get metrics for
            metric_name: Name of metric to retrieve (e.g., "lastfm_global_playcount")
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
        """Error classifier for this connector. Override for service-specific classification."""
        return _DefaultClassifier()

    async def get_playlist(
        self,
        playlist_id: str,
        *,
        on_page: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> ConnectorPlaylist:
        """Fetch playlist from service.

        Playlist-capable connectors (e.g. Spotify) override this method to
        implement the fetch. Connectors without playlist support inherit this
        default, which raises ``NotImplementedError``.

        Args:
            playlist_id: Service-specific playlist identifier
            on_page: Optional per-page pagination progress callback
                ``(fetched_so_far, total)``. Concrete subclasses decide
                whether to forward it — services that paginate (Spotify)
                should; services that return a single response may ignore.

        Returns:
            Playlist with tracks converted to standard format

        Raises:
            NotImplementedError: If service doesn't support playlists
        """
        raise NotImplementedError(
            f"Playlist operations not supported by {self.connector_name} connector"
        )

    @abstractmethod
    def convert_track_to_connector(
        self, track_data: Mapping[str, JsonValue]
    ) -> ConnectorTrack:
        """Convert service-specific track data to ConnectorTrack domain model.

        Each connector must implement this method to handle conversion from their
        service's API response format to the standardized ConnectorTrack domain model.

        Args:
            track_data: Raw track data from the service's API (JSON-shaped)

        Returns:
            ConnectorTrack with standardized fields and service-specific metadata
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
    look up track metrics like play counts or explicit flags.

    Args:
        metric_resolver: Resolver instance that can fetch metric values
        field_map: Maps metric names to connector metadata field names
        freshness_map: Optional per-metric freshness hours (from settings)
    """
    for metric_name, field_name in field_map.items():
        register_metric_resolver(metric_name, metric_resolver)
        freshness_hours = freshness_map.get(metric_name) if freshness_map else None
        register_metric_config(metric_name, field_name, freshness_hours)
