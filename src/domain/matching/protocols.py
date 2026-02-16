"""Protocols for track matching services.

These protocols define contracts for matching services without depending on
external implementations, following the dependency inversion principle.
"""

from typing import Any, Protocol

from .types import MatchResultsById, ProviderMatchResult


class MatchingService(Protocol):
    """Protocol for services that can match tracks to external services."""

    async def match_tracks(
        self,
        track_list: Any,  # TrackList type - avoiding import
        connector: str,
        connector_instance: Any,
    ) -> MatchResultsById:
        """Match tracks to an external service.

        Args:
            track_list: List of tracks to match
            connector: Name of the external service
            connector_instance: Service connector implementation

        Returns:
            Dictionary mapping track IDs to match results
        """
        ...


class MatchProvider(Protocol):
    """Contract for music service providers to find track matches.

    Providers communicate with external APIs and transform responses into
    raw provider data without applying business logic.

    Implemented by infrastructure-layer matching providers (Spotify, Last.fm,
    MusicBrainz). Domain layer owns this contract per dependency inversion.
    """

    async def fetch_raw_matches_for_tracks(
        self,
        tracks: list[Any],
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


class TrackData(Protocol):
    """Protocol for track data objects used in matching."""

    @property
    def title(self) -> str | None:
        """Track title."""
        ...

    @property
    def artists(self) -> list[Any]:
        """List of artist objects or names."""
        ...

    @property
    def duration_ms(self) -> int | None:
        """Track duration in milliseconds."""
        ...

    @property
    def isrc(self) -> str | None:
        """International Standard Recording Code."""
        ...

    @property
    def id(self) -> int | None:
        """Internal track ID."""
        ...
