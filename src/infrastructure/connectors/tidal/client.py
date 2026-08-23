"""Tidal API client — pure JSON:API (v2) wrapper using native httpx2.

Thin async wrappers around Tidal's documented v2 API on ``BaseAPIClient``:
every public method runs through ``_api_call`` (rate limiting, centralized
tenacity retry policy, logging context, error suppression).

Authentication mirrors Spotify's bearer wiring: a ``TidalTokenManager``
(OAuth 2.1 + PKCE, single-flight rotated refresh) feeds ``TidalBearerAuth``
on the pooled client, which injects the bearer and retries ONCE with a
forced refresh on 401. A 401 that survives that replay is real — the client
raises ``TidalAuthRequiredError`` with an actionable reconnect message
instead of dissolving into a suppressed ``None``.

Rate handling is designed to ``Retry-After`` only (backlog v0.11.3
decision): Tidal publishes no rate numbers, so ``TidalErrorClassifier``
routes 429s into the shared ``RetryAfterWait`` policy, which honors the
header when present and falls back to the configured exponential policy
when absent — no invented Tidal-specific backoff.

Pagination is JSON:API cursor-style: the server controls page size (no size
parameter is ever sent), each page's ``links.next`` is a relative URL
(``/tracks?...&page[cursor]=zyx``, verified against the vendored
``tidal-api-oas.json``) followed verbatim until absent, with a page cap as
a runaway guard.
"""

from http import HTTPStatus
from typing import ClassVar, Final, override
import urllib.parse

from attrs import define, field
import httpx2
from pydantic import ValidationError
from tenacity import AsyncRetrying

from src.config import get_logger, settings
from src.domain.entities.shared import JsonDict
from src.domain.exceptions import (
    ConnectorSyncError,
    TidalAuthRequiredError,
    TokenRefreshContendedError,
)
from src.infrastructure.connectors._shared.http_client import (
    make_tidal_client,
    parse_json_response,
)
from src.infrastructure.connectors._shared.retry_policies import (
    RetryConfig,
    RetryPolicyFactory,
)
from src.infrastructure.connectors._shared.token_storage import TokenStorage
from src.infrastructure.connectors.base import BaseAPIClient
from src.infrastructure.connectors.tidal.auth import (
    TidalBearerAuth,
    TidalTokenManager,
)
from src.infrastructure.connectors.tidal.error_classifier import (
    TidalErrorClassifier,
)
from src.infrastructure.connectors.tidal.oas_models import (
    CursorLinks,
    JsonApiDocument,
    TidalCollectionItemRef,
    TidalOasModel,
    TidalTrackResource,
)

logger = get_logger(__name__).bind(service="tidal_client")

# Token-storage / settings service key.
TIDAL_SERVICE: Final = "tidal"

# Catalog country for data-plane lookups (every v2 catalog call requires
# ``countryCode``). REVISIT: the user's real country lives on
# ``GET /users/me`` (``Users_Attributes.country``), but that endpoint needs
# the ``user.read`` scope and the connect flow requests only
# ``collection.read`` — there is no live source this cycle. A wrong country
# makes region-gated tracks read as absent (and puts them on the no-match
# backoff clock), so mirror Apple's storefront-style resolution when the
# scope set expands (v0.13.4 library sync at the latest).
TIDAL_COUNTRY_CODE: Final = "US"

# Runaway guard on cursor walks: Tidal controls the page size, so a
# legitimate ISRC lookup is a handful of pages at most. Hitting this cap
# means the cursor chain is broken (or a collection walk grew beyond what
# one aggregate call should carry) — the walk stops with a warning.
# Revisit when the v0.13.4 library sync needs full-collection walks.
MAX_CURSOR_PAGES: Final = 50

# Spec path (tidal-api-oas.json): /userCollectionTracks/{id}/relationships/
# items, where id "me" addresses the authenticated user's collection.
_COLLECTION_ITEMS_PATH: Final = "/userCollectionTracks/me/relationships/items"

# Spec query-parameter names, verified against the vendored spec.
_CURSOR_PARAM: Final = "page[cursor]"
_ISRC_FILTER_PARAM: Final = "filter[isrc]"


def _validated[ModelT: TidalOasModel](model: type[ModelT], data: JsonDict) -> ModelT:
    """Boundary-validate a response body, or raise the connector-flavored error.

    A body Tidal actually served but we cannot read is an upstream-contract
    failure, not an internal one — surfacing it as ``ConnectorSyncError``
    keeps a malformed page from becoming a 500.
    """
    try:
        return model.model_validate(data)
    except ValidationError:
        logger.error("Tidal response failed boundary validation", exc_info=True)
        raise ConnectorSyncError(
            TIDAL_SERVICE,
            "Tidal returned a response in an unexpected shape — try again in a moment",
        ) from None


