"""Core track repository implementation for basic track operations."""

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar, Final, Literal, NamedTuple, cast
from uuid import UUID, uuid7

from sqlalchemy import (
    ColumnElement,
    Select,
    String,
    and_,
    bindparam,
    cast as sa_cast,
    delete,
    func,
    literal,
    or_,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from src.config import get_logger
from src.config.constants import DenormalizedTrackColumns, MappingOrigin
from src.domain.entities import Track
from src.domain.entities.preference import PREFERENCE_ORDER
from src.domain.entities.sourced_metadata import SOURCE_PRIORITY
from src.domain.entities.track_mapping import SupersessionReason
from src.domain.exceptions import DomainError, OptimisticLockError
from src.domain.matching import normalize_for_comparison, strip_parentheticals
from src.domain.matching.isrc_validation import (
    assess_isrc_match_reliability,
    compute_duration_diff_ms,
)
from src.domain.repositories.resolution import SupersessionEdge
from src.domain.repositories.track import (
    TrackFacets,
    TrackListingPage,
)
from src.infrastructure.persistence.database.db_models import (
    DBTrack,
    DBTrackLike,
    DBTrackMapping,
    DBTrackPreference,
    DBTrackTag,
)
from src.infrastructure.persistence.database.live_rows import (
    INCLUDE_SUPERSEDED,
    expire_mapping_identity,
    live_only,
)
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.repo_decorator import db_operation
from src.infrastructure.persistence.repositories.track.mapper import TrackMapper

logger = get_logger(__name__)

# The supersession reason a merge writes: the retired mapping pointed at a
# duplicate canonical, not at a wrong connector track.
_CONFLATION: SupersessionReason = "conflation"


# The user-scoped unique keys a canonical is deduplicated on, in the
# precedence ``save_track`` applies them: the ISRC first, then the MusicBrainz
# id, then the denormalized Spotify id. Each is backed by a unique constraint
# (``uq_tracks_user_isrc`` and siblings), so a batch insert has to know which
# of them are already claimed before it can call its rows new.
_IDENTITY_COLUMNS: Final[tuple[str, ...]] = ("isrc", "mbid", "spotify_id")


def _identity_keys(values: Mapping[str, object]) -> set[tuple[str, str, str]]:
    """The (column, user, value) identity keys one track row would claim."""
    user_id = cast("str", values["user_id"])
    return {
        (name, user_id, value)
        for name in _IDENTITY_COLUMNS
        if isinstance(value := values.get(name), str) and value
    }


def build_title_artist_probe(
    variants: list[tuple[str, str]], *, user_id: str
) -> Select[tuple[DBTrack]]:
    """Canonical-reuse lookup: join ``tracks`` against a probe of title variants.

    ``variants`` are ``(normalized title variant, normalized artist)`` rows,
    already deduplicated. Both arrays travel as a single bind parameter each,
    so the statement text is identical at any batch size — one plan-cache
    entry, one ``pg_stat_statements`` row — and the planner gets join
    statistics from both sides rather than estimating a large disjunction.

    Module-level so the plan can be asserted on without going through a
    session.
    """
    zipped = func.unnest(
        bindparam("probe_titles", value=[t for t, _ in variants], type_=ARRAY(String)),
        bindparam("probe_artists", value=[a for _, a in variants], type_=ARRAY(String)),
    )
    # render_derived, not table_valued alone: PostgreSQL needs the derived
    # column list to name the two unnested arrays.
    probe = zipped.table_valued("title_variant", "artist_normalized").render_derived(
        name="probe"
    )

    return (
        select(DBTrack)
        .join_from(
            probe,
            DBTrack,
            and_(
                # Kept alongside RLS: a bind parameter the planner costs
                # properly and can drive the index prefix, and the only
                # tenancy guarantee under a BYPASSRLS role.
                DBTrack.user_id == user_id,
                DBTrack.artist_normalized == probe.c.artist_normalized,
                or_(
                    DBTrack.title_normalized == probe.c.title_variant,
                    DBTrack.title_stripped == probe.c.title_variant,
                ),
            ),
        )
        # A join yields a track once per matching probe row; the OR form it
        # replaced yielded it once. Without this the duplicate could be claimed
        # by a second pair, changing which pair a track resolves to.
        .distinct(DBTrack.id)
        .order_by(DBTrack.id.asc())
    )


def _empty_facets() -> TrackFacets:
    """Zero-valued facets for the total=0 early-exit path."""
    return TrackFacets(preference={}, liked={"true": 0, "false": 0}, connector={})


def _sql_case[K: str](values: Mapping[K, int], column: str) -> str:
    """Build a SQL CASE expression from a Python {string_key: int} mapping.

    Used to generate source-priority and preference-order ranking expressions
    from the domain constants so the merge CTE stays in sync with Python.
    """
    whens = " ".join(f"WHEN '{k}' THEN {v}" for k, v in values.items())
    return f"CASE {column} {whens} ELSE 0 END"


# SQL rank expressions derived from the domain's SOURCE_PRIORITY / PREFERENCE_ORDER.
# Used in the preference-merge CTE to keep conflict resolution consistent with
# the Python ``resolve_preference_change`` logic.
_LOSER_SOURCE_RANK = _sql_case(SOURCE_PRIORITY, "pc.loser_source")
_WINNER_SOURCE_RANK = _sql_case(SOURCE_PRIORITY, "pc.winner_source")
_LOSER_STATE_RANK = _sql_case(PREFERENCE_ORDER, "pc.loser_state")
_WINNER_STATE_RANK = _sql_case(PREFERENCE_ORDER, "pc.winner_state")


# Template with placeholder tokens that are substituted below at import time
# with the rank expressions derived from domain constants. Using a plain
# string + ``.replace()`` (rather than f-string) keeps the template auditable
# and avoids triggering SQL-injection lint heuristics, since there is no
# runtime interpolation.
_MOVE_REFERENCES_TEMPLATE = """
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
-- Move non-conflicting likes (explicitly exclude conflict IDs because CTE
-- snapshot semantics mean the DELETE above hasn't removed them from this
-- CTE's view of the table)
moved_likes AS (
    UPDATE track_likes
    SET track_id = :to_id, updated_at = :now
    WHERE track_id = :from_id
      AND id NOT IN (SELECT loser_id FROM like_conflicts)
    RETURNING id
),
-- Find preferences where both tracks have a row for the same user
preference_conflicts AS (
    SELECT
        loser.id AS loser_id,
        winner.id AS winner_id,
        loser.state AS loser_state,
        loser.source AS loser_source,
        loser.preferred_at AS loser_preferred_at,
        winner.state AS winner_state,
        winner.source AS winner_source
    FROM track_preferences loser
    JOIN track_preferences winner ON loser.user_id = winner.user_id
    WHERE loser.track_id = :from_id AND winner.track_id = :to_id
),
-- Copy loser's values into winner when loser wins the priority comparison:
--   source_priority(loser) > source_priority(winner) OR
--   (same source AND preference_order(loser) > preference_order(winner))
updated_winner_prefs AS (
    UPDATE track_preferences tp
    SET
        state = pc.loser_state,
        source = pc.loser_source,
        preferred_at = pc.loser_preferred_at,
        updated_at = :now
    FROM preference_conflicts pc
    WHERE tp.id = pc.winner_id
      AND (
          (__LOSER_SOURCE_RANK__) > (__WINNER_SOURCE_RANK__)
          OR (
              pc.loser_source = pc.winner_source
              AND (__LOSER_STATE_RANK__) > (__WINNER_STATE_RANK__)
          )
      )
    RETURNING tp.id
),
-- Delete all conflicting loser preferences (either winner kept its own
-- or winner was updated to loser's values — either way, loser row goes)
deleted_conflict_prefs AS (
    DELETE FROM track_preferences
    WHERE id IN (SELECT loser_id FROM preference_conflicts)
    RETURNING id
),
-- Move non-conflicting preferences (loser has row, winner doesn't for that user)
moved_prefs AS (
    UPDATE track_preferences
    SET track_id = :to_id, updated_at = :now
    WHERE track_id = :from_id
      AND id NOT IN (SELECT loser_id FROM preference_conflicts)
    RETURNING id
),
-- Move all preference events to winner (append-only — no conflict resolution)
moved_preference_events AS (
    UPDATE track_preference_events
    SET track_id = :to_id, updated_at = :now
    WHERE track_id = :from_id
    RETURNING id
)
SELECT
    (SELECT count(*) FROM moved_playlist_tracks) AS playlist_tracks_moved,
    (SELECT count(*) FROM moved_plays) AS plays_moved,
    (SELECT count(*) FROM deleted_conflict_likes) AS like_conflicts_resolved,
    (SELECT count(*) FROM moved_likes) AS likes_moved,
    (SELECT count(*) FROM deleted_conflict_prefs) AS pref_conflicts_resolved,
    (SELECT count(*) FROM moved_prefs) AS prefs_moved,
    (SELECT count(*) FROM moved_preference_events) AS preference_events_moved
"""


_MOVE_REFERENCES_SQL = (
    _MOVE_REFERENCES_TEMPLATE
    .replace("__LOSER_SOURCE_RANK__", _LOSER_SOURCE_RANK)
    .replace("__WINNER_SOURCE_RANK__", _WINNER_SOURCE_RANK)
    .replace("__LOSER_STATE_RANK__", _LOSER_STATE_RANK)
    .replace("__WINNER_STATE_RANK__", _WINNER_STATE_RANK)
)


# Typed row shapes for the single-row aggregate SELECTs that terminate the
# raw-SQL merge CTEs below. Field order mirrors the SQL SELECT aliases.


class _MoveReferenceCounts(NamedTuple):
    """Final SELECT of ``_MOVE_REFERENCES_SQL``."""

    playlist_tracks_moved: int
    plays_moved: int
    like_conflicts_resolved: int
    likes_moved: int
    pref_conflicts_resolved: int
    prefs_moved: int
    preference_events_moved: int


class _MergeMappingRow(NamedTuple):
    """One row of the ``merge_mappings_to_track`` CTE chain's tagged output.

    The chain returns rows rather than a count tuple because one of its arms
    produces edges the caller must record as events, and a CTE chain gets
    exactly one final SELECT to say everything in.
    """

    kind: str
    mapping_id: UUID
    successor_id: UUID | None
    user_id: str | None
    connector_name: str | None


class _MergeMetricCounts(NamedTuple):
    """Final SELECT of the ``merge_metrics_to_track`` CTE chain."""

    conflicts_resolved: int
    metrics_moved: int


class MappingHistoryLossError(DomainError):
    """Raised when deleting a track would take mapping history with it.

    Mappings have been append-only since migration 044. A retired row is not
    spare data: it is the artifact a ``superseded`` event names, and
    ``resolution_events`` is keyed by value with no FK, so the log outlives a
    delete that takes the row it describes. Production hit exactly that — an
    event recording a superseding mapping id that no longer resolves.

    ``track_mappings.track_id`` was ``ON DELETE CASCADE`` when that happened;
    migration 051 made it ``RESTRICT``, so the database now refuses too. This
    guard is still the one that reports *why*: a Core ``delete(DBTrack)`` past
    this repository raises a bare ``ForeignKeyViolation`` naming a constraint,
    not the history it was about to erase.

    A caller that genuinely means to remove the track moves its mappings
    somewhere first (what ``merge_mappings_to_track`` does) or retires them
    deliberately. There is no "delete the history too" affordance, because the
    history is the one thing a deletion cannot restore.
    """

    def __init__(self, track_id: UUID, mapping_ids: Sequence[UUID]) -> None:
        shown = ", ".join(str(mapping_id) for mapping_id in mapping_ids[:5])
        elided = len(mapping_ids) - 5
        suffix = f" (+{elided} more)" if elided > 0 else ""
        super().__init__(
            f"Refusing to delete track {track_id}: {len(mapping_ids)} mapping(s) "
            f"still point at it and would be cascade-deleted — {shown}{suffix}. "
            "Move or retire them first."
        )
        self.track_id = track_id
        self.mapping_ids = tuple(mapping_ids)


class TrackRepository(BaseRepository[DBTrack, Track]):
    """Repository for core track operations."""

    # ID type lookup definitions: non-connector types + shared connector→column map
    _TRACK_ID_TYPES: ClassVar[dict[str, str]] = {
        "internal": "id",
        "isrc": "isrc",
        **DenormalizedTrackColumns.COLUMN_MAP,
    }

    # Sortable column registry — restricts dynamic sort_field lookups to an
    # explicit allowlist (security: prevents arbitrary attribute access via
    # ``getattr(DBTrack, sort_field)``) and gives pyright a typed handle on
    # the column for ORDER BY / keyset construction. Keys must match the
    # column names in ``_SORT_SPECS`` below.
    _SORT_COLUMNS: ClassVar[dict[str, InstrumentedAttribute[Any]]] = {  # pyright: ignore[reportExplicitAny]  # InstrumentedAttribute is generic over heterogeneous column types
        "title": DBTrack.title,
        "artists_text": DBTrack.artists_text,
        "created_at": DBTrack.created_at,
        "duration_ms": DBTrack.duration_ms,
    }

    # Sort key → (db_column, direction). Infrastructure-owned ORDER BY
    # registry; the cursor-encoding mapping ``TRACK_SORT_COLUMNS`` in
    # ``src/application/pagination.py`` mirrors it — keep the two in sync.
    _SORT_SPECS: ClassVar[dict[str, tuple[str, str]]] = {
        "title_asc": ("title", "asc"),
        "title_desc": ("title", "desc"),
        "artist_asc": ("artists_text", "asc"),
        "artist_desc": ("artists_text", "desc"),
        "added_desc": ("created_at", "desc"),
        "added_asc": ("created_at", "asc"),
        "duration_asc": ("duration_ms", "asc"),
        "duration_desc": ("duration_ms", "desc"),
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

    @db_operation("get_track_by_id")
    async def get_track_by_id(
        self,
        track_id: UUID,
        *,
        user_id: str,
        load_relationships: list[str] | None = None,
    ) -> Track:
        """Get track by ID, scoped to user.

        Raises:
            NotFoundError: If track not found or belongs to another user.
        """
        stmt = self.select_by_id(track_id).where(DBTrack.user_id == user_id)
        if load_relationships:
            stmt = self.with_relationship(stmt, *load_relationships)
        else:
            stmt = self.with_default_relationships(stmt)

        db_model = await self.execute_select_one(stmt)
        if not db_model:
            from src.domain.exceptions import NotFoundError

            raise NotFoundError(f"Track {track_id} not found")

        return await self.mapper.to_domain(db_model)

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
        """Save track using native SQLAlchemy 2.0 features with optimistic locking.

        Update path (version > 0) uses WHERE version = :expected to detect
        concurrent modifications. Insert/upsert path (version == 0) is unaffected.
        """
        values = self._track_column_values(track)

        # --- Update path: optimistic locking via version check ---
        if track.version > 0:
            values["version"] = track.version + 1
            values["updated_at"] = datetime.now(UTC)

            stmt = (
                update(DBTrack)
                .where(DBTrack.id == track.id, DBTrack.version == track.version)
                .values(**values)
                .returning(DBTrack)
            )
            result = await self.session.execute(stmt)
            updated = result.scalar_one_or_none()

            if not updated:
                raise OptimisticLockError(track.id, track.version)

            await self._load_relationships_via_identity_map([updated])
            domain_track = await TrackMapper.to_domain_with_session(
                updated, self.session
            )
            if domain_track is None:
                raise OptimisticLockError(track.id, track.version)
            return domain_track

        # --- Insert/upsert path (version == 0, no locking needed) ---
        # Handle lookups by ISRC, MBID, or Spotify ID
        uid = track.user_id
        if track.isrc:
            if await self._isrc_collision_is_suspect(uid, track):
                # ISRC reuse across versions (remaster, re-release): don't
                # claim the contested ISRC and don't clobber the owner's
                # metadata — fall through the remaining keys (v0.8.18 FM2a).
                #
                # Every suspect defer is recorded by the warning log in
                # _isrc_collision_is_suspect. Callers WITH connector context
                # (Spotify import, cross-discovery, batch ingest) additionally
                # queue an isrc_suspect review and keep the ISRC on
                # connector_tracks.isrc. The direct-save path (local/manual
                # playlist via playlist/core.py, source_connector=None) has no
                # connector track to hold the ISRC or anchor a review, so its new
                # canonical is created without the ISRC and the warning log is
                # the only trail — acceptable, since a >10s-different recording
                # is a genuinely distinct track, not a merge to force.
                _ = values.pop("isrc", None)
            else:
                return await self.upsert({"user_id": uid, "isrc": track.isrc}, values)
        if "musicbrainz" in track.connector_track_identifiers:
            return await self.upsert(
                {
                    "user_id": uid,
                    "mbid": track.connector_track_identifiers["musicbrainz"],
                },
                values,
            )
        if "spotify" in track.connector_track_identifiers:
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
        return await self._refresh_and_map(db_track)

    @staticmethod
    def _track_column_values(track: Track) -> dict[str, object]:
        """The ``tracks`` columns one domain Track writes.

        The single payload builder behind ``save_track`` and ``save_tracks`` —
        a second copy is how the batch path and the per-row path start
        disagreeing about what a saved canonical contains.
        """
        if not track.title or not track.artists:
            raise ValueError("Track must have title and artists")

        values: dict[str, object] = {
            "title": track.title,
            "artists": {"names": [artist.name for artist in track.artists]},
            "album": track.album,
            "duration_ms": track.duration_ms,
            "release_date": track.release_date,
            "isrc": track.isrc,
            "user_id": track.user_id,
            **TrackMapper.normalized_columns(track),
        }

        # Add denormalized connector IDs (fast-path lookup columns)
        for connector, column in DenormalizedTrackColumns.COLUMN_MAP.items():
            if connector in track.connector_track_identifiers:
                values[column] = track.connector_track_identifiers[connector]
        return values

    @db_operation("save_tracks")
    async def save_tracks(self, tracks: Sequence[Track]) -> list[Track]:
        """Insert a batch of new canonical tracks in one statement.

        The batch form of ``save_track``'s insert arm: one probe for every
        identity key the batch carries, then one multi-row INSERT, in place of
        the four statements ``save_track`` spends per row. It exists because
        the Spotify inward resolver creates a whole 50-id chunk of canonicals
        at once, and at a 26ms round trip the per-row form was the bulk of
        import wall time.

        A row whose identity key is **already claimed** — by an existing
        canonical, or by an earlier row of this same batch — is not new, so it
        is handed to ``save_track``, which owns the upsert-or-defer decision
        (including the suspect-ISRC guard). On the paths this method exists
        for — the Spotify and Last.fm inward resolvers' chunk persists, its
        only two callers — that set is expected to be ≈0: creation is only
        reached for ids their mapping-lookup and canonical-reuse passes both
        missed. A non-trivial count of NEW rows landing there means the caller
        is colliding with rows those passes cannot see (the v0.10.2 mistenancy
        bug routed ~40 rows/chunk this way, at ~8 round trips each), so it is
        logged as a WARNING naming the caller bug rather than silently
        absorbed. The delegation itself stays: it is the drift case, not the
        hot one, and not a second implementation of the guard.

        Returns one Track per input, in input order.
        """
        if not tracks:
            return []

        values_by_index = {
            index: self._track_column_values(track)
            for index, track in enumerate(tracks)
        }
        # The optimistic-locking arm is a different statement shape entirely
        # (UPDATE … WHERE version = :expected); it has no batch form and no
        # caller here, so it goes back to the row-at-a-time path.
        claimed = await self._claimed_identity_keys([
            values
            for index, values in values_by_index.items()
            if tracks[index].version == 0
        ])

        rows: list[dict[str, object]] = []
        row_indexes: list[int] = []
        deferred: list[int] = []
        now = datetime.now(UTC)
        for index, values in values_by_index.items():
            keys = _identity_keys(values)
            if tracks[index].version > 0 or keys & claimed:
                deferred.append(index)
                continue
            # An earlier row of this batch now owns these keys — a later row
            # naming any of them would violate the unique constraint and take
            # the whole INSERT down with it.
            claimed |= keys
            rows.append({
                **values,
                # Explicit rather than left to the column defaults: a
                # multi-VALUES insert is compiled once for every row, and the
                # id is needed here anyway to put RETURNING back in input order.
                "id": uuid7(),
                "version": 1,
                "created_at": now,
                "updated_at": now,
            })
            row_indexes.append(index)

        # Only NEW rows are a caller bug: the optimistic-locking arm
        # (version > 0) is deferred by design — UPDATE … WHERE version has no
        # batch form — so it must not trip the alarm for the insert path.
        deferred_new = [index for index in deferred if tracks[index].version == 0]
        if deferred_new:
            sample_identity_keys = [
                key
                for index in deferred_new[:3]
                for key in sorted(_identity_keys(values_by_index[index]))
            ][:3]
            logger.warning(
                f"save_tracks deferred {len(deferred_new)} of {len(tracks)} new "
                f"rows to per-item save_track (~8 round trips each): their "
                f"identity keys are already claimed, so the caller's "
                f"mapping-lookup/reuse passes are not seeing the rows this "
                f"batch collides with",
                deferred_count=len(deferred_new),
                batch_size=len(tracks),
                sample_identity_keys=sample_identity_keys,
            )

        saved: dict[int, Track] = {}
        if rows:
            result = await self.session.execute(
                pg_insert(DBTrack).values(rows).returning(DBTrack)
            )
            inserted = {db_track.id: db_track for db_track in result.scalars().all()}
            for index, row in zip(row_indexes, rows, strict=True):
                # Freshly inserted rows have no mappings, likes or metrics yet,
                # so the mapper's eager-load-or-empty reads are exact — no
                # relationship round trip is needed to map them faithfully.
                saved[index] = await self.mapper.to_domain(
                    inserted[cast("UUID", row["id"])]
                )

        for index in deferred:
            saved[index] = await self.save_track(tracks[index])

        return [saved[index] for index in range(len(tracks))]

    async def _claimed_identity_keys(
        self, rows: Sequence[Mapping[str, object]]
    ) -> set[tuple[str, str, str]]:
        """Which (column, user, value) identity keys the table already holds.

        One statement for a whole batch, replacing ``save_track``'s per-row
        lookup: an OR across the three user-scoped unique keys. Existence is
        the only question asked — a claimed key sends its row to
        ``save_track``, which re-reads what it needs to decide between
        upserting into the owner and deferring a suspect ISRC collision.
        """
        wanted: dict[str, set[str]] = {name: set() for name in _IDENTITY_COLUMNS}
        users: set[str] = set()
        for row in rows:
            users.add(cast("str", row["user_id"]))
            for name, _user, value in _identity_keys(row):
                wanted[name].add(value)

        predicates: list[ColumnElement[bool]] = []
        if wanted["isrc"]:
            predicates.append(DBTrack.isrc.in_(sorted(wanted["isrc"])))
        if wanted["mbid"]:
            predicates.append(DBTrack.mbid.in_(sorted(wanted["mbid"])))
        if wanted["spotify_id"]:
            predicates.append(DBTrack.spotify_id.in_(sorted(wanted["spotify_id"])))
        if not predicates:
            return set()

        result = await self.session.execute(
            select(
                DBTrack.user_id, DBTrack.isrc, DBTrack.mbid, DBTrack.spotify_id
            ).where(DBTrack.user_id.in_(sorted(users)), or_(*predicates))
        )
        return {
            (name, user_id, value)
            for user_id, isrc, mbid, spotify_id in result.tuples()
            for name, value in (
                ("isrc", isrc),
                ("mbid", mbid),
                ("spotify_id", spotify_id),
            )
            if value is not None and value in wanted[name]
        }

    async def _isrc_collision_is_suspect(self, user_id: str, track: Track) -> bool:
        """Check whether merging into the ISRC owner would be a suspect merge.

        Reuses the engine's duration-based suspect check
        (``assess_isrc_match_reliability``) — no new heuristic. Not suspect
        when no owner exists or either duration is unknown (the pre-v0.8.18
        merge behavior is retained for those).
        """
        owner = (
            (
                await self.session.execute(
                    select(DBTrack.id, DBTrack.duration_ms).where(
                        DBTrack.user_id == user_id,
                        DBTrack.isrc == track.isrc,
                    )
                )
            )
            .tuples()
            .first()
        )
        if owner is None:
            return False
        owner_id, owner_duration_ms = owner

        duration_diff_ms = compute_duration_diff_ms(
            track.duration_ms, owner_duration_ms
        )
        suspect = assess_isrc_match_reliability(duration_diff_ms).suspect
        if suspect:
            logger.warning(
                "isrc_collision_deferred",
                isrc=track.isrc,
                owner_track_id=owner_id,
                owner_duration_ms=owner_duration_ms,
                incoming_duration_ms=track.duration_ms,
                incoming_title=track.title,
            )
        return suspect

    async def _refresh_and_map(self, db_track: DBTrack) -> Track:
        """Refresh relationships on a freshly inserted row and map to domain."""

        # Refresh with explicit eager loading of relationships to avoid lazy loading
        default_rels = self.mapper.get_default_relationships()
        if default_rels:
            rel_names = self._extract_relationship_names(default_rels)
            if rel_names:
                await self.session.refresh(db_track, attribute_names=rel_names)
            else:
                await self.session.refresh(db_track)
        else:
            await self.session.refresh(db_track)

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
        user_id: str,
        query: str | None = None,
        liked: bool | None = None,
        connector: str | None = None,
        preference: str | None = None,
        tags: Sequence[str] | None = None,
        tag_mode: Literal["and", "or"] = "and",
        namespace: str | None = None,
        sort_by: str = "title_asc",
        limit: int = 50,
        offset: int = 0,
        # Keyset pagination: seek after this (sort_value, id) pair.
        # ``after_value`` is opaque-to-store, comparable-to-use — its concrete
        # type depends on which sort column is active (str/int/datetime).
        after_value: object = None,
        after_id: UUID | None = None,
        include_total: bool = True,
        include_facets: bool = False,
    ) -> TrackListingPage:
        """List tracks with search, filters, sorting, and pagination.

        Supports both offset-based and keyset (cursor) pagination. When
        ``after_value`` and ``after_id`` are provided, uses a keyset WHERE clause
        for O(1) seeking regardless of page depth. Falls back to OFFSET otherwise.
        """
        conditions = self._build_list_filters(
            user_id=user_id,
            query=query,
            liked=liked,
            connector=connector,
            preference=preference,
            tags=tags,
            tag_mode=tag_mode,
            namespace=namespace,
        )

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
                    tracks=[],
                    total=0,
                    liked_track_ids=set(),
                    next_page_key=None,
                    facets=_empty_facets() if include_facets else None,
                )

        # Build data query with filters, sorting, pagination, and relationships
        data_stmt = self.select()
        if conditions:
            data_stmt = data_stmt.where(*conditions)
        data_stmt, sort_field = self._apply_sort_and_page(
            data_stmt,
            sort_by=sort_by,
            limit=limit,
            offset=offset,
            after_value=after_value,
            after_id=after_id,
        )
        data_stmt = self.with_default_relationships(data_stmt)

        result = await self.session.execute(data_stmt)
        db_tracks = list(result.scalars().all())

        tracks = [await self.mapper.to_domain(db_track) for db_track in db_tracks]

        # Build next-page keyset from the last row. The cursor value type
        # depends on which column is active — see TrackListingPage docstring.
        next_page_key: tuple[str | int | datetime | None, UUID] | None = None
        if db_tracks and len(db_tracks) == limit:
            last_db = db_tracks[-1]
            cursor_value = cast("object", getattr(last_db, sort_field))
            if cursor_value is None or isinstance(cursor_value, (str, int, datetime)):
                next_page_key = (cursor_value, last_db.id)

        # Get authoritative liked status from track_likes table for returned tracks
        track_ids = [t.id for t in tracks]
        liked_ids: set[UUID] = set()
        if track_ids:
            liked_stmt = (
                select(DBTrackLike.track_id)
                .where(
                    DBTrackLike.track_id.in_(track_ids),
                    DBTrackLike.is_liked.is_(True),
                    DBTrackLike.user_id == user_id,
                )
                .distinct()
            )
            liked_result = await self.session.execute(liked_stmt)
            liked_ids = set(liked_result.scalars().all())

        facets = (
            await self._compute_facets(conditions, user_id, total=total)
            if include_facets
            else None
        )

        return TrackListingPage(
            tracks=tracks,
            total=total,
            liked_track_ids=liked_ids,
            next_page_key=next_page_key,
            facets=facets,
        )

    def _build_list_filters(
        self,
        *,
        user_id: str,
        query: str | None,
        liked: bool | None,
        connector: str | None,
        preference: str | None,
        tags: Sequence[str] | None,
        tag_mode: Literal["and", "or"],
        namespace: str | None,
    ) -> list[ColumnElement[bool]]:
        """Build the WHERE conditions for list_tracks — always user-scoped.

        Every filter except the free-text ``query`` (a pg_trgm-accelerated ILIKE
        across title/album/artist) is a correlated ``id IN (subquery)`` term. The
        ``tag_mode="and"`` branch keeps its distinct-count HAVING trick so a track
        must carry every listed tag; ``"or"`` matches any.
        """
        conditions: list[ColumnElement[bool]] = [DBTrack.user_id == user_id]

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
                .where(DBTrackLike.is_liked.is_(True), DBTrackLike.user_id == user_id)
                .distinct()
            )
            if liked:
                conditions.append(DBTrack.id.in_(liked_subq))
            else:
                conditions.append(~DBTrack.id.in_(liked_subq))

        if connector:
            connector_subq = (
                select(DBTrackMapping.track_id)
                .where(
                    DBTrackMapping.connector_name == connector,
                    DBTrackMapping.user_id == user_id,
                    live_only(DBTrackMapping),
                )
                .distinct()
            )
            conditions.append(DBTrack.id.in_(connector_subq))

        if preference:
            pref_subq = select(DBTrackPreference.track_id).where(
                DBTrackPreference.state == preference,
                DBTrackPreference.user_id == user_id,
            )
            conditions.append(DBTrack.id.in_(pref_subq))

        if tags:
            # "and" needs each track to match EVERY listed tag; "or" needs any.
            # For "and" we group by track_id and require the distinct count to
            # hit the requested length — this is the standard relational
            # "tag-filter intersection" shape and keeps the filter as a single
            # correlated subquery.
            if tag_mode == "and":
                tag_subq = (
                    select(DBTrackTag.track_id)
                    .where(
                        DBTrackTag.tag.in_(tags),
                        DBTrackTag.user_id == user_id,
                    )
                    .group_by(DBTrackTag.track_id)
                    .having(func.count(func.distinct(DBTrackTag.tag)) == len(tags))
                )
            else:
                tag_subq = (
                    select(DBTrackTag.track_id)
                    .where(
                        DBTrackTag.tag.in_(tags),
                        DBTrackTag.user_id == user_id,
                    )
                    .distinct()
                )
            conditions.append(DBTrack.id.in_(tag_subq))

        if namespace:
            ns_subq = (
                select(DBTrackTag.track_id)
                .where(
                    DBTrackTag.namespace == namespace,
                    DBTrackTag.user_id == user_id,
                )
                .distinct()
            )
            conditions.append(DBTrack.id.in_(ns_subq))

        return conditions

    def _apply_sort_and_page(
        self,
        stmt: Select[tuple[DBTrack]],
        *,
        sort_by: str,
        limit: int,
        offset: int,
        after_value: object,
        after_id: UUID | None,
    ) -> tuple[Select[tuple[DBTrack]], str]:
        """Apply ORDER BY, keyset/offset pagination, and LIMIT to ``stmt``.

        Returns the augmented statement plus the resolved ``sort_field`` name so
        the caller can read the cursor value off the last row for the next-page
        key. Keyset seeking (``WHERE (sort_col, id) </> (:value, :id)``) engages
        when both ``after_value`` and ``after_id`` are provided; otherwise falls
        back to OFFSET.
        """
        # Resolve sort column from the registry (sort_by validated upstream;
        # unknown values fall back to title_asc).
        sort_field, sort_dir = self._SORT_SPECS.get(
            sort_by, self._SORT_SPECS["title_asc"]
        )
        col = self._SORT_COLUMNS[sort_field]

        # Keyset pagination: WHERE (sort_col, id) > (:value, :id) for ASC
        use_keyset = after_value is not None and after_id is not None
        if use_keyset:
            keyset_pair = tuple_(col, DBTrack.id)
            cursor_pair = tuple_(literal(after_value), literal(after_id))
            if sort_dir == "desc":
                stmt = stmt.where(keyset_pair < cursor_pair)
            else:
                stmt = stmt.where(keyset_pair > cursor_pair)
        else:
            stmt = stmt.offset(offset)

        stmt = stmt.order_by(
            col.desc() if sort_dir == "desc" else col.asc(),
            DBTrack.id.desc() if sort_dir == "desc" else DBTrack.id.asc(),
        )
        return stmt.limit(limit), sort_field

    async def _compute_facets(
        self,
        conditions: list[ColumnElement[bool]],
        user_id: str,
        *,
        total: int | None,
    ) -> TrackFacets:
        """Aggregate per-facet counts against the current filter context.

        Contextual-with-self semantics: each facet's count reflects the
        filter set *including* the dimension being counted, so the number
        is "what the user is seeing right now per bucket." Chosen over the
        Algolia-style peel-away-self shape because it maps to a single
        GROUP BY per dimension — no per-facet query rewrite. Cheap at mixd
        scale thanks to existing indexes on track_preferences(user_id, state),
        track_likes(user_id, is_liked), and track_mappings(user_id, connector_name).
        """
        # Preference facet — LEFT JOIN so unrated tracks count under "unrated".
        pref_stmt = (
            select(DBTrackPreference.state, func.count(DBTrack.id))
            .select_from(DBTrack)
            .outerjoin(
                DBTrackPreference,
                and_(
                    DBTrackPreference.track_id == DBTrack.id,
                    DBTrackPreference.user_id == user_id,
                ),
            )
            .where(*conditions)
            .group_by(DBTrackPreference.state)
        )
        pref_rows = cast(
            "list[tuple[str | None, int]]",
            (await self.session.execute(pref_stmt)).all(),
        )
        preference: dict[str, int] = {}
        for state, count in pref_rows:
            preference[state if state is not None else "unrated"] = count

        # Liked facet — count distinct track_ids with a True like-row on
        # the canonical 'mixd' service. Everything else counts as "false".
        liked_true_stmt = (
            select(func.count(func.distinct(DBTrack.id)))
            .select_from(DBTrack)
            .join(DBTrackLike, DBTrackLike.track_id == DBTrack.id)
            .where(
                *conditions,
                DBTrackLike.is_liked.is_(True),
                DBTrackLike.user_id == user_id,
            )
        )
        liked_true_count = int(
            (await self.session.execute(liked_true_stmt)).scalar_one()
        )
        filtered_total = total if total is not None else sum(preference.values())
        liked: dict[str, int] = {
            "true": liked_true_count,
            "false": max(filtered_total - liked_true_count, 0),
        }

        # Connector facet — one row per connector the user has in the filtered set.
        connector_stmt = (
            select(
                DBTrackMapping.connector_name,
                func.count(func.distinct(DBTrack.id)),
            )
            .select_from(DBTrack)
            .join(DBTrackMapping, DBTrackMapping.track_id == DBTrack.id)
            .where(
                *conditions,
                DBTrackMapping.user_id == user_id,
                live_only(DBTrackMapping),
            )
            .group_by(DBTrackMapping.connector_name)
        )
        connector_rows = cast(
            "list[tuple[str, int]]",
            (await self.session.execute(connector_stmt)).all(),
        )
        connector: dict[str, int] = dict(connector_rows)

        return TrackFacets(
            preference=preference,
            liked=liked,
            connector=connector,
        )

    # -------------------------------------------------------------------------
    # TRACK MERGE OPERATIONS
    # -------------------------------------------------------------------------

    @db_operation("move_references_to_track")
    async def move_references_to_track(self, from_id: UUID, to_id: UUID) -> None:
        """Move all foreign key references from one track to another.

        Single CTE chain: moves playlist tracks, plays, likes, and preferences
        (with conflict resolution). Preference events are moved unconditionally
        — the append-only log preserves history from both tracks.
        """
        result = await self.session.execute(
            text(_MOVE_REFERENCES_SQL),
            {"from_id": from_id, "to_id": to_id, "now": datetime.now(UTC)},
        )
        counts = _MoveReferenceCounts._make(result.one())
        logger.debug(
            f"Moved references: {from_id} → {to_id} "
            f"(playlist_tracks={counts.playlist_tracks_moved}, "
            f"plays={counts.plays_moved}, "
            f"like_conflicts={counts.like_conflicts_resolved}, "
            f"likes_moved={counts.likes_moved}, "
            f"pref_conflicts={counts.pref_conflicts_resolved}, "
            f"prefs_moved={counts.prefs_moved}, "
            f"preference_events_moved={counts.preference_events_moved})"
        )

    @db_operation("merge_mappings_to_track")
    async def merge_mappings_to_track(
        self, from_id: UUID, to_id: UUID
    ) -> list[SupersessionEdge]:
        """Merge connector mappings from one track to another, append-only.

        Single CTE chain, four arms:

        - **Same connector track on both sides.** One live row must survive, so
          the loser is *retired into* the winner — ``superseded_at`` /
          ``supersession_reason = 'conflation'`` / ``superseded_by_id`` pointing
          at the winner's live row — and moved to the winner track so the loser
          track's cascade cannot take the history with it. It is not deleted:
          a merge is an assertion about identity, and deleting the row that
          assertion revises destroys the only evidence for it. Reachable only
          on data predating migration 044 —
          ``uq_track_mappings_live_connector`` now forbids the two live rows
          this arm reconciles — so it is a restore path, not a hot one.
        - **Different connector tracks.** Both stay live on the winner and the
          loser's becomes secondary. One winner per connector is promoted to
          primary *only if the track has none*: ``uq_primary_mapping`` admits
          a single live primary per (user, track, connector), and deposing an
          existing primary is not something a merge gets to decide.
        - **Non-conflicting live rows** follow their track and are re-stamped as
          manually pinned.
        - **Superseded rows** follow their track and *nothing else changes*.
          Rewriting ``origin`` on a retired row would edit history — that row
          records what mixd believed at the time, and it did not believe it
          because a human pinned it.

        No decision field (``confidence``, ``match_method``) is rewritten on any
        row. The pre-v0.10.2 chain copied a better-scoring loser's confidence
        onto the winner in place, which is a re-scoring disguised as a merge;
        the append-only table has no way to express that except as a
        supersession, and a merge is not the moment to re-score.

        Returns the conflation edges for the caller to record as events.
        """
        result = await self.session.execute(
            text("""
            WITH all_conflicts AS (
                -- DISTINCT ON collapses the source to one winner per loser.
                -- A loser can join two live winners on the same connector
                -- (one sharing its connector track, one not), and Branch 1
                -- and Branch 2 would then both UPDATE that loser row in a
                -- single statement — PostgreSQL applies one arm, and which
                -- one is not predictable. If the diff-ext arm won, the row
                -- would survive live on the winner track and trip
                -- uq_track_mappings_live_connector, aborting the merge.
                --
                -- Reachable only on pre-044 data. That index makes two live
                -- rows on one connector track impossible, so the
                -- same-connector-track join can only pair rows a database
                -- restored from an older dump still holds. Kept because that
                -- restore is exactly when a merge would hit it, and the arm
                -- it guards used to DELETE a mapping outright. The tests
                -- reach it by standing the index down for one transaction.
                -- (No semicolons in this literal, please — the live-rows
                -- conformance guard splits fragments on them.)
                --
                -- Preferring the same-connector-track winner is the correct
                -- reading when both apply: retiring a loser into a mapping
                -- that already asserts the same identity is the narrower
                -- claim. winner.id is the final tiebreak so the choice is
                -- deterministic across runs rather than plan-dependent.
                SELECT DISTINCT ON (loser.id)
                    loser.id AS loser_id,
                    winner.id AS winner_id,
                    loser.connector_track_id AS loser_ct_id,
                    winner.connector_track_id AS winner_ct_id,
                    loser.connector_name AS connector_name,
                    winner.is_primary AS winner_is_primary
                FROM track_mappings loser
                JOIN track_mappings winner
                  ON loser.connector_name = winner.connector_name
                 -- track_mappings carries its own user_id, so without this
                 -- two tenants' mappings on the same pair of tracks pair up
                 -- and the merge stamps a cross-tenant superseded_by_id.
                 -- RLS does not backstop it: PDR-002 records that the Neon
                 -- owner role has BYPASSRLS, so this predicate *is* the
                 -- isolation, not defense in depth.
                 AND loser.user_id = winner.user_id
                WHERE loser.track_id = :from_id AND winner.track_id = :to_id
                  AND loser.superseded_at IS NULL
                  AND winner.superseded_at IS NULL
                ORDER BY
                    loser.id,
                    (loser.connector_track_id = winner.connector_track_id) DESC,
                    winner.id
            ),

            -- Branch 1: same connector track — retire the loser into the winner.
            -- Pre-044 shape only (see all_conflicts): the live-connector index
            -- forbids the two rows this arm exists to reconcile.
            retired_same_ext_losers AS (
                UPDATE track_mappings tm
                SET
                    track_id = :to_id,
                    superseded_at = :now,
                    supersession_reason = :conflation,
                    superseded_by_id = ac.winner_id,
                    is_primary = FALSE,
                    updated_at = :now
                FROM all_conflicts ac
                WHERE tm.id = ac.loser_id
                  AND ac.loser_ct_id = ac.winner_ct_id
                  AND tm.superseded_at IS NULL
                RETURNING tm.id, ac.winner_id AS successor_id,
                          tm.user_id, tm.connector_name
            ),

            -- Branch 2: different connector tracks — keep both, winner primary.
            --
            -- uq_primary_mapping admits exactly one live primary per
            -- (user_id, track_id, connector_name), so promoting every
            -- diff-ext winner is unsafe twice over: two losers can select
            -- two different winners on one connector, and the winner track
            -- may already have a primary that is not in the conflict set.
            -- Pick one winner per connector, then promote only when the
            -- track has no live primary — so this arm heals a track that
            -- lacks one and is a no-op for a track that already has one.
            -- Flipping an existing primary is not the merge's business.
            diff_ext_winner_choice AS (
                SELECT DISTINCT ON (connector_name) winner_id
                FROM all_conflicts
                WHERE loser_ct_id != winner_ct_id
                ORDER BY connector_name, winner_is_primary DESC, winner_id
            ),
            updated_diff_ext_winners AS (
                UPDATE track_mappings tm
                SET is_primary = TRUE, updated_at = :now
                FROM diff_ext_winner_choice c
                WHERE tm.id = c.winner_id
                  AND tm.superseded_at IS NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM track_mappings p
                      WHERE p.user_id = tm.user_id
                        AND p.track_id = tm.track_id
                        AND p.connector_name = tm.connector_name
                        AND p.is_primary
                        AND p.superseded_at IS NULL
                  )
                RETURNING tm.id
            ),
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
                  AND tm.superseded_at IS NULL
                RETURNING tm.id
            ),

            -- Non-conflicting live rows: move and re-stamp.
            moved_live_non_conflict AS (
                UPDATE track_mappings
                SET
                    track_id = :to_id,
                    origin = :manual_override,
                    updated_at = :now
                WHERE track_id = :from_id
                  AND superseded_at IS NULL
                  AND id NOT IN (SELECT loser_id FROM all_conflicts)
                RETURNING id
            ),
            -- Retired rows: move only. History follows its track untouched.
            moved_history AS (
                UPDATE track_mappings
                SET track_id = :to_id
                WHERE track_id = :from_id
                  AND superseded_at IS NOT NULL
                RETURNING id
            )
            SELECT 'retired' AS kind, id, successor_id, user_id, connector_name
            FROM retired_same_ext_losers
            UNION ALL
            SELECT 'diff_ext', id, NULL::uuid, NULL::varchar, NULL::varchar
            FROM moved_diff_ext_losers
            UNION ALL
            SELECT 'diff_ext_winner', id, NULL::uuid, NULL::varchar, NULL::varchar
            FROM updated_diff_ext_winners
            UNION ALL
            SELECT 'non_conflict', id, NULL::uuid, NULL::varchar, NULL::varchar
            FROM moved_live_non_conflict
            UNION ALL
            SELECT 'history', id, NULL::uuid, NULL::varchar, NULL::varchar
            FROM moved_history
            """),
            {
                "from_id": from_id,
                "to_id": to_id,
                "now": datetime.now(UTC),
                "manual_override": MappingOrigin.MANUAL_OVERRIDE,
                "conflation": _CONFLATION,
            },
        )
        rows = [_MergeMappingRow._make(row) for row in result.all()]
        edges = [
            SupersessionEdge(
                predecessor_id=row.mapping_id,
                successor_id=row.successor_id,
                user_id=row.user_id,
                connector_name=row.connector_name,
            )
            for row in rows
            if row.kind == "retired"
            and row.successor_id is not None
            and row.user_id is not None
            and row.connector_name is not None
        ]
        # Every arm wrote through Core, so the identity map still holds the
        # pre-merge copies of these rows and of both tracks' collections.
        expire_mapping_identity(
            self.session,
            mapping_ids=[row.mapping_id for row in rows],
            track_ids=[from_id, to_id],
        )
        counted = Counter(row.kind for row in rows)
        logger.debug(
            f"Merged track mappings: {from_id} → {to_id} "
            f"({counted['retired']} same external ID retired, "
            f"{counted['diff_ext']} different external ID conflicts, "
            f"{counted['non_conflict']} non-conflicts moved, "
            f"{counted['history']} superseded rows followed)"
        )
        return edges

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
        counts = _MergeMetricCounts._make(result.one())
        logger.debug(
            f"Merged track metrics: {from_id} → {to_id} "
            f"({counts.conflicts_resolved} conflicts resolved, "
            f"{counts.metrics_moved} moved)"
        )

    @db_operation("hard_delete_track")
    async def hard_delete_track(self, track_id: UUID) -> None:
        """Permanently delete a track — refused while any mapping still points at it.

        Deleting a track used to take its mappings with it:
        ``track_mappings.track_id`` was ``ON DELETE CASCADE``, so the statement
        silently removed every mapping row on the track, retired ones included.
        Since 044 those rows are append-only history, and ``resolution_events``
        references them *by value* — no FK, nothing to cascade, nothing to
        complain — so the log was left describing a decision whose artifact was
        gone. That is the state production reached, and migration 051 closed it
        at the constraint (``RESTRICT`` on all three mapping FKs).

        This guard is the readable half of that pair. The constraint stops any
        path, including a Core ``delete(DBTrack)`` that never reaches this
        method; the guard is what turns the refusal into an error naming the
        mapping ids at risk instead of a constraint violation.

        So the check is "any mapping at all", not "any superseded mapping": a
        live row is a future predecessor, and by the time an event names it the
        track is long deleted. The one caller that legitimately deletes a track
        (:class:`~src.infrastructure.services.track_merge_service.TrackMergeService`)
        moves every mapping — live and retired — onto the winner first, so the
        guard is silent on the happy path and only speaks when something is
        about to take history with it.

        Raises:
            MappingHistoryLossError: When mappings would be cascade-deleted.
        """
        stranded = await self._mappings_on_track(track_id)
        if stranded:
            raise MappingHistoryLossError(track_id, stranded)
        await self.session.execute(delete(DBTrack).where(DBTrack.id == track_id))
        logger.debug(f"Hard deleted track: {track_id}")

    async def _mappings_on_track(self, track_id: UUID) -> list[UUID]:
        """Every mapping id a delete of this track would cascade away.

        Deliberately unscoped by tenant and ``include_superseded``: the cascade
        the FK performs is neither user-scoped nor live-scoped, so a guard that
        saw less than the cascade does would wave through the rows most worth
        keeping — another tenant's mapping on the same track, or the retired
        row an event already names.
        """
        result = await self.session.execute(
            select(DBTrackMapping.id)
            .where(DBTrackMapping.track_id == track_id)
            .execution_options(**{INCLUDE_SUPERSEDED: True})
        )
        return list(result.scalars().all())

    # ── Lookup queries ───────────────────────────────────────────────

    @db_operation("find_tracks_by_title_artist")
    async def find_tracks_by_title_artist(
        self, pairs: list[tuple[str, str]], *, user_id: str
    ) -> dict[tuple[str, str], Track]:
        """Find existing tracks matching (title, first_artist) pairs.

        Uses pre-computed normalized columns for fuzzy matching that handles
        diacritics, smart quotes, articles, and feat./ft. variations.
        Also matches via parenthetical-stripped form so "Song (feat. X)" ↔ "Song".
        Returns only the first match per pair (oldest track by ID).

        Probes by joining against an unnested array pair rather than OR-ing one
        condition group per input. The two forms match identically — a pair's
        four title predicates are exactly ``{normalized, stripped} x
        {title_normalized, title_stripped}``, so emitting one probe row per
        distinct title variant covers the same cross product — but this one
        takes two bind parameters at any batch size instead of 2N, which keeps
        the statement text (and so its plan-cache entry and pg_stat_statements
        row) constant, and gives the planner join statistics from both sides
        instead of a ~250-arm disjunction it must estimate independently.

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

        # Deduplicated: a chunk replays the same tracks, and the two title
        # variants coincide whenever a title has no parenthetical.
        variants: dict[tuple[str, str], None] = {}
        for (norm_title, norm_artist), stripped_title in zip(
            normalized_pairs, stripped_pairs, strict=True
        ):
            variants[norm_title, norm_artist] = None
            variants[stripped_title, norm_artist] = None

        result = await self.session.execute(
            build_title_artist_probe(list(variants), user_id=user_id)
        )
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
            # Insertion-ordered, not a set: with a set the winning pair would
            # depend on PYTHONHASHSEED when the two variants differ.
            for title_val in dict.fromkeys((
                db_track.title_normalized or "",
                db_track.title_stripped or "",
            )):
                lower_key = lookup_to_lower.get((title_val, artist_norm))
                if lower_key and lower_key not in matched:
                    matched[lower_key] = await self.mapper.to_domain(db_track)
                    break

        return matched

    @db_operation("find_tracks_by_isrcs")
    async def find_tracks_by_isrcs(
        self, isrcs: list[str], *, user_id: str
    ) -> dict[str, Track]:
        """Batch lookup tracks by ISRC. Returns {isrc: Track} for found tracks."""
        return await self._find_tracks_by_unique_column(
            DBTrack.isrc, isrcs, user_id=user_id
        )

    @db_operation("find_tracks_by_mbids")
    async def find_tracks_by_mbids(
        self, mbids: list[str], *, user_id: str
    ) -> dict[str, Track]:
        """Batch lookup tracks by MusicBrainz Recording ID (MBID)."""
        return await self._find_tracks_by_unique_column(
            DBTrack.mbid, mbids, user_id=user_id
        )

    # ── Integrity check queries ──────────────────────────────────────

    @db_operation("find_duplicate_tracks_by_fingerprint")
    async def find_duplicate_tracks_by_fingerprint(
        self, *, user_id: str
    ) -> list[dict[str, object]]:
        """Find tracks with identical (title, first_artist, album) tuples."""
        # JSONB array index → text via .as_string() — SQLAlchemy stub returns Any;
        # the declared annotation caps the spread to this one boundary line.
        first_artist: ColumnElement[str] = DBTrack.artists["names"][0].as_string()  # pyright: ignore[reportAny]  # SQLAlchemy JSONB index/cast
        stmt = (
            select(
                DBTrack.title,
                first_artist.label("first_artist"),
                DBTrack.album,
                func.count().label("count"),
                # type_ declares the (text) result type; emitted SQL is unchanged.
                func.string_agg(sa_cast(DBTrack.id, String), ",", type_=String).label(
                    "track_ids"
                ),
            )
            .where(
                DBTrack.user_id == user_id,
                DBTrack.title.isnot(None),
                func.length(DBTrack.title) > 0,
            )
            .group_by(DBTrack.title, first_artist, DBTrack.album)
            .having(func.count() > 1)
        )
        result = await self.session.execute(stmt)
        return [
            {
                "title": title,
                "artist": artist,
                "album": album,
                "count": count,
                "track_ids": [UUID(x) for x in track_ids.split(",")],
            }
            for title, artist, album, count, track_ids in result.tuples()
        ]

    # -------------------------------------------------------------------------
    # PRIVATE HELPERS
    # -------------------------------------------------------------------------

    async def _find_tracks_by_unique_column(
        self,
        column: InstrumentedAttribute[Any],  # pyright: ignore[reportExplicitAny]  # InstrumentedAttribute is generic over column type
        values: list[str],
        *,
        user_id: str,
    ) -> dict[str, Track]:
        """Batch lookup tracks by a unique string column (ISRC, MBID, etc.)."""
        if not values:
            return {}

        stmt = self.select().where(DBTrack.user_id == user_id, column.in_(values))
        stmt = self.with_default_relationships(stmt)
        result = await self.session.execute(stmt)
        rows = result.scalars().all()

        # InstrumentedAttribute.key is str at runtime; stubs declare it str | None.
        col_key: str = column.key or ""
        matched: dict[str, Track] = {}
        for db_track in rows:
            key = cast("str | None", getattr(db_track, col_key))
            if key:
                matched[key] = await self.mapper.to_domain(db_track)
        return matched
