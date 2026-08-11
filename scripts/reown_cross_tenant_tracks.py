#!/usr/bin/env python3
"""Re-own canonical tracks that one tenant's plays depend on but another owns.

Finding C3: prod connects as ``neondb_owner``, which has ``rolbypassrls``, so
RLS is configured on every tenant table and enforced on none. An early
mistenanted poller run created canonicals under ``"default"``; a later import
running as the real user found and reused them, because the cross-tenant read
that should have been impossible succeeded. The result is a real user whose
``track_plays.track_id`` and ``connector_plays.resolved_track_id`` name tracks
another tenant owns.

The user's decision is **re-own, not duplicate**: the canonical is the right
canonical, it simply carries the wrong ``user_id``. Copying it would leave two
rows for one recording and split the play history across them.

What travels with a re-owned track
----------------------------------
``tracks.user_id`` is the tenancy of the *track*. Every other table carrying a
``user_id`` next to a ``track_id`` is one of two things, and the split decides
which rows move:

**Identity/provider facts about the track — these MOVE.** A re-owned track
whose these stayed behind is a new inconsistency, not a fix.

- ``track_mappings`` — the track's connector edges. Both uniques are
  user-scoped (``uq_track_mappings_live_connector`` on
  ``(user_id, connector_track_id, connector_name)``, ``uq_primary_mapping`` on
  ``(user_id, track_id, connector_name)``), so a mapping left behind is
  invisible to the track's new owner *and* still holds the live slot under the
  old tenant — the next import would create a second, competing edge. Retired
  rows move alongside the live ones: they are the append-only history migration
  051 exists to protect, and splitting a supersession chain across tenants is
  the same loss in a slower form.
- ``track_metrics`` — unique on ``(track_id, connector_name, metric_type)``
  with no ``user_id`` in the key. That is definitionally a per-track fact; the
  ``user_id`` column exists only so RLS has something to filter on. A row can
  only belong to whoever owns the track.
- ``match_reviews`` — a pending identity decision *about this track*, and the
  queue that surfaces it is user-scoped. Left behind it is a review the track's
  owner can never act on and the old tenant has no business acting on.
- ``resolution_negatives`` naming the track as ``candidate_track_id`` — the
  cannot-link/backoff clock for this track's identity. Its FK to ``tracks`` is
  ``ON DELETE CASCADE``, so leaving it behind also parks a cross-tenant row
  that a future delete would silently take.

**Behavioural facts of the tenant that recorded them — these STAY.**
``track_plays``, ``connector_plays``, ``play_sources``, ``track_likes``,
``track_preferences``/``_events``, ``track_tags``/``_events``,
``playlist_tracks``, ``playlist_assignment_members``. Moving these would
fabricate listening history, likes, or playlist membership for the target
user out of another tenant's rows. They are counted and printed, because after
the move each is a cross-tenant reference of the opposite polarity — but
resolving them is a separate decision, not this script's.

``resolution_events`` also stays, on the rule the cleanup script already
applies: it is append-only history referenced by value with no FK, and
rewriting it would edit what mixd believed at the time.

Refusals
--------
A track is skipped, never forced, when re-owning it would be a *merge*:

- the target already owns a track with the same ISRC, Spotify id, or MBID —
  ``uq_tracks_user_isrc`` / ``_spotify_id`` / ``_mbid`` would fire;
- the target already owns a normalization-equal track (same
  ``title_normalized`` + ``artist_normalized``) — no constraint fires, but two
  rows for one recording is exactly the duplicate class
  ``find_duplicate_canonicals.py`` reports;
- a travelling ``track_mappings`` / ``match_reviews`` /
  ``resolution_negatives`` row would collide with one the target already owns.

Each skip prints the colliding row, and ``--merge`` finishes them — see below.

Merge mode (``--merge``)
------------------------
A refusal above is a *merge*: two rows for one recording, one of which this
user's history hangs off but does not own. ``--merge`` plans exactly that
population and, with ``--apply`` as well, performs it. Neither flag alone
writes: the merge deletes canonical rows, so it is a second opt-in rather than
a widening of the first.

**The winner is always the track the target user owns.** Ownership decides, not
the ISRC: merging into the foreign row would leave the user's history on a row
they still do not own, which is the defect being repaired. Enforced in three
places rather than by convention — the discovery query joins the winner only out
of ``tracks o ... AND o.user_id = :user``; ``MergePair.__post_init__`` refuses
to construct a pair whose winner is owned by anyone else; and the mutation
re-reads both rows ``FOR UPDATE`` and re-checks before it writes.

**Refuse, never force.** A pair is skipped when ``describes_same_recording``
(from ``src.domain.matching.recording_identity`` — the one definition in this
codebase, shared with the creation paths and ``find_duplicate_canonicals.py``)
says the two rows are different recordings, or when the merge would destroy or
mistenant something: see ``_UNSAFE_REASONS``, ``_LEFT_REASONS`` and
``_CLASH_REFUSALS``. There is no override flag.

What ``TrackMergeService`` moves — and what it does not
------------------------------------------------------
Verified against the repository, not assumed. ``merge_tracks`` calls
``move_references_to_track`` (``playlist_tracks``, ``track_plays``,
``connector_plays``, ``track_likes``, ``track_preferences``,
``track_preference_events``), ``merge_mappings_to_track`` (``track_mappings``,
live and retired), ``merge_metrics_to_track`` (``track_metrics``), then
``hard_delete_track``.

``connector_plays`` is in that list as of v0.10.3: its ``resolved_track_id`` FK
is ON DELETE CASCADE, so a merge that left the ledger behind deleted every
observation resolved to the loser — and ``play_sources`` followed through its
own CASCADE on ``connector_play_id``. This script used to re-point them itself
before calling the service; the service does it now, so that workaround is
gone rather than duplicated.

It also moves none of ``match_reviews``, ``resolution_negatives``,
``track_tags``, ``track_tag_events`` or ``playlist_assignment_members`` — all
CASCADE. Rather than invent semantics for them, a pair with any such row is
refused. And ``move_references_to_track`` is *not* user-scoped: another tenant's
plays, likes or preferences on the loser would be moved onto this user's track
and left owned by that tenant, so a pair with any of those is refused too.

``resolution_events`` naming the loser survive the delete (referenced by value,
no FK) and are left as written, on the rule the cleanup script already applies.
They are counted and printed.

Why this bypasses ``MergeTracksUseCase`` — deliberately
------------------------------------------------------
``MergeTracksUseCase`` resolves *both* ids with ``get_track_by_id(...,
user_id=command.user_id)``, so a loser owned by another tenant raises
``NotFoundError`` and ``mixd tracks merge`` cannot run on this population from
either direction. That guard is correct and stays: a user-facing merge must not
be able to reach across tenants. ``TrackMergeService.merge_tracks`` is not
tenant-scoped (it resolves via ``get_by_id`` with no user filter), so this
script calls the service directly. That is the right layering for a
cross-tenant repair — the guard belongs to the user-facing surface, and an
operator-run repair script is not that surface. Re-owning the loser first is
not an alternative: ``uq_tracks_user_isrc`` fires, which is why the track is in
the refusal list to begin with.

The mapping step is the subtle one. ``merge_mappings_to_track``'s conflict CTE
carries ``loser.user_id = winner.user_id`` (deliberately — without it a merge
would stamp a cross-tenant ``superseded_by_id``), so a foreign loser's mapping
reaches the *non-conflicting* arm and would survive live on the winner's track
under the old tenant: the mistenanted mapping this repair exists to remove,
re-created by the repair. ``merge_pair`` therefore retires those mappings into
the user's own live row and re-owns them first, recording the conflations
through the same ``record_supersessions`` seam the merge service uses.

Retired mappings and the live-rows filter
----------------------------------------
Discovery is raw SQL and the move is a Core ``update()``. Neither is touched by
``live_rows.py``'s ``do_orm_execute`` listener, which scopes *ORM* selects of
``DBTrackMapping`` to live rows — so retired rows are both counted and moved.
That is deliberate: an ORM select here would silently leave every superseded
mapping behind under the old tenant, splitting a supersession chain across two
tenants. If a reader is ever added here, it needs
``execution_options(include_superseded=True)``.

RLS
---
This operation is inherently cross-tenant: the UPDATE reads a row under one
tenant and writes it under another. A single-tenant ``FOR ALL`` policy applies
its ``USING`` expression as ``WITH CHECK`` too, so under an RLS-*enforced* role
no statement here can succeed — by design. It works today only because
``neondb_owner`` has BYPASSRLS (PDR-002), and every statement therefore carries
an explicit ``user_id`` predicate as the actual isolation.

Dry-run by default — prints the full plan without writing. Pass ``--apply``.

Usage:
    uv run python scripts/reown_cross_tenant_tracks.py --user <tenant>
    uv run python scripts/reown_cross_tenant_tracks.py --user <tenant> --apply
    uv run python scripts/reown_cross_tenant_tracks.py --user <tenant> --merge
    uv run python scripts/reown_cross_tenant_tracks.py --user <tenant> --merge --apply
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import batched
from uuid import UUID

from sqlalchemy import String, bindparam, text, update
from sqlalchemy.ext.asyncio import AsyncSession
import typer

from src.config import get_logger, setup_script_logger
from src.config.constants import BusinessLimits
from src.domain.entities.track import Artist, Track
from src.domain.entities.track_mapping import SupersessionReason
from src.domain.matching.isrc_validation import SUSPECT_DURATION_DIFF_MS
from src.domain.matching.recording_identity import (
    describe_track,
    describes_same_recording,
)
from src.domain.repositories.resolution import SupersessionEdge
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import (
    DBMatchReview,
    DBResolutionNegative,
    DBTrack,
    DBTrackMapping,
    DBTrackMetric,
)
from src.infrastructure.persistence.database.user_context import (
    statement_timeout_context,
    user_context,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work

logger = get_logger(__name__)

# The retirement reason a merge stamps, spelled once. ``track/core.py`` names
# the same literal for the same purpose; both are the domain's vocabulary.
_CONFLATION: SupersessionReason = "conflation"

# Every IN-list goes through ``itertools.batched(ids, BATCH_SIZE, strict=False)``:
# one statement per ~1,000 ids, never one per id and never one unbounded list.
BATCH_SIZE = 1_000

# The population both modes work on: tracks the target user's plays point at
# but does not own. Substituted into each query below rather than written twice
# — re-own and merge must not be able to disagree about which tracks are in
# scope, since merge mode exists precisely to finish what re-own refuses.
# ``.replace`` rather than an f-string: runtime interpolation into SQL is a lint
# finding, and the alternative to a placeholder is a suppression.
_POPULATION_CTE = """
WITH depended AS (
    SELECT tp.track_id AS id FROM track_plays tp WHERE tp.user_id = :user
    UNION
    SELECT cp.resolved_track_id AS id FROM connector_plays cp
     WHERE cp.user_id = :user AND cp.resolved_track_id IS NOT NULL
),
foreign_tracks AS (
    SELECT t.*
      FROM tracks t
      JOIN depended d ON d.id = t.id
     WHERE t.user_id <> :user
       AND (:from_tenant IS NULL OR t.user_id = :from_tenant)
)
"""

# The tracks the target user's plays point at but does not own, with everything
# the plan needs: dependency weight, the companion rows that would travel, and
# one flag per way the move could be a merge in disguise.
_DISCOVERY_TEMPLATE = """
__POPULATION__
SELECT
    f.id,
    f.user_id AS owner,
    f.title,
    f.artists_text,
    f.isrc,
    f.spotify_id,
    f.mbid,
    (SELECT count(*) FROM track_plays tp
      WHERE tp.user_id = :user AND tp.track_id = f.id) AS dep_track_plays,
    (SELECT count(*) FROM connector_plays cp
      WHERE cp.user_id = :user AND cp.resolved_track_id = f.id) AS dep_connector_plays,
    -- Companions that travel (counted under the current owner only).
    (SELECT count(*) FROM track_mappings tm
      WHERE tm.user_id = f.user_id AND tm.track_id = f.id) AS move_mappings,
    (SELECT count(*) FROM track_mappings tm
      WHERE tm.user_id = f.user_id AND tm.track_id = f.id
        AND tm.superseded_at IS NOT NULL) AS move_mappings_retired,
    (SELECT count(*) FROM track_metrics m
      WHERE m.user_id = f.user_id AND m.track_id = f.id) AS move_metrics,
    (SELECT count(*) FROM match_reviews mr
      WHERE mr.user_id = f.user_id AND mr.track_id = f.id) AS move_match_reviews,
    (SELECT count(*) FROM resolution_negatives rn
      WHERE rn.user_id = f.user_id AND rn.candidate_track_id = f.id) AS move_negatives,
    -- Companions that stay behind under the old owner (reported, never moved).
    (SELECT count(*) FROM track_plays tp
      WHERE tp.user_id = f.user_id AND tp.track_id = f.id) AS stay_track_plays,
    (SELECT count(*) FROM connector_plays cp
      WHERE cp.user_id = f.user_id AND cp.resolved_track_id = f.id) AS stay_connector_plays,
    (SELECT count(*) FROM track_likes tl
      WHERE tl.user_id = f.user_id AND tl.track_id = f.id) AS stay_likes,
    (SELECT count(*) FROM track_preferences tp2
      WHERE tp2.user_id = f.user_id AND tp2.track_id = f.id) AS stay_preferences,
    (SELECT count(*) FROM track_tags tt
      WHERE tt.user_id = f.user_id AND tt.track_id = f.id) AS stay_tags,
    (SELECT count(*) FROM playlist_tracks pt
      WHERE pt.track_id = f.id) AS stay_playlist_tracks,
    (SELECT count(*) FROM playlist_assignment_members pam
      WHERE pam.user_id = f.user_id AND pam.track_id = f.id) AS stay_assignment_members,
    (SELECT count(*) FROM resolution_events re
      WHERE re.user_id = f.user_id AND re.track_id = f.id) AS stay_resolution_events,
    -- Collision flags: each is a way the re-own would really be a merge.
    (SELECT o.id FROM tracks o
      WHERE o.user_id = :user AND f.isrc IS NOT NULL AND o.isrc = f.isrc
      LIMIT 1) AS clash_isrc,
    (SELECT o.id FROM tracks o
      WHERE o.user_id = :user AND f.spotify_id IS NOT NULL
        AND o.spotify_id = f.spotify_id LIMIT 1) AS clash_spotify_id,
    (SELECT o.id FROM tracks o
      WHERE o.user_id = :user AND f.mbid IS NOT NULL AND o.mbid = f.mbid
      LIMIT 1) AS clash_mbid,
    (SELECT o.id FROM tracks o
      WHERE o.user_id = :user
        AND f.title_normalized IS NOT NULL AND f.artist_normalized IS NOT NULL
        AND o.title_normalized = f.title_normalized
        AND o.artist_normalized = f.artist_normalized LIMIT 1) AS clash_normalized,
    (SELECT mine.id FROM track_mappings mine
       JOIN track_mappings theirs
         ON theirs.connector_track_id = mine.connector_track_id
        AND theirs.connector_name = mine.connector_name
      WHERE mine.user_id = :user AND mine.superseded_at IS NULL
        AND theirs.user_id = f.user_id AND theirs.track_id = f.id
        AND theirs.superseded_at IS NULL
      LIMIT 1) AS clash_live_mapping,
    (SELECT mine.id FROM track_mappings mine
       JOIN track_mappings theirs
         ON theirs.connector_name = mine.connector_name
      WHERE mine.user_id = :user AND mine.track_id = f.id
        AND mine.is_primary AND mine.superseded_at IS NULL
        AND theirs.user_id = f.user_id AND theirs.track_id = f.id
        AND theirs.is_primary AND theirs.superseded_at IS NULL
      LIMIT 1) AS clash_primary_mapping,
    (SELECT mine.id FROM match_reviews mine
       JOIN match_reviews theirs
         ON theirs.connector_name = mine.connector_name
        AND theirs.connector_track_id = mine.connector_track_id
      WHERE mine.user_id = :user AND mine.track_id = f.id
        AND theirs.user_id = f.user_id AND theirs.track_id = f.id
      LIMIT 1) AS clash_match_review,
    (SELECT mine.id FROM resolution_negatives mine
       JOIN resolution_negatives theirs
         ON theirs.connector_track_id = mine.connector_track_id
      WHERE mine.user_id = :user AND mine.kind = 'rejected_pair'
        AND mine.candidate_track_id = f.id
        AND theirs.user_id = f.user_id AND theirs.kind = 'rejected_pair'
        AND theirs.candidate_track_id = f.id
      LIMIT 1) AS clash_negative
