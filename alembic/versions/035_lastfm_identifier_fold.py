"""Fold Last.fm connector_tracks onto their normalized ``artist::title`` key.

Historically, Last.fm connector tracks were minted under several identifier
schemes — full last.fm URLs, MusicBrainz UUIDs, ``lastfm:``-prefixed strings,
and un-normalized (mixed-case / untrimmed) ``artist::title`` composites. The one
canonical scheme is now ``make_lastfm_identifier`` output — Python
``artist.strip().lower() + '::' + title.strip().lower()``. The fold key is
recomputed in Python (see ``_norm``), NOT SQL: ``str.strip()`` removes all
Unicode whitespace (tab / newline / NBSP) that SQL ``btrim`` leaves in place, so
an SQL recompute would diverge from the runtime mint and rename rows to a key a
later import could never reproduce. This migration collapses every
duplicate/variant row onto that key so a Last.fm track owns ONE connector row.
Detection is uniform: a row needs work iff its identifier ≠ the recomputed key
(covers URL / mbid / ``lastfm:`` / case / whitespace variants alike).

OFFLINE by design: the fold key is recomputed from STORED metadata
(``artists->'names'->>0`` + ``title``), never a live API call. Last.fm's runtime
autocorrect may have minted an identifier that diverges from this recomputed key
— that is ACCEPTABLE. The folded survivor keeps its runtime key unless it is a
strict duplicate of another row, so a later raw-key lookup still resolves the
same survivor and NO second row is ever re-minted.

Per target key — survivor = the row already keyed on target, else the one with
the most mappings, else the oldest ``created_at``:
- ``track_mappings`` move loser→survivor. On the ``(user_id, connector_track_id,
  connector_name)`` unique conflict keep manual_override > is_primary > higher
  confidence and delete the other; afterwards re-assert exactly ONE primary per
  ``(user_id, track_id, 'lastfm')`` (the ``uq_primary_mapping`` partial index).
- ``match_reviews`` move loser→survivor. On the
  ``uq_match_reviews_user_track_connector`` conflict keep the earlier
  (``created_at``) row.
- ``playlist_tracks.connector_track_id`` repointed loser→survivor. Its FK is
  ON DELETE SET NULL, so deleting the loser would otherwise silently null this
  best-effort re-resolution pointer; repointing preserves it. (Beyond the
  original task list, but keeps the fold lossless — no unique key to conflict.)
- ``raw_metadata`` shallow-merged (survivor wins); every loser identifier is
  appended to ``raw_metadata['folded_from']`` (a JSON array) for provenance.
- the loser ``connector_tracks`` row is deleted; the survivor is renamed to
  target when its identifier differs. Renames run in a SECOND phase that first
  parks every to-be-renamed survivor at a unique temporary identifier, then
  assigns final targets — so a survivor whose current identifier happens to
  equal another group's target cannot trip the ``(connector_name,
  connector_track_identifier)`` unique constraint mid-fold. Any residual row
  still holding a target (e.g. an unfoldable/skipped row) is absorbed into the
  survivor before its rename.

``connector_plays`` is UNTOUCHED — its identifiers are already
``make_lastfm_identifier`` output (see migration 033 / the Last.fm ingest path).

RLS bracket: the ``track_mappings`` / ``match_reviews`` data phase runs under
``NO FORCE ROW LEVEL SECURITY`` (re-``FORCE``'d at the end). Neon's owner role has
BYPASSRLS, so this is inert in production today, but without it a future
non-bypass owner would see ZERO rows (the ``user_isolation`` policy tests
``user_id = current_setting('app.user_id', TRUE)``, which is NULL here) and the
fold would silently move nothing. A rollback re-forces automatically (DDL is
transactional), so no try/finally is needed.

Downgrade is a NO-OP: the fold is lossy (losers deleted, mappings/reviews merged)
and not mechanically reversible. ``folded_from`` is the per-row audit trail
(precedent for an irreversible data migration: 012).

Revision ID: 035_lastfm_identifier_fold
Revises: 034_track_mappings_last_seen_at
Create Date: 2026-07-03
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
import json
from typing import cast
import uuid

import sqlalchemy as sa

from alembic import op
from src.config.logging import get_logger

revision: str = "035_lastfm_identifier_fold"
down_revision: str | None = "034_track_mappings_last_seen_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen literals — a migration is a historical snapshot, so it must not import
# app enums that can drift. These mirror MappingOrigin.MANUAL_OVERRIDE and the
# Last.fm connector name at the time of writing.
_LASTFM = "lastfm"
_MANUAL_OVERRIDE = "manual_override"
# The two RLS-protected tables whose rows the data phase rewrites.
_RLS_TABLES: tuple[str, ...] = ("track_mappings", "match_reviews")

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Candidate:
    """A Last.fm connector_tracks row considered for folding."""

    id: uuid.UUID
    identifier: str
    mapping_count: int
    created_at: datetime
    raw_metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class _Mapping:
    """A ``track_mappings`` row (only the columns the fold reasons about)."""

    id: uuid.UUID
    user_id: str
    connector_name: str
    track_id: uuid.UUID
    origin: str
    is_primary: bool
    confidence: int


@dataclass(frozen=True, slots=True)
class _Review:
    """A ``match_reviews`` row (only the columns the fold reasons about)."""

    id: uuid.UUID
    user_id: str
    connector_name: str
    track_id: uuid.UUID
    created_at: datetime


def _str_list(value: object) -> list[str]:
    """The string members of ``value`` when it is a JSON array, else ``[]``."""
    if isinstance(value, list):
        items = cast("list[object]", value)
        return [x for x in items if isinstance(x, str)]
    return []


def _dedupe(values: Iterable[str]) -> list[str]:
    """Deduplicate preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _norm(value: str | None) -> str:
    """Normalize one side of the fold key: strip + lowercase.

    MUST stay byte-for-byte in lockstep with ``make_lastfm_identifier``
    (src/infrastructure/connectors/lastfm/identifiers.py), which does
    ``value.strip().lower() if value else ""``. The key is recomputed HERE in
    Python, not in SQL, because Python ``str.strip()`` removes all Unicode
    whitespace (tab / newline / NBSP) while Postgres ``btrim`` strips only ASCII
    spaces — an SQL recompute would produce a key the runtime mint never yields.
    Inlined (not imported) so this historical migration stays self-contained.
    """
    return value.strip().lower() if value else ""


