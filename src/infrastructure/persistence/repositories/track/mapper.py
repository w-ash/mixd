"""Track mappers for converting between domain and database models."""

from collections.abc import Awaitable, Callable
from typing import override
from uuid import UUID

from attrs import define
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.interfaces import ORMOption

from src.config import get_logger
from src.domain.entities import Artist, Track, ensure_utc
from src.domain.entities.playlist import DB_PSEUDO_CONNECTOR
from src.domain.entities.shared import JsonDict
from src.domain.matching import normalize_for_comparison, strip_parentheticals
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrack,
    DBTrackLike,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.mappers import BaseModelMapper

logger = get_logger(__name__)

# Callback type: ensures a primary mapping exists for a (track, connector)
# pair. Args: (track_id, connector_name). The repository owns the selection
# policy (highest confidence) so read-path healing and explicit repair agree.
PromotePrimaryMappingFn = Callable[[UUID, str], Awaitable[None]]


def _get_promote_primary_fn(session: AsyncSession) -> PromotePrimaryMappingFn:
    """Get a callback that promotes a connector mapping to primary.

    Delegates to ``ensure_primary_for_connector`` — the single promotion
    policy (highest confidence), which also syncs the denormalized ID column
    so healing repairs stale fast-path values (v0.8.18 FM4c/FM4d).

    Lazy import avoids circular dependency (connector.py imports mapper.py).
    """
    from src.infrastructure.persistence.repositories.track.connector import (
        TrackConnectorRepository,
    )

    return TrackConnectorRepository(session).ensure_primary_for_connector


def extract_db_artist_names(artists: JsonDict) -> list[str]:
    """Extract artist names from a JSONB ``{"names": [...]}`` column.

    The column type is ``JsonDict`` (``dict[str, JsonValue]``) so the inner
    ``"names"`` value is a ``JsonValue`` union — narrow defensively before
    iterating. Used by both ``TrackMapper`` and ``ConnectorTrackMapper``.
    """
    names_value = artists.get("names")
    if isinstance(names_value, list):
        return [n for n in names_value if isinstance(n, str)]
    return []


