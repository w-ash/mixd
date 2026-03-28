"""Core track repository implementation for basic track operations."""

# pyright: reportAny=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
# Legitimate Unknown: SQLAlchemy ColumnElement types from ilike/in_/cast expressions

from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import (
    String,
    cast,
    delete,
    func,
    literal,
    or_,
    select,
    text,
    tuple_,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.pagination import TRACK_SORT_COLUMNS
from src.config import get_logger
from src.config.constants import DenormalizedTrackColumns, MappingOrigin
from src.domain.entities import Track
from src.domain.matching import normalize_for_comparison, strip_parentheticals
from src.domain.repositories.interfaces import TrackListingPage
from src.infrastructure.persistence.database.db_models import (
    DBTrack,
    DBTrackLike,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.repo_decorator import db_operation
from src.infrastructure.persistence.repositories.track.mapper import TrackMapper

logger = get_logger(__name__)


class TrackRepository(BaseRepository[DBTrack, Track]):
    """Repository for core track operations."""

    # ID type lookup definitions: non-connector types + shared connector→column map
    _TRACK_ID_TYPES: ClassVar[dict[str, str]] = {
        "internal": "id",
        "isrc": "isrc",
        **DenormalizedTrackColumns.COLUMN_MAP,
    }

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with session and mapper."""
        super().__init__(
            session=session,
            model_class=DBTrack,
            mapper=TrackMapper(),
        )

    # -------------------------------------------------------------------------
    # PUBLIC API METHODS
    # -------------------------------------------------------------------------

    @db_operation("find_tracks_by_ids")
    async def find_tracks_by_ids(self, track_ids: list[UUID]) -> dict[UUID, Track]:
        """Find multiple tracks by their internal IDs in a single batch operation.

        Args:
            track_ids: List of internal track IDs to retrieve

        Returns:
            Dictionary mapping track IDs to Track objects
        """
        if not track_ids:
            return {}

        # Leverage the base repository's get_by_ids method
        tracks = await self.get_by_ids(track_ids)

        # Map results by ID for easier lookup
        return {track.id: track for track in tracks}

    @db_operation("save_track")
    async def save_track(self, track: Track) -> Track:
        """Save track without connector mappings using native SQLAlchemy 2.0 features.

        This method follows SQLAlchemy 2.0 async best practices:
        1. Uses direct value mappings instead of complex object hierarchies
        2. Uses explicit eager loading to avoid lazy loading issues
        3. Leverages upsert's two-phase approach for safe async operations
        4. Avoids implicit IO in relationship traversal
        """
        if not track.title or not track.artists:
            raise ValueError("Track must have title and artists")

        # version > 0 means the track was loaded from DB — update path
        if track.version > 0:
            return await self.update(track.id, track)

        # Create direct column-to-value mappings for insert/update
        # This avoids the need to convert the entire Track object to a dict
        values = {
            "title": track.title,
            "artists": {"names": [artist.name for artist in track.artists]},
            "album": track.album,
            "duration_ms": track.duration_ms,
            "release_date": track.release_date,
            "isrc": track.isrc,
        }

        # Pre-compute normalized text for fuzzy matching index
        values["title_normalized"] = normalize_for_comparison(track.title)
        values["artist_normalized"] = (
            normalize_for_comparison(track.artists[0].name) if track.artists else None
        )
        values["title_stripped"] = normalize_for_comparison(
            strip_parentheticals(track.title)
        )
        # Denormalized artist text for search and sorting
        values["artists_text"] = track.artists_display or None

        # Add denormalized connector IDs (fast-path lookup columns)
        for connector, column in DenormalizedTrackColumns.COLUMN_MAP.items():
            if connector in track.connector_track_identifiers:
                values[column] = track.connector_track_identifiers[connector]

        # Handle lookups by ISRC, MBID, or Spotify ID
        # The upsert method uses a two-phase approach that avoids greenlet issues
        uid = track.user_id
        if track.isrc:
            return await self.upsert({"user_id": uid, "isrc": track.isrc}, values)
        elif "musicbrainz" in track.connector_track_identifiers:
            return await self.upsert(
                {
                    "user_id": uid,
                    "mbid": track.connector_track_identifiers["musicbrainz"],
                },
                values,
            )
        elif "spotify" in track.connector_track_identifiers:
            return await self.upsert(
                {
                    "user_id": uid,
                    "spotify_id": track.connector_track_identifiers["spotify"],
                },
                values,
            )

        # Create new track with explicit eager loading for relationships
        db_track = DBTrack(**values)
        self.session.add(db_track)
        await self.session.flush()

        # Refresh with explicit eager loading of relationships to avoid lazy loading
        default_rels = self.mapper.get_default_relationships()
        if default_rels:
            # Extract string names from relationships using utility method
            rel_names = self._extract_relationship_names(default_rels)
            if rel_names:
                await self.session.refresh(db_track, attribute_names=rel_names)
            else:
                await self.session.refresh(db_track)
        else:
            await self.session.refresh(db_track)

        # Map back to domain model - the to_domain method has been updated to use AsyncAttrs safely
        result = await TrackMapper.to_domain_with_session(db_track, self.session)
        if result is None:
            raise ValueError(f"Failed to map track from database (id={db_track.id})")
        return result

    # -------------------------------------------------------------------------
    # LIBRARY LISTING
    # -------------------------------------------------------------------------

    @db_operation("list_tracks")
    async def list_tracks(
        self,
        *,
        query: str | None = None,
        liked: bool | None = None,
        connector: str | None = None,
        sort_by: str = "title_asc",
        limit: int = 50,
        offset: int = 0,
        # Keyset pagination: seek after this (sort_value, id) pair
        after_value: Any = None,
        after_id: UUID | None = None,
        include_total: bool = True,
    ) -> TrackListingPage:
        """List tracks with search, filters, sorting, and pagination.

        Supports both offset-based and keyset (cursor) pagination. When
        ``after_value`` and ``after_id`` are provided, uses a keyset WHERE clause
        for O(1) seeking regardless of page depth. Falls back to OFFSET otherwise.
        """
        # Build base filter conditions
        conditions: list[Any] = []

        if query:
            pattern = f"%{query}%"
            # pg_trgm GIN indexes accelerate ILIKE with substring matching
            conditions.append(
                or_(
                    DBTrack.title.ilike(pattern),
                    DBTrack.album.ilike(pattern),
                    DBTrack.artists_text.ilike(pattern),
                )
            )

        if liked is not None:
            liked_subq = (
                select(DBTrackLike.track_id)
                .where(DBTrackLike.is_liked == True)  # noqa: E712
                .distinct()
            )
            if liked:
                conditions.append(DBTrack.id.in_(liked_subq))
            else:
                conditions.append(~DBTrack.id.in_(liked_subq))

        if connector:
            connector_subq = (
                select(DBTrackMapping.track_id)
                .where(DBTrackMapping.connector_name == connector)
                .distinct()
            )
            conditions.append(DBTrack.id.in_(connector_subq))

        # Count total matching tracks (skipped on cursor-paginated pages)
        total: int | None = None
        if include_total:
            count_stmt = select(func.count()).select_from(DBTrack)
            if conditions:
                count_stmt = count_stmt.where(*conditions)
            count_result = await self.session.execute(count_stmt)
            total = count_result.scalar_one()

            if total == 0:
                return TrackListingPage(
                    tracks=[], total=0, liked_track_ids=set(), next_page_key=None
                )

        # Resolve sort column from canonical mapping (sort_by validated upstream)
        sort_field, sort_dir = TRACK_SORT_COLUMNS.get(sort_by, ("title", "asc"))  # type: ignore[arg-type]
        col = getattr(DBTrack, sort_field)

        # Build data query with sorting, pagination, and relationship loading
        data_stmt = self.select()
        if conditions:
            data_stmt = data_stmt.where(*conditions)

        # Keyset pagination: WHERE (sort_col, id) > (:value, :id) for ASC
        use_keyset = after_value is not None and after_id is not None
        if use_keyset:
            keyset_pair = tuple_(col, DBTrack.id)
            cursor_pair = tuple_(literal(after_value), literal(after_id))
            if sort_dir == "desc":
                data_stmt = data_stmt.where(keyset_pair < cursor_pair)
            else:
                data_stmt = data_stmt.where(keyset_pair > cursor_pair)
        else:
            data_stmt = data_stmt.offset(offset)

        # Apply sort and limit
        data_stmt = data_stmt.order_by(
            col.desc() if sort_dir == "desc" else col.asc(),
            DBTrack.id.desc() if sort_dir == "desc" else DBTrack.id.asc(),
        )
        data_stmt = data_stmt.limit(limit)

        # Eager-load relationships for mapper
        data_stmt = self.with_default_relationships(data_stmt)

        result = await self.session.execute(data_stmt)
        db_tracks = list(result.scalars().all())

        tracks = [await self.mapper.to_domain(db_track) for db_track in db_tracks]

        # Build next-page keyset from the last row
        next_page_key: tuple[Any, UUID] | None = None
        if db_tracks and len(db_tracks) == limit:
            last_db = db_tracks[-1]
            next_page_key = (getattr(last_db, sort_field), last_db.id)

        # Get authoritative liked status from track_likes table for returned tracks
        track_ids = [t.id for t in tracks]
        liked_ids: set[UUID] = set()
        if track_ids:
            liked_stmt = (
                select(DBTrackLike.track_id)
                .where(
                    DBTrackLike.track_id.in_(track_ids), DBTrackLike.is_liked.is_(True)
                )
                .distinct()
            )
            liked_result = await self.session.execute(liked_stmt)
            liked_ids = set(liked_result.scalars().all())

        return TrackListingPage(
            tracks=tracks,
            total=total,
            liked_track_ids=liked_ids,
            next_page_key=next_page_key,
        )

    # -------------------------------------------------------------------------
    # TRACK MERGE OPERATIONS
    # -------------------------------------------------------------------------

    @db_operation("move_references_to_track")
    async def move_references_to_track(self, from_id: UUID, to_id: UUID) -> None:
        """Move all foreign key references from one track to another.

        Single CTE chain: moves playlist tracks, plays, and likes (with
        conflict resolution — keeps the most recently synced like per service).
        """
        result = await self.session.execute(
            text("""
            WITH moved_playlist_tracks AS (
                UPDATE playlist_tracks SET track_id = :to_id, updated_at = :now
                WHERE track_id = :from_id
                RETURNING id
            ),
            moved_plays AS (
                UPDATE track_plays SET track_id = :to_id, updated_at = :now
                WHERE track_id = :from_id
                RETURNING id
            ),
            -- Find likes where both tracks have the same service
            like_conflicts AS (
                SELECT
                    loser.id AS loser_id,
                    winner.id AS winner_id,
                    loser.is_liked AS loser_is_liked,
                    loser.liked_at AS loser_liked_at,
                    loser.last_synced AS loser_last_synced,
                    winner.last_synced AS winner_last_synced
                FROM track_likes loser
                JOIN track_likes winner ON loser.service = winner.service
                WHERE loser.track_id = :from_id AND winner.track_id = :to_id
            ),
            -- Update winner with loser's data when loser was synced more recently
            updated_winner_likes AS (
                UPDATE track_likes tl
                SET
                    is_liked = lc.loser_is_liked,
                    liked_at = lc.loser_liked_at,
                    last_synced = lc.loser_last_synced,
                    updated_at = :now
                FROM like_conflicts lc
                WHERE tl.id = lc.winner_id
                  AND (
                      (lc.loser_last_synced IS NOT NULL AND lc.winner_last_synced IS NOT NULL
                       AND lc.loser_last_synced > lc.winner_last_synced)
                      OR
                      (lc.loser_last_synced IS NOT NULL AND lc.winner_last_synced IS NULL)
                  )
                RETURNING tl.id
            ),
            -- Delete all conflicting loser likes
            deleted_conflict_likes AS (
                DELETE FROM track_likes
                WHERE id IN (SELECT loser_id FROM like_conflicts)
                RETURNING id
            ),
            -- Move non-conflicting likes (explicitly exclude conflict IDs
            -- because CTE snapshot semantics mean the DELETE above hasn't
            -- removed them from this CTE's view of the table)
            moved_likes AS (
                UPDATE track_likes
                SET track_id = :to_id, updated_at = :now
                WHERE track_id = :from_id
                  AND id NOT IN (SELECT loser_id FROM like_conflicts)
                RETURNING id
            )
            SELECT
                (SELECT count(*) FROM moved_playlist_tracks) AS playlist_tracks_moved,
                (SELECT count(*) FROM moved_plays) AS plays_moved,
                (SELECT count(*) FROM deleted_conflict_likes) AS like_conflicts_resolved,
                (SELECT count(*) FROM moved_likes) AS likes_moved
            """),
            {"from_id": from_id, "to_id": to_id, "now": datetime.now(UTC)},
        )
        counts = result.fetchone()
        logger.debug(
            f"Moved references: {from_id} → {to_id} "
            f"(playlist_tracks={counts[0]}, plays={counts[1]}, "  # type: ignore[index]
            f"like_conflicts={counts[2]}, likes_moved={counts[3]})"  # type: ignore[index]
        )

    @db_operation("merge_mappings_to_track")
    async def merge_mappings_to_track(self, from_id: UUID, to_id: UUID) -> None:
        """Merge connector mappings from one track to another with conflict resolution.

        Single CTE chain handling two conflict branches:
        - Same connector_track_id: true duplicates — keep higher confidence, delete loser
        - Different connector_track_ids: keep both on winner track (winner=primary, loser=secondary)
        Non-conflicting mappings are moved directly to the winner.
        """
        result = await self.session.execute(
            text("""
            WITH all_conflicts AS (
                SELECT
                    loser.id AS loser_id,
                    winner.id AS winner_id,
                    loser.connector_track_id AS loser_ct_id,
                    winner.connector_track_id AS winner_ct_id,
                    loser.confidence AS loser_confidence,
                    loser.match_method AS loser_match_method,
                    loser.created_at AS loser_created_at,
                    winner.confidence AS winner_confidence,
                    winner.created_at AS winner_created_at
                FROM track_mappings loser
                JOIN track_mappings winner ON loser.connector_name = winner.connector_name
                WHERE loser.track_id = :from_id AND winner.track_id = :to_id
            ),

            -- Branch 1: Same connector_track_id (true duplicates)
            -- Update winner with loser's confidence/method when loser is better
            updated_same_ext_winners AS (
                UPDATE track_mappings tm
                SET
                    confidence = ac.loser_confidence,
                    match_method = ac.loser_match_method,
                    origin = :manual_override,
                    updated_at = :now
                FROM all_conflicts ac
                WHERE tm.id = ac.winner_id
                  AND ac.loser_ct_id = ac.winner_ct_id
                  AND (
                      ac.loser_confidence > ac.winner_confidence
                      OR (ac.loser_confidence = ac.winner_confidence
                          AND ac.loser_created_at > ac.winner_created_at)
                  )
                RETURNING tm.id
            ),
            -- Delete loser mapping for same-external-ID conflicts
            deleted_same_ext_losers AS (
                DELETE FROM track_mappings
                WHERE id IN (
                    SELECT loser_id FROM all_conflicts
                    WHERE loser_ct_id = winner_ct_id
                )
                RETURNING id
            ),

            -- Branch 2: Different connector_track_ids (keep both on winner track)
            -- Ensure winner's mapping is primary
            updated_diff_ext_winners AS (
                UPDATE track_mappings tm
                SET is_primary = TRUE, updated_at = :now
                FROM all_conflicts ac
                WHERE tm.id = ac.winner_id
                  AND ac.loser_ct_id != ac.winner_ct_id
                RETURNING tm.id
            ),
            -- Move loser's mapping to winner track as secondary
            moved_diff_ext_losers AS (
                UPDATE track_mappings tm
                SET
                    track_id = :to_id,
                    is_primary = FALSE,
                    origin = :manual_override,
                    updated_at = :now
                FROM all_conflicts ac
                WHERE tm.id = ac.loser_id
                  AND ac.loser_ct_id != ac.winner_ct_id
                RETURNING tm.id
            ),

            -- Move all non-conflicting mappings from loser to winner
            moved_non_conflict AS (
                UPDATE track_mappings
                SET
                    track_id = :to_id,
                    origin = :manual_override,
                    updated_at = :now
                WHERE track_id = :from_id
                  AND id NOT IN (SELECT loser_id FROM all_conflicts)
                RETURNING id
            )
            SELECT
                (SELECT count(*) FROM deleted_same_ext_losers) AS same_ext_conflicts,
                (SELECT count(*) FROM moved_diff_ext_losers) AS diff_ext_conflicts,
                (SELECT count(*) FROM moved_non_conflict) AS non_conflicts_moved
            """),
            {
                "from_id": from_id,
                "to_id": to_id,
                "now": datetime.now(UTC),
                "manual_override": MappingOrigin.MANUAL_OVERRIDE,
            },
        )
        counts = result.fetchone()
        logger.debug(
            f"Merged track mappings: {from_id} → {to_id} "
            f"({counts[0]} same external ID, "  # type: ignore[index]
            f"{counts[1]} different external ID conflicts, "  # type: ignore[index]
            f"{counts[2]} non-conflicts moved)"  # type: ignore[index]
        )

    @db_operation("merge_metrics_to_track")
    async def merge_metrics_to_track(self, from_id: UUID, to_id: UUID) -> None:
        """Merge track metrics from one track to another with conflict resolution.

        Single CTE chain: for conflicts (same connector_name + metric_type),
        keeps the most recently collected value. Non-conflicting metrics move directly.
        """
        result = await self.session.execute(
            text("""
            WITH metric_conflicts AS (
                SELECT
                    loser.id AS loser_id,
                    winner.id AS winner_id,
                    loser.value AS loser_value,
                    loser.collected_at AS loser_collected_at,
                    winner.collected_at AS winner_collected_at
                FROM track_metrics loser
                JOIN track_metrics winner ON (
                    loser.connector_name = winner.connector_name
                    AND loser.metric_type = winner.metric_type
                )
                WHERE loser.track_id = :from_id AND winner.track_id = :to_id
            ),
            -- When loser has more recent data, update the winner
            updated_winner_metrics AS (
                UPDATE track_metrics tm
                SET
                    value = mc.loser_value,
                    collected_at = mc.loser_collected_at,
                    updated_at = :now
                FROM metric_conflicts mc
                WHERE tm.id = mc.winner_id
                  AND mc.loser_collected_at > mc.winner_collected_at
                RETURNING tm.id
            ),
            -- Delete all conflicting loser metrics
            deleted_conflict_metrics AS (
                DELETE FROM track_metrics
                WHERE id IN (SELECT loser_id FROM metric_conflicts)
                RETURNING id
            ),
            -- Move non-conflicting metrics (exclude conflict IDs due to CTE snapshot)
            moved_metrics AS (
                UPDATE track_metrics
                SET track_id = :to_id, updated_at = :now
                WHERE track_id = :from_id
                  AND id NOT IN (SELECT loser_id FROM metric_conflicts)
                RETURNING id
            )
            SELECT
                (SELECT count(*) FROM deleted_conflict_metrics) AS conflicts_resolved,
                (SELECT count(*) FROM moved_metrics) AS metrics_moved
            """),
            {"from_id": from_id, "to_id": to_id, "now": datetime.now(UTC)},
        )
        counts = result.fetchone()
        logger.debug(
            f"Merged track metrics: {from_id} → {to_id} "
            f"({counts[0]} conflicts resolved, {counts[1]} moved)"  # type: ignore[index]
        )

    @db_operation("hard_delete_track")
    async def hard_delete_track(self, track_id: UUID) -> None:
        """Permanently delete a track record from the database."""
        await self.session.execute(delete(DBTrack).where(DBTrack.id == track_id))
        logger.debug(f"Hard deleted track: {track_id}")

    # ── Lookup queries ───────────────────────────────────────────────

    @db_operation("find_tracks_by_title_artist")
    async def find_tracks_by_title_artist(
        self, pairs: list[tuple[str, str]]
    ) -> dict[tuple[str, str], Track]:
        """Find existing tracks matching (title, first_artist) pairs.

        Uses pre-computed normalized columns for fuzzy matching that handles
        diacritics, smart quotes, articles, and feat./ft. variations.
        Also matches via parenthetical-stripped form so "Song (feat. X)" ↔ "Song".
        Returns only the first match per pair (oldest track by ID).

        Args:
            pairs: List of (title, first_artist_name) tuples to search for.

        Returns:
            Dict keyed by lowercased (title, artist) → Track.
        """
        if not pairs:
            return {}

        # Normalize input pairs for comparison against indexed columns
        normalized_pairs = [
            (normalize_for_comparison(title), normalize_for_comparison(artist))
            for title, artist in pairs
        ]

        # Also compute stripped versions for parenthetical fallback matching
        stripped_pairs = [
            normalize_for_comparison(strip_parentheticals(title))
            for title, _artist in pairs
        ]

        # Build OR conditions: match on normalized title OR stripped title
        # This enables "Song (feat. X)" in DB to match query "Song" and vice versa
        conditions = [
            (
                (DBTrack.title_normalized == norm_title)
                | (DBTrack.title_stripped == stripped_title)
                | (DBTrack.title_normalized == stripped_title)
                | (DBTrack.title_stripped == norm_title)
            )
            & (DBTrack.artist_normalized == norm_artist)
            for (norm_title, norm_artist), stripped_title in zip(
                normalized_pairs, stripped_pairs, strict=True
            )
        ]

        stmt = select(DBTrack).where(or_(*conditions)).order_by(DBTrack.id.asc())
        result = await self.session.execute(stmt)
        rows = result.scalars().all()

        # Build O(1) lookup index: (title_variant, artist_normalized) -> lower_key
        # Each query pair produces up to 4 lookup keys (norm*norm, norm*stripped, etc.)
        lookup_to_lower: dict[tuple[str, str], tuple[str, str]] = {}
        for (title, artist), (norm_title, norm_artist), stripped_title in zip(
            pairs, normalized_pairs, stripped_pairs, strict=True
        ):
            lower_key = (title.lower(), artist.lower())
            # All four title variants that the DB WHERE clause also matches
            for title_variant in {norm_title, stripped_title}:
                lookup_to_lower.setdefault((title_variant, norm_artist), lower_key)

        # Match DB rows via O(1) dict lookup instead of O(N*M) nested loop
        matched: dict[tuple[str, str], Track] = {}
        for db_track in rows:
            artist_norm = db_track.artist_normalized or ""
            for title_val in {
                db_track.title_normalized or "",
                db_track.title_stripped or "",
            }:
                lower_key = lookup_to_lower.get((title_val, artist_norm))
                if lower_key and lower_key not in matched:
                    matched[lower_key] = await self.mapper.to_domain(db_track)
                    break

        return matched

    @db_operation("find_tracks_by_isrcs")
    async def find_tracks_by_isrcs(self, isrcs: list[str]) -> dict[str, Track]:
        """Batch lookup tracks by ISRC. Returns {isrc: Track} for found tracks."""
        return await self._find_tracks_by_unique_column(DBTrack.isrc, isrcs)

    @db_operation("find_tracks_by_mbids")
    async def find_tracks_by_mbids(self, mbids: list[str]) -> dict[str, Track]:
        """Batch lookup tracks by MusicBrainz Recording ID (MBID)."""
        return await self._find_tracks_by_unique_column(DBTrack.mbid, mbids)

    # ── Integrity check queries ──────────────────────────────────────

    @db_operation("find_duplicate_tracks_by_fingerprint")
    async def find_duplicate_tracks_by_fingerprint(self) -> list[dict[str, object]]:
        """Find tracks with identical (title, first_artist, album) tuples."""
        first_artist = DBTrack.artists["names"][0].as_string()
        stmt = (
            select(
                DBTrack.title,
                first_artist.label("first_artist"),
                DBTrack.album,
                func.count().label("count"),
                func.string_agg(cast(DBTrack.id, String), ",").label("track_ids"),
            )
            .where(DBTrack.title.isnot(None), DBTrack.title != "")  # noqa: PLC1901
            .group_by(DBTrack.title, first_artist, DBTrack.album)
            .having(func.count() > 1)
        )
        result = await self.session.execute(stmt)
        return [
            {
                "title": row.title,
                "artist": row.first_artist,
                "album": row.album,
                "count": row.count,
                "track_ids": [UUID(x) for x in str(row.track_ids).split(",")],
            }
            for row in result.all()
        ]

    # -------------------------------------------------------------------------
    # PRIVATE HELPERS
    # -------------------------------------------------------------------------

    async def _find_tracks_by_unique_column(
        self, column: Any, values: list[str]
    ) -> dict[str, Track]:
        """Batch lookup tracks by a unique string column (ISRC, MBID, etc.)."""
        if not values:
            return {}

        stmt = self.select().where(column.in_(values))
        stmt = self.with_default_relationships(stmt)
        result = await self.session.execute(stmt)
        rows = result.scalars().all()

        matched: dict[str, Track] = {}
        for db_track in rows:
            key = getattr(db_track, column.key)
            if key:
                matched[key] = await self.mapper.to_domain(db_track)
        return matched
