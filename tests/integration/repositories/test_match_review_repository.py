"""Integration tests for MatchReviewRepository.

Tests real database operations for the match review queue — create, list,
update status — using the db_session fixture with SQLite.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.constants import ReviewStatus
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBMatchReview,
    DBTrack,
)
from src.infrastructure.persistence.repositories.match_review import (
    MatchReviewRepository,
)
from src.domain.entities.match_review import MatchReview


async def _seed_track_and_connector_track(
    session: AsyncSession,
) -> tuple[int, int]:
    """Insert a track and connector track, return their IDs."""
    uid = uuid4().hex[:8]
    now = datetime.now(UTC)

    track = DBTrack(
        title=f"Track {uid}",
        artists=[{"name": f"Artist {uid}"}],
        spotify_id=f"sp_{uid}",
        isrc=f"ISRC{uid.upper()[:8]}",
        mbid=f"mbid-{uid}",
    )
    session.add(track)
    await session.flush()

    ct = DBConnectorTrack(
        connector_name="spotify",
        connector_track_identifier=f"sp_ct_{uid}",
        title=f"Spotify Track {uid}",
        artists=[{"name": f"Spotify Artist {uid}"}],
        raw_metadata={"id": f"sp_ct_{uid}"},
        last_updated=now,
    )
    session.add(ct)
    await session.flush()

    return track.id, ct.id


class TestCreateReview:
    """Creating reviews persists them correctly."""

    async def test_create_single_review(self, db_session: AsyncSession):
        track_id, ct_id = await _seed_track_and_connector_track(db_session)
        repo = MatchReviewRepository(db_session)

        review = MatchReview(
            track_id=track_id,
            connector_name="spotify",
            connector_track_id=ct_id,
            match_method="artist_title",
            confidence=72,
            match_weight=4.5,
        )
        result = await repo.create_review(review)

        assert result.id is not None
        assert result.track_id == track_id
        assert result.confidence == 72
        assert result.status == "pending"

    async def test_upsert_deduplicates(self, db_session: AsyncSession):
        track_id, ct_id = await _seed_track_and_connector_track(db_session)
        repo = MatchReviewRepository(db_session)

        review = MatchReview(
            track_id=track_id,
            connector_name="spotify",
            connector_track_id=ct_id,
            match_method="artist_title",
            confidence=72,
            match_weight=4.5,
        )
        first = await repo.create_review(review)
        second = await repo.create_review(review)

        # Same review should be updated, not duplicated
        assert first.id == second.id


class TestCreateBatch:
    """Batch creation handles multiple reviews."""

    async def test_batch_creates_multiple(self, db_session: AsyncSession):
        track_id, ct_id = await _seed_track_and_connector_track(db_session)
        track_id2, ct_id2 = await _seed_track_and_connector_track(db_session)
        repo = MatchReviewRepository(db_session)

        reviews = [
            MatchReview(
                track_id=track_id,
                connector_name="spotify",
                connector_track_id=ct_id,
                match_method="artist_title",
                confidence=72,
                match_weight=4.5,
            ),
            MatchReview(
                track_id=track_id2,
                connector_name="spotify",
                connector_track_id=ct_id2,
                match_method="isrc",
                confidence=65,
                match_weight=3.2,
            ),
        ]
        count = await repo.create_reviews_batch(reviews)
        assert count == 2


class TestListPendingReviews:
    """Listing filters by pending status and paginates."""

    async def test_lists_only_pending(self, db_session: AsyncSession):
        track_id, ct_id = await _seed_track_and_connector_track(db_session)
        track_id2, ct_id2 = await _seed_track_and_connector_track(db_session)
        repo = MatchReviewRepository(db_session)

        # Create one pending and one accepted
        await repo.create_review(
            MatchReview(
                track_id=track_id,
                connector_name="spotify",
                connector_track_id=ct_id,
                match_method="artist_title",
                confidence=72,
                match_weight=4.5,
            )
        )
        accepted = await repo.create_review(
            MatchReview(
                track_id=track_id2,
                connector_name="spotify",
                connector_track_id=ct_id2,
                match_method="isrc",
                confidence=85,
                match_weight=6.0,
            )
        )
        await repo.update_review_status(accepted.id, ReviewStatus.ACCEPTED)

        reviews, total = await repo.list_pending_reviews()
        assert total == 1
        assert len(reviews) == 1
        assert reviews[0].track_id == track_id

    async def test_pagination(self, db_session: AsyncSession):
        repo = MatchReviewRepository(db_session)

        # Create 3 reviews
        for _ in range(3):
            track_id, ct_id = await _seed_track_and_connector_track(db_session)
            await repo.create_review(
                MatchReview(
                    track_id=track_id,
                    connector_name="spotify",
                    connector_track_id=ct_id,
                    match_method="artist_title",
                    confidence=72,
                    match_weight=4.5,
                )
            )

        reviews, total = await repo.list_pending_reviews(limit=2, offset=0)
        assert total == 3
        assert len(reviews) == 2

        reviews2, total2 = await repo.list_pending_reviews(limit=2, offset=2)
        assert total2 == 3
        assert len(reviews2) == 1

    async def test_enriches_connector_track_display_fields(
        self, db_session: AsyncSession
    ):
        track_id, ct_id = await _seed_track_and_connector_track(db_session)
        repo = MatchReviewRepository(db_session)

        await repo.create_review(
            MatchReview(
                track_id=track_id,
                connector_name="spotify",
                connector_track_id=ct_id,
                match_method="artist_title",
                confidence=72,
                match_weight=4.5,
            )
        )

        reviews, _ = await repo.list_pending_reviews()
        review = reviews[0]
        # Should have denormalized display fields from connector_track
        assert review.connector_track_title != ""


class TestUpdateReviewStatus:
    """Status updates set reviewed_at timestamp."""

    async def test_accept_sets_reviewed_at(self, db_session: AsyncSession):
        track_id, ct_id = await _seed_track_and_connector_track(db_session)
        repo = MatchReviewRepository(db_session)

        created = await repo.create_review(
            MatchReview(
                track_id=track_id,
                connector_name="spotify",
                connector_track_id=ct_id,
                match_method="artist_title",
                confidence=72,
                match_weight=4.5,
            )
        )

        updated = await repo.update_review_status(created.id, ReviewStatus.ACCEPTED)
        assert updated.status == ReviewStatus.ACCEPTED
        assert updated.reviewed_at is not None


class TestCountPending:
    """Count pending returns correct number."""

    async def test_counts_pending_only(self, db_session: AsyncSession):
        track_id, ct_id = await _seed_track_and_connector_track(db_session)
        repo = MatchReviewRepository(db_session)

        await repo.create_review(
            MatchReview(
                track_id=track_id,
                connector_name="spotify",
                connector_track_id=ct_id,
                match_method="artist_title",
                confidence=72,
                match_weight=4.5,
            )
        )

        count = await repo.count_pending()
        assert count == 1
