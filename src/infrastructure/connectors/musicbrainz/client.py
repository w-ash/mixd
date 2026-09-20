"""MusicBrainz API client — native async httpx2 wrapper.

Provides a thin wrapper around the MusicBrainz JSON API with:
- Process-wide pacing via the shared ``ConnectorRateLimiter``
  (``settings.api.musicbrainz.rate_limit``, the documented 1 req/s policy),
  applied by ``BaseAPIClient._api_call``
- Centralized retry policy via tenacity
- ISRC lookup via dedicated ``/isrc/{isrc}`` endpoint
- Recording search via Lucene query syntax
- Artist search (``artist:`` OR ``alias:``) and artist lookup with
  ``inc=aliases+url-rels``

No authentication required — MusicBrainz read-only endpoints are public.
"""

from collections.abc import Mapping
from typing import ClassVar, Final, override

from attrs import define, field
import httpx2
from tenacity import AsyncRetrying

from src.config import get_logger, settings
from src.infrastructure.connectors._shared.http_client import (
    make_musicbrainz_client,
    parse_json_response,
)
from src.infrastructure.connectors._shared.retry_policies import (
    RetryPolicyFactory,
)
from src.infrastructure.connectors.base import BaseAPIClient
from src.infrastructure.connectors.musicbrainz.models import (
    MusicBrainzArtist,
    MusicBrainzRecording,
)

logger = get_logger(__name__).bind(service="musicbrainz_client")

# Enough hits to disambiguate a colliding name (Justice, Jungle, Bob Moses and
# Tourist collide on every service) without paying for a long page at 1 req/s.
_ARTIST_SEARCH_LIMIT: Final = 5

# Lucene syntax characters that would otherwise turn a name into an operator.
# Quotes and backslashes are the two that appear in real artist names.
_LUCENE_ESCAPES: Final[Mapping[str, str]] = {"\\": "\\\\", '"': '\\"'}


def _escape_lucene(value: str) -> str:
    """Make *value* safe inside a quoted Lucene phrase."""
    return "".join(_LUCENE_ESCAPES.get(char, char) for char in value)


