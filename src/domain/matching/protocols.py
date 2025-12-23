"""Protocols for track matching services.

These protocols define contracts for matching services without depending on
external implementations, following the dependency inversion principle.
"""

from __future__ import annotations

from typing import Any, Protocol

from .types import MatchResultsById


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
