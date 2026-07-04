"""Protocols for track matching services.

These protocols define contracts for matching services without depending on
external implementations, following the dependency inversion principle.
"""

from typing import Protocol

from attrs import define, field

from src.domain.entities import Track
from src.domain.repositories.uow import UnitOfWorkProtocol

from .types import ProgressCallback, ProviderMatchResult


class MatchProvider(Protocol):
    """Contract for music service providers to find track matches.

    Providers communicate with external APIs and transform responses into
    raw provider data without applying business logic.

    Implemented by infrastructure-layer matching providers (Spotify, Last.fm,
    MusicBrainz). Domain layer owns this contract per dependency inversion.
    """

    async def fetch_raw_matches_for_tracks(
        self,
        tracks: list[Track],
        progress_callback: ProgressCallback | None = None,
        **additional_options: object,
    ) -> ProviderMatchResult:
        """Fetch raw matches for tracks from external service.

        Args:
            tracks: Internal Track objects to match
            progress_callback: Optional async callback invoked with
                (completed_count, total, description) after each matching phase.
            **additional_options: Provider-specific options

        Returns:
            ProviderMatchResult with successful matches and structured failure information.

        Note:
            Handle retries and rate limiting internally. Return structured failure
            information for individual track failures rather than raising exceptions.
        """
        ...

    @property
    def service_name(self) -> str:
        """Service identifier (e.g., 'spotify', 'lastfm')."""
        ...


@define(frozen=True, slots=True)
class ReuseExisting:
    """Discovery resolved to an existing canonical — reuse it, create nothing new.

    The caller maps its own connector identifier(s) onto ``track`` instead of
    building a fresh canonical. When ``spotify_id`` is set, the caller also
    creates that Spotify mapping on the reused canonical — the ISRC-collision
    path found a Spotify id that belongs on the existing owner. When it is
    ``None`` the reused canonical already carries the Spotify mapping (the
    ListenBrainz path found the canonical *by* that Spotify id, so no new
    mapping is needed).
    """

    track: Track
    spotify_id: str | None = None
    confidence: int = 0
    match_method: str = ""
    metadata: dict[str, object] = field(factory=dict)
    confidence_evidence: dict[str, object] | None = None


@define(frozen=True, slots=True)
class NewMapping:
    """Discovery found a Spotify match for a brand-new canonical.

    The caller builds its canonical once, then creates the Spotify mapping and
    backfills the canonical from ``album`` / ``duration_ms`` / ``isrc``. ``isrc``
    is ``None`` when the match was a *suspect* ISRC collision — the contested
    code was stripped and a review queued, so the new canonical never claims it.
    """

    spotify_id: str
    confidence: int
    match_method: str
    metadata: dict[str, object] = field(factory=dict)
    confidence_evidence: dict[str, object] | None = None
    album: str | None = None
    duration_ms: int | None = None
    isrc: str | None = None


@define(frozen=True, slots=True)
class Nothing:
    """Discovery found nothing usable — the caller proceeds with its own canonical."""


type DiscoveryOutcome = ReuseExisting | NewMapping | Nothing
"""What a :class:`CrossDiscoveryProvider` tells its caller to do."""


class CrossDiscoveryProvider(Protocol):
    """Contract for cross-service track discovery.

    Allows one connector (e.g. Last.fm) to discover tracks in another
    service (e.g. Spotify) without coupling to that service's concrete
    implementation. The wiring happens at the composition root.
    """

    async def discover(
        self,
        probe_track: Track,
        artist_name: str,
        track_name: str,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> DiscoveryOutcome:
        """Decide how an unsaved probe track should be mapped cross-service.

        ``probe_track`` is an in-memory canonical the caller has NOT persisted
        yet — reuse-before-create means discovery runs before any row is
        written. Returns a :data:`DiscoveryOutcome`: reuse an existing canonical
        (:class:`ReuseExisting`), create a new Spotify mapping + backfill
        (:class:`NewMapping`), or do nothing (:class:`Nothing`). The provider
        does not mutate the caller's canonical; it may perform its own side
        effects (e.g. queuing an ISRC review).
        """
        ...