FROM foreign_tracks f
ORDER BY f.user_id, f.artists_text, f.title
"""

_DISCOVERY_SQL = _DISCOVERY_TEMPLATE.replace("__POPULATION__", _POPULATION_CTE)

# Supersession chains that would straddle the move: a chain link is only
# meaningful inside one tenant, so an edge with one end in the moved set and the
# other outside it is a warning the operator must see before applying.
_CHAIN_SQL = """
SELECT count(*) FROM track_mappings a
 JOIN track_mappings b ON b.id = a.superseded_by_id
WHERE (a.id = ANY(:mapping_ids)) <> (b.id = ANY(:mapping_ids))
"""

_MOVED_MAPPING_IDS_SQL = """
SELECT tm.id FROM track_mappings tm
 WHERE tm.user_id = :owner AND tm.track_id = ANY(:track_ids)
"""

# Merge mode's population: every refusal above, paired with the track the target
# user already owns that made it a refusal. The winner is joined *only* out of
# ``tracks o`` under ``o.user_id = :user``, which is the first of the two places
# the winner-is-owned invariant is enforced — the query cannot emit a pair whose
# winner belongs to anyone else. Columns come in three groups: identity for the
# printed plan, weight (plays on each side) for the operator, and one count per
# way the merge would destroy or mistenant something (``unsafe_*`` / ``left_*``).
_MERGE_PAIRS_TEMPLATE = """
__POPULATION__
, clashes AS (
    SELECT f.*,
      (SELECT o.id FROM tracks o
        WHERE o.user_id = :user AND f.isrc IS NOT NULL AND o.isrc = f.isrc
        LIMIT 1) AS clash_isrc,
      (SELECT o.id FROM tracks o
        WHERE o.user_id = :user AND f.spotify_id IS NOT NULL
          AND o.spotify_id = f.spotify_id LIMIT 1) AS clash_spotify_id,
      (SELECT o.id FROM tracks o
        WHERE o.user_id = :user AND f.mbid IS NOT NULL AND o.mbid = f.mbid
        LIMIT 1) AS clash_mbid,
      (SELECT o.id FROM tracks o
        WHERE o.user_id = :user
          AND f.title_normalized IS NOT NULL AND f.artist_normalized IS NOT NULL
          AND o.title_normalized = f.title_normalized
          AND o.artist_normalized = f.artist_normalized LIMIT 1) AS clash_normalized
    FROM foreign_tracks f
)
SELECT
    c.user_id AS loser_owner,
    c.id AS loser_id,
    c.title AS loser_title,
    c.artists_text AS loser_artists_text,
    c.artists->'names'->>0 AS loser_primary_artist,
    c.isrc AS loser_isrc,
    c.spotify_id AS loser_spotify_id,
    c.duration_ms AS loser_duration_ms,
    (SELECT count(*) FROM track_plays tp WHERE tp.track_id = c.id)
      AS loser_track_plays,
    (SELECT count(*) FROM connector_plays cp WHERE cp.resolved_track_id = c.id)
      AS loser_connector_plays,
    o.user_id AS winner_owner,
    o.id AS winner_id,
    o.title AS winner_title,
    o.artists_text AS winner_artists_text,
    o.artists->'names'->>0 AS winner_primary_artist,
    o.isrc AS winner_isrc,
    o.spotify_id AS winner_spotify_id,
    o.duration_ms AS winner_duration_ms,
    (SELECT count(*) FROM track_plays tp WHERE tp.track_id = o.id)
      AS winner_track_plays,
    (SELECT count(*) FROM connector_plays cp WHERE cp.resolved_track_id = o.id)
      AS winner_connector_plays,
    c.clash_isrc,
    c.clash_spotify_id,
    c.clash_mbid,
    c.clash_normalized,
    -- Weight the operator needs to see moving.
    (SELECT count(*) FROM play_sources ps
       JOIN connector_plays cp ON cp.id = ps.connector_play_id
      WHERE cp.resolved_track_id = c.id) AS carry_play_sources,
    (SELECT count(*) FROM track_mappings tm WHERE tm.track_id = c.id)
      AS carry_mappings,
    (SELECT count(*) FROM track_metrics m WHERE m.track_id = c.id)
      AS carry_metrics,
    (SELECT count(*) FROM resolution_events re WHERE re.track_id = c.id)
      AS carry_stranded_events,
    -- Rows the merge would CASCADE away with the loser: it moves none of these.
    (SELECT count(*) FROM match_reviews mr WHERE mr.track_id = c.id)
      AS unsafe_match_reviews,
    (SELECT count(*) FROM resolution_negatives rn
      WHERE rn.candidate_track_id = c.id) AS unsafe_resolution_negatives,
    (SELECT count(*) FROM track_tags tt WHERE tt.track_id = c.id)
      AS unsafe_track_tags,
    (SELECT count(*) FROM track_tag_events tte WHERE tte.track_id = c.id)
      AS unsafe_track_tag_events,
    (SELECT count(*) FROM playlist_assignment_members pam
      WHERE pam.track_id = c.id) AS unsafe_playlist_assignment_members,
    -- Behavioural rows on the loser that belong to somebody else: the merge
    -- moves them (unscoped by tenant) onto a track this user owns.
    (SELECT count(*) FROM track_plays tp
      WHERE tp.track_id = c.id AND tp.user_id <> :user) AS left_track_plays,
    (SELECT count(*) FROM connector_plays cp
      WHERE cp.resolved_track_id = c.id AND cp.user_id <> :user)
      AS left_connector_plays,
    (SELECT count(*) FROM track_likes tl
      WHERE tl.track_id = c.id AND tl.user_id <> :user) AS left_track_likes,
    (SELECT count(*) FROM track_preferences tp2
      WHERE tp2.track_id = c.id AND tp2.user_id <> :user)
      AS left_track_preferences,
    (SELECT count(*) FROM playlist_tracks pt
       JOIN playlists pl ON pl.id = pt.playlist_id
      WHERE pt.track_id = c.id AND pl.user_id <> :user) AS left_playlist_tracks,
    -- A play the winner already holds under the same dedup key: moving the
    -- loser's would violate uq_track_plays_deduplication mid-merge.
    (SELECT count(*) FROM track_plays a
       JOIN track_plays b
         ON b.track_id = o.id
        AND b.user_id = a.user_id
        AND b.service = a.service
        AND b.played_at = a.played_at
        AND b.ms_played IS NOT DISTINCT FROM a.ms_played
      WHERE a.track_id = c.id) AS clash_play_dedup,
    -- A live mapping of the loser's whose connector track the user already
    -- claims from some *other* canonical: re-owning it would violate
    -- uq_track_mappings_live_connector, and no merge of this pair fixes that.
    (SELECT count(*) FROM track_mappings lm
      WHERE lm.track_id = c.id AND lm.superseded_at IS NULL
        AND EXISTS (
            SELECT 1 FROM track_mappings mine
             WHERE mine.user_id = :user AND mine.superseded_at IS NULL
               AND mine.connector_track_id = lm.connector_track_id
               AND mine.connector_name = lm.connector_name
               AND mine.track_id <> o.id
        )) AS clash_mapping_claimed_elsewhere
