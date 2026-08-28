"""Apple Music provider for track matching — conservative, ISRC-only.

Matches tracks against the Apple Music catalog by ISRC exclusively. The
``IsrcOnly`` strategy keeps the workflow from ever funneling ISRC-less
tracks (or ISRC misses) into a name search: Apple's catalog search has no
field filters, and an unguarded free-text match would mint cross-service
identities on title similarity alone. Artist/title matching lands with the
v0.12.1 alias-aware comparator, as a new hook wired into an
``IsrcThenArtistTitle`` strategy.
"""

from typing import override
from uuid import UUID

from src.config import get_logger
from src.domain.entities import Track
from src.domain.entities.shared import JsonValue
from src.domain.matching.types import (
    MatchFailure,
    MatchFailureReason,
    RawProviderMatch,
)
from src.infrastructure.connectors._shared.failure_handling import (
    create_and_log_failure,
)
from src.infrastructure.connectors._shared.isrc import normalize_isrc
from src.infrastructure.connectors._shared.matching_provider import (
    BaseMatchingProvider,
    IsrcOnly,
    MatchStrategy,
)
from src.infrastructure.connectors.apple_music.client import AppleMusicAPIClient
from src.infrastructure.connectors.apple_music.models import AppleMusicSong
from src.infrastructure.connectors.apple_music.storefront import resolve_storefront

logger = get_logger(__name__)


class AppleMusicMatchingProvider(BaseMatchingProvider):
    """Apple Music track matching provider (ISRC only)."""

    _client: AppleMusicAPIClient

    def __init__(self, client: AppleMusicAPIClient) -> None:
        """Initialize with the Apple Music API client."""
        self._client = client

    @property
    @override
    def service_name(self) -> str:
        """Data-plane service identifier.

        ``"apple"`` deliberately, not the ``"apple_music"`` package key: this
        name feeds only logging context and ``MatchFailure.service`` — the
        rate limiter and settings lookups key off the *client package* name
        (``service_name_for_client`` → ``settings.api.apple_music``) inside
        ``BaseAPIClient``, below this provider. Mappings, plays, and the
        resolver registry all say ``"apple"``.
        """
        return "apple"

    @override
    def _match_strategy(self) -> MatchStrategy:
        """ISRC exclusively — ISRC-less tracks fail without a name search."""
        return IsrcOnly(match_by_isrc=self._match_by_isrc)

    async def _match_by_isrc(
        self, tracks: list[Track]
    ) -> tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]:
        """Match tracks via one batched catalog ISRC lookup.

        Chunking (25 codes per request) is the client's job — this method
        hands the whole batch over in a single call. Correlation is by
        normalized ``attributes.isrc``, never by position: one ISRC can fan
        out to MANY catalog songs (one recording across many releases) and
        unmatched codes return nothing (200 with empty data, not an error).
        First hit per ISRC wins — all hits share the recording identity the
        ISRC names, so any pick yields a correct match; refining the pick
        (e.g. preferring the original release) is a metadata-quality nicety,
        not a correctness issue. ISRCs in a failed chunk are unanswered, not
        absent — they fail as ``API_ERROR``.
        """
        storefront = await resolve_storefront(self._client)
        if storefront is None:
            return {}, [
                create_and_log_failure(
                    track_id=track.id,
                    reason=MatchFailureReason.API_ERROR,
                    service=self.service_name,
                    method="isrc",
                    details="No Apple Music storefront available "
                    "(user authorization missing or Apple unreachable)",
                )
                for track in tracks
                if track.id
            ]

        isrc_by_track = self._normalized_isrc_by_track(tracks)

        lookup = await self._client.get_songs_by_isrc(
            storefront, list(dict.fromkeys(isrc_by_track.values()))
        )
        # ISRCs whose chunk request failed are UNANSWERED, not absent — they
        # must fail as API_ERROR (matching the no-storefront path), never as
        # NO_RESULTS, which would read as "absent from the catalog".
        failed_isrcs = set(lookup.failed_values)
        song_by_isrc: dict[str, AppleMusicSong] = {}
        for song in lookup.songs:
            isrc = normalize_isrc(song.attributes.isrc or "")
            if isrc and isrc not in song_by_isrc:
                song_by_isrc[isrc] = song

        return self._correlate_by_code(
            tracks,
            code_of=isrc_by_track,
            candidates_by_code=song_by_isrc,
            failed_codes=failed_isrcs,
            make_match=lambda _track, song: self._create_raw_match(song),
            service_label="Apple Music",
            method="isrc",
            code_label="ISRC",
            # One request carries a chunk of ISRCs, so its failure is theirs all.
            batched=True,
        )

    def _create_raw_match(self, song: AppleMusicSong) -> RawProviderMatch:
        """Raw match data from an Apple Music song — no business logic.

        ``match_method`` is the raw-method string ``"isrc"`` (as Spotify
        emits), NOT ``MatchMethod.ISRC_MATCH``: domain confidence scoring
        keys ISRC-grade evidence on ``ISRC_GRADE_METHODS = ("isrc", "mbid")``.
        The ``isrc_match`` provenance string belongs to mapping specs the
        inward resolver writes.
        """
        attributes = song.attributes
        service_data: dict[str, JsonValue] = {
            "title": attributes.name,
            "artist": attributes.artist_name,
            "album": attributes.album_name or None,
            "duration_ms": attributes.duration_in_millis or None,
            "isrc": normalize_isrc(attributes.isrc or ""),
            "release_date": attributes.release_date,
        }
        return RawProviderMatch(
            connector_id=song.id,
            match_method="isrc",
            service_data=service_data,
        )
