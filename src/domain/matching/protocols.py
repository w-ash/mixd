"""Protocols for track matching services.

These protocols define contracts for matching services without depending on
external implementations, following the dependency inversion principle.
"""

from typing import Protocol

from src.domain.entities import Track
from src.domain.repositories import UnitOfWorkProtocol

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


class CrossDiscoveryProvider(Protocol):
    """Contract for cross-service track discovery.

    Allows one connector (e.g. Last.fm) to discover tracks in another
    service (e.g. Spotify) without coupling to that service's concrete
    implementation. The wiring happens at the composition root.
    """

    async def attempt_discovery(
        self,
        track: Track,
        artist_name: str,
        track_name: str,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> bool:
        """Attempt to discover and map a track in another service.

        Returns True if a mapping was successfully created.
        """
        ...