def _mapping_beats(cand: _Mapping, incumbent: _Mapping) -> bool:
    """Whether ``cand`` should win the (user, connector) unique conflict.

    Priority: manual_override > is_primary > higher confidence. A full tie keeps
    the incumbent (the survivor's existing row) — deterministic.
    """
    cand_manual = cand.origin == _MANUAL_OVERRIDE
    inc_manual = incumbent.origin == _MANUAL_OVERRIDE
    if cand_manual != inc_manual:
        return cand_manual
    if cand.is_primary != incumbent.is_primary:
        return cand.is_primary
    return cand.confidence > incumbent.confidence


def _primary_rank(mapping: _Mapping) -> tuple[bool, int, str]:
    """Sort key electing the sole primary: manual_override, then confidence, id."""
    return (mapping.origin != _MANUAL_OVERRIDE, -mapping.confidence, str(mapping.id))


def _load_candidates(bind: sa.Connection) -> tuple[dict[str, list[_Candidate]], int]:
    """Group foldable Last.fm rows by target key; count the unfoldable ones.

    A row is unfoldable when its stored first-artist name or title is blank
    after trimming — the composite key would be meaningless, so it is skipped.
    """
    result = bind.execute(
        sa.text(
            "SELECT ct.id, ct.connector_track_identifier, "
            "ct.artists->'names'->>0 AS artist_raw, "
            "ct.title AS title_raw, "
            "ct.created_at, ct.raw_metadata, "
            "(SELECT count(*) FROM track_mappings tm "
            " WHERE tm.connector_track_id = ct.id) AS mapping_count "
            "FROM connector_tracks ct "
            "WHERE ct.connector_name = :lastfm "
            "ORDER BY ct.id"
        ),
        {"lastfm": _LASTFM},
    )
    groups: dict[str, list[_Candidate]] = {}
    unfoldable = 0
    for row in result:
        # Recompute the key in Python (via _norm) so it matches the runtime mint
        # exactly — raw metadata in, strip().lower() here, never SQL btrim/lower.
        artist_key = _norm(cast("str | None", row[2]))
        title_key = _norm(cast("str | None", row[3]))
        if not artist_key or not title_key:
            unfoldable += 1
            continue
        target = f"{artist_key}::{title_key}"
        raw = cast("object", row[5])
        candidate = _Candidate(
            id=cast("uuid.UUID", row[0]),
            identifier=cast("str", row[1]),
            created_at=cast("datetime", row[4]),
            raw_metadata=cast("dict[str, object]", raw)
            if isinstance(raw, dict)
            else {},
            mapping_count=cast("int", row[6]),
        )
        groups.setdefault(target, []).append(candidate)
    return groups, unfoldable