@define(frozen=True, slots=True)
class TrackMapper(BaseModelMapper[DBTrack, Track]):
    """Bidirectional mapper between DB and domain models for Track."""

    @override
    @staticmethod
    async def to_domain(db_model: DBTrack) -> Track:
        """Convert database track to domain model."""
        return await TrackMapper._to_domain_with_session(db_model)

    @staticmethod
    async def to_domain_with_session(
        db_model: DBTrack | None, session: AsyncSession | None = None
    ) -> Track | None:
        """Convert database track to domain model with session for auto-healing."""
        if not db_model:
            return None
        promote_primary_fn = (
            _get_promote_primary_fn(session) if session is not None else None
        )
        return await TrackMapper._to_domain_with_session(
            db_model, promote_primary_fn=promote_primary_fn
        )

    @staticmethod
    async def _to_domain_with_session(
        db_model: DBTrack,
        connector_filter: set[str] | None = None,
        promote_primary_fn: PromotePrimaryMappingFn | None = None,
    ) -> Track:
        """Convert database track to domain model.

        Args:
            db_model: Database track entity to convert.
            connector_filter: Optional set of connector names to include.
            promote_primary_fn: Optional callback to repair a missing primary
                mapping. Signature: (track_id, connector_name). When provided
                and a connector has no primary mapping, the mapper delegates
                the write to the caller via this callback.
        """
        # Read only eager-loaded relationships (zero I/O) via the typed
        # loaded_list primitive — a forgotten eager-load degrades to [].
        active_mappings = db_model.loaded_list(DBTrack.mappings, DBTrackMapping)
        active_likes = db_model.loaded_list(DBTrack.likes, DBTrackLike)

        # Build connector IDs and metadata
        connector_track_identifiers: dict[str, str] = {}
        connector_metadata: dict[str, JsonDict] = {}

        # Add internal ID first
        if db_model.id:
            connector_track_identifiers[DB_PSEUDO_CONNECTOR] = str(db_model.id)

        # Process connector track mappings with primary awareness.
        # The mapping walk runs BEFORE the denormalized columns are consulted —
        # a stale column value must not shadow a live mapping or mask the
        # promotion pass (v0.8.18 FM4b).
        # First pass: collect all primary mappings
        for mapping in active_mappings:
            if mapping.is_primary:
                conn_track = mapping.loaded_one(
                    DBTrackMapping.connector_track, DBConnectorTrack
                )
                if conn_track:
                    connector_name = conn_track.connector_name
                    # Skip connectors not in filter (if filter is specified)
                    if connector_filter and connector_name not in connector_filter:
                        continue
                    connector_track_identifiers[connector_name] = (
                        conn_track.connector_track_identifier
                    )
                    connector_metadata[connector_name] = conn_track.raw_metadata or {}

        # Second pass: fill in any missing connectors with the HIGHEST-
        # confidence non-primary mapping — the same selection
        # ensure_primary_for_connector makes, so the displayed identifier and
        # the promoted row agree (v0.8.18 FM4c: one promotion policy).
        fallback_mappings: dict[str, DBTrackMapping] = {}

        for mapping in active_mappings:
            if not mapping.is_primary:
                conn_track = mapping.loaded_one(
                    DBTrackMapping.connector_track, DBConnectorTrack
                )
                if conn_track:
                    connector_name = conn_track.connector_name
                    # Skip connectors not in filter (if filter is specified)
                    if connector_filter and connector_name not in connector_filter:
                        continue
                    # Only fall back where no primary exists for this connector
                    if connector_name in connector_track_identifiers:
                        continue
                    best = fallback_mappings.get(connector_name)
                    if best is None or mapping.confidence > best.confidence:
                        fallback_mappings[connector_name] = mapping

        for connector_name, mapping in fallback_mappings.items():
            conn_track = mapping.loaded_one(
                DBTrackMapping.connector_track, DBConnectorTrack
            )
            if conn_track:
                connector_track_identifiers[connector_name] = (
                    conn_track.connector_track_identifier
                )
                connector_metadata[connector_name] = conn_track.raw_metadata or {}

        # Denormalized columns are post-walk FALLBACKS only (no mapping rows
        # to contradict them — e.g. lazy-load degradation or hint columns).
        # Applied regardless of connector_filter, preserving the pre-v0.8.18
        # quirk that column IDs appear even when filtered out.
        if db_model.spotify_id:
            _ = connector_track_identifiers.setdefault("spotify", db_model.spotify_id)
        if db_model.mbid:
            _ = connector_track_identifiers.setdefault("musicbrainz", db_model.mbid)

        # Promote fallback mappings to primary via caller-provided callback
        if fallback_mappings and hasattr(db_model, "id") and promote_primary_fn:
            await TrackMapper._promote_fallback_to_primary(
                db_model.id, fallback_mappings, promote_primary_fn
            )
        elif fallback_mappings:
            logger.warning(
                f"Track {db_model.id} has no primary connector mapping(s): "
                + f"connectors={list(fallback_mappings)} — "
                + "pass a session to to_domain_with_session() to enable auto-promotion"
            )

        # Process likes into connector metadata
        for like in active_likes:
            service = like.service
            # Skip services not in filter (if filter is specified)
            if connector_filter and service not in connector_filter:
                continue
            if service not in connector_metadata:
                connector_metadata[service] = {}

            connector_metadata[service]["is_liked"] = like.is_liked
            if like.liked_at:
                connector_metadata[service]["liked_at"] = like.liked_at.isoformat()

        return Track(
            id=db_model.id,
            version=db_model.version,
            user_id=db_model.user_id,
            title=db_model.title,
            artists=[Artist(name=n) for n in extract_db_artist_names(db_model.artists)],
            album=db_model.album,
            duration_ms=db_model.duration_ms,
            release_date=ensure_utc(db_model.release_date),
            isrc=db_model.isrc,
            connector_track_identifiers=connector_track_identifiers,
            connector_metadata=connector_metadata,
        )

    @staticmethod
    async def _promote_fallback_to_primary(
        track_id: UUID,
        fallback_mappings: dict[str, DBTrackMapping],
        promote_primary_fn: PromotePrimaryMappingFn,
    ) -> None:
        """Promote fallback connector mappings to primary status.

        For each connector that had no primary mapping, delegates the repair
        to ``ensure_primary_for_connector`` via the caller-provided callback —
        the repository owns the selection (highest confidence) and the
        denormalized-column sync. This keeps the mapper read-only while
        enabling opportunistic self-correction.

        Args:
            track_id: The track whose mappings need promotion.
            fallback_mappings: Connector name → the highest-confidence
                fallback mapping the walk selected (for observability).
            promote_primary_fn: Callback that performs the repair:
                (track_id, connector_name).
        """
        log = logger.bind(track_id=track_id)
        for connector_name, mapping in fallback_mappings.items():
            try:
                await promote_primary_fn(track_id, connector_name)
            except Exception:
                log.error(
                    f"Connector mapping promotion failed for {connector_name}",
                    exc_info=True,
                )
                # Best-effort: never interrupt the read path
                continue
            conn_track = mapping.loaded_one(
                DBTrackMapping.connector_track, DBConnectorTrack
            )
            log.info(
                "read_path_promotion",
                connector=connector_name,
                external_id=conn_track.connector_track_identifier
                if conn_track
                else None,
            )

    @override
    @staticmethod
    def to_db(domain_model: Track) -> DBTrack:
        """Convert domain track to database model."""
        return DBTrack(
            user_id=domain_model.user_id,
            title=domain_model.title,
            artists={"names": [a.name for a in domain_model.artists]},
            album=domain_model.album,
            duration_ms=domain_model.duration_ms,
            release_date=domain_model.release_date,
            isrc=domain_model.isrc,
            spotify_id=domain_model.connector_track_identifiers.get("spotify"),
            mbid=domain_model.connector_track_identifiers.get("musicbrainz"),
            **TrackMapper.normalized_columns(domain_model),
        )

    @staticmethod
    def normalized_columns(track: Track) -> dict[str, str | None]:
        """Pre-computed text columns that back the pg_trgm fuzzy-search indexes.

        Both ``save_track`` and ``to_db`` MUST go through this helper — bypassing
        it leaves the row invisible to library search.
        """
        first_artist = track.artists[0].name if track.artists else None
        return {
            "title_normalized": normalize_for_comparison(track.title),
            "artist_normalized": (
                normalize_for_comparison(first_artist) if first_artist else None
            ),
            "title_stripped": normalize_for_comparison(
                strip_parentheticals(track.title)
            ),
            "artists_text": track.artists_display or None,
        }

    @override
    @staticmethod
    def get_default_relationships() -> list[ORMOption]:
        """Get default relationships using SQLAlchemy 2.1 best practices."""
        from sqlalchemy.orm import selectinload

        from src.infrastructure.persistence.database.db_models import (
            DBTrack,
            DBTrackMapping,
        )

        return [
            selectinload(DBTrack.mappings),  # Simple relationship
            selectinload(DBTrack.mappings).selectinload(
                DBTrackMapping.connector_track
            ),  # Nested chaining
            selectinload(DBTrack.likes),  # Simple relationship
        ]