FROM clashes c
JOIN tracks o
  ON o.id = coalesce(c.clash_isrc, c.clash_spotify_id, c.clash_mbid,
                     c.clash_normalized)
 AND o.user_id = :user
ORDER BY c.artists_text, c.title
"""

_MERGE_PAIRS_SQL = _MERGE_PAIRS_TEMPLATE.replace("__POPULATION__", _POPULATION_CTE)

# Both tracks, locked, as the database has them right now. The plan is a
# snapshot; this is the read the mutation actually trusts.
_LOCK_PAIR_SQL = """
SELECT id, user_id FROM tracks WHERE id = ANY(:ids) FOR UPDATE
"""

# The loser's live mappings that the target user already holds a live mapping
# for, on the same connector track: retired into that row and re-owned in one
# statement.
#
# This is ``merge_mappings_to_track``'s Branch 1, for a pair its CTE cannot see
# — that join carries ``loser.user_id = winner.user_id`` (deliberately: without
# it a merge would stamp a cross-tenant ``superseded_by_id``), so a foreign
# loser reaches the non-conflicting arm instead and would survive the merge live
# on the winner's track under the old tenant. That is the mistenanted mapping
# this whole repair exists to remove, re-created by the repair itself.
#
# Retiring and re-owning in one UPDATE is what makes it legal: both
# ``uq_track_mappings_live_connector`` and ``uq_primary_mapping`` are partial
# unique indexes over ``superseded_at IS NULL``, so the row leaves both the
# moment it is retired — a bare ``SET user_id`` would collide with the winner's
# live row. The edges come back for the caller to record as events, exactly as
# the merge service does with Branch 1's.
_CONFLATE_MAPPINGS_SQL = """
UPDATE track_mappings loser
   SET user_id = :user,
       superseded_at = :now,
       supersession_reason = :conflation,
       superseded_by_id = w.id,
       is_primary = FALSE,
       updated_at = :now
  FROM (
      SELECT DISTINCT ON (connector_track_id, connector_name)
             id, connector_track_id, connector_name
        FROM track_mappings
       WHERE user_id = :user AND track_id = :winner AND superseded_at IS NULL
       ORDER BY connector_track_id, connector_name, is_primary DESC, id
  ) w
 WHERE loser.track_id = :loser
   AND loser.user_id <> :user
   AND loser.superseded_at IS NULL
   AND loser.connector_track_id = w.connector_track_id
   AND loser.connector_name = w.connector_name