def _cursor_from_links(links: CursorLinks | None) -> str | None:
    """Extract the ``page[cursor]`` value from a page's ``links.next``.

    ``None`` when there is no next link — the last page. A next link
    without a cursor parameter also reads as "last page" rather than
    looping on an unfollowable link.
    """
    if links is None or links.next is None:
        return None
    query = urllib.parse.urlsplit(links.next).query
    values = urllib.parse.parse_qs(query).get(_CURSOR_PARAM)
    return values[0] if values else None


@define(frozen=True, slots=True)
class TidalCollectionItemsPage:
    """One server-sized page of collection items plus the cursor onward.

    ``total`` is the collection size from the document's ``meta.total`` —
    served "when available" per the spec's pagination prose (and possibly
    approximate); ``None`` when the page carries none, in which case a
    count requires walking the cursor chain.
    """

    items: list[TidalCollectionItemRef]
    next_cursor: str | None
    total: int | None = None


@define(slots=True)
class TidalAPIClient(BaseAPIClient):
    """Pure Tidal API client using native httpx2.

    Example:
        >>> client = TidalAPIClient()
        >>> tracks = await client.get_tracks_by_isrc("QMJMT1701229", "US")
        >>> page = await client.get_collection_track_items()
    """

    _SUPPRESS_ERRORS: ClassVar[tuple[type[BaseException], ...]] = (
        httpx2.HTTPStatusError,
        httpx2.RequestError,
    )

    _retry_policy: AsyncRetrying = field(init=False, repr=False)
    _client: httpx2.AsyncClient = field(init=False, repr=False)
    _storage: TokenStorage = field(init=False, repr=False)
    _user_id: str = field(init=False, repr=False)
    _token_manager: TidalTokenManager = field(init=False, repr=False)

    def __attrs_post_init__(self) -> None:
        """Initialize token manager, retry policy, and long-lived pooled client."""
        logger.debug("Initializing Tidal API client")
        from src.infrastructure.connectors._shared.token_storage import (
            get_token_storage,
        )
        from src.infrastructure.persistence.database.user_context import (
            get_current_user_id_from_context,
        )

        self._storage = get_token_storage()
        self._user_id = get_current_user_id_from_context()
        self._token_manager = TidalTokenManager(
            storage=self._storage, user_id=self._user_id
        )

        self._retry_policy = RetryPolicyFactory.create_policy(
            RetryConfig(
                service_name=TIDAL_SERVICE,
                classifier=TidalErrorClassifier(),
                max_attempts=settings.api.tidal.retry_count,
                wait_multiplier=settings.api.tidal.retry_base_delay,
                wait_max=settings.api.tidal.retry_max_delay,
                # A refresh-lock timeout inside the bearer auth flow is
                # transient (another process's refresh was mid-POST) — let
                # the policy retry it like a network blip.
                service_error_types=(TokenRefreshContendedError,),
            )
        )
        self._client = make_tidal_client(TidalBearerAuth(self._token_manager))

    @override
    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    # -------------------------------------------------------------------------
    # Request plumbing
    # -------------------------------------------------------------------------

    async def _get_json(
        self, path: str, params: dict[str, str] | None = None
    ) -> JsonDict:
        """Authenticated GET with actionable-401 surfacing.

        ``TidalBearerAuth`` has already spent its one forced-refresh replay
        by the time a 401 reaches here — a surviving 401 means the grant is
        genuinely dead, so it raises the typed auth error instead of
        becoming a suppressed ``None``.
        """
        response = await self._client.get(path, params=params)
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            raise TidalAuthRequiredError(
                "Tidal rejected the access token even after a refresh (401) "
                "— reconnect Tidal from the Integrations page."
            )
        _ = response.raise_for_status()
        return parse_json_response(response)

    async def _paginate_cursor[ResourceT](
        self,
        operation: str,
        path: str,
        params: dict[str, str],
        document_model: type[JsonApiDocument[list[ResourceT]]],
    ) -> list[ResourceT]:
        """Follow ``links.next`` until absent, aggregating every page's data.

        Each page is its own ``_api_call`` (independently retried and
        paced); transport failures raise (``suppress=()``) instead of
        silently truncating the walk — a partial aggregate misread as
        complete would poison anything derived from it. ``MAX_CURSOR_PAGES``
        guards against a broken cursor chain walking forever.
        """
        resources: list[ResourceT] = []
        next_link: str | None = None
        for _page_number in range(MAX_CURSOR_PAGES):
            data = (
                await self._api_call(
                    operation, self._get_json, path, params, suppress=()
                )
                if next_link is None
                else await self._api_call(
                    operation, self._get_json, next_link, suppress=()
                )
            )
            if data is None:  # unreachable with suppress=(); satisfies typing
                break
            document = _validated(document_model, data)
            resources.extend(document.data or [])
            next_link = document.links.next if document.links else None
            if next_link is None:
                return resources
        logger.warning(
            "Tidal cursor walk hit the page cap — result may be truncated",
            operation=operation,
            page_cap=MAX_CURSOR_PAGES,
        )
        return resources

    # -------------------------------------------------------------------------
    # Tracks
    # -------------------------------------------------------------------------

    async def get_tracks_by_isrc(
        self, isrc: str, country_code: str
    ) -> list[TidalTrackResource]:
        """Every track resource carrying ``isrc`` (1:N), paginated to completion.

        ONE code per request, enforced: ``filter[isrc]`` accepts multiple
        codes, but multi-code batching returns one match per code — a
        silent partial answer for a 1:N lookup — so a value smuggling
        several codes is rejected before any request is sent.

        Raises:
            ValueError: ``isrc`` is empty or contains more than one code.
        """
        code = isrc.strip()
        if not code or any(separator in code for separator in (",", " ")):
            raise ValueError(
                "get_tracks_by_isrc takes exactly one ISRC per request — "
                "multi-code filter[isrc] batching returns only one match "
                f"per code (got {isrc!r})"
            )
        return await self._paginate_cursor(
            "get_tidal_tracks_by_isrc",
            "/tracks",
            {"countryCode": country_code, _ISRC_FILTER_PARAM: code},
            JsonApiDocument[list[TidalTrackResource]],
        )

    async def get_track(
        self, track_id: str, country_code: str, include_replacement: bool = True
    ) -> JsonApiDocument[TidalTrackResource] | None:
        """One track by id, with its artists (and successor) side-loaded.

        ``include=artists`` always rides along: artist names live only in
        side-loaded artist resources (track attributes carry none), and a
        canonical cannot be minted without them. ``include=replacement``
        (default on) adds Tidal's platform-asserted successor pointer — the
        typed-succession source consulted for dead IDs.

        Returns the whole validated document (``data`` + ``included``) so
        ``models.tidal_track_detail_from_document`` can assemble the
        domain-facing detail; ``None`` on 404 or a suppressed transport
        failure.
        """
        include = "artists,replacement" if include_replacement else "artists"
        params: dict[str, str] = {"countryCode": country_code, "include": include}
        data = await self._api_call(
            "get_tidal_track", self._get_json, f"/tracks/{track_id}", params
        )
        if data is None:
            return None
        return _validated(JsonApiDocument[TidalTrackResource], data)

    # -------------------------------------------------------------------------
    # User collection (favorites)
    # -------------------------------------------------------------------------

    async def get_collection_track_items(
        self, cursor: str | None = None
    ) -> TidalCollectionItemsPage | None:
        """One page of the user's collection track items, newest first.

        ``sort=-addedAt`` (spec enum value); page size is server-controlled
        — deliberately no size parameter. Returns the page's items plus the
        cursor for the next page (``None`` on the last page), so callers
        own the walk. ``None`` on a suppressed transport failure.
        """
        params: dict[str, str] = {"sort": "-addedAt"}
        if cursor is not None:
            params[_CURSOR_PARAM] = cursor
        data = await self._api_call(
            "get_tidal_collection_track_items",
            self._get_json,
            _COLLECTION_ITEMS_PATH,
            params,
        )
        if data is None:
            return None
        document = _validated(JsonApiDocument[list[TidalCollectionItemRef]], data)
        return TidalCollectionItemsPage(
            items=document.data or [],
            next_cursor=_cursor_from_links(document.links),
            total=document.meta.total if document.meta is not None else None,
        )

    async def save_favorites_count(self, count: int) -> None:
        """Refresh ``extra_data["favorites_count"]`` on the stored token.

        The cached count is what ``get_tidal_status`` renders without
        spending a Tidal request on status polls; a snapshot has just paid
        for the real number, so it writes the cache back (mirrors the
        Discogs ``save_collection_count`` posture). The narrow
        ``update_extra_data`` write touches only the cache column — a
        load→save round trip here could race a refresh rotation and write
        a dead refresh token back over the rotated grant. No-op when no
        token is stored (a disconnect raced the snapshot).
        """
        await self._storage.update_extra_data(
            TIDAL_SERVICE, self._user_id, {"favorites_count": count}
        )
