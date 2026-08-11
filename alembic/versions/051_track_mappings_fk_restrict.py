"""Stop a track delete from cascading away append-only mapping history.

Migration 044 made ``track_mappings`` append-only, but left every inbound FK on
the table as ``ON DELETE CASCADE`` — so a single ``DELETE FROM tracks`` still
silently removed the retired rows that record what mixd used to believe.
Production reached exactly that state: ``resolution_events`` holds 5
supersession events naming only 4 surviving superseded mappings, because
``scripts/cleanup_mistenanted_spotify_tracks.py`` issues a Core
``delete(DBTrack)`` that cascades straight through
``fk_track_mappings_track_id_tracks``. The events table references mappings *by
value* with no FK of its own, so nothing complains: the log survives, its
subject does not.

v0.10.3 added a repository guard in ``hard_delete_track``, which a raw Core
DELETE never calls. Only the constraint stops that, so all three FKs flip:

- ``track_id`` → **RESTRICT**. The one caller that legitimately deletes a track
  (``TrackMergeService``) runs ``merge_mappings_to_track`` first, which moves
  live *and* retired rows onto the winner — so the happy path is untouched and
  everything else now fails loudly at the database instead of quietly.
- ``connector_track_id`` → **RESTRICT**. Latent today (no application path
  deletes ``connector_tracks``), closed for the same reason: a shared-cache
  eviction added later must not be able to take user history with it.
- ``superseded_by_id`` → **RESTRICT**, keeping ``DEFERRABLE INITIALLY
  DEFERRED``. The subtler hole: under ``SET NULL``, deleting a successor
  silently blanked its predecessor's pointer, leaving a retired row with a
  ``supersession_reason`` and no successor — indistinguishable from a
  legitimate retirement-with-no-replacement, which
  ``ck_track_mappings_supersession_coherent`` deliberately permits. There is no
  repairing that after the fact, so it has to be prevented.

Deferrability is preserved because it guards a different trigger: the *insert*
side check, which the write path needs when it stamps a predecessor with a
successor id in the same statement batch that later inserts the successor.
PostgreSQL runs the RESTRICT *delete* action non-deferrably regardless of the
clause, which is precisely the behaviour wanted here.

Prod-verified read-only before writing (2026-08-09): the three constraint names
below are the real ones, ``confdeltype`` was ``c``/``c``/``n`` as described, and
there are zero broken supersession chains, zero orphaned ``superseded_by_id``
values and zero mappings on a missing track — so re-validating the rebuilt
constraints cannot fail on existing data.

Rebuild rather than a catalog poke: PostgreSQL has no ``ALTER CONSTRAINT`` for
a referential action, so each FK is dropped and re-added. That re-scans
``track_mappings`` (~84k rows in prod, milliseconds) under an ACCESS EXCLUSIVE
lock on it plus SHARE ROW EXCLUSIVE on the two referenced tables. ``NOT VALID``
+ ``VALIDATE CONSTRAINT`` would trade that for two transactions, which is not
worth it at this size.

Revision ID: 051_track_mappings_fk_restrict
Revises: 050_play_exclusion_reason
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "051_track_mappings_fk_restrict"
down_revision: str | None = "050_play_exclusion_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "track_mappings"

_FK_TRACK = "fk_track_mappings_track_id_tracks"
_FK_CONNECTOR_TRACK = "fk_track_mappings_connector_track_id_connector_tracks"
_FK_SUPERSEDED_BY = "fk_track_mappings_superseded_by_id_track_mappings"


def _rebuild(
    name: str,
    referent: str,
    local_column: str,
    *,
    ondelete: str,
    deferrable: bool = False,
) -> None:
    """Drop and re-add one FK with a different referential action.

    ``op.f`` marks the name as already conforming to the metadata naming
    convention so it is not re-derived (precedent: migration 044) — these names
    were verified against ``pg_constraint`` in production and must round-trip
    unchanged, since ``db_models.py`` mirrors them for ``metadata.create_all``.
    """
    op.drop_constraint(op.f(name), _TABLE, type_="foreignkey")
    op.create_foreign_key(
        op.f(name),
        _TABLE,
        referent,
        [local_column],
        ["id"],
        ondelete=ondelete,
        deferrable=deferrable or None,
        initially="DEFERRED" if deferrable else None,
    )


def upgrade() -> None:
    _rebuild(_FK_TRACK, "tracks", "track_id", ondelete="RESTRICT")
    _rebuild(
        _FK_CONNECTOR_TRACK,
        "connector_tracks",
        "connector_track_id",
        ondelete="RESTRICT",
    )
    # Deferrability is not part of what changes here — see the docstring.
    _rebuild(
        _FK_SUPERSEDED_BY,
        _TABLE,
        "superseded_by_id",
        ondelete="RESTRICT",
        deferrable=True,
    )


def downgrade() -> None:
    """Restore 044's cascading actions.

    Lossy in the same sense 044's downgrade is: it re-opens the hole, and any
    track delete that happens afterwards will take mapping history with it.
    """
    _rebuild(_FK_TRACK, "tracks", "track_id", ondelete="CASCADE")
    _rebuild(
        _FK_CONNECTOR_TRACK,
        "connector_tracks",
        "connector_track_id",
        ondelete="CASCADE",
    )
    _rebuild(
        _FK_SUPERSEDED_BY,
        _TABLE,
        "superseded_by_id",
        ondelete="SET NULL",
        deferrable=True,
    )