RETURNING loser.id AS predecessor_id, w.id AS successor_id,
          loser.connector_name AS connector_name
"""

# What the merge would silently destroy, re-counted inside the mutating
# transaction. The plan's counts are a snapshot; this is the guard.
#
# ``connector_plays`` is deliberately absent: the merge moves the ledger now,
# so rows are expected to still be here when this runs and counting them would
# refuse every merge. The tables below are the ones ``TrackMergeService``
# genuinely moves nothing of.
_CASCADE_GUARD_SQL = """
SELECT
    (SELECT count(*) FROM match_reviews WHERE track_id = :loser)
      AS match_reviews,
    (SELECT count(*) FROM resolution_negatives WHERE candidate_track_id = :loser)
      AS resolution_negatives,
    (SELECT count(*) FROM track_tags WHERE track_id = :loser) AS track_tags,
    (SELECT count(*) FROM track_tag_events WHERE track_id = :loser)
      AS track_tag_events,
    (SELECT count(*) FROM playlist_assignment_members WHERE track_id = :loser)
      AS playlist_assignment_members
"""

# Flag column → the refusal it produces. Order is the order they print in.
_CLASH_REASONS: tuple[tuple[str, str], ...] = (
    ("clash_isrc", "target already owns a track with this ISRC (uq_tracks_user_isrc)"),
    (
        "clash_spotify_id",
        "target already owns a track with this Spotify id (uq_tracks_user_spotify_id)",
    ),
    ("clash_mbid", "target already owns a track with this MBID (uq_tracks_user_mbid)"),
    (
        "clash_normalized",
        "target already owns a normalization-equal track (same title+artist normalized)",
    ),
    (
        "clash_live_mapping",
        (
            "target already owns a live mapping on the same connector track "
            "(uq_track_mappings_live_connector)"
        ),
    ),
    (
        "clash_primary_mapping",
        "target already owns a live primary mapping on this track (uq_primary_mapping)",
    ),
    (
        "clash_match_review",
        (
            "target already owns a match_review on the same pair "
            "(uq_match_reviews_user_track_connector)"
        ),
    ),
    (
        "clash_negative",
        (
            "target already owns a rejected_pair negative on the same pair "
            "(uq_resolution_negatives_pair)"
        ),
    ),
)


@dataclass(frozen=True)
class CrossTenantTrack:
    """One track another tenant owns that the target user's plays depend on."""

    track_id: UUID
    owner: str
    title: str
    artists: str
    isrc: str | None
    spotify_id: str | None
    dep_track_plays: int
    dep_connector_plays: int
    moving: dict[str, int]
    staying: dict[str, int]
    blockers: tuple[tuple[str, UUID], ...]

    @property
    def is_blocked(self) -> bool:
        return bool(self.blockers)

    @property
    def dependent_plays(self) -> int:
        return self.dep_track_plays + self.dep_connector_plays

    def describe(self) -> str:
        ident = self.isrc or self.spotify_id or "no external id"
        return (
            f"{self.track_id}  owner={self.owner}  {self.artists} — {self.title}  "
            f"[{ident}, {self.dep_track_plays} track_plays + "
            f"{self.dep_connector_plays} connector_plays depend on it]"
        )


