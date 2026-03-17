"""Shared 'resolve inward' pattern: external connector IDs → canonical tracks.

Both Spotify and Last.fm resolvers follow a three-step pipeline:
1. **Mapping Lookup**: Bulk-fetch existing connector→track mappings (fast path)
2. **Canonical Reuse**: Match unresolved IDs against existing canonical tracks
3. **Track Creation**: Batch-create new tracks for remaining unresolved IDs

This base class captures that shared pattern while letting subclasses define
connector-specific creation logic (Spotify batches API calls, Last.fm is sequential)
and metadata extraction for canonical reuse (via the _extract_reuse_metadata hook).
"""

from abc import ABC, abstractmethod
from typing import NamedTuple

from attrs import define

from src.config import create_evaluation_service, get_logger
from src.config.constants import MatchMethod
from src.domain.entities import Track
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.types import RawProviderMatch
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


class ReuseMetadata(NamedTuple):
    """Metadata extracted from a connector identifier for canonical reuse matching."""

    artist: str
    title: str
    connector_id: str
    lookup_pair: tuple[str, str]  # (title_lower, artist_lower) for DB search


class InwardTrackResolver(ABC):
    """Shared 'resolve inward' pattern: external IDs → canonical tracks.

    Three-step pipeline:
    1. **Mapping Lookup**: Bulk-fetch existing connector→track mappings
    2. **Canonical Reuse**: Match unresolved IDs against existing canonical tracks
    3. **Track Creation**: Batch-create new tracks for remaining unresolved IDs

    Subclasses provide:
    - connector_name: str property (e.g. "spotify", "lastfm")
    - _normalize_id(raw_id) → connector_track_identifier for DB lookup
    - _create_tracks_batch(missing_ids, uow) → dict mapping ID → Track
    - _extract_reuse_metadata(identifier) → ReuseMetadata or None
    """

    _match_evaluation_service: TrackMatchEvaluationService

    def __init__(
        self, match_evaluation_service: TrackMatchEvaluationService | None = None
    ):
        if match_evaluation_service is None:
            match_evaluation_service = create_evaluation_service()
        self._match_evaluation_service = match_evaluation_service

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

    def _extract_reuse_metadata(
        self,
        identifier: str,  # noqa: ARG002
    ) -> ReuseMetadata | None:
        """Extract metadata for canonical reuse matching.

        Subclasses override to enable canonical reuse. Return None to skip
        this identifier (base default: skip all → no reuse).
        """
        return None

    async def _reuse_existing_canonical_tracks(
        self,
        missing_ids: list[str],
        uow: UnitOfWorkProtocol,
    ) -> dict[str, Track]:
        """Canonical Reuse: match unresolved IDs against existing canonical tracks.

        For each unresolved ID, extracts artist+title metadata via the
        _extract_reuse_metadata hook, batch-searches for existing canonicals
        by title+artist, evaluates match quality via TrackMatchEvaluationService,
        and creates connector mappings for accepted matches.
        """
        # Extract metadata from identifiers via subclass hook
        pairs: list[tuple[str, str]] = []
        id_to_meta: dict[str, ReuseMetadata] = {}
        for identifier in missing_ids:
            meta = self._extract_reuse_metadata(identifier)
            if not meta:
                continue
            pairs.append(meta.lookup_pair)
            id_to_meta[identifier] = meta

        if not pairs:
            return {}

        candidates = await uow.get_track_repository().find_tracks_by_title_artist(pairs)
        if not candidates:
            return {}

        # Evaluate each candidate through the matching system
        result: dict[str, Track] = {}
        for identifier in missing_ids:
            meta = id_to_meta.get(identifier)
            if not meta:
                continue
            candidate = candidates.get(meta.lookup_pair)
            if not candidate:
                continue

            raw_match = RawProviderMatch(
                connector_id=meta.connector_id,
                match_method=MatchMethod.CANONICAL_REUSE,
                service_data={
                    "title": meta.title,
                    "artist": meta.artist,
                    "duration_ms": None,
                },
            )
            match_result = self._match_evaluation_service.evaluate_single_match(
                candidate, raw_match, self.connector_name
            )

            # Require both high overall confidence AND high title similarity.
            # The Fellegi-Sunter model can produce high confidence from artist
            # match alone — insufficient for candidate discovery where we don't
            # have a priori belief the tracks are the same.
            title_sim = (
                match_result.evidence.title_similarity if match_result.evidence else 0.0
            )
            title_threshold = (
                self._match_evaluation_service.config.high_similarity_threshold
            )

            if not match_result.success or title_sim < title_threshold:
                logger.debug(
                    f"Canonical reuse rejected candidate {candidate.id} for {identifier} "
                    f"(confidence: {match_result.confidence}, title_sim: {title_sim:.2f})"
                )
                continue

            try:
                await uow.get_connector_repository().map_track_to_connector(
                    candidate,
                    self.connector_name,
                    meta.connector_id,
                    MatchMethod.CANONICAL_REUSE,
                    confidence=match_result.confidence,
                    metadata={"artist_name": meta.artist, "track_name": meta.title},
                    confidence_evidence=match_result.evidence_dict,
                )
                result[identifier] = candidate
                logger.info(
                    f"Reused canonical track {candidate.id} for {self.connector_name}:{meta.connector_id} "
                    f"(confidence: {match_result.confidence})"
                )
            except Exception as e:
                logger.debug(f"Failed to create reuse mapping for {identifier}: {e}")

        return result

    async def resolve_to_canonical_tracks(
        self,
        connector_ids: list[str],
        uow: UnitOfWorkProtocol,
    ) -> tuple[dict[str, Track], TrackResolutionMetrics]:
        """Resolve external connector IDs to canonical tracks.

        1. Mapping Lookup: Bulk lookup existing mappings.
        2. Canonical Reuse: Match unresolved IDs against existing canonical tracks.
        3. Track Creation: Batch-create missing tracks via subclass hook.

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

        # Step 1 — Mapping Lookup: bulk-fetch existing connector→track mappings
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
                f"Mapping lookup found {existing_count}/{len(unique_ids)} existing {self.connector_name} tracks"
            )

        # Step 2 — Canonical Reuse: match unresolved IDs against existing canonical tracks
        missing_ids = [uid for uid in unique_ids if uid not in result]
        reused_count = 0

        if missing_ids:
            reused_tracks = await self._reuse_existing_canonical_tracks(
                missing_ids, uow
            )
            result.update(reused_tracks)
            reused_count = len(reused_tracks)
            if reused_count:
                logger.info(
                    f"Canonical reuse matched {reused_count}/{len(missing_ids)} existing tracks for {self.connector_name}"
                )

        # Step 3 — Track Creation: batch-create remaining missing tracks
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
