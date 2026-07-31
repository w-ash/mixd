"""Integration tests for MatchReviewRepository.

Tests real database operations for the match review queue — create, list,
update status — using the db_session fixture with SQLite.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.constants import ReviewStatus
from src.domain.entities.match_review import MatchReview
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrack,
)
from src.infrastructure.persistence.repositories.match_review import (
    MatchReviewRepository,
)


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
        written = await repo.create_reviews_batch(reviews)
        assert len(written) == 2


class TestListPendingReviews:
    """Listing filters by pending status and paginates."""

    async def test_lists_only_pending(self, db_session: AsyncSession):
        track_id, ct_id = await _seed_track_and_connector_track(db_session)
        track_id2, ct_id2 = await _seed_track_and_connector_track(db_session)
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

        reviews, total = await repo.list_pending_reviews(user_id="default")
        assert total == 1
        assert len(reviews) == 1
        assert reviews[0].track_id == track_id

    async def test_pagination(self, db_session: AsyncSession):
        repo = MatchReviewRepository(db_session)

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

        reviews, total = await repo.list_pending_reviews(
            user_id="default", limit=2, offset=0
        )
        assert total == 3
        assert len(reviews) == 2

        reviews2, total2 = await repo.list_pending_reviews(
            user_id="default", limit=2, offset=2
        )
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

        reviews, _ = await repo.list_pending_reviews(user_id="default")
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

        count = await repo.count_pending(user_id="default")
        assert count == 1


class TestResolvedReviewsDoNotResurrect:
    """A verdict is a decision, not a cache entry the next import may overwrite."""

    async def test_re_importing_a_rejected_candidate_leaves_it_rejected(
        self, db_session: AsyncSession
    ):
        """The blanket ``SET status = EXCLUDED.status`` flipped it back to pending.

        Which meant the queue re-asked the same question on every import, and
        the ``reviewed_at`` stamp proving the person had answered was
        overwritten along with it.
        """
        track_id, ct_id = await _seed_track_and_connector_track(db_session)
        repo = MatchReviewRepository(db_session)
        review = MatchReview(
            track_id=track_id,
            connector_name="spotify",
            connector_track_id=ct_id,
            match_method="artist_title",
            confidence=60,
            match_weight=2.0,
        )
        await repo.create_reviews_batch([review])
        existing = (await repo.list_pending_reviews(user_id="default"))[0][0]
        rejected = await repo.update_review_status(existing.id, ReviewStatus.REJECTED)
        assert rejected.reviewed_at is not None

        written = await repo.create_reviews_batch([review])

        assert written == [], "a rejected row is neither rewritten nor reported"
        after = await repo.get_by_id(existing.id)
        assert after is not None
        assert after.status == ReviewStatus.REJECTED
        assert after.reviewed_at is not None

    async def test_a_pending_row_still_takes_fresher_evidence(
        self, db_session: AsyncSession
    ):
        """The guard must not freeze the queue: unanswered rows still refresh."""
        track_id, ct_id = await _seed_track_and_connector_track(db_session)
        repo = MatchReviewRepository(db_session)
        base = MatchReview(
            track_id=track_id,
            connector_name="spotify",
            connector_track_id=ct_id,
            match_method="artist_title",
            confidence=60,
            match_weight=2.0,
        )
        await repo.create_reviews_batch([base])

        written = await repo.create_reviews_batch([
            MatchReview(
                track_id=track_id,
                connector_name="spotify",
                connector_track_id=ct_id,
                match_method="isrc",
                confidence=81,
                match_weight=4.0,
            )
        ])

        assert len(written) == 1
        rows, _ = await repo.list_pending_reviews(user_id="default")
        current = next(row for row in rows if row.connector_track_id == ct_id)
        assert current.confidence == 81
        assert current.match_method == "isrc"


class TestOneBadRowDoesNotPoisonTheTransaction:
    """The savepoint is what keeps a constraint failure local to its row.

    Phase 1 materializes connector tracks and Phase 2 writes the reviews, so a
    connector track cascaded away in between leaves a review row pointing at
    nothing. Without a savepoint that 23503 aborts the caller's whole import
    transaction — every accepted mapping in it lost to save one review row.
    """

    async def test_an_orphaned_review_is_dropped_and_the_session_survives(
        self, db_session: AsyncSession
    ):
        track_id, ct_id = await _seed_track_and_connector_track(db_session)
        repo = MatchReviewRepository(db_session)

        def _review(connector_track_id, confidence: int) -> MatchReview:
            return MatchReview(
                track_id=track_id,
                connector_name="spotify",
                connector_track_id=connector_track_id,
                match_method="isrc",
                confidence=confidence,
                match_weight=3.0,
            )

        # The second row's connector track does not exist — the FK fails.
        written = await repo.create_reviews_batch([
            _review(ct_id, 70),
            _review(uuid4(), 72),
        ])

        assert [row.connector_track_id for row in written] == [ct_id], (
            "the good row survives; only the orphan is dropped"
        )
        # Still usable: the proof the failure stayed inside its savepoint
        # rather than aborting the transaction this repository was called in.
        assert await repo.count_pending(user_id="default") == 1


class TestExistingReviewKeys:
    """Which questions were already asked, which a batch write cannot answer."""

    async def test_existing_review_keys_reports_only_pairs_already_present(
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
                confidence=64,
                match_weight=3.0,
            )
        )

        present = await repo.existing_review_keys([
            (track_id, ct_id),
            (uuid4(), uuid4()),
        ])

        assert present == frozenset({(track_id, ct_id)}), (
            "a pair never reviewed must not be reported as already asked"
        )

    async def test_no_keys_asks_nothing_of_the_database(self, db_session: AsyncSession):
        repo = MatchReviewRepository(db_session)

        assert await repo.existing_review_keys([]) == frozenset()
