"""Spotify API client - Pure API wrapper using native httpx2.

Provides a thin async wrapper around the Spotify Web API using httpx2.AsyncClient
directly. All methods are natively async — no asyncio.to_thread() bridging.

Key components:
- SpotifyAPIClient: Token-authenticated client for all API calls
- SpotifyTokenManager handles OAuth 2.0 token lifecycle (auth.py)
- Centralized retry policy using tenacity (retry_policies.py)
- Market-aware API calls with configurable timeouts
"""

from collections.abc import Awaitable, Callable, Mapping
from http import HTTPStatus
from typing import ClassVar, cast, override

from attrs import define, field
import httpx2
from pydantic import BaseModel
from tenacity import AsyncRetrying

from src.config import get_logger, settings
from src.config.constants import SpotifyConstants
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.exceptions import SpotifyQuotaExhaustedError
from src.domain.repositories.play import RECENTLY_PLAYED_PAGE_LIMIT
from src.infrastructure.connectors._shared.boundary import validated
from src.infrastructure.connectors._shared.fan_out import bounded_fan_out
from src.infrastructure.connectors._shared.http_client import parse_json_response
from src.infrastructure.connectors._shared.retry_policies import (
    RetryPolicyFactory,
)
from src.infrastructure.connectors.base import BaseAPIClient
from src.infrastructure.connectors.spotify.auth import SpotifyTokenManager
from src.infrastructure.connectors.spotify.error_classifier import (
    is_quota_exhausted_response,
)
from src.infrastructure.connectors.spotify.models import (
    SpotifyPaginatedPlaylistItems,
    SpotifyPlaylist,
    SpotifyRecentlyPlayedResponse,
    SpotifySnapshotResponse,
    SpotifyTrack,
    SpotifyTracksResponse,
    SpotifyUserPlaylistsResponse,
)

logger = get_logger(__name__).bind(service="spotify_client")


def _validated[ModelT: BaseModel](
    model: type[ModelT], data: JsonDict, subject: str
) -> ModelT:
    """Boundary-validate a response body, or raise the connector-flavored error."""
    return validated(model, data, service="spotify", subject=subject)


def sanitize_search_value(value: str) -> str:
    """Make a raw artist/title safe to sit in a Spotify search query — any shape.

    Spotify's query grammar has no escape syntax for a double quote, and the
    character is destructive in both query shapes this module builds: inside a
    quoted field filter it terminates the filter early and the remainder
    degrades into loose words; in a free-text query it opens a phrase operator
    that is never closed. It is replaced by a space rather than escaped.
    Apostrophes, ampersands and non-ASCII are legal in both and are left
    untouched — GDPR exports carry them verbatim and normalising them would
    lose real matches.
    """
    return " ".join(value.replace('"', " ").split())


def field_filtered_search_query(artist: str, title: str) -> str:
    """``artist:"…" track:"…"`` — filters bound to the full artist and title.

    A Spotify field filter binds to a SINGLE term unless the value is quoted:
    unquoted, ``artist:Robert Johnson track:Come On in My Kitchen`` filters on
    artist "Robert" and track "Come", leaving every remaining word as a loose
    free-text term. Quoting binds the whole value to its filter.
    """
    return (
        f'artist:"{sanitize_search_value(artist)}" '
        f'track:"{sanitize_search_value(title)}"'
    )


def free_text_search_query(artist: str, title: str) -> str:
    """Artist and title as plain terms — no field filters, no quoting.

    Spotify's relevance ranking gets to match across all fields, which recovers
    tracks whose stored artist/title spelling differs from the caller's.

    Sanitized on exactly the same terms as the filtered query: a title like
    ``Move Your Body (12" Mix)`` would otherwise send an unbalanced quote, and
    the widening pass would fail on precisely the titles it exists to rescue.
    """
    terms = (sanitize_search_value(artist), sanitize_search_value(title))
    return " ".join(term for term in terms if term)


