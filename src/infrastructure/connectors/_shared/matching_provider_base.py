"""Base provider protocol for track matching services.

This module defines the contract that all music service providers must implement
to participate in the track matching system.
"""

from __future__ import annotations

from typing import Any, Protocol

from src.domain.matching.types import ProviderMatchResult


class MatchProvider(Protocol):
    """Contract for music service providers to find track matches.

    Providers communicate with external APIs and transform responses into
    raw provider data without applying business logic.
    """

    async def fetch_raw_matches_for_tracks(
        self,
        tracks: list[Any],  # Track objects - avoiding import for simplicity
        **additional_options: Any,
    ) -> ProviderMatchResult:
        """Fetch raw matches for tracks from external service.

        Args:
            tracks: Internal Track objects to match
            **additional_options: Provider-specific options

        Returns:
            ProviderMatchResult with successful matches and structured failure information.

        Raises:
            Exception: Only for unrecoverable infrastructure errors.

        Note:
            Handle retries and rate limiting internally. Return structured failure
            information for individual track failures rather than raising exceptions.
            This method returns raw data only - confidence scoring and business
            decisions are handled by the domain layer.
        """
        ...

    @property
    def service_name(self) -> str:
        """Service identifier (e.g., 'spotify', 'lastfm')."""
        ...
