"""MusicBrainz API client — native async httpx wrapper.

Provides a thin wrapper around the MusicBrainz JSON API with:
- Rate limiting (1 request/second per API policy)
- Centralized retry policy via tenacity
- ISRC lookup via dedicated ``/isrc/{isrc}`` endpoint
- Recording search via Lucene query syntax

No authentication required — MusicBrainz read-only endpoints are public.
"""

# pyright: reportAny=false

import asyncio
import time
from typing import ClassVar, override

from attrs import define, field
import httpx
from tenacity import AsyncRetrying

from src.config import get_logger, settings
from src.infrastructure.connectors._shared.http_client import (
    make_musicbrainz_client,
)
from src.infrastructure.connectors._shared.retry_policies import (
    RetryConfig,
    RetryPolicyFactory,
)
from src.infrastructure.connectors.base import BaseAPIClient
from src.infrastructure.connectors.musicbrainz.models import MusicBrainzRecording

logger = get_logger(__name__).bind(service="musicbrainz_client")


@define(slots=True)
class MusicBrainzAPIClient(BaseAPIClient):
    """Pure MusicBrainz API client with rate limiting and centralized retry policy.

    Uses native httpx AsyncClient instead of musicbrainzngs, providing true
    async I/O and consistent httpx error types for classification.
    """

    _SUPPRESS_ERRORS: ClassVar[tuple[type[BaseException], ...]] = (
        httpx.HTTPStatusError,
        httpx.RequestError,
    )

    _client: httpx.AsyncClient = field(init=False, repr=False)
    _last_request_time: float = field(default=0.0, init=False, repr=False)
    _request_lock: asyncio.Lock = field(factory=asyncio.Lock, init=False, repr=False)
    _retry_policy: AsyncRetrying = field(init=False, repr=False)

    def __attrs_post_init__(self) -> None:
        """Initialize httpx client and retry policy."""
        from src.infrastructure.connectors.musicbrainz.error_classifier import (
            MusicBrainzErrorClassifier,
        )

        self._client = make_musicbrainz_client()
        self._retry_policy = RetryPolicyFactory.create_policy(
            RetryConfig(
                service_name="musicbrainz",
                classifier=MusicBrainzErrorClassifier(),
                max_attempts=settings.api.musicbrainz_retry_count,
                wait_multiplier=settings.api.musicbrainz_retry_base_delay,
                wait_max=settings.api.musicbrainz_retry_max_delay,
                include_httpx_errors=True,
            )
        )

    @property
    def connector_name(self) -> str:
        """Service identifier for this connector."""
        return "musicbrainz"

    @override
    async def aclose(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()

    # ── ISRC Lookup ──────────────────────────────────────────────────────

    async def get_recording_by_isrc(self, isrc: str) -> str | None:
        """Get recording MBID by ISRC with rate limiting."""
        return await self._api_call(
            "musicbrainz_get_recording_by_isrc",
            self._get_recording_by_isrc_impl,
            isrc,
        )

    async def _get_recording_by_isrc_impl(self, isrc: str) -> str | None:
        """Look up ISRC via the dedicated /isrc/{isrc} endpoint."""
        if not isrc:
            return None

        response = await self._rate_limited_request(
            f"/isrc/{isrc}",
            params={"inc": "artist-credits+releases"},
        )
        response.raise_for_status()
        data = response.json()

        recordings = data.get("recordings", [])
        if recordings:
            mbid = recordings[0].get("id")
            logger.debug(f"Found MBID {mbid} for ISRC {isrc}")
            return mbid

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
        response = await self._rate_limited_request(
            "/recording",
            params={"query": query, "limit": "1"},
        )
        response.raise_for_status()
        data = response.json()

        recordings = data.get("recordings", [])
        if recordings:
            return MusicBrainzRecording.model_validate(recordings[0])
        return None

    # ── Rate Limiting ────────────────────────────────────────────────────

    async def _rate_limited_request(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> httpx.Response:
        """Execute GET request with 1-request-per-second rate limiting."""
        async with self._request_lock:
            current_time = time.time()
            time_since_last = current_time - self._last_request_time

            if time_since_last < 1.0:
                sleep_time = 1.0 - time_since_last
                logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s")
                await asyncio.sleep(sleep_time)

            # Update timestamp inside lock before releasing to prevent
            # concurrent requests from bypassing the rate limit.
            self._last_request_time = time.time()

        return await self._client.get(path, params=params)
