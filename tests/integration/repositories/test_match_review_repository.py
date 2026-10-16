"""Integration tests for MatchReviewRepository.

Tests real database operations for the match review queue — create, list,
update status — using the db_session fixture with PostgreSQL.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.constants import ReviewStatus
from src.domain.entities.match_review import MatchReview
from src.infrastructure.persistence.database.models import (
    DBConnectorTrack,
    DBTrack,
)
from src.infrastructure.persistence.repositories.match_review import (
    MatchReviewRepository,
)
from tests.fixtures import TEST_USER_ID


async def _create(repo: MatchReviewRepository, review: MatchReview) -> MatchReview:
    """Seed one review through the batch write, the only write there is."""
    (created,) = await repo.create_reviews_batch([review])
    return created


async def _seed_track_and_connector_track(
    session: AsyncSession,
) -> tuple[int, int]:
    """Insert a track and connector track, return their IDs."""
    uid = uuid4().hex[:8]
    now = datetime.now(UTC)

    track = DBTrack(
        title=f"Track {uid}",
        artists=[{"name": f"Artist {uid}"}],
        isrc=f"ISRC{uid.upper()[:8]}",
        user_id=TEST_USER_ID,
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
            user_id=TEST_USER_ID,
        )
        result = await _create(repo, review)

        assert result.track_id == track_id
        assert result.confidence == 72
        assert result.status == "pending"
        stored = await repo.get_by_id(result.id)
        assert stored is not None
        assert (stored.connector_track_id, stored.match_method) == (
            ct_id,
            "artist_title",
        )


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
                user_id=TEST_USER_ID,
            ),
            MatchReview(
                track_id=track_id2,
                connector_name="spotify",
                connector_track_id=ct_id2,
                match_method="isrc",
                confidence=65,
                match_weight=3.2,
                user_id=TEST_USER_ID,
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

        await _create(
            repo,
            MatchReview(
                track_id=track_id,
                connector_name="spotify",
                connector_track_id=ct_id,
                match_method="artist_title",
                confidence=72,
                match_weight=4.5,
                user_id=TEST_USER_ID,
            ),
        )
        accepted = await _create(
            repo,
            MatchReview(
                track_id=track_id2,
                connector_name="spotify",
                connector_track_id=ct_id2,
                match_method="isrc",
                confidence=85,
                match_weight=6.0,
                user_id=TEST_USER_ID,
            ),
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
            await _create(
                repo,
                MatchReview(
                    track_id=track_id,
                    connector_name="spotify",
                    connector_track_id=ct_id,
                    match_method="artist_title",
                    confidence=72,
                    match_weight=4.5,
                    user_id=TEST_USER_ID,
                ),
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

        await _create(
            repo,
            MatchReview(
                track_id=track_id,
                connector_name="spotify",
                connector_track_id=ct_id,
                match_method="artist_title",
                confidence=72,
                match_weight=4.5,
                user_id=TEST_USER_ID,
            ),
        )

        ct = await db_session.get(DBConnectorTrack, ct_id)
        assert ct is not None
        uid = ct.connector_track_identifier.removeprefix("sp_ct_")

        reviews, _ = await repo.list_pending_reviews(user_id="default")
        (review,) = reviews
        # Display fields come from the connector track, not the canonical one.
        assert review.connector_track_title == f"Spotify Track {uid}"
        assert review.connector_track_artists == [f"Spotify Artist {uid}"]


class TestUpdateReviewStatus:
    """Status updates set reviewed_at timestamp."""

    async def test_accept_sets_reviewed_at(self, db_session: AsyncSession):
        track_id, ct_id = await _seed_track_and_connector_track(db_session)
        repo = MatchReviewRepository(db_session)

        created = await _create(
            repo,
            MatchReview(
                track_id=track_id,
                connector_name="spotify",
                connector_track_id=ct_id,
                match_method="artist_title",
                confidence=72,
                match_weight=4.5,
                user_id=TEST_USER_ID,
            ),
        )

        before = datetime.now(UTC)
        updated = await repo.update_review_status(created.id, ReviewStatus.ACCEPTED)
        assert updated.status == ReviewStatus.ACCEPTED
        assert updated.reviewed_at is not None
        assert before <= updated.reviewed_at <= datetime.now(UTC)


class TestCountPending:
    """Count pending returns correct number."""

    async def test_counts_pending_only(self, db_session: AsyncSession):
        repo = MatchReviewRepository(db_session)
        created = []
        for _ in range(3):
            track_id, ct_id = await _seed_track_and_connector_track(db_session)
            created.append(
                await _create(
                    repo,
                    MatchReview(
                        track_id=track_id,
                        connector_name="spotify",
                        connector_track_id=ct_id,
                        match_method="artist_title",
                        confidence=72,
                        match_weight=4.5,
                        user_id=TEST_USER_ID,
                    ),
                )
            )
        await repo.update_review_status(created[0].id, ReviewStatus.ACCEPTED)
        await repo.update_review_status(created[1].id, ReviewStatus.REJECTED)

        assert await repo.count_pending(user_id="default") == 1


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
            user_id=TEST_USER_ID,
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
        assert after.reviewed_at == rejected.reviewed_at

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
            user_id=TEST_USER_ID,
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
                user_id=TEST_USER_ID,
            )
        ])

        assert len(written) == 1
        rows, _ = await repo.list_pending_reviews(user_id="default")
        current = next(row for row in rows if row.connector_track_id == ct_id)
        assert current.confidence == 81
        assert current.match_method == "isrc"

    async def test_a_refresh_keeps_the_pending_rows_creation_time(
        self, db_session: AsyncSession
    ):
        """Review age is how the queue is ordered and how staleness is counted;
        a re-encounter refreshes the evidence, not the clock."""
        track_id, ct_id = await _seed_track_and_connector_track(db_session)
        repo = MatchReviewRepository(db_session)
        review = MatchReview(
            track_id=track_id,
            connector_name="spotify",
            connector_track_id=ct_id,
            match_method="artist_title",
            confidence=60,
            match_weight=2.0,
            user_id=TEST_USER_ID,
        )
        (first,) = await repo.create_reviews_batch([review])
        assert first.created_at is not None
        assert first.updated_at is not None

        (refreshed,) = await repo.create_reviews_batch([review])

        assert refreshed.id == first.id
        assert refreshed.created_at == first.created_at
        assert refreshed.updated_at is not None
        assert refreshed.updated_at > first.updated_at


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
                user_id=TEST_USER_ID,
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
        await _create(
            repo,
            MatchReview(
                track_id=track_id,
                connector_name="spotify",
                connector_track_id=ct_id,
                match_method="artist_title",
                confidence=64,
                match_weight=3.0,
                user_id=TEST_USER_ID,
            ),
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