def _as_uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _as_int(value: object) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _as_str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def blockers_for(row: dict[str, object]) -> tuple[tuple[str, UUID], ...]:
    """Every reason this track is a merge rather than a re-own.

    Pure over the discovery row so the refusal contract is unit-testable:
    a non-NULL clash column always yields a blocker, and an empty result
    always means the move is safe under every constraint checked.
    """
    return tuple(
        (reason, _as_uuid(row[column]))
        for column, reason in _CLASH_REASONS
        if row.get(column) is not None
    )


def _counts_by_prefix(row: dict[str, object], prefix: str) -> dict[str, int]:
    """The row's ``<prefix>*`` count columns, keyed by the table they counted."""
    return {
        key.removeprefix(prefix): _as_int(value)
        for key, value in row.items()
        if key.startswith(prefix)
    }


def _to_track(row: dict[str, object]) -> CrossTenantTrack:
    return CrossTenantTrack(
        track_id=_as_uuid(row["id"]),
        owner=str(row["owner"]),
        title=str(row["title"]),
        artists=_as_str_or_none(row["artists_text"]) or "unknown artist",
        isrc=_as_str_or_none(row["isrc"]),
        spotify_id=_as_str_or_none(row["spotify_id"]),
        dep_track_plays=_as_int(row["dep_track_plays"]),
        dep_connector_plays=_as_int(row["dep_connector_plays"]),
        moving=_counts_by_prefix(row, "move_"),
        staying={k: v for k, v in _counts_by_prefix(row, "stay_").items() if v},
        blockers=blockers_for(row),
    )


async def discover(
    session: AsyncSession, *, user: str, from_tenant: str | None
) -> list[CrossTenantTrack]:
    """Every cross-tenant track the target user's plays depend on.

    ``from_tenant`` is bound with an explicit type because it is optional: an
    untyped NULL bind leaves PostgreSQL unable to resolve ``IS NULL``'s operand
    type and the statement fails to parse.
    """
    result = await session.execute(
        text(_DISCOVERY_SQL).bindparams(bindparam("from_tenant", type_=String)),
        {"user": user, "from_tenant": from_tenant},
    )
    return [_to_track(dict(row)) for row in result.mappings().all()]


async def straddling_chain_edges(
    session: AsyncSession, *, owner: str, track_ids: list[UUID]
) -> int:
    """Supersession edges with exactly one end inside the moved mapping set."""
    if not track_ids:
        return 0
    mapping_ids = list(
        (
            await session.execute(
                text(_MOVED_MAPPING_IDS_SQL), {"owner": owner, "track_ids": track_ids}
            )
        ).scalars()
    )
    if not mapping_ids:
        return 0
    return (
        await session.execute(text(_CHAIN_SQL), {"mapping_ids": mapping_ids})
    ).scalar_one()


def render_plan(tracks: list[CrossTenantTrack], user: str) -> None:
    """Print the full plan: what moves, what stays, what is refused."""
    movable = [t for t in tracks if not t.is_blocked]
    blocked = [t for t in tracks if t.is_blocked]

    print(f"\n=== cross-tenant tracks for user {user} ===")
    print(f"{len(tracks)} tracks owned by another tenant back this user's plays")
    owners: dict[str, int] = {}
    for track in tracks:
        owners[track.owner] = owners.get(track.owner, 0) + 1
    for owner, count in sorted(owners.items()):
        print(f"  tenant {owner!r}: {count} tracks")

    print(f"\n--- RE-OWNABLE: {len(movable)} tracks ---")
    for track in movable:
        print(f"\n  {track.describe()}")
        moving = ", ".join(f"{k}={v}" for k, v in track.moving.items() if v) or "none"
        print(f"    moves with it: {moving}")
        if track.staying:
            staying = ", ".join(f"{k}={v}" for k, v in track.staying.items())
            print(f"    STAYS under {track.owner!r} (reported, not moved): {staying}")

    print(f"\n--- REFUSED: {len(blocked)} tracks are merges, not re-owns ---")
    for track in blocked:
        print(f"\n  {track.describe()}")
        for reason, other_id in track.blockers:
            print(f"    blocked: {reason}")
            print(f"             colliding row: {other_id}")
        winner = next(
            (
                other
                for reason, other in track.blockers
                if reason.startswith("target already owns a track")
            ),
            None,
        )
        if winner is not None:
            print(
                f"      mixd tracks merge --winner-id {winner} "
                f"--loser-id {track.track_id}"
            )

    print("\n=== summary ===")
    print(f"  {len(movable)} tracks re-ownable")
    print(f"  {len(blocked)} tracks refused (merge required)")
    print(
        f"  {sum(t.dependent_plays for t in movable)} of this user's plays are "
        f"unblocked by the re-ownable set"
    )
    totals: dict[str, int] = {}
    for track in movable:
        for key, value in track.moving.items():
            totals[key] = totals.get(key, 0) + value
    print(f"  companions travelling: {totals or 'none'}")


async def reown(
    session: AsyncSession, *, user: str, owner: str, track_ids: list[UUID]
) -> dict[str, int]:
    """Move the tracks and their identity/provider companions to ``user``.

    One session, therefore one transaction: a unique-constraint failure on any
    companion rolls the whole batch back rather than leaving half a track
    behind. Every statement repeats the owning-tenant predicate so a stale id
    list can never reach a row a re-run already moved.
    """
    counts: dict[str, int] = {}
    for batch in batched(track_ids, BATCH_SIZE, strict=False):
        ids = list(batch)
        result = await session.execute(
            update(DBTrack)
            .where(DBTrack.user_id == owner, DBTrack.id.in_(ids))
            .values(user_id=user)
        )
        counts["tracks"] = counts.get("tracks", 0) + result.rowcount
        for name, model, column in (
            ("track_mappings", DBTrackMapping, DBTrackMapping.track_id),
            ("track_metrics", DBTrackMetric, DBTrackMetric.track_id),
            ("match_reviews", DBMatchReview, DBMatchReview.track_id),
            (
                "resolution_negatives",
                DBResolutionNegative,
                DBResolutionNegative.candidate_track_id,
            ),
        ):
            moved = await session.execute(
                update(model)
                .where(model.user_id == owner, column.in_(ids))
                .values(user_id=user)
            )
            counts[name] = counts.get(name, 0) + moved.rowcount
    return counts


# ─────────────────────────── merge mode ────────────────────────────
# The refusals above, finished. See the module docstring for why this bypasses
# ``MergeTracksUseCase``.

# Count column → the refusal it produces, for rows the merge would CASCADE away
# with the loser. ``TrackMergeService`` moves none of these tables.
_UNSAFE_REASONS: tuple[tuple[str, str], ...] = (
    (
        "unsafe_match_reviews",
        (
            "match_reviews on the loser — the merge moves none, and the "
            "loser's delete CASCADEs them away"
        ),
    ),
    (
        "unsafe_resolution_negatives",
        (
            "resolution_negatives naming the loser as candidate — "
            "CASCADE-deleted with it, taking the cannot-link decision"
        ),
    ),
    ("unsafe_track_tags", "track_tags on the loser — CASCADE-deleted with it"),
    (
        "unsafe_track_tag_events",
        "track_tag_events on the loser — CASCADE-deleted with it",
    ),
    (
        "unsafe_playlist_assignment_members",
        "playlist_assignment_members on the loser — CASCADE-deleted with it",
    ),
)

