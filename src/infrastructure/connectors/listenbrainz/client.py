"""ListenBrainz API client on the shared connector machinery.

All traffic today targets the MetaBrainz Labs (Dataset Hoster) host —
``spotify-id-from-metadata`` lives on ``labs.api.listenbrainz.org``, not the
main API host. Future main-API calls belong on this same class so both hosts
pace through the one ``"listenbrainz"`` limiter: uniform pacing protects a
free shared MetaBrainz service whose Labs host sends no rate headers to
self-correct from.

Every call runs through ``_api_call`` (process-wide limiter, centralized
tenacity retry policy, logging context, suppression). Transport, HTTP, and
response-shape failures suppress to ``None`` — the lookup is an optional
pre-filter and must degrade for its caller, never fail it.
"""

from collections.abc import Sequence
from typing import ClassVar, Final, cast, override

from attrs import define, field
import httpx2
from tenacity import AsyncRetrying

from src.config import get_logger, settings
from src.domain.exceptions import ConnectorSyncError
from src.infrastructure.connectors._shared.http_client import (
    LISTENBRAINZ_LABS_BASE,
    make_listenbrainz_client,
)
from src.infrastructure.connectors._shared.rate_limiting import apply_rate_headers
from src.infrastructure.connectors._shared.retry_policies import RetryPolicyFactory
from src.infrastructure.connectors.base import BaseAPIClient
from src.infrastructure.connectors.listenbrainz.error_classifier import (
    ListenBrainzErrorClassifier,
)
from src.infrastructure.connectors.listenbrainz.models import (
    SpotifyIdLookupQuery,
    SpotifyIdLookupResponse,
    SpotifyIdLookupResult,
)

logger = get_logger(__name__).bind(service="listenbrainz_client")

# Settings key; also the shared limiter's pacing key (derived from the
# package name by BaseAPIClient.service_name).
LISTENBRAINZ_SERVICE: Final = "listenbrainz"

# Labs Dataset Hoster path answering (artist, release, track) → Spotify ids.
_SPOTIFY_ID_LOOKUP_PATH: Final = "/spotify-id-from-metadata/json"


@define(slots=True)
class ListenBrainzAPIClient(BaseAPIClient):
    """Pure ListenBrainz API client using native httpx2."""

    _SUPPRESS_ERRORS: ClassVar[tuple[type[BaseException], ...]] = (
        httpx2.HTTPStatusError,
        httpx2.RequestError,
        ConnectorSyncError,
    )

    _retry_policy: AsyncRetrying = field(init=False, repr=False)
    _client: httpx2.AsyncClient = field(init=False, repr=False)

    def __attrs_post_init__(self) -> None:
        """Initialize the retry policy and the pooled Labs-host client."""
        self._retry_policy = RetryPolicyFactory.for_service(
            LISTENBRAINZ_SERVICE,
            ListenBrainzErrorClassifier(),
            settings.api.listenbrainz,
        )
        self._client = make_listenbrainz_client(LISTENBRAINZ_LABS_BASE)

    @override
    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    async def lookup_spotify_ids(
        self, queries: Sequence[SpotifyIdLookupQuery]
    ) -> list[SpotifyIdLookupResult] | None:
        """One batched POST resolving metadata queries to Spotify track ids.

        The endpoint answers one echoed row per query in request order, with
        misses as ``spotify_track_ids: []``. Returns ``None`` when the call
        failed after retries (suppressed) — distinct from an all-miss answer.
        """
        if not queries:
            return []
        return await self._api_call(
            "lookup_spotify_ids", self._lookup_spotify_ids_impl, queries
        )

    async def _lookup_spotify_ids_impl(
        self, queries: Sequence[SpotifyIdLookupQuery]
    ) -> list[SpotifyIdLookupResult]:
        """POST the query array and boundary-validate the echoed rows."""
        response = await self._client.post(
            _SPOTIFY_ID_LOOKUP_PATH,
            json=[query.model_dump() for query in queries],
        )
        # No-op on Labs responses (no rate headers); main-API responses carry
        # the X-RateLimit-* headers the shared success-path brake reads.
        apply_rate_headers(response, LISTENBRAINZ_SERVICE)
        _ = response.raise_for_status()
        return _validated_rows(response)


def _validated_rows(response: httpx2.Response) -> list[SpotifyIdLookupResult]:
    """Boundary-validate the array body, or raise the connector-flavored error.

    ``_shared.boundary.validated`` handles object bodies only; this is its
    array-body sibling. Pydantic's ``ValidationError`` subclasses
    ``ValueError``, so one arm also covers a non-JSON body.
    """
    try:
        raw = cast("object", response.json())
        return SpotifyIdLookupResponse.model_validate(raw).root
    except ValueError:
        logger.error(
            "listenbrainz lookup response failed boundary validation",
            exc_info=True,
        )
        raise ConnectorSyncError(
            LISTENBRAINZ_SERVICE,
            "ListenBrainz returned a lookup response in an unexpected shape",
        ) from None