def _choose_survivor(members: list[_Candidate], target: str) -> _Candidate:
    """Survivor = already-canonical row, else most mappings, else oldest."""
    for member in members:
        if member.identifier == target:
            return member
    return min(members, key=lambda m: (-m.mapping_count, m.created_at, str(m.id)))


def _query_mappings(
    bind: sa.Connection, sql: str, params: dict[str, object]
) -> list[_Mapping]:
    return [
        _Mapping(
            id=cast("uuid.UUID", r[0]),
            user_id=cast("str", r[1]),
            connector_name=cast("str", r[2]),
            track_id=cast("uuid.UUID", r[3]),
            origin=cast("str", r[4]),
            is_primary=cast("bool", r[5]),
            confidence=cast("int", r[6]),
        )
        for r in bind.execute(sa.text(sql), params)
    ]


def _query_reviews(
    bind: sa.Connection, sql: str, params: dict[str, object]
) -> list[_Review]:
    return [
        _Review(
            id=cast("uuid.UUID", r[0]),
            user_id=cast("str", r[1]),
            connector_name=cast("str", r[2]),
            track_id=cast("uuid.UUID", r[3]),
            created_at=cast("datetime", r[4]),
        )
        for r in bind.execute(sa.text(sql), params)
    ]


_MAPPING_COLS = "id, user_id, connector_name, track_id, origin, is_primary, confidence"
_REVIEW_COLS = "id, user_id, connector_name, track_id, created_at"


def _move_mappings(
    bind: sa.Connection,
    loser_id: uuid.UUID,
    survivor_id: uuid.UUID,
    affected: set[tuple[str, uuid.UUID]],
) -> None:
    """Reassign the loser's track_mappings to the survivor, resolving conflicts.

    Records every ``(user_id, track_id)`` it touches so the caller can re-assert
    the single-primary invariant afterwards.
    """
    losers = _query_mappings(
        bind,
        f"SELECT {_MAPPING_COLS} FROM track_mappings "
        "WHERE connector_track_id = :lid ORDER BY id",
        {"lid": loser_id},
    )
    for mapping in losers:
        incumbents = _query_mappings(
            bind,
            f"SELECT {_MAPPING_COLS} FROM track_mappings "
            "WHERE connector_track_id = :sid AND user_id = :uid "
            "AND connector_name = :cn",
            {"sid": survivor_id, "uid": mapping.user_id, "cn": mapping.connector_name},
        )
        affected.add((mapping.user_id, mapping.track_id))
        if not incumbents:
            bind.execute(
                sa.text(
                    "UPDATE track_mappings SET connector_track_id = :sid WHERE id = :id"
                ),
                {"sid": survivor_id, "id": mapping.id},
            )
            continue
        incumbent = incumbents[0]
        affected.add((incumbent.user_id, incumbent.track_id))
        if _mapping_beats(mapping, incumbent):
            bind.execute(
                sa.text("DELETE FROM track_mappings WHERE id = :id"),
                {"id": incumbent.id},
            )
            bind.execute(
                sa.text(
                    "UPDATE track_mappings SET connector_track_id = :sid WHERE id = :id"
                ),
                {"sid": survivor_id, "id": mapping.id},
            )
        else:
            bind.execute(
                sa.text("DELETE FROM track_mappings WHERE id = :id"),
                {"id": mapping.id},
            )


def _move_reviews(
    bind: sa.Connection, loser_id: uuid.UUID, survivor_id: uuid.UUID
) -> None:
    """Reassign the loser's match_reviews to the survivor, keeping earlier rows."""
    losers = _query_reviews(
        bind,
        f"SELECT {_REVIEW_COLS} FROM match_reviews "
        "WHERE connector_track_id = :lid ORDER BY id",
        {"lid": loser_id},
    )
    for review in losers:
        incumbents = _query_reviews(
            bind,
            f"SELECT {_REVIEW_COLS} FROM match_reviews "
            "WHERE connector_track_id = :sid AND user_id = :uid "
            "AND track_id = :tid AND connector_name = :cn",
            {
                "sid": survivor_id,
                "uid": review.user_id,
                "tid": review.track_id,
                "cn": review.connector_name,
            },
        )
        if not incumbents:
            bind.execute(
                sa.text(
                    "UPDATE match_reviews SET connector_track_id = :sid WHERE id = :id"
                ),
                {"sid": survivor_id, "id": review.id},
            )
            continue
        incumbent = incumbents[0]
        if review.created_at < incumbent.created_at:
            bind.execute(
                sa.text("DELETE FROM match_reviews WHERE id = :id"),
                {"id": incumbent.id},
            )
            bind.execute(
                sa.text(
                    "UPDATE match_reviews SET connector_track_id = :sid WHERE id = :id"
                ),
                {"sid": survivor_id, "id": review.id},
            )
        else:
            bind.execute(
                sa.text("DELETE FROM match_reviews WHERE id = :id"),
                {"id": review.id},
            )


