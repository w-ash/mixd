"""Apple Music API client — pure API wrapper using native httpx2.

Thin async wrappers around the Apple Music API on ``BaseAPIClient``: every
public method runs through ``_api_call`` (rate limiting, centralized tenacity
retry policy, logging context, error suppression).

Authentication is two-layered:
- The developer token (an ES256 JWT minted locally by
  ``DeveloperTokenProvider``) rides on EVERY request as a bearer, injected by
  ``AppleMusicDeveloperAuth`` on the pooled client. There is no refresh flow.
- The per-user Music User Token (MUT) is loaded from token storage (service
  key ``"apple_music"``, ``access_token`` field) and injected per-call as the
  ``Music-User-Token`` header on ``/v1/me/*`` endpoints only. MUTs cannot be
  refreshed: when Apple rejects one — HTTP 403 on a ``/v1/me/*`` request,
  body ``{code: "40300", title: "Forbidden", detail: "Invalid
  authentication"}`` — the client raises ``AppleMusicAuthRequiredError`` and
  best-effort records ``extra_data["reauth_required"]`` on the stored token
  so the connector status surface can prompt re-authorization. A 403 on a
  catalog request carried no MUT and stays an instance-flavored permanent
  error. 401 is the developer token being rejected (instance
  configuration), never a per-user condition.
"""

from collections.abc import Sequence
from http import HTTPStatus
from typing import ClassVar, Final, override

from attrs import define, field
import httpx2
from tenacity import AsyncRetrying

from src.config import get_logger, settings
from src.domain.entities.shared import JsonDict
from src.domain.exceptions import AppleMusicAuthRequiredError
from src.infrastructure.connectors._shared.http_client import (
    make_apple_music_client,
    parse_json_response,
)
from src.infrastructure.connectors._shared.retry_policies import (
    RetryConfig,
    RetryPolicyFactory,
)
from src.infrastructure.connectors._shared.token_storage import TokenStorage
from src.infrastructure.connectors.apple_music.auth import (
    AppleMusicDeveloperAuth,
    DeveloperTokenProvider,
)
from src.infrastructure.connectors.apple_music.error_classifier import (
    AppleMusicErrorClassifier,
    first_json_api_error,
    indicates_user_token_rejection,
    is_music_user_token_request,
)
from src.infrastructure.connectors.apple_music.models import (
    AppleMusicRecentlyPlayedResponse,
    AppleMusicSong,
    AppleMusicSongsResponse,
    AppleMusicStorefront,
    AppleMusicStorefrontResponse,
)
from src.infrastructure.connectors.base import BaseAPIClient

logger = get_logger(__name__).bind(service="apple_music_client")

# Token-storage service key for the per-user Music User Token.
APPLE_MUSIC_SERVICE: Final = "apple_music"


@define(frozen=True, slots=True)
class CatalogSongsLookup:
    """Merged outcome of a chunked catalog lookup.

    ``failed_values`` are the input values (ISRCs or ids) whose chunk request
    failed after retries — they are UNANSWERED, not absent from the catalog.
    Callers must not read them as "no results": the matching provider records
    them as API errors and the inward resolver leaves them off the no-match
    backoff clock.
    """

    songs: list[AppleMusicSong]
    failed_values: list[str]


# Apple documents a 25-code cap for filter[isrc] lookups.
CATALOG_ISRC_CHUNK_SIZE: Final = 25
# Apple does not clearly document an ids-per-request cap for
# /catalog/{storefront}/songs?ids= — chunk conservatively at the same 25.
CATALOG_IDS_CHUNK_SIZE: Final = 25
# filter[equivalents] accepts up to ~300 ids per request.
CATALOG_EQUIVALENTS_CHUNK_SIZE: Final = 300

# Fixed page size for /v1/me/recent/played/tracks — the endpoint maximum
# (limit=50 → 400 with code "40005", "less than or equal to 30").
RECENTLY_PLAYED_LIMIT: Final = 30


