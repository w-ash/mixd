"""add normalized lookup columns to tracks

Revision ID: a1b2c3d4e5f6
Revises: 040d47093f7d
Create Date: 2026-03-12 12:00:00.000000

"""

import json
import re
import unicodedata
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "040d47093f7d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Inline normalization (mirrors domain/matching/text_normalization.py)
# Duplicated here so the migration is self-contained and never breaks
# if the domain code changes later.
# ---------------------------------------------------------------------------

_EQUIVALENCES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bfeat\.?\b", re.IGNORECASE), "featuring"),
    (re.compile(r"\bft\.?\b", re.IGNORECASE), "featuring"),
    (re.compile(r"\s*&\s*"), " and "),
    (re.compile(r"\s*\+\s*"), " and "),
]
_LEADING_ARTICLE = re.compile(r"^the\s+", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize(text: str) -> str:
    """Full normalization: lowercase → strip diacritics → equivalences → strip punctuation."""
    result = text.lower()
    nfd = unicodedata.normalize("NFD", result)
    result = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    for pattern, replacement in _EQUIVALENCES:
        result = pattern.sub(replacement, result)
    result = _LEADING_ARTICLE.sub("", result)
    result = _NON_ALNUM.sub("", result)
    return " ".join(result.split())


def upgrade() -> None:
    """Add title_normalized + artist_normalized columns, backfill, create composite index."""
    # Step 1: Add nullable columns
    with op.batch_alter_table("tracks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("title_normalized", sa.String(255), nullable=True))
        batch_op.add_column(
            sa.Column("artist_normalized", sa.String(255), nullable=True)
        )

    # Step 2: Backfill using Python (SQLite lacks Unicode-aware functions)
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, title, artists FROM tracks")).fetchall()

    for row_id, title, artists_raw in rows:
        title_norm = _normalize(title) if title else None

        artist_norm = None
        if artists_raw:
            try:
                artists_data = (
                    json.loads(artists_raw) if isinstance(artists_raw, str) else artists_raw
                )
                names = artists_data.get("names", [])
                if names:
                    artist_norm = _normalize(names[0])
            except (json.JSONDecodeError, AttributeError, TypeError):
                pass

        conn.execute(
            sa.text(
                "UPDATE tracks SET title_normalized = :tn, artist_normalized = :an WHERE id = :id"
            ),
            {"tn": title_norm, "an": artist_norm, "id": row_id},
        )

    # Step 3: Create composite index for Phase 1.5 lookup
    with op.batch_alter_table("tracks", schema=None) as batch_op:
        batch_op.create_index(
            "ix_tracks_normalized_lookup",
            ["title_normalized", "artist_normalized"],
        )


def downgrade() -> None:
    """Remove normalized columns and index."""
    with op.batch_alter_table("tracks", schema=None) as batch_op:
        batch_op.drop_index("ix_tracks_normalized_lookup")
        batch_op.drop_column("artist_normalized")
        batch_op.drop_column("title_normalized")