# Count column → the refusal it produces, for another tenant's behavioural rows
# on the loser. ``move_references_to_track`` is not user-scoped, so it would
# move them onto a track this user owns and leave them owned by the old tenant.
_LEFT_REASONS: tuple[tuple[str, str], ...] = (
    (
        "left_track_plays",
        (
            "track_plays on the loser owned by another tenant — the merge "
            "would move them onto this user's track and leave them mistenanted"
        ),
    ),
    (
        "left_connector_plays",
        (
            "connector_plays on the loser owned by another tenant — the merge "
            "moves the ledger unscoped, so they would land on this user's "
            "track and stay mistenanted"
        ),
    ),
    (
        "left_track_likes",
        (
            "track_likes on the loser owned by another tenant — the merge "
            "would move them onto this user's track"
        ),
    ),
    (
        "left_track_preferences",
        (
            "track_preferences on the loser owned by another tenant — the "
            "merge would move them onto this user's track"
        ),
    ),
    (
        "left_playlist_tracks",
        (
            "playlist_tracks on the loser in another tenant's playlist — the "
            "merge would move them onto this user's track"
        ),
    ),
)

# Count column → the refusal it produces, for constraints the merge would hit.
_CLASH_REFUSALS: tuple[tuple[str, str], ...] = (
    (
        "clash_play_dedup",
        (
            "the winner already holds a play under the same dedup key "
            "(uq_track_plays_deduplication) — moving the loser's would abort "
            "the merge"
        ),
    ),
    (
        "clash_mapping_claimed_elsewhere",
        (
            "a live mapping of the loser's names a connector track this user "
            "already claims from a different canonical "
            "(uq_track_mappings_live_connector) — an identity conflict one "
            "merge cannot settle"
        ),
    ),
)

# Clash column → the unique constraint it names, for the printed plan.
_MERGE_COLLISION_LABELS: tuple[tuple[str, str], ...] = (
    ("clash_isrc", "uq_tracks_user_isrc"),
    ("clash_spotify_id", "uq_tracks_user_spotify_id"),
    ("clash_mbid", "uq_tracks_user_mbid"),
    ("clash_normalized", "normalization-equal (no constraint — same title+artist)"),
)


@dataclass(frozen=True)
class Side:
    """One end of a merge pair, as the database describes it."""

    track_id: UUID
    owner: str
    title: str
    artists: str
    primary_artist: str
    isrc: str | None
    spotify_id: str | None
    duration_ms: int | None
    track_plays: int
    connector_plays: int

    def as_track(self) -> Track:
        """The row as the domain entity ``describe_track`` asks about.

        Only the fields the same-recording question turns on are real. Building
        it is what lets ``describes_same_recording`` stay the single definition
        rather than being re-implemented over raw columns here.
        """
        return Track(
            title=self.title,
            artists=[Artist(name=self.primary_artist)],
            duration_ms=self.duration_ms,
            isrc=self.isrc,
            id=self.track_id,
        )

    def describe(self) -> str:
        duration = (
            f"{self.duration_ms / 1000:.1f}s" if self.duration_ms else "duration ?"
        )
        return (
            f"{self.track_id}  owner={self.owner}  {self.artists} — {self.title}  "
            f"[{duration}, isrc={self.isrc or '—'}, "
            f"spotify={self.spotify_id or '—'}, {self.track_plays} track_plays, "
            f"{self.connector_plays} connector_plays]"
        )


@dataclass(frozen=True)
class MergePair:
    """A track the target user owns, beside the foreign track to merge into it.

    The winner is the owned side *by construction*: ``__post_init__`` refuses
    any other arrangement, so no code path — plan, refusal check or mutation —
    can be handed a pair pointing the wrong way. Merging into the foreign row
    would leave this user's history on a row they do not own, which is the
    defect being repaired, so this is a type error rather than a convention.
    """

    user: str
    winner: Side
    loser: Side
    counts: dict[str, int]
    clash_ids: dict[str, UUID]

    def __post_init__(self) -> None:
        if self.winner.owner != self.user:
            raise ValueError(
                f"winner {self.winner.track_id} is owned by {self.winner.owner!r}, "
                f"not by the target user {self.user!r} — the survivor must be the "
                f"track the user owns"
            )
        if self.loser.owner == self.user:
            raise ValueError(
                f"loser {self.loser.track_id} is already owned by {self.user!r} — "
                f"that is a same-user duplicate, not a cross-tenant repair"
            )
        if self.winner.track_id == self.loser.track_id:
            raise ValueError("Cannot merge a track with itself")

    @property
    def collisions(self) -> tuple[str, ...]:
        """The unique constraints this pair collides on."""
        return tuple(
            label
            for column, label in _MERGE_COLLISION_LABELS
            if self.clash_ids.get(column) == self.winner.track_id
        )

    @property
    def collisions_elsewhere(self) -> tuple[tuple[str, UUID], ...]:
        """Clashes naming a *third* track the user owns, not this survivor.

        Rare and worth printing: the loser collides with two different rows of
        the user's, so this merge does not clear it and a second pass will find
        what is left.
        """
        return tuple(
            (label, self.clash_ids[column])
            for column, label in _MERGE_COLLISION_LABELS
            if column in self.clash_ids
            and self.clash_ids[column] != self.winner.track_id
        )

    @property
    def is_same_recording(self) -> bool:
        """The domain predicate, unmodified — the only definition in the repo."""
        return describes_same_recording(
            describe_track(self.winner.as_track()),
            describe_track(self.loser.as_track()),
        )

    @property
    def refusals(self) -> tuple[str, ...]:
        """Every reason this pair must not be merged. Empty means it may be.

        Pure over the discovery counts so the contract is unit-testable. A
        refusal is never overridable: each one names either a genuine identity
        conflict or data the merge would destroy, and forcing it would leave the
        user worse off than the deadlock this mode exists to break.
        """
        reasons: list[str] = []
        if not self.is_same_recording:
            reasons.append(self._identity_refusal())
        reasons.extend(
            reason
            for column, reason in (*_UNSAFE_REASONS, *_LEFT_REASONS, *_CLASH_REFUSALS)
            if self.counts.get(column, 0)
        )
        return tuple(reasons)

    @property
    def is_mergeable(self) -> bool:
        return not self.refusals

    def _identity_refusal(self) -> str:
        """Why ``describes_same_recording`` said no, in the operator's terms."""
        if not (self.winner.duration_ms and self.loser.duration_ms):
            return (
                "a duration is missing on one side — the two rows collide on a "
                "unique constraint with no evidence they are one recording"
            )
        diff = abs(self.winner.duration_ms - self.loser.duration_ms)
        if diff > SUSPECT_DURATION_DIFF_MS:
            return (
                f"durations differ by {diff / 1000:.1f}s, over the "
                f"{SUSPECT_DURATION_DIFF_MS / 1000:.0f}s threshold — a genuine "
                f"identity conflict, not a duplicate: two different recordings "
                f"share an identifier across the two tenants"
            )
        return (
            "normalized title/artist differ between the two rows — they collide "
            "on an identifier while describing different recordings"
        )


