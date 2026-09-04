"""Track-like repository protocol.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.domain.entities import (
    TrackLike,
)


class LikeRepositoryProtocol(Protocol):
    """Repository interface for like persistence operations."""

    def get_track_likes(
        self, track_id: UUID, *, user_id: str, services: list[str] | None = None
    ) -> Awaitable[list[TrackLike]]:
        """Get likes for a track across services."""
        ...

    def save_track_likes_batch(
        self,
        likes: list[tuple[UUID, str, bool, datetime | None, datetime | None]],
        *,
        user_id: str,
    ) -> Awaitable[list[TrackLike]]:
        """Save multiple track likes in bulk.

        Args:
            likes: List of (track_id, service, is_liked, last_synced, liked_at) tuples.
            user_id: Owner's user ID.

        Returns:
            List of saved TrackLike domain objects.
        """
        ...

    def get_all_liked_tracks(
        self,
        service: str,
        *,
        user_id: str,
        is_liked: bool = True,
        sort_by: str | None = None,
    ) -> Awaitable[list[TrackLike]]:
        """Get all liked tracks for a service.

        Args:
            service: Service to get likes from
            user_id: Owner's user ID.
            is_liked: Filter by like status
            sort_by: Optional sorting method (liked_at_desc, liked_at_asc, title_asc, random)
        """
        ...

    def get_liked_status_batch(
        self,
        track_ids: list[UUID],
        services: list[str],
        *,
        user_id: str,
    ) -> Awaitable[dict[UUID, dict[str, bool]]]:
        """Check like status for multiple tracks across services.

        Returns:
            Mapping of track_id → {service: is_liked}.
            Missing entries mean no like record exists (treat as False).
        """
        ...

    def count_liked_tracks(
        self, service: str, *, user_id: str, is_liked: bool = True
    ) -> Awaitable[int]:
        """Count tracks with the given like status for a service.

        More efficient than get_all_liked_tracks when only the count is needed,
        as it avoids hydrating domain objects.

        Args:
            service: Service to count likes for
            user_id: Owner's user ID.
            is_liked: Filter by like status
        """
        ...

    def count_liked_tracks_by_service(
        self,
        services: Sequence[str],
        *,
        user_id: str,
        is_liked: bool = True,
    ) -> Awaitable[dict[str, int]]:
        """Count likes per service in one grouped query.

        Batch counterpart to ``count_liked_tracks``. Every requested service
        is present in the result; one with no matching rows maps to 0.

        Args:
            services: Services to count likes for.
            user_id: Owner's user ID.
            is_liked: Filter by like status.
        """
        ...

    def get_unsynced_likes(
        self,
        source_service: str,
        target_service: str,
        *,
        user_id: str,
        is_liked: bool = True,
        since_timestamp: datetime | None = None,
    ) -> Awaitable[list[TrackLike]]:
        """Get tracks liked in source_service but not in target_service."""
        ...
