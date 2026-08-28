"""Discogs API client — pure API wrapper using native httpx2.

Thin async wrappers around the Discogs API on ``BaseAPIClient``: every
public method runs through ``_api_call`` (rate limiting, centralized
tenacity retry policy, logging context, error suppression) while holding the
shared limiter's call slot (``connector_call_slot``, ``max_concurrent=1``) —
Discogs throttles by source IP, so the whole instance shares one 60/min
budget and calls must not interleave across users. Every response feeds
``apply_rate_headers``, the success-path brake on
``X-Discogs-Ratelimit-Remaining``. Image fetches (i.discogs.com) ride a
separate, undocumented bucket: build them with
``make_discogs_image_client()`` (no auth, no slot, no limiter) and never
feed their responses to ``apply_rate_headers``.

Authentication is an injected ``httpx2.Auth`` strategy seam:

- Injected at construction (tests, future OAuth 1.0a), the strategy is used
  verbatim and token storage is never consulted.
- Left unset (production), the user's personal access token is loaded
  lazily from token storage (service key ``"discogs"``, ``access_token``
  field) on the first call — mirroring the Apple Music MUT helper — and
  wrapped in ``DiscogsTokenAuth``. No stored token raises
  ``DiscogsAuthRequiredError`` before any request is sent.

A 401 response raises ``DiscogsAuthRequiredError`` with an actionable
reconnect message instead of dissolving into a suppressed ``None`` — the
token is BYO, so only the user can fix it.
"""

from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import ClassVar, Final, override

from attrs import define, field
import httpx2
from tenacity import AsyncRetrying

from src.config import get_logger, settings
from src.domain.entities.shared import JsonDict
from src.domain.exceptions import DiscogsAuthRequiredError
from src.infrastructure.connectors._shared.boundary import validated
from src.infrastructure.connectors._shared.http_client import (
    make_discogs_client,
    parse_json_response,
)
from src.infrastructure.connectors._shared.rate_limiting import (
    apply_rate_headers,
    connector_call_slot,
)
from src.infrastructure.connectors._shared.retry_policies import (
    RetryPolicyFactory,
)
from src.infrastructure.connectors._shared.token_storage import (
    TokenStorage,
    load_required_access_token,
)
from src.infrastructure.connectors.base import BaseAPIClient
from src.infrastructure.connectors.discogs.auth import DiscogsTokenAuth
from src.infrastructure.connectors.discogs.error_classifier import (
    DiscogsErrorClassifier,
)
from src.infrastructure.connectors.discogs.models import (
    DiscogsCollectionPage,
    DiscogsCollectionRelease,
    DiscogsIdentity,
    DiscogsMaster,
    DiscogsRelease,
)

logger = get_logger(__name__).bind(service="discogs_client")

# Token-storage service key for the per-user personal access token.
DISCOGS_SERVICE: Final = "discogs"

# Discogs's documented per_page maximum for collection pagination.
COLLECTION_PAGE_SIZE: Final = 100

# Discogs refuses to serve items past position 10,000 on paginated
# endpoints — the collection walk stops there rather than earning 404s on
# pages that can never be served.
COLLECTION_ITEM_CEILING: Final = 10_000


