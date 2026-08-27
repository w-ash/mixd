"""Tidal provider for track matching — conservative, ISRC-only.

Matches tracks against the Tidal catalog by ISRC exclusively.
``supports_artist_title_matching = False`` keeps the base template from ever
funneling ISRC-less tracks (or ISRC misses) into a name search — the same
conservative contract as Apple's provider; the artist/title hook stays as
the v0.12.1 plug point.

The lookup is ``GET /tracks?filter[isrc]=`` — ONE code per request, looped
here, because the client rejects multi-code batching (Tidal returns one
match per code for batched filters: a silent partial answer for a 1:N
lookup).
"""

import asyncio
from typing import ClassVar, override
from uuid import UUID

from src.config import get_logger, settings
from src.domain.entities import Track
from src.domain.entities.shared import JsonValue
from src.domain.matching.isrc_validation import (
    assess_isrc_match_reliability,
    compute_duration_diff_ms,
)
from src.domain.matching.types import (
    MatchFailure,
    RawProviderMatch,
)
from src.infrastructure.connectors._shared.isrc import normalize_isrc
from src.infrastructure.connectors._shared.matching_provider import (
    BaseMatchingProvider,
)
from src.infrastructure.connectors.tidal.client import (
    TIDAL_COUNTRY_CODE,
    TidalAPIClient,
)
from src.infrastructure.connectors.tidal.conversions import tidal_duration_ms
from src.infrastructure.connectors.tidal.models import (
    TidalTrack,
    tidal_track_from_resource,
)

logger = get_logger(__name__)


class TidalMatchingProvider(BaseMatchingProvider):
    """Tidal track matching provider (ISRC only)."""

    supports_artist_title_matching: ClassVar[bool] = False

    _client: TidalAPIClient

    def __init__(self, client: TidalAPIClient) -> None:
        """Initialize with the Tidal API client."""
        self._client = client

    @property
    @override
    def service_name(self) -> str:
        """Data-plane service identifier.

        ``"tidal"`` on both planes — unlike Apple there is no package/data
        split: mappings, plays-adjacent rows, the provider registry, and the
        client's own settings/rate-limiter key all say ``"tidal"``.
        """
        return "tidal"

    @override
    async def _match_by_isrc(
        self, tracks: list[Track]
    ) -> tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]:
        """Match tracks via one catalog ISRC lookup per unique code.

        One ``filter[isrc]`` request per code (the client's contract — see
        module docstring), deduplicated across the batch. Each lookup is
        1:N: one ISRC fans out to MANY catalog tracks (one recording across
        many releases); ``_pick_candidate`` documents the pick. A code whose
        request failed is UNANSWERED, not absent — its tracks fail as
        ``API_ERROR``, never ``NO_RESULTS``.
        """
        isrc_by_track: dict[UUID, str] = {}
        for track in tracks:
            if not track.id:
                continue
            normalized = normalize_isrc(track.isrc or "")
            if normalized:
                isrc_by_track[track.id] = normalized

        candidates_by_isrc: dict[str, list[TidalTrack]] = {}
        failed_isrcs: set[str] = set()
        # Multi-ISRC batching returns one match per code, so each code is its own
        # request — bounded concurrency is what keeps a large batch off a serial
        # round trip per track. The shared rate limiter still paces each call.
        semaphore = asyncio.Semaphore(settings.api.tidal.concurrency)

        async def _lookup(code: str) -> None:
            async with semaphore:
                try:
                    resources = await self._client.get_tracks_by_isrc(
                        code, TIDAL_COUNTRY_CODE
                    )
                except Exception:
                    logger.warning(
                        f"Tidal ISRC lookup failed for {code} — "
                        f"its tracks fail as API_ERROR",
                        exc_info=True,
                    )
                    failed_isrcs.add(code)
                else:
                    candidates_by_isrc[code] = [
                        tidal_track_from_resource(resource) for resource in resources
                    ]

        async with asyncio.TaskGroup() as tg:
            for code in dict.fromkeys(isrc_by_track.values()):
                _ = tg.create_task(_lookup(code))

        return self._correlate_by_code(
            tracks,
            code_of=isrc_by_track,
            candidates_by_code=candidates_by_isrc,
            failed_codes=failed_isrcs,
            make_match=lambda track, candidates: self._create_raw_match(
                self._pick_candidate(track, candidates)
            ),
            service_label="Tidal",
            method="isrc",
            code_label="ISRC",
        )

    @staticmethod
    def _pick_candidate(track: Track, candidates: list[TidalTrack]) -> TidalTrack:
        """The 1:N pick: first duration-cross-check-passing candidate.

        All hits share the recording identity the ISRC names, so any pick is
        a correct match — the duration cross-check
        (``assess_isrc_match_reliability``) only steers the pick away from
        remaster/version outliers whose duration disagrees with ours by
        >10s. Tiebreak: candidates are taken in response order; the first
        non-suspect one wins (unknown durations pass). When every candidate
        is suspect, the first result stands and the domain evaluation owns
        acceptance.
        """
        for candidate in candidates:
            diff_ms = compute_duration_diff_ms(
                track.duration_ms, tidal_duration_ms(candidate)
            )
            if not assess_isrc_match_reliability(diff_ms).suspect:
                return candidate
        return candidates[0]

    def _create_raw_match(self, candidate: TidalTrack) -> RawProviderMatch:
        """Raw match data from a Tidal track — no business logic.

        ``match_method`` is the raw-method string ``"isrc"`` (as Spotify and
        Apple emit), NOT ``MatchMethod.ISRC_MATCH``: domain confidence
        scoring keys ISRC-grade evidence on ``ISRC_GRADE_METHODS``. The
        ``isrc_match`` provenance string belongs to mapping specs the inward
        resolver writes. No artist/album keys: the 1:N lookup does not
        side-load relationship resources, and ISRC-grade scoring reads
        ``service_data`` with ``.get`` — absent keys stay absent rather than
        carrying fabricated ``None`` facts.
        """
        service_data: dict[str, JsonValue] = {
            "title": candidate.title,
            "duration_ms": tidal_duration_ms(candidate),
            "isrc": normalize_isrc(candidate.isrc or "") or None,
        }
        return RawProviderMatch(
            connector_id=candidate.id,
            match_method="isrc",
            service_data=service_data,
        )
