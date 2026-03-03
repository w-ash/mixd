"""Protocols for track matching services.

These protocols define contracts for matching services without depending on
external implementations, following the dependency inversion principle.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: service_metadata, raw_data dicts, factory patterns

from typing import Any, Protocol

from src.domain.entities import Track

from .types import ProviderMatchResult


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
        **additional_options: Any,
    ) -> ProviderMatchResult:
        """Fetch raw matches for tracks from external service.

        Args:
            tracks: Internal Track objects to match
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