def _side(row: dict[str, object], *, prefix: str) -> Side:
    """One track's columns out of a pair row, under the alias prefix they carry."""
    artists_text = _as_str_or_none(row[f"{prefix}artists_text"])
    primary = _as_str_or_none(row[f"{prefix}primary_artist"])
    return Side(
        track_id=_as_uuid(row[f"{prefix}id"]),
        owner=str(row[f"{prefix}owner"]),
        title=str(row[f"{prefix}title"]),
        artists=artists_text or primary or "unknown artist",
        primary_artist=primary or "",
        isrc=_as_str_or_none(row[f"{prefix}isrc"]),
        spotify_id=_as_str_or_none(row[f"{prefix}spotify_id"]),
        duration_ms=_as_int(row[f"{prefix}duration_ms"]) or None,
        track_plays=_as_int(row[f"{prefix}track_plays"]),
        connector_plays=_as_int(row[f"{prefix}connector_plays"]),
    )


def to_pair(row: dict[str, object], *, user: str) -> MergePair:
    """One discovery row as a validated pair. Raises if the winner is not owned.

    The ``clash_*`` columns come in two shapes — the four identifier clashes are
    the *id* of the owned track they collide with, the other two are counts —
    so they are split here rather than coerced into one bag: an id read as a
    count is silently zero, which would hide every collision from the plan.
    """
    clash_columns = {column for column, _ in _MERGE_COLLISION_LABELS}
    return MergePair(
        user=user,
        winner=_side(row, prefix="winner_"),
        loser=_side(row, prefix="loser_"),
        counts={
            key: _as_int(value)
            for key, value in row.items()
            if key.startswith(("carry_", "unsafe_", "left_", "clash_"))
            and key not in clash_columns
        },
        clash_ids={
            column: _as_uuid(row[column])
            for column in clash_columns
            if row.get(column) is not None
        },
    )


async def discover_merge_pairs(
    session: AsyncSession, *, user: str, from_tenant: str | None
) -> list[MergePair]:
    """Every cross-tenant track the target user's plays need, with its survivor."""
    result = await session.execute(
        text(_MERGE_PAIRS_SQL).bindparams(bindparam("from_tenant", type_=String)),
        {"user": user, "from_tenant": from_tenant},
    )
    return [to_pair(dict(row), user=user) for row in result.mappings().all()]


def render_merge_plan(pairs: list[MergePair], user: str) -> None:
    """Print every pair, both sides, and what each merge would move or refuse."""
    mergeable = [p for p in pairs if p.is_mergeable]
    refused = [p for p in pairs if not p.is_mergeable]

    print(f"\n\n=== MERGE MODE: cross-tenant pairs for user {user} ===")
    print(
        f"{len(pairs)} foreign tracks collide with a track this user already "
        f"owns, so a re-own is impossible and a merge is the remedy"
    )
    print("survivor rule: the track the target user OWNS wins — always.")
    print(
        "  merging into the other tenant's row would leave this user's history "
        "on a row they do not own, which is the defect being repaired."
    )

    print(f"\n--- MERGEABLE: {len(mergeable)} pairs ---")
    for pair in mergeable:
        _render_merge_pair(pair)
    print(f"\n--- REFUSED: {len(refused)} pairs ---")
    for pair in refused:
        _render_merge_pair(pair)
        for reason in pair.refusals:
            print(f"    REFUSED: {reason}")


def _render_merge_pair(pair: MergePair) -> None:
    print(f"\n  pair: {pair.winner.artists} — {pair.winner.title}")
    print(f"    collides on: {', '.join(pair.collisions) or 'nothing (?)'}")
    print(f"    WINNER (owned)  {pair.winner.describe()}")
    print(f"    loser           {pair.loser.describe()}")
    carried = ", ".join(
        f"{key.removeprefix('carry_')}={pair.counts[key]}"
        for key in sorted(pair.counts)
        if key.startswith("carry_") and pair.counts[key]
    )
    print(f"    carried onto the winner: {carried or 'none'}")
    for label, other_id in pair.collisions_elsewhere:
        print(
            f"    ALSO collides with a different owned track on {label}: "
            f"{other_id} — this merge does not clear that one"
        )
    stranded = pair.counts.get("carry_stranded_events", 0)
    if stranded:
        print(
            f"    NOTE: {stranded} resolution_events name the loser track by "
            f"value (no FK) and will point at a deleted id — history is kept "
            f"as written, exactly as the cleanup script keeps it"
        )


async def _locked_owners(session: AsyncSession, pair: MergePair) -> dict[UUID, str]:
    """Both tracks' current owners, locked for the rest of the transaction."""
    result = await session.execute(
        text(_LOCK_PAIR_SQL), {"ids": [pair.winner.track_id, pair.loser.track_id]}
    )
    return {_as_uuid(row["id"]): str(row["user_id"]) for row in result.mappings().all()}


async def assert_pair_still_valid(session: AsyncSession, pair: MergePair) -> None:
    """Re-read and lock both tracks, and refuse unless ownership still holds.

    The second enforcement of the winner-is-owned invariant, and the one that
    survives a plan going stale: ``MergePair`` proves the *plan* pointed the
    right way, this proves the *database* still does at the moment of writing.
    """
    owners = await _locked_owners(session, pair)
    winner_owner = owners.get(pair.winner.track_id)
    loser_owner = owners.get(pair.loser.track_id)
    if winner_owner != pair.user:
        raise ValueError(
            f"winner {pair.winner.track_id} is owned by {winner_owner!r}, not "
            f"{pair.user!r} — refusing to merge into a row this user does not own"
        )
    if loser_owner is None or loser_owner == pair.user:
        raise ValueError(
            f"loser {pair.loser.track_id} is owned by {loser_owner!r} — the pair "
            f"changed since the plan was built; re-run to converge"
        )


async def assert_nothing_will_cascade(session: AsyncSession, pair: MergePair) -> None:
    """Refuse unless every table the merge does not move is empty on the loser.

    ``hard_delete_track`` is a real DELETE and every FK below it is CASCADE, so
    a row still here at this point is a row the merge destroys silently. The
    ledger is not among them — ``move_references_to_track`` moves
    ``connector_plays`` onto the winner before the delete, so those rows are
    expected to still be present here and are not counted.
    """
    row = dict(
        (
            await session.execute(
                text(_CASCADE_GUARD_SQL), {"loser": pair.loser.track_id}
            )
        )
        .mappings()
        .one()
    )
    remaining = {key: _as_int(value) for key, value in row.items() if _as_int(value)}
    if remaining:
        raise ValueError(
            f"refusing to merge {pair.loser.track_id}: {remaining} would be "
            f"CASCADE-deleted with it — the merge moves none of these tables"
        )


