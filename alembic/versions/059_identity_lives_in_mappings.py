"""Drop ``tracks.spotify_id`` / ``tracks.mbid``: identity lives in mappings.

A canonical's connector ids belong to ``track_mappings``, which records which
connector track a canonical is mapped to, when, and by what evidence. The two
columns on ``tracks`` mirrored the primary mapping and had no lookup readers
left — they only mattered because ``uq_tracks_user_spotify_id`` /
``uq_tracks_user_mbid`` made them identity keys, so a Spotify id the library
already knew blocked an import forever. ``isrc`` and ``uq_tracks_user_isrc``
stay: the ISRC is a property of the recording, not of one service.

What is lost: a ``spotify_id`` (or ``mbid``) with no live primary mapping for
that connector. Production was repaired to zero such rows in v0.12.0.1. List
them before upgrading::

    SELECT t.id, t.spotify_id FROM tracks t WHERE t.spotify_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM track_mappings m WHERE m.track_id = t.id AND m.connector_name = 'spotify' AND m.superseded_by_id IS NULL);

``downgrade`` recreates the columns, indexes and constraints and refills each
from the track's live primary mapping, which is the state the promotion hook
maintained.

Plain DDL, no ``CONCURRENTLY``: the release command runs over the Neon pooler
URL, where ``autocommit_block`` cannot run (see 049 and 051).

Revision ID: 059_identity_lives_in_mappings
Revises: 058_resolution_event_entity_kind
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "059_identity_lives_in_mappings"
down_revision: str | None = "058_resolution_event_entity_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Column → the connector whose live primary mapping refills it on downgrade.
_COLUMN_CONNECTORS: dict[str, str] = {
    "spotify_id": "spotify",
    "mbid": "musicbrainz",
}


def _refill(column: str, connector: str) -> str:
    return f"""
        UPDATE tracks SET {column} = ct.connector_track_identifier
        FROM track_mappings m
        JOIN connector_tracks ct ON ct.id = m.connector_track_id
        WHERE m.track_id = tracks.id
          AND m.connector_name = '{connector}'
          AND m.is_primary
          AND m.superseded_by_id IS NULL
    """


def upgrade() -> None:
    op.drop_constraint("uq_tracks_user_spotify_id", "tracks", type_="unique")
    op.drop_constraint("uq_tracks_user_mbid", "tracks", type_="unique")
    op.drop_index("ix_tracks_spotify_id", table_name="tracks")
    op.drop_index("ix_tracks_mbid", table_name="tracks")
    op.drop_column("tracks", "spotify_id")
    op.drop_column("tracks", "mbid")


def downgrade() -> None:
    op.add_column("tracks", sa.Column("spotify_id", sa.String(), nullable=True))
    op.add_column("tracks", sa.Column("mbid", sa.String(length=36), nullable=True))
    for column, connector in _COLUMN_CONNECTORS.items():
        op.execute(_refill(column, connector))
    op.create_index("ix_tracks_spotify_id", "tracks", ["spotify_id"])
    op.create_index("ix_tracks_mbid", "tracks", ["mbid"])
    op.create_unique_constraint(
        "uq_tracks_user_spotify_id", "tracks", ["user_id", "spotify_id"]
    )
    op.create_unique_constraint("uq_tracks_user_mbid", "tracks", ["user_id", "mbid"])