@define(frozen=True, slots=True)
class SpotifyTracksFetch:
    """A batch track fetch's answer, with "no answer" kept apart from "dead".

    Three outcomes per requested id, never two:

    - **answered** — the id is a key in ``tracks``;
    - **dead** — requested, and in neither field: Spotify returned 200 with
      ``null`` in that id's position, which is the endpoint saying the id is
      gone;
    - **unanswered** — the id's chunk request failed after its retries, or the
      response was too short to correlate that far, so Spotify said nothing
      about the id at all.

    Reading absence from ``tracks`` as death is the whole hazard this type
    exists to remove. One 5xx costs a *chunk* — up to 50 ids at once — and a
    caller that calls those ids dead writes no-match backoff for all fifty and
    stands search-picked recordings in for the hinted ones, permanently, on the
    strength of a request that never landed. ``unanswered`` has to be
    subtracted before anything is concluded; those ids are simply re-asked at
    full strength on the next import.
    """

    tracks: dict[str, SpotifyTrack] = field(factory=dict)
    unanswered: frozenset[str] = frozenset()


@define(slots=True)
class SpotifyAPIClient(BaseAPIClient):
    """Pure Spotify API client using native httpx2.

    Provides thin wrappers around the Spotify Web API with authentication,
    centralized retry policy, and individual API method calls. No business
    logic or complex orchestration.

    Example:
        >>> client = SpotifyAPIClient()
        >>> fetch = await client.get_tracks_batched(["4iV5W9uYEdYUVa79Axb7Rh"])
        >>> playlist_data = await client.get_playlist("37i9dQZF1DX0XUsuxWHRQd")
    """

    _SUPPRESS_ERRORS: ClassVar[tuple[type[BaseException], ...]] = (
        httpx2.HTTPStatusError,
        httpx2.RequestError,
    )

    _token_manager: SpotifyTokenManager = field(init=False, repr=False)
    _retry_policy: AsyncRetrying = field(init=False, repr=False)
    _client: httpx2.AsyncClient = field(init=False, repr=False)
    _cached_user_id: str | None = field(init=False, default=None, repr=False)

    @property
    def market(self) -> str:
        """Configured Spotify market for API requests."""
        return settings.api.spotify_market

    def __attrs_post_init__(self) -> None:
        """Initialize token manager, retry policy, and long-lived pooled client."""
        logger.debug("Initializing Spotify API client")
        from src.infrastructure.connectors._shared.token_storage import (
            get_token_storage,
        )
        from src.infrastructure.persistence.database.user_context import (
            get_current_user_id_from_context,
        )

        self._token_manager = SpotifyTokenManager(
            storage=get_token_storage(),
            user_id=get_current_user_id_from_context(),
        )
        from src.infrastructure.connectors._shared.http_client import (
            make_spotify_client,
        )
        from src.infrastructure.connectors.spotify.auth import SpotifyBearerAuth
        from src.infrastructure.connectors.spotify.error_classifier import (
            SpotifyErrorClassifier,
        )

        self._retry_policy = RetryPolicyFactory.for_service(
            "spotify",
            SpotifyErrorClassifier(),
            settings.api.spotify,
        )
        self._client = make_spotify_client(SpotifyBearerAuth(self._token_manager))

    @override
    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    @override
    def _surface_suppressed_error(self, exc: Exception) -> Exception | None:
        """A quota-exhausted 429 (PDR-003) must surface, never dissolve to None.

        The classifier already marks the shape permanent so the retry policy
        fails fast; without this seam the resulting ``HTTPStatusError`` would
        fall into ``_SUPPRESS_ERRORS`` and the caller would keep firing calls
        that are all doomed the same way — the pooled developer-account quota
        does not clear on Retry-After timescales. Mirrors how
        ``SpotifyAuthRequiredError`` escapes: by being a type suppression
        never covers. Plain 429s stay suppressed unchanged.
        """
        if (
            isinstance(exc, httpx2.HTTPStatusError)
            and exc.response.status_code == HTTPStatus.TOO_MANY_REQUESTS
            and is_quota_exhausted_response(exc.response)
        ):
            return SpotifyQuotaExhaustedError()
        return None

    # -------------------------------------------------------------------------
    # Shared request plumbing
    # -------------------------------------------------------------------------

    async def _json(
        self,
        operation: str,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        json: Mapping[str, JsonValue] | None = None,
    ) -> JsonDict | None:
        """Issue one JSON API request through ``_api_call``.

        Composes the retry policy, rate-limit pacing, context propagation,
        and error suppression of ``_api_call`` with the single-attempt
        request in ``_request_json``. Returns the parsed JSON object, or
        ``None`` when ``_api_call`` suppressed the failure.
        """
        return await self._api_call(
            operation, self._request_json, method, path, params, json
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        params: Mapping[str, str | int] | None,
        json: Mapping[str, JsonValue] | None,
    ) -> JsonDict:
        """One HTTP attempt: send, check status, parse the JSON object body."""
        response = await self._client.request(method, path, params=params, json=json)
        _ = response.raise_for_status()
        return parse_json_response(response)

    # -------------------------------------------------------------------------
    # Track API Methods
    # -------------------------------------------------------------------------

    async def get_tracks_batched(
        self,
        track_ids: list[str],
        progress_callback: Callable[[int, int, str], Awaitable[None]] | None = None,
    ) -> SpotifyTracksFetch:
        """Fetch tracks via GET /tracks?ids=, up to 50 ids per request.

        The only track fetch there is — one id is the degenerate case, asked as
        a one-element list and read back out of ``tracks``. Nothing collapses
        the fetch to a bare ``SpotifyTrack | None``, because that shape has
        nowhere to put the dead-versus-unanswered distinction below.

        One request per chunk instead of one per id: a 46k-id import drops from
        46k requests to ~920. Chunks are issued concurrently (bounded by
        ``settings.api.spotify.concurrency``) and each chunk spends exactly one
        rate-limiter token, since ``_api_call`` paces per invocation.

        ``tracks`` is keyed by REQUESTED ID, not the returned track's ``.id``.
        The distinction is the whole point when Spotify relinks an old id to a
        new one — the caller looks up the id it asked about, and compares it
        against the returned track's id to detect the redirect.

        A chunk request that fails after retries takes ~50 ids down with it,
        which is why its ids land in ``unanswered`` instead of merely going
        missing: see :class:`SpotifyTracksFetch` for what absence would
        otherwise be taken to mean.

        If progress_callback is provided, it is awaited once per completed
        chunk with (completed_tracks, total_tracks, message) — counted in
        tracks, not chunks, so callers keep a track-scale progress bar.
        Single-event-loop concurrency means the callback's closure state is
        safe under concurrent invocations.
        """
        if not track_ids:
            return SpotifyTracksFetch()

        chunks = [
            track_ids[i : i + SpotifyConstants.TRACKS_BATCH_SIZE]
            for i in range(0, len(track_ids), SpotifyConstants.TRACKS_BATCH_SIZE)
        ]
        tracks: dict[str, SpotifyTrack] = {}
        unanswered: set[str] = set()
        total = len(track_ids)
        completed = 0

        async def _fetch_chunk(chunk: list[str]) -> None:
            nonlocal completed
            try:
                fetched = await self._get_tracks_chunk(chunk)
            except SpotifyQuotaExhaustedError:
                # The pooled developer-account quota is empty (PDR-003):
                # every remaining chunk is doomed identically, and
                # "unanswered" would hide the outage. Abort the batch.
                raise
            except Exception as e:
                # Anything that escapes ``_api_call``'s suppression — a
                # response that will not parse, most likely — told us just
                # as little about this chunk's ids as a 5xx did.
                logger.warning(
                    f"Failed to fetch batch of {len(chunk)} tracks: {e}",
                    exc_info=True,
                )
                unanswered.update(chunk)
            else:
                tracks.update(fetched.tracks)
                unanswered.update(fetched.unanswered)
            completed += len(chunk)
            if progress_callback is not None:
                await progress_callback(
                    completed,
                    total,
                    f"Fetched {completed}/{total} from Spotify",
                )

        # ``unwrap`` re-raises the typed quota error bare so every catch site
        # (the middleware's 503, the importers' propagate lists) sees the
        # exception type it knows, not an ExceptionGroup.
        _ = await bounded_fan_out(
            chunks,
            _fetch_chunk,
            concurrency=settings.api.spotify.concurrency,
            unwrap=(SpotifyQuotaExhaustedError,),
        )

        return SpotifyTracksFetch(tracks=tracks, unanswered=frozenset(unanswered))

    async def _get_tracks_chunk(self, track_ids: list[str]) -> SpotifyTracksFetch:
        """One GET /tracks?ids= call, correlated back to the requested ids."""
        data = await self._api_call(
            "get_spotify_tracks_batch", self._get_tracks_batch_impl, track_ids
        )
        if data is None:
            # ``_api_call`` swallowed the failure after exhausting its retries.
            # Every id in the chunk is unanswered, not dead: one post-retry 5xx
            # or timeout — or a 400 the whole chunk inherits from a single
            # malformed id — must not be reported as fifty dead tracks.
            logger.warning(
                f"Spotify /tracks request failed for {len(track_ids)} ids — "
                f"reporting them unanswered, not dead",
                requested=len(track_ids),
            )
            return SpotifyTracksFetch(unanswered=frozenset(track_ids))

        returned = _validated(SpotifyTracksResponse, data, "a tracks batch").tracks
        if len(returned) != len(track_ids):
            # Positional alignment is the only correlation this endpoint
            # offers; a length mismatch means it stops holding partway. The
            # common prefix is still correlated, and the ids past the end of
            # the array are unanswered — reading a truncated response as a run
            # of deaths would invent them out of one malformed reply.
            logger.warning(
                "Spotify /tracks returned a differently-sized array than requested",
                requested=len(track_ids),
                returned=len(returned),
            )
        return SpotifyTracksFetch(
            tracks={
                requested_id: track
                for requested_id, track in zip(track_ids, returned, strict=False)
                if track is not None
            },
            unanswered=frozenset(track_ids[len(returned) :]),
        )

    async def _get_tracks_batch_impl(self, track_ids: list[str]) -> JsonDict | None:
        """One GET /tracks?ids= attempt, with the PDR-003 403 signature logged.

        Kept as a dedicated impl (not routed through ``_json``) because the
        403 detection below needs the raw response before ``raise_for_status``
        converts it into an exception.
        """
        response = await self._client.get(
            "/tracks",
            params={"ids": ",".join(track_ids), "market": self.market},
        )
        if response.status_code == HTTPStatus.FORBIDDEN:
            # PDR-003 decide-by trigger (b): a 403 on the batch /tracks?ids=
            # endpoint is the signature of Spotify's postponed dev-mode
            # batch-endpoint restriction taking effect, and the PDR requires it
            # to be distinguishable in prod logs from ordinary request failures
            # (which land in ``unanswered`` anonymously).
            logger.error(
                "Spotify batch GET /tracks?ids= returned 403 — dev-mode "
                "batch-endpoint restriction may be in effect; see PDR-003 "
                "(docs/decisions/PDR-003-spotify-dev-mode-batch-endpoints.md)",
                requested=len(track_ids),
            )
        _ = response.raise_for_status()
        return parse_json_response(response)

    # -------------------------------------------------------------------------
    # Search API Methods
    # -------------------------------------------------------------------------

    async def search_by_isrc(self, isrc: str) -> SpotifyTrack | None:
        """Search for a track using ISRC identifier."""
        logger.debug(f"Searching Spotify for ISRC: {isrc}")
        data = await self._json(
            "search_spotify_by_isrc",
            "GET",
            "/search",
            params={
                "q": f"isrc:{isrc}",
                "type": "track",
                "limit": 1,
                "market": self.market,
            },
        )
        if data is None:
            return None
        tracks_wrapper = data.get("tracks")
        if not isinstance(tracks_wrapper, dict):
            logger.warning("Spotify search by ISRC returned no results", isrc=isrc)
            return None
        items = tracks_wrapper.get("items")
        if not isinstance(items, list) or not items:
            logger.warning("Spotify search by ISRC returned no results", isrc=isrc)
            return None
        first = items[0]
        if not isinstance(first, dict) or not first:
            return None
        return _validated(SpotifyTrack, first, "a search result")

    async def search_track(
        self, query: str, limit: int = SpotifyConstants.SEARCH_DEFAULT_LIMIT
    ) -> list[SpotifyTrack]:
        """Send a prepared search query to ``/search`` and return the candidates.

        Returns multiple candidates so callers can rank by similarity.

        The query string is built by the caller — with
        :func:`field_filtered_search_query` or :func:`free_text_search_query` —
        and sent verbatim. The client deliberately does not re-derive it from
        artist/title: a caller that logs a query it built separately from the
        one this method assembled can print a string that never went on the
        wire, which is worse than no telemetry at all.
        """
        logger.debug(f"Searching Spotify with query: {query}")
        data = await self._json(
            "search_spotify_track",
            "GET",
            "/search",
            params={
                "q": query,
                "type": "track",
                "limit": min(limit, SpotifyConstants.SEARCH_MAX_LIMIT),
                "market": self.market,
            },
        )
        if data is None:
            return []
        tracks_wrapper = data.get("tracks")
        if not isinstance(tracks_wrapper, dict):
            return []
        items = tracks_wrapper.get("items")
        if not isinstance(items, list):
            return []
        return [
            _validated(SpotifyTrack, item, "a search result")
            for item in items
            if isinstance(item, dict)
        ]

    # -------------------------------------------------------------------------
    # Playlist Read Methods
    # -------------------------------------------------------------------------

    async def get_playlist(self, playlist_id: str) -> SpotifyPlaylist | None:
        """Fetch a Spotify playlist with basic metadata."""
        data = await self._json(
            "get_spotify_playlist",
            "GET",
            f"/playlists/{playlist_id}",
            params={"market": self.market},
        )
        return _validated(SpotifyPlaylist, data, "a playlist") if data else None

    async def get_next_page(
        self, current_page: SpotifyPaginatedPlaylistItems
    ) -> SpotifyPaginatedPlaylistItems | None:
        """Fetch next page of paginated Spotify API results.

        Spotify's "next" cursor is an absolute URL. httpx2 uses absolute URLs
        as-is when a base_url is set, so the pooled client handles them
        correctly.
        """
        if not current_page.next:
            return None

        data = await self._json("get_spotify_next_page", "GET", current_page.next)
        return (
            _validated(SpotifyPaginatedPlaylistItems, data, "a playlist page")
            if data
            else None
        )

    async def get_current_user_playlists(
        self, limit: int = 50, offset: int = 0
    ) -> SpotifyUserPlaylistsResponse | None:
        """Fetch one page of the current user's playlists.

        Metadata-only: each item's `items` node is a `{href, total}` summary,
        not a full tracks list. Scope: `playlist-read-private`. Pagination
        caps: limit max 50 (Spotify default 20), offset max 100,000.
        """
        data = await self._json(
            "get_current_user_playlists",
            "GET",
            "/me/playlists",
            params={"limit": min(limit, 50), "offset": offset},
        )
        return (
            _validated(SpotifyUserPlaylistsResponse, data, "a playlists page")
            if data
            else None
        )

    # -------------------------------------------------------------------------
    # Playlist Write Methods
    # -------------------------------------------------------------------------

    async def create_playlist(
        self, name: str, description: str = "", public: bool = False
    ) -> SpotifyPlaylist | None:
        """Create a new empty Spotify playlist for the current user.

        Uses POST /me/playlists — no user ID prefetch required.
        """
        data = await self._json(
            "create_spotify_playlist",
            "POST",
            "/me/playlists",
            json={"name": name, "public": public, "description": description},
        )
        return _validated(SpotifyPlaylist, data, "a playlist") if data else None

    async def playlist_add_items(
        self, playlist_id: str, items: list[str], position: int | None = None
    ) -> SpotifySnapshotResponse | None:
        """Add items to a Spotify playlist.

        Args:
            playlist_id: Spotify playlist ID
            items: List of track URIs to add
            position: Optional position to insert at

        Returns:
            Validated snapshot response, None if error
        """
        body: JsonDict = {"uris": items}
        if position is not None:
            body["position"] = position

        data = await self._json(
            "add_spotify_playlist_items",
            "POST",
            f"/playlists/{playlist_id}/items",
            json=body,
        )
        return _validated(SpotifySnapshotResponse, data, "a snapshot") if data else None

    async def playlist_remove_specific_occurrences_of_items(
        self,
        playlist_id: str,
        items: list[JsonDict],
        snapshot_id: str | None = None,
    ) -> SpotifySnapshotResponse | None:
        """Remove specific occurrences of items from a Spotify playlist.

        Args:
            playlist_id: Spotify playlist ID
            items: List of items with URIs and optional positions to remove
            snapshot_id: Optional snapshot ID for conflict detection

        Returns:
            Validated snapshot response, None if error
        """
        body: JsonDict = {"items": items}
        if snapshot_id is not None:
            body["snapshot_id"] = snapshot_id

        data = await self._json(
            "remove_specific_spotify_playlist_items",
            "DELETE",
            f"/playlists/{playlist_id}/items",
            json=body,
        )
        return _validated(SpotifySnapshotResponse, data, "a snapshot") if data else None

    async def playlist_reorder_items(
        self,
        playlist_id: str,
        range_start: int,
        insert_before: int,
        range_length: int = 1,
        snapshot_id: str | None = None,
    ) -> SpotifySnapshotResponse | None:
        """Reorder items in a Spotify playlist.

        Args:
            playlist_id: Spotify playlist ID
            range_start: Start position of items to move
            insert_before: Position to insert items before
            range_length: Number of items to move (default 1)
            snapshot_id: Optional snapshot ID for conflict detection

        Returns:
            Validated snapshot response, None if error
        """
        body: JsonDict = {
            "range_start": range_start,
            "insert_before": insert_before,
            "range_length": range_length,
        }
        if snapshot_id is not None:
            body["snapshot_id"] = snapshot_id

        data = await self._json(
            "reorder_spotify_playlist_items",
            "PUT",
            f"/playlists/{playlist_id}/items",
            json=body,
        )
        return _validated(SpotifySnapshotResponse, data, "a snapshot") if data else None

    async def playlist_replace_items(
        self, playlist_id: str, items: list[str]
    ) -> SpotifySnapshotResponse | None:
        """Replace all items in a Spotify playlist.

        Args:
            playlist_id: Spotify playlist ID
            items: List of track URIs to set as playlist contents

        Returns:
            Validated snapshot response, None if error
        """
        data = await self._json(
            "replace_spotify_playlist_items",
            "PUT",
            f"/playlists/{playlist_id}/items",
            json={"uris": items},
        )
        return _validated(SpotifySnapshotResponse, data, "a snapshot") if data else None

    async def playlist_change_details(
        self, playlist_id: str, name: str | None = None, description: str | None = None
    ) -> None:
        """Update Spotify playlist metadata.

        Not routed through ``_json``: the 200 response carries no body to
        parse, and errors must propagate to the caller (``suppress=()``)
        instead of dissolving into an indistinguishable ``None``.

        Args:
            playlist_id: Spotify playlist ID
            name: Optional new playlist name
            description: Optional new playlist description
        """
        body: JsonDict = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description

        if not body:
            return

        async def _put() -> None:
            response = await self._client.put(f"/playlists/{playlist_id}", json=body)
            _ = response.raise_for_status()

        _ = await self._api_call("update_spotify_playlist_metadata", _put, suppress=())

    # -------------------------------------------------------------------------
    # User Library Methods
    # -------------------------------------------------------------------------

    async def check_library_contains(self, uris: list[str]) -> dict[str, bool]:
        """Check which items are in the user's saved library.

        Calls Spotify's /me/library/contains endpoint in batches of 40.
        Accepts Spotify URIs (e.g., "spotify:track:{id}").
        Returns a dict mapping each URI to True/False.
        """
        if not uris:
            return {}

        results: dict[str, bool] = {}
        for i in range(0, len(uris), SpotifyConstants.LIBRARY_CONTAINS_BATCH_SIZE):
            batch = uris[i : i + SpotifyConstants.LIBRARY_CONTAINS_BATCH_SIZE]
            data = await self._api_call(
                "check_spotify_library_contains",
                self._check_library_contains_impl,
                batch,
            )
            if data is not None:
                results.update(zip(batch, data, strict=True))
            else:
                results.update(dict.fromkeys(batch, False))
        return results

    async def _check_library_contains_impl(self, uris: list[str]) -> list[bool]:
        """One GET /me/library/contains attempt.

        Kept as a dedicated impl (not routed through ``_json``): the endpoint
        answers with a JSON array, not an object.
        """
        response = await self._client.get(
            "/me/library/contains",
            params={"uris": ",".join(uris)},
        )
        _ = response.raise_for_status()
        return cast("list[bool]", response.json())

    async def get_saved_tracks(
        self, limit: int = 50, offset: int = 0
    ) -> JsonDict | None:
        """Fetch user's saved/liked tracks from Spotify.

        Args:
            limit: Number of tracks to fetch (max 50)
            offset: Starting position for pagination

        Returns:
            Saved tracks response, None if error
        """
        return await self._json(
            "get_spotify_saved_tracks",
            "GET",
            "/me/tracks",
            params={
                "limit": min(limit, 50),
                "offset": offset,
                "market": self.market,
            },
        )

    async def get_recently_played(
        self, *, after_ms: int | None = None, limit: int = RECENTLY_PLAYED_PAGE_LIMIT
    ) -> SpotifyRecentlyPlayedResponse | None:
        """Fetch the user's recently-played tracks (needs user-read-recently-played).

        Deliberately a SINGLE request, not the paginate-to-completion loop the
        other user-collection readers use: Spotify retains only the trailing
        ~50 plays here, and the ``after`` cursor cannot page beyond that buffer,
        so looping returns nothing extra. Continuity across polls comes from the
        stored checkpoint cursor, not from walking ``next`` (2026-07 check: the
        endpoint survived Spotify's Feb 2026 dev-mode purge with the 50-item
        window intact).

        Args:
            after_ms: Millisecond-epoch cursor; returns plays strictly after it.
                Omitted on a first poll, which yields the trailing window.
            limit: Page size, clamped to the endpoint maximum of 50.

        Returns:
            The parsed response, or None when the call failed (``_SUPPRESS_ERRORS``
            swallows the status code, so callers cannot distinguish causes — an
            empty ``items`` list is the only reliable "nothing new" signal).
        """
        params: dict[str, int] = {"limit": min(limit, RECENTLY_PLAYED_PAGE_LIMIT)}
        if after_ms is not None:
            params["after"] = after_ms
        data = await self._json(
            "get_spotify_recently_played",
            "GET",
            "/me/player/recently-played",
            params=params,
        )
        return (
            _validated(SpotifyRecentlyPlayedResponse, data, "a recently-played page")
            if data
            else None
        )

    async def get_current_user(self) -> JsonDict | None:
        """Get current Spotify user information.

        Returns:
            User data if authenticated, None otherwise
        """
        return await self._json("get_spotify_current_user", "GET", "/me")

    async def get_current_user_id(self) -> str | None:
        """Get (and cache) the current user's Spotify ID."""
        if self._cached_user_id is not None:
            return self._cached_user_id
        user_data = await self.get_current_user()
        if user_data and "id" in user_data:
            user_id = user_data["id"]
            if isinstance(user_id, str):
                self._cached_user_id = user_id
        return self._cached_user_id
