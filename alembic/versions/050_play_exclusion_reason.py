"""Record why a ledger observation is absent from canonical play history.

A service export is an event log, not a list of listens — it records every time
the player started audio, including thousands of one-second skips. Resolution
drops most of those on purpose, but recorded that decision by declining to
write the track id it had already found, so a deliberate skip and a genuine
identification failure both read as ``resolved_track_id IS NULL``.

The backfill separates the classes that were already collapsed:

- ``incognito`` — private sessions, partitioned out before resolution
- ``too_short`` — the track resolved (a connector track and this user's mapping
  both exist) and the play then failed the listen threshold
- ``unresolved`` — no known track: the genuine failures

Explanatory only. ``resolved_track_id`` stays the projection's predicate, so no
play changes status and no rebuild is required.

Revision ID: 050_play_exclusion_reason
Revises: 049_tracks_user_normalized
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "050_play_exclusion_reason"
down_revision: str | None = "049_tracks_user_normalized"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "connector_plays"
_COLUMN = "exclusion_reason"

# The FORCE-RLS tables this backfill touches: connector_plays is updated,
# track_mappings is read by the too_short classification. connector_tracks is
# absent by design — it is not user-scoped and carries no policy.
_RLS_TABLES = ("connector_plays", "track_mappings")

# Private sessions never reach resolution, so they are classified first and
# their track is unknown by construction.
_BACKFILL_INCOGNITO = sa.text("""
    UPDATE connector_plays
    SET exclusion_reason = 'incognito'
    WHERE resolved_track_id IS NULL
      AND exclusion_reason IS NULL
      AND raw_metadata -> 'service_metadata' ->> 'incognito_mode' = 'true'
""")

# Scoped to Spotify because the listen threshold is Spotify's: Last.fm carries
# no ms_played at all, so it cannot produce a too-short play. Without this an
# unresolved Last.fm row whose track happens to be known would be stamped with
# a state its channel can never reach.
#
# "The track is known" is what separates a policy exclusion from a failure:
# resolution is what creates the connector track and the user's mapping, so
# their existence means the play resolved and was then dropped by the listen
# threshold. Matched through the user's own mapping rather than the shared
# connector_tracks row, so another user's track never reads as known here.
#
# The two tables identify a Spotify track differently — the ledger stores the
# full ``spotify:track:<id>`` URI it was imported with, connector_tracks the
# bare id — so the prefix has to come off or the join silently matches nothing
# and every skip is misfiled as a failure. Last.fm identifiers (artist::title)
# carry no such prefix and pass through untouched.
_BACKFILL_TOO_SHORT = sa.text("""
    UPDATE connector_plays cp
    SET exclusion_reason = 'too_short'
    WHERE cp.resolved_track_id IS NULL
      AND cp.exclusion_reason IS NULL
      AND cp.connector_name = 'spotify'
      AND EXISTS (
          SELECT 1
          FROM connector_tracks ct
          JOIN track_mappings tm ON tm.connector_track_id = ct.id
          WHERE ct.connector_name = cp.connector_name
            AND ct.connector_track_identifier = regexp_replace(
                cp.connector_track_identifier, '^spotify:track:', ''
            )
            AND tm.user_id = cp.user_id
      )
""")

_BACKFILL_UNRESOLVED = sa.text("""
    UPDATE connector_plays
    SET exclusion_reason = 'unresolved'
    WHERE resolved_track_id IS NULL
      AND exclusion_reason IS NULL
""")


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(16), nullable=True))

    # RLS bracket (precedent: migrations 035 and 040). Both tables below carry
    # FORCE ROW LEVEL SECURITY (011) with a policy keyed on
    # ``current_setting('app.user_id')``, which is unset under Alembic — so on
    # an owner without BYPASSRLS every UPDATE would match zero rows and the
    # EXISTS over track_mappings would find nothing. The column would ship
    # entirely NULL with the migration reporting success, and no test catches
    # it because the testcontainers superuser bypasses RLS unconditionally.
    #
    # Unlike 040 this needs no autocommit block: one transaction, and ALTER
    # TABLE is transactional in PostgreSQL, so a failure rolls the bracket back
    # with the rest of the migration.
    for table in _RLS_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
    try:
        # Order is load-bearing: each statement claims only rows the previous
        # ones left unclassified, so the three classes stay disjoint.
        for statement in (
            _BACKFILL_INCOGNITO,
            _BACKFILL_TOO_SHORT,
            _BACKFILL_UNRESOLVED,
        ):
            _ = op.get_bind().execute(statement)
    finally:
        for table in _RLS_TABLES:
            op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN)