def _repoint_playlist_tracks(
    bind: sa.Connection, loser_id: uuid.UUID, survivor_id: uuid.UUID
) -> None:
    """Repoint the loser's best-effort playlist_tracks pointer to the survivor.

    No unique key spans ``connector_track_id`` here, so the move never conflicts.
    """
    bind.execute(
        sa.text(
            "UPDATE playlist_tracks SET connector_track_id = :sid "
            "WHERE connector_track_id = :lid"
        ),
        {"sid": survivor_id, "lid": loser_id},
    )


def _reassert_primaries(
    bind: sa.Connection, affected: set[tuple[str, uuid.UUID]]
) -> None:
    """Ensure each touched ``(user, track, 'lastfm')`` has exactly one primary.

    A conflict where a manual_override loser deleted a primary incumbent can
    leave a group with zero primaries; promote the best remaining mapping
    (manual_override, then highest confidence, then lowest id). The >1-primary
    branch is defensive: the partial unique index makes it unreachable.
    """
    for user_id, track_id in sorted(affected, key=lambda pair: (pair[0], str(pair[1]))):
        rows = _query_mappings(
            bind,
            f"SELECT {_MAPPING_COLS} FROM track_mappings "
            "WHERE user_id = :uid AND track_id = :tid AND connector_name = :cn "
            "ORDER BY id",
            {"uid": user_id, "tid": track_id, "cn": _LASTFM},
        )
        if not rows:
            continue
        primaries = [m for m in rows if m.is_primary]
        if len(primaries) == 1:
            continue
        if not primaries:
            best = min(rows, key=_primary_rank)
            bind.execute(
                sa.text("UPDATE track_mappings SET is_primary = TRUE WHERE id = :id"),
                {"id": best.id},
            )
        else:
            keep = min(primaries, key=_primary_rank)
            for mapping in primaries:
                if mapping.id != keep.id:
                    bind.execute(
                        sa.text(
                            "UPDATE track_mappings SET is_primary = FALSE WHERE id = :id"
                        ),
                        {"id": mapping.id},
                    )


def _find_occupant(
    bind: sa.Connection, target: str, exclude_id: uuid.UUID
) -> _Candidate | None:
    """The lastfm connector_tracks row currently holding ``target``, if any.

    Called in phase 2 before a survivor is renamed to ``target``. By then every
    loser is deleted and every to-be-renamed survivor is parked at a temporary
    identifier, so the only row that can still hold ``target`` is an
    unfoldable/skipped one. Absorbing it into the survivor keeps the post-fold
    identifier set unique. At most one row can match (the unique constraint).
    """
    row = bind.execute(
        sa.text(
            "SELECT ct.id, ct.connector_track_identifier, ct.created_at, "
            "ct.raw_metadata, "
            "(SELECT count(*) FROM track_mappings tm "
            " WHERE tm.connector_track_id = ct.id) AS mapping_count "
            "FROM connector_tracks ct "
            "WHERE ct.connector_name = :lastfm "
            "AND ct.connector_track_identifier = :target AND ct.id != :exclude"
        ),
        {"lastfm": _LASTFM, "target": target, "exclude": exclude_id},
    ).first()
    if row is None:
        return None
    raw = cast("object", row[3])
    return _Candidate(
        id=cast("uuid.UUID", row[0]),
        identifier=cast("str", row[1]),
        created_at=cast("datetime", row[2]),
        raw_metadata=cast("dict[str, object]", raw) if isinstance(raw, dict) else {},
        mapping_count=cast("int", row[4]),
    )