@define(slots=True)
class MusicBrainzAPIClient(BaseAPIClient):
    """Pure MusicBrainz API client with centralized retry policy.

    Uses native httpx2 AsyncClient instead of musicbrainzngs, providing true
    async I/O and consistent httpx2 error types for classification. Pacing
    comes from the shared per-service rate limiter inside ``_api_call``.
    """

    _SUPPRESS_ERRORS: ClassVar[tuple[type[BaseException], ...]] = (
        httpx2.HTTPStatusError,
        httpx2.RequestError,
    )

    _client: httpx2.AsyncClient = field(init=False, repr=False)
    _retry_policy: AsyncRetrying = field(init=False, repr=False)

    def __attrs_post_init__(self) -> None:
        """Initialize httpx2 client and retry policy."""
        from src.infrastructure.connectors.musicbrainz.error_classifier import (
            MusicBrainzErrorClassifier,
        )

        self._client = make_musicbrainz_client()
        self._retry_policy = RetryPolicyFactory.for_service(
            "musicbrainz",
            MusicBrainzErrorClassifier(),
            settings.api.musicbrainz,
            include_httpx_errors=True,
        )

    @property
    def connector_name(self) -> str:
        """Service identifier for this connector."""
        return "musicbrainz"

    @override
    async def aclose(self) -> None:
        """Close the underlying httpx2 client."""
        await self._client.aclose()

    # ── ISRC Lookup ──────────────────────────────────────────────────────

    async def get_recording_by_isrc(self, isrc: str) -> MusicBrainzRecording | None:
        """Get the full recording (MBID, title, credits, length) by ISRC."""
        return await self._api_call(
            "musicbrainz_get_recording_by_isrc",
            self._get_recording_by_isrc_impl,
            isrc,
        )

    async def _get_recording_by_isrc_impl(
        self, isrc: str
    ) -> MusicBrainzRecording | None:
        """Look up ISRC via the dedicated /isrc/{isrc} endpoint.

        The response already carries title/artist-credit/length — return the
        validated recording so matching can score against real metadata
        instead of an empty payload.
        """
        if not isrc:
            return None

        response = await self._client.get(
            f"/isrc/{isrc}",
            params={"inc": "artist-credits+releases"},
        )
        response.raise_for_status()
        data = parse_json_response(response)

        recordings_val = data.get("recordings")
        if not isinstance(recordings_val, list) or not recordings_val:
            logger.debug(f"No recording found for ISRC {isrc}")
            return None

        first = recordings_val[0]
        if isinstance(first, dict):
            recording = MusicBrainzRecording.model_validate(first)
            logger.debug(f"Found MBID {recording.id} for ISRC {isrc}")
            return recording

        logger.debug(f"No recording found for ISRC {isrc}")
        return None

    # ── Recording Search ─────────────────────────────────────────────────

    async def search_recording(
        self, artist: str, title: str
    ) -> MusicBrainzRecording | None:
        """Search for recording by artist and title."""
        return await self._api_call(
            "musicbrainz_search_recording",
            self._search_recording_impl,
            artist,
            title,
        )

    async def _search_recording_impl(
        self, artist: str, title: str
    ) -> MusicBrainzRecording | None:
        """Search via Lucene query on /recording endpoint."""
        if not artist or not title:
            return None

        query = f'recording:"{title}" AND artist:"{artist}"'
        response = await self._client.get(
            "/recording",
            params={"query": query, "limit": "1"},
        )
        response.raise_for_status()
        data = parse_json_response(response)

        recordings_val = data.get("recordings")
        if isinstance(recordings_val, list) and recordings_val:
            return MusicBrainzRecording.model_validate(recordings_val[0])
        return None

    # ── Artist Search and Lookup ─────────────────────────────────────────

    async def search_artist(
        self, name: str, *, limit: int = _ARTIST_SEARCH_LIMIT
    ) -> list[MusicBrainzArtist]:
        """Search for artists by name, ranked by MusicBrainz relevance.

        Returns an empty list when the search finds nothing or the request
        fails after retries — a caller cannot act on the difference, and an
        artist is never minted from a name search alone.
        """
        results = await self._api_call(
            "musicbrainz_search_artist",
            self._search_artist_impl,
            name,
            limit,
        )
        return results if results is not None else []

    async def _search_artist_impl(
        self, name: str, limit: int
    ) -> list[MusicBrainzArtist]:
        """Search via Lucene query on the /artist endpoint.

        The query covers ``artist:`` **and** ``alias:``. The fielded ``artist:``
        search alone ranks "Kanye West Tribute Band" above the renamed primary
        "Ye" and returns nothing at all for "STRFKR", whose only entry is an
        alias — both observed in the 51-artist census.
        """
        if not name:
            return []

        escaped = _escape_lucene(name)
        query = f'artist:"{escaped}" OR alias:"{escaped}"'
        response = await self._client.get(
            "/artist",
            params={"query": query, "limit": str(limit)},
        )
        response.raise_for_status()
        data = parse_json_response(response)

        artists_val = data.get("artists")
        if not isinstance(artists_val, list):
            return []
        return [
            MusicBrainzArtist.model_validate(entry)
            for entry in artists_val
            if isinstance(entry, dict)
        ]

    async def get_artist(self, mbid: str) -> MusicBrainzArtist | None:
        """Get one artist by MBID with its aliases and external URL relations."""
        return await self._api_call(
            "musicbrainz_get_artist",
            self._get_artist_impl,
            mbid,
        )

    async def _get_artist_impl(self, mbid: str) -> MusicBrainzArtist | None:
        """Look up /artist/{mbid} with ``inc=aliases+url-rels``.

        One request carries both halves artist resolution needs: the alias
        names that feed the equivalence cache, and the url-rels that seed
        Spotify/Discogs/Apple/Tidal mappings.
        """
        if not mbid:
            return None

        response = await self._client.get(
            f"/artist/{mbid}",
            params={"inc": "aliases+url-rels"},
        )
        response.raise_for_status()
        data = parse_json_response(response)

        if not data.get("id"):
            logger.debug(f"No artist found for MBID {mbid}")
            return None
        return MusicBrainzArtist.model_validate(data)
