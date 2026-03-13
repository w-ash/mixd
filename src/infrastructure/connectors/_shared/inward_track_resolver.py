"""Shared 'resolve inward' pattern: external connector IDs → canonical tracks.

Both Spotify and Last.fm play resolvers follow the same two-phase pattern:
1. Bulk lookup existing connector mappings (fast path)
2. Batch-create missing tracks + save connector mappings

This base class captures that shared pattern while letting subclasses define
connector-specific creation logic (Spotify batches API calls, Last.fm is sequential).
"""

from abc import ABC, abstractmethod

from attrs import define

from src.config import get_logger
from src.domain.entities import Track
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True)
class TrackResolutionMetrics:
    """Outcome counts from an inward resolution pass."""

    existing: int = 0
    reused: int = 0
    created: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.existing + self.reused + self.created + self.failed


class InwardTrackResolver(ABC):
    """Shared 'resolve inward' pattern: external IDs → canonical tracks.

    Subclasses provide:
    - connector_name: str property (e.g. "spotify", "lastfm")
    - _normalize_id(raw_id) → connector_track_identifier for DB lookup
    - _create_tracks_batch(missing_ids, uow) → dict mapping ID → Track
    """

    @property
    @abstractmethod
    def connector_name(self) -> str:
        """Service identifier for connector lookups (e.g. 'spotify', 'lastfm')."""
        ...

    @abstractmethod
    def _normalize_id(self, raw_id: str) -> str:
        """Normalize a raw external ID for dedup and DB lookup."""
        ...

    @abstractmethod
    async def _create_tracks_batch(
        self,
        missing_ids: list[str],
        uow: UnitOfWorkProtocol,
    ) -> dict[str, Track]:
        """Create canonical tracks for IDs not found in existing mappings.

        Args:
            missing_ids: Normalized IDs that had no existing connector mapping.
            uow: Unit of work for database operations.

        Returns:
            Dict mapping normalized ID → newly created Track.
            IDs absent from the result are counted as failures.
        """
        ...

    async def _reuse_existing_canonical_tracks(
        self,
        missing_ids: list[str],  # noqa: ARG002
        uow: UnitOfWorkProtocol,  # noqa: ARG002
    ) -> dict[str, Track]:
        """Phase 1.5: Reuse existing canonical tracks before creating new ones.

        Override in subclasses that can extract title/artist from their IDs
        to search for existing canonical tracks and create connector mappings.
        Default: no reuse (returns empty dict).
        """
        return {}

    async def resolve_to_canonical_tracks(
        self,
        connector_ids: list[str],
        uow: UnitOfWorkProtocol,
    ) -> tuple[dict[str, Track], TrackResolutionMetrics]:
        """Resolve external connector IDs to canonical tracks.

        Phase 1: Bulk lookup existing mappings via find_tracks_by_connectors.
        Phase 2: Batch-create missing tracks via subclass hook.

        Args:
            connector_ids: Raw external IDs (will be normalized).
            uow: Unit of work for database operations.

        Returns:
            Tuple of (normalized_id → Track mapping, resolution metrics).
        """
        if not connector_ids:
            return {}, TrackResolutionMetrics()

        # Normalize + deduplicate
        unique_ids = list({self._normalize_id(cid) for cid in connector_ids})

        # Phase 1: Bulk lookup existing connector mappings
        connections = [(self.connector_name, uid) for uid in unique_ids]
        existing_by_connector = (
            await uow.get_connector_repository().find_tracks_by_connectors(connections)
        )

        # Map connector results back to normalized IDs
        result: dict[str, Track] = {}
        for uid in unique_ids:
            track = existing_by_connector.get((self.connector_name, uid))
            if track:
                result[uid] = track

        existing_count = len(result)

        if existing_count:
            logger.info(
                f"Bulk lookup found {existing_count}/{len(unique_ids)} existing {self.connector_name} tracks"
            )

        # Phase 1.5: Reuse existing canonical tracks by content match
        missing_ids = [uid for uid in unique_ids if uid not in result]
        reused_count = 0

        if missing_ids:
            reused_tracks = await self._reuse_existing_canonical_tracks(missing_ids, uow)
            result.update(reused_tracks)
            reused_count = len(reused_tracks)
            if reused_count:
                logger.info(
                    f"Phase 1.5 reused {reused_count}/{len(missing_ids)} existing canonical tracks for {self.connector_name}"
                )

        # Phase 2: Create remaining missing tracks
        still_missing = [uid for uid in missing_ids if uid not in result]
        created_count = 0

        if still_missing:
            logger.info(
                f"Creating {len(still_missing)} new tracks for {self.connector_name}"
            )
            new_tracks = await self._create_tracks_batch(still_missing, uow)
            result.update(new_tracks)
            created_count = len(new_tracks)

        failed_count = len(unique_ids) - existing_count - reused_count - created_count
        metrics = TrackResolutionMetrics(
            existing=existing_count,
            reused=reused_count,
            created=created_count,
            failed=failed_count,
        )

        logger.info(
            f"{self.connector_name} resolution: {metrics.existing} existing, {metrics.reused} reused, {metrics.created} created, {metrics.failed} failed"
        )

        return result, metrics