def _fold_group(
    bind: sa.Connection,
    target: str,
    members: list[_Candidate],
    affected: set[tuple[str, uuid.UUID]],
) -> tuple[int, _Candidate | None, dict[str, object]]:
    """Fold one target group's losers into its survivor.

    Returns ``(losers_folded, survivor_to_rename_or_None, merged_metadata)``.
    The identifier rename is NOT applied here: when the survivor's identifier
    differs from ``target`` the survivor and its merged metadata are handed back
    so ``upgrade`` can apply every rename together, collision-free (phase 2).
    """
    survivor = _choose_survivor(members, target)
    losers = [m for m in members if m.id != survivor.id]
    needs_rename = survivor.identifier != target
    if not losers and not needs_rename:
        return 0, None, {}

    survivor_meta = dict(survivor.raw_metadata)
    folded_from = _str_list(survivor_meta.get("folded_from"))
    for loser in sorted(losers, key=lambda m: str(m.id)):
        _move_mappings(bind, loser.id, survivor.id, affected)
        _move_reviews(bind, loser.id, survivor.id)
        _repoint_playlist_tracks(bind, loser.id, survivor.id)
        # Shallow merge: survivor keys win; among losers the earlier-processed
        # (lower id) loser wins a key the survivor lacks.
        survivor_meta = {**loser.raw_metadata, **survivor_meta}
        folded_from.extend(_str_list(loser.raw_metadata.get("folded_from")))
        folded_from.append(loser.identifier)
        bind.execute(
            sa.text("DELETE FROM connector_tracks WHERE id = :id"),
            {"id": loser.id},
        )

    deduped = _dedupe(f for f in folded_from if f != target)
    if deduped:
        survivor_meta["folded_from"] = deduped

    if needs_rename:
        # Defer the rename to phase 2; hand back the merged metadata to write
        # alongside the final identifier assignment.
        return len(losers), survivor, survivor_meta

    if losers:
        bind.execute(
            sa.text(
                "UPDATE connector_tracks SET raw_metadata = CAST(:meta AS JSONB) "
                "WHERE id = :id"
            ),
            {"meta": json.dumps(survivor_meta), "id": survivor.id},
        )
    return len(losers), None, {}


def upgrade() -> None:
    """Fold Last.fm connector_tracks onto their normalized composite key."""
    bind = op.get_bind()

    # RLS bracket: make the RLS-protected tables visible to a non-bypass owner.
    for table in _RLS_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))

    groups, unfoldable = _load_candidates(bind)
    affected: set[tuple[str, uuid.UUID]] = set()
    folded_losers = 0
    # (survivor, target, merged_metadata) for each group whose survivor must be
    # renamed — deferred so all renames run collision-free in phase 2 below.
    renames: list[tuple[_Candidate, str, dict[str, object]]] = []
    for target in sorted(groups):
        losers, survivor, meta = _fold_group(bind, target, groups[target], affected)
        folded_losers += losers
        if survivor is not None:
            renames.append((survivor, target, meta))

    # Phase 2 — collision-safe renames. A survivor's CURRENT identifier may equal
    # another (not-yet-renamed) group's target, so a naive per-group rename can
    # trip the (connector_name, connector_track_identifier) unique constraint and
    # abort the whole migration. Break the cycle: park every to-be-renamed
    # survivor at a unique temp identifier first, then assign finals.
    for survivor, _target, _meta in renames:
        bind.execute(
            sa.text(
                "UPDATE connector_tracks SET connector_track_identifier = :tmp "
                "WHERE id = :id"
            ),
            {"tmp": f"__fold_tmp_{survivor.id}__", "id": survivor.id},
        )
    absorbed = 0
    for survivor, target, base_meta in renames:
        meta = base_meta
        # Any residual row still holding `target` (an unfoldable/skipped row) is a
        # true duplicate on the final identifier — absorb it into the survivor.
        occupant = _find_occupant(bind, target, survivor.id)
        if occupant is not None:
            _move_mappings(bind, occupant.id, survivor.id, affected)
            _move_reviews(bind, occupant.id, survivor.id)
            _repoint_playlist_tracks(bind, occupant.id, survivor.id)
            meta = {**occupant.raw_metadata, **base_meta}
            folded = _dedupe([
                *_str_list(meta.get("folded_from")),
                occupant.identifier,
            ])
            folded = [f for f in folded if f != target]
            if folded:
                meta["folded_from"] = folded
            bind.execute(
                sa.text("DELETE FROM connector_tracks WHERE id = :id"),
                {"id": occupant.id},
            )
            absorbed += 1
        bind.execute(
            sa.text(
                "UPDATE connector_tracks "
                "SET raw_metadata = CAST(:meta AS JSONB), "
                "connector_track_identifier = :target WHERE id = :id"
            ),
            {"meta": json.dumps(meta), "target": target, "id": survivor.id},
        )

    _reassert_primaries(bind, affected)

    for table in _RLS_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))

    _logger.info(
        "lastfm_identifier_fold_complete",
        groups=len(groups),
        folded_losers=folded_losers,
        renamed_survivors=len(renames),
        absorbed_occupants=absorbed,
        unfoldable=unfoldable,
    )


def downgrade() -> None:
    """No-op: the fold is lossy and not mechanically reversible (see 012).

    ``raw_metadata['folded_from']`` preserves the collapsed identifiers for
    forensic purposes, but the deleted loser rows and merged mappings/reviews
    cannot be reconstructed.
    """
