"""Base provider protocol for track matching services.

This module defines the contract that all music service providers must implement
to participate in the track matching system.
"""

from typing import Any, Protocol

from src.domain.matching.types import RawProviderMatch


class MatchProvider(Protocol):
    """Contract for music service providers to find track matches.

    Providers communicate with external APIs and transform responses into
    raw provider data without applying business logic.
    """

    async def fetch_raw_matches_for_tracks(
        self,
        tracks: list[Any],  # Track objects - avoiding import for simplicity
        **additional_options: Any,
    ) -> dict[int, RawProviderMatch]:
        """Fetch raw matches for tracks from external service.

        Args:
            tracks: Internal Track objects to match
            **additional_options: Provider-specific options

        Returns:
            Track IDs mapped to raw provider match data (no business logic applied).

        Raises:
            Exception: Unrecoverable errors (network failures, auth issues).

        Note:
            Handle retries and rate limiting internally. Omit failed matches
            from results rather than raising exceptions. This method returns
            raw data only - confidence scoring and business decisions are
            handled by the domain layer.
        """
        ...

    @property
    def service_name(self) -> str:
        """Service identifier (e.g., 'spotify', 'lastfm')."""
        ...
