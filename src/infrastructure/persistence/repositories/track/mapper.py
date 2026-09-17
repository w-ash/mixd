"""Track mappers for converting between domain and database models."""

from typing import override
import warnings

from attrs import define
from sqlalchemy.orm.interfaces import ORMOption

from src.config import get_logger
from src.domain.entities import Artist, Track, ensure_utc
from src.domain.entities.playlist import DB_PSEUDO_CONNECTOR
from src.domain.entities.shared import JsonDict
from src.domain.entities.track_mapping import STALE_ID_FOR
from src.domain.matching import normalize_for_comparison, strip_parentheticals
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrack,
    DBTrackLike,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories._shared.connector_tracks import (
    extract_db_artist_names,
)
from src.infrastructure.persistence.repositories.mappers import BaseModelMapper

logger = get_logger(__name__)

_STALE_ID_METHODS = frozenset(STALE_ID_FOR.values())


class MissingPrimaryMappingWarning(UserWarning):
    """A track has live mappings for a connector but none of them is primary.

    Every mapping writer elects a primary (vacancy-fill in
    ``map_tracks_to_connectors``), so this is a writer defect, never a state a
    read repairs. The test suite turns it into an error (``filterwarnings``).
    """


@define(frozen=True, slots=True)
class TrackMapper(BaseModelMapper[DBTrack, Track]):
    """Bidirectional mapper between DB and domain models for Track."""

    @override
    @staticmethod
    async def to_domain(db_model: DBTrack) -> Track:
        """Convert database track to domain model.

        Pure function of the loaded row: the mapper never writes. A connector
        with live mappings and no primary is displayed through its
        highest-confidence mapping and reported as
        :class:`MissingPrimaryMappingWarning`.
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
        # a stale column value must not shadow a live mapping (v0.8.18 FM4b).
        # First pass: collect all primary mappings
        for mapping in active_mappings:
            if mapping.is_primary:
                conn_track = mapping.loaded_one(
                    DBTrackMapping.connector_track, DBConnectorTrack
                )
                if conn_track:
                    connector_name = conn_track.connector_name
                    connector_track_identifiers[connector_name] = (
                        conn_track.connector_track_identifier
                    )
                    connector_metadata[connector_name] = conn_track.raw_metadata or {}

        # Second pass: fill in any missing connectors with the HIGHEST-
        # confidence non-primary mapping — the same selection
        # ensure_primary_for_connector makes, so the displayed identifier and
        # the elected row agree (v0.8.18 FM4c: one election policy). Stale-id
        # cache rows are skipped for the same reason: no election promotes
        # them, and displaying a dead id would disagree with every writer.
        fallback_mappings: dict[str, DBTrackMapping] = {}

        for mapping in active_mappings:
            if not mapping.is_primary and mapping.match_method not in _STALE_ID_METHODS:
                conn_track = mapping.loaded_one(
                    DBTrackMapping.connector_track, DBConnectorTrack
                )
                if conn_track:
                    connector_name = conn_track.connector_name
                    # Only fall back where no primary exists for this connector
                    if connector_name in connector_track_identifiers:
                        continue
                    best = fallback_mappings.get(connector_name)
                    # Highest confidence, then lowest id — the SAME total order
                    # ensure_primary_for_connector's query uses (confidence desc,
                    # id asc), so display and election pick the same row on ties.
                    if (
                        best is None
                        or mapping.confidence > best.confidence
                        or (
                            mapping.confidence == best.confidence
                            and mapping.id < best.id
                        )
                    ):
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
        if db_model.spotify_id:
            _ = connector_track_identifiers.setdefault("spotify", db_model.spotify_id)
        if db_model.mbid:
            _ = connector_track_identifiers.setdefault("musicbrainz", db_model.mbid)

        if fallback_mappings:
            # structlog carries the detail for operators; the Python warning
            # is the test gate (``filterwarnings = error``) and keeps a
            # constant message so the warning registry does not grow per track.
            logger.warning(
                "missing_primary_mapping",
                track_id=str(db_model.id),
                connectors=sorted(fallback_mappings),
            )
            warnings.warn(
                "Track has live connector mappings with no primary",
                MissingPrimaryMappingWarning,
                stacklevel=2,
            )

        # A like row is presence: its existence marks the service as liked.
        for like in active_likes:
            service = like.service
            if service not in connector_metadata:
                connector_metadata[service] = {}

            connector_metadata[service]["is_liked"] = True
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
            play_count=db_model.play_count,
            last_played_at=ensure_utc(db_model.last_played_at),
            first_played_at=ensure_utc(db_model.first_played_at),
            connector_track_identifiers=connector_track_identifiers,
            connector_metadata=connector_metadata,
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
