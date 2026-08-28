"""Base classes for music service API connectors.

Provides shared functionality for integrating with external music services like Spotify,
Last.fm, MusicBrainz, etc. Child connectors inherit from these base classes to get
standardized retry, rate limiting, and conversion patterns.

Classes:
    BaseAPIClient: Shared retry/rate-limit/suppression plumbing for API clients
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
from src.domain.entities.shared import JsonValue
from src.domain.entities.track import ConnectorTrack
from src.infrastructure.connectors._shared.error_classifier import (
    ErrorClassifier,
    classify_unknown_error,
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

        Before an error is suppressed, ``_surface_suppressed_error`` gets one
        look at it: a subclass can promote a specific failure shape into a
        typed exception that raises instead of dissolving into ``None`` (the
        way Spotify's quota-exhausted 429 must — see the override there).

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
                    replacement = self._surface_suppressed_error(exc)
                    if replacement is not None:
                        raise replacement from exc
                    return None
                raise
            finally:
                record_api_call(time.perf_counter_ns() - started)

    def _surface_suppressed_error(self, exc: Exception) -> Exception | None:
        """Translate an about-to-be-suppressed error into one that must surface.

        Default: nothing escapes (return ``None``). Subclasses override to
        recognize failure shapes whose suppression would strand the caller —
        the replacement is raised ``from`` the original instead of ``None``
        being returned.
        """
        del exc
        return None

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