@define(slots=True)
class DiscogsAPIClient(BaseAPIClient):
    """Pure Discogs API client using native httpx2.

    Example:
        >>> client = DiscogsAPIClient()
        >>> identity = await client.get_identity()
        >>> page = await client.get_collection_page(identity.username)
    """

    _SUPPRESS_ERRORS: ClassVar[tuple[type[BaseException], ...]] = (
        httpx2.HTTPStatusError,
        httpx2.RequestError,
    )

    _auth: httpx2.Auth | None = field(default=None, repr=False)
    _retry_policy: AsyncRetrying = field(init=False, repr=False)
    _client: httpx2.AsyncClient = field(init=False, repr=False)
    _storage: TokenStorage = field(init=False, repr=False)
    _user_id: str = field(init=False, repr=False)

    def __attrs_post_init__(self) -> None:
        """Initialize token storage, retry policy, and long-lived pooled client."""
        logger.debug("Initializing Discogs API client")
        from src.infrastructure.connectors._shared.token_storage import (
            get_token_storage,
        )
        from src.infrastructure.persistence.database.user_context import (
            get_current_user_id_from_context,
        )

        self._storage = get_token_storage()
        self._user_id = get_current_user_id_from_context()

        self._retry_policy = RetryPolicyFactory.for_service(
            DISCOGS_SERVICE,
            DiscogsErrorClassifier(),
            settings.api.discogs,
        )
        self._client = make_discogs_client(self._auth)

    @override
    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    # -------------------------------------------------------------------------
    # Auth plumbing
    # -------------------------------------------------------------------------

    async def _resolve_auth(self) -> httpx2.Auth:
        """The auth strategy for this call; lazily loads the stored token.

        Raises:
            DiscogsAuthRequiredError: No auth strategy was injected and no
                personal access token is stored for this user.
        """
        auth = self._auth
        if auth is None:
            token = await load_required_access_token(
                self._storage,
                DISCOGS_SERVICE,
                self._user_id,
                missing_error=DiscogsAuthRequiredError,
            )
            auth = DiscogsTokenAuth(token)
            self._auth = auth
        return auth

    async def get_stored_username(self) -> str | None:
        """Discogs username recorded at connect time (``account_name``).

        A storage-only read — the username was proven live against
        ``/oauth/identity`` when the token was validated, so no request is
        spent re-asking. None when Discogs has never been connected.
        """
        stored = await self._storage.load_token(DISCOGS_SERVICE, self._user_id)
        return stored.get("account_name") if stored else None

    async def save_account_name(self, username: str) -> None:
        """Backfill ``account_name`` on the stored token.

        The username is normally recorded at connect time; a token row
        missing it (a legacy or partially-written row) gets healed when a
        live identity probe re-derives it. The narrow ``update_extra_data``
        write (empty merge + ``account_name``) never rewrites token
        columns. No-op when no token is stored.
        """
        await self._storage.update_extra_data(
            DISCOGS_SERVICE, self._user_id, {}, account_name=username
        )

    async def save_collection_count(self, count: int) -> None:
        """Refresh ``extra_data["collection_count"]`` on the stored token.

        The cached count is what ``get_discogs_status`` renders without
        spending Discogs budget on status polls; a snapshot has just paid for
        the real number, so it writes the cache back — via the narrow
        ``update_extra_data`` write, which never rewrites token columns from
        a stale load. No-op when no token is stored (a disconnect raced the
        snapshot).
        """
        await self._storage.update_extra_data(
            DISCOGS_SERVICE, self._user_id, {"collection_count": count}
        )

    # -------------------------------------------------------------------------
    # Serialized call plumbing
    # -------------------------------------------------------------------------

    async def _locked_api_call[T](
        self,
        operation: str,
        impl: Callable[..., Awaitable[T]],
        *args: object,
        suppress: tuple[type[BaseException], ...] | None = None,
    ) -> T | None:
        """``_api_call`` while holding the shared Discogs call slot.

        The slot wraps the whole retry loop: retries of one logical call must
        not interleave with another user's calls into the shared per-IP
        budget.
        """
        async with connector_call_slot(DISCOGS_SERVICE):
            return await self._api_call(operation, impl, *args, suppress=suppress)

    async def _get_json(
        self, path: str, params: dict[str, str | int] | None = None
    ) -> JsonDict:
        """Authenticated GET with rate-header self-correction.

        Every response feeds ``apply_rate_headers`` before status handling —
        the remaining-headroom header is present on errors too, and a 429's
        header is exactly the one worth reading.
        """
        response = await self._client.get(
            path, params=params, auth=await self._resolve_auth()
        )
        apply_rate_headers(response, DISCOGS_SERVICE)
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            raise DiscogsAuthRequiredError(
                "Discogs rejected the personal access token (401) — "
                "reconnect Discogs from the Integrations page, or check "
                "that your personal access token is still valid."
            )
        _ = response.raise_for_status()
        return parse_json_response(response)

    # -------------------------------------------------------------------------
    # Identity
    # -------------------------------------------------------------------------

    async def get_identity(self) -> DiscogsIdentity | None:
        """Fetch the authenticated user's identity (id + username)."""
        data = await self._locked_api_call(
            "get_discogs_identity", self._get_identity_impl
        )
        return (
            validated(DiscogsIdentity, data, service="discogs", subject="an identity")
            if data
            else None
        )

    async def _get_identity_impl(self) -> JsonDict:
        """Pure implementation without retry logic."""
        return await self._get_json("/oauth/identity")

    # -------------------------------------------------------------------------
    # Collection
    # -------------------------------------------------------------------------

    async def get_collection_page(
        self,
        username: str,
        page: int = 1,
        per_page: int = COLLECTION_PAGE_SIZE,
    ) -> DiscogsCollectionPage | None:
        """Fetch one page of the user's collection (folder 0 = All).

        Raises:
            ConnectorSyncError: the page body did not match the validated
                Discogs shape at all. A response Discogs actually served but
                we cannot read is an upstream-contract failure, not an
                internal one — surfacing it as the connector-flavored error
                keeps a malformed page from becoming a 500.
        """
        data = await self._locked_api_call(
            "get_discogs_collection_page",
            self._get_collection_page_impl,
            username,
            page,
            per_page,
        )
        if not data:
            return None
        return validated(
            DiscogsCollectionPage, data, service="discogs", subject="a collection page"
        )

    async def get_all_collection_releases(
        self, username: str, per_page: int = COLLECTION_PAGE_SIZE
    ) -> list[DiscogsCollectionRelease]:
        """Walk every collection page, stopping at the 10,000-item ceiling.

        Transport failures raise (``suppress=()``) instead of silently
        truncating the walk — a partial collection misread as complete would
        poison any count derived from it. The queue is taken per page, so a
        long walk interleaves with (rather than starves) other callers.
        """
        releases: list[DiscogsCollectionRelease] = []
        page = 1
        pages = 1
        while page <= pages and len(releases) < COLLECTION_ITEM_CEILING:
            data = await self._locked_api_call(
                "get_discogs_collection_page",
                self._get_collection_page_impl,
                username,
                page,
                per_page,
                suppress=(),
            )
            if data is None:  # unreachable with suppress=(); satisfies typing
                break
            parsed = validated(
                DiscogsCollectionPage,
                data,
                service="discogs",
                subject="a collection page",
            )
            releases.extend(parsed.releases)
            pages = parsed.pagination.pages
            page += 1
        # A final page can overshoot the ceiling; hold the invariant exactly.
        del releases[COLLECTION_ITEM_CEILING:]
        return releases

    async def _get_collection_page_impl(
        self, username: str, page: int, per_page: int
    ) -> JsonDict:
        """Pure implementation without retry logic."""
        return await self._get_json(
            f"/users/{username}/collection/folders/0/releases",
            params={"page": page, "per_page": per_page},
        )

    # -------------------------------------------------------------------------
    # Catalog
    # -------------------------------------------------------------------------

    async def get_release(self, release_id: int) -> DiscogsRelease | None:
        """Fetch full release detail."""
        data = await self._locked_api_call(
            "get_discogs_release", self._get_release_impl, release_id
        )
        return (
            validated(DiscogsRelease, data, service="discogs", subject="a release")
            if data
            else None
        )

    async def _get_release_impl(self, release_id: int) -> JsonDict:
        """Pure implementation without retry logic."""
        return await self._get_json(f"/releases/{release_id}")

    async def get_master(self, master_id: int) -> DiscogsMaster | None:
        """Fetch a master release (the version grouping)."""
        data = await self._locked_api_call(
            "get_discogs_master", self._get_master_impl, master_id
        )
        return (
            validated(DiscogsMaster, data, service="discogs", subject="a master")
            if data
            else None
        )

    async def _get_master_impl(self, master_id: int) -> JsonDict:
        """Pure implementation without retry logic."""
        return await self._get_json(f"/masters/{master_id}")
