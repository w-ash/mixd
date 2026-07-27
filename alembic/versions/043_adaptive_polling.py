"""Adaptive play polling: poll lease + backoff state + interval cadence (v0.10.1 Epic C).

Three independent additions, all in service of polling Spotify's recently-played
window often enough that plays never age out of it, without waking the database
more than necessary.

``sync_checkpoints`` gains the poll's own state, kept separate from the sync
cursor it already holds:

- ``last_polled_at`` is the freshness gate. It records *when we last checked*,
  never *when the user last listened* — ``last_timestamp`` already means the
  latter, and gating on it would fire a pointless poll on every page view of an
  account that simply isn't playing anything.
- ``poll_state`` (JSONB) carries the backoff counter and the near-overflow
  markers, schemaless so tuning the policy needs no migration.
- ``poll_claimed_at`` is a **lease**, not a lock: a claim stamp with a TTL, in the
  same shape as ``schedules.started_at`` and its stuck-claim reaper. A
  transaction-scoped alternative (``FOR UPDATE SKIP LOCKED``, an advisory xact
  lock) cannot work here because the importer commits per batch and would release
  it at the first internal commit.

``operation_runs.trigger_detail`` records *why* a run happened (``web``, ``mcp``,
``workflow:<run_id>``) alongside the existing ``initiated_by``, so a poll fired by
a page view is distinguishable from one fired by a scheduled workflow.

``schedules.interval_minutes`` adds a third cadence arm beside daily and weekly.
The play poller writes its own backed-off interval here after every poll, so the
scheduler's sleep-until-due loop stays asleep for the full stretched duration
instead of waking every 30 minutes to decide it has nothing to do. The 5-minute
lower bound is what the constraint permits, not what the poller uses (30 minutes
base) — a 5-minute cadence would hold the compute awake continuously.

Revision ID: 043_adaptive_polling
Revises: 042_play_sources
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "043_adaptive_polling"
down_revision: str | None = "042_play_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECKPOINTS = "sync_checkpoints"
_RUNS = "operation_runs"
_SCHEDULES = "schedules"


def _check_names() -> tuple[str, str]:
    """Final CHECK-constraint names for the schedules cadence arm.

    ``op.f()`` marks a name as already-conventionalised: the metadata convention
    is ``ck_%(table_name)s_%(constraint_name)s``, so an unwrapped name gets
    prefixed a second time (``ck_schedules_ck_schedules_...``) — a real bug
    caught by reading ``pg_constraint`` after an earlier revision of this file.

    Called from inside ``upgrade``/``downgrade`` rather than at module scope
    because ``op`` is a proxy bound to a live ``MigrationContext``. At import
    time no context exists, so a module-level ``op.f()`` raises and makes the
    whole revision chain unloadable — which breaks ``alembic heads`` and
    ``alembic history`` while leaving ``upgrade`` working, since that command
    establishes the context before loading scripts. Migration 025 calls
    ``op.f()`` inside ``upgrade()`` for the same reason.
    """
    return (
        op.f("ck_schedules_interval_minutes"),
        op.f("ck_schedules_cadence_exclusive"),
    )


def upgrade() -> None:
    interval_range, cadence_exclusive = _check_names()
    op.add_column(
        _CHECKPOINTS,
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        _CHECKPOINTS,
        sa.Column(
            "poll_state",
            JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        _CHECKPOINTS,
        sa.Column("poll_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column(_RUNS, sa.Column("trigger_detail", sa.String(64), nullable=True))

    op.add_column(
        _SCHEDULES, sa.Column("interval_minutes", sa.Integer(), nullable=True)
    )
    op.create_check_constraint(
        interval_range,
        _SCHEDULES,
        "interval_minutes IS NULL OR interval_minutes BETWEEN 5 AND 1440",
    )
    # Exclusive arc, mirroring ck_schedules_target_xor's num_nonnulls idiom: a
    # schedule is daily (neither set), weekly (day_of_week), or interval-based —
    # never two cadences at once.
    op.create_check_constraint(
        cadence_exclusive,
        _SCHEDULES,
        "num_nonnulls(interval_minutes, day_of_week) < 2",
    )


def downgrade() -> None:
    interval_range, cadence_exclusive = _check_names()

    op.drop_constraint(cadence_exclusive, _SCHEDULES, type_="check")
    op.drop_constraint(interval_range, _SCHEDULES, type_="check")
    op.drop_column(_SCHEDULES, "interval_minutes")

    op.drop_column(_RUNS, "trigger_detail")

    op.drop_column(_CHECKPOINTS, "poll_claimed_at")
    op.drop_column(_CHECKPOINTS, "poll_state")
    op.drop_column(_CHECKPOINTS, "last_polled_at")
