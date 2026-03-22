"""add title_stripped column for parenthetical fallback matching

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-12 14:00:00.000000

"""

from collections.abc import Sequence
import re
import unicodedata

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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

_PARENTHETICAL = re.compile(r"\s*[\(\[][^)\]]*[\)\]]")
_DASH_QUALIFIER = re.compile(
    r"\s*-\s*(remix|remaster(?:ed)?|live|radio edit|extended|instrumental|bonus track|deluxe|single version|album version)\b.*",
    re.IGNORECASE,
)


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


def _strip_parentheticals(text: str) -> str:
    """Remove parenthetical/bracket suffixes and dash-separated qualifiers."""
    result = _PARENTHETICAL.sub("", text)
    result = _DASH_QUALIFIER.sub("", result)
    return result.strip()


def upgrade() -> None:
    """Add title_stripped column, backfill, create composite index."""
    # Step 1: Add nullable column
    with op.batch_alter_table("tracks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("title_stripped", sa.String(255), nullable=True))

    # Step 2: Backfill using Python
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, title FROM tracks")).fetchall()

    for row_id, title in rows:
        title_stripped = _normalize(_strip_parentheticals(title)) if title else None
        conn.execute(
            sa.text("UPDATE tracks SET title_stripped = :ts WHERE id = :id"),
            {"ts": title_stripped, "id": row_id},
        )

    # Step 3: Create composite index for stripped lookup
    with op.batch_alter_table("tracks", schema=None) as batch_op:
        batch_op.create_index(
            "ix_tracks_stripped_lookup",
            ["title_stripped", "artist_normalized"],
        )


def downgrade() -> None:
    """Remove title_stripped column and index."""
    with op.batch_alter_table("tracks", schema=None) as batch_op:
        batch_op.drop_index("ix_tracks_stripped_lookup")
        batch_op.drop_column("title_stripped")
