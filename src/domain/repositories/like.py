"""Track-like repository protocol.

A like is a presence row: a ``(user_id, track_id, service)`` row exists
while the track is liked on that service and is deleted when it is not.
Every query here reads presence; none filters on a status flag.
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
        likes: list[tuple[UUID, str, datetime | None]],
        *,
        user_id: str,
    ) -> Awaitable[list[TrackLike]]:
        """Insert or refresh like rows in bulk.

        Args:
            likes: List of (track_id, service, liked_at) tuples. A ``None``
                ``liked_at`` is stamped with the current time.
            user_id: Owner's user ID.

        Returns:
            List of saved TrackLike domain objects.
        """
        ...

    def delete_track_likes_batch(
        self,
        likes: list[tuple[UUID, str]],
        *,
        user_id: str,
    ) -> Awaitable[int]:
        """Delete like rows in bulk.

        Args:
            likes: List of (track_id, service) pairs. Pairs with no row are
                ignored.
            user_id: Owner's user ID.

        Returns:
            Number of rows deleted.
        """
        ...

    def get_all_liked_tracks(
        self,
        service: str,
        *,
        user_id: str,
        sort_by: str | None = None,
    ) -> Awaitable[list[TrackLike]]:
        """Get all liked tracks for a service.

        Args:
            service: Service to get likes from
            user_id: Owner's user ID.
            sort_by: Optional sorting method (liked_at_desc, liked_at_asc, title_asc, random)
        """
        ...

    def get_liked_status_batch(
        self,
        track_ids: list[UUID],
        services: list[str],
        *,
        user_id: str,
    ) -> Awaitable[dict[UUID, set[str]]]:
        """Find which of the given services each track is liked on.

        Returns:
            Mapping of track_id → set of services with a like row. A track
            with no like on any requested service is absent.
        """
        ...

    def count_liked_tracks(self, service: str, *, user_id: str) -> Awaitable[int]:
        """Count liked tracks for a service.

        More efficient than get_all_liked_tracks when only the count is needed,
        as it avoids hydrating domain objects.

        Args:
            service: Service to count likes for
            user_id: Owner's user ID.
        """
        ...

    def count_liked_tracks_by_service(
        self,
        services: Sequence[str],
        *,
        user_id: str,
    ) -> Awaitable[dict[str, int]]:
        """Count likes per service in one grouped query.

        Batch counterpart to ``count_liked_tracks``. Every requested service
        is present in the result; one with no matching rows maps to 0.

        Args:
            services: Services to count likes for.
            user_id: Owner's user ID.
        """
        ...

    def get_unsynced_likes(
        self,
        source_service: str,
        target_service: str,
        *,
        user_id: str,
        since_timestamp: datetime | None = None,
    ) -> Awaitable[list[TrackLike]]:
        """Get tracks liked in source_service but not in target_service."""
        ...