@define(slots=True)
class AppleMusicAPIClient(BaseAPIClient):
    """Pure Apple Music API client using native httpx2.

    Example:
        >>> client = AppleMusicAPIClient()
        >>> storefront = await client.get_storefront()
        >>> lookup = await client.get_songs_by_isrc("us", ["USUM72309818"])
        >>> lookup.songs, lookup.failed_values
    """

    _SUPPRESS_ERRORS: ClassVar[tuple[type[BaseException], ...]] = (
        httpx2.HTTPStatusError,
        httpx2.RequestError,
    )

    _token_provider: DeveloperTokenProvider = field(init=False, repr=False)
    _retry_policy: AsyncRetrying = field(init=False, repr=False)
    _client: httpx2.AsyncClient = field(init=False, repr=False)
    _storage: TokenStorage = field(init=False, repr=False)
    _user_id: str = field(init=False, repr=False)
    _music_user_token: str | None = field(init=False, default=None, repr=False)

    def __attrs_post_init__(self) -> None:
        """Initialize token storage, retry policy, and long-lived pooled client."""
        logger.debug("Initializing Apple Music API client")
        from src.infrastructure.connectors._shared.token_storage import (
            get_token_storage,
        )
        from src.infrastructure.persistence.database.user_context import (
            get_current_user_id_from_context,
        )

        self._storage = get_token_storage()
        self._user_id = get_current_user_id_from_context()
        self._token_provider = DeveloperTokenProvider()

        self._retry_policy = RetryPolicyFactory.create_policy(
            RetryConfig(
                service_name="apple_music",
                classifier=AppleMusicErrorClassifier(),
                max_attempts=settings.api.apple_music.retry_count,
                wait_multiplier=settings.api.apple_music.retry_base_delay,
                wait_max=settings.api.apple_music.retry_max_delay,
            )
        )
        self._client = make_apple_music_client(
            AppleMusicDeveloperAuth(self._token_provider)
        )

    @override
    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    # -------------------------------------------------------------------------
    # Auth plumbing
    # -------------------------------------------------------------------------

    async def _music_user_headers(self) -> dict[str, str]:
        """Header block for ``/v1/me/*`` calls; loads and caches the stored MUT.

        Raises:
            AppleMusicAuthRequiredError: No Music User Token is stored for
                this user — the MusicKit browser authorization has never run
                (or the token was deleted).
        """
        token = self._music_user_token
        if token is None:
            stored = await self._storage.load_token(APPLE_MUSIC_SERVICE, self._user_id)
            token = stored.get("access_token") if stored else None
            if not token:
                logger.info("No Apple Music user token found — auth required")
                raise AppleMusicAuthRequiredError
            self._music_user_token = token
        return {"Music-User-Token": token}

    async def _raise_if_auth_rejected(self, response: httpx2.Response) -> None:
        """Convert Apple's auth rejections into actionable domain errors.

        401 rejects the developer token — an instance configuration problem
        (no reauth marker: the user cannot fix it by re-authorizing). A 403
        on a ``/v1/me/*`` request is Apple rejecting the Music User Token —
        the body is ``code "40300" / "Invalid authentication"`` with no
        token-marker string, and dev-token failures use 401 instead — so the
        user must re-authorize (MUTs cannot refresh); the stored token gets
        a best-effort ``reauth_required`` marker so status surfaces can
        prompt for it. Exempt 403s: one on a catalog request (it carried no
        MUT — an instance problem, left to ``raise_for_status`` and the
        classifier), and one explicitly naming the developer token. Any
        other status falls through to ``raise_for_status`` and the ordinary
        classifier path.
        """
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            raise AppleMusicAuthRequiredError(
                "Apple Music rejected the developer token (401). Check "
                "APPLE_TEAM_ID, APPLE_KEY_ID, and APPLE_PRIVATE_KEY."
            )
        if response.status_code != HTTPStatus.FORBIDDEN:
            return
        if not is_music_user_token_request(response.request):
            return
        if not indicates_user_token_rejection(first_json_api_error(response)):
            return
        # The cached MUT is dead — drop it so a later call reloads storage.
        self._music_user_token = None
        await self._mark_reauth_required()
        raise AppleMusicAuthRequiredError(
            "Apple Music user authorization expired or was revoked — "
            "re-authorize it from the Integrations page."
        )

    async def _mark_reauth_required(self) -> None:
        """Best-effort ``extra_data["reauth_required"]`` marker on the stored token.

        A storage failure must not mask the auth error being raised — log and
        move on.
        """
        try:
            await self._write_reauth_marker()
        except Exception:
            logger.warning(
                "Failed to record Apple Music reauth_required marker",
                exc_info=True,
            )

    async def _write_reauth_marker(self) -> None:
        """Set the reauth marker via the narrow ``update_extra_data`` write.

        Touches only the ``extra_data`` column — never rewrites token
        columns from a stale load. No-op when no token row exists.
        """
        await self._storage.update_extra_data(
            APPLE_MUSIC_SERVICE, self._user_id, {"reauth_required": True}
        )

    # -------------------------------------------------------------------------
    # Storefront
    # -------------------------------------------------------------------------

    async def get_storefront(self) -> AppleMusicStorefront | None:
        """Fetch the authorized user's storefront (requires Music-User-Token)."""
        data = await self._api_call(
            "get_apple_music_storefront", self._get_storefront_impl
        )
        if data is None:
            return None
        parsed = AppleMusicStorefrontResponse.model_validate(data)
        return parsed.data[0] if parsed.data else None

    async def _get_storefront_impl(self) -> JsonDict:
        """Pure implementation without retry logic."""
        headers = await self._music_user_headers()
        response = await self._client.get("/v1/me/storefront", headers=headers)
        await self._raise_if_auth_rejected(response)
        _ = response.raise_for_status()
        return parse_json_response(response)

    # -------------------------------------------------------------------------
    # Catalog lookups
    # -------------------------------------------------------------------------

    async def get_songs_by_isrc(
        self, storefront: str, isrcs: Sequence[str]
    ) -> CatalogSongsLookup:
        """Look up catalog songs by ISRC, chunked at 25 codes per request.

        Results are merged in request order. One ISRC can match multiple
        catalog songs (re-releases), and unmatched codes simply return
        nothing — correlate by ``attributes.isrc``, not by position. ISRCs
        whose chunk request failed come back in ``failed_values``.
        """
        return await self._get_catalog_songs(
            "get_apple_music_songs_by_isrc",
            storefront,
            "filter[isrc]",
            isrcs,
            CATALOG_ISRC_CHUNK_SIZE,
        )

    async def get_songs_by_ids(
        self, storefront: str, ids: Sequence[str]
    ) -> CatalogSongsLookup:
        """Fetch catalog songs by id via ``?ids=``, chunked at 25 per request."""
        return await self._get_catalog_songs(
            "get_apple_music_songs_by_ids",
            storefront,
            "ids",
            ids,
            CATALOG_IDS_CHUNK_SIZE,
        )

    async def get_song_equivalents(
        self, storefront: str, ids: Sequence[str]
    ) -> CatalogSongsLookup:
        """Resolve cross-storefront equivalents via ``filter[equivalents]``."""
        return await self._get_catalog_songs(
            "get_apple_music_song_equivalents",
            storefront,
            "filter[equivalents]",
            ids,
            CATALOG_EQUIVALENTS_CHUNK_SIZE,
        )

    async def _get_catalog_songs(
        self,
        operation: str,
        storefront: str,
        param: str,
        values: Sequence[str],
        chunk_size: int,
    ) -> CatalogSongsLookup:
        """Shared chunk-and-merge loop for the catalog songs endpoint.

        A chunk whose request fails after retries contributes no songs; its
        input values are surfaced in ``failed_values`` so callers can tell
        "unanswered" from "absent from the catalog". The remaining chunks
        still return.
        """
        songs: list[AppleMusicSong] = []
        failed_values: list[str] = []
        for start in range(0, len(values), chunk_size):
            chunk = list(values[start : start + chunk_size])
            data = await self._api_call(
                operation, self._get_catalog_songs_impl, storefront, param, chunk
            )
            if data is None:
                logger.warning(
                    f"Apple Music catalog lookup failed for {len(chunk)} values",
                    operation=operation,
                    requested=len(chunk),
                )
                failed_values.extend(chunk)
                continue
            songs.extend(AppleMusicSongsResponse.model_validate(data).data)
        return CatalogSongsLookup(songs=songs, failed_values=failed_values)

    async def _get_catalog_songs_impl(
        self, storefront: str, param: str, values: list[str]
    ) -> JsonDict:
        """Pure implementation without retry logic."""
        response = await self._client.get(
            f"/v1/catalog/{storefront}/songs",
            params={param: ",".join(values)},
        )
        await self._raise_if_auth_rejected(response)
        _ = response.raise_for_status()
        return parse_json_response(response)

    # -------------------------------------------------------------------------
    # Recently played
    # -------------------------------------------------------------------------

    async def get_recently_played(
        self, offset: int = 0
    ) -> AppleMusicRecentlyPlayedResponse | None:
        """Fetch one page of the user's recently-played songs (requires MUT).

        Fixed page size of 30 (the endpoint maximum); callers page via
        ``offset`` and the response's ``next`` cursor.
        """
        data = await self._api_call(
            "get_apple_music_recently_played",
            self._get_recently_played_impl,
            offset,
        )
        return AppleMusicRecentlyPlayedResponse.model_validate(data) if data else None

    async def _get_recently_played_impl(self, offset: int) -> JsonDict:
        """Pure implementation without retry logic."""
        headers = await self._music_user_headers()
        response = await self._client.get(
            "/v1/me/recent/played/tracks",
            params={"types": "songs", "limit": RECENTLY_PLAYED_LIMIT, "offset": offset},
            headers=headers,
        )
        await self._raise_if_auth_rejected(response)
        _ = response.raise_for_status()
        return parse_json_response(response)