async def merge_pair(
    session: AsyncSession, uow: UnitOfWorkProtocol, pair: MergePair
) -> dict[str, int]:
    """Merge one cross-tenant pair. One transaction, or none of it.

    Order is the correctness property:

    1. lock both tracks and re-check ownership;
    2. retire the loser's colliding live mappings into the user's own and
       re-own the rest, so nothing foreign survives the merge and the mapping
       CTE that follows sees a single-tenant pair;
    3. record those conflations through the same seam the merge service uses;
    4. re-check that nothing is left to CASCADE;
    5. ``TrackMergeService.merge_tracks`` — which moves the mappings and the
       ``connector_plays`` ledger *before* deleting the track, so migration
       051's RESTRICT never fires and no observation is CASCADE-deleted.
    """
    now = datetime.now(UTC)
    ids = {"winner": pair.winner.track_id, "loser": pair.loser.track_id}
    await assert_pair_still_valid(session, pair)

    conflated = (
        (
            await session.execute(
                text(_CONFLATE_MAPPINGS_SQL),
                {**ids, "user": pair.user, "now": now, "conflation": _CONFLATION},
            )
        )
        .mappings()
        .all()
    )
    edges = [
        SupersessionEdge(
            predecessor_id=_as_uuid(row["predecessor_id"]),
            successor_id=_as_uuid(row["successor_id"]),
            user_id=pair.user,
            connector_name=str(row["connector_name"]),
        )
        for row in conflated
    ]
    if edges:
        _ = await uow.get_resolution_recorder().record_supersessions(
            edges, reason=_CONFLATION
        )
    # Whatever is left on the loser has no live counterpart under this user
    # (the statement above retired every one that did), so a plain re-own
    # cannot trip the live-connector index.
    reowned_mappings = await session.execute(
        update(DBTrackMapping)
        .where(
            DBTrackMapping.track_id == pair.loser.track_id,
            DBTrackMapping.user_id != pair.user,
        )
        .values(user_id=pair.user)
    )
    reowned_metrics = await session.execute(
        update(DBTrackMetric)
        .where(
            DBTrackMetric.track_id == pair.loser.track_id,
            DBTrackMetric.user_id != pair.user,
        )
        .values(user_id=pair.user)
    )

    await assert_nothing_will_cascade(session, pair)
    _ = await uow.get_track_merge_service().merge_tracks(
        pair.winner.track_id, pair.loser.track_id, uow
    )
    return {
        "mappings_conflated": len(edges),
        "mappings_reowned": reowned_mappings.rowcount,
        "metrics_reowned": reowned_metrics.rowcount,
    }


async def _run_merge_mode(*, user: str, from_tenant: str | None, apply: bool) -> None:
    """Plan — and with ``--apply --merge``, execute — the cross-tenant merges."""
    with user_context(user):
        async with get_session() as session:
            pairs = await discover_merge_pairs(
                session, user=user, from_tenant=from_tenant
            )
    if not pairs:
        print("\n\nMerge mode: no cross-tenant collisions found. Nothing to merge.")
        return

    render_merge_plan(pairs, user)
    mergeable = [p for p in pairs if p.is_mergeable]
    print("\n=== merge summary ===")
    print(f"  {len(mergeable)} pairs would be merged")
    print(f"  {len(pairs) - len(mergeable)} pairs refused")
    print(
        f"  {sum(p.loser.track_plays for p in mergeable)} track_plays and "
        f"{sum(p.loser.connector_plays for p in mergeable)} connector_plays "
        f"move onto tracks this user owns"
    )
    logger.info(
        "Cross-tenant merge plan",
        pairs=len(pairs),
        mergeable=len(mergeable),
        refused=len(pairs) - len(mergeable),
        user_id=user,
        mode="APPLY" if apply else "DRY-RUN",
    )

    if not apply:
        print("\nDry-run only. Re-run with --merge --apply to execute the merges.")
        return

    for pair in mergeable:
        with user_context(user):
            async with get_session() as session:
                uow = get_unit_of_work(session)
                counts = await merge_pair(session, uow, pair)
                await uow.commit()
        logger.info(
            "Cross-tenant merge complete",
            winner_id=str(pair.winner.track_id),
            loser_id=str(pair.loser.track_id),
            from_tenant=pair.loser.owner,
            to_user=user,
            **counts,
        )


async def _run(*, user: str, from_tenant: str | None, apply: bool, merge: bool) -> None:
    setup_script_logger("reown_cross_tenant_tracks")

    # Discovery is inherently cross-tenant (it compares two tenants' rows), so
    # it runs under the target's context and relies on the explicit predicates.
    with user_context(user):
        async with get_session() as session:
            tracks = await discover(session, user=user, from_tenant=from_tenant)
    logger.info(
        "Cross-tenant tracks discovered",
        count=len(tracks),
        user_id=user,
        from_tenant=from_tenant,
        mode="APPLY" if apply else "DRY-RUN",
    )
    if not tracks:
        print(f"\nNo cross-tenant tracks back user {user}'s plays. Nothing to re-own.")
        if merge:
            await _run_merge_mode(user=user, from_tenant=from_tenant, apply=apply)
        return

    render_plan(tracks, user)

    movable = [t for t in tracks if not t.is_blocked]
    by_owner: dict[str, list[UUID]] = {}
    for track in movable:
        by_owner.setdefault(track.owner, []).append(track.track_id)

    with user_context(user):
        async with get_session() as session:
            for owner, ids in sorted(by_owner.items()):
                straddling = await straddling_chain_edges(
                    session, owner=owner, track_ids=ids
                )
                if straddling:
                    print(
                        f"\n  WARNING: {straddling} supersession edge(s) under "
                        f"{owner!r} have one end inside the moved set and one "
                        f"outside — the chain will straddle two tenants after "
                        f"the move. Nothing breaks at the database (the chain "
                        f"FK does not involve user_id), but a per-tenant read "
                        f"of that history will be incomplete."
                    )

    if not apply:
        print("\nDry-run only. Re-run with --apply to execute the re-own.")
    elif not movable:
        print("\nNothing to apply — every discovered track is refused.")
    else:
        for owner, ids in sorted(by_owner.items()):
            with user_context(owner):
                async with get_session() as session:
                    counts = await reown(session, user=user, owner=owner, track_ids=ids)
            logger.info("Re-own complete", from_tenant=owner, to_user=user, **counts)
            if counts.get("tracks", 0) != len(ids):
                logger.warning(
                    "Re-owned track count differs from the plan — rows changed "
                    "between discovery and update; re-run to converge.",
                    tracks_reowned=counts.get("tracks", 0),
                    planned=len(ids),
                )

    if merge:
        await _run_merge_mode(user=user, from_tenant=from_tenant, apply=apply)


def main(
    user: str = typer.Option(
        ...,
        help="The tenant whose plays depend on tracks another tenant owns — the "
        "tracks are re-owned onto this user.",
    ),
    from_tenant: str = typer.Option(
        default="",
        help="Only re-own tracks currently owned by this tenant. Empty means "
        "every tenant discovery finds.",
    ),
    apply: bool = typer.Option(
        default=False,
        help="Execute the re-own. Without it the script only prints the plan.",
    ),
    merge: bool = typer.Option(
        default=False,
        help="Also plan the merges for the tracks a re-own refuses. Deletes "
        "canonical rows when combined with --apply, so it is a second, "
        "separate opt-in: neither flag alone merges anything.",
    ),
) -> None:
    """Re-own cross-tenant tracks onto the user whose plays need them."""
    # The batched UPDATEs touch tracks plus four companion tables; give them the
    # bulk-import ceiling rather than the 30s connection default.
    with statement_timeout_context(BusinessLimits.BULK_IMPORT_STATEMENT_TIMEOUT):
        asyncio.run(
            _run(
                user=user,
                from_tenant=from_tenant or None,
                apply=apply,
                merge=merge,
            )
        )


if __name__ == "__main__":
    typer.run(main)
