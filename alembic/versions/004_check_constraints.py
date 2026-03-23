"""Add CHECK constraints for confidence and match_weight ranges.

Defense in depth: Python validators catch bad data first, but CHECK
constraints prevent silent corruption if a bug or migration bypasses
application-level validation.

- track_mappings.confidence: 0-100
- match_reviews.confidence: 0-100
- match_reviews.match_weight: 0.0-1.0

Revision ID: 004_check_constraints
Revises: 003_keyset_idx
Create Date: 2026-03-18
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004_check_constraints"
down_revision: str = "003_keyset_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "chk_mapping_confidence_range",
        "track_mappings",
        "confidence >= 0 AND confidence <= 100",
    )
    op.create_check_constraint(
        "chk_review_confidence_range",
        "match_reviews",
        "confidence >= 0 AND confidence <= 100",
    )
    op.create_check_constraint(
        "chk_review_match_weight_range",
        "match_reviews",
        "match_weight >= 0.0 AND match_weight <= 1.0",
    )


def downgrade() -> None:
    op.drop_constraint("chk_review_match_weight_range", "match_reviews", type_="check")
    op.drop_constraint("chk_review_confidence_range", "match_reviews", type_="check")
    op.drop_constraint("chk_mapping_confidence_range", "track_mappings", type_="check")
